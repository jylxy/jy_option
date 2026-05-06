"""
基于 Toolkit 数据源的逐分钟回测引擎

数据来源：
  - option_basic_info: 合约属性（ths_code, strike_price, maturity_date, contract_type, contract_multiplier）
  - option_hf_1min_non_ror: 期权分钟K线（date, time, ths_code, OHLCV, open_interest）

标的价格：无期货行情数据，用 put-call parity 从同到期日的 ATM 期权对反推。

用法：
    python src/toolkit_minute_engine.py --start-date 2024-01-01 --end-date 2024-03-31
    python src/toolkit_minute_engine.py --start-date 2024-06-01 --end-date 2024-06-30 --products m,cu,au
"""
import os
import sys
import time
import logging
import argparse
import re
from datetime import datetime
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import warnings

# 抑制 py_vollib 的 Below Intrinsic 警告（深虚值期权正常现象）
warnings.filterwarnings('ignore', message='.*Below Intrinsic.*')
warnings.filterwarnings('ignore', message='.*Above Max Price.*')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toolkit.selector import select_bars_sql
from option_calc import calc_iv_single, calc_greeks_single, calc_iv_batch, RISK_FREE_RATE
from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy_by_otm, select_s3_sell_by_otm,
    select_s4,
    calc_s1_size, calc_s1_stress_size, calc_s3_size_v2, calc_s4_size,
    extract_atm_iv_series, calc_iv_percentile, calc_iv_rv_features, get_iv_scale,
    should_pause_open, should_close_expiry, should_open_new, can_reopen,
    should_allow_open_low_iv_product, calc_stats,
    choose_s1_option_sides, choose_s1_trend_confidence_sides,
    classify_s1_trend_confidence, s1_trend_side_adjustment,
    s1_forward_vega_quality_filter,
    DEFAULT_PARAMS,
)
from margin_model import estimate_margin, resolve_margin_ratio
from contract_provider import ContractInfo
from spot_provider import spot_tables_for_codes
from budget_model import (
    normalize_open_budget,
    get_effective_open_budget,
    pending_budget_fields,
    execution_budget_for_item,
)
from product_taxonomy import (
    normalize_product_key,
    get_product_bucket,
    get_product_corr_group,
)
from query_filters import (
    normalize_product_pool,
    build_product_like_sql,
)
from product_lifecycle import (
    product_first_trade_cache_path,
    coerce_trade_date_str,
    load_first_trade_cache,
    save_first_trade_cache,
    update_first_trade_dates,
    update_first_trade_dates_from_frame,
    product_observation_ready,
)
from position_model import Position
from execution_model import apply_execution_slippage
from open_execution import (
    build_open_execution_context,
    build_open_order_record,
    estimate_volume_weighted_close,
    scale_deferred_open_item,
    split_open_quantity,
)
from intraday_execution import (
    build_intraday_price_context,
    confirm_intraday_stop_price,
    index_intraday_positions,
    intraday_stop_required_volume,
    intraday_stop_threshold,
    is_intraday_stop_price_illiquid,
    prefilter_intraday_exit_codes_by_daily_high,
    resolve_stop_execution_price,
)
from stop_policy import (
    parse_s1_layered_stop_levels,
    s1_layer_level_key,
    select_s1_stop_scope_positions,
)
from s1_pending_open import build_s1_sell_pending_item
from broker_costs import resolve_option_fee, resolve_option_roundtrip_fee
from runtime_paths import OUTPUT_DIR, CONFIG_PATH, CACHE_DIR
from data_tables import OPTION_MINUTE_TABLE, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE
from result_output import write_backtest_outputs
from day_loader import ToolkitDayLoader
from config_loader import load_engine_config
from iv_warmup import (
    IVWarmupContext,
    get_warmup_contract_codes,
    load_iv_warmup_cache,
    save_iv_warmup_cache,
    warmup_cache_path,
    warmup_iv_consistent,
)
import portfolio_risk as port_risk
import vol_regime as vol_rules

logger = logging.getLogger(__name__)

