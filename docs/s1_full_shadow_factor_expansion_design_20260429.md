# S1 Full Shadow 因子扩展设计：P/C、曲面、跳跃、到期分散与组合尾部

文档日期：2026-04-29  
策略对象：S1 日频低 Delta 卖权策略  
当前基准：B1 liquidity/OI ranking、B2c product tilt、B4 去共线角色分层实验  
本文定位：在不直接修改交易规则的前提下，整理下一轮 full shadow 应记录和检验的候选因子，并标明与 B2/B3/B4 的重复关系、增量信息和适用层级。

## 1. 研究目的

本轮因子扩展不是为了马上形成 B5 交易规则，而是为了先建立一套更完整的候选因子研究面板。原因是 S1 的决策并不是单一排序问题，而是至少包含四个不同目标：

| 决策层级 | 样本单位 | 核心问题 | 主要标签 |
| --- | --- | --- | --- |
| 选品种 | `signal_date + product` | 哪些品种今天值得分配卖权预算？ | 品种未来 PnL、权利金留存、止损率、跳价损失、保证金占用效率 |
| 选方向 | `signal_date + product + option_type` | 同一品种今天更适合卖 Put、卖 Call，还是双卖？ | Put-Call 相对收益、侧向止损率、侧向 vega/gamma 损耗 |
| 选合约 | `signal_date + product + option_type + expiry + strike + code` | 同一品种同一侧的次月低 Delta 合约，应该优先卖哪张？ | 单合约 PnL/margin、theta capture、stop hit、MAE、vega/gamma 归因 |
| 环境调节 | `signal_date` 或 `signal_date + product` | 当前环境应该放大、正常、收缩还是暂停卖权预算？ | 组合回撤、stop cluster、vega loss/gross premium、stress loss |

因此，所有新因子都必须先回答两个问题：

| 问题 | 要求 |
| --- | --- |
| 它预测什么？ | 收益、止损、vega/gamma 损耗、执行滑点、到期集中风险，必须明确其中之一。 |
| 它用在哪里？ | 只能进入适合的层级，不能把合约层有效因子直接拿去做品种预算，也不能把组合层风险指标拿去做合约排序。 |

### 1.1 核心研究口径：先 full shadow，不直接进 B5

本轮新增因子应先进入 `full shadow factor universe` 做标签监测，不应直接进入交易规则。原因是这些因子属于不同决策层级：有些负责 P/C 方向选择，有些负责品种预算，有些负责组合到期分散，有些负责止损与执行质量。如果直接放入 B5，容易把“有效 alpha 因子”和“组合约束/执行卫生条件”混在一起，导致回测超额无法归因。

| 因子族 | 用途 | 核心想法 | 更适合的位置 |
| --- | --- | --- | --- |
| 趋势/动量 | P/C 选择 | 上涨趋势可提高 Put 权重、压低 Call 权重；下跌趋势反过来；震荡环境更适合维持双卖。 | `P/C budget tilt`、`side selection` |
| 到期日分散 | 组合效率 | 即使都卖次月，不同品种到期日也会聚集；同到期聚集会放大同一周 gamma、止损和保证金滚动压力。 | `portfolio risk`、`expiry budget` |
| Skew | P/C 与合约选择 | Put skew 贵不一定是机会，需要结合趋势和 RV；稳定环境下可能是溢价，趋势恶化时可能是尾部风险定价。 | `side selection`、`contract ranking` |
| 最大不利移动 | 风险预算 | 用历史 MAE、gap 和极端路径衡量权利金缓冲是否够厚，避免“权利金看着高但一跳就爆”。 | `budget sizing`、`risk penalty` |
| 跳价/异常价 | 执行与止损 | 识别瞬时跳变又回来的假价格，减少假止损和低流动性污染。 | `stop guard`、`liquidity filter` |
| DTE/滚动效率 | 期限结构 | 次月内部也有 DTE 差异；太近 gamma 太重，太远 theta 效率弱，且到期日集中会影响资金滚动。 | `contract ranking`、`expiry dispersion` |

学术上，本轮因子应服务于短期权收益分解：

```text
short option PnL
= variance risk premium
+ theta carry
- realized variance / gamma cost
- IV shock / vega loss
- skew and jump risk
- transaction cost
- execution slippage
```

因此，新因子不是为了简单预测标的涨跌，而是分别回答四类问题：

| 问题 | 对应交易决策 |
| --- | --- |
| 哪一侧更安全？ | Put / Call / 双卖比例 |
| 哪个品种的权利金质量更高？ | 品种预算分配 |
| 哪个合约的风险调整收益更好？ | 同品种同方向合约排序 |
| 组合什么时候因为到期、跳价或集中度而脆弱？ | 到期分散、板块/方向暴露、止损控制 |

### 1.2 Full shadow 标签监测要求

下一轮 full shadow 需要显式输出四层标签，而不是只输出合约未来收益。不同层级的因子必须在对应层级检验。

| 层级 | 预测目标 | 标签示例 |
| --- | --- | --- |
| 合约层 | 这个合约值不值得卖 | `forward_pnl_per_margin`、`theta_capture`、`vega_pnl`、`gamma_pnl`、`stop_hit`、`max_adverse_pnl` |
| P/C 侧 | 今天该偏 Put 还是 Call | `put_minus_call_outcome`、`side_stop_rate`、`side_theta_vega`、`side_drawdown` |
| 品种层 | 这个品种该不该多分预算 | `product_forward_pnl`、`stop_cluster`、`premium_retention`、`margin_efficiency` |
| 组合层 | 到期日、板块或方向是否过度集中 | `expiry_cluster_loss`、`same_expiry_gamma_loss`、`roll_efficiency`、`stress_loss` |

分析上必须先做因子相关性矩阵，再做 Q1-Q5 分层、累计 IC、正交化 IC 和 regime 切片。特别是：

| 因子类型 | 检验口径 |
| --- | --- |
| P/C 因子 | 单独按趋势、skew、RV regime 分组，不与合约排序混看。 |
| 到期分散因子 | 按组合层看，不用普通合约 IC 强行评价。 |
| 跳价因子 | 重点看 false stop、stop slippage、下一分钟或下一日回归概率。 |

## 2. 与 B2/B3/B4 的重复关系总览

| 因子族 | 用户新增想法 | B2/B3/B4 已覆盖内容 | 本轮增量 | full shadow 处理建议 |
| --- | --- | --- | --- | --- |
| 权利金质量 | premium/margin、theta/margin、premium/stress、premium/expected move | B2 已有 `premium_yield_margin`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`theta_vega_efficiency`、`gamma_rent_penalty`；B4 已用去共线版本 | 增加 `premium_to_expected_move_loss`、`premium_to_tail_move_loss`、`premium_to_mae_loss`，把风险分母从制度保证金扩展到真实路径风险 | 保留 B2/B4 字段，同时新增 MAE/tail/expected move 覆盖率，主要用于 contract 和 product-side |
| IV/RV 与方差溢价 | IV/RV、variance carry、低波/高波状态 | B2 已有 `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate`，但审计显示当前 IC 不理想 | 改为 forward RV / HAR-RV / EWMA RV 口径，避免只用历史 RV 当未来风险 | 新 shadow 中保留旧字段作为对照，新增 `variance_carry_forward` 和 `rv_forecast_*` |
| IV 动量与降波 | IV momentum、IV acceleration、IV mean reversion | B3 已有 `contract_iv_change_1d/3d/5d`、`entry_iv_trend`、`b3_forward_variance_pressure` | 增加 product ATM IV 层面的 `atm_iv_mom`、`atm_iv_accel`、`iv_reversion_state`，用于判断降波能否加预算 | 主要用于 product-date 和 product-side，不直接做合约排序 |
| Vol-of-vol / Vomma | vol-of-vol、vomma、IV shock 后覆盖率 | B3 已有 `b3_vol_of_vol_proxy`、`b3_vomma_loss_ratio`；B4 已把它们做惩罚项 | 把单纯 vov 高低改成 `premium / expected_iv_move_loss`，即 vol-of-vol 是否已经被权利金补偿 | 重新测 `vol_of_vol_risk_premium`，避免简单地“高 vov 就减仓” |
| Skew 与曲面 | skew richness、curvature、wing relative value、term slope | B3 已有 `contract_iv_skew_to_atm`、`contract_skew_change_for_vega`、`b3_skew_steepening`、`b3_term_structure_pressure` | 新增完整曲面形状：put/call skew、risk reversal、butterfly curvature、wing local richness | 主要用于 P/C 方向选择和双卖环境判断 |
| 趋势与动量 | trend、breakout、trend persistence、pullback absence、trend z | 旧规则曾有趋势/动量思想，但 B4 当前主线没有完整纳入 | 作为 P/C budget tilt 的核心候选，不直接变成看涨/看跌押注 | 用于 product-side，检验是否能改善 Put/Call 选择 |
| 最大不利移动与跳跃 | MAE、jump share、gap share、range expansion、stop overshoot | B2/B4 有 stress loss，但主要是模型压力，不是历史路径跳跃；异常价过滤已有部分逻辑 | 增加基于标的分钟/日线的 MAE、gap、jump、range 扩张，以及期权止损后是否反转 | 用于止损概率、止损滑点、风控惩罚，不作为收益因子 |
| 到期与滚动效率 | DTE sweet spot、到期日分散、capital lock-up days | B0/B1 有“次月”与 DTE 字段；B4 未单独做 expiry dispersion | 即使只卖次月，也要控制不同品种到期日集中导致的 gamma/保证金滚动压力 | 生成 expiry cluster 面板，用组合层标签验证 |
| 保证金与资金效率 | marginal margin、margin shock、premium/stressed margin、capital days | 当前已修交易所保证金口径，B2 有 premium/margin；B4 仍主要用单笔估算 | 新增边际保证金、压力保证金、资金锁定天数 | 用于 product/portfolio 预算，不直接替代收益因子 |
| 组合相关性 | tail correlation、stop clustering、sector/directional crowding | 已有板块/相关性风控思路，B4 当前仍轻量 | 将相关性从收益相关扩展到止损同日、尾部同向和 stress loss 聚集 | 用于组合层预算和风险报告 |
| 估值与数据质量 | IV solve quality、PCP deviation、stale price、tick discreteness | 已处理真实 spot、PCP 问题、低价过滤和异常跳价；但字段化不足 | 将数据质量变成 shadow 因子，判断哪些合约回测信号不可靠 | 主要用于硬过滤、报告诊断和执行风控 |
| Ratio / 国优式结构 | sweet width、breakeven distance、tail slope、wing conversion cost、ratio fragility | 当前 S1 主线是纯卖权，不是 ratio；之前讨论过但未进入 B4 | 作为未来结构扩展，不应混入当前 B0/B1/B4 基准 full shadow 主线 | 单独保留为结构研究池，暂不影响 S1 纯卖权实验 |

