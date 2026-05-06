#!/usr/bin/env python3
"""Cross-sectional layer diagnostics for S1 short-premium factors.

This script reads completed order logs and treats open-time B2/B3 fields as
cross-sectional factors. It then checks whether high/low factor layers have
better realized premium retention, net PnL, and stop behavior.

The first version intentionally uses only executed trades. It is a diagnostic
for "trades the strategy actually made", not a full candidate-universe test.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


CLOSE_ACTION_PREFIXES = ("sl_", "tp_", "expiry", "pre_expiry_roll", "greeks_", "s4_")


@dataclass(frozen=True)
class FactorSpec:
    name: str
    direction: str = "high"
    label: Optional[str] = None

    @property
    def display(self) -> str:
        return self.label or self.name


DEFAULT_FACTORS: Tuple[FactorSpec, ...] = (
    FactorSpec("premium_quality_score", "high"),
    FactorSpec("iv_rv_carry_score", "high"),
    FactorSpec("breakeven_cushion_score", "high"),
    FactorSpec("premium_to_iv_shock_score", "high"),
    FactorSpec("premium_to_stress_loss_score", "high"),
    FactorSpec("theta_vega_efficiency_score", "high"),
    FactorSpec("cost_liquidity_score", "high"),
    FactorSpec("variance_carry", "high"),
    FactorSpec("premium_to_iv10_loss", "high"),
    FactorSpec("premium_to_stress_loss", "high"),
    FactorSpec("theta_vega_efficiency", "high"),
    FactorSpec("gamma_rent_penalty", "low"),
    FactorSpec("friction_ratio", "low"),
    FactorSpec("b2_product_score", "high"),
    FactorSpec("b3_clean_vega_score", "high"),
    FactorSpec("b3_forward_variance_score", "high"),
    FactorSpec("b3_vol_of_vol_score", "high"),
    FactorSpec("b3_iv_shock_score", "high"),
    FactorSpec("b3_joint_stress_score", "high"),
    FactorSpec("b3_vomma_score", "high"),
    FactorSpec("b3_skew_stability_score", "high"),
    FactorSpec("b3_vol_of_vol_proxy", "high", "b3_vol_of_vol_proxy_high"),
    FactorSpec("b3_vol_of_vol_proxy", "low", "b3_vol_of_vol_proxy_low"),
    FactorSpec("b3_vov_trend", "low"),
    FactorSpec("b3_iv_shock_coverage", "high"),
    FactorSpec("b3_joint_stress_coverage", "high"),
    FactorSpec("b3_vomma_loss_ratio", "low"),
    FactorSpec("b3_skew_steepening", "low"),
)


OUTCOME_SUM_COLS = [
    "net_pnl_after_fee",
    "pnl",
    "fee",
    "open_premium_cash",
    "close_value_cash",
    "premium_retained_cash",
    "quantity",
    "stop_count",
    "expiry_count",
    "stop_loss_cash",
]

ENV_CANDIDATE_COLS = [
    "option_type",
    "vol_regime",
    "product",
    "bucket",
    "corr_group",
    "trend_state",
    "product_vol_regime",
]

LABEL_COLS = [
    "net_pnl_per_premium",
    "retained_ratio",
    "stop_avoidance",
    "stop_loss_avoidance",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", action="append", help="Backtest tag. Can be repeated.")
    parser.add_argument("--orders", action="append", help="Explicit orders CSV. Can be repeated.")
    parser.add_argument("--output-dir", default="output", help="Directory containing orders CSV files.")
    parser.add_argument("--out-dir", help="Output directory. Defaults to output/factor_layers_<tag>.")
    parser.add_argument("--level", choices=["contract", "product_side"], default="product_side")
    parser.add_argument("--bins", type=int, default=5, help="Number of daily quantile layers.")
    parser.add_argument("--min-cross-section", type=int, default=8)
    parser.add_argument("--min-days", type=int, default=20)
    parser.add_argument("--factor", action="append", help="Custom factor name. Can be repeated.")
    parser.add_argument("--top-n-plot", type=int, default=10)
    parser.add_argument(
        "--env-col",
        action="append",
        help="Environment column for sliced diagnostics. Defaults to option_type and vol_regime when available.",
    )
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


def infer_tag_from_orders(path: Path) -> str:
    stem = path.stem
    return stem[len("orders_") :] if stem.startswith("orders_") else stem


def resolve_order_paths(args: argparse.Namespace) -> List[Path]:
    output_dir = Path(args.output_dir)
    paths: List[Path] = []
    for tag in args.tag or []:
        path = output_dir / f"orders_{tag}.csv"
        if not path.exists():
            raise FileNotFoundError(f"orders file not found for tag={tag}: {path}")
        paths.append(path)
    for raw in args.orders or []:
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"orders file not found: {path}")
        paths.append(path)
    if not paths:
        raise SystemExit("Provide at least one --tag or --orders.")
    return paths


def is_close_action(series: pd.Series) -> pd.Series:
    action = series.astype(str)
    mask = action.ne("open_sell")
    prefix_mask = pd.Series(False, index=series.index)
    for prefix in CLOSE_ACTION_PREFIXES:
        prefix_mask |= action.str.startswith(prefix, na=False)
    return mask & prefix_mask


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    valid = values.notna() & weights.notna() & (weights.abs() > 0)
    if valid.any():
        total_weight = float(weights[valid].abs().sum())
        if total_weight > 0:
            return float((values[valid] * weights[valid].abs()).sum() / total_weight)
    return float(values.mean()) if values.notna().any() else np.nan


def mode_value(series: pd.Series):
    valid = series.dropna()
    if valid.empty:
        return np.nan
    mode = valid.mode(dropna=True)
    if mode.empty:
        return valid.iloc[0]
    return mode.iloc[0]


def prepare_trade_rows(orders: pd.DataFrame) -> pd.DataFrame:
    if "action" not in orders.columns:
        raise ValueError("orders CSV must contain an action column")
    df = orders.loc[is_close_action(orders["action"])].copy()
    if df.empty:
        return df

    if "signal_date" not in df.columns:
        raise ValueError("orders CSV must contain signal_date for layer tests")
    df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
    df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
    df = df.dropna(subset=["signal_date"])

    for col in [
        "pnl",
        "fee",
        "open_premium_cash",
        "close_value_cash",
        "premium_retained_cash",
        "premium_retained_pct",
        "quantity",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan

    df["fee"] = df["fee"].fillna(0.0)
    df["pnl"] = df["pnl"].fillna(0.0)
    df["net_pnl_after_fee"] = df["pnl"] - df["fee"]
    df["open_premium_cash"] = df["open_premium_cash"].replace([np.inf, -np.inf], np.nan)
    df["premium_retained_cash"] = df["premium_retained_cash"].fillna(df["pnl"])
    df["stop_count"] = df["action"].astype(str).str.startswith("sl_", na=False).astype(float)
    df["expiry_count"] = df["action"].astype(str).eq("expiry").astype(float)
    df["stop_loss_cash"] = np.where(df["stop_count"] > 0, df["net_pnl_after_fee"], 0.0)
    add_label_columns(df)
    return df


def add_label_columns(df: pd.DataFrame) -> pd.DataFrame:
    premium = pd.to_numeric(df.get("open_premium_cash"), errors="coerce").replace(0.0, np.nan)
    if "trade_count" in df.columns:
        trade_count_raw = df["trade_count"]
    else:
        trade_count_raw = pd.Series(1.0, index=df.index)
    trade_count = pd.to_numeric(trade_count_raw, errors="coerce").replace(0.0, np.nan)
    df["net_pnl_per_premium"] = pd.to_numeric(df.get("net_pnl_after_fee"), errors="coerce") / premium
    df["retained_ratio"] = pd.to_numeric(df.get("premium_retained_cash"), errors="coerce") / premium
    stop_rate = pd.to_numeric(df.get("stop_count"), errors="coerce") / trade_count
    df["stop_rate"] = stop_rate
    df["stop_avoidance"] = -stop_rate
    df["stop_loss_per_premium"] = pd.to_numeric(df.get("stop_loss_cash"), errors="coerce") / premium
    # This label is higher-is-better: 0 is better than a negative stop loss.
    df["stop_loss_avoidance"] = df["stop_loss_per_premium"]
    return df


def available_factors(df: pd.DataFrame, custom_factors: Optional[Sequence[str]]) -> List[FactorSpec]:
    if custom_factors:
        specs = [FactorSpec(name, "high") for name in custom_factors]
    else:
        specs = list(DEFAULT_FACTORS)

    available: List[FactorSpec] = []
    seen = set()
    for spec in specs:
        key = (spec.name, spec.direction, spec.display)
        if key in seen:
            continue
        seen.add(key)
        if spec.name in df.columns:
            numeric = pd.to_numeric(df[spec.name], errors="coerce")
            if numeric.notna().sum() > 0:
                available.append(spec)
    return available


def aggregate_product_side(df: pd.DataFrame, factor_names: Iterable[str]) -> pd.DataFrame:
    keys = ["signal_date", "product", "option_type"]
    missing = [col for col in keys if col not in df.columns]
    if missing:
        raise ValueError(f"orders CSV missing columns for product_side level: {missing}")

    work = df.copy()
    for factor in factor_names:
        if factor in work.columns:
            work[factor] = pd.to_numeric(work[factor], errors="coerce")

    rows: List[Dict[str, float]] = []
    for key_values, group in work.groupby(keys, dropna=False, sort=False):
        row: Dict[str, float] = {
            "signal_date": key_values[0],
            "product": key_values[1],
            "option_type": key_values[2],
            "trade_count": float(len(group)),
            "code_count": float(group["code"].nunique()) if "code" in group.columns else float(len(group)),
        }
        weights = pd.to_numeric(group["open_premium_cash"], errors="coerce").abs()
        for col in OUTCOME_SUM_COLS:
            if col in group.columns:
                row[col] = float(pd.to_numeric(group[col], errors="coerce").fillna(0.0).sum())
            else:
                row[col] = 0.0
        for col in ENV_CANDIDATE_COLS:
            if col in group.columns and col not in keys:
                row[col] = mode_value(group[col])
        for factor in factor_names:
            if factor in group.columns:
                row[factor] = weighted_mean(pd.to_numeric(group[factor], errors="coerce"), weights)
        rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        add_label_columns(result)
    return result


def prepare_layer_sample(df: pd.DataFrame, level: str, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    factor_names = sorted({spec.name for spec in factors})
    if level == "contract":
        sample = df.copy()
        sample["trade_count"] = 1.0
        sample["code_count"] = 1.0
    elif level == "product_side":
        sample = aggregate_product_side(df, factor_names)
    else:
        raise ValueError(f"unknown level: {level}")

    for col in OUTCOME_SUM_COLS + factor_names:
        if col in sample.columns:
            sample[col] = pd.to_numeric(sample[col], errors="coerce")
    sample = sample.replace([np.inf, -np.inf], np.nan)
    sample["open_premium_cash"] = sample["open_premium_cash"].where(
        sample["open_premium_cash"].abs() > 1e-12, np.nan
    )
    add_label_columns(sample)
    return sample


def assign_daily_layers(
    sample: pd.DataFrame,
    factor: str,
    bins: int,
    min_cross_section: int,
) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []
    use_cols = [
        "signal_date",
        "product",
        "option_type",
        "trade_count",
        "code_count",
        "net_pnl_after_fee",
        "pnl",
        "fee",
        "open_premium_cash",
        "premium_retained_cash",
        "stop_count",
        "expiry_count",
        "stop_loss_cash",
        "net_pnl_per_premium",
        "retained_ratio",
        "stop_rate",
        "stop_avoidance",
        "stop_loss_per_premium",
        "stop_loss_avoidance",
        factor,
    ]
    for col in ENV_CANDIDATE_COLS:
        if col in sample.columns and col not in use_cols:
            use_cols.append(col)
    use_cols = [c for c in use_cols if c in sample.columns]
    work = sample[use_cols].dropna(subset=["signal_date", factor]).copy()

    for _, group in work.groupby("signal_date", sort=True):
        values = pd.to_numeric(group[factor], errors="coerce")
        valid = group.loc[values.notna()].copy()
        if len(valid) < min_cross_section or values.nunique(dropna=True) < 2:
            continue
        n_bins = min(bins, values.nunique(dropna=True), len(valid))
        if n_bins < 2:
            continue
        try:
            valid["layer_num"] = pd.qcut(
                pd.to_numeric(valid[factor], errors="coerce"),
                q=n_bins,
                labels=False,
                duplicates="drop",
            )
        except ValueError:
            continue
        valid = valid.dropna(subset=["layer_num"])
        if valid.empty:
            continue
        valid["layer_num"] = valid["layer_num"].astype(int) + 1
        valid["n_layers"] = int(valid["layer_num"].max())
        valid["layer"] = "Q" + valid["layer_num"].astype(str)
        rows.append(valid)

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def daily_layer_table(
    layered: pd.DataFrame,
    factor_spec: FactorSpec,
    extra_group_cols: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    extra_group_cols = list(extra_group_cols or [])
    group_cols = ["signal_date", "layer_num", "layer", "n_layers"] + extra_group_cols
    agg = {
        "net_pnl_after_fee": "sum",
        "pnl": "sum",
        "fee": "sum",
        "open_premium_cash": "sum",
        "premium_retained_cash": "sum",
        "stop_count": "sum",
        "expiry_count": "sum",
        "stop_loss_cash": "sum",
        "trade_count": "sum",
        "code_count": "sum",
        factor_spec.name: "mean",
    }
    table = layered.groupby(group_cols, dropna=False).agg(agg).reset_index()
    table = table.rename(columns={factor_spec.name: "factor_mean"})
    table["factor"] = factor_spec.display
    table["factor_raw"] = factor_spec.name
    table["direction"] = factor_spec.direction
    table["net_pnl_per_premium"] = table["net_pnl_after_fee"] / table["open_premium_cash"].replace(0, np.nan)
    table["retained_ratio"] = table["premium_retained_cash"] / table["open_premium_cash"].replace(0, np.nan)
    table["stop_rate"] = table["stop_count"] / table["trade_count"].replace(0, np.nan)
    table["expiry_rate"] = table["expiry_count"] / table["trade_count"].replace(0, np.nan)
    table["stop_loss_per_premium"] = table["stop_loss_cash"] / table["open_premium_cash"].replace(0, np.nan)
    return table


def summarize_layers(
    daily: pd.DataFrame,
    min_days: int,
    extra_group_cols: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    rows = []
    extra_group_cols = list(extra_group_cols or [])
    group_keys = ["factor", "layer_num", "layer", "direction"] + extra_group_cols
    for key_values, group in daily.groupby(group_keys, sort=True, dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        key_map = dict(zip(group_keys, key_values))
        ret = group["net_pnl_per_premium"].dropna()
        row = {
            "factor": key_map["factor"],
            "direction": key_map["direction"],
            "layer_num": key_map["layer_num"],
            "layer": key_map["layer"],
            "days": int(group["signal_date"].nunique()),
            "trade_count": float(group["trade_count"].sum()),
            "code_count": float(group["code_count"].sum()),
            "open_premium_cash": float(group["open_premium_cash"].sum()),
            "net_pnl_after_fee": float(group["net_pnl_after_fee"].sum()),
            "premium_retained_cash": float(group["premium_retained_cash"].sum()),
            "stop_count": float(group["stop_count"].sum()),
            "expiry_count": float(group["expiry_count"].sum()),
            "pooled_net_pnl_per_premium": float(group["net_pnl_after_fee"].sum() / group["open_premium_cash"].sum())
            if abs(group["open_premium_cash"].sum()) > 1e-12
            else np.nan,
            "pooled_retained_ratio": float(
                group["premium_retained_cash"].sum() / group["open_premium_cash"].sum()
            )
            if abs(group["open_premium_cash"].sum()) > 1e-12
            else np.nan,
            "pooled_stop_rate": float(group["stop_count"].sum() / group["trade_count"].sum())
            if abs(group["trade_count"].sum()) > 1e-12
            else np.nan,
            "pooled_stop_loss_per_premium": float(
                group["stop_loss_cash"].sum() / group["open_premium_cash"].sum()
            )
            if abs(group["open_premium_cash"].sum()) > 1e-12
            else np.nan,
            "daily_mean_net_per_premium": float(ret.mean()) if len(ret) else np.nan,
            "daily_std_net_per_premium": float(ret.std(ddof=1)) if len(ret) > 1 else np.nan,
            "daily_t_stat": float(ret.mean() / ret.std(ddof=1) * math.sqrt(len(ret)))
            if len(ret) >= min_days and ret.std(ddof=1) > 0
            else np.nan,
            "worst_daily_net_per_premium": float(ret.min()) if len(ret) else np.nan,
            "best_daily_net_per_premium": float(ret.max()) if len(ret) else np.nan,
        }
        for col in extra_group_cols:
            row[col] = key_map[col]
        rows.append(row)
    return pd.DataFrame(rows)


def build_spread_table(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (factor, direction), group in daily.groupby(["factor", "direction"], sort=False):
        for date, day in group.groupby("signal_date", sort=True):
            low = day.loc[day["layer_num"] == day["layer_num"].min()]
            high = day.loc[day["layer_num"] == day["layer_num"].max()]
            if low.empty or high.empty:
                continue
            low_ret = float(low["net_pnl_per_premium"].iloc[0])
            high_ret = float(high["net_pnl_per_premium"].iloc[0])
            if direction == "low":
                good_ret, bad_ret = low_ret, high_ret
            else:
                good_ret, bad_ret = high_ret, low_ret
            rows.append(
                {
                    "signal_date": date,
                    "factor": factor,
                    "direction": direction,
                    "good_minus_bad_ret": good_ret - bad_ret,
                    "high_minus_low_ret": high_ret - low_ret,
                    "high_ret": high_ret,
                    "low_ret": low_ret,
                }
            )
    spread = pd.DataFrame(rows)
    if not spread.empty:
        spread = spread.sort_values(["factor", "signal_date"])
        spread["cum_good_minus_bad_ret"] = spread.groupby("factor")["good_minus_bad_ret"].cumsum()
        spread["cum_high_minus_low_ret"] = spread.groupby("factor")["high_minus_low_ret"].cumsum()
    return spread


def summarize_spreads(spread: pd.DataFrame, min_days: int) -> pd.DataFrame:
    rows = []
    for (factor, direction), group in spread.groupby(["factor", "direction"], sort=False):
        ret = group["good_minus_bad_ret"].dropna()
        rows.append(
            {
                "factor": factor,
                "direction": direction,
                "days": int(len(ret)),
                "mean_good_minus_bad_ret": float(ret.mean()) if len(ret) else np.nan,
                "std_good_minus_bad_ret": float(ret.std(ddof=1)) if len(ret) > 1 else np.nan,
                "t_stat": float(ret.mean() / ret.std(ddof=1) * math.sqrt(len(ret)))
                if len(ret) >= min_days and ret.std(ddof=1) > 0
                else np.nan,
                "positive_day_rate": float((ret > 0).mean()) if len(ret) else np.nan,
                "cum_good_minus_bad_ret": float(ret.sum()) if len(ret) else np.nan,
                "worst_day": float(ret.min()) if len(ret) else np.nan,
                "best_day": float(ret.max()) if len(ret) else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("cum_good_minus_bad_ret", ascending=False)


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        return np.nan
    xv = x[valid]
    yv = y[valid]
    if xv.nunique(dropna=True) < 2 or yv.nunique(dropna=True) < 2:
        return np.nan
    return float(xv.rank(method="average").corr(yv.rank(method="average")))


def build_ic_daily(
    sample: pd.DataFrame,
    factors: Sequence[FactorSpec],
    min_cross_section: int,
    env_cols: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    env_cols = list(env_cols or [])
    rows = []
    group_cols = ["signal_date"] + env_cols
    for key_values, group in sample.groupby(group_cols, sort=True, dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        key_map = dict(zip(group_cols, key_values))
        if len(group) < min_cross_section:
            continue
        for factor in factors:
            if factor.name not in group.columns:
                continue
            raw = pd.to_numeric(group[factor.name], errors="coerce")
            good_factor = -raw if factor.direction == "low" else raw
            for label in LABEL_COLS:
                if label not in group.columns:
                    continue
                label_values = pd.to_numeric(group[label], errors="coerce")
                ic = spearman_ic(good_factor, label_values)
                if not np.isfinite(ic):
                    continue
                row = {
                    "factor": factor.display,
                    "factor_raw": factor.name,
                    "direction": factor.direction,
                    "label": label,
                    "ic": ic,
                    "n": int((good_factor.notna() & label_values.notna()).sum()),
                }
                row.update(key_map)
                rows.append(row)
    return pd.DataFrame(rows)


def summarize_ic(ic_daily: pd.DataFrame, min_days: int, extra_group_cols: Optional[Sequence[str]] = None) -> pd.DataFrame:
    if ic_daily.empty:
        return pd.DataFrame()
    extra_group_cols = list(extra_group_cols or [])
    rows = []
    group_keys = ["factor", "direction", "label"] + extra_group_cols
    for key_values, group in ic_daily.groupby(group_keys, sort=False, dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        key_map = dict(zip(group_keys, key_values))
        values = group["ic"].dropna()
        row = {
            **key_map,
            "days": int(group["signal_date"].nunique()),
            "mean_ic": float(values.mean()) if len(values) else np.nan,
            "std_ic": float(values.std(ddof=1)) if len(values) > 1 else np.nan,
            "t_stat": float(values.mean() / values.std(ddof=1) * math.sqrt(len(values)))
            if len(values) >= min_days and values.std(ddof=1) > 0
            else np.nan,
            "positive_ic_rate": float((values > 0).mean()) if len(values) else np.nan,
            "cum_ic": float(values.sum()) if len(values) else np.nan,
            "mean_n": float(group["n"].mean()) if "n" in group else np.nan,
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["label", "cum_ic"], ascending=[True, False])


def pivot_metric(summary: pd.DataFrame, metric: str) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    return summary.pivot_table(index="factor", columns="layer", values=metric, aggfunc="first")


def plot_heatmap(pivot: pd.DataFrame, title: str, path: Path, fmt: str = ".2%") -> None:
    if pivot.empty:
        return
    data = pivot.sort_index()
    fig, ax = plt.subplots(figsize=(max(8, data.shape[1] * 1.2), max(4, data.shape[0] * 0.35)))
    im = ax.imshow(data.values, aspect="auto", cmap="RdYlGn")
    ax.set_xticks(range(data.shape[1]), data.columns)
    ax.set_yticks(range(data.shape[0]), data.index)
    ax.set_title(title)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data.values[i, j]
            if np.isfinite(val):
                text = format(val, fmt)
                ax.text(j, i, text, ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_ic_heatmap(ic_summary: pd.DataFrame, path: Path) -> None:
    if ic_summary.empty:
        return
    pivot = ic_summary.pivot_table(index="factor", columns="label", values="mean_ic", aggfunc="first")
    if pivot.empty:
        return
    plot_heatmap(pivot, "Mean Rank IC by factor and label", path, fmt=".3f")


def plot_ic_cumulative(ic_daily: pd.DataFrame, ic_summary: pd.DataFrame, path: Path, label: str, top_n: int) -> None:
    if ic_daily.empty or ic_summary.empty:
        return
    top = ic_summary[ic_summary["label"] == label].head(top_n)["factor"].tolist()
    if not top:
        return
    fig, ax = plt.subplots(figsize=(12, 7))
    for factor in top:
        group = ic_daily[(ic_daily["factor"] == factor) & (ic_daily["label"] == label)].sort_values("signal_date")
        if group.empty:
            continue
        ax.plot(group["signal_date"], group["ic"].cumsum(), label=factor, linewidth=1.5)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(f"Cumulative Rank IC: {label}")
    ax.set_ylabel("Cumulative IC")
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_stop_rate_by_layer(layer_summary: pd.DataFrame, spread_summary: pd.DataFrame, path: Path, top_n: int) -> None:
    if layer_summary.empty or spread_summary.empty or "pooled_stop_rate" not in layer_summary.columns:
        return
    top = spread_summary.head(top_n)["factor"].tolist()
    data = layer_summary[layer_summary["factor"].isin(top)].copy()
    if data.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 7))
    for factor, group in data.groupby("factor", sort=False):
        group = group.sort_values("layer_num")
        ax.plot(group["layer"], group["pooled_stop_rate"], marker="o", label=factor)
    ax.set_title("Stop rate by factor layer")
    ax.set_ylabel("Stop rate")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_spreads(spread: pd.DataFrame, spread_summary: pd.DataFrame, path: Path, top_n: int) -> None:
    if spread.empty or spread_summary.empty:
        return
    top = spread_summary.head(top_n)["factor"].tolist()
    fig, ax = plt.subplots(figsize=(12, 7))
    for factor in top:
        group = spread[spread["factor"] == factor].sort_values("signal_date")
        if group.empty:
            continue
        ax.plot(group["signal_date"], group["cum_good_minus_bad_ret"], label=factor, linewidth=1.6)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Cumulative good-minus-bad layer spread")
    ax.set_ylabel("Cumulative daily spread return")
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    columns = list(df.columns)
    rows = [
        "| " + " | ".join(str(col) for col in columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in df.iterrows():
        values = []
        for col in columns:
            val = row[col]
            if pd.isna(val):
                values.append("")
            else:
                values.append(str(val))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def write_report(
    out_dir: Path,
    tag: str,
    level: str,
    factors: Sequence[FactorSpec],
    layer_summary: pd.DataFrame,
    spread_summary: pd.DataFrame,
) -> None:
    lines = [
        f"# Factor Layer Diagnostics: {tag}",
        "",
        f"- Level: `{level}`",
        f"- Factors tested: {len(factors)}",
        "",
        "## Top good-minus-bad spreads",
        "",
    ]
    if spread_summary.empty:
        lines.append("No valid spread summary was produced.")
    else:
        top = spread_summary.head(12).copy()
        for col in ["mean_good_minus_bad_ret", "cum_good_minus_bad_ret", "positive_day_rate", "t_stat"]:
            if col in top.columns:
                top[col] = top[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
        lines.append(dataframe_to_markdown(top.reset_index(drop=True)))
    lines += [
        "",
        "## Prediction labels",
        "",
        "- `net_pnl_per_premium`: future net PnL after fee divided by open premium.",
        "- `retained_ratio`: future retained premium divided by open premium.",
        "- `stop_avoidance`: negative stop rate, so higher is better.",
        "- `stop_loss_avoidance`: stop loss divided by open premium, so higher is better.",
        "",
        "## Output files",
        "",
        "- `factor_layer_daily.csv`: daily layer outcomes.",
        "- `factor_layer_summary.csv`: pooled and date-equal layer metrics.",
        "- `factor_spread_daily.csv`: daily high/low and good/bad spreads.",
        "- `factor_spread_summary.csv`: spread t-stat and cumulative spread.",
        "- `factor_ic_daily.csv`: daily Rank IC by factor and future label.",
        "- `factor_ic_summary.csv`: IC mean, t-stat, hit rate, and cumulative IC.",
        "- `factor_ic_env_summary.csv`: IC summary sliced by environment.",
        "- `factor_layer_env_summary.csv`: layer summary sliced by environment.",
        "- `01_factor_spread_cumulative.png`: cumulative good-minus-bad paths.",
        "- `02_layer_net_premium_heatmap.png`: layer net PnL per open premium.",
        "- `03_layer_retained_heatmap.png`: layer retained premium ratio.",
        "- `04_layer_stop_rate_heatmap.png`: layer stop rate.",
        "- `05_factor_ic_heatmap.png`: factor-label mean IC heatmap.",
        "- `06_cum_ic_net_pnl.png`: cumulative IC for net PnL per premium.",
        "- `07_stop_rate_by_layer.png`: stop rate across layers.",
        "",
        "Note: this diagnostic uses executed trades only. It is not yet a full all-candidate universe test.",
    ]
    (out_dir / "factor_layer_report.md").write_text("\n".join(lines), encoding="utf-8")


def analyze_one_orders(path: Path, args: argparse.Namespace) -> Path:
    tag = infer_tag_from_orders(path)
    out_dir = Path(args.out_dir) if args.out_dir and len(resolve_order_paths(args)) == 1 else Path(args.output_dir) / f"factor_layers_{tag}_{args.level}"
    if args.out_dir and len(resolve_order_paths(args)) > 1:
        out_dir = Path(args.out_dir) / f"{tag}_{args.level}"
    out_dir.mkdir(parents=True, exist_ok=True)

    orders = pd.read_csv(path)
    trades = prepare_trade_rows(orders)
    factors = available_factors(trades, args.factor)
    if trades.empty:
        raise ValueError(f"no close trades found in {path}")
    if not factors:
        raise ValueError(f"no requested/default factor columns found in {path}")

    sample = prepare_layer_sample(trades, args.level, factors)
    env_cols = args.env_col
    if not env_cols:
        env_cols = [col for col in ["option_type", "vol_regime"] if col in sample.columns]
    else:
        env_cols = [col for col in env_cols if col in sample.columns]

    daily_tables = []
    daily_env_tables = []
    for factor in factors:
        layered = assign_daily_layers(sample, factor.name, args.bins, args.min_cross_section)
        if layered.empty:
            continue
        daily_tables.append(daily_layer_table(layered, factor))
        for env_col in env_cols:
            if env_col in layered.columns:
                daily_env_tables.append(daily_layer_table(layered, factor, [env_col]))

    if not daily_tables:
        raise ValueError(f"no factor produced valid daily layers for {path}")

    daily = pd.concat(daily_tables, ignore_index=True)
    layer_summary = summarize_layers(daily, args.min_days)
    spread = build_spread_table(daily)
    spread_summary = summarize_spreads(spread, args.min_days)
    ic_daily = build_ic_daily(sample, factors, args.min_cross_section)
    ic_summary = summarize_ic(ic_daily, args.min_days)

    layer_env_summary = pd.DataFrame()
    if daily_env_tables:
        layer_env_daily = pd.concat(daily_env_tables, ignore_index=True)
        env_summaries = []
        for env_col in env_cols:
            if env_col in layer_env_daily.columns:
                env_summaries.append(summarize_layers(layer_env_daily.dropna(subset=[env_col]), args.min_days, [env_col]))
        if env_summaries:
            layer_env_summary = pd.concat(env_summaries, ignore_index=True)

    ic_env_summary = pd.DataFrame()
    if env_cols:
        env_ic_tables = []
        for env_col in env_cols:
            env_ic = build_ic_daily(sample, factors, args.min_cross_section, [env_col])
            if not env_ic.empty:
                env_ic_tables.append(summarize_ic(env_ic, args.min_days, [env_col]))
        if env_ic_tables:
            ic_env_summary = pd.concat(env_ic_tables, ignore_index=True)

    daily.to_csv(out_dir / "factor_layer_daily.csv", index=False, encoding="utf-8-sig")
    layer_summary.to_csv(out_dir / "factor_layer_summary.csv", index=False, encoding="utf-8-sig")
    spread.to_csv(out_dir / "factor_spread_daily.csv", index=False, encoding="utf-8-sig")
    spread_summary.to_csv(out_dir / "factor_spread_summary.csv", index=False, encoding="utf-8-sig")
    ic_daily.to_csv(out_dir / "factor_ic_daily.csv", index=False, encoding="utf-8-sig")
    ic_summary.to_csv(out_dir / "factor_ic_summary.csv", index=False, encoding="utf-8-sig")
    layer_env_summary.to_csv(out_dir / "factor_layer_env_summary.csv", index=False, encoding="utf-8-sig")
    ic_env_summary.to_csv(out_dir / "factor_ic_env_summary.csv", index=False, encoding="utf-8-sig")

    plot_spreads(spread, spread_summary, out_dir / "01_factor_spread_cumulative.png", args.top_n_plot)
    plot_heatmap(
        pivot_metric(layer_summary, "pooled_net_pnl_per_premium"),
        "Layer net PnL / open premium",
        out_dir / "02_layer_net_premium_heatmap.png",
    )
    plot_heatmap(
        pivot_metric(layer_summary, "pooled_retained_ratio"),
        "Layer retained premium ratio",
        out_dir / "03_layer_retained_heatmap.png",
    )
    plot_heatmap(
        pivot_metric(layer_summary, "pooled_stop_rate"),
        "Layer stop rate",
        out_dir / "04_layer_stop_rate_heatmap.png",
    )
    plot_ic_heatmap(ic_summary, out_dir / "05_factor_ic_heatmap.png")
    plot_ic_cumulative(
        ic_daily,
        ic_summary.sort_values("cum_ic", ascending=False) if not ic_summary.empty else ic_summary,
        out_dir / "06_cum_ic_net_pnl.png",
        "net_pnl_per_premium",
        args.top_n_plot,
    )
    plot_stop_rate_by_layer(layer_summary, spread_summary, out_dir / "07_stop_rate_by_layer.png", args.top_n_plot)
    write_report(out_dir, tag, args.level, factors, layer_summary, spread_summary)
    return out_dir


def main() -> None:
    configure_plot_style()
    args = parse_args()
    paths = resolve_order_paths(args)
    for path in paths:
        out_dir = analyze_one_orders(path, args)
        print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
