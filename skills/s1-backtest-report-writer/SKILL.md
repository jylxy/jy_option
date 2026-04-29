---
name: s1-backtest-report-writer
description: 为 S1 卖权策略回测生成完整中文归因报告、管理层汇报稿和飞书可导入 DOCX。适用于需要以期权量化专家视角分析 NAV、回撤、保证金、Greeks、PnL 归因、品种/板块、P/C 结构、止损路径、尾部风险和乐得差距，并把 output/analysis_* 图表嵌入报告且逐图深度讲解的任务。
---

# S1 Backtest Report Writer

## 触发场景

当用户要求“写回测报告”“做全面归因”“输出飞书文档”“把图表插入报告”“给领导汇报版”或复盘 S1/B0/F 系列回测结果时，使用本 skill。报告作者必须站在期权策略专家和代码审计者的交叉视角：不仅描述结果，还要判断卖权收益来源、风险补偿是否合理、是否可能存在口径或实现问题。

## 必交付物

- 一份中文 Markdown 正文，放在项目 `docs/` 下。
- 一份飞书可导入的 `.docx`，优先放在 `output/analysis_<tag>/` 下。
- 报告必须嵌入 `output/analysis_<tag>/` 中的标准图表，并在每张图后写深度解读：`怎么看 / 图上读数 / 期权专家判断 / 风险或口径疑点 / 下一步验证`。
- 飞书导入版 DOCX 的图片必须优先使用项目脚本的显式图表附录机制：从 `analysis_dir` 读取标准 PNG，并通过 `python-docx` 的 `add_picture` 嵌入。不要依赖 Markdown 的 `![...](...)` 图片语法作为飞书导入主路径，因为飞书导入时可能丢失本地相对路径图片。
- 注意：DOCX 末尾的自动图表附录只是“图片展示 + 机器快读”，不能视为正式分析。正式报告的 Markdown 正文必须单独写逐图深度分析；如果正文没有逐图分析，即使 DOCX 附录已经插图，也视为报告未完成。
- 报告前部必须有“期权专家审议摘要”：明确策略到底赚了 theta、vega、方向、gamma 还是 residual；如果 vega 为负或 gamma 侵蚀过大，必须把它作为核心问题，而不是附带说明。
- 如果本地、GitHub、服务器需要同步，完成后提交并同步三端。

## 标准输入

- 回测 tag，例如 `s1_b0_standard_stop25_allprod_2022_latest`。
- `output/nav_<tag>.csv`
- `output/orders_<tag>.csv`
- `output/diagnostics_<tag>.csv`
- `output/analysis_<tag>/summary_metrics.csv`
- `output/analysis_<tag>/yearly_returns.csv`
- `output/analysis_<tag>/monthly_returns.csv`
- `output/analysis_<tag>/worst_20_days.csv`
- `output/analysis_<tag>/open_sell_product_summary.csv`
- `output/analysis_<tag>/stop_loss_product_summary.csv`
- `output/analysis_<tag>/daily_open_premium.csv`
- `output/analysis_<tag>/premium_quality_summary.csv`
- `output/analysis_<tag>/tail_product_side_contribution.csv`
- `output/analysis_<tag>/vega_quality_by_bucket.csv`
- `output/analysis_<tag>/stop_slippage_distribution.csv`
- `output/analysis_<tag>/pc_funnel.csv`
- `output/analysis_<tag>/core_audit/core_audit_<tag>.csv`，如存在则必须用于校验 Greek 归因。

## 期权专家写作内核

报告不是净值流水账。每一份 S1 报告都必须围绕以下问题展开：

