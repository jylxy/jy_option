# S1 候选池因子检验实验设计

报告日期：2026-04-28

对应问题：B2c 已经使用权利金质量因子做预算倾斜，因此不能再直接用 B2c 已成交样本证明这些单因子有效。下一版实验必须回到“倾斜之前”的全候选池，重新检验每个因子在没有策略处理偏差时是否真的有横截面解释力。

## 1. 核心结论

这版实验的目标不是再跑一个 B2c 参数，也不是继续调 `tilt` 强度，而是建立一个更干净的因子研究框架：

```text
B1/B0 基础可交易候选池
-> 不经过 B2c 预算倾斜
-> 记录所有候选合约和品种-方向候选
-> 对每个候选做统一 1 手 shadow label
-> 再检验 B2c 子因子的 IC、分层、止损概率和尾部质量
```

因此，本实验的基准不是 B2c 组合净值，而是 **B2c 倾斜前的候选池**。B2c 只作为后续策略组合对照，不作为单因子有效性的证明样本。

## 2. 为什么不能直接用 B2c 已成交样本

B2c 已成交样本已经被以下机制处理过：

- B2c 综合质量分影响了预算倾斜。
- 组合保证金、单品种上限、风险预算会决定哪些候选真正成交。
- 高分候选可能被卖更多手，低分候选可能很少成交。
- 部分候选没有进入 orders，因此缺少未来表现标签。

这会造成四类偏差。

### 2.1 样本选择偏差

如果某个低分合约本来就被 B2c 少卖或不卖，那么在 orders 样本里它天然缺失。此时 Q1/Q5 不是完整候选池的 Q1/Q5，而是“B2c 愿意成交的样本”里的 Q1/Q5。

### 2.2 处理后检验偏差

因子已经影响了仓位，再用实际成交 PnL 检验因子，会混入 sizing 效果。这个结果只能说明：

```text
B2c 处理后的组合样本里，哪些指标还解释剩余差异。
```

它不能说明：

```text
如果从零开始看全候选池，这些因子是否有效。
```

### 2.3 因子共线性和综合分稀释

`premium_quality_score` 由多个子因子混合而成。用已经倾斜后的样本拆单因子，强因子可能被综合分稀释，弱因子也可能因为和强因子共线而看起来有效。

### 2.4 组合约束污染因子解释

品种上限、方向上限、保证金上限和风险预算会改变成交结果。如果某个因子看起来有效，可能是因为它碰巧对应了更容易通过组合约束的候选，而不是因子本身真的预测承保质量。

## 3. 本实验要回答的问题

本实验要回答五个问题。

1. B2c 子因子在完整候选池上是否有稳定横截面 Rank IC？
2. 高分层是否比低分层有更高权利金留存率、更低止损率和更好 `PnL / premium`？
3. 哪些因子应该做硬过滤，哪些因子应该做合约选腿，哪些因子应该做品种/方向预算？
4. 这些因子的有效性是否跨年份、Put/Call、波动环境和商品板块稳定？
5. 如果把有效因子放回策略，应该先做哪一类组合实验？

注意，本实验不直接回答“下一版组合 NAV 一定能提高多少”。它先回答因子是否干净有效，再决定如何进入组合回测。

## 4. 候选池定义

候选池必须在 B2c 倾斜之前生成。

### 4.1 主候选池：B1 tradable universe

第一版建议以 B1 可交易候选池为主：

```text
全品种扫描
上市满 3 个月后才允许进入扫描
只看次月合约
只看虚值期权
abs(delta) <= 0.10
保留当前 0.5 元最低期权价格门槛
保留当前 ETF 暂时排除口径，直到 ETF 数据和价格映射 bug 完全修复
保留真实手续费和交易所保证金参数
保留当前成交量、持仓量、异常报价过滤和跳价确认逻辑
```

这里强调：B1 的流动性和价格门槛是“可交易性基础约束”，不是 B2c 因子。候选池必须先满足这些基础条件，否则会把明显不可交易的垃圾报价混进因子检验，污染结论。

### 4.2 扩展候选池：B0 broad universe

为了判断 B1 流动性排序是否本身已经筛掉了太多信息，可以做一个扩展候选池：

```text
B0 基础条件
+ 最低价格门槛
+ 基础成交量/持仓量非零
+ 不做 B1 排序截断
```

扩展候选池不作为第一版主结论，只作为稳健性检查。原因是它会包含更多低流动性合约，shadow label 的成交可实现性较弱。

