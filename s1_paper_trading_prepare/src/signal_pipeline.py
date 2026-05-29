"""Named stages for the current S1 paper-trading signal pipeline."""

from __future__ import annotations

from pathlib import Path

from .config_snapshot import load_effective_config
from .daily_data_update import update_daily_data
from .order_generator import generate_orders
from .product_side_panel import audit_l1_admission


def run_signal_pipeline(
    signal_date: str,
    *,
    replay_start_date: str | None = None,
    products: tuple[str, ...] | None = None,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    tag: str | None = None,
):
    """Run config audit, L1 panel audit, and exact-engine order generation."""
    snapshot = load_effective_config(config_path)
    data_update = update_daily_data(signal_date, config_path=snapshot.path)
    l1_admission = audit_l1_admission(signal_date, snapshot.path)
    result = generate_orders(
        signal_date,
        replay_start_date=replay_start_date,
        products=products,
        config_path=snapshot.path,
        output_dir=output_dir,
        tag=tag,
    )
    return {
        "config_snapshot": snapshot,
        "data_update": data_update,
        "l1_admission": l1_admission,
        "orders_result": result,
    }
