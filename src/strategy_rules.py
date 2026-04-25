"""
策略规则模块：从 unified_engine_v3.py 提取的纯策略逻辑

所有函数均为无状态函数，不依赖全局变量或外部状态。
可被 daily_backtest.py 和 order_generator.py 共同调用。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore
from margin_model import estimate_margin, resolve_margin_ratio

# ── 默认参数 ──────────────────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    "capital": 10_000_000,
    "daily_agg_batch_size": 10,
    "iv_warmup_retry_skipped_products": False,
    "margin_per": 0.02,
    "margin_cap": 0.50,
    "s1_margin_cap": 0.25,
    "s3_margin_cap": 0.25,
    "margin_ratio_by_exchange": {
        "SSE": 0.12, "SZSE": 0.12, "CFFEX": 0.10,
        "SHFE": 0.07, "INE": 0.07, "DCE": 0.07,
        "CZCE": 0.07, "GFEX": 0.07,
    },
    "margin_ratio_by_product": {},
    "margin_ratio_use_broker_table": True,
    "equity_option_min_guarantee_ratio": 0.07,
    "enable_s1": True,
    "enable_s3": True,
    "enable_s4": True,
    "s1_tp": 0.50,
    "s3_tp": 0.50,
    "s3_ratio_candidates": [2, 3],       # S3买卖比例候选（优先小比例）
    "s3_buy_otm_pct": 5.0,               # S3买腿目标OTM%
    "s3_sell_otm_pct": 10.0,             # S3卖腿目标OTM%
    "s3_protect_otm_pct": 15.0,          # S3保护腿目标OTM%
    "s3_buy_otm_range": (3.0, 7.0),      # S3买腿OTM%筛选范围
    "s3_sell_otm_range": (7.0, 13.0),    # S3卖腿OTM%筛选范围
    "s3_protect_otm_range": (12.0, 20.0),# S3保护腿OTM%筛选范围
    "s3_net_premium_tolerance": 0.3,     # 零成本容忍度（允许亏损买腿成本的30%）
    "s3_protect_trigger_otm_pct": 5.0,   # 应急保护触发阈值（卖腿OTM%降至此值以下）
    "s4_prem": 0.005,
    "s4_max_hands": 5,
    "s4_max_hold": 30,  # S4 fallback持仓天数（主要用DTE<10退出）
    "iv_inverse": True,
    "iv_window": 252,
    "iv_min_periods": 60,
    "iv_threshold": 75,
    "iv_open_threshold": 80,
    "dte_target": 35,
    "dte_min": 30,
    "dte_max": 45,
    "tp_min_dte": 5,
    "reopen_min_dte": 10,
    "expiry_dte": 1,
    "fee": 3,
    "option_fee_use_broker_table": True,
    "option_fee_by_product": {},
    "option_fee_by_product_side": {},
    "execution_slippage_enabled": False,
    "execution_slippage_pct": 0.002,
    "execution_open_slippage_pct": None,
    "execution_close_slippage_pct": None,
    "execution_stop_slippage_pct": 0.005,
    "execution_slippage_min_abs": 0.0,
    "execution_slippage_apply_to_expiry": False,
    "vwap_window": 10,
    "hedge_enabled": True,
    "hedge_scope": "family_net",
    "hedge_rebalance": "daily_t1_vwap",
    "hedge_target_cash_delta_pct": 0.0,
    "hedge_rounding": "min_abs_residual",
    "hedge_cost_mode": "none",
    "greeks_vega_warn": 0.008,
    "greeks_vega_hard": 0.01,
    "greeks_delta_hard": 0.10,
    "greeks_delta_target": 0.07,
    "greeks_vega_target": 0.007,
    "greeks_exit_enabled": False,
    "initialize_open_greeks_for_attribution": True,
    "intraday_refresh_spot_greeks_for_attribution": True,
    "intraday_greeks_refresh_interval": 15,
    "intraday_stop_liquidity_filter_enabled": True,
    "intraday_stop_min_trade_volume": 3,
    "intraday_stop_min_group_volume_ratio": 0.10,
    "intraday_stop_confirmation_enabled": True,
    "intraday_stop_confirmation_observations": 2,
    "intraday_stop_confirmation_use_full_minutes": True,
    "intraday_stop_confirmation_revert_ratio": 0.98,
    "intraday_stop_confirmation_max_minutes": 30,
    "intraday_stop_confirmation_use_cumulative_volume": True,
    "take_profit_enabled": False,
    "premium_stop_multiple": 2.50,
    "premium_stop_requires_daily_iv_non_decrease": True,
    "cooldown_days_after_stop": 1,
    "cooldown_repeat_lookback_days": 20,
    "cooldown_repeat_extra_days": 2,
    "cooldown_repeat_threshold": 2,
    "pre_expiry_exit_dte": 2,
    "s1_sell_delta_cap": 0.10,
    "s1_sell_delta_floor": 0.00,
    "s1_target_abs_delta": 0.07,
    "s1_min_volume": 50,
    "s1_min_oi": 200,
    "s1_carry_metric": "premium_margin",
    "s1_ranking_mode": "target_delta",
    "s1_score_premium_stress_weight": 0.55,
    "s1_score_theta_stress_weight": 0.25,
    "s1_score_premium_margin_weight": 0.15,
    "s1_score_liquidity_weight": 0.05,
    "s1_score_delta_weight": 0.00,
    "s1_falling_framework_enabled": False,
    "s1_entry_check_vol_trend": True,
    "s1_entry_block_high_rising_regime": True,
    "s1_prioritize_products_by_regime": True,
    "s1_entry_max_iv_trend": 0.005,
    "s1_entry_max_rv_trend": 0.02,
    "s1_require_risk_release_entry": False,
    "s1_risk_release_max_iv_trend": -0.005,
    "s1_risk_release_require_falling_regime": False,
    "s1_risk_release_require_daily_iv_drop": True,
    "s1_reentry_require_falling_regime": True,
    "s1_reentry_require_daily_iv_drop": False,
    "s1_risk_release_min_iv_rv_spread": 0.02,
    "s1_risk_release_min_iv_rv_ratio": 1.10,
    "s1_risk_release_max_rv_trend": 0.00,
    "s1_risk_release_require_rv_trend": True,
    "s1_risk_release_min_iv_pct": 20,
    "s1_risk_release_max_iv_pct": 90,
    "s1_risk_release_allow_structural_low": False,
    "s1_track_contract_iv_trend": True,
    "s1_require_contract_iv_not_rising": False,
    "s1_contract_iv_max_change_1d": 0.0,
    "s1_contract_iv_missing_policy": "skip",
    "s1_require_contract_price_not_rising": False,
    "s1_contract_price_max_change_1d": 0.10,
    "s1_forward_vega_filter_enabled": False,
    "s1_forward_vega_missing_policy": "skip",
    "s1_forward_vega_candidate_multiplier": 8,
    "s1_forward_vega_contract_iv_lookback": 5,
    "s1_forward_vega_require_contract_iv_falling": True,
    "s1_forward_vega_contract_iv_max_change": 0.0,
    "s1_forward_vega_require_atm_iv_not_rising": True,
    "s1_forward_vega_atm_iv_max_trend": 0.0,
    "s1_forward_vega_require_rv_not_rising": True,
    "s1_forward_vega_rv_max_trend": 0.01,
    "s1_forward_vega_require_skew_not_steepening": True,
    "s1_forward_vega_max_skew_steepen": 0.005,
    "s1_forward_vega_require_contract_price_not_rising": False,
    "s1_forward_vega_contract_price_max_change": 0.10,
    "s1_forward_vega_block_structural_low_breakout": True,
    "s1_forward_vega_structural_low_max_rv_trend": 0.0,
    "s1_forward_vega_structural_low_block_pressure": True,
    "s1_forward_vega_structural_low_min_trend_confidence": 0.35,
    "s1_use_stress_score": False,
    "s1_min_premium_fee_multiple": 2.0,
    "s1_stress_spot_move_pct": 0.03,
    "s1_stress_iv_up_points": 5.0,
    "s1_use_stress_sizing": False,
    "s1_stress_loss_budget_pct": 0.0010,
    "s1_stress_min_qty": 1,
    "s1_stress_max_qty": 50,
    "s1_product_regime_budget_overrides_enabled": False,
    "s1_product_regime_budget_override_prefixes": ["falling"],
    "s1_gamma_penalty": 0.0,
    "s1_vega_penalty": 0.0,
    "s1_falling_vol_margin_per_mult": 1.50,
    "s1_protect_enabled": True,
    "s1_protect_ratio": 0.5,
    "s1_protect_mode": "inner",
    "s1_protect_max_abs_delta": 0.25,
    "s1_protect_min_price": 0.5,
    "s1_protect_premium_ratio_cap": None,
    "s1_reentry_delta_cap": 0.15,
    "s1_reentry_delta_step": 0.02,
    "s1_allow_add_same_side": True,
    "s1_allow_add_same_expiry": True,
    "s1_split_across_neighbor_contracts": True,
    "s1_neighbor_contract_count": 3,
    "s1_neighbor_max_delta_gap": 0.025,
    "s1_side_selection_enabled": False,
    "s1_conditional_strangle_enabled": False,
    "s1_conditional_strangle_allowed_regimes": [
        "falling_vol_carry",
        "low_stable_vol",
    ],
    "s1_side_momentum_lookback": 5,
    "s1_side_momentum_threshold": 0.02,
    "s1_side_momentum_penalty": 0.75,
    "s1_conditional_strangle_max_abs_momentum": 0.015,
    "s1_conditional_strangle_min_score_ratio": 0.90,
    "s1_conditional_strangle_min_adjusted_score": 0.35,
    "s1_conditional_strangle_require_momentum": True,
    "s1_trend_confidence_enabled": False,
    "s1_trend_short_lookback": 5,
    "s1_trend_medium_lookback": 10,
    "s1_trend_long_lookback": 20,
    "s1_trend_min_history": 10,
    "s1_trend_threshold": 0.018,
    "s1_trend_range_threshold": 0.010,
    "s1_trend_rv_rising_threshold": 0.015,
    "s1_trend_allow_weak_side": True,
    "s1_trend_weak_side_delta_cap": 0.060,
    "s1_trend_weak_side_score_mult": 0.60,
    "s1_trend_weak_side_budget_mult": 0.50,
    "s1_trend_weak_side_min_score_ratio": 0.75,
    "s1_trend_strong_side_score_mult": 1.00,
    "s1_trend_strangle_states": ["range_bound"],
    "s3_reentry_otm_shift": 2.0,
    # 品种准入
    "product_min_listing_days": 180,
    "product_min_daily_oi": 500,
    "product_observation_months": 3,
    "daily_scan_top_n": 0,
    "portfolio_construction_enabled": True,
    "portfolio_product_margin_cap": 0.08,
    "portfolio_product_side_margin_cap": 0.0,
    "portfolio_product_side_stress_loss_cap": 0.0,
    "portfolio_bucket_control_enabled": True,
    "portfolio_bucket_max_active_products": 3,
    "portfolio_bucket_margin_cap": 0.18,
    "portfolio_corr_control_enabled": True,
    "portfolio_corr_group_max_active_products": 2,
    "portfolio_corr_group_margin_cap": 0.0,
    "portfolio_corr_group_stress_loss_cap": 0.0,
    "portfolio_contract_lot_cap": 0,
    "portfolio_contract_stress_loss_cap": 0.0,
    "portfolio_dynamic_corr_control_enabled": True,
    "portfolio_corr_window": 60,
    "portfolio_corr_min_periods": 20,
    "portfolio_corr_threshold": 0.70,
    "portfolio_corr_max_high_corr_peers": 1,
    "portfolio_bucket_round_robin": True,
    "portfolio_diagnostics_enabled": True,
    "vol_regime_sizing_enabled": False,
    "vol_regime_low_iv_pct": 45,
    "vol_regime_high_iv_pct": 75,
    "vol_regime_min_iv_rv_spread": 0.02,
    "vol_regime_min_iv_rv_ratio": 1.10,
    "vol_regime_max_low_rv_trend": 0.02,
    "vol_regime_max_low_iv_trend": 0.00,
    "vol_regime_high_rv_trend": 0.05,
    "vol_regime_high_iv_trend": 0.03,
    "vol_regime_falling_iv_pct_min": 25,
    "vol_regime_falling_iv_pct_max": 95,
    "vol_regime_falling_iv_trend": -0.01,
    "vol_regime_falling_rv_trend_max": 0.01,
    "vol_regime_falling_margin_cap": 0.60,
    "vol_regime_falling_s1_margin_cap": 0.40,
    "vol_regime_falling_s3_margin_cap": 0.25,
    "vol_regime_low_margin_cap": 0.60,
    "vol_regime_normal_margin_cap": 0.50,
    "vol_regime_high_margin_cap": 0.22,
    "vol_regime_low_s1_margin_cap": 0.35,
    "vol_regime_normal_s1_margin_cap": 0.25,
    "vol_regime_high_s1_margin_cap": 0.10,
    "vol_regime_low_s3_margin_cap": 0.30,
    "vol_regime_normal_s3_margin_cap": 0.25,
    "vol_regime_high_s3_margin_cap": 0.10,
    "vol_regime_falling_product_margin_cap": 0.10,
    "vol_regime_low_product_margin_cap": 0.09,
    "vol_regime_normal_product_margin_cap": 0.08,
    "vol_regime_high_product_margin_cap": 0.05,
    "vol_regime_falling_bucket_margin_cap": 0.24,
    "vol_regime_low_bucket_margin_cap": 0.20,
    "vol_regime_normal_bucket_margin_cap": 0.18,
    "vol_regime_high_bucket_margin_cap": 0.10,
    "vol_regime_falling_stress_loss_cap": 0.022,
    "vol_regime_low_stress_loss_cap": 0.018,
    "vol_regime_normal_stress_loss_cap": 0.015,
    "vol_regime_high_stress_loss_cap": 0.008,
    "vol_regime_falling_bucket_stress_loss_cap": 0.006,
    "vol_regime_low_bucket_stress_loss_cap": 0.005,
    "vol_regime_normal_bucket_stress_loss_cap": 0.0045,
    "vol_regime_high_bucket_stress_loss_cap": 0.0025,
    "vol_regime_falling_s1_stress_loss_budget_pct": 0.0018,
    "vol_regime_low_s1_stress_loss_budget_pct": 0.0015,
    "vol_regime_normal_s1_stress_loss_budget_pct": 0.0012,
    "vol_regime_high_s1_stress_loss_budget_pct": 0.0006,
    "vol_regime_low_margin_per_mult": 1.12,
    "vol_regime_normal_margin_per_mult": 1.00,
    "vol_regime_high_margin_per_mult": 0.30,
    "vol_regime_post_stop_margin_per_mult": 0.00,
    "vol_regime_count_post_stop_as_high": False,
    "vol_regime_portfolio_high_ratio": 0.25,
    "vol_regime_portfolio_low_ratio": 0.50,
    "vol_regime_portfolio_falling_ratio": 0.25,
    "vol_regime_portfolio_falling_release_enabled": False,
    "vol_regime_portfolio_falling_release_ratio": 0.30,
    "vol_regime_portfolio_falling_release_high_ratio_max": 0.35,
    "vol_regime_portfolio_falling_release_min_products": 1,
    "vol_regime_allow_low_iv_rich": False,
    "low_iv_structural_auto_enabled": False,
    "low_iv_structural_min_history": 120,
    "low_iv_structural_max_current_iv_pct": None,
    "low_iv_structural_max_median_iv": 0.24,
    "low_iv_structural_max_iv_std": 0.08,
    "low_iv_structural_margin_per_mult": 1.25,
    "low_iv_structural_require_low_stable": True,
    "low_iv_structural_caution_enabled": False,
    "low_iv_structural_s1_stress_budget_mult": 1.0,
    "portfolio_cash_vega_cap": 0.008,
    "portfolio_cash_gamma_cap": 0.0,
    "portfolio_bucket_cash_vega_cap": 0.0,
    "portfolio_bucket_cash_gamma_cap": 0.0,
    "portfolio_stress_gate_enabled": False,
    "portfolio_stress_spot_move_pct": 0.03,
    "portfolio_stress_iv_up_points": 5.0,
    "portfolio_stress_loss_cap": 0.03,
    "portfolio_bucket_stress_loss_cap": 0.0,
    "portfolio_execution_budget_policy": "min_signal_current",
    "portfolio_execution_allow_signal_product_overrides": False,
    "portfolio_budget_brake_enabled": True,
    "portfolio_dd_pause_falling": 0.008,
    "portfolio_dd_reduce_limit": 0.012,
    "portfolio_dd_reduce_scale": 0.50,
    "portfolio_dd_defensive_limit": 0.016,
    "portfolio_dd_defensive_scale": 0.25,
    "portfolio_stop_cluster_lookback_days": 5,
    "portfolio_stop_cluster_threshold": 3,
    "portfolio_stop_cluster_scale": 0.50,
    "iv_low_skip_threshold": 20,
    "low_iv_exception_enabled": True,
    "low_iv_allowed_products": [],
    "low_iv_min_iv_rv_spread": 0.02,
    "low_iv_min_iv_rv_ratio": 1.10,
    "low_iv_max_rv_trend": None,
    "rv_lookback": 20,
    "rv_min_periods": 10,
}


# ── 合约选择函数 ──────────────────────────────────────────────────────────────

def _stable_pick(df, sort_cols, ascending):
    """确定性选腿：显式稳定排序，并用 option_code 作为最终 tie-breaker。"""
    ranked = _stable_rank(df, sort_cols, ascending)
    if ranked is None or ranked.empty:
        return None
    return ranked.iloc[0]


def _stable_rank(df, sort_cols, ascending):
    """Return a deterministic ranking with option_code as final tie-breaker."""
    if df is None or df.empty:
        return None
    work = df.copy()
    cols = list(sort_cols)
    orders = list(ascending)
    if "option_code" in work.columns and "option_code" not in cols:
        cols.append("option_code")
        orders.append(True)
    return work.sort_values(cols, ascending=orders, kind="mergesort")


def _pct_rank_high(series, fill_value=0.0):
    """Return percentile ranks where larger raw values are better."""
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(fill_value, index=series.index, dtype=float)
    return values.rank(method="average", pct=True).fillna(fill_value)


def _pct_rank_low(series, fill_value=0.0):
    """Return percentile ranks where smaller raw values are better."""
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(fill_value, index=series.index, dtype=float)
    return (1.0 - values.rank(method="average", pct=True)).fillna(fill_value)


def _float_or_nan(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return value if np.isfinite(value) else np.nan


def classify_s1_trend_confidence(returns, rv_trend=np.nan, *,
                                 short_lookback=5, medium_lookback=10,
                                 long_lookback=20, min_history=10,
                                 trend_threshold=0.018,
                                 range_threshold=0.010,
                                 rv_rising_threshold=0.015,
                                 range_pressure_enabled=False,
                                 range_pressure_lookback=20,
                                 range_pressure_upper=0.80,
                                 range_pressure_lower=0.20,
                                 range_pressure_min_short_ret=0.004):
    """Classify rough trend state from trailing underlying returns.

    This is deliberately a confidence gauge, not a directional forecast. It
    lets S1 keep collecting both sides in range-bound markets while making the
    trend-opposite side smaller and farther OTM when direction looks clearer.
    """
    values = pd.to_numeric(pd.Series(returns), errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    max_lookback = max(
        int(short_lookback or 1),
        int(medium_lookback or 1),
        int(long_lookback or 1),
        1,
    )
    min_history = max(int(min_history or 1), 1)
    if len(values) < min(min_history, max_lookback):
        return {
            "trend_state": "uncertain",
            "trend_score": np.nan,
            "trend_confidence": 0.0,
            "trend_short_ret": np.nan,
            "trend_medium_ret": np.nan,
            "trend_long_ret": np.nan,
            "trend_range_position": np.nan,
            "trend_range_pressure": "",
        }

    def trailing_sum(window):
        window = max(int(window or 1), 1)
        return float(values.tail(min(window, len(values))).sum())

    short_ret = trailing_sum(short_lookback)
    medium_ret = trailing_sum(medium_lookback)
    long_ret = trailing_sum(long_lookback)
    score = 0.50 * short_ret + 0.30 * medium_ret + 0.20 * long_ret

    signs = []
    noise = max(float(range_threshold or 0.0), 0.0)
    for val in (short_ret, medium_ret, long_ret):
        if abs(val) <= noise:
            continue
        signs.append(1 if val > 0 else -1)
    if not signs:
        alignment = 0.0
    else:
        score_sign = 1 if score >= 0 else -1
        alignment = sum(1 for s in signs if s == score_sign) / len(signs)

    threshold = max(float(trend_threshold or 0.0), 1e-12)
    confidence = min(abs(score) / threshold, 1.0) * (0.50 + 0.50 * alignment)
    if abs(score) <= noise:
        state = "range_bound"
    elif score >= threshold and alignment >= 0.50:
        state = "uptrend"
    elif score <= -threshold and alignment >= 0.50:
        state = "downtrend"
    else:
        state = "uncertain"

    range_position = np.nan
    range_pressure = ""
    if range_pressure_enabled and state == "range_bound":
        pressure_window = max(int(range_pressure_lookback or long_lookback or 1), 2)
        pressure_values = values.tail(min(pressure_window, len(values)))
        clipped = pressure_values.clip(lower=-0.95)
        price_path = np.exp(np.log1p(clipped).cumsum())
        if len(price_path) >= 2:
            low = float(price_path.min())
            high = float(price_path.max())
            span = high - low
            if span > 1e-12:
                range_position = float((price_path.iloc[-1] - low) / span)
                upper = min(max(float(range_pressure_upper or 0.80), 0.0), 1.0)
                lower = min(max(float(range_pressure_lower or 0.20), 0.0), 1.0)
                min_short_ret = max(float(range_pressure_min_short_ret or 0.0), 0.0)
                if range_position >= upper and short_ret >= min_short_ret:
                    state = "uptrend"
                    range_pressure = "upper"
                    edge_strength = (
                        (range_position - upper) / max(1.0 - upper, 1e-12)
                    )
                    momentum_strength = min(abs(short_ret) / threshold, 1.0)
                    confidence = max(
                        confidence,
                        min(1.0, 0.35 + 0.65 * (0.60 * edge_strength + 0.40 * momentum_strength)),
                    )
                elif range_position <= lower and short_ret <= -min_short_ret:
                    state = "downtrend"
                    range_pressure = "lower"
                    edge_strength = (
                        (lower - range_position) / max(lower, 1e-12)
                    )
                    momentum_strength = min(abs(short_ret) / threshold, 1.0)
                    confidence = max(
                        confidence,
                        min(1.0, 0.35 + 0.65 * (0.60 * edge_strength + 0.40 * momentum_strength)),
                    )

    rv_trend = _float_or_nan(rv_trend)
    if (
        state == "range_bound" and
        pd.notna(rv_trend) and
        rv_trend >= float(rv_rising_threshold or 0.0)
    ):
        state = "uncertain"
        confidence *= 0.75

    return {
        "trend_state": state,
        "trend_score": float(score),
        "trend_confidence": float(max(0.0, min(confidence, 1.0))),
        "trend_short_ret": short_ret,
        "trend_medium_ret": medium_ret,
        "trend_long_ret": long_ret,
        "trend_range_position": range_position,
        "trend_range_pressure": range_pressure,
    }


def s1_trend_side_adjustment(option_type, trend_state, trend_confidence=0.0, *,
                             weak_delta_cap=0.060,
                             weak_score_mult=0.60,
                             weak_budget_mult=0.50,
                             strong_score_mult=1.00):
    """Return side-level score, delta, and budget adjustments for F3."""
    opt = str(option_type or "").upper()
    state = str(trend_state or "uncertain")
    confidence = max(0.0, min(_float_or_nan(trend_confidence), 1.0))
    if pd.isna(confidence):
        confidence = 0.0
    role = "neutral"
    if state == "uptrend":
        role = "strong" if opt == "P" else "weak"
    elif state == "downtrend":
        role = "strong" if opt == "C" else "weak"

    if role == "weak":
        score_mult = 1.0 - confidence * (1.0 - float(weak_score_mult or 0.0))
        budget_mult = 1.0 - confidence * (1.0 - float(weak_budget_mult or 0.0))
        return {
            "trend_role": role,
            "score_mult": max(score_mult, 0.0),
            "budget_mult": max(budget_mult, 0.0),
            "delta_cap": max(float(weak_delta_cap or 0.0), 0.0),
        }
    if role == "strong":
        score_mult = 1.0 + confidence * (float(strong_score_mult or 1.0) - 1.0)
        return {
            "trend_role": role,
            "score_mult": max(score_mult, 0.0),
            "budget_mult": 1.0,
            "delta_cap": None,
        }
    return {
        "trend_role": role,
        "score_mult": 1.0,
        "budget_mult": 1.0,
        "delta_cap": None,
    }


def s1_side_adjusted_score(row, option_type, momentum=np.nan,
                           momentum_threshold=0.02, momentum_penalty=0.75):
    """Score a side after penalizing adverse short-term underlying momentum.

    Selling puts is vulnerable to falling underlyings; selling calls is
    vulnerable to rising underlyings. The penalty is intentionally mild and
    parameterized because F2 tests direction selection, not a hard trend filter.
    """
    if row is None:
        return np.nan
    raw_score = _float_or_nan(row.get("quality_score", row.get("carry_score", np.nan)))
    if pd.isna(raw_score):
        return np.nan
    momentum = _float_or_nan(momentum)
    if pd.isna(momentum):
        return raw_score

    threshold = max(float(momentum_threshold or 0.0), 0.0)
    penalty_weight = max(float(momentum_penalty or 0.0), 0.0)
    if penalty_weight <= 0:
        return raw_score

    opt = str(option_type or "").upper()
    if opt == "P":
        adverse_move = max(0.0, -momentum - threshold)
    elif opt == "C":
        adverse_move = max(0.0, momentum - threshold)
    else:
        adverse_move = 0.0
    if adverse_move <= 0:
        return raw_score

    adverse_units = adverse_move / threshold if threshold > 0 else 1.0
    return raw_score / (1.0 + penalty_weight * adverse_units)


def choose_s1_trend_confidence_sides(side_candidates, *, trend_state,
                                     current_regime="normal_vol",
                                     conditional_strangle_enabled=True,
                                     allowed_strangle_regimes=None,
                                     strangle_states=None,
                                     strangle_min_score_ratio=0.90,
                                     strangle_min_adjusted_score=0.35,
                                     allow_weak_side=True,
                                     weak_side_min_score_ratio=0.75):
    """Choose S1 sides using trend-confidence roles and adjusted scores."""
    side_candidates = side_candidates or {}
    available = {
        str(ot).upper(): row
        for ot, row in side_candidates.items()
        if row is not None
    }
    if not available:
        return []

    scores = {
        ot: _float_or_nan(row.get("quality_score", row.get("carry_score", np.nan)))
        for ot, row in available.items()
    }
    scores = {ot: val for ot, val in scores.items() if pd.notna(val)}
    if not scores:
        return []

    state = str(trend_state or "uncertain")
    strangle_states = set(strangle_states or ("range_bound",))
    allowed = set(allowed_strangle_regimes or ("falling_vol_carry", "low_stable_vol"))
    if (
        conditional_strangle_enabled and
        state in strangle_states and
        current_regime in allowed and
        {"P", "C"}.issubset(scores)
    ):
        high = max(scores["P"], scores["C"])
        low = min(scores["P"], scores["C"])
        min_score = float(strangle_min_adjusted_score or 0.0)
        min_ratio = float(strangle_min_score_ratio or 0.0)
        if (
            (min_score <= 0 or low >= min_score) and
            (min_ratio <= 0 or high <= 0 or low >= high * min_ratio)
        ):
            return ["P", "C"] if scores["P"] >= scores["C"] else ["C", "P"]

    if state == "uptrend":
        strong, weak = "P", "C"
    elif state == "downtrend":
        strong, weak = "C", "P"
    else:
        return sorted(scores, key=lambda ot: (-scores[ot], ot))[:1]

    if strong not in scores:
        return sorted(scores, key=lambda ot: (-scores[ot], ot))[:1]
    selected = [strong]
    if allow_weak_side and weak in scores:
        min_score = float(strangle_min_adjusted_score or 0.0)
        min_ratio = float(weak_side_min_score_ratio or 0.0)
        if (
            (min_score <= 0 or scores[weak] >= min_score) and
            (min_ratio <= 0 or scores[strong] <= 0 or scores[weak] >= scores[strong] * min_ratio)
        ):
            selected.append(weak)
    return selected


def choose_s1_option_sides(side_candidates, *, enabled=False,
                           conditional_strangle_enabled=False,
                           current_regime="normal_vol", momentum=np.nan,
                           momentum_threshold=0.02, momentum_penalty=0.75,
                           allowed_strangle_regimes=None,
                           strangle_max_abs_momentum=0.015,
                           strangle_min_score_ratio=0.90,
                           strangle_min_adjusted_score=0.35,
                           strangle_require_momentum=True):
    """Choose S1 sides from top put/call candidates.

    When disabled, preserve the legacy behavior of trying P then C. When
    enabled, select the better side by adjusted score and only return both
    sides when strangle quality and neutrality checks pass.
    """
    side_candidates = side_candidates or {}
    legacy_sides = [ot for ot in ("P", "C") if side_candidates.get(ot) is not None]
    if not enabled:
        return legacy_sides

    ranked = []
    for ot in ("P", "C"):
        row = side_candidates.get(ot)
        if row is None:
            continue
        adjusted = s1_side_adjusted_score(
            row,
            ot,
            momentum=momentum,
            momentum_threshold=momentum_threshold,
            momentum_penalty=momentum_penalty,
        )
        if pd.isna(adjusted):
            continue
        ranked.append((ot, float(adjusted)))
    if not ranked:
        return []

    ranked.sort(key=lambda item: (-item[1], item[0]))
    best_side = [ranked[0][0]]
    if not conditional_strangle_enabled or len(ranked) < 2:
        return best_side

    allowed = set(allowed_strangle_regimes or ("falling_vol_carry", "low_stable_vol"))
    if current_regime not in allowed:
        return best_side

    momentum = _float_or_nan(momentum)
    if strangle_require_momentum and pd.isna(momentum):
        return best_side
    max_abs_momentum = float(strangle_max_abs_momentum or 0.0)
    if pd.notna(momentum) and max_abs_momentum >= 0 and abs(momentum) > max_abs_momentum:
        return best_side

    high_score = ranked[0][1]
    low_score = ranked[1][1]
    min_score = float(strangle_min_adjusted_score or 0.0)
    if min_score > 0 and low_score < min_score:
        return best_side
    ratio = float(strangle_min_score_ratio or 0.0)
    if ratio > 0 and high_score > 0 and low_score < high_score * ratio:
        return best_side
    return [ranked[0][0], ranked[1][0]]


def select_s1_protect(day_df, sell_row, mode="inner",
                      max_abs_delta=0.25, min_price=0.5,
                      premium_ratio_cap=None):
    """
    S1保护腿选择：|delta|<0.25、更靠近平值、选|delta|最大

    返回: pd.Series 或 None
    """
    mode = str(mode or "inner").strip().lower()
    ot = sell_row["option_type"]
    sell_abs_delta = abs(float(sell_row.get("delta", 0.0) or 0.0))
    sell_price = float(sell_row.get("option_close", 0.0) or 0.0)
    delta_cap = min(max_abs_delta, sell_abs_delta) if sell_abs_delta > 0 else max_abs_delta

    if ot == "P":
        strike_filter = day_df["strike"] < sell_row["strike"] if mode == "wing" else day_df["strike"] > sell_row["strike"]
        p = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() < delta_cap) &
            (day_df["option_close"] >= min_price) &
            strike_filter
        ]
        if p.empty:
            return None
        if premium_ratio_cap is not None and sell_price > 0:
            p = p[p["option_close"] <= sell_price * float(premium_ratio_cap)]
        if p.empty:
            return None
        p = p.copy()
        if mode == "wing":
            p["strike_gap"] = sell_row["strike"] - p["strike"]
            p["delta_gap"] = delta_cap - p["delta"].abs()
            return _stable_pick(
                p,
                ["strike_gap", "option_close", "delta_gap", "volume", "open_interest"],
                [True, True, True, False, False],
            )
        p["abs_delta"] = p["delta"].abs()
        return _stable_pick(
            p,
            ["abs_delta", "volume", "open_interest"],
            [False, False, False],
        )
    else:
        strike_filter = day_df["strike"] > sell_row["strike"] if mode == "wing" else day_df["strike"] < sell_row["strike"]
        p = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] < delta_cap) &
            (day_df["option_close"] >= min_price) &
            strike_filter
        ]
        if p.empty:
            return None
        if premium_ratio_cap is not None and sell_price > 0:
            p = p[p["option_close"] <= sell_price * float(premium_ratio_cap)]
        if p.empty:
            return None
        p = p.copy()
        if mode == "wing":
            p["strike_gap"] = p["strike"] - sell_row["strike"]
            p["delta_gap"] = delta_cap - p["delta"].abs()
            return _stable_pick(
                p,
                ["strike_gap", "option_close", "delta_gap", "volume", "open_interest"],
                [True, True, True, False, False],
            )
        return _stable_pick(
            p,
            ["delta", "volume", "open_interest"],
            [False, False, False],
        )


def select_s3_buy(day_df, option_type):
    """
    S3买腿选择：|delta| 0.10-0.20，选最接近0.15

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() >= 0.10) &
            (day_df["delta"].abs() <= 0.20) &
            (day_df["option_close"] >= 0.5)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] >= 0.10) &
            (day_df["delta"] <= 0.20) &
            (day_df["option_close"] >= 0.5)
        ]
    if c.empty:
        return None
    c = c.copy()
    c["dd"] = (c["delta"].abs() - 0.15).abs()
    return _stable_pick(
        c,
        ["dd", "volume", "open_interest"],
        [True, False, False],
    )


