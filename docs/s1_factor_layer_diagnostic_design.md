# S1 因子分层检验设计：把 B2/B3 当作期权横截面因子来验证

## 1. 研究动机

目前 B2/B3 的判断主要来自组合回测：某个预算倾斜版本最终 NAV 更高、超额路径更好，就认为该因子可能有效。但卖权策略的组合回测很容易被以下因素干扰：

- 某一段商品行情刚好有利于 Put 或 Call。
- 少数品种、少数月份、少数止损事件决定结果。
- 因子本身没有稳定解释力，只是在某个区间配合了仓位分配。
- 更高收益来自卖了更厚尾部风险，而不是真正提高权利金质量。

因此需要引入类似股票中性研究里的“横截面分层检验”。在股票因子里，我们会每天按因子分成 Q1-Q5，观察高分组和低分组的未来收益差异。S1 也可以做类似检验，但目标不是买高卖低，而是判断：

```text
在同一天、相同 S1 基准框架下，
高质量权利金分层是否比低质量分层更适合卖？
```

## 2. 与股票中性分层的差异

股票中性通常检验的是 `Q5 - Q1` 多空收益。S1 是卖权策略，不天然做“买入低分期权”作为对冲，因此不能简单照搬多空组合。S1 的分层检验应关注：

- 高分层是否有更高的权利金留存率。
- 高分层是否有更高的 `PnL / gross premium`。
- 高分层是否有更低的止损率和止损损耗。
- 高分层是否没有显著更差的 vega/gamma 吞噬。
- 高分层是否在压力月份仍然不明显劣化。

换句话说，S1 的分层目标不是证明“高分能涨、低分会跌”，而是证明：

```text
高分层的承保质量更好；
高分层收到的保险费更能覆盖波动率、路径和尾部风险。
```

## 3. 分层层级

### 3.1 合约级分层

样本单位为单个成交合约。适合检验执行价选择和合约质量指标，例如：

- `premium_to_iv5_loss`
- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `theta_vega_efficiency`
- `gamma_rent_penalty`
- `friction_ratio`
- `b3_vomma_loss_ratio`

回答的问题：

```text
同一批可交易卖权中，哪个执行价/哪张合约更值得卖？
```

风险：同一天同品种的多个邻近执行价高度相关，不能把每张合约都当作完全独立样本。因此合约级结果只作为细节诊断，不作为最终因子结论。

### 3.2 品种-方向分层

样本单位为 `signal_date + product + option_type`，即某日某品种某方向的一组成交。这个层级最接近 B2/B3 的实际用途，因为 B2/B3 本质上是在决定：

```text
今天哪些品种、哪些方向获得更多风险预算？
```

优先用于验证：

- B2c 综合权利金质量分。
- B3c IV shock coverage。
- B3d vomma penalty。
- B3b vol-of-vol proxy 的正向和反向含义。
- B3e composite 是否真的优于单因子。

### 3.3 环境分层

在品种-方向分层基础上，进一步按环境切片：

- 低波环境。
- 正常波动环境。
- 升波环境。
- 降波环境。
- 高 VOV 且 VOV 继续上升。
- 高 VOV 且 VOV 开始稳定或回落。
- stop cluster 密集环境。

回答的问题：

```text
某个因子是普适有效，还是只在特定波动环境有效？
```

尤其是 B3b，需要重点检验：

```text
低 vol-of-vol 是否只是低风险低保费；
高 vol-of-vol 是否在确认不再加速后，反而代表更好的卖方保费环境。
```

## 4. 分层指标

每个因子每天做横截面分组，默认分为 5 层：

```text
Q1 = 因子最低层
Q5 = 因子最高层
```

对低值更好的因子，额外报告“good layer - bad layer”，避免方向误读。例如：

- `friction_ratio`：低值好。
- `gamma_rent_penalty`：低值好。
- `b3_vomma_loss_ratio`：低值好。
- 当前 B3b 的 `b3_vol_of_vol_score` 是低 VOV 得高分，但后续要同时看原始 VOV 的高低分层。

每层必须输出：

