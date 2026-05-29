from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.account_state import validate_account_state
from s1_paper_trading_prepare.src.daily_data_update import update_daily_data
from s1_paper_trading_prepare.src.diagnostics import write_json
from s1_paper_trading_prepare.src.order_generator import generate_orders
from s1_paper_trading_prepare.src.paths import DEFAULT_OUTPUT_DIR, resolve_path


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    products = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return products or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily S1 paper-trading preparation pipeline.")
    parser.add_argument("--signal-date", required=True, help="T date used for signal generation, YYYY-MM-DD.")
    parser.add_argument("--account-date", default=None, help="Account-state date. Defaults to signal date.")
    parser.add_argument("--replay-start-date", default=None, help="Historical replay start date for parity-style generation.")
    parser.add_argument("--state-dir", default=None, help="Optional paper-account state directory.")
    parser.add_argument("--config", default=None, help="Optional paper mainline config.")
    parser.add_argument("--data-dir", default=None, help="Optional data output directory.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument("--products", default=None, help="Optional comma-separated product list.")
    parser.add_argument("--product-chunk-size", type=int, default=8)
    parser.add_argument("--force-data", action="store_true", help="Re-fetch existing Toolkit partitions.")
    parser.add_argument("--require-account-state", action="store_true", help="Fail if paper-account state files are missing.")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    signal_date = str(args.signal_date)[:10]
    account_date = str(args.account_date or signal_date)[:10]
    output_dir = resolve_path(args.output_dir, default=DEFAULT_OUTPUT_DIR)
    tag = args.tag or f"s1_daily_paper_{signal_date.replace('-', '')}"
    products = parse_products(args.products)

    account_state = validate_account_state(
        account_date,
        state_dir=args.state_dir,
        require_files=args.require_account_state,
        output_dir=output_dir / "audit",
    )
    if args.require_account_state and not account_state.ok:
        print(f"PIPELINE_BLOCKED account_state_issues={account_state.issues}")
        return 1

    data_result = update_daily_data(
        signal_date,
        config_path=args.config,
        data_dir=args.data_dir,
        products=products,
        product_chunk_size=args.product_chunk_size,
        force=args.force_data,
    )
    order_result = generate_orders(
        signal_date,
        replay_start_date=args.replay_start_date,
        products=products,
        config_path=args.config,
        output_dir=output_dir,
        tag=tag,
    )

    manifest_path = output_dir / "audit" / f"daily_pipeline_{tag}.json"
    write_json(
        manifest_path,
        {
            "tag": tag,
            "signal_date": signal_date,
            "account_date": account_date,
            "account_state_ok": account_state.ok,
            "account_state_manifest": str(account_state.manifest_path) if account_state.manifest_path else None,
            "account_state_summary": account_state.summary,
            "data_manifest": str(data_result.manifest_path) if data_result.manifest_path else None,
            "fetched_dates": data_result.fetched_dates,
            "reused_dates": data_result.reused_dates,
            "orders_path": str(order_result.orders_path) if order_result.orders_path else None,
            "diagnostics_path": str(order_result.diagnostics_path) if order_result.diagnostics_path else None,
            "order_audit_path": str(order_result.audit_path) if order_result.audit_path else None,
            "generated_order_count": int(len(order_result.orders)),
            "notes": [
                "Phase-1 order generation still follows the locked replay adapter for backtest parity.",
                "Paper-account state is validated and recorded for audit; direct live-state sizing hookup is the next phase.",
            ],
        },
    )

    print(f"DAILY_PIPELINE_OK signal_date={signal_date} execute_date={order_result.execute_date}")
    print(f"account_state_ok={account_state.ok} account_manifest={account_state.manifest_path}")
    print(f"data_manifest={data_result.manifest_path}")
    print(f"orders={len(order_result.orders)} path={order_result.orders_path}")
    print(f"pipeline_manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
