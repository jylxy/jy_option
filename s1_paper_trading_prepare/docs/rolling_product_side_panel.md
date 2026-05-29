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

Stored daily snapshots include the Toolkit minute-aggregated option `vwap`
column.  Existing snapshots that were written before this field existed are
enriched in place by fetching only the missing VWAP partition for that date.
Signal and label prices prefer `vwap`, with `option_close` used only as a
fallback when VWAP is unavailable.

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
  --prehistory-start-date 2021-07-01 `
  --rebuild-contract-history
```

The first formal signal date then rebuilds contract-shadow history once from all
stored warmup and signal snapshots.  Later daily updates can omit
`--rebuild-contract-history` and only append the new date.  Passing
`--prehistory-start-date` by itself no longer forces a full replay; historical
contract-shadow rebuilds require the explicit `--rebuild-contract-history`
flag.

Latest h200 warmup check:

```text
signal_date: 2022-04-01
prehistory_start_date: 2021-07-01
prehistory_dates: 217 stored snapshot dates
rolling_panel_rows: 9172
contract_shadow_observation_rows: 1015827
full_shadow_max_dte: 120
March 2022 rolling-vs-locked gate_match_rate: 99.83%
full warmup rebuild runtime: 213.75s on h200
daily no-rebuild runtime with prehistory argument: 13.97s on h200
March 2022 research-parity check after future snapshots and maturity fix:
  bucket_mismatch=28, l1_gate_mismatch=0
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

- `entry_price` is taken from the next stored trading snapshot, matching the T+1 research-entry convention.
- the T+1 entry price uses Toolkit daily VWAP when available, matching the
  locked full-shadow research tape; close is only a missing-data fallback.
- `retention_10d = 1 - exit_price / entry_price`, with the exit window using the contract's next 10 valid daily observations after that T+1 entry date.
- `max_adverse_price_ratio_10d = max_future_high / entry_price`, with highs measured over the contract's valid daily observations after that T+1 entry date.
- contracts that are signal-eligible but not T+1 entry-feasible remain in the label aggregation with path labels as missing; this preserves the research product stop-cluster convention where all-missing stop rates map to a zero stop flag.
- expiry retention uses a product-level expiry spot map with a 10-calendar-day backward asof tolerance, matching the report-slim full-shadow convention where available; labels whose expiry outcome is not yet observable remain missing.
- product-level expiry spot follows the report-slim convention of taking the
  first full-shadow candidate-tape spot for each product/date; this is retained
  for backtest parity, even though it is less economically clean than a
  contract-underlying settlement lookup.

The locked research panel is still treated as a parity target, not as a
point-in-time data source for paper trading.  Its generation script keeps
forward `label_*` fields for research and computes shifted historical features
from those labels.  The rolling panel therefore remains the safer live path:
labels become usable only when the required future observations are actually
stored.

Full-shadow rolling percentiles and z-scores also use prior dates only.  HAR
forecasts use the original research embargo: a signal date can train only on
targets whose forward horizon has already ended before that signal date.

When exact `underlying_code` history is too short for HAR/VRP/regime fields,
the updater now fills missing history features from the same product's stored
spot series.  This is a point-in-time fallback for the research continuous
contract / alias behavior; it never overwrites exact underlying-code values and
does not read future dates.

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
