# S1 B2 权利金质量排序与风险预算分配研究口径

## 1. 文档定位

本文档定义 S1 策略在 B0、B1 之后的下一版研究口径：B2。

B2 的核心不是增加一组新的黑名单过滤，也不是把策略重新改成趋势预测、降波预测或复杂 alpha 模型。B2 只回答一个更基础、也更接近卖权本质的问题：

```text
在同样只卖次月、同样低 Delta、同样可交易的候选合约中，
哪一笔权利金对我们承担的 vega、gamma、尾部亏损、交易成本补偿得更充分？
```

因此，B2 的正式定位是：

```text
B2 = B1 + 权利金质量排序 + 基于质量分数的风险预算分配
```

必须强调：

```text
B2 以排序和预算分配为主，不以漏斗式删除为主。
```

除 B0/B1 已经存在的硬约束外，B2 不应因为某个质量指标偏低就直接把合约或品种永久剔除。低质量候选应当获得更低排序和更少风险预算；高质量候选应当获得更高排序和更多风险预算，但仍然受总保证金、单品种、单方向、压力亏损和组合风险上限约束。

## 2. 研究动机

B0 的目的是建立一个足够朴素、足够透明的卖权基准：全品种、次月、低 Delta、双侧卖权、目标保证金约 50%、2.5x 权利金止损、持有到期或止损退出。

B1 在 B0 基础上只加入成交量和持仓量排序，目的是让开仓更接近真实可交易合约，而不是让代码顺序、合约代码顺序或单纯 Delta 接近度决定开仓优先级。

B2 进一步处理 B0/B1 暴露出的核心问题：

- 策略能收取较多 theta，但 vega 和 gamma 经常吞噬大部分权利金。
- 单纯提高总权利金或保证金利用率，可能同步提高 short vega、short gamma 和尾部风险。
- 如果只看历史 IV 是否下降、RV 是否下降，会把策略推向弱预测框架；这不够稳健。
- 卖权更应该被视为承保业务：当前收到的保险费是否足够补偿承担的波动率风险、路径风险和尾部风险。

因此，B2 不直接预测未来，而是用当前可见数据衡量权利金质量：

```text
权利金质量 = 收到的净权利金 / 所承担的波动率风险、路径风险、尾部风险、交易摩擦
```

### 2.1 为什么 B2 不以预测为主

在卖权策略中，最容易犯的错误是把问题表述为：

```text
未来 IV 会不会下降？
未来标的会不会不涨不跌？
```

这类问题当然重要，但它们很快会把策略推向预测模型、趋势模型或 regime 模型。而预测模型的有效性高度依赖样本区间、训练目标、特征稳定性和未来市场结构是否延续。对 S1 当前阶段而言，如果先把 B2 做成预测模型，会带来三个问题：

1. 解释性下降，难以判断收益来自卖权风险溢价，还是来自隐含的方向预测。
2. 容易形成过拟合漏斗，尤其是在品种、期限、Delta、IV/RV、趋势、流动性同时筛选时。
3. 即使预测正确，也未必说明这张具体期权的权利金足够补偿其 vega、gamma 和尾部风险。

因此，B2 的出发点不是预测未来，而是做横截面定价比较：

```text
在同一交易日、同一次月、同一低 Delta 框架内，
市场给不同品种、不同执行价、不同侧别支付的权利金，
相对于各自承担的风险是否有明显差异？
```

如果存在这种差异，策略可以不预测未来方向，只通过更合理地分配预算来提高卖权组合质量。

### 2.2 卖权策略的承保视角

卖权更接近保险承保业务，而不是普通多空交易。

对卖方来说，开仓时收到的权利金类似保险费；未来可能出现的不利标的波动、IV 上升、skew 恶化、流动性折价和跳空风险类似赔付风险。一个承保业务是否值得做，核心不是“这份保险费绝对金额高不高”，而是：

```text
保险费 / 潜在赔付风险 是否足够高？
```

映射到 S1：

```text
净权利金 / IV冲击亏损
净权利金 / 压力情景亏损
净权利金 / 保证金占用
净权利金 / 交易摩擦
```

才是比单纯 `option_price`、`theta` 或 `margin_yield` 更接近本质的指标。

这也解释了为什么 B1 的流动性排序可能改善净值，但仍不能证明策略质量真正改善。B1 解决的是“更容易成交、更少脏价格”的问题；B2 要解决的是“成交后这笔保险费是否收得划算”的问题。

### 2.3 从期权收益分解推导权利金质量

对单个期权价值做局部展开：

```text
dV
≈ Delta * dS
 + 0.5 * Gamma * dS^2
 + Vega * dIV
 + 0.5 * Vomma * dIV^2
 + Vanna * dS * dIV
 + Theta * dt
```

对卖方而言，持仓 PnL 约为：

```text
short_option_pnl
≈ -dV - transaction_cost
```

在低 Delta 卖权框架下，`Delta * dS` 的一阶方向风险被限制在较小范围内，但并没有消失。真正决定尾部亏损的是：

```text
- 0.5 * Gamma * dS^2
- Vega * dIV
- 0.5 * Vomma * dIV^2
- Vanna * dS * dIV
- transaction_cost
```

S1 的目标不是最大化 theta，而是最大化：

```text
theta 收入 - gamma 路径成本 - vega/volga 冲击成本 - vanna 联动成本 - 交易摩擦
```

因此，权利金质量必须同时覆盖以下风险项：

- realized movement 造成的 gamma 成本。
- IV 上行造成的 vega 成本。
- IV 大幅上行时 vega 本身扩张造成的 vomma 成本。
- 标的不利移动和 IV 同时上行造成的 vanna / spot-vol coupling 成本。
- 手续费、滑点和流动性退出成本。

这就是为什么 B2 不应只看 `theta / cash_vega`。该指标有用，但只是一阶局部效率；它不能解释 IV 大幅上行、标的跳空和 skew 重新定价。B2 需要直接引入 `premium / IV shock loss` 和 `premium / stress loss`。

### 2.4 从开仓权利金推导最低补偿要求

设单合约开仓净权利金为 `C`。从经济意义上，一笔卖权交易合理的最低要求不是 `C > 0`，而是：

```text
C
> expected_transaction_cost
+ expected_gamma_cost
+ expected_vega_cost
+ tail_risk_charge
+ liquidity_exit_charge
+ required_risk_premium
```

其中：

- `expected_gamma_cost` 可以由 RV、盈亏平衡保护垫和不利方向移动情景近似。
- `expected_vega_cost` 可以由 IV shock 重估和 cash vega 近似。
- `tail_risk_charge` 可以由压力情景亏损和短执行价触及风险近似。
- `liquidity_exit_charge` 可以由成交量、持仓量、价差和手续费近似。
- `required_risk_premium` 是承担短尾部风险所要求的额外补偿。

B2 的指标体系，本质上就是把这个不等式拆成可计算代理变量：

```text
IV-RV carry                -> 市场隐含波动是否高于近期实际波动成本
breakeven cushion          -> 收到权利金后离真正亏损有多远
premium / IV shock loss    -> 权利金能覆盖多少 IV 上行损失
premium / stress loss      -> 权利金能覆盖多少组合不利情景损失
theta / cash_vega          -> 每单位 vega 暴露能收多少日度 theta
fee / premium              -> 交易摩擦是否吞噬权利金
```

### 2.5 为什么使用排序和预算分配，而不是漏斗过滤

B2 明确不设计成多层漏斗，原因有四个。

第一，权利金质量是连续变量，不是二元变量。一个合约的 IV-RV carry 偏低，并不代表它一定不能做；它可能有更好的盈亏平衡保护垫、更低的交易成本或更低的 stress loss。把单一指标作为硬门槛，会丢失横截面信息。

