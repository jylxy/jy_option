"""Budget ledger extraction helpers for generated S1 orders."""

from __future__ import annotations

import pandas as pd


LEDGER_COLUMNS = [
    "signal_date",
    "product",
    "option_type",
    "quantity",
    "net_premium_cash",
    "margin",
    "l1_hist_bucket",
    "l1_tail_bucket",
    "l1_sort_bucket",
    "l1_budget_mult",
    "l1_side_final_budget_pct",
    "l3_ledger_enabled",
    "l3_ledger_base_scope",
    "l3_ledger_base_side_budget_pct",
    "l3_ledger_total_raw_budget_pct",
    "l3_ledger_total_final_budget_pct",
    "l3_ledger_unused_budget_pct",
    "side_budget_mult",
]


def ledger_view(orders: pd.DataFrame) -> pd.DataFrame:
    if orders is None or orders.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    cols = [col for col in LEDGER_COLUMNS if col in orders.columns]
    return orders.loc[:, cols].copy()


def summarize_ledger(orders: pd.DataFrame) -> pd.DataFrame:
    view = ledger_view(orders)
    if view.empty:
        return pd.DataFrame()
    for col in ("quantity", "net_premium_cash", "margin"):
        if col in view.columns:
            view[col] = pd.to_numeric(view[col], errors="coerce").fillna(0.0)
    return (
        view.groupby(["signal_date", "product", "option_type"], as_index=False)
        .agg(
            orders=("quantity", "count"),
            quantity=("quantity", "sum"),
            net_premium_cash=("net_premium_cash", "sum"),
            margin=("margin", "sum"),
            l1_hist_bucket=("l1_hist_bucket", "first"),
            l1_tail_bucket=("l1_tail_bucket", "first"),
            l1_sort_bucket=("l1_sort_bucket", "first"),
            l1_budget_mult=("l1_budget_mult", "first"),
            l3_ledger_enabled=("l3_ledger_enabled", "first"),
            l3_ledger_total_final_budget_pct=("l3_ledger_total_final_budget_pct", "first"),
        )
        .sort_values(["signal_date", "product", "option_type"], kind="mergesort")
    )

