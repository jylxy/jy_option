# S1 paper-account state contract

Date: 2026-05-29

## Purpose

The paper order generator records paper account state before each daily run.
The current line generates external S1 sell intents and uses Toolkit minute
replay for paper fills, pending orders, reroute, expiry, margin, and overlay
stop diagnostics.

## Runtime location

Daily account-state files live under `s1_paper_trading_prepare/state/` and are
runtime artifacts.  They are ignored by GitHub except for `.gitkeep`.

Expected daily files use `YYYYMMDD` tags:

```text
state/
  nav/nav_state_YYYYMMDD.csv
  positions/positions_YYYYMMDD.csv
  pending_orders/pending_orders_YYYYMMDD.csv
  fills/fills_YYYYMMDD.csv
```

Template files are kept under `templates/account_state/`.

Initialize a daily set of account-state files:

```powershell
python s1_paper_trading_prepare/scripts/init_account_state.py --as-of-date 2026-05-29 --nav 50000000
```

## Tables

| Table | Required columns | Notes |
| --- | --- | --- |
| NAV state | `as_of_date`, `nav`, `cash`, `available_cash`, `margin_used` | One row per valuation date is enough. |
| Positions | `as_of_date`, `strategy`, `product`, `code`, `quantity`, `mark_price`, `margin` | Use signed quantity from the paper account. |
| Pending orders | `signal_date`, `execute_date`, `strategy`, `code`, `action`, `quantity`, `status`, `remaining_quantity` | Carries unfilled or partially filled orders into the next run. |
| Fills | `trade_date`, `strategy`, `code`, `action`, `filled_quantity`, `fill_price`, `fee` | T+1 execution feedback and manual fills. |

All rows must be strategy `S1`.  Missing rows are represented by an empty file
with the header, not by deleting the table.

## Validation

Validate one date:

```powershell
python s1_paper_trading_prepare/scripts/validate_account_state.py --as-of-date 2026-05-29 --require-files
```

The validator writes:

```text
output/audit/account_state_validation_YYYYMMDD.json
```

## Daily pipeline

Run the end-of-day pipeline:

```powershell
python s1_paper_trading_prepare/scripts/run_daily_paper_pipeline.py --signal-date 2026-05-29 --require-account-state
```

The pipeline validates account state, refreshes Toolkit daily inputs, generates
the T close mark and daily return, generates T+1 paper orders, and writes a run
manifest under `output/audit/`.

Run only the close mark:

```powershell
python s1_paper_trading_prepare/scripts/mark_close_pnl.py --as-of-date 2026-05-29
```

The close mark writes:

```text
output/audit/close_mark_positions_YYYYMMDD.csv
output/audit/close_mark_summary_YYYYMMDD.json
```

For forced exits and expiry replay, stale marks are not treated as executable
prices.  Expiry settlement uses same-day underlying close to compute intrinsic
value and is blocked with diagnostics if that price is missing.

The main-sleeve pre-expiry ITM fallback uses the marked T close state only as a
signal source.  If a main monthly short has `DTE < 2` and positive intrinsic
value at T close, the daily order generator emits a T+1 `buy_close` plan.  The
minute replay still requires a fresh T+1 option mark before closing; stale marks
are never used as executable fallback prices.
