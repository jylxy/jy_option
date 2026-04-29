# S1 因子库使用决策表：B2 / B3 / B5 合并版

生成日期：2026-04-29  
适用策略：S1 日频低 Delta 纯卖权策略  
适用阶段：B6/B7 之前的因子归位、去共线和交易规则设计  

## 1. 使用原则

本表的目标不是把所有因子堆进一个综合分，而是明确每个因子应该服务于哪个交易决策层级。

统一收益拆解公式为：

```text
S1 收益
= Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

因子归位遵守以下规则：

1. 同一个因子只能有一个主要使用层级。如果在合约层用于排序，就不再在品种层或 P/C 侧重复加分。
2. 高共线因子只保留一个代表，其余作为诊断、对照或暂不采用。
3. 硬过滤因子不当 alpha。费用、低价、tick、异常价、流动性这类指标用于剔除不可交易候选，不用于放大预算。
4. 合约层因子回答“同一品种、同一方向、同一次月里卖哪张合约”。
5. P/C 侧因子回答“今天这个品种更适合偏 Put、偏 Call，还是双卖”。
6. 品种层因子回答“今天哪些品种可以多给预算”，但当前品种层证据弱于合约层，先保持谨慎。
7. 组合层因子回答“这些品种、方向、到期、板块和尾部风险能不能一起卖”，不能用普通合约 IC 评价。

## 2. 当前去共线后的主线代表因子

| 因子族 | 主线代表 | 被合并或降级的高共线因子 | 主用层级 | 用途 |
| --- | --- | --- | --- | --- |
| IV shock 覆盖 | `premium_to_iv10_loss` | `premium_to_iv5_loss`、`premium_to_iv_shock_score`、`b3_iv_shock_coverage`、`b5_premium_per_vega` | 合约层 | 合约排序 |
| Stress 覆盖 | `premium_to_stress_loss` | `premium_to_stress_loss_score`、`b3_joint_stress_coverage` | 合约层 | 合约排序 / stress 覆盖 |
| 资本效率 | `premium_yield_margin` | `premium_yield_notional`、`b5_premium_per_capital_day` | 合约层 | 辅助排序，不单独加预算 |
| Vega 凸性 | `b3_vomma_loss_ratio` | 无，和 IV shock 高相关但经济含义不同 | 合约层 | 风险惩罚 / 排序辅助 |
| Gamma 租金 | `b5_theta_per_gamma` | `gamma_rent_penalty`、`gamma_rent_cash`、`b5_gamma_theta_ratio` | 合约层 | Gamma 风险惩罚 |
| Vega 租金 | `b5_theta_per_vega` | `theta_vega_efficiency`、`theta_vega_efficiency_score` | 合约层 | Vega 风险惩罚 |
| 尾部移动覆盖 | `b5_premium_to_tail_move_loss` | `b5_premium_to_mae20_loss`、`b5_premium_to_expected_move_loss` | 合约层 | 尾部风险惩罚 |
| 交易摩擦 | `friction_ratio` | `fee_ratio`、`slippage_ratio`、`cost_liquidity_score` | 合约层 | 硬过滤 |
| 低价 / tick | `b5_tick_value_ratio` | `b5_low_price_flag` | 合约层 | 硬过滤 |
| P/C 趋势 | `b5_trend_z_20d` | `b5_mom_5d`、`b5_mom_20d`、`b5_mom_60d` | P/C 侧 | 方向预算偏移 |
| P/C 突破 | `b5_breakout_distance_up_60d` / `b5_breakout_distance_down_60d` | `b5_up_day_ratio_20d`、`b5_down_day_ratio_20d` | P/C 侧 | 趋势侧风险惩罚 |
| IV 状态 | `b5_iv_reversion_score` | `b5_atm_iv_mom_5d`、`b5_atm_iv_mom_20d`、`b5_atm_iv_accel`、`b5_iv_zscore_60d` | 时间 / 品种层 | 降波释放、升波收缩 |
| Vol-of-vol | `b3_vol_of_vol_proxy` | `b3_vov_trend` | 时间 / 品种层 | 升波不稳定惩罚 |
| 冷静期 | `b5_cooldown_release_score` | `b5_cooldown_penalty_score`、`b5_cooldown_blocked`、止损计数字段 | 品种方向层 | 重开 / 降权规则 |
| Delta 梯队 | `b5_delta_ratio_to_cap` | `b5_delta_to_cap`、delta bucket 衍生字段 | 合约层 | 梯队位置约束 |
| IV/RV carry | 暂不交易化 | `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate`、`iv_rv_carry_score`、`b5_variance_carry_forward` | 品种层诊断 | 需要 forward RV 重构 |
| B4 综合分 | 暂不作为新因子 | `b4_contract_score` 及其子分 | 诊断 | 避免黑箱重复计分 |

## 3. 完整因子使用表

| 因子 / 字段 | 来源 | 因子族 | 唯一使用层级 | 使用方式 | 采用状态 | 改善公式变量 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gross_premium_cash_1lot` | B2 | premium | 诊断层 | 诊断 | 采用为诊断 | Premium Pool | 毛权利金池，用于观察可卖权利金总量，不直接排序。 |
| `net_premium_cash_1lot` | B2 | premium | 合约层 | 硬过滤 | 采用 | Premium Pool / Cost | 净权利金必须覆盖手续费和最小滑点；低于阈值不做。 |
| `premium_yield_margin` | B2 | premium | 合约层 | 排序辅助 | 采用 | Deployment Ratio | 单位保证金权利金效率；不能单独决定加仓，需叠加 vega/gamma/tail。 |
| `premium_yield_notional` | B2 | premium | 诊断层 | 诊断 | 降级 | Premium Pool | 与资本效率同族，但名义本金分母敏感；不进入主评分。 |
| `premium_margin` | B2 | premium | 合约层 | 排序辅助 | 合并 | Deployment Ratio | 与 `premium_yield_margin` 含义接近，主线统一用 `premium_yield_margin`。 |
| `premium_stress` | B2 | tail | 合约层 | 排序辅助 | 合并 | Tail / Stop Loss | 与 `premium_to_stress_loss` 同族，主线用后者。 |
| `theta_stress` | B2 | gamma/tail | 合约层 | 诊断 | 降级 | Retention Rate / Tail | 用于解释 theta 是否足够覆盖压力风险，暂不单独打分。 |
| `rv_ref` | B2 | vol | 品种层 | 诊断 | 暂不交易化 | Tail / Stop Loss | 只是历史 RV 参考，不是 forward RV，不能直接决定预算。 |
| `iv_rv_spread_candidate` | B2 | variance carry | 品种层 | 诊断 | 暂不交易化 | Retention Rate | 理论重要，但当前审计弱；等待 forward RV 重构。 |
| `iv_rv_ratio_candidate` | B2 | variance carry | 品种层 | 诊断 | 暂不交易化 | Retention Rate | 比率分母敏感，低 RV 环境容易失真。 |
| `variance_carry` | B2 | variance carry | 品种层 | 诊断 | 暂不交易化 | Retention Rate | 理论上应做品种预算，但当前 IC 不支持进入交易。 |
| `iv_rv_carry_score` | B2 | variance carry | 品种层 | 诊断 | 暂不交易化 | Retention Rate | 旧版综合 carry 分，暂不使用。 |
| `breakeven_price` | B2 | cushion | 合约层 | 诊断 | 采用为诊断 | Tail / Stop Loss | 原始盈亏平衡价，不直接评分。 |
| `breakeven_cushion_abs` | B2 | cushion | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 合并进 `breakeven_cushion_score`。 |
| `breakeven_cushion_iv` | B2 | cushion | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 合并进 `breakeven_cushion_score`。 |
| `breakeven_cushion_rv` | B2 | cushion | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 合并进 `breakeven_cushion_score`。 |
| `breakeven_cushion_score` | B2 | cushion | 合约层 | 风控惩罚 | 采用 | Tail / Stop Loss | 收益 IC 不强，但 stop avoidance 有价值；只惩罚安全垫过薄，不正向加预算。 |
| `iv_shock_loss_5_cash` | B2 | vega | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 原始损失金额，合并到覆盖率因子。 |
| `iv_shock_loss_10_cash` | B2 | vega | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 原始损失金额，合并到覆盖率因子。 |
| `premium_to_iv5_loss` | B2 | vega | 合约层 | 诊断 | 降级 | Retention Rate | 与 `premium_to_iv10_loss` 同族，保留 10vol 作为主代表。 |
| `premium_to_iv10_loss` | B2 | vega | 合约层 | 排序 | 采用 | Retention Rate / Tail | 核心合约排序因子，衡量权利金对 IV shock 的覆盖。 |
| `premium_to_iv_shock_score` | B2 | vega | 合约层 | 诊断 | 合并 | Retention Rate | 与 `premium_to_iv10_loss` 合并，不重复打分。 |
| `stress_loss` | B2 | tail | 合约层 | 诊断 / 上限 | 采用为约束 | Tail / Stop Loss | 压力亏损绝对值，用于组合与合约风险约束，不做正向排序。 |
| `premium_to_stress_loss` | B2 | tail | 合约层 | 排序 | 采用 | Retention Rate / Tail | 核心合约排序因子，衡量权利金对 spot+IV 压力的覆盖。 |
| `premium_to_stress_loss_score` | B2 | tail | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 与 `premium_to_stress_loss` 合并。 |
| `cash_theta` | B2 | theta | 合约层 | 诊断 | 采用为诊断 | Retention Rate | 不单独追 theta，避免买入过多 gamma/vega 风险。 |
| `cash_vega` | B2 | vega | 合约层 | 风险约束 | 采用 | Tail / Stop Loss | 用于 cash vega 上限和归因，不做正向排序。 |
| `theta_vega_efficiency` | B2 | vega | 合约层 | 诊断 | 合并 | Retention Rate | 主线改用 `b5_theta_per_vega`。 |
| `theta_vega_efficiency_score` | B2 | vega | 合约层 | 诊断 | 合并 | Retention Rate | 主线改用 `b5_theta_per_vega`。 |
| `cash_gamma` | B2 | gamma | 合约层 | 风险约束 | 采用 | Tail / Stop Loss | 用于 cash gamma 上限和归因，不做正向排序。 |
| `gamma_rent_cash` | B2 | gamma | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 与 `gamma_rent_penalty` 同族。 |
| `gamma_rent_penalty` | B2 | gamma | 合约层 | 诊断 / 惩罚 | 降级 | Tail / Stop Loss | 与 `premium_to_stress_loss` 高共线；主线优先 `b5_theta_per_gamma`。 |
| `open_fee_per_contract` | B2 | cost | 合约层 | 诊断 | 合并 | Cost / Slippage | 合并到 `friction_ratio`。 |
| `close_fee_per_contract` | B2 | cost | 合约层 | 诊断 | 合并 | Cost / Slippage | 合并到 `friction_ratio`。 |
| `roundtrip_fee_per_contract` | B2 | cost | 合约层 | 硬过滤输入 | 采用 | Cost / Slippage | 用于判断权利金是否覆盖往返费用。 |
| `fee_ratio` | B2 | cost | 合约层 | 诊断 | 合并 | Cost / Slippage | 与 `friction_ratio` 高共线，不重复使用。 |
| `slippage_ratio` | B2 | cost | 合约层 | 诊断 | 合并 | Cost / Slippage | 与 `friction_ratio` 合并。 |
| `friction_ratio` | B2 | cost | 合约层 | 硬过滤 | 采用 | Cost / Slippage | 交易卫生条件，不当 alpha；超过阈值直接剔除。 |
| `cost_liquidity_score` | B2 | liquidity | 合约层 | 硬过滤辅助 | 采用为辅助 | Cost / Slippage | 只服务可成交性，不进入品种预算。 |
| `premium_quality_score` | B2 | composite | 诊断层 | 对照 / 控制 | 不交易化 | 多变量 | 旧综合分，作为 benchmark 或残差控制，不进新规则。 |
| `premium_quality_rank_in_side` | B2 | composite | 诊断层 | 对照 | 不交易化 | 多变量 | 旧排序结果，用于复盘，不再直接使用。 |
| `entry_iv_trend` | B3 | vol regime | 时间 / 品种层 | 诊断 | 合并 | Retention Rate | 合并到 IV 状态与 vol-of-vol 规则。 |
| `contract_iv_change_1d` | B3 | vol regime | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 用于解释 IV 短期变化，不单独交易。 |
| `contract_iv_change_3d` | B3 | vol regime | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 用于解释 IV 短期变化，不单独交易。 |
| `contract_iv_change_5d` | B3 | vol regime | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 用于解释 IV 短期变化，不单独交易。 |
| `b3_near_atm_iv` | B3 | term structure | 时间 / 品种层 | 诊断 | 采用为诊断 | Retention Rate | ATM IV 曲面基础字段，不直接打分。 |
| `b3_next_atm_iv` | B3 | term structure | 时间 / 品种层 | 诊断 | 采用为诊断 | Retention Rate | ATM IV 曲面基础字段，不直接打分。 |
| `b3_far_atm_iv` | B3 | term structure | 时间 / 品种层 | 诊断 | 采用为诊断 | Retention Rate | ATM IV 曲面基础字段，不直接打分。 |
| `b3_term_structure_pressure` | B3 | term structure | 时间 / 品种层 | 风险惩罚 | 观察采用 | Tail / Stop Loss | 倒挂或近端压力用于降预算，不用于选执行价。 |
| `b3_forward_variance_pressure` | B3 | vol regime | 时间 / 品种层 | 风险惩罚 | 观察采用 | Tail / Stop Loss | 升波压力惩罚，不正向加预算。 |
| `b3_vol_of_vol_proxy` | B3 | vol-of-vol | 时间 / 品种层 | 风险惩罚 | 采用 | Tail / Stop Loss | 控制 IV 自身不稳定；不是越低越无脑加仓。 |
| `b3_vov_trend` | B3 | vol-of-vol | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 合并到 `b3_vol_of_vol_proxy` 或 IV 状态。 |
| `b3_iv_shock_coverage` | B3 | vega | 合约层 | 诊断 | 合并 | Retention Rate | 与 `premium_to_iv10_loss` 高共线，后者为代表。 |
| `b3_joint_stress_coverage` | B3 | tail | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 与 `premium_to_stress_loss` 高共线，后者为代表。 |
| `b3_vomma_cash` | B3 | vomma | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 原始 vomma 金额，主线用 ratio。 |
| `b3_vomma_loss_ratio` | B3 | vomma | 合约层 | 风险惩罚 / 排序辅助 | 采用 | Tail / Stop Loss | 控制 short-vol 凸性风险；不作为品种预算放大器。 |
| `contract_iv_skew_to_atm` | B3 | skew | P/C 侧 | 风险惩罚 | 采用为侧向因子 | Tail / Stop Loss | skew 贵不是天然机会，结合趋势和 RV 判断 P/C。 |
| `contract_skew_change_for_vega` | B3 | skew | P/C 侧 | 风险惩罚 | 采用为侧向因子 | Tail / Stop Loss | skew 变陡时降低对应方向预算。 |
| `b3_skew_steepening` | B3 | skew | P/C 侧 | 风险惩罚 | 采用为侧向因子 | Tail / Stop Loss | P/C 方向风险因子，不用于合约层重复排序。 |
| `b4_contract_score` | B4 | composite | 诊断层 | 对照 | 不交易化 | 多变量 | 旧综合分，只用于比较，不进入新因子库打分。 |
| `b4_premium_to_iv10_score` | B4 | composite | 诊断层 | 对照 | 合并 | Retention Rate | 已由 `premium_to_iv10_loss` 代表。 |
| `b4_premium_to_stress_score` | B4 | composite | 诊断层 | 对照 | 合并 | Tail / Stop Loss | 已由 `premium_to_stress_loss` 代表。 |
| `b4_premium_yield_margin_score` | B4 | composite | 诊断层 | 对照 | 合并 | Deployment Ratio | 已由 `premium_yield_margin` 代表。 |
| `b4_gamma_rent_score` | B4 | composite | 诊断层 | 对照 | 合并 | Tail / Stop Loss | 已由 gamma / theta 代表因子覆盖。 |
| `b4_vomma_score` | B4 | composite | 诊断层 | 对照 | 合并 | Tail / Stop Loss | 已由 `b3_vomma_loss_ratio` 覆盖。 |
| `b4_breakeven_cushion_score` | B4 | composite | 诊断层 | 对照 | 合并 | Tail / Stop Loss | 已由 `breakeven_cushion_score` 覆盖。 |
| `b4_vol_of_vol_score` | B4 | composite | 诊断层 | 对照 | 合并 | Tail / Stop Loss | 已由 `b3_vol_of_vol_proxy` 覆盖。 |
| `b5_delta_to_cap` | B5 | delta | 合约层 | 诊断 | 合并 | Premium Pool / Tail | 与 `b5_delta_ratio_to_cap` 同族，后者为代表。 |
| `b5_delta_ratio_to_cap` | B5 | delta | 合约层 | 风控惩罚 / 梯队排序 | 采用 | Premium Pool / Tail | 只在 delta<0.1 内部决定更靠近 0.1 还是更深虚。 |
| `b5_premium_share_delta_bucket` | B5 | delta ladder | P/C 侧 | 诊断 | 采用为诊断 | Premium Pool | 观察各 delta 桶权利金贡献，不直接排序合约。 |
| `b5_stress_share_delta_bucket` | B5 | delta ladder | P/C 侧 | 风险约束 | 采用为诊断 | Tail / Stop Loss | 观察某侧 stress 是否集中在某个 delta 桶。 |
| `b5_theta_per_gamma` | B5 | gamma | 合约层 | 风险惩罚 / 排序辅助 | 采用 | Retention Rate / Tail | 控制单位 theta 承担的 gamma 风险。 |
| `b5_gamma_theta_ratio` | B5 | gamma | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 与 `b5_theta_per_gamma` 互为倒数方向，主线用后者。 |
| `b5_theta_per_vega` | B5 | vega | 合约层 | 风险惩罚 / 排序辅助 | 采用 | Retention Rate / Tail | 控制单位 theta 承担的 vega 风险，是止损规避重要因子。 |
| `b5_premium_per_vega` | B5 | vega | 合约层 | 诊断 | 合并 | Retention Rate | 与 `premium_to_iv10_loss` 高共线，不重复计分。 |
| `b5_premium_to_expected_move_loss` | B5 | tail | 合约层 | 诊断 | 合并 | Tail / Stop Loss | 与 tail/MAE 覆盖同族，主线优先 tail move。 |
| `b5_premium_to_mae20_loss` | B5 | tail | 合约层 | 风控惩罚辅助 | 降级采用 | Tail / Stop Loss | 与 tail move coverage 同族，可作为二级诊断。 |
| `b5_premium_to_tail_move_loss` | B5 | tail | 合约层 | 风控惩罚 / 排序辅助 | 采用 | Tail / Stop Loss | 控制历史尾部不利移动覆盖率，残差 IC 较有价值。 |
| `b5_mom_5d` | B5 | trend | P/C 侧 | 诊断 | 合并 | Tail / Stop Loss | 短动量噪音较大，合并到趋势族。 |
| `b5_mom_20d` | B5 | trend | P/C 侧 | 诊断 | 合并 | Tail / Stop Loss | 与 `b5_trend_z_20d` 高相关，后者为代表。 |
| `b5_mom_60d` | B5 | trend | P/C 侧 | 诊断 | 降级 | Tail / Stop Loss | 中期趋势参考，不直接打分。 |
| `b5_trend_z_20d` | B5 | trend | P/C 侧 | 预算偏移 | 采用 | Tail / Stop Loss | 用于 Put/Call 方向偏移，不用于合约排序。 |
| `b5_breakout_distance_up_60d` | B5 | breakout | P/C 侧 | 方向侧惩罚 | 采用 | Tail / Stop Loss | 接近上突破时降低 Call 侧预算或收紧 Call delta。 |
| `b5_breakout_distance_down_60d` | B5 | breakout | P/C 侧 | 方向侧惩罚 | 采用 | Tail / Stop Loss | 接近下突破时降低 Put 侧预算。 |
| `b5_up_day_ratio_20d` | B5 | trend | P/C 侧 | 诊断 | 合并 | Tail / Stop Loss | 与趋势/突破同族，暂不重复使用。 |
| `b5_down_day_ratio_20d` | B5 | trend | P/C 侧 | 诊断 | 合并 | Tail / Stop Loss | 与趋势/突破同族，暂不重复使用。 |
| `b5_range_expansion_proxy_20d` | B5 | regime | 时间 / 品种层 | 风险惩罚 | 采用为观察 | Tail / Stop Loss | 识别波动区间扩张，升波早期降低预算。 |
| `b5_atm_iv_mom_5d` | B5 | IV state | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 合并到 `b5_iv_reversion_score`。 |
| `b5_atm_iv_mom_20d` | B5 | IV state | 时间 / 品种层 | 诊断 | 合并 | Tail / Stop Loss | 合并到 `b5_iv_reversion_score`。 |
| `b5_atm_iv_accel` | B5 | IV state | 时间 / 品种层 | 风险惩罚输入 | 合并 | Tail / Stop Loss | IV 加速上行时降低预算，合并到 reversion / vov 规则。 |
| `b5_iv_zscore_60d` | B5 | IV state | 时间 / 品种层 | 诊断 | 合并 | Retention Rate | 仅高 IV 不等于可卖，需结合是否回落。 |
| `b5_iv_reversion_score` | B5 | IV state | 时间 / 品种层 | 预算释放 / 惩罚 | 采用 | Retention Rate / Tail | 降波确认时释放预算，升波未止时收缩预算。 |
| `b5_days_since_product_stop` | B5 | cooldown | 品种层 | 冷静期输入 | 合并 | Tail / Stop Loss | 合并到冷静期规则，不单独使用。 |
| `b5_product_stop_count_20d` | B5 | cooldown | 品种层 | 风险惩罚 | 采用为输入 | Tail / Stop Loss | 近期止损聚集时降低该品种预算。 |
| `b5_days_since_product_side_stop` | B5 | cooldown | P/C 侧 | 冷静期输入 | 合并 | Tail / Stop Loss | 合并到冷静期规则，不单独使用。 |
| `b5_product_side_stop_count_20d` | B5 | cooldown | P/C 侧 | 风险惩罚 | 采用为输入 | Tail / Stop Loss | 同品种同方向连续止损时降低该侧预算。 |
| `b5_cooldown_blocked` | B5 | cooldown | P/C 侧 | 硬阻断输入 | 观察采用 | Tail / Stop Loss | 只在止损后 IV/RV/skew 仍恶化时阻断。 |
| `b5_cooldown_penalty_score` | B5 | cooldown | P/C 侧 | 风险惩罚 | 采用 | Tail / Stop Loss | 近期止损严重、重复止损概率高时降权。 |
| `b5_cooldown_release_score` | B5 | cooldown | P/C 侧 | 重开释放 | 采用 | Deployment Ratio / Tail | IV/RV/skew 回落后解除冷静期。 |
| `b5_tick_value_ratio` | B5 | liquidity/tick | 合约层 | 硬过滤 | 采用 | Cost / Slippage | tick 占价格比例过高直接剔除或强惩罚。 |
| `b5_low_price_flag` | B5 | liquidity/tick | 合约层 | 硬过滤输入 | 合并 | Cost / Slippage | 与 tick/price 规则合并，低价合约不参与主线。 |
| `b5_variance_carry_forward` | B5 | variance carry | 品种层 | 诊断 | 暂不交易化 | Retention Rate | 与 `variance_carry` 同族，需等 forward RV 口径稳定。 |
| `b5_capital_lockup_days` | B5 | capital | 合约层 | 诊断 / 惩罚 | 观察采用 | Deployment Ratio | 资金锁定过长时降低合约优先级，不做品种预算。 |
| `b5_premium_per_capital_day` | B5 | capital | 合约层 | 诊断 | 合并 | Deployment Ratio | 与 `premium_yield_margin` 高共线，后者为主代表。 |

