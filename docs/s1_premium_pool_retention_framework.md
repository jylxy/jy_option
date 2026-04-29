# S1 权利金池与留存率框架备忘录

文档日期：2026-04-29  
策略上下文：S1 纯卖权 / short premium / short volatility  
相关讨论：品种层权利金厚度、流动性、期望收益反推、权利金留存率  
本文定位：记录“卖权收益目标可以拆成权利金池 × 留存率 - 尾部损耗”的研究框架，作为后续 B5.5/B6 组合预算设计基础。

## 1. 问题起点

我们观察到不同品种之间存在明显差异：

| 品种类型 | 典型例子 | 特征 |
| --- | --- | --- |
| 高流动性但权利金偏薄 | 黄金、铜等 | 成交和退出更可靠，但单位保证金/单位风险可收权利金可能偏低。 |
| 权利金更厚但流动性/尾部风险更差 | 白糖、玻璃等 | 可收权利金更高，但可能伴随低流动性、跳价、止损聚集、尾部风险。 |

这说明品种选择不能只看流动性，也不能只看权利金厚度。

更准确的问题是：

```text
在流动性尚可、风险可控的前提下，
哪些品种能提供足够厚、且能留下来的权利金？
```

## 2. 核心公式

卖权策略的收益可以先写成一个很朴素的公式：

```text
月度净收益
= 当月开仓总净权利金
× 权利金留存率
- 止损/尾部损失
- 交易成本与滑点
```

如果做极端理想化假设：

```text
100% 不止损
100% 到期安全归零
无额外滑点和异常价
```

那么：

```text
月度收益上限 ≈ 当月开仓总净权利金 / NAV
```

这意味着卖权策略的目标收益可以反推成需要多少权利金池。

## 3. 目标收益反推

我们当前长期目标是：

```text
年化收益目标：6%
最大回撤目标：小于 2%
```

如果按月度粗略拆分：

```text
目标月度收益 ≈ 0.50%
```

若权利金留存率不同，则所需月度开仓净权利金不同：

| 假设权利金留存率 | 目标月收益 | 所需月度开仓净权利金 / NAV |
| --- | ---: | ---: |
| 100% | 0.50% | 0.50% |
| 80% | 0.50% | 0.625% |
| 70% | 0.50% | 0.714% |
| 50% | 0.50% | 1.00% |
| 30% | 0.50% | 1.67% |

所以一个很关键的问题是：

```text
我们不是只需要“更多开仓”，
而是要知道“开多少权利金，能留下多少权利金”。
```

## 4. 可计算指标

后续应该围绕这个公式建立指标。

### 4.1 可开权利金池

定义：

```text
available_premium_rate_t
= sum(eligible_net_premium_cash_1lot_t) / NAV_t
```

其中 `eligible` 至少满足：

- 次月；
- `abs(delta) < 0.1`；
- 最低价格/费用过滤；
- 基础流动性/持仓量过滤；
- 无明显异常价；
- 不在强制冷静期；
- 合约信息、乘数、保证金有效。

意义：

```text
如果 available_premium_rate 本身太低，
那么策略即便 100% 留存，也很难达到目标收益。
```

### 4.2 实际开仓权利金池

定义：

```text
opened_premium_rate_t
= sum(opened_net_premium_cash_t) / NAV_t
```

意义：

```text
衡量策略实际吃掉了多少权利金池。
```

如果：

```text
available_premium_rate 很高
但 opened_premium_rate 很低
```

说明问题可能在预算、组合限制、流动性排序、开仓规则太保守。

如果：

```text
available_premium_rate 本身就低
```

说明问题在候选池太薄，需要扩大品种、期限、结构或提高保证金使用率。

### 4.3 权利金池覆盖率

定义：

```text
required_premium_rate
= target_monthly_return / expected_retention_rate

premium_coverage_ratio
= available_premium_rate / required_premium_rate
```

解释：

| 覆盖率 | 含义 |
| --- | --- |
| `< 1` | 理论权利金池不足，哪怕留存率达到假设值也不够目标收益。 |
| `≈ 1` | 权利金池刚够，容错空间低。 |
| `> 1` | 有选择空间，可以挑更安全、更流动、更低尾部相关的合约。 |
| `>> 1` | 权利金池很厚，关键转为选择和风控，而不是强行全吃。 |

