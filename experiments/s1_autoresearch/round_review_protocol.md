# S1 Autoresearch Round Review Protocol

每一轮实验完成后，必须先审议，再决定下一轮。不能只根据 NAV 排名推进。

## 1. 主 agent

- 说明本轮改动：只改了配置、品种池、止损、排序、预算，还是涉及核心引擎。
- 对齐共同截止日，给出相对基准的 NAV、收益、最大回撤、Sharpe、Calmar。
- 标记实验对应的收益公式变量：`Premium Pool`、`Deployment Ratio`、`Retention Rate`、`Tail / Stop Loss`、`Cost / Slippage`。

## 2. 期权策略专家

- 判断策略是否仍然是低 delta 卖权、收权利金、空波动率、控风险。
- 判断收益来源：theta、vega、delta、gamma、residual 哪个在主导。
- 如果 vega PnL 为负，必须解释是选合约问题、升波环境、IV 口径、止损路径，还是异常报价。
- 如果收益靠 delta，必须判断这是合理的 P/C 偏移还是方向暴露。

## 3. 代码专家

- 检查是否存在未来函数、成交价不一致、保证金口径错误、到期结算错误、异常报价污染。
- 检查本轮配置是否真的只改变了声称改变的变量。
- 检查速度是否因为分钟扫描、止损逻辑或输出诊断显著退化。
- 若发现 critical 实现问题，本轮必须标记 `needs_rerun_after_code_fix`。

## 4. Skeptic

- 判断是否过拟合到单一时期、单一品种、单一方向或单一事件。
- 判断是否牺牲尾部换收益：更高 NAV 但更差 worst day、stop cluster、margin spike 或 vega loss。
- 判断是否需要 OOS、压力片段、成本敏感性或品种分层复验。

## 5. 报告 writer

- 必须触发标准图表包。
- 领导版报告必须解释每张图，而不是只插图。
- 报告必须给出下一轮 1-2 个可执行实验，且说明它们改善公式中的哪一项。

## 6. 决策

允许的决策只有：

- `keep_for_oos_validation`
- `keep_as_candidate`
- `discard_or_diagnose`
- `needs_rerun_after_code_fix`
- `diagnostic_only`

没有经过审计和复盘的实验，不能升级为新基准。
