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
from s1_paper_trading_prepare.src.daily_mark import mark_account_to_close
from s1_paper_trading_prepare.src.diagnostics import write_json
from s1_paper_trading_prepare.src.order_generator import generate_orders
from s1_paper_trading_prepare.src.pit_signal_appender import PitSignalAppender
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
    parser.add_argument("--replay-start-date", default=None, help="Optional historical replay start date for paper-account state.")
    parser.add_argument("--state-dir", default=None, help="Optional paper-account state directory.")
    parser.add_argument("--config", default=None, help="Optional paper mainline config.")
    parser.add_argument("--data-dir", default=None, help="Optional data output directory.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument("--products", default=None, help="Optional comma-separated product list.")
    parser.add_argument("--product-chunk-size", type=int, default=8)
    parser.add_argument("--force-data", action="store_true", help="Re-fetch existing Toolkit partitions.")
    parser.add_argument("--require-account-state", action="store_true", help="Fail if paper-account state files are missing.")
    parser.add_argument("--write-marked-state", action="store_true", help="Persist close marks back into paper-account state files.")
    parser.add_argument("--skip-signal-refresh", action="store_true", help="Use the existing external signal schedule without appending T factors.")
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
    mark_result = mark_account_to_close(
        signal_date,
        state_dir=args.state_dir,
        config_path=args.config,
        data_dir=args.data_dir,
        output_dir=output_dir,
        products=products,
        product_chunk_size=args.product_chunk_size,
        write_state=args.write_marked_state,
    )
    signal_result = None
    if not args.skip_signal_refresh:
        appender = PitSignalAppender(
            config_path=args.config,
            data_dir=args.data_dir,
            output_dir=output_dir,
        )
        signal_result = appender.append_date(
            signal_date,
            products=products,
            nav=mark_result.nav,
            current_margin_cash=mark_result.margin_used,
            tag=f"{tag}_signal_append",
        )
    order_result = generate_orders(
        signal_date,
        replay_start_date=args.replay_start_date,
        products=products,
        config_path=args.config,
        output_dir=output_dir,
        state_dir=args.state_dir,
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
            "close_mark_summary_path": str(mark_result.summary_path) if mark_result.summary_path else None,
            "close_mark_positions_path": str(mark_result.positions_path) if mark_result.positions_path else None,
            "close_mark_daily_pnl": mark_result.daily_pnl,
            "close_mark_daily_return": mark_result.daily_return,
            "close_mark_stale_rows": mark_result.stale_rows,
            "close_mark_missing_rows": mark_result.missing_rows,
            "signal_refresh_enabled": not args.skip_signal_refresh,
            "signal_refresh_rows": signal_result.rows_for_date if signal_result else None,
            "signal_refresh_schedule_path": str(signal_result.schedule_path) if signal_result else None,
            "signal_refresh_diagnostics": str(signal_result.diagnostics_path) if signal_result else None,
            "orders_path": str(order_result.orders_path) if order_result.orders_path else None,
            "diagnostics_path": str(order_result.diagnostics_path) if order_result.diagnostics_path else None,
            "order_audit_path": str(order_result.audit_path) if order_result.audit_path else None,
            "generated_order_count": int(len(order_result.orders)),
            "notes": [
                "Order generation follows the approved S1 external-intent schedule for the current line.",
                "Toolkit minute replay remains the validation path for fills, pending, reroute, expiry, and overlay stop.",
            ],
        },
    )

    print(f"DAILY_PIPELINE_OK signal_date={signal_date} execute_date={order_result.execute_date}")
    print(f"account_state_ok={account_state.ok} account_manifest={account_state.manifest_path}")
    print(f"data_manifest={data_result.manifest_path}")
    print(f"close_mark_daily_pnl={mark_result.daily_pnl:.2f} daily_return={mark_result.daily_return:.8f} summary={mark_result.summary_path}")
    if signal_result:
        print(f"signal_refresh_rows={signal_result.rows_for_date} schedule={signal_result.schedule_path}")
    print(f"orders={len(order_result.orders)} path={order_result.orders_path}")
    print(f"pipeline_manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
