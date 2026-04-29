# S1 B4 因子角色化实验研究报告

文档日期：2026-04-29

研究对象：S1 纯卖权策略 B4a / B4b / B4c

对照基准：B1 流动性/OI 排序基准、B2C 权利金质量品种倾斜版本

样本区间：2022-01-04 至 2026-03-31，共 1215 个交易日

## 1. 执行摘要

本次 B4 系列实验的结论比较清晰：**B4 不是下一版主线，但它证明了部分因子“有用，只是用错了位置”。** 如果用新的 S1 收益拆解公式来判断：

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

B4 做对的是前两项：它显著提高了同等保证金水平下实际吃到的权利金池，也改善了一部分 vega 口径的单位权利金损耗。B4 做错的是第三项和第四项：它把合约排序推向了“更厚但更难留住”的权利金，导致留存率下降、gamma/止损尾部变厚、最大回撤和最差单日明显恶化。

核心数字如下：

| 版本 | 总收益 | 年化 | Sharpe | Calmar | 最大回撤 | 毛开仓权利金 | 权利金留存率 | S1 PnL/毛权利金 | 止损损耗/毛权利金 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | 15.20% | 2.98% | 1.53 | 1.19 | -2.49% | 784.10 万 | 18.84% | 20.83% | 44.06% |
| B2C | 17.14% | 3.33% | 1.69 | 1.32 | -2.52% | 812.99 万 | 20.47% | 22.48% | 43.00% |
| B4a | 15.68% | 3.07% | 1.13 | 0.86 | -3.55% | 1008.66 万 | 14.69% | 16.53% | 49.06% |
| B4b | 15.11% | 2.96% | 1.07 | 0.79 | -3.77% | 1038.76 万 | 13.63% | 15.53% | 49.34% |
| B4c | 15.20% | 2.98% | 1.10 | 0.79 | -3.79% | 1034.47 万 | 13.77% | 15.69% | 49.28% |

最重要的判断有三条：

- **B4 的“厚权利金”方向是对的。** B4a/b/c 的毛开仓权利金相对 B1 增加约 28.6%/32.5%/31.9%，但平均保证金使用率几乎不变，说明它不是简单加仓，而是在同等保证金下选择了更高权利金密度的合约。
- **B4 的“权利金质量”没有改善。** B4a/b/c 的留存率降到 14%-15% 区间，显著低于 B1 的 18.84% 和 B2C 的 20.47%。这意味着 B4 收了更多保险费，但承保质量更差，最终没有把更多权利金留下来。
- **B4 的尾部控制明显失败。** B4 的止损次数下降，但止损损耗占毛权利金比例上升到约 49%，最大回撤扩大到 -3.55% 至 -3.79%。这说明止损数量变少不等于尾部风险变小，B4 更像是把小额、分散的止损换成了少数更重的亏损事件。

因此，本报告建议：**不要直接采用 B4 作为交易版本；保留 B4 的“权利金池识别”和“vega 覆盖”因子，但必须拆回正确层级使用。** 更具体地说，`premium_to_iv10_loss`、`premium_to_stress_loss`、`premium_yield_margin` 适合做合约层排序或品种层预算诊断；`gamma_rent_penalty`、`breakeven_cushion`、`vol_of_vol` 不能只作为轻微扣分，必须进入尾部/止损预算和硬约束；产品侧倾斜不能只按厚权利金加预算，而要同时看留存率、止损簇、tail correlation 和退出容量。

## 2. 实验定义

B4 系列都继承 B1，即在 B1 的流动性/OI 排序、次月合约、delta 小于 0.1、止损 2.5x、低价与 ETF 过滤等基准逻辑上，增加因子角色化排序。

