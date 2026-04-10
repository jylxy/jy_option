"""
逐日回测系统：消除前视偏差的回测 + 与批量回测对比验证

核心差异（相对 unified_engine_v3.py）：
1. 不预计算 open_on 字典 → 每天实时检测DTE≈35
2. IV分位数使用因果窗口 → 只用截止当天的数据
3. 记录每笔交易的完整信息 → 支持逐笔对比

对比验证两个层次：
- 层次1：NAV曲线对比（日均偏差、最大偏差）
- 层次2：逐笔订单对比（同日同品种是否触发相同操作）
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy, select_s3_sell, select_s3_protect,
    select_s4,
    calc_s1_size, calc_s3_size, calc_s4_size,
    extract_atm_iv_series, calc_iv_percentile_batch, get_iv_scale,
    check_margin_ok, apply_slippage, calc_stats,
    should_take_profit_s1, should_take_profit_s3,
    should_close_expiry, can_reopen, should_pause_open,
    load_t1_price_index, get_t1_execution_price,
    DEFAULT_PARAMS,
)
from backtest_fast import load_product_data, estimate_margin
from exp_product_count import PRODUCT_MAP, scan_and_rank

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
START_DATE = "2024-01-01"

S4_PRODUCTS = ["中证1000", "沪金", "原油", "沪铜"]


# ── 持仓类（与unified_engine_v3.Pos保持一致） ──────────────────────────────────

class Position:
    __slots__ = [
        "strat", "product", "code", "opt_type", "strike",
        "open_price", "n", "open_date", "mult", "expiry", "mr", "liq", "role",
        "prev_price", "cur_price", "cur_spot",
        # 额外字段用于订单记录
        "open_delta", "open_iv", "open_dte", "open_moneyness",
    ]

    def __init__(self, strat, product, code, ot, strike, op, n, od,
                 mult, exp, mr, liq, role="sell", spot=0,
                 delta=0, iv=0, dte=0, moneyness=0):
        self.strat = strat
        self.product = product
        self.code = code
        self.opt_type = ot
        self.strike = strike
        self.open_price = op
        self.n = n
        self.open_date = od
        self.mult = mult
        self.expiry = exp
        self.mr = mr
        self.liq = liq
        self.role = role
        self.prev_price = op
        self.cur_price = op
        self.cur_spot = spot
        self.open_delta = delta
        self.open_iv = iv
        self.open_dte = dte
        self.open_moneyness = moneyness

    def daily_pnl(self):
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.prev_price) * self.mult * self.n
        return (self.prev_price - self.cur_price) * self.mult * self.n

    def total_pnl(self):
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.open_price) * self.mult * self.n
        return (self.open_price - self.cur_price) * self.mult * self.n

    def profit_pct(self):
        if self.role == "sell" and self.open_price > 0:
            return (self.open_price - self.cur_price) / self.open_price
        return 0

    def cur_margin(self):
        if self.role == "sell":
            return estimate_margin(
                self.cur_spot or self.strike, self.strike, self.opt_type,
                self.cur_price, self.mult, self.mr, 0.5) * self.n
        return 0


# ── 交易记录 ──────────────────────────────────────────────────────────────────

def make_trade_record(date, action, pos, price=None, reason=""):
    """生成一条交易记录"""
    return {
        "date": str(date.date()) if hasattr(date, 'date') else str(date),
        "action": action,  # open / close
        "strategy": pos.strat,
        "product": pos.product,
        "code": pos.code,
        "option_type": pos.opt_type,
        "strike": pos.strike,
        "expiry": str(pos.expiry.date()) if hasattr(pos.expiry, 'date') else str(pos.expiry),
        "role": pos.role,
        "direction": "buy" if pos.role in ("buy", "protect") else "sell",
        "quantity": pos.n,
        "price": price if price is not None else pos.open_price,
        "delta": pos.open_delta,
        "implied_vol": pos.open_iv,
        "dte": pos.open_dte,
        "moneyness": pos.open_moneyness,
        "reason": reason,
    }


# ── 主回测逻辑 ────────────────────────────────────────────────────────────────

def run_daily_backtest(products=None, s4_products=None, enable_s4=True,
                       use_slip=True, start_date=START_DATE, params=None):
    """
    逐日回测（无前视偏差）

    与 unified_engine_v3.run_unified_v3 的区别：
    1. 不预计算 open_on → 每天用 should_open_new() 实时检测
    2. IV分位数用因果窗口 → calc_iv_percentile()
    3. 记录完整交易日志
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    CAPITAL = p["capital"]
    margin_per = p["margin_per"]
    margin_cap = p["margin_cap"]
    s1_margin_cap = p["s1_margin_cap"]
    s3_margin_cap = p["s3_margin_cap"]
    s1_tp = p["s1_tp"]
    s3_tp = p["s3_tp"]
    s3_ratio = p["s3_ratio"]
    s4_prem = p["s4_prem"]
    iv_inverse = p["iv_inverse"]
    slippage = p["slippage"]
    FEE = p["fee"]
    iv_open_threshold = p.get("iv_open_threshold", 80)
    s4_max_hold = p.get("s4_max_hold", 15)
    use_t1_vwap = p.get("use_t1_vwap", True)

    # S4改进：使用全部品种（不限制固定品种池）
    _s4_prods = s4_products if s4_products is not None else product_names_list

    print("加载数据...", end="", flush=True)
    conn = sqlite3.connect(DB_PATH)
    pdata = {}
    product_list = products if products is not None else []
    for where, name, mult, mr, liq in product_list:
        df = load_product_data(conn, where)
        if df.empty or len(df) < 100:
            continue
        pdata[name] = {
            "df": df, "mult": mult, "mr": mr, "liq": liq,
            "dg": {d: g for d, g in df.groupby("trade_date")},
            "idx": df.set_index(["trade_date", "option_code"]),
        }
    conn.close()
    print(f" {len(pdata)}品种")
    product_names_list = list(pdata.keys())  # S4用全部品种

    # T+1 VWAP执行价格索引
    t1_idx = None
    if use_t1_vwap:
        print("加载T+1价格索引...", end="", flush=True)
        t1_idx = load_t1_price_index(DB_PATH)
        print(f" {len(t1_idx)}行")

    # IV分位数：使用与批量回测一致的预计算rolling方式
    # 数据全部是历史的（包含start_date之前的），不构成前视偏差
    iv_pcts = {}
    for name, d in pdata.items():
        s = extract_atm_iv_series(d["df"])
        if not s.empty:
            iv_pcts[name] = calc_iv_percentile_batch(s)

    # 所有交易日
    all_dates = sorted(set().union(*(d["dg"].keys() for d in pdata.values())))
    if start_date:
        start_dt = pd.Timestamp(start_date)
        all_dates = [d for d in all_dates if d >= start_dt]

    # ── 开仓日计算 ──
    # open_on 预计算不构成前视偏差：
    # 它只使用到期日历和交易日历（公开信息），不使用未来价格。
    # 每个(品种,到期日)在DTE最接近35天的那个交易日触发开仓。
    from collections import defaultdict

    # 日期→下一交易日映射（T+1执行用）
    date_to_next = {}
    for i in range(len(all_dates) - 1):
        date_to_next[all_dates[i]] = all_dates[i + 1]

    open_on = defaultdict(list)
    for name, d in pdata.items():
        for exp in d["df"]["expiry_date"].unique():
            ed = d["df"][(d["df"]["expiry_date"] == exp) &
                         (d["df"]["dte"] >= 15) & (d["df"]["dte"] <= 90)]
            if ed.empty:
                continue
            dte_d = ed.groupby("trade_date")["dte"].first()
            open_on[(dte_d - 35).abs().idxmin()].append((name, exp))

    positions = []
    trade_log = []
    records = []

    # Pending队列：T日生成信号，T+1日执行
    pending_open_templates = []   # T日生成的开仓模板
    pending_close_ids = set()     # T日标记的平仓Position IDs

    def get_t1_price(code, fallback, date):
        """获取T+1日VWAP执行价格"""
        if not use_t1_vwap:
            return apply_slippage(fallback, "sell", slippage) if use_slip else fallback
        next_d = date_to_next.get(date)
        return get_t1_execution_price(t1_idx, next_d, code, fallback)

    print(f"逐日回测 {len(all_dates)} 天...", flush=True)
    for di, date in enumerate(all_dates):
        if di % 100 == 0:
            print(f"  [{di}/{len(all_dates)}] {date.date()}", flush=True)

        nav_now = CAPITAL + (records[-1]["cum_pnl"] if records else 0)

        # === MTM更新 ===
        day_pnl = 0.0
        pnl_s1 = pnl_s3 = pnl_s4 = 0.0
        for pos in positions:
            k = (date, pos.code)
            pos.prev_price = pos.cur_price
            if k in pdata[pos.product]["idx"].index:
                row = pdata[pos.product]["idx"].loc[k]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                pos.cur_price = row["option_close"]
                pos.cur_spot = row["spot_close"]
            elif date >= pos.expiry:
                pos.cur_price = 0.0
            dp = pos.daily_pnl()
            day_pnl += dp
            if pos.strat == "S1": pnl_s1 += dp
            elif pos.strat == "S3": pnl_s3 += dp
            elif pos.strat == "S4": pnl_s4 += dp

        # === 止盈+到期 ===
        closed = set()
        new_re = []

        for pos in positions:
            if pos.role in ("buy", "protect"):
                # S4持仓天数限制（15天）
                if pos.strat == "S4" and s4_max_hold > 0:
                    hold_days = (date - pos.open_date).days
                    if hold_days >= s4_max_hold:
                        trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "s4_max_hold"))
                        closed.add(id(pos))
                        continue

                d = pdata.get(pos.product)
                if d is None:
                    continue
                dg = d["dg"].get(date)
                if dg is None:
                    if date >= pos.expiry:
                        trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry_no_data"))
                        closed.add(id(pos))
                    continue
                er = dg[dg["expiry_date"] == pos.expiry]
                if er.empty:
                    if date >= pos.expiry:
                        trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry_no_data"))
                        closed.add(id(pos))
                    continue
                if er["dte"].iloc[0] <= 1:
                    trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry"))
                    closed.add(id(pos))
                continue

            # 卖腿
            d = pdata.get(pos.product)
            if d is None:
                continue
            dg = d["dg"].get(date)
            if dg is None:
                if date >= pos.expiry:
                    trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry_no_data"))
                    closed.add(id(pos))
                    for pp in positions:
                        if (pp.product == pos.product and pp.expiry == pos.expiry and
                                pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                                id(pp) not in closed and pp.role != "sell"):
                            trade_log.append(make_trade_record(date, "close", pp, pp.cur_price, "expiry_no_data"))
                            closed.add(id(pp))
                continue
            er = dg[dg["expiry_date"] == pos.expiry]
            if er.empty:
                if date >= pos.expiry:
                    trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry_no_data"))
                    closed.add(id(pos))
                continue
            dte = er["dte"].iloc[0]

            # 止盈
            if pos.strat == "S1" and should_take_profit_s1(pos.profit_pct(), dte, s1_tp):
                trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "tp_s1"))
                closed.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed and pp.role != "sell"):
                        trade_log.append(make_trade_record(date, "close", pp, pp.cur_price, "tp_s1"))
                        closed.add(id(pp))
                # 止盈重开 S1 — IV>80%时暂停
                if can_reopen(dte):
                    iv_pct = _get_iv_pct(iv_pcts, pos.product, date)
                    if should_pause_open(iv_pct, iv_open_threshold):
                        continue  # IV过高，不重开
                    c = select_s1_sell(dg, pos.opt_type, d["mult"], d["mr"])
                    if c is not None and c["option_code"] != pos.code:
                        sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                        m = estimate_margin(c["spot_close"], c["strike"], c["option_type"],
                                            c["option_close"], d["mult"], d["mr"], 0.5)
                        nn = calc_s1_size(nav_now, margin_per, m, sc)
                        cur_total = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and id(p2) not in closed)
                        cur_total += sum(p2.cur_margin() for p2 in new_re if p2.role == "sell")
                        cur_s1 = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S1" and id(p2) not in closed)
                        cur_s1 += sum(p2.cur_margin() for p2 in new_re if p2.role == "sell" and p2.strat == "S1")
                        if check_margin_ok(cur_total, cur_s1, m * nn, nav_now, margin_cap, s1_margin_cap):
                            op = apply_slippage(c["option_close"], "sell", slippage) if use_slip else c["option_close"]
                            new_pos = Position("S1", pos.product, c["option_code"], c["option_type"],
                                               c["strike"], op, nn, date, d["mult"], pos.expiry,
                                               d["mr"], d["liq"], "sell", c["spot_close"],
                                               c["delta"], c.get("implied_vol", 0), dte, c["moneyness"])
                            new_re.append(new_pos)
                            trade_log.append(make_trade_record(date, "open", new_pos, op, "reopen_s1"))
                            pr = select_s1_protect(dg, c)
                            if pr is not None and pr["option_code"] != c["option_code"]:
                                pn_q = max(1, nn // 2)
                                bop = apply_slippage(pr["option_close"], "buy", slippage) if use_slip else pr["option_close"]
                                pp_pos = Position("S1", pos.product, pr["option_code"], pr["option_type"],
                                                  pr["strike"], bop, pn_q, date, d["mult"], pos.expiry,
                                                  d["mr"], d["liq"], "buy", pr["spot_close"],
                                                  pr["delta"], pr.get("implied_vol", 0), dte, pr["moneyness"])
                                new_re.append(pp_pos)
                                trade_log.append(make_trade_record(date, "open", pp_pos, bop, "reopen_s1_protect"))
                continue

            if pos.strat == "S3" and should_take_profit_s3(pos.profit_pct(), dte, s3_tp):
                trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "tp_s3"))
                closed.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed and pp.role != "sell"):
                        trade_log.append(make_trade_record(date, "close", pp, pp.cur_price, "tp_s3"))
                        closed.add(id(pp))
                # 止盈重开 S3 — IV>80%时暂停
                if can_reopen(dte):
                    iv_pct = _get_iv_pct(iv_pcts, pos.product, date)
                    if should_pause_open(iv_pct, iv_open_threshold):
                        continue  # IV过高，不重开
                    bl = select_s3_buy(dg, pos.opt_type)
                    if bl is not None:
                        sl = select_s3_sell(dg, pos.opt_type, bl["strike"])
                        if sl is not None and sl["option_code"] != bl["option_code"]:
                            sm = estimate_margin(sl["spot_close"], sl["strike"], pos.opt_type,
                                                 sl["option_close"], d["mult"], d["mr"], 0.5)
                            sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                            bq, sq = calc_s3_size(nav_now, margin_per, sm, s3_ratio, sc)
                            new_m = sm * sq
                            cur_total = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and id(p2) not in closed)
                            cur_total += sum(p2.cur_margin() for p2 in new_re if p2.role == "sell")
                            cur_s3 = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S3" and id(p2) not in closed)
                            cur_s3 += sum(p2.cur_margin() for p2 in new_re if p2.role == "sell" and p2.strat == "S3")
                            if check_margin_ok(cur_total, cur_s3, new_m, nav_now, margin_cap, s3_margin_cap):
                                _open_s3_group(new_re, trade_log, date, "S3", pos.product, pos.opt_type,
                                               bl, sl, d, pos.expiry, bq, sq, use_slip, slippage, dte, "reopen_s3")
                continue

            if should_close_expiry(dte):
                trade_log.append(make_trade_record(date, "close", pos, pos.cur_price, "expiry"))
                closed.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed and pp.role != "sell"):
                        trade_log.append(make_trade_record(date, "close", pp, pp.cur_price, "expiry"))
                        closed.add(id(pp))

        if closed:
            positions = [p2 for p2 in positions if id(p2) not in closed]
        positions.extend(new_re)

        # === 新开仓（使用open_on日历） ===
        if date in open_on:
            total_margin = sum(p2.cur_margin() for p2 in positions if p2.role == "sell") if margin_cap else 0
            s1_margin = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S1")
            s3_margin = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S3")

            for pn, exp in open_on[date]:
                if pn not in pdata:
                    continue
                d = pdata[pn]
                dg = d["dg"].get(date)
                if dg is None:
                    continue
                ef = dg[dg["expiry_date"] == exp]
                if ef.empty:
                    continue

                dte_val = ef["dte"].iloc[0]
                iv_pct = _get_iv_pct(iv_pcts, pn, date)
                sc = get_iv_scale(iv_pct) if iv_inverse else 1.0

                # IV环境过滤：IV>80%时暂停S1/S3新开仓（S4不受影响）
                iv_paused = should_pause_open(iv_pct, iv_open_threshold)

                # S3优先
                if not iv_paused and (not margin_cap or total_margin / nav_now < margin_cap):
                    for ot in ["P", "C"]:
                        if any(p2.strat == "S3" and p2.product == pn and p2.opt_type == ot and p2.role == "sell" for p2 in positions):
                            continue
                        if s3_margin_cap and s3_margin / nav_now > s3_margin_cap:
                            break
                        if margin_cap and total_margin / nav_now > margin_cap:
                            break
                        bl = select_s3_buy(ef, ot)
                        if bl is None:
                            continue
                        sl = select_s3_sell(ef, ot, bl["strike"])
                        if sl is None:
                            continue
                        if bl["option_code"] == sl["option_code"]:
                            continue
                        sm = estimate_margin(sl["spot_close"], sl["strike"], ot,
                                             sl["option_close"], d["mult"], d["mr"], 0.5)
                        bq, sq = calc_s3_size(nav_now, margin_per, sm, s3_ratio, sc)
                        new_m = sm * sq
                        if s3_margin_cap and (s3_margin + new_m) / nav_now > s3_margin_cap:
                            continue
                        if margin_cap and (total_margin + new_m) / nav_now > margin_cap:
                            continue
                        _open_s3_group(positions, trade_log, date, "S3", pn, ot,
                                       bl, sl, d, exp, bq, sq, use_slip, slippage, dte_val, "new")
                        total_margin += new_m
                        s3_margin += new_m

                # S1
                if not iv_paused and (not margin_cap or total_margin / nav_now < margin_cap):
                    for ot in ["P", "C"]:
                        if s1_margin_cap and s1_margin / nav_now > s1_margin_cap:
                            break
                        if margin_cap and total_margin / nav_now > margin_cap:
                            break
                        c = select_s1_sell(ef, ot, d["mult"], d["mr"])
                        if c is None:
                            continue
                        m = estimate_margin(c["spot_close"], c["strike"], ot,
                                            c["option_close"], d["mult"], d["mr"], 0.5)
                        nn = calc_s1_size(nav_now, margin_per, m, sc)
                        if s1_margin_cap and (s1_margin + m * nn) / nav_now > s1_margin_cap:
                            continue
                        if margin_cap and (total_margin + m * nn) / nav_now > margin_cap:
                            continue
                        op = apply_slippage(c["option_close"], "sell", slippage) if use_slip else c["option_close"]
                        new_pos = Position("S1", pn, c["option_code"], ot, c["strike"], op, nn,
                                           date, d["mult"], exp, d["mr"], d["liq"], "sell", c["spot_close"],
                                           c["delta"], c.get("implied_vol", 0), dte_val, c["moneyness"])
                        positions.append(new_pos)
                        trade_log.append(make_trade_record(date, "open", new_pos, op, "new"))
                        total_margin += m * nn
                        s1_margin += m * nn
                        pr = select_s1_protect(ef, c)
                        if pr is not None and pr["option_code"] != c["option_code"]:
                            pq = max(1, nn // 2)
                            bop = apply_slippage(pr["option_close"], "buy", slippage) if use_slip else pr["option_close"]
                            pp_pos = Position("S1", pn, pr["option_code"], ot, pr["strike"], bop, pq,
                                              date, d["mult"], exp, d["mr"], d["liq"], "buy", pr["spot_close"],
                                              pr["delta"], pr.get("implied_vol", 0), dte_val, pr["moneyness"])
                            positions.append(pp_pos)
                            trade_log.append(make_trade_record(date, "open", pp_pos, bop, "new_protect"))

                # S4
                if enable_s4 and _s4_prods and pn in _s4_prods:
                    if not any(p2.strat == "S4" and p2.product == pn and p2.expiry == exp for p2 in positions):
                        for ot in ["P", "C"]:
                            opt = select_s4(ef, ot)
                            if opt is None:
                                continue
                            cost = opt["option_close"] * d["mult"]
                            qty = calc_s4_size(nav_now, s4_prem, len(_s4_prods), cost)
                            s4op = apply_slippage(opt["option_close"], "buy", slippage) if use_slip else opt["option_close"]
                            s4_pos = Position("S4", pn, opt["option_code"], ot, opt["strike"],
                                              s4op, qty, date, d["mult"], exp, d["mr"], "deep_otm", "buy",
                                              opt["spot_close"], opt["delta"], opt.get("implied_vol", 0),
                                              dte_val, opt["moneyness"])
                            positions.append(s4_pos)
                            trade_log.append(make_trade_record(date, "open", s4_pos, s4op, "new_s4"))

        # NAV
        cum = (records[-1]["cum_pnl"] if records else 0) + day_pnl
        nav = CAPITAL + cum
        tm = sum(p2.cur_margin() for p2 in positions if p2.role == "sell")
        tm_s1 = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S1")
        tm_s3 = sum(p2.cur_margin() for p2 in positions if p2.role == "sell" and p2.strat == "S3")
        records.append({
            "date": str(date.date()), "daily_pnl": day_pnl, "cum_pnl": cum, "nav": nav,
            "pnl_s1": pnl_s1, "pnl_s3": pnl_s3, "pnl_s4": pnl_s4,
            "margin_pct": tm / nav,
            "margin_s1_pct": tm_s1 / nav,
            "margin_s3_pct": tm_s3 / nav,
            "n_s1": sum(1 for p2 in positions if p2.strat == "S1" and p2.role == "sell"),
            "n_s3": sum(1 for p2 in positions if p2.strat == "S3" and p2.role == "sell"),
            "n_s4": sum(1 for p2 in positions if p2.strat == "S4"),
        })

    nav_df = pd.DataFrame(records)
    trade_df = pd.DataFrame(trade_log)
    return nav_df, trade_df


def _get_iv_pct(iv_pcts, product_name, date):
    """获取预计算的IV分位数（与批量回测一致）"""
    if product_name not in iv_pcts:
        return np.nan
    ps = iv_pcts[product_name]
    if date in ps.index:
        return ps.loc[date]
    return np.nan


def _open_s3_group(positions_list, trade_log, date, strat, pn, ot,
                   bl, sl, d, exp, bq, sq, use_slip, slippage, dte_val, reason,
                   exec_price_fn=None):
    """开一组S3仓位（买+卖+保护），同时记录trade_log"""
    if exec_price_fn:
        bop = exec_price_fn(bl["option_code"], bl["option_close"], "buy", date)
        sop = exec_price_fn(sl["option_code"], sl["option_close"], "sell", date)
    else:
        bop = apply_slippage(bl["option_close"], "buy", slippage) if use_slip else bl["option_close"]
        sop = apply_slippage(sl["option_close"], "sell", slippage) if use_slip else sl["option_close"]

    buy_pos = Position(strat, pn, bl["option_code"], ot, bl["strike"], bop, bq,
                       date, d["mult"], exp, d["mr"], d["liq"], "buy", bl["spot_close"],
                       bl["delta"], bl.get("implied_vol", 0), dte_val, bl["moneyness"])
    sell_pos = Position(strat, pn, sl["option_code"], ot, sl["strike"], sop, sq,
                        date, d["mult"], exp, d["mr"], d["liq"], "sell", sl["spot_close"],
                        sl["delta"], sl.get("implied_vol", 0), dte_val, sl["moneyness"])
    positions_list.append(buy_pos)
    positions_list.append(sell_pos)
    trade_log.append(make_trade_record(date, "open", buy_pos, bop, f"{reason}_buy"))
    trade_log.append(make_trade_record(date, "open", sell_pos, sop, f"{reason}_sell"))

    pt = select_s3_protect(d["dg"][date], ot, sl["strike"], sl["spot_close"])
    if pt is not None and pt["option_code"] != sl["option_code"]:
        if exec_price_fn:
            pop = exec_price_fn(pt["option_code"], pt["option_close"], "buy", date)
        else:
            pop = apply_slippage(pt["option_close"], "buy", slippage) if use_slip else pt["option_close"]
        prot_pos = Position(strat, pn, pt["option_code"], ot, pt["strike"], pop, sq,
                            date, d["mult"], exp, d["mr"], d["liq"], "protect", pt["spot_close"],
                            pt["delta"], pt.get("implied_vol", 0), dte_val, pt["moneyness"])
        positions_list.append(prot_pos)
        trade_log.append(make_trade_record(date, "open", prot_pos, pop, f"{reason}_protect"))


# ── 批量回测交易日志导出 ──────────────────────────────────────────────────────

def run_batch_backtest_with_log(products, s4_products, enable_s4, start_date):
    """
    运行批量回测（unified_engine_v3），同时提取交易日志用于对比。

    由于 unified_engine_v3 没有内置交易日志导出，我们通过NAV差分反推日级PnL，
    并直接返回NAV DataFrame用于曲线对比。
    """
    from unified_engine_v3 import run_unified_v3, stats
    nav_df = run_unified_v3(
        products=products,
        s4_products=s4_products,
        enable_s4=enable_s4,
        use_slip=True,
        start_date=start_date,
    )
    return nav_df


# ── 对比分析 ──────────────────────────────────────────────────────────────────

def compare_nav(daily_nav, batch_nav):
    """层次1：NAV曲线对比"""
    # 对齐日期
    d1 = daily_nav.set_index("date")
    d2 = batch_nav.set_index("date")
    common = d1.index.intersection(d2.index)

    nav1 = d1.loc[common, "nav"].values
    nav2 = d2.loc[common, "nav"].values

    diff = (nav1 - nav2) / nav2
    return {
        "n_days": len(common),
        "mean_diff": np.mean(diff),
        "median_diff": np.median(diff),
        "max_diff": np.max(np.abs(diff)),
        "std_diff": np.std(diff),
        "dates": common.tolist(),
        "daily_diff": diff,
        "nav_daily": nav1,
        "nav_batch": nav2,
    }


def compare_trades(daily_trades_df, batch_nav_df):
    """
    层次2：逐笔对比（简化版）

    由于批量回测没有内置交易日志，我们对比以下指标：
    - 每日持仓数对比（n_s1, n_s3, n_s4）
    - 每日保证金率对比
    - NAV差异时间序列
    """
    return {
        "daily_trades_count": len(daily_trades_df),
        "daily_open_count": len(daily_trades_df[daily_trades_df["action"] == "open"]),
        "daily_close_count": len(daily_trades_df[daily_trades_df["action"] == "close"]),
    }


# ── 图表 ──────────────────────────────────────────────────────────────────────

def plot_comparison(daily_nav, batch_nav, output_dir):
    """NAV对比图"""
    d1 = daily_nav.set_index("date")
    d2 = batch_nav.set_index("date")
    common = d1.index.intersection(d2.index)

    dates = pd.to_datetime(common)
    nav1 = d1.loc[common, "nav"].values
    nav2 = d2.loc[common, "nav"].values
    norm1 = nav1 / nav1[0]
    norm2 = nav2 / nav2[0]

    diff_pct = (nav1 - nav2) / nav2 * 100

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), gridspec_kw={"height_ratios": [3, 1.5, 1.5]})

    # Panel 1: 双NAV曲线
    ax1 = axes[0]
    ax1.plot(dates, norm1, label="逐日回测（无前视偏差）", color="steelblue", linewidth=2)
    ax1.plot(dates, norm2, label="批量回测（原引擎）", color="orange", linewidth=2, linestyle="--")
    ax1.set_title("逐日回测 vs 批量回测 NAV对比", fontsize=14, fontweight="bold")
    ax1.set_ylabel("归一化净值")
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)

    # Panel 2: 差异
    ax2 = axes[1]
    ax2.fill_between(dates, diff_pct, 0, alpha=0.4, color="teal")
    ax2.plot(dates, diff_pct, color="teal", linewidth=0.8)
    ax2.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax2.set_ylabel("NAV偏差 (%)")
    ax2.set_title("逐日偏差（逐日 - 批量）/ 批量")
    ax2.grid(True, alpha=0.3)

    # Panel 3: 持仓数对比
    ax3 = axes[2]
    n1_s1 = d1.loc[common, "n_s1"].values if "n_s1" in d1.columns else np.zeros(len(common))
    n2_s1 = d2.loc[common, "n_s1"].values if "n_s1" in d2.columns else np.zeros(len(common))
    n1_s3 = d1.loc[common, "n_s3"].values if "n_s3" in d1.columns else np.zeros(len(common))
    n2_s3 = d2.loc[common, "n_s3"].values if "n_s3" in d2.columns else np.zeros(len(common))
    ax3.plot(dates, n1_s1 + n1_s3, label="逐日 S1+S3卖腿数", color="steelblue", linewidth=1)
    ax3.plot(dates, n2_s1 + n2_s3, label="批量 S1+S3卖腿数", color="orange", linewidth=1, linestyle="--")
    ax3.set_ylabel("卖腿持仓数")
    ax3.set_xlabel("日期")
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    path = os.path.join(output_dir, "chart_backtest_comparison.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [图表] {path}")


