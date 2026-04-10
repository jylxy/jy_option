"""
验证 benchmark_wind.db 的数据质量和与回测引擎的兼容性。
在 build_enriched_from_wind.py 运行完后执行。
"""
import sqlite3
import pandas as pd

import os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")

def main():
    conn = sqlite3.connect(DB)

    print("=" * 60)
    print("  数据验证")
    print("=" * 60)

    # 1. 基本统计
    n = conn.execute("SELECT COUNT(*) FROM mart_option_daily_enriched").fetchone()[0]
    n_u = conn.execute("SELECT COUNT(*) FROM mart_option_daily_enriched WHERE pricing_status='usable'").fetchone()[0]
    print(f"\n总行数: {n}")
    print(f"usable: {n_u} ({n_u/n*100:.1f}%)")

    # 2. 日期范围
    r = conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) FROM mart_option_daily_enriched").fetchone()
    print(f"日期: {r[0]} ~ {r[1]}, {r[2]}个交易日")

    # 3. underlying_code分布
    print("\n品种分布（前30）:")
    df = pd.read_sql("""
        SELECT underlying_code, COUNT(*) as n, 
               SUM(CASE WHEN pricing_status='usable' THEN 1 ELSE 0 END) as usable,
               MIN(trade_date) as first_date, MAX(trade_date) as last_date
        FROM mart_option_daily_enriched
        GROUP BY underlying_code
        ORDER BY n DESC
        LIMIT 30
    """, conn)
    for _, r in df.iterrows():
        print(f"  {r['underlying_code']:12s}  {r['n']:>8d}行  usable={r['usable']:>7d}  {r['first_date']}~{r['last_date']}")

    # 4. 检查回测引擎需要的品种是否存在
    print("\n回测引擎品种匹配检查:")
    checks = [
        ("沪深300", "underlying_code = 'HS300'"),
        ("中证1000", "underlying_code = 'CSI1000'"),
        ("上证50", "underlying_code = 'SSE50'"),
        ("豆粕", "underlying_code LIKE 'm2%'"),
        ("铁矿石", "underlying_code LIKE 'i2%'"),
        ("沪铜", "underlying_code LIKE 'cu2%'"),
        ("沪金", "underlying_code LIKE 'au2%'"),
        ("沪银", "underlying_code LIKE 'ag2%'"),
        ("原油", "underlying_code LIKE 'sc2%'"),
        ("螺纹钢", "underlying_code LIKE 'rb2%'"),
        ("沪铝", "underlying_code LIKE 'al2%'"),
        ("纯碱", "underlying_code LIKE 'SA%'"),
        ("白糖", "underlying_code LIKE 'SR%'"),
        ("PTA", "underlying_code LIKE 'TA%'"),
        ("棉花", "underlying_code LIKE 'CF%'"),
    ]
    for name, where in checks:
        r = conn.execute(f"SELECT COUNT(*) FROM mart_option_daily_enriched WHERE {where} AND pricing_status='usable'").fetchone()
        status = "✓" if r[0] > 0 else "✗ MISSING"
        print(f"  {name:8s} ({where:40s}): {r[0]:>6d}行 {status}")

    # 5. 数据质量
    print("\n数据质量:")
    for col in ["spot_close", "implied_vol", "delta", "moneyness", "dte"]:
        r = conn.execute(f"SELECT SUM(CASE WHEN {col} IS NOT NULL AND {col} != 0 THEN 1 ELSE 0 END), COUNT(*) FROM mart_option_daily_enriched").fetchone()
        pct = r[0]/r[1]*100 if r[1] > 0 else 0
        print(f"  {col:15s}: {r[0]:>10d}/{r[1]:>10d} ({pct:.1f}%)")

    # 6. stg_option_daily_bar表
    n_bar = conn.execute("SELECT COUNT(*) FROM stg_option_daily_bar").fetchone()[0]
    print(f"\nstg_option_daily_bar: {n_bar}行")

    conn.close()
    print("\n验证完成")


if __name__ == "__main__":
    main()
