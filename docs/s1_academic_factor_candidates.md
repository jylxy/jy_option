# S1 学术文献启发的下一批候选因子

更新日期：2026-04-28

本文档用于记录在 B2/B3 因子实验之后，基于期权收益、波动率风险溢价、尾部风险、流动性和期权曲面相关文献筛选出的下一批 S1 候选因子。它不是直接交易规则，而是下一轮 full shadow candidate universe 应该优先打标签、分层检验和再决定用途的因子池。

## 1. 当前判断

S1 的核心收益应来自卖方承接波动率、跳跃和流动性风险后的风险补偿，而不是简单的“高权利金”或“低 Delta”。B2c 的结果说明，premium quality 类因子能提高权利金收入和累计收益，但 vega / gamma 损耗也会随之扩大；B3 因子说明，直接用 clean vega 类指标做预算倾斜，还没有稳定打赢 B2c。

下一步不应马上继续调权重，而应把更多文献因子放进统一的 full shadow 样本中，按合约、品种-方向、品种、板块和 regime 分层，看它们分别预测：

- 未来净 PnL / premium
- 权利金留存率
- 止损概率
- 止损超越幅度
- vega PnL
- gamma PnL
- 最大不利路径

同时需要特别注意：S1 的研究对象不是单一预测目标，而是四个互相嵌套的决策层级。因子必须先归属到正确层级，再评估其有效性。否则会出现一个常见错误：把合约层有效的因子拿去选品种，或把环境因子拿去做合约排序，导致回测结果混乱且不可解释。

## 1.1 四个决策层级

S1 下一步实验应拆成四个层级，而不是把所有因子扔进一个总分：

| 决策层级 | 要回答的问题 | 主要样本单位 | 因子用途 |
| --- | --- | --- | --- |
| 选品种 | 今天哪些品种值得分配卖权预算？ | `product-date` | 品种级 carry、RV/IV、波动环境、容量、历史止损跳价 |
| 选方向 | 对某个品种，今天卖 Put、卖 Call，还是双卖？ | `product-side-date` | P/C skew、趋势/动量、上下尾溢价、需求压力 |
| 选合约 | 同一品种同一方向，卖哪个执行价/合约？ | `contract-date` | premium quality、theta/vega、breakeven cushion、gamma rent、摩擦成本 |
| 环境调节 | 当前环境下上述规则应该放大、正常、收缩还是暂停？ | `date` 或 `product-date` | 降波/升波、低波收缩、高波回落、趋势突破、stop cluster |

这四层的标签也应不同：

- 品种层标签：该品种若参与交易，未来组合贡献、权利金留存、止损概率、容量与跳价风险。
- 方向层标签：同一品种 Put 与 Call 的未来表现差异、哪一侧止损更多、哪一侧 vega/gamma 损耗更大。
- 合约层标签：单个候选合约的未来净 PnL、premium retained、stop flag、stop overshoot、vega/gamma PnL。
- 环境层标签：该环境下 S1 总体是否适合扩张预算，以及扩张后是否提高 Sharpe/Calmar 而非仅提高收益。

因此，下一步 full shadow 不应只输出一张合约候选表，而应派生出四张研究面板：

1. `product_date_panel`
2. `product_side_date_panel`
3. `contract_candidate_panel`
4. `regime_panel`

每张表各自做相关性矩阵、分层检验和正交化检验。

## 2. 核心文献线索

- Bakshi and Kapadia (2003), Delta-Hedged Gains and the Negative Market Volatility Risk Premium: delta-hedged option PnL 可用于识别波动率风险溢价，卖方长期收益本质上是承接负 volatility risk premium 的补偿。
- Goyal and Saretto (2009), Cross-section of Option Returns and Volatility: IV 与历史 RV 的差异可预测期权收益，是我们 variance carry / IV-RV 因子的主要依据。
- Carr and Wu (2009/2016), Analyzing Volatility Risk and Risk Premium in Option Contracts: 使用期权隐含方差和未来实现方差刻画 VRP，强调应以方差空间而不是简单 IV level 理解卖波收益。
- Bollerslev, Gibson and Zhou (2011), Variance Risk-Premium Dynamics: The Role of Jumps: VRP 中包含连续波动风险和跳跃风险，深虚值尾部期权包含重要 tail/jump 信息。
- Bollerslev and Todorov (2011), Tails, Fears and Risk Premia: OTM option wing 可提取市场恐惧和尾部风险补偿，适合转化为 put wing / call wing tail premium。
- Christoffersen, Fournier and Jacobs (2018), The Factor Structure in Equity Options: 期权收益受 volatility、skew、term structure 等曲面主成分驱动，支持我们把单合约 IV 扩展到曲面因子。
- Borochin, Wu and Zhao (2021), The Effect of Option-Implied Skewness on Delta- and Vega-Hedged Option Returns: implied skewness 对 hedged option returns 有解释力，提示 skew 既可能是溢价来源，也可能是尾部风险警报。
- Option liquidity / stock illiquidity and option returns 相关文献：流动性差会提高要求收益，但也会侵蚀实际成交，适合做 premium net of friction，而不是简单黑名单。
- Cui, Li, Wu and Yu (2019), Variance Risk Premium and Return Predictability: Evidence from Chinese SSE 50 ETF Options: 中国 50ETF 期权同样存在 VRP，且 upside / downside VRP 可拆分，支持在国内市场做方向侧 VRP 因子。

