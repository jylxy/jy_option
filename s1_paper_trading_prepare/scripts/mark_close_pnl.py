from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.daily_mark import mark_account_to_close


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark the S1 paper account to the T-day close.")
    parser.add_argument("--as-of-date", required=True, help="Close mark date, YYYY-MM-DD.")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--products", default=None, help="Optional comma-separated product list.")
    parser.add_argument("--product-chunk-size", type=int, default=32)
    parser.add_argument(
        "--write-state",
        action="store_true",
        help="Overwrite the paper-account NAV/positions files with the marked values.",
    )
    args = parser.parse_args()

    result = mark_account_to_close(
        args.as_of_date,
        state_dir=args.state_dir,
        config_path=args.config,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        products=parse_products(args.products),
        product_chunk_size=args.product_chunk_size,
        write_state=args.write_state,
    )
    print(f"CLOSE_MARK_OK as_of_date={result.as_of_date}")
    print(f"nav={result.nav:.2f} daily_pnl={result.daily_pnl:.2f} daily_return={result.daily_return:.8f}")
    print(
        "positions="
        f"{result.position_rows} fresh={result.marked_rows} stale={result.stale_rows} missing={result.missing_rows}"
    )
    print(f"margin_used={result.margin_used:.2f}")
    print(f"positions_path={result.positions_path}")
    print(f"summary_path={result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
