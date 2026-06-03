# Current S1 Paper Rulebook

Version:

```text
s1_hsafe_addon025_sidecar1_t1lt95_20260603
```

This document is the source-of-truth description for the current paper-trading engineering line. It describes the rule implemented by `configs/s1_paper_mainline.json` and consumed by the external-intent order generator and Toolkit minute replay.

## Scope

- Strategy family: S1 short option premium.
- Trading mode: paper tracking only, no broker adapter.
- Signal clock: compute after the T daily Toolkit snapshot is complete.
- Execution clock: T+1 paper execution through Toolkit minute VWAP replay.
- Future-function guard: all signal fields must be available on or before T. Label or path-outcome fields can be used only after their horizon has matured and only for post-trade audit.

## Main Sleeve L0-L4

The main sleeve is not a free daily scan. It first builds one product-month
opportunity for each product after the previous monthly expiry:

```text
entry window = first tradable date in (previous_expiry, previous_expiry + 7 calendar days]
target expiry = nearest live expiry on that entry date
side = high_iv_pressure on that nearest expiry
```

Only this product-month opportunity table is then passed through L1/L2/L3/L4.

### L0 Data And Tradability

Daily inputs come from Toolkit option-chain snapshots and account state.

Contract-level tradability requirements:

```text
price > 0
volume > 0
open interest >= 1000
valid implied_vol / delta / moneyness / expiry / multiplier
```

Portfolio hard line:

```text
total margin / NAV <= 70%
```

### L1 Product-Side Hard Gate

Current L1 rule:

```text
rule_l1_hsafe_addon025
```

Expanded PIT expression:

```text
rule_l1_hsafe_core OR addon025
```

Where:

```text
pit_low_jump_strict =
  hist_iv_days_756 >= 120
  AND hist_jump5pp_rate_756 <= 0.025
  AND hist_p95_abs_iv_chg_756 <= 0.040

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

`opt_side_oi_x63` and `opt_side_volume_x63` are computed from the same
main-line near-month eligible candidate pool used for side pressure, not from
all listed contracts on that side. The pool is commodity options only, OTM,
nearest valid expiry, `abs(delta) <= 0.08`, `OI >= 1000`, positive volume,
positive price, and valid IV.

Interpretation:

- The hsafe core keeps very clean historical IV-jump products, limits weak
  side-pressure states, and requires either direct pressure evidence or futures
  OI confirmation.
- The addon branch admits a slightly wider low-jump set only when side OI depth
  and cross-sectional rank are stronger.
- Both branches remain point-in-time: same-day pressure/flow is available after
  the T close snapshot, while rolling IV history is trailing.

### L2 Side Choice

Current side rule:

```text
high_iv_pressure
```

For the same product and target near-month expiry:

1. Build eligible OTM put candidates.
2. Build eligible OTM call candidates.
3. Compute each side's median implied volatility.
4. Sell the side with higher median IV pressure.
5. If only one side has eligible candidates, sell that side.

### L3 Budget Tilt

Base target:

```text
0.10% NAV per main-sleeve intent
```

The `l3eff015` tilt then adjusts the target premium:

```text
weak pressure / crowded flow / path risk / low efficiency -> 0.075% NAV
ordinary accepted intent                                  -> 0.10% NAV
high quality with pressure and efficiency support          -> 0.15% NAV
```

Broad-sector portfolio compression:

```text
if total margin / NAV >= 45%
and the new trade has an existing same-sector peer
then cap that new trade at 0.075% NAV
```

### L4 Contract Selection

Current L4 rule:

```text
l4_diff02_delta04_l3eff015
```

Base filters:

```text
OTM
abs(delta) < 0.08
open interest >= 1000
volume > 0
close > 0
nearest target expiry selected by the main schedule
```

Weak-pressure delta cut:

```text
if side_iv_pressure_diff < 0.02:
    abs(delta) cap = 0.04
else:
    abs(delta) cap = 0.08
