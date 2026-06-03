from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
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


DEFAULT_ROLLING_OPPORTUNITIES = (
    REPO_ROOT
    / "output"
    / "reverse_low_jump_iv_pressure_rolling_20260531"
    / "rolling_product_opportunities_l1_flow_oi_guard_20260531.csv"
)
DEFAULT_INPUT_ENTRY = (
    REPO_ROOT
    / "output"
    / "reverse_lowjump_cluster_budget_plus_overlay_include_etf_daily_20260531"
    / "input_entry_signals.csv"
)

SOURCE_SUSPECT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|_)shadow($|_)",
        r"(^|_)label($|_)",
        r"final_",
        r"expiry_(pnl|intrinsic)",
        r"path_(max|stop)",
        r"terminal_otm",
        r"stop_touch",
        r"adverse",
        r"forward",
        r"future_ret",
        r"next_day",
    )
]

KNOWN_SAFE_NAME_EXCEPTIONS = {
    "product_label",
    "product_label_feature",
    "target_expiry",
    "expiry_date",
    "prev_expiry",
    "near_expiry_date",
    "next_expiry_date",
    "expiry_rank",
    "expiry_date_lag1",
    "expiry_date_lag2",
    "expiry_date_lag3",
    "expiry_date_lag4",
    "near_expiry_date_lag1",
    "near_expiry_date_lag2",
    "near_expiry_date_lag3",
    "near_expiry_date_lag4",
    "next_expiry_date_lag1",
    "next_expiry_date_lag2",
    "next_expiry_date_lag3",
    "next_expiry_date_lag4",
}

OUTCOME_TO_SHADOW_PRODUCT = {
    "shadow_prod_pnl_nonnegative": "expiry_pnl_nonnegative",
    "shadow_prod_terminal_otm": "terminal_otm",
    "shadow_prod_stop25_safe": "path_stop25_safe",
    "shadow_prod_stop2_safe": "path_stop2_safe",
    "shadow_prod_pnlprem": "expiry_pnl_per_premium",
}
OUTCOME_TO_SHADOW_SIDE = {
    "shadow_side_pnl_nonnegative": "expiry_pnl_nonnegative",
    "shadow_side_terminal_otm": "terminal_otm",
    "shadow_side_stop25_safe": "path_stop25_safe",
    "shadow_side_stop2_safe": "path_stop2_safe",
    "shadow_side_pnlprem": "expiry_pnl_per_premium",
}


