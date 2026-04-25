"""Volatility regime, cooldown, and re-entry helpers.

These helpers keep S1's short-premium state machine out of the minute engine:
volatility regime classification, post-stop cooldown, and re-entry gating.
They are pure or near-pure functions so the trading logic can be tested without
instantiating the full Toolkit engine.
"""

from collections import Counter

import numpy as np
import pandas as pd


FALLING_VOL_CARRY = "falling_vol_carry"
LOW_STABLE_VOL = "low_stable_vol"
NORMAL_VOL = "normal_vol"
HIGH_RISING_VOL = "high_rising_vol"
POST_STOP_COOLDOWN = "post_stop_cooldown"

REGIME_PRIORITY = {
    FALLING_VOL_CARRY: 0,
    LOW_STABLE_VOL: 1,
    NORMAL_VOL: 2,
    HIGH_RISING_VOL: 3,
    POST_STOP_COOLDOWN: 4,
}


def reentry_key(strat, product, opt_type=None):
    """Key re-entry plans by strategy and product.

    `opt_type` is accepted for call-site compatibility, but intentionally not
    included. A stop on one side means the product/strategy pair must cool down
    before either side re-enters.
    """
    return (str(strat), str(product))


def _iv_values(iv_history, product):
    hist = iv_history.get(product)
    if not hist:
        return None
    ivs = list(hist.get("ivs", []))
    dates = list(hist.get("dates", []))
    if len(ivs) < 2 or len(dates) < 2:
        return None
    values = pd.to_numeric(pd.Series(ivs), errors="coerce")
    cur_iv = values.iloc[-1]
    prev_iv = values.iloc[-2]
    if pd.isna(cur_iv) or pd.isna(prev_iv):
        return None
    return float(prev_iv), float(cur_iv)


def product_iv_turns_lower(iv_history, product):
    values = _iv_values(iv_history, product)
    return bool(values and values[1] < values[0])


def product_iv_not_falling(iv_history, product):
    values = _iv_values(iv_history, product)
    if values is None:
        return True
    return values[1] >= values[0]


def last_iv_trend(iv_history, product, lookback=5):
    hist = iv_history.get(product)
    if not hist:
        return np.nan
    ivs = pd.to_numeric(pd.Series(hist.get("ivs", [])), errors="coerce").dropna()
    if len(ivs) < 2:
        return np.nan
    lb = max(int(lookback or 2), 2)
    prev = ivs.iloc[-min(lb, len(ivs))]
    cur = ivs.iloc[-1]
    if pd.isna(cur) or pd.isna(prev):
        return np.nan
    return float(cur - prev)


def reentry_requires_falling_regime(config, strat):
    strat = str(strat or "").upper()
    if strat == "S1":
        return bool(config.get("s1_reentry_require_falling_regime", True))
    if strat == "S3":
        return bool(config.get("s3_reentry_require_falling_regime", False))
    return bool(config.get("reentry_require_falling_regime", False))


def reentry_requires_daily_iv_drop(config, strat):
    strat = str(strat or "").upper()
    if strat == "S1":
        return bool(config.get(
            "s1_reentry_require_daily_iv_drop",
            config.get("s1_risk_release_require_daily_iv_drop", False),
        ))
    if strat == "S3":
        return bool(config.get("s3_reentry_require_daily_iv_drop", True))
    return bool(config.get("reentry_require_daily_iv_drop", True))


def register_reentry_plan(pos, date_str, *, config, stop_history, reentry_plans,
                          shift_trading_date, normalize_product_key):
    cooldown_days = max(int(config.get("cooldown_days_after_stop", 1)), 0)
    product = normalize_product_key(pos.product)
    hist = stop_history[product]
    hist.append(date_str)

    lookback_days = int(config.get("cooldown_repeat_lookback_days", 20) or 0)
    threshold = int(config.get("cooldown_repeat_threshold", 2) or 0)
    extra_days = int(config.get("cooldown_repeat_extra_days", 2) or 0)
    if lookback_days > 0 and threshold > 0 and extra_days > 0:
        cur_ts = pd.Timestamp(date_str)
        recent = [
            d for d in hist
            if (cur_ts - pd.Timestamp(d)).days <= lookback_days
        ]
        hist[:] = recent
        if len(recent) >= threshold:
            cooldown_days += extra_days * (len(recent) - threshold + 1)

    plan = {
        "earliest_date": shift_trading_date(date_str, cooldown_days),
        "delta_abs": abs(float(getattr(pos, "cur_delta", 0.0) or 0.0)),
        "trigger_opt_type": str(getattr(pos, "opt_type", "") or ""),
        "cooldown_days": cooldown_days,
    }
    spot = float(getattr(pos, "cur_spot", 0.0) or 0.0)
    if spot > 0:
        plan["otm_pct"] = abs(1 - float(pos.strike) / spot) * 100.0

    reentry_plans[reentry_key(pos.strat, pos.product, pos.opt_type)] = plan
    return plan


