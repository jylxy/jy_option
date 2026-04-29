# S1 Full Shadow 因子分层校正版报告

生成日期：2026-04-28  
样本标签：`s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest`  
校正分析目录：`output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/`  
报告目的：修正上一版报告中由比例标签、低价合约和机械相关带来的因子强度高估。

## 1. 结论先行

上一版 full shadow 因子报告的方向不是完全错，但因子强度被明显放大。核心原因有三点：第一，`future_net_pnl_per_premium` 和 `future_retained_ratio` 在代码里完全相同，都是 `future_net_pnl / open_premium_cash`，不能作为两个独立标签重复证明；第二，`fee_ratio` 和 `friction_ratio` 本身就是费用除以权利金，而标签里也含有 `fee / premium`，所以它们和标签之间存在机械相关；第三，低价合约会把比例收益和比例亏损极端放大，导致 Q5-Q1 分层看起来过于漂亮。

校正后，因子仍然有价值，但应当从“强 alpha 因子”降级为“承保质量排序和风险控制因子”。在更保守的主样本 `completed + premium >= 100` 中，`premium_to_iv10_loss` 对现金 PnL 的 Mean IC 从全样本比例口径下的 0.423 降到 0.108；对保证金收益 `pnl_per_margin` 的 IC 为 0.201；`b3_vomma_loss_ratio_low` 对现金 PnL 的 IC 为 0.107，对保证金收益为 0.213。这个量级更可信，也更符合期权横截面研究的直觉。

最重要的策略结论是：B2/B3 因子不能被简单理解为“预测未来收益很强”，而应理解为“帮助我们避免低质量权利金和改善保证金效率”。下一版策略仍然可以使用 `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_vomma_loss_ratio_low`、`breakeven_cushion_score`，但用途应偏向合约排序、保证金效率排序、止损风险控制，而不是直接大幅加仓。

## 2. 为什么上一版会显得过好

代码中 shadow outcome 的核心标签为：

```text
future_net_pnl_per_premium = future_net_pnl / open_premium_cash
future_retained_ratio      = future_net_pnl / open_premium_cash
```

这两个标签在数值上完全一致。因此上一版报告中同时展示 `net_pnl_per_premium` 和 `retained_ratio`，实际上是在重复同一个证据。

更关键的是：

```text
future_net_pnl / premium
= (entry_price - exit_price) / entry_price - fee / premium
```

而 `friction_ratio` 和 `fee_ratio` 正是费用相对权利金的比例。因此 `friction_ratio_low` 的 IC 很高，不应直接解释为 alpha。它更像是交易可行性和低价陷阱过滤器。

![Low Price Distortion](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/00_low_price_distortion.png)

这张图展示了低价合约的扭曲：`entry_price <= 0.5` 的候选止损率约 70%，平均 `PnL / premium` 为 -18.62；而 `entry_price > 10` 的候选止损率约 25.85%，平均 `PnL / premium` 约 -0.14。比例标签会把低价合约的跳价和费用问题放大成极端因子效果。

## 3. 校正口径

本报告采用以下更保守的标签：

| 标签 | 定义 | 目的 |
| --- | --- | --- |
| `pnl_per_premium_raw` | 原始 `future_net_pnl / premium` | 保留原口径作为对照 |
| `pnl_per_premium_clip` | 将原始比例收益裁剪到 `[-3, 1]` | 降低低价合约极端值影响 |
| `cash_pnl` | 单手未来现金 PnL | 检查是否只是比例分母效应 |
| `pnl_per_margin` | 单手未来现金 PnL / 估算保证金 | 更接近资金效率 |
| `pnl_per_stress` | 单手未来现金 PnL / 压力亏损 | 更接近风险调整收益 |
| `stop_avoidance` | `-future_stop_flag` | 检查止损概率 |
| `stop_overshoot_avoidance` | 止损跳价超额的反向指标 | 检查极端止损质量 |

主样本使用 `completed + premium >= 100`。这样做的理由是：未完成 shadow 候选仍被最后一天估值截断，不能作为完整持有结果；权利金过低的合约会让比例标签和手续费占比失真。

样本概况如下：

| 样本 | 行数 | 止损率 | 原始比例收益均值 | 裁剪比例收益均值 | 现金 PnL 均值 | 保证金收益均值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 全样本 | 518,048 | 33.94% | -2.318 | -0.227 | -19.25 | -0.0081 |
| 完成样本 | 432,253 | 40.63% | -2.927 | -0.424 | -73.22 | -0.0153 |
| premium >= 100 | 230,462 | 24.62% | -0.086 | 0.155 | -1.04 | 0.0011 |
| 完成且 premium >= 100 | 182,502 | 31.09% | -0.335 | -0.031 | -113.04 | -0.0092 |

