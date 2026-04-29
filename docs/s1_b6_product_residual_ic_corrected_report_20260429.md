# S1 B6 品种层 Residual IC 修正版报告

生成日期：2026-04-29  
数据版本：`s1_b5_full_shadow_v1_2022_latest`  
分析输出：`output/b6_product_selection_corrected_v3_s1_b5_full_shadow_v1_2022_latest/`  
报告目的：检验原始品种/品种-方向 IC 是否被同分母、权利金池、保证金、stress、delta、候选数量等共同结构污染。

---

## 1. 结论摘要

这次补做 residual IC 后，结论比上一版更清楚，也更克制：

1. 原始 IC 确实“太漂亮”。尤其 `premium_to_margin`、`premium_to_stress`、`vega_per_premium_low`、`gamma_per_premium_low` 这组因子，在控制同分母和基础规模变量后大幅衰减。
2. 品种层的纯 residual alpha 不强。`product_premium_to_margin` 从原始 0.253 降到 full residual 0.012，基本不能再被解释为独立品种预算 alpha。
3. 品种-方向层仍然比纯品种层更有价值。`side_premium_to_margin` full residual IC 仍有 0.057，`side_theta_per_vega` 0.040，`side_premium_to_stress` 0.033，说明 P/C 侧预算倾斜比单纯 product 加权更值得继续。
4. `side_avg_abs_delta` 原始 IC 很高，但 residual 后几乎消失。它更像是 delta/权利金几何效应，不应作为独立因子，更不能解释为可以放宽 `delta < 0.1`。
5. `theta_per_vega` 是这次最值得注意的残差信号。它在品种层 full residual `future_pnl_per_margin` 为 0.056，在品种层 full residual `future_stop_avoidance` 为 0.076；在品种-方向层也分别为 0.040 和 0.043。这和我们“控制 vega、保留 theta”的目标高度一致。
6. `premium_to_margin` 可以继续作为 Premium Pool 和 Deployment Ratio 诊断，但不能单独作为品种预算主因子。它提高收益的同时，在 full residual stop avoidance 上反而偏负，说明它可能把我们推向更厚但更容易止损的风险。

一句话：

```text
B6 不能用原始 IC 直接加品种预算；
更合理的路线是把 raw premium/margin 视为权利金池诊断，
把 residual 后仍有效的 theta/vega、premium/stress、P/C side 因子用于温和预算倾斜。
```

---

## 2. 这次补上的严谨性改动

我已把 residual IC 写入 `scripts/analyze_b6_product_selection.py`，现在每次运行 B6 品种分析都会输出：

```text
product/factor_residual_ic_summary.csv
product_side/factor_residual_ic_summary.csv
product/06_residual_ic_full_denominator.png
product/07_residual_ic_margin_denominator.png
product_side/06_residual_ic_full_denominator.png
product_side/07_residual_ic_margin_denominator.png
```

Residual IC 的核心做法是：

```text
每个 signal_date 横截面内：
    先把因子对控制变量做 OLS 残差化
    再把标签对同一组控制变量做 OLS 残差化
    然后计算 corr(rank(factor_residual), rank(label_residual))
```

这比 raw IC 严格得多，因为它问的是：

```text
控制权利金池、保证金、stress、候选数量、delta 和基础 Greek 后，
这个因子还剩多少增量排序能力？
```

---

## 3. 控制组定义

### 3.1 品种层控制组

| 控制组 | 控制变量 | 目的 |
| --- | --- | --- |
| `base_depth` | `log(premium_sum)`、`log(candidate_count)`、`log(side_count)` | 控制权利金池和候选深度 |
| `margin_denominator` | `log(premium_sum)`、`log(margin_sum)`、`log(candidate_count)`、`avg_delta_ratio_to_cap` | 专门控制 `pnl_per_margin` 的同分母污染 |
| `stress_denominator` | `log(premium_sum)`、`log(stress_sum)`、`log(candidate_count)`、`avg_delta_ratio_to_cap` | 专门控制 `pnl_per_stress` 的同分母污染 |
| `full_denominator` | premium、margin、stress、vega、gamma、theta、candidate、side、delta、cooldown、margin share、stress share | 最严格控制组 |

### 3.2 品种-方向层控制组

