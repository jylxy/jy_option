"""Daily close marking for the current S1 paper account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .account_state import DEFAULT_STATE_DIR, expected_account_state_files
from .config_snapshot import load_effective_config
from .data_loader import load_signal_day_snapshot
from .diagnostics import write_csv, write_json
from .paths import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, ensure_server_deploy_importable, resolve_path


@dataclass(frozen=True)
class DailyCloseMarkResult:
    as_of_date: str
    nav: float
    daily_pnl: float
    daily_return: float
    position_rows: int
    marked_rows: int
    stale_rows: int
    missing_rows: int
    margin_used: float
    gross_option_market_value: float
    positions_path: Path | None
    summary_path: Path | None
    snapshot_path: Path | None
    state_nav_path: Path | None
    state_positions_path: Path | None
    issues: list[dict[str, Any]]


def _date_tag(date: str) -> str:
    return str(date)[:10].replace("-", "")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _safe_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def _signed_quantity(row: pd.Series) -> float:
    qty = _safe_float(row.get("quantity", 0.0), 0.0)
    side = str(row.get("position_side", row.get("role", "")) or "").lower()
    if side in {"short", "sell", "sold"} and qty > 0:
        return -qty
    if side in {"long", "buy", "bought"} and qty < 0:
        return abs(qty)
    return qty


def _snapshot_path(data_dir: Path, date: str) -> Path:
    return data_dir / "daily_snapshots" / f"option_chain_{_date_tag(date)}.csv"


def _load_or_fetch_snapshot(
    date: str,
    *,
    data_dir: Path,
    config_path: Path,
    products: tuple[str, ...] | None,
    product_chunk_size: int,
) -> tuple[pd.DataFrame, Path | None]:
    path = _snapshot_path(data_dir, date)
    if path.exists():
        return _read_csv(path), path
    frame = load_signal_day_snapshot(
        date,
        config_path=config_path,
        products=products,
        product_chunk_size=product_chunk_size,
    )
    return frame, None


def _option_mark_lookup(snapshot: pd.DataFrame) -> dict[str, pd.Series]:
    if snapshot.empty:
        return {}
    work = snapshot.copy()
    code_col = "option_code" if "option_code" in work.columns else "ths_code" if "ths_code" in work.columns else ""
    if not code_col or "option_close" not in work.columns:
        return {}
    work["option_close"] = pd.to_numeric(work["option_close"], errors="coerce")
    work = work[work[code_col].notna() & work[code_col].astype(str).ne("")]
    work = work.drop_duplicates(code_col, keep="last")
    return {str(row[code_col]): row for _, row in work.iterrows()}


def _estimate_short_margin(
    *,
    row: pd.Series,
    mark_row: pd.Series | None,
    mark_price: float,
    abs_qty: float,
    config: dict[str, Any],
    ci: Any,
    estimate_margin_func: Any,
) -> float:
    if abs_qty <= 0 or mark_price < 0:
        return 0.0
    code = str(row.get("code", "") or "")
    info = ci.lookup(code) or {}
    product = str(row.get("product", info.get("product_root", "")) or "").upper()
    exchange = str(row.get("exchange", info.get("exchange", "")) or info.get("exchange", "")).upper()
    underlying_code = str(row.get("underlying_code", info.get("underlying_code", "")) or "")
    option_type = str(row.get("option_type", info.get("option_type", "")) or "").upper()[:1]
    strike = _safe_float(row.get("strike", info.get("strike", np.nan)), np.nan)
    multiplier = _safe_float(row.get("multiplier", info.get("multiplier", np.nan)), np.nan)
    if not np.isfinite(multiplier) or multiplier <= 0:
        multiplier = 1.0
    spot = np.nan
    if mark_row is not None:
        spot = _safe_float(mark_row.get("spot_close", np.nan), np.nan)
        if not underlying_code:
            underlying_code = str(mark_row.get("underlying_code", "") or "")
        if not exchange:
            exchange = str(mark_row.get("exchange", "") or "")
        if not product:
            product = str(mark_row.get("product", "") or "").upper()
    if not np.isfinite(spot) or spot <= 0:
        return _safe_float(row.get("margin", 0.0), 0.0)
    margin_ratio = ci.get_margin_ratio(
        exchange=exchange,
        product=product,
        underlying_code=underlying_code,
        config=config,
    )
    if not np.isfinite(strike) or strike <= 0 or option_type not in {"P", "C"}:
        return _safe_float(row.get("margin", 0.0), 0.0)
    return float(estimate_margin_func(
        spot,
        strike,
        option_type,
        mark_price,
        multiplier,
        margin_ratio,
        0.5,
        exchange=exchange,
        product=product,
    )) * abs_qty


def mark_account_to_close(
    as_of_date: str,
    *,
    state_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 32,
    write_outputs: bool = True,
    write_state: bool = False,
) -> DailyCloseMarkResult:
    date = str(as_of_date)[:10]
    config_snapshot = load_effective_config(config_path)
    config = config_snapshot.config
    state_root = resolve_path(state_dir, default=DEFAULT_STATE_DIR)
    data_root = resolve_path(data_dir, default=DEFAULT_DATA_DIR)
    out_root = resolve_path(output_dir, default=DEFAULT_OUTPUT_DIR) / "audit"
    files = expected_account_state_files(state_root, date)
    nav_frame = _read_csv(files["nav_state"])
    positions = _read_csv(files["positions"])
    issues: list[dict[str, Any]] = []
    if nav_frame.empty or "nav" not in nav_frame.columns:
        raise ValueError(f"missing NAV state for {date}: {files['nav_state']}")
    nav_values = pd.to_numeric(nav_frame["nav"], errors="coerce").dropna()
    nav = _safe_float(nav_values.iloc[-1] if not nav_values.empty else np.nan, np.nan)
    if not np.isfinite(nav) or nav <= 0:
        raise ValueError(f"invalid NAV state for {date}: {files['nav_state']}")

    active_positions = positions.copy()
    if not active_positions.empty and "quantity" in active_positions.columns:
        signed_qty = active_positions.apply(_signed_quantity, axis=1)
        active_positions = active_positions.loc[signed_qty.ne(0)].copy()
    active_products = tuple(sorted({
        str(x).upper()
        for x in active_positions.get("product", pd.Series(dtype=object)).dropna().tolist()
        if str(x).strip()
    }))
    if products:
        active_products = products
    if active_positions.empty:
        snapshot = pd.DataFrame()
        snapshot_file = _snapshot_path(data_root, date) if _snapshot_path(data_root, date).exists() else None
    else:
        snapshot, snapshot_file = _load_or_fetch_snapshot(
            date,
            data_dir=data_root,
            config_path=config_snapshot.path,
            products=active_products or None,
            product_chunk_size=product_chunk_size,
        )
    mark_lookup = _option_mark_lookup(snapshot)

    ci = None
    estimate_margin = None
    if not active_positions.empty:
        ensure_server_deploy_importable()
        from contract_provider import ContractInfo  # type: ignore
        from margin_model import estimate_margin as estimate_margin_func  # type: ignore

        ci = ContractInfo()
        ci.load()
        estimate_margin = estimate_margin_func

    rows: list[dict[str, Any]] = []
    daily_pnl = 0.0
    gross_option_market_value = 0.0
    margin_used = 0.0

    for _, row in active_positions.iterrows():
        code = str(row.get("code", "") or "")
        signed_qty = _signed_quantity(row)
        abs_qty = abs(signed_qty)
        prev_mark = _safe_float(row.get("mark_price", np.nan), np.nan)
        avg_price = _safe_float(row.get("avg_price", prev_mark), prev_mark)
        multiplier = _safe_float(row.get("multiplier", np.nan), np.nan)
        info = ci.lookup(code) if ci is not None else {}
        info = info or {}
        if not np.isfinite(multiplier) or multiplier <= 0:
            multiplier = _safe_float(info.get("multiplier", 1.0), 1.0)
        mark_row = mark_lookup.get(code)
        fresh_mark = np.nan
        mark_status = "missing_option_close"
        if mark_row is not None:
            fresh_mark = _safe_float(mark_row.get("option_close", np.nan), np.nan)
            if np.isfinite(fresh_mark) and fresh_mark > 0:
                mark_status = "fresh"
        if mark_status != "fresh":
            if np.isfinite(prev_mark) and prev_mark >= 0:
                mark_price = prev_mark
                mark_status = "stale_previous_mark"
            else:
                mark_price = np.nan
                issues.append({"issue": "missing_mark_without_previous", "code": code})
        else:
            mark_price = fresh_mark

        position_daily_pnl = (
            signed_qty * (mark_price - prev_mark) * multiplier
            if np.isfinite(mark_price) and np.isfinite(prev_mark)
            else np.nan
        )
        if np.isfinite(position_daily_pnl):
            daily_pnl += float(position_daily_pnl)
        market_value = mark_price * multiplier * abs_qty if np.isfinite(mark_price) else np.nan
        if np.isfinite(market_value):
            gross_option_market_value += float(market_value)
        unrealized_from_avg = (
            signed_qty * (mark_price - avg_price) * multiplier
            if np.isfinite(mark_price) and np.isfinite(avg_price)
            else np.nan
        )
        margin = 0.0
        if signed_qty < 0 and np.isfinite(mark_price):
            margin = _estimate_short_margin(
                row=row,
                mark_row=mark_row,
                mark_price=mark_price,
                abs_qty=abs_qty,
                config=config,
                ci=ci,
                estimate_margin_func=estimate_margin,
            )
            margin_used += float(margin)

        rows.append({
            **row.to_dict(),
            "as_of_date": date,
            "signed_quantity": signed_qty,
            "abs_quantity": abs_qty,
            "previous_mark_price": prev_mark,
            "mark_price": mark_price,
            "mark_status": mark_status,
            "daily_pnl": position_daily_pnl,
            "unrealized_pnl_from_avg": unrealized_from_avg,
            "gross_option_market_value": market_value,
            "margin": margin,
        })

    added_columns = [
        "signed_quantity",
        "abs_quantity",
        "previous_mark_price",
        "mark_status",
        "daily_pnl",
        "unrealized_pnl_from_avg",
        "gross_option_market_value",
    ]
    marked = pd.DataFrame(rows)
    if marked.empty:
        marked = pd.DataFrame(columns=list(positions.columns) + added_columns)
    stale_rows = int(marked["mark_status"].astype(str).str.startswith("stale").sum()) if not marked.empty else 0
    missing_rows = int(marked["mark_status"].astype(str).eq("missing_option_close").sum()) if not marked.empty else 0
    nav_after_mark = nav + daily_pnl
    summary = {
        "as_of_date": date,
        "state_dir": str(state_root),
        "config_path": str(config_snapshot.path),
        "config_sha256": config_snapshot.sha256,
        "input_nav": nav,
        "nav_after_close_mark": nav_after_mark,
        "daily_pnl": daily_pnl,
        "daily_return": daily_pnl / nav if nav > 0 else np.nan,
        "position_rows": int(len(active_positions)),
        "marked_rows": int(marked["mark_status"].eq("fresh").sum()) if not marked.empty else 0,
        "stale_rows": stale_rows,
        "missing_rows": missing_rows,
        "margin_used": margin_used,
        "margin_used_pct_nav": margin_used / nav_after_mark if nav_after_mark > 0 else np.nan,
        "gross_option_market_value": gross_option_market_value,
        "snapshot_path": str(snapshot_file) if snapshot_file else None,
        "issues": issues,
        "updated_at": datetime.now().replace(microsecond=0).isoformat(),
    }

    positions_path = None
    summary_path = None
    if write_outputs:
        positions_path = out_root / f"close_mark_positions_{_date_tag(date)}.csv"
        summary_path = out_root / f"close_mark_summary_{_date_tag(date)}.json"
        write_csv(positions_path, marked)
        write_json(summary_path, summary)

    state_nav_path = None
    state_positions_path = None
    if write_state:
        nav_out = nav_frame.copy()
        latest_index = nav_out.index[-1]
        nav_out.loc[latest_index, "as_of_date"] = date
        nav_out.loc[latest_index, "nav"] = nav_after_mark
        nav_out.loc[latest_index, "margin_used"] = margin_used
        nav_out.loc[latest_index, "gross_option_market_value"] = gross_option_market_value
        nav_out.loc[latest_index, "unrealized_pnl"] = marked["unrealized_pnl_from_avg"].sum() if not marked.empty else 0.0
        nav_out.loc[latest_index, "source"] = "daily_close_mark"
        nav_out.loc[latest_index, "updated_at"] = summary["updated_at"]
        state_nav_path = files["nav_state"]
        state_positions_path = files["positions"]
        write_csv(state_nav_path, nav_out)
        if not marked.empty:
            write_csv(state_positions_path, marked)

    return DailyCloseMarkResult(
        as_of_date=date,
        nav=nav_after_mark,
        daily_pnl=daily_pnl,
        daily_return=daily_pnl / nav if nav > 0 else np.nan,
        position_rows=int(len(active_positions)),
        marked_rows=int(summary["marked_rows"]),
        stale_rows=stale_rows,
        missing_rows=missing_rows,
        margin_used=margin_used,
        gross_option_market_value=gross_option_market_value,
        positions_path=positions_path,
        summary_path=summary_path,
        snapshot_path=snapshot_file,
        state_nav_path=state_nav_path,
        state_positions_path=state_positions_path,
        issues=issues,
    )