第二，硬过滤容易过拟合。若同时设置 IV-RV、premium yield、stress loss、vega shock、gamma、liquidity 等多个阈值，最终表现可能来自参数组合对历史样本的偶然适配，而不是来自稳定的卖权经济逻辑。

第三，硬过滤会造成交易断层。卖权组合需要一定分散度和持续承保能力。若某些日子大部分合约被过滤，组合保证金和风险预算会大幅波动，反而引入路径不稳定。

第四，预算分配更符合承保业务。承保不是只给某些风险“承保/不承保”的二元选择，而是对不同风险收取不同价格、配置不同额度。B2 应该让高质量权利金获得更多额度，让低质量权利金获得较少额度。

因此，B2 的原则是：

```text
指标用于排序，不用于大规模剔除；
分数用于预算，不用于绝对判断；
组合约束用于控制尾部，不让高分合约无限放大。
```

### 2.6 次月约束下 B2 仍然有效的原因

本项目当前约束是只卖次月。这个约束不变时，B2 不能通过期限选择去优化 vega duration，例如不能简单从远月切到近月，也不能通过跨期限结构减少 vega。

但即使在同一次月中，权利金质量仍然存在显著横截面差异：

- 不同品种的 IV-RV carry 不同。
- 同一品种不同执行价的 skew 定价不同。
- 同一 Delta 附近的合约，权利金、盈亏平衡保护垫和 gamma 暴露不同。
- Put 和 Call 对不同方向尾部风险的补偿不同。
- 流动性不同导致实际可留存权利金不同。
- 商品、ETF、股指期权的跳动、保证金、手续费和流动性成本不同。

因此，B2 不需要改变次月规则，也能在同一到期层内做更优排序。

### 2.7 B2 的可检验预测

如果 B2 的权利金质量定义有效，则在回测结果中应观察到以下现象：

```text
高质量分数组合相对低质量分数组合：
1. premium retained ratio 更高；
2. vega_loss / gross_open_premium 更低；
3. gamma_loss / gross_open_premium 不显著更高；
4. 止损概率更低或止损后滑点更低；
5. stress day 损失占 NAV 比例更低；
6. 收益不是单纯来自某一侧 Put 或 Call 的方向行情；
7. 分层表现跨年份、跨品种大类仍具有稳定性。
```

如果这些现象不成立，则说明 B2 指标只是看起来合理，并没有捕捉到真实可交易的权利金质量。

## 3. B2 保持不变的 B0/B1 规则

B2 继续保持以下规则不变：

- 全品种扫描。
- 只卖次月合约。
- 只卖虚值期权。
- 卖腿绝对 Delta 上限仍为 `abs(delta) <= 0.10`。
- Put 和 Call 都参与，不预设长期偏多或偏空。
- 目标总保证金使用率仍以 50% 为基准。
- 真实手续费表、真实保证金率表和当前保证金公式继续沿用。
- 止损仍为开仓权利金的 `2.5x`，并保留盘中跳价确认逻辑。
- 不设置盈利止盈。
- 持有到期或触发止损退出。
- B1 的成交量、持仓量排序仍作为可交易性优先级。

B2 不改变“卖次月低 Delta 权利金”这个基准框架；它只改变候选合约的优先级和风险预算权重。

## 4. B2 的研究假设

B2 的核心假设可以表述为：

```text
在相同次月、相同 Delta 上限和相似可交易性条件下，
权利金质量更高的合约，其长期风险调整后表现应优于单纯按流动性或 Delta 接近度选择的合约。
```

这里的“优于”不只指收益更高，还包括：

- vega 亏损占总开仓权利金比例更低。
- gamma 亏损占总开仓权利金比例更低。
- 权利金留存率更高。
- 单日尾部亏损更小。
- 最大回撤不明显恶化。
- 同等保证金下的 Calmar、Sharpe、Sortino 改善。

## 5. 理论依据与拟引用文献

### 5.1 波动率风险溢价

Carr and Wu 对 variance risk premium 的研究将波动率卖方收益来源表述为风险中性预期方差与实际实现方差之间的差异。该框架支持用 `IV^2 - RV^2` 或类似指标衡量市场是否为波动风险支付了足够保险费。

拟引用：

- Carr, P. and Wu, L. (2009). Variance Risk Premia. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=577222

对 B2 的启发：

```text
如果卖方长期收益来自波动率风险溢价，
那么开仓时至少应确认当前隐含波动率相对近期实际波动率存在正补偿。
```

但该文献不能直接推出“IV 高就应该卖”。原因是：

- 风险溢价是组合和长期统计意义上的，不保证单笔交易赚钱。
- IV-RV spread 可能是对未来事件、跳空或尾部风险的合理补偿。
- 期权卖方承担的是非线性尾部风险，不能只看平均 carry。

因此，B2 只能把 `IV^2 - RV^2` 作为质量评分的一部分，而不能作为唯一开仓条件。

### 5.2 Delta-hedged option gains 与负波动风险溢价

Bakshi and Kapadia 从 delta 对冲期权组合收益角度研究市场波动风险溢价，说明期权卖方并不是简单赚 theta，而是在承担系统性波动风险后获得补偿。

拟引用：

- Bakshi, G. and Kapadia, N. (2003). Delta-Hedged Gains and the Negative Market Volatility Risk Premium. Review of Financial Studies.

对 B2 的启发：

```text
即使方向风险被 delta 对冲或通过低 Delta 约束弱化，
期权组合仍然暴露于波动率风险、gamma 路径风险和高阶凸性风险。
```

这支持 B2 引入：

- `premium / IV shock loss`
- `theta / cash_vega`
- `premium / stress loss`
- `gamma_rent_penalty`

该文献也提醒我们，不能把低 Delta 误解为低风险。低 Delta 降低一阶方向暴露，但不消除 volatility risk premium 背后的系统性风险。

### 5.3 期权期望收益与尾部风险补偿

Coval and Shumway 研究不同期权头寸的期望收益，指出期权收益与系统性风险、尾部状态和非线性风险暴露相关。卖权收益不能只用胜率和平均权利金解释。

拟引用：

- Coval, J. D. and Shumway, T. (2001). Expected Option Returns. Journal of Finance.

对 B2 的启发：

```text
卖权期望收益来自承担不对称风险，
因此衡量一笔交易不能只看平均收益或胜率，
必须看该权利金对尾部风险的补偿是否足够。
```

这支持 B2 把 `premium / stress loss` 作为核心指标之一，并在报告中强制观察：

- 最大亏损是否吃掉长期 theta。
- 高分组是否真的降低尾部亏损。
- 收益是否过度集中于少数非尾部年份。

### 5.4 Put 期权昂贵性与尾部保险需求

Bondarenko 讨论 put options why expensive 的现象。该文献提醒我们，put 昂贵不等于可以无脑卖出；昂贵本身可能是市场对下跌尾部风险的风险厌恶补偿。

拟引用：

- Bondarenko, O. (2014). Why are Put Options So Expensive? Quarterly Journal of Finance.

对 B2 的启发：

```text
Put 昂贵可能是机会，也可能只是尾部风险的合理价格。
```

因此，B2 不能因为 Put 权利金更厚就长期偏向 Put。Put 侧必须额外比较：

- `downside_RV`
- 下跌压力情景损失
- put skew 是否过陡
- Put 权利金留存率是否真的高于 Call
- Put 止损是否集中发生在系统性下跌期

这也回应当前研究中的一个重要担忧：如果长期更多卖 Put，策略可能隐含变成看多所有品种，而不是纯粹卖波动率。

### 5.5 隐含风险中性分布

Figlewski 关于从期权价格估计隐含风险中性分布的研究，支持从整条期权链中观察市场对尾部概率和尾部价格的定价，而不是只看某一个合约的 Delta 或 IV。

