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

## Step8b 复盘与 Step8c 修正

`step8b_falling_granularity_costed` 在 2025-01 到 2025-08 的结果不理想。核心问题不是平均仓位过大，而是一笔 `NI2505P118000.SHF` 的低波卖 Put 在 2025-04-07 触发跳变止损，单笔亏损约 4.9 万。该合约开仓时权利金约 187，止损成交约 2637，实际亏损约为初始权利金的 13 倍。

这暴露了两点：

- `2.5x` 权利金止损只是触发器，不是最大亏损上限；在跳空或快速升波时，成交可能远差于止损阈值。
- 原 `delta/gamma/vega` 压力损失使用 `3% spot move + 5 vol`，对低价深虚合约的 gap risk 明显偏低。
- `s1_forward_vega_candidate_multiplier` 是全局扩宽，导致低波品种也拿到更多候选；这和“只在单品种确认 falling 时扩仓”的设计目标不一致。
- 组合层 falling release 会进入当前 open budget，若不再按产品 regime 收紧，非 falling 品种会间接受到更宽产品 cap。

因此新增 Step8c：

- 配置文件：`config_s1_v2_step8c_tail_stress_falling_only_costed.json`
- 增加 `s1_stress_premium_loss_multiple=10`，用权利金倍数作为 cheap OTM 的压力损失下限。
- 恢复全局 `s1_forward_vega_candidate_multiplier=8`，新增 `s1_forward_vega_falling_candidate_multiplier=12`，只对 falling 品种扩宽候选。
- 开启 `s1_product_regime_budget_clamp_non_release_enabled`，非 release regime 会被压回自身产品 regime cap。
- 单合约手数 cap 从 20 降到 15，促使新增预算更偏向多执行价分散。

Step8c 要验证的是：在仍然允许 falling 品种提高预算的同时，是否能阻止低波 cheap OTM 跳变尾损吞掉组合收益。

### 2025-01 到 2025-08 对照结果

同区间、全品种、costed/slippage 口径：

| 版本 | PnL | 最大回撤 | 平均保证金 | 最大保证金 | 开仓数 | 止损数 | Theta PnL | Vega PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Step7 costed | 28,268 | -0.177% | 5.05% | 14.13% | 70 | 18 | 141,361 | -94,751 |
| Step8b granular | -20,520 | -0.522% | 5.02% | 12.78% | 69 | 16 | 145,462 | -87,944 |
| Step8c tail guard | 2,193 | -0.167% | 2.09% | 4.88% | 52 | 10 | 56,093 | -30,861 |

结论：

- Step8b 的失败主要来自 `NI2505P118000.SHF`，该单笔止损约 -49,003，属于 cheap OTM 跳变尾损。
- Step8c 将 NI 尾损压到约 -14,701，最大回撤也恢复到 Step7 附近，说明 tail stress guard 是必要的。
- 但 Step8c 平均保证金降到 2.09%，开仓数降到 52，收益几乎被压没，说明当前 tail floor 叠加单合约 cap 过于保守。
- Vega 仍为负，但亏损幅度随仓位下降显著收窄；这不是 alpha 变好，而是风险暴露变小。

下一轮不应再提高全局预算。更合理的方向是：

- 保留 tail stress guard，但分 regime 设置倍数：falling 品种可较低，low/normal/high 更高。
- 只对 forward vega 干净且单品种 falling 的品种释放预算。
- 对 low stable/VCP 品种保持谨慎，不允许吃组合 falling release。
- 用收益质量指标约束新增仓位：新增合约必须提高组合 `theta / tail_stress` 或 `premium / tail_stress`。
