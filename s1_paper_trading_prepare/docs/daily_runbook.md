# S1 daily paper-trading runbook

## Daily sequence

1. Prepare the paper-account state files for T date under `state/`.
2. Validate the account state.
3. Refresh Toolkit-backed daily data for T.
4. Recompute S1 derived tables and rolling product-side tables.
5. Generate T+1 planned orders.
6. Review `output/orders/`, `output/diagnostics/`, and the pipeline manifest.
7. After T+1 execution, write fills and any pending remainder back into
   `state/` for the next run.

## Command

```powershell
python s1_paper_trading_prepare/scripts/init_account_state.py ^
  --as-of-date 2026-05-29 ^
  --nav 50000000
```

Then run:

```powershell
python s1_paper_trading_prepare/scripts/run_daily_paper_pipeline.py ^
  --signal-date 2026-05-29 ^
  --account-date 2026-05-29 ^
  --require-account-state
```

For historical parity checks, provide the locked replay start:

```powershell
python s1_paper_trading_prepare/scripts/run_daily_paper_pipeline.py ^
  --signal-date 2022-04-14 ^
  --replay-start-date 2022-01-04
```

## Outputs

| Output | Meaning |
| --- | --- |
| `output/orders/orders_*.csv` | T+1 planned orders for manual confirmation |
| `output/diagnostics/diagnostics_*.csv` | S1 funnel and rule diagnostics |
| `output/audit/daily_pipeline_*.json` | Daily run manifest |
| `data/manifests/daily_data_update_*.json` | Data refresh manifest |
| `output/audit/account_state_validation_*.json` | Account-state validation manifest |

## Current limitation

The phase-1 order path is intentionally still driven by the locked replay
adapter to preserve exact historical parity.  The account-state contract is now
validated and archived every run; directly wiring live NAV, live positions, and
pending-order remainders into sizing/execution is the next engineering phase.
