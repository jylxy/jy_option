from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.data_loader import load_trading_dates, load_underlying_daily_flow_range
from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG
from s1_paper_trading_prepare.src.pit_signal_appender import (
    DEFAULT_FLOW_LOOKBACK,
    DEFAULT_LOWJUMP_LOOKBACK,
    DEFAULT_LOWJUMP_MIN_HISTORY,
    DEFAULT_MAIN_MAX_DTE,
    DEFAULT_MAIN_MIN_DTE,
    EXCLUDED_EXCHANGES,
    SIDE_FLOW_PANEL_COLUMNS,
    PitSignalAppender,
    _add_group_lags,
    _expanding_percentile_including_current,
    _front_expiry_if_eligible,
    _nearest_expiry,
    _otm_mask,
    _safe_float,
    _snapshot_path,
    _valid_option_mask,
    sanitize_main_selected_for_production,
    strip_forbidden_columns_for_production,
)
from s1_paper_trading_prepare.src.reverse_lowjump_main import build_current_main_intents
from s1_paper_trading_prepare.src.signal_feature_utils import ensure_signal_iv, signal_iv_col


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def load_dates(start: str, end: str, config_path: str | Path) -> list[str]:
    try:
        dates = load_trading_dates(start, end)
    except Exception:
        dates = []
    if dates:
        return dates
    root = Path(DEFAULT_DATA_DIR if config_path is None else DEFAULT_DATA_DIR)
    del root
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start=start, end=end, freq="D")]


def load_snapshot(appender: PitSignalAppender, date: str) -> pd.DataFrame:
    path = _snapshot_path(appender.data_dir, date)
    if not path.exists():
        return pd.DataFrame()
    try:
        return appender._load_snapshot(date)
    except Exception as exc:
        print(f"[skip snapshot] {date} {exc}", flush=True)
        return pd.DataFrame()


def compute_lowjump_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort").reset_index(drop=True)
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
    return panel


def compute_overlay_atm_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
    pieces = []
    for _, group in panel.groupby(["exchange", "product"], sort=False):
        pct, prior = _expanding_percentile_including_current(group["atm_iv"])
        out = group.copy()
        out["iv_percentile"] = pct
        out["iv_prior_days"] = prior
        pieces.append(out)
    panel = pd.concat(pieces, ignore_index=True, sort=False)
    return _add_group_lags(panel, ["exchange", "product"], ["atm_iv", "iv_percentile", "iv_prior_days"], 4)


