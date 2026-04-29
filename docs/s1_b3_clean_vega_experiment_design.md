# S1 B3：Forward Vega、Vol-of-Vol 与 IV Shock 覆盖率实验设计

文档日期：2026-04-28  
策略对象：S1 日频低 Delta 卖权策略  
基准版本：B2c，`s1_b2_product_tilt075_stop25_allprod_2022_latest`  
研究目标：在保留 B2c 权利金增厚和留存率改善的同时，降低 vega 与波动率二阶风险损耗，推动 S1 更接近“赚 theta + 赚 vega”的卖波策略画像。

## 1. 研究背景

B2 的核心实验已经说明，品种间权利金质量倾斜是有效的。相较 B1，B2c 在同样约 50% 平均保证金使用率下，将累计收益从 15.20% 提升到 17.14%，CAGR 从 2.98% 提升到 3.33%，标准年化 Sharpe 从 1.53 提升到 1.69，Calmar 从 1.19 提升到 1.32。更关键的是，B2c 毛开仓权利金从 784.10 万提升到 812.99 万，平仓后权利金留存率从 18.84% 提升到 20.47%。

但是，B2c 仍然没有解决 S1 最核心的质量问题：vega 归因仍为负，而且绝对亏损扩大。B2c 的累计 theta 为 1019.05 万，比 B1 多 31.89 万；但 vega 亏损为 -602.62 万，比 B1 多亏 14.10 万；gamma 亏损为 -692.15 万，比 B1 多亏 21.05 万。也就是说，B2c 的进步来自“收更多保险费并留下更多”，而不是已经真正控制住 short vega 和 short convexity。

因此，B3 不应该继续简单放大 B2c，也不应该把策略改成复杂黑名单过滤。B3 要回答一个更严格的问题：

```text
在 B2c 已经能识别更厚、更可留存权利金的基础上，
如何区分“干净的权利金增厚”和“带着更高 forward vega / vol-of-vol / vomma 尾部风险的权利金增厚”？
```

B3 的研究方向是：

```text
B3 = B2c + clean vega quality layer
```

它仍以排序和预算倾斜为主，不以大规模漏斗过滤为主。其目标不是减少交易到没有风险，而是在同样卖次月、低 Delta、全品种、约 50% 保证金目标下，把预算更多给“权利金能覆盖 IV shock 且波动风险正在改善”的品种和侧别，把预算少给“权利金厚但 vega/vomma/vol-of-vol 风险更脏”的品种和侧别。

## 2. 核心研究问题

B3 要检验四个问题：

1. B2c 的超额收益是否主要来自更高毛权利金，还是来自更高质量的波动风险补偿？
2. 能否构造 forward vega、vol-of-vol、vomma 和 IV shock 覆盖率指标，使 B2c 的毛权利金优势保留，但 vega 绝对损耗不再扩大？
3. 在不新增复杂组合风控的前提下，仅通过品种间和侧别间预算倾斜，能否改善 `vega_loss / gross_open_premium`、止损损耗和最大回撤？
4. B3 指标是否在 2022 至最新完整样本、各年份、关键升波月份和 P/C 两侧均具有稳定性，而不是只拟合某一段行情？

其中第三个问题最关键。B3 第一阶段仍是品种间、侧别间预算实验，而不是板块集中度、相关性约束、组合 cash greek 上限或 stress gate 实验。后者有价值，但应放在 B3 之后单独验证，避免一次改变太多变量。

## 3. 策略哲学：从“权利金厚度”升级到“波动风险承保质量”

卖权策略的收益不是来自单纯卖出一张期权，而是来自承担波动风险、路径风险和尾部风险后获得补偿。对低 Delta 卖权而言，Delta 一阶方向风险被限制，但并未消失；真正决定极端亏损的是 gamma、vega、vomma、vanna、skew、跳空和流动性共同作用。

对期权价值做局部展开：

```text
dV
≈ Delta * dS
 + 0.5 * Gamma * dS^2
 + Vega * dIV
 + 0.5 * Vomma * dIV^2
 + Vanna * dS * dIV
 + Theta * dt
```

对卖方而言：

```text
short_option_pnl
≈ -dV - transaction_cost
```

所以一笔卖权交易真正需要满足的不是：

```text
权利金 > 手续费
```

而是：

```text
权利金
> 交易摩擦
 + 预期 gamma 路径成本
 + 预期 vega 冲击成本
 + 预期 vomma 非线性成本
 + spot-vol 联动的 vanna 成本
 + 尾部风险资本占用
 + 必要风险溢价
```

B2 已经开始衡量 `premium / IV shock loss`、`premium / stress loss`、`theta / cash_vega` 等指标。B3 在此基础上进一步强调“forward”和“二阶”：

