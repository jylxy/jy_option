---
name: s1-premium-formula-research
description: 用于 S1 纯卖权策略研究、因子分析、回测复盘和实验设计时，强制围绕“Premium Pool × Deployment Ratio × Retention Rate - Tail/Stop Loss - Cost/Slippage”的收益拆解公式来归因。适用于 B5/B6/B7、full shadow、因子分层、合约层/P-C侧、品种层、组合层、月度层分析，以及判断因子应该用于排序、预算、过滤、退出还是风控。
---

# S1 Premium Formula Research

## Core Principle

所有 S1 卖权研究先回到这个公式：

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

不要只问“哪个因子 IC 高”或“哪个版本 NAV 高”。必须回答：

```text
这个变化改善了公式里的哪一项？
它发生在合约层、P/C 侧、品种层、组合层，还是时间/月度层？
它适合做排序、预算倾斜、硬过滤、退出、风控，还是诊断？
```

## When To Use

Use this skill whenever the user asks to:

- 设计、复盘或优化 S1 卖权策略；
- 分析 B5/B6/B7 full shadow 或因子实验；
- 判断 B2/B3/B4/B5 因子是否有用、应该放在哪里；
- 解释收益为什么低、权利金为什么留不住、回撤为什么变大；
- 设计品种预算、P/C 偏移、合约排序、Tail-HRP、冷静期或退出规则；
- 写 S1 因子报告、管理层汇报或实验方案。

## Mandatory Workflow

1. **先定位公式变量**
   - `Premium Pool`: 可交易净权利金池是否足够厚。
   - `Deployment Ratio`: 策略实际吃掉了多少可交易权利金。
   - `Retention Rate`: 收到的权利金最终留住多少。
   - `Tail / Stop Loss`: 止损、gamma、vega、jump、skew、tail cluster 损耗。
   - `Cost / Slippage`: 手续费、bid/ask、低价 tick、退出流动性。
2. **再定位层级**
   - 合约层：这个合约值不值得卖。
   - P/C 侧：今天该偏 Put、Call，还是双边。
   - 品种层：这个品种该多分预算、少分预算，还是观察。
   - 组合层：这些品种、方向、到期和 delta 梯队能不能一起卖。
   - 时间/月度层：目标收益是否有足够权利金池支持。
3. **最后给用途**
   - 排序因子：改善候选优先级。
   - 预算因子：调整 product / side / cluster risk budget。
   - 硬过滤：排除无法交易或风险不可接受的候选。
   - 退出/止损因子：改善 retention 或降低 tail loss。
   - 诊断因子：解释收益、回撤、止损、vega/gamma 损耗。

## Layer Formulas

### Contract Layer

```text
contract_expected_pnl
= net_premium_cash
× contract_retention_probability
- expected_stop_loss
- transaction_cost
```

Focus on:

- net premium, fee ratio, tick value ratio;
- delta bucket;
- premium / margin;
- premium / stress loss;
- premium / tail move loss;
- theta / vega;
- theta / gamma;
- liquidity and exit capacity.

### P/C Side Layer

```text
side_expected_pnl
= side_premium_pool
× side_retention_rate
- side_tail_loss
- side_stop_loss
```

Focus on:

- put/call premium depth;
- put skew, call skew, risk reversal;
- trend, momentum, breakout distance;
- upper/lower tail risk;
- side stop rate, side vega/gamma loss.

Do not treat “more Put” or “more Call” as naturally safe. Long-term Put bias is directional bullish exposure; long-term Call bias is directional bearish exposure.

### Product Layer

```text
product_expected_pnl
= product_premium_pool
× product_deployment_ratio
× product_retention_rate
- product_stop_tail_loss
- product_cost_slippage
```

Focus on:

- product premium depth;
- premium depth / margin;
- premium depth / stress;
- premium depth / cash vega;
- premium depth / cash gamma;
- liquidity and exit capacity;
- historical retention rate;
- stop cluster and tail beta.

Example interpretation:

- 黄金、铜：可能流动性好但权利金薄。
- 白糖、玻璃：可能权利金厚但尾部、跳价、退出和 stop cluster 风险更高。
- 真正值得多分预算：权利金厚、可成交、留存率高、尾部不聚合。

### Portfolio Layer

```text
portfolio_expected_pnl
= sum(product_side_expected_pnl)
- diversification_failure_loss
- margin_squeeze_loss
```

Focus on:

