"""
数据加载器 v2 —— 完全基于聚合数据库

从 option_daily_agg.db + contract_master.db 拼接出与 benchmark.db 兼容的 DataFrame，
包含自算的 IV/Delta/Vega/DTE/Moneyness。

数据拼图：
  - OHLCV + VWAP + spread → option_daily_agg (期权行情)
  - strike/expiry/option_type/multiplier → contract_master (合约属性)
  - 标的价格 → futures_daily_agg (商品) / option_daily_agg中的ETF标的
  - IV/Delta/Gamma/Vega/Theta → 自算（BSM）
  - DTE/moneyness → 简单计算

输出 DataFrame 列（与 load_product_data 兼容）：
  trade_date, option_code, option_type, strike,
  delta, implied_vol, moneyness, dte,
  spot_close, option_close, expiry_date, vega,
  volume, open_interest
"""
import os
import sys
import time
import sqlite3
import re

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from option_calc import calc_iv_batch, calc_greeks_batch

# ── 路径配置 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGG_DB_DIR = os.path.join(BASE_DIR, "..", "..", "期权相关分钟数据", "db")
CONTRACT_ATTR_DIR = os.path.join(BASE_DIR, "..", "..", "期权相关分钟数据", "期权合约属性")

AGG_DB_PATH = os.path.join(AGG_DB_DIR, "option_daily_agg.db")
CONTRACT_DB_PATH = os.path.join(AGG_DB_DIR, "contract_master.db")


# ── 合约代码 → 品种根码 提取 ──
def extract_product_root(contract_code):
    """
    从合约代码提取品种根码。
    
    示例：
      'DCE.m2501-C-2800' → 'm'
      'CZCE.SA501-P-1200' → 'SA'
      'CFFEX.IO2501-C-4000' → 'IO'
      'SSE.10002117' → None（ETF期权需要特殊处理）
    """
    # 去掉交易所前缀
    if '.' in contract_code:
        code = contract_code.split('.', 1)[1]
    else:
        code = contract_code
    
    # 匹配字母开头的根码
    m = re.match(r'^([a-zA-Z]+)', code)
    if m:
        return m.group(1)
    return None


def _load_contract_master(db_path=None):
    """
    加载合约属性表。优先从 contract_master.db（表名 contracts），fallback 从 CSV 文件构建。
    
    Returns:
        DataFrame: contract_code, exchange, strike, expiry_date, option_type,
                   exercise_type, multiplier, underlying_code
    """
    db_path = db_path or CONTRACT_DB_PATH
    
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        try:
            # 表名是 contracts（不是 contract_master）
            df = pd.read_sql("""
                SELECT contract_code, exchange_code as exchange, 
                       CAST(strike AS REAL) as strike,
                       expiry_date, option_type, exercise_type,
                       CAST(multiplier AS REAL) as multiplier,
                       underlying_code
                FROM contracts
            """, conn)
            conn.close()
            print(f"  合约属性: {len(df):,}条 (from contract_master.db)")
            return df
        except Exception as e:
            conn.close()
            print(f"  contract_master.db 读取失败: {e}，尝试CSV...")
    
    # fallback: 从CSV文件构建
    return _load_contract_attrs_from_csv()


def _load_contract_attrs_from_csv():
    """从 期权合约属性/ 目录下的CSV文件构建合约属性表"""
    all_rows = []
    
    if not os.path.exists(CONTRACT_ATTR_DIR):
        print(f"  警告: 合约属性目录不存在: {CONTRACT_ATTR_DIR}")
        return pd.DataFrame()
    
    for exch_dir in os.listdir(CONTRACT_ATTR_DIR):
        exch_path = os.path.join(CONTRACT_ATTR_DIR, exch_dir)
        if not os.path.isdir(exch_path):
            continue
        
        for csv_file in os.listdir(exch_path):
            if not csv_file.endswith('.csv') or csv_file.startswith('.'):
                continue
            
            csv_path = os.path.join(exch_path, csv_file)
            try:
                df = pd.read_csv(csv_path)
                if df.empty:
                    continue
                
                # 标准化列名（中文→英文）
                rename_map = {
                    "合约代码": "contract_code",
                    "交易所代码": "exchange",
                    "行权价": "strike",
                    "到期日": "expiry_date",
                    "期权类型": "option_type",
                    "行权方式": "exercise_type",
                    "合约乘数": "multiplier",
                }
                df = df.rename(columns=rename_map)
                
                # 期权类型标准化
                if "option_type" in df.columns:
                    df["option_type"] = df["option_type"].map(
                        {"看涨": "C", "看跌": "P", "C": "C", "P": "P"}
                    )
                
                # 品种名（从文件名推断）
                product_name = csv_file.replace('.csv', '')
                df["product_file"] = product_name
                
                for col in rename_map.values():
                    if col not in df.columns:
                        df[col] = None
                
                all_rows.append(df[list(rename_map.values()) + ["product_file"]])
            except Exception as e:
                print(f"  警告: 读取 {csv_path} 失败: {e}")
    
    if not all_rows:
        return pd.DataFrame()
    
    result = pd.concat(all_rows, ignore_index=True)
    print(f"  合约属性: {len(result):,}条 (from CSV files)")
    return result


# ── 标的价格映射 ──