拟引用：

- Figlewski, S. (2010). Estimating the Implied Risk Neutral Density for the U.S. Market Portfolio.

对 B2 的启发：

```text
单个合约 Delta 不是完整风险度量。
同样 Delta 的合约，可能处在完全不同的 skew、tail price 和曲面环境中。
```

因此，B2 在同一低 Delta 约束下仍需比较：

- 盈亏平衡保护垫。
- 同侧相邻执行价权利金形状。
- 同一到期内尾部权利金是否异常便宜或异常昂贵。
- `premium / stress loss` 是否与 Delta 排序一致。

如果后续数据条件允许，可以进一步从次月期权链近似 risk-neutral tail probability，再与历史触及概率比较，形成更严格的 `tail_premium_ratio`。

### 5.6 实务书籍

Natenberg 和 Sinclair 对波动率交易、隐含波动率、实际波动率、skew、期限结构和交易摩擦的讨论，可作为 B2 指标设计的实务依据。

拟引用：

- Natenberg, S. Option Volatility and Pricing.
- Sinclair, E. Option Trading: Pricing and Volatility Strategies and Techniques.

对 B2 的启发：

```text
实盘波动率交易不是只比较 IV 水平，
而是同时比较 IV/RV、skew、期限、Greeks、成交成本和持仓后的调整风险。
```

在 B2 中，这对应：

- 同一次月内比较 skew 和执行价质量。
- 使用成本调整后的净权利金。
- 使用流动性作为摩擦惩罚，而不是只作为排序 tie-breaker。
- 把 vomma、vanna 等高阶风险通过情景重估吸收，而不是强行依赖精确解析 Greek。

### 5.7 PutWrite 基准

Cboe PutWrite 指数是长期卖出指数 put 并以现金抵押的经典基准，可作为“卖权不是单笔预测，而是系统性收取保险费”的公开基准参考。

拟引用：

- Cboe S&P 500 PutWrite Index Methodology.

对 B2 的启发：

```text
卖权策略可以作为系统性风险溢价策略存在，
但其长期表现高度依赖执行口径、保证金、现金抵押、换仓频率和尾部风险控制。
```

这提醒 B2 回测必须固定：

- 真实手续费。
- 真实保证金率。
- 次月定义。
- T 日信号、T+1 开仓。
- 与 B0/B1 完全相同的回测区间。

否则无法判断 B2 改善来自权利金质量，还是来自口径差异。

### 5.8 文献到 B2 指标的映射

| 文献/理论 | 关注问题 | B2 对应指标 | 使用边界 |
| --- | --- | --- | --- |
| Carr and Wu | 隐含方差与实现方差差异 | `variance_carry`, `iv_rv_ratio` | 只能作为长期风险溢价代理，不能单独开仓 |
| Bakshi and Kapadia | delta 对冲后仍存在波动风险溢价 | `premium / IV shock loss`, `theta / cash_vega` | 一阶 Greek 不足以覆盖 IV 大幅跳升 |
| Coval and Shumway | 期权期望收益与系统性尾部风险 | `premium / stress loss` | 高收益可能只是承担更大尾部风险 |
| Bondarenko | Put 昂贵性与尾部保险需求 | Put 侧 downside RV、put stress coverage | Put 贵不等于 Put 便宜 |
| Figlewski | 期权链隐含尾部分布 | `breakeven_cushion`, `tail_premium_ratio` | 精确 RND 需要曲面平滑，初期用代理指标 |
| Natenberg / Sinclair | 波动率交易实务 | skew、成本、流动性、Greeks 综合评分 | 实务指标必须防止过拟合 |
| Cboe PutWrite | 系统性卖权基准 | B0/B1/B2 横向对照 | 指数 PutWrite 不等同于多品种商品/ETF/股指组合 |

### 5.9 B2 的理论边界

B2 的理论基础并不意味着“高质量分数一定赚钱”。它只意味着：

```text
在卖权风险不可避免的前提下，
我们试图选择单位风险补偿更充分的合约，
并把更多风险预算分配给补偿更充分的合约。
```

B2 无法解决的问题包括：

- 系统性极端行情下所有卖权同时亏损。
- IV/RV 指标在结构突变时失效。
- 流动性突然消失导致理论止损无法成交。
- 数据源报价异常或 Greeks 估算错误。
- 交易所保证金临时上调或品种制度变化。

因此，B2 必须与组合层风险上限、压力测试和后续报告归因共同使用。

## 6. B2 权利金质量指标体系

B2 指标全部使用 T 日收盘后已知数据，为 T+1 日交易排序和预算分配服务。严禁使用 T+1 日盘中走势、未来是否止损、未来是否盈利等信息。

本节不是单纯列出指标，而是说明每个指标试图回答的经济问题。B2 的指标设计遵循以下顺序：

```text
第一层：这份权利金是否足够厚？
第二层：这份权利金带来的盈亏平衡保护是否足够？
第三层：这份权利金是否高于近期真实波动成本？
第四层：这份权利金能否覆盖 IV 冲击？
第五层：这份权利金能否覆盖 spot + IV 的联合压力？
第六层：扣除手续费、滑点和流动性后是否仍然值得做？
```

这六层对应一个完整的承保判断，而不是孤立指标。

### 6.1 净权利金

首先定义单合约现金口径：

```text
gross_premium_cash = option_price * contract_multiplier
net_premium_cash = gross_premium_cash - expected_fee_cash - expected_slippage_cash
```

如果暂无 bid/ask 数据，则 `expected_slippage_cash` 可以先设为 0 或使用流动性代理惩罚，但报告中必须明确说明。

净权利金用于所有质量指标，避免手续费较高、权利金过薄的合约被误判为高质量。

论证：

```text
如果 gross premium 很厚，但 fee 和预期滑点占比很高，
真实可留存权利金就会显著低于表面权利金。
```

对深虚值期权尤其如此。某些合约价格只有数个 tick，手续费、买卖价差和异常成交可能决定最终盈亏。若不先转成净权利金，后续所有 `premium / risk` 指标都会偏乐观。

可检验问题：

```text
高 fee_ratio 合约的 premium_retained_ratio 是否显著低于低 fee_ratio 合约？
```

### 6.2 权利金保证金收益率

```text
premium_yield_margin =
    net_premium_cash / estimated_initial_margin_cash * 252 / DTE
```

该指标衡量单位保证金、单位时间的权利金厚度。

注意：

```text
premium_yield_margin 不能单独作为排序依据。
```

原因是该指标会天然偏向 gamma 更高、尾部风险更大的合约。它只能作为质量评分的一部分。

论证：

```text
保证金收益率高有两种完全不同的来源：
1. 市场确实给了更高的波动风险溢价；
2. 合约更接近平值、更短 gamma、更容易在尾部亏损。
```

如果不加入 gamma、vega 和 stress 惩罚，`premium_yield_margin` 会把策略推向“看起来收得多，实际上赔付风险更大”的合约。因此该指标只能回答“保费厚不厚”，不能回答“保费是否划算”。

可检验问题：

```text
premium_yield_margin 最高分组是否同时具有更高止损率？
如果是，则说明该指标单独使用会诱导风险集中。
```

### 6.3 权利金名义收益率

```text
premium_yield_notional =
    net_premium_cash / underlying_notional_cash * 252 / DTE
```

其中：

```text
underlying_notional_cash = spot_or_futures_price * contract_multiplier
```

该指标用于避免某些低保证金率品种在保证金收益率上显得过于便宜或过于昂贵。

论证：

```text
保证金是交易所/期货公司规则结果，不完全等同于真实经济风险。
```

两个合约的保证金收益率相同，并不代表其标的名义风险相同。加入名义收益率可以帮助识别：权利金到底是相对经济风险较厚，还是只是相对保证金制度较厚。