- sector / corr_group exposure;
- tail correlation and stop cluster correlation;
- cash gamma and cash vega concentration;
- margin shock;
- expiry cluster;
- Tail-HRP risk budget;
- effective product count and top stress share.

### Monthly Layer

```text
monthly_expected_return
= monthly_available_premium_pool
× monthly_deployment_ratio
× expected_retention_rate
- expected_monthly_tail_loss
- expected_monthly_cost
```

Key diagnostics:

```text
required_premium_rate = target_monthly_return / expected_retention_rate
available_premium_pool = sum(eligible_net_premium) / NAV
opened_premium_pool = sum(opened_net_premium) / NAV
deployment_ratio = opened_premium_pool / available_premium_pool
realized_retention_rate = realized_net_pnl / opened_net_premium
premium_coverage_ratio = available_premium_pool / required_premium_rate
```

## Factor Mapping

Use this mapping when interpreting factors:

| Factor | Formula Variable | Layer | Typical Use |
| --- | --- | --- | --- |
| `premium_yield_margin` | `Premium Pool`, `Deployment Ratio` | Contract | Contract ranking. |
| `premium_to_stress_loss` | `Retention Rate`, `Tail / Stop Loss` | Contract/Product | Avoid low-quality thick premium. |
| `premium_to_iv5_loss`, `premium_to_iv10_loss` | `Retention Rate`, `Tail / Stop Loss` | Contract | Vega shock coverage. |
| `theta_vega_efficiency` | `Retention Rate` | Contract | Keep theta while reducing vega fragility. |
| `b5_theta_per_gamma`, `b5_gamma_theta_ratio` | `Tail / Stop Loss` | Contract/Delta bucket | Avoid buying theta with too much gamma. |
| `b5_premium_to_tail_move_loss` | `Retention Rate`, `Tail / Stop Loss` | Contract/Product | Tail move coverage. |
| `b5_premium_per_capital_day` | `Premium Pool`, `Deployment Ratio` | Contract/DTE | Capital efficiency. |
| `b5_delta_bucket` | `Premium Pool`, `Retention Rate`, `Tail / Stop Loss` | Contract | Ladder design under delta < 0.1. |
| trend / momentum / breakout | `Retention Rate`, `Tail / Stop Loss` | P/C Side | Avoid selling trend direction. |
| skew / risk reversal | `Premium Pool`, `Retention Rate` | P/C Side | Decide whether premium is rich or dangerous. |
| IV momentum / IV reversion | `Retention Rate` | Product/Time | Distinguish rising vol, falling vol, high-vol plateau. |
| cooldown fields | `Tail / Stop Loss` | Product/Side | Reduce repeat stops and release cooling periods. |
| volume / OI / liquidity | `Deployment Ratio`, `Cost / Slippage` | Contract/Product | Execution and exit capacity. |
| tick ratio / low price | `Cost / Slippage`, `Tail / Stop Loss` | Contract | Avoid fake liquidity and tick-driven stops. |
| product premium depth | `Premium Pool` | Product | Product budget tilt. |
| tail dependence / stop cluster | `Tail / Stop Loss` | Portfolio | Tail-HRP and cluster budget. |
| effective product count / top stress share | `Tail / Stop Loss` | Portfolio | Concentration diagnostics. |

## Report Requirements

Any S1 factor or experiment report must include:

- Factor family.
- Applicable layer.
- Formula variable improved.
- Intended use: ranking, budget, hard filter, exit/risk, diagnostic.
- IC / rank IC / Q1-Q5 if applicable.
- Retention-rate impact.
- Stop-rate and stop-loss impact.
- Tail impact: worst bucket, tail loss, cluster loss.
- Trade-off: premium lost, liquidity lost, or risk increased.

If a factor improves NAV but increases `Tail / Stop Loss` or lowers `Retention Rate`, flag it as potentially misused rather than calling it simply “good”.

## Interpretation Rules

- B4-style mixed scores failing does **not** mean the underlying B2/B3/B5 factors are useless. It may mean they were used in the wrong layer.
- A factor can be valuable even if it does not raise NAV, if it reduces tail cluster, repeat stops, or vega/gamma concentration.
- A high premium factor must be judged together with retention, stress coverage, liquidity, and tail dependence.
- Do not use future shadow outcomes to create same-day signals. Shadow labels are for validation only.

## Project References

When working inside the `jy_option` / S1 workspace and deeper context is needed, read:

- `docs/s1_premium_pool_retention_framework.md`
- `docs/s1_b6_tail_hrp_portfolio_design.md`
- latest B5 full shadow candidate and outcome files in `output/`