def classify_product_vol_regime_base(config, state):
    iv_pct = state.get("iv_pct", np.nan)
    spread = state.get("iv_rv_spread", np.nan)
    ratio = state.get("iv_rv_ratio", np.nan)
    rv_trend = state.get("rv_trend", np.nan)
    iv_trend = state.get("iv_trend", np.nan)

    high_iv_trend = float(config.get("vol_regime_high_iv_trend", 0.03) or 0.03)
    high_rv_trend = float(config.get("vol_regime_high_rv_trend", 0.05) or 0.05)
    if pd.notna(iv_trend) and iv_trend >= high_iv_trend:
        return HIGH_RISING_VOL
    if pd.notna(rv_trend) and rv_trend >= high_rv_trend:
        return HIGH_RISING_VOL

    min_spread = float(config.get("vol_regime_min_iv_rv_spread", 0.02) or 0.0)
    min_ratio = float(config.get("vol_regime_min_iv_rv_ratio", 1.10) or 0.0)
    falling_iv_min = float(config.get("vol_regime_falling_iv_pct_min", 25) or 0.0)
    falling_iv_max = float(config.get("vol_regime_falling_iv_pct_max", 85) or 100.0)
    falling_iv_trend = float(config.get("vol_regime_falling_iv_trend", -0.01) or -0.01)
    falling_rv_max = float(config.get("vol_regime_falling_rv_trend_max", 0.01) or 0.0)
    if (
        pd.notna(iv_pct) and falling_iv_min <= float(iv_pct) <= falling_iv_max and
        pd.notna(spread) and spread >= min_spread and
        pd.notna(ratio) and ratio >= min_ratio and
        pd.notna(iv_trend) and iv_trend <= falling_iv_trend and
        (pd.isna(rv_trend) or rv_trend <= falling_rv_max)
    ):
        return FALLING_VOL_CARRY

    high_iv_pct = float(config.get("vol_regime_high_iv_pct", 75) or 75)
    if pd.notna(iv_pct) and iv_pct >= high_iv_pct:
        return HIGH_RISING_VOL

    low_iv_pct = float(config.get("vol_regime_low_iv_pct", 45) or 45)
    max_rv_trend = float(config.get("vol_regime_max_low_rv_trend", 0.02) or 0.0)
    max_iv_trend = float(config.get("vol_regime_max_low_iv_trend", 0.00) or 0.0)
    if (
        pd.notna(iv_pct) and iv_pct <= low_iv_pct and
        pd.notna(spread) and spread >= min_spread and
        pd.notna(ratio) and ratio >= min_ratio and
        (pd.isna(rv_trend) or rv_trend <= max_rv_trend) and
        (pd.isna(iv_trend) or iv_trend <= max_iv_trend)
    ):
        return LOW_STABLE_VOL

    return NORMAL_VOL


def reentry_plan_blocks(config, *, plan, strat, product, date_str, iv_history,
                        current_iv_state, base_regime=None):
    if not plan:
        return False
    if date_str < plan.get("earliest_date", date_str):
        return True
    if (
        reentry_requires_daily_iv_drop(config, strat) and
        not product_iv_turns_lower(iv_history, product)
    ):
        return True
    if reentry_requires_falling_regime(config, strat):
        if base_regime is None:
            base_regime = classify_product_vol_regime_base(
                config,
                current_iv_state.get(product, {}),
            )
        return base_regime != FALLING_VOL_CARRY
    return False


def has_active_reentry_plan(config, *, product, date_str, reentry_plans,
                            iv_history, current_iv_state, normalize_product_key,
                            base_regime=None):
    product = normalize_product_key(product)
    for (strat, plan_product), plan in reentry_plans.items():
        if normalize_product_key(plan_product) != product:
            continue
        if reentry_plan_blocks(
            config,
            plan=plan,
            strat=strat,
            product=product,
            date_str=date_str,
            iv_history=iv_history,
            current_iv_state=current_iv_state,
            base_regime=base_regime,
        ):
            return True
    return False