# 品种根码 → 期货品种映射（用于从 futures_daily_agg 查标的价格）
# 期货品种名在聚合数据库中是大写的
# 期权品种 → 对应期货品种（大写）
UNDERLYING_PREFIX = {
    # CFFEX 股指期权 → 股指期货
    'IO': 'IF',    # 沪深300期权 → IF
    'HO': 'IH',    # 上证50期权 → IH  
    'MO': 'IM',    # 中证1000期权 → IM
}

# ETF期权 → ETF代码映射
ETF_UNDERLYING = {
    '华泰柏瑞沪深300ETF': '510300',
    '华夏上证50ETF': '510050',
    '南方中证500ETF': '510500',
    '华夏上证科创板50ETF': '588000',
    '易方达上证科创板50ETF': '588080',
    '嘉实沪深300ETF': '159919',
    '嘉实中证500ETF': '159922',
    '易方达创业板ETF': '159915',
    '易方达深证100ETF': '159901',
}

# 品种根码 → underlying_code 映射（用于 PRODUCT_MAP 兼容）
FINANCIAL_EXCHANGES = {"CFFEX", "SSE", "SZSE"}

ROOT_TO_UNDERLYING = {
    'IO': 'HS300', 'HO': 'SSE50', 'MO': 'CSI1000',
    # 商品期权：根码就是underlying_code的前缀
}


def _normalize_underlying_code(underlying_code):
    """
    将期权合约属性里的 underlying_code 规范成期货合约月份代码。

    示例：
      m2407 -> M2407
      IO2406 -> IF2406
      HO2406 -> IH2406
      10002527 -> None（ETF期权，需走现货逻辑）
    """
    if pd.isna(underlying_code):
        return None

    code = str(underlying_code).strip()
    if not code or code.isdigit():
        return None

    m = re.match(r'^([A-Za-z]+)(.+)$', code)
    if not m:
        return None

    root = m.group(1).upper()
    suffix = m.group(2)
    fut_root = UNDERLYING_PREFIX.get(root, root)
    return f"{fut_root}{suffix}".upper()


def _infer_option_price_proxy(exchange, product):
    """Choose the price proxy that best matches the legacy benchmark."""
    exchange_text = str(exchange).upper() if pd.notna(exchange) else ""
    if exchange_text in FINANCIAL_EXCHANGES:
        return "close"
    product_text = str(product).strip()
    if product_text in ETF_UNDERLYING:
        return "close"
    return "twap"


def _build_option_close_series(df):
    """Commodity options align better with benchmark when priced on TWAP."""
    proxy = df.apply(
        lambda row: _infer_option_price_proxy(row.get("exchange"), row.get("product")),
        axis=1,
    )
    option_close = df["close"].copy()
    twap_proxy = df["twap"].where(df["twap"] > 0, df["vwap"])
    option_close = option_close.where(proxy == "close", twap_proxy)
    option_close = option_close.where(option_close > 0, df["close"])
    option_close = option_close.where(option_close > 0, df["vwap"])
    return proxy, option_close


def _load_futures_spot_map(agg_conn, underlying_codes):
    """
    读取指定 underlying_code 对应的同月期货收盘价。

    Returns:
        dict[(trade_date, normalized_underlying)] -> close
    """
    normalized_codes = sorted({
        code for code in (_normalize_underlying_code(x) for x in underlying_codes)
        if code
    })
    if not normalized_codes:
        return {}

    placeholders = ",".join("?" for _ in normalized_codes)
    query = f"""
        SELECT trade_date,
               UPPER(SUBSTR(contract_code, INSTR(contract_code, '.') + 1)) AS fut_code,
               close
        FROM futures_daily_agg
        WHERE close > 0
          AND UPPER(SUBSTR(contract_code, INSTR(contract_code, '.') + 1)) IN ({placeholders})
    """

    fut_df = pd.read_sql(query, agg_conn, params=normalized_codes)
    if fut_df.empty:
        return {}

    return {
        (row["trade_date"], row["fut_code"]): row["close"]
        for _, row in fut_df.iterrows()
    }


def _build_spot_index(agg_conn):
    """
    构建主力/ETF 标的价格索引：(trade_date, product_upper) → spot_close
    
    注意：期货品种名在聚合数据库中是大写的（M, CU, AU），
    期权品种名可能是小写（m, cu）或大写（SA, CF）。
    索引统一用大写 key。
    
    商品期权/股指期权：兜底时从 futures_daily_agg 取主力合约收盘价
    ETF期权：从 etf_daily_agg 读取现货收盘价
    """
    spot_idx = {}
    
    # 1. 从期货聚合表加载
    try:
        fut_df = pd.read_sql("""
            SELECT trade_date, contract_code, product, close, volume
            FROM futures_daily_agg
            WHERE close > 0 AND volume > 0
        """, agg_conn)
    except Exception:
        fut_df = pd.DataFrame()
    
    if not fut_df.empty:
        # 按(日期, 品种)取成交量最大的合约作为主力合约
        fut_df["volume"] = fut_df["volume"].fillna(0)
        idx = fut_df.groupby(["trade_date", "product"])["volume"].idxmax()
        main_contracts = fut_df.loc[idx]
        
        for _, row in main_contracts.iterrows():
            td = row["trade_date"]
            prod = row["product"].upper()  # 统一大写
            spot_idx[(td, prod)] = row["close"]
    
    return spot_idx