```text
不是只看当前 IV 是否高，
而是看当前次月权利金是否足够覆盖后续 IV 上行、vol-of-vol 升高、曲面恶化和 IV shock 后的非线性重估损失。
```

这也是为什么 B3 不把“降波”简单定义成当前 IV 下降。真正值得加预算的不是“IV 已经低”，而是：

```text
IV 仍有厚度；
IV/RV carry 仍为正；
forward variance 不再系统性抬升；
vol-of-vol 开始收敛；
IV shock 后权利金覆盖率足够；
vomma/volga 非线性损失可接受。
```

## 4. 文献基础与策略映射

### 4.1 方差风险溢价：卖权收益来源的母框架

Carr and Wu 提出用期权组合近似方差互换，并用风险中性方差与实现方差之间的差异度量 variance risk premium。该框架说明，卖波收益不是来自“IV 高”这个表象，而是来自市场愿意为未来方差风险支付保险费。

对 S1 的启发：

```text
variance_carry = IV^2 - RV_proxy^2
```

仍然是基础指标，但它只能说明平均意义上的波动风险补偿，不能单独判断某张低 Delta 次月期权是否安全。B3 应保留 B2 的 variance carry，但必须加入 forward variance、IV shock coverage 和 tail risk charge。

参考文献：

- Carr, P. and Wu, L. (2007). Variance Risk Premia. SSRN: https://ssrn.com/abstract=577222

### 4.2 IV-RV 横截面错配：品种间排序的文献基础

Goyal and Saretto 研究股票期权横截面收益，发现历史实现波动率和市场隐含波动率之间的差异具有解释期权收益的能力。这直接支持 B2/B3 的“横截面排序”思路：同一天不同标的的 IV/RV 错配程度不同，卖方不应机械等权承保。

对 S1 的启发：

```text
不同品种之间的 IV/RV carry 可以作为预算倾斜依据，
但必须结合流动性、期限、skew 和 shock coverage 防止把高风险高保费误判为便宜。
```

参考文献：

- Goyal, A. and Saretto, A. (2007). Option Returns and Volatility Mispricing. SSRN: https://ssrn.com/abstract=889947

### 4.3 Delta-hedged gains：低 Delta 不是低波动风险

Bakshi and Kapadia 通过 delta 对冲期权组合研究负波动风险溢价，说明即使方向风险被控制，期权组合仍然承受系统性波动风险，delta-hedged gains 与 volatility risk premium 和 option vega 直接相关。

对 S1 的启发：

```text
低 Delta 只限制一阶方向暴露；
它不能消除 vega、gamma、jump 和高阶 convexity 风险。
```

B3 因此不应因为 `abs(delta) <= 0.10` 就放松 vega 风险，而应该更关注：

```text
premium_to_iv_shock_loss
theta_vega_efficiency
forward_vega_pressure
vomma_penalty
```

参考文献：

- Bakshi, G. and Kapadia, N. (2003). Delta-Hedged Gains and the Negative Market Volatility Risk Premium. Review of Financial Studies. https://doi.org/10.1093/rfs/hhg002

### 4.4 Vol-of-vol 是独立风险因子

Huang, Schlag, Shaliastovich and Thimme 研究表明，volatility-of-volatility 是影响期权收益的显著风险因子，并且不能简单由 volatility 本身替代。对卖方而言，vol-of-vol 高意味着 IV shock 更容易发生，且 vega、vomma 和流动性折价会同时恶化。

对 S1 的启发：

```text
当前 IV 高并不必然可卖；
如果 IV 自身波动率也高，厚权利金可能只是对不稳定波动环境的合理补偿。
```

B3 应引入：

```text
iv_vov_5d
iv_vov_10d
iv_vov_20d
vov_trend
vov_percentile_or_cross_section_rank
```

并以预算惩罚形式使用，而不是简单硬过滤。

参考文献：

- Huang, D., Schlag, C., Shaliastovich, I. and Thimme, J. (2018). Volatility-of-Volatility Risk. SSRN: https://ssrn.com/abstract=2497759

### 4.5 方差风险期限结构：forward variance 的必要性

Dew-Becker, Giglio, Le and Rodriguez 讨论方差风险价格在期限维度上的差异，提示风险补偿并不在所有期限上同质。虽然 S1 目前只卖次月，但我们仍可以用近月、次月和远月的 IV 曲线估算 forward variance pressure，判断次月权利金是否处于“可承保的降波/正常 carry”还是“整条曲线同步升波”的状态。

对 S1 的启发：

```text
只卖次月不代表不能使用期限结构信息；
期限结构是判断次月 short vega 是否干净的重要辅助变量。
```

B3 的 forward variance 指标不是为了改到期，而是为了决定次月预算：

