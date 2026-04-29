# S1 B4 实验设计：去共线因子库角色分层版

文档日期：2026-04-29  
策略对象：S1 日频低 Delta 卖权策略  
基准版本：`B1 liquidity/OI ranking stop25`，同时对照 `B2c product tilt 075` 与 `B3e clean vega composite`  
设计目标：把 full shadow 因子审计中“明显可用”的因子放到它该用的位置，同时去掉高度共线重复计票。

## 1. 实验定位

B4 不是继续堆一个更复杂的综合分，也不是把 B2/B3 中所有看起来有效的字段都加权进去。B4 的定位是：

```text
B4 = B1
   + 去共线后的硬过滤
   + 去共线后的合约排序
   + 去共线后的 product-side 轻度预算倾斜
   + 只作为惩罚项使用的环境/止损风险因子
```

核心思想是“因子各归各位”。  
如果一个因子只解释交易摩擦，就不能拿来做 alpha；如果一个因子只在 contract 层强，就不能拿来大幅倾斜品种预算；如果一个因子在 stop avoidance 上有价值但收益 IC 弱，就只能做风险惩罚，不能做正向加仓。

## 2. B4 要回答的问题

B4 要回答四个问题：

1. 去掉高度共线因子后，因子库是否仍能改善 B1 的权利金留存、保证金收益和回撤？
2. 把因子放到正确层级后，是否比 B2c/B3e 的“混合综合分”更稳健？
3. 合约排序因子是否能在不提高总保证金目标的情况下，提高 theta 留存并降低坏合约比例？
4. 环境/止损惩罚是否能降低 vega/gamma 吞噬和止损跳价，而不是简单砍掉好权利金？

## 3. 不变的基础规则

B4 继承 B1 的基础交易框架，不改变核心卖权结构：

| 模块 | 规则 |
| --- | --- |
| 品种池 | 全品种扫描，但沿用当前已经修复后的可交易品种口径。若 ETF 暂时因数据/费用/流动性口径不稳定，继续按当前 B1 版本屏蔽。 |
| 合约期限 | 只卖次月合约。商品合约按系统当前“次月/下一可交易到期”口径，不改期限选择。 |
| Delta | 卖方腿 `abs(delta) <= 0.10`。 |
| Put/Call | Put 和 Call 都参与，不做长期单边方向押注。 |
| 止损 | 开仓权利金的 `2.5x`，保留异常跳价确认逻辑。 |
| 止盈 | 不设盈利止盈，持有到期或止损退出。 |
| 保证金目标 | 总 S1 目标保证金仍按 B1/B0 基准，即约 `50% NAV`。 |
| 成本与保证金 | 使用当前已接入的真实手续费、保证金率和保证金公式。 |
| 执行 | T 日收盘计算信号，T+1 按既定执行口径开仓，禁止未来函数。 |

## 4. 去共线后的因子选择

### 4.1 保留代表因子

| 因子族群 | 保留因子 | 不再单独使用 | 使用原因 |
| --- | --- | --- | --- |
| 交易摩擦 | `friction_ratio_low` | `fee_ratio_low` | 二者相关性约 `1.000`，只保留一个做硬过滤。 |
| IV 冲击覆盖 | `premium_to_iv10_loss` | `b3_iv_shock_coverage` | 二者相关性约 `0.999`，保留解释更直接的 `premium_to_iv10_loss`。 |
| 压力损失覆盖 | `premium_to_stress_loss` | `b3_joint_stress_coverage` | 二者相关性约 `1.000`，保留 `premium_to_stress_loss`。 |
| Vega 凸性 | `b3_vomma_loss_ratio_low` | 无 | 与 IV shock coverage 高相关，但有独立的 convexity 风险解释，低权重保留。 |
| Gamma/Theta 权衡 | `gamma_rent_penalty_low` | 无 | 用来衡量 theta 是否值得承担 gamma 路径成本。 |
| 资本效率 | `premium_yield_margin` | `premium_yield_notional` | `premium_yield_margin` 更贴近资金效率；`premium_yield_notional` 分母敏感，暂不单独使用。 |
| 止损安全垫 | `breakeven_cushion_score` | 无 | 收益排序 IC 弱，但 stop avoidance 有价值，只做惩罚项。 |
| 环境稳定性 | `b3_vol_of_vol_proxy_low` | `b3_forward_variance_pressure_low` | `vol-of-vol` 用作环境惩罚；forward pressure 当前 IC 不支持正向使用。 |
| IV/RV Carry | 暂不使用 | `variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate` | 当前 full shadow product 层 IC 不支持进入 B4，后续重构 forward RV 标签后再测。 |
| 综合分 | 暂不使用 | `premium_quality_score` | 只作为对照和残差控制，不作为 B4 上线因子。 |

