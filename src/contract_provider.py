"""Contract metadata provider for Toolkit-backed option data.

This module owns option contract metadata, product-root parsing, multiplier
lookup, and futures margin-ratio lookup. Keeping it outside the minute engine
makes spot, IV, and margin code easier to audit.
"""

import json
import logging
import os
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd

from margin_model import resolve_margin_ratio

try:
    from toolkit.selector import select_bars_sql as _toolkit_select_bars_sql
except ImportError:  # Local unit tests may run without the internal Toolkit.
    _toolkit_select_bars_sql = None


logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "output")
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")

SUFFIX_TO_EXCHANGE = {
    "DCE": "DCE",
    "SHF": "SHFE",
    "CZC": "CZCE",
    "INE": "INE",
    "GFE": "GFEX",
    "CFE": "CFFEX",
    "SH": "SSE",
    "SZ": "SZSE",
}

INDEX_OPTION_TO_FUTURE = {"IO": "IF", "HO": "IH", "MO": "IM"}
OPTION_MONTH_RE = re.compile(r"^([A-Za-z]+)(\d{3,4})")
ETF_NAME_PATTERNS = [
    ("科创50", {"SSE": "588000.SH"}),
    ("科创板50", {"SSE": "588000.SH"}),
    ("50ETF", {"SSE": "510050.SH"}),
    ("300ETF", {"SSE": "510300.SH", "SZSE": "159919.SZ"}),
    ("500ETF", {"SSE": "510500.SH"}),
    ("创业板ETF", {"SZSE": "159915.SZ"}),
]


def default_select_bars_sql(sql):
    if _toolkit_select_bars_sql is None:
        raise RuntimeError("toolkit.selector.select_bars_sql is not available")
    return _toolkit_select_bars_sql(sql)


