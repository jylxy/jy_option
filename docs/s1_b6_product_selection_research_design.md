# S1 B6-product 品种筛选专题研究设计

生成日期：2026-04-29  
研究对象：S1 日频低 Delta 纯卖权策略  
输入样本：B5 full shadow 候选池、product panel、product-side panel、shadow outcome  
目标：建立一套专门服务于“品种筛选 / 品种预算倾斜”的因子检验体系。

## 1. 为什么要单独研究品种筛选

S1 当前已经有一批较强的合约层因子，例如 `premium_to_iv10_loss`、`premium_to_stress_loss`、`theta_per_vega`、`theta_per_gamma`。它们回答的是：

```text
同一品种、同一方向、同一次月中，哪张合约更值得卖？
```

但这并不等于已经解决了品种筛选。品种筛选要回答的是：

```text
今天哪些品种应该获得更多 S1 风险预算？
哪些品种只观察？
哪些品种即使有合约可卖，也不应该多做？
```

这两个问题的经济含义不同。合约层因子如果直接拿来做品种预算，容易把“某个品种里有几张漂亮的合约”误判成“这个品种整体值得重仓”。卖权策略的核心风险往往不是单张腿，而是某些品种、板块、方向或到期在同一 regime 中一起亏。

因此 B6-product 的原则是：

1. 品种层和合约层分开检验。
2. 品种层因子只服务于 `Deployment Ratio` 和品种预算，不参与同品种内部合约排序。
3. P/C 侧因子只服务于同品种 Put / Call 预算，不参与全品种排序。
4. 组合层因子如 tail correlation、stop cluster、expiry cluster 不用普通合约 IC 评价，后续进入 B7。

## 2. 收益公式定位

B6-product 仍然围绕 S1 收益拆解公式：

