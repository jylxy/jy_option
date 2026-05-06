#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a B4 formula-oriented research pack.

This script is intentionally read-only with respect to backtest outputs. It
compares B4a/B4b/B4c against B1 and B2C using the S1 premium formula:

    premium pool * deployment * retention - tail/stop - cost/slippage

The charts use ASCII labels to avoid font issues on Windows and remote Linux.
The companion Markdown report can explain the same figures in Chinese.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


RUNS = [
    {
        "label": "B1",
        "tag": "s1_b1_liq_oi_rank_stop25_allprod_2022_latest",
        "role": "baseline_liquidity_oi_rank",
    },
    {
        "label": "B2C",
        "tag": "s1_b2_product_tilt075_stop25_allprod_2022_latest",
        "role": "premium_quality_product_tilt",
    },
    {
        "label": "B4a",
        "tag": "s1_b4a_dedup_contract_rank_stop25_allprod_2022_latest",
        "role": "contract_rank_only",
    },
    {
        "label": "B4b",
        "tag": "s1_b4b_dedup_contract_product_tilt_stop25_allprod_2022_latest",
        "role": "contract_rank_plus_product_tilt",
    },
    {
        "label": "B4c",
        "tag": "s1_b4c_dedup_role_layer_stop25_allprod_2022_latest",
        "role": "role_layer_plus_penalties",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build B4 formula research charts and tables.")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output/analysis_s1_b4_formula_research_20260429"),
    )
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def max_drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def safe_div(num: float, den: float) -> float:
    if den is None or float(den) == 0 or math.isnan(float(den)):
        return np.nan
    return float(num) / float(den)


def load_run(output_dir: Path, run: Dict[str, str]) -> Dict[str, object]:
    tag = run["tag"]
    nav = read_csv(output_dir / f"nav_{tag}.csv")
    orders = read_csv(output_dir / f"orders_{tag}.csv")
    summary_path = output_dir / f"analysis_{tag}" / "summary_metrics.csv"
    if summary_path.exists():
        summary = pd.read_csv(summary_path).iloc[0].to_dict()
    else:
        summary = {}
    nav["date"] = pd.to_datetime(nav["date"])
    orders["date"] = pd.to_datetime(orders["date"])
    if "signal_date" in orders.columns:
        orders["signal_date"] = pd.to_datetime(orders["signal_date"], errors="coerce")
    return {"nav": nav, "orders": orders, "summary": summary}


