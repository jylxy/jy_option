# S1 B6 品种层 IC 严谨性审计报告

生成日期：2026-04-29  
研究对象：`s1_b5_full_shadow_v1_2022_latest` 的 B6 品种层与品种-方向层 IC  
核心问题：当前 IC 是否足够严谨，能否作为 S1 品种预算倾斜与品种筛选的依据

---

## 1. 执行摘要

结论先说清楚：当前 B6 品种层 IC 是一套“方向正确、研究上可用”的横截面 Rank IC，但它还不是可以直接进入交易规则的“严格交易 IC”。它适合告诉我们“哪些品种层变量值得继续研究”，不适合直接告诉我们“从明天开始按这些因子大幅加仓或硬筛品种”。

从代码审计看，当前实现做对了几件关键事情：

1. IC 是按 `signal_date` 做日度横截面，而不是把所有日期混在一起做一个静态相关。
2. 使用的是 Spearman Rank IC，即对因子和标签分别排序后做相关，避免被极端值直接支配。
3. 因子方向已经调整成“越高越好”，低风险因子会先取反再参与排序。
4. 信号来自 T 日候选池聚合面板，标签来自未来 shadow outcome，不是直接用未来结果构造信号。
5. 品种层和品种-方向层分开检验，没有把 `product`、`product + option_type` 和合约层混在一起。

但它也有明显不足：

1. `premium_to_margin`、`premium_to_stress` 这类因子和 `future_pnl_per_margin`、`future_pnl_per_stress` 这类标签共享分母或经济结构，存在机械相关风险。
2. 当前 B6 脚本没有残差 IC，没有控制权利金、保证金、压力亏损、候选数量、平均 delta 和 DTE 之后再看增量解释力。
3. 当前 B6 脚本没有非重叠样本检验，也没有累计 IC 图，无法判断信号是否稳定累积，还是由高度重叠的持有周期贡献。
4. 当前 B6 标签是 all-candidate shadow outcome 聚合，不等同于真实组合在预算约束、持仓上限、止损成交和容量约束后的结果。
5. 当前 B6 还没有把 vega/gamma forward label、stop overshoot、低价 tick 扭曲、板块和波动环境分层纳入严格审计。

因此，本次审计给出的评级是：

| 用途 | 当前 IC 评级 | 是否可直接采用 |
| --- | --- | --- |
| 研究假设发现 | 通过 | 可以使用 |
| 因子族群排序和候选收敛 | 有条件通过 | 可以使用，但要标注污染风险 |
| B6 品种预算轻度倾斜 | 暂不通过 | 需要 corrected product IC 后再定 |
| 品种硬筛或黑名单 | 不通过 | 目前证据不足 |
| 大幅加仓或产品级风险预算迁移 | 不通过 | 必须先做残差、非重叠和样本外验证 |

一句话判断：当前 IC 可以作为“发现方向”的研究工具，但还不能作为“交易执行”的最终证据。

---

## 2. 检查对象与输出位置

本次审计基于以下本地输出：

```text
output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/
```

核心文件：

| 层级 | 主要文件 | 用途 |
| --- | --- | --- |
| 品种层 | `product/factor_ic_summary.csv` | `signal_date + product` 横截面 IC |
| 品种层 | `product/factor_layer_summary.csv` | 因子 Q1-Q5 分层结果 |
| 品种层 | `product/factor_spread_summary.csv` | Q5-Q1 扩散结果 |
| 品种层 | `product/factor_correlation_matrix.csv` | 因子相关性矩阵 |
| 品种-方向层 | `product_side/factor_ic_summary.csv` | `signal_date + product + option_type` 横截面 IC |
| 品种-方向层 | `product_side/factor_layer_summary.csv` | 品种-方向 Q1-Q5 分层 |
| 品种-方向层 | `product_side/factor_spread_summary.csv` | 品种-方向扩散 |
| 品种-方向层 | `product_side/factor_correlation_matrix.csv` | 品种-方向因子相关性 |

本次审计同时对照了两类脚本：

| 脚本 | 作用 | 审计结论 |
| --- | --- | --- |
| `scripts/analyze_b6_product_selection.py` | 当前 B6 品种/品种-方向 IC 生成脚本 | 基础 Rank IC 正确，但 corrected audit 不完整 |
| `scripts/analyze_candidate_universe_corrected.py` | 合约层和产品方向 corrected IC 审计脚本 | 更严格，包含样本过滤、残差 IC、非重叠、累计 IC 和用法映射 |

---

## 3. 当前 IC 的计算口径

当前 B6 的 IC 口径可以概括为：

