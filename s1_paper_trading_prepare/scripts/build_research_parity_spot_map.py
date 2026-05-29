from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from s1_paper_trading_prepare.src.diagnostics import write_csv


DEFAULT_REPORT_SLIM = (
    REPO_ROOT
    / "server_deploy"
    / "output"
    / "shadow_v3_datamart"
    / "s1_full_shadow_v3_71prod_20260522_rerun1"
    / "report_slim_contract_fields_enriched_s1_full_shadow_v3_71prod_20260522_rerun1.parquet"
)
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "product_side_panel" / "rolling_product_spot_map.csv"


def read_report_slim(path: Path) -> pd.DataFrame:
    columns = ["signal_date", "product", "underlying_price"]
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path, columns=columns)
    return pd.read_csv(path, usecols=lambda col: col in columns)


def build_spot_map(report_slim: pd.DataFrame) -> pd.DataFrame:
    required = {"signal_date", "product", "underlying_price"}
    missing = required - set(report_slim.columns)
    if missing:
        raise ValueError(f"report-slim file is missing columns: {sorted(missing)}")
    out = report_slim[["signal_date", "product", "underlying_price"]].copy()
    out["trade_date"] = pd.to_datetime(out["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["product"] = out["product"].astype(str).str.upper().str.strip()
    out["expiry_spot"] = pd.to_numeric(out["underlying_price"], errors="coerce")
    out = out.dropna(subset=["trade_date", "product", "expiry_spot"]).copy()
    out = out.drop_duplicates(["product", "trade_date"], keep="first")
    out["source"] = "report_slim_underlying_price_first"
    return out[["product", "trade_date", "expiry_spot", "source"]].sort_values(
        ["trade_date", "product"], kind="mergesort"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the research-parity product spot map used to replay locked "
            "S1 expiry-retention labels. This is for historical audit parity; "
            "live paper trading should maintain the same file from daily Toolkit snapshots."
        )
    )
    parser.add_argument("--report-slim", type=Path, default=DEFAULT_REPORT_SLIM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report_slim = read_report_slim(args.report_slim)
    spot_map = build_spot_map(report_slim)
    write_csv(args.output, spot_map)
    print(f"spot_map_path={args.output}")
    print(f"spot_map_rows={len(spot_map)}")
    print(f"date_min={spot_map['trade_date'].min() if not spot_map.empty else ''}")
    print(f"date_max={spot_map['trade_date'].max() if not spot_map.empty else ''}")
    print("source=report_slim_underlying_price_first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
