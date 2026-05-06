"""Pure sizing helpers shared by strategy rules and the minute engine."""

import numpy as np


def calc_s1_size(nav, margin_per, single_margin, iv_scale):
    """Size one S1 side from a margin budget."""
    if single_margin <= 0:
        return 1
    return max(1, int(nav * margin_per / 2 * iv_scale / single_margin))


def calc_s1_stress_size(nav, stress_budget_pct, one_contract_stress_loss,
                        iv_scale=1.0, min_qty=1, max_qty=50):
    """Size S1 by stress-risk budget instead of target margin usage."""
    if one_contract_stress_loss is None or not np.isfinite(one_contract_stress_loss):
        return 0
    if one_contract_stress_loss <= 0:
        return int(max(min_qty, 1))
    budget = float(nav) * float(stress_budget_pct or 0.0) * float(iv_scale or 1.0)
    if budget <= 0:
        return 0
    qty = int(budget / float(one_contract_stress_loss))
    qty = max(int(min_qty or 1), qty)
    if max_qty is not None and int(max_qty) > 0:
        qty = min(qty, int(max_qty))
    return max(qty, 0)


def calc_s3_size(nav, margin_per, sell_margin, s3_ratio, iv_scale):
    """Return S3 buy/sell quantities for a fixed ratio."""
    if sell_margin <= 0:
        return 1, s3_ratio
    buy_qty = max(1, int(nav * margin_per / 2 * iv_scale / (sell_margin * s3_ratio)))
    sell_qty = buy_qty * s3_ratio
    return buy_qty, sell_qty


def calc_s4_size(nav, s4_prem, n_s4_products, cost_per_hand, max_hands=5):
    """Size one S4 side from a premium budget."""
    if n_s4_products <= 0 or cost_per_hand <= 0:
        return 1
    budget = nav * s4_prem / n_s4_products / 2
    qty = max(1, int(budget / cost_per_hand))
    return min(qty, max_hands)


def calc_s3_size_v2(nav, margin_per, sell_margin, buy_premium,
                    sell_premium, mult, iv_scale,
                    ratio_candidates=(2, 3), net_premium_tolerance=0.3):
    """Choose the smallest S3 ratio that passes the net-premium tolerance."""
    if sell_margin <= 0 or buy_premium <= 0 or sell_premium <= 0:
        return None
    for ratio in sorted(ratio_candidates):
        buy_qty = max(
            1,
            int(nav * margin_per / 2 * iv_scale / (sell_margin * ratio)),
        )
        sell_qty = buy_qty * ratio
        buy_cost = buy_premium * mult * buy_qty
        sell_income = sell_premium * mult * sell_qty
        net_premium = sell_income - buy_cost
        if net_premium >= -buy_cost * net_premium_tolerance:
            return buy_qty, sell_qty, ratio
    return None


__all__ = [
    "calc_s1_size",
    "calc_s1_stress_size",
    "calc_s3_size",
    "calc_s3_size_v2",
    "calc_s4_size",
]