主样本平均现金 PnL 为负，说明这个 full shadow 候选池本身并不是“无脑可交易池”。因子研究的意义，是看能否在这个候选池中识别出相对更好的承保对象。

## 4. 因子强度衰减：从“很神”回到“可用”

![IC Decay](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/03_ic_decay_by_sample_and_label.png)

这张图是本报告最重要的图。它展示了同一批因子在原始比例标签、现金 PnL、保证金收益和主样本过滤后的变化。结论很明确：原始比例标签下 IC 过高，校正到可交易样本和现金口径后，因子仍有信息，但不再是 0.4 级别的“神因子”。

几个关键数值如下：

| 因子 | 全样本原始比例 IC | 主样本现金 PnL IC | 主样本保证金收益 IC | 主样本压力收益 IC |
| --- | ---: | ---: | ---: | ---: |
| `friction_ratio_low` | 0.458 | 0.034 | 0.127 | 0.074 |
| `premium_to_iv10_loss` | 0.423 | 0.108 | 0.201 | 0.215 |
| `premium_to_stress_loss` | 0.382 | 0.086 | 0.187 | 0.241 |
| `b3_vomma_loss_ratio_low` | 0.436 | 0.107 | 0.213 | 0.193 |
| `b3_vol_of_vol_proxy_low` | 0.113 | 0.042 | -0.033 | -0.115 |
| `premium_yield_notional` | 0.442 | 0.115 | 0.198 | 0.135 |

校正后可以看到，真正更稳的是“权利金覆盖风险”的指标，而不是单纯的摩擦或权利金收益率。`friction_ratio_low` 对原始比例标签很强，但对现金 PnL 只有 0.034，说明它不应作为 alpha 排序因子，而应作为硬过滤或交易成本约束。

## 5. Contract 层：仍可用于合约排序，但不能夸大

![Contract Corrected IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/01_corrected_ic_heatmap.png)

![Contract Corrected Cum IC Cash](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/03_corrected_cum_ic_cash_pnl.png)

![Contract Corrected Cum IC Margin](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/04_corrected_cum_ic_pnl_per_margin.png)

这两张累计 IC 图补上了均值 IC 缺少的时间路径信息。现金 PnL 累计 IC 用来判断因子是不是只在极端低价样本中有效；保证金收益累计 IC 更接近我们后续实际排序的目标。校正后，曲线不再像上一版比例标签那样夸张，但 `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_vomma_loss_ratio_low`、`premium_yield_margin` 仍然呈现较持续的正向累积，说明它们不是纯粹的一天或一段行情偶然。

但这里还需要再降一级解释强度。我们对主样本做了二次审计：先控制 `entry_price`、`open_premium_cash`、`DTE`、`abs_delta`，再进一步控制 `margin_estimate` 和 `stress_loss`。结果显示，很多看起来很强的单调性其实来自价格、权利金、期限和风险分母结构，而不是独立 alpha。

| 因子 | 原始现金 PnL IC | 控制价格/权利金/DTE/Delta 后现金 IC | 再控制保证金/压力后现金 IC | 原始保证金收益 IC | 再控制保证金/压力后保证金 IC |
| --- | ---: | ---: | ---: | ---: | ---: |
| `premium_to_iv10_loss` | 0.108 | 0.017 | 0.013 | 0.201 | 0.070 |
| `premium_to_stress_loss` | 0.086 | 0.014 | 0.012 | 0.187 | 0.069 |
| `b3_vomma_loss_ratio_low` | 0.107 | 0.017 | 0.011 | 0.213 | 0.071 |
| `gamma_rent_penalty_low` | 0.066 | 0.013 | 0.012 | 0.178 | 0.070 |
| `premium_yield_margin` | 0.072 | 0.045 | 0.031 | 0.226 | 0.128 |
| `friction_ratio_low` | 0.034 | -0.014 | -0.012 | 0.127 | 0.011 |

这个结果非常关键：因子不是无效，但不能说它们“预测未来现金 PnL 很强”。更准确的说法是，它们大多在识别同一条链上的权利金厚度、风险覆盖、保证金效率和低价/费用结构。对策略设计而言，这仍然有用，但用途应是“承保质量排序”和“风险预算校准”，不是独立 alpha 放大。

