"""
策略规则模块：从 unified_engine_v3.py 提取的纯策略逻辑

所有函数均为无状态函数，不依赖全局变量或外部状态。
可被 daily_backtest.py 和 order_generator.py 共同调用。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore
from s1_contract_scoring import (
    calc_s1_stress_loss,
    s1_forward_vega_quality_filter,
    select_s1_sell,
)
from s1_side_selection import (
    choose_s1_option_sides,
    choose_s1_trend_confidence_sides,
    classify_s1_trend_confidence,
    s1_side_adjusted_score,
    s1_trend_side_adjustment,
)
from s3_rules import (
    select_s3_buy,
    select_s3_buy_by_otm,
    select_s3_protect,
    select_s3_protect_by_otm,
    select_s3_sell,
    select_s3_sell_by_otm,
)
from strategy_sizing import (
    calc_s1_size,
    calc_s1_stress_size,
    calc_s3_size,
    calc_s3_size_v2,
    calc_s4_size,
)

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
    "intraday_stop_daily_high_prefilter_enabled": True,
    "take_profit_enabled": False,
    "premium_stop_multiple": 2.50,
    "premium_stop_requires_daily_iv_non_decrease": True,
    "s1_stop_close_scope": "group",
    "s1_layered_stop_enabled": False,
    "s1_layered_stop_levels": [],
    "cooldown_days_after_stop": 1,
    "cooldown_repeat_lookback_days": 20,
    "cooldown_repeat_extra_days": 2,
    "cooldown_repeat_threshold": 2,
    "pre_expiry_exit_dte": 2,
    "s1_sell_delta_cap": 0.10,
    "s1_sell_delta_floor": 0.00,
    "s1_target_abs_delta": 0.07,
    "s1_baseline_mode": False,
    "s1_expiry_mode": "dte",
    "s1_expiry_rank": 2,
    "s1_baseline_equal_weight_products": True,
    "s1_baseline_equal_weight_contracts": True,
    "s1_baseline_max_contracts_per_side": 0,
    "reentry_plan_enabled": True,
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
    "s1_forward_vega_falling_candidate_multiplier": 0,
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
    "s1_min_option_price": 0.0,
    "s1_b2_product_tilt_enabled": False,
    "s1_b2_tilt_strength": 0.0,
    "s1_b2_floor_weight": 0.50,
    "s1_b2_power": 1.50,
    "s1_b2_missing_side_penalty": 0.70,
    "s1_b2_missing_score": 20.0,
    "s1_b2_score_clip_low": 5.0,
    "s1_b2_score_clip_high": 95.0,
    "s1_b2_score_top_contracts_per_side": 5,
    "s1_b2_product_budget_diagnostics_enabled": True,
    "s1_b3_clean_vega_tilt_enabled": False,
    "s1_b3_tilt_strength": 0.0,
    "s1_b3_floor_weight": 0.50,
    "s1_b3_power": 1.50,
    "s1_b3_score_clip_low": 5.0,
    "s1_b3_score_clip_high": 95.0,
    "s1_b3_weight_b2": 0.60,
    "s1_b3_weight_forward_variance": 0.08,
    "s1_b3_weight_vol_of_vol": 0.08,
    "s1_b3_weight_iv_shock": 0.10,
    "s1_b3_weight_joint_stress": 0.06,
    "s1_b3_weight_vomma": 0.04,
    "s1_b3_weight_skew_stability": 0.04,
    "s1_b3_vov_lookback_short": 5,
    "s1_b3_vov_lookback_long": 20,
    "s1_b3_product_side_budget_diagnostics_enabled": True,
    "s1_b4_factor_role_enabled": False,
    "s1_b4_hard_filter_enabled": False,
    "s1_b4_contract_rank_enabled": False,
    "s1_b4_product_side_tilt_enabled": False,
    "s1_b4_vov_penalty_enabled": False,
    "s1_b4_breakeven_penalty_enabled": False,
    "s1_b4_min_net_premium_cash": 0.0,
    "s1_b4_max_friction_ratio": 0.20,
    "s1_b4_weight_premium_to_iv10": 0.30,
    "s1_b4_weight_premium_to_stress": 0.25,
    "s1_b4_weight_premium_yield_margin": 0.20,
    "s1_b4_weight_gamma_rent": 0.15,
    "s1_b4_weight_vomma": 0.10,
    "s1_b4_weight_breakeven_cushion": 0.0,
    "s1_b4_weight_vol_of_vol": 0.0,
    "s1_b4_product_weight_premium_to_stress": 0.35,
    "s1_b4_product_weight_premium_to_iv10": 0.30,
    "s1_b4_product_weight_premium_yield_margin": 0.20,
    "s1_b4_product_weight_gamma_rent": 0.15,
    "s1_b4_contract_breakeven_penalty_rank_low": 30.0,
    "s1_b4_contract_breakeven_penalty_rank_very_low": 15.0,
    "s1_b4_contract_breakeven_penalty_points_low": 10.0,
    "s1_b4_contract_breakeven_penalty_points_very_low": 20.0,
    "s1_b4_contract_vov_penalty_rank_low": 30.0,
    "s1_b4_contract_vov_penalty_rank_very_low": 15.0,
    "s1_b4_contract_vov_penalty_points_low": 10.0,
    "s1_b4_contract_vov_penalty_points_very_low": 20.0,
    "s1_b4_side_vov_penalty_rank_low": 30.0,
    "s1_b4_side_vov_penalty_rank_very_low": 15.0,
    "s1_b4_side_vov_penalty_mult_low": 0.85,
    "s1_b4_side_vov_penalty_mult_very_low": 0.70,
    "s1_b4_product_tilt_strength": 0.35,
    "s1_b4_floor_weight": 0.50,
    "s1_b4_power": 1.25,
    "s1_b4_score_clip_low": 5.0,
    "s1_b4_score_clip_high": 95.0,
    "s1_b4_product_side_budget_diagnostics_enabled": True,
    "s1_b6_hard_filter_enabled": False,
    "s1_b6_contract_rank_enabled": False,
    "s1_b6_min_net_premium_cash": 0.0,
    "s1_b6_max_friction_ratio": 0.20,
    "s1_b6_weight_premium_to_stress": 0.24,
    "s1_b6_weight_premium_to_iv10": 0.22,
    "s1_b6_weight_theta_per_vega": 0.22,
    "s1_b6_weight_theta_per_gamma": 0.12,
    "s1_b6_weight_tail_move_coverage": 0.10,
    "s1_b6_weight_vomma": 0.06,
    "s1_b6_weight_premium_yield_margin": 0.04,
    "s1_b6_side_tilt_enabled": False,
    "s1_b6_product_tilt_enabled": False,
    "s1_b6_side_tilt_strength": 0.25,
    "s1_b6_product_tilt_strength": 0.15,
    "s1_b6_side_floor_weight": 0.70,
    "s1_b6_product_floor_weight": 0.80,
    "s1_b6_side_power": 1.25,
    "s1_b6_product_power": 1.25,
    "s1_b6_score_clip_low": 5.0,
    "s1_b6_score_clip_high": 95.0,
    "s1_b6_missing_factor_score": 50.0,
    "s1_b6_side_multiplier_min": 0.70,
    "s1_b6_side_multiplier_max": 1.30,
    "s1_b6_product_multiplier_min": 0.80,
    "s1_b6_product_multiplier_max": 1.20,
    "s1_b6_side_weight_theta_per_vega": 0.35,
    "s1_b6_side_weight_premium_to_stress": 0.25,
    "s1_b6_side_weight_theta_per_gamma": 0.15,
    "s1_b6_side_weight_premium_to_margin": 0.10,
    "s1_b6_side_weight_vega_per_premium": 0.10,
    "s1_b6_side_weight_gamma_per_premium": 0.05,
    "s1_b6_product_weight_theta_per_vega": 0.45,
    "s1_b6_product_weight_premium_to_stress": 0.20,
    "s1_b6_product_weight_theta_per_gamma": 0.15,
    "s1_b6_product_weight_tail_beta": 0.10,
    "s1_b6_product_weight_gamma_per_premium": 0.10,
    "s1_b6_product_side_budget_diagnostics_enabled": True,
    "s1_b6_product_budget_diagnostics_enabled": True,
    "s1_b6_side_direction_penalty_enabled": True,
    "s1_b6_side_adverse_trend_z": 0.80,
    "s1_b6_side_adverse_trend_mult": 0.85,
    "s1_b6_side_breakout_rank_low": 30.0,
    "s1_b6_side_breakout_mult_low": 0.90,
    "s1_b6_side_skew_rank_low": 30.0,
    "s1_b6_side_skew_mult_low": 0.90,
    "s1_b6_side_cooldown_mult_floor": 0.70,
    "s1_b5_shadow_factor_extension_enabled": False,
    "s1_b5_delta_ladder_enabled": True,
    "s1_b5_product_side_trend_skew_enabled": True,
    "s1_b5_cooldown_state_enabled": True,
    "s1_b5_portfolio_panel_enabled": True,
    "s1_b5_tail_dependence_enabled": True,
    "s1_b5_tail_dependence_mode": "empirical_v1",
    "s1_b5_min_history_days": 60,
    "s1_b5_tail_window_days": 120,
    "s1_b5_tail_quantile": 0.05,
    "s1_b5_mae_lookback_days": 20,
    "s1_b5_trend_long_lookback_days": 60,
    "s1_b5_delta_bucket_edges": [0.00, 0.02, 0.04, 0.06, 0.08, 0.10],
    "s1_stress_spot_move_pct": 0.03,
    "s1_stress_iv_up_points": 5.0,
    "s1_stress_premium_loss_multiple": 0.0,
    "s1_use_stress_sizing": False,
    "s1_stress_loss_budget_pct": 0.0010,
    "s1_stress_min_qty": 1,
    "s1_stress_max_qty": 50,
    "s1_product_regime_budget_overrides_enabled": False,
    "s1_product_regime_budget_override_prefixes": ["falling"],
    "s1_product_regime_budget_clamp_non_release_enabled": False,
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

def should_open_new(product_df_today, dte_target=35, dte_min=15, dte_max=90,
                    mode="dte", expiry_rank=2):
    """
    检查今日是否应该触发开仓。

    返回满足条件的 expiry_date 列表，并按更接近目标 DTE、再按到期日稳定排序，
    避免相同行情下到期月处理顺序漂移。
    """
    if product_df_today.empty:
        return []

    mode = str(mode or "dte").strip().lower()
    if mode in {"next_month", "nth_expiry", "rank"}:
        expiries = []
        for exp in product_df_today["expiry_date"].dropna().unique():
            exp_data = product_df_today[product_df_today["expiry_date"] == exp]
            if exp_data.empty:
                continue
            dte = exp_data["dte"].iloc[0]
            if pd.isna(dte) or dte <= 0:
                continue
            expiries.append((str(exp), float(dte)))
        expiries.sort(key=lambda x: (x[1], x[0]))
        if not expiries:
            return []
        rank = max(1, int(expiry_rank or 1))
        if rank > len(expiries):
            return []
        return [expiries[rank - 1][0]]

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
