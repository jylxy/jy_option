"""Portfolio-level risk helpers for the short-premium backtest.

This module owns cross-product constraints: margin caps, bucket/product
concentration, correlation groups, cash Greeks, and stress-loss budgets. The
minute engine supplies current positions and pending orders, while this module
decides whether the next candidate keeps the portfolio inside its risk budget.
"""

from collections import defaultdict

import numpy as np
import pandas as pd


def check_margin_ok(cur_total_margin, cur_strategy_margin, new_margin,
                    nav, margin_cap=0.50, strategy_cap=0.25):
    """Return whether adding margin stays under total and strategy caps."""
    try:
        nav = float(nav)
        cur_total_margin = float(cur_total_margin)
        cur_strategy_margin = float(cur_strategy_margin)
        new_margin = float(new_margin)
    except (TypeError, ValueError):
        return False

    values = (nav, cur_total_margin, cur_strategy_margin, new_margin)
    if any(not np.isfinite(v) for v in values):
        return False
    if nav <= 0 or cur_total_margin < 0 or cur_strategy_margin < 0 or new_margin < 0:
        return False

    margin_cap = float(margin_cap or 0.0)
    strategy_cap = float(strategy_cap or 0.0)
    if margin_cap < 0 or strategy_cap < 0:
        return False

    if margin_cap and (cur_total_margin + new_margin) / nav > margin_cap:
        return False
    if strategy_cap and (cur_strategy_margin + new_margin) / nav > strategy_cap:
        return False
    return True


def candidate_cash_greeks(row, opt_type, mult, qty, role="sell"):
    sign = 1.0 if role in ("buy", "protect") else -1.0
    spot = float(row.get("spot_close", 0.0) or 0.0)
    vega = float(row.get("vega", 0.0) or 0.0)
    gamma = float(row.get("gamma", 0.0) or 0.0)
    return {
        "cash_vega": sign * vega * float(mult) * float(qty),
        "cash_gamma": sign * gamma * float(mult) * float(qty) * spot * spot,
    }


def iter_open_sell_exposures(positions, pending_opens, normalize_product_key,
                             include_pending=True):
    for pos in positions:
        if pos.role == "sell":
            yield normalize_product_key(pos.product), float(pos.cur_margin())
    if not include_pending:
        return
    for item in pending_opens:
        if item.get("role") == "sell":
            yield normalize_product_key(item.get("product", "")), float(item.get("margin", 0.0) or 0.0)


def get_open_sell_margin_total(positions, pending_opens, strat=None, include_pending=True):
    total = 0.0
    for pos in positions:
        if pos.role == "sell" and (strat is None or pos.strat == strat):
            total += float(pos.cur_margin())
    if not include_pending:
        return total
    for item in pending_opens:
        if item.get("role") == "sell" and (strat is None or item.get("strat") == strat):
            total += float(item.get("margin", 0.0) or 0.0)
    return total


def get_open_greek_state(positions, pending_opens, get_product_bucket,
                         include_pending=True):
    state = {
        "cash_delta": 0.0,
        "cash_vega": 0.0,
        "cash_gamma": 0.0,
        "bucket_vega": defaultdict(float),
        "bucket_gamma": defaultdict(float),
    }
    for pos in positions:
        bucket = get_product_bucket(pos.product)
        cd = pos.cash_delta()
        cv = pos.cash_vega()
        cg = pos.cash_gamma()
        state["cash_delta"] += cd
        state["cash_vega"] += cv
        state["cash_gamma"] += cg
        state["bucket_vega"][bucket] += cv
        state["bucket_gamma"][bucket] += cg
    if include_pending:
        for item in pending_opens:
            bucket = get_product_bucket(item.get("product", ""))
            cv = float(item.get("cash_vega", 0.0) or 0.0)
            cg = float(item.get("cash_gamma", 0.0) or 0.0)
            state["cash_vega"] += cv
            state["cash_gamma"] += cg
            state["bucket_vega"][bucket] += cv
            state["bucket_gamma"][bucket] += cg
    return state


