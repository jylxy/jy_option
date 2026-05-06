from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SERIES = {
    "P0 基准(B2C+1.5X)": "s1_b2c_stop15_2022_latest",
    "P1 排除不适合": "s1_b2c_stop15_exclude_unsuitable_2022_latest",
    "P2 流动性Top20": "s1_b2c_stop15_liq_top20_2022_latest",
    "P3 乐得主流": "s1_b2c_stop15_ledet_mainstream_2022_latest",
    "P3B 乐得期限偏好": "s1_b2c_stop15_ledet_term_pref_2022_latest",
}

BASELINE_NAME = "P0 基准(B2C+1.5X)"
REPORT_TAG = "s1_product_pool_p_experiments_2022_latest"

NAV_NUMERIC_COLS = [
    "nav",
    "s1_pnl",
    "fee",
    "margin_used",
    "cash_delta",
    "cash_vega",
    "cash_gamma",
    "delta_pnl",
    "gamma_pnl",
    "theta_pnl",
    "vega_pnl",
    "residual_pnl",
    "s1_short_open_premium",
    "s1_short_liability",
    "s1_short_unrealized_premium",
    "s1_margin_used",
    "s1_call_lot_share",
    "s1_put_call_lot_ratio",
    "s1_active_sell_products",
    "s1_active_sell_contracts",
    "s1_active_sell_lots",
    "s1_active_call_lots",
    "s1_active_put_lots",
]

ORDER_NUMERIC_COLS = [
    "quantity",
    "pnl",
    "fee",
    "gross_premium_cash",
    "net_premium_cash",
    "open_premium_cash",
    "close_value_cash",
    "premium_retained_cash",
    "premium_retained_pct",
    "execution_slippage_cash",
    "open_execution_slippage_cash",
]


def setup_matplotlib() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 140
    plt.rcParams["savefig.dpi"] = 170


def pct(x: float) -> str:
    if pd.isna(x):
        return "N/A"
    return f"{x * 100:.2f}%"


def money_wan(x: float) -> str:
    if pd.isna(x):
        return "N/A"
    return f"{x / 10000:.1f}万"


def num(x: float, digits: int = 2) -> str:
    if pd.isna(x):
        return "N/A"
    return f"{x:.{digits}f}"


def read_nav(output_dir: Path, tag: str) -> pd.DataFrame:
    path = output_dir / f"nav_{tag}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    for col in NAV_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    df["daily_return"] = df["nav"].pct_change().fillna(0.0)
    df["indexed_nav"] = df["nav"] / df["nav"].iloc[0]
    df["drawdown"] = df["nav"] / df["nav"].cummax() - 1.0
    df["margin_pct"] = df["margin_used"] / df["nav"]
    return df


def read_orders(output_dir: Path, tag: str) -> pd.DataFrame:
    path = output_dir / f"orders_{tag}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    for col in ORDER_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def nav_metrics(nav: pd.DataFrame) -> Dict[str, float]:
    total_return = nav["nav"].iloc[-1] / nav["nav"].iloc[0] - 1.0
    days = len(nav)
    ann_return = (1.0 + total_return) ** (252.0 / max(days - 1, 1)) - 1.0
    ann_vol = nav["daily_return"].std(ddof=0) * np.sqrt(252.0)
    max_dd = nav["drawdown"].min()
    return {
        "start": nav["date"].iloc[0],
        "end": nav["date"].iloc[-1],
        "days": days,
        "final_nav": nav["nav"].iloc[-1],
        "total_return": total_return,
        "cagr": ann_return,
        "ann_vol": ann_vol,
        "sharpe": ann_return / ann_vol if ann_vol else np.nan,
        "max_drawdown": max_dd,
        "calmar": ann_return / abs(max_dd) if max_dd else np.nan,
        "avg_margin_pct": nav["margin_pct"].mean(),
        "max_margin_pct": nav["margin_pct"].max(),
        "end_margin_pct": nav["margin_pct"].iloc[-1],
        "cum_theta_pnl": nav.get("theta_pnl", pd.Series(dtype=float)).sum(),
        "cum_vega_pnl": nav.get("vega_pnl", pd.Series(dtype=float)).sum(),
        "cum_gamma_pnl": nav.get("gamma_pnl", pd.Series(dtype=float)).sum(),
        "cum_delta_pnl": nav.get("delta_pnl", pd.Series(dtype=float)).sum(),
        "cum_residual_pnl": nav.get("residual_pnl", pd.Series(dtype=float)).sum(),
        "total_fee": nav.get("fee", pd.Series(dtype=float)).sum(),
        "avg_products": nav.get("s1_active_sell_products", pd.Series(dtype=float)).mean(),
        "avg_contracts": nav.get("s1_active_sell_contracts", pd.Series(dtype=float)).mean(),
        "avg_lots": nav.get("s1_active_sell_lots", pd.Series(dtype=float)).mean(),
        "avg_call_lot_share": nav.get("s1_call_lot_share", pd.Series(dtype=float)).mean(),
        "avg_pc_ratio": nav.get("s1_put_call_lot_ratio", pd.Series(dtype=float)).replace([np.inf, -np.inf], np.nan).mean(),
    }