def select_s3_sell(day_df, option_type, buy_strike):
    """
    S3卖腿选择：|delta| 0.05-0.15，比买腿更虚值，选权利金最高

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() >= 0.05) &
            (day_df["delta"].abs() <= 0.15) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] < buy_strike)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] >= 0.05) &
            (day_df["delta"] <= 0.15) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] > buy_strike)
        ]
    if c.empty:
        return None
    return _stable_pick(
        c,
        ["option_close", "volume", "open_interest"],
        [False, False, False],
    )


def select_s3_protect(day_df, option_type, sell_strike, spot):
    """
    S3保护腿选择：比卖腿更虚值，行权价最接近target

    target_strike:
      Put:  sell_k - (spot - sell_k) × 0.5
      Call: sell_k + (sell_k - spot) × 0.5

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["option_close"] >= 0.1) &
            (day_df["strike"] < sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike - (spot - sell_strike) * 0.5
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["option_close"] >= 0.1) &
            (day_df["strike"] > sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike + (sell_strike - spot) * 0.5
    c = c.copy()
    c["d"] = (c["strike"] - tgt).abs()
    return _stable_pick(
        c,
        ["d", "option_close", "volume", "open_interest"],
        [True, True, False, False],
    )


# ── S3 OTM%选腿函数（v2）────────────────────────────────────────────────────

def select_s3_buy_by_otm(day_df, option_type, spot_close,
                          target_otm_pct=5.0, otm_range=(3.0, 7.0),
                          min_premium=0.5):
    """S3买腿选择（v2）：按OTM%筛选，选最接近target_otm_pct的合约

    参数:
        day_df: 当日该品种该到期日的期权数据
        option_type: "P" 或 "C"
        spot_close: 标的收盘价（同月期货价格）
        target_otm_pct: 目标OTM%，默认5.0
        otm_range: OTM%筛选范围，默认(3.0, 7.0)
        min_premium: 最低权利金，默认0.5
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return _stable_pick(
        c,
        ["dist", "volume", "open_interest"],
        [True, False, False],
    )


def select_s3_sell_by_otm(day_df, option_type, spot_close, buy_strike,
                           target_otm_pct=10.0, otm_range=(7.0, 13.0),
                           min_premium=0.5):
    """S3卖腿选择（v2）：按OTM%筛选，比买腿更虚值，选权利金最高

    参数:
        buy_strike: 买腿行权价，卖腿必须比买腿更虚值
        其余同 select_s3_buy_by_otm
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["strike"] < buy_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["strike"] > buy_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    return _stable_pick(
        c,
        ["option_close", "volume", "open_interest"],
        [False, False, False],
    )


def select_s3_protect_by_otm(day_df, option_type, spot_close, sell_strike,
                              target_otm_pct=15.0, otm_range=(12.0, 20.0),
                              min_premium=0.1):
    """S3保护腿选择（v2）：应急保护触发时，按OTM%筛选，比卖腿更虚值，选最接近target

    参数:
        sell_strike: 卖腿行权价，保护腿必须比卖腿更虚值
        min_premium: 最低权利金，默认0.1（保护腿可以更便宜）
        其余同 select_s3_buy_by_otm
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["strike"] < sell_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["strike"] > sell_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return _stable_pick(
        c,
        ["dist", "option_close", "volume", "open_interest"],
        [True, True, False, False],
    )


def select_s4(day_df, option_type):
    """
    S4尾部对冲选择：最深虚值，premium>=0.1

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["option_close"] >= 0.1)
        ]
        return _stable_pick(
            c,
            ["moneyness", "option_close", "volume", "open_interest"],
            [True, True, False, False],
        ) if not c.empty else None
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["option_close"] >= 0.1)
        ]
        return _stable_pick(
            c,
            ["moneyness", "option_close", "volume", "open_interest"],
            [False, True, False, False],
        ) if not c.empty else None


