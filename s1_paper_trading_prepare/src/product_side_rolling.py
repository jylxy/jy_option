"""S1-only rolling product-side panel maintenance.

This module maintains the live paper-trading replacement for the old research
product-side panel.  It uses stored daily option snapshots only, updates by
date partition, and shifts outcome history before computing signal-day buckets.
"""

from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .diagnostics import write_csv, write_json


ROLLING_PANEL_FILE = "rolling_product_side_panel.csv"
ROLLING_OBSERVATION_FILE = "rolling_product_side_observations.csv"
ROLLING_CONTRACT_OBSERVATION_FILE = "rolling_contract_shadow_observations.csv"
CONTRACT_SHADOW_DIR = "contract_shadow"
CONTRACT_FIELDS_PREFIX = "contract_shadow_fields"
ROLLING_ADMISSION_PREFIX = "rolling_l1_admission"
ROLLING_MANIFEST_PREFIX = "rolling_product_side_update"
CATEGORY_FIELDS = ["sector", "portfolio_bucket", "corr_group"]
FULL_SHADOW_ROLLING_WINDOW = 252
FULL_SHADOW_MIN_OBS = 60
HAR_HORIZON = 5
HAR_TRAIN_WINDOW = 500
HAR_MIN_TRAIN = 80
GARCH_ALPHA = 0.08
GARCH_BETA = 0.90
GARCH_LONG_WINDOW = 252
SNAPSHOT_CACHE_MAX_DATES = 256
MEAN_FEATURES = [
    "v3_contract_vrp_pct",
    "v3_contract_vrp_core",
    "v3_vrp_quality_score",
    "v3_term_not_inverted",
    "v3_term_slope_front_second",
    "v3_b6_premium_to_stress_rank",
    "v3_b6_premium_to_iv10_rank",
    "v3_b6_theta_vega_rank",
    "v3_b6_theta_gamma_rank",
    "v3_disaster_score",
    "v3_trend_breakout_score",
    "v3_rv5_rv20",
    "v3_side_iv_pct",
    "v3_side_iv_change_3d",
    "v3_side_skew_change_3d",
    "v3_front_atm_iv",
    "v3_second_atm_iv",
    "v3_base_b6_score",
    "v3_b6_plus_v3_score",
    "v3_delta_ladder_score",
    "underlying_trend_z_20d",
    "underlying_rv_ratio_5_20",
    "underlying_rv_accel_5_20",
    "underlying_gap_share_20d",
    "underlying_jump_share_20d",
    "underlying_har_pred_rv_5d",
    "underlying_garch_pred_rv_5d",
    "underlying_har_garch_avg_rv_5d",
]
MEAN_LABELS = [
    "v3_retention_5d",
    "v3_retention_10d",
    "v3_stop_touch_5d",
    "v3_stop_touch_10d",
    "v3_max_adverse_price_ratio_10d",
    "v3_retention_to_expiry_clipped",
    "v3_expire_otm_flag",
    "v3_product_stop_cluster_with_portfolio",
]
DELTA_BUCKETS = [
    ("delta_002_004_rows", 0.02, 0.04),
    ("delta_004_006_rows", 0.04, 0.06),
    ("delta_006_008_rows", 0.06, 0.08),
    ("delta_008_010_rows", 0.08, 0.10),
]
LABEL_COLUMNS = [
    "label_v3_stop_touch_5d",
    "label_v3_retention_10d",
    "label_v3_stop_touch_10d",
    "label_v3_retention_to_expiry_clipped",
    "label_v3_max_adverse_price_ratio_10d",
    "label_v3_product_stop_cluster_with_portfolio",
]
L1_PANEL_REQUIRED_COLUMNS = [
    "date",
    "product",
    "side",
    "historical_retention_score",
    "historical_retention_score_rank_date",
    "historical_retention_score_bucket5_date",
    "tail_cluster_safety_score",
    "tail_cluster_safety_score_rank_date",
    "tail_cluster_safety_score_bucket5_date",
    "product_side_score",
    "product_side_score_rank_date",
    "avg_v3_b6_premium_to_stress_rank",
]


@dataclass(frozen=True)
class RollingProductSideUpdateResult:
    signal_date: str
    observation_rows: int
    panel_rows: int
    admission_rows: int
    matured_observation_rows: int
    contract_observation_rows: int
    contract_fields_rows: int
    contract_observation_path: Path | None
    contract_fields_path: Path | None
    observation_path: Path | None
    panel_path: Path | None
    admission_path: Path | None
    manifest_path: Path | None


