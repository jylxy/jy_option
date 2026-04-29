# S1 Full Shadow 因子校正审计报告

生成日期：2026-04-28  
样本标签：`s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest`  
分析目录：`output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/`  
报告目的：基于最新审计结果，重新校正 full shadow 因子 IC 检查体系，判断 B2/B3 因子到底是 alpha、风险覆盖、合约几何结构、交易摩擦，还是低价/分母效应。

## 1. 执行摘要

这次审计的结论比上一版更清楚：B2/B3 因子不是“强预测收益因子”，而是一组有用但必须分层使用的“承保质量因子”。它们最适合用在同品种、同方向、同到期链条里的合约排序，以及在品种/方向层面做轻度预算倾斜；不适合直接理解为可以大幅加仓的 alpha。

原始 `future_net_pnl_per_premium` 的 IC 很高，但它混入了三个机械来源：第一，标签本身是 `future_net_pnl / premium`，低价合约和小权利金会把比例亏损放大；第二，`fee_ratio` 与 `friction_ratio` 本身就是费用除以权利金，和标签分母存在机械关联；第三，同一合约链上相邻执行价之间的权利金、保证金、stress loss、delta、DTE 都高度共线，会让分层单调性看起来过于漂亮。

校正以后，最有价值的因子仍然是 `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_iv_shock_coverage`、`gamma_rent_penalty_low`、`premium_yield_margin`。但它们的正确解释不是“预测未来现金 PnL 很强”，而是“帮助我们在同一批可卖候选里选择权利金更能覆盖 IV 冲击、压力亏损、保证金占用和 gamma 租金的合约”。

最重要的策略动作是分层使用：

- `friction_ratio_low`、`fee_ratio_low`：只做硬过滤和交易可行性控制，不做 alpha，不做预算加权。
- `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_iv_shock_coverage`：用于合约排序和轻度 product-side 预算倾斜。
- `premium_yield_margin`：用于资本效率排序，但必须叠加止损风险约束，因为它在 stop avoidance 上偏弱。
- `b3_vol_of_vol_proxy_low`：更像风险状态惩罚，不应直接作为加仓因子。
- `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate`：当前 full shadow 检验不支持单独使用，后续需要换成更严谨的 forward RV / realized shock 标签再检验。

## 2. 数据与标签口径

本次审计使用 full shadow candidate universe，而不是已成交订单样本。它的优点是可以看到“所有本来可能被选中的候选”，避免只在策略已筛过的样本上做自证；缺点是 shadow outcome 仍然是研究标签，不是完整可执行回测。

当前 shadow outcome 的重要限制是：开仓价格使用下一交易日的 `option_close`，止损使用日频收盘逻辑，而不是真实 TWAP 开仓和分钟级止损成交。因此，本报告用于判断候选质量和排序方向，不应直接等同于可交易绩效。

核心样本如下：

| 样本 | 行数 | 信号日 | 品种数 | 止损率 | 原始 PnL/Premium | 裁剪 PnL/Premium | 现金 PnL | PnL/Margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 全样本 | 518,048 | 1,215 | 65 | 33.94% | -2.318 | -0.227 | -19.25 | -0.0081 |
| 完成样本 | 432,253 | 1,213 | 64 | 40.63% | -2.927 | -0.424 | -73.22 | -0.0153 |
| `entry_price >= 5` | 305,684 | 1,214 | 65 | 27.20% | -0.287 | 0.059 | -10.50 | -0.0046 |
| `premium >= 100` | 230,462 | 1,214 | 65 | 24.62% | -0.086 | 0.155 | -1.04 | 0.0011 |
| 完成且 `premium >= 100` | 182,502 | 1,211 | 64 | 31.09% | -0.335 | -0.031 | -113.04 | -0.0092 |
| 完成且 `premium >= 100` 且低费率 | 171,457 | 1,211 | 63 | 31.75% | -0.317 | -0.047 | -114.73 | -0.0098 |

主审计样本采用“完成且 `premium >= 100`”。这个口径保守但必要：未完成 shadow 样本没有完整退出路径；权利金太小的合约会让比例收益和费用占比失真。

## 3. 低价合约扭曲

![低价合约扭曲](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/00_low_price_distortion.png)

这张图回答的问题是：上一版为什么会出现一些“神奇单调”的 IC 和分层结果。事实很直接：`entry_price <= 0.5` 的候选有 42,879 条，止损率 69.97%，平均 `PnL/Premium` 为 -18.62；而 `entry_price > 10` 的候选有 200,704 条，止损率 25.85%，平均 `PnL/Premium` 只有 -0.143。

