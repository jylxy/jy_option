"""
逐分钟回测引擎 — True Minute Backtest Engine

直接从 Parquet 分钟线数据源读取数据，逐分钟处理事件循环。

盘中处理频率分两层：
  - 每分钟：更新持仓价格 + 应急保护检查
  - 每 N 分钟（默认15）：止盈检查 + IV/Greeks 更新 + Greeks 风控

用法：
    python server_deploy/src/true_minute_engine.py --start-date 2024-01-01
    python server_deploy/src/true_minute_engine.py --start-date 2024-01-01 --end-date 2026-03-31
"""
import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, date
from collections import defaultdict

import numpy as np
import pandas as pd

# 确保同目录模块可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parquet_loader import ParquetDayLoader, ContractMaster, DaySlice
from iv_surface import build_iv_smiles, get_iv_residual
from intraday_monitor import IntradayMonitor
from option_calc import (
    calc_iv_single, calc_greeks_single,
    calc_greeks_batch_vectorized, RISK_FREE_RATE,
)
from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy_by_otm, select_s3_sell_by_otm, select_s3_protect_by_otm,
    select_s4,
    calc_s1_size, calc_s3_size_v2, calc_s4_size,
    check_emergency_protect,
    extract_atm_iv_series, calc_iv_percentile, get_iv_scale,
    should_pause_open, should_close_expiry, can_reopen,
    check_margin_ok, calc_stats,
    DEFAULT_PARAMS,
)
from backtest_fast import estimate_margin

logger = logging.getLogger(__name__)

# 默认配置路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "output")


# ══════════════════════════════════════════════════════════════════════════════
# Position — 持仓对象
# ══════════════════════════════════════════════════════════════════════════════

class Position:
    """
    单个期权持仓，含实时 Greeks 和 IV_Residual。
    """
    __slots__ = [
        "strat", "product", "code", "opt_type", "strike",
        "open_price", "n", "open_date", "mult", "expiry", "mr", "role",
        "prev_price", "cur_price", "cur_spot", "exchange",
        "cur_delta", "cur_gamma", "cur_vega", "cur_theta", "cur_iv",
        "iv_residual", "group_id", "dte",
        "underlying_code", "min_price_tick",
    ]

    def __init__(self, strat, product, code, opt_type, strike, open_price,
                 n, open_date, mult, expiry, mr, role, spot=0,
                 exchange="", group_id="", iv_residual=0.0,
                 underlying_code=None, min_price_tick=0.01):
        self.strat = strat
        self.product = product
        self.code = code
        self.opt_type = opt_type
        self.strike = strike
        self.open_price = open_price
        self.n = n
        self.open_date = open_date
        self.mult = mult
        self.expiry = expiry
        self.mr = mr
        self.role = role
        self.prev_price = open_price
        self.cur_price = open_price
        self.cur_spot = spot
        self.exchange = exchange
        self.cur_delta = 0.0
        self.cur_gamma = 0.0
        self.cur_vega = 0.0
        self.cur_theta = 0.0
        self.cur_iv = 0.0
        self.iv_residual = iv_residual
        self.group_id = group_id
        self.dte = 0
        self.underlying_code = underlying_code
        self.min_price_tick = min_price_tick

    def daily_pnl(self):
        """当日盈亏（prev_price → cur_price）"""
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.prev_price) * self.mult * self.n
        return (self.prev_price - self.cur_price) * self.mult * self.n

    def profit_pct(self, fee_per_hand=0):
        """卖腿累计净利润率（扣除手续费后）"""
        if self.role != "sell" or self.open_price <= 0:
            return 0.0
        gross = (self.open_price - self.cur_price) * self.mult
        fee = fee_per_hand * 2
        revenue = self.open_price * self.mult
        return (gross - fee) / revenue if revenue > 0 else 0.0

    def cur_margin(self):
        """当前保证金（仅卖腿）"""
        if self.role != "sell":
            return 0.0
        return estimate_margin(
            self.cur_spot or self.strike, self.strike, self.opt_type,
            self.cur_price, self.mult, self.mr, 0.5,
            exchange=self.exchange
        ) * self.n

    def cash_delta(self):
        """Cash Delta = sign × delta × 乘数 × 手数 × 标的价"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_delta * self.mult * self.n * (self.cur_spot or 0)

    def cash_vega(self):
        """Cash Vega = sign × vega × 乘数 × 手数"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_vega * self.mult * self.n

    def cash_gamma(self):
        """Cash Gamma = sign × gamma × 乘数 × 手数 × spot²"""
        sign = 1 if self.role in ("buy", "protect") else -1
        spot = self.cur_spot or 0
        return sign * self.cur_gamma * self.mult * self.n * spot * spot

    def cash_theta(self):
        """Cash Theta = sign × theta × 乘数 × 手数"""
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_theta * self.mult * self.n