可检验问题：

```text
按 premium_yield_margin 排序与按 premium_yield_notional 排序差异最大的品种，
是否集中在保证金率特殊或交易所规则特殊的品种？
```

### 6.4 盈亏平衡保护垫

卖 Put：

```text
breakeven_price = strike - net_premium_per_unit
breakeven_cushion_abs = spot - breakeven_price
```

卖 Call：

```text
breakeven_price = strike + net_premium_per_unit
breakeven_cushion_abs = breakeven_price - spot
```

标准化保护垫：

```text
implied_move = spot * IV * sqrt(DTE / 252)
breakeven_cushion_iv =
    breakeven_cushion_abs / implied_move
```

也可以用 RV 标准化：

```text
realized_move = spot * RV_ref * sqrt(DTE / 252)
breakeven_cushion_rv =
    breakeven_cushion_abs / realized_move
```

解释：

```text
同样 Delta < 0.1 的合约，如果收取权利金后盈亏平衡点距离当前标的更远，
且该距离相对隐含预期波动更厚，则该合约的权利金质量更高。
```

论证：

```text
Delta 衡量的是局部风险中性概率和价格敏感度，
但卖方真正关心的是：收到权利金后，标的要走多远才开始实际亏钱。
```

同样是 0.10 Delta 的 Put：

- 合约 A 权利金较厚，breakeven 明显低于执行价。
- 合约 B 权利金较薄，breakeven 接近执行价。

两者 Delta 可能接近，但 A 给了更厚的保护垫。B2 需要偏向 A，前提是 A 的 gamma、vega 和 stress loss 没有同步恶化。

该指标的局限：

- 如果 IV 本身过高，`implied_move` 会变大，使标准化保护垫变小。
- 如果 RV 即将上升，历史 RV 标准化会偏乐观。
- 如果合约价格异常，breakeven 会被脏价格污染。

因此，breakeven cushion 必须与 IV-RV、stress loss 和流动性指标共同使用。

可检验问题：

```text
breakeven_cushion 高分组是否有更低到期实值率、更低止损率、更高权利金留存率？
```

### 6.5 IV-RV Carry

基本形式：

```text
iv_rv_spread = IV_next_month - RV_ref
variance_carry = IV_next_month^2 - RV_ref^2
iv_rv_ratio = IV_next_month / RV_ref
```

`RV_ref` 不建议只使用一个窗口。B2 建议使用保守口径：

```text
RV_ref = max(RV10, RV20, side_specific_RV, gap_adjusted_RV)
```

其中：

- 卖 Put 更关注 downside RV。
- 卖 Call 更关注 upside RV。
- 如果暂时没有 downside/upside RV，可先使用 RV10 和 RV20 的较大值。

B2 对 IV-RV 的使用方式：

```text
IV-RV carry 是排序和预算因子，不是硬黑名单。
```

如果 carry 偏低，候选合约排序靠后、预算权重降低；除非净权利金小于手续费等 B0/B1 既有硬条件失败，否则不直接剔除。

论证：

```text
卖波动率的基础经济来源是 implied volatility 相对 realized volatility 的风险补偿。
```

如果 `IV <= RV`，卖方仍可能赚钱，例如标的方向有利、IV 后续下降、路径温和。但从承保角度看，这类交易缺少足够明确的波动风险溢价补偿，不应获得高预算。

反过来，如果 `IV >> RV`，也不代表一定应该大卖。它可能来自：

- 即将发生的事件风险。
- 市场对尾部风险的合理定价。
- 标的近期趋势风险尚未体现在 RV 中。
- 商品期权特定交割、政策或库存风险。

因此，IV-RV carry 的正确用法是：

```text
提高或降低预算权重，而不是直接决定做与不做。
```

可检验问题：

```text
IV-RV carry 高分组是否在扣除 vega/gamma 后仍有更高净收益？
如果只提高 theta、但同步提高 vega loss，则该指标权重应下降。
```

### 6.6 权利金对 IV 冲击亏损覆盖率

B2 需要直接重估 IV 上行冲击，而不是只使用一阶 vega：

```text
iv_shock_loss_5 =
    option_value(IV + 5vol) - option_value(IV)

iv_shock_loss_10 =
    option_value(IV + 10vol) - option_value(IV)
```

对卖方来说，上述值为不利亏损。定义：

```text
premium_to_iv5_loss =
    net_premium_cash / max(iv_shock_loss_5_cash, epsilon)

premium_to_iv10_loss =
    net_premium_cash / max(iv_shock_loss_10_cash, epsilon)
```

该指标优于单纯 `theta / cash_vega`，因为它部分包含了 vomma，即 IV 大幅变化时 vega 本身变化带来的凸性风险。

论证：

```text
cash_vega 是局部一阶敏感度，
但卖权亏损常常发生在 IV 非线性跳升时。
```

对于深虚值期权，平时 vega 可能不大；但当标的不利移动、合约接近平值时，vega 可能快速放大。单纯 `theta / cash_vega` 会低估这种风险。直接重估 `IV +5vol` 和 `IV +10vol`，可以把部分 vomma 风险包含进来。

该指标的局限：

- IV shock 幅度是人为设定，需要后续按品种校准。
- 如果当前 IV 估算不稳定，shock loss 会被污染。
- 对极端行情，固定 `+5vol/+10vol` 仍可能低估真实风险。

因此，B2 同时保留 stress loss，避免只看单独 IV shock。

可检验问题：

```text
premium_to_iv_shock 高分组是否具有更低 vega_loss / gross_open_premium？
这是 B2 最关键的验证之一。
```

### 6.7 权利金对压力情景亏损覆盖率

卖权真正的亏损通常不是单纯 IV 上升，而是：

```text
标的朝不利方向移动 + IV 上升 + skew 恶化 + 流动性变差
```

建议 B2 初始压力情景：

卖 Put：

```text
P1: spot -3%, IV +5vol
P2: spot -5%, IV +8vol
P3: spot -8%, IV +15vol
```

卖 Call：

```text
C1: spot +3%, IV +3vol
C2: spot +5%, IV +6vol
C3: spot +8%, IV +10vol
```

定义：

```text
premium_to_stress_loss =
    net_premium_cash / max(stress_loss_cash, epsilon)
```

对于商品期权，可按品种历史单日跳动和涨跌停制度进一步校准 shock 幅度，但 B2 初始版先使用统一情景，保证与 B1 的可比性。

论证：

```text
卖权亏损通常来自联合冲击，而不是单一变量冲击。
```

例如卖 Put 时，真实坏情形往往是：

```text
标的下跌 -> Put Delta 变大
IV 上升 -> 期权价格上升
put skew steepen -> 虚值保护变贵
流动性下降 -> 平仓成本提高
```

如果只看 IV-RV 或 theta/vega，很容易低估这种联动风险。`premium_to_stress_loss` 是 B2 中最直接的尾部补偿指标。

该指标的局限：

- 统一情景可能不适合所有品种。
- 商品、股指、ETF 的跳动分布不同。
- 压力情景不能覆盖涨跌停、夜盘跳空、政策冲击等所有极端情况。

因此，B2 初始版应先用统一情景做横向比较，后续再根据归因结果引入品种特异 shock。

可检验问题：

```text
premium_to_stress_loss 高分组是否在最大回撤月份中损失更小？
```

### 6.8 Theta / Vega 效率

```text
theta_vega_efficiency =
    theta_cash_per_day / max(abs(cash_vega), epsilon)
```

该指标衡量一单位 vega 暴露每天能收多少 theta。

注意：

```text
theta_vega_efficiency 不能替代 IV shock 重估。
```

因为 vega 是局部一阶指标，不能充分描述 IV 大幅跳升和 vomma 风险。

论证：

