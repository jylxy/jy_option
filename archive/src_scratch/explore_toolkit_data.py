"""Archived scratch script: 探索 toolkit 数据源 — 第二轮：关键细节."""
from toolkit.selector import select_bars_sql
import pandas as pd

# 1. 商品期权的ths_code格式
print("=" * 60)
print("1. 各交易所期权ths_code样例")
print("=" * 60)
for suffix in ['DCE', 'SHF', 'CZC', 'INE', 'GFE', 'CFE', 'SH', 'SZ']:
    df = select_bars_sql(f"""
        SELECT ths_code, option_short_name, contract_type, 
               strike_price, maturity_date, contract_multiplier
        FROM option_basic_info 
        WHERE ths_code LIKE '%.{suffix}'
        LIMIT 3
    """)
    if df is not None and not df.empty:
        print(f"\n--- {suffix} ({len(df)}条) ---")
        print(df.to_string())

# 2. future_basic_info 结构
print()
print("=" * 60)
print("2. future_basic_info 结构")
print("=" * 60)
df = select_bars_sql("SELECT * FROM future_basic_info LIMIT 5")
if df is not None:
    print(f"列名: {df.columns.tolist()}")
    print(df.to_string())

# 3. 分钟数据日期范围（date字段是整数，需要转换）
print()
print("=" * 60)
print("3. 分钟数据日期范围和数量")
print("=" * 60)
rng = select_bars_sql("""
    SELECT MIN(date) as min_date, MAX(date) as max_date,
           COUNT(DISTINCT date) as n_dates
    FROM option_hf_1min_non_ror
""")
print(rng)

# 4. 看看有没有期货日线数据表
print()
print("=" * 60)
print("4. 查找可用的期货数据表")
print("=" * 60)
for table in ['future_daily_quotes_non_ror', 'future_daily_quotes',
              'future_hf_daily_non_ror', 'index_daily_quotes_non_ror',
              'etf_daily_quotes_non_ror', 'stock_daily_quotes_non_ror']:
    try:
        r = select_bars_sql(f"SELECT COUNT(*) as cnt FROM {table}")
        if r is not None:
            print(f"  {table}: {r['cnt'].iloc[0]:,} 行")
    except Exception as e:
        err = str(e)[:80]
        if 'UNKNOWN_TABLE' not in err:
            print(f"  {table}: {err}")

# 5. 看看option_basic_info有没有标的代码字段
print()
print("=" * 60)
print("5. option_basic_info 是否有标的代码（从short_name解析）")
print("=" * 60)
# 看看豆粕期权的ths_code格式
df = select_bars_sql("""
    SELECT ths_code, option_short_name, strike_price, maturity_date, contract_multiplier
    FROM option_basic_info 
    WHERE ths_code LIKE 'm%DCE'
    LIMIT 5
""")
if df is not None and not df.empty:
    print("豆粕期权:")
    print(df.to_string())
else:
    print("豆粕: 无结果，试试其他格式")
    df = select_bars_sql("""
        SELECT ths_code, option_short_name, strike_price, maturity_date
        FROM option_basic_info 
        WHERE option_short_name LIKE '%豆粕%'
        LIMIT 5
    """)
    if df is not None and not df.empty:
        print(df.to_string())

# 6. 分钟数据中商品期权的样例
print()
print("=" * 60)
print("6. 分钟数据中豆粕期权样例")
print("=" * 60)
df = select_bars_sql("""
    SELECT date, time, ths_code, open, high, low, close, volume, open_interest
    FROM option_hf_1min_non_ror 
    WHERE ths_code LIKE 'M%DCE' AND date = '2024-06-03'
    LIMIT 10
""")
if df is not None and not df.empty:
    print(df.to_string())
else:
    print("无结果")
    # 试试不同日期格式
    df = select_bars_sql("""
        SELECT date, time, ths_code, open, high, low, close, volume, open_interest
        FROM option_hf_1min_non_ror 
        WHERE ths_code LIKE 'M%DCE'
        LIMIT 5
    """)
    if df is not None and not df.empty:
        print(df.to_string())
