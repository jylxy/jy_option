# S1 B5 Full Shadow 因子扩展实施设计

文档日期：2026-04-29  
上游研究文档：`docs/s1_full_shadow_factor_expansion_design_20260429.md`  
当前直接基准：B1 liquidity/OI ranking stop25  
当前强对照：B2c product tilt、B4a/B4b/B4c  
本文定位：把 B5 full shadow 总纲拆成可开发、可验证、可分阶段运行的实施设计。

## 1. 当前 B4 观察

截至最近一次后台监控，B4 仍在运行，三条线都正常。

| 版本 | 最新进度 | 最新日期 | 共同截止日表现 |
| --- | --- | --- | --- |
| B4a | `780/1215` | 2024-09-24 | 共同截止 `2024-02-23` 时，相对 B1 `+1.12%`，相对 B2c `+0.18%`。 |
| B4b | `620/1215` | 2024-03-07 | 共同截止 `2024-02-23` 时，相对 B1 `+0.73%`，相对 B2c `-0.21%`。 |
| B4c | `610/1215` | 2024-02-24 | 共同截止 `2024-02-23` 时，相对 B1 `+1.00%`，相对 B2c `+0.06%`。 |

阶段性判断：

| 观察 | 对 B5 的启发 |
| --- | --- |
| B4a 目前最稳，说明“同品种同方向内选合约”有效。 | B5 第一优先级仍应保留 contract 层的排序/分层诊断。 |
| B4b 的 product-side 倾斜没有稳定增量。 | B5 不能继续用合约层权利金质量粗暴聚合成品种预算，应引入真正的 P/C、趋势、skew、冷静期和组合风险因子。 |
| B4c 虽然追上 B2c，但没有明显优于 B4a。 | vol-of-vol 与 breakeven 惩罚可能口径不准，B5 应先 shadow 检验，不直接交易化。 |
| B4 的 theta 增厚明显，但 gamma 损耗更大。 | B5 必须重点检测 delta ladder、MAE/tail 覆盖、gamma/theta 和 stop/cooldown。 |

## 2. B5 Full Shadow 的原则

B5 full shadow 不是新交易策略，而是研究样本生成器。

| 原则 | 说明 |
| --- | --- |
| 不改变交易 | 新增字段只进入 candidate universe、shadow outcome 和分析面板，不改变 B1/B4 的开仓、止损、预算和排序规则。 |
| 不放宽风险边界 | `abs(delta) < 0.10`、低价过滤、真实手续费、真实保证金和止损口径保持不变。 |
| 不用未来标签做信号 | 所有 `future_*` 标签只能用于研究分析，不能回流到 T 日因子。 |
| 因子分层使用 | 合约因子用于合约排序研究，P/C 因子用于方向选择研究，组合因子用于预算和风控研究。 |
| 先简后繁 | V1 先做稳健、可解释、数据可得字段；copula、分钟 jump、高维组合优化放 V1.1/V2。 |

## 3. 运行配置设计

建议新增一份 shadow-only 配置。

```text
config_s1_candidate_universe_b5_full_shadow_v1_2022_latest.json
```

建议 tag：

```text
s1_candidate_universe_b5_full_shadow_v1_2022_latest
```

建议核心参数：

```json
{
  "extends": "config_s1_candidate_universe_b1_shadow_full_b2b3_factors_2022_latest.json",
  "strategy_version": "s1_candidate_universe_b5_full_shadow_v1_2022_latest",
  "s1_candidate_universe_shadow_only_enabled": true,
  "s1_candidate_universe_max_candidates_per_side": 0,
  "s1_b5_shadow_factor_extension_enabled": true,
  "s1_b5_delta_ladder_enabled": true,
  "s1_b5_product_side_trend_skew_enabled": true,
  "s1_b5_cooldown_state_enabled": true,
  "s1_b5_portfolio_panel_enabled": true,
  "s1_b5_tail_dependence_enabled": true,
  "s1_b5_tail_dependence_mode": "empirical_v1",
  "s1_b5_min_history_days": 60,
  "s1_b5_tail_window_days": 120,
  "s1_b5_tail_quantile": 0.05
}
```

配置继承 B1 shadow full B2/B3 因子，而不是继承 B4 交易配置。B4 的分数字段可以作为诊断字段加入，但不能让 B4 的 product-side 预算倾斜影响 shadow 交易规则。

