from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.scripts.audit_signal_schedule_gold import SCHEDULE_KEY
from s1_paper_trading_prepare.src.config_snapshot import load_effective_config
from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, resolve_path


DEFAULT_OUTPUT = DEFAULT_DATA_DIR / "external_signals" / "regenerated_open_signals.csv"
ALLOWED_ENTRY_REASONS = {"monthly", "iv_extreme_overlay"}
ALLOWED_LAYERS = {"", "overlay2_risk_reversal_same_sign", "overlay3_term_structure_cluster_cap2"}
EXCLUDED_EXCHANGES = {"SSE", "SZSE"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_schedule(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def _filter_dates(frame: pd.DataFrame, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    if "entry_date" not in frame.columns:
        raise ValueError("schedule missing required entry_date column")
    dates = pd.to_datetime(frame["entry_date"], errors="coerce")
    mask = dates.notna()
    if start_date:
        mask &= dates >= pd.Timestamp(start_date)
    if end_date:
        mask &= dates <= pd.Timestamp(end_date)
    return frame.loc[mask].copy()


def _normalize_schedule(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ("entry_date", "target_expiry"):
        if column in out.columns:
            out[column] = pd.to_datetime(out[column], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    text_cols = [
        "product",
        "exchange",
        "contract_code",
        "option_type",
        "entry_reason",
        "strategy_layer",
        "sell_side",
        "side_rule",
        "budget_group",
    ]
    for column in text_cols:
        if column in out.columns:
            out[column] = out[column].fillna("").astype(str)
    if "strategy_layer" in out.columns:
        out["strategy_layer"] = out["strategy_layer"].replace("nan", "")
    if "entry_reason" in out.columns:
        out["entry_reason"] = out["entry_reason"].replace("nan", "")
    if "exchange" in out.columns:
        out["exchange"] = out["exchange"].str.upper()
    if "product" in out.columns:
        out["product"] = out["product"].str.upper()
    return out


def _canonical_sort(frame: pd.DataFrame) -> pd.DataFrame:
    sort_cols = [
        col
        for col in [
            "entry_date",
            "entry_reason",
            "strategy_layer",
            "exchange",
            "product",
            "target_expiry",
            "option_type",
            "contract_code",
        ]
        if col in frame.columns
    ]
    if not sort_cols:
        return frame.reset_index(drop=True)
    return frame.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)


def validate_clean_schedule(frame: pd.DataFrame) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    required = {
        "entry_date",
        "product",
        "exchange",
        "contract_code",
        "option_type",
        "target_expiry",
        "qty",
        "entry_price",
        "premium_cash",
        "target_premium_cash",
        "target_premium_pct",
        "margin_cash",
        "one_lot_margin_cash",
        "entry_reason",
        "strategy_layer",
    }
    missing = sorted(required.difference(frame.columns))
    for column in missing:
        issues.append({"check": "required_column", "level": "fatal", "message": f"missing {column}"})
    if missing:
        return issues

    exchange = frame["exchange"].fillna("").astype(str).str.upper()
    for _, row in frame.loc[exchange.isin(EXCLUDED_EXCHANGES), ["entry_date", "exchange", "product", "contract_code"]].head(100).iterrows():
        issues.append({"check": "no_etf", "level": "fatal", "message": str(row.to_dict())})

    reason = frame["entry_reason"].fillna("").astype(str)
    layer = frame["strategy_layer"].fillna("").astype(str)
    bad_reason = ~reason.isin(ALLOWED_ENTRY_REASONS)
    bad_layer = ~layer.isin(ALLOWED_LAYERS)
    for _, row in frame.loc[bad_reason | bad_layer, ["entry_date", "entry_reason", "strategy_layer", "product", "contract_code"]].head(100).iterrows():
        issues.append({"check": "s1_scope", "level": "fatal", "message": str(row.to_dict())})

    for column in SCHEDULE_KEY:
        if column not in frame.columns:
            frame[column] = ""
    dup = frame[frame.duplicated(SCHEDULE_KEY, keep=False)]
    for _, row in dup[SCHEDULE_KEY].head(100).iterrows():
        issues.append({"check": "duplicate_key", "level": "fatal", "message": str(row.to_dict())})

    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the unified S1 paper external-intent schedule table. "
            "This entrypoint rebuilds the handoff table from an approved clean source schedule; "
            "daily Toolkit factor appenders should write into the same schema before this handoff."
        )
    )
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument(
        "--source-schedule",
        default=None,
        help="Approved source schedule. Defaults to external_signal_path in the paper config.",
    )
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--tag", default="rebuild_signal_schedule")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_effective_config(args.config)
    source_path = resolve_path(args.source_schedule or snapshot.config.get("external_signal_path"))
    if not source_path.exists() and not args.source_schedule and snapshot.config.get("validation_gold_signal_path"):
        gold_path = resolve_path(snapshot.config.get("validation_gold_signal_path"))
        if gold_path.exists():
            source_path = gold_path
    output_path = resolve_path(args.output)
    output_dir = resolve_path(args.output_dir)

    source = _read_schedule(source_path)
    filtered = _filter_dates(source, args.start_date, args.end_date)
    rebuilt = _canonical_sort(_normalize_schedule(filtered))
    issues = validate_clean_schedule(rebuilt)
    write_csv(output_path, rebuilt)

    entry_reason_counts = rebuilt.get("entry_reason", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict()
    layer_counts = rebuilt.get("strategy_layer", pd.Series(dtype=object)).fillna("").astype(str).value_counts().to_dict()
    manifest = {
        "tag": args.tag,
        "mode": "clean_handoff_table_rebuild",
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "source_schedule": str(source_path),
        "source_sha256": sha256_file(source_path),
        "output_schedule": str(output_path),
        "output_sha256": sha256_file(output_path),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source_rows": int(len(source)),
        "output_rows": int(len(rebuilt)),
        "min_entry_date": str(rebuilt["entry_date"].min()) if not rebuilt.empty else "",
        "max_entry_date": str(rebuilt["entry_date"].max()) if not rebuilt.empty else "",
        "entry_reason_counts": entry_reason_counts,
        "strategy_layer_counts": layer_counts,
        "issues": issues,
        "passed": bool(not [issue for issue in issues if issue.get("level") == "fatal"]),
        "future_function_note": (
            "This handoff-table rebuild does not consume research shadow/path/label columns. "
            "Toolkit daily factor appenders must write only point-in-time rows into this schema."
        ),
    }
    audit_dir = output_dir / "audit"
    write_json(audit_dir / f"{args.tag}.json", manifest)
    if issues:
        write_csv(audit_dir / f"{args.tag}_issues.csv", pd.DataFrame(issues))

    print(
        "BUILD_DAILY_SIGNAL_SCHEDULE "
        f"passed={manifest['passed']} rows={manifest['output_rows']} "
        f"source={source_path} output={output_path}"
    )
    print(f"manifest={audit_dir / f'{args.tag}.json'}")
    return 0 if manifest["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
