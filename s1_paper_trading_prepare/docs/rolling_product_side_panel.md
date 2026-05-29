# Rolling Product-Side Panel

This document records the live S1 product-side panel path used for paper-trading tracking.  It is not yet the source of truth for order generation; current orders still use the locked mainline panel so that backtest parity is preserved.

## Objective

The rolling panel is the daily-updated replacement candidate for `s1_l1_product_side_panel_path`.  It must be built only from stored S1 daily Toolkit snapshots and shifted historical outcomes.

The daily updater writes:

- `data/product_side_panel/rolling_contract_shadow_observations.csv`
- `data/product_side_panel/contract_shadow/contract_shadow_fields_YYYYMMDD.csv`
- `data/product_side_panel/rolling_product_side_observations.csv`
- `data/product_side_panel/rolling_product_side_panel.csv`
- `data/product_side_panel/rolling_l1_admission_YYYYMMDD.csv`
- `data/manifests/rolling_product_side_update_YYYYMMDD.json`

The contract-shadow files are the audit layer for full-shadow V3/B6/VRP/regime
fields.  They are computed only from stored Toolkit daily snapshots and prior
snapshot history, then aggregated into the product-side panel.

## Prehistory Warmup

Full-shadow rolling percentiles and HAR inputs need history before the first
formal tracking date.  Use `--prehistory-start-date` to fetch or reuse older
Toolkit snapshots without writing formal L0/L1 partitions for those warmup
dates:

```powershell
python .\s1_paper_trading_prepare\scripts\update_daily_data.py `
  --signal-date 2022-04-01 `
  --prehistory-start-date 2021-09-01 `
  --rebuild-contract-history
```

The first formal signal date then rebuilds contract-shadow history once from all
stored warmup and signal snapshots.  Later daily updates can omit
`--rebuild-contract-history` and only append the new date.

Latest h200 warmup check:

```text
signal_date: 2022-04-01
prehistory_start_date: 2021-09-01
prehistory_dates: 164
rolling_panel_rows: 6970
contract_shadow_observation_rows: 1421227
research_scoring_missing_fields: []
March 2022 rolling-vs-locked gate_match_rate: 93.27%
```

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

The rolling label refresh uses the research convention for matured rows:

- `retention_10d = 1 - T+10 close / entry_price`
- `max_adverse_price_ratio_10d = max_future_high / entry_price`
- expiry retention is written only after expiry can be observed from stored snapshots

Full-shadow rolling percentiles and z-scores also use prior dates only.  HAR
forecasts use the original research embargo: a signal date can train only on
targets whose forward horizon has already ended before that signal date.

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