```text
对每个交易日 t：
    取当天横截面内所有 product 或 product + side 样本
    对因子 f 做方向调整，使得越高越好
    对未来标签 y 做排序
    计算 corr(rank(f), rank(y))

最终 IC = 所有交易日 IC_t 的均值
```

代码层面，对应逻辑是：

```python
ic = group["factor"].rank().corr(group["label"].rank())
```

这意味着当前 IC 是日度横截面 Spearman Rank IC。这个口径在因子研究里是合理的，尤其适合我们当前的问题：每天在多个品种之间判断“哪个品种更值得给预算”。

当前标签主要包括：

| 标签 | 含义 | 对应公式项 |
| --- | --- | --- |
| `future_pnl_per_premium` | 未来净 PnL / 开仓权利金 | Retention Rate |
| `future_pnl_per_margin` | 未来净 PnL / 当前保证金 | Premium Pool、Deployment Ratio、Retention Rate |
| `future_pnl_per_stress` | 未来净 PnL / 压力亏损 | Tail / Stop Loss |
| `future_retained_ratio` | 未来留存权利金 / 开仓权利金 | Retention Rate |
| `future_stop_avoidance` | 1 - 止损率 | Tail / Stop Loss |
| `future_stop_loss_avoidance` | 止损损耗的反向指标 | Tail / Stop Loss |

对 S1 来说，这套标签方向是合理的。它没有试图预测标的涨跌，而是在预测“卖权承保质量”：能不能收得到、能不能留得住、会不会被止损和尾部吞掉。

---

## 4. 当前 IC 做得严谨的地方

### 4.1 横截面维度正确

品种层 IC 用的是 `signal_date + product`，品种-方向层用的是 `signal_date + product + option_type`。这比全样本混合相关更合理，因为 S1 每天真正需要做的是横向分配预算，而不是判断一个长期绝对数值是否高低。

横截面 IC 的好处是：

1. 自动消掉了当天全市场共同环境的影响。
2. 更贴近真实交易决策：今天到底多给哪个品种预算。
3. 避免把 2022 年和 2025 年的整体波动水平差异误当成因子能力。

### 4.2 Rank IC 比线性相关更适合当前阶段

当前因子有很多比值项，例如：

```text
premium / margin
premium / stress loss
theta / vega
stress loss / premium
```

这些变量分布非常偏，且商品期权之间量纲差异大。如果直接用 Pearson 相关，很容易被少数极端品种支配。Rank IC 至少先回答排序是否有效，这更适合品种筛选和预算倾斜。

### 4.3 因子方向已经统一

当前脚本里因子有 `high` 和 `low` 两类方向。比如：

```text
product_premium_to_margin: high
product_stress_per_premium: low
product_vega_per_premium: low
```

低风险因子会转换成“越高越好”的方向后再计算 IC。这一点很重要，否则不同因子之间的 IC 正负号会混乱。

### 4.4 不是只看收益，也看留存和止损

当前 B6 同时看：

```text
future_pnl_per_margin
future_retained_ratio
future_stop_avoidance
future_stop_loss_avoidance
```

这是卖权策略该有的视角。因为一个因子即使能提高未来 PnL，如果同时显著提高止损率或尾部损失，它也不一定应该进入交易规则。

---

## 5. 当前 IC 还不够严谨的地方

### 5.1 分母和经济结构污染

这是当前 B6 最大的问题。

以品种层为例：

```text
product_premium_to_margin = product_premium_sum / product_margin_sum
future_pnl_per_margin = outcome_net_pnl_sum / product_margin_sum
```

这两个变量共享 `product_margin_sum`。如果一个品种保证金分母天然较小，或者权利金和未来净 PnL 高度相关，那么 IC 可能部分来自机械结构，而不是纯粹的预测能力。

类似问题还存在于：

| 因子 | 标签 | 可能污染 |
| --- | --- | --- |
| `product_premium_to_margin` | `future_pnl_per_margin` | 共享 margin 分母 |
| `product_premium_to_stress` | `future_pnl_per_stress` | 共享 stress 分母 |
| `side_premium_to_margin` | `future_pnl_per_margin` | 共享 side margin 分母 |
| `side_premium_to_stress` | `future_pnl_per_stress` | 共享 side stress 分母 |
| `premium_sum` | `future_pnl_per_premium` | 权利金既是收益来源，也是标签分母 |

这不代表这些因子无效，但意味着它们不能直接解释为“alpha 很强”。更准确的说法是：它们可能捕捉到了资本效率、权利金厚度和尾部覆盖能力，也可能部分捕捉到了分母效应。