def get_open_stress_loss_state(positions, pending_opens, get_product_bucket,
                               include_pending=True):
    state = {
        "stress_loss": 0.0,
        "bucket_stress_loss": defaultdict(float),
    }
    for pos in positions:
        loss = float(getattr(pos, "stress_loss", 0.0) or 0.0)
        bucket = get_product_bucket(pos.product)
        state["stress_loss"] += loss
        state["bucket_stress_loss"][bucket] += loss
    if include_pending:
        for item in pending_opens:
            if item.get("role") != "sell":
                continue
            one_loss = float(item.get("one_contract_stress_loss", 0.0) or 0.0)
            loss = float(item.get("stress_loss", one_loss * float(item.get("n", 0) or 0)) or 0.0)
            bucket = get_product_bucket(item.get("product", ""))
            state["stress_loss"] += loss
            state["bucket_stress_loss"][bucket] += loss
    return state


def passes_greek_budget(config, *, product, nav, greek_state, get_product_bucket,
                        new_cash_vega=0.0, new_cash_gamma=0.0):
    if not np.isfinite(nav) or nav <= 0:
        return False

    vega_cap = float(config.get("portfolio_cash_vega_cap", 0.0) or 0.0)
    gamma_cap = float(config.get("portfolio_cash_gamma_cap", 0.0) or 0.0)
    if vega_cap > 0 and abs(greek_state["cash_vega"] + new_cash_vega) / nav > vega_cap:
        return False
    if gamma_cap > 0 and abs(greek_state["cash_gamma"] + new_cash_gamma) / nav > gamma_cap:
        return False

    bucket = get_product_bucket(product)
    bucket_vega_cap = float(config.get("portfolio_bucket_cash_vega_cap", 0.0) or 0.0)
    bucket_gamma_cap = float(config.get("portfolio_bucket_cash_gamma_cap", 0.0) or 0.0)
    if bucket_vega_cap > 0 and abs(greek_state["bucket_vega"].get(bucket, 0.0) + new_cash_vega) / nav > bucket_vega_cap:
        return False
    if bucket_gamma_cap > 0 and abs(greek_state["bucket_gamma"].get(bucket, 0.0) + new_cash_gamma) / nav > bucket_gamma_cap:
        return False
    return True


def passes_stress_budget(config, *, nav, greek_state, stress_state,
                         get_product_bucket, product=None, budget=None,
                         new_cash_vega=0.0, new_cash_gamma=0.0,
                         new_stress_loss=0.0):
    if not config.get("portfolio_stress_gate_enabled", False):
        return True
    if not np.isfinite(nav) or nav <= 0:
        return False
    budget = budget or {}

    loss_cap = float(budget.get(
        "portfolio_stress_loss_cap",
        config.get("portfolio_stress_loss_cap", 0.03),
    ) or 0.0)
    if loss_cap > 0 and new_stress_loss > 0:
        if (stress_state["stress_loss"] + float(new_stress_loss)) / nav > loss_cap:
            return False

    bucket_cap = float(budget.get(
        "portfolio_bucket_stress_loss_cap",
        config.get("portfolio_bucket_stress_loss_cap", 0.0),
    ) or 0.0)
    if bucket_cap > 0 and product is not None and new_stress_loss > 0:
        bucket = get_product_bucket(product)
        if (stress_state["bucket_stress_loss"].get(bucket, 0.0) + float(new_stress_loss)) / nav > bucket_cap:
            return False

    move = float(config.get("portfolio_stress_spot_move_pct", 0.03) or 0.0)
    iv_up_points = float(config.get("portfolio_stress_iv_up_points", 5.0) or 0.0)
    cash_delta = greek_state["cash_delta"]
    cash_gamma = greek_state["cash_gamma"] + new_cash_gamma
    cash_vega = greek_state["cash_vega"] + new_cash_vega
    stress_pnl = -abs(cash_delta) * move + 0.5 * cash_gamma * move * move + cash_vega * iv_up_points
    stress_loss = max(0.0, -float(stress_pnl))
    return loss_cap <= 0 or stress_loss / nav <= loss_cap


