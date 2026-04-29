---
name: s1-factor-layer-report-writer
description: 为 S1 卖权策略的 full shadow / B2 / B3 / B4 因子分层检验生成中文研究报告、Word 文档和飞书可导入 DOCX。适用于分析 factor_layers_*、candidate universe、Rank IC、Q1-Q5 分层、相关性矩阵、正交化/残差 IC、合约级/品种-方向级/品种级/环境级标签，并判断因子适合用于选品种、选 Put/Call、选合约、环境调节、硬过滤、风险预算或止损控制。
---

# S1 Factor Layer Report Writer

## 触发场景

当用户要求“full shadow 因子检查”“单因子分层报告”“相关性矩阵”“正交化分析”“像股票中性那样检验因子”“B2/B3 因子复盘”“判断因子适合用在哪里”“输出 Word/飞书报告”时，使用本 skill。

本 skill 的任务不是写普通回测报告，而是做 S1 卖权策略的因子研究审计：判断因子是否真的有横截面解释力、是否被共线性污染、是否有增量解释、以及应进入哪个交易决策层级。

## 先继承 B2C 报告的经验

写新报告前，先快速回顾项目里已有的 B2C 分层报告，通常是：

- `docs/s1_b2c_factor_layer_report_20260428.md`
- `output/factor_layers_<tag>_product_side/`
- `output/factor_layers_<tag>_contract/`

B2C 那版已经覆盖：

- Rank IC 和 IC t-stat。
- Q1-Q5 分层。
- 合约级与品种-方向级。
- Put/Call 与 vol regime 切片。
- 止损概率、止损损耗、权利金留存。
- 因子用途初步分类。

新报告必须在此基础上补充：

- 因子相关性矩阵和因子族聚类。
- 控制主因子后的正交化/残差 IC。
- 四个决策层级：选品种、选 Put/Call、选合约、环境调节。
- 因子是否只是和 B2c premium quality 共线。
- 因子到底预测收益、留存、止损、vega/gamma，还是只预测交易摩擦。

## 研究对象不是一个目标，而是四个决策层级

S1 因子不是单纯预测未来收益率。报告必须按四个决策层级组织结论：

| 层级 | 样本单位 | 核心问题 | 典型因子 |
| --- | --- | --- | --- |
| 选品种 | `signal_date + product` | 今天哪些品种值得给预算？ | variance carry、RV/IV、term structure、流动性容量、历史 stop overshoot |
| 选方向 | `signal_date + product + option_type` | 卖 Put、卖 Call，还是双卖？ | P/C skew、上下尾溢价、趋势/动量、put/call demand pressure |
| 选合约 | `signal_date + product + option_type + expiry + strike + contract_code` | 同品种同方向卖哪个执行价？ | premium_to_iv_loss、premium_to_stress_loss、theta/vega、gamma rent、friction |
| 环境调节 | `date` 或 `signal_date + product` | 当前环境应放大、正常、收缩还是暂停？ | falling vol、rising vol、vol-of-vol、VCP、stop cluster、相关性抬升 |

报告中不得把所有因子放进同一个大总分后直接判断有效/无效。必须说明每个因子的适用层级。

## 预测标签

报告必须明确：S1 因子预测的是“卖权承保质量”，不是标的涨跌，也不是 IV 点位。

核心标签至少包括：

- `net_pnl_per_premium`：未来净 PnL / 开仓权利金。
- `retained_ratio`：未来留存权利金 / 开仓权利金。
- `stop_avoidance`：负止损率，越高代表越不容易触发止损。
- `stop_loss_avoidance`：止损损耗的反向指标。
- `vega_pnl_per_premium` 或 `vega_loss_to_premium`：vega 吞噬是否更低。
- `gamma_pnl_per_premium` 或 `gamma_loss_to_premium`：gamma 路径损耗是否更低。
- `stop_overshoot`：止损实际成交价越过止损阈值的幅度，如 `raw_stop_execution_price / stop_threshold - 1`。

如果现有输出缺少 vega/gamma 或 stop overshoot 标签，报告必须把它列为数据缺口，而不能假装已经解释清楚。

## 标准输入

优先使用项目脚本生成因子分析包：

```powershell
python .\scripts\analyze_factor_layers.py `
  --tag <tag> `
  --level product_side `
  --output-dir .\output `
  --top-n-plot 8

python .\scripts\analyze_factor_layers.py `
  --tag <tag> `
  --level contract `
  --output-dir .\output `
  --top-n-plot 8
```

如已有 full shadow 候选文件，应优先从 full shadow 候选池生成分层，而不是从已成交 orders 反推。已成交样本只能解释“被策略处理后的剩余差异”，不能证明因子在完整候选池中有效。