def _date_tag(signal_date: str) -> str:
    return str(signal_date)[:10].replace("-", "")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _csv_has_rows(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        _header = handle.readline()
        return bool(handle.readline())


def _upsert_by_key(existing: pd.DataFrame, new_rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if existing.empty:
        return new_rows.copy()
    if new_rows.empty:
        return existing.copy()
    old = existing.copy()
    marker = new_rows[keys].drop_duplicates()
    old = old.merge(marker.assign(_replace=1), how="left", on=keys)
    old = old[old["_replace"].isna()].drop(columns=["_replace"])
    return pd.concat([old, new_rows], ignore_index=True, sort=False)


def _safe_numeric(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(index=frame.index, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce")


def _safe_text(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series("", index=frame.index, dtype=str)
    return frame[col].fillna("").astype(str)


def _rank_by_date(frame: pd.DataFrame, col: str, higher_good: bool = True) -> pd.Series:
    values = _safe_numeric(frame, col)
    if not higher_good:
        values = -values
    return values.groupby(frame["date"]).rank(pct=True, method="average")


def _mean_existing(frame: pd.DataFrame, cols: list[str]) -> pd.Series:
    existing = [col for col in cols if col in frame.columns]
    if not existing:
        return pd.Series(np.nan, index=frame.index)
    return frame[existing].mean(axis=1, skipna=True)


def _cross_section_rank(values: pd.Series, by: pd.Series, higher_good: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if not higher_good:
        numeric = -numeric
    return numeric.groupby(by).rank(pct=True, method="average")


def _to_float(value: Any, default: float = np.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def _rolling_sum_forward(values: pd.Series, horizon: int) -> pd.Series:
    out = pd.Series(0.0, index=values.index)
    for step in range(1, int(horizon) + 1):
        out = out + values.shift(-step)
    return out


def _add_har_forecast(group: pd.DataFrame, horizon: int, train_window: int, min_train: int) -> pd.Series:
    """Rolling HAR-RV forecast copied from the S1 full-shadow research chain."""
    g = group.sort_values("date").copy()
    sq = pd.to_numeric(g["underlying_ret_1d"], errors="coerce").pow(2)
    x = pd.DataFrame(
        {
            "const": 1.0,
            "rv1": sq,
            "rv5": sq.rolling(5, min_periods=3).mean(),
            "rv20": sq.rolling(20, min_periods=10).mean(),
        },
        index=g.index,
    )
    y = _rolling_sum_forward(sq, horizon) / float(horizon)
    pred = pd.Series(np.nan, index=g.index, dtype=float)
    for loc, idx in enumerate(g.index):
        train_end = loc - horizon
        if train_end < min_train:
            continue
        train_start = max(0, train_end - train_window)
        train_idx = g.index[train_start:train_end]
        train = pd.concat([x.loc[train_idx], y.loc[train_idx].rename("target")], axis=1)
        train = train.replace([np.inf, -np.inf], np.nan).dropna()
        if len(train) < min_train:
            continue
        xi = x.loc[idx]
        if xi.isna().any():
            continue
        x_train = train[["const", "rv1", "rv5", "rv20"]].to_numpy(float)
        y_train = train["target"].to_numpy(float)
        ridge = np.eye(x_train.shape[1]) * 1e-8
        ridge[0, 0] = 0.0
        try:
            beta = np.linalg.solve(x_train.T @ x_train + ridge, x_train.T @ y_train)
        except np.linalg.LinAlgError:
            beta = np.linalg.pinv(x_train.T @ x_train + ridge) @ x_train.T @ y_train
        pred.loc[idx] = max(float(xi.to_numpy(float) @ beta), 0.0)
    return np.sqrt(pred * 252.0)


def _add_fixed_garch_forecast(
    group: pd.DataFrame,
    alpha: float,
    beta: float,
    horizon: int,
    long_window: int,
) -> pd.Series:
    """Fast fixed-parameter GARCH forecast copied from the S1 research chain."""
    g = group.sort_values("date").copy()
    sq = pd.to_numeric(g["underlying_ret_1d"], errors="coerce").pow(2)
    long_var = sq.shift(1).rolling(long_window, min_periods=max(20, long_window // 5)).mean()
    unconditional = sq.expanding(min_periods=20).mean().shift(1)
    long_var = long_var.fillna(unconditional).fillna(sq.mean())
    persistence = min(max(alpha + beta, 0.0), 0.995)
    omega_weight = max(1.0 - persistence, 1e-6)
    sigma2_prev = np.nan
    pred = pd.Series(np.nan, index=g.index, dtype=float)
    for idx in g.index:
        lv = _to_float(long_var.loc[idx], np.nan)
        shock = _to_float(sq.loc[idx], np.nan)
        if not np.isfinite(lv) or lv <= 0:
            lv = _to_float(sq.mean(), np.nan)
        if not np.isfinite(lv) or lv <= 0:
            continue
        if not np.isfinite(sigma2_prev) or sigma2_prev <= 0:
            sigma2_prev = lv
        if np.isfinite(shock):
            sigma2_next = omega_weight * lv + alpha * shock + beta * sigma2_prev
        else:
            sigma2_next = omega_weight * lv + beta * sigma2_prev
        horizon_vars = [lv + (persistence ** step) * (sigma2_next - lv) for step in range(horizon)]
        pred.loc[idx] = np.sqrt(max(float(np.mean(horizon_vars)), 0.0) * 252.0)
        sigma2_prev = sigma2_next
    return pred


def _rolling_history_stats(
    frame: pd.DataFrame,
    *,
    group_cols: list[str],
    value_col: str,
    prefix: str,
    window_dates: int = FULL_SHADOW_ROLLING_WINDOW,
    min_obs: int = FULL_SHADOW_MIN_OBS,
) -> pd.DataFrame:
    """Add research-style no-lookahead rolling percentile and z-score."""
    if value_col not in frame.columns or frame.empty:
        return frame
    out = frame.copy()
    pct_col = f"{prefix}_pct"
    z_col = f"{prefix}_z"
    out[pct_col] = np.nan
    out[z_col] = np.nan
    work = out.sort_values(group_cols + ["date"], kind="mergesort")
    group_arg = group_cols[0] if len(group_cols) == 1 else group_cols

    for _, group in work.groupby(group_arg, sort=False, dropna=False):
        hist_sorted: list[float] = []
        hist_dates: deque[tuple[pd.Timestamp, list[float]]] = deque()
        hist_sum = 0.0
        hist_sumsq = 0.0
        hist_count = 0
        for date_value, day in group.groupby("date", sort=True, dropna=False):
            values = pd.to_numeric(day[value_col], errors="coerce")
            if hist_count >= min_obs:
                mean = hist_sum / hist_count
                var = max(hist_sumsq / hist_count - mean * mean, 0.0)
                std = math.sqrt(var)
                pct_values = []
                z_values = []
                for value in values:
                    x = _to_float(value, np.nan)
                    if not np.isfinite(x):
                        pct_values.append(np.nan)
                        z_values.append(np.nan)
                        continue
                    pct_values.append(100.0 * bisect.bisect_right(hist_sorted, x) / hist_count)
                    z_values.append((x - mean) / std if std > 0 else np.nan)
                out.loc[day.index, pct_col] = pct_values
                out.loc[day.index, z_col] = z_values

            clean = [float(x) for x in values.dropna().to_numpy(float) if np.isfinite(x)]
            if clean:
                for x in clean:
                    bisect.insort(hist_sorted, x)
                hist_dates.append((pd.Timestamp(date_value), clean))
                hist_sum += float(np.sum(clean))
                hist_sumsq += float(np.sum(np.square(clean)))
                hist_count += len(clean)

            while len(hist_dates) > window_dates:
                _, old_values = hist_dates.popleft()
                for x in old_values:
                    pos = bisect.bisect_left(hist_sorted, x)
                    if pos < len(hist_sorted) and hist_sorted[pos] == x:
                        hist_sorted.pop(pos)
                hist_sum -= float(np.sum(old_values))
                hist_sumsq -= float(np.sum(np.square(old_values)))
                hist_count -= len(old_values)
    return out


def build_daily_contract_shadow_observations(
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    signal_date: str,
) -> pd.DataFrame:
    """Normalize one Toolkit daily snapshot into the S1 contract shadow schema."""
    if l0_universe.empty:
        return pd.DataFrame()
    out = l0_universe.copy()
    date = str(signal_date)[:10]
    out["date"] = date
    out["signal_date"] = date
    out["product"] = _safe_text(out, "product").str.upper().str.strip()
    out["option_type"] = _safe_text(out, "option_type").str.upper().str[:1]
    out = out[out["option_type"].isin(["C", "P"])].copy()
    if out.empty:
        return out

    if "l0_basic_trade_eligible" in out.columns:
        eligible = out["l0_basic_trade_eligible"].astype(bool)
    else:
        price = _safe_numeric(out, "option_close")
        volume = _safe_numeric(out, "volume").fillna(0)
        oi = _safe_numeric(out, "open_interest").fillna(0)
        dte = _safe_numeric(out, "dte")
        abs_delta_for_gate = _safe_numeric(out, "abs_delta")
        if abs_delta_for_gate.isna().all() and "delta" in out.columns:
            abs_delta_for_gate = _safe_numeric(out, "delta").abs()
        eligible = (
            price.ge(float(config.get("s1_min_option_price", 0.0) or 0.0))
            & volume.ge(float(config.get("s1_min_volume", 0.0) or 0.0))
            & oi.ge(float(config.get("s1_min_oi", 0.0) or 0.0))
            & dte.between(float(config.get("dte_min", 0.0) or 0.0), float(config.get("dte_max", 9999.0) or 9999.0), inclusive="both")
            & abs_delta_for_gate.between(
                float(config.get("s1_sell_delta_floor", 0.0) or 0.0),
                float(config.get("s1_sell_delta_cap", 1.0) or 1.0),
                inclusive="both",
            )
        )
    out["trade_eligible_flag"] = eligible.astype(float)
    out["contract_code"] = _safe_text(out, "option_code")
    out["code"] = out["contract_code"]
    out["expiry"] = _safe_text(out, "expiry_date")
    out["expiry_date"] = _safe_text(out, "expiry_date").str[:10]
    out["entry_price"] = _safe_numeric(out, "option_close")
    out["vwap"] = out["entry_price"]
    out["entry_volume"] = _safe_numeric(out, "volume").fillna(0.0)
    out["entry_open_interest"] = _safe_numeric(out, "open_interest").fillna(0.0)
    out["volume"] = out["entry_volume"]
    out["open_interest"] = out["entry_open_interest"]
    out["multiplier"] = _safe_numeric(out, "multiplier").fillna(1.0)
    out["spot"] = _safe_numeric(out, "spot_close")
    out["spot_close"] = out["spot"]
    out["spot_open"] = _safe_numeric(out, "spot_open").where(_safe_numeric(out, "spot_open").gt(0), out["spot"])
    out["spot_high"] = _safe_numeric(out, "spot_high").where(_safe_numeric(out, "spot_high").gt(0), out["spot"])
    out["spot_low"] = _safe_numeric(out, "spot_low").where(_safe_numeric(out, "spot_low").gt(0), out["spot"])
    out["contract_iv"] = _safe_numeric(out, "implied_vol")
    out["abs_delta"] = _safe_numeric(out, "abs_delta")
    if out["abs_delta"].isna().all() and "delta" in out.columns:
        out["abs_delta"] = _safe_numeric(out, "delta").abs()
    out["entry_premium_cash_1lot"] = out["entry_price"] * out["multiplier"]
    out["v3_capacity_lots_10pct"] = np.floor(out["entry_volume"] * float(config.get("s1_entry_volume_limit_ratio", 0.10) or 0.10))
    out["l0_basic_trade_eligible"] = eligible
    keep_front = [
        "date",
        "signal_date",
        "contract_code",
        "code",
        "product",
        "option_type",
        "strike",
        "expiry",
        "expiry_date",
        "dte",
        "underlying_code",
        "entry_price",
        "vwap",
        "option_high",
        "volume",
        "entry_volume",
        "open_interest",
        "entry_open_interest",
        "entry_premium_cash_1lot",
        "multiplier",
        "spot",
        "spot_close",
        "spot_open",
        "spot_high",
        "spot_low",
        "moneyness",
        "contract_iv",
        "implied_vol",
        "delta",
        "abs_delta",
        "gamma",
        "vega",
        "theta",
        "trade_eligible_flag",
        "l0_basic_trade_eligible",
    ]
    keep = [col for col in keep_front if col in out.columns]
    rest = [col for col in out.columns if col not in keep]
    return out[keep + rest].copy()


def _add_underlying_features_from_contract_history(contract: pd.DataFrame) -> pd.DataFrame:
    if contract.empty:
        return contract
    out = contract.copy()
    unique = (
        out[["date", "underlying_code", "product", "spot", "spot_open", "spot_high", "spot_low"]]
        .dropna(subset=["date", "underlying_code", "spot"])
        .copy()
    )
    if unique.empty:
        return out
    for col in ["spot", "spot_open", "spot_high", "spot_low"]:
        unique[col] = pd.to_numeric(unique[col], errors="coerce")
    ohlc = (
        unique.groupby(["date", "underlying_code"], as_index=False)
        .agg(
            product=("product", "first"),
            close=("spot", "median"),
            open=("spot_open", "median"),
            high=("spot_high", "median"),
            low=("spot_low", "median"),
        )
        .dropna(subset=["close"])
    )
    ohlc = ohlc[(ohlc["close"] > 0)].copy()
    if ohlc.empty:
        return out
    ohlc["open"] = ohlc["open"].where(ohlc["open"].gt(0), ohlc["close"])
    ohlc["high"] = ohlc["high"].where(ohlc["high"].gt(0), ohlc["close"])
    ohlc["low"] = ohlc["low"].where(ohlc["low"].gt(0), ohlc["close"])
    ohlc["date_dt"] = pd.to_datetime(ohlc["date"], errors="coerce")
    ohlc = ohlc.sort_values(["underlying_code", "date_dt"], kind="mergesort")
    g = ohlc.groupby("underlying_code", sort=False)
    prev_close = g["close"].shift(1)
    ohlc["underlying_ret_1d"] = np.log(ohlc["close"] / prev_close)
    ohlc["underlying_abs_ret_1d"] = ohlc["underlying_ret_1d"].abs()
    ohlc["underlying_gap_abs"] = np.log(ohlc["open"] / prev_close).abs()
    tr_abs = pd.concat(
        [
            (ohlc["high"] - ohlc["low"]).abs(),
            (ohlc["high"] - prev_close).abs(),
            (ohlc["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    ohlc["underlying_true_range_pct"] = tr_abs / prev_close.replace(0, np.nan)
    sq = ohlc["underlying_ret_1d"].pow(2)
    for window in (3, 5, 10, 20, 60):
        ohlc[f"underlying_rv_{window}d"] = np.sqrt(
            sq.groupby(ohlc["underlying_code"], sort=False).transform(
                lambda series, w=window: series.rolling(w, min_periods=max(3, w // 2)).mean()
            )
            * 252.0
        )
    ohlc["underlying_rv_ratio_5_20"] = ohlc["underlying_rv_5d"] / ohlc["underlying_rv_20d"].replace(0, np.nan)
    ohlc["underlying_rv_ratio_20_60"] = ohlc["underlying_rv_20d"] / ohlc["underlying_rv_60d"].replace(0, np.nan)
    ohlc["underlying_rv_accel_5_20"] = ohlc["underlying_rv_ratio_5_20"] - 1.0
    ohlc["underlying_atr_5d"] = g["underlying_true_range_pct"].transform(lambda series: series.rolling(5, min_periods=3).mean())
    ohlc["underlying_atr_20d"] = g["underlying_true_range_pct"].transform(lambda series: series.rolling(20, min_periods=10).mean())
    for window in (5, 20, 60):
        ohlc[f"underlying_trend_ret_{window}d"] = g["close"].transform(lambda series, w=window: np.log(series / series.shift(w)))
    ohlc["underlying_trend_z_20d"] = ohlc["underlying_trend_ret_20d"] / (
        ohlc["underlying_rv_20d"] * np.sqrt(20.0 / 252.0)
    )
    denom = g["underlying_true_range_pct"].transform(lambda series: series.rolling(20, min_periods=10).mean())
    ohlc["underlying_gap_share_20d"] = (
        g["underlying_gap_abs"].transform(lambda series: series.rolling(20, min_periods=10).mean())
        / denom.replace(0, np.nan)
    )

    def jump_share(series: pd.Series) -> pd.Series:
        sq_ret = series.pow(2)
        threshold = series.shift(1).rolling(60, min_periods=20).std() * 2.5
        jump_sq = sq_ret.where(series.abs() > threshold, 0.0)
        return jump_sq.rolling(20, min_periods=10).sum() / sq_ret.rolling(20, min_periods=10).sum()

    ohlc["underlying_jump_share_20d"] = g["underlying_ret_1d"].transform(jump_share)
    har_parts = []
    garch_parts = []
    for _, part in g:
        har_parts.append(_add_har_forecast(part, HAR_HORIZON, HAR_TRAIN_WINDOW, HAR_MIN_TRAIN))
        garch_parts.append(_add_fixed_garch_forecast(part, GARCH_ALPHA, GARCH_BETA, HAR_HORIZON, GARCH_LONG_WINDOW))
    ohlc["underlying_har_pred_rv_5d"] = pd.concat(har_parts).sort_index() if har_parts else np.nan
    ohlc["underlying_garch_pred_rv_5d"] = pd.concat(garch_parts).sort_index() if garch_parts else np.nan
    ohlc["underlying_har_garch_avg_rv_5d"] = ohlc[
        ["underlying_har_pred_rv_5d", "underlying_garch_pred_rv_5d"]
    ].mean(axis=1)
    feature_cols = [
        "date",
        "underlying_code",
        "underlying_ret_1d",
        "underlying_rv_5d",
        "underlying_rv_10d",
        "underlying_rv_20d",
        "underlying_rv_60d",
        "underlying_rv_ratio_5_20",
        "underlying_rv_accel_5_20",
        "underlying_atr_20d",
        "underlying_trend_ret_5d",
        "underlying_trend_ret_20d",
        "underlying_trend_z_20d",
        "underlying_gap_share_20d",
        "underlying_jump_share_20d",
        "underlying_har_pred_rv_5d",
        "underlying_garch_pred_rv_5d",
        "underlying_har_garch_avg_rv_5d",
    ]
    out = out.drop(columns=[col for col in feature_cols if col not in ("date", "underlying_code") and col in out.columns], errors="ignore")
    return out.merge(ohlc[feature_cols], on=["date", "underlying_code"], how="left")


def _add_side_surface_features(contract: pd.DataFrame) -> pd.DataFrame:
    if contract.empty:
        return contract
    out = contract.copy()
    df = out.copy()
    abs_delta = pd.to_numeric(df.get("abs_delta"), errors="coerce")
    side_stats = (
        df[abs_delta.between(0.03, 0.15, inclusive="both")]
        .groupby(["date", "product", "option_type"], as_index=False)
        .agg(
            side_contract_count=("contract_code", "count"),
            side_wing_iv=("contract_iv", "median"),
            side_wing_premium=("entry_price", "sum"),
            side_wing_volume=("volume", "sum"),
            side_wing_oi=("open_interest", "sum"),
            side_avg_abs_delta=("abs_delta", "mean"),
        )
    )
    atm_stats = (
        df[abs_delta.between(0.35, 0.65, inclusive="both")]
        .groupby(["date", "product"], as_index=False)
        .agg(atm_iv=("contract_iv", "median"))
    )
    if side_stats.empty:
        out["atm_iv"] = np.nan
        out["side_skew_richness"] = np.nan
        return out
    wide = side_stats.pivot_table(
        index=["date", "product"],
        columns="option_type",
        values=["side_wing_iv", "side_wing_premium", "side_wing_volume", "side_wing_oi"],
        aggfunc="first",
    )
    wide.columns = [f"{left}_{right}" for left, right in wide.columns]
    wide = wide.reset_index().merge(atm_stats, on=["date", "product"], how="left")
    wide["put_skew_richness"] = wide.get("side_wing_iv_P", np.nan) - wide["atm_iv"]
    wide["call_skew_richness"] = wide.get("side_wing_iv_C", np.nan) - wide["atm_iv"]
    wide["risk_reversal_iv"] = wide.get("side_wing_iv_C", np.nan) - wide.get("side_wing_iv_P", np.nan)
    wide["smile_curvature_iv"] = (
        (wide.get("side_wing_iv_C", np.nan) + wide.get("side_wing_iv_P", np.nan)) / 2.0 - wide["atm_iv"]
    )
    wide["pcr_volume"] = wide.get("side_wing_volume_P", np.nan) / pd.Series(wide.get("side_wing_volume_C", np.nan)).replace(0, np.nan)
    wide["pcr_oi"] = wide.get("side_wing_oi_P", np.nan) / pd.Series(wide.get("side_wing_oi_C", np.nan)).replace(0, np.nan)
    wide["pcr_premium"] = wide.get("side_wing_premium_P", np.nan) / pd.Series(wide.get("side_wing_premium_C", np.nan)).replace(0, np.nan)
    out = out.merge(wide, on=["date", "product"], how="left")
    out["side_skew_richness"] = np.where(out["option_type"].eq("P"), out["put_skew_richness"], out["call_skew_richness"])
    out["opposite_side_skew_richness"] = np.where(out["option_type"].eq("P"), out["call_skew_richness"], out["put_skew_richness"])
    return out


def _add_contract_derived_features(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    contract_iv = _safe_numeric(out, "contract_iv")
    atm_iv = _safe_numeric(out, "atm_iv")
    price = _safe_numeric(out, "entry_price")
    multiplier = _safe_numeric(out, "multiplier")
    spot = _safe_numeric(out, "spot")
    rv20 = _safe_numeric(out, "underlying_rv_20d")
    har5 = _safe_numeric(out, "underlying_har_pred_rv_5d")
    garch5 = _safe_numeric(out, "underlying_garch_pred_rv_5d")
    avg5 = _safe_numeric(out, "underlying_har_garch_avg_rv_5d")
    atr20 = _safe_numeric(out, "underlying_atr_20d")
    out["iv_over_rv20"] = contract_iv / rv20.replace(0, np.nan)
    out["iv_over_har5"] = contract_iv / har5.replace(0, np.nan)
    out["iv_over_garch5"] = contract_iv / garch5.replace(0, np.nan)
    out["iv_over_har_garch5"] = contract_iv / avg5.replace(0, np.nan)
    out["variance_carry_har5"] = contract_iv.pow(2) - har5.pow(2)
    out["variance_carry_garch5"] = contract_iv.pow(2) - garch5.pow(2)
    out["variance_carry_har_garch5"] = contract_iv.pow(2) - avg5.pow(2)
    premium_cash = price * multiplier
    out["premium_to_atr20_move"] = premium_cash / (spot * multiplier * atr20).replace(0, np.nan)
    out["premium_to_har5_move"] = premium_cash / (spot * multiplier * har5 * math.sqrt(5.0 / 252.0)).replace(0, np.nan)
    out["premium_to_garch5_move"] = premium_cash / (spot * multiplier * garch5 * math.sqrt(5.0 / 252.0)).replace(0, np.nan)
    out["v3_atm_vrp_core"] = atm_iv.pow(2) - avg5.pow(2)
    return out


def _add_basic_vrp_fields(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    contract_iv = _safe_numeric(out, "contract_iv")
    rv20 = _safe_numeric(out, "underlying_rv_20d")
    har5 = _safe_numeric(out, "underlying_har_pred_rv_5d")
    garch5 = _safe_numeric(out, "underlying_garch_pred_rv_5d")
    avg5 = _safe_numeric(out, "underlying_har_garch_avg_rv_5d")
    out["v3_contract_vrp_rv20"] = contract_iv.pow(2) - rv20.pow(2)
    out["v3_contract_vrp_har5"] = contract_iv.pow(2) - har5.pow(2)
    out["v3_contract_vrp_garch5"] = contract_iv.pow(2) - garch5.pow(2)
    out["v3_contract_vrp_har_garch5"] = contract_iv.pow(2) - avg5.pow(2)
    out["v3_contract_vrp_core"] = out["v3_contract_vrp_har_garch5"]
    out["v3_contract_vrp_core"] = out["v3_contract_vrp_core"].where(out["v3_contract_vrp_core"].notna(), out["v3_contract_vrp_har5"])
    out["v3_contract_vrp_core"] = out["v3_contract_vrp_core"].where(out["v3_contract_vrp_core"].notna(), out["v3_contract_vrp_garch5"])
    out["v3_contract_vrp_core"] = out["v3_contract_vrp_core"].where(out["v3_contract_vrp_core"].notna(), out["v3_contract_vrp_rv20"])
    out["v3_raw_vrp_positive"] = (pd.to_numeric(out["v3_contract_vrp_core"], errors="coerce") > 0).astype(float)
    side_iv = np.where(
        out["option_type"].eq("P"),
        pd.to_numeric(out.get("side_wing_iv_P", np.nan), errors="coerce"),
        pd.to_numeric(out.get("side_wing_iv_C", np.nan), errors="coerce"),
    )
    side_iv = pd.Series(side_iv, index=out.index)
    out["v3_side_iv"] = side_iv.where(side_iv.notna(), contract_iv)
    out["v3_side_vrp_core"] = pd.to_numeric(out["v3_side_iv"], errors="coerce").pow(2) - avg5.pow(2)
    return out


def _add_standardized_v3_fields(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    specs = [
        (["product", "option_type"], "v3_contract_vrp_core", "v3_contract_vrp_core_ps"),
        (["product", "option_type"], "v3_contract_vrp_rv20", "v3_contract_vrp_rv20_ps"),
        (["product", "option_type"], "v3_contract_vrp_har5", "v3_contract_vrp_har5_ps"),
        (["product", "option_type"], "v3_contract_vrp_garch5", "v3_contract_vrp_garch5_ps"),
        (["product", "option_type"], "v3_side_iv", "v3_side_iv_ps"),
        (["product"], "atm_iv", "v3_atm_iv_product"),
        (["product"], "v3_atm_vrp_core", "v3_atm_vrp_product"),
    ]
    for group_cols, value_col, prefix in specs:
        out = _rolling_history_stats(out, group_cols=group_cols, value_col=value_col, prefix=prefix)
    out["v3_contract_vrp_pct"] = out.get("v3_contract_vrp_core_ps_pct", np.nan)
    out["v3_contract_vrp_z"] = out.get("v3_contract_vrp_core_ps_z", np.nan)
    out["v3_side_iv_pct"] = out.get("v3_side_iv_ps_pct", np.nan)
    out["v3_atm_iv_pct"] = out.get("v3_atm_iv_product_pct", np.nan)
    out["contract_vrp_pct_product_side_252"] = out["v3_contract_vrp_pct"]
    out["contract_vrp_z_product_side_252"] = out["v3_contract_vrp_z"]
    out["side_iv_pct_product_side_252"] = out["v3_side_iv_pct"]
    out["atm_iv_pct_product_252"] = out["v3_atm_iv_pct"]
    for col in ["v3_contract_vrp_core", "v3_contract_vrp_pct", "v3_side_iv", "v3_side_iv_pct", "v3_atm_iv_pct"]:
        out[f"{col}_missing"] = out[col].isna().astype(float)
    return out


def _add_iv_vrp_buckets(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    iv_pct = _safe_numeric(out, "v3_side_iv_pct")
    vrp_pct = _safe_numeric(out, "v3_contract_vrp_pct")
    weight = pd.Series(np.nan, index=out.index, dtype=float)
    weight = weight.mask(iv_pct < 30, 0.30)
    weight = weight.mask(iv_pct.between(30, 50, inclusive="left"), 0.60)
    weight = weight.mask(iv_pct.between(50, 85, inclusive="both"), 1.00)
    weight = weight.mask(iv_pct.between(85, 95, inclusive="right"), 0.70)
    weight = weight.mask(iv_pct > 95, 0.30)
    out["v3_iv_premium_weight"] = weight
    out["v3_current_iv70_gate"] = iv_pct.ge(70).astype(float)
    out["v3_current_vrp_gate"] = _safe_numeric(out, "v3_contract_vrp_core").gt(0).astype(float)
    out["v3_current_iv70_vrp_gate"] = (
        out["v3_current_iv70_gate"].eq(1.0) & out["v3_current_vrp_gate"].eq(1.0)
    ).astype(float)
    return out


def _add_side_daily_dynamics(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    if "side_skew_richness" not in out.columns:
        out["side_skew_richness"] = np.nan
    side_daily = (
        out.groupby(["date", "product", "option_type"], as_index=False)
        .agg(
            v3_side_iv_daily=("v3_side_iv", "median"),
            v3_side_skew_daily=("side_skew_richness", "median"),
            v3_side_vrp_daily=("v3_side_vrp_core", "median"),
        )
        .sort_values(["product", "option_type", "date"], kind="mergesort")
    )
    g = side_daily.groupby(["product", "option_type"], sort=False)
    side_daily["v3_side_iv_change_3d"] = g["v3_side_iv_daily"].transform(lambda series: series - series.shift(3))
    side_daily["v3_side_iv_change_1d"] = g["v3_side_iv_daily"].transform(lambda series: series - series.shift(1))
    side_daily["v3_side_iv_change_5d"] = g["v3_side_iv_daily"].transform(lambda series: series - series.shift(5))
    side_daily["v3_side_skew_change_3d"] = g["v3_side_skew_daily"].transform(lambda series: series - series.shift(3))
    side_daily["v3_side_skew_change_5d"] = g["v3_side_skew_daily"].transform(lambda series: series - series.shift(5))
    side_daily["v3_side_vrp_change_3d"] = g["v3_side_vrp_daily"].transform(lambda series: series - series.shift(3))
    return out.merge(
        side_daily[
            [
                "date",
                "product",
                "option_type",
                "v3_side_iv_daily",
                "v3_side_skew_daily",
                "v3_side_vrp_daily",
                "v3_side_iv_change_1d",
                "v3_side_iv_change_3d",
                "v3_side_iv_change_5d",
                "v3_side_skew_change_3d",
                "v3_side_skew_change_5d",
                "v3_side_vrp_change_3d",
            ]
        ],
        on=["date", "product", "option_type"],
        how="left",
    )


def _add_term_structure_fields(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    if not {"date", "product", "expiry_date"}.issubset(out.columns):
        out["v3_front_atm_iv"] = np.nan
        out["v3_second_atm_iv"] = np.nan
        out["v3_term_slope_front_second"] = np.nan
        out["v3_term_inversion"] = np.nan
        out["v3_term_not_inverted"] = np.nan
        return out
    atm_slice = out[["date", "product", "expiry_date", "atm_iv"]].dropna().drop_duplicates() if "atm_iv" in out.columns else pd.DataFrame()
    contract_slice = out[["date", "product", "expiry_date", "contract_iv"]].dropna() if "contract_iv" in out.columns else pd.DataFrame()
    if atm_slice.empty and contract_slice.empty:
        out["v3_front_atm_iv"] = np.nan
        out["v3_second_atm_iv"] = np.nan
        out["v3_term_slope_front_second"] = np.nan
        out["v3_term_inversion"] = np.nan
        out["v3_term_not_inverted"] = np.nan
        return out
    if not atm_slice.empty:
        atm_expiry = atm_slice.groupby(["date", "product", "expiry_date"], as_index=False).agg(atm_expiry_iv=("atm_iv", "median"))
        atm_nunique = (
            atm_expiry.groupby(["date", "product"], as_index=False)["atm_expiry_iv"]
            .nunique()
            .rename(columns={"atm_expiry_iv": "atm_expiry_iv_nunique"})
        )
        atm_expiry = atm_expiry.merge(atm_nunique, on=["date", "product"], how="left")
    else:
        atm_expiry = pd.DataFrame()
    contract_expiry = (
        contract_slice.groupby(["date", "product", "expiry_date"], as_index=False).agg(contract_expiry_iv=("contract_iv", "median"))
        if not contract_slice.empty else pd.DataFrame()
    )
    if atm_expiry.empty:
        expiry_atm = contract_expiry.rename(columns={"contract_expiry_iv": "expiry_atm_iv"})
    elif contract_expiry.empty:
        expiry_atm = atm_expiry.rename(columns={"atm_expiry_iv": "expiry_atm_iv"})
    else:
        expiry_atm = atm_expiry.merge(contract_expiry, on=["date", "product", "expiry_date"], how="left")
        use_contract = expiry_atm["atm_expiry_iv_nunique"].fillna(0).le(1) & expiry_atm["contract_expiry_iv"].notna()
        expiry_atm["expiry_atm_iv"] = expiry_atm["atm_expiry_iv"].where(~use_contract, expiry_atm["contract_expiry_iv"])
    expiry_atm = expiry_atm[["date", "product", "expiry_date", "expiry_atm_iv"]].sort_values(
        ["date", "product", "expiry_date"], kind="mergesort"
    )
    expiry_atm["expiry_rank"] = expiry_atm.groupby(["date", "product"], sort=False).cumcount() + 1
    front = expiry_atm[expiry_atm["expiry_rank"].eq(1)][["date", "product", "expiry_atm_iv"]].rename(
        columns={"expiry_atm_iv": "v3_front_atm_iv"}
    )
    second = expiry_atm[expiry_atm["expiry_rank"].eq(2)][["date", "product", "expiry_atm_iv"]].rename(
        columns={"expiry_atm_iv": "v3_second_atm_iv"}
    )
    out = out.merge(front, on=["date", "product"], how="left").merge(second, on=["date", "product"], how="left")
    out["v3_term_slope_front_second"] = out["v3_front_atm_iv"] - out["v3_second_atm_iv"]
    out["v3_term_inversion"] = out["v3_term_slope_front_second"].gt(0).astype(float)
    out["v3_term_not_inverted"] = np.where(
        out["v3_term_slope_front_second"].notna(),
        out["v3_term_slope_front_second"].le(0).astype(float),
        np.nan,
    )
    return out


def _build_tail_corr_panel(contract: pd.DataFrame, window: int = FULL_SHADOW_ROLLING_WINDOW, min_obs: int = FULL_SHADOW_MIN_OBS) -> pd.DataFrame:
    if "underlying_ret_1d" not in contract.columns:
        return pd.DataFrame(columns=["date", "product", "v3_tail_corr_state"])
    ret = (
        contract[["date", "product", "underlying_ret_1d"]]
        .dropna()
        .drop_duplicates(["date", "product"])
        .pivot(index="date", columns="product", values="underlying_ret_1d")
        .sort_index()
    )
    if ret.empty:
        return pd.DataFrame(columns=["date", "product", "v3_tail_corr_state"])
    market = ret.mean(axis=1, skipna=True)
    rows = []
    for product in ret.columns:
        prod = ret[product]
        for i, date_value in enumerate(ret.index):
            start = max(0, i - window)
            hist_prod = prod.iloc[start:i]
            hist_mkt = market.iloc[start:i]
            valid = hist_prod.notna() & hist_mkt.notna()
            if valid.sum() < min_obs:
                value = np.nan
            else:
                m = hist_mkt[valid]
                p = hist_prod[valid]
                threshold = m.abs().quantile(0.90)
                tail = m.abs() >= threshold
                if tail.sum() >= max(10, min_obs // 5) and p[tail].std(ddof=0) > 0 and m[tail].std(ddof=0) > 0:
                    value = float(p[tail].corr(m[tail]))
                else:
                    value = np.nan
            rows.append({"date": date_value, "product": product, "v3_tail_corr_state": value})
    return pd.DataFrame(rows)


def _add_disaster_score(contract: pd.DataFrame) -> pd.DataFrame:
    out = _add_side_daily_dynamics(contract)
    out = _add_term_structure_fields(out)
    tail = _build_tail_corr_panel(out)
    if not tail.empty:
        out = out.merge(tail, on=["date", "product"], how="left")
    else:
        out["v3_tail_corr_state"] = np.nan
    rv_ratio = _safe_numeric(out, "underlying_rv_ratio_5_20")
    rv_accel_score = ((rv_ratio - 1.0) / 0.50).clip(lower=0.0, upper=1.0)
    rv3 = _safe_numeric(out, "underlying_rv_3d")
    rv10 = _safe_numeric(out, "underlying_rv_10d")
    out["v3_rv3_rv10"] = rv3 / rv10.replace(0, np.nan)
    out["v3_rv5_rv20"] = rv_ratio
    iv_change = _safe_numeric(out, "v3_side_iv_change_3d")
    iv_accel_score = (iv_change / 0.04).clip(lower=0.0, upper=1.0)
    skew_change = _safe_numeric(out, "v3_side_skew_change_3d")
    skew_score = (skew_change / 0.03).clip(lower=0.0, upper=1.0)
    trend_z = _safe_numeric(out, "underlying_trend_z_20d")
    adverse_trend = pd.Series(np.where(out["option_type"].eq("P"), -trend_z, trend_z), index=out.index, dtype=float)
    trend_score = ((adverse_trend - 0.80) / 1.20).clip(lower=0.0, upper=1.0)
    term_score = _safe_numeric(out, "v3_term_inversion").clip(lower=0.0, upper=1.0)
    jump_share = _safe_numeric(out, "underlying_jump_share_20d")
    gap_share = _safe_numeric(out, "underlying_gap_share_20d")
    jump_gap_score = pd.concat([jump_share / 0.35, gap_share / 0.60], axis=1).max(axis=1).clip(lower=0.0, upper=1.0)
    tail_corr_score = _safe_numeric(out, "v3_tail_corr_state").clip(lower=0.0, upper=1.0)
    out["v3_rv_acceleration_score"] = rv_accel_score
    out["v3_iv_acceleration_score"] = iv_accel_score
    out["v3_side_skew_steepening_score"] = skew_score
    out["v3_trend_breakout_score"] = trend_score
    out["v3_term_inversion_score"] = term_score
    out["v3_jump_gap_score"] = jump_gap_score
    out["v3_tail_corr_state_score"] = tail_corr_score
    out["v3_disaster_score"] = (
        20.0 * rv_accel_score.fillna(0.0)
        + 20.0 * iv_accel_score.fillna(0.0)
        + 15.0 * skew_score.fillna(0.0)
        + 15.0 * trend_score.fillna(0.0)
        + 10.0 * term_score.fillna(0.0)
        + 10.0 * jump_gap_score.fillna(0.0)
        + 10.0 * tail_corr_score.fillna(0.0)
    ).clip(lower=0.0, upper=100.0)
    vrp_pct = _safe_numeric(out, "v3_contract_vrp_pct")
    out["v3_high_vrp_risk_dulling_flag"] = (
        vrp_pct.ge(70) & rv_ratio.le(1.0) & _safe_numeric(out, "v3_side_iv_change_3d").le(0) & _safe_numeric(out, "v3_side_skew_change_3d").le(0)
    ).astype(float)
    out["v3_high_vrp_risk_fomenting_flag"] = (
        vrp_pct.ge(70)
        & (
            rv_ratio.gt(1.2)
            | _safe_numeric(out, "v3_side_iv_change_3d").gt(0)
            | _safe_numeric(out, "v3_side_skew_change_3d").gt(0)
            | _safe_numeric(out, "v3_trend_breakout_score").ge(0.5)
        )
    ).astype(float)
    return out


def _rank_by_group_100(frame: pd.DataFrame, group_cols: list[str], col: str, *, high_good: bool = True) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    values = pd.to_numeric(frame[col], errors="coerce")
    if not high_good:
        values = -values
    group_arg = [frame[c] for c in group_cols]
    return values.groupby(group_arg, sort=False).rank(pct=True, method="average") * 100.0


def _add_b6_proxy_scores(contract: pd.DataFrame) -> pd.DataFrame:
    out = contract.copy()
    theta = _safe_numeric(out, "theta").abs()
    vega = _safe_numeric(out, "vega").abs()
    gamma = _safe_numeric(out, "gamma").abs()
    spot = _safe_numeric(out, "spot")
    abs_delta = _safe_numeric(out, "abs_delta").abs()
    out["v3_theta_per_vega"] = theta / vega.replace(0, np.nan)
    out["v3_theta_per_gamma"] = theta / (gamma * spot.pow(2)).replace(0, np.nan)
    out["v3_delta_ladder_score"] = (1.0 - (abs_delta - 0.085).abs() / 0.04).clip(lower=0.0, upper=1.0) * 100.0
    group_cols = ["signal_date", "product", "option_type"]
    out["_r_premium_to_stress"] = _rank_by_group_100(out, group_cols, "premium_to_garch5_move")
    out["_r_premium_to_iv10"] = _rank_by_group_100(out, group_cols, "premium_to_har5_move")
    out["_r_theta_vega"] = _rank_by_group_100(out, group_cols, "v3_theta_per_vega")
    out["_r_theta_gamma"] = _rank_by_group_100(out, group_cols, "v3_theta_per_gamma")
    out["v3_b6_premium_to_stress_rank"] = out["_r_premium_to_stress"]
    out["v3_b6_premium_to_iv10_rank"] = out["_r_premium_to_iv10"]
    out["v3_b6_theta_vega_rank"] = out["_r_theta_vega"]
    out["v3_b6_theta_gamma_rank"] = out["_r_theta_gamma"]
    out["v3_base_b6_score"] = (
        0.22 * out["_r_premium_to_stress"].fillna(50.0)
        + 0.18 * out["_r_premium_to_iv10"].fillna(50.0)
        + 0.18 * out["_r_theta_vega"].fillna(50.0)
        + 0.13 * out["_r_theta_gamma"].fillna(50.0)
        + 0.12 * out["v3_delta_ladder_score"].fillna(50.0)
    ) / 0.83
    vrp_score = _safe_numeric(out, "v3_contract_vrp_pct")
    iv_weight = _safe_numeric(out, "v3_iv_premium_weight")
    disaster = _safe_numeric(out, "v3_disaster_score")
    out["v3_vrp_quality_score"] = (0.65 * vrp_score + 35.0 * iv_weight - 0.35 * disaster).clip(lower=0.0, upper=100.0)
    out["v3_disaster_multiplier"] = (1.0 - disaster / 100.0).clip(lower=0.20, upper=1.0)
    out["v3_b6_plus_v3_score"] = (
        0.70 * pd.to_numeric(out["v3_base_b6_score"], errors="coerce")
        + 0.30 * out["v3_vrp_quality_score"]
    ) * out["v3_disaster_multiplier"]
    out["v3_b6_plus_v3_rank_in_product_side_date"] = (
        out.groupby(group_cols, sort=False)["v3_b6_plus_v3_score"].rank(method="first", ascending=False)
    )
    for col, high_good in [
        ("v3_contract_vrp_pct", True),
        ("v3_vrp_quality_score", True),
        ("v3_base_b6_score", True),
        ("v3_b6_plus_v3_score", True),
        ("v3_disaster_score", False),
    ]:
        out[f"{col}_rank_in_product_side_date"] = _rank_by_group_100(out, ["signal_date", "product", "option_type"], col, high_good=high_good)
        out[f"{col}_rank_in_product_date"] = _rank_by_group_100(out, ["signal_date", "product"], col, high_good=high_good)
        out[f"{col}_rank_cross_section_date"] = _rank_by_group_100(out, ["signal_date"], col, high_good=high_good)
    return out.drop(columns=[c for c in ["_r_premium_to_stress", "_r_premium_to_iv10", "_r_theta_vega", "_r_theta_gamma"] if c in out.columns])


def _add_full_shadow_contract_fields(contract_observations: pd.DataFrame) -> pd.DataFrame:
    """Replay S1 full-shadow V3/B6/VRP/regime fields from stored Toolkit rows."""
    if contract_observations.empty:
        return contract_observations
    out = contract_observations.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out["signal_date"] = out["date"]
    out["product"] = out["product"].astype(str).str.upper().str.strip()
    out["option_type"] = out["option_type"].astype(str).str.upper().str[:1]
    out = out.sort_values(["product", "option_type", "date", "contract_code"], kind="mergesort").reset_index(drop=True)
    out = _add_underlying_features_from_contract_history(out)
    out = _add_side_surface_features(out)
    out = _add_contract_derived_features(out)
    out = _add_basic_vrp_fields(out)
    out = _add_standardized_v3_fields(out)
    out = _add_iv_vrp_buckets(out)
    out = _add_disaster_score(out)
    out = _add_b6_proxy_scores(out)
    out["entry_volume"] = _safe_numeric(out, "entry_volume").fillna(0.0)
    out["entry_open_interest"] = _safe_numeric(out, "entry_open_interest").fillna(0.0)
    out["entry_premium_cash_1lot"] = _safe_numeric(out, "entry_premium_cash_1lot")
    out["v3_capacity_lots_10pct"] = _safe_numeric(out, "v3_capacity_lots_10pct").fillna(0.0)
    out["abs_delta"] = _safe_numeric(out, "abs_delta")
    out["contract_iv"] = _safe_numeric(out, "contract_iv")
    out["implied_vol"] = _safe_numeric(out, "implied_vol")
    for col in MEAN_FEATURES:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["oi_ge1000_flag"] = _safe_numeric(out, "entry_open_interest").ge(1000).astype(float)
    out["oi_ge1000_premium_cash"] = out["entry_premium_cash_1lot"].where(out["oi_ge1000_flag"].eq(1.0), 0.0)
    out["capacity_premium_proxy"] = out["entry_premium_cash_1lot"].clip(lower=0.0) * out["v3_capacity_lots_10pct"].clip(lower=0.0)
    abs_delta = _safe_numeric(out, "abs_delta")
    for name, lo, hi in DELTA_BUCKETS:
        out[name] = ((abs_delta >= lo) & (abs_delta < hi)).astype(int)
    out["side"] = out["option_type"]
    return out


def _normalize_l0_for_research_aggregate(
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    signal_date: str,
) -> pd.DataFrame:
    """Map daily Toolkit snapshot rows to the research panel contract schema."""
    if l0_universe.empty:
        return pd.DataFrame()
    out = l0_universe.copy()
    date = str(signal_date)[:10]
    out["date"] = date
    out["product"] = _safe_text(out, "product").str.upper().str.strip()
    out["side"] = _safe_text(out, "option_type").str.upper().str[:1]
    out = out[out["side"].isin(["C", "P"])].copy()
    if out.empty:
        return out
    eligible = out.get("l0_basic_trade_eligible", True)
    if isinstance(eligible, pd.Series):
        eligible = eligible.astype(bool)
    else:
        eligible = pd.Series(bool(eligible), index=out.index)

    price = _safe_numeric(out, "option_close")
    high = _safe_numeric(out, "option_high")
    volume = _safe_numeric(out, "volume").fillna(0.0)
    oi = _safe_numeric(out, "open_interest").fillna(0.0)
    multiplier = _safe_numeric(out, "multiplier").fillna(1.0)
    abs_delta = _safe_numeric(out, "abs_delta")
    if abs_delta.isna().all() and "delta" in out.columns:
        abs_delta = _safe_numeric(out, "delta").abs()
    implied_vol = _safe_numeric(out, "implied_vol")
    spot_close = _safe_numeric(out, "spot_close")
    theta = _safe_numeric(out, "theta")
    vega = _safe_numeric(out, "vega")
    gamma = _safe_numeric(out, "gamma")

    volume_cap = float(config.get("s1_entry_volume_limit_ratio", config.get("volume_limit_pct", 0.10)) or 0.10)
    out["trade_eligible_flag"] = eligible.astype(float)
    out["entry_price"] = price
    out["entry_volume"] = volume
    out["entry_open_interest"] = oi
    out["entry_premium_cash_1lot"] = price * multiplier
    out["v3_capacity_lots_10pct"] = volume * volume_cap
    out["abs_delta"] = abs_delta
    out["contract_iv"] = implied_vol
    out["atm_iv"] = implied_vol
    out["spot_close"] = spot_close
    out["v3_contract_vrp_pct"] = implied_vol
    out["v3_contract_vrp_core"] = implied_vol
    out["v3_vrp_quality_score"] = implied_vol
    out["v3_side_iv_pct"] = implied_vol
    out["v3_theta_per_vega"] = (-theta) / vega.replace(0, np.nan)
    out["v3_theta_per_gamma"] = (-theta) / gamma.abs().replace(0, np.nan)
    out["v3_b6_theta_vega_rank"] = _cross_section_rank(out["v3_theta_per_vega"], out["date"])
    out["v3_b6_theta_gamma_rank"] = _cross_section_rank(out["v3_theta_per_gamma"], out["date"])
    out["v3_b6_premium_to_iv10_rank"] = _cross_section_rank(price / implied_vol.replace(0, np.nan), out["date"])

    stress_base = pd.concat(
        [
            high.sub(price).abs(),
            price.abs() * 0.10,
        ],
        axis=1,
    ).max(axis=1)
    out["stress_loss"] = stress_base * multiplier
    premium_to_stress = out["entry_premium_cash_1lot"] / out["stress_loss"].replace(0, np.nan)
    out["v3_b6_premium_to_stress_rank"] = _cross_section_rank(premium_to_stress, out["date"])

    if "moneyness" in out.columns:
        moneyness = _safe_numeric(out, "moneyness")
    else:
        moneyness = pd.Series(np.nan, index=out.index)
    out["_atm_distance"] = (moneyness - 1.0).abs()
    if "expiry_date" in out.columns and implied_vol.notna().any():
        atm = out.sort_values(["product", "expiry_date", "_atm_distance"]).groupby(
            ["product", "expiry_date"],
            dropna=False,
        ).head(1)
        expiry_iv = (
            atm.groupby(["product", "expiry_date"], dropna=False)["implied_vol"]
            .mean()
            .reset_index()
            .sort_values(["product", "expiry_date"])
        )
        expiry_iv["_expiry_rank"] = expiry_iv.groupby("product").cumcount()
        term = expiry_iv.pivot(index="product", columns="_expiry_rank", values="implied_vol")
        term = term.rename(columns={0: "_front_iv", 1: "_second_iv"}).reset_index()
        term["_term_slope"] = term.get("_front_iv", np.nan) / term.get("_second_iv", np.nan)
        term["_term_not_inverted"] = term.get("_front_iv", np.nan).le(term.get("_second_iv", np.nan)).astype(float)
        out = out.merge(
            term[["product", "_term_slope", "_term_not_inverted"]],
            on="product",
            how="left",
        )
        out["v3_term_slope_front_second"] = out["_term_slope"]
        out["v3_term_not_inverted"] = out["_term_not_inverted"]
    else:
        out["v3_term_slope_front_second"] = np.nan
        out["v3_term_not_inverted"] = np.nan

    out["oi_ge1000_flag"] = out["entry_open_interest"].ge(float(config.get("s1_min_oi", 1000) or 1000)).astype(float)
    out["oi_ge1000_premium_cash"] = out["entry_premium_cash_1lot"].where(out["oi_ge1000_flag"].eq(1.0), 0.0)
    out["capacity_premium_proxy"] = (
        out["entry_premium_cash_1lot"].clip(lower=0.0)
        * out["v3_capacity_lots_10pct"].clip(lower=0.0)
    )

    for name, lo, hi in DELTA_BUCKETS:
        out[name] = ((abs_delta >= lo) & (abs_delta < hi)).astype(int)
    return out


def _research_aggregate_panel(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    agg: dict[str, tuple[str, str]] = {
        "candidate_rows": ("product", "size"),
        "oi_ge1000_rows": ("oi_ge1000_flag", "sum"),
        "total_premium_pool_1lot": ("entry_premium_cash_1lot", "sum"),
        "oi_ge1000_premium_pool": ("oi_ge1000_premium_cash", "sum"),
        "total_open_interest": ("entry_open_interest", "sum"),
        "avg_open_interest": ("entry_open_interest", "mean"),
        "total_entry_volume": ("entry_volume", "sum"),
        "avg_entry_volume": ("entry_volume", "mean"),
        "capacity_premium_pool_proxy": ("capacity_premium_proxy", "sum"),
        "avg_capacity_lots_10pct": ("v3_capacity_lots_10pct", "mean"),
    }
    for name, _, _ in DELTA_BUCKETS:
        if name in frame.columns:
            agg[name] = (name, "sum")
    for col in MEAN_FEATURES:
        if col in frame.columns:
            agg[f"avg_{col}"] = (col, "mean")
    for col in MEAN_LABELS:
        if col in frame.columns:
            agg[f"label_{col}"] = (col, "mean")
    if "spot_close" in frame.columns:
        agg["avg_spot_close"] = ("spot_close", "mean")
    if "abs_delta" in frame.columns:
        agg["avg_abs_delta"] = ("abs_delta", "mean")
    if "implied_vol" in frame.columns:
        agg["avg_implied_vol"] = ("implied_vol", "mean")

    out = frame.groupby(keys, dropna=False).agg(**agg).reset_index()
    for cat in CATEGORY_FIELDS:
        if cat in frame.columns and cat not in keys:
            cats = frame.groupby(keys, dropna=False)[cat].agg(
                lambda series: series.dropna().iloc[0] if len(series.dropna()) else np.nan
            ).reset_index()
            out = out.merge(cats, on=keys, how="left")
    out["oi_ge1000_ratio"] = out["oi_ge1000_rows"] / out["candidate_rows"].replace(0, np.nan)
    return out


def _research_add_derived_factors(panel: pd.DataFrame, side_level: bool) -> pd.DataFrame:
    if panel.empty:
        return panel
    out = panel.copy()
    rows = pd.to_numeric(out.get("candidate_rows"), errors="coerce").replace(0, np.nan)
    oi_rows = pd.to_numeric(out.get("oi_ge1000_rows"), errors="coerce").replace(0, np.nan)
    total_oi = pd.to_numeric(out.get("total_open_interest"), errors="coerce").replace(0, np.nan)
    total_volume = pd.to_numeric(out.get("total_entry_volume"), errors="coerce")
    premium_pool = pd.to_numeric(out.get("oi_ge1000_premium_pool"), errors="coerce")
    capacity_pool = pd.to_numeric(out.get("capacity_premium_pool_proxy"), errors="coerce")

    out["premium_pool_per_candidate"] = premium_pool / rows
    out["premium_pool_per_oi1000_contract"] = premium_pool / oi_rows
    out["capacity_premium_density"] = capacity_pool / rows
    out["open_interest_per_candidate"] = total_oi / rows
    out["volume_oi_ratio"] = total_volume / total_oi

    bucket_cols = [name for name, _, _ in DELTA_BUCKETS if name in out.columns]
    if bucket_cols:
        bucket_positive = out[bucket_cols].gt(0)
        out["delta_ladder_bucket_count"] = bucket_positive.sum(axis=1)
        out["delta_ladder_depth_score_raw"] = out["delta_ladder_bucket_count"] / float(len(bucket_cols))
        out["near_010_delta_share"] = pd.to_numeric(out.get("delta_008_010_rows"), errors="coerce") / rows
        out["far_002_006_delta_share"] = (
            pd.to_numeric(out.get("delta_002_004_rows"), errors="coerce").fillna(0)
            + pd.to_numeric(out.get("delta_004_006_rows"), errors="coerce").fillna(0)
        ) / rows

    if "avg_v3_front_atm_iv" in out.columns and "avg_v3_second_atm_iv" in out.columns:
        out["front_second_iv_ratio"] = pd.to_numeric(out["avg_v3_front_atm_iv"], errors="coerce") / pd.to_numeric(
            out["avg_v3_second_atm_iv"], errors="coerce"
        ).replace(0, np.nan)
    if "avg_contract_iv" in out.columns and "avg_atm_iv" in out.columns:
        out["wing_iv_premium_to_atm"] = pd.to_numeric(out["avg_contract_iv"], errors="coerce") - pd.to_numeric(
            out["avg_atm_iv"], errors="coerce"
        )

    if "avg_underlying_trend_z_20d" in out.columns:
        out["abs_trend_z_20d"] = pd.to_numeric(out["avg_underlying_trend_z_20d"], errors="coerce").abs()
    if "avg_v3_rv5_rv20" in out.columns:
        out["rv_contraction_raw"] = 1.0 - pd.to_numeric(out["avg_v3_rv5_rv20"], errors="coerce")
    elif "avg_underlying_rv_ratio_5_20" in out.columns:
        out["rv_contraction_raw"] = 1.0 - pd.to_numeric(out["avg_underlying_rv_ratio_5_20"], errors="coerce")

    if side_level:
        out["iv_dulling_raw"] = _mean_existing(
            pd.DataFrame(
                {
                    "iv_falling": -pd.to_numeric(out.get("avg_v3_side_iv_change_3d"), errors="coerce"),
                    "skew_falling": -pd.to_numeric(out.get("avg_v3_side_skew_change_3d"), errors="coerce"),
                    "rv_contraction": pd.to_numeric(out.get("rv_contraction_raw"), errors="coerce"),
                    "term_clean": pd.to_numeric(out.get("avg_v3_term_not_inverted"), errors="coerce"),
                },
                index=out.index,
            ),
            ["iv_falling", "skew_falling", "rv_contraction", "term_clean"],
        )
    return out


def _research_add_rank_fields(panel: pd.DataFrame, side_level: bool) -> pd.DataFrame:
    if panel.empty:
        return panel
    out = panel.copy()
    rank_cols = [
        "capacity_score",
        "premium_quality_score",
        "regime_safety_score",
        "historical_retention_score",
        "tail_cluster_safety_score",
        "product_side_score",
        "product_score",
        "delta_ladder_depth_score",
        "iv_dulling_score",
        "premium_density_score",
        "oi_depth_score",
        "oi_ge1000_premium_pool",
        "capacity_premium_pool_proxy",
        "premium_pool_per_candidate",
        "avg_v3_contract_vrp_pct",
        "avg_v3_vrp_quality_score",
        "avg_v3_term_not_inverted",
    ]
    for col in rank_cols:
        if col not in out.columns:
            continue
        out[f"{col}_rank_date"] = _rank_by_date(out, col)
        out[f"{col}_bucket5_date"] = np.ceil(out[f"{col}_rank_date"] * 5).clip(1, 5)
        if side_level and "side" in out.columns:
            out[f"{col}_rank_side_date"] = pd.to_numeric(out[col], errors="coerce").groupby(
                [out["date"], out["side"]]
            ).rank(pct=True, method="average")
        if "sector" in out.columns:
            out[f"{col}_rank_sector_date"] = pd.to_numeric(out[col], errors="coerce").groupby(
                [out["date"], out["sector"]]
            ).rank(pct=True, method="average")
    return out


def _add_point_in_time_market_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Derive same-day market context from stored history, never forward labels."""
    if panel.empty:
        return panel
    out = panel.copy()
    if "avg_spot_close" in out.columns:
        product_spot = (
            out[["date", "product", "avg_spot_close"]]
            .groupby(["date", "product"], dropna=False)
            .mean(numeric_only=True)
            .reset_index()
            .sort_values(["product", "date"])
        )
        spot_group = product_spot.groupby("product", dropna=False)["avg_spot_close"]
        ret = spot_group.pct_change()
        rv5 = ret.groupby(product_spot["product"]).transform(lambda series: series.rolling(5, min_periods=3).std())
        rv20 = ret.groupby(product_spot["product"]).transform(lambda series: series.rolling(20, min_periods=10).std())
        mean20 = spot_group.transform(lambda series: series.rolling(20, min_periods=10).mean())
        std20 = spot_group.transform(lambda series: series.rolling(20, min_periods=10).std())
        abs_ret = ret.abs()
        product_spot["avg_underlying_rv_ratio_5_20"] = rv5 / rv20.replace(0, np.nan)
        product_spot["avg_underlying_trend_z_20d"] = (
            pd.to_numeric(product_spot["avg_spot_close"], errors="coerce") - mean20
        ) / std20.replace(0, np.nan)
        product_spot["avg_underlying_gap_share_20d"] = abs_ret.gt(0.03).astype(float).groupby(
            product_spot["product"]
        ).transform(lambda series: series.rolling(20, min_periods=10).mean())
        product_spot["avg_underlying_jump_share_20d"] = abs_ret.gt(0.05).astype(float).groupby(
            product_spot["product"]
        ).transform(lambda series: series.rolling(20, min_periods=10).mean())
        payload = [
            "date",
            "product",
            "avg_underlying_rv_ratio_5_20",
            "avg_underlying_trend_z_20d",
            "avg_underlying_gap_share_20d",
            "avg_underlying_jump_share_20d",
        ]
        fallback = product_spot[payload].rename(columns={col: f"__pit_{col}" for col in payload[2:]})
        out = out.merge(
            fallback,
            on=["date", "product"],
            how="left",
        )
        for col in payload[2:]:
            pit_col = f"__pit_{col}"
            if col not in out.columns:
                out[col] = out[pit_col]
            else:
                out[col] = out[col].where(pd.to_numeric(out[col], errors="coerce").notna(), out[pit_col])
        out = out.drop(columns=[f"__pit_{col}" for col in payload[2:]], errors="ignore")

    if "avg_v3_side_iv_pct" in out.columns:
        out = out.sort_values(["product", "side", "date"]).copy()
        side_group = out.groupby(["product", "side"], dropna=False)["avg_v3_side_iv_pct"]
        side_iv_change = side_group.transform(lambda series: pd.to_numeric(series, errors="coerce").diff(3))
        if "avg_v3_side_iv_change_3d" not in out.columns or out["avg_v3_side_iv_change_3d"].isna().all():
            out["avg_v3_side_iv_change_3d"] = side_iv_change
        else:
            out["avg_v3_side_iv_change_3d"] = out["avg_v3_side_iv_change_3d"].where(
                pd.to_numeric(out["avg_v3_side_iv_change_3d"], errors="coerce").notna(),
                side_iv_change,
            )

        iv_side = out.pivot_table(
            index=["date", "product"],
            columns="side",
            values="avg_v3_side_iv_pct",
            aggfunc="mean",
        ).reset_index()
        if "P" in iv_side.columns and "C" in iv_side.columns:
            iv_side["_skew_pc"] = pd.to_numeric(iv_side["P"], errors="coerce") - pd.to_numeric(
                iv_side["C"],
                errors="coerce",
            )
            iv_side = iv_side.sort_values(["product", "date"])
            iv_side["_skew_change_3d"] = iv_side.groupby("product", dropna=False)["_skew_pc"].transform(
                lambda series: series.diff(3)
            )
            skew_payload = iv_side[["date", "product", "_skew_pc", "_skew_change_3d"]]
            out = out.drop(columns=["_skew_pc", "_skew_change_3d"], errors="ignore").merge(
                skew_payload,
                on=["date", "product"],
                how="left",
            )
            signed_skew_change = np.where(out["side"].eq("P"), out["_skew_change_3d"], -out["_skew_change_3d"])
            if "avg_v3_side_skew_change_3d" not in out.columns or out["avg_v3_side_skew_change_3d"].isna().all():
                out["avg_v3_side_skew_change_3d"] = signed_skew_change
            else:
                out["avg_v3_side_skew_change_3d"] = out["avg_v3_side_skew_change_3d"].where(
                    pd.to_numeric(out["avg_v3_side_skew_change_3d"], errors="coerce").notna(),
                    signed_skew_change,
                )
            out = out.drop(columns=["_skew_pc", "_skew_change_3d"], errors="ignore")
    return out


def ensure_l1_loader_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """Return a rolling panel that can be read by the locked L1 loader."""
    out = panel.copy()
    if "option_type" in out.columns:
        out["option_type"] = out["option_type"].fillna("").astype(str).str.upper().str[:1]
    if "side" in out.columns:
        out["side"] = out["side"].fillna("").astype(str).str.upper().str[:1]
    elif "option_type" in out.columns:
        out["side"] = out["option_type"]
    else:
        out["side"] = ""
    if "option_type" not in out.columns:
        out["option_type"] = out["side"]
    for col in L1_PANEL_REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    if "date" in out.columns:
        out["date"] = out["date"].astype(str).str[:10]
    if "product" in out.columns:
        out["product"] = out["product"].fillna("").astype(str).str.upper().str.strip()
    return out


def l1_loader_readiness(panel: pd.DataFrame, signal_date: str) -> dict[str, Any]:
    """Summarize whether the rolling panel is structurally ready for L1 use."""
    missing_columns = [col for col in L1_PANEL_REQUIRED_COLUMNS if col not in panel.columns]
    if panel.empty or missing_columns:
        day = pd.DataFrame()
    else:
        day = panel[panel["date"].astype(str).str[:10].eq(str(signal_date)[:10])].copy()
    hist = pd.to_numeric(day.get("historical_retention_score_bucket5_date"), errors="coerce")
    tail = pd.to_numeric(day.get("tail_cluster_safety_score_bucket5_date"), errors="coerce")
    score = pd.to_numeric(day.get("product_side_score"), errors="coerce")
    gate_ready = hist.notna() & tail.notna()
    score_ready = score.notna()
    return {
        "l1_loader_required_columns_present": not missing_columns,
        "l1_loader_missing_columns": missing_columns,
        "rolling_panel_day_rows": int(len(day)),
        "rolling_panel_gate_ready_rows": int(gate_ready.sum()) if len(day) else 0,
        "rolling_panel_score_ready_rows": int(score_ready.sum()) if len(day) else 0,
        "rolling_panel_loader_ready": bool(not missing_columns and len(day) > 0),
        "rolling_panel_history_ready": bool(not missing_columns and len(day) > 0 and gate_ready.all()),
    }


def scoring_feature_coverage(panel: pd.DataFrame, signal_date: str) -> dict[str, Any]:
    """Record which research scoring inputs are populated for the signal day."""
    if panel.empty or "date" not in panel.columns:
        day = pd.DataFrame()
    else:
        day = panel[panel["date"].astype(str).str[:10].eq(str(signal_date)[:10])].copy()
    fields = [
        "capacity_score",
        "premium_quality_score",
        "regime_safety_score",
        "historical_retention_score",
        "tail_cluster_safety_score",
        "product_side_score",
        "delta_ladder_depth_score",
        "iv_dulling_score",
        "premium_density_score",
        "oi_depth_score",
        "avg_v3_b6_premium_to_stress_rank",
        "avg_v3_b6_premium_to_iv10_rank",
        "avg_v3_b6_theta_vega_rank",
        "avg_v3_b6_theta_gamma_rank",
        "avg_v3_base_b6_score",
        "avg_v3_b6_plus_v3_score",
        "avg_v3_term_not_inverted",
        "avg_underlying_rv_ratio_5_20",
        "avg_underlying_har_pred_rv_5d",
        "avg_underlying_garch_pred_rv_5d",
        "avg_underlying_har_garch_avg_rv_5d",
        "avg_v3_side_iv_change_3d",
        "avg_v3_side_skew_change_3d",
    ]
    coverage = {}
    missing = []
    for field in fields:
        if field not in day.columns or day.empty:
            coverage[field] = 0.0
            missing.append(field)
            continue
        ratio = float(pd.to_numeric(day[field], errors="coerce").notna().mean())
        coverage[field] = ratio
        if ratio <= 0.0:
            missing.append(field)
    return {
        "research_scoring_feature_coverage": coverage,
        "research_scoring_missing_fields": missing,
    }


def build_daily_product_side_observations(
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    signal_date: str,
) -> pd.DataFrame:
    """Aggregate a signal-day L0 universe using the research panel chain."""
    if l0_universe.empty:
        return pd.DataFrame(columns=["date", "product", "side", "option_type", *LABEL_COLUMNS])

    if {"v3_contract_vrp_pct", "v3_b6_premium_to_stress_rank", "v3_disaster_score"}.issubset(l0_universe.columns):
        work = l0_universe.copy()
        work["date"] = work["date"].astype(str).str[:10]
        work = work[work["date"].eq(str(signal_date)[:10])].copy()
        if "side" not in work.columns:
            work["side"] = _safe_text(work, "option_type").str.upper().str[:1]
    else:
        work = _normalize_l0_for_research_aggregate(l0_universe, config, signal_date)
    if work.empty:
        return pd.DataFrame(columns=["date", "product", "side", "option_type", *LABEL_COLUMNS])

    out = _research_aggregate_panel(work, ["date", "product", "side"])
    out = _research_add_derived_factors(out, side_level=True)
    out["option_type"] = out["side"]
    for col in LABEL_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return ensure_l1_loader_columns(out)


def build_product_side_observations_from_contract_fields(
    contract_fields: pd.DataFrame,
    config: dict[str, Any],
    signal_date: str | None = None,
) -> pd.DataFrame:
    """Aggregate full-shadow contract fields to product-side observations."""
    if contract_fields.empty:
        return pd.DataFrame(columns=["date", "product", "side", "option_type", *LABEL_COLUMNS])
    work = contract_fields.copy()
    work["date"] = work["date"].astype(str).str[:10]
    if signal_date is not None:
        work = work[work["date"].eq(str(signal_date)[:10])].copy()
    if work.empty:
        return pd.DataFrame(columns=["date", "product", "side", "option_type", *LABEL_COLUMNS])
    if "side" not in work.columns:
        work["side"] = _safe_text(work, "option_type").str.upper().str[:1]
    out = _research_aggregate_panel(work, ["date", "product", "side"])
    out = _research_add_derived_factors(out, side_level=True)
    out["option_type"] = out["side"]
    for col in LABEL_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return ensure_l1_loader_columns(out)


def _contract_key_frame(snapshot: pd.DataFrame) -> pd.DataFrame:
    contract_cols = [
        "option_code",
        "trade_date",
        "option_close",
        "option_high",
        "expiry_date",
        "option_type",
        "strike",
        "spot_close",
        "multiplier",
    ]
    cols = [col for col in contract_cols if col in snapshot.columns]
    if not cols:
        return pd.DataFrame(columns=contract_cols)
    out = snapshot[cols].copy()
    if "option_code" not in out.columns:
        out["option_code"] = ""
    out["option_code"] = out["option_code"].astype(str)
    if "option_close" not in out.columns:
        out["option_close"] = np.nan
    if "option_high" not in out.columns:
        out["option_high"] = out["option_close"]
    if "expiry_date" not in out.columns:
        out["expiry_date"] = ""
    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].astype(str).str[:10]
    if "option_close" in out.columns:
        out["option_close"] = pd.to_numeric(out["option_close"], errors="coerce")
    if "option_high" in out.columns:
        out["option_high"] = pd.to_numeric(out["option_high"], errors="coerce")
    if "expiry_date" in out.columns:
        out["expiry_date"] = out["expiry_date"].astype(str).str[:10]
    if "option_type" in out.columns:
        out["option_type"] = out["option_type"].astype(str).str.upper().str[:1]
    for col in ["strike", "spot_close", "multiplier"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _load_snapshot_for_date(data_dir: Path, date: str) -> pd.DataFrame:
    path = data_dir / "daily_snapshots" / f"option_chain_{_date_tag(date)}.csv"
    return _read_csv(path)


def _load_snapshot_cached(data_dir: Path, date: str, snapshot_cache: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
    if snapshot_cache is None:
        return _load_snapshot_for_date(data_dir, date)
    key = str(date)[:10]
    if key not in snapshot_cache:
        snapshot_cache[key] = _load_snapshot_for_date(data_dir, key)
        if len(snapshot_cache) > SNAPSHOT_CACHE_MAX_DATES:
            evictable = [old_key for old_key in sorted(snapshot_cache) if old_key != key]
            for old_key in evictable[: len(snapshot_cache) - SNAPSHOT_CACHE_MAX_DATES]:
                snapshot_cache.pop(old_key, None)
    return snapshot_cache[key]


def _available_snapshot_dates(data_dir: Path) -> list[str]:
    out = []
    for path in sorted((data_dir / "daily_snapshots").glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if len(tag) == 8 and tag.isdigit() and _csv_has_rows(path):
            out.append(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}")
    return out


def _bootstrap_contract_shadow_observations(
    data_dir: Path,
    config: dict[str, Any],
    through_date: str,
) -> pd.DataFrame:
    parts = []
    for path in sorted((data_dir / "daily_snapshots").glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if len(tag) != 8 or not tag.isdigit():
            continue
        date = f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"
        if date > str(through_date)[:10]:
            continue
        try:
            snapshot = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            continue
        part = build_daily_contract_shadow_observations(snapshot, config, date)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)


def _mature_observation_labels(
    observations: pd.DataFrame,
    data_dir: Path,
    config: dict[str, Any],
    outcome_horizon: int,
    snapshot_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Update matured product-side outcome labels from stored daily snapshots."""
    if observations.empty:
        return observations

    out = observations.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out["product"] = out["product"].astype(str).str.upper().str.strip()
    out["option_type"] = out["option_type"].astype(str).str.upper().str[:1]
    for col in LABEL_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    if "outcome_matured" not in out.columns:
        out["outcome_matured"] = 0
    available_dates = _available_snapshot_dates(data_dir)
    if not available_dates:
        return out
    date_pos = {date: idx for idx, date in enumerate(available_dates)}
    max_date = max(available_dates)
    stop_multiple = float(config.get("premium_stop_multiple", 2.5) or 2.5)

    for obs_date in sorted(out["date"].dropna().astype(str).unique()):
        if obs_date not in date_pos:
            continue
        start_idx = date_pos[obs_date]
        end_idx = start_idx + int(outcome_horizon)
        if end_idx >= len(available_dates):
            continue
        obs_mask = out["date"].eq(obs_date)
        already_matured = pd.to_numeric(out.loc[obs_mask, "outcome_matured"], errors="coerce").fillna(0).eq(1).all()
        if already_matured:
            continue
        future_dates = available_dates[start_idx + 1:end_idx + 1]
        if not future_dates:
            continue

        entry_snapshot = _load_snapshot_cached(data_dir, obs_date, snapshot_cache)
        if entry_snapshot.empty:
            continue
        entry = entry_snapshot.copy()
        entry["product"] = _safe_text(entry, "product").str.upper().str.strip()
        entry["option_type"] = _safe_text(entry, "option_type").str.upper().str[:1]
        entry["option_code"] = _safe_text(entry, "option_code")
        entry_price = _safe_numeric(entry, "option_close")
        entry = entry[entry_price.gt(0)].copy()
        if entry.empty:
            continue
        entry["_entry_price"] = _safe_numeric(entry, "option_close")
        if "expiry_date" in entry.columns:
            entry["expiry_date"] = entry["expiry_date"].astype(str).str[:10]
        else:
            entry["expiry_date"] = ""
        entry["_entry_multiplier"] = _safe_numeric(entry, "multiplier").fillna(1.0) if "multiplier" in entry.columns else 1.0

        future_5_dates = future_dates[: min(5, len(future_dates))]
        future_5_parts = []
        for future_date in future_5_dates:
            part = _contract_key_frame(_load_snapshot_cached(data_dir, future_date, snapshot_cache))
            if not part.empty:
                part["trade_date"] = future_date
                future_5_parts.append(part)
        future_5 = pd.concat([part for part in future_5_parts if not part.empty], ignore_index=True, sort=False)
        future_parts = []
        for future_date in future_dates:
            part = _contract_key_frame(_load_snapshot_cached(data_dir, future_date, snapshot_cache))
            if not part.empty:
                part["trade_date"] = future_date
                future_parts.append(part)
        future = pd.concat([part for part in future_parts if not part.empty], ignore_index=True, sort=False)
        if future.empty:
            continue
        future_5_max = pd.DataFrame(columns=["option_code", "future_5d_max_high"])
        if not future_5.empty:
            future_5_max = future_5.groupby("option_code").agg(
                future_5d_max_high=("option_high", "max"),
            ).reset_index()
        future_max = future.groupby("option_code").agg(
            future_max_high=("option_high", "max"),
        ).reset_index()
        horizon_close = (
            future[future["trade_date"].astype(str).str[:10].eq(future_dates[-1])]
            .groupby("option_code", as_index=False)
            .agg(future_horizon_close=("option_close", "last"))
        )
        expiry_candidates = entry[["option_code", "expiry_date"]].dropna().drop_duplicates("option_code").copy()
        expiry_candidates = expiry_candidates[expiry_candidates["expiry_date"].astype(str).str.len().ge(10)]
        expiry_labels = pd.DataFrame(
            columns=["option_code", "expiry_option_type", "expiry_strike", "expiry_spot", "expiry_multiplier"]
        )
        if not expiry_candidates.empty:
            max_needed_expiry = expiry_candidates["expiry_date"].max()
            expiry_dates = [
                future_date
                for future_date in available_dates[start_idx + 1:]
                if future_date <= max_needed_expiry and future_date <= max_date
            ]
            expiry_parts = []
            for future_date in expiry_dates:
                part = _contract_key_frame(_load_snapshot_cached(data_dir, future_date, snapshot_cache))
                if not part.empty:
                    part["trade_date"] = future_date
                    expiry_parts.append(part)
            if expiry_parts:
                expiry_tape = pd.concat(expiry_parts, ignore_index=True, sort=False)
                expiry_tape = expiry_tape.merge(expiry_candidates, on="option_code", how="inner", suffixes=("", "_entry"))
                expiry_tape = expiry_tape[
                    expiry_tape["trade_date"].astype(str).str[:10].le(expiry_tape["expiry_date_entry"].astype(str).str[:10])
                ].copy()
                if not expiry_tape.empty:
                    expiry_last = (
                        expiry_tape.sort_values(["option_code", "trade_date"], kind="mergesort")
                        .groupby("option_code", as_index=False)
                        .tail(1)
                    )
                    expiry_labels = expiry_last[
                        ["option_code", "option_type", "strike", "spot_close", "multiplier"]
                    ].rename(
                        columns={
                            "option_type": "expiry_option_type",
                            "strike": "expiry_strike",
                            "spot_close": "expiry_spot",
                            "multiplier": "expiry_multiplier",
                        }
                    )
        labeled = (
            entry.merge(future_5_max, how="left", on="option_code")
            .merge(future_max, how="left", on="option_code")
            .merge(horizon_close, how="left", on="option_code")
            .merge(expiry_labels, how="left", on="option_code")
        )
        max_high_5d = pd.to_numeric(labeled["future_5d_max_high"], errors="coerce")
        max_high = pd.to_numeric(labeled["future_max_high"], errors="coerce")
        horizon_close_px = pd.to_numeric(labeled["future_horizon_close"], errors="coerce")
        entry_px = pd.to_numeric(labeled["_entry_price"], errors="coerce")
        labeled["label_v3_stop_touch_5d"] = np.where(
            max_high_5d.notna(),
            max_high_5d.ge(entry_px * stop_multiple).astype(float),
            np.nan,
        )
        labeled["label_v3_stop_touch_10d"] = np.where(
            max_high.notna(),
            max_high.ge(entry_px * stop_multiple).astype(float),
            np.nan,
        )
        labeled["label_v3_retention_10d"] = 1.0 - horizon_close_px / entry_px
        labeled["label_v3_max_adverse_price_ratio_10d"] = max_high / entry_px
        expiry_spot = _safe_numeric(labeled, "expiry_spot")
        expiry_strike = _safe_numeric(labeled, "expiry_strike")
        expiry_multiplier = _safe_numeric(labeled, "expiry_multiplier")
        expiry_multiplier = expiry_multiplier.where(expiry_multiplier.gt(0), labeled["_entry_multiplier"])
        entry_multiplier = pd.to_numeric(labeled["_entry_multiplier"], errors="coerce").replace(0, np.nan)
        entry_premium_cash = entry_px * entry_multiplier
        expiry_type_source = labeled["expiry_option_type"] if "expiry_option_type" in labeled.columns else labeled["option_type"]
        expiry_type = expiry_type_source.astype(str).str.upper().str[:1]
        call_intrinsic = (expiry_spot - expiry_strike).clip(lower=0.0)
        put_intrinsic = (expiry_strike - expiry_spot).clip(lower=0.0)
        intrinsic = pd.Series(np.nan, index=labeled.index, dtype=float)
        intrinsic.loc[expiry_type.eq("C")] = call_intrinsic.loc[expiry_type.eq("C")]
        intrinsic.loc[expiry_type.eq("P")] = put_intrinsic.loc[expiry_type.eq("P")]
        intrinsic_cash = intrinsic * expiry_multiplier
        labeled["label_v3_retention_to_expiry_clipped"] = (
            (entry_premium_cash - intrinsic_cash) / entry_premium_cash.replace(0, np.nan)
        ).clip(lower=-3.0, upper=1.0)
        labeled["label_v3_expire_otm_flag"] = intrinsic.le(1e-9).where(intrinsic.notna())
        product_daily = labeled.groupby("product", dropna=False).agg(
            product_stop_rate_5d=("label_v3_stop_touch_5d", "mean"),
        ).reset_index()
        product_daily["product_stop_flag_5d"] = product_daily["product_stop_rate_5d"].gt(0).astype(float)
        stop_product_count = float(product_daily["product_stop_flag_5d"].sum())
        product_daily["label_v3_product_stop_cluster_with_portfolio"] = np.where(
            product_daily["product_stop_flag_5d"].eq(1.0),
            stop_product_count - 1.0,
            0.0,
        )

        side_labels = labeled.groupby(["product", "option_type"], dropna=False).agg(
            label_v3_stop_touch_5d=("label_v3_stop_touch_5d", "mean"),
            label_v3_retention_10d=("label_v3_retention_10d", "mean"),
            label_v3_stop_touch_10d=("label_v3_stop_touch_10d", "mean"),
            label_v3_retention_to_expiry_clipped=("label_v3_retention_to_expiry_clipped", "mean"),
            label_v3_max_adverse_price_ratio_10d=("label_v3_max_adverse_price_ratio_10d", "mean"),
        ).reset_index()
        side_labels["date"] = obs_date
        side_labels = side_labels.merge(
            product_daily[["product", "label_v3_product_stop_cluster_with_portfolio"]],
            on="product",
            how="left",
        )

        label_map = side_labels.set_index(["date", "product", "option_type"])[LABEL_COLUMNS]
        target_idx = out.index[obs_mask]
        target_keys = pd.MultiIndex.from_frame(out.loc[target_idx, ["date", "product", "option_type"]])
        updates = label_map.reindex(target_keys)
        updates.index = target_idx
        for col in LABEL_COLUMNS:
            out.loc[target_idx, col] = updates[col].where(updates[col].notna(), out.loc[target_idx, col])
        out.loc[target_idx, "outcome_matured"] = 1

    out["outcome_matured"] = out["date"].astype(str).map(
        lambda date: int(date in date_pos and date_pos[date] + int(outcome_horizon) < len(available_dates) and max_date > date)
    )
    return out


def _add_hist_and_scores(observations: pd.DataFrame, hist_window: int) -> pd.DataFrame:
    if observations.empty:
        return ensure_l1_loader_columns(observations)
    out = observations.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out["product"] = out["product"].astype(str).str.upper().str.strip()
    out["option_type"] = out["option_type"].astype(str).str.upper().str[:1]
    out["side"] = out["option_type"]
    out = out.sort_values(["product", "side", "date"]).reset_index(drop=True)
    group_keys = ["product", "side"]

    def shifted_roll(col: str, min_periods: int = 10) -> pd.Series:
        if col not in out.columns:
            return pd.Series(np.nan, index=out.index)
        return out.groupby(group_keys, dropna=False)[col].transform(
            lambda series: pd.to_numeric(series, errors="coerce").shift(1).rolling(
                hist_window,
                min_periods=min(min_periods, hist_window),
            ).mean()
        )

    out["hist_retention_10d_63d"] = shifted_roll("label_v3_retention_10d")
    out["label_stop_avoid_10d"] = 1.0 - _safe_numeric(out, "label_v3_stop_touch_10d")
    out["hist_stop_avoid_10d_63d"] = shifted_roll("label_stop_avoid_10d")
    out["hist_expiry_retention_63d"] = shifted_roll("label_v3_retention_to_expiry_clipped")
    out["hist_max_adverse_10d_63d"] = shifted_roll("label_v3_max_adverse_price_ratio_10d")
    out["hist_stop_cluster_63d"] = shifted_roll("label_v3_product_stop_cluster_with_portfolio")
    out = _add_point_in_time_market_features(out)

    capacity_components = pd.DataFrame(
        {
            "oi_rows": _rank_by_date(out, "oi_ge1000_rows") if "oi_ge1000_rows" in out else np.nan,
            "oi_premium": _rank_by_date(out, "oi_ge1000_premium_pool") if "oi_ge1000_premium_pool" in out else np.nan,
            "capacity_pool": _rank_by_date(out, "capacity_premium_pool_proxy")
            if "capacity_premium_pool_proxy" in out else np.nan,
        },
        index=out.index,
    )
    out["capacity_score"] = _mean_existing(capacity_components, list(capacity_components.columns))

    premium_cols: dict[str, pd.Series] = {}
    if "avg_v3_contract_vrp_pct" in out:
        premium_cols["vrp_pct"] = _rank_by_date(out, "avg_v3_contract_vrp_pct")
    if "avg_v3_vrp_quality_score" in out:
        premium_cols["vrp_quality"] = _rank_by_date(out, "avg_v3_vrp_quality_score")
    if "avg_v3_term_not_inverted" in out:
        premium_cols["term_clean"] = _rank_by_date(out, "avg_v3_term_not_inverted")
    if "avg_v3_b6_premium_to_stress_rank" in out:
        premium_cols["premium_stress"] = _rank_by_date(out, "avg_v3_b6_premium_to_stress_rank")
    out["premium_quality_score"] = _mean_existing(pd.DataFrame(premium_cols, index=out.index), list(premium_cols))

    regime_cols: dict[str, pd.Series] = {}
    for col in [
        "avg_v3_rv5_rv20",
        "avg_underlying_rv_ratio_5_20",
        "avg_underlying_gap_share_20d",
        "avg_underlying_jump_share_20d",
        "avg_v3_disaster_score",
        "avg_v3_trend_breakout_score",
    ]:
        if col in out:
            regime_cols[col] = _rank_by_date(out, col, higher_good=False)
    if "avg_underlying_trend_z_20d" in out:
        tmp = out[["date", "avg_underlying_trend_z_20d"]].copy()
        tmp["_abs_trend"] = pd.to_numeric(tmp["avg_underlying_trend_z_20d"], errors="coerce").abs()
        regime_cols["abs_trend_safe"] = _rank_by_date(
            tmp.rename(columns={"_abs_trend": "abs_trend"}),
            "abs_trend",
            higher_good=False,
        )
    for col in ["avg_v3_side_iv_change_3d", "avg_v3_side_skew_change_3d"]:
        if col in out:
            regime_cols[col] = _rank_by_date(out, col, higher_good=False)
    out["regime_safety_score"] = _mean_existing(pd.DataFrame(regime_cols, index=out.index), list(regime_cols))

    hist_cols: dict[str, pd.Series] = {}
    if "hist_retention_10d_63d" in out:
        hist_cols["hist_retention"] = _rank_by_date(out, "hist_retention_10d_63d")
    if "hist_stop_avoid_10d_63d" in out:
        hist_cols["hist_stop"] = _rank_by_date(out, "hist_stop_avoid_10d_63d")
    if "hist_expiry_retention_63d" in out:
        hist_cols["hist_expiry"] = _rank_by_date(out, "hist_expiry_retention_63d")
    if "hist_max_adverse_10d_63d" in out:
        hist_cols["hist_path"] = _rank_by_date(out, "hist_max_adverse_10d_63d", higher_good=False)
    out["historical_retention_score"] = _mean_existing(pd.DataFrame(hist_cols, index=out.index), list(hist_cols))

    out["product_side_score"] = (
        0.25 * out["capacity_score"]
        + 0.30 * out["premium_quality_score"]
        + 0.25 * out["regime_safety_score"]
        + 0.20 * out["historical_retention_score"]
    )
    if "hist_stop_cluster_63d" in out:
        cluster_penalty = _rank_by_date(out, "hist_stop_cluster_63d", higher_good=False)
        out["tail_cluster_safety_score"] = cluster_penalty
        out["product_side_score"] = 0.90 * out["product_side_score"] + 0.10 * cluster_penalty
    if "delta_ladder_depth_score_raw" in out:
        out["delta_ladder_depth_score"] = _rank_by_date(out, "delta_ladder_depth_score_raw")
    if "iv_dulling_raw" in out:
        out["iv_dulling_score"] = _rank_by_date(out, "iv_dulling_raw")
    if "premium_pool_per_candidate" in out:
        out["premium_density_score"] = _rank_by_date(out, "premium_pool_per_candidate")
    if "open_interest_per_candidate" in out:
        out["oi_depth_score"] = _rank_by_date(out, "open_interest_per_candidate")
    return ensure_l1_loader_columns(_research_add_rank_fields(out, side_level=True))


def build_rolling_l1_admission(panel: pd.DataFrame, config: dict[str, Any], signal_date: str) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    panel = ensure_l1_loader_columns(panel)
    date = str(signal_date)[:10]
    day = panel[panel["date"].astype(str).str[:10].eq(date)].copy()
    if day.empty:
        return day
    hist = pd.to_numeric(day.get("historical_retention_score_bucket5_date"), errors="coerce")
    tail = pd.to_numeric(day.get("tail_cluster_safety_score_bucket5_date"), errors="coerce")
    min_hist = float(config.get("s1_l1_min_hist_bucket", 3) or 3)
    min_tail = float(config.get("s1_l1_min_tail_bucket", 3) or 3)
    day["l1_hist_bucket"] = hist
    day["l1_tail_bucket"] = tail
    day["l1_missing_panel"] = hist.isna() | tail.isna()
    day["l1_pass_gate"] = hist.ge(min_hist) & tail.ge(min_tail) & ~day["l1_missing_panel"]
    day["l1_min_hist_bucket"] = min_hist
    day["l1_min_tail_bucket"] = min_tail
    return day


def update_rolling_product_side_panel(
    signal_date: str,
    *,
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    data_dir: Path,
    hist_window: int = 63,
    outcome_horizon: int = 10,
    write_outputs: bool = True,
    snapshot_cache: dict[str, pd.DataFrame] | None = None,
    rebuild_contract_history: bool = False,
) -> RollingProductSideUpdateResult:
    """Upsert one date and refresh the full rolling S1 product-side panel."""
    date = str(signal_date)[:10]
    product_side_dir = data_dir / "product_side_panel"
    contract_shadow_dir = product_side_dir / CONTRACT_SHADOW_DIR
    manifest_dir = data_dir / "manifests"
    contract_observation_path = product_side_dir / ROLLING_CONTRACT_OBSERVATION_FILE
    contract_fields_path = contract_shadow_dir / f"{CONTRACT_FIELDS_PREFIX}_{_date_tag(date)}.csv"
    observation_path = product_side_dir / ROLLING_OBSERVATION_FILE
    panel_path = product_side_dir / ROLLING_PANEL_FILE
    admission_path = product_side_dir / f"{ROLLING_ADMISSION_PREFIX}_{_date_tag(date)}.csv"
    manifest_path = manifest_dir / f"{ROLLING_MANIFEST_PREFIX}_{_date_tag(date)}.json"

    existing_contracts = _read_csv(contract_observation_path)
    if (rebuild_contract_history or existing_contracts.empty) and (data_dir / "daily_snapshots").exists():
        existing_contracts = _bootstrap_contract_shadow_observations(data_dir, config, date)
    new_contracts = build_daily_contract_shadow_observations(l0_universe, config, date)
    contract_observations = _upsert_by_key(existing_contracts, new_contracts, ["date", "contract_code"])
    contract_fields = _add_full_shadow_contract_fields(contract_observations)
    day_contract_fields = contract_fields[contract_fields["date"].astype(str).str[:10].eq(date)].copy()

    existing = _read_csv(observation_path)
    exact_required_cols = [
        "avg_v3_base_b6_score",
        "avg_v3_b6_plus_v3_score",
        "avg_underlying_har_garch_avg_rv_5d",
    ]
    rebuild_observations = (
        rebuild_contract_history
        or existing.empty
        or any(col not in existing.columns for col in exact_required_cols)
    )
    if rebuild_observations:
        new_observations = build_product_side_observations_from_contract_fields(contract_fields, config)
        observations = new_observations.copy()
    else:
        new_observations = build_product_side_observations_from_contract_fields(day_contract_fields, config, date)
        observations = _upsert_by_key(existing, new_observations, ["date", "product", "option_type"])
    observations = _mature_observation_labels(observations, data_dir, config, outcome_horizon, snapshot_cache)
    panel = _add_hist_and_scores(observations, hist_window)
    panel = ensure_l1_loader_columns(panel)
    admission = build_rolling_l1_admission(panel, config, date)
    matured_rows = int(pd.to_numeric(observations.get("outcome_matured", 0), errors="coerce").fillna(0).sum())
    readiness = l1_loader_readiness(panel, date)
    coverage = scoring_feature_coverage(panel, date)

    if write_outputs:
        write_csv(contract_observation_path, contract_observations)
        write_csv(contract_fields_path, day_contract_fields)
        write_csv(observation_path, observations)
        write_csv(panel_path, panel)
        write_csv(admission_path, admission)
        write_json(
            manifest_path,
            {
                "signal_date": date,
                "contract_observation_rows": int(len(contract_observations)),
                "new_contract_observation_rows": int(len(new_contracts)),
                "contract_fields_rows": int(len(day_contract_fields)),
                "rebuilt_contract_shadow_history": bool(rebuild_contract_history),
                "observation_rows": int(len(observations)),
                "new_observation_rows": int(len(new_observations)),
                "rebuilt_product_side_observation_history": bool(rebuild_observations),
                "panel_rows": int(len(panel)),
                "admission_rows": int(len(admission)),
                "matured_observation_rows": matured_rows,
                **readiness,
                **coverage,
                "hist_window": int(hist_window),
                "full_shadow_rolling_window": int(FULL_SHADOW_ROLLING_WINDOW),
                "full_shadow_min_obs": int(FULL_SHADOW_MIN_OBS),
                "outcome_horizon": int(outcome_horizon),
                "contract_observation_path": str(contract_observation_path),
                "contract_fields_path": str(contract_fields_path),
                "panel_path": str(panel_path),
                "admission_path": str(admission_path),
                "no_future_function_note": (
                    "Signal-day hist_* features use shifted rolling outcomes. "
                    "Outcome labels are updated only after stored future snapshots "
                    "cover the configured horizon."
                ),
            },
        )
    else:
        contract_observation_path = contract_fields_path = observation_path = panel_path = admission_path = manifest_path = None

    return RollingProductSideUpdateResult(
        signal_date=date,
        observation_rows=int(len(observations)),
        panel_rows=int(len(panel)),
        admission_rows=int(len(admission)),
        matured_observation_rows=matured_rows,
        contract_observation_rows=int(len(contract_observations)),
        contract_fields_rows=int(len(day_contract_fields)),
        contract_observation_path=contract_observation_path,
        contract_fields_path=contract_fields_path,
        observation_path=observation_path,
        panel_path=panel_path,
        admission_path=admission_path,
        manifest_path=manifest_path,
    )
