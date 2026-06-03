"""Schemas and stable column lists for current S1 paper outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG


@dataclass(frozen=True)
class PaperTradingRunRequest:
    signal_date: str
    replay_start_date: str | None = None
    products: tuple[str, ...] | None = None
    config_path: Path = DEFAULT_PAPER_CONFIG
    output_dir: Path = DEFAULT_OUTPUT_DIR
    state_dir: Path | None = None
    tag: str | None = None
    write_outputs: bool = True


ORDER_FRONT_COLUMNS = [
    "signal_date",
    "execute_date",
    "order_status",
    "action",
    "strategy",
    "entry_reason",
    "strategy_layer",
    "product",
    "exchange",
    "code",
    "source_contract_code",
    "option_type",
    "strike",
    "expiry",
    "dte",
    "quantity",
    "signal_ref_price",
    "gross_premium_cash",
    "net_premium_cash",
    "target_premium_cash",
    "target_premium_pct",
    "margin",
    "one_contract_margin",
    "budget_group",
    "side_rule",
    "forced_sell_side",
    "selected_side_iv_pressure",
    "other_side_iv_pressure",
    "side_iv_pressure_diff",
    "overlay_strategy",
    "overlay_signal_family",
    "overlay_signal_rule",
    "overlay_side_rule",
    "overlay_priority",
    "l1_rule",
    "l2_rule",
    "l3_rule",
    "l4_rule",
]
