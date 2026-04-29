# S1 B2/B3 Full Shadow Candidate Universe 因子清单

文档日期：2026-04-28

## 1. 实验目的

本实验用于在 B2/B3 倾斜之前的 B1 可交易候选池上，同时记录 B2 权利金质量因子和 B3 clean vega 因子，并为每个候选合约生成统一的 1 手 shadow outcome 标签。

核心原则：

- 候选池仍然来自 B1 可交易口径，B2/B3 不参与筛选、不参与排序、不参与预算倾斜。
- 因子只使用 T 日及以前可知信息，用于解释 T+1 之后 shadow 持有结果。
- 标签是未来结果，只能用于研究分析，不能回流到开仓因子。
- 本次重跑必须启用 `s1_track_contract_iv_trend=true`，否则 B3 的 forward variance、vol-of-vol trend、skew steepening 相关字段会缺失。

## 2. Shadow 标签

主要标签：

- `future_retained_ratio`
- `future_net_pnl_per_premium`
- `future_stop_avoidance`
- `future_stop_loss_avoidance`

辅助标签：

- `future_exit_reason`
- `future_holding_days`
- `future_net_pnl`
- `future_gross_premium_cash`
- `future_stop_loss_cash`

## 3. B2 因子

权利金厚度与资金效率：

- `gross_premium_cash_1lot`
- `net_premium_cash_1lot`
- `premium_yield_margin`
- `premium_yield_notional`
- `premium_margin`
- `premium_stress`
- `theta_stress`

IV/RV carry：

- `rv_ref`
- `iv_rv_spread_candidate`
- `iv_rv_ratio_candidate`
- `variance_carry`
- `iv_rv_carry_score`

盈亏平衡保护垫：

- `breakeven_price`
- `breakeven_cushion_abs`
- `breakeven_cushion_iv`
- `breakeven_cushion_rv`
- `breakeven_cushion_score`

IV shock 覆盖率：

- `iv_shock_loss_5_cash`
- `iv_shock_loss_10_cash`
- `premium_to_iv5_loss`
- `premium_to_iv10_loss`
- `premium_to_iv_shock_score`

联合压力覆盖率：

- `stress_loss`
- `premium_to_stress_loss`
- `premium_to_stress_loss_score`

Theta/Vega 效率：

- `cash_theta`
- `cash_vega`
- `theta_vega_efficiency`
- `theta_vega_efficiency_score`

Gamma 租金惩罚：

- `cash_gamma`
- `gamma_rent_cash`
- `gamma_rent_penalty`

交易摩擦：

- `open_fee_per_contract`
- `close_fee_per_contract`
- `roundtrip_fee_per_contract`
- `fee_ratio`
- `slippage_ratio`
- `friction_ratio`
- `cost_liquidity_score`

B2 综合质量分：

- `premium_quality_score`
- `premium_quality_rank_in_side`

## 4. B3 因子

Forward variance / term structure：

- `entry_iv_trend`
- `contract_iv_change_1d`
- `contract_iv_change_3d`
- `contract_iv_change_5d`
- `b3_near_atm_iv`
- `b3_next_atm_iv`
- `b3_far_atm_iv`
- `b3_term_structure_pressure`
- `b3_forward_variance_pressure`

Vol-of-vol：

- `b3_vol_of_vol_proxy`
- `b3_vov_trend`

IV shock coverage：

- `b3_iv_shock_coverage`

Spot + IV joint stress coverage：

- `b3_joint_stress_coverage`

Vomma / volga penalty：

- `b3_vomma_cash`
- `b3_vomma_loss_ratio`

Skew steepening penalty：

- `contract_iv_skew_to_atm`
- `contract_skew_change_for_vega`
- `b3_skew_steepening`

## 5. 分析层级

合约层：

- 样本单位：`signal_date + product + option_type + expiry + strike + code`
- 回答问题：同一日、同一品种、同一侧、同一次月中，哪张合约更值得卖。

品种-方向层：

- 样本单位：`signal_date + product + option_type`
- 回答问题：当天哪个品种、哪一侧应该获得更多风险预算。

## 6. 本次运行配置

配置文件：

```text
config_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest.json
```

核心参数：

```text
s1_candidate_universe_dump_enabled = true
s1_candidate_universe_shadow_enabled = true
s1_candidate_universe_shadow_only_enabled = true
s1_candidate_universe_max_candidates_per_side = 0
s1_b2_product_tilt_enabled = false
s1_b3_clean_vega_tilt_enabled = false
s1_forward_vega_filter_enabled = false
s1_track_contract_iv_trend = true
```

输出标签：

```text
s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest
```