| 版本 | 相对 B1 的变化 | 研究目的 |
|---|---|---|
| B4a | 合约层硬过滤 + 合约层 B4 排序 | 检验同一品种/方向内，使用权利金质量、stress 覆盖、gamma rent、vomma 等指标排序是否改善交易质量。 |
| B4b | B4a + 产品/方向层预算倾斜 | 检验把 B4 因子从合约层扩展到品种/方向预算后，是否能提高组合收益。 |
| B4c | B4b + vol-of-vol 与 breakeven 惩罚 | 检验在 B4b 基础上增加波动率不稳定和安全垫惩罚，是否能控制尾部风险。 |

B4a 的核心权重为：`premium_to_iv10_loss` 30%、`premium_to_stress_loss` 25%、`premium_yield_margin` 20%、`gamma_rent` 15%、`vomma` 10%。B4b 增加产品/方向层倾斜，倾斜强度 0.35，底仓权重 0.50，幂次 1.25。B4c 增加 vol-of-vol 和 breakeven 相关惩罚。

这组实验的检验重点不是“哪个参数最好”，而是检验 B2/B3/B5 因子到底适合放在哪个决策层级。用新公式看，B4 本质上同时动了三个东西：合约层的 Premium Pool、产品/方向层的 Deployment Ratio，以及尾部风险项 Tail / Stop Loss。这个混合使用本身就容易把有效因子和错误约束搅在一起。

## 3. 图表深读：净值和超额路径

![B4 NAV and excess](../output/analysis_s1_b4_formula_research_20260429/01_b4_nav_excess_vs_b1.png)

这张图的上半部分比较 B1、B2C、B4a/b/c 的标准化净值，下半部分比较各版本相对 B1 的超额收益路径。它回答的是：B4 的最终结果是稳定超额，还是少数阶段的路径偶然。

从图上读数看，B2C 的超额路径最平滑，最终相对 B1 多出约 1.93 个百分点。B4a 最终只多出约 0.47 个百分点，且超额路径有明显回吐；B4b 相对 B1 低约 0.09 个百分点；B4c 几乎与 B1 持平。也就是说，B4 并非完全无效，但它没有形成稳定、可持续的超额。

期权卖方角度看，B4a 的短期超额说明“更厚权利金合约”在部分阶段确实能提高收入；但后续超额回吐说明这些权利金没有稳定留住。卖权策略最怕的不是某个月跑输，而是超额收益在尾部月份集中吐回，因为这意味着因子是在提高承保风险，而不是提高承保质量。

风险疑点在于：B4b/B4c 加了产品/方向预算倾斜和惩罚后，并没有明显优于 B4a，反而更接近或略弱于 B1。这说明 B4 的产品层倾斜没有把“厚权利金”转化为“高留存权利金”，可能只是把仓位推向了权利金厚但跳变、gamma 或 stop cluster 更重的品种。

下一步验证不应该继续调 B4 的权重，而应该拆开：合约层只检验同链条 strike 选择；品种层只检验可用权利金池和历史留存；组合层单独引入 tail correlation 和 stop cluster。否则我们会继续把不同层级的因子混成一个分数，看不出谁贡献超额、谁放大尾部。

## 4. 图表深读：回撤路径

![B4 drawdown](../output/analysis_s1_b4_formula_research_20260429/02_b4_drawdown_compare.png)

这张图比较各版本回撤路径。它比最终收益更重要，因为 S1 的目标不是单纯提高收益，而是在最大回撤可控的情况下提高卖权 carry。

从图上和统计表看，B1 最大回撤 -2.49%，B2C -2.52%，两者接近；B4a 扩大到 -3.55%，B4b -3.77%，B4c -3.79%。B4 系列的最差单日也显著恶化：B1 为 -1.17%，B2C 为 -1.04%，B4a 为 -1.96%，B4b/B4c 约为 -1.60%。

期权专家判断：B4 的问题不是“没赚钱”，而是用更厚的权利金换来了更厚的左尾。卖权策略里，权利金厚常常有两种含义：一是市场错误定价给了好保险费，二是市场正确反映了更高的跳变/gamma/流动性风险。B4 目前更像第二种。