```text
若次月 IV 高，但 forward variance 开始回落或未继续抬升，预算可保留或增加；
若近月、次月、远月同步上行，说明不是短期噪声，而是系统性波动风险抬升，应降预算。
```

参考文献：

- Dew-Becker, I., Giglio, S., Le, A. and Rodriguez, M. (2015). The Price of Variance Risk. SSRN: https://ssrn.com/abstract=2607370

### 4.6 尾部恐惧与跳跃风险：高保费可能是灾难保险费

Bollerslev and Todorov 将尾部风险、jump fear 与风险溢价联系起来。对卖权策略而言，特别是低 Delta OTM 期权，权利金厚度的一部分可能来自市场对跳跃和尾部状态的定价。卖方收取这部分权利金，等价于承担灾难尾部赔付。

对 S1 的启发：

```text
premium 高不等于 premium 便宜；
必须看 premium / tail stress loss。
```

B3 应继续强化：

```text
premium_to_stress_loss
premium_to_spot_iv_stress_loss
tail_product_side_contribution
stop_cluster_penalty
```

参考文献：

- Bollerslev, T. and Todorov, V. (2009). Tails, Fears, and Risk Premia. SSRN: https://ssrn.com/abstract=1418488

### 4.7 Risk-neutral skewness：P/C 结构不能机械偏向

Bali and Murray 研究 risk-neutral skewness 与期权组合收益，说明期权隐含偏度具有定价信息。对 S1 来说，Put 和 Call 两侧不能简单按长期收益倒推偏向；P/C 应由当日 skew、趋势、forward vega、tail coverage 和权利金质量共同决定。

对 S1 的启发：

```text
不能因为某段样本 Put 留存率更高，就长期更偏 Put；
也不能因为 Call 某段亏损，就长期放弃 Call。
```

B3 应把 clean vega score 分成 product-side 口径：

```text
product_put_clean_vega_score
product_call_clean_vega_score
```

让 P/C 结构由两侧权利金质量自然形成，而不是人为固定或方向押注。

参考文献：

- Bali, T. G. and Murray, S. (2013). Does Risk-Neutral Skewness Predict the Cross-Section of Equity Option Portfolio Returns? Journal of Financial and Quantitative Analysis. https://doi.org/10.1017/S0022109013000410

### 4.8 Put 昂贵性：Put 贵不等于 Put 值得卖

Bondarenko 讨论 Put 期权为何显得昂贵，提醒我们 Put 的高权利金可能反映投资者尾部保险需求和约束，而不是无风险套利机会。对商品期权尤其要谨慎，因为某些商品的供需、政策、库存和天气风险会造成真实尾部。

对 S1 的启发：

```text
Put 侧预算增厚必须同时满足 downside stress coverage，
不能只看 Put premium 高。
```

参考文献：

- Bondarenko, O. (2014). Why are Put Options So Expensive? Quarterly Journal of Finance. SSRN: https://ssrn.com/abstract=375784

### 4.9 商品方差风险溢价：商品期权不能照搬股指/ETF

Trolle and Schwartz 研究能源商品中的 variance risk premia，说明商品波动风险溢价具有自身结构。S1 当前主要暴露在商品期权，因此 B3 的指标不能只按股指期权直觉设计。商品中的库存、季节性、供给冲击、政策和交易时段差异会让 IV/RV、skew 和 VOV 的含义与股指 ETF 不完全相同。

对 S1 的启发：

```text
B3 应保留跨品种横截面排序，
但报告中必须按商品板块、股指、ETF 分开验证指标有效性。
```

参考文献：

- Trolle, A. B. and Schwartz, E. S. (2010). Variance Risk Premia in Energy Commodities. The Journal of Derivatives. https://doi.org/10.3905/jod.2010.17.3.015

### 4.10 期权期望收益与尾部风险补偿

Coval and Shumway 从主流资产定价角度研究期权期望收益，提示期权收益需要与非线性风险暴露、系统性风险和尾部状态联系起来，而不能只看胜率。

对 S1 的启发：

```text
高胜率卖权不是成功标准；
核心是最大亏损、尾部亏损集中度和权利金留存率。
```

参考文献：

- Coval, J. D. and Shumway, T. (2001). Expected Option Returns. Journal of Finance. https://deepblue.lib.umich.edu/handle/2027.42/74142

## 5. B3 的实验原则

B3 遵循以下原则：

