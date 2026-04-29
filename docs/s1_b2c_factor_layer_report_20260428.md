# S1 B2c 因子分层检验报告

报告日期：2026-04-28  
研究对象：`s1_b2_product_tilt075_stop25_allprod_2022_latest`  
分析目录：

- 品种-方向级：`output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_product_side`
- 合约级：`output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_contract`

## 1. 执行摘要

这次分层检验的结论比较明确：B2c 的因子体系是有信息量的，但不应该继续只作为一个综合预算倾斜分数使用。更合理的方式是把它拆成五类：硬筛选、合约选腿排序、品种方向预算、止损概率控制、环境调制。

最重要的发现有四个。

第一，`premium_quality_score` 这个综合分本身并不是最强因子。在品种-方向级，它对未来 `net_pnl_per_premium` 的 Rank IC 只有 `0.0165`，t-stat 约 `0.56`，明显弱于 `friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 和 `theta_vega_efficiency`。这说明 B2c 当前综合分可能把强因子和弱因子混在一起，稀释了信息。

第二，合约级因子非常强，尤其是费用和风险覆盖相关指标。合约级 `friction_ratio` 对未来净收益/权利金的 Rank IC 为 `0.2440`，t-stat `14.68`；`premium_to_iv10_loss` 为 `0.2261`，t-stat `12.42`；`premium_to_stress_loss` 为 `0.2030`，t-stat `10.35`。这些因子更像“这张合约值不值得卖”的判断，应优先用于选腿和硬过滤，而不只是品种预算。

第三，`theta_vega_efficiency` 更像止损概率因子，而不是单纯收益因子。合约级它对 `stop_avoidance` 的 Rank IC 为 `0.0955`，t-stat `4.06`；品种-方向级也排在止损避免的第一位，mean IC `0.0654`，t-stat `2.63`。这意味着它可能适合控制手数、铺单厚度、止损后重开，而不是简单加大预算。

第四，环境切片显示 Put 侧信号更清晰，Call 侧更弱。品种-方向级 Put 侧 `friction_ratio` 的收益 IC 为 `0.2346`，而 Call 侧为 `0.1198`；Put 侧 `b2_product_score` 对止损避免 IC 为 `0.1078`，Call 侧没有同等级别效果。B2c 后续不能只做全市场统一因子，应至少区分 Put/Call。

## 2. 我们要预测的不是涨跌，而是承保质量

S1 是卖权收权利金策略，因子分层检验不应该预测标的涨跌，也不应该直接预测明天 IV 点位。我们真正要预测的是一张卖权交易未来是不是一张“好保险单”。

本次报告使用四个未来标签：

| 标签 | 含义 | 越高是否越好 | 策略意义 |
|---|---|---|---|
| `net_pnl_per_premium` | 未来净 PnL / 开仓权利金 | 是 | 这张保单最终是否赚钱 |
| `retained_ratio` | 留存权利金 / 开仓权利金 | 是 | 收到的权利金是否留得住 |
| `stop_avoidance` | 负止损率 | 是 | 是否更少触发 2.5x 止损 |
| `stop_loss_avoidance` | 止损损耗 / 开仓权利金 | 是 | 即使止损，损耗是否更轻 |

Rank IC 的方向已经统一调整为“越高越好”。例如 `friction_ratio` 和 `gamma_rent_penalty` 是低值更好，计算 IC 时按反向处理。因此 IC 为正，代表因子排序方向正确。

## 3. 方法和局限

本次分层有两个层级。

品种-方向级是主口径，样本单位为 `signal_date + product + option_type`。它回答的是：今天哪些品种、哪一侧应该获得更多预算。这个层级最接近 B2c 当前实际用途。

合约级是选腿口径，样本单位是一张实际成交期权合约。它回答的是：同一品种、同一方向、同一次月内，哪张执行价更值得卖。

每个交易日按因子横截面分成 Q1-Q5，并计算高分层和低分层的未来表现。同时计算 Rank IC、IC t-stat、正 IC 占比、止损率、权利金留存率和分层累计路径。

重要局限：本次分析使用的是已成交样本，不是完整候选池。它能回答“策略实际交易过的样本里，哪些因子有解释力”，但还不能完全回答“所有未成交候选里，这些因子是否同样有效”。如果后续基于本报告改策略，建议下一步让引擎落盘完整候选池，做 all-candidate universe 检验。

## 4. 品种-方向级结果：预算因子不能只看综合分

### 4.1 Rank IC：哪些因子能解释未来净收益

| 因子 | 方向 | mean IC | t-stat | 正 IC 占比 | 判断 |
|---|---|---:|---:|---:|---|
| `friction_ratio` | 低值好 | 0.2223 | 9.43 | 80.9% | 最强，但更像交易质量/硬过滤 |
| `b2_product_score` | 高值好 | 0.1555 | 6.07 | 66.4% | 适合预算，但不是唯一主因子 |
| `premium_to_iv10_loss` | 高值好 | 0.1528 | 6.68 | 73.3% | 适合预算，也适合选腿 |
| `premium_to_stress_loss` | 高值好 | 0.1521 | 6.86 | 75.6% | 适合预算，也适合红线 |
| `gamma_rent_penalty` | 低值好 | 0.1513 | 6.76 | 75.6% | 适合控制短 gamma 质量 |
| `theta_vega_efficiency` | 高值好 | 0.1405 | 5.36 | 66.4% | 有收益解释力，也控制止损 |
| `variance_carry` | 高值好 | 0.0736 | 2.62 | 61.7% | 预算因子，但不能单独依赖 |
| `premium_quality_score` | 高值好 | 0.0165 | 0.56 | 52.7% | 综合分被稀释 |

期权专家判断：B2c 的组合超额不是因为 `premium_quality_score` 这个综合分本身特别强，而是因为里面包含了一些强单因子。当前综合分把费用、IV shock 覆盖、stress 覆盖、gamma rent、theta/vega 等混在一起，经济含义太杂。下一版应该把它拆开，而不是继续调一个总分。

### 4.2 Good-minus-bad 分层：经济价值没有 IC 那么单调

品种-方向级 good-minus-bad 分层收益最高的是：

| 因子 | 累计 good-minus-bad | 日均 spread | t-stat | 正收益天数 |
|---|---:|---:|---:|---:|
| `variance_carry` | 17.64 | 0.1378 | 1.57 | 58.6% |
| `theta_vega_efficiency` | 11.57 | 0.0883 | 1.08 | 55.7% |
| `cost_liquidity_score` | 7.42 | 0.0567 | 0.55 | 48.9% |
| `b2_product_score` | 5.23 | 0.0399 | 0.50 | 55.0% |
| `gamma_rent_penalty` | 4.39 | 0.0335 | 0.34 | 52.7% |

这里有一个细节：`friction_ratio` 的 Rank IC 很强，但 good-minus-bad spread 不强。这不矛盾。IC 说明日内横截面排序方向稳定，spread 不强说明 Q5/Q1 两端的经济差距可能被仓位、样本权利金大小、止损路径和聚合方式稀释。它更像硬过滤/执行质量因子，而不是预算放大因子。

### 4.3 图表深读：品种-方向级累计 spread

![品种-方向级累计分层超额](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_product_side/01_factor_spread_cumulative.png)

怎么看：这张图看每个因子的 good-minus-bad 分层路径是否长期向上，而不是只看终点。

图上事实：`variance_carry` 和 `theta_vega_efficiency` 的累计路径相对靠前，`b2_product_score` 有正贡献但不算特别陡；综合 `premium_quality_score` 并没有形成明显领先路径。

期权专家判断：品种-方向预算层更应该使用少数纯净因子，而不是综合大杂烩。`variance_carry` 表示卖方是否拿到波动风险溢价，`theta_vega_efficiency` 表示每单位 short vega 能收多少 theta，这两个更符合“预算层”的经济含义。

策略含义：下一版可以设计 `B2_budget_slim`，把预算层改成更少、更纯的因子；合约级因子不要继续混入预算层。

### 4.4 图表深读：品种-方向级 IC 热力图

![品种-方向级 IC 热力图](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_product_side/05_factor_ic_heatmap.png)

怎么看：这张图同时检查每个因子对四个未来标签的解释力。真正好的卖权因子不应只解释收益，还应解释止损避免和止损损耗。

图上事实：`friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 对净收益标签的 IC 更强；`theta_vega_efficiency` 对 `stop_avoidance` 更突出。