def _read_csv(path: Path, *, parse_dates: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, parse_dates=parse_dates or [])


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _boolish(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(object).where(series.notna(), "").astype(str).str.lower().isin({"true", "1", "yes"})


def _date_key(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d").fillna("")


def suspect_columns(frame: pd.DataFrame) -> list[str]:
    out = []
    for column in frame.columns:
        if column in KNOWN_SAFE_NAME_EXCEPTIONS:
            continue
        if any(pattern.search(column) for pattern in SOURCE_SUSPECT_PATTERNS):
            out.append(column)
    return out


def shifted_rolling_match_audit(frame: pd.DataFrame, group_cols: list[str], mapping: dict[str, str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    needed = {"entry_date", *group_cols}
    if not needed.issubset(frame.columns):
        return pd.DataFrame()
    out = frame.sort_values(group_cols + ["entry_date"]).copy()
    rows = []
    for shadow_col, outcome_col in mapping.items():
        if shadow_col not in out.columns or outcome_col not in out.columns:
            continue
        pred = (
            out.groupby(group_cols, dropna=False)[outcome_col]
            .transform(lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(12, min_periods=1).mean())
        )
        actual = pd.to_numeric(out[shadow_col], errors="coerce")
        diff = (actual - pred).abs()
        rows.append(
            {
                "shadow_field": shadow_col,
                "outcome_field": outcome_col,
                "group": "+".join(group_cols),
                "non_null": int(actual.notna().sum()),
                "exact_matches": int(diff.lt(1e-9).sum()),
                "max_abs_diff": float(diff.dropna().max()) if diff.notna().any() else None,
                "mean_abs_diff": float(diff.dropna().mean()) if diff.notna().any() else None,
            }
        )
    return pd.DataFrame(rows)


def immature_shadow_context(frame: pd.DataFrame, group_cols: list[str], window: int = 12) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"entry_date", "target_expiry", *group_cols}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(index=frame.index), pd.DataFrame()

    work = frame.reset_index(drop=False).rename(columns={"index": "_source_index"}).copy()
    work["entry_date"] = pd.to_datetime(work["entry_date"], errors="coerce")
    work["target_expiry"] = pd.to_datetime(work["target_expiry"], errors="coerce")
    work = work.sort_values(group_cols + ["entry_date", "target_expiry", "_source_index"])

    count_rows: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    for _, group in work.groupby(group_cols, dropna=False, sort=False):
        idxs = list(group.index)
        entries = group["entry_date"].tolist()
        expiries = group["target_expiry"].tolist()
        for pos, row_index in enumerate(idxs):
            previous = list(range(max(0, pos - window), pos))
            immature_positions = [
                prev_pos
                for prev_pos in previous
                if pd.notna(expiries[prev_pos])
                and pd.notna(entries[pos])
                and expiries[prev_pos] >= entries[pos]
            ]
            source_index = int(group.loc[row_index, "_source_index"])
            count_rows.append(
                {
                    "_source_index": source_index,
                    f"{'_'.join(group_cols)}_prev_n": len(previous),
                    f"{'_'.join(group_cols)}_immature_prev_n": len(immature_positions),
                }
            )
            for prev_pos in immature_positions[:3]:
                if len(examples) >= 200:
                    break
                previous_row = group.iloc[prev_pos]
                current_row = group.loc[row_index]
                examples.append(
                    {
                        "group": "+".join(group_cols),
                        "group_value": "|".join(str(current_row.get(col, "")) for col in group_cols),
                        "current_entry_date": entries[pos].strftime("%Y-%m-%d") if pd.notna(entries[pos]) else "",
                        "current_product": current_row.get("product_label", current_row.get("product", "")),
                        "current_sell_side": current_row.get("sell_side", ""),
                        "current_contract_code": current_row.get("contract_code", ""),
                        "current_target_expiry": current_row["target_expiry"].strftime("%Y-%m-%d")
                        if pd.notna(current_row["target_expiry"])
                        else "",
                        "previous_entry_date": previous_row["entry_date"].strftime("%Y-%m-%d")
                        if pd.notna(previous_row["entry_date"])
                        else "",
                        "previous_target_expiry": previous_row["target_expiry"].strftime("%Y-%m-%d")
                        if pd.notna(previous_row["target_expiry"])
                        else "",
                        "previous_product": previous_row.get("product_label", previous_row.get("product", "")),
                        "previous_sell_side": previous_row.get("sell_side", ""),
                        "previous_contract_code": previous_row.get("contract_code", ""),
                    }
                )

    counts = pd.DataFrame(count_rows).set_index("_source_index").sort_index()
    examples_frame = pd.DataFrame(examples)
    return counts, examples_frame


def attach_rolling_shadow_flags(rolling: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    product_counts, product_examples = immature_shadow_context(rolling, ["product_key"])
    side_counts, side_examples = immature_shadow_context(rolling, ["product_key", "sell_side"])
    flags = pd.DataFrame(index=rolling.index)
    for counts in (product_counts, side_counts):
        if counts.empty:
            continue
        flags = flags.join(counts, how="left")
    immature_cols = [col for col in flags.columns if col.endswith("_immature_prev_n")]
    flags["has_immature_shadow_context"] = flags[immature_cols].fillna(0).gt(0).any(axis=1) if immature_cols else False
    examples = pd.concat([product_examples, side_examples], ignore_index=True, sort=False)
    return flags, examples


def match_input_to_rolling(input_entry: pd.DataFrame, rolling: pd.DataFrame, rolling_flags: pd.DataFrame) -> pd.DataFrame:
    if input_entry.empty or rolling.empty:
        return pd.DataFrame()
    key_cols = ["entry_date_key", "product_key", "sell_side", "contract_code"]
    left = input_entry.copy()
    right = rolling.copy()
    if "entry_date" not in left.columns or "entry_date" not in right.columns:
        return pd.DataFrame()
    left["entry_date_key"] = _date_key(left["entry_date"])
    right["entry_date_key"] = _date_key(right["entry_date"])
    if "product_key" not in left.columns and {"exchange", "product"}.issubset(left.columns):
        left["product_key"] = left["exchange"].astype(str) + "|" + left["product"].astype(str)
    right = right.join(rolling_flags[["has_immature_shadow_context"]], how="left")
    keep_right = key_cols + [
        col
        for col in (
            "has_immature_shadow_context",
            "rule_l1_hsafe_addon025",
            "rule_l1_hsafe_core",
            "rule_l1_oi03_flow_guard",
            "rule_pit_shadow_rank20",
            "rule_pit_shadow_rank20_hiqual",
        )
        if col in right.columns
    ]
    merged = left.merge(right[keep_right].drop_duplicates(key_cols), on=key_cols, how="left", suffixes=("", "_rolling"))
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deep audit S1 factor construction for hidden future-function risk.")
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--schedule", default=None, help="Current schedule path. Defaults to external_signal_path in config.")
    parser.add_argument("--rolling-opportunities", default=str(DEFAULT_ROLLING_OPPORTUNITIES))
    parser.add_argument("--input-entry", default=str(DEFAULT_INPUT_ENTRY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--tag", default="factor_future_leakage_deep_audit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_effective_config(args.config)
    schedule_path = resolve_path(args.schedule or snapshot.config.get("external_signal_path"))
    if not schedule_path.exists() and not args.schedule and snapshot.config.get("validation_gold_signal_path"):
        gold_path = resolve_path(snapshot.config.get("validation_gold_signal_path"))
        if gold_path.exists():
            schedule_path = gold_path
    rolling_path = resolve_path(args.rolling_opportunities)
    input_entry_path = resolve_path(args.input_entry)
    output_dir = resolve_path(args.output_dir)
    audit_dir = output_dir / "audit"

    schedule = _read_csv(schedule_path)
    rolling = _read_csv(rolling_path, parse_dates=["entry_date", "target_expiry"])
    input_entry = _read_csv(input_entry_path, parse_dates=["entry_date", "target_expiry"])

    source_column_rows = []
    for name, frame, path in (
        ("schedule", schedule, schedule_path),
        ("rolling_opportunities", rolling, rolling_path),
        ("input_entry_signals", input_entry, input_entry_path),
    ):
        for column in suspect_columns(frame):
            source_column_rows.append({"source": name, "path": str(path), "column": column})
    source_columns = pd.DataFrame(source_column_rows)

    match_product = shifted_rolling_match_audit(rolling, ["product_key"], OUTCOME_TO_SHADOW_PRODUCT)
    match_side = shifted_rolling_match_audit(rolling, ["product_key", "sell_side"], OUTCOME_TO_SHADOW_SIDE)
    shadow_match = pd.concat([match_product, match_side], ignore_index=True, sort=False)

    rolling_flags, immature_examples = attach_rolling_shadow_flags(rolling)
    input_matched = match_input_to_rolling(input_entry, rolling, rolling_flags)
    input_immature = (
        input_matched["has_immature_shadow_context"].where(input_matched["has_immature_shadow_context"].notna(), False).astype(bool)
        if "has_immature_shadow_context" in input_matched.columns
        else pd.Series(False, index=input_matched.index)
    )
    input_boost = (
        input_matched["l3eff015_tier"].astype(str).eq("boost015")
        if "l3eff015_tier" in input_matched.columns
        else pd.Series(False, index=input_matched.index)
    )

    rule_rows = []
    for rule in [
        "rule_l1_hsafe_addon025",
        "rule_l1_hsafe_core",
        "rule_l1_oi03_flow_guard",
        "rule_pit_shadow_rank20",
        "rule_pit_shadow_rank20_hiqual",
        "rule_shadow_rank12",
        "rule_shadow_rank15",
    ]:
        if rule not in rolling.columns:
            continue
        selected = _boolish(rolling[rule])
        immature = rolling_flags["has_immature_shadow_context"].fillna(False)
        rule_rows.append(
            {
                "rule": rule,
                "selected_rows": int(selected.sum()),
                "selected_with_immature_shadow_context": int((selected & immature).sum()),
                "rule_name_contains_shadow": bool("shadow" in rule.lower()),
            }
        )
    rule_audit = pd.DataFrame(rule_rows)

    schedule_suspect = source_columns[source_columns["source"].eq("schedule")]
    input_shadow_cols = [col for col in input_entry.columns if col.startswith("shadow_")]
    input_shadow_or_outcome_fields_present = bool(input_shadow_cols or not source_columns[source_columns["source"].eq("input_entry_signals")].empty)
    fatal_findings = []
    if not schedule_suspect.empty:
        fatal_findings.append("schedule_contains_future_or_shadow_like_columns")
    if int((input_immature & input_boost).sum()) > 0:
        fatal_findings.append("boost_sizing_rows_have_immature_shadow_context")

    summary = {
        "tag": args.tag,
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "schedule_path": str(schedule_path),
        "rolling_opportunities_path": str(rolling_path),
        "input_entry_path": str(input_entry_path),
        "schedule_rows": int(len(schedule)),
        "rolling_opportunity_rows": int(len(rolling)),
        "input_entry_rows": int(len(input_entry)),
        "schedule_suspect_columns": schedule_suspect["column"].tolist(),
        "rolling_suspect_columns_count": int(source_columns["source"].eq("rolling_opportunities").sum()),
        "input_entry_suspect_columns_count": int(source_columns["source"].eq("input_entry_signals").sum()),
        "input_entry_shadow_columns_count": int(len(input_shadow_cols)),
        "rolling_rows_with_immature_shadow_context": int(rolling_flags["has_immature_shadow_context"].fillna(False).sum()),
        "input_entry_rows_with_immature_shadow_context": int(input_immature.sum()),
        "input_entry_boost_rows": int(input_boost.sum()),
        "input_entry_boost_rows_with_immature_shadow_context": int((input_immature & input_boost).sum()),
        "fatal_findings": fatal_findings,
        "passed_for_current_schedule": bool(not fatal_findings),
        "interpretation": [
            "Schedule-level handoff should contain no shadow/path/expiry-PnL fields.",
            "rolling_opportunities/input_entry_signals are research lineage tables and may contain labels for audit.",
            "shadow_* fields are expected to be shifted rolling histories, but they are unsafe for live generation unless every included prior outcome had matured by the current signal date.",
            "The clean incremental generator should not consume shadow_* fields; if a historical-performance feature is needed, rebuild it with an explicit maturity date guard.",
        ],
    }

    write_json(audit_dir / f"{args.tag}.json", summary)
    if not source_columns.empty:
        write_csv(audit_dir / f"{args.tag}_suspect_columns.csv", source_columns)
    if not shadow_match.empty:
        write_csv(audit_dir / f"{args.tag}_shadow_shifted_match.csv", shadow_match)
    if not rule_audit.empty:
        write_csv(audit_dir / f"{args.tag}_rule_shadow_maturity.csv", rule_audit)
    if not immature_examples.empty:
        write_csv(audit_dir / f"{args.tag}_immature_shadow_examples.csv", immature_examples)
    if not input_matched.empty:
        sample_cols = [
            col
            for col in [
                "entry_date",
                "product",
                "sell_side",
                "contract_code",
                "target_expiry",
                "l3eff015_tier",
                "target_premium_pct_override",
                "shadow_score",
                "shadow_prod_n12",
                "shadow_side_n12",
                "has_immature_shadow_context",
            ]
            if col in input_matched.columns
        ]
        write_csv(
            audit_dir / f"{args.tag}_input_entry_shadow_context.csv",
            input_matched.loc[input_immature, sample_cols].copy(),
        )

    print(
        "FACTOR_FUTURE_LEAKAGE_AUDIT "
        f"passed_for_current_schedule={summary['passed_for_current_schedule']} "
        f"schedule_rows={summary['schedule_rows']} "
        f"schedule_suspect_cols={len(summary['schedule_suspect_columns'])} "
        f"rolling_immature_shadow_rows={summary['rolling_rows_with_immature_shadow_context']} "
        f"input_immature_shadow_rows={summary['input_entry_rows_with_immature_shadow_context']} "
        f"boost_immature_shadow_rows={summary['input_entry_boost_rows_with_immature_shadow_context']}"
    )
    print(f"audit={audit_dir / f'{args.tag}.json'}")
    return 0 if summary["passed_for_current_schedule"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
