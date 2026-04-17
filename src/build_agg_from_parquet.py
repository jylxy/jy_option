"""
从IT提供的Parquet分钟数据构建聚合数据库

数据源（/macro/home/lxy/yy_2_lxy_20260415/）：
  OPTION1MINRESULT.parquet  — 64亿行期权分钟数据
  FUTURE1MINRESULT.parquet  — 2.3亿行期货分钟数据
  ETF1MINRESULT.parquet     — 3.6亿行ETF分钟数据
  CONTRACTINFORESULT.parquet — 19.5万行合约属性

输出：
  option_daily_agg.db  — 期权+期货+ETF每日聚合（与本地格式兼容）
  contract_master.db   — 合约属性

注意：所有Parquet列都是string类型（Spark导出），需要转换。
64亿行不能一次读入内存，按row_group分批处理。

用法：
  python src/build_agg_from_parquet.py
  python src/build_agg_from_parquet.py --data-dir /macro/home/lxy/yy_2_lxy_20260415
  python src/build_agg_from_parquet.py --output-dir ./db
"""
import os
import sys
import time
import argparse
import sqlite3
import numpy as np

import pyarrow.parquet as pq
import pandas as pd

# 默认路径
DEFAULT_DATA_DIR = "/macro/home/lxy/yy_2_lxy_20260415"
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db")