def order_metrics(orders: pd.DataFrame) -> Dict[str, float]:
    open_orders = orders[orders["action"].eq("open_sell")]
    close_orders = orders[~orders["action"].eq("open_sell")]
    stops = orders[orders["action"].astype(str).str.contains("sl", case=False, na=False)]
    expiry = orders[orders["action"].eq("expiry")]
    gross_premium = open_orders.get("gross_premium_cash", pd.Series(dtype=float)).sum()
    net_premium = open_orders.get("net_premium_cash", pd.Series(dtype=float)).sum()
    retained = close_orders.get("premium_retained_cash", pd.Series(dtype=float)).sum()
    open_ref = close_orders.get("open_premium_cash", pd.Series(dtype=float)).sum()
    side_lots = open_orders.groupby("option_type")["quantity"].sum().to_dict() if not open_orders.empty else {}
    return {
        "open_orders": len(open_orders),
        "close_orders": len(close_orders),
        "stop_orders": len(stops),
        "expiry_orders": len(expiry),
        "products_traded": open_orders["product"].nunique() if "product" in open_orders.columns else np.nan,
        "total_open_gross_premium": gross_premium,
        "total_open_net_premium": net_premium,
        "closed_premium_retained": retained,
        "closed_open_premium_ref": open_ref,
        "closed_premium_retained_ratio": retained / open_ref if open_ref else np.nan,
        "stop_pnl": stops.get("pnl", pd.Series(dtype=float)).sum(),
        "expiry_pnl": expiry.get("pnl", pd.Series(dtype=float)).sum(),
        "open_call_lots": side_lots.get("C", 0.0),
        "open_put_lots": side_lots.get("P", 0.0),
        "open_put_call_lot_ratio": side_lots.get("P", 0.0) / side_lots.get("C", np.nan) if side_lots.get("C", 0.0) else np.nan,
    }


def product_summary(orders: pd.DataFrame) -> pd.DataFrame:
    open_orders = orders[orders["action"].eq("open_sell")].copy()
    if open_orders.empty:
        return pd.DataFrame()
    grouped = open_orders.groupby("product", dropna=False).agg(
        gross_premium=("gross_premium_cash", "sum"),
        net_premium=("net_premium_cash", "sum"),
        lots=("quantity", "sum"),
        orders=("product", "count"),
    )
    total_premium = grouped["gross_premium"].sum()
    grouped["premium_share"] = grouped["gross_premium"] / total_premium if total_premium else np.nan
    return grouped.sort_values("gross_premium", ascending=False)


def close_product_summary(orders: pd.DataFrame) -> pd.DataFrame:
    close_orders = orders[~orders["action"].eq("open_sell")].copy()
    if close_orders.empty:
        return pd.DataFrame()
    grouped = close_orders.groupby(["product", "option_type", "action"], dropna=False).agg(
        pnl=("pnl", "sum"),
        retained=("premium_retained_cash", "sum"),
        open_premium=("open_premium_cash", "sum"),
        count=("action", "count"),
    ).reset_index()
    grouped["retained_ratio"] = grouped["retained"] / grouped["open_premium"].replace(0, np.nan)
    return grouped.sort_values("pnl")