期权卖方视角下，这不是 alpha，而是低价深虚合约的凸性和报价噪声被比例标签放大。低价期权在实盘中也有类似问题：看上去权利金很便宜、delta 很小，但一旦出现跳价，`2.5x` 止损会很容易被触发，而且成交价格可能比理论止损更差。

策略含义很明确：低价、低权利金、高费用占比不能只在报告里校正，后续策略也应该继续保留硬过滤。`friction_ratio` 的高 IC 不能解释为 alpha，只能解释为“费用/低价陷阱识别器”。

## 4. IC 衰减：从原始比例标签回到可交易口径

![IC 衰减](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/03_ic_decay_by_sample_and_label.png)

这张图是本报告的主图。它把同一组因子放在全样本原始比例标签、主样本现金 PnL、主样本保证金收益等口径下对比。结论是：因子并没有失效，但原始 `PnL/Premium` 口径显著高估了强度。

| 因子 | 全样本原始比例 IC | 主样本现金 PnL IC | 主样本 PnL/Margin IC | 主样本 PnL/Stress IC |
| --- | ---: | ---: | ---: | ---: |
| `friction_ratio_low` | 0.458 | 0.034 | 0.127 | 0.074 |
| `premium_to_iv10_loss` | 0.423 | 0.108 | 0.201 | 0.215 |
| `premium_to_stress_loss` | 0.382 | 0.086 | 0.187 | 0.241 |
| `b3_vomma_loss_ratio_low` | 0.436 | 0.107 | 0.213 | 0.193 |
| `b3_vol_of_vol_proxy_low` | 0.113 | 0.042 | -0.033 | -0.115 |
| `premium_yield_notional` | 0.442 | 0.115 | 0.198 | 0.135 |

`friction_ratio_low` 是最典型的例子：原始比例 IC 很高，但现金 PnL IC 只有 0.034，说明它主要是在识别低价和费用陷阱。与此相比，`premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_iv_shock_coverage` 经过校正后仍然在保证金收益和压力收益上保留 0.18-0.24 的 IC，这说明它们确实能帮助识别“权利金相对风险更厚”的合约。

## 5. Contract 层：最适合做合约排序

![Contract 校正 IC 热力图](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/01_corrected_ic_heatmap.png)

Contract 层的结论是：这些因子最强的落地位置不是选品种，而是“同一品种、同一方向、同一批到期合约中选哪一个执行价”。主样本下，`premium_yield_margin` 对 `PnL/Margin` 的 IC 为 0.226，`b3_vomma_loss_ratio_low` 为 0.213，`premium_to_iv10_loss` 为 0.201，`b3_iv_shock_coverage` 为 0.198，`premium_to_stress_loss` 为 0.187。

![Contract 现金 PnL 累计 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/03_corrected_cum_ic_cash_pnl.png)

现金 PnL 累计 IC 的意义在于看因子是否只是在比例标签上漂亮。这里可以看到，`premium_to_iv10_loss`、`b3_iv_shock_coverage`、`b3_vomma_loss_ratio_low` 的现金 PnL 路径仍然是正向的，但强度明显低于原始比例标签。这是合理的：卖权合约排序本来更多是“减少坏承保”和“提高单位风险权利金”，不是稳定预测每一笔绝对现金收益。

![Contract 保证金收益累计 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/04_corrected_cum_ic_pnl_per_margin.png)

保证金收益累计 IC 更接近我们后续排序目标。这里 `premium_yield_margin`、`b3_vomma_loss_ratio_low`、`premium_to_iv10_loss` 和 `b3_iv_shock_coverage` 的路径更稳定，说明它们对资本效率有真实信息。但要注意，资本效率高不等于尾部风险低，所以不能只按这个指标扩大仓位。

![Contract 止损累计 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/05_corrected_cum_ic_stop_avoidance.png)

止损累计 IC 说明了另一面：收益/效率因子不一定降低止损率。`b3_vol_of_vol_proxy_low` 在止损规避上更好，但在 `PnL/Margin` 和 `PnL/Stress` 上偏弱甚至为负。`premium_yield_margin` 在收益效率上强，但 stop avoidance 为 -0.021。这意味着下一版排序必须是多目标，而不是简单追求最高权利金或最高单位保证金收益。

![Contract 止损分层](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract_primary/02_corrected_stop_rate_by_layer.png)

