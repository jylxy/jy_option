# S1 research table dependency audit

Date: 2026-05-29

## Effective mainline

The paper-trading wrapper loads the locked S1 mainline and then forces the
runtime to the primary strategy family only.  The active mainline is:

`s1_sim_mainline_20260529`

The current active candidate is the L3 ledger branch with Q3/Q3 product-side
admission, open interest >= 1000, total premium cap 2.5% NAV, bucket/correlation
premium cap 0.8% NAV, and total margin cap 70% NAV.

## Active precomputed research table

Only one stored research table is an active input to the current locked S1
mainline:

`server_deploy/output/analysis_product_scoring_v3_71prod_20260525/product_side_date_panel_v3_71prod_20260522_rerun1.csv`

It is configured by `s1_l1_product_side_panel_path` and is loaded through
`_load_l1_product_side_panel()`.

The same table is used in two places:

| Use | Code path | Required fields |
| --- | --- | --- |
| Product-side pre-candidate gate | `ToolkitMinuteEngine._s1_l1_pre_candidate_products()` | `historical_retention_score_bucket5_date`, `tail_cluster_safety_score_bucket5_date` |
| Product-side gate and budget overlay | `l1_product_side_gate_budget_overlay()` | the two bucket fields plus `product_side_score`, `product_side_score_rank_date`, `avg_v3_b6_premium_to_stress_rank`, `historical_retention_score`, `historical_retention_score_rank_date` |

Paper-trading action: keep the locked panel only as a parity baseline, and
maintain `data/product_side_panel/rolling_product_side_panel.csv` as the live
replacement.  The rolling replacement is rebuilt from stored Toolkit daily
snapshots, with signal-day `hist_*` values shifted before scoring.

## Paper-maintained tables

These tables are required for daily paper order generation and are already
materialized by `scripts/update_daily_data.py`:

| Table | Path | Update rule |
| --- | --- | --- |
| Daily option-chain snapshot | `data/daily_snapshots/option_chain_YYYYMMDD.csv` | fetch missing dates from Toolkit |
| S1 L0 contract universe | `data/derived/s1_l0_contract_universe_YYYYMMDD.csv` | recompute from stored daily snapshot |
| Rolling product-side observations | `data/product_side_panel/rolling_product_side_observations.csv` | upsert signal-date product-side rows |
| Rolling product-side panel | `data/product_side_panel/rolling_product_side_panel.csv` | refresh rolling features and buckets |
| Rolling L1 admission audit | `data/product_side_panel/rolling_l1_admission_YYYYMMDD.csv` | recompute from rolling panel |
| Locked-panel L1 admission audit | `data/product_side_panel/l1_admission_YYYYMMDD.csv` | parity reference against current locked panel |

The stored outcome labels inside the rolling observations are not live signals.
They are filled only after enough later daily snapshots exist.  Live signal
features use shifted rolling history, so the current signal date never reads its
own forward outcome.

## Research outputs found but inactive

The backtest repository contains several S1 research table families, but the
effective current mainline does not load them:

| Table family | Loader / writer | Current status | Paper action |
| --- | --- | --- | --- |
| Full Shadow V3 lookup | `_load_s1_full_shadow_v3_lookup()` | `s1_full_shadow_v3_lookup_enabled = False` | skip; rebuild only if a later approved mainline enables it |
| B14 layered signal map | `load_b14_signal_map()` | `s1_b14_layered_model_enabled` is not enabled | skip; requires rolling train/score with embargo if reintroduced |
| Environment portfolio score | `_load_s1_env_portfolio_scores()` | `s1_env_portfolio_budget_enabled = False`, score path empty | skip; recompute from point-in-time regime data if reintroduced |
| Candidate universe and shadow outcomes | `write_s1_candidate_outputs()` | dump/shadow mode is not enabled | skip; paper generator should emit only order candidates and diagnostics |
| B5/B23D panel writers | `write_b5_candidate_panels()` and B23D outcome writers | offline diagnostics only in current branch | skip; do not copy these panels into the paper project |
| Historical NAV/orders/diagnostics | baseline output files | parity reference only | use only in comparison scripts |

## Audit conclusion

For the current S1 paper-trading landing path, the only historical research
table that must be replaced by rolling daily maintenance is the product-side
date panel behind `s1_l1_product_side_panel_path`.  Other stored S1 research
outputs should stay out of the paper project unless the approved mainline
configuration explicitly turns their gates on.