## 3. 下一批建议因子

### 3.1 方差溢价与权利金质量

1. `variance_carry_forward`

定义：`target_iv^2 - rv_forecast^2`，其中 `rv_forecast` 使用只含历史信息的 RV5/RV10/RV20、EWMA 或 HAR-RV 预测。

用途：品种-方向预算和合约排序。若分层显示高 carry 桶的权利金留存率更高、止损率不升，则可提高预算。

建议层级：优先用于 `product-date` 和 `product-side-date`，谨慎用于单合约排序。

2. `premium_to_expected_variance_loss`

定义：`gross_premium_cash / expected_gamma_variance_loss`，其中 expected loss 由 gamma、spot、rv_forecast、DTE 近似。

用途：合约排序。它比单纯 `premium / margin` 更接近“我收的 theta 是否足够补偿 gamma 路径风险”。

建议层级：优先用于 `contract-date`。

3. `breakeven_cushion_to_forecast_rv`

定义：期权 breakeven 到 spot 的距离，除以未来持有期 forecast RV move。

用途：合约排序和止损概率预测。对低 Delta 合约尤其重要，因为表面低 Delta 不一定代表路径安全。

建议层级：优先用于 `contract-date`，也可聚合到 `product-side-date`。

4. `premium_to_tail_loss`

定义：`gross_premium_cash / scenario_tail_loss`，tail loss 可用 spot shock + IV shock + skew steepening 重估。

用途：风险预算裁剪。高权利金但尾部覆盖率低的合约不应被 B2c 式排序过度奖励。

建议层级：`contract-date` 和 `product-side-date`。

### 3.2 Vega、vol-of-vol 与二阶波动风险

5. `vega_carry_efficiency`

定义：`theta_cash / abs(cash_vega)` 或 `gross_premium_cash / abs(cash_vega)`。

用途：保留 theta 的同时控制 vega 暴露。该因子要和 premium quality 同时看，否则会偏向低 vega 但权利金很薄的合约。

建议层级：优先用于 `contract-date`。

6. `iv_shock_coverage`

定义：`gross_premium_cash / loss_under_iv_plus_5_or_10_vol`。

用途：vega 防守。B3c 已经有类似思想，但建议先在 full shadow 中按合约和品种-方向重新验证，而不是直接做组合预算倾斜。

建议层级：`contract-date`；若聚合到品种方向层，应使用分位数或加权均值，而不是简单平均。

7. `vomma_loss_ratio`

定义：`second_order_iv_loss / gross_premium_cash`，近似为 `0.5 * vomma * iv_shock^2 / premium`。

用途：识别 IV spike 中 vega 非线性扩大的合约。它更适合做惩罚项，不适合单独做正向排序。

建议层级：`contract-date` 风控惩罚。

8. `vol_of_vol_risk_premium`

定义：`premium / expected_iv_move_loss`，其中 expected IV move 来自历史 IV 变动波动率，而不是仅看 vol-of-vol 高低。

用途：重新处理 B3b。单纯高 vol-of-vol 可能意味着风险，也可能意味着市场给了更厚权利金；关键要看权利金是否覆盖 IV 变动风险。

建议层级：`product-date` 或 `product-side-date` 环境/预算调节，不建议直接做合约排序。

### 3.3 尾部、skew 与跳跃风险

9. `downside_tail_premium`

定义：put wing IV 相对 ATM IV 的斜率或价差，例如 `put_10d_iv - atm_iv`、`put_25d_iv - atm_iv`。

用途：卖 put 的方向预算。若高 downside tail premium 同时伴随 RV 不升、trend 不坏，可能是可收的恐惧溢价；若伴随 RV 上升和 trend 破位，则是危险信号。

建议层级：`product-side-date`。

10. `skew_steepening_speed`

定义：put skew 或 call skew 的 1 日 / 5 日变化。

