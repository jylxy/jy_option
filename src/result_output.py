"""Result output helpers for backtest runs."""

import os

import numpy as np
import pandas as pd

from portfolio_diagnostics import build_portfolio_diagnostics_records
from runtime_paths import OUTPUT_DIR


def append_daily_diagnostics_records(
    diagnostics_records,
    *,
    positions,
    config,
    budget,
    date_str,
    nav,
    current_vol_regimes,
    current_portfolio_regime,
    normalize_product_key,
    get_product_bucket,
    get_product_corr_group,
):
    """Append portfolio diagnostics rows for one trading day."""
    if not config.get('portfolio_diagnostics_enabled', True):
        return 0
    rows = build_portfolio_diagnostics_records(
        positions=positions,
        config=config,
        budget=budget,
        date_str=date_str,
        nav=nav,
        current_vol_regimes=current_vol_regimes,
        current_portfolio_regime=current_portfolio_regime,
        normalize_product_key=normalize_product_key,
        get_product_bucket=get_product_bucket,
        get_product_corr_group=get_product_corr_group,
    )
    diagnostics_records.extend(rows)
    return len(rows)


def build_nav_snapshot_record(
    date_str,
    *,
    positions,
    nav_records,
    capital,
    day_realized,
    day_attr_realized,
    current_open_budget,
    effective_open_budget,
    current_vol_regime_counts,
    current_iv_state,
    current_portfolio_regime,
    stress_state,
    s1_shape,
    config,
):
    """Build one daily NAV record without mutating engine state."""
    holding_pnl = sum(p.daily_pnl() for p in positions)
    realized_pnl = day_realized['pnl']
    realized_fee = day_realized['fee']
    day_pnl = holding_pnl + realized_pnl - realized_fee

    s1_pnl = sum(p.daily_pnl() for p in positions if p.strat == 'S1') + day_realized['s1']
    s3_pnl = sum(p.daily_pnl() for p in positions if p.strat == 'S3') + day_realized['s3']
    s4_pnl = sum(p.daily_pnl() for p in positions if p.strat == 'S4') + day_realized['s4']

    attr = dict(day_attr_realized)
    for p in positions:
        pa = p.pnl_attribution()
        for key in attr:
            attr[key] += pa[key]

    cum_pnl = (nav_records[-1]['cum_pnl'] if nav_records else 0) + day_pnl
    nav = capital + cum_pnl
    margin = sum(p.cur_margin() for p in positions if p.role == 'sell')

    cash_delta = sum(p.cash_delta() for p in positions)
    cash_vega = sum(p.cash_vega() for p in positions)
    cash_gamma = sum(p.cash_gamma() for p in positions)
    budget = current_open_budget or effective_open_budget
    regime_counts = current_vol_regime_counts or {}
    structural_low_count = sum(
        1 for state in current_iv_state.values()
        if state.get('is_structural_low_iv')
    )

    nav_record = {
        'date': date_str, 'nav': nav, 'cum_pnl': cum_pnl,
        's1_pnl': s1_pnl, 's3_pnl': s3_pnl, 's4_pnl': s4_pnl,
        'fee': realized_fee, 'margin_used': margin,
        'cash_delta': cash_delta / max(nav, 1), 'cash_vega': cash_vega / max(nav, 1),
        'cash_gamma': cash_gamma / max(nav, 1),
        'delta_pnl': attr['delta_pnl'], 'gamma_pnl': attr['gamma_pnl'],
        'theta_pnl': attr['theta_pnl'], 'vega_pnl': attr['vega_pnl'],
        'residual_pnl': attr['residual_pnl'],
        'portfolio_vol_regime': current_portfolio_regime,
        'effective_margin_cap': budget.get('margin_cap', np.nan),
        'effective_s1_margin_cap': budget.get('s1_margin_cap', np.nan),
        'effective_s3_margin_cap': budget.get('s3_margin_cap', np.nan),
        'effective_product_margin_cap': budget.get('product_margin_cap', np.nan),
        'effective_product_side_margin_cap': budget.get('product_side_margin_cap', np.nan),
        'effective_bucket_margin_cap': budget.get('bucket_margin_cap', np.nan),
        'effective_corr_group_margin_cap': budget.get('corr_group_margin_cap', np.nan),
        'effective_bucket_max_active_products': int(
            config.get('portfolio_bucket_max_active_products', 3) or 0
        ),
        'effective_corr_group_max_active_products': int(
            config.get('portfolio_corr_group_max_active_products', 2) or 0
        ),
        'effective_stress_loss_cap': budget.get('portfolio_stress_loss_cap', np.nan),
        'effective_bucket_stress_loss_cap': budget.get('portfolio_bucket_stress_loss_cap', np.nan),
        'effective_product_side_stress_loss_cap': budget.get('product_side_stress_loss_cap', np.nan),
        'effective_corr_group_stress_loss_cap': budget.get('corr_group_stress_loss_cap', np.nan),
        'effective_contract_stress_loss_cap': budget.get('contract_stress_loss_cap', np.nan),
        'effective_contract_lot_cap': int(config.get('portfolio_contract_lot_cap', 0) or 0),
        'effective_s1_stress_budget_pct': budget.get('s1_stress_loss_budget_pct', np.nan),
        'open_budget_risk_scale': budget.get('risk_scale', np.nan),
        'open_budget_brake_reason': budget.get('brake_reason', ''),
        'current_drawdown': budget.get('current_drawdown', np.nan),
        'recent_stop_count': budget.get('recent_stop_count', np.nan),
        'stress_loss_used': stress_state.get('stress_loss', 0.0) / max(nav, 1),
        'vol_falling_products': regime_counts.get('falling_vol_carry', 0),
        'vol_low_products': regime_counts.get('low_stable_vol', 0),
        'vol_normal_products': regime_counts.get('normal_vol', 0),
        'vol_high_products': regime_counts.get('high_rising_vol', 0),
        'vol_post_stop_products': regime_counts.get('post_stop_cooldown', 0),
        'structural_low_iv_products': structural_low_count,
        'n_positions': len(positions),
    }
    nav_record.update(s1_shape)
    return nav_record, nav