我们还做了两个重叠样本检查。第一，只取每 5 个交易日一个信号，`premium_to_iv10_loss` 对现金 PnL 的 IC 仍约 0.098、对保证金收益约 0.199。第二，每个合约只保留第一次出现，`premium_to_iv10_loss` 对现金 PnL 的 IC 约 0.126、对保证金收益约 0.150。这说明累计 IC 的稳定性不完全来自每日重复样本，但日频 full shadow 中大量相邻日期、同一合约链的重复观察确实会让累计 IC 看起来更顺滑，不能按独立样本理解 t-stat。

源码层面没有发现明显未来函数。合约 IV 历史在当日 daily_df 更新，信号也基于当日收盘后可见信息；若假设 T 日收盘生成 T+1 计划，这不构成未来信息。但 shadow outcome 的执行标签仍与真实回测不同：shadow 使用 T+1 日收盘作为 entry，并用日频收盘判断止损，不等同于真实 TWAP 开仓和分钟级止损。因此这套 full shadow 更适合做候选质量研究，不应直接当成可交易绩效。

主样本 contract 层中，最值得保留的是以下因子：

| 因子 | 现金 PnL IC | 保证金收益 IC | 压力收益 IC | 策略解释 |
| --- | ---: | ---: | ---: | --- |
| `premium_to_iv10_loss` | 0.108 | 0.201 | 0.215 | 权利金覆盖 IV shock 越厚越好 |
| `premium_to_stress_loss` | 0.086 | 0.187 | 0.241 | 权利金覆盖压力亏损越厚越好 |
| `b3_vomma_loss_ratio_low` | 0.107 | 0.213 | 0.193 | IV 凸性风险相对权利金越小越好 |
| `gamma_rent_penalty_low` | 0.066 | 0.178 | 0.240 | Gamma 租金越低，压力收益越好 |
| `premium_yield_margin` | 0.072 | 0.226 | 0.103 | 对保证金效率有帮助，但需防止只追高权利金 |

![Contract Corrected Stop Layer](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/02_corrected_stop_rate_by_layer.png)

![Contract Corrected Cum IC Stop](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/05_corrected_cum_ic_stop_avoidance.png)

止损避免累计 IC 图显示，收益型因子和止损型因子不是完全同一回事。`breakeven_cushion_score`、`b3_vol_of_vol_proxy_low` 这类因子更偏风险控制，而 `premium_to_iv10_loss`、`premium_to_stress_loss` 更偏收益/效率。下一版不能只按收益 IC 排序，否则可能牺牲止损概率。

止损分层图给出的结论更谨慎。`breakeven_cushion_score` 对止损概率改善最清楚，Q5-Q1 止损率差约 -6.61 个百分点；`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty_low` 对止损也有小幅改善，但不是上一版看起来那么夸张。

这意味着下一版 contract 排序应该是多目标，而不是只用一个收益因子：

```text
合约排序 = 权利金/IV shock 覆盖
       + 权利金/stress 覆盖
       + breakeven cushion
       + gamma rent penalty
       + 费用硬过滤
```

其中 `friction_ratio` 只做硬过滤，不作为 alpha 加分；低价合约也要继续挡掉。

## 6. Product 与 Product-Side 层：不能简单用 vol-of-vol 加预算

![Product-Side Corrected IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side_primary/01_corrected_ic_heatmap.png)

![Product-Side Corrected Cum IC Margin](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side_primary/04_corrected_cum_ic_pnl_per_margin.png)

![Product Corrected IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_primary/01_corrected_ic_heatmap.png)

![Product Corrected Cum IC Margin](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_primary/04_corrected_cum_ic_pnl_per_margin.png)

聚合层的累计 IC 图是判断“能不能做预算倾斜”的关键。Product-Side 和 Product 层如果只看均值 IC，容易把某些短期有效的因子误判为预算因子；累计路径能看出这种效果是否跨时间持续。校正后，`premium_to_stress_loss`、`premium_to_iv10_loss` 仍比单纯 `vol_of_vol` 更像预算因子。

Product 层校正后，`b3_vol_of_vol_proxy_low` 并没有像上一版报告那样稳定正向。它对主样本 product 层的 `stop_avoidance` IC 为 0.069，但对 `pnl_per_margin` 为负，对 `pnl_per_stress` 也偏负。这说明它更像是止损概率/环境风险指标，而不是收益预算放大指标。

Product 层更可用的预算因子反而是：

