from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.order_generator import generate_orders


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test one historical S1 paper-order generation day.")
    parser.add_argument("--signal-date", default="2022-04-14")
    parser.add_argument("--replay-start-date", default="2022-01-04")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()
    tag = args.tag or f"s1_paper_smoke_{args.signal_date.replace('-', '')}"
    result = generate_orders(
        args.signal_date,
        replay_start_date=args.replay_start_date,
        tag=tag,
    )
    print(f"SMOKE_OK signal_date={result.signal_date} execute_date={result.execute_date} orders={len(result.orders)}")
    print(f"orders_path={result.orders_path}")
    print(f"diagnostics_path={result.diagnostics_path}")
    print(f"audit_path={result.audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

