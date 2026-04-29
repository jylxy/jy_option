# S1 B6-product 品种筛选初步审计报告

生成日期：2026-04-29  
输入标签：`s1_b5_full_shadow_v1_2022_latest`  
输出目录：`output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/`

## 1. 核心结论

这次审计说明：**我们不是没有品种层信号，而是之前没有把品种层信号和合约层信号分开检验。**

从 B5 full shadow 聚合到品种层后，最有效的品种筛选信号并不是传统 `IV/RV carry`，而是下面几类：

1. 权利金 / 保证金：`product_premium_to_margin`
2. 权利金 / stress：`product_premium_to_stress`
3. 单位权利金的 gamma / vega 风险：`product_gamma_per_premium_low`、`product_vega_per_premium_low`
4. theta / gamma：`product_theta_per_gamma`
5. P/C 侧的 delta 梯队和权利金厚度：`side_avg_abs_delta`、`side_premium_to_margin`、`side_premium_to_stress`

这说明品种筛选的主线应该是：

```text
不是简单判断“哪个品种 IV 高”，
而是判断“这个品种的权利金池是否足够厚，且相对保证金、stress、vega/gamma 是否足够补偿”。
```

因此，下一步确实应该把品种筛选作为独立模块做，而不是只在合约层挑腿。

## 2. Product 层结果

Product 层样本单位是：

```text
signal_date + product
```

它回答：

```text
今天这个品种该不该多给预算？
```

### 2.1 Product 层 IC

`future_pnl_per_margin` 上表现最强的因子：

| 因子 | mean IC | t-stat | 正 IC 比例 | 解释 |
| --- | ---: | ---: | ---: | --- |
| `product_premium_to_margin` | 0.253 | 31.09 | 81.93% | 品种层最强信号，说明单位保证金权利金厚度有明确解释力。 |
| `product_gamma_per_premium_low` | 0.218 | 30.20 | 81.75% | 单位权利金 gamma 风险越低，品种后续表现越好。 |
| `product_premium_to_stress` | 0.213 | 30.83 | 82.76% | 权利金对压力亏损覆盖越厚，品种表现越好。 |
| `product_stress_per_premium_low` | 0.213 | 30.83 | 82.76% | 与上一个互为倒数，方向一致。 |
| `product_vega_per_premium_low` | 0.195 | 29.82 | 81.93% | 单位权利金 vega 风险越低越好。 |
| `product_theta_per_gamma` | 0.172 | 26.71 | 81.93% | theta 相对 gamma 更划算的品种更好。 |

这组结果比我们之前预想更积极。它说明品种层预算并不是完全没信号，真正有效的是“品种级权利金质量”，而不是单纯 IV/RV。

### 2.2 Product 层 Q5-Q1

按 Q1-Q5 分层后：

| 因子 | PnL/Margin Q5-Q1 | Retention Q5-Q1 | Stop Avoidance Q5-Q1 |
| --- | ---: | ---: | ---: |
| `product_premium_sum` | 0.0219 | 2.1164 | 0.1205 |
| `product_premium_to_stress` | 0.0180 | 0.9246 | 0.0878 |
| `product_gamma_per_premium_low` | 0.0177 | 0.9999 | 0.0751 |
| `product_vega_per_premium_low` | 0.0160 | 0.7743 | 0.0910 |
| `product_avg_tail_coverage` | 0.0147 | 0.4727 | 0.0810 |
| `product_theta_per_gamma` | 0.0134 | 0.5019 | 0.0835 |
| `product_premium_to_margin` | 0.0101 | 0.6026 | 0.0974 |

比较重要的是，好的品种层因子不是只提高收益，也同步改善留存率和止损规避。这比单纯提高 Premium Pool 更健康。

![Product IC](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/01_factor_ic_heatmap.png)

![Product PnL/Margin Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/02_spread_pnl_per_margin.png)

![Product Retention Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/03_spread_retention.png)

![Product Stop Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/04_spread_stop_avoidance.png)

![Product Correlation](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/05_factor_correlation_matrix.png)

## 3. Product-side 层结果

Product-side 层样本单位是：

```text
signal_date + product + option_type
```

它回答：

```text
今天这个品种更适合卖 Put 还是卖 Call？
```

### 3.1 Product-side 层 IC

`future_pnl_per_margin` 上表现最强的因子：

| 因子 | mean IC | t-stat | 正 IC 比例 | 解释 |
| --- | ---: | ---: | ---: | --- |
| `side_premium_to_margin` | 0.340 | 49.12 | 91.95% | 最强 P/C 侧预算信号。 |
| `side_avg_abs_delta` | 0.279 | 43.64 | 89.21% | 在 delta<0.1 内，平均 delta 更靠近上限的侧更有效。 |
| `side_gamma_per_premium_low` | 0.277 | 47.00 | 90.71% | 单位权利金 gamma 风险越低越好。 |
| `side_premium_to_stress` | 0.270 | 46.93 | 90.46% | 某侧权利金对 stress 覆盖越厚越好。 |
| `side_vega_per_premium_low` | 0.252 | 44.88 | 90.12% | 单位权利金 vega 风险越低越好。 |
| `side_theta_per_gamma` | 0.205 | 37.50 | 87.14% | 某侧 theta/gamma 质量越好越值得卖。 |

这说明 P/C 侧预算可以有更明确的量化基础。尤其是 `side_premium_to_margin`、`side_premium_to_stress`、`side_avg_abs_delta`、`side_gamma_per_premium_low` 很强。

