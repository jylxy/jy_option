"""Diagnostics, output, and comparison helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isfinite(value):
            return float(value)
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def write_csv(path: str | Path, frame: pd.DataFrame) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, encoding="utf-8-sig")


def diagnostics_frame(records: list[dict[str, Any]], signal_date: str | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(records or [])
    if frame.empty or not signal_date or "date" not in frame.columns:
        return frame
    mask = frame["date"].astype(str).str[:10].eq(str(signal_date)[:10])
    return frame.loc[mask].copy()


def _as_key_str(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def normalize_order_frame(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """Normalize generated or historical orders to a comparable shape."""
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=[
                "source",
                "signal_date",
                "execute_date",
                "action",
                "strategy",
                "product",
                "code",
                "option_type",
                "strike_key",
                "expiry",
                "quantity",
                "signal_ref_price",
                "net_premium_cash",
                "margin",
            ]
        )
    work = frame.copy()
    if "execute_date" not in work.columns:
        work["execute_date"] = work.get("date", "")
    if "strategy" not in work.columns and "strat" in work.columns:
        work["strategy"] = work["strat"]
    if "option_type" not in work.columns and "opt_type" in work.columns:
        work["option_type"] = work["opt_type"]
    if "quantity" not in work.columns and "n" in work.columns:
        work["quantity"] = work["n"]
    if "signal_ref_price" not in work.columns and "ref_price" in work.columns:
        work["signal_ref_price"] = work["ref_price"]
    if "action" not in work.columns and "role" in work.columns:
        work["action"] = np.where(work["role"].astype(str).str.lower().eq("sell"), "open_sell", "open_buy")

    out = pd.DataFrame(
        {
            "source": source,
            "signal_date": _as_key_str(work.get("signal_date", pd.Series("", index=work.index))).str[:10],
            "execute_date": _as_key_str(work.get("execute_date", pd.Series("", index=work.index))).str[:10],
            "action": _as_key_str(work.get("action", pd.Series("", index=work.index))),
            "strategy": _as_key_str(work.get("strategy", pd.Series("", index=work.index))),
            "product": _as_key_str(work.get("product", pd.Series("", index=work.index))).str.upper(),
            "code": _as_key_str(work.get("code", pd.Series("", index=work.index))),
            "option_type": _as_key_str(work.get("option_type", pd.Series("", index=work.index))).str.upper(),
            "expiry": _as_key_str(work.get("expiry", pd.Series("", index=work.index))).str[:10],
            "quantity": pd.to_numeric(work.get("quantity", 0), errors="coerce").fillna(0).astype(int),
            "signal_ref_price": pd.to_numeric(work.get("signal_ref_price", np.nan), errors="coerce"),
            "net_premium_cash": pd.to_numeric(work.get("net_premium_cash", np.nan), errors="coerce"),
            "margin": pd.to_numeric(work.get("margin", work.get("open_margin", np.nan)), errors="coerce"),
        }
    )
    strike = pd.to_numeric(work.get("strike", np.nan), errors="coerce")
    out["strike_key"] = strike.round(6).astype(str)
    return out


def compare_order_frames(generated: pd.DataFrame, baseline: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compare final S1 open orders with quantity and basic economics checks."""
    gen = normalize_order_frame(generated, "generated")
    base = normalize_order_frame(baseline, "baseline")
    key_cols = [
        "signal_date",
        "execute_date",
        "action",
        "strategy",
        "product",
        "code",
        "option_type",
        "strike_key",
        "expiry",
    ]
    merged = gen.merge(base, on=key_cols, how="outer", suffixes=("_generated", "_baseline"), indicator=True)
    rows = []
    for _, row in merged.iterrows():
        status = row["_merge"]
        issue = None
        if status == "left_only":
            issue = "extra_in_generated"
        elif status == "right_only":
            issue = "missing_in_generated"
        else:
            q_gen = int(row.get("quantity_generated", 0) or 0)
            q_base = int(row.get("quantity_baseline", 0) or 0)
            if q_gen != q_base:
                issue = "quantity_mismatch"
        if issue:
            item = {col: row.get(col) for col in key_cols}
            item.update(
                {
                    "issue": issue,
                    "quantity_generated": row.get("quantity_generated", np.nan),
                    "quantity_baseline": row.get("quantity_baseline", np.nan),
                    "signal_ref_price_generated": row.get("signal_ref_price_generated", np.nan),
                    "signal_ref_price_baseline": row.get("signal_ref_price_baseline", np.nan),
                    "net_premium_cash_generated": row.get("net_premium_cash_generated", np.nan),
                    "net_premium_cash_baseline": row.get("net_premium_cash_baseline", np.nan),
                    "margin_generated": row.get("margin_generated", np.nan),
                    "margin_baseline": row.get("margin_baseline", np.nan),
                }
            )
            rows.append(item)
    diff = pd.DataFrame(rows)
    summary = {
        "generated_orders": int(len(gen)),
        "baseline_orders": int(len(base)),
        "diff_rows": int(len(diff)),
        "issues": diff["issue"].value_counts().to_dict() if not diff.empty else {},
    }
    return diff, summary