B4c 原本加入了 vol-of-vol 和 breakeven 惩罚，但回撤并未改善，说明惩罚力度或所在层级不对。轻微扣分无法阻止高权利金高风险合约进入组合；如果这些指标确实对应尾部风险，它们应该进入 hard filter、contract lot cap 或 stress budget，而不是只参与同一个综合排序分。

下一步需要把“尾部风险惩罚”从候选评分中分离出来：合约层可以继续按权利金质量排序，但组合层要用最大单日 stress、stop cluster、tail correlation 和到期聚集来裁剪手数。

## 5. 图表深读：公式拆解

![B4 formula decomposition](../output/analysis_s1_b4_formula_research_20260429/03_b4_formula_decomposition.png)

这张图是本报告最核心的图。它按新公式拆解 B4：第一格是吃到的毛权利金池，第二格是权利金留存率，第三格是 theta、vega、gamma 对毛权利金的吞噬关系，第四格是净捕获、止损和费用。

第一格显示，B4a/b/c 毛开仓权利金分别为 1008.66 万、1038.76 万、1034.47 万，明显高于 B1 的 784.10 万和 B2C 的 812.99 万。这个结论是正面的：B4 因子确实能找到更厚的权利金池。

第二格显示，B4 的留存率明显下降。B1 的已平仓权利金留存率为 18.84%，B2C 为 20.47%，B4a 降到 14.69%，B4b 13.63%，B4c 13.77%。这说明 B4 的新增权利金大部分被后续止损、gamma 路径和估值损耗吃掉，没有转化为净收益。

第三格显示一个很有价值的信号：B4 的 vega loss / gross premium 反而低于 B1/B2C。B1 为 75.06%，B2C 为 74.12%，B4 系列降到约 55%。这意味着 B4 的部分 vega 覆盖思路是对的，尤其是 `premium_to_iv10_loss` 和 `theta_vega_efficiency` 这类指标不应被否定。

但同一张图也暴露了 B4 的关键失败：B4 的 theta / gross premium 从 B1 的 125.90%、B2C 的 125.35% 降到约 111%，gamma loss / gross premium 仍在 84% 左右，止损损耗 / gross premium 升到约 49%。也就是说，B4 并没有得到“更便宜的 theta”，而是得到“毛权利金更大、theta 效率更低、尾部更重”的组合。

下一步应把 B4 因子分成两组：一组保留为 vega 覆盖与权利金厚度识别，另一组转为 gamma/tail 风控。不要再让它们在一个综合分里互相抵消。

## 6. 图表深读：Greek 归因

![B4 Greek attribution](../output/analysis_s1_b4_formula_research_20260429/04_b4_greek_attribution_compare.png)

这张图按 delta、gamma、theta、vega、residual 拆解各版本累计收益。它回答的是：B4 的收益改善或恶化，究竟来自卖权应赚的 theta/vega，还是来自方向、gamma、residual。

从累计数看，B4 的 theta 绝对值更高：B1 约 987.17 万，B2C 1019.06 万，B4a/b/c 约 1122.30 万、1149.96 万、1147.46 万。这和 B4 收到更多权利金一致。但 B4 的 gamma 绝对亏损也更重：B1 约 -671.10 万，B2C -692.15 万，B4a/b/c 扩大到 -848.28 万、-877.05 万、-867.25 万。

vega 维度反而是 B4 的亮点。B1 vega 约 -588.52 万，B2C -602.62 万，B4a/b/c 约 -562.71 万、-571.78 万、-570.21 万。B4 在更高权利金规模下没有扩大 vega 绝对损耗，说明它对 IV shock 覆盖率的处理有可取之处。

但 S1 的目标不是只控制 vega，而是同时保住 theta 并控制 gamma。B4 的 gamma 恶化足以抵消 vega 改善和 theta 增量，所以最终 Sharpe 和 Calmar 都下降。换句话说，B4 是“vega 方向有进步，gamma/tail 方向退步”。

