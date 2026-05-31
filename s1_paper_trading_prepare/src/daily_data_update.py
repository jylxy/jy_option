"""Daily data refresh manifest for the current S1 paper line."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .config_snapshot import load_effective_config
from .data_loader import load_signal_day_snapshot, load_trading_dates
from .diagnostics import write_csv, write_json
from .paths import DEFAULT_DATA_DIR, DEFAULT_PAPER_CONFIG, resolve_path
from .table_registry import current_registry_status


@dataclass(frozen=True)
class DailyDataUpdateResult:
    signal_date: str
    dates: list[str]
    snapshot_path: Path | None
    manifest_path: Path | None
    snapshot_rows: int
    fetched_dates: list[str]
    reused_dates: list[str]
    empty_dates: list[str]
    table_status: list[dict[str, Any]]
    meta: dict[str, Any]


def _date_tag(date: str) -> str:
    return str(date)[:10].replace("-", "")


def _snapshot_path(data_dir: Path, date: str) -> Path:
    return data_dir / "daily_snapshots" / f"option_chain_{_date_tag(date)}.csv"


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _resolve_update_dates(signal_date: str | None, start_date: str | None, end_date: str | None) -> list[str]:
    if start_date or end_date:
        start = str(start_date or signal_date or end_date)[:10]
        end = str(end_date or signal_date or start_date)[:10]
        try:
            return load_trading_dates(start, end)
        except Exception:
            return [date.strftime("%Y-%m-%d") for date in pd.date_range(start=start, end=end, freq="B")]
    if not signal_date:
        raise ValueError("signal_date is required when start_date/end_date are not provided")
    return [str(signal_date)[:10]]


def update_daily_data(
    signal_date: str | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 32,
    force: bool = False,
    write_outputs: bool = True,
) -> DailyDataUpdateResult:
    """Fetch missing Toolkit daily snapshots and write the current-line manifest.

    The approved line consumes daily external S1 intents. Full product-side and
    overlay tables are tracked in the registry; this function keeps the daily
    option snapshot fresh and records which downstream tables must be refreshed
    by the S1 signal builder.
    """
    config_target = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
    data_target = resolve_path(data_dir, default=DEFAULT_DATA_DIR)
    snapshot = load_effective_config(config_target)
    dates = _resolve_update_dates(signal_date, start_date, end_date)
    fetched: list[str] = []
    reused: list[str] = []
    empty: list[str] = []
    last_path: Path | None = None
    last_rows = 0

    for date in dates:
        path = _snapshot_path(data_target, date)
        last_path = path
        if path.exists() and not force and _count_csv_rows(path) > 0:
            reused.append(date)
            last_rows = _count_csv_rows(path)
            continue
        frame = load_signal_day_snapshot(
            date,
            config_target,
            products=products,
            product_chunk_size=product_chunk_size,
        )
        if frame.empty:
            empty.append(date)
            last_rows = 0
            continue
        if write_outputs:
            write_csv(path, frame)
        fetched.append(date)
        last_rows = int(len(frame))

    status = current_registry_status(snapshot.config)
    manifest_path = data_target / "manifests" / f"daily_data_update_{_date_tag(dates[-1])}.json"
    meta = {
        "signal_date": dates[-1],
        "dates": dates,
        "config_path": str(config_target),
        "config_sha256": snapshot.sha256,
        "products": list(products) if products else None,
        "fetched_dates": fetched,
        "reused_dates": reused,
        "empty_dates": empty,
        "table_status": status,
        "notes": [
            "This refresh is scoped to the approved S1 four-layer external-intent line.",
            "Daily signal tables must be point-in-time and then update external_signal_path before order generation.",
        ],
    }
    if write_outputs:
        write_json(manifest_path, meta)
    return DailyDataUpdateResult(
        signal_date=dates[-1],
        dates=dates,
        snapshot_path=last_path,
        manifest_path=manifest_path if write_outputs else None,
        snapshot_rows=last_rows,
        fetched_dates=fetched,
        reused_dates=reused,
        empty_dates=empty,
        table_status=status,
        meta=meta,
    )
