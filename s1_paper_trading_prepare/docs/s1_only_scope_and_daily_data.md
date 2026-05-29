# S1-only scope and daily data refresh

## Scope

This paper-trading workspace is for S1 only.

Keep:

- Locked S1 mainline configs under `configs/source_mainline/`
- S1 order-generation adapter, diagnostics, config audit, and compare scripts
- S1-relevant data refresh code that reads Toolkit data
- In-memory no-op import guards only when the historical engine imports unrelated modules at module load time

Do not keep:

- Any non-S1 side-strategy implementation code, config branches, reports, or compatibility files
- Research notebooks, exploratory scripts, or old full-shadow reports that are not required by the locked S1 config
- Historical output CSV/parquet/log files in Git
- Credentials, local database dumps, or large Toolkit extracts

## GitHub deploy policy

The company GitHub repository can be cleared and replaced by this clean project.
Before pushing, confirm the target repository and branch, then commit only
`s1_paper_trading_prepare/` content.  Runtime folders under `output/` and
`data/` are ignored except for `.gitkeep` anchors.

## Daily Toolkit data refresh

The paper workflow should run in this order:

1. `scripts/update_daily_data.py --signal-date YYYY-MM-DD`
2. `scripts/generate_orders.py --signal-date YYYY-MM-DD --replay-start-date <state-start>`
3. Manual review and export of `output/orders/orders_*.csv`
4. Write back fills, partial fills, cancellations, NAV, and positions for the next day

The refresh step currently materializes:

- Daily option-chain snapshot from Toolkit daily aggregation
- S1 L0 contract universe computed from the stored option-chain snapshot
- S1 rolling product-side observations and panel
- S1 rolling L1 product-side admission view for the signal date
- A manifest with config hash, source table notes, row counts, and required runtime tables

Existing date partitions are reused by default.  Only missing dates are fetched
from Toolkit; use `--force` to deliberately rebuild an existing partition.

The order-generation adapter still uses the locked mainline panel for historical
parity checks until the rolling panel has enough matured history and is approved
as the live panel.  The rolling updater itself is S1-only: it uses Toolkit daily
snapshots, updates only matured historical outcome rows, and exposes feature or
lagged `hist_*` fields for admission.  Forward `label_*` columns must never enter
live signal decisions.