def classify_product_vol_regime(config, *, product, state, date_str,
                                reentry_plans, iv_history, current_iv_state,
                                normalize_product_key):
    base_regime = classify_product_vol_regime_base(config, state)
    if has_active_reentry_plan(
        config,
        product=product,
        date_str=date_str,
        reentry_plans=reentry_plans,
        iv_history=iv_history,
        current_iv_state=current_iv_state,
        normalize_product_key=normalize_product_key,
        base_regime=base_regime,
    ):
        return POST_STOP_COOLDOWN
    return base_regime


def is_structural_low_iv_product(config, *, product, iv_history,
                                 current_iv_state, normalize_product_key,
                                 state=None):
    product = normalize_product_key(product)
    allowed = {
        normalize_product_key(p)
        for p in config.get("low_iv_allowed_products", [])
        if str(p).strip()
    }
    if product in allowed:
        return True
    if not config.get("low_iv_structural_auto_enabled", False):
        return False

    hist = iv_history.get(product)
    if not hist:
        return False
    ivs = pd.to_numeric(pd.Series(hist.get("ivs", [])), errors="coerce").dropna()
    min_history = int(config.get("low_iv_structural_min_history", 120) or 0)
    if len(ivs) < max(min_history, 20):
        return False
    window = min(len(ivs), int(config.get("iv_window", 252) or 252))
    recent = ivs.iloc[-window:]
    median_iv = float(recent.median())
    iv_std = float(recent.std(ddof=0))
    max_median_iv = float(config.get("low_iv_structural_max_median_iv", 0.24) or 0.0)
    max_iv_std = float(config.get("low_iv_structural_max_iv_std", 0.08) or 0.0)
    if max_median_iv > 0 and median_iv > max_median_iv:
        return False
    if max_iv_std > 0 and iv_std > max_iv_std:
        return False

    state = state or current_iv_state.get(product, {})
    max_current_iv_pct = config.get("low_iv_structural_max_current_iv_pct", None)
    if max_current_iv_pct is not None:
        iv_pct = state.get("iv_pct", np.nan)
        if pd.isna(iv_pct) or float(iv_pct) > float(max_current_iv_pct):
            return False
    spread = state.get("iv_rv_spread", np.nan)
    ratio = state.get("iv_rv_ratio", np.nan)
    min_spread = float(config.get("low_iv_min_iv_rv_spread", 0.02) or 0.0)
    min_ratio = float(config.get("low_iv_min_iv_rv_ratio", 1.10) or 0.0)
    if pd.isna(spread) or spread < min_spread:
        return False
    if pd.isna(ratio) or ratio < min_ratio:
        return False
    return True


def refresh_vol_regime_state(config, *, current_iv_state, reentry_plans,
                             iv_history, normalize_product_key, date_str):
    regimes = {}
    for product, state in current_iv_state.items():
        regimes[product] = classify_product_vol_regime(
            config,
            product=product,
            state=state,
            date_str=date_str,
            reentry_plans=reentry_plans,
            iv_history=iv_history,
            current_iv_state=current_iv_state,
            normalize_product_key=normalize_product_key,
        )
        state["is_structural_low_iv"] = is_structural_low_iv_product(
            config,
            product=product,
            iv_history=iv_history,
            current_iv_state=current_iv_state,
            normalize_product_key=normalize_product_key,
            state=state,
        )

    counts = Counter(regimes.values())
    active = sum(counts.get(k, 0) for k in (
        FALLING_VOL_CARRY,
        LOW_STABLE_VOL,
        NORMAL_VOL,
        HIGH_RISING_VOL,
    ))
    high_ratio = counts.get(HIGH_RISING_VOL, 0) / active if active else 0.0
    low_ratio = counts.get(LOW_STABLE_VOL, 0) / active if active else 0.0
    falling_ratio = counts.get(FALLING_VOL_CARRY, 0) / active if active else 0.0
    release_ratio = (
        (counts.get(FALLING_VOL_CARRY, 0) + counts.get(LOW_STABLE_VOL, 0)) / active
        if active else 0.0
    )

    count_post_stop_as_high = bool(config.get("vol_regime_count_post_stop_as_high", False))
    if (
        count_post_stop_as_high and
        counts.get(POST_STOP_COOLDOWN, 0) >= int(config.get("vol_regime_portfolio_stop_count", 3) or 3)
    ):
        portfolio_regime = HIGH_RISING_VOL
    elif (
        config.get("vol_regime_portfolio_falling_release_enabled", False) and
        counts.get(FALLING_VOL_CARRY, 0) >= int(
            config.get("vol_regime_portfolio_falling_release_min_products", 1) or 1
        ) and
        release_ratio >= float(config.get("vol_regime_portfolio_falling_release_ratio", 0.30) or 0.30) and
        high_ratio <= float(
            config.get("vol_regime_portfolio_falling_release_high_ratio_max", 0.35) or 0.35
        )
    ):
        portfolio_regime = FALLING_VOL_CARRY
    elif high_ratio >= float(config.get("vol_regime_portfolio_high_ratio", 0.25) or 0.25):
        portfolio_regime = HIGH_RISING_VOL
    elif falling_ratio >= float(config.get("vol_regime_portfolio_falling_ratio", 0.25) or 0.25):
        portfolio_regime = FALLING_VOL_CARRY
    elif (
        active > 0 and
        low_ratio >= float(config.get("vol_regime_portfolio_low_ratio", 0.50) or 0.50) and
        counts.get(HIGH_RISING_VOL, 0) == 0
    ):
        portfolio_regime = LOW_STABLE_VOL
    else:
        portfolio_regime = NORMAL_VOL

    return regimes, counts, portfolio_regime


