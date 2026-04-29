# S1 卖权策略因子库初版：Full Shadow 校正审计结果

生成日期：2026-04-29  
样本标签：`s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest`  
分析目录：`output/candidate_layers_corrected_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest/`

## 口径说明

本表用于后续建立 S1 因子库，不直接等同于策略上线规则。

IC 口径为 Spearman Rank IC，因子方向已统一调整为“越高越好”。主样本为 `completed + premium >= 100`。  
`Contract IC` 主要用于合约排序；`Product-side IC` 主要用于品种/Put-Call 方向预算；`Stop IC` 用于止损风险控制；`Residual IC` 是控制 `entry_price`、`open_premium_cash`、`margin_estimate`、`stress_loss`、`DTE`、`abs_delta` 后的残差 IC；`Non-overlap IC` 为每 5 个信号日抽样后的稳健性检验。

注意：full shadow outcome 使用次日收盘价作为入场价、日频收盘逻辑判断止损，因此它是候选质量研究标签，不是完整实盘成交标签。

## 因子库表

| 因子族群 | 因子 | 可用位置 | 因子说明 | 对应可用领域 IC | 使用结论 |
| --- | --- | --- | --- | --- | --- |
| 资本效率 | `premium_yield_margin` | 合约排序、资本效率微调 | 权利金相对保证金越厚越好，代表单位保证金能收多少 theta，但可能牺牲止损安全垫。 | Contract: Margin `0.226`, Cash `0.072`, Stop `-0.021`; 组内 Margin `0.374`; Product-side Margin `0.167`; Residual Margin `0.022`; Non-overlap Margin `0.208` | 可用，但不能单独追求高资本效率；必须叠加止损、gamma、流动性惩罚。 |
| IV 冲击覆盖 | `premium_to_iv10_loss` | 合约排序、轻度 product-side 预算倾斜 | 权利金对 IV +10vol 冲击损失的覆盖度，越高说明短 vega 承保补偿更厚。 | Contract: Margin `0.201`, Cash `0.108`, Stress `0.215`, Stop `0.018`; 组内 Margin `0.355`; Product-side Margin `0.155`; Residual Margin `0.024`; Non-overlap Margin `0.199` | 核心可用因子，适合做合约排序；与 `b3_iv_shock_coverage` 高度共线，二选一或合并为同一因子族。 |
| Vega 凸性风险 | `b3_vomma_loss_ratio_low` | 合约排序、Vega 凸性惩罚 | IV 凸性/vomma 损失相对权利金越低越好，用来约束短波尾部凸性。 | Contract: Margin `0.213`, Cash `0.107`, Stress `0.193`, Stop `0.006`; 组内 Margin `0.366`; Product-side Margin `0.142`; Residual Margin `-0.007`; Non-overlap Margin `0.209` | 可用作风险覆盖排序，但残差 IC 弱，说明很大部分来自权利金/风险分母结构；不宜单独加预算。 |
| IV 冲击覆盖 | `b3_iv_shock_coverage` | 合约排序、Vega 风险控制 | 权利金对 IV shock 的覆盖度，和 `premium_to_iv10_loss` 几乎等价。 | Contract: Margin `0.198`, Cash `0.108`, Stress `0.219`, Stop `0.020`; 组内 Margin `0.351`; Product-side Margin `0.155`; Residual Margin `0.025`; Non-overlap Margin `0.196` | 可用，但与 `premium_to_iv10_loss` 相关性约 `0.999`，不能重复计票。 |
| 联合压力覆盖 | `b3_joint_stress_coverage` | 合约排序、压力预算 | 权利金对 spot/IV 联合压力亏损的覆盖度，和 `premium_to_stress_loss` 等价。 | Contract: Margin `0.187`, Cash `0.086`, Stress `0.241`, Stop `0.026`; 组内 Margin `0.258`; Product-side Margin `0.171`; Residual Margin `0.026`; Non-overlap Margin `0.186` | 可用作 stress budget 排序；与 `premium_to_stress_loss` 相关性约 `1.000`，保留一个代表即可。 |
| 压力损失覆盖 | `premium_to_stress_loss` | 合约排序、压力预算、轻度 product-side 倾斜 | 权利金对压力亏损的覆盖度，越高说明这笔卖权的承保补偿更厚。 | Contract: Margin `0.187`, Cash `0.086`, Stress `0.241`, Stop `0.026`; 组内 Margin `0.258`; Product-side Margin `0.171`; Residual Margin `0.026`; Non-overlap Margin `0.186` | 核心可用因子，适合做压力预算和合约排序；不要和高度等价指标重复加权。 |
| 权利金厚度/分母敏感 | `premium_yield_notional` | 合约排序辅助 | 权利金相对名义本金越厚越好，但容易受低价、合约乘数和分母结构影响。 | Contract: Margin `0.198`, Cash `0.115`, Stop `-0.005`; 组内 Margin `0.376`; Product-side Margin `0.114`; Residual Margin `0.024`; Non-overlap Margin `0.194` | 可用作辅助排序，但不宜单独用；需要低价过滤和 stop 风险惩罚。 |
| Gamma/Theta 权衡 | `gamma_rent_penalty_low` | 合约排序、止损风险控制、压力收益排序 | gamma 租金/路径风险惩罚越低越好，用来避免短 gamma 太贵的合约。 | Contract: Margin `0.178`, Cash `0.066`, Stress `0.240`, Stop `0.026`; 组内 Margin `0.125`; Product-side Margin `0.177`; Residual Margin `0.014`; Non-overlap Margin `0.177` | 可用，尤其适合和 premium coverage 一起构建“theta 是否值得冒 gamma 风险”的指标。 |
| 费用占比 | `fee_ratio_low` | 硬过滤 | 手续费占权利金越低越好；与 `friction_ratio_low` 基本同源。 | Contract: Margin `0.127`, Cash `0.034`, Stop `-0.080`; 组内 Margin `0.376`; Product-side Margin `0.004`; Residual Margin `-0.030`; Non-overlap Margin `0.128` | 只做硬过滤或交易可行性检查，不能当 alpha；Stop IC 为负，说明低费率不等于更安全。 |
| 交易摩擦/分母卫生 | `friction_ratio_low` | 硬过滤、交易可行性控制 | 费用/权利金越低越好，用来识别低价、低权利金和手续费侵蚀陷阱。 | Contract: Margin `0.127`, Cash `0.034`, Stop `-0.080`; 组内 Margin `0.376`; Product-side Margin `0.004`; Residual Margin `-0.030`; Non-overlap Margin `0.128` | 只做硬过滤，不做预算加权；原始 IC 高主要来自比例标签和低价合约扭曲。 |
| 安全垫/盈亏平衡保护 | `breakeven_cushion_score` | 止损风险控制、合约排序惩罚项 | 安全垫越高越好，用来衡量标的需要移动多少才会显著威胁卖方。 | Contract: Margin `-0.093`, Cash `-0.015`, Stop `0.066`; 组内 Margin `-0.375`; Product-side Margin `0.066`; Residual Margin `0.014`; Non-overlap Margin `-0.080` | 不适合做收益排序；适合做止损概率、跳价风险和低安全垫惩罚。 |
| 成本与流动性质量 | `cost_liquidity_score` | 硬过滤、合约排序辅助 | 成本和流动性综合质量分，用于提高候选的实盘可成交性。 | Contract: Margin `0.090`, Cash `0.028`, Stop `-0.037`; 组内 Margin `0.294`; Product-side Margin `-0.071`; Residual Margin `0.006`; Non-overlap Margin `0.075` | 用于硬过滤和执行质量控制；不适合做品种预算。 |
| Forward 波动压力 | `b3_forward_variance_pressure_low` | 环境惩罚、品种预算观察 | forward variance pressure 越低越好，用来避免升波压力中的卖权。 | Product-side: Margin `-0.057`, Cash `0.005`, Stop `0.006`; Residual Margin `-0.019`; Non-overlap Margin `-0.063` | 当前不支持正向加预算；可作为升波风险惩罚或观察变量。 |
| Vol-of-Vol 环境 | `b3_vol_of_vol_proxy_low` | 环境惩罚、冷却期/重开约束、止损风险控制 | vol-of-vol 越低代表波动状态更稳定，但低 vol-of-vol 不等于权利金更值得卖。 | Product-side: Margin `-0.029`, Cash `0.050`, Stop `0.078`; Contract Stop `0.039`; Residual Margin `0.036`; Non-overlap Margin `-0.044` | 适合做风险状态判断，不适合简单加仓；高 vol-of-vol 应提高门槛，低 vol-of-vol 只能解除部分惩罚。 |
| IV/RV 相对溢价 | `iv_rv_ratio_candidate` | 暂观察 | IV/RV ratio 理论上衡量相对波动溢价，但分母敏感，容易在低 RV 环境下失真。 | Product-side: Margin `-0.054`, Cash `0.023`, Stop `0.001`; Residual Margin `0.006`; Non-overlap Margin `-0.019` | 当前不采用；后续需要按期限、品种、未来 RV 标签重做。 |
| 综合权利金质量 | `premium_quality_score` | 综合分参考、回归控制项 | 旧版综合权利金质量分，可作为已有框架的对照或控制变量。 | Contract: Margin `0.025`; Product-side Margin `0.007`; Residual Margin `0.019`; Non-overlap Margin `0.028` | 暂不直接采用；适合作为 benchmark 或 residual control，不宜黑箱上线。 |
| IV-RV Spread | `iv_rv_spread_candidate` | 暂观察 | IV-RV spread 理论上衡量波动风险溢价厚度，但当前 product 层检验较弱。 | Product-side: Margin `-0.032`, Cash `0.033`, Stop `0.010`; Residual Margin `0.009`; Non-overlap Margin `-0.011` | 当前不采用；需要重新定义 future RV/forward RV 后再检验。 |
| IV/RV 方差 Carry | `variance_carry` | 暂观察 | IV 方差相对 RV 的 carry，理论上应服务于品种预算。 | Product-side: Margin `-0.012`, Cash `0.038`, Stop `0.017`; Residual Margin `0.003`; Non-overlap Margin `-0.009` | 理论重要但当前口径未通过；不进入下一版主线，后续重构标签后再测。 |

