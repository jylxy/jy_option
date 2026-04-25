"""Portfolio open-budget helpers for S1/S3 sizing and risk brakes.

The minute engine owns portfolio state. This module owns the deterministic
budget transformations so they can be tested without loading market data.
"""

NAN = float("nan")


def safe_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if value == value and value not in (float("inf"), float("-inf")) else float(default)


def safe_pct(value, default=0.0, upper=1.0):
    value = safe_float(value, default)
    if value < 0:
        return 0.0
    if upper is not None:
        return min(value, float(upper))
    return value


def float_or_default(value, default):
    return safe_float(value, default) if value else float(default)


def regime_budget_prefix(regime):
    if regime == "falling_vol_carry":
        return "falling"
    if regime == "low_stable_vol":
        return "low"
    if regime == "high_rising_vol":
        return "high"
    return "normal"


def normalize_open_budget(budget):
    budget = dict(budget)
    budget["margin_cap"] = safe_pct(budget.get("margin_cap"), 0.50)
    budget["s1_margin_cap"] = min(
        safe_pct(budget.get("s1_margin_cap"), 0.25),
        budget["margin_cap"],
    )
    budget["s3_margin_cap"] = min(
        safe_pct(budget.get("s3_margin_cap"), 0.25),
        budget["margin_cap"],
    )
    budget["margin_per"] = safe_pct(budget.get("margin_per"), 0.02)

    product_cap = safe_pct(budget.get("product_margin_cap"), 0.0)
    product_side_cap = safe_pct(budget.get("product_side_margin_cap"), 0.0)
    bucket_cap = safe_pct(budget.get("bucket_margin_cap"), 0.0)
    corr_group_cap = safe_pct(budget.get("corr_group_margin_cap"), 0.0)
    if bucket_cap > 0:
        bucket_cap = min(bucket_cap, budget["margin_cap"])
    if corr_group_cap > 0:
        corr_group_cap = min(corr_group_cap, budget["margin_cap"])
    if product_cap > 0:
        product_cap = min(product_cap, budget["margin_cap"])
        if bucket_cap > 0:
            product_cap = min(product_cap, bucket_cap)
        if corr_group_cap > 0:
            product_cap = min(product_cap, corr_group_cap)
    if product_side_cap > 0:
        product_side_cap = min(product_side_cap, budget["margin_cap"])
        if product_cap > 0:
            product_side_cap = min(product_side_cap, product_cap)
    budget["product_margin_cap"] = product_cap
    budget["product_side_margin_cap"] = product_side_cap
    budget["bucket_margin_cap"] = bucket_cap
    budget["corr_group_margin_cap"] = corr_group_cap

    stress_cap = safe_pct(budget.get("portfolio_stress_loss_cap"), 0.0)
    bucket_stress_cap = safe_pct(budget.get("portfolio_bucket_stress_loss_cap"), 0.0)
    product_side_stress_cap = safe_pct(budget.get("product_side_stress_loss_cap"), 0.0)
    corr_group_stress_cap = safe_pct(budget.get("corr_group_stress_loss_cap"), 0.0)
    contract_stress_cap = safe_pct(budget.get("contract_stress_loss_cap"), 0.0)
    s1_stress_budget = safe_pct(budget.get("s1_stress_loss_budget_pct"), 0.0)
    if stress_cap > 0:
        if bucket_stress_cap > 0:
            bucket_stress_cap = min(bucket_stress_cap, stress_cap)
        if product_side_stress_cap > 0:
            product_side_stress_cap = min(product_side_stress_cap, stress_cap)
        if corr_group_stress_cap > 0:
            corr_group_stress_cap = min(corr_group_stress_cap, stress_cap)
        if contract_stress_cap > 0:
            contract_stress_cap = min(contract_stress_cap, stress_cap)
        if s1_stress_budget > 0:
            s1_stress_budget = min(s1_stress_budget, stress_cap)
    budget["portfolio_stress_loss_cap"] = stress_cap
    budget["portfolio_bucket_stress_loss_cap"] = bucket_stress_cap
    budget["product_side_stress_loss_cap"] = product_side_stress_cap
    budget["corr_group_stress_loss_cap"] = corr_group_stress_cap
    budget["contract_stress_loss_cap"] = contract_stress_cap
    budget["s1_stress_loss_budget_pct"] = s1_stress_budget
    return budget


