# S1 B6 Tail-HRP 组合优化设计备忘录

文档日期：2026-04-29  
策略上下文：S1 纯卖权 / short volatility / short premium  
上游研究：B1/B2/B3/B4/B5 full shadow  
本文定位：记录“用尾部相关性 + HRP 管理卖权组合聚合风险”的研究想法，作为后续 B6 实验设计基础。

## 1. 核心想法

卖权策略天然有正 carry，但真正危险的不是日常波动，而是尾部风险聚合。

平时各品种、各板块看起来相关性不高，权利金也在持续流入；但在宏观冲击、产业链共振、升波、跳空、流动性消失或保证金抬升时，多条卖权仓位可能同时亏损、同时止损、同时占用更多保证金。这种尾部同步风险无法靠普通收益率相关性充分刻画。

因此 B6 的方向不是继续单纯提高权利金厚度，而是：

```text
在 B2/B4/B5 已经完成候选质量排序之后，
用尾部相关性和 HRP 给品种、方向、板块、delta 梯队分配 risk budget，
避免多个看似独立的 short premium 仓位在同一个尾部场景里一起爆。
```

一句话：

```text
B5 解决“哪些权利金质量更好”；
B6 解决“这些好权利金不能同时挤在同一个尾部风险簇里”。
```

## 2. 为什么普通相关性不够

普通 Pearson 相关性通常衡量的是全样本线性共动，但卖权组合最关心的是：

| 普通组合问题 | 卖权组合真正问题 |
| --- | --- |
| 日收益是否相关 | 最差 5% 日是否一起亏 |
| 波动率是否相近 | IV spike 时是否同时亏 |
| 均值方差是否最优 | stress loss、cash gamma、cash vega 是否同时集中 |
| 品种是否分散 | 止损是否 cluster，保证金是否同时上升 |
| 正常环境是否平滑 | 危机环境是否出现跨品种同步尾损 |

所以 B6 不能直接用标的日收益相关性做 HRP。更合理的是构建“卖方风险相关性矩阵”。

## 3. Tail-HRP 在 S1 中的位置

B6 只放在组合预算层，不改变 S1 的基本交易哲学。

```text
候选生成
  -> B1 流动性/持仓量基础排序
  -> B2/B4/B5 合约质量评分
  -> 硬约束过滤：delta < 0.1、费用、低价、异常价、冷静期
  -> Tail-HRP 分配 product / side / bucket 风险预算
  -> stress / margin / cash Greeks 统一裁剪
  -> 开仓
```

它不应该替代：

- 合约层权利金质量判断；
- IV/RV carry 判断；
- forward vega / vol-of-vol / gamma rent 判断；
- 流动性和低价合约过滤；
- 止损、冷静期、异常价确认。

Tail-HRP 的角色是组合层的“防尾部聚合器”。

## 4. 风险矩阵如何构造

B6 的关键不在 HRP 算法本身，而在给 HRP 喂什么矩阵。

### 4.1 第一优先：shadow PnL 尾部相关性

用 B5 full shadow 的候选结果构造 product-side 层的未来收益序列：

```text
R_{product, side, t}
= 当日该 product-side 候选按固定规则卖出后的 forward pnl / stress_loss
```

然后只在尾部样本中计算相关性：

```text
tail_corr_ij
= Corr(R_i, R_j | market_shadow_pnl 位于最差 5% 或 10%)
```

这比标的收益率相关性更贴近实际卖权损益。

### 4.2 第二优先：止损聚集相关性

构造同日止损或相邻窗口止损的条件概率：

```text
stop_cluster_ij = P(i 触发止损 | j 触发止损)
```

或对称化：

```text
stop_cluster_corr_ij
= 0.5 * [P(i stop | j stop) + P(j stop | i stop)]
```

这直接服务我们最关心的问题：大回撤往往来自多个品种同日或连续几日止损，而不是单个品种亏损。

### 4.3 第三优先：stress loss 相关性

每天对候选和现有组合做统一情景：

| 情景 | 含义 |
| --- | --- |
| spot adverse move | Put 看下跌，Call 看上涨 |
| IV +5/+10 vol | 升波冲击 |
| skew steepening | 尾部报价变贵 |
| margin shock | 保证金率或保证金占用上升 |

构造每个 product-side 的 stress loss 序列，再算相关性或 tail beta。

### 4.4 第四优先：尾部相依系数

对每对品种估计经验下尾/上尾相依：

```text
lower_tail_dep_ij
= P(R_i < Q_i(5%) | R_j < Q_j(5%))

upper_tail_dep_ij
= P(R_i > Q_i(95%) | R_j > Q_j(95%))
```