```

Contract ranking:

```text
abs(delta) desc
open interest desc
volume desc
close desc
```

## Overlay Sidecars L0-L4

The three overlays are sidecars. They do not participate in the main monthly rhythm and each has its own trigger, sizing, layer identity, and layer-level loss stop.

### Common Overlay L0 Universe

Current approved config scans non-ETF options:

```text
exclude exchanges: SSE, SZSE
```

Commodity options and CFFEX index options remain in scope.

Common tradability:

```text
nearest expiry
DTE >= 10
OTM
open interest >= 1000
volume > 0
close > 0
```

### Overlay1 L1 Trigger

Build product-day ATM IV:

```text
DTE 7-90
moneyness 0.95-1.05
implied_vol between 1% and 250%
close > 0
ATM IV = median implied_vol
```

Trigger `t4_p90_pullback_3d_lag1_ivpct_lt95_trend15`:

```text
T-4 IV percentile >= 90%
T-3 ATM IV < T-4 ATM IV
T-2 ATM IV < T-3 ATM IV
T-1 ATM IV < T-2 ATM IV
T-1 IV percentile < 95%
abs(T-1 20d futures trend) <= 15% when the lagged trend is available
min historical IV observations = 252
```

### Overlay2 Risk-Reversal Trigger

Layer id:

```text
overlay2_risk_reversal_same_sign
```

Build nearest-expiry risk reversal:

```text
sample: DTE 10-90, OTM puts/calls, 0.01 <= abs(delta) <= 0.30
implied_vol between 1% and 250%
close > 0
require at least 2 put observations and 2 call observations
risk_reversal = median_call_iv - median_put_iv
abs_risk_reversal = abs(risk_reversal)
```

Trigger `t3_p95_rr_2d_repair_same_sign`:

```text
iv_percentile_lag3 >= 95%
abs_risk_reversal_lag3 > 0
abs_risk_reversal_lag2 < abs_risk_reversal_lag3
abs_risk_reversal_lag1 <= abs_risk_reversal_lag2
sign(risk_reversal_lag1) == sign(lag2) == sign(lag3)
min historical IV observations = 252
```

Side:

```text
risk_reversal_lag1 > 0 -> sell Call
risk_reversal_lag1 < 0 -> sell Put
```

### Overlay3 Term-Structure Trigger

Layer id:

```text
overlay3_term_structure_cluster_cap2
```

Build term spread:

```text
sample: DTE 7-90, moneyness 0.95-1.05
implied_vol between 1% and 250%
close > 0
near_atm_iv = nearest-expiry ATM IV median
next_atm_iv = second-nearest-expiry ATM IV median
term_spread = near_atm_iv - next_atm_iv
```

Trigger `t3_p95_term_2d_repair_t1_no_trend_conflict_cluster_cap2`:

```text
iv_percentile_lag3 >= 95%
term_spread_lag3 > 0
term_spread_lag2 < term_spread_lag3
term_spread_lag1 <= term_spread_lag2
term_spread_lag1 > 0
min historical IV observations = 252
```

Side and trend conflict:

```text
At T-1, compare nearest-expiry OTM put/call IV pressure and choose the richer side.
If chosen side is Call and 20d trend > 0: skip.
If chosen side is Put and 20d trend < 0: skip.
Same strategy_layer + week + exchange + sell side <= 2 signals.
```

### Overlay L2 Side Choice

Overlay1 and Overlay3 compare eligible OTM put/call candidate median IV and sell the side with higher IV pressure. If both sides are tied, implementation preference is call. Overlay2 forces the rich side from the sign of lag1 risk reversal.

### Overlay L3 Sizing

```text
target premium = 0.025% NAV per overlay signal
```

The overlay shares the same portfolio margin hard line:

```text
total margin / NAV <= 70%
```

### Overlay L4 Contract Selection

Filters:

```text
nearest expiry
DTE >= 10
OTM
overlay1: abs(delta) < 0.04
overlay2/3: abs(delta) < 0.03
open interest >= 1000
volume > 0
close > 0
one open per product and expiry
```

Ranking:

```text
abs(delta) desc
open interest desc
volume desc
close desc
```

## Exit And Risk Rules

Main sleeve:

```text
hold to expiry / settlement
pre-expiry ITM fallback exit:
    at T close, scan main-monthly short positions only
    if DTE < 2 and the short option is ITM:
        generate a T+1 buy_close fallback order
        minute replay executes it only with a fresh T+1 option mark
        if no fresh executable mark exists, do not use stale cached price
no take profit
no premium stop
no Greeks stop
no factor exit
no holiday-risk overlay
```

Overlay stop:

```text
for each strategy_layer independently:
    if layer unrealized PnL < -0.20% NAV:
        close only losing positions in that layer
        do not close profitable positions in that layer
        do not close positions in other overlay layers
```

## Execution Rules

Minute replay execution:

```text
signal date = T
execution date = T+1
price mode = Toolkit minute VWAP
price column = close
execution-day volume cap = 10%
```

Close marking:

```text
daily NAV mark uses T option_close from the Toolkit daily snapshot
if a position has no T option close:
    daily mark may use previous mark only as stale valuation
    stale rows are reported in close_mark_summary_YYYYMMDD.json
forced exits do not use stale marks when external_exit_require_fresh_mark=true
same-day open execution price is not accepted as a forced-exit close mark
```

Pending behavior:

```text
if no valid price or no minute bar:
    keep the open pending
max carry = 5 trading days
cancel if DTE <= 0
```

Farther-OTM reroute:

```text
enabled
same product
same option side
same expiry
same multiplier
put: lower strike only
call: higher strike only
max scan contracts = 10
max rerouted contracts = 2
max rerouted quantity = 35% of original target quantity
min reroute contract volume = 1
```

Expiry settlement:

```text
settlement value = intrinsic value from same-day underlying settlement source
underlying price priority:
    1. Toolkit future_daily_quote settlement for the exact underlying_code
    2. Toolkit future_daily_quote close for the exact underlying_code
    3. Toolkit same-day underlying daily close for the exact underlying_code
    4. Exact same-underlying or same-expiry Toolkit daily snapshot spot_close, including PCP fallback when the futures table is missing
call intrinsic = max(underlying_price - strike, 0)
put intrinsic  = max(strike - underlying_price, 0)
if same-day underlying price is unavailable:
    do not fall back to previous cached spot
    do not use cross-month same-product substitutes
    block expiry settlement and write expiry_settlement_blocked_missing_spot
```

## Audit Outputs

Daily order generation writes:

```text
output/orders/orders_TAG.csv
output/diagnostics/diagnostics_TAG.csv
output/audit/audit_TAG.json
```

Daily close marking writes:

```text
output/audit/close_mark_positions_YYYYMMDD.csv
output/audit/close_mark_summary_YYYYMMDD.json
```

Minute replay writes NAV, orders, diagnostics, summary, and unexecuted-signal audit files under `server_deploy/output` unless an explicit output path is supplied.
