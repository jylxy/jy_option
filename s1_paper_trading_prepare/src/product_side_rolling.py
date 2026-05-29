"""S1-only rolling product-side panel maintenance.

This module maintains the live paper-trading replacement for the old research
product-side panel.  It uses stored daily option snapshots only, updates by
date partition, and shifts outcome history before computing signal-day buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .diagnostics import write_csv, write_json


ROLLING_PANEL_FILE = "rolling_product_side_panel.csv"
ROLLING_OBSERVATION_FILE = "rolling_product_side_observations.csv"
ROLLING_ADMISSION_PREFIX = "rolling_l1_admission"
ROLLING_MANIFEST_PREFIX = "rolling_product_side_update"
CATEGORY_FIELDS = ["sector", "portfolio_bucket", "corr_group"]
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
    "underlying_trend_z_20d",
    "underlying_rv_ratio_5_20",
    "underlying_gap_share_20d",
    "underlying_jump_share_20d",
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
    out["entry_volume"] = volume.where(eligible, 0.0)
    out["entry_open_interest"] = oi.where(eligible, 0.0)
    out["entry_premium_cash_1lot"] = (price * multiplier).where(eligible, 0.0)
    out["v3_capacity_lots_10pct"] = (volume * volume_cap).where(eligible, 0.0)
    out["abs_delta"] = abs_delta.where(eligible)
    out["contract_iv"] = implied_vol.where(eligible)
    out["atm_iv"] = implied_vol.where(eligible)
    out["spot_close"] = spot_close
    out["v3_contract_vrp_pct"] = implied_vol.where(eligible)
    out["v3_contract_vrp_core"] = implied_vol.where(eligible)
    out["v3_vrp_quality_score"] = implied_vol.where(eligible)
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

    out["oi_ge1000_flag"] = (
        eligible & out["entry_open_interest"].ge(float(config.get("s1_min_oi", 1000) or 1000))
    ).astype(float)
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
        "candidate_rows": ("trade_eligible_flag", "sum") if "trade_eligible_flag" in frame.columns else ("product", "size"),
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
        out = out.drop(columns=[col for col in payload[2:] if col in out.columns], errors="ignore").merge(
            product_spot[payload],
            on=["date", "product"],
            how="left",
        )

    if "avg_v3_side_iv_pct" in out.columns:
        out = out.sort_values(["product", "side", "date"]).copy()
        side_group = out.groupby(["product", "side"], dropna=False)["avg_v3_side_iv_pct"]
        side_iv_change = side_group.transform(lambda series: pd.to_numeric(series, errors="coerce").diff(3))
        if "avg_v3_side_iv_change_3d" not in out.columns or out["avg_v3_side_iv_change_3d"].isna().all():
            out["avg_v3_side_iv_change_3d"] = side_iv_change

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
        "avg_v3_term_not_inverted",
        "avg_underlying_rv_ratio_5_20",
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


def _contract_key_frame(snapshot: pd.DataFrame) -> pd.DataFrame:
    cols = [col for col in ["option_code", "trade_date", "option_close", "option_high", "expiry_date"] if col in snapshot.columns]
    if not cols:
        return pd.DataFrame(columns=["option_code", "trade_date", "option_close", "option_high", "expiry_date"])
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
        if len(snapshot_cache) > 32:
            for old_key in sorted(snapshot_cache)[: len(snapshot_cache) - 32]:
                snapshot_cache.pop(old_key, None)
    return snapshot_cache[key]


def _available_snapshot_dates(data_dir: Path) -> list[str]:
    out = []
    for path in sorted((data_dir / "daily_snapshots").glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if len(tag) == 8 and tag.isdigit():
            out.append(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}")
    return out


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

        future_5_dates = future_dates[: min(5, len(future_dates))]
        future_5_parts = [
            _contract_key_frame(_load_snapshot_cached(data_dir, date, snapshot_cache))
            for date in future_5_dates
        ]
        future_5 = pd.concat([part for part in future_5_parts if not part.empty], ignore_index=True, sort=False)
        future_parts = [
            _contract_key_frame(_load_snapshot_cached(data_dir, date, snapshot_cache))
            for date in future_dates
        ]
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
            future_last_close=("option_close", "last"),
        ).reset_index()
        labeled = entry.merge(future_5_max, how="left", on="option_code").merge(future_max, how="left", on="option_code")
        max_high_5d = pd.to_numeric(labeled["future_5d_max_high"], errors="coerce")
        max_high = pd.to_numeric(labeled["future_max_high"], errors="coerce")
        last_close = pd.to_numeric(labeled["future_last_close"], errors="coerce")
        entry_px = pd.to_numeric(labeled["_entry_price"], errors="coerce")
        labeled["label_v3_stop_touch_5d"] = max_high_5d.ge(entry_px * stop_multiple).astype(float)
        labeled["label_v3_stop_touch_10d"] = max_high.ge(entry_px * stop_multiple).astype(float)
        labeled["label_v3_retention_10d"] = ((entry_px - last_close) / entry_px).clip(lower=0.0, upper=1.0)
        labeled["label_v3_max_adverse_price_ratio_10d"] = ((max_high / entry_px) - 1.0).clip(lower=0.0)
        labeled["label_v3_retention_to_expiry_clipped"] = labeled["label_v3_retention_10d"]
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
) -> RollingProductSideUpdateResult:
    """Upsert one date and refresh the full rolling S1 product-side panel."""
    date = str(signal_date)[:10]
    product_side_dir = data_dir / "product_side_panel"
    manifest_dir = data_dir / "manifests"
    observation_path = product_side_dir / ROLLING_OBSERVATION_FILE
    panel_path = product_side_dir / ROLLING_PANEL_FILE
    admission_path = product_side_dir / f"{ROLLING_ADMISSION_PREFIX}_{_date_tag(date)}.csv"
    manifest_path = manifest_dir / f"{ROLLING_MANIFEST_PREFIX}_{_date_tag(date)}.json"

    existing = _read_csv(observation_path)
    new_observations = build_daily_product_side_observations(l0_universe, config, date)
    observations = _upsert_by_key(existing, new_observations, ["date", "product", "option_type"])
    observations = _mature_observation_labels(observations, data_dir, config, outcome_horizon, snapshot_cache)
    panel = _add_hist_and_scores(observations, hist_window)
    panel = ensure_l1_loader_columns(panel)
    admission = build_rolling_l1_admission(panel, config, date)
    matured_rows = int(pd.to_numeric(observations.get("outcome_matured", 0), errors="coerce").fillna(0).sum())
    readiness = l1_loader_readiness(panel, date)
    coverage = scoring_feature_coverage(panel, date)

    if write_outputs:
        write_csv(observation_path, observations)
        write_csv(panel_path, panel)
        write_csv(admission_path, admission)
        write_json(
            manifest_path,
            {
                "signal_date": date,
                "observation_rows": int(len(observations)),
                "new_observation_rows": int(len(new_observations)),
                "panel_rows": int(len(panel)),
                "admission_rows": int(len(admission)),
                "matured_observation_rows": matured_rows,
                **readiness,
                **coverage,
                "hist_window": int(hist_window),
                "outcome_horizon": int(outcome_horizon),
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
        observation_path = panel_path = admission_path = manifest_path = None

    return RollingProductSideUpdateResult(
        signal_date=date,
        observation_rows=int(len(observations)),
        panel_rows=int(len(panel)),
        admission_rows=int(len(admission)),
        matured_observation_rows=matured_rows,
        observation_path=observation_path,
        panel_path=panel_path,
        admission_path=admission_path,
        manifest_path=manifest_path,
    )