期权专家判断：B2c 里的因子功能不同。费用和风险覆盖负责“这张保单能不能赚钱”，theta/vega 负责“这张保单会不会半路爆掉”。如果把二者合成一个预算分，会丢失规则含义。

策略含义：预算、选腿、止损概率要拆。不要让一个综合分同时承担所有任务。

### 4.5 图表深读：品种-方向级累计 IC

![品种-方向级累计 IC](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_product_side/06_cum_ic_net_pnl.png)

怎么看：这张图不是看 Q5-Q1 分层收益，而是看每天在 `product + option_type` 横截面上计算出来的 Rank IC 是否能稳定累积。它回答的是“这些因子能不能持续把更好的品种/方向排到前面”，而不是“某一个品种自身能不能择时”。如果要做单品种自己的时间序列 IC，那是另一个检验，不能和这里的横截面 IC 混用。

图上事实：品种-方向级累计 IC 里，`friction_ratio` 最强，`net_pnl_per_premium` 标签下累计 IC 为 `29.13`，mean IC `0.2223`，正 IC 占比 `80.9%`。第二梯队是 `b2_product_score`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty`，累计 IC 大约都在 `19.8-20.4` 附近；`theta_vega_efficiency` 累计 IC 为 `18.41`。相比之下，`variance_carry` 的累计 IC 为 `9.42`，虽然为正但明显弱一档；综合 `premium_quality_score` 只有 `2.16`，几乎没有形成稳定排序能力。

期权专家判断：这张图比 4.3 的累计 spread 更像“因子排序稳定性”的证据。`friction_ratio` 在品种-方向层面持续向上，说明交易摩擦不仅是合约级问题，也会影响品种/方向预算质量；但它的 good-minus-bad 经济 spread 不如 IC 强，说明它更适合做红线过滤或降权，而不是单独作为预算放大器。`b2_product_score`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 的累计 IC 也持续向上，说明它们确实能帮助我们在品种-方向之间排序。

策略含义：B2c 后续应该把“品种-方向级预算排序”和“合约级选腿排序”拆开。品种-方向层可以保留 `b2_product_score`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 等有累计 IC 的因子，但 `premium_quality_score` 这个总分本身不应作为唯一预算因子。`friction_ratio` 虽然累计 IC 最强，但更适合作为流动性/费用红线或强降权项。

## 5. 合约级结果：强信号主要在选腿和红线过滤

### 5.1 合约级 Rank IC 非常强

| 因子 | 方向 | mean IC | t-stat | 正 IC 占比 | 判断 |
|---|---|---:|---:|---:|---|
| `friction_ratio` | 低值好 | 0.2440 | 14.68 | 79.9% | 强硬筛选/执行质量因子 |
| `premium_to_iv10_loss` | 高值好 | 0.2261 | 12.42 | 79.3% | 强选腿因子 |
| `premium_to_stress_loss` | 高值好 | 0.2030 | 10.35 | 73.4% | 强选腿/红线因子 |
| `cost_liquidity_score` | 高值好 | 0.1982 | 10.38 | 73.4% | 执行可交易性因子 |
| `gamma_rent_penalty` | 低值好 | 0.1768 | 9.09 | 70.1% | 短 gamma 质量因子 |
| `premium_quality_score` | 高值好 | 0.0872 | 5.46 | 62.4% | 有效但弱于单因子 |
| `theta_vega_efficiency` | 高值好 | 0.0642 | 3.26 | 56.8% | 收益解释力一般，止损解释力更强 |

这是本报告最关键的证据。合约级强 IC 说明，B2c 里的许多因子更适合解决“卖哪张执行价”，而不是“哪个品种给更多预算”。

### 5.2 合约级止损预测

| 因子 | mean IC vs `stop_avoidance` | t-stat | 判断 |
|---|---:|---:|---|
| `theta_vega_efficiency` | 0.0955 | 4.06 | 最强止损概率因子 |
| `gamma_rent_penalty` | 0.0623 | 2.38 | 短 gamma 质量影响止损 |
| `premium_to_stress_loss` | 0.0582 | 2.27 | 压力覆盖能减少止损 |
| `premium_to_iv10_loss` | 0.0569 | 2.53 | IV shock 覆盖能减少止损 |
| `friction_ratio` | 0.0451 | 2.39 | 高摩擦也会影响止损质量 |

期权专家判断：中途止损不是一个纯价格路径事件，也和开仓时的权利金质量有关。theta/vega 效率低的合约，即使 Delta 低，也可能因为 theta 不够、vega 冲击覆盖不足而更容易触发 2.5x 止损。

策略含义：`theta_vega_efficiency` 更适合作为手数和铺单厚度控制，而不是只作为收益排序因子。

### 5.3 图表深读：合约级分层净收益热力图

![合约级净收益热力图](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_contract/02_layer_net_premium_heatmap.png)

怎么看：这张图看每个因子的 Q1-Q5 是否存在单调或准单调关系。对低值好的因子，应关注低分险层是否优于高风险层。

图上事实：`friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 在合约级有强 IC，但 Q1-Q5 的 pooled 净收益并非完美单调。比如 `premium_to_iv10_loss` 的 Q2/Q3/Q4 表现不差，Q5 未必最高。

