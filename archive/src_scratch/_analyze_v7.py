"""Archived scratch script: 分析v7回测结果：保证金使用率、Vega变化、Gamma亏损归因"""
import pandas as pd
import numpy as np

nav = pd.read_csv('/macro/home/lxy/jy_option/output/nav_v7_30prod.csv')
orders = pd.read_csv('/macro/home/lxy/jy_option/output/orders_v7_30prod.csv')

print("=" * 70)
print("1. 保证金使用率")
print("=" * 70)
nav['margin_pct'] = nav['margin_used'] / nav['nav'] * 100
for _, r in nav.iterrows():
    flag = " *** >40%" if r['margin_pct'] > 40 else ""
    print(f"  {r['date']}  保证金={r['margin_used']:>12,.0f}  占比={r['margin_pct']:5.1f}%  持仓={int(r['n_positions']):>3}{flag}")
print(f"\n  均值: {nav['margin_pct'].mean():.1f}%")
print(f"  最大: {nav['margin_pct'].max():.1f}%")
print(f"  最小: {nav['margin_pct'].min():.1f}% (不含0)")

print()
print("=" * 70)
print("2. Cash Vega 变化")
print("=" * 70)
for _, r in nav.iterrows():
    vega_pct = r['cash_vega'] * 100
    print(f"  {r['date']}  Vega={vega_pct:+.3f}%")
print(f"\n  均值: {nav['cash_vega'].abs().mean()*100:.3f}%")
print(f"  最大: {nav['cash_vega'].abs().max()*100:.3f}%")

print()
print("=" * 70)
print("3. Gamma PnL 逐日分析（为什么亏这么多）")
print("=" * 70)
total_gamma = 0
for _, r in nav.iterrows():
    gp = r['gamma_pnl']
    total_gamma += gp
    flag = " *** 大亏" if gp < -50000 else ""
    if abs(gp) > 1:
        print(f"  {r['date']}  Gamma={gp:>+12,.0f}  累计={total_gamma:>+12,.0f}{flag}")

print(f"\n  Gamma总计: {nav['gamma_pnl'].sum():,.0f}")
print(f"  Gamma日均: {nav['gamma_pnl'].mean():,.0f}")
print(f"  Gamma最差日: {nav['gamma_pnl'].min():,.0f}")

# 找出Gamma大亏的日期，看看那天发生了什么
worst_days = nav.nsmallest(5, 'gamma_pnl')
print(f"\n  Gamma最差5天:")
for _, r in worst_days.iterrows():
    print(f"    {r['date']}  Gamma={r['gamma_pnl']:>+12,.0f}  Delta={r['delta_pnl']:>+12,.0f}  Theta={r['theta_pnl']:>+8,.0f}  Vega={r['vega_pnl']:>+12,.0f}  总PnL={r['s1_pnl']+r['s3_pnl']+r['s4_pnl']-r['fee']:>+12,.0f}")

print()
print("=" * 70)
print("4. PnL归因占比")
print("=" * 70)
d = nav['delta_pnl'].sum()
g = nav['gamma_pnl'].sum()
t = nav['theta_pnl'].sum()
v = nav['vega_pnl'].sum()
total = d + g + t + v
print(f"  Delta:  {d:>+12,.0f}  ({d/abs(total)*100:+.1f}%)")
print(f"  Gamma:  {g:>+12,.0f}  ({g/abs(total)*100:+.1f}%)")
print(f"  Theta:  {t:>+12,.0f}  ({t/abs(total)*100:+.1f}%)")
print(f"  Vega:   {v:>+12,.0f}  ({v/abs(total)*100:+.1f}%)")
print(f"  合计:   {total:>+12,.0f}")
print(f"\n  注意: Vega是残差项(total-delta-gamma-theta)，包含IV变化+高阶项+离散化误差")

print()
print("=" * 70)
print("5. 每日手数统计（检查是否过大）")
print("=" * 70)
opens = orders[orders['action'].str.startswith('open')]
print(f"  开仓订单数: {len(opens)}")
print(f"  手数均值: {opens['quantity'].mean():.0f}")
print(f"  手数最大: {opens['quantity'].max()}")
print(f"  手数>100的订单: {(opens['quantity']>100).sum()}")
print(f"\n  按品种手数均值:")
for prod, grp in opens.groupby('product'):
    print(f"    {prod}: 均{grp['quantity'].mean():.0f}手, 最大{grp['quantity'].max()}手, {len(grp)}笔")
