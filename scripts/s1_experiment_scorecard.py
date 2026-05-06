#!/usr/bin/env python3
"""Score S1 autoresearch experiments.

The script is intentionally ASCII-only. It reads backtest outputs, computes a
stable scorecard, optionally compares a baseline, and appends a TSV row to the
autoresearch result ledger.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


TRADING_DAYS = 252
ATTR_COLS = ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_pnl", "residual_pnl"]
LEDGER_HEADER = [
    "timestamp",
    "experiment_id",
    "tag",
    "status",
    "formula_variable",
    "sample_role",
    "start_date",
    "end_date",
    "total_return",
    "annual_return",
    "max_drawdown",
    "sharpe",
    "calmar",
    "vega_pnl",
    "theta_pnl",
    "gamma_pnl",
    "delta_pnl",
    "residual_pnl",
    "stop_count",
    "stop_pnl",
    "avg_margin_ratio",
    "max_margin_ratio",
    "excess_return_vs_baseline",
    "excess_mdd_vs_baseline",
    "decision",
    "notes",
]


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def output_paths(output_dir: Path, tag: str) -> Dict[str, Path]:
    return {
        "nav": output_dir / f"nav_{tag}.csv",
        "orders": output_dir / f"orders_{tag}.csv",
        "diagnostics": output_dir / f"diagnostics_{tag}.csv",
    }


def max_drawdown(nav: pd.Series) -> float:
    nav = pd.to_numeric(nav, errors="coerce").dropna()
    if nav.empty:
        return float("nan")
    return float((nav / nav.cummax() - 1.0).min())


def annualized_return(total_return: float, rows: int) -> float:
    if rows <= 1 or not np.isfinite(total_return) or total_return <= -1:
        return float("nan")
    return float((1.0 + total_return) ** (TRADING_DAYS / max(rows - 1, 1)) - 1.0)


def sharpe_ratio(nav: pd.Series) -> float:
    ret = pd.to_numeric(nav, errors="coerce").pct_change().dropna()
    if ret.empty:
        return float("nan")
    std = float(ret.std(ddof=1))
    if std <= 0 or not np.isfinite(std):
        return float("nan")
    return float(ret.mean() / std * math.sqrt(TRADING_DAYS))


def numeric_series(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def load_metrics(tag: str, output_dir: Path) -> Dict[str, object]:
    paths = output_paths(output_dir, tag)
    nav_df = read_csv_if_exists(paths["nav"])
    orders_df = read_csv_if_exists(paths["orders"])
    if nav_df.empty:
        raise FileNotFoundError(f"missing or empty NAV for tag={tag}: {paths['nav']}")
    if "nav" not in nav_df.columns:
        raise ValueError(f"NAV file has no nav column: {paths['nav']}")

    nav = pd.to_numeric(nav_df["nav"], errors="coerce")
    start_nav = float(nav.iloc[0])
    end_nav = float(nav.iloc[-1])
    total_ret = end_nav / start_nav - 1.0 if start_nav else float("nan")
    ann_ret = annualized_return(total_ret, len(nav_df))
    mdd = max_drawdown(nav)
    sharpe = sharpe_ratio(nav)
    calmar = ann_ret / abs(mdd) if np.isfinite(ann_ret) and np.isfinite(mdd) and mdd < 0 else float("nan")

    cum_pnl = numeric_series(nav_df, "cum_pnl", 0.0)
    if "cum_pnl" not in nav_df.columns:
        cum_pnl = nav - start_nav
    day_pnl = cum_pnl.diff().fillna(cum_pnl)
    worst_day_pnl = float(day_pnl.min()) if len(day_pnl) else float("nan")
    worst_day_ret = float(nav.pct_change().min()) if len(nav) else float("nan")

    margin = numeric_series(nav_df, "margin_used", float("nan"))
    margin_ratio = margin / nav.replace(0, np.nan)

    attr = {}
    for col in ATTR_COLS:
        attr[col] = float(numeric_series(nav_df, col, 0.0).fillna(0.0).sum())

    stop_count = 0
    stop_pnl = 0.0
    gross_open_premium = 0.0
    net_open_premium = 0.0
    close_premium_retained = 0.0
    close_open_premium = 0.0
    if not orders_df.empty:
        action = orders_df.get("action", pd.Series("", index=orders_df.index)).astype(str)
        stop_mask = action.str.startswith("sl_")
        stop_count = int(stop_mask.sum())
        stop_pnl = float(pd.to_numeric(orders_df.loc[stop_mask, "pnl"], errors="coerce").fillna(0.0).sum()) if "pnl" in orders_df else 0.0
        open_mask = action.str.startswith("open")
        if "gross_premium_cash" in orders_df:
            gross_open_premium = float(pd.to_numeric(orders_df.loc[open_mask, "gross_premium_cash"], errors="coerce").fillna(0.0).sum())
        if "net_premium_cash" in orders_df:
            net_open_premium = float(pd.to_numeric(orders_df.loc[open_mask, "net_premium_cash"], errors="coerce").fillna(0.0).sum())
        close_mask = ~open_mask
        if "premium_retained_cash" in orders_df:
            close_premium_retained = float(pd.to_numeric(orders_df.loc[close_mask, "premium_retained_cash"], errors="coerce").fillna(0.0).sum())
        if "open_premium_cash" in orders_df:
            close_open_premium = float(pd.to_numeric(orders_df.loc[close_mask, "open_premium_cash"], errors="coerce").fillna(0.0).sum())

    premium_retention = (
        close_premium_retained / close_open_premium
        if close_open_premium > 0
        else float("nan")
    )
    fee_sum = float(numeric_series(nav_df, "fee", 0.0).fillna(0.0).sum())

    return {
        "tag": tag,
        "start_date": str(nav_df["date"].iloc[0]) if "date" in nav_df else "",
        "end_date": str(nav_df["date"].iloc[-1]) if "date" in nav_df else "",
        "rows": int(len(nav_df)),
        "nav_start": start_nav,
        "nav_end": end_nav,
        "total_return": total_ret,
        "annual_return": ann_ret,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "worst_day_pnl": worst_day_pnl,
        "worst_day_return": worst_day_ret,
        "avg_margin_ratio": float(margin_ratio.mean()) if margin_ratio.notna().any() else float("nan"),
        "max_margin_ratio": float(margin_ratio.max()) if margin_ratio.notna().any() else float("nan"),
        "last_margin_ratio": float(margin_ratio.iloc[-1]) if margin_ratio.notna().any() else float("nan"),
        "stop_count": stop_count,
        "stop_pnl": stop_pnl,
        "gross_open_premium": gross_open_premium,
        "net_open_premium": net_open_premium,
        "premium_retention": premium_retention,
        "fee_sum": fee_sum,
        **attr,
    }


def compare_to_baseline(metrics: Dict[str, object], baseline: Optional[Dict[str, object]]) -> Dict[str, object]:
    if not baseline:
        return {
            "excess_return_vs_baseline": float("nan"),
            "excess_mdd_vs_baseline": float("nan"),
            "baseline_tag": "",
        }
    return {
        "excess_return_vs_baseline": float(metrics["total_return"]) - float(baseline["total_return"]),
        "excess_mdd_vs_baseline": float(metrics["max_drawdown"]) - float(baseline["max_drawdown"]),
        "baseline_tag": baseline["tag"],
    }


def decision(metrics: Dict[str, object], audit_status: str = "") -> str:
    ann = float(metrics.get("annual_return", float("nan")))
    mdd = float(metrics.get("max_drawdown", float("nan")))
    vega = float(metrics.get("vega_pnl", float("nan")))
    if audit_status in {"critical", "needs_code_fix"}:
        return "needs_rerun"
    if np.isfinite(ann) and np.isfinite(mdd) and np.isfinite(vega):
        if ann >= 0.06 and abs(mdd) <= 0.02 and vega >= 0:
            return "keep_candidate"
    if np.isfinite(mdd) and abs(mdd) > 0.05:
        return "diagnostic_only"
    return "review_required"


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_ledger(path: Path, row: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_HEADER, delimiter="\t", extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in LEDGER_HEADER})


def format_float(value: object) -> str:
    try:
        v = float(value)
    except Exception:
        return ""
    if not np.isfinite(v):
        return ""
    return f"{v:.10g}"


def build_row(
    metrics: Dict[str, object],
    extra: Dict[str, object],
    experiment_id: str,
    formula_variable: str,
    sample_role: str,
    status: str,
    notes: str,
) -> Dict[str, object]:
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "experiment_id": experiment_id,
        "tag": metrics["tag"],
        "status": status,
        "formula_variable": formula_variable,
        "sample_role": sample_role,
        "start_date": metrics["start_date"],
        "end_date": metrics["end_date"],
        "decision": decision(metrics),
        "notes": notes,
    }
    for key in [
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "calmar",
        "vega_pnl",
        "theta_pnl",
        "gamma_pnl",
        "delta_pnl",
        "residual_pnl",
        "stop_count",
        "stop_pnl",
        "avg_margin_ratio",
        "max_margin_ratio",
    ]:
        row[key] = format_float(metrics.get(key))
    for key in ["excess_return_vs_baseline", "excess_mdd_vs_baseline"]:
        row[key] = format_float(extra.get(key))
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score S1 autoresearch experiments.")
    parser.add_argument("--tag", required=True, help="Candidate backtest tag.")
    parser.add_argument("--baseline-tag", default="", help="Optional baseline tag.")
    parser.add_argument("--output-dir", default="output", help="Backtest output directory.")
    parser.add_argument("--results-dir", default="experiments/s1_autoresearch", help="Autoresearch result directory.")
    parser.add_argument("--experiment-id", default="", help="Experiment id for ledger.")
    parser.add_argument("--formula-variable", default="", help="Formula variable improved by this experiment.")
    parser.add_argument("--sample-role", default="", help="sample/validation/oos/stress.")
    parser.add_argument("--status", default="scored", help="Lifecycle status.")
    parser.add_argument("--notes", default="", help="Ledger notes.")
    parser.add_argument("--write-ledger", action="store_true", help="Append results.tsv.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    results_dir = Path(args.results_dir)
    metrics = load_metrics(args.tag, output_dir)
    baseline = load_metrics(args.baseline_tag, output_dir) if args.baseline_tag else None
    extra = compare_to_baseline(metrics, baseline)
    payload = {"metrics": metrics, "baseline": baseline, "comparison": extra}

    score_path = results_dir / f"scorecard_{args.tag}.json"
    write_json(score_path, payload)

    row = build_row(
        metrics,
        extra,
        experiment_id=args.experiment_id or args.tag,
        formula_variable=args.formula_variable,
        sample_role=args.sample_role,
        status=args.status,
        notes=args.notes,
    )
    if args.write_ledger:
        append_ledger(results_dir / "results.tsv", row)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
