from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.config_snapshot import apply_primary_strategy_only, load_effective_config
from s1_paper_trading_prepare.src.diagnostics import compare_order_frames, write_csv, write_json
from s1_paper_trading_prepare.src.paths import (
    DEFAULT_BACKTEST_ORDERS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PAPER_CONFIG,
    ensure_server_deploy_importable,
    resolve_path,
)


def _filter_s1_open_orders(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    if "signal_date" in out.columns:
        signal = out["signal_date"].astype(str).str[:10]
        out = out.loc[signal.ge(start_date) & signal.le(end_date)].copy()
    if "strategy" in out.columns:
        out = out.loc[out["strategy"].astype(str).str.upper().eq("S1")].copy()
    if "action" in out.columns:
        out = out.loc[out["action"].astype(str).eq("open_sell")].copy()
    return out


def _run_locked_replay(config_path: Path, start_date: str, replay_end_date: str, tag: str) -> pd.DataFrame:
    ensure_server_deploy_importable()
    from toolkit_minute_engine import ToolkitMinuteEngine

    class SmokeReplayEngine(ToolkitMinuteEngine):
        def _output_results(self, nav_df, orders_df, stats, tag, elapsed):  # noqa: D401
            self._paper_nav_df = nav_df
            self._paper_orders_df = orders_df
            self._paper_stats = stats
            self._paper_tag = tag
            self._paper_elapsed = elapsed

    engine = SmokeReplayEngine(config_path=str(config_path))
    engine.config = apply_primary_strategy_only(engine.config)
    engine.run(start_date=start_date, end_date=replay_end_date, tag=f"{tag}_compat_replay")
    return getattr(engine, "_paper_orders_df", pd.DataFrame()).copy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test S1 paper replay against locked backtest orders over a date range.")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default="2022-06-30")
    parser.add_argument(
        "--replay-end-date",
        default=None,
        help="Optional replay end date. Use T+1 when the signal end date's planned orders execute on the next trading day.",
    )
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--baseline", default=str(DEFAULT_BACKTEST_ORDERS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR / "audit"))
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    start_date = str(args.start_date)[:10]
    end_date = str(args.end_date)[:10]
    replay_end_date = str(args.replay_end_date or args.end_date)[:10]
    tag = args.tag or f"s1_range_smoke_{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    config_path = resolve_path(args.config)
    output_dir = resolve_path(args.output_dir) / tag
    baseline_path = resolve_path(args.baseline)

    snapshot = load_effective_config(config_path)
    generated_raw = _run_locked_replay(config_path, start_date, replay_end_date, tag)
    generated = _filter_s1_open_orders(generated_raw, start_date, end_date)
    baseline_raw = pd.read_csv(baseline_path)
    baseline = _filter_s1_open_orders(baseline_raw, start_date, end_date)

    diff, summary = compare_order_frames(generated, baseline)
    generated_path = output_dir / f"generated_orders_{tag}.csv"
    baseline_slice_path = output_dir / f"baseline_orders_{tag}.csv"
    diff_path = output_dir / f"diff_orders_{tag}.csv"
    summary_path = output_dir / f"summary_{tag}.json"
    write_csv(generated_path, generated)
    write_csv(baseline_slice_path, baseline)
    write_csv(diff_path, diff)
    write_json(
        summary_path,
        {
            **summary,
            "start_date": start_date,
            "end_date": end_date,
            "replay_end_date": replay_end_date,
            "tag": tag,
            "config_path": str(config_path),
            "config_sha256": snapshot.sha256,
            "baseline_path": str(baseline_path),
            "generated_path": str(generated_path),
            "baseline_slice_path": str(baseline_slice_path),
            "diff_path": str(diff_path),
        },
    )

    generated_days = sorted(generated.get("signal_date", pd.Series(dtype=str)).astype(str).str[:10].unique().tolist())
    baseline_days = sorted(baseline.get("signal_date", pd.Series(dtype=str)).astype(str).str[:10].unique().tolist())
    print(f"RANGE_SMOKE start_date={start_date} end_date={end_date} replay_end_date={replay_end_date}")
    print(f"generated_orders={summary['generated_orders']} baseline_orders={summary['baseline_orders']}")
    print(f"diff_rows={summary['diff_rows']} issues={summary['issues']}")
    print(f"generated_signal_days={len(generated_days)} first={generated_days[0] if generated_days else '-'} last={generated_days[-1] if generated_days else '-'}")
    print(f"baseline_signal_days={len(baseline_days)} first={baseline_days[0] if baseline_days else '-'} last={baseline_days[-1] if baseline_days else '-'}")
    print(f"summary_path={summary_path}")
    print(f"diff_path={diff_path}")
    return 0 if summary["diff_rows"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
