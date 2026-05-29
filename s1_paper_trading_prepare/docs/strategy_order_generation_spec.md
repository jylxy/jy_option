# S1 order-generation spec

## Objective

This workspace generates S1 paper-trading orders.  It must not redesign S1 and
must not edit the historical backtest main engine in place.  Phase 1 calls the
locked S1 path through a narrow adapter so historical parity is measurable while
the S1-only order-generation code is extracted.

## Locked mainline

Entry config snapshot:

```text
s1_paper_trading_prepare/configs/source_mainline/config_s1_mainline_current.json
```

Effective strategy version:

```text
s1_mainline_current_locked_50m_liq10_20260527
```

Core rules:

| Layer | Rule |
| --- | --- |
| L0 | OI >= 1000, volume >= 10, valid price, DTE/delta/expiry path from locked S1 config |
| L1 | `historical_retention_score_bucket5_date >= 3` and `tail_cluster_safety_score_bucket5_date >= 3` |
| L2 | product-side ranking bucket multipliers: 5/4/3/2/1 = 1.3/1.1/0.9/0.7/0.5 |
| L3 | product-side ledger enabled; failed sides are not forcibly reallocated |
| L4 | B6 contract ranking from the locked mainline |
| L5 | total entry premium <= 2.5% NAV; bucket/corr-group premium <= 0.8% NAV; margin <= 70% NAV |
| Execution | T signal, T+1 planned execution, entry participation cap 10% |

## Output columns

The front order columns are:

```text
signal_date, execute_date, order_status, action, strategy, product, code,
option_type, strike, expiry, quantity, signal_ref_price, gross_premium_cash,
net_premium_cash, margin, stress_loss, L1/L3 budget diagnostics
```

Diagnostics must preserve enough detail to audit:

```text
candidate pool, L1 admission, L2/L3 budget, contract filters, ranking,
premium and margin constraints, final pending orders
```

## Rolling Product-Side Panel

Daily data refresh maintains an S1-only rolling product-side panel:

```text
data/product_side_panel/rolling_product_side_panel.csv
```

It is not allowed to use same-day or future labels in signal decisions.  Outcome
columns are populated only when the required future daily snapshots already
exist, and `hist_*` admission fields are calculated with a one-row shift inside
each product-side series.
