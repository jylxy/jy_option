# S1 Full Shadow 单因子分层检查报告

生成日期：2026-04-28  
样本：`s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest`  
分析对象：full shadow 候选池，而不是已成交订单  
输出目录：`output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/`

## 1. 执行摘要

这次 full shadow 检验的结论比 B2C 单次回测更重要：B2C 里有效的“权利金质量”不是一个单一因子，而是一组高度相关的承保质量因子。它们在合约层非常有效，但在品种预算层不能照搬；尤其是 `variance_carry`、`iv_rv_spread_candidate`、`premium_to_iv_shock_score` 这类看似合理的指标，如果直接用于品种加仓，反而可能把仓位推向高风险品种。

最适合进入下一版策略主线的因子有三类。第一类是合约层的权利金覆盖因子，包括 `premium_to_iv10_loss`、`b3_iv_shock_coverage`、`premium_to_stress_loss`、`b3_joint_stress_coverage`、`b3_vomma_loss_ratio_low`。这类因子预测的是“同一品种同一方向下，卖哪一个执行价更值得承保”。第二类是品种/方向层的环境稳定因子，最突出的是 `b3_vol_of_vol_proxy_low`，它对留存、止损率和止损跳价都有稳定改善，适合用于品种预算倾斜。第三类是交易摩擦因子，包括 `friction_ratio_low` 和 `fee_ratio_low`，它们的 IC 很高，但经济含义更接近“过滤低价、高费率、低可交易性合约”，不应该被当成预测 alpha 直接加仓。

需要特别警惕的是，绝对金额类风险因子不能直接正用。`stress_loss_low`、`iv_shock_loss_5_cash_low`、`gamma_rent_cash_low` 在合约层分层里反而表现很差，原因大概率是它们选中了价格很薄、权利金覆盖不足、容易被跳价和费用吞噬的深虚值合约。对卖权来说，风险不是“绝对亏损金额小”就安全，而是“收到的权利金能否覆盖 IV 冲击、Gamma 租金、压力亏损和交易摩擦”。

本轮报告的实务结论是：B4 不应该继续把所有因子揉成一个综合分，而应该分层使用。合约层做执行价排序，品种层做预算倾斜，成本层做硬过滤，vol-of-vol / vomma / skew 层做环境惩罚；同时每个高度共线因子族只保留一个代表，避免重复计分。

## 2. 数据口径与标签

本次分析使用的是 full shadow 候选池。它的意义是：把每日所有满足 B1 基础候选条件的合约都贴上“如果开 1 手并按规则持有，未来会怎样”的标签，然后做横截面分层检验。这比只看已成交订单更接近因子研究，因为已成交样本已经被旧策略筛过，会有严重选择偏差。

样本规模如下：

| 层级 | 样本单位 | 样本数 | 交易日 | 品种数 | 平均留存率 | 止损率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Contract | 日期-合约 | 518,048 | 1,215 | 65 | -2.318 | 33.94% |
| Product-Side | 日期-品种-方向 | 74,880 | 1,215 | 65 | -1.015 | 31.00% |
| Product | 日期-品种 | 42,301 | 1,215 | 65 | -0.984 | 30.09% |

这些数值不是策略绩效。它们是“完整候选池”的承保结果，所以会包含很多我们最终不会交易的低质量合约。尤其 contract 层平均留存率为负，恰恰说明 full shadow 里有大量不值得承保的候选，因子研究的目标就是识别它们。

本次核心标签包括：

| 标签 | 含义 | 解释 |
| --- | --- | --- |
| `future_net_pnl_per_premium` | 未来净收益 / 开仓权利金 | 越高代表这笔承保越赚钱 |
| `future_retained_ratio` | 未来留存权利金 / 开仓权利金 | 本批数据中与净收益率基本等价 |
| `future_stop_avoidance` | 负止损标记 | 越高代表越不容易止损 |
| `future_stop_loss_avoidance` | 负止损损耗 | 越高代表止损损耗越小 |
| `future_stop_overshoot_avoidance` | 负止损跳价超额 | 越高代表触发止损后的跳价越温和 |

当前 full shadow 缺少两个重要标签：真实 `vega_pnl_per_premium` 和真实 `gamma_pnl_per_premium`。因此本报告不能声称某个因子已经直接改善了真实 Vega 收益，只能说它改善了 IV 冲击覆盖、vomma 代理、止损跳价和权利金留存。后续需要把 PnL attribution 的 vega/gamma 标签接入 shadow outcome。

