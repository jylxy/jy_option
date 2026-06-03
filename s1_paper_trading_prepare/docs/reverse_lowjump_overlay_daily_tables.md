# Current Daily Data Contract

This document lists the daily tables needed by the current S1 paper line:

```text
s1_hsafe_addon025_sidecar1_t1lt95_20260603
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
The main low-jump gate uses `hist_iv_days_756 >= 120`.

### 3. reverse_lowjump_side_flow_guard_panel

Purpose: L1 side-flow and OI guard.

Important: this is the main-line eligible near-month candidate-side flow, not
the full option-chain side flow. Each daily row is built from the same pool used
for high-IV-pressure side choice: commodity options only, OTM, nearest valid
expiry, `abs(delta) <= 0.08`, `OI >= 1000`, positive volume, positive price, and
valid IV.

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
rule_l1_hsafe_addon025
```

Current L1 rule:

```text
rule_l1_hsafe_core OR addon025
```

Expanded:

```text
rule_l1_hsafe_core =
  hist_iv_days_756 >= 120
  AND hist_jump5pp_rate_756 <= 0.005
  AND hist_p95_abs_iv_chg_756 <= 0.030
  AND opt_side_oi_x63 >= 0.3
  AND (fut_oi_chg5 > 0 OR opt_side_volume_x63 <= 1.5)
  AND rolling_cs_rank <= 20
  AND (side_iv_pressure_diff <= 0.06 OR side_iv_pressure_diff is missing)
  AND (side_iv_pressure_diff is present OR fut_oi_chg5 > 0)

addon025 =
  hist_iv_days_756 >= 120
  AND hist_jump5pp_rate_756 <= 0.025
  AND hist_p95_abs_iv_chg_756 <= 0.030
  AND opt_side_oi_x63 >= 0.5
  AND (fut_oi_chg5 > 0 OR opt_side_volume_x63 <= 1.3)
  AND rolling_cs_rank <= 15
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

Purpose: final product-month main-sleeve intents. This table is generated from
the product-month opportunity table, not from a free daily scan.

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
iv_percentile_lag4 >= 90%
AND atm_iv_lag3 < atm_iv_lag4
AND atm_iv_lag2 < atm_iv_lag3
AND atm_iv_lag1 < atm_iv_lag2
AND iv_percentile_lag1 < 95%
AND abs(trend_20d_lag1) <= 15% when trend_20d_lag1 is available
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
DTE >= 10
OTM
abs(delta) < 0.04
open_interest >= 1000
volume > 0
close > 0
```

### 8. risk_reversal_sidecar_panel

Purpose: Overlay2 skew/RR trigger.

Path:

```text
data/risk_reversal_sidecar/risk_reversal_panel.csv
```

Required fields:

```text
risk_reversal
abs_risk_reversal
iv_percentile
iv_prior_days
risk_reversal_lag1, risk_reversal_lag2, risk_reversal_lag3
abs_risk_reversal_lag1, abs_risk_reversal_lag2, abs_risk_reversal_lag3
iv_percentile_lag1, iv_percentile_lag2, iv_percentile_lag3
put_iv_lag1, call_iv_lag1
forced_sell_side
```

Trigger:

```text
iv_percentile_lag3 >= 95%
AND abs_risk_reversal_lag3 > 0
AND abs_risk_reversal_lag2 < abs_risk_reversal_lag3
AND abs_risk_reversal_lag1 <= abs_risk_reversal_lag2
AND sign(risk_reversal_lag1) == sign(risk_reversal_lag2) == sign(risk_reversal_lag3)
```

Guard: all trigger columns are lagged. The T row may be stored for audit but cannot be used to decide the T signal.

### 9. risk_reversal_sidecar_contract_candidates

Purpose: Overlay2 side and contract selection.

Partition:

```text
data/risk_reversal_sidecar/contract_candidates_YYYYMMDD.csv
```

Filters:

```text
nearest expiry
DTE >= 10
OTM
abs(delta) < 0.03
open_interest >= 1000
volume > 0
close > 0
```