class ContractInfo:
    """Cache option contract metadata from option_basic_info."""

    def __init__(self, cache_dir=None, selector=None):
        self.cache_dir = cache_dir or CACHE_DIR
        self._select_bars_sql = selector or default_select_bars_sql
        self._cache = {}
        self._products = {}
        self._future_margin_ratios = {}
        self._loaded = False

    def load(self):
        """Load all contract metadata, using a JSON cache when safe."""
        if self._loaded:
            return
        cache_path = os.path.join(self.cache_dir, "contract_info_cache.json")
        if os.path.exists(cache_path):
            try:
                t0 = time.time()
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                self._cache = cache.get("by_code", {})
                self._products = {
                    product: set(codes) for product, codes in cache.get("by_product", {}).items()
                }
                self._future_margin_ratios = cache.get("future_margin_ratios", {}) or {}
                cache_has_underlying = any(
                    isinstance(v, dict) and v.get("underlying_code")
                    for v in self._cache.values()
                )
                if self._cache and cache_has_underlying:
                    if not self._future_margin_ratios:
                        self._load_future_margin_ratios()
                    self._loaded = True
                    logger.info(
                        "loaded contract cache: %d contracts, %d products, %.1fs",
                        len(self._cache),
                        len(self._products),
                        time.time() - t0,
                    )
                    return
                logger.warning(
                    "contract metadata cache misses underlying_code; rebuilding from database"
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("contract metadata cache load failed; querying database: %s", exc)

        logger.info("loading contract metadata from option_basic_info ...")
        t0 = time.time()
        df = self._select_bars_sql("""
            SELECT ths_code, option_short_name, contract_type,
                   strike_price, maturity_date, last_strike_date,
                   contract_multiplier, strike_method
            FROM option_basic_info
        """)
        if df is None or df.empty:
            raise RuntimeError("option_basic_info load failed")

        for _, row in df.iterrows():
            code = row["ths_code"]
            suffix = code.rsplit(".", 1)[-1] if "." in code else ""
            exchange = SUFFIX_TO_EXCHANGE.get(suffix, "")

            contract_type = str(row.get("contract_type", ""))
            if "看涨" in contract_type or "call" in contract_type.lower():
                opt_type = "C"
            elif "看跌" in contract_type or "put" in contract_type.lower():
                opt_type = "P"
            else:
                opt_type = "C" if "C" in code.upper().split(".")[0] else "P"

            short_name = str(row.get("option_short_name", ""))
            underlying_code = self._extract_underlying_code(code, exchange, short_name)
            product_root = self._extract_product_root(code, exchange, short_name)

            strike = float(row["strike_price"]) if pd.notna(row["strike_price"]) else 0.0
            mult = (
                float(row["contract_multiplier"])
                if pd.notna(row["contract_multiplier"])
                else 10.0
            )
            expiry = str(row["maturity_date"])[:10] if pd.notna(row["maturity_date"]) else ""

            info = {
                "ths_code": code,
                "option_type": opt_type,
                "strike": strike,
                "expiry_date": expiry,
                "multiplier": mult,
                "exchange": exchange,
                "product_root": product_root,
                "underlying_code": underlying_code,
                "exercise_type": str(row.get("strike_method", "")),
                "short_name": short_name,
            }
            self._cache[code] = info
            if product_root:
                self._products.setdefault(product_root, set()).add(code)

        self._loaded = True
        self._load_future_margin_ratios()
        logger.info(
            "contract metadata loaded: %d contracts, %d products, %.1fs",
            len(self._cache),
            len(self._products),
            time.time() - t0,
        )
        self._save_cache(cache_path)

    def _save_cache(self, cache_path):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "by_code": self._cache,
                        "by_product": {k: sorted(v) for k, v in self._products.items()},
                        "future_margin_ratios": self._future_margin_ratios,
                    },
                    f,
                )
        except OSError as exc:
            logger.warning("contract metadata cache save failed: %s", exc)

    @staticmethod
    def _parse_margin_ratio(value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip().replace("%", "")
            if not value:
                return None
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(ratio) or ratio <= 0:
            return None
        if ratio > 1:
            ratio /= 100.0
        return ratio if 0 < ratio < 1 else None

    def _load_future_margin_ratios(self):
        if self._future_margin_ratios:
            return
        try:
            df = self._select_bars_sql("""
                SELECT ths_code, initial_td_deposit
                FROM future_basic_info
                WHERE initial_td_deposit IS NOT NULL
            """)
        except Exception as exc:
            logger.warning("future_basic_info margin ratio load failed: %s", exc)
            return
        if df is None or df.empty:
            return
        ratios = {}
        for _, row in df.iterrows():
            ratio = self._parse_margin_ratio(row.get("initial_td_deposit"))
            if ratio is None:
                continue
            code = str(row.get("ths_code", "")).upper().strip()
            if not code:
                continue
            ratios[code] = ratio
            ratios[code.split(".", 1)[0]] = ratio
        self._future_margin_ratios = ratios
        logger.info("loaded future margin ratios: %d keys", len(ratios))

    def get_margin_ratio(self, exchange=None, product=None, underlying_code=None, config=None):
        self._load_future_margin_ratios()
        data_ratio = None
        if str(exchange or "").upper() in ("SHFE", "INE", "DCE", "CZCE", "GFEX") and underlying_code:
            key = str(underlying_code).upper().strip()
            data_ratio = self._future_margin_ratios.get(key)
            if data_ratio is None:
                data_ratio = self._future_margin_ratios.get(key.split(".", 1)[0])
        return resolve_margin_ratio(
            exchange=exchange,
            product=product,
            config=config,
            data_ratio=data_ratio,
        )

    def _infer_etf_underlying_code(self, short_name, exchange):
        text = str(short_name or "")
        for pattern, exchange_map in ETF_NAME_PATTERNS:
            if pattern in text:
                if exchange in exchange_map:
                    return exchange_map[exchange]
                if exchange_map:
                    return next(iter(exchange_map.values()))
        return None

    def _extract_underlying_code(self, ths_code, exchange, short_name=""):
        base, _, suffix = str(ths_code).partition(".")
        if base.isdigit():
            return self._infer_etf_underlying_code(short_name, exchange)

        option_head = base.split("-", 1)[0]
        matched = OPTION_MONTH_RE.match(option_head)
        if not matched:
            return None

        root = matched.group(1).upper()
        month = matched.group(2)
        fut_root = INDEX_OPTION_TO_FUTURE.get(root, root)
        return f"{fut_root}{month}.{suffix}" if suffix else f"{fut_root}{month}"

    def _extract_product_root(self, ths_code, exchange, short_name=""):
        base = str(ths_code).split(".")[0]
        if base.isdigit():
            underlying = self._infer_etf_underlying_code(short_name, exchange)
            return underlying.split(".", 1)[0] if underlying else base
        root = []
        for ch in base:
            if ch.isalpha():
                root.append(ch)
            else:
                break
        return "".join(root).upper() if root else base

    def lookup(self, ths_code):
        return self._cache.get(ths_code)

    def get_product_codes(self, product_root):
        return self._products.get(product_root, set())

    def get_all_products(self):
        return list(self._products.keys())

    def calc_dte(self, ths_code, current_date):
        info = self._cache.get(ths_code)
        if not info or not info["expiry_date"]:
            return -1
        try:
            exp = datetime.strptime(info["expiry_date"], "%Y-%m-%d").date()
            if isinstance(current_date, str):
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date()
            return (exp - current_date).days
        except (ValueError, TypeError):
            return -1