## 4. 总纲因子覆盖矩阵

本实施设计没有否定总纲中的其他因子。为了避免遗漏，下面把总纲因子全部映射到开发阶段。`V1 实现` 表示第一版直接落字段；`V1 预留` 表示保留字段接口或输出面板位置，但不作为第一版必算项；`V1.1/V2` 表示需要更多数据或更复杂模型；`暂缓` 表示与当前纯卖权主线不一致或过拟合风险过高。

### 4.1 合约层因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| B2/B4 基础权利金质量 | `premium_yield_margin`、`premium_to_iv10_loss`、`premium_to_stress_loss`、`gamma_rent_penalty`、`friction_ratio`、`b3_vomma_loss_ratio` | V1 实现 | 已有字段，必须保留为基准和控制项。 |
| Breakeven cushion | `breakeven_cushion_iv`、`breakeven_cushion_rv`、`breakeven_cushion_score` | V1 实现 | 已有字段，继续用于 stop/安全垫诊断。 |
| Theta/Vega | `theta_vega_efficiency`、`theta_per_vega`、`premium_per_vega` | V1 部分实现 | `theta_vega_efficiency` 已有，后两个可由现有字段派生。 |
| Theta/Gamma | `theta_per_gamma`、`gamma_theta_ratio` | V1 实现 | 用于解释 B4 gamma 损耗问题。 |
| Delta ladder | `delta_bucket`、`delta_to_cap`、`rank_in_delta_bucket` | V1 实现 | 直接回答 delta 小于 0.1 内部怎么铺。 |
| Ladder 暴露占比 | `premium_share_by_delta_bucket`、`stress_share_by_delta_bucket` | V1 实现 | 用于判断多梯队边际收益/风险。 |
| Expected move 覆盖 | `premium_to_expected_move_loss` | V1 实现 | 第一版用 RV forecast 或历史 RV 近似。 |
| Tail move 覆盖 | `premium_to_tail_move_loss` | V1 实现 | 第一版用日线尾部移动近似。 |
| MAE 覆盖 | `premium_to_mae20_loss` | V1 实现 | 第一版用日线 MAE；分钟路径放 V1.1。 |
| Tick 离散 | `tick_value_ratio`、`low_price_flag` | V1 实现 | 用于解释低价合约和假止损。 |
| Stale price | `last_trade_age`、`stale_price_ratio` | V1 预留 | 取决于行情表是否有最近成交时间。 |
| IV 质量 | `iv_solve_fail_rate`、`iv_outlier_rate`、`iv_outlier_flag` | V1 预留 | 若当前引擎已有 IV solve 标记可直接落；否则 V1.1。 |
| PCP 偏离 | `pcp_deviation` | V1 预留 | 已修 spot/PCP 主路径，偏离因子可放诊断字段。 |
| Wing 局部价值 | `wing_richness_local` | V1.1 | 需要完整相邻行权价 IV 插值，第一版先不强行做。 |

### 4.2 Product-side / P/C 因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| 简单动量 | `mom_5d`、`mom_20d`、`mom_60d` | V1 实现 | P/C 选择的基础。 |
| 波动调整趋势 | `trend_z_20d` | V1 实现 | 比普通收益更可比。 |
| 突破距离 | `breakout_distance_up_60d`、`breakout_distance_down_60d` | V1 实现 | 解释卖趋势方向的止损风险。 |
| 趋势持续 | `up_day_ratio_20d`、`down_day_ratio_20d` | V1 实现 | 判断单边趋势是否持续。 |
| 回撤缺失 | `days_since_pullback`、`days_since_ma_touch` | V1 预留 | 第一版可先用趋势/突破近似，回撤细节放 V1.1。 |
| Put skew | `put_skew_10d`、`put_skew_percentile` | V1 实现 | 用于 Put 侧预算研究。 |
| Call skew | `call_skew_10d`、`call_skew_percentile` | V1 实现 | 用于 Call 侧预算研究。 |
| Risk reversal | `risk_reversal_25d`、`risk_reversal_proxy` | V1 实现 | 若 25d 不稳定，先用低 delta proxy。 |
| Skew steepening | `put_skew_change_5d`、`call_skew_change_5d` | V1 部分实现 | 可复用已有 `contract_skew_change_for_vega`，再做 product-side 聚合。 |
| Smile curvature | `smile_curvature` | V1 实现 | 用 ATM、put wing、call wing IV 近似。 |
| P/C 需求压力 | `pc_volume_imbalance`、`pc_oi_imbalance`、`wing_demand_pressure` | V1.1 | 需要更完整成交量/持仓量分桶，第一版先不作为必选。 |