报告结论因此不能简单写成“B4 无效”。更准确的结论是：B4 的 vega 因子适合保留，但必须引入更强的 gamma rent、最大不利移动、DTE/gamma bucket、止损跳价和 tail correlation 约束，否则会把 vega 风险换成 gamma 风险。

## 7. 图表深读：保证金与权利金部署

![B4 deployment](../output/analysis_s1_b4_formula_research_20260429/05_b4_deployment_margin_premium.png)

这张图比较平均/峰值保证金和每日开仓权利金流。它用于区分 B4 的收益变化到底是因为仓位变大，还是因为同等仓位下选择了更高权利金密度。

平均保证金使用率几乎没有差别：B1 48.42%，B2C 48.42%，B4a/b/c 约 48.38%。这排除了“B4 只是更激进加仓”的解释。B4 的毛权利金提升来自合约选择和权利金密度，而不是总保证金上限放松。

这点对研究很重要。它说明我们前面关于“产品权利金深度”和“合约权利金质量”的思路是有价值的：在保证金不变的情况下，确实可以通过排序拿到更多毛保险费。

但这张图必须和留存率一起看。B4 每个开仓日收的权利金更高，但净捕获率更低，所以 Deployment Ratio 改善没有转化为 Retention Rate 改善。卖权不是卖得越多越好，而是要卖“足够厚且能留下”的保险费。

下一步建议把 Deployment 分成两层：第一层是可用权利金池 `available premium pool`，第二层是开仓吃掉比例 `opened premium / available premium`。B4 目前只证明它能提高 opened premium，没有证明它提高了可留存的 available premium。

## 8. 图表深读：P/C 结构

![B4 P/C structure](../output/analysis_s1_b4_formula_research_20260429/06_b4_pc_structure.png)

这张图比较 Put/Call 的毛权利金占比和平均 Call 手数占比。它回答的是：B4 是否通过改变方向暴露来获得收益，或者把策略变成了隐含方向仓。

从图上看，B4 并没有明显改变 P/C 方向结构。B1 的 Put 毛权利金占比为 56.23%，B2C 为 55.80%，B4a/b/c 分别约 55.36%、55.51%、54.82%。平均 Call lot share 也没有发生决定性变化。

这意味着 B4 的主要问题不在“长期更多卖 Put 或更多卖 Call”，而在同一 P/C 框架下选到了不同质量的合约和品种。换句话说，B4 的失败不是方向偏移导致的，而是权利金质量、gamma、止损和产品预算层级的问题。

这也解释了为什么后续 P/C 因子不能直接套用 B4 结果。趋势/动量、skew、上下尾部不利移动应单独做 P/C 侧 full shadow 标签，而不是从 B4 的交易结果里反推。

下一步 P/C 侧仍应围绕“哪一边的权利金更容易留住”来设计，而不是简单让 Put 或 Call 固定占优。长期偏 Put 本质上是看涨所有品种，长期偏 Call 本质上是看跌所有品种，这两者都不是纯卖波的稳健主线。

## 9. 图表深读：止损和尾部

![B4 stop and tail](../output/analysis_s1_b4_formula_research_20260429/07_b4_tail_stop_compare.png)

这张图表面上可能让人误判：B4 的止损次数比 B1 少很多。B1 止损 4528 次，B2C 4391 次，B4a/b/c 约 3019-3041 次。

但真正关键的是右侧尾部强度。B4 的止损损耗占毛权利金比例升到约 49%，而 B1 是 44.06%，B2C 是 43.00%。这说明 B4 不是减少了尾部，而是减少了交易数量和止损次数，同时让单次止损更重。

期权卖方角度看，这是非常典型的“止损次数幻觉”。如果策略卖得更少、更厚、更集中，止损次数自然下降，但每次止损都可能更痛。我们真正要优化的是 `expected stop loss / premium` 和 `left-tail loss / NAV`，不是单纯 stop count。