期权专家判断：期权因子不同于股票因子。高分层可能权利金更厚、风险也更厚，因此分层收益不会总是线性单调。IC 强说明排序方向稳定，但策略落地时更适合“剔除明显差的一端”和“同组内优先排序”，而不是简单把最高层无限加仓。

策略含义：合约级强因子应用方式应是红线过滤和选腿排序，而不是让 Q5 合约单边获得极高预算。

### 5.4 图表深读：合约级累计 IC

![合约级累计 IC](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_contract/06_cum_ic_net_pnl.png)

怎么看：这张图看 IC 是否稳定累积。如果某因子累计 IC 长期向上，说明它不是某一天的偶然结果。

图上事实：合约级 `friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`cost_liquidity_score` 的累计 IC 显著向上，且远强于综合 `premium_quality_score`。

期权专家判断：单张合约层面，交易摩擦和冲击覆盖比“综合美观分”更重要。卖权本质是收保险费，如果费用、IV shock 或 stress loss 一开始就覆盖不了，那么这张保单不值得承保。

策略含义：B2c 下一版应优先做 `FEE/IVS/STRESS` 红线过滤和 `LEG` 选腿重排。

## 6. 止损概率专题

### 6.1 图表深读：止损率分层图

![合约级止损率分层图](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_contract/07_stop_rate_by_layer.png)

怎么看：这张图不看收益，而看每个因子分层对应的中途止损率。卖方策略里，止损概率本身就是核心标签。

图上事实：`theta_vega_efficiency` 在合约级的止损解释力最强，Q1 层 pooled stop rate 约 `37.60%`，Q5 层约 `29.47%`；`premium_to_iv10_loss`、`premium_to_stress_loss` 和 `gamma_rent_penalty` 也对止损有正向解释。

期权专家判断：止损不是完全由标的方向决定。开仓权利金对 vega、gamma、stress 的覆盖能力，会影响这笔交易能不能扛过中途波动。尤其是低 theta/vega 效率的合约，虽然可能看起来便宜、安全，但实际 theta 不够抵消 vega/gamma 冲击。

策略含义：下一版可以把 `theta_vega_efficiency` 用于三类规则：单合约手数上限、同品种同方向铺单数量、止损后重开条件。它不一定要作为预算加仓因子。

## 7. 环境切片：Put 侧更清楚，正常/高波环境更有效

### 7.1 Put/Call 差异

品种-方向级收益 IC 按方向看：

| 方向 | 最强因子 | mean IC | 说明 |
|---|---|---:|---|
| Call | `friction_ratio` | 0.1198 | 有效但较弱 |
| Call | `premium_quality_score` | 0.0960 | Call 侧综合分略有效 |
| Put | `friction_ratio` | 0.2346 | 明显更强 |
| Put | `premium_to_stress_loss` | 0.1539 | Put 侧 stress 覆盖有效 |
| Put | `variance_carry` | 0.1537 | Put 侧预算因子更强 |
| Put | `b2_product_score` | 0.1491 | Put 侧产品预算更有效 |

止损避免上，Put 侧 `b2_product_score` 的 IC 为 `0.1078`，`theta_vega_efficiency` 为 `0.0862`；Call 侧没有这么强的止损预测效果。

期权专家判断：B2c 的因子体系对 Put 更有效，可能因为 Put 侧权利金质量、stress loss、IV/RV carry 更接近卖方风险补偿逻辑；Call 侧更多受到商品趋势、跳涨、逼仓和 skew 变化影响，单纯权利金质量解释力弱一些。

策略含义：下一版不应该长期偏 Put，但因子参数可以区分 P/C。Put 侧可更重视 `variance_carry`、`b2_product_score`、`stress coverage`；Call 侧要更重视趋势、尾部跳涨、流动性和单边风险。

### 7.2 Vol regime 差异

合约级按 vol regime 看，`friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss` 在 normal、high_rising、low_stable 中都有效。

例如合约级：

| 环境 | 因子 | mean IC |
|---|---|---:|
| normal_vol | `premium_to_iv10_loss` | 0.2619 |
| normal_vol | `friction_ratio` | 0.2514 |
| normal_vol | `premium_to_stress_loss` | 0.2340 |
| high_rising_vol | `friction_ratio` | 0.2466 |
| high_rising_vol | `premium_to_iv10_loss` | 0.2066 |
| low_stable_vol | `premium_to_stress_loss` | 0.3027 |
| low_stable_vol | `premium_to_iv10_loss` | 0.2874 |

期权专家判断：这说明“费用 + IV shock 覆盖 + stress 覆盖”不是单一环境因子，而是较稳定的合约质量因子。它们可以成为更严肃的交易准入和选腿规则。

策略含义：这些因子适合先做红线过滤和选腿排序；环境层更多用于决定是否加大预算，而不是替代基础质量判断。

## 8. 因子用途重分类

### 8.1 硬筛选因子

建议优先考虑：

- `friction_ratio`
- 极低 `premium_to_iv10_loss`
- 极低 `premium_to_stress_loss`

理由：这些因子在合约级和品种-方向级都对未来净收益有显著解释力。尤其 `friction_ratio`，合约级 mean IC `0.2440`，品种-方向级 mean IC `0.2223`。它代表交易摩擦是否已经侵蚀权利金，低质量层不应该只是低预算，而应该直接不做。

落地方式：先做最低 20% 分层过滤，不改变总预算，观察毛权利金、留存率和止损是否改善。

### 8.2 选腿排序因子

建议优先考虑：

- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `gamma_rent_penalty`
- `friction_ratio`

理由：这些因子在合约级明显强于综合分，适合同品种同方向内部选执行价。尤其 `premium_to_iv10_loss` 和 `premium_to_stress_loss` 直接衡量权利金能覆盖多少 IV shock 和 stress loss，比单纯 Delta 接近目标更接近卖方承保逻辑。

落地方式：在同一 product + side + expiry 中，先用这些因子排序，再选择多个小 Delta 执行价。

### 8.3 品种/方向预算因子

建议保留：

- `variance_carry`
- `b2_product_score`
- `theta_vega_efficiency`

理由：这些因子在品种-方向层更有预算意义。`variance_carry` 的 good-minus-bad 累计最高，`b2_product_score` 对未来净收益和止损都有解释，`theta_vega_efficiency` 同时影响收益和止损。

但预算层应瘦身，不宜继续把所有合约级强因子混进综合分。

### 8.4 止损概率控制因子

建议重点使用：

- `theta_vega_efficiency`
- `gamma_rent_penalty`
- `premium_to_stress_loss`
- `premium_to_iv10_loss`

理由：这些因子对 `stop_avoidance` 有解释力。尤其 `theta_vega_efficiency`，合约级止损避免 IC 为 `0.0955`，明显高于它对收益的 IC。

落地方式：低 `theta_vega_efficiency` 分层不一定不做，但应该降低单合约手数、减少邻近执行价铺单，或者止损后重开要求该指标恢复。

### 8.5 环境调制因子

B2c 当前还没有充分使用 VOV，但从 B3b 的阶段性表现看，VOV 不应该简单低值好。未来应把 VOV 当环境调制因子：

```text
高 VOV + VOV 继续上升 = 风险；
高 VOV + VOV 稳定或回落 = 卖方机会；
低 VOV + 权利金薄 = 低波陷阱。
```

落地方式：等 B3 orders 落盘后，对 `b3_vol_of_vol_proxy` 和 `b3_vov_trend` 做同样分层报告，再决定反向或条件反向用法。

## 9. 图表索引与解读补充

### 9.1 品种-方向级分层收益热力图

![品种-方向级净收益热力图](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_product_side/02_layer_net_premium_heatmap.png)

怎么看：这张图检验预算层因子的 Q1-Q5 经济价值。

图上事实：`variance_carry` 的高分层更有优势，`premium_quality_score` 不强，`premium_to_iv_shock_score` 和 `premium_to_stress_loss_score` 在 product-side 层的分层表现并不理想。

期权专家判断：product-side 层不是所有合约级强因子的合适用法。IV shock 和 stress coverage 可能更适合选腿，而非直接决定整个品种方向预算。

策略含义：拆分功能层，而不是继续做总分预算。

### 9.2 合约级止损率热力图

![合约级止损率热力图](../output/factor_layers_s1_b2_product_tilt075_stop25_allprod_2022_latest_contract/04_layer_stop_rate_heatmap.png)

怎么看：这张图直接看哪些因子的分层会改变中途止损概率。

图上事实：`theta_vega_efficiency` 的低分层止损率更高，高分层止损率更低；`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty` 也有止损解释力。

期权专家判断：止损概率因子和收益因子不是一回事。一个因子可能收益 IC 很强，但止损 IC 一般；另一个因子可能主要用于降低爆单概率。

策略含义：止损概率因子应该进入手数和铺单控制。

## 10. 下一版实验建议

### 实验 A：红线过滤

在 B2c 基础上，只增加最差层过滤：

```text
friction_ratio 最差 20% 不做；
premium_to_iv10_loss 最差 20% 不做；
premium_to_stress_loss 最差 20% 不做。
```

目标：减少明显不值得承保的交易，而不是降低整体开仓积极性。

观察指标：毛权利金是否明显下降、留存率是否提高、止损次数是否下降、vega/gamma 吞噬率是否改善。

### 实验 B：选腿重排

不改变品种预算，只改变同品种同方向内合约排序：

```text
先按 premium_to_iv10_loss、premium_to_stress_loss、gamma_rent_penalty 排序；
再按原 B2c 规则开多个低 Delta 执行价。
```

目标：把合约级强 IC 转成真实策略收益。

### 实验 C：预算瘦身

把品种方向预算层改成更少的因子：

```text
variance_carry
b2_product_score
theta_vega_efficiency
```

同时把 `friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss` 移出预算层，改为过滤和选腿层。

目标：避免综合分稀释强因子。

### 实验 D：止损概率控制

低 `theta_vega_efficiency` 分层不直接禁做，但降低风险：

```text
单合约手数下调；
同品种同方向最多执行价数量下降；
止损后重开要求 theta_vega_efficiency 不在低分层。
```

目标：降低中途 2.5x 止损频率，而不是单纯追求更高毛权利金。

## 11. 结论

B2c 的方向是对的：它证明权利金质量因子能改善策略。但现在的证据也说明，B2c 不应该被理解为“一个综合质量分 + 一个预算倾斜强度”。更合理的理解是：

```text
B2c 是一组承保质量因子库。
其中一部分用于拒绝坏保单；
一部分用于选择执行价；
一部分用于分配品种方向预算；
一部分用于预测止损概率；
一部分需要和环境结合使用。
```

下一步真正的改进空间，不是继续把预算倾斜从 0.75 调到 1.0，而是把这些因子放到正确的策略层级里。