### 5.2 缺少残差 IC

严格的品种层 IC 应该回答：

```text
控制权利金、保证金、压力亏损、候选数量、平均 delta、DTE 之后，
这个因子是否仍然有增量解释力？
```

当前 B6 脚本还没有做这一步。相比之下，`analyze_candidate_universe_corrected.py` 已经在合约层里做了更严格的残差 IC，包括：

```text
log_entry_price
log_open_premium_cash
dte
abs_delta
log_margin_estimate
log_stress_loss
```

B6 品种层也需要类似协议。否则我们无法区分：

1. 因子真的能识别好品种。
2. 因子只是识别了权利金厚的品种。
3. 因子只是识别了更接近 0.1 delta 的品种。
4. 因子只是识别了候选数量更多、可开仓权利金池更大的品种。

### 5.3 缺少非重叠样本检验

full shadow 的标签通常有持有期重叠。同一个品种在连续日期上的未来标签高度相关，如果直接逐日平均 IC，t-stat 可能会偏高。

严格检验至少要补：

```text
every_5th_signal_date
first_signal_per_product_month
first_signal_per_contract_or_product_side
block bootstrap by product-month
```

当前 B6 还没有这些结果，所以现在看到的 t-stat 不能直接理解为真实独立样本显著性。

### 5.4 缺少累计 IC 图

一个好因子不只要平均 IC 高，还要看累计 IC 是否稳定向上。如果累计 IC 只在某几个月暴涨，大部分时间横盘，那么它可能只是某段行情的巧合。

当前 B6 输出有 IC 热力图和 Q5-Q1 扩散图，但没有累计 IC。对后续是否进入 B6/B7 交易实验来说，这是一个明显缺口。

### 5.5 标签不是实际交易组合标签

当前品种层标签来自 all-candidate shadow outcome 聚合。它更像是在问：

```text
如果当天这个品种所有符合候选条件的合约都按一单位 shadow 卖出，
它之后的平均承保质量如何？
```

但真实策略不会无差别买入所有候选。真实策略还会经过：

```text
保证金预算
品种上限
方向上限
合约数量上限
流动性排序
止损成交
持仓滚动
组合尾部约束
```

因此当前标签适合做候选池研究，不适合直接等同于真实 NAV 超额。

### 5.6 对 vega/gamma 的最终解释还不够

我们现在的核心目标之一是：卖权应该赚 theta 和 vega，尤其 vega 收益要改善。B6 当前标签还没有完整引入：

```text
future_vega_pnl_per_premium
future_gamma_pnl_per_premium
vega_loss_to_premium
gamma_loss_to_premium
iv_shock_coverage
stop_overshoot
```

所以当前 IC 可以解释“资本效率”和“止损率”，但还不能完整解释“为什么 vega 最终赚或亏”。

---

## 6. 当前结果的关键事实

### 6.1 品种层：`future_pnl_per_margin` 的 Top 因子

| 因子 | IC | t-stat | 正 IC 比例 | 交易日 |
| --- | ---: | ---: | ---: | ---: |
| `product_premium_to_margin` | 0.253 | 31.09 | 81.93% | 1096 |
| `product_gamma_per_premium_low` | 0.218 | 30.20 | 81.75% | 1096 |
| `product_premium_to_stress` | 0.213 | 30.83 | 82.76% | 1096 |
| `product_stress_per_premium_low` | 0.213 | 30.83 | 82.76% | 1096 |
| `product_vega_per_premium_low` | 0.195 | 29.82 | 81.93% | 1096 |
| `product_theta_per_gamma` | 0.172 | 26.71 | 81.93% | 1096 |

这说明从产品层看，真正强的不是“传统 IV/RV carry”本身，而是：

```text
每单位保证金收到多少权利金
每单位压力亏损覆盖多少权利金
每单位权利金承担多少 gamma / vega
```

这和我们最近的研究范式是一致的。S1 的核心不是预测涨跌，而是寻找高质量权利金。

但这里必须谨慎：`product_premium_to_margin` 与 `future_pnl_per_margin` 存在分母相似性，不能直接把 0.253 解释为纯 alpha。

### 6.2 品种-方向层：`future_pnl_per_margin` 的 Top 因子