### 4.3 不允许进入候选池的处理

以下条件仍然直接排除，不进入因子检验：

- 缺真实标的价格，无法计算 IV/Greeks。
- 缺合约乘数、到期日、行权价、期权类型等基础字段。
- 报价为 0 或小于当前最低价格门槛。
- 明显异常报价且未通过跳价确认。
- 无法映射到次月主交易期限。
- 因数据缺失无法做统一未来标签。

这些是数据和可交易性问题，不是策略因子问题。

## 5. 样本层级

实验必须同时落盘两个层级。

### 5.1 合约级 candidate

样本单位：

```text
signal_date + product + option_type + expiry + strike + contract_code
```

它回答：

```text
同一天、同一品种、同一方向、同一次月中，哪张执行价更值得卖？
```

适合检验：

- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `gamma_rent_penalty`
- `friction_ratio`
- `theta_vega_efficiency`
- `premium_to_iv_shock_score`
- `premium_to_stress_loss_score`

### 5.2 品种-方向级 candidate

样本单位：

```text
signal_date + product + option_type
```

它由同一日同一品种同一方向下的候选合约聚合得到。聚合方式默认用候选开仓权利金加权，同时保留等权聚合作为稳健性检查。

它回答：

```text
今天哪些品种、哪一侧应该获得更多风险预算？
```

适合检验：

- `b2_product_score`
- `variance_carry`
- `theta_vega_efficiency`
- 品种方向层面的 `premium_to_iv10_loss`
- 品种方向层面的 `premium_to_stress_loss`
- 后续 B3 的 forward vega、VOV、vomma、IV shock coverage

### 5.3 品种级 candidate

第一版可选，不作为主结论。样本单位：

```text
signal_date + product
```

它把 Call 和 Put 聚合，用于判断某个品种整体是否适合承保。这个层级容易混入 P/C 偏移和趋势判断，必须谨慎解释。

## 6. 因子字段

每个候选必须落盘以下字段。

### 6.1 基础识别字段

```text
signal_date
intended_open_date
product
exchange
sector
corr_group
option_type
contract_code
expiry
dte
strike
spot
future_contract
contract_multiplier
```

### 6.2 行情和可交易性字段

```text
option_price
bid
ask
mid
volume
open_interest
turnover
spread
spread_ratio
fee_per_lot
fee_ratio
friction_ratio
liquidity_rank
oi_rank
```

如果没有 bid/ask，只能使用当前可得的成交价或 TWAP，则必须记录 `quote_source`，并在报告里降低对交易摩擦因子的置信度。

### 6.3 Greeks 和风险字段

```text
delta
gamma
vega
theta
cash_delta
cash_gamma
cash_vega
cash_theta
margin_estimate
stress_loss
iv5_loss
iv10_loss
iv_shock_loss
vomma_loss_proxy
```

### 6.4 B2 权利金质量字段

```text
variance_carry
iv_rv_carry_score
breakeven_cushion
breakeven_cushion_score
premium_to_iv5_loss
premium_to_iv10_loss
premium_to_iv_shock_score
premium_to_stress_loss
premium_to_stress_loss_score
theta_vega_efficiency
theta_vega_efficiency_score
gamma_rent_penalty
cost_liquidity_score
premium_quality_score
b2_product_score
```

### 6.5 环境字段

```text
vol_regime
product_iv
atm_iv
rv5
rv10
rv20
rv60
iv_change_5d
rv_change_5d
vov_proxy
vov_trend
is_falling_vol
is_rising_vol
is_high_vol
is_structural_low_iv
recent_stop_count_by_product
recent_stop_count_portfolio
```

环境字段只能使用 `signal_date` 当日及以前可见数据。

## 7. 未来标签设计

每个候选必须使用统一 shadow trade 标签。不要使用真实 B2c 成交手数，不要使用组合预算权重。

### 7.1 Shadow trade 规则

默认标签规则：

```text
每个候选按 1 手卖出
使用和策略一致的开仓成交口径
使用真实手续费
持有到期或触发 2.5x 权利金止损
止损仍使用当前异常报价过滤和跳价确认逻辑
到期若未实值，期权价值归零
到期若实值，按内在价值结算
```

这能保证不同候选之间可比。

### 7.2 核心未来标签

```text
future_net_pnl
future_fee
future_gross_pnl
future_net_pnl_per_premium
future_retained_premium
future_retained_ratio
future_stop_flag
future_stop_avoidance
future_stop_loss
future_stop_loss_per_premium
future_stop_loss_avoidance
future_expiry_flag
future_days_held
future_max_premium_ratio
future_max_drawdown_per_premium
future_worst_daily_pnl
```

