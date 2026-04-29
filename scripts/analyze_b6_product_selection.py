#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit S1 product and product-side factors from the B5 full-shadow panels.

This script intentionally works above the contract layer. It joins T-day
product/product-side panels with T+1 forward shadow outcomes and answers:

1. Which products deserve more S1 budget?
2. Which product-side (Put/Call) deserves more budget?

It does not select strikes and does not use future labels as signals.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FactorSpec:
    name: str
    direction: str = "high"
    family: str = ""
    role: str = ""

    @property
    def display(self) -> str:
        return f"{self.name}_low" if self.direction == "low" else self.name


PRODUCT_FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("product_premium_sum", "high", "premium", "product_budget"),
    FactorSpec("product_candidate_count", "high", "liquidity", "product_budget"),
    FactorSpec("product_side_count", "high", "liquidity", "product_budget"),
    FactorSpec("product_premium_to_margin", "high", "capital", "product_budget"),
    FactorSpec("product_premium_to_stress", "high", "tail", "product_budget"),
    FactorSpec("product_theta_per_vega", "high", "vega", "product_budget"),
    FactorSpec("product_theta_per_gamma", "high", "gamma", "product_budget"),
    FactorSpec("product_stress_per_premium", "low", "tail", "risk_penalty"),
    FactorSpec("product_vega_per_premium", "low", "vega", "risk_penalty"),
    FactorSpec("product_gamma_per_premium", "low", "gamma", "risk_penalty"),
    FactorSpec("product_stress_share", "low", "concentration", "risk_penalty"),
    FactorSpec("product_margin_share", "low", "concentration", "risk_penalty"),
    FactorSpec("product_cooldown_penalty", "low", "cooldown", "risk_penalty"),
    FactorSpec("product_tail_dependence_max", "low", "tail_corr", "risk_penalty"),
    FactorSpec("product_tail_dependence_excess_max", "low", "tail_corr", "risk_penalty"),
    FactorSpec("product_tail_beta_abs_max", "low", "tail_corr", "risk_penalty"),
    FactorSpec("product_avg_delta_ratio_to_cap", "low", "delta", "diagnostic"),
    FactorSpec("product_avg_tail_coverage", "high", "tail", "diagnostic"),
)


PRODUCT_SIDE_FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("side_premium_sum", "high", "premium", "side_budget"),
    FactorSpec("side_candidate_count", "high", "liquidity", "side_budget"),
    FactorSpec("side_premium_to_margin", "high", "capital", "side_budget"),
    FactorSpec("side_premium_to_stress", "high", "tail", "side_budget"),
    FactorSpec("side_theta_per_vega", "high", "vega", "side_budget"),
    FactorSpec("side_theta_per_gamma", "high", "gamma", "side_budget"),
    FactorSpec("side_stress_per_premium", "low", "tail", "risk_penalty"),
    FactorSpec("side_vega_per_premium", "low", "vega", "risk_penalty"),
    FactorSpec("side_gamma_per_premium", "low", "gamma", "risk_penalty"),
    FactorSpec("side_trend_alignment", "high", "trend", "pc_budget"),
    FactorSpec("side_momentum_alignment", "high", "trend", "pc_budget"),
    FactorSpec("side_breakout_cushion", "high", "breakout", "pc_budget"),
    FactorSpec("side_iv_mom_5d", "low", "vol_regime", "risk_penalty"),
    FactorSpec("side_iv_accel", "low", "vol_regime", "risk_penalty"),
    FactorSpec("side_cooldown_penalty", "low", "cooldown", "risk_penalty"),
    FactorSpec("side_avg_tail_coverage", "high", "tail", "diagnostic"),
    FactorSpec("side_avg_abs_delta", "high", "delta", "diagnostic"),
    FactorSpec("side_avg_contract_iv_skew_to_atm", "high", "skew", "diagnostic"),
)


LABELS: tuple[str, ...] = (
    "future_pnl_per_premium",
    "future_pnl_per_margin",
    "future_pnl_per_stress",
    "future_retained_ratio",
    "future_stop_avoidance",
    "future_stop_loss_avoidance",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", default="output", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--bins", default=5, type=int)
    parser.add_argument("--min-cross-section", default=8, type=int)
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


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    out = num / den.where(den.abs() > 1e-12)
    return out.replace([np.inf, -np.inf], np.nan)


def safe_log1p(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return np.log1p(numeric.where(numeric >= 0.0))


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, **kwargs)


