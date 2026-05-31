"""Registry for S1 paper-trading table dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import PROJECT_DIR


REGISTRY_PATH = PROJECT_DIR / "configs" / "s1_table_registry.json"


def _config_get(config: dict[str, Any], key: str | None, default: Any = None) -> Any:
    if not key:
        return default
    current: Any = config
    for part in str(key).split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def load_table_registry(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path or REGISTRY_PATH)
    return json.loads(target.read_text(encoding="utf-8"))


def current_registry_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return active/inactive table-dependency status under the effective config."""
    registry = load_table_registry()
    rows: list[dict[str, Any]] = []

    for table in registry.get("tables", []):
        gate_key = table.get("config_gate_key")
        gate_expected = table.get("config_gate_expected")
        path_key = table.get("config_path_key")
        if gate_key:
            gate_value = _config_get(config, gate_key)
            active = gate_value == gate_expected if "config_gate_expected" in table else bool(gate_value)
        else:
            gate_value = None
            active = table.get("status") in {"active_mainline_input", "paper_required"}

        rows.append(
            {
                "name": table.get("name"),
                "status": table.get("status"),
                "active_under_effective_config": bool(active),
                "config_gate_key": gate_key,
                "config_gate_value": gate_value,
                "config_path_key": path_key,
                "configured_path": _config_get(config, path_key) if path_key else table.get("paper_path"),
                "paper_action": table.get("paper_action"),
            }
        )
    return rows