```text
theta / vega 衡量的是日常 carry 效率，
premium / IV shock loss 衡量的是冲击覆盖率。
```

两者应同时存在。一个合约可能日常 theta/vega 很好，但在 IV 大幅跳升时亏损覆盖很差；另一个合约冲击覆盖率不错，但日常 theta 太薄、资金效率不足。B2 需要在二者之间折中。

可检验问题：

```text
theta_vega_efficiency 高分组是否提高日度收益平滑度？
```

### 6.9 Gamma 租金惩罚

卖权的 theta 通常来自承担 gamma 风险。B2 需要惩罚“表面权利金厚、实际短 gamma 很重”的合约。

建议定义：

```text
gamma_rent_penalty =
    adverse_gamma_loss_proxy / max(net_premium_cash, epsilon)
```

其中：

```text
adverse_gamma_loss_proxy =
    0.5 * abs(cash_gamma) * spot_shock^2
```

初始 `spot_shock` 可与该方向压力情景一致，例如 Put 用 `3%`，Call 用 `3%`。

论证：

```text
theta 不是免费的。
很多时候更高 theta 来自更高 short gamma。
```

如果不惩罚 gamma，B2 可能会把“更接近平值、更容易出事”的合约误判为高质量权利金。尤其在只卖次月的约束下，DTE 相对固定，但不同执行价之间 gamma 差异仍然显著。

可检验问题：

```text
提高 premium_quality_score 后，如果 gamma_loss / gross_open_premium 同步上升，
说明 gamma 惩罚不足。
```

### 6.10 交易摩擦惩罚

```text
fee_ratio = expected_fee_cash / gross_premium_cash
slippage_ratio = expected_slippage_cash / gross_premium_cash
friction_ratio = fee_ratio + slippage_ratio
```

如果没有 bid/ask，可先用成交量和持仓量做流动性惩罚代理：

```text
liquidity_penalty =
    f(volume_percentile, open_interest_percentile)
```

B1 已经把流动性用于排序；B2 中流动性不再是唯一排序因子，而是交易摩擦的一部分。

论证：

```text
卖权策略的毛收益通常由大量小权利金构成，
交易摩擦对最终留存率的影响远高于普通方向策略。
```

某些合约理论上 `premium / risk` 很好，但实际成交量很低、持仓量很低、bid/ask 很宽，导致开仓和平仓都很难以理论价格成交。B1 通过成交量/OI 排序已经改善了这一点；B2 需要进一步把流动性理解为净权利金质量的一部分。

可检验问题：

```text
低流动性但高理论质量合约，是否在实盘化成本假设下表现明显下降？
```

### 6.11 指标之间的冲突与优先级

B2 指标之间可能出现冲突，这不是缺陷，而是卖权交易本身的真实权衡。

典型冲突：

```text
premium_yield 高        -> 可能 gamma 高
breakeven cushion 厚    -> 可能权利金厚但 IV 已经很高
IV-RV carry 高          -> 可能隐含了真实事件风险
theta/vega 高           -> 可能 DTE 更短、gamma 更高
stress coverage 好      -> 可能权利金太薄、资金效率低
liquidity 好            -> 可能交易拥挤、权利金不便宜
```

因此，B2 的综合分数不应追求单指标最优，而是追求：

```text
权利金厚度、波动风险溢价、冲击覆盖、尾部覆盖、交易摩擦之间的平衡。
```

### 6.12 为什么 B2 仍需保留低 Delta 硬约束

B2 虽然强调权利金质量，但不能用高权利金质量替代低 Delta 约束。原因是本策略不主动做 Delta/Gamma 盘中对冲，且用户明确要求 Delta 不能选大。

因此：

```text
abs(delta) <= 0.10 仍是硬约束；
B2 只在低 Delta 候选内部排序。
```

这可以避免评分系统为了追求更高权利金，把策略隐性推向方向性卖权或高 gamma 卖权。

## 7. B2 综合质量分数

建议初始综合分数：

```text
premium_quality_score =
    25% * iv_rv_carry_score
  + 20% * breakeven_cushion_score
  + 20% * premium_to_iv_shock_score
  + 15% * premium_to_stress_loss_score
  + 10% * theta_vega_efficiency_score
  + 10% * cost_liquidity_score
```

其中每个子分数使用当日横截面分位数或稳健 z-score 映射到 `[0, 100]`。

为避免极端值控制排序，建议：

```text
winsorize at 5% / 95%
missing value -> neutral low score, not direct delete
score floor -> 10
score cap -> 100
```

### 7.1 子分数方向

正向指标：

- `variance_carry`
- `iv_rv_ratio`
- `breakeven_cushion_iv`
- `breakeven_cushion_rv`
- `premium_to_iv5_loss`
- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `theta_vega_efficiency`
- `premium_yield_margin`
- `premium_yield_notional`

反向惩罚指标：

- `gamma_rent_penalty`
- `friction_ratio`
- `stress_loss_cash / NAV`
- `iv_shock_loss_cash / NAV`
- `fee_ratio`

### 7.2 Put 和 Call 分开排序

Put 与 Call 的风险来源不同，B2 不应把两侧直接放在同一个横截面中评分。

建议：

```text
同一交易日、同一侧别、同一次月口径内分别计算分位数。
```

原因：

- Put 的主要风险是下跌、IV 上升和 put skew steepening。
- Call 的主要风险是上涨突破、挤仓和 call wing 重新定价。
- 直接混排可能造成某一侧天然获得更高或更低分数。

## 8. 排序规则

B2 的合约排序应在 B1 可交易性排序基础上进行。

建议排序顺序：

```text
premium_quality_score 降序
contract_liquidity_score 降序
open_interest 降序
volume 降序
delta_dist 升序
option_code 升序
```

解释：

- `premium_quality_score` 是 B2 的主排序因子。
- `contract_liquidity_score` 保留 B1 的实盘可交易性思想。
- `delta_dist` 仍作为次级 tie-breaker，保证低 Delta 框架不被破坏。
- `option_code` 作为最终 tie-breaker，保证回测可复现。

## 9. 风险预算分配原则

B2 的核心不是“高分做、低分不做”，而是：

```text
高分合约获得更多风险预算，低分合约获得更少风险预算。
```

### 9.1 预算货币

B0/B1 主要用保证金作为仓位预算。B2 应保留保证金约束，但新增风险预算货币：

```text
risk_budget_cost =
    max(
        estimated_margin_cash,
        iv_shock_loss_5_cash / iv_shock_budget_ratio,
        stress_loss_cash / stress_budget_ratio
    )
```

初始研究可先简化为：

```text
risk_budget_cost =
    max(estimated_margin_cash, iv_shock_loss_5_cash, stress_loss_cash)
```

后续再根据结果校准比例。

### 9.2 质量分数到预算权重

建议使用连续函数，而不是分段黑名单：

```text
quality_weight =
    floor_weight + (1 - floor_weight) * (premium_quality_score / 100) ^ power
```

初始参数：

```text
floor_weight = 0.20
power = 1.50
```

含义：

- 即使低分候选，也保留最低 20% 权重，避免变成硬过滤。
- 高分候选预算显著提高，但不是无限放大。
- `power > 1` 让高质量候选获得更明显的边际预算优势。

### 9.3 合约层预算分配

同一品种、同一侧、同一次月内：

```text
contract_budget_share_i =
    quality_weight_i / sum(quality_weight_j)
```

每个合约的目标手数：

```text
target_lots_i =
    floor(
        product_side_budget_cash
        * contract_budget_share_i
        / risk_budget_cost_i
    )
```

这意味着：

```text
质量高且风险成本低的合约会自然获得更多手数；
质量低或风险成本高的合约仍可交易，但手数较少。
```

### 9.4 品种层预算分配