### 4.3 Product-date / Regime 因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| RV forecast | `rv_forecast_5d`、`rv_forecast_10d`、`rv_forecast_har` | V1 部分实现 | V1 用 RV5/RV10/RV20/EWMA，HAR 可放 V1.1。 |
| Forward variance carry | `variance_carry_forward` | V1 实现 | 替代旧版只看历史 RV 的 `variance_carry`。 |
| IV 动量 | `atm_iv_mom_5d`、`atm_iv_accel` | V1 实现 | 用 product ATM IV 历史。 |
| IV 均值回归 | `iv_reversion_score` | V1 实现 | 用 IV percentile 与 IV momentum 组合。 |
| Vol-of-vol 覆盖 | `vol_of_vol_risk_premium` | V1 实现 | 改造 B3 的简单 vov proxy。 |
| Range 扩张 | `range_expansion_5d`、`range_expansion_20d` | V1 实现 | 日线可得，服务升波预警。 |
| Gap 风险 | `gap_share_20d`、`gap_frequency_20d` | V1.1 | 需要可靠开盘价和夜盘口径。 |
| Jump share | `jump_share_20d` | V1.1 | 需要分钟数据，第一版先预留。 |
| Intraday trendiness | `intraday_trend_score` | V1.1 | 需要分钟或日内 OHLC 更稳定口径。 |
| 资金锁定 | `capital_lockup_days`、`premium_per_capital_day` | V1 实现 | 可由 DTE/保证金/持有天数近似。 |

### 4.4 冷静期 / 止损状态因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| 同品种止损新近度 | `days_since_product_stop`、`product_stop_count_20d` | V1 实现 | 来自真实已发生止损。 |
| 同方向止损新近度 | `days_since_product_side_stop`、`product_side_stop_count_20d` | V1 实现 | Put/Call 分开。 |
| 止损严重度 | `product_stop_loss_nav_20d` | V1 实现 | 衡量冷静期强度。 |
| 止损越界 | `product_stop_overshoot_20d` | V1 实现 | 用真实执行价与止损阈值比较。 |
| 二次止损倾向 | `repeat_stop_rate_prior` | V1.1 | 需要较长历史止损样本，第一版可预留。 |
| 止损后 IV/RV/skew | `post_stop_iv_change`、`post_stop_rv_change`、`post_stop_skew_change` | V1 实现 | 判断是否允许释放冷静期。 |
| 冷静期分数 | `cooldown_penalty_score`、`cooldown_release_score` | V1 实现 | 只做 shadow 诊断，不交易化。 |

### 4.5 组合层因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| 原始品种数 | `active_product_count`、`active_product_side_count`、`active_sector_count` | V1 实现 | 基础组合结构。 |
| 有效品种数 | `effective_product_count_margin`、`effective_product_count_stress`、`effective_product_count_vega`、`effective_product_count_gamma` | V1 实现 | 判断真分散。 |
| 集中度 | `top1_product_stress_share`、`top5_product_stress_share`、`hhi_product_stress`、`hhi_sector_stress` | V1 实现 | 判断单品种/板块集中。 |
| 到期集中 | `expiry_cluster_weight`、`same_expiry_stress_share` | V1 实现 | 只卖次月也需要看到期聚集。 |
| 保证金 | `margin_usage_rate`、`stressed_margin_usage_rate`、`margin_shock_ratio` | V1 实现 | 当前和压力保证金。 |
| 保证金变化 | `margin_usage_change_5d`、`margin_usage_change_20d` | V1 实现 | 判断仓位是否快速膨胀。 |
| 回撤耦合 | `margin_drawdown_coupling` | V1.1 | 需要与 NAV 路径联合构造。 |
| Stop cluster | `stop_cluster_score`、`sector_stop_count_20d` | V1 实现 | 来自真实止损日志。 |
| Sector/directional crowding | `sector_stress_exposure`、`portfolio_put_stress`、`portfolio_call_stress` | V1 实现 | 板块和方向拥挤。 |

