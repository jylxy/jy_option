"""S1 short-leg contract scoring and selection rules."""

from collections import defaultdict

import numpy as np
import pandas as pd

from margin_model import estimate_margin, resolve_margin_ratio
from option_calc import calc_option_price_batch


def _stable_rank(df, sort_cols, ascending):
    """Return a deterministic ranking with option_code as final tie-breaker."""
    if df is None or df.empty:
        return None
    work = df.copy()
    cols = list(sort_cols)
    orders = list(ascending)
    if "option_code" in work.columns and "option_code" not in cols:
        cols.append("option_code")
        orders.append(True)
    return work.sort_values(cols, ascending=orders, kind="mergesort")


def _pct_rank_high(series, fill_value=0.0):
    """Return percentile ranks where larger raw values are better."""
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(fill_value, index=series.index, dtype=float)
    return values.rank(method="average", pct=True).fillna(fill_value)


def _pct_rank_low(series, fill_value=0.0):
    """Return percentile ranks where smaller raw values are better."""
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(fill_value, index=series.index, dtype=float)
    return (1.0 - values.rank(method="average", pct=True)).fillna(fill_value)


def _safe_ratio_series(numerator, denominator):
    num = pd.to_numeric(numerator, errors="coerce").replace([np.inf, -np.inf], np.nan)
    den = pd.to_numeric(denominator, errors="coerce").replace([np.inf, -np.inf], np.nan)
    den = den.where(den.abs() > 1e-12, np.nan)
    return (num / den).replace([np.inf, -np.inf], np.nan)


def _numeric_column(frame, col, default=np.nan):
    if col in frame.columns:
        return pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return pd.Series(default, index=frame.index, dtype=float)


def _neutral_rank_high(series):
    return _pct_rank_high(series, fill_value=0.10).clip(0.0, 1.0)


def _neutral_rank_low(series):
    return _pct_rank_low(series, fill_value=0.10).clip(0.0, 1.0)


def _b6_rank_high(series):
    return _pct_rank_high(series, fill_value=0.50).clip(0.0, 1.0)


def _b6_rank_low(series):
    return _pct_rank_low(series, fill_value=0.50).clip(0.0, 1.0)


