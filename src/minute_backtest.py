"""
分钟级数据回测引擎 v2

真实交易模拟流程：
  T日15:15收盘后：
    1. MTM更新所有持仓（用T日收盘价）
    2. 检查止盈/到期信号 → 生成平仓订单（pending_closes）
    3. 检查新开仓信号 → 生成开仓订单（pending_opens）
    4. 输出订单明细到 orders_YYYYMMDD.csv
  T+1日开盘后：
    5. 执行平仓订单（用T+1日开盘后N分钟VWAP）
    6. 执行开仓订单（用T+1日开盘后N分钟VWAP）
    7. 更新持仓
    8. 对冲层：按 hedge_family 汇总净 Cash Delta，生成期货/ETF对冲订单

与日频版的关键区别：
  - 开仓/平仓都用T+1分钟VWAP执行（不是日线(H+L+C)/3）
  - 可选盘中止盈：用T日分钟数据检查是否盘中触及止盈线
  - 每日输出订单CSV，完整记录每笔交易
  - 消除open_on前视偏差：每天实时检查DTE≈35
  - 对冲层：按 hedge_family 汇总净 Cash Delta，用期货/ETF对冲

用法：
    python 分钟级数据回测/src/minute_backtest.py
    python 分钟级数据回测/src/minute_backtest.py --no-agg-db   # 用benchmark.db
    python 分钟级数据回测/src/minute_backtest.py --start 2024-01-01
"""
import sys
import os
import re
import time
import json
import argparse
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from collections import defaultdict

from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy, select_s3_sell, select_s3_protect,
    select_s3_buy_by_otm, select_s3_sell_by_otm, select_s3_protect_by_otm,
    select_s4,
    calc_s1_size, calc_s3_size, calc_s3_size_v2, calc_s4_size,
    check_emergency_protect,
    extract_atm_iv_series, calc_iv_percentile_batch, get_iv_scale,
    calc_stats,
    should_take_profit_s1, should_take_profit_s3,
    should_close_expiry, can_reopen, should_pause_open,
    DEFAULT_PARAMS,
)
from backtest_fast import load_product_data, estimate_margin
from exp_product_count import PRODUCT_MAP, EXCHANGE_OF, scan_and_rank
from correlation_monitor import CorrelationMonitor, SECTOR_MAP

# 尝试导入 v2 数据加载器
try:
    from data_loader_v2 import DataLoaderV2, rank_products_from_pdata
    HAS_LOADER_V2 = True
except ImportError:
    HAS_LOADER_V2 = False

DB_PATH = os.environ.get("OPTION_DB_PATH", "benchmark.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MINUTE_DB_DIR = os.path.join(BASE_DIR, "..", "..", "期权相关分钟数据", "db")
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "output")
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config.json")
AGG_DB_PATH = os.path.join(MINUTE_DB_DIR, "option_daily_agg.db")
START_DATE = None  # 默认不限制起始日期

# 对冲标的乘数映射（从 PRODUCT_MAP 自动构建 + 股指期货特殊值）
HEDGE_MULTIPLIER_BY_ROOT = {}
for _root, _info in PRODUCT_MAP.items():
    if isinstance(_info, tuple) and len(_info) >= 3:
        HEDGE_MULTIPLIER_BY_ROOT[_root.upper()] = _info[2]
HEDGE_MULTIPLIER_BY_ROOT.update({"IF": 300, "IH": 300, "IM": 200, "IC": 200})