def product_margin_per_multiplier(config, *, product, current_vol_regimes,
                                  iv_history, current_iv_state,
                                  normalize_product_key):
    if not config.get("vol_regime_sizing_enabled", False):
        return 1.0
    regime = current_vol_regimes.get(product, NORMAL_VOL)
    structural = is_structural_low_iv_product(
        config,
        product=product,
        iv_history=iv_history,
        current_iv_state=current_iv_state,
        normalize_product_key=normalize_product_key,
    )
    structural_requires_low = bool(config.get("low_iv_structural_require_low_stable", True))
    if regime == FALLING_VOL_CARRY:
        return float(config.get("s1_falling_vol_margin_per_mult", 1.50) or 1.0)
    if regime == LOW_STABLE_VOL:
        mult = float(config.get("vol_regime_low_margin_per_mult", 1.12) or 1.0)
        if structural:
            structural_mult = float(config.get("low_iv_structural_margin_per_mult", 1.25) or mult)
            mult = max(mult, structural_mult)
        return mult
    if regime == HIGH_RISING_VOL:
        return float(config.get("vol_regime_high_margin_per_mult", 0.30) or 1.0)
    if regime == POST_STOP_COOLDOWN:
        return float(config.get("vol_regime_post_stop_margin_per_mult", 0.0) or 0.0)
    mult = float(config.get("vol_regime_normal_margin_per_mult", 1.0) or 1.0)
    if structural and not structural_requires_low:
        structural_mult = float(config.get("low_iv_structural_margin_per_mult", 1.25) or mult)
        mult = max(mult, structural_mult)
    return mult


def passes_s1_risk_release_entry(config, *, product, iv_state,
                                 current_vol_regimes, iv_history):
    if not config.get("s1_require_risk_release_entry", False):
        return True

    regime = current_vol_regimes.get(product)
    if regime in (HIGH_RISING_VOL, POST_STOP_COOLDOWN):
        return False

    iv_pct = iv_state.get("iv_pct", np.nan)
    spread = iv_state.get("iv_rv_spread", np.nan)
    ratio = iv_state.get("iv_rv_ratio", np.nan)
    rv_trend = iv_state.get("rv_trend", np.nan)
    iv_trend = iv_state.get("iv_trend", np.nan)
    allow_structural_low = bool(config.get("s1_risk_release_allow_structural_low", False))
    is_structural_low = bool(iv_state.get("is_structural_low_iv", False))

    if config.get("s1_risk_release_require_falling_regime", False):
        if regime != FALLING_VOL_CARRY:
            if not (allow_structural_low and is_structural_low and regime == LOW_STABLE_VOL):
                return False

    min_spread = float(config.get(
        "s1_risk_release_min_iv_rv_spread",
        config.get("vol_regime_min_iv_rv_spread", 0.02),
    ) or 0.0)
    min_ratio = float(config.get(
        "s1_risk_release_min_iv_rv_ratio",
        config.get("vol_regime_min_iv_rv_ratio", 1.10),
    ) or 0.0)
    if pd.isna(spread) or float(spread) < min_spread:
        return False
    if pd.isna(ratio) or float(ratio) < min_ratio:
        return False

    min_iv_pct = config.get("s1_risk_release_min_iv_pct", None)
    max_iv_pct = config.get("s1_risk_release_max_iv_pct", None)
    if min_iv_pct is not None and not (allow_structural_low and is_structural_low):
        if pd.isna(iv_pct) or float(iv_pct) < float(min_iv_pct):
            return False
    if max_iv_pct is not None:
        if pd.isna(iv_pct) or float(iv_pct) > float(max_iv_pct):
            return False

    max_iv_trend = float(config.get("s1_risk_release_max_iv_trend", -0.005) or 0.0)
    if pd.isna(iv_trend) or float(iv_trend) > max_iv_trend:
        return False
    if bool(config.get("s1_risk_release_require_daily_iv_drop", True)):
        if not product_iv_turns_lower(iv_history, product):
            return False

    require_rv_trend = bool(config.get("s1_risk_release_require_rv_trend", True))
    max_rv_trend = float(config.get(
        "s1_risk_release_max_rv_trend",
        config.get("s1_entry_max_rv_trend", 0.0),
    ) or 0.0)
    if pd.isna(rv_trend):
        return not require_rv_trend
    if float(rv_trend) > max_rv_trend:
        return False
    return True


