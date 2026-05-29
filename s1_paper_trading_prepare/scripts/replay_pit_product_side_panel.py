from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.config_snapshot import load_effective_config
from s1_paper_trading_prepare.src.data_loader import load_trading_dates
from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_PAPER_CONFIG, resolve_path
from s1_paper_trading_prepare.src.product_side_rolling import (
    _add_full_shadow_contract_fields,
    _add_hist_and_scores,
    _available_snapshot_dates,
    _bootstrap_contract_shadow_observations,
    _build_product_spot_map_from_snapshots,
    _date_tag,
    _mature_observation_labels,
    _product_spot_map_path,
    build_product_side_observations_from_contract_fields,
    build_rolling_l1_admission,
)


DEFAULT_OUTPUT = "product_side_panel/rolling_product_side_panel_pit_asof.csv"


def _empty_like(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.iloc[0:0].copy()


def _date_mask(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.Series:
    dates = frame["date"].astype(str).str[:10]
    mask = pd.Series(True, index=frame.index)
    if start:
        mask &= dates.ge(str(start)[:10])
    if end:
        mask &= dates.le(str(end)[:10])
    return mask


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay S1 product-side scoring as a point-in-time panel. This freezes "
            "each signal date's current-date rows after capping label maturity to "
            "snapshots visible on that date."
        )
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--prehistory-start-date", default=None)
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output", default=None)
    parser.add_argument("--hist-window", type=int, default=63)
    parser.add_argument("--outcome-horizon", type=int, default=10)
    parser.add_argument("--write-daily-admissions", action="store_true")
    parser.add_argument("--write-intermediate", action="store_true")
    args = parser.parse_args()

    started = time.time()
    config_path = resolve_path(args.config)
    data_dir = resolve_path(args.data_dir)
    output = resolve_path(args.output) if args.output else data_dir / DEFAULT_OUTPUT
    snapshot = load_effective_config(config_path)
    config = snapshot.config

    signal_dates = load_trading_dates(args.start_date, args.end_date)
    if not signal_dates:
        raise SystemExit("no signal dates resolved")
    warmup_start = str(args.prehistory_start_date or args.start_date)[:10]
    all_snapshot_dates = [
        date for date in _available_snapshot_dates(data_dir)
        if warmup_start <= date <= str(args.end_date)[:10]
    ]
    missing_signal_dates = [date for date in signal_dates if date not in set(all_snapshot_dates)]
    if missing_signal_dates:
        raise SystemExit(
            "missing daily snapshots for signal dates; run fetch_daily_snapshots.py first: "
            + ",".join(missing_signal_dates[:10])
        )

    print(
        "PIT_REPLAY_START "
        f"signals={len(signal_dates)} snapshots={len(all_snapshot_dates)} "
        f"warmup_start={warmup_start} end={args.end_date}",
        flush=True,
    )

    snapshot_cache: dict[str, pd.DataFrame] = {}
    spot_map = _build_product_spot_map_from_snapshots(data_dir, all_snapshot_dates, snapshot_cache)
    spot_map_path = _product_spot_map_path(data_dir, config)
    if not spot_map.empty:
        write_csv(spot_map_path, spot_map)

    contract_observations = _bootstrap_contract_shadow_observations(data_dir, config, args.end_date)
    if contract_observations.empty:
        raise SystemExit("no contract observations built from stored snapshots")
    contract_observations = contract_observations.loc[
        _date_mask(contract_observations, warmup_start, args.end_date)
    ].copy()
    print(f"CONTRACT_OBS rows={len(contract_observations)}", flush=True)

    contract_fields = _add_full_shadow_contract_fields(contract_observations)
    if args.write_intermediate:
        product_side_dir = data_dir / "product_side_panel"
        write_csv(product_side_dir / "rolling_contract_shadow_observations.csv", contract_observations)
        for date, part in contract_fields.groupby(contract_fields["date"].astype(str).str[:10], sort=True):
            write_csv(product_side_dir / "contract_shadow" / f"contract_shadow_fields_{_date_tag(date)}.csv", part)
    print(f"CONTRACT_FIELDS rows={len(contract_fields)}", flush=True)

    observations_base = build_product_side_observations_from_contract_fields(contract_fields, config)
    observations_base = observations_base.loc[
        _date_mask(observations_base, warmup_start, args.end_date)
    ].copy()
    observations_base["date"] = observations_base["date"].astype(str).str[:10]
    if args.write_intermediate:
        write_csv(data_dir / "product_side_panel" / "rolling_product_side_observations_base.csv", observations_base)
    print(f"PRODUCT_SIDE_OBS rows={len(observations_base)}", flush=True)

    by_date = {
        date: part.copy()
        for date, part in observations_base.groupby("date", sort=True)
    }
    obs_dates = sorted(by_date)
    next_obs = 0
    working = _empty_like(observations_base)
    frozen_parts: list[pd.DataFrame] = []
    admission_dir = data_dir / "product_side_panel"
    last_report = time.time()

    for idx, date in enumerate(signal_dates, start=1):
        new_parts: list[pd.DataFrame] = []
        while next_obs < len(obs_dates) and obs_dates[next_obs] <= date:
            new_parts.append(by_date[obs_dates[next_obs]])
            next_obs += 1
        if new_parts:
            working = pd.concat([working, *new_parts], ignore_index=True, sort=False)

        working = _mature_observation_labels(
            working,
            data_dir,
            config,
            int(args.outcome_horizon),
            snapshot_cache,
            as_of_date=date,
        )
        panel = _add_hist_and_scores(working, int(args.hist_window))
        admission = build_rolling_l1_admission(panel, config, date)
        if not admission.empty:
            frozen_parts.append(admission)
            if args.write_daily_admissions:
                write_csv(admission_dir / f"rolling_l1_admission_{_date_tag(date)}.csv", admission)
        now = time.time()
        if idx == 1 or idx == len(signal_dates) or idx % 20 == 0 or now - last_report >= 30:
            print(
                f"PIT_REPLAY_PROGRESS {idx}/{len(signal_dates)} date={date} "
                f"working_rows={len(working)} frozen_rows={sum(len(p) for p in frozen_parts)} "
                f"elapsed={now - started:.1f}s",
                flush=True,
            )
            last_report = now

    if not frozen_parts:
        raise SystemExit("no frozen product-side rows produced")
    frozen = pd.concat(frozen_parts, ignore_index=True, sort=False)
    frozen = frozen.sort_values(["date", "product", "side"], kind="mergesort").reset_index(drop=True)
    write_csv(output, frozen)
    manifest = output.with_suffix(".manifest.json")
    write_json(
        manifest,
        {
            "output": str(output),
            "rows": int(len(frozen)),
            "dates": int(frozen["date"].nunique()),
            "min_date": str(frozen["date"].min()),
            "max_date": str(frozen["date"].max()),
            "config": str(config_path),
            "config_sha256": snapshot.sha256,
            "source_snapshot_dates": len(all_snapshot_dates),
            "warmup_start": warmup_start,
            "hist_window": int(args.hist_window),
            "outcome_horizon": int(args.outcome_horizon),
            "point_in_time_note": (
                "Label maturity is capped by as_of_date for every replayed signal date; "
                "future snapshots in the cache are not visible to earlier rows."
            ),
            "elapsed_seconds": round(time.time() - started, 3),
        },
    )
    print(
        "PIT_REPLAY_OK "
        f"rows={len(frozen)} dates={frozen['date'].nunique()} "
        f"min={frozen['date'].min()} max={frozen['date'].max()} path={output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
