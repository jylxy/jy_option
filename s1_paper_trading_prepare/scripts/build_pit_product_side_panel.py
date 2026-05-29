from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.diagnostics import write_csv, write_json
from s1_paper_trading_prepare.src.paths import DEFAULT_DATA_DIR, resolve_path


ADMISSION_PATTERN = re.compile(r"rolling_l1_admission_(\d{8})\.csv$")
DEFAULT_OUTPUT = "product_side_panel/rolling_product_side_panel_pit_asof.csv"


def _date_from_path(path: Path) -> str | None:
    match = ADMISSION_PATTERN.match(path.name)
    if not match:
        return None
    tag = match.group(1)
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def build_panel(data_dir: Path, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    product_side_dir = data_dir / "product_side_panel"
    parts: list[pd.DataFrame] = []
    scanned = 0
    for path in sorted(product_side_dir.glob("rolling_l1_admission_*.csv")):
        date = _date_from_path(path)
        if not date:
            continue
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        scanned += 1
        frame = pd.read_csv(path)
        if frame.empty or "date" not in frame.columns:
            continue
        current = frame[frame["date"].astype(str).str[:10].eq(date)].copy()
        if current.empty:
            continue
        current["date"] = date
        parts.append(current)
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts, ignore_index=True, sort=False)
    if "product" in panel.columns:
        panel["product"] = panel["product"].astype(str).str.upper().str.strip()
    if "side" in panel.columns:
        panel["side"] = panel["side"].astype(str).str.upper().str[:1]
    if "option_type" in panel.columns:
        panel["option_type"] = panel["option_type"].astype(str).str.upper().str[:1]
    sort_cols = [col for col in ["date", "product", "side"] if col in panel.columns]
    if sort_cols:
        panel = panel.sort_values(sort_cols).reset_index(drop=True)
    panel.attrs["scanned_files"] = scanned
    return panel


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a static point-in-time S1 product-side panel by freezing each "
            "date's own rolling_l1_admission rows. Use this for historical NAV "
            "replay; the full rolling_product_side_panel.csv is a live daily state file."
        )
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Daily data directory.")
    parser.add_argument("--start-date", default=None, help="Inclusive start date.")
    parser.add_argument("--end-date", default=None, help="Inclusive end date.")
    parser.add_argument(
        "--output",
        default=None,
        help=f"Output CSV path. Defaults to DATA_DIR/{DEFAULT_OUTPUT}.",
    )
    args = parser.parse_args()

    data_dir = resolve_path(args.data_dir)
    output = resolve_path(args.output) if args.output else data_dir / DEFAULT_OUTPUT
    panel = build_panel(data_dir, args.start_date, args.end_date)
    if panel.empty:
        raise SystemExit("no point-in-time admission files found for requested range")
    write_csv(output, panel)
    meta_path = output.with_suffix(".manifest.json")
    write_json(
        meta_path,
        {
            "output": str(output),
            "rows": int(len(panel)),
            "dates": int(panel["date"].nunique()) if "date" in panel.columns else 0,
            "min_date": str(panel["date"].min()) if "date" in panel.columns else None,
            "max_date": str(panel["date"].max()) if "date" in panel.columns else None,
            "scanned_files": int(panel.attrs.get("scanned_files", 0)),
            "source": "rolling_l1_admission_<YYYYMMDD>.csv current-date rows",
            "point_in_time_note": (
                "Each row is the product-side score and gate state as computed on its "
                "own signal date, before later label maturity can rewrite history."
            ),
        },
    )
    print(
        "PIT_PANEL_OK "
        f"rows={len(panel)} dates={panel['date'].nunique()} "
        f"min={panel['date'].min()} max={panel['date'].max()} path={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
