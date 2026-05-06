"""S1 product and side budget tilt helpers."""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd


def compute_b2_product_budget_map(
    *,
    side_df: pd.DataFrame,
    candidate_products: Sequence[str],
    total_budget_pct: float,
    config: Mapping[str, object],
    date_str: str,
    nav: float,
    rank_high: Callable[[pd.Series], pd.Series],
    rank_low: Callable[[pd.Series], pd.Series],
) -> tuple[pd.DataFrame, dict, dict, list[dict]]:
    """Compute B2 product quality scores and budget tilts.

    The function is intentionally pure: it does not know about engine state and
    returns diagnostics rows for the caller to append.
    """
    product_scores = {
        product: {
            "product": product,
            "product_score": np.nan,
            "put_score": np.nan,
            "call_score": np.nan,
            "side_count": 0,
            "candidate_count": 0,
        }
        for product in candidate_products
    }
    missing_score = float(config.get("s1_b2_missing_score", 20.0) or 20.0)
    missing_side_penalty = float(config.get("s1_b2_missing_side_penalty", 0.70) or 0.70)

    side_df = side_df.copy()
    if not side_df.empty:
        breakeven_score = (
            0.5 * rank_high(side_df["breakeven_cushion_iv"])
            + 0.5 * rank_high(side_df["breakeven_cushion_rv"])
        )
        iv_shock_score = (
            0.5 * rank_high(side_df["premium_to_iv5_loss"])
            + 0.5 * rank_high(side_df["premium_to_iv10_loss"])
        )
        side_df["b2_side_score"] = 100.0 * (
            0.20 * rank_high(side_df["variance_carry"])
            + 0.15 * breakeven_score
            + 0.20 * iv_shock_score
            + 0.15 * rank_high(side_df["premium_to_stress_loss"])
            + 0.10 * rank_high(side_df["theta_vega_efficiency"])
            + 0.10 * rank_low(side_df["gamma_rent_penalty"])
            + 0.10 * rank_low(side_df["friction_ratio"])
        ).clip(0.0, 100.0)

        for product, group in side_df.groupby("product", sort=False):
            put = group[group["option_type"] == "P"]
            call = group[group["option_type"] == "C"]
            put_score = float(put["b2_side_score"].mean()) if not put.empty else np.nan
            call_score = float(call["b2_side_score"].mean()) if not call.empty else np.nan
            valid_scores = [s for s in (put_score, call_score) if np.isfinite(s)]
            if len(valid_scores) >= 2:
                product_score = float(np.mean(valid_scores))
            elif len(valid_scores) == 1:
                product_score = float(valid_scores[0] * missing_side_penalty)
            else:
                product_score = missing_score
            product_scores[product] = {
                "product": product,
                "product_score": product_score,
                "put_score": put_score,
                "call_score": call_score,
                "side_count": len(valid_scores),
                "candidate_count": int(group["candidate_count"].sum()),
            }

    n_products = len(candidate_products)
    equal_budget_pct = float(total_budget_pct or 0.0) / max(n_products, 1)
    floor_weight = max(0.0, float(config.get("s1_b2_floor_weight", 0.50) or 0.0))
    power = max(0.01, float(config.get("s1_b2_power", 1.50) or 1.50))
    clip_low = float(config.get("s1_b2_score_clip_low", 5.0) or 5.0)
    clip_high = float(config.get("s1_b2_score_clip_high", 95.0) or 95.0)
    if clip_high < clip_low:
        clip_low, clip_high = clip_high, clip_low
    tilt_strength = float(config.get("s1_b2_tilt_strength", 0.0) or 0.0)
    tilt_strength = float(np.clip(tilt_strength, 0.0, 1.0))

    rows = []
    for product in candidate_products:
        item = product_scores.get(product, {})
        score = float(item.get("product_score", np.nan))
        if not np.isfinite(score):
            score = missing_score
        clipped_score = float(np.clip(score, clip_low, clip_high))
        raw_weight = floor_weight + (max(clipped_score, 0.0) / 100.0) ** power
        rows.append({
            **item,
            "product": product,
            "product_score": score,
            "clipped_score": clipped_score,
            "raw_weight": raw_weight,
        })

    weight_sum = sum(float(r["raw_weight"] or 0.0) for r in rows)
    if weight_sum <= 0:
        weight_sum = float(n_products)
        for row in rows:
            row["raw_weight"] = 1.0

    budget_map = {}
    meta_map = {}
    for row in rows:
        quality_budget_pct = float(total_budget_pct or 0.0) * float(row["raw_weight"]) / weight_sum
        final_budget_pct = (
            (1.0 - tilt_strength) * equal_budget_pct
            + tilt_strength * quality_budget_pct
        )
        budget_mult = final_budget_pct / equal_budget_pct if equal_budget_pct > 0 else np.nan
        product = row["product"]
        budget_map[product] = final_budget_pct
        meta_map[product] = {
            **row,
            "equal_budget_pct": equal_budget_pct,
            "quality_budget_pct": quality_budget_pct,
            "final_budget_pct": final_budget_pct,
            "budget_mult": budget_mult,
            "tilt_strength": tilt_strength,
            "floor_weight": floor_weight,
            "power": power,
        }

    diagnostics = []
    if config.get("s1_b2_product_budget_diagnostics_enabled", True):
        for product in candidate_products:
            meta = meta_map.get(product, {})
            diagnostics.append({
                "date": date_str,
                "scope": "s1_b2_product_budget",
                "name": product,
                "nav": nav,
                "n_products": n_products,
                "product_score": meta.get("product_score", np.nan),
                "put_score": meta.get("put_score", np.nan),
                "call_score": meta.get("call_score", np.nan),
                "side_count": meta.get("side_count", 0),
                "candidate_count": meta.get("candidate_count", 0),
                "equal_budget_pct": meta.get("equal_budget_pct", np.nan),
                "quality_budget_pct": meta.get("quality_budget_pct", np.nan),
                "final_budget_pct": meta.get("final_budget_pct", np.nan),
                "budget_mult": meta.get("budget_mult", np.nan),
                "tilt_strength": tilt_strength,
            })

    return side_df, budget_map, meta_map, diagnostics