| 因子 | IC | t-stat | 正 IC 比例 | 交易日 |
| --- | ---: | ---: | ---: | ---: |
| `side_premium_to_margin` | 0.340 | 49.12 | 91.95% | 1205 |
| `side_avg_abs_delta` | 0.279 | 43.64 | 89.21% | 1205 |
| `side_gamma_per_premium_low` | 0.277 | 47.00 | 90.71% | 1205 |
| `side_premium_to_stress` | 0.270 | 46.93 | 90.46% | 1205 |
| `side_stress_per_premium_low` | 0.270 | 46.93 | 90.46% | 1205 |
| `side_vega_per_premium_low` | 0.252 | 44.88 | 90.12% | 1205 |
| `side_theta_per_gamma` | 0.205 | 37.50 | 87.14% | 1205 |

品种-方向层更强，说明一个品种内 Put/Call 的权利金质量差异非常重要。这个结果支持我们后续把“选品种”和“选 P/C 侧”分开，而不是简单给整个 product 一个总预算。

但 `side_avg_abs_delta` 排名很靠前，要小心解释。它可能说明“过深虚的合约权利金太薄，承保质量差”，而不是允许我们突破 `delta < 0.1` 的硬约束。更合理的使用方式是：在小于 0.1 的范围内，避免过度偏向极深虚的低价合约。

### 6.3 止损回避结果也支持“权利金质量”逻辑

品种层 `future_stop_avoidance` 的强因子包括：

| 因子 | IC | t-stat | 正 IC 比例 |
| --- | ---: | ---: | ---: |
| `product_premium_to_margin` | 0.160 | 24.90 | 78.45% |
| `product_vega_per_premium_low` | 0.142 | 24.50 | 78.36% |
| `product_premium_to_stress` | 0.137 | 22.85 | 76.16% |
| `product_theta_per_gamma` | 0.131 | 22.12 | 77.08% |

品种-方向层 `future_stop_avoidance` 的强因子包括：

| 因子 | IC | t-stat | 正 IC 比例 |
| --- | ---: | ---: | ---: |
| `side_avg_abs_delta` | 0.193 | 32.52 | 84.63% |
| `side_premium_to_margin` | 0.146 | 25.57 | 79.07% |
| `side_vega_per_premium_low` | 0.130 | 25.42 | 78.82% |
| `side_premium_to_stress` | 0.123 | 23.95 | 76.41% |

这点很重要：强因子不仅提高 `pnl_per_margin`，也改善止损回避。这比只提高 PnL 更可信，因为它说明这些因子可能不是单纯拿更多风险换收益，而是在改善权利金留存质量。

但是否真的降低尾部，还要补 `worst bucket`、`stop overshoot`、`vega/gamma loss` 和极端月份分层。

---

## 7. 图表审计与解读

### 7.1 品种层 IC 热力图

![品种层 IC 热力图](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/01_factor_ic_heatmap.png)

这张图验证的是：在 `signal_date + product` 层面，不同品种层因子对未来收益、留存、止损是否有横截面排序能力。

图上最清楚的事实是：`product_premium_to_margin`、`product_premium_to_stress`、`product_gamma_per_premium_low`、`product_vega_per_premium_low` 在 `future_pnl_per_margin` 上表现最好，且对 `future_stop_avoidance` 也有正贡献。这说明品种层真正重要的是“每单位风险拿到的权利金是否够厚”，而不是单纯的成交量、候选数量或传统波动环境标签。

期权专家视角下，这符合卖权逻辑。卖方的目标不是买到最便宜的风险，而是收取足够保险费并确保这份保险费能覆盖正常波动、IV 冲击和止损损耗。图里强的几个因子本质上都在衡量：

```text
Premium Pool 是否足够厚
Retention Rate 是否有基础
Tail / Stop Loss 是否被权利金覆盖
```

策略含义是：这些因子值得进入 B6 品种预算研究，但当前阶段只能作为预算倾斜候选，不能直接作为硬过滤。原因是它们和标签存在分母结构重叠，需要 corrected product IC 再确认。

### 7.2 品种-方向层 IC 热力图

![品种-方向层 IC 热力图](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/01_factor_ic_heatmap.png)

这张图看的是 `product + Put/Call` 这一层。它比纯品种层更接近实际交易，因为同一个品种的 Put 侧和 Call 侧在不同趋势、skew 和产业环境下，承保质量可能完全不同。

图上最重要的现象是：品种-方向层 IC 普遍比品种层更强，尤其 `side_premium_to_margin` 的 `future_pnl_per_margin` IC 达到 0.340。这说明 S1 后续不能只问“哪个品种好”，还要问“这个品种今天是 Put 侧好还是 Call 侧好”。

这也解释了为什么我们之前长期讨论 P/C 偏移。长期机械偏 Put 或偏 Call 都不严谨，因为那会变成方向性押注；但如果在 `product + side` 层面识别哪一侧权利金质量更高、尾部更可控，那么 P/C 偏移就不再是主观判断，而是承保质量分配。

