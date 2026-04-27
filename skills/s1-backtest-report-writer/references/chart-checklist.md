# S1 报告图表检查清单

报告插图不只是附录。每张图都需要回答一个研究问题：

| 图表 | 研究问题 |
|---|---|
| NAV + Drawdown | 收益是否平滑，回撤是否集中，是否达到目标收益/回撤 |
| Margin + Positions | 是否真的用了仓位，还是规则导致低暴露 |
| Greeks Timeseries | cash Greeks 是否长期偏向单侧，尾部前是否已预警 |
| PnL Attribution | 收益来自 theta/vega 还是 delta 侥幸 |
| Daily PnL Tail | 左尾是否吞掉多月收益 |
| Premium + P/C | 双卖是否实际变成单边方向仓 |
| Vol Regime Exposure | falling/low/high/post-stop 状态的仓位是否合理 |
| Calendar Returns | 哪些月份决定全年收益和回撤 |
| Product Share Top10 | 全品种扫描是否真实分散 |
| Order Action Summary | 持有到期、止损、期末持仓分别占比 |
| Close Event Timeline | 止损是否簇集，是否需要冷却和重开规则 |

最低标准：图下必须写“怎么看 / 本次观察 / 策略含义”。