def roll_position_previous_marks(positions):
    """Move current marks into previous marks after a NAV snapshot."""
    for pos in positions:
        pos.prev_price = pos.cur_price
        pos.prev_spot = pos.cur_spot
        pos.prev_iv = pos.cur_iv
        pos.prev_delta = pos.cur_delta
        pos.prev_gamma = pos.cur_gamma
        pos.prev_vega = pos.cur_vega
        pos.prev_theta = pos.cur_theta


def write_nav_progress(nav_records, tag, output_dir=OUTPUT_DIR):
    """Persist an in-progress NAV CSV during long-running backtests."""
    if not nav_records:
        return None
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"nav_{tag}.csv")
    pd.DataFrame(nav_records).to_csv(path, index=False)
    return path


def write_orders_only(orders_df, tag, output_dir=OUTPUT_DIR):
    """Persist orders when no NAV records were produced."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"orders_{tag}.csv")
    orders_df.to_csv(path, index=False)
    return path


def write_backtest_outputs(
    nav_df,
    orders_df,
    diagnostics_records,
    stats,
    tag,
    elapsed,
    output_dir=OUTPUT_DIR,
    logger=None,
):
    """Write NAV, orders, diagnostics, and Markdown report files."""
    os.makedirs(output_dir, exist_ok=True)

    nav_path = os.path.join(output_dir, f"nav_{tag}.csv")
    nav_df.to_csv(nav_path, index=False)
    if logger is not None:
        logger.info("NAV: %s (%d行)", nav_path, len(nav_df))

    orders_path = os.path.join(output_dir, f"orders_{tag}.csv")
    orders_df.to_csv(orders_path, index=False)
    if logger is not None:
        logger.info("订单: %s (%d行)", orders_path, len(orders_df))

    diagnostics_df = pd.DataFrame(diagnostics_records)
    diagnostics_path = os.path.join(output_dir, f"diagnostics_{tag}.csv")
    diagnostics_df.to_csv(diagnostics_path, index=False)
    if logger is not None:
        logger.info("诊断: %s (%d行)", diagnostics_path, len(diagnostics_df))

    report_path = os.path.join(output_dir, f"report_{tag}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 回测报告 — {tag}\n\n")
        if len(nav_df) > 0:
            f.write(f"**日期**: {nav_df['date'].iloc[0]} ~ {nav_df['date'].iloc[-1]}")
            f.write(f" ({len(nav_df)}天)\n")
        else:
            f.write("**日期**: N/A\n")
        f.write(f"**耗时**: {elapsed:.0f}秒\n\n")
        f.write("## 核心指标\n\n| 指标 | 值 |\n|------|------|\n")
        for key, value in stats.items():
            f.write(f"| {key} | {value:.4f} |\n")
        if len(nav_df) > 0:
            f.write("\n## 策略PnL\n\n")
            f.write(f"| S1 | {nav_df['s1_pnl'].sum():.0f} |\n")
            f.write(f"| S3 | {nav_df['s3_pnl'].sum():.0f} |\n")
            f.write(f"| S4 | {nav_df['s4_pnl'].sum():.0f} |\n")
            f.write(f"| 手续费 | {nav_df['fee'].sum():.0f} |\n")
            if "delta_pnl" in nav_df.columns:
                f.write("\n## PnL归因\n\n| 来源 | 累计 |\n|------|------|\n")
                f.write(f"| Delta | {nav_df['delta_pnl'].sum():,.0f} |\n")
                f.write(f"| Gamma | {nav_df['gamma_pnl'].sum():,.0f} |\n")
                f.write(f"| Theta | {nav_df['theta_pnl'].sum():,.0f} |\n")
                f.write(f"| Vega | {nav_df['vega_pnl'].sum():,.0f} |\n")
                if "residual_pnl" in nav_df.columns:
                    f.write(f"| Residual | {nav_df['residual_pnl'].sum():,.0f} |\n")
    if logger is not None:
        logger.info("报告: %s", report_path)

    return {
        "nav": nav_path,
        "orders": orders_path,
        "diagnostics": diagnostics_path,
        "report": report_path,
    }
