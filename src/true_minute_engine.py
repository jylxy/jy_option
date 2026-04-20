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
    should_pause_open, should_close_expiry, should_open_new, can_reopen,
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
        "prev_price", "cur_price", "cur_spot", "prev_spot", "exchange",
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
        self.prev_spot = spot
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

    def pnl_attribution(self):
        """
        PnL 归因分解（Taylor 展开近似）。

        delta_pnl = sign × delta × dS × mult × n
        gamma_pnl = sign × 0.5 × gamma × dS² × mult × n
        theta_pnl = sign × theta × (1/365) × mult × n
        vega_pnl  = total_pnl - delta_pnl - gamma_pnl - theta_pnl（残差）

        Returns:
            dict: {delta_pnl, gamma_pnl, theta_pnl, vega_pnl, total_pnl}
        """
        sign = 1 if self.role in ("buy", "protect") else -1
        total = self.daily_pnl()
        ds = (self.cur_spot or 0) - (self.prev_spot or 0)

        d_pnl = sign * self.cur_delta * ds * self.mult * self.n
        g_pnl = sign * 0.5 * self.cur_gamma * ds * ds * self.mult * self.n
        t_pnl = sign * self.cur_theta * (1.0 / 365.0) * self.mult * self.n
        # Vega PnL 作为残差（包含 IV 变化 + 高阶项 + 离散化误差）
        v_pnl = total - d_pnl - g_pnl - t_pnl

        return {
            "delta_pnl": d_pnl,
            "gamma_pnl": g_pnl,
            "theta_pnl": t_pnl,
            "vega_pnl": v_pnl,
            "total_pnl": total,
        }


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

        # IV 历史累积（品种 → 日期列表 + IV 列表）
        self._iv_history = defaultdict(lambda: {"dates": [], "ivs": []})
        self._iv_daily_records = []  # 每日 IV 快照，用于输出 CSV

        # 当日已平仓的 PnL/fee 累加器（每日开始时清零）
        self._day_realized = {"pnl": 0.0, "fee": 0.0,
                              "s1": 0.0, "s3": 0.0, "s4": 0.0}
        self._current_date_str = ""  # 当前交易日
        self._pending_opens = []  # T日决策、T+1日执行的待开仓列表
        self._current_avg_iv_pct = np.nan  # 当日组合平均 IV 分位数
        self._current_iv_pcts = {}  # {product: iv_percentile} 当日各品种 IV 分位数

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
            if di % 10 == 0:
                nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
                elapsed = time.time() - t0
                logger.info("  [%d/%d] %s NAV=%.0f 持仓=%d 耗时=%.0fs",
                            di, len(dates), date_str, nav, len(self.positions), elapsed)
                # 每10天增量保存 NAV CSV，方便中途查看
                if self.nav_records:
                    _nav_path = os.path.join(OUTPUT_DIR, f"nav_{tag}.csv")
                    os.makedirs(OUTPUT_DIR, exist_ok=True)
                    pd.DataFrame(self.nav_records).to_csv(_nav_path, index=False)

            t_day = time.time()

            # 清零当日已平仓 PnL 累加器
            self._day_realized = {"pnl": 0.0, "fee": 0.0,
                                  "s1": 0.0, "s3": 0.0, "s4": 0.0}
            self._current_date_str = date_str

            # 加载当日数据
            day_slice = self.loader.load_day(date_str)

            # Phase 0: 执行昨日决策的待开仓（T+1 VWAP 执行）
            self._execute_pending_opens(day_slice, date_str)

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

        # S4 盘中 Gamma PnL 追踪（修正公式 + 累加多持仓）
        s4_intraday_pnl = 0.0
        s4_high_pnl = 0.0
        s4_low_pnl = 0.0
        day_open_spot = {}  # product → 开盘标的价格

        for mi, ts in enumerate(timestamps):
            # ── 1. 更新所有持仓价格（每分钟）──
            for pos in self.positions:
                bar = day_slice.get_option_bar(ts, pos.code)
                if bar and bar["volume"] > 0:
                    # 注意：不更新 prev_price，prev_price 保持为昨日收盘价
                    # daily_pnl() = prev_price → cur_price = 昨收 → 当前价
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

            # ── 4. S4 Gamma PnL 追踪（修正公式：0.5 * gamma * dS^2 * mult * n）──
            s4_snap = 0.0
            for pos in self.positions:
                if pos.strat == "S4" and pos.product in day_open_spot:
                    delta_s = pos.cur_spot - day_open_spot[pos.product]
                    # 标准 Gamma PnL = 0.5 * gamma * dS^2 * mult * n
                    sign = 1 if pos.role in ("buy", "protect") else -1
                    gamma_pnl = 0.5 * sign * pos.cur_gamma * (delta_s ** 2) * pos.mult * pos.n
                    s4_snap += gamma_pnl
            s4_intraday_pnl = s4_snap
            s4_high_pnl = max(s4_high_pnl, s4_snap)
            s4_low_pnl = min(s4_low_pnl, s4_snap)

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
        """检查所有卖腿的止盈（因子6：动态止盈阈值）"""
        closed_ids = set()
        for pos in list(self.positions):
            if id(pos) in closed_ids:
                continue
            if pos.role != "sell":
                continue
            # 传入品种的 IV 分位数（因子6）
            iv_pct = self._current_iv_pcts.get(pos.product)
            if not self.monitor.check_stop_profit(pos, iv_pct=iv_pct):
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

        # 按成交量排序品种（Bug 4 修复：按流动性排序而非随机顺序）
        prod_volume = daily_df.groupby("product")["volume"].sum().sort_values(ascending=False)
        sorted_products = [p for p in prod_volume.index if p in products_in_data]

        # 2. 构建 IV Smile
        smiles = build_iv_smiles(daily_df, sorted_products, date_str)

        # 2b. 累积 ATM IV 历史 + 计算 IV 分位数
        product_iv_pcts = {}  # {product: iv_percentile}
        for product in sorted_products:
            prod_df = daily_df[daily_df["product"] == product]
            if prod_df.empty:
                continue
            # 提取当日 ATM IV（moneyness 0.95~1.05, dte 15~90）
            atm = prod_df[
                (prod_df["moneyness"].between(0.95, 1.05)) &
                (prod_df["dte"].between(15, 90)) &
                (prod_df["implied_vol"] > 0)
            ]
            if not atm.empty:
                daily_atm_iv = atm["implied_vol"].mean()
                hist = self._iv_history[product]
                hist["dates"].append(date_str)
                hist["ivs"].append(daily_atm_iv)
                # 计算分位数（因果窗口，只用历史数据）
                iv_series = pd.Series(hist["ivs"], index=hist["dates"])
                iv_pct = calc_iv_percentile(
                    iv_series, date_str,
                    window=cfg.get("iv_window", 252),
                    min_periods=cfg.get("iv_min_periods", 60)
                )
                product_iv_pcts[product] = iv_pct
                # 记录到每日快照
                self._iv_daily_records.append({
                    "date": date_str,
                    "product": product,
                    "atm_iv": daily_atm_iv,
                    "iv_percentile": iv_pct,
                    "iv_history_len": len(hist["ivs"]),
                })

        # 组合级平均 IV 分位数（用于 NAV 快照）
        valid_pcts = [v for v in product_iv_pcts.values() if not np.isnan(v)]
        self._current_avg_iv_pct = np.mean(valid_pcts) if valid_pcts else np.nan
        self._current_iv_pcts = product_iv_pcts

        # 3. 到期平仓
        for pos in list(self.positions):
            if pos not in self.positions:
                continue  # 已被前一个条件平仓
            if pos.dte <= cfg.get("expiry_dte", 1):
                self._execute_close(pos, pos.cur_price, "close", "expiry", 0)
            elif pos.strat == "S4" and pos.role == "buy" and pos.dte < 10:
                self._execute_close(pos, pos.cur_price, "close", "s4_dte_exit", 0)

        # 4. 新开仓（生成 pending_opens，T+1 执行）
        total_m = sum(p.cur_margin() for p in self.positions if p.role == "sell")
        s1_m = sum(p.cur_margin() for p in self.positions if p.role == "sell" and p.strat == "S1")
        s3_m = sum(p.cur_margin() for p in self.positions if p.role == "sell" and p.strat == "S3")
        # 追踪 pending 的预估保证金（pending 未加入 positions，需要单独累加）
        pending_total_m = 0.0
        pending_s1_m = 0.0
        pending_s3_m = 0.0
        margin_cap = cfg.get("margin_cap", 0.50)
        s1_cap = cfg.get("s1_margin_cap", 0.25)
        s3_cap = cfg.get("s3_margin_cap", 0.25)
        margin_per = cfg.get("margin_per", 0.02)
        iv_open_thr = cfg.get("iv_open_threshold", 80)
        top_n = cfg.get("products_top_n", 20)

        for product in sorted_products[:top_n]:
            prod_df = daily_df[daily_df["product"] == product]
            if prod_df.empty:
                continue

            # 检查开仓条件（Bug 6 修复：import 已移到文件顶部）
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

                # IV 分位数检查 + 因子1（波动率自适应仓位）
                iv_pct = product_iv_pcts.get(product, np.nan)
                # IV>80% 暂停 S1/S3 开仓（S4 不受影响）
                if should_pause_open(iv_pct, iv_open_thr):
                    continue
                # 因子1：高IV时缩小仓位 margin_per = base * (1 - iv_pct/200)
                iv_scale = get_iv_scale(iv_pct, cfg.get("iv_threshold", 75))

                # 保证金检查（含 pending 预估）
                eff_total_m = total_m + pending_total_m
                if margin_cap and eff_total_m / max(nav, 1) >= margin_cap:
                    break

                spot = ef["spot_close"].iloc[0] if "spot_close" in ef.columns else 0
                mult = ef["multiplier"].iloc[0] if "multiplier" in ef.columns else 10
                exchange = ef["exchange"].iloc[0] if "exchange" in ef.columns else ""
                # 保证金比例按交易所区分
                if exchange in ("CFFEX",):
                    mr = 0.10  # 股指期权保证金较高
                elif exchange in ("SSE", "SZSE"):
                    mr = 0.12  # ETF 期权
                else:
                    mr = 0.05  # 商品期权

                # ── S3 开仓（优先）──
                eff_s3_m = s3_m + pending_s3_m
                if s3_cap and eff_s3_m / max(nav, 1) < s3_cap:
                    # S3 也使用 Delta 平衡的方向优先级
                    s3_ot_order = self.monitor.get_delta_preferred_order(
                        self.positions, nav)
                    for ot in s3_ot_order:
                        if any(p.strat == "S3" and p.product == product and
                               p.opt_type == ot and p.role == "sell"
                               for p in self.positions):
                            continue
                        if self.monitor.should_skip_direction(
                                self.positions, nav, ot):
                            continue
                        old_pending_len = len(self._pending_opens)
                        self._try_open_s3(ef, product, ot, spot, mult, mr,
                                          exchange, exp, nav, margin_per,
                                          iv_scale, smiles, date_str, day_slice,
                                          spread_mode)
                        # 更新 pending 保证金预估
                        for item in self._pending_opens[old_pending_len:]:
                            if item["role"] == "sell":
                                est_m = estimate_margin(
                                    item["spot"], item["strike"], item["opt_type"],
                                    item["ref_price"], item["mult"], item["mr"], 0.5,
                                    exchange=item["exchange"]
                                ) * item["n"]
                                pending_total_m += est_m
                                if item["strat"] == "S3":
                                    pending_s3_m += est_m

                # ── S1 开仓 ──
                eff_s1_m = s1_m + pending_s1_m
                if s1_cap and eff_s1_m / max(nav, 1) < s1_cap:
                    # Delta 平衡：根据净 Delta 方向决定 Put/Call 优先级
                    s1_ot_order = self.monitor.get_delta_preferred_order(
                        self.positions, nav)
                    for ot in s1_ot_order:
                        eff_total_m = total_m + pending_total_m
                        if margin_cap and eff_total_m / max(nav, 1) >= margin_cap:
                            break
                        # 跳过会加剧 Delta 偏离的方向
                        if self.monitor.should_skip_direction(
                                self.positions, nav, ot):
                            continue
                        old_pending_len = len(self._pending_opens)
                        self._try_open_s1(ef, product, ot, mult, mr,
                                          exchange, exp, nav, margin_per,
                                          iv_scale, smiles, date_str, day_slice,
                                          spread_mode)
                        for item in self._pending_opens[old_pending_len:]:
                            if item["role"] == "sell":
                                est_m = estimate_margin(
                                    item["spot"], item["strike"], item["opt_type"],
                                    item["ref_price"], item["mult"], item["mr"], 0.5,
                                    exchange=item["exchange"]
                                ) * item["n"]
                                pending_total_m += est_m
                                if item["strat"] == "S1":
                                    pending_s1_m += est_m

                # ── S4 开仓 ──
                if not any(p.strat == "S4" and p.product == product and
                           p.expiry == exp for p in self.positions):
                    self._try_open_s4(ef, product, mult, mr, exchange, exp,
                                      nav, date_str, day_slice, spread_mode)


    # ── 开仓辅助 ──────────────────────────────────────────────────────────────

    def _try_open_s1(self, ef, product, ot, mult, mr, exchange, exp,
                     nav, margin_per, iv_scale, smiles, date_str,
                     day_slice, spread_mode):
        """尝试 S1 开仓（因子5：IV_Residual 加权选腿）"""
        # 因子5：为候选合约计算 IV_Residual（IV 相对 smile 的偏离）
        ef_with_ivr = ef.copy()
        if smiles and product in smiles:
            smile = smiles[product]
            if hasattr(smile, 'get') and exp in smile:
                smile_data = smile[exp]
            else:
                smile_data = smile
            # 批量计算 iv_residual
            ef_with_ivr["iv_residual"] = ef_with_ivr.apply(
                lambda r: get_iv_residual(smiles, product, exp,
                                          r["strike"], r["spot_close"],
                                          r.get("implied_vol", 0)),
                axis=1
            )
        else:
            ef_with_ivr["iv_residual"] = 0.0

        c = select_s1_sell(ef_with_ivr, ot, mult, mr)
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

        # 执行价格：T+1 日 VWAP（在 _execute_pending_opens 中计算）
        # 这里先用收盘价作为参考价，实际执行价在 T+1 确定
        ref_price = c["option_close"]

        group_id = f"S1_{product}_{ot}_{exp}_{date_str}"
        iv_res = get_iv_residual(smiles, product, exp,
                                  c["strike"], c["spot_close"],
                                  c.get("implied_vol", 0))

        # 加入待开仓列表（T+1 执行）
        self._pending_opens.append({
            "strat": "S1", "product": product, "code": c["option_code"],
            "opt_type": ot, "strike": c["strike"], "ref_price": ref_price,
            "n": nn, "decision_date": date_str, "mult": mult, "expiry": exp,
            "mr": mr, "role": "sell", "spot": c["spot_close"],
            "exchange": exchange, "group_id": group_id, "iv_residual": iv_res,
            "underlying_code": c.get("underlying_code"),
        })

        # 保护腿
        pr = select_s1_protect(ef, c)
        if pr is not None and pr["option_code"] != c["option_code"]:
            pn = max(1, nn // 2)
            self._pending_opens.append({
                "strat": "S1", "product": product, "code": pr["option_code"],
                "opt_type": ot, "strike": pr["strike"], "ref_price": pr["option_close"],
                "n": pn, "decision_date": date_str, "mult": mult, "expiry": exp,
                "mr": mr, "role": "buy", "spot": pr["spot_close"],
                "exchange": exchange, "group_id": group_id, "iv_residual": 0,
                "underlying_code": pr.get("underlying_code"),
            })

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

        # 买腿加入待开仓
        iv_res = get_iv_residual(smiles, product, exp,
                                  sl["strike"], sl["spot_close"],
                                  sl.get("implied_vol", 0))
        self._pending_opens.append({
            "strat": "S3", "product": product, "code": bl["option_code"],
            "opt_type": ot, "strike": bl["strike"], "ref_price": bl["option_close"],
            "n": bq, "decision_date": date_str, "mult": mult, "expiry": exp,
            "mr": mr, "role": "buy", "spot": bl["spot_close"],
            "exchange": exchange, "group_id": group_id, "iv_residual": 0,
            "underlying_code": bl.get("underlying_code"),
        })

        # 卖腿加入待开仓
        self._pending_opens.append({
            "strat": "S3", "product": product, "code": sl["option_code"],
            "opt_type": ot, "strike": sl["strike"], "ref_price": sl["option_close"],
            "n": sq, "decision_date": date_str, "mult": mult, "expiry": exp,
            "mr": mr, "role": "sell", "spot": sl["spot_close"],
            "exchange": exchange, "group_id": group_id, "iv_residual": iv_res,
            "underlying_code": sl.get("underlying_code"),
        })

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
            self._pending_opens.append({
                "strat": "S4", "product": product, "code": opt["option_code"],
                "opt_type": ot, "strike": opt["strike"],
                "ref_price": opt["option_close"],
                "n": qty, "decision_date": date_str, "mult": mult, "expiry": exp,
                "mr": mr, "role": "buy", "spot": opt["spot_close"],
                "exchange": exchange, "group_id": group_id, "iv_residual": 0,
                "underlying_code": opt.get("underlying_code"),
            })


    # ── 执行与记录 ────────────────────────────────────────────────────────────

    def _execute_pending_opens(self, day_slice, date_str):
        """
        执行昨日决策的待开仓订单，用 T+1 日开盘后 VWAP 作为执行价格。

        这消除了前视偏差：T 日收盘决策 → T+1 日开盘执行。
        """
        if not self._pending_opens:
            return

        spread_mode = self.config.get("spread_mode", "tick")
        vwap_window = self.config.get("vwap_window", 10)
        nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)

        executed = 0
        for item in self._pending_opens:
            code = item["code"]

            # T+1 VWAP 执行价格
            vwap = day_slice.calc_vwap(code, vwap_window)
            if vwap and vwap > 0:
                price = vwap
            else:
                # fallback: 用决策日收盘价（ref_price）
                price = item["ref_price"]

            if price <= 0:
                continue

            # 施加买卖价差
            role = item["role"]
            spread = self.monitor.calc_spread(price, code, self.cm, spread_mode)
            direction = "buy" if role in ("buy", "protect") else "sell"
            price = IntradayMonitor.apply_spread(price, direction, spread)

            # 保证金二次验证（卖腿）
            if role == "sell":
                total_m = sum(p.cur_margin() for p in self.positions if p.role == "sell")
                strat = item["strat"]
                strat_m = sum(p.cur_margin() for p in self.positions
                              if p.role == "sell" and p.strat == strat)
                new_m = estimate_margin(
                    item["spot"], item["strike"], item["opt_type"],
                    price, item["mult"], item["mr"], 0.5,
                    exchange=item["exchange"]
                ) * item["n"]
                cap_key = f"{strat.lower()}_margin_cap"
                if not check_margin_ok(total_m, strat_m, new_m, nav,
                                       self.config.get("margin_cap", 0.50),
                                       self.config.get(cap_key, 0.25)):
                    continue

            # 创建持仓
            pos = Position(
                item["strat"], item["product"], code, item["opt_type"],
                item["strike"], price, item["n"], date_str,
                item["mult"], item["expiry"], item["mr"], role,
                spot=item["spot"], exchange=item["exchange"],
                group_id=item["group_id"], iv_residual=item.get("iv_residual", 0),
                underlying_code=item.get("underlying_code"),
            )
            self.positions.append(pos)

            action = f"open_{direction}"
            self._record_order(
                date_str, "vwap", action, item["strat"], item["product"],
                code, item["opt_type"], item["strike"], item["expiry"],
                price, item["n"], 0, 0, item.get("iv_residual", 0),
            )
            executed += 1

        if executed > 0:
            logger.debug("  T+1 执行 %d/%d 笔待开仓", executed, len(self._pending_opens))
        self._pending_opens = []

    def _execute_close(self, position, price, timestamp, action, spread=0):
        """执行平仓：计算PnL、扣手续费、记录订单、移除持仓"""
        close_role = "buy" if position.role == "sell" else "sell"
        exec_price = IntradayMonitor.apply_spread(price, close_role, spread)

        # PnL：当日盯市部分（prev_price → exec_price），不是全生命周期
        # 全生命周期 PnL 已通过前几天的 daily_pnl() 逐日计入 cum_pnl
        if position.role in ("buy", "protect"):
            pnl = (exec_price - position.prev_price) * position.mult * position.n
        else:
            pnl = (position.prev_price - exec_price) * position.mult * position.n

        fee = self.config.get("fee", 3) * position.n * 2  # 开+平

        # 累加到当日已平仓 PnL
        self._day_realized["pnl"] += pnl
        self._day_realized["fee"] += fee
        strat_key = position.strat.lower()  # "S1" → "s1"
        if strat_key in self._day_realized:
            self._day_realized[strat_key] += pnl

        # 记录订单（订单中记录全生命周期 PnL，方便分析）
        if position.role in ("buy", "protect"):
            order_pnl = (exec_price - position.open_price) * position.mult * position.n
        else:
            order_pnl = (position.open_price - exec_price) * position.mult * position.n

        # 记录订单（Bug 1 修复：用当前交易日而非 open_date）
        time_str = str(timestamp)[-8:-3] if len(str(timestamp)) > 10 else "close"
        self._record_order(
            self._current_date_str, time_str, action, position.strat,
            position.product, position.code, position.opt_type,
            position.strike, position.expiry,
            exec_price, position.n, fee, order_pnl, position.iv_residual
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
        # 持仓的当日盯市 PnL（prev_price → cur_price）
        holding_pnl = sum(p.daily_pnl() for p in self.positions)

        # 已平仓的已实现 PnL（开仓价 → 平仓价，在 _execute_close 中累加）
        realized_pnl = self._day_realized["pnl"]
        realized_fee = self._day_realized["fee"]

        # 当日总 PnL = 持仓盯市 + 已平仓已实现 - 手续费
        day_pnl = holding_pnl + realized_pnl - realized_fee

        # 分策略 PnL（持仓 + 已平仓）
        s1_pnl = (sum(p.daily_pnl() for p in self.positions if p.strat == "S1")
                  + self._day_realized["s1"])
        s3_pnl = (sum(p.daily_pnl() for p in self.positions if p.strat == "S3")
                  + self._day_realized["s3"])
        s4_pnl = (sum(p.daily_pnl() for p in self.positions if p.strat == "S4")
                  + self._day_realized["s4"])

        # PnL 归因（仅持仓部分，已平仓无法归因）
        attr = {"delta_pnl": 0.0, "gamma_pnl": 0.0,
                "theta_pnl": 0.0, "vega_pnl": 0.0}
        for p in self.positions:
            pa = p.pnl_attribution()
            attr["delta_pnl"] += pa["delta_pnl"]
            attr["gamma_pnl"] += pa["gamma_pnl"]
            attr["theta_pnl"] += pa["theta_pnl"]
            attr["vega_pnl"] += pa["vega_pnl"]

        cum_pnl = (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0) + day_pnl
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
            "fee": realized_fee,
            "margin_used": margin,
            "cash_delta": cd / max(nav, 1),
            "cash_vega": cv / max(nav, 1),
            "cash_gamma": cg / max(nav, 1),
            "cash_theta": ct,
            "delta_pnl": attr["delta_pnl"],
            "gamma_pnl": attr["gamma_pnl"],
            "theta_pnl": attr["theta_pnl"],
            "vega_pnl": attr["vega_pnl"],
            "avg_iv_pct": self._current_avg_iv_pct,
            "iv_smile_avg_r2": 0.0,
            "n_positions": len(self.positions),
        })

        # 重置 prev_price 和 prev_spot 为当前值（为下一天准备）
        for p in self.positions:
            p.prev_price = p.cur_price
            p.prev_spot = p.cur_spot

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
                f.write(f"| S1 | {nav_df['s1_pnl'].sum():.0f} |\n")
                f.write(f"| S3 | {nav_df['s3_pnl'].sum():.0f} |\n")
                f.write(f"| S4 | {nav_df['s4_pnl'].sum():.0f} |\n")
                f.write(f"| 手续费 | {nav_df['fee'].sum():.0f} |\n")

            # PnL 归因
            if "delta_pnl" in nav_df.columns:
                f.write(f"\n## PnL 归因\n\n")
                f.write("| 来源 | 累计PnL | 占比 |\n|------|---------|------|\n")
                d_total = nav_df["delta_pnl"].sum()
                g_total = nav_df["gamma_pnl"].sum()
                t_total = nav_df["theta_pnl"].sum()
                v_total = nav_df["vega_pnl"].sum()
                all_total = d_total + g_total + t_total + v_total
                safe_all = max(abs(all_total), 1)
                f.write(f"| Delta | {d_total:,.0f} | {d_total/safe_all*100:.1f}% |\n")
                f.write(f"| Gamma | {g_total:,.0f} | {g_total/safe_all*100:.1f}% |\n")
                f.write(f"| Theta | {t_total:,.0f} | {t_total/safe_all*100:.1f}% |\n")
                f.write(f"| Vega(残差) | {v_total:,.0f} | {v_total/safe_all*100:.1f}% |\n")
                f.write(f"| **合计** | **{all_total:,.0f}** | |\n")
        logger.info("报告输出: %s", report_path)

        # IV 每日数据输出
        if self._iv_daily_records:
            iv_path = os.path.join(OUTPUT_DIR, f"iv_daily_{tag}.csv")
            iv_df = pd.DataFrame(self._iv_daily_records)
            iv_df.to_csv(iv_path, index=False)
            logger.info("IV 数据输出: %s (%d 行)", iv_path, len(iv_df))


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
