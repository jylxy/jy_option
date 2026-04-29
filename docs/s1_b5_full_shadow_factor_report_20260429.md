# S1 B5 Full Shadow 因子审计报告

报告日期：2026-04-29  
回测标签：`s1_b5_full_shadow_v1_2022_latest`  
样本区间：2022-01-04 至 2026-03-31  
报告用途：用于评估 B5 full shadow 因子宇宙是否能够支持下一阶段 B6/B7 的合约排序、品种预算倾斜、P/C 侧选择、冷静期规则与组合尾部风控。

## 1. 执行摘要

B5 是一版“影子候选池”实验，不进行真实开仓，因此 NAV、订单和持仓为 0 是正常现象。它的核心价值不是看净值，而是把全市场、全品种、次月、delta 小于 0.1 的候选合约全部打标签，并观察这些合约在未来持有路径中的收益、止损、留存率、尾部亏损、Greek 损耗和交易代价。

这次 B5 的最重要结论有四个。

第一，候选池的 Premium Pool 足够厚。全样本候选超过 51.8 万条，日均候选权利金池约 9.34 万，90 分位约 15.82 万，极值超过 37 万。这说明 S1 的收益上限不是“市场没有权利金”，而是我们是否能把资金部署到更高质量的权利金上。

第二，全量无脑卖是严重亏损的。全候选 1 手影子卖出合计 PnL 约 -996 万，候选止损率 33.94%，平均留存率为 -231.78%。这意味着候选池中存在大量低质量腿，尤其是低价、tick 占比高、深虚但权利金过薄的合约，它们会持续侵蚀 Retention Rate。

第三，“越深虚越安全”的直觉在数据里不成立。0.00-0.02 delta 桶的止损率达到 46.95%，低价合约占比 55.61%，净 PnL 为 -566 万；而 0.08-0.10 delta 桶净 PnL反而为 +88.4 万，止损率约 28.36%。因此 delta 小于 0.1 可以继续作为硬约束，但梯队应优先靠近 0.06-0.10，并由权利金质量、vega/gamma 风险、尾部覆盖和流动性进一步排序。

第四，B5 因子不是应该“一股脑进交易”，而是要分角色使用。权利金质量类因子适合做合约排序；theta/vega、theta/gamma、tail move coverage 适合做止损和尾部风险控制；趋势、breakout、skew 更适合做 P/C 侧预算偏移；tail correlation、到期集中和 stop cluster 更适合进入组合层风险预算，而不应使用普通合约 IC 粗暴评价。

## 2. 研究范式：围绕卖权收益公式归因

后续 S1 研究统一围绕以下公式拆解：