def product_return_series(spot_history, normalize_product_key, product, current_date=None):
    product = normalize_product_key(product)
    hist = spot_history.get(product, {})
    dates = hist.get("dates", [])
    spots = hist.get("spots", [])
    if not dates or not spots:
        return pd.Series(dtype=float)
    series = pd.Series(spots, index=pd.Index(dates, dtype=object), dtype=float)
    series = series[~series.index.duplicated(keep="last")]
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    series = series[series > 0]
    if current_date is not None:
        series = series[series.index <= current_date]
    if len(series) < 2:
        return pd.Series(dtype=float)
    return np.log(series).diff().dropna()


def recent_product_corr(config, spot_history, normalize_product_key,
                        product, peer_product, current_date):
    window = int(config.get("portfolio_corr_window", 60) or 0)
    min_periods = int(config.get("portfolio_corr_min_periods", 20) or 0)
    if window <= 1:
        return np.nan
    left = product_return_series(
        spot_history,
        normalize_product_key,
        product,
        current_date=current_date,
    ).tail(window)
    right = product_return_series(
        spot_history,
        normalize_product_key,
        peer_product,
        current_date=current_date,
    ).tail(window)
    if left.empty or right.empty:
        return np.nan
    aligned = pd.concat([left.rename("x"), right.rename("y")], axis=1, join="inner").dropna()
    if len(aligned) < max(min_periods, 2):
        return np.nan
    return float(aligned["x"].corr(aligned["y"]))


def get_open_concentration_state(positions, pending_opens, normalize_product_key,
                                 get_product_bucket, get_product_corr_group,
                                 include_pending=True):
    state = {
        "product_margin": defaultdict(float),
        "product_side_margin": defaultdict(float),
        "product_side_stress_loss": defaultdict(float),
        "bucket_margin": defaultdict(float),
        "bucket_products": defaultdict(set),
        "corr_group_margin": defaultdict(float),
        "corr_group_stress_loss": defaultdict(float),
        "corr_products": defaultdict(set),
        "contract_lots": defaultdict(float),
        "contract_margin": defaultdict(float),
        "contract_stress_loss": defaultdict(float),
    }

    def add_exposure(product, option_type, code, margin, lots, stress_loss):
        product = normalize_product_key(product)
        if not product:
            return
        margin = float(margin or 0.0)
        lots = float(lots or 0.0)
        stress_loss = float(stress_loss or 0.0)
        side = str(option_type or "").upper()[:1]
        code = str(code or "")
        bucket = get_product_bucket(product)
        corr_group = get_product_corr_group(product)
        state["product_margin"][product] += margin
        if side:
            product_side = (product, side)
            state["product_side_margin"][product_side] += margin
            state["product_side_stress_loss"][product_side] += stress_loss
        state["bucket_margin"][bucket] += margin
        state["bucket_products"][bucket].add(product)
        state["corr_group_margin"][corr_group] += margin
        state["corr_group_stress_loss"][corr_group] += stress_loss
        state["corr_products"][corr_group].add(product)
        if code:
            state["contract_lots"][code] += lots
            state["contract_margin"][code] += margin
            state["contract_stress_loss"][code] += stress_loss

    for pos in positions:
        if pos.role != "sell":
            continue
        add_exposure(
            pos.product,
            getattr(pos, "opt_type", ""),
            getattr(pos, "code", ""),
            pos.cur_margin(),
            getattr(pos, "n", 0.0),
            getattr(pos, "stress_loss", 0.0),
        )
    if include_pending:
        for item in pending_opens:
            if item.get("role") != "sell":
                continue
            one_loss = float(item.get("one_contract_stress_loss", 0.0) or 0.0)
            lots = float(item.get("n", 0.0) or 0.0)
            add_exposure(
                item.get("product", ""),
                item.get("opt_type", ""),
                item.get("code", ""),
                item.get("margin", 0.0),
                lots,
                item.get("stress_loss", one_loss * lots),
            )
    return state


