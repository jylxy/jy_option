"""Main-sleeve pre-expiry ITM fallback exit helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


EXIT_REASON = "main_pre_expiry_itm_exit"


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "nat"} else text


def config_enabled(config: dict[str, Any]) -> bool:
    rule_cfg = config.get("main_pre_expiry_itm_exit", {})
    if isinstance(rule_cfg, dict) and "enabled" in rule_cfg:
        return bool(rule_cfg.get("enabled"))
    return bool(config.get("main_pre_expiry_itm_exit_enabled", False))


def scan_dte_lt(config: dict[str, Any]) -> float:
    rule_cfg = config.get("main_pre_expiry_itm_exit", {})
    if isinstance(rule_cfg, dict) and "scan_dte_lt" in rule_cfg:
        return safe_float(rule_cfg.get("scan_dte_lt"), 2.0)
    return safe_float(config.get("main_pre_expiry_itm_exit_dte_lt", 2.0), 2.0)


def exit_reason(config: dict[str, Any]) -> str:
    rule_cfg = config.get("main_pre_expiry_itm_exit", {})
    value = rule_cfg.get("exit_reason") if isinstance(rule_cfg, dict) else None
    return safe_text(value) or EXIT_REASON


def intrinsic_value(option_type: Any, strike: Any, spot: Any) -> float:
    opt = safe_text(option_type).upper()[:1]
    k = safe_float(strike)
    s = safe_float(spot)
    if opt not in {"P", "C"} or not np.isfinite(k) or not np.isfinite(s) or k <= 0 or s <= 0:
        return np.nan
    if opt == "C":
        return max(s - k, 0.0)
    return max(k - s, 0.0)


def is_itm(option_type: Any, strike: Any, spot: Any) -> bool:
    intrinsic = intrinsic_value(option_type, strike, spot)
    return bool(np.isfinite(intrinsic) and intrinsic > 0)


def calendar_dte(expiry: Any, date: Any) -> float:
    exp = pd.to_datetime(expiry, errors="coerce")
    cur = pd.to_datetime(date, errors="coerce")
    if pd.isna(exp) or pd.isna(cur):
        return np.nan
    return float((exp.normalize() - cur.normalize()).days)


def parsed_date(value: Any):
    text = safe_text(value)[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _main_fields_from_mapping(values: Any) -> tuple[str, str, str]:
    get = values.get if hasattr(values, "get") else lambda key, default="": default
    entry_reason = safe_text(get("external_entry_reason", "")) or safe_text(get("entry_reason", ""))
    strategy_layer = safe_text(get("external_strategy_layer", "")) or safe_text(get("strategy_layer", ""))
    signal_family = safe_text(get("external_overlay_signal_family", "")) or safe_text(get("overlay_signal_family", ""))
    return entry_reason, strategy_layer, signal_family


def is_main_monthly_mapping(values: Any) -> bool:
    entry_reason, strategy_layer, signal_family = _main_fields_from_mapping(values)
    if entry_reason == "monthly":
        return True
    if entry_reason or strategy_layer or signal_family:
        return False
    return False


def is_main_monthly_position(pos: Any) -> bool:
    meta = getattr(pos, "entry_meta", {}) or {}
    entry_reason, strategy_layer, signal_family = _main_fields_from_mapping(meta)
    if entry_reason == "monthly":
        return True
    if entry_reason or strategy_layer or signal_family:
        return False
    return False