# ══════════════════════════════════════════════════════════════════════════════
# TrueMinuteEngine — 主引擎
# ══════════════════════════════════════════════════════════════════════════════

class TrueMinuteEngine:
    """逐分钟回测引擎"""

    def __init__(self, config_path=None):
        """加载配置、初始化各模块"""
        self.config = self._load_config(config_path or CONFIG_PATH)
        self.capital = self.config.get("capital", 10_000_000)

        # 初始化数据加载器
        data_dir = self.config.get("parquet_data_dir") or None
        self.loader = ParquetDayLoader(data_dir=data_dir)
        self.cm = self.loader.contract_master

        # 初始化盘中监控
        self.monitor = IntradayMonitor(self.config)

        # 状态
        self.positions = []
        self.nav_records = []
        self.orders = []
        self.iv_pcts = {}  # {product: pd.Series}

    def _load_config(self, path):
        """加载 config.json，合并默认参数"""
        merged = dict(DEFAULT_PARAMS)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    merged.update(cfg)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("config.json 读取失败: %s", exc)
        return merged

    def run(self, start_date=None, end_date=None, tag="minute"):
        """
        主入口：执行完整回测。

        Returns:
            dict: {nav_df, orders_df, stats}
        """
        t0 = time.time()
        dates = self.loader.get_trading_dates()
        if start_date:
            dates = [d for d in dates if d >= start_date]
        if end_date:
            dates = [d for d in dates if d <= end_date]

        if not dates:
            logger.error("无交易日数据")
            return {}

        logger.info("回测 %d 天: %s ~ %s", len(dates), dates[0], dates[-1])

        # 品种池（简化：使用全部有数据的品种）
        product_pool = set()

        for di, date_str in enumerate(dates):
            if di % 100 == 0:
                nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
                elapsed = time.time() - t0
                logger.info("  [%d/%d] %s NAV=%.0f 持仓=%d 耗时=%.0fs",
                            di, len(dates), date_str, nav, len(self.positions), elapsed)

            t_day = time.time()

            # 加载当日数据
            day_slice = self.loader.load_day(date_str)

            # Phase A: 盘中处理
            self._process_intraday(day_slice, date_str)

            # Phase B: 收盘决策
            self._process_daily_decision(day_slice, date_str, product_pool)

            # NAV 快照
            self._update_nav_snapshot(date_str)

            # 释放内存
            day_slice.release()

            day_elapsed = time.time() - t_day
            if day_elapsed > 60:
                logger.warning("  %s 处理耗时 %.0f 秒（超过60秒警告）", date_str, day_elapsed)

        total_elapsed = time.time() - t0
        avg_per_day = total_elapsed / max(len(dates), 1)
        logger.info("回测完成: 总耗时 %.0f 秒, 平均 %.1f 秒/天", total_elapsed, avg_per_day)

        # 输出
        nav_df = pd.DataFrame(self.nav_records)
        orders_df = pd.DataFrame(self.orders)
        stats = {}
        if len(nav_df) > 0 and "nav" in nav_df.columns:
            stats = calc_stats(nav_df["nav"].values)

        self._output_results(nav_df, orders_df, stats, tag, total_elapsed)
        return {"nav_df": nav_df, "orders_df": orders_df, "stats": stats}


    # ── Phase A: 盘中逐分钟处理 ──────────────────────────────────────────────

    def _process_intraday(self, day_slice, date_str):
        """
        Phase A: 盘中逐分钟处理。

        每分钟：更新价格 + 应急保护检查
        每 N 分钟：止盈检查 + IV/Greeks 更新 + Greeks 风控
        """
        if day_slice.option_bars is None or len(day_slice.option_bars) == 0:
            return

        timestamps = day_slice.get_minute_timestamps()
        if not timestamps:
            return

        current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
        spread_mode = self.config.get("spread_mode", "tick")

        # S4 盘中 Gamma PnL 追踪
        s4_intraday_pnl = 0.0
        s4_high_pnl = 0.0
        s4_low_pnl = 0.0
        day_open_spot = {}  # product → 开盘标的价格

        for mi, ts in enumerate(timestamps):
            # ── 1. 更新所有持仓价格（每分钟）──
            for pos in self.positions:
                bar = day_slice.get_option_bar(ts, pos.code)
                if bar and bar["volume"] > 0:
                    pos.prev_price = pos.cur_price
                    pos.cur_price = bar["close"]

                # 更新标的价格
                if pos.underlying_code:
                    spot = day_slice.get_spot_price(ts, pos.underlying_code)
                    if spot and spot > 0:
                        pos.cur_spot = spot

                # 更新 DTE
                dte = self.cm.calc_dte(pos.code, current_date)
                if dte >= 0:
                    pos.dte = dte

                # 记录 S4 开盘标的价格
                if mi == 0 and pos.strat == "S4" and pos.cur_spot > 0:
                    day_open_spot[pos.product] = pos.cur_spot

            # ── 2. 应急保护检查（每分钟）──
            self._check_emergency_all(day_slice, date_str, ts, spread_mode)

            # ── 3. 按间隔执行：止盈 + Greeks 更新 + 风控 ──
            if self.monitor.should_update_greeks(mi):
                # 止盈检查
                self._check_stop_profit_all(date_str, ts, spread_mode)

                # IV/Greeks 更新
                for pos in self.positions:
                    if pos.cur_price > 0 and pos.cur_spot > 0 and pos.dte > 0:
                        iv = calc_iv_single(
                            pos.cur_price, pos.cur_spot, pos.strike,
                            pos.dte, pos.opt_type
                        )
                        if not np.isnan(iv) and iv > 0:
                            pos.cur_iv = iv
                            g = calc_greeks_single(
                                pos.cur_spot, pos.strike, pos.dte,
                                iv, pos.opt_type
                            )
                            if not np.isnan(g["delta"]):
                                pos.cur_delta = g["delta"]
                                pos.cur_gamma = g["gamma"]
                                pos.cur_vega = g["vega"]
                                pos.cur_theta = g["theta"]

                # Greeks 风控检查
                greeks = self.monitor.aggregate_greeks(self.positions, nav)
                breaches = self.monitor.check_greeks_breach(greeks, nav)
                for breach in breaches:
                    to_close = self.monitor.select_positions_to_reduce(
                        self.positions, breach["type"], nav
                    )
                    for pos in to_close:
                        spread = self.monitor.calc_spread(
                            pos.cur_price, pos.code, self.cm, spread_mode
                        )
                        self._execute_close(
                            pos, pos.cur_price, ts,
                            f"greeks_breach_{breach['type']}", spread
                        )

            # ── 4. S4 Gamma PnL 追踪 ──
            for pos in self.positions:
                if pos.strat == "S4" and pos.product in day_open_spot:
                    delta_s = pos.cur_spot - day_open_spot[pos.product]
                    gamma_pnl = 0.5 * pos.cash_gamma() * (delta_s ** 2)
                    s4_intraday_pnl = gamma_pnl
                    s4_high_pnl = max(s4_high_pnl, gamma_pnl)
                    s4_low_pnl = min(s4_low_pnl, gamma_pnl)

    def _check_emergency_all(self, day_slice, date_str, ts, spread_mode):
        """检查所有 S3 卖腿的应急保护"""
        for pos in list(self.positions):
            if not self.monitor.check_emergency_protect(pos, pos.cur_spot):
                continue

            # 检查是否已有保护腿
            has_protect = any(
                p.strat == "S3" and p.product == pos.product and
                p.opt_type == pos.opt_type and p.group_id == pos.group_id and
                p.role == "protect"
                for p in self.positions
            )
            if has_protect:
                continue

            # 尝试找保护腿（从当日数据聚合）
            # 简化：直接平仓整组（盘中无法可靠选腿）
            logger.info("  %s S3应急保护触发: %s %s, 平仓整组",
                        ts, pos.product, pos.code)
            self._close_group(pos, ts, "emergency_close", spread_mode)

    def _check_stop_profit_all(self, date_str, ts, spread_mode):
        """检查所有卖腿的止盈"""
        closed_ids = set()
        for pos in list(self.positions):
            if id(pos) in closed_ids:
                continue
            if pos.role != "sell":
                continue
            if not self.monitor.check_stop_profit(pos):
                continue

            # 记录即将关闭的组
            group = pos.group_id
            group_ids = {id(p) for p in self.positions
                         if p.group_id == group and group}
            closed_ids.update(group_ids or {id(pos)})

            reason = f"stop_profit_{pos.strat.lower()}"
            self._close_group(pos, ts, reason, spread_mode)

    def _close_group(self, trigger_pos, ts, reason, spread_mode):
        """平仓整组持仓（同 group_id 的所有腿）"""
        group = trigger_pos.group_id
        to_close = [p for p in self.positions
                     if p.group_id == group and p.group_id]
        if not to_close:
            to_close = [trigger_pos]

        for pos in to_close:
            spread = self.monitor.calc_spread(
                pos.cur_price, pos.code, self.cm, spread_mode
            )
            self._execute_close(pos, pos.cur_price, ts, reason, spread)


    # ── Phase B: 收盘后决策 ──────────────────────────────────────────────────

    def _process_daily_decision(self, day_slice, date_str, product_pool):
        """
        Phase B: 每日收盘后决策。

        1. 聚合日频数据
        2. 构建 IV Smile
        3. 检查到期平仓
        4. 检查开仓条件 → 选腿 → 手数 → 开仓
        """
        current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
        spread_mode = self.config.get("spread_mode", "tick")
        cfg = self.config

        # 1. 聚合日频数据
        daily_df = day_slice.aggregate_daily(self.cm)
        if daily_df.empty:
            return

        # 更新品种池
        products_in_data = daily_df["product"].unique().tolist()
        product_pool.update(products_in_data)

        # 2. 构建 IV Smile
        smiles = build_iv_smiles(daily_df, products_in_data, date_str)

        # 3. 到期平仓
        for pos in list(self.positions):
            if pos.dte <= cfg.get("expiry_dte", 1):
                self._execute_close(pos, pos.cur_price, "close", "expiry", 0)

            # S4: DTE < 10 退出
            if pos.strat == "S4" and pos.role == "buy" and pos.dte < 10:
                self._execute_close(pos, pos.cur_price, "close", "s4_dte_exit", 0)

        # 4. 新开仓
        total_m = sum(p.cur_margin() for p in self.positions if p.role == "sell")
        s1_m = sum(p.cur_margin() for p in self.positions if p.role == "sell" and p.strat == "S1")
        s3_m = sum(p.cur_margin() for p in self.positions if p.role == "sell" and p.strat == "S3")
        margin_cap = cfg.get("margin_cap", 0.50)
        s1_cap = cfg.get("s1_margin_cap", 0.25)
        s3_cap = cfg.get("s3_margin_cap", 0.25)
        margin_per = cfg.get("margin_per", 0.02)
        iv_open_thr = cfg.get("iv_open_threshold", 80)
        top_n = cfg.get("products_top_n", 20)

        for product in products_in_data[:top_n]:
            prod_df = daily_df[daily_df["product"] == product]
            if prod_df.empty:
                continue

            # 检查开仓条件
            from strategy_rules import should_open_new
            open_expiries = should_open_new(prod_df,
                                            dte_target=cfg.get("dte_target", 35),
                                            dte_min=cfg.get("dte_min", 15),
                                            dte_max=cfg.get("dte_max", 90))
            if not open_expiries:
                continue

            for exp in open_expiries:
                # 跳过已有持仓的到期日
                if any(p.product == product and p.expiry == exp for p in self.positions):
                    continue

                ef = prod_df[prod_df["expiry_date"] == exp]
                if ef.empty:
                    continue

                # IV 分位数检查
                # （简化：暂不实现 IV 分位数，后续可从历史数据累积）
                iv_scale = 1.0

                # 保证金检查
                if margin_cap and total_m / max(nav, 1) >= margin_cap:
                    break

                spot = ef["spot_close"].iloc[0] if "spot_close" in ef.columns else 0
                mult = ef["multiplier"].iloc[0] if "multiplier" in ef.columns else 10
                mr = 0.05  # 默认保证金比例
                exchange = ef["exchange"].iloc[0] if "exchange" in ef.columns else ""

                # ── S3 开仓（优先）──
                if s3_cap and s3_m / max(nav, 1) < s3_cap:
                    for ot in ["P", "C"]:
                        if any(p.strat == "S3" and p.product == product and
                               p.opt_type == ot and p.role == "sell"
                               for p in self.positions):
                            continue
                        self._try_open_s3(ef, product, ot, spot, mult, mr,
                                          exchange, exp, nav, margin_per,
                                          iv_scale, smiles, date_str, day_slice,
                                          spread_mode)
                        # 更新保证金
                        s3_m = sum(p.cur_margin() for p in self.positions
                                   if p.role == "sell" and p.strat == "S3")
                        total_m = sum(p.cur_margin() for p in self.positions
                                       if p.role == "sell")

                # ── S1 开仓 ──
                if s1_cap and s1_m / max(nav, 1) < s1_cap:
                    for ot in ["P", "C"]:
                        if margin_cap and total_m / max(nav, 1) >= margin_cap:
                            break
                        self._try_open_s1(ef, product, ot, mult, mr,
                                          exchange, exp, nav, margin_per,
                                          iv_scale, smiles, date_str, day_slice,
                                          spread_mode)
                        s1_m = sum(p.cur_margin() for p in self.positions
                                   if p.role == "sell" and p.strat == "S1")
                        total_m = sum(p.cur_margin() for p in self.positions
                                       if p.role == "sell")

                # ── S4 开仓 ──
                if not any(p.strat == "S4" and p.product == product and
                           p.expiry == exp for p in self.positions):
                    self._try_open_s4(ef, product, mult, mr, exchange, exp,
                                      nav, date_str, day_slice, spread_mode)


    # ── 开仓辅助 ──────────────────────────────────────────────────────────────

    def _try_open_s1(self, ef, product, ot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, smiles, date_str,
                     day_slice, spread_mode):
        """尝试 S1 开仓"""
        c = select_s1_sell(ef, ot, mult, mr)
        if c is None:
            return

        m = estimate_margin(c["spot_close"], c["strike"], ot,
                            c["option_close"], mult, mr, 0.5, exchange=exchange)
        nn = calc_s1_size(nav, margin_per, m, iv_scale)

        # 保证金二次验证
        total_m = sum(p.cur_margin() for p in self.positions if p.role == "sell")
        s1_m = sum(p.cur_margin() for p in self.positions
                   if p.role == "sell" and p.strat == "S1")
        if not check_margin_ok(total_m, s1_m, m * nn, nav,
                               self.config.get("margin_cap", 0.50),
                               self.config.get("s1_margin_cap", 0.25)):
            return

        # 执行价格：次日 VWAP
        vwap = day_slice.calc_vwap(c["option_code"],
                                    self.config.get("vwap_window", 10))
        price = vwap if vwap and vwap > 0 else c["option_close"]
        spread = self.monitor.calc_spread(price, c["option_code"], self.cm, spread_mode)
        price = IntradayMonitor.apply_spread(price, "sell", spread)

        group_id = f"S1_{product}_{ot}_{exp}_{date_str}"
        iv_res = get_iv_residual(smiles, product, exp,
                                  c["strike"], c["spot_close"],
                                  c.get("implied_vol", 0))

        pos = Position("S1", product, c["option_code"], ot, c["strike"],
                        price, nn, date_str, mult, exp, mr, "sell",
                        spot=c["spot_close"], exchange=exchange,
                        group_id=group_id, iv_residual=iv_res,
                        underlying_code=c.get("underlying_code"))
        self.positions.append(pos)
        self._record_order(date_str, "close", "open_sell", "S1", product,
                           c["option_code"], ot, c["strike"], exp,
                           price, nn, 0, 0, iv_res)

        # 保护腿
        pr = select_s1_protect(ef, c)
        if pr is not None and pr["option_code"] != c["option_code"]:
            pn = max(1, nn // 2)
            p_price = pr["option_close"]
            p_spread = self.monitor.calc_spread(p_price, pr["option_code"], self.cm, spread_mode)
            p_price = IntradayMonitor.apply_spread(p_price, "buy", p_spread)

            ppos = Position("S1", product, pr["option_code"], ot, pr["strike"],
                            p_price, pn, date_str, mult, exp, mr, "buy",
                            spot=pr["spot_close"], exchange=exchange,
                            group_id=group_id,
                            underlying_code=pr.get("underlying_code"))
            self.positions.append(ppos)
            self._record_order(date_str, "close", "open_buy", "S1", product,
                               pr["option_code"], ot, pr["strike"], exp,
                               p_price, pn, 0, 0, 0)

    def _try_open_s3(self, ef, product, ot, spot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, smiles, date_str,
                     day_slice, spread_mode):
        """尝试 S3 开仓（裸比例价差）"""
        cfg = self.config
        bl = select_s3_buy_by_otm(ef, ot, spot,
                                   target_otm_pct=cfg.get("s3_buy_otm_pct", 5.0),
                                   otm_range=tuple(cfg.get("s3_buy_otm_range", (3.0, 7.0))))
        if bl is None:
            return
        sl = select_s3_sell_by_otm(ef, ot, spot, bl["strike"],
                                    target_otm_pct=cfg.get("s3_sell_otm_pct", 10.0),
                                    otm_range=tuple(cfg.get("s3_sell_otm_range", (7.0, 13.0))))
        if sl is None or sl["option_code"] == bl["option_code"]:
            return

        sm = estimate_margin(sl["spot_close"], sl["strike"], ot,
                             sl["option_close"], mult, mr, 0.5, exchange=exchange)
        size_result = calc_s3_size_v2(
            nav, margin_per, sm, bl["option_close"], sl["option_close"],
            mult, iv_scale,
            ratio_candidates=tuple(cfg.get("s3_ratio_candidates", (2, 3))),
            net_premium_tolerance=cfg.get("s3_net_premium_tolerance", 0.3)
        )
        if size_result is None:
            return
        bq, sq, ratio = size_result

        # 保证金二次验证
        total_m = sum(p.cur_margin() for p in self.positions if p.role == "sell")
        s3_m = sum(p.cur_margin() for p in self.positions
                   if p.role == "sell" and p.strat == "S3")
        if not check_margin_ok(total_m, s3_m, sm * sq, nav,
                               self.config.get("margin_cap", 0.50),
                               self.config.get("s3_margin_cap", 0.25)):
            return

        group_id = f"S3_{product}_{ot}_{exp}_{date_str}"

        # 买腿
        bp = bl["option_close"]
        bpos = Position("S3", product, bl["option_code"], ot, bl["strike"],
                        bp, bq, date_str, mult, exp, mr, "buy",
                        spot=bl["spot_close"], exchange=exchange,
                        group_id=group_id,
                        underlying_code=bl.get("underlying_code"))
        self.positions.append(bpos)
        self._record_order(date_str, "close", "open_buy", "S3", product,
                           bl["option_code"], ot, bl["strike"], exp,
                           bp, bq, 0, 0, 0)

        # 卖腿
        sp = sl["option_close"]
        iv_res = get_iv_residual(smiles, product, exp,
                                  sl["strike"], sl["spot_close"],
                                  sl.get("implied_vol", 0))
        spos = Position("S3", product, sl["option_code"], ot, sl["strike"],
                        sp, sq, date_str, mult, exp, mr, "sell",
                        spot=sl["spot_close"], exchange=exchange,
                        group_id=group_id, iv_residual=iv_res,
                        underlying_code=sl.get("underlying_code"))
        self.positions.append(spos)
        self._record_order(date_str, "close", "open_sell", "S3", product,
                           sl["option_code"], ot, sl["strike"], exp,
                           sp, sq, 0, 0, iv_res)

    def _try_open_s4(self, ef, product, mult, mr, exchange, exp,
                     nav, date_str, day_slice, spread_mode):
        """尝试 S4 开仓"""
        cfg = self.config
        n_products = max(cfg.get("products_top_n", 20), 1)

        for ot in ["P", "C"]:
            opt = select_s4(ef, ot)
            if opt is None:
                continue
            cost = opt["option_close"] * mult
            if cost <= 0:
                continue
            qty = calc_s4_size(nav, cfg.get("s4_prem", 0.005),
                               n_products, cost,
                               max_hands=cfg.get("s4_max_hands", 5))

            group_id = f"S4_{product}_{ot}_{exp}_{date_str}"
            pos = Position("S4", product, opt["option_code"], ot, opt["strike"],
                           opt["option_close"], qty, date_str, mult, exp, mr,
                           "buy", spot=opt["spot_close"], exchange=exchange,
                           group_id=group_id,
                           underlying_code=opt.get("underlying_code"))
            self.positions.append(pos)
            self._record_order(date_str, "close", "open_buy", "S4", product,
                               opt["option_code"], ot, opt["strike"], exp,
                               opt["option_close"], qty, 0, 0, 0)


    # ── 执行与记录 ────────────────────────────────────────────────────────────

    def _execute_close(self, position, price, timestamp, action, spread=0):
        """执行平仓：计算PnL、扣手续费、记录订单、移除持仓"""
        close_role = "buy" if position.role == "sell" else "sell"
        exec_price = IntradayMonitor.apply_spread(price, close_role, spread)

        # PnL
        if position.role in ("buy", "protect"):
            pnl = (exec_price - position.open_price) * position.mult * position.n
        else:
            pnl = (position.open_price - exec_price) * position.mult * position.n

        fee = self.config.get("fee", 3) * position.n * 2  # 开+平

        # 记录订单
        time_str = str(timestamp)[-8:-3] if len(str(timestamp)) > 10 else "close"
        self._record_order(
            position.open_date, time_str, action, position.strat,
            position.product, position.code, position.opt_type,
            position.strike, position.expiry,
            exec_price, position.n, fee, pnl, position.iv_residual
        )

        # 移除持仓
        self.positions = [p for p in self.positions if p is not position]

    def _record_order(self, date, time_str, action, strategy, product,
                      code, option_type, strike, expiry,
                      price, quantity, fee, pnl, iv_residual):
        """记录订单"""
        nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
        self.orders.append({
            "date": str(date)[:10],
            "time": time_str,
            "action": action,
            "strategy": strategy,
            "product": product,
            "code": code,
            "option_type": option_type,
            "strike": strike,
            "expiry": str(expiry)[:10] if expiry else "",
            "price": round(float(price), 4) if price else 0,
            "quantity": int(quantity),
            "fee": round(float(fee), 2),
            "pnl": round(float(pnl), 2),
            "nav": round(float(nav), 2),
            "iv_residual": round(float(iv_residual), 4) if iv_residual else 0,
        })

    def _update_nav_snapshot(self, date_str):
        """记录每日 NAV 快照"""
        # 计算当日 PnL
        day_pnl = sum(p.daily_pnl() for p in self.positions)

        # 手续费（当日开仓的）
        day_fee = sum(o["fee"] for o in self.orders if o["date"] == date_str)

        # 分策略 PnL
        s1_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == "S1")
        s3_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == "S3")
        s4_pnl = sum(p.daily_pnl() for p in self.positions if p.strat == "S4")

        cum_pnl = (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0) + day_pnl - day_fee
        nav = self.capital + cum_pnl
        margin = sum(p.cur_margin() for p in self.positions if p.role == "sell")

        # Greeks
        cd = sum(p.cash_delta() for p in self.positions)
        cv = sum(p.cash_vega() for p in self.positions)
        cg = sum(p.cash_gamma() for p in self.positions)
        ct = sum(p.cash_theta() for p in self.positions)

        self.nav_records.append({
            "date": date_str,
            "nav": nav,
            "cum_pnl": cum_pnl,
            "s1_pnl": s1_pnl,
            "s3_pnl": s3_pnl,
            "s4_pnl": s4_pnl,
            "fee": day_fee,
            "margin_used": margin,
            "cash_delta": cd / max(nav, 1),
            "cash_vega": cv / max(nav, 1),
            "cash_gamma": cg / max(nav, 1),
            "cash_theta": ct,
            "iv_smile_avg_r2": 0.0,  # 由 Phase B 填充
            "n_positions": len(self.positions),
        })

        # 重置 prev_price 为 cur_price（为下一天准备）
        for p in self.positions:
            p.prev_price = p.cur_price

    def _output_results(self, nav_df, orders_df, stats, tag, elapsed):
        """输出 nav/orders/report CSV 和 Markdown"""
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        nav_path = os.path.join(OUTPUT_DIR, f"nav_{tag}.csv")
        nav_df.to_csv(nav_path, index=False)
        logger.info("NAV 输出: %s (%d 行)", nav_path, len(nav_df))

        orders_path = os.path.join(OUTPUT_DIR, f"orders_{tag}.csv")
        orders_df.to_csv(orders_path, index=False)
        logger.info("订单输出: %s (%d 行)", orders_path, len(orders_df))

        # 报告
        report_path = os.path.join(OUTPUT_DIR, f"report_{tag}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 回测报告 — {tag}\n\n")
            f.write(f"**日期范围**: {nav_df['date'].iloc[0]} ~ {nav_df['date'].iloc[-1]}")
            f.write(f" ({len(nav_df)}天)\n")
            f.write(f"**耗时**: {elapsed:.0f}秒\n\n")
            f.write("## 核心指标\n\n")
            f.write("| 指标 | 值 |\n|------|------|\n")
            for k, v in stats.items():
                f.write(f"| {k} | {v:.4f} |\n")
            f.write(f"\n## 策略PnL\n\n")
            if len(nav_df) > 0:
                last = nav_df.iloc[-1]
                f.write(f"| S1 | {last.get('s1_pnl', 0):.0f} |\n")
                f.write(f"| S3 | {last.get('s3_pnl', 0):.0f} |\n")
                f.write(f"| S4 | {last.get('s4_pnl', 0):.0f} |\n")
                f.write(f"| 手续费 | {nav_df['fee'].sum():.0f} |\n")
        logger.info("报告输出: %s", report_path)


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="逐分钟回测引擎")
    parser.add_argument("--start-date", type=str, default=None,
                        help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None,
                        help="结束日期 YYYY-MM-DD")
    parser.add_argument("--tag", type=str, default="minute",
                        help="输出文件标签")
    parser.add_argument("--config", type=str, default=None,
                        help="config.json 路径")
    parser.add_argument("--verbose", action="store_true",
                        help="详细日志")
    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    engine = TrueMinuteEngine(config_path=args.config or CONFIG_PATH)
    result = engine.run(
        start_date=args.start_date,
        end_date=args.end_date,
        tag=args.tag,
    )

    if result and "stats" in result:
        stats = result["stats"]
        print("\n=== 回测结果 ===")
        for k, v in stats.items():
            print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