1. 只在 B2c 基础上增量修改。B2c 是当前最优主线底座，不重新设计 B0/B1/B2。
2. 保持总 S1 目标保证金约 50%，不新增板块、相关性、组合 Greek 上限，避免混入组合风控变量。
3. 仍只卖次月、低 Delta、虚值期权，仍执行 2.5x 权利金止损和异常跳价确认。
4. 指标用于预算倾斜，不做大规模黑名单。只有数据异常、价格低于门槛、流动性硬条件等已有规则继续硬过滤。
5. 所有指标只使用 T 日及以前信息，T 日收盘计算，T+1 开仓，严禁未来函数。
6. 以 product-side 为最小预算单元，即同一品种的 Put 侧和 Call 侧可以获得不同 clean vega budget。
7. 成功标准不是单纯 NAV 更高，而是同时改善权利金留存、vega 损耗率、止损损耗和压力月表现。

## 6. B3 指标体系

### 6.1 Forward Variance Pressure

目标：判断次月 short vega 是否处在整条波动曲线继续抬升的压力中。

建议字段：

```text
atm_iv_near
atm_iv_next
atm_iv_far
var_near = atm_iv_near^2 * T_near
var_next = atm_iv_next^2 * T_next
var_far  = atm_iv_far^2  * T_far

forward_var_near_to_next =
    max(var_next - var_near, 0) / max(T_next - T_near, eps)

forward_var_next_to_far =
    max(var_far - var_next, 0) / max(T_far - T_next, eps)

forward_var_pressure =
    rank_or_zscore(forward_var_near_to_next_change)
  + rank_or_zscore(forward_var_next_to_far_change)
```

若缺少近月或远月合约，允许降级为：

```text
next_month_iv_trend
next_month_iv_rank
next_month_iv_change_3d
```

方向：

```text
forward variance 回落或稳定：加分；
forward variance 抬升：扣分；
近月短期事件升波但次月/远月稳定：不直接扣重分；
近月、次月、远月同步升波：重扣分。
```

### 6.2 Vol-of-Vol Proxy

目标：识别 IV 自身是否不稳定。卖方最怕的不是 IV 高，而是 IV 高且继续大幅跳动。

建议字段：

```text
iv_change_1d = atm_iv_t - atm_iv_t-1
iv_change_3d = atm_iv_t - atm_iv_t-3
iv_vov_5d  = std(iv_change_1d over last 5 trading days)
iv_vov_10d = std(iv_change_1d over last 10 trading days)
iv_vov_20d = std(iv_change_1d over last 20 trading days)
vov_trend = iv_vov_5d / max(iv_vov_20d, eps)
```

方向：

```text
vov_trend < 1 且 IV/RV carry 为正：加分；
vov_trend > 1.3：扣分；
vov_trend > 1.8：强扣分，但不一定硬禁做；
IV 已极低且 vov 极低：不加分，因为可能是 VCP 低波陷阱。
```

### 6.3 IV Shock Coverage

目标：用当日盘口与 Black76 重估，计算开仓权利金能覆盖多少 IV 上行冲击。

建议字段：

```text
iv5_loss = short_option_loss_under_IV_plus_5vol
iv10_loss = short_option_loss_under_IV_plus_10vol

premium_to_iv5_loss =
    net_open_premium / max(iv5_loss, eps)

premium_to_iv10_loss =
    net_open_premium / max(iv10_loss, eps)
```

方向：

```text
coverage 越高越好；
coverage 低说明权利金看似厚，但对 IV spike 的缓冲不足；
Put/Call 分侧计算，避免一侧 skew 掩盖另一侧风险。
```

实现要求：

```text
只在日频候选层向量化计算；
不得进入分钟循环；
使用真实标的价格、真实到期时间、当前 IV 和 Black76；
若 IV 缺失，不能用未来或全样本均值填充，降级或记为 missing。
```

### 6.4 Spot + IV Joint Stress Coverage

目标：覆盖卖方真正危险的组合情景：标的不利移动与 IV 上升同时发生。

建议情景：

```text
Put 侧：
    spot -2%, IV +3vol
    spot -5%, IV +8vol
    spot -8%, IV +15vol

Call 侧：
    spot +2%, IV +3vol
    spot +5%, IV +8vol
    spot +8%, IV +15vol
```

建议字段：

```text
premium_to_mild_stress_loss
premium_to_medium_stress_loss
premium_to_crash_stress_loss
```

B3 第一阶段不把这些指标作为组合 stress gate，只用于 product-side 预算倾斜。后续组合风控版本再考虑硬上限。

### 6.5 Vomma / Volga Penalty

目标：识别 IV shock 的非线性风险。对于卖方，IV 上升 10 vol 的损失可能不只是 IV 上升 5 vol 的两倍。

建议有限差分：

```text
price_base = option_price(IV)
price_up5 = option_price(IV + 5vol)
price_up10 = option_price(IV + 10vol)

vomma_proxy =
    price_up10 - 2 * price_up5 + price_base

vomma_loss_ratio =
    vomma_proxy / max(net_open_premium, eps)
```

方向：