def run_metrics(label: str, role: str, nav: pd.DataFrame, orders: pd.DataFrame, summary: Dict[str, object]) -> Dict[str, object]:
    open_orders = orders[orders["action"].eq("open_sell")].copy()
    close_orders = orders[~orders["action"].eq("open_sell")].copy()
    stop_orders = orders[orders["action"].astype(str).str.contains("sl", case=False, na=False)].copy()
    expiry_orders = orders[orders["action"].eq("expiry")].copy()
    gross = float(open_orders.get("gross_premium_cash", pd.Series(dtype=float)).sum())
    net = float(open_orders.get("net_premium_cash", pd.Series(dtype=float)).sum())
    fee = float(orders.get("fee", pd.Series(dtype=float)).sum())
    s1_pnl = float(nav["s1_pnl"].sum()) if "s1_pnl" in nav else float(nav["nav"].iloc[-1] - nav["nav"].iloc[0])
    delta = float(nav.get("delta_pnl", pd.Series(0.0, index=nav.index)).sum())
    gamma = float(nav.get("gamma_pnl", pd.Series(0.0, index=nav.index)).sum())
    theta = float(nav.get("theta_pnl", pd.Series(0.0, index=nav.index)).sum())
    vega = float(nav.get("vega_pnl", pd.Series(0.0, index=nav.index)).sum())
    residual = float(nav.get("residual_pnl", pd.Series(0.0, index=nav.index)).sum())
    closed_open_premium = float(close_orders.get("open_premium_cash", pd.Series(dtype=float)).sum())
    retained = float(close_orders.get("premium_retained_cash", close_orders.get("pnl", pd.Series(dtype=float))).sum())
    stop_pnl = float(stop_orders.get("pnl", pd.Series(dtype=float)).sum())
    expiry_pnl = float(expiry_orders.get("pnl", pd.Series(dtype=float)).sum())
    dd = max_drawdown(nav["nav"])
    put_gross = float(open_orders.loc[open_orders["option_type"].eq("P"), "gross_premium_cash"].sum()) if "option_type" in open_orders else np.nan
    call_gross = float(open_orders.loc[open_orders["option_type"].eq("C"), "gross_premium_cash"].sum()) if "option_type" in open_orders else np.nan
    return {
        "label": label,
        "role": role,
        "start_date": nav["date"].min().date().isoformat(),
        "end_date": nav["date"].max().date().isoformat(),
        "rows": len(nav),
        "final_nav": float(nav["nav"].iloc[-1]),
        "total_return": safe_div(float(nav["nav"].iloc[-1] - nav["nav"].iloc[0]), float(nav["nav"].iloc[0])),
        "cagr": float(summary.get("cagr", np.nan)),
        "ann_vol": float(summary.get("ann_vol", np.nan)),
        "sharpe": float(summary.get("sharpe", np.nan)),
        "calmar": float(summary.get("calmar", np.nan)),
        "max_drawdown": float(dd.min()),
        "worst_day_return": float(nav["nav"].pct_change().min()),
        "avg_margin_pct": float(nav.get("s1_margin_used_pct", nav.get("margin_used", pd.Series(dtype=float))).mean()),
        "max_margin_pct": float(nav.get("s1_margin_used_pct", nav.get("margin_used", pd.Series(dtype=float))).max()),
        "avg_contracts": float(nav.get("s1_active_sell_contracts", pd.Series(dtype=float)).mean()),
        "avg_products": float(nav.get("s1_active_sell_products", pd.Series(dtype=float)).mean()),
        "total_open_gross_premium": gross,
        "total_open_net_premium": net,
        "open_days": int(open_orders["date"].nunique()) if not open_orders.empty else 0,
        "open_sell_count": int(len(open_orders)),
        "closed_count": int(len(close_orders)),
        "stop_count": int(len(stop_orders)),
        "expiry_count": int(len(expiry_orders)),
        "closed_open_premium": closed_open_premium,
        "closed_retained_cash": retained,
        "closed_retained_ratio": safe_div(retained, closed_open_premium),
        "s1_pnl": s1_pnl,
        "s1_pnl_to_gross_premium": safe_div(s1_pnl, gross),
        "stop_pnl": stop_pnl,
        "stop_loss_to_gross_premium": safe_div(-stop_pnl, gross),
        "expiry_pnl": expiry_pnl,
        "expiry_pnl_to_gross_premium": safe_div(expiry_pnl, gross),
        "cum_fee": fee,
        "fee_to_gross_premium": safe_div(fee, gross),
        "delta_pnl": delta,
        "gamma_pnl": gamma,
        "theta_pnl": theta,
        "vega_pnl": vega,
        "residual_pnl": residual,
        "gamma_loss_to_gross_premium": safe_div(-gamma, gross),
        "vega_loss_to_gross_premium": safe_div(-vega, gross),
        "theta_to_gross_premium": safe_div(theta, gross),
        "put_gross_premium_share": safe_div(put_gross, gross),
        "call_gross_premium_share": safe_div(call_gross, gross),
        "avg_call_lot_share": float(nav.get("s1_call_lot_share", pd.Series(dtype=float)).mean()),
        "avg_put_call_lot_ratio": float(nav.get("s1_put_call_lot_ratio", pd.Series(dtype=float)).mean()),
    }