### 4.6 尾部相依与 copula 因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| 经验下尾相依 | `empirical_lower_tail_dependence_95` | V1 实现 | 先做 pairwise/sector 聚合。 |
| 经验上尾相依 | `empirical_upper_tail_dependence_95` | V1 实现 | 区分 Put/Call 不利方向。 |
| Tail Kendall | `tail_kendall_tau` | V1 实现 | 稳健秩相关。 |
| Tail beta | `lower_tail_beta`、`upper_tail_beta` | V1 实现 | 单品种对组合尾部的敏感度。 |
| Tail jump | `tail_dependence_jump` | V1 实现 | 当前尾部相依相对历史基准变化。 |
| t-copula | `t_copula_df`、`t_copula_rho` | V1.1 | 第一版可预留，样本够再算。 |
| Clayton/Gumbel | `clayton_theta_lower`、`gumbel_theta_upper` | V2 | 参数估计更不稳，暂缓。 |
| CoVaR/MES | `product_delta_covar`、`product_marginal_expected_shortfall`、`tail_es_contribution` | V1.1/V2 | 先定义标签和面板，后续实现。 |
| Tail network | `tail_network_centrality` | V2 | 需要网络构造和稳定性检验。 |

### 4.7 Ratio / 结构扩展因子

| 因子族 | 字段 | 阶段 | 说明 |
| --- | --- | --- | --- |
| Sweet width | `sweet_width`、`sweet_width_pct` | 暂缓 | 属于 ratio/断翅蝶，不进入纯卖权 B5 V1。 |
| Ratio breakeven | `ratio_breakeven_distance` | 暂缓 | 需要独立结构 payoff 引擎。 |
| Tail slope | `tail_slope` | 暂缓 | 纯卖权与 ratio 定义不同。 |
| Wing conversion cost | `wing_conversion_cost_now`、`wing_conversion_cost_if_stop` | 暂缓 | 后续 ratio 转断翅研究。 |
| Ratio fragility | `ratio_fragility_score` | 暂缓 | 不污染当前 S1 纯卖权主线。 |

## 5. 开发切片

### 5.1 V1a：合约层与 P/C 侧基础字段

V1a 优先级最高，主要扩展候选合约表。

| 模块 | 字段 | 数据源 | 开发位置 |
| --- | --- | --- | --- |
| Delta ladder | `delta_bucket`、`delta_to_cap`、`available_ladder_count`、`rank_in_delta_bucket` | 候选合约 delta、product、side、expiry | `strategy_rules.py` 或 `toolkit_minute_engine.py::_append_s1_candidate_universe` 前 |
| Ladder 权利金/压力 | `premium_share_by_delta_bucket`、`stress_share_by_delta_bucket` | 候选合约 premium、stress loss | 同 product-side-expiry groupby |
| 合约 MAE/tail 覆盖 | `premium_to_mae20_loss`、`premium_to_tail_move_loss` | 标的历史 high/low、spot、mult、delta/gamma 近似 | 先用日线近似，分钟版放 V1.1 |
| P/C 趋势 | `mom_5d`、`mom_20d`、`trend_z_20d`、`breakout_distance_up_60d`、`breakout_distance_down_60d` | 标的历史价格 | engine 内 product history cache |
| Skew 基础 | `put_skew_10d`、`call_skew_10d`、`risk_reversal_proxy`、`skew_change_5d` | 当日期权链和历史 IV 状态 | 优先使用 ATM IV 与低 delta IV 近似 |

V1a 输出仍然是：

```text
output/s1_candidate_universe_<tag>.csv
output/s1_candidate_shadow_outcomes_<tag>.csv
```

### 5.2 V1b：冷静期与止损状态字段

V1b 用于回答“止损后同品种是否应该冷却，以及何时释放”。

| 模块 | 字段 | 数据源 | 注意事项 |
| --- | --- | --- | --- |
| 同品种冷却 | `days_since_product_stop`、`product_stop_count_20d` | 已发生订单/平仓日志 | 只能用 T 日以前真实止损。 |
| 同方向冷却 | `days_since_product_side_stop`、`product_side_stop_count_20d` | 已发生订单/平仓日志 | Put/Call 分开。 |
| 止损严重度 | `product_stop_loss_nav_20d`、`product_stop_overshoot_20d` | 止损成交价、止损阈值、NAV | 用真实执行价，不用理论阈值。 |
| 止损后状态 | `post_stop_iv_change`、`post_stop_rv_change`、`post_stop_skew_change` | 最近一次止损日至 T 日的 IV/RV/skew 变化 | 用于冷静期释放判断。 |
| 冷静期分 | `cooldown_penalty_score`、`cooldown_release_score` | 上述字段组合 | 只做 shadow 分析，不交易化。 |

