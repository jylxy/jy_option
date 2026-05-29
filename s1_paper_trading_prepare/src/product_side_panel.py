"""Product-side panel admission audit for S1 L1."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config_snapshot import load_effective_config
from .paths import REPO_ROOT


def load_panel(config: dict) -> pd.DataFrame:
    path = Path(config.get("s1_l1_product_side_panel_path", ""))
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        return pd.DataFrame()
    panel = pd.read_csv(path)
    if "product" in panel.columns:
        panel["product"] = panel["product"].astype(str).str.upper().str.strip()
    if "option_type" in panel.columns:
        panel["option_type"] = panel["option_type"].astype(str).str.upper().str[:1]
    return panel


def audit_l1_admission(signal_date: str, config_path: str | Path | None = None) -> pd.DataFrame:
    """Return Q3/Q3 L1 pass/fail rows for a signal date."""
    snapshot = load_effective_config(config_path)
    config = snapshot.config
    panel = load_panel(config)
    if panel.empty:
        return pd.DataFrame()
    date_key = str(signal_date)[:10]
    day = panel[panel["date"].astype(str).str[:10].eq(date_key)].copy()
    hist_col = "historical_retention_score_bucket5_date"
    tail_col = "tail_cluster_safety_score_bucket5_date"
    min_hist = float(config.get("s1_l1_min_hist_bucket", 3) or 3)
    min_tail = float(config.get("s1_l1_min_tail_bucket", 3) or 3)
    day["l1_hist_bucket"] = pd.to_numeric(day.get(hist_col), errors="coerce")
    day["l1_tail_bucket"] = pd.to_numeric(day.get(tail_col), errors="coerce")
    day["l1_pass_gate"] = day["l1_hist_bucket"].ge(min_hist) & day["l1_tail_bucket"].ge(min_tail)
    day["l1_min_hist_bucket"] = min_hist
    day["l1_min_tail_bucket"] = min_tail
    return day