标签方向统一为“越高越好”：

- `future_net_pnl_per_premium` 越高越好。
- `future_retained_ratio` 越高越好。
- `future_stop_avoidance = -future_stop_flag` 越高越好。
- `future_stop_loss_avoidance = -abs(stop_loss) / premium` 越高越好。

### 7.3 PnL 归因标签

如果计算成本允许，建议为 shadow trade 生成近似归因：

```text
future_theta_pnl
future_vega_pnl
future_gamma_pnl
future_delta_pnl
future_residual_pnl
future_vega_loss_per_premium
future_gamma_loss_per_premium
future_theta_capture_ratio
```

如果第一版计算过慢，可以先只做核心标签，归因标签作为第二阶段。

## 8. 避免未来函数规则

候选池实验必须严格分离因子和标签。

### 8.1 因子只允许使用 signal_date 信息

允许使用：

- `signal_date` 当日收盘或约定信号时点已经可见的期权链、标的价、成交量、持仓量。
- 截止 `signal_date` 的历史 IV、RV、VOV、stop cluster。
- 截止 `signal_date` 的横截面排名。

不允许使用：

- `signal_date` 之后的 IV、RV、成交量或价格。
- 用完整样本均值/标准差做 zscore。
- 用未来是否止损、未来持有天数、未来收益参与因子计算。

### 8.2 Z-score 和分位数口径

第一版建议全部使用当日横截面标准化：

```text
z_t = (x_t - cross_section_mean_t) / cross_section_std_t
```

如果要做历史分位数，必须使用 rolling 或 expanding 历史窗口：

```text
rank_t = percentile(x_t within dates <= t)
```

不能用全样本回看分位数。

### 8.3 开仓价格口径

候选生成时不能使用未来成交结果决定是否进入候选。允许用统一约定的执行价格作为 shadow label 的开仓价，但这个价格必须对所有候选一致，并且不能影响候选是否入池。

推荐第一版：

```text
signal_date 生成候选
intended_open_date 按当前策略相同的开仓成交口径估计开仓价
若 intended_open_date 无有效价格，则该候选标记为 no_label，不进入 IC
```

## 9. 计算效率设计

全候选池 shadow label 会比真实组合回测更重。必须一开始就做性能设计。

### 9.1 两阶段标签

第一阶段使用日频预筛：

```text
用日内最高期权价或日频高点判断是否可能触发 2.5x 止损。
如果从未触及止损阈值，则不用进入分钟扫描。
```

第二阶段仅对可能触发止损的候选做分钟级确认：

```text
检查是否为瞬时跳价
检查价格是否快速回落
按当前止损确认逻辑确定真正 stop time 和 stop price
```

### 9.2 标签缓存

同一候选合约、同一开仓日、同一止损倍数、同一费用口径下，标签应该缓存：

```text
cache_key = signal_date + contract_code + open_price + stop_multiple + fee_model_version
```

后续新增因子时，不应重复计算未来标签。

### 9.3 分批落盘

建议按年份或月份分批：

```text
candidate_universe_2022.csv
candidate_universe_2023.csv
candidate_universe_2024.csv
candidate_universe_2025.csv
candidate_universe_2026.csv
```

标签也按同样分区落盘，最后再合并做分层检验。

## 10. 分层和 IC 检验

### 10.1 横截面 Rank IC

每天在同一层级做横截面 Rank IC：

```text
IC_t = corr(rank(factor_t), rank(future_label_t))
```

低值好的因子需要反向：

```text
friction_ratio_good = -friction_ratio
gamma_rent_penalty_good = -gamma_rent_penalty
vomma_loss_proxy_good = -vomma_loss_proxy
```

输出：

```text
mean_ic
ic_t_stat
positive_ic_rate
cum_ic
cum_ic_path
mean_sample_count
```

### 10.2 Q1-Q5 分层

每天按因子分 5 层，输出：

```text
layer_count
gross_premium
net_pnl
net_pnl_per_premium
retained_ratio
stop_rate
stop_loss_per_premium
expiry_rate
avg_days_held
vega_loss_per_premium
gamma_loss_per_premium
```

对低值好的因子，同时输出：

```text
good_minus_bad = low_risk_layer - high_risk_layer
```

不要只看 Q5 是否最高。卖权因子可能不是单调收益因子，而是“剔除最差层”的红线因子。

