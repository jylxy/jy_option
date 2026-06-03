"""External-intent order adapter for the current S1 paper line."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .account_state import DEFAULT_STATE_DIR, expected_account_state_files
from .config_snapshot import important_rules, load_effective_config
from .data_loader import load_signal_day_snapshot
from .diagnostics import write_csv, write_json
from .main_pre_expiry_itm_exit import (
    calendar_dte,
    config_enabled as main_pre_expiry_itm_exit_enabled,
    exit_reason as main_pre_expiry_itm_exit_reason,
    intrinsic_value,
    is_main_monthly_mapping,
    scan_dte_lt as main_pre_expiry_itm_scan_dte_lt,
    safe_float,
    safe_text,
)
from .paths import DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, resolve_path
from .schemas import ORDER_FRONT_COLUMNS, PaperTradingRunRequest


EXCHANGE_TO_SUFFIX = {
    "DCE": "DCE",
    "SHFE": "SHF",
    "CZCE": "CZC",
    "INE": "INE",
    "GFEX": "GFE",
    "CFFEX": "CFE",
    "SSE": "SH",
    "SZSE": "SZ",
}
TOOLKIT_SUFFIXES = set(EXCHANGE_TO_SUFFIX.values())


@dataclass(frozen=True)
class PaperTradingRunResult:
    signal_date: str
    execute_date: str
    orders_path: Path | None
    diagnostics_path: Path | None
    audit_path: Path | None
    orders: pd.DataFrame
    diagnostics: pd.DataFrame
    meta: dict[str, Any]


def normalize_signal_date(value: Any) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def to_toolkit_code(contract_code: Any, exchange: Any = "") -> str:
    raw = str(contract_code or "").strip()
    if not raw:
        return ""
    if "." in raw:
        suffix = raw.rsplit(".", 1)[-1].upper()
        if suffix in TOOLKIT_SUFFIXES:
            return raw.upper()
        prefix, short_name = raw.split(".", 1)
        mapped = EXCHANGE_TO_SUFFIX.get(prefix.upper())
        if mapped:
            return f"{short_name.upper()}.{mapped}"
    mapped = EXCHANGE_TO_SUFFIX.get(str(exchange or "").upper())
    return f"{raw.upper()}.{mapped}" if mapped else raw.upper()


def _frontload_columns(frame: pd.DataFrame, front_columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=front_columns)
    ordered = [col for col in front_columns if col in frame.columns]
    rest = [col for col in frame.columns if col not in ordered]
    return frame.loc[:, ordered + rest]


def _safe_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(np.nan, index=frame.index)


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _signed_quantity(row: pd.Series) -> float:
    qty = safe_float(row.get("quantity", 0.0), 0.0)
    side = safe_text(row.get("position_side", row.get("role", ""))).lower()
    if side in {"short", "sell", "sold"} and qty > 0:
        return -qty
    if side in {"long", "buy", "bought"} and qty < 0:
        return abs(qty)
    return qty


def _option_mark_lookup(snapshot: pd.DataFrame) -> dict[str, pd.Series]:
    if snapshot.empty:
        return {}
    work = snapshot.copy()
    code_col = "option_code" if "option_code" in work.columns else "ths_code" if "ths_code" in work.columns else ""
    if not code_col:
        return {}
    work[code_col] = work[code_col].astype(str)
    work = work[work[code_col].ne("")]
    work = work.drop_duplicates(code_col, keep="last")
    return {str(row[code_col]): row for _, row in work.iterrows()}


def _next_execution_date(signal_date: str, lag: str) -> str:
    signal = str(signal_date)[:10]
    if str(lag or "").lower() not in {"t_plus_1", "t+1"}:
        return signal
    try:
        from .data_loader import load_trading_dates

        end = (pd.Timestamp(signal) + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        for date in load_trading_dates(signal, end):
            if date > signal:
                return date
    except Exception:
        pass
    return pd.bdate_range(pd.Timestamp(signal) + pd.Timedelta(days=1), periods=1)[0].strftime("%Y-%m-%d")


def _load_external_signals(path: Path, date_column: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"external signal schedule not found: {path}")
    frame = pd.read_csv(path)
    required = {
        date_column,
        "product",
        "exchange",
        "contract_code",
        "option_type",
        "target_expiry",
        "qty",
        "entry_price",
        "premium_cash",
        "target_premium_cash",
        "target_premium_pct",
        "margin_cash",
        "one_lot_margin_cash",
        "entry_reason",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"external signal schedule missing columns: {missing}")
    out = frame.copy()
    out["signal_date_key"] = out[date_column].map(normalize_signal_date)
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0).astype(int)
    out["toolkit_code"] = [
        to_toolkit_code(code, exchange)
        for code, exchange in zip(out["contract_code"], out["exchange"])
    ]
    return out[(out["signal_date_key"] != "") & (out["qty"] > 0) & (out["toolkit_code"] != "")].copy()


def _orders_from_signals(signals: pd.DataFrame, signal_date: str, execute_date: str) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS)
    out = signals.copy()
    out["signal_date"] = signal_date
    out["execute_date"] = execute_date
    out["order_status"] = "planned_external_intent"
    out["action"] = "open_sell"
    out["strategy"] = "S1"
    if "strategy_layer" not in out.columns:
        out["strategy_layer"] = out["entry_reason"].fillna("").replace("", "external_signal")
    else:
        entry_reason = out["entry_reason"].fillna("").astype(str)
        entry_reason = entry_reason.mask(entry_reason.eq(""), "external_signal")
        strategy_layer = out["strategy_layer"].fillna("").astype(str)
        out["strategy_layer"] = strategy_layer.mask(strategy_layer.eq(""), entry_reason)
    out["code"] = out["toolkit_code"]
    out["source_contract_code"] = out["contract_code"].astype(str)
    out["expiry"] = out["target_expiry"].astype(str).str[:10]
    out["quantity"] = out["qty"].astype(int)
    out["signal_ref_price"] = _safe_numeric(out, "entry_price")
    out["gross_premium_cash"] = _safe_numeric(out, "premium_cash")
    out["net_premium_cash"] = out["gross_premium_cash"]
    out["margin"] = _safe_numeric(out, "margin_cash")
    out["one_contract_margin"] = _safe_numeric(out, "one_lot_margin_cash")
    out["selected_side_iv_pressure"] = _safe_numeric(out, "selected_side_iv_pressure")
    out["other_side_iv_pressure"] = _safe_numeric(out, "other_side_iv_pressure")
    out["side_iv_pressure_diff"] = _safe_numeric(out, "side_iv_pressure_diff")
    if "forced_sell_side" not in out.columns and "sell_side" in out.columns:
        out["forced_sell_side"] = out["sell_side"]
    if "overlay_signal_family" not in out.columns:
        layer = out["strategy_layer"].astype(str)
        out["overlay_signal_family"] = np.where(layer.str.startswith("overlay"), layer, "")
    for column in ("overlay_strategy", "overlay_signal_rule", "overlay_side_rule", "overlay_priority"):
        if column not in out.columns:
            out[column] = ""
    out["l1_rule"] = "rule_l1_hsafe_addon025"
    out["l2_rule"] = "high_iv_pressure_side"
    out["l3_rule"] = "l3eff015_budget_tilt_and_margin45_new075"
    out["l4_rule"] = out["side_rule"].fillna("").replace("", "l4_diff02_delta04_l3eff015")
    return _frontload_columns(out, ORDER_FRONT_COLUMNS)


def _diagnostics_from_orders(orders: pd.DataFrame) -> pd.DataFrame:
    if orders.empty:
        return pd.DataFrame(
            columns=[
                "signal_date",
                "product",
                "code",
                "entry_reason",
                "l0_oi_ok",
                "l0_volume_ok",
                "l0_delta_ok",
                "l4_weak_pressure_delta_cut",
            ]
        )
    out = orders.copy()
    entry_reason = out.get("entry_reason", pd.Series("", index=out.index)).astype(str)
    strategy_layer = out.get("strategy_layer", pd.Series("", index=out.index)).astype(str)
    overlay_mask = entry_reason.eq("iv_extreme_overlay") | strategy_layer.str.startswith("overlay")
    min_oi = np.where(overlay_mask, 1000.0, 1000.0)
    delta_cap = np.where(overlay_mask, 0.04, 0.08)
    delta_cap = np.where(
        strategy_layer.isin(["overlay2_risk_reversal_same_sign", "overlay3_term_structure_cluster_cap2"]),
        0.03,
        delta_cap,
    )
    if "overlay_tier_max_abs_delta" in out.columns:
        tier_cap = pd.to_numeric(out["overlay_tier_max_abs_delta"], errors="coerce")
        delta_cap = np.where(tier_cap.notna(), tier_cap.to_numpy(), delta_cap)
    weak = _safe_numeric(out, "side_iv_pressure_diff").lt(0.02)
    delta_cap = np.where(entry_reason.eq("monthly") & weak.fillna(False), 0.04, delta_cap)
    abs_delta = _safe_numeric(out, "delta").abs()
    diag = pd.DataFrame(
        {
            "signal_date": out["signal_date"],
            "product": out["product"],
            "code": out["code"],
            "entry_reason": entry_reason,
            "strategy_layer": strategy_layer,
            "l0_oi_ok": pd.to_numeric(out.get("close_oi", np.nan), errors="coerce").ge(min_oi),
            "l0_volume_ok": pd.to_numeric(out.get("volume", np.nan), errors="coerce").gt(0),
            "l0_delta_ok": abs_delta.lt(delta_cap),
            "l4_weak_pressure_delta_cut": entry_reason.eq("monthly") & weak.fillna(False),
            "target_premium_pct": pd.to_numeric(out.get("target_premium_pct", np.nan), errors="coerce"),
            "quantity": pd.to_numeric(out.get("quantity", np.nan), errors="coerce"),
            "margin": pd.to_numeric(out.get("margin", np.nan), errors="coerce"),
        }
    )
    return diag


def _orders_from_main_pre_expiry_itm_exit(
    *,
    signal_date: str,
    execute_date: str,
    config: dict[str, Any],
    config_path: Path,
    state_dir: Path,
    products: tuple[str, ...] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    reason = main_pre_expiry_itm_exit_reason(config)
    diagnostics: list[dict[str, Any]] = []
    if not main_pre_expiry_itm_exit_enabled(config):
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS), pd.DataFrame()

    files = expected_account_state_files(state_dir, signal_date)
    positions = _read_optional_csv(files["positions"])
    if positions.empty:
        diagnostics.append({
            "signal_date": signal_date,
            "entry_reason": "monthly",
            "strategy_layer": "main_monthly",
            "reason": "main_pre_expiry_itm_exit_no_positions_state",
            "positions_path": str(files["positions"]),
        })
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS), pd.DataFrame(diagnostics)

    work = positions.copy()
    work["_signed_quantity"] = work.apply(_signed_quantity, axis=1)
    work["_is_main_monthly"] = work.apply(is_main_monthly_mapping, axis=1)
    work = work[work["_signed_quantity"].lt(0) & work["_is_main_monthly"]].copy()
    if products:
        product_set = {str(product).upper() for product in products}
        work = work[work.get("product", pd.Series("", index=work.index)).astype(str).str.upper().isin(product_set)].copy()
    if work.empty:
        diagnostics.append({
            "signal_date": signal_date,
            "entry_reason": "monthly",
            "strategy_layer": "main_monthly",
            "reason": "main_pre_expiry_itm_exit_no_active_main_short_positions",
            "positions_path": str(files["positions"]),
        })
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS), pd.DataFrame(diagnostics)

    active_products = tuple(sorted({
        safe_text(x).upper()
        for x in work.get("product", pd.Series(dtype=object)).dropna().tolist()
        if safe_text(x)
    }))
    try:
        snapshot = load_signal_day_snapshot(
            signal_date,
            config_path=config_path,
            products=products or active_products or None,
        )
    except Exception as exc:  # noqa: BLE001 - daily orders should still emit diagnostics.
        diagnostics.append({
            "signal_date": signal_date,
            "entry_reason": "monthly",
            "strategy_layer": "main_monthly",
            "reason": "main_pre_expiry_itm_exit_snapshot_load_failed",
            "error": str(exc),
        })
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS), pd.DataFrame(diagnostics)

    mark_lookup = _option_mark_lookup(snapshot)
    threshold = main_pre_expiry_itm_scan_dte_lt(config)
    rows: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        code = safe_text(row.get("code", ""))
        option_type = safe_text(row.get("option_type", "")).upper()[:1]
        strike = safe_float(row.get("strike"), np.nan)
        expiry = safe_text(row.get("expiry", ""))[:10]
        signed_qty = safe_float(row.get("_signed_quantity"), 0.0)
        quantity = int(abs(signed_qty))
        if not code or option_type not in {"P", "C"} or quantity <= 0:
            continue
        mark_row = mark_lookup.get(code)
        if mark_row is None:
            diagnostics.append({
                "signal_date": signal_date,
                "product": row.get("product", ""),
                "code": code,
                "entry_reason": "monthly",
                "strategy_layer": "main_monthly",
                "reason": "main_pre_expiry_itm_exit_missing_option_snapshot",
                "expiry": expiry,
            })
            continue
        dte = safe_float(mark_row.get("dte"), np.nan)
        if not np.isfinite(dte):
            dte = calendar_dte(expiry, signal_date)
        spot = safe_float(mark_row.get("spot_close"), np.nan)
        intrinsic = intrinsic_value(option_type, strike, spot)
        option_close = safe_float(mark_row.get("option_close"), np.nan)
        if not np.isfinite(option_close) or option_close <= 0:
            option_close = safe_float(row.get("mark_price"), np.nan)
        triggered = bool(np.isfinite(dte) and dte < threshold and np.isfinite(intrinsic) and intrinsic > 0)
        diagnostics.append({
            "signal_date": signal_date,
            "product": row.get("product", ""),
            "code": code,
            "entry_reason": "monthly",
            "strategy_layer": "main_monthly",
            "reason": reason if triggered else "main_pre_expiry_itm_exit_not_triggered",
            "scan_dte": dte,
            "scan_dte_lt": threshold,
            "option_type": option_type,
            "strike": strike,
            "spot_close": spot,
            "intrinsic": intrinsic,
            "option_close": option_close,
            "expiry": expiry,
        })
        if not triggered:
            continue
        multiplier = safe_float(row.get("multiplier"), 1.0)
        if not np.isfinite(multiplier) or multiplier <= 0:
            multiplier = 1.0
        close_value_cash = option_close * multiplier * quantity if np.isfinite(option_close) else np.nan
        base_row = row.drop(labels=["_signed_quantity", "_is_main_monthly"], errors="ignore").to_dict()
        rows.append({
            **base_row,
            "signal_date": signal_date,
            "execute_date": execute_date,
            "order_status": "planned_main_pre_expiry_itm_exit",
            "action": "buy_close",
            "strategy": "S1",
            "entry_reason": "monthly",
            "strategy_layer": "main_monthly",
            "product": safe_text(row.get("product", "")),
            "exchange": safe_text(row.get("exchange", "")),
            "code": code,
            "source_contract_code": code,
            "option_type": option_type,
            "strike": strike,
            "expiry": expiry,
            "dte": dte,
            "quantity": quantity,
            "signal_ref_price": option_close,
            "gross_premium_cash": close_value_cash,
            "net_premium_cash": -close_value_cash if np.isfinite(close_value_cash) else np.nan,
            "target_premium_cash": np.nan,
            "target_premium_pct": np.nan,
            "margin": safe_float(row.get("margin"), np.nan),
            "one_contract_margin": np.nan,
            "budget_group": safe_text(row.get("budget_group", "")),
            "side_rule": reason,
            "forced_sell_side": "",
            "selected_side_iv_pressure": np.nan,
            "other_side_iv_pressure": np.nan,
            "side_iv_pressure_diff": np.nan,
            "overlay_strategy": "",
            "overlay_signal_family": "",
            "overlay_signal_rule": "",
            "overlay_side_rule": "",
            "overlay_priority": "",
            "l1_rule": "main_pre_expiry_itm_exit",
            "l2_rule": "dte_lt_2_and_itm",
            "l3_rule": "risk_exit",
            "l4_rule": "buy_close_next_trading_day",
            "exit_rule": reason,
            "exit_scan_date": signal_date,
            "exit_execute_date": execute_date,
            "exit_scan_dte": dte,
            "exit_scan_dte_lt": threshold,
            "exit_scan_spot_close": spot,
            "exit_intrinsic": intrinsic,
        })
    orders = _frontload_columns(pd.DataFrame(rows), ORDER_FRONT_COLUMNS)
    return orders, pd.DataFrame(diagnostics)


class PaperTradingOrderGenerator:
    """Generate T+1 paper orders from the approved external S1 intent schedule."""

    def __init__(self, config_path: str | Path | None = None, output_dir: str | Path | None = None):
        self.config_path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
        self.output_dir = resolve_path(output_dir, default=DEFAULT_OUTPUT_DIR)

    def generate(self, request: PaperTradingRunRequest) -> PaperTradingRunResult:
        signal_date = normalize_signal_date(request.signal_date)
        if not signal_date:
            raise ValueError(f"invalid signal_date: {request.signal_date}")
        config_path = resolve_path(request.config_path, default=self.config_path)
        output_dir = resolve_path(request.output_dir, default=self.output_dir)
        state_dir = resolve_path(request.state_dir, default=DEFAULT_STATE_DIR)
        tag = request.tag or f"s1_external_intents_{signal_date.replace('-', '')}"

        snapshot = load_effective_config(config_path)
        config = snapshot.config
        signal_path_value = config.get("external_signal_path")
        if not signal_path_value:
            raise ValueError("s1 paper config must define external_signal_path")
        signal_path = resolve_path(signal_path_value)
        if not signal_path.exists() and config.get("validation_gold_signal_path"):
            gold_path = resolve_path(config.get("validation_gold_signal_path"))
            if gold_path.exists():
                signal_path = gold_path
        date_col = str(config.get("external_signal_date_column", "entry_date") or "entry_date")
        all_signals = _load_external_signals(signal_path, date_col)
        day_signals = all_signals[all_signals["signal_date_key"].eq(signal_date)].copy()
        if request.products:
            products = {str(product).upper() for product in request.products}
            day_signals = day_signals[day_signals["product"].astype(str).str.upper().isin(products)].copy()
        execute_date = _next_execution_date(signal_date, str(config.get("external_signal_default_execution_lag", "T_plus_1")))
        open_orders = _orders_from_signals(day_signals, signal_date, execute_date)
        exit_orders, exit_diagnostics = _orders_from_main_pre_expiry_itm_exit(
            signal_date=signal_date,
            execute_date=execute_date,
            config=config,
            config_path=config_path,
            state_dir=state_dir,
            products=request.products,
        )
        order_parts = [frame for frame in (exit_orders, open_orders) if not frame.empty]
        orders = (
            pd.concat(order_parts, ignore_index=True, sort=False)
            if order_parts
            else pd.DataFrame(columns=ORDER_FRONT_COLUMNS)
        )
        diagnostic_parts = [frame for frame in (_diagnostics_from_orders(open_orders), exit_diagnostics) if not frame.empty]
        diagnostics = (
            pd.concat(diagnostic_parts, ignore_index=True, sort=False)
            if diagnostic_parts
            else pd.DataFrame()
        )
        schedule_dates = all_signals["signal_date_key"].dropna().astype(str)
        schedule_min = schedule_dates.min() if not schedule_dates.empty else ""
        schedule_max = schedule_dates.max() if not schedule_dates.empty else ""
        if not schedule_min or signal_date < schedule_min or signal_date > schedule_max:
            schedule_status = "outside_schedule_range"
            diagnostics = pd.concat(
                [
                    diagnostics,
                    pd.DataFrame(
                        [
                            {
                                "signal_date": signal_date,
                                "product": "",
                                "code": "",
                                "entry_reason": "external_intent_schedule",
                                "strategy_layer": "schedule_audit",
                                "reason": schedule_status,
                                "schedule_min_date": schedule_min,
                                "schedule_max_date": schedule_max,
                                "external_signal_path": str(signal_path),
                            }
                        ]
                    ),
                ],
                ignore_index=True,
                sort=False,
            )
        elif day_signals.empty:
            schedule_status = "inside_range_no_signal"
        else:
            schedule_status = "covered"

        orders_path = output_dir / "orders" / f"orders_{tag}.csv"
        diagnostics_path = output_dir / "diagnostics" / f"diagnostics_{tag}.csv"
        audit_path = output_dir / "audit" / f"audit_{tag}.json"
        meta = {
            "tag": tag,
            "signal_date": signal_date,
            "execute_date": execute_date,
            "config_path": str(config_path),
            "config_sha256": snapshot.sha256,
            "state_dir": str(state_dir),
            "external_signal_path": str(signal_path),
            "external_signal_rows": int(len(all_signals)),
            "external_signal_min_date": schedule_min,
            "external_signal_max_date": schedule_max,
            "external_signal_schedule_status": schedule_status,
            "generated_order_count": int(len(orders)),
            "generated_open_order_count": int(len(open_orders)),
            "generated_main_pre_expiry_itm_exit_order_count": int(len(exit_orders)),
            "diagnostic_row_count": int(len(diagnostics)),
            "products": list(request.products) if request.products else None,
            "important_rules": important_rules(config),
            "notes": [
                "Orders are generated from the approved daily S1 external-intent schedule.",
                "Toolkit minute replay remains responsible for VWAP fills, pending, reroute, expiry, and overlay stop simulation.",
            ],
        }

        if request.write_outputs:
            write_csv(orders_path, orders)
            write_csv(diagnostics_path, diagnostics)
            write_json(audit_path, meta)
        else:
            orders_path = diagnostics_path = audit_path = None

        return PaperTradingRunResult(
            signal_date=signal_date,
            execute_date=execute_date,
            orders_path=orders_path,
            diagnostics_path=diagnostics_path,
            audit_path=audit_path,
            orders=orders,
            diagnostics=diagnostics,
            meta=meta,
        )
