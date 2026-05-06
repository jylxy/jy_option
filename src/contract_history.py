"""Lightweight helpers for per-contract IV and price history.

The S1 selection path calls these lookups for many candidate contracts each day.
Keeping the math list-based avoids constructing small pandas Series repeatedly.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


CONTRACT_TREND_FIELDS = (
    'contract_iv',
    'contract_iv_change_1d',
    'contract_iv_change_3d',
    'contract_iv_change_5d',
    'contract_price',
    'contract_price_change_1d',
    'contract_price_change_3d',
    'contract_price_change_5d',
)


def empty_contract_trend_state() -> dict:
    return {field: np.nan for field in CONTRACT_TREND_FIELDS}


def _finite_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return np.nan
    return out if math.isfinite(out) else np.nan


def update_contract_iv_history(history_map, daily_df: pd.DataFrame, date_str: str) -> bool:
    """Append the latest IV/price rows into the mutable contract history map."""
    cols = {'option_code', 'implied_vol', 'option_close'}
    if daily_df is None or daily_df.empty or not cols.issubset(set(daily_df.columns)):
        return False
    rows = daily_df[['option_code', 'implied_vol', 'option_close']].copy()
    rows['implied_vol'] = pd.to_numeric(rows['implied_vol'], errors='coerce')
    rows['option_close'] = pd.to_numeric(rows['option_close'], errors='coerce')
    rows = rows[
        rows['option_code'].notna()
        & (rows['implied_vol'] > 0)
        & (rows['option_close'] > 0)
    ]
    if rows.empty:
        return False
    for row in rows.itertuples(index=False):
        code = str(row.option_code)
        hist = history_map[code]
        iv = float(row.implied_vol)
        price = float(row.option_close)
        if hist['dates'] and hist['dates'][-1] == date_str:
            hist['ivs'][-1] = iv
            hist['prices'][-1] = price
        else:
            hist['dates'].append(date_str)
            hist['ivs'].append(iv)
            hist['prices'].append(price)
    return True


def contract_trend_state_from_history(history_map, option_code) -> dict:
    hist = history_map.get(str(option_code)) if history_map is not None else None
    state = empty_contract_trend_state()
    if not hist:
        return state
    ivs = hist.get('ivs', [])
    prices = hist.get('prices', [])
    if not ivs and not prices:
        return state

    last_iv = _finite_float(ivs[-1]) if len(ivs) >= 1 else np.nan
    last_price = _finite_float(prices[-1]) if len(prices) >= 1 else np.nan
    if np.isfinite(last_iv):
        state['contract_iv'] = last_iv
    if np.isfinite(last_price):
        state['contract_price'] = last_price

    if len(ivs) >= 2:
        prev_iv = _finite_float(ivs[-2])
        if np.isfinite(last_iv) and np.isfinite(prev_iv):
            state['contract_iv_change_1d'] = last_iv - prev_iv
    if len(prices) >= 2:
        prev_price = _finite_float(prices[-2])
        if np.isfinite(last_price) and np.isfinite(prev_price) and prev_price > 0:
            state['contract_price_change_1d'] = last_price / prev_price - 1.0

    for lookback in (3, 5):
        if len(ivs) > lookback:
            prev_iv = _finite_float(ivs[-1 - lookback])
            if np.isfinite(last_iv) and np.isfinite(prev_iv):
                state[f'contract_iv_change_{lookback}d'] = last_iv - prev_iv
        if len(prices) > lookback:
            prev_price = _finite_float(prices[-1 - lookback])
            if np.isfinite(last_price) and np.isfinite(prev_price) and prev_price > 0:
                state[f'contract_price_change_{lookback}d'] = last_price / prev_price - 1.0
    return state
