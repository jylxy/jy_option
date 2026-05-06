# S1 全市场期货尾部相关性与相关性突变研究方案

日期：2026-04-30  
适用策略：S1 纯卖权 / 空波动率 / 收权利金策略  
研究对象：全市场期货标的收益率，而不是 S1 已成交持仓  
目标用途：为 B7 组合层风险预算、Tail-HRP、板块/相关组约束、相关性突变降仓机制提供依据。

---

## 1. 研究背景

S1 策略的核心收益来源是卖出期权权利金，承担的是 short gamma、short vega 和跳跃风险。单个品种的风险可以通过 Delta、DTE、权利金质量、止损和流动性管理，但组合层最危险的问题不是某一个品种亏损，而是多个品种在同一段尾部行情中同时不利移动。

因此，本研究不再从“我们交易过什么合约”出发，而是从全市场期货标的本身出发，研究：

1. 哪些品种在普通行情里相关，但尾部并不强相关。
2. 哪些品种平时相关不高，但在尾部行情中会突然一起动。
3. 人工板块分类是否能解释尾部相关性。
4. 相关性突变是否能提前作为组合降预算信号。
5. 最终是否可以构造比简单板块约束更合理的 `tail cluster` 和 Tail-HRP 风险预算。

这件事对卖权策略尤其重要，因为卖权策略平时看起来天然分散，但一旦发生宏观冲击、产业链共振、流动性收缩或政策冲击，相关性会显著上升，多个短期权同时承压，导致止损聚集、保证金挤压和 NAV 跳水。

---

## 2. 学术依据

### 2.1 极端相关不是普通相关

Longin and Solnik (2001) 研究国际股票市场的极端相关，指出相关性并不只是随波动率机械上升，更与市场趋势和下行尾部有关；他们发现熊市中相关性上升更明显，而牛市中不一定对称。这对 S1 很关键：我们不能只用普通 Pearson 相关来管理卖权组合，因为真正伤害组合的是同向尾部共振。

参考：  
[Longin and Solnik, Extreme Correlation of International Equity Markets, Journal of Finance, 2001](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=147848)

### 2.2 多变量 EVT 与尾部依赖

Poon, Rockinger and Tawn (2004) 提出用多变量极值理论识别和建模金融市场联合尾部分布，并强调传统依赖度量可能低估组合风险。S1 的组合风险不是均值-方差问题，而是“多个品种同时进入极端不利收益”的联合尾部问题。