策略含义是：B6 不应只设计 product budget tilt，还要设计 product-side budget tilt。比如同一个品种可以保留总预算中性，但根据 side score 动态分给 Put 或 Call。

### 7.3 品种层 Q5-Q1 收益扩散

![品种层 Q5-Q1 PnL/Margin 扩散](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/02_spread_pnl_per_margin.png)

这张图看的是每个因子最高分组 Q5 与最低分组 Q1 在 `future_pnl_per_margin` 上的差异。它比 IC 更直观，因为交易规则最终往往是“多给高分组预算，少给低分组预算”。

如果某个因子 IC 高，但 Q5-Q1 扩散很小，说明它排序稳定但经济意义弱。当前图中，权利金效率、压力覆盖、低 vega/gamma 单位负担这几个因子同时具备较好的 IC 和扩散，这使它们比单纯趋势、候选数量类因子更值得关注。

不过这里依然要谨慎。Q5-Q1 扩散同样可能被分母影响放大。因此后续需要补一张 corrected spread：在控制 `premium_sum`、`margin_sum`、`stress_sum`、`candidate_count`、`avg_abs_delta` 后再看 Q5-Q1。

### 7.4 品种-方向层 Q5-Q1 收益扩散

![品种-方向层 Q5-Q1 PnL/Margin 扩散](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/02_spread_pnl_per_margin.png)

品种-方向层的扩散更清楚。这说明很多超额可能不是来自“选对品种”，而是来自“同一品种选对方向侧”。

对卖权来说，这一点非常有交易含义：

```text
上涨趋势中，Put 侧可能更安全，但 Call 侧未必不能卖，只是预算要更小、delta 要更远。
下跌趋势中，Call 侧可能更安全，但 Put 侧如果 skew 足够厚且 tail coverage 足够高，也未必完全不能做。
震荡环境下，双卖更合理，但两侧预算不应该机械相等。
```

所以 B6/B7 的方向不是“长期偏 Put”或“长期偏 Call”，而是用 product-side 因子决定 P/C 侧预算。

### 7.5 品种层因子相关性矩阵

![品种层因子相关性矩阵](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product/05_factor_correlation_matrix.png)

相关性矩阵说明，当前很多强因子不是彼此独立的。最高相关关系包括：

| 因子对 | 相关系数 | 含义 |
| --- | ---: | --- |
| `product_premium_to_stress` vs `product_stress_per_premium_low` | 1.000 | 完全互为倒数方向，不能同时当独立因子 |
| `product_stress_per_premium_low` vs `product_gamma_per_premium_low` | 0.974 | 压力亏损和 gamma 负担高度同源 |
| `product_premium_to_stress` vs `product_vega_per_premium_low` | 0.963 | 权利金覆盖压力亏损和 vega 负担高度相关 |
| `product_theta_per_gamma` vs `product_vega_per_premium_low` | 0.969 | theta/gamma 和 vega/premium 可能同属风险覆盖族 |
| `product_stress_share_low` vs `product_margin_share_low` | 0.933 | 保证金占比和压力占比在品种层高度同步 |

这说明 B6 不能把这些因子简单相加。更好的做法是按因子族群选代表变量：

| 因子族 | 建议保留代表 | 暂不重复使用 |
| --- | --- | --- |
| 权利金/资本效率 | `product_premium_to_margin` | `product_premium_sum` 只做规模诊断 |
| 压力覆盖 | `product_premium_to_stress` | `product_stress_per_premium_low` |
| Vega 负担 | `product_vega_per_premium_low` | 与压力覆盖高共线，需残差后决定 |
| Gamma 负担 | `product_gamma_per_premium_low` 或 `product_theta_per_gamma` | 不和压力覆盖同时重权 |
| 集中度 | `product_margin_share_low` | `product_stress_share_low` 作为辅助诊断 |

### 7.6 品种-方向层因子相关性矩阵

![品种-方向层因子相关性矩阵](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/product_side/05_factor_correlation_matrix.png)

品种-方向层同样存在高度共线。最典型的是：

| 因子对 | 相关系数 | 含义 |
| --- | ---: | --- |
| `side_premium_to_stress` vs `side_stress_per_premium_low` | 1.000 | 同一信息的两个方向 |
| `side_stress_per_premium_low` vs `side_gamma_per_premium_low` | 0.977 | stress 与 gamma 负担几乎同源 |
| `side_trend_alignment` vs `side_momentum_alignment` | 0.965 | 趋势和动量类信号不能重复加权 |
| `side_theta_per_gamma` vs `side_vega_per_premium_low` | 0.954 | theta/gamma 与 vega/premium 有明显同源性 |

