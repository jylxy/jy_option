# Incremental Signal Update Design

This note defines the production path for keeping
`data/external_signals/strict_main_overlay1_plus_overlay23_open_signals.csv`
fresh without rerunning the full historical backtest every day.

## Objective

Daily paper trading should:

1. Fetch only the missing Toolkit daily partition for signal date `T`.
2. Append or refresh point-in-time intermediate panels through `T`.
3. Generate only `T` external-intent rows.
4. Replace rows where `entry_date == T` in the external-intent schedule.
5. Generate `T+1` planned orders from the schedule.

The order generator remains a lookup reader. The signal updater owns all
factor refresh, candidate generation, sizing, and audit.

## Canonical Historical Reference

The current committed schedule is the gold reference for historical parity.

The source-key audit command is self-integrity only by default:

```powershell
python s1_paper_trading_prepare/scripts/audit_signal_schedule_sources.py
```

Historical lookup sources may be supplied explicitly for archaeology, but they
must not be treated as field-parity references unless they were generated under
the same clean account state. Strict field parity for a future incremental
builder must compare generated rows against the committed gold schedule on
historical dates.

The strict gold audit command is:

```powershell
python s1_paper_trading_prepare/scripts/audit_signal_schedule_gold.py --generated path/to/generated_open_signals.csv
```

The point-in-time guard audit command is:

```powershell
python s1_paper_trading_prepare/scripts/audit_future_function_guards.py --schedule path/to/generated_open_signals.csv
```

The deeper factor-lineage audit command is:

```powershell
python s1_paper_trading_prepare/scripts/audit_factor_construction_future_leakage.py --schedule path/to/generated_open_signals.csv
```

This audit intentionally separates three cases:

1. Final schedule columns. Any shadow/path/label column here is a blocker.
2. Research lineage tables. They may contain labels for audit, but cannot be
   consumed by the daily generator.
3. Historical `shadow_*` summaries. They are shifted historical means, not the
   current row's outcome, but they are still unsafe for live generation unless
   every prior outcome included in the rolling window had already matured by
   the current signal date.

The current lineage audit found that the committed handoff schedule is clean,
while the old research opportunity table contains a small number of rows where
an unfinished prior opportunity could enter `shadow_*` history. The current
0.15% boost rows are not hit by that subset, but the production generator must
avoid `shadow_*` entirely or rebuild any historical-performance feature with an
explicit maturity-date guard.

Execution replay changes such as `no_valid_minute_price`, pending carry, and
farther-contract reroute must not mutate the schedule's intent fields
(`qty`, `target_qty`, `premium_cash`, `target_premium_cash`, `margin_cash`).
If those fields differ from the gold schedule, the cause is in signal sizing,
NAV/account state, margin-budget state, or source-version selection before
execution, not in the minute fill path.

The old main + overlay1 source came from an `include_etf` research output.
After excluding `SSE/SZSE` rows, it still carried ETF trades in account-state
fields such as NAV, current margin, and margin budget. For example, that source
opened an SSE ETF sidecar on `2022-04-29` with `premium_cash=10032` and
`margin_cash=382432`; after the ETF row was excluded, its realized premium
still lifted the source margin budget by `10032 * 70% = 7022.4`, causing the
`2022-05-26` ZN target quantity to differ by one lot. That source is therefore
removed from default audits and must not drive regenerated paper schedules.

## Daily Incremental State

Persist these small rolling tables locally and append `T` only:

```text
data/daily_snapshots/option_chain_YYYYMMDD.csv
data/reverse_lowjump/iv_daily_panel.csv
data/reverse_lowjump/side_flow_guard_panel.csv
data/reverse_lowjump/side_iv_pressure_panel.csv
data/reverse_lowjump/product_side_opportunities.csv
data/iv_pullback_overlay/atm_iv_percentile_panel.csv
data/iv_pullback_overlay/contract_candidates_YYYYMMDD.csv
data/risk_reversal_sidecar/risk_reversal_panel.csv
data/risk_reversal_sidecar/contract_candidates_YYYYMMDD.csv
data/term_structure_sidecar/term_structure_panel.csv
data/term_structure_sidecar/contract_candidates_YYYYMMDD.csv
state/sidecar_week_exchange_side_ledger.csv
state/overlay_opened_expiry_ledger.csv
```

For speed, expanding percentiles and rolling windows should be calculated from
these stored panels, not by querying all historical Toolkit rows each day.

## Daily Flow

```text
update_daily_data(T)
  -> fetch Toolkit T snapshot if missing
  -> update PIT rolling panels from stored history + T
  -> build main monthly intent rows for T if T is a schedule day
  -> build overlay1/2/3 intent rows for T if lagged triggers fire
  -> apply current paper-account NAV, margin, opened-expiry, and weekly sidecar ledgers
  -> replace schedule rows where entry_date == T
  -> write audit with row counts, source snapshots, config hash, and future-function guard status
generate_orders(T)
  -> read the schedule
  -> write T+1 planned orders
```

## Future-Function Guard

- Main L1/L2/L3/L4 may use the completed `T` daily snapshot because execution is `T+1`.
- Rolling history that summarizes prior labels must be shifted before scoring `T`.
- Overlay1 trigger uses `T-4` through `T-1` ATM IV and percentile fields.
- Overlay2 trigger uses `lag3` through `lag1` risk-reversal fields and `lag3` percentile.
- Overlay3 trigger uses `lag3` through `lag1` term-spread fields; side choice and trend conflict use `T-1`.
- No `T+1` minute data may enter signal generation.

## Performance Rule

Normal daily mode should not run the minute replay and should not scan the full
2018-present history from Toolkit. Full replay is reserved for validation,
repairs, and backfills.

If historical data is corrected, rerun only from the first corrected date with
an explicit `--force-from YYYY-MM-DD` style backfill, then re-diff the rebuilt
schedule against the gold historical reference where overlap exists.

## Backfill Validation Sequence

When the clean incremental signal builder is ready, rebuild the full historical
schedule without the old `include_etf` account state:

```powershell
python s1_paper_trading_prepare/scripts/update_daily_data.py --start-date 2022-01-01 --end-date 2026-03-31
python s1_paper_trading_prepare/scripts/build_daily_signal_schedule.py --start-date 2022-01-01 --end-date 2026-03-31 --output data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_future_function_guards.py --schedule data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_signal_schedule_gold.py --generated data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_factor_construction_future_leakage.py --schedule data/external_signals/regenerated_open_signals.csv
```

After these pass, rerun the full minute replay from `2022-01-01` through
`2026-03-31` and compare NAV, annual return, drawdown, open counts, pending
fills, no-price diagnostics, margin usage, and main/overlay sleeve PnL against
the last clean baseline.

Current implementation note: `build_daily_signal_schedule.py` is the clean
handoff-table builder. It rebuilds the unified schedule schema from the
approved clean schedule and is the table consumed by order generation and
minute replay. The remaining production work is to replace the historical
source schedule input with Toolkit daily factor appenders that generate T rows
directly from stored PIT panels, then write into this same schema and pass the
same guard chain.
