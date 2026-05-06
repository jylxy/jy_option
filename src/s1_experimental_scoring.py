"""Experimental S1 scoring helpers.

The P3B/A0 mainline does not use these scores by default. They stay available
for historical B6 reruns and future controlled experiments.
"""

from __future__ import annotations

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

