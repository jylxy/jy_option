"""S1 put/call side-selection and trend-confidence helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _float_or_nan(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return value if np.isfinite(value) else np.nan


def classify_s1_trend_confidence(
    returns,
    rv_trend=np.nan,
    *,
    short_lookback=5,
    medium_lookback=10,
    long_lookback=20,
    min_history=10,
    trend_threshold=0.018,
    range_threshold=0.010,
    rv_rising_threshold=0.015,
    range_pressure_enabled=False,
    range_pressure_lookback=20,
    range_pressure_upper=0.80,
    range_pressure_lower=0.20,
    range_pressure_min_short_ret=0.004,
):
    """Classify a rough trend state from trailing underlying returns."""
    values = pd.to_numeric(pd.Series(returns), errors="coerce").replace(
        [np.inf, -np.inf],
        np.nan,
    ).dropna()
    max_lookback = max(
        int(short_lookback or 1),
        int(medium_lookback or 1),
        int(long_lookback or 1),
        1,
    )
    min_history = max(int(min_history or 1), 1)
    if len(values) < min(min_history, max_lookback):
        return {
            "trend_state": "uncertain",
            "trend_score": np.nan,
            "trend_confidence": 0.0,
            "trend_short_ret": np.nan,
            "trend_medium_ret": np.nan,
            "trend_long_ret": np.nan,
            "trend_range_position": np.nan,
            "trend_range_pressure": "",
        }

    def trailing_sum(window):
        window = max(int(window or 1), 1)
        return float(values.tail(min(window, len(values))).sum())

    short_ret = trailing_sum(short_lookback)
    medium_ret = trailing_sum(medium_lookback)
    long_ret = trailing_sum(long_lookback)
    score = 0.50 * short_ret + 0.30 * medium_ret + 0.20 * long_ret

    signs = []
    noise = max(float(range_threshold or 0.0), 0.0)
    for val in (short_ret, medium_ret, long_ret):
        if abs(val) <= noise:
            continue
        signs.append(1 if val > 0 else -1)
    if not signs:
        alignment = 0.0
    else:
        score_sign = 1 if score >= 0 else -1
        alignment = sum(1 for s in signs if s == score_sign) / len(signs)

    threshold = max(float(trend_threshold or 0.0), 1e-12)
    confidence = min(abs(score) / threshold, 1.0) * (0.50 + 0.50 * alignment)
    if abs(score) <= noise:
        state = "range_bound"
    elif score >= threshold and alignment >= 0.50:
        state = "uptrend"
    elif score <= -threshold and alignment >= 0.50:
        state = "downtrend"
    else:
        state = "uncertain"

    range_position = np.nan
    range_pressure = ""
    if range_pressure_enabled and state == "range_bound":
        pressure_window = max(int(range_pressure_lookback or long_lookback or 1), 2)
        pressure_values = values.tail(min(pressure_window, len(values)))
        clipped = pressure_values.clip(lower=-0.95)
        price_path = np.exp(np.log1p(clipped).cumsum())
        if len(price_path) >= 2:
            low = float(price_path.min())
            high = float(price_path.max())
            span = high - low
            if span > 1e-12:
                range_position = float((price_path.iloc[-1] - low) / span)
                upper = min(max(float(range_pressure_upper or 0.80), 0.0), 1.0)
                lower = min(max(float(range_pressure_lower or 0.20), 0.0), 1.0)
                min_short_ret = max(float(range_pressure_min_short_ret or 0.0), 0.0)
                if range_position >= upper and short_ret >= min_short_ret:
                    state = "uptrend"
                    range_pressure = "upper"
                    edge_strength = (range_position - upper) / max(1.0 - upper, 1e-12)
                    momentum_strength = min(abs(short_ret) / threshold, 1.0)
                    confidence = max(
                        confidence,
                        min(1.0, 0.35 + 0.65 * (0.60 * edge_strength + 0.40 * momentum_strength)),
                    )
                elif range_position <= lower and short_ret <= -min_short_ret:
                    state = "downtrend"
                    range_pressure = "lower"
                    edge_strength = (lower - range_position) / max(lower, 1e-12)
                    momentum_strength = min(abs(short_ret) / threshold, 1.0)
                    confidence = max(
                        confidence,
                        min(1.0, 0.35 + 0.65 * (0.60 * edge_strength + 0.40 * momentum_strength)),
                    )

    rv_trend = _float_or_nan(rv_trend)
    if (
        state == "range_bound"
        and pd.notna(rv_trend)
        and rv_trend >= float(rv_rising_threshold or 0.0)
    ):
        state = "uncertain"
        confidence *= 0.75

    return {
        "trend_state": state,
        "trend_score": float(score),
        "trend_confidence": float(max(0.0, min(confidence, 1.0))),
        "trend_short_ret": short_ret,
        "trend_medium_ret": medium_ret,
        "trend_long_ret": long_ret,
        "trend_range_position": range_position,
        "trend_range_pressure": range_pressure,
    }


def s1_trend_side_adjustment(
    option_type,
    trend_state,
    trend_confidence=0.0,
    *,
    weak_delta_cap=0.060,
    weak_score_mult=0.60,
    weak_budget_mult=0.50,
    strong_score_mult=1.00,
):
    """Return side-level score, delta, and budget adjustments for trend-aware S1."""
    opt = str(option_type or "").upper()
    state = str(trend_state or "uncertain")
    confidence = max(0.0, min(_float_or_nan(trend_confidence), 1.0))
    if pd.isna(confidence):
        confidence = 0.0
    role = "neutral"
    if state == "uptrend":
        role = "strong" if opt == "P" else "weak"
    elif state == "downtrend":
        role = "strong" if opt == "C" else "weak"

    if role == "weak":
        score_mult = 1.0 - confidence * (1.0 - float(weak_score_mult or 0.0))
        budget_mult = 1.0 - confidence * (1.0 - float(weak_budget_mult or 0.0))
        return {
            "trend_role": role,
            "score_mult": max(score_mult, 0.0),
            "budget_mult": max(budget_mult, 0.0),
            "delta_cap": max(float(weak_delta_cap or 0.0), 0.0),
        }
    if role == "strong":
        score_mult = 1.0 + confidence * (float(strong_score_mult or 1.0) - 1.0)
        return {
            "trend_role": role,
            "score_mult": max(score_mult, 0.0),
            "budget_mult": 1.0,
            "delta_cap": None,
        }
    return {
        "trend_role": role,
        "score_mult": 1.0,
        "budget_mult": 1.0,
        "delta_cap": None,
    }


def s1_side_adjusted_score(row, option_type, momentum=np.nan,
                           momentum_threshold=0.02, momentum_penalty=0.75):
    """Score a side after penalizing adverse short-term underlying momentum."""
    if row is None:
        return np.nan
    raw_score = _float_or_nan(row.get("quality_score", row.get("carry_score", np.nan)))
    if pd.isna(raw_score):
        return np.nan
    momentum = _float_or_nan(momentum)
    if pd.isna(momentum):
        return raw_score

    threshold = max(float(momentum_threshold or 0.0), 0.0)
    penalty_weight = max(float(momentum_penalty or 0.0), 0.0)
    if penalty_weight <= 0:
        return raw_score

    opt = str(option_type or "").upper()
    if opt == "P":
        adverse_move = max(0.0, -momentum - threshold)
    elif opt == "C":
        adverse_move = max(0.0, momentum - threshold)
    else:
        adverse_move = 0.0
    if adverse_move <= 0:
        return raw_score

    adverse_units = adverse_move / threshold if threshold > 0 else 1.0
    return raw_score / (1.0 + penalty_weight * adverse_units)


def choose_s1_trend_confidence_sides(
    side_candidates,
    *,
    trend_state,
    current_regime="normal_vol",
    conditional_strangle_enabled=True,
    allowed_strangle_regimes=None,
    strangle_states=None,
    strangle_min_score_ratio=0.90,
    strangle_min_adjusted_score=0.35,
    allow_weak_side=True,
    weak_side_min_score_ratio=0.75,
):
    """Choose S1 sides using trend-confidence roles and adjusted scores."""
    side_candidates = side_candidates or {}
    available = {
        str(ot).upper(): row
        for ot, row in side_candidates.items()
        if row is not None
    }
    if not available:
        return []

    scores = {
        ot: _float_or_nan(row.get("quality_score", row.get("carry_score", np.nan)))
        for ot, row in available.items()
    }
    scores = {ot: val for ot, val in scores.items() if pd.notna(val)}
    if not scores:
        return []

    state = str(trend_state or "uncertain")
    strangle_states = set(strangle_states or ("range_bound",))
    allowed = set(allowed_strangle_regimes or ("falling_vol_carry", "low_stable_vol"))
    if (
        conditional_strangle_enabled
        and state in strangle_states
        and current_regime in allowed
        and {"P", "C"}.issubset(scores)
    ):
        high = max(scores["P"], scores["C"])
        low = min(scores["P"], scores["C"])
        min_score = float(strangle_min_adjusted_score or 0.0)
        min_ratio = float(strangle_min_score_ratio or 0.0)
        if (
            (min_score <= 0 or low >= min_score)
            and (min_ratio <= 0 or high <= 0 or low >= high * min_ratio)
        ):
            return ["P", "C"] if scores["P"] >= scores["C"] else ["C", "P"]

    if state == "uptrend":
        strong, weak = "P", "C"
    elif state == "downtrend":
        strong, weak = "C", "P"
    else:
        return sorted(scores, key=lambda ot: (-scores[ot], ot))[:1]

    if strong not in scores:
        return sorted(scores, key=lambda ot: (-scores[ot], ot))[:1]
    selected = [strong]
    if allow_weak_side and weak in scores:
        min_score = float(strangle_min_adjusted_score or 0.0)
        min_ratio = float(weak_side_min_score_ratio or 0.0)
        if (
            (min_score <= 0 or scores[weak] >= min_score)
            and (min_ratio <= 0 or scores[strong] <= 0 or scores[weak] >= scores[strong] * min_ratio)
        ):
            selected.append(weak)
    return selected


def choose_s1_option_sides(
    side_candidates,
    *,
    enabled=False,
    conditional_strangle_enabled=False,
    current_regime="normal_vol",
    momentum=np.nan,
    momentum_threshold=0.02,
    momentum_penalty=0.75,
    allowed_strangle_regimes=None,
    strangle_max_abs_momentum=0.015,
    strangle_min_score_ratio=0.90,
    strangle_min_adjusted_score=0.35,
    strangle_require_momentum=True,
):
    """Choose S1 sides from top put/call candidates."""
    side_candidates = side_candidates or {}
    legacy_sides = [ot for ot in ("P", "C") if side_candidates.get(ot) is not None]
    if not enabled:
        return legacy_sides

    ranked = []
    for ot in ("P", "C"):
        row = side_candidates.get(ot)
        if row is None:
            continue
        adjusted = s1_side_adjusted_score(
            row,
            ot,
            momentum=momentum,
            momentum_threshold=momentum_threshold,
            momentum_penalty=momentum_penalty,
        )
        if pd.isna(adjusted):
            continue
        ranked.append((ot, float(adjusted)))
    if not ranked:
        return []

    ranked.sort(key=lambda item: (-item[1], item[0]))
    best_side = [ranked[0][0]]
    if not conditional_strangle_enabled or len(ranked) < 2:
        return best_side

    allowed = set(allowed_strangle_regimes or ("falling_vol_carry", "low_stable_vol"))
    if current_regime not in allowed:
        return best_side

    momentum = _float_or_nan(momentum)
    if strangle_require_momentum and pd.isna(momentum):
        return best_side
    max_abs_momentum = float(strangle_max_abs_momentum or 0.0)
    if pd.notna(momentum) and max_abs_momentum >= 0 and abs(momentum) > max_abs_momentum:
        return best_side

    high_score = ranked[0][1]
    low_score = ranked[1][1]
    min_score = float(strangle_min_adjusted_score or 0.0)
    if min_score > 0 and low_score < min_score:
        return best_side
    ratio = float(strangle_min_score_ratio or 0.0)
    if ratio > 0 and high_score > 0 and low_score < high_score * ratio:
        return best_side
    return [ranked[0][0], ranked[1][0]]
