"""
从Wind MySQL数据库构建 mart_option_daily_enriched 兼容的SQLite数据库。

用法：在服务器上运行
    python3 build_enriched_from_wind.py

输出：benchmark_wind.db（与原benchmark.db格式兼容，回测引擎可直接使用）

Wind表映射：
  CHINAOPTIONDESCRIPTION     → 合约属性（行权价、到期日、C/P、乘数、标的）
  CHINAOPTIONEODPRICES       → 期权日线行情（OHLC、成交量、持仓量）
  CHINAOPTIONVALUATION       → Greeks（IV、Delta）
  CCOMMODITYFUTURESEODPRICES → 商品期货日线（标的收盘价）
  CINDEXFUTURESEODPRICES     → 股指期货日线（标的收盘价）
"""
import os
import sys
import time
import re
import sqlite3
import numpy as np
import pandas as pd

# ============================================================
# 配置（根据你的服务器修改）
# ============================================================
MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "user": "lxy",
    "passwd": "",       # ← 填你的MySQL密码
    "db": "wind_test_yangyi",
    "charset": "utf8mb4",
}

OUTPUT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")
START_DATE = "20230103"

# ── MySQL连接 ──
def get_mysql_conn():
    try:
        import pymysql
        return pymysql.connect(**MYSQL_CONFIG)
    except ImportError:
        pass
    try:
        import mysql.connector
        cfg = {k: v for k, v in MYSQL_CONFIG.items()}
        cfg["password"] = cfg.pop("passwd")
        cfg["database"] = cfg.pop("db")
        return mysql.connector.connect(**cfg)
    except ImportError:
        print("ERROR: 需要 pip3 install pymysql 或 mysql-connector-python")
        sys.exit(1)


# ============================================================
# Step 1: 合约属性
# ============================================================
def load_contracts(conn):
    print("[1/5] 合约属性...", end="", flush=True)
    sql = """
    SELECT S_INFO_WINDCODE  as windcode,
           S_INFO_SCCODE    as sc_code,
           S_INFO_CALLPUT   as cp_code,
           S_INFO_STRIKEPRICE as strike,
           S_INFO_MATURITYDATE as maturity,
           S_INFO_COUNIT    as multiplier
    FROM CHINAOPTIONDESCRIPTION
    WHERE S_INFO_STRIKEPRICE IS NOT NULL
      AND S_INFO_MATURITYDATE IS NOT NULL
    """
    df = pd.read_sql(sql, conn)
    df["option_type"] = df["cp_code"].map({708001000: "C", 708002000: "P"})
    df = df[df["option_type"].notna()].copy()

    # 从sc_code推导标的期货Wind代码
    # sc_code格式: MO2505.DCE → 期货 M2505.DCE
    #              CUO2108.SHF → 期货 CU2108.SHF
    #              SAO511.CZC → 期货 SA511.CZC
    #              SCO2607.INE → 期货 SC2607.INE
    #              HO.CFE / IO.CFE / MO.CFE → 股指（特殊处理）
    # 规律：品种名 + "O" + 月份.交易所 → 去掉品种名后的第一个O
    df["fut_windcode"] = df["sc_code"].apply(sc_code_to_fut_code)

    # underlying_code: 回测引擎用的格式（m2505, cu2108, HS300等）
    df["underlying_code"] = df.apply(
        lambda r: derive_underlying_code(r["windcode"], r["sc_code"]), axis=1)

    print(f" {len(df)}个合约")
    return df[["windcode", "option_type", "strike", "maturity",
               "multiplier", "sc_code", "fut_windcode", "underlying_code"]]


def sc_code_to_fut_code(sc):
    """
    期权sc_code → 标的期货Wind代码
    MO2505.DCE → M2505.DCE  (品种名后第一个O是期权标记，去掉)
    CUO2108.SHF → CU2108.SHF
    SAO511.CZC → SA511.CZC
    BRO2512.SHF → BR2512.SHF
    SIO2702.GFE → SI2702.GFE
    HO.CFE → None (股指期权，无对应期货月份合约)
    IO.CFE → None
    MO.CFE → None
    IO2502.DCE → I2502.DCE (大商所铁矿石期权，IO→I)
    """
    if not sc or not isinstance(sc, str):
        return None
    parts = sc.split(".")
    if len(parts) != 2:
        return None
    code, exch = parts

    # 股指期权：品种代码无月份
    if code in ("HO", "IO", "MO"):
        return None

    # 通用规则：找到品种名后面的"O"并去掉
    # 品种名 = 字母部分（去掉末尾的O和数字）
    m = re.match(r'^([A-Za-z]+?)O(\d+)$', code)
    if m:
        return f"{m.group(1)}{m.group(2)}.{exch}"

    return None


