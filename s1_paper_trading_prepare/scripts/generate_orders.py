from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.order_generator import generate_orders


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate S1 paper-trading T+1 planned orders.")
    parser.add_argument("--signal-date", required=True, help="T date used for signal generation, YYYY-MM-DD.")
    parser.add_argument(
        "--replay-start-date",
        default=None,
        help="Start date for historical state replay. Use the locked backtest start date for parity checks.",
    )
    parser.add_argument("--products", default=None, help="Optional comma-separated product list.")
    parser.add_argument("--config", default=None, help="Optional config path. Defaults to configs/s1_paper_mainline.json.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument("--tag", default=None, help="Optional output tag.")
    args = parser.parse_args()

    result = generate_orders(
        args.signal_date,
        replay_start_date=args.replay_start_date,
        products=parse_products(args.products),
        config_path=args.config,
        output_dir=args.output_dir,
        tag=args.tag,
    )
    print(f"signal_date={result.signal_date}")
    print(f"execute_date={result.execute_date}")
    print(f"orders={len(result.orders)} path={result.orders_path}")
    print(f"diagnostics={len(result.diagnostics)} path={result.diagnostics_path}")
    print(f"audit={result.audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