B2 可以在保持全品种扫描的基础上，让高质量品种获得更高边际预算。

品种侧质量分数：

```text
product_side_quality_score =
    weighted_average(premium_quality_score_i, weight=contract_liquidity_score_i)
```

品种侧预算权重：

```text
product_side_budget_multiplier =
    clip(
        0.50 + product_side_quality_score / 100,
        0.50,
        1.50
    )
```

但要强调：

```text
该 multiplier 只能在组合总保证金、总 stress budget、单品种上限内生效。
```

它不是允许单一品种无限加仓。

## 10. 防未来函数规则

B2 允许使用：

- T 日收盘期权价格。
- T 日成交量、持仓量。
- T 日估算 IV、Greeks。
- T 日及以前的 RV。
- T 日及以前的手续费、保证金参数。
- T 日及以前的标的价格路径。

B2 禁止使用：

- T+1 日成交量或持仓量。
- T+1 日开仓后才知道的日内高低价。
- 未来是否触发止损。
- 未来到期是否实值。
- 未来 NAV、未来 PnL、未来 IV 路径。

所有分数必须在 T 日收盘后可计算，并用于 T+1 日开仓。

## 11. 缺失值处理

B2 不应因为某项指标缺失就大规模删样本。

建议：

```text
IV 缺失 -> 使用中性偏低分数，不直接剔除
RV 缺失 -> 使用可得窗口中的最大 RV；仍缺失则中性偏低分
Greek 缺失 -> 使用近似重算；仍缺失则降低预算权重
stress_loss 缺失 -> 使用保守高风险成本
成交量/OI 缺失 -> 沿用 B1，按 0 处理
```

只有以下情况可以硬剔除：

- B0/B1 已定义的不可交易合约。
- 价格非正。
- 合约乘数缺失且无法修复。
- 净权利金小于往返手续费等基础经济性条件不成立。
- 估值所需标的价格缺失。

## 12. B2 与“降波判断”的关系

B2 不以“过去 IV 已经下降”作为主逻辑。

更准确地说：

```text
B2 不预测未来降波，而是衡量当前权利金是否足够补偿波动风险。
```

如果某一品种处于降波环境，通常会自然表现为：

- IV-RV carry 较好。
- IV shock loss 相对于权利金较低。
- breakeven cushion 较厚。
- stress loss 相对于权利金可接受。

此时该品种自然会获得更高质量分数和更多预算。

如果某一品种长期低波但权利金不足、IV-RV carry 不厚、stress coverage 较差，则即使看起来平稳，也不应获得高预算。这一点用于避免 VCP 或低波尾部突然切换风险。

## 13. 与 vega 控制的关系

B2 的目标不是机械降低组合 cash vega，因为简单降低 vega 通常会同步损失 theta。

B2 的目标是降低：

```text
vega_loss / gross_open_premium
gamma_loss / gross_open_premium
stress_loss / net_premium
```

并提高：

```text
premium_retained_ratio
theta / cash_vega
net_premium / iv_shock_loss
net_premium / stress_loss
```

换句话说：

```text
B2 控制的是 vega 暴露质量，而不是单纯控制 vega 暴露数量。
```

## 14. 回测验证口径

B2 必须与 B1 在完全相同区间、相同品种池、相同手续费、相同保证金率、相同初始资金、相同次月口径下对比。

建议正式标签：

```text
s1_b2_premium_quality_rank_stop25_allprod_2022_latest
```

建议配置文件：

```text
config_s1_baseline_b2_premium_quality_rank_stop25.json
```

必须输出：

- NAV 曲线。
- 回撤曲线。
- 保证金使用率。
- gross open premium 时序。
- net open premium 时序。
- premium retained ratio。
- theta、vega、gamma、delta、residual 归因。
- `vega_loss / gross_open_premium`。
- `gamma_loss / gross_open_premium`。
- `theta / gross_open_premium`。
- `s1_pnl / gross_open_premium`。
- Put/Call 侧别分布。
- 品种权重分布。
- B2 quality score 分布。
- B2 quality score 与后续交易 PnL 的分层关系。
- B2 quality score 与止损概率的分层关系。
- B2 quality score 与 vega loss 的分层关系。

### 14.1 分层验证

B2 必须按 `premium_quality_score` 做分层，而不是只看整体 NAV。

建议至少分成五组：

```text
Q1: 最低 20%
Q2
Q3
Q4
Q5: 最高 20%
```

每组分别统计：

- 开仓次数。
- 开仓总权利金。
- 平均净权利金。
- 平均保证金。
- 平均 `premium / IV shock loss`。
- 平均 `premium / stress loss`。
- 持有期 PnL。
- 止损率。
- 到期实值率。
- `vega_loss / gross_open_premium`。
- `gamma_loss / gross_open_premium`。
- `premium_retained_ratio`。

如果 B2 的逻辑正确，则应看到：

```text
Q5 的权利金留存率高于 Q1；
Q5 的 vega_loss/gross_premium 低于 Q1；
Q5 的止损率不高于 Q1；
Q5 的收益不是单纯来自更高保证金占用。
```

如果只看到 Q5 收益更高，但 vega/gamma 损失也更高，则 B2 只是增加风险，不是提高质量。

### 14.2 时间稳定性验证

B2 不能只在某一段行情中有效。正式报告应按年份和关键波动阶段拆解：

```text
2022
2023
2024
2025
2026 已有数据
```

并重点观察：

- 升波阶段是否少亏。
- 降波阶段是否能保留更多 theta。
- 震荡低波阶段是否没有因为 VCP 风险过度加仓。
- 极端事件月份是否比 B1 更稳。

如果 B2 只在某个特定窗口有效，而其他年份无效，应降低结论置信度。

### 14.3 品种稳定性验证

由于本策略覆盖商品、ETF、股指期权，B2 必须按品种类别拆解：

```text
商品期权
ETF 期权
股指期权
```

并进一步按板块拆解商品：

```text
有色、黑色、能化、农产品、贵金属、软商品等
```

核心问题：

```text
B2 是普遍提升权利金质量，
还是只把仓位集中到了某几个历史表现好的品种？
```

如果 B2 的改善主要来自少数品种，应检查：

- 是否只是样本期该品种方向有利。
- 是否隐含板块集中度上升。
- 是否流动性和容量足以承载更高预算。
- 是否与乐得组合画像中的品种结构相近。

### 14.4 侧别稳定性验证

B2 必须分别报告 Put 与 Call：

- Put 开仓权利金。
- Call 开仓权利金。
- Put/Call 权利金比例。
- Put/Call 手数比例。
- Put/Call 止损率。
- Put/Call vega loss。
- Put/Call gamma loss。
- Put/Call premium retained ratio。

B2 不应变成长期单边卖 Put 或长期单边卖 Call。若评分自然导致 P/C 明显偏移，必须解释：

```text
这是权利金质量差异，
还是方向行情偏移，
还是评分公式对某一侧存在结构性偏见？
```

### 14.5 交易摩擦敏感性验证

B2 如果依赖更薄权利金或更深虚值合约，则对手续费和滑点非常敏感。

因此必须做三档成本情景：

```text
当前成本口径
手续费 +25%
手续费 +50% 或滑点保守化
```

若 B2 只在乐观成本下胜出，则不能直接进入下一步策略主线。

### 14.6 样本外与防过拟合

B2 初始研究可以用 2022 至最新全样本观察，但正式判断应至少做时间切分：

```text
样本内：2022-2024
样本外：2025-最新
```

或采用 walk-forward：

```text
用过去窗口校准分数权重；
用之后窗口验证；
滚动推进。
```

B2 初版不建议优化太多参数。优先固定一套经济含义明确的默认权重，观察是否已经优于 B1。如果需要调权重，必须记录每次调参理由，避免回测结果倒推公式。

