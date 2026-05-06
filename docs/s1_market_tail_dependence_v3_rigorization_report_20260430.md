# S1 全市场期货尾部相关性研究 v3 严谨化报告

日期：2026-04-30  
研究目录：`market_tail_dependence`  
正式输出：`market_tail_dependence/output/full_market_2022_20260430_v3`  
远端输出：`/macro/home/lxy/jy_option_codex_refactor/market_tail_dependence/output/full_market_2022_20260430_v3`

---

## 1. 本轮严谨化目标

v2 已经打通了全市场期货尾部相关研究链路，但还存在三个问题：

1. `tail_cluster` 使用单一阈值 `0.12`，最大 cluster 有 67 个品种，过于粗糙。
2. `corr_shock_state` 使用“任一指标超过历史 90% 分位”触发，过于敏感。
3. 板块分类有解释力，但尚未检验自动 tail cluster 与人工 `bucket/corr_group` 的一致性。

v3 的目标不是生成交易信号，而是把这个模块升级为 B7 组合风控可以引用的研究基础设施。

本轮主要改善 S1 收益拆解公式中的：

```text
Tail / Stop Loss
Deployment Ratio
```

对应层级是组合层和时间层，不是合约层 alpha。

---

## 2. v3 新增内容

### 2.1 尾部阈值稳健性

新增：

```text
market_tail_sensitivity_summary.csv
market_pair_tail_dependence_q0p050.csv
market_pair_tail_dependence_q0p025.csv
```

现在同时检验：

```text
5% 尾部
2.5% 极端尾部
```

目的是确认板块解释力不是某一个分位数偶然造成的。

### 2.2 Tail cluster 阈值敏感性

新增：

```text
market_tail_cluster_sensitivity.csv
market_tail_cluster_membership_by_threshold.csv
market_tail_cluster_validation.csv
charts/tail_cluster_threshold_sensitivity.png
```

现在同时检验：

```text
abs_tail_jaccard threshold = 0.12 / 0.18 / 0.25 / 0.35
```

并计算自动 tail cluster 与人工 `bucket/corr_group` 的：

```text
ARI
NMI
purity
```

### 2.3 相关性突变信号收紧

v2 的 `corr_shock_state` 是任一滚动指标超过历史 90% 分位即触发。v3 改为：

```text
corr_shock_state_any_p90     = 至少 1 个指标超过历史 90% 分位
corr_shock_state_strict_p90  = 至少 2 个指标超过历史 90% 分位
corr_shock_state_extreme_p95 = 至少 2 个指标超过历史 95% 分位
corr_shock_state             = corr_shock_state_strict_p90
```

这样更适合作为组合降预算研究信号。

### 2.4 单品种尾部画像

新增：

```text
market_product_tail_profile.csv
```

包括：

```text
样本起止日期
收益波动
1% / 5% / 95% / 99% 分位
最大上涨/最大下跌
绝对尾部阈值
上尾/下尾/绝对尾部日数量
range / RV proxy
```

这个表后续可以接品种筛选和风险预算，但目前仍属于诊断，不直接进入交易。

---

## 3. 样本覆盖

本轮正式运行结果：

```text
样本区间：2022-01-01 至 2026-04-30
日频记录：67,490 行
收益矩阵：1,238 个交易日 × 78 个有效期货品种
pair 数量：2,975
主尾部阈值：5%
稳健性尾部阈值：2.5%
滚动窗口：120 个交易日
```

---

## 4. 主要结果

### 4.1 板块分类确实解释尾部相关

在 5% 尾部下：

| 指标 | 同 bucket | 跨 bucket | 差值 |
|---|---:|---:|---:|
| `lower_tail_jaccard` | 0.163 | 0.057 | 0.106 |
| `upper_tail_jaccard` | 0.155 | 0.055 | 0.100 |
| `abs_tail_jaccard` | 0.138 | 0.050 | 0.088 |
| `lower_tail_rate` | 0.268 | 0.107 | 0.161 |
| `upper_tail_rate` | 0.258 | 0.104 | 0.153 |
| `abs_tail_rate` | 0.233 | 0.096 | 0.137 |