Sell side:

```text
risk_reversal_lag1 > 0 -> Call
risk_reversal_lag1 < 0 -> Put
```

### 10. term_structure_sidecar_panel

Purpose: Overlay3 term-structure trigger.

Path:

```text
data/term_structure_sidecar/term_structure_panel.csv
```

Required fields:

```text
near_atm_iv
next_atm_iv
term_spread
iv_percentile
term_spread_lag1, term_spread_lag2, term_spread_lag3
near_atm_iv_lag1, next_atm_iv_lag1
iv_percentile_lag1, iv_percentile_lag2, iv_percentile_lag3
t1_trend_20d
t1_side
t1_trend_conflict
```

Trigger:

```text
iv_percentile_lag3 >= 95%
AND term_spread_lag3 > 0
AND term_spread_lag2 < term_spread_lag3
AND term_spread_lag1 <= term_spread_lag2
AND term_spread_lag1 > 0
```

### 11. term_structure_sidecar_contract_candidates

Purpose: Overlay3 side and contract selection.

Partition:

```text
data/term_structure_sidecar/contract_candidates_YYYYMMDD.csv
```

Filters:

```text
nearest expiry
DTE >= 10
OTM
abs(delta) < 0.03
open_interest >= 1000
volume > 0
close > 0
```

Side and concentration:

```text
At T-1, choose the higher-IV-pressure side.
If Call and 20d trend > 0, skip.
If Put and 20d trend < 0, skip.
same strategy_layer + week + exchange + sell side <= 2
```

### 12. overlay_opened_expiry_ledger

Purpose: enforce one overlay open per product and expiry.

Path:

```text
state/overlay_opened_expiry_ledger.csv
```

### 13. sidecar_week_exchange_side_ledger

Purpose: enforce weekly sidecar concentration for overlay3 and any future layer that uses the same cap.

Path:

```text
state/sidecar_week_exchange_side_ledger.csv
```

Key:

```text
strategy_layer, week, exchange, sell_side
```

Current cap:

```text
count <= 2
```

### 14. sidecar_layer_stop_diagnostics

Purpose: evaluate the overlay stop independently by strategy layer.

Output:

```text
output/audit/sidecar_layer_stop_YYYYMMDD.csv
```

Rule:

```text
if layer_unrealized_pnl < -0.20% NAV:
    close only losing positions in that layer
```

### 15. paper_account_state

Purpose: NAV, open positions, fills, fees, margin, pending orders.

Paths:

```text
state/account_YYYYMMDD.json
state/positions.csv
state/fills.csv
state/pending_orders.csv
```

### 16. paper_close_mark

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

### 17. expiry_underlying_settlement_price

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
4. Exact same-underlying or same-expiry Toolkit daily snapshot spot_close, including same-day PCP fallback when the futures table is missing
```

Rules:

```text
call intrinsic = max(underlying_price - strike, 0)
put intrinsic  = max(strike - underlying_price, 0)
do not use previous cached spot if all same-day sources are missing
do not use cross-month same-product substitutes
write expiry_settlement_blocked_missing_spot when blocked
```

### 18. external_intent_schedule

Purpose: clean handoff from daily signal generation to order review and minute replay.

Configured path:

```text
data/external_signals/s1_hsafe_addon025_sidecar1_t1lt95_20220104_20260331_20260603.csv
```

Generated schedules are local data artifacts and are not committed.

## Future-Function Guardrails

- L1 low-jump and flow features use T or earlier data.
- Overlay trigger uses lag1-lag4 fields only.
- Overlay2 RR trigger uses lag1-lag3 fields only; sell side is forced from lag1 risk_reversal sign.
- Overlay3 term trigger uses lag1-lag3 fields only; side pressure and trend conflict use T-1 information.
- Contract selection and budget sizing use the T snapshot plus current paper-account state.
- Minute replay may use T+1 minute data only for execution simulation, not for T signal selection.
