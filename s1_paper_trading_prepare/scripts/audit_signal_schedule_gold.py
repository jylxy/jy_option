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


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def _normalize_key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in SCHEDULE_KEY:
        if column not in out.columns:
            out[column] = ""
        out[column] = out[column].fillna("").astype(str)
    return out


def _filter_entry_dates(frame: pd.DataFrame, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    if not start_date and not end_date:
        return frame.copy()
    if "entry_date" not in frame.columns:
        return frame.iloc[0:0].copy()
    dates = pd.to_datetime(frame["entry_date"], errors="coerce")
    mask = dates.notna()
    if start_date:
        mask &= dates >= pd.Timestamp(start_date)
    if end_date:
        mask &= dates <= pd.Timestamp(end_date)
    return frame.loc[mask].copy()


def _duplicate_key_rows(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    keys = _normalize_key_frame(frame)[SCHEDULE_KEY]
    duplicates = keys[keys.duplicated(SCHEDULE_KEY, keep=False)].copy()
    if duplicates.empty:
        return pd.DataFrame()
    duplicates.insert(0, "scope", label)
    return duplicates


def _key_diff(generated: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    generated_keys = _normalize_key_frame(generated)[SCHEDULE_KEY].drop_duplicates()
    gold_keys = _normalize_key_frame(gold)[SCHEDULE_KEY].drop_duplicates()
    missing = gold_keys.merge(generated_keys, how="left", indicator=True).query("_merge == 'left_only'")
    extra = generated_keys.merge(gold_keys, how="left", indicator=True).query("_merge == 'left_only'")
    rows: list[dict[str, Any]] = []
    for diff_type, frame in (("missing_in_generated", missing), ("extra_in_generated", extra)):
        for _, row in frame.drop(columns=["_merge"], errors="ignore").iterrows():
            rows.append({"diff_type": diff_type, **row.to_dict()})
    return pd.DataFrame(rows)


def _is_numeric_like(left: pd.Series, right: pd.Series) -> bool:
    combined = pd.concat([left, right], ignore_index=True)
    combined = combined.dropna()
    if combined.empty:
        return False
    text = combined.astype(str).str.strip()
    text = text[text != ""]
    if text.empty:
        return False
    parsed = pd.to_numeric(text, errors="coerce")
    return bool(parsed.notna().mean() >= 0.95)


def _value_diffs(
    generated: pd.DataFrame,
    gold: pd.DataFrame,
    *,
    tolerance: float,
    sample_per_column: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    generated_norm = _normalize_key_frame(generated)
    gold_norm = _normalize_key_frame(gold)
    common_columns = [
        column
        for column in gold_norm.columns
        if column not in SCHEDULE_KEY and column in generated_norm.columns
    ]
    if not common_columns:
        return pd.DataFrame(), pd.DataFrame()

    merged = generated_norm[SCHEDULE_KEY + common_columns].merge(
        gold_norm[SCHEDULE_KEY + common_columns],
        on=SCHEDULE_KEY,
        how="inner",
        suffixes=("_generated", "_gold"),
    )
    diff_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for column in common_columns:
        generated_values = merged[f"{column}_generated"]
        gold_values = merged[f"{column}_gold"]
        numeric_like = _is_numeric_like(generated_values, gold_values)
        if numeric_like:
            generated_num = pd.to_numeric(generated_values, errors="coerce")
            gold_num = pd.to_numeric(gold_values, errors="coerce")
            abs_diff = (generated_num - gold_num).abs()
            mismatch = (
                generated_num.isna().ne(gold_num.isna())
                | abs_diff.gt(tolerance)
            )
            max_abs_diff = float(abs_diff.loc[mismatch].max()) if bool(mismatch.any()) else 0.0
        else:
            generated_text = generated_values.fillna("").astype(str)
            gold_text = gold_values.fillna("").astype(str)
            mismatch = generated_text.ne(gold_text)
            abs_diff = pd.Series(np.nan, index=merged.index)
            max_abs_diff = np.nan

        diff_count = int(mismatch.sum())
        if diff_count <= 0:
            continue
        summary_rows.append({
            "column": column,
            "diff_rows": diff_count,
            "numeric_like": bool(numeric_like),
            "max_abs_diff": max_abs_diff,
        })
        sample = merged.loc[mismatch, SCHEDULE_KEY].head(sample_per_column).copy()
        sample["column"] = column
        sample["generated_value"] = generated_values.loc[mismatch].head(sample_per_column).to_numpy()
        sample["gold_value"] = gold_values.loc[mismatch].head(sample_per_column).to_numpy()
        sample["abs_diff"] = abs_diff.loc[mismatch].head(sample_per_column).to_numpy()
        diff_rows.extend(sample.to_dict("records"))
    return pd.DataFrame(summary_rows), pd.DataFrame(diff_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly audit a generated S1 signal schedule against the committed gold schedule."
    )
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument(
        "--generated",
        default=None,
        help="Generated schedule to audit. Defaults to the configured gold schedule for a self-check.",
    )
    parser.add_argument(
        "--gold",
        default=None,
        help="Gold schedule path. Defaults to external_signal_path in the paper config.",
    )
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--tag", default="signal_schedule_gold_audit")
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument("--sample-per-column", type=int, default=50)
    parser.add_argument(
        "--allow-extra-columns",
        action="store_true",
        help="Allow generated files to contain extra columns not present in the gold schedule.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_effective_config(args.config)
    gold_path = resolve_path(args.gold or snapshot.config.get("external_signal_path"))
    generated_path = resolve_path(args.generated or gold_path)
    output_dir = resolve_path(args.output_dir)

    gold = _filter_entry_dates(_read_csv(gold_path), args.start_date, args.end_date)
    generated = _filter_entry_dates(_read_csv(generated_path), args.start_date, args.end_date)

    gold_columns = list(gold.columns)
    generated_columns = list(generated.columns)
    missing_columns = [column for column in gold_columns if column not in generated_columns]
    extra_columns = [column for column in generated_columns if column not in gold_columns]
    duplicates = pd.concat(
        [
            _duplicate_key_rows(generated, "generated_schedule"),
            _duplicate_key_rows(gold, "gold_schedule"),
        ],
        ignore_index=True,
        sort=False,
    )
    key_diffs = _key_diff(generated, gold)
    value_summary, value_samples = _value_diffs(
        generated,
        gold,
        tolerance=float(args.tolerance),
        sample_per_column=max(int(args.sample_per_column), 1),
    )

    audit = {
        "tag": args.tag,
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "generated_path": str(generated_path),
        "gold_path": str(gold_path),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "generated_rows": int(len(generated)),
        "gold_rows": int(len(gold)),
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "missing_column_count": int(len(missing_columns)),
        "extra_column_count": int(len(extra_columns)),
        "key_diff_rows": int(len(key_diffs)),
        "duplicate_key_rows": int(len(duplicates)),
        "value_diff_columns": int(len(value_summary)),
        "value_diff_cells_sampled": int(len(value_samples)),
        "value_diff_rows_total": int(value_summary["diff_rows"].sum()) if not value_summary.empty else 0,
        "tolerance": float(args.tolerance),
        "allow_extra_columns": bool(args.allow_extra_columns),
    }
    audit["passed"] = bool(
        len(generated) == len(gold)
        and not missing_columns
        and (not extra_columns or bool(args.allow_extra_columns))
        and key_diffs.empty
        and duplicates.empty
        and value_summary.empty
    )

    audit_dir = output_dir / "audit"
    write_json(audit_dir / f"{args.tag}.json", audit)
    if not key_diffs.empty:
        write_csv(audit_dir / f"{args.tag}_key_diffs.csv", key_diffs)
    if not duplicates.empty:
        write_csv(audit_dir / f"{args.tag}_duplicate_keys.csv", duplicates)
    if not value_summary.empty:
        write_csv(audit_dir / f"{args.tag}_value_diff_summary.csv", value_summary)
    if not value_samples.empty:
        write_csv(audit_dir / f"{args.tag}_value_diff_samples.csv", value_samples)

    print(
        "SIGNAL_GOLD_AUDIT "
        f"passed={audit['passed']} generated_rows={audit['generated_rows']} gold_rows={audit['gold_rows']} "
        f"key_diffs={audit['key_diff_rows']} value_diff_rows={audit['value_diff_rows_total']} "
        f"missing_columns={audit['missing_column_count']} extra_columns={audit['extra_column_count']}"
    )
    print(f"audit={audit_dir / f'{args.tag}.json'}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