这张图从 Q1-Q5 分层角度验证了同一件事。承保收益因子和止损控制因子不是同一个维度。后续如果要保留 B2C 的 theta 增厚，同时控制 vega/gamma 尾部，就需要把“收益排序”和“止损概率/跳价风险”分开建模。

## 6. 因子相关性矩阵：很多指标其实是一家人

![因子相关性矩阵](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/08_corrected_factor_correlation_matrix.png)

相关矩阵是这版新增的关键审计。它告诉我们，不能把所有高 IC 因子简单相加，否则只是重复计票。高相关对包括：

| 因子 A | 因子 B | Spearman 相关 |
| --- | --- | ---: |
| `friction_ratio_low` | `fee_ratio_low` | 1.000 |
| `premium_to_stress_loss` | `b3_joint_stress_coverage` | 1.000 |
| `premium_to_iv10_loss` | `b3_iv_shock_coverage` | 0.999 |
| `premium_to_iv10_loss` | `b3_vomma_loss_ratio_low` | 0.984 |
| `variance_carry` | `iv_rv_spread_candidate` | 0.981 |
| `premium_to_stress_loss` | `gamma_rent_penalty_low` | 0.972 |

期权解释上，这很自然：`premium_to_iv10_loss`、`b3_iv_shock_coverage` 和 `b3_vomma_loss_ratio_low` 本质上都在问“这份权利金能不能覆盖 IV 上行和短 vega 凸性损失”；`premium_to_stress_loss`、`b3_joint_stress_coverage`、`gamma_rent_penalty_low` 都在问“这份权利金能不能覆盖 spot/IV 联合压力和 gamma 路径亏损”。

策略含义是：下一版 composite score 应先做因子族归并。每个族保留一个解释最清楚、实盘可控性最强的代表变量，而不是把高度相关变量全部加进去。否则评分会虚假放大某一种风险覆盖维度。

## 7. 组内 IC：强项是执行价选择，不是全市场 alpha

![组内 IC 热力图](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/09_within_product_side_ic_heatmap.png)

组内 IC 的计算单位是 `signal_date + product + option_type`，也就是同一天、同一品种、同一 Put/Call 方向内部比较不同合约。这里的 IC 很高：`friction_ratio_low` 对 `PnL/Margin` 为 0.376，`premium_yield_margin` 为 0.374，`b3_vomma_loss_ratio_low` 为 0.366，`premium_to_iv10_loss` 为 0.355。

这说明很多信号来自合约链几何结构：同一条链上，不同行权价的权利金、delta、保证金、stress loss 之间有稳定排序关系。这个结论不是坏事，反而很有用，因为我们的 B0/B1/B2 本来就需要在 delta 小于 0.1 的多个候选里选“更值得卖”的合约。

但组内止损结果给出了警告：在同一链条内，`premium_yield_margin` 的 stop avoidance IC 为 -0.117，`friction_ratio_low` 为 -0.120，`b3_vomma_loss_ratio_low` 为 -0.101，`premium_to_iv10_loss` 为 -0.077。也就是说，追求更高单位保证金收益或更厚权利金覆盖，可能同时把我们推向更容易触发止损的合约。

因此，合约选择不能只做“收益最大化”。更合理的是两阶段：

```text

第一步：硬过滤低价、低权利金、高费用占比、流动性差的合约。
第二步：在剩余合约中，用 premium_to_iv / premium_to_stress / iv_shock_coverage 做收益排序。
第三步：用 breakeven cushion、vol-of-vol、stop risk 对排序做惩罚，而不是让收益因子单独决定。

```

## 8. 残差 IC：控制价格、权利金、保证金和压力后还剩多少

![残差 IC 热力图](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/10_residual_ic_plus_margin_stress.png)

残差 IC 是这次最重要的“刹车”。它控制了 `log_entry_price`、`log_open_premium_cash`、`log_margin_estimate`、`log_stress_loss`、`DTE`、`abs_delta` 后，再看因子是否还有增量解释。

对 `PnL/Margin` 的双残差 IC 如下：

| 因子 | 控制价格/权利金/DTE/Delta 后 | 再控制保证金后 | 再控制保证金/压力后 |
| --- | ---: | ---: | ---: |
| `premium_yield_margin` | 0.100 | 0.017 | 0.022 |
| `premium_to_stress_loss` | 0.073 | 0.036 | 0.026 |
| `b3_iv_shock_coverage` | 0.073 | 0.034 | 0.025 |
| `premium_to_iv10_loss` | 0.072 | 0.034 | 0.024 |
| `b3_vol_of_vol_proxy_low` | -0.007 | 0.027 | 0.036 |
| `b3_vomma_loss_ratio_low` | 0.015 | -0.012 | -0.007 |
| `friction_ratio_low` | -0.032 | -0.045 | -0.030 |