def _match_spot_for_option(option_product, trade_date, spot_idx):
    """
    为期权合约匹配兜底标的价格。
    
    Args:
        option_product: 期权品种根码（如 'm', 'IO', 'SA'）或ETF名（如'华泰柏瑞沪深300ETF'）
        trade_date: 交易日期字符串
        spot_idx: 标的价格索引（key 统一大写）
    
    Returns:
        float or None
    """
    prod_upper = option_product.upper()
    
    # 股指期权：IO→IF, HO→IH, MO→IM
    fut_product = UNDERLYING_PREFIX.get(prod_upper, prod_upper)
    
    # 统一大写查找
    key = (trade_date, fut_product)
    if key in spot_idx:
        return spot_idx[key]
    
    # ETF期权：中文名直接匹配（_build_etf_spot_index 存的是大写中文名）
    # 中文大写和原文相同，所以也尝试原始名
    key2 = (trade_date, option_product)
    if key2 in spot_idx:
        return spot_idx[key2]
    
    return None


class DataLoaderV2:
    """
    基于聚合数据库的数据加载器。
    
    替代原来的 load_product_data(conn, where_clause) 模式，
    输出格式完全兼容，可直接插入 minute_backtest.py。
    """
    
    def __init__(self, agg_db_path=None, contract_db_path=None):
        self.agg_db_path = agg_db_path or AGG_DB_PATH
        self.contract_db_path = contract_db_path or CONTRACT_DB_PATH
        
        self._contract_master = None  # 延迟加载
        self._spot_idx = None
        self._agg_conn = None
    
    def _ensure_loaded(self):
        """确保基础数据已加载"""
        if self._contract_master is None:
            print("加载合约属性...", end="", flush=True)
            self._contract_master = _load_contract_master(self.contract_db_path)
            print(f" 完成")
        
        if self._agg_conn is None:
            self._agg_conn = sqlite3.connect(self.agg_db_path)
        
        if self._spot_idx is None:
            print("构建标的价格索引...", end="", flush=True)
            t0 = time.time()
            self._spot_idx = _build_spot_index(self._agg_conn)
            # 补充ETF标的价格（从期权数据推算）
            self._build_etf_spot_index()
            print(f" {len(self._spot_idx):,}条, {time.time()-t0:.0f}秒")
    
    def _build_etf_spot_index(self):
        """
        从 etf_daily_agg 表读取真实ETF收盘价作为标的价格。
        """
        # ETF期权品种名 → ETF代码
        etf_name_to_code = {
            '华泰柏瑞沪深300ETF': '510300',
            '华夏上证50ETF': '510050',
            '南方中证500ETF': '510500',
            '华夏上证科创板50ETF': '588000',
            '易方达上证科创板50ETF': '588080',
            '嘉实沪深300ETF': '159919',
            '嘉实中证500ETF': '159922',
            '易方达创业板ETF': '159915',
            '易方达深证100ETF': '159901',
        }
        
        try:
            etf_df = pd.read_sql("""
                SELECT trade_date, etf_code, etf_name, close
                FROM etf_daily_agg
                WHERE close > 0
            """, self._agg_conn)
        except Exception:
            # 表不存在，跳过
            return
        
        if etf_df.empty:
            return
        
        # 建立 etf_code → etf_name 的反向映射
        code_to_name = {v: k for k, v in etf_name_to_code.items()}
        
        for _, row in etf_df.iterrows():
            etf_name = code_to_name.get(row["etf_code"], row.get("etf_name"))
            if etf_name:
                self._spot_idx[(row["trade_date"], etf_name)] = row["close"]
    
    def load_product(self, product_root, product_name=None, start_date=None):
        """
        加载单个品种的完整数据（行情+属性+标的价格+IV+Greeks）。
        
        Args:
            product_root: 品种根码（如 'm', 'IO', 'SA', '510300'）
            product_name: 品种中文名（可选，用于日志）
            start_date: 起始日期（可选）
        
        Returns:
            DataFrame: 与 load_product_data 输出格式兼容
        """
        self._ensure_loaded()
        
        label = product_name or product_root
        
        # 1. 从聚合数据库加载期权行情
        where_parts = [f"product = '{product_root}'"]
        if start_date:
            where_parts.append(f"trade_date >= '{start_date}'")
        where_sql = " AND ".join(where_parts)
        
        try:
            odf = pd.read_sql(f"""
                SELECT trade_date, contract_code, exchange, product,
                       open, high, low, close, vwap, twap,
                       vwap_5, vwap_10, vwap_15, vwap_30,
                       volume, close_oi, spread_proxy
                FROM option_daily_agg
                WHERE {where_sql}
                  AND close > 0
                ORDER BY trade_date, contract_code
            """, self._agg_conn)
        except Exception as e:
            print(f"  {label}: 查询失败 - {e}")
            return pd.DataFrame()
        
        if odf.empty:
            return pd.DataFrame()
        
        # 2. 关联合约属性（strike, expiry, option_type, multiplier, underlying_code）
        cm = self._contract_master
        if cm is not None and not cm.empty:
            # 确保 strike 是数值类型
            cm_subset = cm[["contract_code", "strike", "expiry_date", "option_type",
                            "exercise_type", "multiplier", "underlying_code"]].copy()
            cm_subset["strike"] = pd.to_numeric(cm_subset["strike"], errors="coerce")
            cm_subset["multiplier"] = pd.to_numeric(cm_subset["multiplier"], errors="coerce")
            odf = odf.merge(cm_subset, on="contract_code", how="inner")
        
        if odf.empty or "strike" not in odf.columns:
            return pd.DataFrame()
        
        # 3. 先按 underlying_code 匹配同月期货；找不到时再回退到主力/ETF 逻辑
        odf["normalized_underlying"] = odf["underlying_code"].map(_normalize_underlying_code)
        futures_spot_map = _load_futures_spot_map(
            self._agg_conn, odf["underlying_code"].dropna().unique().tolist()
        )
        odf["spot_key"] = list(zip(odf["trade_date"], odf["normalized_underlying"]))
        odf["spot_close"] = odf["spot_key"].map(futures_spot_map)

        missing_spot = odf["spot_close"].isna()
        if missing_spot.any():
            # ETF期权以及少量缺失合约，回退到现有主力/ETF 映射
            odf.loc[missing_spot, "spot_close"] = odf.loc[missing_spot].apply(
                lambda row: _match_spot_for_option(
                    row["product"], row["trade_date"], self._spot_idx
                ),
                axis=1
            )
        odf["hedge_symbol"] = odf["normalized_underlying"]
        etf_mask = odf["product"].isin(ETF_UNDERLYING)
        # ETF期权用股指期货对冲（ETF只能做多不能做空，不适合双向对冲）
        # 300ETF → IF，50ETF → IH，500ETF → IC，科创50 → 暂不对冲
        ETF_TO_INDEX_FUTURES = {
            '华泰柏瑞沪深300ETF': 'IF',   # 300ETF沪 → IF
            '嘉实沪深300ETF': 'IF',        # 300ETF深 → IF
            '华夏上证50ETF': 'IH',         # 50ETF → IH
            '南方中证500ETF': 'IC',        # 500ETF沪 → IC（暂无IC期权，用期货）
            '嘉实中证500ETF': 'IC',        # 500ETF深 → IC
        }
        # ETF期权的 hedge_symbol 改为股指期货代码，asset_type 改为 futures
        for etf_name, fut_root in ETF_TO_INDEX_FUTURES.items():
            mask = odf["product"] == etf_name
            if mask.any():
                odf.loc[mask, "hedge_symbol"] = fut_root
        # 没有映射到股指期货的ETF期权（科创50、创业板等），暂不对冲
        unmapped_etf = etf_mask & ~odf["product"].isin(ETF_TO_INDEX_FUTURES)
        odf.loc[unmapped_etf, "hedge_symbol"] = None
        odf["hedge_family"] = odf["hedge_symbol"]
        # ETF期权统一用 futures 对冲（通过股指期货），不再标记为 etf
        odf["hedge_asset_type"] = "futures"
        odf["price_proxy"], odf["option_close"] = _build_option_close_series(odf)
        odf = odf.drop(columns=["normalized_underlying", "spot_key"])
        
        # 4. 计算 DTE
        odf["expiry_date_dt"] = pd.to_datetime(odf["expiry_date"])
        odf["trade_date_dt"] = pd.to_datetime(odf["trade_date"])
        odf["dte"] = (odf["expiry_date_dt"] - odf["trade_date_dt"]).dt.days
        
        # 5. 计算 moneyness
        odf["moneyness"] = np.where(
            odf["spot_close"] > 0,
            odf["strike"] / odf["spot_close"],
            np.nan
        )
        
        # 6. 过滤无效数据
        valid_mask = (
            (odf["option_close"] > 0) &
            (odf["spot_close"] > 0) &
            (odf["dte"] > 0) &
            (odf["strike"] > 0)
        )
        odf = odf[valid_mask].copy()
        
        if odf.empty:
            return pd.DataFrame()
        
        # 7. 计算 IV
        odf["implied_vol"] = calc_iv_batch(
            odf,
            price_col="option_close",
            spot_col="spot_close",
            strike_col="strike",
            dte_col="dte",
            otype_col="option_type"
        )
        
        # 8. 计算 Greeks（只对有IV的行）
        has_iv = odf["implied_vol"].notna() & (odf["implied_vol"] > 0)
        greeks_df = calc_greeks_batch(
            odf[has_iv],
            spot_col="spot_close",
            strike_col="strike",
            dte_col="dte",
            iv_col="implied_vol",
            otype_col="option_type"
        )
        odf.loc[has_iv, "delta"] = greeks_df["delta"].values
        odf.loc[has_iv, "vega"] = greeks_df["vega"].values
        odf.loc[has_iv, "gamma"] = greeks_df["gamma"].values
        odf.loc[has_iv, "theta"] = greeks_df["theta"].values
        
        # 9. pricing_status
        odf["pricing_status"] = np.where(
            (odf["implied_vol"] > 0) & (odf["implied_vol"] < 5) &
            (odf["option_close"] > 0) & odf["delta"].notna(),
            "usable", "unusable"
        )
        
        # 10. 输出格式（与 load_product_data 兼容）
        result = pd.DataFrame({
            "trade_date": pd.to_datetime(odf["trade_date"]),
            "option_code": odf["contract_code"],
            "option_type": odf["option_type"],
            "strike": odf["strike"],
            "delta": odf["delta"],
            "implied_vol": odf["implied_vol"],
            "moneyness": odf["moneyness"],
            "dte": odf["dte"],
            "spot_close": odf["spot_close"],
            "option_close": odf["option_close"],
            "expiry_date": odf["expiry_date_dt"],
            "vega": odf["vega"],
            "gamma": odf["gamma"],
            "theta": odf["theta"],
            "volume": odf["volume"],
            "open_interest": odf["close_oi"],
            "pricing_status": odf["pricing_status"],
            "underlying_code": odf["underlying_code"],
            "hedge_symbol": odf["hedge_symbol"],
            "hedge_family": odf["hedge_family"],
            "hedge_asset_type": odf["hedge_asset_type"],
            "price_proxy": odf["price_proxy"],
        })
        
        # 只保留 usable
        result = result[result["pricing_status"] == "usable"].copy()
        result = result.sort_values(["trade_date", "expiry_date", "strike"]).reset_index(drop=True)
        
        return result
    
    def load_all_products(self, product_map, start_date=None):
        """
        批量加载所有品种。
        
        Args:
            product_map: {root: (where_clause, name, mult, mr, liq)} 格式的品种映射
                         或 {root: {"name": ..., "mult": ..., ...}} 格式
            start_date: 起始日期
        
        Returns:
            dict: {品种名: {"df": DataFrame, "mult": ..., "mr": ..., ...}}
        """
        self._ensure_loaded()
        
        pdata = {}
        t0 = time.time()
        
        # 获取聚合数据库中实际有数据的品种列表
        try:
            available = set(
                r[0] for r in self._agg_conn.execute(
                    "SELECT DISTINCT product FROM option_daily_agg"
                ).fetchall()
            )
        except Exception:
            available = set()
        
        print(f"聚合数据库品种: {len(available)}个")
        
        for root, info in product_map.items():
            # 兼容两种格式
            if isinstance(info, tuple):
                where, name, mult, mr, liq = info
            else:
                name = info.get("name", root)
                mult = info.get("mult", 10)
                mr = info.get("mr", 0.05)
                liq = info.get("liq", "commodity_low")
            
            # 确定聚合数据库中的品种标识
            agg_product = self._resolve_agg_product(root, available)
            if agg_product is None:
                continue
            
            df = self.load_product(agg_product, name, start_date)
            if df.empty or len(df) < 100:
                continue
            
            pdata[name] = {
                "df": df,
                "mult": mult,
                "mr": mr,
                "liq": liq,
                "dg": {d: g for d, g in df.groupby("trade_date")},
                "idx": df.set_index(["trade_date", "option_code"]).sort_index(),
                "exchange": self._get_exchange(root),
            }
            
            n_usable = len(df)
            date_range = f"{df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}"
            print(f"  {name}: {n_usable:,}行, {date_range}")
        
        elapsed = time.time() - t0
        print(f"加载完成: {len(pdata)}品种, {elapsed:.0f}秒")
        return pdata
    
    def _resolve_agg_product(self, root, available_products):
        """
        将 PRODUCT_MAP 的 root 映射到聚合数据库中的 product 标识。
        
        聚合数据库的 product 字段来自目录名（如 'm', 'SA', 'IO'）。
        PRODUCT_MAP 的 root 可能是 'm', 'HS300', 'CSI1000' 等。
        """
        # 直接匹配
        if root in available_products:
            return root
        
        # 股指期权映射：HS300→IO, CSI1000→MO, SSE50→HO
        underlying_to_option = {
            'HS300': 'IO', 'CSI1000': 'MO', 'SSE50': 'HO',
        }
        mapped = underlying_to_option.get(root)
        if mapped and mapped in available_products:
            return mapped
        
        # ETF期权映射
        etf_map = {
            '510300': '华泰柏瑞沪深300ETF',
            '510050': '华夏上证50ETF',
            '510500': '南方中证500ETF',
            '588000': '华夏上证科创板50ETF',
            '588080': '易方达上证科创板50ETF',
            '159919': '嘉实沪深300ETF',
            '159922': '嘉实中证500ETF',
            '159915': '易方达创业板ETF',
            '159901': '易方达深证100ETF',
        }
        mapped = etf_map.get(root)
        if mapped and mapped in available_products:
            return mapped
        
        # 去掉 _SSE/_SZSE 后缀再试（PRODUCT_MAP 的 key 如 '510300_SSE'）
        stripped = root.split('_')[0] if '_' in root else None
        if stripped:
            mapped = etf_map.get(stripped)
            if mapped and mapped in available_products:
                return mapped
        
        return None
    
    def _get_exchange(self, root):
        """获取品种所属交易所"""
        from exp_product_count import EXCHANGE_OF
        return EXCHANGE_OF.get(root)
    
    def close(self):
        """关闭数据库连接"""
        if self._agg_conn:
            self._agg_conn.close()
            self._agg_conn = None


