# Incremental Signal Update Design

This note defines the production path for keeping
`data/external_signals/live_current_open_signals.csv`
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

The production updater is:

```powershell
python s1_paper_trading_prepare/scripts/append_daily_signals.py --signal-date 2026-03-31 --fetch-missing
```

For a validation backfill, run the same appender sequentially:

```powershell
python s1_paper_trading_prepare/scripts/append_daily_signals.py --start-date 2022-01-01 --end-date 2026-03-31 --fetch-missing --output-schedule s1_paper_trading_prepare/data/external_signals/regenerated_open_signals.csv
```

## Canonical Historical Reference

The current committed hsafe_addon025 + sidecar1 T-1<95% schedule is the gold
reference for historical parity.

The live production schedule is separate from the frozen gold schedule:

```text
live: data/external_signals/live_current_open_signals.csv
gold: data/external_signals/s1_hsafe_addon025_sidecar1_t1lt95_20220104_20260331_20260603.csv
```

Daily production appends or replaces rows in the live file only. The gold file
is read-only validation evidence for the frozen 2022-01-01 through 2026-03-31
replay.

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

The monthly builder may calculate internal `histperf_*` fields for L3 sizing,
but those fields are built only from prior opportunities whose target expiry is
strictly before the current entry date. They are then stripped by
`sanitize_main_selected_for_production()` before the live selected table is
written. Production input guards reject both research `shadow_*` columns and
internal `histperf_*` columns if either appears in a live selected source.

`PitSignalAppender` enforces this in code:

- raw inputs are rejected if they contain `shadow_*`, `histperf_*`, path label,
  terminal label, or expiry-PnL style columns;
- main L1 low-jump history uses shifted prior ATM-IV observations;
- overlay triggers use only lag columns (`T-1` through `T-4`, depending on the
  sidecar);
- any historical-performance feature must use prior opportunities with
  `target_expiry < signal_date`; it must not be copied from old research wide
  tables into live.

Current Toolkit note: `future_hf_1min` in the H200 environment does not expose a
futures open-interest column, only OHLC/volume-style fields. The appender
therefore sources daily futures volume/OI from `future_daily_quote` and falls
back to `future_history_quote` if needed. `fut_oi_chg5/fut_oi_chg20` are then
computed from the stored PIT flow panel.

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
data/reverse_lowjump/live_product_side_opportunities.csv
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
- Rolling price/IV history is point-in-time. Main low-jump statistics are built
  from observations strictly before `T`; same-day pressure/flow and contract
  selection can use the completed `T` snapshot.
- Rolling history that summarizes prior labels must be shifted and maturity
  guarded before scoring `T`. The current production appender does not consume
  any label-derived or unsanitized `histperf_*` history.
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
```

After these pass, rerun the full minute replay from `2022-01-01` through
`2026-03-31` and compare NAV, annual return, drawdown, open counts, pending
fills, no-price diagnostics, margin usage, and main/overlay sleeve PnL against
the last clean baseline.

Current implementation note: `build_daily_signal_schedule.py` remains the clean
handoff-table normalizer for an approved source schedule. The daily production
path is now `append_daily_signals.py`, backed by
`src/pit_signal_appender.py`. It reads Toolkit raw snapshots, refreshes the
stored PIT panels, generates same-date L1/L2/L3/L4 and overlay intent rows, and
then writes those rows back into the same external-intent schedule consumed by
order generation and minute replay.