解释要克制：控制项加入后，多数因子的增量明显收缩，说明原始 IC 很大一部分来自合约价格、权利金、保证金和 stress denominator 的共同结构。保留下来的 0.02-0.04 残差信号仍然有价值，但它更像“排序微调”和“风险预算校准”，不是可以让我们大幅加杠杆的独立 alpha。

`b3_vol_of_vol_proxy_low` 很有意思：原始 `PnL/Margin` IC 是 -0.033，但在控制保证金和压力后残差 IC 变成 0.036。我的理解是，它不适合作为简单的正向收益排序，但适合做条件变量：当其他承保质量已经相近时，较低 vol-of-vol 的候选更干净；当 vol-of-vol 高时，不应直接给更多预算。

## 9. 非重叠稳健性：不是纯重复样本幻觉

![非重叠 IC 对比](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/11_nonoverlap_ic_pnl_per_margin.png)

为了防止日频候选池里同一合约连续多日出现导致 t-stat 虚高，我们做了两类非重叠检验：每 5 个信号日取 1 天，以及每个合约代码只保留第一次出现。

每 5 日抽样下，`premium_to_iv10_loss` 的 `PnL/Margin` IC 为 0.199，`premium_to_stress_loss` 为 0.186，`b3_vomma_loss_ratio_low` 为 0.209，`premium_yield_margin` 为 0.208。每个代码只保留第一次出现时，`premium_to_iv10_loss` 为 0.158，`premium_to_stress_loss` 为 0.157，`b3_vomma_loss_ratio_low` 为 0.162，`premium_yield_margin` 为 0.182。

这说明两个事实同时成立：第一，因子不是完全由重复样本制造出来的；第二，非重叠以后 IC 会下降，尤其是 first signal per code 口径更保守。因此后续汇报不能用原始 t-stat 讲“显著性”，要更多看路径稳定性、样本外、分层方向和实际回测表现。

## 10. Product / Product-Side 层：预算倾斜只能轻用

![Product-Side 校正 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side_primary/01_corrected_ic_heatmap.png)

Product-Side 层回答的是“今天这个品种的 Put 或 Call 方向是否值得多给预算”。结果显示，`premium_to_stress_loss` 对 `PnL/Margin` 的 IC 为 0.171，`premium_yield_margin` 为 0.167，`b3_iv_shock_coverage` 为 0.155，`premium_to_iv10_loss` 为 0.155，`b3_vomma_loss_ratio_low` 为 0.142。

![Product-Side 保证金收益累计 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side_primary/04_corrected_cum_ic_pnl_per_margin.png)

Product-Side 的累计 IC 说明，风险覆盖类因子确实可以做轻度预算倾斜。但这个倾斜应该是“等权基础上的小幅偏移”，而不是大幅集中。原因是 product-side 层的样本更少，且同一品种方向在不同市场环境下行为会变化。

![Product 校正 IC](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_primary/01_corrected_ic_heatmap.png)

Product 层进一步聚合后，`variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate` 并没有表现出足够稳健的正向效果。这个结果和我们的直觉有冲突，因为理论上卖权应该看 IV/RV carry。我的判断是：当前 IV/RV 因子口径还不够干净，可能混入了次月期限、商品不同上市周期、RV 估计窗口和 shadow 标签执行方式的问题。它不能被废掉，但暂时不能作为 B2/B3 的主排序因子。

## 11. 因子用途地图

![因子用途地图](../output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/12_factor_usage_map.png)

因子不应该只分“有效/无效”，而要分“用在哪里”。本次审计后的建议如下：

| 因子族 | 代表因子 | 建议用途 | 不建议用途 |
| --- | --- | --- | --- |
| 交易摩擦 | `friction_ratio_low`、`fee_ratio_low` | 硬过滤、交易可行性控制 | alpha、预算加权 |
| IV shock 覆盖 | `premium_to_iv10_loss`、`b3_iv_shock_coverage` | 合约排序、轻度 product-side 倾斜 | 重复计票、单独大幅加仓 |
| Stress 覆盖 | `premium_to_stress_loss`、`b3_joint_stress_coverage` | 合约排序、stress budget 分配 | 和高度相关因子叠加放大 |
| 资本效率 | `premium_yield_margin` | 合约排序、资本效率微调 | 单独追求高收益率 |
| Vega 凸性 | `b3_vomma_loss_ratio_low` | vega 凸性惩罚、合约排序辅助 | 作为独立 alpha |
| Gamma 风险 | `gamma_rent_penalty_low` | stop risk 控制、压力收益排序 | 牺牲过多 theta 后单独使用 |
| 环境风险 | `b3_vol_of_vol_proxy_low`、`b3_forward_variance_pressure_low` | regime penalty、冷却期、重开约束 | 简单正向加预算 |
| IV/RV Carry | `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate` | 暂作观察，需重做标签 | 当前不单独进入预算 |
| 综合分 | `premium_quality_score` | 对照基准、回归控制项 | 黑箱直接上线 |

