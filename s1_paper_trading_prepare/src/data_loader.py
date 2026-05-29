"""Data snapshot helpers for the S1 paper-trading pipeline."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

import pandas as pd

from .paths import DEFAULT_PAPER_CONFIG, ensure_server_deploy_importable, resolve_path


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    size = max(int(size or 1), 1)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def load_signal_day_snapshot(
    signal_date: str,
    config_path: str | Path | None = None,
    *,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 8,
) -> pd.DataFrame:
    """Load the same daily aggregate snapshot used by the ToolkitMinuteEngine."""
    ensure_server_deploy_importable()
    from contract_provider import ContractInfo
    from day_loader import ToolkitDayLoader
    from query_filters import build_product_like_sql
    from strategy_rules import DEFAULT_PARAMS
    from config_loader import load_engine_config

    path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
    config = load_engine_config(str(path), DEFAULT_PARAMS)
    ci = ContractInfo()
    ci.load()
    product_pool = products or config.get("product_pool") or config.get("products") or ci.get_all_products()
    if isinstance(product_pool, str):
        product_pool = [p.strip() for p in product_pool.split(",") if p.strip()]
    product_list = sorted({str(p).upper().strip() for p in product_pool if str(p).strip()})
    date = str(signal_date)[:10]
    parts = []
    for chunk in _chunks(product_list, product_chunk_size):
        like_sql = build_product_like_sql(chunk, ci._cache, ci.get_product_codes)
        loader = ToolkitDayLoader(ci)
        loader.preload_daily_agg_batch([date], like_sql, ci)
        part = loader.get_daily_agg(date, ci)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True, sort=False)
    key_cols = [col for col in ("option_code", "ths_code") if col in out.columns]
    if key_cols:
        out = out.drop_duplicates(subset=key_cols, keep="last")
    return out.reset_index(drop=True)


def load_trading_dates(start_date: str, end_date: str) -> list[str]:
    """Load Toolkit trading dates for incremental refresh windows."""
    ensure_server_deploy_importable()
    from contract_provider import ContractInfo
    from day_loader import ToolkitDayLoader

    loader = ToolkitDayLoader(ContractInfo())
    return [str(date)[:10] for date in loader.get_trading_dates(str(start_date)[:10], str(end_date)[:10])]