同 `corr_group` 的差异更明显：

| 指标 | 同 corr_group | 跨 corr_group | 差值 |
|---|---:|---:|---:|
| `lower_tail_jaccard` | 0.325 | 0.064 | 0.261 |
| `upper_tail_jaccard` | 0.325 | 0.061 | 0.264 |
| `abs_tail_jaccard` | 0.300 | 0.055 | 0.245 |

结论：人工板块和 `corr_group` 不是装饰字段，确实包含尾部共振信息。B7 应保留板块/相关组预算约束。

### 4.2 2.5% 极端尾部下，板块解释力仍然存在

在 2.5% 极端尾部下：

| 指标 | 同 bucket | 跨 bucket | 差值 |
|---|---:|---:|---:|
| `lower_tail_jaccard` | 0.140 | 0.038 | 0.101 |
| `upper_tail_jaccard` | 0.125 | 0.035 | 0.090 |
| `abs_tail_jaccard` | 0.118 | 0.033 | 0.084 |

同 `corr_group` 下：

| 指标 | 同 corr_group | 跨 corr_group | 差值 |
|---|---:|---:|---:|
| `lower_tail_jaccard` | 0.286 | 0.045 | 0.241 |
| `upper_tail_jaccard` | 0.295 | 0.040 | 0.255 |
| `abs_tail_jaccard` | 0.276 | 0.038 | 0.238 |

结论：板块解释力不是 5% 阈值的偶然现象，极端尾部下仍然成立。

### 4.3 Tail cluster 阈值应比 0.12 更严格

阈值敏感性：

| abs-tail Jaccard 阈值 | cluster 数 | 多品种 cluster | singleton | 最大 cluster | Top5 cluster share |
|---:|---:|---:|---:|---:|---:|
| 0.12 | 12 | 1 | 11 | 67 | 0.910 |
| 0.18 | 30 | 6 | 24 | 37 | 0.667 |
| 0.25 | 45 | 13 | 32 | 13 | 0.372 |
| 0.35 | 59 | 10 | 49 | 6 | 0.244 |

解释：

- `0.12` 太松，几乎把大部分市场连成一个大 cluster，只适合判断“市场有整体尾部共振”，不适合作为 B7 分组预算。
- `0.18` 仍偏粗，但可以作为中观风险簇。
- `0.25` 最大 cluster 降到 13 个品种，分组更适合组合预算。
- `0.35` 太碎，虽然 purity 高，但会丢失很多跨品种尾部结构。

初步建议：B7 第一版可优先测试 `0.25` 作为 tail cluster 分组阈值，同时保留 `0.18` 作为宽松压力观察层。

### 4.4 自动 cluster 与人工分类的关系

自动 tail cluster 与人工分类一致性：

| 阈值 | 对比标签 | ARI | NMI | purity |
|---:|---|---:|---:|---:|
| 0.12 | bucket | 0.039 | 0.362 | 0.346 |
| 0.12 | corr_group | 0.010 | 0.429 | 0.192 |
| 0.18 | bucket | 0.236 | 0.664 | 0.692 |
| 0.18 | corr_group | 0.067 | 0.745 | 0.513 |
| 0.25 | bucket | 0.487 | 0.814 | 0.974 |
| 0.25 | corr_group | 0.346 | 0.900 | 0.782 |
| 0.35 | bucket | 0.193 | 0.782 | 1.000 |
| 0.35 | corr_group | 0.541 | 0.941 | 0.949 |

解释：

- `0.25` 在“不太粗”和“不太碎”之间比较均衡。
- `0.35` 与 corr_group 一致性更高，但 singleton 太多，可能过度切碎。
- `0.12` 与人工分类一致性很差，因为它把大部分市场连成一个大簇。

### 4.5 相关性突变信号收紧后仍偏多，但已明显改善

滚动日期共 1,119 个。