标准目录：

```text
output/factor_layers_<tag>_product/
output/factor_layers_<tag>_product_side/
output/factor_layers_<tag>_contract/
output/factor_layers_<tag>_regime/
```

必读文件：

- `factor_ic_summary.csv`
- `factor_spread_summary.csv`
- `factor_layer_summary.csv`
- `factor_ic_env_summary.csv`
- `factor_layer_env_summary.csv`
- `factor_layer_report.md`
- 如存在：`factor_correlation_matrix.csv`
- 如存在：`factor_cluster_summary.csv`
- 如存在：`factor_orthogonal_ic_summary.csv`
- 如存在：`factor_residual_layer_summary.csv`

核心图表：

- `01_factor_spread_cumulative.png`
- `02_layer_net_premium_heatmap.png`
- `03_layer_retained_heatmap.png`
- `04_layer_stop_rate_heatmap.png`
- `05_factor_ic_heatmap.png`
- `06_cum_ic_net_pnl.png`
- `07_stop_rate_by_layer.png`
- 新增建议：`08_factor_correlation_matrix.png`
- 新增建议：`09_orthogonal_ic_heatmap.png`
- 新增建议：`10_factor_usage_map.png`

## 相关性矩阵要求

每份 full shadow 因子报告必须先做相关性审计，再讨论组合因子。

最低要求：

- 分层级计算 Spearman 相关矩阵：contract、product_side、product、regime 分开。
- 对 Put/Call 分开再看一次，避免方向结构把相关性平均掉。
- 对年份或大 regime 分段再看一次，避免相关性只在单一阶段成立。
- 标记高度共线因子，默认阈值为 `|rho| >= 0.70`，可按样本调整。
- 用因子族描述，而不是逐个因子机械报数。

典型因子族：

- 权利金/方差溢价族：variance carry、premium_to_iv_loss、breakeven cushion。
- Vega/vol convexity 族：theta_vega_efficiency、iv_shock_coverage、vomma_loss_ratio。
- Gamma/stress 族：gamma_rent_penalty、premium_to_stress_loss、stress_loss。
- 摩擦/流动性族：friction_ratio、fee_ratio、spread_ratio、liquidity rent。
- 尾部/skew 族：put wing premium、skew steepening、tail asymmetry。
- 环境族：falling vol、rising vol、vol-of-vol、VCP、stop cluster。

报告必须回答：如果两个因子高度相关，保留哪一个，为什么。优先级不是 IC 最高，而是经济解释更清楚、跨样本更稳定、实盘更可控。

## 正交化和增量解释要求

相关性矩阵之后必须做正交化，不允许直接把共线因子堆进 composite score。

初始正交化优先使用简单、可解释的方法：

- 分层控制：在同一主因子分位内，再看候选因子的 Q1-Q5。
- 残差法：`candidate_factor ~ main_factor_family`，用残差计算 IC。
- 分组回归：`outcome ~ premium_quality + residual_factor`。
- 层级内正交：contract 层只和 contract 因子正交；product_side 层只和 product_side 因子正交。

建议主因子：

- 对 B2/B3/full shadow：先以 `premium_quality_score` 或 B2c 的核心 premium quality 因子族作为主因子。
- 对合约层：先控制 `friction_ratio`、`premium_to_iv10_loss`、`premium_to_stress_loss`。
- 对方向层：先控制 `variance_carry`、P/C skew、trend/momentum。
- 对环境层：先控制 `vol_regime`、falling/rising vol、VOV level。

报告必须回答：

- 控制主因子后，该因子对哪个标签仍有 residual IC？
- residual IC 是否跨年份、Put/Call、regime 稳定？
- 它是收益增量、止损增量、vega/gamma 风险增量，还是没有增量？

## IC 写法要求

必须报告 Rank IC，而不能只看 Q1-Q5 收益。

写 IC 时必须说明：

- 因子方向是否已经调整为“越高越好”。
- IC 是按 `signal_date` 横截面计算。
- 每个层级的横截面对象不同，不能混用。
- IC 标签至少覆盖收益、留存、止损、vega/gamma、stop overshoot。
- 累计 IC 要区分 product-side 层和 contract 层。

重点判断：

```text
如果某因子 net_pnl IC 高，但 stop_avoidance 或 vega/gamma 标签差，
它可能只是提高收益、同时放大尾部风险，不能直接加仓。
```

```text
如果某因子收益 IC 一般，但 stop_avoidance 或 stop_overshoot 改善明显，
它适合做风控惩罚或手数控制，而不是收益排序。
```

## 因子用途分类

报告最后必须把因子分成至少七类，不允许只说有效/无效：