这张用途地图的核心是：B2/B3 因子应该拆开使用。把所有东西混成一个分数，会掩盖“收益效率”和“止损风险”之间的冲突，也会把高度相关的 IV shock / stress coverage 重复计票。

## 12. 对 B2/B3 的重新判断

B2C 的结果之所以能带来更多权利金收入，是因为它确实提升了“权利金相对风险”的排序质量。但这次审计说明，收益提升未必都来自真实独立 alpha，很多来自合约链结构和资本效率倾斜。

作为期权卖方，下一步不应简单扩大 B2C 风险预算，而应该把它拆成三个模块：

```text

模块一：硬过滤
低价、低权利金、高费用占比、明显低流动性先剔除。

模块二：收益排序
用 premium_to_iv10_loss、premium_to_stress_loss、b3_iv_shock_coverage、premium_yield_margin 做合约排序。

模块三：风险惩罚
用 breakeven cushion、gamma rent、vol-of-vol、forward variance pressure 控制止损概率和尾部跳价。

```

如果后续要做预算倾斜，应优先在 product-side 层使用 `premium_to_stress_loss` 和 `premium_to_iv10_loss` 的族代表，倾斜幅度从小开始，例如只在等权预算上做 20%-30% 的偏移，而不是直接让最高分品种拿到过多仓位。

## 13. 后续实验建议

第一，做 B4a：只改同品种同方向内的合约排序。保持 B1 的品种池、预算和组合约束不变，只把合约排序改成“IV shock 覆盖 + stress 覆盖 + 资本效率 + gamma/stop 惩罚”。这是最干净的验证，因为它直接检验这批因子最强的应用层级。

第二，做 B4b：轻度 product-side 预算倾斜。只允许 `premium_to_stress_loss`、`premium_to_iv10_loss`、`breakeven_cushion_score` 进入预算倾斜，`vol_of_vol` 只做惩罚，不做正向加分。倾斜幅度分 0.2、0.3、0.5 三档。

第三，做 B4c：补真实 vega/gamma 标签。现在 full shadow 标签只有总 PnL、止损和比例收益，不能回答我们最关心的问题：是否真的赚 theta 和 vega。后续 outcome 应输出 `future_theta_pnl`、`future_vega_pnl`、`future_gamma_pnl`、`future_iv_change_pnl`，否则 vega 目标只能间接判断。

第四，做 B4d：重新定义 IV/RV carry。当前 `variance_carry` 和 `iv_rv_spread_candidate` 在 Product 层表现弱，不代表理论错了，更可能是口径不够好。需要按次月期限、商品/ETF/股指分组、未来持有期 RV、波动跳升标签重新做。

第五，做 B4e：分环境检验。因子应该按降波、升波、低波稳定、VCP 收缩、stop cluster 环境分别出 IC 和分层。卖权最赚钱的时候通常是降波，但长期低波收缩后可能是尾部风险累积，这两个环境不能混在一个均值里。

## 14. 最终结论

这次校正以后，我们对 B2/B3 因子的态度应该是“更相信它们的方向，但更克制地使用它们”。它们证明了：在 full shadow 候选池中，权利金相对 IV shock、stress loss、gamma rent 和保证金占用更厚的合约，确实有更好的风险调整表现；但它们没有证明：这些因子可以单独预测绝对收益，或者可以支撑大幅提高组合风险预算。

下一版策略的正确方向不是把 B2C 的高分因子全加进去，而是建立一个更像承保系统的流程：

```text

先用硬过滤避免低价和摩擦陷阱；
再用风险覆盖因子选择更厚的权利金；
再用 stop/gamma/vol-of-vol 因子压住尾部；
最后只在 product-side 层做轻度预算倾斜。

```

如果我们能在这个框架下继续提高 theta 收入，同时让 vega 亏损不再扩大，S1 才有可能从“复杂但保守”走向“可解释、可交易、可扩展”的纯卖权策略版本。