def yearly_returns(nav_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    pieces = []
    for name, nav in nav_data.items():
        yr = nav.set_index("date")["nav"].groupby(lambda x: x.year).agg(["first", "last"])
        ret = (yr["last"] / yr["first"] - 1.0).rename(name)
        pieces.append(ret)
    return pd.concat(pieces, axis=1)


def align_excess(nav_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    baseline = nav_data[BASELINE_NAME][["date", "indexed_nav"]].rename(columns={"indexed_nav": BASELINE_NAME})
    out = baseline.copy()
    for name, nav in nav_data.items():
        if name == BASELINE_NAME:
            continue
        candidate = nav[["date", "indexed_nav"]].rename(columns={"indexed_nav": name})
        out = out.merge(candidate, on="date", how="outer")
    out = out.sort_values("date").ffill()
    for name in nav_data:
        if name != BASELINE_NAME:
            out[f"{name} 超额"] = out[name] - out[BASELINE_NAME]
    return out


def save_table(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=True, encoding="utf-8-sig")


def add_watermark(ax, text: str = "") -> None:
    if text:
        ax.text(0.99, 0.01, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color="#777777")


def plot_nav(nav_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.5), sharex=True)
    colors = {
        BASELINE_NAME: "#2f4f4f",
        "P1 排除不适合": "#7f7f7f",
        "P2 流动性Top20": "#1f77b4",
        "P3 乐得主流": "#ff7f0e",
        "P3B 乐得期限偏好": "#d62728",
    }
    for name, nav in nav_data.items():
        axes[0].plot(nav["date"], nav["indexed_nav"], label=name, lw=1.8, color=colors.get(name))
    excess = align_excess(nav_data)
    for col in [c for c in excess.columns if c.endswith(" 超额")]:
        axes[1].plot(excess["date"], excess[col] * 100, label=col.replace(" 超额", ""), lw=1.5, color=colors.get(col.replace(" 超额", "")))
    axes[0].set_title("NAV 指数化对比")
    axes[0].set_ylabel("NAV / 初始 NAV")
    axes[1].set_title("相对 P0 基准的超额收益")
    axes[1].set_ylabel("超额收益百分点")
    axes[1].axhline(0, color="#333333", lw=0.8)
    for ax in axes:
        ax.grid(True, alpha=0.25)
        ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_01_nav_relative_to_b0.png")
    plt.close(fig)


def plot_drawdown(nav_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(11.5, 5.3))
    for name, nav in nav_data.items():
        ax.plot(nav["date"], nav["drawdown"] * 100, label=name, lw=1.6)
    ax.set_title("回撤曲线对比")
    ax.set_ylabel("回撤(%)")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_02_drawdown_relative_to_b0.png")
    plt.close(fig)


def plot_margin_position(nav_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(11.5, 8.2), sharex=True)
    for name, nav in nav_data.items():
        axes[0].plot(nav["date"], nav["margin_pct"] * 100, label=name, lw=1.2)
        if "s1_active_sell_products" in nav:
            axes[1].plot(nav["date"], nav["s1_active_sell_products"], label=name, lw=1.2)
        if "s1_active_sell_contracts" in nav:
            axes[2].plot(nav["date"], nav["s1_active_sell_contracts"], label=name, lw=1.2)
    axes[0].set_title("保证金使用率")
    axes[1].set_title("活跃品种数")
    axes[2].set_title("活跃合约数")
    axes[0].set_ylabel("%")
    for ax in axes:
        ax.grid(True, alpha=0.25)
        ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_03_margin_position_relative_to_b0.png")
    plt.close(fig)


def plot_greek(metrics: pd.DataFrame, analysis_dir: Path) -> None:
    cols = ["cum_theta_pnl", "cum_vega_pnl", "cum_gamma_pnl", "cum_delta_pnl", "cum_residual_pnl"]
    labels = ["Theta", "Vega", "Gamma", "Delta", "Residual"]
    data = metrics[cols] / 10000.0
    data.columns = labels
    fig, ax = plt.subplots(figsize=(11.5, 5.5))
    data.plot(kind="bar", ax=ax, width=0.78)
    ax.axhline(0, color="#333333", lw=0.8)
    ax.set_title("累计 Greek / Residual 归因对比")
    ax.set_ylabel("金额(万元)")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_04_greek_attribution_relative_to_b0.png")
    plt.close(fig)


def plot_daily_tail(nav_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2))
    for name, nav in nav_data.items():
        axes[0].hist(nav["daily_return"] * 100, bins=80, alpha=0.35, label=name)
    tail = pd.DataFrame({
        name: nav["daily_return"].quantile([0.01, 0.05, 0.5]).rename({0.01: "1%分位", 0.05: "5%分位", 0.5: "中位数"})
        for name, nav in nav_data.items()
    }).T * 100
    tail.plot(kind="bar", ax=axes[1])
    axes[0].set_title("日收益分布")
    axes[0].set_xlabel("日收益(%)")
    axes[0].legend(fontsize=7)
    axes[1].set_title("左尾分位对比")
    axes[1].set_ylabel("日收益(%)")
    axes[1].axhline(0, color="#333333", lw=0.8)
    for ax in axes:
        ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_05_daily_pnl_tail_relative_to_b0.png")
    plt.close(fig)


def plot_pc(nav_data: Dict[str, pd.DataFrame], order_metrics_df: pd.DataFrame, analysis_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.3))
    for name, nav in nav_data.items():
        if "s1_call_lot_share" in nav:
            axes[0].plot(nav["date"], nav["s1_call_lot_share"] * 100, label=name, lw=1.2)
    side = order_metrics_df[["open_call_lots", "open_put_lots"]].copy()
    side.columns = ["Call 开仓手数", "Put 开仓手数"]
    side.plot(kind="bar", stacked=True, ax=axes[1])
    axes[0].set_title("持仓 Call 手数占比")
    axes[0].set_ylabel("%")
    axes[1].set_title("全周期开仓手数 P/C 结构")
    axes[1].set_ylabel("手数")
    for ax in axes:
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_06_pc_structure_relative_to_b0.png")
    plt.close(fig)


def plot_product_exposure(product_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.5))
    top_products = sorted(set().union(*[set(df.head(8).index.astype(str)) for df in product_data.values() if not df.empty]))
    share_table = pd.DataFrame(index=top_products)
    for name, df in product_data.items():
        share_table[name] = df.reindex(top_products)["premium_share"]
    share_table.fillna(0.0).sort_values(BASELINE_NAME, ascending=False).plot(kind="bar", ax=axes[0])
    concentration = pd.Series({name: df["premium_share"].head(5).sum() for name, df in product_data.items()})
    concentration.plot(kind="bar", ax=axes[1], color="#b55d5d")
    axes[0].set_title("主要品种开仓权利金占比")
    axes[0].set_ylabel("占比")
    axes[1].set_title("Top5 品种权利金集中度")
    axes[1].set_ylabel("Top5 占比")
    for ax in axes:
        ax.grid(True, axis="y", alpha=0.25)
        ax.tick_params(axis="x", labelrotation=35)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_07_product_exposure_relative_to_b0.png")
    plt.close(fig)