### 4.2 B4 不采用的因子

B4 明确不采用以下因子进入交易规则：

```text
fee_ratio_low
b3_iv_shock_coverage
b3_joint_stress_coverage
premium_yield_notional
b3_forward_variance_pressure_low
variance_carry
iv_rv_spread_candidate
iv_rv_ratio_candidate
premium_quality_score
```

不采用不代表理论上无效，而是当前审计结果下它们存在高共线、口径未验证或适用层级不清的问题。

## 5. 因子落地层级

### 5.0 品种筛选与预算倾斜的边界

B4 不是没有品种层动作，而是刻意区分“硬品种筛选”和“预算倾斜”：

```text
硬品种筛选：第一版不做。
预算倾斜：B4b/B4c 要做，而且是核心实验之一。
```

不做硬品种筛选的原因是，full shadow 审计显示当前 product 层和 product-side 层 IC 明显弱于 contract 层，且 IV/RV carry 类因子尚未通过验证。如果第一版直接用这些因子把某些品种从池子里剔除，容易把实验变成过拟合的黑名单，也会破坏 B0/B1 “全品种承保基准”的研究意义。

因此，B4 的品种层处理原则是：

```text
所有满足基础数据、上市观察期、流动性和可交易条件的品种仍进入扫描；
但不同 product + side 会根据当日承保质量拿到不同预算；
高分品种/方向多拿预算，低分品种/方向少拿预算；
低分不是永久剔除，而是当天承保额度降低。
```

B4 用于 product-side 预算倾斜的因子只保留去共线后的代表变量：

```text
premium_to_stress_loss      -> 压力损失覆盖
premium_to_iv10_loss        -> IV 冲击覆盖
premium_yield_margin        -> 资本效率
gamma_rent_penalty_low      -> gamma/theta 权衡
```

`b3_vol_of_vol_proxy_low` 和 `breakeven_cushion_score` 不用于正向加预算，只作为风险惩罚；`variance_carry`、`iv_rv_spread_candidate`、`iv_rv_ratio_candidate` 暂不进入 B4 品种预算，因为当前 product 层 IC 不支持直接采用。

### 5.1 硬过滤层

硬过滤只处理“明显不值得交易”的候选，不用于加分。

```text
hard_filter_pass =
    entry_price >= 0.5
    and open_premium_cash >= 100
    and friction_ratio <= 0.10
    and liquidity/OI pass
```

说明：

- `entry_price >= 0.5` 沿用 B1 已恢复的低价过滤。
- `open_premium_cash >= 100` 来自 full shadow 审计，低权利金会严重扭曲比例收益和止损统计。
- `friction_ratio <= 0.10` 是交易摩擦卫生条件，不是收益因子。
- 如果某些品种因合约乘数小导致 `open_premium_cash >= 100` 过严，可以在诊断中记录，但 B4 第一版不放宽。

### 5.2 合约排序层

合约排序是 B4 最核心、也最干净的落地层级。它回答：

```text
同一交易日、同一品种、同一 Put/Call 方向、同一次月到期中，
应该优先卖哪几个低 Delta 合约？
```

B4 合约排序分数：

```text
contract_quality_score =
    30% * rank(premium_to_iv10_loss)
  + 25% * rank(premium_to_stress_loss)
  + 20% * rank(premium_yield_margin)
  + 15% * rank(gamma_rent_penalty_low)
  + 10% * rank(b3_vomma_loss_ratio_low)
  - stop_safety_penalty
  - vov_penalty
```

其中：

```text
stop_safety_penalty =
    0.15 * max(0, 50 - rank(breakeven_cushion_score)) / 50

vov_penalty =
    0.10 * max(0, 50 - rank(b3_vol_of_vol_proxy_low)) / 50
```