1. **选品种预算因子**：用于决定 product 是否给预算、给多少预算。
2. **选 Put/Call 方向因子**：用于决定 Put/Call 权重、是否双卖、是否单侧降权。
3. **选合约排序因子**：用于同一 product/side/expiry 内选择执行价。
4. **硬过滤因子**：用于剔除明显不值得承保的合约或品种。
5. **止损概率/跳价风险因子**：用于控制手数、铺单厚度、冷却期和重开条件。
6. **环境调节因子**：用于调整总预算、单品种预算和降波加仓/升波收缩。
7. **暂不采用因子**：因共线、样本不稳、只有已成交样本有效、或经济含义不清，暂不进规则。

每个因子必须给出建议位置，例如：

```text
friction_ratio：contract 层硬过滤 + 合约排序，不适合做品种预算。
variance_carry：product_side 层预算因子，不适合单独选执行价。
theta_vega_efficiency：contract 层止损概率/手数控制因子，不宜单独加预算。
vol_of_vol：regime/product 层条件因子，不能简单正用或反用。
stop_overshoot_prior：product/contract 层风控惩罚，不是收益型排序因子。
```

## 公式映射表格要求

以后所有 S1 因子分层报告都必须新增“因子公式映射总表”。该表不是附录装饰，而是判断因子能否进入 B5/B6/B7 交易规则的核心证据。表格至少包含以下字段：

| 字段 | 说明 |
| --- | --- |
| 因子名称 | 原始字段名或派生字段名，例如 `premium_to_iv10_loss`、`b5_tail_corr_proxy`。 |
| 因子族群 | `premium`、`vega`、`gamma`、`trend`、`skew`、`liquidity`、`tail`、`cooldown`、`portfolio` 等。 |
| 适用层级 | 合约层、P/C 侧、品种层、组合层、时间层。必须避免把合约层 IC 直接解释成品种预算能力。 |
| 改善公式变量 | `Premium Pool`、`Deployment Ratio`、`Retention Rate`、`Tail / Stop Loss`、`Cost / Slippage`。可多选，但要指出主变量。 |
| 使用方式 | 排序、预算倾斜、硬过滤、风控约束、退出/冷却、诊断。 |
| IC/分层表现 | 对应标签下的 Rank IC、Q1-Q5、累计 IC、稳定性和是否通过 corrected IC audit。 |
| 留存率影响 | 是否提高 premium retention、`retained_ratio`、`net_pnl_per_premium`，以及是否只是分母效应。 |
| 止损影响 | 是否降低 stop rate、stop loss、stop overshoot，或是否只是减少交易次数。 |
| 尾部影响 | 是否降低 worst bucket、tail loss、cluster loss、tail correlation 或同到期/同板块集中损耗。 |
| 交易代价 | 是否牺牲权利金池、流动性、成交容量、DTE 分散、产品覆盖或 P/C 平衡。 |

写结论时必须遵守以下解释规则：

- 一个因子即使 IC 不是最高，只要能稳定降低 `Tail / Stop Loss`、stop cluster 或 stop overshoot，也可能值得进入风控层。
- 一个因子即使提高 NAV，如果主要靠牺牲 `Retention Rate`、放大尾部聚合、提高 gamma loss 或降低流动性，也不应直接进入交易主线。
- `Premium Pool` 型因子不能单独决定加仓；必须同时检查 `Retention Rate` 和 `Tail / Stop Loss`。
- `Retention Rate` 型因子如果显著牺牲可交易权利金池，应标记为“保守过滤”，不能简单评价为好。
- `Cost / Slippage` 型因子通常更适合硬过滤和执行风控，不应包装成收益 alpha。
- 组合层因子如 tail correlation、HRP、expiry cluster、sector crowding 不应使用普通合约 IC 强行评价，应使用组合尾部和簇拥损耗标签。

因子报告必须在执行摘要中给出“公式项结论”，例如：

```text
Premium Pool：哪些因子能扩大可交易权利金池。
Deployment Ratio：哪些因子提高实际吃到的权利金比例。
Retention Rate：哪些因子提高留存率，哪些只是减少交易。
Tail / Stop Loss：哪些因子降低尾部和止损，哪些会放大左尾。
Cost / Slippage：哪些因子降低费用、滑点、低价 tick 和退出成本。
```

## 图表解读要求

每张核心图至少写四段：

- 图怎么看：这张图验证哪条假设。
- 图上事实：具体哪些因子、哪些层、IC 或 stop rate 是多少。
- 期权专家判断：它对卖权承保质量意味着什么。
- 策略含义：应用于选品种、选 Put/Call、选合约、预算、止损，还是暂不采用。

