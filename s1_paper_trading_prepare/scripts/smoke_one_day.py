from __future__ import annotations

from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.order_generator import generate_orders


def main() -> int:
    result = generate_orders(
        "2024-10-24",
        tag="smoke_current_line_20241024",
    )
    print(f"SMOKE_OK signal_date={result.signal_date} execute_date={result.execute_date}")
    print(f"orders={len(result.orders)} path={result.orders_path}")
    print(f"diagnostics={len(result.diagnostics)} path={result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
