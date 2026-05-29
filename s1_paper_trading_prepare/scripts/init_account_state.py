from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.account_state import ACCOUNT_STATE_COLUMNS, expected_account_state_files
from s1_paper_trading_prepare.src.paths import PROJECT_DIR as PAPER_PROJECT_DIR, resolve_path


def _write_if_allowed(path: Path, frame: pd.DataFrame, force: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return "exists"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return "written"


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize S1 paper-account state files for one date.")
    parser.add_argument("--as-of-date", required=True, help="Account-state date, YYYY-MM-DD.")
    parser.add_argument("--nav", type=float, required=True, help="Paper account NAV.")
    parser.add_argument("--cash", type=float, default=None)
    parser.add_argument("--available-cash", type=float, default=None)
    parser.add_argument("--margin-used", type=float, default=0.0)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--force", action="store_true", help="Overwrite existing files.")
    args = parser.parse_args()

    date = str(args.as_of_date)[:10]
    state_dir = resolve_path(args.state_dir, default=PAPER_PROJECT_DIR / "state")
    files = expected_account_state_files(state_dir, date)
    cash = args.nav if args.cash is None else args.cash
    available_cash = cash if args.available_cash is None else args.available_cash
    now = datetime.now().replace(microsecond=0).isoformat()

    nav_frame = pd.DataFrame(
        [
            {
                "as_of_date": date,
                "nav": args.nav,
                "cash": cash,
                "available_cash": available_cash,
                "margin_used": args.margin_used,
                "gross_option_market_value": 0.0,
                "realized_pnl_mtd": 0.0,
                "unrealized_pnl": 0.0,
                "source": "manual",
                "updated_at": now,
            }
        ],
        columns=ACCOUNT_STATE_COLUMNS["nav_state"],
    )
    empty_frames = {
        "positions": pd.DataFrame(columns=ACCOUNT_STATE_COLUMNS["positions"]),
        "pending_orders": pd.DataFrame(columns=ACCOUNT_STATE_COLUMNS["pending_orders"]),
        "fills": pd.DataFrame(columns=ACCOUNT_STATE_COLUMNS["fills"]),
    }

    results = {"nav_state": _write_if_allowed(files["nav_state"], nav_frame, args.force)}
    for name, frame in empty_frames.items():
        results[name] = _write_if_allowed(files[name], frame, args.force)

    print(f"ACCOUNT_STATE_INIT date={date} state_dir={state_dir}")
    for name, status in results.items():
        print(f"{name}={status} path={files[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
