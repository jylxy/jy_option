from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.diagnostics import compare_order_frames, write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_BACKTEST_ORDERS, DEFAULT_OUTPUT_DIR, resolve_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare generated S1 paper orders with locked backtest orders.")
    parser.add_argument("--generated", required=True, help="Generated orders CSV from output/orders.")
    parser.add_argument("--baseline", default=str(DEFAULT_BACKTEST_ORDERS), help="Locked baseline orders CSV.")
    parser.add_argument("--signal-date", default=None, help="Signal date to compare. Defaults to generated file value.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR / "audit"))
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    generated_path = resolve_path(args.generated)
    baseline_path = resolve_path(args.baseline)
    generated = pd.read_csv(generated_path)
    if args.signal_date:
        signal_date = str(args.signal_date)[:10]
    elif not generated.empty and "signal_date" in generated.columns:
        signal_date = str(generated["signal_date"].iloc[0])[:10]
    else:
        raise SystemExit("Cannot infer signal date from generated file; pass --signal-date.")

    baseline = pd.read_csv(baseline_path)
    if "signal_date" in baseline.columns:
        baseline = baseline[baseline["signal_date"].astype(str).str[:10].eq(signal_date)].copy()
    if "strategy" in baseline.columns:
        baseline = baseline[baseline["strategy"].astype(str).eq("S1")].copy()
    if "action" in baseline.columns:
        baseline = baseline[baseline["action"].astype(str).eq("open_sell")].copy()

    diff, summary = compare_order_frames(generated, baseline)
    tag = args.tag or f"s1_paper_compare_{signal_date.replace('-', '')}"
    output_dir = resolve_path(args.output_dir)
    diff_path = output_dir / f"diff_orders_{tag}.csv"
    summary_path = output_dir / f"summary_{tag}.json"
    write_csv(diff_path, diff)
    write_json(
        summary_path,
        {
            **summary,
            "signal_date": signal_date,
            "generated_path": str(generated_path),
            "baseline_path": str(baseline_path),
            "diff_path": str(diff_path),
        },
    )
    print(f"signal_date={signal_date}")
    print(f"generated_orders={summary['generated_orders']}")
    print(f"baseline_orders={summary['baseline_orders']}")
    print(f"diff_rows={summary['diff_rows']} issues={summary['issues']}")
    print(f"diff_path={diff_path}")
    print(f"summary_path={summary_path}")
    return 0 if summary["diff_rows"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