def build_base_open_budget(config):
    cfg = config or {}
    return normalize_open_budget({
        "portfolio_regime": "normal_vol",
        "margin_cap": float_or_default(cfg.get("margin_cap", 0.50), 0.50),
        "s1_margin_cap": float_or_default(cfg.get("s1_margin_cap", 0.25), 0.25),
        "s3_margin_cap": float_or_default(cfg.get("s3_margin_cap", 0.25), 0.25),
        "margin_per": float_or_default(cfg.get("margin_per", 0.02), 0.02),
        "product_margin_cap": safe_float(cfg.get("portfolio_product_margin_cap", 0.08), 0.0),
        "product_side_margin_cap": safe_float(
            cfg.get("portfolio_product_side_margin_cap", 0.0), 0.0
        ),
        "bucket_margin_cap": safe_float(cfg.get("portfolio_bucket_margin_cap", 0.18), 0.0),
        "corr_group_margin_cap": safe_float(
            cfg.get("portfolio_corr_group_margin_cap", 0.0), 0.0
        ),
        "portfolio_stress_loss_cap": safe_float(cfg.get("portfolio_stress_loss_cap", 0.03), 0.0),
        "portfolio_bucket_stress_loss_cap": safe_float(
            cfg.get("portfolio_bucket_stress_loss_cap", 0.0), 0.0
        ),
        "product_side_stress_loss_cap": safe_float(
            cfg.get("portfolio_product_side_stress_loss_cap", 0.0), 0.0
        ),
        "corr_group_stress_loss_cap": safe_float(
            cfg.get("portfolio_corr_group_stress_loss_cap", 0.0), 0.0
        ),
        "contract_stress_loss_cap": safe_float(
            cfg.get("portfolio_contract_stress_loss_cap", 0.0), 0.0
        ),
        "s1_stress_loss_budget_pct": safe_float(cfg.get("s1_stress_loss_budget_pct", 0.0010), 0.0),
    })


def apply_regime_overrides(base_budget, config, portfolio_regime):
    cfg = config or {}
    budget = dict(base_budget)
    regime = portfolio_regime or "normal_vol"
    prefix = regime_budget_prefix(regime)
    budget["portfolio_regime"] = regime
    budget["margin_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_margin_cap", base_budget["margin_cap"]),
        base_budget["margin_cap"],
    )
    budget["s1_margin_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_s1_margin_cap", base_budget["s1_margin_cap"]),
        base_budget["s1_margin_cap"],
    )
    budget["s3_margin_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_s3_margin_cap", base_budget["s3_margin_cap"]),
        base_budget["s3_margin_cap"],
    )
    budget["product_margin_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_product_margin_cap", base_budget["product_margin_cap"]),
        base_budget["product_margin_cap"],
    )
    budget["product_side_margin_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_product_side_margin_cap",
            base_budget["product_side_margin_cap"],
        ),
        base_budget["product_side_margin_cap"],
    )
    budget["bucket_margin_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_bucket_margin_cap", base_budget["bucket_margin_cap"]),
        base_budget["bucket_margin_cap"],
    )
    budget["corr_group_margin_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_corr_group_margin_cap",
            base_budget["corr_group_margin_cap"],
        ),
        base_budget["corr_group_margin_cap"],
    )
    budget["portfolio_stress_loss_cap"] = float_or_default(
        cfg.get(f"vol_regime_{prefix}_stress_loss_cap", base_budget["portfolio_stress_loss_cap"]),
        base_budget["portfolio_stress_loss_cap"],
    )
    budget["portfolio_bucket_stress_loss_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_bucket_stress_loss_cap",
            base_budget["portfolio_bucket_stress_loss_cap"],
        ),
        base_budget["portfolio_bucket_stress_loss_cap"],
    )
    budget["product_side_stress_loss_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_product_side_stress_loss_cap",
            base_budget["product_side_stress_loss_cap"],
        ),
        base_budget["product_side_stress_loss_cap"],
    )
    budget["corr_group_stress_loss_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_corr_group_stress_loss_cap",
            base_budget["corr_group_stress_loss_cap"],
        ),
        base_budget["corr_group_stress_loss_cap"],
    )
    budget["contract_stress_loss_cap"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_contract_stress_loss_cap",
            base_budget["contract_stress_loss_cap"],
        ),
        base_budget["contract_stress_loss_cap"],
    )
    budget["s1_stress_loss_budget_pct"] = float_or_default(
        cfg.get(
            f"vol_regime_{prefix}_s1_stress_loss_budget_pct",
            base_budget["s1_stress_loss_budget_pct"],
        ),
        base_budget["s1_stress_loss_budget_pct"],
    )
    return normalize_open_budget(budget)


