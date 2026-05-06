"""S1 candidate-universe and B5 shadow diagnostics helpers.

These diagnostics are useful for factor research, but they are not part of the
P3B/A0 trading decision path. Keeping them outside the main engine makes the
default backtest easier to audit.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd


S1_B5_CANDIDATE_FIELDS = (
    'b5_delta_bucket', 'b5_delta_to_cap', 'b5_delta_ratio_to_cap',
    'b5_rank_in_delta_bucket', 'b5_delta_bucket_candidate_count',
    'b5_premium_share_delta_bucket', 'b5_stress_share_delta_bucket',
    'b5_theta_per_gamma', 'b5_gamma_theta_ratio',
    'b5_theta_per_vega', 'b5_premium_per_vega',
    'b5_expected_move_pct', 'b5_expected_move_loss_cash',
    'b5_premium_to_expected_move_loss',
    'b5_mae20_move_pct', 'b5_mae20_loss_cash',
    'b5_premium_to_mae20_loss',
    'b5_tail_move_pct', 'b5_tail_move_loss_cash',
    'b5_premium_to_tail_move_loss',
    'b5_mom_5d', 'b5_mom_20d', 'b5_mom_60d',
    'b5_trend_z_20d',
    'b5_breakout_distance_up_60d',
    'b5_breakout_distance_down_60d',
    'b5_up_day_ratio_20d', 'b5_down_day_ratio_20d',
    'b5_range_expansion_proxy_20d',
    'b5_atm_iv_mom_5d', 'b5_atm_iv_mom_20d',
    'b5_atm_iv_accel', 'b5_iv_zscore_60d',
    'b5_iv_reversion_score',
    'b5_days_since_product_stop', 'b5_product_stop_count_20d',
    'b5_days_since_product_side_stop',
    'b5_product_side_stop_count_20d',
    'b5_cooldown_blocked', 'b5_cooldown_penalty_score',
    'b5_cooldown_release_score',
    'b5_tick_value_ratio', 'b5_low_price_flag',
    'b5_variance_carry_forward',
    'b5_capital_lockup_days', 'b5_premium_per_capital_day',
)


def effective_count(values) -> float:
    arr = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    arr = arr[arr > 0]
    if arr.empty:
        return 0.0
    total = float(arr.sum())
    denom = float((arr ** 2).sum())
    return total * total / denom if denom > 0 else 0.0


def hhi(values) -> float:
    arr = pd.to_numeric(pd.Series(values), errors='coerce').dropna()
    arr = arr[arr > 0]
    if arr.empty:
        return np.nan
    shares = arr / float(arr.sum())
    return float((shares ** 2).sum())


def tail_dependence_product_panel(
    candidates_df: pd.DataFrame,
    *,
    config: dict,
    spot_history,
    history_series,
) -> pd.DataFrame:
    if not config.get('s1_b5_tail_dependence_enabled', True):
        return pd.DataFrame()
    if candidates_df.empty or not spot_history:
        return pd.DataFrame()

    series_map = {}
    for product in sorted(candidates_df['product'].dropna().astype(str).unique()):
        s = history_series(spot_history, product, 'spots')
        if not s.empty:
            series_map[product] = s
    if not series_map:
        return pd.DataFrame()

    spot_df = pd.DataFrame(series_map).sort_index()
    returns = spot_df.pct_change(fill_method=None)
    window_days = int(config.get('s1_b5_tail_window_days', 120) or 120)
    min_days = int(config.get('s1_b5_min_history_days', 60) or 60)
    q = float(config.get('s1_b5_tail_quantile', 0.05) or 0.05)
    q = min(max(q, 0.01), 0.49)
    rows = []
    for date_str, group in candidates_df.groupby('signal_date', sort=False):
        dt = pd.Timestamp(date_str)
        products = [
            p for p in sorted(group['product'].dropna().astype(str).unique())
            if p in returns.columns
        ]
        if len(products) < 2:
            continue
        window = returns.loc[returns.index <= dt, products].tail(window_days)
        window = window.dropna(how='all')
        if len(window) < min_days:
            continue
        port = window[products].mean(axis=1, skipna=True).dropna()
        if len(port) < min_days:
            continue
        lower_mask = port <= port.quantile(q)
        upper_mask = port >= port.quantile(1.0 - q)
        lower_n = int(lower_mask.sum())
        upper_n = int(upper_mask.sum())
        port_lower = port[lower_mask]
        port_upper = port[upper_mask]
        for product in products:
            x = window[product].reindex(port.index).dropna()
            if len(x) < min_days:
                continue
            lower_dep = np.nan
            upper_dep = np.nan
            lower_beta = np.nan
            upper_beta = np.nan
            if lower_n > 0:
                x_lower_q = float(x.quantile(q))
                lower_dep = float((x.reindex(port_lower.index) <= x_lower_q).mean())
                if len(port_lower) > 1 and float(port_lower.var()) > 0:
                    x_lower = x.reindex(port_lower.index).dropna()
                    aligned = port_lower.reindex(x_lower.index)
                    if len(x_lower) > 1 and float(aligned.var()) > 0:
                        lower_beta = float(np.cov(x_lower, aligned)[0, 1] / aligned.var())
            if upper_n > 0:
                x_upper_q = float(x.quantile(1.0 - q))
                upper_dep = float((x.reindex(port_upper.index) >= x_upper_q).mean())
                if len(port_upper) > 1 and float(port_upper.var()) > 0:
                    x_upper = x.reindex(port_upper.index).dropna()
                    aligned = port_upper.reindex(x_upper.index)
                    if len(x_upper) > 1 and float(aligned.var()) > 0:
                        upper_beta = float(np.cov(x_upper, aligned)[0, 1] / aligned.var())
            rows.append({
                'signal_date': date_str,
                'product': product,
                'b5_empirical_lower_tail_dependence_95': lower_dep,
                'b5_empirical_upper_tail_dependence_95': upper_dep,
                'b5_lower_tail_beta': lower_beta,
                'b5_upper_tail_beta': upper_beta,
                'b5_lower_tail_dependence_excess': lower_dep - q if np.isfinite(lower_dep) else np.nan,
                'b5_upper_tail_dependence_excess': upper_dep - q if np.isfinite(upper_dep) else np.nan,
                'b5_tail_window_days_used': len(window),
            })
    return pd.DataFrame(rows)


def write_b5_candidate_panels(
    candidates_df: pd.DataFrame,
    tag: str,
    *,
    config: dict,
    spot_history,
    history_series,
    output_dir: str,
    logger,
) -> None:
    if not config.get('s1_b5_shadow_factor_extension_enabled', False) or candidates_df.empty:
        return
    df = candidates_df.copy()
    for col in (
        'net_premium_cash_1lot', 'stress_loss', 'margin_estimate',
        'cash_vega', 'cash_gamma', 'cash_theta', 'abs_delta',
        'contract_iv_skew_to_atm', 'b4_contract_score',
        'b5_theta_per_gamma', 'b5_premium_to_tail_move_loss',
        'b5_cooldown_penalty_score', 'b5_delta_ratio_to_cap',
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        else:
            df[col] = np.nan
    df['product_side_key'] = df['product'].astype(str) + '_' + df['option_type'].astype(str)

    product_panel = df.groupby(['signal_date', 'product'], sort=False).agg(
        product_candidate_count=('candidate_id', 'count'),
        product_side_count=('option_type', 'nunique'),
        product_premium_sum=('net_premium_cash_1lot', 'sum'),
        product_stress_sum=('stress_loss', 'sum'),
        product_margin_sum=('margin_estimate', 'sum'),
        product_cash_vega_sum=('cash_vega', 'sum'),
        product_cash_gamma_sum=('cash_gamma', 'sum'),
        product_cash_theta_sum=('cash_theta', 'sum'),
        product_avg_delta_ratio_to_cap=('b5_delta_ratio_to_cap', 'mean'),
        product_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
        product_cooldown_penalty=('b5_cooldown_penalty_score', 'mean'),
    ).reset_index()
    total_stress = product_panel.groupby('signal_date')['product_stress_sum'].transform('sum')
    total_margin = product_panel.groupby('signal_date')['product_margin_sum'].transform('sum')
    product_panel['product_stress_share'] = product_panel['product_stress_sum'] / total_stress.replace(0, np.nan)
    product_panel['product_margin_share'] = product_panel['product_margin_sum'] / total_margin.replace(0, np.nan)
    tail_panel = tail_dependence_product_panel(
        df,
        config=config,
        spot_history=spot_history,
        history_series=history_series,
    )
    if not tail_panel.empty:
        product_panel = product_panel.merge(tail_panel, on=['signal_date', 'product'], how='left')
    product_path = os.path.join(output_dir, f"s1_b5_product_panel_{tag}.csv")
    product_panel.to_csv(product_path, index=False)
    logger.info("S1 B5 product panel: %s (%d rows)", product_path, len(product_panel))

    side_panel = df.groupby(['signal_date', 'product', 'option_type'], sort=False).agg(
        side_candidate_count=('candidate_id', 'count'),
        side_premium_sum=('net_premium_cash_1lot', 'sum'),
        side_stress_sum=('stress_loss', 'sum'),
        side_margin_sum=('margin_estimate', 'sum'),
        side_cash_vega_sum=('cash_vega', 'sum'),
        side_cash_gamma_sum=('cash_gamma', 'sum'),
        side_cash_theta_sum=('cash_theta', 'sum'),
        side_avg_abs_delta=('abs_delta', 'mean'),
        side_avg_contract_iv_skew_to_atm=('contract_iv_skew_to_atm', 'mean'),
        side_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
        side_avg_theta_per_gamma=('b5_theta_per_gamma', 'mean'),
        side_cooldown_penalty=('b5_cooldown_penalty_score', 'mean'),
        b5_mom_20d=('b5_mom_20d', 'mean'),
        b5_trend_z_20d=('b5_trend_z_20d', 'mean'),
        b5_breakout_distance_up_60d=('b5_breakout_distance_up_60d', 'mean'),
        b5_breakout_distance_down_60d=('b5_breakout_distance_down_60d', 'mean'),
        b5_atm_iv_mom_5d=('b5_atm_iv_mom_5d', 'mean'),
        b5_atm_iv_accel=('b5_atm_iv_accel', 'mean'),
    ).reset_index()
    side_path = os.path.join(output_dir, f"s1_b5_product_side_panel_{tag}.csv")
    side_panel.to_csv(side_path, index=False)
    logger.info("S1 B5 product-side panel: %s (%d rows)", side_path, len(side_panel))

    ladder_cols = ['signal_date', 'product', 'option_type', 'expiry', 'b5_delta_bucket']
    ladder_panel = df.groupby(ladder_cols, sort=False).agg(
        bucket_candidate_count=('candidate_id', 'count'),
        bucket_premium_sum=('net_premium_cash_1lot', 'sum'),
        bucket_stress_sum=('stress_loss', 'sum'),
        bucket_margin_sum=('margin_estimate', 'sum'),
        bucket_avg_abs_delta=('abs_delta', 'mean'),
        bucket_avg_tail_coverage=('b5_premium_to_tail_move_loss', 'mean'),
        bucket_avg_theta_per_gamma=('b5_theta_per_gamma', 'mean'),
        bucket_avg_b4_contract_score=('b4_contract_score', 'mean'),
    ).reset_index()
    ladder_path = os.path.join(output_dir, f"s1_b5_delta_ladder_panel_{tag}.csv")
    ladder_panel.to_csv(ladder_path, index=False)
    logger.info("S1 B5 delta-ladder panel: %s (%d rows)", ladder_path, len(ladder_panel))

    rows = []
    for date_str, group in df.groupby('signal_date', sort=False):
        product_stress = group.groupby('product')['stress_loss'].sum()
        sector_stress = group.groupby('bucket')['stress_loss'].sum()
        product_margin = group.groupby('product')['margin_estimate'].sum()
        product_vega = group.groupby('product')['cash_vega'].sum().abs()
        product_gamma = group.groupby('product')['cash_gamma'].sum().abs()
        stress_total = float(product_stress.sum())
        top5_share = np.nan
        top1_share = np.nan
        if stress_total > 0:
            shares = product_stress.sort_values(ascending=False) / stress_total
            top1_share = float(shares.iloc[0]) if len(shares) else np.nan
            top5_share = float(shares.head(5).sum())
        rows.append({
            'signal_date': date_str,
            'candidate_count': int(len(group)),
            'active_product_count': int(group['product'].nunique()),
            'active_product_side_count': int(group['product_side_key'].nunique()),
            'active_sector_count': int(group['bucket'].nunique()),
            'portfolio_premium_sum': float(group['net_premium_cash_1lot'].sum()),
            'portfolio_stress_sum': stress_total,
            'portfolio_margin_sum': float(product_margin.sum()),
            'portfolio_cash_vega_abs_sum': float(product_vega.sum()),
            'portfolio_cash_gamma_abs_sum': float(product_gamma.sum()),
            'effective_product_count_margin': effective_count(product_margin),
            'effective_product_count_stress': effective_count(product_stress),
            'effective_product_count_vega': effective_count(product_vega),
            'effective_product_count_gamma': effective_count(product_gamma),
            'top1_product_stress_share': top1_share,
            'top5_product_stress_share': top5_share,
            'hhi_product_stress': hhi(product_stress),
            'hhi_sector_stress': hhi(sector_stress),
            'portfolio_put_stress': float(group.loc[group['option_type'] == 'P', 'stress_loss'].sum()),
            'portfolio_call_stress': float(group.loc[group['option_type'] == 'C', 'stress_loss'].sum()),
        })
    portfolio_panel = pd.DataFrame(rows)
    portfolio_path = os.path.join(output_dir, f"s1_b5_portfolio_panel_{tag}.csv")
    portfolio_panel.to_csv(portfolio_path, index=False)
    logger.info("S1 B5 portfolio panel: %s (%d rows)", portfolio_path, len(portfolio_panel))