B4 的 stop count 降低也可能来自合约筛选后开仓笔数下降：B4a open_sell 为 10156 笔，B1 为 14220 笔。但毛权利金反而更高，说明每笔风险密度显著上升。这种结构如果没有更强的 stress budget，很容易把日常小亏少亏变成尾部大亏。

下一步必须把止损研究从“次数”升级成“止损质量”：止损前权利金倍数、止损成交滑点、止损后是否继续不利、是否快速回归、同品种冷静期是否有效、同板块是否簇拥触发。

## 10. 图表深读：月度超额

![B4 monthly excess](../output/analysis_s1_b4_formula_research_20260429/08_b4_monthly_excess_vs_b1.png)

这张图比较各版本相对 B1 的月度超额收益。它用于判断超额是否稳定，还是集中在少数月份。

B2C 在 51 个月中有 29 个月跑赢 B1，月均超额约 0.033 个百分点，超额合计约 1.67 个百分点；B4a 有 30 个月跑赢，但月均超额只有约 0.010 个百分点，且最差月相对 B1 跑输约 1.34 个百分点；B4b/B4c 的月均超额几乎为 0。

这说明 B4a 的胜率并不差，但左尾月份抹掉了大部分日常优势。对卖权策略来说，这比低胜率更危险：它容易让研究者被多数月份的小幅改善吸引，忽略少数月份的损耗会把长期复利质量拉低。

几个最差月值得重点复盘。B4a 在 2026-03、2023-04、2025-04 相对 B1 明显恶化；B4b/B4c 在 2026-03、2023-03/04 等月份也出现明显跑输。这些月份应作为下一轮 tail audit 的重点，而不是只看全样本平均。

下一步要把 B4 因子按月份和环境切片：在 falling vol、normal vol、rising vol、high vol 中分别看权利金留存率、gamma loss 和 stop loss。如果 B4 只在少数平稳月份有效，而在升波/趋势月份扩大亏损，它就不能作为全环境主排序。

## 11. 图表深读：品种权利金集中度

![B4 product premium](../output/analysis_s1_b4_formula_research_20260429/09_b4_product_premium_concentration.png)

这张图比较各版本 Top 产品的毛权利金贡献。它回答的是：B4 的权利金增加来自更广泛的产品池，还是来自少数高权利金品种集中。

从表格和图上看，B4 的产品结构相对 B1/B2C 有明显变化。B1 前几大毛权利金来源包括 M、SR、CF、I、TA、C、AG、RB；B4 中 M、I、SR、CF、AG、CU、TA、MO 的权重更突出。其中 I、CU、MO 等在 B4 中的权利金贡献显著抬升。

这说明 B4 的产品侧变化不是随机噪声。它确实让组合向更高权利金密度的产品移动。问题在于，这种移动没有经过独立的产品层留存率和尾部聚合验证。

从公式看，产品层不能只看 `product_premium_pool`，还要看：

```text
product_expected_pnl
= product_premium_pool
× product_deployment_ratio
× product_retention_rate
- product_stop_tail_loss
- product_cost_slippage
```

如果某个产品权利金很厚，但 stop cluster、跳价、gamma、退出容量不好，它应该只是“高毛保费产品”，不是“高预算产品”。这也是 B4b/B4c 没有改善的根本原因：产品倾斜很可能把预算给了权利金厚但留存差的产品。

下一步产品层应单独构建 `premium_depth × retention × tail_beta × exit_capacity` 的预算评分，并用 full shadow 的 product-level 标签验证，而不是直接沿用合约层分数。

## 12. B4 哪些地方可能对了

第一，B4 证明了“权利金池厚度”可以被因子识别。它在保证金使用率几乎不变的情况下，提高了约 30% 的毛权利金，这说明 `premium_yield_margin`、`premium_to_iv10_loss`、`premium_to_stress_loss` 等因子确实能影响 Premium Pool 和 Deployment Ratio。

