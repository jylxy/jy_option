# S1 Paper Trading Prepare

This workspace is the clean paper-trading preparation project for the current S1 line:

```text
s1_strict_main_overlay1_plus_overlay23_20260531
```

It contains only the approved S1 external-intent order flow:

1. Refresh Toolkit daily data.
2. Update the current reverse-lowjump/high-IV-pressure daily signal tables.
3. Write the S1 external sell-intent schedule.
4. Mark the paper account to the T close and calculate daily return.
5. Generate T+1 paper orders for review.
6. Replay the same intents through Toolkit minute bars for fill, pending, reroute, expiry, margin, and overlay-stop diagnostics.

The rulebook is in:

```text
docs/current_rulebook.md
```

The daily table contract is in:

```text
docs/reverse_lowjump_overlay_daily_tables.md
```

Overlay2/3 ported rule details are in:

```text
docs/overlay2_overlay3_rulebook.md
```

The incremental daily signal-refresh design is in:

```text
docs/incremental_signal_update_design.md
```

## Current Rule Summary

Main sleeve:

```text
L1: rule_l1_oi03_flow_guard
L2: sell the higher-IV-pressure side
L3: l3eff015 budget tilt, broad-sector margin45 new075
L4: l4_diff02_delta04_l3eff015
```

Overlay sidecar:

```text
Overlay1: T-4 IV percentile >= 95%, then ATM IV pulls back for 3 days.
Overlay2: T-3 risk-reversal percentile >= 95%, same-sign RR repairs for 2 days.
Overlay3: T-3 near-minus-next ATM IV percentile >= 95%, term spread repairs for 2 days,
          with T-1 side pressure and 20d trend-conflict filter.
Sizing:   0.025% NAV target premium per signal.
L4:       nearest expiry, DTE >= 10, OTM, OI >= 1000, volume > 0,
          overlay1 abs(delta) < 0.04, overlay2/3 abs(delta) < 0.03.
Stops:    overlay portfolio loss stop is independent by strategy_layer.
```

Execution:

```text
T signal, T+1 Toolkit minute VWAP, 10% volume cap,
keep no-price/no-bar opens pending, and allow limited farther-OTM reroute.
Expiry uses intrinsic settlement from same-day underlying futures settlement first,
then futures close, then same-day underlying daily close, then exact
same-underlying or same-expiry daily snapshot/PCP spot; stale cached spot and
cross-month same-product substitutes are never used.
```

## Daily Commands

Refresh the T-day Toolkit snapshot and write a manifest:

```powershell
python s1_paper_trading_prepare/scripts/update_daily_data.py --signal-date 2026-05-29
```

Calculate the T close paper-account mark and daily return:

```powershell
python s1_paper_trading_prepare/scripts/mark_close_pnl.py --as-of-date 2026-05-29
```

Generate T+1 paper orders from the approved external-intent schedule:

```powershell
python s1_paper_trading_prepare/scripts/generate_orders.py --signal-date 2024-10-24
```

Run a small order-generation smoke:

```powershell
python s1_paper_trading_prepare/scripts/smoke_one_day.py
```

Replay a date range through Toolkit minute bars:

```powershell
python s1_paper_trading_prepare/scripts/minute_replay_external_signals.py --config s1_paper_trading_prepare/configs/s1_paper_mainline.json --start-date 2024-10-01 --end-date 2024-10-31 --tag s1_current_202410
```

Check the workspace remains scoped to this S1 line:

```powershell
python s1_paper_trading_prepare/scripts/check_s1_only_scope.py
```

Audit the current signal schedule for self-integrity:

```powershell
python s1_paper_trading_prepare/scripts/audit_signal_schedule_sources.py
```

Strictly audit a generated schedule against the committed gold schedule:

```powershell
python s1_paper_trading_prepare/scripts/audit_signal_schedule_gold.py --generated path/to/generated_open_signals.csv
```

Audit point-in-time guardrails for the current schedule:

```powershell
python s1_paper_trading_prepare/scripts/audit_future_function_guards.py
```

Run the deeper factor-lineage audit. This checks that the final schedule does
not carry shadow/path/label columns, and that historical `shadow_*` lineage
fields are not treated as live inputs unless their prior outcomes have matured:

```powershell
python s1_paper_trading_prepare/scripts/audit_factor_construction_future_leakage.py
```

Rebuild the unified external-intent handoff table and validate it:

```powershell
python s1_paper_trading_prepare/scripts/build_daily_signal_schedule.py --start-date 2022-01-01 --end-date 2026-03-31 --output s1_paper_trading_prepare/data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_signal_schedule_gold.py --generated s1_paper_trading_prepare/data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_future_function_guards.py --schedule s1_paper_trading_prepare/data/external_signals/regenerated_open_signals.csv
python s1_paper_trading_prepare/scripts/audit_factor_construction_future_leakage.py --schedule s1_paper_trading_prepare/data/external_signals/regenerated_open_signals.csv
```

## Local Artifacts

Generated data, signals, output, logs, and paper-account state stay local and are ignored by Git. Keep only source code, configs, docs, templates, and empty directory anchors in the repository.
