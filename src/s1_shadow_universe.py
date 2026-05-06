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


def product_history_features(
    *,
    product: str,
    option_type: str,
    config: dict,
    spot_history,
    history_series,
) -> dict:
    spots = history_series(spot_history, product, 'spots')
    fields = {
        'b5_mom_5d': np.nan,
        'b5_mom_20d': np.nan,
        'b5_mom_60d': np.nan,
        'b5_trend_z_20d': np.nan,
        'b5_breakout_distance_up_60d': np.nan,
        'b5_breakout_distance_down_60d': np.nan,
        'b5_up_day_ratio_20d': np.nan,
        'b5_down_day_ratio_20d': np.nan,
        'b5_range_expansion_proxy_20d': np.nan,
        'b5_mae20_move_pct': np.nan,
        'b5_tail_move_pct': np.nan,
    }
    if spots.empty:
        return fields
    spots = spots[spots > 0]
    if spots.empty:
        return fields
    cur = float(spots.iloc[-1])
    for lb in (5, 20, 60):
        if len(spots) > lb and spots.iloc[-1 - lb] > 0:
            fields[f'b5_mom_{lb}d'] = float(cur / spots.iloc[-1 - lb] - 1.0)

    returns = spots.pct_change(fill_method=None).dropna()
    if len(returns) >= 5:
        recent_abs = returns.abs()
        if len(recent_abs) >= 21:
            denom = float(recent_abs.iloc[-21:-1].mean())
            if denom > 0:
                fields['b5_range_expansion_proxy_20d'] = float(recent_abs.iloc[-1] / denom)
    if len(returns) >= 20:
        r20 = returns.iloc[-20:]
        fields['b5_up_day_ratio_20d'] = float((r20 > 0).mean())
        fields['b5_down_day_ratio_20d'] = float((r20 < 0).mean())
        vol20 = float(r20.std(ddof=0) * np.sqrt(20.0))
        if vol20 > 0 and np.isfinite(fields['b5_mom_20d']):
            fields['b5_trend_z_20d'] = float(fields['b5_mom_20d'] / vol20)
        if str(option_type).upper() == 'P':
            fields['b5_mae20_move_pct'] = float(max(-r20.min(), 0.0))
        else:
            fields['b5_mae20_move_pct'] = float(max(r20.max(), 0.0))
    if len(returns) >= 60:
        tail_q = float(config.get('s1_b5_tail_quantile', 0.05) or 0.05)
        tail_q = min(max(tail_q, 0.01), 0.49)
        r = returns.iloc[-int(config.get('s1_b5_tail_window_days', 120) or 120):]
        if str(option_type).upper() == 'P':
            fields['b5_tail_move_pct'] = float(max(-r.quantile(tail_q), 0.0))
        else:
            fields['b5_tail_move_pct'] = float(max(r.quantile(1.0 - tail_q), 0.0))

    long_lb = int(config.get('s1_b5_trend_long_lookback_days', 60) or 60)
    if len(spots) >= max(long_lb, 2):
        window = spots.iloc[-long_lb:]
        high = float(window.max())
        low = float(window.min())
        if cur > 0:
            fields['b5_breakout_distance_up_60d'] = float(high / cur - 1.0)
            fields['b5_breakout_distance_down_60d'] = float(1.0 - low / cur)
    return fields


def iv_history_features(*, product: str, iv_history, history_series) -> dict:
    ivs = history_series(iv_history, product, 'ivs')
    fields = {
        'b5_atm_iv_mom_5d': np.nan,
        'b5_atm_iv_mom_20d': np.nan,
        'b5_atm_iv_accel': np.nan,
        'b5_iv_zscore_60d': np.nan,
        'b5_iv_reversion_score': np.nan,
    }
    if ivs.empty:
        return fields
    cur = float(ivs.iloc[-1])
    if len(ivs) > 5:
        fields['b5_atm_iv_mom_5d'] = float(cur - ivs.iloc[-6])
    if len(ivs) > 20:
        fields['b5_atm_iv_mom_20d'] = float(cur - ivs.iloc[-21])
    if np.isfinite(fields['b5_atm_iv_mom_5d']) and np.isfinite(fields['b5_atm_iv_mom_20d']):
        fields['b5_atm_iv_accel'] = float(
            fields['b5_atm_iv_mom_5d'] - fields['b5_atm_iv_mom_20d'] * 0.25
        )
    if len(ivs) >= 60:
        win = ivs.iloc[-60:]
        std = float(win.std(ddof=0))
        if std > 0:
            z = float((cur - win.mean()) / std)
            fields['b5_iv_zscore_60d'] = z
            mom_penalty = max(float(fields.get('b5_atm_iv_mom_5d') or 0.0), 0.0)
            fields['b5_iv_reversion_score'] = z - 10.0 * mom_penalty
    return fields


