"""
Parquet 数据加载模块 — 逐分钟回测引擎的数据层

从 Parquet 分钟线数据源按日分片加载，管理内存。
所有 Parquet 列为 string 类型（Spark 导出），需要显式类型转换。

模块：
  ContractMaster  — 合约属性查找表（启动时一次性加载）
  ParquetDayLoader — 按日分片加载分钟数据
  DaySlice — 单日数据容器与查询接口

数据路径通过环境变量 PARQUET_DATA_DIR 配置，
默认 /macro/home/lxy/yy_2_lxy_20260415/
"""
import os
import re
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

logger = logging.getLogger(__name__)

# 默认 Parquet 数据目录
DEFAULT_DATA_DIR = os.environ.get(
    "PARQUET_DATA_DIR",
    "/macro/home/lxy/yy_2_lxy_20260415/"
)

# Parquet 文件名
OPTION_PARQUET = "OPTION1MINRESULT.parquet"
FUTURE_PARQUET = "FUTURE1MINRESULT.parquet"
ETF_PARQUET = "ETF1MINRESULT.parquet"
CONTRACT_PARQUET = "CONTRACTINFORESULT.parquet"

# 期权代码正则：提取品种根码和月份
# 例: "DCE.m2409-C-3100" → root="m", month="2409"
_OPT_CODE_RE = re.compile(
    r"^(?:[A-Z]+\.)?([a-zA-Z]+)(\d{4})-([CP])-"
)
# 期货代码正则：提取品种根码和月份
# 例: "DCE.m2409" → root="m", month="2409"
_FUT_CODE_RE = re.compile(
    r"^(?:[A-Z]+\.)?([a-zA-Z]+)(\d{4})$"
)

# ETF 期权的标的映射（underlying_code → ETF代码）
# contract_master 中 ETF 期权的 underlying_code 是合约编号，需要映射
ETF_UNDERLYING_MAP = {
    "510300": "510300", "510050": "510050", "510500": "510500",
    "588000": "588000", "159919": "159919", "159915": "159915",
    "588080": "588080", "159922": "159922", "510100": "510100",
}

# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _parse_expiry(val):
    """解析到期日字符串，支持 YYYY-MM-DD 和 YYYYMMDD 两种格式"""
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _safe_float(val, default=np.nan):
    """安全转换为 float"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        v = float(val)
        return v if np.isfinite(v) else default
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    """安全转换为 int"""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# ContractMaster — 合约属性查找表
# ══════════════════════════════════════════════════════════════════════════════

class ContractMaster:
    """
    合约属性查找表，启动时一次性从 CONTRACTINFORESULT.parquet 加载。

    将所有 string 列转换为正确类型：
      strike_price → float, expiry_date → date, contract_multiplier → int,
      option_type "认购"/"认沽" → "C"/"P"
    """

    def __init__(self, parquet_path: str = None):
        """
        从 Parquet 加载合约属性。

        Args:
            parquet_path: CONTRACTINFORESULT.parquet 的完整路径，
                          None 时从 DEFAULT_DATA_DIR 拼接
        """
        if parquet_path is None:
            parquet_path = os.path.join(DEFAULT_DATA_DIR, CONTRACT_PARQUET)
        if not os.path.exists(parquet_path):
            raise FileNotFoundError(f"合约属性文件不存在: {parquet_path}")

        logger.info("加载合约属性: %s", parquet_path)
        raw = pd.read_parquet(parquet_path)
        logger.info("  原始行数: %d, 列: %s", len(raw), list(raw.columns))

        # 构建查找字典：contract_code → 属性 dict
        self._lookup_dict = {}
        self._product_roots = {}  # contract_code → product_root
        n_ok, n_skip = 0, 0

        for _, row in raw.iterrows():
            code = str(row.get("contract_code", row.get("code", ""))).strip()
            if not code:
                n_skip += 1
                continue

            expiry = _parse_expiry(row.get("expiry_date"))
            if expiry is None:
                # 尝试 last_exercise_date
                expiry = _parse_expiry(row.get("last_exercise_date"))
            if expiry is None:
                logger.debug("  跳过合约 %s: 无法解析到期日", code)
                n_skip += 1
                continue

            strike = _safe_float(row.get("strike_price"))
            if np.isnan(strike) or strike <= 0:
                n_skip += 1
                continue

            # option_type: "认购"→"C", "认沽"→"P"
            ot_raw = str(row.get("option_type", "")).strip()
            if ot_raw in ("C", "P"):
                ot = ot_raw
            elif "认购" in ot_raw or "call" in ot_raw.lower():
                ot = "C"
            elif "认沽" in ot_raw or "put" in ot_raw.lower():
                ot = "P"
            else:
                # 尝试从合约代码提取
                if "-C-" in code:
                    ot = "C"
                elif "-P-" in code:
                    ot = "P"
                else:
                    n_skip += 1
                    continue

            mult = _safe_int(row.get("contract_multiplier"), 1)
            if mult <= 0:
                mult = 1

            exchange = str(row.get("exchange_code", "")).strip()
            min_tick = _safe_float(row.get("min_price_tick"), 0.01)
            underlying = str(row.get("underlying_code", "")).strip() if pd.notna(row.get("underlying_code")) else None

            self._lookup_dict[code] = {
                "expiry_date": expiry,
                "strike_price": strike,
                "option_type": ot,
                "contract_multiplier": mult,
                "exchange_code": exchange,
                "min_price_tick": min_tick,
                "underlying_code": underlying,
            }

            # 缓存品种根码
            root = self.get_product_root(code)
            if root:
                self._product_roots[code] = root

            n_ok += 1

        logger.info("  加载完成: %d 个合约, 跳过 %d", n_ok, n_skip)

    def lookup(self, contract_code: str):
        """
        查找合约属性。

        Returns:
            dict | None: {expiry_date, strike_price, option_type,
                          contract_multiplier, exchange_code, min_price_tick,
                          underlying_code}
        """
        return self._lookup_dict.get(contract_code)

    def calc_dte(self, contract_code: str, current_date) -> int:
        """
        计算 DTE = expiry_date - current_date（日历天）。

        Args:
            current_date: date 或 str "YYYY-MM-DD"
        Returns:
            int: DTE，找不到合约返回 -1
        """
        info = self._lookup_dict.get(contract_code)
        if info is None:
            return -1
        if isinstance(current_date, str):
            current_date = datetime.strptime(current_date[:10], "%Y-%m-%d").date()
        elif isinstance(current_date, datetime):
            current_date = current_date.date()
        return (info["expiry_date"] - current_date).days

    def get_underlying_month(self, contract_code: str) -> str:
        """
        从合约代码提取到期月份。

        例: 'DCE.m2409-C-3100' → 'm2409'
            'CZCE.SR403-C-6500' → 'SR403'
        """
        m = _OPT_CODE_RE.match(contract_code)
        if m:
            return m.group(1) + m.group(2)
        # fallback: 从 underlying_code 字段
        info = self._lookup_dict.get(contract_code)
        if info and info.get("underlying_code"):
            return info["underlying_code"]
        return ""

    def get_product_root(self, contract_code: str) -> str:
        """
        提取品种根码。

        例: 'DCE.m2409-C-3100' → 'm'
            'CFFEX.IO2409-C-4000' → 'IO'
        """
        # 先查缓存
        if contract_code in self._product_roots:
            return self._product_roots[contract_code]
        m = _OPT_CODE_RE.match(contract_code)
        if m:
            return m.group(1)
        # 去掉交易所前缀后再试
        if "." in contract_code:
            code_no_exch = contract_code.split(".", 1)[1]
            m = _OPT_CODE_RE.match(code_no_exch)
            if m:
                return m.group(1)
        return ""

    def get_exchange(self, contract_code: str) -> str:
        """获取交易所代码"""
        info = self._lookup_dict.get(contract_code)
        if info:
            return info["exchange_code"]
        # 从合约代码前缀提取
        if "." in contract_code:
            return contract_code.split(".")[0]
        return ""

    @property
    def size(self) -> int:
        """合约总数"""
        return len(self._lookup_dict)

    def all_codes(self):
        """返回所有合约代码"""
        return list(self._lookup_dict.keys())


# ══════════════════════════════════════════════════════════════════════════════
# DaySlice — 单日数据容器
# ══════════════════════════════════════════════════════════════════════════════

class DaySlice:
    """
    某一交易日的全部分钟数据（期权+期货+ETF）。

    提供单点查询、分钟级标的价格序列构建、日频聚合等接口。
    """

    def __init__(self, option_bars, futures_bars, etf_bars, date_str,
                 contract_master):
        """
        Args:
            option_bars: 期权分钟K线 DataFrame（已转数值类型）
            futures_bars: 期货分钟K线 DataFrame
            etf_bars: ETF分钟K线 DataFrame
            date_str: 交易日期 "YYYY-MM-DD"
            contract_master: ContractMaster 实例
        """
        self.option_bars = option_bars
        self.futures_bars = futures_bars
        self.etf_bars = etf_bars
        self.date_str = date_str
        self._cm = contract_master

        # 构建索引加速查询
        self._opt_idx = {}  # (timestamp, code) → row dict
        self._fut_idx = {}  # (timestamp, code) → close
        self._etf_idx = {}  # (timestamp, code) → close

        # 缓存的标的价格序列
        self._spot_cache = {}

        self._build_indices()

    def _build_indices(self):
        """构建内存索引，加速单点查询（向量化构建，避免逐行迭代）"""
        if self.option_bars is not None and len(self.option_bars) > 0:
            df = self.option_bars
            for ts, code, o, h, l, c, v in zip(
                df["datetime"].values, df["code"].values,
                df["open"].values, df["high"].values,
                df["low"].values, df["close"].values,
                df["volume"].values
            ):
                self._opt_idx[(str(ts), str(code))] = {
                    "open": o, "high": h, "low": l, "close": c, "volume": v,
                }

        if self.futures_bars is not None and len(self.futures_bars) > 0:
            df = self.futures_bars
            for ts, code, c in zip(
                df["datetime"].values, df["code"].values, df["close"].values
            ):
                self._fut_idx[(str(ts), str(code))] = c

        if self.etf_bars is not None and len(self.etf_bars) > 0:
            df = self.etf_bars
            for ts, code, c in zip(
                df["datetime"].values, df["code"].values, df["close"].values
            ):
                self._etf_idx[(str(ts), str(code))] = c

    def get_minute_timestamps(self):
        """返回该日所有分钟时间戳（升序去重）"""
        ts_set = set()
        if self.option_bars is not None and len(self.option_bars) > 0:
            ts_set.update(self.option_bars["datetime"].unique())
        if self.futures_bars is not None and len(self.futures_bars) > 0:
            ts_set.update(self.futures_bars["datetime"].unique())
        if self.etf_bars is not None and len(self.etf_bars) > 0:
            ts_set.update(self.etf_bars["datetime"].unique())
        return sorted(ts_set)

    def get_option_bar(self, timestamp, contract_code):
        """
        获取某合约某分钟的K线数据。

        Returns:
            dict | None: {open, high, low, close, volume}
        """
        return self._opt_idx.get((str(timestamp), contract_code))

    def get_spot_price(self, timestamp, underlying_code):
        """
        获取某标的某分钟的价格（期货close或ETF close）。

        Args:
            underlying_code: 期货合约代码（如 "DCE.m2409"）或 ETF 代码
        Returns:
            float | None
        """
        ts = str(timestamp)
        # 先查期货
        v = self._fut_idx.get((ts, underlying_code))
        if v is not None and v > 0:
            return v
        # 查 ETF
        v = self._etf_idx.get((ts, underlying_code))
        if v is not None and v > 0:
            return v
        return None

    def build_spot_series(self, underlying_code):
        """
        构建某标的的分钟级价格序列，用 pandas ffill() 向前填充缺失分钟。

        Returns:
            dict: {timestamp_str: float}，每个分钟都有值
        """
        if underlying_code in self._spot_cache:
            return self._spot_cache[underlying_code]

        timestamps = self.get_minute_timestamps()
        if not timestamps:
            return {}

        # 收集原始价格
        prices = {}
        for ts in timestamps:
            p = self.get_spot_price(ts, underlying_code)
            if p is not None and p > 0:
                prices[ts] = p

        if not prices:
            self._spot_cache[underlying_code] = {}
            return {}

        # 用 pandas ffill 填充
        s = pd.Series(index=timestamps, dtype=float)
        for ts, p in prices.items():
            s[ts] = p
        s = s.ffill()
        # 第一个值之前的 NaN 用第一个有效值 bfill
        s = s.bfill()

        result = s.to_dict()
        self._spot_cache[underlying_code] = result
        return result

    def _match_futures_code(self, option_code):
        """
        从期权合约代码匹配同月期货合约代码。

        例: "DCE.m2409-C-3100" → 在 futures_bars 中找 "DCE.m2409"
            或从 contract_master 的 underlying_code 字段获取
        """
        # 方法1：从 contract_master 获取 underlying_code
        info = self._cm.lookup(option_code)
        if info and info.get("underlying_code"):
            uc = info["underlying_code"]
            # 检查期货数据中是否有这个代码
            if self.futures_bars is not None and len(self.futures_bars) > 0:
                # 尝试精确匹配
                if uc in self.futures_bars["code"].values:
                    return uc
                # 尝试带交易所前缀
                exchange = info.get("exchange_code", "")
                full_code = f"{exchange}.{uc}" if exchange else uc
                if full_code in self.futures_bars["code"].values:
                    return full_code

        # 方法2：从期权代码提取月份，拼接期货代码
        m = _OPT_CODE_RE.match(option_code)
        if m:
            root, month = m.group(1), m.group(2)
            # 期货代码可能是大写或小写
            candidates = [
                f"{root}{month}",
                f"{root.upper()}{month}",
                f"{root.lower()}{month}",
            ]
            # 加交易所前缀
            if "." in option_code:
                exch = option_code.split(".")[0]
                candidates = [f"{exch}.{c}" for c in candidates] + candidates

            if self.futures_bars is not None and len(self.futures_bars) > 0:
                fut_codes = set(self.futures_bars["code"].values)
                for c in candidates:
                    if c in fut_codes:
                        return c

        return None

    def calc_vwap(self, contract_code, window=10):
        """
        计算开盘后 N 分钟 VWAP = sum(close×volume) / sum(volume)。

        volume 全为 0 时 fallback 到首根 close。
        """
        if self.option_bars is None or len(self.option_bars) == 0:
            return None

        bars = self.option_bars[self.option_bars["code"] == contract_code]
        if bars.empty:
            return None

        bars = bars.sort_values("datetime").head(window)
        valid = bars[bars["volume"] > 0]

        if valid.empty:
            # fallback: 首根 close
            return bars.iloc[0]["close"] if len(bars) > 0 else None

        total_vol = valid["volume"].sum()
        if total_vol <= 0:
            return valid.iloc[0]["close"]

        return (valid["close"] * valid["volume"]).sum() / total_vol

    def aggregate_daily(self, contract_master=None):
        """
        从分钟K线聚合日频数据，格式兼容 strategy_rules 选腿函数。

        性能优化：IV/Greeks 只对候选合约计算（先按 moneyness 粗筛 OTM 合约）。

        Returns:
            pd.DataFrame: 包含 option_code, strike, option_type, option_close,
                          spot_close, dte, implied_vol, delta, volume, product, exchange
        """
        cm = contract_master or self._cm
        if self.option_bars is None or len(self.option_bars) == 0:
            return pd.DataFrame()

        current_date = datetime.strptime(self.date_str, "%Y-%m-%d").date()

        # 按合约聚合 OHLCV
        grouped = self.option_bars.groupby("code")
        records = []

        for code, grp in grouped:
            grp_sorted = grp.sort_values("datetime")
            valid = grp_sorted[grp_sorted["volume"] > 0]
            if valid.empty:
                continue

            info = cm.lookup(code)
            if info is None:
                continue

            dte = (info["expiry_date"] - current_date).days
            if dte <= 0:
                continue

            # OHLCV 聚合
            opt_close = valid.iloc[-1]["close"]
            volume = int(valid["volume"].sum())

            # 标的价格：同月期货
            fut_code = self._match_futures_code(code)
            spot_close = None
            if fut_code:
                spot_series = self.build_spot_series(fut_code)
                if spot_series:
                    # 取收盘价（最后一个时间戳）
                    last_ts = sorted(spot_series.keys())[-1]
                    spot_close = spot_series[last_ts]

            if spot_close is None or spot_close <= 0:
                continue

            moneyness = info["strike_price"] / max(spot_close, 1e-10)

            records.append({
                "option_code": code,
                "strike": info["strike_price"],
                "option_type": info["option_type"],
                "option_close": opt_close,
                "spot_close": spot_close,
                "dte": dte,
                "moneyness": moneyness,
                "volume": volume,
                "product": cm.get_product_root(code),
                "exchange": info["exchange_code"],
                "expiry_date": info["expiry_date"],
                "multiplier": info["contract_multiplier"],
                "min_price_tick": info["min_price_tick"],
                "underlying_code": info.get("underlying_code"),
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        # 候选合约粗筛：只对 OTM 合约计算 IV/delta
        # Put: moneyness < 1.0, Call: moneyness > 1.0
        is_otm = (
            ((df["option_type"] == "P") & (df["moneyness"] < 1.0)) |
            ((df["option_type"] == "C") & (df["moneyness"] > 1.0))
        )
        # 进一步筛选：moneyness 在合理范围内（0.7~1.3）
        in_range = df["moneyness"].between(0.70, 1.30)
        candidate_mask = is_otm & in_range

        # 初始化 IV/delta 为 NaN
        df["implied_vol"] = np.nan
        df["delta"] = np.nan

        # 只对候选合约计算 IV 和 delta
        candidates = df[candidate_mask]
        if len(candidates) > 0:
            try:
                from option_calc import calc_iv_batch, calc_greeks_batch_vectorized
            except ImportError:
                logger.warning("  option_calc 导入失败，跳过 IV/delta 计算")
                return df

            iv_series = calc_iv_batch(candidates,
                                      price_col="option_close",
                                      spot_col="spot_close",
                                      strike_col="strike",
                                      dte_col="dte",
                                      otype_col="option_type")
            df.loc[candidate_mask, "implied_vol"] = iv_series.values

            # Greeks：只对有有效 IV 的合约计算
            has_iv = candidate_mask & df["implied_vol"].notna() & (df["implied_vol"] > 0)
            if has_iv.any():
                greeks_df = calc_greeks_batch_vectorized(
                    df[has_iv],
                    spot_col="spot_close",
                    strike_col="strike",
                    dte_col="dte",
                    iv_col="implied_vol",
                    otype_col="option_type"
                )
                df.loc[has_iv, "delta"] = greeks_df["delta"].values

        return df

    def release(self):
        """释放内存引用"""
        self.option_bars = None
        self.futures_bars = None
        self.etf_bars = None
        self._opt_idx.clear()
        self._fut_idx.clear()
        self._etf_idx.clear()
        self._spot_cache.clear()


# ══════════════════════════════════════════════════════════════════════════════
# ParquetDayLoader — 按日分片加载 Parquet 分钟数据
# ══════════════════════════════════════════════════════════════════════════════

class ParquetDayLoader:
    """
    按日分片加载 Parquet 分钟数据。

    使用 PyArrow dataset API 打开文件句柄（不读数据），
    load_day() 时用 string prefix 过滤 datetime 列，谓词下推跳过无关 row_group。
    """

    def __init__(self, data_dir=None, contract_master=None):
        """
        Args:
            data_dir: Parquet 文件目录，None 时用 PARQUET_DATA_DIR 环境变量
            contract_master: ContractMaster 实例，None 时自动加载
        """
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        if not os.path.isdir(self.data_dir):
            raise FileNotFoundError(f"数据目录不存在: {self.data_dir}")

        # 合约属性
        if contract_master is None:
            cm_path = os.path.join(self.data_dir, CONTRACT_PARQUET)
            self.contract_master = ContractMaster(cm_path)
        else:
            self.contract_master = contract_master

        # 打开 dataset 句柄（不读数据）
        self._opt_ds = self._open_dataset(OPTION_PARQUET)
        self._fut_ds = self._open_dataset(FUTURE_PARQUET)
        self._etf_ds = self._open_dataset(ETF_PARQUET)

        # 缓存交易日列表
        self._trading_dates = None

        logger.info("ParquetDayLoader 初始化完成: %s", self.data_dir)
        logger.info("  期权 dataset: %s", "OK" if self._opt_ds else "未找到")
        logger.info("  期货 dataset: %s", "OK" if self._fut_ds else "未找到")
        logger.info("  ETF dataset: %s", "OK" if self._etf_ds else "未找到")

    def _open_dataset(self, filename):
        """打开 Parquet dataset 句柄"""
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            logger.warning("  Parquet 文件不存在: %s", path)
            return None
        try:
            return ds.dataset(path, format="parquet")
        except Exception as exc:
            logger.error("  打开 Parquet 失败: %s — %s", path, exc)
            return None

    def _load_day_from_dataset(self, dataset, date_str):
        """
        从 dataset 加载某一交易日的数据。

        使用 string 比较做谓词下推：
          datetime >= '{date_str} 00:00' AND datetime < '{next_date} 00:00'
        """
        if dataset is None:
            return pd.DataFrame()

        # 计算次日日期
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        next_date_str = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            table = dataset.to_table(
                filter=(
                    (ds.field("datetime") >= f"{date_str} 00:00") &
                    (ds.field("datetime") < f"{next_date_str} 00:00")
                )
            )
            df = table.to_pandas()
        except Exception as exc:
            logger.warning("  加载 %s 数据失败: %s", date_str, exc)
            return pd.DataFrame()

        return df

    def _convert_numeric(self, df, float_cols, int_cols):
        """
        将 string 列转换为数值类型。

        转换失败的行设为 NaN/0，不跳过（保留行以便后续处理）。
        """
        for col in float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df

    def load_day(self, date_str):
        """
        加载某一交易日的全部分钟数据。

        Args:
            date_str: "YYYY-MM-DD"
        Returns:
            DaySlice
        """
        # 期权
        opt_df = self._load_day_from_dataset(self._opt_ds, date_str)
        if len(opt_df) > 0:
            opt_df = self._convert_numeric(
                opt_df,
                float_cols=["open", "high", "low", "close", "open_oi", "close_oi"],
                int_cols=["volume"]
            )
            # 过滤无效行（close <= 0）
            opt_df = opt_df[opt_df["close"] > 0].copy()

        # 期货
        fut_df = self._load_day_from_dataset(self._fut_ds, date_str)
        if len(fut_df) > 0:
            fut_df = self._convert_numeric(
                fut_df,
                float_cols=["open", "high", "low", "close", "money", "open_interest"],
                int_cols=["volume"]
            )
            fut_df = fut_df[fut_df["close"] > 0].copy()

        # ETF
        etf_df = self._load_day_from_dataset(self._etf_ds, date_str)
        if len(etf_df) > 0:
            etf_df = self._convert_numeric(
                etf_df,
                float_cols=["open", "high", "low", "close", "amount"],
                int_cols=["volume"]
            )
            etf_df = etf_df[etf_df["close"] > 0].copy()

        return DaySlice(
            option_bars=opt_df if len(opt_df) > 0 else None,
            futures_bars=fut_df if len(fut_df) > 0 else None,
            etf_bars=etf_df if len(etf_df) > 0 else None,
            date_str=date_str,
            contract_master=self.contract_master,
        )

    def get_trading_dates(self):
        """
        获取所有交易日期列表（升序）。

        从期货数据的 datetime 列提取唯一日期（期货数据量最小，扫描最快）。
        结果缓存，只计算一次。
        """
        if self._trading_dates is not None:
            return self._trading_dates

        logger.info("扫描交易日列表...")
        dates_set = set()

        # 优先从期货数据提取（数据量最小）
        target_ds = self._fut_ds or self._opt_ds
        if target_ds is None:
            logger.warning("  无可用 dataset，返回空列表")
            self._trading_dates = []
            return []

        try:
            # 只读 datetime 列，提取日期部分
            table = target_ds.to_table(columns=["datetime"])
            dt_col = table.column("datetime").to_pylist()
            for dt_str in dt_col:
                if dt_str and len(str(dt_str)) >= 10:
                    dates_set.add(str(dt_str)[:10])
        except Exception as exc:
            logger.error("  扫描交易日失败: %s", exc)
            self._trading_dates = []
            return []

        self._trading_dates = sorted(dates_set)
        logger.info("  共 %d 个交易日: %s ~ %s",
                     len(self._trading_dates),
                     self._trading_dates[0] if self._trading_dates else "N/A",
                     self._trading_dates[-1] if self._trading_dates else "N/A")
        return self._trading_dates

    def release(self):
        """释放资源（dataset 句柄由 PyArrow 管理，这里主要清缓存）"""
        self._trading_dates = None