### 4.4 权利金留存率

定义：

```text
premium_retention_rate
= realized_net_pnl_from_premium / opened_net_premium_cash
```

可以分层：

- 按品种；
- 按 Put/Call；
- 按 delta bucket；
- 按 IV/RV；
- 按 vol regime；
- 按流动性分层；
- 按尾部相关性 cluster；
- 按是否发生止损。

意义：

```text
同样的权利金厚度，能留下来的才是真正的收益来源。
```

## 5. 与现有因子的关系

这个框架不是简单新增一个 premium 因子，而是把现有因子放入同一个收益漏斗。

| 现有因子/字段 | 在公式中的角色 |
| --- | --- |
| `premium_yield_margin` | 单合约权利金相对保证金是否厚。 |
| `premium_to_stress_loss` | 权利金相对压力亏损是否厚。 |
| `premium_to_iv5_loss`、`premium_to_iv10_loss` | 权利金能否覆盖 IV shock。 |
| `theta_vega_efficiency` | 收 theta 时承担多少 vega。 |
| `b5_premium_to_tail_move_loss` | 权利金能否覆盖历史尾部移动。 |
| `b5_premium_per_capital_day` | 单位资金占用天数的权利金效率。 |
| `liquidity_score`、`volume`、`open_interest` | 权利金是否可真实成交和退出。 |
| `stop_cluster`、tail dependence | 权利金是否来自同一个尾部风险簇。 |
| 本框架新增的 `premium_coverage_ratio` | 从组合/品种层判断权利金池是否足够支撑收益目标。 |

所以，后续研究不应该再是“无限堆因子”，而应该围绕收益公式逐项改善。

## 6. 公式拆解后的研究任务

我们可以把策略优化拆成四个变量：

```text
净收益
= 可开权利金池
× 实际开仓比例
× 权利金留存率
- 止损/尾部损耗
- 成本/滑点
```

对应研究方向：

| 变量 | 要改善的问题 | 可能工具 |
| --- | --- | --- |
| 可开权利金池 | 是否有足够可交易的厚权利金 | 扩品种、次月池、delta 梯队、premium depth、流动性容量 |
| 实际开仓比例 | 有权利金但有没有吃到 | 预算上限、保证金上限、品种预算、Tail-HRP 风险预算 |
| 权利金留存率 | 收到的权利金是否能留下来 | B2/B4/B5 因子、forward vega、tail coverage、冷静期、异常价过滤 |
| 止损/尾部损耗 | 是否一次亏掉多月 theta | stop guard、tail dependence、HRP、stress budget、gamma/vega cap |
| 成本/滑点 | 交易成本是否吃掉薄权利金 | 费用表、bid/ask/slippage、低价 tick 过滤、退出容量 |

## 7. 品种层 premium depth

为了解释黄金、铜、白糖、玻璃这类差异，建议新增 product-level 的 premium depth 指标。

### 7.1 基础版本

```text
product_premium_depth
= sum(eligible_net_premium_cash_1lot for product) / NAV
```

### 7.2 风险调整版本

```text
product_premium_depth_per_margin
= sum(net_premium) / sum(margin)

product_premium_depth_per_stress
= sum(net_premium) / sum(stress_loss)

product_premium_depth_per_vega
= sum(net_premium) / abs(sum(cash_vega))

product_premium_depth_per_gamma
= sum(net_premium) / abs(sum(cash_gamma))
```

### 7.3 流动性调整版本

```text
product_premium_depth_liquidity_adjusted
= product_premium_depth * liquidity_capacity_score

product_premium_depth_exit_capacity
= sum(net_premium capped by volume/OI exit capacity)
```

意义：

- 黄金/铜：可能 `liquidity_capacity_score` 高，但 `premium_depth_per_margin` 低；
- 白糖/玻璃：可能 `premium_depth` 高，但 `exit_capacity`、`tail coverage`、`stop cluster` 差；
- 真正值得多给预算：权利金厚、流动性尚可、留存率高、尾部聚合低。