def stop_state_fields(
    *,
    product: str,
    option_type: str,
    date_str: str,
    stop_history,
    stop_side_history,
    normalize_product,
    is_reentry_blocked,
    last_iv_trend,
) -> dict:
    product_key = normalize_product(product)
    side_key = (product_key, str(option_type).upper())
    current = pd.Timestamp(date_str)

    def summarize_stop_dates(dates):
        clean = [pd.Timestamp(d) for d in dates if str(d)]
        clean = [d for d in clean if d <= current]
        if not clean:
            return np.nan, 0
        last = max(clean)
        count20 = sum((current - d).days <= 20 for d in clean)
        return int((current - last).days), int(count20)

    days_product, count_product = summarize_stop_dates(stop_history.get(product_key, []))
    days_side, count_side = summarize_stop_dates(stop_side_history.get(side_key, []))
    blocked = 1.0 if is_reentry_blocked('S1', product, option_type, date_str) else 0.0
    penalty = 0.0
    if np.isfinite(days_side):
        penalty = max(penalty, max(0.0, 1.0 - float(days_side) / 20.0))
    if np.isfinite(days_product):
        penalty = max(penalty, 0.5 * max(0.0, 1.0 - float(days_product) / 20.0))
    iv_trend = last_iv_trend(product)
    release = 1.0
    if blocked:
        release = 0.0
    elif np.isfinite(iv_trend) and iv_trend > 0:
        release = max(0.0, 1.0 - min(iv_trend / 0.02, 1.0))
    return {
        'b5_days_since_product_stop': days_product,
        'b5_product_stop_count_20d': count_product,
        'b5_days_since_product_side_stop': days_side,
        'b5_product_side_stop_count_20d': count_side,
        'b5_cooldown_blocked': blocked,
        'b5_cooldown_penalty_score': penalty,
        'b5_cooldown_release_score': release,
    }


def delta_bucket(abs_delta, *, config: dict) -> str:
    try:
        abs_delta = float(abs_delta)
    except (TypeError, ValueError):
        return ''
    if not np.isfinite(abs_delta):
        return ''
    raw_edges = config.get('s1_b5_delta_bucket_edges', [0, 0.02, 0.04, 0.06, 0.08, 0.10])
    edges = []
    for value in raw_edges:
        try:
            edges.append(float(value))
        except (TypeError, ValueError):
            continue
    edges = sorted(set(edges))
    if len(edges) < 2:
        edges = [0, 0.02, 0.04, 0.06, 0.08, 0.10]
    for left, right in zip(edges[:-1], edges[1:]):
        if abs_delta >= left and abs_delta <= right:
            return f"{left:.2f}_{right:.2f}"
    return 'above_cap' if abs_delta > edges[-1] else 'below_floor'


def _numeric_column(frame: pd.DataFrame, *names: str, default=np.nan) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return pd.to_numeric(frame[name], errors='coerce')
    return pd.Series(default, index=frame.index, dtype=float)


def _loss_for_move_vectorized(frame: pd.DataFrame, move_pct) -> pd.Series:
    move = pd.to_numeric(move_pct, errors='coerce')
    if not isinstance(move, pd.Series):
        move = pd.Series(move, index=frame.index)
    move = move.reindex(frame.index)
    spot = _numeric_column(frame, 'spot_close', 'spot')
    mult = _numeric_column(frame, 'multiplier', 'mult')
    delta = _numeric_column(frame, 'delta').abs().fillna(0.0)
    gamma = _numeric_column(frame, 'gamma').abs().fillna(0.0)
    valid = move.notna() & (move > 0) & spot.notna() & mult.notna() & (spot > 0) & (mult > 0)
    d_spot = spot * move
    loss = delta * d_spot * mult + 0.5 * gamma * d_spot * d_spot * mult
    loss = loss.where(valid & (loss > 0), np.nan)
    return loss.astype(float)


