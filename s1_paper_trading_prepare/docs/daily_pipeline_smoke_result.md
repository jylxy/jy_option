# S1 daily pipeline smoke result

Date: 2026-05-29

## Scope

- Host: h200
- Signal date: 2022-04-14
- Account date: 2022-04-14
- Replay start date: 2022-01-04
- Paper NAV used for smoke: 50,000,000
- Account-state mode: required files

## Result

| Check | Result |
| --- | --- |
| Python compile | passed |
| S1-only scope check | passed |
| Account-state initialization | passed |
| Account-state validation | passed |
| Toolkit daily data refresh | passed |
| Order generation | passed |
| Generated orders | 1 |
| Execute date | 2022-04-15 |

## Conclusion

The daily paper pipeline can validate account state, refresh S1 daily data,
recompute derived tables, and generate T+1 planned orders in the h200 Toolkit
environment.  Runtime data, orders, diagnostics, and manifests were treated as
artifacts and removed after the smoke.