### 10.3 分组内 IC

为了避免品种差异主导合约级结论，合约级必须额外做组内 IC：

```text
within signal_date + product + option_type
```

它回答：

```text
在同一品种、同一方向、同一次月内，这个因子能否选出更好的执行价？
```

这是判断选腿因子的核心口径。

### 10.4 Partial IC

为处理因子共线性，第一版至少做两个 partial IC：

```text
factor residualized by liquidity/oi/friction
factor residualized by abs(delta)/premium/dte
```

示例：

```text
premium_to_iv10_loss_resid
= residual(premium_to_iv10_loss ~ friction_ratio + volume_rank + oi_rank + abs_delta + dte)
```

这样可以判断一个因子是否只是流动性、Delta 或权利金厚度的代理变量。

## 11. 环境切片

所有 IC 和分层必须至少按以下维度切片：

```text
year
option_type: Call / Put
vol_regime: falling / low / normal / high / post_stop
sector
corr_group
price_bucket
premium_bucket
```

B3 相关实验额外切：

```text
vov_high_and_rising
vov_high_and_falling
iv_shock_coverage_high_low
vomma_loss_high_low
```

关键解释规则：

```text
如果因子只在某一年有效，不进入主策略。
如果因子只在 Put 侧有效，只能做侧别规则，不能全市场统一使用。
如果因子在 high vol 中失效，但在 falling vol 中有效，应作为环境调制因子。
```

## 12. 判定标准

### 12.1 硬过滤因子

满足以下条件的因子可以考虑做硬过滤：

```text
最差层在 OOS 中 net_pnl_per_premium 明显低
最差层 stop_rate 明显高
剔除最差层后总权利金下降可控
剔除后 vega_loss_per_premium 或 gamma_loss_per_premium 改善
跨年份和 Put/Call 至少不矛盾
```

典型候选：

- `friction_ratio`
- 极低 `premium_to_iv10_loss`
- 极低 `premium_to_stress_loss`

### 12.2 合约选腿因子

满足以下条件的因子可以进入选腿排序：

```text
合约级 IC 显著
组内 IC 显著
高质量层 retained_ratio 更高
高质量层 stop_rate 不更差
不只是由流动性或 Delta 解释
```

典型候选：

- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `gamma_rent_penalty`
- `friction_ratio`

### 12.3 品种/方向预算因子

满足以下条件的因子可以进入预算倾斜：

```text
品种-方向级 IC 显著
累计 IC 稳定向上
分层经济价值为正
不会显著提高 stop_rate
在 OOS 和关键升波月份不崩
```

典型候选：

- `b2_product_score`
- `variance_carry`
- `theta_vega_efficiency`
- B3 的 forward vega、VOV 稳定/回落状态、IV shock coverage、vomma penalty

### 12.4 止损概率控制因子

满足以下条件的因子进入手数和铺单控制：

```text
对 stop_avoidance 或 stop_loss_avoidance 的 IC 显著
对平均收益不一定最强
但能降低 stop_rate 或 stop_loss_per_premium
```

典型候选：

- `theta_vega_efficiency`
- `gamma_rent_penalty`
- `premium_to_stress_loss`
- `premium_to_iv10_loss`

## 13. 样本内和样本外设计

第一版建议：

```text
样本内：2022-01-04 至 2024-12-31
样本外：2025-01-01 至 最新数据
```

原因：

- 2022-2024 用于发现因子结构。
- 2025 含有明显升波和关税冲击阶段，可检验尾部稳定性。
- 2026 当前样本较短，只作为延伸观察，不作为单独调参依据。

必须额外做滚动年度检查：

```text
2022
2023
2024
2025
2026 YTD
```

如果一个因子只在 2025 表现好，不能作为主规则，只能作为特定环境线索。

## 14. 输出文件

建议输出目录：

```text
output/candidate_factor_test_<base_tag>/
```

核心文件：

```text
candidate_contracts.parquet
candidate_product_side.parquet
shadow_labels_contract.parquet
shadow_labels_product_side.parquet
factor_ic_daily.csv
factor_ic_summary.csv
factor_partial_ic_summary.csv
factor_layer_daily.csv
factor_layer_summary.csv
factor_layer_env_summary.csv
factor_ic_env_summary.csv
factor_correlation.csv
factor_usage_recommendation.csv
candidate_factor_test_report.md
```

核心图表：

