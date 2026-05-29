"""Public order-generation API."""

from __future__ import annotations

from pathlib import Path

from .engine_adapter import PaperTradingOrderGenerator, PaperTradingRunResult
from .schemas import PaperTradingRunRequest


def generate_orders(
    signal_date: str,
    *,
    replay_start_date: str | None = None,
    products: tuple[str, ...] | None = None,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    tag: str | None = None,
) -> PaperTradingRunResult:
    generator = PaperTradingOrderGenerator(config_path=config_path, output_dir=output_dir)
    request = PaperTradingRunRequest(
        signal_date=signal_date,
        replay_start_date=replay_start_date,
        products=products,
        config_path=generator.config_path,
        output_dir=generator.output_dir,
        tag=tag,
        write_outputs=True,
    )
    return generator.generate(request)