def apply_open_budget_brakes(budget, config, drawdown=0.0, recent_stop_count=0):
    cfg = config or {}
    if not cfg.get("portfolio_budget_brake_enabled", True):
        return normalize_open_budget(budget)

    budget = dict(budget)
    drawdown = safe_pct(drawdown, 0.0, upper=None)
    recent_stop_count = int(safe_float(recent_stop_count, 0.0))
    scale = 1.0
    reasons = []

    if (
        budget.get("portfolio_regime") == "falling_vol_carry" and
        drawdown >= safe_float(cfg.get("portfolio_dd_pause_falling", 0.008), 0.0)
    ):
        for key in (
            "margin_cap",
            "s1_margin_cap",
            "s3_margin_cap",
            "product_margin_cap",
            "product_side_margin_cap",
            "bucket_margin_cap",
            "corr_group_margin_cap",
            "portfolio_stress_loss_cap",
            "portfolio_bucket_stress_loss_cap",
            "product_side_stress_loss_cap",
            "corr_group_stress_loss_cap",
            "contract_stress_loss_cap",
            "s1_stress_loss_budget_pct",
        ):
            normal_key = f"vol_regime_normal_{key}"
            if key == "portfolio_stress_loss_cap":
                normal_key = "vol_regime_normal_stress_loss_cap"
            elif key == "portfolio_bucket_stress_loss_cap":
                normal_key = "vol_regime_normal_bucket_stress_loss_cap"
            elif key == "s1_stress_loss_budget_pct":
                normal_key = "vol_regime_normal_s1_stress_loss_budget_pct"
            if normal_key in cfg:
                budget[key] = min(
                    safe_float(budget.get(key, cfg[normal_key]), 0.0),
                    safe_float(cfg.get(normal_key), 0.0),
                )
        budget["portfolio_regime"] = "falling_vol_carry_paused"
        reasons.append("dd_pause_falling")

    hard_dd = safe_float(cfg.get("portfolio_dd_reduce_limit", 0.012), 0.0)
    defensive_dd = safe_float(cfg.get("portfolio_dd_defensive_limit", 0.016), 0.0)
    if defensive_dd > 0 and drawdown >= defensive_dd:
        scale = min(scale, float_or_default(cfg.get("portfolio_dd_defensive_scale", 0.25), 0.25))
        reasons.append("dd_defensive")
    elif hard_dd > 0 and drawdown >= hard_dd:
        scale = min(scale, float_or_default(cfg.get("portfolio_dd_reduce_scale", 0.50), 0.50))
        reasons.append("dd_reduce")

    stop_threshold = int(safe_float(cfg.get("portfolio_stop_cluster_threshold", 3), 0.0))
    if stop_threshold > 0 and recent_stop_count >= stop_threshold:
        scale = min(scale, float_or_default(cfg.get("portfolio_stop_cluster_scale", 0.50), 0.50))
        reasons.append("stop_cluster")

    if scale < 1.0:
        for key in (
            "margin_cap",
            "s1_margin_cap",
            "s3_margin_cap",
            "product_margin_cap",
            "product_side_margin_cap",
            "bucket_margin_cap",
            "corr_group_margin_cap",
            "margin_per",
            "portfolio_stress_loss_cap",
            "portfolio_bucket_stress_loss_cap",
            "product_side_stress_loss_cap",
            "corr_group_stress_loss_cap",
            "contract_stress_loss_cap",
            "s1_stress_loss_budget_pct",
        ):
            if key in budget:
                budget[key] = safe_float(budget[key], 0.0) * scale

    budget["current_drawdown"] = drawdown
    budget["recent_stop_count"] = recent_stop_count
    budget["risk_scale"] = scale
    budget["brake_reason"] = ",".join(reasons)
    return normalize_open_budget(budget)


