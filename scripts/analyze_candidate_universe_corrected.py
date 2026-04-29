#!/usr/bin/env python3
"""Corrected full-shadow factor diagnostics for S1 candidate universe.

The first full-shadow report used net PnL / premium as the primary label. That
is useful, but it mechanically rewards factors that contain fee / premium or
premium yield terms. This script adds conservative labels and sample filters so
we can separate tradable signal from denominator mechanics.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

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


FACTORS: tuple[FactorSpec, ...] = (
    # Representative corrected-audit set. The raw layer script covers the full
    # B5 factor universe; corrected audit deliberately keeps one or two
    # interpretable representatives per factor family to avoid spending most of
    # the run time on highly collinear fields.
    FactorSpec("friction_ratio", "low"),
    FactorSpec("premium_yield_margin", "high"),
    FactorSpec("premium_to_iv10_loss", "high"),
    FactorSpec("premium_to_stress_loss", "high"),
    FactorSpec("b3_vomma_loss_ratio", "low"),
    FactorSpec("b3_vol_of_vol_proxy", "low"),
    FactorSpec("gamma_rent_penalty", "low"),
    FactorSpec("variance_carry", "high"),
    FactorSpec("breakeven_cushion_score", "high"),
    FactorSpec("premium_quality_score", "high"),
    FactorSpec("cost_liquidity_score", "high"),
    FactorSpec("b4_contract_score", "high"),
    FactorSpec("b4_gamma_rent_score", "high"),
    FactorSpec("b4_vol_of_vol_score", "high"),
    FactorSpec("b5_delta_ratio_to_cap", "low"),
    FactorSpec("b5_theta_per_gamma", "high"),
    FactorSpec("b5_theta_per_vega", "high"),
    FactorSpec("b5_premium_per_vega", "high"),
    FactorSpec("b5_premium_to_expected_move_loss", "high"),
    FactorSpec("b5_premium_to_mae20_loss", "high"),
    FactorSpec("b5_premium_to_tail_move_loss", "high"),
    FactorSpec("b5_mom_20d", "high"),
    FactorSpec("b5_trend_z_20d", "high"),
    FactorSpec("b5_breakout_distance_up_60d", "high"),
    FactorSpec("b5_breakout_distance_down_60d", "high"),
    FactorSpec("b5_range_expansion_proxy_20d", "low"),
    FactorSpec("b5_atm_iv_mom_5d", "low"),
    FactorSpec("b5_atm_iv_accel", "low"),
    FactorSpec("b5_iv_reversion_score", "high"),
    FactorSpec("b5_product_stop_count_20d", "low"),
    FactorSpec("b5_product_side_stop_count_20d", "low"),
    FactorSpec("b5_cooldown_penalty_score", "low"),
    FactorSpec("b5_cooldown_release_score", "high"),
    FactorSpec("b5_tick_value_ratio", "low"),
    FactorSpec("b5_low_price_flag", "low"),
    FactorSpec("b5_variance_carry_forward", "high"),
    FactorSpec("b5_capital_lockup_days", "low"),
    FactorSpec("b5_premium_per_capital_day", "high"),
)


LABELS: tuple[str, ...] = (
    "pnl_per_premium_raw",
    "pnl_per_premium_clip",
    "cash_pnl",
    "pnl_per_margin",
    "pnl_per_stress",
    "stop_avoidance",
    "stop_overshoot_avoidance",
)


PRIMARY_SAMPLE = "completed_premium_ge_100"
KEY_LABELS: tuple[str, ...] = (
    "cash_pnl",
    "pnl_per_margin",
    "pnl_per_stress",
    "stop_avoidance",
    "stop_overshoot_avoidance",
)
RESIDUAL_CONTROL_SETS: dict[str, tuple[str, ...]] = {
    "price_premium_dte_delta": (
        "log_entry_price",
        "log_open_premium_cash",
        "dte",
        "abs_delta",
    ),
    "plus_margin": (
        "log_entry_price",
        "log_open_premium_cash",
        "log_margin_estimate",
        "dte",
        "abs_delta",
    ),
    "plus_margin_stress": (
        "log_entry_price",
        "log_open_premium_cash",
        "log_margin_estimate",
        "log_stress_loss",
        "dte",
        "abs_delta",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", default="output", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--bins", type=int, default=5)
    parser.add_argument("--min-cross-section", type=int, default=8)
    parser.add_argument("--top-n", type=int, default=8)
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


def candidate_columns() -> list[str]:
    return [
        "candidate_id",
        "signal_date",
        "product",
        "option_type",
        "code",
        "option_price",
        "gross_premium_cash_1lot",
        "net_premium_cash_1lot",
        "roundtrip_fee_per_contract",
        "margin_estimate",
        "stress_loss",
        "fee_ratio",
        "friction_ratio",
        "premium_yield_notional",
        "premium_yield_margin",
        "premium_to_iv10_loss",
        "premium_to_stress_loss",
        "b3_vomma_loss_ratio",
        "b3_vol_of_vol_proxy",
        "b3_forward_variance_pressure",
        "b3_joint_stress_coverage",
        "b3_iv_shock_coverage",
        "gamma_rent_penalty",
        "variance_carry",
        "iv_rv_spread_candidate",
        "iv_rv_ratio_candidate",
        "breakeven_cushion_score",
        "premium_quality_score",
        "cost_liquidity_score",
        "dte",
        "abs_delta",
        "volume",
        "open_interest",
        "b4_contract_score",
        "b4_premium_to_iv10_score",
        "b4_premium_to_stress_score",
        "b4_premium_yield_margin_score",
        "b4_gamma_rent_score",
        "b4_vomma_score",
        "b4_breakeven_cushion_score",
        "b4_vol_of_vol_score",
        "b5_delta_to_cap",
        "b5_delta_ratio_to_cap",
        "b5_premium_share_delta_bucket",
        "b5_stress_share_delta_bucket",
        "b5_theta_per_gamma",
        "b5_gamma_theta_ratio",
        "b5_theta_per_vega",
        "b5_premium_per_vega",
        "b5_premium_to_expected_move_loss",
        "b5_premium_to_mae20_loss",
        "b5_premium_to_tail_move_loss",
        "b5_mom_5d",
        "b5_mom_20d",
        "b5_mom_60d",
        "b5_trend_z_20d",
        "b5_breakout_distance_up_60d",
        "b5_breakout_distance_down_60d",
        "b5_up_day_ratio_20d",
        "b5_down_day_ratio_20d",
        "b5_range_expansion_proxy_20d",
        "b5_atm_iv_mom_5d",
        "b5_atm_iv_mom_20d",
        "b5_atm_iv_accel",
        "b5_iv_zscore_60d",
        "b5_iv_reversion_score",
        "b5_days_since_product_stop",
        "b5_product_stop_count_20d",
        "b5_days_since_product_side_stop",
        "b5_product_side_stop_count_20d",
        "b5_cooldown_blocked",
        "b5_cooldown_penalty_score",
        "b5_cooldown_release_score",
        "b5_tick_value_ratio",
        "b5_low_price_flag",
        "b5_variance_carry_forward",
        "b5_capital_lockup_days",
        "b5_premium_per_capital_day",
    ]


def outcome_columns() -> list[str]:
    return [
        "candidate_id",
        "entry_price",
        "exit_price",
        "future_net_pnl",
        "future_net_pnl_per_premium",
        "future_retained_ratio",
        "future_stop_flag",
        "future_fee",
        "open_premium_cash",
        "future_max_price_multiple",
        "reason",
    ]


def read_data(output_dir: Path, tag: str) -> pd.DataFrame:
    candidates_path = output_dir / f"s1_candidate_universe_{tag}.csv"
    outcomes_path = output_dir / f"s1_candidate_outcomes_{tag}.csv"
    candidates = pd.read_csv(candidates_path, usecols=lambda col: col in set(candidate_columns()))
    outcomes = pd.read_csv(outcomes_path, usecols=lambda col: col in set(outcome_columns()))
    df = candidates.merge(outcomes, on="candidate_id", how="inner")
    df["signal_date"] = pd.to_datetime(df["signal_date"], errors="coerce")
    skip = {"candidate_id", "signal_date", "product", "option_type", "code", "reason"}
    for col in df.columns:
        if col not in skip:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return add_corrected_labels(df.dropna(subset=["signal_date"]))


def safe_log(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return np.log(numeric.where(numeric > 0.0))


def add_corrected_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    premium = pd.to_numeric(out["open_premium_cash"], errors="coerce").replace(0.0, np.nan)
    margin = pd.to_numeric(out.get("margin_estimate"), errors="coerce").replace(0.0, np.nan)
    stress = pd.to_numeric(out.get("stress_loss"), errors="coerce").replace(0.0, np.nan)
    pnl = pd.to_numeric(out["future_net_pnl"], errors="coerce")
    out["pnl_per_premium_raw"] = pnl / premium
    # Clip the ratio label to reduce the influence of low-price stop explosions.
    out["pnl_per_premium_clip"] = out["pnl_per_premium_raw"].clip(lower=-3.0, upper=1.0)
    out["cash_pnl"] = pnl
    out["pnl_per_margin"] = pnl / margin
    out["pnl_per_stress"] = pnl / stress
    stop = pd.to_numeric(out.get("future_stop_flag"), errors="coerce").fillna(0.0)
    out["stop_avoidance"] = -stop
    overshoot = (pd.to_numeric(out.get("future_max_price_multiple"), errors="coerce") / 2.5) - 1.0
    out["stop_overshoot"] = overshoot.where(stop > 0, 0.0).clip(lower=0.0)
    out["stop_overshoot_avoidance"] = -out["stop_overshoot"]
    out["completed_flag"] = out["reason"].isin(["shadow_expiry", "shadow_sl_daily_close"])
    out["log_entry_price"] = safe_log(out.get("entry_price"))
    out["log_open_premium_cash"] = safe_log(out.get("open_premium_cash"))
    out["log_margin_estimate"] = safe_log(out.get("margin_estimate"))
    out["log_stress_loss"] = safe_log(out.get("stress_loss"))
    return out.replace([np.inf, -np.inf], np.nan)


def sample_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    completed = df["completed_flag"].fillna(False)
    premium = pd.to_numeric(df["open_premium_cash"], errors="coerce")
    price = pd.to_numeric(df["entry_price"], errors="coerce")
    friction = pd.to_numeric(df["friction_ratio"], errors="coerce")
    return {
        "all": pd.Series(True, index=df.index),
        "completed_only": completed,
        "entry_price_ge_5": price >= 5.0,
        "premium_ge_100": premium >= 100.0,
        PRIMARY_SAMPLE: completed & (premium >= 100.0),
        "completed_premium_ge_100_low_fee": completed & (premium >= 100.0) & (friction < 0.10),
    }


def available_factors(df: pd.DataFrame) -> list[FactorSpec]:
    specs: list[FactorSpec] = []
    for spec in FACTORS:
        if spec.name not in df.columns:
            continue
        s = pd.to_numeric(df[spec.name], errors="coerce")
        if s.notna().sum() > 0 and s.nunique(dropna=True) > 1:
            specs.append(spec)
    return specs


def daily_rank_ic(df: pd.DataFrame, factors: Sequence[FactorSpec], labels: Sequence[str], min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        factor_raw = pd.to_numeric(df[spec.name], errors="coerce")
        factor = -factor_raw if spec.direction == "low" else factor_raw
        for label in labels:
            if label not in df.columns:
                continue
            data = pd.DataFrame(
                {
                    "signal_date": df["signal_date"],
                    "factor": factor,
                    "label": pd.to_numeric(df[label], errors="coerce"),
                }
            ).replace([np.inf, -np.inf], np.nan).dropna()
            ics = []
            for _, group in data.groupby("signal_date", sort=True):
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


def daily_rank_ic_series(df: pd.DataFrame, factors: Sequence[FactorSpec], label: str, min_cross_section: int) -> pd.DataFrame:
    rows = []
    if label not in df.columns:
        return pd.DataFrame()
    for spec in factors:
        factor_raw = pd.to_numeric(df[spec.name], errors="coerce")
        factor = -factor_raw if spec.direction == "low" else factor_raw
        data = pd.DataFrame(
            {
                "signal_date": df["signal_date"],
                "factor": spec.display,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "factor_value": factor,
                "label_value": pd.to_numeric(df[label], errors="coerce"),
            }
        ).replace([np.inf, -np.inf], np.nan).dropna()
        for date, group in data.groupby("signal_date", sort=True):
            if len(group) < min_cross_section:
                continue
            if group["factor_value"].nunique() <= 1 or group["label_value"].nunique() <= 1:
                continue
            ic = group["factor_value"].rank().corr(group["label_value"].rank())
            if np.isfinite(ic):
                rows.append(
                    {
                        "signal_date": date,
                        "factor": spec.display,
                        "raw_factor": spec.name,
                        "direction": spec.direction,
                        "label": label,
                        "ic": float(ic),
                        "n": int(len(group)),
                    }
                )
    return pd.DataFrame(rows)


def directed_factor_frame(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    values: dict[str, pd.Series] = {}
    for spec in factors:
        raw = pd.to_numeric(df[spec.name], errors="coerce")
        values[spec.display] = -raw if spec.direction == "low" else raw
    return pd.DataFrame(values, index=df.index).replace([np.inf, -np.inf], np.nan)


def factor_correlation_matrix(df: pd.DataFrame, factors: Sequence[FactorSpec]) -> pd.DataFrame:
    matrix = directed_factor_frame(df, factors)
    matrix = matrix.dropna(axis=1, how="all")
    return matrix.corr(method="spearman", min_periods=100)


def top_correlated_pairs(corr: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    rows = []
    if corr.empty:
        return pd.DataFrame(rows)
    cols = list(corr.columns)
    for i, left in enumerate(cols):
        for right in cols[i + 1 :]:
            rho = corr.loc[left, right]
            if pd.isna(rho):
                continue
            rows.append({"factor_a": left, "factor_b": right, "spearman_rho": float(rho), "abs_rho": abs(float(rho))})
    return pd.DataFrame(rows).sort_values("abs_rho", ascending=False).head(top_n)


def rank_corr(a, b) -> float:
    left = pd.Series(a, dtype=float)
    right = pd.Series(b, dtype=float)
    data = pd.DataFrame({"left": left, "right": right}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 3 or data["left"].nunique() <= 1 or data["right"].nunique() <= 1:
        return np.nan
    return float(data["left"].rank().corr(data["right"].rank()))


def ols_residual(y: pd.Series, controls: pd.DataFrame) -> pd.Series:
    data = pd.concat([pd.to_numeric(y, errors="coerce").rename("_y"), controls.apply(pd.to_numeric, errors="coerce")], axis=1)
    data = data.replace([np.inf, -np.inf], np.nan).dropna()
    residual = pd.Series(np.nan, index=y.index, dtype=float)
    if data.empty:
        return residual
    control_cols = [col for col in data.columns if col != "_y" and data[col].nunique(dropna=True) > 1]
    if not control_cols or len(data) <= len(control_cols) + 3:
        residual.loc[data.index] = data["_y"] - data["_y"].mean()
        return residual
    x = data[control_cols].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(x)), x])
    beta = np.linalg.lstsq(x, data["_y"].to_numpy(dtype=float), rcond=None)[0]
    residual.loc[data.index] = data["_y"].to_numpy(dtype=float) - x @ beta
    return residual


def summarize_ic_values(values: list[float], extra: dict[str, object]) -> dict[str, object]:
    arr = pd.Series(values, dtype=float).dropna()
    row = dict(extra)
    row.update(
        {
            "n_groups": int(len(arr)),
            "mean_ic": float(arr.mean()) if len(arr) else np.nan,
            "median_ic": float(arr.median()) if len(arr) else np.nan,
            "positive_ic_rate": float((arr > 0).mean()) if len(arr) else np.nan,
            "t_stat": float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))))
            if len(arr) > 2 and arr.std(ddof=1) > 0
            else np.nan,
        }
    )
    return row


def within_group_ic_summary(
    df: pd.DataFrame,
    factors: Sequence[FactorSpec],
    labels: Sequence[str],
    group_cols: Sequence[str],
    min_group_size: int,
) -> pd.DataFrame:
    available_labels = [label for label in labels if label in df.columns]
    present_group_cols = [col for col in group_cols if col in df.columns]
    factor_frame = directed_factor_frame(df, factors)
    factor_names = list(factor_frame.columns)
    label_frame = df[available_labels].apply(pd.to_numeric, errors="coerce")
    work = pd.concat([df[present_group_cols], factor_frame, label_frame], axis=1).replace([np.inf, -np.inf], np.nan)

    acc: dict[tuple[str, str], list[float]] = {(factor, label): [] for factor in factor_names for label in available_labels}
    sizes: dict[tuple[str, str], list[int]] = {(factor, label): [] for factor in factor_names for label in available_labels}
    for _, group in work.groupby(present_group_cols, sort=False, dropna=False):
        if len(group) < min_group_size:
            continue
        numeric = group[factor_names + available_labels]
        corr = numeric.rank().corr()
        for factor in factor_names:
            for label in available_labels:
                if factor not in corr.index or label not in corr.columns:
                    continue
                val = corr.loc[factor, label]
                if np.isfinite(val):
                    acc[(factor, label)].append(float(val))
                    sizes[(factor, label)].append(len(group))

    spec_by_display = {spec.display: spec for spec in factors}
    rows = []
    for (factor, label), values in acc.items():
        spec = spec_by_display[factor]
        rows.append(
            summarize_ic_values(
                values,
                {
                    "factor": factor,
                    "raw_factor": spec.name,
                    "direction": spec.direction,
                    "label": label,
                    "group_cols": "+".join(present_group_cols),
                    "mean_group_size": float(np.mean(sizes[(factor, label)])) if sizes[(factor, label)] else np.nan,
                },
            )
        )
    return pd.DataFrame(rows)


def residual_ic_summary(
    df: pd.DataFrame,
    factors: Sequence[FactorSpec],
    labels: Sequence[str],
    control_sets: dict[str, tuple[str, ...]],
    min_cross_section: int,
) -> pd.DataFrame:
    labels = [label for label in labels if label in df.columns]
    factor_frame = directed_factor_frame(df, factors)
    factor_names = list(factor_frame.columns)
    label_frame = df[labels].apply(pd.to_numeric, errors="coerce")
    spec_by_display = {spec.display: spec for spec in factors}
    rows = []
    for control_name, controls in control_sets.items():
        present_controls = [col for col in controls if col in df.columns]
        if not present_controls:
            continue
        control_frame = df[present_controls].apply(pd.to_numeric, errors="coerce")
        work = pd.concat([df[["signal_date"]], control_frame, factor_frame, label_frame], axis=1).replace([np.inf, -np.inf], np.nan)
        factor_resid_acc: dict[tuple[str, str], list[float]] = {
            (factor, label): [] for factor in factor_names for label in labels
        }
        double_resid_acc: dict[tuple[str, str], list[float]] = {
            (factor, label): [] for factor in factor_names for label in labels
        }
        sizes: dict[tuple[str, str], list[int]] = {(factor, label): [] for factor in factor_names for label in labels}
        required_size = max(min_cross_section, len(present_controls) + 5)
        for _, group in work.groupby("signal_date", sort=True):
            group = group.dropna(subset=present_controls)
            if len(group) < required_size:
                continue
            controls_df = group[present_controls]
            factor_resids = pd.DataFrame(
                {factor: ols_residual(group[factor], controls_df) for factor in factor_names},
                index=group.index,
            )
            label_resids = pd.DataFrame(
                {label: ols_residual(group[label], controls_df) for label in labels},
                index=group.index,
            )
            raw_label_rank = group[labels].rank()
            factor_resid_rank = factor_resids.rank()
            label_resid_rank = label_resids.rank()
            corr_factor_raw = pd.concat([factor_resid_rank, raw_label_rank], axis=1).corr()
            corr_double = pd.concat([factor_resid_rank, label_resid_rank], axis=1).corr()
            for factor in factor_names:
                for label in labels:
                    raw_val = corr_factor_raw.loc[factor, label] if factor in corr_factor_raw.index and label in corr_factor_raw.columns else np.nan
                    double_val = corr_double.loc[factor, label] if factor in corr_double.index and label in corr_double.columns else np.nan
                    if np.isfinite(raw_val):
                        factor_resid_acc[(factor, label)].append(float(raw_val))
                    if np.isfinite(double_val):
                        double_resid_acc[(factor, label)].append(float(double_val))
                    if np.isfinite(raw_val) or np.isfinite(double_val):
                        sizes[(factor, label)].append(len(group))

        for factor in factor_names:
            spec = spec_by_display[factor]
            for label in labels:
                base = {
                    "control_set": control_name,
                    "controls": "+".join(present_controls),
                    "factor": factor,
                    "raw_factor": spec.name,
                    "direction": spec.direction,
                    "label": label,
                    "mean_group_size": float(np.mean(sizes[(factor, label)])) if sizes[(factor, label)] else np.nan,
                }
                rows.append(
                    summarize_ic_values(
                        factor_resid_acc[(factor, label)],
                        {**base, "residual_mode": "factor_resid_vs_raw_label"},
                    )
                )
                rows.append(
                    summarize_ic_values(
                        double_resid_acc[(factor, label)],
                        {**base, "residual_mode": "factor_and_label_resid"},
                    )
                )
    return pd.DataFrame(rows)


def nonoverlap_ic_summary(
    df: pd.DataFrame,
    factors: Sequence[FactorSpec],
    labels: Sequence[str],
    min_cross_section: int,
) -> pd.DataFrame:
    dates = pd.Series(pd.to_datetime(df["signal_date"].dropna().unique())).sort_values().reset_index(drop=True)
    every_5th = set(dates.iloc[::5])
    subsets: dict[str, pd.DataFrame] = {
        "primary_all_dates": df,
        "every_5th_signal_date": df[df["signal_date"].isin(every_5th)],
    }
    if "code" in df.columns:
        first_code_idx = df.sort_values("signal_date").groupby("code", dropna=False).head(1).index
        subsets["first_signal_per_code"] = df.loc[first_code_idx]

    rows = []
    for name, data in subsets.items():
        if len(data) < min_cross_section:
            continue
        ic = daily_rank_ic(data, factors, labels, min_cross_section)
        if ic.empty:
            continue
        ic.insert(0, "nonoverlap_sample", name)
        ic["rows"] = len(data)
        ic["signal_days"] = data["signal_date"].nunique()
        rows.append(ic)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def assign_layers(df: pd.DataFrame, spec: FactorSpec, bins: int, min_cross_section: int) -> pd.Series:
    layer = pd.Series(np.nan, index=df.index, dtype=float)
    for _, idx in df.groupby("signal_date", sort=False).groups.items():
        values = pd.to_numeric(df.loc[idx, spec.name], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(values) < min_cross_section or values.nunique() <= 1:
            continue
        ranked = values.rank(method="first", ascending=(spec.direction != "low"))
        try:
            q = pd.qcut(ranked, q=min(bins, len(values)), labels=False, duplicates="drop") + 1
        except ValueError:
            continue
        layer.loc[q.index] = q.astype(float)
    return layer


def layer_summary(df: pd.DataFrame, factors: Sequence[FactorSpec], labels: Sequence[str], bins: int, min_cross_section: int) -> pd.DataFrame:
    rows = []
    for spec in factors:
        layer = assign_layers(df, spec, bins, min_cross_section)
        work = df.assign(layer=layer).dropna(subset=["layer"])
        for layer_id, group in work.groupby("layer", sort=True):
            row = {
                "factor": spec.display,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "layer": int(layer_id),
                "n": len(group),
                "days": group["signal_date"].nunique(),
                "stop_rate": pd.to_numeric(group["future_stop_flag"], errors="coerce").mean(),
            }
            for label in labels:
                row[label] = pd.to_numeric(group[label], errors="coerce").mean()
            rows.append(row)
    return pd.DataFrame(rows)


def spread_summary(layers: pd.DataFrame, labels: Sequence[str]) -> pd.DataFrame:
    rows = []
    for (factor, raw_factor, direction), group in layers.groupby(["factor", "raw_factor", "direction"], sort=False):
        low = group[group["layer"] == group["layer"].min()]
        high = group[group["layer"] == group["layer"].max()]
        if low.empty or high.empty:
            continue
        row = {"factor": factor, "raw_factor": raw_factor, "direction": direction}
        for label in labels:
            row[f"{label}_good_minus_bad"] = float(high[label].iloc[0] - low[label].iloc[0])
        row["stop_rate_good_minus_bad"] = float(high["stop_rate"].iloc[0] - low["stop_rate"].iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


def aggregate_level(df: pd.DataFrame, factors: Sequence[FactorSpec], group_cols: Sequence[str]) -> pd.DataFrame:
    weight = pd.to_numeric(df["open_premium_cash"], errors="coerce").fillna(0.0).abs()
    weight = weight.where(weight > 0, 1.0)
    cols = list(dict.fromkeys(list(group_cols) + [f.name for f in factors] + list(LABELS) + ["future_stop_flag", "open_premium_cash"]))
    work = df[[col for col in cols if col in df.columns]].copy()
    work["_w"] = weight
    grouped = work.groupby(list(group_cols), dropna=False, sort=False)
    result = grouped.size().rename("candidate_count").to_frame()
    result["open_premium_cash"] = grouped["open_premium_cash"].sum()
    mean_cols = [col for col in [f.name for f in factors] + list(LABELS) + ["future_stop_flag"] if col in work.columns]
    if mean_cols:
        values = work[mean_cols].apply(pd.to_numeric, errors="coerce")
        weighted = values.multiply(work["_w"], axis=0)
        weighted = pd.concat([work[list(group_cols)], weighted], axis=1)
        weighted_sum = weighted.groupby(list(group_cols), dropna=False, sort=False)[mean_cols].sum(min_count=1)
        denom = grouped["_w"].sum().replace(0.0, np.nan)
        result = result.join(weighted_sum.div(denom, axis=0))
    return result.reset_index()


def summarize_samples(df: pd.DataFrame, masks: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for name, mask in masks.items():
        data = df[mask].copy()
        rows.append(
            {
                "sample": name,
                "rows": len(data),
                "signal_days": data["signal_date"].nunique(),
                "products": data["product"].nunique() if "product" in data else np.nan,
                "stop_rate": pd.to_numeric(data["future_stop_flag"], errors="coerce").mean(),
                "mean_pnl_per_premium_raw": data["pnl_per_premium_raw"].mean(),
                "mean_pnl_per_premium_clip": data["pnl_per_premium_clip"].mean(),
                "mean_cash_pnl": data["cash_pnl"].mean(),
                "mean_pnl_per_margin": data["pnl_per_margin"].mean(),
                "mean_entry_price": data["entry_price"].mean(),
                "mean_open_premium_cash": data["open_premium_cash"].mean(),
                "unfinished_rate": data["reason"].eq("shadow_unfinished").mean(),
            }
        )
    return pd.DataFrame(rows)


def price_bin_summary(df: pd.DataFrame) -> pd.DataFrame:
    bins = [-np.inf, 0.5, 1, 2, 5, 10, np.inf]
    data = df.copy()
    data["entry_price_bin"] = pd.cut(data["entry_price"], bins=bins)
    return (
        data.groupby("entry_price_bin", observed=True)
        .agg(
            rows=("candidate_id", "size"),
            stop_rate=("future_stop_flag", "mean"),
            mean_pnl_per_premium_raw=("pnl_per_premium_raw", "mean"),
            median_pnl_per_premium_raw=("pnl_per_premium_raw", "median"),
            mean_cash_pnl=("cash_pnl", "mean"),
            mean_friction_ratio=("friction_ratio", "mean"),
            mean_open_premium_cash=("open_premium_cash", "mean"),
        )
        .reset_index()
    )


def plot_ic_heatmap(ic: pd.DataFrame, path: Path, title: str) -> None:
    if ic.empty:
        return
    pivot = ic.pivot_table(index="factor", columns="label", values="mean_ic", aggfunc="mean")
    if pivot.empty:
        return
    order = pivot.abs().max(axis=1).sort_values(ascending=False).head(18).index
    pivot = pivot.loc[order]
    fig, ax = plt.subplots(figsize=(12, max(6, 0.36 * len(pivot))))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-0.4, vmax=0.4)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=35, ha="right")
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title(title)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Mean daily Rank IC")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_cumulative_ic(ic_daily: pd.DataFrame, ic_summary: pd.DataFrame, path: Path, label: str, title: str, top_n: int) -> None:
    if ic_daily.empty or ic_summary.empty:
        return
    summary = ic_summary[ic_summary["label"].eq(label)].copy()
    if summary.empty:
        return
    summary["rank_key"] = pd.to_numeric(summary["mean_ic"], errors="coerce").abs()
    factors = summary.sort_values("rank_key", ascending=False)["factor"].head(top_n).tolist()
    data = ic_daily[ic_daily["factor"].isin(factors) & ic_daily["label"].eq(label)].copy()
    if data.empty:
        return
    data["signal_date"] = pd.to_datetime(data["signal_date"], errors="coerce")
    data["ic"] = pd.to_numeric(data["ic"], errors="coerce")
    data = data.dropna(subset=["signal_date", "ic"]).sort_values(["factor", "signal_date"])
    if data.empty:
        return

    fig, ax = plt.subplots(figsize=(11, 6))
    for factor, group in data.groupby("factor", sort=False):
        group = group.sort_values("signal_date")
        ax.plot(group["signal_date"], group["ic"].cumsum(), linewidth=1.5, label=factor)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(title)
    ax.set_ylabel("Cumulative daily Rank IC")
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_sample_decay(ic_all: pd.DataFrame, path: Path) -> None:
    if ic_all.empty:
        return
    selected = [
        "friction_ratio_low",
        "premium_to_iv10_loss",
        "premium_to_stress_loss",
        "b3_vomma_loss_ratio_low",
        "b3_vol_of_vol_proxy_low",
        "premium_yield_notional",
    ]
    data = ic_all[
        ic_all["factor"].isin(selected)
        & ic_all["label"].isin(["pnl_per_premium_raw", "cash_pnl", "pnl_per_margin"])
        & ic_all["sample"].isin(["all", PRIMARY_SAMPLE])
        & ic_all["level"].eq("contract")
    ].copy()
    if data.empty:
        return
    data["series"] = data["sample"] + " / " + data["label"]
    pivot = data.pivot_table(index="factor", columns="series", values="mean_ic", aggfunc="mean").reindex(selected)
    fig, ax = plt.subplots(figsize=(12, 6))
    pivot.plot(kind="bar", ax=ax)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("IC decay after corrected labels and tradable sample filter")
    ax.set_ylabel("Mean daily Rank IC")
    ax.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_price_bins(price_bins: pd.DataFrame, path: Path) -> None:
    if price_bins.empty:
        return
    labels = price_bins["entry_price_bin"].astype(str)
    fig, ax1 = plt.subplots(figsize=(11, 6))
    ax1.bar(labels, price_bins["mean_pnl_per_premium_raw"], color="#9a4d42", alpha=0.75, label="Mean PnL / premium")
    ax1.axhline(0, color="black", linewidth=0.8)
    ax1.set_ylabel("Mean PnL / premium")
    ax1.tick_params(axis="x", rotation=25)
    ax2 = ax1.twinx()
    ax2.plot(labels, price_bins["stop_rate"], color="#245f73", marker="o", label="Stop rate")
    ax2.set_ylabel("Stop rate")
    fig.suptitle("Low-price contracts amplify ratio labels and stop losses")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_stop_layers(layers: pd.DataFrame, spreads: pd.DataFrame, path: Path, top_n: int) -> None:
    if layers.empty or spreads.empty:
        return
    top = spreads.sort_values("stop_rate_good_minus_bad", key=lambda s: s.abs(), ascending=False)["factor"].head(top_n)
    fig, ax = plt.subplots(figsize=(10, 6))
    for factor in top:
        group = layers[layers["factor"].eq(factor)].copy()
        if group.empty:
            continue
        ax.plot(group["layer"], group["stop_rate"], marker="o", linewidth=1.4, label=factor)
    ax.set_title("Corrected primary sample: stop rate by layer")
    ax.set_xlabel("Layer, Q1 low quality -> Q5 high quality")
    ax.set_ylabel("Future stop rate")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_correlation_matrix(corr: pd.DataFrame, path: Path) -> None:
    if corr.empty:
        return
    order = corr.abs().sum(axis=1).sort_values(ascending=False).index
    corr = corr.loc[order, order]
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(corr.values, aspect="auto", cmap="RdBu_r", vmin=-1.0, vmax=1.0)
    ax.set_xticks(range(len(corr.columns)), corr.columns, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(len(corr.index)), corr.index, fontsize=8)
    ax.set_title("Primary sample: directed factor Spearman correlation matrix")
    for i in range(corr.shape[0]):
        for j in range(corr.shape[1]):
            val = corr.iat[i, j]
            if np.isfinite(val) and (abs(val) >= 0.65 or i == j):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=6)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Spearman rho")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_residual_ic_heatmap(residual: pd.DataFrame, path: Path, control_set: str, residual_mode: str) -> None:
    if residual.empty:
        return
    data = residual[
        residual["control_set"].eq(control_set)
        & residual["residual_mode"].eq(residual_mode)
        & residual["label"].isin(KEY_LABELS)
    ].copy()
    if data.empty:
        return
    pivot = data.pivot_table(index="factor", columns="label", values="mean_ic", aggfunc="mean")
    if pivot.empty:
        return
    order = pivot.abs().max(axis=1).sort_values(ascending=False).head(18).index
    pivot = pivot.loc[order]
    fig, ax = plt.subplots(figsize=(12, max(6, 0.36 * len(pivot))))
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-0.25, vmax=0.25)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=35, ha="right")
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_title(f"Residual IC after controls: {control_set} / {residual_mode}")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if np.isfinite(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.75, label="Mean daily residual Rank IC")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_nonoverlap_comparison(nonoverlap: pd.DataFrame, path: Path, label: str, top_n: int) -> None:
    if nonoverlap.empty:
        return
    data = nonoverlap[nonoverlap["label"].eq(label)].copy()
    if data.empty:
        return
    primary = data[data["nonoverlap_sample"].eq("primary_all_dates")].copy()
    if primary.empty:
        return
    top = primary.assign(rank_key=primary["mean_ic"].abs()).sort_values("rank_key", ascending=False)["factor"].head(top_n)
    data = data[data["factor"].isin(top)]
    pivot = data.pivot_table(index="factor", columns="nonoverlap_sample", values="mean_ic", aggfunc="mean").reindex(top)
    if pivot.empty:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    pivot.plot(kind="bar", ax=ax)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(f"Non-overlap robustness for {label}")
    ax.set_ylabel("Mean daily Rank IC")
    ax.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_usage_map(usage: pd.DataFrame, path: Path) -> None:
    if usage.empty:
        return
    data = usage.copy()
    data["primary_score"] = pd.to_numeric(data["primary_score"], errors="coerce").fillna(0.0)
    data = data.sort_values("primary_score", ascending=True).tail(18)
    fig, ax = plt.subplots(figsize=(11, max(6, 0.32 * len(data))))
    colors = np.where(data["adoption"].eq("do_not_use_yet"), "#9a4d42", "#245f73")
    ax.barh(data["factor"], data["primary_score"], color=colors, alpha=0.8)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title("Factor usage map: primary evidence score")
    ax.set_xlabel("Composite evidence score")
    for i, row in enumerate(data.itertuples(index=False)):
        ax.text(row.primary_score, i, f" {row.primary_use}", va="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def metric_lookup(table: pd.DataFrame, label: str, column: str = "mean_ic") -> dict[str, float]:
    if table.empty or "label" not in table.columns or column not in table.columns:
        return {}
    data = table[table["label"].eq(label)]
    return dict(zip(data["factor"], pd.to_numeric(data[column], errors="coerce")))


def factor_usage_map(
    factors: Sequence[FactorSpec],
    contract_ic: pd.DataFrame,
    product_side_ic: pd.DataFrame,
    residual: pd.DataFrame,
    nonoverlap: pd.DataFrame,
    corr_pairs: pd.DataFrame,
) -> pd.DataFrame:
    cash_ic = metric_lookup(contract_ic, "cash_pnl")
    margin_ic = metric_lookup(contract_ic, "pnl_per_margin")
    stress_ic = metric_lookup(contract_ic, "pnl_per_stress")
    stop_ic = metric_lookup(contract_ic, "stop_avoidance")
    product_margin_ic = metric_lookup(product_side_ic, "pnl_per_margin")

    residual_core = residual[
        residual.get("control_set", pd.Series(dtype=str)).eq("plus_margin_stress")
        & residual.get("residual_mode", pd.Series(dtype=str)).eq("factor_and_label_resid")
        & residual.get("label", pd.Series(dtype=str)).eq("pnl_per_margin")
    ] if not residual.empty else pd.DataFrame()
    residual_margin_ic = metric_lookup(residual_core, "pnl_per_margin") if not residual_core.empty else {}

    nonoverlap_every5 = nonoverlap[
        nonoverlap.get("nonoverlap_sample", pd.Series(dtype=str)).eq("every_5th_signal_date")
        & nonoverlap.get("label", pd.Series(dtype=str)).eq("pnl_per_margin")
    ] if not nonoverlap.empty else pd.DataFrame()
    every5_margin_ic = metric_lookup(nonoverlap_every5, "pnl_per_margin") if not nonoverlap_every5.empty else {}

    max_corr: dict[str, float] = {}
    corr_partner: dict[str, str] = {}
    if not corr_pairs.empty:
        for row in corr_pairs.itertuples(index=False):
            for side, other in ((row.factor_a, row.factor_b), (row.factor_b, row.factor_a)):
                if side not in max_corr or row.abs_rho > max_corr[side]:
                    max_corr[side] = float(row.abs_rho)
                    corr_partner[side] = str(other)

    static_guidance = {
        "friction_ratio_low": (
            "hard_filter + contract_ranking",
            "Do not use as alpha; it is mechanically related to low premium and fee drag.",
            "execution cost / denominator hygiene",
        ),
        "fee_ratio_low": (
            "hard_filter",
            "Do not use as product budget signal.",
            "fee drag hygiene",
        ),
        "premium_yield_notional": (
            "contract_ranking",
            "Use only with low-price and residual checks.",
            "premium richness / denominator sensitive",
        ),
        "premium_yield_margin": (
            "contract_ranking + capital_efficiency",
            "Do not use alone for product budget until residual IC is stable.",
            "capital efficiency",
        ),
        "premium_to_iv10_loss": (
            "contract_ranking",
            "Do not use as a hard filter; it is a risk-coverage ranking factor.",
            "premium coverage against IV shock",
        ),
        "premium_to_stress_loss": (
            "contract_ranking + stress_budget",
            "Do not use as product alpha without product-side confirmation.",
            "premium coverage against stress loss",
        ),
        "b3_vomma_loss_ratio_low": (
            "contract_ranking + vega_convexity_penalty",
            "Use to reduce bad short-vol convexity; not a standalone budget expander.",
            "vomma / IV convexity risk",
        ),
        "b3_vol_of_vol_proxy_low": (
            "regime_penalty",
            "Treat conditionally; high vol-of-vol may mean danger or rich insurance.",
            "vol-of-vol regime",
        ),
        "b3_forward_variance_pressure_low": (
            "regime_penalty + product_budget",
            "Use to avoid rising forward-vol pressure, not to select strikes alone.",
            "forward volatility pressure",
        ),
        "b3_joint_stress_coverage": (
            "contract_ranking + stress_budget",
            "Use as risk-budget allocator after liquidity checks.",
            "joint spot/IV stress coverage",
        ),
        "b3_iv_shock_coverage": (
            "contract_ranking + vega_risk_control",
            "Use to preserve theta while controlling IV spike loss.",
            "IV shock premium coverage",
        ),
        "gamma_rent_penalty_low": (
            "contract_ranking + stop_risk_control",
            "Use to avoid high gamma rent; do not blindly favor low gamma if theta is too thin.",
            "gamma/theta tradeoff",
        ),
        "variance_carry": (
            "product_budget",
            "Use at product/product-side level only; not for strike ranking.",
            "IV/RV variance carry",
        ),
        "iv_rv_spread_candidate": (
            "product_budget",
            "Use with RV acceleration and trend context.",
            "IV minus RV richness",
        ),
        "iv_rv_ratio_candidate": (
            "product_budget",
            "Prefer as supplementary signal because ratios are denominator-sensitive.",
            "IV/RV ratio",
        ),
        "breakeven_cushion_score": (
            "stop_risk_control + contract_ranking",
            "Use to avoid thin cushion contracts; not enough alone for budget expansion.",
            "distance-to-breakeven protection",
        ),
        "premium_quality_score": (
            "composite_reference",
            "Use as baseline control factor and benchmark, not as an unexplained black box.",
            "existing composite premium quality",
        ),
        "cost_liquidity_score": (
            "hard_filter + contract_ranking",
            "Use to protect executability; not alpha by itself.",
            "cost and liquidity quality",
        ),
    }

    rows = []
    for spec in factors:
        factor = spec.display
        primary_use, avoid, family = static_guidance.get(
            factor,
            ("do_not_use_yet", "No stable economic role assigned.", "unclassified"),
        )
        score = (
            0.35 * abs(float(margin_ic.get(factor, 0.0) or 0.0))
            + 0.25 * abs(float(residual_margin_ic.get(factor, 0.0) or 0.0))
            + 0.20 * abs(float(every5_margin_ic.get(factor, 0.0) or 0.0))
            + 0.20 * abs(float(product_margin_ic.get(factor, 0.0) or 0.0))
        )
        adoption = "use_with_role"
        if "Do not use as alpha" in avoid or "Do not use as product" in avoid:
            adoption = "hard_filter_or_control"
        if abs(float(margin_ic.get(factor, 0.0) or 0.0)) < 0.03 and abs(float(residual_margin_ic.get(factor, 0.0) or 0.0)) < 0.03:
            adoption = "do_not_use_yet"
        rows.append(
            {
                "factor": factor,
                "raw_factor": spec.name,
                "direction": spec.direction,
                "family": family,
                "primary_use": primary_use,
                "avoid_or_limit": avoid,
                "adoption": adoption,
                "contract_cash_ic": cash_ic.get(factor, np.nan),
                "contract_margin_ic": margin_ic.get(factor, np.nan),
                "contract_stress_ic": stress_ic.get(factor, np.nan),
                "contract_stop_ic": stop_ic.get(factor, np.nan),
                "product_side_margin_ic": product_margin_ic.get(factor, np.nan),
                "residual_margin_ic_plus_margin_stress": residual_margin_ic.get(factor, np.nan),
                "nonoverlap_every5_margin_ic": every5_margin_ic.get(factor, np.nan),
                "max_abs_corr": max_corr.get(factor, np.nan),
                "most_correlated_factor": corr_partner.get(factor, ""),
                "primary_score": score,
            }
        )
    return pd.DataFrame(rows).sort_values("primary_score", ascending=False)


def write_level_outputs(
    name: str,
    df: pd.DataFrame,
    factors: Sequence[FactorSpec],
    labels: Sequence[str],
    args: argparse.Namespace,
    out_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    level_dir = out_dir / name
    level_dir.mkdir(parents=True, exist_ok=True)
    ic = daily_rank_ic(df, factors, labels, args.min_cross_section)
    layers = layer_summary(df, factors, labels, args.bins, args.min_cross_section)
    spreads = spread_summary(layers, labels)
    daily_frames = [
        daily_rank_ic_series(df, factors, label, args.min_cross_section)
        for label in ("cash_pnl", "pnl_per_margin", "pnl_per_stress", "pnl_per_premium_clip", "stop_avoidance")
        if label in df.columns
    ]
    daily_ic = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    ic.to_csv(level_dir / "corrected_factor_ic_summary.csv", index=False)
    daily_ic.to_csv(level_dir / "corrected_factor_ic_daily.csv", index=False)
    layers.to_csv(level_dir / "corrected_factor_layer_summary.csv", index=False)
    spreads.to_csv(level_dir / "corrected_factor_spread_summary.csv", index=False)
    plot_ic_heatmap(ic, level_dir / "01_corrected_ic_heatmap.png", f"{name}: corrected IC heatmap")
    plot_stop_layers(layers, spreads, level_dir / "02_corrected_stop_rate_by_layer.png", args.top_n)
    plot_cumulative_ic(
        daily_ic,
        ic,
        level_dir / "03_corrected_cum_ic_cash_pnl.png",
        "cash_pnl",
        f"{name}: cumulative IC for cash PnL",
        args.top_n,
    )
    plot_cumulative_ic(
        daily_ic,
        ic,
        level_dir / "04_corrected_cum_ic_pnl_per_margin.png",
        "pnl_per_margin",
        f"{name}: cumulative IC for PnL / margin",
        args.top_n,
    )
    plot_cumulative_ic(
        daily_ic,
        ic,
        level_dir / "05_corrected_cum_ic_stop_avoidance.png",
        "stop_avoidance",
        f"{name}: cumulative IC for stop avoidance",
        args.top_n,
    )
    return ic, layers, spreads


def main() -> None:
    args = parse_args()
    configure_plot_style()
    out_dir = args.out_dir or args.output_dir / f"candidate_layers_corrected_{args.tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    df = read_data(args.output_dir, args.tag)
    factors = available_factors(df)
    masks = sample_masks(df)
    sample_summary = summarize_samples(df, masks)
    price_bins = price_bin_summary(df)
    sample_summary.to_csv(out_dir / "corrected_sample_summary.csv", index=False)
    price_bins.to_csv(out_dir / "corrected_price_bin_summary.csv", index=False)
    plot_price_bins(price_bins, out_dir / "00_low_price_distortion.png")

    # Keep the sample-decay check focused. Running every label across every
    # sample is expensive for the B5 full-shadow universe and duplicates the
    # primary-sample corrected audit below. The summary CSV still covers all
    # samples; this IC panel contrasts the broad universe with the tradable
    # primary sample on the labels that matter most for adoption decisions.
    sample_decay_names = ("all", PRIMARY_SAMPLE, "completed_premium_ge_100_low_fee")
    sample_decay_labels = (
        "pnl_per_premium_raw",
        "cash_pnl",
        "pnl_per_margin",
        "stop_avoidance",
    )
    all_ic = []
    for sample_name in sample_decay_names:
        mask = masks.get(sample_name)
        if mask is None:
            continue
        data = df[mask].copy()
        if len(data) < args.min_cross_section:
            continue
        ic = daily_rank_ic(data, factors, sample_decay_labels, args.min_cross_section)
        ic["sample"] = sample_name
        ic["level"] = "contract"
        all_ic.append(ic)
    all_ic_df = pd.concat(all_ic, ignore_index=True) if all_ic else pd.DataFrame()
    all_ic_df.to_csv(out_dir / "corrected_ic_by_sample.csv", index=False)
    plot_sample_decay(all_ic_df, out_dir / "03_ic_decay_by_sample_and_label.png")

    primary = df[masks[PRIMARY_SAMPLE]].copy()
    contract_ic, contract_layers, contract_spreads = write_level_outputs(
        "contract_primary",
        primary,
        factors,
        LABELS,
        args,
        out_dir,
    )

    product_side = aggregate_level(primary, factors, ["signal_date", "product", "option_type"])
    product_side_ic, _, _ = write_level_outputs(
        "product_side_primary",
        product_side,
        factors,
        LABELS,
        args,
        out_dir,
    )
    product = aggregate_level(primary, factors, ["signal_date", "product"])
    product_ic, _, _ = write_level_outputs(
        "product_primary",
        product,
        factors,
        LABELS,
        args,
        out_dir,
    )

    factor_corr = factor_correlation_matrix(primary, factors)
    factor_corr.to_csv(out_dir / "corrected_factor_correlation_matrix.csv")
    corr_pairs = top_correlated_pairs(factor_corr, top_n=80)
    corr_pairs.to_csv(out_dir / "corrected_factor_top_correlated_pairs.csv", index=False)
    plot_correlation_matrix(factor_corr, out_dir / "08_corrected_factor_correlation_matrix.png")

    within_group = within_group_ic_summary(
        primary,
        factors,
        KEY_LABELS,
        ["signal_date", "product", "option_type"],
        min_group_size=max(4, args.min_cross_section // 2),
    )
    within_group.to_csv(out_dir / "corrected_within_product_side_ic.csv", index=False)
    plot_ic_heatmap(
        within_group,
        out_dir / "09_within_product_side_ic_heatmap.png",
        "Within signal_date+product+option_type IC",
    )

    residual = residual_ic_summary(
        primary,
        factors,
        KEY_LABELS,
        RESIDUAL_CONTROL_SETS,
        args.min_cross_section,
    )
    residual.to_csv(out_dir / "corrected_residual_ic_summary.csv", index=False)
    plot_residual_ic_heatmap(
        residual,
        out_dir / "10_residual_ic_plus_margin_stress.png",
        control_set="plus_margin_stress",
        residual_mode="factor_and_label_resid",
    )

    nonoverlap = nonoverlap_ic_summary(primary, factors, KEY_LABELS, args.min_cross_section)
    nonoverlap.to_csv(out_dir / "corrected_nonoverlap_ic_summary.csv", index=False)
    plot_nonoverlap_comparison(
        nonoverlap,
        out_dir / "11_nonoverlap_ic_pnl_per_margin.png",
        label="pnl_per_margin",
        top_n=args.top_n,
    )

    usage = factor_usage_map(factors, contract_ic, product_side_ic, residual, nonoverlap, corr_pairs)
    usage.to_csv(out_dir / "corrected_factor_usage_map.csv", index=False)
    plot_usage_map(usage, out_dir / "12_factor_usage_map.png")

    combined = pd.concat(
        [
            contract_ic.assign(sample=PRIMARY_SAMPLE, level="contract_primary"),
            product_side_ic.assign(sample=PRIMARY_SAMPLE, level="product_side_primary"),
            product_ic.assign(sample=PRIMARY_SAMPLE, level="product_primary"),
        ],
        ignore_index=True,
    )
    combined.to_csv(out_dir / "corrected_primary_ic_all_levels.csv", index=False)
    print(f"Wrote corrected diagnostics to {out_dir}")


if __name__ == "__main__":
    main()