class ToolkitMinuteEngine:
    """基于 Toolkit 数据源的回测引擎"""

    def __init__(self, config_path=None):
        self.config = self._load_config(config_path or CONFIG_PATH)
        if not self.config.get('intraday_risk_interval'):
            self.config['intraday_risk_interval'] = 15
        self.capital = self.config.get('capital', 10_000_000)
        self.ci = ContractInfo()
        self.ci.load()
        self.loader = ToolkitDayLoader(self.ci)
        self.positions = []
        self.nav_records = []
        self.diagnostics_records = []
        self.orders = []
        self._iv_history = defaultdict(lambda: {'dates': [], 'ivs': []})
        self._spot_history = defaultdict(lambda: {'dates': [], 'spots': []})
        self._contract_iv_history = defaultdict(lambda: {'dates': [], 'ivs': [], 'prices': []})
        self._day_realized = {'pnl': 0.0, 'fee': 0.0, 's1': 0.0, 's3': 0.0, 's4': 0.0}
        self._day_attr_realized = self._zero_attr_bucket()
        self._current_date_str = ''
        self._pending_opens = []
        self._current_iv_pcts = {}
        self._current_iv_state = {}
        self._current_vol_regimes = {}
        self._current_vol_regime_counts = Counter()
        self._current_portfolio_regime = 'normal_vol'
        self._current_open_budget = {}
        self._reentry_plans = {}
        self._stop_history = defaultdict(list)
        self._stop_side_history = defaultdict(list)
        self._layered_stop_done = defaultdict(set)
        self._product_first_trade_dates = {}
        self._product_like_sql_cache = {}
        self._warmup_contract_sql_cache = {}
        self._s1_candidate_funnel = None
        self.s1_candidate_records = []
        self.s1_candidate_outcomes = []
        self._s1_shadow_candidates = []
        self._s1_candidate_next_id = 1

        # 策略参数（与strategy_rules.py DEFAULT_PARAMS一致，此处显式声明便于查看）
        # DTE 30-45（次月合约），止盈50%不重开
        # 30品种时 margin_per 调低，避免保证金上限卡住太多品种

    _ENTRY_META_FIELDS = (
        'signal_date',
        'signal_ref_price', 'execution_price_drift',
        'premium_stress', 'theta_stress', 'premium_margin',
        'signal_premium_stress', 'signal_theta_stress', 'signal_premium_margin',
        'premium_yield_margin', 'premium_yield_notional',
        'rv_ref', 'iv_rv_spread_candidate', 'iv_rv_ratio_candidate',
        'variance_carry',
        'breakeven_price', 'breakeven_cushion_abs',
        'breakeven_cushion_iv', 'breakeven_cushion_rv',
        'iv_shock_loss_5_cash', 'iv_shock_loss_10_cash',
        'premium_to_iv5_loss', 'premium_to_iv10_loss',
        'premium_to_stress_loss',
        'theta_vega_efficiency',
        'gamma_rent_cash', 'gamma_rent_penalty',
        'fee_ratio', 'slippage_ratio', 'friction_ratio',
        'premium_quality_score', 'premium_quality_rank_in_side',
        'iv_rv_carry_score', 'breakeven_cushion_score',
        'premium_to_iv_shock_score', 'premium_to_stress_loss_score',
        'theta_vega_efficiency_score', 'cost_liquidity_score',
        'b3_forward_variance_pressure', 'b3_vol_of_vol_proxy',
        'b3_vov_trend', 'b3_iv_shock_coverage',
        'b3_joint_stress_coverage', 'b3_vomma_cash',
        'b3_vomma_loss_ratio', 'b3_skew_steepening',
        'b3_clean_vega_score',
        'b3_forward_variance_score', 'b3_vol_of_vol_score',
        'b3_iv_shock_score', 'b3_joint_stress_score',
        'b3_vomma_score', 'b3_skew_stability_score',
        'open_fee_per_contract', 'close_fee_per_contract',
        'roundtrip_fee_per_contract',
        'abs_delta', 'delta', 'gamma', 'vega', 'theta',
        'volume', 'open_interest', 'moneyness', 'liquidity_score',
        'vol_regime', 'selection_score', 'selection_rank',
        'entry_atm_iv', 'entry_iv_pct', 'entry_iv_trend', 'entry_rv_trend',
        'entry_iv_rv_spread', 'entry_iv_rv_ratio',
        'contract_iv', 'contract_iv_change_1d', 'contract_iv_change_3d',
        'contract_iv_change_5d', 'contract_iv_change_for_vega',
        'contract_iv_skew_to_atm', 'contract_skew_change_for_vega',
        'contract_price_change_1d',
        'effective_margin_cap', 'effective_strategy_margin_cap',
        'effective_product_margin_cap', 'effective_product_side_margin_cap',
        'effective_bucket_margin_cap', 'effective_corr_group_margin_cap',
        'effective_stress_loss_cap', 'effective_bucket_stress_loss_cap',
        'effective_product_side_stress_loss_cap',
        'effective_corr_group_stress_loss_cap',
        'effective_contract_stress_loss_cap',
        'open_budget_risk_scale', 'open_budget_brake_reason',
        'trend_state', 'trend_score', 'trend_confidence',
        'trend_range_position', 'trend_range_pressure',
        'trend_role', 'side_score_mult', 'side_budget_mult', 'side_delta_cap',
        'ladder_candidate_count', 'ladder_delta_gap', 'effective_s1_stress_max_qty',
        'b2_product_score', 'b2_product_equal_budget_pct',
        'b2_product_quality_budget_pct', 'b2_product_final_budget_pct',
        'b2_product_budget_mult',
        'b3_product_side_score', 'b3_side_equal_budget_pct',
        'b3_side_quality_budget_pct', 'b3_side_final_budget_pct',
        'b3_side_budget_mult', 'b3_clean_vega_tilt_strength',
        'b4_contract_score', 'b4_product_side_score',
        'b4_side_equal_budget_pct', 'b4_side_quality_budget_pct',
        'b4_side_final_budget_pct', 'b4_side_budget_mult',
        'b4_product_tilt_strength', 'b4_side_vov_penalty_mult',
        'b4_premium_to_iv10_score', 'b4_premium_to_stress_score',
        'b4_premium_yield_margin_score', 'b4_gamma_rent_score',
        'b4_vomma_score', 'b4_breakeven_cushion_score',
        'b4_vol_of_vol_score',
        'b6_contract_score', 'b6_product_score', 'b6_product_side_score',
        'b6_product_equal_budget_pct', 'b6_product_quality_budget_pct',
        'b6_product_final_budget_pct', 'b6_product_budget_mult',
        'b6_side_equal_budget_pct', 'b6_side_quality_budget_pct',
        'b6_side_final_budget_pct', 'b6_side_budget_mult',
        'b6_product_tilt_strength', 'b6_side_tilt_strength',
        'b6_side_direction_penalty_mult',
        'b6_premium_to_stress_score', 'b6_premium_to_iv10_score',
        'b6_theta_per_vega_score', 'b6_theta_per_gamma_score',
        'b6_tail_move_coverage_score', 'b6_vomma_score',
        'b6_premium_yield_margin_score',
    )

    _S1_B5_CANDIDATE_FIELDS = (
        'b5_delta_bucket', 'b5_delta_to_cap', 'b5_delta_ratio_to_cap',
        'b5_rank_in_delta_bucket', 'b5_delta_bucket_candidate_count',
        'b5_premium_share_delta_bucket', 'b5_stress_share_delta_bucket',
        'b5_theta_per_gamma', 'b5_gamma_theta_ratio',
        'b5_theta_per_vega', 'b5_premium_per_vega',
        'b5_expected_move_pct', 'b5_expected_move_loss_cash',
        'b5_premium_to_expected_move_loss',
        'b5_mae20_move_pct', 'b5_mae20_loss_cash',
        'b5_premium_to_mae20_loss',
        'b5_tail_move_pct', 'b5_tail_move_loss_cash',
        'b5_premium_to_tail_move_loss',
        'b5_mom_5d', 'b5_mom_20d', 'b5_mom_60d',
        'b5_trend_z_20d',
        'b5_breakout_distance_up_60d',
        'b5_breakout_distance_down_60d',
        'b5_up_day_ratio_20d', 'b5_down_day_ratio_20d',
        'b5_range_expansion_proxy_20d',
        'b5_atm_iv_mom_5d', 'b5_atm_iv_mom_20d',
        'b5_atm_iv_accel', 'b5_iv_zscore_60d',
        'b5_iv_reversion_score',
        'b5_days_since_product_stop', 'b5_product_stop_count_20d',
        'b5_days_since_product_side_stop',
        'b5_product_side_stop_count_20d',
        'b5_cooldown_blocked', 'b5_cooldown_penalty_score',
        'b5_cooldown_release_score',
        'b5_tick_value_ratio', 'b5_low_price_flag',
        'b5_variance_carry_forward',
        'b5_capital_lockup_days', 'b5_premium_per_capital_day',
    )

    def _entry_meta_from_item(self, item):
        return {key: item.get(key, np.nan) for key in self._ENTRY_META_FIELDS}

    def _option_fee_per_contract(self, product, option_type=None, action='open',
                                 default=None):
        return resolve_option_fee(
            self.config,
            product=product,
            option_type=option_type,
            action=action,
            default=default,
        )

    def _option_roundtrip_fee_per_contract(self, product, option_type=None):
        return resolve_option_roundtrip_fee(
            self.config,
            product=product,
            option_type=option_type,
        )

    def _position_fee_per_contract(self, pos, action='close', default=None):
        action = str(action or 'close').lower()
        meta = getattr(pos, 'entry_meta', {}) or {}
        meta_key = {
            'open': 'open_fee_per_contract',
            'close': 'close_fee_per_contract',
        }.get(action, f'{action}_fee_per_contract')
        if meta_key in meta:
            value = self._safe_float(meta.get(meta_key), np.nan)
            if np.isfinite(value):
                return value
        return self._option_fee_per_contract(
            pos.product,
            pos.opt_type,
            action=action,
            default=default,
        )

    def _position_roundtrip_fee_per_side(self, pos, default=None):
        open_fee = self._position_fee_per_contract(pos, action='open', default=default)
        close_fee = self._position_fee_per_contract(pos, action='close', default=default)
        return 0.5 * (open_fee + close_fee)

    def _load_config(self, path):
        return load_engine_config(path, DEFAULT_PARAMS, logger=logger)

    @staticmethod
    def _zero_attr_bucket():
        return {
            'delta_pnl': 0.0,
            'gamma_pnl': 0.0,
            'theta_pnl': 0.0,
            'vega_pnl': 0.0,
            'residual_pnl': 0.0,
        }

    @staticmethod
    def _spot_tables_for_codes(underlying_codes):
        return spot_tables_for_codes(underlying_codes, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE)

    def _reentry_key(self, strat, product, opt_type=None):
        return vol_rules.reentry_key(strat, product, opt_type)

    def _shift_trading_date(self, date_str, offset):
        if offset <= 0:
            return date_str
        dates = self.loader.get_trading_dates()
        try:
            idx = dates.index(date_str)
        except ValueError:
            return date_str
        return dates[min(idx + offset, len(dates) - 1)]

    @staticmethod
    def _sample_intraday_times(time_points, interval_minutes):
        if not time_points:
            return []
        interval = max(int(interval_minutes or 1), 1)
        if interval <= 1:
            return list(time_points)

        selected = []
        last_bucket = None
        for tm in time_points:
            ts = pd.Timestamp(tm)
            bucket = (ts.hour * 60 + ts.minute) // interval
            if bucket != last_bucket:
                selected.append(tm)
                last_bucket = bucket
        if selected[-1] != time_points[-1]:
            selected.append(time_points[-1])
        return selected

    def _register_reentry_plan(self, pos, date_str):
        plan = vol_rules.register_reentry_plan(
            pos,
            date_str,
            config=self.config,
            stop_history=self._stop_history,
            reentry_plans=self._reentry_plans,
            shift_trading_date=self._shift_trading_date,
            normalize_product_key=self._normalize_product_key,
        )
        product = self._normalize_product_key(getattr(pos, 'product', ''))
        opt_type = str(getattr(pos, 'opt_type', '') or '').upper()
        if product and opt_type:
            self._stop_side_history[(product, opt_type)].append(str(date_str))
        return plan

    def _product_iv_turns_lower(self, product):
        return vol_rules.product_iv_turns_lower(self._iv_history, product)

    def _product_iv_not_falling(self, product):
        return vol_rules.product_iv_not_falling(self._iv_history, product)

    def _reentry_requires_falling_regime(self, strat):
        return vol_rules.reentry_requires_falling_regime(self.config, strat)

    def _reentry_requires_daily_iv_drop(self, strat):
        return vol_rules.reentry_requires_daily_iv_drop(self.config, strat)

    def _reentry_plan_blocks(self, strat, product, opt_type, date_str, plan=None, base_regime=None):
        plan = plan or self._reentry_plans.get(self._reentry_key(strat, product, opt_type))
        return vol_rules.reentry_plan_blocks(
            self.config,
            plan=plan,
            strat=strat,
            product=product,
            date_str=date_str,
            iv_history=self._iv_history,
            current_iv_state=self._current_iv_state,
            base_regime=base_regime,
        )

    def _should_trigger_premium_stop(self, pos, product_iv_pcts=None):
        return vol_rules.should_trigger_premium_stop(
            self.config,
            pos,
            product_iv_pcts,
            self._iv_history,
        )

    def _premium_stop_hit(self, pos, multiple, product_iv_pcts=None):
        multiple = float(multiple or 0.0)
        if multiple <= 0 or pos.cur_price < pos.open_price * multiple:
            return False
        require_daily_iv = bool(self.config.get("premium_stop_requires_daily_iv_non_decrease", True))
        if not require_daily_iv:
            return True
        product_iv_pcts = product_iv_pcts or {}
        if not product_iv_pcts or pos.product not in product_iv_pcts:
            return True
        return vol_rules.product_iv_not_falling(self._iv_history, pos.product)

    def _is_reentry_blocked(self, strat, product, opt_type, date_str):
        plan = self._reentry_plans.get(self._reentry_key(strat, product, opt_type))
        if not plan:
            return False
        return self._reentry_plan_blocks(strat, product, opt_type, date_str, plan=plan)

    def _get_reentry_plan(self, strat, product, opt_type, date_str):
        plan = self._reentry_plans.get(self._reentry_key(strat, product, opt_type))
        if not plan:
            return None
        if self._reentry_plan_blocks(strat, product, opt_type, date_str, plan=plan):
            return None
        return plan

    def _clear_reentry_plan(self, strat, product, opt_type):
        self._reentry_plans.pop(self._reentry_key(strat, product, opt_type), None)

    def _last_iv_trend(self, product, lookback=5):
        return vol_rules.last_iv_trend(self._iv_history, product, lookback=lookback)

    def _update_contract_iv_history(self, daily_df, date_str):
        if not self.config.get('s1_track_contract_iv_trend', True):
            return
        cols = {'option_code', 'implied_vol', 'option_close'}
        if daily_df.empty or not cols.issubset(set(daily_df.columns)):
            return
        rows = daily_df[['option_code', 'implied_vol', 'option_close']].copy()
        rows['implied_vol'] = pd.to_numeric(rows['implied_vol'], errors='coerce')
        rows['option_close'] = pd.to_numeric(rows['option_close'], errors='coerce')
        rows = rows[
            rows['option_code'].notna() &
            (rows['implied_vol'] > 0) &
            (rows['option_close'] > 0)
        ]
        if rows.empty:
            return
        for row in rows.itertuples(index=False):
            code = str(row.option_code)
            hist = self._contract_iv_history[code]
            iv = float(row.implied_vol)
            price = float(row.option_close)
            if hist['dates'] and hist['dates'][-1] == date_str:
                hist['ivs'][-1] = iv
                hist['prices'][-1] = price
            else:
                hist['dates'].append(date_str)
                hist['ivs'].append(iv)
                hist['prices'].append(price)

    def _contract_trend_state(self, option_code):
        hist = self._contract_iv_history.get(str(option_code))
        state = {
            'contract_iv': np.nan,
            'contract_iv_change_1d': np.nan,
            'contract_iv_change_3d': np.nan,
            'contract_iv_change_5d': np.nan,
            'contract_price': np.nan,
            'contract_price_change_1d': np.nan,
            'contract_price_change_3d': np.nan,
            'contract_price_change_5d': np.nan,
        }
        if not hist:
            return state
        ivs = pd.to_numeric(pd.Series(hist.get('ivs', [])), errors='coerce')
        prices = pd.to_numeric(pd.Series(hist.get('prices', [])), errors='coerce')
        if len(ivs) >= 1 and pd.notna(ivs.iloc[-1]):
            state['contract_iv'] = float(ivs.iloc[-1])
        if len(prices) >= 1 and pd.notna(prices.iloc[-1]):
            state['contract_price'] = float(prices.iloc[-1])
        if len(ivs) >= 2 and pd.notna(ivs.iloc[-1]) and pd.notna(ivs.iloc[-2]):
            state['contract_iv_change_1d'] = float(ivs.iloc[-1] - ivs.iloc[-2])
        if len(prices) >= 2 and pd.notna(prices.iloc[-1]) and pd.notna(prices.iloc[-2]):
            prev = float(prices.iloc[-2])
            if prev > 0:
                state['contract_price_change_1d'] = float(prices.iloc[-1] / prev - 1.0)
        for lookback in (3, 5):
            if len(ivs) > lookback and pd.notna(ivs.iloc[-1]) and pd.notna(ivs.iloc[-1 - lookback]):
                state[f'contract_iv_change_{lookback}d'] = float(ivs.iloc[-1] - ivs.iloc[-1 - lookback])
            if len(prices) > lookback and pd.notna(prices.iloc[-1]) and pd.notna(prices.iloc[-1 - lookback]):
                prev = float(prices.iloc[-1 - lookback])
                if prev > 0:
                    state[f'contract_price_change_{lookback}d'] = float(prices.iloc[-1] / prev - 1.0)
        return state

    def _prepare_s1_selection_frame(self, ef, option_type):
        side = ef[ef['option_type'] == option_type].copy()
        if side.empty or not self.config.get('s1_track_contract_iv_trend', True):
            return side

        trend_rows = [self._contract_trend_state(code) for code in side['option_code']]
        trend_df = pd.DataFrame(trend_rows, index=side.index)
        for col in trend_df.columns:
            side[col] = trend_df[col]

        cfg = self.config
        if cfg.get('s1_require_contract_iv_not_rising', False):
            max_iv_change = float(cfg.get('s1_contract_iv_max_change_1d', 0.0) or 0.0)
            missing_policy = str(cfg.get('s1_contract_iv_missing_policy', 'allow')).lower()
            change = pd.to_numeric(side['contract_iv_change_1d'], errors='coerce')
            known_ok = change.notna() & (change <= max_iv_change)
            missing_ok = change.isna() & (missing_policy != 'skip')
            side = side[known_ok | missing_ok].copy()

        if side.empty:
            return side

        if cfg.get('s1_require_contract_price_not_rising', False):
            max_price_change = float(cfg.get('s1_contract_price_max_change_1d', 0.10) or 0.0)
            change = pd.to_numeric(side['contract_price_change_1d'], errors='coerce')
            missing_policy = str(cfg.get('s1_contract_iv_missing_policy', 'allow')).lower()
            known_ok = change.notna() & (change <= max_price_change)
            missing_ok = change.isna() & (missing_policy != 'skip')
            side = side[known_ok | missing_ok].copy()

        return side

    def _filter_s1_forward_vega_candidates(self, candidates, product, ot,
                                           iv_state=None, side_meta=None):
        """Apply causal wing-IV quality gates before committing S1 short vega."""
        iv_context = dict(iv_state or {})
        iv_context['vol_regime'] = self._current_vol_regimes.get(product, '')
        filtered, stats = s1_forward_vega_quality_filter(
            candidates,
            ot,
            iv_state=iv_context,
            side_meta=side_meta or {},
            config=self.config,
        )
        for key, value in stats.items():
            if value:
                self._bump_s1_funnel(key, value)
        return filtered

    def _has_active_reentry_plan(self, product, date_str, base_regime=None):
        return vol_rules.has_active_reentry_plan(
            self.config,
            product=product,
            date_str=date_str,
            reentry_plans=self._reentry_plans,
            iv_history=self._iv_history,
            current_iv_state=self._current_iv_state,
            normalize_product_key=self._normalize_product_key,
            base_regime=base_regime,
        )

    def _classify_product_vol_regime_base(self, product, state):
        return vol_rules.classify_product_vol_regime_base(self.config, state)

    def _classify_product_vol_regime(self, product, state, date_str):
        return vol_rules.classify_product_vol_regime(
            self.config,
            product=product,
            state=state,
            date_str=date_str,
            reentry_plans=self._reentry_plans,
            iv_history=self._iv_history,
            current_iv_state=self._current_iv_state,
            normalize_product_key=self._normalize_product_key,
        )

    def _is_structural_low_iv_product(self, product, state=None):
        return vol_rules.is_structural_low_iv_product(
            self.config,
            product=product,
            iv_history=self._iv_history,
            current_iv_state=self._current_iv_state,
            normalize_product_key=self._normalize_product_key,
            state=state,
        )

    def _refresh_vol_regime_state(self, date_str):
        regimes, counts, portfolio_regime = vol_rules.refresh_vol_regime_state(
            self.config,
            current_iv_state=self._current_iv_state,
            reentry_plans=self._reentry_plans,
            iv_history=self._iv_history,
            normalize_product_key=self._normalize_product_key,
            date_str=date_str,
        )
        self._current_vol_regimes = regimes
        self._current_vol_regime_counts = counts
        self._current_portfolio_regime = portfolio_regime
        return regimes

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return float(default)
        return value if np.isfinite(value) else float(default)

    @classmethod
    def _safe_pct(cls, value, default=0.0, upper=1.0):
        value = cls._safe_float(value, default)
        if value < 0:
            return 0.0
        if upper is not None:
            return min(value, float(upper))
        return value

    def _s1_b4_params(self):
        cfg = self.config
        return {
            'hard_filter_enabled': bool(cfg.get('s1_b4_hard_filter_enabled', False)),
            'min_net_premium_cash': float(cfg.get('s1_b4_min_net_premium_cash', 0.0) or 0.0),
            'max_friction_ratio': cfg.get('s1_b4_max_friction_ratio', 0.20),
            'weight_premium_to_iv10': float(cfg.get('s1_b4_weight_premium_to_iv10', 0.30) or 0.0),
            'weight_premium_to_stress': float(cfg.get('s1_b4_weight_premium_to_stress', 0.25) or 0.0),
            'weight_premium_yield_margin': float(cfg.get('s1_b4_weight_premium_yield_margin', 0.20) or 0.0),
            'weight_gamma_rent': float(cfg.get('s1_b4_weight_gamma_rent', 0.15) or 0.0),
            'weight_vomma': float(cfg.get('s1_b4_weight_vomma', 0.10) or 0.0),
            'breakeven_penalty_enabled': bool(cfg.get('s1_b4_breakeven_penalty_enabled', False)),
            'vov_penalty_enabled': bool(cfg.get('s1_b4_vov_penalty_enabled', False)),
            'breakeven_penalty_rank_low': float(
                cfg.get('s1_b4_contract_breakeven_penalty_rank_low', 30.0) or 30.0
            ),
            'breakeven_penalty_rank_very_low': float(
                cfg.get('s1_b4_contract_breakeven_penalty_rank_very_low', 15.0) or 15.0
            ),
            'breakeven_penalty_points_low': float(
                cfg.get('s1_b4_contract_breakeven_penalty_points_low', 10.0) or 10.0
            ),
            'breakeven_penalty_points_very_low': float(
                cfg.get('s1_b4_contract_breakeven_penalty_points_very_low', 20.0) or 20.0
            ),
            'vov_penalty_rank_low': float(cfg.get('s1_b4_contract_vov_penalty_rank_low', 30.0) or 30.0),
            'vov_penalty_rank_very_low': float(
                cfg.get('s1_b4_contract_vov_penalty_rank_very_low', 15.0) or 15.0
            ),
            'vov_penalty_points_low': float(cfg.get('s1_b4_contract_vov_penalty_points_low', 10.0) or 10.0),
            'vov_penalty_points_very_low': float(
                cfg.get('s1_b4_contract_vov_penalty_points_very_low', 20.0) or 20.0
            ),
        }

    def _s1_b6_params(self):
        cfg = self.config
        return {
            'hard_filter_enabled': bool(cfg.get('s1_b6_hard_filter_enabled', False)),
            'min_net_premium_cash': float(cfg.get('s1_b6_min_net_premium_cash', 0.0) or 0.0),
            'max_friction_ratio': cfg.get('s1_b6_max_friction_ratio', 0.20),
            'weight_premium_to_stress': float(cfg.get('s1_b6_weight_premium_to_stress', 0.24) or 0.0),
            'weight_premium_to_iv10': float(cfg.get('s1_b6_weight_premium_to_iv10', 0.22) or 0.0),
            'weight_theta_per_vega': float(cfg.get('s1_b6_weight_theta_per_vega', 0.22) or 0.0),
            'weight_theta_per_gamma': float(cfg.get('s1_b6_weight_theta_per_gamma', 0.12) or 0.0),
            'weight_tail_move_coverage': float(
                cfg.get('s1_b6_weight_tail_move_coverage', 0.10) or 0.0
            ),
            'weight_vomma': float(cfg.get('s1_b6_weight_vomma', 0.06) or 0.0),
            'weight_premium_yield_margin': float(
                cfg.get('s1_b6_weight_premium_yield_margin', 0.04) or 0.0
            ),
            'missing_factor_score': float(cfg.get('s1_b6_missing_factor_score', 50.0) or 50.0),
        }

    def _s1_candidate_universe_enabled(self):
        return bool(self.config.get('s1_candidate_universe_dump_enabled', False))

    def _s1_candidate_signal_in_scope(self, date_str):
        start = self.config.get('s1_candidate_universe_signal_start_date')
        end = self.config.get('s1_candidate_universe_signal_end_date')
        text = str(date_str)
        if start and text < str(start):
            return False
        if end and text > str(end):
            return False
        return True

    def _s1_candidate_after_signal_window(self, date_str):
        end = self.config.get('s1_candidate_universe_signal_end_date')
        return bool(end and str(date_str) > str(end))

    def _s1_candidate_shadow_enabled(self):
        return (
            self._s1_candidate_universe_enabled()
            and bool(self.config.get('s1_candidate_universe_shadow_enabled', False))
        )

    def _next_s1_candidate_id(self):
        candidate_id = self._s1_candidate_next_id
        self._s1_candidate_next_id += 1
        return candidate_id

    def _s1_candidate_universe_max_candidates(self):
        raw = self.config.get('s1_candidate_universe_max_candidates_per_side', 0)
        try:
            return max(0, int(raw or 0))
        except (TypeError, ValueError):
            return 0

    def _s1_b5_shadow_enabled(self):
        return bool(self.config.get('s1_b5_shadow_factor_extension_enabled', False))

    def _s1_b6_enabled(self):
        mode = str(self.config.get('s1_ranking_mode', '') or '').lower()
        return (
            mode in {'b6', 'b6_residual_quality', 'b6_contract', 'b6_role'}
            or bool(self.config.get('s1_b6_contract_rank_enabled', False))
            or bool(self.config.get('s1_b6_side_tilt_enabled', False))
            or bool(self.config.get('s1_b6_product_tilt_enabled', False))
        )

    @staticmethod
    def _safe_divide(numerator, denominator):
        numerator = float(numerator) if np.isfinite(numerator) else np.nan
        denominator = float(denominator) if np.isfinite(denominator) else np.nan
        if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
            return np.nan
        return numerator / denominator

    def _history_series(self, history_map, product, value_key):
        hist = history_map.get(product)
        if not hist:
            hist = history_map.get(self._normalize_product_key(product))
        if not hist:
            return pd.Series(dtype=float)
        values = pd.to_numeric(pd.Series(hist.get(value_key, [])), errors='coerce')
        dates = hist.get('dates', [])
        if len(values) == 0 or len(dates) != len(values):
            return pd.Series(dtype=float)
        out = pd.Series(values.values, index=pd.to_datetime(dates, errors='coerce'))
        out = out[out.index.notna()].dropna()
        return out[~out.index.duplicated(keep='last')].sort_index()

    def _b5_product_history_features(self, product, option_type, dte):
        spots = self._history_series(self._spot_history, product, 'spots')
        fields = {
            'b5_mom_5d': np.nan,
            'b5_mom_20d': np.nan,
            'b5_mom_60d': np.nan,
            'b5_trend_z_20d': np.nan,
            'b5_breakout_distance_up_60d': np.nan,
            'b5_breakout_distance_down_60d': np.nan,
            'b5_up_day_ratio_20d': np.nan,
            'b5_down_day_ratio_20d': np.nan,
            'b5_range_expansion_proxy_20d': np.nan,
            'b5_mae20_move_pct': np.nan,
            'b5_tail_move_pct': np.nan,
        }
        if spots.empty:
            return fields
        spots = spots[spots > 0]
        if spots.empty:
            return fields
        cur = float(spots.iloc[-1])
        for lb in (5, 20, 60):
            if len(spots) > lb and spots.iloc[-1 - lb] > 0:
                fields[f'b5_mom_{lb}d'] = float(cur / spots.iloc[-1 - lb] - 1.0)

        returns = spots.pct_change(fill_method=None).dropna()
        if len(returns) >= 5:
            recent_abs = returns.abs()
            if len(recent_abs) >= 21:
                denom = float(recent_abs.iloc[-21:-1].mean())
                if denom > 0:
                    fields['b5_range_expansion_proxy_20d'] = float(recent_abs.iloc[-1] / denom)
        if len(returns) >= 20:
            r20 = returns.iloc[-20:]
            fields['b5_up_day_ratio_20d'] = float((r20 > 0).mean())
            fields['b5_down_day_ratio_20d'] = float((r20 < 0).mean())
            vol20 = float(r20.std(ddof=0) * np.sqrt(20.0))
            if vol20 > 0 and np.isfinite(fields['b5_mom_20d']):
                fields['b5_trend_z_20d'] = float(fields['b5_mom_20d'] / vol20)
            if str(option_type).upper() == 'P':
                fields['b5_mae20_move_pct'] = float(max(-r20.min(), 0.0))
            else:
                fields['b5_mae20_move_pct'] = float(max(r20.max(), 0.0))
        if len(returns) >= 60:
            tail_q = float(self.config.get('s1_b5_tail_quantile', 0.05) or 0.05)
            tail_q = min(max(tail_q, 0.01), 0.49)
            r = returns.iloc[-int(self.config.get('s1_b5_tail_window_days', 120) or 120):]
            if str(option_type).upper() == 'P':
                fields['b5_tail_move_pct'] = float(max(-r.quantile(tail_q), 0.0))
            else:
                fields['b5_tail_move_pct'] = float(max(r.quantile(1.0 - tail_q), 0.0))

        long_lb = int(self.config.get('s1_b5_trend_long_lookback_days', 60) or 60)
        if len(spots) >= max(long_lb, 2):
            window = spots.iloc[-long_lb:]
            high = float(window.max())
            low = float(window.min())
            if cur > 0:
                fields['b5_breakout_distance_up_60d'] = float(high / cur - 1.0)
                fields['b5_breakout_distance_down_60d'] = float(1.0 - low / cur)
        return fields

    def _b5_iv_history_features(self, product):
        ivs = self._history_series(self._iv_history, product, 'ivs')
        fields = {
            'b5_atm_iv_mom_5d': np.nan,
            'b5_atm_iv_mom_20d': np.nan,
            'b5_atm_iv_accel': np.nan,
            'b5_iv_zscore_60d': np.nan,
            'b5_iv_reversion_score': np.nan,
        }
        if ivs.empty:
            return fields
        cur = float(ivs.iloc[-1])
        if len(ivs) > 5:
            fields['b5_atm_iv_mom_5d'] = float(cur - ivs.iloc[-6])
        if len(ivs) > 20:
            fields['b5_atm_iv_mom_20d'] = float(cur - ivs.iloc[-21])
        if np.isfinite(fields['b5_atm_iv_mom_5d']) and np.isfinite(fields['b5_atm_iv_mom_20d']):
            fields['b5_atm_iv_accel'] = float(fields['b5_atm_iv_mom_5d'] - fields['b5_atm_iv_mom_20d'] * 0.25)
        if len(ivs) >= 60:
            win = ivs.iloc[-60:]
            std = float(win.std(ddof=0))
            if std > 0:
                z = float((cur - win.mean()) / std)
                fields['b5_iv_zscore_60d'] = z
                mom_penalty = max(float(fields.get('b5_atm_iv_mom_5d') or 0.0), 0.0)
                fields['b5_iv_reversion_score'] = z - 10.0 * mom_penalty
        return fields

    def _b5_stop_state_fields(self, product, option_type, date_str):
        product_key = self._normalize_product_key(product)
        side_key = (product_key, str(option_type).upper())
        current = pd.Timestamp(date_str)

        def summarize_stop_dates(dates):
            clean = [pd.Timestamp(d) for d in dates if str(d)]
            clean = [d for d in clean if d <= current]
            if not clean:
                return np.nan, 0
            last = max(clean)
            count20 = sum((current - d).days <= 20 for d in clean)
            return int((current - last).days), int(count20)

        days_product, count_product = summarize_stop_dates(self._stop_history.get(product_key, []))
        days_side, count_side = summarize_stop_dates(self._stop_side_history.get(side_key, []))
        blocked = 1.0 if self._is_reentry_blocked('S1', product, option_type, date_str) else 0.0
        penalty = 0.0
        if np.isfinite(days_side):
            penalty = max(penalty, max(0.0, 1.0 - float(days_side) / 20.0))
        if np.isfinite(days_product):
            penalty = max(penalty, 0.5 * max(0.0, 1.0 - float(days_product) / 20.0))
        iv_trend = self._last_iv_trend(product)
        release = 1.0
        if blocked:
            release = 0.0
        elif np.isfinite(iv_trend) and iv_trend > 0:
            release = max(0.0, 1.0 - min(iv_trend / 0.02, 1.0))
        return {
            'b5_days_since_product_stop': days_product,
            'b5_product_stop_count_20d': count_product,
            'b5_days_since_product_side_stop': days_side,
            'b5_product_side_stop_count_20d': count_side,
            'b5_cooldown_blocked': blocked,
            'b5_cooldown_penalty_score': penalty,
            'b5_cooldown_release_score': release,
        }

    @staticmethod
    def _b5_loss_for_move(row, move_pct):
        try:
            move_pct = float(move_pct)
        except (TypeError, ValueError):
            return np.nan
        if not np.isfinite(move_pct) or move_pct <= 0:
            return np.nan
        spot = pd.to_numeric(row.get('spot_close', row.get('spot', np.nan)), errors='coerce')
        mult = pd.to_numeric(row.get('multiplier', row.get('mult', np.nan)), errors='coerce')
        delta = abs(pd.to_numeric(row.get('delta', np.nan), errors='coerce'))
        gamma = abs(pd.to_numeric(row.get('gamma', np.nan), errors='coerce'))
        if not np.isfinite(spot) or not np.isfinite(mult) or spot <= 0 or mult <= 0:
            return np.nan
        d_spot = spot * move_pct
        delta_loss = (delta if np.isfinite(delta) else 0.0) * d_spot * mult
        gamma_loss = 0.5 * (gamma if np.isfinite(gamma) else 0.0) * d_spot * d_spot * mult
        loss = float(delta_loss + gamma_loss)
        return loss if loss > 0 else np.nan

    def _s1_b5_delta_bucket(self, abs_delta):
        try:
            abs_delta = float(abs_delta)
        except (TypeError, ValueError):
            return ''
        if not np.isfinite(abs_delta):
            return ''
        raw_edges = self.config.get('s1_b5_delta_bucket_edges', [0, 0.02, 0.04, 0.06, 0.08, 0.10])
        edges = []
        for value in raw_edges:
            try:
                edges.append(float(value))
            except (TypeError, ValueError):
                continue
        edges = sorted(set(edges))
        if len(edges) < 2:
            edges = [0, 0.02, 0.04, 0.06, 0.08, 0.10]
        for left, right in zip(edges[:-1], edges[1:]):
            if abs_delta >= left and abs_delta <= right:
                return f"{left:.2f}_{right:.2f}"
        return 'above_cap' if abs_delta > edges[-1] else 'below_floor'

    def _add_s1_b5_shadow_fields(self, candidates, date_str, product, exp, option_type,
                                 force=False):
        if (not force and not self._s1_b5_shadow_enabled()) or candidates is None or candidates.empty:
            return candidates
        c = candidates.copy()
        if 'abs_delta' in c.columns:
            abs_delta = pd.to_numeric(c['abs_delta'], errors='coerce').abs()
        elif 'delta' in c.columns:
            abs_delta = pd.to_numeric(c['delta'], errors='coerce').abs()
        else:
            abs_delta = pd.Series(np.nan, index=c.index)
        delta_cap = float(self.config.get('s1_sell_delta_cap', 0.10) or 0.10)
        c['b5_delta_bucket'] = abs_delta.map(self._s1_b5_delta_bucket)
        c['b5_delta_to_cap'] = delta_cap - abs_delta
        c['b5_delta_ratio_to_cap'] = abs_delta / delta_cap if delta_cap > 0 else np.nan
        c['b5_rank_in_delta_bucket'] = (
            c.groupby('b5_delta_bucket', sort=False).cumcount() + 1
        )
        c['b5_delta_bucket_candidate_count'] = c.groupby(
            'b5_delta_bucket', sort=False
        )['b5_delta_bucket'].transform('size')

        mult = pd.to_numeric(
            c['multiplier'] if 'multiplier' in c.columns else c.get('mult', np.nan),
            errors='coerce',
        )
        price = pd.to_numeric(c.get('option_close', np.nan), errors='coerce')
        roundtrip_fee = self._option_roundtrip_fee_per_contract(product, option_type)
        net_premium = pd.to_numeric(c.get('net_premium_cash', np.nan), errors='coerce')
        fallback_premium = price * mult - float(roundtrip_fee or 0.0)
        net_premium = net_premium.where(net_premium.notna(), fallback_premium)
        stress = pd.to_numeric(c.get('stress_loss', np.nan), errors='coerce').clip(lower=0)
        bucket_premium = net_premium.groupby(c['b5_delta_bucket']).transform('sum')
        bucket_stress = stress.groupby(c['b5_delta_bucket']).transform('sum')
        total_premium = float(net_premium.sum()) if net_premium.notna().any() else np.nan
        total_stress = float(stress.sum()) if stress.notna().any() else np.nan
        c['b5_premium_share_delta_bucket'] = (
            bucket_premium / total_premium if np.isfinite(total_premium) and total_premium != 0 else np.nan
        )
        c['b5_stress_share_delta_bucket'] = (
            bucket_stress / total_stress if np.isfinite(total_stress) and total_stress != 0 else np.nan
        )

        spot = pd.to_numeric(c.get('spot_close', c.get('spot', np.nan)), errors='coerce')
        theta_cash = pd.to_numeric(c.get('theta', np.nan), errors='coerce').abs() * mult
        vega_cash = pd.to_numeric(c.get('vega', np.nan), errors='coerce').abs() * mult
        gamma_cash = (
            pd.to_numeric(c.get('gamma', np.nan), errors='coerce').abs()
            * mult
            * spot
            * spot
        )
        c['b5_theta_per_gamma'] = theta_cash / gamma_cash.replace(0, np.nan)
        c['b5_gamma_theta_ratio'] = gamma_cash / theta_cash.replace(0, np.nan)
        c['b5_theta_per_vega'] = theta_cash / vega_cash.replace(0, np.nan)
        c['b5_premium_per_vega'] = net_premium / vega_cash.replace(0, np.nan)

        dte = pd.to_numeric(c.get('dte', np.nan), errors='coerce')
        rv_ref = pd.to_numeric(c.get('rv_ref', c.get('entry_rv20', np.nan)), errors='coerce')
        c['b5_expected_move_pct'] = rv_ref * np.sqrt(dte.clip(lower=1) / 252.0)

        hist_features = self._b5_product_history_features(
            product,
            option_type,
            float(dte.dropna().median()) if dte.notna().any() else np.nan,
        )
        iv_features = self._b5_iv_history_features(product)
        stop_features = self._b5_stop_state_fields(product, option_type, date_str)
        for key, value in {**hist_features, **iv_features, **stop_features}.items():
            c[key] = value

        c['b5_expected_move_loss_cash'] = c.apply(
            lambda row: self._b5_loss_for_move(row, row.get('b5_expected_move_pct', np.nan)),
            axis=1,
        )
        c['b5_mae20_loss_cash'] = c.apply(
            lambda row: self._b5_loss_for_move(row, row.get('b5_mae20_move_pct', np.nan)),
            axis=1,
        )
        c['b5_tail_move_loss_cash'] = c.apply(
            lambda row: self._b5_loss_for_move(row, row.get('b5_tail_move_pct', np.nan)),
            axis=1,
        )
        c['b5_premium_to_expected_move_loss'] = (
            net_premium / pd.to_numeric(c['b5_expected_move_loss_cash'], errors='coerce').replace(0, np.nan)
        )
        c['b5_premium_to_mae20_loss'] = (
            net_premium / pd.to_numeric(c['b5_mae20_loss_cash'], errors='coerce').replace(0, np.nan)
        )
        c['b5_premium_to_tail_move_loss'] = (
            net_premium / pd.to_numeric(c['b5_tail_move_loss_cash'], errors='coerce').replace(0, np.nan)
        )

        min_tick = float(self.config.get('s1_b5_min_tick', 1.0) or 1.0)
        c['b5_tick_value_ratio'] = min_tick / price.replace(0, np.nan)
        min_price = float(self.config.get('s1_min_option_price', 0.0) or 0.0)
        c['b5_low_price_flag'] = (price < max(min_price, min_tick * 2.0)).astype(float)
        c['b5_variance_carry_forward'] = (
            pd.to_numeric(c.get('contract_iv', c.get('entry_atm_iv', np.nan)), errors='coerce') ** 2
            - rv_ref ** 2
        )
        margin = pd.to_numeric(c.get('margin', np.nan), errors='coerce')
        c['b5_capital_lockup_days'] = margin * dte.clip(lower=1)
        c['b5_premium_per_capital_day'] = net_premium / c['b5_capital_lockup_days'].replace(0, np.nan)
        return c

    def _apply_s1_b6_candidate_ranking(self, candidates):
        if candidates is None or candidates.empty:
            return candidates
        c = candidates.copy()
        cfg = self.config
        if bool(cfg.get('s1_b6_hard_filter_enabled', False)):
            min_net = float(cfg.get('s1_b6_min_net_premium_cash', 0.0) or 0.0)
            max_friction = cfg.get('s1_b6_max_friction_ratio', 0.20)
            if min_net > 0 and 'net_premium_cash' in c.columns:
                net = pd.to_numeric(c['net_premium_cash'], errors='coerce')
                c = c[net >= min_net].copy()
            if max_friction is not None and 'friction_ratio' in c.columns:
                try:
                    max_friction = float(max_friction)
                except (TypeError, ValueError):
                    max_friction = np.nan
                if np.isfinite(max_friction):
                    friction = pd.to_numeric(c['friction_ratio'], errors='coerce')
                    c = c[friction.isna() | (friction <= max_friction)].copy()
            if c.empty:
                return c

        def col(name):
            return c[name] if name in c.columns else pd.Series(np.nan, index=c.index, dtype=float)

        c['b6_premium_to_stress_score'] = 100.0 * self._b2_rank_high(col('premium_to_stress_loss'))
        c['b6_premium_to_iv10_score'] = 100.0 * self._b2_rank_high(col('premium_to_iv10_loss'))
        c['b6_theta_per_vega_score'] = 100.0 * self._b2_rank_high(
            c['b5_theta_per_vega'] if 'b5_theta_per_vega' in c.columns else col('theta_vega_efficiency')
        )
        c['b6_theta_per_gamma_score'] = 100.0 * self._b2_rank_high(col('b5_theta_per_gamma'))
        c['b6_tail_move_coverage_score'] = 100.0 * self._b2_rank_high(
            col('b5_premium_to_tail_move_loss')
        )
        c['b6_vomma_score'] = 100.0 * self._b2_rank_low(col('b3_vomma_loss_ratio'))
        c['b6_premium_yield_margin_score'] = 100.0 * self._b2_rank_high(col('premium_yield_margin'))
        weights = {
            'b6_premium_to_stress_score': float(cfg.get('s1_b6_weight_premium_to_stress', 0.24) or 0.0),
            'b6_premium_to_iv10_score': float(cfg.get('s1_b6_weight_premium_to_iv10', 0.22) or 0.0),
            'b6_theta_per_vega_score': float(cfg.get('s1_b6_weight_theta_per_vega', 0.22) or 0.0),
            'b6_theta_per_gamma_score': float(cfg.get('s1_b6_weight_theta_per_gamma', 0.12) or 0.0),
            'b6_tail_move_coverage_score': float(cfg.get('s1_b6_weight_tail_move_coverage', 0.10) or 0.0),
            'b6_vomma_score': float(cfg.get('s1_b6_weight_vomma', 0.06) or 0.0),
            'b6_premium_yield_margin_score': float(
                cfg.get('s1_b6_weight_premium_yield_margin', 0.04) or 0.0
            ),
        }
        weight_sum = sum(max(0.0, v) for v in weights.values())
        missing = float(cfg.get('s1_b6_missing_factor_score', 50.0) or 50.0)
        if weight_sum <= 0:
            c['b6_contract_score'] = pd.to_numeric(
                c.get('quality_score', pd.Series(missing, index=c.index)),
                errors='coerce',
            ).fillna(missing)
        else:
            score = pd.Series(0.0, index=c.index, dtype=float)
            for column, weight in weights.items():
                weight = max(0.0, float(weight or 0.0))
                if weight <= 0:
                    continue
                score += weight * pd.to_numeric(c[column], errors='coerce').fillna(missing)
            c['b6_contract_score'] = (score / weight_sum).clip(0.0, 100.0)
        c['quality_score'] = c['b6_contract_score']
        sort_cols = [
            col for col in (
                'b6_contract_score',
                'b6_theta_per_vega_score',
                'b6_premium_to_stress_score',
                'b6_premium_to_iv10_score',
                'b6_theta_per_gamma_score',
                'b6_tail_move_coverage_score',
                'open_interest',
                'volume',
                'option_code',
            )
            if col in c.columns
        ]
        ascending = [False] * len(sort_cols)
        if sort_cols and sort_cols[-1] == 'option_code':
            ascending[-1] = True
        return c.sort_values(sort_cols, ascending=ascending, kind='mergesort') if sort_cols else c

    def _prepare_s1_b6_selection_candidates(self, candidates, date_str, product, exp, option_type,
                                            term_features=None):
        if not self._s1_b6_enabled() or candidates is None or candidates.empty:
            return candidates
        c = self._b3_add_candidate_fields(
            candidates.copy(),
            product,
            option_type,
            term_features=term_features or {},
        )
        c = self._add_s1_b5_shadow_fields(
            c,
            date_str,
            product,
            exp,
            option_type,
            force=True,
        )
        return self._apply_s1_b6_candidate_ranking(c)

    def _select_s1_candidate_universe_frame(self, ef, product, ot, mult, mr,
                                            exchange, min_abs_delta, delta_cap,
                                            iv_state=None, side_meta=None,
                                            term_features=None):
        max_candidates = self._s1_candidate_universe_max_candidates()
        candidates = self._select_s1_sell_candidates(
            ef, product, ot, mult, mr, exchange,
            min_abs_delta, delta_cap, max_candidates,
        )
        candidates = self._filter_s1_forward_vega_candidates(
            candidates,
            product,
            ot,
            iv_state=iv_state,
            side_meta=side_meta,
        )
        if candidates is None or candidates.empty:
            return candidates
        return self._b3_add_candidate_fields(
            candidates.copy(),
            product,
            ot,
            term_features=term_features,
        )

    def _append_s1_candidate_universe(self, date_str, nav, product, exp, ot,
                                      candidates, side_meta=None):
        if not self._s1_candidate_universe_enabled():
            return
        if not self._s1_candidate_signal_in_scope(date_str):
            return
        if candidates is None or candidates.empty:
            return

        side_meta = side_meta or {}
        candidates = self._add_s1_b5_shadow_fields(
            candidates,
            date_str,
            product,
            exp,
            ot,
        )
        total = len(candidates)
        for rank, (_, row) in enumerate(candidates.iterrows(), start=1):
            candidate_id = self._next_s1_candidate_id()
            mult = self._safe_float(row.get('multiplier', row.get('mult', np.nan)), np.nan)
            if not np.isfinite(mult) or mult <= 0:
                mult = self._safe_float(row.get('contract_multiplier', np.nan), np.nan)
            signal_price = self._safe_float(row.get('option_close', np.nan), np.nan)
            spot = self._safe_float(row.get('spot_close', row.get('spot', np.nan)), np.nan)
            cash_greeks = (
                self._candidate_cash_greeks(row, ot, mult, 1, role='sell')
                if np.isfinite(mult) and mult > 0 else {}
            )
            open_fee = self._option_fee_per_contract(product, ot, action='open')
            close_fee = self._option_fee_per_contract(product, ot, action='close')
            margin = self._safe_float(row.get('margin', np.nan), np.nan)
            stress_loss = self._safe_float(row.get('stress_loss', np.nan), np.nan)

            record = {
                'candidate_id': candidate_id,
                'signal_date': date_str,
                'product': product,
                'bucket': self._get_product_bucket(product),
                'corr_group': self._get_product_corr_group(product),
                'exchange': row.get('exchange', ''),
                'underlying_code': row.get('underlying_code', ''),
                'option_type': ot,
                'code': row.get('option_code', ''),
                'expiry': str(exp)[:10],
                'dte': self._safe_float(row.get('dte', np.nan), np.nan),
                'strike': self._safe_float(row.get('strike', np.nan), np.nan),
                'spot': spot,
                'option_price': signal_price,
                'mult': mult,
                'nav': nav,
                'rank_in_side': rank,
                'candidate_count_in_side': total,
                'candidate_stage': 'pre_budget_universe',
                'label_mode': self.config.get(
                    's1_candidate_universe_label_mode',
                    'daily_close_shadow',
                ),
                'open_fee_per_contract': open_fee,
                'close_fee_per_contract': close_fee,
                'roundtrip_fee_per_contract': open_fee + close_fee,
                'margin_estimate': margin,
                'cash_vega': cash_greeks.get('cash_vega', np.nan),
                'cash_gamma': cash_greeks.get('cash_gamma', np.nan),
                'cash_delta': self._safe_float(row.get('delta', np.nan), np.nan) * spot * mult
                if np.isfinite(spot) and np.isfinite(mult) else np.nan,
                'cash_theta': abs(self._safe_float(row.get('theta', np.nan), np.nan)) * mult
                if np.isfinite(mult) else np.nan,
                'stress_loss': stress_loss,
                'gross_premium_cash_1lot': signal_price * mult
                if np.isfinite(signal_price) and np.isfinite(mult) else np.nan,
                'net_premium_cash_1lot': self._safe_float(
                    row.get('net_premium_cash', np.nan),
                    np.nan,
                ),
                'vol_regime': self._current_vol_regimes.get(product, ''),
                'entry_atm_iv': row.get('entry_atm_iv', side_meta.get('entry_atm_iv', np.nan)),
                'entry_iv_pct': row.get('entry_iv_pct', side_meta.get('entry_iv_pct', np.nan)),
                'entry_iv_trend': row.get('entry_iv_trend', np.nan),
                'entry_rv_trend': row.get('entry_rv_trend', np.nan),
                'entry_iv_rv_spread': row.get('entry_iv_rv_spread', np.nan),
                'entry_iv_rv_ratio': row.get('entry_iv_rv_ratio', np.nan),
                'trend_state': side_meta.get('trend_state', row.get('trend_state', '')),
                'trend_role': side_meta.get('trend_role', row.get('trend_role', '')),
                'side_budget_mult': side_meta.get('budget_mult', np.nan),
                'b2_product_score': side_meta.get('b2_product_score', np.nan),
                'b2_product_budget_mult': side_meta.get('b2_product_budget_mult', np.nan),
                'b3_product_side_score': side_meta.get('b3_product_side_score', np.nan),
                'b3_side_budget_mult': side_meta.get('b3_side_budget_mult', np.nan),
                'b4_product_side_score': side_meta.get('b4_product_side_score', np.nan),
                'b4_side_budget_mult': side_meta.get('b4_side_budget_mult', np.nan),
                'b4_side_vov_penalty_mult': side_meta.get('b4_side_vov_penalty_mult', np.nan),
                'b6_product_score': side_meta.get('b6_product_score', np.nan),
                'b6_product_budget_mult': side_meta.get('b6_product_budget_mult', np.nan),
                'b6_product_side_score': side_meta.get('b6_product_side_score', np.nan),
                'b6_side_budget_mult': side_meta.get('b6_side_budget_mult', np.nan),
                'b6_side_direction_penalty_mult': side_meta.get(
                    'b6_side_direction_penalty_mult',
                    np.nan,
                ),
            }
            for field in (
                'volume', 'open_interest', 'moneyness',
                'abs_delta', 'delta', 'gamma', 'vega', 'theta',
                'liquidity_score', 'quality_score', 'carry_score', 'eff', 'net_eff',
                'premium_stress', 'theta_stress', 'premium_margin',
                'premium_yield_margin', 'premium_yield_notional',
                'rv_ref', 'iv_rv_spread_candidate', 'iv_rv_ratio_candidate',
                'variance_carry', 'breakeven_price', 'breakeven_cushion_abs',
                'breakeven_cushion_iv', 'breakeven_cushion_rv',
                'iv_shock_loss_5_cash', 'iv_shock_loss_10_cash',
                'premium_to_iv5_loss', 'premium_to_iv10_loss',
                'premium_to_stress_loss', 'theta_vega_efficiency',
                'gamma_rent_cash', 'gamma_rent_penalty',
                'fee_ratio', 'slippage_ratio', 'friction_ratio',
                'premium_quality_score', 'premium_quality_rank_in_side',
                'iv_rv_carry_score', 'breakeven_cushion_score',
                'premium_to_iv_shock_score', 'premium_to_stress_loss_score',
                'theta_vega_efficiency_score', 'cost_liquidity_score',
                'contract_iv', 'contract_iv_change_1d', 'contract_iv_change_3d',
                'contract_iv_change_5d', 'contract_iv_change_for_vega',
                'contract_iv_skew_to_atm', 'contract_skew_change_for_vega',
                'contract_price_change_1d',
                'b3_forward_variance_pressure', 'b3_vol_of_vol_proxy',
                'b3_vov_trend', 'b3_iv_shock_coverage',
                'b3_joint_stress_coverage', 'b3_vomma_cash',
                'b3_vomma_loss_ratio', 'b3_skew_steepening',
                'b3_near_atm_iv', 'b3_next_atm_iv', 'b3_far_atm_iv',
                'b3_term_structure_pressure',
                'b4_contract_score', 'b4_premium_to_iv10_score',
                'b4_premium_to_stress_score', 'b4_premium_yield_margin_score',
                'b4_gamma_rent_score', 'b4_vomma_score',
                'b4_breakeven_cushion_score', 'b4_vol_of_vol_score',
                'b6_contract_score', 'b6_premium_to_stress_score',
                'b6_premium_to_iv10_score', 'b6_theta_per_vega_score',
                'b6_theta_per_gamma_score', 'b6_tail_move_coverage_score',
                'b6_vomma_score', 'b6_premium_yield_margin_score',
            ):
                record[field] = row.get(field, np.nan)
            for field in self._S1_B5_CANDIDATE_FIELDS:
                record[field] = row.get(field, np.nan)
            self.s1_candidate_records.append(record)
            if self._s1_candidate_shadow_enabled():
                self._queue_s1_shadow_candidate(record)

    def _queue_s1_shadow_candidate(self, record):
        price = self._safe_float(record.get('option_price', np.nan), np.nan)
        mult = self._safe_float(record.get('mult', np.nan), np.nan)
        if not np.isfinite(price) or price <= 0 or not np.isfinite(mult) or mult <= 0:
            return
        self._s1_shadow_candidates.append({
            'candidate_id': record.get('candidate_id'),
            'signal_date': record.get('signal_date'),
            'product': record.get('product'),
            'option_type': record.get('option_type'),
            'code': record.get('code'),
            'strike': record.get('strike'),
            'mult': mult,
            'signal_price': price,
            'entry_date': None,
            'entry_price': np.nan,
            'last_date': record.get('signal_date'),
            'last_price': price,
            'last_spot': record.get('spot', np.nan),
            'max_price': price,
            'open_fee': self._safe_float(record.get('open_fee_per_contract', 0.0), 0.0),
            'close_fee': self._safe_float(record.get('close_fee_per_contract', 0.0), 0.0),
            'expiry': record.get('expiry'),
        })

    def _shadow_intrinsic_value(self, option_type, strike, spot):
        strike = self._safe_float(strike, np.nan)
        spot = self._safe_float(spot, np.nan)
        if not np.isfinite(strike) or not np.isfinite(spot):
            return np.nan
        if str(option_type).upper() == 'P':
            return max(strike - spot, 0.0)
        return max(spot - strike, 0.0)

    def _update_s1_shadow_candidate_outcomes(self, daily_df, date_str):
        if not self._s1_candidate_shadow_enabled() or not self._s1_shadow_candidates:
            return
        required = {'option_code', 'option_close'}
        if daily_df is None or daily_df.empty or not required.issubset(daily_df.columns):
            return

        cols = [
            col for col in (
                'option_code', 'option_close', 'dte', 'spot_close',
                'strike', 'option_type',
            )
            if col in daily_df.columns
        ]
        prices = daily_df.loc[daily_df['option_close'].fillna(0) > 0, cols].copy()
        if prices.empty:
            return
        prices = prices.drop_duplicates('option_code', keep='last')
        by_code = {str(row.option_code): row for row in prices.itertuples(index=False)}

        stop_multiple = float(self.config.get('premium_stop_multiple', 2.5) or 2.5)
        expiry_dte = int(self.config.get('expiry_dte', 1) or 1)
        remaining = []
        for item in self._s1_shadow_candidates:
            code = str(item.get('code') or '')
            row = by_code.get(code)
            if row is None:
                remaining.append(item)
                continue
            price = self._safe_float(getattr(row, 'option_close', np.nan), np.nan)
            if not np.isfinite(price) or price <= 0:
                remaining.append(item)
                continue

            spot = self._safe_float(getattr(row, 'spot_close', np.nan), item.get('last_spot', np.nan))
            dte = self._safe_float(getattr(row, 'dte', np.nan), np.nan)
            if not np.isfinite(dte):
                try:
                    dte = self.ci.calc_dte(code, datetime.strptime(date_str, '%Y-%m-%d').date())
                except Exception:
                    dte = np.nan

            item['last_date'] = date_str
            item['last_price'] = price
            item['last_spot'] = spot
            item['max_price'] = max(self._safe_float(item.get('max_price', price), price), price)

            if not item.get('entry_date'):
                if str(date_str) <= str(item.get('signal_date') or ''):
                    remaining.append(item)
                    continue
                item['entry_date'] = date_str
                item['entry_price'] = price
                item['max_price'] = price
                remaining.append(item)
                continue

            if str(date_str) == str(item.get('entry_date')):
                remaining.append(item)
                continue

            entry_price = self._safe_float(item.get('entry_price', np.nan), np.nan)
            if not np.isfinite(entry_price) or entry_price <= 0:
                remaining.append(item)
                continue

            stop_hit = price >= entry_price * stop_multiple
            expiry_hit = np.isfinite(dte) and dte <= expiry_dte
            if stop_hit:
                self._close_s1_shadow_candidate(
                    item, date_str, price, spot, 'shadow_sl_daily_close'
                )
            elif expiry_hit:
                intrinsic = self._shadow_intrinsic_value(
                    item.get('option_type'),
                    item.get('strike', getattr(row, 'strike', np.nan)),
                    spot,
                )
                exit_price = intrinsic if np.isfinite(intrinsic) else price
                self._close_s1_shadow_candidate(
                    item, date_str, exit_price, spot, 'shadow_expiry'
                )
            else:
                remaining.append(item)
        self._s1_shadow_candidates = remaining

    def _close_s1_shadow_candidate(self, item, exit_date, exit_price, spot, reason):
        entry_price = self._safe_float(item.get('entry_price', np.nan), np.nan)
        mult = self._safe_float(item.get('mult', np.nan), np.nan)
        if not np.isfinite(entry_price) or entry_price <= 0 or not np.isfinite(mult) or mult <= 0:
            return
        exit_price = self._safe_float(exit_price, np.nan)
        if not np.isfinite(exit_price):
            exit_price = self._safe_float(item.get('last_price', entry_price), entry_price)
        open_fee = self._safe_float(item.get('open_fee', 0.0), 0.0)
        close_fee = self._safe_float(item.get('close_fee', 0.0), 0.0)
        if reason == 'shadow_expiry' and exit_price <= 0:
            close_fee = 0.0
        gross_pnl = (entry_price - exit_price) * mult
        fee = open_fee + close_fee
        net_pnl = gross_pnl - fee
        premium = entry_price * mult
        max_price = self._safe_float(item.get('max_price', entry_price), entry_price)
        stop_flag = 1.0 if str(reason).startswith('shadow_sl') else 0.0
        expiry_flag = 1.0 if reason == 'shadow_expiry' else 0.0
        days_held = np.nan
        try:
            days_held = (
                datetime.strptime(exit_date, '%Y-%m-%d')
                - datetime.strptime(str(item.get('entry_date')), '%Y-%m-%d')
            ).days
        except Exception:
            pass

        self.s1_candidate_outcomes.append({
            'candidate_id': item.get('candidate_id'),
            'signal_date': item.get('signal_date'),
            'entry_date': item.get('entry_date'),
            'exit_date': exit_date,
            'product': item.get('product'),
            'option_type': item.get('option_type'),
            'code': item.get('code'),
            'reason': reason,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'exit_spot': spot,
            'open_premium_cash': premium,
            'exit_value_cash': exit_price * mult,
            'future_gross_pnl': gross_pnl,
            'future_fee': fee,
            'future_net_pnl': net_pnl,
            'future_net_pnl_per_premium': net_pnl / premium if premium > 0 else np.nan,
            'future_retained_premium': net_pnl,
            'future_retained_ratio': net_pnl / premium if premium > 0 else np.nan,
            'future_stop_flag': stop_flag,
            'future_stop_avoidance': -stop_flag,
            'future_stop_loss': net_pnl if stop_flag else 0.0,
            'future_stop_loss_per_premium': net_pnl / premium if stop_flag and premium > 0 else 0.0,
            'future_stop_loss_avoidance': net_pnl / premium if stop_flag and premium > 0 else 0.0,
            'future_expiry_flag': expiry_flag,
            'future_expiry_itm_flag': 1.0 if expiry_flag and exit_price > 0 else 0.0,
            'future_days_held': days_held,
            'future_max_price': max_price,
            'future_max_price_multiple': max_price / entry_price if entry_price > 0 else np.nan,
        })

    def _finalize_s1_shadow_candidate_outcomes(self):
        if not self._s1_candidate_shadow_enabled() or not self._s1_shadow_candidates:
            return
        for item in list(self._s1_shadow_candidates):
            if item.get('entry_date'):
                self._close_s1_shadow_candidate(
                    item,
                    item.get('last_date') or self._current_date_str,
                    item.get('last_price', item.get('entry_price', np.nan)),
                    item.get('last_spot', np.nan),
                    'shadow_unfinished',
                )
            else:
                self.s1_candidate_outcomes.append({
                    'candidate_id': item.get('candidate_id'),
                    'signal_date': item.get('signal_date'),
                    'entry_date': '',
                    'exit_date': '',
                    'product': item.get('product'),
                    'option_type': item.get('option_type'),
                    'code': item.get('code'),
                    'reason': 'shadow_no_entry_price',
                })
        self._s1_shadow_candidates = []

    @staticmethod
    def _b5_effective_count(values):
        arr = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
        arr = arr[arr > 0]
        if arr.empty:
            return 0.0
        total = float(arr.sum())
        denom = float((arr ** 2).sum())
        return total * total / denom if denom > 0 else 0.0

    @staticmethod
    def _b5_hhi(values):
        arr = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
        arr = arr[arr > 0]
        if arr.empty:
            return np.nan
        shares = arr / float(arr.sum())
        return float((shares ** 2).sum())

    def _b5_tail_dependence_product_panel(self, candidates_df):
        if not self.config.get('s1_b5_tail_dependence_enabled', True):
            return pd.DataFrame()
        if candidates_df.empty or not self._spot_history:
            return pd.DataFrame()
        series_map = {}
        for product in sorted(candidates_df['product'].dropna().astype(str).unique()):
            s = self._history_series(self._spot_history, product, 'spots')
            if not s.empty:
                series_map[product] = s
        if not series_map:
            return pd.DataFrame()
        spot_df = pd.DataFrame(series_map).sort_index()
        returns = spot_df.pct_change(fill_method=None)
        window_days = int(self.config.get('s1_b5_tail_window_days', 120) or 120)
        min_days = int(self.config.get('s1_b5_min_history_days', 60) or 60)
        q = float(self.config.get('s1_b5_tail_quantile', 0.05) or 0.05)
        q = min(max(q, 0.01), 0.49)
        rows = []
        for date_str, group in candidates_df.groupby('signal_date', sort=False):
            dt = pd.Timestamp(date_str)
            products = [
                p for p in sorted(group['product'].dropna().astype(str).unique())
                if p in returns.columns
            ]
            if len(products) < 2:
                continue
            window = returns.loc[returns.index <= dt, products].tail(window_days)
            window = window.dropna(how='all')
            if len(window) < min_days:
                continue
            port = window[products].mean(axis=1, skipna=True).dropna()
            if len(port) < min_days:
                continue
            lower_mask = port <= port.quantile(q)
            upper_mask = port >= port.quantile(1.0 - q)
            lower_n = int(lower_mask.sum())
            upper_n = int(upper_mask.sum())
            port_lower = port[lower_mask]
            port_upper = port[upper_mask]
            for product in products:
                x = window[product].reindex(port.index).dropna()
                if len(x) < min_days:
                    continue
                lower_dep = np.nan
                upper_dep = np.nan
                lower_beta = np.nan
                upper_beta = np.nan
                if lower_n > 0:
                    x_lower_q = float(x.quantile(q))
                    lower_dep = float((x.reindex(port_lower.index) <= x_lower_q).mean())
                    if len(port_lower) > 1 and float(port_lower.var()) > 0:
                        x_lower = x.reindex(port_lower.index).dropna()
                        aligned = port_lower.reindex(x_lower.index)
                        if len(x_lower) > 1 and float(aligned.var()) > 0:
                            lower_beta = float(np.cov(x_lower, aligned)[0, 1] / aligned.var())
                if upper_n > 0:
                    x_upper_q = float(x.quantile(1.0 - q))
                    upper_dep = float((x.reindex(port_upper.index) >= x_upper_q).mean())
                    if len(port_upper) > 1 and float(port_upper.var()) > 0:
                        x_upper = x.reindex(port_upper.index).dropna()
                        aligned = port_upper.reindex(x_upper.index)
                        if len(x_upper) > 1 and float(aligned.var()) > 0:
                            upper_beta = float(np.cov(x_upper, aligned)[0, 1] / aligned.var())
                rows.append({
                    'signal_date': date_str,
                    'product': product,
                    'b5_empirical_lower_tail_dependence_95': lower_dep,
                    'b5_empirical_upper_tail_dependence_95': upper_dep,
                    'b5_lower_tail_beta': lower_beta,
                    'b5_upper_tail_beta': upper_beta,
                    'b5_lower_tail_dependence_excess': lower_dep - q if np.isfinite(lower_dep) else np.nan,
                    'b5_upper_tail_dependence_excess': upper_dep - q if np.isfinite(upper_dep) else np.nan,
                    'b5_tail_window_days_used': len(window),
                })
        return pd.DataFrame(rows)

    def _write_s1_b5_candidate_panels(self, candidates_df, tag):
        if not self._s1_b5_shadow_enabled() or candidates_df.empty:
            return
        df = candidates_df.copy()
        for col in (
            'net_premium_cash_1lot', 'stress_loss', 'margin_estimate',
            'cash_vega', 'cash_gamma', 'cash_theta', 'abs_delta',
            'contract_iv_skew_to_atm', 'b4_contract_score',
            'b5_theta_per_gamma', 'b5_premium_to_tail_move_loss',
            'b5_cooldown_penalty_score', 'b5_delta_ratio_to_cap',
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            else:
                df[col] = np.nan
        df['product_side_key'] = df['product'].astype(str) + '_' + df['option_type'].astype(str)

        product_panel = df.groupby(['signal_date', 'product'], sort=False).agg(
            product_candidate_count=('candidate_id', 'count'),
            product_side_count=('option_type', 'nunique'),
            product_premium_sum=('net_premium_cash_1lot', 'sum'),
            product_stress_sum=('stress_loss', 'sum'),
            product_margin_sum=('margin_estimate', 'sum'),
            product_cash_vega_sum=('cash_vega', 'sum'),
            product_cash_gamma_sum=('cash_gamma', 'sum'),
            product_cash_theta_sum=('cash_theta', 'sum'),
            product_avg_delta_ratio_to_cap=('b5_delta_ratio_to_cap', 'mean'),
            product_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
            product_cooldown_penalty=('b5_cooldown_penalty_score', 'mean'),
        ).reset_index()
        total_stress = product_panel.groupby('signal_date')['product_stress_sum'].transform('sum')
        total_margin = product_panel.groupby('signal_date')['product_margin_sum'].transform('sum')
        product_panel['product_stress_share'] = product_panel['product_stress_sum'] / total_stress.replace(0, np.nan)
        product_panel['product_margin_share'] = product_panel['product_margin_sum'] / total_margin.replace(0, np.nan)
        tail_panel = self._b5_tail_dependence_product_panel(df)
        if not tail_panel.empty:
            product_panel = product_panel.merge(tail_panel, on=['signal_date', 'product'], how='left')
        product_path = os.path.join(OUTPUT_DIR, f"s1_b5_product_panel_{tag}.csv")
        product_panel.to_csv(product_path, index=False)
        logger.info("S1 B5 product panel: %s (%d rows)", product_path, len(product_panel))

        side_panel = df.groupby(['signal_date', 'product', 'option_type'], sort=False).agg(
            side_candidate_count=('candidate_id', 'count'),
            side_premium_sum=('net_premium_cash_1lot', 'sum'),
            side_stress_sum=('stress_loss', 'sum'),
            side_margin_sum=('margin_estimate', 'sum'),
            side_cash_vega_sum=('cash_vega', 'sum'),
            side_cash_gamma_sum=('cash_gamma', 'sum'),
            side_cash_theta_sum=('cash_theta', 'sum'),
            side_avg_abs_delta=('abs_delta', 'mean'),
            side_avg_contract_iv_skew_to_atm=('contract_iv_skew_to_atm', 'mean'),
            side_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
            side_avg_theta_per_gamma=('b5_theta_per_gamma', 'mean'),
            side_cooldown_penalty=('b5_cooldown_penalty_score', 'mean'),
            b5_mom_20d=('b5_mom_20d', 'mean'),
            b5_trend_z_20d=('b5_trend_z_20d', 'mean'),
            b5_breakout_distance_up_60d=('b5_breakout_distance_up_60d', 'mean'),
            b5_breakout_distance_down_60d=('b5_breakout_distance_down_60d', 'mean'),
            b5_atm_iv_mom_5d=('b5_atm_iv_mom_5d', 'mean'),
            b5_atm_iv_accel=('b5_atm_iv_accel', 'mean'),
        ).reset_index()
        side_path = os.path.join(OUTPUT_DIR, f"s1_b5_product_side_panel_{tag}.csv")
        side_panel.to_csv(side_path, index=False)
        logger.info("S1 B5 product-side panel: %s (%d rows)", side_path, len(side_panel))

        ladder_cols = ['signal_date', 'product', 'option_type', 'expiry', 'b5_delta_bucket']
        ladder_panel = df.groupby(ladder_cols, sort=False).agg(
            bucket_candidate_count=('candidate_id', 'count'),
            bucket_premium_sum=('net_premium_cash_1lot', 'sum'),
            bucket_stress_sum=('stress_loss', 'sum'),
            bucket_margin_sum=('margin_estimate', 'sum'),
            bucket_avg_abs_delta=('abs_delta', 'mean'),
            bucket_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
            bucket_avg_theta_per_gamma=('b5_theta_per_gamma', 'mean'),
            bucket_avg_b4_contract_score=('b4_contract_score', 'mean'),
        ).reset_index()
        ladder_path = os.path.join(OUTPUT_DIR, f"s1_b5_delta_ladder_panel_{tag}.csv")
        ladder_panel.to_csv(ladder_path, index=False)
        logger.info("S1 B5 delta-ladder panel: %s (%d rows)", ladder_path, len(ladder_panel))

        rows = []
        for date_str, group in df.groupby('signal_date', sort=False):
            product_stress = group.groupby('product')['stress_loss'].sum()
            sector_stress = group.groupby('bucket')['stress_loss'].sum()
            product_margin = group.groupby('product')['margin_estimate'].sum()
            product_vega = group.groupby('product')['cash_vega'].sum().abs()
            product_gamma = group.groupby('product')['cash_gamma'].sum().abs()
            stress_total = float(product_stress.sum())
            top5_share = np.nan
            top1_share = np.nan
            if stress_total > 0:
                shares = product_stress.sort_values(ascending=False) / stress_total
                top1_share = float(shares.iloc[0]) if len(shares) else np.nan
                top5_share = float(shares.head(5).sum())
            rows.append({
                'signal_date': date_str,
                'candidate_count': int(len(group)),
                'active_product_count': int(group['product'].nunique()),
                'active_product_side_count': int(group['product_side_key'].nunique()),
                'active_sector_count': int(group['bucket'].nunique()),
                'portfolio_premium_sum': float(group['net_premium_cash_1lot'].sum()),
                'portfolio_stress_sum': stress_total,
                'portfolio_margin_sum': float(product_margin.sum()),
                'portfolio_cash_vega_abs_sum': float(product_vega.sum()),
                'portfolio_cash_gamma_abs_sum': float(product_gamma.sum()),
                'effective_product_count_margin': self._b5_effective_count(product_margin),
                'effective_product_count_stress': self._b5_effective_count(product_stress),
                'effective_product_count_vega': self._b5_effective_count(product_vega),
                'effective_product_count_gamma': self._b5_effective_count(product_gamma),
                'top1_product_stress_share': top1_share,
                'top5_product_stress_share': top5_share,
                'hhi_product_stress': self._b5_hhi(product_stress),
                'hhi_sector_stress': self._b5_hhi(sector_stress),
                'portfolio_put_stress': float(group.loc[group['option_type'] == 'P', 'stress_loss'].sum()),
                'portfolio_call_stress': float(group.loc[group['option_type'] == 'C', 'stress_loss'].sum()),
            })
        portfolio_panel = pd.DataFrame(rows)
        portfolio_path = os.path.join(OUTPUT_DIR, f"s1_b5_portfolio_panel_{tag}.csv")
        portfolio_panel.to_csv(portfolio_path, index=False)
        logger.info("S1 B5 portfolio panel: %s (%d rows)", portfolio_path, len(portfolio_panel))

    def _write_s1_candidate_outputs(self, tag):
        if not self._s1_candidate_universe_enabled():
            return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        candidates_df = pd.DataFrame(self.s1_candidate_records)
        candidates_path = os.path.join(OUTPUT_DIR, f"s1_candidate_universe_{tag}.csv")
        candidates_df.to_csv(candidates_path, index=False)
        logger.info("S1 candidate universe: %s (%d rows)", candidates_path, len(candidates_df))
        self._write_s1_b5_candidate_panels(candidates_df, tag)

        if self.config.get('s1_candidate_universe_shadow_enabled', False):
            outcomes_df = pd.DataFrame(self.s1_candidate_outcomes)
            outcomes_path = os.path.join(OUTPUT_DIR, f"s1_candidate_outcomes_{tag}.csv")
            outcomes_df.to_csv(outcomes_path, index=False)
            logger.info("S1 candidate outcomes: %s (%d rows)", outcomes_path, len(outcomes_df))

    def _normalize_open_budget(self, budget):
        return normalize_open_budget(budget)

    def _get_effective_open_budget(self):
        nav = self._current_nav()
        budget = get_effective_open_budget(
            self.config,
            portfolio_regime=self._current_portfolio_regime or 'normal_vol',
            drawdown=self._current_drawdown(nav),
            recent_stop_count=self._recent_stop_count(),
        )
        self._current_open_budget = budget
        return budget

    def _product_margin_per_multiplier(self, product):
        return vol_rules.product_margin_per_multiplier(
            self.config,
            product=product,
            current_vol_regimes=self._current_vol_regimes,
            iv_history=self._iv_history,
            current_iv_state=self._current_iv_state,
            normalize_product_key=self._normalize_product_key,
        )

    def _passes_s1_falling_framework_entry(self, product, iv_state):
        return vol_rules.passes_s1_falling_framework_entry(
            self.config,
            product=product,
            iv_state=iv_state,
            current_vol_regimes=self._current_vol_regimes,
            iv_history=self._iv_history,
        )

    def _passes_s1_risk_release_entry(self, product, iv_state):
        return vol_rules.passes_s1_risk_release_entry(
            self.config,
            product=product,
            iv_state=iv_state,
            current_vol_regimes=self._current_vol_regimes,
            iv_history=self._iv_history,
        )

    def _candidate_cash_greeks(self, row, opt_type, mult, qty, role='sell'):
        return port_risk.candidate_cash_greeks(row, opt_type, mult, qty, role=role)

    def _get_open_greek_state(self, include_pending=True):
        return port_risk.get_open_greek_state(
            self.positions,
            self._pending_opens,
            self._get_product_bucket,
            include_pending=include_pending,
        )

    def _get_open_stress_loss_state(self, include_pending=True):
        return port_risk.get_open_stress_loss_state(
            self.positions,
            self._pending_opens,
            self._get_product_bucket,
            include_pending=include_pending,
        )

    def _passes_greek_budget(self, product, nav, new_cash_vega=0.0,
                             new_cash_gamma=0.0, include_pending=True):
        return port_risk.passes_greek_budget(
            self.config,
            product=product,
            nav=nav,
            greek_state=self._get_open_greek_state(include_pending=include_pending),
            get_product_bucket=self._get_product_bucket,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
        )

    def _current_drawdown(self, nav=None):
        if nav is None:
            nav = self._current_nav()
        try:
            nav = float(nav)
        except (TypeError, ValueError):
            return 0.0
        peaks = [float(self.capital)]
        for record in self.nav_records:
            try:
                peaks.append(float(record.get('nav', np.nan)))
            except (TypeError, ValueError):
                continue
        peaks = [v for v in peaks if np.isfinite(v) and v > 0]
        peak = max(peaks) if peaks else max(float(self.capital), 1.0)
        if peak <= 0:
            return 0.0
        return max(0.0, 1.0 - nav / peak)

    def _recent_stop_count(self, date_str=None):
        if date_str is None:
            date_str = self._current_date_str
        return vol_rules.recent_stop_count(self.config, self._stop_history, date_str)

    def _pending_budget_fields(self, budget=None, strategy_cap=None):
        if strategy_cap is None:
            strategy_cap = budget
            budget = None
        budget = budget or self._current_open_budget or self._get_effective_open_budget()
        return pending_budget_fields(budget, strategy_cap)

    def _execution_budget_for_item(self, item):
        current = self._current_open_budget or self._get_effective_open_budget()
        return execution_budget_for_item(item, current, self.config)

    def _passes_stress_budget(self, nav, new_cash_vega=0.0, new_cash_gamma=0.0,
                              product=None, new_stress_loss=0.0, budget=None,
                              include_pending=True):
        budget = budget or self._current_open_budget or self._get_effective_open_budget()
        return port_risk.passes_stress_budget(
            self.config,
            nav=nav,
            greek_state=self._get_open_greek_state(include_pending=include_pending),
            stress_state=self._get_open_stress_loss_state(include_pending=include_pending),
            get_product_bucket=self._get_product_bucket,
            product=product,
            budget=budget,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
            new_stress_loss=new_stress_loss,
        )

    def run(self, start_date=None, end_date=None, products=None, tag='toolkit'):
        """主入口"""
        t0 = time.time()
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        dates = self.loader.get_trading_dates(start_date, end_date)
        if not dates:
            logger.error("无交易日数据")
            return {}

        logger.info("回测 %d 天: %s ~ %s", len(dates), dates[0], dates[-1])

        # 确定品种池
        if products:
            product_pool = set(p.upper() for p in products)
        else:
            product_pool = self._get_default_product_pool()
        self._ensure_product_first_trade_dates(product_pool)
        logger.info("品种池: %s", sorted(product_pool))

        # 构建品种LIKE过滤SQL（复用于预热和回测）
        like_sql = self._build_product_like_sql(product_pool)
        logger.info("品种过滤SQL: %s", like_sql[:200] if like_sql else "无")

        # IV预热（先检查缓存，缺失品种补跑）
        warmup_days = int(self.config.get('iv_window', 252) or 0)
        all_dates = self.loader.get_trading_dates()
        warmup_dates = [d for d in all_dates if d < dates[0]][-warmup_days:]
        if self.config.get('iv_warmup_enabled', True) and warmup_days > 0 and warmup_dates:
            cache_path = warmup_cache_path(OUTPUT_DIR)
            cached_products, skipped_warmup_products = load_iv_warmup_cache(
                cache_path,
                product_pool,
                self._iv_history,
                self._spot_history,
                required_end_date=warmup_dates[-1],
                logger=logger,
                return_skipped=True,
            )
            if self.config.get('iv_warmup_retry_skipped_products', False):
                skipped_warmup_products = set()

            missing = set(product_pool - cached_products - skipped_warmup_products)
            if self.config.get('portfolio_dynamic_corr_control_enabled', True):
                missing_spot = {
                    product for product in product_pool
                    if product not in skipped_warmup_products
                    if not self._spot_history[product]['spots']
                }
                missing |= missing_spot
            if missing:
                for product in missing:
                    self._iv_history[product] = {'dates': [], 'ivs': []}
                    self._spot_history[product] = {'dates': [], 'spots': []}
                logger.info("IV预热: 缓存命中 %d个品种, 补跑 %d个: %s",
                            len(cached_products), len(missing), sorted(missing))
                self._warmup_iv_consistent(warmup_dates, missing)
                warmup_failed = {
                    product for product in missing
                    if not self._iv_history[product]['ivs']
                }
                skipped_warmup_products |= warmup_failed
                save_iv_warmup_cache(
                    cache_path,
                    warmup_dates[-1],
                    self._iv_history,
                    self._spot_history,
                    n_days=len(warmup_dates),
                    logger=logger,
                    skipped_products=skipped_warmup_products,
                )
            else:
                logger.info("IV预热: 从缓存加载, %d个品种全部命中", len(cached_products))

        # 主循环（批量预加载分钟数据）
        batch_size = max(1, int(self.config.get('daily_agg_batch_size', 10) or 10))
        for di, date_str in enumerate(dates):
            if di % batch_size == 0:
                nav = self.capital + (self.nav_records[-1]['cum_pnl'] if self.nav_records else 0)
                elapsed = time.time() - t0
                logger.info("  [%d/%d] %s NAV=%.0f 持仓=%d %.0fs",
                            di, len(dates), date_str, nav, len(self.positions), elapsed)
                if self.nav_records:
                    pd.DataFrame(self.nav_records).to_csv(
                        os.path.join(OUTPUT_DIR, f"nav_{tag}.csv"), index=False)

                # 批量预加载接下来的N天（日频聚合，不拉分钟明细）
                upcoming = dates[di:di + batch_size]
                self.loader.preload_daily_agg_batch(upcoming, like_sql, self.ci)
                # 清理旧缓存
                self.loader.clear_cache(keep_dates=set(upcoming))

            self._day_realized = {'pnl': 0.0, 'fee': 0.0, 's1': 0.0, 's3': 0.0, 's4': 0.0}
            self._day_attr_realized = self._zero_attr_bucket()
            self._current_date_str = date_str

            t_day = time.time()
            minute_df = pd.DataFrame()
            pending_codes = {
                item['code'] for item in self._pending_opens if item.get('code')
            }
            pre_open_held_codes = {pos.code for pos in self.positions if pos.code}
            exit_codes = set(pre_open_held_codes)
            if not self.config.get('skip_same_day_exit_for_vwap_opens', True):
                exit_codes |= pending_codes
            exit_codes = self._prefilter_intraday_exit_codes_by_daily_high(date_str, exit_codes)
            needed_minute_codes = pending_codes | exit_codes
            if needed_minute_codes:
                minute_df = self.loader.load_day_minute(date_str, code_list=needed_minute_codes)

            # Phase 0: 执行昨日待开仓（需要分钟数据做T+1 VWAP）
            if self._pending_opens:
                pending_minute_df = (
                    minute_df[minute_df['ths_code'].isin(pending_codes)].copy()
                    if pending_codes and not minute_df.empty
                    else pd.DataFrame()
                )
                if not pending_minute_df.empty:
                    self._execute_pending_opens(pending_minute_df, date_str)

            intraday_exit_done = False
            if self.positions:
                if self.config.get('skip_same_day_exit_for_vwap_opens', True):
                    held_codes = {
                        pos.code for pos in self.positions
                        if pos.code and pos.open_date != date_str
                    }
                else:
                    held_codes = {pos.code for pos in self.positions if pos.code}
                exit_minute_df = (
                    minute_df[minute_df['ths_code'].isin(held_codes)].copy()
                    if held_codes and not minute_df.empty
                    else pd.DataFrame()
                )
                if held_codes and not exit_minute_df.empty:
                    intraday_exit_done = self._process_intraday_exits(exit_minute_df, date_str)

            # Phase A+B: 用日频聚合数据（不需要分钟明细）
            daily_df = self.loader.get_daily_agg(date_str, self.ci)
            if daily_df.empty:
                logger.debug("  %s 无日频数据，跳过", date_str)
                if self.positions:
                    self._update_nav_snapshot(date_str)
                continue

            # 更新持仓价格（从日频聚合数据）
            self._update_positions_from_daily(daily_df, date_str)
            self._update_s1_shadow_candidate_outcomes(daily_df, date_str)

            # 收盘决策
            self._process_daily_decision(
                daily_df, date_str, product_pool,
                run_risk_and_tp=not intraday_exit_done,
            )

            # NAV快照
            self._update_nav_snapshot(date_str)

            day_elapsed = time.time() - t_day
            if day_elapsed > 120:
                logger.warning("  %s 耗时 %.0f秒", date_str, day_elapsed)

        self._finalize_s1_shadow_candidate_outcomes()

        total_elapsed = time.time() - t0
        logger.info("回测完成: %.0f秒, 平均 %.1f秒/天",
                    total_elapsed, total_elapsed / max(len(dates), 1))

        # 输出
        nav_df = pd.DataFrame(self.nav_records)
        orders_df = pd.DataFrame(self.orders)
        stats = calc_stats(nav_df['nav'].values) if len(nav_df) > 0 and 'nav' in nav_df.columns else {}
        if len(nav_df) > 0:
            self._output_results(nav_df, orders_df, stats, tag, total_elapsed)
        else:
            logger.warning("无NAV数据，跳过输出")
            orders_df.to_csv(os.path.join(OUTPUT_DIR, f"orders_{tag}.csv"), index=False)
            self._write_s1_candidate_outputs(tag)

        # 打印结果
        print("\n=== 回测结果 ===")
        for k, v in stats.items():
            print(f"  {k}: {v:.4f}")

        return {'nav_df': nav_df, 'orders_df': orders_df, 'stats': stats}

    def _get_default_product_pool(self):
        """默认品种池：按合约数量排序取Top20"""
        products = self.ci.get_all_products()
        valid = []
        for product in products:
            key = str(product).strip().upper()
            if not key:
                continue
            if key.isalpha() and len(key) <= 4:
                valid.append(key)
            elif key.isdigit() and len(key) == 6:
                valid.append(key)
        return set(sorted(valid))

    @staticmethod
    def _product_first_trade_cache_path():
        return product_first_trade_cache_path(CACHE_DIR)

    @staticmethod
    def _coerce_trade_date_str(value):
        return coerce_trade_date_str(value)

    def _load_product_first_trade_cache(self):
        cache_path = self._product_first_trade_cache_path()
        self._product_first_trade_dates.update(
            load_first_trade_cache(cache_path, logger=logger)
        )

    def _save_product_first_trade_cache(self):
        cache_path = self._product_first_trade_cache_path()
        save_first_trade_cache(cache_path, self._product_first_trade_dates, logger=logger)

    def _ensure_product_first_trade_dates(self, product_pool):
        if not self._product_first_trade_dates:
            self._load_product_first_trade_cache()

    def _update_product_first_trade_dates(self, products, trade_date):
        if update_first_trade_dates(self._product_first_trade_dates, products, trade_date):
            self._save_product_first_trade_cache()

    def _update_product_first_trade_dates_from_frame(self, df, product_col='product', date_col='trade_date'):
        if update_first_trade_dates_from_frame(
            self._product_first_trade_dates,
            df,
            product_col=product_col,
            date_col=date_col,
        ):
            self._save_product_first_trade_cache()

    def _product_observation_ready(self, product, date_str):
        observation_months = int(self.config.get('product_observation_months', 0) or 0)
        min_listing_days = int(self.config.get('product_min_listing_days', 0) or 0)
        return product_observation_ready(
            self._product_first_trade_dates,
            product,
            date_str,
            observation_months=observation_months,
            min_listing_days=min_listing_days,
        )

    def _passes_product_entry_filters(self, product, date_str):
        return self._product_observation_ready(product, date_str)

    @staticmethod
    def _preview_sql(sql, max_len=240):
        if sql is None:
            return "None"
        text = str(sql)
        if len(text) <= max_len:
            return text
        return text[:max_len] + " ..."

    @staticmethod
    def _normalize_product_key(product):
        return normalize_product_key(product)

    def _get_product_bucket(self, product):
        return get_product_bucket(product)

    def _get_product_corr_group(self, product):
        return get_product_corr_group(product)

    def _iter_open_sell_exposures(self, include_pending=True):
        yield from port_risk.iter_open_sell_exposures(
            self.positions,
            self._pending_opens,
            self._normalize_product_key,
            include_pending=include_pending,
        )

    def _get_open_sell_margin_total(self, strat=None, include_pending=True):
        return port_risk.get_open_sell_margin_total(
            self.positions,
            self._pending_opens,
            strat=strat,
            include_pending=include_pending,
        )

    def _get_product_return_series(self, product, current_date=None):
        return port_risk.product_return_series(
            self._spot_history,
            self._normalize_product_key,
            product,
            current_date=current_date,
        )

    def _get_recent_product_corr(self, product, peer_product, current_date):
        return port_risk.recent_product_corr(
            self.config,
            self._spot_history,
            self._normalize_product_key,
            product,
            peer_product,
            current_date,
        )

    def _recent_product_momentum(self, product, date_str, lookback):
        returns = self._get_product_return_series(product, current_date=date_str)
        if returns.empty:
            return np.nan
        lb = max(1, int(lookback or 1))
        return float(returns.tail(lb).sum())

    def _s1_trend_confidence_info(self, product, date_str, iv_state=None):
        returns = self._get_product_return_series(product, current_date=date_str)
        iv_state = iv_state or {}
        return classify_s1_trend_confidence(
            returns,
            rv_trend=iv_state.get('rv_trend', np.nan),
            short_lookback=self.config.get('s1_trend_short_lookback', 5),
            medium_lookback=self.config.get('s1_trend_medium_lookback', 10),
            long_lookback=self.config.get('s1_trend_long_lookback', 20),
            min_history=self.config.get('s1_trend_min_history', 10),
            trend_threshold=self.config.get('s1_trend_threshold', 0.018),
            range_threshold=self.config.get('s1_trend_range_threshold', 0.010),
            rv_rising_threshold=self.config.get('s1_trend_rv_rising_threshold', 0.015),
            range_pressure_enabled=bool(self.config.get('s1_trend_range_pressure_enabled', False)),
            range_pressure_lookback=self.config.get('s1_trend_range_pressure_lookback', 20),
            range_pressure_upper=self.config.get('s1_trend_range_pressure_upper', 0.80),
            range_pressure_lower=self.config.get('s1_trend_range_pressure_lower', 0.20),
            range_pressure_min_short_ret=self.config.get(
                's1_trend_range_pressure_min_short_ret',
                0.004,
            ),
        )

    def _get_open_concentration_state(self, include_pending=True):
        return port_risk.get_open_concentration_state(
            self.positions,
            self._pending_opens,
            self._normalize_product_key,
            self._get_product_bucket,
            self._get_product_corr_group,
            include_pending=include_pending,
        )

    def _passes_portfolio_construction(self, product, nav, new_margin, date_str=None,
                                       new_cash_vega=0.0, new_cash_gamma=0.0,
                                       new_stress_loss=0.0, budget=None,
                                       include_pending=True, option_type=None,
                                       code=None, new_lots=0.0):
        budget = budget or self._current_open_budget or self._get_effective_open_budget()
        return port_risk.passes_portfolio_construction(
            self.config,
            product=product,
            nav=nav,
            new_margin=new_margin,
            positions=self.positions,
            pending_opens=self._pending_opens,
            spot_history=self._spot_history,
            normalize_product_key=self._normalize_product_key,
            get_product_bucket=self._get_product_bucket,
            get_product_corr_group=self._get_product_corr_group,
            date_str=date_str,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
            new_stress_loss=new_stress_loss,
            budget=budget,
            include_pending=include_pending,
            option_type=option_type,
            code=code,
            new_lots=new_lots,
        )

    def _diversify_product_order(self, products):
        return port_risk.diversify_product_order(
            self.config,
            products,
            self._get_product_bucket,
        )

    def _prioritize_products_by_regime(self, products):
        return vol_rules.prioritize_products_by_regime(
            self.config,
            products,
            self._current_vol_regimes,
        )

    def _start_s1_candidate_funnel(self, date_str, nav, product_pool):
        if not self.config.get('s1_candidate_funnel_enabled', True):
            self._s1_candidate_funnel = None
            return
        self._s1_candidate_funnel = defaultdict(float)
        self._bump_s1_funnel('product_pool', len(product_pool or []))
        self._s1_candidate_funnel['nav'] = float(nav or 0.0)
        self._s1_candidate_funnel['date_marker'] = 1.0

    def _bump_s1_funnel(self, key, amount=1):
        if self._s1_candidate_funnel is None:
            return
        try:
            self._s1_candidate_funnel[str(key)] += float(amount)
        except (TypeError, ValueError):
            self._s1_candidate_funnel[str(key)] += 1.0

    def _finish_s1_candidate_funnel(self, date_str, nav):
        counts = self._s1_candidate_funnel
        self._s1_candidate_funnel = None
        if not counts:
            return
        candidate_products = counts.get('candidate_products', 0.0)
        product_entry_pass = counts.get('product_entry_pass', 0.0)
        side_with_candidates = counts.get('side_with_candidates', 0.0)
        side_selected = counts.get('side_selected', 0.0)
        open_candidates = counts.get('open_candidates_after_ladder', 0.0)
        open_sell_legs = counts.get('open_sell_legs', 0.0)
        record = {
            'date': date_str,
            'scope': 's1_candidate_funnel',
            'name': 'daily',
            'portfolio_vol_regime': self._current_portfolio_regime,
            'candidate_count': candidate_products,
            'n_products': candidate_products,
            'n_positions': open_sell_legs,
            'product_entry_pass_rate': (
                product_entry_pass / candidate_products if candidate_products > 0 else np.nan
            ),
            'side_select_rate': (
                side_selected / side_with_candidates if side_with_candidates > 0 else np.nan
            ),
            'open_conversion_rate': (
                open_sell_legs / open_candidates if open_candidates > 0 else np.nan
            ),
        }
        for key, value in sorted(counts.items()):
            if key in {'date_marker', 'nav'}:
                continue
            record[f'funnel_{key}'] = value
        self.diagnostics_records.append(record)

    def _build_product_like_sql(self, product_pool):
        """构建品种的ths_code LIKE过滤SQL"""
        normalized_pool = normalize_product_pool(product_pool)
        if not normalized_pool:
            return None
        cached_sql = self._product_like_sql_cache.get(normalized_pool)
        if cached_sql is not None:
            return cached_sql

        like_sql = build_product_like_sql(
            normalized_pool,
            self.ci._cache,
            self.ci.get_product_codes,
        )
        self._product_like_sql_cache[normalized_pool] = like_sql
        return like_sql

    def _get_warmup_contract_codes(self, product_pool, warmup_dates):
        return get_warmup_contract_codes(
            product_pool,
            warmup_dates,
            self.ci,
            max_dte=int(self.config.get('dte_max', 90) or 90),
            cache=self._warmup_contract_sql_cache,
        )

    def _warmup_iv_consistent(self, warmup_dates, product_pool):
        """Warm up ATM IV history through the extracted IV warmup pipeline."""
        like_sql = self._build_product_like_sql(product_pool)
        contract_codes = self._get_warmup_contract_codes(product_pool, warmup_dates)
        context = IVWarmupContext(
            config=self.config,
            contract_info=self.ci,
            iv_history=self._iv_history,
            spot_history=self._spot_history,
            select_sql=select_bars_sql,
            calc_iv_batch=calc_iv_batch,
            update_product_first_trade_dates_from_frame=self._update_product_first_trade_dates_from_frame,
            spot_tables_for_codes=self._spot_tables_for_codes,
            option_minute_table=OPTION_MINUTE_TABLE,
            risk_free_rate=RISK_FREE_RATE,
            logger=logger,
        )
        warmup_iv_consistent(
            warmup_dates,
            product_pool,
            like_sql=like_sql,
            contract_codes=contract_codes,
            context=context,
        )

    # _estimate_spot_from_daily 已被向量化的 put-call parity merge 替代


    # ── Phase 0: T+1 执行 ────────────────────────────────────────────────────

    def _pending_open_execution_risk_ok(self, item, code, actual_n, price, nav, date_str):
        """Re-check execution-day margin and portfolio limits before opening."""
        if item['role'] != 'sell':
            return True
        strat = item['strat']
        exec_budget = self._execution_budget_for_item(item)
        total_m = self._get_open_sell_margin_total(include_pending=False)
        strat_m = self._get_open_sell_margin_total(strat, include_pending=False)
        new_m = estimate_margin(
            item['spot'], item['strike'], item['opt_type'],
            price, item['mult'], item['mr'], 0.5,
            exchange=item['exchange'], product=item.get('product')
        ) * actual_n
        cap_key = f"{strat.lower()}_margin_cap"
        effective_strategy_cap = exec_budget.get(cap_key, self.config.get(cap_key, 0.25))
        if not port_risk.check_margin_ok(
            total_m, strat_m, new_m, nav,
            exec_budget.get('margin_cap', self.config.get('margin_cap', 0.50)),
            effective_strategy_cap,
        ):
            return False
        item_n = float(item.get('n', 0) or 0)
        exposure_scale = actual_n / item_n if item_n > 0 else 0.0
        new_cash_vega = float(item.get('cash_vega', 0.0) or 0.0) * exposure_scale
        new_cash_gamma = float(item.get('cash_gamma', 0.0) or 0.0) * exposure_scale
        new_stress_loss = float(item.get('one_contract_stress_loss', 0.0) or 0.0) * actual_n
        return self._passes_portfolio_construction(
            item.get('product', ''), nav, new_m, date_str=date_str,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
            new_stress_loss=new_stress_loss,
            budget=exec_budget,
            include_pending=False,
            option_type=item.get('opt_type'),
            code=code,
            new_lots=actual_n,
        )

    def _build_position_from_pending_open(
        self,
        item,
        code,
        actual_n,
        price,
        raw_execution_price,
        execution_slippage,
        date_str,
    ):
        """Create a Position and attach entry metadata for a filled open."""
        pos = Position(
            item['strat'], item['product'], code, item['opt_type'],
            item['strike'], price, actual_n, date_str,
            item['mult'], item['expiry'], item['mr'], item['role'],
            spot=item['spot'], exchange=item['exchange'],
            group_id=item.get('group_id', ''),
            underlying_code=item.get('underlying_code', ''),
        )
        open_fee_per_contract = self._safe_float(
            item.get('open_fee_per_contract', np.nan),
            self._option_fee_per_contract(
                item.get('product'),
                item.get('opt_type'),
                action='open',
                default=self.config.get('fee', 0.0),
            ),
        )
        close_fee_per_contract = self._safe_float(
            item.get('close_fee_per_contract', np.nan),
            self._option_fee_per_contract(
                item.get('product'),
                item.get('opt_type'),
                action='close',
                default=self.config.get('fee', 0.0),
            ),
        )
        roundtrip_fee_per_contract = open_fee_per_contract + close_fee_per_contract
        item['open_fee_per_contract'] = open_fee_per_contract
        item['close_fee_per_contract'] = close_fee_per_contract
        item['roundtrip_fee_per_contract'] = roundtrip_fee_per_contract
        pos.entry_meta = self._entry_meta_from_item(item)
        slippage_cash = float(execution_slippage) * float(item['mult']) * float(actual_n)
        pos.entry_meta.update({
            'open_raw_execution_price': raw_execution_price,
            'open_execution_slippage': execution_slippage,
            'open_execution_slippage_cash': slippage_cash,
        })
        one_loss = float(item.get('one_contract_stress_loss', 0.0) or 0.0)
        if one_loss > 0:
            pos.stress_loss = one_loss * actual_n
        self._set_open_greeks_for_attribution(pos, date_str)
        return (
            pos,
            open_fee_per_contract,
            close_fee_per_contract,
            roundtrip_fee_per_contract,
            slippage_cash,
        )

    @staticmethod
    def _trim_s1_open_candidates(candidates, *, baseline_mode, split_enabled,
                                 max_candidates, max_delta_gap):
        if candidates is None or candidates.empty:
            return candidates
        if baseline_mode:
            return candidates.head(max_candidates) if max_candidates > 0 else candidates
        if split_enabled and max_candidates > 1:
            trimmed = candidates
            if max_delta_gap > 0 and 'abs_delta' in candidates.columns:
                center_delta = float(candidates['abs_delta'].iloc[0])
                delta = pd.to_numeric(candidates['abs_delta'], errors='coerce')
                trimmed = candidates[(delta - center_delta).abs() <= max_delta_gap]
            return trimmed.head(max_candidates)
        return candidates.head(1)

    def _execute_pending_opens(self, minute_df, date_str):
        """
        执行昨日决策的待开仓，用T+1当日全日TWAP。
        成交量约束：每天每合约最多下当日成交量的10%，超量部分留到下一天。
        """
        if not self._pending_opens:
            return
        nav = self.capital + (self.nav_records[-1]['cum_pnl'] if self.nav_records else 0)
        vol_limit_pct = self.config.get('volume_limit_pct', 0.10)  # 日成交量的10%
        executed = 0
        deferred = []  # 超量部分留到下一天
        open_context = build_open_execution_context(minute_df)

        for item in self._pending_opens:
            code = item['code']
            code_bars = open_context.bars_by_code.get(code)

            # 计算全天TWAP/VWAP（只用当日真实分钟成交）
            price = estimate_volume_weighted_close(code_bars)

            if price <= 0:
                continue

            raw_execution_price = price
            open_action = 'sell_open' if item['role'] == 'sell' else 'buy_open'
            price, execution_slippage = apply_execution_slippage(
                raw_execution_price,
                open_action,
                self.config,
            )

            # 成交量约束：用执行日全日成交量，匹配“全天TWAP执行”的口径
            if price <= 0:
                continue

            target_n = item['n']
            today_vol = open_context.day_volume.get(code, 0)
            actual_n, remaining_n = split_open_quantity(target_n, today_vol, vol_limit_pct)

            if remaining_n > 0:
                # 超量部分留到下一天
                deferred.append(scale_deferred_open_item(item, remaining_n))
                logger.debug("  %s 分批建仓: %s 目标%d手, 今日%d手(当日成交%d), 剩余%d手",
                             date_str, code, target_n, actual_n, today_vol, remaining_n)

            if actual_n <= 0:
                continue

            if not self._pending_open_execution_risk_ok(item, code, actual_n, price, nav, date_str):
                continue

            (
                pos,
                open_fee_per_contract,
                close_fee_per_contract,
                roundtrip_fee_per_contract,
                slippage_cash,
            ) = self._build_position_from_pending_open(
                item,
                code,
                actual_n,
                price,
                raw_execution_price,
                execution_slippage,
                date_str,
            )
            self.positions.append(pos)
            open_fee = open_fee_per_contract * actual_n
            self._day_realized['fee'] += open_fee
            gross_premium_cash = float(price) * float(item['mult']) * float(actual_n)
            net_premium_cash = (
                gross_premium_cash - open_fee
                if item['role'] == 'sell'
                else -gross_premium_cash - open_fee
            )
            open_margin = pos.cur_margin() if item['role'] == 'sell' else 0.0
            self.orders.append(build_open_order_record(
                date_str=date_str,
                item=item,
                code=code,
                actual_n=actual_n,
                price=price,
                raw_execution_price=raw_execution_price,
                execution_slippage=execution_slippage,
                slippage_cash=slippage_cash,
                open_fee=open_fee,
                open_fee_per_contract=open_fee_per_contract,
                close_fee_per_contract=close_fee_per_contract,
                roundtrip_fee_per_contract=roundtrip_fee_per_contract,
                pos=pos,
                open_margin=open_margin,
                gross_premium_cash=gross_premium_cash,
                net_premium_cash=net_premium_cash,
            ))
            executed += 1

        if executed:
            logger.debug("  T+1执行 %d/%d笔, 延期%d笔", executed, len(self._pending_opens), len(deferred))
        # 超量部分留到下一天继续执行
        self._pending_opens = deferred

    # ── Phase A: 盘中更新 ────────────────────────────────────────────────────

    def _update_positions_from_daily(self, daily_df, date_str):
        """用日频聚合数据更新持仓价格和标的价格"""
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        price_idx = {}
        if {'option_code', 'option_close'}.issubset(daily_df.columns):
            valid_prices = daily_df.loc[
                daily_df['option_close'].fillna(0) > 0,
                ['option_code', 'option_close'],
            ].dropna(subset=['option_code'])
            valid_prices = valid_prices[valid_prices['option_code'].astype(str) != '']
            if not valid_prices.empty:
                valid_prices = valid_prices.drop_duplicates('option_code', keep='last')
                price_idx = dict(zip(valid_prices['option_code'], valid_prices['option_close']))

        spot_by_product = {}
        spot_by_underlying = {}
        if 'spot_close' in daily_df.columns:
            valid_spots = daily_df[daily_df['spot_close'].notna() & (daily_df['spot_close'] > 0)]
            if not valid_spots.empty and 'product' in valid_spots.columns:
                product_spots = valid_spots[
                    valid_spots['product'].notna() & (valid_spots['product'].astype(str) != '')
                ][['product', 'spot_close']].drop_duplicates('product', keep='last')
                spot_by_product = dict(zip(product_spots['product'], product_spots['spot_close']))
            if not valid_spots.empty and 'underlying_code' in valid_spots.columns:
                underlying_spots = valid_spots[
                    valid_spots['underlying_code'].notna()
                    & (valid_spots['underlying_code'].astype(str) != '')
                ][['underlying_code', 'spot_close']].drop_duplicates('underlying_code', keep='last')
                spot_by_underlying = dict(zip(underlying_spots['underlying_code'], underlying_spots['spot_close']))

        for pos in self.positions:
            if pos.code in price_idx:
                pos.cur_price = price_idx[pos.code]
            if pos.underlying_code and pos.underlying_code in spot_by_underlying:
                pos.cur_spot = spot_by_underlying[pos.underlying_code]
            elif pos.product in spot_by_product:
                pos.cur_spot = spot_by_product[pos.product]
            dte = self.ci.calc_dte(pos.code, current_date)
            if dte >= 0:
                pos.dte = dte

    # ── Phase B: 收盘决策 ────────────────────────────────────────────────────

    def _current_nav(self):
        """基于当前持仓价格估算当下NAV，用于盘中风控比例。"""
        base_cum_pnl = self.nav_records[-1]['cum_pnl'] if self.nav_records else 0.0
        holding_pnl = sum(p.daily_pnl() for p in self.positions)
        return self.capital + base_cum_pnl + holding_pnl + self._day_realized['pnl'] - self._day_realized['fee']

    def _refresh_position_greeks(self):
        """按当前价格和spot刷新持仓IV/Greeks。"""
        for pos in self.positions:
            if pos.cur_price > 0 and pos.cur_spot > 0 and pos.dte > 0:
                iv = calc_iv_single(pos.cur_price, pos.cur_spot, pos.strike, pos.dte, pos.opt_type, exchange=pos.exchange)
                if not np.isnan(iv) and iv > 0:
                    pos.cur_iv = iv
                    g = calc_greeks_single(pos.cur_spot, pos.strike, pos.dte, iv, pos.opt_type, exchange=pos.exchange)
                    if not np.isnan(g['delta']):
                        pos.cur_delta = g['delta']
                        pos.cur_gamma = g['gamma']
                        pos.cur_vega = g['vega']
                        pos.cur_theta = g['theta']

    def _set_open_greeks_for_attribution(self, pos, date_str):
        if not self.config.get('initialize_open_greeks_for_attribution', True):
            return
        try:
            current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return

        dte = self.ci.calc_dte(pos.code, current_date)
        if dte >= 0:
            pos.dte = dte
        if pos.open_price <= 0 or pos.cur_spot <= 0 or pos.dte <= 0:
            return

        iv = calc_iv_single(
            pos.open_price, pos.cur_spot, pos.strike, pos.dte,
            pos.opt_type, exchange=pos.exchange,
        )
        if not np.isfinite(iv) or iv <= 0:
            return
        greeks = calc_greeks_single(
            pos.cur_spot, pos.strike, pos.dte, iv,
            pos.opt_type, exchange=pos.exchange,
        )
        if not np.isfinite(greeks.get('delta', np.nan)):
            return

        pos.prev_iv = pos.cur_iv = float(iv)
        pos.prev_delta = pos.cur_delta = float(greeks['delta'])
        pos.prev_gamma = pos.cur_gamma = float(greeks['gamma'])
        pos.prev_vega = pos.cur_vega = float(greeks['vega'])
        pos.prev_theta = pos.cur_theta = float(greeks['theta'])

    def _s1_stop_scope_positions(self, trigger_pos, scope, multiple=0.0):
        return select_s1_stop_scope_positions(
            self.positions,
            trigger_pos,
            scope,
            multiple=float(multiple or 0.0),
        )

    @staticmethod
    def _s1_layer_level_key(level):
        return s1_layer_level_key(level)

    def _s1_layered_stop_levels(self):
        return parse_s1_layered_stop_levels(self.config.get('s1_layered_stop_levels') or [])

    def _close_s1_stop_scope(self, trigger_pos, date_str, reason, fee_per_hand,
                             exec_time='', scope='group', multiple=0.0,
                             action='close', ratio=1.0):
        to_close = self._s1_stop_scope_positions(trigger_pos, scope, multiple=multiple)
        if not to_close:
            return False
        action = str(action or 'close').lower()
        if action == 'warn':
            return True
        qty_by_pos = None
        if action == 'reduce':
            qty_by_pos = {}
            ratio = min(max(float(ratio or 0.0), 0.0), 1.0)
            if ratio <= 0:
                return True
            for pos in to_close:
                qty = int(np.ceil(float(pos.n) * ratio))
                qty = min(max(qty, 1), int(pos.n))
                qty_by_pos[pos] = qty
        self._close_positions(
            to_close,
            date_str,
            reason,
            fee_per_hand,
            exec_time=exec_time,
            close_qty_by_pos=qty_by_pos,
        )
        return True

    def _apply_s1_layered_premium_stop(self, pos, date_str, fee, product_iv_pcts=None, exec_time=''):
        for level in self._s1_layered_stop_levels():
            key = self._s1_layer_level_key(level)
            if key in self._layered_stop_done[id(pos)]:
                continue
            multiple = float(level.get('multiple', 0.0) or 0.0)
            if not self._premium_stop_hit(pos, multiple, product_iv_pcts):
                continue
            self._layered_stop_done[id(pos)].add(key)
            reason = f"sl_{pos.strat.lower()}_{level.get('action', 'close')}_{int(round(multiple * 100))}"
            return self._close_s1_stop_scope(
                pos,
                date_str,
                reason,
                fee,
                exec_time=exec_time,
                scope=level.get('scope', 'contract'),
                multiple=multiple,
                action=level.get('action', 'close'),
                ratio=level.get('ratio', 1.0),
            )
        return False

    def _calc_product_iv_pcts(self, daily_df, date_str):
        """基于日频ATM IV更新历史分位，并生成低IV准入所需状态。"""
        cfg = self.config
        product_iv_pcts = {}
        product_iv_state = {}
        target_dte = float(cfg.get('dte_target', 35))
        required_cols = {'product', 'moneyness', 'dte', 'implied_vol'}
        if not required_cols.issubset(daily_df.columns):
            self._update_contract_iv_history(daily_df, date_str)
            self._current_iv_pcts = product_iv_pcts
            self._current_iv_state = product_iv_state
            self._refresh_vol_regime_state(date_str)
            return product_iv_pcts

        atm_mask = (
            daily_df['moneyness'].between(0.95, 1.05)
            & daily_df['dte'].between(15, 90)
            & (daily_df['implied_vol'] > 0)
        )
        daily_atm_ivs = daily_df.loc[atm_mask].groupby('product', sort=False)['implied_vol'].mean()

        daily_spots = {}
        spot_cols = {'product', 'expiry_date', 'dte', 'spot_close'}
        if spot_cols.issubset(daily_df.columns):
            spot_candidates = daily_df.loc[
                daily_df['spot_close'].notna() & (daily_df['spot_close'] > 0),
                ['product', 'expiry_date', 'dte', 'spot_close'],
            ].copy()
            if not spot_candidates.empty:
                spot_candidates['dte_dist'] = (spot_candidates['dte'] - target_dte).abs()
                spot_candidates = spot_candidates.sort_values(
                    ['product', 'dte_dist', 'dte', 'expiry_date'],
                    ascending=[True, True, True, True],
                    kind='mergesort',
                )
                spot_candidates = spot_candidates.drop_duplicates('product', keep='first')
                daily_spots = dict(zip(spot_candidates['product'], spot_candidates['spot_close']))

        for product, daily_atm_iv in daily_atm_ivs.items():
            daily_atm_iv = float(daily_atm_iv)
            hist = self._iv_history[product]
            hist['dates'].append(date_str)
            hist['ivs'].append(daily_atm_iv)
            iv_series = pd.Series(hist['ivs'], index=hist['dates'])
            iv_pct = calc_iv_percentile(
                iv_series, date_str,
                window=cfg.get('iv_window', 252),
                min_periods=cfg.get('iv_min_periods', 60),
            )
            product_iv_pcts[product] = iv_pct

            daily_spot = float(daily_spots.get(product, np.nan))

            if pd.notna(daily_spot) and daily_spot > 0:
                spot_hist = self._spot_history[product]
                spot_hist['dates'].append(date_str)
                spot_hist['spots'].append(daily_spot)
                spot_series = pd.Series(spot_hist['spots'], index=spot_hist['dates'])
            else:
                spot_series = pd.Series(dtype=float)

            features = calc_iv_rv_features(
                iv_series,
                spot_series,
                date_str,
                rv_window=cfg.get('rv_lookback', 20),
                min_periods=cfg.get('rv_min_periods', 10),
            )
            product_iv_state[product] = {
                'iv_pct': iv_pct,
                'atm_iv': daily_atm_iv,
                'spot_close': daily_spot,
                'rv20': features.get('rv20', np.nan),
                'iv_rv_spread': features.get('iv_rv_spread', np.nan),
                'iv_rv_ratio': features.get('iv_rv_ratio', np.nan),
                'rv_trend': features.get('rv_trend', np.nan),
                'iv_trend': self._last_iv_trend(product),
            }
        self._update_contract_iv_history(daily_df, date_str)
        self._current_iv_pcts = product_iv_pcts
        self._current_iv_state = product_iv_state
        self._refresh_vol_regime_state(date_str)
        return product_iv_pcts

    def _apply_exit_rules(self, date_str, fee, product_iv_pcts=None,
                          check_greeks=True, check_tp=True, check_expiry=True,
                          exec_time=''):
        """执行平仓规则；可用于盘中或收盘。"""
        cfg = self.config
        product_iv_pcts = product_iv_pcts or {}
        nav = max(self._current_nav(), 1.0)
        skip_same_day_vwap = bool(cfg.get('skip_same_day_exit_for_vwap_opens', True))

        def is_exit_eligible(pos):
            # Full-day VWAP fills are only known after the day is complete.
            # Do not let same-day exits use that fill price.
            return not (skip_same_day_vwap and pos.open_date == date_str)

        if check_greeks and cfg.get('greeks_exit_enabled', False):
            delta_hard = cfg.get('greeks_delta_hard', 0.10)
            delta_target = cfg.get('greeks_delta_target', 0.07)
            vega_hard = cfg.get('greeks_vega_hard', 0.01)
            vega_target = cfg.get('greeks_vega_target', 0.007)
            eligible_positions = [p for p in self.positions if is_exit_eligible(p)]

            gross_delta_pct = sum(p.cash_delta() for p in eligible_positions) / nav
            gross_vega_pct = sum(p.cash_vega() for p in eligible_positions) / nav

            if abs(gross_delta_pct) > delta_hard:
                logger.info("  %s%s Delta超限: %.1f%% > %.1f%%, 触发减仓",
                            date_str, f' {exec_time}' if exec_time else '',
                            gross_delta_pct * 100, delta_hard * 100)
                sell_positions = sorted(
                    [p for p in eligible_positions if p.role == 'sell'],
                    key=lambda p: abs(p.cash_delta()), reverse=True
                )
                closed_groups = set()
                for pos in sell_positions:
                    cur_nav = max(self._current_nav(), 1.0)
                    current_delta = sum(p.cash_delta() for p in self.positions if is_exit_eligible(p))
                    if abs(current_delta / cur_nav) <= delta_target:
                        break
                    gid = pos.group_id
                    if gid in closed_groups:
                        continue
                    self._close_group(pos, date_str, 'greeks_delta_breach', fee, exec_time=exec_time)
                    if gid:
                        closed_groups.add(gid)

            if eligible_positions and abs(sum(p.cash_vega() for p in eligible_positions) / max(self._current_nav(), 1.0)) > vega_hard:
                logger.info("  %s%s Vega超限: %.3f%% > %.1f%%, 触发减仓",
                            date_str, f' {exec_time}' if exec_time else '',
                            abs(sum(p.cash_vega() for p in eligible_positions) / max(self._current_nav(), 1.0)) * 100,
                            vega_hard * 100)
                sell_positions = sorted(
                    [p for p in eligible_positions if p.role == 'sell'],
                    key=lambda p: abs(p.cash_vega()), reverse=True
                )
                closed_groups = set()
                for pos in sell_positions:
                    cur_nav = max(self._current_nav(), 1.0)
                    current_vega = sum(p.cash_vega() for p in self.positions if is_exit_eligible(p))
                    if abs(current_vega / cur_nav) <= vega_target:
                        break
                    gid = pos.group_id
                    if gid in closed_groups:
                        continue
                    self._close_group(pos, date_str, 'greeks_vega_breach', fee, exec_time=exec_time)
                    if gid:
                        closed_groups.add(gid)

        if check_tp:
            take_profit_enabled = bool(cfg.get('take_profit_enabled', False))
            premium_stop_multiple = float(cfg.get('premium_stop_multiple', 0.0) or 0.0)
            for pos in list(self.positions):
                if pos.role != 'sell' or pos not in self.positions:
                    continue
                if not is_exit_eligible(pos):
                    continue
                if cfg.get('s1_layered_stop_enabled', False) and pos.strat == 'S1':
                    if self._apply_s1_layered_premium_stop(
                        pos, date_str, fee, product_iv_pcts=product_iv_pcts, exec_time=exec_time
                    ):
                        continue
                elif premium_stop_multiple > 0 and self._should_trigger_premium_stop(pos, product_iv_pcts):
                    scope = cfg.get('s1_stop_close_scope', 'group') if pos.strat == 'S1' else 'group'
                    self._close_s1_stop_scope(
                        pos,
                        date_str,
                        f'sl_{pos.strat.lower()}',
                        fee,
                        exec_time=exec_time,
                        scope=scope,
                        multiple=premium_stop_multiple,
                        action='close',
                    )
                    continue
                if take_profit_enabled:
                    gid = pos.group_id
                    tp = cfg.get('s1_tp', 0.40) if pos.strat == 'S1' else cfg.get('s3_tp', 0.30)
                    tp_fee = self._position_roundtrip_fee_per_side(pos, default=fee)
                    if pos.profit_pct(tp_fee) >= tp and pos.dte > cfg.get('tp_min_dte', 5):
                        self._close_group(pos, date_str, f'tp_{pos.strat.lower()}', fee, exec_time=exec_time)

        if check_expiry:
            for pos in list(self.positions):
                if pos not in self.positions:
                    continue
                pre_expiry_exit_dte = int(cfg.get('pre_expiry_exit_dte', 2))
                expiry_dte = int(cfg.get('expiry_dte', 1))
                if pos.role == 'sell' and pre_expiry_exit_dte > expiry_dte and expiry_dte < pos.dte <= pre_expiry_exit_dte:
                    self._close_group(pos, date_str, 'pre_expiry_roll', fee, exec_time=exec_time)
                elif pos.dte <= expiry_dte:
                    if pos.cur_spot and pos.cur_spot > 0:
                        if pos.opt_type == 'C':
                            intrinsic = max(pos.cur_spot - pos.strike, 0)
                        else:
                            intrinsic = max(pos.strike - pos.cur_spot, 0)
                        pos.cur_price = intrinsic
                    self._close_group(pos, date_str, 'expiry', fee, exec_time=exec_time)
                elif pos.strat == 'S4' and pos.role == 'buy' and pos.dte < 10:
                    self._close_group(pos, date_str, 's4_dte_exit', fee, exec_time=exec_time)

    def _is_intraday_stop_price_illiquid(self, code, price, volume, pos_by_code, qty_by_code):
        return is_intraday_stop_price_illiquid(
            self.config, code, price, volume, pos_by_code, qty_by_code
        )

    def _intraday_stop_threshold(self, code, pos_by_code):
        return intraday_stop_threshold(self.config, code, pos_by_code)

    def _intraday_stop_required_volume(self, code, qty_by_code):
        return intraday_stop_required_volume(self.config, code, qty_by_code)

    def _prefilter_intraday_exit_codes_by_daily_high(self, date_str, exit_codes):
        """Skip minute stop scans when the daily high cannot reach any stop line."""
        high_map = self.loader.get_daily_option_high_map(date_str)
        code_set = set(exit_codes or [])
        keep_codes = prefilter_intraday_exit_codes_by_daily_high(
            config=self.config,
            positions=self.positions,
            high_map=high_map,
            exit_codes=code_set,
        )
        skipped_codes = code_set - keep_codes
        if skipped_codes:
            logger.debug(
                "  %s 日内止损预筛跳过 %d/%d 个持仓合约",
                date_str, len(skipped_codes), len(code_set),
            )
        return keep_codes

    def _confirm_intraday_stop_price(self, code, price, volume, tm, stop_pending, pos_by_code, qty_by_code):
        return confirm_intraday_stop_price(
            config=self.config,
            code=code,
            price=price,
            volume=volume,
            tm=tm,
            stop_pending=stop_pending,
            positions_by_code=pos_by_code,
            quantity_by_code=qty_by_code,
        )

    def _process_intraday_exits(self, minute_df, date_str):
        """盘中逐分钟监控止损；默认按触发分钟 close，可配置为下一分钟 high 压力成交。"""
        if minute_df.empty or not self.positions:
            return False

        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        fee = self.config.get('fee', 3)
        skip_same_day_vwap = bool(self.config.get('skip_same_day_exit_for_vwap_opens', True))
        eligible_positions = [
            pos for pos in self.positions
            if not (skip_same_day_vwap and pos.open_date == date_str)
        ]
        if not eligible_positions:
            return False
        held_codes = {pos.code for pos in eligible_positions}
        price_df = minute_df[minute_df['ths_code'].isin(held_codes)].copy()

        for pos in eligible_positions:
            dte = self.ci.calc_dte(pos.code, current_date)
            if dte >= 0:
                pos.dte = dte

        position_index = index_intraday_positions(eligible_positions)
        price_context = build_intraday_price_context(
            price_df,
            execution_mode=self.config.get('intraday_stop_execution_price_mode', 'current_close'),
        )
        if not price_context.time_points:
            return False

        def stop_execution_price(code, tm, fallback_price):
            return resolve_stop_execution_price(
                mode=price_context.stop_execution_mode,
                code=code,
                tm=tm,
                fallback_price=fallback_price,
                time_points=price_context.time_points,
                time_index=price_context.time_index,
                next_price_maps=price_context.next_price_maps,
            )

        def apply_scope_stop_execution_price(trigger_pos, tm, trigger_price):
            if price_context.stop_execution_mode != 'next_minute_high':
                return
            scope = (
                self.config.get('s1_stop_close_scope', 'group')
                if trigger_pos.strat == 'S1'
                else 'group'
            )
            multiple = float(self.config.get('premium_stop_multiple', 0.0) or 0.0)
            for pos in self._s1_stop_scope_positions(trigger_pos, scope, multiple=multiple):
                fallback = self._safe_float(getattr(pos, 'cur_price', np.nan), np.nan)
                if not np.isfinite(fallback) or fallback <= 0:
                    fallback = trigger_price
                pos.cur_price = stop_execution_price(pos.code, tm, fallback)

        monitor_times = self._sample_intraday_times(
            price_context.time_points,
            self.config.get('intraday_risk_interval', 15),
        )
        greek_refresh_interval = max(
            1,
            int(self.config.get(
                'intraday_greeks_refresh_interval',
                self.config.get('intraday_risk_interval', 15),
            ) or 15),
        )
        greek_refresh_times = set(self._sample_intraday_times(price_context.time_points, greek_refresh_interval))
        greek_refresh_times.add(price_context.time_points[-1])
        spot_groups = {}
        if self.config.get('intraday_refresh_spot_greeks_for_attribution', True):
            spot_df = self.loader.load_spot_day_minute(
                date_str,
                list(position_index.positions_by_underlying),
                time_list=greek_refresh_times,
            )
            if not spot_df.empty:
                spot_df = spot_df.sort_values(['time', 'underlying_code'])
                spot_groups = {tm: grp for tm, grp in spot_df.groupby('time')}
        stop_pending = {}
        if self.config.get('intraday_stop_confirmation_use_full_minutes', True):
            monitor_times = list(price_context.time_points)
        take_profit_enabled = bool(self.config.get('take_profit_enabled', False))
        closed_any = False
        for tm in monitor_times:
            spot_grp = spot_groups.get(tm)
            if spot_grp is not None:
                for row in spot_grp.itertuples(index=False):
                    for pos in position_index.positions_by_underlying.get(row.underlying_code, []):
                        pos.cur_spot = float(row.spot)

            grp = price_context.price_groups.get(tm)
            confirmed_stop_this_minute = False
            if grp is not None:
                for row in grp.itertuples(index=False):
                    code = row.ths_code
                    price = float(row.close)
                    volume = float(getattr(row, 'volume', 0.0) or 0.0)
                    threshold = self._intraday_stop_threshold(code, position_index.positions_by_code)
                    stop_candidate = np.isfinite(threshold) and threshold > 0 and price >= threshold
                    if not self._confirm_intraday_stop_price(
                        code,
                        price,
                        volume,
                        tm,
                        stop_pending,
                        position_index.positions_by_code,
                        position_index.quantity_by_code,
                    ):
                        continue
                    if not (stop_candidate or take_profit_enabled):
                        continue
                    confirmed_stop_this_minute = confirmed_stop_this_minute or stop_candidate
                    execution_price = stop_execution_price(code, tm, price)
                    for pos in position_index.positions_by_code.get(code, []):
                        pos.cur_price = execution_price
                        apply_scope_stop_execution_price(pos, tm, execution_price)

            if not (take_profit_enabled or confirmed_stop_this_minute):
                continue

            if self.config.get('intraday_refresh_spot_greeks_for_attribution', True) and tm in greek_refresh_times:
                self._refresh_position_greeks()

            before_positions = len(self.positions)
            before_qty = sum(int(getattr(pos, 'n', 0) or 0) for pos in self.positions)
            before_realized_pnl = float(self._day_realized.get('pnl', 0.0) or 0.0)
            before_realized_fee = float(self._day_realized.get('fee', 0.0) or 0.0)
            self._apply_exit_rules(
                date_str, fee, product_iv_pcts={},
                check_greeks=False, check_tp=True, check_expiry=False,
                exec_time=str(tm),
            )
            after_qty = sum(int(getattr(pos, 'n', 0) or 0) for pos in self.positions)
            if (
                len(self.positions) != before_positions
                or after_qty != before_qty
                or float(self._day_realized.get('pnl', 0.0) or 0.0) != before_realized_pnl
                or float(self._day_realized.get('fee', 0.0) or 0.0) != before_realized_fee
            ):
                closed_any = True
            if not self.positions:
                break

        return closed_any

    def _baseline_product_order(self, product_frames):
        mode = str(self.config.get('s1_baseline_product_ranking_mode', 'code') or 'code').lower()
        if mode not in {'liquidity', 'liquidity_oi', 'volume_oi'}:
            return sorted(product_frames)

        rows = []
        for product, frame in product_frames.items():
            volume, open_interest = self._baseline_product_liquidity_stats(product, frame)
            rows.append({
                'product': product,
                'volume': volume,
                'open_interest': open_interest,
            })
        if not rows:
            return sorted(product_frames)

        ranked = pd.DataFrame(rows)
        volume_rank_source = np.log1p(
            pd.to_numeric(ranked['volume'], errors='coerce').fillna(0).clip(lower=0)
        )
        oi_rank_source = np.log1p(
            pd.to_numeric(ranked['open_interest'], errors='coerce').fillna(0).clip(lower=0)
        )
        if len(ranked) > 1:
            ranked['liquidity_score'] = (
                0.5 * volume_rank_source.rank(pct=True) +
                0.5 * oi_rank_source.rank(pct=True)
            )
        else:
            ranked['liquidity_score'] = 0.0
        ranked = ranked.sort_values(
            ['liquidity_score', 'open_interest', 'volume', 'product'],
            ascending=[False, False, False, True],
            kind='mergesort',
        )
        return ranked['product'].tolist()

    @staticmethod
    def _contract_month_series(df):
        result = pd.Series(np.nan, index=df.index, dtype=float)
        for col in ('underlying_code', 'code', 'ths_code'):
            if col not in df.columns:
                continue
            text = df[col].astype(str).str.upper()
            month = text.str.extract(r'(\d{4})', expand=False)
            month = pd.to_numeric(month.str[-2:], errors='coerce')
            month = month.where(month.between(1, 12))
            result = result.where(result.notna(), month.astype(float))
        if 'expiry_date' in df.columns:
            expiry_month = pd.to_datetime(df['expiry_date'], errors='coerce').dt.month
            result = result.where(result.notna(), expiry_month.astype(float))
        return result

    def _s1_product_expiry_override(self, product):
        overrides = self.config.get('s1_product_expiry_overrides') or {}
        if not isinstance(overrides, dict):
            return {}
        key = str(product or '').strip().upper()
        override = overrides.get(key) or overrides.get(key.lower()) or {}
        return override if isinstance(override, dict) else {}

    def _select_s1_open_expiries(self, product_df_today, product):
        override = self._s1_product_expiry_override(product)
        mode = str(override.get('mode', self.config.get('s1_expiry_mode', 'dte')) or 'dte').lower()
        prod_df = product_df_today
        dte_target = override.get('dte_target', self.config.get('dte_target', 35))
        dte_min = override.get('dte_min', self.config.get('dte_min', 15))
        dte_max = override.get('dte_max', self.config.get('dte_max', 90))
        respect_dte_bounds = bool(override.get('respect_dte_bounds', False))

        if mode in {'allowed_contract_months', 'contract_months'}:
            months = {
                int(m) for m in override.get('months', [])
                if pd.notna(m) and 1 <= int(m) <= 12
            }
            if not months:
                return []
            contract_month = self._contract_month_series(prod_df)
            prod_df = prod_df[contract_month.isin(months)].copy()
            if prod_df.empty:
                return []
            mode = str(override.get('selection_mode', 'nth_expiry') or 'nth_expiry').lower()

        if respect_dte_bounds:
            if 'dte' not in prod_df.columns:
                return []
            dte = pd.to_numeric(prod_df['dte'], errors='coerce')
            prod_df = prod_df[dte.between(float(dte_min), float(dte_max), inclusive='both')].copy()
            if prod_df.empty:
                return []

        if mode in {'main_month', 'main_expiry', 'most_liquid_expiry'}:
            rows = []
            for exp, exp_data in prod_df.groupby('expiry_date', sort=False):
                if exp_data.empty:
                    continue
                dte = pd.to_numeric(exp_data['dte'], errors='coerce').dropna()
                if dte.empty or float(dte.iloc[0]) <= 0:
                    continue
                volume = pd.to_numeric(exp_data.get('volume', 0.0), errors='coerce').fillna(0).clip(lower=0)
                oi = pd.to_numeric(exp_data.get('open_interest', 0.0), errors='coerce').fillna(0).clip(lower=0)
                rows.append({
                    'expiry': str(exp),
                    'dte': float(dte.iloc[0]),
                    'volume': float(volume.sum()),
                    'open_interest': float(oi.sum()),
                })
            if not rows:
                return []
            ranked = pd.DataFrame(rows)
            ranked['score'] = np.log1p(ranked['volume']) + np.log1p(ranked['open_interest'])
            ranked = ranked.sort_values(
                ['score', 'open_interest', 'volume', 'dte', 'expiry'],
                ascending=[False, False, False, True, True],
                kind='mergesort',
            )
            return [ranked['expiry'].iloc[0]]

        return should_open_new(
            prod_df,
            dte_target=dte_target,
            dte_min=dte_min,
            dte_max=dte_max,
            mode=mode,
            expiry_rank=override.get('expiry_rank', self.config.get('s1_expiry_rank', 2)),
        )

    def _baseline_product_liquidity_stats(self, product, prod_df):
        open_expiries = self._select_s1_open_expiries(prod_df, product)
        if not open_expiries:
            return 0.0, 0.0

        total_volume = 0.0
        total_oi = 0.0
        for exp in open_expiries:
            ef = prod_df[prod_df['expiry_date'] == exp]
            if ef.empty:
                continue
            mult = float(ef['multiplier'].iloc[0] or 0.0)
            if mult <= 0:
                continue
            min_abs_delta, delta_cap = self._s1_delta_bounds(None)
            for option_type in ('P', 'C'):
                eligible = self._baseline_product_liquidity_eligible_frame(
                    ef, product, option_type, mult, min_abs_delta, delta_cap,
                )
                if eligible.empty:
                    continue
                volume = pd.to_numeric(eligible['volume'], errors='coerce').fillna(0).clip(lower=0)
                oi = pd.to_numeric(eligible['open_interest'], errors='coerce').fillna(0).clip(lower=0)
                total_volume += float(volume.sum())
                total_oi += float(oi.sum())
        return total_volume, total_oi

    def _baseline_product_liquidity_eligible_frame(self, ef, product, option_type, mult,
                                                   min_abs_delta, delta_cap):
        option_price = pd.to_numeric(ef['option_close'], errors='coerce').fillna(0)
        price_positive = option_price > 0
        if option_type == 'P':
            mask = (
                (ef['option_type'] == 'P') &
                (ef['moneyness'] < 1.0) &
                (ef['delta'] < 0) &
                (ef['delta'].abs() >= min_abs_delta) &
                (ef['delta'].abs() <= delta_cap) &
                price_positive
            )
        else:
            mask = (
                (ef['option_type'] == 'C') &
                (ef['moneyness'] > 1.0) &
                (ef['delta'] > 0) &
                (ef['delta'] >= min_abs_delta) &
                (ef['delta'] <= delta_cap) &
                price_positive
            )
        eligible = ef[mask].copy()
        if eligible.empty:
            return eligible

        min_option_price = float(self.config.get('s1_min_option_price', 0.0) or 0.0)
        if min_option_price > 0:
            eligible = eligible[
                pd.to_numeric(eligible['option_close'], errors='coerce').fillna(0) >= min_option_price
            ]
            if eligible.empty:
                return eligible
        roundtrip_fee = self._option_roundtrip_fee_per_contract(product, option_type)
        min_premium = roundtrip_fee * float(self.config.get('s1_min_premium_fee_multiple', 0.0) or 0.0)
        if min_premium > 0:
            eligible = eligible[eligible['option_close'] * float(mult) >= min_premium]
        min_volume = int(self.config.get('s1_min_volume', 0) or 0)
        min_oi = int(self.config.get('s1_min_oi', 0) or 0)
        if min_volume > 0 and 'volume' in eligible.columns:
            eligible = eligible[pd.to_numeric(eligible['volume'], errors='coerce').fillna(0) >= min_volume]
        if min_oi > 0 and 'open_interest' in eligible.columns:
            eligible = eligible[pd.to_numeric(eligible['open_interest'], errors='coerce').fillna(0) >= min_oi]
        return eligible

    @staticmethod
    def _b2_rank_high(series):
        values = pd.to_numeric(series, errors='coerce').replace([np.inf, -np.inf], np.nan)
        if values.notna().sum() <= 1:
            return pd.Series(0.5, index=values.index)
        return values.rank(pct=True).fillna(0.5).clip(0.0, 1.0)

    @staticmethod
    def _b2_rank_low(series):
        values = pd.to_numeric(series, errors='coerce').replace([np.inf, -np.inf], np.nan)
        if values.notna().sum() <= 1:
            return pd.Series(0.5, index=values.index)
        return (1.0 - values.rank(pct=True) + 1.0 / values.notna().sum()).fillna(0.5).clip(0.0, 1.0)

    def _b2_weighted_average(self, frame, column):
        if frame is None or frame.empty or column not in frame.columns:
            return np.nan
        values = pd.to_numeric(frame[column], errors='coerce').replace([np.inf, -np.inf], np.nan)
        if values.notna().sum() == 0:
            return np.nan
        volume_source = frame['volume'] if 'volume' in frame.columns else pd.Series(0.0, index=frame.index)
        oi_source = frame['open_interest'] if 'open_interest' in frame.columns else pd.Series(0.0, index=frame.index)
        volume = pd.to_numeric(volume_source, errors='coerce').fillna(0.0).clip(lower=0.0)
        oi = pd.to_numeric(oi_source, errors='coerce').fillna(0.0).clip(lower=0.0)
        weights = np.log1p(volume) + 0.5 * np.log1p(oi)
        weights = weights.where(values.notna(), 0.0).clip(lower=0.0)
        weight_sum = float(weights.sum())
        if weight_sum <= 0:
            return float(values.mean())
        return float((values.fillna(0.0) * weights).sum() / weight_sum)

    def _b3_contract_iv_vov(self, option_code, lookback=20):
        hist = self._contract_iv_history.get(str(option_code))
        if not hist:
            return np.nan
        ivs = pd.to_numeric(pd.Series(hist.get('ivs', [])), errors='coerce').replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        if len(ivs) < 3:
            return np.nan
        diffs = ivs.diff().dropna().tail(max(2, int(lookback or 20)))
        if len(diffs) < 2:
            return np.nan
        return float(diffs.std(ddof=0))

    def _b3_term_structure_features(self, prod_df, expiry):
        if prod_df is None or prod_df.empty:
            return {}
        required = {'expiry_date', 'dte', 'moneyness', 'implied_vol'}
        if not required.issubset(set(prod_df.columns)):
            return {}
        atm = prod_df.copy()
        atm['implied_vol'] = pd.to_numeric(atm['implied_vol'], errors='coerce')
        atm['moneyness'] = pd.to_numeric(atm['moneyness'], errors='coerce')
        atm['dte'] = pd.to_numeric(atm['dte'], errors='coerce')
        atm = atm[
            atm['expiry_date'].notna()
            & atm['implied_vol'].gt(0)
            & atm['dte'].gt(0)
            & atm['moneyness'].between(0.95, 1.05)
        ]
        if atm.empty:
            return {}
        curve = (
            atm.groupby('expiry_date', as_index=False)
            .agg(atm_iv=('implied_vol', 'mean'), dte=('dte', 'median'))
            .sort_values(['dte', 'expiry_date'], kind='mergesort')
            .reset_index(drop=True)
        )
        if curve.empty:
            return {}
        exp_str = str(expiry)
        matches = curve.index[curve['expiry_date'].astype(str) == exp_str].tolist()
        if matches:
            idx = int(matches[0])
        else:
            exp_rows = prod_df[prod_df['expiry_date'].astype(str) == exp_str]
            exp_dte = pd.to_numeric(exp_rows.get('dte', pd.Series(dtype=float)), errors='coerce')
            if exp_dte.notna().sum() == 0:
                return {}
            idx = int((curve['dte'] - float(exp_dte.median())).abs().idxmin())
        cur = curve.iloc[idx]
        near = curve.iloc[idx - 1] if idx > 0 else None
        far = curve.iloc[idx + 1] if idx + 1 < len(curve) else None
        cur_iv = float(cur['atm_iv']) if pd.notna(cur['atm_iv']) else np.nan
        near_iv = float(near['atm_iv']) if near is not None and pd.notna(near['atm_iv']) else np.nan
        far_iv = float(far['atm_iv']) if far is not None and pd.notna(far['atm_iv']) else np.nan
        term_pressure = 0.0
        if np.isfinite(near_iv) and np.isfinite(cur_iv):
            term_pressure += max(cur_iv - near_iv, 0.0)
        if np.isfinite(far_iv) and np.isfinite(cur_iv):
            term_pressure += max(far_iv - cur_iv, 0.0)
        return {
            'b3_near_atm_iv': near_iv,
            'b3_next_atm_iv': cur_iv,
            'b3_far_atm_iv': far_iv,
            'b3_term_structure_pressure': term_pressure if np.isfinite(term_pressure) else np.nan,
        }

    def _b3_add_candidate_fields(self, candidates, product, option_type, term_features=None):
        if candidates is None or candidates.empty:
            return candidates
        c = candidates.copy()
        term_features = term_features or {}
        eps = 1e-12

        entry_iv_trend = pd.to_numeric(
            c.get('entry_iv_trend', pd.Series(np.nan, index=c.index)),
            errors='coerce',
        )
        if entry_iv_trend.notna().sum() == 0:
            iv_state = self._current_iv_state.get(product, {}) or {}
            entry_iv_trend = pd.Series(iv_state.get('iv_trend', np.nan), index=c.index, dtype=float)

        change_1d = pd.to_numeric(
            c.get('contract_iv_change_1d', pd.Series(np.nan, index=c.index)),
            errors='coerce',
        )
        change_5d = pd.to_numeric(
            c.get('contract_iv_change_5d', pd.Series(np.nan, index=c.index)),
            errors='coerce',
        )
        term_pressure = float(term_features.get('b3_term_structure_pressure', np.nan))
        term_pressure_series = pd.Series(term_pressure, index=c.index, dtype=float)
        c['b3_forward_variance_pressure'] = (
            entry_iv_trend.clip(lower=0.0).fillna(0.0)
            + change_5d.clip(lower=0.0).fillna(change_1d.clip(lower=0.0))
            + term_pressure_series.clip(lower=0.0).fillna(0.0)
        ).replace([np.inf, -np.inf], np.nan)

        short_lookback = int(self.config.get('s1_b3_vov_lookback_short', 5) or 5)
        long_lookback = int(self.config.get('s1_b3_vov_lookback_long', 20) or 20)
        codes = c['option_code'] if 'option_code' in c.columns else pd.Series('', index=c.index)
        vov_short = pd.Series(
            [self._b3_contract_iv_vov(code, short_lookback) for code in codes],
            index=c.index,
            dtype=float,
        )
        vov_long = pd.Series(
            [self._b3_contract_iv_vov(code, long_lookback) for code in codes],
            index=c.index,
            dtype=float,
        )
        fallback_vov = change_1d.abs().fillna(0.0) + 0.5 * change_5d.abs().fillna(0.0)
        c['b3_vol_of_vol_proxy'] = vov_short.fillna(fallback_vov).replace([np.inf, -np.inf], np.nan)
        c['b3_vov_trend'] = (vov_short / vov_long.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)

        iv5 = pd.to_numeric(c.get('premium_to_iv5_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
        iv10 = pd.to_numeric(c.get('premium_to_iv10_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
        stress = pd.to_numeric(c.get('premium_to_stress_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
        c['b3_iv_shock_coverage'] = (0.5 * iv5 + 0.5 * iv10).replace([np.inf, -np.inf], np.nan)
        c['b3_joint_stress_coverage'] = stress.replace([np.inf, -np.inf], np.nan)

        iv5_loss = pd.to_numeric(c.get('iv_shock_loss_5_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
        iv10_loss = pd.to_numeric(c.get('iv_shock_loss_10_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
        net_premium = pd.to_numeric(c.get('net_premium_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
        vomma_cash = (iv10_loss - 2.0 * iv5_loss).clip(lower=0.0)
        c['b3_vomma_cash'] = vomma_cash.replace([np.inf, -np.inf], np.nan)
        c['b3_vomma_loss_ratio'] = (vomma_cash / net_premium.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)

        skew_change = pd.to_numeric(
            c.get('contract_skew_change_for_vega', pd.Series(np.nan, index=c.index)),
            errors='coerce',
        )
        skew_fallback = change_5d - entry_iv_trend
        c['contract_skew_change_for_vega'] = skew_change.fillna(skew_fallback)
        c['b3_skew_steepening'] = c['contract_skew_change_for_vega'].clip(lower=0.0).replace(
            [np.inf, -np.inf],
            np.nan,
        )

        for key, value in term_features.items():
            c[key] = value
        return c

    def _b3_product_side_budget_overlay(self, side_df, b2_product_budget_map,
                                        total_budget_pct, date_str, nav):
        if side_df is None or side_df.empty:
            return None
        weights = {
            'b2_side_score': float(self.config.get('s1_b3_weight_b2', 0.60) or 0.0),
            'b3_forward_variance_score': float(self.config.get('s1_b3_weight_forward_variance', 0.0) or 0.0),
            'b3_vol_of_vol_score': float(self.config.get('s1_b3_weight_vol_of_vol', 0.0) or 0.0),
            'b3_iv_shock_score': float(self.config.get('s1_b3_weight_iv_shock', 0.0) or 0.0),
            'b3_joint_stress_score': float(self.config.get('s1_b3_weight_joint_stress', 0.0) or 0.0),
            'b3_vomma_score': float(self.config.get('s1_b3_weight_vomma', 0.0) or 0.0),
            'b3_skew_stability_score': float(self.config.get('s1_b3_weight_skew_stability', 0.0) or 0.0),
        }
        weight_sum = sum(max(0.0, v) for v in weights.values())
        if weight_sum <= 0:
            return None

        b3 = side_df.copy()
        b3['b3_forward_variance_score'] = 100.0 * self._b2_rank_low(b3['b3_forward_variance_pressure'])
        b3['b3_vol_of_vol_score'] = 100.0 * self._b2_rank_low(b3['b3_vol_of_vol_proxy'])
        b3['b3_iv_shock_score'] = 100.0 * self._b2_rank_high(b3['b3_iv_shock_coverage'])
        b3['b3_joint_stress_score'] = 100.0 * self._b2_rank_high(b3['b3_joint_stress_coverage'])
        b3['b3_vomma_score'] = 100.0 * self._b2_rank_low(b3['b3_vomma_loss_ratio'])
        b3['b3_skew_stability_score'] = 100.0 * self._b2_rank_low(b3['b3_skew_steepening'])

        score = pd.Series(0.0, index=b3.index, dtype=float)
        for column, weight in weights.items():
            weight = max(0.0, float(weight or 0.0))
            if weight <= 0:
                continue
            score += weight * pd.to_numeric(b3[column], errors='coerce').fillna(50.0)
        b3['b3_clean_vega_score'] = (score / weight_sum).clip(0.0, 100.0)

        floor_weight = max(0.0, float(self.config.get('s1_b3_floor_weight', 0.50) or 0.0))
        power = max(0.01, float(self.config.get('s1_b3_power', 1.50) or 1.50))
        clip_low = float(self.config.get('s1_b3_score_clip_low', 5.0) or 5.0)
        clip_high = float(self.config.get('s1_b3_score_clip_high', 95.0) or 95.0)
        if clip_high < clip_low:
            clip_low, clip_high = clip_high, clip_low
        tilt_strength = float(np.clip(float(self.config.get('s1_b3_tilt_strength', 0.0) or 0.0), 0.0, 1.0))

        b3['b3_clipped_score'] = b3['b3_clean_vega_score'].clip(clip_low, clip_high)
        b3['b3_raw_weight'] = floor_weight + (b3['b3_clipped_score'].clip(lower=0.0) / 100.0) ** power
        weight_total = float(b3['b3_raw_weight'].sum())
        if weight_total <= 0:
            b3['b3_raw_weight'] = 1.0
            weight_total = float(len(b3))

        base_side_budget = []
        for row in b3.itertuples(index=False):
            product_budget = float(b2_product_budget_map.get(row.product, 0.0) or 0.0)
            base_side_budget.append(product_budget / 2.0)
        b3['b3_side_equal_budget_pct'] = base_side_budget
        base_total_budget = float(pd.to_numeric(b3['b3_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
        if base_total_budget <= 0:
            base_total_budget = float(total_budget_pct or 0.0)
        b3['b3_side_quality_budget_pct'] = base_total_budget * b3['b3_raw_weight'] / weight_total
        b3['b3_side_final_budget_pct'] = (
            (1.0 - tilt_strength) * b3['b3_side_equal_budget_pct']
            + tilt_strength * b3['b3_side_quality_budget_pct']
        )

        product_budget_map = (
            b3.groupby('product', sort=False)['b3_side_final_budget_pct'].sum().to_dict()
        )
        side_meta_map = defaultdict(dict)
        for row in b3.to_dict('records'):
            product = row['product']
            ot = row['option_type']
            product_budget = float(product_budget_map.get(product, 0.0) or 0.0)
            final_side_budget = float(row.get('b3_side_final_budget_pct', 0.0) or 0.0)
            side_budget_mult = (
                final_side_budget / (product_budget / 2.0)
                if product_budget > 0 else np.nan
            )
            meta = dict(row)
            meta.update({
                'b3_product_side_score': row.get('b3_clean_vega_score', np.nan),
                'b3_side_budget_mult': side_budget_mult,
                'b3_clean_vega_tilt_strength': tilt_strength,
            })
            side_meta_map[product][ot] = meta

        if self.config.get('s1_b3_product_side_budget_diagnostics_enabled', True):
            for row in b3.to_dict('records'):
                product = row['product']
                ot = row['option_type']
                meta = side_meta_map.get(product, {}).get(ot, {})
                self.diagnostics_records.append({
                    'date': date_str,
                    'scope': 's1_b3_product_side_budget',
                    'name': f"{product}_{ot}",
                    'product': product,
                    'option_type': ot,
                    'nav': nav,
                    'product_budget_pct': product_budget_map.get(product, np.nan),
                    'b3_clean_vega_score': meta.get('b3_clean_vega_score', np.nan),
                    'b3_forward_variance_score': meta.get('b3_forward_variance_score', np.nan),
                    'b3_vol_of_vol_score': meta.get('b3_vol_of_vol_score', np.nan),
                    'b3_iv_shock_score': meta.get('b3_iv_shock_score', np.nan),
                    'b3_joint_stress_score': meta.get('b3_joint_stress_score', np.nan),
                    'b3_vomma_score': meta.get('b3_vomma_score', np.nan),
                    'b3_skew_stability_score': meta.get('b3_skew_stability_score', np.nan),
                    'b3_side_equal_budget_pct': meta.get('b3_side_equal_budget_pct', np.nan),
                    'b3_side_quality_budget_pct': meta.get('b3_side_quality_budget_pct', np.nan),
                    'b3_side_final_budget_pct': meta.get('b3_side_final_budget_pct', np.nan),
                    'b3_side_budget_mult': meta.get('b3_side_budget_mult', np.nan),
                    'tilt_strength': tilt_strength,
                })

        return {
            'product_budget_map': product_budget_map,
            'side_meta_map': side_meta_map,
        }

    def _b4_product_side_budget_overlay(self, side_df, base_product_budget_map,
                                        total_budget_pct, date_str, nav):
        if side_df is None or side_df.empty:
            return None

        weights = {
            'b4_premium_to_stress_score': float(
                self.config.get('s1_b4_product_weight_premium_to_stress', 0.35) or 0.0
            ),
            'b4_premium_to_iv10_score': float(
                self.config.get('s1_b4_product_weight_premium_to_iv10', 0.30) or 0.0
            ),
            'b4_premium_yield_margin_score': float(
                self.config.get('s1_b4_product_weight_premium_yield_margin', 0.20) or 0.0
            ),
            'b4_gamma_rent_score': float(
                self.config.get('s1_b4_product_weight_gamma_rent', 0.15) or 0.0
            ),
        }
        weight_sum = sum(max(0.0, v) for v in weights.values())
        if weight_sum <= 0:
            return None

        b4 = side_df.copy()
        b4['b4_premium_to_iv10_score'] = 100.0 * self._b2_rank_high(b4['premium_to_iv10_loss'])
        b4['b4_premium_to_stress_score'] = 100.0 * self._b2_rank_high(b4['premium_to_stress_loss'])
        b4['b4_premium_yield_margin_score'] = 100.0 * self._b2_rank_high(b4['premium_yield_margin'])
        b4['b4_gamma_rent_score'] = 100.0 * self._b2_rank_low(b4['gamma_rent_penalty'])
        b4['b4_vomma_score'] = 100.0 * self._b2_rank_low(b4['b3_vomma_loss_ratio'])
        b4['b4_breakeven_cushion_score'] = 100.0 * (
            0.5 * self._b2_rank_high(b4['breakeven_cushion_iv'])
            + 0.5 * self._b2_rank_high(b4['breakeven_cushion_rv'])
        )
        b4['b4_vol_of_vol_score'] = 100.0 * self._b2_rank_low(b4['b3_vol_of_vol_proxy'])

        score = pd.Series(0.0, index=b4.index, dtype=float)
        for column, weight in weights.items():
            weight = max(0.0, float(weight or 0.0))
            if weight <= 0:
                continue
            score += weight * pd.to_numeric(b4[column], errors='coerce').fillna(50.0)
        b4['b4_product_side_score'] = (score / weight_sum).clip(0.0, 100.0)

        floor_weight = max(0.0, float(self.config.get('s1_b4_floor_weight', 0.50) or 0.0))
        power = max(0.01, float(self.config.get('s1_b4_power', 1.25) or 1.25))
        clip_low = float(self.config.get('s1_b4_score_clip_low', 5.0) or 5.0)
        clip_high = float(self.config.get('s1_b4_score_clip_high', 95.0) or 95.0)
        if clip_high < clip_low:
            clip_low, clip_high = clip_high, clip_low
        tilt_strength = float(np.clip(
            float(self.config.get('s1_b4_product_tilt_strength', 0.35) or 0.0),
            0.0,
            1.0,
        ))

        b4['b4_clipped_score'] = b4['b4_product_side_score'].clip(clip_low, clip_high)
        b4['b4_raw_weight'] = floor_weight + (b4['b4_clipped_score'].clip(lower=0.0) / 100.0) ** power
        weight_total = float(b4['b4_raw_weight'].sum())
        if weight_total <= 0:
            b4['b4_raw_weight'] = 1.0
            weight_total = float(len(b4))

        base_side_budget = []
        for row in b4.itertuples(index=False):
            product_budget = float(base_product_budget_map.get(row.product, 0.0) or 0.0)
            base_side_budget.append(product_budget / 2.0)
        b4['b4_side_equal_budget_pct'] = base_side_budget
        base_total_budget = float(pd.to_numeric(b4['b4_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
        if base_total_budget <= 0:
            base_total_budget = float(total_budget_pct or 0.0)
        b4['b4_side_quality_budget_pct'] = base_total_budget * b4['b4_raw_weight'] / weight_total
        b4['b4_side_final_budget_pct'] = (
            (1.0 - tilt_strength) * b4['b4_side_equal_budget_pct']
            + tilt_strength * b4['b4_side_quality_budget_pct']
        )
        if self.config.get('s1_b4_vov_penalty_enabled', False):
            very_low = float(self.config.get('s1_b4_side_vov_penalty_rank_very_low', 15.0) or 15.0)
            low = float(self.config.get('s1_b4_side_vov_penalty_rank_low', 30.0) or 30.0)
            very_low_mult = float(self.config.get('s1_b4_side_vov_penalty_mult_very_low', 0.70) or 0.70)
            low_mult = float(self.config.get('s1_b4_side_vov_penalty_mult_low', 0.85) or 0.85)
            vov_rank = pd.to_numeric(b4['b4_vol_of_vol_score'], errors='coerce')
            b4['b4_side_vov_penalty_mult'] = np.where(
                vov_rank < very_low,
                very_low_mult,
                np.where(vov_rank < low, low_mult, 1.0),
            )
            b4['b4_side_final_budget_pct'] = (
                b4['b4_side_final_budget_pct']
                * pd.to_numeric(b4['b4_side_vov_penalty_mult'], errors='coerce').fillna(1.0)
            )
        else:
            b4['b4_side_vov_penalty_mult'] = 1.0

        product_budget_map = (
            b4.groupby('product', sort=False)['b4_side_final_budget_pct'].sum().to_dict()
        )
        side_meta_map = defaultdict(dict)
        for row in b4.to_dict('records'):
            product = row['product']
            ot = row['option_type']
            product_budget = float(product_budget_map.get(product, 0.0) or 0.0)
            final_side_budget = float(row.get('b4_side_final_budget_pct', 0.0) or 0.0)
            side_budget_mult = (
                final_side_budget / (product_budget / 2.0)
                if product_budget > 0 else np.nan
            )
            meta = dict(row)
            meta.update({
                'b4_product_side_score': row.get('b4_product_side_score', np.nan),
                'b4_side_budget_mult': side_budget_mult,
                'b4_product_tilt_strength': tilt_strength,
                'b4_side_vov_penalty_mult': row.get('b4_side_vov_penalty_mult', np.nan),
            })
            side_meta_map[product][ot] = meta

        if self.config.get('s1_b4_product_side_budget_diagnostics_enabled', True):
            for row in b4.to_dict('records'):
                product = row['product']
                ot = row['option_type']
                meta = side_meta_map.get(product, {}).get(ot, {})
                self.diagnostics_records.append({
                    'date': date_str,
                    'scope': 's1_b4_product_side_budget',
                    'name': f"{product}_{ot}",
                    'product': product,
                    'option_type': ot,
                    'nav': nav,
                    'product_budget_pct': product_budget_map.get(product, np.nan),
                    'b4_product_side_score': meta.get('b4_product_side_score', np.nan),
                    'b4_premium_to_iv10_score': meta.get('b4_premium_to_iv10_score', np.nan),
                    'b4_premium_to_stress_score': meta.get('b4_premium_to_stress_score', np.nan),
                    'b4_premium_yield_margin_score': meta.get('b4_premium_yield_margin_score', np.nan),
                    'b4_gamma_rent_score': meta.get('b4_gamma_rent_score', np.nan),
                    'b4_vomma_score': meta.get('b4_vomma_score', np.nan),
                    'b4_breakeven_cushion_score': meta.get('b4_breakeven_cushion_score', np.nan),
                    'b4_vol_of_vol_score': meta.get('b4_vol_of_vol_score', np.nan),
                    'b4_side_equal_budget_pct': meta.get('b4_side_equal_budget_pct', np.nan),
                    'b4_side_quality_budget_pct': meta.get('b4_side_quality_budget_pct', np.nan),
                    'b4_side_final_budget_pct': meta.get('b4_side_final_budget_pct', np.nan),
                    'b4_side_budget_mult': meta.get('b4_side_budget_mult', np.nan),
                    'b4_side_vov_penalty_mult': meta.get('b4_side_vov_penalty_mult', np.nan),
                    'tilt_strength': tilt_strength,
                })

        return {
            'product_budget_map': product_budget_map,
            'side_meta_map': side_meta_map,
        }

    def _b6_score_from_weight_map(self, frame, weight_map):
        weight_sum = sum(max(0.0, float(v or 0.0)) for v in weight_map.values())
        missing = float(self.config.get('s1_b6_missing_factor_score', 50.0) or 50.0)
        if weight_sum <= 0:
            return pd.Series(missing, index=frame.index, dtype=float)
        score = pd.Series(0.0, index=frame.index, dtype=float)
        for column, weight in weight_map.items():
            weight = max(0.0, float(weight or 0.0))
            if weight <= 0:
                continue
            values = pd.to_numeric(
                frame[column] if column in frame.columns else pd.Series(np.nan, index=frame.index),
                errors='coerce',
            ).fillna(missing)
            score += weight * values
        return (score / weight_sum).clip(0.0, 100.0)

    def _b6_product_budget_overlay(self, side_df, candidate_products, total_budget_pct,
                                   date_str, nav):
        if side_df is None or side_df.empty or not candidate_products:
            return None
        b6 = side_df.copy()

        def col(name):
            return b6[name] if name in b6.columns else pd.Series(np.nan, index=b6.index, dtype=float)

        b6['b6_product_theta_per_vega_score'] = 100.0 * self._b2_rank_high(col('b5_theta_per_vega'))
        b6['b6_product_premium_to_stress_score'] = 100.0 * self._b2_rank_high(col('premium_to_stress_loss'))
        b6['b6_product_theta_per_gamma_score'] = 100.0 * self._b2_rank_high(col('b5_theta_per_gamma'))
        b6['b6_product_tail_beta_score'] = 100.0 * self._b2_rank_low(col('b5_range_expansion_proxy_20d'))
        b6['b6_product_gamma_per_premium_score'] = 100.0 * self._b2_rank_low(col('gamma_rent_penalty'))
        side_score = self._b6_score_from_weight_map(
            b6,
            {
                'b6_product_theta_per_vega_score': self.config.get('s1_b6_product_weight_theta_per_vega', 0.45),
                'b6_product_premium_to_stress_score': self.config.get('s1_b6_product_weight_premium_to_stress', 0.20),
                'b6_product_theta_per_gamma_score': self.config.get('s1_b6_product_weight_theta_per_gamma', 0.15),
                'b6_product_tail_beta_score': self.config.get('s1_b6_product_weight_tail_beta', 0.10),
                'b6_product_gamma_per_premium_score': self.config.get('s1_b6_product_weight_gamma_per_premium', 0.10),
            },
        )
        b6['b6_side_for_product_score'] = side_score

        product_rows = []
        for product in candidate_products:
            group = b6[b6['product'] == product]
            if group.empty:
                product_rows.append({'product': product, 'b6_product_score': np.nan})
                continue
            count_series = (
                group['candidate_count']
                if 'candidate_count' in group.columns
                else pd.Series(1.0, index=group.index, dtype=float)
            )
            weights = pd.to_numeric(count_series, errors='coerce').fillna(1.0).clip(lower=1.0)
            score = float((group['b6_side_for_product_score'] * weights).sum() / weights.sum())
            product_candidate_count = (
                pd.to_numeric(group['candidate_count'], errors='coerce').fillna(0.0).sum()
                if 'candidate_count' in group.columns
                else float(len(group))
            )
            product_rows.append({
                'product': product,
                'b6_product_score': score,
                'b6_product_candidate_count': int(product_candidate_count),
                'b6_product_theta_per_vega': self._b2_weighted_average(group, 'b5_theta_per_vega'),
                'b6_product_premium_to_stress': self._b2_weighted_average(group, 'premium_to_stress_loss'),
                'b6_product_theta_per_gamma': self._b2_weighted_average(group, 'b5_theta_per_gamma'),
                'b6_product_range_expansion': self._b2_weighted_average(group, 'b5_range_expansion_proxy_20d'),
                'b6_product_cooldown_penalty': self._b2_weighted_average(group, 'b5_cooldown_penalty_score'),
            })
        prod = pd.DataFrame(product_rows)
        missing = float(self.config.get('s1_b6_missing_factor_score', 50.0) or 50.0)
        prod['b6_product_score'] = pd.to_numeric(prod['b6_product_score'], errors='coerce').fillna(missing)

        n_products = len(candidate_products)
        total_budget_pct = float(total_budget_pct or 0.0)
        equal_budget_pct = total_budget_pct / max(n_products, 1)
        floor_weight = max(0.0, float(self.config.get('s1_b6_product_floor_weight', 0.80) or 0.80))
        power = max(0.01, float(self.config.get('s1_b6_product_power', 1.25) or 1.25))
        clip_low = float(self.config.get('s1_b6_score_clip_low', 5.0) or 5.0)
        clip_high = float(self.config.get('s1_b6_score_clip_high', 95.0) or 95.0)
        if clip_high < clip_low:
            clip_low, clip_high = clip_high, clip_low
        tilt_strength = float(np.clip(
            float(self.config.get('s1_b6_product_tilt_strength', 0.15) or 0.0),
            0.0,
            1.0,
        ))
        raw = floor_weight + (prod['b6_product_score'].clip(clip_low, clip_high) / 100.0) ** power
        quality = total_budget_pct * raw / float(raw.sum() if raw.sum() > 0 else len(raw))
        final = (1.0 - tilt_strength) * equal_budget_pct + tilt_strength * quality
        mult = final / equal_budget_pct if equal_budget_pct > 0 else np.nan
        min_mult = float(self.config.get('s1_b6_product_multiplier_min', 0.80) or 0.80)
        max_mult = float(self.config.get('s1_b6_product_multiplier_max', 1.20) or 1.20)
        final = np.clip(mult, min_mult, max_mult) * equal_budget_pct
        prod['b6_product_equal_budget_pct'] = equal_budget_pct
        prod['b6_product_quality_budget_pct'] = quality
        prod['b6_product_final_budget_pct'] = final
        prod['b6_product_budget_mult'] = final / equal_budget_pct if equal_budget_pct > 0 else np.nan
        prod['b6_product_tilt_strength'] = tilt_strength

        budget_map = dict(zip(prod['product'], prod['b6_product_final_budget_pct']))
        meta_map = {row['product']: dict(row) for row in prod.to_dict('records')}
        if self.config.get('s1_b6_product_budget_diagnostics_enabled', True):
            for row in prod.to_dict('records'):
                self.diagnostics_records.append({
                    'date': date_str,
                    'scope': 's1_b6_product_budget',
                    'name': row.get('product'),
                    'product': row.get('product'),
                    'nav': nav,
                    **row,
                })
        return {'product_budget_map': budget_map, 'product_meta_map': meta_map}

    def _b6_product_side_budget_overlay(self, side_df, base_product_budget_map,
                                        total_budget_pct, date_str, nav):
        if side_df is None or side_df.empty:
            return None
        b6 = side_df.copy()

        def col(name):
            return b6[name] if name in b6.columns else pd.Series(np.nan, index=b6.index, dtype=float)

        b6['b6_side_theta_per_vega_score'] = 100.0 * self._b2_rank_high(col('b5_theta_per_vega'))
        b6['b6_side_premium_to_stress_score'] = 100.0 * self._b2_rank_high(col('premium_to_stress_loss'))
        b6['b6_side_theta_per_gamma_score'] = 100.0 * self._b2_rank_high(col('b5_theta_per_gamma'))
        b6['b6_side_premium_to_margin_score'] = 100.0 * self._b2_rank_high(col('premium_yield_margin'))
        b6['b6_side_vega_per_premium_score'] = 100.0 * self._b2_rank_high(col('b5_premium_per_vega'))
        b6['b6_side_gamma_per_premium_score'] = 100.0 * self._b2_rank_low(col('gamma_rent_penalty'))
        b6['b6_product_side_score'] = self._b6_score_from_weight_map(
            b6,
            {
                'b6_side_theta_per_vega_score': self.config.get('s1_b6_side_weight_theta_per_vega', 0.35),
                'b6_side_premium_to_stress_score': self.config.get('s1_b6_side_weight_premium_to_stress', 0.25),
                'b6_side_theta_per_gamma_score': self.config.get('s1_b6_side_weight_theta_per_gamma', 0.15),
                'b6_side_premium_to_margin_score': self.config.get('s1_b6_side_weight_premium_to_margin', 0.10),
                'b6_side_vega_per_premium_score': self.config.get('s1_b6_side_weight_vega_per_premium', 0.10),
                'b6_side_gamma_per_premium_score': self.config.get('s1_b6_side_weight_gamma_per_premium', 0.05),
            },
        )

        floor_weight = max(0.0, float(self.config.get('s1_b6_side_floor_weight', 0.70) or 0.70))
        power = max(0.01, float(self.config.get('s1_b6_side_power', 1.25) or 1.25))
        clip_low = float(self.config.get('s1_b6_score_clip_low', 5.0) or 5.0)
        clip_high = float(self.config.get('s1_b6_score_clip_high', 95.0) or 95.0)
        if clip_high < clip_low:
            clip_low, clip_high = clip_high, clip_low
        tilt_strength = float(np.clip(
            float(self.config.get('s1_b6_side_tilt_strength', 0.25) or 0.0),
            0.0,
            1.0,
        ))
        b6['b6_raw_weight'] = floor_weight + (b6['b6_product_side_score'].clip(clip_low, clip_high) / 100.0) ** power
        weight_total = float(b6['b6_raw_weight'].sum())
        if weight_total <= 0:
            b6['b6_raw_weight'] = 1.0
            weight_total = float(len(b6))

        base_side_budget = []
        for row in b6.itertuples(index=False):
            product_budget = float(base_product_budget_map.get(row.product, 0.0) or 0.0)
            base_side_budget.append(product_budget / 2.0)
        b6['b6_side_equal_budget_pct'] = base_side_budget
        base_total_budget = float(pd.to_numeric(b6['b6_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
        if base_total_budget <= 0:
            base_total_budget = float(total_budget_pct or 0.0)
        b6['b6_side_quality_budget_pct'] = base_total_budget * b6['b6_raw_weight'] / weight_total
        b6['b6_side_final_budget_pct'] = (
            (1.0 - tilt_strength) * b6['b6_side_equal_budget_pct']
            + tilt_strength * b6['b6_side_quality_budget_pct']
        )

        b6['b6_side_direction_penalty_mult'] = 1.0
        if self.config.get('s1_b6_side_direction_penalty_enabled', True):
            trend_z = pd.to_numeric(col('b5_trend_z_20d'), errors='coerce').fillna(0.0)
            threshold = float(self.config.get('s1_b6_side_adverse_trend_z', 0.80) or 0.80)
            trend_mult = float(self.config.get('s1_b6_side_adverse_trend_mult', 0.85) or 0.85)
            adverse_trend = (
                ((b6['option_type'].astype(str).str.upper() == 'C') & (trend_z > threshold))
                | ((b6['option_type'].astype(str).str.upper() == 'P') & (trend_z < -threshold))
            )
            b6.loc[adverse_trend, 'b6_side_direction_penalty_mult'] *= trend_mult

            up_score = 100.0 * self._b2_rank_high(col('b5_breakout_distance_up_60d'))
            down_score = 100.0 * self._b2_rank_high(col('b5_breakout_distance_down_60d'))
            cushion_score = np.where(
                b6['option_type'].astype(str).str.upper() == 'C',
                up_score,
                down_score,
            )
            low_rank = float(self.config.get('s1_b6_side_breakout_rank_low', 30.0) or 30.0)
            breakout_mult = float(self.config.get('s1_b6_side_breakout_mult_low', 0.90) or 0.90)
            b6.loc[pd.Series(cushion_score, index=b6.index) < low_rank, 'b6_side_direction_penalty_mult'] *= breakout_mult

            skew_score = 100.0 * self._b2_rank_low(col('b3_skew_steepening'))
            skew_rank = float(self.config.get('s1_b6_side_skew_rank_low', 30.0) or 30.0)
            skew_mult = float(self.config.get('s1_b6_side_skew_mult_low', 0.90) or 0.90)
            b6.loc[skew_score < skew_rank, 'b6_side_direction_penalty_mult'] *= skew_mult

            cooldown = pd.to_numeric(col('b5_cooldown_penalty_score'), errors='coerce').fillna(0.0).clip(0.0, 1.0)
            floor = float(self.config.get('s1_b6_side_cooldown_mult_floor', 0.70) or 0.70)
            cooldown_mult = 1.0 - cooldown * (1.0 - floor)
            b6['b6_side_direction_penalty_mult'] *= cooldown_mult

        b6['b6_side_final_budget_pct'] *= pd.to_numeric(
            b6['b6_side_direction_penalty_mult'],
            errors='coerce',
        ).fillna(1.0)
        min_mult = float(self.config.get('s1_b6_side_multiplier_min', 0.70) or 0.70)
        max_mult = float(self.config.get('s1_b6_side_multiplier_max', 1.30) or 1.30)

        product_budget_map = {}
        side_meta_map = defaultdict(dict)
        for product, group in b6.groupby('product', sort=False):
            base_product_budget = float(base_product_budget_map.get(product, 0.0) or 0.0)
            if base_product_budget > 0:
                side_equal = base_product_budget / 2.0
                clipped = group.copy()
                mult = pd.to_numeric(clipped['b6_side_final_budget_pct'], errors='coerce') / side_equal
                clipped['b6_side_final_budget_pct'] = mult.clip(min_mult, max_mult) * side_equal
                b6.loc[clipped.index, 'b6_side_final_budget_pct'] = clipped['b6_side_final_budget_pct']
            product_budget_map[product] = float(
                pd.to_numeric(b6.loc[group.index, 'b6_side_final_budget_pct'], errors='coerce').sum()
            )

        for row in b6.to_dict('records'):
            product = row['product']
            ot = row['option_type']
            product_budget = float(product_budget_map.get(product, 0.0) or 0.0)
            equal_side = float(base_product_budget_map.get(product, 0.0) or 0.0) / 2.0
            final_side = float(row.get('b6_side_final_budget_pct', 0.0) or 0.0)
            side_budget_mult = final_side / equal_side if equal_side > 0 else np.nan
            meta = dict(row)
            meta.update({
                'b6_product_side_score': row.get('b6_product_side_score', np.nan),
                'b6_side_budget_mult': side_budget_mult,
                'b6_side_tilt_strength': tilt_strength,
            })
            side_meta_map[product][ot] = meta

        if self.config.get('s1_b6_product_side_budget_diagnostics_enabled', True):
            for row in b6.to_dict('records'):
                meta = side_meta_map.get(row['product'], {}).get(row['option_type'], {})
                self.diagnostics_records.append({
                    'date': date_str,
                    'scope': 's1_b6_product_side_budget',
                    'name': f"{row['product']}_{row['option_type']}",
                    'product': row['product'],
                    'option_type': row['option_type'],
                    'nav': nav,
                    'product_budget_pct': product_budget_map.get(row['product'], np.nan),
                    **meta,
                })

        return {
            'product_budget_map': product_budget_map,
            'side_meta_map': side_meta_map,
        }

    def _b2_collect_product_quality_inputs(self, product_frames, candidate_products, date_str=None):
        max_candidates = int(
            self.config.get(
                's1_b2_score_top_contracts_per_side',
                self.config.get('s1_baseline_max_contracts_per_side', 5),
            ) or 0
        )
        if max_candidates <= 0:
            max_candidates = int(self.config.get('s1_baseline_max_contracts_per_side', 5) or 5)
        min_abs_delta, delta_cap = self._s1_delta_bounds(None)
        side_rows = []
        candidate_cache = {}

        for product in candidate_products:
            prod_df = product_frames.get(product)
            if prod_df is None or prod_df.empty:
                continue
            open_expiries = self._select_s1_open_expiries(prod_df, product)
            if not open_expiries:
                continue
            for exp in open_expiries:
                ef = prod_df[prod_df['expiry_date'] == exp]
                if ef.empty:
                    continue
                term_features = self._b3_term_structure_features(prod_df, exp)
                mult = float(ef['multiplier'].iloc[0] or 0.0)
                if mult <= 0:
                    continue
                exchange = ef['exchange'].iloc[0]
                underlying_code = ef['underlying_code'].iloc[0] if 'underlying_code' in ef.columns else ''
                mr = self.ci.get_margin_ratio(
                    exchange=exchange,
                    product=product,
                    underlying_code=underlying_code,
                    config=self.config,
                )
                for option_type in ('P', 'C'):
                    candidates = self._select_s1_sell_candidates(
                        ef, product, option_type, mult, mr, exchange,
                        min_abs_delta, delta_cap, max_candidates,
                    )
                    if candidates is None or candidates.empty:
                        continue
                    candidates = candidates.copy()
                    candidates = self._b3_add_candidate_fields(
                        candidates,
                        product,
                        option_type,
                        term_features=term_features,
                    )
                    if self._s1_b6_enabled():
                        candidates = self._add_s1_b5_shadow_fields(
                            candidates,
                            date_str or '',
                            product,
                            exp,
                            option_type,
                            force=True,
                        )
                        candidates = self._apply_s1_b6_candidate_ranking(candidates)
                        if candidates is None or candidates.empty:
                            continue
                    candidate_cache[(product, exp, option_type)] = candidates
                    row = {
                        'product': product,
                        'expiry': exp,
                        'option_type': option_type,
                        'candidate_count': len(candidates),
                    }
                    for column in (
                        'variance_carry',
                        'breakeven_cushion_iv',
                        'breakeven_cushion_rv',
                        'premium_to_iv5_loss',
                        'premium_to_iv10_loss',
                        'premium_to_stress_loss',
                        'premium_yield_margin',
                        'theta_vega_efficiency',
                        'gamma_rent_penalty',
                        'friction_ratio',
                        'liquidity_score',
                        'b3_forward_variance_pressure',
                        'b3_vol_of_vol_proxy',
                        'b3_vov_trend',
                        'b3_iv_shock_coverage',
                        'b3_joint_stress_coverage',
                        'b3_vomma_cash',
                        'b3_vomma_loss_ratio',
                        'b3_skew_steepening',
                        'b5_theta_per_vega',
                        'b5_theta_per_gamma',
                        'b5_premium_per_vega',
                        'b5_premium_to_tail_move_loss',
                        'b5_trend_z_20d',
                        'b5_breakout_distance_up_60d',
                        'b5_breakout_distance_down_60d',
                        'b5_range_expansion_proxy_20d',
                        'b5_iv_reversion_score',
                        'b5_cooldown_penalty_score',
                        'b5_cooldown_release_score',
                        'b5_product_stop_count_20d',
                        'b5_product_side_stop_count_20d',
                        'b5_tick_value_ratio',
                    ):
                        row[column] = self._b2_weighted_average(candidates, column)
                    for column in (
                        'b3_near_atm_iv',
                        'b3_next_atm_iv',
                        'b3_far_atm_iv',
                        'b3_term_structure_pressure',
                    ):
                        row[column] = term_features.get(column, np.nan)
                    side_rows.append(row)
        return side_rows, candidate_cache

    def _b2_product_budget_map(self, product_frames, candidate_products, total_budget_pct,
                               date_str, nav):
        if not candidate_products:
            return {}, {}, {}
        total_budget_pct = float(total_budget_pct or 0.0)
        if total_budget_pct <= 0:
            return {}, {}, {}

        side_rows, candidate_cache = self._b2_collect_product_quality_inputs(
            product_frames,
            candidate_products,
            date_str=date_str,
        )
        side_df = pd.DataFrame(side_rows)
        product_scores = {
            product: {
                'product': product,
                'product_score': np.nan,
                'put_score': np.nan,
                'call_score': np.nan,
                'side_count': 0,
                'candidate_count': 0,
            }
            for product in candidate_products
        }
        missing_score = float(self.config.get('s1_b2_missing_score', 20.0) or 20.0)
        missing_side_penalty = float(self.config.get('s1_b2_missing_side_penalty', 0.70) or 0.70)

        if not side_df.empty:
            breakeven_score = (
                0.5 * self._b2_rank_high(side_df['breakeven_cushion_iv'])
                + 0.5 * self._b2_rank_high(side_df['breakeven_cushion_rv'])
            )
            iv_shock_score = (
                0.5 * self._b2_rank_high(side_df['premium_to_iv5_loss'])
                + 0.5 * self._b2_rank_high(side_df['premium_to_iv10_loss'])
            )
            side_df['b2_side_score'] = 100.0 * (
                0.20 * self._b2_rank_high(side_df['variance_carry'])
                + 0.15 * breakeven_score
                + 0.20 * iv_shock_score
                + 0.15 * self._b2_rank_high(side_df['premium_to_stress_loss'])
                + 0.10 * self._b2_rank_high(side_df['theta_vega_efficiency'])
                + 0.10 * self._b2_rank_low(side_df['gamma_rent_penalty'])
                + 0.10 * self._b2_rank_low(side_df['friction_ratio'])
            ).clip(0.0, 100.0)

            for product, group in side_df.groupby('product', sort=False):
                put = group[group['option_type'] == 'P']
                call = group[group['option_type'] == 'C']
                put_score = float(put['b2_side_score'].mean()) if not put.empty else np.nan
                call_score = float(call['b2_side_score'].mean()) if not call.empty else np.nan
                valid_scores = [s for s in (put_score, call_score) if np.isfinite(s)]
                if len(valid_scores) >= 2:
                    product_score = float(np.mean(valid_scores))
                elif len(valid_scores) == 1:
                    product_score = float(valid_scores[0] * missing_side_penalty)
                else:
                    product_score = missing_score
                product_scores[product] = {
                    'product': product,
                    'product_score': product_score,
                    'put_score': put_score,
                    'call_score': call_score,
                    'side_count': len(valid_scores),
                    'candidate_count': int(group['candidate_count'].sum()),
                }

        n_products = len(candidate_products)
        equal_budget_pct = total_budget_pct / max(n_products, 1)
        floor_weight = max(0.0, float(self.config.get('s1_b2_floor_weight', 0.50) or 0.0))
        power = max(0.01, float(self.config.get('s1_b2_power', 1.50) or 1.50))
        clip_low = float(self.config.get('s1_b2_score_clip_low', 5.0) or 5.0)
        clip_high = float(self.config.get('s1_b2_score_clip_high', 95.0) or 95.0)
        if clip_high < clip_low:
            clip_low, clip_high = clip_high, clip_low
        tilt_strength = float(self.config.get('s1_b2_tilt_strength', 0.0) or 0.0)
        tilt_strength = float(np.clip(tilt_strength, 0.0, 1.0))

        rows = []
        for product in candidate_products:
            item = product_scores.get(product, {})
            score = float(item.get('product_score', np.nan))
            if not np.isfinite(score):
                score = missing_score
            clipped_score = float(np.clip(score, clip_low, clip_high))
            raw_weight = floor_weight + (max(clipped_score, 0.0) / 100.0) ** power
            rows.append({
                **item,
                'product': product,
                'product_score': score,
                'clipped_score': clipped_score,
                'raw_weight': raw_weight,
            })

        weight_sum = sum(float(r['raw_weight'] or 0.0) for r in rows)
        if weight_sum <= 0:
            weight_sum = float(n_products)
            for r in rows:
                r['raw_weight'] = 1.0

        budget_map = {}
        meta_map = {}
        for r in rows:
            quality_budget_pct = total_budget_pct * float(r['raw_weight']) / weight_sum
            final_budget_pct = (
                (1.0 - tilt_strength) * equal_budget_pct
                + tilt_strength * quality_budget_pct
            )
            budget_mult = final_budget_pct / equal_budget_pct if equal_budget_pct > 0 else np.nan
            product = r['product']
            budget_map[product] = final_budget_pct
            meta = {
                **r,
                'equal_budget_pct': equal_budget_pct,
                'quality_budget_pct': quality_budget_pct,
                'final_budget_pct': final_budget_pct,
                'budget_mult': budget_mult,
                'tilt_strength': tilt_strength,
                'floor_weight': floor_weight,
                'power': power,
            }
            meta_map[product] = meta

        if self.config.get('s1_b2_product_budget_diagnostics_enabled', True):
            for product in candidate_products:
                meta = meta_map.get(product, {})
                self.diagnostics_records.append({
                    'date': date_str,
                    'scope': 's1_b2_product_budget',
                    'name': product,
                    'nav': nav,
                    'n_products': n_products,
                    'product_score': meta.get('product_score', np.nan),
                    'put_score': meta.get('put_score', np.nan),
                    'call_score': meta.get('call_score', np.nan),
                    'side_count': meta.get('side_count', 0),
                    'candidate_count': meta.get('candidate_count', 0),
                    'equal_budget_pct': meta.get('equal_budget_pct', np.nan),
                    'quality_budget_pct': meta.get('quality_budget_pct', np.nan),
                    'final_budget_pct': meta.get('final_budget_pct', np.nan),
                    'budget_mult': meta.get('budget_mult', np.nan),
                    'tilt_strength': tilt_strength,
                })

        if self.config.get('s1_b6_product_tilt_enabled', False) and not side_df.empty:
            b6_product_overlay = self._b6_product_budget_overlay(
                side_df,
                candidate_products,
                total_budget_pct,
                date_str,
                nav,
            )
            if b6_product_overlay:
                b6_product_budget_map = b6_product_overlay.get('product_budget_map') or {}
                b6_product_meta_map = b6_product_overlay.get('product_meta_map') or {}
                for product in candidate_products:
                    if product in b6_product_budget_map:
                        budget_map[product] = b6_product_budget_map[product]
                    meta = meta_map.setdefault(product, {'product': product})
                    meta.update({
                        'b6_product_final_budget_pct': budget_map.get(product, np.nan),
                        **(b6_product_meta_map.get(product, {}) or {}),
                    })

        if self.config.get('s1_b3_clean_vega_tilt_enabled', False) and not side_df.empty:
            b3_overlay = self._b3_product_side_budget_overlay(
                side_df,
                budget_map,
                total_budget_pct,
                date_str,
                nav,
            )
            if b3_overlay:
                b3_product_budget_map = b3_overlay.get('product_budget_map') or {}
                b3_side_meta_map = b3_overlay.get('side_meta_map') or {}
                for product in candidate_products:
                    if product in b3_product_budget_map:
                        budget_map[product] = b3_product_budget_map[product]
                    meta = meta_map.setdefault(product, {'product': product})
                    meta['b3_product_final_budget_pct'] = budget_map.get(product, np.nan)
                    meta['b3_side_meta'] = b3_side_meta_map.get(product, {})

        if self.config.get('s1_b4_product_side_tilt_enabled', False) and not side_df.empty:
            b4_overlay = self._b4_product_side_budget_overlay(
                side_df,
                budget_map,
                total_budget_pct,
                date_str,
                nav,
            )
            if b4_overlay:
                b4_product_budget_map = b4_overlay.get('product_budget_map') or {}
                b4_side_meta_map = b4_overlay.get('side_meta_map') or {}
                for product in candidate_products:
                    if product in b4_product_budget_map:
                        budget_map[product] = b4_product_budget_map[product]
                    meta = meta_map.setdefault(product, {'product': product})
                    meta['b4_product_final_budget_pct'] = budget_map.get(product, np.nan)
                    meta['b4_side_meta'] = b4_side_meta_map.get(product, {})

        if self.config.get('s1_b6_side_tilt_enabled', False) and not side_df.empty:
            b6_side_overlay = self._b6_product_side_budget_overlay(
                side_df,
                budget_map,
                total_budget_pct,
                date_str,
                nav,
            )
            if b6_side_overlay:
                b6_product_budget_map = b6_side_overlay.get('product_budget_map') or {}
                b6_side_meta_map = b6_side_overlay.get('side_meta_map') or {}
                for product in candidate_products:
                    if product in b6_product_budget_map:
                        budget_map[product] = b6_product_budget_map[product]
                    meta = meta_map.setdefault(product, {'product': product})
                    meta['b6_product_final_budget_pct'] = budget_map.get(product, np.nan)
                    meta['b6_side_meta'] = b6_side_meta_map.get(product, {})

        return budget_map, meta_map, candidate_cache

    @staticmethod
    def _s1_side_meta_from_product_budget(product_meta, option_type):
        if not product_meta:
            return {}
        meta = {
            'b2_product_score': product_meta.get('product_score', np.nan),
            'b2_product_equal_budget_pct': product_meta.get('equal_budget_pct', np.nan),
            'b2_product_quality_budget_pct': product_meta.get('quality_budget_pct', np.nan),
            'b2_product_final_budget_pct': product_meta.get('final_budget_pct', np.nan),
            'b2_product_budget_mult': product_meta.get('budget_mult', np.nan),
        }
        b3_side_meta = (product_meta.get('b3_side_meta') or {}).get(option_type, {})
        if b3_side_meta:
            meta.update({
                'budget_mult': b3_side_meta.get('b3_side_budget_mult', 1.0),
                'b3_product_side_score': b3_side_meta.get('b3_product_side_score', np.nan),
                'b3_side_equal_budget_pct': b3_side_meta.get('b3_side_equal_budget_pct', np.nan),
                'b3_side_quality_budget_pct': b3_side_meta.get('b3_side_quality_budget_pct', np.nan),
                'b3_side_final_budget_pct': b3_side_meta.get('b3_side_final_budget_pct', np.nan),
                'b3_side_budget_mult': b3_side_meta.get('b3_side_budget_mult', np.nan),
                'b3_clean_vega_tilt_strength': b3_side_meta.get('b3_clean_vega_tilt_strength', np.nan),
                'b3_clean_vega_score': b3_side_meta.get('b3_clean_vega_score', np.nan),
                'b3_forward_variance_score': b3_side_meta.get('b3_forward_variance_score', np.nan),
                'b3_vol_of_vol_score': b3_side_meta.get('b3_vol_of_vol_score', np.nan),
                'b3_iv_shock_score': b3_side_meta.get('b3_iv_shock_score', np.nan),
                'b3_joint_stress_score': b3_side_meta.get('b3_joint_stress_score', np.nan),
                'b3_vomma_score': b3_side_meta.get('b3_vomma_score', np.nan),
                'b3_skew_stability_score': b3_side_meta.get('b3_skew_stability_score', np.nan),
            })
        b4_side_meta = (product_meta.get('b4_side_meta') or {}).get(option_type, {})
        if b4_side_meta:
            meta.update({
                'budget_mult': b4_side_meta.get('b4_side_budget_mult', 1.0),
                'b4_product_side_score': b4_side_meta.get('b4_product_side_score', np.nan),
                'b4_side_equal_budget_pct': b4_side_meta.get('b4_side_equal_budget_pct', np.nan),
                'b4_side_quality_budget_pct': b4_side_meta.get('b4_side_quality_budget_pct', np.nan),
                'b4_side_final_budget_pct': b4_side_meta.get('b4_side_final_budget_pct', np.nan),
                'b4_side_budget_mult': b4_side_meta.get('b4_side_budget_mult', np.nan),
                'b4_product_tilt_strength': b4_side_meta.get('b4_product_tilt_strength', np.nan),
                'b4_side_vov_penalty_mult': b4_side_meta.get('b4_side_vov_penalty_mult', np.nan),
                'b4_premium_to_iv10_score': b4_side_meta.get('b4_premium_to_iv10_score', np.nan),
                'b4_premium_to_stress_score': b4_side_meta.get('b4_premium_to_stress_score', np.nan),
                'b4_premium_yield_margin_score': b4_side_meta.get('b4_premium_yield_margin_score', np.nan),
                'b4_gamma_rent_score': b4_side_meta.get('b4_gamma_rent_score', np.nan),
                'b4_vomma_score': b4_side_meta.get('b4_vomma_score', np.nan),
                'b4_breakeven_cushion_score': b4_side_meta.get('b4_breakeven_cushion_score', np.nan),
                'b4_vol_of_vol_score': b4_side_meta.get('b4_vol_of_vol_score', np.nan),
            })
        if 'b6_product_score' in product_meta:
            meta.update({
                'b6_product_score': product_meta.get('b6_product_score', np.nan),
                'b6_product_equal_budget_pct': product_meta.get('b6_product_equal_budget_pct', np.nan),
                'b6_product_quality_budget_pct': product_meta.get('b6_product_quality_budget_pct', np.nan),
                'b6_product_final_budget_pct': product_meta.get('b6_product_final_budget_pct', np.nan),
                'b6_product_budget_mult': product_meta.get('b6_product_budget_mult', np.nan),
                'b6_product_tilt_strength': product_meta.get('b6_product_tilt_strength', np.nan),
            })
        b6_side_meta = (product_meta.get('b6_side_meta') or {}).get(option_type, {})
        if b6_side_meta:
            meta.update({
                'budget_mult': b6_side_meta.get('b6_side_budget_mult', 1.0),
                'b6_product_side_score': b6_side_meta.get('b6_product_side_score', np.nan),
                'b6_side_equal_budget_pct': b6_side_meta.get('b6_side_equal_budget_pct', np.nan),
                'b6_side_quality_budget_pct': b6_side_meta.get('b6_side_quality_budget_pct', np.nan),
                'b6_side_final_budget_pct': b6_side_meta.get('b6_side_final_budget_pct', np.nan),
                'b6_side_budget_mult': b6_side_meta.get('b6_side_budget_mult', np.nan),
                'b6_side_tilt_strength': b6_side_meta.get('b6_side_tilt_strength', np.nan),
                'b6_side_direction_penalty_mult': b6_side_meta.get(
                    'b6_side_direction_penalty_mult', np.nan
                ),
            })
        return meta

    def _build_s1_side_items_for_expiry(
        self,
        *,
        prod_df,
        ef,
        product,
        exp,
        mult,
        mr,
        exchange,
        date_str,
        nav,
        iv_state,
        b2_meta,
        b2_candidate_cache,
    ):
        if self.config.get('s1_side_selection_enabled', False):
            return self._select_s1_side_items(
                ef, product, mult, mr, exchange, date_str,
                iv_state=iv_state,
            )

        side_items = []
        record_universe_today = (
            self._s1_candidate_universe_enabled()
            and self._s1_candidate_signal_in_scope(date_str)
        )
        term_features = (
            self._b3_term_structure_features(prod_df, exp)
            if record_universe_today
            else {}
        )
        for ot in ['P', 'C']:
            preselected = b2_candidate_cache.get((product, exp, ot))
            side_meta = self._s1_side_meta_from_product_budget(b2_meta, ot)
            if record_universe_today:
                min_abs_delta, delta_cap = self._s1_delta_bounds(None)
                record_candidates = self._select_s1_candidate_universe_frame(
                    ef, product, ot, mult, mr, exchange,
                    min_abs_delta, delta_cap,
                    iv_state=iv_state,
                    side_meta=side_meta,
                    term_features=term_features,
                )
                self._append_s1_candidate_universe(
                    date_str,
                    nav,
                    product,
                    exp,
                    ot,
                    record_candidates,
                    side_meta=side_meta,
                )
                if preselected is None:
                    preselected = record_candidates
            side_items.append((ot, preselected, side_meta))
        return side_items

    def _prepare_open_product_scan(self, daily_df, product_pool, baseline_mode, scan_top_n, date_str):
        filtered_daily = daily_df[daily_df['product'].isin(product_pool)]
        if filtered_daily.empty:
            self._bump_s1_funnel('skip_empty_filtered_daily')
            return None

        product_frames = {
            product: frame
            for product, frame in filtered_daily.groupby('product', sort=False)
            if not frame.empty
        }
        if not product_frames:
            self._bump_s1_funnel('skip_no_product_frames')
            return None
        self._bump_s1_funnel('loaded_products', len(product_frames))
        self._update_product_first_trade_dates(list(product_frames), date_str)

        if baseline_mode:
            sorted_products = self._baseline_product_order(product_frames)
        else:
            prod_volume = filtered_daily.groupby('product', as_index=False)['volume'].sum()
            prod_volume = prod_volume.sort_values(
                ['volume', 'product'],
                ascending=[False, True],
                kind='mergesort',
            )
            sorted_products = self._diversify_product_order(prod_volume['product'].tolist())
            sorted_products = self._prioritize_products_by_regime(sorted_products)
        candidate_products = sorted_products if scan_top_n <= 0 else sorted_products[:scan_top_n]
        return product_frames, candidate_products

    def _resolve_s1_product_budget_maps(
        self,
        *,
        baseline_mode,
        candidate_products,
        product_frames,
        margin_cap,
        s1_cap,
        date_str,
        nav,
    ):
        baseline_product_margin_per = (
            float(s1_cap or margin_cap or 0.0) / max(len(candidate_products), 1)
            if baseline_mode and self.config.get('s1_baseline_equal_weight_products', True)
            else None
        )
        b2_product_budget_map = {}
        b2_product_budget_meta = {}
        b2_candidate_cache = {}
        if (
            baseline_mode
            and (
                self.config.get('s1_b2_product_tilt_enabled', False)
                or self.config.get('s1_b4_product_side_tilt_enabled', False)
                or self.config.get('s1_b6_product_tilt_enabled', False)
                or self.config.get('s1_b6_side_tilt_enabled', False)
            )
            and self.config.get('s1_baseline_equal_weight_products', True)
        ):
            (
                b2_product_budget_map,
                b2_product_budget_meta,
                b2_candidate_cache,
            ) = self._b2_product_budget_map(
                product_frames,
                candidate_products,
                float(s1_cap or margin_cap or 0.0),
                date_str,
                nav,
            )
        return (
            baseline_product_margin_per,
            b2_product_budget_map,
            b2_product_budget_meta,
            b2_candidate_cache,
        )

    def _open_position_state(self):
        return {
            'product_expiries': {(p.product, p.expiry) for p in self.positions},
            's1_sell_sides': {
                (p.product, p.opt_type)
                for p in self.positions
                if p.strat == 'S1' and p.role == 'sell'
            },
            's3_sell_sides': {
                (p.product, p.opt_type)
                for p in self.positions
                if p.strat == 'S3' and p.role == 'sell'
            },
            's4_products': {
                p.product
                for p in self.positions
                if p.strat == 'S4'
            },
        }

    def _s1_product_open_context(
        self,
        *,
        product,
        baseline_mode,
        product_iv_pcts,
        iv_open_thr,
        baseline_product_margin_per,
        b2_product_budget_map,
        b2_product_budget_meta,
        margin_per,
    ):
        cfg = self.config
        iv_pct = product_iv_pcts.get(product, np.nan)
        iv_state = self._current_iv_state.get(product, {})
        if not baseline_mode and should_pause_open(iv_pct, iv_open_thr):
            self._bump_s1_funnel('skip_iv_pause')
            return None

        low_iv_threshold = cfg.get('iv_low_skip_threshold', 20)
        low_iv_allowed = should_allow_open_low_iv_product(
            product,
            iv_pct,
            iv_state,
            enabled=cfg.get('low_iv_exception_enabled', False),
            low_iv_allowed_products=cfg.get('low_iv_allowed_products', []),
            iv_low_skip_threshold=low_iv_threshold,
            min_iv_rv_spread=cfg.get('low_iv_min_iv_rv_spread', 0.02),
            min_iv_rv_ratio=cfg.get('low_iv_min_iv_rv_ratio', 1.10),
            max_rv_trend=cfg.get('low_iv_max_rv_trend', None),
        )
        if (
            cfg.get('vol_regime_sizing_enabled', False) and
            cfg.get('vol_regime_allow_low_iv_rich', True) and
            self._current_vol_regimes.get(product) == 'low_stable_vol'
        ):
            low_iv_allowed = True
        if self._is_structural_low_iv_product(product, iv_state):
            low_iv_allowed = True
        if not baseline_mode and pd.notna(iv_pct) and iv_pct < low_iv_threshold and not low_iv_allowed:
            self._bump_s1_funnel('skip_low_iv')
            return None

        iv_scale = 1.0 if baseline_mode else get_iv_scale(iv_pct, cfg.get('iv_threshold', 75))
        regime_mult = 1.0 if baseline_mode else self._product_margin_per_multiplier(product)
        if regime_mult <= 0:
            self._bump_s1_funnel('skip_regime_budget_zero')
            return None
        if baseline_product_margin_per is not None:
            product_margin_per = (
                b2_product_budget_map.get(product, baseline_product_margin_per)
                if b2_product_budget_map
                else baseline_product_margin_per
            )
        else:
            product_margin_per = margin_per * regime_mult
        return {
            'iv_pct': iv_pct,
            'iv_state': iv_state,
            'iv_scale': iv_scale,
            'product_margin_per': product_margin_per,
            'b2_meta': b2_product_budget_meta.get(product, {}),
        }

    def _queue_new_opens(self, daily_df, date_str, product_pool, product_iv_pcts):
        """收盘后生成下一交易日待开仓指令。"""
        cfg = self.config
        nav = max(self._current_nav(), 1.0)
        self._start_s1_candidate_funnel(date_str, nav, product_pool)
        if (
            self._s1_candidate_universe_enabled()
            and cfg.get('s1_candidate_universe_skip_new_opens_after_signal_end', False)
            and self._s1_candidate_after_signal_window(date_str)
        ):
            self._bump_s1_funnel('skip_candidate_universe_after_signal_window')
            self._finish_s1_candidate_funnel(date_str, nav)
            return
        baseline_mode = bool(cfg.get('s1_baseline_mode', False))
        vega_warn = cfg.get('greeks_vega_warn', 0.008)
        current_vega_pct = abs(sum(p.cash_vega() for p in self.positions) / nav)
        if not baseline_mode and current_vega_pct > vega_warn:
            logger.debug("  %s Vega预警 %.3f%% > %.1f%%, 暂停新开仓",
                         date_str, current_vega_pct * 100, vega_warn * 100)
            self._bump_s1_funnel('skip_vega_warn')
            self._finish_s1_candidate_funnel(date_str, nav)
            return

        total_m = self._get_open_sell_margin_total()
        s1_m = self._get_open_sell_margin_total('S1')
        s3_m = self._get_open_sell_margin_total('S3')
        budget = self._get_effective_open_budget()
        margin_cap = budget['margin_cap']
        s1_cap = budget['s1_margin_cap']
        s3_cap = budget['s3_margin_cap']
        margin_per = budget['margin_per']
        iv_open_thr = cfg.get('iv_open_threshold', 80)
        scan_top_n = int(cfg.get('daily_scan_top_n', 0) or 0)

        product_scan = self._prepare_open_product_scan(
            daily_df,
            product_pool,
            baseline_mode,
            scan_top_n,
            date_str,
        )
        if product_scan is None:
            self._finish_s1_candidate_funnel(date_str, nav)
            return
        product_frames, candidate_products = product_scan
        (
            baseline_product_margin_per,
            b2_product_budget_map,
            b2_product_budget_meta,
            b2_candidate_cache,
        ) = self._resolve_s1_product_budget_maps(
            baseline_mode=baseline_mode,
            candidate_products=candidate_products,
            product_frames=product_frames,
            margin_cap=margin_cap,
            s1_cap=s1_cap,
            date_str=date_str,
            nav=nav,
        )
        self._bump_s1_funnel('candidate_products', len(candidate_products))
        open_state = self._open_position_state()

        for product in candidate_products:
            prod_df = product_frames.get(product)
            if prod_df.empty:
                self._bump_s1_funnel('skip_empty_product_frame')
                continue
            if not self._passes_product_entry_filters(product, date_str):
                self._bump_s1_funnel('skip_product_observation')
                continue
            self._bump_s1_funnel('product_entry_pass')

            open_expiries = self._select_s1_open_expiries(prod_df, product)
            if not open_expiries:
                self._bump_s1_funnel('skip_no_open_expiry')
                continue
            self._bump_s1_funnel('open_expiry_candidates', len(open_expiries))

            product_context = self._s1_product_open_context(
                product=product,
                baseline_mode=baseline_mode,
                product_iv_pcts=product_iv_pcts,
                iv_open_thr=iv_open_thr,
                baseline_product_margin_per=baseline_product_margin_per,
                b2_product_budget_map=b2_product_budget_map,
                b2_product_budget_meta=b2_product_budget_meta,
                margin_per=margin_per,
            )
            if product_context is None:
                continue
            iv_state = product_context['iv_state']
            iv_scale = product_context['iv_scale']
            product_margin_per = product_context['product_margin_per']
            b2_meta = product_context['b2_meta']

            for exp in open_expiries:
                expiry_has_position = (product, exp) in open_state['product_expiries']

                ef = prod_df[prod_df['expiry_date'] == exp]
                if ef.empty:
                    self._bump_s1_funnel('skip_empty_expiry_frame')
                    continue
                self._bump_s1_funnel('product_expiry_frames')

                if total_m / nav >= margin_cap:
                    self._bump_s1_funnel('break_total_margin_cap')
                    break

                spot = ef['spot_close'].iloc[0]
                mult = ef['multiplier'].iloc[0]
                exchange = ef['exchange'].iloc[0]
                underlying_code = ef['underlying_code'].iloc[0] if 'underlying_code' in ef.columns else ''
                mr = self.ci.get_margin_ratio(
                    exchange=exchange,
                    product=product,
                    underlying_code=underlying_code,
                    config=cfg,
                )

                s1_can_add_expiry = bool(cfg.get(
                    's1_allow_add_same_expiry',
                    cfg.get('s1_allow_add_same_side', False),
                ))
                if (
                    cfg.get('enable_s1', True) and
                    s1_m / nav < s1_cap and
                    (not expiry_has_position or s1_can_add_expiry)
                ):
                    if not baseline_mode and not self._passes_s1_falling_framework_entry(product, iv_state):
                        self._bump_s1_funnel('skip_s1_falling_framework')
                        continue
                    self._bump_s1_funnel('s1_framework_pass')
                    s1_side_items = self._build_s1_side_items_for_expiry(
                        prod_df=prod_df,
                        ef=ef,
                        product=product,
                        exp=exp,
                        mult=mult,
                        mr=mr,
                        exchange=exchange,
                        date_str=date_str,
                        nav=nav,
                        iv_state=iv_state,
                        b2_meta=b2_meta,
                        b2_candidate_cache=b2_candidate_cache,
                    )
                    self._bump_s1_funnel('s1_selected_side_items', len(s1_side_items))
                    if (
                        self._s1_candidate_universe_enabled()
                        and cfg.get('s1_candidate_universe_shadow_only_enabled', False)
                    ):
                        self._bump_s1_funnel('skip_candidate_universe_shadow_only')
                        continue
                    for ot, preselected_candidates, side_meta in s1_side_items:
                        if (
                            not cfg.get('s1_allow_add_same_side', False) and
                            (product, ot) in open_state['s1_sell_sides']
                        ):
                            self._bump_s1_funnel('skip_same_side_position')
                            continue
                        if self._is_reentry_blocked('S1', product, ot, date_str):
                            self._bump_s1_funnel('skip_reentry_blocked')
                            continue
                        s1_plan = self._get_reentry_plan('S1', product, ot, date_str)
                        pending_before = len(self._pending_opens)
                        self._try_open_s1(ef, product, ot, mult, mr, exchange, exp,
                                          nav, product_margin_per, iv_scale, date_str,
                                          reentry_plan=s1_plan,
                                          iv_state=iv_state,
                                          margin_cap=margin_cap, strategy_cap=s1_cap,
                                          preselected_candidates=preselected_candidates,
                                          side_meta=side_meta)
                        self._bump_s1_funnel(
                            'pending_items_added',
                            max(0, len(self._pending_opens) - pending_before),
                        )

                if cfg.get('enable_s3', True) and s3_m / nav < s3_cap and not expiry_has_position:
                    for ot in ['P', 'C']:
                        if (product, ot) in open_state['s3_sell_sides']:
                            continue
                        if self._is_reentry_blocked('S3', product, ot, date_str):
                            continue
                        s3_plan = self._get_reentry_plan('S3', product, ot, date_str)
                        self._try_open_s3(ef, product, ot, spot, mult, mr, exchange, exp,
                                          nav, product_margin_per, iv_scale, date_str,
                                          reentry_plan=s3_plan,
                                          margin_cap=margin_cap, strategy_cap=s3_cap)

                if cfg.get('enable_s4', True) and product not in open_state['s4_products']:
                    self._try_open_s4(ef, product, mult, mr, exchange, exp, nav, date_str)
        self._finish_s1_candidate_funnel(date_str, nav)

    def _process_daily_decision(self, daily_df, date_str, product_pool, run_risk_and_tp=True):
        """每日收盘后决策：补做收盘风控、到期结算、生成待开仓。"""
        cfg = self.config
        fee = cfg.get('fee', 3)

        spot_by_product = {}
        spot_by_underlying = {}
        valid_spots = daily_df[daily_df['spot_close'].notna() & (daily_df['spot_close'] > 0)]
        if not valid_spots.empty:
            product_spots = valid_spots[
                valid_spots['product'].notna() & (valid_spots['product'] != '')
            ][['product', 'spot_close']].drop_duplicates('product', keep='last')
            underlying_spots = valid_spots[
                valid_spots['underlying_code'].notna() & (valid_spots['underlying_code'] != '')
            ][['underlying_code', 'spot_close']].drop_duplicates('underlying_code', keep='last')
            spot_by_product = dict(zip(product_spots['product'], product_spots['spot_close']))
            spot_by_underlying = dict(zip(underlying_spots['underlying_code'], underlying_spots['spot_close']))

        for pos in self.positions:
            if pos.underlying_code and pos.underlying_code in spot_by_underlying:
                pos.cur_spot = spot_by_underlying[pos.underlying_code]
            elif pos.product in spot_by_product:
                pos.cur_spot = spot_by_product[pos.product]

        self._refresh_position_greeks()
        product_iv_pcts = self._calc_product_iv_pcts(daily_df, date_str)

        if run_risk_and_tp:
            self._apply_exit_rules(
                date_str, fee, product_iv_pcts=product_iv_pcts,
                check_greeks=True, check_tp=True, check_expiry=False,
            )

        self._apply_exit_rules(
            date_str, fee, product_iv_pcts=product_iv_pcts,
            check_greeks=False, check_tp=False, check_expiry=True,
        )
        self._queue_new_opens(daily_df, date_str, product_pool, product_iv_pcts)


    # ── 开仓辅助 ─────────────────────────────────────────────────────────────

    def _s1_delta_bounds(self, reentry_plan=None):
        delta_cap = float(self.config.get('s1_sell_delta_cap', 0.10))
        min_abs_delta = float(self.config.get('s1_sell_delta_floor', 0.0))
        if reentry_plan:
            delta_cap = float(self.config.get('s1_reentry_delta_cap', 0.15))
            min_abs_delta = min(
                delta_cap,
                max(
                    min_abs_delta,
                    float(reentry_plan.get('delta_abs', 0.0))
                    + float(self.config.get('s1_reentry_delta_step', 0.02)),
                ),
            )
        if self.config.get('s1_falling_framework_enabled', False):
            delta_cap = min(delta_cap, 0.10)
            min_abs_delta = min(min_abs_delta, delta_cap)
        return min_abs_delta, delta_cap

    def _s1_max_selection_candidates(self):
        if bool(self.config.get('s1_split_across_neighbor_contracts', False)):
            return max(1, int(self.config.get('s1_neighbor_contract_count', 3) or 1))
        return 1

    def _s1_vol_regime_prefix(self, product=None):
        regime = self._current_vol_regimes.get(product, 'normal_vol') if product else 'normal_vol'
        return {
            'falling_vol_carry': 'falling',
            'low_stable_vol': 'low',
            'high_rising_vol': 'high',
            'post_stop_cooldown': 'high',
        }.get(regime, 'normal')

    def _s1_ladder_shape(self, side_meta=None, product=None):
        base_count = self._s1_max_selection_candidates()
        base_gap = float(self.config.get('s1_neighbor_max_delta_gap', 0.0) or 0.0)
        role = str((side_meta or {}).get('trend_role', 'neutral') or 'neutral').lower()
        if role == 'strong':
            count_key = 's1_trend_ladder_strong_contract_count'
            gap_key = 's1_trend_ladder_strong_max_delta_gap'
            role_key = 'strong'
        elif role == 'weak':
            count_key = 's1_trend_ladder_weak_contract_count'
            gap_key = 's1_trend_ladder_weak_max_delta_gap'
            role_key = 'weak'
        else:
            count_key = 's1_trend_ladder_neutral_contract_count'
            gap_key = 's1_trend_ladder_neutral_max_delta_gap'
            role_key = 'neutral'
        count = base_count
        gap = base_gap
        if self.config.get('s1_trend_ladder_enabled', False):
            count = int(self.config.get(count_key, count) or count)
            gap = float(self.config.get(gap_key, gap) or gap)
        if self.config.get('s1_regime_ladder_enabled', False):
            prefix = self._s1_vol_regime_prefix(product)
            regime_count_key = f'vol_regime_{prefix}_s1_ladder_{role_key}_contract_count'
            regime_gap_key = f'vol_regime_{prefix}_s1_ladder_{role_key}_max_delta_gap'
            count = int(self.config.get(regime_count_key, count) or count)
            gap = float(self.config.get(regime_gap_key, gap) or gap)
        return max(1, count), max(0.0, gap)

    def _s1_stress_max_qty(self, product=None):
        base_qty = int(self.config.get('s1_stress_max_qty', 50) or 50)
        if not self.config.get('s1_regime_stress_max_qty_enabled', False):
            return base_qty
        prefix = self._s1_vol_regime_prefix(product)
        key = f'vol_regime_{prefix}_s1_stress_max_qty'
        return max(1, int(self.config.get(key, base_qty) or base_qty))

    def _product_s1_stress_budget_pct(self, product, fallback_pct, iv_state=None):
        fallback_pct = max(0.0, float(fallback_pct or 0.0))
        if not self.config.get('s1_product_regime_stress_budget_enabled', False):
            return fallback_pct
        prefix = self._s1_vol_regime_prefix(product)
        key = f'vol_regime_{prefix}_s1_stress_loss_budget_pct'
        if key not in self.config:
            return fallback_pct
        product_pct = max(0.0, float(self.config.get(key, fallback_pct) or fallback_pct))
        risk_scale = float((self._current_open_budget or {}).get('risk_scale', 1.0) or 1.0)
        product_pct *= max(0.0, risk_scale)
        if (
            self.config.get('low_iv_structural_caution_enabled', False) and
            prefix != 'falling' and
            bool((iv_state or {}).get('is_structural_low_iv', False))
        ):
            product_pct *= max(
                0.0,
                float(self.config.get('low_iv_structural_s1_stress_budget_mult', 1.0) or 0.0),
            )
        return product_pct

    def _product_regime_open_budget(self, product, budget):
        budget = normalize_open_budget(budget or {})
        if not self.config.get('s1_product_regime_budget_overrides_enabled', False):
            return budget
        prefix = self._s1_vol_regime_prefix(product)
        enabled_prefixes = self.config.get(
            's1_product_regime_budget_override_prefixes',
            ['falling'],
        )
        if isinstance(enabled_prefixes, str):
            enabled_prefixes = [enabled_prefixes]
        enabled_prefixes = {str(p).lower() for p in (enabled_prefixes or [])}
        strict_clamp = bool(self.config.get(
            's1_product_regime_budget_clamp_non_release_enabled',
            False,
        ))
        if prefix not in enabled_prefixes and not strict_clamp:
            return budget

        adjusted = dict(budget)
        override_keys = (
            ('product_margin_cap', f'vol_regime_{prefix}_product_margin_cap'),
            ('product_side_margin_cap', f'vol_regime_{prefix}_product_side_margin_cap'),
            ('bucket_margin_cap', f'vol_regime_{prefix}_bucket_margin_cap'),
            ('corr_group_margin_cap', f'vol_regime_{prefix}_corr_group_margin_cap'),
            ('portfolio_bucket_stress_loss_cap', f'vol_regime_{prefix}_bucket_stress_loss_cap'),
            ('product_side_stress_loss_cap', f'vol_regime_{prefix}_product_side_stress_loss_cap'),
            ('corr_group_stress_loss_cap', f'vol_regime_{prefix}_corr_group_stress_loss_cap'),
            ('contract_stress_loss_cap', f'vol_regime_{prefix}_contract_stress_loss_cap'),
        )
        for budget_key, config_key in override_keys:
            if config_key not in self.config:
                continue
            override = float(self.config.get(config_key, 0.0) or 0.0)
            if override <= 0:
                continue
            if prefix in enabled_prefixes:
                adjusted[budget_key] = max(float(adjusted.get(budget_key, 0.0) or 0.0), override)
            else:
                current = float(adjusted.get(budget_key, 0.0) or 0.0)
                adjusted[budget_key] = min(current, override) if current > 0 else override
        return normalize_open_budget(adjusted)

    def _select_s1_sell_candidates(self, ef, product, ot, mult, mr, exchange,
                                   min_abs_delta, delta_cap, max_candidates):
        s1_frame = self._prepare_s1_selection_frame(ef, ot)
        iv_state = self._current_iv_state.get(product, {}) or {}
        if s1_frame is not None and not s1_frame.empty:
            s1_frame = s1_frame.copy()
            s1_frame['entry_atm_iv'] = iv_state.get('atm_iv', np.nan)
            s1_frame['entry_iv_pct'] = iv_state.get('iv_pct', np.nan)
            s1_frame['entry_iv_trend'] = iv_state.get('iv_trend', np.nan)
            s1_frame['entry_rv_trend'] = iv_state.get('rv_trend', np.nan)
            s1_frame['entry_iv_rv_spread'] = iv_state.get('iv_rv_spread', np.nan)
            s1_frame['entry_iv_rv_ratio'] = iv_state.get('iv_rv_ratio', np.nan)
            s1_frame['entry_rv_ref'] = iv_state.get('rv20', np.nan)
            if self.config.get('s1_b4_vov_penalty_enabled', False):
                lookback = int(self.config.get('s1_b3_vov_lookback_short', 5) or 5)
                codes = s1_frame['option_code'] if 'option_code' in s1_frame.columns else pd.Series('', index=s1_frame.index)
                vov = pd.Series(
                    [self._b3_contract_iv_vov(code, lookback) for code in codes],
                    index=s1_frame.index,
                    dtype=float,
                )
                change_1d = pd.to_numeric(
                    s1_frame.get('contract_iv_change_1d', pd.Series(np.nan, index=s1_frame.index)),
                    errors='coerce',
                )
                change_5d = pd.to_numeric(
                    s1_frame.get('contract_iv_change_5d', pd.Series(np.nan, index=s1_frame.index)),
                    errors='coerce',
                )
                fallback = change_1d.abs().fillna(0.0) + 0.5 * change_5d.abs().fillna(0.0)
                s1_frame['b3_vol_of_vol_proxy'] = vov.fillna(fallback).replace([np.inf, -np.inf], np.nan)
        candidate_multiplier = 3
        if self.config.get('s1_forward_vega_filter_enabled', False):
            base_multiplier = int(self.config.get('s1_forward_vega_candidate_multiplier', 8) or 8)
            falling_multiplier = int(
                self.config.get('s1_forward_vega_falling_candidate_multiplier', 0) or 0
            )
            if self._s1_vol_regime_prefix(product) == 'falling' and falling_multiplier > 0:
                base_multiplier = max(base_multiplier, falling_multiplier)
            candidate_multiplier = max(candidate_multiplier, base_multiplier)
        ranking_mode = self.config.get('s1_ranking_mode', 'target_delta')
        ranking_params = (
            self._s1_b6_params()
            if str(ranking_mode or '').lower() in {'b6', 'b6_residual_quality', 'b6_contract', 'b6_role'}
            else self._s1_b4_params()
        )
        return select_s1_sell(
            s1_frame, ot, mult, mr,
            min_volume=int(self.config.get('s1_min_volume', 0)),
            min_oi=int(self.config.get('s1_min_oi', 0)),
            min_abs_delta=min_abs_delta,
            max_abs_delta=delta_cap,
            target_abs_delta=float(self.config.get('s1_target_abs_delta', 0.07)),
            carry_metric=self.config.get('s1_carry_metric', 'premium_margin'),
            fee_per_contract=float(self.config.get('fee', 0.0) or 0.0),
            roundtrip_fee_per_contract=self._option_roundtrip_fee_per_contract(product, ot),
            min_premium_fee_multiple=float(self.config.get('s1_min_premium_fee_multiple', 0.0) or 0.0),
            min_option_price=float(self.config.get('s1_min_option_price', 0.0) or 0.0),
            use_stress_score=bool(self.config.get('s1_use_stress_score', False)),
            stress_spot_move_pct=float(self.config.get('s1_stress_spot_move_pct', 0.03) or 0.03),
            stress_iv_up_points=float(self.config.get('s1_stress_iv_up_points', 5.0) or 5.0),
            stress_premium_loss_multiple=float(
                self.config.get('s1_stress_premium_loss_multiple', 0.0) or 0.0
            ),
            gamma_penalty=float(self.config.get('s1_gamma_penalty', 0.0) or 0.0),
            vega_penalty=float(self.config.get('s1_vega_penalty', 0.0) or 0.0),
            ranking_mode=ranking_mode,
            premium_stress_weight=float(self.config.get('s1_score_premium_stress_weight', 0.55) or 0.0),
            theta_stress_weight=float(self.config.get('s1_score_theta_stress_weight', 0.25) or 0.0),
            premium_margin_weight=float(self.config.get('s1_score_premium_margin_weight', 0.15) or 0.0),
            liquidity_weight=float(self.config.get('s1_score_liquidity_weight', 0.05) or 0.0),
            delta_weight=float(self.config.get('s1_score_delta_weight', 0.0) or 0.0),
            return_candidates=True,
            max_candidates=max_candidates * candidate_multiplier,
            exchange=exchange,
            product=product,
            b4_params=ranking_params,
        )

    def _select_s1_side_items(self, ef, product, mult, mr, exchange, date_str,
                              iv_state=None):
        max_candidates = self._s1_max_selection_candidates()
        trend_enabled = bool(self.config.get('s1_trend_confidence_enabled', False))
        trend_info = (
            self._s1_trend_confidence_info(product, date_str, iv_state)
            if trend_enabled else {}
        )
        current_regime = self._current_vol_regimes.get(product, 'normal_vol')
        side_candidates = {}
        side_frames = {}
        side_meta = {}
        for ot in ['P', 'C']:
            self._bump_s1_funnel('side_checked')
            if self._is_reentry_blocked('S1', product, ot, date_str):
                self._bump_s1_funnel('side_reentry_blocked')
                continue
            reentry_plan = self._get_reentry_plan('S1', product, ot, date_str)
            min_abs_delta, delta_cap = self._s1_delta_bounds(reentry_plan)
            adjustment = {
                'trend_role': 'neutral',
                'score_mult': 1.0,
                'budget_mult': 1.0,
                'delta_cap': None,
            }
            if trend_enabled:
                adjustment = s1_trend_side_adjustment(
                    ot,
                    trend_info.get('trend_state', 'uncertain'),
                    trend_info.get('trend_confidence', 0.0),
                    weak_delta_cap=self.config.get('s1_trend_weak_side_delta_cap', 0.060),
                    weak_score_mult=self.config.get('s1_trend_weak_side_score_mult', 0.60),
                    weak_budget_mult=self.config.get('s1_trend_weak_side_budget_mult', 0.50),
                    strong_score_mult=self.config.get('s1_trend_strong_side_score_mult', 1.00),
                )
                weak_delta_cap = adjustment.get('delta_cap')
                if weak_delta_cap is not None and float(weak_delta_cap or 0.0) > 0:
                    delta_cap = min(delta_cap, float(weak_delta_cap))
                    min_abs_delta = min(min_abs_delta, delta_cap)
            candidates = self._select_s1_sell_candidates(
                ef, product, ot, mult, mr, exchange,
                min_abs_delta, delta_cap, max_candidates,
            )
            filter_meta = {
                **trend_info,
                **adjustment,
                'delta_cap': delta_cap,
            }
            candidates = self._filter_s1_forward_vega_candidates(
                candidates,
                product,
                ot,
                iv_state=iv_state,
                side_meta=filter_meta,
            )
            if candidates is None or candidates.empty:
                self._bump_s1_funnel('side_no_candidates')
                continue
            candidates = candidates.copy()
            if self._s1_b6_enabled():
                exp = ef['expiry_date'].iloc[0] if 'expiry_date' in ef.columns and not ef.empty else ''
                candidates = self._prepare_s1_b6_selection_candidates(
                    candidates,
                    date_str,
                    product,
                    exp,
                    ot,
                    term_features={},
                )
                if candidates is None or candidates.empty:
                    self._bump_s1_funnel('side_no_candidates_after_b6')
                    continue
            self._bump_s1_funnel('side_with_candidates')
            self._bump_s1_funnel('side_candidate_rows', len(candidates))
            score_mult = float(adjustment.get('score_mult', 1.0) or 0.0)
            if trend_enabled and score_mult != 1.0 and 'quality_score' in candidates.columns:
                candidates['quality_score'] = pd.to_numeric(
                    candidates['quality_score'], errors='coerce'
                ) * score_mult
                sort_cols = [
                    col for col in (
                        'quality_score', 'premium_stress', 'theta_stress',
                        'premium_margin', 'volume', 'open_interest', 'option_code',
                    )
                    if col in candidates.columns
                ]
                ascending = [False] * len(sort_cols)
                if sort_cols and sort_cols[-1] == 'option_code':
                    ascending[-1] = True
                candidates = candidates.sort_values(
                    sort_cols,
                    ascending=ascending,
                    kind='mergesort',
                )
            if trend_enabled:
                candidates['trend_state'] = trend_info.get('trend_state', '')
                candidates['trend_score'] = trend_info.get('trend_score', np.nan)
                candidates['trend_confidence'] = trend_info.get('trend_confidence', np.nan)
                candidates['trend_role'] = adjustment.get('trend_role', '')
                candidates['side_score_mult'] = score_mult
                candidates['side_budget_mult'] = float(adjustment.get('budget_mult', 1.0) or 0.0)
            self._append_s1_candidate_universe(
                date_str,
                self._current_nav(),
                product,
                ef['expiry_date'].iloc[0] if 'expiry_date' in ef.columns and not ef.empty else '',
                ot,
                candidates,
                side_meta=filter_meta,
            )
            side_frames[ot] = candidates
            side_candidates[ot] = candidates.iloc[0]
            side_meta[ot] = filter_meta

        momentum = self._recent_product_momentum(
            product,
            date_str,
            self.config.get('s1_side_momentum_lookback', 5),
        )
        if trend_enabled:
            selected_sides = choose_s1_trend_confidence_sides(
                side_candidates,
                trend_state=trend_info.get('trend_state', 'uncertain'),
                current_regime=current_regime,
                conditional_strangle_enabled=bool(self.config.get('s1_conditional_strangle_enabled', False)),
                allowed_strangle_regimes=self.config.get(
                    's1_conditional_strangle_allowed_regimes',
                    ['falling_vol_carry', 'low_stable_vol'],
                ),
                strangle_states=self.config.get('s1_trend_strangle_states', ['range_bound']),
                strangle_min_score_ratio=float(
                    self.config.get('s1_conditional_strangle_min_score_ratio', 0.90) or 0.0
                ),
                strangle_min_adjusted_score=float(
                    self.config.get('s1_conditional_strangle_min_adjusted_score', 0.35) or 0.0
                ),
                allow_weak_side=bool(self.config.get('s1_trend_allow_weak_side', True)),
                weak_side_min_score_ratio=float(
                    self.config.get('s1_trend_weak_side_min_score_ratio', 0.75) or 0.0
                ),
            )
        else:
            selected_sides = choose_s1_option_sides(
                side_candidates,
                enabled=bool(self.config.get('s1_side_selection_enabled', False)),
                conditional_strangle_enabled=bool(self.config.get('s1_conditional_strangle_enabled', False)),
                current_regime=current_regime,
                momentum=momentum,
                momentum_threshold=float(self.config.get('s1_side_momentum_threshold', 0.02) or 0.02),
                momentum_penalty=float(self.config.get('s1_side_momentum_penalty', 0.75) or 0.0),
                allowed_strangle_regimes=self.config.get(
                    's1_conditional_strangle_allowed_regimes',
                    ['falling_vol_carry', 'low_stable_vol'],
                ),
                strangle_max_abs_momentum=float(
                    self.config.get('s1_conditional_strangle_max_abs_momentum', 0.015) or 0.0
                ),
                strangle_min_score_ratio=float(
                    self.config.get('s1_conditional_strangle_min_score_ratio', 0.90) or 0.0
                ),
                strangle_min_adjusted_score=float(
                    self.config.get('s1_conditional_strangle_min_adjusted_score', 0.35) or 0.0
                ),
                strangle_require_momentum=bool(
                    self.config.get('s1_conditional_strangle_require_momentum', True)
                ),
            )
        self._bump_s1_funnel('side_selected', len(selected_sides))
        self._bump_s1_funnel(
            'side_not_selected',
            max(0, len(side_frames) - len([ot for ot in selected_sides if ot in side_frames])),
        )
        return [
            (ot, side_frames.get(ot), side_meta.get(ot, {}))
            for ot in selected_sides
            if ot in side_frames
        ]

    def _try_open_s1(self, ef, product, ot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, date_str, reentry_plan=None,
                     iv_state=None, margin_cap=None, strategy_cap=None,
                     preselected_candidates=None, side_meta=None):
        """S1开仓"""
        baseline_mode = bool(self.config.get('s1_baseline_mode', False))
        min_abs_delta, delta_cap = self._s1_delta_bounds(reentry_plan)
        split_enabled = bool(self.config.get('s1_split_across_neighbor_contracts', False))
        if baseline_mode:
            max_candidates = int(self.config.get('s1_baseline_max_contracts_per_side', 0) or 0)
            max_delta_gap = 0.0
        else:
            max_candidates, max_delta_gap = self._s1_ladder_shape(side_meta, product=product)
        if preselected_candidates is None:
            candidates = self._select_s1_sell_candidates(
                ef, product, ot, mult, mr, exchange,
                min_abs_delta, delta_cap, max_candidates,
            )
            candidates = self._filter_s1_forward_vega_candidates(
                candidates,
                product,
                ot,
                iv_state=iv_state,
                side_meta=side_meta,
            )
        else:
            candidates = preselected_candidates.copy()
        if candidates is None or candidates.empty:
            self._bump_s1_funnel('open_skip_no_candidates')
            return
        candidates = candidates.copy()
        if self._s1_b6_enabled():
            candidates = self._prepare_s1_b6_selection_candidates(
                candidates,
                date_str,
                product,
                exp,
                ot,
                term_features={},
            )
            if candidates is None or candidates.empty:
                self._bump_s1_funnel('open_skip_no_candidates_after_b6')
                return
        self._bump_s1_funnel('open_candidates_considered', len(candidates))
        candidates = self._trim_s1_open_candidates(
            candidates,
            baseline_mode=baseline_mode,
            split_enabled=split_enabled,
            max_candidates=max_candidates,
            max_delta_gap=max_delta_gap,
        )
        if candidates.empty:
            self._bump_s1_funnel('open_skip_ladder_filter')
            return
        self._bump_s1_funnel('open_candidates_after_ladder', len(candidates))
        iv_state = iv_state or {}
        side_meta = side_meta or {}
        effective_margin_cap = self.config.get('margin_cap', 0.50) if margin_cap is None else margin_cap
        effective_strategy_cap = self.config.get('s1_margin_cap', 0.25) if strategy_cap is None else strategy_cap
        base_margin_per = float(self.config.get('margin_per', 0.02) or 0.02)
        regime_scale = margin_per / base_margin_per if base_margin_per > 0 else 1.0
        open_budget = self._current_open_budget or {}
        product_budget = self._product_regime_open_budget(product, open_budget)
        if 's1_stress_loss_budget_pct' in open_budget:
            stress_budget_pct = float(open_budget.get('s1_stress_loss_budget_pct') or 0.0)
        else:
            stress_budget_pct = float(self.config.get('s1_stress_loss_budget_pct', 0.0010) or 0.0) * regime_scale
        stress_budget_pct = self._product_s1_stress_budget_pct(
            product,
            stress_budget_pct,
            iv_state=iv_state,
        )
        side_budget_mult = max(0.0, float(side_meta.get('budget_mult', 1.0) or 0.0))
        remaining_stress_budget = nav * stress_budget_pct * float(iv_scale or 1.0) * side_budget_mult
        remaining_margin_budget = nav * float(margin_per or 0.0) / 2.0 * float(iv_scale or 1.0) * side_budget_mult
        min_qty = int(self.config.get('s1_stress_min_qty', 1) or 1)
        max_qty = self._s1_stress_max_qty(product)
        group_id = f"S1_{product}_{ot}_{exp}_{date_str}"

        def max_allowed_qty(row, single_margin, target_qty, one_loss):
            target_qty = int(target_qty or 0)
            if target_qty <= 0:
                return 0

            def passes(qty):
                total_m = self._get_open_sell_margin_total()
                s1_m = self._get_open_sell_margin_total('S1')
                new_greeks = self._candidate_cash_greeks(row, ot, mult, qty, role='sell')
                total_stress_loss = one_loss * qty if one_loss > 0 else 0.0
                if not port_risk.check_margin_ok(
                    total_m, s1_m, single_margin * qty, nav,
                    effective_margin_cap, effective_strategy_cap,
                ):
                    return False
                return self._passes_portfolio_construction(
                    product, nav, single_margin * qty, date_str=date_str,
                    new_cash_vega=new_greeks['cash_vega'],
                    new_cash_gamma=new_greeks['cash_gamma'],
                    new_stress_loss=total_stress_loss,
                    budget=product_budget,
                    option_type=ot,
                    code=row.get('option_code', ''),
                    new_lots=qty,
                )

            lo, hi = 0, target_qty
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if passes(mid):
                    lo = mid
                else:
                    hi = mid - 1
            return lo

        opened_any = False
        for rank, c in enumerate(candidates.to_dict('records'), start=1):
            m = estimate_margin(c['spot_close'], c['strike'], ot,
                                c['option_close'], mult, mr, 0.5,
                                exchange=exchange, product=product)
            if not np.isfinite(m) or m <= 0:
                self._bump_s1_funnel('open_skip_invalid_margin')
                continue
            one_stress_loss = float(c.get('stress_loss', 0.0) or 0.0)
            if self.config.get('s1_use_stress_sizing', False):
                if one_stress_loss <= 0 or remaining_stress_budget <= 0:
                    self._bump_s1_funnel('open_skip_invalid_stress_budget')
                    continue
                target_qty = int(remaining_stress_budget / one_stress_loss)
                if max_qty > 0:
                    target_qty = min(target_qty, max_qty)
                if target_qty < min_qty:
                    self._bump_s1_funnel('open_skip_budget_too_small')
                    continue
            else:
                if remaining_margin_budget <= 0:
                    self._bump_s1_funnel('open_skip_margin_budget_empty')
                    continue
                if baseline_mode and self.config.get('s1_baseline_equal_weight_contracts', True):
                    contracts_left = max(1, len(candidates) - rank + 1)
                    contract_budget = remaining_margin_budget / contracts_left
                    target_qty = max(1, int(contract_budget / m))
                else:
                    target_qty = max(1, int(remaining_margin_budget / m))
            nn = max_allowed_qty(c, m, target_qty, one_stress_loss)
            if nn <= 0:
                self._bump_s1_funnel('open_skip_portfolio_constraints')
                continue

            new_greeks = self._candidate_cash_greeks(c, ot, mult, nn, role='sell')
            total_stress_loss = one_stress_loss * nn if one_stress_loss > 0 else 0.0
            open_fee_per_contract = self._option_fee_per_contract(product, ot, action='open')
            close_fee_per_contract = self._option_fee_per_contract(product, ot, action='close')
            roundtrip_fee_per_contract = open_fee_per_contract + close_fee_per_contract
            pending_item = build_s1_sell_pending_item(
                row=c,
                product=product,
                option_type=ot,
                mult=mult,
                expiry=exp,
                mr=mr,
                exchange=exchange,
                date_str=date_str,
                group_id=group_id,
                quantity=nn,
                single_margin=m,
                new_greeks=new_greeks,
                one_stress_loss=one_stress_loss,
                total_stress_loss=total_stress_loss,
                open_fee_per_contract=open_fee_per_contract,
                close_fee_per_contract=close_fee_per_contract,
                roundtrip_fee_per_contract=roundtrip_fee_per_contract,
                iv_state=iv_state,
                side_meta=side_meta,
                side_budget_mult=side_budget_mult,
                max_candidates=max_candidates,
                max_delta_gap=max_delta_gap,
                max_qty=max_qty,
                current_vol_regime=self._current_vol_regimes.get(product, ''),
                effective_strategy_cap=effective_strategy_cap,
                product_budget_fields=self._pending_budget_fields(product_budget, effective_strategy_cap),
                stress_budget_pct=stress_budget_pct,
            )
            self._pending_opens.append(pending_item)
            self._bump_s1_funnel('open_sell_legs')
            self._bump_s1_funnel('open_sell_lots', nn)
            opened_any = True
            if self.config.get('s1_use_stress_sizing', False):
                remaining_stress_budget -= total_stress_loss
            else:
                remaining_margin_budget -= m * nn

            protect_enabled = bool(self.config.get('s1_protect_enabled', True))
            protect_ratio = max(0.0, float(self.config.get('s1_protect_ratio', 0.5)))
            protect_mode = self.config.get('s1_protect_mode', 'inner')
            protect_max_abs_delta = float(self.config.get('s1_protect_max_abs_delta', 0.25))
            protect_min_price = float(self.config.get('s1_protect_min_price', 0.5))
            protect_premium_ratio_cap = self.config.get('s1_protect_premium_ratio_cap', None)
            pr = select_s1_protect(
                ef, c,
                mode=protect_mode,
                max_abs_delta=protect_max_abs_delta,
                min_price=protect_min_price,
                premium_ratio_cap=protect_premium_ratio_cap,
            ) if protect_enabled and protect_ratio > 0 else None
            if pr is not None and pr['option_code'] != c['option_code']:
                protect_n = max(1, int(round(nn * protect_ratio)))
                self._pending_opens.append({
                    'strat': 'S1', 'product': product, 'code': pr['option_code'],
                    'opt_type': ot, 'strike': pr['strike'], 'ref_price': pr['option_close'],
                    'n': protect_n, 'mult': mult, 'expiry': exp, 'mr': mr,
                    'role': 'buy', 'spot': pr['spot_close'], 'exchange': exchange,
                    'group_id': group_id, 'underlying_code': pr.get('underlying_code'),
                    'signal_date': date_str,
                    'open_fee_per_contract': self._option_fee_per_contract(product, ot, action='open'),
                    'close_fee_per_contract': self._option_fee_per_contract(product, ot, action='close'),
                    'roundtrip_fee_per_contract': self._option_roundtrip_fee_per_contract(product, ot),
                })

        if opened_any and reentry_plan:
            self._clear_reentry_plan('S1', product, ot)

    def _try_open_s3(self, ef, product, ot, spot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, date_str, reentry_plan=None,
                     margin_cap=None, strategy_cap=None):
        """S3开仓"""
        cfg = self.config
        bl = select_s3_buy_by_otm(ef, ot, spot,
                                   target_otm_pct=cfg.get('s3_buy_otm_pct', 5.0),
                                   otm_range=tuple(cfg.get('s3_buy_otm_range', (3.0, 7.0))))
        if bl is None:
            return
        sell_target_otm = cfg.get('s3_sell_otm_pct', 10.0)
        sell_otm_range = tuple(cfg.get('s3_sell_otm_range', (7.0, 13.0)))
        if reentry_plan:
            otm_shift = float(cfg.get('s3_reentry_otm_shift', 2.0))
            sell_lo = max(0.5, sell_otm_range[0] - otm_shift)
            sell_hi = max(sell_lo, sell_otm_range[1] - otm_shift)
            sell_otm_range = (sell_lo, sell_hi)
            sell_target_otm = min(sell_hi, max(sell_lo, sell_target_otm - otm_shift))
        sl = select_s3_sell_by_otm(ef, ot, spot, bl['strike'],
                                    target_otm_pct=sell_target_otm,
                                    otm_range=sell_otm_range)
        if sl is None or sl['option_code'] == bl['option_code']:
            return
        sm = estimate_margin(sl['spot_close'], sl['strike'], ot,
                             sl['option_close'], mult, mr, 0.5,
                             exchange=exchange, product=product)
        size_result = calc_s3_size_v2(
            nav, margin_per, sm, bl['option_close'], sl['option_close'],
            mult, iv_scale,
            ratio_candidates=tuple(cfg.get('s3_ratio_candidates', (2, 3))),
            net_premium_tolerance=cfg.get('s3_net_premium_tolerance', 0.3)
        )
        if size_result is None:
            return
        bq, sq, ratio = size_result
        total_m = self._get_open_sell_margin_total()
        s3_m = self._get_open_sell_margin_total('S3')
        new_greeks = self._candidate_cash_greeks(sl, ot, mult, sq, role='sell')
        effective_margin_cap = self.config.get('margin_cap', 0.50) if margin_cap is None else margin_cap
        effective_strategy_cap = self.config.get('s3_margin_cap', 0.25) if strategy_cap is None else strategy_cap
        if not port_risk.check_margin_ok(
            total_m, s3_m, sm * sq, nav,
            effective_margin_cap,
            effective_strategy_cap,
        ):
            return
        if not self._passes_portfolio_construction(
            product, nav, sm * sq, date_str=date_str,
            new_cash_vega=new_greeks['cash_vega'],
            new_cash_gamma=new_greeks['cash_gamma'],
            option_type=ot,
            code=sl['option_code'],
            new_lots=sq,
        ):
            return
        group_id = f"S3_{product}_{ot}_{exp}_{date_str}"
        self._pending_opens.append({
            'strat': 'S3', 'product': product, 'code': bl['option_code'],
            'opt_type': ot, 'strike': bl['strike'], 'ref_price': bl['option_close'],
            'n': bq, 'mult': mult, 'expiry': exp, 'mr': mr, 'role': 'buy',
            'spot': bl['spot_close'], 'exchange': exchange, 'group_id': group_id,
            'underlying_code': bl.get('underlying_code'),
            'open_fee_per_contract': self._option_fee_per_contract(product, ot, action='open'),
            'close_fee_per_contract': self._option_fee_per_contract(product, ot, action='close'),
            'roundtrip_fee_per_contract': self._option_roundtrip_fee_per_contract(product, ot),
        })
        sell_pending_item = {
            'strat': 'S3', 'product': product, 'code': sl['option_code'],
            'opt_type': ot, 'strike': sl['strike'], 'ref_price': sl['option_close'],
            'n': sq, 'mult': mult, 'expiry': exp, 'mr': mr, 'role': 'sell',
            'spot': sl['spot_close'], 'exchange': exchange, 'group_id': group_id,
            'underlying_code': sl.get('underlying_code'),
            'margin': sm * sq,
            'cash_vega': new_greeks['cash_vega'],
            'cash_gamma': new_greeks['cash_gamma'],
            'open_fee_per_contract': self._option_fee_per_contract(product, ot, action='open'),
            'close_fee_per_contract': self._option_fee_per_contract(product, ot, action='close'),
            'roundtrip_fee_per_contract': self._option_roundtrip_fee_per_contract(product, ot),
        }
        sell_pending_item.update(self._pending_budget_fields(effective_strategy_cap))
        self._pending_opens.append(sell_pending_item)
        if reentry_plan:
            self._clear_reentry_plan('S3', product, ot)

    def _try_open_s4(self, ef, product, mult, mr, exchange, exp, nav, date_str):
        """S4开仓"""
        cfg = self.config
        n_products = max(cfg.get('products_top_n', 20), 1)
        for ot in ['P', 'C']:
            opt = select_s4(ef, ot)
            if opt is None:
                continue
            cost = opt['option_close'] * mult
            if cost <= 0:
                continue
            qty = calc_s4_size(nav, cfg.get('s4_prem', 0.005), n_products, cost,
                               max_hands=cfg.get('s4_max_hands', 5))
            group_id = f"S4_{product}_{ot}_{exp}_{date_str}"
            self._pending_opens.append({
                'strat': 'S4', 'product': product, 'code': opt['option_code'],
                'opt_type': ot, 'strike': opt['strike'], 'ref_price': opt['option_close'],
                'n': qty, 'mult': mult, 'expiry': exp, 'mr': mr, 'role': 'buy',
                'spot': opt['spot_close'], 'exchange': exchange, 'group_id': group_id,
                'underlying_code': opt.get('underlying_code'),
                'open_fee_per_contract': self._option_fee_per_contract(product, ot, action='open'),
                'close_fee_per_contract': self._option_fee_per_contract(product, ot, action='close'),
                'roundtrip_fee_per_contract': self._option_roundtrip_fee_per_contract(product, ot),
            })

    # ── 平仓 ─────────────────────────────────────────────────────────────────

    def _close_positions(self, positions, date_str, reason, fee_per_hand, exec_time='', close_qty_by_pos=None):
        """Close or partially close an explicit list of positions."""
        to_close = []
        seen = set()
        for pos in positions:
            if pos not in self.positions:
                continue
            key = id(pos)
            if key in seen:
                continue
            seen.add(key)
            to_close.append(pos)
        if exec_time and self.config.get('skip_same_day_exit_for_vwap_opens', True):
            to_close = [p for p in to_close if p.open_date != date_str]
            if not to_close:
                return
        if reason.startswith('sl_') and self.config.get('reentry_plan_enabled', True):
            for pos in to_close:
                if pos.role == 'sell':
                    self._register_reentry_plan(pos, date_str)
                    break

        fully_closed = set()
        close_qty_by_pos = close_qty_by_pos or {}
        for pos in to_close:
            original_n = int(pos.n)
            if original_n <= 0:
                fully_closed.add(pos)
                continue
            close_qty = int(close_qty_by_pos.get(pos, original_n))
            close_qty = min(max(close_qty, 1), original_n)
            is_partial = close_qty < original_n

            original_stress_loss = float(pos.stress_loss or 0.0)
            original_n_value = pos.n
            original_cur_price = pos.cur_price
            if is_partial:
                pos.n = close_qty
                pos.stress_loss = original_stress_loss * close_qty / original_n

            raw_execution_price = pos.cur_price
            close_action = 'sell_close' if pos.role in ('buy', 'protect') else 'buy_close'
            pos.cur_price, execution_slippage = apply_execution_slippage(
                raw_execution_price,
                close_action,
                self.config,
                reason=reason,
            )
            slippage_cash = float(execution_slippage) * float(pos.mult) * float(pos.n)
            if pos.role in ('buy', 'protect'):
                pnl = (pos.cur_price - pos.prev_price) * pos.mult * pos.n
            else:
                pnl = (pos.prev_price - pos.cur_price) * pos.mult * pos.n
            pa = pos.pnl_attribution(total_pnl=pnl)
            for k, v in pa.items():
                if k.endswith('_pnl') and k in self._day_attr_realized:
                    self._day_attr_realized[k] += float(v)
            fee_action = 'close'
            if reason == 'expiry':
                if float(pos.cur_price or 0.0) <= 0.0:
                    fee_per_contract = 0.0
                    fee_action = 'expire_otm'
                elif pos.role in ('buy', 'protect'):
                    fee_action = 'exercise'
                    fee_per_contract = self._position_fee_per_contract(
                        pos, action='exercise', default=fee_per_hand,
                    )
                else:
                    fee_action = 'assign'
                    fee_per_contract = self._position_fee_per_contract(
                        pos, action='assign', default=fee_per_hand,
                    )
            else:
                if pos.open_date == date_str:
                    fee_action = 'close_today'
                fee_per_contract = self._position_fee_per_contract(
                    pos, action=fee_action, default=fee_per_hand,
                )
            fee = fee_per_contract * pos.n
            self._day_realized['pnl'] += pnl
            self._day_realized['fee'] += fee
            strat_key = pos.strat.lower()
            if strat_key in self._day_realized:
                self._day_realized[strat_key] += pnl

            if pos.role in ('buy', 'protect'):
                order_pnl = (pos.cur_price - pos.open_price) * pos.mult * pos.n
            else:
                order_pnl = (pos.open_price - pos.cur_price) * pos.mult * pos.n

            open_premium_cash = float(pos.open_price) * float(pos.mult) * float(pos.n)
            close_value_cash = float(pos.cur_price) * float(pos.mult) * float(pos.n)
            premium_retained_cash = (
                open_premium_cash - close_value_cash
                if pos.role == 'sell'
                else np.nan
            )
            premium_retained_pct = (
                premium_retained_cash / open_premium_cash
                if pos.role == 'sell' and open_premium_cash > 0
                else np.nan
            )
            order_record = {
                'date': date_str, 'action': reason,
                'time': exec_time or '',
                'strategy': pos.strat, 'product': pos.product,
                'code': pos.code, 'option_type': pos.opt_type,
                'strike': pos.strike, 'expiry': str(pos.expiry)[:10],
                'price': round(pos.cur_price, 4), 'quantity': pos.n,
                'raw_execution_price': round(raw_execution_price, 4),
                'execution_slippage': round(execution_slippage, 6),
                'execution_slippage_cash': round(slippage_cash, 2),
                'fee': round(fee, 2),
                'fee_per_contract': fee_per_contract,
                'fee_action': fee_action,
                'pnl': round(order_pnl, 2),
                'stress_loss': round(float(pos.stress_loss or 0.0), 2),
                'margin_at_close': round(pos.cur_margin() if pos.role == 'sell' else 0.0, 2),
                'open_premium_cash': round(open_premium_cash, 2),
                'close_value_cash': round(close_value_cash, 2),
                'premium_retained_cash': round(premium_retained_cash, 2)
                if np.isfinite(premium_retained_cash) else np.nan,
                'premium_retained_pct': premium_retained_pct,
                'position_close_qty': int(close_qty),
                'position_remaining_qty': int(original_n - close_qty),
            }
            for key, value in getattr(pos, 'entry_meta', {}).items():
                order_record.setdefault(key, value)
            self.orders.append(order_record)

            if is_partial:
                remaining_n = original_n - close_qty
                pos.n = remaining_n
                pos.stress_loss = original_stress_loss * remaining_n / original_n
                pos.cur_price = original_cur_price
            else:
                pos.n = original_n_value
                pos.stress_loss = original_stress_loss
                fully_closed.add(pos)

        if fully_closed:
            self.positions = [p for p in self.positions if p not in fully_closed]

    def _close_group(self, trigger_pos, date_str, reason, fee_per_hand, exec_time=''):
        """平仓整组"""
        gid = trigger_pos.group_id
        to_close = [p for p in self.positions if p.group_id == gid and gid] if gid else [trigger_pos]
        if exec_time and self.config.get('skip_same_day_exit_for_vwap_opens', True):
            to_close = [p for p in to_close if p.open_date != date_str]
            if not to_close:
                return
        if reason.startswith('sl_') and self.config.get('reentry_plan_enabled', True):
            for pos in to_close:
                if pos.role == 'sell':
                    self._register_reentry_plan(pos, date_str)
                    break

        for pos in to_close:
            raw_execution_price = pos.cur_price
            close_action = 'sell_close' if pos.role in ('buy', 'protect') else 'buy_close'
            pos.cur_price, execution_slippage = apply_execution_slippage(
                raw_execution_price,
                close_action,
                self.config,
                reason=reason,
            )
            slippage_cash = float(execution_slippage) * float(pos.mult) * float(pos.n)
            if pos.role in ('buy', 'protect'):
                pnl = (pos.cur_price - pos.prev_price) * pos.mult * pos.n
            else:
                pnl = (pos.prev_price - pos.cur_price) * pos.mult * pos.n
            pa = pos.pnl_attribution(total_pnl=pnl)
            for k, v in pa.items():
                if k.endswith('_pnl') and k in self._day_attr_realized:
                    self._day_attr_realized[k] += float(v)
            fee_action = 'close'
            if reason == 'expiry':
                if float(pos.cur_price or 0.0) <= 0.0:
                    fee_per_contract = 0.0
                    fee_action = 'expire_otm'
                elif pos.role in ('buy', 'protect'):
                    fee_action = 'exercise'
                    fee_per_contract = self._position_fee_per_contract(
                        pos, action='exercise', default=fee_per_hand,
                    )
                else:
                    fee_action = 'assign'
                    fee_per_contract = self._position_fee_per_contract(
                        pos, action='assign', default=fee_per_hand,
                    )
            else:
                if pos.open_date == date_str:
                    fee_action = 'close_today'
                fee_per_contract = self._position_fee_per_contract(
                    pos, action=fee_action, default=fee_per_hand,
                )
            fee = fee_per_contract * pos.n
            self._day_realized['pnl'] += pnl
            self._day_realized['fee'] += fee
            strat_key = pos.strat.lower()
            if strat_key in self._day_realized:
                self._day_realized[strat_key] += pnl

            # 全生命周期PnL
            if pos.role in ('buy', 'protect'):
                order_pnl = (pos.cur_price - pos.open_price) * pos.mult * pos.n
            else:
                order_pnl = (pos.open_price - pos.cur_price) * pos.mult * pos.n

            open_premium_cash = float(pos.open_price) * float(pos.mult) * float(pos.n)
            close_value_cash = float(pos.cur_price) * float(pos.mult) * float(pos.n)
            premium_retained_cash = (
                open_premium_cash - close_value_cash
                if pos.role == 'sell'
                else np.nan
            )
            premium_retained_pct = (
                premium_retained_cash / open_premium_cash
                if pos.role == 'sell' and open_premium_cash > 0
                else np.nan
            )
            order_record = {
                'date': date_str, 'action': reason,
                'time': exec_time or '',
                'strategy': pos.strat, 'product': pos.product,
                'code': pos.code, 'option_type': pos.opt_type,
                'strike': pos.strike, 'expiry': str(pos.expiry)[:10],
                'price': round(pos.cur_price, 4), 'quantity': pos.n,
                'raw_execution_price': round(raw_execution_price, 4),
                'execution_slippage': round(execution_slippage, 6),
                'execution_slippage_cash': round(slippage_cash, 2),
                'fee': round(fee, 2),
                'fee_per_contract': fee_per_contract,
                'fee_action': fee_action,
                'pnl': round(order_pnl, 2),
                'stress_loss': round(float(pos.stress_loss or 0.0), 2),
                'margin_at_close': round(pos.cur_margin() if pos.role == 'sell' else 0.0, 2),
                'open_premium_cash': round(open_premium_cash, 2),
                'close_value_cash': round(close_value_cash, 2),
                'premium_retained_cash': round(premium_retained_cash, 2)
                if np.isfinite(premium_retained_cash) else np.nan,
                'premium_retained_pct': premium_retained_pct,
            }
            for key, value in getattr(pos, 'entry_meta', {}).items():
                order_record.setdefault(key, value)
            self.orders.append(order_record)

        self.positions = [p for p in self.positions if p not in to_close]


    # ── NAV快照 ──────────────────────────────────────────────────────────────

    def _s1_sell_shape_snapshot(self, nav):
        nav = max(float(nav), 1.0)
        side_state = defaultdict(lambda: {
            'lots': 0.0,
            'contracts': set(),
            'products': set(),
            'open_premium': 0.0,
            'liability': 0.0,
        })
        regime_state = defaultdict(lambda: {
            'lots': 0.0,
            'contracts': set(),
            'products': set(),
            'open_premium': 0.0,
            'liability': 0.0,
            'margin': 0.0,
            'stress_loss': 0.0,
        })
        total_lots = 0.0
        total_contracts = set()
        total_products = set()
        total_open_premium = 0.0
        total_liability = 0.0
        total_margin = 0.0
        total_stress_loss = 0.0

        for pos in self.positions:
            if pos.strat != 'S1' or pos.role != 'sell':
                continue
            side = str(pos.opt_type or '').upper()[:1]
            product = self._normalize_product_key(pos.product)
            lots = float(pos.n or 0.0)
            open_premium = float(pos.open_price) * float(pos.mult) * lots
            liability = float(pos.cur_price) * float(pos.mult) * lots
            margin = float(pos.cur_margin() or 0.0)
            stress_loss = float(getattr(pos, 'stress_loss', 0.0) or 0.0)
            regime = self._current_vol_regimes.get(product, 'normal_vol')

            total_lots += lots
            total_contracts.add(pos.code)
            total_products.add(product)
            total_open_premium += open_premium
            total_liability += liability
            total_margin += margin
            total_stress_loss += stress_loss

            state = side_state[side]
            state['lots'] += lots
            state['contracts'].add(pos.code)
            state['products'].add(product)
            state['open_premium'] += open_premium
            state['liability'] += liability

            rstate = regime_state[regime]
            rstate['lots'] += lots
            rstate['contracts'].add(pos.code)
            rstate['products'].add(product)
            rstate['open_premium'] += open_premium
            rstate['liability'] += liability
            rstate['margin'] += margin
            rstate['stress_loss'] += stress_loss

        snapshot = {
            's1_active_sell_lots': total_lots,
            's1_active_sell_contracts': len(total_contracts),
            's1_active_sell_products': len(total_products),
            's1_lots_per_contract': total_lots / len(total_contracts) if total_contracts else 0.0,
            's1_contracts_per_product': (
                len(total_contracts) / len(total_products) if total_products else 0.0
            ),
            's1_short_open_premium': total_open_premium,
            's1_short_liability': total_liability,
            's1_short_unrealized_premium': total_open_premium - total_liability,
            's1_short_open_premium_pct': total_open_premium / nav,
            's1_short_liability_pct': total_liability / nav,
            's1_short_unrealized_premium_pct': (total_open_premium - total_liability) / nav,
            's1_margin_used': total_margin,
            's1_margin_used_pct': total_margin / nav,
            's1_stress_loss_used': total_stress_loss,
            's1_stress_loss_used_pct': total_stress_loss / nav,
        }
        call_lots = side_state['C']['lots']
        put_lots = side_state['P']['lots']
        snapshot['s1_call_lot_share'] = call_lots / total_lots if total_lots > 0 else 0.0
        snapshot['s1_put_call_lot_ratio'] = put_lots / call_lots if call_lots > 0 else np.nan

        for side, label in (('C', 'call'), ('P', 'put')):
            state = side_state[side]
            lots = state['lots']
            open_premium = state['open_premium']
            liability = state['liability']
            snapshot[f's1_active_{label}_lots'] = lots
            snapshot[f's1_active_{label}_contracts'] = len(state['contracts'])
            snapshot[f's1_active_{label}_products'] = len(state['products'])
            snapshot[f's1_{label}_open_premium'] = open_premium
            snapshot[f's1_{label}_liability'] = liability
            snapshot[f's1_{label}_unrealized_premium'] = open_premium - liability
            snapshot[f's1_{label}_open_premium_pct'] = open_premium / nav
            snapshot[f's1_{label}_liability_pct'] = liability / nav
        regime_labels = {
            'falling_vol_carry': 'falling',
            'low_stable_vol': 'low',
            'normal_vol': 'normal',
            'high_rising_vol': 'high',
            'post_stop_cooldown': 'post_stop',
        }
        for regime, label in regime_labels.items():
            state = regime_state[regime]
            open_premium = state['open_premium']
            margin = state['margin']
            snapshot[f's1_{label}_lots'] = state['lots']
            snapshot[f's1_{label}_contracts'] = len(state['contracts'])
            snapshot[f's1_{label}_products'] = len(state['products'])
            snapshot[f's1_{label}_open_premium_pct'] = open_premium / nav
            snapshot[f's1_{label}_margin_pct'] = margin / nav
            snapshot[f's1_{label}_stress_loss_pct'] = state['stress_loss'] / nav
            snapshot[f's1_{label}_open_premium_share'] = (
                open_premium / total_open_premium if total_open_premium > 0 else 0.0
            )
            snapshot[f's1_{label}_margin_share'] = (
                margin / total_margin if total_margin > 0 else 0.0
            )

        products_score = min(len(total_products) / 15.0, 1.0) if total_products else 0.0
        contracts_score = min(len(total_contracts) / 40.0, 1.0) if total_contracts else 0.0
        lots_per_contract = snapshot['s1_lots_per_contract']
        granularity_score = min(20.0 / lots_per_contract, 1.0) if lots_per_contract > 0 else 0.0
        premium_score = min((total_open_premium / nav) / 0.004, 1.0) if total_open_premium > 0 else 0.0
        margin_score = min((total_margin / nav) / 0.12, 1.0) if total_margin > 0 else 0.0
        snapshot['s1_ledet_similarity_score'] = 100.0 * (
            0.25 * products_score +
            0.25 * contracts_score +
            0.20 * granularity_score +
            0.15 * premium_score +
            0.15 * margin_score
        )
        snapshot['s1_ledet_products_score'] = products_score
        snapshot['s1_ledet_contracts_score'] = contracts_score
        snapshot['s1_ledet_granularity_score'] = granularity_score
        snapshot['s1_ledet_premium_score'] = premium_score
        snapshot['s1_ledet_margin_score'] = margin_score
        return snapshot

    def _record_daily_diagnostics(self, date_str, nav):
        if not self.config.get('portfolio_diagnostics_enabled', True):
            return
        nav = max(float(nav), 1.0)
        budget = self._current_open_budget or self._get_effective_open_budget()
        bucket_max_active = int(self.config.get('portfolio_bucket_max_active_products', 3) or 0)
        corr_max_active = int(self.config.get('portfolio_corr_group_max_active_products', 2) or 0)
        bucket_margin_cap = float(budget.get('bucket_margin_cap', 0.0) or 0.0)
        bucket_stress_cap = float(budget.get('portfolio_bucket_stress_loss_cap', 0.0) or 0.0)
        corr_group_margin_cap = float(budget.get('corr_group_margin_cap', 0.0) or 0.0)
        corr_group_stress_cap = float(budget.get('corr_group_stress_loss_cap', 0.0) or 0.0)
        product_side_margin_cap = float(budget.get('product_side_margin_cap', 0.0) or 0.0)
        product_side_stress_cap = float(budget.get('product_side_stress_loss_cap', 0.0) or 0.0)
        contract_lot_cap = int(self.config.get('portfolio_contract_lot_cap', 0) or 0)
        contract_stress_cap = float(budget.get('contract_stress_loss_cap', 0.0) or 0.0)
        bucket_state = defaultdict(lambda: {
            'margin': 0.0,
            'cash_vega': 0.0,
            'cash_gamma': 0.0,
            'stress_loss': 0.0,
            'products': set(),
            'positions': 0,
        })
        corr_state = defaultdict(lambda: {
            'margin': 0.0,
            'cash_vega': 0.0,
            'cash_gamma': 0.0,
            'stress_loss': 0.0,
            'products': set(),
            'positions': 0,
        })
        s1_product_side_state = defaultdict(lambda: {
            'margin': 0.0,
            'cash_vega': 0.0,
            'cash_gamma': 0.0,
            'cash_theta': 0.0,
            'stress_loss': 0.0,
            'contracts': set(),
            'contract_lots': defaultdict(float),
            'contract_stress_loss': defaultdict(float),
            'lots': 0.0,
            'open_premium': 0.0,
            'liability': 0.0,
        })
        for pos in self.positions:
            bucket = self._get_product_bucket(pos.product)
            corr_group = self._get_product_corr_group(pos.product)
            margin = pos.cur_margin() if pos.role == 'sell' else 0.0
            cv = pos.cash_vega()
            cg = pos.cash_gamma()
            stress_loss = float(getattr(pos, 'stress_loss', 0.0) or 0.0)
            for state, key in ((bucket_state, bucket), (corr_state, corr_group)):
                state[key]['margin'] += margin
                state[key]['cash_vega'] += cv
                state[key]['cash_gamma'] += cg
                state[key]['stress_loss'] += stress_loss
                state[key]['products'].add(self._normalize_product_key(pos.product))
                state[key]['positions'] += 1
            if pos.strat == 'S1' and pos.role == 'sell':
                side = str(pos.opt_type or '').upper()[:1]
                product = self._normalize_product_key(pos.product)
                data = s1_product_side_state[(product, side)]
                data['margin'] += margin
                data['cash_vega'] += cv
                data['cash_gamma'] += cg
                data['cash_theta'] += pos.cash_theta()
                data['stress_loss'] += stress_loss
                data['contracts'].add(pos.code)
                lots = float(pos.n or 0.0)
                data['lots'] += lots
                data['contract_lots'][pos.code] += lots
                data['contract_stress_loss'][pos.code] += stress_loss
                data['open_premium'] += float(pos.open_price) * float(pos.mult) * float(pos.n or 0.0)
                data['liability'] += float(pos.cur_price) * float(pos.mult) * float(pos.n or 0.0)
        for bucket, data in bucket_state.items():
            self.diagnostics_records.append({
                'date': date_str,
                'scope': 'bucket',
                'name': bucket,
                'margin_pct': data['margin'] / nav,
                'cash_vega_pct': data['cash_vega'] / nav,
                'cash_gamma_pct': data['cash_gamma'] / nav,
                'stress_loss_pct': data['stress_loss'] / nav,
                'margin_cap': bucket_margin_cap,
                'stress_loss_cap': bucket_stress_cap,
                'margin_cap_used': (
                    data['margin'] / nav / bucket_margin_cap if bucket_margin_cap > 0 else np.nan
                ),
                'stress_cap_used': (
                    data['stress_loss'] / nav / bucket_stress_cap if bucket_stress_cap > 0 else np.nan
                ),
                'n_products': len(data['products']),
                'n_positions': data['positions'],
                'max_active_products': bucket_max_active,
                'active_product_cap_used': (
                    len(data['products']) / bucket_max_active if bucket_max_active > 0 else np.nan
                ),
                'portfolio_vol_regime': self._current_portfolio_regime,
            })
        for (product, side), data in s1_product_side_state.items():
            bucket = self._get_product_bucket(product)
            corr_group = self._get_product_corr_group(product)
            unrealized_premium = data['open_premium'] - data['liability']
            max_contract_lots = max(data['contract_lots'].values()) if data['contract_lots'] else 0.0
            max_contract_stress = (
                max(data['contract_stress_loss'].values())
                if data['contract_stress_loss'] else 0.0
            )
            self.diagnostics_records.append({
                'date': date_str,
                'scope': 's1_product_side',
                'name': f'{product}:{side}',
                'product': product,
                'option_type': side,
                'bucket': bucket,
                'corr_group': corr_group,
                'product_vol_regime': self._current_vol_regimes.get(product, ''),
                'lots': data['lots'],
                'n_contracts': len(data['contracts']),
                'max_contract_lots': max_contract_lots,
                'margin_pct': data['margin'] / nav,
                'cash_vega_pct': data['cash_vega'] / nav,
                'cash_gamma_pct': data['cash_gamma'] / nav,
                'cash_theta_pct': data['cash_theta'] / nav,
                'stress_loss_pct': data['stress_loss'] / nav,
                'max_contract_stress_loss_pct': max_contract_stress / nav,
                'margin_cap': product_side_margin_cap,
                'stress_loss_cap': product_side_stress_cap,
                'contract_lot_cap': contract_lot_cap,
                'contract_stress_loss_cap': contract_stress_cap,
                'margin_cap_used': (
                    data['margin'] / nav / product_side_margin_cap
                    if product_side_margin_cap > 0 else np.nan
                ),
                'stress_cap_used': (
                    data['stress_loss'] / nav / product_side_stress_cap
                    if product_side_stress_cap > 0 else np.nan
                ),
                'max_contract_lot_cap_used': (
                    max_contract_lots / contract_lot_cap if contract_lot_cap > 0 else np.nan
                ),
                'max_contract_stress_cap_used': (
                    max_contract_stress / nav / contract_stress_cap
                    if contract_stress_cap > 0 else np.nan
                ),
                'open_premium': data['open_premium'],
                'current_liability': data['liability'],
                'unrealized_premium': unrealized_premium,
                'open_premium_pct': data['open_premium'] / nav,
                'current_liability_pct': data['liability'] / nav,
                'unrealized_premium_pct': unrealized_premium / nav,
                'portfolio_vol_regime': self._current_portfolio_regime,
            })
        for group, data in corr_state.items():
            self.diagnostics_records.append({
                'date': date_str,
                'scope': 'corr_group',
                'name': group,
                'margin_pct': data['margin'] / nav,
                'cash_vega_pct': data['cash_vega'] / nav,
                'cash_gamma_pct': data['cash_gamma'] / nav,
                'stress_loss_pct': data['stress_loss'] / nav,
                'margin_cap': corr_group_margin_cap,
                'stress_loss_cap': corr_group_stress_cap,
                'margin_cap_used': (
                    data['margin'] / nav / corr_group_margin_cap
                    if corr_group_margin_cap > 0 else np.nan
                ),
                'stress_cap_used': (
                    data['stress_loss'] / nav / corr_group_stress_cap
                    if corr_group_stress_cap > 0 else np.nan
                ),
                'n_products': len(data['products']),
                'n_positions': data['positions'],
                'max_active_products': corr_max_active,
                'active_product_cap_used': (
                    len(data['products']) / corr_max_active if corr_max_active > 0 else np.nan
                ),
                'portfolio_vol_regime': self._current_portfolio_regime,
            })

    def _update_nav_snapshot(self, date_str):
        """记录每日NAV"""
        holding_pnl = sum(p.daily_pnl() for p in self.positions)
        realized_pnl = self._day_realized['pnl']
        realized_fee = self._day_realized['fee']
        day_pnl = holding_pnl + realized_pnl - realized_fee

        s1_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == 'S1') + self._day_realized['s1']
        s3_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == 'S3') + self._day_realized['s3']
        s4_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == 'S4') + self._day_realized['s4']

        # PnL归因
        attr = dict(self._day_attr_realized)
        for p in self.positions:
            pa = p.pnl_attribution()
            for k in attr:
                attr[k] += pa[k]

        cum_pnl = (self.nav_records[-1]['cum_pnl'] if self.nav_records else 0) + day_pnl
        nav = self.capital + cum_pnl
        margin = sum(p.cur_margin() for p in self.positions if p.role == 'sell')

        cd = sum(p.cash_delta() for p in self.positions)
        cv = sum(p.cash_vega() for p in self.positions)
        cg = sum(p.cash_gamma() for p in self.positions)
        budget = self._current_open_budget or self._get_effective_open_budget()
        regime_counts = self._current_vol_regime_counts or Counter()
        stress_state = self._get_open_stress_loss_state()
        structural_low_count = sum(
            1 for state in self._current_iv_state.values()
            if state.get('is_structural_low_iv')
        )
        self._record_daily_diagnostics(date_str, nav)
        s1_shape = self._s1_sell_shape_snapshot(nav)

        nav_record = {
            'date': date_str, 'nav': nav, 'cum_pnl': cum_pnl,
            's1_pnl': s1_pnl, 's3_pnl': s3_pnl, 's4_pnl': s4_pnl,
            'fee': realized_fee, 'margin_used': margin,
            'cash_delta': cd / max(nav, 1), 'cash_vega': cv / max(nav, 1),
            'cash_gamma': cg / max(nav, 1),
            'delta_pnl': attr['delta_pnl'], 'gamma_pnl': attr['gamma_pnl'],
            'theta_pnl': attr['theta_pnl'], 'vega_pnl': attr['vega_pnl'],
            'residual_pnl': attr['residual_pnl'],
            'portfolio_vol_regime': self._current_portfolio_regime,
            'effective_margin_cap': budget.get('margin_cap', np.nan),
            'effective_s1_margin_cap': budget.get('s1_margin_cap', np.nan),
            'effective_s3_margin_cap': budget.get('s3_margin_cap', np.nan),
            'effective_product_margin_cap': budget.get('product_margin_cap', np.nan),
            'effective_product_side_margin_cap': budget.get('product_side_margin_cap', np.nan),
            'effective_bucket_margin_cap': budget.get('bucket_margin_cap', np.nan),
            'effective_corr_group_margin_cap': budget.get('corr_group_margin_cap', np.nan),
            'effective_bucket_max_active_products': int(
                self.config.get('portfolio_bucket_max_active_products', 3) or 0
            ),
            'effective_corr_group_max_active_products': int(
                self.config.get('portfolio_corr_group_max_active_products', 2) or 0
            ),
            'effective_stress_loss_cap': budget.get('portfolio_stress_loss_cap', np.nan),
            'effective_bucket_stress_loss_cap': budget.get('portfolio_bucket_stress_loss_cap', np.nan),
            'effective_product_side_stress_loss_cap': budget.get('product_side_stress_loss_cap', np.nan),
            'effective_corr_group_stress_loss_cap': budget.get('corr_group_stress_loss_cap', np.nan),
            'effective_contract_stress_loss_cap': budget.get('contract_stress_loss_cap', np.nan),
            'effective_contract_lot_cap': int(self.config.get('portfolio_contract_lot_cap', 0) or 0),
            'effective_s1_stress_budget_pct': budget.get('s1_stress_loss_budget_pct', np.nan),
            'open_budget_risk_scale': budget.get('risk_scale', np.nan),
            'open_budget_brake_reason': budget.get('brake_reason', ''),
            'current_drawdown': budget.get('current_drawdown', np.nan),
            'recent_stop_count': budget.get('recent_stop_count', np.nan),
            'stress_loss_used': stress_state.get('stress_loss', 0.0) / max(nav, 1),
            'vol_falling_products': regime_counts.get('falling_vol_carry', 0),
            'vol_low_products': regime_counts.get('low_stable_vol', 0),
            'vol_normal_products': regime_counts.get('normal_vol', 0),
            'vol_high_products': regime_counts.get('high_rising_vol', 0),
            'vol_post_stop_products': regime_counts.get('post_stop_cooldown', 0),
            'structural_low_iv_products': structural_low_count,
            'n_positions': len(self.positions),
        }
        nav_record.update(s1_shape)
        self.nav_records.append(nav_record)

        for p in self.positions:
            p.prev_price = p.cur_price
            p.prev_spot = p.cur_spot
            p.prev_iv = p.cur_iv
            p.prev_delta = p.cur_delta
            p.prev_gamma = p.cur_gamma
            p.prev_vega = p.cur_vega
            p.prev_theta = p.cur_theta

    # ── 输出 ─────────────────────────────────────────────────────────────────

    def _output_results(self, nav_df, orders_df, stats, tag, elapsed):
        """输出CSV和报告"""
        write_backtest_outputs(
            nav_df,
            orders_df,
            self.diagnostics_records,
            stats,
            tag,
            elapsed,
            output_dir=OUTPUT_DIR,
            logger=logger,
        )
        self._write_s1_candidate_outputs(tag)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Toolkit分钟回测引擎')
    parser.add_argument('--start-date', type=str, default=None)
    parser.add_argument('--end-date', type=str, default=None)
    parser.add_argument('--products', type=str, default=None,
                        help='品种列表，逗号分隔，如 m,cu,au,IO')
    parser.add_argument('--tag', type=str, default='toolkit')
    parser.add_argument('--config', type=str, default=None)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)-5s %(message)s',
                        datefmt='%H:%M:%S')

    products = args.products.split(',') if args.products else None

    engine = ToolkitMinuteEngine(config_path=args.config)
    engine.run(start_date=args.start_date, end_date=args.end_date,
               products=products, tag=args.tag)


if __name__ == '__main__':
    main()