## 3. 决策层级

S1 的因子不能只回答“哪个因子 IC 高”。卖权策略至少有四个决策层：

| 决策层 | 样本单位 | 需要回答的问题 | 本轮可用因子 |
| --- | --- | --- | --- |
| 选品种 | `signal_date + product` | 今天哪些品种值得给预算 | vol-of-vol、vomma、breakeven、历史跳价风险 |
| 选方向 | `signal_date + product + option_type` | 卖 Put、卖 Call，还是双卖 | P/C 方向上的 carry、skew、趋势和 shock coverage |
| 选合约 | `signal_date + product + option_type + code` | 同品种同方向卖哪个执行价 | premium_to_iv_loss、premium_to_stress_loss、theta/vega、gamma rent、friction |
| 环境调节 | `date` 或 `date + product` | 是否加预算、降预算或暂停 | falling/rising vol、vol-of-vol、VCP、stop cluster |

本轮结果显示：合约层与品种层的有效因子并不完全相同。合约层适合用“权利金覆盖风险”的指标；品种层更适合用“环境稳定和波动率二阶风险”的指标。

## 4. Contract 层：最强信号来自权利金覆盖质量

Contract 层 Rank IC 非常强，说明同一天、同一批候选合约之间，因子确实能区分未来承保质量。对 `future_retained_ratio` 和 `future_net_pnl_per_premium` 的前几名几乎一致：

| 因子 | Mean IC | t-stat | 正 IC 占比 | 解释 |
| --- | ---: | ---: | ---: | --- |
| `friction_ratio_low` | 0.458 | 97.12 | 99.51% | 低摩擦/高权利金合约显著更好 |
| `premium_yield_notional` | 0.442 | 111.69 | 99.75% | 名义权利金收益率越高，承保质量越好 |
| `b3_vomma_loss_ratio_low` | 0.436 | 105.43 | 99.34% | IV 凸性冲击相对权利金越小越好 |
| `premium_to_iv10_loss` | 0.423 | 101.24 | 99.18% | 权利金覆盖 10vol IV 冲击越厚越好 |
| `b3_iv_shock_coverage` | 0.417 | 99.68 | 99.18% | 与 IV shock 覆盖高度等价 |
| `premium_yield_margin` | 0.411 | 106.40 | 99.84% | 保证金权利金收益率越高越好 |

![Contract IC Heatmap](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/05_factor_ic_heatmap.png)

![Contract Cumulative IC](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/06_cum_ic_net_pnl.png)

累计 IC 图补充的是“稳定性”视角。Contract 层最强的几个因子不是只在少数极端日期有效，而是在 2022 至最新样本里持续累积正 IC；这比单点均值更重要，因为 S1 后续要做的是系统化规则，不是挑一段行情。对卖方而言，累计 IC 持续向上说明该因子长期在帮助我们识别更高质量的承保费。

这张图的核心不是“所有绿色都加仓”，而是告诉我们：contract 层的有效信息主要集中在权利金覆盖、摩擦成本、IV shock 覆盖、vomma 损失覆盖这几个族。它们预测的不是标的方向，而是“这份权利金够不够厚”。这与卖权策略的经济逻辑一致：我们不是靠方向预测赚钱，而是靠承保费足够覆盖正常和中等压力情景。

Q1-Q5 分层更直观。`premium_to_iv10_loss` 的高分层比低分层多留存约 7.44 倍开仓权利金，止损率降低约 25.11 个百分点，止损跳价损耗改善约 2.65 倍权利金。`b3_vomma_loss_ratio_low` 的分层效果更强，高低分层留存差约 7.49，止损率降低约 24.75 个百分点。这说明“Vega/Vomma 风险相对权利金的覆盖率”确实是 S1 下一阶段必须保留的合约排序指标。

![Contract Retained Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/03_layer_retained_heatmap.png)

![Contract Stop Rate Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/04_layer_stop_rate_heatmap.png)

![Contract Stop Rate By Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/07_stop_rate_by_layer.png)

![Contract Q5-Q1 Spread](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/01_factor_spread_cumulative.png)

止损分层图是这份报告必须保留的关键证据。它说明部分因子并不只是提高权利金收益，也同步降低了未来止损概率；但也能看到绝对 `stress_loss_low` 这类因子方向相反，低风险金额并不等于低止损概率。这个图直接支持后续规则：contract 层应优先使用“权利金 / 风险”的覆盖型因子，而不是单独偏好低风险金额。