def passes_concentration_limits(config, *, product, nav, new_margin, date_str,
                                budget, concentration_state, spot_history,
                                normalize_product_key, get_product_bucket,
                                get_product_corr_group, option_type=None,
                                code=None, new_lots=0.0,
                                new_stress_loss=0.0):
    product = normalize_product_key(product)
    product_cap = float(
        budget.get("product_margin_cap", config.get("portfolio_product_margin_cap", 0.08)) or 0.0
    )
    product_side_cap = float(
        budget.get(
            "product_side_margin_cap",
            config.get("portfolio_product_side_margin_cap", 0.0),
        ) or 0.0
    )
    product_side_stress_cap = float(
        budget.get(
            "product_side_stress_loss_cap",
            config.get("portfolio_product_side_stress_loss_cap", 0.0),
        ) or 0.0
    )
    bucket = get_product_bucket(product)
    corr_group = get_product_corr_group(product)
    side = str(option_type or "").upper()[:1]
    code = str(code or "")
    new_margin = float(new_margin or 0.0)
    new_lots = float(new_lots or 0.0)
    new_stress_loss = float(new_stress_loss or 0.0)

    if product_cap > 0:
        if (concentration_state["product_margin"].get(product, 0.0) + new_margin) / nav > product_cap:
            return False
    if side:
        product_side = (product, side)
        if product_side_cap > 0:
            used = concentration_state["product_side_margin"].get(product_side, 0.0)
            if (used + new_margin) / nav > product_side_cap:
                return False
        if product_side_stress_cap > 0 and new_stress_loss > 0:
            used = concentration_state["product_side_stress_loss"].get(product_side, 0.0)
            if (used + new_stress_loss) / nav > product_side_stress_cap:
                return False

    if config.get("portfolio_bucket_control_enabled", True):
        bucket_cap = float(
            budget.get("bucket_margin_cap", config.get("portfolio_bucket_margin_cap", 0.18)) or 0.0
        )
        bucket_max_active = int(config.get("portfolio_bucket_max_active_products", 3) or 0)
        bucket_products = concentration_state["bucket_products"].get(bucket, set())
        if bucket_max_active > 0 and product not in bucket_products and len(bucket_products) >= bucket_max_active:
            return False
        if bucket_cap > 0:
            if (concentration_state["bucket_margin"].get(bucket, 0.0) + new_margin) / nav > bucket_cap:
                return False

    if config.get("portfolio_corr_control_enabled", True):
        corr_max_active = int(config.get("portfolio_corr_group_max_active_products", 2) or 0)
        corr_group_cap = float(
            budget.get(
                "corr_group_margin_cap",
                config.get("portfolio_corr_group_margin_cap", 0.0),
            ) or 0.0
        )
        corr_group_stress_cap = float(
            budget.get(
                "corr_group_stress_loss_cap",
                config.get("portfolio_corr_group_stress_loss_cap", 0.0),
            ) or 0.0
        )
        corr_products = concentration_state["corr_products"].get(corr_group, set())
        if corr_max_active > 0 and product not in corr_products and len(corr_products) >= corr_max_active:
            return False
        if corr_group_cap > 0:
            used = concentration_state["corr_group_margin"].get(corr_group, 0.0)
            if (used + new_margin) / nav > corr_group_cap:
                return False
        if corr_group_stress_cap > 0 and new_stress_loss > 0:
            used = concentration_state["corr_group_stress_loss"].get(corr_group, 0.0)
            if (used + new_stress_loss) / nav > corr_group_stress_cap:
                return False
        if config.get("portfolio_dynamic_corr_control_enabled", True) and date_str is not None:
            corr_threshold = float(config.get("portfolio_corr_threshold", 0.70) or 0.0)
            max_high_corr_peers = int(config.get("portfolio_corr_max_high_corr_peers", 1) or 0)
            if corr_threshold > 0 and max_high_corr_peers >= 0:
                high_corr_peers = 0
                for peer in corr_products:
                    if peer == product:
                        continue
                    corr = recent_product_corr(
                        config,
                        spot_history,
                        normalize_product_key,
                        product,
                        peer,
                        current_date=date_str,
                    )
                    if pd.notna(corr) and corr >= corr_threshold:
                        high_corr_peers += 1
                if high_corr_peers > max_high_corr_peers:
                    return False

    if code:
        contract_lot_cap = int(config.get("portfolio_contract_lot_cap", 0) or 0)
        contract_stress_cap = float(
            budget.get(
                "contract_stress_loss_cap",
                config.get("portfolio_contract_stress_loss_cap", 0.0),
            ) or 0.0
        )
        if contract_lot_cap > 0:
            used_lots = concentration_state["contract_lots"].get(code, 0.0)
            if used_lots + new_lots > contract_lot_cap:
                return False
        if contract_stress_cap > 0 and new_stress_loss > 0:
            used_stress = concentration_state["contract_stress_loss"].get(code, 0.0)
            if (used_stress + new_stress_loss) / nav > contract_stress_cap:
                return False

    return True