解释：

- `premium_to_iv10_loss` 代表短 vega 的权利金覆盖。
- `premium_to_stress_loss` 代表 spot/IV 联合压力覆盖。
- `premium_yield_margin` 代表资本效率，但不让它单独主导。
- `gamma_rent_penalty_low` 约束短 gamma 租金。
- `b3_vomma_loss_ratio_low` 只低权重保留，避免和 IV shock coverage 重复计票。
- `breakeven_cushion_score` 不正向奖励，只惩罚安全垫太薄。
- `b3_vol_of_vol_proxy_low` 不正向奖励，只惩罚波动自身不稳定。

合约选择规则：

```text
先通过硬过滤；
再在同 product + side + expiry 内按 contract_quality_score 排序；
每侧最多保留 N 个合约；
N 沿用当前 B1 设置，若当前为每侧最多 5 个，则 B4 第一版也为 5。
```

### 5.3 Product-side 预算倾斜层

Product-side 预算倾斜回答：

```text
今天某个品种的 Put 侧或 Call 侧，是否应该比等权获得更多预算？
```

B4 不再使用 B2c 旧版综合分，而是使用去共线代表因子聚合：

```text
product_side_quality_score =
    35% * agg_rank(premium_to_stress_loss)
  + 30% * agg_rank(premium_to_iv10_loss)
  + 20% * agg_rank(premium_yield_margin)
  + 15% * agg_rank(gamma_rent_penalty_low)
```

聚合方式：

```text
agg_rank(factor) =
    liquidity_weighted_mean(top ranked contracts within product + side)
```

其中 top contracts 来自 B4 合约排序后的候选，而不是全链任意合约。

预算倾斜公式：

```text
raw_quality_weight_i_s =
    floor_weight + (product_side_quality_score_i_s / 100) ^ power

quality_side_budget_i_s =
    total_s1_budget * raw_quality_weight_i_s / sum(raw_quality_weight_all_sides)

final_side_budget_i_s =
    (1 - tilt_strength) * equal_side_budget_i_s
  + tilt_strength * quality_side_budget_i_s
```

第一版参数：

```text
floor_weight = 0.50
power = 1.25
tilt_strength = 0.35
score_clip = [5, 95]
```

为什么 `tilt_strength` 不直接用 B2c 的 `0.75`：

- B4 已经同时改变了合约排序和预算倾斜，不能一开始倾斜过重。
- full shadow 审计显示很多因子残差 IC 收缩，说明应把它们视为承保质量修正，而不是强 alpha。
- 0.35 足够观察预算是否向高质量品种迁移，又不至于把结果变成集中度实验。

### 5.4 环境/止损惩罚层

B4 不使用 `b3_vol_of_vol_proxy_low` 正向加预算，只作为惩罚：

```text
if product_side_vov_rank < 30:
    side_budget_multiplier *= 0.85

if product_side_vov_rank < 15:
    side_budget_multiplier *= 0.70
```

说明：

- 低 vol-of-vol 不代表一定赚钱，只代表环境较稳定。
- 高 vol-of-vol 往往对应 IV 跳动、止损和流动性折价风险，应降预算。
- 这和“降波环境多卖”不冲突：B4 第一版还没有把 falling vol 做成正向加仓因子，只先把不稳定升波环境惩罚掉。

`breakeven_cushion_score` 同样只用于惩罚：

```text
if contract_breakeven_cushion_rank < 30:
    contract_quality_score -= 0.10

if contract_breakeven_cushion_rank < 15:
    contract_quality_score -= 0.20
```

## 6. B4 实验组设计

B4 需要分成三组跑，避免一次混入太多变化后无法归因。

| 实验 | 定义 | 目的 |
| --- | --- | --- |
| B4a | B1 + 硬过滤 + 去共线合约排序，不做 product-side 预算倾斜 | 验证因子最干净的应用场景：同品种同方向内选合约。 |
| B4b | B4a + 去共线 product-side 轻度预算倾斜，`tilt_strength = 0.35` | 验证品种/方向层预算倾斜是否有增量。 |
| B4c | B4b + vol-of-vol 与 breakeven cushion 惩罚 | 验证环境和止损惩罚能否降低 vega/gamma 吞噬与止损跳价。 |