卖 Put 更关注下尾相依，卖 Call 更关注上尾相依，双卖则两侧都要看。

## 5. Tail-HRP 距离矩阵

将尾部相关性映射为距离：

```text
d_ij = sqrt(0.5 * (1 - tail_corr_ij))
```

但这里的 `tail_corr_ij` 不一定只是一种相关性，可以做加权合成：

```text
tail_corr_ij =
  w1 * shadow_tail_corr_ij
+ w2 * stop_cluster_corr_ij
+ w3 * stress_loss_tail_corr_ij
+ w4 * empirical_tail_dependence_ij
```

初始建议：

```text
w1 = 0.40  # shadow PnL 尾部相关性
w2 = 0.25  # 止损聚集
w3 = 0.25  # stress loss 尾部相关性
w4 = 0.10  # 经验尾部相依
```

这些权重第一版不应参数搜索，而应先做稳定性和敏感性检验。

## 6. 分层结构

Tail-HRP 不应完全让算法自由聚类，也不应完全按人工板块固定。建议混合：

```text
第一层：market family
  commodity / index / ETF

第二层：sector / corr_group
  黑色、能化、有色、贵金属、农产品、股指、ETF

第三层：product
  CU / AU / M / IO / HO ...

第四层：side
  Put / Call

第五层：delta bucket
  0-0.02 / 0.02-0.04 / 0.04-0.06 / 0.06-0.08 / 0.08-0.10
```

HRP 可以用于第三层以后，也可以在第二层先做人工 cap，再在组内用 HRP。

建议第一版：

```text
sector cap 先验保留；
sector 内 product-side 用 Tail-HRP；
全组合再做一次 stress budget 汇总裁剪。
```

## 7. 风险预算口径

HRP 输出不应该是“资金权重”，而应该是风险预算权重。

建议预算单位：

| 预算口径 | 说明 |
| --- | --- |
| `stress_loss_budget` | 主要口径，衡量极端情景可亏多少 |
| `cash_gamma_budget` | 控制短 gamma 集中 |
| `cash_vega_budget` | 控制升波损失 |
| `margin_shock_budget` | 控制保证金挤兑 |
| `stop_cluster_budget` | 控制止损聚集 |

最终每个 product-side 的开仓上限：

```text
budget_i =
  total_stress_budget
* tail_hrp_weight_i
* premium_quality_multiplier_i
* regime_multiplier_i
```

其中：

- `tail_hrp_weight_i` 负责分散尾部聚合；
- `premium_quality_multiplier_i` 来自 B2/B4/B5，负责把预算给高质量权利金；
- `regime_multiplier_i` 负责在升波/降波/正常环境下调节总风险；
- 最后仍需经过 margin、cash gamma、cash vega、单品种、单板块硬约束。

## 8. 预期效果

B6 不应该被期待为单独大幅提高收益的模块。它更可能带来：

| 目标 | 预期 |
| --- | --- |
| 收益 | 可能略降或持平 |
| 最大回撤 | 下降 |
| Calmar | 提升 |
| Sharpe | 可能提升 |
| 止损聚集 | 下降 |
| vega/gamma 损耗集中度 | 下降 |
| 保证金挤兑概率 | 下降 |
| 策略容量 | 提升 |

如果 Tail-HRP 明显牺牲 theta，但没有降低回撤和止损 cluster，则说明风险矩阵口径不对，不能上线。

## 9. 实验设计建议

### 9.1 Shadow first

第一阶段不直接改交易。用 B5 full shadow 结果做回放：

```text
每天根据 T 日已知候选因子和历史尾部矩阵生成 Tail-HRP 权重；
用 T+1 后的 shadow outcome 评估按权重组合后的表现。
```

这避免在没有确认矩阵有效前直接改变真实回测。

### 9.2 三组对照

| 版本 | 描述 |
| --- | --- |
| B6a | 普通相关性 HRP，仅作反例基准 |
| B6b | shadow PnL tail corr HRP |
| B6c | shadow tail corr + stop cluster + stress loss hybrid HRP |
| B6d | B6c + sector/corr_group 先验 cap |

重点不是选收益最高，而是看尾部指标。

### 9.3 评价指标

必须报告：

- 年化收益；
- 最大回撤；
- Calmar；
- Sharpe；
- worst 1d / worst 5d；
- stop cluster count；
- cluster-level stress share；
- top5 product stress share；
- cash vega concentration；
- cash gamma concentration；
- margin shock days；
- PnL attribution 中 vega/gamma 损耗是否更分散。

## 10. 未来函数边界

Tail-HRP 最容易犯的错误是用未来 tail outcome 估计当日矩阵。

硬约束：

