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
s1_sim_l1_tail_q4_l2_risk_top4clean_pcprice_oi_l4_riskbuffer_20260530
```

Core rules:

| Layer | Rule |
| --- | --- |
| L0 | OI >= 1000, volume >= 10, valid price, DTE/delta/expiry path from locked S1 config |
| L1 | `tail_cluster_safety_score_bucket5_date >= 4`; the same bucket is wired to the two-column L1 loader contract |
| L2 | product-side score = 12.5% `premium_quality_score` + 12.5% `avg_v3_contract_vrp_pct` + 12.5% `capacity_premium_density` + 12.5% `avg_v3_b6_premium_to_iv10_rank` + 30% `neg_side_signed_pc_same_delta_log_price_ratio` + 20% `side_signed_log_pc_side_total_oi`; bucket multipliers: 5/4/3/2/1 = 1.3/1.1/0.9/0.7/0.5 |
| L3 | product-side ledger enabled; failed sides are not forcibly reallocated |
| L4 | risk-buffer gate score = 30% high-rank `v3_b6_premium_to_stress_rank` + 30% high-rank `v3_b6_premium_to_iv10_rank` + 40% high-rank `v3_base_b6_score`; when at least 5 candidates exist in a product-side slice, drop the bottom 20%, then keep the original B6/delta-band ranking for final contract choice |
| L5 | total entry premium <= 2.5% NAV; bucket/corr-group premium <= 0.8% NAV; margin <= 70% NAV |
| Execution | T signal, T+1 planned execution, entry participation cap 10% |

Final red lines:

- total new-entry premium <= 2.5% NAV
- portfolio bucket premium <= 0.8% NAV
- correlation-group premium <= 0.8% NAV
- product premium <= 0.3% NAV
- product-side premium <= 0.2% NAV
- total S1/portfolio margin hard cap <= 70% NAV
- OI >= 1000, volume >= 10, valid price, configured DTE/delta/expiry eligibility
- factor redline remains enabled: worst 20% high `friction_ratio` and worst 20% low `premium_to_stress_loss`, with missing allowed and minimum keep guard
- L4 risk-buffer gate removes the bottom 20% of `l4_custom_score` only when the candidate count guard is satisfied; missing L4 components are neutral-filled at 50
- holiday T+1 block for new opens remains enabled
- variance-carry and B15/B2 IV entry gates remain active under the effective config

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

The L4 risk-buffer fields are contract-level V3/B6 fields computed from the
stored T close snapshot and prior history.  They are used as a gate/diagnostic
inside the paper adapter; the historical engine files are not edited in place.
