# S1 paper-trading data contract

## Daily inputs

The paper order generator should materialize its required inputs every signal
day before generating orders.

| Table | Source | Current file |
| --- | --- | --- |
| Daily option-chain snapshot | Toolkit via `ToolkitDayLoader` | `data/daily_snapshots/option_chain_YYYYMMDD.csv` |
| S1 L0 contract universe | computed from daily option-chain snapshot | `data/derived/s1_l0_contract_universe_YYYYMMDD.csv` |
| S1 rolling product-side observations | computed from S1 L0 contract universe | `data/product_side_panel/rolling_product_side_observations.csv` |
| S1 rolling product-side panel | shifted rolling outcomes and date buckets | `data/product_side_panel/rolling_product_side_panel.csv` |
| S1 rolling L1 admission | computed from rolling product-side panel | `data/product_side_panel/rolling_l1_admission_YYYYMMDD.csv` |
| S1 table dependency registry | audited S1 mainline table list | `configs/s1_table_registry.json` |
| Contract metadata, multiplier, exchange | `ContractInfo` | included in the daily snapshot and manifest |
| Spot/underlying daily features | Toolkit daily aggregation/enrichment | included in the daily snapshot |
| Trading calendar | Toolkit calendar loader | manifest |
| L1 product-side admission | locked mainline panel snapshot for parity | `data/product_side_panel/l1_admission_YYYYMMDD.csv` |
| NAV, positions, pending/unfilled orders | paper account state, later phase | not implemented in phase 1 |
| Fees, margin, taxonomy | S1 config and server-deploy S1 helpers | audit manifest |

## Daily outputs

| File | Meaning |
| --- | --- |
| `output/orders/orders_*.csv` | T+1 planned paper orders for manual review |
| `output/diagnostics/diagnostics_*.csv` | signal-date diagnostics emitted by the locked S1 path |
| `output/audit/audit_*.json` | config hash, rule snapshot, and run metadata |
| `output/audit/diff_orders_*.csv` | parity diff against locked historical backtest orders |
| `data/manifests/daily_data_update_*.json` | daily input refresh manifest |
| `data/manifests/data_store_index.json` | partition index and row-count ledger |

Each daily manifest includes `s1_table_dependency_status`, which records the
effective-config status of the active S1 research table and the inactive
research table families that must not be loaded by the paper generator.

## Live-table rule

For live paper trading, all computed signal tables must be refreshed from
Toolkit data or paper account state.  Research-only forward labels are allowed
only in offline validation.  Live order generation may use current-day features
and lagged historical `hist_*` fields, but must not use forward `label_*`
fields.

The rolling product-side updater stores outcome labels only after enough future
daily snapshots are already present.  Signal-day buckets use shifted rolling
history, so the current day never reads its own future outcome.

## Incremental refresh rule

Daily data is stored by trading-date partition.  `update_daily_data.py` reuses an
existing partition by default and fetches only missing dates from Toolkit.  Pass
`--force` only when the upstream Toolkit data for an already stored date must be
rebuilt.  Derived S1 tables are refreshed every run from the stored daily
snapshot, so recalculation can happen without re-downloading historical data.

## Research-table audit

The active locked mainline reads the stored product-side date panel configured
by `s1_l1_product_side_panel_path`.  In paper trading this table is replaced by
the rolling product-side updater.  Full Shadow V3 lookup, B14 layered signals,
environment portfolio scores, candidate-universe dumps, and B5/B23D research
panels are inactive under the current effective config and are excluded from the
paper order-generation surface.

See `docs/s1_research_table_dependency_audit.md` for the detailed code-path
audit.
