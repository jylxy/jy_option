"""Premium stop scope and layered-stop parsing helpers."""

from __future__ import annotations

from typing import Iterable, List, Mapping, Sequence


def select_s1_stop_scope_positions(
    positions: Iterable[object],
    trigger_pos: object,
    scope: object,
    *,
    multiple: float = 0.0,
) -> List[object]:
    scope_name = str(scope or "group").lower()
    gid = getattr(trigger_pos, "group_id", "")
    all_positions = list(positions)

    if scope_name == "contract":
        return [trigger_pos]
    if scope_name == "same_code":
        return [
            pos for pos in all_positions
            if pos.role == trigger_pos.role and pos.code == trigger_pos.code
        ]
    if scope_name == "triggered_in_group":
        if not gid:
            return [trigger_pos]
        threshold_multiple = float(multiple or 0.0)
        result = []
        for pos in all_positions:
            if pos.group_id != gid or pos.role != trigger_pos.role:
                continue
            if threshold_multiple <= 0 or pos.cur_price >= pos.open_price * threshold_multiple:
                result.append(pos)
        return result
    if scope_name == "product_side_group":
        return [
            pos for pos in all_positions
            if pos.role == trigger_pos.role
            and pos.product == trigger_pos.product
            and pos.opt_type == trigger_pos.opt_type
        ]
    return [pos for pos in all_positions if pos.group_id == gid and gid] if gid else [trigger_pos]


def s1_layer_level_key(level: Mapping[str, object]) -> str:
    multiple = float(level.get("multiple", 0.0) or 0.0)
    action = str(level.get("action", "close") or "close").lower()
    scope = str(level.get("scope", "contract") or "contract").lower()
    return f"{multiple:.6f}:{action}:{scope}"


def parse_s1_layered_stop_levels(raw_levels: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(raw_levels, list):
        return []
    levels = []
    for raw in raw_levels:
        if not isinstance(raw, dict):
            continue
        multiple = float(raw.get("multiple", 0.0) or 0.0)
        if multiple <= 0:
            continue
        level = dict(raw)
        level["multiple"] = multiple
        level["action"] = str(level.get("action", "close") or "close").lower()
        level["scope"] = str(level.get("scope", "contract") or "contract").lower()
        level["ratio"] = float(level.get("ratio", 1.0) or 1.0)
        levels.append(level)
    return sorted(levels, key=lambda item: float(item.get("multiple", 0.0)), reverse=True)
