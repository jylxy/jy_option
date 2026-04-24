"""IV warmup pipeline for the Toolkit minute backtest engine.

The warmup path is deliberately separate from the main engine because it has
its own data contract: use real underlying prices first, use PCP only as a
fallback when paired calls/puts exist, and never invent a spot from strikes.
"""

import json
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Iterable, Mapping, MutableMapping

import numpy as np
import pandas as pd

from query_filters import (
    normalize_product_pool,
    build_code_filter_sql,
    iter_code_filter_sql,
)
from spot_provider import (
    build_underlying_alias_map,
    map_alias_spot_frame,
    build_pcp_spot_frame,
)


@dataclass(frozen=True)
class IVWarmupContext:
    """External dependencies needed by the IV warmup pipeline."""

    config: Mapping
    contract_info: object
    iv_history: MutableMapping
    spot_history: MutableMapping
    select_sql: Callable[[str], pd.DataFrame]
    calc_iv_batch: Callable[..., pd.Series]
    update_product_first_trade_dates_from_frame: Callable[..., None]
    spot_tables_for_codes: Callable[[Iterable[str]], list]
    option_minute_table: str
    risk_free_rate: float = 0.0
    logger: object = None


def warmup_cache_path(output_dir):
    return os.path.join(output_dir, "iv_warmup_cache.json")


def load_iv_warmup_cache(cache_path, product_pool, iv_history, spot_history,
                         required_end_date, logger=None):
    """Load cached IV/spot histories that cover the requested warmup window."""
    cached_products = set()
    if not os.path.exists(cache_path):
        return cached_products

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if logger is not None:
            logger.warning("IV warmup cache load failed: %s", exc)
        return cached_products

    cache_end = cache.get("_meta", {}).get("end_date", "")
    if cache_end < required_end_date:
        return cached_products

    requested = set(product_pool)
    for product, data in cache.items():
        if product == "_meta" or product not in requested:
            continue
        hist = iv_history[product]
        hist["dates"] = list(data.get("dates", []))
        hist["ivs"] = list(data.get("ivs", []))
        spot_hist = spot_history[product]
        spot_hist["dates"] = list(data.get("spot_dates", []))
        spot_hist["spots"] = list(data.get("spots", []))
        cached_products.add(product)
    return cached_products


