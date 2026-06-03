"""Rebuild only the reverse-low-jump main sleeve from Toolkit snapshots.

This is the fast validation path for main-sleeve PIT opportunity generation. It
does not rebuild sidecars or minute replay outputs, and by default writes audit
artifacts only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.diagnostics import write_csv  # noqa: E402
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, resolve_path  # noqa: E402
from s1_paper_trading_prepare.src.pit_signal_appender import PitSignalAppender, sanitize_main_selected_for_production  # noqa: E402
from s1_paper_trading_prepare.src.reverse_lowjump_main import build_current_main_intents  # noqa: E402


def _date_tag(date: str) -> str:
    return str(date)[:10].replace("-", "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild main-sleeve opportunities from cached Toolkit daily snapshots only.")
    parser.add_argument("--feature-start-date", default="2018-01-01")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--signals-start-date", default="2022-01-04")
    parser.add_argument("--signals-end-date", default=None)
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--opportunity-rule", default=None, help="Override main_sleeve.opportunity_rule for candidate validation.")
    parser.add_argument("--tag", default="main_snapshot_rebuild")
    parser.add_argument("--write-data-panels", action="store_true", help="Also replace data/reverse_lowjump main opportunity files.")
    parser.add_argument("--progress-every", type=int, default=250)
    return parser.parse_args()


def _snapshot_files(data_dir: Path, start: str, end: str) -> list[Path]:
    start_tag = _date_tag(start)
    end_tag = _date_tag(end)
    root = data_dir / "daily_snapshots"
    files = []
    for path in sorted(root.glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if start_tag <= tag <= end_tag:
            files.append(path)
    return files


def main() -> int:
    args = parse_args()
    data_dir = resolve_path(args.data_dir)
    output_dir = resolve_path(args.output_dir)
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    appender = PitSignalAppender(config_path=args.config, data_dir=data_dir, output_dir=output_dir)
    files = _snapshot_files(data_dir, args.feature_start_date, args.end_date)
    snapshots: list[pd.DataFrame] = []
    skipped: list[str] = []
    for idx, path in enumerate(files, start=1):
        date = path.stem.replace("option_chain_", "")
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        try:
            snapshots.append(appender._load_snapshot(date))
        except Exception as exc:
            skipped.append(f"{date}: {exc}")
        if idx == 1 or idx == len(files) or idx % max(1, int(args.progress_every or 1)) == 0:
            print(f"[load snapshots {idx}/{len(files)}] {date} loaded={len(snapshots)} skipped={len(skipped)}", flush=True)

    option_data = pd.concat(snapshots, ignore_index=True, sort=False) if snapshots else pd.DataFrame()
    flow_path = data_dir / "reverse_lowjump" / "side_flow_guard_panel.csv"
    flow_panel = pd.read_csv(flow_path) if flow_path.exists() else pd.DataFrame()
    main_cfg = appender.config.get("main_sleeve", {})
    opportunity_rule = str(args.opportunity_rule or main_cfg.get("opportunity_rule", "rule_l1_hsafe_addon025"))
    signals_end = str(args.signals_end_date or args.end_date)[:10]
    opps, selected, skips, dense = build_current_main_intents(
        option_data,
        flow_panel,
        start_date=str(args.signals_start_date)[:10],
        end_date=signals_end,
        rule=opportunity_rule,
        entry_window_calendar_days=int(main_cfg.get("entry_window_calendar_days", 7)),
        min_history_days=int(main_cfg.get("min_history_days", 120)),
        max_abs_delta=float(main_cfg.get("max_abs_delta", 0.08)),
        min_oi=float(main_cfg.get("min_oi", 1000)),
        l3_weak_pressure_threshold=float(main_cfg.get("l3_weak_pressure_threshold", 0.02)),
        l4_weak_pressure_threshold=float(main_cfg.get("l4_weak_pressure_threshold", 0.02)),
        l4_weak_delta_cap=float(main_cfg.get("l4_weak_delta_cap", 0.04)),
    )

    paths = {
        "opportunities": audit_dir / f"{args.tag}_main_opportunities.csv",
        "selected": audit_dir / f"{args.tag}_main_selected.csv",
        "skips": audit_dir / f"{args.tag}_main_l4_skips.csv",
        "dense_scores": audit_dir / f"{args.tag}_main_dense_scores.csv",
        "summary": audit_dir / f"{args.tag}.json",
    }
    write_csv(paths["opportunities"], opps)
    write_csv(paths["selected"], selected)
    write_csv(paths["skips"], skips)
    write_csv(paths["dense_scores"], dense)

    if args.write_data_panels:
        write_csv(data_dir / "reverse_lowjump" / "product_month_opportunities.csv", opps)
        write_csv(appender._panel_paths()["main_selected"], sanitize_main_selected_for_production(selected))

    summary = {
        "tag": args.tag,
        "feature_start_date": str(args.feature_start_date)[:10],
        "signals_start_date": str(args.signals_start_date)[:10],
        "signals_end_date": signals_end,
        "end_date": str(args.end_date)[:10],
        "snapshot_files": len(files),
        "snapshots_loaded": len(snapshots),
        "skipped": skipped[:20],
        "flow_panel_rows": int(len(flow_panel)),
        "opportunity_rule": opportunity_rule,
        "opportunities": int(len(opps)),
        "selected": int(len(selected)),
        "l4_skips": int(len(skips)),
        "write_data_panels": bool(args.write_data_panels),
        "paths": {key: str(value) for key, value in paths.items()},
    }
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MAIN_SNAPSHOT_REBUILD", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