```text
S1 收益
= Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

其中：

Premium Pool 代表市场可收取的总权利金池。它由品种、期限、delta 梯队、P/C 侧、流动性和可交易候选数量共同决定。

Deployment Ratio 代表我们把多少风险预算部署到候选池里。它不是简单仓位，而是带有保证金、stress budget、品种约束、板块约束、P/C 侧预算和尾部相关性约束的有效部署比例。

Retention Rate 代表收进来的权利金最终能留下多少。它是卖权策略最核心的质量指标，比单纯开仓权利金更重要。

Tail / Stop Loss 代表被 2.5x 止损、跳价、IV spike、gamma 扩张、尾部相关性聚集吞掉的部分。

Cost / Slippage 代表手续费、买卖价差、低价 tick、假价格、止损执行冲击和低流动性造成的损耗。

B5 的意义，是把每个候选合约、每个品种方向、每个品种和组合状态都映射到这个公式上，判断因子到底改善的是哪一项，而不是只看某个单一 IC。

## 3. 样本概览

| 指标 | 数值 |
| --- | ---: |
| 候选合约数 | 518,048 |
| 信号日数量 | 1,215 |
| 覆盖品种数 | 65 |
| 覆盖品种方向数 | 127 |
| 候选止损率 | 33.94% |
| 到期实值率 | 0.12% |
| 低价候选占比 | 19.73% |
| 候选平均留存率 | -231.78% |
| 全候选影子 PnL 合计 | -9,962,410 |

这张表说明一个关键事实：单个合约最终到期实值的比例并不高，但止损率很高。这意味着亏损主要不是来自“最终被行权”，而是来自持有过程中权利金膨胀、IV 扩张、gamma 路径、低价跳变和止损执行。对 S1 来说，未来的研究重点不应只盯到期安全，而要盯住“持有路径是否能活到到期”。

## 4. Premium Pool 与组合压力

![组合权利金、压力与保证金](output/analysis_s1_b5_full_shadow_factor_report_20260429/01_portfolio_premium_stress_margin.png)

图 1 展示了候选池每日权利金、stress loss 与保证金占用的时间序列。可以看到，候选权利金池并不稀缺，尤其在波动抬升后，市场会给出明显更厚的权利金。但这类时期同时伴随 stress loss 扩张，因此不能简单把“权利金变厚”理解为“机会变好”。卖权最赚钱的阶段通常是高波后确认降波，而不是升波早期盲目加仓。

从公式角度看，这张图对应 Premium Pool 与 Tail / Stop Loss 的联动。B6/B7 不应该只最大化 Premium Pool，而应该寻找“权利金变厚但 stress loss 没有同比例变坏”的区域。

![有效品种数与集中度](output/analysis_s1_b5_full_shadow_factor_report_20260429/02_portfolio_effective_count_concentration.png)

图 2 显示表面上活跃品种较多，但按 stress 贡献计算的有效分散度并不高。B5 样本中，候选活跃品种数均值约 34.8，但 stress-effective product count 均值约 13.1，Top5 品种 stress 占比均值约 56.06%，90 分位约 89.19%。这说明组合真实风险远比“品种数量”看起来集中。

这对后续组合优化很重要：S1 不应该只看每个品种等权，也不应该只看名义保证金。更合理的是在组合层使用 tail correlation、stress contribution、same-expiry gamma 与 stop cluster 管理风险。后续可以设计 Tail-HRP 或基于尾部相依性的风险预算框架。

## 5. P/C 侧拆解

| option_type | rows | premium_sum | net_pnl_sum | mean_retained_ratio | stop_rate | low_price_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 214,579 | 47,413,730 | 528,589 | -249.18% | 34.66% | 17.78% |
| P | 303,469 | 72,584,715 | -10,490,999 | -219.47% | 33.43% | 21.10% |

![P/C 侧权利金、止损率与留存率](output/analysis_s1_b5_full_shadow_factor_report_20260429/04_side_premium_stop_retention.png)

Call 侧在全候选影子中表现更好，Put 侧亏损更大。但这不能直接推导出“长期只卖 Call”。原因是 B5 覆盖了 2022-2026 的多种行情，P/C 收益高度依赖趋势、skew、RV 抬升、商品自身供需和宏观环境。Put 侧亏损较大，更可能说明在部分下行或波动扩张环境中，我们没有充分控制 Put 侧尾部，而不是 Put 本身不能卖。

后续使用方式应是：趋势向上或下跌风险缓和时增加 Put 预算、压低 Call 预算；趋势向下或下方突破靠近时降低 Put 预算；震荡环境允许双卖；强趋势环境减少逆趋势一侧。P/C 因子应进入 side selection 和 side budget tilt，而不应该与合约层排序混在一起。

## 6. Delta 梯队：深虚不是天然安全

| b5_delta_bucket | rows | premium_sum | net_pnl_sum | mean_retained_ratio | stop_rate | low_price_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.00_0.02 | 143,460 | 6,957,536 | -5,661,610 | -648.96% | 46.95% | 55.61% |
| 0.02_0.04 | 119,965 | 17,096,306 | -3,596,207 | -104.92% | 30.16% | 10.47% |
| 0.04_0.06 | 99,092 | 25,098,058 | -1,597,747 | -70.61% | 28.49% | 5.80% |
| 0.06_0.08 | 83,580 | 32,247,345 | 9,059 | -52.26% | 28.25% | 3.36% |
| 0.08_0.10 | 71,951 | 38,599,202 | 884,096 | -41.32% | 28.36% | 1.80% |

![Delta 桶权利金与止损率](output/analysis_s1_b5_full_shadow_factor_report_20260429/03_delta_bucket_premium_stop.png)

这张图是本次 B5 最有价值的图之一。它显示 0.00-0.02 delta 深虚合约虽然方向安全感最强，但实际交易质量最差：权利金过薄、低价占比过高、tick 占比过高、止损倍数容易被微小跳价触发。反过来，0.08-0.10 桶虽然更靠近平值，但权利金更厚、流动性更好、单位成本更低，影子 PnL 反而最好。

这并不意味着要突破 delta 小于 0.1 的硬约束，而是说明 delta 梯队要从“越小越好”改成“0.06-0.10 优先，0.04-0.06 辅助，0.00-0.02 需要极强流动性和价格质量才允许”。这会直接改善 Premium Pool、Retention Rate 和 Cost / Slippage。

## 7. DTE 与次月内部结构

| dte_bucket | rows | premium_sum | net_pnl_sum | mean_retained_ratio | stop_rate | low_price_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| <=30 | 17,360 | 4,717,885 | -3,683,972 | -387.83% | 29.95% | 31.73% |
| 31-45 | 201,620 | 46,992,297 | 1,428,944 | -246.28% | 30.14% | 23.60% |
| 46-60 | 178,106 | 48,459,500 | -5,739,526 | -278.20% | 37.12% | 18.21% |
| 61-90 | 77,270 | 12,329,260 | -605,845 | -140.68% | 35.13% | 15.05% |
| >90 | 43,692 | 7,499,504 | -1,362,012 | -74.91% | 37.98% | 11.53% |

![DTE 桶权利金、止损率与留存率](output/analysis_s1_b5_full_shadow_factor_report_20260429/05_dte_bucket_premium_stop_retention.png)

即便都叫“次月”，不同商品和不同到期结构的 DTE 差异仍然很大。31-45 DTE 是本次样本中最好的区间，全候选影子 PnL 为正；46-60 DTE 权利金池更大，但止损率和亏损明显变差；<=30 DTE 则受短 gamma 和低价合约影响较大。

因此，B6 不应改变“只做次月”的大方向，但应在次月内部引入 DTE sweet spot 和 capital lock-up days。具体做法是：优先 31-45 DTE；对 46-60 DTE 要求更高的 premium_to_stress_loss 和 theta_per_vega；对 <=30 DTE 则需要更强的价格质量、流动性与 gamma 限制。

## 8. 低价、tick 与交易代价

| price_bin | rows | premium_sum | net_pnl_sum | mean_retained_ratio | stop_rate | low_price_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| <=5 | 220,531 | 42,882,076 | -5,253,805 | -440.05% | 40.59% | 46.34% |
| 5-10 | 94,231 | 17,920,900 | -2,272,907 | -103.25% | 31.33% | 0.00% |
| 10-30 | 125,524 | 26,329,296 | -3,566,078 | -88.19% | 29.51% | 0.00% |
| 30-50 | 35,804 | 10,293,004 | 991,545 | -35.75% | 25.04% | 0.00% |
| 50-100 | 20,454 | 7,880,172 | 786,722 | -19.14% | 24.40% | 0.00% |
| 100-300 | 14,186 | 8,692,423 | -300,622 | -23.34% | 26.48% | 0.00% |
| >300 | 7,318 | 6,000,574 | -347,266 | -26.48% | 27.39% | 0.00% |

![低价合约失真诊断](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/00_low_price_distortion.png)

低价合约是 B5 中非常明确的负贡献来源。价格小于等于 5 的候选数量最多，权利金池看起来也不小，但净 PnL 为 -525 万，止损率 40.59%，平均留存率 -440.05%。这类合约往往看似深虚、名义风险小，但一个 tick 的价格变化就可能带来很大的百分比变化，2.5x 止损也更容易被噪音触发。

这部分应该进入硬过滤，而不是只做排序惩罚。建议 B6 保留以下规则：低价合约过滤、tick_value_ratio 上限、权利金必须覆盖手续费和最小滑点、异常跳价需要二次确认。这样会牺牲一部分 Premium Pool，但能显著改善 Retention Rate 和 Cost / Slippage。

## 9. 原始因子层结果

![合约层因子 IC 热力图](output/candidate_layers_s1_b5_full_shadow_v1_2022_latest/contract/05_factor_ic_heatmap.png)

![合约层留存率分层热力图](output/candidate_layers_s1_b5_full_shadow_v1_2022_latest/contract/03_layer_retained_heatmap.png)

原始分层结果显示，权利金质量、单位风险覆盖、单位 vega/gamma 的 theta 效率、流动性成本和低价过滤都有明显信号。但原始层结果不能直接用于交易，因为同一天、同品种、同方向会有大量相邻行权价候选，它们高度相关；如果不做修正，IC 会被同一条波动曲线上的重复候选放大。

因此，本报告更重视修正后的结果：同日同品种方向去重、低价样本敏感性、非重叠日期检验、控制保证金和 stress 后的残差 IC，以及相关性矩阵。

## 10. 修正后因子审计

![修正后 IC 衰减检验](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/03_ic_decay_by_sample_and_label.png)

这张图检查因子在不同样本口径和不同标签下是否稳定。一个可靠的 S1 因子不应只在全样本中好看，还应该在剔除低价、剔除费用过高、使用非重叠日期、控制 margin/stress 后仍有方向一致的效果。B5 的结果显示，权利金质量类因子在 margin 标签上稳定；theta_per_vega、theta_per_gamma 在 stop avoidance 上更稳定；tail-move coverage 在残差 IC 中更有价值。

### 10.1 合约层：用于选腿排序

| factor | mean_ic | t_stat | positive_ic_rate |
| --- | ---: | ---: | ---: |
| b5_premium_per_capital_day | 0.226 | 24.3 | 80.5% |
| premium_yield_margin | 0.226 | 24.3 | 80.5% |
| b3_vomma_loss_ratio_low | 0.213 | 27.3 | 81.8% |
| premium_to_iv10_loss | 0.201 | 26.6 | 80.6% |
| b5_premium_per_vega | 0.189 | 25.8 | 79.5% |
| premium_to_stress_loss | 0.187 | 25.5 | 78.5% |
| gamma_rent_penalty_low | 0.178 | 24.3 | 77.1% |
| friction_ratio_low | 0.127 | 19.3 | 74.0% |
| b5_capital_lockup_days_low | 0.112 | 14.4 | 71.2% |
| cost_liquidity_score | 0.090 | 16.2 | 72.5% |
| b5_theta_per_gamma | 0.085 | 13.5 | 65.9% |
| b5_low_price_flag_low | 0.080 | 14.9 | 75.3% |

![合约层修正 IC 热力图](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/contract_primary/01_corrected_ic_heatmap.png)

![合约层累计 IC](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/contract_primary/04_corrected_cum_ic_pnl_per_margin.png)

合约层最强的是资本效率和权利金覆盖类因子。`premium_yield_margin` 与 `b5_premium_per_capital_day` 本质相同，适合衡量每单位资金占用能买到多少权利金；`premium_to_iv10_loss`、`b5_premium_per_vega`、`premium_to_stress_loss` 更直接衡量权利金对 IV 冲击、vega 暴露和压力亏损的覆盖程度。

但需要注意，资本效率因子容易把策略推向“高权利金、高风险”的区域，因此不能单独作为排序主因子。更合理的合约层排序应是：先通过低价、tick、手续费、异常价硬过滤；再用 premium_to_iv10_loss / premium_to_stress_loss / b5_premium_per_vega 排序；最后用 theta_per_vega、theta_per_gamma、tail_move_coverage 做风险惩罚。

### 10.2 止损规避：用于尾部与持有质量

| factor | mean_ic | t_stat | positive_ic_rate |
| --- | ---: | ---: | ---: |
| b5_theta_per_vega | 0.097 | 13.8 | 67.2% |
| b5_theta_per_gamma | 0.079 | 12.6 | 64.2% |
| breakeven_cushion_score | 0.066 | 17.5 | 70.2% |
| b5_delta_ratio_to_cap_low | 0.061 | 13.4 | 68.8% |
| b5_breakout_distance_down_60d | 0.041 | 5.8 | 56.8% |
| b3_vol_of_vol_proxy_low | 0.039 | 6.2 | 56.7% |
| gamma_rent_penalty_low | 0.026 | 4.0 | 54.2% |
| premium_to_stress_loss | 0.026 | 3.9 | 53.6% |
| b5_premium_per_vega | 0.026 | 4.0 | 53.9% |
| b5_iv_reversion_score | 0.021 | 2.9 | 53.7% |

止损规避上，最有效的不是单纯 premium，而是 theta 与 vega/gamma 的相对效率。这符合期权卖方逻辑：我们不是要收最多权利金，而是要收“单位 vega/gamma 风险下更值得收”的权利金。`theta_per_vega` 对 vega 损耗特别重要，后续可以作为硬止损之外的持仓质量监控指标。

### 10.3 品种方向层：用于品种与 P/C 侧预算

| factor | mean_ic | t_stat | positive_ic_rate |
| --- | ---: | ---: | ---: |
| gamma_rent_penalty_low | 0.177 | 26.3 | 80.8% |
| premium_to_stress_loss | 0.171 | 26.0 | 80.0% |
| b5_premium_per_capital_day | 0.167 | 19.5 | 76.4% |
| premium_yield_margin | 0.167 | 19.5 | 76.4% |
| b5_premium_per_vega | 0.155 | 23.9 | 79.0% |
| premium_to_iv10_loss | 0.155 | 23.5 | 79.3% |
| b5_theta_per_gamma | 0.153 | 25.4 | 78.5% |
| b3_vomma_loss_ratio_low | 0.142 | 20.7 | 78.4% |
| b5_capital_lockup_days_low | 0.114 | 14.6 | 70.0% |
| b5_theta_per_vega | 0.079 | 11.7 | 64.6% |
| b5_breakout_distance_up_60d | 0.073 | 11.4 | 64.7% |
| b5_breakout_distance_down_60d | 0.070 | 11.3 | 63.6% |

![品种方向层累计 IC](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/product_side_primary/04_corrected_cum_ic_pnl_per_margin.png)

品种方向层的结果说明，许多合约层有效的因子，在聚合到品种和 P/C 侧后仍然有效。这对后续很关键：B6 不必只在合约层做排序，还可以把品种方向预算向“该品种该方向的权利金质量更高、gamma rent 更低、stress 覆盖更好”的方向倾斜。

趋势和 breakout 因子在这个层级更有意义。它们不一定能直接预测哪个合约 PnL 最高，但能帮助判断今天该多卖 Put、少卖 Call，还是反过来。

## 11. 共线性与正交化

![因子相关性矩阵](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/08_corrected_factor_correlation_matrix.png)

| factor_a | factor_b | spearman_rho |
| --- | --- | ---: |
| variance_carry | b5_variance_carry_forward | 1.000 |
| premium_yield_margin | b5_premium_per_capital_day | 1.000 |
| premium_to_iv10_loss | b5_premium_per_vega | 0.993 |
| premium_to_iv10_loss | b3_vomma_loss_ratio_low | 0.984 |
| premium_to_stress_loss | gamma_rent_penalty_low | 0.972 |
| b5_mom_20d | b5_trend_z_20d | 0.966 |
| premium_to_stress_loss | b5_premium_per_vega | 0.961 |
| b3_vomma_loss_ratio_low | b5_premium_per_vega | 0.956 |
| premium_to_iv10_loss | premium_to_stress_loss | 0.944 |
| premium_to_stress_loss | b3_vomma_loss_ratio_low | 0.897 |

B5 因子存在明显共线性。很多看似不同的指标，其实都在表达“权利金相对某种风险是否够厚”。这不是坏事，但如果全部放进打分模型，会造成重复计分，导致倾斜强度失真。

建议每个因子族只保留一个代表变量：

- premium / capital：保留 `premium_yield_margin` 或 `b5_premium_per_capital_day`，二选一。
- premium / vega shock：保留 `premium_to_iv10_loss` 或 `b5_premium_per_vega`，二选一。
- premium / stress：保留 `premium_to_stress_loss` 或 `gamma_rent_penalty_low`，二选一。
- trend：保留 `b5_trend_z_20d` 或 breakout distance，不要同时把普通动量、趋势 z 和 breakout 都作为同等 alpha。
- vol regime：保留 `b5_iv_reversion_score` 与 `b3_vol_of_vol_proxy_low`，但它们更像状态变量，不应与合约 ranking 重复加分。

![控制 margin/stress 后的残差 IC](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/10_residual_ic_plus_margin_stress.png)

残差 IC 更能反映“扣除保证金和压力亏损解释后，还有没有增量信息”。这里表现较好的包括：

| factor | mean_ic | t_stat | positive_ic_rate |
| --- | ---: | ---: | ---: |
| b5_premium_to_tail_move_loss | 0.049 | 7.7 | 60.4% |
| b3_vol_of_vol_proxy_low | 0.036 | 5.4 | 56.6% |
| b5_premium_to_mae20_loss | 0.036 | 5.7 | 58.4% |
| b5_iv_reversion_score | 0.034 | 5.6 | 55.0% |
| b5_capital_lockup_days_low | 0.030 | 4.7 | 55.3% |
| premium_to_stress_loss | 0.026 | 3.7 | 55.8% |
| b5_premium_per_vega | 0.025 | 3.7 | 56.1% |
| premium_to_iv10_loss | 0.024 | 3.5 | 56.5% |

这说明 tail-move coverage、vol-of-vol、IV reversion 是更接近“独立增量”的变量。它们不一定给最高 raw IC，但非常适合做尾部风控和状态释放。

![非重叠日期 IC](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/11_nonoverlap_ic_pnl_per_margin.png)

非重叠检验中，`b3_vomma_loss_ratio_low`、`premium_yield_margin`、`premium_to_iv10_loss`、`b5_premium_per_vega`、`premium_to_stress_loss` 仍然保持较强效果，说明这些指标不是完全由相邻交易日重复样本堆出来的。

## 12. 因子库映射表

以后所有因子报告都需要明确说明：这个因子属于什么族群、适用哪个层级、改善收益公式里的哪一项、应该作为排序还是风控，以及它对留存率、止损、尾部和交易代价的影响。

| 因子名称 | 因子族群 | 适用层级 | 改善公式变量 | 使用方式 | IC/分层表现 | 留存率影响 | 止损影响 | 尾部影响 | 交易代价 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| premium_yield_margin / b5_premium_per_capital_day | premium | 合约层、品种层 | Premium Pool、Deployment Ratio | 排序、资金效率辅助 | 合约 margin IC 0.226；非重叠 0.208 | 能提高单位资金权利金，但残差 IC 较弱 | 不是主要止损因子 | 可能偏向高权利金高风险腿 | 需叠加流动性与低价过滤 |
| premium_to_iv10_loss | vega | 合约层 | Retention Rate、Tail / Stop Loss | 合约排序、vega 风险覆盖 | 合约 margin IC 0.201；残差 IC 0.024 | 有助于提高权利金留存质量 | 止损 IC 中等偏弱 | 对 IV shock 覆盖有直接意义 | 与 premium_per_vega 高度共线 |
| b5_premium_per_vega | vega | 合约层、品种方向层 | Retention Rate、Tail / Stop Loss | 合约排序、vega 预算 | 合约 margin IC 0.189；品种方向 IC 0.155 | 提高单位 vega 收益 | 止损 IC 约 0.026 | 控制 short vega 性价比 | 与 premium_to_iv10_loss 二选一 |
| premium_to_stress_loss | tail/gamma | 合约层、品种方向层 | Retention Rate、Tail / Stop Loss | stress budget 排序、预算倾斜 | 合约 IC 0.187；品种方向 IC 0.171 | 对留存率有稳定贡献 | 止损 IC 约 0.026 | 是核心尾部覆盖因子 | 可能牺牲部分权利金池 |
| gamma_rent_penalty_low | gamma | 合约层、品种方向层 | Tail / Stop Loss | gamma 风险惩罚 | 合约 IC 0.178；品种方向 IC 0.177 | 改善 gamma 风险下的留存 | 止损 IC 约 0.026 | 可降低短 gamma 脆弱性 | 与 stress_loss 高度共线 |
| b5_theta_per_gamma | gamma | 合约层、品种方向层 | Retention Rate、Tail / Stop Loss | 排序、gamma 风控 | 合约 IC 0.085；品种方向 IC 0.153；stop IC 0.079 | 提高 theta 质量 | 止损规避较强 | 降低单位 theta 的 gamma 租金 | 可能降低开仓权利金 |
| b5_theta_per_vega | vega | 合约层、品种方向层 | Retention Rate、Tail / Stop Loss | vega 风控、持仓质量 | stop IC 0.097；品种 stop IC 0.081 | 对留存率改善更偏防守 | 是最强止损规避因子之一 | 控制升波亏损 | 可能牺牲高 vega 高权利金腿 |
| b5_premium_to_tail_move_loss | tail | 合约层、品种层 | Tail / Stop Loss | 尾部覆盖、预算惩罚 | 残差 IC 0.049 | 有助于剔除脆弱腿 | 间接降低止损 | 控制历史尾部不利移动 | 需要防止历史尾部低估未来 |
| b5_premium_to_mae20_loss | tail | 合约层、品种层 | Tail / Stop Loss | 尾部覆盖、诊断 | 残差 IC 0.036 | 有助于提高持有质量 | 止损 IC 中等 | 衡量近期最大不利移动覆盖 | 与 tail_move_loss 同族 |
| b5_iv_reversion_score | vol regime | 时间层、品种层 | Retention Rate、Tail / Stop Loss | 降波环境释放、升波环境降权 | 残差 IC 0.034；stop IC 0.021 | 在降波阶段改善留存 | 辅助止损规避 | 识别波动状态切换 | 不宜单独作为合约 alpha |
| b3_vol_of_vol_proxy_low | vol regime | 时间层、品种层 | Tail / Stop Loss | 波动率二阶风险控制 | 残差 IC 0.036；stop IC 0.039 | 改善 vega 暴露质量 | 对止损有帮助 | 控制 IV 自身波动放大 | 可能过度保守 |
| b5_breakout_distance_up_60d / down_60d | trend | P/C 侧、品种方向层 | Tail / Stop Loss | P/C 预算偏移、方向侧惩罚 | 品种方向 IC 约 0.07 | 依赖方向环境 | 可减少趋势侧止损 | 避免卖在突破方向 | 不应作为全局排序因子 |
| friction_ratio_low | liquidity/cost | 合约层 | Cost / Slippage | 硬过滤、执行质量 | 合约 IC 0.127；残差为负 | 主要改善成本而非 alpha | 低流动性下可降止损噪音 | 防止假价格污染 | 应作为硬过滤 |
| b5_low_price_flag_low / b5_tick_value_ratio_low | liquidity/tick | 合约层 | Cost / Slippage、Tail / Stop Loss | 硬过滤、低价保护 | 低价分层差异极大 | 明显改善低价留存 | 明显降低噪音止损 | 防止 tick 放大尾部 | 会牺牲部分候选数 |
| cooldown_penalty / cooldown_release | cooldown | 品种层、时间层 | Tail / Stop Loss | 重复止损诊断、冷静期规则 | 当前 IC 较弱 | 暂不宜直接加分 | 可作为重复止损约束 | 控制同品种连续踩雷 | 需要重新定义 release 条件 |

![因子用途图](output/candidate_layers_corrected_s1_b5_full_shadow_v1_2022_latest/12_factor_usage_map.png)

## 13. 品种层结果与交易含义

权利金贡献最高的品种包括 AU、SC、MO、CU、IO、AG、PG、AL、I、ZN。这些品种提供了主要 Premium Pool，但并不代表它们都适合高预算。比如 AU、SC 权利金很厚，但低价占比也偏高；MO 权利金厚但影子 PnL 为负；L、PP、V、EG 等化工链品种亏损显著，说明尾部路径和止损聚集风险较高。

| 品种 | 权利金池特征 | 影子结果 | 交易含义 |
| --- | --- | --- | --- |
| SC | 权利金极厚 | PnL 为正，止损率约 25% | 可作为高质量候选，但需要流动性和尾部能源风险约束 |
| CU | 权利金厚、流动性较好 | PnL 为正，止损率约 20.8% | 适合进入主力候选，但要看金属板块集中 |
| I | 权利金中等偏厚 | PnL 为正，但低价占比高 | 可做，但必须过滤低价和异常价 |
| CF / SR / PK / AP | 权利金不算最大 | 留存较好 | 适合做品种层预算倾斜候选 |
| MO | 权利金很厚 | PnL 明显为负 | 不能只看权利金，应叠加 tail / stress / vega 约束 |
| L / PP / V / EG | 权利金不够厚或路径差 | 亏损显著，止损率高 | 化工链需要单独的尾部和板块约束 |

这部分也解释了为什么“像乐得”的组合不会只追求流动性，也不会只追求权利金。真正的目标是：在流动性可执行的前提下，找到每单位 tail risk 能留下最多权利金的品种。

## 14. 与后续 B6/B7 的关系

B5 的结果支持下一步分三条线推进。

第一条是 B6 合约层排序。使用去共线后的核心因子：`premium_to_iv10_loss` 或 `b5_premium_per_vega`、`premium_to_stress_loss` 或 `gamma_rent_penalty_low`、`b5_theta_per_gamma`、`b5_theta_per_vega`、`b5_premium_to_tail_move_loss`，并保留低价、tick、friction 的硬过滤。

第二条是 B6 P/C 侧预算。趋势、breakout、skew、IV state 不应直接当作合约 alpha，而应判断今天更适合卖 Put、卖 Call 还是双卖。上涨趋势中 Put 预算可提升、Call delta 或预算应压低；下跌趋势反过来；震荡环境下维持双卖。

第三条是 B7 组合层风险预算。B5 已经显示 Top5 stress 占比很高，因此组合层必须考虑板块、到期日、tail correlation、stop cluster 和 same-expiry gamma。这里可以引入 Tail-HRP，而不是简单等权或流动性权重。

## 15. 对“vega 要赚钱”的启发

S1 是 short theta、short vega、short gamma 的策略。理论上，长期希望 theta 收益为正、vega 收益也为正，至少不能持续依赖 delta 方向收益。但 B0-B4 的归因显示，我们此前经常没有真正赚到 vega，甚至在升波或假降波阶段承担了过多 vega 损耗。

B5 对这个问题给出了更清晰的路径：

- 用 `premium_to_iv10_loss`、`b5_premium_per_vega` 提高单位 vega 的权利金覆盖。
- 用 `b5_theta_per_vega` 过滤“为了很少 theta 承担过多 vega”的候选。
- 用 `b3_vol_of_vol_proxy_low` 和 `b5_iv_reversion_score` 区分稳定降波与波动率二阶风险。
- 用 `b5_premium_to_tail_move_loss` 避免看起来 vega 合理、但标的尾部移动会直接打穿的候选。

这意味着 vega 风控不应只是限制净 cash vega，而应该是“收进来的权利金是否足够覆盖合理 IV shock”。这是 B6 的核心方向。

## 16. 结论

B5 full shadow 的结论非常清楚：S1 不是没有权利金，也不是必须靠大幅放大保证金才能接近目标；真正的问题是权利金质量、部署位置和尾部留存。候选池中有大量可以收的钱，也有大量会把钱吐回去甚至倒亏的腿。下一阶段的关键，是把候选池从“可交易”升级为“值得交易”。

从收益公式看，B5 给出的研究路线是：

- Premium Pool：保持全品种、次月、delta 小于 0.1 的广覆盖，但过滤低价、tick 和费用不合格合约。
- Deployment Ratio：不再等权分配，而是按品种方向和合约质量倾斜。
- Retention Rate：核心使用 premium_to_iv10_loss、premium_to_stress_loss、theta_per_vega、theta_per_gamma 提高留存。
- Tail / Stop Loss：引入 tail move coverage、vol-of-vol、IV reversion、breakout 和 stop cluster。
- Cost / Slippage：低价、tick、friction 必须进入硬过滤。

建议下一版 B6 先做“去共线合约质量排序 + 低价执行硬过滤 + P/C 方向预算轻倾斜”，不要一次性加入组合 Tail-HRP。B7 再专门做组合层的到期分散、板块约束、尾部相关性和 stop cluster 管理。这样可以把因子效果、组合约束和风险预算三件事拆开验证，不会再把有效因子和复杂风控混在一起。