- `gross_open_premium`
- `net_pnl_after_fee`
- `net_pnl / gross_open_premium`
- `premium_retained_cash`
- `premium_retained_ratio`
- `stop_rate`
- `stop_loss / gross_open_premium`
- `expiry_rate`
- `trade_count`
- `product_side_count`
- `worst_daily_layer_return`
- `daily_mean`
- `daily_t_stat`

## 4.1 我们到底要预测什么

S1 因子分层检验不是预测标的涨跌，也不是预测明天 IV 点位。它要预测的是一张卖权交易作为“保险承保单”的未来质量。第一版定义四个核心未来标签：

```text
1. future_net_pnl_per_premium
   = 未来按当前规则平仓后的净 PnL / 开仓权利金

2. future_retained_ratio
   = 未来最终留存权利金 / 开仓权利金

3. future_stop_avoidance
   = - 是否触发止损
   或在品种-方向聚合后 = - 止损率

4. future_stop_loss_avoidance
   = 止损损耗 / 开仓权利金
   该值通常小于等于 0，越高代表止损损耗越小
```

它们分别回答四个问题：

```text
高分层最终赚钱吗？
高分层保费留得住吗？
高分层中途是否更少触发 2.5x 止损？
即使触发止损，高分层损耗是否更轻？
```

对 S1 来说，`future_stop_avoidance` 非常关键。因为卖权策略平时收益平滑，真正决定长期表现的往往是少数止损或尾部日。如果一个因子提高了平均收益，但显著提高止损概率，就不能视为优质因子。

## 4.2 是否需要 IC

需要，但 IC 的定义要改成期权卖方口径。

股票因子常用 Rank IC：

```text
IC_t = corr(rank(factor_t), rank(future_return_t))
```

S1 中对应为：

```text
IC_t = corr(rank(good_factor_t), rank(future_quality_label_t))
```

其中 `good_factor_t` 已经做过方向调整：

- 高值好的因子：直接使用原始因子。
- 低值好的因子：使用 `-因子值`。

未来标签也统一为“越高越好”：

- `future_net_pnl_per_premium`：越高越好。
- `future_retained_ratio`：越高越好。
- `future_stop_avoidance`：越高越好，代表越不容易止损。
- `future_stop_loss_avoidance`：越高越好，代表止损损耗越小。

因此，IC 为正才代表因子有正确方向。需要输出：

- 日度 Rank IC。
- 平均 IC。
- IC t-stat。
- IC 正值占比。
- IC 累计路径。
- 按 Put/Call、vol regime、年份切片后的 IC。

注意：IC 不能替代分层收益。IC 检验排序方向，分层检验经济价值。一个因子可能 IC 不高，但 Q5 的容量和收益很好；也可能 IC 很高，但只在很薄的样本里有效。

## 4.3 合约级与品种-方向级

第一版必须同时支持两个层级。

### 合约级

样本单位是一张已开仓合约。标签来自该合约最终平仓结果：

```text
open_sell -> expiry 或 sl_s1
```

合约级最适合研究：

- 哪个执行价更好。
- `premium_to_iv10_loss` 是否能预测止损。
- `b3_vomma_loss_ratio` 是否能预测 IV spike 下的非线性亏损。
- `friction_ratio` 是否真的应该做硬过滤。

### 品种-方向级

样本单位是：

```text
signal_date + product + option_type
```

它把同一天同品种同方向的多张合约聚合起来，用开仓权利金加权平均因子值，用实际结果合计未来标签。这个层级最接近 B2/B3 的实际预算用途，因此是主检验口径。

## 4.4 环境切片

分层结果必须按环境切片，否则容易把不同市场状态混在一起误读。第一版至少支持：

- Put / Call。
- `vol_regime`，例如 normal、falling、high 等。
- 年份和月份。

后续应增加：

- 高 VOV 且 VOV 继续上升。
- 高 VOV 且 VOV 开始回落。
- IV shock coverage 高/低。
- stop cluster 密集/不密集。
- 商品板块、相关性组。

特别是 B3b 的反向假设必须通过环境切片验证：

```text
高 VOV + VOV 继续上升 = 危险；
高 VOV + VOV 稳定或回落 = 卖方机会。
```

