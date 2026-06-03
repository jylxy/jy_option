from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.daily_data_update import update_daily_data
from s1_paper_trading_prepare.src.data_loader import load_trading_dates
from s1_paper_trading_prepare.src.pit_signal_appender import PitSignalAppender
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, resolve_path


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Append one point-in-time S1 signal date from Toolkit raw snapshots into "
            "the unified external-intent schedule. This path forbids shadow/path/label inputs."
        )
    )
    parser.add_argument("--signal-date", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--signals-start-date", default=None, help="Warm up panels before this date without mutating the schedule.")
    parser.add_argument("--signals-end-date", default=None, help="Refresh panels after this date without mutating the schedule.")
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-schedule", default=None)
    parser.add_argument("--output-schedule", default=None)
    parser.add_argument("--products", default=None)
    parser.add_argument("--product-chunk-size", type=int, default=8)
    parser.add_argument("--fetch-missing", action="store_true", help="Fetch the daily snapshot first if needed.")
    parser.add_argument("--force-data", action="store_true", help="Re-fetch the snapshot when --fetch-missing is set.")
    parser.add_argument("--nav", type=float, default=None)
    parser.add_argument("--current-margin-cash", type=float, default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--no-replace-date", action="store_true")
    parser.add_argument("--skip-panel-refresh", action="store_true", help="Use already rebuilt PIT panel files and only regenerate schedule rows.")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    if not args.signal_date and not (args.start_date and args.end_date):
        parser.error("provide --signal-date or both --start-date/--end-date")

    products = parse_products(args.products)
    if args.start_date or args.end_date:
        start = args.start_date or args.signal_date or args.end_date
        end = args.end_date or args.signal_date or args.start_date
        try:
            dates = load_trading_dates(start, end)
        except Exception:
            import pandas as pd

            dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(start=start, end=end, freq="D")]
    else:
        dates = [str(args.signal_date)[:10]]
    signals_start = str(args.signals_start_date or dates[0])[:10]
    signals_end = str(args.signals_end_date or dates[-1])[:10]

    if args.fetch_missing:
        update_daily_data(
            None,
            start_date=dates[0],
            end_date=dates[-1],
            config_path=args.config,
            data_dir=args.data_dir,
            products=products,
            product_chunk_size=args.product_chunk_size,
            force=args.force_data,
        )

    appender = PitSignalAppender(
        config_path=args.config,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        schedule_path=args.source_schedule or None,
    )
    if args.skip_panel_refresh:
        appender.preload_panels()
    source_schedule = args.source_schedule
    output_schedule = args.output_schedule
    result = None
    for idx, date in enumerate(dates, start=1):
        tag = args.tag if len(dates) == 1 else f"{args.tag or 'incremental_backfill'}_{date.replace('-', '')}"
        update_schedule = signals_start <= date <= signals_end
        result = appender.append_date(
            date,
            source_schedule=source_schedule,
            output_schedule=output_schedule,
            products=products,
            nav=args.nav,
            current_margin_cash=args.current_margin_cash,
            replace_existing_date=not args.no_replace_date,
            update_schedule=update_schedule,
            refresh_panels=not args.skip_panel_refresh,
            tag=tag,
        )
        if update_schedule:
            source_schedule = output_schedule or str(result.schedule_path)
            output_schedule = output_schedule or str(result.schedule_path)
        if idx == 1 or idx == len(dates) or idx % max(1, int(args.progress_every or 1)) == 0 or result.rows_for_date:
            print(
                "[append "
                f"{idx}/{len(dates)}] {date} "
                f"schedule_updated={update_schedule} rows={result.rows_for_date} "
                f"output_rows={result.output_rows}",
                flush=True,
            )
    assert result is not None
    print(
        "APPEND_DAILY_SIGNALS "
        f"dates={dates[0]}..{dates[-1]} last_date={result.signal_date} rows_for_last_date={result.rows_for_date} "
        f"output_rows={result.output_rows} schedule={result.schedule_path}"
    )
    print(f"diagnostics={result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
