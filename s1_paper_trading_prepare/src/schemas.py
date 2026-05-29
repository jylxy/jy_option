"""Schemas and stable column lists for S1 paper-trading outputs."""

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
    tag: str | None = None
    write_outputs: bool = True


ORDER_FRONT_COLUMNS = [
    "signal_date",
    "execute_date",
    "order_status",
    "action",
    "strategy",
    "product",
    "code",
    "option_type",
    "strike",
    "expiry",
    "quantity",
    "signal_ref_price",
    "gross_premium_cash",
    "net_premium_cash",
    "margin",
    "one_contract_margin",
    "stress_loss",
    "selection_score",
    "l1_hist_bucket",
    "l1_tail_bucket",
    "l1_sort_bucket",
    "l1_budget_mult",
    "l3_ledger_enabled",
    "l3_ledger_base_side_budget_pct",
    "l3_ledger_total_final_budget_pct",
    "side_budget_mult",
    "effective_strategy_margin_cap",
]