## 8. 后续实验设计

建议在 B5 full shadow 完成后先做分析，不急着改交易。

### 8.1 输出

新增分析脚本或在 B5 报告中派生：

```text
daily_available_premium_pool.csv
monthly_premium_coverage.csv
product_premium_depth_panel.csv
premium_retention_by_product_side.csv
premium_funnel_report.md
```

### 8.2 必看图表

- 每日/每月可开权利金池；
- 实际开仓权利金池；
- 目标收益所需权利金线；
- 权利金池覆盖率；
- 品种 premium depth Top/Bottom；
- 品种留存率；
- 权利金厚度 × 留存率二维图；
- 权利金厚度 × 流动性二维图；
- 厚权利金品种的止损率和尾部亏损；
- 月度收益拆解：权利金池、留存、止损、成本。

### 8.3 判断标准

| 问题 | 判断 |
| --- | --- |
| 权利金池不足 | 扩大可交易池或提高风险预算才有意义。 |
| 权利金池足但开仓少 | 检查预算、组合约束、保证金和排序。 |
| 开仓权利金足但留不住 | 检查因子、止损、异常价和尾部相关性。 |
| 留存率高但收益低 | 可能仓位过低或只做了薄权利金品种。 |
| 权利金厚但回撤大 | 需要 Tail-HRP、stress cap、流动性/异常价约束。 |

## 9. 当前结论

我们后续不应该只问：

```text
哪个因子 IC 更高？
哪个版本 NAV 更高？
```

更应该把问题改写为：

```text
目标收益需要多少权利金？
当前市场和规则下可开权利金池够不够？
我们实际吃到了多少？
吃到的权利金留住了多少？
损耗来自止损、vega、gamma、流动性还是尾部聚合？
```

这会让 S1 的研究从“堆因子”转成“逐项改善收益公式”。

后续主线可以写成：

