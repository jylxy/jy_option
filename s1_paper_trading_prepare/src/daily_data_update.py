"""Daily toolkit-backed data refresh for the S1 paper-trading workspace."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config_snapshot import important_rules, load_effective_config
from .data_loader import load_signal_day_snapshot, load_trading_dates
from .diagnostics import write_csv, write_json
from .paths import DEFAULT_DATA_DIR, DEFAULT_PAPER_CONFIG, resolve_path
from .product_side_panel import audit_l1_admission_from_panel, load_panel
from .product_side_rolling import RollingProductSideUpdateResult, update_rolling_product_side_panel
from .table_registry import current_registry_status


@dataclass(frozen=True)
class DailyPartitionResult:
    signal_date: str
    action: str
    snapshot_path: Path | None
    l0_universe_path: Path | None
    l1_admission_path: Path | None
    rolling_l1_admission_path: Path | None
    rolling_panel_path: Path | None
    manifest_path: Path | None
    snapshot_rows: int
    l0_universe_rows: int
    l1_admission_rows: int
    rolling_l1_admission_rows: int
    rolling_panel_rows: int
    rolling_matured_rows: int


@dataclass(frozen=True)
class DailyDataUpdateResult:
    signal_date: str
    dates: list[str]
    snapshot_path: Path | None
    l0_universe_path: Path | None
    l1_admission_path: Path | None
    rolling_l1_admission_path: Path | None
    rolling_panel_path: Path | None
    manifest_path: Path | None
    snapshot_rows: int
    l0_universe_rows: int
    l1_admission_rows: int
    rolling_l1_admission_rows: int
    rolling_panel_rows: int
    rolling_matured_rows: int
    fetched_dates: list[str]
    reused_dates: list[str]
    partitions: list[DailyPartitionResult]
    meta: dict[str, Any]


def _date_tag(signal_date: str, products: tuple[str, ...] | None = None) -> str:
    tag = str(signal_date)[:10].replace("-", "")
    if products:
        product_tag = "_".join(str(product).upper() for product in products)
        return f"{tag}_{product_tag}"
    return tag


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        rows = sum(1 for _ in handle)
    return max(rows - 1, 0)


def _load_existing_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or _count_csv_rows(path) <= 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def build_l0_contract_universe(option_snapshot: pd.DataFrame, config: dict[str, Any], signal_date: str) -> pd.DataFrame:
    """Build the daily S1 L0 contract-eligibility table from stored snapshot data."""
    front_cols = [
        "signal_date",
        "product",
        "option_code",
        "option_type",
        "strike",
        "expiry_date",
        "dte",
        "option_close",
        "volume",
        "open_interest",
        "delta",
        "abs_delta",
        "implied_vol",
        "l0_price_ok",
        "l0_volume_ok",
        "l0_oi_ok",
        "l0_dte_ok",
        "l0_delta_ok",
        "l0_basic_trade_eligible",
    ]
    if option_snapshot.empty:
        return pd.DataFrame(columns=front_cols)

    work = option_snapshot.copy()
    work["signal_date"] = str(signal_date)[:10]
    if "abs_delta" not in work.columns:
        delta_source = work["delta"] if "delta" in work.columns else pd.Series(index=work.index, dtype=float)
        work["abs_delta"] = pd.to_numeric(delta_source, errors="coerce").abs()

    def numeric_col(name: str) -> pd.Series:
        source = work[name] if name in work.columns else pd.Series(index=work.index, dtype=float)
        return pd.to_numeric(source, errors="coerce")

    price = numeric_col("option_close")
    volume = numeric_col("volume").fillna(0)
    oi = numeric_col("open_interest").fillna(0)
    dte = numeric_col("dte")
    abs_delta = numeric_col("abs_delta")

    min_price = float(config.get("s1_min_option_price", 0.0) or 0.0)
    min_volume = float(config.get("s1_min_volume", 0.0) or 0.0)
    min_oi = float(config.get("s1_min_oi", 0.0) or 0.0)
    dte_min = float(config.get("dte_min", 0.0) or 0.0)
    dte_max = float(config.get("dte_max", 9999.0) or 9999.0)
    delta_floor = float(config.get("s1_sell_delta_floor", 0.0) or 0.0)
    delta_cap = float(config.get("s1_sell_delta_cap", 1.0) or 1.0)

    work["l0_price_ok"] = price.ge(min_price)
    work["l0_volume_ok"] = volume.ge(min_volume)
    work["l0_oi_ok"] = oi.ge(min_oi)
    work["l0_dte_ok"] = dte.between(dte_min, dte_max, inclusive="both")
    work["l0_delta_ok"] = abs_delta.between(delta_floor, delta_cap, inclusive="both")
    work["l0_basic_trade_eligible"] = (
        work["l0_price_ok"]
        & work["l0_volume_ok"]
        & work["l0_oi_ok"]
        & work["l0_dte_ok"]
        & work["l0_delta_ok"]
    )

    ordered = [col for col in front_cols if col in work.columns]
    rest = [col for col in work.columns if col not in ordered]
    return work.loc[:, ordered + rest].copy()


def _resolve_update_dates(
    signal_date: str | None,
    start_date: str | None,
    end_date: str | None,
    data_dir: Path,
) -> list[str]:
    if start_date or end_date:
        start = str(start_date or signal_date or end_date)[:10]
        end = str(end_date or signal_date or start_date)[:10]
        try:
            return load_trading_dates(start, end)
        except Exception:
            stored = [
                date for date in _available_stored_dates(data_dir)
                if start <= date <= end
            ]
            if stored:
                return stored
            return [
                date.strftime("%Y-%m-%d")
                for date in pd.date_range(start=start, end=end, freq="B")
            ]
    if not signal_date:
        raise ValueError("signal_date is required when start_date/end_date are not provided")
    return [str(signal_date)[:10]]


def _available_stored_dates(data_dir: Path) -> list[str]:
    dates = []
    for path in sorted((data_dir / "daily_snapshots").glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if len(tag) == 8 and tag.isdigit():
            dates.append(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}")
    return dates


def _write_store_index(index_path: Path, partitions: list[DailyPartitionResult], meta: dict[str, Any]) -> None:
    existing: dict[str, Any] = {}
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    dates = dict(existing.get("dates", {}))
    for part in partitions:
        dates[part.signal_date] = {
            "last_action": part.action,
            "snapshot_path": str(part.snapshot_path) if part.snapshot_path else None,
            "l0_universe_path": str(part.l0_universe_path) if part.l0_universe_path else None,
            "l1_admission_path": str(part.l1_admission_path) if part.l1_admission_path else None,
            "rolling_l1_admission_path": (
                str(part.rolling_l1_admission_path) if part.rolling_l1_admission_path else None
            ),
            "rolling_panel_path": str(part.rolling_panel_path) if part.rolling_panel_path else None,
            "manifest_path": str(part.manifest_path) if part.manifest_path else None,
            "snapshot_rows": part.snapshot_rows,
            "l0_universe_rows": part.l0_universe_rows,
            "l1_admission_rows": part.l1_admission_rows,
            "rolling_l1_admission_rows": part.rolling_l1_admission_rows,
            "rolling_panel_rows": part.rolling_panel_rows,
            "rolling_matured_rows": part.rolling_matured_rows,
        }
    write_json(
        index_path,
        {
            "strategy_version": meta.get("strategy_version"),
            "paper_strategy_version": meta.get("paper_strategy_version"),
            "config_sha256": meta.get("config_sha256"),
            "dates": dict(sorted(dates.items())),
        },
    )


def update_daily_data(
    signal_date: str | None = None,
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
    """Fetch and materialize the daily S1 input tables used by order generation.

    The option-chain snapshot is fetched from Toolkit.  The current L1
    product-side table is materialized from the locked mainline panel snapshot;
    the manifest makes that source explicit so the next extraction step can
    replace it with a live rolling-history updater without changing consumers.
    """
    config_target = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
    out_dir = resolve_path(data_dir, default=DEFAULT_DATA_DIR)
    snapshot = load_effective_config(config_target)
    dates = _resolve_update_dates(signal_date, start_date, end_date, out_dir)
    locked_l1_panel = load_panel(snapshot.config)
    rolling_snapshot_cache: dict[str, pd.DataFrame] = {}

    base_meta = {
        "signal_date": dates[-1] if dates else None,
        "dates": dates,
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "strategy_version": snapshot.config.get("strategy_version"),
        "paper_strategy_version": snapshot.config.get("_paper_strategy_version"),
        "products": list(products) if products else None,
        "product_chunk_size": int(product_chunk_size or 32),
        "force": bool(force),
        "toolkit_sources": [
            "Toolkit option minute table via ToolkitDayLoader daily aggregation",
            "Toolkit futures/ETF spot tables via ToolkitDayLoader enrichment",
            "ContractInfo contract metadata",
            "Toolkit trading calendar",
        ],
        "required_s1_runtime_tables": [
            "daily option-chain snapshot",
            "contract metadata and multipliers",
            "spot/underlying daily features",
            "IV/Greeks and VRP-derived candidate fields",
            "L1 product-side rolling historical buckets",
            "fees, margin ratios, sector/bucket/corr-group taxonomy",
            "current NAV, positions, pending/unfilled orders",
        ],
        "important_rules": important_rules(snapshot.config),
        "s1_table_dependency_status": current_registry_status(snapshot.config),
    }

    partitions: list[DailyPartitionResult] = []
    fetched_dates: list[str] = []
    reused_dates: list[str] = []
    last_manifest: dict[str, Any] | None = None

    for date in dates:
        tag = _date_tag(date, products)
        snapshot_path = out_dir / "daily_snapshots" / f"option_chain_{tag}.csv"
        l0_path = out_dir / "derived" / f"s1_l0_contract_universe_{tag}.csv"
        l1_path = out_dir / "product_side_panel" / f"l1_admission_{tag}.csv"
        manifest_path = out_dir / "manifests" / f"daily_data_update_{tag}.json"

        if snapshot_path.exists() and not force:
            action = "reuse_existing_snapshot"
            option_snapshot = _load_existing_csv(snapshot_path)
            reused_dates.append(date)
        else:
            action = "fetch_from_toolkit"
            option_snapshot = load_signal_day_snapshot(
                date,
                snapshot.path,
                products=products,
                product_chunk_size=product_chunk_size,
            )
            fetched_dates.append(date)
            if write_outputs:
                write_csv(snapshot_path, option_snapshot)

        l0_universe = build_l0_contract_universe(option_snapshot, snapshot.config, date)
        l1_admission = audit_l1_admission_from_panel(date, snapshot.config, locked_l1_panel)
        rolling_result = update_rolling_product_side_panel(
            date,
            l0_universe=l0_universe,
            config=snapshot.config,
            data_dir=out_dir,
            write_outputs=write_outputs,
            snapshot_cache=rolling_snapshot_cache,
        )
        manifest = {
            **base_meta,
            "signal_date": date,
            "incremental_action": action,
            "materialized_tables": [
                {
                    "name": "option_chain_daily_snapshot",
                    "path": str(snapshot_path) if write_outputs else None,
                    "source": "toolkit",
                    "rows": int(len(option_snapshot)),
                    "update_policy": "date partition; reused unless missing or --force is passed",
                },
                {
                    "name": "s1_l0_contract_universe",
                    "path": str(l0_path) if write_outputs else None,
                    "source": "computed from option_chain_daily_snapshot",
                    "rows": int(len(l0_universe)),
                },
                {
                    "name": "s1_l1_product_side_admission",
                    "path": str(l1_path) if write_outputs else None,
                    "source": snapshot.config.get("s1_l1_product_side_panel_path"),
                    "rows": int(len(l1_admission)),
                    "live_update_note": (
                        "This is the locked mainline panel materialization.  In "
                        "live paper trading it must be replaced by the same "
                        "rolling historical feature updater, using Toolkit data "
                        "only and no forward labels."
                    ),
                },
                {
                    "name": "s1_rolling_contract_shadow_observations",
                    "path": str(rolling_result.contract_observation_path)
                    if rolling_result.contract_observation_path else None,
                    "source": "computed from stored S1 daily snapshots",
                    "rows": int(rolling_result.contract_observation_rows),
                },
                {
                    "name": "s1_rolling_contract_shadow_fields",
                    "path": str(rolling_result.contract_fields_path)
                    if rolling_result.contract_fields_path else None,
                    "source": "full-shadow V3/B6/VRP/regime fields recomputed from Toolkit snapshots",
                    "rows": int(rolling_result.contract_fields_rows),
                },
                {
                    "name": "s1_rolling_product_side_panel",
                    "path": str(rolling_result.panel_path) if rolling_result.panel_path else None,
                    "source": "computed from stored S1 daily snapshots",
                    "rows": int(rolling_result.panel_rows),
                },
                {
                    "name": "s1_rolling_l1_admission",
                    "path": str(rolling_result.admission_path) if rolling_result.admission_path else None,
                    "source": "computed from s1_rolling_product_side_panel",
                    "rows": int(rolling_result.admission_rows),
                    "matured_observation_rows": int(rolling_result.matured_observation_rows),
                },
            ],
        }

        if write_outputs:
            write_csv(l0_path, l0_universe)
            write_csv(l1_path, l1_admission)
            write_json(manifest_path, manifest)
        else:
            snapshot_path = l0_path = l1_path = manifest_path = None

        partitions.append(
            DailyPartitionResult(
                signal_date=date,
                action=action,
                snapshot_path=snapshot_path,
                l0_universe_path=l0_path,
                l1_admission_path=l1_path,
                rolling_l1_admission_path=rolling_result.admission_path,
                rolling_panel_path=rolling_result.panel_path,
                manifest_path=manifest_path,
                snapshot_rows=int(len(option_snapshot)),
                l0_universe_rows=int(len(l0_universe)),
                l1_admission_rows=int(len(l1_admission)),
                rolling_l1_admission_rows=int(rolling_result.admission_rows),
                rolling_panel_rows=int(rolling_result.panel_rows),
                rolling_matured_rows=int(rolling_result.matured_observation_rows),
            )
        )
        last_manifest = manifest

    if write_outputs:
        _write_store_index(out_dir / "manifests" / "data_store_index.json", partitions, base_meta)

    last = partitions[-1] if partitions else DailyPartitionResult(
        signal_date=str(signal_date or ""),
        action="none",
        snapshot_path=None,
        l0_universe_path=None,
        l1_admission_path=None,
        rolling_l1_admission_path=None,
        rolling_panel_path=None,
        manifest_path=None,
        snapshot_rows=0,
        l0_universe_rows=0,
        l1_admission_rows=0,
        rolling_l1_admission_rows=0,
        rolling_panel_rows=0,
        rolling_matured_rows=0,
    )
    meta = {
        **(last_manifest or base_meta),
        "fetched_dates": fetched_dates,
        "reused_dates": reused_dates,
        "partition_count": len(partitions),
    }

    return DailyDataUpdateResult(
        signal_date=last.signal_date,
        dates=dates,
        snapshot_path=last.snapshot_path,
        l0_universe_path=last.l0_universe_path,
        l1_admission_path=last.l1_admission_path,
        rolling_l1_admission_path=last.rolling_l1_admission_path,
        rolling_panel_path=last.rolling_panel_path,
        manifest_path=last.manifest_path,
        snapshot_rows=last.snapshot_rows,
        l0_universe_rows=last.l0_universe_rows,
        l1_admission_rows=last.l1_admission_rows,
        rolling_l1_admission_rows=last.rolling_l1_admission_rows,
        rolling_panel_rows=last.rolling_panel_rows,
        rolling_matured_rows=last.rolling_matured_rows,
        fetched_dates=fetched_dates,
        reused_dates=reused_dates,
        partitions=partitions,
        meta=meta,
    )
