"""Config loading and audit helpers for the locked S1 mainline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .paths import DEFAULT_PAPER_CONFIG, ensure_server_deploy_importable


RULE_KEYS = (
    "strategy_version",
    "capital",
    "s1_l1_pre_candidate_enabled",
    "s1_l1_pre_candidate_missing_policy",
    "s1_l1_product_side_panel_path",
    "s1_l1_primary_bucket_col",
    "s1_l1_secondary_bucket_col",
    "s1_l1_min_primary_bucket",
    "s1_l1_min_secondary_bucket",
    "s1_l2_sort_weights",
    "s1_l1_sort_weights",
    "s1_l1_budget_multipliers",
    "s1_l3_product_side_ledger_enabled",
    "s1_l3_ledger_base_scope",
    "s1_l3_ledger_cap_total_budget",
    "s1_l3_refill_min_bucket",
    "s1_min_volume",
    "s1_min_oi",
    "s1_sell_delta_floor",
    "s1_sell_delta_cap",
    "s1_target_abs_delta",
    "s1_expiry_mode",
    "s1_expiry_rank",
    "s1_b15b2_entry_enabled",
    "s1_b15b2_min_iv_pct",
    "s1_variance_carry_filter_enabled",
    "s1_min_variance_carry",
    "s1_b6_contract_rank_enabled",
    "s1_b6_weight_premium_to_stress",
    "s1_b6_weight_premium_to_iv10",
    "s1_b6_weight_theta_per_vega",
    "s1_b6_weight_theta_per_gamma",
    "s1_b6_weight_tail_move_coverage",
    "s1_b6_weight_vomma",
    "s1_b6_weight_premium_yield_margin",
    "s1_b6_weight_delta_band",
    "portfolio_premium_budget_enabled",
    "portfolio_entry_premium_cap",
    "portfolio_product_entry_premium_cap",
    "portfolio_product_side_entry_premium_cap",
    "portfolio_bucket_entry_premium_cap",
    "portfolio_corr_group_entry_premium_cap",
    "margin_cap",
    "s1_margin_cap",
    "open_execution_volume_limit_enabled",
    "s1_entry_volume_target_cap_enabled",
    "volume_limit_pct",
    "s1_entry_volume_limit_ratio",
    "premium_stop_multiple",
    "intraday_stop_execution_price_mode",
    "intraday_stop_vwap_participation_rate",
    "s1_deferred_intent_enabled",
    "s1_deferred_intent_scope",
    "s1_holiday_risk_enabled",
    "s1_holiday_t1_block_all_new_opens",
)


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    config: dict[str, Any]
    sha256: str


SIDE_STRATEGY_SUFFIXES = tuple(str(index) for index in range(2, 5))


def apply_primary_strategy_only(config: dict[str, Any]) -> dict[str, Any]:
    """Force the paper runtime to run only the S1 strategy family."""
    out = dict(config)
    out["enable_s1"] = True
    for suffix in SIDE_STRATEGY_SUFFIXES:
        out[f"enable_s{suffix}"] = False
        out[f"s{suffix}_margin_cap"] = 0.0
    out["enabled_strategy_families"] = ["S1"]
    return out


def load_effective_config(config_path: str | Path | None = None) -> ConfigSnapshot:
    """Load the engine config exactly as the backtest engine loads it."""
    ensure_server_deploy_importable()
    from config_loader import load_engine_config
    from strategy_rules import DEFAULT_PARAMS

    path = Path(config_path or DEFAULT_PAPER_CONFIG).resolve()
    config = apply_primary_strategy_only(load_engine_config(str(path), DEFAULT_PARAMS))
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ConfigSnapshot(path=path, config=config, sha256=digest)


def important_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the compact rule table used in audit metadata."""
    return [{"key": key, "value": config.get(key)} for key in RULE_KEYS]


def write_config_audit(path: str | Path, snapshot: ConfigSnapshot) -> None:
    """Write an audit JSON for the effective mainline config."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "important_rules": important_rules(snapshot.config),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