def save_iv_warmup_cache(cache_path, end_date, iv_history, spot_history,
                         n_days=None, logger=None):
    """Persist IV/spot warmup histories. Returns True on success."""
    cache = {"_meta": {"end_date": end_date}}
    if n_days is not None:
        cache["_meta"]["n_days"] = int(n_days)
    for product, hist in iv_history.items():
        if not hist.get("ivs"):
            continue
        spot_hist = spot_history.get(product, {"dates": [], "spots": []})
        cache[product] = {
            "dates": list(hist.get("dates", [])),
            "ivs": list(hist.get("ivs", [])),
            "spot_dates": list(spot_hist.get("dates", [])),
            "spots": list(spot_hist.get("spots", [])),
        }

    try:
        cache_dir = os.path.dirname(cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError as exc:
        if logger is not None:
            logger.warning("IV warmup cache save failed: %s", exc)
        return False

    if logger is not None:
        logger.info("  IV warmup cache saved: %s (%d products)", cache_path, len(cache) - 1)
    return True


def get_warmup_contract_codes(product_pool, warmup_dates, contract_info,
                              max_dte=90, cache=None):
    """Return contracts whose expiry can affect the warmup window."""
    normalized_pool = normalize_product_pool(product_pool)
    if not normalized_pool or not warmup_dates:
        return []

    warmup_start = pd.Timestamp(warmup_dates[0]).date()
    max_expiry = pd.Timestamp(warmup_dates[-1]).date() + timedelta(days=max(int(max_dte), 90))
    cache_key = (normalized_pool, warmup_start.isoformat(), max_expiry.isoformat())
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    eligible_codes = []
    for product in normalized_pool:
        for code in contract_info.get_product_codes(product):
            info = contract_info.lookup(code) or {}
            expiry_text = info.get("expiry_date")
            if not expiry_text:
                continue
            try:
                expiry_date = pd.Timestamp(expiry_text).date()
            except (TypeError, ValueError):
                continue
            if warmup_start < expiry_date <= max_expiry:
                eligible_codes.append(code)

    eligible_codes = sorted(set(eligible_codes))
    if cache is not None:
        cache[cache_key] = eligible_codes
    return eligible_codes


def build_warmup_filter_sqls(like_sql, contract_codes, chunk_size=2000, max_chunks=4):
    """Choose prefiltered contract chunks when doing so reduces query scope."""
    prefilter_sqls = list(iter_code_filter_sql(contract_codes, chunk_size=chunk_size)) if contract_codes else []
    if prefilter_sqls and len(prefilter_sqls) <= max_chunks:
        return prefilter_sqls, prefilter_sqls, "chunked"
    return ([like_sql] if like_sql else []), prefilter_sqls, "like"


def attach_warmup_contract_columns(df, contract_cache):
    """Attach static contract attributes and drop rows without valid metadata."""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    codes = out["ths_code"].astype(str).values
    out["strike"] = pd.to_numeric(
        pd.Series([contract_cache.get(code, {}).get("strike", 0) for code in codes], index=out.index),
        errors="coerce",
    ).fillna(0.0)
    out["option_type"] = np.array([contract_cache.get(code, {}).get("option_type", "") for code in codes])
    out["expiry_date"] = np.array([contract_cache.get(code, {}).get("expiry_date", "") for code in codes])
    out["product"] = np.array([contract_cache.get(code, {}).get("product_root", "") for code in codes])
    out["underlying_code"] = np.array(
        [contract_cache.get(code, {}).get("underlying_code") for code in codes],
        dtype=object,
    )
    return out[(out["strike"] > 0) & (out["option_type"] != "") & (out["product"] != "")].copy()


def query_warmup_option_rows(warmup_dates, filter_sqls, option_minute_table,
                             select_sql, logger=None):
    """Query daily last option close and total volume for warmup contracts."""
    if not filter_sqls:
        return pd.DataFrame()

    t_query = time.time()
    df_parts = []
    for filter_sql in filter_sqls:
        query = f"""
            SELECT
                toString(date) as trade_date,
                ths_code,
                argMax(toFloat64OrZero(close), time) as last_close,
                sum(toInt64OrZero(volume)) as total_volume
            FROM {option_minute_table}
            WHERE date >= '{warmup_dates[0]}' AND date <= '{warmup_dates[-1]}'
              AND ({filter_sql})
              AND toFloat64OrZero(close) > 0
            GROUP BY date, ths_code
        """
        part = select_sql(query)
        if part is not None and not part.empty:
            df_parts.append(part)
    if not df_parts:
        return pd.DataFrame()

    df = pd.concat(df_parts, ignore_index=True)
    if logger is not None:
        logger.info("  Warmup option query done: %d rows, %.1fs", len(df), time.time() - t_query)
    df["last_close"] = pd.to_numeric(df["last_close"], errors="coerce").fillna(0.0)
    df["total_volume"] = pd.to_numeric(df["total_volume"], errors="coerce").fillna(0.0)
    return df


def attach_real_spot_for_warmup(df, warmup_dates, select_sql, spot_tables_for_codes,
                                logger=None):
    """Attach real underlying closes by exact/continuous-code alias matching."""
    out = df.copy()
    underlying_codes = sorted({code for code in out["underlying_code"].dropna().tolist() if code})
    real_spot_df = pd.DataFrame(columns=["trade_date", "underlying_code", "spot"])
    if not underlying_codes:
        out["spot"] = np.nan
        return out, real_spot_df

    alias_map = build_underlying_alias_map(underlying_codes)
    lookup_codes = sorted({alias for aliases in alias_map.values() for alias in aliases})
    spot_filter_sql = build_code_filter_sql(lookup_codes)
    if not spot_filter_sql:
        out["spot"] = np.nan
        return out, real_spot_df

    spot_frames = []
    for table_name in spot_tables_for_codes(lookup_codes):
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
        spot_part = select_sql(spot_query)
        if spot_part is not None and not spot_part.empty:
            spot_frames.append(spot_part)

    if not spot_frames:
        out["spot"] = np.nan
        return out, real_spot_df

    real_spot_df = map_alias_spot_frame(
        pd.concat(spot_frames, ignore_index=True),
        alias_map,
        lookup_col="underlying_code",
        value_col="spot",
        sort_cols=["trade_date"],
    )
    if logger is not None:
        logger.info("  Warmup real spot rows: %d", len(real_spot_df))
    return out.merge(real_spot_df, on=["trade_date", "underlying_code"], how="left"), real_spot_df


def fill_missing_spot_with_pcp(df, risk_free_rate=0.0):
    """Fill missing spot values only when valid same-strike C/P pairs exist."""
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    out = df.copy()
    if "spot" not in out.columns:
        out["spot"] = np.nan
    unresolved = out["spot"].isna() | (out["spot"] <= 0)
    if not unresolved.any():
        return out, pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    fallback_cols = [
        "trade_date", "product", "expiry_date", "strike",
        "option_type", "last_close", "total_volume",
    ]
    missing_cols = [col for col in fallback_cols if col not in out.columns]
    if missing_cols:
        return out, pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    fallback_src = out.loc[unresolved, fallback_cols].copy()
    spot_agg = build_pcp_spot_frame(fallback_src, risk_free_rate=risk_free_rate)
    if spot_agg.empty:
        return out, spot_agg

    out = out.merge(spot_agg, on=["trade_date", "product", "expiry_date"], how="left")
    fill_mask = (
        (out["spot"].isna() | (out["spot"] <= 0)) &
        out["spot_pcp"].notna() &
        (out["spot_pcp"] > 0)
    )
    out.loc[fill_mask, "spot"] = out.loc[fill_mask, "spot_pcp"]
    out = out.drop(columns=["spot_pcp"], errors="ignore")
    return out, spot_agg


def append_spot_history_from_warmup(df, product_pool, spot_history, target_dte):
    valid = df[df["spot"].notna() & (df["spot"] > 0)].copy()
    if valid.empty:
        return 0

    requested = set(product_pool)
    spot_hist_df = valid[["trade_date", "product", "expiry_date", "spot"]].copy()
    spot_hist_df["trade_dt"] = pd.to_datetime(spot_hist_df["trade_date"], errors="coerce")
    spot_hist_df["expiry_dt"] = pd.to_datetime(spot_hist_df["expiry_date"], errors="coerce")
    spot_hist_df["dte"] = (spot_hist_df["expiry_dt"] - spot_hist_df["trade_dt"]).dt.days.clip(lower=1)
    spot_hist_df["dte_dist"] = (spot_hist_df["dte"] - float(target_dte)).abs()
    spot_hist_df = spot_hist_df.sort_values(
        ["trade_date", "product", "dte_dist", "dte", "expiry_date"],
        ascending=[True, True, True, True, True],
        kind="mergesort",
    )
    spot_hist_df = spot_hist_df.drop_duplicates(["trade_date", "product"], keep="first")

    appended = 0
    for row in spot_hist_df.itertuples(index=False):
        if row.product not in requested:
            continue
        hist = spot_history[row.product]
        hist["dates"].append(row.trade_date)
        hist["spots"].append(float(row.spot))
        appended += 1
    return appended


def build_daily_warmup_iv(df, calc_iv_batch):
    valid = df[df["spot"].notna() & (df["spot"] > 0)].copy()
    if valid.empty:
        return pd.DataFrame(columns=["trade_date", "product", "iv"]), 0

    valid["moneyness"] = valid["strike"] / valid["spot"]
    atm = valid[valid["moneyness"].between(0.95, 1.05)].copy()
    if atm.empty:
        return pd.DataFrame(columns=["trade_date", "product", "iv"]), 0

    atm["trade_dt"] = pd.to_datetime(atm["trade_date"], errors="coerce")
    atm["expiry_dt"] = pd.to_datetime(atm["expiry_date"], errors="coerce")
    atm["dte"] = (atm["expiry_dt"] - atm["trade_dt"]).dt.days
    atm = atm[(atm["dte"] > 0) & (atm["dte"] <= 90)].copy()
    if atm.empty:
        return pd.DataFrame(columns=["trade_date", "product", "iv"]), 0

    atm = atm.rename(columns={"last_close": "option_close", "spot": "spot_close"})
    iv_values = calc_iv_batch(
        atm,
        price_col="option_close",
        spot_col="spot_close",
        strike_col="strike",
        dte_col="dte",
        otype_col="option_type",
    )
    iv_array = np.asarray(iv_values, dtype=float)
    if len(iv_array) != len(atm):
        return pd.DataFrame(columns=["trade_date", "product", "iv"]), 0

    atm["iv"] = iv_array
    atm = atm[atm["iv"].notna() & (atm["iv"] > 0.01) & (atm["iv"] < 3.0)].copy()
    if atm.empty:
        return pd.DataFrame(columns=["trade_date", "product", "iv"]), 0
    return atm.groupby(["trade_date", "product"])["iv"].mean().reset_index(), len(atm)


def warmup_iv_consistent(warmup_dates, product_pool, *, like_sql, contract_codes,
                         context):
    """Warm up ATM IV history using real spot first and PCP fallback second."""
    log = context.logger
    if not warmup_dates:
        return
    if log is not None:
        log.info(
            "IV warmup: %d days (%s ~ %s), real spot first",
            len(warmup_dates), warmup_dates[0], warmup_dates[-1],
        )

    t0 = time.time()
    requested_products = set(product_pool)
    if not like_sql:
        if log is not None:
            log.warning("  No matching contracts for product pool; skip IV warmup")
        return

    warmup_chunk_size = int(context.config.get("warmup_prefilter_chunk_size", 2000) or 2000)
    max_prefilter_chunks = int(context.config.get("warmup_prefilter_max_chunks", 4) or 4)
    filter_sqls, prefilter_sqls, mode = build_warmup_filter_sqls(
        like_sql,
        contract_codes,
        chunk_size=warmup_chunk_size,
        max_chunks=max_prefilter_chunks,
    )
    if log is not None:
        log.info("  LIKE filter: %s", like_sql)
        log.info(
            "  Warmup contract prefilter: %d contracts, %d candidate chunks, mode=%s",
            len(contract_codes), len(prefilter_sqls), mode,
        )

    df = query_warmup_option_rows(
        warmup_dates,
        filter_sqls,
        context.option_minute_table,
        context.select_sql,
        logger=log,
    )
    if df.empty:
        if log is not None:
            log.warning("  Warmup option query returned no rows, skip IV warmup")
        return

    t_attr = time.time()
    df = attach_warmup_contract_columns(df, getattr(context.contract_info, "_cache", {}))
    if log is not None:
        log.info("  Warmup contract attributes: %d rows, %.1fs", len(df), time.time() - t_attr)
    if df.empty:
        return

    observed_products = set(df["product"].dropna().unique())
    context.update_product_first_trade_dates_from_frame(
        df,
        product_col="product",
        date_col="trade_date",
    )
    missing_option_products = sorted(requested_products - observed_products)
    if missing_option_products and log is not None:
        log.info("  No option minute data in warmup window; skip products: %s", missing_option_products)

    t_spot = time.time()
    df, real_spot_df = attach_real_spot_for_warmup(
        df,
        warmup_dates,
        context.select_sql,
        context.spot_tables_for_codes,
        logger=log,
    )
    if observed_products and log is not None:
        real_spot_products = set(
            df.loc[df["spot"].notna() & (df["spot"] > 0), "product"].dropna().unique()
        )
        missing_real_spot_products = sorted(observed_products - real_spot_products)
        if missing_real_spot_products:
            log.info("  Missing real spot; fallback to PCP where paired C/P exists: %s",
                     missing_real_spot_products)

    df, _spot_pcp = fill_missing_spot_with_pcp(df, risk_free_rate=context.risk_free_rate)
    valid_spot_products = set(
        df.loc[df["spot"].notna() & (df["spot"] > 0), "product"].dropna().unique()
    )
    skipped_products = sorted(requested_products - valid_spot_products)
    if skipped_products and log is not None:
        log.info("  No valid spot; skip IV warmup by product: %s", skipped_products)
    if not valid_spot_products:
        if log is not None:
            log.warning("  No valid spot after real spot and PCP fallback; skip IV warmup")
        return
    if log is not None:
        log.info(
            "  Warmup spot done: real rows=%d, valid rows=%d, %.1fs",
            len(real_spot_df),
            int((df["spot"].notna() & (df["spot"] > 0)).sum()),
            time.time() - t_spot,
        )

    t_iv = time.time()
    append_spot_history_from_warmup(
        df,
        product_pool,
        context.spot_history,
        target_dte=float(context.config.get("dte_target", 35)),
    )
    daily_iv, n_atm = build_daily_warmup_iv(df, context.calc_iv_batch)
    if daily_iv.empty:
        if log is not None:
            log.warning("  No valid ATM contracts for IV warmup")
        return

    for row in daily_iv.itertuples(index=False):
        if row.product not in product_pool:
            continue
        hist = context.iv_history[row.product]
        hist["dates"].append(row.trade_date)
        hist["ivs"].append(float(row.iv))

    if log is not None:
        n_products = sum(1 for hist in context.iv_history.values() if hist.get("ivs"))
        log.info("  Warmup IV calc: %d ATM contracts, %.1fs", n_atm, time.time() - t_iv)
        log.info("IV warmup done: %.0fs, %d products with history", time.time() - t0, n_products)