# ── 手数计算 ──────────────────────────────────────────────────────────────────

def calc_s1_size(nav, margin_per, single_margin, iv_scale):
    """S1每方向卖腿手数 = nav × margin_per/2 × iv_scale / 单手保证金"""
    if single_margin <= 0:
        return 1
    return max(1, int(nav * margin_per / 2 * iv_scale / single_margin))


def calc_s1_stress_loss(row, option_type, mult, spot_move_pct=0.03,
                        iv_up_points=5.0):
    """Estimate one-contract short-option stress loss with delta/gamma/vega."""
    spot = float(row.get("spot_close", 0.0) or 0.0)
    if spot <= 0:
        return np.nan
    delta = float(row.get("delta", 0.0) or 0.0)
    gamma = float(row.get("gamma", 0.0) or 0.0)
    vega = float(row.get("vega", 0.0) or 0.0)
    move = max(float(spot_move_pct or 0.0), 0.0)
    ds = -spot * move if option_type == "P" else spot * move
    long_change = delta * ds + 0.5 * gamma * ds * ds + vega * float(iv_up_points or 0.0)
    return max(float(long_change), 0.0) * float(mult)


def calc_s1_stress_size(nav, stress_budget_pct, one_contract_stress_loss,
                        iv_scale=1.0, min_qty=1, max_qty=50):
    """Size S1 by stress-risk budget instead of target margin usage."""
    if one_contract_stress_loss is None or not np.isfinite(one_contract_stress_loss):
        return 0
    if one_contract_stress_loss <= 0:
        return int(max(min_qty, 1))
    budget = float(nav) * float(stress_budget_pct or 0.0) * float(iv_scale or 1.0)
    if budget <= 0:
        return 0
    qty = int(budget / float(one_contract_stress_loss))
    qty = max(int(min_qty or 1), qty)
    if max_qty is not None and int(max_qty) > 0:
        qty = min(qty, int(max_qty))
    return max(qty, 0)


