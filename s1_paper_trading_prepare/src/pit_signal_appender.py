"""Point-in-time daily factor appender for the current S1 paper line.

This module is the production-facing path for:

1. reading Toolkit daily raw snapshots,
2. appending point-in-time factor panels,
3. generating T-day external intent rows for T+1 paper orders, and
4. replacing the same T rows in the unified external signal schedule.

It intentionally does not consume research `shadow_*`, path, or outcome label
fields. If a future historical-performance feature is added, it must be built
from matured prior opportunities with an explicit maturity-date guard before it
can be admitted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

from .config_snapshot import load_effective_config
from .data_loader import load_underlying_daily_flow
from .diagnostics import write_csv, write_json
from .paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, ensure_server_deploy_importable, resolve_path
from .signal_feature_utils import ensure_signal_iv, signal_iv_col


EXCLUDED_EXCHANGES = {"SSE", "SZSE"}
FORBIDDEN_INPUT_PREFIXES = ("shadow_",)
FORBIDDEN_INPUT_TOKENS = (
    "expiry_pnl",
    "terminal_otm",
    "path_stop",
    "path_safe",
    "future_label",
    "forward_label",
)

PRODUCTION_MAIN_SELECTED_COLUMNS = [
    "product_key",
    "exchange",
    "product",
    "product_label",
    "entry_date",
    "prev_expiry",
    "target_expiry",
    "expiry_date",
    "trade_date",
    "contract_code",
    "option_type",
    "strike",
    "dte",
    "close",
    "entry_price",
    "volume",
    "close_oi",
    "implied_vol",
    "delta",
    "abs_delta",
    "moneyness",
    "spot_close",
    "multiplier",
    "premium_margin",
    "one_contract_margin",
    "one_lot_margin_cash",
    "gross_premium_cash_1lot",
    "put_iv_pressure",
    "call_iv_pressure",
    "put_candidate_count",
    "call_candidate_count",
    "sell_side",
    "selected_side_iv_pressure",
    "other_side_iv_pressure",
    "side_iv_pressure_diff",
    "hist_iv_days_756",
    "hist_jump5pp_rate_756",
    "hist_p95_abs_iv_chg_756",
    "candidate_signal_days_252",
    "candidate_pm_median_252",
    "candidate_oi_median_252",
    "rolling_cs_score",
    "rolling_cs_rank",
    "rolling_cs_count",
    "option_side_oi",
    "option_side_volume",
    "option_side_count",
    "option_side_iv",
    "opt_side_oi_x63",
    "opt_side_volume_x63",
    "opt_side_oi_chg5",
    "opt_side_volume_chg5",
    "fut_oi",
    "fut_oi_chg5",
    "fut_oi_chg20",
    "fut_volume_x63",
    "fut_oi_x63",
    "futures_flow_source_table",
    "pit_low_jump_strict",
    "rule_l1_hsafe_core",
    "rule_l1_hsafe_addon025",
    "side_rule",
    "extra_contract_mode",
    "target_premium_pct_override",
    "l3eff015_tier",
    "l4_diff02_weak_pressure",
    "l4_diff02_delta_cap",
    "l3_weak_pressure_threshold",
    "l4_weak_pressure_threshold",
    "group_margin_trigger_margin_pct",
    "group_margin_trigger_budget_cash",
    "capped_by_group_margin_trigger",
]

DEFAULT_MAIN_MIN_DTE = 15
DEFAULT_MAIN_MAX_DTE = 90
DEFAULT_LOWJUMP_LOOKBACK = 756
DEFAULT_LOWJUMP_MIN_HISTORY = 120
DEFAULT_FLOW_LOOKBACK = 63
DEFAULT_CAPITAL = 50_000_000.0

SIDE_FLOW_PANEL_COLUMNS = [
    "trade_date",
    "exchange",
    "product",
    "sell_side",
    "option_side_oi",
    "option_side_volume",
    "option_side_count",
    "option_side_iv",
    "fut_volume",
    "fut_open_interest",
    "fut_close",
    "fut_settlement",
    "futures_flow_source_table",
]

DEFAULT_SCHEDULE_COLUMNS = [
    "entry_date",
    "product",
    "exchange",
    "contract_code",
    "option_type",
    "target_expiry",
    "dte",
    "trend_20d",
    "delta",
    "close_oi",
    "volume",
    "entry_price",
    "strike",
    "spot_close",
    "sell_side",
    "side_rule",
    "put_iv_pressure",
    "call_iv_pressure",
    "selected_side_iv_pressure",
    "other_side_iv_pressure",
    "side_iv_pressure_diff",
    "qty",
    "target_qty",
    "qty_fill_ratio",
    "premium_cash",
    "target_premium_cash",
    "target_premium_pct",
    "budget_group",
    "current_group_premium_cash_before",
    "group_premium_budget_cash",
    "group_premium_cap_cash",
    "portfolio_group_premium_cap_pct_nav",
    "capped_by_group_premium",
    "corr_peer_count",
    "corr_peer_products",
    "current_corr_premium_cash_before",
    "corr_premium_budget_cash",
    "corr_premium_cap_cash",
    "portfolio_corr_premium_cap_pct_nav",
    "portfolio_corr_threshold",
    "capped_by_corr_premium",
    "portfolio_group_margin_trigger_pct_nav",
    "portfolio_group_new_trade_cap_after_trigger_pct_nav",
    "group_margin_trigger_margin_pct",
    "group_margin_trigger_budget_cash",
    "capped_by_group_margin_trigger",
    "margin_cash",
    "current_margin_cash_before",
    "post_open_margin_pct_nav",
    "margin_budget_cash",
    "one_lot_margin_cash",
    "max_qty_by_margin",
    "capped_by_margin",
    "entry_reason",
    "reentry_count",
    "trigger_contract_code",
    "trigger_entry_price",
    "trigger_last_price",
    "trigger_decay_ratio",
    "overlay_atm_iv",
    "overlay_iv_percentile",
    "overlay_iv_prior_days",
    "overlay_atm_option_obs",
    "overlay_signal_rule",
    "overlay_trigger_trade_date",
    "overlay_atm_iv_lag1",
    "overlay_atm_iv_lag2",
    "overlay_atm_iv_lag3",
    "overlay_atm_iv_lag4",
    "overlay_iv_percentile_lag1",
    "overlay_iv_percentile_lag2",
    "overlay_iv_percentile_lag3",
    "overlay_iv_percentile_lag4",
    "strategy_layer",
    "overlay_trigger_iv_percentile",
    "overlay_tier_threshold",
    "overlay_tier_max_abs_delta",
    "overlay_tier_target_premium_pct",
    "overlay_iv_pullback_pct",
    "overlay_entry_iv_rebound_pct",
    "overlay_strategy",
    "overlay_signal_family",
    "overlay_side_rule",
    "overlay_priority",
    "forced_sell_side",
    "risk_reversal_lag1",
    "risk_reversal_lag2",
    "risk_reversal_lag3",
    "abs_risk_reversal_lag1",
    "abs_risk_reversal_lag2",
    "abs_risk_reversal_lag3",
    "put_iv_lag1",
    "call_iv_lag1",
    "put_iv_lag2",
    "call_iv_lag2",
    "put_iv_lag3",
    "call_iv_lag3",
    "term_spread_lag1",
    "term_spread_lag2",
    "term_spread_lag3",
    "near_atm_iv_lag1",
    "next_atm_iv_lag1",
    "near_atm_iv_lag2",
    "next_atm_iv_lag2",
    "near_atm_iv_lag3",
    "next_atm_iv_lag3",
    "t1_trend_20d",
    "t1_side",
    "t1_trend_conflict",
    "t1_side_skip_reason",
]


@dataclass(frozen=True)
class DailySignalAppendResult:
    signal_date: str
    schedule_path: Path
    rows_for_date: int
    output_rows: int
    diagnostics_path: Path
    intent_rows: pd.DataFrame
    diagnostics: dict[str, Any]


def _date_tag(date: str) -> str:
    return str(date)[:10].replace("-", "")


def _snapshot_path(data_dir: Path, date: str) -> Path:
    return data_dir / "daily_snapshots" / f"option_chain_{_date_tag(date)}.csv"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _safe_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def _safe_int(value: Any, default: int = 0) -> int:
    out = _safe_float(value, np.nan)
    if not np.isfinite(out):
        return default
    return int(math.floor(out))


def _normalize_date_series(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce").dt.strftime("%Y-%m-%d")


def _is_put(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.startswith("P")


def _is_call(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.startswith("C")


def _assert_no_forbidden_columns(frame: pd.DataFrame, source: str) -> None:
    bad = []
    for column in frame.columns:
        lower = str(column).lower()
        if lower.startswith(FORBIDDEN_INPUT_PREFIXES) or any(token in lower for token in FORBIDDEN_INPUT_TOKENS):
            bad.append(str(column))
    if bad:
        raise ValueError(
            f"{source} contains research outcome/shadow columns that are forbidden in production signal generation: {bad[:20]}"
        )


def sanitize_main_selected_for_production(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only PIT fields needed by the live main-sleeve order builder."""
    if frame.empty:
        return frame.copy()
    out = frame[[column for column in PRODUCTION_MAIN_SELECTED_COLUMNS if column in frame.columns]].copy()
    if "entry_price" not in out.columns and "close" in out.columns:
        out["entry_price"] = out["close"]
    if "one_lot_margin_cash" not in out.columns and "one_contract_margin" in out.columns:
        out["one_lot_margin_cash"] = out["one_contract_margin"]
    if "product" not in out.columns and "product_label" in out.columns:
        out["product"] = out["product_label"]
    _assert_no_forbidden_columns(out, "main selected PIT source")
    return out


def apply_matured_history_guard(
    frame: pd.DataFrame,
    *,
    signal_date: str,
    maturity_col: str,
    source: str,
) -> pd.DataFrame:
    """Return only rows whose prior opportunity had matured before signal_date.

    This is intentionally strict and unused by the current production factors,
    because the current L1/L2/L3/L4 appender avoids label-derived history
    entirely. It is kept here as the only admissible entry point if a future
    historical-performance factor is added.
    """
    if maturity_col not in frame.columns:
        raise ValueError(f"{source} missing required maturity guard column: {maturity_col}")
    maturity = pd.to_datetime(frame[maturity_col], errors="coerce")
    signal = pd.Timestamp(str(signal_date)[:10])
    return frame[maturity.notna() & maturity.lt(signal)].copy()


def _replace_date_rows(existing: pd.DataFrame, new_rows: pd.DataFrame, date: str) -> pd.DataFrame:
    if existing.empty:
        out = new_rows.copy()
    else:
        kept = existing[pd.to_datetime(existing.get("entry_date"), errors="coerce").dt.strftime("%Y-%m-%d").ne(date)].copy()
        out = kept if new_rows.empty else pd.concat([kept, new_rows], ignore_index=True, sort=False)
    return _canonical_schedule(out)