策略含义是：B6 如果要做 P/C 侧预算，不应该把所有强因子堆成一个 score。它应该采用“族群代表 + 残差检验”的方式：

```text
资本效率族：side_premium_to_margin
压力覆盖族：side_premium_to_stress
方向环境族：side_trend_alignment 或 side_momentum_alignment 二选一
Vega/Gamma 质量族：side_vega_per_premium_low 或 side_theta_per_gamma 二选一
```

### 7.7 品种权利金压力覆盖散点

![品种权利金压力覆盖散点](output/b6_product_selection_s1_b5_full_shadow_v1_2022_latest/06_product_premium_stress_scatter.png)

这张图从直观上解释了为什么品种筛选重要。横轴是 `product_premium_to_stress`，纵轴是未来 `future_pnl_per_margin`。如果右侧区域整体更高，说明“每单位压力亏损收到更多权利金”的品种，后续资本效率更好。

这张图的价值不是证明单个因子已经可交易，而是把我们的公式具体化：

```text
product_expected_pnl
= product_premium_pool
× product_deployment_ratio
× product_retention_rate
- product_stop_tail_loss
- product_cost_slippage
```

`premium_to_stress` 同时连接了 `Premium Pool` 和 `Tail / Stop Loss`。它不是普通收益因子，而是承保质量因子。

---

## 8. 严谨性评分表

| 审计项 | 当前状态 | 评级 | 说明 |
| --- | --- | --- | --- |
| 日度横截面 Rank IC | 已实现 | 通过 | 按 `signal_date` 分组，Rank IC 口径正确 |
| 因子方向统一 | 已实现 | 通过 | `high/low` 已调整为越高越好 |
| 品种层与品种-方向层分离 | 已实现 | 通过 | 没有混用 product、side、contract |
| 未来标签与当前信号分离 | 基本实现 | 通过 | shadow outcome 是未来标签，不直接参与信号构造 |
| 多标签检验 | 已实现 | 通过 | 收益、留存、止损都有 |
| 因子相关性矩阵 | 已实现 | 通过 | 但还需要族群化决策 |
| Q1-Q5 分层 | 已实现 | 通过 | 可看经济扩散 |
| 分母污染审计 | 部分识别 | 未通过 | 报告层面识别，脚本未控制 |
| 残差 IC | 未实现 | 未通过 | 需要控制 premium、margin、stress、delta、DTE |
| 非重叠 IC | 未实现 | 未通过 | t-stat 可能偏乐观 |
| 累计 IC | 未实现 | 未通过 | 无法看稳定性路径 |
| 样本过滤 | 未完整实现 | 未通过 | 缺少 completed、premium、low-fee 过滤 |
| vega/gamma forward label | 未完整实现 | 未通过 | 无法完整评价 vega 目标 |
| stop overshoot label | 未完整实现 | 未通过 | 无法评价止损成交跳价风险 |
| 样本外验证 | 未实现 | 未通过 | 无法排除阶段性过拟合 |

---

## 9. 对当前强 IC 的重新解释

### 9.1 `premium_to_margin` 不是简单 alpha，而是资本效率因子

`product_premium_to_margin` 和 `side_premium_to_margin` 的 IC 很高，但它们本质上更像资本效率指标：

```text
单位保证金能收多少权利金
```

它们改善的是：

```text
Premium Pool
Deployment Ratio
Retention Rate 的基础条件
```

但因为标签里也有 `per_margin`，所以它们必须做残差检验后才能决定是否用于品种预算倾斜。

### 9.2 `premium_to_stress` 更像承保质量因子

`premium_to_stress` 的经济含义更干净：

```text
收的保险费 / 压力情景下可能亏的钱
```

这个因子和我们的卖权逻辑高度一致。如果 corrected IC 后仍然有效，它会是 B6 品种预算最值得保留的核心变量之一。

### 9.3 `vega_per_premium_low` 是我们当前最关心的方向之一

我们的目标之一是 vega 收益为正。`vega_per_premium_low` 不是直接预测 vega PnL，但它在结构上降低了每单位权利金承受的 vega 风险。

如果后续加入 `future_vega_pnl_per_premium` 后，它仍然有效，那么它可能进入：

```text
品种预算倾斜
品种-方向预算倾斜
合约排序惩罚
止损后重开冷却
```

### 9.4 `side_avg_abs_delta` 不能被误读为可以卖大 delta