```text
vomma_loss_ratio 越高，预算越低；
如果 IV shock loss 已经高且 vomma_loss_ratio 也高，应明显降权；
深虚值期权也可能存在较高非线性跳变风险，不能因为 delta 小就忽略。
```

### 6.6 Skew Steepening Penalty

目标：避免在市场尾部保险需求正在快速升温时继续卖最脆弱的一侧。

建议字段：

```text
put_skew = iv_10d_put - atm_iv
call_skew = iv_10d_call - atm_iv
put_skew_change_3d
call_skew_change_3d
skew_steepening_penalty
```

方向：

```text
Put skew 快速 steepen：Put 侧降预算；
Call skew 快速 steepen：Call 侧降预算；
skew 高但正在回落，且 premium coverage 足够：不必直接降权。
```

### 6.7 Clean Vega Score

综合分数建议采用 product-side 口径：

```text
clean_vega_score_side =
    25% * premium_to_iv_shock_score
  + 20% * forward_variance_score
  + 20% * vol_of_vol_score
  + 15% * premium_to_joint_stress_score
  + 10% * vomma_score
  + 10% * skew_stability_score
```

其中：

```text
越高越好：
premium_to_iv_shock_score
forward_variance_score
vol_of_vol_score
premium_to_joint_stress_score
vomma_score
skew_stability_score
```

`vomma_score` 与 `vol_of_vol_score` 是惩罚项反向映射后的分数。

最终预算仍与 B2c 的 product quality score 混合：

```text
final_product_side_score =
    60% * b2_product_quality_score
  + 40% * clean_vega_score_side
```

第一版不建议让 clean vega 完全替代 B2，因为 B2 已经证明能提高权利金留存。B3 的目标是“修正 B2 的 vega 瑕疵”，不是推翻 B2。

## 7. 实验组设计

### 7.1 B3a：Forward Variance Tilt

定义：

```text
B3a = B2c + forward variance pressure 预算倾斜
```

只新增 forward variance / term structure 信息，不加入 VOV、vomma 或 IV shock coverage。

目的：

```text
检验期限结构是否能识别“次月权利金厚但后续升波压力大”的品种。
```

预期：

- 如果 B3a 有效，应看到 vega_loss/gross_premium 下降。
- 如果收益下降但 vega 显著改善，说明期限结构是有效风控信号。
- 如果收益和 vega 都恶化，说明中国场内次月期限结构数据质量或代理方式不足。

### 7.2 B3b：Vol-of-Vol Tilt

定义：

```text
B3b = B2c + vol-of-vol 预算倾斜
```

只新增 IV 自身波动率和 VOV trend，不改变其他指标。

目的：

```text
检验“IV 不稳定”是否解释 B2c 中更大的 vega 绝对损耗和重止损。
```

预期：

- 高 VOV 品种预算下降后，止损亏损和最差日应改善。
- 若毛权利金下降过多但 vega 改善不明显，说明 VOV 代理过于粗糙。

### 7.3 B3c：IV Shock Coverage Tilt

定义：

```text
B3c = B2c + premium_to_iv5_loss / premium_to_iv10_loss 预算倾斜
```

目的：

```text
检验“权利金能否覆盖 IV shock”是否比单纯 IV/RV carry 更接近卖方真实风险。
```

预期：

- 毛权利金可能略降，但留存率应提升。
- vega_loss/gross_premium 应下降。
- 若 B3c 收益下降但最大回撤改善，是可接受结果。

### 7.4 B3d：Vomma Penalty Tilt

定义：

```text
B3d = B2c + vomma / volga 非线性惩罚
```

目的：

```text
检验 IV 大幅上行时的非线性重估损失是否是 B2c vega 绝对损耗扩大的主要来源。
```

预期：

- 对深虚值、低价但跳价敏感合约有更强降权。
- 止损次数未必明显下降，但单次止损金额可能下降。

### 7.5 B3e：Clean Vega Composite

定义：

```text
B3e = B2c + clean_vega_score_side 综合预算倾斜
```

综合 B3a/B3b/B3c/B3d，并加入 skew stability。

目的：

```text
验证完整 clean vega layer 是否能成为 B2 之后的主线。
```

建议第一版参数：

```text
b2_score_weight = 0.60
clean_vega_score_weight = 0.40
clean_vega_tilt_strength = 0.50
floor_weight = 0.50
power = 1.50
score_clip = [5, 95]
```

若 B3e 成功，再测试：

```text
clean_vega_tilt_strength = 0.25 / 0.50 / 0.75
b2_score_weight = 0.70 / 0.60 / 0.50
```

但第一轮不应同时扩展太多参数，避免过拟合。

### 7.6 本轮落地配置

本轮五组实验均继承：