def get_effective_open_budget(config, portfolio_regime="normal_vol",
                              drawdown=0.0, recent_stop_count=0):
    base = build_base_open_budget(config)
    if not (config or {}).get("vol_regime_sizing_enabled", False):
        return apply_open_budget_brakes(
            base,
            config,
            drawdown=drawdown,
            recent_stop_count=recent_stop_count,
        )
    budget = apply_regime_overrides(base, config, portfolio_regime or "normal_vol")
    return apply_open_budget_brakes(
        budget,
        config,
        drawdown=drawdown,
        recent_stop_count=recent_stop_count,
    )


def pending_budget_fields(budget, strategy_cap):
    budget = budget or {}
    return {
        "effective_margin_cap": budget.get("margin_cap", NAN),
        "effective_strategy_margin_cap": strategy_cap,
        "effective_product_margin_cap": budget.get("product_margin_cap", NAN),
        "effective_product_side_margin_cap": budget.get("product_side_margin_cap", NAN),
        "effective_bucket_margin_cap": budget.get("bucket_margin_cap", NAN),
        "effective_corr_group_margin_cap": budget.get("corr_group_margin_cap", NAN),
        "effective_stress_loss_cap": budget.get("portfolio_stress_loss_cap", NAN),
        "effective_bucket_stress_loss_cap": budget.get("portfolio_bucket_stress_loss_cap", NAN),
        "effective_product_side_stress_loss_cap": budget.get("product_side_stress_loss_cap", NAN),
        "effective_corr_group_stress_loss_cap": budget.get("corr_group_stress_loss_cap", NAN),
        "effective_contract_stress_loss_cap": budget.get("contract_stress_loss_cap", NAN),
        "effective_s1_stress_budget_pct": budget.get("s1_stress_loss_budget_pct", NAN),
        "open_budget_risk_scale": budget.get("risk_scale", NAN),
        "open_budget_brake_reason": budget.get("brake_reason", ""),
    }


