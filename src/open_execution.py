"""Helpers for deferred open execution in the minute backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping

import numpy as np


@dataclass(frozen=True)
class OpenExecutionContext:
    """Precomputed minute-bar lookups for pending open execution."""

    day_volume: Dict[str, float]
    bars_by_code: Dict[str, Any]


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def build_open_execution_context(minute_df: Any) -> OpenExecutionContext:
    if minute_df is None or minute_df.empty:
        return OpenExecutionContext(day_volume={}, bars_by_code={})
    vol_agg = minute_df.groupby("ths_code")["volume"].sum()
    bars_by_code = {
        code: grp.sort_values("time")
        for code, grp in minute_df.groupby("ths_code", sort=False)
    }
    return OpenExecutionContext(day_volume=vol_agg.to_dict(), bars_by_code=bars_by_code)


def estimate_volume_weighted_close(code_bars: Any) -> float:
    """Use executable minute bars to estimate the full-day open fill price."""

    if code_bars is None or code_bars.empty:
        return 0.0
    valid = code_bars[code_bars["volume"] > 0]
    if valid.empty:
        return 0.0
    prices = valid["close"].values.astype(float)
    volumes = valid["volume"].values.astype(float)
    total_vol = volumes.sum()
    return float(np.sum(prices * volumes) / total_vol) if total_vol > 0 else float(prices[-1])


def split_open_quantity(target_n: int, today_volume: float, volume_limit_pct: float) -> tuple[int, int]:
    if today_volume > 0:
        max_today = max(1, int(today_volume * volume_limit_pct))
    else:
        max_today = target_n
    actual_n = min(int(target_n), int(max_today))
    return actual_n, int(target_n) - actual_n


def scale_deferred_open_item(item: Mapping[str, Any], remaining_n: int) -> Dict[str, Any]:
    deferred_item = dict(item)
    deferred_item["n"] = int(remaining_n)
    original_n = float(item.get("n", 0) or 0)
    scale = float(remaining_n) / original_n if original_n > 0 else 0.0
    one_loss = float(deferred_item.get("one_contract_stress_loss", 0.0) or 0.0)
    if one_loss > 0:
        deferred_item["stress_loss"] = one_loss * remaining_n
    for key in ("cash_vega", "cash_gamma", "margin"):
        if key in deferred_item:
            deferred_item[key] = float(deferred_item.get(key, 0.0) or 0.0) * scale
    return deferred_item


OPEN_ITEM_AUDIT_FIELDS = (
    "signal_premium_stress",
    "signal_theta_stress",
    "signal_premium_margin",
    "premium_yield_margin",
    "premium_yield_notional",
    "rv_ref",
    "iv_rv_spread_candidate",
    "iv_rv_ratio_candidate",
    "variance_carry",
    "breakeven_price",
    "breakeven_cushion_abs",
    "breakeven_cushion_iv",
    "breakeven_cushion_rv",
    "iv_shock_loss_5_cash",
    "iv_shock_loss_10_cash",
    "premium_to_iv5_loss",
    "premium_to_iv10_loss",
    "premium_to_stress_loss",
    "theta_vega_efficiency",
    "gamma_rent_cash",
    "gamma_rent_penalty",
    "fee_ratio",
    "slippage_ratio",
    "friction_ratio",
    "premium_quality_score",
    "premium_quality_rank_in_side",
    "iv_rv_carry_score",
    "breakeven_cushion_score",
    "premium_to_iv_shock_score",
    "premium_to_stress_loss_score",
    "theta_vega_efficiency_score",
    "cost_liquidity_score",
    "b3_forward_variance_pressure",
    "b3_vol_of_vol_proxy",
    "b3_vov_trend",
    "b3_iv_shock_coverage",
    "b3_joint_stress_coverage",
    "b3_vomma_cash",
    "b3_vomma_loss_ratio",
    "b3_skew_steepening",
    "b3_clean_vega_score",
    "b3_forward_variance_score",
    "b3_vol_of_vol_score",
    "b3_iv_shock_score",
    "b3_joint_stress_score",
    "b3_vomma_score",
    "b3_skew_stability_score",
    "b4_contract_score",
    "b4_product_side_score",
    "b4_premium_to_iv10_score",
    "b4_premium_to_stress_score",
    "b4_premium_yield_margin_score",
    "b4_gamma_rent_score",
    "b4_vomma_score",
    "b4_breakeven_cushion_score",
    "b4_vol_of_vol_score",
    "abs_delta",
    "delta",
    "gamma",
    "vega",
    "theta",
    "volume",
    "open_interest",
    "moneyness",
    "liquidity_score",
    "vol_regime",
    "selection_score",
    "selection_rank",
    "entry_atm_iv",
    "entry_iv_pct",
    "entry_iv_trend",
    "entry_rv_trend",
    "entry_iv_rv_spread",
    "entry_iv_rv_ratio",
    "contract_iv",
    "contract_iv_change_1d",
    "contract_iv_change_3d",
    "contract_iv_change_5d",
    "contract_iv_change_for_vega",
    "contract_iv_skew_to_atm",
    "contract_skew_change_for_vega",
    "contract_price_change_1d",
    "effective_margin_cap",
    "effective_strategy_margin_cap",
    "effective_product_margin_cap",
    "effective_product_side_margin_cap",
    "effective_bucket_margin_cap",
    "effective_corr_group_margin_cap",
    "effective_stress_loss_cap",
    "effective_bucket_stress_loss_cap",
    "effective_product_side_stress_loss_cap",
    "effective_corr_group_stress_loss_cap",
    "effective_contract_stress_loss_cap",
    "open_budget_risk_scale",
    "open_budget_brake_reason",
    "trend_state",
    "trend_score",
    "trend_confidence",
    "trend_range_position",
    "trend_range_pressure",
    "trend_role",
    "side_score_mult",
    "side_budget_mult",
    "side_delta_cap",
    "ladder_candidate_count",
    "ladder_delta_gap",
    "effective_s1_stress_max_qty",
    "b2_product_score",
    "b2_product_equal_budget_pct",
    "b2_product_quality_budget_pct",
    "b2_product_final_budget_pct",
    "b2_product_budget_mult",
    "b3_product_side_score",
    "b3_side_equal_budget_pct",
    "b3_side_quality_budget_pct",
    "b3_side_final_budget_pct",
    "b3_side_budget_mult",
    "b3_clean_vega_tilt_strength",
    "b4_side_equal_budget_pct",
    "b4_side_quality_budget_pct",
    "b4_side_final_budget_pct",
    "b4_side_budget_mult",
    "b4_product_tilt_strength",
    "b4_side_vov_penalty_mult",
)

OPEN_ITEM_TEXT_FIELDS = frozenset({
    "vol_regime",
    "open_budget_brake_reason",
    "trend_state",
    "trend_range_pressure",
    "trend_role",
})

OPEN_ITEM_FIELD_ALIASES = {
    "signal_premium_stress": "premium_stress",
    "signal_theta_stress": "theta_stress",
    "signal_premium_margin": "premium_margin",
}


def build_open_order_record(
    *,
    date_str: str,
    item: Mapping[str, Any],
    code: str,
    actual_n: int,
    price: float,
    raw_execution_price: float,
    execution_slippage: float,
    slippage_cash: float,
    open_fee: float,
    open_fee_per_contract: float,
    close_fee_per_contract: float,
    roundtrip_fee_per_contract: float,
    pos: Any,
    open_margin: float,
    gross_premium_cash: float,
    net_premium_cash: float,
) -> Dict[str, Any]:
    """Build the open-order audit row without mutating engine state."""

    ref_price = safe_float(item.get("ref_price", np.nan), np.nan)
    price_drift = price / ref_price - 1.0 if np.isfinite(ref_price) and ref_price > 0 else np.nan
    theta = safe_float(item.get("theta", np.nan), np.nan)
    theta_cash = abs(theta) * float(item["mult"]) * float(actual_n) if np.isfinite(theta) else np.nan
    stress_loss = float(pos.stress_loss or 0.0)
    exec_premium_stress = (
        net_premium_cash / stress_loss
        if item["role"] == "sell" and stress_loss > 0
        else np.nan
    )
    exec_theta_stress = (
        theta_cash / stress_loss
        if item["role"] == "sell" and stress_loss > 0 and np.isfinite(theta_cash)
        else np.nan
    )
    exec_premium_margin = (
        net_premium_cash / open_margin
        if item["role"] == "sell" and open_margin > 0
        else np.nan
    )
    record = {
        "date": date_str, "signal_date": item.get("signal_date", ""),
        "action": f"open_{item['role']}",
        "strategy": item["strat"], "product": item["product"],
        "code": code, "option_type": item["opt_type"],
        "strike": item["strike"], "expiry": str(item["expiry"])[:10],
        "price": round(price, 4), "quantity": actual_n,
        "raw_execution_price": round(raw_execution_price, 4),
        "execution_slippage": round(execution_slippage, 6),
        "execution_slippage_cash": round(slippage_cash, 2),
        "fee": round(open_fee, 2),
        "fee_per_contract": open_fee_per_contract,
        "fee_action": "open",
        "open_fee_per_contract": open_fee_per_contract,
        "close_fee_per_contract": close_fee_per_contract,
        "roundtrip_fee_per_contract": roundtrip_fee_per_contract,
        "pnl": 0,
        "stress_loss": round(pos.stress_loss, 2),
        "open_margin": round(open_margin, 2),
        "one_contract_margin": item.get("one_contract_margin", np.nan),
        "gross_premium_cash": round(gross_premium_cash, 2),
        "net_premium_cash": round(net_premium_cash, 2),
        "signal_ref_price": ref_price,
        "execution_price_drift": price_drift,
        "premium_stress": exec_premium_stress,
        "theta_stress": exec_theta_stress,
        "premium_margin": exec_premium_margin,
    }
    for field in OPEN_ITEM_AUDIT_FIELDS:
        default = "" if field in OPEN_ITEM_TEXT_FIELDS else np.nan
        record[field] = item.get(OPEN_ITEM_FIELD_ALIASES.get(field, field), default)
    return record