用途：止损概率和降级冷却。skew 正在变陡时，即使 IV/RV carry 看起来为正，也不应加仓。

建议层级：`product-side-date` 和 `regime_panel`。

11. `jump_proxy_from_intraday_range`

定义：历史日内 high-low、overnight gap、分钟极端跳价频率和 stop overshoot 历史分位。

用途：补足我们刚刚讨论的止损跳价问题。该因子预测的不是平均收益，而是实际止损成交价越过阈值的幅度。

建议层级：`product-date` 风控与 `contract-date` 止损风险诊断。

12. `option_implied_tail_asymmetry`

定义：下尾风险溢价与上尾风险溢价之差，例如 put wing premium - call wing premium。

用途：P/C 偏移。它比单纯趋势更接近期权市场对左右尾的定价。

建议层级：`product-side-date`。

### 3.4 曲面结构与跨期限信息

13. `forward_variance_slope`

定义：由近月和次月 IV 推出的 forward variance，或简单使用 `next_iv^2 - near_iv^2` 的期限结构斜率。

用途：判断市场是在短期事件升波，还是远端风险溢价整体抬升。对“只卖次月”仍有意义，因为其他期限是环境信息。

建议层级：`product-date` 和 `regime_panel`。

14. `surface_pca_score`

定义：对每个品种的 ATM IV、put wing、call wing、term slope、butterfly 做滚动 PCA 或标准化综合主成分。

用途：压缩曲面信息，避免单一 IV 指标过拟合。先用于分层诊断，不急着交易化。

建议层级：`product-date`。

15. `smile_curvature_butterfly`

定义：`(put_25d_iv + call_25d_iv) / 2 - atm_iv`。

用途：识别两侧尾部都昂贵的震荡卖权环境。若 butterfly 高但 realized jump 风险低，可能适合双卖；若 butterfly 高且 jump proxy 高，则可能是市场正确定价尾部。

建议层级：`product-side-date` 和 `regime_panel`。

### 3.5 流动性、容量和成交质量

16. `premium_net_of_friction`

定义：`gross_premium_cash - fee - estimated_open_slippage - expected_close_slippage`。

用途：合约排序。不要只看成交量/持仓量，也不要只看权利金，要看扣除成本后的可留存权利金。

建议层级：`contract-date`。

17. `friction_to_premium`

定义：`roundtrip_fee_and_slippage / gross_premium_cash`。

用途：硬过滤或强惩罚。低价合约和 ETF/股指品种的手续费差异会显著影响这个指标。

建议层级：`contract-date`。

18. `liquidity_rent_score`

定义：`premium_yield - expected_friction - illiquidity_tail_penalty`。

用途：把“流动性差但权利金厚”与“流动性差且根本不够补偿”区分开。

建议层级：`product-date` 和 `contract-date`，但要分开检验。

19. `stop_overshoot_prior`

定义：过去 N 日或历史同品种止损事件中，`raw_stop_execution_price / stop_threshold - 1` 的分位。

用途：止损风控。它应该进入报告和后续实盘监控，因为固定滑点无法覆盖跳价风险。

建议层级：`product-date` 和 `contract-date`，不建议作为收益型排序因子。

### 3.6 订单流和需求压力

20. `put_call_volume_imbalance`

定义：put volume / call volume、put OI / call OI、按 Delta 桶的 P/C 结构。

用途：方向侧预算和尾部风险识别。它不是趋势预测本身，而是期权市场需求压力。

建议层级：`product-side-date`。

21. `wing_demand_pressure`

定义：OTM put/call 的成交量、持仓量、IV 变化共同打分。

用途：识别某一侧保护需求正在拥挤。若需求压力高但权利金覆盖足够，可能有溢价；若需求压力升且 skew steepening，则应防守。

建议层级：`product-side-date`。

## 4. 暂不建议优先使用的方向

- 纯 IV rank：太粗，容易把“危险的高 IV”和“可收的高 IV”混在一起。
- 纯高 premium / margin：B2c 已证明能增厚收入，但也会增大 vega/gamma 损耗。
- 纯 vol-of-vol 反向或正向使用：高 vol-of-vol 既可能是风险，也可能是溢价来源，必须改为 coverage 口径。
- 复杂 LSTM / Transformer：在当前阶段不如 full shadow 分层清楚，且更容易过拟合。
- 单股基本面因子：对中国商品期权和指数/ETF 混合池的适配性弱，可放后面。
- Rho：短期限、低利率变化环境下不是当前主要矛盾，可先记录但不进入主线。

## 5. 下一步实验建议

建议先做 B4 full shadow 扩展，而不是直接上线新策略：