第二，B4 对 vega 的控制不是失败的。虽然 B4 没有带来更好的最终绩效，但其 vega loss / gross premium 明显优于 B1/B2C，绝对 vega 损耗也没有随毛权利金扩张而增加。这说明 B3 中关于 forward vega、IV shock coverage、theta/vega efficiency 的研究方向值得保留。

第三，B4 的硬过滤降低了低质量小票交易数量。B4 的开仓笔数下降、手续费占毛权利金比例下降，说明低价、摩擦、细碎合约过滤有实盘意义。这类因子更适合做执行层 hard filter，而不是收益排序。

第四，B4 帮我们确认了“综合分不是答案”。它让我们看见：同一个因子在合约层可能有效，在产品层可能无效；用于排序可能有效，用于预算倾斜可能有害。这正是下一步 full shadow 因子库要解决的问题。

## 13. B4 哪些地方可能错了

第一，B4 把“权利金更厚”误当成“权利金质量更高”。从结果看，B4 的毛保费大幅增加，但留存率明显下降。卖权研究不能只最大化收进来的 premium，而要最大化留得住的 premium。

第二，B4 的 gamma/tail 惩罚不足。B4 的 vega 改善没有转化为净值改善，主要被更大的 gamma loss 和止损损耗抵消。说明 `gamma_rent_penalty` 在当前权重和形式下不够强，可能应进入 stress budget 或 contract lot cap。

第三，B4b/B4c 的产品侧预算倾斜可能错位。产品预算不应该直接继承合约层权利金质量分；产品层要单独回答这个品种是否能承载更大预算，包括历史留存、stop cluster、尾部相关性、退出容量、保证金冲击。

第四，B4c 的 vol-of-vol 与 breakeven 惩罚没有实质降低回撤，说明它们要么信号方向需要重检，要么惩罚方式太轻。对于尾部风险，轻微扣分通常不够，应该以“不过线不加仓”或“触发后降手数”的方式进入。

第五，B4 当前没有充分区分合约层、P/C 侧、品种层和组合层。合约排序的好坏不能直接推出品种预算的好坏；P/C 侧的趋势和 skew 也不能被合约 premium score 替代；组合层的 tail dependence 更不能由单合约分数自动解决。

## 14. 与 B2C 的差距

B2C 仍是当前更优的研究主线。它相对 B1 提高了收益，同时几乎没有扩大最大回撤，并且留存率从 18.84% 提高到 20.47%。这说明 B2C 更像是在改善 Retention Rate，而不是单纯扩大 Premium Pool。

B4 相比 B2C 的差距不是权利金不够，而是权利金过多但质量不足。B4b/B4c 的毛权利金比 B2C 多约 225 万左右，但最终收益反而低约 1.9-2.0 个百分点，说明新增的毛权利金被 tail/gamma/stop 吃掉了。

从策略哲学上看，B2C 更接近“精选可留存的权利金”，B4 更接近“追求更厚的权利金”。卖方策略要向乐得画像靠近，核心不是收更多，而是在降波/稳定环境敢于多收，在升波/跳变/趋势环境少收或不收。

因此，B4 因子不应该替代 B2C，而应该拆出来补充 B2C：

- 合约层：用 B4 的 `premium_to_iv10_loss` 和 `premium_to_stress_loss` 选择同品种同方向下更优 strike。
- 品种层：继续以 B2C 的 premium quality/retention 思路做预算倾斜。
- 风控层：用 B4 暴露出来的 gamma/tail 问题，引入更强的 tail budget 和止损质量监控。
- 组合层：用 B6 Tail-HRP 管理板块、尾部相关性和 stop cluster。

## 15. 后续实验建议

本报告建议下一步不要做“B4d 调权重”，而是做结构拆分实验。

### 实验 1：B4 合约层独立验证

保持 B1/B2C 的品种预算不变，只在同一 `product + side + expiry` 内用 B4 合约层分数选 strike。目标是验证 B4 的合约排序是否真的改善 `contract_expected_pnl`：