def passes_s1_falling_framework_entry(config, *, product, iv_state,
                                      current_vol_regimes, iv_history):
    if not config.get("s1_falling_framework_enabled", False):
        return True

    spread = iv_state.get("iv_rv_spread", np.nan)
    ratio = iv_state.get("iv_rv_ratio", np.nan)
    min_spread = float(config.get("vol_regime_min_iv_rv_spread", 0.02) or 0.0)
    min_ratio = float(config.get("vol_regime_min_iv_rv_ratio", 1.10) or 0.0)
    if pd.isna(spread) or spread < min_spread:
        return False
    if pd.isna(ratio) or ratio < min_ratio:
        return False

    if bool(config.get("s1_entry_check_vol_trend", True)):
        rv_trend = iv_state.get("rv_trend", np.nan)
        iv_trend = iv_state.get("iv_trend", np.nan)
        max_rv_trend = float(config.get(
            "s1_entry_max_rv_trend",
            config.get("vol_regime_max_low_rv_trend", 0.02),
        ) or 0.0)
        max_iv_trend = float(config.get("s1_entry_max_iv_trend", 0.0) or 0.0)
        if pd.notna(rv_trend) and rv_trend > max_rv_trend:
            return False
        if pd.notna(iv_trend) and iv_trend > max_iv_trend:
            return False

    if (
        bool(config.get("s1_entry_block_high_rising_regime", True)) and
        current_vol_regimes.get(product) in (HIGH_RISING_VOL, POST_STOP_COOLDOWN)
    ):
        return False
    return passes_s1_risk_release_entry(
        config,
        product=product,
        iv_state=iv_state,
        current_vol_regimes=current_vol_regimes,
        iv_history=iv_history,
    )


def recent_stop_count(config, stop_history, date_str):
    lookback_days = int(config.get("portfolio_stop_cluster_lookback_days", 5) or 0)
    if lookback_days <= 0 or not date_str:
        return 0
    cur_ts = pd.Timestamp(date_str)
    count = 0
    for dates in stop_history.values():
        for stop_date in dates:
            try:
                if (cur_ts - pd.Timestamp(stop_date)).days <= lookback_days:
                    count += 1
            except (TypeError, ValueError):
                continue
    return count


def prioritize_products_by_regime(config, products, current_vol_regimes):
    if (
        not config.get("s1_falling_framework_enabled", False) or
        not config.get("s1_prioritize_products_by_regime", True)
    ):
        return list(products)
    order = {p: i for i, p in enumerate(products)}
    return sorted(
        list(products),
        key=lambda p: (
            REGIME_PRIORITY.get(current_vol_regimes.get(p, NORMAL_VOL), REGIME_PRIORITY[NORMAL_VOL]),
            order.get(p, 0),
        ),
    )


def should_trigger_premium_stop(config, pos, product_iv_pcts, iv_history):
    multiple = float(config.get("premium_stop_multiple", 0.0) or 0.0)
    if multiple <= 0 or pos.cur_price < pos.open_price * multiple:
        return False
    require_daily_iv = bool(config.get("premium_stop_requires_daily_iv_non_decrease", True))
    if not require_daily_iv:
        return True
    if not product_iv_pcts or pos.product not in product_iv_pcts:
        return True
    return product_iv_not_falling(iv_history, pos.product)