def aggregate_outcomes(outcomes: pd.DataFrame, group_cols: Sequence[str]) -> pd.DataFrame:
    numeric_cols = [
        "open_premium_cash",
        "future_net_pnl",
        "future_retained_premium",
        "future_stop_flag",
        "future_stop_loss",
        "future_days_held",
        "future_max_price_multiple",
        "future_expiry_itm_flag",
    ]
    for col in numeric_cols:
        if col in outcomes.columns:
            outcomes[col] = pd.to_numeric(outcomes[col], errors="coerce")
    grouped = outcomes.groupby(list(group_cols), dropna=False, sort=False)
    result = grouped.size().rename("outcome_candidate_count").to_frame()
    result["outcome_premium_sum"] = grouped["open_premium_cash"].sum(min_count=1)
    result["outcome_net_pnl_sum"] = grouped["future_net_pnl"].sum(min_count=1)
    result["outcome_retained_premium_sum"] = grouped["future_retained_premium"].sum(min_count=1)
    result["outcome_stop_count"] = grouped["future_stop_flag"].sum(min_count=1)
    result["outcome_stop_loss_sum"] = grouped["future_stop_loss"].sum(min_count=1)
    result["future_days_held_mean"] = grouped["future_days_held"].mean()
    result["future_max_price_multiple_mean"] = grouped["future_max_price_multiple"].mean()
    result["future_expiry_itm_rate"] = grouped["future_expiry_itm_flag"].mean()
    result = result.reset_index()

    premium = result["outcome_premium_sum"]
    count = result["outcome_candidate_count"].replace(0, np.nan)
    result["future_pnl_per_premium"] = safe_div(result["outcome_net_pnl_sum"], premium)
    result["future_retained_ratio"] = safe_div(result["outcome_retained_premium_sum"], premium)
    result["future_stop_rate"] = safe_div(result["outcome_stop_count"], count)
    result["future_stop_avoidance"] = 1.0 - result["future_stop_rate"]
    result["future_stop_loss_per_premium"] = safe_div(result["outcome_stop_loss_sum"], premium)
    # Higher is better: zero loss is better than a negative stop-loss sum.
    result["future_stop_loss_avoidance"] = -result["future_stop_loss_per_premium"].abs()
    return result


def add_product_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
    premium = df["product_premium_sum"]
    stress = df["product_stress_sum"]
    margin = df["product_margin_sum"]
    theta = df["product_cash_theta_sum"].abs()
    vega = df["product_cash_vega_sum"].abs()
    gamma = df["product_cash_gamma_sum"].abs()
    df["product_premium_depth"] = safe_log1p(premium)
    df["product_candidate_depth"] = safe_log1p(df["product_candidate_count"])
    df["product_premium_to_margin"] = safe_div(premium, margin)
    df["product_premium_to_stress"] = safe_div(premium, stress)
    df["product_theta_per_vega"] = safe_div(theta, vega)
    df["product_theta_per_gamma"] = safe_div(theta, gamma)
    df["product_stress_per_premium"] = safe_div(stress, premium)
    df["product_vega_per_premium"] = safe_div(vega, premium)
    df["product_gamma_per_premium"] = safe_div(gamma, premium)
    df["product_tail_dependence_max"] = df[
        ["b5_empirical_lower_tail_dependence_95", "b5_empirical_upper_tail_dependence_95"]
    ].max(axis=1, skipna=True)
    df["product_tail_dependence_excess_max"] = df[
        ["b5_lower_tail_dependence_excess", "b5_upper_tail_dependence_excess"]
    ].max(axis=1, skipna=True)
    df["product_tail_beta_abs_max"] = df[["b5_lower_tail_beta", "b5_upper_tail_beta"]].abs().max(axis=1, skipna=True)
    return df


