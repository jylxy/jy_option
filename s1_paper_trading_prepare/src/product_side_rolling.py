"""S1-only rolling product-side panel maintenance.

This module maintains the live paper-trading replacement for the old research
product-side panel.  It uses stored daily option snapshots only, updates by
date partition, and shifts outcome history before computing signal-day buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .diagnostics import write_csv, write_json


ROLLING_PANEL_FILE = "rolling_product_side_panel.csv"
ROLLING_OBSERVATION_FILE = "rolling_product_side_observations.csv"
ROLLING_ADMISSION_PREFIX = "rolling_l1_admission"
ROLLING_MANIFEST_PREFIX = "rolling_product_side_update"
L1_PANEL_REQUIRED_COLUMNS = [
    "date",
    "product",
    "side",
    "historical_retention_score",
    "historical_retention_score_rank_date",
    "historical_retention_score_bucket5_date",
    "tail_cluster_safety_score",
    "tail_cluster_safety_score_rank_date",
    "tail_cluster_safety_score_bucket5_date",
    "product_side_score",
    "product_side_score_rank_date",
    "avg_v3_b6_premium_to_stress_rank",
]


@dataclass(frozen=True)
class RollingProductSideUpdateResult:
    signal_date: str
    observation_rows: int
    panel_rows: int
    admission_rows: int
    matured_observation_rows: int
    observation_path: Path | None
    panel_path: Path | None
    admission_path: Path | None
    manifest_path: Path | None


def _date_tag(signal_date: str) -> str:
    return str(signal_date)[:10].replace("-", "")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _upsert_by_key(existing: pd.DataFrame, new_rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if existing.empty:
        return new_rows.copy()
    if new_rows.empty:
        return existing.copy()
    old = existing.copy()
    marker = new_rows[keys].drop_duplicates()
    old = old.merge(marker.assign(_replace=1), how="left", on=keys)
    old = old[old["_replace"].isna()].drop(columns=["_replace"])
    return pd.concat([old, new_rows], ignore_index=True, sort=False)


def _safe_numeric(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(index=frame.index, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce")


def _safe_text(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series("", index=frame.index, dtype=str)
    return frame[col].fillna("").astype(str)


def _rank_by_date(frame: pd.DataFrame, col: str, higher_good: bool = True) -> pd.Series:
    values = _safe_numeric(frame, col)
    if not higher_good:
        values = -values
    return values.groupby(frame["date"]).rank(pct=True, method="average")


def _mean_existing(frame: pd.DataFrame, cols: list[str]) -> pd.Series:
    existing = [col for col in cols if col in frame.columns]
    if not existing:
        return pd.Series(np.nan, index=frame.index)
    return frame[existing].mean(axis=1, skipna=True)


def ensure_l1_loader_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """Return a rolling panel that can be read by the locked L1 loader."""
    out = panel.copy()
    if "option_type" in out.columns:
        out["option_type"] = out["option_type"].fillna("").astype(str).str.upper().str[:1]
    if "side" in out.columns:
        out["side"] = out["side"].fillna("").astype(str).str.upper().str[:1]
    elif "option_type" in out.columns:
        out["side"] = out["option_type"]
    else:
        out["side"] = ""
    if "option_type" not in out.columns:
        out["option_type"] = out["side"]
    for col in L1_PANEL_REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    if "date" in out.columns:
        out["date"] = out["date"].astype(str).str[:10]
    if "product" in out.columns:
        out["product"] = out["product"].fillna("").astype(str).str.upper().str.strip()
    return out


def l1_loader_readiness(panel: pd.DataFrame, signal_date: str) -> dict[str, Any]:
    """Summarize whether the rolling panel is structurally ready for L1 use."""
    missing_columns = [col for col in L1_PANEL_REQUIRED_COLUMNS if col not in panel.columns]
    if panel.empty or missing_columns:
        day = pd.DataFrame()
    else:
        day = panel[panel["date"].astype(str).str[:10].eq(str(signal_date)[:10])].copy()
    hist = pd.to_numeric(day.get("historical_retention_score_bucket5_date"), errors="coerce")
    tail = pd.to_numeric(day.get("tail_cluster_safety_score_bucket5_date"), errors="coerce")
    score = pd.to_numeric(day.get("product_side_score"), errors="coerce")
    gate_ready = hist.notna() & tail.notna()
    score_ready = score.notna()
    return {
        "l1_loader_required_columns_present": not missing_columns,
        "l1_loader_missing_columns": missing_columns,
        "rolling_panel_day_rows": int(len(day)),
        "rolling_panel_gate_ready_rows": int(gate_ready.sum()) if len(day) else 0,
        "rolling_panel_score_ready_rows": int(score_ready.sum()) if len(day) else 0,
        "rolling_panel_loader_ready": bool(not missing_columns and len(day) > 0),
        "rolling_panel_history_ready": bool(not missing_columns and len(day) > 0 and gate_ready.all()),
    }


def build_daily_product_side_observations(
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    signal_date: str,
) -> pd.DataFrame:
    """Aggregate a signal-day L0 universe into product-side observation rows."""
    cols = [
        "date",
        "product",
        "option_type",
        "candidate_rows",
        "oi_ge1000_rows",
        "total_premium_pool_1lot",
        "oi_ge1000_premium_pool",
        "total_open_interest",
        "total_entry_volume",
        "capacity_premium_pool_proxy",
        "premium_pool_per_candidate",
        "open_interest_per_candidate",
        "avg_abs_delta",
        "avg_implied_vol",
        "avg_vrp_proxy",
        "avg_v3_b6_premium_to_stress_rank",
        "avg_v3_contract_vrp_pct",
        "avg_v3_vrp_quality_score",
        "avg_v3_term_not_inverted",
        "label_v3_retention_10d",
        "label_v3_stop_touch_10d",
        "label_v3_retention_to_expiry_clipped",
        "label_v3_max_adverse_price_ratio_10d",
        "label_v3_product_stop_cluster_with_portfolio",
    ]
    if l0_universe.empty:
        return pd.DataFrame(columns=cols)

    work = l0_universe.copy()
    work["date"] = str(signal_date)[:10]
    work["product"] = _safe_text(work, "product").str.upper().str.strip()
    work["option_type"] = _safe_text(work, "option_type").str.upper().str[:1]
    work = work[work["option_type"].isin(["P", "C"])].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)

    price = _safe_numeric(work, "option_close")
    mult = _safe_numeric(work, "multiplier").fillna(1.0)
    volume = _safe_numeric(work, "volume").fillna(0.0)
    oi = _safe_numeric(work, "open_interest").fillna(0.0)
    abs_delta = _safe_numeric(work, "abs_delta")
    implied_vol = _safe_numeric(work, "implied_vol")
    eligible = work.get("l0_basic_trade_eligible", False)
    if not isinstance(eligible, pd.Series):
        eligible = pd.Series(False, index=work.index)
    eligible = eligible.astype(bool)

    min_oi = float(config.get("s1_min_oi", 1000) or 1000)
    volume_cap = float(config.get("s1_entry_volume_limit_ratio", config.get("volume_limit_pct", 0.10)) or 0.10)
    premium_cash = price * mult
    work["_candidate"] = eligible.astype(int)
    work["_oi_ge_min"] = (eligible & oi.ge(min_oi)).astype(int)
    work["_premium_cash"] = premium_cash.where(eligible, 0.0)
    work["_oi_premium_cash"] = premium_cash.where(eligible & oi.ge(min_oi), 0.0)
    work["_capacity_premium_proxy"] = (premium_cash * volume * volume_cap).where(eligible, 0.0)
    work["_open_interest"] = oi.where(eligible, 0.0)
    work["_entry_volume"] = volume.where(eligible, 0.0)
    work["_abs_delta"] = abs_delta.where(eligible)
    work["_implied_vol"] = implied_vol.where(eligible)

    grouped = work.groupby(["date", "product", "option_type"], dropna=False)
    out = grouped.agg(
        candidate_rows=("_candidate", "sum"),
        oi_ge1000_rows=("_oi_ge_min", "sum"),
        total_premium_pool_1lot=("_premium_cash", "sum"),
        oi_ge1000_premium_pool=("_oi_premium_cash", "sum"),
        total_open_interest=("_open_interest", "sum"),
        total_entry_volume=("_entry_volume", "sum"),
        capacity_premium_pool_proxy=("_capacity_premium_proxy", "sum"),
        avg_abs_delta=("_abs_delta", "mean"),
        avg_implied_vol=("_implied_vol", "mean"),
    ).reset_index()
    candidates = pd.to_numeric(out["candidate_rows"], errors="coerce").replace(0, np.nan)
    out["premium_pool_per_candidate"] = pd.to_numeric(out["total_premium_pool_1lot"], errors="coerce") / candidates
    out["open_interest_per_candidate"] = pd.to_numeric(out["total_open_interest"], errors="coerce") / candidates
    out["avg_vrp_proxy"] = out["avg_implied_vol"]
    out["avg_v3_contract_vrp_pct"] = out["avg_vrp_proxy"]
    out["avg_v3_vrp_quality_score"] = out["premium_pool_per_candidate"]
    out["avg_v3_term_not_inverted"] = np.nan

    proxy_rank = _rank_by_date(out, "capacity_premium_pool_proxy")
    out["avg_v3_b6_premium_to_stress_rank"] = proxy_rank
    for col in cols:
        if col not in out.columns:
            out[col] = np.nan
    return out.loc[:, cols].copy()


def _contract_key_frame(snapshot: pd.DataFrame) -> pd.DataFrame:
    cols = [col for col in ["option_code", "trade_date", "option_close", "option_high", "expiry_date"] if col in snapshot.columns]
    if not cols:
        return pd.DataFrame(columns=["option_code", "trade_date", "option_close", "option_high", "expiry_date"])
    out = snapshot[cols].copy()
    if "option_code" not in out.columns:
        out["option_code"] = ""
    out["option_code"] = out["option_code"].astype(str)
    if "option_close" not in out.columns:
        out["option_close"] = np.nan
    if "option_high" not in out.columns:
        out["option_high"] = out["option_close"]
    if "expiry_date" not in out.columns:
        out["expiry_date"] = ""
    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].astype(str).str[:10]
    if "option_close" in out.columns:
        out["option_close"] = pd.to_numeric(out["option_close"], errors="coerce")
    if "option_high" in out.columns:
        out["option_high"] = pd.to_numeric(out["option_high"], errors="coerce")
    if "expiry_date" in out.columns:
        out["expiry_date"] = out["expiry_date"].astype(str).str[:10]
    return out


def _load_snapshot_for_date(data_dir: Path, date: str) -> pd.DataFrame:
    path = data_dir / "daily_snapshots" / f"option_chain_{_date_tag(date)}.csv"
    return _read_csv(path)


def _available_snapshot_dates(data_dir: Path) -> list[str]:
    out = []
    for path in sorted((data_dir / "daily_snapshots").glob("option_chain_*.csv")):
        tag = path.stem.replace("option_chain_", "")
        if len(tag) == 8 and tag.isdigit():
            out.append(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}")
    return out


def _mature_observation_labels(
    observations: pd.DataFrame,
    data_dir: Path,
    config: dict[str, Any],
    outcome_horizon: int,
) -> pd.DataFrame:
    """Update matured product-side outcome labels from stored daily snapshots."""
    if observations.empty:
        return observations

    out = observations.copy()
    available_dates = _available_snapshot_dates(data_dir)
    if not available_dates:
        return out
    date_pos = {date: idx for idx, date in enumerate(available_dates)}
    max_date = max(available_dates)
    stop_multiple = float(config.get("premium_stop_multiple", 2.5) or 2.5)

    for obs_date in sorted(out["date"].dropna().astype(str).unique()):
        if obs_date not in date_pos:
            continue
        start_idx = date_pos[obs_date]
        end_idx = start_idx + int(outcome_horizon)
        if end_idx >= len(available_dates):
            continue
        future_dates = available_dates[start_idx + 1:end_idx + 1]
        if not future_dates:
            continue

        entry_snapshot = _load_snapshot_for_date(data_dir, obs_date)
        if entry_snapshot.empty:
            continue
        entry = entry_snapshot.copy()
        entry["product"] = _safe_text(entry, "product").str.upper().str.strip()
        entry["option_type"] = _safe_text(entry, "option_type").str.upper().str[:1]
        entry["option_code"] = _safe_text(entry, "option_code")
        entry_price = _safe_numeric(entry, "option_close")
        entry = entry[entry_price.gt(0)].copy()
        if entry.empty:
            continue
        entry["_entry_price"] = _safe_numeric(entry, "option_close")

        future_parts = [_contract_key_frame(_load_snapshot_for_date(data_dir, date)) for date in future_dates]
        future = pd.concat([part for part in future_parts if not part.empty], ignore_index=True, sort=False)
        if future.empty:
            continue
        future_max = future.groupby("option_code").agg(
            future_max_high=("option_high", "max"),
            future_last_close=("option_close", "last"),
        ).reset_index()
        labeled = entry.merge(future_max, how="left", on="option_code")
        max_high = pd.to_numeric(labeled["future_max_high"], errors="coerce")
        last_close = pd.to_numeric(labeled["future_last_close"], errors="coerce")
        entry_px = pd.to_numeric(labeled["_entry_price"], errors="coerce")
        labeled["label_v3_stop_touch_10d"] = max_high.ge(entry_px * stop_multiple).astype(float)
        labeled["label_v3_retention_10d"] = ((entry_px - last_close) / entry_px).clip(lower=0.0, upper=1.0)
        labeled["label_v3_max_adverse_price_ratio_10d"] = ((max_high / entry_px) - 1.0).clip(lower=0.0)
        labeled["label_v3_retention_to_expiry_clipped"] = labeled["label_v3_retention_10d"]

        side_labels = labeled.groupby(["product", "option_type"], dropna=False).agg(
            label_v3_retention_10d=("label_v3_retention_10d", "mean"),
            label_v3_stop_touch_10d=("label_v3_stop_touch_10d", "mean"),
            label_v3_retention_to_expiry_clipped=("label_v3_retention_to_expiry_clipped", "mean"),
            label_v3_max_adverse_price_ratio_10d=("label_v3_max_adverse_price_ratio_10d", "mean"),
        ).reset_index()
        side_labels["date"] = obs_date
        side_labels["label_v3_product_stop_cluster_with_portfolio"] = side_labels["label_v3_stop_touch_10d"]

        label_cols = [
            "label_v3_retention_10d",
            "label_v3_stop_touch_10d",
            "label_v3_retention_to_expiry_clipped",
            "label_v3_max_adverse_price_ratio_10d",
            "label_v3_product_stop_cluster_with_portfolio",
        ]
        merged = out.merge(
            side_labels[["date", "product", "option_type", *label_cols]],
            how="left",
            on=["date", "product", "option_type"],
            suffixes=("", "_new"),
        )
        for col in label_cols:
            new_col = f"{col}_new"
            if new_col in merged.columns:
                out[col] = merged[new_col].where(merged[new_col].notna(), merged[col])

    out["outcome_matured"] = out["date"].astype(str).map(
        lambda date: int(date in date_pos and date_pos[date] + int(outcome_horizon) < len(available_dates) and max_date > date)
    )
    return out


def _add_hist_and_scores(observations: pd.DataFrame, hist_window: int) -> pd.DataFrame:
    if observations.empty:
        return ensure_l1_loader_columns(observations)
    out = observations.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out["product"] = out["product"].astype(str).str.upper().str.strip()
    out["option_type"] = out["option_type"].astype(str).str.upper().str[:1]
    out = out.sort_values(["product", "option_type", "date"]).reset_index(drop=True)
    group_keys = ["product", "option_type"]

    def shifted_roll(col: str, min_periods: int = 10) -> pd.Series:
        if col not in out.columns:
            return pd.Series(np.nan, index=out.index)
        return out.groupby(group_keys, dropna=False)[col].transform(
            lambda series: pd.to_numeric(series, errors="coerce").shift(1).rolling(
                hist_window,
                min_periods=min(min_periods, hist_window),
            ).mean()
        )

    out["hist_retention_10d_63d"] = shifted_roll("label_v3_retention_10d")
    out["label_stop_avoid_10d"] = 1.0 - _safe_numeric(out, "label_v3_stop_touch_10d")
    out["hist_stop_avoid_10d_63d"] = shifted_roll("label_stop_avoid_10d")
    out["hist_expiry_retention_63d"] = shifted_roll("label_v3_retention_to_expiry_clipped")
    out["hist_max_adverse_10d_63d"] = shifted_roll("label_v3_max_adverse_price_ratio_10d")
    out["hist_stop_cluster_63d"] = shifted_roll("label_v3_product_stop_cluster_with_portfolio")

    capacity_frame = pd.DataFrame(index=out.index)
    for col in ["oi_ge1000_rows", "oi_ge1000_premium_pool", "capacity_premium_pool_proxy"]:
        if col in out.columns:
            capacity_frame[col] = _rank_by_date(out, col)
    out["capacity_score"] = _mean_existing(capacity_frame, list(capacity_frame.columns))

    premium_frame = pd.DataFrame(index=out.index)
    for col in ["avg_v3_contract_vrp_pct", "avg_v3_vrp_quality_score", "avg_v3_b6_premium_to_stress_rank"]:
        if col in out.columns:
            premium_frame[col] = _rank_by_date(out, col)
    out["premium_quality_score"] = _mean_existing(premium_frame, list(premium_frame.columns))

    hist_frame = pd.DataFrame(index=out.index)
    for col in ["hist_retention_10d_63d", "hist_stop_avoid_10d_63d", "hist_expiry_retention_63d"]:
        if col in out.columns:
            hist_frame[col] = _rank_by_date(out, col)
    if "hist_max_adverse_10d_63d" in out.columns:
        hist_frame["hist_max_adverse_10d_63d"] = _rank_by_date(out, "hist_max_adverse_10d_63d", higher_good=False)
    out["historical_retention_score"] = _mean_existing(hist_frame, list(hist_frame.columns))

    out["tail_cluster_safety_score"] = _rank_by_date(out, "hist_stop_cluster_63d", higher_good=False)
    out["regime_safety_score"] = np.nan
    out["product_side_score"] = (
        0.25 * out["capacity_score"]
        + 0.30 * out["premium_quality_score"]
        + 0.20 * out["historical_retention_score"]
    )
    out["product_side_score"] = out["product_side_score"].where(out["tail_cluster_safety_score"].isna(),
        0.90 * out["product_side_score"] + 0.10 * out["tail_cluster_safety_score"]
    )

    for col in [
        "historical_retention_score",
        "tail_cluster_safety_score",
        "product_side_score",
        "avg_v3_b6_premium_to_stress_rank",
    ]:
        if col not in out.columns:
            continue
        out[f"{col}_rank_date"] = _rank_by_date(out, col)
        out[f"{col}_bucket5_date"] = np.ceil(out[f"{col}_rank_date"] * 5.0).clip(1, 5)
    return ensure_l1_loader_columns(out)


def build_rolling_l1_admission(panel: pd.DataFrame, config: dict[str, Any], signal_date: str) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    panel = ensure_l1_loader_columns(panel)
    date = str(signal_date)[:10]
    day = panel[panel["date"].astype(str).str[:10].eq(date)].copy()
    if day.empty:
        return day
    hist = pd.to_numeric(day.get("historical_retention_score_bucket5_date"), errors="coerce")
    tail = pd.to_numeric(day.get("tail_cluster_safety_score_bucket5_date"), errors="coerce")
    min_hist = float(config.get("s1_l1_min_hist_bucket", 3) or 3)
    min_tail = float(config.get("s1_l1_min_tail_bucket", 3) or 3)
    day["l1_hist_bucket"] = hist
    day["l1_tail_bucket"] = tail
    day["l1_missing_panel"] = hist.isna() | tail.isna()
    day["l1_pass_gate"] = hist.ge(min_hist) & tail.ge(min_tail) & ~day["l1_missing_panel"]
    day["l1_min_hist_bucket"] = min_hist
    day["l1_min_tail_bucket"] = min_tail
    return day


def update_rolling_product_side_panel(
    signal_date: str,
    *,
    l0_universe: pd.DataFrame,
    config: dict[str, Any],
    data_dir: Path,
    hist_window: int = 63,
    outcome_horizon: int = 10,
    write_outputs: bool = True,
) -> RollingProductSideUpdateResult:
    """Upsert one date and refresh the full rolling S1 product-side panel."""
    date = str(signal_date)[:10]
    product_side_dir = data_dir / "product_side_panel"
    manifest_dir = data_dir / "manifests"
    observation_path = product_side_dir / ROLLING_OBSERVATION_FILE
    panel_path = product_side_dir / ROLLING_PANEL_FILE
    admission_path = product_side_dir / f"{ROLLING_ADMISSION_PREFIX}_{_date_tag(date)}.csv"
    manifest_path = manifest_dir / f"{ROLLING_MANIFEST_PREFIX}_{_date_tag(date)}.json"

    existing = _read_csv(observation_path)
    new_observations = build_daily_product_side_observations(l0_universe, config, date)
    observations = _upsert_by_key(existing, new_observations, ["date", "product", "option_type"])
    observations = _mature_observation_labels(observations, data_dir, config, outcome_horizon)
    panel = _add_hist_and_scores(observations, hist_window)
    panel = ensure_l1_loader_columns(panel)
    admission = build_rolling_l1_admission(panel, config, date)
    matured_rows = int(pd.to_numeric(observations.get("outcome_matured", 0), errors="coerce").fillna(0).sum())
    readiness = l1_loader_readiness(panel, date)

    if write_outputs:
        write_csv(observation_path, observations)
        write_csv(panel_path, panel)
        write_csv(admission_path, admission)
        write_json(
            manifest_path,
            {
                "signal_date": date,
                "observation_rows": int(len(observations)),
                "new_observation_rows": int(len(new_observations)),
                "panel_rows": int(len(panel)),
                "admission_rows": int(len(admission)),
                "matured_observation_rows": matured_rows,
                **readiness,
                "hist_window": int(hist_window),
                "outcome_horizon": int(outcome_horizon),
                "panel_path": str(panel_path),
                "admission_path": str(admission_path),
                "no_future_function_note": (
                    "Signal-day hist_* features use shifted rolling outcomes. "
                    "Outcome labels are updated only after stored future snapshots "
                    "cover the configured horizon."
                ),
            },
        )
    else:
        observation_path = panel_path = admission_path = manifest_path = None

    return RollingProductSideUpdateResult(
        signal_date=date,
        observation_rows=int(len(observations)),
        panel_rows=int(len(panel)),
        admission_rows=int(len(admission)),
        matured_observation_rows=matured_rows,
        observation_path=observation_path,
        panel_path=panel_path,
        admission_path=admission_path,
        manifest_path=manifest_path,
    )
