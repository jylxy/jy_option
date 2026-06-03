"""Point-in-time builder for the current reverse-low-jump main sleeve."""

from __future__ import annotations

import bisect
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .paths import ensure_server_deploy_importable
from .signal_feature_utils import ensure_signal_iv, signal_iv_col


COMMODITY_EXCHANGES = {"CZCE", "DCE", "GFEX", "INE", "SHFE"}
LOW_METRICS = {
    "hist_jump5pp_rate_756": 0.35,
    "hist_p95_abs_iv_chg_756": 0.20,
    "hist_jump20pct_rate_756": 0.15,
}
HIGH_METRICS = {
    "candidate_pm_median_252": 0.15,
    "candidate_signal_days_252": 0.10,
    "candidate_oi_median_252": 0.05,
}


def _safe_float(value: object) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def _product_key(exchange: object, product: object) -> str:
    return f"{str(exchange or '').strip().upper()}|{str(product or '').strip().upper()}"


def _normalize_options(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    rename = {
        "target_expiry": "expiry_date",
        "entry_price": "close",
        "close_oi": "open_interest",
    }
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out[dst] = out[src]
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out["expiry_date"] = pd.to_datetime(out["expiry_date"], errors="coerce")
    out["exchange"] = out["exchange"].astype(str).str.upper()
    out["product_label"] = out["product"].astype(str).str.upper()
    out["product_key"] = [
        _product_key(exchange, product)
        for exchange, product in zip(out["exchange"], out["product_label"])
    ]
    out["option_type"] = out["option_type"].astype(str).str.upper().str[0]
    if "open_interest" in out.columns and "close_oi" not in out.columns:
        out["close_oi"] = out["open_interest"]
    if "close" not in out.columns and "option_close" in out.columns:
        out["close"] = out["option_close"]
    for column in [
        "strike",
        "dte",
        "close",
        "volume",
        "close_oi",
        "implied_vol",
        "delta",
        "moneyness",
        "spot_close",
        "multiplier",
    ]:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out["abs_delta"] = pd.to_numeric(out.get("delta"), errors="coerce").abs()
    out["gross_premium_cash_1lot"] = pd.to_numeric(out["close"], errors="coerce") * pd.to_numeric(
        out["multiplier"], errors="coerce"
    )
    return out.dropna(subset=["trade_date", "expiry_date", "exchange", "product_label"])


def _estimate_margin_vectorized(frame: pd.DataFrame) -> pd.Series:
    ensure_server_deploy_importable()
    from broker_costs import broker_margin_ratio_for_product
    from margin_model import (
        COMMODITY_OPTION_EXCHANGES,
        DEFAULT_MARGIN_RATIO_BY_EXCHANGE,
        EQUITY_OPTION_EXCHANGES,
        coerce_ratio,
        normalize_exchange,
    )

    spot = pd.to_numeric(frame["spot_close"], errors="coerce")
    strike = pd.to_numeric(frame["strike"], errors="coerce")
    price = pd.to_numeric(frame["close"], errors="coerce").clip(lower=0.0)
    mult = pd.to_numeric(frame["multiplier"], errors="coerce").fillna(1.0)
    ratios = []
    for exch, prod in zip(frame["exchange"], frame["product_label"]):
        ratio = coerce_ratio(broker_margin_ratio_for_product(prod))
        if ratio is None:
            ratio = DEFAULT_MARGIN_RATIO_BY_EXCHANGE.get(normalize_exchange(exch), 0.10)
        ratios.append(ratio)
    ratio = pd.Series(ratios, index=frame.index, dtype=float)
    option_type = frame["option_type"].astype(str).str.upper().str[0]
    exch = frame["exchange"].map(normalize_exchange)
    valid = (
        spot.gt(0)
        & strike.gt(0)
        & price.notna()
        & mult.gt(0)
        & ratio.gt(0)
        & option_type.isin(["C", "P"])
    )
    margin = pd.Series(np.nan, index=frame.index, dtype=float)
    otm_call = (strike - spot).clip(lower=0.0)
    otm_put = (spot - strike).clip(lower=0.0)
    otm = pd.Series(np.where(option_type.eq("C"), otm_call, otm_put), index=frame.index)

    equity = valid & exch.isin(EQUITY_OPTION_EXCHANGES)
    if equity.any():
        min_ratio = 0.07
        call = equity & option_type.eq("C")
        put = equity & option_type.eq("P")
        required = pd.Series(np.nan, index=frame.index, dtype=float)
        required.loc[call] = price.loc[call] + np.maximum(
            spot.loc[call] * ratio.loc[call] - otm.loc[call],
            spot.loc[call] * min_ratio,
        )
        required.loc[put] = np.minimum(
            price.loc[put]
            + np.maximum(
                spot.loc[put] * ratio.loc[put] - otm.loc[put],
                strike.loc[put] * min_ratio,
            ),
            strike.loc[put],
        )
        margin.loc[equity] = np.maximum(required.loc[equity], price.loc[equity]) * mult.loc[equity]

    commodity = valid & exch.isin(COMMODITY_OPTION_EXCHANGES)
    if commodity.any():
        fut_margin = spot.loc[commodity] * ratio.loc[commodity]
        margin.loc[commodity] = (
            price.loc[commodity]
            + np.maximum(fut_margin - 0.5 * otm.loc[commodity], 0.5 * fut_margin)
        ) * mult.loc[commodity]

    generic = valid & ~(equity | commodity)
    if generic.any():
        call = generic & option_type.eq("C")
        put = generic & option_type.eq("P")
        generic_margin = pd.Series(np.nan, index=frame.index, dtype=float)
        generic_margin.loc[call] = (
            price.loc[call]
            + np.maximum(
                spot.loc[call] * ratio.loc[call] - otm.loc[call],
                0.5 * spot.loc[call] * ratio.loc[call],
            )
        ) * mult.loc[call]
        generic_margin.loc[put] = (
            price.loc[put]
            + np.maximum(
                spot.loc[put] * ratio.loc[put] - otm.loc[put],
                0.5 * strike.loc[put] * ratio.loc[put],
            )
        ) * mult.loc[put]
        margin.loc[generic] = generic_margin.loc[generic]
    margin.loc[valid & margin.isna()] = price.loc[valid & margin.isna()] * mult.loc[valid & margin.isna()]
    return margin.clip(lower=0.0)


def _eligible_contracts(df: pd.DataFrame, max_abs_delta: float, min_oi: float) -> pd.DataFrame:
    base = df[
        df["exchange"].isin(COMMODITY_EXCHANGES)
        & df["option_type"].isin(["P", "C"])
        & df["abs_delta"].le(max_abs_delta)
        & pd.to_numeric(df["close_oi"], errors="coerce").ge(min_oi)
        & pd.to_numeric(df["volume"], errors="coerce").gt(0)
        & pd.to_numeric(df["close"], errors="coerce").gt(0)
        & (
            (df["option_type"].eq("P") & pd.to_numeric(df["moneyness"], errors="coerce").lt(1.0))
            | (df["option_type"].eq("C") & pd.to_numeric(df["moneyness"], errors="coerce").gt(1.0))
        )
    ].copy()
    out = ensure_signal_iv(base, price_col="close")
    iv_col = signal_iv_col(out)
    out = out[pd.to_numeric(out[iv_col], errors="coerce").gt(0)].copy()
    if out.empty:
        return out
    if "one_contract_margin" not in out.columns or pd.to_numeric(out["one_contract_margin"], errors="coerce").isna().any():
        out["one_contract_margin"] = _estimate_margin_vectorized(out)
    out["premium_margin"] = out["gross_premium_cash_1lot"] / pd.to_numeric(
        out["one_contract_margin"], errors="coerce"
    ).replace(0, np.nan)
    if "implied_vol" in out.columns:
        out["implied_vol_raw"] = out["implied_vol"]
    out["implied_vol"] = pd.to_numeric(out[iv_col], errors="coerce")
    return out


def _choose_contract(rows: pd.DataFrame, side: str, max_abs_delta: float, min_oi: float) -> pd.Series | None:
    candidates = _eligible_contracts(rows[rows["option_type"].eq(side)], max_abs_delta, min_oi)
    candidates = candidates[pd.to_numeric(candidates["abs_delta"], errors="coerce").lt(max_abs_delta)].copy()
    if candidates.empty:
        return None
    candidates = candidates.sort_values(
        ["abs_delta", "close_oi", "volume", "close"],
        ascending=[False, False, False, False],
        kind="mergesort",
    )
    return candidates.iloc[0]


def _choose_high_iv_pressure(rows: pd.DataFrame, max_abs_delta: float, min_oi: float) -> tuple[pd.Series | None, dict[str, Any]]:
    diag: dict[str, Any] = {
        "put_iv_pressure": np.nan,
        "call_iv_pressure": np.nan,
        "put_candidate_count": 0,
        "call_candidate_count": 0,
        "sell_side": "",
        "selected_side_iv_pressure": np.nan,
        "other_side_iv_pressure": np.nan,
        "side_iv_pressure_diff": np.nan,
    }
    stats: dict[str, tuple[pd.DataFrame, float]] = {}
    for side in ["P", "C"]:
        candidates = _eligible_contracts(rows[rows["option_type"].eq(side)], max_abs_delta, min_oi)
        iv_col = signal_iv_col(candidates)
        pressure = float(pd.to_numeric(candidates[iv_col], errors="coerce").median()) if not candidates.empty else np.nan
        stats[side] = (candidates, pressure)
        prefix = "put" if side == "P" else "call"
        diag[f"{prefix}_iv_pressure"] = pressure
        diag[f"{prefix}_candidate_count"] = int(len(candidates))
    available = [
        (side, pressure)
        for side, (candidates, pressure) in stats.items()
        if not candidates.empty and np.isfinite(pressure)
    ]
    if not available:
        return None, diag
    side = sorted(available, key=lambda x: (x[1], 0 if x[0] == "P" else 1), reverse=True)[0][0]
    other = "C" if side == "P" else "P"
    picked = stats[side][0].sort_values(
        ["abs_delta", "close_oi", "volume", "close"],
        ascending=[False, False, False, False],
        kind="mergesort",
    ).iloc[0]
    diag["sell_side"] = side
    diag["selected_side_iv_pressure"] = stats[side][1]
    diag["other_side_iv_pressure"] = stats[other][1]
    if np.isfinite(stats[side][1]) and np.isfinite(stats[other][1]):
        diag["side_iv_pressure_diff"] = stats[side][1] - stats[other][1]
    return picked, diag


def _monthly_expiries(df: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    out: dict[str, list[pd.Timestamp]] = {}
    for key, group in df.groupby("product_key", sort=False):
        expiries = (
            group[["expiry_date"]]
            .dropna()
            .drop_duplicates()
            .assign(ym=lambda x: x["expiry_date"].dt.to_period("M"))
            .sort_values("expiry_date", kind="mergesort")
        )
        out[key] = [pd.Timestamp(x) for x in expiries.groupby("ym", as_index=False)["expiry_date"].min()["expiry_date"]]
    return out


def build_product_opportunities(
    option_data: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    entry_window_calendar_days: int = 7,
    max_abs_delta: float = 0.08,
    min_oi: float = 1000.0,
) -> pd.DataFrame:
    data = _normalize_options(option_data)
    data = data[data["exchange"].isin(COMMODITY_EXCHANGES)].copy()
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    rows_by_product_date = {key: group for key, group in data.groupby(["product_key", "trade_date"], sort=False)}
    rows_by_product_date_expiry = {
        key: group for key, group in data.groupby(["product_key", "trade_date", "expiry_date"], sort=False)
    }
    product_dates = {
        key: sorted(group["trade_date"].drop_duplicates().tolist())
        for key, group in data.groupby("product_key", sort=False)
    }
    meta = data.groupby("product_key", as_index=False).agg(exchange=("exchange", "first"), product_label=("product_label", "first"))
    meta_by_key = meta.set_index("product_key").to_dict("index")
    rows: list[dict[str, Any]] = []
    for key, expiries in _monthly_expiries(data).items():
        dates = pd.Series(product_dates.get(key, []), dtype="datetime64[ns]")
        if dates.empty:
            continue
        for idx in range(len(expiries) - 1):
            prev_expiry = expiries[idx]
            entry_dates = dates[(dates > prev_expiry) & (dates <= prev_expiry + pd.Timedelta(days=entry_window_calendar_days))]
            if entry_dates.empty:
                continue
            picked = None
            picked_date = None
            picked_expiry = None
            picked_diag: dict[str, Any] = {}
            for entry_date in entry_dates.tolist():
                if entry_date < start or entry_date > end:
                    continue
                day_all = rows_by_product_date.get((key, entry_date), pd.DataFrame())
                if day_all.empty:
                    continue
                available = day_all[day_all["expiry_date"].gt(entry_date)].copy()
                if available.empty:
                    continue
                near_expiry = pd.Timestamp(available["expiry_date"].min())
                day_rows = rows_by_product_date_expiry.get((key, entry_date, near_expiry), pd.DataFrame())
                candidate, diag = _choose_high_iv_pressure(day_rows, max_abs_delta, min_oi)
                if candidate is None:
                    continue
                picked = candidate
                picked_date = pd.Timestamp(entry_date)
                picked_expiry = near_expiry
                picked_diag = diag
                break
            if picked is None:
                continue
            info = meta_by_key.get(key, {})
            row = {
                "product_key": key,
                "exchange": info.get("exchange"),
                "product_label": info.get("product_label"),
                "entry_date": picked_date,
                "prev_expiry": prev_expiry,
                "target_expiry": picked_expiry,
                "contract_code": picked.get("contract_code"),
                "option_type": picked.get("option_type"),
                "strike": picked.get("strike"),
                "dte": picked.get("dte"),
                "close": picked.get("close"),
                "volume": picked.get("volume"),
                "close_oi": picked.get("close_oi"),
                "implied_vol": picked.get("implied_vol"),
                "delta": picked.get("delta"),
                "abs_delta": picked.get("abs_delta"),
                "moneyness": picked.get("moneyness"),
                "spot_close": picked.get("spot_close"),
                "multiplier": picked.get("multiplier"),
                "premium_margin": picked.get("premium_margin"),
                "one_contract_margin": picked.get("one_contract_margin"),
                "gross_premium_cash_1lot": picked.get("gross_premium_cash_1lot"),
                **picked_diag,
            }
            rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["entry_date", "product_key"], kind="mergesort")


def build_feature_panel(
    option_data: pd.DataFrame,
    *,
    rolling_window: int = 756,
    candidate_window: int = 252,
    max_abs_delta: float = 0.08,
    min_oi: float = 1000.0,
) -> pd.DataFrame:
    data = _normalize_options(option_data)
    data = data[data["exchange"].isin(COMMODITY_EXCHANGES)].copy()
    atm_base = data[
        pd.to_numeric(data["dte"], errors="coerce").between(7, 90)
        & pd.to_numeric(data["moneyness"], errors="coerce").between(0.95, 1.05)
        & pd.to_numeric(data["close"], errors="coerce").gt(0)
    ].copy()
    atm = ensure_signal_iv(atm_base, price_col="close")
    atm_iv_col = signal_iv_col(atm)
    atm = atm[pd.to_numeric(atm[atm_iv_col], errors="coerce").gt(0.01) & pd.to_numeric(atm[atm_iv_col], errors="coerce").lt(2.5)].copy()
    if atm.empty:
        return pd.DataFrame()
    daily = (
        atm.groupby(["product_key", "trade_date"], as_index=False)
        .agg(
            exchange=("exchange", "first"),
            product_label=("product_label", "first"),
            atm_iv=(atm_iv_col, "median"),
            option_obs=(atm_iv_col, "size"),
            total_volume=("volume", "sum"),
            total_oi=("close_oi", "sum"),
        )
        .sort_values(["product_key", "trade_date"], kind="mergesort")
    )
    pieces: list[pd.DataFrame] = []
    for _, group in daily.groupby("product_key", sort=False):
        group = group.sort_values("trade_date", kind="mergesort").copy()
        group["iv_chg_1d"] = group["atm_iv"].diff()
        group["iv_rel_chg_1d"] = group["iv_chg_1d"] / group["atm_iv"].shift(1).replace(0, np.nan)
        group["abs_iv_chg_1d"] = group["iv_chg_1d"].abs()
        group["iv_jump_5pp"] = group["abs_iv_chg_1d"].ge(0.05).astype(float)
        group["iv_jump_20pct"] = group["iv_rel_chg_1d"].abs().ge(0.20).astype(float)
        shifted_iv = group["atm_iv"].shift(1)
        shifted_abs = group["abs_iv_chg_1d"].shift(1)
        group["hist_iv_days_756"] = shifted_iv.rolling(rolling_window, min_periods=1).count()
        group["hist_median_iv_756"] = shifted_iv.rolling(rolling_window, min_periods=1).median()
        mean = shifted_iv.rolling(rolling_window, min_periods=1).mean()
        std = shifted_iv.rolling(rolling_window, min_periods=1).std()
        group["hist_iv_cv_756"] = std / mean.replace(0, np.nan)
        group["hist_jump5pp_rate_756"] = group["iv_jump_5pp"].shift(1).rolling(rolling_window, min_periods=1).mean()
        group["hist_jump20pct_rate_756"] = group["iv_jump_20pct"].shift(1).rolling(rolling_window, min_periods=1).mean()
        group["hist_p95_abs_iv_chg_756"] = shifted_abs.rolling(rolling_window, min_periods=20).quantile(0.95)
        pieces.append(group)
    features = pd.concat(pieces, ignore_index=True, sort=False)

    candidate_base = data[
        data["exchange"].isin(COMMODITY_EXCHANGES)
        & pd.to_numeric(data["close"], errors="coerce").gt(0)
        & pd.to_numeric(data["spot_close"], errors="coerce").gt(0)
        & pd.to_numeric(data["strike"], errors="coerce").gt(0)
        & pd.to_numeric(data["multiplier"], errors="coerce").gt(0)
        & pd.to_numeric(data["delta"], errors="coerce").notna()
        & pd.to_numeric(data["dte"], errors="coerce").between(30, 45)
        & pd.to_numeric(data["volume"], errors="coerce").gt(0)
        & pd.to_numeric(data["close_oi"], errors="coerce").ge(min_oi)
        & data["option_type"].isin(["P", "C"])
        & (
            (data["option_type"].eq("P") & pd.to_numeric(data["moneyness"], errors="coerce").lt(1.0))
            | (data["option_type"].eq("C") & pd.to_numeric(data["moneyness"], errors="coerce").gt(1.0))
        )
        & pd.to_numeric(data["abs_delta"], errors="coerce").between(0.04, max_abs_delta, inclusive="both")
    ].copy()
    eligible = ensure_signal_iv(candidate_base, price_col="close")
    cand_iv_col = signal_iv_col(eligible)
    if not eligible.empty:
        eligible = eligible[
            pd.to_numeric(eligible[cand_iv_col], errors="coerce").gt(0.01)
            & pd.to_numeric(eligible[cand_iv_col], errors="coerce").lt(2.5)
        ].copy()
    if not eligible.empty:
        if "one_contract_margin" not in eligible.columns:
            eligible["one_contract_margin"] = np.nan
        missing_margin = pd.to_numeric(eligible["one_contract_margin"], errors="coerce").le(0) | pd.to_numeric(
            eligible["one_contract_margin"], errors="coerce"
        ).isna()
        if missing_margin.any():
            eligible.loc[missing_margin, "one_contract_margin"] = _estimate_margin_vectorized(eligible.loc[missing_margin])
        eligible["gross_premium_cash_1lot"] = pd.to_numeric(eligible["close"], errors="coerce") * pd.to_numeric(
            eligible["multiplier"], errors="coerce"
        )
        eligible["premium_margin"] = eligible["gross_premium_cash_1lot"] / pd.to_numeric(
            eligible["one_contract_margin"], errors="coerce"
        ).replace(0, np.nan)
        eligible = eligible[pd.to_numeric(eligible["premium_margin"], errors="coerce").gt(0) & pd.to_numeric(eligible["premium_margin"], errors="coerce").lt(1)].copy()
    if not eligible.empty:
        cand_daily = (
            eligible.groupby(["product_key", "trade_date"], as_index=False)
            .agg(
                candidate_has_signal=("dte", "size"),
                candidate_pm_day=("premium_margin", "median"),
                candidate_oi_day=("close_oi", "median"),
                candidate_rows_day=("dte", "size"),
            )
        )
        cand_daily["candidate_has_signal"] = cand_daily["candidate_has_signal"].gt(0).astype(float)
    else:
        cand_daily = pd.DataFrame(columns=["product_key", "trade_date"])
    base = features[["product_key", "trade_date"]].drop_duplicates().merge(cand_daily, on=["product_key", "trade_date"], how="left")
    base["candidate_has_signal"] = base["candidate_has_signal"].fillna(0.0)
    base["candidate_rows_day"] = base["candidate_rows_day"].fillna(0.0)
    cand_pieces: list[pd.DataFrame] = []
    for _, group in base.groupby("product_key", sort=False):
        group = group.sort_values("trade_date", kind="mergesort").copy()
        group["candidate_signal_days_252"] = group["candidate_has_signal"].shift(1).rolling(candidate_window, min_periods=1).sum()
        group["candidate_rows_252"] = group["candidate_rows_day"].shift(1).rolling(candidate_window, min_periods=1).sum()
        group["candidate_pm_median_252"] = group["candidate_pm_day"].shift(1).rolling(candidate_window, min_periods=5).median()
        group["candidate_oi_median_252"] = group["candidate_oi_day"].shift(1).rolling(candidate_window, min_periods=5).median()
        cand_pieces.append(group[["product_key", "trade_date", "candidate_signal_days_252", "candidate_rows_252", "candidate_pm_median_252", "candidate_oi_median_252"]])
    candidate_features = pd.concat(cand_pieces, ignore_index=True, sort=False)
    features = features.merge(candidate_features, on=["product_key", "trade_date"], how="left")
    features = features.rename(columns={"trade_date": "feature_date"})
    return features


def _asof_by_product(left: pd.DataFrame, right: pd.DataFrame, left_date_col: str, right_date_col: str) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for key, left_group in left.groupby("product_key", sort=False):
        right_group = right[right["product_key"].eq(key)].sort_values(right_date_col, kind="mergesort")
        if right_group.empty:
            empty = left_group.copy()
            for column in right.columns:
                if column != "product_key" and column not in empty.columns:
                    empty[column] = np.nan
            pieces.append(empty)
            continue
        merged = pd.merge_asof(
            left_group.sort_values(left_date_col, kind="mergesort"),
            right_group,
            left_on=left_date_col,
            right_on=right_date_col,
            direction="backward",
            suffixes=("", "_feature"),
        )
        pieces.append(merged.drop(columns=["product_key_feature"], errors="ignore"))
    return pd.concat(pieces, ignore_index=True, sort=False) if pieces else pd.DataFrame()


def add_cross_section_scores(opps: pd.DataFrame, features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if opps.empty or features.empty:
        return opps.copy(), pd.DataFrame()
    entry_dates = pd.Series(opps["entry_date"].drop_duplicates().sort_values().tolist(), name="entry_date")
    products = features[["product_key", "exchange", "product_label"]].drop_duplicates("product_key").sort_values("product_key")
    dense = products.merge(entry_dates.to_frame(), how="cross")
    dense = _asof_by_product(dense, features, "entry_date", "feature_date")
    dense = dense.copy()
    score = pd.Series(0.0, index=dense.index)
    for metric, weight in LOW_METRICS.items():
        rank = dense.groupby("entry_date")[metric].rank(pct=True, ascending=False)
        dense[f"{metric}_score"] = rank
        score = score + weight * rank.fillna(0.0)
    for metric, weight in HIGH_METRICS.items():
        rank = dense.groupby("entry_date")[metric].rank(pct=True, ascending=True)
        dense[f"{metric}_score"] = rank
        score = score + weight * rank.fillna(0.0)
    valid = dense[list(LOW_METRICS) + list(HIGH_METRICS)].notna().all(axis=1)
    dense["rolling_cs_score"] = score
    dense.loc[~valid, "rolling_cs_score"] = np.nan
    dense["rolling_cs_rank"] = dense.groupby("entry_date")["rolling_cs_score"].rank(method="first", ascending=False)
    dense["rolling_cs_count"] = dense.groupby("entry_date")["rolling_cs_score"].transform("count")
    score_cols = ["product_key", "entry_date", "rolling_cs_score", "rolling_cs_rank", "rolling_cs_count"]
    out = opps.merge(dense[score_cols], on=["product_key", "entry_date"], how="left")
    return out, dense


def intrinsic_value(option_type: object, strike: float, spot: float) -> float:
    if not np.isfinite(strike) or not np.isfinite(spot):
        return np.nan
    return max(strike - spot, 0.0) if str(option_type).upper() == "P" else max(spot - strike, 0.0)


def add_matured_shadow_stats(opps: pd.DataFrame, option_data: pd.DataFrame) -> pd.DataFrame:
    if opps.empty:
        return opps.copy()
    data = _normalize_options(option_data)
    by_code = {code: group.sort_values("trade_date", kind="mergesort") for code, group in data.groupby("contract_code", sort=False)}
    labels: list[dict[str, Any]] = []
    for row in opps.itertuples(index=False):
        group = by_code.get(row.contract_code)
        if group is None or group.empty:
            labels.append({})
            continue
        hold = group[(group["trade_date"].ge(row.entry_date)) & (group["trade_date"].le(row.target_expiry))]
        if hold.empty:
            hold = group[group["trade_date"].le(row.target_expiry)].tail(1)
        if hold.empty:
            labels.append({})
            continue
        last = hold.iloc[-1]
        entry_price = float(row.close)
        intrinsic = intrinsic_value(row.option_type, float(row.strike), float(last["spot_close"]))
        pnl_unit = entry_price - intrinsic
        max_close = float(hold["close"].max())
        labels.append(
            {
                "expiry_pnl_per_premium": pnl_unit / entry_price if entry_price > 0 else np.nan,
                "expiry_pnl_nonnegative": bool(pnl_unit >= 0) if np.isfinite(pnl_unit) else np.nan,
                "terminal_otm": bool(intrinsic <= 1e-12) if np.isfinite(intrinsic) else np.nan,
                "path_stop25_safe": bool(max_close < 2.5 * entry_price) if entry_price > 0 else np.nan,
                "path_stop2_safe": bool(max_close < 2.0 * entry_price) if entry_price > 0 else np.nan,
            }
        )
    out = pd.concat([opps.reset_index(drop=True), pd.DataFrame(labels)], axis=1)
    out = out.sort_values(["entry_date", "product_key"], kind="mergesort").reset_index(drop=True)
    metrics: list[list[float]] = []
    for row in out.itertuples(index=False):
        past = out[
            out["product_label"].eq(row.product_label)
            & (pd.to_datetime(out["target_expiry"], errors="coerce") < pd.Timestamp(row.entry_date))
            & out["expiry_pnl_per_premium"].notna()
        ]
        past_side = past[past["sell_side"].eq(row.sell_side)]

        def pack(frame: pd.DataFrame) -> list[float]:
            if frame.empty:
                return [0, np.nan, np.nan, np.nan, np.nan, np.nan]
            tail = frame.tail(12)
            return [
                float(len(tail)),
                float(tail["expiry_pnl_nonnegative"].mean()),
                float(tail["terminal_otm"].mean()),
                float(tail["path_stop25_safe"].mean()),
                float(tail["path_stop2_safe"].mean()),
                float(tail["expiry_pnl_per_premium"].mean()),
            ]

        metrics.append(pack(past) + pack(past_side))
    cols = [
        "shadow_prod_n12",
        "shadow_prod_pnl_nonnegative",
        "shadow_prod_terminal_otm",
        "shadow_prod_stop25_safe",
        "shadow_prod_stop2_safe",
        "shadow_prod_pnlprem",
        "shadow_side_n12",
        "shadow_side_pnl_nonnegative",
        "shadow_side_terminal_otm",
        "shadow_side_stop25_safe",
        "shadow_side_stop2_safe",
        "shadow_side_pnlprem",
    ]
    out[cols] = metrics

    def choose(row: pd.Series, name: str) -> float:
        side_value = row[f"shadow_side_{name}"]
        prod_value = row[f"shadow_prod_{name}"]
        if row["shadow_side_n12"] >= 3 and pd.notna(side_value):
            return float(side_value)
        return float(prod_value) if pd.notna(prod_value) else np.nan

    out["shadow_pnl_nonnegative_blend"] = out.apply(lambda r: choose(r, "pnl_nonnegative"), axis=1)
    out["shadow_terminal_otm_blend"] = out.apply(lambda r: choose(r, "terminal_otm"), axis=1)
    out["shadow_stop25_blend"] = out.apply(lambda r: choose(r, "stop25_safe"), axis=1)
    out["shadow_stop2_blend"] = out.apply(lambda r: choose(r, "stop2_safe"), axis=1)
    out["shadow_pnlprem_blend"] = out.apply(lambda r: choose(r, "pnlprem"), axis=1)
    out["shadow_score"] = (
        0.30 * out["shadow_pnl_nonnegative_blend"]
        + 0.25 * out["shadow_terminal_otm_blend"]
        + 0.25 * out["shadow_stop25_blend"]
        + 0.10 * out["shadow_stop2_blend"]
        + 0.10 * ((out["shadow_pnlprem_blend"].clip(-1, 1) + 1.0) / 2.0)
    )
    return out


def add_rule_flags(opps: pd.DataFrame, *, min_history_days: int = 120) -> pd.DataFrame:
    out = opps.copy()
    hist_days = pd.to_numeric(out["hist_iv_days_756"], errors="coerce")
    jump5 = pd.to_numeric(out["hist_jump5pp_rate_756"], errors="coerce")
    p95_abs = pd.to_numeric(out["hist_p95_abs_iv_chg_756"], errors="coerce")
    opt_oi_x63 = pd.to_numeric(out["opt_side_oi_x63"], errors="coerce")
    opt_volume_x63 = pd.to_numeric(out["opt_side_volume_x63"], errors="coerce")
    rolling_rank = pd.to_numeric(out["rolling_cs_rank"], errors="coerce")
    pressure_diff = pd.to_numeric(out["side_iv_pressure_diff"], errors="coerce")
    fut_oi_chg5 = pd.to_numeric(out["fut_oi_chg5"], errors="coerce")
    out["pit_low_jump_strict"] = (
        hist_days.ge(min_history_days)
        & jump5.le(0.025)
        & p95_abs.le(0.040)
    )
    out["rule_l1_oi03"] = out["pit_low_jump_strict"] & opt_oi_x63.ge(0.3)
    out["rule_l1_oi03_flow_guard"] = out["rule_l1_oi03"] & (
        fut_oi_chg5.gt(0)
        | opt_volume_x63.le(1.0)
    )
    out["rule_l1_strict_low_rank15_oi03"] = (
        hist_days.ge(min_history_days)
        & jump5.le(0.005)
        & p95_abs.le(0.025)
        & rolling_rank.le(15)
        & opt_oi_x63.ge(0.3)
    )
    out["rule_l1_oi03_optvol_x12"] = out["rule_l1_oi03"] & opt_volume_x63.le(1.2)
    out["rule_l1_oi03_flow_guard_diff02"] = out["rule_l1_oi03_flow_guard"] & pd.to_numeric(
        out["side_iv_pressure_diff"], errors="coerce"
    ).ge(0.02)
    out["rule_l1_oi03_flow_guard_no_weak_pressure_flow"] = out["rule_l1_oi03_flow_guard"] & ~(
        pressure_diff.lt(0.02)
        & opt_volume_x63.gt(1.5)
    )
    h_core = (
        hist_days.ge(min_history_days)
        & jump5.le(0.005)
        & p95_abs.le(0.030)
        & opt_oi_x63.ge(0.3)
        & (fut_oi_chg5.gt(0) | opt_volume_x63.le(1.5))
        & rolling_rank.le(20)
        & (pressure_diff.le(0.06) | pressure_diff.isna())
    )
    hsafe_core = h_core & (pressure_diff.notna() | fut_oi_chg5.gt(0))
    addon025 = (
        hist_days.ge(min_history_days)
        & jump5.le(0.025)
        & p95_abs.le(0.030)
        & opt_oi_x63.ge(0.5)
        & (fut_oi_chg5.gt(0) | opt_volume_x63.le(1.3))
        & rolling_rank.le(15)
    )
    out["rule_l1_hsafe_core"] = hsafe_core
    out["rule_l1_hsafe_addon025"] = hsafe_core | addon025
    return out


def l3eff015_target_pct(record: dict[str, object], *, weak_pressure_threshold: float = 0.02) -> float:
    rank = _safe_float(record.get("rolling_cs_rank"))
    shadow = _safe_float(record.get("shadow_score"))
    pressure_diff = _safe_float(record.get("side_iv_pressure_diff"))
    selected_iv_pressure = _safe_float(record.get("selected_side_iv_pressure"))
    premium_margin = _safe_float(record.get("premium_margin"))
    candidate_pm = _safe_float(record.get("candidate_pm_median_252"))
    shadow_stop25 = _safe_float(record.get("shadow_stop25_blend"))
    shadow_stop2 = _safe_float(record.get("shadow_stop2_blend"))
    shadow_pnlprem = _safe_float(record.get("shadow_pnlprem_blend"))
    shadow_prod_n = _safe_float(record.get("shadow_prod_n12"))
    shadow_side_n = _safe_float(record.get("shadow_side_n12"))
    opt_volume_x63 = _safe_float(record.get("opt_side_volume_x63"))
    opt_oi_chg5 = _safe_float(record.get("opt_side_oi_chg5"))
    fut_oi_chg5 = _safe_float(record.get("fut_oi_chg5"))
    weak_pressure = bool(np.isfinite(pressure_diff) and pressure_diff < weak_pressure_threshold)
    option_flow_stress = bool(np.isfinite(opt_volume_x63) and opt_volume_x63 > 1.5 and (not np.isfinite(fut_oi_chg5) or fut_oi_chg5 < 0.03))
    crowded_fast_oi = bool(np.isfinite(opt_oi_chg5) and opt_oi_chg5 > 1.0 and (not np.isfinite(pressure_diff) or pressure_diff < 0.04))
    has_pressure_edge = bool((np.isfinite(pressure_diff) and pressure_diff >= 0.02) or (not np.isfinite(pressure_diff) and np.isfinite(selected_iv_pressure) and selected_iv_pressure >= 0.30))
    has_efficiency_edge = bool((np.isfinite(premium_margin) and premium_margin >= 0.02) or (np.isfinite(candidate_pm) and candidate_pm >= 0.025))
    score = int(np.isfinite(rank) and rank <= 9) + int(np.isfinite(shadow) and shadow >= 0.94) + int(has_pressure_edge) + int(has_efficiency_edge)
    has_shadow_sample = bool(np.isfinite(shadow_prod_n) and shadow_prod_n >= 6)
    severe_path_risk = bool(
        has_shadow_sample
        and (
            (np.isfinite(shadow_stop2) and shadow_stop2 < 0.65)
            or (np.isfinite(shadow_stop25) and shadow_stop25 < 0.75)
            or (np.isfinite(shadow_pnlprem) and shadow_pnlprem < 0.50)
        )
    )
    thin_side_low_efficiency = bool(np.isfinite(shadow_side_n) and shadow_side_n < 3 and not np.isfinite(pressure_diff) and np.isfinite(premium_margin) and premium_margin < 0.012)
    low_eff_low_pressure = bool(np.isfinite(premium_margin) and premium_margin < 0.020 and np.isfinite(selected_iv_pressure) and selected_iv_pressure < 0.30 and (not np.isfinite(pressure_diff) or pressure_diff < 0.03))
    path_risk_without_enough_edge = bool(severe_path_risk and (weak_pressure or not has_efficiency_edge or (np.isfinite(premium_margin) and premium_margin < 0.020)))
    if weak_pressure and (option_flow_stress or crowded_fast_oi):
        return 0.00075
    if path_risk_without_enough_edge or thin_side_low_efficiency:
        return 0.00075
    if low_eff_low_pressure:
        return 0.00100
    if score >= 3:
        return 0.00150
    return 0.00100


def adjust_l4_diff02_l3eff015(
    selected: pd.DataFrame,
    option_data: pd.DataFrame,
    *,
    max_abs_delta: float = 0.08,
    min_oi: float = 1000.0,
    l3_weak_pressure_threshold: float = 0.02,
    l4_weak_pressure_threshold: float = 0.02,
    l4_weak_delta_cap: float = 0.04,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if selected.empty:
        return selected.copy(), pd.DataFrame()
    data = _normalize_options(option_data)
    rows_by_key = {key: group for key, group in data.groupby(["product_key", "trade_date", "expiry_date"], sort=False)}
    adjusted: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    for record in selected.to_dict("records"):
        entry_date = pd.Timestamp(record["entry_date"])
        expiry = pd.Timestamp(record["target_expiry"])
        rows = rows_by_key.get((record["product_key"], entry_date, expiry), pd.DataFrame())
        side = str(record.get("sell_side") or record.get("option_type") or "").upper()
        pressure_diff = _safe_float(record.get("side_iv_pressure_diff"))
        weak_pressure = bool(np.isfinite(pressure_diff) and pressure_diff < l4_weak_pressure_threshold)
        effective_cap = l4_weak_delta_cap if weak_pressure else max_abs_delta
        picked = _choose_contract(rows, side, effective_cap, min_oi) if weak_pressure else pd.Series(record)
        target_pct = l3eff015_target_pct(record, weak_pressure_threshold=l3_weak_pressure_threshold)
        if picked is None:
            skips.append(
                {
                    "entry_date": entry_date,
                    "product": record.get("product_label"),
                    "contract_code": record.get("contract_code"),
                    "reason": "no_adjusted_contract",
                    "extra_contract_mode": "l4_diff02_delta04_l3eff015",
                    "delta_cap": effective_cap,
                    "requested_side": side,
                    "target_premium_pct_override": target_pct,
                    "l3eff015_tier": "boost015" if target_pct >= 0.0015 else "base_or_cut",
                    "l4_diff02_weak_pressure": weak_pressure,
                    "l4_diff02_delta_cap": effective_cap,
                    "l3_weak_pressure_threshold": l3_weak_pressure_threshold,
                    "l4_weak_pressure_threshold": l4_weak_pressure_threshold,
                }
            )
            continue
        row = dict(record)
        for column in ["contract_code", "option_type", "strike", "expiry_date", "target_expiry", "dte", "close", "volume", "close_oi", "implied_vol", "delta", "moneyness", "spot_close", "multiplier", "abs_delta", "gross_premium_cash_1lot", "one_contract_margin", "premium_margin"]:
            if column in picked:
                row[column] = picked.get(column)
        row["entry_date"] = entry_date
        row["trade_date"] = entry_date
        row["product"] = row.get("product_label")
        row["side_rule"] = "l4_diff02_delta04_l3eff015"
        row["extra_contract_mode"] = "l4_diff02_delta04_l3eff015"
        row["target_premium_pct_override"] = l3eff015_target_pct(row, weak_pressure_threshold=l3_weak_pressure_threshold)
        row["l3eff015_tier"] = "boost015" if row["target_premium_pct_override"] >= 0.0015 else "base_or_cut"
        row["l4_diff02_weak_pressure"] = weak_pressure
        row["l4_diff02_delta_cap"] = effective_cap
        row["l3_weak_pressure_threshold"] = l3_weak_pressure_threshold
        row["l4_weak_pressure_threshold"] = l4_weak_pressure_threshold
        adjusted.append(row)
    return pd.DataFrame(adjusted), pd.DataFrame(skips)


def build_current_main_intents(
    option_data: pd.DataFrame,
    flow_panel: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    rule: str = "rule_l1_hsafe_addon025",
    entry_window_calendar_days: int = 7,
    min_history_days: int = 120,
    max_abs_delta: float = 0.08,
    min_oi: float = 1000.0,
    l3_weak_pressure_threshold: float = 0.02,
    l4_weak_pressure_threshold: float = 0.02,
    l4_weak_delta_cap: float = 0.04,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    option_data = _normalize_options(option_data)
    features = build_feature_panel(option_data, max_abs_delta=max_abs_delta, min_oi=min_oi)
    opps = build_product_opportunities(
        option_data,
        start_date=start_date,
        end_date=end_date,
        entry_window_calendar_days=entry_window_calendar_days,
        max_abs_delta=max_abs_delta,
        min_oi=min_oi,
    )
    if opps.empty:
        return opps, opps, pd.DataFrame(), features
    opps = _asof_by_product(opps, features, "entry_date", "feature_date")
    opps, dense = add_cross_section_scores(opps, features)
    if not flow_panel.empty:
        flow = flow_panel.copy()
        flow["trade_date"] = pd.to_datetime(flow["trade_date"], errors="coerce")
        flow["entry_date"] = flow["trade_date"]
        flow["product_label"] = flow["product"].astype(str).str.upper()
        opps = opps.merge(
            flow.drop(columns=["trade_date"], errors="ignore"),
            left_on=["entry_date", "exchange", "product_label", "sell_side"],
            right_on=["entry_date", "exchange", "product", "sell_side"],
            how="left",
            suffixes=("", "_flow"),
        )
        opps = opps.drop(columns=["product_flow"], errors="ignore")
    opps = add_matured_shadow_stats(opps, option_data)
    opps = add_rule_flags(opps, min_history_days=min_history_days)
    if rule not in opps.columns:
        raise KeyError(f"main opportunity rule not found: {rule}")
    selected = opps[opps[rule].fillna(False).astype(bool)].copy()
    adjusted, skips = adjust_l4_diff02_l3eff015(
        selected,
        option_data,
        max_abs_delta=max_abs_delta,
        min_oi=min_oi,
        l3_weak_pressure_threshold=l3_weak_pressure_threshold,
        l4_weak_pressure_threshold=l4_weak_pressure_threshold,
        l4_weak_delta_cap=l4_weak_delta_cap,
    )
    return opps, adjusted, skips, dense
