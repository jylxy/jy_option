from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.daily_data_update import update_daily_data


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh S1 paper-trading daily input tables from Toolkit.")
    parser.add_argument("--signal-date", default=None, help="Single T date to refresh, YYYY-MM-DD.")
    parser.add_argument("--start-date", default=None, help="Inclusive trading-date window start.")
    parser.add_argument("--end-date", default=None, help="Inclusive trading-date window end.")
    parser.add_argument("--config", default=None, help="Optional paper mainline config path.")
    parser.add_argument("--data-dir", default=None, help="Optional data output directory.")
    parser.add_argument("--products", default=None, help="Optional comma-separated product list.")
    parser.add_argument("--product-chunk-size", type=int, default=32, help="Products per Toolkit query chunk.")
    parser.add_argument("--force", action="store_true", help="Re-fetch existing date partitions from Toolkit.")
    args = parser.parse_args()

    result = update_daily_data(
        args.signal_date,
        start_date=args.start_date,
        end_date=args.end_date,
        config_path=args.config,
        data_dir=args.data_dir,
        products=parse_products(args.products),
        product_chunk_size=args.product_chunk_size,
        force=args.force,
    )
    print(f"DATA_UPDATE_OK dates={len(result.dates)} last_signal_date={result.signal_date}")
    print(f"fetched_dates={','.join(result.fetched_dates) if result.fetched_dates else '-'}")
    print(f"reused_dates={','.join(result.reused_dates) if result.reused_dates else '-'}")
    print(f"snapshot_rows={result.snapshot_rows} path={result.snapshot_path}")
    print(f"l0_universe_rows={result.l0_universe_rows} path={result.l0_universe_path}")
    print(f"l1_admission_rows={result.l1_admission_rows} path={result.l1_admission_path}")
    print(
        "rolling_l1_admission_rows="
        f"{result.rolling_l1_admission_rows} path={result.rolling_l1_admission_path}"
    )
    print(f"rolling_panel_rows={result.rolling_panel_rows} path={result.rolling_panel_path}")
    print(f"rolling_matured_rows={result.rolling_matured_rows}")
    print(f"manifest_path={result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
