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
- `output/analysis_<tag>/core_audit/core_audit_<tag>.csv`，如存在则必须用于校验 Greek 归因。

## 期权专家写作内核

报告不是净值流水账。每一份 S1 报告都必须围绕以下问题展开：

- 这是不是一个合格的卖权/空波动率策略：收益是否主要来自正 theta 和正 vega，还是来自 delta 方向侥幸、residual 或更高尾部暴露。
- 这份 theta 是否“够厚”：按保证金、stress loss、尾部亏损和手续费之后，权利金是否足以补偿 gamma、vega、跳空和流动性风险。
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

## 图表讲解标准

每张图后至少写五段，不能只复述标题：

- 怎么看：这张图应该关注什么。
- 图上读数：结合 CSV 指标给出本次具体数字、日期、峰值、低点、占比或异常点。
- 期权专家判断：解释这些现象对应的卖权经济含义，例如 theta 是否覆盖 gamma、降波阶段是否给足预算、P/C 是否偏成方向仓、止损是否像真实风险还是报价跳变。
- 风险或口径疑点：指出图表不能解释的部分、可能的实现口径、成交假设、Greek 模型、IV 口径或数据异常。
- 下一步验证：给出可以通过脚本、拆解或对照实验验证的动作。

若图表是多子图，必须逐个子图说明，不得只概括整张图。若图片内容和 CSV 指标矛盾，以 CSV 为准并提示需要复核绘图脚本。

详细图表问题清单见 `references/chart-checklist.md`。写报告前应读取该文件。

## 归因审计要求

- 主 NAV 归因必须检查 `delta + gamma + theta + vega + residual` 是否闭合到 S1 PnL。
- 如果存在 `core_audit`，必须比较主归因和独立审计方向是否一致。
- 如果 vega 为负，不能简单写“波动率上升导致亏损”，必须区分：真实升波、到期/近到期 IV 口径、异常报价、止损路径、方向导致的隐含波动变化。
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
  --output-docx .\output\analysis_<tag>\<report>_feishu.docx
```

如果默认 Python 缺少 `python-docx`，使用 Codex bundled Python。

DOCX 是飞书导入优先格式；Markdown 仍保留作为版本控制和审阅用。

## 写作风格

- 用中文。
- 先结论后证据。
- 管理层版要克制但不能浅：少写操作过程，多写判断、证据、影响、风险疑点和下一步验证。
- 不要把高胜率包装成好策略；卖权报告必须强调左尾、止损簇集、保证金挤兑和升波阶段。
- 不要只说“参数可能需要优化”；要提出可以验证的结构性假设。
- 不要把图表当装饰。每张图必须服务一个研究判断，图下解读要能独立回答“这张图改变了我们对策略的什么判断”。