# ── ETF期权品种映射（扩展 PRODUCT_MAP）──

ETF_PRODUCT_MAP = {
    # SSE ETF期权
    '510300_SSE': ("contract_code LIKE 'SSE.%' AND product = '华泰柏瑞沪深300ETF'",
                   "300ETF沪", 10000, 0.12, "etf"),
    '510050_SSE': ("contract_code LIKE 'SSE.%' AND product = '华夏上证50ETF'",
                   "50ETF", 10000, 0.12, "etf"),
    '510500_SSE': ("contract_code LIKE 'SSE.%' AND product = '南方中证500ETF'",
                   "500ETF沪", 10000, 0.12, "etf"),
    '588000_SSE': ("contract_code LIKE 'SSE.%' AND product = '华夏上证科创板50ETF'",
                   "科创50ETF华夏", 10000, 0.12, "etf"),
    '588080_SSE': ("contract_code LIKE 'SSE.%' AND product = '易方达上证科创板50ETF'",
                   "科创50ETF易方达", 10000, 0.12, "etf"),
    # SZSE ETF期权
    '159919_SZSE': ("contract_code LIKE 'SZSE.%' AND product = '嘉实沪深300ETF'",
                    "300ETF深", 10000, 0.12, "etf"),
    '159922_SZSE': ("contract_code LIKE 'SZSE.%' AND product = '嘉实中证500ETF'",
                    "500ETF深", 10000, 0.12, "etf"),
    '159915_SZSE': ("contract_code LIKE 'SZSE.%' AND product = '易方达创业板ETF'",
                    "创业板ETF", 10000, 0.12, "etf"),
    '159901_SZSE': ("contract_code LIKE 'SZSE.%' AND product = '易方达深证100ETF'",
                    "深证100ETF", 10000, 0.12, "etf"),
}

