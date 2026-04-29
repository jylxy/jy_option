#!/usr/bin/env python3
"""Build extra full-shadow factor-layer plots from existing CSV outputs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DEFAULT_LEVELS = ("contract", "product_side", "product")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--level", action="append", choices=list(DEFAULT_LEVELS))
    return parser.parse_args()


def configure_plot_style() -> None:
    plt.rcParams["figure.dpi"] = 120
    plt.rcParams["savefig.dpi"] = 150
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    return pd.read_csv(path)


def top_factors(ic_summary: pd.DataFrame, label: str, top_n: int) -> list[str]:
    if ic_summary.empty or "label" not in ic_summary.columns:
        return []
    data = ic_summary[ic_summary["label"].eq(label)].copy()
    if data.empty:
        return []
    data["rank_key"] = pd.to_numeric(data.get("mean_ic"), errors="coerce").abs()
    return data.sort_values("rank_key", ascending=False)["factor"].head(top_n).tolist()


def plot_cumulative_ic(level_dir: Path, top_n: int) -> None:
    daily = read_csv(level_dir / "factor_ic_daily_net_pnl.csv")
    summary = read_csv(level_dir / "factor_ic_summary.csv")
    if daily.empty or summary.empty:
        return
    factors = top_factors(summary, "future_net_pnl_per_premium", top_n)
    if not factors:
        return
    daily = daily[daily["factor"].isin(factors)].copy()
    daily["signal_date"] = pd.to_datetime(daily["signal_date"], errors="coerce")
    daily["ic"] = pd.to_numeric(daily["ic"], errors="coerce")
    daily = daily.dropna(subset=["signal_date", "ic"]).sort_values(["factor", "signal_date"])
    if daily.empty:
        return

    fig, ax = plt.subplots(figsize=(11, 6))
    for factor, group in daily.groupby("factor", sort=False):
        group = group.sort_values("signal_date")
        ax.plot(group["signal_date"], group["ic"].cumsum(), label=factor, linewidth=1.5)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(f"{level_dir.name}: cumulative Rank IC for net PnL / premium")
    ax.set_ylabel("Cumulative IC")
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(level_dir / "06_cum_ic_net_pnl.png")
    plt.close(fig)


def plot_stop_rate_by_layer(level_dir: Path, top_n: int) -> None:
    layers = read_csv(level_dir / "factor_layer_summary.csv")
    spread = read_csv(level_dir / "factor_spread_summary.csv")
    if layers.empty or spread.empty or "future_stop_rate" not in layers.columns:
        return
    if "future_stop_rate_good_minus_bad" in spread.columns:
        spread["rank_key"] = pd.to_numeric(spread["future_stop_rate_good_minus_bad"], errors="coerce").abs()
    elif "future_retained_ratio_good_minus_bad" in spread.columns:
        spread["rank_key"] = pd.to_numeric(spread["future_retained_ratio_good_minus_bad"], errors="coerce").abs()
    else:
        return
    factors = spread.sort_values("rank_key", ascending=False)["factor"].head(top_n).tolist()
    if not factors:
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    for factor in factors:
        group = layers[layers["factor"].eq(factor)].copy()
        if group.empty:
            continue
        group["layer"] = pd.to_numeric(group["layer"], errors="coerce")
        group["future_stop_rate"] = pd.to_numeric(group["future_stop_rate"], errors="coerce")
        group = group.dropna(subset=["layer", "future_stop_rate"]).sort_values("layer")
        if group.empty:
            continue
        ax.plot(group["layer"], group["future_stop_rate"], marker="o", linewidth=1.5, label=factor)
    ax.set_title(f"{level_dir.name}: stop rate by factor layer")
    ax.set_xlabel("Layer, Q1 low quality -> Q5 high quality")
    ax.set_ylabel("Future stop rate")
    ax.set_xticks(sorted(pd.to_numeric(layers["layer"], errors="coerce").dropna().unique()))
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(level_dir / "07_stop_rate_by_layer.png")
    plt.close(fig)


def plot_layer_spread_bar(level_dir: Path, top_n: int) -> None:
    spread = read_csv(level_dir / "factor_spread_summary.csv")
    if spread.empty or "future_retained_ratio_good_minus_bad" not in spread.columns:
        return
    data = spread.copy()
    data["spread"] = pd.to_numeric(data["future_retained_ratio_good_minus_bad"], errors="coerce")
    data = data.dropna(subset=["spread"])
    if data.empty:
        return
    top_good = data.sort_values("spread", ascending=False).head(max(top_n // 2, 4))
    top_bad = data.sort_values("spread", ascending=True).head(max(top_n // 2, 4))
    plot_df = pd.concat([top_good, top_bad], ignore_index=True).drop_duplicates("factor")
    plot_df = plot_df.sort_values("spread")
    colors = np.where(plot_df["spread"] >= 0, "#245f73", "#9a4d42")

    fig, ax = plt.subplots(figsize=(11, max(5, 0.35 * len(plot_df))))
    ax.barh(plot_df["factor"], plot_df["spread"], color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title(f"{level_dir.name}: Q5-Q1 retained ratio spread")
    ax.set_xlabel("Good-minus-bad retained ratio")
    fig.tight_layout()
    fig.savefig(level_dir / "01_factor_spread_cumulative.png")
    plt.close(fig)


def build_level(level_dir: Path, top_n: int) -> None:
    if not level_dir.exists():
        return
    plot_layer_spread_bar(level_dir, top_n)
    plot_cumulative_ic(level_dir, top_n)
    plot_stop_rate_by_layer(level_dir, top_n)


def main() -> None:
    args = parse_args()
    configure_plot_style()
    for level in args.level or DEFAULT_LEVELS:
        build_level(args.analysis_dir / level, args.top_n)
    print(f"Wrote extra plots under {args.analysis_dir}")


if __name__ == "__main__":
    main()
