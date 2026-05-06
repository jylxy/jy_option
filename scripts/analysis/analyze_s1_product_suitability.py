"""Build product suitability tiers for S1 short-premium research.

The screen is intentionally split into two parts:
- ex-ante suitability: only fields available at signal time.
- outcome validation: shadow labels used as diagnostics, not as the
  primary source of the blacklist.

Example:
    python scripts/analyze_s1_product_suitability.py \
        --tag s1_b5_full_shadow_v1_2022_latest

    python scripts/analyze_s1_product_suitability.py \
        --tag s1_b5_full_shadow_v1_delta006_012 \
        --min-abs-delta 0.06 --max-abs-delta 0.12 \
        --min-option-price 0.5
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_CANDIDATE = (
    "output/s1_candidate_universe_s1_b5_full_shadow_v1_2022_latest.csv"
)
DEFAULT_OUTCOMES = (
    "output/s1_candidate_outcomes_s1_b5_full_shadow_v1_2022_latest.csv"
)
DEFAULT_PRODUCT_PANEL = None


def _num(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return pd.Series(default, index=frame.index, dtype=float)


def _rank_high(series: pd.Series, fill: float = 0.5) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(fill, index=values.index, dtype=float)
    return values.rank(pct=True).fillna(fill).clip(0.0, 1.0)


def _rank_low(series: pd.Series, fill: float = 0.5) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    n = int(values.notna().sum())
    if n <= 1:
        return pd.Series(fill, index=values.index, dtype=float)
    return (1.0 - values.rank(pct=True) + 1.0 / n).fillna(fill).clip(0.0, 1.0)


def _clip100(series: pd.Series) -> pd.Series:
    return (100.0 * series).clip(0.0, 100.0)


def _safe_divide(a: pd.Series, b: pd.Series) -> pd.Series:
    return pd.to_numeric(a, errors="coerce") / pd.to_numeric(b, errors="coerce").replace(0, np.nan)


def _safe_cv(std: pd.Series, center: pd.Series) -> pd.Series:
    denom = pd.to_numeric(center, errors="coerce").abs().replace(0, np.nan)
    return _safe_divide(std, denom)


def _q75(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(clean.quantile(0.75)) if len(clean) else np.nan


def _median_abs(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(clean.abs().median()) if len(clean) else np.nan


def _weighted_score(parts: dict[str, tuple[pd.Series, float]]) -> pd.Series:
    score = None
    weight_sum = 0.0
    for series, weight in parts.values():
        weight = max(float(weight or 0.0), 0.0)
        if weight <= 0:
            continue
        values = pd.to_numeric(series, errors="coerce").fillna(50.0)
        score = values * weight if score is None else score + values * weight
        weight_sum += weight
    if score is None or weight_sum <= 0:
        return pd.Series(50.0, index=next(iter(parts.values()))[0].index)
    return (score / weight_sum).clip(0.0, 100.0)


def _filter_target_contract_band(candidates: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, str]:
    c = candidates.copy()
    filters = []

    if args.min_abs_delta is not None:
        c["abs_delta"] = _num(c, "abs_delta")
        c = c[c["abs_delta"] >= float(args.min_abs_delta)].copy()
        filters.append(f"abs_delta>={float(args.min_abs_delta):.4f}")
    if args.max_abs_delta is not None:
        c["abs_delta"] = _num(c, "abs_delta")
        c = c[c["abs_delta"] <= float(args.max_abs_delta)].copy()
        filters.append(f"abs_delta<={float(args.max_abs_delta):.4f}")
    if args.min_option_price is not None:
        c["option_price"] = _num(c, "option_price")
        c = c[c["option_price"] >= float(args.min_option_price)].copy()
        filters.append(f"option_price>={float(args.min_option_price):.4f}")
    if args.max_option_price is not None:
        c["option_price"] = _num(c, "option_price")
        c = c[c["option_price"] <= float(args.max_option_price)].copy()
        filters.append(f"option_price<={float(args.max_option_price):.4f}")
    if args.max_rank_in_side is not None:
        c["rank_in_side"] = _num(c, "rank_in_side")
        c = c[c["rank_in_side"] <= float(args.max_rank_in_side)].copy()
        filters.append(f"rank_in_side<={float(args.max_rank_in_side):.0f}")
    if args.min_dte is not None:
        c["dte"] = _num(c, "dte")
        c = c[c["dte"] >= float(args.min_dte)].copy()
        filters.append(f"dte>={float(args.min_dte):.0f}")
    if args.max_dte is not None:
        c["dte"] = _num(c, "dte")
        c = c[c["dte"] <= float(args.max_dte)].copy()
        filters.append(f"dte<={float(args.max_dte):.0f}")

    return c, "; ".join(filters) if filters else "all_candidates"


def _aggregate_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    c = candidates.copy()
    c["signal_date"] = pd.to_datetime(c["signal_date"], errors="coerce")
    c["month"] = c["signal_date"].dt.to_period("M").astype(str)
    c["side_key"] = c["product"].astype(str) + "_" + c["option_type"].astype(str)

    for column in (
        "volume",
        "open_interest",
        "liquidity_score",
        "friction_ratio",
        "fee_ratio",
        "b5_tick_value_ratio",
        "b5_low_price_flag",
        "contract_iv",
        "net_premium_cash_1lot",
        "gross_premium_cash_1lot",
        "premium_yield_margin",
        "b5_premium_per_capital_day",
        "rv_ref",
        "variance_carry",
        "b5_variance_carry_forward",
        "iv_rv_spread_candidate",
        "iv_rv_ratio_candidate",
        "premium_to_iv10_loss",
        "theta_vega_efficiency",
        "b5_theta_per_vega",
        "b5_premium_per_vega",
        "premium_to_stress_loss",
        "b5_premium_to_tail_move_loss",
        "b5_tail_move_pct",
        "b5_tail_move_loss_cash",
        "b5_premium_to_mae20_loss",
        "b5_mae20_move_pct",
        "b5_mae20_loss_cash",
        "b5_premium_to_expected_move_loss",
        "b5_expected_move_pct",
        "b5_expected_move_loss_cash",
        "gamma_rent_penalty",
        "b3_vomma_loss_ratio",
        "b3_vol_of_vol_proxy",
        "b5_range_expansion_proxy_20d",
        "b5_atm_iv_mom_5d",
        "b5_atm_iv_mom_20d",
        "b5_atm_iv_accel",
        "b5_iv_zscore_60d",
        "b5_iv_reversion_score",
        "b5_product_stop_count_20d",
        "b5_cooldown_penalty_score",
        "option_price",
        "abs_delta",
        "dte",
    ):
        c[column] = _num(c, column)

    monthly_pool = (
        c.assign(net_premium_pos=c["net_premium_cash_1lot"].clip(lower=0.0))
        .groupby(["product", "month"], dropna=False)["net_premium_pos"]
        .sum()
        .groupby("product")
        .median()
        .rename("monthly_premium_pool_median")
    )
    side_day_counts = (
        c.groupby(["product", "signal_date"])["option_type"]
        .nunique()
        .groupby("product")
        .mean()
        .rename("avg_sides_per_day")
    )
    product_side_count = c.groupby("product")["side_key"].nunique().rename("product_side_count")

    agg_spec = {
        "signal_date": [lambda x: x.nunique()],
        "candidate_id": "count",
        "volume": "median",
        "open_interest": "median",
        "liquidity_score": "median",
        "friction_ratio": ["median", lambda x: x.quantile(0.75)],
        "fee_ratio": "median",
        "b5_tick_value_ratio": "median",
        "b5_low_price_flag": "mean",
        "contract_iv": lambda x: x.notna().mean(),
        "net_premium_cash_1lot": "median",
        "gross_premium_cash_1lot": "median",
        "premium_yield_margin": "median",
        "b5_premium_per_capital_day": "median",
        "variance_carry": "median",
        "b5_variance_carry_forward": "median",
        "iv_rv_spread_candidate": "median",
        "iv_rv_ratio_candidate": "median",
        "premium_to_iv10_loss": "median",
        "theta_vega_efficiency": "median",
        "b5_theta_per_vega": "median",
        "b5_premium_per_vega": "median",
        "premium_to_stress_loss": "median",
        "b5_premium_to_tail_move_loss": "median",
        "b5_premium_to_mae20_loss": "median",
        "b5_premium_to_expected_move_loss": "median",
        "gamma_rent_penalty": "median",
        "b3_vomma_loss_ratio": "median",
        "b5_range_expansion_proxy_20d": "median",
        "b5_product_stop_count_20d": "median",
        "b5_cooldown_penalty_score": "median",
        "option_price": "median",
        "abs_delta": "median",
        "dte": "median",
    }
    grouped = c.groupby("product", dropna=False).agg(agg_spec)
    grouped.columns = [
        "signal_days",
        "candidate_count",
        "volume_median",
        "open_interest_median",
        "liquidity_score_median",
        "friction_ratio_median",
        "friction_ratio_p75",
        "fee_ratio_median",
        "tick_value_ratio_median",
        "low_price_rate",
        "valid_iv_rate",
        "net_premium_cash_median",
        "gross_premium_cash_median",
        "premium_yield_margin_median",
        "premium_per_capital_day_median",
        "variance_carry_median",
        "variance_carry_forward_median",
        "iv_rv_spread_median",
        "iv_rv_ratio_median",
        "premium_to_iv10_loss_median",
        "theta_vega_efficiency_median",
        "theta_per_vega_median",
        "premium_per_vega_median",
        "premium_to_stress_loss_median",
        "premium_to_tail_move_loss_median",
        "premium_to_mae20_loss_median",
        "premium_to_expected_move_loss_median",
        "gamma_rent_penalty_median",
        "vomma_loss_ratio_median",
        "range_expansion_median",
        "product_stop_count_20d_median",
        "cooldown_penalty_median",
        "option_price_median",
        "abs_delta_median",
        "dte_median",
    ]
    grouped = grouped.join(monthly_pool, how="left")
    grouped = grouped.join(side_day_counts, how="left")
    grouped = grouped.join(product_side_count, how="left")
    grouped["avg_candidates_per_day"] = _safe_divide(grouped["candidate_count"], grouped["signal_days"])
    grouped["both_side_day_ratio_proxy"] = (grouped["avg_sides_per_day"] / 2.0).clip(0.0, 1.0)

    stability = c.groupby("product", dropna=False).agg(
        contract_iv_median=("contract_iv", "median"),
        contract_iv_std=("contract_iv", "std"),
        rv_ref_median=("rv_ref", "median"),
        rv_ref_std=("rv_ref", "std"),
        iv_rv_spread_std=("iv_rv_spread_candidate", "std"),
        iv_rv_ratio_std=("iv_rv_ratio_candidate", "std"),
        variance_carry_std=("variance_carry", "std"),
        variance_carry_forward_std=("b5_variance_carry_forward", "std"),
        tail_move_pct_median=("b5_tail_move_pct", "median"),
        tail_move_pct_p75=("b5_tail_move_pct", _q75),
        mae20_move_pct_median=("b5_mae20_move_pct", "median"),
        mae20_move_pct_p75=("b5_mae20_move_pct", _q75),
        expected_move_pct_median=("b5_expected_move_pct", "median"),
        expected_move_pct_p75=("b5_expected_move_pct", _q75),
        range_expansion_p75=("b5_range_expansion_proxy_20d", _q75),
        range_expansion_std=("b5_range_expansion_proxy_20d", "std"),
        vol_of_vol_median=("b3_vol_of_vol_proxy", "median"),
        vol_of_vol_p75=("b3_vol_of_vol_proxy", _q75),
        atm_iv_abs_mom5_median=("b5_atm_iv_mom_5d", _median_abs),
        atm_iv_abs_accel_median=("b5_atm_iv_accel", _median_abs),
        iv_reversion_score_median=("b5_iv_reversion_score", "median"),
    )
    stability["contract_iv_cv"] = _safe_cv(stability["contract_iv_std"], stability["contract_iv_median"])
    stability["rv_ref_cv"] = _safe_cv(stability["rv_ref_std"], stability["rv_ref_median"])
    grouped = grouped.join(stability, how="left")
    return grouped.reset_index()


def _aggregate_product_panel(product_panel: pd.DataFrame) -> pd.DataFrame:
    if product_panel is None or product_panel.empty:
        return pd.DataFrame(columns=["product"])
    p = product_panel.copy()
    for column in (
        "b5_empirical_lower_tail_dependence_95",
        "b5_empirical_upper_tail_dependence_95",
        "b5_lower_tail_dependence_excess",
        "b5_upper_tail_dependence_excess",
        "b5_lower_tail_beta",
        "b5_upper_tail_beta",
        "b5_tail_window_days_used",
    ):
        p[column] = _num(p, column)
    p["product_tail_dependence_max"] = p[
        ["b5_empirical_lower_tail_dependence_95", "b5_empirical_upper_tail_dependence_95"]
    ].max(axis=1, skipna=True)
    p["product_tail_dependence_excess_max"] = p[
        ["b5_lower_tail_dependence_excess", "b5_upper_tail_dependence_excess"]
    ].max(axis=1, skipna=True)
    p["product_tail_beta_abs_max"] = p[["b5_lower_tail_beta", "b5_upper_tail_beta"]].abs().max(axis=1, skipna=True)
    out = p.groupby("product", dropna=False).agg(
        product_tail_dependence_max_median=("product_tail_dependence_max", "median"),
        product_tail_dependence_max_p75=("product_tail_dependence_max", _q75),
        product_tail_dependence_excess_max_median=("product_tail_dependence_excess_max", "median"),
        product_tail_dependence_excess_max_p75=("product_tail_dependence_excess_max", _q75),
        product_tail_beta_abs_max_median=("product_tail_beta_abs_max", "median"),
        product_tail_beta_abs_max_p75=("product_tail_beta_abs_max", _q75),
        product_tail_window_days_used_median=("b5_tail_window_days_used", "median"),
    )
    return out.reset_index()


def _aggregate_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=["product"])
    o = outcomes.copy()
    o["exit_date"] = pd.to_datetime(o["exit_date"], errors="coerce")
    for column in (
        "future_net_pnl_per_premium",
        "future_retained_ratio",
        "future_stop_flag",
        "future_stop_loss_per_premium",
        "future_expiry_itm_flag",
        "future_days_held",
        "future_max_price_multiple",
    ):
        o[column] = _num(o, column, 0.0)

    stop_rows = o[o["future_stop_flag"] > 0].copy()
    if not stop_rows.empty:
        cluster = (
            stop_rows.groupby("exit_date")["product"].nunique().rename("same_day_stop_products")
        )
        stop_rows = stop_rows.join(cluster, on="exit_date")
        cluster_by_product = (
            stop_rows.groupby("product")["same_day_stop_products"]
            .mean()
            .rename("avg_stop_cluster_products")
        )
    else:
        cluster_by_product = pd.Series(dtype=float, name="avg_stop_cluster_products")

    g = o.groupby("product", dropna=False)
    out = g.agg(
        outcome_count=("candidate_id", "count"),
        retention_ratio_mean=("future_retained_ratio", "mean"),
        retention_ratio_median=("future_retained_ratio", "median"),
        stop_rate=("future_stop_flag", "mean"),
        stop_loss_per_premium_mean=("future_stop_loss_per_premium", "mean"),
        expiry_itm_rate=("future_expiry_itm_flag", "mean"),
        days_held_median=("future_days_held", "median"),
        max_price_multiple_p95=("future_max_price_multiple", lambda x: x.quantile(0.95)),
        net_pnl_per_premium_mean=("future_net_pnl_per_premium", "mean"),
        net_pnl_per_premium_q05=("future_net_pnl_per_premium", lambda x: x.quantile(0.05)),
    )
    out = out.join(cluster_by_product, how="left")
    out["avg_stop_cluster_products"] = out["avg_stop_cluster_products"].fillna(0.0)
    return out.reset_index()


def _score_products(product_frame: pd.DataFrame, min_signal_days: int) -> pd.DataFrame:
    p = product_frame.copy()
    p = p.replace([np.inf, -np.inf], np.nan)

    p["data_score"] = _weighted_score(
        {
            "signal_days": (_clip100(_rank_high(p["signal_days"])), 0.35),
            "candidate_count": (_clip100(_rank_high(p["candidate_count"])), 0.20),
            "avg_candidates": (_clip100(_rank_high(p["avg_candidates_per_day"])), 0.15),
            "valid_iv": ((100.0 * p["valid_iv_rate"]).clip(0.0, 100.0), 0.20),
            "both_sides": ((100.0 * p["both_side_day_ratio_proxy"]).clip(0.0, 100.0), 0.10),
        }
    )
    p["liquidity_score_pss"] = _weighted_score(
        {
            "volume": (_clip100(_rank_high(p["volume_median"])), 0.22),
            "oi": (_clip100(_rank_high(p["open_interest_median"])), 0.22),
            "liq": (_clip100(_rank_high(p["liquidity_score_median"])), 0.18),
            "friction": (_clip100(_rank_low(p["friction_ratio_median"])), 0.16),
            "tick": (_clip100(_rank_low(p["tick_value_ratio_median"])), 0.12),
            "fee": (_clip100(_rank_low(p["fee_ratio_median"])), 0.10),
        }
    )
    p["premium_pool_score"] = _weighted_score(
        {
            "monthly_pool": (_clip100(_rank_high(p["monthly_premium_pool_median"])), 0.30),
            "net_premium": (_clip100(_rank_high(p["net_premium_cash_median"])), 0.20),
            "premium_margin": (_clip100(_rank_high(p["premium_yield_margin_median"])), 0.20),
            "capital_day": (_clip100(_rank_high(p["premium_per_capital_day_median"])), 0.15),
            "premium_vega": (_clip100(_rank_high(p["premium_per_vega_median"])), 0.15),
        }
    )
    p["carry_score"] = _weighted_score(
        {
            "var_carry": (_clip100(_rank_high(p["variance_carry_median"])), 0.22),
            "var_carry_fwd": (_clip100(_rank_high(p["variance_carry_forward_median"])), 0.18),
            "iv_rv_spread": (_clip100(_rank_high(p["iv_rv_spread_median"])), 0.17),
            "premium_iv10": (_clip100(_rank_high(p["premium_to_iv10_loss_median"])), 0.18),
            "theta_vega": (_clip100(_rank_high(p["theta_per_vega_median"])), 0.15),
            "premium_stress": (_clip100(_rank_high(p["premium_to_stress_loss_median"])), 0.10),
        }
    )
    p["tail_safety_score"] = _weighted_score(
        {
            "tail_cover": (_clip100(_rank_high(p["premium_to_tail_move_loss_median"])), 0.22),
            "mae_cover": (_clip100(_rank_high(p["premium_to_mae20_loss_median"])), 0.18),
            "expected_cover": (_clip100(_rank_high(p["premium_to_expected_move_loss_median"])), 0.15),
            "stress_cover": (_clip100(_rank_high(p["premium_to_stress_loss_median"])), 0.15),
            "gamma": (_clip100(_rank_low(p["gamma_rent_penalty_median"])), 0.12),
            "vomma": (_clip100(_rank_low(p["vomma_loss_ratio_median"])), 0.08),
            "range": (_clip100(_rank_low(p["range_expansion_median"])), 0.06),
            "cooldown": (_clip100(_rank_low(p["cooldown_penalty_median"])), 0.04),
        }
    )
    tail_dependence_p75 = p.get(
        "product_tail_dependence_max_p75",
        pd.Series(np.nan, index=p.index),
    )
    tail_beta_p75 = p.get(
        "product_tail_beta_abs_max_p75",
        pd.Series(np.nan, index=p.index),
    )
    p["stability_score"] = _weighted_score(
        {
            "tail_move": (_clip100(_rank_low(p["tail_move_pct_p75"])), 0.16),
            "mae20": (_clip100(_rank_low(p["mae20_move_pct_p75"])), 0.10),
            "expected_move": (_clip100(_rank_low(p["expected_move_pct_p75"])), 0.08),
            "range_p75": (_clip100(_rank_low(p["range_expansion_p75"])), 0.10),
            "range_std": (_clip100(_rank_low(p["range_expansion_std"])), 0.06),
            "iv_cv": (_clip100(_rank_low(p["contract_iv_cv"])), 0.12),
            "rv_cv": (_clip100(_rank_low(p["rv_ref_cv"])), 0.10),
            "carry_std": (_clip100(_rank_low(p["variance_carry_forward_std"])), 0.10),
            "iv_mom": (_clip100(_rank_low(p["atm_iv_abs_mom5_median"])), 0.06),
            "vov": (_clip100(_rank_low(p["vol_of_vol_p75"])), 0.06),
            "tail_dep": (_clip100(_rank_low(tail_dependence_p75)), 0.04),
            "tail_beta": (_clip100(_rank_low(tail_beta_p75)), 0.02),
        }
    )
    p["ex_ante_score"] = _weighted_score(
        {
            "data": (p["data_score"], 0.12),
            "liquidity": (p["liquidity_score_pss"], 0.18),
            "premium": (p["premium_pool_score"], 0.18),
            "carry": (p["carry_score"], 0.17),
            "tail": (p["tail_safety_score"], 0.20),
            "stability": (p["stability_score"], 0.15),
        }
    )

    if "retention_ratio_mean" in p.columns:
        p["outcome_validation_score"] = _weighted_score(
            {
                "retention": (_clip100(_rank_high(p["retention_ratio_mean"])), 0.25),
                "net_pnl": (_clip100(_rank_high(p["net_pnl_per_premium_mean"])), 0.20),
                "left_tail": (_clip100(_rank_high(p["net_pnl_per_premium_q05"])), 0.15),
                "stop": (_clip100(_rank_low(p["stop_rate"])), 0.18),
                "stop_loss": (_clip100(_rank_low(p["stop_loss_per_premium_mean"])), 0.10),
                "itm": (_clip100(_rank_low(p["expiry_itm_rate"])), 0.07),
                "cluster": (_clip100(_rank_low(p["avg_stop_cluster_products"])), 0.05),
            }
        )
    else:
        p["outcome_validation_score"] = np.nan

    p["insufficient_history_flag"] = p["signal_days"] < min_signal_days
    p["severe_history_flag"] = p["signal_days"] < max(40, int(min_signal_days * 0.5))
    p["low_iv_quality_flag"] = p["valid_iv_rate"] < 0.50
    p["low_liquidity_flag"] = p["liquidity_score_pss"] < 30.0
    p["low_premium_pool_flag"] = p["premium_pool_score"] < 30.0
    p["tail_fragile_flag"] = p["tail_safety_score"] < 30.0
    p["unstable_vol_tail_flag"] = p["stability_score"] < 30.0
    p["microstructure_bad_flag"] = (
        (p["low_price_rate"].fillna(0.0) > 0.80)
        | (p["friction_ratio_p75"].fillna(0.0) > 0.30)
        | (p["tick_value_ratio_median"].fillna(0.0) > 0.25)
    )
    cluster_cut = p["avg_stop_cluster_products"].quantile(0.80) if "avg_stop_cluster_products" in p.columns else np.inf
    p["stop_cluster_bad_flag"] = p.get(
        "avg_stop_cluster_products",
        pd.Series(0.0, index=p.index),
    ).fillna(0.0) > cluster_cut
    p["validation_bad_flag"] = (
        (p.get("stop_rate", pd.Series(0.0, index=p.index)).fillna(0.0) > 0.45)
        | (p.get("retention_ratio_mean", pd.Series(1.0, index=p.index)).fillna(1.0) < -0.75)
        | (p.get("outcome_validation_score", pd.Series(50.0, index=p.index)).fillna(50.0) < 35.0)
        | (
            p["stop_cluster_bad_flag"]
            & (p.get("stop_rate", pd.Series(0.0, index=p.index)).fillna(0.0) > 0.30)
        )
    )

    def base_tier(row: pd.Series) -> str:
        if row["severe_history_flag"]:
            return "Exclude"
        if row["insufficient_history_flag"] or row["low_iv_quality_flag"]:
            return "Observe"
        if row["low_liquidity_flag"] or row["microstructure_bad_flag"]:
            return "Exclude"
        if row["tail_fragile_flag"] and row["unstable_vol_tail_flag"]:
            return "Exclude"
        if row["tail_fragile_flag"]:
            return "Observe"
        if row["unstable_vol_tail_flag"]:
            return "Observe"
        score = row["ex_ante_score"]
        if score >= 70:
            return "Core"
        if score >= 55:
            return "Conditional"
        if score >= 40:
            return "Observe"
        return "Exclude"

    def recommended_tier(row: pd.Series) -> str:
        tier = base_tier(row)
        if row["validation_bad_flag"] and tier == "Core":
            return "Conditional"
        if row["validation_bad_flag"] and tier == "Conditional":
            return "Observe"
        return tier

    p["base_tier_ex_ante"] = p.apply(base_tier, axis=1)
    p["recommended_tier"] = p.apply(recommended_tier, axis=1)
    p["main_reason"] = p.apply(_reason_text, axis=1)
    return p.sort_values(
        ["recommended_tier", "ex_ante_score", "outcome_validation_score"],
        ascending=[True, False, False],
    )


def _reason_text(row: pd.Series) -> str:
    reasons = []
    if row.get("severe_history_flag", False):
        reasons.append("history_too_short")
    elif row.get("insufficient_history_flag", False):
        reasons.append("short_history")
    if row.get("low_iv_quality_flag", False):
        reasons.append("weak_iv_quality")
    if row.get("low_liquidity_flag", False):
        reasons.append("weak_liquidity")
    if row.get("low_premium_pool_flag", False):
        reasons.append("thin_premium_pool")
    if row.get("tail_fragile_flag", False):
        reasons.append("tail_fragile")
    if row.get("unstable_vol_tail_flag", False):
        reasons.append("unstable_vol_tail")
    if row.get("microstructure_bad_flag", False):
        reasons.append("microstructure_bad")
    if row.get("validation_bad_flag", False):
        reasons.append("bad_shadow_validation")
    return ",".join(reasons) if reasons else "balanced"


def _write_report(scored: pd.DataFrame, output_path: Path, tag: str, filter_description: str) -> None:
    def markdown_table(frame: pd.DataFrame, floatfmt: str = ".3f") -> str:
        if frame.empty:
            return "_No rows._"
        safe = frame.copy()
        for column in safe.columns:
            if pd.api.types.is_float_dtype(safe[column]):
                safe[column] = safe[column].map(
                    lambda x: "" if pd.isna(x) else format(float(x), floatfmt)
                )
            else:
                safe[column] = safe[column].map(lambda x: "" if pd.isna(x) else str(x))
        headers = list(safe.columns)
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        for _, row in safe.iterrows():
            lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")
        return "\n".join(lines)

    tier_order = ["Core", "Conditional", "Observe", "Exclude"]
    lines = []
    lines.append(f"# S1 Product Suitability Screen - {tag}")
    lines.append("")
    lines.append("This screen uses ex-ante candidate factors as the primary suitability score.")
    lines.append("Shadow outcomes are reported as validation diagnostics, not as the only blacklist rule.")
    lines.append("")
    lines.append(f"Target contract band: `{filter_description}`")
    lines.append("")
    lines.append("## Tier Counts")
    counts = scored["recommended_tier"].value_counts().reindex(tier_order).fillna(0).astype(int)
    lines.append(markdown_table(counts.to_frame("count").reset_index(names="tier")))
    lines.append("")
    lines.append("## Core / Conditional Products")
    cols = [
        "product",
        "recommended_tier",
        "ex_ante_score",
        "data_score",
        "liquidity_score_pss",
        "premium_pool_score",
        "carry_score",
        "tail_safety_score",
        "stability_score",
        "outcome_validation_score",
        "signal_days",
        "monthly_premium_pool_median",
        "tail_move_pct_p75",
        "contract_iv_cv",
        "rv_ref_cv",
        "variance_carry_forward_std",
        "stop_rate",
        "retention_ratio_mean",
        "main_reason",
    ]
    good = scored[scored["recommended_tier"].isin(["Core", "Conditional"])][cols].copy()
    lines.append(markdown_table(good, floatfmt=".3f"))
    lines.append("")
    lines.append("## Observe / Exclude Products")
    weak = scored[scored["recommended_tier"].isin(["Observe", "Exclude"])][cols].copy()
    lines.append(markdown_table(weak, floatfmt=".3f"))
    lines.append("")
    lines.append("## Scoring Logic")
    lines.append("- `data_score`: history length, candidate count, valid IV rate, two-sided availability.")
    lines.append("- `liquidity_score_pss`: volume, OI, liquidity score, friction, tick/price, fee ratio.")
    lines.append("- `premium_pool_score`: monthly available premium pool, premium/margin, premium/vega and capital-day efficiency.")
    lines.append("- `carry_score`: IV/RV and variance-carry style compensation plus theta/vega and premium/stress.")
    lines.append("- `tail_safety_score`: premium coverage of tail/MAE/stress, gamma/vomma/range/cooldown fragility.")
    lines.append("- `stability_score`: lower realized tail move, MAE, range expansion, IV/RV dispersion, vol-of-vol and tail-dependence risk.")
    lines.append("- `outcome_validation_score`: retained premium, stop rate, left-tail labels and stop clustering.")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
    parser.add_argument("--product-panel", default=DEFAULT_PRODUCT_PANEL)
    parser.add_argument("--output-dir", default="output/product_suitability")
    parser.add_argument("--tag", default="s1_b5_full_shadow_v1_2022_latest")
    parser.add_argument("--min-signal-days", type=int, default=120)
    parser.add_argument("--min-abs-delta", type=float, default=None)
    parser.add_argument("--max-abs-delta", type=float, default=None)
    parser.add_argument("--min-option-price", type=float, default=None)
    parser.add_argument("--max-option-price", type=float, default=None)
    parser.add_argument("--max-rank-in-side", type=float, default=None)
    parser.add_argument("--min-dte", type=float, default=None)
    parser.add_argument("--max-dte", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidate_path = Path(args.candidate)
    outcomes_path = Path(args.outcomes)
    product_panel_path = Path(args.product_panel) if args.product_panel else Path(
        f"output/s1_b5_product_panel_{args.tag}.csv"
    )
    if not candidate_path.exists():
        raise FileNotFoundError(f"candidate file not found: {candidate_path}")

    candidates = pd.read_csv(candidate_path, low_memory=False)
    candidates, filter_description = _filter_target_contract_band(candidates, args)
    if candidates.empty:
        raise ValueError(f"no candidates left after target contract filter: {filter_description}")
    outcomes = pd.read_csv(outcomes_path, low_memory=False) if outcomes_path.exists() else pd.DataFrame()
    if not outcomes.empty and "candidate_id" in outcomes.columns and "candidate_id" in candidates.columns:
        outcomes = outcomes[outcomes["candidate_id"].isin(candidates["candidate_id"])].copy()
    product_candidates = _aggregate_candidates(candidates)
    product_outcomes = _aggregate_outcomes(outcomes)
    merged = product_candidates.merge(product_outcomes, on="product", how="left")
    if product_panel_path.exists():
        product_panel = pd.read_csv(product_panel_path, low_memory=False)
        product_panel_agg = _aggregate_product_panel(product_panel)
        merged = merged.merge(product_panel_agg, on="product", how="left")
    scored = _score_products(merged, min_signal_days=args.min_signal_days)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"product_suitability_{args.tag}.csv"
    md_path = out_dir / f"product_suitability_{args.tag}.md"
    scored.to_csv(csv_path, index=False, encoding="utf-8-sig")
    _write_report(scored, md_path, args.tag, filter_description)

    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")
    print(f"target_filter: {filter_description}")
    print(f"filtered_candidates: {len(candidates)}")
    print(scored["recommended_tier"].value_counts().to_string())
    show_cols = [
        "product",
        "recommended_tier",
        "ex_ante_score",
        "data_score",
        "liquidity_score_pss",
        "premium_pool_score",
        "carry_score",
        "tail_safety_score",
        "stability_score",
        "outcome_validation_score",
        "main_reason",
    ]
    print(scored[show_cols].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