不要只写“图中可以看到某因子表现较好”。必须解释为什么好、好在哪里、是否有代价。

## 报告结构

建议报告结构：

1. 执行摘要：哪些因子可用，哪些只是共线，哪些需要正交化后再看。
2. 数据和样本：full shadow 还是已成交样本、样本层级、时间区间、缺失字段。
3. 决策层级框架：选品种、选 Put/Call、选合约、环境调节。
4. 标签定义：收益、留存、止损、vega/gamma、stop overshoot。
5. 相关性矩阵和因子族：高度共线因子、保留代表因子。
6. 单因子 IC 和 Q1-Q5 分层：按层级分别写。
7. 正交化/残差 IC：控制主因子后是否仍有效。
8. Put/Call、年份、vol regime、板块切片。
9. 因子公式映射总表：因子名称、族群、层级、改善公式变量、使用方式、IC/分层、留存、止损、尾部和交易代价。
10. 下一版实验建议：给出可回测、非过拟合导向的 B4/B5 方案。

## DOCX 输出

默认同时输出：

- Markdown 研究底稿。
- Word 报告 `.docx`。
- 飞书可导入 `.docx`。

如果项目里存在 `scripts/build_factor_layer_report_docx.py`，优先使用：

```powershell
python .\scripts\build_factor_layer_report_docx.py `
  --markdown .\docs\<report>.md `
  --output .\output\factor_layer_reports\<report>_word.docx `
  --feishu-output .\output\factor_layer_reports\<report>_feishu.docx `
  --repo-root .
```

DOCX 必须嵌入 PNG 图，不允许只保留本地 Markdown 图片路径。若 documents 渲染工具可用，应渲染检查首页、含图页和末页。

## Corrected IC Audit Protocol

When writing any S1 full-shadow factor layer report, first run the corrected IC
audit instead of relying only on raw `future_net_pnl_per_premium`.

Mandatory audit checks:

- Explicitly state that `future_net_pnl_per_premium` and retained-ratio style labels can be mechanically affected by premium, fee, entry price and denominator effects.
- Always report multiple samples: all candidates, completed-only candidates, premium >= 100, completed + premium >= 100, and completed + premium >= 100 + low-fee.
- Always separate raw ratio labels from corrected labels: `cash_pnl`, `pnl_per_margin`, `pnl_per_stress`, clipped pnl/premium, `stop_avoidance`, and `stop_overshoot_avoidance`.
- Always include cumulative IC plots for corrected labels. A report without cumulative IC charts is incomplete.
- Always include low-price distortion diagnostics. Contracts with very low entry price or very small premium can dominate ratio labels and stop statistics.
- Always include factor correlation matrix and top correlated pairs. Highly correlated factors should be treated as one family unless residual IC proves incremental value.
- Always include within `signal_date + product + option_type` IC. This distinguishes cross-product allocation signal from same-chain strike-selection geometry.
- Always include residual IC after controlling at least `log_entry_price`, `log_open_premium_cash`, `dte`, `abs_delta`, then a second set adding `log_margin_estimate`, and a third set adding `log_stress_loss`.
- Always include non-overlap robustness: every 5th signal date and first signal per contract code.
- Always produce a factor usage map. Each factor must be assigned to one or more of: hard filter, product budget, Put/Call direction, contract ranking, stop-risk control, regime penalty, or do-not-use-yet.
- Always state shadow-label limitations: the current shadow outcome uses next-day close entry and daily close stop logic, so it is a candidate research label, not an executable intraday stop PnL.

Interpretation rules:

- If a factor works only on raw pnl/premium but disappears on `cash_pnl`, `pnl_per_margin`, residual IC or non-overlap tests, classify it as denominator/geometry contaminated.
- If a factor keeps positive `pnl_per_margin` or `pnl_per_stress` IC after residual controls, classify it as a capital-efficiency or risk-coverage ranking factor, not necessarily a product-level alpha factor.
- If a factor improves stop avoidance but not cash return, use it for stop-risk sizing or penalty, not for adding budget.
- If a factor has high within-chain IC but weak product-side IC, use it for strike/contract selection only.
- If a factor has strong product/product-side IC and survives non-overlap tests, it may be used for cross-product budget tilt, subject to out-of-sample validation.

## 写作风格

- 用中文。
- 先结论后证据。
- 像期权量化专家，不像数据表搬运员。
- 明确指出共线性、样本选择偏差、已成交样本偏差和未来函数风险。
- 不因为一个因子 IC 高就建议加仓；必须同时看止损、vega/gamma、尾部和环境切片。
- 如果结果只来自已成交样本，必须提示它不是 all-candidate universe 检验。
