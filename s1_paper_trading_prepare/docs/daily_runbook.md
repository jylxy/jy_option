# Current S1 Daily Paper Runbook

## Daily Sequence

1. Refresh or validate the paper-account state for T.
2. Fetch missing Toolkit daily snapshots for T.
3. Rebuild PIT panels and the new-month main selected table from stored raw snapshots.
4. Append T rows into the external S1 intent schedule from the rebuilt PIT panels.
5. Mark the paper account to the T close and calculate daily return.
6. Generate T+1 paper orders for review, including any main-sleeve pre-expiry ITM fallback `buy_close` orders.
7. Optionally replay the date through Toolkit minute bars to audit fill, pending, reroute, expiry, and overlay stop.
8. Carry unfilled or partial orders forward in paper state.

## Commands

Initialize paper state:

```powershell
python s1_paper_trading_prepare/scripts/init_account_state.py --as-of-date 2026-05-29 --nav 50000000
```

Refresh T data:

```powershell
python s1_paper_trading_prepare/scripts/update_daily_data.py --signal-date 2026-05-29
```

Rebuild PIT panels and the monthly main selected table from Toolkit snapshots:

```powershell
python s1_paper_trading_prepare/scripts/rebuild_pit_panels.py --start-date 2018-01-01 --end-date 2026-05-29 --signals-start-date 2026-05-01 --signals-end-date 2026-05-29 --tag pit_rebuild_20260529
```

Append T signal rows from the rebuilt PIT panels:

```powershell
python s1_paper_trading_prepare/scripts/append_daily_signals.py --signal-date 2026-05-29 --skip-panel-refresh --tag signal_append_20260529
```

Mark T close PnL:

```powershell
python s1_paper_trading_prepare/scripts/mark_close_pnl.py --as-of-date 2026-05-29
```

Generate T+1 orders:

```powershell
python s1_paper_trading_prepare/scripts/generate_orders.py --signal-date 2026-05-29
```

Replay a validation range:

```powershell
python s1_paper_trading_prepare/scripts/minute_replay_external_signals.py --config s1_paper_trading_prepare/configs/s1_paper_mainline.json --start-date 2024-10-01 --end-date 2024-10-31 --tag s1_current_202410
```

## Outputs

| Output | Meaning |
| --- | --- |
| `output/orders/orders_*.csv` | T+1 planned S1 orders |
| `output/diagnostics/diagnostics_*.csv` | L0-L4 order diagnostics |
| `output/audit/audit_*.json` | Order-generation audit manifest |
| `output/audit/close_mark_summary_*.json` | T close NAV, daily PnL, daily return, stale mark count |
| `output/audit/close_mark_positions_*.csv` | Position-level T close marks and PnL |
| `data/manifests/daily_data_update_*.json` | Daily data refresh manifest |

## Operating Notes

- The approved external intent schedule is the order handoff.
- The main selected source is rebuilt from `data/daily_snapshots/option_chain_*.csv`; do not copy research wide tables into `data/reverse_lowjump/live_product_side_opportunities.csv`.
- Generated schedules and replay outputs are local artifacts and are not committed.
- The order generator does not place broker orders.
- Main-sleeve fallback exit: after the T close mark, the order generator scans active monthly short positions. If `DTE < 2` and the option is ITM at T close, it emits a T+1 `buy_close` plan with `exit_rule=main_pre_expiry_itm_exit`.
- Minute replay is the only fill simulator in this project.
- Stale marks are allowed only for valuation reporting and are flagged; forced exits and expiry settlement require same-day marks or same-day underlying close.
