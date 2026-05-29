"""Paper-account state contracts and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .diagnostics import write_json
from .paths import PROJECT_DIR, resolve_path


DEFAULT_STATE_DIR = PROJECT_DIR / "state"


ACCOUNT_STATE_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "nav_state": {
        "required": ["as_of_date", "nav", "cash", "available_cash", "margin_used"],
        "numeric": ["nav", "cash", "available_cash", "margin_used", "gross_option_market_value"],
    },
    "positions": {
        "required": ["as_of_date", "strategy", "product", "code", "quantity", "mark_price", "margin"],
        "numeric": ["quantity", "avg_price", "mark_price", "multiplier", "margin", "delta", "gamma", "vega", "theta"],
    },
    "pending_orders": {
        "required": [
            "signal_date",
            "execute_date",
            "strategy",
            "code",
            "action",
            "quantity",
            "status",
            "remaining_quantity",
        ],
        "numeric": ["quantity", "limit_price", "remaining_quantity", "strike"],
    },
    "fills": {
        "required": ["trade_date", "strategy", "code", "action", "filled_quantity", "fill_price", "fee"],
        "numeric": ["filled_quantity", "fill_price", "fee", "strike"],
    },
}

ACCOUNT_STATE_COLUMNS: dict[str, list[str]] = {
    "nav_state": [
        "as_of_date",
        "nav",
        "cash",
        "available_cash",
        "margin_used",
        "gross_option_market_value",
        "realized_pnl_mtd",
        "unrealized_pnl",
        "source",
        "updated_at",
    ],
    "positions": [
        "as_of_date",
        "strategy",
        "product",
        "code",
        "option_type",
        "strike",
        "expiry",
        "quantity",
        "avg_price",
        "mark_price",
        "multiplier",
        "margin",
        "delta",
        "gamma",
        "vega",
        "theta",
        "open_signal_date",
    ],
    "pending_orders": [
        "signal_date",
        "execute_date",
        "strategy",
        "product",
        "code",
        "option_type",
        "strike",
        "expiry",
        "action",
        "quantity",
        "limit_price",
        "status",
        "remaining_quantity",
        "source_order_id",
        "note",
    ],
    "fills": [
        "trade_date",
        "strategy",
        "product",
        "code",
        "option_type",
        "strike",
        "expiry",
        "action",
        "filled_quantity",
        "fill_price",
        "fee",
        "order_id",
        "fill_id",
        "source",
        "note",
    ],
}


@dataclass(frozen=True)
class AccountStateValidationResult:
    as_of_date: str
    state_dir: Path
    files: dict[str, str | None]
    row_counts: dict[str, int]
    issues: list[dict[str, Any]]
    summary: dict[str, Any]
    manifest_path: Path | None

    @property
    def ok(self) -> bool:
        return not self.issues


def _date_tag(date: str) -> str:
    return str(date)[:10].replace("-", "")


def expected_account_state_files(state_dir: Path, as_of_date: str) -> dict[str, Path]:
    tag = _date_tag(as_of_date)
    return {
        "nav_state": state_dir / "nav" / f"nav_state_{tag}.csv",
        "positions": state_dir / "positions" / f"positions_{tag}.csv",
        "pending_orders": state_dir / "pending_orders" / f"pending_orders_{tag}.csv",
        "fills": state_dir / "fills" / f"fills_{tag}.csv",
    }


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _validate_frame(name: str, frame: pd.DataFrame, path: Path, required: bool) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    schema = ACCOUNT_STATE_SCHEMAS[name]
    if not path.exists():
        if required:
            issues.append({"table": name, "issue": "missing_file", "path": str(path)})
        return issues
    if required and name == "nav_state" and frame.empty:
        issues.append({"table": name, "issue": "empty_nav_state", "path": str(path)})

    missing = [col for col in schema["required"] if col not in frame.columns]
    if missing:
        issues.append({"table": name, "issue": "missing_columns", "path": str(path), "columns": missing})
    for col in schema.get("numeric", []):
        if col not in frame.columns:
            continue
        values = pd.to_numeric(frame[col], errors="coerce")
        invalid = int(frame[col].notna().sum() - values.notna().sum())
        if invalid > 0:
            issues.append({"table": name, "issue": "invalid_numeric", "path": str(path), "column": col, "rows": invalid})
    if "strategy" in frame.columns:
        non_s1 = frame["strategy"].fillna("").astype(str).str.upper().ne("S1") & frame["strategy"].notna()
        non_s1_count = int(non_s1.sum())
        if non_s1_count > 0:
            issues.append({"table": name, "issue": "non_s1_rows", "path": str(path), "rows": non_s1_count})
    return issues


def validate_account_state(
    as_of_date: str,
    *,
    state_dir: str | Path | None = None,
    require_files: bool = False,
    output_dir: str | Path | None = None,
    write_manifest: bool = True,
) -> AccountStateValidationResult:
    """Validate the paper-account files for one date without mutating them."""
    date = str(as_of_date)[:10]
    root = resolve_path(state_dir, default=DEFAULT_STATE_DIR)
    files = expected_account_state_files(root, date)
    frames = {name: _read_optional_csv(path) for name, path in files.items()}
    issues: list[dict[str, Any]] = []
    for name, path in files.items():
        issues.extend(_validate_frame(name, frames[name], path, required=require_files))

    nav_frame = frames["nav_state"]
    nav = None
    margin_used = None
    if not nav_frame.empty:
        nav = pd.to_numeric(nav_frame.get("nav"), errors="coerce").dropna()
        margin_used = pd.to_numeric(nav_frame.get("margin_used"), errors="coerce").dropna()
    summary = {
        "nav": float(nav.iloc[-1]) if nav is not None and not nav.empty else None,
        "margin_used": float(margin_used.iloc[-1]) if margin_used is not None and not margin_used.empty else None,
        "open_position_rows": int(len(frames["positions"])),
        "pending_order_rows": int(len(frames["pending_orders"])),
        "fill_rows": int(len(frames["fills"])),
    }
    if summary["nav"] and summary["margin_used"] is not None:
        summary["margin_used_pct_nav"] = summary["margin_used"] / summary["nav"]
    else:
        summary["margin_used_pct_nav"] = None

    manifest_path = None
    if write_manifest:
        out_root = resolve_path(output_dir, default=PROJECT_DIR / "output" / "audit")
        manifest_path = out_root / f"account_state_validation_{_date_tag(date)}.json"
        write_json(
            manifest_path,
            {
                "as_of_date": date,
                "state_dir": str(root),
                "files": {name: str(path) for name, path in files.items()},
                "row_counts": {name: int(len(frame)) for name, frame in frames.items()},
                "issues": issues,
                "summary": summary,
                "ok": not issues,
            },
        )

    return AccountStateValidationResult(
        as_of_date=date,
        state_dir=root,
        files={name: str(path) if path.exists() else None for name, path in files.items()},
        row_counts={name: int(len(frame)) for name, frame in frames.items()},
        issues=issues,
        summary=summary,
        manifest_path=manifest_path,
    )