参考：  
[Poon, Rockinger and Tawn, Extreme Value Dependence in Financial Markets, Review of Financial Studies, 2004](https://academic.oup.com/rfs/article/17/2/581/1577005)

### 2.3 动态相关与相关性突变

Engle (2002) 的 DCC-GARCH 提供了动态条件相关的经典框架，用于描述相关性随时间变化。我们的第一版不需要完整估计 DCC-GARCH，但应借鉴其思想：相关性不是常数，需要做滚动、EWMA 或状态切换监控。

参考：  
[Engle, Dynamic Conditional Correlation, Journal of Business & Economic Statistics, 2002](https://www.tandfonline.com/doi/abs/10.1198/073500102288618487)

### 2.4 高频数据可以更稳健地估计波动和协动

Andersen, Bollerslev, Diebold and Labys (2003) 建立了 realized volatility 的高频数据测度框架；Barndorff-Nielsen and Shephard (2004) 进一步研究 realized covariation。我们有分钟级期货数据，因此不仅可以做日收盘收益率相关，也可以做日内 realized covariance、跳跃贡献和 gap/intraday 分解。

参考：  
[Andersen et al., Modeling and Forecasting Realized Volatility, Econometrica, 2003](https://www.nber.org/papers/w8160)  
[Barndorff-Nielsen and Shephard, Econometric Analysis of Realized Covariation, Econometrica, 2004](https://public.econ.duke.edu/~get/browse/courses/883/Spr15/COURSE-MATERIALS/Z_Papers/BNSEcma2004.pdf)

### 2.5 Copula 可以刻画非线性与不对称尾部依赖

Patton (2006) 提出条件 copula 框架，用于描述汇率之间的不对称依赖。对期货品种而言，上尾和下尾可能完全不同：卖 Put 关心下尾共振，卖 Call 关心上尾共振，双卖关心绝对尾部和波动扩张。因此，后续可以在非参数经验尾部指标基础上，引入 t-copula 或动态 copula。

参考：  
[Patton, Modelling Asymmetric Exchange Rate Dependence, International Economic Review, 2006](https://public.econ.duke.edu/~ap172/Patton_IER_2006.pdf)

### 2.6 商品期货存在金融化和跨品种相关性抬升

Tang and Xiong (2012) 讨论商品金融化之后，不同商品价格共动增强；Silvennoinen and Thorp (2013) 使用动态相关模型研究商品期货与传统资产的相关性变化，发现危机期相关性和整合程度会上升。这说明商品并不是天然分散，尤其在宏观风险或资金行为主导时，多个板块可能被同一个风险因子驱动。

参考：  
[Tang and Xiong, Index Investment and the Financialization of Commodities, Financial Analysts Journal, 2012](https://www.tandfonline.com/doi/abs/10.2469/faj.v68.n6.5)  
[Silvennoinen and Thorp, Financialization, Crisis and Commodity Correlation Dynamics, Journal of International Financial Markets, Institutions and Money, 2013](https://www.sciencedirect.com/science/article/pii/S1042443112001059)

### 2.7 网络结构与 Tail-HRP

Mantegna (1999) 用相关矩阵构造金融市场的层级网络，López de Prado (2016) 提出 Hierarchical Risk Parity，用聚类和递归风险分配避免直接反演不稳定协方差矩阵。S1 后续的组合预算不应只按人工板块等权，而应结合尾部相关矩阵形成 tail cluster，再做 Tail-HRP。

参考：  
[Mantegna, Hierarchical Structure in Financial Markets, 1999](https://arxiv.org/abs/cond-mat/9802256)  
[López de Prado, Building Diversified Portfolios that Outperform Out-of-Sample, 2016](https://smallake.kr/wp-content/uploads/2020/04/SSRN-id2708678.pdf)

---

## 3. 核心研究问题

### Q1：普通相关是否低估尾部相关？

检验每一对期货品种：

```text
normal_corr_ij
tail_dependence_ij
tail_dependence_ij - normal_corr_ij
```

如果很多品种普通相关不高，但尾部相关显著高，说明简单分散会在压力期失效。

### Q2：板块分类是否能解释尾部相关？

检验同板块 pair 是否比跨板块 pair 有更高尾部相关，同时控制普通相关：

```text
tail_dep_ij = alpha
            + beta1 * same_bucket_ij
            + beta2 * same_corr_group_ij
            + beta3 * normal_corr_ij
            + beta4 * same_exchange_ij
            + epsilon_ij
```

如果 `beta1` 或 `beta2` 显著为正，说明板块分类对尾部共振有解释力。  
如果不显著，说明目前板块分类可能只是业务分类，不足以作为组合风险分类。

### Q3：是否存在“平时低相关、尾部高相关”的隐性风险对？

寻找以下 pair：

```text
normal_corr_ij <= 0.20
tail_dependence_ij >= 0.40
```

这类品种在普通回测中会被误认为分散，但在尾部会一起冲击组合，是 S1 组合层最应关注的对象。

### Q4：相关性突变是否可以作为 S1 降预算信号？

构造每日市场相关性状态：

```text
avg_pair_corr_t
avg_tail_corr_t
top_eigenvalue_share_t
matrix_distance_t
tail_cluster_density_t
```

当这些指标从历史分位的正常区间快速上升时，认为市场进入“相关性收敛”或“尾部共振升温”状态。此时即使单品种信号仍然好，也需要降低组合总预算。

### Q5：上尾和下尾是否对称？

对卖 Put 和卖 Call 分别研究：

```text
lower_tail_dep_ij = P(r_j <= q_j(5%) | r_i <= q_i(5%))
upper_tail_dep_ij = P(r_j >= q_j(95%) | r_i >= q_i(95%))
```

如果下尾更强，说明卖 Put 的组合拥挤风险更高；如果上尾更强，说明卖 Call 在某些板块可能更危险。  
这会直接影响 P/C 预算偏移，而不是只影响品种选择。

---

## 4. 数据设计

### 4.1 数据源

使用 IT 已提供的分钟级期货数据：

```text
future_hf_1min
```

研究对象是期货标的，不是期权成交记录。  
期权回测输出只用于后续验证尾部相关指标是否能解释 S1 的止损聚集和回撤，不参与第一阶段指标构造。

### 4.2 样本范围

建议尽量覆盖有数据以来的全部期货品种，并按上市后观察期处理：

```text
样本起点：数据可得最早日期
样本终点：数据可得最后一日
品种准入：上市后至少 3 个月观察期
最低样本：至少 120 个交易日
```

上市较晚的品种不应被排除，但其尾部指标在样本不足时只作为探索性结果，不进入正式组合约束。

### 4.3 收益率口径

每个品种每日生成以下收益序列：

| 字段 | 定义 | 用途 |
|---|---|---|
| `ret_cc` | 收盘到收盘收益 | 全日普通相关与尾部相关主口径 |
| `ret_overnight` | 今日开盘 / 昨日收盘 - 1 | 跳空与止损失效风险 |
| `ret_intraday` | 今日收盘 / 今日开盘 - 1 | 日内趋势与盘中风险 |
| `ret_range` | high / low - 1 | 盘中波动扩张 |
| `rv_1d` | 分钟收益平方和 | realized vol / realized covariance |
| `jump_share` | 大分钟收益平方 / 总平方收益 | 跳跃贡献 |

对 S1 而言，`ret_cc` 是基础，`ret_overnight` 和 `jump_share` 对止损有效性更关键，`ret_range` 和 `rv_1d` 对 short gamma 风险更关键。

### 4.4 连续合约处理

尾部相关研究非常怕换月假跳变，因此必须明确连续合约规则：

1. 优先使用已复权主力连续收益。
2. 如果只能读取具体合约分钟数据，则按主力持仓/成交额切换，并在换月日剔除由合约切换造成的价差跳变。
3. 极端收益日必须附带 `roll_flag`，后续统计可以选择剔除或单独检验。

---

## 5. 指标体系

### 5.1 普通相关

```text
pearson_corr_ij
spearman_corr_ij
kendall_tau_ij
```

普通相关用于描述日常共动，但不作为尾部风控的唯一依据。

### 5.2 经验尾部依赖

对每个品种使用自身分位数定义尾部：

```text
lower_tail_i(q) = 1(ret_i <= quantile_i(q))
upper_tail_i(q) = 1(ret_i >= quantile_i(1-q))
abs_tail_i(q)   = 1(abs(ret_i) >= quantile_i(abs, 1-q))
```

建议第一版使用：

```text
q = 5%
q = 2.5% 作为稳健性检验
```

尾部依赖指标：

```text
lambda_lower_ij(q) = P(lower_tail_j | lower_tail_i)
lambda_upper_ij(q) = P(upper_tail_j | upper_tail_i)
jaccard_lower_ij   = common_lower_tail_days / union_lower_tail_days
jaccard_upper_ij   = common_upper_tail_days / union_upper_tail_days
phi_tail_ij        = corr(binary_tail_i, binary_tail_j)
```

解释：

- `lambda_lower` 更适合卖 Put 风险。
- `lambda_upper` 更适合卖 Call 风险。
- `abs_tail` 更适合双卖、strangle 和组合总风险。
- `jaccard` 更适合构建网络和聚类。

### 5.3 条件尾部损失

为了避免只看“是否同一天尾部”，还要看尾部日损失强度：

```text
co_tail_loss_ij = mean(loss_j | i is tail)
co_tail_es_ij   = ES_j | i is tail
tail_beta_ij    = regression(loss_j ~ loss_i | tail state)
```

这里的 `loss` 对卖 Put 可取 `-ret` 的正部，对卖 Call 可取 `ret` 的正部，对双卖可取 `abs(ret)`。

### 5.4 相关性突变指标

滚动窗口建议：

```text
short_window = 60 trading days
mid_window   = 120 trading days
long_window  = 250 trading days
```

每日构造：

```text
rolling_corr_matrix_t
rolling_tail_matrix_t
```

然后计算：

```text
avg_corr_t = average upper triangle corr
avg_tail_dep_t = average upper triangle tail dependence
top_eigen_share_t = first eigenvalue / sum eigenvalues
matrix_distance_t = FrobeniusDistance(Corr_t, Corr_longrun)
network_density_t = share of pair tail_dep > threshold
tail_cluster_count_t = number of detected communities
```

突变判定：

```text
corr_shock_t = avg_corr_t > rolling_percentile(avg_corr, 90%)
            or top_eigen_share_t > rolling_percentile(top_eigen_share, 90%)
            or matrix_distance_t > rolling_percentile(matrix_distance, 90%)
```

这一类指标用于组合层总预算，而不是单合约排序。

---

## 6. 板块解释力检验

### 6.1 同板块 vs 跨板块

计算：

```text
mean_tail_dep_same_bucket
mean_tail_dep_cross_bucket
mean_tail_dep_same_corr_group
mean_tail_dep_cross_corr_group
```

然后做：

1. bootstrap 均值差置信区间。
2. permutation test：随机打乱板块标签，检验真实板块分类是否显著优于随机分类。
3. 分上尾、下尾、绝对尾部分别检验。

### 6.2 控制普通相关后的板块解释

回归：

```text
tail_dep_ij = alpha
            + beta1 * same_bucket_ij
            + beta2 * same_corr_group_ij
            + beta3 * pearson_corr_ij
            + beta4 * spearman_corr_ij
            + beta5 * same_exchange_ij
            + epsilon_ij
```

如果 `same_bucket` 在控制普通相关后仍然显著，说明板块分类确实包含尾部结构信息。  
如果不显著，说明尾部风险需要重新聚类，不能只靠人工板块。

### 6.3 网络聚类与人工板块对比

用尾部距离构造网络：

```text
tail_distance_ij = sqrt(0.5 * (1 - tail_corr_ij))
```

或：

```text
tail_distance_ij = 1 - jaccard_tail_ij
```

对网络做层次聚类或 Louvain 社区发现，并用以下指标比较自动 cluster 与人工板块：

```text
Adjusted Rand Index
Normalized Mutual Information
Cluster purity
```

如果自动聚类和板块分类高度一致，说明当前板块约束有理论支持。  
如果不一致，则应新增 `tail_cluster` 字段，并在组合风控里优先使用。

---

## 7. 与 S1 收益拆解公式的关系

本研究主要改善公式中的：

```text
Tail / Stop Loss
```

其次改善：

```text
Retention Rate
Deployment Ratio
```

具体映射如下：

| 研究输出 | 改善变量 | 适用层级 | 使用方式 |
|---|---|---|---|
| `tail_cluster` | Tail / Stop Loss | 组合层 | 限制同 cluster stress budget |
| `lower_tail_beta` | Tail / Stop Loss | P/C 侧、品种层 | 降低卖 Put 预算 |
| `upper_tail_beta` | Tail / Stop Loss | P/C 侧、品种层 | 降低卖 Call 预算 |
| `corr_shock_state` | Tail / Stop Loss、Deployment Ratio | 时间层、组合层 | 全组合降预算 |
| `tail_hrp_weight` | Tail / Stop Loss | 组合层 | 组合预算分配 |
| `sector_explain_power` | Tail / Stop Loss | 组合层 | 判断板块约束是否足够 |
| `hidden_tail_pair` | Tail / Stop Loss | 组合层 | 限制隐性高尾部 pair 同时持仓 |

注意：这些指标不直接回答“哪个合约权利金质量更好”，而回答“哪些品种不能同时卖太多”。所以它们不应混入 B2/B6 的合约排序因子，而应进入 B7 组合层。

---

## 8. 假设体系

### H1：尾部相关显著高于普通相关

```text
H0: tail_dep_ij 与 normal_corr_ij 没有系统差异
H1: 存在大量 pair 的 tail_dep_ij 显著高于 normal_corr_ij
```

如果 H1 成立，说明普通相关矩阵不足以管理 S1 组合尾部风险。

### H2：板块分类能解释部分尾部相关，但不完全充分

```text
H0: same_bucket 对 tail_dep 无解释力
H1: same_bucket 对 tail_dep 有显著正解释力
```

如果 H1 只在部分板块成立，说明需要“人工板块 + 自动 tail cluster”双层结构。

### H3：尾部相关存在方向不对称

```text
H0: upper_tail_dep = lower_tail_dep
H1: upper_tail_dep != lower_tail_dep
```

如果成立，说明卖 Put 和卖 Call 的组合预算不能共用同一个相关矩阵。

### H4：相关性突变与 S1 回撤/止损聚集有关

第一阶段先独立研究市场，不使用交易数据。  
第二阶段验证：

```text
corr_shock_state_t 是否领先或同步解释 S1 stop_count_t、drawdown_t、stress_loss_t
```

如果成立，则 `corr_shock_state` 可以作为组合降预算信号。

### H5：尾部自动聚类优于人工板块分类

```text
H0: 人工板块分类已足够解释尾部共振
H1: 自动 tail cluster 能额外解释尾部共振和 S1 回撤
```

如果成立，B7 应引入 `tail_cluster_budget`，而不是只使用 `bucket_budget`。

---

## 9. 输出物设计

### 9.1 数据表

| 文件 | 内容 |
|---|---|
| `market_product_daily_returns.csv` | 每个期货品种的日频收益、gap、intraday、RV、jump share |
| `market_pair_tail_dependence.csv` | pair 级普通相关、尾部相关、Jaccard、tail beta |
| `market_tail_cluster_membership.csv` | 每个品种的自动 tail cluster、人工板块、差异标记 |
| `market_corr_shock_timeseries.csv` | 每日相关性突变指标 |
| `market_sector_explain_tests.csv` | 板块解释力检验结果 |
| `market_hidden_tail_pairs.csv` | 平时低相关但尾部高相关的隐性风险 pair |

### 9.2 图表

| 图 | 用途 |
|---|---|
| 普通相关热力图 | 平时共动结构 |
| 下尾相关热力图 | 卖 Put 组合风险 |
| 上尾相关热力图 | 卖 Call 组合风险 |
| 尾部 Jaccard 网络图 | 识别 tail cluster |
| 相关性突变时序图 | 判断何时全组合降预算 |
| 第一特征值解释度时序图 | 判断市场是否进入单因子驱动 |
| 同板块 vs 跨板块箱线图 | 检验板块分类有效性 |
| 自动 cluster 与人工板块 Sankey/交叉表 | 看板块是否错配 |
| hidden tail pair Top 30 | 找平时看似分散但尾部一起动的品种 |

### 9.3 研究报告

报告应至少包含：

1. 研究目的和结论摘要。
2. 数据口径和样本覆盖。
3. 普通相关 vs 尾部相关差异。
4. 上尾、下尾、绝对尾部三套结果。
5. 板块分类解释力检验。
6. 相关性突变事件复盘。
7. 对 S1/B7 组合风控的建议。
8. 可直接进入配置文件的 `tail_cluster` 和预算建议。

---

## 10. 实验步骤

### Phase 0：数据抽取和清洗

目标：从 `future_hf_1min` 构建全市场日频期货收益面板。

关键检查：

1. 品种上市时间。
2. 主力连续切换。
3. 夜盘和日盘交易时段。
4. 停牌、无成交、涨跌停。
5. 换月日异常跳变。
6. 极端收益是否来自真实行情还是数据问题。

### Phase 1：静态尾部相关矩阵

在全样本上计算：

1. Pearson / Spearman。
2. 下尾、上尾、绝对尾部依赖。
3. pairwise tail beta。
4. hidden tail pair。

目的：回答“哪些品种天然不能简单视为分散”。

### Phase 2：滚动相关性突变

使用 60D、120D、250D 窗口构造动态指标：

1. 滚动平均普通相关。
2. 滚动平均尾部相关。
3. 第一特征值解释度。
4. 矩阵距离。
5. 网络密度。

目的：识别市场从分散状态进入共振状态的日期。

### Phase 3：板块解释力检验

将 pairwise 指标和人工板块、`corr_group` 连接，检验：

1. 同板块是否更高尾部相关。
2. 控制普通相关后，板块是否仍有解释力。
3. 自动 tail cluster 是否和人工板块一致。

目的：判断当前板块约束是否足够，还是需要新增 tail cluster。

### Phase 4：和 S1 回测结果交叉验证

不使用 S1 数据训练尾部指标，只用来验证：

1. `corr_shock_state` 是否对应 S1 回撤期。
2. `tail_cluster` 是否解释止损聚集。
3. hidden tail pair 是否在 S1 中造成同日或连续止损。
4. 如果按 tail cluster 限制预算，历史回撤会不会降低。

目的：把市场结构研究接入 B7，而不是让它停留在图表层。

### Phase 5：B7 组合风控实验

将研究输出转成策略参数：

```text
tail_cluster_budget_limit
tail_cluster_stress_limit
lower_tail_budget_limit
upper_tail_budget_limit
corr_shock_total_budget_multiplier
tail_hrp_product_weight
```

然后设计 B7 系列：

1. `B7a`: 仅加入 tail cluster 预算上限。
2. `B7b`: 加入相关性突变全组合降预算。
3. `B7c`: Tail-HRP 替代简单等权/质量倾斜。
4. `B7d`: 上尾/下尾分开控制 P/C 预算。
5. `B7e`: B7a-d 综合版。

---

## 11. 防未来函数要求

本研究输出若用于交易，必须遵守：

1. 任一日期 `t` 的相关性指标只能使用 `t` 之前的数据。
2. 滚动尾部阈值必须用历史分位，不能用全样本分位。
3. 新上市品种必须先观察 3 个月，观察期内只记录，不交易或不进入正式预算。
4. 自动聚类如用于实盘，必须按月或按季度用过去数据重估，不能用未来全样本 cluster。
5. B7 回测中，`tail_cluster` 和 `corr_shock_state` 必须是当时可知版本。

为了研究展示，可以同时输出：

```text
full_sample_ex_post_result
rolling_out_of_sample_result
```

但最终判断必须以 rolling out-of-sample 为准。

---

## 12. 对 S1 的预期贡献

这项研究不直接提高 Premium Pool，也不负责选出权利金最厚的合约。它的价值在于：

1. 降低多个品种同时止损的概率。
2. 降低保证金使用在尾部行情中突然聚集的风险。
3. 在相关性突变时主动降低 Deployment Ratio。
4. 避免“看起来跨板块，实际上尾部同源”的假分散。
5. 为 Tail-HRP 提供比普通相关更贴近卖权风险的距离矩阵。

用收益公式表达：

```text
S1 net return
= Premium Pool
 * Deployment Ratio
 * Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

本研究主要目标是降低 `Tail / Stop Loss`，并在市场共振时动态降低 `Deployment Ratio`。  
如果后续 B7 实验成功，应该表现为：收益可能略降或持平，但最大回撤、止损聚集、单日尾部亏损、保证金挤压显著下降，Calmar 和回撤恢复速度改善。

---

## 13. 第一版最小可行实验

为了快速落地，第一版不直接做复杂 copula 或 DCC，而做非参数经验版：

1. 从 `future_hf_1min` 聚合日频收益。
2. 计算 `ret_cc`、`ret_overnight`、`ret_intraday`、`rv_1d`。
3. 使用 5% 分位定义上尾、下尾、绝对尾部。
4. 输出普通相关、尾部条件概率、Jaccard。
5. 做同板块 vs 跨板块检验。
6. 做 120D 滚动平均相关和第一特征值时序。
7. 输出初版 `tail_cluster`。
8. 与 B2C/B6 的止损聚集和回撤日期做交叉验证。

第一版先回答：

```text
我们的人工板块分类到底能不能解释尾部共振？
哪些品种 pair 平时不强相关但尾部强相关？
哪些日期是全市场相关性突变状态？
这些日期是否对应 S1 回撤和止损聚集？
```

---

## 14. 后续升级方向

第一版完成后，再考虑：

1. DCC-GARCH 或 EWMA 动态相关模型。
2. t-copula / dynamic copula 的上尾、下尾依赖。
3. EVT + CoVaR / CoES。
4. 高频 realized covariance 矩阵。
5. 区分宏观冲击、产业链冲击、政策冲击、流动性冲击。
6. Tail-HRP 与普通 HRP、等权、流动性权重的对照实验。
7. 把 `corr_shock_state` 作为 S1 总预算 multiplier。

---

## 15. 结论

全市场尾部相关性研究是 S1 从“单品种卖权规则”升级到“组合卖权风控系统”的关键一步。  

普通板块分类可以作为起点，但不能默认它足以覆盖尾部共振。我们需要用期货标的收益率本身验证：哪些品种在极端行情中一起动，哪些板块分类有效，哪些尾部关系隐藏在人工分类之外，以及相关性何时从分散状态突然收敛。

最终，本研究不应变成一个新的合约 alpha 因子，而应成为 B7 组合层风控的基础设施：

```text
人工板块约束
+ 经验 tail cluster
+ 相关性突变降预算
+ 上下尾分侧控制
+ Tail-HRP 风险分配
```

这比简单地增加或减少某些品种更严谨，也更符合卖权策略的真实风险来源：不是每天的波动，而是尾部共振时多个短期权同时失控。