| 控制组 | 控制变量 | 目的 |
| --- | --- | --- |
| `base_depth` | `log(side_premium_sum)`、`log(side_candidate_count)`、`side_avg_abs_delta`、`is_put` | 控制方向侧权利金池、delta 和 P/C 基础差异 |
| `margin_denominator` | base + `log(side_margin_sum)` | 控制 `pnl_per_margin` 同分母污染 |
| `stress_denominator` | base + `log(side_stress_sum)` | 控制 `pnl_per_stress` 同分母污染 |
| `full_denominator` | premium、margin、stress、vega、gamma、theta、candidate、delta、cooldown、is_put、trend、IV momentum | 最严格控制组 |

本报告主要采用 `factor_and_label_resid`，即因子和标签都残差化后的 Rank IC。

---

## 4. Raw IC 与 Residual IC 对比

### 4.1 品种层：原始强因子大幅衰减

| 因子 | Raw IC: PnL/Margin | Margin Residual IC | Full Residual IC | Full Residual Stop IC | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `product_premium_to_margin` | 0.253 | -0.005 | 0.012 | -0.060 | 原始 IC 主要来自分母/权利金结构，不宜做独立预算因子 |
| `product_premium_to_stress` | 0.213 | 0.038 | 0.017 | -0.006 | 有弱增量，但不够强；更适合承保质量辅助 |
| `product_vega_per_premium_low` | 0.195 | 0.027 | -0.002 | 0.012 | 控制后收益 IC 消失；仍可做 vega 风险诊断 |
| `product_gamma_per_premium_low` | 0.218 | 0.026 | 0.018 | 0.029 | 弱正，适合作为 gamma 惩罚辅助 |
| `product_theta_per_vega` | 0.068 | 0.038 | 0.056 | 0.076 | 最值得保留的残差信号 |
| `product_theta_per_gamma` | 0.172 | 0.037 | 0.025 | 0.013 | 有弱增量，但不如 theta/vega |
| `product_tail_beta_abs_max_low` | -0.033 | 0.020 | 0.026 | 0.025 | 原始看不出，残差后有尾部控制价值 |

期权专家解读：

`product_premium_to_margin` 原始 IC 很强，但控制 `premium_sum` 和 `margin_sum` 后几乎没了。这说明它主要是“品种权利金池厚、保证金效率高”的描述，而不是一个独立预测因子。它可以帮助我们理解 Premium Pool，但不能单独决定给哪个品种更大风险预算。

真正更干净的是 `product_theta_per_vega`。它控制了 premium、margin、stress、vega、gamma、theta 等变量后仍然为正，而且对 stop avoidance 也强。这说明在品种层，“每单位 vega 承担下能拿到多少 theta”确实可能改善 S1 的承保质量。

### 4.2 品种-方向层：仍然保留可用残差信号

| 因子 | Raw IC: PnL/Margin | Margin Residual IC | Full Residual IC | Full Residual Stop IC | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `side_premium_to_margin` | 0.340 | 0.050 | 0.057 | -0.034 | 收益残差仍强，但止损残差偏负，不能单独加仓 |
| `side_premium_to_stress` | 0.270 | 0.041 | 0.033 | 0.006 | 更适合 P/C 侧承保质量排序 |
| `side_avg_abs_delta` | 0.279 | -0.053 | 0.005 | 0.012 | 原始 IC 几乎全是几何效应 |
| `side_vega_per_premium_low` | 0.252 | 0.014 | 0.015 | 0.025 | 更适合 vega 风险控制，不适合作主收益因子 |
| `side_gamma_per_premium_low` | 0.277 | 0.011 | 0.011 | 0.007 | 控制后较弱 |
| `side_theta_per_vega` | 0.084 | 0.028 | 0.040 | 0.043 | 残差稳定，符合控制 vega 目标 |
| `side_theta_per_gamma` | 0.205 | 0.040 | 0.029 | 0.021 | 可作为 gamma 辅助 |
| `side_trend_alignment` | 0.018 | 0.052 | 0.016 | 0.012 | 在 margin 控制下有效，full 控制后减弱 |
| `side_momentum_alignment` | 0.025 | 0.049 | 0.000 | -0.007 | 被 full 控制吸收，不宜直接入主 score |
| `side_breakout_cushion` | 0.067 | 0.039 | -0.004 | -0.004 | 控制后不稳 |