- 这是不是一个合格的卖权/空波动率策略：收益是否主要来自正 theta 和正 vega，还是来自 delta 方向侥幸、residual 或更高尾部暴露。
- 这份 theta 是否“够厚”：按保证金、stress loss、尾部亏损和手续费之后，权利金是否足以补偿 gamma、vega、跳空和流动性风险。
- 权利金是否“留得住”：不要只看毛权利金流量，要看 `vega loss / gross premium`、`gamma loss / gross premium`、`premium retained ratio` 和 `S1 PnL / gross premium`。若毛权利金足够高但 vega 吞噬率高，下一版主线必须是控制 vega，而不是继续放大开仓。
- vega 为何为正或为负：区分真实降波/升波、IV 计算口径、近到期 IV 噪声、skew/vanna/volga、止损路径和异常报价。
- gamma 亏损是否合理：短 DTE、接近平值、单边趋势、合约过度集中或止损成交滑点是否放大了 gamma 亏损。
- P/C 是否让组合变成方向仓：双卖是否实际单侧化，趋势偏移是否有逻辑，Call 和 Put 各自是否赚到应赚的钱。
- 品种池是否真的分散：全品种扫描不等于尾部分散，必须看 top 产品、板块、相关组和最差日期上的共同亏损。
- 保证金使用是否有效：高保证金使用率如果换来更差 vega/gamma 或尾部簇集，不应被写成单纯进步。
- 和乐得画像差在哪：持仓颗粒度、P/C、商品/股指/ETF 权重、降波时是否敢加、升波时是否能活下来。

若上述问题无法从现有输出回答，报告必须明确列为“数据缺口/下一步诊断”，不能装作已经解释清楚。

## 报告结构

1. 核心结论：先判断是否达到年化收益、最大回撤、Sharpe/Calmar、theta/vega 目标，以及是否接近乐得画像。
2. 策略定义：写清楚品种池、期限、Delta、仓位、止损、到期、费用、保证金、成交口径。
3. 总体绩效：NAV、累计收益、年化收益、最大回撤、波动、Sharpe、Calmar、手续费和保证金使用率。
4. 年度/月度拆解：说明收益是否稳定，关键亏损月份发生了什么。
5. 尾部日期拆解：列最差日，解释是否由跳空、升波、止损簇集、P/C 偏移或板块集中导致。
6. Greek 与 PnL 归因：必须拆 delta、gamma、theta、vega、residual。卖权报告要特别回答：theta 是否足够厚，vega 为什么为正或为负，gamma 是否吞掉收益。
7. 平仓路径归因：到期、止损、期末持仓分别贡献多少；止损亏损是否集中在少数品种或月份。
8. 品种/板块归因：列赚钱和亏钱品种、板块贡献、集中度，以及是否“全品种但实际不分散”。
9. P/C 与结构画像：解释双卖是否实际单侧化，P/C 极端偏离是否让策略变成方向仓。
10. 与目标/乐得画像差距：从收益效率、回撤修复、持仓颗粒度、P/C 结构、降波阶段仓位、品种覆盖比较。
11. 结论和下一步实验：给出 3-7 个清晰、可回测、非过拟合导向的下一步。

## B0 对照报告模式

当用户要求“B1 和 B0 对比”“新版本和基准对比”“对照报告”“相对 B0 看效果”时，进入 B0 对照报告模式。

对照原则：
- B0 是基准，其他版本必须相对 B0 解释，不得只写新版本绝对表现。
- 必须固定共同截止日。如果新版本未跑完，只能把 B0 截到同一天比较，并明确写出共同区间。
- 必须同时判断三件事：收益是否改善、卖波质量是否改善、风险是否只是被放大。
- 如果新版本 NAV 更好但 vega/gamma/stress loss/止损簇集更差，必须写成“净值改善但卖波质量未必改善”，不能直接判定策略升级成功。
- 必须拆解净值改善来源：更高保证金、更厚 theta、更大 residual、方向收益、P/C 偏移、品种结构变化、成交/费用变化、尾部样本尚未经历。
- 必须给出“可作为下一版主线 / 只作为诊断线 / 暂不采用”的明确结论。

生成 B0 对照分析包时，优先使用：

```powershell
python .\scripts\analyze_backtest_outputs.py `
  --tag <candidate_tag> `
  --baseline-tag <b0_tag> `
  --out-dir .\output\analysis_<candidate_tag> `
  --candidate-label <candidate_label> `
  --baseline-label B0
