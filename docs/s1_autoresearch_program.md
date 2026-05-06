# S1 Autoresearch 自动研究系统设计

## 1. 目标

本系统用于把 S1 纯卖权策略研究从“人工提出想法、手动跑回测、聊天里临时复盘”升级为可追踪、可审计、可复现的自动化研究闭环。

目标不变：

- 年化收益目标：不低于 6%。
- 最大回撤目标：不超过 2%。
- Vega 归因目标：累计 vega PnL 应为正，或至少不能依靠扩大 short vega 暴露换收益。
- 策略画像目标：接近乐得式卖权画像，即高分散、低 delta、收权利金、控制止损和尾部聚合。

## 2. 研究公式

所有实验必须先回到公式：

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

每个实验必须说明它试图改善哪一项：

| 变量 | 含义 | 常见实验位置 |
|---|---|---|
| Premium Pool | 可交易权利金池是否更厚 | 品种池、合约排序、期限选择、P/C 预算 |
| Deployment Ratio | 实际吃掉了多少可交易权利金 | 保证金预算、品种预算、重开规则 |
| Retention Rate | 权利金最终留存比例 | 止损、退出、合约质量、IV/RV/vega 控制 |
| Tail / Stop Loss | 尾部、gamma、vega、跳空和止损损耗 | 止损机制、组合风险、尾部相关、压力测试 |
| Cost / Slippage | 手续费、滑点、流动性折损 | 流动性过滤、成交口径、低价/tick 过滤 |

如果一个实验不能映射到这些变量之一，默认不允许进入自动队列。

## 3. 角色分工

每轮实验完成后必须经过四方审议。

| 角色 | 必须回答的问题 |
|---|---|
| 主 agent | 这次实验改了什么，结果相对基准如何，是否达到目标 |
| 期权策略专家 | 逻辑上是否仍是卖权、收权利金、空波动率、控风险；收益来自 theta/vega 还是方向或尾部 |
| 代码专家 | 是否可能有未来函数、实现 bug、执行口径、成交口径、保证金口径、速度妥协 |
| Skeptic | 是否过拟合、是否只吃某个样本段、是否样本外不稳、是否牺牲尾部换收益 |
| 报告 writer | 是否已经生成图表、Markdown、DOCX，且图表有逐图解释 |

自动系统不会代替这些判断，只会把判断结构化、强制落盘。

## 4. 实验生命周期

每个实验从 idea 到 decision 必须走完以下状态：

```text
proposed
→ configured
→ launched
→ running
→ completed
→ scored
→ audited
→ reviewed
→ kept / discarded / needs_rerun
```

状态定义：

| 状态 | 含义 |
|---|---|
| proposed | 只有研究想法，还未生成配置 |
| configured | 已生成回测配置和 tag |
| launched | 已启动回测 |
| running | 回测正在跑或有部分落盘 |
| completed | NAV/orders/diagnostics 已完整落盘 |
| scored | 已生成 scorecard |
| audited | 已完成实现和结果口径审计 |
| reviewed | 已完成四方审议 |
| kept | 可作为下一轮基准或主线候选 |
| discarded | 不进入下一轮 |
| needs_rerun | 有实现/口径疑点，修正后重跑 |

## 5. 样本纪律

每个实验必须标记样本角色：

- `sample`: 用于发现和初筛。
- `validation`: 用于候选确认。
- `oos`: 用于最终样本外检查。
- `stress`: 用于压力片段，例如 2022 年上半年、2022 年 7 月、2024 年 7 月、2025 年关税风波。

禁止只因为单一短样本 NAV 更高就保留实验。

## 6. 评分标准

Scorecard 至少记录：

- 总收益和年化收益。
- 最大回撤。
- Sharpe、Calmar。
- 最差单日、最差 5 日。
- 平均/峰值保证金使用率。
- 止损次数和止损 PnL。
- Delta/Gamma/Theta/Vega/Residual 归因。
- Vega PnL 是否为正。
- 相对基准的超额收益和超额回撤。
- 权利金池、权利金留存、止损损耗、成本损耗相关字段。

