"""Experimental S1 scoring helpers.

The P3B/A0 mainline does not use these scores by default. They stay available
for historical B6 reruns and future controlled experiments.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Mapping

import numpy as np
import pandas as pd


def s1_b6_enabled(config: Mapping[str, object]) -> bool:
    mode = str(config.get('s1_ranking_mode', '') or '').lower()
    return (
        mode in {'b6', 'b6_residual_quality', 'b6_contract', 'b6_role'}
        or bool(config.get('s1_b6_contract_rank_enabled', False))
        or bool(config.get('s1_b6_side_tilt_enabled', False))
        or bool(config.get('s1_b6_product_tilt_enabled', False))
    )


def apply_s1_b6_candidate_ranking(
    candidates: pd.DataFrame,
    *,
    config: Mapping[str, object],
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return candidates
    c = candidates.copy()
    if bool(config.get('s1_b6_hard_filter_enabled', False)):
        min_net = float(config.get('s1_b6_min_net_premium_cash', 0.0) or 0.0)
        max_friction = config.get('s1_b6_max_friction_ratio', 0.20)
        if min_net > 0 and 'net_premium_cash' in c.columns:
            net = pd.to_numeric(c['net_premium_cash'], errors='coerce')
            c = c[net >= min_net].copy()
        if max_friction is not None and 'friction_ratio' in c.columns:
            try:
                max_friction = float(max_friction)
            except (TypeError, ValueError):
                max_friction = np.nan
            if np.isfinite(max_friction):
                friction = pd.to_numeric(c['friction_ratio'], errors='coerce')
                c = c[friction.isna() | (friction <= max_friction)].copy()
        if c.empty:
            return c

    def col(name):
        return c[name] if name in c.columns else pd.Series(np.nan, index=c.index, dtype=float)

    c['b6_premium_to_stress_score'] = 100.0 * rank_high(col('premium_to_stress_loss'))
    c['b6_premium_to_iv10_score'] = 100.0 * rank_high(col('premium_to_iv10_loss'))
    c['b6_theta_per_vega_score'] = 100.0 * rank_high(
        c['b5_theta_per_vega'] if 'b5_theta_per_vega' in c.columns else col('theta_vega_efficiency')
    )
    c['b6_theta_per_gamma_score'] = 100.0 * rank_high(col('b5_theta_per_gamma'))
    c['b6_tail_move_coverage_score'] = 100.0 * rank_high(
        col('b5_premium_to_tail_move_loss')
    )
    c['b6_vomma_score'] = 100.0 * rank_low(col('b3_vomma_loss_ratio'))
    c['b6_premium_yield_margin_score'] = 100.0 * rank_high(col('premium_yield_margin'))
    weights = {
        'b6_premium_to_stress_score': float(config.get('s1_b6_weight_premium_to_stress', 0.24) or 0.0),
        'b6_premium_to_iv10_score': float(config.get('s1_b6_weight_premium_to_iv10', 0.22) or 0.0),
        'b6_theta_per_vega_score': float(config.get('s1_b6_weight_theta_per_vega', 0.22) or 0.0),
        'b6_theta_per_gamma_score': float(config.get('s1_b6_weight_theta_per_gamma', 0.12) or 0.0),
        'b6_tail_move_coverage_score': float(config.get('s1_b6_weight_tail_move_coverage', 0.10) or 0.0),
        'b6_vomma_score': float(config.get('s1_b6_weight_vomma', 0.06) or 0.0),
        'b6_premium_yield_margin_score': float(
            config.get('s1_b6_weight_premium_yield_margin', 0.04) or 0.0
        ),
    }
    weight_sum = sum(max(0.0, v) for v in weights.values())
    missing = float(config.get('s1_b6_missing_factor_score', 50.0) or 50.0)
    if weight_sum <= 0:
        c['b6_contract_score'] = pd.to_numeric(
            c.get('quality_score', pd.Series(missing, index=c.index)),
            errors='coerce',
        ).fillna(missing)
    else:
        score = pd.Series(0.0, index=c.index, dtype=float)
        for column, weight in weights.items():
            weight = max(0.0, float(weight or 0.0))
            if weight <= 0:
                continue
            score += weight * pd.to_numeric(c[column], errors='coerce').fillna(missing)
        c['b6_contract_score'] = (score / weight_sum).clip(0.0, 100.0)
    c['quality_score'] = c['b6_contract_score']
    sort_cols = [
        col for col in (
            'b6_contract_score',
            'b6_theta_per_vega_score',
            'b6_premium_to_stress_score',
            'b6_premium_to_iv10_score',
            'b6_theta_per_gamma_score',
            'b6_tail_move_coverage_score',
            'open_interest',
            'volume',
            'option_code',
        )
        if col in c.columns
    ]
    ascending = [False] * len(sort_cols)
    if sort_cols and sort_cols[-1] == 'option_code':
        ascending[-1] = True
    return c.sort_values(sort_cols, ascending=ascending, kind='mergesort') if sort_cols else c


def contract_iv_vov_from_history(contract_iv_history, option_code, lookback=20) -> float:
    hist = contract_iv_history.get(str(option_code)) if contract_iv_history is not None else None
    if not hist:
        return np.nan
    ivs = pd.to_numeric(pd.Series(hist.get('ivs', [])), errors='coerce').replace(
        [np.inf, -np.inf],
        np.nan,
    ).dropna()
    if len(ivs) < 3:
        return np.nan
    diffs = ivs.diff().dropna().tail(max(2, int(lookback or 20)))
    if len(diffs) < 2:
        return np.nan
    return float(diffs.std(ddof=0))


def term_structure_features(prod_df: pd.DataFrame, expiry) -> dict:
    if prod_df is None or prod_df.empty:
        return {}
    required = {'expiry_date', 'dte', 'moneyness', 'implied_vol'}
    if not required.issubset(set(prod_df.columns)):
        return {}
    atm = prod_df.copy()
    atm['implied_vol'] = pd.to_numeric(atm['implied_vol'], errors='coerce')
    atm['moneyness'] = pd.to_numeric(atm['moneyness'], errors='coerce')
    atm['dte'] = pd.to_numeric(atm['dte'], errors='coerce')
    atm = atm[
        atm['expiry_date'].notna()
        & atm['implied_vol'].gt(0)
        & atm['dte'].gt(0)
        & atm['moneyness'].between(0.95, 1.05)
    ]
    if atm.empty:
        return {}
    curve = (
        atm.groupby('expiry_date', as_index=False)
        .agg(atm_iv=('implied_vol', 'mean'), dte=('dte', 'median'))
        .sort_values(['dte', 'expiry_date'], kind='mergesort')
        .reset_index(drop=True)
    )
    if curve.empty:
        return {}
    exp_str = str(expiry)
    matches = curve.index[curve['expiry_date'].astype(str) == exp_str].tolist()
    if matches:
        idx = int(matches[0])
    else:
        exp_rows = prod_df[prod_df['expiry_date'].astype(str) == exp_str]
        exp_dte = pd.to_numeric(exp_rows.get('dte', pd.Series(dtype=float)), errors='coerce')
        if exp_dte.notna().sum() == 0:
            return {}
        idx = int((curve['dte'] - float(exp_dte.median())).abs().idxmin())
    cur = curve.iloc[idx]
    near = curve.iloc[idx - 1] if idx > 0 else None
    far = curve.iloc[idx + 1] if idx + 1 < len(curve) else None
    cur_iv = float(cur['atm_iv']) if pd.notna(cur['atm_iv']) else np.nan
    near_iv = float(near['atm_iv']) if near is not None and pd.notna(near['atm_iv']) else np.nan
    far_iv = float(far['atm_iv']) if far is not None and pd.notna(far['atm_iv']) else np.nan
    term_pressure = 0.0
    if np.isfinite(near_iv) and np.isfinite(cur_iv):
        term_pressure += max(cur_iv - near_iv, 0.0)
    if np.isfinite(far_iv) and np.isfinite(cur_iv):
        term_pressure += max(far_iv - cur_iv, 0.0)
    return {
        'b3_near_atm_iv': near_iv,
        'b3_next_atm_iv': cur_iv,
        'b3_far_atm_iv': far_iv,
        'b3_term_structure_pressure': term_pressure if np.isfinite(term_pressure) else np.nan,
    }


def add_b3_candidate_fields(
    candidates: pd.DataFrame,
    product,
    option_type,
    *,
    config: Mapping[str, object],
    current_iv_state: Mapping[str, object],
    contract_iv_vov: Callable[[object, int], float],
    term_features: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return candidates
    c = candidates.copy()
    term_features = term_features or {}

    entry_iv_trend = pd.to_numeric(
        c.get('entry_iv_trend', pd.Series(np.nan, index=c.index)),
        errors='coerce',
    )
    if entry_iv_trend.notna().sum() == 0:
        iv_state = current_iv_state.get(product, {}) if current_iv_state is not None else {}
        entry_iv_trend = pd.Series(iv_state.get('iv_trend', np.nan), index=c.index, dtype=float)

    change_1d = pd.to_numeric(
        c.get('contract_iv_change_1d', pd.Series(np.nan, index=c.index)),
        errors='coerce',
    )
    change_5d = pd.to_numeric(
        c.get('contract_iv_change_5d', pd.Series(np.nan, index=c.index)),
        errors='coerce',
    )
    term_pressure = float(term_features.get('b3_term_structure_pressure', np.nan))
    term_pressure_series = pd.Series(term_pressure, index=c.index, dtype=float)
    c['b3_forward_variance_pressure'] = (
        entry_iv_trend.clip(lower=0.0).fillna(0.0)
        + change_5d.clip(lower=0.0).fillna(change_1d.clip(lower=0.0))
        + term_pressure_series.clip(lower=0.0).fillna(0.0)
    ).replace([np.inf, -np.inf], np.nan)

    short_lookback = int(config.get('s1_b3_vov_lookback_short', 5) or 5)
    long_lookback = int(config.get('s1_b3_vov_lookback_long', 20) or 20)
    codes = c['option_code'] if 'option_code' in c.columns else pd.Series('', index=c.index)
    vov_short = pd.Series(
        [contract_iv_vov(code, short_lookback) for code in codes],
        index=c.index,
        dtype=float,
    )
    vov_long = pd.Series(
        [contract_iv_vov(code, long_lookback) for code in codes],
        index=c.index,
        dtype=float,
    )
    fallback_vov = change_1d.abs().fillna(0.0) + 0.5 * change_5d.abs().fillna(0.0)
    c['b3_vol_of_vol_proxy'] = vov_short.fillna(fallback_vov).replace([np.inf, -np.inf], np.nan)
    c['b3_vov_trend'] = (vov_short / vov_long.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)

    iv5 = pd.to_numeric(c.get('premium_to_iv5_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
    iv10 = pd.to_numeric(c.get('premium_to_iv10_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
    stress = pd.to_numeric(c.get('premium_to_stress_loss', pd.Series(np.nan, index=c.index)), errors='coerce')
    c['b3_iv_shock_coverage'] = (0.5 * iv5 + 0.5 * iv10).replace([np.inf, -np.inf], np.nan)
    c['b3_joint_stress_coverage'] = stress.replace([np.inf, -np.inf], np.nan)

    iv5_loss = pd.to_numeric(c.get('iv_shock_loss_5_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
    iv10_loss = pd.to_numeric(c.get('iv_shock_loss_10_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
    net_premium = pd.to_numeric(c.get('net_premium_cash', pd.Series(np.nan, index=c.index)), errors='coerce')
    vomma_cash = (iv10_loss - 2.0 * iv5_loss).clip(lower=0.0)
    c['b3_vomma_cash'] = vomma_cash.replace([np.inf, -np.inf], np.nan)
    c['b3_vomma_loss_ratio'] = (vomma_cash / net_premium.replace(0.0, np.nan)).replace(
        [np.inf, -np.inf],
        np.nan,
    )

    skew_change = pd.to_numeric(
        c.get('contract_skew_change_for_vega', pd.Series(np.nan, index=c.index)),
        errors='coerce',
    )
    skew_fallback = change_5d - entry_iv_trend
    c['contract_skew_change_for_vega'] = skew_change.fillna(skew_fallback)
    c['b3_skew_steepening'] = c['contract_skew_change_for_vega'].clip(lower=0.0).replace(
        [np.inf, -np.inf],
        np.nan,
    )

    for key, value in term_features.items():
        c[key] = value
    return c


def score_from_weight_map(
    frame: pd.DataFrame,
    weight_map: Mapping[str, object],
    *,
    config: Mapping[str, object],
) -> pd.Series:
    weight_sum = sum(max(0.0, float(v or 0.0)) for v in weight_map.values())
    missing = float(config.get('s1_b6_missing_factor_score', 50.0) or 50.0)
    if weight_sum <= 0:
        return pd.Series(missing, index=frame.index, dtype=float)
    score = pd.Series(0.0, index=frame.index, dtype=float)
    for column, weight in weight_map.items():
        weight = max(0.0, float(weight or 0.0))
        if weight <= 0:
            continue
        values = pd.to_numeric(
            frame[column] if column in frame.columns else pd.Series(np.nan, index=frame.index),
            errors='coerce',
        ).fillna(missing)
        score += weight * values
    return (score / weight_sum).clip(0.0, 100.0)


def b3_product_side_budget_overlay(
    side_df: pd.DataFrame,
    b2_product_budget_map,
    total_budget_pct,
    date_str,
    nav,
    *,
    config: Mapping[str, object],
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
):
    if side_df is None or side_df.empty:
        return None
    weights = {
        'b2_side_score': float(config.get('s1_b3_weight_b2', 0.60) or 0.0),
        'b3_forward_variance_score': float(config.get('s1_b3_weight_forward_variance', 0.0) or 0.0),
        'b3_vol_of_vol_score': float(config.get('s1_b3_weight_vol_of_vol', 0.0) or 0.0),
        'b3_iv_shock_score': float(config.get('s1_b3_weight_iv_shock', 0.0) or 0.0),
        'b3_joint_stress_score': float(config.get('s1_b3_weight_joint_stress', 0.0) or 0.0),
        'b3_vomma_score': float(config.get('s1_b3_weight_vomma', 0.0) or 0.0),
        'b3_skew_stability_score': float(config.get('s1_b3_weight_skew_stability', 0.0) or 0.0),
    }
    weight_sum = sum(max(0.0, v) for v in weights.values())
    if weight_sum <= 0:
        return None

    b3 = side_df.copy()

    def col(name):
        return b3[name] if name in b3.columns else pd.Series(np.nan, index=b3.index, dtype=float)

    b3['b3_forward_variance_score'] = 100.0 * rank_low(col('b3_forward_variance_pressure'))
    b3['b3_vol_of_vol_score'] = 100.0 * rank_low(col('b3_vol_of_vol_proxy'))
    b3['b3_iv_shock_score'] = 100.0 * rank_high(col('b3_iv_shock_coverage'))
    b3['b3_joint_stress_score'] = 100.0 * rank_high(col('b3_joint_stress_coverage'))
    b3['b3_vomma_score'] = 100.0 * rank_low(col('b3_vomma_loss_ratio'))
    b3['b3_skew_stability_score'] = 100.0 * rank_low(col('b3_skew_steepening'))
    b3['b3_clean_vega_score'] = score_from_weight_map(b3, weights, config=config)

    floor_weight = max(0.0, float(config.get('s1_b3_floor_weight', 0.50) or 0.0))
    power = max(0.01, float(config.get('s1_b3_power', 1.50) or 1.50))
    clip_low = float(config.get('s1_b3_score_clip_low', 5.0) or 5.0)
    clip_high = float(config.get('s1_b3_score_clip_high', 95.0) or 95.0)
    if clip_high < clip_low:
        clip_low, clip_high = clip_high, clip_low
    tilt_strength = float(np.clip(float(config.get('s1_b3_tilt_strength', 0.0) or 0.0), 0.0, 1.0))

    b3['b3_clipped_score'] = b3['b3_clean_vega_score'].clip(clip_low, clip_high)
    b3['b3_raw_weight'] = floor_weight + (b3['b3_clipped_score'].clip(lower=0.0) / 100.0) ** power
    weight_total = float(b3['b3_raw_weight'].sum())
    if weight_total <= 0:
        b3['b3_raw_weight'] = 1.0
        weight_total = float(len(b3))

    base_side_budget = []
    for row in b3.itertuples(index=False):
        product_budget = float(b2_product_budget_map.get(row.product, 0.0) or 0.0)
        base_side_budget.append(product_budget / 2.0)
    b3['b3_side_equal_budget_pct'] = base_side_budget
    base_total_budget = float(pd.to_numeric(b3['b3_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
    if base_total_budget <= 0:
        base_total_budget = float(total_budget_pct or 0.0)
    b3['b3_side_quality_budget_pct'] = base_total_budget * b3['b3_raw_weight'] / weight_total
    b3['b3_side_final_budget_pct'] = (
        (1.0 - tilt_strength) * b3['b3_side_equal_budget_pct']
        + tilt_strength * b3['b3_side_quality_budget_pct']
    )

    product_budget_map = b3.groupby('product', sort=False)['b3_side_final_budget_pct'].sum().to_dict()
    side_meta_map = defaultdict(dict)
    for row in b3.to_dict('records'):
        product = row['product']
        ot = row['option_type']
        product_budget = float(product_budget_map.get(product, 0.0) or 0.0)
        final_side_budget = float(row.get('b3_side_final_budget_pct', 0.0) or 0.0)
        side_budget_mult = final_side_budget / (product_budget / 2.0) if product_budget > 0 else np.nan
        meta = dict(row)
        meta.update({
            'b3_product_side_score': row.get('b3_clean_vega_score', np.nan),
            'b3_side_budget_mult': side_budget_mult,
            'b3_clean_vega_tilt_strength': tilt_strength,
        })
        side_meta_map[product][ot] = meta

    diagnostics = []
    if config.get('s1_b3_product_side_budget_diagnostics_enabled', True):
        for row in b3.to_dict('records'):
            product = row['product']
            ot = row['option_type']
            meta = side_meta_map.get(product, {}).get(ot, {})
            diagnostics.append({
                'date': date_str,
                'scope': 's1_b3_product_side_budget',
                'name': f"{product}_{ot}",
                'product': product,
                'option_type': ot,
                'nav': nav,
                'product_budget_pct': product_budget_map.get(product, np.nan),
                'b3_clean_vega_score': meta.get('b3_clean_vega_score', np.nan),
                'b3_forward_variance_score': meta.get('b3_forward_variance_score', np.nan),
                'b3_vol_of_vol_score': meta.get('b3_vol_of_vol_score', np.nan),
                'b3_iv_shock_score': meta.get('b3_iv_shock_score', np.nan),
                'b3_joint_stress_score': meta.get('b3_joint_stress_score', np.nan),
                'b3_vomma_score': meta.get('b3_vomma_score', np.nan),
                'b3_skew_stability_score': meta.get('b3_skew_stability_score', np.nan),
                'b3_side_equal_budget_pct': meta.get('b3_side_equal_budget_pct', np.nan),
                'b3_side_quality_budget_pct': meta.get('b3_side_quality_budget_pct', np.nan),
                'b3_side_final_budget_pct': meta.get('b3_side_final_budget_pct', np.nan),
                'b3_side_budget_mult': meta.get('b3_side_budget_mult', np.nan),
                'tilt_strength': tilt_strength,
            })

    return {
        'product_budget_map': product_budget_map,
        'side_meta_map': side_meta_map,
        'diagnostics': diagnostics,
    }


def b4_product_side_budget_overlay(
    side_df: pd.DataFrame,
    base_product_budget_map,
    total_budget_pct,
    date_str,
    nav,
    *,
    config: Mapping[str, object],
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
):
    if side_df is None or side_df.empty:
        return None
    weights = {
        'b4_premium_to_stress_score': float(
            config.get('s1_b4_product_weight_premium_to_stress', 0.35) or 0.0
        ),
        'b4_premium_to_iv10_score': float(
            config.get('s1_b4_product_weight_premium_to_iv10', 0.30) or 0.0
        ),
        'b4_premium_yield_margin_score': float(
            config.get('s1_b4_product_weight_premium_yield_margin', 0.20) or 0.0
        ),
        'b4_gamma_rent_score': float(
            config.get('s1_b4_product_weight_gamma_rent', 0.15) or 0.0
        ),
    }
    weight_sum = sum(max(0.0, v) for v in weights.values())
    if weight_sum <= 0:
        return None

    b4 = side_df.copy()

    def col(name):
        return b4[name] if name in b4.columns else pd.Series(np.nan, index=b4.index, dtype=float)

    b4['b4_premium_to_iv10_score'] = 100.0 * rank_high(col('premium_to_iv10_loss'))
    b4['b4_premium_to_stress_score'] = 100.0 * rank_high(col('premium_to_stress_loss'))
    b4['b4_premium_yield_margin_score'] = 100.0 * rank_high(col('premium_yield_margin'))
    b4['b4_gamma_rent_score'] = 100.0 * rank_low(col('gamma_rent_penalty'))
    b4['b4_vomma_score'] = 100.0 * rank_low(col('b3_vomma_loss_ratio'))
    b4['b4_breakeven_cushion_score'] = 100.0 * (
        0.5 * rank_high(col('breakeven_cushion_iv'))
        + 0.5 * rank_high(col('breakeven_cushion_rv'))
    )
    b4['b4_vol_of_vol_score'] = 100.0 * rank_low(col('b3_vol_of_vol_proxy'))
    b4['b4_product_side_score'] = score_from_weight_map(b4, weights, config=config)

    floor_weight = max(0.0, float(config.get('s1_b4_floor_weight', 0.50) or 0.0))
    power = max(0.01, float(config.get('s1_b4_power', 1.25) or 1.25))
    clip_low = float(config.get('s1_b4_score_clip_low', 5.0) or 5.0)
    clip_high = float(config.get('s1_b4_score_clip_high', 95.0) or 95.0)
    if clip_high < clip_low:
        clip_low, clip_high = clip_high, clip_low
    tilt_strength = float(np.clip(
        float(config.get('s1_b4_product_tilt_strength', 0.35) or 0.0),
        0.0,
        1.0,
    ))

    b4['b4_clipped_score'] = b4['b4_product_side_score'].clip(clip_low, clip_high)
    b4['b4_raw_weight'] = floor_weight + (b4['b4_clipped_score'].clip(lower=0.0) / 100.0) ** power
    weight_total = float(b4['b4_raw_weight'].sum())
    if weight_total <= 0:
        b4['b4_raw_weight'] = 1.0
        weight_total = float(len(b4))

    base_side_budget = []
    for row in b4.itertuples(index=False):
        product_budget = float(base_product_budget_map.get(row.product, 0.0) or 0.0)
        base_side_budget.append(product_budget / 2.0)
    b4['b4_side_equal_budget_pct'] = base_side_budget
    base_total_budget = float(pd.to_numeric(b4['b4_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
    if base_total_budget <= 0:
        base_total_budget = float(total_budget_pct or 0.0)
    b4['b4_side_quality_budget_pct'] = base_total_budget * b4['b4_raw_weight'] / weight_total
    b4['b4_side_final_budget_pct'] = (
        (1.0 - tilt_strength) * b4['b4_side_equal_budget_pct']
        + tilt_strength * b4['b4_side_quality_budget_pct']
    )
    if config.get('s1_b4_vov_penalty_enabled', False):
        very_low = float(config.get('s1_b4_side_vov_penalty_rank_very_low', 15.0) or 15.0)
        low = float(config.get('s1_b4_side_vov_penalty_rank_low', 30.0) or 30.0)
        very_low_mult = float(config.get('s1_b4_side_vov_penalty_mult_very_low', 0.70) or 0.70)
        low_mult = float(config.get('s1_b4_side_vov_penalty_mult_low', 0.85) or 0.85)
        vov_rank = pd.to_numeric(b4['b4_vol_of_vol_score'], errors='coerce')
        b4['b4_side_vov_penalty_mult'] = np.where(
            vov_rank < very_low,
            very_low_mult,
            np.where(vov_rank < low, low_mult, 1.0),
        )
        b4['b4_side_final_budget_pct'] = (
            b4['b4_side_final_budget_pct']
            * pd.to_numeric(b4['b4_side_vov_penalty_mult'], errors='coerce').fillna(1.0)
        )
    else:
        b4['b4_side_vov_penalty_mult'] = 1.0

    product_budget_map = b4.groupby('product', sort=False)['b4_side_final_budget_pct'].sum().to_dict()
    side_meta_map = defaultdict(dict)
    for row in b4.to_dict('records'):
        product = row['product']
        ot = row['option_type']
        product_budget = float(product_budget_map.get(product, 0.0) or 0.0)
        final_side_budget = float(row.get('b4_side_final_budget_pct', 0.0) or 0.0)
        side_budget_mult = final_side_budget / (product_budget / 2.0) if product_budget > 0 else np.nan
        meta = dict(row)
        meta.update({
            'b4_product_side_score': row.get('b4_product_side_score', np.nan),
            'b4_side_budget_mult': side_budget_mult,
            'b4_product_tilt_strength': tilt_strength,
            'b4_side_vov_penalty_mult': row.get('b4_side_vov_penalty_mult', np.nan),
        })
        side_meta_map[product][ot] = meta

    diagnostics = []
    if config.get('s1_b4_product_side_budget_diagnostics_enabled', True):
        for row in b4.to_dict('records'):
            product = row['product']
            ot = row['option_type']
            meta = side_meta_map.get(product, {}).get(ot, {})
            diagnostics.append({
                'date': date_str,
                'scope': 's1_b4_product_side_budget',
                'name': f"{product}_{ot}",
                'product': product,
                'option_type': ot,
                'nav': nav,
                'product_budget_pct': product_budget_map.get(product, np.nan),
                'b4_product_side_score': meta.get('b4_product_side_score', np.nan),
                'b4_premium_to_iv10_score': meta.get('b4_premium_to_iv10_score', np.nan),
                'b4_premium_to_stress_score': meta.get('b4_premium_to_stress_score', np.nan),
                'b4_premium_yield_margin_score': meta.get('b4_premium_yield_margin_score', np.nan),
                'b4_gamma_rent_score': meta.get('b4_gamma_rent_score', np.nan),
                'b4_vomma_score': meta.get('b4_vomma_score', np.nan),
                'b4_breakeven_cushion_score': meta.get('b4_breakeven_cushion_score', np.nan),
                'b4_vol_of_vol_score': meta.get('b4_vol_of_vol_score', np.nan),
                'b4_side_equal_budget_pct': meta.get('b4_side_equal_budget_pct', np.nan),
                'b4_side_quality_budget_pct': meta.get('b4_side_quality_budget_pct', np.nan),
                'b4_side_final_budget_pct': meta.get('b4_side_final_budget_pct', np.nan),
                'b4_side_budget_mult': meta.get('b4_side_budget_mult', np.nan),
                'b4_side_vov_penalty_mult': meta.get('b4_side_vov_penalty_mult', np.nan),
                'tilt_strength': tilt_strength,
            })

    return {
        'product_budget_map': product_budget_map,
        'side_meta_map': side_meta_map,
        'diagnostics': diagnostics,
    }


def b6_product_budget_overlay(
    side_df: pd.DataFrame,
    candidate_products,
    total_budget_pct,
    date_str,
    nav,
    *,
    config: Mapping[str, object],
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
    weighted_average: Callable[[pd.DataFrame, str], float],
):
    if side_df is None or side_df.empty or not candidate_products:
        return None
    b6 = side_df.copy()

    def col(name):
        return b6[name] if name in b6.columns else pd.Series(np.nan, index=b6.index, dtype=float)

    b6['b6_product_theta_per_vega_score'] = 100.0 * rank_high(col('b5_theta_per_vega'))
    b6['b6_product_premium_to_stress_score'] = 100.0 * rank_high(col('premium_to_stress_loss'))
    b6['b6_product_theta_per_gamma_score'] = 100.0 * rank_high(col('b5_theta_per_gamma'))
    b6['b6_product_tail_beta_score'] = 100.0 * rank_low(col('b5_range_expansion_proxy_20d'))
    b6['b6_product_gamma_per_premium_score'] = 100.0 * rank_low(col('gamma_rent_penalty'))
    b6['b6_side_for_product_score'] = score_from_weight_map(
        b6,
        {
            'b6_product_theta_per_vega_score': config.get('s1_b6_product_weight_theta_per_vega', 0.45),
            'b6_product_premium_to_stress_score': config.get('s1_b6_product_weight_premium_to_stress', 0.20),
            'b6_product_theta_per_gamma_score': config.get('s1_b6_product_weight_theta_per_gamma', 0.15),
            'b6_product_tail_beta_score': config.get('s1_b6_product_weight_tail_beta', 0.10),
            'b6_product_gamma_per_premium_score': config.get('s1_b6_product_weight_gamma_per_premium', 0.10),
        },
        config=config,
    )

    product_rows = []
    for product in candidate_products:
        group = b6[b6['product'] == product]
        if group.empty:
            product_rows.append({'product': product, 'b6_product_score': np.nan})
            continue
        count_series = (
            group['candidate_count']
            if 'candidate_count' in group.columns
            else pd.Series(1.0, index=group.index, dtype=float)
        )
        weights = pd.to_numeric(count_series, errors='coerce').fillna(1.0).clip(lower=1.0)
        score = float((group['b6_side_for_product_score'] * weights).sum() / weights.sum())
        product_candidate_count = (
            pd.to_numeric(group['candidate_count'], errors='coerce').fillna(0.0).sum()
            if 'candidate_count' in group.columns
            else float(len(group))
        )

        def avg(column):
            if column not in group.columns:
                return np.nan
            return weighted_average(group, column)

        product_rows.append({
            'product': product,
            'b6_product_score': score,
            'b6_product_candidate_count': int(product_candidate_count),
            'b6_product_theta_per_vega': avg('b5_theta_per_vega'),
            'b6_product_premium_to_stress': avg('premium_to_stress_loss'),
            'b6_product_theta_per_gamma': avg('b5_theta_per_gamma'),
            'b6_product_range_expansion': avg('b5_range_expansion_proxy_20d'),
            'b6_product_cooldown_penalty': avg('b5_cooldown_penalty_score'),
        })
    prod = pd.DataFrame(product_rows)
    missing = float(config.get('s1_b6_missing_factor_score', 50.0) or 50.0)
    prod['b6_product_score'] = pd.to_numeric(prod['b6_product_score'], errors='coerce').fillna(missing)

    n_products = len(candidate_products)
    total_budget_pct = float(total_budget_pct or 0.0)
    equal_budget_pct = total_budget_pct / max(n_products, 1)
    floor_weight = max(0.0, float(config.get('s1_b6_product_floor_weight', 0.80) or 0.80))
    power = max(0.01, float(config.get('s1_b6_product_power', 1.25) or 1.25))
    clip_low = float(config.get('s1_b6_score_clip_low', 5.0) or 5.0)
    clip_high = float(config.get('s1_b6_score_clip_high', 95.0) or 95.0)
    if clip_high < clip_low:
        clip_low, clip_high = clip_high, clip_low
    tilt_strength = float(np.clip(
        float(config.get('s1_b6_product_tilt_strength', 0.15) or 0.0),
        0.0,
        1.0,
    ))
    raw = floor_weight + (prod['b6_product_score'].clip(clip_low, clip_high) / 100.0) ** power
    quality = total_budget_pct * raw / float(raw.sum() if raw.sum() > 0 else len(raw))
    final = (1.0 - tilt_strength) * equal_budget_pct + tilt_strength * quality
    mult = final / equal_budget_pct if equal_budget_pct > 0 else np.nan
    min_mult = float(config.get('s1_b6_product_multiplier_min', 0.80) or 0.80)
    max_mult = float(config.get('s1_b6_product_multiplier_max', 1.20) or 1.20)
    final = np.clip(mult, min_mult, max_mult) * equal_budget_pct
    prod['b6_product_equal_budget_pct'] = equal_budget_pct
    prod['b6_product_quality_budget_pct'] = quality
    prod['b6_product_final_budget_pct'] = final
    prod['b6_product_budget_mult'] = final / equal_budget_pct if equal_budget_pct > 0 else np.nan
    prod['b6_product_tilt_strength'] = tilt_strength

    budget_map = dict(zip(prod['product'], prod['b6_product_final_budget_pct']))
    meta_map = {row['product']: dict(row) for row in prod.to_dict('records')}
    diagnostics = []
    if config.get('s1_b6_product_budget_diagnostics_enabled', True):
        for row in prod.to_dict('records'):
            diagnostics.append({
                'date': date_str,
                'scope': 's1_b6_product_budget',
                'name': row.get('product'),
                'product': row.get('product'),
                'nav': nav,
                **row,
            })
    return {
        'product_budget_map': budget_map,
        'product_meta_map': meta_map,
        'diagnostics': diagnostics,
    }


def b6_product_side_budget_overlay(
    side_df: pd.DataFrame,
    base_product_budget_map,
    total_budget_pct,
    date_str,
    nav,
    *,
    config: Mapping[str, object],
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
):
    if side_df is None or side_df.empty:
        return None
    b6 = side_df.copy()

    def col(name):
        return b6[name] if name in b6.columns else pd.Series(np.nan, index=b6.index, dtype=float)

    b6['b6_side_theta_per_vega_score'] = 100.0 * rank_high(col('b5_theta_per_vega'))
    b6['b6_side_premium_to_stress_score'] = 100.0 * rank_high(col('premium_to_stress_loss'))
    b6['b6_side_theta_per_gamma_score'] = 100.0 * rank_high(col('b5_theta_per_gamma'))
    b6['b6_side_premium_to_margin_score'] = 100.0 * rank_high(col('premium_yield_margin'))
    b6['b6_side_vega_per_premium_score'] = 100.0 * rank_high(col('b5_premium_per_vega'))
    b6['b6_side_gamma_per_premium_score'] = 100.0 * rank_low(col('gamma_rent_penalty'))
    b6['b6_product_side_score'] = score_from_weight_map(
        b6,
        {
            'b6_side_theta_per_vega_score': config.get('s1_b6_side_weight_theta_per_vega', 0.35),
            'b6_side_premium_to_stress_score': config.get('s1_b6_side_weight_premium_to_stress', 0.25),
            'b6_side_theta_per_gamma_score': config.get('s1_b6_side_weight_theta_per_gamma', 0.15),
            'b6_side_premium_to_margin_score': config.get('s1_b6_side_weight_premium_to_margin', 0.10),
            'b6_side_vega_per_premium_score': config.get('s1_b6_side_weight_vega_per_premium', 0.10),
            'b6_side_gamma_per_premium_score': config.get('s1_b6_side_weight_gamma_per_premium', 0.05),
        },
        config=config,
    )

    floor_weight = max(0.0, float(config.get('s1_b6_side_floor_weight', 0.70) or 0.70))
    power = max(0.01, float(config.get('s1_b6_side_power', 1.25) or 1.25))
    clip_low = float(config.get('s1_b6_score_clip_low', 5.0) or 5.0)
    clip_high = float(config.get('s1_b6_score_clip_high', 95.0) or 95.0)
    if clip_high < clip_low:
        clip_low, clip_high = clip_high, clip_low
    tilt_strength = float(np.clip(
        float(config.get('s1_b6_side_tilt_strength', 0.25) or 0.0),
        0.0,
        1.0,
    ))
    b6['b6_raw_weight'] = floor_weight + (b6['b6_product_side_score'].clip(clip_low, clip_high) / 100.0) ** power
    weight_total = float(b6['b6_raw_weight'].sum())
    if weight_total <= 0:
        b6['b6_raw_weight'] = 1.0
        weight_total = float(len(b6))

    base_side_budget = []
    for row in b6.itertuples(index=False):
        product_budget = float(base_product_budget_map.get(row.product, 0.0) or 0.0)
        base_side_budget.append(product_budget / 2.0)
    b6['b6_side_equal_budget_pct'] = base_side_budget
    base_total_budget = float(pd.to_numeric(b6['b6_side_equal_budget_pct'], errors='coerce').clip(lower=0).sum())
    if base_total_budget <= 0:
        base_total_budget = float(total_budget_pct or 0.0)
    b6['b6_side_quality_budget_pct'] = base_total_budget * b6['b6_raw_weight'] / weight_total
    b6['b6_side_final_budget_pct'] = (
        (1.0 - tilt_strength) * b6['b6_side_equal_budget_pct']
        + tilt_strength * b6['b6_side_quality_budget_pct']
    )

    b6['b6_side_direction_penalty_mult'] = 1.0
    if config.get('s1_b6_side_direction_penalty_enabled', True):
        trend_z = pd.to_numeric(col('b5_trend_z_20d'), errors='coerce').fillna(0.0)
        threshold = float(config.get('s1_b6_side_adverse_trend_z', 0.80) or 0.80)
        trend_mult = float(config.get('s1_b6_side_adverse_trend_mult', 0.85) or 0.85)
        adverse_trend = (
            ((b6['option_type'].astype(str).str.upper() == 'C') & (trend_z > threshold))
            | ((b6['option_type'].astype(str).str.upper() == 'P') & (trend_z < -threshold))
        )
        b6.loc[adverse_trend, 'b6_side_direction_penalty_mult'] *= trend_mult

        up_score = 100.0 * rank_high(col('b5_breakout_distance_up_60d'))
        down_score = 100.0 * rank_high(col('b5_breakout_distance_down_60d'))
        cushion_score = np.where(
            b6['option_type'].astype(str).str.upper() == 'C',
            up_score,
            down_score,
        )
        low_rank = float(config.get('s1_b6_side_breakout_rank_low', 30.0) or 30.0)
        breakout_mult = float(config.get('s1_b6_side_breakout_mult_low', 0.90) or 0.90)
        b6.loc[pd.Series(cushion_score, index=b6.index) < low_rank, 'b6_side_direction_penalty_mult'] *= breakout_mult

        skew_score = 100.0 * rank_low(col('b3_skew_steepening'))
        skew_rank = float(config.get('s1_b6_side_skew_rank_low', 30.0) or 30.0)
        skew_mult = float(config.get('s1_b6_side_skew_mult_low', 0.90) or 0.90)
        b6.loc[skew_score < skew_rank, 'b6_side_direction_penalty_mult'] *= skew_mult

        cooldown = pd.to_numeric(col('b5_cooldown_penalty_score'), errors='coerce').fillna(0.0).clip(0.0, 1.0)
        floor = float(config.get('s1_b6_side_cooldown_mult_floor', 0.70) or 0.70)
        cooldown_mult = 1.0 - cooldown * (1.0 - floor)
        b6['b6_side_direction_penalty_mult'] *= cooldown_mult

    b6['b6_side_final_budget_pct'] *= pd.to_numeric(
        b6['b6_side_direction_penalty_mult'],
        errors='coerce',
    ).fillna(1.0)
    min_mult = float(config.get('s1_b6_side_multiplier_min', 0.70) or 0.70)
    max_mult = float(config.get('s1_b6_side_multiplier_max', 1.30) or 1.30)

    product_budget_map = {}
    side_meta_map = defaultdict(dict)
    for product, group in b6.groupby('product', sort=False):
        base_product_budget = float(base_product_budget_map.get(product, 0.0) or 0.0)
        if base_product_budget > 0:
            side_equal = base_product_budget / 2.0
            clipped = group.copy()
            mult = pd.to_numeric(clipped['b6_side_final_budget_pct'], errors='coerce') / side_equal
            clipped['b6_side_final_budget_pct'] = mult.clip(min_mult, max_mult) * side_equal
            b6.loc[clipped.index, 'b6_side_final_budget_pct'] = clipped['b6_side_final_budget_pct']
        product_budget_map[product] = float(
            pd.to_numeric(b6.loc[group.index, 'b6_side_final_budget_pct'], errors='coerce').sum()
        )

    for row in b6.to_dict('records'):
        product = row['product']
        ot = row['option_type']
        equal_side = float(base_product_budget_map.get(product, 0.0) or 0.0) / 2.0
        final_side = float(row.get('b6_side_final_budget_pct', 0.0) or 0.0)
        side_budget_mult = final_side / equal_side if equal_side > 0 else np.nan
        meta = dict(row)
        meta.update({
            'b6_product_side_score': row.get('b6_product_side_score', np.nan),
            'b6_side_budget_mult': side_budget_mult,
            'b6_side_tilt_strength': tilt_strength,
        })
        side_meta_map[product][ot] = meta

    diagnostics = []
    if config.get('s1_b6_product_side_budget_diagnostics_enabled', True):
        for row in b6.to_dict('records'):
            meta = side_meta_map.get(row['product'], {}).get(row['option_type'], {})
            diagnostics.append({
                'date': date_str,
                'scope': 's1_b6_product_side_budget',
                'name': f"{row['product']}_{row['option_type']}",
                'product': row['product'],
                'option_type': row['option_type'],
                'nav': nav,
                'product_budget_pct': product_budget_map.get(row['product'], np.nan),
                **meta,
            })

    return {
        'product_budget_map': product_budget_map,
        'side_meta_map': side_meta_map,
        'diagnostics': diagnostics,
    }