## 4. 组合层与下一批 full shadow 因子

以下因子属于组合层或执行层，不能拿普通合约 IC 来判断，也不能进入合约排序。它们应该进入 B7 组合风控或 B5/B6 full shadow 扩展面板。

| 因子 / 字段 | 因子族 | 唯一使用层级 | 使用方式 | 采用状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `sector_stress_exposure` | sector | 组合层 | 风控上限 | 待实现 | 板块 stress loss 聚集上限。 |
| `directional_crowding_put/call` | direction | 组合层 | 风控上限 | 待实现 | Put 或 Call 侧方向暴露拥挤控制。 |
| `expiry_cluster_score` | expiry | 组合层 | 风控上限 | 待实现 | 同到期 gamma 与滚动压力集中控制。 |
| `effective_product_count` | concentration | 组合层 | 风险诊断 | 待实现 | 用 stress 权重计算真实有效品种数。 |
| `top_stress_share` | concentration | 组合层 | 风控上限 | 待实现 | Top N 品种 stress 占比上限。 |
| `stop_cluster_score` | cooldown/tail | 组合层 | 风险惩罚 | 待实现 | 多品种同日止损风险，决定总预算收缩。 |
| `empirical_lower_tail_dependence_95` | tail corr | 组合层 | 风控上限 | 待实现 | Put 侧下尾相依性。 |
| `empirical_upper_tail_dependence_95` | tail corr | 组合层 | 风控上限 | 待实现 | Call 侧上尾相依性。 |
| `tail_kendall_tau` | tail corr | 组合层 | 风控诊断 | 待实现 | 尾部秩相关，辅助 HRP。 |
| `tail_dependence_jump` | tail corr | 组合层 | 总预算收缩 | 待实现 | 尾部相关突然抬升时降低总保证金目标。 |
| `tail_corr_first_pc_share` | tail corr | 组合层 | 总预算收缩 | 待实现 | 尾部相关第一主成分占比，衡量分散失效。 |
| `product_delta_covar` | tail risk | 品种层 | 品种预算降权 | 待实现 | 某品种处于尾部时组合 VaR 增量。 |
| `tail_es_contribution` | tail risk | 品种层 | 品种预算降权 | 待实现 | 单品种对组合 ES 的贡献。 |
| `tail_network_centrality` | tail risk | 品种层 | 品种预算降权 | 待实现 | 尾部相依网络中的中心性，中心品种降权。 |
| `margin_shock_ratio` | margin | 组合层 | 风控上限 | 待实现 | 压力情景下保证金相对当前保证金放大倍数。 |
| `premium_to_stressed_margin` | margin | 合约层 | 排序辅助 | 待实现 | 权利金对压力保证金占用的覆盖。 |
| `jump_share_20d` | jump | 品种层 | 风险惩罚 | 待实现 | 标的跳跃波动占比，跳跃高则卖方路径风险高。 |
| `gap_share_20d` / `gap_frequency_20d` | gap | 品种层 | 风险惩罚 | 待实现 | 开盘跳空风险，影响止损可执行性。 |
| `stale_price_ratio` | data quality | 合约层 | 硬过滤 | 待实现 | 陈旧价格占比，防止估值污染。 |
| `pcp_deviation` | data quality | 合约层 | 硬过滤 / 诊断 | 待实现 | PCP 偏离过大说明报价或标的映射异常。 |
| `iv_solve_fail_rate` | data quality | 合约层 | 硬过滤 / 诊断 | 待实现 | IV 反解质量差的合约不参与排序。 |
| `stop_overshoot_prior` | execution | 品种层 | 冷静期惩罚 | 待实现 | 历史止损越界幅度高说明退出质量差。 |
| `false_stop_prior` | execution | 品种层 | 诊断 / 止损规则 | 待实现 | 止损后快速回归概率，用于研究止损确认。 |
| `exit_capacity_score` | liquidity | 合约层 | 硬过滤 / 风控 | 待实现 | 止损时能否出，比开仓流动性更重要。 |

