"""Exchange-aware short option margin model.

The functions in this module are shared by the daily and minute engines. Keep
them independent from backtest state so they can be tested directly.
"""

import re

import numpy as np

from broker_costs import broker_margin_ratio_for_product


EXCHANGE_ALIASES = {
    "SH": "SSE",
    "SZ": "SZSE",
    "CFE": "CFFEX",
    "SHF": "SHFE",
    "CZC": "CZCE",
    "GFE": "GFEX",
}

EQUITY_OPTION_EXCHANGES = {"SSE", "SZSE"}
COMMODITY_OPTION_EXCHANGES = {"SHFE", "INE", "DCE", "CZCE", "GFEX"}

DEFAULT_MARGIN_RATIO_BY_EXCHANGE = {
    "SSE": 0.12,
    "SZSE": 0.12,
    "CFFEX": 0.10,
    "SHFE": 0.07,
    "INE": 0.07,
    "DCE": 0.07,
    "CZCE": 0.07,
    "GFEX": 0.07,
}

DEFAULT_MARGIN_RATIO_BY_PRODUCT = {
    "510050": 0.12,
    "510300": 0.12,
    "510500": 0.12,
    "159915": 0.12,
    "159919": 0.12,
    "588000": 0.12,
}


def normalize_exchange(exchange):
    text = str(exchange or "").upper().strip()
    return EXCHANGE_ALIASES.get(text, text)


def normalize_product(product):
    text = str(product or "").upper().strip()
    if not text:
        return ""
    text = text.split(".", 1)[0]
    if text.isdigit():
        return text
    match = re.match(r"([A-Z]+)", text)
    return match.group(1) if match else text


def coerce_ratio(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "")
        if not value:
            return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(ratio) or ratio <= 0:
        return None
    if ratio > 1:
        ratio /= 100.0
    return ratio if 0 < ratio < 1 else None


def lookup_ratio(mapping, key):
    if not mapping or not key:
        return None
    if key in mapping:
        return coerce_ratio(mapping[key])
    upper_map = {str(k).upper(): v for k, v in mapping.items()}
    return coerce_ratio(upper_map.get(str(key).upper()))


def resolve_margin_ratio(exchange=None, product=None, config=None,
                         default=None, data_ratio=None):
    """Resolve the underlying margin ratio used inside option margin formulas."""
    exchange = normalize_exchange(exchange)
    product = normalize_product(product)
    cfg = config or {}

    ratio = lookup_ratio(cfg.get("margin_ratio_by_product", {}), product)
    if ratio is not None:
        return ratio

    if cfg.get("margin_ratio_use_broker_table", True):
        ratio = coerce_ratio(broker_margin_ratio_for_product(product))
        if ratio is not None:
            return ratio

    ratio = coerce_ratio(data_ratio)
    if ratio is not None:
        return ratio

    ratio = lookup_ratio(DEFAULT_MARGIN_RATIO_BY_PRODUCT, product)
    if ratio is not None:
        return ratio

    ratio = lookup_ratio(cfg.get("margin_ratio_by_exchange", {}), exchange)
    if ratio is not None:
        return ratio

    ratio = lookup_ratio(DEFAULT_MARGIN_RATIO_BY_EXCHANGE, exchange)
    if ratio is not None:
        return ratio

    ratio = coerce_ratio(default)
    return ratio if ratio is not None else 0.10


def estimate_margin(spot, strike, option_type, option_price, multiplier,
                    margin_ratio=None, min_guarantee=0.5, exchange=None,
                    product=None, equity_min_guarantee_ratio=0.07):
    """Estimate one-lot short option margin by exchange-specific formula."""
    try:
        spot = float(spot)
        strike = float(strike)
        option_price = float(option_price)
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        return 0.0
    if not all(np.isfinite(v) for v in (spot, strike, option_price, multiplier)):
        return 0.0
    if multiplier <= 0:
        return 0.0
    option_price = max(option_price, 0.0)
    if spot <= 0 or strike <= 0:
        return option_price * multiplier

    exchange = normalize_exchange(exchange)
    option_type = str(option_type or "").upper()[:1]
    margin_ratio = resolve_margin_ratio(
        exchange=exchange,
        product=product,
        data_ratio=margin_ratio,
    )
    if option_type == "C":
        otm_amount = max(strike - spot, 0.0)
    else:
        otm_amount = max(spot - strike, 0.0)

    if exchange in EQUITY_OPTION_EXCHANGES:
        min_ratio = coerce_ratio(equity_min_guarantee_ratio) or 0.07
        if option_type == "C":
            required = option_price + max(
                spot * margin_ratio - otm_amount,
                spot * min_ratio,
            )
        else:
            required = min(
                option_price + max(
                    spot * margin_ratio - otm_amount,
                    strike * min_ratio,
                ),
                strike,
            )
        margin = max(required, option_price) * multiplier
    elif exchange in COMMODITY_OPTION_EXCHANGES:
        futures_margin = spot * margin_ratio
        margin = (
            option_price + max(
                futures_margin - 0.5 * otm_amount,
                0.5 * futures_margin,
            )
        ) * multiplier
    else:
        min_guarantee = coerce_ratio(min_guarantee)
        if min_guarantee is None:
            min_guarantee = 0.5
        if option_type == "C":
            margin = (
                option_price + max(
                    spot * margin_ratio - otm_amount,
                    min_guarantee * spot * margin_ratio,
                )
            ) * multiplier
        else:
            margin = (
                option_price + max(
                    spot * margin_ratio - otm_amount,
                    min_guarantee * strike * margin_ratio,
                )
            ) * multiplier
    return max(float(margin), 0.0)
