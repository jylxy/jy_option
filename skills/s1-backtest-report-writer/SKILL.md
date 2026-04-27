---
name: s1-backtest-report-writer
description: 为 S1 卖权策略回测生成完整中文归因报告、管理层汇报稿和飞书可导入 DOCX。适用于需要分析 NAV、回撤、保证金、Greeks、PnL 归因、品种/板块、P/C 结构、止损路径，并把 output/analysis_* 图表嵌入报告且逐图讲解的任务。
---

# S1 Backtest Report Writer

## 触发场景

当用户要求“写回测报告”“做全面归因”“输出飞书文档”“把图表插入报告”“给领导汇报版”或复盘 S1/B0/F 系列回测结果时，使用本 skill。

## 必交付物

- 一份中文 Markdown 正文，放在项目 `docs/` 下。
- 一份飞书可导入的 `.docx`，优先放在 `output/analysis_<tag>/` 下。
- 报告必须嵌入 `output/analysis_<tag>/` 中的标准图表，并在每张图后写“怎么看 / 本次观察 / 策略含义”。
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

每张图后写三段：

- 怎么看：这张图应该关注什么。
- 本次观察：结合 CSV 指标给出本次具体数字或现象。
- 策略含义：它对下一版规则、风控或实验有什么启发。

## 归因审计要求

- 主 NAV 归因必须检查 `delta + gamma + theta + vega + residual` 是否闭合到 S1 PnL。
- 如果存在 `core_audit`，必须比较主归因和独立审计方向是否一致。
- 如果 vega 为负，不能简单写“波动率上升导致亏损”，必须区分：真实升波、到期/近到期 IV 口径、异常报价、止损路径、方向导致的隐含波动变化。
- Residual 不得直接当作 alpha；要解释它可能来自离散重估、近到期非线性、skew/vanna/volga、成交和模型误差。

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
- 管理层版要克制：少写过程，多写判断、证据、影响和下一步。
- 不要把高胜率包装成好策略；卖权报告必须强调左尾、止损簇集、保证金挤兑和升波阶段。
- 不要只说“参数可能需要优化”；要提出可以验证的结构性假设。