def add_excess_columns(summary: pd.DataFrame) -> pd.DataFrame:
    b1 = summary.loc[summary["label"].eq("B1")].iloc[0]
    b2c = summary.loc[summary["label"].eq("B2C")].iloc[0]
    for base_name, base in [("b1", b1), ("b2c", b2c)]:
        summary[f"excess_return_vs_{base_name}"] = summary["total_return"] - float(base["total_return"])
        summary[f"excess_nav_vs_{base_name}"] = summary["final_nav"] - float(base["final_nav"])
        summary[f"dd_diff_vs_{base_name}"] = summary["max_drawdown"] - float(base["max_drawdown"])
        summary[f"premium_diff_vs_{base_name}"] = summary["total_open_gross_premium"] - float(base["total_open_gross_premium"])
        summary[f"retention_diff_vs_{base_name}"] = summary["closed_retained_ratio"] - float(base["closed_retained_ratio"])
    return summary


def plot_nav(data: Dict[str, Dict[str, object]], out: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    base_nav = data["B1"]["nav"].set_index("date")["nav"]
    for label, obj in data.items():
        nav = obj["nav"].set_index("date")["nav"]
        axes[0].plot(nav.index, nav / nav.iloc[0], label=label, linewidth=1.4)
        if label != "B1":
            common = nav.index.intersection(base_nav.index)
            excess = nav.loc[common] / base_nav.loc[common] - 1.0
            axes[1].plot(common, excess, label=f"{label} vs B1", linewidth=1.3)
    axes[0].set_title("NAV indexed")
    axes[0].set_ylabel("NAV / start")
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=5, fontsize=9)
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_title("Excess return vs B1")
    axes[1].set_ylabel("Excess")
    axes[1].grid(alpha=0.25)
    axes[1].legend(ncol=4, fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_drawdown(data: Dict[str, Dict[str, object]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.8))
    for label, obj in data.items():
        nav = obj["nav"].set_index("date")["nav"]
        ax.plot(nav.index, max_drawdown(nav) * 100, label=label, linewidth=1.2)
    ax.set_title("Drawdown comparison")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(alpha=0.25)
    ax.legend(ncol=5, fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_formula(summary: pd.DataFrame, out: Path) -> None:
    labels = summary["label"].tolist()
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    axes = axes.ravel()
    axes[0].bar(labels, summary["total_open_gross_premium"] / 1e6, color="#4C78A8")
    axes[0].set_title("Premium pool consumed")
    axes[0].set_ylabel("Gross premium (mn)")
    axes[1].bar(labels, summary["closed_retained_ratio"] * 100, color="#59A14F")
    axes[1].set_title("Retention rate")
    axes[1].set_ylabel("% of closed open premium")
    x = np.arange(len(labels))
    width = 0.25
    axes[2].bar(x - width, summary["theta_to_gross_premium"] * 100, width, label="Theta/gross", color="#F28E2B")
    axes[2].bar(x, summary["vega_loss_to_gross_premium"] * 100, width, label="Vega loss/gross", color="#E15759")
    axes[2].bar(x + width, summary["gamma_loss_to_gross_premium"] * 100, width, label="Gamma loss/gross", color="#B07AA1")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels)
    axes[2].set_title("Greek absorption vs premium")
    axes[2].set_ylabel("% of gross premium")
    axes[2].legend(fontsize=8)
    axes[3].bar(x - width, summary["s1_pnl_to_gross_premium"] * 100, width, label="S1 pnl/gross", color="#76B7B2")
    axes[3].bar(x, summary["stop_loss_to_gross_premium"] * 100, width, label="Stop loss/gross", color="#FF9DA7")
    axes[3].bar(x + width, summary["fee_to_gross_premium"] * 100, width, label="Fee/gross", color="#9C755F")
    axes[3].set_xticks(x)
    axes[3].set_xticklabels(labels)
    axes[3].set_title("Net capture, stops, and cost")
    axes[3].set_ylabel("% of gross premium")
    axes[3].legend(fontsize=8)
    for ax in axes:
        ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_greeks(summary: pd.DataFrame, out: Path) -> None:
    labels = summary["label"].tolist()
    cols = ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_pnl", "residual_pnl"]
    colors = ["#4C78A8", "#E15759", "#F28E2B", "#59A14F", "#B07AA1"]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    bottom_pos = np.zeros(len(labels))
    bottom_neg = np.zeros(len(labels))
    for col, color in zip(cols, colors):
        vals = summary[col].to_numpy(dtype=float) / 1e6
        pos = np.where(vals > 0, vals, 0)
        neg = np.where(vals < 0, vals, 0)
        ax.bar(labels, pos, bottom=bottom_pos, label=col, color=color)
        ax.bar(labels, neg, bottom=bottom_neg, color=color)
        bottom_pos += pos
        bottom_neg += neg
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Cumulative Greek attribution")
    ax.set_ylabel("PnL (mn)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_margin_premium(summary: pd.DataFrame, out: Path) -> None:
    labels = summary["label"].tolist()
    x = np.arange(len(labels))
    fig, ax1 = plt.subplots(figsize=(12, 5.2))
    ax2 = ax1.twinx()
    ax1.bar(x - 0.18, summary["avg_margin_pct"] * 100, width=0.36, label="Avg margin %", color="#4C78A8")
    ax1.bar(x + 0.18, summary["max_margin_pct"] * 100, width=0.36, label="Max margin %", color="#9ecae1")
    ax2.plot(x, summary["avg_open_day_gross_premium"] / 1e3 if "avg_open_day_gross_premium" in summary else summary["total_open_gross_premium"] / summary["open_days"] / 1e3, marker="o", color="#F28E2B", label="Premium/open day (k)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("Margin (%)")
    ax2.set_ylabel("Gross premium per open day (k)")
    ax1.set_title("Deployment proxy: margin and premium flow")
    ax1.grid(axis="y", alpha=0.25)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_pc(summary: pd.DataFrame, out: Path) -> None:
    labels = summary["label"].tolist()
    x = np.arange(len(labels))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.bar(x - width, summary["put_gross_premium_share"] * 100, width, label="Put gross premium share", color="#59A14F")
    ax.bar(x, summary["call_gross_premium_share"] * 100, width, label="Call gross premium share", color="#E15759")
    ax.bar(x + width, summary["avg_call_lot_share"] * 100, width, label="Avg call lot share", color="#4C78A8")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 100)
    ax.set_title("P/C structure")
    ax.set_ylabel("Share (%)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def plot_tail_stop(summary: pd.DataFrame, out: Path) -> None:
    labels = summary["label"].tolist()
    x = np.arange(len(labels))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    axes[0].bar(labels, summary["stop_count"], color="#E15759")
    axes[0].set_title("Stop count")
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].bar(x - 0.2, summary["stop_loss_to_gross_premium"] * 100, width=0.4, label="Stop loss/gross", color="#E15759")
    axes[1].bar(x + 0.2, summary["worst_day_return"] * 100, width=0.4, label="Worst day", color="#B07AA1")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels)
    axes[1].set_title("Tail severity")
    axes[1].set_ylabel("%")
    axes[1].grid(axis="y", alpha=0.25)
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def monthly_table(data: Dict[str, Dict[str, object]]) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []
    for label, obj in data.items():
        nav = obj["nav"].copy()
        nav = nav.set_index("date")
        monthly = nav["nav"].resample("ME").last().pct_change()
        first = nav["nav"].iloc[0]
        first_month = nav.index.min().to_period("M").to_timestamp("M")
        monthly.loc[first_month] = nav[nav.index.to_period("M") == nav.index.min().to_period("M")]["nav"].iloc[-1] / first - 1
        part = monthly.sort_index().rename(label).reset_index()
        part.columns = ["month", label]
        rows.append(part)
    result = rows[0]
    for part in rows[1:]:
        result = result.merge(part, on="month", how="outer")
    result = result.sort_values("month")
    return result


def plot_monthly_excess(monthly: pd.DataFrame, out: Path) -> None:
    df = monthly.copy()
    months = pd.to_datetime(df["month"]).dt.strftime("%Y-%m")
    fig, ax = plt.subplots(figsize=(13, 5.8))
    for label in ["B2C", "B4a", "B4b", "B4c"]:
        ax.plot(months, (df[label] - df["B1"]) * 100, marker="o", linewidth=1.1, label=f"{label} - B1")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Monthly excess return vs B1")
    ax.set_ylabel("Excess return (%)")
    ax.grid(alpha=0.25)
    ax.legend(ncol=4, fontsize=9)
    ax.set_xticks(range(0, len(months), max(len(months) // 12, 1)))
    ax.set_xticklabels(months.iloc[:: max(len(months) // 12, 1)], rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def product_premium_table(data: Dict[str, Dict[str, object]], out_dir: Path) -> pd.DataFrame:
    frames = []
    for label, obj in data.items():
        orders = obj["orders"]
        open_orders = orders[orders["action"].eq("open_sell")].copy()
        if open_orders.empty:
            continue
        g = open_orders.groupby("product", dropna=False)["gross_premium_cash"].sum().sort_values(ascending=False)
        total = g.sum()
        top = g.head(12).reset_index()
        top["label"] = label
        top["gross_premium_share"] = top["gross_premium_cash"] / total if total else np.nan
        frames.append(top)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result.to_csv(out_dir / "b4_product_premium_top.csv", index=False, encoding="utf-8-sig")
    return result


def plot_product_premium(top: pd.DataFrame, out: Path) -> None:
    if top.empty:
        return
    pivot = top.pivot_table(index="product", columns="label", values="gross_premium_share", aggfunc="sum").fillna(0)
    # Keep products important in any B4/B1 line.
    keep = pivot.max(axis=1).sort_values(ascending=False).head(15).index
    pivot = pivot.loc[keep, [c for c in ["B1", "B2C", "B4a", "B4b", "B4c"] if c in pivot.columns]]
    fig, ax = plt.subplots(figsize=(12, 6))
    bottom = np.zeros(len(pivot.columns))
    x = np.arange(len(pivot.columns))
    for product in pivot.index:
        vals = pivot.loc[product].to_numpy(dtype=float) * 100
        ax.bar(x, vals, bottom=bottom, label=product)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.columns)
    ax.set_ylabel("Top product gross premium share (%)")
    ax.set_title("Product premium concentration")
    ax.legend(ncol=3, fontsize=7, bbox_to_anchor=(1.02, 1.0), loc="upper left")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    ensure_dir(args.out_dir)
    data: Dict[str, Dict[str, object]] = {}
    metrics = []
    for run in RUNS:
        obj = load_run(args.output_dir, run)
        data[run["label"]] = obj
        metrics.append(run_metrics(run["label"], run["role"], obj["nav"], obj["orders"], obj["summary"]))
    summary = add_excess_columns(pd.DataFrame(metrics))
    summary["avg_open_day_gross_premium"] = summary["total_open_gross_premium"] / summary["open_days"].replace(0, np.nan)
    summary.to_csv(args.out_dir / "b4_formula_summary.csv", index=False, encoding="utf-8-sig")
    monthly = monthly_table(data)
    monthly.to_csv(args.out_dir / "b4_monthly_returns.csv", index=False, encoding="utf-8-sig")
    top = product_premium_table(data, args.out_dir)

    plot_nav(data, args.out_dir / "01_b4_nav_excess_vs_b1.png")
    plot_drawdown(data, args.out_dir / "02_b4_drawdown_compare.png")
    plot_formula(summary, args.out_dir / "03_b4_formula_decomposition.png")
    plot_greeks(summary, args.out_dir / "04_b4_greek_attribution_compare.png")
    plot_margin_premium(summary, args.out_dir / "05_b4_deployment_margin_premium.png")
    plot_pc(summary, args.out_dir / "06_b4_pc_structure.png")
    plot_tail_stop(summary, args.out_dir / "07_b4_tail_stop_compare.png")
    plot_monthly_excess(monthly, args.out_dir / "08_b4_monthly_excess_vs_b1.png")
    plot_product_premium(top, args.out_dir / "09_b4_product_premium_concentration.png")
    print(f"B4 formula research pack written to: {args.out_dir}")


if __name__ == "__main__":
    main()
