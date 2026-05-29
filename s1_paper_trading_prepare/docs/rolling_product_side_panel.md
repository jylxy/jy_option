# Rolling Product-Side Panel

This document records the live S1 product-side panel path used for paper-trading tracking.  It is not yet the source of truth for order generation; current orders still use the locked mainline panel so that backtest parity is preserved.

## Objective

The rolling panel is the daily-updated replacement candidate for `s1_l1_product_side_panel_path`.  It must be built only from stored S1 daily Toolkit snapshots and shifted historical outcomes.

The daily updater writes:

- `data/product_side_panel/rolling_product_side_observations.csv`
- `data/product_side_panel/rolling_product_side_panel.csv`
- `data/product_side_panel/rolling_l1_admission_YYYYMMDD.csv`
- `data/manifests/rolling_product_side_update_YYYYMMDD.json`

## Loader Contract

`rolling_product_side_panel.csv` is written with the columns required by the locked L1 loader:

- `date`
- `product`
- `side`
- `historical_retention_score`
- `historical_retention_score_rank_date`
- `historical_retention_score_bucket5_date`
- `tail_cluster_safety_score`
- `tail_cluster_safety_score_rank_date`
- `tail_cluster_safety_score_bucket5_date`
- `product_side_score`
- `product_side_score_rank_date`
- `avg_v3_b6_premium_to_stress_rank`

The source aggregation keeps `option_type`; `side` is the loader-compatible alias.

## No-Future Rule

Outcome labels are updated only after enough stored future snapshots exist for the configured horizon.  Signal-day features use shifted rolling windows, so the current row's outcome is not included in its own score or bucket.

The manifest separates two states:

- `rolling_panel_loader_ready`: required columns and the signal-day rows exist.
- `rolling_panel_history_ready`: signal-day rows also have non-missing shifted L1 buckets.

Early tracking windows can be loader-ready but not history-ready.  That is expected until enough daily snapshots have accumulated.

## Audit Command

Compare the rolling candidate panel with the locked mainline panel:

```powershell
python .\s1_paper_trading_prepare\scripts\compare_rolling_product_side_panel.py --signal-date 2022-04-14
```

For a window:

```powershell
python .\s1_paper_trading_prepare\scripts\compare_rolling_product_side_panel.py --start-date 2022-01-04 --end-date 2022-06-30
```

The script writes a diff CSV and summary JSON under `output/audit/`.  It does not change order generation.