```text
config_s1_baseline_b2_product_tilt075_stop25.json
```

因此除 B3 clean vega 层外，B0/B1/B2 的基础规则保持一致：全品种、只卖次月、低 Delta、最低期权价格 0.5、流动性/OI 排序、B2c 75% 品种间权利金质量倾斜、约 50% 保证金目标、2.5x 权利金止损。

五组配置文件与权重如下：

| 实验 | 配置文件 | B2 权重 | Forward variance | Vol-of-vol | IV shock coverage | Joint stress | Vomma | Skew stability | B3 倾斜强度 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B3a | `config_s1_baseline_b3a_forward_variance_tilt_stop25.json` | 0.70 | 0.30 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.50 |
| B3b | `config_s1_baseline_b3b_vol_of_vol_tilt_stop25.json` | 0.70 | 0.00 | 0.30 | 0.00 | 0.00 | 0.00 | 0.00 | 0.50 |
| B3c | `config_s1_baseline_b3c_iv_shock_coverage_tilt_stop25.json` | 0.70 | 0.00 | 0.00 | 0.30 | 0.00 | 0.00 | 0.00 | 0.50 |
| B3d | `config_s1_baseline_b3d_vomma_penalty_tilt_stop25.json` | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0.30 | 0.00 | 0.50 |
| B3e | `config_s1_baseline_b3e_clean_vega_composite_tilt_stop25.json` | 0.60 | 0.08 | 0.08 | 0.10 | 0.06 | 0.04 | 0.04 | 0.50 |

五组均启用：

```text
s1_track_contract_iv_trend = true
s1_b3_clean_vega_tilt_enabled = true
s1_b3_floor_weight = 0.50
s1_b3_power = 1.50
s1_b3_score_clip = [5, 95]
s1_b3_product_side_budget_diagnostics_enabled = true
```

`s1_track_contract_iv_trend` 只用于 T 日收盘后生成 T+1 预算倾斜所需的历史 IV 变化和 vol-of-vol proxy，不启用合约 IV 硬过滤，因此不会改变 B2c 的基础候选池规则。

## 8. 预算分配公式

B2c 的品种预算为：

```text
b2_product_budget_i
```

B3 建议切换到 product-side 预算，即：

```text
b2_product_side_budget_i_put
b2_product_side_budget_i_call
```

若当前实现仍是 product-level，可先按 B2c 的 Put/Call 候选权利金或候选数量拆分基础预算：

```text
base_side_budget_i_s =
    b2_product_budget_i * side_base_share_i_s
```

其中 `s ∈ {put, call}`，第一版可用：

```text
side_base_share_i_put = 0.5
side_base_share_i_call = 0.5
```

然后按 clean vega score 做侧别预算倾斜：

```text
raw_side_weight_i_s =
    floor_weight + (final_product_side_score_i_s / 100) ^ power

quality_side_budget_i_s =
    total_s1_budget * raw_side_weight_i_s / sum(raw_side_weight_all)

final_side_budget_i_s =
    (1 - clean_vega_tilt_strength) * base_side_budget_i_s
  + clean_vega_tilt_strength * quality_side_budget_i_s
```

这样做有两个好处：

1. Put 和 Call 不再机械等权，也不长期单边偏置，而是由各自权利金质量决定。
2. 若某品种 Put 侧出现 skew steepening 或 IV shock coverage 变差，Put 预算会下降，但 Call 侧不一定被连带惩罚。

## 9. 未来函数控制

B3 所有指标必须满足：

```text
T 日收盘计算指标；
T 日收盘生成 T+1 开仓计划；
T+1 按既定执行口径开仓；
持仓期间按已有止损和到期规则执行。
```

允许使用：

- T 日当日及以前的期权报价、IV、Greeks、成交量、持仓量。
- T 日当日及以前的真实标的价格。
- 截至 T 日的历史 IV、历史 RV、历史 skew、历史 VOV。
- T 日同一横截面的 rank 或分位数。

禁止使用：

- T+1 之后的成交量、持仓量、IV、RV 或价格路径。
- 全样本均值、全样本标准差、全样本 zscore。
- 未来是否止损、未来是否到期盈利、未来权利金留存。
- 未来高低点判断当前止损风险。

若使用历史 zscore，必须写成：

```text
historical_zscore_t =
    (value_t - rolling_mean(value_{t-lookback : t-1}))
    / rolling_std(value_{t-lookback : t-1})
```

也就是 rolling 统计必须 `shift(1)`。如果第一版实现成本较高，优先使用 T 日横截面 rank，降低未来函数风险。

## 10. 数据与实现要求

### 10.1 数据字段

B3 需要以下字段：

```text
option_code
product
side
trade_date
expiry
dte
strike
underlying_price
option_price
iv
delta
gamma
vega
theta
volume
open_interest
contract_multiplier
fee
margin
```

