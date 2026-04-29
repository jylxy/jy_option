#!/usr/bin/env python3
"""Build a standard analysis pack for option backtest outputs.

The script is intentionally source-ASCII only to avoid terminal/font encoding
issues on the remote server. It accepts either a backtest tag or explicit CSV
paths and writes plots plus tabular summaries into output/analysis_<tag>/.

Examples:
    python scripts/analyze_backtest_outputs.py --tag my_run
    python scripts/analyze_backtest_outputs.py --nav output/nav_my_run.csv
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


TRADING_DAYS = 252
ATTRIBUTION_COLS = [
    "delta_pnl",
    "gamma_pnl",
    "theta_pnl",
    "vega_pnl",
    "residual_pnl",
]
B2_QUALITY_FIELDS = [
    "variance_carry",
    "breakeven_cushion_iv",
    "breakeven_cushion_rv",
    "premium_to_iv5_loss",
    "premium_to_iv10_loss",
    "premium_to_stress_loss",
    "theta_vega_efficiency",
    "gamma_rent_penalty",
    "friction_ratio",
]
B2_SCORE_FIELDS = [
    "premium_quality_score",
    "iv_rv_carry_score",
    "breakeven_cushion_score",
    "premium_to_iv_shock_score",
    "premium_to_stress_loss_score",
    "theta_vega_efficiency_score",
    "cost_liquidity_score",
]
CLOSE_PREFIXES = (
    "sl_",
    "tp_",
    "pre_expiry_roll",
    "expiry",
    "greeks_",
    "s4_",
)


def configure_plot_style() -> None:
    plt.rcParams["figure.dpi"] = 120
    plt.rcParams["savefig.dpi"] = 140
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Backtest tag, e.g. s1_b0_standard")
    parser.add_argument("--output-dir", default="output", help="Backtest output directory")
    parser.add_argument("--nav", help="Explicit NAV CSV path")
    parser.add_argument("--orders", help="Explicit orders CSV path")
    parser.add_argument("--diagnostics", help="Explicit diagnostics CSV path")
    parser.add_argument("--out-dir", help="Analysis output directory")
    parser.add_argument("--top-n", type=int, default=10, help="Top N products for share chart")
    parser.add_argument("--rolling-window", type=int, default=20, help="Rolling window in days")
    parser.add_argument("--baseline-tag", help="Baseline tag for comparison charts, e.g. s1_b0_standard_stop25_allprod_2022_latest")
    parser.add_argument("--baseline-nav", help="Explicit baseline NAV CSV path")
    parser.add_argument("--baseline-orders", help="Explicit baseline orders CSV path")
    parser.add_argument("--baseline-label", default="B0", help="Baseline label used in comparison charts")
    parser.add_argument("--candidate-label", help="Candidate label used in comparison charts. Default: --tag")
    return parser.parse_args()


def resolve_path(path: Optional[str]) -> Optional[Path]:
    if not path:
        return None
    return Path(path).expanduser().resolve()


def find_by_tag(output_dir: Path, prefix: str, tag: Optional[str]) -> Optional[Path]:
    if not tag:
        return None
    path = output_dir / f"{prefix}_{tag}.csv"
    return path if path.exists() else None


def infer_tag(nav_path: Path) -> str:
    name = nav_path.stem
    return name[4:] if name.startswith("nav_") else name


def read_csv_optional(path: Optional[Path]) -> Optional[pd.DataFrame]:
    if path is None or not path.exists():
        return None
    if path.stat().st_size == 0:
        return None
    return pd.read_csv(path)


def require_nav(args: argparse.Namespace) -> Tuple[pd.DataFrame, Path, str, Path, Optional[Path], Optional[Path]]:
    output_dir = Path(args.output_dir).expanduser().resolve()
    nav_path = resolve_path(args.nav) or find_by_tag(output_dir, "nav", args.tag)
    if nav_path is None or not nav_path.exists():
        raise FileNotFoundError("NAV CSV not found. Provide --tag or --nav.")

    tag = args.tag or infer_tag(nav_path)
    orders_path = resolve_path(args.orders) or find_by_tag(output_dir, "orders", tag)
    diagnostics_path = resolve_path(args.diagnostics) or find_by_tag(output_dir, "diagnostics", tag)
    out_dir = resolve_path(args.out_dir) or (output_dir / f"analysis_{tag}")
    out_dir.mkdir(parents=True, exist_ok=True)

    nav = pd.read_csv(nav_path)
    return nav, nav_path, tag, out_dir, orders_path, diagnostics_path


def to_datetime_date(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date", kind="mergesort").reset_index(drop=True)


def numeric_series(frame: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").fillna(default)


def pct_or_ratio(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.dropna().abs().median() > 2:
        return values / 100.0
    return values


def enrich_nav(nav: pd.DataFrame) -> pd.DataFrame:
    nav = to_datetime_date(nav)
    nav["nav"] = pd.to_numeric(nav["nav"], errors="coerce")
    nav = nav.dropna(subset=["nav"]).reset_index(drop=True)
    initial_nav = nav["nav"].iloc[0]
    nav["daily_return"] = nav["nav"].pct_change().fillna(0.0)
    nav["daily_pnl"] = nav["nav"].diff().fillna(nav["nav"].iloc[0] - initial_nav)
    nav["running_peak"] = nav["nav"].cummax()
    nav["drawdown"] = nav["nav"] / nav["running_peak"] - 1.0
    nav["cum_fee"] = numeric_series(nav, "fee").cumsum()
    if "cum_pnl" not in nav.columns:
        nav["cum_pnl"] = nav["nav"] - initial_nav
    if "margin_pct" not in nav.columns:
        if "s1_margin_used_pct" in nav.columns:
            nav["margin_pct"] = pct_or_ratio(nav["s1_margin_used_pct"])
        elif "margin_used" in nav.columns:
            nav["margin_pct"] = numeric_series(nav, "margin_used") / nav["nav"].replace(0, np.nan)
        else:
            nav["margin_pct"] = np.nan
    return nav


def calc_metrics(nav: pd.DataFrame) -> Dict[str, float]:
    n = len(nav)
    initial_nav = float(nav["nav"].iloc[0])
    final_nav = float(nav["nav"].iloc[-1])
    total_return = final_nav / initial_nav - 1.0 if initial_nav else np.nan
    years = max(n / TRADING_DAYS, 1e-9)
    cagr = (final_nav / initial_nav) ** (1.0 / years) - 1.0 if initial_nav > 0 and n > 1 else np.nan
    daily_ret = pd.to_numeric(nav["daily_return"], errors="coerce").fillna(0.0)
    ann_vol = daily_ret.std(ddof=1) * math.sqrt(TRADING_DAYS) if n > 1 else np.nan
    sharpe = daily_ret.mean() / daily_ret.std(ddof=1) * math.sqrt(TRADING_DAYS) if daily_ret.std(ddof=1) > 0 else np.nan
    downside = daily_ret[daily_ret < 0].std(ddof=1)
    sortino = daily_ret.mean() / downside * math.sqrt(TRADING_DAYS) if downside and downside > 0 else np.nan
    max_dd = float(nav["drawdown"].min())
    calmar = cagr / abs(max_dd) if max_dd < 0 and pd.notna(cagr) else np.nan
    worst_idx = daily_ret.idxmin()
    best_idx = daily_ret.idxmax()
    return {
        "start_nav": initial_nav,
        "final_nav": final_nav,
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "current_drawdown": float(nav["drawdown"].iloc[-1]),
        "worst_day_return": float(daily_ret.loc[worst_idx]),
        "best_day_return": float(daily_ret.loc[best_idx]),
        "avg_margin_pct": float(pd.to_numeric(nav["margin_pct"], errors="coerce").mean()),
        "max_margin_pct": float(pd.to_numeric(nav["margin_pct"], errors="coerce").max()),
        "cum_fee": float(numeric_series(nav, "fee").sum()),
        "cum_s1_pnl": float(numeric_series(nav, "s1_pnl").sum()),
        "cum_delta_pnl": float(numeric_series(nav, "delta_pnl").sum()),
        "cum_gamma_pnl": float(numeric_series(nav, "gamma_pnl").sum()),
        "cum_theta_pnl": float(numeric_series(nav, "theta_pnl").sum()),
        "cum_vega_pnl": float(numeric_series(nav, "vega_pnl").sum()),
        "cum_residual_pnl": float(numeric_series(nav, "residual_pnl").sum()),
    }


def premium_quality_metrics(nav: pd.DataFrame, orders: Optional[pd.DataFrame]) -> Dict[str, float]:
    if orders is None or orders.empty:
        return {}
    daily = build_daily_open_premium(nav, orders)
    if daily is None or daily.empty:
        return {}

    total_gross = float(daily["open_gross_premium"].sum())
    total_net = float(daily["open_net_premium"].sum())
    nonzero = daily[daily["open_gross_premium"] > 0].copy()
    cum_vega = float(numeric_series(nav, "vega_pnl").sum())
    cum_gamma = float(numeric_series(nav, "gamma_pnl").sum())
    cum_theta = float(numeric_series(nav, "theta_pnl").sum())
    cum_s1 = float(numeric_series(nav, "s1_pnl").sum())
    vega_loss = max(-cum_vega, 0.0)
    gamma_loss = max(-cum_gamma, 0.0)

    result = {
        "open_days": float(len(nonzero)),
        "total_open_gross_premium": total_gross,
        "total_open_net_premium": total_net,
        "avg_daily_open_gross_premium": float(daily["open_gross_premium"].mean()),
        "avg_open_day_gross_premium": float(nonzero["open_gross_premium"].mean()) if len(nonzero) else np.nan,
        "avg_daily_open_gross_premium_pct_nav": float(daily["open_gross_premium_pct_nav"].mean()),
        "avg_open_day_gross_premium_pct_nav": float(nonzero["open_gross_premium_pct_nav"].mean()) if len(nonzero) else np.nan,
        "max_daily_open_gross_premium": float(daily["open_gross_premium"].max()),
        "put_gross_premium_share": (
            float(daily["open_put_gross_premium"].sum() / total_gross) if total_gross > 0 else np.nan
        ),
        "vega_pnl_to_gross_premium": cum_vega / total_gross if total_gross > 0 else np.nan,
        "vega_loss_to_gross_premium": vega_loss / total_gross if total_gross > 0 else np.nan,
        "gamma_loss_to_gross_premium": gamma_loss / total_gross if total_gross > 0 else np.nan,
        "theta_to_gross_premium": cum_theta / total_gross if total_gross > 0 else np.nan,
        "s1_pnl_to_gross_premium": cum_s1 / total_gross if total_gross > 0 else np.nan,
    }

    close_events = (
        orders[orders["action"].astype(str).str.lower().map(is_close_action)].copy()
        if "action" in orders.columns
        else pd.DataFrame()
    )
    if not close_events.empty and {"open_premium_cash", "premium_retained_cash"}.issubset(close_events.columns):
        open_premium = pd.to_numeric(close_events["open_premium_cash"], errors="coerce").fillna(0.0)
        retained = pd.to_numeric(close_events["premium_retained_cash"], errors="coerce").fillna(0.0)
        closed_open_premium = float(open_premium[open_premium > 0].sum())
        closed_retained = float(retained[open_premium > 0].sum())
        result["closed_open_premium"] = closed_open_premium
        result["closed_premium_retained"] = closed_retained
        result["closed_premium_retained_ratio"] = (
            closed_retained / closed_open_premium if closed_open_premium > 0 else np.nan
        )
    return result


def fmt_pct(x: float) -> str:
    return "NA" if pd.isna(x) else f"{x:.2%}"


def fmt_num(x: float) -> str:
    return "NA" if pd.isna(x) else f"{x:,.2f}"


def save_fig(fig: plt.Figure, out_dir: Path, name: str) -> Path:
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def legend_if_any(ax: plt.Axes, **kwargs) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(**kwargs)


def plot_nav_drawdown(nav: pd.DataFrame, metrics: Dict[str, float], out_dir: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(nav["date"], nav["nav"] / 1e6, label="NAV", color="#1f77b4", linewidth=1.6)
    axes[0].plot(nav["date"], nav["running_peak"] / 1e6, label="Running peak", color="#7f7f7f", linewidth=1.0, alpha=0.8)
    axes[0].set_ylabel("NAV (mn)")
    axes[0].set_title("NAV and drawdown")
    axes[0].legend(loc="upper left")
    text = (
        f"Total: {fmt_pct(metrics['total_return'])}\n"
        f"CAGR: {fmt_pct(metrics['cagr'])}\n"
        f"Max DD: {fmt_pct(metrics['max_drawdown'])}\n"
        f"Sharpe: {fmt_num(metrics['sharpe'])}\n"
        f"Calmar: {fmt_num(metrics['calmar'])}"
    )
    axes[0].text(0.99, 0.05, text, ha="right", va="bottom", transform=axes[0].transAxes, fontsize=9,
                 bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8, "edgecolor": "#cccccc"})
    axes[1].fill_between(nav["date"], nav["drawdown"] * 100.0, 0.0, color="#d62728", alpha=0.35)
    axes[1].set_ylabel("DD (%)")
    axes[1].set_xlabel("Date")
    save_fig(fig, out_dir, "01_nav_drawdown.png")


def plot_margin_positions(nav: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    axes[0].plot(nav["date"], nav["margin_pct"] * 100.0, label="Margin used", color="#8c564b", linewidth=1.3)
    for col, label, color in [
        ("effective_margin_cap", "Effective cap", "#d62728"),
        ("effective_s1_margin_cap", "S1 cap", "#ff7f0e"),
    ]:
        if col in nav.columns:
            axes[0].plot(nav["date"], pct_or_ratio(nav[col]) * 100.0, label=label, linewidth=1.0, alpha=0.85, color=color)
    axes[0].set_ylabel("Margin (%)")
    axes[0].set_title("Margin, positions, and active universe")
    axes[0].legend(loc="upper left")

    for col, label in [
        ("n_positions", "Positions"),
        ("s1_active_sell_contracts", "Active sell contracts"),
        ("s1_active_sell_lots", "Active sell lots"),
    ]:
        if col in nav.columns:
            axes[1].plot(nav["date"], numeric_series(nav, col), label=label, linewidth=1.1)
    axes[1].set_ylabel("Count / lots")
    legend_if_any(axes[1], loc="upper left")

    for col, label in [
        ("s1_active_sell_products", "Active products"),
        ("vol_falling_products", "Falling vol products"),
        ("vol_low_products", "Low vol products"),
        ("vol_high_products", "High vol products"),
    ]:
        if col in nav.columns:
            axes[2].plot(nav["date"], numeric_series(nav, col), label=label, linewidth=1.1)
    axes[2].set_ylabel("Products")
    axes[2].set_xlabel("Date")
    legend_if_any(axes[2], loc="upper left")
    save_fig(fig, out_dir, "02_margin_positions.png")


def plot_greeks(nav: pd.DataFrame, out_dir: Path) -> None:
    cols = [
        ("cash_delta", "Cash delta"),
        ("cash_vega", "Cash vega"),
        ("cash_gamma", "Cash gamma"),
        ("cash_theta", "Cash theta"),
    ]
    present = [(c, label) for c, label in cols if c in nav.columns]
    if not present:
        return
    fig, axes = plt.subplots(len(present), 1, figsize=(14, 2.8 * len(present)), sharex=True)
    if len(present) == 1:
        axes = [axes]
    for ax, (col, label) in zip(axes, present):
        ax.plot(nav["date"], numeric_series(nav, col), linewidth=1.0)
        ax.axhline(0.0, color="#777777", linewidth=0.8)
        ax.set_ylabel(label)
    axes[0].set_title("Daily cash Greeks")
    axes[-1].set_xlabel("Date")
    save_fig(fig, out_dir, "03_greeks_timeseries.png")


def plot_pnl_attribution(nav: pd.DataFrame, out_dir: Path, rolling_window: int) -> None:
    present = [c for c in ATTRIBUTION_COLS if c in nav.columns]
    if not present and "s1_pnl" not in nav.columns:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for col in present:
        axes[0].plot(nav["date"], numeric_series(nav, col).cumsum() / 1e4, label=col.replace("_pnl", ""), linewidth=1.2)
    if "fee" in nav.columns:
        axes[0].plot(nav["date"], -numeric_series(nav, "fee").cumsum() / 1e4, label="fee", color="#111111", linestyle="--")
    axes[0].set_title("Cumulative PnL attribution")
    axes[0].set_ylabel("Cumulative PnL (10k)")
    axes[0].legend(loc="upper left", ncol=3)

    for col in present:
        axes[1].plot(
            nav["date"],
            numeric_series(nav, col).rolling(rolling_window, min_periods=1).sum() / 1e4,
            label=col.replace("_pnl", ""),
            linewidth=1.1,
        )
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_title(f"Rolling {rolling_window}D attribution")
    axes[1].set_ylabel("Rolling PnL (10k)")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left", ncol=3)
    save_fig(fig, out_dir, "04_pnl_attribution.png")


def plot_daily_tail(nav: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))
    colors = np.where(nav["daily_return"] >= 0, "#2ca02c", "#d62728")
    axes[0].bar(nav["date"], nav["daily_return"] * 100.0, color=colors, width=1.0, alpha=0.75)
    axes[0].set_title("Daily returns")
    axes[0].set_ylabel("Daily return (%)")
    axes[1].hist(nav["daily_return"] * 100.0, bins=60, color="#1f77b4", alpha=0.75)
    axes[1].axvline(nav["daily_return"].quantile(0.01) * 100.0, color="#d62728", linestyle="--", label="1% quantile")
    axes[1].axvline(nav["daily_return"].quantile(0.05) * 100.0, color="#ff7f0e", linestyle="--", label="5% quantile")
    axes[1].set_xlabel("Daily return (%)")
    axes[1].set_ylabel("Frequency")
    axes[1].legend(loc="upper left")
    save_fig(fig, out_dir, "05_daily_pnl_tail.png")


def plot_premium_pc(nav: pd.DataFrame, out_dir: Path) -> None:
    cols = [
        "s1_short_open_premium_pct",
        "s1_short_liability_pct",
        "s1_short_unrealized_premium_pct",
        "s1_call_open_premium_pct",
        "s1_put_open_premium_pct",
    ]
    if not any(c in nav.columns for c in cols + ["s1_call_lot_share", "s1_put_call_lot_ratio"]):
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for col in cols:
        if col in nav.columns:
            axes[0].plot(nav["date"], pct_or_ratio(nav[col]) * 100.0, label=col, linewidth=1.1)
    axes[0].set_title("Short premium and liability")
    axes[0].set_ylabel("% NAV")
    axes[0].legend(loc="upper left", ncol=2)

    if "s1_call_lot_share" in nav.columns:
        axes[1].plot(nav["date"], pct_or_ratio(nav["s1_call_lot_share"]) * 100.0, label="call lot share", linewidth=1.2)
        axes[1].axhline(50.0, color="#777777", linewidth=0.8, linestyle="--")
    if "s1_put_call_lot_ratio" in nav.columns:
        ax2 = axes[1].twinx()
        ax2.plot(nav["date"], numeric_series(nav, "s1_put_call_lot_ratio"), label="put/call lot ratio", color="#d62728", linewidth=1.0)
        ax2.set_ylabel("P/C ratio")
        ax2.legend(loc="upper right")
    axes[1].set_ylabel("Call share (%)")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left")
    save_fig(fig, out_dir, "06_premium_pc_structure.png")


def build_daily_open_premium(nav: pd.DataFrame, orders: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if orders is None or orders.empty:
        return None
    if "date" not in orders.columns or "action" not in orders.columns:
        return None
    orders = orders.copy()
    orders["date"] = pd.to_datetime(orders["date"])
    open_sell = orders[orders["action"].astype(str).str.lower().map(is_open_sell_action)].copy()
    if "strategy" in open_sell.columns:
        open_sell = open_sell[open_sell["strategy"].astype(str).str.upper() == "S1"].copy()

    base = nav[["date", "nav"]].copy()
    if open_sell.empty:
        daily = base.copy()
        for col in [
            "open_gross_premium",
            "open_net_premium",
            "open_fee",
            "open_lots",
            "open_contracts",
            "open_products",
            "open_orders",
            "open_call_gross_premium",
            "open_put_gross_premium",
        ]:
            daily[col] = 0.0
    else:
        for col in ["gross_premium_cash", "net_premium_cash", "fee", "quantity"]:
            if col not in open_sell.columns:
                open_sell[col] = 0.0
            open_sell[col] = pd.to_numeric(open_sell[col], errors="coerce").fillna(0.0)
        if "code" not in open_sell.columns:
            open_sell["code"] = ""
        if "product" not in open_sell.columns:
            open_sell["product"] = ""
        if "option_type" not in open_sell.columns:
            open_sell["option_type"] = ""
        open_sell["option_type"] = open_sell["option_type"].astype(str).str.upper().str[:1]

        daily = open_sell.groupby("date").agg(
            open_gross_premium=("gross_premium_cash", "sum"),
            open_net_premium=("net_premium_cash", "sum"),
            open_fee=("fee", "sum"),
            open_lots=("quantity", "sum"),
            open_contracts=("code", "nunique"),
            open_products=("product", "nunique"),
            open_orders=("code", "size"),
        ).reset_index()
        side = open_sell.pivot_table(
            index="date",
            columns="option_type",
            values="gross_premium_cash",
            aggfunc="sum",
            fill_value=0.0,
        ).reset_index()
        side = side.rename(columns={"C": "open_call_gross_premium", "P": "open_put_gross_premium"})
        for col in ["open_call_gross_premium", "open_put_gross_premium"]:
            if col not in side.columns:
                side[col] = 0.0
        daily = daily.merge(side[["date", "open_call_gross_premium", "open_put_gross_premium"]], on="date", how="left")
        daily = base.merge(daily, on="date", how="left")
        fill_cols = [c for c in daily.columns if c not in ("date", "nav")]
        daily[fill_cols] = daily[fill_cols].fillna(0.0)

    daily["open_gross_premium_pct_nav"] = daily["open_gross_premium"] / daily["nav"].replace(0.0, np.nan)
    daily["open_net_premium_pct_nav"] = daily["open_net_premium"] / daily["nav"].replace(0.0, np.nan)
    daily["open_fee_pct_gross"] = np.where(
        daily["open_gross_premium"] > 0,
        daily["open_fee"] / daily["open_gross_premium"],
        np.nan,
    )
    daily["open_gross_premium_20d_avg"] = daily["open_gross_premium"].rolling(20, min_periods=1).mean()
    daily["open_gross_premium_pct_nav_20d_avg"] = (
        daily["open_gross_premium_pct_nav"].rolling(20, min_periods=1).mean()
    )
    daily["open_net_premium_pct_nav_20d_avg"] = daily["open_net_premium_pct_nav"].rolling(20, min_periods=1).mean()
    daily["cum_open_gross_premium"] = daily["open_gross_premium"].cumsum()
    daily["cum_open_net_premium"] = daily["open_net_premium"].cumsum()
    return daily


def plot_premium_vega_quality(nav: pd.DataFrame, orders: Optional[pd.DataFrame], out_dir: Path) -> Optional[pd.DataFrame]:
    daily = build_daily_open_premium(nav, orders)
    if daily is None or daily.empty:
        return None
    daily.to_csv(out_dir / "daily_open_premium.csv", index=False)

    summary = premium_quality_metrics(nav, orders)
    if summary:
        pd.DataFrame([summary]).to_csv(out_dir / "premium_quality_summary.csv", index=False)

    fig, axes = plt.subplots(
        4,
        1,
        figsize=(15, 13),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.4, 1.7, 1.4]},
    )
    axes[0].bar(daily["date"], daily["open_put_gross_premium"] / 1e4, label="Put gross premium", color="#4C78A8", width=1.0)
    axes[0].bar(
        daily["date"],
        daily["open_call_gross_premium"] / 1e4,
        bottom=daily["open_put_gross_premium"] / 1e4,
        label="Call gross premium",
        color="#F58518",
        width=1.0,
    )
    axes[0].plot(daily["date"], daily["open_gross_premium_20d_avg"] / 1e4, label="20D avg gross premium", color="#111111", linewidth=1.3)
    axes[0].set_title("Daily new opening premium and vega quality")
    axes[0].set_ylabel("Premium (10k)")
    axes[0].legend(loc="upper left", ncol=3)

    axes[1].plot(daily["date"], daily["open_gross_premium_pct_nav"] * 100.0, label="Gross premium / NAV", color="#54A24B", linewidth=0.9, alpha=0.7)
    axes[1].plot(daily["date"], daily["open_gross_premium_pct_nav_20d_avg"] * 100.0, label="20D gross / NAV", color="#006400", linewidth=1.5)
    axes[1].plot(daily["date"], daily["open_net_premium_pct_nav_20d_avg"] * 100.0, label="20D net / NAV", color="#B279A2", linewidth=1.2)
    axes[1].set_ylabel("New premium / NAV (%)")
    axes[1].legend(loc="upper left", ncol=3)

    axes[2].plot(daily["date"], daily["cum_open_gross_premium"] / 1e4, label="Cum gross premium", color="#54A24B", linewidth=1.3)
    if "theta_pnl" in nav.columns:
        axes[2].plot(nav["date"], numeric_series(nav, "theta_pnl").cumsum() / 1e4, label="Cum theta", color="#2CA02C", linewidth=1.1)
    if "vega_pnl" in nav.columns:
        axes[2].plot(nav["date"], -numeric_series(nav, "vega_pnl").cumsum() / 1e4, label="- Cum vega PnL", color="#D62728", linewidth=1.1)
    if "gamma_pnl" in nav.columns:
        axes[2].plot(nav["date"], -numeric_series(nav, "gamma_pnl").cumsum() / 1e4, label="- Cum gamma PnL", color="#9467BD", linewidth=1.1)
    if "s1_pnl" in nav.columns:
        axes[2].plot(nav["date"], numeric_series(nav, "s1_pnl").cumsum() / 1e4, label="Cum S1 PnL", color="#111111", linewidth=1.3)
    axes[2].set_ylabel("Cumulative cash (10k)")
    axes[2].legend(loc="upper left", ncol=3)

    if "s1_short_liability_pct" in nav.columns:
        axes[3].plot(nav["date"], pct_or_ratio(numeric_series(nav, "s1_short_liability_pct")) * 100.0, label="Open liability / NAV", color="#E45756", linewidth=1.0)
    if "s1_short_unrealized_premium_pct" in nav.columns:
        axes[3].plot(nav["date"], pct_or_ratio(numeric_series(nav, "s1_short_unrealized_premium_pct")) * 100.0, label="Retained open premium / NAV", color="#72B7B2", linewidth=1.0)
    if "s1_short_open_premium_pct" in nav.columns:
        axes[3].plot(nav["date"], pct_or_ratio(numeric_series(nav, "s1_short_open_premium_pct")) * 100.0, label="Open premium / NAV", color="#4C78A8", linewidth=1.0)
    axes[3].set_ylabel("Open stock premium (%)")
    axes[3].set_xlabel("Date")
    axes[3].legend(loc="upper left", ncol=3)

    save_fig(fig, out_dir, "12_daily_open_premium_vega_quality.png")
    return daily


def s1_open_orders(orders: Optional[pd.DataFrame]) -> pd.DataFrame:
    if orders is None or orders.empty or "action" not in orders.columns:
        return pd.DataFrame()
    frame = orders.copy()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["action"].astype(str).str.lower().map(is_open_sell_action)].copy()
    if "strategy" in frame.columns:
        frame = frame[frame["strategy"].astype(str).str.upper() == "S1"].copy()
    return frame


def s1_close_orders(orders: Optional[pd.DataFrame]) -> pd.DataFrame:
    if orders is None or orders.empty or "action" not in orders.columns:
        return pd.DataFrame()
    frame = orders.copy()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["action"].astype(str).str.lower().map(is_close_action)].copy()
    if "strategy" in frame.columns:
        frame = frame[frame["strategy"].astype(str).str.upper() == "S1"].copy()
    return frame


def normalize_side(frame: pd.DataFrame) -> pd.Series:
    if "option_type" not in frame.columns:
        return pd.Series("UNKNOWN", index=frame.index)
    side = frame["option_type"].astype(str).str.upper().str[:1]
    return side.where(side.isin(["C", "P"]), "UNKNOWN")


def plot_tail_product_side_contribution(nav: pd.DataFrame, orders: Optional[pd.DataFrame], out_dir: Path, tail_n: int = 20) -> Optional[pd.DataFrame]:
    close_events = s1_close_orders(orders)
    if close_events.empty or "date" not in close_events.columns or "pnl" not in close_events.columns:
        return None
    worst_dates = set(pd.to_datetime(nav.sort_values("daily_return", kind="mergesort").head(tail_n)["date"]))
    close_events = close_events[close_events["date"].isin(worst_dates)].copy()
    if close_events.empty:
        return None
    close_events["side"] = normalize_side(close_events)
    close_events["pnl"] = numeric_series(close_events, "pnl")
    close_events["quantity"] = numeric_series(close_events, "quantity")
    close_events["open_premium_cash"] = numeric_series(close_events, "open_premium_cash")
    group = close_events.groupby(["product", "side"], dropna=False).agg(
        close_orders=("action", "size"),
        lots=("quantity", "sum"),
        realized_pnl=("pnl", "sum"),
        open_premium_cash=("open_premium_cash", "sum"),
    ).reset_index()
    group["pnl_per_open_premium"] = np.where(
        group["open_premium_cash"].abs() > 0,
        group["realized_pnl"] / group["open_premium_cash"].abs(),
        np.nan,
    )
    group = group.sort_values("realized_pnl", kind="mergesort")
    group.to_csv(out_dir / "tail_product_side_contribution.csv", index=False)

    top = group.head(20).copy()
    if top.empty:
        return group
    labels = top["product"].astype(str) + "-" + top["side"].astype(str)
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [2.1, 1.4]})
    colors = np.where(top["realized_pnl"] >= 0, "#2ca02c", "#d62728")
    axes[0].barh(labels, top["realized_pnl"] / 1e4, color=colors, alpha=0.8)
    axes[0].axvline(0.0, color="#777777", linewidth=0.8)
    axes[0].set_title(f"Tail product-side contribution on worst {tail_n} NAV days")
    axes[0].set_xlabel("Realized close PnL (10k)")
    axes[0].invert_yaxis()

    daily_side = close_events.groupby(["date", "side"])["pnl"].sum().unstack(fill_value=0.0).sort_index()
    daily_side.to_csv(out_dir / "tail_daily_side_contribution.csv")
    for side in ["P", "C", "UNKNOWN"]:
        if side in daily_side.columns:
            axes[1].bar(daily_side.index, daily_side[side] / 1e4, label=side, alpha=0.75, width=1.0)
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_ylabel("Tail-day close PnL (10k)")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left", ncol=3)
    save_fig(fig, out_dir, "13_tail_product_side_contribution.png")
    return group


def plot_vega_quality_by_bucket(orders: Optional[pd.DataFrame], out_dir: Path) -> Optional[pd.DataFrame]:
    close_events = s1_close_orders(orders)
    if close_events.empty or "open_premium_cash" not in close_events.columns:
        return None
    close_events = close_events.copy()
    close_events["side"] = normalize_side(close_events)
    if "vol_regime" in close_events.columns:
        close_events["bucket"] = close_events["vol_regime"].fillna("unknown").astype(str)
    else:
        close_events["bucket"] = "unknown"
    close_events["open_premium_cash"] = numeric_series(close_events, "open_premium_cash")
    close_events["premium_retained_cash"] = numeric_series(close_events, "premium_retained_cash")
    close_events["pnl"] = numeric_series(close_events, "pnl")
    close_events["quantity"] = numeric_series(close_events, "quantity")
    close_events["abs_vega_proxy"] = numeric_series(close_events, "vega").abs() * close_events["quantity"].abs()
    close_events["iv_change_for_vega"] = numeric_series(close_events, "contract_iv_change_for_vega", np.nan)
    close_events = close_events[close_events["open_premium_cash"] > 0].copy()
    if close_events.empty:
        return None
    group = close_events.groupby(["bucket", "side"], dropna=False).agg(
        close_orders=("action", "size"),
        lots=("quantity", "sum"),
        open_premium_cash=("open_premium_cash", "sum"),
        retained_cash=("premium_retained_cash", "sum"),
        pnl=("pnl", "sum"),
        abs_vega_proxy=("abs_vega_proxy", "sum"),
        avg_iv_change_for_vega=("iv_change_for_vega", "mean"),
    ).reset_index()
    group["retained_ratio"] = group["retained_cash"] / group["open_premium_cash"].replace(0.0, np.nan)
    group["pnl_to_open_premium"] = group["pnl"] / group["open_premium_cash"].replace(0.0, np.nan)
    group["premium_per_vega_proxy"] = group["open_premium_cash"] / group["abs_vega_proxy"].replace(0.0, np.nan)
    group = group.sort_values(["bucket", "side"], kind="mergesort")
    group.to_csv(out_dir / "vega_quality_by_bucket.csv", index=False)

    labels = group["bucket"].astype(str) + "-" + group["side"].astype(str)
    x = np.arange(len(group))
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    axes[0].bar(x, group["retained_ratio"] * 100.0, color="#4C78A8", alpha=0.8)
    axes[0].axhline(0.0, color="#777777", linewidth=0.8)
    axes[0].set_title("Vega quality by vol bucket and side")
    axes[0].set_ylabel("Retained ratio (%)")

    axes[1].bar(x, group["premium_per_vega_proxy"], color="#F58518", alpha=0.8)
    axes[1].set_ylabel("Premium / abs vega proxy")

    axes[2].bar(x, group["open_premium_cash"] / 1e4, color="#54A24B", alpha=0.75, label="open premium")
    ax2 = axes[2].twinx()
    ax2.plot(x, group["abs_vega_proxy"], color="#D62728", marker="o", linewidth=1.0, label="abs vega proxy")
    axes[2].set_ylabel("Open premium (10k)")
    ax2.set_ylabel("Abs vega proxy")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels, rotation=35, ha="right")
    axes[2].set_xlabel("Vol bucket - side")
    legend_if_any(axes[2], loc="upper left")
    legend_if_any(ax2, loc="upper right")
    save_fig(fig, out_dir, "14_vega_quality_by_bucket.png")
    return group


def plot_stop_slippage_distribution(orders: Optional[pd.DataFrame], out_dir: Path) -> Optional[pd.DataFrame]:
    close_events = s1_close_orders(orders)
    if close_events.empty:
        return None
    stops = close_events[close_events["action"].astype(str).str.lower().str.startswith("sl_")].copy()
    if stops.empty:
        return None
    stops["side"] = normalize_side(stops)
    stops["execution_slippage_cash"] = numeric_series(stops, "execution_slippage_cash", np.nan)
    stops["open_execution_slippage_cash"] = numeric_series(stops, "open_execution_slippage_cash", np.nan)
    stops["open_premium_cash"] = numeric_series(stops, "open_premium_cash", np.nan)
    stops["pnl"] = numeric_series(stops, "pnl")
    stops["close_slippage_pct_open_premium"] = stops["execution_slippage_cash"] / stops["open_premium_cash"].replace(0.0, np.nan)
    stops.to_csv(out_dir / "stop_slippage_distribution.csv", index=False)

    product = stops.groupby("product", dropna=False).agg(
        stop_orders=("action", "size"),
        pnl=("pnl", "sum"),
        close_slippage_cash=("execution_slippage_cash", "sum"),
        open_slippage_cash=("open_execution_slippage_cash", "sum"),
    ).sort_values("close_slippage_cash", ascending=False)
    product.reset_index().to_csv(out_dir / "stop_slippage_product_summary.csv", index=False)

    fig, axes = plt.subplots(3, 1, figsize=(14, 11))
    values = stops["execution_slippage_cash"].replace([np.inf, -np.inf], np.nan).dropna()
    axes[0].hist(values, bins=60, color="#4C78A8", alpha=0.75)
    axes[0].axvline(values.median() if len(values) else 0.0, color="#111111", linestyle="--", label="median")
    axes[0].set_title("Stop close slippage distribution")
    axes[0].set_xlabel("Close slippage cash per order")
    axes[0].set_ylabel("Count")
    axes[0].legend(loc="upper right")

    pct = stops["close_slippage_pct_open_premium"].replace([np.inf, -np.inf], np.nan).dropna()
    axes[1].hist(pct * 100.0, bins=60, color="#F58518", alpha=0.75)
    axes[1].set_xlabel("Close slippage / open premium (%)")
    axes[1].set_ylabel("Count")

    top = product.head(15).sort_values("close_slippage_cash")
    axes[2].barh(top.index.astype(str), top["close_slippage_cash"], color="#D62728", alpha=0.75)
    axes[2].set_xlabel("Total close slippage cash")
    axes[2].set_title("Top products by stop close slippage")
    save_fig(fig, out_dir, "15_stop_slippage_distribution.png")
    return stops


def plot_pc_funnel(orders: Optional[pd.DataFrame], out_dir: Path) -> Optional[pd.DataFrame]:
    opens = s1_open_orders(orders)
    closes = s1_close_orders(orders)
    if opens.empty and closes.empty:
        return None
    rows = []
    for side in ["P", "C"]:
        open_side = opens[normalize_side(opens) == side].copy() if not opens.empty else pd.DataFrame()
        close_side = closes[normalize_side(closes) == side].copy() if not closes.empty else pd.DataFrame()
        stop_side = close_side[close_side["action"].astype(str).str.lower().str.startswith("sl_")].copy() if not close_side.empty else pd.DataFrame()
        open_gross = float(numeric_series(open_side, "gross_premium_cash").sum()) if not open_side.empty else 0.0
        open_net = float(numeric_series(open_side, "net_premium_cash").sum()) if not open_side.empty else 0.0
        closed_open_premium = float(numeric_series(close_side, "open_premium_cash").sum()) if not close_side.empty else 0.0
        retained = float(numeric_series(close_side, "premium_retained_cash").sum()) if not close_side.empty else 0.0
        pnl = float(numeric_series(close_side, "pnl").sum()) if not close_side.empty else 0.0
        rows.append(
            {
                "side": side,
                "open_orders": float(len(open_side)),
                "open_lots": float(numeric_series(open_side, "quantity").sum()) if not open_side.empty else 0.0,
                "open_gross_premium": open_gross,
                "open_net_premium": open_net,
                "close_orders": float(len(close_side)),
                "stop_orders": float(len(stop_side)),
                "closed_open_premium": closed_open_premium,
                "premium_retained_cash": retained,
                "close_pnl": pnl,
                "retained_ratio": retained / closed_open_premium if closed_open_premium > 0 else np.nan,
                "stop_order_share": len(stop_side) / len(close_side) if len(close_side) else np.nan,
            }
        )
    funnel = pd.DataFrame(rows)
    funnel.to_csv(out_dir / "pc_funnel.csv", index=False)

    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    x = np.arange(len(funnel))
    axes[0].bar(x - 0.2, funnel["open_gross_premium"] / 1e4, width=0.4, label="open gross premium", color="#4C78A8")
    axes[0].bar(x + 0.2, funnel["premium_retained_cash"] / 1e4, width=0.4, label="retained premium", color="#54A24B")
    axes[0].axhline(0.0, color="#777777", linewidth=0.8)
    axes[0].set_title("Put/Call premium funnel")
    axes[0].set_ylabel("Cash (10k)")
    axes[0].legend(loc="upper left")

    axes[1].bar(x - 0.2, funnel["retained_ratio"] * 100.0, width=0.4, label="retained ratio", color="#72B7B2")
    axes[1].bar(x + 0.2, funnel["stop_order_share"] * 100.0, width=0.4, label="stop order share", color="#E45756")
    axes[1].set_ylabel("Ratio (%)")
    axes[1].legend(loc="upper left")

    axes[2].bar(x - 0.2, funnel["open_lots"], width=0.4, label="open lots", color="#F58518")
    axes[2].bar(x + 0.2, funnel["open_orders"], width=0.4, label="open orders", color="#9467BD")
    axes[2].set_ylabel("Count")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(funnel["side"])
    axes[2].set_xlabel("Side")
    axes[2].legend(loc="upper left")
    save_fig(fig, out_dir, "16_pc_funnel.png")
    return funnel


def metric_quantile_bucket(frame: pd.DataFrame, col: str, buckets: int = 5) -> Optional[pd.Series]:
    if frame.empty or col not in frame.columns:
        return None
    values = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = values.notna()
    if valid.sum() < 2:
        return None
    q = int(min(max(buckets, 2), valid.sum()))
    labels = [f"Q{i}" for i in range(1, q + 1)]
    ranked = values[valid].rank(method="first")
    bucket = pd.Series(np.nan, index=frame.index, dtype=object)
    try:
        bucket.loc[valid] = pd.qcut(ranked, q=q, labels=labels).astype(str)
    except ValueError:
        return None
    return bucket


def b2_close_bucket_summary(frame: pd.DataFrame, group_cols: List[str], metric_col: Optional[str] = None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    work["side"] = normalize_side(work)
    work["quantity"] = numeric_series(work, "quantity")
    work["open_premium_cash"] = numeric_series(work, "open_premium_cash")
    work["premium_retained_cash"] = numeric_series(work, "premium_retained_cash")
    work["pnl"] = numeric_series(work, "pnl")
    work["is_stop"] = work["action"].astype(str).str.lower().str.startswith("sl_")
    agg = {
        "close_orders": ("action", "size"),
        "lots": ("quantity", "sum"),
        "open_premium_cash": ("open_premium_cash", "sum"),
        "retained_cash": ("premium_retained_cash", "sum"),
        "pnl": ("pnl", "sum"),
        "stop_orders": ("is_stop", "sum"),
    }
    if metric_col and metric_col in work.columns:
        work[metric_col] = numeric_series(work, metric_col, np.nan)
        agg["avg_metric"] = (metric_col, "mean")
        agg["median_metric"] = (metric_col, "median")
    for col in B2_QUALITY_FIELDS + B2_SCORE_FIELDS:
        if col in work.columns and col != metric_col:
            work[col] = numeric_series(work, col, np.nan)
            agg[f"avg_{col}"] = (col, "mean")
    group = work.groupby(group_cols, dropna=False).agg(**agg).reset_index()
    group["retained_ratio"] = group["retained_cash"] / group["open_premium_cash"].replace(0.0, np.nan)
    group["pnl_to_open_premium"] = group["pnl"] / group["open_premium_cash"].replace(0.0, np.nan)
    group["stop_order_share"] = group["stop_orders"] / group["close_orders"].replace(0.0, np.nan)
    return group


def plot_b2_premium_quality_diagnostics(orders: Optional[pd.DataFrame], out_dir: Path) -> Optional[pd.DataFrame]:
    opens = s1_open_orders(orders)
    closes = s1_close_orders(orders)
    available = [col for col in (B2_QUALITY_FIELDS + B2_SCORE_FIELDS) if col in opens.columns or col in closes.columns]
    if not available:
        return None

    if not opens.empty:
        open_work = opens.copy()
        open_work["side"] = normalize_side(open_work)
        open_work["quantity"] = numeric_series(open_work, "quantity")
        open_work["gross_premium_cash"] = numeric_series(open_work, "gross_premium_cash")
        open_work["net_premium_cash"] = numeric_series(open_work, "net_premium_cash")
        open_agg = {
            "open_orders": ("action", "size"),
            "lots": ("quantity", "sum"),
            "gross_premium_cash": ("gross_premium_cash", "sum"),
            "net_premium_cash": ("net_premium_cash", "sum"),
        }
        for col in available:
            if col in open_work.columns:
                open_work[col] = numeric_series(open_work, col, np.nan)
                open_agg[f"avg_{col}"] = (col, "mean")
                open_agg[f"median_{col}"] = (col, "median")
        open_summary = open_work.groupby("side", dropna=False).agg(**open_agg).reset_index()
        open_summary["net_to_gross_premium"] = (
            open_summary["net_premium_cash"] / open_summary["gross_premium_cash"].replace(0.0, np.nan)
        )
        open_summary.to_csv(out_dir / "b2_open_quality_field_summary.csv", index=False)

    score_summary = pd.DataFrame()
    if not closes.empty and "premium_quality_score" in closes.columns:
        close_work = closes.copy()
        score_bucket = metric_quantile_bucket(close_work, "premium_quality_score")
        if score_bucket is not None:
            close_work["premium_quality_score_bucket"] = score_bucket
            close_work = close_work[close_work["premium_quality_score_bucket"].notna()].copy()
            score_summary = b2_close_bucket_summary(
                close_work,
                ["premium_quality_score_bucket", "side"],
                metric_col="premium_quality_score",
            )
            score_summary = score_summary.sort_values(["premium_quality_score_bucket", "side"], kind="mergesort")
            score_summary.to_csv(out_dir / "b2_quality_score_quintiles.csv", index=False)

    metric_rows = []
    if not closes.empty:
        for metric in B2_QUALITY_FIELDS:
            if metric not in closes.columns:
                continue
            close_work = closes.copy()
            bucket = metric_quantile_bucket(close_work, metric)
            if bucket is None:
                continue
            close_work["metric"] = metric
            close_work["metric_bucket"] = bucket
            close_work = close_work[close_work["metric_bucket"].notna()].copy()
            metric_summary = b2_close_bucket_summary(
                close_work,
                ["metric", "metric_bucket", "side"],
                metric_col=metric,
            )
            metric_rows.append(metric_summary)
    if metric_rows:
        metric_table = pd.concat(metric_rows, ignore_index=True)
        metric_table = metric_table.sort_values(["metric", "metric_bucket", "side"], kind="mergesort")
        metric_table.to_csv(out_dir / "b2_metric_quintiles.csv", index=False)

    if score_summary.empty:
        return score_summary

    labels = score_summary["premium_quality_score_bucket"].astype(str) + "-" + score_summary["side"].astype(str)
    x = np.arange(len(score_summary))
    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    axes[0].bar(x, score_summary["retained_ratio"] * 100.0, color="#4C78A8", alpha=0.8)
    axes[0].axhline(0.0, color="#777777", linewidth=0.8)
    axes[0].set_title("B2 premium quality score quintiles")
    axes[0].set_ylabel("Retained ratio (%)")

    axes[1].bar(x, score_summary["pnl_to_open_premium"] * 100.0, color="#54A24B", alpha=0.8)
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_ylabel("PnL / open premium (%)")

    axes[2].bar(x, score_summary["open_premium_cash"] / 1e4, color="#F58518", alpha=0.75, label="open premium")
    ax2 = axes[2].twinx()
    ax2.plot(x, score_summary["stop_order_share"] * 100.0, color="#D62728", marker="o", linewidth=1.0, label="stop share")
    axes[2].set_ylabel("Closed open premium (10k)")
    ax2.set_ylabel("Stop order share (%)")
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(labels, rotation=35, ha="right")
    axes[2].set_xlabel("Premium quality quintile - side")
    legend_if_any(axes[2], loc="upper left")
    legend_if_any(ax2, loc="upper right")
    save_fig(fig, out_dir, "17_b2_premium_quality_score.png")
    return score_summary


def plot_regime_exposure(nav: pd.DataFrame, out_dir: Path) -> None:
    groups = [
        ("margin", ["s1_falling_margin_pct", "s1_low_margin_pct", "s1_normal_margin_pct", "s1_high_margin_pct", "s1_post_stop_margin_pct"]),
        ("premium", ["s1_falling_open_premium_pct", "s1_low_open_premium_pct", "s1_normal_open_premium_pct", "s1_high_open_premium_pct", "s1_post_stop_open_premium_pct"]),
    ]
    if not any(any(c in nav.columns for c in cols) for _, cols in groups):
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for ax, (name, cols) in zip(axes, groups):
        for col in cols:
            if col in nav.columns:
                ax.plot(nav["date"], pct_or_ratio(nav[col]) * 100.0, label=col.replace("s1_", "").replace(f"_{name}_pct", ""), linewidth=1.1)
        ax.set_ylabel(f"{name} (% NAV)")
        ax.legend(loc="upper left", ncol=3)
    axes[0].set_title("Vol regime exposure")
    axes[-1].set_xlabel("Date")
    save_fig(fig, out_dir, "07_vol_regime_exposure.png")


def monthly_and_yearly_returns(nav: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    indexed = nav.set_index("date")["nav"]

    def resample_last(series: pd.Series, aliases: Iterable[str]) -> pd.Series:
        last_error = None
        for alias in aliases:
            try:
                return series.resample(alias).last().dropna()
            except ValueError as exc:
                last_error = exc
        raise last_error if last_error is not None else ValueError("No valid resample alias")

    monthly_nav = resample_last(indexed, ("ME", "M"))
    monthly_ret = monthly_nav.pct_change()
    if not monthly_nav.empty:
        first_month = monthly_nav.index[0]
        first_nav = indexed.loc[indexed.index <= first_month].iloc[0] if (indexed.index <= first_month).any() else indexed.iloc[0]
        monthly_ret.iloc[0] = monthly_nav.iloc[0] / first_nav - 1.0
    monthly = monthly_ret.rename("return").reset_index()
    monthly["year"] = monthly["date"].dt.year
    monthly["month"] = monthly["date"].dt.month

    yearly_nav = resample_last(indexed, ("YE", "Y"))
    yearly_ret = yearly_nav.pct_change()
    if not yearly_nav.empty:
        first_year = yearly_nav.index[0]
        first_nav = indexed.loc[indexed.index <= first_year].iloc[0] if (indexed.index <= first_year).any() else indexed.iloc[0]
        yearly_ret.iloc[0] = yearly_nav.iloc[0] / first_nav - 1.0
    yearly = yearly_ret.rename("return").reset_index()
    yearly["year"] = yearly["date"].dt.year
    return monthly, yearly


def plot_calendar_returns(monthly: pd.DataFrame, yearly: pd.DataFrame, out_dir: Path) -> None:
    if monthly.empty and yearly.empty:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))
    if not yearly.empty:
        axes[0].bar(yearly["year"].astype(str), yearly["return"] * 100.0, color=np.where(yearly["return"] >= 0, "#2ca02c", "#d62728"))
        axes[0].set_title("Yearly returns")
        axes[0].set_ylabel("Return (%)")
    if not monthly.empty:
        pivot = monthly.pivot(index="year", columns="month", values="return").sort_index()
        data = pivot.to_numpy(dtype=float) * 100.0
        vmax = np.nanmax(np.abs(data)) if np.isfinite(data).any() else 1.0
        im = axes[1].imshow(data, aspect="auto", cmap="RdYlGn", vmin=-vmax, vmax=vmax)
        axes[1].set_title("Monthly return heatmap")
        axes[1].set_yticks(range(len(pivot.index)))
        axes[1].set_yticklabels([str(x) for x in pivot.index])
        axes[1].set_xticks(range(12))
        axes[1].set_xticklabels([str(i) for i in range(1, 13)])
        for y in range(data.shape[0]):
            for x in range(data.shape[1]):
                if np.isfinite(data[y, x]):
                    axes[1].text(x, y, f"{data[y, x]:.1f}", ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=axes[1], label="Return (%)")
    save_fig(fig, out_dir, "08_calendar_returns.png")


def is_close_action(action: str) -> bool:
    action = str(action or "").strip().lower()
    return (
        action.endswith("_close")
        or action in {"close", "buy_close", "sell_close"}
        or any(action.startswith(prefix) for prefix in CLOSE_PREFIXES)
    )


def is_open_sell_action(action: str) -> bool:
    action = str(action or "").strip().lower()
    return action in {"open_sell", "sell_open"}


def reconstruct_product_lots(nav: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "action", "code", "product", "quantity"}
    if not required.issubset(orders.columns):
        return pd.DataFrame()
    orders = to_datetime_date(orders)
    nav_dates = pd.to_datetime(nav["date"]).drop_duplicates().sort_values().tolist()
    active: Dict[str, Dict[str, object]] = {}
    rows: List[Dict[str, object]] = []
    idx = 0
    order_records = orders.to_dict("records")
    for date in nav_dates:
        while idx < len(order_records) and pd.Timestamp(order_records[idx]["date"]) <= date:
            row = order_records[idx]
            idx += 1
            action = str(row.get("action", "")).strip().lower()
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            qty = float(pd.to_numeric(pd.Series([row.get("quantity", 0.0)]), errors="coerce").iloc[0] or 0.0)
            if is_open_sell_action(action):
                rec = active.setdefault(
                    code,
                    {
                        "product": str(row.get("product", "")),
                        "option_type": str(row.get("option_type", "")),
                        "strategy": str(row.get("strategy", "")),
                        "quantity": 0.0,
                    },
                )
                rec["quantity"] = float(rec.get("quantity", 0.0)) + qty
            elif is_close_action(action) and code in active:
                active[code]["quantity"] = float(active[code].get("quantity", 0.0)) - qty
                if active[code]["quantity"] <= 1e-9:
                    active.pop(code, None)
        totals: Dict[str, float] = defaultdict(float)
        for rec in active.values():
            product = str(rec.get("product", "UNKNOWN")) or "UNKNOWN"
            totals[product] += float(rec.get("quantity", 0.0))
        row = {"date": date}
        row.update(totals)
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).fillna(0.0)
    return frame.sort_values("date", kind="mergesort").reset_index(drop=True)


def plot_product_share(nav: pd.DataFrame, orders: Optional[pd.DataFrame], out_dir: Path, top_n: int) -> Optional[pd.DataFrame]:
    if orders is None or orders.empty:
        return None
    lots = reconstruct_product_lots(nav, orders)
    if lots.empty or len(lots.columns) <= 1:
        return None
    lots_path = out_dir / "product_active_sell_lots_timeseries.csv"
    lots.to_csv(lots_path, index=False)

    product_cols = [c for c in lots.columns if c != "date"]
    avg_lots = lots[product_cols].mean().sort_values(ascending=False)
    top_products = avg_lots.head(top_n).index.tolist()
    total = lots[product_cols].sum(axis=1).replace(0.0, np.nan)
    shares = lots[["date"] + top_products].copy()
    for col in top_products:
        shares[col] = lots[col] / total
    other = 1.0 - shares[top_products].sum(axis=1)
    shares["OTHER"] = other.clip(lower=0.0)
    shares.to_csv(out_dir / "product_share_top10.csv", index=False)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = shares["date"]
    y_cols = top_products + ["OTHER"]
    ax.stackplot(x, [shares[c].fillna(0.0) * 100.0 for c in y_cols], labels=y_cols, alpha=0.85)
    ax.set_title(f"Top {top_n} product active sell lot share")
    ax.set_ylabel("Share (%)")
    ax.set_xlabel("Date")
    ax.legend(loc="upper left", ncol=3, fontsize=8)
    save_fig(fig, out_dir, "09_product_share_top10.png")
    return shares


def order_summaries(orders: Optional[pd.DataFrame], out_dir: Path) -> None:
    if orders is None or orders.empty:
        return
    orders = orders.copy()
    if "date" in orders.columns:
        orders["date"] = pd.to_datetime(orders["date"])
    if "action" in orders.columns:
        action_summary = orders.groupby("action").agg(
            count=("action", "size"),
            quantity=("quantity", "sum") if "quantity" in orders.columns else ("action", "size"),
            fee=("fee", "sum") if "fee" in orders.columns else ("action", "size"),
            pnl=("pnl", "sum") if "pnl" in orders.columns else ("action", "size"),
        )
        action_summary.to_csv(out_dir / "order_action_summary.csv")
        fig, ax = plt.subplots(figsize=(12, 5))
        action_summary["count"].sort_values(ascending=False).plot(kind="bar", ax=ax, color="#1f77b4")
        ax.set_title("Order action count")
        ax.set_ylabel("Count")
        save_fig(fig, out_dir, "10_order_action_summary.png")

        close_events = orders[orders["action"].astype(str).str.lower().map(is_close_action)].copy()
        if not close_events.empty and "date" in close_events.columns:
            daily_events = close_events.groupby(["date", "action"]).size().unstack(fill_value=0)
            daily_events.to_csv(out_dir / "daily_close_event_summary.csv")
            fig, ax = plt.subplots(figsize=(14, 6))
            daily_events.plot(kind="bar", stacked=True, ax=ax, width=0.8)
            ax.set_title("Close event timeline")
            ax.set_ylabel("Event count")
            ax.set_xlabel("Date")
            legend_if_any(ax, loc="upper left", ncol=3, fontsize=8)
            save_fig(fig, out_dir, "11_close_event_timeline.png")

            stop_events = close_events[close_events["action"].astype(str).str.lower().str.startswith("sl_")].copy()
            if not stop_events.empty and "product" in stop_events.columns:
                stop_summary = stop_events.groupby(["product", "action"]).agg(
                    count=("action", "size"),
                    quantity=("quantity", "sum") if "quantity" in stop_events.columns else ("action", "size"),
                    pnl=("pnl", "sum") if "pnl" in stop_events.columns else ("action", "size"),
                ).sort_values("count", ascending=False)
                stop_summary.to_csv(out_dir / "stop_loss_product_summary.csv")

    if {"product", "action"}.issubset(orders.columns):
        opens = orders[orders["action"].astype(str).str.lower().map(is_open_sell_action)].copy()
        if not opens.empty:
            product_summary = opens.groupby("product").agg(
                open_sell_orders=("product", "size"),
                open_sell_lots=("quantity", "sum") if "quantity" in opens.columns else ("product", "size"),
            ).sort_values("open_sell_lots", ascending=False)
            product_summary.to_csv(out_dir / "open_sell_product_summary.csv")


def table_outputs(nav: pd.DataFrame, metrics: Dict[str, float], out_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics_df = pd.DataFrame([metrics])
    metrics_df.to_csv(out_dir / "summary_metrics.csv", index=False)

    worst_cols = ["date", "nav", "daily_return", "daily_pnl", "drawdown"]
    worst = nav.sort_values("daily_return", kind="mergesort").head(20)[worst_cols]
    worst.to_csv(out_dir / "worst_20_days.csv", index=False)

    monthly, yearly = monthly_and_yearly_returns(nav)
    monthly.to_csv(out_dir / "monthly_returns.csv", index=False)
    yearly.to_csv(out_dir / "yearly_returns.csv", index=False)
    return worst, monthly, yearly


def write_report(
    tag: str,
    nav_path: Path,
    orders_path: Optional[Path],
    diagnostics_path: Optional[Path],
    nav: pd.DataFrame,
    metrics: Dict[str, float],
    worst: pd.DataFrame,
    out_dir: Path,
) -> None:
    lines = [
        f"# Backtest Analysis Pack - {tag}",
        "",
        "## Inputs",
        "",
        f"- NAV: `{nav_path}`",
        f"- Orders: `{orders_path}`" if orders_path and orders_path.exists() else "- Orders: not found",
        f"- Diagnostics: `{diagnostics_path}`" if diagnostics_path and diagnostics_path.exists() else "- Diagnostics: not found",
        "",
        "## Core Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Start date | {nav['date'].iloc[0].date()} |",
        f"| End date | {nav['date'].iloc[-1].date()} |",
        f"| Trading rows | {len(nav):,} |",
        f"| Start NAV | {fmt_num(metrics['start_nav'])} |",
        f"| Final NAV | {fmt_num(metrics['final_nav'])} |",
        f"| Total return | {fmt_pct(metrics['total_return'])} |",
        f"| CAGR | {fmt_pct(metrics['cagr'])} |",
        f"| Annual vol | {fmt_pct(metrics['ann_vol'])} |",
        f"| Sharpe | {fmt_num(metrics['sharpe'])} |",
        f"| Sortino | {fmt_num(metrics['sortino'])} |",
        f"| Calmar | {fmt_num(metrics['calmar'])} |",
        f"| Max drawdown | {fmt_pct(metrics['max_drawdown'])} |",
        f"| Current drawdown | {fmt_pct(metrics['current_drawdown'])} |",
        f"| Avg margin | {fmt_pct(metrics['avg_margin_pct'])} |",
        f"| Max margin | {fmt_pct(metrics['max_margin_pct'])} |",
        f"| Cum fee | {fmt_num(metrics['cum_fee'])} |",
        f"| Cum S1 PnL | {fmt_num(metrics['cum_s1_pnl'])} |",
        f"| Cum theta PnL | {fmt_num(metrics['cum_theta_pnl'])} |",
        f"| Cum vega PnL | {fmt_num(metrics['cum_vega_pnl'])} |",
        f"| Total open gross premium | {fmt_num(metrics.get('total_open_gross_premium', np.nan))} |",
        f"| Avg daily open gross premium / NAV | {fmt_pct(metrics.get('avg_daily_open_gross_premium_pct_nav', np.nan))} |",
        f"| Vega PnL / gross premium | {fmt_pct(metrics.get('vega_pnl_to_gross_premium', np.nan))} |",
        f"| Vega loss / gross premium | {fmt_pct(metrics.get('vega_loss_to_gross_premium', np.nan))} |",
        f"| Closed premium retained ratio | {fmt_pct(metrics.get('closed_premium_retained_ratio', np.nan))} |",
        "",
        "## Worst Days",
        "",
        "| Date | Daily return | Daily PnL | Drawdown |",
        "|---|---:|---:|---:|",
    ]
    for _, row in worst.head(10).iterrows():
        lines.append(
            f"| {pd.Timestamp(row['date']).date()} | {fmt_pct(row['daily_return'])} | "
            f"{fmt_num(row['daily_pnl'])} | {fmt_pct(row['drawdown'])} |"
        )
    lines += [
        "",
        "## Generated Files",
        "",
    ]
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "analysis_report.md":
            lines.append(f"- `{path.name}`")
    (out_dir / "analysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_baseline_inputs(
    args: argparse.Namespace,
) -> Optional[Tuple[pd.DataFrame, Path, Optional[pd.DataFrame], Optional[Path], str]]:
    if not args.baseline_tag and not args.baseline_nav:
        return None
    output_dir = Path(args.output_dir).expanduser().resolve()
    nav_path = resolve_path(args.baseline_nav) or find_by_tag(output_dir, "nav", args.baseline_tag)
    if nav_path is None or not nav_path.exists():
        raise FileNotFoundError("Baseline NAV CSV not found. Provide --baseline-tag or --baseline-nav.")
    tag = args.baseline_tag or infer_tag(nav_path)
    orders_path = resolve_path(args.baseline_orders) or find_by_tag(output_dir, "orders", tag)
    nav = pd.read_csv(nav_path)
    orders = read_csv_optional(orders_path)
    return nav, nav_path, orders, orders_path, tag


def clip_to_common_dates(left: pd.DataFrame, right: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[pd.Timestamp]]:
    left_dates = set(pd.to_datetime(left["date"]))
    right_dates = set(pd.to_datetime(right["date"]))
    common_dates = sorted(left_dates.intersection(right_dates))
    if not common_dates:
        raise ValueError("No common dates between candidate and baseline NAV.")
    common_index = pd.DatetimeIndex(common_dates)
    left_clip = enrich_nav(left[pd.to_datetime(left["date"]).isin(common_index)].copy())
    right_clip = enrich_nav(right[pd.to_datetime(right["date"]).isin(common_index)].copy())
    return left_clip, right_clip, common_dates


def comparison_metric_rows(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    candidate_orders: Optional[pd.DataFrame] = None,
    baseline_orders: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    cand_metrics = calc_metrics(candidate)
    base_metrics = calc_metrics(baseline)
    cand_metrics.update(premium_quality_metrics(candidate, candidate_orders))
    base_metrics.update(premium_quality_metrics(baseline, baseline_orders))
    rows = []
    keys = [
        "start_nav",
        "final_nav",
        "total_return",
        "cagr",
        "ann_vol",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "current_drawdown",
        "worst_day_return",
        "avg_margin_pct",
        "max_margin_pct",
        "cum_fee",
        "cum_s1_pnl",
        "cum_delta_pnl",
        "cum_gamma_pnl",
        "cum_theta_pnl",
        "cum_vega_pnl",
        "cum_residual_pnl",
        "total_open_gross_premium",
        "total_open_net_premium",
        "avg_daily_open_gross_premium_pct_nav",
        "avg_open_day_gross_premium_pct_nav",
        "vega_pnl_to_gross_premium",
        "vega_loss_to_gross_premium",
        "gamma_loss_to_gross_premium",
        "theta_to_gross_premium",
        "s1_pnl_to_gross_premium",
        "closed_premium_retained_ratio",
    ]
    for key in keys:
        cand_value = cand_metrics.get(key, np.nan)
        base_value = base_metrics.get(key, np.nan)
        rows.append(
            {
                "metric": key,
                "candidate": cand_value,
                "baseline": base_value,
                "diff": cand_value - base_value if pd.notna(cand_value) and pd.notna(base_value) else np.nan,
            }
        )
    extra_cols = [
        "n_positions",
        "s1_active_sell_lots",
        "s1_active_sell_contracts",
        "s1_active_sell_products",
        "s1_put_call_lot_ratio",
        "s1_call_lot_share",
        "s1_stress_loss_used_pct",
        "s1_short_open_premium_pct",
    ]
    for col in extra_cols:
        if col in candidate.columns or col in baseline.columns:
            cand_value = float(numeric_series(candidate, col, np.nan).mean())
            base_value = float(numeric_series(baseline, col, np.nan).mean())
            rows.append(
                {
                    "metric": f"avg_{col}",
                    "candidate": cand_value,
                    "baseline": base_value,
                    "diff": cand_value - base_value if pd.notna(cand_value) and pd.notna(base_value) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def plot_compare_nav(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    cand_norm = candidate["nav"] / candidate["nav"].iloc[0]
    base_norm = baseline["nav"] / baseline["nav"].iloc[0]
    excess = cand_norm / base_norm - 1.0
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(candidate["date"], cand_norm, label=candidate_label, linewidth=1.5, color="#1f77b4")
    axes[0].plot(baseline["date"], base_norm, label=baseline_label, linewidth=1.5, color="#7f7f7f")
    axes[0].set_title("NAV relative to baseline")
    axes[0].set_ylabel("Normalized NAV")
    axes[0].legend(loc="upper left")
    axes[1].plot(candidate["date"], excess * 100.0, label=f"{candidate_label} excess vs {baseline_label}", color="#2ca02c")
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_ylabel("Excess (%)")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left")
    save_fig(fig, out_dir, "compare_01_nav_relative_to_b0.png")


def plot_compare_drawdown(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.fill_between(candidate["date"], candidate["drawdown"] * 100.0, 0.0, alpha=0.25, color="#1f77b4", label=candidate_label)
    ax.fill_between(baseline["date"], baseline["drawdown"] * 100.0, 0.0, alpha=0.25, color="#d62728", label=baseline_label)
    cand_min = candidate.loc[candidate["drawdown"].idxmin()]
    base_min = baseline.loc[baseline["drawdown"].idxmin()]
    ax.scatter([cand_min["date"]], [cand_min["drawdown"] * 100.0], color="#1f77b4", zorder=3)
    ax.scatter([base_min["date"]], [base_min["drawdown"] * 100.0], color="#d62728", zorder=3)
    ax.set_title("Drawdown relative to baseline")
    ax.set_ylabel("Drawdown (%)")
    ax.set_xlabel("Date")
    ax.legend(loc="lower left")
    save_fig(fig, out_dir, "compare_02_drawdown_relative_to_b0.png")


def plot_compare_margin_position(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    specs = [
        ("margin_pct", "Margin (%)", True),
        ("s1_active_sell_products", "Active products", False),
        ("s1_active_sell_contracts", "Active contracts", False),
        ("s1_active_sell_lots", "Active lots", False),
    ]
    fig, axes = plt.subplots(len(specs), 1, figsize=(14, 11), sharex=True)
    for ax, (col, label, is_pct) in zip(axes, specs):
        if col in candidate.columns:
            cand = numeric_series(candidate, col, np.nan)
            if is_pct:
                cand = pct_or_ratio(cand) * 100.0
            ax.plot(candidate["date"], cand, label=candidate_label, color="#1f77b4", linewidth=1.1)
        if col in baseline.columns:
            base = numeric_series(baseline, col, np.nan)
            if is_pct:
                base = pct_or_ratio(base) * 100.0
            ax.plot(baseline["date"], base, label=baseline_label, color="#7f7f7f", linewidth=1.1)
        ax.set_ylabel(label)
        legend_if_any(ax, loc="upper left")
    axes[0].set_title("Margin and position load relative to baseline")
    axes[-1].set_xlabel("Date")
    save_fig(fig, out_dir, "compare_03_margin_position_relative_to_b0.png")


def plot_compare_greek_attribution(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    cols = [c for c in ATTRIBUTION_COLS if c in candidate.columns or c in baseline.columns]
    if not cols:
        return
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for col in cols:
        axes[0].plot(candidate["date"], numeric_series(candidate, col).cumsum() / 1e4, label=f"{candidate_label} {col.replace('_pnl', '')}", linewidth=1.0)
        axes[0].plot(baseline["date"], numeric_series(baseline, col).cumsum() / 1e4, label=f"{baseline_label} {col.replace('_pnl', '')}", linestyle="--", linewidth=0.9)
        diff = numeric_series(candidate, col).cumsum() - numeric_series(baseline, col).cumsum()
        axes[1].plot(candidate["date"], diff / 1e4, label=col.replace("_pnl", ""), linewidth=1.1)
    axes[0].set_title("Greek attribution relative to baseline")
    axes[0].set_ylabel("Cumulative PnL (10k)")
    axes[0].legend(loc="upper left", ncol=3, fontsize=8)
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_ylabel("Candidate - baseline (10k)")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left", ncol=3)
    save_fig(fig, out_dir, "compare_04_greek_attribution_relative_to_b0.png")


def plot_compare_daily_tail(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))
    axes[0].plot(candidate["date"], candidate["daily_return"] * 100.0, label=candidate_label, linewidth=0.9, alpha=0.8)
    axes[0].plot(baseline["date"], baseline["daily_return"] * 100.0, label=baseline_label, linewidth=0.9, alpha=0.8)
    axes[0].axhline(0.0, color="#777777", linewidth=0.8)
    axes[0].set_title("Daily PnL tail relative to baseline")
    axes[0].set_ylabel("Daily return (%)")
    axes[0].legend(loc="upper left")
    axes[1].hist(candidate["daily_return"] * 100.0, bins=60, alpha=0.55, label=candidate_label, color="#1f77b4")
    axes[1].hist(baseline["daily_return"] * 100.0, bins=60, alpha=0.55, label=baseline_label, color="#7f7f7f")
    for frame, color, label in [(candidate, "#1f77b4", candidate_label), (baseline, "#7f7f7f", baseline_label)]:
        axes[1].axvline(frame["daily_return"].quantile(0.01) * 100.0, color=color, linestyle="--", label=f"{label} 1%")
    axes[1].set_xlabel("Daily return (%)")
    axes[1].set_ylabel("Frequency")
    axes[1].legend(loc="upper left")
    save_fig(fig, out_dir, "compare_05_daily_pnl_tail_relative_to_b0.png")


def plot_compare_pc_structure(candidate: pd.DataFrame, baseline: pd.DataFrame, out_dir: Path, candidate_label: str, baseline_label: str) -> None:
    if not any(c in candidate.columns or c in baseline.columns for c in ["s1_put_call_lot_ratio", "s1_call_lot_share", "s1_call_open_premium_pct", "s1_put_open_premium_pct"]):
        return
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    for frame, label, color in [(candidate, candidate_label, "#1f77b4"), (baseline, baseline_label, "#7f7f7f")]:
        if "s1_put_call_lot_ratio" in frame.columns:
            axes[0].plot(frame["date"], numeric_series(frame, "s1_put_call_lot_ratio", np.nan), label=label, color=color, linewidth=1.0)
        if "s1_call_lot_share" in frame.columns:
            axes[1].plot(frame["date"], pct_or_ratio(numeric_series(frame, "s1_call_lot_share", np.nan)) * 100.0, label=label, color=color, linewidth=1.0)
        for col, linestyle in [("s1_call_open_premium_pct", "-"), ("s1_put_open_premium_pct", "--")]:
            if col in frame.columns:
                axes[2].plot(frame["date"], pct_or_ratio(numeric_series(frame, col, np.nan)) * 100.0, label=f"{label} {col.replace('s1_', '').replace('_pct', '')}", color=color, linestyle=linestyle, linewidth=1.0)
    axes[0].set_title("P/C structure relative to baseline")
    axes[0].set_ylabel("P/C ratio")
    axes[1].axhline(50.0, color="#777777", linewidth=0.8, linestyle="--")
    axes[1].set_ylabel("Call lot share (%)")
    axes[2].set_ylabel("Premium (% NAV)")
    axes[2].set_xlabel("Date")
    for ax in axes:
        legend_if_any(ax, loc="upper left", ncol=2, fontsize=8)
    save_fig(fig, out_dir, "compare_06_pc_structure_relative_to_b0.png")


def average_product_share(nav: pd.DataFrame, orders: Optional[pd.DataFrame]) -> pd.Series:
    if orders is None or orders.empty:
        return pd.Series(dtype=float)
    lots = reconstruct_product_lots(nav, orders)
    if lots.empty or len(lots.columns) <= 1:
        return pd.Series(dtype=float)
    product_cols = [c for c in lots.columns if c != "date"]
    total = lots[product_cols].sum(axis=1).replace(0.0, np.nan)
    shares = lots[product_cols].div(total, axis=0)
    return shares.mean().sort_values(ascending=False)


def plot_compare_product_exposure(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    candidate_orders: Optional[pd.DataFrame],
    baseline_orders: Optional[pd.DataFrame],
    out_dir: Path,
    candidate_label: str,
    baseline_label: str,
    top_n: int,
) -> None:
    cand_share = average_product_share(candidate, candidate_orders)
    base_share = average_product_share(baseline, baseline_orders)
    if cand_share.empty and base_share.empty:
        return
    top_products = pd.concat([cand_share, base_share], axis=1).fillna(0.0).sum(axis=1).sort_values(ascending=False).head(top_n).index
    data = pd.DataFrame({candidate_label: cand_share.reindex(top_products).fillna(0.0), baseline_label: base_share.reindex(top_products).fillna(0.0)})
    data.to_csv(out_dir / "compare_product_average_share.csv")
    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(top_products))
    width = 0.38
    ax.bar(x - width / 2, data[candidate_label] * 100.0, width=width, label=candidate_label, color="#1f77b4")
    ax.bar(x + width / 2, data[baseline_label] * 100.0, width=width, label=baseline_label, color="#7f7f7f")
    ax.set_xticks(x)
    ax.set_xticklabels(top_products, rotation=45, ha="right")
    ax.set_ylabel("Average active lot share (%)")
    ax.set_title("Product exposure relative to baseline")
    ax.legend(loc="upper right")
    save_fig(fig, out_dir, "compare_07_product_exposure_relative_to_b0.png")


def daily_stop_summary(nav: pd.DataFrame, orders: Optional[pd.DataFrame]) -> pd.DataFrame:
    dates = pd.DataFrame({"date": pd.to_datetime(nav["date"])})
    if orders is None or orders.empty or "date" not in orders.columns or "action" not in orders.columns:
        dates["stop_count"] = 0
        dates["stop_pnl"] = 0.0
        return dates
    frame = orders.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    action = frame["action"].astype(str).str.lower()
    stops = frame[action.str.startswith("sl_")].copy()
    if stops.empty:
        dates["stop_count"] = 0
        dates["stop_pnl"] = 0.0
        return dates
    grouped = stops.groupby("date").agg(
        stop_count=("action", "size"),
        stop_pnl=("pnl", "sum") if "pnl" in stops.columns else ("action", "size"),
    ).reset_index()
    return dates.merge(grouped, on="date", how="left").fillna({"stop_count": 0, "stop_pnl": 0.0})


def plot_compare_stop_cluster(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    candidate_orders: Optional[pd.DataFrame],
    baseline_orders: Optional[pd.DataFrame],
    out_dir: Path,
    candidate_label: str,
    baseline_label: str,
) -> None:
    cand = daily_stop_summary(candidate, candidate_orders)
    base = daily_stop_summary(baseline, baseline_orders)
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    axes[0].bar(cand["date"], cand["stop_count"], label=candidate_label, color="#1f77b4", alpha=0.55, width=1.0)
    axes[0].bar(base["date"], base["stop_count"], label=baseline_label, color="#7f7f7f", alpha=0.45, width=1.0)
    axes[0].set_title("Stop cluster relative to baseline")
    axes[0].set_ylabel("Stop count")
    axes[0].legend(loc="upper left")
    axes[1].bar(cand["date"], cand["stop_pnl"], label=candidate_label, color="#1f77b4", alpha=0.55, width=1.0)
    axes[1].bar(base["date"], base["stop_pnl"], label=baseline_label, color="#7f7f7f", alpha=0.45, width=1.0)
    axes[1].axhline(0.0, color="#777777", linewidth=0.8)
    axes[1].set_ylabel("Stop PnL")
    axes[1].set_xlabel("Date")
    axes[1].legend(loc="upper left")
    save_fig(fig, out_dir, "compare_08_stop_cluster_relative_to_b0.png")


def generate_baseline_comparison(
    candidate_nav: pd.DataFrame,
    candidate_orders: Optional[pd.DataFrame],
    baseline_nav_raw: pd.DataFrame,
    baseline_orders: Optional[pd.DataFrame],
    out_dir: Path,
    candidate_label: str,
    baseline_label: str,
    top_n: int,
) -> None:
    candidate_common, baseline_common, _ = clip_to_common_dates(candidate_nav, baseline_nav_raw)
    comparison_metric_rows(candidate_common, baseline_common, candidate_orders, baseline_orders).to_csv(
        out_dir / "comparison_summary.csv",
        index=False,
    )
    plot_compare_nav(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_drawdown(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_margin_position(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_greek_attribution(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_daily_tail(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_pc_structure(candidate_common, baseline_common, out_dir, candidate_label, baseline_label)
    plot_compare_product_exposure(
        candidate_common,
        baseline_common,
        candidate_orders,
        baseline_orders,
        out_dir,
        candidate_label,
        baseline_label,
        top_n,
    )
    plot_compare_stop_cluster(
        candidate_common,
        baseline_common,
        candidate_orders,
        baseline_orders,
        out_dir,
        candidate_label,
        baseline_label,
    )


def main() -> None:
    configure_plot_style()
    args = parse_args()
    nav_raw, nav_path, tag, out_dir, orders_path, diagnostics_path = require_nav(args)
    nav = enrich_nav(nav_raw)
    orders = read_csv_optional(orders_path)
    diagnostics = read_csv_optional(diagnostics_path)
    if diagnostics is not None:
        diagnostics.to_csv(out_dir / "diagnostics_copy.csv", index=False)

    metrics = calc_metrics(nav)
    metrics.update(premium_quality_metrics(nav, orders))
    worst, monthly, yearly = table_outputs(nav, metrics, out_dir)

    plot_nav_drawdown(nav, metrics, out_dir)
    plot_margin_positions(nav, out_dir)
    plot_greeks(nav, out_dir)
    plot_pnl_attribution(nav, out_dir, args.rolling_window)
    plot_daily_tail(nav, out_dir)
    plot_premium_pc(nav, out_dir)
    plot_premium_vega_quality(nav, orders, out_dir)
    plot_regime_exposure(nav, out_dir)
    plot_calendar_returns(monthly, yearly, out_dir)
    plot_product_share(nav, orders, out_dir, args.top_n)
    order_summaries(orders, out_dir)
    plot_tail_product_side_contribution(nav, orders, out_dir)
    plot_vega_quality_by_bucket(orders, out_dir)
    plot_stop_slippage_distribution(orders, out_dir)
    plot_pc_funnel(orders, out_dir)
    plot_b2_premium_quality_diagnostics(orders, out_dir)
    baseline_inputs = load_baseline_inputs(args)
    if baseline_inputs is not None:
        baseline_nav, baseline_nav_path, baseline_orders, baseline_orders_path, baseline_tag = baseline_inputs
        candidate_label = args.candidate_label or tag
        baseline_label = args.baseline_label or baseline_tag
        generate_baseline_comparison(
            nav,
            orders,
            baseline_nav,
            baseline_orders,
            out_dir,
            candidate_label,
            baseline_label,
            args.top_n,
        )
    write_report(tag, nav_path, orders_path, diagnostics_path, nav, metrics, worst, out_dir)

    print(f"Analysis pack written to: {out_dir}")
    if baseline_inputs is not None:
        print(f"Baseline comparison written against: {baseline_nav_path}")
        if baseline_orders_path is not None:
            print(f"Baseline orders: {baseline_orders_path}")
    print(f"Total return: {fmt_pct(metrics['total_return'])}")
    print(f"Max drawdown: {fmt_pct(metrics['max_drawdown'])}")
    print(f"Sharpe: {fmt_num(metrics['sharpe'])}")
    print(f"Calmar: {fmt_num(metrics['calmar'])}")


if __name__ == "__main__":
    main()
