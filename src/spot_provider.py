"""Spot lookup and PCP fallback helpers.

This module keeps underlying-price mechanics separate from the minute engine.
It handles futures/ETF table routing, continuous-contract aliases, alias
resolution, and put-call parity spot estimates.
"""

import numpy as np
import pandas as pd


FUTURE_MINUTE_TABLE = "future_hf_1min"
ETF_MINUTE_TABLE = "etf_hf_1min_non_ror"


def extract_future_root(code):
    """Return the alphabetic root of a futures-like underlying code."""
    base = str(code).split(".", 1)[0]
    root = []
    for ch in base:
        if ch.isalpha():
            root.append(ch)
        else:
            break
    return "".join(root).upper()


def build_underlying_alias_map(underlying_codes):
    """Map requested underlyings to exact and fallback lookup codes."""
    alias_map = {}
    for code in sorted({str(code) for code in underlying_codes if code}):
        aliases = [code]
        if "." not in code:
            alias_map[code] = aliases
            continue
        base, suffix = code.split(".", 1)
        if base.isdigit():
            alias_map[code] = aliases
            continue
        root = extract_future_root(base)
        if root:
            continuous = f"{root}ZL.{suffix}"
            if continuous not in aliases:
                aliases.append(continuous)
        alias_map[code] = aliases
    return alias_map


def spot_tables_for_codes(underlying_codes, future_table=FUTURE_MINUTE_TABLE,
                          etf_table=ETF_MINUTE_TABLE):
    codes = [str(code) for code in underlying_codes if code]
    if not codes:
        return []
    code_suffixes = {code.rsplit(".", 1)[-1] if "." in code else "" for code in codes}
    if code_suffixes and code_suffixes.issubset({"SH", "SZ"}):
        return [etf_table]
    if code_suffixes.isdisjoint({"SH", "SZ"}):
        return [future_table]
    return [future_table, etf_table]


def build_alias_reverse_frame(alias_map):
    rows = []
    for requested_code, aliases in (alias_map or {}).items():
        for alias_rank, alias_code in enumerate(aliases):
            rows.append({
                "lookup_code": alias_code,
                "underlying_code": requested_code,
                "alias_rank": alias_rank,
            })
    return pd.DataFrame(rows)


def map_alias_spot_frame(df, alias_map, lookup_col, value_col,
                         sort_cols, output_value_col="spot"):
    """Resolve exact/continuous aliases and keep the best alias per sort key."""
    if df is None or df.empty:
        return pd.DataFrame(columns=list(sort_cols) + ["underlying_code", output_value_col])
    reverse_df = build_alias_reverse_frame(alias_map)
    if reverse_df.empty:
        return pd.DataFrame(columns=list(sort_cols) + ["underlying_code", output_value_col])

    out = df.copy()
    out[output_value_col] = pd.to_numeric(out[value_col], errors="coerce")
    out = out[out[output_value_col].notna() & (out[output_value_col] > 0)].copy()
    if out.empty:
        return pd.DataFrame(columns=list(sort_cols) + ["underlying_code", output_value_col])

    out = out.rename(columns={lookup_col: "lookup_code"})
    out = out.merge(reverse_df, on="lookup_code", how="inner")
    sort_cols = list(sort_cols)
    out = out.sort_values(
        sort_cols + ["underlying_code", "alias_rank"],
        ascending=[True] * (len(sort_cols) + 2),
        kind="mergesort",
    )
    out = out.drop_duplicates(sort_cols + ["underlying_code"], keep="first")
    return out[sort_cols + ["underlying_code", output_value_col]]


def resolve_alias_value_map(raw_map, alias_map):
    """Resolve requested code values from exact aliases first, then fallbacks."""
    resolved = {}
    for requested_code, aliases in (alias_map or {}).items():
        for alias in aliases:
            value = raw_map.get(alias)
            if value is not None and value > 0:
                resolved[requested_code] = float(value)
                break
    return resolved


def estimate_spot_pcp(group, price_col="option_close", volume_col="volume",
                      dte_col="dte", risk_free_rate=0.0):
    """Estimate spot from paired calls/puts using discounted put-call parity."""
    if group is None or group.empty:
        return None
    calls = group[group["option_type"] == "C"]
    puts = group[group["option_type"] == "P"]
    if calls.empty or puts.empty:
        return None

    common_strikes = set(calls["strike"].values) & set(puts["strike"].values)
    if not common_strikes:
        return None

    dte = group[dte_col].iloc[0] if dte_col in group.columns else 30
    t = max(float(dte), 1.0) / 365.0
    discount = np.exp(float(risk_free_rate) * t)

    estimates = []
    for strike in common_strikes:
        c_row = calls[calls["strike"] == strike]
        p_row = puts[puts["strike"] == strike]
        if c_row.empty or p_row.empty:
            continue
        c_price = float(c_row.iloc[0][price_col])
        p_price = float(p_row.iloc[0][price_col])
        if c_price <= 0 or p_price <= 0:
            continue
        spot = float(strike) + discount * (c_price - p_price)
        if spot > 0:
            c_vol = int(c_row.iloc[0].get(volume_col, 0) or 0)
            p_vol = int(p_row.iloc[0].get(volume_col, 0) or 0)
            estimates.append((spot, min(c_vol, p_vol) + 1.0))

    if not estimates:
        return None
    total_w = sum(w for _, w in estimates)
    return sum(spot * w for spot, w in estimates) / total_w


def build_pcp_spot_frame(fallback_src, risk_free_rate=0.0,
                         price_col="last_close", volume_col="total_volume"):
    """Vectorized PCP fallback used by IV warmup."""
    if fallback_src is None or fallback_src.empty:
        return pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    calls = fallback_src[fallback_src["option_type"] == "C"]
    puts = fallback_src[fallback_src["option_type"] == "P"]
    pairs = calls.merge(
        puts,
        on=["trade_date", "product", "expiry_date", "strike"],
        suffixes=("_c", "_p"),
        how="inner",
    )
    if pairs.empty:
        return pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    pairs["trade_dt"] = pd.to_datetime(pairs["trade_date"], errors="coerce")
    pairs["expiry_dt"] = pd.to_datetime(pairs["expiry_date"], errors="coerce")
    pairs["dte"] = (pairs["expiry_dt"] - pairs["trade_dt"]).dt.days.clip(lower=1)
    pairs["discount"] = np.exp(float(risk_free_rate) * pairs["dte"].astype(float) / 365.0)
    pairs["spot_est"] = (
        pairs["strike"] +
        pairs["discount"] * (pairs[f"{price_col}_c"] - pairs[f"{price_col}_p"])
    )
    pairs = pairs[pairs["spot_est"] > 0].copy()
    if pairs.empty:
        return pd.DataFrame(columns=["trade_date", "product", "expiry_date", "spot_pcp"])

    pairs["weight"] = np.minimum(pairs[f"{volume_col}_c"], pairs[f"{volume_col}_p"]) + 1.0
    pairs["weighted_spot"] = pairs["spot_est"] * pairs["weight"]
    spot_agg = pairs.groupby(["trade_date", "product", "expiry_date"]).agg(
        spot_sum=("weighted_spot", "sum"),
        weight_sum=("weight", "sum"),
    ).reset_index()
    spot_agg = spot_agg[spot_agg["weight_sum"] > 0].copy()
    spot_agg["spot_pcp"] = spot_agg["spot_sum"] / spot_agg["weight_sum"]
    return spot_agg[["trade_date", "product", "expiry_date", "spot_pcp"]]