```

如果候选版本尚未跑完，但已有阶段性 NAV 或 orders，可以用 `--nav`、`--orders`、`--baseline-nav`、`--baseline-orders` 显式指定文件；报告必须写清楚这是阶段性共同区间，不是完整样本结论。

对照报告的必备表格或段落：
- B0 vs 新版本核心绩效：NAV、累计收益、年化收益、最大回撤、最差单日、Sharpe、Calmar、手续费。
- 仓位与结构：平均/峰值保证金、平均品种数、平均合约数、平均手数、P/C 均值和极值。
- Greek 归因差异：Delta、Gamma、Theta、Vega、Residual 的累计值和相对变化。
- 权利金质量差异：新开仓毛/净权利金、`vega loss / gross premium`、`gamma loss / gross premium`、`premium retained ratio`、`S1 PnL / gross premium`。
- 尾部风险差异：最差 10 日、最差月份、止损次数、止损簇集、tail contribution、stress loss。
- 品种和方向差异：Top 产品、亏损产品、Put/Call 贡献、商品/ETF/股指权重。
- 成交可交易性差异：成交量/OI 分层、低流动性合约占比、费用占权利金比例。

对照图表必须以 B0 为基准输出。若分析脚本尚未生成，应新增或调用对照绘图脚本，至少产出：
- `compare_01_nav_relative_to_b0.png`：B0 与新版本 NAV 叠加，以及新版本相对 B0 的超额 NAV/超额收益。
- `compare_02_drawdown_relative_to_b0.png`：两条线回撤对比，并标注共同区间最差回撤日期。
- `compare_03_margin_position_relative_to_b0.png`：保证金、持仓品种、合约数、手数对比，判断是否只是仓位更厚。
- `compare_04_greek_attribution_relative_to_b0.png`：Theta、Vega、Gamma、Delta、Residual 的累计差异，重点看收益改善是否伴随更差 short vega/short gamma。
- `compare_05_daily_pnl_tail_relative_to_b0.png`：日度 PnL 分布、最差日和左尾分位对比。
- `compare_06_pc_structure_relative_to_b0.png`：P/C、Call lot share、Put/Call 贡献差异，判断是否变成方向仓。
- `compare_07_product_exposure_relative_to_b0.png`：Top 产品占比和产品贡献差异，判断是否真实分散。
- `compare_08_stop_cluster_relative_to_b0.png`：止损次数、止损金额、止损簇集和关键尾部日对比。

对照图表解读方式仍按五段式：怎么看 / 图上读数 / 期权专家判断 / 风险或口径疑点 / 下一步验证。每张图都必须回答“相对 B0，这张图改变了我们对新版本的什么判断”。

## 必嵌入图表

如果文件存在，必须插入并讲解：

- `01_nav_drawdown.png`
- `02_margin_positions.png`
- `03_greeks_timeseries.png`
- `04_pnl_attribution.png`
- `05_daily_pnl_tail.png`
- `06_premium_pc_structure.png`
- `07_vol_regime_exposure.png`
- `08_calendar_returns.png`
- `09_product_share_top10.png`
- `10_order_action_summary.png`
- `11_close_event_timeline.png`
- `12_daily_open_premium_vega_quality.png`
- `13_tail_product_side_contribution.png`
- `14_vega_quality_by_bucket.png`
- `15_stop_slippage_distribution.png`
- `16_pc_funnel.png`

## 图表讲解标准

每张图后至少写五段，不能只复述标题，也不能只使用脚本 `chart_observation` 里的机器快读。机器快读只能作为取数参考，不能作为正文结论。

- 怎么看：这张图应该关注什么。
- 图上读数：结合 CSV 指标给出本次具体数字、日期、峰值、低点、占比或异常点。
- 期权专家判断：解释这些现象对应的卖权经济含义，例如 theta 是否覆盖 gamma、降波阶段是否给足预算、P/C 是否偏成方向仓、止损是否像真实风险还是报价跳变。
- 风险或口径疑点：指出图表不能解释的部分、可能的实现口径、成交假设、Greek 模型、IV 口径或数据异常。
- 下一步验证：给出可以通过脚本、拆解或对照实验验证的动作。

若图表是多子图，必须逐个子图说明，不得只概括整张图。若图片内容和 CSV 指标矛盾，以 CSV 为准并提示需要复核绘图脚本。

详细图表问题清单见 `references/chart-checklist.md`。写报告前应读取该文件。

## 深度图表分析写作要求

正式报告必须在正文中设置“图表深读”或将图表分析嵌入对应章节。每张核心图至少回答以下判断问题：

- 这张图改变了我们对策略质量的什么判断，而不是“图上有什么”。
- 这个现象是收益来源、风险暴露、交易摩擦、样本路径，还是回测口径导致的。
- 如果图显示改善，必须说明它是否来自更高仓位、更厚权利金、更好 vega 控制、更低 tail loss，还是只是阶段性方向。
- 如果图显示变差，必须说明它会影响哪条策略规则：入场、选腿、仓位、组合约束、退出、止损或数据清洗。
- 对 B0/B1 对比图，必须明确“新版本相对 B0 的增量判断”，不能分别描述两条线。

写作时可以引用自动附录里的图片，但不能把自动附录中的 `机器快读 - 本次观察` 原样当正文。正文应体现期权专家的推理过程和取舍。

## 归因审计要求

- 主 NAV 归因必须检查 `delta + gamma + theta + vega + residual` 是否闭合到 S1 PnL。
- 如果存在 `core_audit`，必须比较主归因和独立审计方向是否一致。
- 如果 vega 为负，不能简单写“波动率上升导致亏损”，必须区分：真实升波、到期/近到期 IV 口径、异常报价、止损路径、方向导致的隐含波动变化。
- 如果 `vega loss / gross premium` 较高，必须把它作为核心问题。S1 的目标不是最大化毛权利金，而是最大化权利金留存率和风险调整后的正 theta/正 vega。
- 如果新版本比 B0 收取了更多权利金但 `premium retained ratio` 没有提升，或 vega/gamma 吞噬率恶化，必须写成“保险费收入增加但承保质量未改善”。
- Residual 不得直接当作 alpha；要解释它可能来自离散重估、近到期非线性、skew/vanna/volga、成交和模型误差。
- 如果策略收益优于基准但 vega/gamma 更差，必须写成“净值改善但卖波质量未必改善”，并说明可能只是方向、残差、仓位或样本路径带来的阶段性优势。
- 如果策略回撤较小但保证金使用率也更低，要区分是风控有效还是仓位不足。
- 如果保证金使用率更高但 stress loss、最差日或止损簇集恶化，不能只写“资金效率提升”。

## DOCX 生成

项目中优先使用：

```powershell
python .\scripts\build_s1_report_docx.py `
  --tag <tag> `
  --markdown .\docs\<report>.md `
  --analysis-dir .\output\analysis_<tag> `
  --output-docx .\output\analysis_<tag>\<report>_feishu.docx `
  --skip-markdown-images
```