## 3. Product-date 因子：选品种和环境预算

Product-date 因子回答“今天这个品种是否值得承保”。它不直接决定卖哪张合约，而是决定这个品种能否获得更多预算、是否进入冷却、是否需要降低止损后重开速度。

| 因子族 | 建议字段 | 定义草案 | 与现有重复关系 | 标签目标 | 使用方式 |
| --- | --- | --- | --- | --- | --- |
| Forward RV | `rv_forecast_5d`、`rv_forecast_10d`、`rv_forecast_har` | 用 T 日及以前 RV5/RV10/RV20、EWMA 或 HAR 预测未来 RV | 升级 B2 的 `rv_ref` | product PnL、stop rate、vega/gamma loss | 先做研究字段，后续服务 variance carry |
| 方差溢价 | `variance_carry_forward` | `target_iv^2 - rv_forecast^2` | 重构 B2 `variance_carry` | premium retention、PnL/margin | 品种预算候选，不直接硬过滤 |
| IV 状态 | `atm_iv_mom_5d`、`atm_iv_accel`、`iv_reversion_score` | ATM IV 的短期动量、加速度、分位与回落状态 | 扩展 B3 contract IV trend | vega PnL、stop hit | 判断降波加预算或升波降预算 |
| Vol-of-vol 覆盖 | `vol_of_vol_risk_premium` | `net_premium_proxy / expected_iv_move_loss` | 升级 B3 `b3_vol_of_vol_proxy` | vega loss/gross premium | 环境预算调节 |
| 跳跃风险 | `jump_share_20d` | 大分钟收益平方和 / 全部分钟收益平方和 | 新增，需要标的分钟数据 | stop overshoot、max adverse pnl | 风险惩罚 |
| Gap 风险 | `gap_share_20d`、`gap_frequency_20d` | 开盘跳空占日内 range 比例及频率 | 新增 | stop slippage、stop hit | 风险惩罚 |
| Range 扩张 | `range_expansion_5d`、`range_expansion_20d` | 当前 high-low range / 历史均值 | 新增 | regime shift、stop cluster | 升波预警 |
| 资金锁定 | `capital_lockup_days`、`premium_per_capital_day` | `margin * expected_holding_days` 与权利金之比 | 扩展 B2 premium/margin | PnL/margin、turnover efficiency | 预算效率评估 |
| 历史止损质量 | `stop_overshoot_prior`、`false_stop_prior` | 历史止损超过阈值幅度、止损后反转概率 | 新增，依赖已有回测/影子标签 | stop slippage、post-stop reversal | 止损风控与冷却期 |

## 4. Product-side 因子：P/C 选择与双卖比例

Product-side 因子回答“同一品种今天卖 Put 还是卖 Call”。这类因子的目标不是做方向预测，而是减少卖在趋势突破方向、skew 快速变陡方向和需求压力拥挤方向。

| 因子族 | 建议字段 | 定义草案 | 与现有重复关系 | 标签目标 | 使用方式 |
| --- | --- | --- | --- | --- | --- |
| 趋势强度 | `trend_z_20d` | 20 日收益 / 20 日实现波动 | 新增 | put-call relative pnl、side stop rate | P/C budget tilt |
| 动量分层 | `mom_5d`、`mom_20d`、`mom_60d` | 标的历史收益 | 新增 | side PnL、side stop rate | 简单趋势基线 |
| 突破距离 | `breakout_distance_up_60d`、`breakout_distance_down_60d` | 到 60 日新高/新低的距离 | 新增 | Call/Put stop hit | 趋势方向降权 |
| 趋势持续 | `up_day_ratio_20d`、`down_day_ratio_20d` | 过去 20 日上涨/下跌天数比例 | 新增 | side gamma loss | P/C 选择辅助 |
| 回撤缺失 | `days_since_pullback`、`days_since_ma_touch` | 距离最近有效回调或均线触碰天数 | 新增 | squeeze/trend continuation loss | 趋势方向卖权惩罚 |
| Put skew richness | `put_skew_10d`、`put_skew_percentile` | `IV_10d_put - IV_ATM` 及历史分位 | 扩展 B3 skew | put premium retention、put stop rate | Put 侧预算候选 |
| Call skew richness | `call_skew_10d`、`call_skew_percentile` | `IV_10d_call - IV_ATM` 及历史分位 | 扩展 B3 skew | call premium retention、call stop rate | Call 侧预算候选 |
| Risk reversal | `risk_reversal_25d` | `IV_25d_call - IV_25d_put` | 新增曲面字段 | put-call relative pnl | P/C tilt |
| Skew steepening | `put_skew_change_5d`、`call_skew_change_5d` | 左右尾 skew 的短期变化 | B3 已有合约近似版本 | side stop rate、vega loss | 方向侧风险惩罚 |
| P/C 需求压力 | `pc_volume_imbalance`、`pc_oi_imbalance`、`wing_demand_pressure` | Put/Call 成交、持仓与 wing IV 同步变化 | 新增 | side stop rate、skew reprice | 方向侧预算或拥挤惩罚 |

## 5. Contract-date 因子：同品种同方向内选合约

Contract-date 因子回答“同一品种、同一方向、同一次月里面，哪张合约的权利金质量更好”。这一层是 B2/B4 当前最有效的落点。

| 因子族 | 建议字段 | 定义草案 | 与现有重复关系 | 标签目标 | 使用方式 |
| --- | --- | --- | --- | --- | --- |
| IV shock 覆盖 | `premium_to_iv10_loss` | 已有，权利金 / IV +10vol 损失 | B2/B4 已用 | PnL/margin、vega loss | 保留代表因子 |
| Stress 覆盖 | `premium_to_stress_loss` | 已有，权利金 / spot+IV 压力损失 | B2/B4 已用 | stress pnl、stop hit | 保留代表因子 |
| Gamma 租金 | `gamma_rent_penalty`、`theta_per_gamma` | 已有 gamma rent，新增显式 theta/gamma 比 | B2/B4 已用部分 | gamma loss、stop hit | 排序与惩罚 |
| Vega 租金 | `theta_per_vega`、`premium_per_vega` | theta 或权利金 / abs(vega) | B2 已有 `theta_vega_efficiency` | vega loss/gross premium | 控制 short vega 质量 |
| Expected move 覆盖 | `premium_to_expected_move_loss` | 权利金 / forecast RV 下预期不利损失 | B2 尚未充分覆盖 | future pnl、MAE | 新增排序候选 |
| Tail move 覆盖 | `premium_to_tail_move_loss` | 权利金 / 历史 95% 不利移动损失 | 新增 | stop hit、tail pnl | 风险惩罚 |
| MAE 覆盖 | `premium_to_mae20_loss` | 权利金 / 过去 20 日最大不利路径估算损失 | 新增 | max adverse pnl、stop hit | 路径风险排序 |
| Tick 离散 | `tick_value_ratio` | 最小变动价位 / 期权价格 | 新增，但与低价过滤相关 | stop slippage、false stop | 硬过滤或强惩罚 |
| Stale price | `last_trade_age`、`stale_price_ratio` | 最近成交距当前时间、无成交报价占比 | 新增，需行情字段支持 | execution quality | 硬过滤 |
| IV 可解质量 | `iv_solve_fail_rate`、`iv_outlier_rate` | IV 反解失败或异常比例 | 新增 | signal reliability | 数据质量过滤 |
| PCP 偏离 | `pcp_deviation` | 同到期 C/P 配对偏离 | 与 spot 修复议题相关 | data error、mispricing | 诊断，不先交易化 |
| Wing 局部相对价值 | `wing_richness_local` | 短腿 IV 相对相邻行权价插值 IV 的偏离 | 新增 | contract pnl、retention | 合约排序候选 |

## 6. Portfolio / Regime 因子：组合风险和到期分散

组合层因子不能用普通合约 IC 评价。它们更适合看组合回撤、stop cluster、保证金滚动和尾部暴露。

| 因子族 | 建议字段 | 定义草案 | 与现有重复关系 | 标签目标 | 使用方式 |
| --- | --- | --- | --- | --- | --- |
| 到期集中 | `expiry_cluster_weight` | 同一到期日的候选权利金、保证金或 stress loss 占比 | 新增 | same-expiry drawdown、roll efficiency | 到期预算上限 |
| DTE 分桶 | `dte_bucket`、`dte_sweet_spot_score` | 10-15、16-25、26-35、36-50 等分桶 | DTE 已有但未系统分层 | PnL/margin、gamma loss | 期限效率研究 |
| 边际保证金 | `marginal_margin_estimate` | 加入候选后保证金增加额 | 当前主要是逐笔估算 | margin efficiency | 组合预算 |
| 压力保证金 | `margin_shock_sensitivity` | 压力行情后保证金 / 当前保证金 | 新增 | margin squeeze | 预算风控 |
| 板块拥挤 | `sector_stress_exposure` | 按板块聚合 stress loss | 已有板块风控思想 | drawdown、tail loss | 风控上限 |
| 方向拥挤 | `portfolio_put_stress`、`portfolio_call_stress` | Put/Call 侧 stress loss 聚合 | 已讨论 P/C 偏移 | directional drawdown | P/C 总预算 |
| 尾部相关 | `up_tail_corr`、`down_tail_corr` | 上涨/下跌不利行情中的相关性 | 扩展相关性监控 | stop cluster、drawdown | 组合风险 |
| 止损聚集 | `stop_cluster_score` | 品种历史止损日与其他品种同日止损概率 | 新增 | portfolio drawdown | 风控惩罚 |
| Regime 扩张 | `falling_vol_budget_state`、`rising_vol_budget_state` | 降波、升波、低波收缩、事件/跳跃状态 | 与前期 falling framework 相关 | portfolio Sharpe/Calmar | 总预算调节 |

## 7. Ratio / 国优式结构扩展池

