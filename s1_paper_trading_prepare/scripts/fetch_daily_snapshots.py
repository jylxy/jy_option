from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.data_loader import _attach_option_vwap, _chunks, load_trading_dates
from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import (
    DEFAULT_DATA_DIR,
    DEFAULT_PAPER_CONFIG,
    ensure_server_deploy_importable,
    resolve_path,
)


def parse_products(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    parsed = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return parsed or None


def snapshot_path(data_dir: Path, date: str) -> Path:
    tag = str(date)[:10].replace("-", "")
    return data_dir / "daily_snapshots" / f"option_chain_{tag}.csv"


def has_rows(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 8:
        return False
    try:
        return bool(pd.read_csv(path, nrows=1).shape[0])
    except Exception:
        return False


def query_option_daily_vwap_batch(dates: list[str], like_sql: str | None) -> pd.DataFrame:
    ensure_server_deploy_importable()
    from data_tables import OPTION_MINUTE_TABLE
    from query_filters import build_time_in_dates_sql
    from toolkit.selector import select_bars_sql

    where = build_time_in_dates_sql(dates)
    if like_sql:
        where += f" AND ({like_sql})"
    query = f"""
        SELECT
            toString(date) AS trade_date,
            ths_code AS option_code,
            if(
                sum(toFloat64OrZero(toString(volume))) > 0,
                sum(toFloat64OrZero(toString(close)) * toFloat64OrZero(toString(volume)))
                    / sum(toFloat64OrZero(toString(volume))),
                avg(toFloat64OrZero(toString(close)))
            ) AS option_vwap
        FROM {OPTION_MINUTE_TABLE}
        WHERE {where}
          AND toFloat64OrZero(toString(close)) > 0
        GROUP BY date, ths_code
    """
    frame = select_bars_sql(query)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["trade_date", "option_code", "vwap"])
    out = frame.rename(columns={"option_vwap": "vwap"}).copy()
    out["trade_date"] = out["trade_date"].astype(str).str[:10]
    out["option_code"] = out["option_code"].astype(str)
    out["vwap"] = pd.to_numeric(out["vwap"], errors="coerce")
    out = out[out["vwap"].notna() & out["vwap"].gt(0)].copy()
    return out[["trade_date", "option_code", "vwap"]].drop_duplicates(["trade_date", "option_code"], keep="last")


def load_signal_snapshots_batch(
    dates: list[str],
    config_path: Path,
    *,
    products: tuple[str, ...] | None,
    product_chunk_size: int,
) -> dict[str, pd.DataFrame]:
    ensure_server_deploy_importable()
    from config_loader import load_engine_config
    from contract_provider import ContractInfo
    from day_loader import ToolkitDayLoader
    from query_filters import build_product_like_sql
    from strategy_rules import DEFAULT_PARAMS

    ci = ContractInfo()
    ci.load()
    config = load_engine_config(str(config_path), DEFAULT_PARAMS)
    product_pool = products or config.get("product_pool") or config.get("products") or ci.get_all_products()
    if isinstance(product_pool, str):
        product_pool = [p.strip() for p in product_pool.split(",") if p.strip()]
    product_list = sorted({str(p).upper().strip() for p in product_pool if str(p).strip()})
    parts_by_date: dict[str, list[pd.DataFrame]] = {date: [] for date in dates}
    for chunk in _chunks(product_list, product_chunk_size):
        like_sql = build_product_like_sql(chunk, ci._cache, ci.get_product_codes)
        loader = ToolkitDayLoader(ci)
        loader.preload_daily_agg_batch(dates, like_sql, ci)
        vwap_all = query_option_daily_vwap_batch(dates, like_sql)
        for date in dates:
            part = loader.get_daily_agg(date, ci)
            if part.empty:
                continue
            vwap_part = vwap_all[vwap_all["trade_date"].eq(date)][["option_code", "vwap"]]
            parts_by_date[date].append(_attach_option_vwap(part, vwap_part))
    out: dict[str, pd.DataFrame] = {}
    for date, parts in parts_by_date.items():
        if not parts:
            out[date] = pd.DataFrame()
            continue
        frame = pd.concat(parts, ignore_index=True, sort=False)
        key_cols = [col for col in ("option_code", "ths_code") if col in frame.columns]
        if key_cols:
            frame = frame.drop_duplicates(subset=key_cols, keep="last")
        out[date] = frame.reset_index(drop=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch/reuse Toolkit daily option snapshots for the current S1 paper line.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--products", default=None)
    parser.add_argument("--product-chunk-size", type=int, default=32)
    parser.add_argument("--date-batch-size", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data_dir = resolve_path(args.data_dir)
    config = resolve_path(args.config)
    products = parse_products(args.products)
    dates = load_trading_dates(args.start_date, args.end_date)
    fetched: list[str] = []
    reused: list[str] = []
    empty: list[str] = []
    pending: list[str] = []
    for idx, date in enumerate(dates, start=1):
        path = snapshot_path(data_dir, date)
        if path.exists() and not args.force and has_rows(path):
            reused.append(date)
        else:
            pending.append(date)
        if idx == 1 or idx == len(dates) or idx % 50 == 0:
            print(f"[scan {idx}/{len(dates)}] {date} reused={len(reused)} pending={len(pending)}", flush=True)

    for batch_idx, start in enumerate(range(0, len(pending), max(1, int(args.date_batch_size or 1))), start=1):
        batch = pending[start:start + max(1, int(args.date_batch_size or 1))]
        frames = load_signal_snapshots_batch(
            batch,
            config,
            products=products,
            product_chunk_size=args.product_chunk_size,
        )
        for date in batch:
            frame = frames.get(date, pd.DataFrame())
            if frame.empty:
                empty.append(date)
                action = "empty"
            else:
                write_csv(snapshot_path(data_dir, date), frame)
                fetched.append(date)
                action = f"fetch rows={len(frame)}"
            print(
                f"[fetch batch={batch_idx} {len(fetched) + len(empty)}/{len(pending)}] {date} {action}",
                flush=True,
            )

    manifest = data_dir / "manifests" / "fetch_daily_snapshots_manifest.json"
    write_json(
        manifest,
        {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "dates": len(dates),
            "fetched_dates": fetched,
            "reused_dates": reused,
            "empty_dates": empty,
            "data_dir": str(data_dir),
            "config": str(config),
        },
    )
    print(
        "SNAPSHOT_FETCH_OK "
        f"dates={len(dates)} fetched={len(fetched)} reused={len(reused)} "
        f"empty={len(empty)} manifest={manifest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