| 信号 | 触发天数 | 占比 |
|---|---:|---:|
| 任一指标超过 p90 | 437 | 39.1% |
| 至少两个指标超过 p90 | 269 | 24.0% |
| 至少两个指标超过 p95 | 202 | 18.1% |

解释：

- v3 默认 `corr_shock_state` 使用“至少两个指标超过 p90”，比 v2 更严格。
- 但 24% 的触发比例仍偏高，作为交易降预算信号还需要进一步校准。
- 更稳健的做法可能是使用 `extreme_p95`，或要求 shock 状态连续 3 天以上才生效。

### 4.6 隐性尾部风险 pair

当前 strict 规则下仍只筛出一个明显隐性 pair：

```text
HC-WH
普通相关：0.115
abs_tail_jaccard：0.333
abs_tail_rate：0.549
```

这类 pair 的特点是普通相关不高，但尾部共同出现概率高。后续 B7 可以将其作为 hidden pair 黑名单或 pair budget 限制的示例。

---

## 5. 当前可用结论

1. 板块和 `corr_group` 应继续保留，因为它们对尾部共振有显著解释力。
2. 组合层不能只靠普通相关，必须用上尾、下尾和绝对尾部三套矩阵。
3. `tail_cluster` 不应使用 v2 的默认 0.12 阈值；B7 初版建议优先尝试 0.25。
4. `corr_shock_state` 已经从宽松版收紧，但仍需要和 S1 回撤/止损聚集对齐后再决定是否进交易。
5. 当前研究模块已经可以支撑 B7 的设计，但还不应该直接替代现有板块约束。

---

## 6. 尚未完成

### 6.1 与 S1 回测结果交叉验证

还需要检验：

```text
corr_shock_state 是否对应 B2C/B6 的回撤期
tail_cluster 是否解释止损聚集
same cluster 的 stress budget 是否在回撤期过高
hidden pair 是否对应同日/连续止损
```

### 6.2 更真实的高频风险口径

当前 `rv_1d` 使用 high-low Parkinson proxy。后续需要：

```text
分钟级 realized variance
分钟级 realized covariance
jump share
overnight gap contribution
intraday trendiness
```

### 6.3 Tail-HRP 还没有落地

现在已有尾部距离矩阵和 cluster，但还没有：

```text
tail distance matrix
hierarchical ordering
recursive bisection budget
B7 Tail-HRP 回测
```

### 6.4 Shock 信号阈值还需校准

候选规则：

```text
strict_p90 连续 3 天
extreme_p95 单日触发
top_eigen_share 与 avg_abs_tail_jaccard 同时触发
shock state 只作为预算 multiplier，不作为硬禁做
```

---

## 7. 下一步建议

下一步应进入 Phase 4：把市场尾部相关结果与 S1 回测结果对齐。

建议先做三个验证：

1. `corr_shock_state` 与 B2C/B6 的日度回撤、止损数量、保证金使用率对齐。
2. 按 `tail_cluster@0.25` 汇总 S1 每日 stress、cash gamma、cash vega，看回撤期是否集中。
3. 比较 B2C/B6 在 shock vs non-shock 日期的收益、止损率、vega/gamma 损耗。

只有这一步通过后，才进入 B7：

```text
B7a: tail_cluster@0.25 预算上限
B7b: corr_shock_state_extreme_p95 降组合预算
B7c: Tail-HRP 风险预算
B7d: 上尾/下尾分侧 P/C 预算
B7e: 综合版
```

---

## 8. 管理层口径结论

本轮研究说明：商品和金融期货品种并不是天然分散。人工板块分类确实能解释尾部共振，但简单板块约束仍不够，因为尾部相关存在跨板块和动态突变问题。

对 S1 卖权策略而言，这一模块的价值不是提高开仓权利金，而是降低组合层的“假分散”和“尾部聚集”。它应作为 B7 的组合风险基础设施，服务于 tail cluster 预算、相关性突变降仓和 Tail-HRP，而不是作为新的合约排序 alpha。