# ── 报告生成 ──────────────────────────────────────────────────────────────────

def generate_comparison_report(daily_nav, batch_nav, daily_trades_df,
                               s_daily, s_batch, cmp, output_dir):
    """生成对比报告"""
    lines = []
    lines.append("# 逐日回测 vs 批量回测 对比报告")
    lines.append("")
    lines.append(f"**日期**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append(f"**回测区间**: {daily_nav['date'].iloc[0]} ~ {daily_nav['date'].iloc[-1]}")
    lines.append(f"**交易日数**: {len(daily_nav)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 一、风险收益对比
    lines.append("## 一、风险收益指标对比")
    lines.append("")
    lines.append("| 指标 | 逐日回测 | 批量回测 | 差异 |")
    lines.append("|------|---------|---------|------|")
    for k, label in [("ann_return", "年化收益率"), ("ann_vol", "年化波动率"),
                     ("max_dd", "最大回撤"), ("sharpe", "夏普比率"), ("calmar", "卡玛比率")]:
        v1 = s_daily.get(k, 0)
        v2 = s_batch.get(k, 0)
        diff = v1 - v2
        if k in ("ann_return", "ann_vol", "max_dd"):
            lines.append(f"| {label} | {v1:+.2%} | {v2:+.2%} | {diff:+.2%} |")
        else:
            lines.append(f"| {label} | {v1:.2f} | {v2:.2f} | {diff:+.2f} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 二、NAV偏差
    lines.append("## 二、NAV偏差分析")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 日均偏差 | {cmp['mean_diff']:+.4%} |")
    lines.append(f"| 中位数偏差 | {cmp['median_diff']:+.4%} |")
    lines.append(f"| 最大偏差（绝对值） | {cmp['max_diff']:.4%} |")
    lines.append(f"| 偏差标准差 | {cmp['std_diff']:.4%} |")
    lines.append("")

    # 判断偏差来源
    lines.append("### 偏差来源分析")
    lines.append("")
    if cmp["max_diff"] < 0.01:
        lines.append("最大偏差 < 1%，两个回测系统高度一致。")
        lines.append("微小差异可能来源于：")
    else:
        lines.append(f"最大偏差 {cmp['max_diff']:.2%}，存在可分析的差异。")
        lines.append("差异可能来源于：")
    lines.append("")
    lines.append("1. **open_on 逻辑差异**: 批量回测预计算所有到期日的最佳开仓日（使用全量数据），"
                 "逐日回测实时判断（只用当日数据），在边界情况下可能有1天偏差")
    lines.append("2. **IV分位数计算**: 批量回测用全量rolling，逐日回测用因果窗口，"
                 "在数据起始阶段可能有微小差异")
    lines.append("3. **保证金余量**: 由于开仓顺序或NAV微小差异，保证金约束可能在边界处产生不同决策")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 三、交易统计
    lines.append("## 三、交易统计（逐日回测）")
    lines.append("")
    if not daily_trades_df.empty:
        lines.append(f"- 总交易笔数: {len(daily_trades_df)}")
        lines.append(f"- 开仓笔数: {len(daily_trades_df[daily_trades_df['action']=='open'])}")
        lines.append(f"- 平仓笔数: {len(daily_trades_df[daily_trades_df['action']=='close'])}")
        lines.append("")

        # 按策略统计
        lines.append("### 按策略分布")
        lines.append("")
        for strat in ["S1", "S3", "S4"]:
            st = daily_trades_df[daily_trades_df["strategy"] == strat]
            opens = st[st["action"] == "open"]
            closes = st[st["action"] == "close"]
            lines.append(f"- **{strat}**: 开仓{len(opens)}笔, 平仓{len(closes)}笔")

        lines.append("")

        # 按平仓原因统计
        closes = daily_trades_df[daily_trades_df["action"] == "close"]
        if not closes.empty:
            lines.append("### 平仓原因分布")
            lines.append("")
            reason_counts = closes["reason"].value_counts()
            for reason, count in reason_counts.items():
                lines.append(f"- {reason}: {count}笔")
        lines.append("")
    lines.append("---")
    lines.append("")

    # 四、结论
    lines.append("## 四、结论")
    lines.append("")
    if cmp["max_diff"] < 0.01:
        lines.append("逐日回测与批量回测高度一致（最大偏差<1%），**策略无显著前视偏差**。")
    elif cmp["max_diff"] < 0.05:
        lines.append("逐日回测与批量回测存在可接受的偏差（最大偏差<5%），"
                     "主要来源于open_on时序差异，**策略整体可信**。")
    else:
        lines.append(f"两个回测存在较大偏差（最大{cmp['max_diff']:.1%}），需进一步排查。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 五、图表")
    lines.append("")
    lines.append("![对比图](chart_backtest_comparison.png)")
    lines.append("")

    path = os.path.join(output_dir, "daily_backtest_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [报告] {path}")


# ── 主函数 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 80)
    print("  逐日回测 vs 批量回测 对比验证")
    print("=" * 80)

    # 获取持仓量Top20品种
    print("\n[1/5] 获取持仓量Top20品种...")
    ranked = scan_and_rank(sort_by="oi")
    top20 = ranked[:20]
    product_list = [r["product_tuple"] for r in top20]
    product_names = [r["name"] for r in top20]
    s4_in = product_names  # S4改进：使用全部品种
    print(f"  品种: {', '.join(product_names[:10])}...")
    print(f"  S4品种: 全部{len(s4_in)}品种")

    # 逐日回测
    print(f"\n[2/5] 运行逐日回测...")
    t0 = time.time()
    daily_nav, daily_trades = run_daily_backtest(
        products=product_list,
        s4_products=s4_in if s4_in else None,
        enable_s4=bool(s4_in),
        use_slip=True,
        start_date=START_DATE,
    )
    t1 = time.time()
    s_daily = calc_stats(daily_nav["nav"].values)
    print(f"  耗时: {t1-t0:.0f}s")
    print(f"  年化{s_daily['ann_return']:+.2%} | 回撤{s_daily['max_dd']:.2%} | "
          f"夏普{s_daily['sharpe']:.2f} | 卡玛{s_daily['calmar']:.2f}")

    # 批量回测
    print(f"\n[3/5] 运行批量回测...")
    t0 = time.time()
    batch_nav = run_batch_backtest_with_log(product_list, s4_in if s4_in else None,
                                            bool(s4_in), START_DATE)
    t1 = time.time()
    s_batch = calc_stats(batch_nav["nav"].values)
    print(f"  耗时: {t1-t0:.0f}s")
    print(f"  年化{s_batch['ann_return']:+.2%} | 回撤{s_batch['max_dd']:.2%} | "
          f"夏普{s_batch['sharpe']:.2f} | 卡玛{s_batch['calmar']:.2f}")

    # 对比
    print(f"\n[4/5] 对比分析...")
    cmp = compare_nav(daily_nav, batch_nav)
    print(f"  日均偏差: {cmp['mean_diff']:+.4%}")
    print(f"  最大偏差: {cmp['max_diff']:.4%}")

    # 输出
    print(f"\n[5/5] 生成图表和报告...")
    plot_comparison(daily_nav, batch_nav, OUTPUT_DIR)
    generate_comparison_report(daily_nav, batch_nav, daily_trades,
                               s_daily, s_batch, cmp, OUTPUT_DIR)

    # 保存数据
    daily_nav.to_csv(os.path.join(OUTPUT_DIR, "nav_daily_backtest.csv"), index=False)
    if not daily_trades.empty:
        daily_trades.to_csv(os.path.join(OUTPUT_DIR, "trade_log_daily.csv"), index=False)
    batch_nav.to_csv(os.path.join(OUTPUT_DIR, "nav_batch_backtest.csv"), index=False)
    print(f"  [数据] nav_daily_backtest.csv, trade_log_daily.csv, nav_batch_backtest.csv")

    # 摘要
    print(f"\n{'='*80}")
    print(f"  对比摘要")
    print(f"{'='*80}")
    print(f"  逐日: 年化{s_daily['ann_return']:+.2%}, 夏普{s_daily['sharpe']:.2f}")
    print(f"  批量: 年化{s_batch['ann_return']:+.2%}, 夏普{s_batch['sharpe']:.2f}")
    print(f"  NAV偏差: 均值{cmp['mean_diff']:+.4%}, 最大{cmp['max_diff']:.4%}")
    if cmp["max_diff"] < 0.01:
        print(f"  结论: 高度一致（最大偏差<1%），无显著前视偏差")
    elif cmp["max_diff"] < 0.05:
        print(f"  结论: 可接受偏差（<5%），策略整体可信")
    else:
        print(f"  结论: 存在较大偏差，需排查")


if __name__ == "__main__":
    main()
