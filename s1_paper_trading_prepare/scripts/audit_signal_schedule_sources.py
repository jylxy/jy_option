from __future__ import annotations

import argparse
import json
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
from s1_paper_trading_prepare.src.paths import DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, resolve_path


DEFAULT_OVERLAY23_REFERENCE = (
    REPO_ROOT
    / "output"
    / "four_layer_main_s1p95_025_s2_025_s3_025_layerstop_cluster2_20220104_20260331"
    / "open_signals.csv"
)
REFERENCE_FIELD_PARITY_NOTE = (
    "Historical source references are optional. Do not use the old include_etf "
    "main+overlay1 research output as a field-parity source: after filtering "
    "SSE/SZSE rows, ETF trades can still affect NAV, current margin, and margin "
    "budget fields. Strict parity must be checked against the regenerated clean "
    "schedule with audit_signal_schedule_gold.py."
)

OVERLAY23_LAYERS = {
    "overlay2_risk_reversal_same_sign",
    "overlay3_term_structure_cluster_cap2",
}
SCHEDULE_KEY = [
    "entry_date",
    "product",
    "exchange",
    "contract_code",
    "option_type",
    "target_expiry",
    "entry_reason",
    "strategy_layer",
]
IMPORTANT_COLUMNS = [
    "dte",
    "delta",
    "close_oi",
    "volume",
    "entry_price",
    "strike",
    "spot_close",
    "sell_side",
    "side_rule",
    "selected_side_iv_pressure",
    "other_side_iv_pressure",
    "side_iv_pressure_diff",
    "qty",
    "target_qty",
    "premium_cash",
    "target_premium_cash",
    "target_premium_pct",
    "budget_group",
    "margin_cash",
    "one_lot_margin_cash",
    "current_margin_cash_before",
    "margin_budget_cash",
    "post_open_margin_pct_nav",
    "entry_reason",
    "strategy_layer",
    "overlay_signal_rule",
    "overlay_trigger_trade_date",
    "overlay_tier_max_abs_delta",
    "overlay_tier_target_premium_pct",
    "overlay_strategy",
    "overlay_signal_family",
    "overlay_side_rule",
    "forced_sell_side",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def _layer_series(frame: pd.DataFrame) -> pd.Series:
    if "strategy_layer" not in frame.columns:
        return pd.Series("", index=frame.index, dtype=object)
    return frame["strategy_layer"].fillna("").astype(str)


def _normalize_key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in SCHEDULE_KEY:
        if col not in out.columns:
            out[col] = ""
    for col in SCHEDULE_KEY:
        out[col] = out[col].fillna("").astype(str)
    return out


def _split_current_schedule(schedule: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    layer = _layer_series(schedule)
    overlay23 = schedule[layer.isin(OVERLAY23_LAYERS)].copy()
    main_overlay1 = schedule[~layer.isin(OVERLAY23_LAYERS)].copy()
    return main_overlay1, overlay23


def _build_reference_schedule(
    main_overlay1: pd.DataFrame,
    overlay23: pd.DataFrame,
    excluded_exchanges: set[str],
) -> pd.DataFrame:
    main = main_overlay1.copy()
    if excluded_exchanges and "exchange" in main.columns:
        main = main[~main["exchange"].fillna("").astype(str).str.upper().isin(excluded_exchanges)].copy()
    layer = _layer_series(overlay23)
    sidecars = overlay23[layer.isin(OVERLAY23_LAYERS)].copy()
    return pd.concat([main, sidecars], ignore_index=True, sort=False)


def _key_diff(left: pd.DataFrame, right: pd.DataFrame, label: str) -> pd.DataFrame:
    left_keys = _normalize_key_frame(left)[SCHEDULE_KEY].drop_duplicates()
    right_keys = _normalize_key_frame(right)[SCHEDULE_KEY].drop_duplicates()
    missing = left_keys.merge(right_keys, how="left", indicator=True).query("_merge == 'left_only'")
    extra = right_keys.merge(left_keys, how="left", indicator=True).query("_merge == 'left_only'")
    rows = []
    for side, frame in (("missing_in_reference", missing), ("extra_in_reference", extra)):
        for _, row in frame.drop(columns=["_merge"], errors="ignore").iterrows():
            rows.append({"scope": label, "diff_type": side, **row.to_dict()})
    return pd.DataFrame(rows)


def _column_diff(
    current: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    tolerance: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    current_norm = _normalize_key_frame(current)
    reference_norm = _normalize_key_frame(reference)
    common_columns = [
        col
        for col in IMPORTANT_COLUMNS
        if col not in SCHEDULE_KEY and col in current_norm.columns and col in reference_norm.columns
    ]
    merged = current_norm[SCHEDULE_KEY + common_columns].merge(
        reference_norm[SCHEDULE_KEY + common_columns],
        on=SCHEDULE_KEY,
        how="inner",
        suffixes=("_current", "_reference"),
    )
    summary_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    for col in common_columns:
        cur = merged[f"{col}_current"]
        ref = merged[f"{col}_reference"]
        cur_num = pd.to_numeric(cur, errors="coerce")
        ref_num = pd.to_numeric(ref, errors="coerce")
        numeric_mask = cur_num.notna() | ref_num.notna()
        if numeric_mask.any():
            diff = (cur_num - ref_num).abs()
            mismatch = numeric_mask & (
                (cur_num.isna() != ref_num.isna())
                | diff.gt(tolerance)
            )
        else:
            mismatch = cur.fillna("").astype(str).ne(ref.fillna("").astype(str))
        if not bool(mismatch.any()):
            continue
        summary_rows.append({
            "column": col,
            "diff_rows": int(mismatch.sum()),
            "max_abs_diff": float((cur_num - ref_num).abs().loc[mismatch].max())
            if numeric_mask.any()
            else np.nan,
        })
        sample = merged.loc[mismatch, SCHEDULE_KEY].head(20).copy()
        sample["column"] = col
        sample["current_value"] = cur.loc[mismatch].head(20).to_numpy()
        sample["reference_value"] = ref.loc[mismatch].head(20).to_numpy()
        sample_rows.extend(sample.to_dict("records"))
    return pd.DataFrame(summary_rows), pd.DataFrame(sample_rows)


def _duplicate_key_rows(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    keys = _normalize_key_frame(frame)[SCHEDULE_KEY]
    dup = keys[keys.duplicated(SCHEDULE_KEY, keep=False)].copy()
    if dup.empty:
        return pd.DataFrame()
    dup.insert(0, "scope", label)
    return dup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the current S1 paper signal schedule against reference lookup tables.")
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--schedule", default=None, help="Override current schedule path.")
    parser.add_argument(
        "--main-overlay1-reference",
        default=None,
        help="Optional historical source for key provenance. No default is used to avoid include_etf leakage.",
    )
    parser.add_argument(
        "--overlay23-reference",
        default=None,
        help=f"Optional overlay2/3 source for key provenance, e.g. {DEFAULT_OVERLAY23_REFERENCE}",
    )
    parser.add_argument("--exclude-exchanges", default="SSE,SZSE")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--tag", default="signal_schedule_source_audit")
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument(
        "--strict-fields",
        action="store_true",
        help="Fail on field-level differences. By default the audit enforces source row keys and reports field diffs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_effective_config(args.config)
    schedule_path = resolve_path(args.schedule or snapshot.config.get("external_signal_path"))
    main_ref_path = resolve_path(args.main_overlay1_reference) if args.main_overlay1_reference else None
    overlay23_ref_path = resolve_path(args.overlay23_reference) if args.overlay23_reference else None
    output_dir = resolve_path(args.output_dir)
    excluded_exchanges = {
        item.strip().upper()
        for item in str(args.exclude_exchanges or "").split(",")
        if item.strip()
    }

    current = _read_csv(schedule_path)
    current_main, current_23 = _split_current_schedule(current)
    duplicates = _duplicate_key_rows(current, "current_schedule")
    reference_available = (
        main_ref_path is not None
        and overlay23_ref_path is not None
        and main_ref_path.exists()
        and overlay23_ref_path.exists()
    )
    if not reference_available:
        audit = {
            "tag": args.tag,
            "config_path": str(snapshot.path),
            "config_sha256": snapshot.sha256,
            "schedule_path": str(schedule_path),
            "main_overlay1_reference": str(main_ref_path) if main_ref_path else None,
            "overlay23_reference": str(overlay23_ref_path) if overlay23_ref_path else None,
            "reference_available": False,
            "missing_references": [
                str(path)
                for path in (main_ref_path, overlay23_ref_path)
                if path is None or not path.exists()
            ],
            "current_rows": int(len(current)),
            "current_main_overlay1_rows": int(len(current_main)),
            "current_overlay23_rows": int(len(current_23)),
            "duplicate_key_rows": int(len(duplicates)),
            "strict_fields": bool(args.strict_fields),
            "field_parity_note": REFERENCE_FIELD_PARITY_NOTE,
            "passed": bool(duplicates.empty and not current.empty),
        }
        audit_dir = output_dir / "audit"
        write_json(audit_dir / f"{args.tag}.json", audit)
        if not duplicates.empty:
            write_csv(audit_dir / f"{args.tag}_duplicate_keys.csv", duplicates)
        print(
            "SIGNAL_SOURCE_AUDIT "
            f"passed={audit['passed']} reference_available=False current_rows={audit['current_rows']} "
            f"duplicates={audit['duplicate_key_rows']}"
        )
        print(f"audit={audit_dir / f'{args.tag}.json'}")
        return 0 if audit["passed"] else 1

    main_ref = _read_csv(main_ref_path)
    overlay23_ref = _read_csv(overlay23_ref_path)
    reference = _build_reference_schedule(main_ref, overlay23_ref, excluded_exchanges)
    ref_main, ref_23 = _split_current_schedule(reference)

    key_diffs = pd.concat(
        [
            _key_diff(current_main, ref_main, "main_overlay1"),
            _key_diff(current_23, ref_23, "overlay23"),
        ],
        ignore_index=True,
        sort=False,
    )
    column_diff_summary, column_diffs = _column_diff(current, reference, tolerance=float(args.tolerance))
    duplicates = pd.concat(
        [
            duplicates,
            _duplicate_key_rows(reference, "reference_schedule"),
        ],
        ignore_index=True,
        sort=False,
    )
    audit = {
        "tag": args.tag,
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "schedule_path": str(schedule_path),
        "main_overlay1_reference": str(main_ref_path) if main_ref_path else None,
        "overlay23_reference": str(overlay23_ref_path) if overlay23_ref_path else None,
        "reference_available": True,
        "exclude_exchanges": sorted(excluded_exchanges),
        "current_rows": int(len(current)),
        "reference_rows": int(len(reference)),
        "current_main_overlay1_rows": int(len(current_main)),
        "reference_main_overlay1_rows": int(len(ref_main)),
        "current_overlay23_rows": int(len(current_23)),
        "reference_overlay23_rows": int(len(ref_23)),
        "key_diff_rows": int(len(key_diffs)),
        "column_diff_columns": int(len(column_diff_summary)),
        "column_diff_rows_total": int(column_diff_summary["diff_rows"].sum()) if not column_diff_summary.empty else 0,
        "column_diff_rows_sampled": int(len(column_diffs)),
        "duplicate_key_rows": int(len(duplicates)),
        "strict_fields": bool(args.strict_fields),
        "field_parity_note": REFERENCE_FIELD_PARITY_NOTE,
    }
    audit["passed"] = bool(
        key_diffs.empty
        and duplicates.empty
        and len(current) == len(reference)
        and (column_diff_summary.empty or not args.strict_fields)
    )

    audit_dir = output_dir / "audit"
    write_json(audit_dir / f"{args.tag}.json", audit)
    if not key_diffs.empty:
        write_csv(audit_dir / f"{args.tag}_key_diffs.csv", key_diffs)
    if not column_diff_summary.empty:
        write_csv(audit_dir / f"{args.tag}_column_diff_summary.csv", column_diff_summary)
    if not column_diffs.empty:
        write_csv(audit_dir / f"{args.tag}_column_diffs.csv", column_diffs)
    if not duplicates.empty:
        write_csv(audit_dir / f"{args.tag}_duplicate_keys.csv", duplicates)

    print(
        "SIGNAL_SOURCE_AUDIT "
        f"passed={audit['passed']} current_rows={audit['current_rows']} reference_rows={audit['reference_rows']} "
        f"key_diffs={audit['key_diff_rows']} column_diff_rows={audit['column_diff_rows_total']} "
        f"column_diff_columns={audit['column_diff_columns']} duplicates={audit['duplicate_key_rows']}"
    )
    print(f"audit={audit_dir / f'{args.tag}.json'}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