def calc_s3_size(nav, margin_per, sell_margin, s3_ratio, iv_scale):
    """
    S3手数计算
    买腿手数 = nav × margin_per/2 × iv_scale / (单手卖腿保证金 × ratio)
    卖腿手数 = 买腿 × ratio
    """
    if sell_margin <= 0:
        return 1, s3_ratio
    buy_qty = max(1, int(nav * margin_per / 2 * iv_scale / (sell_margin * s3_ratio)))
    sell_qty = buy_qty * s3_ratio
    return buy_qty, sell_qty


def calc_s4_size(nav, s4_prem, n_s4_products, cost_per_hand, max_hands=5):
    """S4每方向手数，预算 = nav × s4_prem / 品种数 / 2方向"""
    if n_s4_products <= 0 or cost_per_hand <= 0:
        return 1
    budget = nav * s4_prem / n_s4_products / 2
    qty = max(1, int(budget / cost_per_hand))
    return min(qty, max_hands)


def calc_s3_size_v2(nav, margin_per, sell_margin, buy_premium,
                     sell_premium, mult, iv_scale,
                     ratio_candidates=(2, 3), net_premium_tolerance=0.3):
    """
    S3手数计算（v2）：灵活比例 + 零成本进场约束

    先尝试小比例(1:2)，若净权利金不够覆盖买腿成本再尝试大比例(1:3)。
    返回 (buy_qty, sell_qty, chosen_ratio) 或 None（无法满足零成本约束时）
    """
    if sell_margin <= 0 or buy_premium <= 0 or sell_premium <= 0:
        return None
    for ratio in sorted(ratio_candidates):
        buy_qty = max(1, int(nav * margin_per / 2 * iv_scale
                             / (sell_margin * ratio)))
        sell_qty = buy_qty * ratio
        # 零成本检查：net_premium = sell收入 - buy成本
        buy_cost = buy_premium * mult * buy_qty
        sell_income = sell_premium * mult * sell_qty
        net_premium = sell_income - buy_cost
        # 容忍范围：net_premium >= -buy_cost × tolerance
        if net_premium >= -buy_cost * net_premium_tolerance:
            return buy_qty, sell_qty, ratio
    return None