期限结构还需要：

```text
near_expiry_atm_iv
next_expiry_atm_iv
far_expiry_atm_iv
near_dte
next_dte
far_dte
```

若无法稳定获得 ATM IV，可用当日同品种同到期的低 Delta 候选中位 IV 或 delta-nearest ATM proxy 作为降级口径，但必须在诊断中标注。

### 10.2 计算性能

B3 指标应在日频候选层一次性向量化计算：

```text
候选链生成后；
开仓排序前；
风险预算分配前。
```

禁止把 IV shock 和 vomma 重估放入分钟止损扫描。IV shock 重估只需要对当日候选合约做 Black76 向量化，不应显著拖慢回测。

### 10.3 缺失值处理

原则：

```text
缺失不等于 0；
缺失不允许用未来值填补；
缺失应降低置信度或降级为 B2c 原预算。
```

建议：

- 缺少 forward variance：B3a 该项记为中性分，不惩罚也不奖励。
- 缺少 VOV 历史：前 20 个可用交易日不启用 VOV 倾斜。
- 缺少 IV shock 重估：该合约不参与 clean vega 加分，预算回落到 B2c。
- 某侧候选完全缺失：该侧预算为 0，不把另一侧自动放大到两倍。

## 11. 诊断输出

B3 必须新增以下诊断文件或字段：

```text
daily_product_side_clean_vega_score.csv
daily_product_side_budget.csv
daily_forward_variance_pressure.csv
daily_vol_of_vol_proxy.csv
daily_iv_shock_coverage.csv
daily_vomma_penalty.csv
daily_skew_stability.csv
```

订单级字段：

```text
b3_forward_variance_score
b3_vol_of_vol_score
b3_iv5_coverage
b3_iv10_coverage
b3_joint_stress_coverage
b3_vomma_penalty
b3_skew_stability_score
b3_clean_vega_score
b3_final_side_budget_pct
b3_budget_mult_vs_b2c
```

报告必须新增分层：

```text
clean_vega_score quintile
iv_shock_coverage quintile
vol_of_vol quintile
vomma_penalty quintile
forward_variance_pressure quintile
```

每个分层统计：

- 开仓权利金。
- 平仓后留存权利金。
- 留存率。
- S1 PnL / 毛权利金。
- vega_loss / 毛权利金。
- gamma_loss / 毛权利金。
- 止损次数。
- 止损损失。
- 最差单日贡献。
- Put/Call 拆分。
- 品种/板块拆分。

## 12. 成功标准

B3 相对 B2c 的主成功标准：

```text
1. CAGR 不低于 B2c，或最多小幅下降；
2. vega_loss / gross_open_premium 明显下降；
3. vega 绝对亏损不再扩大，最好收敛；
4. premium_retained_ratio 不低于 B2c；
5. max drawdown 不高于 B2c，最好向 2%以内收敛；
6. 最差单日亏损不高于 B2c；
7. 止损亏损金额不高于 B2c；
8. P/C 结构没有无法解释的长期单边偏移；
9. 年度和关键压力月份稳定性不恶化。
```

更严格的目标：

```text
长期目标：S1 年化收益靠近 6%，最大回撤不超过 2%；
质量目标：总 vega 归因转正，或至少 vega_loss/gross_premium 显著低于 B2c；
画像目标：在风险受控前提下，保留全品种、多执行价、小单腿、动态 P/C 的乐得式卖权结构。
```

## 13. 失败情形与解释路径

### 13.1 收益下降且 vega 未改善

说明 B3 指标只是砍掉了好权利金，没有识别真实 vega 风险。应检查：

- forward variance proxy 是否噪声太大。
- VOV 是否只是在惩罚正常波动。
- IV shock 情景是否过于保守。
- 商品期权的 IV 数据是否稳定。

### 13.2 收益提高但 vega 继续扩大

说明 B3 仍然追逐厚权利金，而不是 clean vega。应提高：

- IV shock coverage 权重。
- VOV penalty 权重。
- vomma penalty 权重。

并降低：

- 单纯 premium yield 权重。
- 单纯 variance carry 权重。

### 13.3 vega 改善但 gamma 恶化

说明策略可能从高 vega 品种转向高 gamma、近价或跳价敏感合约。应检查：

- gamma_rent_penalty 是否不足。
- stress loss 是否没有覆盖 spot move。
- 低价深虚值合约是否仍有异常跳价。

### 13.4 Put 或 Call 单侧失衡

如果 B3 自然形成长期 Put 偏置或 Call 偏置，不能立刻认为错误，但必须解释：