正式候选主线：

```text
B4_main = B4c
```

但报告必须同时展示 B4a/B4b/B4c，只有 B4c 优于 B4b，才说明惩罚层有价值；只有 B4b 优于 B4a，才说明 product-side 预算倾斜有价值；如果 B4a 已经改善明显，说明真正有效的可能只是合约排序。

## 7. 对照组

B4 必须至少对照三条线：

| 对照 | 用途 |
| --- | --- |
| B1 | 流动性/OI 排序基准，是 B4 的直接增量基准。 |
| B2c | 旧版权利金质量 product tilt 强倾斜基准，用来判断去共线后是否减少过拟合。 |
| B3e | clean vega composite 基准，用来判断 B4 的角色分层是否优于混合综合分。 |

## 8. 回测区间

建议两阶段：

```text
阶段 1：2025-01-01 至 2025-08-31
目的：包含 2025 年 4 月关税冲击和 7 月升波窗口，用于快速 sanity check。

阶段 2：2022-01-04 至数据库可用最新日
目的：正式评估全周期表现、年份稳定性和品种上市后的动态扫描。
```

如果资源允许，可以直接跑阶段 2；但报告中仍需单独拆 2025-01 至 2025-08。

## 9. 成功标准

B4 不能只看收益率。成功标准按优先级排序：

1. 相对 B1，`premium retained ratio` 提高。
2. 相对 B1，`PnL / margin` 或 CAGR 提高。
3. 相对 B2c，`vega_loss / gross_open_premium` 不扩大，最好下降。
4. 相对 B2c，最大回撤不扩大，最差单日不恶化。
5. 相对 B3e，权利金收入不能明显被砍掉。
6. Stop 次数和 stop overshoot 不增加。
7. P/C 结构没有不可解释的长期单边偏移。
8. 品种和板块集中度没有显著恶化。
9. 累计超额路径不是只来自少数月份或少数品种。

如果 B4 收益提高但 vega 亏损也同步扩大，则不能视为成功，只能说明它追到了更厚但更危险的权利金。

## 10. 必须输出的诊断

B4 必须新增或确认以下输出：

```text
daily_contract_quality_score.csv
daily_product_side_quality_score.csv
daily_product_side_budget.csv
selected_contract_factor_snapshot.csv
factor_family_budget_contribution.csv
contract_quality_quintile_performance.csv
product_side_quality_quintile_performance.csv
vov_penalty_diagnostics.csv
breakeven_cushion_penalty_diagnostics.csv
```

报告中必须展示：

- NAV、最大回撤、保证金使用率。
- 毛开仓权利金、平仓后留存权利金、留存率。
- Theta / Vega / Gamma / Delta / Residual 归因。
- Vega loss / gross premium。
- Gamma loss / gross premium。
- Stop 次数、stop loss、stop overshoot。
- 合约质量分 Q1-Q5 分层表现。
- Product-side 质量分 Q1-Q5 分层表现。
- P/C 结构变化。
- 品种 Top10 暴露变化。
- 板块集中度变化。
- B4a/B4b/B4c 相对 B1/B2c/B3e 的超额路径。

## 11. 未来函数控制

B4 因子只允许使用 T 日及以前可见信息：

```text
T 日期权收盘价、IV、Greeks、成交量、持仓量；
T 日真实标的价格；
截至 T 日的历史 IV/RV/vol-of-vol；
T 日横截面 rank；
T 日交易所手续费和保证金参数。
```

禁止使用：

```text
T+1 之后的成交量、持仓量、IV、RV、价格路径；
未来是否止损；
未来权利金留存；
全样本均值、全样本标准差、全样本 zscore；
未来高低点用于当前止损风险判断。
```

如果使用历史 zscore，必须写成：

```text
z_t = (x_t - rolling_mean(x_{t-lookback : t-1}))
    / rolling_std(x_{t-lookback : t-1})
```

第一版 B4 优先使用 T 日横截面 rank，降低未来函数风险和实现复杂度。

## 12. 参数文件建议

建议新增三份配置：