但是有一个反直觉结果非常重要：`stress_loss_low`、`iv_shock_loss_5_cash_low`、`gamma_rent_cash_low` 这类“绝对风险金额低”的因子在 contract 层反而表现很差。`stress_loss_low` 的高低分层留存差为 -7.66，止损率反而上升 23.44 个百分点。这不是说压力测试没用，而是说明绝对金额不能单独使用。深虚值低价合约的现金 stress loss 看起来小，但权利金也极薄，手续费和异常跳价会把收益结构打穿。我们应该使用 `premium_to_stress_loss`，而不是直接偏好低 `stress_loss`。

## 5. Product-Side 与 Product 层：vol-of-vol 比 IV/RV 更像预算因子

Product-Side 层回答的是“某品种某方向今天是否值得给更多预算”。这一层最重要的发现是：`b3_vol_of_vol_proxy_low` 的分层改善非常稳定。它在 product-side 层 Q5-Q1 留存改善约 3.38，止损率降低约 14.66 个百分点；在 product 层留存改善约 3.43，止损率降低约 15.58 个百分点。

![Product-Side IC Heatmap](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side/05_factor_ic_heatmap.png)

![Product-Side Cumulative IC](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side/06_cum_ic_net_pnl.png)

![Product-Side Retained Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side/03_layer_retained_heatmap.png)

![Product-Side Stop Rate By Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side/07_stop_rate_by_layer.png)

![Product-Side Q5-Q1 Spread](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product_side/01_factor_spread_cumulative.png)

Product-Side 的累计 IC 与止损分层图共同说明：这一层最值得关注的不是“哪个合约最厚”，而是“哪个品种方向当前的波动率状态更适合承保”。`b3_vol_of_vol_proxy_low` 在留存和止损两端都更稳，因此更适合做品种-方向预算倾斜，而不是合约执行价排序。

这与我们前面讨论的“卖权最怕升波切换”是一致的。低 vol-of-vol 并不一定意味着 IV 低，而是意味着波动率自身不再剧烈跳动，权利金更容易被 theta 消化。如果一个品种 IV/RV 看似有 carry，但 vol-of-vol 很高，说明市场正在重新定价风险，这种权利金可能不是便宜保险费，而是事故前的保费重估。

Product 层也支持这个判断：

| 因子 | Product Mean IC | Q5-Q1 留存差 | Q5-Q1 止损率差 | 含义 |
| --- | ---: | ---: | ---: | --- |
| `b3_vol_of_vol_proxy_low` | 0.170 | 3.428 | -15.58% | 适合做品种预算倾斜 |
| `b3_vomma_loss_ratio_low` | 0.224 | 1.094 | -9.48% | 适合预算和合约双层使用 |
| `breakeven_cushion_score` | 0.186 | 1.386 | -10.40% | 适合品种/方向层做安全垫 |
| `gamma_rent_penalty_low` | 0.180 | 1.065 | -5.35% | 更适合约束近月短 gamma |
| `premium_to_stress_loss` | 0.168 | 0.856 | -5.64% | 可做风险覆盖下限 |

![Product IC Heatmap](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product/05_factor_ic_heatmap.png)

![Product Cumulative IC](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product/06_cum_ic_net_pnl.png)

![Product Retained Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product/03_layer_retained_heatmap.png)

![Product Stop Rate By Layer](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product/07_stop_rate_by_layer.png)

![Product Q5-Q1 Spread](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/product/01_factor_spread_cumulative.png)

Product 层图组进一步验证了这个判断：品种预算层的核心不是单纯追求 IV/RV carry，而是识别“波动率自身是否稳定、二阶风险是否可承受”。如果 Product 层累计 IC 稳定、止损分层也改善，才有资格进入预算放大；如果只在留存上有效但止损图恶化，则只能作为收益解释，不能作为加仓依据。

这也解释了为什么之前 B3 的一些直接倾斜没有明显打赢 B2C。B3 的很多因子如果作为“全局预算倾斜”会过粗；它们应该被分配到对应层级。例如 `premium_to_iv10_loss` 很适合 contract 排序，但不一定适合 product 预算；`b3_vol_of_vol_proxy_low` 更适合 product 预算，但不应该替代合约层执行价选择。

## 6. 反向信号：IV/RV carry 不能直接当加仓理由