def add_b5_shadow_fields(
    candidates: pd.DataFrame,
    *,
    date_str: str,
    product: str,
    option_type: str,
    config: dict,
    spot_history,
    iv_history,
    stop_history,
    stop_side_history,
    history_series,
    option_roundtrip_fee,
    normalize_product,
    is_reentry_blocked,
    last_iv_trend,
    force: bool = False,
) -> pd.DataFrame:
    if (
        not force
        and not config.get('s1_b5_shadow_factor_extension_enabled', False)
    ) or candidates is None or candidates.empty:
        return candidates

    c = candidates.copy()
    if 'abs_delta' in c.columns:
        abs_delta = pd.to_numeric(c['abs_delta'], errors='coerce').abs()
    elif 'delta' in c.columns:
        abs_delta = pd.to_numeric(c['delta'], errors='coerce').abs()
    else:
        abs_delta = pd.Series(np.nan, index=c.index)
    delta_cap = float(config.get('s1_sell_delta_cap', 0.10) or 0.10)
    c['b5_delta_bucket'] = abs_delta.map(lambda value: delta_bucket(value, config=config))
    c['b5_delta_to_cap'] = delta_cap - abs_delta
    c['b5_delta_ratio_to_cap'] = abs_delta / delta_cap if delta_cap > 0 else np.nan
    c['b5_rank_in_delta_bucket'] = c.groupby('b5_delta_bucket', sort=False).cumcount() + 1
    c['b5_delta_bucket_candidate_count'] = c.groupby(
        'b5_delta_bucket', sort=False
    )['b5_delta_bucket'].transform('size')

    mult = _numeric_column(c, 'multiplier', 'mult')
    price = _numeric_column(c, 'option_close')
    roundtrip_fee = option_roundtrip_fee(product, option_type)
    net_premium = _numeric_column(c, 'net_premium_cash')
    fallback_premium = price * mult - float(roundtrip_fee or 0.0)
    net_premium = net_premium.where(net_premium.notna(), fallback_premium)
    stress = _numeric_column(c, 'stress_loss').clip(lower=0)
    bucket_premium = net_premium.groupby(c['b5_delta_bucket']).transform('sum')
    bucket_stress = stress.groupby(c['b5_delta_bucket']).transform('sum')
    total_premium = float(net_premium.sum()) if net_premium.notna().any() else np.nan
    total_stress = float(stress.sum()) if stress.notna().any() else np.nan
    c['b5_premium_share_delta_bucket'] = (
        bucket_premium / total_premium if np.isfinite(total_premium) and total_premium != 0 else np.nan
    )
    c['b5_stress_share_delta_bucket'] = (
        bucket_stress / total_stress if np.isfinite(total_stress) and total_stress != 0 else np.nan
    )

    spot = _numeric_column(c, 'spot_close', 'spot')
    theta_cash = _numeric_column(c, 'theta').abs() * mult
    vega_cash = _numeric_column(c, 'vega').abs() * mult
    gamma_cash = _numeric_column(c, 'gamma').abs() * mult * spot * spot
    c['b5_theta_per_gamma'] = theta_cash / gamma_cash.replace(0, np.nan)
    c['b5_gamma_theta_ratio'] = gamma_cash / theta_cash.replace(0, np.nan)
    c['b5_theta_per_vega'] = theta_cash / vega_cash.replace(0, np.nan)
    c['b5_premium_per_vega'] = net_premium / vega_cash.replace(0, np.nan)

    dte = _numeric_column(c, 'dte')
    rv_ref = _numeric_column(c, 'rv_ref', 'entry_rv20')
    c['b5_expected_move_pct'] = rv_ref * np.sqrt(dte.clip(lower=1) / 252.0)

    hist_features = product_history_features(
        product=product,
        option_type=option_type,
        config=config,
        spot_history=spot_history,
        history_series=history_series,
    )
    iv_features = iv_history_features(
        product=product,
        iv_history=iv_history,
        history_series=history_series,
    )
    stop_features = stop_state_fields(
        product=product,
        option_type=option_type,
        date_str=date_str,
        stop_history=stop_history,
        stop_side_history=stop_side_history,
        normalize_product=normalize_product,
        is_reentry_blocked=is_reentry_blocked,
        last_iv_trend=last_iv_trend,
    )
    for key, value in {**hist_features, **iv_features, **stop_features}.items():
        c[key] = value

    c['b5_expected_move_loss_cash'] = _loss_for_move_vectorized(c, c['b5_expected_move_pct'])
    c['b5_mae20_loss_cash'] = _loss_for_move_vectorized(c, c['b5_mae20_move_pct'])
    c['b5_tail_move_loss_cash'] = _loss_for_move_vectorized(c, c['b5_tail_move_pct'])
    c['b5_premium_to_expected_move_loss'] = (
        net_premium / pd.to_numeric(c['b5_expected_move_loss_cash'], errors='coerce').replace(0, np.nan)
    )
    c['b5_premium_to_mae20_loss'] = (
        net_premium / pd.to_numeric(c['b5_mae20_loss_cash'], errors='coerce').replace(0, np.nan)
    )
    c['b5_premium_to_tail_move_loss'] = (
        net_premium / pd.to_numeric(c['b5_tail_move_loss_cash'], errors='coerce').replace(0, np.nan)
    )

    min_tick = float(config.get('s1_b5_min_tick', 1.0) or 1.0)
    c['b5_tick_value_ratio'] = min_tick / price.replace(0, np.nan)
    min_price = float(config.get('s1_min_option_price', 0.0) or 0.0)
    c['b5_low_price_flag'] = (price < max(min_price, min_tick * 2.0)).astype(float)
    c['b5_variance_carry_forward'] = (
        _numeric_column(c, 'contract_iv', 'entry_atm_iv') ** 2
        - rv_ref ** 2
    )
    margin = _numeric_column(c, 'margin')
    c['b5_capital_lockup_days'] = margin * dte.clip(lower=1)
    c['b5_premium_per_capital_day'] = net_premium / c['b5_capital_lockup_days'].replace(0, np.nan)
    return c


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