`side_avg_abs_delta` 强，更多说明当前候选池里过深虚合约的权利金质量不足。它不能推翻 `delta < 0.1` 的硬约束。

更合理的交易解释是：

```text
在 delta < 0.1 内，不要无脑买最远、最便宜、最深虚的尾部；
应在 0.05 到 0.10 或 0.06 到 0.10 内做梯队选择，
并用权利金质量和流动性决定具体铺开厚度。
```

### 9.5 尾部相关性因子不能只用普通 IC 判断

如果 tail dependence 类因子在平均 IC 上不强，不代表它没用。尾部相关性本来就是解释极端回撤和 stop cluster 的变量，不一定每天都提高平均收益。

这类因子更适合用：

```text
worst 1% days
stop cluster days
same-sector loss
same-expiry gamma loss
portfolio stress loss
```

来评价，而不是只看 `future_pnl_per_margin` 的平均 Rank IC。

---

## 10. 更严格的 B6 corrected product IC 协议

为了把 B6 从“研究假设”升级为“可交易证据”，下一版脚本应补充以下协议。

### 10.1 标签体系

品种层和品种-方向层至少应保留以下标签：

| 标签 | 用途 |
| --- | --- |
| `cash_pnl` | 避免所有比值标签的分母污染 |
| `pnl_per_premium_raw` | 粗看权利金留存 |
| `pnl_per_premium_clip` | 降低极端低价合约扭曲 |
| `pnl_per_margin` | 资本效率 |
| `pnl_per_stress` | 压力收益效率 |
| `retained_ratio` | 权利金留存率 |
| `stop_avoidance` | 止损回避 |
| `stop_loss_avoidance` | 止损损耗控制 |
| `stop_overshoot_avoidance` | 止损跳价风险 |
| `vega_pnl_per_premium` | vega 是否赚到 |
| `gamma_pnl_per_premium` | gamma 是否吞噬 theta |

### 10.2 样本过滤

至少拆成以下样本：

| 样本 | 目的 |
| --- | --- |
| `all` | 看完整候选池 |
| `completed_only` | 去掉未完成或标签不完整样本 |
| `premium_ge_100` | 排除极小权利金导致的比值噪声 |
| `completed_premium_ge_100` | 主研究样本 |
| `completed_premium_ge_100_low_fee` | 检查手续费和低价合约污染 |
| `active_product_days_ge_60` | 排除上市时间太短或样本太稀疏品种 |

### 10.3 残差控制变量

品种层至少控制：

```text
log(product_premium_sum)
log(product_margin_sum)
log(product_stress_sum)
product_candidate_count
product_side_count
avg_abs_delta
avg_dte
sector / board
year or vol regime
```

品种-方向层至少控制：

```text
log(side_premium_sum)
log(side_margin_sum)
log(side_stress_sum)
side_candidate_count
side_avg_abs_delta
side_avg_dte
option_type
product fixed effect
year or vol regime
```

残差 IC 至少分两类：

```text
factor_resid_vs_raw_label
factor_and_label_resid
```

只有在 `factor_and_label_resid` 下仍然有效的因子，才可以被视作有增量解释力。

### 10.4 非重叠检验

应输出：

```text
every_5th_signal_date
first_signal_per_product_month
first_signal_per_product_side_month
block_by_product_month
```

如果某个因子只在逐日重叠样本有效，在非重叠样本明显衰减，它更适合诊断，不适合进交易主规则。

### 10.5 累计 IC

必须画至少三类累计 IC：

```text
cumulative IC: pnl_per_margin
cumulative IC: stop_avoidance
cumulative IC: vega_pnl_per_premium
```

如果 `pnl_per_margin` 累计向上，但 `vega_pnl_per_premium` 累计向下，该因子可能只是提高权利金池，同时放大 short vega 暴露。

### 10.6 样本外验证

建议用：

```text
Train: 2022-2024
Validation: 2025
Test / live-like: 2026 有数据部分
```

或者滚动年度 walk-forward：

```text
用过去 12-24 个月确定因子方向和权重
只在未来 3-6 个月评估
```

S1 的因子不能只在 2025 年有效。否则很容易变成某段商品行情的过拟合。

---

## 11. 因子使用建议

基于当前证据，只能给出“候选用途”，不能直接给最终交易规则。

