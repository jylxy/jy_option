"""Result output helpers for backtest runs."""

import os

import pandas as pd

from runtime_paths import OUTPUT_DIR


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