```text
T 日权重只能使用 T 日及以前已经观察到的数据；
shadow outcome 只能用于研究标签，不能用于同日或过去权重生成；
滚动窗口必须严格 shift；
新上市品种尾部样本不足时，先继承 sector prior，不能用未来样本补齐。
```

样本不足处理：

| 情况 | 处理 |
| --- | --- |
| 单品种历史 < 60 日 | 不估个体 tail corr，使用 sector prior |
| shadow outcome 不足 | 使用 stress proxy 和标的尾部相关性 |
| 新品种刚上市 | 观察期后交易，但 HRP 权重受 sector cap 约束 |
| 尾部样本太少 | 使用 shrinkage：向 sector matrix / identity matrix 收缩 |

## 11. 与 B5 的关系

B5 full shadow 已经开始输出：

- product panel；
- product-side panel；
- delta-ladder panel；
- portfolio panel；
- empirical lower/upper tail dependence proxy；
- candidate-level forward labels。

B6 应优先复用这些输出，不要在交易引擎里重复实现复杂统计。

推荐实现方式：

```text
scripts/analyze_s1_b6_tail_hrp_shadow.py
```

该脚本读取：

```text
s1_candidate_universe_<tag>.csv
s1_candidate_outcomes_<tag>.csv
s1_b5_product_panel_<tag>.csv
s1_b5_product_side_panel_<tag>.csv
s1_b5_portfolio_panel_<tag>.csv
```

输出：

```text
s1_b6_tail_hrp_weights_<tag>.csv
s1_b6_tail_hrp_shadow_nav_<tag>.csv
s1_b6_tail_hrp_cluster_risk_<tag>.csv
s1_b6_tail_hrp_report_<tag>.md
```

## 12. 参考文献与启发

| 主题 | 文献 | 对本项目的启发 |
| --- | --- | --- |
| HRP | Marcos López de Prado, “Building Diversified Portfolios that Outperform Out-of-Sample”, Journal of Portfolio Management, 2016. SSRN: https://ssrn.com/abstract=2708678 | HRP 用层级聚类和递归二分做组合分配，避免传统二次优化对协方差矩阵求逆的脆弱性。 |
| Tail-HRP | Harald Lohre, Carsten Rother, Kilian Axel Schäfer, “Hierarchical Risk Parity: Accounting for Tail Dependencies in Multi-Asset Multi-Factor Allocations”, 2020. SSRN: https://ssrn.com/abstract=3513399 | 直接支持“HRP 可以用 lower tail dependence 替代 Pearson correlation”，非常适合卖权组合的下行/上行尾部管理。 |
| CoVaR | Tobias Adrian and Markus K. Brunnermeier, “CoVaR”, NBER Working Paper, 2011. SSRN: https://ssrn.com/abstract=1939717 | 用“系统在某一成员处于压力状态下的 VaR”度量系统性贡献，启发我们做 `P(portfolio tail loss | product-side stop/stress)`。 |
| Expected Shortfall / CVaR | Rockafellar and Uryasev, “Optimization of Conditional Value-at-Risk”, Journal of Risk, 2000. DOI: https://doi.org/10.21314/JOR.2000.038 | ES/CVaR 比 VaR 更关心尾部均值损失，适合定义 Tail-HRP 的风险预算目标和 shadow 回放评价指标。 |
| Copula 与尾部依赖 | Embrechts, McNeil and Straumann, “Correlation and Dependence in Risk Management: Properties and Pitfalls”, 2002. | 普通相关性无法完整描述风险依赖，尤其是非正态和尾部风险；支持我们引入 tail dependence / copula proxy。 |
| 金融时间序列 Copula | Andrew Patton, “Copula-Based Models for Financial Time Series”, Handbook of Financial Time Series, 2009. | 提供金融时间序列中非对称依赖、动态依赖和 copula 建模框架；后续可扩展到 t-copula / skew-t copula。 |

## 13. 当前结论

Tail-HRP 是 S1 后续组合优化中值得优先研究的一层，但它的目标应是“减少尾部聚合”，而不是“直接追求收益最大化”。

更具体地说：

```text
如果 B2/B4/B5 负责把权利金收厚，
那么 B6 负责防止这些厚权利金来自同一个尾部风险簇。
```

这对我们的目标非常关键：

- 年化收益要向乐得靠近；
- 最大回撤要受控；
- vega 收益希望改善；
- gamma / stop cluster 不能因为加仓而失控；
- 组合要能支撑更多品种和更高保证金使用率。

因此下一步建议：

```text
等 B5 full shadow 完成后，
先用 B5 输出做 B6 Tail-HRP shadow replay，
验证它是否能降低 stop cluster、cash vega/gamma 集中和最大回撤，
再决定是否进入真实交易回测。
```
