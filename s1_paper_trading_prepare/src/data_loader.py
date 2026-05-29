"""Data snapshot helpers for the S1 paper-trading pipeline."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

import pandas as pd

from .paths import DEFAULT_PAPER_CONFIG, ensure_server_deploy_importable, resolve_path


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    size = max(int(size or 1), 1)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _query_option_daily_vwap(signal_date: str, like_sql: str | None) -> pd.DataFrame:
    """Fetch the research-style option VWAP partition for one trading date."""
    from data_tables import OPTION_MINUTE_TABLE
    from query_filters import build_time_eq_sql
    from toolkit.selector import select_bars_sql

    where = build_time_eq_sql(str(signal_date)[:10])
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
        return pd.DataFrame(columns=["option_code", "vwap"])
    out = frame.rename(columns={"option_vwap": "vwap"}).copy()
    out["option_code"] = out["option_code"].astype(str)
    out["vwap"] = pd.to_numeric(out["vwap"], errors="coerce")
    out = out[out["vwap"].notna() & out["vwap"].gt(0)].copy()
    return out[["option_code", "vwap"]].drop_duplicates("option_code", keep="last")


def _attach_option_vwap(snapshot: pd.DataFrame, vwap_frame: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty:
        return snapshot
    out = snapshot.copy()
    if "option_code" not in out.columns:
        return out
    close_source = out["option_close"] if "option_close" in out.columns else pd.Series(index=out.index, dtype=float)
    close = pd.to_numeric(close_source, errors="coerce")
    if vwap_frame.empty:
        if "vwap" not in out.columns:
            out["vwap"] = close
        return out
    out["option_code"] = out["option_code"].astype(str)
    out = out.drop(columns=["vwap"], errors="ignore").merge(vwap_frame, on="option_code", how="left")
    close_source = out["option_close"] if "option_close" in out.columns else pd.Series(index=out.index, dtype=float)
    close = pd.to_numeric(close_source, errors="coerce")
    out["vwap"] = pd.to_numeric(out["vwap"], errors="coerce").where(
        pd.to_numeric(out["vwap"], errors="coerce").gt(0),
        close,
    )
    return out


def load_option_daily_vwap(
    signal_date: str,
    config_path: str | Path | None = None,
    *,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 32,
) -> pd.DataFrame:
    """Load only the Toolkit option VWAP partition needed by stored snapshots."""
    ensure_server_deploy_importable()
    from contract_provider import ContractInfo
    from query_filters import build_product_like_sql
    from strategy_rules import DEFAULT_PARAMS
    from config_loader import load_engine_config

    ci = ContractInfo()
    ci.load()
    if products:
        product_pool = products
    else:
        path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
        config = load_engine_config(str(path), DEFAULT_PARAMS)
        product_pool = config.get("product_pool") or config.get("products") or ci.get_all_products()
    if isinstance(product_pool, str):
        product_pool = [p.strip() for p in product_pool.split(",") if p.strip()]
    product_list = sorted({str(p).upper().strip() for p in product_pool if str(p).strip()})
    parts = []
    for chunk in _chunks(product_list, product_chunk_size):
        like_sql = build_product_like_sql(chunk, ci._cache, ci.get_product_codes)
        part = _query_option_daily_vwap(str(signal_date)[:10], like_sql)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["option_code", "vwap"])
    return pd.concat(parts, ignore_index=True, sort=False).drop_duplicates("option_code", keep="last")


def enrich_snapshot_with_option_vwap(
    snapshot: pd.DataFrame,
    signal_date: str,
    config_path: str | Path | None = None,
    *,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 32,
) -> pd.DataFrame:
    """Attach VWAP to an existing daily snapshot without refetching all fields."""
    if snapshot.empty:
        return snapshot
    vwap = load_option_daily_vwap(
        signal_date,
        config_path,
        products=products,
        product_chunk_size=product_chunk_size,
    )
    return _attach_option_vwap(snapshot, vwap)


def load_signal_day_snapshot(
    signal_date: str,
    config_path: str | Path | None = None,
    *,
    products: tuple[str, ...] | None = None,
    product_chunk_size: int = 32,
) -> pd.DataFrame:
    """Load the same daily aggregate snapshot used by the ToolkitMinuteEngine."""
    ensure_server_deploy_importable()
    from contract_provider import ContractInfo
    from day_loader import ToolkitDayLoader
    from query_filters import build_product_like_sql
    from strategy_rules import DEFAULT_PARAMS
    from config_loader import load_engine_config

    path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
    config = load_engine_config(str(path), DEFAULT_PARAMS)
    ci = ContractInfo()
    ci.load()
    product_pool = products or config.get("product_pool") or config.get("products") or ci.get_all_products()
    if isinstance(product_pool, str):
        product_pool = [p.strip() for p in product_pool.split(",") if p.strip()]
    product_list = sorted({str(p).upper().strip() for p in product_pool if str(p).strip()})
    date = str(signal_date)[:10]
    parts = []
    for chunk in _chunks(product_list, product_chunk_size):
        like_sql = build_product_like_sql(chunk, ci._cache, ci.get_product_codes)
        loader = ToolkitDayLoader(ci)
        loader.preload_daily_agg_batch([date], like_sql, ci)
        part = loader.get_daily_agg(date, ci)
        if not part.empty:
            vwap_part = _query_option_daily_vwap(date, like_sql)
            parts.append(_attach_option_vwap(part, vwap_part))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True, sort=False)
    key_cols = [col for col in ("option_code", "ths_code") if col in out.columns]
    if key_cols:
        out = out.drop_duplicates(subset=key_cols, keep="last")
    return out.reset_index(drop=True)


def load_trading_dates(start_date: str, end_date: str) -> list[str]:
    """Load Toolkit trading dates for incremental refresh windows."""
    ensure_server_deploy_importable()
    from contract_provider import ContractInfo
    from day_loader import ToolkitDayLoader

    loader = ToolkitDayLoader(ContractInfo())
    return [str(date)[:10] for date in loader.get_trading_dates(str(start_date)[:10], str(end_date)[:10])]
