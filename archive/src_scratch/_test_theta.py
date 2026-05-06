"""Archived scratch script: 验证py_vollib的theta单位"""
from py_vollib.black_scholes.greeks.analytical import theta, delta, vega, gamma

# 豆粕 M2408-P-3200, spot=3400, strike=3200, dte=35天, iv=0.20
t = 35/365.0
r = 0.02
iv = 0.20

for flag, name in [('p', 'Put'), ('c', 'Call')]:
    th = theta(flag, 3400, 3200, t, r, iv)
    d = delta(flag, 3400, 3200, t, r, iv)
    v = vega(flag, 3400, 3200, t, r, iv)
    g = gamma(flag, 3400, 3200, t, r, iv)
    print(f"--- {name} (spot=3400, K=3200, dte=35, iv=20%) ---")
    print(f"  theta = {th:.6f}")
    print(f"  theta/365 = {th/365:.8f}")
    print(f"  delta = {d:.6f}")
    print(f"  vega = {v:.6f}")
    print(f"  gamma = {g:.8f}")
    print(f"  97手卖权日theta(除365): {-1 * th/365 * 10 * 97:.2f}元")
    print(f"  97手卖权日theta(不除365): {-1 * th * 10 * 97:.2f}元")
    print()

# 更接近ATM的合约
print("--- ATM Put (spot=3400, K=3400, dte=35, iv=20%) ---")
th = theta('p', 3400, 3400, t, r, iv)
print(f"  theta = {th:.6f}")
print(f"  97手卖权日theta(除365): {-1 * th/365 * 10 * 97:.2f}元")
print(f"  97手卖权日theta(不除365): {-1 * th * 10 * 97:.2f}元")