以下因子与当前 S1 纯卖权 B0/B1/B4 主线不同，更适合未来研究 ratio 或买近卖远结构。它们应记录在因子库，但不建议混入当前 full shadow 主表，避免污染“纯卖权”基准。

| 因子族 | 建议字段 | 定义草案 | 适用结构 | 暂不进入主线原因 |
| --- | --- | --- | --- | --- |
| 盈利区间宽度 | `sweet_width`、`sweet_width_pct` | Ratio 中长腿和短腿之间的宽度 | Ratio、断翅蝶 | 当前 S1 不买保护腿 |
| 盈亏平衡距离 | `ratio_breakeven_distance` | 穿过短腿后的真实盈亏平衡点距离 spot | Ratio | 当前单腿卖权没有同样 payoff |
| 尾部斜率 | `tail_slope` | 穿透短腿后每 1% 标的移动亏损 | Ratio | 纯卖权 tail slope 定义不同 |
| 转断翅成本 | `wing_conversion_cost_now`、`wing_conversion_cost_if_stop` | 当前或止损时补保护腿成本 | Ratio 风控 | 当前止损不是转结构 |
| Ratio 脆弱度 | `ratio_fragility_score` | tail slope + delta acceleration + wing cost - sweet width - credit buffer | Ratio 选结构 | 需要单独 payoff 引擎 |

## 8. Full Shadow 标签设计

下一轮 full shadow 不应只输出未来净 PnL，而应同时输出收益、路径、止损、希腊字母和执行质量标签。

| 标签族 | 字段 | 说明 | 对应因子层 |
| --- | --- | --- | --- |
| 收益标签 | `future_net_pnl`、`future_net_pnl_per_margin`、`future_net_pnl_per_premium` | 单合约或聚合层未来净收益 | 所有层 |
| 权利金留存 | `future_retained_ratio`、`future_premium_capture` | 收到的权利金最终留住多少 | contract、product-side |
| 止损标签 | `future_stop_hit`、`future_stop_loss_cash`、`future_stop_overshoot` | 是否止损、止损亏损、越过阈值幅度 | contract、product、execution |
| 路径标签 | `future_mae_pnl`、`future_mfe_pnl`、`future_max_adverse_underlying_move` | 持有期最大不利/有利路径 | contract、product-side |
| Greek 标签 | `future_delta_pnl`、`future_gamma_pnl`、`future_theta_pnl`、`future_vega_pnl`、`future_residual_pnl` | 判断是否真的赚 theta/vega | contract、product-side、portfolio |
| P/C 标签 | `future_put_minus_call_pnl`、`future_side_stop_diff` | 同品种 Put 与 Call 相对表现 | product-side |
| 组合标签 | `future_expiry_cluster_drawdown`、`future_stop_cluster_count`、`future_margin_efficiency` | 到期集中、止损聚集和保证金滚动效率 | portfolio |
| 执行标签 | `future_false_stop_reversal`、`future_stop_reversal_1d`、`future_stale_price_flag` | 止损后是否反转、价格是否不可靠 | execution |

## 9. 分层检验与正交化要求

| 检验 | 方法 | 目的 |
| --- | --- | --- |
| 分层单调性 | 每个因子按 Q1-Q5 分层，观察 PnL、stop、vega/gamma、MAE | 判断是否有可交易方向，而不是只看平均 IC |
| Rank IC | 按层级计算 Spearman IC，合约层、product-side 层、product 层分开 | 避免把合约排序因子误用于品种预算 |
| 累计 IC | 按信号日滚动累加 IC | 判断稳定性和是否只来自少数月份 |
| 相关性矩阵 | 同层级因子 Spearman 相关 | 识别 B2/B3/B4 那类高共线重复计票 |
| 因子族聚类 | 对高相关因子只保留代表或构造族群分 | 防止综合分重复奖励同一信息 |
| 正交化 IC | 控制 premium、margin、DTE、delta、stress 后看残差 IC | 判断新因子是否有增量信息 |
| Regime 切片 | 低波、升波、降波、趋势、跳跃、商品/股指/ETF 分组 | 判断因子在哪些环境可用 |
| P/C 交互检验 | trend × skew、trend × risk reversal、RV × skew 二维分组 | 判断 Put/Call 偏移是否应条件化 |

## 10. 未来函数与口径约束

| 约束 | 规则 |
| --- | --- |
| 信号时间 | 所有因子只允许使用 T 日及以前可见数据，交易结果从 T+1 开始。 |
| 历史分位 | rolling percentile、zscore、均值、标准差必须只用 `<= T` 的数据；若用于严格信号，建议均值方差用 `T-1` 之前窗口。 |
| 未来标签 | `future_*` 字段只能用于研究报告和因子审计，禁止回流到交易规则。 |
| 横截面 rank | T 日同层级横截面 rank 可以使用，因为当天所有候选在信号生成时可见。 |
| 到期集中 | 只能使用 T 日已经生成的候选和已有持仓，不得使用未来实际开仓结果。 |
| 历史止损质量 | 若使用 `stop_overshoot_prior`，必须用历史已发生止损事件，不得包含当前信号之后事件。 |
| 分钟跳跃 | 使用 T 日及以前标的分钟数据，不得使用 T+1 以后 high/low 预知未来路径。 |

## 11. B5 新增：止损后同品种冷静期因子

### 11.1 学术逻辑

止损后冷静期不应被设计成固定日历规则，而应设计成一个可检验的状态因子。Kaminski and Lo 的 stop-loss 框架指出，简单止损在随机游走环境下会降低期望收益，但在存在趋势或动量时可能增加价值；后续关于序列相关、regime switching 和交易成本的研究也强调，止损后的再入场政策是止损策略的一部分，而不是附属细节。

对 S1 卖权策略而言，止损后的关键问题不是“止损后等几天”，而是：

| 问题 | 卖权含义 |
| --- | --- |
| 止损后不利方向是否延续？ | 若延续，马上重开容易二次止损。 |
| IV、RV、skew 是否继续恶化？ | 若继续恶化，卖方仍在承接升波和尾部风险。 |
| 止损是否来自真实 regime 切换，还是低流动性跳价？ | 前者应冷却，后者应改执行确认而不是长期禁做。 |
| 同品种或同板块是否出现 stop cluster？ | 若集群化，说明风险不是单点噪声，而是组合层 regime。 |
| 重开的机会成本是否过高？ | 若止损后很快降波，过长冷静期会错过最赚钱的 theta/vega 修复。 |

因此，B5 的冷静期因子应先进入 full shadow，预测“止损后重开是否值得”，而不是先写死交易规则。

### 11.2 因子定义

冷静期因子应至少分为三层：同品种、同品种同方向、同板块。第一版优先实现同品种和同品种同方向。

| 因子族 | 建议字段 | 定义草案 | 使用层级 | 预期方向 |
| --- | --- | --- | --- | --- |
| 止损新近度 | `days_since_product_stop` | 距离该品种最近一次实际止损的交易日数 | product-date | 越短越谨慎 |
| 方向新近度 | `days_since_product_side_stop` | 距离该品种同 Put/Call 方向最近一次实际止损的交易日数 | product-side | 越短越谨慎 |
| 止损频率 | `product_stop_count_5d`、`product_stop_count_20d` | 过去 5/20 个交易日该品种止损次数 | product-date | 越多越谨慎 |
| 止损严重度 | `product_stop_loss_nav_20d` | 过去 20 日该品种止损亏损 / NAV | product-date | 越高越谨慎 |
| 止损越界 | `product_stop_overshoot_20d` | 实际止损成交价相对阈值的平均越界幅度 | product-date、execution | 越高越谨慎 |
| 二次止损倾向 | `repeat_stop_rate_prior` | 历史同品种止损后 N 日内再次止损概率，只能用 T 日以前历史 | product-date | 越高越谨慎 |
| 止损后趋势延续 | `post_stop_adverse_trend_state` | 最近一次止损后，标的是否继续朝卖方不利方向运动 | product-side | 延续则谨慎 |
| 止损后 IV 状态 | `post_stop_iv_change`、`post_stop_iv_release` | 最近一次止损后 ATM IV 是否继续上升或开始回落 | product-date | IV 回落可解除惩罚 |
| 止损后 RV 状态 | `post_stop_rv_change` | 最近一次止损后 RV 是否继续抬升 | product-date | RV 抬升则谨慎 |
| 止损后 skew 状态 | `post_stop_skew_change` | 最近一次止损后对应方向 skew 是否继续变陡 | product-side | skew 变陡则谨慎 |
| 冷静期释放分 | `cooldown_release_score` | 由止损新近度、IV/RV/skew 回落、趋势钝化共同构造 | product-side | 越高越可重开 |
| 冷静期惩罚分 | `cooldown_penalty_score` | 由止损频率、严重度、二次止损、stop cluster 构造 | product-side | 越高越应降预算 |

### 11.3 两类口径：实盘可用与研究诊断

冷静期因子需要区分“实盘可用状态”和“研究诊断标签”。

| 口径 | 字段来源 | 是否可进交易 | 说明 |
| --- | --- | --- | --- |
| 实盘可用冷静期 | 当前策略已经真实发生的止损日志，且只到 T 日以前 | 可以 | 如 `days_since_product_stop`、`product_stop_count_20d`、`post_stop_iv_change`。 |
| Shadow 诊断冷静期 | full shadow 中候选合约未来是否会止损、止损后是否反转 | 不可以直接进交易 | 只能用于判断冷静期规则有没有价值，例如二次止损率、错过收益、false stop。 |

第一版 B5 full shadow 应同时输出两类字段。交易化时只能使用实盘可用冷静期字段，不能把未来 shadow 标签回流。

### 11.4 冷静期标签

为了判断冷静期是否真的能减少止损，full shadow 需要补以下标签：

| 标签 | 定义 | 用途 |
| --- | --- | --- |
| `future_repeat_stop_5d` | 若当前重开，未来 5 日内是否再次止损 | 检验冷静期是否能避免连续止损 |
| `future_repeat_stop_10d` | 若当前重开，未来 10 日内是否再次止损 | 检验较长冷却窗口 |
| `future_reopen_pnl_per_margin` | 止损后重开候选的未来 PnL / margin | 衡量重开收益 |
| `future_missed_theta_if_blocked` | 如果因冷静期不做，错过的 theta 或净 PnL | 衡量机会成本 |
| `future_stop_reversal_1d` | 触发止损后 1 日内价格是否明显回落 | 识别假止损和噪声止损 |
| `future_stop_reversal_3d` | 触发止损后 3 日内价格是否明显回落 | 识别过紧止损 |
| `future_cooldown_value` | 避免的二次亏损 - 错过的净收益 | 直接衡量冷静期价值 |