```text
S1 收益
= Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

品种筛选主要改善三项：

| 公式变量 | 品种层问题 | 典型因子 |
| --- | --- | --- |
| Premium Pool | 这个品种每天是否有足够可交易权利金池？ | `product_premium_sum`、`product_candidate_count`、`side_premium_sum` |
| Deployment Ratio | 这个品种是否值得多分预算？ | `product_premium_to_margin`、`product_premium_to_stress`、`product_iv_reversion` |
| Retention Rate | 这个品种收进来的权利金能否留下来？ | `future_retained_ratio`、`future_pnl_per_margin`、`future_stop_avoidance` |
| Tail / Stop Loss | 这个品种是否容易跳价、止损、尾部聚集？ | `product_stop_count_20d`、`tail_dependence`、`range_expansion`、`vol_of_vol` |
| Cost / Slippage | 这个品种是否有真实可执行容量？ | `candidate_count`、`low_price/tick`、未来扩展 `exit_capacity` |

## 3. 决策层级

### 3.1 Product 层

样本单位：

```text
signal_date + product
```

回答问题：

```text
今天这个品种是否值得获得更多预算？
```

标签：

- `future_pnl_per_margin`
- `future_pnl_per_premium`
- `future_retained_ratio`
- `future_stop_avoidance`
- `future_stop_loss_avoidance`
- `future_pnl_per_stress`

### 3.2 Product-side 层

样本单位：

```text
signal_date + product + option_type
```

回答问题：

```text
今天这个品种更适合卖 Put、卖 Call，还是双边都卖？
```

标签：

- `future_pnl_per_margin`
- `future_pnl_per_premium`
- `future_retained_ratio`
- `future_stop_avoidance`
- `future_stop_loss_avoidance`
- `future_pnl_per_stress`

### 3.3 暂不做的层级

组合层如 `tail_dependence`、`stop_cluster`、`sector_stress`、`expiry_cluster` 很重要，但它们回答的是：

```text
这些品种能不能一起卖？
```

这属于 B7 组合预算问题，B6-product 只先做 product / product-side 横截面检验。

## 4. 因子设计

### 4.1 Product 层候选因子

| 因子 | 方向 | 用途 | 说明 |
| --- | --- | --- | --- |
| `product_premium_sum` | 高更好 | Premium Pool | 品种权利金池厚度。 |
| `product_candidate_count` | 高更好 | Premium Pool / liquidity | 可交易候选数量。 |
| `product_premium_to_margin` | 高更好 | Deployment Ratio | 权利金 / 保证金。 |
| `product_premium_to_stress` | 高更好 | Retention / Tail | 权利金 / stress loss。 |
| `product_theta_per_vega` | 高更好 | Retention / Vega | 单位 vega 的 theta 质量。 |
| `product_theta_per_gamma` | 高更好 | Retention / Gamma | 单位 gamma 的 theta 质量。 |
| `product_stress_per_premium` | 低更好 | Tail | stress loss / 权利金。 |
| `product_vega_per_premium` | 低更好 | Vega | vega 暴露 / 权利金。 |
| `product_gamma_per_premium` | 低更好 | Gamma | gamma 暴露 / 权利金。 |
| `product_stress_share` | 低更好 | concentration | 该品种在全候选 stress 中占比。 |
| `product_margin_share` | 低更好 | concentration | 该品种在全候选保证金中占比。 |
| `product_cooldown_penalty` | 低更好 | stop risk | 近期止损惩罚。 |
| `product_tail_dependence_max` | 低更好 | tail corr | 上下尾相依最大值。 |
| `product_tail_beta_abs_max` | 低更好 | tail risk | 上下尾 beta 绝对值最大值。 |

### 4.2 Product-side 层候选因子

| 因子 | 方向 | 用途 | 说明 |
| --- | --- | --- | --- |
| `side_premium_sum` | 高更好 | side Premium Pool | 某品种某侧权利金池。 |
| `side_candidate_count` | 高更好 | side liquidity | 某侧候选数量。 |
| `side_premium_to_margin` | 高更好 | side budget | 某侧权利金 / 保证金。 |
| `side_premium_to_stress` | 高更好 | side tail coverage | 某侧权利金 / stress。 |
| `side_theta_per_vega` | 高更好 | side vega quality | 某侧 theta / vega。 |
| `side_theta_per_gamma` | 高更好 | side gamma quality | 某侧 theta / gamma。 |
| `side_stress_per_premium` | 低更好 | side tail | 某侧 stress / premium。 |
| `side_vega_per_premium` | 低更好 | side vega | 某侧 vega / premium。 |
| `side_gamma_per_premium` | 低更好 | side gamma | 某侧 gamma / premium。 |
| `side_trend_alignment` | 高更好 | P/C side selection | 上涨偏 Put、下跌偏 Call。 |
| `side_breakout_cushion` | 高更好 | P/C side risk | Put 看下方距离，Call 看上方距离。 |
| `side_iv_mom_5d` | 低更好 | vol regime | IV 短期上升越快越谨慎。 |
| `side_iv_accel` | 低更好 | vol regime | IV 加速度越高越谨慎。 |
| `side_cooldown_penalty` | 低更好 | cooldown | 同品种同方向止损后降权。 |
| `side_avg_tail_coverage` | 高更好 | tail coverage | 某侧平均尾部覆盖。 |
| `side_avg_contract_iv_skew_to_atm` | 条件使用 | skew | skew 贵可能是溢价，也可能是风险，先诊断。 |

## 5. 检验方法

每个因子必须在正确层级做检验：

```text
Product 因子 -> product 横截面
Product-side 因子 -> product + option_type 横截面
Contract 因子 -> 不在本专题评价
Portfolio 因子 -> 不用普通 IC 评价
```

输出至少包括：

1. Rank IC：按 `signal_date` 横截面计算 Spearman IC。
2. Q1-Q5 分层：观察收益、留存率、止损率是否单调。
3. 相关性矩阵：识别品种层因子共线性。
4. Spread 图：Q5-Q1 的 `future_pnl_per_margin`、`future_retained_ratio`、`future_stop_avoidance`。
5. Top / bottom 产品表：看因子是否只是少数品种驱动。

## 6. 判断标准

一个品种筛选因子要进入交易规则，至少要满足：

1. 在 product 或 product-side 层 `future_pnl_per_margin` IC 为正。
2. Q5 相对 Q1 的 `future_retained_ratio` 改善。
3. Q5 相对 Q1 的 `future_stop_avoidance` 不恶化。
4. 不只是提高 Premium Pool，同时放大 Tail / Stop Loss。
5. 不与已有合约层因子重复计分。

如果一个因子收益 IC 不高，但显著降低 stop loss 或 tail loss，它可以进入风险预算或冷静期，不进入收益排序。

## 7. 初步预期

我预计有效的品种筛选不会来自单一 IV/RV 指标，而会来自组合：

```text
品种权利金池足够厚
+ 权利金 / stress 覆盖较高
+ theta / vega、theta / gamma 质量不差
+ IV 不再上升或开始回落
+ 近期没有连续止损
+ 尾部相依和板块拥挤不过高
```

这也解释为什么“黄金、铜流动性好但权利金薄；白糖、玻璃权利金厚但路径风险和流动性风险更高”不能用单个维度判断。真正值得多给预算的品种，是在可交易容量、权利金厚度、留存率和尾部风险之间有更好折中。