需要在 engine 中维护一个轻量状态缓存：

```text
product_stop_state[product]
product_side_stop_state[(product, option_type)]
```

状态只从已经发生的真实持仓平仓事件更新，禁止使用 shadow 未来结果。

### 5.3 V1c：组合层面板

V1c 不应该写进每个合约行里重复膨胀，而应输出独立日度面板。

建议新增输出：

```text
output/s1_b5_portfolio_panel_<tag>.csv
output/s1_b5_product_panel_<tag>.csv
output/s1_b5_product_side_panel_<tag>.csv
output/s1_b5_delta_ladder_panel_<tag>.csv
```

| 面板 | 样本单位 | 字段 |
| --- | --- | --- |
| `portfolio_panel` | `signal_date` | `active_product_count`、`effective_product_count_stress`、`top5_product_stress_share`、`hhi_sector_stress`、`margin_usage_rate`、`stressed_margin_usage_rate` |
| `product_panel` | `signal_date + product` | `product_candidate_count`、`product_stress_share`、`product_margin_share`、`product_tail_beta`、`product_stop_state` |
| `product_side_panel` | `signal_date + product + option_type` | `side_candidate_count`、`side_premium_sum`、`side_stress_sum`、`side_trend_score`、`side_skew_score`、`cooldown_release_score` |
| `delta_ladder_panel` | `signal_date + product + option_type + expiry + delta_bucket` | `bucket_candidate_count`、`bucket_premium_sum`、`bucket_stress_sum`、`bucket_avg_score` |

### 5.4 V1d：经验尾部相依

V1d 第一版只做经验尾部相依，不做高维 vine copula。

| 字段 | 计算 |
| --- | --- |
| `empirical_lower_tail_dependence_95` | `P(r_i < q_i(5%) | r_j < q_j(5%))` rolling 估计 |
| `empirical_upper_tail_dependence_95` | `P(r_i > q_i(95%) | r_j > q_j(95%))` rolling 估计 |
| `tail_kendall_tau` | 尾部样本的 Kendall 相关 |
| `lower_tail_beta`、`upper_tail_beta` | 尾部样本中单品种对组合/板块尾部收益的 beta |
| `tail_dependence_jump` | 当前尾部相依相对滚动中位数的变化 |

第一版只输出到 `product_panel` 和 `portfolio_panel`，不参与合约排序。

## 6. Shadow 标签扩展

需要在现有 shadow outcome 基础上增加派生标签。若现有逐合约 shadow 已能生成未来 PnL 和 stop，则优先写分析脚本派生，不一定全部在 engine 内实时写。

| 标签 | 层级 | 来源 |
| --- | --- | --- |
| `future_pnl_by_delta_bucket` | delta bucket | 合约 shadow outcome 聚合 |
| `future_stop_rate_by_delta_bucket` | delta bucket | 合约 shadow outcome 聚合 |
| `marginal_ladder_pnl_k` | product-side-expiry | 对同组候选按质量分取前 K 个模拟聚合 |
| `marginal_ladder_theta_vega_k` | product-side-expiry | 对前 K 个候选的 theta/vega 聚合 |
| `future_repeat_stop_5d/10d` | product/product-side | 止损后候选在未来窗口是否再止损 |
| `future_cooldown_value` | product-side | 避免二次亏损 - 错过收益 |
| `future_tail_cluster_loss` | portfolio | 多品种同时损失日的组合亏损 |
| `future_margin_squeeze` | portfolio | 保证金上升且 NAV 回撤的事件 |

建议将标签计算放在单独分析脚本，而不是让回测引擎过度膨胀。

```text
scripts/analyze_s1_b5_full_shadow.py
```

## 7. 代码触点