def plot_stop_cluster(orders_data: Dict[str, pd.DataFrame], analysis_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.4))
    for name, orders in orders_data.items():
        stops = orders[orders["action"].astype(str).str.contains("sl", case=False, na=False)].copy()
        if stops.empty:
            continue
        monthly = stops.set_index("date")["pnl"].resample("M").sum()
        axes[0].plot(monthly.index, monthly.values / 10000.0, label=name, lw=1.3)
    counts = pd.DataFrame({
        name: orders[orders["action"].astype(str).str.contains("sl", case=False, na=False)]
        .set_index("date")
        .resample("M")["action"].count()
        for name, orders in orders_data.items()
    }).fillna(0)
    counts.plot(ax=axes[1], lw=1.3)
    axes[0].set_title("月度止损实现 PnL")
    axes[0].set_ylabel("万元")
    axes[1].set_title("月度止损次数")
    for ax in axes:
        ax.axhline(0, color="#333333", lw=0.8)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(analysis_dir / "compare_08_stop_cluster_relative_to_b0.png")
    plt.close(fig)


def markdown_table(df: pd.DataFrame, columns: Iterable[str]) -> str:
    table = df.loc[:, list(columns)].copy()
    return table.to_markdown()


def write_report(
    report_path: Path,
    analysis_dir: Path,
    metrics: pd.DataFrame,
    yearly: pd.DataFrame,
    product_data: Dict[str, pd.DataFrame],
) -> None:
    p0 = metrics.loc[BASELINE_NAME]
    p3 = metrics.loc["P3 乐得主流"]
    p3b = metrics.loc["P3B 乐得期限偏好"]
    p2 = metrics.loc["P2 流动性Top20"]
    p1 = metrics.loc["P1 排除不适合"]

    perf = metrics[[
        "total_return",
        "cagr",
        "ann_vol",
        "max_drawdown",
        "sharpe",
        "calmar",
        "excess_total_return",
        "avg_margin_pct",
        "max_margin_pct",
    ]].copy()
    for col in ["total_return", "cagr", "ann_vol", "max_drawdown", "excess_total_return", "avg_margin_pct", "max_margin_pct"]:
        perf[col] = perf[col].map(pct)
    perf["sharpe"] = perf["sharpe"].map(lambda x: num(x, 2))
    perf["calmar"] = perf["calmar"].map(lambda x: num(x, 2))
    perf = perf.rename(columns={
        "total_return": "总收益",
        "cagr": "年化",
        "ann_vol": "年化波动",
        "max_drawdown": "最大回撤",
        "sharpe": "Sharpe",
        "calmar": "Calmar",
        "excess_total_return": "相对P0超额",
        "avg_margin_pct": "平均保证金",
        "max_margin_pct": "峰值保证金",
    })

    formula = metrics[[
        "total_open_gross_premium",
        "closed_premium_retained_ratio",
        "stop_pnl",
        "expiry_pnl",
        "total_fee",
        "cum_theta_pnl",
        "cum_vega_pnl",
        "cum_gamma_pnl",
        "cum_delta_pnl",
        "cum_residual_pnl",
    ]].copy()
    for col in ["total_open_gross_premium", "stop_pnl", "expiry_pnl", "total_fee", "cum_theta_pnl", "cum_vega_pnl", "cum_gamma_pnl", "cum_delta_pnl", "cum_residual_pnl"]:
        formula[col] = formula[col].map(money_wan)
    formula["closed_premium_retained_ratio"] = formula["closed_premium_retained_ratio"].map(pct)
    formula = formula.rename(columns={
        "total_open_gross_premium": "开仓权利金池",
        "closed_premium_retained_ratio": "权利金留存率",
        "stop_pnl": "止损损益",
        "expiry_pnl": "到期损益",
        "total_fee": "费用",
        "cum_theta_pnl": "Theta",
        "cum_vega_pnl": "Vega",
        "cum_gamma_pnl": "Gamma",
        "cum_delta_pnl": "Delta",
        "cum_residual_pnl": "Residual",
    })

    yearly_fmt = yearly.copy()
    for col in yearly_fmt.columns:
        yearly_fmt[col] = yearly_fmt[col].map(pct)

    top_p3b = product_data["P3B 乐得期限偏好"].head(8)
    top_p3b_text = "、".join(f"{idx}({row['premium_share']:.1%})" for idx, row in top_p3b.iterrows())

    md = f"""# S1 产品池与期限偏好实验分析报告

报告日期：2026-05-04  
样本区间：{p0['start'].date()} 至 {p0['end'].date()}  
基准：P0 = B2C + 1.5X 止损，全品种扫描  
候选：P1 排除不适合品种、P2 流动性 Top20、P3 乐得主流名单、P3B 乐得名单 + 期限偏好

## 1. 期权专家审议摘要

本轮实验最重要的结论是：**P3B 是目前最接近目标画像的一条线**。P3B 全周期总收益 {pct(p3b['total_return'])}、年化 {pct(p3b['cagr'])}、最大回撤 {pct(p3b['max_drawdown'])}、Sharpe {num(p3b['sharpe'], 2)}、Calmar {num(p3b['calmar'], 2)}，相对 P0 基准多赚 {pct(p3b['excess_total_return'])}。它已经接近“年化 6%、最大回撤约 2%”的目标边界。

但这不是一个“纯粹更安全”的升级。按我们的 S1 收益公式：

`S1 net return = Premium Pool × Deployment Ratio × Retention Rate - Tail / Stop Loss - Cost / Slippage`

P3B 的改善主要来自 **Premium Pool 显著扩大**：开仓毛权利金从 P0 的 {money_wan(p0['total_open_gross_premium'])} 提高到 P3B 的 {money_wan(p3b['total_open_gross_premium'])}。与此同时，P3B 的权利金留存率只有 {pct(p3b['closed_premium_retained_ratio'])}，低于 P0 的 {pct(p0['closed_premium_retained_ratio'])}。也就是说，P3B 不是因为每一单位权利金更容易留下来，而是因为它承保了更厚的主流品种和期限权利金池。

从 Greek 角度看，P3B 确实更像一个“更激进、更接近乐得画像”的卖方组合：Theta 从 P0 的 {money_wan(p0['cum_theta_pnl'])} 增至 {money_wan(p3b['cum_theta_pnl'])}，但 Vega 亏损也从 {money_wan(p0['cum_vega_pnl'])} 扩大到 {money_wan(p3b['cum_vega_pnl'])}，Gamma 亏损从 {money_wan(p0['cum_gamma_pnl'])} 扩大到 {money_wan(p3b['cum_gamma_pnl'])}。净值变好，但卖波质量并没有自动变好；它更像是“收更多保险费，同时承担更尖的短 Gamma 风险”。

我的策略判断：P1 可以暂时淘汰；P2 可作为稳健可交易参考线；P3 有研究价值但单独优势不够；**P3B 应作为下一轮主线候选**，下一步必须做 `P3B × 止损倍数`、到期集中度、尾部相关性和品种/板块容量控制，而不是立刻扩大仓位。

## 2. 实验定义与控制变量

本轮实验只改变品种池和期限偏好，核心策略框架保持 S1 纯卖权：

- P0 基准：B2C + 1.5X 止损，全品种扫描。
- P1 排除不适合：剔除前期品种适配评分中明显不适合的品种。
- P2 流动性 Top20：按流动性/持仓量优先，仅交易前 20 个。
- P3 乐得主流：采用乐得口径的主流商品期权名单。
- P3B 期限偏好：在 P3 品种池基础上加入期限偏好，黄金限定 2/4/6/8/10 双月合约，白银/铁矿近月，螺纹/豆粕/菜粕/豆二/棕榈油等主力月，同时仍要求满足 DTE 边界。

这轮不是参数拟合，而是在验证一个结构假设：**卖权收益的第一层不是因子排序，而是可交易权利金池是否足够厚、是否集中在管理人真正会交易的主流品种和期限上。**

## 3. 核心绩效对比

{perf.to_markdown()}

P3B 的最终收益和 Calmar 都显著优于 P0；P3 的收益也高于 P0，但回撤更深、Sharpe 更低，说明仅使用乐得主流名单还不够，期限偏好是这轮增益的关键。P2 的最大回撤最低，但收益提升有限，更像是交易可行性过滤，而不是收益增强器。P1 排除“不适合品种”后反而略弱，说明前期品种适配评分不能简单作为硬过滤。

## 4. 年度稳定性

{yearly_fmt.to_markdown()}

年度结果显示，P3B 的优势不是只来自某一年。2022、2023、2025 和 2026 年初都优于 P0，2024 年也没有明显恶化。不过 2024 年所有版本收益都偏低，说明这一年可能是权利金池偏薄、留存率不高或波动路径不友好的年份，后续要单独拆月份和品种。

## 5. 权利金公式拆解

{formula.to_markdown()}

### 5.1 Premium Pool

P3B 的开仓权利金池是 {money_wan(p3b['total_open_gross_premium'])}，比 P0 多 {money_wan(p3b['total_open_gross_premium'] - p0['total_open_gross_premium'])}。这说明乐得品种池 + 期限偏好确实捕捉到了更厚的可交易权利金来源。P3 也提升到 {money_wan(p3['total_open_gross_premium'])}，但仍明显低于 P3B。

### 5.2 Retention Rate

P3B 留存率 {pct(p3b['closed_premium_retained_ratio'])}，低于 P0 的 {pct(p0['closed_premium_retained_ratio'])}。这点非常重要：P3B 的提升不是因为“每张保单更优质”，而是因为“保费池变大”。如果未来我们要把年化从 5.6% 推到 6% 以上，不能只继续扩大 Premium Pool，还必须提高 Retention Rate 或减少 Tail / Stop Loss。

### 5.3 Tail / Stop Loss

P3B 止损损益 {money_wan(p3b['stop_pnl'])}，比 P0 多亏 {money_wan(p3b['stop_pnl'] - p0['stop_pnl'])}；Gamma 亏损也从 P0 的 {money_wan(p0['cum_gamma_pnl'])} 扩大到 {money_wan(p3b['cum_gamma_pnl'])}。这说明期限偏好带来的收益提升伴随着更强的短 Gamma 风险。P3B 的下一步不能只是加仓，必须增加到期集中、品种相关性和尾部簇集控制。

## 6. 品种结构与乐得画像

P3B 的 Top 权利金品种为：{top_p3b_text}。这比 P0 更接近主流管理人会交易的商品池：TA、I、CU、SA、MA 等贡献更高，ETF/股指权重仍然不足。P3/P3B 的共同特点是活跃品种更少，但单品种权利金更厚、期限更集中，因此看起来更像“管理人真实会做的组合”，不像全品种扫描那样机械分散。

风险在于：这种分散不是数学意义上的尾部分散。TA、MA、SA、I、CU 等在宏观商品行情中可能出现板块联动，P3B 的 Top5 权利金集中度达到 {pct(p3b['top5_premium_share'])}，显著高于 P0 的 {pct(p0['top5_premium_share'])}。下一轮需要用尾部相关性和板块预算约束，而不是只看品种数量。

## 7. 图表深读

### 图 1：NAV 与相对基准超额

![图1 NAV与超额](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_01_nav_relative_to_b0.png)

怎么看：上图看绝对净值，下图看相对 P0 的超额路径。重点不是最后谁最高，而是超额是否持续、是否只靠某一段行情冲出来。

图上读数：P3B 最终 NAV 为 {money_wan(p3b['final_nav'])}，累计收益 {pct(p3b['total_return'])}，相对 P0 超额 {pct(p3b['excess_total_return'])}。P3 也有 {pct(p3['excess_total_return'])} 超额，但弱于 P3B。P1 全程偏弱，P2 后期小幅优于基准。

期权专家判断：P3B 的超额更像“期限/主力合约权利金池增强”带来的，而不是单纯偶然方向收益；但中间阶段超额有回吐，说明短 Gamma 和止损损耗仍会侵蚀收益。P3 只有品种池没有期限偏好，表现不如 P3B，支持“品种池 + 期限结构”必须一起看。

风险或口径疑点：NAV 图无法解释超额来自权利金、方向还是 Greek 模型 residual，因此必须结合后面的 Greek 和平仓路径。P3B 的高收益不应直接等同于更优质卖波。

下一步验证：以 P3B 为主线，做 1.5X/2.0X/2.5X/3.0X/不止损阶梯；同时拆 2024 年低收益期，判断是否需要动态降低部署率。

### 图 2：回撤曲线

![图2 回撤](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_02_drawdown_relative_to_b0.png)

怎么看：回撤图回答“收益提升是否用更厚左尾换来”。卖权策略的核心约束不是平均收益，而是某些波动切换期能不能活下来。

图上读数：P0 最大回撤 {pct(p0['max_drawdown'])}，P3B 为 {pct(p3b['max_drawdown'])}，P3 为 {pct(p3['max_drawdown'])}，P2 最低为 {pct(p2['max_drawdown'])}。

期权专家判断：P3B 虽然收益最高，但回撤并未比 P0 更低，只是仍处在可接受区间。P3 的回撤 {pct(p3['max_drawdown'])} 已经超过 2.5%，说明“乐得主流名单”如果没有期限控制和其他规则，风险调整后并不如 P3B。

风险或口径疑点：回撤是按日度 NAV 统计，分钟止损内的盘中极值未必完全反映。对于卖方策略，后续需要补盘中最大浮亏和 margin shock。

下一步验证：增加尾部日产品/方向归因，检查 P3B 的回撤是否集中在少数到期、少数板块或止损簇集日。

### 图 3：保证金与持仓结构

![图3 保证金与持仓](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_03_margin_position_relative_to_b0.png)

怎么看：这张图判断收益提升是不是简单来自更高保证金使用。若收益提升只是仓位更大，不算策略质量改善。

图上读数：五条线平均保证金都在 46%-48% 附近，P3B 平均保证金 {pct(p3b['avg_margin_pct'])}，低于 P0 的 {pct(p0['avg_margin_pct'])}；P3B 峰值保证金 {pct(p3b['max_margin_pct'])}，也与基准接近。

期权专家判断：P3B 的收益提升不是因为保证金更高，而是同样保证金下开到了更厚的权利金池。这一点是本轮最有价值的发现。它说明“品种和期限选择”比盲目提高保证金更重要。

风险或口径疑点：保证金口径是盯市保证金，不完全等于交易所/期货公司真实结算压力；同时缺少组合 margin shock 指标。

下一步验证：在 P3B 上加入尾部相关性和到期集中度预算，确认不降低平均保证金的情况下能否压低回撤。

### 图 4：Greek 归因

![图4 Greek归因](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_04_greek_attribution_relative_to_b0.png)

怎么看：卖权策略理想状态是 Theta 为正，Vega 尽量不拖累，Gamma 可控。若收益提升伴随 Vega/Gamma 大幅恶化，就只能算更激进承保，而不是更高质量卖波。

图上读数：P3B Theta {money_wan(p3b['cum_theta_pnl'])}，Vega {money_wan(p3b['cum_vega_pnl'])}，Gamma {money_wan(p3b['cum_gamma_pnl'])}，Delta {money_wan(p3b['cum_delta_pnl'])}。相比 P0，P3B Theta 多 {money_wan(p3b['cum_theta_pnl'] - p0['cum_theta_pnl'])}，但 Gamma 多亏 {money_wan(p3b['cum_gamma_pnl'] - p0['cum_gamma_pnl'])}，Vega 多亏 {money_wan(p3b['cum_vega_pnl'] - p0['cum_vega_pnl'])}。

期权专家判断：P3B 是“theta 厚度胜出”，但 vega 目标仍未达成。我们的长期目标之一是 vega 收益为正或至少不显著为负，这一版还没有做到。若只看 NAV，会误以为 P3B 已经接近完成；但从卖波质量看，下一步仍要控制 vega/gamma。

风险或口径疑点：Greek 归因依赖日度重估和模型口径，近到期、深虚合约和商品美式期权的非线性可能进 residual。报告不能把 residual 当 alpha。

下一步验证：对 P3B 做 forward vega、IV shock coverage、gamma/theta 分桶；保留权利金池，同时惩罚最差的短 Gamma 合约。

### 图 5：日收益左尾

![图5 左尾分布](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_05_daily_pnl_tail_relative_to_b0.png)

怎么看：左尾分布用于检查“赚得多是不是亏得也更尖”。卖权策略不能只看胜率和累计收益。

图上读数：P3/P3B 的收益分布更宽，P2 更接近稳健型。P3B 年化波动 {pct(p3b['ann_vol'])}，高于 P0 的 {pct(p0['ann_vol'])}，但低于 P3 的 {pct(p3['ann_vol'])}。

期权专家判断：P3B 提高收益后，左尾没有像 P3 那样明显恶化到不可接受，是它优于 P3 的关键。P2 的左尾较稳，但 Premium Pool 不够厚，难以支撑年化目标。

风险或口径疑点：日收益分布看不到盘中止损前最大浮亏，且极端行情样本仍有限。2022-2026 的样本不能保证覆盖所有商品尾部共振。

下一步验证：把最差 20 日拆成产品/方向/到期，检查是否由 TA/I/CU/SA/MA 等主品种共同驱动。

### 图 6：P/C 结构

![图6 PC结构](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_06_pc_structure_relative_to_b0.png)

怎么看：P/C 结构判断策略是否实际变成方向仓。长期偏 Put 是隐含看涨商品，长期偏 Call 是隐含看跌商品。

图上读数：P3B 开仓 P/C 手数比 {num(p3b['open_put_call_lot_ratio'], 2)}，与 P0 的 {num(p0['open_put_call_lot_ratio'], 2)} 接近；P3 为 {num(p3['open_put_call_lot_ratio'], 2)}。

期权专家判断：P3B 的优势不是来自明显单边 Put 偏置，而是来自期限和品种权利金池。这个很重要，因为它降低了“只是押对商品方向”的嫌疑。

风险或口径疑点：手数 P/C 不等于风险 P/C，仍需看 cash delta、side premium、side gamma 和 side stop loss。商品期权不同乘数和标的价格差异较大，手数不完全可比。

下一步验证：按 P/C 侧分别做 retained ratio、stop loss 和 vega/gamma 归因，判断哪一侧拖累留存。

### 图 7：品种暴露

![图7 品种暴露](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_07_product_exposure_relative_to_b0.png)

怎么看：这张图回答“全品种是否真的分散，以及 P3B 是否集中在少数品种”。

图上读数：P3B Top5 权利金集中度 {pct(p3b['top5_premium_share'])}，P0 为 {pct(p0['top5_premium_share'])}。P3B Top 品种为 {top_p3b_text}。

期权专家判断：P3B 的集中度更高，但这不是天然坏事。卖权不是越分散越好，而是要在“权利金厚、可成交、风险不共振”的品种上承担预算。问题在于这些品种是否尾部相关，尤其化工、黑色、有色在宏观冲击中可能同向。

风险或口径疑点：图里是开仓权利金占比，不是 stress loss 占比。下一步若用 Tail-HRP，必须改用尾部相关和 stress contribution。

下一步验证：用期货历史数据做尾部相关性、相关性突变和 stop cluster 潜在暴露，按板块/相关组给预算。

### 图 8：止损簇集

![图8 止损簇集](../output/analysis_s1_product_pool_p_experiments_2022_latest/compare_08_stop_cluster_relative_to_b0.png)

怎么看：止损图判断亏损是否集中发生。卖权最大的风险不是单笔止损，而是同一段行情里多个品种、多个方向同时止损。

图上读数：P3B 止损单数 {int(p3b['stop_orders'])}，止损损益 {money_wan(p3b['stop_pnl'])}；P0 止损单数 {int(p0['stop_orders'])}，止损损益 {money_wan(p0['stop_pnl'])}。P3B 止损亏损更大，但到期收益也更高：{money_wan(p3b['expiry_pnl'])}。

期权专家判断：P3B 是典型“多收、多亏、净额更好”的卖权版本。若下一步把止损从 1.5X 放到 2.5X，可能减少被噪音洗出去，但也可能把止损簇集推成更深的净值回撤。

风险或口径疑点：止损执行已经包含确认和滑点，但真实市场在极端时的退出容量仍可能更差。止损簇集还需要看同日品种相关性和盘口成交。

下一步验证：P3B 上做止损阶梯；同时统计止损后最终归零比例和止损后继续不利移动比例，判断 1.5X 是保护还是过早退出。

## 8. 与乐得画像的差距

本轮 P3B 比 P0 更接近乐得画像：主流商品品种、主力/近月/双月期限偏好、较厚权利金池、保证金接近 50%。但仍有三点差距：

- Vega 仍为负，说明我们还没有真正做到“降波时多赚、升波时少亏”。
- Gamma 损耗很大，期限偏好提升了收益，也提升了短 Gamma 尖度。
- 组合层还没有严格做板块、尾部相关、到期集中和 stop cluster 控制。

因此，P3B 可以作为下一版主线，但不能直接视为可模拟盘版本。它更像是一个有希望的承保池定义。

## 9. 结论与下一步

1. P3B 作为下一轮主线候选，优先级最高。
2. P1 简单排除“不适合品种”不成立，不能作为硬过滤。
3. P2 有稳定价值，但更适合作为流动性/容量约束，不是收益增强主线。
4. P3 说明乐得主流品种池有效，但期限偏好才是核心增益来源。
5. 下一轮先做 P3B 止损阶梯：1.5X、2.0X、2.5X、3.0X、不止损。
6. 同步做 P3B 的尾部相关性、到期集中度和板块预算约束。
7. 针对 Vega 为负的问题，继续引入 forward vega、IV shock coverage、vol-of-vol、vomma 和 side-level P/C 留存分析。

最终判断：**P3B 不是终版策略，但它把我们从“全品种机械卖权”推进到了“主流承保池 + 期限偏好 + 权利金池增强”的正确方向。**
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")


def build(args: argparse.Namespace) -> None:
    setup_matplotlib()
    output_dir = args.repo_root / "output"
    analysis_dir = args.output_dir or output_dir / f"analysis_{REPORT_TAG}"
    docs_dir = args.repo_root / "docs"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    nav_data = {name: read_nav(output_dir, tag) for name, tag in SERIES.items()}
    orders_data = {name: read_orders(output_dir, tag) for name, tag in SERIES.items()}
    product_data = {name: product_summary(orders) for name, orders in orders_data.items()}

    metrics_rows = []
    for name in SERIES:
        row = {}
        row.update(nav_metrics(nav_data[name]))
        row.update(order_metrics(orders_data[name]))
        row["name"] = name
        metrics_rows.append(row)
    metrics = pd.DataFrame(metrics_rows).set_index("name")
    baseline = metrics.loc[BASELINE_NAME]
    metrics["excess_total_return"] = metrics["total_return"] - baseline["total_return"]
    metrics["excess_cagr"] = metrics["cagr"] - baseline["cagr"]
    metrics["vega_loss_to_gross_premium"] = (-metrics["cum_vega_pnl"]) / metrics["total_open_gross_premium"]
    metrics["gamma_loss_to_gross_premium"] = (-metrics["cum_gamma_pnl"]) / metrics["total_open_gross_premium"]
    metrics["s1_pnl_to_gross_premium"] = (metrics["final_nav"] - 10_000_000.0) / metrics["total_open_gross_premium"]
    metrics["top5_premium_share"] = pd.Series({
        name: product_data[name]["premium_share"].head(5).sum()
        for name in SERIES
    })

    yearly = yearly_returns(nav_data)
    save_table(metrics, analysis_dir / "summary_metrics_all_series.csv")
    # One-row summary for existing DOCX builder context. Use P3B as candidate because it is the selected line.
    metrics.loc[["P3B 乐得期限偏好"]].rename(index={"P3B 乐得期限偏好": REPORT_TAG}).to_csv(
        analysis_dir / "summary_metrics.csv",
        encoding="utf-8-sig",
    )
    save_table(yearly, analysis_dir / "yearly_returns.csv")
    save_table(align_excess(nav_data), analysis_dir / "excess_nav_series.csv")
    for name, df in product_data.items():
        safe_name = SERIES[name]
        save_table(df, analysis_dir / f"product_summary_{safe_name}.csv")
    close_summary = pd.concat(
        {name: close_product_summary(orders) for name, orders in orders_data.items()},
        names=["series", "row"],
    )
    save_table(close_summary, analysis_dir / "close_product_side_action_summary.csv")

    plot_nav(nav_data, analysis_dir)
    plot_drawdown(nav_data, analysis_dir)
    plot_margin_position(nav_data, analysis_dir)
    plot_greek(metrics, analysis_dir)
    plot_daily_tail(nav_data, analysis_dir)
    plot_pc(nav_data, metrics, analysis_dir)
    plot_product_exposure(product_data, analysis_dir)
    plot_stop_cluster(orders_data, analysis_dir)

    report_path = docs_dir / "s1_product_pool_p_experiments_report_20260504.md"
    write_report(report_path, analysis_dir, metrics, yearly, product_data)
    print(f"analysis_dir={analysis_dir}")
    print(f"report={report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build S1 product-pool comparison analysis package and report.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