```text
01_product_side_cum_ic.png
02_contract_cum_ic.png
03_product_side_layer_heatmap.png
04_contract_layer_heatmap.png
05_stop_rate_by_layer.png
06_retained_ratio_by_layer.png
07_factor_correlation_heatmap.png
08_partial_ic_comparison.png
09_env_slice_ic.png
10_yearly_ic_stability.png
11_tail_month_layer_performance.png
12_usage_map_filter_leg_budget_stop.png
```

## 15. 实验分阶段

### 阶段 A：候选池落盘

目标：

```text
确认每天每个品种/方向有哪些候选，不经过 B2c 倾斜。
```

验收：

- 候选数量、品种数量、Call/Put 数量合理。
- 与 B1/B2c orders 对比，能解释真实成交样本只是候选池子集。
- 无未来字段混入。

### 阶段 B：Shadow label

目标：

```text
为每个候选生成统一 1 手未来标签。
```

验收：

- 随机抽查候选止损路径和到期结算正确。
- 未触发止损的候选无需分钟扫描。
- 标签和真实 orders 中同一合约同一开仓日的结果方向一致。

### 阶段 C：单因子检验

目标：

```text
在干净候选池上检验 B2c 子因子的 IC、分层和止损解释力。
```

验收：

- 同时输出合约级和品种-方向级。
- 同时输出 IC 和 Q1-Q5 分层。
- 同时输出 Put/Call、年份、vol regime 切片。

### 阶段 D：因子用途映射

目标：

```text
把因子分类成过滤、选腿、预算、止损概率、环境调制。
```

验收：

- 每个因子都有明确使用建议或暂不使用原因。
- 不允许只按 IC 高低排序。

### 阶段 E：回到组合实验

只有通过阶段 C/D 的因子，才进入下一轮组合回测：

```text
B2c_clean_filter
B2c_leg_rank_only
B2c_budget_slim
B2c_stop_control
B2c_candidate_validated_all
```

组合回测仍以 B2c 和 B1 作为策略净值对照，但因子有效性证明来自候选池实验。

## 16. 和当前 B2c 报告的关系

当前 B2c 分层报告应降级为：

```text
B2c 已成交样本的事后拆解和线索发现。
```

它的价值是提示哪些方向值得研究，例如：

- `friction_ratio` 可能应做红线或强降权。
- `premium_to_iv10_loss` 和 `premium_to_stress_loss` 可能更适合选腿。
- `theta_vega_efficiency` 可能更适合止损概率控制。
- `premium_quality_score` 综合分可能稀释强因子。

但这些结论必须经过候选池实验确认，才能升级成策略规则。

## 17. 本实验不能回答的问题

第一版候选池实验不能直接回答：

- 完整组合回测最终 CAGR 会提高多少。
- 组合风险预算如何设置最优。
- 高分层容量是否足够支撑真实资金规模。
- ETF 修复后是否同样有效。
- B3 因子在全周期完成后是否稳定。

这些需要在候选池因子检验后，再进入组合回测和容量检验。

## 18. 第一版推荐执行顺序

推荐按以下顺序落地：

1. 在策略引擎中新增 `dump_candidate_universe=true`，位置放在 B2c 倾斜和组合预算之前。
2. 先跑 2025-03 至 2025-06 小样本，验证候选数量、字段完整性和 shadow label 正确。
3. 跑 2022 至最新全样本候选池和标签。
4. 在候选池上复用并升级 `analyze_factor_layers.py`，加入 all-candidate、partial IC 和组内 IC。
5. 生成候选池因子检验报告。
6. 根据报告设计下一轮组合回测，而不是直接凭 B2c 已成交样本改规则。

## 19. 预期结论形式

最终报告应给出类似下面的结论，而不是只说“哪个因子 IC 高”：

```text
friction_ratio:
  全候选池 contract-level 和 product-side 都有效；
  最差层 stop_rate 高，retained_ratio 低；
  建议作为硬过滤或强降权，不作为预算放大因子。

premium_to_iv10_loss:
  contract-level 组内 IC 显著；
  product-side 效果次之；
  建议作为选腿排序和最低覆盖红线。

theta_vega_efficiency:
  对 stop_avoidance 更强，对收益标签中等；
  建议进入手数和铺单厚度控制。

variance_carry:
  product-side 有一定解释力，但 contract-level 不一定强；
  建议用于品种方向预算，不用于选腿。
```

这才是把 B2c 从“一个综合倾斜分”升级为“可解释卖权承保系统”的关键步骤。