| 文件 | 需要新增内容 |
| --- | --- |
| `src/strategy_rules.py` | 纯函数：delta bucket、MAE/tail coverage、product-side trend/skew factor 的计算函数。 |
| `src/toolkit_minute_engine.py` | 在 candidate universe append 前补 B5 字段；维护 stop state；输出 product/product-side/portfolio/delta-ladder 面板。 |
| `config_s1_candidate_universe_b5_full_shadow_v1_2022_latest.json` | 新增 shadow-only 配置。 |
| `scripts/analyze_s1_b5_full_shadow.py` | 从候选和 shadow outcome 派生分层、IC、delta ladder、cooldown、tail panel 报告。 |
| `docs/s1_full_shadow_factor_expansion_design_20260429.md` | 总纲，保持不再频繁改动。 |
| `docs/s1_b5_full_shadow_factor_extension_implementation_design.md` | 本实施设计，记录开发顺序和字段边界。 |

## 8. 第一版不做

| 暂不做 | 原因 |
| --- | --- |
| 改交易规则 | B5 先是 full shadow，不是策略上线。 |
| 放宽 `abs(delta)<0.10` | 用户已明确这是硬约束。 |
| 高维 vine/factor copula | 实现复杂，样本不齐，过拟合风险高。 |
| 直接上机器学习综合模型 | 当前先做单因子、分层、正交化和角色归位。 |
| 用 shadow 未来止损更新冷静期状态 | 未来函数风险，严格禁止。 |
| 用分钟 jump 作为 V1 必选项 | 可做 V1.1，先用日线 MAE/range/gap 近似。 |

## 9. 验收标准

开发完成后，至少要能输出以下检查结果：

| 检查 | 输出 |
| --- | --- |
| 候选字段完整性 | 每个 B5 字段非空率、分布、极值。 |
| Delta 桶覆盖 | D1-D5 候选数、权利金、stress、stop、PnL 分层。 |
| P/C 因子 | trend × skew × RV 的 P/C outcome 交互表。 |
| 冷静期 | cooldown score 分层下的 repeat stop、missed theta、cooldown value。 |
| 组合有效分散 | effective product count、top5 stress、HHI 与未来回撤关系。 |
| 尾部相依 | empirical tail dependence 与 future tail cluster loss 的关系。 |
| 共线性 | B2/B3/B4/B5 同层级字段相关性矩阵。 |
| 正交化 | 控制 premium、margin、DTE、delta、stress 后的 residual IC。 |

如果这些检查不能解释 B5 因子是否有增量，就不进入交易实验。

## 10. 实施顺序

建议按以下顺序开始：

| 顺序 | 任务 | 原因 |
| --- | --- | --- |
| 1 | 新增配置文件和 B5 字段开关 | 保持当前 B1/B4/B2c 不受影响。 |
| 2 | 实现 delta ladder 字段 | 数据最稳定，直接回答当前每侧 K 和 delta 桶问题。 |
| 3 | 实现 product-side trend/skew 字段 | 直接服务 P/C 选择。 |
| 4 | 实现 stop cooldown state | 需要小心未来函数，单独做更安全。 |
| 5 | 实现 portfolio/product/product-side 面板 | 为组合风控和预算研究准备。 |
| 6 | 实现经验尾部相依 | 作为组合风控字段，不影响合约候选生成。 |
| 7 | 写 B5 shadow 分析脚本 | 输出分层、IC、相关性、正交化和图表。 |

一句话：B5 Full Shadow V1 的开发目标不是“更聪明地交易”，而是“更诚实地知道我们为什么赚钱、为什么止损、为什么回撤，以及哪个因子真的应该进入下一版策略”。

## 11. V1 落地检查

本节记录 2026-04-29 第一版代码落地后的检查结果，避免后续忘记 V1 到底覆盖了哪些东西。

### 11.1 已落地输出

| 输出 | 文件名模板 | 状态 | 说明 |
| --- | --- | --- | --- |
| 候选合约全量表 | `s1_candidate_universe_<tag>.csv` | 已落地 | 在原 B1/B2/B3 shadow 字段上新增 `b5_*` 候选字段。 |
| Shadow outcome | `s1_candidate_outcomes_<tag>.csv` | 沿用 | 仍按原 shadow 标签输出，不把未来标签回流为 T 日信号。 |
| 品种面板 | `s1_b5_product_panel_<tag>.csv` | 已落地 | 输出品种候选数、权利金、stress、margin、cash Greeks、尾部相依 proxy。 |
| 品种方向面板 | `s1_b5_product_side_panel_<tag>.csv` | 已落地 | 输出 product × Put/Call 的权利金、stress、trend、IV 动量、skew proxy。 |
| Delta 梯队面板 | `s1_b5_delta_ladder_panel_<tag>.csv` | 已落地 | 输出 product × side × expiry × delta bucket 的权利金、stress、theta/gamma。 |
| 组合候选面板 | `s1_b5_portfolio_panel_<tag>.csv` | 已落地 | 输出有效品种数、HHI、top stress share、P/C stress。 |