期权专家解读：

品种-方向层比品种层更有交易价值。`side_premium_to_margin`、`side_premium_to_stress`、`side_theta_per_vega` 在 full residual 下仍为正，说明同一个品种里面 Put/Call 两侧的预算分配，确实可以通过承保质量因子改善。

但要注意，`side_premium_to_margin` 的 full residual stop IC 是 -0.034。也就是说，它能提高 `pnl_per_margin`，但可能没有同步降低止损风险。它适合做“收益端倾斜”，不能单独作为风险放大器。

---

## 5. 图表解读

### 5.1 品种层 Raw IC

![品种层 Raw IC](output/b6_product_selection_corrected_v3_s1_b5_full_shadow_v1_2022_latest/product/01_factor_ic_heatmap.png)

这张图是未残差化的 IC。它告诉我们，权利金效率、stress 覆盖、低 vega/gamma 单位负担在原始横截面上非常强。

但这张图现在只能作为“发现候选因子”的图，不能作为“交易采纳证据”。原因是图里最强的几个因子和标签共同使用了 premium、margin、stress 等基础变量。

### 5.2 品种层 Full Residual IC

![品种层 Full Residual IC](output/b6_product_selection_corrected_v3_s1_b5_full_shadow_v1_2022_latest/product/06_residual_ic_full_denominator.png)

这张图才是本次最关键的审计图。控制完整分母和基础结构后，原来最强的一批品种层因子明显衰减。

图上的事实是：

1. `product_premium_to_margin` 不再是强信号。
2. `product_vega_per_premium_low` 的收益 residual IC 接近消失。
3. `product_theta_per_vega` 成为更干净的残差信号。
4. tail beta / tail dependence 类因子在 residual 后出现弱正，说明尾部控制可能不能用 raw IC 直接判断。

策略含义：

品种层不应该直接用 `premium_to_margin` 做预算权重。更合理的是：先用它判断品种权利金池是否足够，再用 `theta_per_vega`、`premium_to_stress`、tail beta 等因子做质量修正。

### 5.3 品种-方向层 Raw IC

![品种-方向层 Raw IC](output/b6_product_selection_corrected_v3_s1_b5_full_shadow_v1_2022_latest/product_side/01_factor_ic_heatmap.png)

品种-方向层 raw IC 比品种层更强，这解释了为什么我们不能只做 product 预算。S1 的实际问题往往是：

```text
今天这个品种的 Put 侧还是 Call 侧更值得卖？
```

而不是简单问：

```text
今天这个品种值不值得做？
```

但 raw 图里 `side_avg_abs_delta` 很强，这一项现在已经被 residual 证明主要是几何效应，不能过度解释。

### 5.4 品种-方向层 Full Residual IC

![品种-方向层 Full Residual IC](output/b6_product_selection_corrected_v3_s1_b5_full_shadow_v1_2022_latest/product_side/06_residual_ic_full_denominator.png)

控制 P/C、delta、premium、margin、stress、vega、gamma、theta、趋势和 IV momentum 后，品种-方向层仍然保留了一些残差信号。

最重要的是：

1. `side_premium_to_margin` 仍有 0.057 的 full residual IC，但止损端偏负，需要和风险因子配套。
2. `side_theta_per_vega` 同时改善收益和止损，是更适合进入 P/C 预算倾斜的因子。
3. `side_premium_to_stress` 仍然为正，但强度不如 raw 图看上去那么夸张。
4. 趋势/动量类在 full 控制后明显衰减，说明它们更像是条件因子，不适合直接和 premium quality 因子线性相加。

---

## 6. 对 B6 因子用途的修正

### 6.1 可以进入下一轮 B6 实验的因子

| 因子 | 层级 | 使用方式 | 原因 |
| --- | --- | --- | --- |
| `product_theta_per_vega` | 品种层 | 温和预算倾斜 | full residual 收益和止损都较好 |
| `product_premium_to_stress` | 品种层 | 质量辅助，不单独加仓 | margin residual 仍为正，full 后偏弱 |
| `product_tail_beta_abs_max_low` | 品种层 | 风险惩罚 | raw 不强但 residual 后有尾部控制价值 |
| `side_premium_to_margin` | P/C 侧 | 收益端预算倾斜，但加止损惩罚 | residual 收益强，止损端偏负 |
| `side_premium_to_stress` | P/C 侧 | 承保质量排序 | residual 稳定为正 |
| `side_theta_per_vega` | P/C 侧 | 核心质量因子 | 收益和 stop avoidance 都为正 |
| `side_theta_per_gamma` | P/C 侧 | gamma 辅助惩罚 | 弱正，适合辅助 |