def add_product_side_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
    premium = df["side_premium_sum"]
    stress = df["side_stress_sum"]
    margin = df["side_margin_sum"]
    theta = df["side_cash_theta_sum"].abs()
    vega = df["side_cash_vega_sum"].abs()
    gamma = df["side_cash_gamma_sum"].abs()
    df["side_premium_depth"] = safe_log1p(premium)
    df["side_candidate_depth"] = safe_log1p(df["side_candidate_count"])
    df["side_premium_to_margin"] = safe_div(premium, margin)
    df["side_premium_to_stress"] = safe_div(premium, stress)
    df["side_theta_per_vega"] = safe_div(theta, vega)
    df["side_theta_per_gamma"] = safe_div(theta, gamma)
    df["side_stress_per_premium"] = safe_div(stress, premium)
    df["side_vega_per_premium"] = safe_div(vega, premium)
    df["side_gamma_per_premium"] = safe_div(gamma, premium)

    is_put = df["option_type"].astype(str).str.upper().eq("P")
    trend = pd.to_numeric(df.get("b5_trend_z_20d"), errors="coerce")
    mom = pd.to_numeric(df.get("b5_mom_20d", trend), errors="coerce")
    up_dist = pd.to_numeric(df.get("b5_breakout_distance_up_60d"), errors="coerce")
    down_dist = pd.to_numeric(df.get("b5_breakout_distance_down_60d"), errors="coerce")
    df["side_trend_alignment"] = np.where(is_put, trend, -trend)
    df["side_momentum_alignment"] = np.where(is_put, mom, -mom)
    df["side_breakout_cushion"] = np.where(is_put, down_dist, up_dist)
    df["side_iv_mom_5d"] = pd.to_numeric(df.get("b5_atm_iv_mom_5d"), errors="coerce")
    df["side_iv_accel"] = pd.to_numeric(df.get("b5_atm_iv_accel"), errors="coerce")
    return df


def add_outcome_efficiency(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    df = df.copy()
    df["future_pnl_per_margin"] = safe_div(df["outcome_net_pnl_sum"], df[f"{prefix}_margin_sum"])
    df["future_pnl_per_stress"] = safe_div(df["outcome_net_pnl_sum"], df[f"{prefix}_stress_sum"])
    return df


def available_factors(df: pd.DataFrame, specs: Sequence[FactorSpec]) -> list[FactorSpec]:
    out = []
    for spec in specs:
        if spec.name not in df.columns:
            continue
        values = pd.to_numeric(df[spec.name], errors="coerce")
        if values.notna().sum() > 0 and values.nunique(dropna=True) > 1:
            out.append(spec)
    return out


def adjusted_factor(df: pd.DataFrame, spec: FactorSpec) -> pd.Series:
    values = pd.to_numeric(df[spec.name], errors="coerce")
    return -values if spec.direction == "low" else values


def daily_rank_ic(df: pd.DataFrame, factors: Sequence[FactorSpec], labels: Sequence[str], min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        factor = adjusted_factor(df, spec)
        for label in labels:
            if label not in df.columns:
                continue
            label_values = pd.to_numeric(df[label], errors="coerce")
            work = pd.DataFrame({"signal_date": df["signal_date"], "factor": factor, "label": label_values})
            work = work.replace([np.inf, -np.inf], np.nan).dropna()
            ics = []
            for _, group in work.groupby("signal_date", sort=True):
                if len(group) < min_cross_section:
                    continue
                if group["factor"].nunique() <= 1 or group["label"].nunique() <= 1:
                    continue
                ic = group["factor"].rank().corr(group["label"].rank())
                if np.isfinite(ic):
                    ics.append(float(ic))
            arr = pd.Series(ics, dtype=float)
            rows.append(
                {
                    "factor": spec.display,
                    "raw_factor": spec.name,
                    "direction": spec.direction,
                    "family": spec.family,
                    "role": spec.role,
                    "label": label,
                    "n_days": int(len(arr)),
                    "mean_ic": float(arr.mean()) if len(arr) else np.nan,
                    "median_ic": float(arr.median()) if len(arr) else np.nan,
                    "positive_ic_rate": float((arr > 0).mean()) if len(arr) else np.nan,
                    "t_stat": float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))))
                    if len(arr) > 2 and arr.std(ddof=1) > 0
                    else np.nan,
                }
            )
    return pd.DataFrame(rows)


def factor_layers(df: pd.DataFrame, factors: Sequence[FactorSpec], bins: int, min_cross_section: int) -> pd.DataFrame:
    rows = []
    label_cols = [label for label in LABELS if label in df.columns]
    for spec in factors:
        factor = adjusted_factor(df, spec)
        work = df[["signal_date"] + label_cols].copy()
        work["factor"] = factor
        work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=["factor"])
        layer_frames = []
        for _, group in work.groupby("signal_date", sort=True):
            valid = group.dropna(subset=["factor"])
            if len(valid) < max(min_cross_section, bins) or valid["factor"].nunique() <= 1:
                continue
            pct = valid["factor"].rank(method="first", pct=True)
            valid = valid.copy()
            valid["layer"] = np.ceil(pct * bins).clip(1, bins).astype(int)
            layer_frames.append(valid)
        if not layer_frames:
            continue
        layered = pd.concat(layer_frames, ignore_index=True)
        for layer, group in layered.groupby("layer", sort=True):
            row = {
                "factor": spec.display,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "family": spec.family,
                "role": spec.role,
                "layer": int(layer),
                "rows": int(len(group)),
            }
            for label in label_cols:
                row[label] = float(pd.to_numeric(group[label], errors="coerce").mean())
            rows.append(row)
    return pd.DataFrame(rows)