# ETF品种的交易所映射
ETF_EXCHANGE_OF = {
    '510300_SSE': 'SSE', '510050_SSE': 'SSE', '510500_SSE': 'SSE',
    '588000_SSE': 'SSE', '588080_SSE': 'SSE',
    '159919_SZSE': 'SZSE', '159922_SZSE': 'SZSE',
    '159915_SZSE': 'SZSE', '159901_SZSE': 'SZSE',
}


def _filter_rank_candidates(df):
    if df.empty:
        return df
    return df[
        (df["delta"].abs() < 0.15)
        & (df["delta"].abs() > 0.01)
        & (df["option_close"] >= 0.5)
        & df["dte"].between(15, 90)
        & (
            ((df["option_type"] == "P") & (df["moneyness"] < 1.0))
            | ((df["option_type"] == "C") & (df["moneyness"] > 1.0))
        )
        & (df["volume"] > 0)
        & (df["open_interest"] > 0)
    ].copy()


def rank_products_from_pdata(product_map, pdata, sort_by="oi", end_date=None):
    """Rank products using corrected v2 fields already loaded in memory."""
    from exp_product_count import EXCHANGE_OF

    if end_date:
        end_ts = pd.Timestamp(end_date)
        start_ts = end_ts - pd.DateOffset(months=6)
    else:
        end_ts = None
        start_ts = None

    ranked = []
    for root, info in product_map.items():
        if not isinstance(info, tuple):
            continue
        where, name, mult, mr, liq = info
        if name not in pdata:
            continue

        df = pdata[name]["df"]
        if end_ts is not None:
            df = df[(df["trade_date"] >= start_ts) & (df["trade_date"] <= end_ts)]
        df = _filter_rank_candidates(df)
        if df.empty:
            continue

        grouped = (
            df.groupby("option_type", dropna=False)
            .agg(
                avg_vol=("volume", "mean"),
                avg_oi=("open_interest", "mean"),
                avg_spot=("spot_close", "mean"),
                avg_prem=("option_close", "mean"),
                n_rows=("option_code", "size"),
            )
            .reset_index()
        )
        if grouped["n_rows"].sum() < 10:
            continue

        put_row = grouped[grouped["option_type"] == "P"]
        call_row = grouped[grouped["option_type"] == "C"]
        put_vol = float(put_row["avg_vol"].iloc[0]) if not put_row.empty else 0.0
        call_vol = float(call_row["avg_vol"].iloc[0]) if not call_row.empty else 0.0
        put_oi = float(put_row["avg_oi"].iloc[0]) if not put_row.empty else 0.0
        call_oi = float(call_row["avg_oi"].iloc[0]) if not call_row.empty else 0.0
        avg_spot = float(grouped["avg_spot"].mean())
        min_oi = min(put_oi, call_oi) if put_oi > 0 and call_oi > 0 else max(put_oi, call_oi)
        min_vol = min(put_vol, call_vol) if put_vol > 0 and call_vol > 0 else max(put_vol, call_vol)

        ranked.append({
            "root": root,
            "name": name,
            "daily_vol": put_vol + call_vol,
            "daily_oi": put_oi + call_oi,
            "put_vol": put_vol,
            "call_vol": call_vol,
            "put_oi": put_oi,
            "call_oi": call_oi,
            "effective_limit": min(int(max(min_oi, 1) * 0.05), int(max(min_vol, 1) * 0.10), 2000),
            "avg_spot": avg_spot,
            "exchange": pdata[name].get("exchange") or EXCHANGE_OF.get(root, "UNKNOWN"),
            "product_tuple": (where, name, mult, mr, liq),
        })

    if sort_by == "oi":
        ranked.sort(key=lambda x: x["daily_oi"], reverse=True)
    else:
        ranked.sort(key=lambda x: x["daily_vol"], reverse=True)
    return ranked


