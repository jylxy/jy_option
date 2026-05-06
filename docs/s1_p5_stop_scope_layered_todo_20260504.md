# S1 P5 止损粒度与分层止损实验待办

本文记录 P4 之后的两组止损机制实验。实验暂不立即执行，待 P3/P3B × 止损倍数网格跑完并完成复盘后，再作为下一轮控制变量实验启动。

## 1. 背景

当前 S1 的 1.5X 权利金止损不是单合约逐笔平仓，而是按 `group_id` 平仓。S1 开仓时的 `group_id` 口径为：

```text
S1_{product}_{option_type}_{expiry}_{open_date}
```

因此，只要同一天、同品种、同方向、同到期的一组 ladder 合约中有一条腿触发止损，当前实现会把该组一起平掉。这不是“同品种同方向所有历史仓位全平”，但仍可能导致未触发的同组合约被连带平仓，降低权利金留存率。

## 2. 实验 A：止损粒度实验

目标：验证“单合约止损”能否降低无效止损、提高权利金留存，同时不显著放大最大回撤和 Vega/Gamma 损耗。

| 实验 | 止损倍数 | 止损范围 | 目的 |
|---|---:|---|---|
| A0 | 1.5X | group | 当前基准，复用 P3/P3B 1.5X 结果 |
| A1 | 1.5X | contract | 只平真正触发止损的单个合约 |
| A2 | 1.5X | same_code | 若同一合约存在多批次持仓，则平同代码持仓，介于 contract 与 group 之间 |
| A3 | 1.5X | product_side_group | 反向压力测试，观察更粗粒度批量止损是否过度保护或过度损耗 |

第一优先级为 A1。若 A1 的止损频率下降、权利金留存改善、最大回撤不明显恶化，则说明当前 group 止损过度保守。

## 3. 实验 B：分层止损实验

目标：保留 1.5X 的早期风险提示，但不在第一次触发时直接全组平仓；以 2.5X 作为最后硬止损。

| 实验 | 1.5X | 2.0X | 2.5X | 目的 |
|---|---|---|---|---|
| B1 | 触发合约减半 | 无 | 触发合约剩余全平 | 测试轻量分层止损 |
| B2 | 触发合约减半 | 触发合约全平 | 同组剩余硬平 | 推荐主测版本，兼顾留存与尾部控制 |
| B3 | 只标记预警 | 触发合约减半 | 触发合约全平 | 测试更宽容的逐笔止损 |
| B4 | 触发合约全平 | 无 | 同组剩余硬平 | 测试“单合约先走、极端再组内清仓” |

第一优先级为 B2。它符合交易直觉：1.5X 先处理真正出问题的合约，2.0X 承认单腿风险继续恶化，2.5X 才把同组剩余风险一起硬平。

## 4. 必须记录的评价指标

本轮不能只看 NAV，需要围绕 S1 收益拆解公式评价：

```text
S1 net return
= Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

必看指标：

| 指标 | 解释 |
|---|---|
| `sl_s1 / open_sell` | 止损触发频率是否下降 |
| `sl_loss_cash / open_premium` | 真实止损损耗是否下降 |
| `premium_retained_pct` | 权利金留存率是否改善 |
| `expiry_rows / open_rows` | 更多仓位是否能安全持有到期 |
| `max_drawdown` | 单合约止损是否放大尾部 |
| `theta_pnl / vega_pnl / gamma_pnl` | 是否仍是卖 Theta / Vega 的收益结构 |
| `post_stop_same_group_loss` | 没有一起平掉的同组仓位后续是否继续亏 |
| `false_stop_ratio` | 止损触发后价格快速回落的比例 |
| `cost_slippage` | 分层止损是否增加交易成本 |

## 5. 实施要求

需要先把止损行为参数化：

```text
s1_stop_close_scope = group | contract | same_code | product_side_group
s1_layered_stop_enabled = true | false
s1_layered_stop_levels = [
  {multiple: 1.5, action: reduce, ratio: 0.5, scope: contract},
  {multiple: 2.0, action: close, scope: contract},
  {multiple: 2.5, action: close, scope: group}
]
```

实现时应保持默认行为不变，即默认仍为当前 `group + premium_stop_multiple` 逻辑，避免影响既有 B0-P4 实验可复现性。

## 6. 初始运行建议

第一轮只在 P3B 上开三条线：

1. `P3B_A0_current_group_stop15`：复用已完成 P3B 1.5X。
2. `P3B_A1_contract_stop15`：单合约 1.5X 止损。
3. `P3B_B2_contract_layered_15_20_25`：1.5X 单合约减半，2.0X 单合约全平，2.5X 同组硬平。

若 A1 或 B2 明显优于 A0，再补跑 P3 作为稳健性验证。