def check_emergency_protect(sell_strike, spot_close, option_type,
                             trigger_otm_pct=5.0):
    """
    检查卖腿是否接近平值，需要触发应急保护。

    当卖腿OTM%降至trigger_otm_pct以下时返回True。
    Put端：spot下跌使put卖腿接近平值
    Call端：spot上涨使call卖腿接近平值
    """
    if spot_close <= 0:
        return False
    current_otm_pct = abs(1 - sell_strike / spot_close) * 100
    return current_otm_pct < trigger_otm_pct


# ── IV分位数 ──────────────────────────────────────────────────────────────────

def calc_iv_percentile(iv_series, current_date, window=252, min_periods=60):
    """
    计算IV分位数（因果窗口：只使用截止current_date的数据）

    参数:
        iv_series: pd.Series，index=trade_date, value=ATM平均implied_vol
        current_date: 当前日期
        window: 滚动窗口大小
        min_periods: 最少数据点
    返回:
        float 分位数(0-100) 或 NaN
    """
    # 只使用截止当前日期的数据
    causal = iv_series[iv_series.index <= current_date]
    if len(causal) < min_periods:
        return np.nan
    recent = causal.iloc[-window:] if len(causal) > window else causal
    current_val = recent.iloc[-1]
    return percentileofscore(recent.values, current_val, kind='rank')