```text
config_s1_baseline_b4a_dedup_contract_rank_stop25.json
config_s1_baseline_b4b_dedup_contract_product_tilt_stop25.json
config_s1_baseline_b4c_dedup_role_layer_stop25.json
```

建议 tag：

```text
s1_b4a_dedup_contract_rank_stop25_allprod_2022_latest
s1_b4b_dedup_contract_product_tilt_stop25_allprod_2022_latest
s1_b4c_dedup_role_layer_stop25_allprod_2022_latest
```

核心参数建议：

```json
{
  "s1_b4_factor_role_enabled": true,
  "s1_b4_hard_filter_enabled": true,
  "s1_b4_contract_rank_enabled": true,
  "s1_b4_product_side_tilt_enabled": true,
  "s1_b4_vov_penalty_enabled": true,
  "s1_b4_breakeven_penalty_enabled": true,
  "s1_b4_min_net_premium_cash": 100,
  "s1_b4_max_friction_ratio": 0.10,
  "s1_b4_product_tilt_strength": 0.35,
  "s1_b4_floor_weight": 0.50,
  "s1_b4_power": 1.25,
  "s1_b4_score_clip_low": 5,
  "s1_b4_score_clip_high": 95
}
```

B4a 关闭：

```json
{
  "s1_b4_product_side_tilt_enabled": false,
  "s1_b4_vov_penalty_enabled": false,
  "s1_b4_breakeven_penalty_enabled": false
}
```

B4b 关闭：

```json
{
  "s1_b4_vov_penalty_enabled": false,
  "s1_b4_breakeven_penalty_enabled": false
}
```

B4c 全部开启。

### 12.1 已落地配置

本轮已经按上述口径落地三份配置：

| 实验 | 配置文件 | 关键差异 |
| --- | --- | --- |
| B4a | `config_s1_baseline_b4a_dedup_contract_rank_stop25.json` | `B1 + hard filter + B4 contract ranking`，不做 product-side 预算倾斜。 |
| B4b | `config_s1_baseline_b4b_dedup_contract_product_tilt_stop25.json` | 在 B4a 基础上开启 product-side 轻度预算倾斜，`tilt_strength = 0.35`。 |
| B4c | `config_s1_baseline_b4c_dedup_role_layer_stop25.json` | 在 B4b 基础上开启 breakeven 与 vol-of-vol 惩罚。 |

本轮三个实验均使用：

```text
--start-date 2022-01-04
--end-date 不指定，即跑到数据当前最后交易日
```

## 13. 预期结果与失败解释

### 13.1 如果 B4a 改善、B4b/B4c 不改善

说明因子最有效的位置是合约排序，而不是品种预算。后续应先把 contract ranking 做稳，不急着做预算倾斜。

### 13.2 如果 B4b 改善、B4c 不改善

说明 product-side 预算倾斜有效，但当前 `vol-of-vol` 和 `breakeven cushion` 惩罚过强或方向不稳。后续需要调低惩罚权重，而不是否定 product-side quality。

### 13.3 如果 B4c 改善最明显

说明角色分层成立：收益因子负责收权利金，风险因子负责压尾部。这个结果最理想，可以把 B4c 作为下一版主线候选。

### 13.4 如果 B4 收益更高但 vega 更差

说明排序仍然在追更厚的 short vega，而不是更干净的 theta。后续要提高 `premium_to_iv10_loss`、`b3_vomma_loss_ratio_low` 与 `vol-of-vol` 惩罚权重，降低 `premium_yield_margin` 权重。

### 13.5 如果 B4 收益更低但回撤明显更好

不直接否定。需要看 Calmar、Sortino、vega loss/gross premium 和 stop overshoot。如果风险收益比改善，可以作为低风险版本保留。

## 14. 一句话定义

B4 是 S1 因子库的第一版真正落地实验：

```text
它不再把所有高 IC 因子混成一个黑箱分数，
而是把交易摩擦用于硬过滤，
把 IV/stress/gamma/premium 因子用于合约排序，
把去共线后的承保质量用于轻度 product-side 预算倾斜，
把 vol-of-vol 和安全垫用于风险惩罚，
目标是在保留 B2c 权利金增厚的同时，
减少 vega/gamma 对 theta 的吞噬。
```
