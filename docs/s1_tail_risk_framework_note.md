# S1 尾部风险框架接入备忘

## 1. 背景

本备忘基于用户过往研究《尾部风险的度量及组合.pdf》，用于记录该尾部风险框架如何接入当前 S1 卖权策略研究。

S1 的核心仍然是：

- 卖权收权利金，获取 theta 与波动率风险溢价。
- 控制尾部亏损，不让少数极端日期吞掉长期权利金。
- 目标保持不变：年化收益接近或超过 6%，最大回撤尽量控制在 2% 内。
- 归因目标保持不变：vega 收益最好为正，不能主要依赖方向收益。
- 组合画像希望接近乐得：多品种、多执行价、P/C 随趋势适度偏移，并在确认降波时敢于提高预算。

## 2. PDF 框架的核心价值

该研究的主线不是普通 VaR，而是更适合尾部风险管理的一套组合框架：

- 用 ES/CVaR 衡量进入尾部之后的平均损失，而不是只看最大回撤或 VaR 阈值。
- 用 GPD/EVT 拟合极端损失，估计历史样本中没有充分出现、但未来可能发生的深尾部。
- 用 Clayton Copula 和下尾相依系数刻画资产在极端亏损时是否一起下跌。
- 用下尾相依距离做分层聚类，识别真正共享尾部风险来源的资产簇。
- 用尾部风险合成公式估计组合整体尾部风险，并据此做组合权重或风险预算。

这套框架对 S1 最有价值的地方是：它可以把“看上去分散”的品种池，重新按“极端亏损时是否一起亏”进行分组。

## 3. 对 S1 的适用判断

结论：可以用，而且应该用，但应定位为 S1 的尾部雷达和组合预算引擎，而不是单合约 alpha 因子。

不建议一开始直接用它替代开仓信号，原因是：

- 卖期权的亏损高度路径依赖，单纯基于历史日收益的尾部拟合无法覆盖跳空、IV spike、流动性枯竭和止损滑点。
- 商品、ETF、股指期权的尾部来源不同，同一个产品的 Put 和 Call 尾部方向也不同。
- GPD 和 Copula 对样本长度、尾部样本数量、阈值选择较敏感，如果直接进入交易规则，容易过拟合。

更合理的使用顺序是：

1. 先作为回测后诊断工具。
2. 再作为组合风险预算工具。
3. 最后才小心接入开仓与 sizing。

## 4. 对象定义需要适配卖权

PDF 中的对象主要是基金净值或资产收益率；S1 中不能直接照搬。

S1 应该按“产品-方向”的损失序列建模，例如：

```text
CU_P_loss
CU_C_loss
AU_P_loss
AU_C_loss
IF_P_loss
IF_C_loss
300ETF_P_loss
300ETF_C_loss
```

原因是：

- Short Put 的灾难通常来自标的下跌、IV 上升和 put skew steepen。
- Short Call 的灾难通常来自标的上涨、IV 上升和 call skew steepen。
- 同一个品种的 Put 与 Call 不应简单视为同一类风险。
- 不同商品板块在日常相关性低时，尾部仍可能共振。
- ETF/股指与商品卖权平时净值走势可能相似，但极端风险来源可能完全不同。

因此，尾部相依应主要基于策略损失序列，而不是只基于标的收益率。

## 5. 第一阶段：回测后尾部诊断

先不改交易逻辑，新增一个只读诊断脚本，对 B0、B1 以及后续版本输出做尾部拆解。

建议输出：

- 组合日度 PnL 的 VaR、ES、GPD-ES。
- 最差 1%、5% 日期清单。
- 最差日期上的持仓、保证金、P/C 结构、Greeks、止损和到期损益。
- 产品维度尾部贡献。
- 产品-方向维度尾部贡献。
- 板块维度尾部贡献。
- 最差日期中的 theta、vega、gamma、delta 和 residual 归因。
- 产品-方向之间的下尾相依矩阵。
- 基于下尾相依距离的聚类图。
- “平时相关性”和“尾部相依性”的对比热力图。

该阶段要回答的问题：

- S1 的最大亏损是否来自少数品种或少数方向？
- P/C 双卖是否在尾部真的分散，还是在 IV spike 时一起亏？
- 商品、股指、ETF 是否提供真正的尾部分散？
- 当前板块划分和相关组划分是否低估了尾部共振？
- B0 看似朴素但表现较好的原因，是不是来自更自然的尾部分散？

## 6. 第二阶段：组合风险预算接入

