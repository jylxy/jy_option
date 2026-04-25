# S1 组合风险预算 v2 设计记录

## 背景

S1 当前主线是卖权收权利金，目标仍然是：

- 年化收益接近或超过 6%。
- 最大回撤控制在 2%以内。
- Vega 归因尽量为正，收益不能主要靠方向侥幸。
- 组合画像尽量接近乐得：多品种、多执行价、P/C 随趋势偏移，降波确认时敢于提高仓位。

前一轮 `step7_forward_vega_quality` 证明 forward vega 过滤有效，Vega 质量明显改善，但平均保证金使用率过低。`step8_clean_vega_budget_ramp` 直接放大预算后，手数和保证金上去了，但活跃品种和合约宽度没有明显改善，反而放大了单一品种/单一合约尾损。结论是：下一步不能只放大总预算，必须先把风险账本细化。

## 核心判断

S1 的问题不是“过滤条件不够多”，而是“干净信号通过之后，组合如何安全地吃更多权利金”。因此 v2 改造重点是仓位和组合结构：

- 降波确认品种可以拿到更高预算。
- 预算释放必须落到多品种、多方向、多执行价，而不是堆到一个合约。
- 板块、相关组、品种方向、单合约都要有硬上限。
- 诊断落盘必须能解释仓位为什么低、尾损来自哪里。

## 新增风险账本

新增并接入开仓检查的约束：

- `portfolio_product_side_margin_cap`：同品种同方向保证金上限，例如 `CU:P`。
- `portfolio_product_side_stress_loss_cap`：同品种同方向压力亏损上限。
- `portfolio_corr_group_margin_cap`：相关组保证金上限，例如有色基础金属组。
- `portfolio_corr_group_stress_loss_cap`：相关组压力亏损上限。
- `portfolio_contract_lot_cap`：单合约最大手数。
- `portfolio_contract_stress_loss_cap`：单合约压力亏损上限。

这些约束同时支持 regime override，例如：

- `vol_regime_falling_product_side_margin_cap`
- `vol_regime_falling_corr_group_margin_cap`
- `vol_regime_falling_product_side_stress_loss_cap`
- `vol_regime_falling_corr_group_stress_loss_cap`
- `vol_regime_falling_contract_stress_loss_cap`

## 诊断落盘

`nav` 增加有效预算字段：

- `effective_product_side_margin_cap`
- `effective_corr_group_margin_cap`
- `effective_product_side_stress_loss_cap`
- `effective_corr_group_stress_loss_cap`
- `effective_contract_stress_loss_cap`
- `effective_contract_lot_cap`

`diagnostics` 增加使用率：

- bucket/corr group 的 margin cap 与 stress cap 使用率。
- S1 product-side 的 margin/stress 使用率。
- 单合约最大手数、单合约压力亏损、对应 cap 使用率。

## Step8b 实验设计

配置文件：`config_s1_v2_step8b_falling_granularity_costed.json`

它以 `step7_forward_vega_quality_costed` 为基准，只做组合预算升级：

- 保持 forward vega 条件，不放松入口质量。
- 降波品种 stress budget 从 `0.004` 提到 `0.006`。
- 降波品种产品 cap 从 `0.12` 提到 `0.14`。
- 降波 bucket cap 从 `0.28` 提到 `0.32`。
- 增加相关组 cap、品种方向 cap、单合约 cap。
- 增加 ladder 候选宽度，让新增仓位优先铺到附近 delta 合约。
- 保留成本与滑点配置。

这次实验要回答的问题不是“收益是否最高”，而是：

- 在不放松 forward vega 质量的情况下，是否能提升保证金使用率。
- 新增仓位是否分散到更多合约/品种方向，而不是集中到一个合约。
- Vega 归因是否继续改善或至少不恶化。
- 最大回撤、止损簇、单产品尾损是否被新账本限制住。

## 下一轮观察指标

- 年化收益、最大回撤、Sharpe、Calmar。
- Vega PnL、Theta PnL、Delta/Gamma PnL 占比。
- 平均和峰值保证金使用率。
- 平均活跃品种数、活跃合约数。
- 单产品/单方向/单合约最大损失。
- bucket/corr/product-side/contract cap 使用率分布。
- 止损次数、止损品种、止损后是否出现同相关组连续亏损。
