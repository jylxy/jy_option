"""Portfolio diagnostics record builders.

This module contains pure aggregation helpers used by the minute engine. Keeping
diagnostics outside the event loop class makes it easier to test and later add
lite/full output modes without touching trading logic.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np


def _state_bucket():
    return {
        "margin": 0.0,
        "cash_vega": 0.0,
        "cash_gamma": 0.0,
        "stress_loss": 0.0,
        "products": set(),
        "positions": 0,
    }


def _s1_product_side_bucket():
    return {
        "margin": 0.0,
        "cash_vega": 0.0,
        "cash_gamma": 0.0,
        "cash_theta": 0.0,
        "stress_loss": 0.0,
        "contracts": set(),
        "contract_lots": defaultdict(float),
        "contract_stress_loss": defaultdict(float),
        "lots": 0.0,
        "open_premium": 0.0,
        "liability": 0.0,
    }


def build_portfolio_diagnostics_records(
    *,
    positions: Sequence[object],
    config: Mapping[str, object],
    budget: Mapping[str, object],
    date_str: str,
    nav: float,
    current_vol_regimes: Mapping[str, str],
    current_portfolio_regime: str,
    normalize_product_key: Callable[[object], str],
    get_product_bucket: Callable[[object], str],
    get_product_corr_group: Callable[[object], str],
) -> list[dict]:
    """Build daily bucket/correlation/product-side diagnostics records."""
    nav = max(float(nav), 1.0)
    bucket_max_active = int(config.get("portfolio_bucket_max_active_products", 3) or 0)
    corr_max_active = int(config.get("portfolio_corr_group_max_active_products", 2) or 0)
    bucket_margin_cap = float(budget.get("bucket_margin_cap", 0.0) or 0.0)
    bucket_stress_cap = float(budget.get("portfolio_bucket_stress_loss_cap", 0.0) or 0.0)
    corr_group_margin_cap = float(budget.get("corr_group_margin_cap", 0.0) or 0.0)
    corr_group_stress_cap = float(budget.get("corr_group_stress_loss_cap", 0.0) or 0.0)
    product_side_margin_cap = float(budget.get("product_side_margin_cap", 0.0) or 0.0)
    product_side_stress_cap = float(budget.get("product_side_stress_loss_cap", 0.0) or 0.0)
    contract_lot_cap = int(config.get("portfolio_contract_lot_cap", 0) or 0)
    contract_stress_cap = float(budget.get("contract_stress_loss_cap", 0.0) or 0.0)

    bucket_state = defaultdict(_state_bucket)
    corr_state = defaultdict(_state_bucket)
    s1_product_side_state = defaultdict(_s1_product_side_bucket)

    for pos in positions:
        bucket = get_product_bucket(pos.product)
        corr_group = get_product_corr_group(pos.product)
        margin = pos.cur_margin() if pos.role == "sell" else 0.0
        cv = pos.cash_vega()
        cg = pos.cash_gamma()
        stress_loss = float(getattr(pos, "stress_loss", 0.0) or 0.0)
        for state, key in ((bucket_state, bucket), (corr_state, corr_group)):
            state[key]["margin"] += margin
            state[key]["cash_vega"] += cv
            state[key]["cash_gamma"] += cg
            state[key]["stress_loss"] += stress_loss
            state[key]["products"].add(normalize_product_key(pos.product))
            state[key]["positions"] += 1
        if pos.strat == "S1" and pos.role == "sell":
            side = str(pos.opt_type or "").upper()[:1]
            product = normalize_product_key(pos.product)
            data = s1_product_side_state[(product, side)]
            data["margin"] += margin
            data["cash_vega"] += cv
            data["cash_gamma"] += cg
            data["cash_theta"] += pos.cash_theta()
            data["stress_loss"] += stress_loss
            data["contracts"].add(pos.code)
            lots = float(pos.n or 0.0)
            data["lots"] += lots
            data["contract_lots"][pos.code] += lots
            data["contract_stress_loss"][pos.code] += stress_loss
            data["open_premium"] += float(pos.open_price) * float(pos.mult) * lots
            data["liability"] += float(pos.cur_price) * float(pos.mult) * lots

    records: list[dict] = []
    for bucket, data in bucket_state.items():
        records.append({
            "date": date_str,
            "scope": "bucket",
            "name": bucket,
            "margin_pct": data["margin"] / nav,
            "cash_vega_pct": data["cash_vega"] / nav,
            "cash_gamma_pct": data["cash_gamma"] / nav,
            "stress_loss_pct": data["stress_loss"] / nav,
            "margin_cap": bucket_margin_cap,
            "stress_loss_cap": bucket_stress_cap,
            "margin_cap_used": (
                data["margin"] / nav / bucket_margin_cap if bucket_margin_cap > 0 else np.nan
            ),
            "stress_cap_used": (
                data["stress_loss"] / nav / bucket_stress_cap if bucket_stress_cap > 0 else np.nan
            ),
            "n_products": len(data["products"]),
            "n_positions": data["positions"],
            "max_active_products": bucket_max_active,
            "active_product_cap_used": (
                len(data["products"]) / bucket_max_active if bucket_max_active > 0 else np.nan
            ),
            "portfolio_vol_regime": current_portfolio_regime,
        })

    for (product, side), data in s1_product_side_state.items():
        bucket = get_product_bucket(product)
        corr_group = get_product_corr_group(product)
        unrealized_premium = data["open_premium"] - data["liability"]
        max_contract_lots = max(data["contract_lots"].values()) if data["contract_lots"] else 0.0
        max_contract_stress = (
            max(data["contract_stress_loss"].values())
            if data["contract_stress_loss"] else 0.0
        )
        records.append({
            "date": date_str,
            "scope": "s1_product_side",
            "name": f"{product}:{side}",
            "product": product,
            "option_type": side,
            "bucket": bucket,
            "corr_group": corr_group,
            "product_vol_regime": current_vol_regimes.get(product, ""),
            "lots": data["lots"],
            "n_contracts": len(data["contracts"]),
            "max_contract_lots": max_contract_lots,
            "margin_pct": data["margin"] / nav,
            "cash_vega_pct": data["cash_vega"] / nav,
            "cash_gamma_pct": data["cash_gamma"] / nav,
            "cash_theta_pct": data["cash_theta"] / nav,
            "stress_loss_pct": data["stress_loss"] / nav,
            "max_contract_stress_loss_pct": max_contract_stress / nav,
            "margin_cap": product_side_margin_cap,
            "stress_loss_cap": product_side_stress_cap,
            "contract_lot_cap": contract_lot_cap,
            "contract_stress_loss_cap": contract_stress_cap,
            "margin_cap_used": (
                data["margin"] / nav / product_side_margin_cap
                if product_side_margin_cap > 0 else np.nan
            ),
            "stress_cap_used": (
                data["stress_loss"] / nav / product_side_stress_cap
                if product_side_stress_cap > 0 else np.nan
            ),
            "max_contract_lot_cap_used": (
                max_contract_lots / contract_lot_cap if contract_lot_cap > 0 else np.nan
            ),
            "max_contract_stress_cap_used": (
                max_contract_stress / nav / contract_stress_cap
                if contract_stress_cap > 0 else np.nan
            ),
            "open_premium": data["open_premium"],
            "current_liability": data["liability"],
            "unrealized_premium": unrealized_premium,
            "open_premium_pct": data["open_premium"] / nav,
            "current_liability_pct": data["liability"] / nav,
            "unrealized_premium_pct": unrealized_premium / nav,
            "portfolio_vol_regime": current_portfolio_regime,
        })

    for group, data in corr_state.items():
        records.append({
            "date": date_str,
            "scope": "corr_group",
            "name": group,
            "margin_pct": data["margin"] / nav,
            "cash_vega_pct": data["cash_vega"] / nav,
            "cash_gamma_pct": data["cash_gamma"] / nav,
            "stress_loss_pct": data["stress_loss"] / nav,
            "margin_cap": corr_group_margin_cap,
            "stress_loss_cap": corr_group_stress_cap,
            "margin_cap_used": (
                data["margin"] / nav / corr_group_margin_cap
                if corr_group_margin_cap > 0 else np.nan
            ),
            "stress_cap_used": (
                data["stress_loss"] / nav / corr_group_stress_cap
                if corr_group_stress_cap > 0 else np.nan
            ),
            "n_products": len(data["products"]),
            "n_positions": data["positions"],
            "max_active_products": corr_max_active,
            "active_product_cap_used": (
                len(data["products"]) / corr_max_active if corr_max_active > 0 else np.nan
            ),
            "portfolio_vol_regime": current_portfolio_regime,
        })

    return records