def calc_iv_percentile_batch(iv_series, window=252, min_periods=60):
    """
    批量计算IV分位数（rolling，与unified_engine_v3.py一致）

    返回: pd.Series，index=trade_date, value=percentile
    """
    return iv_series.rolling(window, min_periods=min_periods).apply(
        lambda x: percentileofscore(x, x.iloc[-1], kind='rank'))


def get_iv_scale(iv_pct, threshold=75):
    """
    因子1：波动率自适应仓位缩放

    iv_pct <= threshold: scale = 1.0（满仓）
    iv_pct > threshold:  scale = 1 - iv_pct/200（线性缩减）
    最低不低于 0.3（保留30%仓位）

    示例：iv_pct=80 → scale=0.6, iv_pct=90 → scale=0.55, iv_pct=100 → scale=0.5
    """
    if pd.isna(iv_pct) or iv_pct <= threshold:
        return 1.0
    scale = 1.0 - iv_pct / 200.0
    return max(scale, 0.3)


def should_pause_open(iv_pct, iv_open_threshold=80):
    """
    IV环境过滤：IV分位>阈值时暂停S1/S3新开仓（含止盈重开）。
    S4不受影响（买方策略，高波时更有价值）。

    回测验证：IV>80%暂停使夏普从1.13提升到1.47（+30%），
    回撤从-8.03%降到-7.05%，年化反而提升到+22.50%。
    """
    if pd.isna(iv_pct):
        return False  # 无数据时不过滤
    return iv_pct > iv_open_threshold


# ── ATM IV提取 ────────────────────────────────────────────────────────────────

def extract_atm_iv_series(product_df):
    """
    从品种全量数据中提取ATM隐含波动率时间序列

    筛选: moneyness ∈ [0.95, 1.05], dte ∈ [15, 90], implied_vol > 0
    返回: pd.Series，index=trade_date, value=daily_mean_atm_iv
    """
    atm = product_df[
        (product_df["moneyness"].between(0.95, 1.05)) &
        (product_df["dte"].between(15, 90)) &
        (product_df["implied_vol"] > 0)
    ]
    if atm.empty:
        return pd.Series(dtype=float)
    return atm.groupby("trade_date")["implied_vol"].mean()


# ── 信号判断 ──────────────────────────────────────────────────────────────────

def should_open_new(product_df_today, dte_target=35, dte_min=15, dte_max=90):
    """
    检查今日是否应该触发开仓。

    返回满足条件的 expiry_date 列表，并按更接近目标 DTE、再按到期日稳定排序，
    避免相同行情下到期月处理顺序漂移。
    """
    if product_df_today.empty:
        return []

    candidates = []
    for exp in product_df_today["expiry_date"].unique():
        exp_data = product_df_today[product_df_today["expiry_date"] == exp]
        if exp_data.empty:
            continue
        dte = exp_data["dte"].iloc[0]
        if dte < dte_min or dte > dte_max:
            continue
        dist_today = abs(dte - dte_target)
        dist_tomorrow = abs(dte - 1 - dte_target)
        if dist_today <= dist_tomorrow:
            candidates.append((dist_today, dte, str(exp)))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    return [exp for _, _, exp in candidates]


def should_take_profit_s1(profit_pct, dte, tp=0.40, min_dte=5, iv_pct=None):
    """
    S1止盈判断（因子6：动态止盈阈值）

    高IV(>75分位): 止盈上移10%（让利润跑，高IV环境Theta衰减更快）
    低IV(<25分位): 止盈下移10%（快速落袋，低IV环境收益有限）
    """
    adj_tp = tp
    if iv_pct is not None and not pd.isna(iv_pct):
        if iv_pct > 75:
            adj_tp = tp * 1.1  # 高IV: 40% → 44%
        elif iv_pct < 25:
            adj_tp = tp * 0.9  # 低IV: 40% → 36%
    return profit_pct >= adj_tp and dte > min_dte


def should_take_profit_s3(profit_pct, dte, tp=0.30, min_dte=5, iv_pct=None):
    """
    S3止盈判断（因子6：动态止盈阈值）

    高IV(>75分位): 止盈上移10%
    低IV(<25分位): 止盈下移10%
    """
    adj_tp = tp
    if iv_pct is not None and not pd.isna(iv_pct):
        if iv_pct > 75:
            adj_tp = tp * 1.1  # 高IV: 30% → 33%
        elif iv_pct < 25:
            adj_tp = tp * 0.9  # 低IV: 30% → 27%
    return profit_pct >= adj_tp and dte > min_dte


def should_close_expiry(dte, threshold=1):
    """到期平仓判断"""
    return dte <= threshold


def can_reopen(dte, min_dte=10):
    """止盈后是否可以重开"""
    return dte > min_dte


# ── 保证金检查 ────────────────────────────────────────────────────────────────

def check_margin_ok(cur_total_margin, cur_strategy_margin, new_margin,
                    nav, margin_cap=0.50, strategy_cap=0.25):
    """
    检查新增保证金是否超限

    参数:
        cur_total_margin: 当前所有策略卖腿保证金之和
        cur_strategy_margin: 当前该策略的卖腿保证金之和
        new_margin: 新增保证金
        nav: 当前NAV
        margin_cap: 组合总保证金上限
        strategy_cap: 策略独立保证金上限
    """
    try:
        nav = float(nav)
        cur_total_margin = float(cur_total_margin)
        cur_strategy_margin = float(cur_strategy_margin)
        new_margin = float(new_margin)
    except (TypeError, ValueError):
        return False

    values = (nav, cur_total_margin, cur_strategy_margin, new_margin)
    if any(not np.isfinite(v) for v in values):
        return False
    if nav <= 0 or cur_total_margin < 0 or cur_strategy_margin < 0 or new_margin < 0:
        return False

    margin_cap = float(margin_cap or 0.0)
    strategy_cap = float(strategy_cap or 0.0)
    if margin_cap < 0 or strategy_cap < 0:
        return False

    if margin_cap and (cur_total_margin + new_margin) / nav > margin_cap:
        return False
    if strategy_cap and (cur_strategy_margin + new_margin) / nav > strategy_cap:
        return False
    return True


# ── 滑点 ──────────────────────────────────────────────────────────────────────

def apply_slippage(price, direction, slippage=0.002):
    """
    施加滑点
    direction: 'buy' or 'sell'
    """
    if direction == "buy":
        return price * (1 + slippage)
    else:
        return price * (1 - slippage)


# ── NAV & 统计 ────────────────────────────────────────────────────────────────

def calc_stats(nav_array):
    """计算风险收益指标（与unified_engine_v3.stats一致）"""
    if len(nav_array) < 10:
        return {}
    dr = np.diff(nav_array) / nav_array[:-1]
    yrs = max(len(nav_array) / 252, 0.5)
    ar = (nav_array[-1] / nav_array[0]) ** (1 / yrs) - 1
    vol = np.std(dr) * np.sqrt(252)
    sr = (ar - 0.02) / vol if vol > 0 else 0
    pk = np.maximum.accumulate(nav_array)
    mdd = ((nav_array - pk) / pk).min()
    cal = ar / abs(mdd) if mdd != 0 else 0
    return {"ann_return": ar, "ann_vol": vol, "max_dd": mdd, "sharpe": sr, "calmar": cal}


# ── T+1 VWAP执行价格 ─────────────────────────────────────────────────────────