```text
contract_expected_pnl
= net_premium_cash
× contract_retention_probability
- expected_stop_loss
- transaction_cost
```

如果这一层有效，应表现为同等品种预算下留存率提高、gamma loss 不恶化、stop loss / gross premium 下降。

### 实验 2：B4 vega 因子保留，gamma/tail 因子转硬约束

保留 `premium_to_iv10_loss`、`theta_vega_efficiency`、IV shock coverage 作为排序正因子；把 `gamma_rent_penalty`、最大不利移动、安全垫、DTE/gamma bucket 变成手数上限或硬过滤。目标是保留 B4 的 vega 改善，同时阻止 gamma 损耗扩大。

### 实验 3：产品层预算重做

产品预算不再使用合约层综合分，而改成：

```text
product_budget_score
= product_premium_depth
× product_retention_rate
× exit_capacity
- stop_cluster_penalty
- tail_beta_penalty
- margin_shock_penalty
```

这个实验应基于 B5 full shadow 的 product-level 标签，而不是只看已成交订单。

### 实验 4：止损质量标签

把止损次数拆成止损质量，包括 stop overshoot、止损后继续不利、止损后回归、同品种重复止损、同板块簇拥止损。B4 暴露出的问题是“止损次数少但每次更重”，因此下一版必须看止损严重度，而不是 stop count。

### 实验 5：Tail-HRP 组合预算

在产品层确定高质量权利金池后，用尾部相关性、stop cluster、同到期 gamma、板块 stress loss 做组合预算裁剪。目标不是降低收益，而是防止 B4 这种“单合约看起来厚，组合左尾变厚”的问题。

## 16. 结论

B4 的研究价值很高，但交易结论不支持直接上线。它告诉我们两件事：

第一，**我们确实可以找到更厚的权利金池。** 这对 S1 达成更高年化目标非常重要，因为在 50% 保证金基准下，如果毛权利金池太薄，6% 年化目标很难靠参数微调实现。

第二，**更厚权利金如果留不住，就是更重的承保风险。** B4 的留存率下降、回撤扩大、止损损耗占比提高，说明当前 B4 把一部分因子放错了层级。它不是“因子没用”，而是“因子用途没有拆清楚”。

用新的公式总结：

| 公式项 | B4 结果 | 判断 |
|---|---|---|
| Premium Pool | 明显提高 | 方向对，值得保留。 |
| Deployment Ratio | 同等保证金下吃到更多权利金 | 方向对，但要区分可留存权利金与毛权利金。 |
| Retention Rate | 明显下降 | 主要失败点。 |
| Tail / Stop Loss | 止损次数下降但尾部强度上升 | 主要失败点。 |
| Cost / Slippage | 费用占毛权利金下降 | 执行层过滤有效，但不是核心 alpha。 |

所以，B4 后续不应作为一个“整包版本”继续优化，而应拆成因子库中的三个模块：合约层权利金/vega 覆盖排序、产品层 premium depth 诊断、组合层 tail/gamma 风险惩罚。下一轮实验应围绕“提高 Premium Pool 的同时不牺牲 Retention Rate”来设计，而不是继续追求更高毛权利金。

## 附录：数据与图表文件

本报告使用的统一分析包位于：

```text
output/analysis_s1_b4_formula_research_20260429/
```

核心数据表：

- `b4_formula_summary.csv`
- `b4_monthly_returns.csv`
- `b4_product_premium_top.csv`

核心图表：

- `01_b4_nav_excess_vs_b1.png`
- `02_b4_drawdown_compare.png`
- `03_b4_formula_decomposition.png`
- `04_b4_greek_attribution_compare.png`
- `05_b4_deployment_margin_premium.png`
- `06_b4_pc_structure.png`
- `07_b4_tail_stop_compare.png`
- `08_b4_monthly_excess_vs_b1.png`
- `09_b4_product_premium_concentration.png`