在 `portfolio_risk.py` 中，当前已有保证金、cash Greeks、stress loss、板块、相关组等预算控制。尾部风险框架可以在这一层接入，而不是直接写进选腿函数。

建议新增或预留以下预算概念：

```text
portfolio_es_cap
portfolio_gpd_es_cap
tail_cluster_margin_cap
tail_cluster_stress_cap
tail_cluster_es_cap
product_marginal_es_cap
product_side_tail_contribution_cap
```

核心思路：

- 不是所有低 delta 都一样安全。
- 不是所有分散到不同品种的仓位都真的分散。
- 如果多个品种在尾部亏损时高度共振，它们应该共享同一个 tail cluster budget。
- 如果某个产品-方向已经对组合 ES 贡献过高，即使保证金还没满，也不应继续加仓。
- 如果某个产品处于确认降波且尾部贡献低，可以给更高风险预算。

这可以和我们已有的 falling release 结合：

```text
确认降波 + forward vega 干净 + tail contribution 低：
    允许提高产品/方向/cluster 预算。

确认降波 + forward vega 干净 + tail cluster 已拥挤：
    不再继续加仓，或者只允许换到不同执行价/不同方向/不同板块。

低波稳定但 VCP 收缩 + tail compensation 低：
    谨慎做，不能因为低波就机械加仓。
```

## 7. 第三阶段：开仓与仓位接入

在确认第一、二阶段有效后，再把尾部指标接入开仓和 sizing。

建议不要做硬过滤的第一反应，而是作为风险预算和候选排序的调整项：

- 候选合约的 `theta / tail_stress` 越高越好。
- 候选合约的 `premium / tail_stress` 越高越好。
- 候选合约加入后组合 ES 增量越低越好。
- 候选合约加入后如果提高某个 tail cluster 的拥挤度，应降权。
- 同样满足 delta、流动性、费用条件时，优先选择能改善组合尾部分散的品种和方向。

潜在评分项：

```text
tail_adjusted_score
= base_score
+ theta_quality_score
+ forward_vega_score
- marginal_es_penalty
- tail_cluster_crowding_penalty
- product_side_tail_concentration_penalty
```

## 8. 与 B0/B1 的关系

B0 是朴素全品种卖权基准，B1 是在 B0 基础上增加流动性和持仓量排序。

尾部风险框架不应改变 B0 定义。它应该先用于解释 B0/B1：

- 为什么 B0 这么朴素，阶段性结果却可能比复杂规则更好？
- B0 的收益是否来自更高保证金使用率，还是来自更自然的多品种尾部分散？
- B1 的流动性排序是否会把仓位集中到少数热门品种，从而提高尾部相依？
- 如果 B1 提高可交易性但降低尾部分散，需要在后续版本加入 tail cluster cap。

因此，建议后续实验路径为：

```text
B0：标准朴素基准
B1：B0 + 流动性/OI 排序
T0：对 B0/B1 做尾部风险诊断，不改交易
T1：B1 + tail cluster 诊断报表
T2：B1 + tail cluster budget
T3：B1 + falling release + tail contribution sizing
```

## 9. 需要避免的误用

该框架不能被误用为：

- 单纯用历史 VaR 判断是否开仓。
- 用少量极端样本拟合 GPD 后直接决定大幅加仓。
- 只看标的收益相关性，而不看期权方向和 IV 变化。
- 只看日度收盘损失，而忽略盘中跳变、止损成交价、滑点和流动性枯竭。
- 只优化样本内 ES 最小，而不做样本外和 walk-forward 验证。

卖权策略真正的尾部经常来自：

- 标的跳空。
- IV 急升。
- skew steepen。
- 深虚值合约报价跳变。
- 止损成交远差于触发价。
- 多品种在同一宏观事件下同时进入亏损。

这些需要和现有分钟回测、异常报价过滤、日内止损预筛、真实费用和保证金模型共同使用。

## 10. 下一步建议

最优先落地的是 T0 尾部风险诊断脚本。

输入：

```text
nav_{tag}.csv
orders_{tag}.csv
diagnostics_{tag}.csv
analysis_{tag}/product_share_top10.csv
analysis_{tag}/daily_close_event_summary.csv
analysis_{tag}/worst_20_days.csv
```

输出：

```text
tail_risk_summary.csv
tail_worst_days.csv
tail_product_contribution.csv
tail_product_side_contribution.csv
tail_dependence_matrix.csv
tail_cluster_map.csv
tail_risk_report.md
tail_dependence_heatmap.png
tail_cluster_dendrogram.png
```

这一步完成后，我们再决定是否把尾部风险预算正式接入 `portfolio_risk.py`。