def load_t1_price_index(db_path="benchmark.db"):
    """
    加载所有合约的OHLC数据，构建T+1日执行价格索引。
    返回: dict[(trade_date, option_code)] -> {"open":, "high":, "low":, "close":, "typical":}
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    bars = pd.read_sql(
        "SELECT trade_date, option_code, open, high, low, close "
        "FROM stg_option_daily_bar WHERE volume > 0 AND open > 0",
        conn)
    conn.close()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["typical"] = (bars["high"] + bars["low"] + bars["close"]) / 3
    return bars.set_index(["trade_date", "option_code"])


def get_t1_execution_price(t1_idx, next_date, option_code, fallback_price):
    """
    获取T+1日VWAP执行价格。
    如果T+1日该合约无数据，返回fallback_price（T日收盘价）。
    """
    if t1_idx is None or next_date is None:
        return fallback_price
    key = (next_date, option_code)
    if key not in t1_idx.index:
        return fallback_price
    row = t1_idx.loc[key]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    typical = row["typical"]
    if pd.notna(typical) and typical > 0:
        return typical
    return fallback_price


def calc_realized_vol_series(price_series, window=20, min_periods=10):
    """Compute annualized realized volatility series from spot prices."""
    prices = pd.Series(price_series, dtype=float)
    prices = prices.replace([np.inf, -np.inf], np.nan).dropna()
    prices = prices[prices > 0]
    if prices.empty:
        return pd.Series(dtype=float)
    log_ret = np.log(prices).diff()
    return log_ret.rolling(window, min_periods=min_periods).std() * np.sqrt(252.0)


def calc_iv_rv_features(iv_series, spot_series, current_date, rv_window=20, min_periods=10):
    """Build low-IV admission features from IV and spot history."""
    iv = pd.Series(iv_series, dtype=float)
    spot = pd.Series(spot_series, dtype=float)
    if len(iv) == 0 or len(spot) == 0:
        return {"rv20": np.nan, "iv_rv_spread": np.nan, "iv_rv_ratio": np.nan, "rv_trend": np.nan}

    iv = iv[iv.index <= current_date].dropna()
    spot = spot[spot.index <= current_date].dropna()
    if iv.empty or spot.empty:
        return {"rv20": np.nan, "iv_rv_spread": np.nan, "iv_rv_ratio": np.nan, "rv_trend": np.nan}

    rv_series = calc_realized_vol_series(spot, window=rv_window, min_periods=min_periods)
    rv_series = rv_series.dropna()
    if rv_series.empty:
        return {"rv20": np.nan, "iv_rv_spread": np.nan, "iv_rv_ratio": np.nan, "rv_trend": np.nan}

    rv20 = float(rv_series.iloc[-1])
    iv_now = float(iv.iloc[-1])
    spread = iv_now - rv20 if pd.notna(rv20) else np.nan
    ratio = iv_now / rv20 if pd.notna(rv20) and rv20 > 0 else np.nan
    rv_trend = np.nan
    if len(rv_series) >= 5:
        rv_trend = float(rv_series.iloc[-1] - rv_series.iloc[-5])
    return {
        "rv20": rv20,
        "iv_rv_spread": spread,
        "iv_rv_ratio": ratio,
        "rv_trend": rv_trend,
    }


def should_allow_open_low_iv_product(product, iv_pct, feature_state,
                                     enabled=False, low_iv_allowed_products=None,
                                     iv_low_skip_threshold=20,
                                     min_iv_rv_spread=0.02,
                                     min_iv_rv_ratio=1.10,
                                     max_rv_trend=None):
    """Allow selected structurally-low-IV products when IV remains rich to RV."""
    if not enabled:
        return False
    if pd.isna(iv_pct) or iv_pct >= iv_low_skip_threshold:
        return False
    allowed = {str(p).upper() for p in (low_iv_allowed_products or []) if str(p).strip()}
    if not allowed or str(product).upper() not in allowed:
        return False

    spread = feature_state.get("iv_rv_spread", np.nan)
    ratio = feature_state.get("iv_rv_ratio", np.nan)
    rv_trend = feature_state.get("rv_trend", np.nan)
    if pd.isna(spread) or spread < float(min_iv_rv_spread):
        return False
    if pd.isna(ratio) or ratio < float(min_iv_rv_ratio):
        return False
    if max_rv_trend is not None and pd.notna(rv_trend) and rv_trend > float(max_rv_trend):
        return False
    return True


def s1_forward_vega_quality_filter(candidates, option_type, *, iv_state=None,
                                   side_meta=None, config=None):
    """Filter S1 candidates whose wing IV quality does not support short vega.

    The filter is causal: it only uses signal-date ATM IV, contract IV history,
    realized-vol trend, and trailing trend pressure.  It deliberately checks the
    actual wing contract, because ATM IV falling is not enough for a short-vega
    trade when skew is steepening.
    """
    stats = defaultdict(float)
    if candidates is None or candidates.empty:
        return candidates, stats

    cfg = config or {}
    if not cfg.get("s1_forward_vega_filter_enabled", False):
        return candidates, stats

    df = candidates.copy()
    mask = pd.Series(True, index=df.index)
    policy = str(cfg.get("s1_forward_vega_missing_policy", "skip") or "skip").lower()

    def finite_number(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return np.nan
        return value if np.isfinite(value) else np.nan

    def numeric_col(name, default=np.nan):
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(default, index=df.index, dtype=float)

    def apply_rule(rule_ok, key):
        nonlocal mask
        rule_ok = pd.Series(rule_ok, index=df.index).fillna(False)
        failed = mask & ~rule_ok
        stats[key] += float(failed.sum())
        mask = mask & rule_ok

    def threshold_rule(values, max_value):
        values = pd.to_numeric(values, errors="coerce")
        known_ok = values.notna() & (values <= float(max_value))
        missing_ok = values.isna() & (policy != "skip")
        return known_ok | missing_ok

    lookback = max(1, int(cfg.get("s1_forward_vega_contract_iv_lookback", 5) or 1))
    contract_change_col = f"contract_iv_change_{lookback}d"
    if contract_change_col in df.columns:
        contract_iv_change = numeric_col(contract_change_col)
    else:
        contract_iv_change = numeric_col("contract_iv_change_1d")
    df["contract_iv_change_for_vega"] = contract_iv_change

    atm_iv = finite_number((iv_state or {}).get("atm_iv", np.nan))
    atm_trend = finite_number((iv_state or {}).get("iv_trend", np.nan))
    rv_trend = finite_number((iv_state or {}).get("rv_trend", np.nan))
    contract_iv = numeric_col("contract_iv")
    if np.isfinite(atm_iv):
        df["contract_iv_skew_to_atm"] = contract_iv - atm_iv
    else:
        df["contract_iv_skew_to_atm"] = np.nan
    if np.isfinite(atm_trend):
        df["contract_skew_change_for_vega"] = contract_iv_change - atm_trend
    else:
        df["contract_skew_change_for_vega"] = np.nan

    if cfg.get("s1_forward_vega_require_contract_iv_falling", True):
        apply_rule(
            threshold_rule(
                contract_iv_change,
                cfg.get("s1_forward_vega_contract_iv_max_change", 0.0),
            ),
            "skip_forward_vega_contract_iv",
        )

    if cfg.get("s1_forward_vega_require_atm_iv_not_rising", True):
        if np.isfinite(atm_trend):
            rule = pd.Series(
                atm_trend <= float(cfg.get("s1_forward_vega_atm_iv_max_trend", 0.0) or 0.0),
                index=df.index,
            )
        else:
            rule = pd.Series(policy != "skip", index=df.index)
        apply_rule(rule, "skip_forward_vega_atm_iv")

    if cfg.get("s1_forward_vega_require_rv_not_rising", True):
        if np.isfinite(rv_trend):
            rule = pd.Series(
                rv_trend <= float(cfg.get("s1_forward_vega_rv_max_trend", 0.01) or 0.0),
                index=df.index,
            )
        else:
            rule = pd.Series(policy != "skip", index=df.index)
        apply_rule(rule, "skip_forward_vega_rv")

    if cfg.get("s1_forward_vega_require_skew_not_steepening", True):
        apply_rule(
            threshold_rule(
                df["contract_skew_change_for_vega"],
                cfg.get("s1_forward_vega_max_skew_steepen", 0.005),
            ),
            "skip_forward_vega_skew",
        )

    if cfg.get("s1_forward_vega_require_contract_price_not_rising", False):
        price_change = numeric_col("contract_price_change_1d")
        apply_rule(
            threshold_rule(
                price_change,
                cfg.get("s1_forward_vega_contract_price_max_change", 0.10),
            ),
            "skip_forward_vega_price",
        )

    if cfg.get("s1_forward_vega_block_structural_low_breakout", True):
        iv_state = iv_state or {}
        side_meta = side_meta or {}
        regime = str(iv_state.get("vol_regime", "") or "").lower()
        structural_low = bool(iv_state.get("is_structural_low_iv", False))
        if structural_low and not regime.startswith("falling"):
            block = False
            if np.isfinite(rv_trend):
                max_rv = float(
                    cfg.get("s1_forward_vega_structural_low_max_rv_trend", 0.0) or 0.0
                )
                block = block or rv_trend > max_rv
            if cfg.get("s1_forward_vega_structural_low_block_pressure", True):
                pressure = str(side_meta.get("trend_range_pressure", "") or "").lower()
                trend_state = str(side_meta.get("trend_state", "") or "").lower()
                confidence = finite_number(side_meta.get("trend_confidence", np.nan))
                min_conf = float(
                    cfg.get("s1_forward_vega_structural_low_min_trend_confidence", 0.35) or 0.0
                )
                opt = str(option_type or "").upper()[:1]
                call_pressure = pressure == "upper" or (
                    trend_state == "uptrend" and np.isfinite(confidence) and confidence >= min_conf
                )
                put_pressure = pressure == "lower" or (
                    trend_state == "downtrend" and np.isfinite(confidence) and confidence >= min_conf
                )
                block = block or (opt == "C" and call_pressure) or (opt == "P" and put_pressure)
            if block:
                apply_rule(pd.Series(False, index=df.index), "skip_forward_vega_vcp")

    filtered = df[mask].copy()
    stats["forward_vega_candidates_before"] += float(len(df))
    stats["forward_vega_candidates_after"] += float(len(filtered))
    return filtered, stats


def select_s1_sell(day_df, option_type, mult, mr, min_volume=0, min_oi=0,
                   iv_residual_weight=0.3, min_abs_delta=0.0,
                   max_abs_delta=0.10, target_abs_delta=None,
                   carry_metric="premium_margin", fee_per_contract=0.0,
                   roundtrip_fee_per_contract=None,
                   min_premium_fee_multiple=0.0, use_stress_score=False,
                   stress_spot_move_pct=0.03, stress_iv_up_points=5.0,
                   gamma_penalty=0.0, vega_penalty=0.0,
                   ranking_mode="target_delta",
                   premium_stress_weight=0.55,
                   theta_stress_weight=0.25,
                   premium_margin_weight=0.15,
                   liquidity_weight=0.05,
                   delta_weight=0.0,
                   return_candidates=False, max_candidates=1,
                   exchange=None, product=None):
    """Deterministic S1 sell-leg selector with optional carry/stress ranking."""
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() >= min_abs_delta) &
            (day_df["delta"].abs() <= max_abs_delta) &
            (day_df["option_close"] >= 0.5)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] >= min_abs_delta) &
            (day_df["delta"] <= max_abs_delta) &
            (day_df["option_close"] >= 0.5)
        ]
    if c.empty:
        return None
    roundtrip_fee = (
        float(roundtrip_fee_per_contract)
        if roundtrip_fee_per_contract is not None
        else float(fee_per_contract or 0.0) * 2.0
    )
    min_premium = roundtrip_fee * float(min_premium_fee_multiple or 0.0)
    if min_premium > 0:
        c = c[c["option_close"] * float(mult) >= min_premium]
    if c.empty:
        return None
    if min_volume > 0 and "volume" in c.columns:
        c = c[c["volume"] >= min_volume]
    if min_oi > 0 and "open_interest" in c.columns:
        c = c[c["open_interest"] >= min_oi]
    if c.empty:
        return None

    def row_margin(r):
        row_exchange = r["exchange"] if "exchange" in r.index else exchange
        row_product = r["product"] if "product" in r.index else product
        row_mr = resolve_margin_ratio(row_exchange, row_product, default=mr)
        return estimate_margin(
            r["spot_close"], r["strike"], option_type,
            r["option_close"], mult, row_mr, 0.5,
            exchange=row_exchange, product=row_product,
        )

    c = c.copy()
    c["margin"] = c.apply(row_margin, axis=1)
    c = c[c["margin"] > 0].copy()
    if c.empty:
        return None

    gross_premium_cash = c["option_close"] * float(mult)
    net_premium_cash = (gross_premium_cash - roundtrip_fee).clip(lower=0.0)
    c["net_premium_cash"] = net_premium_cash
    c["eff"] = gross_premium_cash / c["margin"]
    c["net_eff"] = net_premium_cash / c["margin"]
    theta_cash = c["theta"].abs() * float(mult) if "theta" in c.columns else pd.Series(0.0, index=c.index)
    if carry_metric == "theta_margin" and "theta" in c.columns:
        c["carry_score"] = theta_cash / c["margin"]
    elif carry_metric == "theta" and "theta" in c.columns:
        c["carry_score"] = theta_cash
    elif carry_metric == "premium":
        c["carry_score"] = net_premium_cash
    elif carry_metric == "net_premium_margin":
        c["carry_score"] = c["net_eff"]
    else:
        c["carry_score"] = c["eff"]
    c["stress_loss"] = c.apply(
        lambda r: calc_s1_stress_loss(
            r, option_type, mult,
            spot_move_pct=stress_spot_move_pct,
            iv_up_points=stress_iv_up_points,
        ),
        axis=1,
    )
    c["stress_loss"] = c["stress_loss"].replace([np.inf, -np.inf], np.nan)
    c = c[c["stress_loss"].notna() & (c["stress_loss"] > 0)].copy()
    if c.empty:
        return None

    if target_abs_delta is None:
        target_abs_delta = (float(min_abs_delta) + float(max_abs_delta)) / 2.0
    c["abs_delta"] = c["delta"].abs()
    c["delta_dist"] = (c["abs_delta"] - float(target_abs_delta)).abs()
    c["premium_stress"] = c["net_premium_cash"] / c["stress_loss"]
    c["theta_stress"] = theta_cash / c["stress_loss"]
    c["premium_margin"] = c["net_eff"]
    volume_rank = _pct_rank_high(c["volume"]) if "volume" in c.columns else pd.Series(0.0, index=c.index)
    oi_rank = _pct_rank_high(c["open_interest"]) if "open_interest" in c.columns else pd.Series(0.0, index=c.index)
    c["liquidity_score"] = 0.5 * volume_rank + 0.5 * oi_rank

    if "iv_residual" in c.columns and iv_residual_weight > 0:
        iv_res = c["iv_residual"].fillna(0).clip(-1, 1)
        c["quality_score"] = c["carry_score"] * (1 + iv_residual_weight * iv_res)
    else:
        c["quality_score"] = c["carry_score"]
    if str(ranking_mode or "").lower() in {"risk_reward", "stress_reward", "premium_stress"}:
        gamma_abs = c["gamma"].abs().fillna(0) if "gamma" in c.columns else pd.Series(0.0, index=c.index)
        vega_abs = c["vega"].abs().fillna(0) if "vega" in c.columns else pd.Series(0.0, index=c.index)
        gamma_penalty_rank = _pct_rank_high(gamma_abs)
        vega_penalty_rank = _pct_rank_high(vega_abs)
        penalty = (
            1.0
            + float(gamma_penalty or 0.0) * gamma_penalty_rank
            + float(vega_penalty or 0.0) * vega_penalty_rank
        )
        c["quality_score"] = (
            float(premium_stress_weight or 0.0) * _pct_rank_high(c["premium_stress"])
            + float(theta_stress_weight or 0.0) * _pct_rank_high(c["theta_stress"])
            + float(premium_margin_weight or 0.0) * _pct_rank_high(c["premium_margin"])
            + float(liquidity_weight or 0.0) * c["liquidity_score"]
            + float(delta_weight or 0.0) * _pct_rank_low(c["delta_dist"])
        ) / penalty
        if "iv_residual" in c.columns and iv_residual_weight > 0:
            iv_res = c["iv_residual"].fillna(0).clip(-1, 1)
            c["quality_score"] = c["quality_score"] * (1 + iv_residual_weight * iv_res)
        ranked = _stable_rank(
            c,
            ["quality_score", "premium_stress", "theta_stress", "premium_margin", "volume", "open_interest"],
            [False, False, False, False, False, False],
        )
        if return_candidates:
            return ranked.head(max(1, int(max_candidates or 1))) if ranked is not None else ranked
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    if use_stress_score:
        gamma_abs = c["gamma"].abs().fillna(0) if "gamma" in c.columns else 0.0
        vega_abs = c["vega"].abs().fillna(0) if "vega" in c.columns else 0.0
        penalty = 1.0 + float(gamma_penalty or 0.0) * gamma_abs + float(vega_penalty or 0.0) * vega_abs
        c["quality_score"] = c["quality_score"] / c["stress_loss"] / penalty
        ranked = _stable_rank(
            c,
            ["quality_score", "volume", "open_interest", "delta_dist", "eff"],
            [False, False, False, True, False],
        )
        if return_candidates:
            return ranked.head(max(1, int(max_candidates or 1))) if ranked is not None else ranked
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    ranked = _stable_rank(
        c,
        ["delta_dist", "volume", "open_interest", "quality_score", "eff"],
        [True, False, False, False, False],
    )
    if return_candidates:
        return ranked.head(max(1, int(max_candidates or 1))) if ranked is not None else ranked
    return None if ranked is None or ranked.empty else ranked.iloc[0]