def load_runtime_params(params=None):
    """从 config.json 加载参数，再用传入的 params 覆盖"""
    merged = dict(DEFAULT_PARAMS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                merged.update(cfg)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  警告: config.json 读取失败: {exc}")
    if params:
        merged.update(params)
    return merged


# ============================================================
# 对冲层辅助函数
# ============================================================
def _extract_symbol_root(symbol):
    """从对冲标的代码提取品种根码（大写）"""
    if not symbol:
        return None
    match = re.match(r"^([A-Za-z]+)", str(symbol))
    return match.group(1).upper() if match else None


def _series_value(row, key, default=None):
    """安全地从 Series/dict 中取值"""
    if row is None:
        return default
    if hasattr(row, "index") and key in row.index and pd.notna(row[key]):
        return row[key]
    if isinstance(row, dict):
        v = row.get(key)
        return v if v is not None and (not isinstance(v, float) or not np.isnan(v)) else default
    return default


def _resolve_hedge_multiplier(symbol, asset_type, option_mult=None):
    """确定对冲标的的合约乘数"""
    root = _extract_symbol_root(symbol)
    if root and root in HEDGE_MULTIPLIER_BY_ROOT:
        return HEDGE_MULTIPLIER_BY_ROOT[root]
    return option_mult or 1


def _row_to_hedge_meta(row, option_mult):
    """从数据行提取对冲元数据"""
    hedge_symbol = _series_value(row, "hedge_symbol")
    hedge_family = _series_value(row, "hedge_family", hedge_symbol)
    hedge_asset_type = _series_value(row, "hedge_asset_type", "futures")
    return {
        "underlying_code": _series_value(row, "underlying_code"),
        "hedge_symbol": hedge_symbol,
        "hedge_family": hedge_family or hedge_symbol,
        "hedge_asset_type": hedge_asset_type,
        "hedge_multiplier": _resolve_hedge_multiplier(
            hedge_symbol, hedge_asset_type, option_mult),
    }


def _signed_qty(role, qty):
    """根据角色返回带符号的手数（买=正，卖=负）"""
    return qty if role in ("buy", "protect") else -qty


def _best_rebalance_qty(net_cash_delta, price, multiplier, lot_size=1):
    """
    计算最优对冲手数：使对冲后残余 Cash Delta 最小。
    
    net_cash_delta: 当前组合净 Cash Delta（正=多头暴露）
    price: 对冲标的价格
    multiplier: 对冲标的合约乘数
    lot_size: 最小交易单位（期货=1手，ETF=100股）
    """
    if price is None or price <= 0 or multiplier <= 0:
        return 0
    unit_delta = price * multiplier
    if abs(unit_delta) < 1e-10:
        return 0
    raw_units = -net_cash_delta / unit_delta
    lower = int(np.floor(raw_units / max(lot_size, 1)) * lot_size)
    upper = int(np.ceil(raw_units / max(lot_size, 1)) * lot_size)
    candidates = sorted(set([lower, upper]))
    return min(candidates, key=lambda q: abs(net_cash_delta + q * unit_delta))


# ============================================================
# 分钟数据加载器（批量预加载版）
# ============================================================
class MinuteDataLoader:
    """
    从分钟级SQLite数据库批量预加载数据到内存。
    
    预加载后查询速度从 ~0.1秒/次 降到 ~0.001秒/次（100倍提速）。
    内存占用取决于品种数和时间范围，20品种×2年约2-4GB。
    """

    def __init__(self, db_dir=MINUTE_DB_DIR, products=None):
        self.db_dir = db_dir
        self._conns = {}
        self._price_idx = {}
        self._loaded = False

    def _get_conn(self, exchange):
        """获取交易所分钟数据库连接"""
        if exchange not in self._conns:
            db_path = os.path.join(self.db_dir, f"option_minute_{exchange}.db")
            if not os.path.exists(db_path):
                return None
            self._conns[exchange] = sqlite3.connect(db_path)
        return self._conns[exchange]

    def preload(self, exchanges=None, start_date=None):
        """
        预加载分钟数据。优先用聚合数据库，fallback到分钟明细。
        """
        agg_path = os.path.join(self.db_dir, "option_daily_agg.db")
        if os.path.exists(agg_path):
            return self._preload_from_agg(agg_path, start_date)
        if exchanges is None:
            exchanges = ['DCE', 'SHFE', 'CZCE', 'CFFEX', 'INE', 'GFEX']
        return self._preload_from_minute(exchanges, start_date)

    def _preload_from_agg(self, agg_path, start_date):
        """从聚合数据库预加载，包含分时段VWAP和流动性指标"""
        print(f"  从聚合数据库预加载...", end="", flush=True)
        t0 = time.time()
        conn = sqlite3.connect(agg_path)
        
        where = f"WHERE trade_date >= '{start_date}'" if start_date else ""
        try:
            rows = conn.execute(f"""
                SELECT trade_date, contract_code, 
                       vwap, twap, open, low,
                       vwap_5, vwap_10, vwap_15, vwap_30,
                       volume, spread_proxy
                FROM option_daily_agg {where}
            """).fetchall()
            has_new_cols = True
        except Exception:
            rows = conn.execute(f"""
                SELECT trade_date, contract_code, vwap, twap, open, low
                FROM option_daily_agg {where}
            """).fetchall()
            has_new_cols = False
        
        for row in rows:
            if has_new_cols:
                (td, cc, vwap, twap, op, low,
                 v5, v10, v15, v30, vol, spread) = row
            else:
                td, cc, vwap, twap, op, low = row
                v5 = v10 = v15 = v30 = vol = spread = None
            
            self._price_idx[(td, cc)] = {
                "vwap": vwap if vwap and vwap > 0 else None,
                "twap": twap if twap and twap > 0 else None,
                "open": op if op and op > 0 else None,
                "low": low if low and low > 0 else None,
                "vwap_5": v5 if v5 and v5 > 0 else None,
                "vwap_10": v10 if v10 and v10 > 0 else None,
                "vwap_15": v15 if v15 and v15 > 0 else None,
                "vwap_30": v30 if v30 and v30 > 0 else None,
                "volume": vol or 0,
                "spread_proxy": spread or 0,
            }
        
        conn.close()
        self._loaded = True
        print(f" {len(self._price_idx):,}条, {time.time()-t0:.0f}秒")

    def _preload_from_minute(self, exchanges, start_date):
        """从分钟明细数据库聚合预加载（fallback路径）"""
        if exchanges is None:
            exchanges = ['DCE', 'SHFE', 'CZCE', 'CFFEX', 'INE', 'GFEX']
        
        total = 0
        t0 = time.time()
        
        for exch in exchanges:
            conn = self._get_conn(exch)
            if conn is None:
                continue
            try:
                conn.execute("SELECT 1 FROM option_minute LIMIT 1")
            except Exception:
                continue
            
            print(f"  预加载 {exch}...", end="", flush=True)
            where = f"AND datetime >= '{start_date} 00:00:00'" if start_date else ""
            
            sql = f"""
                SELECT 
                    SUBSTR(datetime, 1, 10) as trade_date,
                    contract_code,
                    MIN(CASE WHEN volume > 0 THEN open END) as first_open,
                    MIN(CASE WHEN volume > 0 THEN low END) as day_low,
                    SUM(CASE WHEN volume > 0 THEN (high+low+close)/3.0 * volume ELSE 0 END) as tp_x_vol,
                    SUM(CASE WHEN volume > 0 THEN volume ELSE 0 END) as total_vol,
                    COUNT(CASE WHEN volume > 0 AND close > 0 THEN 1 END) as n_bars,
                    SUM(CASE WHEN volume > 0 AND close > 0 THEN close ELSE 0 END) as sum_close
                FROM option_minute
                WHERE 1=1 {where}
                GROUP BY SUBSTR(datetime, 1, 10), contract_code
                HAVING total_vol > 0
            """
            
            try:
                rows = conn.execute(sql).fetchall()
            except Exception as e:
                print(f" 错误: {e}")
                continue
            
            for row in rows:
                trade_date, contract_code, first_open, day_low, tp_x_vol, total_vol, n_bars, sum_close = row
                vwap = tp_x_vol / total_vol if total_vol > 0 else None
                twap = sum_close / n_bars if n_bars > 0 else None
                
                self._price_idx[(trade_date, contract_code)] = {
                    "vwap": vwap,
                    "twap": twap,
                    "open": first_open if first_open and first_open > 0 else None,
                    "low": day_low if day_low and day_low > 0 else None,
                }
                total += 1
            
            print(f" {len(rows):,}条")
        
        self._loaded = True
        print(f"  预加载完成: {total:,}条索引, {time.time()-t0:.0f}秒")

    def _lookup(self, date_str, contract_code):
        """从预加载索引中查找（支持模糊匹配）"""
        key = (date_str, contract_code)
        if key in self._price_idx:
            return self._price_idx[key]
        # 模糊匹配（合约代码格式可能不完全一致）
        for k, v in self._price_idx.items():
            if k[0] == date_str and contract_code in k[1]:
                self._price_idx[key] = v  # 缓存映射
                return v
        return None

    def get_vwap(self, date_str, contract_code, window_minutes=10):
        """获取VWAP，优先用对应窗口的分时段VWAP"""
        if self._loaded:
            data = self._lookup(date_str, contract_code)
            if data:
                key = f"vwap_{window_minutes}"
                if key in data and data[key]:
                    return data[key]
                if data["vwap"]:
                    return data["vwap"]
            return None
        return self._query_vwap(date_str, contract_code, window_minutes)

    def get_twap(self, date_str, contract_code, window_minutes=10):
        """获取TWAP"""
        if self._loaded:
            data = self._lookup(date_str, contract_code)
            if data and data.get("twap"):
                return data["twap"]
            return None
        return None

    def get_intraday_low(self, date_str, contract_code):
        """获取盘中最低价"""
        if self._loaded:
            data = self._lookup(date_str, contract_code)
            if data and data.get("low"):
                return data["low"]
            return None
        return self._query_intraday_low(date_str, contract_code)

    def get_execution_prices(self, date_str, contract_code, window_minutes=10):
        """获取多种执行价格"""
        if self._loaded:
            data = self._lookup(date_str, contract_code)
            if data:
                return {
                    "vwap": data.get("vwap"),
                    "twap": data.get("twap"),
                    "open": data.get("open"),
                }
        return {"vwap": None, "twap": None, "open": None}

    def _query_vwap(self, date_str, contract_code, window_minutes):
        """实时查询VWAP（fallback，慢）"""
        for exch in ['DCE', 'SHFE', 'CZCE', 'CFFEX', 'INE', 'GFEX', 'SSE', 'SZSE']:
            conn = self._get_conn(exch)
            if conn is None:
                continue
            try:
                df = pd.read_sql(
                    f"SELECT high, low, close, volume FROM option_minute "
                    f"WHERE contract_code LIKE ? "
                    f"AND datetime >= '{date_str} 09:00:00' "
                    f"AND datetime <= '{date_str} 15:00:00' "
                    f"ORDER BY datetime LIMIT {window_minutes}",
                    conn, params=(f"%{contract_code}%",))
                if df.empty:
                    continue
                df = df[df["volume"] > 0]
                if df.empty:
                    continue
                df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
                return (df["tp"] * df["volume"]).sum() / df["volume"].sum()
            except Exception:
                continue
        return None

    def _query_intraday_low(self, date_str, contract_code):
        """实时查询盘中最低价（fallback，慢）"""
        for exch in ['DCE', 'SHFE', 'CZCE', 'CFFEX', 'INE', 'GFEX', 'SSE', 'SZSE']:
            conn = self._get_conn(exch)
            if conn is None:
                continue
            try:
                r = conn.execute(
                    "SELECT MIN(low) FROM option_minute "
                    "WHERE contract_code LIKE ? "
                    f"AND datetime >= '{date_str} 00:00:00' "
                    f"AND datetime <= '{date_str} 23:59:59' "
                    "AND volume > 0",
                    (f"%{contract_code}%",)).fetchone()
                if r and r[0] is not None and r[0] > 0:
                    return r[0]
            except Exception:
                continue
        return None

    def close(self):
        """关闭所有数据库连接"""
        for c in self._conns.values():
            c.close()
        self._conns.clear()
        self._price_idx.clear()



# ============================================================
# HedgeDataLoader — 对冲标的价格加载器
# ============================================================
class HedgeDataLoader:
    """从聚合数据库读取期货/ETF对冲标的的收盘价和执行价"""

    def __init__(self, agg_db_path=AGG_DB_PATH):
        self.agg_db_path = agg_db_path
        self._conn = None
        if os.path.exists(agg_db_path):
            try:
                self._conn = sqlite3.connect(agg_db_path)
            except sqlite3.Error:
                pass
        self._cache = {}

    def _fetch(self, date_str, hedge_symbol, asset_type):
        """查询并缓存对冲标的价格"""
        key = (date_str, hedge_symbol, asset_type)
        if key in self._cache:
            return self._cache[key]
        if self._conn is None:
            self._cache[key] = None
            return None
        
        data = None
        try:
            if asset_type == "etf":
                row = self._conn.execute(
                    "SELECT close, vwap FROM etf_daily_agg "
                    "WHERE trade_date = ? AND etf_code = ?",
                    (date_str, str(hedge_symbol)),
                ).fetchone()
                if row:
                    data = {"close": row[0], "vwap": row[1], "twap": None}
            else:
                row = self._conn.execute(
                    "SELECT close, vwap, twap FROM futures_daily_agg "
                    "WHERE trade_date = ? "
                    "  AND UPPER(SUBSTR(contract_code, INSTR(contract_code, '.') + 1)) = ? "
                    "LIMIT 1",
                    (date_str, str(hedge_symbol).upper()),
                ).fetchone()
                if row:
                    data = {"close": row[0], "vwap": row[1], "twap": row[2]}
        except sqlite3.Error:
            pass
        
        self._cache[key] = data
        return data

    def get_close(self, date_str, hedge_symbol, asset_type):
        """获取对冲标的收盘价"""
        data = self._fetch(date_str, hedge_symbol, asset_type)
        if not data:
            return None
        return data.get("close") or data.get("vwap") or data.get("twap")

    def get_exec_price(self, date_str, hedge_symbol, asset_type):
        """获取对冲标的执行价（优先VWAP）"""
        data = self._fetch(date_str, hedge_symbol, asset_type)
        if not data:
            return None
        return data.get("vwap") or data.get("close") or data.get("twap")

    def close(self):
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ============================================================
# Position — 期权持仓
# ============================================================
class Position:
    """单个期权持仓，含对冲元数据"""
    __slots__ = [
        "strat", "product", "code", "opt_type", "strike",
        "open_price", "n", "open_date", "mult", "expiry", "mr", "liq", "role",
        "prev_price", "cur_price", "cur_spot", "exchange",
        "cur_delta", "cur_gamma", "cur_vega", "cur_theta",
        # 对冲元数据
        "underlying_code", "hedge_symbol", "hedge_family",
        "hedge_asset_type", "hedge_multiplier",
    ]

    def __init__(self, strat, product, code, ot, strike, op, n, od,
                 mult, exp, mr, liq, role="sell", spot=0, exchange=None,
                 hedge_meta=None):
        self.strat = strat; self.product = product; self.code = code
        self.opt_type = ot; self.strike = strike; self.open_price = op
        self.n = n; self.open_date = od; self.mult = mult; self.expiry = exp
        self.mr = mr; self.liq = liq; self.role = role
        self.prev_price = op; self.cur_price = op; self.cur_spot = spot
        self.exchange = exchange
        self.cur_delta = 0; self.cur_gamma = 0
        self.cur_vega = 0; self.cur_theta = 0
        # 对冲元数据
        hm = hedge_meta or {}
        self.underlying_code = hm.get("underlying_code")
        self.hedge_symbol = hm.get("hedge_symbol")
        self.hedge_family = hm.get("hedge_family")
        self.hedge_asset_type = hm.get("hedge_asset_type", "futures")
        self.hedge_multiplier = hm.get("hedge_multiplier", mult)

    def daily_pnl(self):
        """当日盈亏（prev_price → cur_price）"""
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.prev_price) * self.mult * self.n
        return (self.prev_price - self.cur_price) * self.mult * self.n

    def profit_pct(self, fee_per_hand=0):
        """
        卖腿累计盈利百分比（扣除手续费后）。
        
        净利润 = (open_price - cur_price) * mult * n - fee_per_hand * n * 2
        （开仓+平仓各一次手续费）
        净利润率 = 净利润 / (open_price * mult * n)
        """
        if self.role == "sell" and self.open_price > 0:
            gross_pnl_per_hand = (self.open_price - self.cur_price) * self.mult
            fee_cost_per_hand = fee_per_hand * 2  # 开仓+平仓
            net_pnl_per_hand = gross_pnl_per_hand - fee_cost_per_hand
            revenue_per_hand = self.open_price * self.mult
            if revenue_per_hand > 0:
                return net_pnl_per_hand / revenue_per_hand
        return 0

    def cur_margin(self):
        """当前保证金（仅卖腿）"""
        if self.role == "sell":
            return estimate_margin(
                self.cur_spot or self.strike, self.strike, self.opt_type,
                self.cur_price, self.mult, self.mr, 0.5,
                exchange=self.exchange) * self.n
        return 0

    def cash_delta(self):
        """Cash Delta = sign × delta × 乘数 × 手数 × 标的价（去掉abs，允许自然对冲）"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_delta * self.mult * self.n * (self.cur_spot or 0)

    def cash_vega(self):
        """Cash Vega = sign × vega × 乘数 × 手数（不乘0.01，BSM vega单位已是 dPrice/dSigma）"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_vega * self.mult * self.n

    def cash_gamma(self):
        """Cash Gamma = sign × gamma × 乘数 × 手数 × 标的价²（不乘0.01）"""
        sign = 1 if self.role in ("buy", "protect") else -1
        spot = self.cur_spot or 0
        return sign * self.cur_gamma * self.mult * self.n * spot * spot

    def cash_theta(self):
        """Cash Theta = sign × theta × 乘数 × 手数"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_theta * self.mult * self.n


# ============================================================
# HedgePosition — 对冲持仓（期货/ETF）
# ============================================================
class HedgePosition:
    """单个对冲标的持仓"""
    __slots__ = [
        "hedge_symbol", "hedge_family", "asset_type", "qty", "side",
        "multiplier", "open_price", "prev_price", "cur_price",
        "cur_spot", "open_date",
    ]

    def __init__(self, hedge_symbol, hedge_family, asset_type,
                 signed_qty, multiplier, price, open_date):
        self.hedge_symbol = hedge_symbol
        self.hedge_family = hedge_family
        self.asset_type = asset_type
        self.multiplier = multiplier
        self.open_date = open_date
        self.open_price = price
        self.prev_price = price
        self.cur_price = price
        self.cur_spot = price
        self.set_signed_qty(signed_qty)

    def signed_qty(self):
        """带符号手数（long=正，short=负）"""
        return self.qty if self.side == "long" else -self.qty

    def set_signed_qty(self, signed_qty):
        """设置带符号手数"""
        self.side = "long" if signed_qty >= 0 else "short"
        self.qty = abs(int(signed_qty))

    def mark_to_market(self, new_price):
        """MTM更新价格，返回当日PnL"""
        self.prev_price = self.cur_price
        self.cur_price = new_price
        self.cur_spot = new_price
        return self.daily_pnl()

    def daily_pnl(self):
        """当日盈亏"""
        return self.signed_qty() * self.multiplier * (self.cur_price - self.prev_price)

    def cash_delta(self):
        """对冲持仓的 Cash Delta"""
        return self.signed_qty() * self.multiplier * (self.cur_spot or self.cur_price or 0)

    def notional(self):
        """名义价值"""
        return abs(self.signed_qty()) * self.multiplier * (self.cur_spot or self.cur_price or 0)



# ============================================================
# 订单记录
# ============================================================
class OrderLog:
    """记录每日订单，输出CSV"""
    def __init__(self):
        self.orders = []

    def add(self, date, action, strat, product, code, ot, strike, expiry,
            role, qty, ref_price, exec_price, exec_source, reason, **extra):
        """添加一条订单记录"""
        self.orders.append({
            "signal_date": str(date)[:10],
            "exec_date": "",
            "action": action,
            "strategy": strat,
            "product": product,
            "code": code,
            "option_type": ot,
            "strike": strike,
            "expiry": str(expiry)[:10],
            "role": role,
            "quantity": qty,
            "ref_price": round(ref_price, 4) if ref_price else 0,
            "exec_price": round(exec_price, 4) if exec_price else 0,
            "exec_source": exec_source,
            "reason": reason,
            **extra,
        })

    def to_dataframe(self):
        """转为DataFrame"""
        return pd.DataFrame(self.orders)


# ============================================================
# 辅助函数
# ============================================================
def _get_exec_price(ml, date, code, fallback, vwap_window, method="vwap"):
    """
    获取T+1执行价格，返回 (price, source)。
    用分钟聚合数据的精确窗口VWAP，并加上买卖价差模拟真实滑点。
    """
    if ml is None:
        return fallback, "fallback"
    ds = str(date.date()) if hasattr(date, 'date') else str(date)[:10]

    if method == "twap":
        v = ml.get_twap(ds, code, vwap_window)
        if v is not None and v > 0:
            return v, "minute_twap"
    elif method == "open":
        prices = ml.get_execution_prices(ds, code, vwap_window)
        if prices["open"] is not None and prices["open"] > 0:
            return prices["open"], "minute_open"
    else:  # vwap
        v = ml.get_vwap(ds, code, vwap_window)
        if v is not None and v > 0:
            return v, "minute_vwap"

    return fallback, "fallback"


def _apply_spread(price, role, ml, date, code):
    """
    根据买卖价差代理调整执行价格。
    卖出（开仓卖腿/平仓买腿）：价格下调半个spread
    买入（开仓买腿/平仓卖腿）：价格上调半个spread
    """
    if ml is None or not ml._loaded:
        return price
    ds = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
    data = ml._lookup(ds, code)
    if data is None or data.get("spread_proxy", 0) <= 0:
        return price
    half_spread = data["spread_proxy"] / 2
    if role in ("sell",):
        return price - half_spread
    elif role in ("buy", "protect"):
        return price + half_spread
    return price


def _cap_quantity_by_volume(qty, ml, date, code, max_pct=0.10):
    """根据日成交量限制手数：不超过日均成交量的max_pct"""
    if ml is None or not ml._loaded or qty <= 0:
        return qty
    ds = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
    data = ml._lookup(ds, code)
    if data is None or data.get("volume", 0) <= 0:
        return qty
    vol_limit = int(data["volume"] * max_pct)
    if vol_limit <= 0:
        return qty
    return min(qty, max(1, vol_limit))


def _check_should_open(day_df, dte_target=35, dte_min=15, dte_max=90):
    """
    实时检查当天是否有DTE≈35的到期月需要开仓（消除前视偏差）。
    返回: list of expiry_date
    """
    if day_df is None or day_df.empty:
        return []
    candidates = day_df[(day_df["dte"] >= dte_min) & (day_df["dte"] <= dte_max)]
    if candidates.empty:
        return []
    result = []
    for exp, grp in candidates.groupby("expiry_date"):
        dte = grp["dte"].iloc[0]
        if abs(dte - dte_target) <= abs(dte - 1 - dte_target):
            result.append(exp)
    return result


def _make_s3_templates(product, ot, bl, sl, ef, d, exp, bq, sq):
    """生成S3三腿模板列表"""
    templates = [
        {"strat": "S3", "product": product, "code": bl["option_code"],
         "ot": ot, "strike": bl["strike"], "fallback": bl["option_close"],
         "n": bq, "mult": d["mult"], "expiry": exp,
         "mr": d["mr"], "liq": d["liq"], "role": "buy", "spot": bl["spot_close"]},
        {"strat": "S3", "product": product, "code": sl["option_code"],
         "ot": ot, "strike": sl["strike"], "fallback": sl["option_close"],
         "n": sq, "mult": d["mult"], "expiry": exp,
         "mr": d["mr"], "liq": d["liq"], "role": "sell", "spot": sl["spot_close"]},
    ]
    pt = select_s3_protect(ef, ot, sl["strike"], sl["spot_close"])
    if pt is not None and pt["option_code"] != sl["option_code"]:
        templates.append({
            "strat": "S3", "product": product, "code": pt["option_code"],
            "ot": ot, "strike": pt["strike"], "fallback": pt["option_close"],
            "n": sq, "mult": d["mult"], "expiry": exp,
            "mr": d["mr"], "liq": d["liq"], "role": "protect", "spot": pt["spot_close"]})
    return templates


def _make_s3_templates_v2(product, ot, bl, sl, d, exp, bq, sq):
    """生成S3裸比例价差的2腿模板（不含保护腿）"""
    return [
        {"strat": "S3", "product": product, "code": bl["option_code"],
         "ot": ot, "strike": bl["strike"], "fallback": bl["option_close"],
         "n": bq, "mult": d["mult"], "expiry": exp,
         "mr": d["mr"], "liq": d["liq"], "role": "buy", "spot": bl["spot_close"]},
        {"strat": "S3", "product": product, "code": sl["option_code"],
         "ot": ot, "strike": sl["strike"], "fallback": sl["option_close"],
         "n": sq, "mult": d["mult"], "expiry": exp,
         "mr": d["mr"], "liq": d["liq"], "role": "sell", "spot": sl["spot_close"]},
    ]


def _get_option_row(pdata, product, date, code):
    """从pdata索引中获取期权行数据"""
    if product not in pdata:
        return None
    idx = pdata[product]["idx"]
    key = (date, code)
    if key not in idx.index:
        return None
    row = idx.loc[key]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return row


def _refresh_position_from_row(pos, row):
    """用最新行情数据刷新持仓的价格和Greeks"""
    if row is None:
        return
    pos.cur_price = float(_series_value(row, "option_close", pos.cur_price) or pos.cur_price)
    pos.cur_spot = float(_series_value(row, "spot_close", pos.cur_spot) or pos.cur_spot or 0)
    pos.cur_delta = float(_series_value(row, "delta", pos.cur_delta) or 0)
    pos.cur_vega = float(_series_value(row, "vega", pos.cur_vega) or 0)
    pos.cur_gamma = float(_series_value(row, "gamma", pos.cur_gamma) or 0)
    pos.cur_theta = float(_series_value(row, "theta", pos.cur_theta) or 0)
    # 刷新对冲元数据
    hm = _row_to_hedge_meta(row, pos.mult)
    pos.underlying_code = hm["underlying_code"]
    pos.hedge_symbol = hm["hedge_symbol"]
    pos.hedge_family = hm["hedge_family"]
    pos.hedge_asset_type = hm["hedge_asset_type"]
    pos.hedge_multiplier = hm["hedge_multiplier"]


def _project_hedge_orders(date, positions, pending_closes, pending_opens,
                          hedge_positions, hedge_loader, pdata):
    """
    按 hedge_family 汇总净 Cash Delta，计算对冲手数。
    
    考虑：当前持仓 + 待平仓（排除）+ 待开仓 + 已有对冲持仓
    目标：使每个 family 的净 Cash Delta 尽量为零。
    """
    family_delta = defaultdict(float)
    family_meta = {}
    pending_close_ids = {id(pos) for pos, _, _ in pending_closes}

    # 1. 当前期权持仓（排除待平仓的）
    for pos in positions:
        if id(pos) in pending_close_ids:
            continue
        if not pos.hedge_family or not pos.hedge_symbol:
            row = _get_option_row(pdata, pos.product, date, pos.code)
            _refresh_position_from_row(pos, row)
        if not pos.hedge_family or not pos.hedge_symbol:
            continue
        family_delta[pos.hedge_family] += pos.cash_delta()
        family_meta[pos.hedge_family] = {
            "hedge_symbol": pos.hedge_symbol,
            "asset_type": pos.hedge_asset_type or "futures",
            "multiplier": pos.hedge_multiplier or _resolve_hedge_multiplier(
                pos.hedge_symbol, pos.hedge_asset_type or "futures", pos.mult),
        }

    # 2. 待开仓的期权
    for tmpl in pending_opens:
        row = _get_option_row(pdata, tmpl["product"], date, tmpl["code"])
        if row is None:
            continue
        hm = _row_to_hedge_meta(row, tmpl["mult"])
        hf = hm["hedge_family"]
        hs = hm["hedge_symbol"]
        if not hf or not hs:
            continue
        delta_val = float(_series_value(row, "delta", 0) or 0)
        spot_val = float(_series_value(row, "spot_close", 0) or 0)
        cd = _signed_qty(tmpl["role"], tmpl["n"]) * delta_val * tmpl["mult"] * spot_val
        family_delta[hf] += cd
        family_meta[hf] = {
            "hedge_symbol": hs,
            "asset_type": hm["hedge_asset_type"],
            "multiplier": hm["hedge_multiplier"],
        }

    # 3. 已有对冲持仓
    for family, hpos in hedge_positions.items():
        family_delta[family] += hpos.cash_delta()
        family_meta.setdefault(family, {
            "hedge_symbol": hpos.hedge_symbol,
            "asset_type": hpos.asset_type,
            "multiplier": hpos.multiplier,
        })

    # 4. 计算每个 family 需要的对冲交易
    projected = []
    date_str = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
    for family, net_cd in family_delta.items():
        meta = family_meta.get(family)
        if not meta:
            continue
        price = None
        if hedge_loader:
            price = hedge_loader.get_close(date_str, meta["hedge_symbol"], meta["asset_type"])
        lot_size = 100 if meta["asset_type"] == "etf" else 1
        trade_qty = _best_rebalance_qty(net_cd, price, meta["multiplier"], lot_size)
        if trade_qty == 0:
            continue
        projected.append({
            "family": family,
            "hedge_symbol": meta["hedge_symbol"],
            "asset_type": meta["asset_type"],
            "multiplier": meta["multiplier"],
            "trade_qty": int(trade_qty),
            "signal_cash_delta": net_cd,
            "signal_price": price,
        })
    return projected



# ============================================================
# 主回测函数
# ============================================================
def run_minute_backtest(products=None, s4_products=None, enable_s4=True,
                        start_date=None, params=None,
                        vwap_window=10, use_intraday_tp=False,
                        minute_loader=None, product_exchanges=None,
                        exec_method="vwap", use_agg_db=True):
    """
    分钟级回测主函数。
    
    Args:
        use_agg_db: True=用聚合数据库(v2模式)，False=用benchmark.db(旧模式)
    """
    p = load_runtime_params(params)
    CAPITAL = p["capital"]
    margin_per = p["margin_per"]
    margin_cap = p["margin_cap"]
    s1_cap = p["s1_margin_cap"]
    s3_cap = p["s3_margin_cap"]
    s1_tp = p["s1_tp"]
    s3_tp = p["s3_tp"]
    s4_prem = p["s4_prem"]
    iv_inverse = p["iv_inverse"]
    iv_open_thr = p.get("iv_open_threshold", 80)
    s4_max_hold = p.get("s4_max_hold", 15)
    _product_exchange = product_exchanges or {}
    ml = minute_loader
    hedge_enabled = bool(p.get("hedge_enabled", True))
    hedge_loader = HedgeDataLoader() if hedge_enabled else None

    # ── 加载日频数据 ──
    if use_agg_db and HAS_LOADER_V2:
        print("加载日频数据（v2聚合数据库模式）...", flush=True)
        loader = DataLoaderV2()
        pdata = loader.load_all_products(PRODUCT_MAP, start_date=start_date)
        # 补充 exchange 信息
        for name, d in pdata.items():
            if d.get("exchange") is None:
                for root, info in PRODUCT_MAP.items():
                    if isinstance(info, tuple) and info[1] == name:
                        d["exchange"] = EXCHANGE_OF.get(root) or _product_exchange.get(name)
                        break
        loader.close()
    else:
        print("加载日频数据（benchmark.db模式）...", end="", flush=True)
        conn = sqlite3.connect(DB_PATH)
        pdata = {}
        all_product_tuples = list(PRODUCT_MAP.values())
        if products:
            all_product_tuples = list(set(all_product_tuples + list(products)))
        for where, name, mult, mr, liq in all_product_tuples:
            df = load_product_data(conn, where)
            if df.empty or len(df) < 100:
                continue
            pdata[name] = {
                "df": df, "mult": mult, "mr": mr, "liq": liq,
                "dg": {d: g for d, g in df.groupby("trade_date")},
                "idx": df.set_index(["trade_date", "option_code"]),
                "exchange": _product_exchange.get(name),
            }
        conn.close()
        print(f" {len(pdata)}品种")

    # ── 相关性监控 ──
    corr_monitor = CorrelationMonitor(pdata, window=60)
    print(f"相关性监控: {len(corr_monitor.spot_returns.columns)}品种")

    # ── IV分位数 ──
    iv_pcts = {}
    for name, d in pdata.items():
        s = extract_atm_iv_series(d["df"])
        if not s.empty:
            iv_pcts[name] = calc_iv_percentile_batch(s)

    all_dates = sorted(set().union(*(d["dg"].keys() for d in pdata.values())))
    if start_date:
        all_dates = [d for d in all_dates if d >= pd.Timestamp(start_date)]

    # ── 动态品种池 ──
    monthly_product_pool = {}
    top_n = p.get("products_top_n", 20) if isinstance(p.get("products_top_n"), int) else 20

    # 选择排名函数
    if use_agg_db and HAS_LOADER_V2:
        def _rank_fn(sort_by, end_date):
            return rank_products_from_pdata(PRODUCT_MAP, pdata, sort_by=sort_by, end_date=end_date)
    else:
        def _rank_fn(sort_by, end_date):
            return scan_and_rank(sort_by=sort_by, end_date=end_date)

    print("计算动态品种池...", end="", flush=True)
    months_in_range = sorted(set(str(d.date())[:7] for d in all_dates))
    for month_key in months_in_range:
        end_str = f"{month_key}-28"
        try:
            ranked_now = _rank_fn(sort_by="oi", end_date=end_str)
            pool = set()
            for r in ranked_now[:top_n]:
                if r["name"] in pdata:
                    pool.add(r["name"])
            if pool:
                monthly_product_pool[month_key] = pool
        except Exception:
            pass
    # 填充缺失月份
    prev_pool = set(pdata.keys())
    for month_key in months_in_range:
        if month_key not in monthly_product_pool:
            monthly_product_pool[month_key] = prev_pool
        else:
            prev_pool = monthly_product_pool[month_key]
    print(f" {len(months_in_range)}个月")

    # ── 初始化状态 ──
    positions = []
    hedge_positions = {}  # family -> HedgePosition
    records = []
    order_log = OrderLog()
    snapshots = {}

    pending_opens = []
    pending_closes = []
    pending_hedges = []  # 对冲订单模板

    exec_stats = defaultdict(int)
    itp_count = 0

    def _iv(pn, dt):
        if pn not in iv_pcts:
            return np.nan
        ps = iv_pcts[pn]
        return ps.loc[dt] if dt in ps.index else np.nan

    print(f"回测 {len(all_dates)}天, VWAP={vwap_window}min, "
          f"盘中止盈={'ON' if use_intraday_tp else 'OFF'}, "
          f"对冲={'ON' if hedge_enabled else 'OFF'}...", flush=True)

    for di, date in enumerate(all_dates):
        if di % 100 == 0:
            print(f"  [{di}/{len(all_dates)}] {date.date()}", flush=True)

        nav_now = CAPITAL + (records[-1]["cum_pnl"] if records else 0)
        date_str = str(date.date())

        # ============================================================
        # Phase 1: 执行昨天的pending（T+1日执行）
        # ============================================================

        # 1a. 执行平仓
        close_ids = set()
        for pos, reason, ref_price in pending_closes:
            if id(pos) in close_ids:
                continue
            close_price, src = _get_exec_price(
                ml, date, pos.code, ref_price, vwap_window, exec_method)
            close_role = "buy" if pos.role == "sell" else "sell"
            close_price = _apply_spread(close_price, close_role, ml, date, pos.code)
            exec_stats[src] += 1
            pos.prev_price = pos.cur_price
            pos.cur_price = close_price
            close_ids.add(id(pos))
            order_log.add(
                date=records[-1]["date"] if records else date_str,
                action="close", strat=pos.strat, product=pos.product,
                code=pos.code, ot=pos.opt_type, strike=pos.strike,
                expiry=pos.expiry, role=pos.role, qty=pos.n,
                ref_price=ref_price, exec_price=close_price,
                exec_source=src, reason=reason,
                exec_date=date_str,
            )

        # 计算平仓PnL
        close_pnl = 0.0
        close_pnl_s1 = close_pnl_s3 = close_pnl_s4 = 0.0
        for pos in [p for p in positions if id(p) in close_ids]:
            dp = pos.daily_pnl()
            close_pnl += dp
            if pos.strat == "S1": close_pnl_s1 += dp
            elif pos.strat == "S3": close_pnl_s3 += dp
            elif pos.strat == "S4": close_pnl_s4 += dp
        positions = [p for p in positions if id(p) not in close_ids]
        pending_closes_executed = list(pending_closes)
        pending_closes.clear()

        # 1b. 执行开仓
        pending_opens_executed = list(pending_opens)
        for tmpl in pending_opens:
            price, src = _get_exec_price(
                ml, date, tmpl["code"], tmpl["fallback"], vwap_window, exec_method)
            price = _apply_spread(price, tmpl["role"], ml, date, tmpl["code"])
            capped_n = _cap_quantity_by_volume(tmpl["n"], ml, date, tmpl["code"])
            exec_stats[src] += 1
            exch = pdata[tmpl["product"]]["exchange"] if tmpl["product"] in pdata else None
            # 提取对冲元数据
            hm = tmpl.get("hedge_meta")
            if hm is None and use_agg_db:
                row = _get_option_row(pdata, tmpl["product"], date, tmpl["code"])
                if row is not None:
                    hm = _row_to_hedge_meta(row, tmpl["mult"])
            pos = Position(
                tmpl["strat"], tmpl["product"], tmpl["code"], tmpl["ot"],
                tmpl["strike"], price, capped_n, date,
                tmpl["mult"], tmpl["expiry"], tmpl["mr"], tmpl["liq"],
                tmpl["role"], tmpl.get("spot", 0),
                exchange=exch, hedge_meta=hm)
            positions.append(pos)
            order_log.add(
                date=records[-1]["date"] if records else date_str,
                action="open", strat=tmpl["strat"], product=tmpl["product"],
                code=tmpl["code"], ot=tmpl["ot"], strike=tmpl["strike"],
                expiry=tmpl["expiry"], role=tmpl["role"], qty=capped_n,
                ref_price=tmpl["fallback"], exec_price=price,
                exec_source=src, reason=tmpl.get("reason", "new"),
                exec_date=date_str,
            )
        pending_opens.clear()

        # 1c. 执行对冲订单
        hedge_pnl = 0.0
        for htmpl in pending_hedges:
            family = htmpl["family"]
            exec_price = None
            if hedge_loader:
                exec_price = hedge_loader.get_exec_price(
                    date_str, htmpl["hedge_symbol"], htmpl["asset_type"])
            if exec_price is None or exec_price <= 0:
                exec_price = htmpl.get("signal_price")
            if exec_price is None or exec_price <= 0:
                continue

            if family in hedge_positions:
                # 已有对冲持仓：调整手数
                hpos = hedge_positions[family]
                old_qty = hpos.signed_qty()
                new_qty = old_qty + htmpl["trade_qty"]
                if new_qty == 0:
                    # 完全平仓
                    hedge_pnl += hpos.signed_qty() * hpos.multiplier * (exec_price - hpos.cur_price)
                    del hedge_positions[family]
                else:
                    # 部分调整
                    trade_pnl = 0
                    if abs(new_qty) < abs(old_qty):
                        # 减仓：部分平仓PnL
                        closed_qty = old_qty - new_qty  # 带符号
                        trade_pnl = closed_qty * hpos.multiplier * (exec_price - hpos.cur_price)
                    hpos.set_signed_qty(new_qty)
                    hpos.cur_price = exec_price
                    hpos.cur_spot = exec_price
                    hedge_pnl += trade_pnl
            else:
                # 新建对冲持仓
                hpos = HedgePosition(
                    htmpl["hedge_symbol"], family, htmpl["asset_type"],
                    htmpl["trade_qty"], htmpl["multiplier"],
                    exec_price, date)
                hedge_positions[family] = hpos

            order_log.add(
                date=records[-1]["date"] if records else date_str,
                action="hedge", strat="HEDGE", product=family,
                code=htmpl["hedge_symbol"], ot=htmpl["asset_type"],
                strike=0, expiry="", role="hedge",
                qty=htmpl["trade_qty"],
                ref_price=htmpl.get("signal_price", 0),
                exec_price=exec_price,
                exec_source="hedge_vwap", reason="delta_hedge",
                exec_date=date_str,
            )
        pending_hedges.clear()

        # ============================================================
        # Phase 2: MTM（用T日收盘价）+ Greeks更新
        # ============================================================
        day_pnl = close_pnl
        pnl_s1 = close_pnl_s1
        pnl_s3 = close_pnl_s3
        pnl_s4 = close_pnl_s4
        day_fee = 0.0

        for pos in positions:
            k = (date, pos.code)
            pos.prev_price = pos.cur_price
            if pos.product in pdata and k in pdata[pos.product]["idx"].index:
                row = pdata[pos.product]["idx"].loc[k]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                pos.cur_price = row["option_close"]
                pos.cur_spot = row["spot_close"]
                # 更新Greeks
                pos.cur_delta = float(row["delta"]) if "delta" in row.index and pd.notna(row["delta"]) else 0
                pos.cur_vega = float(row["vega"]) if "vega" in row.index and pd.notna(row["vega"]) else 0
                pos.cur_gamma = float(row["gamma"]) if "gamma" in row.index and pd.notna(row["gamma"]) else 0
                pos.cur_theta = float(row["theta"]) if "theta" in row.index and pd.notna(row["theta"]) else 0
                # 刷新对冲元数据（v2模式下数据行含 hedge_symbol 等字段）
                if use_agg_db:
                    hm = _row_to_hedge_meta(row, pos.mult)
                    pos.underlying_code = hm["underlying_code"]
                    pos.hedge_symbol = hm["hedge_symbol"]
                    pos.hedge_family = hm["hedge_family"]
                    pos.hedge_asset_type = hm["hedge_asset_type"]
                    pos.hedge_multiplier = hm["hedge_multiplier"]
            elif date >= pos.expiry:
                pos.cur_price = 0.0
            dp = pos.daily_pnl()
            day_pnl += dp
            if pos.strat == "S1": pnl_s1 += dp
            elif pos.strat == "S3": pnl_s3 += dp
            elif pos.strat == "S4": pnl_s4 += dp

        # 对冲持仓 MTM
        for family, hpos in list(hedge_positions.items()):
            hp = None
            if hedge_loader:
                hp = hedge_loader.get_close(date_str, hpos.hedge_symbol, hpos.asset_type)
            if hp and hp > 0:
                hpos.prev_price = hpos.cur_price
                hpos.cur_price = hp
                hpos.cur_spot = hp
            hedge_pnl += hpos.daily_pnl()

        # 手续费：今天执行的开仓和平仓都扣手续费
        fee_per_hand = p.get("fee", 3)
        for pos, _, _ in pending_closes_executed:
            day_fee += fee_per_hand * pos.n
        for tmpl in pending_opens_executed:
            day_fee += fee_per_hand * tmpl["n"]
        day_pnl -= day_fee

        # ── 组合Greeks汇总（含对冲持仓）──
        total_cash_delta = sum(p.cash_delta() for p in positions)
        total_cash_vega = sum(p.cash_vega() for p in positions)
        total_cash_gamma = sum(p.cash_gamma() for p in positions)
        total_cash_theta = sum(p.cash_theta() for p in positions)
        # 对冲持仓的 Cash Delta
        hedge_cash_delta = sum(hp.cash_delta() for hp in hedge_positions.values())
        gross_cash_delta = total_cash_delta  # 期权组合的 gross delta
        net_cash_delta = total_cash_delta + hedge_cash_delta  # 含对冲后的 net delta

        nav_for_greeks = CAPITAL + (records[-1]["cum_pnl"] if records else 0) + day_pnl
        pct_delta = abs(gross_cash_delta) / max(nav_for_greeks, 1) 
        pct_net_delta = abs(net_cash_delta) / max(nav_for_greeks, 1)
        pct_vega = abs(total_cash_vega) / max(nav_for_greeks, 1)
        pct_gamma = abs(total_cash_gamma) / max(nav_for_greeks, 1)

        # ── Greeks预警检查 ──
        greeks_warning = ""
        vega_paused = False
        vega_warn_thr = p.get("greeks_vega_warn", 0.015)
        vega_hard_thr = p.get("greeks_vega_hard", 0.02)
        if pct_vega > vega_hard_thr:
            greeks_warning = f"VEGA_HARD_STOP({pct_vega:.1%})"
            vega_paused = True
        elif pct_vega > vega_warn_thr:
            greeks_warning = f"VEGA_WARNING({pct_vega:.1%})"
            vega_paused = True

        # ── 压力测试矩阵 ──
        spot_shocks = [-0.05, -0.03, -0.01, 0.01, 0.03, 0.05]
        iv_shocks = [5, 10, 20]
        stress_results = {}
        for ds in spot_shocks:
            for div in iv_shocks:
                pnl_est = (total_cash_delta * ds
                           + total_cash_gamma * 0.5 * ds * ds
                           + total_cash_vega * div)
                stress_results[f"s{int(ds*100)}_iv{div}"] = pnl_est

        # ── 每日持仓快照 ──
        daily_snapshot = []
        for pos in positions:
            daily_snapshot.append({
                "strat": pos.strat, "product": pos.product, "code": pos.code,
                "opt_type": pos.opt_type, "strike": pos.strike, "role": pos.role,
                "n": pos.n, "open_price": pos.open_price, "cur_price": pos.cur_price,
                "delta": pos.cur_delta, "vega": pos.cur_vega,
                "margin": pos.cur_margin(),
                "pnl": (pos.open_price - pos.cur_price) * pos.mult * pos.n
                       if pos.role == "sell" else
                       (pos.cur_price - pos.open_price) * pos.mult * pos.n,
            })
        for family, hpos in hedge_positions.items():
            daily_snapshot.append({
                "strat": "HEDGE", "product": family, "code": hpos.hedge_symbol,
                "opt_type": hpos.asset_type, "strike": 0, "role": hpos.side,
                "n": hpos.qty, "open_price": hpos.open_price, "cur_price": hpos.cur_price,
                "delta": hpos.signed_qty() * hpos.multiplier, "vega": 0,
                "margin": 0,
                "pnl": hpos.signed_qty() * hpos.multiplier * (hpos.cur_price - hpos.open_price),
            })
        snapshots[date_str] = daily_snapshot

        # ── Phase 1b: S3应急保护检查 ──
        trigger_otm = p.get("s3_protect_trigger_otm_pct", 5.0)
        for pos in list(positions):
            if pos.strat != "S3" or pos.role != "sell":
                continue
            # 检查是否已有保护腿
            has_protect = any(
                pp.strat == "S3" and pp.product == pos.product and
                pp.opt_type == pos.opt_type and pp.expiry == pos.expiry and
                pp.role == "protect" for pp in positions)
            if has_protect:
                continue
            # 检查是否触发应急保护
            if not check_emergency_protect(pos.strike, pos.cur_spot, pos.opt_type, trigger_otm):
                continue
            # 选择保护腿
            dg_prot = pdata[pos.product]["dg"].get(date) if pos.product in pdata else None
            if dg_prot is None:
                continue
            ef_prot = dg_prot[dg_prot["expiry_date"] == pos.expiry]
            if ef_prot.empty:
                continue
            pt = select_s3_protect_by_otm(
                ef_prot, pos.opt_type, pos.cur_spot, pos.strike,
                target_otm_pct=p.get("s3_protect_otm_pct", 15.0),
                otm_range=tuple(p.get("s3_protect_otm_range", (12.0, 20.0))),
                min_premium=0.1)
            if pt is not None:
                # 买入保护腿，手数等于卖腿手数
                hedge_meta = _row_to_hedge_meta(pt, pos.mult) if hasattr(pt, 'index') else {}
                prot_pos = Position("S3", pos.product, pt["option_code"], pos.opt_type,
                                   pt["strike"], pt["option_close"], pos.n, date,
                                   pos.mult, pos.expiry, pos.mr, pos.liq, "protect",
                                   pt["spot_close"], pos.exchange, hedge_meta)
                positions.append(prot_pos)
                order_log.add(date, "open", "S3", pos.product, pt["option_code"],
                             pos.opt_type, pt["strike"], pos.expiry, "protect",
                             pos.n, pt["option_close"], pt["option_close"],
                             "emergency", "emergency_protect")
            else:
                # 无合适保护合约 → 平仓整组
                for pp in list(positions):
                    if (pp.strat == "S3" and pp.product == pos.product and
                            pp.opt_type == pos.opt_type and pp.expiry == pos.expiry):
                        pending_closes.append((pp, "emergency_no_protect", pp.cur_price))

        # ============================================================
        # Phase 3: T日收盘后生成信号 → pending队列
        # ============================================================
        nav_after_mtm = CAPITAL + (records[-1]["cum_pnl"] if records else 0) + day_pnl

        # ── 平仓信号 ──
        for pos in positions:
            # S4平仓：DTE<10
            if pos.strat == "S4" and pos.role == "buy":
                d = pdata.get(pos.product)
                if d:
                    dg = d["dg"].get(date)
                    if dg is not None:
                        er = dg[dg["expiry_date"] == pos.expiry]
                        if not er.empty and er["dte"].iloc[0] < 10:
                            pending_closes.append((pos, "s4_dte_exit", pos.cur_price))
                            continue
                if s4_max_hold > 0 and (date - pos.open_date).days >= s4_max_hold:
                    pending_closes.append((pos, "s4_max_hold", pos.cur_price))
                    continue

            # 买腿/保护腿：只检查到期
            if pos.role in ("buy", "protect"):
                d = pdata.get(pos.product)
                if d is None:
                    continue
                dg = d["dg"].get(date)
                if dg is None:
                    if date >= pos.expiry:
                        pending_closes.append((pos, "expiry_no_data", 0.0))
                    continue
                er = dg[dg["expiry_date"] == pos.expiry]
                if er.empty or er["dte"].iloc[0] <= 1:
                    if date >= pos.expiry or (not er.empty and er["dte"].iloc[0] <= 1):
                        pending_closes.append((pos, "expiry", pos.cur_price))
                continue

            # ── 卖腿逻辑 ──
            d = pdata.get(pos.product)
            if d is None:
                continue
            dg = d["dg"].get(date)
            if dg is None:
                if date >= pos.expiry:
                    pending_closes.append((pos, "expiry_no_data", 0.0))
                    for pp in positions:
                        if (pp.product == pos.product and pp.expiry == pos.expiry and
                                pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                                pp.role != "sell"):
                            pending_closes.append((pp, "expiry_no_data", 0.0))
                continue

            er = dg[dg["expiry_date"] == pos.expiry]
            if er.empty:
                if date >= pos.expiry:
                    pending_closes.append((pos, "expiry", pos.cur_price))
                continue
            dte = er["dte"].iloc[0]

            # 止盈检查
            tp_hit = False
            tp_reason = ""

            if pos.strat == "S1":
                if use_intraday_tp and ml:
                    ds = str(date.date())
                    ilow = ml.get_intraday_low(ds, pos.code)
                    if ilow is not None and pos.open_price > 0:
                        # 盘中止盈也扣手续费：净利润率 = (毛利 - 手续费) / 权利金收入
                        gross_pnl = (pos.open_price - ilow) * pos.mult
                        fee_cost = fee_per_hand * 2  # 开仓+平仓
                        net_pnl = gross_pnl - fee_cost
                        ipct = net_pnl / (pos.open_price * pos.mult) if pos.open_price * pos.mult > 0 else 0
                        if ipct >= s1_tp and dte > 5:
                            tp_hit = True
                            tp_reason = "intraday_tp_s1"
                            itp_count += 1
                if not tp_hit and should_take_profit_s1(pos.profit_pct(fee_per_hand), dte, s1_tp):
                    tp_hit = True
                    tp_reason = "tp_s1"

            elif pos.strat == "S3":
                if use_intraday_tp and ml:
                    ds = str(date.date())
                    ilow = ml.get_intraday_low(ds, pos.code)
                    if ilow is not None and pos.open_price > 0:
                        gross_pnl = (pos.open_price - ilow) * pos.mult
                        fee_cost = fee_per_hand * 2
                        net_pnl = gross_pnl - fee_cost
                        ipct = net_pnl / (pos.open_price * pos.mult) if pos.open_price * pos.mult > 0 else 0
                        if ipct >= s3_tp and dte > 5:
                            tp_hit = True
                            tp_reason = "intraday_tp_s3"
                            itp_count += 1
                if not tp_hit and should_take_profit_s3(pos.profit_pct(fee_per_hand), dte, s3_tp):
                    tp_hit = True
                    tp_reason = "tp_s3"

            if tp_hit:
                pending_closes.append((pos, tp_reason, pos.cur_price))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            pp.role != "sell"):
                        pending_closes.append((pp, tp_reason + "_leg", pp.cur_price))

                # 止盈重开
                if can_reopen(dte):
                    iv_pct = _iv(pos.product, date)
                    if not should_pause_open(iv_pct, iv_open_thr):
                        sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                        ef = dg[dg["expiry_date"] == pos.expiry]
                        if pos.strat == "S1":
                            c = select_s1_sell(ef, pos.opt_type, d["mult"], d["mr"])
                            if c is not None and c["option_code"] != pos.code:
                                m = estimate_margin(c["spot_close"], c["strike"],
                                    c["option_type"], c["option_close"], d["mult"], d["mr"], 0.5)
                                nn = calc_s1_size(nav_after_mtm, margin_per, m, sc)
                                pending_opens.append({
                                    "strat": "S1", "product": pos.product,
                                    "code": c["option_code"], "ot": c["option_type"],
                                    "strike": c["strike"], "fallback": c["option_close"],
                                    "n": nn, "mult": d["mult"], "expiry": pos.expiry,
                                    "mr": d["mr"], "liq": d["liq"], "role": "sell",
                                    "spot": c["spot_close"], "reason": "reopen_s1",
                                })
                                pr = select_s1_protect(ef, c)
                                if pr is not None and pr["option_code"] != c["option_code"]:
                                    pending_opens.append({
                                        "strat": "S1", "product": pos.product,
                                        "code": pr["option_code"], "ot": pr["option_type"],
                                        "strike": pr["strike"], "fallback": pr["option_close"],
                                        "n": max(1, nn // 2), "mult": d["mult"],
                                        "expiry": pos.expiry, "mr": d["mr"], "liq": d["liq"],
                                        "role": "buy", "spot": pr["spot_close"],
                                        "reason": "reopen_s1_protect",
                                    })
                        elif pos.strat == "S3":
                            # v2：止盈重开，按OTM%选腿
                            # 持仓唯一性检查
                            if any(pp.strat == "S3" and pp.product == pos.product
                                   and pp.opt_type == pos.opt_type and pp.role == "sell"
                                   and id(pp) not in {id(x) for x, _, _ in pending_closes}
                                   for pp in positions):
                                pass  # 已有同品种同方向持仓，跳过重开
                            else:
                                spot_re = ef["spot_close"].iloc[0] if not ef.empty else 0
                                bl = select_s3_buy_by_otm(ef, pos.opt_type, spot_re,
                                                           target_otm_pct=p.get("s3_buy_otm_pct", 5.0),
                                                           otm_range=tuple(p.get("s3_buy_otm_range", (3.0, 7.0))),
                                                           min_premium=0.5)
                                if bl is not None:
                                    sl = select_s3_sell_by_otm(ef, pos.opt_type, spot_re, bl["strike"],
                                                                target_otm_pct=p.get("s3_sell_otm_pct", 10.0),
                                                                otm_range=tuple(p.get("s3_sell_otm_range", (7.0, 13.0))),
                                                                min_premium=0.5)
                                    if sl is not None and sl["option_code"] != bl["option_code"]:
                                        sm = estimate_margin(sl["spot_close"], sl["strike"],
                                            pos.opt_type, sl["option_close"], d["mult"], d["mr"], 0.5,
                                            exchange=d.get("exchange"))
                                        iv_sc_re = sc
                                        size_result = calc_s3_size_v2(
                                            nav_after_mtm, margin_per, sm,
                                            bl["option_close"], sl["option_close"], d["mult"], iv_sc_re,
                                            ratio_candidates=tuple(p.get("s3_ratio_candidates", (2, 3))),
                                            net_premium_tolerance=p.get("s3_net_premium_tolerance", 0.3))
                                        if size_result is not None:
                                            bq, sq, chosen_ratio = size_result
                                            for t in _make_s3_templates_v2(pos.product, pos.opt_type,
                                                                            bl, sl, d, pos.expiry, bq, sq):
                                                t["reason"] = "reopen_s3"
                                                pending_opens.append(t)
                continue

            # 到期
            if should_close_expiry(dte):
                pending_closes.append((pos, "expiry", pos.cur_price))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            pp.role != "sell"):
                        pending_closes.append((pp, "expiry_leg", pp.cur_price))

        # ── 新开仓信号 ──
        cur_month = str(date.date())[:7]
        cur_pool = monthly_product_pool.get(cur_month, set(pdata.keys()))

        total_m = sum(p.cur_margin() for p in positions if p.role == "sell")
        s1_m = sum(p.cur_margin() for p in positions if p.role == "sell" and p.strat == "S1")
        s3_m = sum(p.cur_margin() for p in positions if p.role == "sell" and p.strat == "S3")

        for pn, d in pdata.items():
            if pn not in cur_pool:
                continue
            dg = d["dg"].get(date)
            if dg is None:
                continue
            open_expiries = _check_should_open(dg)
            for exp in open_expiries:
                if any(p.product == pn and p.expiry == exp for p in positions):
                    continue
                if any(t["product"] == pn and t["expiry"] == exp for t in pending_opens):
                    continue

                ef = dg[dg["expiry_date"] == exp]
                if ef.empty:
                    continue

                iv_pct = _iv(pn, date)
                sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                iv_paused = should_pause_open(iv_pct, iv_open_thr)
                if vega_paused:
                    iv_paused = True

                # S3优先（v2：裸比例价差，按OTM%选腿）
                if not iv_paused and (not margin_cap or total_m / max(nav_after_mtm, 1) < margin_cap):
                    for ot in ["P", "C"]:
                        if any(p.strat == "S3" and p.product == pn and p.opt_type == ot
                               and p.role == "sell" for p in positions):
                            continue
                        if s3_cap and s3_m / max(nav_after_mtm, 1) > s3_cap:
                            break
                        if margin_cap and total_m / max(nav_after_mtm, 1) > margin_cap:
                            break
                        # v2：按OTM%选腿
                        spot = ef["spot_close"].iloc[0] if not ef.empty else 0
                        bl = select_s3_buy_by_otm(ef, ot, spot,
                                                   target_otm_pct=p.get("s3_buy_otm_pct", 5.0),
                                                   otm_range=tuple(p.get("s3_buy_otm_range", (3.0, 7.0))),
                                                   min_premium=0.5)
                        if bl is None:
                            continue
                        sl = select_s3_sell_by_otm(ef, ot, spot, bl["strike"],
                                                    target_otm_pct=p.get("s3_sell_otm_pct", 10.0),
                                                    otm_range=tuple(p.get("s3_sell_otm_range", (7.0, 13.0))),
                                                    min_premium=0.5)
                        if sl is None or bl["option_code"] == sl["option_code"]:
                            continue
                        sm = estimate_margin(sl["spot_close"], sl["strike"], ot,
                                             sl["option_close"], d["mult"], d["mr"], 0.5,
                                             exchange=d.get("exchange"))
                        size_result = calc_s3_size_v2(
                            nav_after_mtm, margin_per, sm,
                            bl["option_close"], sl["option_close"], d["mult"], sc,
                            ratio_candidates=tuple(p.get("s3_ratio_candidates", (2, 3))),
                            net_premium_tolerance=p.get("s3_net_premium_tolerance", 0.3))
                        if size_result is None:
                            continue
                        bq, sq, chosen_ratio = size_result
                        new_m = sm * sq
                        if s3_cap and (s3_m + new_m) / max(nav_after_mtm, 1) > s3_cap:
                            continue
                        if margin_cap and (total_m + new_m) / max(nav_after_mtm, 1) > margin_cap:
                            continue
                        for t in _make_s3_templates_v2(pn, ot, bl, sl, d, exp, bq, sq):
                            t["reason"] = "new_s3"
                            pending_opens.append(t)
                        total_m += new_m
                        s3_m += new_m

                # S1
                if not iv_paused and (not margin_cap or total_m / max(nav_after_mtm, 1) < margin_cap):
                    for ot in ["P", "C"]:
                        if s1_cap and s1_m / max(nav_after_mtm, 1) > s1_cap:
                            break
                        if margin_cap and total_m / max(nav_after_mtm, 1) > margin_cap:
                            break
                        c = select_s1_sell(ef, ot, d["mult"], d["mr"])
                        if c is None:
                            continue
                        m = estimate_margin(c["spot_close"], c["strike"], ot,
                                            c["option_close"], d["mult"], d["mr"], 0.5)
                        nn = calc_s1_size(nav_after_mtm, margin_per, m, sc)
                        if s1_cap and (s1_m + m * nn) / max(nav_after_mtm, 1) > s1_cap:
                            continue
                        if margin_cap and (total_m + m * nn) / max(nav_after_mtm, 1) > margin_cap:
                            continue
                        pending_opens.append({
                            "strat": "S1", "product": pn, "code": c["option_code"],
                            "ot": ot, "strike": c["strike"], "fallback": c["option_close"],
                            "n": nn, "mult": d["mult"], "expiry": exp,
                            "mr": d["mr"], "liq": d["liq"], "role": "sell",
                            "spot": c["spot_close"], "reason": "new_s1",
                        })
                        total_m += m * nn
                        s1_m += m * nn
                        pr = select_s1_protect(ef, c)
                        if pr is not None and pr["option_code"] != c["option_code"]:
                            pending_opens.append({
                                "strat": "S1", "product": pn, "code": pr["option_code"],
                                "ot": ot, "strike": pr["strike"], "fallback": pr["option_close"],
                                "n": max(1, nn // 2), "mult": d["mult"], "expiry": exp,
                                "mr": d["mr"], "liq": d["liq"], "role": "buy",
                                "spot": pr["spot_close"], "reason": "new_s1_protect",
                            })

                # S4
                if enable_s4:
                    if not any(p.strat == "S4" and p.product == pn and p.expiry == exp
                               for p in positions):
                        for ot in ["P", "C"]:
                            opt = select_s4(ef, ot)
                            if opt is None:
                                continue
                            cost = opt["option_close"] * d["mult"]
                            n_s4_products = max(len(cur_pool), 1)
                            qty = calc_s4_size(nav_after_mtm, s4_prem, n_s4_products, cost)
                            pending_opens.append({
                                "strat": "S4", "product": pn, "code": opt["option_code"],
                                "ot": ot, "strike": opt["strike"],
                                "fallback": opt["option_close"],
                                "n": qty, "mult": d["mult"], "expiry": exp,
                                "mr": d["mr"], "liq": "deep_otm", "role": "buy",
                                "spot": opt["spot_close"], "reason": "new_s4",
                            })

        # ── 对冲信号（Phase 3 末尾）──
        if hedge_enabled and hedge_loader:
            pending_hedges = _project_hedge_orders(
                date, positions, pending_closes, pending_opens,
                hedge_positions, hedge_loader, pdata)

        # ── NAV ──
        cum = (records[-1]["cum_pnl"] if records else 0) + day_pnl
        nav = CAPITAL + cum
        tm = sum(p.cur_margin() for p in positions if p.role == "sell")

        # 相关性检查（每20天）
        corr_eff_n = 0
        corr_warning = ""
        if di % 20 == 0 and positions:
            corr_result = corr_monitor.daily_check(date, positions, nav)
            corr_eff_n = corr_result["effective_n"]
            if corr_result["warnings"]:
                corr_warning = "; ".join(corr_result["warnings"])

        records.append({
            "date": date_str, "daily_pnl": day_pnl, "cum_pnl": cum,
            "nav": nav, "pnl_s1": pnl_s1, "pnl_s3": pnl_s3, "pnl_s4": pnl_s4,
            "margin_pct": tm / max(nav, 1),
            "n_positions": len(positions),
            "n_active_products": len(monthly_product_pool.get(cur_month, set())),
            "n_pending_open": len(pending_opens),
            "n_pending_close": len(pending_closes),
            # Greeks
            "gross_cash_delta_pct": pct_delta,
            "net_cash_delta_pct": pct_net_delta,
            "cash_delta_pct": pct_delta,  # 兼容旧版
            "cash_vega_pct": pct_vega,
            "cash_gamma_pct": pct_gamma,
            "cash_theta": total_cash_theta,
            "greeks_warning": greeks_warning,
            # 对冲
            "hedge_pnl": hedge_pnl,
            "hedge_notional": sum(hp.notional() for hp in hedge_positions.values()),
            "n_hedge_positions": len(hedge_positions),
            # 手续费
            "fee": day_fee,
            # 压力测试
            "stress_5pct": stress_results.get("s-5_iv10", 0),
            "stress_3pct": stress_results.get("s-3_iv5", 0),
            "stress_10pct": stress_results.get("s-5_iv20", 0),
            # 相关性
            "effective_n": corr_eff_n,
            "corr_warning": corr_warning,
        })

    print(f"\n执行价格: {dict(exec_stats)}")
    if use_intraday_tp:
        print(f"盘中止盈: {itp_count}次")
    if hedge_enabled:
        print(f"对冲持仓: {len(hedge_positions)}个family")

    # 清理
    if hedge_loader:
        hedge_loader.close()

    return pd.DataFrame(records), order_log, snapshots



# ============================================================
# Main
# ============================================================
def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="分钟级数据回测 v2")
    parser.add_argument("--vwap-window", type=int, default=10,
                        help="VWAP/TWAP窗口（开盘后N分钟），默认10")
    parser.add_argument("--exec-method", type=str, default="vwap",
                        choices=["vwap", "twap", "open"],
                        help="执行价格方法: vwap/twap/open")
    parser.add_argument("--intraday-tp", action="store_true",
                        help="启用盘中止盈")
    parser.add_argument("--no-minute", action="store_true",
                        help="不用分钟数据（对比基准）")
    parser.add_argument("--no-agg-db", action="store_true",
                        help="不用聚合数据库，退化为benchmark.db模式")
    parser.add_argument("--start", type=str, default=START_DATE,
                        help="起始日期（如 2024-01-01），默认不限制")
    parser.add_argument("--no-hedge", action="store_true",
                        help="禁用对冲层")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    use_agg_db = (not args.no_agg_db) and HAS_LOADER_V2
    params_override = {}
    if args.no_hedge:
        params_override["hedge_enabled"] = False
    p = load_runtime_params(params_override)
    hedge_on = p.get("hedge_enabled", True)

    print("=" * 70)
    print("  分钟级数据回测引擎 v2")
    print(f"  数据源: {'聚合数据库' if use_agg_db else 'benchmark.db'}")
    print(f"  执行: {args.exec_method.upper()} {args.vwap_window}min | "
          f"盘中止盈: {'ON' if args.intraday_tp else 'OFF'} | "
          f"对冲: {'ON' if hedge_on else 'OFF'}")
    if args.start:
        print(f"  起始日期: {args.start}")
    print("=" * 70)

    # 品种排名
    if use_agg_db:
        # v2模式：品种在 load_all_products 中自动加载，这里只做初始排名展示
        try:
            from data_loader_v2 import scan_and_rank_v2
            ranked = scan_and_rank_v2(sort_by="oi")
        except Exception:
            ranked = scan_and_rank(sort_by="oi")
    else:
        ranked = scan_and_rank(sort_by="oi")
    top20 = ranked[:20]
    product_list = [r["product_tuple"] for r in top20]
    product_names = [r["name"] for r in top20]
    product_exchanges = {r["name"]: r.get("exchange", "UNKNOWN") for r in top20}
    print(f"\n品种({len(top20)}): {', '.join(product_names[:10])}...")

    # 分钟数据加载器
    ml = None
    if not args.no_minute and os.path.isdir(MINUTE_DB_DIR):
        ml = MinuteDataLoader(MINUTE_DB_DIR)
        print(f"分钟数据: {MINUTE_DB_DIR}")
        print("预加载分钟数据...")
        ml.preload(start_date=args.start)
    else:
        print("分钟数据: 未找到，退化为日频")

    t0 = time.time()
    nav_df, order_log, snapshots = run_minute_backtest(
        products=product_list, s4_products=product_names,
        enable_s4=True, start_date=args.start,
        params=params_override,
        vwap_window=args.vwap_window, use_intraday_tp=args.intraday_tp,
        minute_loader=ml, product_exchanges=product_exchanges,
        exec_method=args.exec_method, use_agg_db=use_agg_db,
    )
    elapsed = time.time() - t0

    s = calc_stats(nav_df["nav"].values)
    p = load_runtime_params(params_override)
    CAPITAL = p["capital"]

    print(f"\n耗时: {elapsed:.0f}s")
    print(f"年化{s['ann_return']:+.2%} | 回撤{s['max_dd']:.2%} | "
          f"夏普{s['sharpe']:.2f} | 卡玛{s['calmar']:.2f}")

    # Greeks统计
    if "cash_vega_pct" in nav_df.columns:
        print(f"\nGreeks统计:")
        print(f"  Cash Vega: 均值{nav_df['cash_vega_pct'].mean():.2%}, "
              f"最大{nav_df['cash_vega_pct'].max():.2%}, "
              f"超1.5%天数={(nav_df['cash_vega_pct'] > 0.015).sum()}")
        print(f"  Gross Delta: 均值{nav_df['gross_cash_delta_pct'].mean():.2%}, "
              f"最大{nav_df['gross_cash_delta_pct'].max():.2%}")
        if "net_cash_delta_pct" in nav_df.columns:
            print(f"  Net Delta(含对冲): 均值{nav_df['net_cash_delta_pct'].mean():.2%}, "
                  f"最大{nav_df['net_cash_delta_pct'].max():.2%}")
    if "fee" in nav_df.columns:
        total_fee = nav_df["fee"].sum()
        print(f"  累积手续费: {total_fee:,.0f}元 ({total_fee/CAPITAL:.2%} of初始资金)")
    if "hedge_pnl" in nav_df.columns:
        total_hedge_pnl = nav_df["hedge_pnl"].sum()
        print(f"  对冲累积PnL: {total_hedge_pnl:,.0f}元")
    if "greeks_warning" in nav_df.columns:
        warnings = nav_df[nav_df["greeks_warning"] != ""]
        if len(warnings) > 0:
            print(f"  Greeks预警天数: {len(warnings)}")

    # 保存NAV
    tag = f"{args.exec_method}{args.vwap_window}"
    if args.intraday_tp:
        tag += "_itp"
    if args.no_minute:
        tag = "no_minute"
    if args.no_agg_db:
        tag += "_benchmark"
    nav_df.to_csv(os.path.join(OUTPUT_DIR, f"nav_{tag}.csv"), index=False)

    # 保存订单明细
    orders_df = order_log.to_dataframe()
    orders_df.to_csv(os.path.join(OUTPUT_DIR, f"orders_{tag}.csv"), index=False)
    print(f"订单: {len(orders_df)}笔 → output/orders_{tag}.csv")

    # 按天输出订单文件
    if not orders_df.empty and "exec_date" in orders_df.columns:
        daily_order_dir = os.path.join(OUTPUT_DIR, "daily_orders")
        os.makedirs(daily_order_dir, exist_ok=True)
        for exec_date, day_orders in orders_df.groupby("exec_date"):
            if exec_date:
                day_orders.to_csv(
                    os.path.join(daily_order_dir, f"orders_{exec_date.replace('-','')}.csv"),
                    index=False)
        n_days = orders_df["exec_date"].nunique()
        print(f"每日订单: {n_days}天 → output/daily_orders/")

    # 订单统计
    if not orders_df.empty:
        print(f"\n订单统计:")
        print(f"  开仓: {(orders_df['action']=='open').sum()}笔")
        print(f"  平仓: {(orders_df['action']=='close').sum()}笔")
        if "hedge" in orders_df["action"].values:
            print(f"  对冲: {(orders_df['action']=='hedge').sum()}笔")
        print(f"  执行来源: {orders_df['exec_source'].value_counts().to_dict()}")
        close_reasons = orders_df[orders_df['action']=='close']['reason'].value_counts()
        print(f"  平仓原因: {close_reasons.head(10).to_dict()}")

    if ml:
        ml.close()

    # 生成报告和图表
    generate_report(nav_df, orders_df, s, tag, elapsed, args, CAPITAL)

    # 保存持仓快照
    snapshot_path = os.path.join(OUTPUT_DIR, f"snapshots_{tag}.json")
    filtered = {k: v for k, v in snapshots.items() if v}
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=1)
    print(f"持仓快照: {len(filtered)}天 → {snapshot_path}")



def generate_report(nav_df, orders_df, stats, tag, elapsed, args, capital):
    """生成回测报告（Markdown）和净值图"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    s = stats
    dates = pd.to_datetime(nav_df["date"])

    # ── 1. 净值图（4子图）──
    fig, axes = plt.subplots(4, 1, figsize=(14, 16),
                             gridspec_kw={"height_ratios": [3, 1, 1, 1]})

    # 1a. NAV曲线 + 回撤
    ax1 = axes[0]
    nav = nav_df["nav"].values
    ax1.plot(dates, nav / 1e6, "b-", linewidth=1.2, label="NAV")
    ax1.set_ylabel("NAV (百万)")
    ax1.set_title(f"回测净值曲线 | 年化{s['ann_return']:+.1%} 回撤{s['max_dd']:.1%} "
                  f"夏普{s['sharpe']:.2f} 卡玛{s['calmar']:.2f}")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / np.where(peak > 0, peak, 1)
    ax1_dd = ax1.twinx()
    ax1_dd.fill_between(dates, dd * 100, 0, alpha=0.15, color="red", label="回撤")
    ax1_dd.set_ylabel("回撤 (%)")
    ax1_dd.set_ylim(min(dd * 100) * 1.5, 5)
    ax1_dd.legend(loc="lower left")

    # 1b. 每日PnL（按策略分色）
    ax2 = axes[1]
    ax2.bar(dates, nav_df["pnl_s1"] / 1e4, width=1, alpha=0.7, label="S1", color="steelblue")
    ax2.bar(dates, nav_df["pnl_s3"] / 1e4, width=1, alpha=0.7, label="S3",
            bottom=nav_df["pnl_s1"] / 1e4, color="orange")
    ax2.bar(dates, nav_df["pnl_s4"] / 1e4, width=1, alpha=0.7, label="S4",
            bottom=(nav_df["pnl_s1"] + nav_df["pnl_s3"]) / 1e4, color="green")
    ax2.set_ylabel("日PnL (万)")
    ax2.legend(loc="upper left", ncol=3, fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.axhline(0, color="black", linewidth=0.5)

    # 1c. 保证金率
    ax3 = axes[2]
    ax3.fill_between(dates, nav_df["margin_pct"] * 100, alpha=0.5, color="purple")
    ax3.axhline(50, color="red", linestyle="--", linewidth=0.8, label="上限50%")
    ax3.set_ylabel("保证金率 (%)")
    ax3.set_ylim(0, 70)
    ax3.legend(loc="upper left", fontsize=8)
    ax3.grid(True, alpha=0.3)

    # 1d. Cash Vega
    ax4 = axes[3]
    if "cash_vega_pct" in nav_df.columns:
        ax4.fill_between(dates, nav_df["cash_vega_pct"] * 100, alpha=0.5, color="teal")
        ax4.axhline(1.5, color="orange", linestyle="--", linewidth=0.8, label="预警1.5%")
        ax4.axhline(2.0, color="red", linestyle="--", linewidth=0.8, label="硬止损2%")
    ax4.set_ylabel("Cash Vega (%NAV)")
    ax4.set_xlabel("日期")
    ax4.legend(loc="upper left", fontsize=8)
    ax4.grid(True, alpha=0.3)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    plt.tight_layout()
    chart_path = os.path.join(OUTPUT_DIR, f"chart_{tag}.png")
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n净值图: {chart_path}")

    # ── 2. 组合Greeks变化图（4子图）──
    fig2, axes2 = plt.subplots(4, 1, figsize=(14, 14), sharex=True)

    # Cash Delta（gross vs net）
    ax = axes2[0]
    if "gross_cash_delta_pct" in nav_df.columns:
        ax.fill_between(dates, nav_df["gross_cash_delta_pct"] * 100,
                        alpha=0.3, color="royalblue", label="Gross Delta")
    if "net_cash_delta_pct" in nav_df.columns:
        ax.plot(dates, nav_df["net_cash_delta_pct"] * 100,
                color="darkblue", linewidth=0.8, label="Net Delta(含对冲)")
    ax.axhline(15, color="orange", ls="--", lw=0.8, label="预警15%")
    ax.axhline(-15, color="orange", ls="--", lw=0.8)
    ax.axhline(0, color="black", lw=0.5)
    ax.set_ylabel("Cash Delta (%NAV)")
    ax.set_title("组合Greeks每日变化")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)

    # Cash Gamma
    ax = axes2[1]
    ax.fill_between(dates, nav_df["cash_gamma_pct"] * 100, alpha=0.6, color="darkorange")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_ylabel("Cash Gamma (%NAV)")
    ax.grid(True, alpha=0.3)

    # Cash Vega
    ax = axes2[2]
    ax.fill_between(dates, nav_df["cash_vega_pct"] * 100, alpha=0.6, color="teal")
    ax.axhline(1.5, color="orange", ls="--", lw=0.8, label="预警1.5%")
    ax.axhline(2.0, color="red", ls="--", lw=0.8, label="硬止损2%")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_ylabel("Cash Vega (%NAV)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)

    # Cash Theta
    ax = axes2[3]
    ax.fill_between(dates, nav_df["cash_theta"] / 1e4, alpha=0.6, color="crimson")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_ylabel("Cash Theta (万/天)")
    ax.set_xlabel("日期")
    ax.grid(True, alpha=0.3)

    for ax in axes2:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    plt.tight_layout()
    greeks_path = os.path.join(OUTPUT_DIR, f"greeks_{tag}.png")
    plt.savefig(greeks_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Greeks图: {greeks_path}")

    # ── 3. 策略对比图 ──
    fig3, ax3 = plt.subplots(figsize=(14, 6))
    cum_s1 = nav_df["pnl_s1"].cumsum() / 1e4
    cum_s3 = nav_df["pnl_s3"].cumsum() / 1e4
    cum_s4 = nav_df["pnl_s4"].cumsum() / 1e4
    ax3.plot(dates, cum_s1, label=f"S1 ({cum_s1.iloc[-1]:+.1f}万)", linewidth=1.2)
    ax3.plot(dates, cum_s3, label=f"S3 ({cum_s3.iloc[-1]:+.1f}万)", linewidth=1.2)
    ax3.plot(dates, cum_s4, label=f"S4 ({cum_s4.iloc[-1]:+.1f}万)", linewidth=1.2)
    if "fee" in nav_df.columns:
        cum_fee = -nav_df["fee"].cumsum() / 1e4
        ax3.plot(dates, cum_fee, label=f"手续费 ({cum_fee.iloc[-1]:+.1f}万)",
                 linewidth=1, linestyle="--", color="gray")
    if "hedge_pnl" in nav_df.columns:
        cum_hedge = nav_df["hedge_pnl"].cumsum() / 1e4
        ax3.plot(dates, cum_hedge, label=f"对冲 ({cum_hedge.iloc[-1]:+.1f}万)",
                 linewidth=1, linestyle="--", color="brown")
    ax3.axhline(0, color="black", linewidth=0.5)
    ax3.set_ylabel("累积PnL (万)")
    ax3.set_title("各策略累积PnL对比")
    ax3.legend(loc="best", fontsize=9)
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.tight_layout()
    compare_path = os.path.join(OUTPUT_DIR, f"compare_{tag}.png")
    plt.savefig(compare_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"策略对比图: {compare_path}")

    # ── 4. Markdown报告 ──
    report_path = os.path.join(OUTPUT_DIR, f"report_{tag}.md")
    total_fee = nav_df["fee"].sum() if "fee" in nav_df.columns else 0
    total_hedge = nav_df["hedge_pnl"].sum() if "hedge_pnl" in nav_df.columns else 0
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 回测报告 — {tag}\n\n")
        f.write(f"**日期范围**: {nav_df['date'].iloc[0]} ~ {nav_df['date'].iloc[-1]} "
                f"({len(nav_df)}天)\n")
        f.write(f"**耗时**: {elapsed:.0f}秒\n\n")
        f.write(f"## 核心指标\n\n")
        f.write(f"| 指标 | 值 |\n|------|------|\n")
        f.write(f"| 年化收益 | {s['ann_return']:+.2%} |\n")
        f.write(f"| 最大回撤 | {s['max_dd']:.2%} |\n")
        f.write(f"| 夏普比率 | {s['sharpe']:.2f} |\n")
        f.write(f"| 卡玛比率 | {s['calmar']:.2f} |\n")
        f.write(f"| 累积手续费 | {total_fee:,.0f}元 |\n")
        f.write(f"| 对冲累积PnL | {total_hedge:,.0f}元 |\n")
        f.write(f"\n## 策略PnL\n\n")
        f.write(f"| 策略 | 累积PnL |\n|------|------|\n")
        f.write(f"| S1 | {cum_s1.iloc[-1]:+,.1f}万 |\n")
        f.write(f"| S3 | {cum_s3.iloc[-1]:+,.1f}万 |\n")
        f.write(f"| S4 | {cum_s4.iloc[-1]:+,.1f}万 |\n")
        f.write(f"\n![净值图](chart_{tag}.png)\n")
        f.write(f"![Greeks图](greeks_{tag}.png)\n")
        f.write(f"![策略对比](compare_{tag}.png)\n")
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
