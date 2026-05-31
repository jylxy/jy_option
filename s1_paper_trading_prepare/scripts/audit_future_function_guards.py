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


FORBIDDEN_COLUMN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|_)label($|_)",
        r"forward",
        r"future_ret",
        r"next_day",
        r"tomorrow",
        r"post_trade_label",
    )
]
OVERLAY23_LAYERS = {
    "overlay2_risk_reversal_same_sign",
    "overlay3_term_structure_cluster_cap2",
}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def _bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(object).where(series.notna(), "").astype(str).str.lower().isin({"true", "1", "yes"})


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype=object)
    return frame[column].fillna("").astype(str)


def _record_failures(
    rows: list[dict[str, Any]],
    frame: pd.DataFrame,
    mask: pd.Series,
    check_name: str,
    message: str,
    columns: list[str] | None = None,
) -> None:
    if not bool(mask.any()):
        return
    key_columns = [
        column
        for column in (
            "entry_date",
            "product",
            "exchange",
            "contract_code",
            "entry_reason",
            "strategy_layer",
        )
        if column in frame.columns
    ]
    detail_columns = [column for column in (columns or []) if column in frame.columns]
    for _, row in frame.loc[mask, key_columns + detail_columns].head(200).iterrows():
        rows.append({
            "check": check_name,
            "message": message,
            **row.to_dict(),
        })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit point-in-time guardrails for the S1 paper signal schedule.")
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--schedule", default=None, help="Schedule path. Defaults to external_signal_path in config.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--tag", default="future_function_guard_audit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_effective_config(args.config)
    schedule_path = resolve_path(args.schedule or snapshot.config.get("external_signal_path"))
    output_dir = resolve_path(args.output_dir)
    schedule = _read_csv(schedule_path)
    failures: list[dict[str, Any]] = []

    layer = _text(schedule, "strategy_layer")
    entry_reason = _text(schedule, "entry_reason")
    exchange = _text(schedule, "exchange").str.upper()

    forbidden_columns = [
        column
        for column in schedule.columns
        if any(pattern.search(column) for pattern in FORBIDDEN_COLUMN_PATTERNS)
        and column not in {"target_expiry"}
    ]
    if forbidden_columns:
        failures.append({
            "check": "forbidden_columns",
            "message": "Schedule contains columns that look like forward labels or future-return fields.",
            "columns": ",".join(forbidden_columns),
        })

    _record_failures(
        failures,
        schedule,
        exchange.isin({"SSE", "SZSE"}),
        "no_etf_exchanges",
        "Paper schedule must exclude SSE/SZSE ETF options.",
        ["target_expiry", "qty", "premium_cash"],
    )

    if "overlay_trigger_trade_date" in schedule.columns:
        trigger_date = pd.to_datetime(schedule["overlay_trigger_trade_date"], errors="coerce")
        entry_date = pd.to_datetime(schedule.get("entry_date"), errors="coerce")
        mask = trigger_date.notna() & entry_date.notna() & trigger_date.gt(entry_date)
        _record_failures(
            failures,
            schedule,
            mask,
            "overlay_trigger_not_after_entry",
            "Overlay trigger date cannot be after the signal entry date.",
            ["overlay_trigger_trade_date"],
        )

    overlay1 = schedule[entry_reason.eq("iv_extreme_overlay") & layer.eq("")].copy()
    if not overlay1.empty:
        cond = (
            _num(overlay1, "overlay_iv_percentile_lag4").ge(0.95)
            & _num(overlay1, "overlay_atm_iv_lag3").lt(_num(overlay1, "overlay_atm_iv_lag4"))
            & _num(overlay1, "overlay_atm_iv_lag2").lt(_num(overlay1, "overlay_atm_iv_lag3"))
            & _num(overlay1, "overlay_atm_iv_lag1").lt(_num(overlay1, "overlay_atm_iv_lag2"))
        )
        _record_failures(
            failures,
            overlay1,
            ~cond,
            "overlay1_lag_only_pullback",
            "Overlay1 must trigger from T-4 through T-1 lagged ATM IV and percentile fields only.",
            [
                "overlay_iv_percentile_lag4",
                "overlay_atm_iv_lag1",
                "overlay_atm_iv_lag2",
                "overlay_atm_iv_lag3",
                "overlay_atm_iv_lag4",
            ],
        )

    overlay2 = schedule[layer.eq("overlay2_risk_reversal_same_sign")].copy()
    if not overlay2.empty:
        rr1 = _num(overlay2, "risk_reversal_lag1")
        rr2 = _num(overlay2, "risk_reversal_lag2")
        rr3 = _num(overlay2, "risk_reversal_lag3")
        cond = (
            _num(overlay2, "overlay_trigger_iv_percentile").ge(0.95)
            & rr3.abs().gt(0)
            & rr2.abs().lt(rr3.abs())
            & rr1.abs().le(rr2.abs())
            & pd.Series(np.sign(rr1), index=overlay2.index).eq(np.sign(rr2))
            & pd.Series(np.sign(rr2), index=overlay2.index).eq(np.sign(rr3))
        )
        _record_failures(
            failures,
            overlay2,
            ~cond,
            "overlay2_lag_only_rr_repair",
            "Overlay2 must trigger from lag3-lag1 risk-reversal repair fields and lag3 percentile.",
            [
                "overlay_trigger_iv_percentile",
                "risk_reversal_lag1",
                "risk_reversal_lag2",
                "risk_reversal_lag3",
            ],
        )

    overlay3 = schedule[layer.eq("overlay3_term_structure_cluster_cap2")].copy()
    if not overlay3.empty:
        trend_conflict = _bool_series(overlay3.get("t1_trend_conflict", pd.Series(False, index=overlay3.index)))
        cond = (
            _num(overlay3, "overlay_trigger_iv_percentile").ge(0.95)
            & _num(overlay3, "term_spread_lag3").gt(0)
            & _num(overlay3, "term_spread_lag2").lt(_num(overlay3, "term_spread_lag3"))
            & _num(overlay3, "term_spread_lag1").le(_num(overlay3, "term_spread_lag2"))
            & _num(overlay3, "term_spread_lag1").gt(0)
            & ~trend_conflict
        )
        _record_failures(
            failures,
            overlay3,
            ~cond,
            "overlay3_lag_only_term_repair",
            "Overlay3 must trigger from lag3-lag1 term-spread repair fields, with T-1 trend-conflict filter.",
            [
                "overlay_trigger_iv_percentile",
                "term_spread_lag1",
                "term_spread_lag2",
                "term_spread_lag3",
                "t1_trend_conflict",
                "t1_side_skip_reason",
            ],
        )

    audit = {
        "tag": args.tag,
        "config_path": str(snapshot.path),
        "config_sha256": snapshot.sha256,
        "schedule_path": str(schedule_path),
        "rows": int(len(schedule)),
        "forbidden_columns": forbidden_columns,
        "etf_rows": int(exchange.isin({"SSE", "SZSE"}).sum()),
        "monthly_rows": int(entry_reason.eq("monthly").sum()),
        "overlay1_rows": int(len(overlay1)),
        "overlay2_rows": int(len(overlay2)),
        "overlay3_rows": int(len(overlay3)),
        "failure_rows_sampled": int(len(failures)),
        "passed": bool(not failures),
        "scope_note": (
            "This audit verifies schedule-level point-in-time guardrails. "
            "A regenerated full schedule must also pass strict gold parity and a full minute replay."
        ),
    }

    audit_dir = output_dir / "audit"
    write_json(audit_dir / f"{args.tag}.json", audit)
    if failures:
        write_csv(audit_dir / f"{args.tag}_failures.csv", pd.DataFrame(failures))

    print(
        "FUTURE_FUNCTION_GUARD_AUDIT "
        f"passed={audit['passed']} rows={audit['rows']} failures={audit['failure_rows_sampled']} "
        f"etf_rows={audit['etf_rows']} overlay1={audit['overlay1_rows']} "
        f"overlay2={audit['overlay2_rows']} overlay3={audit['overlay3_rows']}"
    )
    print(f"audit={audit_dir / f'{args.tag}.json'}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
