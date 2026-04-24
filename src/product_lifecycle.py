"""Product listing lifecycle helpers.

These helpers keep listing-date discovery and observation-window checks outside
the minute engine so newly listed options can be tested independently.
"""

import json
import os

import pandas as pd

from product_taxonomy import normalize_product_key


FIRST_TRADE_CACHE_FILE = "product_first_trade_cache.json"


def product_first_trade_cache_path(cache_dir):
    """Return the first-trade cache path for a cache directory."""
    return os.path.join(cache_dir, FIRST_TRADE_CACHE_FILE)


def coerce_trade_date_str(value):
    """Coerce a date-like value to YYYY-MM-DD, returning empty string on failure."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit() and len(text) <= 6:
        try:
            ts = pd.Timestamp("1970-01-01") + pd.Timedelta(days=int(text))
            return ts.strftime("%Y-%m-%d")
        except (ValueError, OverflowError, TypeError):
            return ""
    try:
        return pd.Timestamp(text).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def load_first_trade_cache(cache_path, logger=None):
    """Load normalized first-trade dates from disk."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            return {}
        result = {}
        for product, first_trade_date in cache.items():
            key = normalize_product_key(product)
            value = coerce_trade_date_str(first_trade_date)
            if key and value:
                result[key] = value
        return result
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if logger is not None:
            logger.warning("product first-trade cache load failed: %s", exc)
        return {}


def save_first_trade_cache(cache_path, first_trade_dates, logger=None):
    """Persist normalized first-trade dates to disk."""
    try:
        cache_dir = os.path.dirname(cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        cache = {
            product: first_trade_date
            for product, first_trade_date in sorted(first_trade_dates.items())
            if first_trade_date
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        return True
    except OSError as exc:
        if logger is not None:
            logger.warning("product first-trade cache save failed: %s", exc)
        return False


def update_first_trade_dates(first_trade_dates, products, trade_date):
    """Update first-trade dates from a product iterable. Mutates the mapping."""
    trade_date = coerce_trade_date_str(trade_date)
    if not trade_date:
        return False
    changed = False
    for product in products:
        key = normalize_product_key(product)
        if not key:
            continue
        current = first_trade_dates.get(key)
        if not current or trade_date < current:
            first_trade_dates[key] = trade_date
            changed = True
    return changed


def update_first_trade_dates_from_frame(first_trade_dates, df, product_col="product", date_col="trade_date"):
    """Update first-trade dates from a DataFrame. Mutates the mapping."""
    if df is None or df.empty or product_col not in df.columns or date_col not in df.columns:
        return False
    work = df[[product_col, date_col]].dropna().copy()
    if work.empty:
        return False
    work[product_col] = work[product_col].map(normalize_product_key)
    work[date_col] = work[date_col].map(coerce_trade_date_str)
    work = work[(work[product_col] != "") & (work[date_col] != "")]
    if work.empty:
        return False
    firsts = work.groupby(product_col, as_index=False)[date_col].min()
    changed = False
    for _, row in firsts.iterrows():
        product = row[product_col]
        trade_date = row[date_col]
        current = first_trade_dates.get(product)
        if not current or trade_date < current:
            first_trade_dates[product] = trade_date
            changed = True
    return changed


def product_observation_ready(
    first_trade_dates,
    product,
    date_str,
    observation_months=0,
    min_listing_days=0,
):
    """Return whether a product has passed the post-listing observation window."""
    product = normalize_product_key(product)
    first_trade_date = first_trade_dates.get(product)
    if not first_trade_date:
        return True
    first_ts = pd.Timestamp(first_trade_date)
    if observation_months > 0:
        eligible_ts = first_ts + pd.DateOffset(months=int(observation_months))
    elif min_listing_days > 0:
        eligible_ts = first_ts + pd.Timedelta(days=int(min_listing_days))
    else:
        eligible_ts = first_ts
    return pd.Timestamp(date_str) >= eligible_ts
