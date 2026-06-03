"""Signal-only feature helpers shared by PIT panel builders.

The daily Toolkit snapshots can carry missing IV on otherwise valid option
rows. The old research intermediate tables used a fully populated IV field, so
signal features that aggregate IV should work from a deterministic signal IV
without mutating the raw snapshot value.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from .paths import ensure_server_deploy_importable


SIGNAL_IV_COL = "implied_vol_signal"
SIGNAL_IV_SOURCE_COL = "implied_vol_signal_source"
DEFAULT_IV_MIN = 0.01
DEFAULT_IV_MAX = 2.50


def signal_iv_col(frame: pd.DataFrame) -> str:
    """Return the preferred IV column for signal construction."""
    return SIGNAL_IV_COL if SIGNAL_IV_COL in frame.columns else "implied_vol"


def _first_existing(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for column in candidates:
        if column in frame.columns:
            return column
    return None


def ensure_signal_iv(
    frame: pd.DataFrame,
    *,
    price_col: str | None = None,
    output_col: str = SIGNAL_IV_COL,
    source_col: str = "implied_vol",
    iv_min: float = DEFAULT_IV_MIN,
    iv_max: float = DEFAULT_IV_MAX,
) -> pd.DataFrame:
    """Return a copy with `output_col` filled from raw IV or recomputed IV.

    The function is intentionally meant to be called after cheap structural
    filters such as DTE/moneyness/delta. That keeps daily appends fast while
    still matching the old research tables that had populated IV on ATM rows.
    """
    if frame.empty:
        out = frame.copy()
        if output_col not in out.columns:
            out[output_col] = pd.Series(dtype=float)
        if SIGNAL_IV_SOURCE_COL not in out.columns:
            out[SIGNAL_IV_SOURCE_COL] = pd.Series(dtype=object)
        return out

    out = frame.copy()
    if source_col in out.columns:
        raw_iv = pd.to_numeric(out[source_col], errors="coerce")
    else:
        raw_iv = pd.Series(np.nan, index=out.index, dtype=float)
    valid_raw = raw_iv.gt(iv_min) & raw_iv.lt(iv_max)
    out[output_col] = raw_iv.where(valid_raw)
    out[SIGNAL_IV_SOURCE_COL] = np.where(valid_raw, "snapshot", "")

    price_name = price_col or _first_existing(out, ("entry_price", "close", "option_close"))
    if price_name is None:
        return out

    missing = out[output_col].isna()
    if not missing.any():
        return out

    required = {
        price_name: pd.to_numeric(out[price_name], errors="coerce"),
        "spot_close": pd.to_numeric(out.get("spot_close"), errors="coerce"),
        "strike": pd.to_numeric(out.get("strike"), errors="coerce"),
        "dte": pd.to_numeric(out.get("dte"), errors="coerce"),
    }
    option_type = out.get("option_type", pd.Series("", index=out.index)).fillna("").astype(str).str.upper().str[0]
    recalc_mask = (
        missing
        & required[price_name].gt(0)
        & required["spot_close"].gt(0)
        & required["strike"].gt(0)
        & required["dte"].gt(0)
        & option_type.isin(["P", "C"])
    )
    if not recalc_mask.any():
        return out

    calc_frame = out.loc[recalc_mask].copy()
    calc_frame["_signal_iv_price"] = required[price_name].loc[recalc_mask]
    try:
        ensure_server_deploy_importable()
        from option_calc import calc_iv_batch

        filled = pd.to_numeric(
            calc_iv_batch(
                calc_frame,
                price_col="_signal_iv_price",
                spot_col="spot_close",
                strike_col="strike",
                dte_col="dte",
                otype_col="option_type",
                exchange_col="exchange",
            ),
            errors="coerce",
        )
    except Exception:
        filled = pd.Series(np.nan, index=calc_frame.index, dtype=float)

    valid_filled = filled.gt(iv_min) & filled.lt(iv_max)
    out.loc[filled.index[valid_filled], output_col] = filled.loc[valid_filled]
    out.loc[filled.index[valid_filled], SIGNAL_IV_SOURCE_COL] = "recomputed"
    return out