### 11.5 冷静期候选规则，不直接上线

第一版只建议用于 shadow 检验，不直接改交易。若未来通过检验，可考虑三档状态：

| 状态 | 条件草案 | 交易含义 |
| --- | --- | --- |
| 硬冷却 | 最近 1-2 日同品种同方向止损，且 IV/RV/skew 仍在恶化 | 不新开同品种同方向 |
| 软冷却 | 最近 3-10 日止损，IV 不再上升但未明显回落 | 降低该 product-side 预算 |
| 释放 | IV 开始回落、RV 不再抬升、趋势不再延续、无 stop cluster | 恢复正常预算，甚至允许降波环境加权 |

这里的重点是：冷静期不等于永久黑名单。若止损后进入清晰降波环境，过长冷却会错过卖权最赚钱的阶段。

## 12. 为减少止损新增的因子池

以下因子目标不是提高平均收益，而是降低止损概率、降低二次止损、降低止损越界和减少 false stop。

| 因子族 | 建议字段 | 学术/经济逻辑 | 使用层级 |
| --- | --- | --- | --- |
| 趋势延续 | `trend_z_20d`、`breakout_distance_up/down`、`up_day_ratio_20d` | 止损在趋势延续环境更有价值；卖在趋势突破方向更容易二次止损 | product-side |
| 波动加速 | `rv_accel`、`iv_accel`、`range_expansion` | 波动聚集和 regime switching 会使止损后风险继续存在 | product-date |
| Skew 变陡 | `put_skew_change_5d`、`call_skew_change_5d` | 尾部保护需求上升时，卖方短 vega/短 skew 风险加大 | product-side |
| 跳跃风险 | `jump_share_20d`、`gap_frequency_20d`、`gap_share_20d` | 跳跃风险会让固定倍数止损越界，尤其商品夜盘和低流动性期权 | product-date |
| 权利金缓冲 | `premium_to_mae20_loss`、`premium_to_tail_move_loss` | 权利金必须覆盖正常不利移动和尾部移动，否则止损只是时间问题 | contract、product-side |
| Gamma 压力 | `gamma_theta_ratio`、`theta_per_gamma`、`dte_bucket` | 近到期和高 gamma 合约更容易被路径波动打到止损 | contract |
| Vega 压力 | `theta_per_vega`、`premium_to_iv10_loss`、`vol_of_vol_risk_premium` | 止损常来自 IV 抬升而非单纯方向，需看权利金是否覆盖 vega shock | contract、product-date |
| 估值离散 | `tick_value_ratio`、`stale_price_ratio`、`iv_outlier_rate` | 低价和陈旧成交会制造假止损 | contract、execution |
| 流动性退出 | `exit_capacity_score`、`stop_depth_proxy` | 止损时能否成交比开仓时能否成交更重要 | contract、execution |
| 止损聚集 | `stop_cluster_score`、`sector_stop_count_20d` | 多品种同日止损代表组合 regime，而非单品种噪声 | portfolio、sector |

这些因子进入 B5 时应优先做“止损 hazard model”或“stop avoidance 分层”，而不是先加入收益排序。一个因子若能降低止损但同时大幅砍掉 theta，需要看 `future_cooldown_value` 和 `missed_theta_if_blocked` 后再决定是否交易化。

### 12.1 参考文献线索