def spread_summary(layers: pd.DataFrame) -> pd.DataFrame:
    if layers.empty:
        return pd.DataFrame()
    rows = []
    max_layer = int(layers["layer"].max())
    for factor, group in layers.groupby("factor", sort=False):
        low = group[group["layer"] == 1]
        high = group[group["layer"] == max_layer]
        if low.empty or high.empty:
            continue
        row = {"factor": factor}
        for label in LABELS:
            if label in group.columns:
                row[f"{label}_q5_minus_q1"] = float(high[label].iloc[0] - low[label].iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


def factor_correlation(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    data = {}
    for spec in factors:
        data[spec.display] = adjusted_factor(df, spec)
    if not data:
        return pd.DataFrame()
    work = pd.DataFrame(data).replace([np.inf, -np.inf], np.nan)
    return work.corr(method="spearman", min_periods=30)


def plot_heatmap(matrix: pd.DataFrame, out_path: Path, title: str, cmap: str = "RdBu_r") -> None:
    if matrix.empty:
        return
    plt.figure(figsize=(max(8, 0.42 * len(matrix.columns)), max(5, 0.34 * len(matrix.index))))
    values = matrix.to_numpy(dtype=float)
    plt.imshow(values, aspect="auto", cmap=cmap, vmin=-np.nanmax(np.abs(values)), vmax=np.nanmax(np.abs(values)))
    plt.colorbar(label="value")
    plt.xticks(range(len(matrix.columns)), matrix.columns, rotation=75, ha="right", fontsize=7)
    plt.yticks(range(len(matrix.index)), matrix.index, fontsize=7)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_ic_heatmap(ic: pd.DataFrame, out_path: Path, title: str) -> None:
    if ic.empty:
        return
    pivot = ic.pivot_table(index="factor", columns="label", values="mean_ic", aggfunc="mean")
    plot_heatmap(pivot, out_path, title)


def plot_spread_bar(spread: pd.DataFrame, out_path: Path, title: str, metric: str) -> None:
    col = f"{metric}_q5_minus_q1"
    if spread.empty or col not in spread.columns:
        return
    data = spread[["factor", col]].dropna().sort_values(col, ascending=False)
    if data.empty:
        return
    data = pd.concat([data.head(12), data.tail(8)]).drop_duplicates("factor")
    plt.figure(figsize=(10, max(5, 0.32 * len(data))))
    colors = np.where(data[col] >= 0, "#2E7D32", "#B71C1C")
    plt.barh(data["factor"], data[col], color=colors)
    plt.axvline(0, color="black", linewidth=0.8)
    plt.title(title)
    plt.xlabel("Q5 - Q1")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_product_scatter(df: pd.DataFrame, out_path: Path) -> None:
    cols = ["product_premium_to_stress", "future_pnl_per_margin", "product", "outcome_premium_sum"]
    if any(col not in df.columns for col in cols):
        return
    work = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if work.empty:
        return
    sample = work.sample(min(len(work), 8000), random_state=7)
    size = np.sqrt(sample["outcome_premium_sum"].clip(lower=0)) / 3
    size = size.clip(5, 80)
    plt.figure(figsize=(9, 6))
    plt.scatter(sample["product_premium_to_stress"], sample["future_pnl_per_margin"], s=size, alpha=0.35)
    plt.axhline(0, color="black", linewidth=0.8)
    plt.title("Product budget: premium/stress vs future pnl/margin")
    plt.xlabel("product_premium_to_stress")
    plt.ylabel("future_pnl_per_margin")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def write_factor_catalog(factors: Sequence[FactorSpec], out_path: Path) -> None:
    pd.DataFrame(
        {
            "factor": [spec.display for spec in factors],
            "raw_factor": [spec.name for spec in factors],
            "direction": [spec.direction for spec in factors],
            "family": [spec.family for spec in factors],
            "role": [spec.role for spec in factors],
        }
    ).to_csv(out_path, index=False, encoding="utf-8-sig")


def analyze_level(name: str, df: pd.DataFrame, factors: Sequence[FactorSpec], args: argparse.Namespace, out_dir: Path) -> None:
    level_dir = out_dir / name
    level_dir.mkdir(parents=True, exist_ok=True)
    factors = available_factors(df, factors)
    df.to_csv(level_dir / f"{name}_dataset.csv", index=False)
    write_factor_catalog(factors, level_dir / "factor_catalog.csv")
    ic = daily_rank_ic(df, factors, LABELS, args.min_cross_section)
    ic.to_csv(level_dir / "factor_ic_summary.csv", index=False, encoding="utf-8-sig")
    layers = factor_layers(df, factors, args.bins, args.min_cross_section)
    layers.to_csv(level_dir / "factor_layer_summary.csv", index=False, encoding="utf-8-sig")
    spreads = spread_summary(layers)
    spreads.to_csv(level_dir / "factor_spread_summary.csv", index=False, encoding="utf-8-sig")
    corr = factor_correlation(df, factors)
    corr.to_csv(level_dir / "factor_correlation_matrix.csv", encoding="utf-8-sig")

    plot_ic_heatmap(ic, level_dir / "01_factor_ic_heatmap.png", f"{name}: rank IC by label")
    plot_spread_bar(spreads, level_dir / "02_spread_pnl_per_margin.png", f"{name}: Q5-Q1 pnl/margin", "future_pnl_per_margin")
    plot_spread_bar(spreads, level_dir / "03_spread_retention.png", f"{name}: Q5-Q1 retained ratio", "future_retained_ratio")
    plot_spread_bar(spreads, level_dir / "04_spread_stop_avoidance.png", f"{name}: Q5-Q1 stop avoidance", "future_stop_avoidance")
    plot_heatmap(corr, level_dir / "05_factor_correlation_matrix.png", f"{name}: factor correlation")


def main() -> None:
    args = parse_args()
    configure_plot_style()
    output_dir = args.output_dir
    out_dir = args.out_dir or output_dir / f"b6_product_selection_{args.tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    outcomes_path = output_dir / f"s1_candidate_outcomes_{args.tag}.csv"
    product_panel_path = output_dir / f"s1_b5_product_panel_{args.tag}.csv"
    side_panel_path = output_dir / f"s1_b5_product_side_panel_{args.tag}.csv"

    outcome_cols = [
        "candidate_id",
        "signal_date",
        "product",
        "option_type",
        "open_premium_cash",
        "future_net_pnl",
        "future_retained_premium",
        "future_stop_flag",
        "future_stop_loss",
        "future_days_held",
        "future_max_price_multiple",
        "future_expiry_itm_flag",
    ]
    outcomes = read_csv(outcomes_path, usecols=lambda col: col in set(outcome_cols))
    outcomes["signal_date"] = pd.to_datetime(outcomes["signal_date"], errors="coerce")

    product_panel = add_product_features(read_csv(product_panel_path))
    side_panel = add_product_side_features(read_csv(side_panel_path))

    product_labels = aggregate_outcomes(outcomes.copy(), ["signal_date", "product"])
    side_labels = aggregate_outcomes(outcomes.copy(), ["signal_date", "product", "option_type"])

    product = product_panel.merge(product_labels, on=["signal_date", "product"], how="inner")
    product = add_outcome_efficiency(product, "product")
    side = side_panel.merge(side_labels, on=["signal_date", "product", "option_type"], how="inner")
    side = add_outcome_efficiency(side, "side")

    product.to_csv(out_dir / "product_research_dataset.csv", index=False)
    side.to_csv(out_dir / "product_side_research_dataset.csv", index=False)

    analyze_level("product", product, PRODUCT_FACTORS, args, out_dir)
    analyze_level("product_side", side, PRODUCT_SIDE_FACTORS, args, out_dir)
    plot_product_scatter(product, out_dir / "06_product_premium_stress_scatter.png")

    manifest = pd.DataFrame(
        [
            {"item": "tag", "value": args.tag},
            {"item": "product_rows", "value": len(product)},
            {"item": "product_side_rows", "value": len(side)},
            {"item": "out_dir", "value": str(out_dir)},
        ]
    )
    manifest.to_csv(out_dir / "manifest.csv", index=False, encoding="utf-8-sig")
    print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