## 因子族群归并建议

| 因子族群 | 建议保留代表 | 可合并/降权因子 | 原因 |
| --- | --- | --- | --- |
| IV 冲击覆盖 | `premium_to_iv10_loss` | `b3_iv_shock_coverage` | 两者相关性约 `0.999`，经济含义高度一致。 |
| 压力损失覆盖 | `premium_to_stress_loss` | `b3_joint_stress_coverage` | 两者相关性约 `1.000`，保留一个即可。 |
| 交易摩擦 | `friction_ratio_low` | `fee_ratio_low` | 两者相关性约 `1.000`，只做硬过滤。 |
| IV/RV Carry | 暂不保留 | `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate` | 当前 IC 不支持进入预算层，后续需要重构口径。 |
| Vega 凸性 | `b3_vomma_loss_ratio_low` | 与 `premium_to_iv10_loss` 联合使用 | 与 IV shock 覆盖高度相关，但有不同的 convexity 解释，可作为惩罚项。 |
| Gamma 风险 | `gamma_rent_penalty_low` | 与 stress coverage 联合使用 | 与 stress coverage 高相关，但可解释为 gamma/theta 权衡。 |
| 环境状态 | `b3_vol_of_vol_proxy_low` | `b3_forward_variance_pressure_low` | 更适合做 regime penalty 和冷却期条件，不适合正向预算加权。 |

## 下一步建库规则

1. 因子库不只保存 IC，还必须保存“适用层级”。同一个因子在 contract 层有效，不代表能用于 product budget。
2. 高共线因子只保留一个代表，或者先做因子族打分，再进入组合评分。
3. `friction_ratio`、`fee_ratio`、低价过滤、低权利金过滤属于卫生条件，不属于收益因子。
4. 下一版 B4 实验建议先做 contract-ranking-only，以验证这些因子最干净的应用场景。
5. 后续必须补充 theta、vega、gamma 的 forward attribution 标签，否则无法判断因子是否真的改善“赚 theta 和 vega”的目标。