def derive_underlying_code(windcode, sc_code):
    """
    推导回测引擎用的underlying_code。

    回测引擎PRODUCT_MAP中的WHERE子句格式：
      "underlying_code LIKE 'm2%'"   → 豆粕
      "underlying_code LIKE 'cu2%'"  → 沪铜
      "underlying_code LIKE 'SA%'"   → 纯碱（郑商所大写）
      "underlying_code = 'HS300'"    → 沪深300
      "underlying_code = 'CSI1000'"  → 中证1000
      "underlying_code = 'SSE50'"    → 上证50

    所以：
      M2505-C-2800.DCE → m2505  (大商所小写)
      CU2108P67000.SHF → cu2108 (上期所小写)
      SA511C1180.CZC   → SA511  (郑商所保持大写)
      IO2503-C-4000.CFE → HS300 (股指期权映射到指数名)
      HO2409-C-2425.CFE → SSE50
      SI2702-P-9500.GFE → si2702 (广期所小写)
    """
    if not windcode:
        return ""

    # 股指期权 → 映射到指数名
    if windcode.startswith("IO") and windcode.endswith(".CFE"):
        return "HS300"
    if windcode.startswith("HO") and windcode.endswith(".CFE"):
        return "SSE50"
    if windcode.startswith("MO") and windcode.endswith(".CFE"):
        return "CSI1000"

    # ETF期权（纯数字开头如 90002276.SZ）
    if windcode[0].isdigit():
        # 暂时跳过ETF期权，回测引擎目前不用
        return windcode.split(".")[0]

    # 从windcode提取品种+月份
    code = windcode.split(".")[0]
    exch = windcode.split(".")[-1] if "." in windcode else ""

    # 大商所: M2505-C-2800 → m2505
    m = re.match(r'^([A-Za-z]+)(\d+)-[CP]-', code)
    if m:
        root, month = m.group(1), m.group(2)
        if exch in ("DCE", "SHF", "INE", "GFE"):
            return root.lower() + month
        return root + month  # 郑商所保持大写

    # 上期所/郑商所: CU2108P67000 → cu2108, SA511C1180 → SA511
    m = re.match(r'^([A-Za-z]+)(\d+)[CP]', code)
    if m:
        root, month = m.group(1), m.group(2)
        if exch in ("SHF", "INE", "GFE"):
            return root.lower() + month
        if exch == "CZC":
            return root.upper() + month  # 郑商所大写
        if exch == "DCE":
            return root.lower() + month
        return root + month

    return code



# ============================================================
# Step 2: 期权日线行情
# ============================================================
def load_option_eod(conn):
    print("[2/5] 期权日线行情...", end="", flush=True)
    sql = f"""
    SELECT S_INFO_WINDCODE as windcode,
           TRADE_DT        as trade_date,
           S_DQ_OPEN       as `open`,
           S_DQ_HIGH       as high,
           S_DQ_LOW        as low,
           S_DQ_CLOSE      as option_close,
           S_DQ_SETTLE     as settle,
           S_DQ_VOLUME     as volume,
           S_DQ_OI         as open_interest
    FROM CHINAOPTIONEODPRICES
    WHERE TRADE_DT >= '{START_DATE}'
    """
    df = pd.read_sql(sql, conn)
    # option_close为空时用settle
    df["option_close"] = df["option_close"].fillna(df["settle"])
    print(f" {len(df)}行")
    return df


# ============================================================
# Step 3: Greeks
# ============================================================
def load_greeks(conn):
    print("[3/5] Greeks(IV/Delta)...", end="", flush=True)
    sql = f"""
    SELECT S_INFO_WINDCODE             as windcode,
           TRADE_DT                    as trade_date,
           W_ANAL_UNDERLYINGIMPLIEDVOL as implied_vol,
           W_ANAL_DELTA                as delta,
           W_ANAL_VEGA                 as vega
    FROM CHINAOPTIONVALUATION
    WHERE TRADE_DT >= '{START_DATE}'
    """
    df = pd.read_sql(sql, conn)
    print(f" {len(df)}行")
    return df


