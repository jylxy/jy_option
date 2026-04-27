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
        self._product_first_trade_dates = {}
        self._product_like_sql_cache = {}
        self._warmup_contract_sql_cache = {}
        self._s1_candidate_funnel = None

        # 策略参数（与strategy_rules.py DEFAULT_PARAMS一致，此处显式声明便于查看）
        # DTE 30-45（次月合约），止盈50%不重开
        # 30品种时 margin_per 调低，避免保证金上限卡住太多品种

    _ENTRY_META_FIELDS = (
        'signal_date',
        'signal_ref_price', 'execution_price_drift',
        'premium_stress', 'theta_stress', 'premium_margin',
        'signal_premium_stress', 'signal_theta_stress', 'signal_premium_margin',
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
        return vol_rules.register_reentry_plan(
            pos,
            date_str,
            config=self.config,
            stop_history=self._stop_history,
            reentry_plans=self._reentry_plans,
            shift_trading_date=self._shift_trading_date,
            normalize_product_key=self._normalize_product_key,
        )

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
        day_volume = {}
        bars_by_code = {}
        if not minute_df.empty:
            vol_agg = minute_df.groupby('ths_code')['volume'].sum()
            day_volume = vol_agg.to_dict()
            bars_by_code = {
                code: grp.sort_values('time')
                for code, grp in minute_df.groupby('ths_code', sort=False)
            }

        for item in self._pending_opens:
            code = item['code']
            code_bars = bars_by_code.get(code)

            # 计算全天TWAP/VWAP（只用当日真实分钟成交）
            if code_bars is not None and not code_bars.empty:
                valid = code_bars[code_bars['volume'] > 0]
                if not valid.empty:
                    prices = valid['close'].values.astype(float)
                    volumes = valid['volume'].values.astype(float)
                    total_vol = volumes.sum()
                    price = float(np.sum(prices * volumes) / total_vol) if total_vol > 0 else float(prices[-1])
                else:
                    price = 0.0
            else:
                price = 0.0

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
            today_vol = day_volume.get(code, 0)
            max_today = max(1, int(today_vol * vol_limit_pct)) if today_vol > 0 else target_n
            actual_n = min(target_n, max_today)
            remaining_n = target_n - actual_n

            if remaining_n > 0:
                # 超量部分留到下一天
                deferred_item = dict(item)
                deferred_item['n'] = remaining_n
                original_n = float(item.get('n', 0) or 0)
                scale = remaining_n / original_n if original_n > 0 else 0.0
                one_loss = float(deferred_item.get('one_contract_stress_loss', 0.0) or 0.0)
                if one_loss > 0:
                    deferred_item['stress_loss'] = one_loss * remaining_n
                if 'cash_vega' in deferred_item:
                    deferred_item['cash_vega'] = float(deferred_item.get('cash_vega', 0.0) or 0.0) * scale
                if 'cash_gamma' in deferred_item:
                    deferred_item['cash_gamma'] = float(deferred_item.get('cash_gamma', 0.0) or 0.0) * scale
                if 'margin' in deferred_item:
                    deferred_item['margin'] = float(deferred_item.get('margin', 0.0) or 0.0) * scale
                deferred.append(deferred_item)
                logger.debug("  %s 分批建仓: %s 目标%d手, 今日%d手(当日成交%d), 剩余%d手",
                             date_str, code, target_n, actual_n, today_vol, remaining_n)

            if actual_n <= 0:
                continue

            # 保证金验证（卖腿）
            if item['role'] == 'sell':
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
                    continue
                item_n = float(item.get('n', 0) or 0)
                exposure_scale = actual_n / item_n if item_n > 0 else 0.0
                new_cash_vega = float(item.get('cash_vega', 0.0) or 0.0) * exposure_scale
                new_cash_gamma = float(item.get('cash_gamma', 0.0) or 0.0) * exposure_scale
                new_stress_loss = float(item.get('one_contract_stress_loss', 0.0) or 0.0) * actual_n
                if not self._passes_portfolio_construction(
                    item.get('product', ''), nav, new_m, date_str=date_str,
                    new_cash_vega=new_cash_vega,
                    new_cash_gamma=new_cash_gamma,
                    new_stress_loss=new_stress_loss,
                    budget=exec_budget,
                    include_pending=False,
                    option_type=item.get('opt_type'),
                    code=code,
                    new_lots=actual_n,
                ):
                    continue

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
            ref_price = self._safe_float(item.get('ref_price', np.nan), np.nan)
            price_drift = price / ref_price - 1.0 if np.isfinite(ref_price) and ref_price > 0 else np.nan
            theta = self._safe_float(item.get('theta', np.nan), np.nan)
            theta_cash = abs(theta) * float(item['mult']) * float(actual_n) if np.isfinite(theta) else np.nan
            stress_loss = float(pos.stress_loss or 0.0)
            exec_premium_stress = (
                net_premium_cash / stress_loss
                if item['role'] == 'sell' and stress_loss > 0
                else np.nan
            )
            exec_theta_stress = (
                theta_cash / stress_loss
                if item['role'] == 'sell' and stress_loss > 0 and np.isfinite(theta_cash)
                else np.nan
            )
            exec_premium_margin = (
                net_premium_cash / open_margin
                if item['role'] == 'sell' and open_margin > 0
                else np.nan
            )
            self.orders.append({
                'date': date_str, 'signal_date': item.get('signal_date', ''),
                'action': f"open_{item['role']}",
                'strategy': item['strat'], 'product': item['product'],
                'code': code, 'option_type': item['opt_type'],
                'strike': item['strike'], 'expiry': str(item['expiry'])[:10],
                'price': round(price, 4), 'quantity': actual_n,
                'raw_execution_price': round(raw_execution_price, 4),
                'execution_slippage': round(execution_slippage, 6),
                'execution_slippage_cash': round(slippage_cash, 2),
                'fee': round(open_fee, 2),
                'fee_per_contract': open_fee_per_contract,
                'fee_action': 'open',
                'open_fee_per_contract': open_fee_per_contract,
                'close_fee_per_contract': close_fee_per_contract,
                'roundtrip_fee_per_contract': roundtrip_fee_per_contract,
                'pnl': 0,
                'stress_loss': round(pos.stress_loss, 2),
                'open_margin': round(open_margin, 2),
                'one_contract_margin': item.get('one_contract_margin', np.nan),
                'gross_premium_cash': round(gross_premium_cash, 2),
                'net_premium_cash': round(net_premium_cash, 2),
                'signal_ref_price': ref_price,
                'execution_price_drift': price_drift,
                'premium_stress': exec_premium_stress,
                'theta_stress': exec_theta_stress,
                'premium_margin': exec_premium_margin,
                'signal_premium_stress': item.get('premium_stress', np.nan),
                'signal_theta_stress': item.get('theta_stress', np.nan),
                'signal_premium_margin': item.get('premium_margin', np.nan),
                'abs_delta': item.get('abs_delta', np.nan),
                'delta': item.get('delta', np.nan),
                'gamma': item.get('gamma', np.nan),
                'vega': item.get('vega', np.nan),
                'theta': item.get('theta', np.nan),
                'volume': item.get('volume', np.nan),
                'open_interest': item.get('open_interest', np.nan),
                'moneyness': item.get('moneyness', np.nan),
                'liquidity_score': item.get('liquidity_score', np.nan),
                'vol_regime': item.get('vol_regime', ''),
                'selection_score': item.get('selection_score', np.nan),
                'selection_rank': item.get('selection_rank', np.nan),
                'entry_atm_iv': item.get('entry_atm_iv', np.nan),
                'entry_iv_pct': item.get('entry_iv_pct', np.nan),
                'entry_iv_trend': item.get('entry_iv_trend', np.nan),
                'entry_rv_trend': item.get('entry_rv_trend', np.nan),
                'entry_iv_rv_spread': item.get('entry_iv_rv_spread', np.nan),
                'entry_iv_rv_ratio': item.get('entry_iv_rv_ratio', np.nan),
                'contract_iv': item.get('contract_iv', np.nan),
                'contract_iv_change_1d': item.get('contract_iv_change_1d', np.nan),
                'contract_iv_change_3d': item.get('contract_iv_change_3d', np.nan),
                'contract_iv_change_5d': item.get('contract_iv_change_5d', np.nan),
                'contract_iv_change_for_vega': item.get('contract_iv_change_for_vega', np.nan),
                'contract_iv_skew_to_atm': item.get('contract_iv_skew_to_atm', np.nan),
                'contract_skew_change_for_vega': item.get('contract_skew_change_for_vega', np.nan),
                'contract_price_change_1d': item.get('contract_price_change_1d', np.nan),
                'effective_margin_cap': item.get('effective_margin_cap', np.nan),
                'effective_strategy_margin_cap': item.get('effective_strategy_margin_cap', np.nan),
                'effective_product_margin_cap': item.get('effective_product_margin_cap', np.nan),
                'effective_product_side_margin_cap': item.get('effective_product_side_margin_cap', np.nan),
                'effective_bucket_margin_cap': item.get('effective_bucket_margin_cap', np.nan),
                'effective_corr_group_margin_cap': item.get('effective_corr_group_margin_cap', np.nan),
                'effective_stress_loss_cap': item.get('effective_stress_loss_cap', np.nan),
                'effective_bucket_stress_loss_cap': item.get('effective_bucket_stress_loss_cap', np.nan),
                'effective_product_side_stress_loss_cap': item.get('effective_product_side_stress_loss_cap', np.nan),
                'effective_corr_group_stress_loss_cap': item.get('effective_corr_group_stress_loss_cap', np.nan),
                'effective_contract_stress_loss_cap': item.get('effective_contract_stress_loss_cap', np.nan),
                'open_budget_risk_scale': item.get('open_budget_risk_scale', np.nan),
                'open_budget_brake_reason': item.get('open_budget_brake_reason', ''),
                'trend_state': item.get('trend_state', ''),
                'trend_score': item.get('trend_score', np.nan),
                'trend_confidence': item.get('trend_confidence', np.nan),
                'trend_range_position': item.get('trend_range_position', np.nan),
                'trend_range_pressure': item.get('trend_range_pressure', ''),
                'trend_role': item.get('trend_role', ''),
                'side_score_mult': item.get('side_score_mult', np.nan),
                'side_budget_mult': item.get('side_budget_mult', np.nan),
                'side_delta_cap': item.get('side_delta_cap', np.nan),
                'ladder_candidate_count': item.get('ladder_candidate_count', np.nan),
                'ladder_delta_gap': item.get('ladder_delta_gap', np.nan),
                'effective_s1_stress_max_qty': item.get('effective_s1_stress_max_qty', np.nan),
            })
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
            closed_groups = set()
            for pos in list(self.positions):
                if pos.role != 'sell' or pos not in self.positions:
                    continue
                if not is_exit_eligible(pos):
                    continue
                gid = pos.group_id
                if gid and gid in closed_groups:
                    continue
                if premium_stop_multiple > 0 and self._should_trigger_premium_stop(pos, product_iv_pcts):
                    self._close_group(pos, date_str, f'sl_{pos.strat.lower()}', fee, exec_time=exec_time)
                    if gid:
                        closed_groups.add(gid)
                    continue
                if take_profit_enabled:
                    tp = cfg.get('s1_tp', 0.40) if pos.strat == 'S1' else cfg.get('s3_tp', 0.30)
                    tp_fee = self._position_roundtrip_fee_per_side(pos, default=fee)
                    if pos.profit_pct(tp_fee) >= tp and pos.dte > cfg.get('tp_min_dte', 5):
                        self._close_group(pos, date_str, f'tp_{pos.strat.lower()}', fee, exec_time=exec_time)
                        if gid:
                            closed_groups.add(gid)

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
        cfg = self.config
        if not cfg.get('intraday_stop_liquidity_filter_enabled', True):
            return False
        multiple = float(cfg.get('premium_stop_multiple', 0.0) or 0.0)
        if multiple <= 0 or price <= 0:
            return False

        positions = pos_by_code.get(code, [])
        triggers_stop = any(
            pos.role == 'sell' and pos.open_price > 0 and price >= pos.open_price * multiple
            for pos in positions
        )
        if not triggers_stop:
            return False

        min_volume = float(cfg.get('intraday_stop_min_trade_volume', 3) or 0.0)
        volume_ratio = float(cfg.get('intraday_stop_min_group_volume_ratio', 0.10) or 0.0)
        group_qty = float(qty_by_code.get(code, 0) or 0.0)
        required_volume = max(min_volume, np.ceil(group_qty * volume_ratio))
        return float(volume or 0.0) < required_volume

    def _intraday_stop_threshold(self, code, pos_by_code):
        multiple = float(self.config.get('premium_stop_multiple', 0.0) or 0.0)
        if multiple <= 0:
            return np.nan
        thresholds = [
            float(pos.open_price) * multiple
            for pos in pos_by_code.get(code, [])
            if pos.role == 'sell' and float(getattr(pos, 'open_price', 0.0) or 0.0) > 0
        ]
        return min(thresholds) if thresholds else np.nan

    def _intraday_stop_required_volume(self, code, qty_by_code):
        min_volume = float(self.config.get('intraday_stop_min_trade_volume', 3) or 0.0)
        volume_ratio = float(self.config.get('intraday_stop_min_group_volume_ratio', 0.10) or 0.0)
        group_qty = float(qty_by_code.get(code, 0) or 0.0)
        return max(min_volume, np.ceil(group_qty * volume_ratio))

    def _prefilter_intraday_exit_codes_by_daily_high(self, date_str, exit_codes):
        """Skip minute stop scans when the daily high cannot reach any stop line."""
        if not exit_codes or not self.config.get('intraday_stop_daily_high_prefilter_enabled', True):
            return set(exit_codes or [])
        if self.config.get('take_profit_enabled', False):
            return set(exit_codes)

        multiple = float(self.config.get('premium_stop_multiple', 0.0) or 0.0)
        if multiple <= 0:
            return set()

        high_map = self.loader.get_daily_option_high_map(date_str)
        if not high_map:
            return set(exit_codes)

        code_set = set(exit_codes)
        positions_by_group = defaultdict(list)
        for pos in self.positions:
            if not getattr(pos, 'code', None) or pos.code not in code_set:
                continue
            gid = getattr(pos, 'group_id', None) or pos.code
            positions_by_group[gid].append(pos)

        keep_codes = set()
        for positions in positions_by_group.values():
            group_codes = {pos.code for pos in positions if getattr(pos, 'code', None)}
            group_may_stop = False
            for pos in positions:
                if pos.role != 'sell':
                    continue
                open_price = float(getattr(pos, 'open_price', 0.0) or 0.0)
                if open_price <= 0:
                    group_may_stop = True
                    break
                day_high = high_map.get(pos.code)
                if day_high is None or day_high <= 0:
                    group_may_stop = True
                    break
                if float(day_high) >= open_price * multiple:
                    group_may_stop = True
                    break
            if group_may_stop:
                keep_codes.update(group_codes)

        skipped_codes = code_set - keep_codes
        if skipped_codes:
            logger.debug(
                "  %s 日内止损预筛跳过 %d/%d 个持仓合约",
                date_str, len(skipped_codes), len(code_set),
            )
        return keep_codes

    def _confirm_intraday_stop_price(self, code, price, volume, tm, stop_pending, pos_by_code, qty_by_code):
        threshold = self._intraday_stop_threshold(code, pos_by_code)
        if not np.isfinite(threshold) or threshold <= 0 or price < threshold:
            revert_ratio = float(self.config.get('intraday_stop_confirmation_revert_ratio', 0.98) or 0.98)
            if np.isfinite(threshold) and threshold > 0 and price < threshold * revert_ratio:
                stop_pending.pop(code, None)
            return True

        if self._is_intraday_stop_price_illiquid(code, price, volume, pos_by_code, qty_by_code):
            # Keep it as a candidate only; a single thin print should not execute the stop.
            pass

        if not self.config.get('intraday_stop_confirmation_enabled', True):
            return not self._is_intraday_stop_price_illiquid(code, price, volume, pos_by_code, qty_by_code)

        now = pd.Timestamp(tm)
        max_minutes = float(self.config.get('intraday_stop_confirmation_max_minutes', 30) or 0.0)
        required_obs = max(1, int(self.config.get('intraday_stop_confirmation_observations', 2) or 1))
        required_volume = self._intraday_stop_required_volume(code, qty_by_code)
        cur_volume = float(volume or 0.0)

        pending = stop_pending.get(code)
        if pending:
            age_minutes = (now - pending['first_time']).total_seconds() / 60.0
            if max_minutes > 0 and age_minutes > max_minutes:
                pending = None
                stop_pending.pop(code, None)
        if not pending:
            stop_pending[code] = {
                'first_time': now,
                'observations': 1,
                'cum_volume': max(cur_volume, 0.0),
                'threshold': threshold,
                'max_price': price,
            }
            return False

        pending['observations'] += 1
        pending['cum_volume'] += max(cur_volume, 0.0)
        pending['threshold'] = min(float(pending.get('threshold', threshold)), threshold)
        pending['max_price'] = max(float(pending.get('max_price', price)), price)

        use_cum_volume = bool(self.config.get('intraday_stop_confirmation_use_cumulative_volume', True))
        if use_cum_volume:
            volume_ok = pending['cum_volume'] >= required_volume and cur_volume > 0
        else:
            volume_ok = cur_volume >= required_volume
        if pending['observations'] >= required_obs and volume_ok:
            stop_pending.pop(code, None)
            return True
        return False

    def _process_intraday_exits(self, minute_df, date_str):
        """盘中逐分钟监控止盈和Greeks超限，触发即按该分钟close成交。"""
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

        pos_by_code = defaultdict(list)
        for pos in eligible_positions:
            pos_by_code[pos.code].append(pos)
        qty_by_code = {
            code: sum(int(getattr(pos, 'n', 0) or 0) for pos in positions)
            for code, positions in pos_by_code.items()
        }

        pos_by_underlying = defaultdict(list)
        for pos in eligible_positions:
            if pos.underlying_code:
                pos_by_underlying[pos.underlying_code].append(pos)

        price_df = price_df.sort_values(['time', 'ths_code'])
        price_groups = {tm: grp for tm, grp in price_df.groupby('time')}
        time_points = sorted(price_groups.keys())
        if not time_points:
            return False

        monitor_times = self._sample_intraday_times(
            time_points,
            self.config.get('intraday_risk_interval', 15),
        )
        greek_refresh_interval = max(
            1,
            int(self.config.get(
                'intraday_greeks_refresh_interval',
                self.config.get('intraday_risk_interval', 15),
            ) or 15),
        )
        greek_refresh_times = set(self._sample_intraday_times(time_points, greek_refresh_interval))
        greek_refresh_times.add(time_points[-1])
        spot_groups = {}
        if self.config.get('intraday_refresh_spot_greeks_for_attribution', True):
            spot_df = self.loader.load_spot_day_minute(
                date_str,
                list(pos_by_underlying),
                time_list=greek_refresh_times,
            )
            if not spot_df.empty:
                spot_df = spot_df.sort_values(['time', 'underlying_code'])
                spot_groups = {tm: grp for tm, grp in spot_df.groupby('time')}
        stop_pending = {}
        if self.config.get('intraday_stop_confirmation_use_full_minutes', True):
            monitor_times = list(time_points)
        for tm in monitor_times:
            spot_grp = spot_groups.get(tm)
            if spot_grp is not None:
                for row in spot_grp.itertuples(index=False):
                    for pos in pos_by_underlying.get(row.underlying_code, []):
                        pos.cur_spot = float(row.spot)

            grp = price_groups.get(tm)
            if grp is not None:
                for row in grp.itertuples(index=False):
                    code = row.ths_code
                    price = float(row.close)
                    volume = float(getattr(row, 'volume', 0.0) or 0.0)
                    if not self._confirm_intraday_stop_price(
                        code, price, volume, tm, stop_pending, pos_by_code, qty_by_code
                    ):
                        continue
                    for pos in pos_by_code.get(code, []):
                        pos.cur_price = price

            if self.config.get('intraday_refresh_spot_greeks_for_attribution', True) and tm in greek_refresh_times:
                self._refresh_position_greeks()

            self._apply_exit_rules(
                date_str, fee, product_iv_pcts={},
                check_greeks=False, check_tp=True, check_expiry=False,
                exec_time=str(tm),
            )
            if not self.positions:
                break

        return True

    def _queue_new_opens(self, daily_df, date_str, product_pool, product_iv_pcts):
        """收盘后生成下一交易日待开仓指令。"""
        cfg = self.config
        nav = max(self._current_nav(), 1.0)
        self._start_s1_candidate_funnel(date_str, nav, product_pool)
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

        filtered_daily = daily_df[daily_df['product'].isin(product_pool)].copy()
        if filtered_daily.empty:
            self._bump_s1_funnel('skip_empty_filtered_daily')
            self._finish_s1_candidate_funnel(date_str, nav)
            return

        product_frames = {
            product: frame
            for product, frame in filtered_daily.groupby('product', sort=False)
            if not frame.empty
        }
        if not product_frames:
            self._bump_s1_funnel('skip_no_product_frames')
            self._finish_s1_candidate_funnel(date_str, nav)
            return
        self._bump_s1_funnel('loaded_products', len(product_frames))

        self._update_product_first_trade_dates(list(product_frames), date_str)
        if baseline_mode:
            sorted_products = sorted(product_frames)
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
        baseline_product_margin_per = (
            float(s1_cap or margin_cap or 0.0) / max(len(candidate_products), 1)
            if baseline_mode and cfg.get('s1_baseline_equal_weight_products', True)
            else None
        )
        self._bump_s1_funnel('candidate_products', len(candidate_products))
        open_product_expiries = {(p.product, p.expiry) for p in self.positions}
        open_s1_sell_sides = {
            (p.product, p.opt_type)
            for p in self.positions
            if p.strat == 'S1' and p.role == 'sell'
        }
        open_s3_sell_sides = {
            (p.product, p.opt_type)
            for p in self.positions
            if p.strat == 'S3' and p.role == 'sell'
        }
        open_s4_products = {
            p.product
            for p in self.positions
            if p.strat == 'S4'
        }

        for product in candidate_products:
            prod_df = product_frames.get(product)
            if prod_df.empty:
                self._bump_s1_funnel('skip_empty_product_frame')
                continue
            if not self._passes_product_entry_filters(product, date_str):
                self._bump_s1_funnel('skip_product_observation')
                continue
            self._bump_s1_funnel('product_entry_pass')

            open_expiries = should_open_new(
                prod_df,
                dte_target=cfg.get('dte_target', 35),
                dte_min=cfg.get('dte_min', 15),
                dte_max=cfg.get('dte_max', 90),
                mode=cfg.get('s1_expiry_mode', 'dte'),
                expiry_rank=cfg.get('s1_expiry_rank', 2),
            )
            if not open_expiries:
                self._bump_s1_funnel('skip_no_open_expiry')
                continue
            self._bump_s1_funnel('open_expiry_candidates', len(open_expiries))

            for exp in open_expiries:
                expiry_has_position = (product, exp) in open_product_expiries

                ef = prod_df[prod_df['expiry_date'] == exp]
                if ef.empty:
                    self._bump_s1_funnel('skip_empty_expiry_frame')
                    continue
                self._bump_s1_funnel('product_expiry_frames')

                iv_pct = product_iv_pcts.get(product, np.nan)
                iv_state = self._current_iv_state.get(product, {})
                if not baseline_mode and should_pause_open(iv_pct, iv_open_thr):
                    self._bump_s1_funnel('skip_iv_pause')
                    continue
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
                    continue
                iv_scale = 1.0 if baseline_mode else get_iv_scale(iv_pct, cfg.get('iv_threshold', 75))
                regime_mult = 1.0 if baseline_mode else self._product_margin_per_multiplier(product)
                if regime_mult <= 0:
                    self._bump_s1_funnel('skip_regime_budget_zero')
                    continue
                product_margin_per = (
                    baseline_product_margin_per
                    if baseline_product_margin_per is not None
                    else margin_per * regime_mult
                )

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
                    if cfg.get('s1_side_selection_enabled', False):
                        s1_side_items = self._select_s1_side_items(
                            ef, product, mult, mr, exchange, date_str,
                            iv_state=iv_state,
                        )
                    else:
                        s1_side_items = [(ot, None, {}) for ot in ['P', 'C']]
                    self._bump_s1_funnel('s1_selected_side_items', len(s1_side_items))
                    for ot, preselected_candidates, side_meta in s1_side_items:
                        if (
                            not cfg.get('s1_allow_add_same_side', False) and
                            (product, ot) in open_s1_sell_sides
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
                        if (product, ot) in open_s3_sell_sides:
                            continue
                        if self._is_reentry_blocked('S3', product, ot, date_str):
                            continue
                        s3_plan = self._get_reentry_plan('S3', product, ot, date_str)
                        self._try_open_s3(ef, product, ot, spot, mult, mr, exchange, exp,
                                          nav, product_margin_per, iv_scale, date_str,
                                          reentry_plan=s3_plan,
                                          margin_cap=margin_cap, strategy_cap=s3_cap)

                if cfg.get('enable_s4', True) and product not in open_s4_products:
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
        candidate_multiplier = 3
        if self.config.get('s1_forward_vega_filter_enabled', False):
            base_multiplier = int(self.config.get('s1_forward_vega_candidate_multiplier', 8) or 8)
            falling_multiplier = int(
                self.config.get('s1_forward_vega_falling_candidate_multiplier', 0) or 0
            )
            if self._s1_vol_regime_prefix(product) == 'falling' and falling_multiplier > 0:
                base_multiplier = max(base_multiplier, falling_multiplier)
            candidate_multiplier = max(candidate_multiplier, base_multiplier)
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
            use_stress_score=bool(self.config.get('s1_use_stress_score', False)),
            stress_spot_move_pct=float(self.config.get('s1_stress_spot_move_pct', 0.03) or 0.03),
            stress_iv_up_points=float(self.config.get('s1_stress_iv_up_points', 5.0) or 5.0),
            stress_premium_loss_multiple=float(
                self.config.get('s1_stress_premium_loss_multiple', 0.0) or 0.0
            ),
            gamma_penalty=float(self.config.get('s1_gamma_penalty', 0.0) or 0.0),
            vega_penalty=float(self.config.get('s1_vega_penalty', 0.0) or 0.0),
            ranking_mode=self.config.get('s1_ranking_mode', 'target_delta'),
            premium_stress_weight=float(self.config.get('s1_score_premium_stress_weight', 0.55) or 0.0),
            theta_stress_weight=float(self.config.get('s1_score_theta_stress_weight', 0.25) or 0.0),
            premium_margin_weight=float(self.config.get('s1_score_premium_margin_weight', 0.15) or 0.0),
            liquidity_weight=float(self.config.get('s1_score_liquidity_weight', 0.05) or 0.0),
            delta_weight=float(self.config.get('s1_score_delta_weight', 0.0) or 0.0),
            return_candidates=True,
            max_candidates=max_candidates * candidate_multiplier,
            exchange=exchange,
            product=product,
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
        self._bump_s1_funnel('open_candidates_considered', len(candidates))
        if baseline_mode:
            if max_candidates > 0:
                candidates = candidates.head(max_candidates)
        elif split_enabled and max_candidates > 1:
            if max_delta_gap > 0 and 'abs_delta' in candidates.columns:
                center_delta = float(candidates['abs_delta'].iloc[0])
                candidates = candidates[
                    (pd.to_numeric(candidates['abs_delta'], errors='coerce') - center_delta).abs() <= max_delta_gap
                ].copy()
            candidates = candidates.head(max_candidates)
        else:
            candidates = candidates.head(1)
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
        for rank, (_, c) in enumerate(candidates.iterrows(), start=1):
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
            pending_item = {
                'strat': 'S1', 'product': product, 'code': c['option_code'],
                'opt_type': ot, 'strike': c['strike'], 'ref_price': c['option_close'],
                'n': nn, 'mult': mult, 'expiry': exp, 'mr': mr, 'role': 'sell',
                'spot': c['spot_close'], 'exchange': exchange, 'group_id': group_id,
                'underlying_code': c.get('underlying_code'),
                'signal_date': date_str,
                'margin': m * nn,
                'cash_vega': new_greeks['cash_vega'],
                'cash_gamma': new_greeks['cash_gamma'],
                'one_contract_stress_loss': one_stress_loss,
                'stress_loss': total_stress_loss,
                'one_contract_margin': m,
                'gross_premium_cash': self._safe_float(c.get('option_close', np.nan), np.nan) * float(mult) * nn,
                'net_premium_cash': self._safe_float(c.get('net_premium_cash', np.nan), np.nan) * nn,
                'premium_stress': self._safe_float(c.get('premium_stress', np.nan), np.nan),
                'theta_stress': self._safe_float(c.get('theta_stress', np.nan), np.nan),
                'premium_margin': self._safe_float(c.get('premium_margin', np.nan), np.nan),
                'open_fee_per_contract': open_fee_per_contract,
                'close_fee_per_contract': close_fee_per_contract,
                'roundtrip_fee_per_contract': roundtrip_fee_per_contract,
                'liquidity_score': self._safe_float(c.get('liquidity_score', np.nan), np.nan),
                'volume': self._safe_float(c.get('volume', np.nan), np.nan),
                'open_interest': self._safe_float(c.get('open_interest', np.nan), np.nan),
                'moneyness': self._safe_float(c.get('moneyness', np.nan), np.nan),
                'abs_delta': self._safe_float(c.get('abs_delta', np.nan), np.nan),
                'delta': self._safe_float(c.get('delta', np.nan), np.nan),
                'gamma': self._safe_float(c.get('gamma', np.nan), np.nan),
                'vega': self._safe_float(c.get('vega', np.nan), np.nan),
                'theta': self._safe_float(c.get('theta', np.nan), np.nan),
                'selection_score': float(c.get('quality_score', np.nan)) if pd.notna(c.get('quality_score', np.nan)) else np.nan,
                'selection_rank': rank,
                'vol_regime': self._current_vol_regimes.get(product, ''),
                'entry_atm_iv': iv_state.get('atm_iv', np.nan),
                'entry_iv_pct': iv_state.get('iv_pct', np.nan),
                'entry_iv_trend': iv_state.get('iv_trend', np.nan),
                'entry_rv_trend': iv_state.get('rv_trend', np.nan),
                'entry_iv_rv_spread': iv_state.get('iv_rv_spread', np.nan),
                'entry_iv_rv_ratio': iv_state.get('iv_rv_ratio', np.nan),
                'contract_iv': c.get('contract_iv', np.nan),
                'contract_iv_change_1d': c.get('contract_iv_change_1d', np.nan),
                'contract_iv_change_3d': c.get('contract_iv_change_3d', np.nan),
                'contract_iv_change_5d': c.get('contract_iv_change_5d', np.nan),
                'contract_iv_change_for_vega': c.get('contract_iv_change_for_vega', np.nan),
                'contract_iv_skew_to_atm': c.get('contract_iv_skew_to_atm', np.nan),
                'contract_skew_change_for_vega': c.get('contract_skew_change_for_vega', np.nan),
                'contract_price_change_1d': c.get('contract_price_change_1d', np.nan),
                'trend_state': side_meta.get('trend_state', c.get('trend_state', '')),
                'trend_score': side_meta.get('trend_score', c.get('trend_score', np.nan)),
                'trend_confidence': side_meta.get('trend_confidence', c.get('trend_confidence', np.nan)),
                'trend_range_position': side_meta.get(
                    'trend_range_position',
                    c.get('trend_range_position', np.nan),
                ),
                'trend_range_pressure': side_meta.get(
                    'trend_range_pressure',
                    c.get('trend_range_pressure', ''),
                ),
                'trend_role': side_meta.get('trend_role', c.get('trend_role', '')),
                'side_score_mult': side_meta.get('score_mult', c.get('side_score_mult', np.nan)),
                'side_budget_mult': side_budget_mult,
                'side_delta_cap': side_meta.get('delta_cap', np.nan),
                'ladder_candidate_count': max_candidates,
                'ladder_delta_gap': max_delta_gap,
                'effective_s1_stress_max_qty': max_qty,
            }
            pending_item.update(self._pending_budget_fields(product_budget, effective_strategy_cap))
            pending_item['effective_s1_stress_budget_pct'] = stress_budget_pct
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