本轮最值得写进策略纪律的是：`iv_rv_spread_candidate`、`iv_rv_ratio_candidate`、`variance_carry` 在 product 层分层表现偏负。Product 层 Q5-Q1 留存差分别约为 -2.03、-2.03 和 -1.85，止损跳价也更差。

这不代表 IV/RV carry 没有经济意义，而是说明它不能孤立使用。高 IV/RV 往往同时出现在风险正在被重新定价的品种上，如果没有 vol-of-vol、skew steepening、趋势和跳价风险约束，高 carry 可能只是“危险保费”。这与卖保险的直觉一致：保费贵不代表一定划算，可能是因为事故概率正在上升。

因此下一版不应该把 `variance_carry` 当作硬筛选或直接预算放大因子。更合理的用法是：

- 作为收益来源解释变量保留。
- 仅在 `vol_of_vol` 不高、RV 不抬升、skew 未恶化时才允许正向加分。
- 与 `premium_to_iv10_loss`、`premium_to_stress_loss` 做交互，而不是单独排序。
- 如果其与高止损率同步出现，应作为“高风险高保费”而不是“高质量权利金”。

## 7. 相关性矩阵：必须每个因子族只留一个代表

相关性矩阵显示，很多因子本质上是同一个东西的不同写法。典型高相关关系如下：

| 因子 A | 因子 B | Spearman 相关 |
| --- | --- | ---: |
| `fee_ratio_low` | `friction_ratio_low` | 1.000 |
| `premium_to_stress_loss` | `b3_joint_stress_coverage` | 1.000 |
| `premium_to_iv5_loss` | `b3_iv_shock_coverage` | 0.9999 |
| `premium_to_iv10_loss` | `b3_iv_shock_coverage` | 0.9993 |
| `premium_to_iv5_loss` | `premium_to_iv10_loss` | 0.9985 |
| `iv_rv_spread_candidate` | `variance_carry` | 0.983 左右 |

![Contract Correlation Matrix](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/08_factor_correlation_matrix.png)

这张图的策略含义很明确：不能把 `premium_to_iv5_loss`、`premium_to_iv10_loss`、`b3_iv_shock_coverage` 同时放进一个 composite score 里，否则会重复奖励同一类“IV shock 覆盖”信息。同理，`premium_to_stress_loss` 和 `b3_joint_stress_coverage` 只能二选一。

建议保留的代表因子如下：

| 因子族 | 代表因子 | 原因 |
| --- | --- | --- |
| 成本/摩擦 | `friction_ratio_low` | 与 fee_ratio 等价，但更贴近交易可行性 |
| IV shock 覆盖 | `premium_to_iv10_loss` | 比 5vol 更严格，更贴近卖权尾部管理 |
| Stress 覆盖 | `premium_to_stress_loss` | 经济含义直观，适合做覆盖下限 |
| Vega/Vomma | `b3_vomma_loss_ratio_low` | 对留存和止损均有效，能服务 Vega 风控目标 |
| 环境稳定 | `b3_vol_of_vol_proxy_low` | 在 product/product-side 层最稳定 |
| Gamma 租金 | `gamma_rent_penalty_low` | 适合做短 gamma 惩罚，而不是收益排序 |
| IV/RV | 暂不单独正用 | 需要与 vol-of-vol、RV trend、skew 交互 |

## 8. 正交化结果：真正有增量的是摩擦、vol-of-vol 和部分覆盖率

Contract 层做了残差 IC。控制主因子族后，仍然有增量解释的因子包括：

| 因子 | Retained Residual IC | t-stat | 正残差 IC 占比 | 判断 |
| --- | ---: | ---: | ---: | --- |
| `friction_ratio_low` | 0.137 | 29.06 | 80.90% | 成本/低价陷阱有独立信息 |
| `b3_vol_of_vol_proxy_low` | 0.090 | 22.82 | 76.91% | 环境稳定信息有增量 |
| `premium_yield_margin` | 0.066 | 18.42 | 70.43% | 权利金收益率仍有边际信息 |
| `premium_to_iv10_loss` | 0.066 | 20.60 | 74.58% | IV shock 覆盖仍有边际信息 |

![Contract Orthogonal IC](../output/candidate_layers_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/contract/09_orthogonal_ic_heatmap.png)

正交化结果说明，B2C 的成功不是纯粹过拟合。即使控制了主权利金质量族，交易摩擦、vol-of-vol 和 IV shock 覆盖仍然有增量。但是这也说明下一版不能简单堆因子。我们应该用“代表因子 + 分层用途”的方式构造规则，而不是继续扩大一个总分模型。

