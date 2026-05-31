# Current Daily Data Contract

This document lists the daily tables needed by the current S1 paper line:

```text
s1_reverse_lowjump_highiv_cluster_m45_new075_plus_iv95_pullback_overlay002_20260531
```

All rolling statistics must be point-in-time. A T signal may use the T close snapshot and trailing history through T, then paper execution starts on T+1.

## Required Tables

### 1. option_chain_daily_snapshot

Source: Toolkit daily option aggregation.

Partition:

```text
data/daily_snapshots/option_chain_YYYYMMDD.csv
```

Required columns:

```text
trade_date, exchange, product, option_code, option_type, strike,
expiry_date, dte, option_close, volume, open_interest, implied_vol,
delta, moneyness, spot_close, multiplier, underlying_code
```

Refresh rule: fetch only missing trading dates unless `--force` is used.

### 2. reverse_lowjump_iv_daily_panel

Purpose: main-sleeve low-jump history.

Path:

```text
data/reverse_lowjump/iv_daily_panel.csv
```

Required fields:

```text
hist_iv_days_756
hist_jump5pp_rate_756
hist_jump20pct_rate_756
hist_p95_abs_iv_chg_756
candidate_signal_days_252
candidate_pm_median_252
candidate_oi_median_252
```

Guard: trailing or expanding statistics only. No future outcome labels may score the current T row.

### 3. reverse_lowjump_side_flow_guard_panel

Purpose: L1 side-flow and OI guard.

Path:

```text
data/reverse_lowjump/side_flow_guard_panel.csv
```

Required fields:

```text
opt_side_oi_x63
opt_side_volume_x63
opt_side_oi_chg5
opt_side_oi_chg20
fut_volume_x63
fut_oi_x63
fut_oi_chg5
fut_oi_chg20
rule_l1_oi03_flow_guard
```

Current L1 rule:

```text
pit_low_jump_strict
AND opt_side_oi_x63 >= 0.3
AND (fut_oi_chg5 > 0 OR opt_side_volume_x63 <= 1.0)
```

### 4. reverse_lowjump_side_iv_pressure_panel

Purpose: L2 side choice and L4 weak-pressure delta cut.

Path:

```text
data/reverse_lowjump/side_iv_pressure_panel.csv
```

Required fields:

```text
put_iv_pressure
call_iv_pressure
put_candidate_count
call_candidate_count
selected_side_iv_pressure
other_side_iv_pressure
side_iv_pressure_diff
```

The chosen side is the side with higher candidate-pool median IV.

### 5. reverse_lowjump_product_side_opportunities

Purpose: final daily main-sleeve intents.

Path:

```text
data/reverse_lowjump/product_side_opportunities.csv
```

Required fields include:

```text
entry_date, product, exchange, contract_code, option_type, target_expiry,
dte, delta, close_oi, volume, entry_price, strike, spot_close,
sell_side, side_rule, qty, target_qty, premium_cash,
target_premium_cash, target_premium_pct, budget_group, margin_cash,
one_lot_margin_cash, entry_reason
```

These rows feed the external intent schedule.

### 6. iv_pullback_overlay_atm_iv_panel

Purpose: overlay trigger.

Path:

```text
data/iv_pullback_overlay/atm_iv_percentile_panel.csv
```

ATM IV definition:

```text
DTE 7-90
moneyness 0.95-1.05
implied_vol between 1% and 250%
close > 0
ATM IV = median implied_vol
```

Required lag fields:

```text
atm_iv_lag1, atm_iv_lag2, atm_iv_lag3, atm_iv_lag4
iv_percentile_lag1, iv_percentile_lag2, iv_percentile_lag3, iv_percentile_lag4
```

Trigger:

```text
iv_percentile_lag4 >= 95%
AND atm_iv_lag3 < atm_iv_lag4
AND atm_iv_lag2 < atm_iv_lag3
AND atm_iv_lag1 < atm_iv_lag2
```

### 7. iv_pullback_overlay_contract_candidates

Purpose: overlay side and contract selection.

Partition:

```text
data/iv_pullback_overlay/contract_candidates_YYYYMMDD.csv
```

Filters:

```text
nearest expiry
DTE >= 7
OTM
abs(delta) < 0.05
open_interest >= 1000
volume > 0
close > 0
```

### 8. overlay_opened_expiry_ledger

Purpose: enforce one overlay open per product and expiry.

Path:

```text
state/overlay_opened_expiry_ledger.csv
```

### 9. paper_account_state

Purpose: NAV, open positions, fills, fees, margin, pending orders.

Paths:

```text
state/account_YYYYMMDD.json
state/positions.csv
state/fills.csv
state/pending_orders.csv
```

### 10. paper_close_mark

Purpose: T close paper-account valuation and return.

Outputs:

```text
output/audit/close_mark_positions_YYYYMMDD.csv
output/audit/close_mark_summary_YYYYMMDD.json
```

Rules:

```text
fresh mark = same-day Toolkit option_close
stale mark = previous account mark, reported but not accepted for forced exits
daily_return = daily_pnl / input_nav
short-option daily_pnl = signed_quantity * (mark_price - previous_mark_price) * multiplier
```

### 11. expiry_underlying_settlement_price

Purpose: exact expiry intrinsic settlement when the option itself has no tradable minute price.

Lookup:

```text
underlying_code from option metadata / position state
date = expiry processing date
```

Price priority:

```text
1. Toolkit future_daily_quote settlement
2. Toolkit future_daily_quote close
3. Toolkit underlying daily close map
```

Rules:

```text
call intrinsic = max(underlying_price - strike, 0)
put intrinsic  = max(strike - underlying_price, 0)
do not use previous cached spot if all same-day sources are missing
write expiry_settlement_blocked_missing_spot when blocked
```

### 12. external_intent_schedule

Purpose: clean handoff from daily signal generation to order review and minute replay.

Configured path:

```text
data/external_signals/broad_sector_margin45_new075_plus_iv95_pullback_overlay002_open_signals.csv
```

Generated schedules are local data artifacts and are not committed.

## Future-Function Guardrails

- L1 low-jump and flow features use T or earlier data.
- Overlay trigger uses lag1-lag4 fields only.
- Contract selection and budget sizing use the T snapshot plus current paper-account state.
- Minute replay may use T+1 minute data only for execution simulation, not for T signal selection.