## 15. 成功标准

B2 不能只以收益率更高作为成功。

相对 B1，B2 的成功应至少满足其中多数：

- 年化收益不低于 B1，或下降很小但回撤显著改善。
- 最大回撤不高于 B1。
- `vega_loss / gross_open_premium` 明显下降。
- `gamma_loss / gross_open_premium` 不明显上升。
- `premium_retained_ratio` 明显提高。
- 最差单日亏损不恶化。
- 止损次数不显著增加。
- 收益来源更接近 theta 和正向波动风险溢价，而不是 residual 或方向暴露。

如果 B2 收益更高但 vega_loss/gross_premium 也更高，则不能认为 B2 成功，只能认为它增加了风险暴露。

### 15.1 主成功标准

B2 的主成功标准应是：

```text
在收益不显著下降的情况下，
明显降低 vega_loss / gross_open_premium，
并提高 premium_retained_ratio。
```

原因是当前 S1 的核心问题不是完全没有 theta，而是 theta 被 vega/gamma 吞噬。若 B2 不能改善这一点，即使 NAV 短期更好，也不应认为策略逻辑改善。

### 15.2 次级成功标准

次级成功标准包括：

- 最大回撤低于 B1。
- 最差单日亏损低于 B1。
- Calmar 高于 B1。
- Sharpe 或 Sortino 高于 B1。
- 止损率不高于 B1。
- P/C 结构不出现无法解释的长期偏移。
- 品种集中度不显著恶化。
- ETF/股指/商品结构更接近可实盘组合。

### 15.3 不应接受的“伪成功”

以下情况不能视为 B2 成功：

```text
1. 收益更高，但 vega_loss/gross_premium 也更高。
2. 收益更高，但主要来自某一侧方向暴露。
3. 收益更高，但最大回撤或最差单日明显恶化。
4. 收益更高，但只来自少数品种或少数月份。
5. 收益更高，但手续费/滑点稍微上调后优势消失。
6. 高分组合的分层表现并不优于低分组合。
```

这些情形说明 B2 可能只是重新分配了风险，而不是提高了权利金质量。

## 16. 失败情形与解释路径

如果 B2 失败，优先按以下顺序解释：

1. 质量评分是否过度偏向权利金厚度，导致 gamma 风险上升。
2. IV-RV carry 是否在商品期权上失真，因为 RV 口径没有匹配具体期货标的。
3. IV shock 重估是否过于温和，低估了 vega/vomma 风险。
4. stress loss 情景是否过于统一，没有反映不同品种跳动特征。
5. 质量分数是否间接造成 P/C 偏移或板块集中。
6. 成本和滑点是否仍然低估。
7. 评分是否只在样本内有效，样本外不稳定。

### 16.1 如果收益下降但风险也下降

这种结果不一定是失败。需要进一步看：

```text
收益下降幅度 / 回撤下降幅度
收益下降幅度 / vega_loss下降幅度
Calmar 是否改善
```

如果 B2 明显降低尾部风险，但收益略降，可以考虑后续在高分组内提高预算，而不是否定评分逻辑。

### 16.2 如果收益上升但 vega 亏损更大

这是最危险的结果。说明 B2 可能偏向了更厚权利金，但厚权利金背后是更重 short vega。

处理方向：

- 提高 `premium_to_iv_shock_score` 权重。
- 提高 `premium_to_stress_loss_score` 权重。
- 降低 `premium_yield_margin` 权重。
- 增加组合 cash vega 或 IV shock budget 上限。

### 16.3 如果收益上升但 gamma 亏损更大

说明 B2 可能偏向了更近价、更高 gamma 的合约。

处理方向：

- 提高 `gamma_rent_penalty`。
- 提高临近到期惩罚。
- 在次月内限制过高 gamma 合约的预算倍率。
- 增加 `premium / spot_shock_loss` 指标。

### 16.4 如果高分组止损更多

说明评分把“高风险高保费”误认为“高质量权利金”。

处理方向：

- 检查 stop 前 IV、spot、skew 的共同变化。
- 检查该现象是否集中在 Put 或 Call 一侧。
- 检查是否集中在某几个商品板块。
- 检查成交量/OI 是否不足导致止损价被异常报价触发。

### 16.5 如果评分导致品种集中

如果高分合约集中于某几个品种，B2 需要额外报告：

```text
高分品种占总保证金比例
高分品种占总 gross premium 比例
高分品种在最大回撤日贡献
```

如果集中度过高，后续应引入组合层预算上限，而不是修改单合约质量评分本身。

## 17. 一句话定义

B2 是 B1 之后的权利金质量版：

```text
仍然只卖次月、低 Delta、全品种、双侧卖权、50% 保证金、2.5x 止损；
但不再只按流动性选择合约，
而是在可交易候选中按 IV-RV carry、盈亏平衡保护垫、权利金对 IV 冲击和压力亏损的覆盖率、theta/vega 效率与交易摩擦进行排序，
并把排序结果转化为连续风险预算权重，而不是硬性黑名单。
```

## 18. B2 第一阶段实验修订：品种间预算偏移

### 18.1 修订背景

前文将 B2 描述为“权利金质量排序 + 风险预算分配”。在具体实验设计上，需要进一步拆分两个不同问题：

```text
问题 A：同一个品种、同一个方向、同一个次月中，应该选哪几个执行价？
问题 B：同一天全市场这么多品种中，哪些品种更值得分配保证金？
```

当前阶段优先研究的是问题 B。

也就是说，B2 第一阶段不先改变同品种内部的执行价选择方式，而是只回答：

```text
在 B1 已经按成交量和持仓量选择可交易合约的基础上，
是否应该把更多保证金分给当日权利金质量更高的品种？
```

这比合约内排序更接近组合承保问题。因为 S1 不是只交易一个品种，而是全品种卖权组合。若全品种机械等权，等于默认所有品种当日提供的波动率风险补偿相同；这在经济逻辑上并不成立。

### 18.2 实验定位

B2 第一阶段正式定义为：

```text
B2-product-tilt = B1 + 品种间权利金质量预算偏移
```

它不是组合风控实验，也不是板块/相关性/集中度实验。

本阶段只允许改变一件事：

```text
把原来 B1 的品种等权预算，按品种级 premium quality score 做连续倾斜。
```

其余规则必须保持 B1 不变：

- 总 S1 目标保证金仍为 `50% NAV`。
- 全品种扫描。
- 只交易次月合约。
- 只交易虚值期权。
- 卖方腿 `abs(delta) <= 0.10`。
- Put 和 Call 都参与。
- 每侧最多 `5` 个合约。
- 合约内部仍按 B1 的 `liquidity_oi` 选择。
- 止损仍为开仓权利金的 `2.5x`。
- 不设置盈利止盈。
- 持有到期或触发止损退出。
- 不新增板块上限、品种上限、相关性约束、组合 cash greek 上限或 stress gate。

### 18.3 与前文合约层 B2 的关系

前文关于单合约 `premium_quality_score` 的指标体系仍然保留，但第一阶段使用方式发生变化：

```text
单合约质量分数 -> 聚合成品种质量分数 -> 决定品种间预算权重
```

暂不使用它来替代 B1 的合约内 `liquidity_oi` 排序。

原因是：

1. 若同时改变品种预算和合约排序，无法判断收益变化来自“品种选择”还是“执行价选择”。
2. B1 的主要实验目标是更接近真实可交易合约，因此合约内流动性排序应先保留。
3. S1 当前更大的问题是全品种等权承保可能过于粗糙，优先研究品种间预算是否应当倾斜。

因此，前文“合约层排序/预算偏移”应作为后续独立实验，暂不并入 B2-product-tilt。

### 18.4 品种级质量分数

每个交易日、每个品种分别计算 Put 侧和 Call 侧的候选合约质量。

