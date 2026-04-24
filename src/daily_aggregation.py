"""Daily option aggregation helpers for minute backtests."""

from datetime import datetime

import numpy as np
import pandas as pd

from option_calc import calc_iv_batch, calc_greeks_batch_vectorized, RISK_FREE_RATE
from spot_provider import estimate_spot_pcp


def calc_dte_from_expiry(expiry_str, current_date):
    """Calculate calendar-day DTE from an expiry string."""
    try:
        exp = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
        return (exp - current_date).days
    except (ValueError, TypeError):
        return -1


def attach_contract_columns(raw_df, contract_info):
    """Attach contract metadata used by downstream strategy rules."""
    df = raw_df.copy()
    ci_cache = contract_info._cache
    codes = df["ths_code"].values
    df["strike"] = np.array([ci_cache.get(c, {}).get("strike", 0) for c in codes], dtype=float)
    df["option_type"] = np.array([ci_cache.get(c, {}).get("option_type", "") for c in codes])
    df["expiry_date"] = np.array([ci_cache.get(c, {}).get("expiry_date", "") for c in codes])
    df["product"] = np.array([ci_cache.get(c, {}).get("product_root", "") for c in codes])
    df["exchange"] = np.array([ci_cache.get(c, {}).get("exchange", "") for c in codes])
    df["multiplier"] = np.array([ci_cache.get(c, {}).get("multiplier", 10) for c in codes], dtype=float)
    return df


def normalize_preloaded_daily_agg(raw_df, date_str, contract_info):
    """Convert ClickHouse daily aggregation output to strategy-rule columns."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    df = raw_df.copy()
    df["dte"] = df["expiry_date"].map(lambda expiry: calc_dte_from_expiry(expiry, current_date))
    df = df[df["dte"] > 0].copy()
    if df.empty:
        return df

    df["underlying_code"] = df["ths_code"].map(
        lambda c: (contract_info.lookup(c) or {}).get("underlying_code")
    )
    return df.rename(columns={
        "ths_code": "option_code",
        "last_close": "option_close",
        "total_volume": "volume",
        "last_oi": "open_interest",
    })


def aggregate_minute_daily(minute_df, date_str, contract_info):
    """Aggregate option minute bars into daily strategy-rule rows."""
    if minute_df is None or minute_df.empty:
        return pd.DataFrame()

    current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    records = []

    for code, grp in minute_df.groupby("ths_code"):
        info = contract_info.lookup(code)
        if info is None:
            continue

        dte = contract_info.calc_dte(code, current_date)
        if dte <= 0:
            continue

        grp_sorted = grp.sort_values("time")
        valid = grp_sorted[grp_sorted["volume"] > 0]
        if not valid.empty:
            opt_close = float(valid.iloc[-1]["close"])
            volume = int(valid["volume"].sum())
            oi = int(valid.iloc[-1]["open_interest"])
        else:
            opt_close = float(grp_sorted.iloc[-1]["close"])
            volume = 0
            oi = int(grp_sorted.iloc[-1]["open_interest"])

        if opt_close <= 0:
            continue

        records.append({
            "option_code": code,
            "strike": info["strike"],
            "option_type": info["option_type"],
            "option_close": opt_close,
            "dte": dte,
            "volume": volume,
            "open_interest": oi,
            "product": info["product_root"],
            "exchange": info["exchange"],
            "expiry_date": info["expiry_date"],
            "multiplier": info["multiplier"],
            "underlying_code": info.get("underlying_code"),
        })

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def enrich_daily_with_spot_iv_delta(df, spot_map=None, risk_free_rate=RISK_FREE_RATE):
    """Attach spot, moneyness, IV, and delta to daily option rows."""
    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()
    result["spot_close"] = np.nan
    if spot_map:
        result["spot_close"] = result["underlying_code"].map(spot_map)

    for (_product, _expiry), group in result.groupby(["product", "expiry_date"]):
        need_fill = group["spot_close"].isna() | (group["spot_close"] <= 0)
        if need_fill.any():
            spot = estimate_spot_pcp(group, risk_free_rate=risk_free_rate)
            if spot and spot > 0:
                result.loc[group.index[need_fill], "spot_close"] = spot

    result = result[result["spot_close"].notna() & (result["spot_close"] > 0)].copy()
    if result.empty:
        return result

    result["moneyness"] = result["strike"] / result["spot_close"]
    is_otm = (
        ((result["option_type"] == "P") & (result["moneyness"] < 1.0)) |
        ((result["option_type"] == "C") & (result["moneyness"] > 1.0))
    )
    in_range = result["moneyness"].between(0.70, 1.30)
    candidate = is_otm & in_range

    result["implied_vol"] = np.nan
    result["delta"] = np.nan

    if candidate.any():
        iv = calc_iv_batch(
            result[candidate],
            price_col="option_close",
            spot_col="spot_close",
            strike_col="strike",
            dte_col="dte",
            otype_col="option_type",
        )
        result.loc[candidate, "implied_vol"] = iv.values

        has_iv = candidate & result["implied_vol"].notna() & (result["implied_vol"] > 0)
        if has_iv.any():
            greeks = calc_greeks_batch_vectorized(
                result[has_iv],
                spot_col="spot_close",
                strike_col="strike",
                dte_col="dte",
                iv_col="implied_vol",
                otype_col="option_type",
            )
            result.loc[has_iv, "delta"] = greeks["delta"].values

    return result