| 主题 | 文献线索 | 对 S1 的启发 |
| --- | --- | --- |
| 止损规则 | [Kaminski and Lo, When Do Stop-Loss Rules Stop Losses?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338) | 止损价值依赖路径状态；随机游走下可能降低收益，动量状态下可能有效。 |
| 再入场与 regime | [Stop-loss strategies with serial correlation, regime switching, and transaction costs](https://www.sciencedirect.com/science/article/pii/S1386418117300472) | 止损规则必须包含重开政策；交易成本和序列相关会改变止损价值。 |
| IV/RV 与期权收益 | [Goyal and Saretto, Cross-section of option returns and volatility](https://www.sciencedirect.com/science/article/pii/S0304405X09001251) | 卖权不是看 IV 高低，而是看 IV 相对未来 realized 是否错价，并且必须扣交易摩擦。 |
| 跳跃和尾部风险 | [Bollerslev and Todorov, Tails, Fears and Risk Premia](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1589719) | 深虚值期权价格包含跳跃尾部补偿；止损风险要区分连续波动和跳跃。 |
| 曲面结构 | [Christoffersen, Fournier and Jacobs, The Factor Structure in Equity Options](https://memento.epfl.ch/public/upload/files/Paper_Jacobs.pdf) | IV level、skew、term structure 是不同风险因子，不能只用单一 IV。 |
| Skew 预测力 | [Borochin, Wu and Zhao, The Effect of Option-implied Skewness on Delta- and Vega-Hedged Option Returns](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3426557) | skew 对期权收益有独立解释力，P/C 选择应把 skew 作为条件变量。 |

## 13. B5 新增：Delta 梯队与铺开宽度因子

### 13.1 研究问题

`abs(delta) < 0.10` 是 S1 的硬约束，不应因为实验结果临时放宽。但在这个硬约束内部，仍然有两个重要问题：

| 问题 | 交易含义 |
| --- | --- |
| 更靠近 `0.10`，还是更深虚值？ | 靠近 `0.10` 权利金更厚、theta 更高，但 stop/gamma/vega 风险更大；更深虚值止损概率低，但权利金薄、tick/手续费/陈旧成交污染更重。 |
| 每个品种每侧铺几个执行价梯队？ | 多梯队可以降低单一执行价报价异常和 pin risk，但并不真正分散同一标的尾部风险，还会提高低质量合约进入概率。 |

因此，B5 不应直接规定“每侧卖 5 个”或“全部卖 0.09 附近”，而应先在 full shadow 中建立 delta ladder 研究面板，评估不同 delta 桶和不同梯队数的边际收益与边际风险。

### 13.2 学术逻辑

Delta 可以视作 moneyness 和风险中性的近似坐标，但它不是物理世界的到期实值概率。尤其在存在 skew、jump risk、套保需求和流动性压力时，`0.10 delta` 在不同品种、不同 regime、不同到期日下代表的真实风险并不相同。

文献给出三条启发：

| 文献线索 | 对 delta ladder 的启发 |
| --- | --- |
| [Coval and Shumway, Expected Option Returns](https://deepblue.lib.umich.edu/handle/2027.42/74142) | 期权收益和 moneyness/strike 有系统关系，深虚值期权的收益分布更偏尾部，不能只按名义 delta 机械比较。 |
| [Bollen and Whaley, Does Net Buying Pressure Affect the Shape of Implied Volatility Functions?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=319261) | 不同 moneyness 区间的 IV 受买方需求压力影响，远端翼部高 IV 可能是可收溢价，也可能是拥挤保护需求。 |
| [Bollerslev and Todorov, Tails, Fears and Risk Premia](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1589719) | 短期限深虚值期权包含尾部恐惧和 jump risk premium，卖得越远端，越像在承保低频跳跃而非普通 theta。 |
| [Goyal and Saretto, Cross-section of option returns and volatility](https://www.sciencedirect.com/science/article/pii/S0304405X09001251) | IV 相对未来 realized 的错价比单纯 IV level 更重要；delta 桶也应结合 IV/RV、MAE 和 stress coverage 检验。 |

结论是：delta 桶不是 alpha 本身，而是卖方风险分层坐标。B5 应先检验每个 delta 桶的权利金留存、止损、gamma/vega 损耗和交易质量，再决定梯队铺法。

### 13.3 Delta 桶定义

第一版建议把 `abs(delta) < 0.10` 拆成以下桶。`<=0.02` 暂时只做诊断，不建议第一版交易化加权。

| 桶 | 字段值 | 经济含义 | 预期风险 |
| --- | --- | --- | --- |
| D1 | `0.08 <= abs_delta < 0.10` | 靠近硬上限，权利金最厚，最像主动承保普通尾部 | theta 高、gamma/stop 高 |
| D2 | `0.06 <= abs_delta < 0.08` | 中高 delta 低虚值区，可能是收益和风险的折中 | theta 中高、止损中等 |
| D3 | `0.04 <= abs_delta < 0.06` | 中低 delta 区，止损概率降低但权利金变薄 | theta 中低、tick 风险上升 |
| D4 | `0.02 <= abs_delta < 0.04` | 深虚值翼部，主要承保跳跃和 skew risk premium | 权利金薄、跳价/流动性风险高 |
| D5 | `abs_delta < 0.02` | 极深虚值，价格离散和成交质量可能主导结果 | 暂只诊断，不优先交易化 |

### 13.4 建议新增字段

| 因子族 | 建议字段 | 定义草案 | 使用层级 |
| --- | --- | --- | --- |
| Delta 桶 | `delta_bucket` | D1-D5 桶分类 | contract |
| 距离上限 | `delta_to_cap` | `0.10 - abs_delta` | contract |
| 距离桶中心 | `delta_bucket_center_distance` | 与所属桶中心的距离 | contract |
| 桶内排名 | `rank_in_delta_bucket` | 同 product-side-expiry-delta_bucket 内按 B4/B5 合约质量排名 | contract |
| 可用梯队数 | `available_ladder_count` | 同 product-side-expiry 下通过硬过滤的 delta 桶数量 | product-side |
| 选中梯队数 | `selected_ladder_count_shadow` | shadow 假设选前 K 个梯队 | product-side |
| 桶权利金占比 | `premium_share_by_delta_bucket` | 每个 delta 桶权利金占该 product-side 权利金比例 | product-side |
| 桶 stress 占比 | `stress_share_by_delta_bucket` | 每个 delta 桶 stress loss 占比 | product-side |
| 桶 stop 历史 | `delta_bucket_stop_prior` | T 日以前同品种同侧同桶止损率 | product-side |
| 桶质量分 | `delta_bucket_quality_score` | 由 premium retention、stop、vega/gamma、friction 构成的 shadow 研究分 | product-side |

### 13.5 Full shadow 标签

Delta ladder 必须看边际贡献，而不是只看单个合约 IC。

| 标签 | 定义 | 用途 |
| --- | --- | --- |
| `future_pnl_by_delta_bucket` | 每个 delta 桶的未来 PnL/margin | 判断哪个桶贡献收益 |
| `future_stop_rate_by_delta_bucket` | 每个 delta 桶的止损率 | 判断高 delta 是否显著增加止损 |
| `future_vega_loss_by_delta_bucket` | 每个 delta 桶的 vega 归因 | 判断是否靠卖更重 vega 换 theta |
| `future_gamma_loss_by_delta_bucket` | 每个 delta 桶的 gamma 归因 | 判断靠近 0.1 是否被 gamma 吞噬 |
| `future_retention_by_delta_bucket` | 每个 delta 桶权利金留存率 | 判断薄权利金是否真实可留存 |
| `future_stop_overshoot_by_delta_bucket` | 每个 delta 桶止损越界幅度 | 判断深虚值低价合约是否更容易跳价 |
| `marginal_ladder_pnl_k` | 同 product-side 选择前 K 个梯队的边际 PnL | 判断铺几个梯队最划算 |
| `marginal_ladder_stop_k` | 选择前 K 个梯队后的边际止损变化 | 判断多铺是否只是增加尾部暴露 |
| `marginal_ladder_theta_vega_k` | 选择前 K 个梯队后的 theta/vega 变化 | 判断多铺是否改善 theta 质量 |

### 13.6 未来交易化方向，不直接上线

若 full shadow 检验有效，B5 后续可以测试四种 delta ladder 结构：

| 结构 | 定义 | 适用假设 |
| --- | --- | --- |
| Near-cap | 主要选择 D1-D2，即 `0.06-0.10` | 市场稳定、降波确认、权利金质量高，追求 theta 增厚 |
| Balanced ladder | D1-D4 分散铺开，例如 D1/D2/D3/D4 按风险预算分配 | 希望降低单一执行价异常和 pin risk |
| Tail-light | 降低 D1 权重，提高 D2-D3，基本不用 D4-D5 | 目标是降低止损和 gamma 吞噬 |
| Adaptive ladder | 趋势、skew、RV、jump、cooldown 决定每侧桶权重 | 更接近最终主线，但必须在 shadow 检验后再交易化 |

第一版不建议直接使用 D5。若 D5 在 shadow 中表现看似很好，必须先排查低价、tick、stale price、成交量和止损标签偏差。

### 13.7 与每侧最多合约数的关系

每侧最多合约数不应是纯参数，而应由边际风险收益决定。B5 full shadow 应对每个 `product + side + expiry + date` 计算：

```text
K = 1, 2, 3, 4, 5, ...
```

对应的边际结果：

```text
marginal_premium_k
marginal_stress_loss_k
marginal_theta_k
marginal_vega_k
marginal_gamma_k
marginal_stop_probability_k
marginal_pnl_per_margin_k
```

如果 `K` 增加后，premium 增加但 `pnl_per_margin` 不升、stop cluster 上升、vega/gamma 损耗恶化，则说明当前“每侧最多 5 个”已经偏多。  
如果 `K` 增加后，premium retention 稳定、stop 未明显上升、gamma/vega per premium 改善，则说明铺开更多梯队有价值。

### 13.8 先验判断

在没有 full shadow 验证前，我的先验判断是：

| 结论 | 理由 |
| --- | --- |
| D1 不应被完全排除 | 这里通常 theta 最厚，是提高收益的主要来源。 |
| D1 不应无脑最大权重 | 它最容易把策略变成短 gamma / 短 vega，尤其在趋势和升波中。 |
| D4-D5 不能因为低 delta 就认为安全 | 权利金薄、tick 大、成交陈旧、跳价止损可能吞掉收益。 |
| 梯队数应由 product-side 风险预算控制 | 同品种同方向多执行价不是真分散，本质仍是同一标的尾部风险。 |
| 最终可能是 adaptive ladder | 稳定降波时靠近 0.1，多拿 theta；趋势/跳跃/冷静期未释放时向 D2-D3 收缩。 |

## 14. B5 新增：品种数量、保证金使用率与尾部相关性骤变

### 14.1 研究问题

S1 是多品种卖权策略，组合层的关键问题不是“开了多少品种”本身，而是：

| 问题 | 卖权含义 |
| --- | --- |
| 品种数量多少才够？ | 原始品种数只衡量表面分散，不能衡量尾部同涨同跌、同日止损和同一板块暴露。 |
| 保证金使用到多少才合理？ | 当前保证金率不等于压力保证金率；卖方最怕亏损和保证金上升同时发生。 |
| 尾部相关性是否突然升高？ | 平时低相关的品种，在宏观冲击、商品板块共振和流动性收缩时可能同时触发止损。 |
| 增加一个品种是否真的增加分散？ | 若新增品种与组合尾部同向，它增加的是隐含杠杆，不是有效分散。 |

因此，B5 full shadow 需要记录的不只是 `active_product_count`，而是 `effective_product_count`、`stress concentration`、`tail correlation jump` 和 `stressed margin usage`。

### 14.2 学术逻辑

组合理论中的经典分散依赖低相关性，但大量研究显示相关性具有时变性、非对称性和尾部依赖。Longin and Solnik、Ang and Chen 等研究讨论过极端下跌中相关性上升或尾部相关变化；后续关于动态 copula、tail correlation 和 crisis correlation 的研究也提示，普通滚动相关在压力期可能低估组合共同亏损。

对卖权组合而言，这个问题更尖锐：组合不是持有标的多头，而是在多个品种上卖保险。亏损通常来自“多个品种同时进入不利尾部 + IV/skew 同时重估 + 保证金同时上升”。所以 B5 组合层因子应该围绕三个对象构造：

```text
有效品种数
压力保证金
尾部相关性/止损聚集
```

### 14.3 品种数量因子

| 因子族 | 建议字段 | 定义草案 | 使用层级 |
| --- | --- | --- | --- |
| 原始数量 | `active_product_count` | 当日有持仓或候选预算的品种数量 | portfolio |
| 方向数量 | `active_product_side_count` | 当日有持仓或候选预算的 product-side 数量 | portfolio |
| 板块数量 | `active_sector_count` | 当日有风险暴露的板块数量 | portfolio |
| 保证金有效品种数 | `effective_product_count_margin` | 基于产品保证金权重的 `1 / sum(w_i^2)` | portfolio |
| Stress 有效品种数 | `effective_product_count_stress` | 基于产品 stress loss 权重的 `1 / sum(w_i^2)` | portfolio |
| Vega 有效品种数 | `effective_product_count_vega` | 基于 abs cash vega 权重的 `1 / sum(w_i^2)` | portfolio |
| Gamma 有效品种数 | `effective_product_count_gamma` | 基于 abs cash gamma 权重的 `1 / sum(w_i^2)` | portfolio |
| Top 集中度 | `top1_product_stress_share`、`top5_product_stress_share` | 最大/前五品种 stress loss 占比 | portfolio |
| HHI 集中度 | `hhi_product_stress`、`hhi_sector_stress` | 品种/板块 stress loss HHI | portfolio |
| 边际分散价值 | `marginal_diversification_value_product` | 新增该品种后组合有效品种数和 stress concentration 的变化 | product-date、portfolio |

有效品种数应优先基于 stress loss、vega、gamma 和保证金，而不是只基于名义持仓。若原始品种数增加但 `effective_product_count_stress` 不升，说明只是增加了同一尾部风险的重复承保。

### 14.4 保证金使用率因子

| 因子族 | 建议字段 | 定义草案 | 使用层级 |
| --- | --- | --- | --- |
| 当前保证金 | `margin_usage_rate` | 当前保证金占 NAV | portfolio |
| 保证金余量 | `margin_headroom_to_target`、`margin_headroom_to_hard_limit` | 距目标/硬上限的剩余额度 | portfolio |
| 压力保证金 | `stressed_margin_usage_rate` | spot/IV/stress 情景后估计保证金占 NAV | portfolio |
| 保证金冲击 | `margin_shock_ratio` | 压力保证金 / 当前保证金 | portfolio |
| 保证金速度 | `margin_usage_change_5d`、`margin_usage_change_20d` | 保证金使用率短期变化 | portfolio |
| 回撤耦合 | `margin_drawdown_coupling` | 保证金上升与 NAV 回撤是否同步 | portfolio |
| 品种边际保证金 | `marginal_margin_by_product` | 新增某品种/方向后组合保证金增加 | product-date |
| 单位压力保证金权利金 | `premium_to_stressed_margin` | 权利金 / 压力保证金 | contract、product-side |
| 保证金尾部占用 | `stressed_margin_top_product_share` | 压力情景下最大品种保证金占比 | portfolio |

保证金使用率的研究目标不是简单找“越高越赚钱”，而是找到在不同 regime 下的可承受区间。Full shadow 应按 margin bucket 检验：

```text
0-20%
20-35%
35-50%
50-65%
65%+
```

每个 bucket 观察未来 PnL、最大回撤、stop cluster、stressed margin usage 和 vega/gamma loss。若 50% 以上只是提高收益但显著放大回撤和保证金冲击，则不能作为主线仓位；若在降波稳定环境中 50%-65% 仍能改善 Calmar，则可作为条件化放大规则。

### 14.5 尾部相关性骤变因子

| 因子族 | 建议字段 | 定义草案 | 使用层级 |
| --- | --- | --- | --- |
| 普通相关 | `avg_pair_corr_20d`、`avg_pair_corr_60d` | 标的收益滚动相关均值 | portfolio |
| 下尾相关 | `down_tail_corr_60d` | 只在组合不利下跌/上涨尾部样本中计算相关 | portfolio、sector |
| 上尾相关 | `up_tail_corr_60d` | 对卖 Call 不利的上涨尾部相关 | portfolio、sector |
| 相关跳变 | `tail_corr_jump` | 当前尾部相关 - 历史基准尾部相关 | portfolio |
| 第一主成分 | `tail_corr_first_pc_share` | 尾部相关矩阵第一特征值占比 | portfolio |
| 板块尾部相关 | `sector_tail_corr` | 同板块内部尾部相关 | sector |
| Stop 相关 | `stop_cluster_score` | 同日或短窗口多品种止损重合概率 | portfolio、sector |
| 方向拥挤相关 | `put_side_tail_corr`、`call_side_tail_corr` | Put/Call 方向不利行情中的相关性 | portfolio |
| 相关性状态 | `correlation_regime_state` | 低相关、正常、相关抬升、尾部相关骤变 | portfolio |

尾部相关不应只用普通 Pearson 相关。B5 至少要同时看三类代理：

| 代理 | 说明 |
| --- | --- |
| 标的收益尾部相关 | 用标的期货/ETF/指数收益衡量尾部同向移动。 |
| 止损共现 | 用策略自身历史止损事件衡量“卖方损失是否聚集”。 |
| Stress loss 相关 | 用候选或持仓在共同 stress 情景下的亏损同向性衡量组合尾部。 |

### 14.6 Full shadow 标签

| 标签 | 定义 | 用途 |
| --- | --- | --- |
| `future_portfolio_drawdown_by_product_count` | 不同有效品种数下未来组合回撤 | 判断品种数是否真的降低尾部 |
| `future_pnl_by_margin_bucket` | 不同保证金使用率 bucket 的未来 PnL | 判断仓位放大是否有效 |
| `future_calmar_by_margin_bucket` | 不同保证金 bucket 的 Calmar 或回撤收益比 | 判断是否只是放大风险 |
| `future_stop_cluster_count` | 未来 N 日同日/短窗口止损品种数 | 检验 stop cluster 风险 |
| `future_tail_corr_drawdown` | 高尾部相关状态后的未来回撤 | 检验相关跳变预警 |
| `future_margin_squeeze` | 未来是否出现保证金上升 + NAV 回撤 | 检验保证金冲击 |
| `future_diversification_value` | 新增品种后的组合风险下降量 | 判断新增品种是否提供真分散 |
| `future_stress_loss_realization` | 未来实际损失是否接近 stress loss 排序 | 校准 stress 模型 |

### 14.7 未来交易化方向，不直接上线

若 B5 full shadow 检验有效，未来可测试以下组合层规则：

| 规则 | 思路 | 注意事项 |
| --- | --- | --- |
| 有效品种数下限 | 不看原始品种数，看 `effective_product_count_stress` 或 `effective_product_count_vega` | 原始品种数可能虚高 |
| Top stress 限制 | 限制 Top1/Top5 品种或板块 stress share | 比单品种名义上限更贴近尾部 |
| 动态保证金目标 | 正常 50%，稳定降波可提高，高相关/升波/stop cluster 时降低 | 必须看 Calmar 和回撤 |
| 尾部相关降预算 | `tail_corr_jump` 或 `stop_cluster_score` 升高时降低总预算 | 避免压力期“看似分散、实际同亏” |
| 边际分散准入 | 新增品种只有提高有效分散或改善收益/风险时才加预算 | 防止为了数量而开仓 |

### 14.8 Copula 与尾部相依性风控因子

普通相关性只能描述均值附近的线性共振，对卖权策略最危险的“多品种同时触发尾部”解释力不足。B5 应引入 copula / tail dependence 因子，把组合风险从相关矩阵升级为尾部相依矩阵。

对 S1 而言，需要区分三类尾部：

| 尾部类型 | 对卖方的不利场景 | 适用方向 |
| --- | --- | --- |
| 下尾相依 | 多个标的同时大跌，卖 Put 同时亏损，Put skew 和 IV 同时上升 | Put 侧 |
| 上尾相依 | 多个标的同时大涨，卖 Call 同时亏损，Call skew 或 squeezes 同时出现 | Call 侧 |
| 双尾相依 | 商品板块受宏观、政策、流动性冲击时，上下尾都可能同步放大 | 组合层 |

第一版不建议直接拟合过度复杂的高维 copula。更稳妥的做法是分三层实现：先做经验尾部相依，再做 pairwise copula 参数，再做组合层网络聚合。

| 因子族 | 建议字段 | 定义草案 | 使用层级 |
| --- | --- | --- | --- |
| 经验下尾相依 | `empirical_lower_tail_dependence_95` | `P(r_i < q_i(5%) | r_j < q_j(5%))` 的滚动估计 | product-pair、sector、portfolio |
| 经验上尾相依 | `empirical_upper_tail_dependence_95` | `P(r_i > q_i(95%) | r_j > q_j(95%))` 的滚动估计 | product-pair、sector、portfolio |
| 双尾共振 | `two_tail_dependence_score` | 上尾和下尾相依的加权组合 | portfolio |
| Tail beta | `lower_tail_beta`、`upper_tail_beta` | 在尾部样本中，单品种对组合尾部损失的回归 beta | product-date |
| Tail Kendall | `tail_kendall_tau` | 只在尾部样本中计算 Kendall 秩相关 | product-pair |
| Tail copula jump | `tail_dependence_jump` | 当前尾部相依 - 历史中位尾部相依 | portfolio |
| t-copula 自由度 | `t_copula_df` | pairwise 或 sector-level t-copula 的自由度；越低代表联合肥尾越重 | sector、portfolio |
| t-copula 相关 | `t_copula_rho` | t-copula 的相关参数 | product-pair、sector |
| Clayton 下尾参数 | `clayton_theta_lower` | Clayton copula 对下尾相依的拟合参数 | product-pair、sector |
| Gumbel 上尾参数 | `gumbel_theta_upper` | Gumbel copula 对上尾相依的拟合参数 | product-pair、sector |
| CoVaR | `product_delta_covar` | 某品种处于尾部时组合 VaR 的增量 | product-date |
| MES | `product_marginal_expected_shortfall` | 组合尾部日中该品种贡献的平均损失 | product-date |
| Tail network centrality | `tail_network_centrality` | 以尾部相依为边权构造网络后的中心性 | product-date、sector |
| Tail risk contribution | `tail_es_contribution` | 组合 Expected Shortfall 中该品种的贡献 | product-date、portfolio |

这些字段的目标不是预测平均收益，而是识别“这个品种在尾部会不会拖着组合一起亏”。因此，full shadow 应对它们使用组合层标签：

| 标签 | 定义 | 用途 |
| --- | --- | --- |
| `future_tail_cluster_loss` | 未来 N 日内多个品种同时大亏的组合损失 | 检验尾部相依是否预警组合回撤 |
| `future_multi_stop_event` | 未来 N 日是否出现多品种同日或短窗口止损 | 检验 stop cluster |
| `future_tail_es_realized` | 高尾部相依状态后的实际 Expected Shortfall | 校准尾部风控 |
| `future_product_tail_contribution` | 单品种对组合尾部亏损的贡献 | 决定品种预算降权 |
| `future_tail_corr_breakdown` | 普通相关低但尾部共同亏损高的事件 | 检验普通相关是否失效 |

### 14.9 Copula 因子的实现分层

为了避免过拟合，copula 因子应按复杂度分阶段实现。

| 阶段 | 方法 | 优点 | 风险 |
| --- | --- | --- | --- |
| C1 | 经验共尾概率、tail Kendall、tail beta | 简单、稳健、可解释 | 样本少时噪声大 |
| C2 | Pairwise t-copula、Clayton、Gumbel | 能区分上下尾和肥尾 | 参数估计不稳，需要滚动窗口和收缩 |
| C3 | Sector-level vine copula 或 factor copula | 能处理高维组合尾部 | 实现复杂，过拟合风险高 |
| C4 | CoVaR、MES、ES contribution | 直接服务风控和预算 | 标签需要严格定义组合损失 |

第一版 B5 full shadow 建议只做 C1 + 部分 C2：

```text
empirical_lower_tail_dependence_95
empirical_upper_tail_dependence_95
tail_kendall_tau
lower_tail_beta
upper_tail_beta
t_copula_df
t_copula_rho
product_delta_covar
product_marginal_expected_shortfall
```

Vine copula 和高维 factor copula 暂时只写入研究储备，不进入第一版实现。

### 14.10 Copula 因子的交易化边界

尾部相依因子不应直接做合约排序。它们适合进入三个位置：

| 位置 | 用法 |
| --- | --- |
| 总预算 | 当 `tail_dependence_jump`、`t_copula_df` 变差、`multi_stop_event` 概率升高时，降低总保证金目标。 |
| 品种预算 | 对 `product_delta_covar`、`MES`、`tail_es_contribution` 高的品种降权。 |
| 板块/方向上限 | 当同板块下尾相依升高时降低 Put 侧预算；上尾相依升高时降低 Call 侧预算。 |

它们不适合直接回答“这张合约是不是比另一张合约好”。同一品种内的合约排序仍应主要由权利金质量、gamma/vega、MAE 覆盖、friction 和 delta ladder 决定。

### 14.11 参考文献线索

| 主题 | 文献线索 | 对 S1 的启发 |
| --- | --- | --- |
| 尾部相关和分散失效 | [Longin and Solnik / Ang and Chen 相关研究综述：Increasing correlations or just fat tails?](https://www.sciencedirect.com/science/article/abs/pii/S0927539807000631) | 压力期相关性变化会削弱普通分散；需要尾部相关而非普通相关。 |
| 动态尾部依赖 | [Is the Potential for International Diversification Disappearing?](https://academic.oup.com/rfs/article/25/12/3711/1594463) | Copula 和非对称依赖能刻画普通相关看不到的尾部共振。 |
| 时变 copula | [Tail Dependence in Financial Markets: A Dynamic Copula Approach](https://www.mdpi.com/2227-9091/7/4/116) | Copula 参数可以时变，适合刻画相关性突然抬升的 regime。 |
| 非对称 copula | [Patton, Modelling Asymmetric Exchange Rate Dependence](https://academic.oup.com/rof/article-abstract/10/4/527/1580161) | 上尾和下尾相依可以不同，适合分别管理卖 Put 和卖 Call 风险。 |
| Tail correlation portfolio | [Portfolio optimization in the presence of tail correlation](https://www.sciencedirect.com/science/article/pii/S0264999323000470) | 组合优化应显式考虑 tail behavior，而不是只看均值方差。 |
| 相关结构危机变化 | [Risk diversification with filtered correlation-network approach](https://arxiv.org/abs/1410.5621) | 危机中相关网络结构会变化，静态板块分散可能失效。 |
| CoVaR | [Adrian and Brunnermeier, CoVaR](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1939717) | 可衡量单个品种处于压力时对组合 VaR 的边际贡献。 |
| MES/SRISK | [Acharya, Pedersen, Philippon and Richardson, systemic expected shortfall / MES](https://www.nber.org/system/files/chapters/c12063/revisions/c12063.rev0.pdf) | 可把单品种在组合尾部日的边际损失贡献转化为预算降权。 |
| 保证金和期权卖方义务 | [Cboe Strategy-based Margin](https://www.cboe.com/en/markets/us/options/margin/strategy-based-margin/) | 卖方保证金是履约义务抵押，券商可设更高要求；回测要看压力保证金。 |
| Put-write 基准 | [Bondarenko, Historical Performance of Put-Writing Strategies](https://cdn.cboe.com/resources/spx/bondarenko-oleg-putwrite-putw-2019.pdf) | 系统卖 Put 可有长期风险溢价，但 drawdown、抵押和期限滚动是策略定义的一部分。 |
| 交易摩擦与保证金 | [Volatility trading profitability and trading frictions](https://www.sciencedirect.com/science/article/pii/S1059056017305191) | 波动交易盈利依赖 VRP 捕捉和交易择时，也受到保证金占用和 margin call 风险约束。 |

## 15. B5 Full Shadow V1 实施矩阵

前文是完整研究地图，本节是第一版可执行施工图。V1 的原则是：优先实现可解释、数据可得、直接回答 S1 核心问题的字段；对 copula、CoVaR、MES 等更高阶指标，先保留接口和少量稳健版本，不在第一版过度拟合。

### 15.1 V1 必须回答的问题

| 层级 | 必须回答的问题 | 若回答不清，后续交易化风险 |
| --- | --- | --- |
| 合约层 | 哪些合约在控制 `premium/margin/DTE/delta/stress` 后，仍有更高留存率、更低止损和更少 vega/gamma 损耗？ | 合约排序可能只是追高权利金和高风险。 |
| P/C 侧 | 趋势、skew、RV、risk reversal 是否能解释今天该偏 Put、偏 Call，还是双卖？ | P/C 偏移可能变成隐含方向押注。 |
| Delta 梯队 | `0.08-0.10` 是否真的贡献更多净收益？多铺 K 个合约后边际 theta 是否大于新增 gamma/vega/stop 风险？ | 每侧合约数和 delta 位置会变成拍脑袋参数。 |
| 冷静期 | 止损后固定禁做是否有价值？还是应等 IV/RV/skew 回落后再重开？ | 可能减少二次止损，但错过最赚钱的降波修复。 |
| 品种层 | 哪些品种是真正高质量权利金来源，哪些只是高权利金高尾部风险？ | 品种预算可能过拟合某段行情。 |
| 组合层 | 有效品种数、stress 集中度、尾部相依、stop cluster 能否提前解释组合回撤？ | 保证金放大前无法判断组合是否真分散。 |
| 保证金层 | 50%、65% 等保证金使用率在哪些 regime 下有效，在哪些 regime 下只是放大回撤？ | 仓位提高可能只提高左尾亏损。 |
| 执行层 | tick、stale price、异常跳价、stop overshoot 是否解释假止损和回测污染？ | 因子 IC 可能来自不可成交或异常报价。 |

### 15.2 V1 字段与数据源

| 模块 | V1 字段 | 数据源 | 第一版说明 |
| --- | --- | --- | --- |
| 已有权利金质量 | `premium_yield_margin`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty`、`friction_ratio`、`b3_vomma_loss_ratio` | 现有候选合约表、Greeks、保证金、手续费 | B2/B3/B4 已实现，作为对照和控制项必须保留。 |
| Delta 梯队 | `delta_bucket`、`delta_to_cap`、`available_ladder_count`、`premium_share_by_delta_bucket`、`stress_share_by_delta_bucket` | 候选合约表、delta、权利金、stress loss | V1 只做 D1-D5 桶和边际 K 诊断，不直接改交易。 |
| P/C 趋势 | `mom_5d`、`mom_20d`、`trend_z_20d`、`breakout_distance_up_60d`、`breakout_distance_down_60d` | 标的日线或期货/ETF 日频价格 | 用于 product-side 面板，必须 T 日收盘后才可见。 |
| Skew 曲面 | `put_skew_10d`、`call_skew_10d`、`risk_reversal_25d`、`smile_curvature`、`skew_change_5d` | 期权链、IV surface、ATM IV | V1 可先用可获得的低 delta/ATM IV 近似，不强求完美曲面拟合。 |
| 冷静期 | `days_since_product_stop`、`days_since_product_side_stop`、`product_stop_count_20d`、`product_stop_overshoot_20d`、`post_stop_iv_change`、`post_stop_rv_change` | 真实策略止损日志、NAV/持仓日志、IV/RV 历史 | 只用 T 日以前真实已发生止损，严禁用未来 shadow 止损当信号。 |
| 路径风险 | `premium_to_mae20_loss`、`premium_to_tail_move_loss`、`range_expansion_20d` | 标的日线 high/low、历史收益、合约 Greeks | V1 先用日线 MAE；分钟 jump/gap 可作为 V1.1 扩展。 |
| 执行质量 | `tick_value_ratio`、`low_price_flag`、`stale_price_ratio`、`iv_outlier_flag` | 期权行情、最小变动价位、成交时间、IV 反解结果 | 若缺少 last trade age，V1 先做 tick 和低价离散。 |
| 品种数量 | `active_product_count`、`effective_product_count_stress`、`top5_product_stress_share`、`hhi_product_stress` | 候选/持仓、stress loss、产品映射 | V1 用候选组合和已有持仓分别计算。 |
| 保证金 | `margin_usage_rate`、`stressed_margin_usage_rate`、`margin_shock_ratio`、`premium_to_stressed_margin` | NAV、保证金模型、stress 情景 | V1 先用现有 stress 模型估算压力保证金。 |
| 尾部相依 | `empirical_lower_tail_dependence_95`、`empirical_upper_tail_dependence_95`、`tail_kendall_tau`、`lower_tail_beta`、`upper_tail_beta` | 标的收益历史、板块映射 | V1 优先经验相依，不先上高维 copula。 |
| 高阶尾部风险 | `t_copula_df`、`t_copula_rho`、`product_delta_covar`、`product_marginal_expected_shortfall` | 标的收益历史、组合损失序列 | V1 可选实现 pairwise/sector 版本；没有稳定样本时只出诊断，不进评分。 |

### 15.3 V1 Shadow 标签

| 标签族 | V1 标签 | Horizon | 用途 |
| --- | --- | --- | --- |
| 合约收益 | `future_net_pnl_per_margin`、`future_retained_ratio`、`future_premium_capture` | `5D`、`10D`、`trade_rule`、`expiry` | 判断合约质量。 |
| Greek 归因 | `future_theta_pnl`、`future_vega_pnl`、`future_gamma_pnl`、`future_delta_pnl`、`future_residual_pnl` | `trade_rule` | 判断是否真的改善 theta/vega，而不是靠方向或 residual。 |
| 止损 | `future_stop_hit`、`future_stop_loss_cash`、`future_stop_overshoot` | `trade_rule` | 判断止损风险和跳价风险。 |
| 路径 | `future_mae_pnl`、`future_mfe_pnl`、`future_max_adverse_underlying_move` | `5D`、`10D`、`trade_rule` | 判断最大不利路径和权利金缓冲。 |
| P/C | `future_put_minus_call_outcome`、`future_side_stop_diff`、`future_side_vega_gamma_diff` | `5D`、`10D`、`trade_rule` | 判断 P/C 偏移因子是否有效。 |
| Delta 梯队 | `future_pnl_by_delta_bucket`、`future_stop_rate_by_delta_bucket`、`marginal_ladder_pnl_k`、`marginal_ladder_stop_k`、`marginal_ladder_theta_vega_k` | `trade_rule` | 判断 delta 桶和每侧 K 的边际价值。 |
| 冷静期 | `future_repeat_stop_5d`、`future_repeat_stop_10d`、`future_missed_theta_if_blocked`、`future_cooldown_value` | `5D`、`10D` | 判断冷静期是否减少净损失而非错过收益。 |
| 组合尾部 | `future_stop_cluster_count`、`future_tail_cluster_loss`、`future_margin_squeeze`、`future_tail_es_realized` | `5D`、`10D`、`20D` | 判断组合风控因子是否预警回撤。 |

`trade_rule` 指沿用当前 S1 shadow 持有规则：T 日信号、T+1 入场、止损倍数、到期/提前处理和费用口径均与当前 B1/B4 基准一致。若未来修改 shadow 持有规则，必须重新标注文档版本。

### 15.4 V1 检验方法

| 检验 | V1 要求 | 目的 |
| --- | --- | --- |
| Q1-Q5 分层 | 每个因子在适用层级分 5 组，输出 PnL、retention、stop、vega/gamma、MAE | 判断经济方向是否单调。 |
| Rank IC | Spearman IC 按日计算，再做累计 IC | 判断因子稳定性。 |
| Non-overlap IC | 至少按 5 个交易日抽样一次重算 IC | 降低持有期重叠导致的虚高显著性。 |
| 相关性矩阵 | 同层级因子 Spearman 相关 | 防止 B2/B3/B4 类共线重复计票。 |
| 正交化 IC | 控制 `entry_price`、`open_premium_cash`、`margin_estimate`、`stress_loss`、`DTE`、`abs_delta` 后看残差 IC | 判断是否有增量信息。 |
| Regime 切片 | 低波、升波、降波、趋势、跳跃、商品/股指/ETF 分组 | 判断因子是否只在特定环境有效。 |
| 年份切片 | 至少按年份输出 IC 和分层表现 | 防止单一年份过拟合。 |
| Product split | 高流动性/低流动性、长历史/短历史、板块分组 | 判断是否只来自少数品种。 |
| P/C 交互 | `trend × skew`、`trend × RV`、`risk reversal × momentum` 二维表 | 判断 P/C 偏移是否条件化有效。 |
| 组合事件检验 | 高 tail dependence、stop cluster、margin squeeze 状态后的组合回撤 | 判断组合风控因子是否有预警价值。 |

### 15.5 V1 通过标准

单个因子不因为一次 IC 为正就通过。建议使用以下最低标准：

| 因子类型 | 通过标准 |
| --- | --- |
| 合约排序因子 | Q5-Q1 的 `future_net_pnl_per_margin` 为正，`future_retained_ratio` 更高，且 `vega_loss/gross_premium` 或 `gamma_loss/gross_premium` 不恶化。 |
| P/C 因子 | 在至少两个 horizon 上，P/C 相对收益方向一致；同时该方向的 stop rate 不显著上升。 |
| Delta ladder 因子 | 某个 delta 桶的净收益提升不能完全来自更高 theta，必须看 `theta - gamma - vega` 后仍更优。 |
| 冷静期因子 | `future_cooldown_value > 0`，即避免的二次亏损大于错过的 theta/vega 修复收益。 |
| 品种预算因子 | 高分品种的 PnL/margin 更高，且 stop cluster 和 tail loss 不更差。 |
| 组合风控因子 | 高风险状态后未来组合回撤、stop cluster 或 margin squeeze 显著更高，且不是普通相关因子的重复表达。 |
| 执行质量因子 | 能解释 stop overshoot、false stop 或低价异常；不要求解释平均收益。 |
| Copula / tail 因子 | 能在普通相关控制后解释 tail cluster loss、multi-stop 或 ES contribution。 |

策略层面，B5 候选交易化版本必须同时观察：

| 目标 | 要求 |
| --- | --- |
| 收益 | 相对 B1/B2c/B4 的 NAV 超额不能只来自少数月份或少数品种。 |
| 回撤 | 最大回撤、最差单日、stop cluster 不应恶化；若收益提高但回撤更深，需要看 Calmar 是否改善。 |
| Vega | `vega_pnl` 或 `vega_loss/gross_premium` 必须改善，至少不能明显恶化。 |
| Theta | 不能通过砍掉过多 theta 换来“看起来更安全”；需看 theta capture 和 premium retention。 |
| 保证金 | 若提高保证金使用率，必须在对应 regime 下改善 Calmar，而不是只放大收益和亏损。 |
| 可解释性 | 每个进入交易的因子必须归属于合约、P/C、品种、组合或执行层之一。 |

### 15.6 V1 暂不做的内容

| 暂缓内容 | 原因 |
| --- | --- |
| 高维 vine copula / factor copula | 样本短、上市时间不齐，第一版过拟合风险高。 |
| 机器学习综合模型 | 当前阶段先做单因子、正交化和角色归位，避免黑箱。 |
| Ratio / 国优式结构交易化 | 当前 S1 主线仍是纯卖权，ratio 因子先作为扩展池。 |
| 直接改变保证金目标 | 先用 shadow 判断 margin bucket 和 regime，再决定是否放大。 |
| 直接改变 `abs(delta)<0.10` 硬约束 | 只研究 0.10 内部的 ladder，不放宽风险边界。 |
| 用 shadow 未来止损构造实时冷静期信号 | 未来标签只能研究，不能回流。 |

## 16. 优先级排序

| 优先级 | 因子组 | 原因 | 实现难度 |
| --- | --- | --- | --- |
| P0 | 已有 B2/B3/B4 字段完整保留 | 作为基准和去共线对照，不能丢 | 低 |
| P1 | P/C 趋势动量、skew richness、risk reversal、curvature | 直接服务 Put/Call 选择，是当前策略逻辑缺口 | 中 |
| P1 | delta bucket、delta ladder、marginal ladder K | 直接决定 theta 厚度、止损概率和多执行价铺开的边际价值 | 中 |
| P1 | effective product count、stress concentration、tail corr jump、empirical tail dependence | 决定组合是否真分散，是保证金放大前必须先看的尾部约束 | 中高 |
| P1 | expected move / tail move / MAE 覆盖率 | 比 delta 和 margin 更贴近卖方真实路径风险 | 中 |
| P1 | tick_value_ratio、stale price、IV solve quality、PCP deviation | 可解释异常止损和低价合约污染 | 中 |
| P2 | pairwise t-copula、CoVaR、MES、tail network centrality | 更学术的组合尾部风险指标，适合进入风控而非合约排序 | 高 |
| P2 | jump share、gap share、intraday trendiness、range expansion | 需要真实标的分钟数据，但与止损失效高度相关 | 中高 |
| P2 | expiry cluster、stop clustering、tail correlation | 组合层风险，是后续接近管理人风格必须补的部分 | 中高 |
| P2 | marginal margin、margin shock、capital lock-up days | 关系到保证金滚动效率和容量 | 中 |
| P3 | ratio fragility、wing conversion cost、sweet width | 属于未来结构扩展，不应干扰当前纯卖权基准 | 高 |

## 17. 建议的下一步落地路径

| 步骤 | 内容 | 输出 |
| --- | --- | --- |
| 1 | 扩展 full shadow 字段，不改变交易，不改变 B1/B4 规则 | 新版 candidate universe CSV |
| 2 | 派生四张面板：contract、product-side、product、portfolio/regime | `factor_panel_contract.csv` 等 |
| 3 | 对每个层级分别跑分层、IC、累计 IC、相关性和正交化 | 完整因子审计报告 |
| 4 | 对 P/C 类因子单独做 trend × skew × RV regime 交互表 | P/C 选择专题报告 |
| 5 | 对 expiry、stop cluster、jump/slippage 单独做组合层诊断 | 组合风险专题报告 |
| 6 | 只把通过审计的因子放入 B5，且按角色进入交易规则 | B5 实验设计文档 |

## 18. 本文结论

本文最终形成的不是一组立即交易化的参数，而是一套 B5 full shadow 研究规格。核心结论是：S1 下一阶段不应继续把所有因子压成一个综合分，也不应直接用更多过滤条件降低交易频率；更合理的路径是先把卖方收益来源、亏损来源、执行污染和组合尾部风险拆到正确层级，再用 shadow 标签判断哪些因子有资格进入 B5 交易规则。

### 18.1 与 B2/B3/B4 的关系

| 类型 | 判断 | B5 处理方式 |
| --- | --- | --- |
| 已验证且必须保留的基础因子 | `premium_to_iv10_loss`、`premium_to_stress_loss`、`premium_yield_margin`、`gamma_rent_penalty`、`friction_ratio`、`b3_vomma_loss_ratio` | 作为合约层对照、控制项和 B4/B5 比较基准。 |
| 已覆盖但口径需要升级的因子 | `variance_carry`、`iv_rv_spread`、`vol-of-vol`、`term structure`、`skew steepening` | 改为 forward RV、premium-to-expected-IV-move、完整曲面和 product-side 层检验。 |
| 本轮新增且优先进入 V1 shadow 的因子 | P/C 趋势动量、skew richness、risk reversal、delta ladder、MAE/tail 覆盖、冷静期、有效品种数、压力保证金、经验尾部相依 | 不直接交易化，先做分层、IC、正交化和 regime 切片。 |
| 适合组合层而非合约排序的因子 | expiry cluster、stop cluster、sector/directional crowding、tail dependence、CoVaR、MES、margin shock | 用于总预算、品种预算、板块/方向上限和风险报告。 |
| 暂不进入纯卖权主线的扩展因子 | ratio fragility、wing conversion cost、sweet width、ratio breakeven、高维 vine/factor copula | 写入研究储备，等纯卖权 B5 逻辑清楚后再开独立结构实验。 |

### 18.2 B5 要检测的核心假设

| 假设 | 需要用什么验证 |
| --- | --- |
| 更高质量的权利金应该提高留存率，而不是只提高毛权利金 | `future_retained_ratio`、`future_net_pnl_per_margin`、`vega_loss/gross_premium`、`gamma_loss/gross_premium` |
| P/C 偏移应该来自趋势、skew 和 RV 条件，而不是长期方向押注 | `future_put_minus_call_outcome`、`side_stop_rate`、`trend × skew × RV` 交互表 |
| `abs(delta)<0.10` 内部仍有最优梯队 | `future_pnl_by_delta_bucket`、`marginal_ladder_pnl_k`、`marginal_ladder_theta_vega_k` |
| 止损后冷静期的价值来自避免二次止损，而不是简单减少交易 | `future_repeat_stop_5d/10d`、`future_missed_theta_if_blocked`、`future_cooldown_value` |
| 多品种开仓必须提高有效分散，而非重复卖同一个尾部 | `effective_product_count_stress`、`top5_product_stress_share`、`hhi_sector_stress` |
| 提高保证金使用率只有在特定 regime 下才合理 | `future_pnl_by_margin_bucket`、`future_calmar_by_margin_bucket`、`future_margin_squeeze` |
| 尾部相依性比普通相关更能解释组合回撤 | `empirical_lower/upper_tail_dependence`、`tail_dependence_jump`、`future_tail_cluster_loss` |
| 执行质量会显著影响止损与收益 | `tick_value_ratio`、`stale_price_ratio`、`future_stop_overshoot`、`future_false_stop_reversal` |

### 18.3 B5 的开发边界

B5 full shadow V1 应坚持三个边界：

| 边界 | 含义 |
| --- | --- |
| 不直接交易化 | 所有新增因子先只进入 shadow universe 和研究面板，不改变 B1/B4 交易规则。 |
| 不放宽核心风险线 | `abs(delta)<0.10`、费用和低价过滤、真实保证金和真实手续费口径继续保留。 |
| 不用未来标签做信号 | `future_*`、shadow 止损和 future cooldown value 只能用于研究，不得回流成 T 日可用因子。 |

### 18.4 最终落地原则

只有同时满足以下条件的因子，才有资格进入 B5 交易规则：

| 条件 | 说明 |
| --- | --- |
| 层级正确 | 合约因子用于合约排序，P/C 因子用于方向预算，组合因子用于预算和风控，不跨层滥用。 |
| 经济解释清楚 | 能解释卖方收益、止损、vega/gamma 损耗、执行污染或组合尾部之一。 |
| 分层稳定 | Q1-Q5、累计 IC、non-overlap IC、年份切片和 regime 切片不能只在单段样本好看。 |
| 有增量信息 | 控制 premium、margin、DTE、delta、stress 后仍有 residual IC 或明确风险解释。 |
| 不牺牲核心目标 | 不能通过砍掉 theta 或增加隐含方向押注来换取表面低回撤。 |
| 符合 S1 总目标 | 最终交易化版本必须服务于年化收益、最大回撤、Calmar、theta capture 和 vega 归因改善。 |

一句话总结：B5 的重点不是“找更多因子”，而是建立一个能判断因子该放在哪里、能解释什么、是否真的改善卖方风险收益的 full shadow 研究系统。只有通过这套系统验证的因子，才进入后续交易规则；否则即使短期回测好看，也只能留在研究储备中。
