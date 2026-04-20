"""
回测结果可视化 — 完整版

生成 6 张图：
1. NAV 曲线 + 回撤
2. 分策略累计 PnL（S1/S3/S4）+ 手续费
3. Greeks 四维（Cash Delta / Vega / Gamma / Theta）
4. 保证金使用率 + 持仓数
5. 盘中事件统计（止盈/应急保护/Greeks超限/到期平仓）
6. 各策略独立收益率曲线（假设各策略独立运行）

用法：
    python server_deploy/output/plot_nav.py
"""
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

BASE_DIR = os.path.dirname(__file__)
NAV_FILE = os.path.join(BASE_DIR, "nav_minute.csv")
ORDERS_FILE = os.path.join(BASE_DIR, "orders_minute.csv")


def main():
    df = pd.read_csv(NAV_FILE)
    df['date'] = pd.to_datetime(df['date'])
    n = len(df)

    # 累计分策略 PnL
    df['cum_s1'] = df['s1_pnl'].cumsum()
    df['cum_s3'] = df['s3_pnl'].cumsum()
    df['cum_s4'] = df['s4_pnl'].cumsum()
    df['cum_fee'] = df['fee'].cumsum()

    # 回撤
    peak = df['nav'].cummax()
    df['drawdown'] = (df['nav'] - peak) / peak * 100

    # 保证金使用率
    df['margin_pct'] = df['margin_used'] / df['nav'] * 100

    # ══ 图1：NAV 曲线 + 回撤 ══
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                    gridspec_kw={'height_ratios': [3, 1]})
    ax1.plot(df['date'], df['nav'] / 1e6, 'b-', linewidth=1.5, label='NAV')
    ax1.axhline(10, color='gray', linestyle='--', alpha=0.5, label='起始 1000万')
    ax1.set_ylabel('NAV (百万)')
    ax1.set_title(f'逐分钟回测 NAV ({df["date"].iloc[0].strftime("%Y-%m-%d")} ~ '
                  f'{df["date"].iloc[-1].strftime("%Y-%m-%d")}，{n}天)')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    final_nav = df['nav'].iloc[-1]
    ret = (final_nav / 10_000_000 - 1) * 100
    max_dd = df['drawdown'].min()
    ax1.text(0.98, 0.95, f'收益: {ret:+.2f}%\n年化: {ret/max(n/252,0.1):+.1f}%\n'
             f'最大回撤: {max_dd:.2f}%',
             transform=ax1.transAxes, ha='right', va='top', fontsize=10,
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    ax2.fill_between(df['date'], df['drawdown'], 0, color='red', alpha=0.3)
    ax2.set_ylabel('回撤 (%)')
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(BASE_DIR, 'nav_curve.png'), dpi=120)
    print('已保存: nav_curve.png')
    plt.close()

    # ══ 图2：分策略累计 PnL ══
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(df['date'], df['cum_s1'] / 1e4, 'g-', linewidth=1.5, label='S1 卖权')
    ax.plot(df['date'], df['cum_s3'] / 1e4, 'r-', linewidth=1.5, label='S3 比例价差')
    ax.plot(df['date'], df['cum_s4'] / 1e4, 'b-', linewidth=1.5, label='S4 尾部对冲')
    ax.plot(df['date'], -df['cum_fee'] / 1e4, 'k--', linewidth=1, label='手续费（负）')
    ax.plot(df['date'], df['cum_pnl'] / 1e4, 'purple', linewidth=2, label='总 PnL')
    ax.axhline(0, color='gray', linestyle='-', alpha=0.3)
    ax.set_ylabel('累计 PnL (万元)')
    ax.set_title('分策略累计 PnL')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(BASE_DIR, 'strategy_pnl.png'), dpi=120)
    print('已保存: strategy_pnl.png')
    plt.close()

    # ══ 图3：Greeks 四维 ══
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)

    axes[0].plot(df['date'], df['cash_delta'] * 100, 'b-', linewidth=1)
    axes[0].axhline(20, color='r', linestyle='--', alpha=0.5)
    axes[0].axhline(-20, color='r', linestyle='--', alpha=0.5)
    axes[0].axhline(0, color='gray', alpha=0.3)
    axes[0].set_ylabel('Delta (% NAV)')
    axes[0].set_title('组合 Greeks 监控')
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(df['date'], df['cash_vega'] * 100, 'orange', linewidth=1)
    axes[1].axhline(-2, color='r', linestyle='--', alpha=0.5, label='硬限')
    axes[1].axhline(-1.5, color='orange', linestyle='--', alpha=0.5, label='预警')
    axes[1].axhline(0, color='gray', alpha=0.3)
    axes[1].set_ylabel('Vega (% NAV)')
    axes[1].legend(loc='upper right', fontsize=8)
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(df['date'], df['cash_gamma'], 'green', linewidth=1)
    axes[2].axhline(0, color='gray', alpha=0.3)
    axes[2].set_ylabel('Gamma')
    axes[2].grid(True, alpha=0.3)

    axes[3].plot(df['date'], df['cash_theta'], 'purple', linewidth=1)
    axes[3].axhline(0, color='gray', alpha=0.3)
    axes[3].set_ylabel('Theta (元/天)')
    axes[3].set_xlabel('日期')
    axes[3].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(BASE_DIR, 'greeks.png'), dpi=120)
    print('已保存: greeks.png')
    plt.close()

    # ══ 图4：保证金使用率 + 持仓数 ══
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    ax1.plot(df['date'], df['margin_pct'], 'brown', linewidth=1.5)
    ax1.axhline(50, color='r', linestyle='--', alpha=0.5, label='上限 50%')
    ax1.axhline(25, color='orange', linestyle='--', alpha=0.5, label='S1/S3 各 25%')
    ax1.set_ylabel('保证金使用率 (%)')
    ax1.set_title('保证金 + 持仓数')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.3)

    ax2.bar(df['date'], df['n_positions'], color='steelblue', alpha=0.7, width=1)
    ax2.set_ylabel('持仓数')
    ax2.set_xlabel('日期')
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(BASE_DIR, 'margin_positions.png'), dpi=120)
    print('已保存: margin_positions.png')
    plt.close()

    # ══ 图5：盘中事件统计 ══
    if os.path.exists(ORDERS_FILE):
        orders = pd.read_csv(ORDERS_FILE)
        orders['date'] = pd.to_datetime(orders['date'])

        # 按 action 分类统计
        action_counts = orders['action'].value_counts()
        print(f'\n=== 订单 action 统计 ===')
        for act, cnt in action_counts.items():
            print(f'  {act}: {cnt}')

        # 盘中事件（非 open/close 的 action）
        intraday_actions = ['stop_profit_s1', 'stop_profit_s3',
                            'emergency_close', 'greeks_breach_delta',
                            'greeks_breach_vega', 'expiry', 's4_dte_exit']
        events = orders[orders['action'].isin(intraday_actions)].copy()

        if not events.empty:
            fig, ax = plt.subplots(figsize=(14, 5))
            event_daily = events.groupby(['date', 'action']).size().unstack(fill_value=0)
            event_daily.plot(kind='bar', stacked=True, ax=ax, width=0.8)
            ax.set_title('盘中事件统计（止盈/应急保护/Greeks超限/到期）')
            ax.set_ylabel('事件数')
            ax.set_xlabel('日期')
            ax.legend(loc='upper right', fontsize=8)
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(os.path.join(BASE_DIR, 'intraday_events.png'), dpi=120)
            print('已保存: intraday_events.png')
            plt.close()
        else:
            print('无盘中事件记录')

    # ══ 图6：各策略独立收益率曲线 ══
    fig, ax = plt.subplots(figsize=(14, 6))
    capital = df['nav'].iloc[0] if df['nav'].iloc[0] > 0 else 10_000_000
    ax.plot(df['date'], df['cum_s1'] / capital * 100, 'g-', linewidth=1.5, label='S1 卖权')
    ax.plot(df['date'], df['cum_s3'] / capital * 100, 'r-', linewidth=1.5, label='S3 比例价差')
    ax.plot(df['date'], df['cum_s4'] / capital * 100, 'b-', linewidth=1.5, label='S4 尾部对冲')
    ax.plot(df['date'], -df['cum_fee'] / capital * 100, 'k--', linewidth=1, label='手续费（负）')
    # 净收益 = S1+S3+S4-手续费
    net_ret = (df['cum_s1'] + df['cum_s3'] + df['cum_s4'] - df['cum_fee']) / capital * 100
    ax.plot(df['date'], net_ret, 'purple', linewidth=2, label='净收益')
    ax.axhline(0, color='gray', linestyle='-', alpha=0.3)
    ax.set_ylabel('累计收益率 (%)')
    ax.set_xlabel('日期')
    ax.set_title('各策略独立收益率')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    # 标注最终值
    for label, series, color in [
        ('S1', df['cum_s1'], 'green'),
        ('S3', df['cum_s3'], 'red'),
        ('S4', df['cum_s4'], 'blue'),
    ]:
        final_pct = series.iloc[-1] / capital * 100
        ax.annotate(f'{label}: {final_pct:+.2f}%',
                    xy=(df['date'].iloc[-1], final_pct),
                    fontsize=9, color=color,
                    xytext=(5, 0), textcoords='offset points')
    plt.tight_layout()
    plt.savefig(os.path.join(BASE_DIR, 'strategy_returns.png'), dpi=120)
    print('已保存: strategy_returns.png')
    plt.close()

    # ══ 打印统计 ══
    print(f'\n=== 回测统计 ===')
    print(f'区间: {df["date"].iloc[0].strftime("%Y-%m-%d")} ~ '
          f'{df["date"].iloc[-1].strftime("%Y-%m-%d")} ({n}天)')
    print(f'最终 NAV: {final_nav:,.0f}')
    print(f'收益率: {ret:+.2f}%')
    print(f'年化: {ret/max(n/252,0.1):+.1f}%')
    print(f'最大回撤: {max_dd:.2f}%')
    print(f'总手续费: {df["fee"].sum():,.0f}')
    print(f'S1 累计: {df["cum_s1"].iloc[-1]:+,.0f}')
    print(f'S3 累计: {df["cum_s3"].iloc[-1]:+,.0f}')
    print(f'S4 累计: {df["cum_s4"].iloc[-1]:+,.0f}')
    print(f'平均持仓: {df["n_positions"].mean():.0f}')
    print(f'平均保证金率: {df["margin_pct"].mean():.1f}%')
    print(f'最高保证金率: {df["margin_pct"].max():.1f}%')


if __name__ == '__main__':
    main()
