# S1 experiment audit - s1_b2c_stop15_2022_latest

- Time: 2026-05-06T10:46:00
- Config: `config_s1_baseline_b2_product_tilt075_stop15.json`
- Status: `needs_code_fix`

## Findings

| Severity | Code | Message |
|---|---|---|
| critical | `intraday_exit_skips_daily_stop` | 盘中扫描即使没有真实平仓，也可能关闭日频止损兜底。 |
| warning | `warn_action_short_circuits_exit` | 分层止损的 warn 动作可能在未降风险时中断后续退出检查。 |
| warning | `negative_vega_pnl` | Vega PnL 为负: -5702706 |
