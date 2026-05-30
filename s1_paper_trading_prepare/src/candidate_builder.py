"""Candidate-builder facade for the current S1 mainline."""

from __future__ import annotations

from typing import Any


def describe_candidate_builder(config: dict[str, Any]) -> dict[str, Any]:
    """Return the active contract candidate and ranking settings."""
    return {
        "selector": "server_deploy/src/s1_contract_scoring.py::select_s1_sell",
        "ranking_mode": config.get("s1_ranking_mode"),
        "min_volume": config.get("s1_min_volume"),
        "min_oi": config.get("s1_min_oi"),
        "min_option_price": config.get("s1_min_option_price"),
        "delta_floor": config.get("s1_sell_delta_floor"),
        "delta_cap": config.get("s1_sell_delta_cap"),
        "target_abs_delta": config.get("s1_target_abs_delta"),
        "b6_enabled": config.get("s1_b6_contract_rank_enabled"),
        "variance_carry_filter_enabled": config.get("s1_variance_carry_filter_enabled"),
        "b15b2_entry_enabled": config.get("s1_b15b2_entry_enabled"),
        "l4_custom_score_enabled": config.get("s1_l4_custom_score_enabled"),
        "l4_custom_score_mode": config.get("s1_l4_custom_score_mode"),
        "l4_custom_keep_quantile": config.get("s1_l4_custom_keep_quantile"),
        "l4_custom_gate_min_count": config.get("s1_l4_custom_gate_min_count"),
        "l4_custom_gate_min_keep": config.get("s1_l4_custom_gate_min_keep"),
        "l4_custom_score_components": config.get("s1_l4_custom_score_components"),
        "l4_product_side_fields": config.get("s1_l4_product_side_fields"),
    }