| 因子 | 因子族 | 层级 | 改善公式变量 | 当前建议 | 原因 |
| --- | --- | --- | --- | --- | --- |
| `product_premium_to_margin` | premium/capital | 品种层 | Premium Pool、Deployment Ratio | 暂作预算候选，不硬用 | IC 强，但和标签分母相关 |
| `product_premium_to_stress` | tail/premium | 品种层 | Retention Rate、Tail / Stop Loss | 优先进入 corrected IC | 经济含义最贴近承保质量 |
| `product_vega_per_premium_low` | vega | 品种层 | Tail / Stop Loss、Retention Rate | 优先补 vega label 验证 | 与 vega 正收益目标直接相关 |
| `product_gamma_per_premium_low` | gamma | 品种层 | Tail / Stop Loss | 作为 gamma 风险惩罚候选 | 与 stress 高共线，需二选一 |
| `product_theta_per_gamma` | gamma/theta | 品种层 | Retention Rate、Tail / Stop Loss | 作为辅助候选 | 可能和 vega/stress 高共线 |
| `side_premium_to_margin` | premium/capital | P/C 侧 | Premium Pool、Deployment Ratio | P/C 预算候选 | 强但需残差控制 |
| `side_premium_to_stress` | tail/premium | P/C 侧 | Retention Rate、Tail / Stop Loss | P/C 预算优先候选 | 比单纯 premium 更稳健 |
| `side_avg_abs_delta` | delta/ladder | P/C 侧 | Premium Pool、Retention Rate | 只用于 delta 梯队设计 | 不能突破 delta < 0.1 |
| `side_vega_per_premium_low` | vega | P/C 侧 | Tail / Stop Loss | vega 风险预算候选 | 与目标最相关，但需 forward vega 标签 |
| `side_trend_alignment` | trend | P/C 侧 | Tail / Stop Loss | 方向侧预算条件因子 | 与 momentum 高共线，二选一 |
| tail dependence | tail/portfolio | 组合层 | Tail / Stop Loss | 不用普通 IC 定优劣 | 应进 Tail-HRP 和 cluster risk |

---

## 12. 对 B6 的明确研究判断

### 12.1 当前结果说明了什么

当前结果说明：

```text
品种筛选确实重要。
品种-方向筛选比纯品种筛选更重要。
高质量权利金不是“权利金越多越好”，而是要看 premium / margin、premium / stress、premium / vega、premium / gamma。
```

这与我们对 S1 的核心公式一致：

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

品种层因子主要改善 `Premium Pool` 和 `Deployment Ratio`；品种-方向层因子同时改善 `Retention Rate` 和 `Tail / Stop Loss`。

### 12.2 当前结果不能说明什么

当前结果不能直接说明：

```text
某个品种从此应该被永久加仓。
某个品种应该被永久剔除。
premium_to_margin 可以直接作为预算权重。
B6 一定会在真实 NAV 上超越 B1/B2C。
这些因子一定能改善 vega PnL。
```

原因是当前 IC 还没有通过 corrected audit，也没有通过真实组合约束下的回测。

### 12.3 最稳妥的下一步

下一步应该做三件事：

1. 升级 B6 product IC 脚本，加入残差 IC、非重叠 IC、累计 IC、样本过滤和 vega/gamma 标签。
2. 只选择共线因子族中的代表变量，避免把同一个经济含义重复计分。
3. 在 corrected IC 通过后，先做轻度预算倾斜实验，不做硬筛，不做大幅加仓。

建议 B6 的第一版交易实验应遵守：

```text
不减少可交易品种池。
不设置永久黑名单。
只在品种和品种-方向预算上做温和倾斜。
每个品种仍保留最低探索预算，避免因短期 IC 误杀。
```

---

## 13. 最终结论

本次审计的最终结论是：

```text
当前 IC 是合格的研究型 Rank IC，
但不是完整的严格交易型 IC。
```

它足以支持我们继续做 B6 品种筛选和品种-方向预算研究，因为信号强度、经济解释和止损回避表现都不是随机噪声级别。尤其是 `premium_to_margin`、`premium_to_stress`、`vega_per_premium_low`、`gamma_per_premium_low` 这一组因子，和 S1 的卖权收益拆解高度吻合。

但它还不足以支持直接大幅改变交易规则。当前最关键的风险不是 IC 算错，而是 IC 可能“太漂亮”：其中一部分漂亮来自权利金、保证金、压力亏损和未来标签之间的共同结构。我们需要用 corrected product IC 把这部分污染剥掉，再决定哪些因子真正进入 B6。

给后续研究的一句话标准：

```text
只有同时通过横截面 Rank IC、残差 IC、非重叠 IC、累计 IC、样本外验证，
并且能在 Premium Pool、Retention Rate、Tail / Stop Loss、Cost / Slippage 中明确改善至少一项且不恶化其他项的因子，
才应该进入 S1 的正式交易规则。
```