# ============================================================
# Step 4: 标的价格
# ============================================================
def load_underlying(conn):
    print("[4/5] 标的价格...", end="", flush=True)

    # 商品期货（FS_INFO_TYPE='1'是普通合约，排除价差等）
    sql_comm = f"""
    SELECT S_INFO_WINDCODE as fut_code,
           TRADE_DT        as trade_date,
           S_DQ_CLOSE      as close_price,
           S_DQ_SETTLE     as settle_price
    FROM CCOMMODITYFUTURESEODPRICES
    WHERE TRADE_DT >= '{START_DATE}'
    """
    df_comm = pd.read_sql(sql_comm, conn)

    # 股指期货
    sql_idx = f"""
    SELECT S_INFO_WINDCODE as fut_code,
           TRADE_DT        as trade_date,
           S_DQ_CLOSE      as close_price,
           S_DQ_SETTLE     as settle_price
    FROM CINDEXFUTURESEODPRICES
    WHERE TRADE_DT >= '{START_DATE}'
    """
    df_idx = pd.read_sql(sql_idx, conn)

    df = pd.concat([df_comm, df_idx], ignore_index=True)
    df["spot_close"] = df["close_price"].fillna(df["settle_price"])
    df = df[df["spot_close"].notna() & (df["spot_close"] > 0)]

    print(f" {len(df)}行")
    return df[["fut_code", "trade_date", "spot_close"]]