### 11.2 已落地因子族

| 因子族 | 已落地字段示例 | 用途 |
| --- | --- | --- |
| Delta ladder | `b5_delta_bucket`、`b5_delta_ratio_to_cap`、`b5_rank_in_delta_bucket` | 检验 delta < 0.1 内部到底应铺几档、靠近 0.1 还是更深虚。 |
| 梯队风险/收益占比 | `b5_premium_share_delta_bucket`、`b5_stress_share_delta_bucket` | 检验多执行价铺开是否只是增加 gamma/stress。 |
| Theta/Gamma/Vega 效率 | `b5_theta_per_gamma`、`b5_gamma_theta_ratio`、`b5_theta_per_vega`、`b5_premium_per_vega` | 检验保留 theta 时是否能减少 vega/gamma 质量问题。 |
| Expected/tail/MAE 覆盖 | `b5_premium_to_expected_move_loss`、`b5_premium_to_mae20_loss`、`b5_premium_to_tail_move_loss` | 检验权利金是否覆盖历史不利移动和尾部移动。 |
| 趋势/动量 | `b5_mom_5d`、`b5_mom_20d`、`b5_trend_z_20d`、`b5_breakout_distance_*` | 用于后续 P/C 偏移和卖趋势方向风险检测。 |
| IV 状态 | `b5_atm_iv_mom_5d`、`b5_atm_iv_accel`、`b5_iv_zscore_60d`、`b5_iv_reversion_score` | 用于区分高 IV、升波、降波和可释放风险预算状态。 |
| 冷静期状态 | `b5_days_since_product_stop`、`b5_product_side_stop_count_20d`、`b5_cooldown_penalty_score`、`b5_cooldown_release_score` | 检验止损后是否应同品种/同方向降级，以及何时释放。 |
| 成本/低价离散 | `b5_tick_value_ratio`、`b5_low_price_flag` | 检验低价合约 tick 离散和假止损风险。 |
| 资金效率 | `b5_capital_lockup_days`、`b5_premium_per_capital_day` | 检验次月内部不同 DTE 的资金占用效率。 |
| 组合集中 | `effective_product_count_*`、`top5_product_stress_share`、`hhi_product_stress`、`hhi_sector_stress` | 检验候选池天然集中度和组合脆弱性。 |
| 尾部相依 | `b5_empirical_lower_tail_dependence_95`、`b5_empirical_upper_tail_dependence_95`、`b5_lower_tail_beta`、`b5_upper_tail_beta` | 第一版用经验尾部相依 proxy，不做复杂 copula 参数估计。 |

### 11.3 V1 暂未落地或仅预留

| 因子族 | 当前处理 | 原因 |
| --- | --- | --- |
| 分钟 jump share、intraday trendiness、gap dominance | V1 暂缓 | 需要更细的分钟/开盘口径，先用日线 MAE、tail move、range expansion proxy。 |
| t-copula、Clayton/Gumbel copula、tail network | V1 暂缓 | 高维估计不稳，先用经验尾部相依 proxy 做方向验证。 |
| PCP deviation、IV solve fail rate、stale price age | V1 预留 | 需要把数据质量标记更系统地从 IV/行情清洗链路传到候选表。 |
| ratio/断翅蝶相关字段 | V1 暂缓 | 当前 B5 仍服务纯卖权 S1，不污染 ratio 结构研究。 |

### 11.4 Smoke test 结果

服务器 smoke test：

```text
config: config_s1_candidate_universe_b5_full_shadow_v1_2022_latest.json
tag: b5_shadow_v1_smoke
date: 2025-03-03 ~ 2025-03-14
products: CU, AU, M
candidate rows: 294
product panel rows: 32
product-side panel rows: 58
delta-ladder panel rows: 189
portfolio panel rows: 11
```

检查结论：即时字段如 delta bucket、theta/gamma、theta/vega、冷静期分数、tick ratio 已正常填充；依赖 20/60 日历史的趋势、tail move、IV zscore 字段在短样本早期非空率较低，属于预期现象，长周期 full shadow 中会逐步填满。
