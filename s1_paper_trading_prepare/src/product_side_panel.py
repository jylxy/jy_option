"""Product-side panel admission audit for S1 L1."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config_snapshot import load_effective_config
from .paths import REPO_ROOT
from .product_side_rolling import ensure_l1_gate_columns, l1_gate_bucket_columns


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


def audit_l1_admission_from_panel(signal_date: str, config: dict, panel: pd.DataFrame) -> pd.DataFrame:
    """Return Q3/Q3 L1 pass/fail rows from a preloaded product-side panel."""
    if panel.empty:
        return pd.DataFrame()
    date_key = str(signal_date)[:10]
    panel = ensure_l1_gate_columns(panel)
    day = panel[panel["date"].astype(str).str[:10].eq(date_key)].copy()
    primary_col, secondary_col = l1_gate_bucket_columns(config)
    min_primary = float(config.get("s1_l1_min_primary_bucket", 3) or 3)
    min_secondary = float(config.get("s1_l1_min_secondary_bucket", 3) or 3)
    day["l1_primary_bucket"] = pd.to_numeric(day.get(primary_col), errors="coerce")
    day["l1_secondary_bucket"] = pd.to_numeric(day.get(secondary_col), errors="coerce")
    day["l1_pass_gate"] = day["l1_primary_bucket"].ge(min_primary) & day["l1_secondary_bucket"].ge(min_secondary)
    day["l1_min_primary_bucket"] = min_primary
    day["l1_min_secondary_bucket"] = min_secondary
    day["l1_primary_bucket_col"] = primary_col
    day["l1_secondary_bucket_col"] = secondary_col
    day["l1_tail_bucket"] = day["l1_primary_bucket"]
    day["l1_hist_bucket"] = day["l1_secondary_bucket"]
    return day


def audit_l1_admission(signal_date: str, config_path: str | Path | None = None) -> pd.DataFrame:
    """Return Q3/Q3 L1 pass/fail rows for a signal date."""
    snapshot = load_effective_config(config_path)
    config = snapshot.config
    panel = load_panel(config)
    return audit_l1_admission_from_panel(signal_date, config, panel)
