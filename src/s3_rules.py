"""S3 ratio-spread contract selection rules."""

from __future__ import annotations

import pandas as pd


def _stable_pick(df, sort_cols, ascending):
    ranked = _stable_rank(df, sort_cols, ascending)
    if ranked is None or ranked.empty:
        return None
    return ranked.iloc[0]


def _stable_rank(df, sort_cols, ascending):
    if df is None or df.empty:
        return None
    work = df.copy()
    cols = list(sort_cols)
    orders = list(ascending)
    if "option_code" in work.columns and "option_code" not in cols:
        cols.append("option_code")
        orders.append(True)
    return work.sort_values(cols, ascending=orders, kind="mergesort")


def select_s3_buy(day_df, option_type):
    """S3 buy leg: 0.10-0.20 abs delta, closest to 0.15."""
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P")
            & (day_df["moneyness"] < 1.0)
            & (day_df["delta"] < 0)
            & (day_df["delta"].abs() >= 0.10)
            & (day_df["delta"].abs() <= 0.20)
            & (day_df["option_close"] >= 0.5)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C")
            & (day_df["moneyness"] > 1.0)
            & (day_df["delta"] > 0)
            & (day_df["delta"] >= 0.10)
            & (day_df["delta"] <= 0.20)
            & (day_df["option_close"] >= 0.5)
        ]
    if c.empty:
        return None
    c = c.copy()
    c["dd"] = (c["delta"].abs() - 0.15).abs()
    return _stable_pick(c, ["dd", "volume", "open_interest"], [True, False, False])


def select_s3_sell(day_df, option_type, buy_strike):
    """S3 sell leg: farther OTM than buy leg, highest premium first."""
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P")
            & (day_df["moneyness"] < 1.0)
            & (day_df["delta"] < 0)
            & (day_df["delta"].abs() >= 0.05)
            & (day_df["delta"].abs() <= 0.15)
            & (day_df["option_close"] >= 0.5)
            & (day_df["strike"] < buy_strike)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C")
            & (day_df["moneyness"] > 1.0)
            & (day_df["delta"] > 0)
            & (day_df["delta"] >= 0.05)
            & (day_df["delta"] <= 0.15)
            & (day_df["option_close"] >= 0.5)
            & (day_df["strike"] > buy_strike)
        ]
    if c.empty:
        return None
    return _stable_pick(c, ["option_close", "volume", "open_interest"], [False, False, False])


def select_s3_protect(day_df, option_type, sell_strike, spot):
    """S3 protection leg farther OTM than the sell leg."""
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P")
            & (day_df["moneyness"] < 1.0)
            & (day_df["option_close"] >= 0.1)
            & (day_df["strike"] < sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike - (spot - sell_strike) * 0.5
    else:
        c = day_df[
            (day_df["option_type"] == "C")
            & (day_df["moneyness"] > 1.0)
            & (day_df["option_close"] >= 0.1)
            & (day_df["strike"] > sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike + (sell_strike - spot) * 0.5
    c = c.copy()
    c["d"] = (c["strike"] - tgt).abs()
    return _stable_pick(c, ["d", "option_close", "volume", "open_interest"], [True, True, False, False])


def _with_otm_pct(day_df, spot_close):
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    return df


def select_s3_buy_by_otm(day_df, option_type, spot_close,
                         target_otm_pct=5.0, otm_range=(3.0, 7.0),
                         min_premium=0.5):
    """S3 buy leg by OTM percent, closest to target OTM."""
    df = _with_otm_pct(day_df, spot_close)
    if df is None:
        return None
    if option_type == "P":
        c = df[
            (df["option_type"] == "P")
            & (df["strike"] < spot_close)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    else:
        c = df[
            (df["option_type"] == "C")
            & (df["strike"] > spot_close)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return _stable_pick(c, ["dist", "volume", "open_interest"], [True, False, False])


def select_s3_sell_by_otm(day_df, option_type, spot_close, buy_strike,
                          target_otm_pct=10.0, otm_range=(7.0, 13.0),
                          min_premium=0.5):
    """S3 sell leg by OTM percent, farther OTM than buy leg."""
    df = _with_otm_pct(day_df, spot_close)
    if df is None:
        return None
    if option_type == "P":
        c = df[
            (df["option_type"] == "P")
            & (df["strike"] < spot_close)
            & (df["strike"] < buy_strike)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    else:
        c = df[
            (df["option_type"] == "C")
            & (df["strike"] > spot_close)
            & (df["strike"] > buy_strike)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    if c.empty:
        return None
    return _stable_pick(c, ["option_close", "volume", "open_interest"], [False, False, False])


def select_s3_protect_by_otm(day_df, option_type, spot_close, sell_strike,
                             target_otm_pct=15.0, otm_range=(12.0, 20.0),
                             min_premium=0.1):
    """S3 protection leg by OTM percent, farther OTM than sell leg."""
    df = _with_otm_pct(day_df, spot_close)
    if df is None:
        return None
    if option_type == "P":
        c = df[
            (df["option_type"] == "P")
            & (df["strike"] < spot_close)
            & (df["strike"] < sell_strike)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    else:
        c = df[
            (df["option_type"] == "C")
            & (df["strike"] > spot_close)
            & (df["strike"] > sell_strike)
            & (df["otm_pct"] >= otm_range[0])
            & (df["otm_pct"] <= otm_range[1])
            & (df["option_close"] >= min_premium)
        ]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return _stable_pick(c, ["dist", "option_close", "volume", "open_interest"], [True, True, False, False])
