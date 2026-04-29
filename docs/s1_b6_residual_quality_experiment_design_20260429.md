# S1 B6 残差质量因子交易实验设计

生成日期：2026-04-29  
适用策略：S1 日频低 Delta 纯卖权策略  
上游依据：`s1_factor_library_usage_decision_20260429.md`、B2/B3/B5 corrected full shadow、B6 product / product-side residual IC 审计  
本文定位：把已经审计过的因子库，转化为可回测的 B6 交易实验，而不是继续做单因子检验。

## 1. 核心目标

B6 不是单纯优化参数，也不是把 B2/B3/B5 的所有因子堆成一个更复杂的综合分。B6 要回答一个更具体的问题：

```text
在 B1 可交易基准之上，
如果只使用经过同分母控制和残差 IC 审计后仍有解释力的质量因子，
能否提高权利金留存、降低 vega/gamma/stop 损耗，
同时不显著牺牲可交易权利金池？
```

统一收益拆解仍然是：

```text
S1 net return
= Premium Pool
* Deployment Ratio
* Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

B6 的直接目标不是马上追求最高 NAV，而是让策略从“能收到权利金”升级为“能收到更干净、更容易留住的权利金”。

## 2. B6 相对 B1 的不变项

B6 的所有实验均以 B1 为基准，不改变以下口径：

| 项目 | B6 保持方式 |
| --- | --- |
| 策略结构 | 纯卖权，仍然只做 S1，不加入保护腿、价差或 ratio |
| 合约期限 | 仍然只交易次月合约，商品期权按当前系统的次月/主交易到期逻辑 |
| Delta 约束 | 硬约束 `abs(delta) < 0.1` 不放松 |
| 止损 | 权利金 2.5x 硬止损，并保留异常价 / 跳价保护逻辑 |
| 低价合约 | 保留 B1 的最低价格过滤，避免低价 tick 噪声污染 |
| ETF | 暂按当前 B1 口径剔除 ETF，等 ETF 合约乘数、费用和价格口径单独修正后再恢复 |
| 手续费与保证金 | 使用已修正的交易所 / 期货公司口径 |
| 组合复杂风控 | 不新增 Tail-HRP、板块集中、到期聚集等 B7 组合层规则 |
| 回测区间 | 与 B1 / B2C / B4 可比，主跑 2022 至最新可用日，并输出逐年和关键压力期切片 |

这点很重要：B6 只验证“因子归位后是否改善交易质量”，不同时混入新的组合风控和容量约束。

## 3. B6 的因子使用原则

### 3.1 不再使用 raw premium/margin 做大幅预算 alpha

B6 residual IC 审计后，`product_premium_to_margin` 的 raw IC 很高，但在 full denominator 残差控制后几乎消失，说明它很大程度上只是告诉我们“哪里权利金池更厚”，而不一定说明“这个品种更会赚钱”。

因此：

```text
premium/margin 可以帮助识别 Premium Pool；
但不能单独决定品种大幅加预算。
```

### 3.2 核心升级为 theta/vega 和 theta/gamma 质量

残差 IC 后最值得保留的是：

```text
product_theta_per_vega
side_theta_per_vega
side_theta_per_gamma
product_theta_per_gamma
```

这些因子更接近卖权的真实问题：

```text
我为了收这份 theta，到底承担了多少 vega 和 gamma？
```

它们主要改善：

```text
Retention Rate
Tail / Stop Loss
vega 损耗
gamma 路径亏损
```

### 3.3 P/C 侧比纯品种层更值得优先尝试

B6 residual IC 显示，product-side 层的信号更清楚：

```text
side_theta_per_vega
side_premium_to_stress
side_theta_per_gamma
```

比纯 product 层更适合先做预算倾斜。原因是 S1 的风险很多时候不是“某个品种该不该做”，而是：

```text
这个品种今天更适合卖 Put，还是卖 Call？
```

### 3.4 组合层暂不并入 B6

以下因子先不进入 B6 交易规则，而进入 B7：

```text
tail_dependence
stop_cluster_score
sector_stress_exposure
expiry_cluster_score
effective_product_count
top_stress_share
margin_shock_ratio
Tail-HRP
```

B6 可以输出这些诊断，但不让它们参与交易，避免把“因子有效性”和“组合约束有效性”混在一起。

## 4. 第一批 B6 实验

第一批建议先跑 3 条线，分别回答三个层级问题。

### 4.1 B6a：合约层残差质量排序

实验问题：

```text
在同一品种、同一方向、同一次月里，
如果减少 raw premium/margin 权重、提高 theta/vega 和 stress coverage 权重，
能否提高权利金留存并降低 vega/gamma 损耗？
```

只改合约排序，不改品种预算，不改 P/C 侧预算。

建议合约层评分：

| 因子 | 权重 | 方向 | 改善公式变量 | 说明 |
| --- | ---: | --- | --- | --- |
| `premium_to_stress_loss` | 0.24 | 高更好 | Retention / Tail | 权利金对 spot+IV 压力亏损的覆盖 |
| `premium_to_iv10_loss` | 0.22 | 高更好 | Retention / Vega | 权利金对 10 vol IV shock 的覆盖 |
| `b5_theta_per_vega` | 0.22 | 高更好 | Retention / Vega | 单位 vega 的 theta 质量 |
| `b5_theta_per_gamma` | 0.12 | 高更好 | Retention / Gamma | 单位 gamma 的 theta 质量 |
| `b5_premium_to_tail_move_loss` | 0.10 | 高更好 | Tail / Stop Loss | 权利金对历史不利尾部移动的覆盖 |
| `b3_vomma_loss_ratio` | 0.06 | 低更好 | Tail / Stop Loss | IV shock 凸性惩罚 |
| `premium_yield_margin` | 0.04 | 高更好 | Deployment Ratio | 只保留轻微资本效率影响 |

硬过滤仍按 B1：

```text
最低期权价格
净权利金覆盖手续费
friction_ratio
成交量 / 持仓量基础可交易性
delta < 0.1
```

评价重点：

```text
Opened Premium / NAV 是否显著下降；
Retention Rate 是否提高；
Vega PnL 是否改善；
Stop count 和 stop loss 是否下降；
Theta / Vega、Theta / Gamma 暴露质量是否变好。
```

### 4.2 B6b：P/C 侧残差质量预算倾斜

实验问题：

```text
在 B6a 的合约排序基础上，
如果按 product-side 层质量因子倾斜 Put/Call 预算，
能否减少卖错方向导致的 stop 和 vega/gamma 损耗？
```

只改同一品种内部 Put / Call 侧预算，不改品种总预算。

建议 P/C 侧评分分成两层。

第一层是 residual quality：

| 因子 | 权重 | 方向 | 用途 |
| --- | ---: | --- | --- |
| `side_theta_per_vega` | 0.35 | 高更好 | 侧向 theta/vega 质量 |
| `side_premium_to_stress` | 0.25 | 高更好 | 侧向压力覆盖 |
| `side_theta_per_gamma` | 0.15 | 高更好 | 侧向 gamma 质量 |
| `side_premium_to_margin` | 0.10 | 高更好 | 只做轻度权利金池确认 |
| `side_vega_per_premium_low` | 0.10 | 高更好 | vega 风险轻惩罚 |
| `side_gamma_per_premium_low` | 0.05 | 高更好 | gamma 风险轻惩罚 |

第二层是方向风险闸门，不直接加 alpha，只做惩罚或上限：

| 因子 | 规则 |
| --- | --- |
| `b5_trend_z_20d` | 上涨趋势降低 Call 侧上限，下跌趋势降低 Put 侧上限 |
| `b5_breakout_distance_up_60d` | 接近上突破时压低 Call 侧预算 |
| `b5_breakout_distance_down_60d` | 接近下突破时压低 Put 侧预算 |
| `b3_skew_steepening` | 对应侧 skew 变陡时降低该侧预算 |
| `b5_cooldown_penalty_score` | 同品种同方向止损后降低该侧预算 |
| `b5_cooldown_release_score` | IV/RV/skew 回落后才释放冷静期 |

预算倾斜强度建议：

```text
side multiplier clip: 0.70 - 1.30
side tilt strength: 0.25
side floor weight: 0.70
```

评价重点：

```text
P/C 结构是否更稳定，而不是长期单边偏 Put 或偏 Call；
同方向 stop rate 是否下降；
side-level retention 是否提高；
Put / Call 各自 vega 和 gamma 损耗是否改善。
```

### 4.3 B6c：品种层轻量残差质量预算倾斜

实验问题：

```text
在 B6a 的合约排序基础上，
只对品种总预算做轻量倾斜，能否在不引入明显集中风险的前提下提高收益质量？
```

只改 product 总预算，不改 P/C 侧预算。

建议品种层评分：

| 因子 | 权重 | 方向 | 改善公式变量 | 说明 |
| --- | ---: | --- | --- | --- |
| `product_theta_per_vega` | 0.45 | 高更好 | Retention / Vega | B6 residual 后最干净的品种层质量因子 |
| `product_premium_to_stress` | 0.20 | 高更好 | Tail / Stop Loss | 只做质量辅助，不单独加预算 |
| `product_theta_per_gamma` | 0.15 | 高更好 | Retention / Gamma | gamma 质量辅助 |
| `product_tail_beta_abs_max_low` | 0.10 | 高更好 | Tail / Stop Loss | 尾部 beta 风险惩罚 |
| `product_gamma_per_premium_low` | 0.10 | 高更好 | Tail / Stop Loss | gamma / premium 风险辅助 |

明确不纳入主评分：

```text
product_premium_to_margin
product_vega_per_premium_low
variance_carry
iv_rv_spread_candidate
```

其中 `product_premium_to_margin` 只输出诊断，用来解释 Premium Pool 在哪里；不直接加预算。

预算倾斜强度建议：

```text
product multiplier clip: 0.80 - 1.20
product tilt strength: 0.15
product floor weight: 0.80
```

评价重点：

```text
是否提高 NAV / Calmar；
是否没有显著降低 effective product count；
Top 5 品种 premium / stress / margin 占比是否没有恶化；
是否没有把预算集中到白糖、玻璃等高权利金但高尾损品种。
```

## 5. 第二批候选实验

第一批结果清楚后，再考虑组合版，不建议一开始就全开。

### 5.1 B6d：合约 + P/C + 品种轻量组合版

实验问题：

```text
B6a、B6b、B6c 的有效部分合并后，是否可以成为下一版主线？
```

组合方式：

```text
B6a contract score
+ B6b side tilt，强度从 0.25 降到 0.20
+ B6c product tilt，强度维持 0.15
+ cooldown / trend / skew 只做惩罚，不做正向加预算
```

通过标准：

```text
相对 B1 和 B2C：
NAV 更高或至少不低；
最大回撤不扩大；
vega PnL 改善；
Retention Rate 提升；
Stop loss / opened premium 下降；
Premium Pool 损失不超过 15%。
```

### 5.2 B6e：防守版质量因子

实验问题：

```text
如果我们不追求更多权利金，只追求减少 stop 和 tail loss，
质量因子是否能显著改善回撤和 vega 损耗？
```

规则：

```text
不使用 premium_yield_margin；
side_premium_to_margin 权重降为 0；
提高 theta_per_vega、premium_to_stress、tail beta、cooldown 惩罚；
product multiplier clip 收窄到 0.85 - 1.15；
side multiplier clip 收窄到 0.75 - 1.25。
```

这条线不一定 NAV 最高，但可以检验 B6 因子是否真的有风控价值。

## 6. 实现要求

### 6.1 需要新增的配置命名

建议配置文件：

```text
config_s1_baseline_b6a_residual_contract_quality_stop25.json
config_s1_baseline_b6b_residual_side_tilt_stop25.json
config_s1_baseline_b6c_residual_product_tilt_stop25.json
config_s1_baseline_b6d_residual_quality_combo_stop25.json
config_s1_baseline_b6e_residual_defensive_stop25.json
```

建议 run tag：

```text
s1_b6a_residual_contract_quality_2022_latest
s1_b6b_residual_side_tilt_2022_latest
s1_b6c_residual_product_tilt_2022_latest
s1_b6d_residual_quality_combo_2022_latest
s1_b6e_residual_defensive_2022_latest
```

### 6.2 需要新增的代码能力

当前 B4 代码可以复用部分框架，但还不完全符合 B6 的残差质量结论。B6 实现应新增独立模式：

```text
s1_ranking_mode = "b6_residual_quality"
```

并新增参数组：

```text
s1_b6_contract_rank_enabled
s1_b6_side_tilt_enabled
s1_b6_product_tilt_enabled
s1_b6_contract_weights
s1_b6_side_weights
s1_b6_product_weights
s1_b6_side_tilt_strength
s1_b6_product_tilt_strength
s1_b6_side_floor_weight
s1_b6_product_floor_weight
s1_b6_missing_factor_score
```

字段缺失时不能直接删除候选，应按中性分处理：

```text
missing factor score = 50
```

原因是很多新品种或短历史品种在上市初期会缺少足够历史，不应因缺字段形成幸存者偏差。

### 6.3 不允许引入未来函数

B6 的所有横截面 rank / z-score 必须只使用当日候选池：

```text
signal_date 当日横截面排序可以使用；
rolling history 只能使用 signal_date 之前已经可见的数据；
shadow outcome、future pnl、future stop、future retention 只能用于报告，不能进入交易。
```

特别注意：

```text
product_theta_per_vega / side_theta_per_vega
只能由当日候选 Greeks 聚合；
product/side stop count 只能来自此前已发生止损；
cooldown release 只能看当前可见 IV/RV/skew 是否回落；
不能使用未来持仓表现来决定当天预算。
```

## 7. 输出与评估

B6 每条实验必须与 B1、B2C、B4a/B4b/B4c 做同截止日对比。

### 7.1 绩效指标

```text
NAV
CAGR / Annual Return
Max Drawdown
Sharpe
Calmar
Worst 1d / 5d PnL
Monthly win rate
```

### 7.2 收益公式拆解

必须输出：

```text
Available Premium Pool / NAV
Opened Premium / NAV
Deployment Ratio
Realized Retention Rate
Stop Loss / Opened Premium
Cost / Opened Premium
Tail Loss / Opened Premium
```

这部分是判断 B6 的核心。如果 B6 NAV 变好但只是因为开了更多权利金，而 retention 和 tail 变差，则不能认为 B6 成功。

### 7.3 Greeks 与卖方质量指标

必须输出：

```text
Theta PnL
Vega PnL
Gamma PnL
Residual PnL
cash theta / NAV
cash vega / NAV
cash gamma / NAV
theta / cash vega
theta / cash gamma
premium / 10 vol IV shock loss
premium / stress loss
```

策略目标新增一条：

```text
Vega PnL 应尽量为正，至少不能因为 B6 放大预算而恶化。
```

### 7.4 结构诊断

必须输出：

```text
P/C premium share
P/C margin share
P/C stop count
P/C vega and gamma loss
Top 10 product premium share
Top 10 product stress share
Top 10 product PnL contribution
effective product count
product-side budget multiplier distribution
```

### 7.5 稳健性切片

至少拆：

```text
逐年：2022 / 2023 / 2024 / 2025 / 2026 available
2025-01 至 2025-08
2025-04 tariff shock
2025-07 vol-up regime
降波环境
升波环境
高 vol-of-vol 环境
止损 cluster 窗口
```

## 8. 判断标准

### 8.1 可进入下一轮主线的条件

B6d 只有同时满足以下条件，才能进入下一轮主线：

```text
相对 B1：
年化收益提升；
最大回撤不扩大；
Calmar 提升；
Vega PnL 改善；
Retention Rate 提升；
Stop Loss / Opened Premium 不恶化；
Premium Pool 损失不超过 15%；
Top 5 品种 stress share 不显著恶化。
```

### 8.2 需要否决的情况

即使 NAV 变好，出现以下情况也不能直接采纳：

```text
收益主要来自 raw premium/margin 放大；
P/C 长期单边偏 Put 或偏 Call；
vega PnL 更负；
stop loss / opened premium 更高；
有效品种数明显下降；
收益只集中在一个年份或一个板块；
关键压力期回撤显著扩大。
```

### 8.3 如果 B6a 好、B6b/B6c 不好

说明因子更适合做合约层排序，而不适合做预算倾斜。下一步应该：

```text
保留 B6a；
暂停 product / side budget；
把 P/C 和 product 方向继续留在 full shadow；
进入 B7 组合层风控实验。
```

### 8.4 如果 B6b 好、B6c 不好

说明 product-side 比 product 更有交易意义。下一步应该：

```text
保留 P/C 侧预算；
品种层只做诊断和轻量上限；
不要直接按品种 raw premium/margin 加仓。
```

### 8.5 如果 B6c 好但集中度恶化

说明品种因子有收益，但需要 B7 组合层接管。下一步应该：

```text
把 B6c 作为 alpha input；
用 Tail-HRP / sector stress / expiry cluster 约束预算；
不允许单独上线。
```

## 9. 建议执行顺序

第一阶段先跑：

```text
B6a
B6b
B6c
```

如果 B6a 明显不如 B1，则暂不跑 B6d/B6e，先回到合约层因子设计。

如果 B6a 有效，再跑：

```text
B6d
B6e
```

最后用报告回答：

```text
B6 到底改善了公式里的哪一项？
是扩大了 Premium Pool？
提高了 Deployment Ratio？
提高了 Retention Rate？
降低了 Tail / Stop Loss？
还是只是增加了隐含风险？
```

这是 B6 能否进入 S1 下一版主线的唯一判断框架。