### 6.2 不应直接采用的因子

| 因子 | 原因 | 处理方式 |
| --- | --- | --- |
| `product_premium_to_margin` | 同分母控制后几乎消失，stop residual 偏负 | 只做 Premium Pool 诊断 |
| `side_avg_abs_delta` | raw 强但 residual 消失 | 只用于 delta 梯队，不做 alpha |
| `side_momentum_alignment` | margin residual 有效，full residual 消失 | 暂作条件切片，不入主 score |
| `side_breakout_cushion` | full residual 转弱 | 暂不进 B6 主实验 |
| `product_vega_per_premium_low` | full residual 收益接近 0 | 只做 vega 风险诊断 |

---

## 7. 对收益公式的映射

按照我们现在统一的 S1 研究范式：

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

这次 residual IC 对各项的判断如下：

| 公式项 | 原来 raw IC 给出的印象 | residual 后的修正 |
| --- | --- | --- |
| Premium Pool | `premium_to_margin` 非常强 | 主要是权利金池/分母结构，不能直接当 alpha |
| Deployment Ratio | 高权利金效率品种看似应该多做 | 只能温和倾斜，且要加 stop / vega 约束 |
| Retention Rate | 低 vega/gamma per premium 看似强 | 控制后更可靠的是 `theta_per_vega` |
| Tail / Stop Loss | stress 覆盖看似强 | `premium_to_stress` 有弱残差，tail beta 残差后有价值 |
| Cost / Slippage | 本次没有专门 residual 标签 | 后续仍需引入低价 tick、退出容量和 stop overshoot |

---

## 8. 下一步建议

### 8.1 交易实验不要使用 raw IC 权重

B6 第一版不应该把 `product_premium_to_margin` 原始 IC 直接做成预算权重。更稳妥的是：

```text
product_score =
  0.40 * product_theta_per_vega_rank
+ 0.25 * product_premium_to_stress_rank
+ 0.20 * product_tail_beta_low_rank
+ 0.15 * product_gamma_per_premium_low_rank
```

但这个只适合温和倾斜，不适合硬筛。

### 8.2 P/C 侧预算比品种总预算更值得做

建议优先实验：

```text
side_score =
  0.35 * side_theta_per_vega_rank
+ 0.30 * side_premium_to_stress_rank
+ 0.20 * side_premium_to_margin_rank
+ 0.15 * side_theta_per_gamma_rank
- stop_risk_penalty
```

其中 `side_premium_to_margin` 必须配 `stop_risk_penalty`，因为它 full residual stop IC 为负。

### 8.3 还要补非重叠和累计 residual IC

这次已经补上同分母残差控制，但还没有完全解决两个问题：

```text
持有期重叠导致 t-stat 偏高
残差信号是否稳定累积
```

因此下一步脚本还应该继续补：

```text
every_5th_signal_date residual IC
first_signal_per_product_month residual IC
cumulative residual IC
2022-2024 / 2025 / 2026 walk-forward
```

---

## 9. 最终结论

这次 residual IC 把上一版最关键的问题补上了。结论不是“因子没用”，而是：

```text
raw IC 里最漂亮的部分，确实有相当一部分来自权利金、保证金、stress 和 delta 的共同结构；
但 residual 后仍然留下了少数更干净、更符合卖权逻辑的因子。
```

最值得继续的不是单纯高权利金、也不是单纯高 premium/margin，而是：

```text
theta / vega 更高，
premium / stress 更合理，
P/C 侧承保质量更好，
并且不会同步提高 stop / tail loss 的候选。
```

所以 B6 的方向应从“用 raw IC 选高收益品种”修正为：

```text
用 corrected residual IC 识别能改善 Retention Rate 和 Tail / Stop Loss 的品种/方向质量因子；
raw premium 因子只负责告诉我们权利金池在哪里，
residual quality 因子才决定预算能不能倾斜过去。
```