单合约质量指标仍使用前文定义的字段：

```text
variance_carry
breakeven_cushion_iv
breakeven_cushion_rv
premium_to_iv5_loss
premium_to_iv10_loss
premium_to_stress_loss
theta_vega_efficiency
gamma_rent_penalty
friction_ratio
```

单合约质量分数建议仍使用当日横截面分位数映射：

```text
contract_premium_quality_score =
  20% * variance_carry_score
+ 15% * breakeven_cushion_score
+ 20% * premium_to_iv_shock_score
+ 15% * premium_to_stress_loss_score
+ 10% * theta_vega_efficiency_score
+ 10% * gamma_rent_score
+ 10% * friction_score
```

其中：

```text
越高越好：
variance_carry
breakeven_cushion_iv / breakeven_cushion_rv
premium_to_iv5_loss / premium_to_iv10_loss
premium_to_stress_loss
theta_vega_efficiency

越低越好：
gamma_rent_penalty
friction_ratio
```

品种内先分别得到：

```text
put_side_score_i
call_side_score_i
```

建议第一版聚合规则为：

```text
put_side_score_i =
    liquidity_weighted_average(top_put_contract_scores)

call_side_score_i =
    liquidity_weighted_average(top_call_contract_scores)
```

其中 top contract 集合沿用 B1 的 `liquidity_oi` 选择逻辑，最多取每侧 `5` 个。

流动性权重可使用：

```text
contract_liquidity_weight =
    0.5 * volume_rank + 0.5 * open_interest_rank
```

品种总分为：

```text
product_score_i = 0.5 * put_side_score_i + 0.5 * call_side_score_i
```

如果某天某品种只有一侧有有效候选，则：

```text
product_score_i = available_side_score_i * missing_side_penalty
missing_side_penalty = 0.70
```

如果两侧都没有有效候选，则该品种当日不进入候选品种池。

这里刻意使用 P/C 等权聚合，而不是让 Put 或 Call 中某一侧自动主导品种分数。原因是 B2 第一阶段不希望隐含形成长期看涨或看跌偏置；方向倾斜应作为后续趋势/动量实验单独研究。

### 18.5 品种间预算偏移

B1 的品种预算为：

```text
equal_product_budget_i =
    total_s1_budget / number_of_candidate_products
```

B2-product-tilt 改为先计算质量权重：

```text
raw_quality_weight_i =
    floor_weight + (product_score_i / 100) ^ power
```

再归一化：

```text
quality_product_budget_i =
    total_s1_budget * raw_quality_weight_i / sum(raw_quality_weight_j)
```

为了避免第一阶段直接变成过强集中度实验，最终预算使用等权和质量权重的线性混合：

```text
final_product_budget_i =
    (1 - tilt_strength) * equal_product_budget_i
  + tilt_strength * quality_product_budget_i
```

其中：

```text
total_s1_budget = NAV * 50%
```

初始参数建议：

```text
floor_weight = 0.50
power = 1.50
missing_side_penalty = 0.70
missing_score = 20
score_clip = [5, 95]
```

`floor_weight` 的意义是防止低分品种预算被压到接近 0。B2 第一阶段不是黑名单过滤，而是预算倾斜。

`power` 的意义是控制高分品种的凸性奖励。

`tilt_strength` 的意义是控制从等权到质量权重的迁移强度。

### 18.6 三档实验

建议 B2 第一阶段至少做三档：

```text
B2a_product_tilt_025:
    tilt_strength = 0.25

B2b_product_tilt_050:
    tilt_strength = 0.50

B2c_product_tilt_075:
    tilt_strength = 0.75
```

三档实验可以回答：

```text
质量分数越强地影响品种预算，绩效是否单调改善？
```

如果只有某一档表现最好，而另外两档没有规律，则需要谨慎判断是否过拟合。

若三档呈现：

```text
收益改善
最大回撤不显著恶化
vega_loss / gross_premium 下降
premium retained ratio 提升
```

则说明品种间权利金质量分配可能是真实有效的方向。

### 18.7 不允许混入的变量

B2-product-tilt 阶段明确不允许同时引入以下变化：

- 不新增品种最大保证金上限。
- 不新增板块最大保证金上限。
- 不新增相关性组约束。
- 不新增组合 cash vega 上限。
- 不新增组合 cash gamma 上限。
- 不新增 stress gate。
- 不改变 P/C 方向选择规则。
- 不改变每侧最多合约数。
- 不改变止损倍数。
- 不改变次月规则。
- 不改变 delta 上限。
- 不改变交易费用和保证金公式。

这些变量都有研究价值，但必须在 B2-product-tilt 之后作为独立实验逐步加入。

### 18.8 未来函数控制

B2-product-tilt 必须严格遵守：

```text
T 日收盘计算 product_score_i
T 日收盘生成 T+1 开仓计划
T+1 使用既定执行口径开仓
```

质量分数只能使用：

- T 日当日可见的期权收盘价、IV、Greeks、成交量、持仓量。
- T 日当日可见的标的价格。
- 截至 T 日的历史 IV、RV 和趋势特征。
- T 日当日横截面 rank。

禁止使用：

- 全样本均值和标准差。
- 全样本 zscore。
- 未来收益。
- 未来止损结果。
- 未来成交量或持仓量。
- T+1 之后才知道的 RV、IV 变化或价格路径。

第一版建议完全不用历史 zscore，只使用当日横截面 rank。若后续要使用历史 zscore，必须写成：

```text
historical_zscore_t =
    (value_t - rolling_mean(value_{t-lookback : t-1}))
    / rolling_std(value_{t-lookback : t-1})
```

也就是 rolling / expanding 统计必须 `shift(1)`，不能包含当前值之后的任何信息。

### 18.9 诊断输出

B2-product-tilt 不能只看 NAV。必须额外输出以下诊断：

```text
daily_product_score.csv
daily_product_budget.csv
daily_product_budget_vs_equal.csv
product_score_quintile_performance.csv
product_budget_tilt_summary.csv
```

核心观察指标包括：

- 高分品种是否获得更多预算。
- 高分品种的权利金留存率是否更高。
- 高分品种的止损率是否更低。
- 高分品种的 `vega_loss / gross_premium` 是否更低。
- 高分品种的 `gamma_loss / gross_premium` 是否没有显著升高。
- 预算倾斜后 P/C 结构是否发生非预期偏移。
- 预算倾斜后 ETF、股指、商品三类占比是否发生明显变化。
- 最大回撤日损失是否集中于高分品种。

如果高分品种拿到更多预算，但高分组留存率没有提高，说明 B2 指标没有真正刻画“可承保权利金质量”。

如果高分品种收益更高但 vega 亏损更大，说明评分可能只是追逐更厚的 short vega，而不是更高质量的 theta。

### 18.10 实验判定标准

B2-product-tilt 相对 B1 的优先目标不是单纯提高收益，而是提高风险调整后的承保质量。

优先判断顺序：

1. `premium retained ratio` 是否提高。
2. `vega_loss / gross_premium` 是否下降。
3. `gamma_loss / gross_premium` 是否不显著上升。
4. 年化收益是否提高。
5. 最大回撤是否不显著恶化。
6. Sharpe / Calmar 是否改善。
7. 最大回撤日是否没有集中暴露于少数高分品种。

如果收益提高但 vega 亏损占权利金比例也提高，则不应直接视为成功。

如果收益略降但 vega 亏损显著下降、Calmar 改善，也可以视为有研究价值。

### 18.11 一句话定义

B2 第一阶段不是“更复杂的风控”，而是：

```text
在 B1 完全相同的交易结构下，
把 50% 总保证金从机械品种等权，
改为按当日品种级权利金质量做连续预算倾斜，
以验证哪些品种更值得承保。
```
