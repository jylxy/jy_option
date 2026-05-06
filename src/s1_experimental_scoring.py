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
