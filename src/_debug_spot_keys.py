"""
调试脚本：打印 _fut_idx 的 key 格式 和 Position 的 underlying_code 格式
在服务器上运行：python _debug_spot_keys.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parquet_loader import ParquetDayLoader, ContractMaster

DATA_DIR = os.environ.get("PARQUET_DATA_DIR", "/macro/home/lxy/yy_2_lxy_20260415")
loader = ParquetDayLoader(DATA_DIR)

# 加载一天的数据
day = loader.load_day("2026-01-06")

print("=== _fut_idx sample keys (前20个) ===")
fut_keys = list(day._fut_idx.keys())[:20]
for k in fut_keys:
    print(f"  {k}")

print(f"\n_fut_idx 总数: {len(day._fut_idx)}")

# 提取所有唯一的期货代码
fut_codes = set(k[1] for k in day._fut_idx.keys())
print(f"\n=== 唯一期货代码 (前30个) ===")
for c in sorted(fut_codes)[:30]:
    print(f"  {c}")

print(f"\n唯一期货代码总数: {len(fut_codes)}")

print("\n=== _etf_idx sample keys (前10个) ===")
etf_keys = list(day._etf_idx.keys())[:10]
for k in etf_keys:
    print(f"  {k}")

# 看 aggregate_daily 输出的 underlying_code
cm = loader.contract_master
daily_df = day.aggregate_daily(cm)
if not daily_df.empty and "underlying_code" in daily_df.columns:
    uc_samples = daily_df["underlying_code"].dropna().unique()[:20]
    print(f"\n=== aggregate_daily underlying_code (前20个) ===")
    for uc in sorted(uc_samples):
        print(f"  {uc}")
    print(f"\n唯一 underlying_code 总数: {daily_df['underlying_code'].nunique()}")

# 测试 _resolve_spot_code
print("\n=== _resolve_spot_code 测试 ===")
if not daily_df.empty and "underlying_code" in daily_df.columns:
    test_ucs = daily_df["underlying_code"].dropna().unique()[:10]
    for uc in test_ucs:
        resolved = day._resolve_spot_code(str(uc))
        print(f"  {uc} -> {resolved}")

day.release()
print("\n完成")
