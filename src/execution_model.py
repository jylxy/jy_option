"""Execution price helpers for backtests."""

import math


def _as_float(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _configured_pct(config, action, reason):
    if not config.get("execution_slippage_enabled", False):
        return 0.0

    reason = str(reason or "").lower()
    if reason == "expiry" and not config.get("execution_slippage_apply_to_expiry", False):
        return 0.0

    if reason.startswith("sl_"):
        stop_pct = config.get("execution_stop_slippage_pct")
        if stop_pct is not None:
            return max(0.0, _as_float(stop_pct, 0.0))

    action = str(action or "").lower()
    if action.endswith("_open"):
        open_pct = config.get("execution_open_slippage_pct")
        if open_pct is not None:
            return max(0.0, _as_float(open_pct, 0.0))
    if action.endswith("_close"):
        close_pct = config.get("execution_close_slippage_pct")
        if close_pct is not None:
            return max(0.0, _as_float(close_pct, 0.0))

    return max(0.0, _as_float(config.get("execution_slippage_pct", 0.0), 0.0))


def apply_execution_slippage(price, action, config, reason=""):
    """Return (adjusted_price, slippage_per_unit) under adverse execution.

    Supported actions:
    - buy_open / buy_close: execute higher than the reference price.
    - sell_open / sell_close: execute lower than the reference price.

    Expiry settlement is left untouched by default because it is intrinsic-value
    settlement rather than a tradable quote.
    """
    config = config or {}
    reason = str(reason or "").lower()
    if not config.get("execution_slippage_enabled", False):
        price = _as_float(price, 0.0)
        return price, 0.0
    if reason == "expiry" and not config.get("execution_slippage_apply_to_expiry", False):
        price = _as_float(price, 0.0)
        return price, 0.0

    price = _as_float(price, 0.0)
    if price <= 0:
        return price, 0.0

    pct = _configured_pct(config, action, reason)
    min_abs = max(0.0, _as_float(config.get("execution_slippage_min_abs", 0.0), 0.0))
    slip = max(price * pct, min_abs) if pct > 0 or min_abs > 0 else 0.0
    if slip <= 0:
        return price, 0.0

    action = str(action or "").lower()
    if action.startswith("buy_"):
        return price + slip, slip
    if action.startswith("sell_"):
        return max(0.0, price - slip), min(price, slip)
    return price, 0.0