| 因子 | Product 保证金收益 IC | Product 现金 PnL IC | Product 止损避免 IC | 判断 |
| --- | ---: | ---: | ---: | --- |
| `premium_to_stress_loss` | 0.122 | 0.065 | 0.044 | 可做品种预算参考 |
| `b3_joint_stress_coverage` | 0.122 | 0.065 | 0.044 | 与上高度等价，二选一 |
| `premium_to_iv10_loss` | 0.113 | 0.058 | 0.056 | 可做预算和合约排序的桥接 |
| `breakeven_cushion_score` | 约 0.08 | 0.116 | 0.049 | 对现金 PnL 与止损均有解释 |
| `b3_vol_of_vol_proxy_low` | -0.03 左右 | 0.04 | 0.069 | 更适合风险惩罚，不适合加预算 |

![Product Corrected Stop Layer](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_primary/02_corrected_stop_rate_by_layer.png)

![Product Corrected Cum IC Stop](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_primary/05_corrected_cum_ic_stop_avoidance.png)

Product 层止损累计 IC 支持一个更细的用法：`vol_of_vol` 更适合做风险惩罚或冷却期判断，而不是正向加预算。换句话说，高 vol-of-vol 应该阻止我们扩仓；低 vol-of-vol 只能说明环境没那么糟，不能自动说明这份权利金值得多卖。

因此，我们要修正前一个判断：低 vol-of-vol 不能直接作为“加仓理由”。它最多说明当前止损概率可能更低，但未必带来更好的保证金收益。更合理的用途是：

- 高 vol-of-vol：降预算或提高开仓门槛。
- 低 vol-of-vol：解除部分惩罚，但不单独加预算。
- 是否加预算仍应看 `premium_to_stress_loss`、`premium_to_iv10_loss`、`breakeven_cushion_score`。

## 7. 对上一版报告的修正

上一版报告中应修正的表述如下：

| 原表述 | 校正后表述 |
| --- | --- |
| 因子 IC 可达 0.4，说明很强 | 0.4 主要来自比例标签和机械分母；校正后可交易 IC 多在 0.05-0.22 |
| `friction_ratio_low` 是强 alpha | 它主要是交易成本/低价过滤器，不能作为加仓 alpha |
| `retained_ratio` 与 `net_pnl_per_premium` 双重验证 | 两者完全相同，不能重复计证据 |
| `b3_vol_of_vol_proxy_low` 可直接加品种预算 | 它更适合风险惩罚或解除惩罚，不适合单独加预算 |
| B2C 因子非常确定有效 | B2C 因子在保证金收益和压力收益上仍有效，但应作为排序/风控因子，而不是大幅加仓因子 |

## 8. 下一版实验建议

基于校正版结果，下一步不建议直接扩大 B2C 风险预算，而应做三个更干净的实验。

### B4a：合约排序校正版

保持品种预算和组合约束不变，只改变同品种同方向内部的合约排序。

排序因子：

```text
premium_to_iv10_loss
premium_to_stress_loss
b3_vomma_loss_ratio_low
breakeven_cushion_score
gamma_rent_penalty_low
```

过滤条件：

```text
premium >= 100
entry_price >= 5
friction_ratio < 10%
```

目标不是追求更高交易次数，而是验证是否提高 `PnL / margin`、降低止损率、降低止损跳价。

### B4b：品种预算校正版

品种预算不再使用单独 `vol_of_vol` 加仓，而使用：

```text
product_budget_score =
    premium_to_stress_loss
  + premium_to_iv10_loss
  + breakeven_cushion_score
  - high_vol_of_vol_penalty
```

`vol_of_vol` 只作为惩罚项，不作为正向加仓项。

### B4c：标签体系升级

后续 full shadow 必须新增真实归因标签：

```text
future_theta_pnl_per_premium
future_vega_pnl_per_premium
future_gamma_pnl_per_premium
future_pnl_per_margin
future_pnl_per_stress
```

如果我们的目标之一是 Vega 收益为正，那么因子检验不能只看总收益，必须把 Vega 标签接进 shadow outcome。

## 9. 最终判断

校正后，S1 因子研究仍然有价值，但证据强度要下调。更准确的判断是：这些因子不是“预测未来收益特别强”，而是在帮助我们识别哪份权利金更能覆盖 IV 冲击、压力亏损、Gamma 租金和费用摩擦。

下一版策略应该更像一个承保质量系统，而不是一个单纯的收益排序系统：

```text
先过滤低价和高摩擦，
再用权利金覆盖风险做合约排序，
再用压力覆盖和安全垫做品种预算，
最后用 vol-of-vol 做风险惩罚，
而不是因为某个比例收益 IC 很高就直接加仓。
```

这版校正之后，我们对 B2/B3 的信心应该更稳，但也更克制：因子可用，不能神化。