如果不分环境，VOV 因子可能在总样本里显得无效，但在“高 VOV 后开始降波”的子环境里非常有效。

## 5. 防未来函数原则

分层必须严格使用 `signal_date` 当天已经可见的指标：

- 因子值来自开仓信号生成时的字段，如 `premium_quality_score`、`premium_to_iv10_loss`、`b3_vomma_loss_ratio`。
- 分层在 `signal_date` 横截面内完成。
- 平仓结果、止损、到期收益只作为未来标签，不参与分层。
- 统计时按日期聚合后再计算均值和 t 值，避免同一天多合约重复样本放大显著性。

第一版脚本基于已经成交的订单做“成交后分层检验”。它能回答：

```text
在策略实际交易过的样本里，高分层是否更好？
```

它还不能完全回答：

```text
在所有候选但未成交的样本里，高分层是否也更好？
```

如果第一版结果有价值，后续需要让回测引擎额外落盘完整候选池，做真正的 all-candidate factor test。

## 6. B2/B3 的首批检验因子

### 6.1 B2 因子

- `premium_quality_score`
- `iv_rv_carry_score`
- `breakeven_cushion_score`
- `premium_to_iv_shock_score`
- `premium_to_stress_loss_score`
- `theta_vega_efficiency_score`
- `cost_liquidity_score`
- `variance_carry`
- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `theta_vega_efficiency`
- `gamma_rent_penalty`
- `friction_ratio`

### 6.2 B3 因子

- `b3_clean_vega_score`
- `b3_forward_variance_score`
- `b3_vol_of_vol_score`
- `b3_iv_shock_score`
- `b3_joint_stress_score`
- `b3_vomma_score`
- `b3_skew_stability_score`
- `b3_vol_of_vol_proxy`
- `b3_vov_trend`
- `b3_iv_shock_coverage`
- `b3_joint_stress_coverage`
- `b3_vomma_loss_ratio`
- `b3_skew_steepening`

## 7. 对当前实验的预期判断

### 7.1 B2c

如果 B2c 真的有效，应看到：

```text
premium_quality_score 高分层的留存率更高；
S1 PnL / gross premium 更高；
vega/gamma 吞噬率不明显更差；
止损率不更高。
```

### 7.2 B3c

如果 IV shock coverage 有效，应看到：

```text
premium_to_iv_shock 高分层在升波月份损失更小；
高分层的 stop loss / premium 更低；
高分层不只是 theta 更厚，而是升波覆盖更好。
```

### 7.3 B3d

如果 vomma penalty 有效，应看到：

```text
b3_vomma_loss_ratio 低分险层更稳；
高 b3_vomma_score 分层在 IV 大幅跳升时回撤更小；
高分层保留 B2c 的 theta，同时降低非线性升波亏损。
```

### 7.4 B3b

当前 B3b 正向防守版几乎全程跑输，分层检验要重点确认：

```text
低 VOV 是否只是低保费；
高 VOV 是否在 VOV 不再加速时反而更赚钱；
VOV 是否应该作为“条件反向”的降波加仓信号，而不是简单风险降权信号。
```

## 8. 第一版落地范围

第一版脚本只做离线诊断：

- 输入一个或多个回测 tag。
- 读取 `orders_<tag>.csv`。
- 使用平仓行作为未来结果标签。
- 按 `signal_date` 横截面分层。
- 输出 Rank IC 和止损概率检验。
- 同时支持合约级和品种-方向级。
- 输出 CSV、PNG 和简短 Markdown 报告。

第一版不改变策略，不改变回测结果，不影响正在运行的 B3 回测。

## 9. 下一步使用方式

建议按以下顺序使用：

1. 对 B2c 跑分层诊断，确认 B2 的综合质量分是否有单调性。
2. 对 B3c/B3d 跑分层诊断，确认当前组合超额是否来自真实因子解释力。
3. 对 B3b 同时看 `b3_vol_of_vol_score` 和 `b3_vol_of_vol_proxy`，判断是否应做反向或条件反向。
4. 如果分层结果稳定，再设计更强倾斜或筛选版本。
5. 如果分层结果不稳定，则暂停继续优化参数，优先补完整候选池和样本外检验。
