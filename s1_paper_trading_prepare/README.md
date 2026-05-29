# S1 Paper Trading Prepare

This folder is the independent preparation workspace for the S1 paper-trading order generator.

The current first-stage implementation deliberately calls the locked S1 backtest mainline through a narrow adapter, instead of rewriting the strategy. This keeps the paper-trading output auditable against the historical engine while we extract the order-generation path step by step.

## Locked Mainline

Current source config snapshot:

```text
s1_paper_trading_prepare/configs/source_mainline/config_s1_mainline_current.json
```

The snapshot is copied from the local locked mainline so remote runs do not drift if
`server_deploy/config_s1_mainline_current.json` points to an older candidate.

Effective chain:

```text
config_s1_mainline_current.json
-> config_s1_mainline_locked_50m_liq10_20260527.json
-> config_s1_candidate3_oi1000_premium25_bucket08_margin70_20260527.json
-> config_s1_candidate3_oi1000_premium25_bucket08_20260527.json
-> config_s1_mainline_candidate1_l3_ledger_l1q3_oi1000_20260526.json
-> config_s1_mainline_candidate1_l3_ledger_l1q3_20260526.json
-> config_s1_mainline_candidate1_l3_ledger_20260526.json
-> config_s1_mainline_candidate1_l1_first_20260526.json
-> config_s1_mainline_candidate1_l1_clean_20260526.json
-> config_s1_mainline_l1_clean_20260526.json
```

Mainline rule summary:

```text
L1 product-side gate: historical_retention_score_bucket5_date >= Q3
L1 product-side gate: tail_cluster_safety_score_bucket5_date >= Q3
L3 product-side ledger: enabled, no forced rebudget of failed sides
Contract OI: >= 1000
Total entry premium cap: 2.5% NAV
Bucket/corr-group entry premium cap: 0.8% NAV
Total margin hard cap: 70% NAV
Execution participation cap: 10%
```

## Usage

Refresh T-day input tables from Toolkit:

```powershell
python s1_paper_trading_prepare/scripts/update_daily_data.py --signal-date 2022-04-14
```

Refresh only missing trading-day partitions in a date window:

```powershell
python s1_paper_trading_prepare/scripts/update_daily_data.py --start-date 2022-04-14 --end-date 2022-04-20
```

Check deployable files remain S1-only:

```powershell
python s1_paper_trading_prepare/scripts/check_s1_only_scope.py
```

Generate T+1 planned orders from a signal date:

```powershell
python s1_paper_trading_prepare/scripts/generate_orders.py --signal-date 2022-04-14 --replay-start-date 2022-01-04
```

Run the default one-day smoke:

```powershell
python s1_paper_trading_prepare/scripts/smoke_one_day.py
```

Validate paper-account state files:

```powershell
python s1_paper_trading_prepare/scripts/init_account_state.py --as-of-date 2026-05-29 --nav 50000000
python s1_paper_trading_prepare/scripts/validate_account_state.py --as-of-date 2026-05-29 --require-files
```

Run the daily preparation pipeline:

```powershell
python s1_paper_trading_prepare/scripts/run_daily_paper_pipeline.py --signal-date 2026-05-29 --require-account-state
```

Compare generated pending orders with the locked historical backtest output:

```powershell
python s1_paper_trading_prepare/scripts/compare_with_backtest.py --generated s1_paper_trading_prepare/output/orders/orders_s1_paper_20220414.csv
```

Compare the daily rolling product-side panel candidate with the locked mainline panel:

```powershell
python s1_paper_trading_prepare/scripts/compare_rolling_product_side_panel.py --signal-date 2022-04-14
```

Outputs are written under:

```text
s1_paper_trading_prepare/output/orders/
s1_paper_trading_prepare/output/diagnostics/
s1_paper_trading_prepare/output/audit/
```

## Principle

This project is an order generator, not a new backtester. For any historical date, a full-state replay with the same data snapshot, NAV, positions, and config should produce the same S1 target/pending opens as the locked backtest. Differences must be explainable by candidate pool, product-side admission, ledger budget, contract ranking, premium/margin constraints, or execution-cap handling.

The deployable GitHub scope is S1-only.  Non-S1 side-strategy files are not
kept in this project; the phase-1 adapter installs in-memory no-op import guards
only because the historical engine imports those modules at load time. See:

```text
docs/s1_only_scope_and_daily_data.md
docs/rolling_product_side_panel.md
docs/paper_account_state_contract.md
docs/daily_runbook.md
```