def build_side_flow_base_rows(date: str, snapshot: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    main_cfg = (config or {}).get("main_sleeve", {})
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
    if not rows:
        return pd.DataFrame(columns=SIDE_FLOW_PANEL_COLUMNS)
    return pd.DataFrame(rows)


def attach_futures_flow(side_rows: pd.DataFrame, futures: pd.DataFrame) -> pd.DataFrame:
    if side_rows.empty:
        return pd.DataFrame(columns=SIDE_FLOW_PANEL_COLUMNS)
    side_rows = side_rows.copy()
    side_rows["trade_date"] = pd.to_datetime(side_rows["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    side_rows["product"] = side_rows["product"].astype(str).str.upper()
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
        side_rows = side_rows.merge(futures, on=["trade_date", "product"], how="left")
    else:
        for column in ("fut_volume", "fut_open_interest", "fut_close", "fut_settlement"):
            side_rows[column] = np.nan
        side_rows["futures_flow_source_table"] = ""
    for column in SIDE_FLOW_PANEL_COLUMNS:
        if column not in side_rows.columns:
            side_rows[column] = np.nan if column.startswith(("fut_", "option_")) else ""
    return side_rows


def compute_side_flow_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    panel = panel.sort_values(["exchange", "product", "sell_side", "trade_date"], kind="mergesort").reset_index(drop=True)
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
    panel = panel.merge(
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
    return panel


def build_pressure_rows(appender: PitSignalAppender, date: str, snapshot: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    main_cfg = appender.config.get("main_sleeve", {})
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
    return pd.DataFrame(rows)


def build_rr_rows(appender: PitSignalAppender, date: str, snapshot: pd.DataFrame, atm_today: pd.DataFrame) -> pd.DataFrame:
    cfg = appender.config.get("risk_reversal_sidecar", {})
    rows: list[dict[str, Any]] = []
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
        atm_row = atm_today[atm_today["exchange"].eq(exchange) & atm_today["product"].eq(product)]
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
    return pd.DataFrame(rows)


def build_term_rows(appender: PitSignalAppender, date: str, snapshot: pd.DataFrame, pressure_today: pd.DataFrame) -> pd.DataFrame:
    cfg = appender.config.get("term_structure_sidecar", {})
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
    if work.empty:
        return pd.DataFrame()
    expiry_iv = (
        work.groupby(["exchange", "product", "target_expiry"], as_index=False)
        .agg(atm_iv=(term_iv_col, "median"), dte=("dte", "median"), spot_close=("spot_close", "median"))
    )
    rows: list[dict[str, Any]] = []
    for (exchange, product), group in expiry_iv.groupby(["exchange", "product"], sort=False):
        ordered = group.sort_values(["dte", "target_expiry"], kind="mergesort")
        if len(ordered) < 2:
            continue
        near = ordered.iloc[0]
        nxt = ordered.iloc[1]
        original = snapshot[(snapshot["exchange"].eq(exchange)) & (snapshot["product"].eq(product))]
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
    return pd.DataFrame(rows)


def compute_rr_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
    pieces = []
    for _, group in panel.groupby(["exchange", "product"], sort=False):
        out = group.copy()
        pct, prior = _expanding_percentile_including_current(out["abs_risk_reversal"])
        out["iv_percentile"] = pct
        out["iv_prior_days"] = prior
        pieces.append(out)
    panel = pd.concat(pieces, ignore_index=True, sort=False)
    return _add_group_lags(panel, ["exchange", "product"], ["risk_reversal", "abs_risk_reversal", "put_iv", "call_iv", "iv_percentile", "iv_prior_days"], 3)


def compute_term_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    panel = panel.sort_values(["exchange", "product", "trade_date"], kind="mergesort")
    pieces = []
    for _, group in panel.groupby(["exchange", "product"], sort=False):
        pct, prior = _expanding_percentile_including_current(group["term_spread"])
        out = group.copy()
        out["iv_percentile"] = pct
        out["iv_prior_days"] = prior
        out["trend_20d"] = pd.to_numeric(out["spot_close"], errors="coerce") / pd.to_numeric(out["spot_close"], errors="coerce").shift(20) - 1.0
        pieces.append(out)
    panel = pd.concat(pieces, ignore_index=True, sort=False)
    return _add_group_lags(panel, ["exchange", "product"], ["term_spread", "near_atm_iv", "next_atm_iv", "iv_percentile", "iv_prior_days", "trend_20d", "t_side"], 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild all PIT factor panels from daily raw Toolkit snapshots in one pass.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--signals-start-date", default=None, help="Start date for generated main/overlay signal candidates; raw panels still warm up from --start-date.")
    parser.add_argument("--signals-end-date", default=None, help="End date for generated signal candidates; defaults to --end-date.")
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--products", default=None)
    parser.add_argument("--tag", default="pit_panel_rebuild")
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    products = parse_products(args.products)
    appender = PitSignalAppender(config_path=args.config, data_dir=args.data_dir, output_dir=args.output_dir)
    dates = load_dates(str(args.start_date)[:10], str(args.end_date)[:10], args.config)

    snapshots: dict[str, pd.DataFrame] = {}
    iv_rows: list[pd.DataFrame] = []
    overlay_rows: list[pd.DataFrame] = []
    flow_base_rows: list[pd.DataFrame] = []
    pressure_rows: list[pd.DataFrame] = []
    flow_products: set[str] = set()

    for idx, date in enumerate(dates, start=1):
        snapshot = load_snapshot(appender, date)
        if snapshot.empty:
            continue
        snapshots[date] = snapshot
        iv_rows.append(appender._build_atm_rows(date, snapshot, config=appender.config.get("iv_pullback_sidecar", {})))
        overlay_rows.append(appender._build_atm_rows(date, snapshot, config=appender.config.get("iv_pullback_sidecar", {})))
        side_base = build_side_flow_base_rows(date, snapshot, appender.config)
        flow_base_rows.append(side_base)
        if not side_base.empty:
            flow_products.update(str(p).upper() for p in side_base["product"].dropna().astype(str) if str(p).strip())
        pressure_rows.append(build_pressure_rows(appender, date, snapshot))
        if idx == 1 or idx == len(dates) or idx % max(1, int(args.progress_every or 1)) == 0:
            print(f"[raw panels {idx}/{len(dates)}] {date}", flush=True)

    iv_panel = compute_lowjump_panel(pd.concat(iv_rows, ignore_index=True, sort=False) if iv_rows else pd.DataFrame())
    overlay_panel = compute_overlay_atm_panel(pd.concat(overlay_rows, ignore_index=True, sort=False) if overlay_rows else pd.DataFrame())
    flow_base = pd.concat(flow_base_rows, ignore_index=True, sort=False) if flow_base_rows else pd.DataFrame(columns=SIDE_FLOW_PANEL_COLUMNS)
    if flow_base.empty:
        futures_flow = pd.DataFrame()
    else:
        futures_products = products or tuple(sorted(flow_products))
        print(f"[futures flow] products={len(futures_products or ())} start={args.start_date} end={args.end_date}", flush=True)
        futures_flow = load_underlying_daily_flow_range(args.start_date, args.end_date, appender.config_path, products=futures_products)
        print(f"[futures flow] rows={len(futures_flow)}", flush=True)
    flow_panel = compute_side_flow_panel(attach_futures_flow(flow_base, futures_flow))
    pressure_panel = pd.concat(pressure_rows, ignore_index=True, sort=False) if pressure_rows else pd.DataFrame()

    option_data = pd.concat(snapshots.values(), ignore_index=True, sort=False) if snapshots else pd.DataFrame()
    main_cfg = appender.config.get("main_sleeve", {})
    print("[main sleeve] building product-month opportunities and selected intents", flush=True)
    signal_start = str(args.signals_start_date or args.start_date)[:10]
    signal_end = str(args.signals_end_date or args.end_date)[:10]
    main_opps, main_selected, main_adjust_skips, dense_scores = build_current_main_intents(
        option_data,
        flow_panel,
        start_date=signal_start,
        end_date=signal_end,
        rule=str(main_cfg.get("opportunity_rule", "rule_l1_hsafe_addon025")),
        entry_window_calendar_days=int(main_cfg.get("entry_window_calendar_days", 7)),
        min_history_days=int(main_cfg.get("min_history_days", DEFAULT_LOWJUMP_MIN_HISTORY)),
        max_abs_delta=float(main_cfg.get("max_abs_delta", 0.08)),
        min_oi=float(main_cfg.get("min_oi", 1000)),
        l3_weak_pressure_threshold=float(main_cfg.get("l3_weak_pressure_threshold", 0.02)),
        l4_weak_pressure_threshold=float(main_cfg.get("l4_weak_pressure_threshold", 0.02)),
        l4_weak_delta_cap=float(main_cfg.get("l4_weak_delta_cap", 0.04)),
    )
    print(
        f"[main sleeve] opportunities={len(main_opps)} selected_intents={len(main_selected)} adjust_skips={len(main_adjust_skips)}",
        flush=True,
    )

    overlay_by_date = {str(date): group.copy() for date, group in overlay_panel.groupby("trade_date", sort=False)} if not overlay_panel.empty else {}
    pressure_by_date = {str(date): group.copy() for date, group in pressure_panel.groupby("trade_date", sort=False)} if not pressure_panel.empty else {}
    rr_rows: list[pd.DataFrame] = []
    term_rows: list[pd.DataFrame] = []
    for idx, (date, snapshot) in enumerate(snapshots.items(), start=1):
        rr_rows.append(build_rr_rows(appender, date, snapshot, overlay_by_date.get(date, pd.DataFrame())))
        term_rows.append(build_term_rows(appender, date, snapshot, pressure_by_date.get(date, pd.DataFrame())))
        if idx == 1 or idx == len(snapshots) or idx % max(1, int(args.progress_every or 1)) == 0:
            print(f"[derived panels {idx}/{len(snapshots)}] {date}", flush=True)

    rr_panel = compute_rr_panel(pd.concat(rr_rows, ignore_index=True, sort=False) if rr_rows else pd.DataFrame())
    term_panel = compute_term_panel(pd.concat(term_rows, ignore_index=True, sort=False) if term_rows else pd.DataFrame())

    paths = appender._panel_paths()
    write_csv(paths["iv_daily"], iv_panel)
    write_csv(paths["flow"], flow_panel)
    write_csv(paths["pressure"], pressure_panel)
    write_csv(paths["main_opportunities"], strip_forbidden_columns_for_production(main_opps))
    write_csv(paths["main_selected"], sanitize_main_selected_for_production(main_selected))
    write_csv(paths["overlay_atm"], overlay_panel)
    write_csv(paths["rr"], rr_panel)
    write_csv(paths["term"], term_panel)
    write_csv(Path(args.output_dir) / "audit" / f"{args.tag}_main_l4_adjust_skips.csv", main_adjust_skips)
    write_csv(Path(args.output_dir) / "audit" / f"{args.tag}_main_dense_scores.csv", dense_scores)

    summary = {
        "tag": args.tag,
        "start_date": str(args.start_date)[:10],
        "end_date": str(args.end_date)[:10],
        "signals_start_date": signal_start,
        "signals_end_date": signal_end,
        "dates_requested": len(dates),
        "snapshots_loaded": len(snapshots),
        "panel_rows": {
            "iv_daily": int(len(iv_panel)),
            "flow": int(len(flow_panel)),
            "pressure": int(len(pressure_panel)),
            "main_opportunities": int(len(main_opps)),
            "main_selected": int(len(main_selected)),
            "main_adjust_skips": int(len(main_adjust_skips)),
            "overlay_atm": int(len(overlay_panel)),
            "rr": int(len(rr_panel)),
            "term": int(len(term_panel)),
        },
        "panel_paths": {name: str(path) for name, path in paths.items()},
    }
    audit_path = Path(args.output_dir) / "audit" / f"{args.tag}.json"
    write_json(audit_path, summary)
    print("REBUILD_PIT_PANELS", summary, f"audit={audit_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