Keep 条件不是简单的 NAV 最高，而是：

```text
annual_return >= 6%
and abs(max_drawdown) <= 2%
and vega_pnl >= 0
and no critical implementation/audit issue
and improvement is not explained mainly by leverage, tail concentration, or sample luck
```

如果收益提高但回撤、vega、gamma 或尾部聚合显著恶化，应标记为 `diagnostic_only` 或 `needs_rerun`，不能直接 keep。

## 7. 允许自动改动的范围

第一阶段只允许自动系统生成或修改：

- 实验配置 JSON。
- 实验 idea JSON。
- 实验结果 TSV / JSON。
- 审计和审议 Markdown。
- 分析包和报告触发脚本。

第一阶段不允许自动修改：

- `src/toolkit_minute_engine.py`
- `src/strategy_rules.py`
- 保证金、成交、数据读取、IV/Greek 计算等核心引擎

核心引擎发现问题时，只能输出 `needs_code_fix` 审计结论，由人类/代码专家确认后再单独修复。

## 8. 与现有脚本的关系

自动研究系统复用现有能力：

- 回测：`src/toolkit_minute_engine.py`
- 图表分析：`scripts/analyze_backtest_outputs.py`
- S1 DOCX 报告：`scripts/build_s1_report_docx.py`
- 实验结果评分：`scripts/s1_experiment_scorecard.py`
- 实验口径审计：`scripts/s1_experiment_audit.py`
- 自动研究编排：`scripts/s1_autoresearch_runner.py`

## 9. 标准命令

初始化：

```bash
python scripts/s1_autoresearch_runner.py init
```

登记 idea：

```bash
python scripts/s1_autoresearch_runner.py add-idea experiments/s1_autoresearch/ideas/my_idea.json
```

生成配置：

```bash
python scripts/s1_autoresearch_runner.py configure --id my_idea
```

后台启动：

```bash
python scripts/s1_autoresearch_runner.py launch --id my_idea --background
```

完成后评分、审计、审议：

```bash
python scripts/s1_autoresearch_runner.py score --id my_idea
python scripts/s1_autoresearch_runner.py audit --id my_idea
python scripts/s1_autoresearch_runner.py review --id my_idea
```

生成图表和 DOCX 报告入口：

```bash
python scripts/s1_autoresearch_runner.py report --tag <tag> --baseline-tag <baseline_tag>
```

## 10. 每轮审议模板

每轮实验完成后，review 文档必须包含：

1. 实验假设。
2. 公式变量定位。
3. 相对基准结果。
4. 期权专家判断。
5. 代码专家判断。
6. Skeptic 判断。
7. 是否达到目标。
8. Keep / discard / needs_rerun 决策。
9. 下一轮最值得继续的 1-2 个方向。

## 11. 自动系统的硬性刹车

出现以下任一情况，即使 NAV 更好，也不能自动进入下一轮主线：

- 代码审计为 `critical` 或 `needs_code_fix`。
- Vega PnL 为负且没有清晰解释。
- 最大回撤超过目标太多，且收益改善主要来自保证金或尾部暴露放大。
- 超额收益只集中在单一月份、单一品种或单一 P/C 方向。
- 止损次数下降但最差止损亏损、gap 损失或止损后滑点显著恶化。
- 报告未能解释图表和归因，只有净值结论。

## 12. P5 当前教训

P5 止损机制实验说明，自动研究系统必须把“代码审计”放在评分前面。

示例：

- B1/B3 净值在部分阶段更高，但 2022 年 7 月出现接近 20% 回撤。
- 初步审计显示可能存在盘中止损未成交后跳过日频兜底的问题。
- 因此这类结果不能直接 keep，必须标记为 `needs_rerun_after_code_fix`。

这也是自动系统存在的意义：不是追高 NAV，而是防止我们被错误口径或尾部样本误导。