- 是该侧权利金质量确实更高？
- 还是某一侧 IV shock coverage 计算有偏？
- 是否样本期方向行情导致分数学习到隐性趋势？
- 是否 skew 指标缺失导致某侧过度加分？

### 13.5 改善只来自少数品种

如果 B3 只靠少数商品品种贡献超额，应标记为“品种集中风险”，但第一阶段不立即加板块风控。应先把集中度诊断写清楚，再在 B4 单独研究板块/相关性约束。

## 14. 回测设计

### 14.1 样本

正式样本：

```text
2022-01-04 至数据库可用最新日
```

同时分层：

```text
2022
2023
2024
2025
2026 已有数据
```

关键压力窗口：

```text
2022-04
2022-06/07
2025-03
2025-04
2026-03
```

### 14.2 对照组

必须保留：

```text
B1：流动性和持仓量排序基准
B2c：75% 品种间权利金质量倾斜
```

B3 的每一组都必须相对 B2c 和 B1 双重比较。

### 14.3 第一轮实验顺序

建议顺序：

```text
1. B3c：IV Shock Coverage Tilt
2. B3b：Vol-of-Vol Tilt
3. B3a：Forward Variance Tilt
4. B3d：Vomma Penalty Tilt
5. B3e：Clean Vega Composite
```

原因：

- IV shock coverage 最贴近 vega 亏损问题，经济含义最直接。
- VOV 是文献支持的独立风险因子，且实现相对容易。
- forward variance 对期限结构数据质量要求更高，适合作为第三步。
- vomma 有用，但可能与 IV shock coverage 高度相关，应单独验证增量。
- composite 必须在单项实验后做，避免不知道哪个因子有效。

## 15. 对当前 B2 结果的预期判断

基于 B2c 已有结果，B3 最可能有效的方向不是“进一步多卖”，而是：

```text
保留 B2c 的高毛权利金、高留存率品种；
降低那些毛权利金高但 IV shock coverage 差、VOV 高、vomma 高、skew 正在恶化的品种和侧别预算。
```

如果 B3 成功，预期表现应是：

- 毛开仓权利金可能略低于 B2c，但不应回落到 B1。
- 权利金留存率应高于 B2c 或至少持平。
- vega_loss / 毛权利金应明显下降。
- 止损次数和止损损失至少一项改善。
- 2025-04 这种冲击后降波月份不应被砍掉太多收益。
- 2026-03 这类升波/压力月份应明显少亏。

## 16. 一句话定义

B3 不是新的预测模型，也不是复杂风控叠加，而是 B2c 之后的“干净 vega 质量层”：

```text
在仍然只卖次月、低 Delta、全品种、约 50% 保证金、2.5x 止损的前提下，
用 forward variance、vol-of-vol、IV shock coverage、joint stress coverage、vomma 和 skew stability
区分“值得承保的厚权利金”和“只是更危险的厚权利金”，
并把结果转化为 product-side 风险预算倾斜，
目标是在保留 B2c theta 增厚的同时，让 vega 损耗率和尾部止损损耗下降。
```

## 17. 参考文献

1. Bakshi, G. and Kapadia, N. (2003). Delta-Hedged Gains and the Negative Market Volatility Risk Premium. Review of Financial Studies. https://doi.org/10.1093/rfs/hhg002
2. Bali, T. G. and Murray, S. (2013). Does Risk-Neutral Skewness Predict the Cross-Section of Equity Option Portfolio Returns? Journal of Financial and Quantitative Analysis. https://doi.org/10.1017/S0022109013000410
3. Bollerslev, T. and Todorov, V. (2009). Tails, Fears, and Risk Premia. SSRN. https://ssrn.com/abstract=1418488
4. Bondarenko, O. (2014). Why are Put Options So Expensive? Quarterly Journal of Finance. https://ssrn.com/abstract=375784
5. Carr, P. and Wu, L. (2007). Variance Risk Premia. SSRN. https://ssrn.com/abstract=577222
6. Coval, J. D. and Shumway, T. (2001). Expected Option Returns. Journal of Finance. https://deepblue.lib.umich.edu/handle/2027.42/74142
7. Dew-Becker, I., Giglio, S., Le, A. and Rodriguez, M. (2015). The Price of Variance Risk. SSRN. https://ssrn.com/abstract=2607370
8. Goyal, A. and Saretto, A. (2007). Option Returns and Volatility Mispricing. SSRN. https://ssrn.com/abstract=889947
9. Huang, D., Schlag, C., Shaliastovich, I. and Thimme, J. (2018). Volatility-of-Volatility Risk. SSRN. https://ssrn.com/abstract=2497759
10. Trolle, A. B. and Schwartz, E. S. (2010). Variance Risk Premia in Energy Commodities. The Journal of Derivatives. https://doi.org/10.3905/jod.2010.17.3.015
