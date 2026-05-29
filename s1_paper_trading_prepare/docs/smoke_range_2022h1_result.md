# S1 2022H1 range smoke result

Date: 2026-05-29

## Scope

- Signal window: 2022-01-01 to 2022-06-30
- Replay window: 2022-01-01 to 2022-07-01
- Reason for the extra replay day: 2022-06-30 signal orders execute on T+1.
- Baseline: locked S1 backtest orders from the 50m liquidity-10 mainline run.
- Comparison key: normalized S1 open orders by signal date, execution date,
  action, strategy, product, option code, option type, strike, expiry, and
  quantity.

## Result

| Metric | Value |
| --- | ---: |
| Generated orders | 104 |
| Baseline orders | 104 |
| Diff rows | 0 |
| Issue counts | none |
| Generated signal days | 33 |
| Baseline signal days | 33 |
| First signal day | 2022-04-08 |
| Last signal day | 2022-06-30 |

## Conclusion

The S1 paper-trading wrapper reproduced the locked backtest S1 order stream
exactly for the 2022H1 smoke window.  Raw generated/baseline/diff CSV outputs
were treated as runtime artifacts and are not part of the GitHub payload.
