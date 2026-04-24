"""Trading-calendar cache and query helpers."""

import json
import os

from data_tables import OPTION_MINUTE_TABLE


def trading_dates_cache_path(cache_dir):
    """Return the trading-date cache path."""
    return os.path.join(cache_dir, "trading_dates_cache.json")


def load_trading_dates_cache(cache_dir, logger=None):
    """Load cached trading dates, returning None when cache is unavailable."""
    cache_path = trading_dates_cache_path(cache_dir)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            dates = json.load(f)
        if not isinstance(dates, list):
            return None
        if logger is not None:
            logger.info("交易日缓存加载完成: 共 %d 个交易日", len(dates))
        return dates
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if logger is not None:
            logger.warning("交易日缓存加载失败，回退数据库查询: %s", exc)
        return None


def save_trading_dates_cache(cache_dir, dates, logger=None):
    """Persist trading dates to cache."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(trading_dates_cache_path(cache_dir), "w", encoding="utf-8") as f:
            json.dump(dates, f)
        return True
    except OSError as exc:
        if logger is not None:
            logger.warning("交易日缓存保存失败: %s", exc)
        return False


def query_trading_dates(select_sql, table_name=OPTION_MINUTE_TABLE):
    """Query distinct trading dates from the option minute table."""
    query = f"SELECT DISTINCT toString(date) as date_str FROM {table_name} ORDER BY date_str"
    df = select_sql(query)
    if df is None or df.empty:
        return []
    return [str(d)[:10] for d in df["date_str"].tolist()]


def filter_trading_dates(dates, start_date=None, end_date=None):
    """Filter trading dates inclusively by optional start/end dates."""
    result = list(dates or [])
    if start_date:
        result = [d for d in result if d >= start_date]
    if end_date:
        result = [d for d in result if d <= end_date]
    return result
