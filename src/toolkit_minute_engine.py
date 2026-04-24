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
import json
import time
import logging
import argparse
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import warnings

# 抑制 py_vollib 的 Below Intrinsic 警告（深虚值期权正常现象）
warnings.filterwarnings('ignore', message='.*Below Intrinsic.*')
warnings.filterwarnings('ignore', message='.*Above Max Price.*')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toolkit.selector import select_bars_sql, select
from option_calc import calc_iv_single, calc_greeks_single, calc_iv_batch, RISK_FREE_RATE
from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy_by_otm, select_s3_sell_by_otm,
    select_s4,
    calc_s1_size, calc_s1_stress_size, calc_s3_size_v2, calc_s4_size,
    extract_atm_iv_series, calc_iv_percentile, calc_iv_rv_features, get_iv_scale,
    should_pause_open, should_close_expiry, should_open_new, can_reopen,
    should_allow_open_low_iv_product, check_margin_ok, calc_stats,
    DEFAULT_PARAMS,
)
from margin_model import estimate_margin, resolve_margin_ratio
from contract_provider import ContractInfo
from spot_provider import (
    build_underlying_alias_map,
    spot_tables_for_codes,
    map_alias_spot_frame,
    resolve_alias_value_map,
    build_pcp_spot_frame,
)
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
    build_code_filter_sql,
    iter_code_filter_sql,
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
from daily_aggregation import (
    attach_contract_columns,
    normalize_preloaded_daily_agg,
    aggregate_minute_daily,
    enrich_daily_with_spot_iv_delta,
)
from position_model import Position
from runtime_paths import BASE_DIR, OUTPUT_DIR, CONFIG_PATH, CACHE_DIR
from data_tables import OPTION_MINUTE_TABLE, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE
from result_output import write_backtest_outputs
from trading_calendar import (
    load_trading_dates_cache,
    save_trading_dates_cache,
    query_trading_dates,
    filter_trading_dates,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 合约属性管理
# ══════════════════════════════════════════════════════════════════════════════



class ToolkitDayLoader:
    """从 toolkit 批量拉取分钟数据并聚合"""

    def __init__(self, contract_info):
        self._ci = contract_info
        self._trading_dates = None
        # 批量缓存
        self._day_cache = {}  # 分钟明细：{date_str: DataFrame}
        self._daily_agg_cache = {}  # 日频聚合：{date_str: DataFrame}
        self._spot_daily_cache = {}  # {date_str: {underlying_code: last_close}}
        self._batch_size = 5

    def get_trading_dates(self, start_date=None, end_date=None):
        """获取交易日列表"""
        if self._trading_dates is None:
            self._trading_dates = load_trading_dates_cache(CACHE_DIR, logger=logger)
            if self._trading_dates is None:
                logger.info("获取交易日列表...")
                self._trading_dates = query_trading_dates(select_bars_sql)
                logger.info("  共 %d 个交易日", len(self._trading_dates))
                save_trading_dates_cache(CACHE_DIR, self._trading_dates, logger=logger)

        return filter_trading_dates(self._trading_dates, start_date=start_date, end_date=end_date)

    def preload_batch(self, dates, like_sql=None):
        """
        批量预加载多天的分钟数据到内存缓存。

        一条SQL拉取多天数据，按date分组存入 _day_cache。
        比逐天查询快 N 倍（N=天数）。

        Args:
            dates: 日期列表 ['2024-06-03', '2024-06-04', ...]
            like_sql: 品种过滤条件（如 "ths_code LIKE 'M%.DCE' OR ths_code LIKE 'CU%.SHF'"）
        """
        # 过滤已缓存的日期
        to_load = [d for d in dates if d not in self._day_cache]
        if not to_load:
            return

        # 分批加载（避免单次查询太大）
        chunk_size = self._batch_size
        for i in range(0, len(to_load), chunk_size):
            chunk = to_load[i:i + chunk_size]
            date_list = ", ".join(f"'{d}'" for d in chunk)
            where = f"date IN ({date_list})"
            if like_sql:
                where += f" AND ({like_sql})"

            query = f"""
                SELECT toString(date) as trade_date,
                       ths_code, time, open, high, low, close, volume, open_interest
                FROM option_hf_1min_non_ror
                WHERE {where}
            """
            t0 = time.time()
            df = select_bars_sql(query)
            elapsed = time.time() - t0

            if df is None or df.empty:
                for d in chunk:
                    self._day_cache[d] = pd.DataFrame()
                logger.debug("  批量加载 %d天 无数据 (%.1fs)", len(chunk), elapsed)
                continue

            # 类型转换
            for col in ['open', 'high', 'low', 'close', 'volume', 'open_interest']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            df = df[df['close'] > 0].copy()

            # 按日期分组存入缓存
            n_rows = len(df)
            for d in chunk:
                day_df = df[df['trade_date'] == d].drop(columns=['trade_date'], errors='ignore')
                self._day_cache[d] = day_df
            logger.debug("  批量加载 %d天 %d行 (%.1fs)", len(chunk), n_rows, elapsed)

    def load_day_minute(self, date_str, like_sql=None, code_list=None):
        """
        加载某天的分钟数据。优先从缓存读取，缓存未命中则单独查询。
        """
        if code_list is not None and not any(code_list):
            return pd.DataFrame()
        if like_sql is None and not code_list and date_str in self._day_cache:
            return self._day_cache[date_str].copy()

        # 缓存未命中，单独查询
        where = f"date = '{date_str}'"
        if code_list:
            code_sql = ", ".join(f"'{str(code)}'" for code in sorted({str(code) for code in code_list if code}))
            if code_sql:
                where += f" AND ths_code IN ({code_sql})"
        elif like_sql:
            where += f" AND ({like_sql})"
        query = f"""
            SELECT ths_code, time, open, high, low, close, volume, open_interest
            FROM option_hf_1min_non_ror
            WHERE {where}
        """
        df = select_bars_sql(query)
        if df is None or df.empty:
            return pd.DataFrame()

        for col in ['open', 'high', 'low', 'close', 'volume', 'open_interest']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        df = df[df['close'] > 0].copy()
        if like_sql is None and not code_list:
            self._day_cache[date_str] = df
        return df

    def load_spot_day_minute(self, date_str, underlying_codes):
        if not underlying_codes:
            return pd.DataFrame()

        alias_map = build_underlying_alias_map(underlying_codes)
        lookup_codes = sorted({alias for aliases in alias_map.values() for alias in aliases})
        if not lookup_codes:
            return pd.DataFrame()

        code_sql = ", ".join(f"'{code}'" for code in lookup_codes)
        frames = []
        for table_name in self._spot_tables_for_codes(lookup_codes):
            query = f"""
                SELECT ths_code, time, close
                FROM {table_name}
                WHERE date = '{date_str}'
                  AND ths_code IN ({code_sql})
                  AND toFloat64OrZero(close) > 0
            """
            part = select_bars_sql(query)
            if part is not None and not part.empty:
                frames.append(part)
        if not frames:
            return pd.DataFrame()

        return map_alias_spot_frame(
            pd.concat(frames, ignore_index=True),
            alias_map,
            lookup_col='ths_code',
            value_col='close',
            sort_cols=['time'],
        )

    def clear_cache(self, keep_dates=None):
        """清理缓存，释放内存。可选保留指定日期。"""
        if keep_dates:
            self._day_cache = {d: v for d, v in self._day_cache.items() if d in keep_dates}
            self._daily_agg_cache = {d: v for d, v in self._daily_agg_cache.items() if d in keep_dates}
            self._spot_daily_cache = {d: v for d, v in self._spot_daily_cache.items() if d in keep_dates}
        else:
            self._day_cache.clear()
            self._daily_agg_cache.clear()
            self._spot_daily_cache.clear()

    def _query_spot_daily_table(self, table_name, date_str, code_list_sql):
        query = f"""
            SELECT
                toString(date) as trade_date,
                ths_code,
                argMax(toFloat64OrZero(close), time) as last_close
            FROM {table_name}
            WHERE date = '{date_str}'
              AND ths_code IN ({code_list_sql})
              AND toFloat64OrZero(close) > 0
            GROUP BY date, ths_code
        """
        return select_bars_sql(query)

    def _spot_tables_for_codes(self, underlying_codes):
        return spot_tables_for_codes(underlying_codes, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE)

    def _get_spot_daily_close_map(self, date_str, underlying_codes):
        if not underlying_codes:
            return {}

        cache = self._spot_daily_cache.setdefault(date_str, {})
        missing = [code for code in sorted(set(underlying_codes)) if code and code not in cache]
        if missing:
            alias_map = build_underlying_alias_map(missing)
            lookup_codes = sorted({alias for aliases in alias_map.values() for alias in aliases})
            code_list_sql = ", ".join(f"'{code}'" for code in lookup_codes)
            frames = []
            for table_name in self._spot_tables_for_codes(lookup_codes):
                df = self._query_spot_daily_table(table_name, date_str, code_list_sql)
                if df is not None and not df.empty:
                    frames.append(df)
            if frames:
                merged = pd.concat(frames, ignore_index=True)
                merged['last_close'] = pd.to_numeric(merged['last_close'], errors='coerce')
                merged = merged[merged['last_close'].notna() & (merged['last_close'] > 0)]
                for _, row in merged.iterrows():
                    cache[str(row['ths_code'])] = float(row['last_close'])
            cache.update(resolve_alias_value_map(cache, alias_map))
            for code in missing:
                cache.setdefault(code, None)

        return {
            code: cache.get(code)
            for code in underlying_codes
            if cache.get(code) is not None and cache.get(code) > 0
        }

    def preload_daily_agg_batch(self, dates, like_sql, contract_info):
        """
        批量预加载多天的日频聚合数据（ClickHouse端聚合，不拉分钟明细）。

        一条SQL完成：每天每合约的收盘价、成交量、持仓量。
        比拉分钟明细再Python聚合快10倍+。
        """
        to_load = [d for d in dates if d not in self._daily_agg_cache]
        if not to_load:
            return

        date_list = ", ".join(f"'{d}'" for d in to_load)
        where = f"date IN ({date_list})"
        if like_sql:
            where += f" AND ({like_sql})"

        query = f"""
            SELECT
                toString(date) as trade_date,
                ths_code,
                argMax(toFloat64OrZero(close), time) as last_close,
                sum(toInt64OrZero(volume)) as total_volume,
                argMax(toInt64OrZero(open_interest), time) as last_oi
            FROM option_hf_1min_non_ror
            WHERE {where}
              AND toFloat64OrZero(close) > 0
            GROUP BY date, ths_code
        """
        t0 = time.time()
        df = select_bars_sql(query)
        elapsed = time.time() - t0

        if df is None or df.empty:
            for d in to_load:
                self._daily_agg_cache[d] = pd.DataFrame()
            logger.debug("  日频聚合 %d天 无数据 (%.1fs)", len(to_load), elapsed)
            return

        # 类型转换
        df['last_close'] = pd.to_numeric(df['last_close'], errors='coerce').fillna(0)
        df['total_volume'] = pd.to_numeric(df['total_volume'], errors='coerce').fillna(0)
        df['last_oi'] = pd.to_numeric(df['last_oi'], errors='coerce').fillna(0)

        df = attach_contract_columns(df, contract_info)

        # 过滤无效
        df = df[(df['strike'] > 0) & (df['option_type'] != '') & (df['last_close'] > 0)].copy()

        # 按日期分组存入缓存
        n_rows = len(df)
        for d in to_load:
            day_df = df[df['trade_date'] == d].copy()
            self._daily_agg_cache[d] = day_df
        logger.debug("  日频聚合 %d天 %d行 (%.1fs)", len(to_load), n_rows, elapsed)

    def get_daily_agg(self, date_str, contract_info):
        """
        获取某天的日频聚合数据，格式兼容 strategy_rules。
        优先从缓存读取。包含 spot_close（put-call parity）、IV、delta。
        """
        if date_str in self._daily_agg_cache:
            raw = self._daily_agg_cache[date_str]
        else:
            return pd.DataFrame()

        if raw.empty:
            return pd.DataFrame()

        df = normalize_preloaded_daily_agg(raw, date_str, contract_info)
        if df.empty:
            return df

        spot_map = self._get_spot_daily_close_map(
            date_str,
            [code for code in df['underlying_code'].dropna().tolist() if code]
        )
        return enrich_daily_with_spot_iv_delta(df, spot_map=spot_map, risk_free_rate=RISK_FREE_RATE)

    def aggregate_daily(self, minute_df, date_str):
        """
        从分钟数据聚合成日频DataFrame，格式兼容 strategy_rules 选腿函数。
        用 put-call parity 推算标的价格。
        """
        df = aggregate_minute_daily(minute_df, date_str, self._ci)
        if df.empty:
            return df

        spot_map = self._get_spot_daily_close_map(
            date_str,
            [code for code in df['underlying_code'].dropna().tolist() if code]
        )
        return enrich_daily_with_spot_iv_delta(df, spot_map=spot_map, risk_free_rate=RISK_FREE_RATE)


# ══════════════════════════════════════════════════════════════════════════════
# 主引擎
# ══════════════════════════════════════════════════════════════════════════════

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

        # 策略参数（与strategy_rules.py DEFAULT_PARAMS一致，此处显式声明便于查看）
        # DTE 30-45（次月合约），止盈50%不重开
        # 30品种时 margin_per 调低，避免保证金上限卡住太多品种

    def _load_config(self, path):
        merged = dict(DEFAULT_PARAMS)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    merged.update(cfg)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("config.json 读取失败: %s", exc)
        return merged

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
        return (str(strat), str(product))

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
        cooldown_days = max(int(self.config.get('cooldown_days_after_stop', 1)), 0)
        product = self._normalize_product_key(pos.product)
        hist = self._stop_history[product]
        hist.append(date_str)
        lookback_days = int(self.config.get('cooldown_repeat_lookback_days', 20) or 0)
        threshold = int(self.config.get('cooldown_repeat_threshold', 2) or 0)
        extra_days = int(self.config.get('cooldown_repeat_extra_days', 2) or 0)
        if lookback_days > 0 and threshold > 0 and extra_days > 0:
            cur_ts = pd.Timestamp(date_str)
            recent = [
                d for d in hist
                if (cur_ts - pd.Timestamp(d)).days <= lookback_days
            ]
            hist[:] = recent
            if len(recent) >= threshold:
                cooldown_days += extra_days * (len(recent) - threshold + 1)
        plan = {
            'earliest_date': self._shift_trading_date(date_str, cooldown_days),
            'delta_abs': abs(float(getattr(pos, 'cur_delta', 0.0) or 0.0)),
            'trigger_opt_type': str(getattr(pos, 'opt_type', '') or ''),
            'cooldown_days': cooldown_days,
        }
        spot = float(getattr(pos, 'cur_spot', 0.0) or 0.0)
        if spot > 0:
            plan['otm_pct'] = abs(1 - float(pos.strike) / spot) * 100.0
        self._reentry_plans[self._reentry_key(pos.strat, pos.product, pos.opt_type)] = plan

    def _product_iv_turns_lower(self, product):
        hist = self._iv_history.get(product)
        if not hist:
            return False
        ivs = list(hist.get('ivs', []))
        dates = list(hist.get('dates', []))
        if len(ivs) < 2 or len(dates) < 2:
            return False
        cur_iv = pd.to_numeric(pd.Series(ivs), errors='coerce').iloc[-1]
        prev_iv = pd.to_numeric(pd.Series(ivs), errors='coerce').iloc[-2]
        if pd.isna(cur_iv) or pd.isna(prev_iv):
            return False
        return float(cur_iv) < float(prev_iv)

    def _product_iv_not_falling(self, product):
        hist = self._iv_history.get(product)
        if not hist:
            return True
        ivs = list(hist.get('ivs', []))
        dates = list(hist.get('dates', []))
        if len(ivs) < 2 or len(dates) < 2:
            return True
        cur_iv = pd.to_numeric(pd.Series(ivs), errors='coerce').iloc[-1]
        prev_iv = pd.to_numeric(pd.Series(ivs), errors='coerce').iloc[-2]
        if pd.isna(cur_iv) or pd.isna(prev_iv):
            return True
        return float(cur_iv) >= float(prev_iv)

    def _reentry_requires_falling_regime(self, strat):
        cfg = self.config
        strat = str(strat or '').upper()
        if strat == 'S1':
            return bool(cfg.get('s1_reentry_require_falling_regime', True))
        if strat == 'S3':
            return bool(cfg.get('s3_reentry_require_falling_regime', False))
        return bool(cfg.get('reentry_require_falling_regime', False))

    def _reentry_requires_daily_iv_drop(self, strat):
        cfg = self.config
        strat = str(strat or '').upper()
        if strat == 'S1':
            return bool(cfg.get('s1_reentry_require_daily_iv_drop',
                                cfg.get('s1_risk_release_require_daily_iv_drop', False)))
        if strat == 'S3':
            return bool(cfg.get('s3_reentry_require_daily_iv_drop', True))
        return bool(cfg.get('reentry_require_daily_iv_drop', True))

    def _reentry_plan_blocks(self, strat, product, opt_type, date_str, plan=None, base_regime=None):
        plan = plan or self._reentry_plans.get(self._reentry_key(strat, product, opt_type))
        if not plan:
            return False
        if date_str < plan.get('earliest_date', date_str):
            return True
        if self._reentry_requires_daily_iv_drop(strat) and not self._product_iv_turns_lower(product):
            return True
        if self._reentry_requires_falling_regime(strat):
            if base_regime is None:
                base_regime = self._classify_product_vol_regime_base(
                    product,
                    self._current_iv_state.get(product, {}),
                )
            return base_regime != 'falling_vol_carry'
        return False

    def _should_trigger_premium_stop(self, pos, product_iv_pcts=None):
        multiple = float(self.config.get('premium_stop_multiple', 0.0) or 0.0)
        if multiple <= 0 or pos.cur_price < pos.open_price * multiple:
            return False
        require_daily_iv = bool(self.config.get('premium_stop_requires_daily_iv_non_decrease', True))
        if not require_daily_iv:
            return True
        if not product_iv_pcts or pos.product not in product_iv_pcts:
            return True
        return self._product_iv_not_falling(pos.product)

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
        hist = self._iv_history.get(product)
        if not hist:
            return np.nan
        ivs = pd.to_numeric(pd.Series(hist.get('ivs', [])), errors='coerce').dropna()
        if len(ivs) < 2:
            return np.nan
        lb = max(int(lookback or 2), 2)
        prev = ivs.iloc[-min(lb, len(ivs))]
        cur = ivs.iloc[-1]
        if pd.isna(cur) or pd.isna(prev):
            return np.nan
        return float(cur - prev)

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
            'contract_price': np.nan,
            'contract_price_change_1d': np.nan,
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

    def _has_active_reentry_plan(self, product, date_str, base_regime=None):
        product = self._normalize_product_key(product)
        for (strat, plan_product), plan in self._reentry_plans.items():
            if self._normalize_product_key(plan_product) != product:
                continue
            if self._reentry_plan_blocks(strat, product, None, date_str,
                                         plan=plan, base_regime=base_regime):
                return True
        return False

    def _classify_product_vol_regime_base(self, product, state):
        cfg = self.config
        iv_pct = state.get('iv_pct', np.nan)
        spread = state.get('iv_rv_spread', np.nan)
        ratio = state.get('iv_rv_ratio', np.nan)
        rv_trend = state.get('rv_trend', np.nan)
        iv_trend = state.get('iv_trend', np.nan)

        high_iv_trend = float(cfg.get('vol_regime_high_iv_trend', 0.03) or 0.03)
        high_rv_trend = float(cfg.get('vol_regime_high_rv_trend', 0.05) or 0.05)
        if pd.notna(iv_trend) and iv_trend >= high_iv_trend:
            return 'high_rising_vol'
        if pd.notna(rv_trend) and rv_trend >= high_rv_trend:
            return 'high_rising_vol'

        min_spread = float(cfg.get('vol_regime_min_iv_rv_spread', 0.02) or 0.0)
        min_ratio = float(cfg.get('vol_regime_min_iv_rv_ratio', 1.10) or 0.0)
        falling_iv_min = float(cfg.get('vol_regime_falling_iv_pct_min', 25) or 0.0)
        falling_iv_max = float(cfg.get('vol_regime_falling_iv_pct_max', 85) or 100.0)
        falling_iv_trend = float(cfg.get('vol_regime_falling_iv_trend', -0.01) or -0.01)
        falling_rv_max = float(cfg.get('vol_regime_falling_rv_trend_max', 0.01) or 0.0)
        if (
            pd.notna(iv_pct) and falling_iv_min <= float(iv_pct) <= falling_iv_max and
            pd.notna(spread) and spread >= min_spread and
            pd.notna(ratio) and ratio >= min_ratio and
            pd.notna(iv_trend) and iv_trend <= falling_iv_trend and
            (pd.isna(rv_trend) or rv_trend <= falling_rv_max)
        ):
            return 'falling_vol_carry'

        high_iv_pct = float(cfg.get('vol_regime_high_iv_pct', 75) or 75)
        if pd.notna(iv_pct) and iv_pct >= high_iv_pct:
            return 'high_rising_vol'

        low_iv_pct = float(cfg.get('vol_regime_low_iv_pct', 45) or 45)
        max_rv_trend = float(cfg.get('vol_regime_max_low_rv_trend', 0.02) or 0.0)
        max_iv_trend = float(cfg.get('vol_regime_max_low_iv_trend', 0.00) or 0.0)
        if (
            pd.notna(iv_pct) and iv_pct <= low_iv_pct and
            pd.notna(spread) and spread >= min_spread and
            pd.notna(ratio) and ratio >= min_ratio and
            (pd.isna(rv_trend) or rv_trend <= max_rv_trend) and
            (pd.isna(iv_trend) or iv_trend <= max_iv_trend)
        ):
            return 'low_stable_vol'

        return 'normal_vol'

    def _classify_product_vol_regime(self, product, state, date_str):
        base_regime = self._classify_product_vol_regime_base(product, state)
        if self._has_active_reentry_plan(product, date_str, base_regime=base_regime):
            return 'post_stop_cooldown'
        return base_regime

    def _is_structural_low_iv_product(self, product, state=None):
        cfg = self.config
        product = self._normalize_product_key(product)
        allowed = {
            self._normalize_product_key(p)
            for p in cfg.get('low_iv_allowed_products', [])
            if str(p).strip()
        }
        if product in allowed:
            return True
        if not cfg.get('low_iv_structural_auto_enabled', False):
            return False

        hist = self._iv_history.get(product)
        if not hist:
            return False
        ivs = pd.to_numeric(pd.Series(hist.get('ivs', [])), errors='coerce').dropna()
        min_history = int(cfg.get('low_iv_structural_min_history', 120) or 0)
        if len(ivs) < max(min_history, 20):
            return False
        window = min(len(ivs), int(cfg.get('iv_window', 252) or 252))
        recent = ivs.iloc[-window:]
        median_iv = float(recent.median())
        iv_std = float(recent.std(ddof=0))
        max_median_iv = float(cfg.get('low_iv_structural_max_median_iv', 0.24) or 0.0)
        max_iv_std = float(cfg.get('low_iv_structural_max_iv_std', 0.08) or 0.0)
        if max_median_iv > 0 and median_iv > max_median_iv:
            return False
        if max_iv_std > 0 and iv_std > max_iv_std:
            return False

        state = state or self._current_iv_state.get(product, {})
        max_current_iv_pct = cfg.get('low_iv_structural_max_current_iv_pct', None)
        if max_current_iv_pct is not None:
            iv_pct = state.get('iv_pct', np.nan)
            if pd.isna(iv_pct) or float(iv_pct) > float(max_current_iv_pct):
                return False
        spread = state.get('iv_rv_spread', np.nan)
        ratio = state.get('iv_rv_ratio', np.nan)
        min_spread = float(cfg.get('low_iv_min_iv_rv_spread', 0.02) or 0.0)
        min_ratio = float(cfg.get('low_iv_min_iv_rv_ratio', 1.10) or 0.0)
        if pd.isna(spread) or spread < min_spread:
            return False
        if pd.isna(ratio) or ratio < min_ratio:
            return False
        return True

    def _refresh_vol_regime_state(self, date_str):
        regimes = {}
        for product, state in self._current_iv_state.items():
            regimes[product] = self._classify_product_vol_regime(product, state, date_str)
            state['is_structural_low_iv'] = self._is_structural_low_iv_product(product, state)
        counts = Counter(regimes.values())
        active = sum(counts.get(k, 0) for k in ('falling_vol_carry', 'low_stable_vol', 'normal_vol', 'high_rising_vol'))
        high_ratio = counts.get('high_rising_vol', 0) / active if active else 0.0
        low_ratio = counts.get('low_stable_vol', 0) / active if active else 0.0
        falling_ratio = counts.get('falling_vol_carry', 0) / active if active else 0.0
        cfg = self.config
        count_post_stop_as_high = bool(cfg.get('vol_regime_count_post_stop_as_high', False))
        if (
            count_post_stop_as_high and
            counts.get('post_stop_cooldown', 0) >= int(cfg.get('vol_regime_portfolio_stop_count', 3) or 3)
        ):
            portfolio_regime = 'high_rising_vol'
        elif high_ratio >= float(cfg.get('vol_regime_portfolio_high_ratio', 0.25) or 0.25):
            portfolio_regime = 'high_rising_vol'
        elif falling_ratio >= float(cfg.get('vol_regime_portfolio_falling_ratio', 0.25) or 0.25):
            portfolio_regime = 'falling_vol_carry'
        elif active > 0 and low_ratio >= float(cfg.get('vol_regime_portfolio_low_ratio', 0.50) or 0.50) and counts.get('high_rising_vol', 0) == 0:
            portfolio_regime = 'low_stable_vol'
        else:
            portfolio_regime = 'normal_vol'
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
        cfg = self.config
        if not cfg.get('vol_regime_sizing_enabled', False):
            return 1.0
        regime = self._current_vol_regimes.get(product, 'normal_vol')
        structural = self._is_structural_low_iv_product(product)
        structural_requires_low = bool(cfg.get('low_iv_structural_require_low_stable', True))
        if regime == 'falling_vol_carry':
            return float(cfg.get('s1_falling_vol_margin_per_mult', 1.50) or 1.0)
        if regime == 'low_stable_vol':
            mult = float(cfg.get('vol_regime_low_margin_per_mult', 1.12) or 1.0)
            if structural:
                structural_mult = float(cfg.get('low_iv_structural_margin_per_mult', 1.25) or mult)
                mult = max(mult, structural_mult)
            return mult
        if regime == 'high_rising_vol':
            return float(cfg.get('vol_regime_high_margin_per_mult', 0.30) or 1.0)
        if regime == 'post_stop_cooldown':
            return float(cfg.get('vol_regime_post_stop_margin_per_mult', 0.0) or 0.0)
        mult = float(cfg.get('vol_regime_normal_margin_per_mult', 1.0) or 1.0)
        if structural and not structural_requires_low:
            structural_mult = float(cfg.get('low_iv_structural_margin_per_mult', 1.25) or mult)
            mult = max(mult, structural_mult)
        return mult

    def _passes_s1_falling_framework_entry(self, product, iv_state):
        cfg = self.config
        if not cfg.get('s1_falling_framework_enabled', False):
            return True
        spread = iv_state.get('iv_rv_spread', np.nan)
        ratio = iv_state.get('iv_rv_ratio', np.nan)
        rv_trend = iv_state.get('rv_trend', np.nan)
        iv_trend = iv_state.get('iv_trend', np.nan)
        min_spread = float(cfg.get('vol_regime_min_iv_rv_spread', 0.02) or 0.0)
        min_ratio = float(cfg.get('vol_regime_min_iv_rv_ratio', 1.10) or 0.0)
        max_rv_trend = float(cfg.get('s1_entry_max_rv_trend', cfg.get('vol_regime_max_low_rv_trend', 0.02)) or 0.0)
        max_iv_trend = float(cfg.get('s1_entry_max_iv_trend', 0.0) or 0.0)
        if pd.isna(spread) or spread < min_spread:
            return False
        if pd.isna(ratio) or ratio < min_ratio:
            return False
        if pd.notna(rv_trend) and rv_trend > max_rv_trend:
            return False
        if pd.notna(iv_trend) and iv_trend > max_iv_trend:
            return False
        if self._current_vol_regimes.get(product) in ('high_rising_vol', 'post_stop_cooldown'):
            return False
        if not self._passes_s1_risk_release_entry(product, iv_state):
            return False
        return True

    def _passes_s1_risk_release_entry(self, product, iv_state):
        cfg = self.config
        if not cfg.get('s1_require_risk_release_entry', False):
            return True

        regime = self._current_vol_regimes.get(product)
        if regime in ('high_rising_vol', 'post_stop_cooldown'):
            return False

        iv_pct = iv_state.get('iv_pct', np.nan)
        spread = iv_state.get('iv_rv_spread', np.nan)
        ratio = iv_state.get('iv_rv_ratio', np.nan)
        rv_trend = iv_state.get('rv_trend', np.nan)
        iv_trend = iv_state.get('iv_trend', np.nan)
        allow_structural_low = bool(cfg.get('s1_risk_release_allow_structural_low', False))
        is_structural_low = bool(iv_state.get('is_structural_low_iv', False))

        if cfg.get('s1_risk_release_require_falling_regime', False):
            if regime != 'falling_vol_carry':
                if not (allow_structural_low and is_structural_low and regime == 'low_stable_vol'):
                    return False

        min_spread = float(cfg.get('s1_risk_release_min_iv_rv_spread',
                                   cfg.get('vol_regime_min_iv_rv_spread', 0.02)) or 0.0)
        min_ratio = float(cfg.get('s1_risk_release_min_iv_rv_ratio',
                                  cfg.get('vol_regime_min_iv_rv_ratio', 1.10)) or 0.0)
        if pd.isna(spread) or float(spread) < min_spread:
            return False
        if pd.isna(ratio) or float(ratio) < min_ratio:
            return False

        min_iv_pct = cfg.get('s1_risk_release_min_iv_pct', None)
        max_iv_pct = cfg.get('s1_risk_release_max_iv_pct', None)
        if min_iv_pct is not None and not (allow_structural_low and is_structural_low):
            if pd.isna(iv_pct) or float(iv_pct) < float(min_iv_pct):
                return False
        if max_iv_pct is not None:
            if pd.isna(iv_pct) or float(iv_pct) > float(max_iv_pct):
                return False

        max_iv_trend = float(cfg.get('s1_risk_release_max_iv_trend', -0.005) or 0.0)
        if pd.isna(iv_trend) or float(iv_trend) > max_iv_trend:
            return False
        if bool(cfg.get('s1_risk_release_require_daily_iv_drop', True)):
            if not self._product_iv_turns_lower(product):
                return False

        require_rv_trend = bool(cfg.get('s1_risk_release_require_rv_trend', True))
        max_rv_trend = float(cfg.get('s1_risk_release_max_rv_trend',
                                     cfg.get('s1_entry_max_rv_trend', 0.0)) or 0.0)
        if pd.isna(rv_trend):
            return not require_rv_trend
        if float(rv_trend) > max_rv_trend:
            return False
        return True

    def _candidate_cash_greeks(self, row, opt_type, mult, qty, role='sell'):
        sign = 1.0 if role in ('buy', 'protect') else -1.0
        spot = float(row.get('spot_close', 0.0) or 0.0)
        vega = float(row.get('vega', 0.0) or 0.0)
        gamma = float(row.get('gamma', 0.0) or 0.0)
        return {
            'cash_vega': sign * vega * float(mult) * float(qty),
            'cash_gamma': sign * gamma * float(mult) * float(qty) * spot * spot,
        }

    def _get_open_greek_state(self, include_pending=True):
        state = {
            'cash_delta': 0.0,
            'cash_vega': 0.0,
            'cash_gamma': 0.0,
            'bucket_vega': defaultdict(float),
            'bucket_gamma': defaultdict(float),
        }
        for pos in self.positions:
            bucket = self._get_product_bucket(pos.product)
            cd = pos.cash_delta()
            cv = pos.cash_vega()
            cg = pos.cash_gamma()
            state['cash_delta'] += cd
            state['cash_vega'] += cv
            state['cash_gamma'] += cg
            state['bucket_vega'][bucket] += cv
            state['bucket_gamma'][bucket] += cg
        if include_pending:
            for item in self._pending_opens:
                bucket = self._get_product_bucket(item.get('product', ''))
                cv = float(item.get('cash_vega', 0.0) or 0.0)
                cg = float(item.get('cash_gamma', 0.0) or 0.0)
                state['cash_vega'] += cv
                state['cash_gamma'] += cg
                state['bucket_vega'][bucket] += cv
                state['bucket_gamma'][bucket] += cg
        return state

    def _get_open_stress_loss_state(self, include_pending=True):
        state = {
            'stress_loss': 0.0,
            'bucket_stress_loss': defaultdict(float),
        }
        for pos in self.positions:
            loss = float(getattr(pos, 'stress_loss', 0.0) or 0.0)
            bucket = self._get_product_bucket(pos.product)
            state['stress_loss'] += loss
            state['bucket_stress_loss'][bucket] += loss
        if include_pending:
            for item in self._pending_opens:
                if item.get('role') != 'sell':
                    continue
                one_loss = float(item.get('one_contract_stress_loss', 0.0) or 0.0)
                loss = float(item.get('stress_loss', one_loss * float(item.get('n', 0) or 0)) or 0.0)
                bucket = self._get_product_bucket(item.get('product', ''))
                state['stress_loss'] += loss
                state['bucket_stress_loss'][bucket] += loss
        return state

    def _passes_greek_budget(self, product, nav, new_cash_vega=0.0,
                             new_cash_gamma=0.0, include_pending=True):
        cfg = self.config
        if not np.isfinite(nav) or nav <= 0:
            return False
        state = self._get_open_greek_state(include_pending=include_pending)
        vega_cap = float(cfg.get('portfolio_cash_vega_cap', 0.0) or 0.0)
        gamma_cap = float(cfg.get('portfolio_cash_gamma_cap', 0.0) or 0.0)
        if vega_cap > 0 and abs(state['cash_vega'] + new_cash_vega) / nav > vega_cap:
            return False
        if gamma_cap > 0 and abs(state['cash_gamma'] + new_cash_gamma) / nav > gamma_cap:
            return False

        bucket = self._get_product_bucket(product)
        bucket_vega_cap = float(cfg.get('portfolio_bucket_cash_vega_cap', 0.0) or 0.0)
        bucket_gamma_cap = float(cfg.get('portfolio_bucket_cash_gamma_cap', 0.0) or 0.0)
        if bucket_vega_cap > 0 and abs(state['bucket_vega'].get(bucket, 0.0) + new_cash_vega) / nav > bucket_vega_cap:
            return False
        if bucket_gamma_cap > 0 and abs(state['bucket_gamma'].get(bucket, 0.0) + new_cash_gamma) / nav > bucket_gamma_cap:
            return False
        return True

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
        cfg = self.config
        lookback_days = int(cfg.get('portfolio_stop_cluster_lookback_days', 5) or 0)
        if lookback_days <= 0:
            return 0
        if date_str is None:
            date_str = self._current_date_str
        if not date_str:
            return 0
        cur_ts = pd.Timestamp(date_str)
        count = 0
        for dates in self._stop_history.values():
            for stop_date in dates:
                try:
                    if (cur_ts - pd.Timestamp(stop_date)).days <= lookback_days:
                        count += 1
                except (TypeError, ValueError):
                    continue
        return count

    def _pending_budget_fields(self, strategy_cap):
        budget = self._current_open_budget or self._get_effective_open_budget()
        return pending_budget_fields(budget, strategy_cap)

    def _execution_budget_for_item(self, item):
        current = self._current_open_budget or self._get_effective_open_budget()
        return execution_budget_for_item(item, current, self.config)

    def _passes_stress_budget(self, nav, new_cash_vega=0.0, new_cash_gamma=0.0,
                              product=None, new_stress_loss=0.0, budget=None,
                              include_pending=True):
        cfg = self.config
        if not cfg.get('portfolio_stress_gate_enabled', False):
            return True
        if not np.isfinite(nav) or nav <= 0:
            return False
        budget = budget or self._current_open_budget or self._get_effective_open_budget()
        explicit_state = self._get_open_stress_loss_state(include_pending=include_pending)
        loss_cap = float(budget.get('portfolio_stress_loss_cap',
                                    cfg.get('portfolio_stress_loss_cap', 0.03)) or 0.0)
        if loss_cap > 0 and new_stress_loss > 0:
            if (explicit_state['stress_loss'] + float(new_stress_loss)) / nav > loss_cap:
                return False
        bucket_cap = float(budget.get('portfolio_bucket_stress_loss_cap',
                                      cfg.get('portfolio_bucket_stress_loss_cap', 0.0)) or 0.0)
        if bucket_cap > 0 and product is not None and new_stress_loss > 0:
            bucket = self._get_product_bucket(product)
            if (explicit_state['bucket_stress_loss'].get(bucket, 0.0) + float(new_stress_loss)) / nav > bucket_cap:
                return False
        state = self._get_open_greek_state(include_pending=include_pending)
        move = float(cfg.get('portfolio_stress_spot_move_pct', 0.03) or 0.0)
        iv_up_points = float(cfg.get('portfolio_stress_iv_up_points', 5.0) or 0.0)
        cash_delta = state['cash_delta']
        cash_gamma = state['cash_gamma'] + new_cash_gamma
        cash_vega = state['cash_vega'] + new_cash_vega
        stress_pnl = -abs(cash_delta) * move + 0.5 * cash_gamma * move * move + cash_vega * iv_up_points
        stress_loss = max(0.0, -float(stress_pnl))
        return loss_cap <= 0 or stress_loss / nav <= loss_cap

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
        warmup_days = self.config.get('iv_window', 252)
        all_dates = self.loader.get_trading_dates()
        warmup_dates = [d for d in all_dates if d < dates[0]][-warmup_days:]
        if warmup_dates:
            cache_path = os.path.join(OUTPUT_DIR, 'iv_warmup_cache.json')
            cached_products = set()
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r') as f:
                        cache = json.load(f)
                    cache_end = cache.get('_meta', {}).get('end_date', '')
                    if cache_end >= warmup_dates[-1]:
                        for product, data in cache.items():
                            if product == '_meta':
                                continue
                            if product in product_pool:
                                hist = self._iv_history[product]
                                hist['dates'] = data['dates']
                                hist['ivs'] = data['ivs']
                                spot_hist = self._spot_history[product]
                                spot_hist['dates'] = list(data.get('spot_dates', []))
                                spot_hist['spots'] = list(data.get('spots', []))
                                cached_products.add(product)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("IV缓存加载失败: %s", exc)

            missing = set(product_pool - cached_products)
            if self.config.get('portfolio_dynamic_corr_control_enabled', True):
                missing_spot = {
                    product for product in product_pool
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
                # 更新缓存（合并已有+新增）
                try:
                    new_cache = {'_meta': {'end_date': warmup_dates[-1]}}
                    for product, hist in self._iv_history.items():
                        if hist['ivs']:
                            spot_hist = self._spot_history.get(product, {'dates': [], 'spots': []})
                            new_cache[product] = {
                                'dates': hist['dates'],
                                'ivs': hist['ivs'],
                                'spot_dates': spot_hist.get('dates', []),
                                'spots': spot_hist.get('spots', []),
                            }
                    with open(cache_path, 'w') as f:
                        json.dump(new_cache, f)
                    logger.info("  IV缓存已更新: %d个品种", len(new_cache) - 1)
                except OSError:
                    pass
            else:
                logger.info("IV预热: 从缓存加载, %d个品种全部命中", len(cached_products))

        # 主循环（批量预加载分钟数据）
        batch_size = 5  # 每次预加载5天
        for di, date_str in enumerate(dates):
            if di % 5 == 0:
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

            # Phase 0: 执行昨日待开仓（需要分钟数据做T+1 VWAP）
            if self._pending_opens:
                pending_codes = {item['code'] for item in self._pending_opens if item.get('code')}
                minute_df = self.loader.load_day_minute(date_str, code_list=pending_codes)
            if self._pending_opens:
                if not minute_df.empty:
                    self._execute_pending_opens(minute_df, date_str)

            intraday_exit_done = False
            if self.positions:
                held_codes = {pos.code for pos in self.positions if pos.code}
                exit_minute_df = self.loader.load_day_minute(date_str, code_list=held_codes)
                if not exit_minute_df.empty:
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
        for pos in self.positions:
            if pos.role == 'sell':
                yield self._normalize_product_key(pos.product), float(pos.cur_margin())
        if not include_pending:
            return
        for item in self._pending_opens:
            if item.get('role') == 'sell':
                yield self._normalize_product_key(item.get('product', '')), float(item.get('margin', 0.0) or 0.0)

    def _get_open_sell_margin_total(self, strat=None, include_pending=True):
        total = 0.0
        for pos in self.positions:
            if pos.role == 'sell' and (strat is None or pos.strat == strat):
                total += float(pos.cur_margin())
        if not include_pending:
            return total
        for item in self._pending_opens:
            if item.get('role') == 'sell' and (strat is None or item.get('strat') == strat):
                total += float(item.get('margin', 0.0) or 0.0)
        return total

    def _get_product_return_series(self, product, current_date=None):
        product = self._normalize_product_key(product)
        hist = self._spot_history.get(product, {})
        dates = hist.get('dates', [])
        spots = hist.get('spots', [])
        if not dates or not spots:
            return pd.Series(dtype=float)
        series = pd.Series(spots, index=pd.Index(dates, dtype=object), dtype=float)
        series = series[~series.index.duplicated(keep='last')]
        series = series.replace([np.inf, -np.inf], np.nan).dropna()
        series = series[series > 0]
        if current_date is not None:
            series = series[series.index <= current_date]
        if len(series) < 2:
            return pd.Series(dtype=float)
        returns = np.log(series).diff().dropna()
        return returns

    def _get_recent_product_corr(self, product, peer_product, current_date):
        cfg = self.config
        window = int(cfg.get('portfolio_corr_window', 60) or 0)
        min_periods = int(cfg.get('portfolio_corr_min_periods', 20) or 0)
        if window <= 1:
            return np.nan
        left = self._get_product_return_series(product, current_date=current_date).tail(window)
        right = self._get_product_return_series(peer_product, current_date=current_date).tail(window)
        if left.empty or right.empty:
            return np.nan
        aligned = pd.concat([left.rename('x'), right.rename('y')], axis=1, join='inner').dropna()
        if len(aligned) < max(min_periods, 2):
            return np.nan
        return float(aligned['x'].corr(aligned['y']))

    def _get_open_concentration_state(self, include_pending=True):
        state = {
            'product_margin': defaultdict(float),
            'bucket_margin': defaultdict(float),
            'bucket_products': defaultdict(set),
            'corr_products': defaultdict(set),
        }
        for product, margin in self._iter_open_sell_exposures(include_pending=include_pending):
            if not product:
                continue
            bucket = self._get_product_bucket(product)
            corr_group = self._get_product_corr_group(product)
            state['product_margin'][product] += margin
            state['bucket_margin'][bucket] += margin
            state['bucket_products'][bucket].add(product)
            state['corr_products'][corr_group].add(product)
        return state

    def _passes_portfolio_construction(self, product, nav, new_margin, date_str=None,
                                       new_cash_vega=0.0, new_cash_gamma=0.0,
                                       new_stress_loss=0.0, budget=None,
                                       include_pending=True):
        cfg = self.config
        budget = budget or self._current_open_budget or self._get_effective_open_budget()
        if not cfg.get('portfolio_construction_enabled', True):
            return (
                self._passes_greek_budget(
                    product, nav, new_cash_vega, new_cash_gamma,
                    include_pending=include_pending,
                ) and
                self._passes_stress_budget(
                    nav, new_cash_vega, new_cash_gamma, product, new_stress_loss,
                    budget=budget, include_pending=include_pending,
                )
            )
        if not np.isfinite(nav) or nav <= 0:
            return False

        product = self._normalize_product_key(product)
        state = self._get_open_concentration_state(include_pending=include_pending)
        product_cap = float(
            budget.get('product_margin_cap', cfg.get('portfolio_product_margin_cap', 0.08)) or 0.0
        )
        bucket = self._get_product_bucket(product)
        corr_group = self._get_product_corr_group(product)

        if product_cap > 0:
            if (state['product_margin'].get(product, 0.0) + new_margin) / nav > product_cap:
                return False

        if cfg.get('portfolio_bucket_control_enabled', True):
            bucket_cap = float(
                budget.get('bucket_margin_cap', cfg.get('portfolio_bucket_margin_cap', 0.18)) or 0.0
            )
            bucket_max_active = int(cfg.get('portfolio_bucket_max_active_products', 3) or 0)
            bucket_products = state['bucket_products'].get(bucket, set())
            if bucket_max_active > 0 and product not in bucket_products and len(bucket_products) >= bucket_max_active:
                return False
            if bucket_cap > 0:
                if (state['bucket_margin'].get(bucket, 0.0) + new_margin) / nav > bucket_cap:
                    return False

        if cfg.get('portfolio_corr_control_enabled', True):
            corr_max_active = int(cfg.get('portfolio_corr_group_max_active_products', 2) or 0)
            corr_products = state['corr_products'].get(corr_group, set())
            if corr_max_active > 0 and product not in corr_products and len(corr_products) >= corr_max_active:
                return False
            if cfg.get('portfolio_dynamic_corr_control_enabled', True) and date_str is not None:
                corr_threshold = float(cfg.get('portfolio_corr_threshold', 0.70) or 0.0)
                max_high_corr_peers = int(cfg.get('portfolio_corr_max_high_corr_peers', 1) or 0)
                if corr_threshold > 0 and max_high_corr_peers >= 0:
                    high_corr_peers = 0
                    for peer in corr_products:
                        if peer == product:
                            continue
                        corr = self._get_recent_product_corr(product, peer, current_date=date_str)
                        if pd.notna(corr) and corr >= corr_threshold:
                            high_corr_peers += 1
                    if high_corr_peers > max_high_corr_peers:
                        return False

        return (
            self._passes_greek_budget(
                product, nav, new_cash_vega, new_cash_gamma,
                include_pending=include_pending,
            ) and
            self._passes_stress_budget(
                nav, new_cash_vega, new_cash_gamma, product, new_stress_loss,
                budget=budget, include_pending=include_pending,
            )
        )

    def _diversify_product_order(self, products):
        if not self.config.get('portfolio_bucket_round_robin', True):
            return list(products)
        bucket_products = defaultdict(list)
        bucket_order = []
        for product in products:
            bucket = self._get_product_bucket(product)
            if bucket not in bucket_products:
                bucket_order.append(bucket)
            bucket_products[bucket].append(product)
        diversified = []
        has_remaining = True
        while has_remaining:
            has_remaining = False
            for bucket in bucket_order:
                if bucket_products[bucket]:
                    diversified.append(bucket_products[bucket].pop(0))
                    has_remaining = True
        return diversified

    def _prioritize_products_by_regime(self, products):
        if not self.config.get('s1_falling_framework_enabled', False):
            return list(products)
        priority = {
            'falling_vol_carry': 0,
            'low_stable_vol': 1,
            'normal_vol': 2,
            'high_rising_vol': 3,
            'post_stop_cooldown': 4,
        }
        order = {p: i for i, p in enumerate(products)}
        return sorted(
            list(products),
            key=lambda p: (priority.get(self._current_vol_regimes.get(p, 'normal_vol'), 2), order.get(p, 0)),
        )

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
        normalized_pool = normalize_product_pool(product_pool)
        if not normalized_pool or not warmup_dates:
            return []

        max_dte = int(self.config.get('dte_max', 90) or 90)
        warmup_start = pd.Timestamp(warmup_dates[0]).date()
        max_expiry = pd.Timestamp(warmup_dates[-1]).date() + timedelta(days=max(max_dte, 90))
        cache_key = (normalized_pool, warmup_start.isoformat(), max_expiry.isoformat())
        cached_codes = self._warmup_contract_sql_cache.get(cache_key)
        if cached_codes is not None:
            return cached_codes

        eligible_codes = []
        for product in normalized_pool:
            for code in self.ci.get_product_codes(product):
                info = self.ci.lookup(code) or {}
                expiry_text = info.get('expiry_date')
                if not expiry_text:
                    continue
                try:
                    expiry_date = datetime.strptime(expiry_text, '%Y-%m-%d').date()
                except (TypeError, ValueError):
                    continue
                if warmup_start < expiry_date <= max_expiry:
                    eligible_codes.append(code)
        eligible_codes = sorted(set(eligible_codes))
        self._warmup_contract_sql_cache[cache_key] = eligible_codes
        return eligible_codes

    def _warmup_iv_consistent(self, warmup_dates, product_pool):
        """批量预热 ATM IV 历史：优先使用真实标的收盘价，缺失时才回退 PCP。"""
        logger.info("IV预热: %d天 (%s ~ %s) - 真实标的优先",
                    len(warmup_dates), warmup_dates[0], warmup_dates[-1])
        t0 = time.time()
        requested_products = set(product_pool)

        like_sql = self._build_product_like_sql(product_pool)
        if not like_sql:
            logger.warning("  品种池无匹配合约，跳过预热")
            return

        contract_codes = self._get_warmup_contract_codes(product_pool, warmup_dates)
        warmup_chunk_size = int(self.config.get('warmup_prefilter_chunk_size', 2000) or 2000)
        max_prefilter_chunks = int(self.config.get('warmup_prefilter_max_chunks', 4) or 4)
        prefilter_sqls = list(
            iter_code_filter_sql(contract_codes, chunk_size=warmup_chunk_size)
        ) if contract_codes else []
        filter_sqls = prefilter_sqls if prefilter_sqls and len(prefilter_sqls) <= max_prefilter_chunks else [like_sql]
        logger.info("  LIKE条件: %s", like_sql)

        logger.info(
            "  Warmup contract prefilter: %d contracts, %d candidate chunks, mode=%s",
            len(contract_codes), len(prefilter_sqls),
            'chunked' if filter_sqls is prefilter_sqls else 'like',
        )
        logger.info("  Executing warmup batch query...")
        t_query = time.time()
        df_parts = []
        for filter_sql in filter_sqls:
            query = f"""
                SELECT
                    toString(date) as trade_date,
                    ths_code,
                    argMax(toFloat64OrZero(close), time) as last_close,
                    sum(toInt64OrZero(volume)) as total_volume
                FROM {OPTION_MINUTE_TABLE}
                WHERE date >= '{warmup_dates[0]}' AND date <= '{warmup_dates[-1]}'
                  AND ({filter_sql})
                  AND toFloat64OrZero(close) > 0
                GROUP BY date, ths_code
            """
            part = select_bars_sql(query)
            if part is not None and not part.empty:
                df_parts.append(part)
        if not df_parts:
            logger.warning("  Warmup batch query returned no rows, skip IV warmup")
            return
        df = pd.concat(df_parts, ignore_index=True)
        logger.info("  Warmup query done: %d rows, %.1fs", len(df), time.time() - t_query)

        df['last_close'] = pd.to_numeric(df['last_close'], errors='coerce').fillna(0)
        df['total_volume'] = pd.to_numeric(df['total_volume'], errors='coerce').fillna(0)

        t_attr = time.time()
        ci_cache = self.ci._cache
        codes = df['ths_code'].values
        df['strike'] = np.array([ci_cache.get(c, {}).get('strike', 0) for c in codes], dtype=float)
        df['option_type'] = np.array([ci_cache.get(c, {}).get('option_type', '') for c in codes])
        df['expiry_date'] = np.array([ci_cache.get(c, {}).get('expiry_date', '') for c in codes])
        df['product'] = np.array([ci_cache.get(c, {}).get('product_root', '') for c in codes])
        df['underlying_code'] = np.array([ci_cache.get(c, {}).get('underlying_code') for c in codes], dtype=object)
        df = df[(df['strike'] > 0) & (df['option_type'] != '') & (df['product'] != '')].copy()
        logger.info("  合约属性关联: %d行, %.1f秒", len(df), time.time() - t_attr)
        observed_products = set(df['product'].dropna().unique())
        self._update_product_first_trade_dates_from_frame(df, product_col='product', date_col='trade_date')
        missing_option_products = sorted(requested_products - observed_products)
        if missing_option_products:
            logger.info("  预热区间无期权分钟数据，按品种跳过: %s", missing_option_products)

        t_spot = time.time()
        underlying_codes = sorted({code for code in df['underlying_code'].dropna().tolist() if code})
        real_spot_df = pd.DataFrame(columns=['trade_date', 'underlying_code', 'spot'])
        if underlying_codes:
            alias_map = build_underlying_alias_map(underlying_codes)
            lookup_codes = sorted({alias for aliases in alias_map.values() for alias in aliases})
            spot_filter_sql = build_code_filter_sql(lookup_codes)
            spot_frames = []
            for table_name in self._spot_tables_for_codes(lookup_codes):
                spot_query = f"""
                    SELECT
                        toString(date) as trade_date,
                        ths_code as underlying_code,
                        argMax(toFloat64OrZero(close), time) as spot
                    FROM {table_name}
                    WHERE date >= '{warmup_dates[0]}' AND date <= '{warmup_dates[-1]}'
                      AND ({spot_filter_sql})
                      AND toFloat64OrZero(close) > 0
                    GROUP BY date, ths_code
                """
                spot_part = select_bars_sql(spot_query)
                if spot_part is not None and not spot_part.empty:
                    spot_frames.append(spot_part)
            if spot_frames:
                real_spot_df = map_alias_spot_frame(
                    pd.concat(spot_frames, ignore_index=True),
                    alias_map,
                    lookup_col='underlying_code',
                    value_col='spot',
                    sort_cols=['trade_date'],
                )
                df = df.merge(real_spot_df, on=['trade_date', 'underlying_code'], how='left')
            else:
                df['spot'] = np.nan
        else:
            df['spot'] = np.nan

        if observed_products:
            real_spot_products = set(df.loc[df['spot'].notna() & (df['spot'] > 0), 'product'].dropna().unique())
            missing_real_spot_products = sorted(observed_products - real_spot_products)
            if missing_real_spot_products:
                logger.info("  无法直接匹配真实标的收盘价，转PCP回退: %s", missing_real_spot_products)

        unresolved = df['spot'].isna() | (df['spot'] <= 0)
        if unresolved.any():
            fallback_src = df.loc[unresolved, ['trade_date', 'product', 'expiry_date', 'strike',
                                               'option_type', 'last_close', 'total_volume']].copy()
            spot_agg = build_pcp_spot_frame(fallback_src, risk_free_rate=RISK_FREE_RATE)
            if not spot_agg.empty:
                df = df.merge(
                    spot_agg,
                    on=['trade_date', 'product', 'expiry_date'],
                    how='left'
                )
                fill_mask = (df['spot'].isna() | (df['spot'] <= 0)) & df['spot_pcp'].notna() & (df['spot_pcp'] > 0)
                df.loc[fill_mask, 'spot'] = df.loc[fill_mask, 'spot_pcp']
                df = df.drop(columns=['spot_pcp'], errors='ignore')

        valid_spot_products = set(df.loc[df['spot'].notna() & (df['spot'] > 0), 'product'].dropna().unique())
        skipped_products = sorted(requested_products - valid_spot_products)
        if skipped_products:
            logger.info("  无有效 spot，按品种跳过IV预热: %s", skipped_products)
        if not valid_spot_products:
            logger.warning("  真实spot与PCP回退后仍无有效spot，跳过IV预热")
            return
        logger.info("  warmup spot完成: 真实=%d, 有效行=%d, %.1f秒",
                    len(real_spot_df),
                    int((df['spot'].notna() & (df['spot'] > 0)).sum()),
                    time.time() - t_spot)

        t_iv = time.time()
        df = df[df['spot'].notna() & (df['spot'] > 0)].copy()
        spot_hist_df = df[['trade_date', 'product', 'expiry_date', 'spot']].copy()
        spot_hist_df['trade_dt'] = pd.to_datetime(spot_hist_df['trade_date'], errors='coerce')
        spot_hist_df['expiry_dt'] = pd.to_datetime(spot_hist_df['expiry_date'], errors='coerce')
        spot_hist_df['dte'] = (spot_hist_df['expiry_dt'] - spot_hist_df['trade_dt']).dt.days.clip(lower=1)
        target_dte = float(self.config.get('dte_target', 35))
        spot_hist_df['dte_dist'] = (spot_hist_df['dte'] - target_dte).abs()
        spot_hist_df = spot_hist_df.sort_values(
            ['trade_date', 'product', 'dte_dist', 'dte', 'expiry_date'],
            ascending=[True, True, True, True, True],
            kind='mergesort',
        )
        spot_hist_df = spot_hist_df.drop_duplicates(['trade_date', 'product'], keep='first')
        for row in spot_hist_df.itertuples(index=False):
            product = row.product
            if product in product_pool:
                spot_hist = self._spot_history[product]
                spot_hist['dates'].append(row.trade_date)
                spot_hist['spots'].append(float(row.spot))
        df['moneyness'] = df['strike'] / df['spot']

        atm = df[df['moneyness'].between(0.95, 1.05)].copy()
        if atm.empty:
            logger.warning("  无ATM合约，跳过IV计算")
            return

        atm['trade_dt'] = pd.to_datetime(atm['trade_date'])
        atm['expiry_dt'] = pd.to_datetime(atm['expiry_date'], errors='coerce')
        atm['dte'] = (atm['expiry_dt'] - atm['trade_dt']).dt.days
        atm = atm[(atm['dte'] > 0) & (atm['dte'] <= 90)].copy()
        if atm.empty:
            logger.warning("  无有效DTE的ATM合约")
            return

        atm = atm.rename(columns={'last_close': 'option_close', 'spot': 'spot_close'})
        iv_series = calc_iv_batch(
            atm, price_col='option_close', spot_col='spot_close',
            strike_col='strike', dte_col='dte', otype_col='option_type'
        )
        atm['iv'] = iv_series.values
        atm = atm[atm['iv'].notna() & (atm['iv'] > 0.01) & (atm['iv'] < 3.0)].copy()
        logger.info("  IV计算: %d个ATM合约有效IV, %.1f秒", len(atm), time.time() - t_iv)

        daily_iv = atm.groupby(['trade_date', 'product'])['iv'].mean().reset_index()
        for row in daily_iv.itertuples(index=False):
            product = row.product
            if product in product_pool:
                hist = self._iv_history[product]
                hist['dates'].append(row.trade_date)
                hist['ivs'].append(float(row.iv))

        elapsed = time.time() - t0
        n_products = sum(1 for h in self._iv_history.values() if h['ivs'])
        logger.info("IV预热完成: %.0f秒, %d个品种有历史", elapsed, n_products)

        cache_path = os.path.join(OUTPUT_DIR, 'iv_warmup_cache.json')
        try:
            cache = {'_meta': {'end_date': warmup_dates[-1], 'n_days': len(warmup_dates)}}
            for product, hist in self._iv_history.items():
                if hist['ivs']:
                    spot_hist = self._spot_history.get(product, {'dates': [], 'spots': []})
                    cache[product] = {
                        'dates': hist['dates'],
                        'ivs': hist['ivs'],
                        'spot_dates': spot_hist.get('dates', []),
                        'spots': spot_hist.get('spots', []),
                    }
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f)
            logger.info("  IV缓存已保存: %s", cache_path)
        except OSError as exc:
            logger.warning("  IV缓存保存失败: %s", exc)

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
        if not minute_df.empty:
            vol_agg = minute_df.groupby('ths_code')['volume'].sum()
            day_volume = vol_agg.to_dict()

        for item in self._pending_opens:
            code = item['code']
            code_bars = minute_df[minute_df['ths_code'] == code]

            # 计算全天TWAP/VWAP（只用当日真实分钟成交）
            if not code_bars.empty:
                sorted_bars = code_bars.sort_values('time')
                valid = sorted_bars[sorted_bars['volume'] > 0]
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

            # 成交量约束：用执行日全日成交量，匹配“全天TWAP执行”的口径
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
                if not check_margin_ok(total_m, strat_m, new_m, nav,
                                       exec_budget.get('margin_cap', self.config.get('margin_cap', 0.50)),
                                       effective_strategy_cap):
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
            one_loss = float(item.get('one_contract_stress_loss', 0.0) or 0.0)
            if one_loss > 0:
                pos.stress_loss = one_loss * actual_n
            self._set_open_greeks_for_attribution(pos, date_str)
            self.positions.append(pos)
            open_fee = float(self.config.get('fee', 3) or 0.0) * actual_n
            self._day_realized['fee'] += open_fee
            self.orders.append({
                'date': date_str, 'signal_date': item.get('signal_date', ''),
                'action': f"open_{item['role']}",
                'strategy': item['strat'], 'product': item['product'],
                'code': code, 'option_type': item['opt_type'],
                'strike': item['strike'], 'expiry': str(item['expiry'])[:10],
                'price': round(price, 4), 'quantity': actual_n,
                'fee': round(open_fee, 2), 'pnl': 0,
                'stress_loss': round(pos.stress_loss, 2),
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
                'contract_price_change_1d': item.get('contract_price_change_1d', np.nan),
                'effective_margin_cap': item.get('effective_margin_cap', np.nan),
                'effective_strategy_margin_cap': item.get('effective_strategy_margin_cap', np.nan),
                'effective_product_margin_cap': item.get('effective_product_margin_cap', np.nan),
                'effective_bucket_margin_cap': item.get('effective_bucket_margin_cap', np.nan),
                'effective_stress_loss_cap': item.get('effective_stress_loss_cap', np.nan),
                'effective_bucket_stress_loss_cap': item.get('effective_bucket_stress_loss_cap', np.nan),
                'open_budget_risk_scale': item.get('open_budget_risk_scale', np.nan),
                'open_budget_brake_reason': item.get('open_budget_brake_reason', ''),
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

        # 构建 option_code → (option_close, spot_close) 索引
        price_idx = {}
        spot_by_product = {}
        spot_by_underlying = {}
        for _, row in daily_df.iterrows():
            code = row.get('option_code', '')
            if code and row.get('option_close', 0) > 0:
                price_idx[code] = row['option_close']
            product = row.get('product', '')
            if product and pd.notna(row.get('spot_close')) and row['spot_close'] > 0:
                spot_by_product[product] = row['spot_close']
            underlying_code = row.get('underlying_code', '')
            if underlying_code and pd.notna(row.get('spot_close')) and row['spot_close'] > 0:
                spot_by_underlying[underlying_code] = row['spot_close']

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
        for product in daily_df['product'].unique():
            prod_df = daily_df[daily_df['product'] == product]
            atm = prod_df[
                (prod_df['moneyness'].between(0.95, 1.05)) &
                (prod_df['dte'].between(15, 90)) &
                (prod_df['implied_vol'] > 0)
            ]
            if atm.empty:
                continue
            daily_atm_iv = float(atm['implied_vol'].mean())
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

            daily_spot = np.nan
            spot_candidates = prod_df[['expiry_date', 'dte', 'spot_close']].dropna().copy()
            if not spot_candidates.empty:
                spot_candidates['dte_dist'] = (spot_candidates['dte'] - target_dte).abs()
                spot_candidates = spot_candidates.sort_values(
                    ['dte_dist', 'dte', 'expiry_date'],
                    ascending=[True, True, True],
                    kind='mergesort',
                )
                daily_spot = float(spot_candidates['spot_close'].iloc[0])

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
                    if pos.profit_pct(fee) >= tp and pos.dte > cfg.get('tp_min_dte', 5):
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

        spot_groups = {}
        if self.config.get('intraday_refresh_spot_greeks_for_attribution', True):
            spot_df = self.loader.load_spot_day_minute(date_str, list(pos_by_underlying))
            if not spot_df.empty:
                spot_df = spot_df.sort_values(['time', 'underlying_code'])
                spot_groups = {tm: grp for tm, grp in spot_df.groupby('time')}

        price_df = price_df.sort_values(['time', 'ths_code'])
        price_groups = {tm: grp for tm, grp in price_df.groupby('time')}
        time_points = sorted(price_groups.keys())
        if not time_points:
            return False

        monitor_times = self._sample_intraday_times(
            time_points,
            self.config.get('intraday_risk_interval', 15),
        )
        stop_pending = {}
        if self.config.get('intraday_stop_confirmation_use_full_minutes', True):
            monitor_times = list(time_points)
        for tm in monitor_times:
            spot_grp = spot_groups.get(tm)
            if spot_grp is not None:
                for _, row in spot_grp.iterrows():
                    for pos in pos_by_underlying.get(row['underlying_code'], []):
                        pos.cur_spot = float(row['spot'])

            grp = price_groups.get(tm)
            if grp is not None:
                for _, row in grp.iterrows():
                    code = row['ths_code']
                    price = float(row['close'])
                    volume = float(row.get('volume', 0.0) or 0.0)
                    if not self._confirm_intraday_stop_price(
                        code, price, volume, tm, stop_pending, pos_by_code, qty_by_code
                    ):
                        continue
                    for pos in pos_by_code.get(row['ths_code'], []):
                        pos.cur_price = price

            if self.config.get('intraday_refresh_spot_greeks_for_attribution', True):
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
        vega_warn = cfg.get('greeks_vega_warn', 0.008)
        current_vega_pct = abs(sum(p.cash_vega() for p in self.positions) / nav)
        if current_vega_pct > vega_warn:
            logger.debug("  %s Vega预警 %.3f%% > %.1f%%, 暂停新开仓",
                         date_str, current_vega_pct * 100, vega_warn * 100)
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
            return

        product_frames = {
            product: frame
            for product, frame in filtered_daily.groupby('product', sort=False)
            if not frame.empty
        }
        if not product_frames:
            return

        prod_volume = filtered_daily.groupby('product', as_index=False)['volume'].sum()
        prod_volume = prod_volume.sort_values(
            ['volume', 'product'],
            ascending=[False, True],
            kind='mergesort',
        )
        self._update_product_first_trade_dates(list(product_frames), date_str)
        sorted_products = self._diversify_product_order(prod_volume['product'].tolist())
        sorted_products = self._prioritize_products_by_regime(sorted_products)

        candidate_products = sorted_products if scan_top_n <= 0 else sorted_products[:scan_top_n]
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
                continue
            if not self._passes_product_entry_filters(product, date_str):
                continue

            open_expiries = should_open_new(
                prod_df,
                dte_target=cfg.get('dte_target', 35),
                dte_min=cfg.get('dte_min', 15),
                dte_max=cfg.get('dte_max', 90),
            )
            if not open_expiries:
                continue

            for exp in open_expiries:
                expiry_has_position = (product, exp) in open_product_expiries

                ef = prod_df[prod_df['expiry_date'] == exp]
                if ef.empty:
                    continue

                iv_pct = product_iv_pcts.get(product, np.nan)
                iv_state = self._current_iv_state.get(product, {})
                if should_pause_open(iv_pct, iv_open_thr):
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
                if pd.notna(iv_pct) and iv_pct < low_iv_threshold and not low_iv_allowed:
                    continue
                iv_scale = get_iv_scale(iv_pct, cfg.get('iv_threshold', 75))
                regime_mult = self._product_margin_per_multiplier(product)
                if regime_mult <= 0:
                    continue
                product_margin_per = margin_per * regime_mult

                if total_m / nav >= margin_cap:
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
                    if not self._passes_s1_falling_framework_entry(product, iv_state):
                        continue
                    for ot in ['P', 'C']:
                        if (
                            not cfg.get('s1_allow_add_same_side', False) and
                            (product, ot) in open_s1_sell_sides
                        ):
                            continue
                        if self._is_reentry_blocked('S1', product, ot, date_str):
                            continue
                        s1_plan = self._get_reentry_plan('S1', product, ot, date_str)
                        self._try_open_s1(ef, product, ot, mult, mr, exchange, exp,
                                          nav, product_margin_per, iv_scale, date_str,
                                          reentry_plan=s1_plan,
                                          iv_state=iv_state,
                                          margin_cap=margin_cap, strategy_cap=s1_cap)

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

    def _try_open_s1(self, ef, product, ot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, date_str, reentry_plan=None,
                     iv_state=None, margin_cap=None, strategy_cap=None):
        """S1开仓"""
        delta_cap = float(self.config.get('s1_sell_delta_cap', 0.10))
        min_abs_delta = float(self.config.get('s1_sell_delta_floor', 0.0))
        if reentry_plan:
            delta_cap = float(self.config.get('s1_reentry_delta_cap', 0.15))
            min_abs_delta = min(
                delta_cap,
                max(min_abs_delta, float(reentry_plan.get('delta_abs', 0.0)) + float(self.config.get('s1_reentry_delta_step', 0.02)))
            )
        if self.config.get('s1_falling_framework_enabled', False):
            delta_cap = min(delta_cap, 0.10)
            min_abs_delta = min(min_abs_delta, delta_cap)
        split_enabled = bool(self.config.get('s1_split_across_neighbor_contracts', False))
        max_candidates = 1
        if split_enabled:
            max_candidates = max(1, int(self.config.get('s1_neighbor_contract_count', 3) or 1))
        s1_frame = self._prepare_s1_selection_frame(ef, ot)
        candidates = select_s1_sell(
            s1_frame, ot, mult, mr,
            min_volume=int(self.config.get('s1_min_volume', 0)),
            min_oi=int(self.config.get('s1_min_oi', 0)),
            min_abs_delta=min_abs_delta,
            max_abs_delta=delta_cap,
            target_abs_delta=float(self.config.get('s1_target_abs_delta', 0.07)),
            carry_metric=self.config.get('s1_carry_metric', 'premium_margin'),
            fee_per_contract=float(self.config.get('fee', 0.0) or 0.0),
            min_premium_fee_multiple=float(self.config.get('s1_min_premium_fee_multiple', 0.0) or 0.0),
            use_stress_score=bool(self.config.get('s1_use_stress_score', False)),
            stress_spot_move_pct=float(self.config.get('s1_stress_spot_move_pct', 0.03) or 0.03),
            stress_iv_up_points=float(self.config.get('s1_stress_iv_up_points', 5.0) or 5.0),
            gamma_penalty=float(self.config.get('s1_gamma_penalty', 0.0) or 0.0),
            vega_penalty=float(self.config.get('s1_vega_penalty', 0.0) or 0.0),
            return_candidates=True,
            max_candidates=max_candidates * 3,
            exchange=exchange,
            product=product,
        )
        if candidates is None or candidates.empty:
            return
        candidates = candidates.copy()
        if split_enabled and max_candidates > 1:
            max_delta_gap = float(self.config.get('s1_neighbor_max_delta_gap', 0.0) or 0.0)
            if max_delta_gap > 0 and 'abs_delta' in candidates.columns:
                center_delta = float(candidates['abs_delta'].iloc[0])
                candidates = candidates[
                    (pd.to_numeric(candidates['abs_delta'], errors='coerce') - center_delta).abs() <= max_delta_gap
                ].copy()
            candidates = candidates.head(max_candidates)
        else:
            candidates = candidates.head(1)
        if candidates.empty:
            return
        iv_state = iv_state or {}
        effective_margin_cap = self.config.get('margin_cap', 0.50) if margin_cap is None else margin_cap
        effective_strategy_cap = self.config.get('s1_margin_cap', 0.25) if strategy_cap is None else strategy_cap
        base_margin_per = float(self.config.get('margin_per', 0.02) or 0.02)
        regime_scale = margin_per / base_margin_per if base_margin_per > 0 else 1.0
        open_budget = self._current_open_budget or {}
        if 's1_stress_loss_budget_pct' in open_budget:
            stress_budget_pct = float(open_budget.get('s1_stress_loss_budget_pct') or 0.0)
        else:
            stress_budget_pct = float(self.config.get('s1_stress_loss_budget_pct', 0.0010) or 0.0) * regime_scale
        remaining_stress_budget = nav * stress_budget_pct * float(iv_scale or 1.0)
        remaining_margin_budget = nav * float(margin_per or 0.0) / 2.0 * float(iv_scale or 1.0)
        min_qty = int(self.config.get('s1_stress_min_qty', 1) or 1)
        max_qty = int(self.config.get('s1_stress_max_qty', 50) or 50)
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
                if not check_margin_ok(
                    total_m, s1_m, single_margin * qty, nav,
                    effective_margin_cap, effective_strategy_cap,
                ):
                    return False
                return self._passes_portfolio_construction(
                    product, nav, single_margin * qty, date_str=date_str,
                    new_cash_vega=new_greeks['cash_vega'],
                    new_cash_gamma=new_greeks['cash_gamma'],
                    new_stress_loss=total_stress_loss,
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
                continue
            one_stress_loss = float(c.get('stress_loss', 0.0) or 0.0)
            if self.config.get('s1_use_stress_sizing', False):
                if one_stress_loss <= 0 or remaining_stress_budget <= 0:
                    continue
                target_qty = int(remaining_stress_budget / one_stress_loss)
                if max_qty > 0:
                    target_qty = min(target_qty, max_qty)
                if target_qty < min_qty:
                    continue
            else:
                if remaining_margin_budget <= 0:
                    continue
                target_qty = max(1, int(remaining_margin_budget / m))
            nn = max_allowed_qty(c, m, target_qty, one_stress_loss)
            if nn <= 0:
                continue

            new_greeks = self._candidate_cash_greeks(c, ot, mult, nn, role='sell')
            total_stress_loss = one_stress_loss * nn if one_stress_loss > 0 else 0.0
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
                'contract_price_change_1d': c.get('contract_price_change_1d', np.nan),
            }
            pending_item.update(self._pending_budget_fields(effective_strategy_cap))
            self._pending_opens.append(pending_item)
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
        if not check_margin_ok(total_m, s3_m, sm * sq, nav,
                               effective_margin_cap,
                               effective_strategy_cap):
            return
        if not self._passes_portfolio_construction(
            product, nav, sm * sq, date_str=date_str,
            new_cash_vega=new_greeks['cash_vega'],
            new_cash_gamma=new_greeks['cash_gamma'],
        ):
            return
        group_id = f"S3_{product}_{ot}_{exp}_{date_str}"
        self._pending_opens.append({
            'strat': 'S3', 'product': product, 'code': bl['option_code'],
            'opt_type': ot, 'strike': bl['strike'], 'ref_price': bl['option_close'],
            'n': bq, 'mult': mult, 'expiry': exp, 'mr': mr, 'role': 'buy',
            'spot': bl['spot_close'], 'exchange': exchange, 'group_id': group_id,
            'underlying_code': bl.get('underlying_code'),
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
        if reason.startswith('sl_'):
            for pos in to_close:
                if pos.role == 'sell':
                    self._register_reentry_plan(pos, date_str)
                    break

        for pos in to_close:
            if pos.role in ('buy', 'protect'):
                pnl = (pos.cur_price - pos.prev_price) * pos.mult * pos.n
            else:
                pnl = (pos.prev_price - pos.cur_price) * pos.mult * pos.n
            pa = pos.pnl_attribution(total_pnl=pnl)
            for k, v in pa.items():
                if k.endswith('_pnl') and k in self._day_attr_realized:
                    self._day_attr_realized[k] += float(v)
            fee = fee_per_hand * pos.n
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

            self.orders.append({
                'date': date_str, 'action': reason,
                'time': exec_time or '',
                'strategy': pos.strat, 'product': pos.product,
                'code': pos.code, 'option_type': pos.opt_type,
                'strike': pos.strike, 'expiry': str(pos.expiry)[:10],
                'price': round(pos.cur_price, 4), 'quantity': pos.n,
                'fee': round(fee, 2), 'pnl': round(order_pnl, 2),
            })

        self.positions = [p for p in self.positions if p not in to_close]


    # ── NAV快照 ──────────────────────────────────────────────────────────────

    def _record_daily_diagnostics(self, date_str, nav):
        if not self.config.get('portfolio_diagnostics_enabled', True):
            return
        nav = max(float(nav), 1.0)
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
        for bucket, data in bucket_state.items():
            self.diagnostics_records.append({
                'date': date_str,
                'scope': 'bucket',
                'name': bucket,
                'margin_pct': data['margin'] / nav,
                'cash_vega_pct': data['cash_vega'] / nav,
                'cash_gamma_pct': data['cash_gamma'] / nav,
                'stress_loss_pct': data['stress_loss'] / nav,
                'n_products': len(data['products']),
                'n_positions': data['positions'],
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
                'n_products': len(data['products']),
                'n_positions': data['positions'],
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

        self.nav_records.append({
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
            'effective_bucket_margin_cap': budget.get('bucket_margin_cap', np.nan),
            'effective_stress_loss_cap': budget.get('portfolio_stress_loss_cap', np.nan),
            'effective_bucket_stress_loss_cap': budget.get('portfolio_bucket_stress_loss_cap', np.nan),
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
        })

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