def _resolve_candidate_rv_ref(frame, iv_series):
    """Infer a same-day RV reference without using future data."""
    iv = pd.to_numeric(iv_series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    candidates = []

    for col in ("rv_ref", "entry_rv_ref", "rv"):
        if col in frame.columns:
            candidates.append(pd.to_numeric(frame[col], errors="coerce"))

    if "entry_iv_rv_spread" in frame.columns:
        spread = pd.to_numeric(frame["entry_iv_rv_spread"], errors="coerce")
        candidates.append(iv - spread)
    elif "iv_rv_spread" in frame.columns:
        spread = pd.to_numeric(frame["iv_rv_spread"], errors="coerce")
        candidates.append(iv - spread)

    if "entry_iv_rv_ratio" in frame.columns:
        ratio = pd.to_numeric(frame["entry_iv_rv_ratio"], errors="coerce")
        candidates.append(iv / ratio.replace(0.0, np.nan))
    elif "iv_rv_ratio" in frame.columns:
        ratio = pd.to_numeric(frame["iv_rv_ratio"], errors="coerce")
        candidates.append(iv / ratio.replace(0.0, np.nan))

    if not candidates:
        return pd.Series(np.nan, index=frame.index, dtype=float)

    rv = pd.concat(candidates, axis=1).replace([np.inf, -np.inf], np.nan).max(axis=1, skipna=True)
    return rv.where((rv > 0.0) & (rv < 5.0))


def _add_s1_premium_quality_fields(frame, option_type, mult, roundtrip_fee,
                                   theta_cash, stress_spot_move_pct=0.03,
                                   exchange=None, product=None):
    """Add B2 premium-quality diagnostics without changing trading decisions."""
    if frame is None or frame.empty:
        return frame

    c = frame
    mult = float(mult)
    roundtrip_fee = float(roundtrip_fee or 0.0)
    opt = str(option_type or "").upper()[:1]

    spot = _numeric_column(c, "spot_close")
    strike = _numeric_column(c, "strike")
    dte = _numeric_column(c, "dte")
    option_price = _numeric_column(c, "option_close")
    iv = _numeric_column(c, "implied_vol")
    if iv.notna().sum() == 0:
        iv = _numeric_column(c, "contract_iv")
    gross_premium_cash = option_price * mult
    net_premium_cash = pd.to_numeric(c["net_premium_cash"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    net_premium_unit = net_premium_cash / mult if mult > 0 else pd.Series(np.nan, index=c.index)

    c["gross_premium_cash"] = gross_premium_cash
    c["premium_yield_margin"] = (
        _safe_ratio_series(net_premium_cash, c["margin"])
        * (252.0 / dte.replace(0.0, np.nan))
    )
    c["premium_yield_notional"] = (
        _safe_ratio_series(net_premium_cash, spot * mult)
        * (252.0 / dte.replace(0.0, np.nan))
    )

    if opt == "P":
        breakeven = strike - net_premium_unit
        cushion_abs = spot - breakeven
    else:
        breakeven = strike + net_premium_unit
        cushion_abs = breakeven - spot
    c["breakeven_price"] = breakeven
    c["breakeven_cushion_abs"] = cushion_abs

    rv_ref = _resolve_candidate_rv_ref(c, iv)
    c["rv_ref"] = rv_ref
    c["iv_rv_spread_candidate"] = iv - rv_ref
    c["iv_rv_ratio_candidate"] = _safe_ratio_series(iv, rv_ref)
    c["variance_carry"] = iv * iv - rv_ref * rv_ref

    sqrt_year = np.sqrt((dte / 252.0).where(dte > 0.0))
    implied_move = spot * iv * sqrt_year
    realized_move = spot * rv_ref * sqrt_year
    c["breakeven_cushion_iv"] = _safe_ratio_series(cushion_abs, implied_move)
    c["breakeven_cushion_rv"] = _safe_ratio_series(cushion_abs, realized_move)

    price_frame = c.copy()
    price_frame["implied_vol"] = iv
    if "exchange" not in price_frame.columns:
        price_frame["exchange"] = exchange
    if "product" not in price_frame.columns:
        price_frame["product"] = product
    required_price_cols = {"spot_close", "strike", "dte", "implied_vol", "option_type"}
    if required_price_cols.issubset(set(price_frame.columns)):
        base_model_price = calc_option_price_batch(price_frame)
        iv5_price = calc_option_price_batch(price_frame, iv_shift=0.05)
        iv10_price = calc_option_price_batch(price_frame, iv_shift=0.10)
    else:
        base_model_price = option_price
        iv5_price = pd.Series(np.nan, index=c.index)
        iv10_price = pd.Series(np.nan, index=c.index)
    vega_cash = _numeric_column(c, "vega", 0.0).abs() * mult
    iv5_loss = ((iv5_price - base_model_price).clip(lower=0.0) * mult).replace([np.inf, -np.inf], np.nan)
    iv10_loss = ((iv10_price - base_model_price).clip(lower=0.0) * mult).replace([np.inf, -np.inf], np.nan)
    c["iv_shock_loss_5_cash"] = iv5_loss.fillna(vega_cash * 5.0)
    c["iv_shock_loss_10_cash"] = iv10_loss.fillna(vega_cash * 10.0)
    vomma_cash = (c["iv_shock_loss_10_cash"] - 2.0 * c["iv_shock_loss_5_cash"]).clip(lower=0.0)
    c["b3_vomma_cash"] = vomma_cash.replace([np.inf, -np.inf], np.nan)
    c["b3_vomma_loss_ratio"] = _safe_ratio_series(c["b3_vomma_cash"], net_premium_cash)
    c["premium_to_iv5_loss"] = _safe_ratio_series(net_premium_cash, c["iv_shock_loss_5_cash"])
    c["premium_to_iv10_loss"] = _safe_ratio_series(net_premium_cash, c["iv_shock_loss_10_cash"])
    c["premium_to_stress_loss"] = _safe_ratio_series(net_premium_cash, c["stress_loss"])

    theta_cash = pd.to_numeric(theta_cash, errors="coerce").replace([np.inf, -np.inf], np.nan)
    c["theta_vega_efficiency"] = _safe_ratio_series(theta_cash, vega_cash)
    gamma = _numeric_column(c, "gamma", 0.0).abs()
    spot_for_gamma = pd.to_numeric(c.get("spot_close", c.get("spot", np.nan)), errors="coerce")
    gamma_cash_unscaled = gamma * mult * spot_for_gamma * spot_for_gamma
    c["b5_theta_per_vega"] = _safe_ratio_series(theta_cash, vega_cash)
    c["b5_premium_per_vega"] = _safe_ratio_series(net_premium_cash, vega_cash)
    c["b5_theta_per_gamma"] = _safe_ratio_series(theta_cash, gamma_cash_unscaled)
    c["b5_gamma_theta_ratio"] = _safe_ratio_series(gamma_cash_unscaled, theta_cash)
    gamma_shock_pct = max(float(stress_spot_move_pct or 0.03), 0.0)
    gamma_rent_cash = 0.5 * gamma * (spot * gamma_shock_pct) ** 2 * mult
    c["gamma_rent_cash"] = gamma_rent_cash.replace([np.inf, -np.inf], np.nan)
    c["gamma_rent_penalty"] = _safe_ratio_series(c["gamma_rent_cash"], net_premium_cash)

    c["fee_ratio"] = _safe_ratio_series(pd.Series(roundtrip_fee, index=c.index), gross_premium_cash)
    c["slippage_ratio"] = 0.0
    c["friction_ratio"] = c["fee_ratio"].fillna(0.0) + c["slippage_ratio"]

    c["iv_rv_carry_score"] = _neutral_rank_high(c["variance_carry"])
    c["breakeven_cushion_score"] = (
        0.5 * _neutral_rank_high(c["breakeven_cushion_iv"])
        + 0.5 * _neutral_rank_high(c["breakeven_cushion_rv"])
    )
    c["premium_to_iv_shock_score"] = (
        0.5 * _neutral_rank_high(c["premium_to_iv5_loss"])
        + 0.5 * _neutral_rank_high(c["premium_to_iv10_loss"])
    )
    c["premium_to_stress_loss_score"] = _neutral_rank_high(c["premium_to_stress_loss"])
    c["theta_vega_efficiency_score"] = _neutral_rank_high(c["theta_vega_efficiency"])
    liquidity_score = pd.to_numeric(
        c.get("liquidity_score", pd.Series(0.10, index=c.index)),
        errors="coerce",
    ).fillna(0.10).clip(0.0, 1.0)
    c["cost_liquidity_score"] = (
        0.5 * _neutral_rank_low(c["friction_ratio"])
        + 0.5 * liquidity_score
    )
    raw_score = (
        0.25 * c["iv_rv_carry_score"]
        + 0.20 * c["breakeven_cushion_score"]
        + 0.20 * c["premium_to_iv_shock_score"]
        + 0.15 * c["premium_to_stress_loss_score"]
        + 0.10 * c["theta_vega_efficiency_score"]
        + 0.10 * c["cost_liquidity_score"]
    )
    c["premium_quality_score"] = (raw_score * 100.0).clip(0.0, 100.0)
    c["premium_quality_rank_in_side"] = _neutral_rank_high(c["premium_quality_score"])
    return c


def _apply_s1_b4_contract_ranking(frame, b4_params=None):
    """Apply B4 role-aware contract score and optional friction-only hard gates."""
    if frame is None or frame.empty:
        return frame
    params = b4_params or {}
    c = frame.copy()

    if bool(params.get("hard_filter_enabled", False)):
        min_net_premium = float(params.get("min_net_premium_cash", 0.0) or 0.0)
        max_friction = params.get("max_friction_ratio", None)
        if min_net_premium > 0 and "net_premium_cash" in c.columns:
            net_premium = pd.to_numeric(c["net_premium_cash"], errors="coerce")
            c = c[net_premium >= min_net_premium].copy()
        if max_friction is not None and "friction_ratio" in c.columns:
            try:
                max_friction = float(max_friction)
            except (TypeError, ValueError):
                max_friction = np.nan
            if np.isfinite(max_friction):
                friction = pd.to_numeric(c["friction_ratio"], errors="coerce")
                c = c[friction.isna() | (friction <= max_friction)].copy()
        if c.empty:
            return c

    weights = {
        "b4_premium_to_iv10_score": float(params.get("weight_premium_to_iv10", 0.30) or 0.0),
        "b4_premium_to_stress_score": float(params.get("weight_premium_to_stress", 0.25) or 0.0),
        "b4_premium_yield_margin_score": float(params.get("weight_premium_yield_margin", 0.20) or 0.0),
        "b4_gamma_rent_score": float(params.get("weight_gamma_rent", 0.15) or 0.0),
        "b4_vomma_score": float(params.get("weight_vomma", 0.10) or 0.0),
    }
    weight_sum = sum(max(0.0, w) for w in weights.values())
    if weight_sum <= 0:
        c["b4_contract_score"] = c.get("premium_quality_score", 50.0)
        return c

    c["b4_premium_to_iv10_score"] = 100.0 * _neutral_rank_high(c.get("premium_to_iv10_loss"))
    c["b4_premium_to_stress_score"] = 100.0 * _neutral_rank_high(c.get("premium_to_stress_loss"))
    c["b4_premium_yield_margin_score"] = 100.0 * _neutral_rank_high(c.get("premium_yield_margin"))
    c["b4_gamma_rent_score"] = 100.0 * _neutral_rank_low(c.get("gamma_rent_penalty"))
    c["b4_vomma_score"] = 100.0 * _neutral_rank_low(c.get("b3_vomma_loss_ratio"))
    c["b4_breakeven_cushion_score"] = 100.0 * _neutral_rank_high(c.get("breakeven_cushion_score"))
    if "b3_vol_of_vol_proxy" in c.columns:
        c["b4_vol_of_vol_score"] = 100.0 * _neutral_rank_low(c.get("b3_vol_of_vol_proxy"))
    else:
        c["b4_vol_of_vol_score"] = np.nan

    score = pd.Series(0.0, index=c.index, dtype=float)
    for column, weight in weights.items():
        weight = max(0.0, float(weight or 0.0))
        if weight <= 0:
            continue
        score += weight * pd.to_numeric(c[column], errors="coerce").fillna(50.0)
    c["b4_contract_score_raw"] = (score / weight_sum).clip(0.0, 100.0)
    penalty = pd.Series(0.0, index=c.index, dtype=float)
    if bool(params.get("breakeven_penalty_enabled", False)):
        rank = pd.to_numeric(c["b4_breakeven_cushion_score"], errors="coerce")
        very_low = float(params.get("breakeven_penalty_rank_very_low", 15.0) or 15.0)
        low = float(params.get("breakeven_penalty_rank_low", 30.0) or 30.0)
        very_low_points = float(params.get("breakeven_penalty_points_very_low", 20.0) or 20.0)
        low_points = float(params.get("breakeven_penalty_points_low", 10.0) or 10.0)
        penalty += np.where(rank < very_low, very_low_points, np.where(rank < low, low_points, 0.0))
    if bool(params.get("vov_penalty_enabled", False)):
        rank = pd.to_numeric(c["b4_vol_of_vol_score"], errors="coerce")
        very_low = float(params.get("vov_penalty_rank_very_low", 15.0) or 15.0)
        low = float(params.get("vov_penalty_rank_low", 30.0) or 30.0)
        very_low_points = float(params.get("vov_penalty_points_very_low", 20.0) or 20.0)
        low_points = float(params.get("vov_penalty_points_low", 10.0) or 10.0)
        penalty += np.where(rank < very_low, very_low_points, np.where(rank < low, low_points, 0.0))
    c["b4_contract_penalty_points"] = pd.Series(penalty, index=c.index, dtype=float)
    c["b4_contract_score"] = (c["b4_contract_score_raw"] - c["b4_contract_penalty_points"]).clip(0.0, 100.0)
    c["quality_score"] = c["b4_contract_score"]
    return c


def _apply_s1_b6_contract_ranking(frame, b6_params=None):
    """Apply B6 residual-quality contract score with neutral missing-factor ranks."""
    if frame is None or frame.empty:
        return frame
    params = b6_params or {}
    c = frame.copy()

    if bool(params.get("hard_filter_enabled", False)):
        min_net_premium = float(params.get("min_net_premium_cash", 0.0) or 0.0)
        max_friction = params.get("max_friction_ratio", None)
        if min_net_premium > 0 and "net_premium_cash" in c.columns:
            net_premium = pd.to_numeric(c["net_premium_cash"], errors="coerce")
            c = c[net_premium >= min_net_premium].copy()
        if max_friction is not None and "friction_ratio" in c.columns:
            try:
                max_friction = float(max_friction)
            except (TypeError, ValueError):
                max_friction = np.nan
            if np.isfinite(max_friction):
                friction = pd.to_numeric(c["friction_ratio"], errors="coerce")
                c = c[friction.isna() | (friction <= max_friction)].copy()
        if c.empty:
            return c

    if "b5_theta_per_vega" not in c.columns:
        c["b5_theta_per_vega"] = c.get("theta_vega_efficiency", np.nan)
    if "b5_theta_per_gamma" not in c.columns:
        c["b5_theta_per_gamma"] = np.nan
    if "b5_premium_to_tail_move_loss" not in c.columns:
        c["b5_premium_to_tail_move_loss"] = np.nan

    def col(name):
        return c[name] if name in c.columns else pd.Series(np.nan, index=c.index, dtype=float)

    c["b6_premium_to_stress_score"] = 100.0 * _b6_rank_high(col("premium_to_stress_loss"))
    c["b6_premium_to_iv10_score"] = 100.0 * _b6_rank_high(col("premium_to_iv10_loss"))
    c["b6_theta_per_vega_score"] = 100.0 * _b6_rank_high(col("b5_theta_per_vega"))
    c["b6_theta_per_gamma_score"] = 100.0 * _b6_rank_high(col("b5_theta_per_gamma"))
    c["b6_tail_move_coverage_score"] = 100.0 * _b6_rank_high(col("b5_premium_to_tail_move_loss"))
    c["b6_vomma_score"] = 100.0 * _b6_rank_low(col("b3_vomma_loss_ratio"))
    c["b6_premium_yield_margin_score"] = 100.0 * _b6_rank_high(col("premium_yield_margin"))

    weights = {
        "b6_premium_to_stress_score": float(params.get("weight_premium_to_stress", 0.24) or 0.0),
        "b6_premium_to_iv10_score": float(params.get("weight_premium_to_iv10", 0.22) or 0.0),
        "b6_theta_per_vega_score": float(params.get("weight_theta_per_vega", 0.22) or 0.0),
        "b6_theta_per_gamma_score": float(params.get("weight_theta_per_gamma", 0.12) or 0.0),
        "b6_tail_move_coverage_score": float(params.get("weight_tail_move_coverage", 0.10) or 0.0),
        "b6_vomma_score": float(params.get("weight_vomma", 0.06) or 0.0),
        "b6_premium_yield_margin_score": float(params.get("weight_premium_yield_margin", 0.04) or 0.0),
    }
    weight_sum = sum(max(0.0, w) for w in weights.values())
    missing_score = float(params.get("missing_factor_score", 50.0) or 50.0)
    if weight_sum <= 0:
        c["b6_contract_score"] = c.get("premium_quality_score", missing_score)
    else:
        score = pd.Series(0.0, index=c.index, dtype=float)
        for column, weight in weights.items():
            weight = max(0.0, float(weight or 0.0))
            if weight <= 0:
                continue
            score += weight * pd.to_numeric(c[column], errors="coerce").fillna(missing_score)
        c["b6_contract_score"] = (score / weight_sum).clip(0.0, 100.0)
    c["quality_score"] = c["b6_contract_score"]
    return c


def calc_s1_stress_loss(row, option_type, mult, spot_move_pct=0.03,
                        iv_up_points=5.0, premium_loss_multiple=0.0):
    """Estimate one-contract short-option stress loss with delta/gamma/vega."""
    spot = float(row.get("spot_close", 0.0) or 0.0)
    if spot <= 0:
        return np.nan
    delta = float(row.get("delta", 0.0) or 0.0)
    gamma = float(row.get("gamma", 0.0) or 0.0)
    vega = float(row.get("vega", 0.0) or 0.0)
    move = max(float(spot_move_pct or 0.0), 0.0)
    ds = -spot * move if option_type == "P" else spot * move
    long_change = delta * ds + 0.5 * gamma * ds * ds + vega * float(iv_up_points or 0.0)
    greek_loss = max(float(long_change), 0.0) * float(mult)
    premium_loss_multiple = max(float(premium_loss_multiple or 0.0), 0.0)
    if premium_loss_multiple <= 0:
        return greek_loss
    premium = float(row.get("option_close", 0.0) or 0.0) * float(mult)
    return max(greek_loss, premium * premium_loss_multiple)


def s1_forward_vega_quality_filter(candidates, option_type, *, iv_state=None,
                                   side_meta=None, config=None):
    """Filter S1 candidates whose wing IV quality does not support short vega."""
    stats = defaultdict(float)
    if candidates is None or candidates.empty:
        return candidates, stats

    cfg = config or {}
    if not cfg.get("s1_forward_vega_filter_enabled", False):
        return candidates, stats

    df = candidates.copy()
    mask = pd.Series(True, index=df.index)
    policy = str(cfg.get("s1_forward_vega_missing_policy", "skip") or "skip").lower()

    def finite_number(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return np.nan
        return value if np.isfinite(value) else np.nan

    def numeric_col(name, default=np.nan):
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(default, index=df.index, dtype=float)

    def apply_rule(rule_ok, key):
        nonlocal mask
        rule_ok = pd.Series(rule_ok, index=df.index).fillna(False)
        failed = mask & ~rule_ok
        stats[key] += float(failed.sum())
        mask = mask & rule_ok

    def threshold_rule(values, max_value):
        values = pd.to_numeric(values, errors="coerce")
        known_ok = values.notna() & (values <= float(max_value))
        missing_ok = values.isna() & (policy != "skip")
        return known_ok | missing_ok

    lookback = max(1, int(cfg.get("s1_forward_vega_contract_iv_lookback", 5) or 1))
    contract_change_col = f"contract_iv_change_{lookback}d"
    if contract_change_col in df.columns:
        contract_iv_change = numeric_col(contract_change_col)
    else:
        contract_iv_change = numeric_col("contract_iv_change_1d")
    df["contract_iv_change_for_vega"] = contract_iv_change

    atm_iv = finite_number((iv_state or {}).get("atm_iv", np.nan))
    atm_trend = finite_number((iv_state or {}).get("iv_trend", np.nan))
    rv_trend = finite_number((iv_state or {}).get("rv_trend", np.nan))
    contract_iv = numeric_col("contract_iv")
    if np.isfinite(atm_iv):
        df["contract_iv_skew_to_atm"] = contract_iv - atm_iv
    else:
        df["contract_iv_skew_to_atm"] = np.nan
    if np.isfinite(atm_trend):
        df["contract_skew_change_for_vega"] = contract_iv_change - atm_trend
    else:
        df["contract_skew_change_for_vega"] = np.nan

    if cfg.get("s1_forward_vega_require_contract_iv_falling", True):
        apply_rule(
            threshold_rule(
                contract_iv_change,
                cfg.get("s1_forward_vega_contract_iv_max_change", 0.0),
            ),
            "skip_forward_vega_contract_iv",
        )

    if cfg.get("s1_forward_vega_require_atm_iv_not_rising", True):
        if np.isfinite(atm_trend):
            rule = pd.Series(
                atm_trend <= float(cfg.get("s1_forward_vega_atm_iv_max_trend", 0.0) or 0.0),
                index=df.index,
            )
        else:
            rule = pd.Series(policy != "skip", index=df.index)
        apply_rule(rule, "skip_forward_vega_atm_iv")

    if cfg.get("s1_forward_vega_require_rv_not_rising", True):
        if np.isfinite(rv_trend):
            rule = pd.Series(
                rv_trend <= float(cfg.get("s1_forward_vega_rv_max_trend", 0.01) or 0.0),
                index=df.index,
            )
        else:
            rule = pd.Series(policy != "skip", index=df.index)
        apply_rule(rule, "skip_forward_vega_rv")

    if cfg.get("s1_forward_vega_require_skew_not_steepening", True):
        apply_rule(
            threshold_rule(
                df["contract_skew_change_for_vega"],
                cfg.get("s1_forward_vega_max_skew_steepen", 0.005),
            ),
            "skip_forward_vega_skew",
        )

    if cfg.get("s1_forward_vega_require_contract_price_not_rising", False):
        price_change = numeric_col("contract_price_change_1d")
        apply_rule(
            threshold_rule(
                price_change,
                cfg.get("s1_forward_vega_contract_price_max_change", 0.10),
            ),
            "skip_forward_vega_price",
        )

    if cfg.get("s1_forward_vega_block_structural_low_breakout", True):
        iv_state = iv_state or {}
        side_meta = side_meta or {}
        regime = str(iv_state.get("vol_regime", "") or "").lower()
        structural_low = bool(iv_state.get("is_structural_low_iv", False))
        if structural_low and not regime.startswith("falling"):
            block = False
            if np.isfinite(rv_trend):
                max_rv = float(
                    cfg.get("s1_forward_vega_structural_low_max_rv_trend", 0.0) or 0.0
                )
                block = block or rv_trend > max_rv
            if cfg.get("s1_forward_vega_structural_low_block_pressure", True):
                pressure = str(side_meta.get("trend_range_pressure", "") or "").lower()
                trend_state = str(side_meta.get("trend_state", "") or "").lower()
                confidence = finite_number(side_meta.get("trend_confidence", np.nan))
                min_conf = float(
                    cfg.get("s1_forward_vega_structural_low_min_trend_confidence", 0.35) or 0.0
                )
                opt = str(option_type or "").upper()[:1]
                call_pressure = pressure == "upper" or (
                    trend_state == "uptrend" and np.isfinite(confidence) and confidence >= min_conf
                )
                put_pressure = pressure == "lower" or (
                    trend_state == "downtrend" and np.isfinite(confidence) and confidence >= min_conf
                )
                block = block or (opt == "C" and call_pressure) or (opt == "P" and put_pressure)
            if block:
                apply_rule(pd.Series(False, index=df.index), "skip_forward_vega_vcp")

    filtered = df[mask].copy()
    stats["forward_vega_candidates_before"] += float(len(df))
    stats["forward_vega_candidates_after"] += float(len(filtered))
    return filtered, stats


def select_s1_sell(day_df, option_type, mult, mr, min_volume=0, min_oi=0,
                   iv_residual_weight=0.3, min_abs_delta=0.0,
                   max_abs_delta=0.10, target_abs_delta=None,
                   carry_metric="premium_margin", fee_per_contract=0.0,
                   roundtrip_fee_per_contract=None,
                   min_premium_fee_multiple=0.0, min_option_price=0.0,
                   use_stress_score=False,
                   stress_spot_move_pct=0.03, stress_iv_up_points=5.0,
                   stress_premium_loss_multiple=0.0,
                   gamma_penalty=0.0, vega_penalty=0.0,
                   ranking_mode="target_delta",
                   premium_stress_weight=0.55,
                   theta_stress_weight=0.25,
                   premium_margin_weight=0.15,
                   liquidity_weight=0.05,
                   delta_weight=0.0,
                   return_candidates=False, max_candidates=1,
                   exchange=None, product=None, b4_params=None):
    """Deterministic S1 sell-leg selector with optional carry/stress ranking."""
    option_price = pd.to_numeric(day_df["option_close"], errors="coerce").fillna(0)
    price_positive = option_price > 0
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P")
            & (day_df["moneyness"] < 1.0)
            & (day_df["delta"] < 0)
            & (day_df["delta"].abs() >= min_abs_delta)
            & (day_df["delta"].abs() <= max_abs_delta)
            & price_positive
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C")
            & (day_df["moneyness"] > 1.0)
            & (day_df["delta"] > 0)
            & (day_df["delta"] >= min_abs_delta)
            & (day_df["delta"] <= max_abs_delta)
            & price_positive
        ]
    if c.empty:
        return None
    min_option_price = float(min_option_price or 0.0)
    if min_option_price > 0:
        c = c[pd.to_numeric(c["option_close"], errors="coerce").fillna(0) >= min_option_price]
    if c.empty:
        return None
    roundtrip_fee = (
        float(roundtrip_fee_per_contract)
        if roundtrip_fee_per_contract is not None
        else float(fee_per_contract or 0.0) * 2.0
    )
    min_premium = roundtrip_fee * float(min_premium_fee_multiple or 0.0)
    if min_premium > 0:
        c = c[c["option_close"] * float(mult) >= min_premium]
    if c.empty:
        return None
    if min_volume > 0 and "volume" in c.columns:
        c = c[c["volume"] >= min_volume]
    if min_oi > 0 and "open_interest" in c.columns:
        c = c[c["open_interest"] >= min_oi]
    if c.empty:
        return None

    def row_margin(r):
        row_exchange = r["exchange"] if "exchange" in r.index else exchange
        row_product = r["product"] if "product" in r.index else product
        row_mr = resolve_margin_ratio(row_exchange, row_product, default=mr)
        return estimate_margin(
            r["spot_close"], r["strike"], option_type,
            r["option_close"], mult, row_mr, 0.5,
            exchange=row_exchange, product=row_product,
        )

    c = c.copy()
    c["margin"] = c.apply(row_margin, axis=1)
    c = c[c["margin"] > 0].copy()
    if c.empty:
        return None

    gross_premium_cash = c["option_close"] * float(mult)
    net_premium_cash = (gross_premium_cash - roundtrip_fee).clip(lower=0.0)
    c["net_premium_cash"] = net_premium_cash
    c["eff"] = gross_premium_cash / c["margin"]
    c["net_eff"] = net_premium_cash / c["margin"]
    theta_cash = c["theta"].abs() * float(mult) if "theta" in c.columns else pd.Series(0.0, index=c.index)
    if carry_metric == "theta_margin" and "theta" in c.columns:
        c["carry_score"] = theta_cash / c["margin"]
    elif carry_metric == "theta" and "theta" in c.columns:
        c["carry_score"] = theta_cash
    elif carry_metric == "premium":
        c["carry_score"] = net_premium_cash
    elif carry_metric == "net_premium_margin":
        c["carry_score"] = c["net_eff"]
    else:
        c["carry_score"] = c["eff"]
    c["stress_loss"] = c.apply(
        lambda r: calc_s1_stress_loss(
            r, option_type, mult,
            spot_move_pct=stress_spot_move_pct,
            iv_up_points=stress_iv_up_points,
            premium_loss_multiple=stress_premium_loss_multiple,
        ),
        axis=1,
    )
    c["stress_loss"] = c["stress_loss"].replace([np.inf, -np.inf], np.nan)
    c = c[c["stress_loss"].notna() & (c["stress_loss"] > 0)].copy()
    if c.empty:
        return None

    if target_abs_delta is None:
        target_abs_delta = (float(min_abs_delta) + float(max_abs_delta)) / 2.0
    c["abs_delta"] = c["delta"].abs()
    c["delta_dist"] = (c["abs_delta"] - float(target_abs_delta)).abs()
    c["premium_stress"] = c["net_premium_cash"] / c["stress_loss"]
    c["theta_stress"] = theta_cash / c["stress_loss"]
    c["premium_margin"] = c["net_eff"]
    volume_rank = _pct_rank_high(c["volume"]) if "volume" in c.columns else pd.Series(0.0, index=c.index)
    oi_rank = _pct_rank_high(c["open_interest"]) if "open_interest" in c.columns else pd.Series(0.0, index=c.index)
    c["liquidity_score"] = 0.5 * volume_rank + 0.5 * oi_rank
    c = _add_s1_premium_quality_fields(
        c, option_type, mult, roundtrip_fee, theta_cash,
        stress_spot_move_pct=stress_spot_move_pct,
        exchange=exchange, product=product,
    )
    ranking_key = str(ranking_mode or "").lower()
    if ranking_key in {"b4", "b4_role", "b4_dedup", "b4_contract"}:
        c = _apply_s1_b4_contract_ranking(c, b4_params=b4_params)
        if c is None or c.empty:
            return None
        ranked = _stable_rank(
            c,
            [
                "b4_contract_score",
                "premium_to_iv10_loss",
                "premium_to_stress_loss",
                "premium_yield_margin",
                "gamma_rent_penalty",
                "open_interest",
                "volume",
            ],
            [False, False, False, False, True, False, False],
        )
        if return_candidates:
            if ranked is None:
                return ranked
            max_n = int(max_candidates or 0)
            return ranked if max_n <= 0 else ranked.head(max_n)
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    if ranking_key in {"b6", "b6_residual_quality", "b6_contract", "b6_role"}:
        c = _apply_s1_b6_contract_ranking(c, b6_params=b4_params)
        if c is None or c.empty:
            return None
        ranked = _stable_rank(
            c,
            [
                "b6_contract_score",
                "b6_theta_per_vega_score",
                "b6_premium_to_stress_score",
                "b6_premium_to_iv10_score",
                "b6_theta_per_gamma_score",
                "b6_tail_move_coverage_score",
                "open_interest",
                "volume",
            ],
            [False, False, False, False, False, False, False, False],
        )
        if return_candidates:
            if ranked is None:
                return ranked
            max_n = int(max_candidates or 0)
            return ranked if max_n <= 0 else ranked.head(max_n)
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    if "iv_residual" in c.columns and iv_residual_weight > 0:
        iv_res = c["iv_residual"].fillna(0).clip(-1, 1)
        c["quality_score"] = c["carry_score"] * (1 + iv_residual_weight * iv_res)
    else:
        c["quality_score"] = c["carry_score"]
    if ranking_key in {"risk_reward", "stress_reward", "premium_stress"}:
        gamma_abs = c["gamma"].abs().fillna(0) if "gamma" in c.columns else pd.Series(0.0, index=c.index)
        vega_abs = c["vega"].abs().fillna(0) if "vega" in c.columns else pd.Series(0.0, index=c.index)
        gamma_penalty_rank = _pct_rank_high(gamma_abs)
        vega_penalty_rank = _pct_rank_high(vega_abs)
        penalty = (
            1.0
            + float(gamma_penalty or 0.0) * gamma_penalty_rank
            + float(vega_penalty or 0.0) * vega_penalty_rank
        )
        c["quality_score"] = (
            float(premium_stress_weight or 0.0) * _pct_rank_high(c["premium_stress"])
            + float(theta_stress_weight or 0.0) * _pct_rank_high(c["theta_stress"])
            + float(premium_margin_weight or 0.0) * _pct_rank_high(c["premium_margin"])
            + float(liquidity_weight or 0.0) * c["liquidity_score"]
            + float(delta_weight or 0.0) * _pct_rank_low(c["delta_dist"])
        ) / penalty
        if "iv_residual" in c.columns and iv_residual_weight > 0:
            iv_res = c["iv_residual"].fillna(0).clip(-1, 1)
            c["quality_score"] = c["quality_score"] * (1 + iv_residual_weight * iv_res)
        ranked = _stable_rank(
            c,
            ["quality_score", "premium_stress", "theta_stress", "premium_margin", "volume", "open_interest"],
            [False, False, False, False, False, False],
        )
        if return_candidates:
            if ranked is None:
                return ranked
            max_n = int(max_candidates or 0)
            return ranked if max_n <= 0 else ranked.head(max_n)
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    if ranking_key in {"liquidity", "liquidity_oi", "volume_oi"}:
        ranked = _stable_rank(
            c,
            ["liquidity_score", "open_interest", "volume", "delta_dist"],
            [False, False, False, True],
        )
        if return_candidates:
            if ranked is None:
                return ranked
            max_n = int(max_candidates or 0)
            return ranked if max_n <= 0 else ranked.head(max_n)
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    if use_stress_score:
        gamma_abs = c["gamma"].abs().fillna(0) if "gamma" in c.columns else 0.0
        vega_abs = c["vega"].abs().fillna(0) if "vega" in c.columns else 0.0
        penalty = 1.0 + float(gamma_penalty or 0.0) * gamma_abs + float(vega_penalty or 0.0) * vega_abs
        c["quality_score"] = c["quality_score"] / c["stress_loss"] / penalty
        ranked = _stable_rank(
            c,
            ["quality_score", "volume", "open_interest", "delta_dist", "eff"],
            [False, False, False, True, False],
        )
        if return_candidates:
            if ranked is None:
                return ranked
            max_n = int(max_candidates or 0)
            return ranked if max_n <= 0 else ranked.head(max_n)
        return None if ranked is None or ranked.empty else ranked.iloc[0]

    ranked = _stable_rank(
        c,
        ["delta_dist", "volume", "open_interest", "quality_score", "eff"],
        [True, False, False, False, False],
    )
    if return_candidates:
        if ranked is None:
            return ranked
        max_n = int(max_candidates or 0)
        return ranked if max_n <= 0 else ranked.head(max_n)
    return None if ranked is None or ranked.empty else ranked.iloc[0]


__all__ = [
    "calc_s1_stress_loss",
    "s1_forward_vega_quality_filter",
    "select_s1_sell",
]
