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
    worst, monthly, yearly = table_outputs(nav, metrics, out_dir)

    plot_nav_drawdown(nav, metrics, out_dir)
    plot_margin_positions(nav, out_dir)
    plot_greeks(nav, out_dir)
    plot_pnl_attribution(nav, out_dir, args.rolling_window)
    plot_daily_tail(nav, out_dir)
    plot_premium_pc(nav, out_dir)
    plot_regime_exposure(nav, out_dir)
    plot_calendar_returns(monthly, yearly, out_dir)
    plot_product_share(nav, orders, out_dir, args.top_n)
    order_summaries(orders, out_dir)
    write_report(tag, nav_path, orders_path, diagnostics_path, nav, metrics, worst, out_dir)

    print(f"Analysis pack written to: {out_dir}")
    print(f"Total return: {fmt_pct(metrics['total_return'])}")
    print(f"Max drawdown: {fmt_pct(metrics['max_drawdown'])}")
    print(f"Sharpe: {fmt_num(metrics['sharpe'])}")
    print(f"Calmar: {fmt_num(metrics['calmar'])}")


if __name__ == "__main__":
    main()
