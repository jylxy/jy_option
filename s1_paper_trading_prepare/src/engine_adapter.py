"""External-intent order adapter for the current S1 paper line."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config_snapshot import important_rules, load_effective_config
from .diagnostics import write_csv, write_json
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
    out["l1_rule"] = "rule_l1_oi03_flow_guard"
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
        tag = request.tag or f"s1_external_intents_{signal_date.replace('-', '')}"

        snapshot = load_effective_config(config_path)
        config = snapshot.config
        signal_path = resolve_path(config.get("external_signal_path"), default=DEFAULT_PAPER_CONFIG)
        date_col = str(config.get("external_signal_date_column", "entry_date") or "entry_date")
        all_signals = _load_external_signals(signal_path, date_col)
        day_signals = all_signals[all_signals["signal_date_key"].eq(signal_date)].copy()
        if request.products:
            products = {str(product).upper() for product in request.products}
            day_signals = day_signals[day_signals["product"].astype(str).str.upper().isin(products)].copy()
        execute_date = _next_execution_date(signal_date, str(config.get("external_signal_default_execution_lag", "T_plus_1")))
        orders = _orders_from_signals(day_signals, signal_date, execute_date)
        diagnostics = _diagnostics_from_orders(orders)

        orders_path = output_dir / "orders" / f"orders_{tag}.csv"
        diagnostics_path = output_dir / "diagnostics" / f"diagnostics_{tag}.csv"
        audit_path = output_dir / "audit" / f"audit_{tag}.json"
        meta = {
            "tag": tag,
            "signal_date": signal_date,
            "execute_date": execute_date,
            "config_path": str(config_path),
            "config_sha256": snapshot.sha256,
            "external_signal_path": str(signal_path),
            "external_signal_rows": int(len(all_signals)),
            "generated_order_count": int(len(orders)),
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