如果默认 Python 缺少 `python-docx`，使用 Codex bundled Python。

DOCX 是飞书导入优先格式；Markdown 仍保留作为版本控制和审阅用。

图片导入规则：

- Markdown 可以保留 `![图名](../output/analysis_<tag>/xx.png)`，便于本地预览和版本审阅。
- 生成飞书 DOCX 时，默认加 `--skip-markdown-images`，并且不要加 `--skip-existing-chart-section`。这样 DOCX 图片走第一版的显式图表附录导入方式，由 `CHART_SPECS` 逐张从 `analysis_dir` 嵌入。
- 如果报告正文已经逐图写了深度解读，DOCX 里的正文位置会保留 `[图表位置]` 文本，真正图片集中放在后面的“图表展示附录（机器快读，不替代正文深度分析）”。这比依赖 Markdown 图片路径更适合飞书导入。
- 只有在用户明确要求“图片必须出现在正文原位置”时，才考虑不使用 `--skip-markdown-images`；这种模式需要额外做飞书导入验证。

## 写作风格

- 用中文。
- 先结论后证据。
- 管理层版要克制但不能浅：少写操作过程，多写判断、证据、影响、风险疑点和下一步验证。
- 不要把高胜率包装成好策略；卖权报告必须强调左尾、止损簇集、保证金挤兑和升波阶段。
- 不要只说“参数可能需要优化”；要提出可以验证的结构性假设。
- 不要把图表当装饰。每张图必须服务一个研究判断，图下解读要能独立回答“这张图改变了我们对策略的什么判断”。
