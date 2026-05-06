"""Archived scratch script for LIKE-pattern checks."""
from toolkit.selector import select_bars_sql

# 测试LIKE条件
for pattern in ["M%.DCE", "CU%.SHF", "CU%.SHF"]:
    df = select_bars_sql(f"SELECT COUNT(*) as cnt FROM option_hf_1min_non_ror WHERE date = '2024-01-02' AND ths_code LIKE '{pattern}'")
    print(f"  {pattern}: {df}")

# 看看实际的ths_code样例
df = select_bars_sql("SELECT DISTINCT ths_code FROM option_hf_1min_non_ror WHERE date = '2024-01-02' LIMIT 20")
print(f"\n样例ths_code:\n{df}")

# 看看option_basic_info中M和CU的ths_code格式
df2 = select_bars_sql("SELECT ths_code FROM option_basic_info WHERE ths_code LIKE 'M%' AND ths_code LIKE '%.DCE' LIMIT 5")
print(f"\noption_basic_info M%.DCE:\n{df2}")

df3 = select_bars_sql("SELECT ths_code FROM option_basic_info WHERE ths_code LIKE 'CU%' AND ths_code LIKE '%.SHF' LIMIT 5")
print(f"\noption_basic_info CU%.SHF:\n{df3}")