但这里要小心：`side_avg_abs_delta` 很强，不代表我们应该突破 `abs(delta)<0.1`。它只说明在 0.1 以内，太深虚的低价薄权利金合约质量差，0.06-0.10 区域更值得优先考虑。

### 3.2 趋势 / 突破类因子

P/C 侧趋势和突破因子也有正向解释力，但弱于权利金质量：

| 因子 | PnL/Margin IC | Stop Avoidance IC | 解释 |
| --- | ---: | ---: | --- |
| `side_breakout_cushion` | 0.067 | 0.043 | 远离对应方向突破的侧更安全。 |
| `side_momentum_alignment` | 0.025 | 未进前列 | 趋势对 P/C 选择有帮助，但不是主因子。 |
| `side_trend_alignment` | 未进 PnL 前列 | 0.042 | 趋势更适合做风险惩罚，而不是收益排序主因子。 |

这符合直觉：趋势能帮助我们少卖危险方向，但真正决定品种侧收益质量的，仍然是权利金是否足够覆盖 vega/gamma/stress。

![Product-side IC](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/01_factor_ic_heatmap.png)

![Product-side PnL/Margin Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/02_spread_pnl_per_margin.png)

![Product-side Retention Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/03_spread_retention.png)

![Product-side Stop Spread](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/04_spread_stop_avoidance.png)

![Product-side Correlation](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/05_factor_correlation_matrix.png)

## 4. 品种表现观察

按长期平均 `future_pnl_per_margin`，表现较好的品种包括：

| 品种 | 特征 |
| --- | --- |
| `LG` | 样本较短但表现好，需要观察上市后稳定性。 |
| `PK` | 留存率高、止损率低，值得重点关注。 |
| `UR` | 品种层表现较好。 |
| `SR` | 长样本下表现稳定，值得进入预算倾斜候选。 |
| `I` | 权利金池较厚，Put 侧表现尤其好。 |
| `SC` | 权利金池非常厚，整体 PnL 贡献大，但需要注意能源尾部风险。 |

表现较差的品种包括：

| 品种 | 问题 |
| --- | --- |
| `L` | 止损率高、留存率极差，典型尾部和路径风险品种。 |
| `PP` | 与 L 类似，化工链风险明显。 |
| `V` | 止损率高，留存差。 |
| `PS` | 权利金看似厚，但路径亏损明显。 |
| `LH` | 部分方向亏损严重，不能只看权利金池。 |
| `MO` | 权利金池很厚，但影子结果差，说明股指期权不能简单按权利金厚度加预算。 |

这部分说明：**品种筛选真的很重要**。有些品种即便权利金厚，也可能只是“卖方风险被正确高价定价”；有些品种权利金不最大，但留存率更好。

## 5. 对 B6 的直接启发

我建议 B6 后续拆成三步：

### B6-product-a：只做品种预算倾斜

不改变合约排序，只按 product 层质量轻度倾斜预算。

候选品种预算分：

```text
product_budget_score =
    35% * rank(product_premium_to_margin)
  + 30% * rank(product_premium_to_stress)
  + 15% * rank(product_vega_per_premium_low)
  + 15% * rank(product_gamma_per_premium_low)
  +  5% * rank(product_theta_per_gamma)
```

暂不引入 tail dependence，因为当前 tail dependence IC 较弱，但保留为组合层诊断。

### B6-product-b：做 product-side 预算倾斜

不改总品种预算，只在 Put/Call 两侧内分配。

候选方向预算分：

```text
side_budget_score =
    30% * rank(side_premium_to_margin)
  + 25% * rank(side_premium_to_stress)
  + 15% * rank(side_gamma_per_premium_low)
  + 15% * rank(side_vega_per_premium_low)
  + 10% * rank(side_avg_abs_delta)
  +  5% * rank(side_breakout_cushion)
```

其中 `side_avg_abs_delta` 只在 `abs(delta)<0.1` 硬约束内使用，不能突破 delta 上限。

### B6-product-c：品种 + P/C 两层联动

先用 product score 决定品种预算，再用 side score 决定同品种 Put/Call 预算。

这样比直接在合约层综合打分更清晰：

```text
先决定做哪些品种
再决定该品种做 Put 还是 Call
最后才在每个 product-side 内选具体合约
```

## 6. 当前限制

这次审计仍然有几个限制：

1. 结果来自 full shadow，仍然是候选研究标签，不是完整组合回测结果。
2. product 层因子与合约层因子仍可能存在信息重叠，后续需要做残差 IC。
3. tail dependence 当前解释力弱，不代表组合层不重要；它可能只能解释极端回撤，而不是每日 PnL/margin。
4. IV/RV carry 当前不强，可能是 RV 口径不够 forward-looking，不应直接放弃，但也不能先用于交易。
5. 下一步必须把 product budget tilt 真正接入回测，再看 NAV、回撤、vega/gamma 归因是否改善。

## 7. 结论

这次 B6-product 初步审计改变了我们对品种筛选的判断：

```text
品种筛选不是没有信号；
只是有效信号不是传统 IV/RV，
而是品种层的权利金质量、stress 覆盖、vega/gamma 风险覆盖和 P/C 侧承保质量。
```

因此，后续 S1 不应该只靠合约层排序。更合理的框架是：

```text
Product 层：决定预算给哪些品种
Product-side 层：决定该品种偏 Put、偏 Call，还是双卖
Contract 层：决定同侧次月内具体卖哪些低 Delta 合约
Portfolio 层：控制板块、到期、tail correlation 和 stop cluster
```

这条线很值得继续做，而且应该成为 B6 的主线之一。

