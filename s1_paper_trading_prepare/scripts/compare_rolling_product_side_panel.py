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


def _rank_high(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() <= 1:
        return pd.Series(0.5, index=values.index)
    return values.rank(pct=True).fillna(0.5).clip(0.0, 1.0)


def _flatten_l1_overlay(overlay: dict[str, Any] | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not overlay:
        return pd.DataFrame()
    for product, sides in (overlay.get("side_meta_map") or {}).items():
        for side, meta in (sides or {}).items():
            item = {
                "product": str(product).upper().strip(),
                "side": str(side).upper().strip()[:1],
            }
            for col in (
                "l1_pass_gate",
                "l1_hist_bucket",
                "l1_tail_bucket",
                "l1_sort_score",
                "l1_sort_rank",
                "l1_sort_bucket",
                "l1_budget_mult",
                "l1_side_final_budget_pct",
                "l3_refill_allowed",
            ):
                item[col] = meta.get(col, np.nan)
            rows.append(item)
    return pd.DataFrame(rows)


def _budget_impact_rows(
    *,
    locked_path: Path,
    rolling_path: Path,
    locked: pd.DataFrame,
    rolling: pd.DataFrame,
    diff: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    if diff.empty:
        return pd.DataFrame()
    try:
        from s1_experimental_scoring import l1_product_side_gate_budget_overlay
    except Exception as exc:
        return pd.DataFrame([{"budget_impact_error": repr(exc)}])

    locked = ensure_l1_loader_columns(locked)
    rolling = ensure_l1_loader_columns(rolling)
    key_cols = ["date", "product", "side"]
    outputs: list[pd.DataFrame] = []
    total_budget = float(config.get("portfolio_entry_premium_cap", 0.025) or 0.025)

    for date in sorted(diff["date"].dropna().astype(str).str[:10].unique()):
        locked_day = _filter_dates(locked, date, date)
        rolling_day = _filter_dates(rolling, date, date)
        grid = (
            pd.concat(
                [
                    locked_day[["product", "option_type"]],
                    rolling_day[["product", "option_type"]],
                ],
                ignore_index=True,
                sort=False,
            )
            .dropna(subset=["product", "option_type"])
            .drop_duplicates()
        )
        if grid.empty:
            continue
        grid["product"] = grid["product"].astype(str).str.upper().str.strip()
        grid["option_type"] = grid["option_type"].astype(str).str.upper().str[:1]
        products = sorted(grid["product"].dropna().astype(str).str.upper().unique())
        base_product_budget_map = {product: total_budget / max(len(products), 1) for product in products}
        locked_overlay = l1_product_side_gate_budget_overlay(
            grid,
            base_product_budget_map,
            total_budget,
            date,
            1.0,
            config={**config, "s1_l1_product_side_panel_path": str(locked_path)},
            rank_high=_rank_high,
        )
        rolling_overlay = l1_product_side_gate_budget_overlay(
            grid,
            base_product_budget_map,
            total_budget,
            date,
            1.0,
            config={**config, "s1_l1_product_side_panel_path": str(rolling_path)},
            rank_high=_rank_high,
        )
        locked_flat = _flatten_l1_overlay(locked_overlay).add_suffix("_locked")
        rolling_flat = _flatten_l1_overlay(rolling_overlay).add_suffix("_rolling")
        if locked_flat.empty and rolling_flat.empty:
            continue
        locked_flat = locked_flat.rename(columns={"product_locked": "product", "side_locked": "side"})
        rolling_flat = rolling_flat.rename(columns={"product_rolling": "product", "side_rolling": "side"})
        merged = locked_flat.merge(rolling_flat, how="outer", on=["product", "side"])
        merged["date"] = date
        outputs.append(merged)

    if not outputs:
        return pd.DataFrame()
    impact = pd.concat(outputs, ignore_index=True, sort=False)
    diff_keys = diff.loc[:, [col for col in key_cols + ["issue"] if col in diff.columns]].copy()
    impact = diff_keys.merge(impact, on=key_cols, how="left")
    for col in (
        "l1_pass_gate",
        "l1_sort_bucket",
        "l1_budget_mult",
        "l1_side_final_budget_pct",
        "l3_refill_allowed",
    ):
        left = pd.to_numeric(impact.get(f"{col}_locked"), errors="coerce").round(12)
        right = pd.to_numeric(impact.get(f"{col}_rolling"), errors="coerce").round(12)
        impact[f"{col}_mismatch"] = left.ne(right)
    return impact


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
    budget_impact_path = output_dir / f"budget_impact_{tag}.csv"
    budget_impact = _budget_impact_rows(
        locked_path=locked_path,
        rolling_path=rolling_path,
        locked=locked,
        rolling=rolling,
        diff=diff,
        config=snapshot.config,
    )
    if not budget_impact.empty:
        issue_mask = budget_impact["issue"].eq("bucket_mismatch") if "issue" in budget_impact.columns else pd.Series(False, index=budget_impact.index)
        summary["bucket_budget_impact"] = {
            "rows": int(issue_mask.sum()),
            "l1_sort_bucket_mismatch": int(budget_impact.loc[issue_mask, "l1_sort_bucket_mismatch"].sum())
            if "l1_sort_bucket_mismatch" in budget_impact.columns
            else 0,
            "l1_budget_mult_mismatch": int(budget_impact.loc[issue_mask, "l1_budget_mult_mismatch"].sum())
            if "l1_budget_mult_mismatch" in budget_impact.columns
            else 0,
            "l1_side_final_budget_pct_mismatch": int(
                budget_impact.loc[issue_mask, "l1_side_final_budget_pct_mismatch"].sum()
            )
            if "l1_side_final_budget_pct_mismatch" in budget_impact.columns
            else 0,
            "l3_refill_allowed_mismatch": int(budget_impact.loc[issue_mask, "l3_refill_allowed_mismatch"].sum())
            if "l3_refill_allowed_mismatch" in budget_impact.columns
            else 0,
            "l1_pass_gate_mismatch": int(budget_impact.loc[issue_mask, "l1_pass_gate_mismatch"].sum())
            if "l1_pass_gate_mismatch" in budget_impact.columns
            else 0,
            "path": str(budget_impact_path),
        }
    else:
        summary["bucket_budget_impact"] = {"rows": 0, "path": str(budget_impact_path)}
    write_csv(diff_path, diff)
    write_csv(budget_impact_path, budget_impact)
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
    print(f"bucket_budget_impact={summary['bucket_budget_impact']}")
    print(f"gate_match_rate={summary['l1_gate_match_rate']}")
    print(f"diff_path={diff_path}")
    print(f"budget_impact_path={budget_impact_path}")
    print(f"summary_path={summary_path}")
    return 1 if args.fail_on_diff and summary["diff_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
