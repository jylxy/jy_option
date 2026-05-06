"""Archived scratch script for ad hoc vega checks."""
import pandas as pd
df = pd.read_csv('/macro/home/lxy/jy_option/output/nav_v6_20prod_tp50.csv')
print("=== Cash Vega % (每日) ===")
for _, r in df.iterrows():
    vega_pct = r['cash_vega'] * 100
    delta_pct = r['cash_delta'] * 100
    flag_v = " *** VEGA>1%" if abs(r['cash_vega']) > 0.01 else ""
    flag_d = " *** DELTA>10%" if abs(r['cash_delta']) > 0.10 else ""
    print(f"  {r['date']}  Vega={vega_pct:+.3f}%  Delta={delta_pct:+.2f}%  持仓={int(r['n_positions'])}{flag_v}{flag_d}")

print()
print(f"Vega绝对值最大: {df['cash_vega'].abs().max()*100:.3f}%")
print(f"Vega绝对值均值: {df['cash_vega'].abs().mean()*100:.3f}%")
print(f"Delta绝对值最大: {df['cash_delta'].abs().max()*100:.2f}%")
print(f"Vega>0.8%的天数: {(df['cash_vega'].abs() > 0.008).sum()}")
print(f"Vega>1.0%的天数: {(df['cash_vega'].abs() > 0.01).sum()}")