需要注意，聚合层 product/product-side 的正交化本轮为了效率主动跳过。原因是 full shadow 聚合层正交在服务器上耗时过长，而 contract 层已经足以验证合约排序的残差信息。后续如果要写入论文式研究底稿，应单独跑 product 层的轻量残差检验，或者用分年度抽样验证。

## 9. 因子用途地图

以下是基于本轮 full shadow 的建议落地位置。

| 用途 | 建议因子 | 是否进入下一版 |
| --- | --- | --- |
| 选品种预算 | `b3_vol_of_vol_proxy_low`、`b3_vomma_loss_ratio_low`、`breakeven_cushion_score` | 是，作为预算倾斜 |
| 选 Put/Call 方向 | 暂用 product-side 层的 `b3_vomma_loss_ratio_low`、`vol_of_vol`，但需要加入趋势/skew 专项标签 | 谨慎进入 |
| 选合约排序 | `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_vomma_loss_ratio_low`、`gamma_rent_penalty_low` | 是，作为核心排序 |
| 硬过滤 | `friction_ratio_low`、`fee_ratio_low`、最低权利金、极端跳价过滤 | 是，不能只做排序 |
| 止损/跳价风险 | `future_stop_overshoot` 的历史代理、`vol_of_vol`、`vomma_loss_ratio` | 需要新增字段后进入 |
| 环境调节 | `b3_vol_of_vol_proxy_low`、falling/rising vol、VCP、stop cluster | 是，但要做状态交互 |
| 暂不采用 | 单独 `variance_carry`、单独 `iv_rv_spread_candidate`、绝对 `stress_loss_low` | 暂不正向使用 |

## 10. 对 B4/B5 实验的建议

下一步实验不建议再做“一个更复杂的综合分”。更合理的是分层实验：

1. **B4a：合约层覆盖排序实验**  
   在 B1/B2C 基础上，只改变同品种同方向内的合约排序。排序使用 `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_vomma_loss_ratio_low`、`gamma_rent_penalty_low`，不改变品种预算。目标是验证“选更厚的权利金覆盖”是否能提高留存并降低止损。

2. **B4b：品种预算 vol-of-vol 倾斜实验**  
   只用 `b3_vol_of_vol_proxy_low` 和 `b3_vomma_loss_ratio_low` 调整品种预算，不改变合约排序。目标是确认低 vol-of-vol 品种是否可以获得更多预算，而高 vol-of-vol 品种是否应该降权。

3. **B4c：交易摩擦硬过滤实验**  
   将 `friction_ratio`、最低权利金、手续费/权利金占比作为硬过滤，而不是排序因子。目标是降低低价合约、跳价合约和手续费吞噬对结果的污染。

4. **B4d：去共线 composite 实验**  
   每个因子族只保留一个代表：`friction_ratio_low`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_vomma_loss_ratio_low`、`b3_vol_of_vol_proxy_low`、`gamma_rent_penalty_low`。所有因子 winsorize + 横截面 zscore，不允许同一族重复计分。

5. **B5：P/C 方向专项实验**  
   目前 product-side 层还不足以回答“卖 Put 还是卖 Call”。需要补充趋势、动量、skew steepening、上下尾风险、P/C 权利金覆盖差的专项标签，再做方向层分层。这个实验应单独设计，不应由 B2C 合约质量因子代替。

## 11. 结论

这次 full shadow 检验把 S1 下一阶段的方向拆得更清楚了。B2C 之所以有效，不只是因为“多收权利金”，而是因为它无意中偏向了权利金对 IV shock、stress loss、vomma 和摩擦成本覆盖更厚的合约。但如果把这些因子直接提升到品种预算层，部分指标会失效甚至反向。

因此，S1 的下一版应该采用“分层因子架构”：合约层追求权利金覆盖质量，品种层追求低 vol-of-vol 和低二阶波动风险，成本层设置硬过滤，环境层控制预算放大与收缩。这样更符合卖权策略的本质：不是预测方向，而是判断这份承保费是否足够补偿未来波动、跳价、Gamma、Vega 和交易摩擦。

本轮最明确的策略纪律是：不要因为 IV/RV carry 高就加仓，也不要因为绝对 stress loss 小就认为安全。真正应该买账的是“权利金相对风险足够厚”，以及“波动率自身不再剧烈恶化”。
