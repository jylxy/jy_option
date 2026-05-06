"""Intraday execution price helpers.

These helpers keep the minute engine focused on orchestration. They only
transform already-loaded minute bars into conservative execution references; no
trading signal should depend on future bars produced here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Sequence, Set

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class IntradayPositionIndex:
    positions_by_code: Mapping[str, Sequence[object]]
    quantity_by_code: Mapping[str, int]
    positions_by_underlying: Mapping[str, Sequence[object]]


@dataclass(frozen=True)
class IntradayPriceContext:
    price_groups: Mapping[object, pd.DataFrame]
    time_points: Sequence[object]
    time_index: Mapping[object, int]
    stop_execution_mode: str
    next_price_maps: Mapping[object, Mapping[str, float]]


def index_intraday_positions(positions: Iterable[object]) -> IntradayPositionIndex:
    positions_by_code = defaultdict(list)
    positions_by_underlying = defaultdict(list)
    for pos in positions:
        positions_by_code[pos.code].append(pos)
        if getattr(pos, "underlying_code", None):
            positions_by_underlying[pos.underlying_code].append(pos)

    quantity_by_code = {
        code: sum(int(getattr(pos, "n", 0) or 0) for pos in code_positions)
        for code, code_positions in positions_by_code.items()
    }
    return IntradayPositionIndex(
        positions_by_code=positions_by_code,
        quantity_by_code=quantity_by_code,
        positions_by_underlying=positions_by_underlying,
    )


def normalize_stop_execution_mode(value: object) -> str:
    mode = str(value or "current_close").lower()
    if mode in {"next_high", "next_minute_high"}:
        return "next_minute_high"
    return "current_close"


def build_time_index(time_points: Sequence[object]) -> Dict[object, int]:
    return {tm: idx for idx, tm in enumerate(time_points)}


def build_next_minute_price_maps(
    price_df: pd.DataFrame,
    price_groups: Mapping[object, pd.DataFrame],
    price_col: str = "high",
) -> Dict[object, Dict[str, float]]:
    """Build per-minute code->price maps for delayed execution stress tests."""
    if price_df.empty:
        return {}
    col = price_col if price_col in price_df.columns else "close"
    maps: Dict[object, Dict[str, float]] = {}
    for tm, grp in price_groups.items():
        prices = pd.to_numeric(grp[col], errors="coerce")
        maps[tm] = dict(zip(grp["ths_code"], prices))
    return maps


def build_intraday_price_context(
    price_df: pd.DataFrame,
    *,
    execution_mode: object = "current_close",
) -> IntradayPriceContext:
    if price_df.empty:
        mode = normalize_stop_execution_mode(execution_mode)
        return IntradayPriceContext({}, [], {}, mode, {})

    sorted_price = price_df.sort_values(["time", "ths_code"])
    price_groups = {tm: grp for tm, grp in sorted_price.groupby("time")}
    time_points: List[object] = sorted(price_groups)
    time_index = build_time_index(time_points)
    mode = normalize_stop_execution_mode(execution_mode)
    next_price_maps = (
        build_next_minute_price_maps(sorted_price, price_groups, price_col="high")
        if mode == "next_minute_high"
        else {}
    )
    return IntradayPriceContext(
        price_groups=price_groups,
        time_points=time_points,
        time_index=time_index,
        stop_execution_mode=mode,
        next_price_maps=next_price_maps,
    )


def resolve_stop_execution_price(
    *,
    mode: str,
    code: str,
    tm: object,
    fallback_price: float,
    time_points: Sequence[object],
    time_index: Mapping[object, int],
    next_price_maps: Mapping[object, Mapping[str, float]],
) -> float:
    """Return the reference close price for an intraday stop execution.

    `next_minute_high` is intentionally conservative: the trigger is known at
    time `tm`, but execution is assumed to occur at the next minute's high. This
    is for execution stress testing only, not for signal generation.
    """
    try:
        fallback = float(fallback_price or 0.0)
    except (TypeError, ValueError):
        fallback = 0.0
    if mode != "next_minute_high":
        return fallback
    idx = time_index.get(tm)
    if idx is None or idx + 1 >= len(time_points):
        return fallback
    next_tm = time_points[idx + 1]
    try:
        next_price = float(next_price_maps.get(next_tm, {}).get(code))
    except (TypeError, ValueError):
        next_price = math.nan
    if math.isfinite(next_price) and next_price > 0:
        return next_price
    return fallback


def intraday_stop_threshold(
    config: Mapping[str, object],
    code: str,
    positions_by_code: Mapping[str, Sequence[object]],
) -> float:
    multiple = float(config.get("premium_stop_multiple", 0.0) or 0.0)
    if multiple <= 0:
        return math.nan
    thresholds = [
        float(getattr(pos, "open_price", 0.0) or 0.0) * multiple
        for pos in positions_by_code.get(code, [])
        if (
            getattr(pos, "role", None) == "sell"
            and float(getattr(pos, "open_price", 0.0) or 0.0) > 0
        )
    ]
    return min(thresholds) if thresholds else math.nan


def intraday_stop_required_volume(
    config: Mapping[str, object],
    code: str,
    quantity_by_code: Mapping[str, int],
) -> float:
    min_volume = float(config.get("intraday_stop_min_trade_volume", 3) or 0.0)
    volume_ratio = float(config.get("intraday_stop_min_group_volume_ratio", 0.10) or 0.0)
    group_qty = float(quantity_by_code.get(code, 0) or 0.0)
    return max(min_volume, math.ceil(group_qty * volume_ratio))


def is_intraday_stop_price_illiquid(
    config: Mapping[str, object],
    code: str,
    price: float,
    volume: float,
    positions_by_code: Mapping[str, Sequence[object]],
    quantity_by_code: Mapping[str, int],
) -> bool:
    if not config.get("intraday_stop_liquidity_filter_enabled", True):
        return False
    multiple = float(config.get("premium_stop_multiple", 0.0) or 0.0)
    if multiple <= 0 or price <= 0:
        return False

    triggers_stop = any(
        getattr(pos, "role", None) == "sell"
        and float(getattr(pos, "open_price", 0.0) or 0.0) > 0
        and price >= float(getattr(pos, "open_price", 0.0) or 0.0) * multiple
        for pos in positions_by_code.get(code, [])
    )
    if not triggers_stop:
        return False

    required_volume = intraday_stop_required_volume(config, code, quantity_by_code)
    return float(volume or 0.0) < required_volume


def prefilter_intraday_exit_codes_by_daily_high(
    *,
    config: Mapping[str, object],
    positions: Iterable[object],
    high_map: Mapping[str, float],
    exit_codes: Iterable[str],
) -> Set[str]:
    """Skip minute stop scans when daily high cannot reach any stop line.

    The function is conservative: missing daily high keeps the group in scope.
    Group-level retention is intentional because S1 stops can close protection
    or sibling legs even when only one short leg triggers.
    """
    code_set = set(exit_codes or [])
    if not code_set or not config.get("intraday_stop_daily_high_prefilter_enabled", True):
        return code_set
    if config.get("take_profit_enabled", False):
        return code_set

    multiple = float(config.get("premium_stop_multiple", 0.0) or 0.0)
    if multiple <= 0:
        return set()
    if not high_map:
        return code_set

    positions_by_group = defaultdict(list)
    for pos in positions:
        code = getattr(pos, "code", None)
        if not code or code not in code_set:
            continue
        gid = getattr(pos, "group_id", None) or code
        positions_by_group[gid].append(pos)

    keep_codes: Set[str] = set()
    for group_positions in positions_by_group.values():
        group_codes = {pos.code for pos in group_positions if getattr(pos, "code", None)}
        group_may_stop = False
        for pos in group_positions:
            if getattr(pos, "role", None) != "sell":
                continue
            open_price = float(getattr(pos, "open_price", 0.0) or 0.0)
            if open_price <= 0:
                group_may_stop = True
                break
            day_high = high_map.get(pos.code)
            if day_high is None or day_high <= 0:
                group_may_stop = True
                break
            if float(day_high) >= open_price * multiple:
                group_may_stop = True
                break
        if group_may_stop:
            keep_codes.update(group_codes)

    return keep_codes


def confirm_intraday_stop_price(
    *,
    config: Mapping[str, object],
    code: str,
    price: float,
    volume: float,
    tm: object,
    stop_pending: Dict[str, dict],
    positions_by_code: Mapping[str, Sequence[object]],
    quantity_by_code: Mapping[str, int],
) -> bool:
    threshold = intraday_stop_threshold(config, code, positions_by_code)
    if not np.isfinite(threshold) or threshold <= 0 or price < threshold:
        revert_ratio = float(config.get("intraday_stop_confirmation_revert_ratio", 0.98) or 0.98)
        if np.isfinite(threshold) and threshold > 0 and price < threshold * revert_ratio:
            stop_pending.pop(code, None)
        return True

    illiquid = is_intraday_stop_price_illiquid(
        config, code, price, volume, positions_by_code, quantity_by_code
    )
    if not config.get("intraday_stop_confirmation_enabled", True):
        return not illiquid

    now = pd.Timestamp(tm)
    max_minutes = float(config.get("intraday_stop_confirmation_max_minutes", 30) or 0.0)
    required_obs = max(1, int(config.get("intraday_stop_confirmation_observations", 2) or 1))
    required_volume = intraday_stop_required_volume(config, code, quantity_by_code)
    cur_volume = float(volume or 0.0)

    pending = stop_pending.get(code)
    if pending:
        age_minutes = (now - pending["first_time"]).total_seconds() / 60.0
        if max_minutes > 0 and age_minutes > max_minutes:
            pending = None
            stop_pending.pop(code, None)
    if not pending:
        stop_pending[code] = {
            "first_time": now,
            "observations": 1,
            "cum_volume": max(cur_volume, 0.0),
            "threshold": threshold,
            "max_price": price,
        }
        return False

    pending["observations"] += 1
    pending["cum_volume"] += max(cur_volume, 0.0)
    pending["threshold"] = min(float(pending.get("threshold", threshold)), threshold)
    pending["max_price"] = max(float(pending.get("max_price", price)), price)

    use_cum_volume = bool(config.get("intraday_stop_confirmation_use_cumulative_volume", True))
    if use_cum_volume:
        volume_ok = pending["cum_volume"] >= required_volume and cur_volume > 0
    else:
        volume_ok = cur_volume >= required_volume
    if pending["observations"] >= required_obs and volume_ok:
        stop_pending.pop(code, None)
        return True
    return False