# ============================================================
# Step 5: 拼接
# ============================================================
def build_enriched(contracts, eod, greeks, underlying):
    print("[5/5] 拼接数据...", flush=True)

    # ── A. eod × contracts ──
    print("  A. 行情 × 合约属性...", end="", flush=True)
    merged = eod.merge(
        contracts[["windcode", "option_type", "strike", "maturity",
                   "multiplier", "fut_windcode", "underlying_code"]],
        on="windcode", how="inner"
    )
    print(f" {len(merged)}行")

    # ── B. + Greeks ──
    print("  B. + Greeks...", end="", flush=True)
    merged = merged.merge(
        greeks, on=["windcode", "trade_date"], how="left"
    )
    print(f" {len(merged)}行")

    # ── C. + 标的价格（商品期权：通过fut_windcode关联）──
    print("  C. + 标的价格(商品)...", end="", flush=True)
    spot_df = underlying.rename(columns={"fut_code": "fut_windcode"})
    merged = merged.merge(
        spot_df, on=["fut_windcode", "trade_date"], how="left"
    )
    n_has_spot = merged["spot_close"].notna().sum()
    print(f" 有标的价: {n_has_spot}/{len(merged)}")

    # ── D. 股指期权标的价格（用连续合约 IF.CFE/IH.CFE/IM.CFE）──
    print("  D. + 标的价格(股指)...", end="", flush=True)
    idx_map = {
        "HS300":  ["IF.CFE", "IF00.CFE"],   # 沪深300 → IF
        "SSE50":  ["IH.CFE", "IH00.CFE"],   # 上证50 → IH
        "CSI1000":["IM.CFE", "IM00.CFE"],   # 中证1000 → IM
    }
    for idx_name, fut_codes in idx_map.items():
        mask = (merged["underlying_code"] == idx_name) & merged["spot_close"].isna()
        if mask.sum() == 0:
            continue
        # 取连续合约价格
        idx_prices = underlying[underlying["fut_code"].isin(fut_codes)].copy()
        if idx_prices.empty:
            print(f"\n    WARNING: {idx_name}无连续合约数据({fut_codes})")
            continue
        # 按日期去重（优先用.CFE，其次00.CFE）
        idx_daily = idx_prices.sort_values("fut_code").groupby("trade_date")["spot_close"].first()
        idx_dict = idx_daily.to_dict()
        fill_count = 0
        for i in merged[mask].index:
            td = merged.loc[i, "trade_date"]
            if td in idx_dict:
                merged.loc[i, "spot_close"] = idx_dict[td]
                fill_count += 1
        print(f" {idx_name}: 填充{fill_count}/{mask.sum()}", end="")
    print()

    # ── E. 计算衍生字段 ──
    print("  E. 计算衍生字段...", flush=True)

    # IV: Wind给的是小数（如0.25）还是百分比（如25.0）？
    iv_sample = merged["implied_vol"].dropna()
    if len(iv_sample) > 0:
        iv_median = iv_sample.median()
        if iv_median > 1:
            print(f"    IV中位数={iv_median:.1f}，判断为百分比格式，÷100")
            merged["implied_vol"] = merged["implied_vol"] / 100.0
        else:
            print(f"    IV中位数={iv_median:.4f}，判断为小数格式，不转换")

    # moneyness
    merged["moneyness"] = np.where(
        merged["spot_close"] > 0,
        merged["strike"] / merged["spot_close"],
        np.nan
    )

    # dte（日历天数）
    td = pd.to_datetime(merged["trade_date"], format="%Y%m%d")
    ed = pd.to_datetime(merged["maturity"], format="%Y%m%d")
    merged["dte"] = (ed - td).dt.days

    # option_code = windcode
    merged["option_code"] = merged["windcode"]

    # expiry_date
    merged["expiry_date"] = merged["maturity"]

    # pricing_status
    merged["pricing_status"] = np.where(
        (merged["implied_vol"] > 0) & (merged["implied_vol"] < 5) &
        (merged["option_close"] > 0) & (merged["delta"].notna()),
        "usable", "unusable"
    )

    # 日期格式 YYYYMMDD → YYYY-MM-DD
    merged["trade_date"] = pd.to_datetime(merged["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    merged["expiry_date"] = pd.to_datetime(merged["expiry_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")

    result = merged[[
        "trade_date", "option_code", "underlying_code", "option_type",
        "strike", "expiry_date", "dte", "spot_close", "option_close",
        "implied_vol", "delta", "moneyness", "vega",
        "volume", "open_interest", "pricing_status",
        "open", "high", "low", "multiplier",
    ]].copy()

    n_total = len(result)
    n_usable = (result["pricing_status"] == "usable").sum()
    n_spot = result["spot_close"].notna().sum()
    print(f"\n  汇总: {n_total}行, usable={n_usable}({n_usable/n_total*100:.1f}%), "
          f"有标的价={n_spot}({n_spot/n_total*100:.1f}%)")

    return result



# ============================================================
# Step 6: 写入SQLite
# ============================================================
def write_sqlite(df, db_path):
    print(f"\n写入 {db_path}...", flush=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)

    # 主表
    df.to_sql("mart_option_daily_enriched", conn, index=False)
    conn.execute("CREATE INDEX idx_e_date ON mart_option_daily_enriched(trade_date)")
    conn.execute("CREATE INDEX idx_e_code ON mart_option_daily_enriched(option_code)")
    conn.execute("CREATE INDEX idx_e_underlying ON mart_option_daily_enriched(underlying_code)")
    conn.execute("CREATE INDEX idx_e_dt_ul ON mart_option_daily_enriched(trade_date, underlying_code)")

    # OHLC表（T+1 VWAP用）
    bar = df[["trade_date", "option_code", "open", "high", "low",
              "option_close", "volume"]].copy()
    bar = bar.rename(columns={"option_close": "close"})
    bar.to_sql("stg_option_daily_bar", conn, index=False)
    conn.execute("CREATE INDEX idx_bar_dt_code ON stg_option_daily_bar(trade_date, option_code)")

    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM mart_option_daily_enriched").fetchone()[0]
    n_u = conn.execute("SELECT COUNT(*) FROM mart_option_daily_enriched WHERE pricing_status='usable'").fetchone()[0]
    n_d = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM mart_option_daily_enriched").fetchone()[0]
    conn.close()

    sz = os.path.getsize(db_path) / 1024 / 1024
    print(f"  {n}行, usable={n_u}, {n_d}个交易日, {sz:.0f}MB")


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("  Wind MySQL → benchmark_wind.db")
    print("=" * 60)
    t0 = time.time()

    conn = get_mysql_conn()
    contracts = load_contracts(conn)
    eod       = load_option_eod(conn)
    greeks    = load_greeks(conn)
    underlying= load_underlying(conn)
    conn.close()

    enriched = build_enriched(contracts, eod, greeks, underlying)
    write_sqlite(enriched, OUTPUT_DB)

    print(f"\n总耗时: {time.time()-t0:.0f}秒")
    print(f"输出: {OUTPUT_DB}")
    print(f"\n验证命令:")
    print(f"  python3 -c \"import sqlite3; c=sqlite3.connect('{OUTPUT_DB}'); "
          f"print(c.execute('SELECT COUNT(*) FROM mart_option_daily_enriched WHERE pricing_status=\\'usable\\'').fetchone())\"")


if __name__ == "__main__":
    main()