def passes_portfolio_construction(config, *, product, nav, new_margin,
                                  positions, pending_opens, spot_history,
                                  normalize_product_key, get_product_bucket,
                                  get_product_corr_group, date_str=None,
                                  new_cash_vega=0.0, new_cash_gamma=0.0,
                                  new_stress_loss=0.0, budget=None,
                                  include_pending=True, option_type=None,
                                  code=None, new_lots=0.0):
    budget = budget or {}
    greek_state = get_open_greek_state(
        positions,
        pending_opens,
        get_product_bucket,
        include_pending=include_pending,
    )
    stress_state = get_open_stress_loss_state(
        positions,
        pending_opens,
        get_product_bucket,
        include_pending=include_pending,
    )
    if not config.get("portfolio_construction_enabled", True):
        return (
            passes_greek_budget(
                config,
                product=product,
                nav=nav,
                greek_state=greek_state,
                get_product_bucket=get_product_bucket,
                new_cash_vega=new_cash_vega,
                new_cash_gamma=new_cash_gamma,
            ) and
            passes_stress_budget(
                config,
                nav=nav,
                greek_state=greek_state,
                stress_state=stress_state,
                get_product_bucket=get_product_bucket,
                product=product,
                budget=budget,
                new_cash_vega=new_cash_vega,
                new_cash_gamma=new_cash_gamma,
                new_stress_loss=new_stress_loss,
            )
        )
    if not np.isfinite(nav) or nav <= 0:
        return False

    concentration_state = get_open_concentration_state(
        positions,
        pending_opens,
        normalize_product_key,
        get_product_bucket,
        get_product_corr_group,
        include_pending=include_pending,
    )
    if not passes_concentration_limits(
        config,
        product=product,
        nav=nav,
        new_margin=new_margin,
        date_str=date_str,
        budget=budget,
        concentration_state=concentration_state,
        spot_history=spot_history,
        normalize_product_key=normalize_product_key,
        get_product_bucket=get_product_bucket,
        get_product_corr_group=get_product_corr_group,
        option_type=option_type,
        code=code,
        new_lots=new_lots,
        new_stress_loss=new_stress_loss,
    ):
        return False

    return (
        passes_greek_budget(
            config,
            product=product,
            nav=nav,
            greek_state=greek_state,
            get_product_bucket=get_product_bucket,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
        ) and
        passes_stress_budget(
            config,
            nav=nav,
            greek_state=greek_state,
            stress_state=stress_state,
            get_product_bucket=get_product_bucket,
            product=product,
            budget=budget,
            new_cash_vega=new_cash_vega,
            new_cash_gamma=new_cash_gamma,
            new_stress_loss=new_stress_loss,
        )
    )


def diversify_product_order(config, products, get_product_bucket):
    if not config.get("portfolio_bucket_round_robin", True):
        return list(products)
    bucket_products = defaultdict(list)
    bucket_order = []
    for product in products:
        bucket = get_product_bucket(product)
        if bucket not in bucket_products:
            bucket_order.append(bucket)
        bucket_products[bucket].append(product)
    diversified = []
    has_remaining = True
    while has_remaining:
        has_remaining = False
        for bucket in bucket_order:
            if bucket_products[bucket]:
                diversified.append(bucket_products[bucket].pop(0))
                has_remaining = True
    return diversified
