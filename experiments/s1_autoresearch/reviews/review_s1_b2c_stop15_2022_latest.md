# S1 Autoresearch Review - s1_b2c_stop15_2022_latest

- timestamp: 2026-05-06T10:46:00
- decision: needs_rerun_after_code_fix
- 实验假设: 
- 公式变量: 
- 样本角色: 

## 主 Agent 审议

本实验按 S1 收益拆解公式评估：Premium Pool x Deployment Ratio x Retention Rate - Tail / Stop Loss - Cost / Slippage。
累计收益为 0.229789，年化收益为 0.0438711，最大回撤为 -0.022397。
相对基准累计超额为 。

## 期权策略专家审议

Theta PnL 为 9737840.88，Vega PnL 为 -5702706.14。
S1 候选不能只看 NAV：必须确认权利金留得住，并且 vega 不能长期为负。
如果实验靠提高权利金池改善 NAV，但同步放大 vega 损耗或止损尾部，它更像风险转移，不应直接视为 alpha。

## 代码专家审议

- [critical] intraday_exit_skips_daily_stop: 盘中扫描即使没有真实平仓，也可能关闭日频止损兜底。
- [warning] warn_action_short_circuits_exit: 分层止损的 warn 动作可能在未降风险时中断后续退出检查。
- [warning] negative_vega_pnl: Vega PnL 为负: -5702706

## Skeptic 审议

这份审议不是稳健性证明。候选仍需要共同截止日对比、样本外验证、成本敏感性和尾部时期拆解。
如果样本角色只是 sample 或 stress，不能因为单段表现好就升级为生产规则。

## 下一轮方向

- 先修实现路径并重跑，再解释绩效。
