"""Config loading and audit helpers for the current S1 paper line."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .paths import DEFAULT_PAPER_CONFIG, resolve_path


RULE_KEYS = (
    "strategy_version",
    "_order_generation_mode",
    "capital",
    "external_signal_path",
    "external_signal_date_column",
    "external_signal_default_execution_lag",
    "open_execution_price_mode",
    "open_execution_volume_limit_enabled",
    "volume_limit_pct",
    "external_open_keep_pending_without_price",
    "external_exit_require_fresh_mark",
    "external_exit_accept_open_execution_mark",
    "external_mark_missing_diagnostics_enabled",
    "external_pending_max_carry_trading_days",
    "external_open_farther_contract_reroute_enabled",
    "external_open_farther_contract_max_contracts",
    "external_open_farther_contract_max_qty_pct",
    "main_pre_expiry_itm_exit",
    "margin_cap",
    "s1_margin_cap",
    "main_sleeve",
    "iv_pullback_sidecar",
    "risk_reversal_sidecar",
    "term_structure_sidecar",
    "sidecar_risk_controls",
    "broad_sector_groups",
    "future_function_guards",
)


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    config: dict[str, Any]
    sha256: str


def load_effective_config(config_path: str | Path | None = None) -> ConfigSnapshot:
    """Load the narrow JSON config used by the current external-intent line."""
    path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
    config = json.loads(path.read_text(encoding="utf-8"))
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return ConfigSnapshot(path=path, config=config, sha256=digest)


def important_rules(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a compact rule table for order and replay audit metadata."""
    return [{"key": key, "value": config.get(key)} for key in RULE_KEYS]


def write_config_audit(path: str | Path, snapshot: ConfigSnapshot) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "important_rules": important_rules(snapshot.config),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
