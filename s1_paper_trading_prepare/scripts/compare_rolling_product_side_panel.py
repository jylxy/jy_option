from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.config_snapshot import load_effective_config
from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, resolve_path
from s1_paper_trading_prepare.src.product_side_rolling import ensure_l1_loader_columns


COMPARE_COLUMNS = [
    "historical_retention_score",
    "historical_retention_score_rank_date",
    "historical_retention_score_bucket5_date",
    "tail_cluster_safety_score",
    "tail_cluster_safety_score_rank_date",
    "tail_cluster_safety_score_bucket5_date",
    "product_side_score",
    "product_side_score_rank_date",
    "avg_v3_b6_premium_to_stress_rank",
]


def _read_panel(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"{label} panel does not exist: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        return ensure_l1_loader_columns(frame)
    return ensure_l1_loader_columns(frame)


def _filter_dates(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = work["date"].astype(str).str[:10]
    return work[work["date"].between(start_date, end_date)].copy()


def _numeric(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def _series(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return frame[col]


def _safe_corr(left: pd.Series, right: pd.Series) -> float | None:
    pair = pd.concat([pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")], axis=1).dropna()
    if len(pair) < 3:
        return None
    corr = pair.iloc[:, 0].corr(pair.iloc[:, 1])
    if pd.isna(corr):
        return None
    return float(corr)


def _match_rate(left: pd.Series, right: pd.Series) -> float | None:
    valid = left.notna() & right.notna()
    if not valid.any():
        return None
    return float(left[valid].eq(right[valid]).mean())


def _build_issue_rows(merged: pd.DataFrame, min_hist_bucket: float, min_tail_bucket: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    key_cols = ["date", "product", "side"]
    for _, row in merged.iterrows():
        issue = ""
        if row["_merge"] == "left_only":
            issue = "missing_in_locked"
        elif row["_merge"] == "right_only":
            issue = "missing_in_rolling"
        else:
            locked_hist = row.get("historical_retention_score_bucket5_date_locked")
            locked_tail = row.get("tail_cluster_safety_score_bucket5_date_locked")
            rolling_hist = row.get("historical_retention_score_bucket5_date_rolling")
            rolling_tail = row.get("tail_cluster_safety_score_bucket5_date_rolling")
            locked_pass = (
                pd.notna(locked_hist)
                and pd.notna(locked_tail)
                and float(locked_hist) >= min_hist_bucket
                and float(locked_tail) >= min_tail_bucket
            )
            rolling_pass = (
                pd.notna(rolling_hist)
                and pd.notna(rolling_tail)
                and float(rolling_hist) >= min_hist_bucket
                and float(rolling_tail) >= min_tail_bucket
            )
            bucket_mismatch = (
                pd.notna(locked_hist)
                and pd.notna(rolling_hist)
                and float(locked_hist) != float(rolling_hist)
            ) or (
                pd.notna(locked_tail)
                and pd.notna(rolling_tail)
                and float(locked_tail) != float(rolling_tail)
            )
            if locked_pass != rolling_pass:
                issue = "l1_gate_mismatch"
            elif bucket_mismatch:
                issue = "bucket_mismatch"
        if not issue:
            continue
        item = {col: row.get(col) for col in key_cols}
        item["issue"] = issue
        for col in COMPARE_COLUMNS:
            item[f"{col}_locked"] = row.get(f"{col}_locked")
            item[f"{col}_rolling"] = row.get(f"{col}_rolling")
        rows.append(item)
    return pd.DataFrame(rows)


def compare_panels(
    locked: pd.DataFrame,
    rolling: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    min_hist_bucket: float,
    min_tail_bucket: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    locked = ensure_l1_loader_columns(locked)
    rolling = ensure_l1_loader_columns(rolling)
    locked_window = _filter_dates(locked, start_date, end_date)
    rolling_window = _filter_dates(rolling, start_date, end_date)
    key_cols = ["date", "product", "side"]
    keep_cols = key_cols + [col for col in COMPARE_COLUMNS if col in locked_window.columns or col in rolling_window.columns]
    locked_cmp = locked_window.loc[:, [col for col in keep_cols if col in locked_window.columns]].drop_duplicates(key_cols)
    rolling_cmp = rolling_window.loc[:, [col for col in keep_cols if col in rolling_window.columns]].drop_duplicates(key_cols)
    merged = locked_cmp.merge(rolling_cmp, how="outer", on=key_cols, suffixes=("_locked", "_rolling"), indicator=True)

    locked_hist = _numeric(merged, "historical_retention_score_bucket5_date_locked")
    locked_tail = _numeric(merged, "tail_cluster_safety_score_bucket5_date_locked")
    rolling_hist = _numeric(merged, "historical_retention_score_bucket5_date_rolling")
    rolling_tail = _numeric(merged, "tail_cluster_safety_score_bucket5_date_rolling")
    locked_pass = locked_hist.ge(min_hist_bucket) & locked_tail.ge(min_tail_bucket)
    rolling_pass = rolling_hist.ge(min_hist_bucket) & rolling_tail.ge(min_tail_bucket)
    both = merged["_merge"].eq("both")
    rolling_ready = rolling_hist.notna() & rolling_tail.notna()

    diff = _build_issue_rows(merged, min_hist_bucket, min_tail_bucket)
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "locked_rows": int(len(locked_window)),
        "rolling_rows": int(len(rolling_window)),
        "overlap_rows": int(both.sum()),
        "missing_in_rolling": int(merged["_merge"].eq("left_only").sum()),
        "missing_in_locked": int(merged["_merge"].eq("right_only").sum()),
        "rolling_gate_ready_rows": int((both & rolling_ready).sum()),
        "locked_l1_pass_rows": int((both & locked_pass).sum()),
        "rolling_l1_pass_rows": int((both & rolling_pass).sum()),
        "l1_gate_match_rate": _match_rate(locked_pass[both], rolling_pass[both]),
        "hist_bucket_match_rate": _match_rate(locked_hist[both], rolling_hist[both]),
        "tail_bucket_match_rate": _match_rate(locked_tail[both], rolling_tail[both]),
        "product_side_score_corr": _safe_corr(
            _series(merged, "product_side_score_locked")[both],
            _series(merged, "product_side_score_rolling")[both],
        ),
        "diff_rows": int(len(diff)),
        "issues": diff["issue"].value_counts().to_dict() if not diff.empty else {},
        "readiness_note": (
            "A new rolling panel can be structurally loader-ready before its shifted "
            "history is mature enough to match the locked research panel."
        ),
    }
    return diff, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare live rolling product-side panel with locked S1 panel.")
    parser.add_argument("--signal-date", default=None, help="Single signal date to compare.")
    parser.add_argument("--start-date", default=None, help="Inclusive start date.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date.")
    parser.add_argument("--config", default=None, help="Optional paper mainline config path.")
    parser.add_argument(
        "--rolling-panel",
        default=str(DEFAULT_DATA_DIR / "product_side_panel" / "rolling_product_side_panel.csv"),
        help="Rolling product-side panel CSV.",
    )
    parser.add_argument("--locked-panel", default=None, help="Override locked product-side panel CSV.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR / "audit"))
    parser.add_argument("--tag", default=None)
    parser.add_argument("--fail-on-diff", action="store_true")
    args = parser.parse_args()

    if args.signal_date:
        start_date = end_date = str(args.signal_date)[:10]
    else:
        if not args.start_date or not args.end_date:
            raise SystemExit("Pass --signal-date or both --start-date and --end-date.")
        start_date = str(args.start_date)[:10]
        end_date = str(args.end_date)[:10]

    snapshot = load_effective_config(args.config)
    locked_path = resolve_path(args.locked_panel or snapshot.config.get("s1_l1_product_side_panel_path"))
    rolling_path = resolve_path(args.rolling_panel)
    locked = _read_panel(locked_path, "locked")
    rolling = _read_panel(rolling_path, "rolling")
    diff, summary = compare_panels(
        locked,
        rolling,
        start_date=start_date,
        end_date=end_date,
        min_hist_bucket=float(snapshot.config.get("s1_l1_min_hist_bucket", 3) or 3),
        min_tail_bucket=float(snapshot.config.get("s1_l1_min_tail_bucket", 3) or 3),
    )

    tag = args.tag or f"rolling_panel_compare_{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    output_dir = resolve_path(args.output_dir)
    diff_path = output_dir / f"diff_{tag}.csv"
    summary_path = output_dir / f"summary_{tag}.json"
    write_csv(diff_path, diff)
    write_json(
        summary_path,
        {
            **summary,
            "config_path": str(snapshot.path),
            "config_sha256": snapshot.sha256,
            "locked_panel_path": str(locked_path),
            "rolling_panel_path": str(rolling_path),
            "diff_path": str(diff_path),
        },
    )
    print(f"date_window={start_date}..{end_date}")
    print(f"locked_rows={summary['locked_rows']} rolling_rows={summary['rolling_rows']} overlap_rows={summary['overlap_rows']}")
    print(f"rolling_gate_ready_rows={summary['rolling_gate_ready_rows']}")
    print(f"diff_rows={summary['diff_rows']} issues={summary['issues']}")
    print(f"gate_match_rate={summary['l1_gate_match_rate']}")
    print(f"diff_path={diff_path}")
    print(f"summary_path={summary_path}")
    return 1 if args.fail_on_diff and summary["diff_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