1. 在 B1 基准候选池上，加入上面 21 个因子中可立即计算的字段。
2. 从同一批候选中派生 `product_date_panel`、`product_side_date_panel`、`contract_candidate_panel`、`regime_panel` 四张表。
3. 标签维度同时包含收益、止损、vega/gamma 归因、权利金留存和 stop overshoot。
4. 每个因子只在其经济含义对应的层级上做 Rank IC 和 Q1-Q5 分层。
5. 分 Put / Call、商品 / 股指 / ETF、低波 / 正常 / 升波 / 降波环境做切片。
6. 先做相关性矩阵，再做因子族聚类，避免把同一信息重复加权。
7. 对核心主因子做正交化检验，例如控制 B2c premium quality 后，再看 vega coverage、tail/skew、liquidity rent 和 stop overshoot 是否还有 residual IC。
8. 只有通过稳定分层检验且有增量解释的因子，再进入 B4 策略实验。

## 5.1 分层与正交化流程

每个层级都应先做相关性矩阵，再做正交化：

1. `contract-date`：先看合约层因子之间相关性，重点识别 premium、theta、vega、gamma、friction 是否只是同一信息的不同写法。
2. `product-side-date`：先看 P/C skew、tail premium、趋势、需求压力之间是否共线，避免方向偏移规则重复表达“趋势”。
3. `product-date`：先看 IV/RV、vol-of-vol、term structure、liquidity、历史 stop overshoot 是否形成不同因子族。
4. `regime_panel`：先看降波、升波、低波收缩、stop cluster、市场相关性是否能解释不同阶段的 S1 预算上限。

正交化不建议一开始使用复杂机器学习。初始可采用：

- Spearman 相关矩阵。
- 层次聚类选代表因子。
- 分层控制，例如在同一 premium quality 分位内，再看 vega coverage 的 Q1-Q5。
- 线性残差法，例如 `outcome ~ premium_quality + residual_factor`。
- 分年份和 regime 稳定性检验。

通过标准不是单一 IC，而是：

- 对目标层级的标签有解释力。
- 分层方向稳定。
- 对止损、vega/gamma 或权利金留存有明确增量。
- 与主因子不过度共线，或共线但经济解释更强、实盘更可控。

## 5.2 因子用途归类

通过 full shadow 检验后，因子只能进入其适合的位置：

- 选品种因子：决定品种是否分配预算、预算大小和是否进入冷却。
- 选方向因子：决定 Put / Call / 双卖比例，而不是直接决定合约。
- 选合约因子：决定同一品种同一侧中哪个执行价、哪个低 Delta 合约优先。
- 环境因子：决定总预算、是否放大、是否暂停、是否降低止损后重开速度。

这套约束很重要。若一个因子只在合约层有效，就不能直接拿去做品种预算；若一个因子只解释环境，就不能拿去在同一天同品种候选合约之间排序。

初步策略实验顺序：

1. B4a：variance carry + breakeven cushion 排序。
2. B4b：tail/skew adjusted premium quality 排序。
3. B4c：vega coverage + vomma penalty 惩罚。
4. B4d：liquidity rent + stop overshoot penalty。
5. B4e：通过 shadow 检验后的 composite score，不再手工拍权重。

## 6. 参考文献链接

- [Bakshi and Kapadia, Delta-Hedged Gains and the Negative Market Volatility Risk Premium](https://academic.oup.com/rfs/article/16/2/527/1579962)
- [Goyal and Saretto, Cross-section of Option Returns and Volatility](https://www.sciencedirect.com/science/article/pii/S0304405X09001251)
- [Carr and Wu, Analyzing Volatility Risk and Risk Premium in Option Contracts](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1701685)
- [Bollerslev, Gibson and Zhou, Variance Risk-Premium Dynamics: The Role of Jumps](https://academic.oup.com/rfs/article/23/1/345/1578053)
- [Bollerslev and Todorov, Tails, Fears and Risk Premia](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1589719)
- [Christoffersen, Fournier and Jacobs, The Factor Structure in Equity Options](https://academic.oup.com/rfs/article/31/2/595/4060546)
- [Borochin, Wu and Zhao, The Effect of Option-implied Skewness on Delta- and Vega-Hedged Option Returns](https://www.sciencedirect.com/science/article/pii/S1042443121001244)
- [Cao and Han, Cross section of option returns and idiosyncratic stock volatility](https://www.sciencedirect.com/science/article/abs/pii/S0304405X12002450)
- [Cui, Li, Wu and Yu, Variance Risk Premium and Return Predictability: Evidence from Chinese SSE 50 ETF Options](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3426118)
- [The volatility index and volatility risk premium in China](https://www.sciencedirect.com/science/article/abs/pii/S1062976923000789)
