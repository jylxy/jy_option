"""Toolkit day-level data loader for the minute backtest engine."""

import logging
import time

import pandas as pd

from toolkit.selector import select_bars_sql
from option_calc import RISK_FREE_RATE
from data_tables import OPTION_MINUTE_TABLE, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE
from runtime_paths import CACHE_DIR
from spot_provider import (
    build_underlying_alias_map,
    spot_tables_for_codes,
    map_alias_spot_frame,
    resolve_alias_value_map,
)
from daily_aggregation import (
    attach_contract_columns,
    normalize_preloaded_daily_agg,
    aggregate_minute_daily,
    enrich_daily_with_spot_iv_delta,
)
from trading_calendar import (
    load_trading_dates_cache,
    save_trading_dates_cache,
    query_trading_dates,
    filter_trading_dates,
)

logger = logging.getLogger(__name__)


class ToolkitDayLoader:
    """Load Toolkit minute bars and expose day-level aggregates."""

    def __init__(self, contract_info):
        self._ci = contract_info
        self._trading_dates = None
        self._day_cache = {}
        self._daily_agg_cache = {}
        self._spot_daily_cache = {}
        self._batch_size = 5

    def get_trading_dates(self, start_date=None, end_date=None):
        """Return cached/query trading dates filtered by optional bounds."""
        if self._trading_dates is None:
            self._trading_dates = load_trading_dates_cache(CACHE_DIR, logger=logger)
            if self._trading_dates is None:
                logger.info("获取交易日列表...")
                self._trading_dates = query_trading_dates(select_bars_sql)
                logger.info("  共 %d 个交易日", len(self._trading_dates))
                save_trading_dates_cache(CACHE_DIR, self._trading_dates, logger=logger)
        return filter_trading_dates(self._trading_dates, start_date=start_date, end_date=end_date)

    def preload_batch(self, dates, like_sql=None):
        """Preload several days of minute data into memory cache."""
        to_load = [d for d in dates if d not in self._day_cache]
        if not to_load:
            return

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
                FROM {OPTION_MINUTE_TABLE}
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

            for col in ["open", "high", "low", "close", "volume", "open_interest"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            df = df[df["close"] > 0].copy()

            n_rows = len(df)
            for d in chunk:
                day_df = df[df["trade_date"] == d].drop(columns=["trade_date"], errors="ignore")
                self._day_cache[d] = day_df
            logger.debug("  批量加载 %d天 %d行 (%.1fs)", len(chunk), n_rows, elapsed)

    def load_day_minute(self, date_str, like_sql=None, code_list=None):
        """Load one trading day's option minute bars."""
        if code_list is not None and not any(code_list):
            return pd.DataFrame()
        if like_sql is None and not code_list and date_str in self._day_cache:
            return self._day_cache[date_str].copy()

        where = f"date = '{date_str}'"
        if code_list:
            code_sql = ", ".join(f"'{str(code)}'" for code in sorted({str(code) for code in code_list if code}))
            if code_sql:
                where += f" AND ths_code IN ({code_sql})"
        elif like_sql:
            where += f" AND ({like_sql})"
        query = f"""
            SELECT ths_code, time, open, high, low, close, volume, open_interest
            FROM {OPTION_MINUTE_TABLE}
            WHERE {where}
        """
        df = select_bars_sql(query)
        if df is None or df.empty:
            return pd.DataFrame()

        for col in ["open", "high", "low", "close", "volume", "open_interest"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df = df[df["close"] > 0].copy()
        if like_sql is None and not code_list:
            self._day_cache[date_str] = df
        return df

    def load_spot_day_minute(self, date_str, underlying_codes):
        """Load one trading day's underlying minute spots."""
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
            lookup_col="ths_code",
            value_col="close",
            sort_cols=["time"],
        )

    def clear_cache(self, keep_dates=None):
        """Clear memory caches, optionally preserving specific dates."""
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

    def _query_spot_daily_table_batch(self, table_name, dates, code_list_sql):
        date_list = ", ".join(f"'{d}'" for d in dates)
        query = f"""
            SELECT
                toString(date) as trade_date,
                ths_code,
                argMax(toFloat64OrZero(close), time) as last_close
            FROM {table_name}
            WHERE date IN ({date_list})
              AND ths_code IN ({code_list_sql})
              AND toFloat64OrZero(close) > 0
            GROUP BY date, ths_code
        """
        return select_bars_sql(query)

    def _spot_tables_for_codes(self, underlying_codes):
        return spot_tables_for_codes(underlying_codes, FUTURE_MINUTE_TABLE, ETF_MINUTE_TABLE)

    def _preload_spot_daily_close_batch(self, dates, underlying_codes):
        dates = [d for d in dates if d]
        requested = sorted({str(code) for code in underlying_codes if code})
        if not dates or not requested:
            return

        missing_by_date = {}
        missing_codes = set()
        for date_str in dates:
            cache = self._spot_daily_cache.setdefault(date_str, {})
            missing = [code for code in requested if code not in cache]
            if missing:
                missing_by_date[date_str] = missing
                missing_codes.update(missing)
        if not missing_codes:
            return

        alias_map = build_underlying_alias_map(sorted(missing_codes))
        lookup_codes = sorted({alias for aliases in alias_map.values() for alias in aliases})
        if not lookup_codes:
            return

        code_list_sql = ", ".join(f"'{code}'" for code in lookup_codes)
        frames = []
        for table_name in self._spot_tables_for_codes(lookup_codes):
            df = self._query_spot_daily_table_batch(table_name, dates, code_list_sql)
            if df is not None and not df.empty:
                frames.append(df)

        if frames:
            merged = pd.concat(frames, ignore_index=True)
            merged["last_close"] = pd.to_numeric(merged["last_close"], errors="coerce")
            merged = merged[merged["last_close"].notna() & (merged["last_close"] > 0)]
            for row in merged.itertuples(index=False):
                cache = self._spot_daily_cache.setdefault(str(row.trade_date), {})
                cache[str(row.ths_code)] = float(row.last_close)

        for date_str, missing in missing_by_date.items():
            cache = self._spot_daily_cache.setdefault(date_str, {})
            cache.update(resolve_alias_value_map(cache, alias_map))
            for code in missing:
                cache.setdefault(code, None)

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
                merged["last_close"] = pd.to_numeric(merged["last_close"], errors="coerce")
                merged = merged[merged["last_close"].notna() & (merged["last_close"] > 0)]
                for _, row in merged.iterrows():
                    cache[str(row["ths_code"])] = float(row["last_close"])
            cache.update(resolve_alias_value_map(cache, alias_map))
            for code in missing:
                cache.setdefault(code, None)

        return {
            code: cache.get(code)
            for code in underlying_codes
            if cache.get(code) is not None and cache.get(code) > 0
        }

    def preload_daily_agg_batch(self, dates, like_sql, contract_info):
        """Preload day-level option aggregates for multiple dates."""
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
            FROM {OPTION_MINUTE_TABLE}
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

        df["last_close"] = pd.to_numeric(df["last_close"], errors="coerce").fillna(0)
        df["total_volume"] = pd.to_numeric(df["total_volume"], errors="coerce").fillna(0)
        df["last_oi"] = pd.to_numeric(df["last_oi"], errors="coerce").fillna(0)
        df = attach_contract_columns(df, contract_info)
        df = df[(df["strike"] > 0) & (df["option_type"] != "") & (df["last_close"] > 0)].copy()
        self._preload_spot_daily_close_batch(
            to_load,
            [code for code in df["underlying_code"].dropna().tolist() if code],
        )

        n_rows = len(df)
        for d in to_load:
            day_df = df[df["trade_date"] == d].copy()
            self._daily_agg_cache[d] = day_df
        logger.debug("  日频聚合 %d天 %d行 (%.1fs)", len(to_load), n_rows, elapsed)

    def get_daily_agg(self, date_str, contract_info):
        """Return day-level option rows enriched with spot, IV, and delta."""
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
            [code for code in df["underlying_code"].dropna().tolist() if code],
        )
        return enrich_daily_with_spot_iv_delta(df, spot_map=spot_map, risk_free_rate=RISK_FREE_RATE)

    def aggregate_daily(self, minute_df, date_str):
        """Aggregate minute bars into strategy-rule compatible day rows."""
        df = aggregate_minute_daily(minute_df, date_str, self._ci)
        if df.empty:
            return df

        spot_map = self._get_spot_daily_close_map(
            date_str,
            [code for code in df["underlying_code"].dropna().tolist() if code],
        )
        return enrich_daily_with_spot_iv_delta(df, spot_map=spot_map, risk_free_rate=RISK_FREE_RATE)