def build_contract_master(data_dir, output_dir):
    """构建合约属性数据库"""
    print("=" * 60)
    print("构建 contract_master.db ...")
    path = os.path.join(data_dir, "CONTRACTINFORESULT.parquet")
    df = pd.read_parquet(path)
    print(f"  原始行数: {len(df):,}")

    # 列名映射（与本地contract_master.db兼容）
    col_map = {
        "contract_code": "contract_code",
        "contract_name": "contract_name",
        "exchange_code": "exchange_code",
        "expiry_date": "expiry_date",
        "last_exercise_date": "last_exercise_date",
        "strike_price": "strike",
        "option_type": "option_type",
        "exercise_style": "exercise_type",
        "min_price_tick": "tick_size",
        "price_decimal_places": "price_decimals",
        "contract_multiplier": "multiplier",
        "path": "source_file",
    }
    df = df.rename(columns=col_map)

    # 推断exchange字段
    def _extract_exchange(code):
        if not isinstance(code, str):
            return ""
        code_upper = code.upper()
        if code_upper.startswith("CFFEX.") or code_upper.startswith("IO") or code_upper.startswith("HO") or code_upper.startswith("MO"):
            return "CFFEX"
        if code_upper.startswith("DCE.") or any(code_upper.startswith(p) for p in ["M2", "C2", "I2", "V2", "P2", "JD", "JM", "J2", "PP", "L2", "EG", "EB", "PG", "LH", "CS", "A2", "Y2", "B2", "RR"]):
            return "DCE"
        if code_upper.startswith("SHFE.") or any(code_upper.startswith(p) for p in ["CU", "AL", "ZN", "PB", "NI", "SN", "AU", "AG", "RB", "HC", "BU", "RU", "FU", "SS", "SP", "AO", "BR"]):
            return "SHFE"
        if code_upper.startswith("CZCE.") or any(code_upper.startswith(p) for p in ["SR", "CF", "TA", "MA", "OI", "RM", "FG", "ZC", "SA", "UR", "PF", "PK", "AP", "CJ", "SF", "SM", "WH", "PM", "RI", "RS", "JR", "LR", "CY", "PX", "SH"]):
            return "CZCE"
        if code_upper.startswith("INE.") or code_upper.startswith("SC"):
            return "INE"
        if code_upper.startswith("GFEX.") or any(code_upper.startswith(p) for p in ["SI", "LC"]):
            return "GFEX"
        if code_upper.startswith("SSE.") or any(code_upper.startswith(p) for p in ["1000", "5100", "5880", "5105"]):
            return "SSE"
        if code_upper.startswith("SZSE.") or any(code_upper.startswith(p) for p in ["1599", "1591"]):
            return "SZSE"
        return ""

    df["exchange"] = df["contract_code"].apply(_extract_exchange)

    # 推断option_type标准化（认购/认沽 → C/P）
    if "option_type" in df.columns:
        df["option_type_cn"] = df["option_type"]
        df["option_type"] = df["option_type"].map(
            lambda x: "C" if "认购" in str(x) or "Call" in str(x) or x == "C" else
                      "P" if "认沽" in str(x) or "Put" in str(x) or x == "P" else str(x))

    # 推断underlying_code（从contract_code提取）
    import re
    def _extract_underlying(code):
        if not isinstance(code, str):
            return ""
        # 去掉交易所前缀
        if "." in code:
            code = code.split(".", 1)[1]
        # 提取品种+月份（如 m2409, cu2406, IO2401）
        m = re.match(r"([A-Za-z]+\d{4})", code)
        return m.group(1) if m else ""

    df["underlying_code"] = df["contract_code"].apply(_extract_underlying)

    # 写入SQLite
    db_path = os.path.join(output_dir, "contract_master.db")
    conn = sqlite3.connect(db_path)
    df.to_sql("contracts", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contracts_code ON contracts(contract_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contracts_underlying ON contracts(underlying_code)")
    conn.close()
    print(f"  写入: {db_path} ({len(df):,}行)")
    return df


def _agg_one_batch(batch_df):
    """对一批分钟数据做每日聚合"""
    # 转换数值列
    for col in ["open", "high", "low", "close", "volume"]:
        if col in batch_df.columns:
            batch_df[col] = pd.to_numeric(batch_df[col], errors="coerce")

    if "open_oi" in batch_df.columns:
        batch_df["open_oi"] = pd.to_numeric(batch_df["open_oi"], errors="coerce")
    if "close_oi" in batch_df.columns:
        batch_df["close_oi"] = pd.to_numeric(batch_df["close_oi"], errors="coerce")
    if "open_interest" in batch_df.columns:
        batch_df["open_interest"] = pd.to_numeric(batch_df["open_interest"], errors="coerce")
    if "money" in batch_df.columns:
        batch_df["money"] = pd.to_numeric(batch_df["money"], errors="coerce")
    if "amount" in batch_df.columns:
        batch_df["amount"] = pd.to_numeric(batch_df["amount"], errors="coerce")

    # 提取日期
    batch_df["trade_date"] = batch_df["datetime"].str[:10]

    # 过滤无效数据
    valid = batch_df[(batch_df["close"] > 0) & (batch_df["volume"] > 0)].copy()
    if valid.empty:
        return pd.DataFrame()

    # 提取合约代码（code列）
    code_col = "code" if "code" in valid.columns else "contract_code"

    # 计算VWAP的辅助列
    valid["typical"] = (valid["high"] + valid["low"] + valid["close"]) / 3
    valid["tp_x_vol"] = valid["typical"] * valid["volume"]

    # 按(trade_date, code)聚合
    agg = valid.groupby(["trade_date", code_col]).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        vwap_num=("tp_x_vol", "sum"),
        vwap_den=("volume", "sum"),
        volume=("volume", "sum"),
        n_bars=("close", "count"),
        sum_close=("close", "sum"),
    ).reset_index()

    agg["vwap"] = agg["vwap_num"] / agg["vwap_den"].replace(0, np.nan)
    agg["twap"] = agg["sum_close"] / agg["n_bars"].replace(0, np.nan)

    # 收盘持仓量（取最后一条）
    if "close_oi" in valid.columns:
        last_oi = valid.groupby(["trade_date", code_col])["close_oi"].last().reset_index()
        agg = agg.merge(last_oi, on=["trade_date", code_col], how="left")
    elif "open_interest" in valid.columns:
        last_oi = valid.groupby(["trade_date", code_col])["open_interest"].last().reset_index()
        last_oi = last_oi.rename(columns={"open_interest": "close_oi"})
        agg = agg.merge(last_oi, on=["trade_date", code_col], how="left")

    # 买卖价差代理（日内high-low均值）
    spread = valid.groupby(["trade_date", code_col]).apply(
        lambda g: (g["high"] - g["low"]).mean(), include_groups=False
    ).reset_index(name="spread_proxy")
    agg = agg.merge(spread, on=["trade_date", code_col], how="left")

    # 分时段VWAP（前5/10/15/30分钟）
    # 按datetime排序后取前N条
    for window, col_name in [(5, "vwap_5"), (10, "vwap_10"), (15, "vwap_15"), (30, "vwap_30")]:
        w_agg = valid.sort_values("datetime").groupby(["trade_date", code_col]).head(window)
        w_vwap = w_agg.groupby(["trade_date", code_col]).apply(
            lambda g: (g["typical"] * g["volume"]).sum() / g["volume"].sum() if g["volume"].sum() > 0 else np.nan,
            include_groups=False
        ).reset_index(name=col_name)
        agg = agg.merge(w_vwap, on=["trade_date", code_col], how="left")

    # 整理输出列
    agg = agg.rename(columns={code_col: "contract_code"})
    keep_cols = ["trade_date", "contract_code", "open", "high", "low", "close",
                 "vwap", "twap", "vwap_5", "vwap_10", "vwap_15", "vwap_30",
                 "volume", "spread_proxy"]
    if "close_oi" in agg.columns:
        keep_cols.append("close_oi")

    return agg[[c for c in keep_cols if c in agg.columns]]


def build_option_agg(data_dir, output_dir, batch_size=50_000_000):
    """从期权分钟Parquet构建每日聚合"""
    print("=" * 60)
    print("构建期权每日聚合 ...")
    path = os.path.join(data_dir, "OPTION1MINRESULT.parquet")
    pf = pq.ParquetFile(path)
    total_rows = pf.metadata.num_rows
    n_groups = pf.metadata.num_row_groups
    print(f"  总行数: {total_rows:,}, row_groups: {n_groups}")

    db_path = os.path.join(output_dir, "option_daily_agg.db")
    conn = sqlite3.connect(db_path)

    total_agg = 0
    t0 = time.time()

    for i in range(n_groups):
        batch = pf.read_row_group(i).to_pandas()
        print(f"  row_group {i+1}/{n_groups}: {len(batch):,}行 ...", end="", flush=True)

        agg = _agg_one_batch(batch)
        if not agg.empty:
            # 推断exchange和product
            def _extract_exchange_from_code(code):
                if not isinstance(code, str):
                    return ""
                if "." in code:
                    return code.split(".")[0]
                return ""

            def _extract_product_from_code(code):
                if not isinstance(code, str):
                    return ""
                if "." in code:
                    code = code.split(".", 1)[1]
                import re
                m = re.match(r"([A-Za-z]+)", code)
                return m.group(1) if m else ""

            agg["exchange"] = agg["contract_code"].apply(_extract_exchange_from_code)
            agg["product"] = agg["contract_code"].apply(_extract_product_from_code)

            agg.to_sql("option_daily_agg", conn, if_exists="append", index=False)
            total_agg += len(agg)
            print(f" → {len(agg):,}行聚合")
        else:
            print(" → 跳过（无有效数据）")

        del batch, agg

    # 创建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_agg_date ON option_daily_agg(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_agg_code ON option_daily_agg(contract_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opt_agg_exchange ON option_daily_agg(exchange)")
    conn.close()

    elapsed = time.time() - t0
    print(f"  完成: {total_agg:,}行, {elapsed:.0f}秒")
    print(f"  输出: {db_path}")


def build_futures_agg(data_dir, output_dir):
    """从期货分钟Parquet构建每日聚合"""
    print("=" * 60)
    print("构建期货每日聚合 ...")
    path = os.path.join(data_dir, "FUTURE1MINRESULT.parquet")
    pf = pq.ParquetFile(path)
    total_rows = pf.metadata.num_rows
    n_groups = pf.metadata.num_row_groups
    print(f"  总行数: {total_rows:,}, row_groups: {n_groups}")

    db_path = os.path.join(output_dir, "option_daily_agg.db")
    conn = sqlite3.connect(db_path)

    total_agg = 0
    t0 = time.time()

    for i in range(n_groups):
        batch = pf.read_row_group(i).to_pandas()
        print(f"  row_group {i+1}/{n_groups}: {len(batch):,}行 ...", end="", flush=True)

        agg = _agg_one_batch(batch)
        if not agg.empty:
            agg.to_sql("futures_daily_agg", conn, if_exists="append", index=False)
            total_agg += len(agg)
            print(f" → {len(agg):,}行聚合")
        else:
            print(" → 跳过")

        del batch, agg

    conn.execute("CREATE INDEX IF NOT EXISTS idx_fut_agg_date ON futures_daily_agg(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fut_agg_code ON futures_daily_agg(contract_code)")
    conn.close()

    elapsed = time.time() - t0
    print(f"  完成: {total_agg:,}行, {elapsed:.0f}秒")


def build_etf_agg(data_dir, output_dir):
    """从ETF分钟Parquet构建每日聚合"""
    print("=" * 60)
    print("构建ETF每日聚合 ...")
    path = os.path.join(data_dir, "ETF1MINRESULT.parquet")
    pf = pq.ParquetFile(path)
    total_rows = pf.metadata.num_rows
    n_groups = pf.metadata.num_row_groups
    print(f"  总行数: {total_rows:,}, row_groups: {n_groups}")

    db_path = os.path.join(output_dir, "option_daily_agg.db")
    conn = sqlite3.connect(db_path)

    total_agg = 0
    t0 = time.time()

    for i in range(n_groups):
        batch = pf.read_row_group(i).to_pandas()
        print(f"  row_group {i+1}/{n_groups}: {len(batch):,}行 ...", end="", flush=True)

        # ETF没有open_oi/close_oi列，用amount代替
        agg = _agg_one_batch(batch)
        if not agg.empty:
            # ETF的code就是ETF代码
            if "contract_code" in agg.columns:
                agg = agg.rename(columns={"contract_code": "etf_code"})
            agg.to_sql("etf_daily_agg", conn, if_exists="append", index=False)
            total_agg += len(agg)
            print(f" → {len(agg):,}行聚合")
        else:
            print(" → 跳过")

        del batch, agg

    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_agg_date ON etf_daily_agg(trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_agg_code ON etf_daily_agg(etf_code)")
    conn.close()

    elapsed = time.time() - t0
    print(f"  完成: {total_agg:,}行, {elapsed:.0f}秒")


def main():
    parser = argparse.ArgumentParser(description="从Parquet分钟数据构建聚合数据库")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Parquet文件目录")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出数据库目录")
    parser.add_argument("--skip-option", action="store_true", help="跳过期权聚合（最耗时）")
    parser.add_argument("--skip-futures", action="store_true", help="跳过期货聚合")
    parser.add_argument("--skip-etf", action="store_true", help="跳过ETF聚合")
    parser.add_argument("--skip-contract", action="store_true", help="跳过合约属性")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    print(f"数据目录: {args.data_dir}")
    print(f"输出目录: {args.output_dir}")

    t_total = time.time()

    if not args.skip_contract:
        build_contract_master(args.data_dir, args.output_dir)

    if not args.skip_futures:
        build_futures_agg(args.data_dir, args.output_dir)

    if not args.skip_etf:
        build_etf_agg(args.data_dir, args.output_dir)

    if not args.skip_option:
        build_option_agg(args.data_dir, args.output_dir)

    print("=" * 60)
    print(f"全部完成，总耗时: {time.time() - t_total:.0f}秒")

    # 显示输出文件大小
    for f in ["option_daily_agg.db", "contract_master.db"]:
        fp = os.path.join(args.output_dir, f)
        if os.path.exists(fp):
            size_mb = os.path.getsize(fp) / 1024 / 1024
            print(f"  {f}: {size_mb:.0f}MB")


if __name__ == "__main__":
    main()
