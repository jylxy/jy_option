from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.account_state import validate_account_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate S1 paper-account state files for one date.")
    parser.add_argument("--as-of-date", required=True, help="Account-state date, YYYY-MM-DD.")
    parser.add_argument("--state-dir", default=None, help="Optional state directory. Defaults to s1_paper_trading_prepare/state.")
    parser.add_argument("--output-dir", default=None, help="Optional audit output directory.")
    parser.add_argument("--require-files", action="store_true", help="Fail if expected state files are missing.")
    args = parser.parse_args()

    result = validate_account_state(
        args.as_of_date,
        state_dir=args.state_dir,
        require_files=args.require_files,
        output_dir=args.output_dir,
    )
    print(f"ACCOUNT_STATE date={result.as_of_date} ok={result.ok}")
    print(f"state_dir={result.state_dir}")
    print(f"row_counts={result.row_counts}")
    print(f"summary={result.summary}")
    print(f"manifest_path={result.manifest_path}")
    if result.issues:
        print(f"issues={result.issues}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
