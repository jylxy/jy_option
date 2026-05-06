#!/usr/bin/env python3
"""Candidate-universe factor diagnostics for S1 shadow labels.

This script is intentionally different from analyze_factor_layers.py:
it reads pre-budget candidate snapshots and their 1-lot shadow outcomes,
so the test is not conditioned on B2/B3 actually trading the candidate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


@dataclass(frozen=True)
class FactorSpec:
    name: str
    direction: str = "high"

    @property
    def display(self) -> str:
        return self.name if self.direction == "high" else f"{self.name}_low"


DEFAULT_FACTORS: Tuple[FactorSpec, ...] = (
    FactorSpec("premium_quality_score", "high"),
    FactorSpec("iv_rv_carry_score", "high"),
    FactorSpec("breakeven_cushion_score", "high"),
    FactorSpec("premium_to_iv_shock_score", "high"),
    FactorSpec("premium_to_stress_loss_score", "high"),
    FactorSpec("theta_vega_efficiency_score", "high"),
    FactorSpec("cost_liquidity_score", "high"),
    FactorSpec("premium_yield_margin", "high"),
    FactorSpec("premium_yield_notional", "high"),
    FactorSpec("iv_rv_spread_candidate", "high"),
    FactorSpec("iv_rv_ratio_candidate", "high"),
    FactorSpec("variance_carry", "high"),
    FactorSpec("breakeven_cushion_iv", "high"),
    FactorSpec("breakeven_cushion_rv", "high"),
    FactorSpec("premium_to_iv5_loss", "high"),
    FactorSpec("premium_to_iv10_loss", "high"),
    FactorSpec("premium_to_stress_loss", "high"),
    FactorSpec("theta_vega_efficiency", "high"),
    FactorSpec("iv_shock_loss_5_cash", "low"),
    FactorSpec("iv_shock_loss_10_cash", "low"),
    FactorSpec("stress_loss", "low"),
    FactorSpec("gamma_rent_cash", "low"),
    FactorSpec("gamma_rent_penalty", "low"),
    FactorSpec("fee_ratio", "low"),
    FactorSpec("slippage_ratio", "low"),
    FactorSpec("friction_ratio", "low"),
    FactorSpec("b3_forward_variance_pressure", "low"),
    FactorSpec("b3_term_structure_pressure", "low"),
    FactorSpec("b3_vol_of_vol_proxy", "low"),
    FactorSpec("b3_vov_trend", "low"),
    FactorSpec("b3_iv_shock_coverage", "high"),
    FactorSpec("b3_joint_stress_coverage", "high"),
    FactorSpec("b3_vomma_cash", "low"),
    FactorSpec("b3_vomma_loss_ratio", "low"),
    FactorSpec("contract_iv_skew_to_atm", "low"),
    FactorSpec("contract_skew_change_for_vega", "low"),
    FactorSpec("b3_skew_steepening", "low"),
    # B5 full-shadow extensions. These factors are intentionally mixed across
    # contract, product-side, product, and portfolio research layers; the
    # report layer is responsible for deciding where each factor belongs.
    FactorSpec("b4_contract_score", "high"),
    FactorSpec("b4_premium_to_iv10_score", "high"),
    FactorSpec("b4_premium_to_stress_score", "high"),
    FactorSpec("b4_premium_yield_margin_score", "high"),
    FactorSpec("b4_gamma_rent_score", "high"),
    FactorSpec("b4_vomma_score", "high"),
    FactorSpec("b4_breakeven_cushion_score", "high"),
    FactorSpec("b4_vol_of_vol_score", "high"),
    FactorSpec("b5_delta_to_cap", "low"),
    FactorSpec("b5_delta_ratio_to_cap", "low"),
    FactorSpec("b5_premium_share_delta_bucket", "high"),
    FactorSpec("b5_stress_share_delta_bucket", "low"),
    FactorSpec("b5_theta_per_gamma", "high"),
    FactorSpec("b5_gamma_theta_ratio", "low"),
    FactorSpec("b5_theta_per_vega", "high"),
    FactorSpec("b5_premium_per_vega", "high"),
    FactorSpec("b5_premium_to_expected_move_loss", "high"),
    FactorSpec("b5_premium_to_mae20_loss", "high"),
    FactorSpec("b5_premium_to_tail_move_loss", "high"),
    FactorSpec("b5_mom_5d", "high"),
    FactorSpec("b5_mom_20d", "high"),
    FactorSpec("b5_mom_60d", "high"),
    FactorSpec("b5_trend_z_20d", "high"),
    FactorSpec("b5_breakout_distance_up_60d", "high"),
    FactorSpec("b5_breakout_distance_down_60d", "high"),
    FactorSpec("b5_up_day_ratio_20d", "high"),
    FactorSpec("b5_down_day_ratio_20d", "high"),
    FactorSpec("b5_range_expansion_proxy_20d", "low"),
    FactorSpec("b5_atm_iv_mom_5d", "low"),
    FactorSpec("b5_atm_iv_mom_20d", "low"),
    FactorSpec("b5_atm_iv_accel", "low"),
    FactorSpec("b5_iv_zscore_60d", "high"),
    FactorSpec("b5_iv_reversion_score", "high"),
    FactorSpec("b5_days_since_product_stop", "high"),
    FactorSpec("b5_product_stop_count_20d", "low"),
    FactorSpec("b5_days_since_product_side_stop", "high"),
    FactorSpec("b5_product_side_stop_count_20d", "low"),
    FactorSpec("b5_cooldown_blocked", "low"),
    FactorSpec("b5_cooldown_penalty_score", "low"),
    FactorSpec("b5_cooldown_release_score", "high"),
    FactorSpec("b5_tick_value_ratio", "low"),
    FactorSpec("b5_low_price_flag", "low"),
    FactorSpec("b5_variance_carry_forward", "high"),
    FactorSpec("b5_capital_lockup_days", "low"),
    FactorSpec("b5_premium_per_capital_day", "high"),
)

LABELS: Tuple[str, ...] = (
    "future_retained_ratio",
    "future_net_pnl_per_premium",
    "future_stop_avoidance",
    "future_stop_loss_avoidance",
    "future_stop_overshoot_avoidance",
)

PRIMARY_BASE_FACTORS: Tuple[str, ...] = (
    "premium_quality_score",
    "friction_ratio",
    "premium_to_iv10_loss",
    "premium_to_stress_loss",
    "variance_carry",
    "theta_vega_efficiency",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Backtest tag used in output file names.")
    parser.add_argument("--candidates", help="Explicit candidate universe CSV.")
    parser.add_argument("--outcomes", help="Explicit candidate outcome CSV.")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--out-dir")
    parser.add_argument("--bins", type=int, default=5)
    parser.add_argument("--min-cross-section", type=int, default=8)
    parser.add_argument("--factor", action="append")
    parser.add_argument(
        "--level",
        action="append",
        choices=["contract", "product_side", "product"],
        help="Run only selected decision levels. Can be repeated. Defaults to all levels.",
    )
    parser.add_argument(
        "--orthogonal-max-rows-per-day",
        type=int,
        default=500,
        help="Cap rows per date for residual IC. Keeps full IC/layers exact while making orthogonal checks tractable.",
    )
    parser.add_argument("--skip-orthogonal", action="store_true")
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


def resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path, Path]:
    output_dir = Path(args.output_dir)
    if args.candidates:
        candidates = Path(args.candidates)
    elif args.tag:
        candidates = output_dir / f"s1_candidate_universe_{args.tag}.csv"
    else:
        raise SystemExit("Provide --tag or --candidates.")

    if args.outcomes:
        outcomes = Path(args.outcomes)
    elif args.tag:
        outcomes = output_dir / f"s1_candidate_outcomes_{args.tag}.csv"
    else:
        raise SystemExit("Provide --tag or --outcomes.")

    if not candidates.exists():
        raise FileNotFoundError(candidates)
    if not outcomes.exists():
        raise FileNotFoundError(outcomes)

    out_dir = Path(args.out_dir) if args.out_dir else output_dir / f"candidate_layers_{args.tag or candidates.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return candidates, outcomes, out_dir


def numeric(df: pd.DataFrame, cols: Iterable[str]) -> None:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def load_dataset(candidates_path: Path, outcomes_path: Path) -> pd.DataFrame:
    candidates = pd.read_csv(candidates_path)
    outcomes = pd.read_csv(outcomes_path)
    if "candidate_id" not in candidates.columns or "candidate_id" not in outcomes.columns:
        raise ValueError("Both files must contain candidate_id.")
    df = candidates.merge(outcomes, on="candidate_id", how="left", suffixes=("", "_out"))
    if "signal_date" not in df.columns:
        raise ValueError("Candidate file must contain signal_date.")
    df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
    df = df.dropna(subset=["signal_date"])
    categorical_cols = {
        "candidate_id",
        "signal_date",
        "entry_date",
        "exit_date",
        "product",
        "product_out",
        "bucket",
        "corr_group",
        "exchange",
        "underlying_code",
        "option_type",
        "option_type_out",
        "code",
        "code_out",
        "expiry",
        "candidate_stage",
        "label_mode",
        "vol_regime",
        "trend_state",
        "trend_role",
        "reason",
    }
    numeric(df, [col for col in df.columns if col not in categorical_cols])
    if "future_max_price_multiple" in df.columns:
        stop_line = 2.5
        overshoot = (pd.to_numeric(df["future_max_price_multiple"], errors="coerce") / stop_line) - 1.0
        stop_flag = pd.to_numeric(df.get("future_stop_flag", 0.0), errors="coerce").fillna(0.0) > 0
        df["future_stop_overshoot"] = overshoot.where(stop_flag, 0.0).clip(lower=0.0)
        df["future_stop_overshoot_avoidance"] = -df["future_stop_overshoot"]
    return df


def available_factors(df: pd.DataFrame, custom: Optional[Sequence[str]]) -> List[FactorSpec]:
    specs = [FactorSpec(name, "high") for name in custom] if custom else list(DEFAULT_FACTORS)
    out: List[FactorSpec] = []
    seen = set()
    for spec in specs:
        key = (spec.name, spec.direction)
        if key in seen or spec.name not in df.columns:
            continue
        series = pd.to_numeric(df[spec.name], errors="coerce")
        if series.notna().sum() == 0 or series.nunique(dropna=True) <= 1:
            continue
        out.append(spec)
        seen.add(key)
    return out


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce")
    weights = pd.to_numeric(weights, errors="coerce").abs()
    valid = values.notna() & weights.notna() & (weights > 0)
    if valid.any():
        return float((values[valid] * weights[valid]).sum() / weights[valid].sum())
    return float(values.mean()) if values.notna().any() else np.nan


def aggregate_candidate_level(
    df: pd.DataFrame,
    factors: Sequence[FactorSpec],
    group_cols: Sequence[str],
    include_side_count: bool = False,
) -> pd.DataFrame:
    """Aggregate contract-level shadow labels to a decision level.

    The first version used a Python loop over every date/product group, which is
    too slow for a full multi-year shadow universe. This vectorized version keeps
    the exact same weighted-mean semantics for labels/factors, with unweighted
    means as a fallback for groups whose premium weights are all zero.
    """
    weight_col = "gross_premium_cash_1lot"
    if weight_col not in df.columns:
        weight_values = pd.Series(1.0, index=df.index)
    else:
        weight_values = pd.to_numeric(df[weight_col], errors="coerce").fillna(0).abs()

    label_cols = [label for label in LABELS if label in df.columns]
    factor_cols = [spec.name for spec in factors if spec.name in df.columns]
    mean_cols = list(dict.fromkeys(["future_stop_flag"] + label_cols + factor_cols))
    extra_cols = [col for col in ("bucket", "corr_group", "vol_regime") if col in df.columns]
    optional_cols = ["option_type"] if include_side_count and "option_type" in df.columns else []
    sum_cols = ["open_premium_cash"] if "open_premium_cash" in df.columns else []
    cols = list(dict.fromkeys(list(group_cols) + optional_cols + extra_cols + mean_cols + sum_cols))

    work = df[cols].copy()
    work["_w"] = weight_values
    grouped = work.groupby(list(group_cols), dropna=False, sort=False)
    result = grouped.size().rename("candidate_count").to_frame()
    result["weight_sum"] = grouped["_w"].sum()

    if "open_premium_cash" in work.columns:
        premium_values = pd.to_numeric(work["open_premium_cash"], errors="coerce")
        result["open_premium_cash"] = premium_values.groupby(
            [work[col] for col in group_cols], dropna=False, sort=False
        ).sum()
    else:
        result["open_premium_cash"] = 0.0

    if include_side_count and "option_type" in work.columns:
        result["side_count"] = grouped["option_type"].nunique(dropna=True)

    if mean_cols:
        values = work[mean_cols].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        weighted_values = values.multiply(work["_w"], axis=0)
        weighted_values = pd.concat([work[list(group_cols)], weighted_values], axis=1)
        weighted_sums = weighted_values.groupby(list(group_cols), dropna=False, sort=False)[mean_cols].sum(min_count=1)

        mean_values = pd.concat([work[list(group_cols)], values], axis=1)
        simple_means = mean_values.groupby(list(group_cols), dropna=False, sort=False)[mean_cols].mean()

        denom = result["weight_sum"].replace(0, np.nan)
        weighted_means = weighted_sums.div(denom, axis=0)
        weighted_means = weighted_means.where(result["weight_sum"].gt(0), simple_means)
        result = result.join(weighted_means)

    if extra_cols:
        result = result.join(grouped[extra_cols].first())

    return result.reset_index().drop(columns=["weight_sum"], errors="ignore")


def make_product_side(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    return aggregate_candidate_level(df, factors, ["signal_date", "product", "option_type"])


def make_product(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    return aggregate_candidate_level(df, factors, ["signal_date", "product"], include_side_count=True)


def daily_rank_ic(df: pd.DataFrame, factors: Sequence[FactorSpec], label: str,
                  min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        data = df[["signal_date", spec.name, label]].replace([np.inf, -np.inf], np.nan).dropna()
        ics = []
        for date, group in data.groupby("signal_date", sort=True):
            if len(group) < min_cross_section:
                continue
            factor = pd.to_numeric(group[spec.name], errors="coerce")
            if spec.direction == "low":
                factor = -factor
            target = pd.to_numeric(group[label], errors="coerce")
            if factor.nunique(dropna=True) <= 1 or target.nunique(dropna=True) <= 1:
                continue
            ic = factor.rank().corr(target.rank())
            if np.isfinite(ic):
                ics.append(ic)
        arr = pd.Series(ics, dtype=float)
        t_stat = np.nan
        if len(arr) > 2 and arr.std(ddof=1) > 0:
            t_stat = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))))
        rows.append({
            "factor": spec.display,
            "raw_factor": spec.name,
            "direction": spec.direction,
            "label": label,
            "n_days": int(len(arr)),
            "mean_ic": float(arr.mean()) if len(arr) else np.nan,
            "median_ic": float(arr.median()) if len(arr) else np.nan,
            "ic_ir": float(arr.mean() / arr.std(ddof=1)) if len(arr) > 2 and arr.std(ddof=1) > 0 else np.nan,
            "t_stat": t_stat,
            "positive_ic_rate": float((arr > 0).mean()) if len(arr) else np.nan,
        })
    return pd.DataFrame(rows)


def daily_rank_ic_series(df: pd.DataFrame, factors: Sequence[FactorSpec], label: str,
                         min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        data = df[["signal_date", spec.name, label]].replace([np.inf, -np.inf], np.nan).dropna()
        for date, group in data.groupby("signal_date", sort=True):
            if len(group) < min_cross_section:
                continue
            factor = pd.to_numeric(group[spec.name], errors="coerce")
            if spec.direction == "low":
                factor = -factor
            target = pd.to_numeric(group[label], errors="coerce")
            if factor.nunique(dropna=True) <= 1 or target.nunique(dropna=True) <= 1:
                continue
            ic = factor.rank().corr(target.rank())
            if np.isfinite(ic):
                rows.append({
                    "signal_date": date,
                    "factor": spec.display,
                    "raw_factor": spec.name,
                    "direction": spec.direction,
                    "label": label,
                    "ic": float(ic),
                })
    return pd.DataFrame(rows)


def factor_matrix(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    cols = {}
    for spec in factors:
        series = pd.to_numeric(df.get(spec.name), errors="coerce")
        cols[spec.display] = -series if spec.direction == "low" else series
    return pd.DataFrame(cols).replace([np.inf, -np.inf], np.nan)


def factor_correlation(df: pd.DataFrame, factors: Sequence[FactorSpec],
                       max_rows: int = 500_000) -> pd.DataFrame:
    mat = factor_matrix(df, factors)
    mat = mat.dropna(how="all")
    if len(mat) > max_rows:
        mat = mat.sample(max_rows, random_state=7)
    return mat.corr(method="spearman", min_periods=200)


def top_correlated_pairs(corr: pd.DataFrame, threshold: float = 0.70) -> pd.DataFrame:
    rows = []
    cols = list(corr.columns)
    for i, left in enumerate(cols):
        for right in cols[i + 1:]:
            rho = corr.loc[left, right]
            if np.isfinite(rho) and abs(rho) >= threshold:
                rows.append({"factor_a": left, "factor_b": right, "spearman_rho": float(rho)})
    return pd.DataFrame(rows).sort_values("spearman_rho", key=lambda s: s.abs(), ascending=False)


def orthogonal_ic_summary(df: pd.DataFrame, factors: Sequence[FactorSpec],
                          labels: Sequence[str], min_cross_section: int,
                          max_rows_per_day: int = 500) -> pd.DataFrame:
    spec_by_name = {spec.name: spec for spec in factors}
    base_specs = [spec_by_name[name] for name in PRIMARY_BASE_FACTORS if name in spec_by_name]
    rows = []
    if not base_specs:
        return pd.DataFrame()
    for spec in factors:
        controls = [base for base in base_specs if base.name != spec.name]
        if not controls:
            continue
        need_cols = ["signal_date", spec.name] + [base.name for base in controls] + list(labels)
        data = df[[col for col in need_cols if col in df.columns]].replace([np.inf, -np.inf], np.nan)
        label_ics = {label: [] for label in labels if label in data.columns}
        for date, group in data.groupby("signal_date", sort=True):
            if len(group) < min_cross_section:
                continue
            if max_rows_per_day and len(group) > max_rows_per_day:
                group = group.sample(max_rows_per_day, random_state=int(pd.Timestamp(date).strftime("%Y%m%d")))
            use_cols = [spec.name] + [base.name for base in controls]
            work = group[use_cols].copy()
            for base in controls:
                if base.direction == "low":
                    work[base.name] = -pd.to_numeric(work[base.name], errors="coerce")
            y_factor = pd.to_numeric(work[spec.name], errors="coerce")
            if spec.direction == "low":
                y_factor = -y_factor
            work[spec.name] = y_factor
            work = work.dropna()
            if len(work) < min_cross_section or work[spec.name].nunique() <= 1:
                continue
            y = work[spec.name].rank(method="average").to_numpy(dtype=float)
            x_parts = []
            for base in controls:
                s = work[base.name]
                if s.nunique(dropna=True) <= 1:
                    continue
                x_parts.append(s.rank(method="average").to_numpy(dtype=float))
            if not x_parts:
                continue
            x = np.column_stack([np.ones(len(y))] + x_parts)
            try:
                beta = np.linalg.lstsq(x, y, rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            residual = pd.Series(y - x @ beta, index=work.index)
            if residual.nunique(dropna=True) <= 1:
                continue
            for label in label_ics:
                target = pd.to_numeric(group.loc[work.index, label], errors="coerce")
                valid = residual.notna() & target.notna()
                if valid.sum() < min_cross_section or target[valid].nunique() <= 1:
                    continue
                ic = residual[valid].rank().corr(target[valid].rank())
                if np.isfinite(ic):
                    label_ics[label].append(ic)
        for label, ics in label_ics.items():
            arr = pd.Series(ics, dtype=float)
            t_stat = np.nan
            if len(arr) > 2 and arr.std(ddof=1) > 0:
                t_stat = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))))
            rows.append({
                "factor": spec.display,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "label": label,
                "controls": ",".join(base.display for base in controls),
                "n_days": int(len(arr)),
                "mean_residual_ic": float(arr.mean()) if len(arr) else np.nan,
                "median_residual_ic": float(arr.median()) if len(arr) else np.nan,
                "t_stat": t_stat,
                "positive_residual_ic_rate": float((arr > 0).mean()) if len(arr) else np.nan,
            })
    return pd.DataFrame(rows)


def assign_daily_layers(df: pd.DataFrame, factor: str, direction: str, bins: int,
                        min_cross_section: int) -> pd.Series:
    layer = pd.Series(np.nan, index=df.index, dtype=float)
    for _, idx in df.groupby("signal_date", sort=False).groups.items():
        group = df.loc[idx, factor].replace([np.inf, -np.inf], np.nan).dropna()
        if len(group) < min_cross_section or group.nunique() <= 1:
            continue
        ranked = group.rank(method="first", ascending=(direction != "low"))
        try:
            q = pd.qcut(ranked, q=min(bins, len(group)), labels=False, duplicates="drop") + 1
        except ValueError:
            continue
        layer.loc[q.index] = q.astype(float)
    return layer


def factor_layers(df: pd.DataFrame, factors: Sequence[FactorSpec], bins: int,
                  min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        layer = assign_daily_layers(df, spec.name, spec.direction, bins, min_cross_section)
        work = df.copy()
        work["layer"] = layer
        work = work.dropna(subset=["layer"])
        if work.empty:
            continue
        for layer_id, group in work.groupby("layer", sort=True):
            row = {
                "factor": spec.display,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "layer": int(layer_id),
                "n": len(group),
                "days": group["signal_date"].nunique(),
            }
            for label in LABELS:
                if label in group.columns:
                    row[label] = pd.to_numeric(group[label], errors="coerce").mean()
            if "future_stop_flag" in group.columns:
                row["future_stop_rate"] = pd.to_numeric(group["future_stop_flag"], errors="coerce").mean()
            rows.append(row)
    return pd.DataFrame(rows)


def spread_summary(layers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if layers.empty:
        return pd.DataFrame()
    for (factor, raw_factor, direction), group in layers.groupby(["factor", "raw_factor", "direction"]):
        high = group[group["layer"] == group["layer"].max()]
        low = group[group["layer"] == group["layer"].min()]
        if high.empty or low.empty:
            continue
        row = {"factor": factor, "raw_factor": raw_factor, "direction": direction}
        for label in LABELS:
            if label in group.columns:
                row[f"{label}_good_minus_bad"] = float(high[label].iloc[0] - low[label].iloc[0])
        if "future_stop_rate" in group.columns:
            row["future_stop_rate_good_minus_bad"] = float(high["future_stop_rate"].iloc[0] - low["future_stop_rate"].iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_dataset(df: pd.DataFrame) -> pd.DataFrame:
    rows = [{
        "scope": "all",
        "rows": len(df),
        "signal_days": df["signal_date"].nunique(),
        "products": df["product"].nunique() if "product" in df.columns else np.nan,
        "codes": df["code"].nunique() if "code" in df.columns else np.nan,
        "retained_ratio": pd.to_numeric(df.get("future_retained_ratio"), errors="coerce").mean(),
        "stop_rate": pd.to_numeric(df.get("future_stop_flag"), errors="coerce").mean(),
        "unfinished_rate": df.get("reason", pd.Series(index=df.index, dtype=object)).eq("shadow_unfinished").mean(),
    }]
    if "option_type" in df.columns:
        for opt, group in df.groupby("option_type"):
            rows.append({
                "scope": f"option_type={opt}",
                "rows": len(group),
                "signal_days": group["signal_date"].nunique(),
                "products": group["product"].nunique() if "product" in group.columns else np.nan,
                "codes": group["code"].nunique() if "code" in group.columns else np.nan,
                "retained_ratio": pd.to_numeric(group.get("future_retained_ratio"), errors="coerce").mean(),
                "stop_rate": pd.to_numeric(group.get("future_stop_flag"), errors="coerce").mean(),
                "unfinished_rate": group.get("reason", pd.Series(index=group.index, dtype=object)).eq("shadow_unfinished").mean(),
            })
    return pd.DataFrame(rows)


def plot_ic_summary(ic: pd.DataFrame, out_path: Path, title: str) -> None:
    if ic.empty:
        return
    plot_df = ic[ic["label"].eq("future_retained_ratio")].sort_values("mean_ic", ascending=False).head(15)
    if plot_df.empty:
        return
    plt.figure(figsize=(11, 6))
    plt.barh(plot_df["factor"], plot_df["mean_ic"], color="#245f73")
    plt.axvline(0, color="black", linewidth=0.8)
    plt.gca().invert_yaxis()
    plt.title(title)
    plt.xlabel("Mean daily Rank IC")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_ic_heatmap(ic: pd.DataFrame, out_path: Path, title: str) -> None:
    if ic.empty:
        return
    pivot = ic.pivot_table(index="factor", columns="label", values="mean_ic", aggfunc="mean")
    if pivot.empty:
        return
    order = pivot.abs().max(axis=1).sort_values(ascending=False).head(24).index
    pivot = pivot.loc[order]
    fig, ax = plt.subplots(figsize=(12, max(6, 0.35 * len(pivot))))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-0.25, vmax=0.25)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=35, ha="right")
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title(title)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Mean Rank IC")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_layer_heatmap(layers: pd.DataFrame, out_path: Path, label: str, title: str) -> None:
    if layers.empty or label not in layers.columns:
        return
    pivot = layers.pivot_table(index="factor", columns="layer", values=label, aggfunc="mean")
    if pivot.empty:
        return
    order = pivot.std(axis=1).abs().sort_values(ascending=False).head(24).index
    pivot = pivot.loc[order]
    fig, ax = plt.subplots(figsize=(10, max(6, 0.35 * len(pivot))))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn")
    ax.set_xticks(range(len(pivot.columns)), [f"Q{int(c)}" for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title(title)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.75, label=label)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_correlation(corr: pd.DataFrame, out_path: Path, title: str) -> None:
    if corr.empty:
        return
    order = corr.abs().sum(axis=1).sort_values(ascending=False).head(28).index
    corr = corr.loc[order, order]
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(corr.values, aspect="auto", cmap="RdBu_r", vmin=-1.0, vmax=1.0)
    ax.set_xticks(range(len(corr.columns)), corr.columns, rotation=75, ha="right", fontsize=8)
    ax.set_yticks(range(len(corr.index)), corr.index, fontsize=8)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Spearman rho")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_orthogonal_heatmap(ortho: pd.DataFrame, out_path: Path, title: str) -> None:
    if ortho.empty:
        return
    pivot = ortho.pivot_table(index="factor", columns="label", values="mean_residual_ic", aggfunc="mean")
    if pivot.empty:
        return
    order = pivot.abs().max(axis=1).sort_values(ascending=False).head(24).index
    pivot = pivot.loc[order]
    fig, ax = plt.subplots(figsize=(12, max(6, 0.35 * len(pivot))))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-0.20, vmax=0.20)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=35, ha="right")
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title(title)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Residual Rank IC")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def factor_usage_map(factors: Sequence[FactorSpec]) -> pd.DataFrame:
    rows = []
    for spec in factors:
        name = spec.name
        usage = "暂不采用/待验证"
        level = "unknown"
        if name in {"friction_ratio", "fee_ratio", "slippage_ratio", "cost_liquidity_score"}:
            usage, level = "硬过滤 + 合约排序", "contract"
        elif name in {"premium_to_iv5_loss", "premium_to_iv10_loss", "premium_to_stress_loss", "premium_to_iv_shock_score", "premium_to_stress_loss_score"}:
            usage, level = "合约排序 + 风险覆盖红线", "contract"
        elif name in {"theta_vega_efficiency", "theta_vega_efficiency_score", "gamma_rent_penalty", "gamma_rent_cash"}:
            usage, level = "止损概率/手数控制", "contract"
        elif name in {"variance_carry", "iv_rv_spread_candidate", "iv_rv_ratio_candidate", "iv_rv_carry_score", "b2_product_score"}:
            usage, level = "选品种/选方向预算", "product_side"
        elif name.startswith("b3_") or "vol_of_vol" in name or "vov" in name or "skew" in name:
            usage, level = "环境调节/风控惩罚", "regime_or_product_side"
        elif name in {"premium_yield_margin", "premium_yield_notional", "premium_quality_score"}:
            usage, level = "收益质量排序，需正交化", "contract_or_product_side"
        rows.append({
            "factor": spec.display,
            "raw_factor": name,
            "direction": spec.direction,
            "suggested_usage": usage,
            "suggested_level": level,
        })
    return pd.DataFrame(rows)


def plot_layer_curve(layers: pd.DataFrame, out_path: Path, factor: str) -> None:
    data = layers[(layers["factor"] == factor) & layers["future_retained_ratio"].notna()]
    if data.empty:
        return
    plt.figure(figsize=(8, 5))
    plt.plot(data["layer"], data["future_retained_ratio"], marker="o", label="retained ratio")
    if "future_stop_rate" in data.columns:
        plt.plot(data["layer"], data["future_stop_rate"], marker="s", label="stop rate")
    plt.title(f"Layer diagnostics: {factor}")
    plt.xlabel("Layer, Q1 low quality -> Q5 high quality")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def factor_usage_map(factors: Sequence[FactorSpec]) -> pd.DataFrame:
    """Map factors to the most plausible S1 decision layer.

    Kept ASCII-only to avoid Windows console encoding corruption in generated
    CSVs and downstream reports.
    """
    rows = []
    for spec in factors:
        name = spec.name
        usage = "holdout_or_retest"
        level = "unknown"
        if name in {"friction_ratio", "fee_ratio", "slippage_ratio", "cost_liquidity_score"}:
            usage, level = "hard_cost_gate_or_contract_ranking", "contract"
        elif name in {
            "premium_to_iv5_loss",
            "premium_to_iv10_loss",
            "premium_to_stress_loss",
            "premium_to_iv_shock_score",
            "premium_to_stress_loss_score",
        }:
            usage, level = "contract_ranking_and_risk_coverage_floor", "contract"
        elif name in {
            "theta_vega_efficiency",
            "theta_vega_efficiency_score",
            "gamma_rent_penalty",
            "gamma_rent_cash",
        }:
            usage, level = "contract_sizing_or_stop_probability_control", "contract"
        elif name in {
            "variance_carry",
            "iv_rv_spread_candidate",
            "iv_rv_ratio_candidate",
            "iv_rv_carry_score",
            "b2_product_score",
        }:
            usage, level = "product_or_side_budget_tilt", "product_side"
        elif name.startswith("b3_") or "vol_of_vol" in name or "vov" in name or "skew" in name:
            usage, level = "regime_adjustment_or_risk_penalty", "regime_or_product_side"
        elif name in {"premium_yield_margin", "premium_yield_notional", "premium_quality_score"}:
            usage, level = "premium_quality_ranking_needs_orthogonalization", "contract_or_product_side"
        rows.append({
            "factor": spec.display,
            "raw_factor": name,
            "direction": spec.direction,
            "suggested_usage": usage,
            "suggested_level": level,
        })
    return pd.DataFrame(rows)


def run_level(name: str, df: pd.DataFrame, factors: Sequence[FactorSpec],
              args: argparse.Namespace, out_dir: Path) -> None:
    level_dir = out_dir / name
    level_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_dataset(df)
    summary.to_csv(level_dir / "dataset_summary.csv", index=False)

    corr = factor_correlation(df, factors)
    corr.to_csv(level_dir / "factor_correlation_matrix.csv")
    top_pairs = top_correlated_pairs(corr)
    top_pairs.to_csv(level_dir / "factor_top_correlated_pairs.csv", index=False)
    plot_correlation(corr, level_dir / "08_factor_correlation_matrix.png", f"{name}: factor correlation")

    ic_frames = [
        daily_rank_ic(df, factors, label, args.min_cross_section)
        for label in LABELS
        if label in df.columns
    ]
    ic = pd.concat(ic_frames, ignore_index=True) if ic_frames else pd.DataFrame()
    ic.to_csv(level_dir / "factor_ic_summary.csv", index=False)
    if "future_net_pnl_per_premium" in df.columns:
        daily_ic = daily_rank_ic_series(df, factors, "future_net_pnl_per_premium", args.min_cross_section)
        daily_ic.to_csv(level_dir / "factor_ic_daily_net_pnl.csv", index=False)

    layers = factor_layers(df, factors, args.bins, args.min_cross_section)
    layers.to_csv(level_dir / "factor_layer_summary.csv", index=False)
    spread_summary(layers).to_csv(level_dir / "factor_spread_summary.csv", index=False)

    ortho = (
        pd.DataFrame()
        if args.skip_orthogonal
        else orthogonal_ic_summary(
            df,
            factors,
            [label for label in LABELS if label in df.columns],
            args.min_cross_section,
            max_rows_per_day=args.orthogonal_max_rows_per_day,
        )
    )
    ortho.to_csv(level_dir / "factor_orthogonal_ic_summary.csv", index=False)
    usage = factor_usage_map(factors)
    usage.to_csv(level_dir / "factor_usage_map.csv", index=False)

    plot_ic_summary(ic, level_dir / "factor_ic_retained_ratio.png", f"{name}: retained ratio IC")
    plot_ic_heatmap(ic, level_dir / "05_factor_ic_heatmap.png", f"{name}: factor IC heatmap")
    plot_layer_heatmap(layers, level_dir / "02_layer_net_premium_heatmap.png", "future_net_pnl_per_premium", f"{name}: net pnl per premium by layer")
    plot_layer_heatmap(layers, level_dir / "03_layer_retained_heatmap.png", "future_retained_ratio", f"{name}: retained ratio by layer")
    plot_layer_heatmap(layers, level_dir / "04_layer_stop_rate_heatmap.png", "future_stop_rate", f"{name}: stop rate by layer")
    plot_orthogonal_heatmap(ortho, level_dir / "09_orthogonal_ic_heatmap.png", f"{name}: residual IC after base controls")
    if not layers.empty:
        for factor in ["premium_quality_score", "premium_to_iv10_loss", "premium_to_stress_loss", "b3_vomma_loss_ratio"]:
            if factor in set(layers["factor"]):
                plot_layer_curve(layers, level_dir / f"layer_{factor}.png", factor)


def main() -> None:
    args = parse_args()
    configure_plot_style()
    candidates_path, outcomes_path, out_dir = resolve_paths(args)
    df = load_dataset(candidates_path, outcomes_path)
    factors = available_factors(df, args.factor)
    if not factors:
        raise SystemExit("No usable factors found.")

    levels = args.level or ["contract", "product_side", "product"]
    if "contract" in levels:
        run_level("contract", df, factors, args, out_dir)
    if "product_side" in levels:
        product_side = make_product_side(df, factors)
        run_level("product_side", product_side, factors, args, out_dir)
    if "product" in levels:
        product = make_product(df, factors)
        run_level("product", product, factors, args, out_dir)

    manifest = pd.DataFrame([{
        "candidates": str(candidates_path),
        "outcomes": str(outcomes_path),
        "out_dir": str(out_dir),
        "rows": len(df),
        "factors": len(factors),
    }])
    manifest.to_csv(out_dir / "manifest.csv", index=False)
    print(f"Wrote candidate-universe diagnostics to {out_dir}")


if __name__ == "__main__":
    main()