def execution_budget_for_item(item, current_budget, config):
    current = normalize_open_budget(current_budget or {})
    strat = str((item or {}).get("strat", "")).lower()
    strategy_key = f"{strat}_margin_cap" if strat else "s1_margin_cap"
    if strategy_key not in current:
        strategy_key = "s1_margin_cap"

    signal = dict(current)
    signal["margin_cap"] = safe_pct(
        item.get("effective_margin_cap", current["margin_cap"]),
        current["margin_cap"],
    )
    signal[strategy_key] = safe_pct(
        item.get("effective_strategy_margin_cap", current[strategy_key]),
        current[strategy_key],
    )
    signal["product_margin_cap"] = safe_pct(
        item.get("effective_product_margin_cap", current.get("product_margin_cap", 0.0)),
        current.get("product_margin_cap", 0.0),
    )
    signal["bucket_margin_cap"] = safe_pct(
        item.get("effective_bucket_margin_cap", current.get("bucket_margin_cap", 0.0)),
        current.get("bucket_margin_cap", 0.0),
    )
    signal["product_side_margin_cap"] = safe_pct(
        item.get("effective_product_side_margin_cap", current.get("product_side_margin_cap", 0.0)),
        current.get("product_side_margin_cap", 0.0),
    )
    signal["corr_group_margin_cap"] = safe_pct(
        item.get("effective_corr_group_margin_cap", current.get("corr_group_margin_cap", 0.0)),
        current.get("corr_group_margin_cap", 0.0),
    )
    signal["portfolio_stress_loss_cap"] = safe_pct(
        item.get("effective_stress_loss_cap", current.get("portfolio_stress_loss_cap", 0.0)),
        current.get("portfolio_stress_loss_cap", 0.0),
    )
    signal["portfolio_bucket_stress_loss_cap"] = safe_pct(
        item.get(
            "effective_bucket_stress_loss_cap",
            current.get("portfolio_bucket_stress_loss_cap", 0.0),
        ),
        current.get("portfolio_bucket_stress_loss_cap", 0.0),
    )
    signal["product_side_stress_loss_cap"] = safe_pct(
        item.get(
            "effective_product_side_stress_loss_cap",
            current.get("product_side_stress_loss_cap", 0.0),
        ),
        current.get("product_side_stress_loss_cap", 0.0),
    )
    signal["corr_group_stress_loss_cap"] = safe_pct(
        item.get(
            "effective_corr_group_stress_loss_cap",
            current.get("corr_group_stress_loss_cap", 0.0),
        ),
        current.get("corr_group_stress_loss_cap", 0.0),
    )
    signal["contract_stress_loss_cap"] = safe_pct(
        item.get(
            "effective_contract_stress_loss_cap",
            current.get("contract_stress_loss_cap", 0.0),
        ),
        current.get("contract_stress_loss_cap", 0.0),
    )
    signal["s1_stress_loss_budget_pct"] = safe_pct(
        item.get("effective_s1_stress_budget_pct", current.get("s1_stress_loss_budget_pct", 0.0)),
        current.get("s1_stress_loss_budget_pct", 0.0),
    )
    signal = normalize_open_budget(signal)

    policy = str((config or {}).get("portfolio_execution_budget_policy", "min_signal_current") or "").lower()
    if policy == "signal":
        return signal
    if policy == "current":
        return current

    budget = dict(current)
    for key in (
        "margin_cap",
        strategy_key,
        "product_margin_cap",
        "product_side_margin_cap",
        "bucket_margin_cap",
        "corr_group_margin_cap",
        "portfolio_stress_loss_cap",
        "portfolio_bucket_stress_loss_cap",
        "product_side_stress_loss_cap",
        "corr_group_stress_loss_cap",
        "contract_stress_loss_cap",
        "s1_stress_loss_budget_pct",
    ):
        budget[key] = min(
            safe_pct(current.get(key, 0.0), 0.0),
            safe_pct(signal.get(key, current.get(key, 0.0)), current.get(key, 0.0)),
        )
    if (config or {}).get("portfolio_execution_allow_signal_product_overrides", False):
        for key in (
            "product_margin_cap",
            "product_side_margin_cap",
            "bucket_margin_cap",
            "corr_group_margin_cap",
            "portfolio_bucket_stress_loss_cap",
            "product_side_stress_loss_cap",
            "corr_group_stress_loss_cap",
            "contract_stress_loss_cap",
        ):
            budget[key] = max(
                safe_pct(budget.get(key, 0.0), 0.0),
                safe_pct(signal.get(key, 0.0), 0.0),
            )
    return normalize_open_budget(budget)
