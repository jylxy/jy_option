# S1 Execution Cost And Financial Options Audit

## Execution cost changes

- Current baseline still keeps `fee = 3` per contract side, and the engine deducts it on both open and close.
- Added a parameterized adverse execution model in `src/execution_model.py`.
- Historical configs remain reproducible because `execution_slippage_enabled` defaults to `false`.
- New costed config `config_s1_v2_step7_forward_vega_quality_costed.json` extends Step7 and enables:
  - normal open/close slippage: `0.20%` of option price;
  - stop-loss close slippage: `0.50%` of option price;
  - expiry settlement: no slippage by default.
- Open and close orders now record:
  - `raw_execution_price`;
  - `execution_slippage`;
  - `execution_slippage_cash`;
  - close orders also carry the original open-side execution metadata through `entry_meta`.

## Remaining execution assumptions to keep visible

- There is no reliable bid/ask in the current 2025 sample path, so slippage is still a haircut model rather than real quote execution.
- Open execution uses full-day volume-weighted minute close, which is close to a VWAP proxy but not a true bid/ask fill.
- Volume participation uses the full execution-day volume, matching the current full-day VWAP assumption.
- `skip_same_day_exit_for_vwap_opens` is still conservative for avoiding same-day lookahead, but it can miss same-day adverse stop after the T+1 open.
- Group close can still close non-trigger legs at their latest known mark if that leg did not trade at the trigger minute.

## Why index and ETF options were not large S1 weights

The issue is not raw data availability. On the server, 2025-01-01 to 2025-08-31 minute data exists for both index and ETF options when using the engine's product filter:

| Product | Minute rows | Trading days | Contracts | Total volume |
| --- | ---: | ---: | ---: | ---: |
| IO | 9,404,719 | 161 | 716 | 15,614,764 |
| HO | 8,143,191 | 161 | 572 | 6,031,007 |
| MO | 9,743,818 | 161 | 730 | 37,075,066 |
| 510050 | 5,269,882 | 161 | 416 | 188,548,188 |
| 510300 | 4,569,072 | 161 | 418 | 175,210,037 |
| 510500 | 10,326,334 | 161 | 874 | 256,529,334 |
| 159915 | 5,235,398 | 161 | 478 | 218,244,521 |
| 159919 | 5,411,079 | 161 | 447 | 23,575,323 |
| 588000 | 7,665,635 | 161 | 682 | 200,196,257 |

Existing Step7/Step8 results show index options did trade, but ETF options did not:

| Run | S1 open sell legs | Index open sell legs | ETF open sell legs |
| --- | ---: | ---: | ---: |
| Step7 forward vega quality | 62 | IO 3, HO 3, MO 2 | 0 |
| Step8 clean vega budget ramp | 75 | HO 5, MO 2 | 0 |

The main causes are strategy-level, not data-level:

- All products compete in the same `risk_reward` ranking. ETF options usually have lower absolute IV and thinner premium/stress than commodity wings, so they often lose before allocation.
- Step7 requires clean forward vega quality: contract IV falling, ATM IV not rising, RV not rising, and skew not steepening. That is good for vega quality, but it also rejects many stable low-vol ETF candidates.
- There is no reserved financial-options sleeve. Managers often carry ETF/index options as a capacity and liquidity core, while our engine only picks them if they beat commodities on the same score.
- Current portfolio limits treat `510050/510300/159919/IO/HO` as the same `equity_core` bucket/correlation family. That is good for risk control, but it also means one or two index trades can consume the equity bucket and leave no room for ETF alternatives.
- The current trend side-selection often selected call-side index shorts in early 2025. It did not intentionally build a balanced ETF/index premium book.

## Research implication

The next strategy change should not blindly force ETF trades. A better next step is to add a financial-options sleeve with its own allocation target and thresholds:

- reserve a modest equity/index/ETF stress budget when carry quality is positive;
- score ETF/index options against their own IV/RV history rather than commodity premium density;
- keep the same hard constraints: delta cap, forward vega quality, cost-adjusted premium, bucket/correlation stress, and liquidity;
- report financial sleeve utilization separately, so we can distinguish "not selected because unattractive" from "not selected because the global scorer crowded it out."