```text
S1 净收益
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

我们的优化工作就是逐项改善：

1. 扩大和识别高质量 `Premium Pool`；
2. 在风险可控下提高 `Deployment Ratio`；
3. 通过合约质量、IV/RV、forward vega、冷静期提高 `Retention Rate`；
4. 通过 Tail-HRP、stress budget、stop guard 降低 `Tail / Stop Loss`；
5. 通过费用、低价、流动性和退出容量降低 `Cost / Slippage`。

## 10. 学术与实盘合理性

这个框架从学术和实盘角度都是合理的，但必须把它理解为“收益目标拆解框架”，而不是“按照目标收益倒推开仓规模”的机械交易规则。

卖权收益的长期基础通常来自：

- 隐含波动率相对未来实现波动率的风险溢价；
- 市场对尾部风险、跳空风险、流动性风险和保险需求支付的补偿；
- 时间价值衰减带来的 theta carry。

但权利金厚并不天然代表好交易。高权利金可能来自：

- 真实 realized volatility 将要上升；
- IV 正在扩张；
- skew 正在 steepen；
- 标的处于趋势突破或跳空环境；
- 流动性差、tick 离散大、退出成本高；
- 市场正在为事件或尾部风险定价。

所以我们的公式可以用来回答：

```text
目标收益需要多少权利金？
当前市场是否提供足够可交易的权利金池？
这些权利金最终能留下多少？
损耗主要来自哪里？
```

但不能简化成：

```text
为了赚 0.5% 月收益，就强行开够 0.5% 或 1.0% NAV 的权利金。
```

更准确的做法是：

```text
用目标收益反推 required premium；
用候选池衡量 available premium；
用风控和组合优化决定 opened premium；
用因子、止损、异常价、Tail-HRP 提高 retention、降低 tail loss。
```

## 11. 分层公式

为了避免公式过粗，后续分析需要分层。

### 11.1 合约层

合约层回答：

```text
这个合约值不值得卖？
```

核心拆解：

```text
contract_expected_pnl
= net_premium_cash
× contract_retention_probability
- expected_stop_loss
- transaction_cost
```

需要关注：

- delta bucket；
- net premium；
- premium / margin；
- premium / stress loss；
- premium / tail move loss；
- theta / vega；
- theta / gamma；
- fee ratio；
- tick value ratio；
- low price flag；
- abnormal jump risk；
- liquidity and exit capacity。

### 11.2 P/C 侧

P/C 侧回答：

```text
今天这个品种更应该卖 Put、Call，还是双边都卖？
```

核心拆解：

```text
side_expected_pnl
= side_premium_pool
× side_retention_rate
- side_tail_loss
- side_stop_loss
```

需要关注：

- Put/Call premium depth；
- put skew / call skew；
- risk reversal；
- trend / momentum；
- breakout distance；
- upper/lower tail risk；
- side stop rate；
- side vega/gamma loss；
- side liquidity。

这里尤其要注意：长期偏 Put 本质上是方向性看涨暴露；长期偏 Call 本质上是方向性看跌暴露。P/C 偏移必须由趋势、skew、RV、tail risk 和权利金质量共同决定，不能只看哪边权利金更厚。

### 11.3 品种层

品种层回答：

```text
这个品种应该多分预算，少分预算，还是只观察？
```

核心拆解：

```text
product_expected_pnl
= product_premium_pool
× product_deployment_ratio
× product_retention_rate
- product_stop_tail_loss
- product_cost_slippage
```

需要关注：

- product premium depth；
- premium depth / margin；
- premium depth / stress；
- premium depth / cash vega；
- premium depth / cash gamma；
- product liquidity capacity；
- product exit capacity；
- product historical retention rate；
- product stop cluster；
- product tail beta；
- product regime stability。

这正好解释黄金、铜、白糖、玻璃这类差异：黄金/铜可能流动性好但权利金薄；白糖/玻璃可能权利金厚但尾部和流动性损耗更大。真正值得给更多预算的是“权利金厚 + 能留下 + 能退出 + 尾部不聚集”的品种。

### 11.4 组合层

组合层回答：

```text
这些品种、方向和合约能不能同时卖？
```

核心拆解：

```text
portfolio_expected_pnl
= sum(product_side_expected_pnl)
- diversification_failure_loss
- margin_squeeze_loss
```

需要关注：

- sector/corr_group exposure；
- tail correlation；
- stop cluster correlation；
- cash gamma concentration；
- cash vega concentration；
- margin shock；
- expiry cluster；
- Tail-HRP risk budget；
- effective product count；
- top5 stress share。

### 11.5 时间/月度层

时间层回答：

```text
这个月的目标收益是否有足够权利金池支持？
```

核心拆解：

```text
monthly_expected_return
= monthly_available_premium_pool
× monthly_deployment_ratio
× expected_retention_rate
- expected_monthly_tail_loss
- expected_monthly_cost
```

需要关注：

- required monthly premium；
- available monthly premium；
- opened monthly premium；
- premium coverage ratio；
- realized retention rate；
- monthly stop loss；
- monthly attribution；
- drawdown and recovery。

## 12. 因子分析的新要求

后续所有因子分析都必须回答一个问题：

```text
这个因子可能改善公式里的哪个变量？
```

不能只报告：

```text
IC 多高？
Q1-Q5 是否单调？
```

还要报告：

```text
它改善的是 Premium Pool、Deployment Ratio、Retention Rate、Tail / Stop Loss，还是 Cost / Slippage？
它适合用于合约层、P/C 侧、品种层、组合层，还是时间层？
它是排序因子、预算因子、过滤因子、风控因子，还是诊断因子？
```

### 12.1 因子到公式变量映射

| 因子/字段 | 主要改善变量 | 适用层级 | 典型用途 |
| --- | --- | --- | --- |
| `premium_yield_margin` | `Premium Pool`、`Deployment Ratio` | 合约层 | 找单位保证金权利金更厚的合约。 |
| `premium_to_stress_loss` | `Retention Rate`、`Tail / Stop Loss` | 合约层、品种层 | 避免权利金厚但压力亏损更厚。 |
| `premium_to_iv5_loss`、`premium_to_iv10_loss` | `Retention Rate`、`Tail / Stop Loss` | 合约层 | 衡量权利金是否覆盖升波冲击。 |
| `theta_vega_efficiency` | `Retention Rate` | 合约层 | 保留 theta 同时降低 vega 脆弱性。 |
| `b5_theta_per_gamma`、`b5_gamma_theta_ratio` | `Tail / Stop Loss` | 合约层、delta bucket | 防止用过高 gamma 换 theta。 |
| `b5_premium_to_tail_move_loss` | `Retention Rate`、`Tail / Stop Loss` | 合约层、品种层 | 判断权利金是否覆盖历史尾部不利移动。 |
| `b5_premium_per_capital_day` | `Premium Pool`、`Deployment Ratio` | 合约层、期限层 | 衡量资金占用效率。 |
| `b5_delta_bucket` | `Premium Pool`、`Retention Rate`、`Tail / Stop Loss` | 合约层、delta 梯队 | 检验 delta < 0.1 内部如何铺梯队。 |
| `b5_mom_20d`、`b5_trend_z_20d`、`breakout_distance` | `Retention Rate`、`Tail / Stop Loss` | P/C 侧 | 避免卖趋势突破方向。 |
| `put_skew`、`call_skew`、`risk_reversal` | `Premium Pool`、`Retention Rate` | P/C 侧 | 判断哪侧权利金是溢价还是真实尾部风险。 |
| `b5_atm_iv_mom_5d`、`b5_atm_iv_accel`、`b5_iv_reversion_score` | `Retention Rate` | 品种层、时间层 | 区分升波、降波、高位钝化。 |
| `b5_cooldown_*` | `Tail / Stop Loss` | 品种层、P/C 侧 | 降低重复止损，决定冷静期释放。 |
| `liquidity_score`、`volume`、`open_interest` | `Cost / Slippage`、`Deployment Ratio` | 合约层、品种层 | 衡量能否真实开仓和平仓。 |
| `tick_value_ratio`、`low_price_flag` | `Cost / Slippage`、`Tail / Stop Loss` | 合约层 | 防止低价 tick 离散和假止损。 |
| `product_premium_depth` | `Premium Pool` | 品种层 | 衡量某品种到底能提供多少可交易权利金。 |
| `product_premium_depth_per_stress` | `Premium Pool`、`Tail / Stop Loss` | 品种层 | 品种预算倾斜。 |
| `product_liquidity_capacity` | `Deployment Ratio`、`Cost / Slippage` | 品种层 | 防止厚权利金但无法退出。 |
| `tail_dependence`、`stop_cluster_corr` | `Tail / Stop Loss` | 组合层 | Tail-HRP 和组合预算。 |
| `effective_product_count`、`top5_stress_share` | `Tail / Stop Loss` | 组合层 | 判断组合是否真分散。 |

### 12.2 因子报告必须新增的栏目

以后因子报告表格必须至少包含：

| 字段 | 说明 |
| --- | --- |
| 因子名称 | 原始字段名或派生字段名。 |
| 因子族群 | premium、vega、gamma、trend、skew、liquidity、tail、cooldown、portfolio 等。 |
| 适用层级 | 合约层、P/C 侧、品种层、组合层、时间层。 |
| 改善公式变量 | `Premium Pool`、`Deployment Ratio`、`Retention Rate`、`Tail / Stop Loss`、`Cost / Slippage`。 |
| 使用方式 | 排序、预算倾斜、硬过滤、风控约束、诊断。 |
| IC/分层表现 | 对应标签下的 IC、Q1-Q5、累计 IC。 |
| 留存率影响 | 是否提高 premium retention。 |
| 止损影响 | 是否降低 stop rate / stop loss。 |
| 尾部影响 | 是否降低 worst bucket、tail loss、cluster loss。 |
| 交易代价 | 是否牺牲权利金池或流动性。 |

这样我们后面对 B5/B6/B7 的判断会更清晰：  
一个因子即使 IC 不最高，只要能稳定降低 `Tail / Stop Loss`，也可能值得进入风控层；一个因子即使能提高 NAV，如果主要靠牺牲 `Retention Rate` 或放大尾部聚合，也不一定值得采用。
