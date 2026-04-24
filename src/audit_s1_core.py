"""
Core audit for S1 short-premium backtest outputs.

The audit is intentionally read-only. It matches open/close orders into FIFO
lots, rebuilds daily marks with ToolkitDayLoader, recomputes IV/Greeks, and
summarizes whether S1 actually earns theta and vega carry.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from option_calc import calc_greeks_single, calc_iv_single
from toolkit_minute_engine import ContractInfo, ToolkitDayLoader


CLOSE_ACTION_PREFIXES = ("sl_", "tp_", "pre_expiry_roll", "expiry", "greeks_", "s4_")


@dataclass
class Lot:
    product: str
    code: str
    option_type: str
    strike: float
    expiry: str
    open_date: str
    open_price: float
    quantity: int
    close_date: str = ""
    close_price: float = np.nan
    close_reason: str = "open_end"
    open_fee: float = 0.0
    close_fee: float = 0.0
    open_time: str = ""
    close_time: str = ""


def _date_key(row: pd.Series) -> tuple:
    return (str(row.get("date", "")), str(row.get("time", "")), str(row.get("action", "")))


def _is_close_action(action: str) -> bool:
    action = str(action)
    return any(action.startswith(prefix) for prefix in CLOSE_ACTION_PREFIXES)


def _as_float(value, default=np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def match_lots(orders: pd.DataFrame) -> List[Lot]:
    orders = orders.copy()
    orders["_sort_key"] = orders.apply(_date_key, axis=1)
    orders = orders.sort_values("_sort_key", kind="mergesort")

    queues: Dict[str, deque[Lot]] = defaultdict(deque)
    lots: List[Lot] = []

    for _, row in orders.iterrows():
        action = str(row.get("action", ""))
        if str(row.get("strategy", "")) != "S1":
            continue
        code = str(row.get("code", ""))
        qty = _as_int(row.get("quantity"), 0)
        if not code or qty <= 0:
            continue

        if action == "open_sell":
            lot = Lot(
                product=str(row.get("product", "")),
                code=code,
                option_type=str(row.get("option_type", "")),
                strike=_as_float(row.get("strike")),
                expiry=str(row.get("expiry", ""))[:10],
                open_date=str(row.get("date", ""))[:10],
                open_price=_as_float(row.get("price")),
                quantity=qty,
                open_fee=_as_float(row.get("fee"), 0.0),
                open_time=str(row.get("time", "")),
            )
            queues[code].append(lot)
            continue

        if not _is_close_action(action):
            continue

        remaining = qty
        while remaining > 0 and queues[code]:
            open_lot = queues[code][0]
            close_qty = min(remaining, open_lot.quantity)
            matched = Lot(
                product=open_lot.product,
                code=open_lot.code,
                option_type=open_lot.option_type,
                strike=open_lot.strike,
                expiry=open_lot.expiry,
                open_date=open_lot.open_date,
                open_price=open_lot.open_price,
                quantity=close_qty,
                close_date=str(row.get("date", ""))[:10],
                close_price=_as_float(row.get("price")),
                close_reason=action,
                open_fee=open_lot.open_fee * close_qty / max(open_lot.quantity, 1),
                close_fee=_as_float(row.get("fee"), 0.0) * close_qty / max(qty, 1),
                open_time=open_lot.open_time,
                close_time=str(row.get("time", "")),
            )
            lots.append(matched)
            open_lot.quantity -= close_qty
            remaining -= close_qty
            if open_lot.quantity <= 0:
                queues[code].popleft()

    for queue in queues.values():
        lots.extend(list(queue))
    return lots


def _build_like_sql(codes: Iterable[str]) -> str:
    code_sql = ", ".join(f"'{str(code)}'" for code in sorted({c for c in codes if c}))
    return f"ths_code IN ({code_sql})" if code_sql else ""


def load_daily_maps(loader: ToolkitDayLoader, ci: ContractInfo, dates: List[str], codes: List[str]) -> Dict[str, Dict[str, pd.Series]]:
    like_sql = _build_like_sql(codes)
    if like_sql:
        loader.preload_daily_agg_batch(dates, like_sql, ci)

    daily_maps: Dict[str, Dict[str, pd.Series]] = {}
    for date_str in dates:
        df = loader.get_daily_agg(date_str, ci)
        if df is None or df.empty:
            daily_maps[date_str] = {}
            continue
        daily_maps[date_str] = {
            str(row.option_code): row
            for row in df.itertuples(index=False)
        }
    return daily_maps


def _mark(row: Optional[pd.Series], code: str, price: float, ci: ContractInfo) -> Optional[dict]:
    info = ci.lookup(code) or {}
    if row is None:
        return None

    spot = _as_float(getattr(row, "spot_close", np.nan))
    strike = _as_float(getattr(row, "strike", info.get("strike", np.nan)))
    dte = _as_float(getattr(row, "dte", np.nan))
    option_type = str(getattr(row, "option_type", info.get("option_type", "")))
    exchange = str(getattr(row, "exchange", info.get("exchange", "")))
    mult = _as_float(getattr(row, "multiplier", info.get("multiplier", 1.0)), 1.0)

    if not np.isfinite(price) or price <= 0:
        price = _as_float(getattr(row, "option_close", np.nan))
    if not all(np.isfinite(x) and x > 0 for x in [spot, strike, dte, price]):
        return None

    iv = calc_iv_single(price, spot, strike, dte, option_type, exchange=exchange)
    greeks = calc_greeks_single(spot, strike, dte, iv, option_type, exchange=exchange)
    return {
        "price": float(price),
        "spot": float(spot),
        "strike": float(strike),
        "dte": float(dte),
        "option_type": option_type,
        "exchange": exchange,
        "mult": float(mult),
        "iv": float(iv) if np.isfinite(iv) else np.nan,
        "delta": float(greeks.get("delta", np.nan)),
        "gamma": float(greeks.get("gamma", np.nan)),
        "vega": float(greeks.get("vega", np.nan)),
        "theta": float(greeks.get("theta", np.nan)),
    }


def _step_attr(prev: dict, cur: dict, qty: int) -> dict:
    mult = prev["mult"]
    ds = cur["spot"] - prev["spot"]
    d_iv = cur["iv"] - prev["iv"] if np.isfinite(cur["iv"]) and np.isfinite(prev["iv"]) else np.nan
    gross_pnl = (prev["price"] - cur["price"]) * mult * qty
    delta_pnl = -prev["delta"] * ds * mult * qty if np.isfinite(prev["delta"]) else 0.0
    gamma_pnl = -0.5 * prev["gamma"] * ds * ds * mult * qty if np.isfinite(prev["gamma"]) else 0.0
    theta_pnl = -prev["theta"] * mult * qty if np.isfinite(prev["theta"]) else 0.0
    vega_pnl = -prev["vega"] * (d_iv / 0.01) * mult * qty if np.isfinite(prev["vega"]) and np.isfinite(d_iv) else 0.0
    residual = gross_pnl - delta_pnl - gamma_pnl - theta_pnl - vega_pnl
    return {
        "gross_pnl": gross_pnl,
        "delta_pnl": delta_pnl,
        "gamma_pnl": gamma_pnl,
        "theta_pnl": theta_pnl,
        "vega_pnl": vega_pnl,
        "residual_pnl": residual,
        "d_iv": d_iv,
        "d_spot": ds,
    }


def audit_lot(lot: Lot, trading_dates: List[str], daily_maps: Dict[str, Dict[str, pd.Series]], ci: ContractInfo, end_date: str) -> dict:
    close_date = lot.close_date or end_date
    if close_date not in trading_dates:
        close_date = max([d for d in trading_dates if d <= close_date], default=trading_dates[-1])
    path_dates = [d for d in trading_dates if lot.open_date <= d <= close_date]
    if not path_dates:
        return {"code": lot.code, "audit_status": "no_dates"}

    first_row = daily_maps.get(lot.open_date, {}).get(lot.code)
    entry = _mark(first_row, lot.code, lot.open_price, ci)
    if entry is None:
        return {"code": lot.code, "audit_status": "missing_entry_mark"}

    prev = entry
    sums = defaultdict(float)
    first_day_residual = 0.0
    missing_marks = 0

    for date_str in path_dates:
        row = daily_maps.get(date_str, {}).get(lot.code)
        if row is None:
            missing_marks += 1
            continue
        price = lot.close_price if date_str == close_date and np.isfinite(lot.close_price) else _as_float(getattr(row, "option_close", np.nan))
        cur = _mark(row, lot.code, price, ci)
        if cur is None:
            missing_marks += 1
            continue
        attr = _step_attr(prev, cur, lot.quantity)
        for key, value in attr.items():
            if key != "d_iv" and key != "d_spot":
                sums[key] += float(value)
        if date_str == lot.open_date:
            first_day_residual += attr["residual_pnl"]
        prev = cur

    exit_row = daily_maps.get(close_date, {}).get(lot.code)
    exit_mark = _mark(exit_row, lot.code, lot.close_price if np.isfinite(lot.close_price) else np.nan, ci) if exit_row is not None else None
    if exit_mark is None:
        exit_mark = prev

    net_pnl = sums["gross_pnl"] - lot.open_fee - lot.close_fee
    entry_iv = entry.get("iv", np.nan)
    exit_iv = exit_mark.get("iv", np.nan)
    d_iv = exit_iv - entry_iv if np.isfinite(entry_iv) and np.isfinite(exit_iv) else np.nan
    gross_premium = lot.open_price * entry["mult"] * lot.quantity

    return {
        "audit_status": "ok",
        "product": lot.product,
        "code": lot.code,
        "option_type": lot.option_type,
        "strike": lot.strike,
        "expiry": lot.expiry,
        "open_date": lot.open_date,
        "close_date": close_date,
        "close_reason": lot.close_reason,
        "same_day_close": lot.open_date == close_date,
        "quantity": lot.quantity,
        "open_price": lot.open_price,
        "close_price": lot.close_price,
        "entry_spot": entry.get("spot", np.nan),
        "exit_spot": exit_mark.get("spot", np.nan),
        "entry_dte": entry.get("dte", np.nan),
        "exit_dte": exit_mark.get("dte", np.nan),
        "entry_iv": entry_iv,
        "exit_iv": exit_iv,
        "iv_change": d_iv,
        "entry_delta": entry.get("delta", np.nan),
        "entry_vega": entry.get("vega", np.nan),
        "entry_theta": entry.get("theta", np.nan),
        "gross_premium": gross_premium,
        "gross_pnl": sums["gross_pnl"],
        "fee": lot.open_fee + lot.close_fee,
        "net_pnl": net_pnl,
        "delta_pnl": sums["delta_pnl"],
        "gamma_pnl": sums["gamma_pnl"],
        "theta_pnl": sums["theta_pnl"],
        "vega_pnl": sums["vega_pnl"],
        "residual_pnl": sums["residual_pnl"],
        "first_day_residual": first_day_residual,
        "missing_marks": missing_marks,
        "n_path_days": len(path_dates),
    }


def summarize(df: pd.DataFrame) -> List[str]:
    ok = df[df["audit_status"] == "ok"].copy()
    lines = ["# S1 Core Audit", ""]
    lines.append(f"- audited_lots: {len(ok)} / {len(df)}")
    if ok.empty:
        return lines
    lines.append(f"- net_pnl: {ok['net_pnl'].sum():.2f}")
    for col in ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_pnl", "residual_pnl", "first_day_residual"]:
        lines.append(f"- {col}: {ok[col].sum():.2f}")
    lines.append(f"- avg_entry_iv: {ok['entry_iv'].mean():.4f}")
    lines.append(f"- avg_exit_iv: {ok['exit_iv'].mean():.4f}")
    lines.append(f"- avg_iv_change: {ok['iv_change'].mean():.4f}")
    lines.append(f"- iv_up_lots: {int((ok['iv_change'] > 0).sum())}")
    lines.append(f"- iv_down_lots: {int((ok['iv_change'] < 0).sum())}")
    lines.append(f"- same_day_close_lots: {int(ok['same_day_close'].sum())}")
    lines.append("")

    lines.append("## By Close Reason")
    reason = ok.groupby("close_reason", dropna=False).agg(
        lots=("code", "count"),
        net_pnl=("net_pnl", "sum"),
        theta_pnl=("theta_pnl", "sum"),
        vega_pnl=("vega_pnl", "sum"),
        residual_pnl=("residual_pnl", "sum"),
        avg_iv_change=("iv_change", "mean"),
        same_day_close=("same_day_close", "sum"),
    ).reset_index()
    lines.extend(_markdown_table(reason))
    lines.append("")

    lines.append("## By Product")
    product = ok.groupby("product", dropna=False).agg(
        lots=("code", "count"),
        net_pnl=("net_pnl", "sum"),
        theta_pnl=("theta_pnl", "sum"),
        vega_pnl=("vega_pnl", "sum"),
        residual_pnl=("residual_pnl", "sum"),
        avg_iv_change=("iv_change", "mean"),
    ).sort_values("net_pnl").reset_index()
    lines.extend(_markdown_table(product))
    lines.append("")

    lines.append("## Worst Lots")
    worst = ok.sort_values("net_pnl").head(20)[[
        "product", "code", "option_type", "open_date", "close_date", "close_reason",
        "quantity", "entry_iv", "exit_iv", "iv_change", "net_pnl",
        "theta_pnl", "vega_pnl", "residual_pnl", "same_day_close",
    ]]
    lines.extend(_markdown_table(worst))
    lines.append("")
    return lines


def _markdown_table(df: pd.DataFrame) -> List[str]:
    if df.empty:
        return ["(empty)"]
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in cols:
            value = row[col]
            if isinstance(value, float):
                values.append(f"{value:.6g}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", required=True)
    parser.add_argument("--tag", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--output-dir", default="/macro/home/lxy/jy_option/output/core_audit")
    args = parser.parse_args()

    orders_path = Path(args.orders)
    orders = pd.read_csv(orders_path)
    lots = match_lots(orders)
    if not lots:
        raise SystemExit("no S1 lots matched")

    min_date = min(lot.open_date for lot in lots if lot.open_date)
    explicit_close_dates = [lot.close_date for lot in lots if lot.close_date]
    max_date = args.end_date or (max(explicit_close_dates) if explicit_close_dates else max(lot.open_date for lot in lots))

    ci = ContractInfo()
    ci.load()
    loader = ToolkitDayLoader(ci)
    trading_dates = loader.get_trading_dates(min_date, max_date)
    if not trading_dates:
        raise SystemExit("no trading dates")
    codes = sorted({lot.code for lot in lots})
    daily_maps = load_daily_maps(loader, ci, trading_dates, codes)

    rows = [
        audit_lot(lot, trading_dates, daily_maps, ci, max_date)
        for lot in lots
    ]
    result = pd.DataFrame(rows)

    tag = args.tag or orders_path.stem.replace("orders_", "")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"core_audit_{tag}.csv"
    md_path = output_dir / f"core_audit_{tag}.md"
    result.to_csv(csv_path, index=False)
    md_path.write_text("\n".join(summarize(result)), encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