def _canonical_schedule(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    for column in ("entry_date", "target_expiry"):
        if column in out.columns:
            out[column] = _normalize_date_series(out[column]).fillna("")
    for column in ("product", "exchange", "option_type", "sell_side"):
        if column in out.columns:
            out[column] = out[column].fillna("").astype(str).str.upper()
    for column in ("contract_code", "entry_reason", "strategy_layer", "side_rule", "budget_group"):
        if column in out.columns:
            out[column] = out[column].fillna("").astype(str)
    sort_cols = [c for c in ("entry_date", "entry_reason", "strategy_layer", "exchange", "product", "target_expiry", "option_type", "contract_code") if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, kind="mergesort")
    return out.reset_index(drop=True)


def _merge_panel(path: Path, rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    old = _read_csv(path)
    if rows.empty:
        return old if not old.empty else rows.copy()
    if old.empty:
        merged = rows.copy()
    else:
        merged = pd.concat([old, rows], ignore_index=True, sort=False)
        merged = merged.drop_duplicates(keys, keep="last")
    for column in ("trade_date", "entry_date"):
        if column in merged.columns:
            merged[column] = _normalize_date_series(merged[column])
    return merged.sort_values(keys, kind="mergesort").reset_index(drop=True)


def _expanding_percentile_including_current(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    nums = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(nums), np.nan)
    prior = np.zeros(len(nums), dtype=int)
    history: list[float] = []
    for idx, value in enumerate(nums):
        clean = [x for x in history if np.isfinite(x)]
        prior[idx] = len(clean)
        if np.isfinite(value) and clean:
            out[idx] = sum(x <= value for x in clean) / len(clean)
        if np.isfinite(value):
            history.append(float(value))
        else:
            history.append(np.nan)
    return pd.Series(out, index=values.index), pd.Series(prior, index=values.index)


def _add_group_lags(frame: pd.DataFrame, group_cols: list[str], cols: list[str], lags: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.sort_values(group_cols + ["trade_date"], kind="mergesort").copy()
    grouped = out.groupby(group_cols, sort=False)
    for col in cols:
        if col not in out.columns:
            continue
        for lag in range(1, lags + 1):
            out[f"{col}_lag{lag}"] = grouped[col].shift(lag)
    return out


def _product_from_code(value: Any) -> str:
    text = str(value or "").strip()
    base = text.split(".", 1)[0]
    match = re.match(r"([A-Za-z]+)", base)
    return match.group(1).upper() if match else ""


def _normalize_option_type(value: Any, option_code: Any = "") -> str:
    text = str(value or "").upper().strip()
    if text.startswith("P"):
        return "P"
    if text.startswith("C"):
        return "C"
    code = str(option_code or "").upper()
    if "-P-" in code or re.search(r"P\d", code):
        return "P"
    if "-C-" in code or re.search(r"C\d", code):
        return "C"
    return ""


def _format_contract_code(row: pd.Series) -> str:
    raw = str(row.get("option_code", "") or row.get("ths_code", "") or "").strip()
    exchange = str(row.get("exchange", "") or "").upper()
    if not raw:
        return ""
    if "." in raw:
        body, suffix = raw.rsplit(".", 1)
        suffix = suffix.upper()
        exchange = exchange or {
            "SHF": "SHFE",
            "CZC": "CZCE",
            "CFE": "CFFEX",
            "GFE": "GFEX",
            "DCE": "DCE",
            "INE": "INE",
        }.get(suffix, suffix)
    else:
        body = raw
    product = str(row.get("product", "") or _product_from_code(body)).upper()
    if exchange in {"DCE", "CFFEX", "INE", "GFEX"} and re.match(r"^[A-Za-z]+\d+-[CP]-", body):
        if exchange in {"DCE", "INE", "GFEX"}:
            body = body[: len(product)].lower() + body[len(product):]
        return f"{exchange}.{body}"
    if exchange in {"SHFE", "CZCE"} and re.match(r"^[A-Za-z]+\d+[CP]\d", body):
        if exchange == "SHFE":
            body = body[: len(product)].lower() + body[len(product):]
        return f"{exchange}.{body}"
    return f"{exchange}.{body}" if exchange and not raw.upper().startswith(exchange + ".") else raw


def _normalize_snapshot(snapshot: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    _assert_no_forbidden_columns(snapshot, "daily option snapshot")
    if snapshot.empty:
        return snapshot.copy()
    out = snapshot.copy()
    if "option_code" not in out.columns and "ths_code" in out.columns:
        out["option_code"] = out["ths_code"]
    out["trade_date"] = _normalize_date_series(out.get("trade_date", pd.Series(signal_date, index=out.index))).fillna(str(signal_date)[:10])
    out["product"] = out.get("product", pd.Series("", index=out.index)).fillna("").astype(str).str.upper()
    missing_product = out["product"].eq("")
    if "option_code" in out.columns:
        out.loc[missing_product, "product"] = out.loc[missing_product, "option_code"].map(_product_from_code)
    out["exchange"] = out.get("exchange", pd.Series("", index=out.index)).fillna("").astype(str).str.upper()
    out["option_type"] = [
        _normalize_option_type(value, code)
        for value, code in zip(out.get("option_type", pd.Series("", index=out.index)), out.get("option_code", pd.Series("", index=out.index)))
    ]
    if "option_close" in out.columns and "close" not in out.columns:
        out["close"] = out["option_close"]
    if "open_interest" in out.columns and "close_oi" not in out.columns:
        out["close_oi"] = out["open_interest"]
    for column in ("close", "option_close", "option_high", "volume", "close_oi", "open_interest", "strike", "spot_close", "implied_vol", "delta", "gamma", "vega", "theta", "multiplier", "vwap"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out["entry_price"] = out.get("close", pd.Series(np.nan, index=out.index))
    if "expiry_date" in out.columns:
        out["target_expiry"] = _normalize_date_series(out["expiry_date"])
    elif "target_expiry" not in out.columns:
        out["target_expiry"] = ""
    if "dte" not in out.columns or out["dte"].isna().all():
        out["dte"] = (
            pd.to_datetime(out["target_expiry"], errors="coerce")
            - pd.to_datetime(out["trade_date"], errors="coerce")
        ).dt.days
    out["dte"] = pd.to_numeric(out["dte"], errors="coerce")
    if "moneyness" not in out.columns:
        out["moneyness"] = out["strike"] / out["spot_close"]
    out["moneyness"] = pd.to_numeric(out["moneyness"], errors="coerce")
    out["abs_delta"] = pd.to_numeric(out.get("delta"), errors="coerce").abs()
    out["contract_code"] = out.apply(_format_contract_code, axis=1)
    return out


def _otm_mask(frame: pd.DataFrame, side: str | None = None) -> pd.Series:
    side_series = frame["option_type"].fillna("").astype(str).str.upper()
    if side:
        side_series = pd.Series(str(side).upper(), index=frame.index)
    put = side_series.eq("P") & pd.to_numeric(frame["strike"], errors="coerce").lt(pd.to_numeric(frame["spot_close"], errors="coerce"))
    call = side_series.eq("C") & pd.to_numeric(frame["strike"], errors="coerce").gt(pd.to_numeric(frame["spot_close"], errors="coerce"))
    return put | call


def _valid_option_mask(frame: pd.DataFrame, *, min_oi: float = 0.0, min_volume: float = 0.0, max_abs_delta: float | None = None) -> pd.Series:
    iv_col = signal_iv_col(frame)
    iv_series = frame[iv_col] if iv_col in frame.columns else pd.Series(np.nan, index=frame.index)
    mask = (
        pd.to_numeric(frame.get("entry_price"), errors="coerce").gt(0)
        & pd.to_numeric(iv_series, errors="coerce").gt(0.01)
        & pd.to_numeric(iv_series, errors="coerce").lt(2.5)
        & pd.to_numeric(frame.get("close_oi"), errors="coerce").ge(min_oi)
        & pd.to_numeric(frame.get("volume"), errors="coerce").ge(min_volume)
        & pd.to_numeric(frame.get("strike"), errors="coerce").gt(0)
        & pd.to_numeric(frame.get("spot_close"), errors="coerce").gt(0)
        & frame.get("option_type", pd.Series("", index=frame.index)).isin(["P", "C"])
    )
    if max_abs_delta is not None:
        mask &= pd.to_numeric(frame.get("abs_delta"), errors="coerce").lt(max_abs_delta)
    return mask


def _select_contract(frame: pd.DataFrame, side: str, expiry: str, *, max_abs_delta: float, min_oi: float, min_volume: float) -> pd.Series | None:
    base = frame[
        frame["option_type"].eq(side)
        & frame["target_expiry"].eq(expiry)
        & _otm_mask(frame, side)
    ].copy()
    subset = ensure_signal_iv(base, price_col="entry_price")
    subset = subset[_valid_option_mask(subset, min_oi=min_oi, min_volume=min_volume, max_abs_delta=max_abs_delta)].copy()
    if subset.empty:
        return None
    subset = subset.sort_values(["abs_delta", "close_oi", "volume", "entry_price"], ascending=[False, False, False, False], kind="mergesort")
    return subset.iloc[0]


def _nearest_expiry(frame: pd.DataFrame, min_dte: float, max_dte: float | None = None) -> str | None:
    mask = pd.to_numeric(frame["dte"], errors="coerce").ge(min_dte)
    if max_dte is not None:
        mask &= pd.to_numeric(frame["dte"], errors="coerce").le(max_dte)
    values = frame.loc[mask, ["target_expiry", "dte"]].dropna().drop_duplicates()
    if values.empty:
        return None
    values = values.sort_values(["dte", "target_expiry"], kind="mergesort")
    return str(values.iloc[0]["target_expiry"])[:10]


def _front_expiry_if_eligible(frame: pd.DataFrame, min_dte: float, max_dte: float | None = None) -> str | None:
    """Return the front live expiry only if that exact expiry passes DTE guards."""
    values = frame[["target_expiry", "dte"]].dropna().drop_duplicates().copy()
    if values.empty:
        return None
    values["dte"] = pd.to_numeric(values["dte"], errors="coerce")
    values = values[values["dte"].notna()]
    if values.empty:
        return None
    values = values.sort_values(["dte", "target_expiry"], kind="mergesort")
    front = values.iloc[0]
    dte = float(front["dte"])
    if dte < float(min_dte):
        return None
    if max_dte is not None and dte > float(max_dte):
        return None
    return str(front["target_expiry"])[:10]


def _estimate_margin(row: pd.Series, config: dict[str, Any]) -> float:
    existing = _safe_float(row.get("one_lot_margin_cash", row.get("one_contract_margin")))
    if np.isfinite(existing) and existing > 0:
        return existing
    ensure_server_deploy_importable()
    from margin_model import estimate_margin

    return float(
        estimate_margin(
            row.get("spot_close"),
            row.get("strike"),
            row.get("option_type"),
            row.get("entry_price", row.get("close")),
            row.get("multiplier"),
            exchange=row.get("exchange"),
            product=row.get("product", row.get("product_label")),
        )
    )


def _budget_group(product: str, side: str, groups: dict[str, list[str]]) -> str:
    product = str(product).upper()
    for group, products in groups.items():
        if product in {str(p).upper() for p in products}:
            return f"{group}|{side}"
    return f"OTHER|{side}"


def _schedule_columns(schedule: pd.DataFrame) -> list[str]:
    if not schedule.empty:
        return list(schedule.columns)
    return DEFAULT_SCHEDULE_COLUMNS.copy()


def _base_schedule_row(columns: list[str]) -> dict[str, Any]:
    return {column: np.nan for column in columns}


def _size_row(
    row: dict[str, Any],
    *,
    nav: float,
    current_margin_cash: float,
    margin_cap: float,
    target_premium_pct: float,
) -> dict[str, Any]:
    price = _safe_float(row.get("entry_price"), 0.0)
    multiplier = _safe_float(row.get("multiplier"), 0.0)
    one_lot_margin = _safe_float(row.get("one_lot_margin_cash"), 0.0)
    target_cash = nav * target_premium_pct
    target_qty = int(math.floor(target_cash / (price * multiplier))) if price > 0 and multiplier > 0 else 0
    margin_budget = max(nav * margin_cap - current_margin_cash, 0.0)
    max_qty_by_margin = int(math.floor(margin_budget / one_lot_margin)) if one_lot_margin > 0 else target_qty
    qty = max(min(target_qty, max_qty_by_margin), 0)
    row["target_premium_pct"] = target_premium_pct
    row["target_premium_cash"] = target_cash
    row["target_qty"] = target_qty
    row["qty"] = qty
    row["qty_fill_ratio"] = qty / target_qty if target_qty > 0 else 0.0
    row["premium_cash"] = qty * price * multiplier
    row["margin_cash"] = qty * one_lot_margin
    row["current_margin_cash_before"] = current_margin_cash
    row["margin_budget_cash"] = margin_budget
    row["max_qty_by_margin"] = max_qty_by_margin
    row["capped_by_margin"] = bool(max_qty_by_margin < target_qty)
    row["post_open_margin_pct_nav"] = (current_margin_cash + row["margin_cash"]) / nav if nav > 0 else np.nan
    return row


class PitSignalAppender:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        schedule_path: str | Path | None = None,
    ) -> None:
        self.config_snapshot = load_effective_config(config_path or DEFAULT_PAPER_CONFIG)
        self.config_path = self.config_snapshot.path
        self.config = self.config_snapshot.config
        self.data_dir = resolve_path(data_dir, default=DEFAULT_DATA_DIR)
        self.output_dir = resolve_path(output_dir, default=DEFAULT_OUTPUT_DIR)
        self.schedule_path = resolve_path(schedule_path or self.config.get("external_signal_path"))
        self._panel_cache: dict[str, pd.DataFrame] | None = None

    def append_date(
        self,
        signal_date: str,
        *,
        source_schedule: str | Path | None = None,
        output_schedule: str | Path | None = None,
        products: tuple[str, ...] | None = None,
        nav: float | None = None,
        current_margin_cash: float | None = None,
        replace_existing_date: bool = True,
        update_schedule: bool = True,
        refresh_panels: bool = True,
        write_outputs: bool = True,
        tag: str | None = None,
    ) -> DailySignalAppendResult:
        date = str(signal_date)[:10]
        source_path = resolve_path(source_schedule, default=self.schedule_path)
        output_path = resolve_path(output_schedule, default=self.schedule_path)
        source_read_path = source_path
        if not source_read_path.exists():
            gold_value = self.config.get("validation_gold_signal_path")
            gold_path = resolve_path(gold_value) if gold_value else None
            if gold_path is not None and gold_path.exists():
                source_read_path = gold_path
        schedule = _read_csv(source_read_path)
        columns = _schedule_columns(schedule)
        snapshot = self._load_snapshot(date)
        if products:
            keep = {p.upper() for p in products}
            snapshot = snapshot[snapshot["product"].isin(keep)].copy()
        diag: dict[str, Any] = {
            "tag": tag or f"pit_signal_append_{_date_tag(date)}",
            "signal_date": date,
            "config_path": str(self.config_path),
            "config_sha256": self.config_snapshot.sha256,
            "source_schedule": str(source_read_path),
            "output_schedule": str(output_path),
            "snapshot_rows": int(len(snapshot)),
            "shadow_input_policy": "blocked",
            "maturity_guard_policy": (
                "No shadow/outcome history is consumed. Any future historical-performance factor must filter "
                "prior rows where target_expiry/open outcome maturity date is strictly before signal_date."
            ),
        }

        nav_value = float(nav or self.config.get("capital") or DEFAULT_CAPITAL)
        margin_before = float(current_margin_cash or 0.0)
        panels = (
            self._refresh_panels(date, snapshot, products=products, diagnostics=diag)
            if refresh_panels
            else self._load_panels(diagnostics=diag)
        )
        rows = (
            self._build_all_intents(date, snapshot, panels, schedule, columns, nav_value, margin_before, diagnostics=diag)
            if update_schedule
            else []
        )
        new_rows = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
        _assert_no_forbidden_columns(new_rows, "generated intent rows")
        if update_schedule and not new_rows.empty and "target_expiry" in new_rows.columns:
            new_rows["target_expiry"] = _normalize_date_series(new_rows["target_expiry"]).fillna("")
            missing_expiry = new_rows["target_expiry"].astype(str).str.strip().eq("")
            if missing_expiry.any():
                sample = new_rows.loc[missing_expiry, ["entry_date", "product", "contract_code", "entry_reason"]].head(10).to_dict("records")
                raise ValueError(f"generated intent rows missing target_expiry from option maturity data: {sample}")
        if not update_schedule:
            output = schedule.copy()
        elif replace_existing_date:
            output = _replace_date_rows(schedule, new_rows, date)
        else:
            output = _canonical_schedule(pd.concat([schedule, new_rows], ignore_index=True, sort=False))
        for column in columns:
            if column not in output.columns:
                output[column] = np.nan
        output = output[columns]

        diag.update(
            {
                "nav_for_sizing": nav_value,
                "current_margin_cash_before": margin_before,
                "generated_rows_for_date": int(len(new_rows)),
                "output_rows": int(len(output)),
                "schedule_updated": bool(update_schedule),
                "panels_refreshed": bool(refresh_panels),
                "row_counts_by_entry_reason": new_rows.get("entry_reason", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict(),
                "row_counts_by_strategy_layer": new_rows.get("strategy_layer", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict(),
                "generated_columns": list(new_rows.columns),
                "passed": True,
            }
        )
        audit_path = self.output_dir / "audit" / f"{diag['tag']}.json"
        if write_outputs:
            if update_schedule:
                write_csv(output_path, output)
            write_json(audit_path, diag)
        return DailySignalAppendResult(
            signal_date=date,
            schedule_path=output_path,
            rows_for_date=int(len(new_rows)),
            output_rows=int(len(output)),
            diagnostics_path=audit_path,
            intent_rows=new_rows,
            diagnostics=diag,
        )

    def _panel_paths(self) -> dict[str, Path]:
        main_cfg = self.config.get("main_sleeve", {})
        return {
            "iv_daily": self.data_dir / "reverse_lowjump" / "iv_daily_panel.csv",
            "flow": self.data_dir / "reverse_lowjump" / "side_flow_guard_panel.csv",
            "pressure": self.data_dir / "reverse_lowjump" / "side_iv_pressure_panel.csv",
            "main_opportunities": self.data_dir / "reverse_lowjump" / "product_month_opportunities.csv",
            "main_selected": resolve_path(
                main_cfg.get("source_opportunity_file"),
                default=self.data_dir / "reverse_lowjump" / "live_product_side_opportunities.csv",
            ),
            "overlay_atm": self.data_dir / "iv_pullback_overlay" / "atm_iv_percentile_panel.csv",
            "rr": self.data_dir / "risk_reversal_sidecar" / "risk_reversal_panel.csv",
            "term": self.data_dir / "term_structure_sidecar" / "term_structure_panel.csv",
        }

    def _load_panels(self, *, diagnostics: dict[str, Any]) -> dict[str, pd.DataFrame]:
        if self._panel_cache is not None:
            panels = self._panel_cache
        else:
            panels = {name: _read_csv(path) for name, path in self._panel_paths().items()}
        if "main_selected" in panels and not panels["main_selected"].empty:
            _assert_no_forbidden_columns(panels["main_selected"], "main selected PIT source")
        diagnostics["panel_rows"] = {name: int(len(frame)) for name, frame in panels.items()}
        diagnostics["panels_loaded_from_existing_files"] = True
        return panels

    def preload_panels(self) -> dict[str, pd.DataFrame]:
        diagnostics: dict[str, Any] = {}
        self._panel_cache = {name: _read_csv(path) for name, path in self._panel_paths().items()}
        self._load_panels(diagnostics=diagnostics)
        return self._panel_cache

    def _load_snapshot(self, date: str) -> pd.DataFrame:
        path = _snapshot_path(self.data_dir, date)
        frame = _read_csv(path)
        if frame.empty:
            raise FileNotFoundError(f"missing daily snapshot for {date}: {path}")
        return _normalize_snapshot(frame, date)

    def _refresh_panels(
        self,
        date: str,
        snapshot: pd.DataFrame,
        *,
        products: tuple[str, ...] | None,
        diagnostics: dict[str, Any],
    ) -> dict[str, pd.DataFrame]:
        panels: dict[str, pd.DataFrame] = {}
        panels["iv_daily"] = self._refresh_iv_daily_panel(date, snapshot, self.data_dir / "reverse_lowjump" / "iv_daily_panel.csv")
        panels["flow"] = self._refresh_side_flow_panel(date, snapshot, products)
        panels["pressure"] = self._refresh_side_iv_pressure_panel(date, snapshot)
        panels["overlay_atm"] = self._refresh_overlay_atm_panel(date, snapshot)
        panels["rr"] = self._refresh_risk_reversal_panel(date, snapshot, panels["overlay_atm"])
        panels["term"] = self._refresh_term_structure_panel(date, snapshot, panels["pressure"])
        panels["main_selected"] = _read_csv(self._panel_paths()["main_selected"])
        if not panels["main_selected"].empty:
            _assert_no_forbidden_columns(panels["main_selected"], "main selected PIT source")
        diagnostics["panel_rows"] = {name: int(len(frame)) for name, frame in panels.items()}
        flow_today = panels["flow"][panels["flow"].get("trade_date", pd.Series(dtype=object)).eq(date)] if not panels["flow"].empty else pd.DataFrame()
        diagnostics["futures_oi_available_for_l1"] = bool(
            not flow_today.empty
            and "fut_open_interest" in flow_today.columns
            and pd.to_numeric(flow_today["fut_open_interest"], errors="coerce").gt(0).any()
        )
        if not diagnostics["futures_oi_available_for_l1"]:
            diagnostics.setdefault("notes", []).append(
                "No positive futures open-interest was available from Toolkit future_daily_quote/future_history_quote "
                "for this date/product slice; fut_oi_chg5/fut_oi_chg20 remain NaN and L1 flow guard can only pass "
                "via option-side volume ratio."
            )
        return panels

    def _refresh_iv_daily_panel(self, date: str, snapshot: pd.DataFrame, path: Path) -> pd.DataFrame:
        rows = self._build_atm_rows(date, snapshot, config=self.config.get("iv_pullback_sidecar", {}))
        panel = _merge_panel(path, rows, ["trade_date", "exchange", "product"])
        if not panel.empty:
            panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
            grouped = panel.groupby(["exchange", "product"], sort=False)
            panel["atm_iv_chg"] = grouped["atm_iv"].diff()
            panel["abs_atm_iv_chg"] = panel["atm_iv_chg"].abs()
            shifted_abs = grouped["abs_atm_iv_chg"].shift(1)
            shifted_atm = grouped["atm_iv"].shift(1)
            panel["hist_iv_days_756"] = shifted_atm.groupby([panel["exchange"], panel["product"]]).rolling(DEFAULT_LOWJUMP_LOOKBACK, min_periods=1).count().reset_index(level=[0, 1], drop=True)
            panel["hist_jump5pp_rate_756"] = shifted_abs.gt(0.05).groupby([panel["exchange"], panel["product"]]).rolling(DEFAULT_LOWJUMP_LOOKBACK, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
            panel["hist_p95_abs_iv_chg_756"] = shifted_abs.groupby([panel["exchange"], panel["product"]]).rolling(DEFAULT_LOWJUMP_LOOKBACK, min_periods=20).quantile(0.95).reset_index(level=[0, 1], drop=True)
            panel["pit_low_jump_strict"] = (
                pd.to_numeric(panel["hist_iv_days_756"], errors="coerce").ge(DEFAULT_LOWJUMP_MIN_HISTORY)
                & pd.to_numeric(panel["hist_jump5pp_rate_756"], errors="coerce").le(0.025)
                & pd.to_numeric(panel["hist_p95_abs_iv_chg_756"], errors="coerce").le(0.040)
            )
        write_csv(path, panel)
        return panel

    def _refresh_side_flow_panel(self, date: str, snapshot: pd.DataFrame, products: tuple[str, ...] | None) -> pd.DataFrame:
        main_cfg = self.config.get("main_sleeve", {})
        min_dte = float(main_cfg.get("min_dte", DEFAULT_MAIN_MIN_DTE))
        max_dte = float(main_cfg.get("max_dte", DEFAULT_MAIN_MAX_DTE))
        min_oi = float(main_cfg.get("min_oi", 1000))
        max_abs_delta = float(main_cfg.get("max_abs_delta", 0.08))
        rows: list[dict[str, Any]] = []
        valid = snapshot[snapshot["exchange"].isin(EXCLUDED_EXCHANGES).eq(False)].copy()
        for (exchange, product), group in valid.groupby(["exchange", "product"], sort=False):
            expiry = _front_expiry_if_eligible(group, min_dte, max_dte)
            if not expiry:
                continue
            candidates = group[
                group["target_expiry"].eq(expiry)
                & _otm_mask(group)
            ].copy()
            candidates = ensure_signal_iv(candidates, price_col="entry_price")
            candidates = candidates[_valid_option_mask(candidates, min_oi=min_oi, min_volume=1, max_abs_delta=max_abs_delta)].copy()
            if candidates.empty:
                continue
            for side, side_group in candidates.groupby("option_type", sort=False):
                iv_col = signal_iv_col(side_group)
                rows.append(
                    {
                        "trade_date": date,
                        "exchange": exchange,
                        "product": product,
                        "sell_side": side,
                        "option_side_oi": float(pd.to_numeric(side_group["close_oi"], errors="coerce").sum()),
                        "option_side_volume": float(pd.to_numeric(side_group["volume"], errors="coerce").sum()),
                        "option_side_count": int(len(side_group)),
                        "option_side_iv": float(pd.to_numeric(side_group[iv_col], errors="coerce").median()),
                    }
                )
        side_rows = pd.DataFrame(rows)
        path = self.data_dir / "reverse_lowjump" / "side_flow_guard_panel.csv"
        if side_rows.empty:
            side_rows = pd.DataFrame(columns=SIDE_FLOW_PANEL_COLUMNS)
            panel = _merge_panel(path, side_rows, ["trade_date", "exchange", "product", "sell_side"])
            write_csv(path, panel)
            return panel

        product_filter = products or tuple(sorted(p for p in side_rows["product"].dropna().astype(str).str.upper().unique() if p))
        if not product_filter:
            futures = pd.DataFrame(columns=["trade_date", "product", "fut_volume", "fut_open_interest", "fut_close", "fut_settlement", "futures_flow_source_table"])
        else:
            futures = None
        try:
            if futures is None:
                futures = load_underlying_daily_flow(date, self.config_path, products=product_filter)
        except Exception as exc:  # pragma: no cover - Toolkit may be unavailable locally.
            futures = pd.DataFrame(columns=["trade_date", "product", "fut_volume", "fut_open_interest", "fut_close"])
            side_rows["futures_flow_error"] = str(exc)
        stale_futures_cols = [
            "fut_volume",
            "fut_open_interest",
            "fut_close",
            "fut_settlement",
            "futures_flow_source_table",
        ]
        side_rows = side_rows.drop(columns=[c for c in stale_futures_cols if c in side_rows.columns], errors="ignore")
        if not futures.empty:
            futures = futures.copy()
            futures["trade_date"] = pd.to_datetime(futures["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            futures["product"] = futures["product"].astype(str).str.upper()
            keep_cols = ["trade_date", "product", *stale_futures_cols]
            futures = futures[[c for c in keep_cols if c in futures.columns]].drop_duplicates(["trade_date", "product"])
            side_rows["trade_date"] = pd.to_datetime(side_rows["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            side_rows["product"] = side_rows["product"].astype(str).str.upper()
            side_rows = side_rows.merge(futures, on=["trade_date", "product"], how="left")
        else:
            for column in ("fut_volume", "fut_open_interest", "fut_close", "fut_settlement"):
                side_rows[column] = np.nan
            side_rows["futures_flow_source_table"] = ""
        for column in SIDE_FLOW_PANEL_COLUMNS:
            if column not in side_rows.columns:
                side_rows[column] = np.nan if column.startswith(("fut_", "option_")) else ""
        panel = _merge_panel(path, side_rows, ["trade_date", "exchange", "product", "sell_side"])
        if not panel.empty:
            panel = panel.sort_values(["exchange", "product", "sell_side", "trade_date"], kind="mergesort")
            g_side = panel.groupby(["exchange", "product", "sell_side"], sort=False)
            oi_ref = g_side["option_side_oi"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).median().reset_index(level=[0, 1, 2], drop=True)
            vol_ref = g_side["option_side_volume"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).median().reset_index(level=[0, 1, 2], drop=True)
            oi_mean = g_side["option_side_oi"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).mean().reset_index(level=[0, 1, 2], drop=True)
            oi_std = g_side["option_side_oi"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).std().reset_index(level=[0, 1, 2], drop=True)
            vol_mean = g_side["option_side_volume"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).mean().reset_index(level=[0, 1, 2], drop=True)
            vol_std = g_side["option_side_volume"].shift(1).groupby([panel["exchange"], panel["product"], panel["sell_side"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).std().reset_index(level=[0, 1, 2], drop=True)
            panel["opt_side_oi_x63"] = pd.to_numeric(panel["option_side_oi"], errors="coerce") / oi_ref.replace(0, np.nan)
            panel["opt_side_volume_x63"] = pd.to_numeric(panel["option_side_volume"], errors="coerce") / vol_ref.replace(0, np.nan)
            panel["opt_side_oi_z63"] = (pd.to_numeric(panel["option_side_oi"], errors="coerce") - oi_mean) / oi_std.replace(0, np.nan)
            panel["opt_side_volume_z63"] = (pd.to_numeric(panel["option_side_volume"], errors="coerce") - vol_mean) / vol_std.replace(0, np.nan)
            panel["opt_side_oi_chg5"] = g_side["option_side_oi"].pct_change(5, fill_method=None)
            panel["opt_side_oi_chg20"] = g_side["option_side_oi"].pct_change(20, fill_method=None)
            panel["opt_side_volume_chg5"] = g_side["option_side_volume"].pct_change(5, fill_method=None)
            panel["opt_side_volume_chg20"] = g_side["option_side_volume"].pct_change(20, fill_method=None)
            g_prod = panel.drop_duplicates(["trade_date", "exchange", "product"]).sort_values(["exchange", "product", "trade_date"]).groupby(["exchange", "product"], sort=False)
            prod_flow = panel[["trade_date", "exchange", "product", "fut_open_interest", "fut_volume", "fut_close"]].drop_duplicates(["trade_date", "exchange", "product"]).copy()
            prod_flow = prod_flow.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
            gp = prod_flow.groupby(["exchange", "product"], sort=False)
            prod_flow["fut_oi"] = pd.to_numeric(prod_flow["fut_open_interest"], errors="coerce")
            prod_flow["fut_oi_chg5"] = gp["fut_open_interest"].pct_change(5, fill_method=None)
            prod_flow["fut_oi_chg20"] = gp["fut_open_interest"].pct_change(20, fill_method=None)
            fut_vol_ref = gp["fut_volume"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).median().reset_index(level=[0, 1], drop=True)
            fut_vol_mean = gp["fut_volume"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).mean().reset_index(level=[0, 1], drop=True)
            fut_vol_std = gp["fut_volume"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).std().reset_index(level=[0, 1], drop=True)
            fut_oi_ref = gp["fut_open_interest"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).median().reset_index(level=[0, 1], drop=True)
            fut_oi_mean = gp["fut_open_interest"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).mean().reset_index(level=[0, 1], drop=True)
            fut_oi_std = gp["fut_open_interest"].shift(1).groupby([prod_flow["exchange"], prod_flow["product"]]).rolling(DEFAULT_FLOW_LOOKBACK, min_periods=5).std().reset_index(level=[0, 1], drop=True)
            prod_flow["fut_volume_x63"] = pd.to_numeric(prod_flow["fut_volume"], errors="coerce") / fut_vol_ref.replace(0, np.nan)
            prod_flow["fut_volume_z63"] = (pd.to_numeric(prod_flow["fut_volume"], errors="coerce") - fut_vol_mean) / fut_vol_std.replace(0, np.nan)
            prod_flow["fut_oi_x63"] = pd.to_numeric(prod_flow["fut_open_interest"], errors="coerce") / fut_oi_ref.replace(0, np.nan)
            prod_flow["fut_oi_z63"] = (pd.to_numeric(prod_flow["fut_open_interest"], errors="coerce") - fut_oi_mean) / fut_oi_std.replace(0, np.nan)
            panel = panel.drop(columns=["fut_oi", "fut_oi_chg5", "fut_oi_chg20", "fut_volume_x63", "fut_volume_z63", "fut_oi_x63", "fut_oi_z63"], errors="ignore").merge(
                prod_flow[["trade_date", "exchange", "product", "fut_oi", "fut_oi_chg5", "fut_oi_chg20", "fut_volume_x63", "fut_volume_z63", "fut_oi_x63", "fut_oi_z63"]],
                on=["trade_date", "exchange", "product"],
                how="left",
            )
            panel["flow_guard_pass"] = (
                pd.to_numeric(panel["opt_side_oi_x63"], errors="coerce").ge(0.3)
                & (
                    pd.to_numeric(panel["fut_oi_chg5"], errors="coerce").gt(0)
                    | pd.to_numeric(panel["opt_side_volume_x63"], errors="coerce").le(1.0)
                )
            )
        write_csv(path, panel)
        return panel

    def _refresh_side_iv_pressure_panel(self, date: str, snapshot: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        main_cfg = self.config.get("main_sleeve", {})
        min_dte = float(main_cfg.get("min_dte", DEFAULT_MAIN_MIN_DTE))
        max_dte = float(main_cfg.get("max_dte", DEFAULT_MAIN_MAX_DTE))
        min_oi = float(main_cfg.get("min_oi", 1000))
        for (exchange, product), group in snapshot[snapshot["exchange"].isin(EXCLUDED_EXCHANGES).eq(False)].groupby(["exchange", "product"], sort=False):
            expiry = _front_expiry_if_eligible(group, min_dte, max_dte)
            if not expiry:
                continue
            candidates = group[
                group["target_expiry"].eq(expiry)
                & _otm_mask(group)
            ].copy()
            candidates = ensure_signal_iv(candidates, price_col="entry_price")
            candidates = candidates[_valid_option_mask(candidates, min_oi=min_oi, min_volume=1, max_abs_delta=float(main_cfg.get("max_abs_delta", 0.08)))].copy()
            iv_col = signal_iv_col(candidates)
            side_iv = candidates.groupby("option_type")[iv_col].median().to_dict() if not candidates.empty else {}
            put_iv = _safe_float(side_iv.get("P"))
            call_iv = _safe_float(side_iv.get("C"))
            selected = ""
            other = np.nan
            selected_pressure = np.nan
            if np.isfinite(put_iv) and (not np.isfinite(call_iv) or put_iv > call_iv):
                selected = "P"
                selected_pressure = put_iv
                other = call_iv
            elif np.isfinite(call_iv):
                selected = "C"
                selected_pressure = call_iv
                other = put_iv
            diff = abs(put_iv - call_iv) if np.isfinite(put_iv) and np.isfinite(call_iv) else np.nan
            rows.append(
                {
                    "trade_date": date,
                    "exchange": exchange,
                    "product": product,
                    "target_expiry": expiry,
                    "target_dte": float(pd.to_numeric(group.loc[group["target_expiry"].eq(expiry), "dte"], errors="coerce").median()),
                    "put_iv_pressure": put_iv,
                    "call_iv_pressure": call_iv,
                    "selected_side": selected,
                    "selected_side_iv_pressure": selected_pressure,
                    "other_side_iv_pressure": other,
                    "side_iv_pressure_diff": diff,
                    "weak_pressure": bool(np.isfinite(diff) and diff < 0.02),
                    "candidate_obs": int(len(candidates)),
                    "put_candidate_obs": int(candidates["option_type"].eq("P").sum()) if not candidates.empty else 0,
                    "call_candidate_obs": int(candidates["option_type"].eq("C").sum()) if not candidates.empty else 0,
                }
            )
        path = self.data_dir / "reverse_lowjump" / "side_iv_pressure_panel.csv"
        panel = _merge_panel(path, pd.DataFrame(rows), ["trade_date", "exchange", "product"])
        write_csv(path, panel)
        return panel

    def _build_atm_rows(self, date: str, snapshot: pd.DataFrame, *, config: dict[str, Any]) -> pd.DataFrame:
        dte_min = float(config.get("atm_iv_dte_min", 7))
        dte_max = float(config.get("atm_iv_dte_max", 90))
        mon_min = float(config.get("atm_iv_moneyness_min", 0.95))
        mon_max = float(config.get("atm_iv_moneyness_max", 1.05))
        iv_min = float(config.get("atm_iv_min", config.get("iv_min", 0.01)))
        iv_max = float(config.get("atm_iv_max", config.get("iv_max", 2.5)))
        work = snapshot[
            snapshot["exchange"].isin(set(config.get("exclude_exchanges", EXCLUDED_EXCHANGES))).eq(False)
            & pd.to_numeric(snapshot["dte"], errors="coerce").between(dte_min, dte_max)
            & pd.to_numeric(snapshot["moneyness"], errors="coerce").between(mon_min, mon_max)
            & pd.to_numeric(snapshot["entry_price"], errors="coerce").gt(0)
        ].copy()
        work = ensure_signal_iv(work, price_col="entry_price", iv_min=iv_min, iv_max=iv_max)
        iv_col = signal_iv_col(work)
        work = work[pd.to_numeric(work[iv_col], errors="coerce").gt(iv_min) & pd.to_numeric(work[iv_col], errors="coerce").lt(iv_max)].copy()
        if work.empty:
            return pd.DataFrame(columns=["trade_date", "exchange", "product", "atm_iv", "atm_option_obs", "spot_close"])
        rows = (
            work.groupby(["trade_date", "exchange", "product"], as_index=False)
            .agg(
                atm_iv=(iv_col, "median"),
                atm_option_obs=(iv_col, "count"),
                spot_close=("spot_close", "median"),
            )
        )
        rows["trade_date"] = date
        return rows

    def _refresh_overlay_atm_panel(self, date: str, snapshot: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config.get("iv_pullback_sidecar", {})
        rows = self._build_atm_rows(date, snapshot, config=cfg)
        path = self.data_dir / "iv_pullback_overlay" / "atm_iv_percentile_panel.csv"
        panel = _merge_panel(path, rows, ["trade_date", "exchange", "product"])
        if not panel.empty:
            panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
            pieces = []
            for _, g in panel.groupby(["exchange", "product"], sort=False):
                pct, prior = _expanding_percentile_including_current(g["atm_iv"])
                h = g.copy()
                h["iv_percentile"] = pct
                h["iv_prior_days"] = prior
                pieces.append(h)
            panel = pd.concat(pieces, ignore_index=True, sort=False)
            panel = _add_group_lags(panel, ["exchange", "product"], ["atm_iv", "iv_percentile", "iv_prior_days"], 4)
        write_csv(path, panel)
        return panel

    def _refresh_risk_reversal_panel(self, date: str, snapshot: pd.DataFrame, atm_panel: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config.get("risk_reversal_sidecar", {})
        rows = []
        sample_min = float(cfg.get("rr_sample_abs_delta_min", 0.01))
        sample_max = float(cfg.get("rr_sample_abs_delta_max", 0.30))
        for (exchange, product), group in snapshot[snapshot["exchange"].isin(set(cfg.get("exclude_exchanges", EXCLUDED_EXCHANGES))).eq(False)].groupby(["exchange", "product"], sort=False):
            expiry = _front_expiry_if_eligible(group, float(cfg.get("rr_sample_dte_min", 10)), float(cfg.get("rr_sample_dte_max", 90)))
            if not expiry:
                continue
            sample = group[
                group["target_expiry"].eq(expiry)
                & _otm_mask(group)
                & pd.to_numeric(group["abs_delta"], errors="coerce").between(sample_min, sample_max)
                & pd.to_numeric(group["entry_price"], errors="coerce").gt(0)
            ].copy()
            sample = ensure_signal_iv(sample, price_col="entry_price", iv_min=float(cfg.get("iv_min", 0.01)), iv_max=float(cfg.get("iv_max", 2.5)))
            rr_iv_col = signal_iv_col(sample)
            sample = sample[pd.to_numeric(sample[rr_iv_col], errors="coerce").gt(float(cfg.get("iv_min", 0.01))) & pd.to_numeric(sample[rr_iv_col], errors="coerce").lt(float(cfg.get("iv_max", 2.5)))].copy()
            side_agg = sample.groupby("option_type")[rr_iv_col].agg(["median", "size"]) if not sample.empty else pd.DataFrame()
            put_obs = int(side_agg.loc["P", "size"]) if "P" in side_agg.index else 0
            call_obs = int(side_agg.loc["C", "size"]) if "C" in side_agg.index else 0
            if put_obs < 2 or call_obs < 2:
                continue
            side_iv = side_agg["median"].to_dict()
            put_iv = _safe_float(side_iv.get("P"))
            call_iv = _safe_float(side_iv.get("C"))
            rr = call_iv - put_iv if np.isfinite(call_iv) and np.isfinite(put_iv) else np.nan
            atm_row = atm_panel[
                atm_panel["trade_date"].eq(date)
                & atm_panel["exchange"].eq(exchange)
                & atm_panel["product"].eq(product)
            ]
            rows.append(
                {
                    "trade_date": date,
                    "exchange": exchange,
                    "product": product,
                    "target_expiry": expiry,
                    "put_iv": put_iv,
                    "call_iv": call_iv,
                    "risk_reversal": rr,
                    "abs_risk_reversal": abs(rr) if np.isfinite(rr) else np.nan,
                    "atm_iv": _safe_float(atm_row["atm_iv"].iloc[-1]) if not atm_row.empty else np.nan,
                    "iv_percentile": _safe_float(atm_row["iv_percentile"].iloc[-1]) if not atm_row.empty and "iv_percentile" in atm_row else np.nan,
                    "iv_prior_days": _safe_float(atm_row["iv_prior_days"].iloc[-1]) if not atm_row.empty and "iv_prior_days" in atm_row else np.nan,
                    "sample_obs": int(len(sample)),
                    "put_obs": put_obs,
                    "call_obs": call_obs,
                }
            )
        path = self.data_dir / "risk_reversal_sidecar" / "risk_reversal_panel.csv"
        panel = _merge_panel(path, pd.DataFrame(rows), ["trade_date", "exchange", "product"])
        if not panel.empty:
            panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
            pieces = []
            for _, g in panel.groupby(["exchange", "product"], sort=False):
                pct, prior = _expanding_percentile_including_current(g["abs_risk_reversal"])
                h = g.copy()
                h["iv_percentile"] = pct
                h["iv_prior_days"] = prior
                pieces.append(h)
            panel = pd.concat(pieces, ignore_index=True, sort=False)
            panel = _add_group_lags(panel, ["exchange", "product"], ["risk_reversal", "abs_risk_reversal", "put_iv", "call_iv", "iv_percentile", "iv_prior_days"], 3)
        write_csv(path, panel)
        return panel

    def _refresh_term_structure_panel(self, date: str, snapshot: pd.DataFrame, pressure_panel: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config.get("term_structure_sidecar", {})
        rows = []
        atm_cfg = {
            "exclude_exchanges": cfg.get("exclude_exchanges", list(EXCLUDED_EXCHANGES)),
            "atm_iv_dte_min": cfg.get("atm_iv_dte_min", 7),
            "atm_iv_dte_max": cfg.get("atm_iv_dte_max", 90),
            "atm_iv_moneyness_min": cfg.get("atm_iv_moneyness_min", 0.95),
            "atm_iv_moneyness_max": cfg.get("atm_iv_moneyness_max", 1.05),
            "iv_min": cfg.get("iv_min", 0.01),
            "iv_max": cfg.get("iv_max", 2.5),
        }
        work = snapshot[
            snapshot["exchange"].isin(set(atm_cfg["exclude_exchanges"])).eq(False)
            & pd.to_numeric(snapshot["dte"], errors="coerce").between(float(atm_cfg["atm_iv_dte_min"]), float(atm_cfg["atm_iv_dte_max"]))
            & pd.to_numeric(snapshot["moneyness"], errors="coerce").between(float(atm_cfg["atm_iv_moneyness_min"]), float(atm_cfg["atm_iv_moneyness_max"]))
            & pd.to_numeric(snapshot["entry_price"], errors="coerce").gt(0)
        ].copy()
        work = ensure_signal_iv(work, price_col="entry_price", iv_min=float(atm_cfg["iv_min"]), iv_max=float(atm_cfg["iv_max"]))
        term_iv_col = signal_iv_col(work)
        work = work[pd.to_numeric(work[term_iv_col], errors="coerce").gt(float(atm_cfg["iv_min"])) & pd.to_numeric(work[term_iv_col], errors="coerce").lt(float(atm_cfg["iv_max"]))].copy()
        expiry_iv_columns = ["exchange", "product", "target_expiry", "atm_iv", "dte", "spot_close"]
        expiry_iv = (
            work.groupby(["exchange", "product", "target_expiry"], as_index=False)
            .agg(atm_iv=(term_iv_col, "median"), dte=("dte", "median"), spot_close=("spot_close", "median"))
            if not work.empty
            else pd.DataFrame(columns=expiry_iv_columns)
        )
        for (exchange, product), group in expiry_iv.groupby(["exchange", "product"], sort=False):
            ordered = group.sort_values(["dte", "target_expiry"], kind="mergesort")
            if len(ordered) < 2:
                continue
            near = ordered.iloc[0]
            nxt = ordered.iloc[1]
            original = snapshot[snapshot["exchange"].eq(exchange) & snapshot["product"].eq(product)]
            t_side = ""
            if not original.empty:
                expiry = _front_expiry_if_eligible(original, float(cfg.get("min_dte", 10)), None)
                if expiry:
                    candidates = original[
                        original["target_expiry"].eq(expiry)
                        & _otm_mask(original)
                    ].copy()
                    candidates = ensure_signal_iv(candidates, price_col="entry_price")
                    candidates = candidates[
                        _valid_option_mask(
                            candidates,
                            min_oi=float(cfg.get("min_oi", 1000)),
                            min_volume=float(cfg.get("min_volume", 1)),
                            max_abs_delta=float(cfg.get("max_abs_delta", 0.03)),
                        )
                    ].copy()
                    side_iv_col = signal_iv_col(candidates)
                    side_iv = candidates.groupby("option_type")[side_iv_col].median().to_dict() if not candidates.empty else {}
                    put_iv = _safe_float(side_iv.get("P"))
                    call_iv = _safe_float(side_iv.get("C"))
                    if np.isfinite(put_iv) and (not np.isfinite(call_iv) or put_iv > call_iv):
                        t_side = "P"
                    elif np.isfinite(call_iv):
                        t_side = "C"
            rows.append(
                {
                    "trade_date": date,
                    "exchange": exchange,
                    "product": product,
                    "near_expiry": near["target_expiry"],
                    "next_expiry": nxt["target_expiry"],
                    "near_atm_iv": near["atm_iv"],
                    "next_atm_iv": nxt["atm_iv"],
                    "term_spread": near["atm_iv"] - nxt["atm_iv"],
                    "spot_close": near["spot_close"],
                    "t_side": t_side,
                }
            )
        path = self.data_dir / "term_structure_sidecar" / "term_structure_panel.csv"
        panel = _merge_panel(path, pd.DataFrame(rows), ["trade_date", "exchange", "product"])
        if not panel.empty:
            panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
            pieces = []
            for _, g in panel.groupby(["exchange", "product"], sort=False):
                pct, prior = _expanding_percentile_including_current(g["term_spread"])
                h = g.copy()
                h["iv_percentile"] = pct
                h["iv_prior_days"] = prior
                h["trend_20d"] = pd.to_numeric(h["spot_close"], errors="coerce") / pd.to_numeric(h["spot_close"], errors="coerce").shift(20) - 1.0
                pieces.append(h)
            panel = pd.concat(pieces, ignore_index=True, sort=False)
            panel = _add_group_lags(panel, ["exchange", "product"], ["term_spread", "near_atm_iv", "next_atm_iv", "iv_percentile", "iv_prior_days", "trend_20d", "t_side"], 3)
        write_csv(path, panel)
        return panel

    def _build_all_intents(
        self,
        date: str,
        snapshot: pd.DataFrame,
        panels: dict[str, pd.DataFrame],
        existing_schedule: pd.DataFrame,
        columns: list[str],
        nav: float,
        current_margin_cash: float,
        *,
        diagnostics: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        context = {
            "date": date,
            "snapshot": snapshot,
            "existing_schedule": existing_schedule,
            "columns": columns,
            "nav": nav,
            "current_margin_cash": current_margin_cash,
            "same_day_overlay_opened": set(),
            "overlay_week_layer_side_counts": {},
        }
        main_rows = self._build_main_rows(context, panels)
        rows.extend(main_rows)
        overlay1 = self._build_overlay1_rows(context, panels)
        rows.extend(overlay1)
        overlay2 = self._build_overlay2_rows(context, panels)
        rows.extend(overlay2)
        overlay3 = self._build_overlay3_rows(context, panels)
        rows.extend(overlay3)
        diagnostics["builder_row_counts"] = {
            "main": len(main_rows),
            "overlay1": len(overlay1),
            "overlay2": len(overlay2),
            "overlay3": len(overlay3),
        }
        return rows

    def _overlay_product_expiry_key(self, row: dict[str, Any] | pd.Series) -> tuple[str, str, str] | None:
        exchange = str(row.get("exchange", "") or "").upper()
        product = str(row.get("product", row.get("product_label", "")) or "").upper()
        expiry = str(row.get("target_expiry", row.get("expiry_date", "")) or "")[:10]
        if not exchange or not product or not expiry:
            return None
        return exchange, product, expiry

    def _overlay_already_opened(
        self,
        context: dict[str, Any],
        row: dict[str, Any],
        opened: set[tuple[str, str, str]],
    ) -> bool:
        controls = self.config.get("sidecar_risk_controls", {})
        if not bool(controls.get("one_open_per_product_expiry", True)):
            return False
        key = self._overlay_product_expiry_key(row)
        if key is None:
            return False
        same_day = context.setdefault("same_day_overlay_opened", set())
        return key in opened or key in same_day

    def _sidecar_weekly_cap(self, cfg: dict[str, Any]) -> int:
        controls = self.config.get("sidecar_risk_controls", {})
        return int(cfg.get("weekly_exchange_side_cap", controls.get("weekly_exchange_side_cap", 0)) or 0)

    def _overlay_weekly_cap_reached(
        self,
        context: dict[str, Any],
        cfg: dict[str, Any],
        row: dict[str, Any],
    ) -> bool:
        cap = self._sidecar_weekly_cap(cfg)
        if cap <= 0:
            return False
        layer = str(row.get("strategy_layer", "") or "")
        exchange = str(row.get("exchange", "") or "").upper()
        side = str(row.get("sell_side", row.get("option_type", "")) or "").upper()[:1]
        if not exchange or side not in {"P", "C"}:
            return False
        by_layer = context.setdefault("overlay_week_layer_side_counts", {})
        if layer not in by_layer:
            by_layer[layer] = self._weekly_layer_side_counts(context["existing_schedule"], context["date"], layer)
        return int(by_layer[layer].get((exchange, side), 0)) >= cap

    def _remember_overlay_row(self, context: dict[str, Any], cfg: dict[str, Any], row: dict[str, Any]) -> None:
        key = self._overlay_product_expiry_key(row)
        if key is not None:
            context.setdefault("same_day_overlay_opened", set()).add(key)
        cap = self._sidecar_weekly_cap(cfg)
        if cap <= 0:
            return
        layer = str(row.get("strategy_layer", "") or "")
        exchange = str(row.get("exchange", "") or "").upper()
        side = str(row.get("sell_side", row.get("option_type", "")) or "").upper()[:1]
        if not exchange or side not in {"P", "C"}:
            return
        by_layer = context.setdefault("overlay_week_layer_side_counts", {})
        if layer not in by_layer:
            by_layer[layer] = self._weekly_layer_side_counts(context["existing_schedule"], context["date"], layer)
        by_layer[layer][(exchange, side)] = int(by_layer[layer].get((exchange, side), 0)) + 1

    def _opened_product_expiry(
        self,
        existing_schedule: pd.DataFrame,
        date: str,
        *,
        layers: set[str] | None = None,
        entry_reasons: set[str] | None = None,
    ) -> set[tuple[str, str, str]]:
        if existing_schedule.empty or "target_expiry" not in existing_schedule.columns:
            return set()
        work_dates = pd.to_datetime(existing_schedule.get("entry_date"), errors="coerce")
        work = existing_schedule[work_dates.lt(pd.Timestamp(date))].copy()
        if layers is not None and "strategy_layer" in work.columns:
            work = work[work["strategy_layer"].fillna("").astype(str).isin(layers)]
        if entry_reasons is not None and "entry_reason" in work.columns:
            work = work[work["entry_reason"].fillna("").astype(str).isin(entry_reasons)]
        keys = set()
        for row in work.itertuples(index=False):
            exchange = str(getattr(row, "exchange", "") or "").upper()
            product = str(getattr(row, "product", "") or "").upper()
            expiry = str(getattr(row, "target_expiry", "") or "")[:10]
            if exchange and product and expiry:
                keys.add((exchange, product, expiry))
        return keys

    def _weekly_layer_side_counts(self, existing_schedule: pd.DataFrame, date: str, layer: str) -> dict[tuple[str, str], int]:
        if existing_schedule.empty:
            return {}
        ts = pd.Timestamp(date)
        iso = ts.isocalendar()
        work = existing_schedule.copy()
        work_dates = pd.to_datetime(work.get("entry_date"), errors="coerce")
        same_week = work_dates.apply(
            lambda x: bool(
                pd.notna(x)
                and x < ts
                and x.isocalendar().year == iso.year
                and x.isocalendar().week == iso.week
            )
        )
        work = work[same_week & work.get("strategy_layer", pd.Series("", index=work.index)).fillna("").astype(str).eq(layer)]
        counts: dict[tuple[str, str], int] = {}
        for _, row in work.iterrows():
            key = (str(row.get("exchange", "")).upper(), str(row.get("sell_side", "")).upper())
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _build_main_rows(self, context: dict[str, Any], panels: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        date = context["date"]
        existing = context["existing_schedule"]
        columns = context["columns"]
        nav = context["nav"]
        current_margin_cash = context["current_margin_cash"]
        main_cfg = self.config.get("main_sleeve", {})
        margin_cap = float(self.config.get("margin_cap", main_cfg.get("portfolio_margin_cap_pct_nav", 0.7)))
        selected = panels.get("main_selected", pd.DataFrame())
        if selected.empty or "entry_date" not in selected.columns:
            return []
        selected = selected.copy()
        selected["entry_date"] = _normalize_date_series(selected["entry_date"])
        today = selected[selected["entry_date"].eq(date)].copy()
        opened = self._opened_product_expiry(existing, date, layers={""}, entry_reasons={"monthly"})
        rows = []
        for _, signal in today.iterrows():
            exchange = str(signal.get("exchange", "")).upper()
            product = str(signal.get("product", signal.get("product_label", ""))).upper()
            expiry = str(signal.get("target_expiry", signal.get("expiry_date", "")))[:10]
            side = str(signal.get("sell_side", signal.get("option_type", ""))).upper()
            if not side or not expiry or (exchange, product, expiry) in opened:
                continue
            target_pct = _safe_float(signal.get("target_premium_pct_override"), float(main_cfg.get("target_premium_pct_nav", 0.001)))
            row = self._row_from_contract(
                columns,
                signal,
                date=date,
                entry_reason="monthly",
                strategy_layer="",
                side_rule=str(main_cfg.get("contract_mode", "l4_diff02_delta04_l3eff015")),
                target_pct=target_pct,
                nav=nav,
                current_margin_cash=current_margin_cash,
                margin_cap=margin_cap,
            )
            row.update(
                {
                    "sell_side": side,
                    "put_iv_pressure": signal.get("put_iv_pressure"),
                    "call_iv_pressure": signal.get("call_iv_pressure"),
                    "selected_side_iv_pressure": signal.get("selected_side_iv_pressure"),
                    "other_side_iv_pressure": signal.get("other_side_iv_pressure"),
                    "side_iv_pressure_diff": signal.get("side_iv_pressure_diff"),
                    "trend_20d": np.nan,
                    "budget_group": _budget_group(product, side, self.config.get("broad_sector_groups", {})),
                    "portfolio_group_margin_trigger_pct_nav": main_cfg.get("portfolio_group_margin_trigger_pct_nav", 0.45),
                    "portfolio_group_new_trade_cap_after_trigger_pct_nav": main_cfg.get("portfolio_group_new_trade_cap_after_trigger_pct_nav", 0.00075),
                    "group_margin_trigger_margin_pct": signal.get("group_margin_trigger_margin_pct", 0.0),
                    "capped_by_group_margin_trigger": bool(signal.get("capped_by_group_margin_trigger", False)),
                    "reentry_count": 0,
                }
            )
            rows.append(row)
        return rows

    def _build_overlay1_rows(self, context: dict[str, Any], panels: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        cfg = self.config.get("iv_pullback_sidecar", {})
        if not cfg.get("enabled", True):
            return []
        date = context["date"]
        rows = []
        atm_today = panels["overlay_atm"][panels["overlay_atm"]["trade_date"].eq(date)].copy()
        if not atm_today.empty and "term" in panels and not panels["term"].empty:
            term_cols = ["trade_date", "exchange", "product", "trend_20d_lag1"]
            term_today = panels["term"][panels["term"].get("trade_date", pd.Series(dtype=object)).eq(date)].copy()
            if not term_today.empty and all(col in term_today.columns for col in term_cols):
                atm_today = atm_today.merge(
                    term_today[term_cols],
                    on=["trade_date", "exchange", "product"],
                    how="left",
                )
        if not atm_today.empty:
            atm_today["_overlay_sort_iv"] = pd.to_numeric(atm_today.get("iv_percentile_lag4"), errors="coerce")
            atm_today = atm_today.sort_values(["_overlay_sort_iv"], ascending=[False], kind="mergesort")
        opened = self._opened_product_expiry(
            context["existing_schedule"],
            date,
            layers={"", "overlay2_risk_reversal_same_sign", "overlay3_term_structure_cluster_cap2"},
            entry_reasons={"iv_extreme_overlay"},
        )
        for _, signal in atm_today.iterrows():
            if not self._overlay1_trigger(signal, cfg):
                continue
            row = self._build_overlay_contract_row(context, signal, cfg, layer="", side=None, forced_side_rule="higher_iv_pressure")
            if row is None:
                continue
            if self._overlay_already_opened(context, row, opened):
                continue
            if self._overlay_weekly_cap_reached(context, cfg, row):
                continue
            row.update(
                {
                    "entry_reason": "iv_extreme_overlay",
                    "strategy_layer": "",
                    "overlay_signal_rule": cfg.get("signal_rule", "t4_p95_pullback_3d"),
                    "overlay_trigger_trade_date": signal.get("trade_date"),
                    "overlay_atm_iv": signal.get("atm_iv"),
                    "overlay_iv_percentile": signal.get("iv_percentile"),
                    "overlay_iv_prior_days": signal.get("iv_prior_days"),
                    "overlay_atm_option_obs": signal.get("atm_option_obs"),
                    "overlay_atm_iv_lag1": signal.get("atm_iv_lag1"),
                    "overlay_atm_iv_lag2": signal.get("atm_iv_lag2"),
                    "overlay_atm_iv_lag3": signal.get("atm_iv_lag3"),
                    "overlay_atm_iv_lag4": signal.get("atm_iv_lag4"),
                    "overlay_iv_percentile_lag1": signal.get("iv_percentile_lag1"),
                    "overlay_iv_percentile_lag2": signal.get("iv_percentile_lag2"),
                    "overlay_iv_percentile_lag3": signal.get("iv_percentile_lag3"),
                    "overlay_iv_percentile_lag4": signal.get("iv_percentile_lag4"),
                    "overlay_trigger_iv_percentile": signal.get("iv_percentile_lag4"),
                    "overlay_tier_threshold": cfg.get("iv_percentile_threshold", 0.95),
                    "overlay_tier_max_abs_delta": cfg.get("max_abs_delta", 0.04),
                    "overlay_tier_target_premium_pct": cfg.get("target_premium_pct_nav", 0.00025),
                    "overlay_strategy": "iv_pullback_sidecar",
                    "overlay_signal_family": "iv_extreme_pullback",
                    "overlay_side_rule": "higher_iv_pressure",
                    "overlay_priority": 1,
                    "t1_trend_20d": signal.get("trend_20d_lag1"),
                }
            )
            self._remember_overlay_row(context, cfg, row)
            rows.append(row)
        return rows

    def _overlay1_trigger(self, signal: pd.Series, cfg: dict[str, Any]) -> bool:
        base_trigger = (
            _safe_float(signal.get("iv_prior_days_lag4")) >= float(cfg.get("min_iv_history_days", 252))
            and _safe_float(signal.get("iv_percentile_lag4")) >= float(cfg.get("iv_percentile_threshold", 0.95))
            and _safe_float(signal.get("atm_iv_lag3")) < _safe_float(signal.get("atm_iv_lag4"))
            and _safe_float(signal.get("atm_iv_lag2")) < _safe_float(signal.get("atm_iv_lag3"))
            and _safe_float(signal.get("atm_iv_lag1")) < _safe_float(signal.get("atm_iv_lag2"))
        )
        if not base_trigger:
            return False
        lag1_iv_max = _safe_float(cfg.get("lag1_iv_percentile_max"))
        if np.isfinite(lag1_iv_max) and not _safe_float(signal.get("iv_percentile_lag1")) < lag1_iv_max:
            return False
        lag1_trend_abs_max = _safe_float(cfg.get("lag1_trend20_abs_max"))
        lag1_trend = _safe_float(signal.get("trend_20d_lag1"))
        if np.isfinite(lag1_trend_abs_max) and np.isfinite(lag1_trend) and abs(lag1_trend) > lag1_trend_abs_max:
            return False
        return True

    def _build_overlay2_rows(self, context: dict[str, Any], panels: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        cfg = self.config.get("risk_reversal_sidecar", {})
        if not cfg.get("enabled", True):
            return []
        date = context["date"]
        rr_today = panels["rr"][panels["rr"]["trade_date"].eq(date)].copy()
        if not rr_today.empty:
            rr_today["_overlay_sort_iv"] = pd.to_numeric(rr_today.get("iv_percentile_lag3"), errors="coerce")
            rr_today = rr_today.sort_values(["_overlay_sort_iv"], ascending=[False], kind="mergesort")
        opened = self._opened_product_expiry(
            context["existing_schedule"],
            date,
            layers={"", "overlay2_risk_reversal_same_sign", "overlay3_term_structure_cluster_cap2"},
            entry_reasons={"iv_extreme_overlay"},
        )
        rows = []
        for _, signal in rr_today.iterrows():
            if not self._overlay2_trigger(signal, cfg):
                continue
            side = "C" if _safe_float(signal.get("risk_reversal_lag1")) > 0 else "P"
            row = self._build_overlay_contract_row(
                context,
                signal,
                cfg,
                layer="overlay2_risk_reversal_same_sign",
                side=side,
                forced_side_rule="risk_reversal_lag1_positive_sell_call_else_sell_put",
            )
            if row is None:
                continue
            if self._overlay_already_opened(context, row, opened):
                continue
            if self._overlay_weekly_cap_reached(context, cfg, row):
                continue
            row.update(
                {
                    "entry_reason": "iv_extreme_overlay",
                    "strategy_layer": "overlay2_risk_reversal_same_sign",
                    "overlay_strategy": "risk_reversal_sidecar",
                    "overlay_signal_family": "risk_reversal_repair",
                    "overlay_signal_rule": cfg.get("signal_rule", "t3_p95_rr_2d_repair_same_sign"),
                    "overlay_side_rule": cfg.get("sell_side_rule", "risk_reversal_lag1_positive_sell_call_else_sell_put"),
                    "overlay_priority": 2,
                    "forced_sell_side": side,
                    "risk_reversal_lag1": signal.get("risk_reversal_lag1"),
                    "risk_reversal_lag2": signal.get("risk_reversal_lag2"),
                    "risk_reversal_lag3": signal.get("risk_reversal_lag3"),
                    "abs_risk_reversal_lag1": signal.get("abs_risk_reversal_lag1"),
                    "abs_risk_reversal_lag2": signal.get("abs_risk_reversal_lag2"),
                    "abs_risk_reversal_lag3": signal.get("abs_risk_reversal_lag3"),
                    "put_iv_lag1": signal.get("put_iv_lag1"),
                    "call_iv_lag1": signal.get("call_iv_lag1"),
                    "put_iv_lag2": signal.get("put_iv_lag2"),
                    "call_iv_lag2": signal.get("call_iv_lag2"),
                    "put_iv_lag3": signal.get("put_iv_lag3"),
                    "call_iv_lag3": signal.get("call_iv_lag3"),
                    "overlay_trigger_iv_percentile": signal.get("iv_percentile_lag3"),
                }
            )
            self._remember_overlay_row(context, cfg, row)
            rows.append(row)
        return rows

    def _overlay2_trigger(self, signal: pd.Series, cfg: dict[str, Any]) -> bool:
        rr1 = _safe_float(signal.get("risk_reversal_lag1"))
        rr2 = _safe_float(signal.get("risk_reversal_lag2"))
        rr3 = _safe_float(signal.get("risk_reversal_lag3"))
        same_sign = np.isfinite(rr1) and np.isfinite(rr2) and np.isfinite(rr3) and np.sign(rr1) == np.sign(rr2) == np.sign(rr3) and rr1 != 0
        return (
            _safe_float(signal.get("iv_prior_days_lag3")) >= float(cfg.get("min_iv_history_days", 252))
            and _safe_float(signal.get("iv_percentile_lag3")) >= float(cfg.get("iv_percentile_threshold", 0.95))
            and same_sign
            and _safe_float(signal.get("abs_risk_reversal_lag2")) < _safe_float(signal.get("abs_risk_reversal_lag3"))
            and _safe_float(signal.get("abs_risk_reversal_lag1")) < _safe_float(signal.get("abs_risk_reversal_lag2"))
        )

    def _build_overlay3_rows(self, context: dict[str, Any], panels: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        cfg = self.config.get("term_structure_sidecar", {})
        if not cfg.get("enabled", True):
            return []
        date = context["date"]
        layer = "overlay3_term_structure_cluster_cap2"
        opened = self._opened_product_expiry(
            context["existing_schedule"],
            date,
            layers={"", "overlay2_risk_reversal_same_sign", "overlay3_term_structure_cluster_cap2"},
            entry_reasons={"iv_extreme_overlay"},
        )
        term_today = panels["term"][panels["term"]["trade_date"].eq(date)].copy()
        if not term_today.empty:
            term_today["_overlay_sort_iv"] = pd.to_numeric(term_today.get("iv_percentile_lag3"), errors="coerce")
            term_today = term_today.sort_values(["_overlay_sort_iv"], ascending=[False], kind="mergesort")
        rows = []
        for _, signal in term_today.iterrows():
            triggered, side, conflict, reason = self._overlay3_trigger(signal, cfg)
            if not triggered:
                continue
            row = self._build_overlay_contract_row(context, signal, cfg, layer=layer, side=side, forced_side_rule="term_structure_t1_high_iv_pressure")
            if row is None:
                continue
            if self._overlay_already_opened(context, row, opened):
                continue
            if self._overlay_weekly_cap_reached(context, cfg, row):
                continue
            row.update(
                {
                    "entry_reason": "iv_extreme_overlay",
                    "strategy_layer": layer,
                    "overlay_strategy": "term_structure_sidecar",
                    "overlay_signal_family": "term_structure_repair",
                    "overlay_signal_rule": cfg.get("signal_rule", "t3_p95_term_2d_repair_t1_no_trend_conflict_cluster_cap2"),
                    "overlay_side_rule": cfg.get("side_rule", "term_structure_t1_high_iv_pressure"),
                    "overlay_priority": 3,
                    "forced_sell_side": side,
                    "term_spread_lag1": signal.get("term_spread_lag1"),
                    "term_spread_lag2": signal.get("term_spread_lag2"),
                    "term_spread_lag3": signal.get("term_spread_lag3"),
                    "near_atm_iv_lag1": signal.get("near_atm_iv_lag1"),
                    "next_atm_iv_lag1": signal.get("next_atm_iv_lag1"),
                    "near_atm_iv_lag2": signal.get("near_atm_iv_lag2"),
                    "next_atm_iv_lag2": signal.get("next_atm_iv_lag2"),
                    "near_atm_iv_lag3": signal.get("near_atm_iv_lag3"),
                    "next_atm_iv_lag3": signal.get("next_atm_iv_lag3"),
                    "t1_trend_20d": signal.get("trend_20d_lag1"),
                    "t1_side": side,
                    "t1_trend_conflict": conflict,
                    "t1_side_skip_reason": reason,
                    "overlay_trigger_iv_percentile": signal.get("iv_percentile_lag3"),
                }
            )
            self._remember_overlay_row(context, cfg, row)
            rows.append(row)
        return rows

    def _overlay3_trigger(self, signal: pd.Series, cfg: dict[str, Any]) -> tuple[bool, str, bool, str]:
        side = str(signal.get("t_side_lag1", "") or "").upper()
        if side not in {"P", "C"}:
            return False, "", False, "missing_t1_side"
        trend = _safe_float(signal.get("trend_20d_lag1"))
        if side == "C" and np.isfinite(trend) and trend > 0:
            return False, side, True, "skip_call_when_t1_trend20_positive"
        if side == "P" and np.isfinite(trend) and trend < 0:
            return False, side, True, "skip_put_when_t1_trend20_negative"
        triggered = (
            _safe_float(signal.get("iv_prior_days_lag3")) >= float(cfg.get("min_iv_history_days", 252))
            and _safe_float(signal.get("iv_percentile_lag3")) >= float(cfg.get("iv_percentile_threshold", 0.95))
            and _safe_float(signal.get("term_spread_lag3")) > 0
            and _safe_float(signal.get("term_spread_lag2")) < _safe_float(signal.get("term_spread_lag3"))
            and _safe_float(signal.get("term_spread_lag1")) <= _safe_float(signal.get("term_spread_lag2"))
            and _safe_float(signal.get("term_spread_lag1")) > 0
        )
        return triggered, side, False, ""

    def _build_overlay_contract_row(
        self,
        context: dict[str, Any],
        signal: pd.Series,
        cfg: dict[str, Any],
        *,
        layer: str,
        side: str | None,
        forced_side_rule: str,
    ) -> dict[str, Any] | None:
        snapshot = context["snapshot"]
        exchange = str(signal.get("exchange", "")).upper()
        product = str(signal.get("product", "")).upper()
        group = snapshot[snapshot["exchange"].eq(exchange) & snapshot["product"].eq(product)].copy()
        if group.empty:
            return None
        expiry = _front_expiry_if_eligible(group, float(cfg.get("min_dte", 10)), None)
        if not expiry:
            return None
        selected_side = side
        put_iv = call_iv = np.nan
        if not selected_side:
            candidates = group[
                group["target_expiry"].eq(expiry)
                & _otm_mask(group)
            ].copy()
            candidates = ensure_signal_iv(candidates, price_col="entry_price")
            candidates = candidates[
                _valid_option_mask(
                    candidates,
                    min_oi=float(cfg.get("min_oi", 1000)),
                    min_volume=float(cfg.get("min_volume", 1)),
                    max_abs_delta=float(cfg.get("max_abs_delta", 0.04)),
                )
            ].copy()
            iv_col = signal_iv_col(candidates)
            side_iv = candidates.groupby("option_type")[iv_col].median().to_dict() if not candidates.empty else {}
            put_iv = _safe_float(side_iv.get("P"))
            call_iv = _safe_float(side_iv.get("C"))
            if np.isfinite(put_iv) and (not np.isfinite(call_iv) or put_iv > call_iv):
                selected_side = "P"
            elif np.isfinite(call_iv):
                selected_side = "C"
            else:
                return None
        contract = _select_contract(
            group,
            selected_side,
            expiry,
            max_abs_delta=float(cfg.get("max_abs_delta", 0.04)),
            min_oi=float(cfg.get("min_oi", 1000)),
            min_volume=float(cfg.get("min_volume", 1)),
        )
        if contract is None:
            return None
        row = self._row_from_contract(
            context["columns"],
            contract,
            date=context["date"],
            entry_reason="iv_extreme_overlay",
            strategy_layer=layer,
            side_rule=forced_side_rule,
            target_pct=float(cfg.get("target_premium_pct_nav", 0.00025)),
            nav=context["nav"],
            current_margin_cash=context["current_margin_cash"],
            margin_cap=float(self.config.get("margin_cap", 0.7)),
        )
        row.update(
            {
                "sell_side": selected_side,
                "put_iv_pressure": put_iv,
                "call_iv_pressure": call_iv,
                "selected_side_iv_pressure": put_iv if selected_side == "P" else call_iv,
                "other_side_iv_pressure": call_iv if selected_side == "P" else put_iv,
                "side_iv_pressure_diff": abs(put_iv - call_iv) if np.isfinite(put_iv) and np.isfinite(call_iv) else np.nan,
                "budget_group": _budget_group(product, selected_side, self.config.get("broad_sector_groups", {})),
            }
        )
        return row

    def _row_from_contract(
        self,
        columns: list[str],
        contract: pd.Series,
        *,
        date: str,
        entry_reason: str,
        strategy_layer: str,
        side_rule: str,
        target_pct: float,
        nav: float,
        current_margin_cash: float,
        margin_cap: float,
    ) -> dict[str, Any]:
        row = _base_schedule_row(columns)
        one_lot_margin = _estimate_margin(contract, self.config)
        product = str(contract.get("product", contract.get("product_label", ""))).upper()
        entry_price = contract.get("entry_price", contract.get("close"))
        target_expiry = contract.get("target_expiry")
        if pd.isna(target_expiry) or not str(target_expiry).strip():
            target_expiry = contract.get("expiry_date")
        target_expiry = _normalize_date_series(pd.Series([target_expiry])).iloc[0]
        row.update(
            {
                "entry_date": date,
                "product": product,
                "exchange": str(contract.get("exchange", "")).upper(),
                "contract_code": contract.get("contract_code"),
                "option_type": contract.get("option_type"),
                "target_expiry": target_expiry,
                "dte": contract.get("dte"),
                "delta": contract.get("delta"),
                "close_oi": contract.get("close_oi"),
                "volume": contract.get("volume"),
                "entry_price": entry_price,
                "strike": contract.get("strike"),
                "spot_close": contract.get("spot_close"),
                "sell_side": contract.get("sell_side", contract.get("option_type")),
                "side_rule": side_rule,
                "entry_reason": entry_reason,
                "strategy_layer": strategy_layer,
                "multiplier": contract.get("multiplier"),
                "one_lot_margin_cash": one_lot_margin,
                "capped_by_group_premium": False,
                "capped_by_corr_premium": False,
                "capped_by_group_margin_trigger": False,
            }
        )
        return _size_row(
            row,
            nav=nav,
            current_margin_cash=current_margin_cash,
            margin_cap=margin_cap,
            target_premium_pct=target_pct,
        )