## 5. 按层级的最终使用清单

### 5.1 合约层：排序与硬过滤

合约层只回答“卖哪张合约”。主线使用：

```text
硬过滤：
entry_price / option_price
net_premium_cash_1lot
roundtrip_fee_per_contract
friction_ratio
b5_tick_value_ratio
volume / open_interest
```

```text
排序主因子：
premium_to_iv10_loss
premium_to_stress_loss
premium_yield_margin
b5_theta_per_gamma
b5_theta_per_vega
b3_vomma_loss_ratio
b5_premium_to_tail_move_loss
b5_delta_ratio_to_cap
```

```text
排序惩罚或辅助：
breakeven_cushion_score
b5_capital_lockup_days
stress_loss
cash_gamma
cash_vega
```

不在合约层使用：

```text
trend / momentum
skew steepening
cooldown
tail correlation
sector concentration
product stop cluster
```

### 5.2 P/C 侧：方向预算

P/C 侧只回答“今天偏 Put、偏 Call，还是双卖”。主线使用：

```text
b5_trend_z_20d
b5_breakout_distance_up_60d
b5_breakout_distance_down_60d
contract_iv_skew_to_atm
contract_skew_change_for_vega
b3_skew_steepening
b5_product_side_stop_count_20d
b5_cooldown_penalty_score
b5_cooldown_release_score
```

