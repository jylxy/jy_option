# S1 候选池因子检验首轮实验记录

报告日期：2026-04-28

## 1. 实验目的

本轮实验不是继续调 B2c 参数，而是验证一个更基础的问题：

```text
在 B2c 倾斜和组合预算裁剪之前，
B2/B3 这些权利金质量因子是否真的能解释未来承保质量？
```

因此，本轮使用 B1 可交易候选池作为样本源，记录所有候选合约，并对每个候选做统一 1 手 shadow label。这样可以避免“只看 B2c 已成交样本”带来的处理后偏差。

## 2. 实验口径

配置文件：

```text
config_s1_candidate_universe_b1_shadow_202503_signal_stop25.json
```

核心设置：

- 基础策略口径继承 B1：`config_s1_baseline_b1_liquidity_oi_rank_stop25.json`。
- 信号采样期：`2025-03-01` 至 `2025-03-31`。
- 标签观察期：回测运行至 `2025-06-30`，用于让 3 月候选完成止损或到期。
- 候选池阶段：`pre_budget_universe`，即预算倾斜和组合裁剪之前。
- 每个候选按 1 手卖出做 shadow label。
- shadow 止损：期权价格达到入场权利金 `2.5x`。
- shadow 到期：按内在价值结算，虚值归零。
- 本轮 shadow 使用日收盘路径，不等同于真实分钟止损确认。

输出文件：

```text
output/s1_candidate_universe_s1_candidate_universe_b1_shadow_sig202503_eval202506.csv
output/s1_candidate_outcomes_s1_candidate_universe_b1_shadow_sig202503_eval202506.csv
output/candidate_layers_s1_candidate_universe_b1_shadow_sig202503_eval202506/
```

## 3. 样本概况

候选池样本：

- 候选合约记录：`8,204` 条。
- 信号日：`16` 个交易日。
- 覆盖品种：`53` 个。
- 覆盖合约：`1,450` 个。
- shadow outcome 覆盖率：`96.8%`，未完成样本 `3.2%`。

shadow 标签结果：

- 整体止损率：`52.3%`。
- 整体到期率：`44.5%`。
- 整体平均权利金留存率：`-396.2%`。
- Call 候选平均留存率：`-25.3%`，止损率 `25.5%`。
- Put 候选平均留存率：`-732.6%`，止损率 `76.6%`。

这说明 2025 年 3 月信号期本身是一个非常偏压力的样本，尤其是 Put 侧。它适合检验尾部与止损解释力，但不能单独代表长期均值。

## 4. 合约级结论

以 `future_retained_ratio` 为标签，合约级 Rank IC 较强的因子是：

| 因子 | 方向 | 日数 | 平均 Rank IC | t 值 |
|---|---:|---:|---:|---:|
| `friction_ratio` | 越低越好 | 16 | 0.411 | 20.85 |
| `premium_to_iv10_loss` | 越高越好 | 16 | 0.395 | 24.24 |
| `b3_vomma_loss_ratio` | 越低越好 | 16 | 0.394 | 28.20 |
| `b3_iv_shock_coverage` | 越高越好 | 16 | 0.391 | 23.27 |
| `premium_to_stress_loss` | 越高越好 | 16 | 0.375 | 22.57 |
| `gamma_rent_penalty` | 越低越好 | 16 | 0.356 | 19.03 |

合约级 Q5-Q1 分层差也支持同样结论：

- `friction_ratio_low` 的 Q5-Q1 留存率差约 `+5.41`，止损率差约 `-25.1%`。
- `gamma_rent_penalty_low` 的 Q5-Q1 留存率差约 `+4.67`，止损率差约 `-31.2%`。
- `premium_to_stress_loss` 的 Q5-Q1 留存率差约 `+4.41`，止损率差约 `-34.5%`。
- `premium_to_iv10_loss` 的 Q5-Q1 留存率差约 `+4.14`，止损率差约 `-35.7%`。

初步判断：合约内选腿时，真正有效的不是“权利金绝对厚”，而是“权利金能覆盖多少 IV shock / stress loss / convexity / friction”。

## 5. 品种-方向级结论

品种-方向级样本把同一日、同一品种、同一方向的候选合约聚合，用于判断风险预算应给哪个品种和哪一侧。

以 `future_retained_ratio` 为标签，品种-方向级 Rank IC 较强的因子是：

| 因子 | 方向 | 日数 | 平均 Rank IC | t 值 |
|---|---:|---:|---:|---:|
| `friction_ratio` | 越低越好 | 16 | 0.305 | 8.41 |
| `b3_vomma_loss_ratio` | 越低越好 | 16 | 0.294 | 9.28 |
| `gamma_rent_penalty` | 越低越好 | 16 | 0.284 | 15.43 |
| `premium_to_stress_loss` | 越高越好 | 16 | 0.259 | 13.41 |
| `premium_to_iv10_loss` | 越高越好 | 16 | 0.248 | 8.28 |
| `variance_carry` | 越高越好 | 9 | 0.238 | 5.14 |

但有一个非常重要的反常结果：

- `premium_quality_score` 在合约级 IC 为 `+0.108`。
- `premium_quality_score` 在品种-方向级 IC 为 `-0.096`。
- `premium_to_iv_shock_score`、`premium_to_stress_loss_score`、`cost_liquidity_score` 在品种-方向级也出现负 IC。

这说明当前综合分更适合作为合约级辅助排序，不宜直接作为品种预算倾斜的核心分数。品种预算层应该更多使用原始、可解释、跨品种更稳定的指标，例如 `premium_to_stress_loss`、`premium_to_iv10_loss`、`gamma_rent_penalty`、`b3_vomma_loss_ratio` 和 `variance_carry`。

## 6. 策略含义

本轮实验支持一个重要方向：

```text
B2c 不应该只是一个综合质量分。
它应该拆成两层：
1. 合约级：选择哪张执行价。
2. 品种-方向级：决定哪个品种/哪一侧拿更多风险预算。
```

更具体地说：

- 合约级可以继续使用 `premium_to_iv10_loss`、`premium_to_stress_loss`、`b3_iv_shock_coverage`、`gamma_rent_penalty`、`friction_ratio` 做排序。
- 品种-方向级应降低 `premium_quality_score` 的权重，甚至暂时不用它直接倾斜预算。
- 品种预算层应优先使用原始覆盖率和凸性风险指标，而不是把多个 score 混成一个总分。
- Put/Call 不能再机械对称，因为压力样本下 Put 侧暴露极其差；但这不等于长期只卖 Call，而是需要趋势/压力/尾部预算来动态调 P/C。

## 7. 工程结论

本轮原始长标签回测耗时 `1,359` 秒，平均 `14.2` 秒/天，偏慢。

原因不是候选池行数太大，而是标签观察期仍在继续执行真实 B1 开仓和组合管理。已经新增参数：

```text
s1_candidate_universe_skip_new_opens_after_signal_end = true
```

后续滚动月度实验中，信号期结束后将不再新开真实仓，只继续读取行情更新 shadow label，预计会明显提速，也能让实验口径更干净。

## 8. 下一步

建议下一步做滚动月度稳定性检验：

- 先跑 `2025-01` 至 `2025-08` 的逐月信号窗口，每个月观察到后 2-3 个月。
- 再扩展到 `2022` 至最新，按月生成候选池和 shadow label。
- 每个月分别输出合约级和品种-方向级 IC。
- 检查因子 IC 是否跨月份稳定，而不是只在 2025 年 3 月压力样本中有效。
- 如果稳定，再回到策略层做 B2d：合约排序和品种预算拆分的新组合实验。

