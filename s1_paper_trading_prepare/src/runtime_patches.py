"""Runtime adapters that keep paper-trading changes outside the historical engine."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


_SCORING_PATCHED = False
_TOOLKIT_PATCHED = False


def _numeric_column(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col in frame.columns:
        return pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return pd.Series(default, index=frame.index, dtype=float)


def _rank_score(values: pd.Series, direction: str, missing: float) -> pd.Series:
    known = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if known.notna().sum() <= 1 or known.nunique(dropna=True) <= 1:
        return pd.Series(missing, index=values.index, dtype=float)
    ranks = known.rank(method="average", pct=True)
    if direction == "low":
        ranks = 1.0 - ranks
    return (100.0 * ranks).fillna(missing).clip(0.0, 100.0)


def _paper_l4_custom_score(frame: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Score and optionally gate L4 candidates without replacing the engine module."""
    params = params or {}
    if frame is None or frame.empty or not bool(params.get("l4_custom_score_enabled", False)):
        return frame
    components = params.get("l4_custom_score_components") or []
    if not isinstance(components, (list, tuple)) or not components:
        return frame

    c = frame.copy()
    missing = float(params.get("l4_custom_missing_score", 50.0) or 50.0)
    total = pd.Series(0.0, index=c.index, dtype=float)
    weight_sum = 0.0
    for component in components:
        if not isinstance(component, dict):
            continue
        field = str(component.get("field", "") or "").strip()
        if not field:
            continue
        try:
            weight = float(component.get("weight", 0.0) or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0.0:
            continue
        direction = str(component.get("direction", "high") or "high").lower()
        if direction not in {"high", "low"}:
            direction = "high"
        score_col = f"l4_custom_{field}_score"
        c[score_col] = _rank_score(_numeric_column(c, field), direction, missing)
        total += weight * c[score_col]
        weight_sum += weight

    if weight_sum <= 0.0:
        return c

    c["l4_custom_score"] = (total / weight_sum).clip(0.0, 100.0)
    keep_quantile = float(params.get("l4_custom_keep_quantile", 1.0) or 1.0)
    keep_quantile = min(max(keep_quantile, 0.0), 1.0)
    drop_quantile = 1.0 - keep_quantile
    min_count = int(params.get("l4_custom_gate_min_count", 5) or 5)
    min_keep = int(params.get("l4_custom_gate_min_keep", 1) or 1)
    missing_policy = str(params.get("l4_custom_gate_missing_policy", "allow") or "allow").lower()

    if drop_quantile > 0.0:
        scores = pd.to_numeric(c["l4_custom_score"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        known = scores.notna()
        if int(known.sum()) >= max(min_count, min_keep + 1) and scores[known].nunique(dropna=True) >= 2:
            known_scores = scores[known]
            ranks = known_scores.rank(method="first", pct=True)
            bad_count = int(np.floor(int(known.sum()) * drop_quantile + 1e-9))
            bad_count = min(max(bad_count, 1), max(int(known.sum()) - min_keep, 0))
            keep = pd.Series(True, index=c.index)
            if bad_count > 0:
                bad_index = known_scores.sort_values(kind="mergesort").index[:bad_count]
                keep.loc[bad_index] = False
            if missing_policy == "skip":
                keep = keep & scores.notna()
            c["l4_custom_gate_rank"] = np.nan
            c.loc[ranks.index, "l4_custom_gate_rank"] = ranks
            c["l4_custom_gate_keep"] = keep.astype(int)
            if int(keep.sum()) >= min_keep:
                c = c[keep].copy()
                if c.empty:
                    return c

    mode = str(params.get("l4_custom_score_mode", "replace_b6") or "replace_b6").lower()
    if mode in {"gate_only", "gate_then_b6", "diagnostic_only"}:
        return c

    c["b6_contract_score"] = c["l4_custom_score"]
    c["quality_score"] = c["b6_contract_score"]
    return c


def install_s1_paper_runtime_patches() -> None:
    """Install config-gated S1 paper-trading runtime adapters."""
    global _SCORING_PATCHED, _TOOLKIT_PATCHED

    if not _SCORING_PATCHED:
        import s1_contract_scoring

        s1_contract_scoring._apply_s1_l4_custom_score = _paper_l4_custom_score
        _SCORING_PATCHED = True

    if _TOOLKIT_PATCHED:
        return

    try:
        import toolkit_minute_engine
    except ModuleNotFoundError as exc:
        if exc.name == "toolkit":
            return
        raise

    original_b6_params = toolkit_minute_engine.ToolkitMinuteEngine._s1_b6_params

    def patched_b6_params(self):
        params = original_b6_params(self)
        cfg = self.config
        params.update(
            {
                "l4_custom_score_mode": cfg.get("s1_l4_custom_score_mode", "replace_b6"),
                "l4_custom_keep_quantile": float(cfg.get("s1_l4_custom_keep_quantile", 1.0) or 1.0),
                "l4_custom_gate_min_count": int(cfg.get("s1_l4_custom_gate_min_count", 5) or 5),
                "l4_custom_gate_min_keep": int(cfg.get("s1_l4_custom_gate_min_keep", 1) or 1),
                "l4_custom_gate_missing_policy": cfg.get("s1_l4_custom_gate_missing_policy", "allow"),
            }
        )
        return params

    toolkit_minute_engine.ToolkitMinuteEngine._s1_b6_params = patched_b6_params
    _TOOLKIT_PATCHED = True
