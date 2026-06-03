# Overlay2 And Overlay3 Rulebook

Ported from:

```text
D:/工作实验/期权卖权策略相关研究/docs/s1_overlay2_overlay3_rulebook_20260531.md
```

These rules are part of the current paper-trading S1 line:

```text
s1_hsafe_addon025_sidecar1_t1lt95_20260603
```

The two sidecars scan the full non-ETF option universe. `SSE` and `SZSE` are excluded; commodity options and CFFEX index options remain eligible. They are signal sidecars, not broker execution adapters. A T signal is generated after the T daily Toolkit snapshot is available and is executed in paper mode on T+1 through Toolkit minute replay.

## Overlay2 Risk Reversal

Layer:

```text
overlay2_risk_reversal_same_sign
```

Daily input:

```text
nearest expiry only
DTE 10-90
OTM put/call contracts
0.01 <= abs(delta) <= 0.30
implied_vol between 1% and 250%
close > 0
at least 2 put IV observations and 2 call IV observations
risk_reversal = median_call_iv - median_put_iv
abs_risk_reversal = abs(risk_reversal)
```

Point-in-time trigger:

```text
iv_percentile_lag3 >= 0.95
abs_risk_reversal_lag3 > 0
abs_risk_reversal_lag2 < abs_risk_reversal_lag3
abs_risk_reversal_lag1 <= abs_risk_reversal_lag2
sign(risk_reversal_lag1) == sign(risk_reversal_lag2) == sign(risk_reversal_lag3)
```

Sell side:

```text
risk_reversal_lag1 > 0 -> sell Call
risk_reversal_lag1 < 0 -> sell Put
```

Sizing and contract:

```text
target premium = 0.025% NAV
nearest expiry
DTE >= 10
OTM
abs(delta) < 0.03
OI >= 1000
volume > 0
close > 0
rank by abs(delta), OI, volume, close, all descending
```

## Overlay3 Term Structure

Layer:

```text
overlay3_term_structure_cluster_cap2
```

Daily input:

```text
DTE 7-90
moneyness 0.95-1.05
implied_vol between 1% and 250%
close > 0
near_atm_iv = nearest-expiry ATM IV median
next_atm_iv = second-nearest-expiry ATM IV median
term_spread = near_atm_iv - next_atm_iv
```

Point-in-time trigger:

```text
iv_percentile_lag3 >= 0.95
term_spread_lag3 > 0
term_spread_lag2 < term_spread_lag3
term_spread_lag1 <= term_spread_lag2
term_spread_lag1 > 0
```

Side selection:

```text
At T-1, compare nearest-expiry OTM put/call IV pressure.
Choose the side with higher IV pressure.
If chosen side is Call and 20d trend > 0, skip.
If chosen side is Put and 20d trend < 0, skip.
```

Concentration:

```text
same strategy_layer + week + exchange + sell side <= 2 signals
```

Sizing and contract:

```text
target premium = 0.025% NAV
nearest expiry
DTE >= 10
OTM
abs(delta) < 0.03
OI >= 1000
volume > 0
close > 0
rank by abs(delta), OI, volume, close, all descending
```

## Shared Controls

```text
total margin / NAV <= 70%
one sidecar open per product and expiry
layer unrealized PnL stop = -0.20% NAV
when a layer stop triggers, close only losing positions in that layer
```

Future-function guard:

```text
All trigger fields are lagged or expanding-history fields available no later than T.
Overlay2 sell side uses lag1 risk_reversal sign.
Overlay3 side and trend filter use T-1 side pressure and 20d trend.
T+1 minute bars are used only for paper execution replay, not for T signal selection.
```