不在 P/C 侧使用：

```text
premium_to_iv10_loss
premium_to_stress_loss
premium_yield_margin
friction_ratio
tick_value_ratio
```

这些是合约层或硬过滤因子，不重复给 P/C 预算加分。

### 5.3 品种层：预算倾斜

当前品种层证据弱于合约层，所以第一版只做轻量预算，不做黑名单。

主线可用：

```text
b5_iv_reversion_score
b3_vol_of_vol_proxy
b5_range_expansion_proxy_20d
b5_product_stop_count_20d
```

暂不交易化：

```text
variance_carry
iv_rv_spread_candidate
iv_rv_ratio_candidate
iv_rv_carry_score
b5_variance_carry_forward
```

原因是当前 IV/RV carry 口径还没有在 corrected full shadow 中稳定通过，需要先重构 forward RV。

### 5.4 组合层：风控与预算上限

组合层不参与合约排序，后续进入 B7：

```text
sector_stress_exposure
directional_crowding_put/call
expiry_cluster_score
effective_product_count
top_stress_share
stop_cluster_score
tail_dependence_jump
empirical_lower_tail_dependence_95
empirical_upper_tail_dependence_95
tail_es_contribution
margin_shock_ratio
```

### 5.5 诊断层：不交易化但必须监控

```text
premium_quality_score
premium_quality_rank_in_side
b4_contract_score
b4_*_score
gross_premium_cash_1lot
cash_theta
cash_gamma
cash_vega
rv_ref
b3_near_atm_iv
b3_next_atm_iv
b3_far_atm_iv
```

这些字段用于报告、归因、对照和解释，不作为直接交易规则。

## 6. 建议的 B6 因子落地方式

B6 不建议继续堆综合分，而是按层级落地：

```text
B6a：只改合约层排序
硬过滤不变；
合约排序使用 premium_to_iv10_loss、premium_to_stress_loss、
premium_yield_margin、b5_theta_per_gamma、b5_theta_per_vega、
b3_vomma_loss_ratio、b5_premium_to_tail_move_loss。
```

```text
B6b：在 B6a 上加入 P/C 侧预算
趋势、突破、skew、同方向止损冷却决定 Put/Call 权重；
不让合约层 premium 因子重复影响 P/C 预算。
```

```text
B6c：在 B6b 上加入品种层波动状态
只用 b5_iv_reversion_score、b3_vol_of_vol_proxy、range expansion、
product stop count 做轻量预算释放或收缩；
暂不使用 IV/RV carry 作为预算 alpha。
```

```text
B7：单独做组合层
tail dependence、stop cluster、sector stress、expiry cluster、margin shock
进入组合预算和上限，不参与 B6 的合约排序。
```