def scan_and_rank_v2(sort_by="oi", end_date=None, agg_db_path=None):
    """
    基于聚合数据库的品种排名（与旧版 scan_and_rank 筛选条件一致）。
    
    筛选条件（与旧版完全一致）：
      - |delta| < 0.15 AND |delta| > 0.01（深虚值）
      - close >= 0.5（有一定权利金）
      - dte BETWEEN 15 AND 90（中期合约）
      - Put: moneyness < 1.0 / Call: moneyness > 1.0（虚值方向正确）
    
    需要先运行 enrich_agg_db.py 为 option_daily_agg 补充 delta/moneyness/dte 字段。
    """
    from exp_product_count import PRODUCT_MAP, scan_and_rank

    db_path = agg_db_path or AGG_DB_PATH
    if not os.path.exists(db_path):
        return scan_and_rank(sort_by=sort_by, end_date=end_date)

    start_date = None
    if end_date:
        start_date = (pd.Timestamp(end_date) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")

    loader = DataLoaderV2(agg_db_path=db_path)
    try:
        pdata = loader.load_all_products(PRODUCT_MAP, start_date=start_date)
    finally:
        loader.close()
    return rank_products_from_pdata(PRODUCT_MAP, pdata, sort_by=sort_by, end_date=end_date)

    conn = sqlite3.connect(db_path)
    
    # 检查是否有 delta 列（enrich_agg_db.py 是否已运行）
    cols = set(r[1] for r in conn.execute("PRAGMA table_info(option_daily_agg)").fetchall())
    if "delta" not in cols:
        conn.close()
        print("  警告: option_daily_agg 缺少 delta 列，请先运行 enrich_agg_db.py")
        return _scan_and_rank_v2_fallback(sort_by, end_date, agg_db_path)
    
    # 用截止 end_date 前6个月的数据
    date_filter = ""
    if end_date:
        sd = pd.Timestamp(end_date) - pd.DateOffset(months=6)
        date_filter += f"AND trade_date >= '{sd.strftime('%Y-%m-%d')}'"
        date_filter += f" AND trade_date <= '{end_date}'"
    
    # 与旧版完全一致的筛选条件
    try:
        df = pd.read_sql(f"""
            SELECT product, option_type,
                   AVG(volume) as avg_vol,
                   AVG(close_oi) as avg_oi,
                   AVG(spot_close) as avg_spot,
                   AVG(close) as avg_prem,
                   COUNT(*) as n_rows
            FROM option_daily_agg
            WHERE ABS(delta) < 0.15 AND ABS(delta) > 0.01
              AND close >= 0.5
              AND dte BETWEEN 15 AND 90
              AND ((option_type = 'P' AND moneyness < 1.0)
                   OR (option_type = 'C' AND moneyness > 1.0))
              AND volume > 0 AND close_oi > 0
              {date_filter}
            GROUP BY product, option_type
        """, conn)
    except Exception as e:
        conn.close()
        print(f"  scan_and_rank_v2 查询失败: {e}")
        return []
    
    conn.close()
    
    if df.empty:
        return []
    
    # 按品种汇总 Put + Call
    underlying_to_option = {'HS300': 'IO', 'CSI1000': 'MO', 'SSE50': 'HO'}
    agg_to_root = {}
    for root in PRODUCT_MAP:
        agg_to_root[root] = root
        if root in underlying_to_option:
            agg_to_root[underlying_to_option[root]] = root
    
    product_data = {}
    for _, row in df.iterrows():
        agg_prod = row["product"]
        root = agg_to_root.get(agg_prod)
        if root is None or root not in PRODUCT_MAP:
            continue
        
        if root not in product_data:
            product_data[root] = {
                "put_vol": 0, "put_oi": 0, "call_vol": 0, "call_oi": 0,
                "avg_spot": 0, "avg_prem": 0, "n": 0,
            }
        
        d = product_data[root]
        ot = row["option_type"]
        if ot == "P":
            d["put_vol"] = row["avg_vol"]
            d["put_oi"] = row["avg_oi"]
        else:
            d["call_vol"] = row["avg_vol"]
            d["call_oi"] = row["avg_oi"]
        d["avg_spot"] = row["avg_spot"]
        d["avg_prem"] = max(d["avg_prem"], row["avg_prem"])
        d["n"] += row["n_rows"]
    
    ranked = []
    for root, d in product_data.items():
        if d["n"] < 10:
            continue
        
        info = PRODUCT_MAP[root]
        if isinstance(info, tuple):
            where, name, mult, mr, liq = info
        else:
            continue
        
        total_vol = d["put_vol"] + d["call_vol"]
        total_oi = d["put_oi"] + d["call_oi"]
        min_oi = min(d["put_oi"], d["call_oi"]) if d["call_oi"] > 0 else d["put_oi"]
        min_vol = min(d["put_vol"], d["call_vol"]) if d["call_vol"] > 0 else d["put_vol"]
        oi_limit = int(max(min_oi, 1) * 0.05)
        vol_limit = int(max(min_vol, 1) * 0.10)
        
        ranked.append({
            "root": root,
            "name": name,
            "daily_vol": total_vol,
            "daily_oi": total_oi,
            "put_vol": d["put_vol"],
            "call_vol": d["call_vol"],
            "put_oi": d["put_oi"],
            "call_oi": d["call_oi"],
            "effective_limit": min(oi_limit, vol_limit, 2000),
            "avg_spot": d["avg_spot"],
            "exchange": EXCHANGE_OF.get(root, "UNKNOWN"),
            "product_tuple": (where, name, mult, mr, liq),
        })
    
    if sort_by == "oi":
        ranked.sort(key=lambda x: x["daily_oi"], reverse=True)
    else:
        ranked.sort(key=lambda x: x["daily_vol"], reverse=True)
    
    return ranked


def _scan_and_rank_v2_fallback(sort_by="oi", end_date=None, agg_db_path=None):
    """delta列不存在时的退化版本（按全品种总量排序）"""
    from exp_product_count import PRODUCT_MAP, EXCHANGE_OF
    
    db_path = agg_db_path or AGG_DB_PATH
    conn = sqlite3.connect(db_path)
    
    date_filter = ""
    if end_date:
        sd = pd.Timestamp(end_date) - pd.DateOffset(months=6)
        date_filter += f"AND trade_date >= '{sd.strftime('%Y-%m-%d')}'"
        date_filter += f" AND trade_date <= '{end_date}'"
    
    try:
        df = pd.read_sql(f"""
            SELECT product, 
                   AVG(volume) as avg_vol,
                   AVG(close_oi) as avg_oi,
                   COUNT(DISTINCT trade_date) as n_days
            FROM option_daily_agg
            WHERE close > 0 AND volume > 0
              {date_filter}
            GROUP BY product
            HAVING n_days >= 20
        """, conn)
    except Exception as e:
        conn.close()
        return []
    
    conn.close()
    
    if df.empty:
        return []
    
    underlying_to_option = {'HS300': 'IO', 'CSI1000': 'MO', 'SSE50': 'HO'}
    agg_to_root = {}
    for root in PRODUCT_MAP:
        agg_to_root[root] = root
        if root in underlying_to_option:
            agg_to_root[underlying_to_option[root]] = root
    
    ranked = []
    for _, row in df.iterrows():
        agg_prod = row["product"]
        root = agg_to_root.get(agg_prod)
        if root is None or root not in PRODUCT_MAP:
            continue
        info = PRODUCT_MAP[root]
        if isinstance(info, tuple):
            where, name, mult, mr, liq = info
        else:
            continue
        ranked.append({
            "root": root, "name": name,
            "daily_vol": row["avg_vol"], "daily_oi": row["avg_oi"],
            "put_vol": row["avg_vol"] / 2, "call_vol": row["avg_vol"] / 2,
            "put_oi": row["avg_oi"] / 2, "call_oi": row["avg_oi"] / 2,
            "effective_limit": int(max(row["avg_oi"], 1) * 0.05),
            "avg_spot": 0,
            "exchange": EXCHANGE_OF.get(root, "UNKNOWN"),
            "product_tuple": (where, name, mult, mr, liq),
        })
    
    if sort_by == "oi":
        ranked.sort(key=lambda x: x["daily_oi"], reverse=True)
    else:
        ranked.sort(key=lambda x: x["daily_vol"], reverse=True)
    
    return ranked


def get_extended_product_map():
    """
    获取扩展后的品种映射（商品 + 股指 + ETF）。
    
    Returns:
        tuple: (product_map, exchange_of) 两个字典
    """
    from exp_product_count import PRODUCT_MAP, EXCHANGE_OF
    
    extended_map = dict(PRODUCT_MAP)
    extended_exchange = dict(EXCHANGE_OF)
    
    # 加入ETF期权
    extended_map.update(ETF_PRODUCT_MAP)
    extended_exchange.update(ETF_EXCHANGE_OF)
    
    return extended_map, extended_exchange


# ── 便捷函数：替代 load_product_data ──

def load_product_data_v2(product_root, product_name=None, start_date=None,
                         agg_db_path=None, contract_db_path=None):
    """
    单品种加载（便捷函数，兼容旧接口）。
    
    用法：
        df = load_product_data_v2('m', '豆粕', '2024-01-01')
    """
    loader = DataLoaderV2(agg_db_path, contract_db_path)
    try:
        return loader.load_product(product_root, product_name, start_date)
    finally:
        loader.close()


if __name__ == "__main__":
    """测试：加载几个品种验证数据完整性"""
    print("=" * 60)
    print("DataLoaderV2 测试")
    print("=" * 60)
    
    loader = DataLoaderV2()
    
    # 测试单品种
    test_products = [
        ("m", "豆粕"),
        ("IO", "沪深300股指"),
        ("SA", "纯碱"),
        ("cu", "沪铜"),
    ]
    
    for root, name in test_products:
        print(f"\n--- {name} ({root}) ---")
        df = loader.load_product(root, name)  # 加载全部数据
        if df.empty:
            print(f"  无数据")
            continue
        print(f"  行数: {len(df):,}")
        print(f"  日期: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
        print(f"  IV范围: {df['implied_vol'].quantile(0.05):.3f} ~ {df['implied_vol'].quantile(0.95):.3f}")
        print(f"  Delta范围: {df['delta'].min():.3f} ~ {df['delta'].max():.3f}")
        print(f"  有效率: {(df['pricing_status']=='usable').mean():.1%}")
        # 检查关键列
        for col in ["delta", "implied_vol", "vega", "spot_close", "dte", "moneyness"]:
            pct = df[col].notna().mean()
            print(f"    {col}: {pct:.1%} 非空")
    
    loader.close()
    print("\n测试完成")
