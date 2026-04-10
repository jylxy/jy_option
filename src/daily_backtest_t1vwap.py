"""
逐日回测引擎（T+1 VWAP执行版）

与 daily_backtest.py 完全一致的策略逻辑，唯一区别：
- T日收盘后生成信号（选合约、判断止盈、检查IV等）
- 信号放入pending队列
- T+1日执行：用T+1日VWAP=(H+L+C)/3作为执行价格创建/移除Position
- 如果T+1日该合约无成交数据，用T日收盘价作为fallback

这是最接近真实交易的回测方式。
"""
import sys
import os
import time

# 服务器部署版：所有模块在同一目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import numpy as np
import pandas as pd
from collections import defaultdict

from strategy_rules import (
    select_s1_sell, select_s1_protect,
    select_s3_buy, select_s3_sell, select_s3_protect,
    select_s4,
    calc_s1_size, calc_s3_size, calc_s4_size,
    extract_atm_iv_series, calc_iv_percentile_batch, get_iv_scale,
    check_margin_ok, calc_stats,
    should_take_profit_s1, should_take_profit_s3,
    should_close_expiry, can_reopen, should_pause_open,
    load_t1_price_index, get_t1_execution_price,
    DEFAULT_PARAMS,
)
from backtest_fast import load_product_data, estimate_margin
from exp_product_count import scan_and_rank

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
START_DATE = "2024-01-01"


class Position:
    __slots__ = [
        "strat", "product", "code", "opt_type", "strike",
        "open_price", "n", "open_date", "mult", "expiry", "mr", "liq", "role",
        "prev_price", "cur_price", "cur_spot",
    ]
    def __init__(self, strat, product, code, ot, strike, op, n, od,
                 mult, exp, mr, liq, role="sell", spot=0):
        self.strat = strat; self.product = product; self.code = code
        self.opt_type = ot; self.strike = strike; self.open_price = op
        self.n = n; self.open_date = od; self.mult = mult; self.expiry = exp
        self.mr = mr; self.liq = liq; self.role = role
        self.prev_price = op; self.cur_price = op; self.cur_spot = spot

    def daily_pnl(self):
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.prev_price) * self.mult * self.n
        return (self.prev_price - self.cur_price) * self.mult * self.n

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


def run_t1vwap_backtest(products=None, s4_products=None, enable_s4=True,
                        start_date=START_DATE, params=None):
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
    iv_open_threshold = p.get("iv_open_threshold", 80)
    s4_max_hold = p.get("s4_max_hold", 15)

    _s4_prods = s4_products or []

    # 加载数据
    print("加载数据...", end="", flush=True)
    conn = sqlite3.connect(DB_PATH)
    pdata = {}
    product_list = products or []
    for where, name, mult, mr, liq in product_list:
        df = load_product_data(conn, where)
        if df.empty or len(df) < 100: continue
        pdata[name] = {
            "df": df, "mult": mult, "mr": mr, "liq": liq,
            "dg": {d: g for d, g in df.groupby("trade_date")},
            "idx": df.set_index(["trade_date", "option_code"]),
        }
    conn.close()
    print(f" {len(pdata)}品种")

    # T+1价格索引
    print("加载T+1价格索引...", end="", flush=True)
    t1_idx = load_t1_price_index(DB_PATH)
    print(f" {len(t1_idx)}行")

    iv_pcts = {}
    for name, d in pdata.items():
        s = extract_atm_iv_series(d["df"])
        if not s.empty:
            iv_pcts[name] = calc_iv_percentile_batch(s)

    all_dates = sorted(set().union(*(d["dg"].keys() for d in pdata.values())))
    if start_date:
        all_dates = [d for d in all_dates if d >= pd.Timestamp(start_date)]

    date_to_next = {}
    for i in range(len(all_dates) - 1):
        date_to_next[all_dates[i]] = all_dates[i + 1]

    open_on = defaultdict(list)
    for name, d in pdata.items():
        for exp in d["df"]["expiry_date"].unique():
            ed = d["df"][(d["df"]["expiry_date"] == exp) &
                         (d["df"]["dte"] >= 15) & (d["df"]["dte"] <= 90)]
            if ed.empty: continue
            dte_d = ed.groupby("trade_date")["dte"].first()
            open_on[(dte_d - 35).abs().idxmin()].append((name, exp))

    positions = []
    records = []
    # Pending队列
    pending_opens = []    # list of template dicts
    pending_closes = set()  # set of position ids to close

    def _get_iv(product_name, date):
        if product_name not in iv_pcts: return np.nan
        ps = iv_pcts[product_name]
        return ps.loc[date] if date in ps.index else np.nan

    print(f"逐日回测(T+1 VWAP) {len(all_dates)} 天...", flush=True)
    for di, date in enumerate(all_dates):
        if di % 100 == 0:
            print(f"  [{di}/{len(all_dates)}] {date.date()}", flush=True)

        nav_now = CAPITAL + (records[-1]["cum_pnl"] if records else 0)

        # ===== Phase 1: 执行昨天的pending =====
        # 平仓
        if pending_closes:
            positions = [p for p in positions if id(p) not in pending_closes]
            pending_closes.clear()

        # 开仓（用今天的VWAP价格）
        for tmpl in pending_opens:
            t1_price = get_t1_execution_price(t1_idx, date, tmpl["code"], tmpl["fallback"])
            pos = Position(
                tmpl["strat"], tmpl["product"], tmpl["code"], tmpl["ot"],
                tmpl["strike"], t1_price, tmpl["n"], date,
                tmpl["mult"], tmpl["expiry"], tmpl["mr"], tmpl["liq"],
                tmpl["role"], tmpl.get("spot", 0))
            positions.append(pos)
        pending_opens.clear()

        # ===== Phase 2: MTM =====
        day_pnl = pnl_s1 = pnl_s3 = pnl_s4 = 0.0
        for pos in positions:
            k = (date, pos.code)
            pos.prev_price = pos.cur_price
            if k in pdata[pos.product]["idx"].index:
                row = pdata[pos.product]["idx"].loc[k]
                if isinstance(row, pd.DataFrame): row = row.iloc[0]
                pos.cur_price = row["option_close"]
                pos.cur_spot = row["spot_close"]
            elif date >= pos.expiry:
                pos.cur_price = 0.0
            dp = pos.daily_pnl()
            day_pnl += dp
            if pos.strat == "S1": pnl_s1 += dp
            elif pos.strat == "S3": pnl_s3 += dp
            elif pos.strat == "S4": pnl_s4 += dp

        # ===== Phase 3: 生成信号 → pending队列 =====
        closed_ids = set()

        for pos in positions:
            # S4持仓天数限制
            if pos.strat == "S4" and pos.role == "buy" and s4_max_hold > 0:
                if (date - pos.open_date).days >= s4_max_hold:
                    closed_ids.add(id(pos))
                    continue

            if pos.role in ("buy", "protect"):
                d = pdata.get(pos.product)
                if d is None: continue
                dg = d["dg"].get(date)
                if dg is None:
                    if date >= pos.expiry: closed_ids.add(id(pos))
                    continue
                er = dg[dg["expiry_date"] == pos.expiry]
                if er.empty:
                    if date >= pos.expiry: closed_ids.add(id(pos))
                    continue
                if er["dte"].iloc[0] <= 1:
                    closed_ids.add(id(pos))
                continue

            # 卖腿
            d = pdata.get(pos.product)
            if d is None: continue
            dg = d["dg"].get(date)
            if dg is None:
                if date >= pos.expiry:
                    closed_ids.add(id(pos))
                    for pp in positions:
                        if (pp.product == pos.product and pp.expiry == pos.expiry and
                                pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                                id(pp) not in closed_ids and pp.role != "sell"):
                            closed_ids.add(id(pp))
                continue
            er = dg[dg["expiry_date"] == pos.expiry]
            if er.empty:
                if date >= pos.expiry: closed_ids.add(id(pos))
                continue
            dte = er["dte"].iloc[0]

            # S1止盈
            if pos.strat == "S1" and should_take_profit_s1(pos.profit_pct(), dte, s1_tp):
                closed_ids.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed_ids and pp.role != "sell"):
                        closed_ids.add(id(pp))
                # 重开
                if can_reopen(dte):
                    iv_pct = _get_iv(pos.product, date)
                    if not should_pause_open(iv_pct, iv_open_threshold):
                        c = select_s1_sell(dg, pos.opt_type, d["mult"], d["mr"])
                        if c is not None and c["option_code"] != pos.code:
                            sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                            m = estimate_margin(c["spot_close"], c["strike"], c["option_type"],
                                                c["option_close"], d["mult"], d["mr"], 0.5)
                            nn = calc_s1_size(nav_now, margin_per, m, sc)
                            pending_opens.append({
                                "strat": "S1", "product": pos.product, "code": c["option_code"],
                                "ot": c["option_type"], "strike": c["strike"],
                                "fallback": c["option_close"], "n": nn,
                                "mult": d["mult"], "expiry": pos.expiry,
                                "mr": d["mr"], "liq": d["liq"], "role": "sell",
                                "spot": c["spot_close"],
                            })
                            pr = select_s1_protect(dg, c)
                            if pr is not None and pr["option_code"] != c["option_code"]:
                                pending_opens.append({
                                    "strat": "S1", "product": pos.product, "code": pr["option_code"],
                                    "ot": pr["option_type"], "strike": pr["strike"],
                                    "fallback": pr["option_close"], "n": max(1, nn // 2),
                                    "mult": d["mult"], "expiry": pos.expiry,
                                    "mr": d["mr"], "liq": d["liq"], "role": "buy",
                                    "spot": pr["spot_close"],
                                })
                continue

            # S3止盈
            if pos.strat == "S3" and should_take_profit_s3(pos.profit_pct(), dte, s3_tp):
                closed_ids.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed_ids and pp.role != "sell"):
                        closed_ids.add(id(pp))
                # S3重开
                if can_reopen(dte):
                    iv_pct = _get_iv(pos.product, date)
                    if not should_pause_open(iv_pct, iv_open_threshold):
                        bl = select_s3_buy(dg, pos.opt_type)
                        if bl is not None:
                            sl = select_s3_sell(dg, pos.opt_type, bl["strike"])
                            if sl is not None and sl["option_code"] != bl["option_code"]:
                                sm = estimate_margin(sl["spot_close"], sl["strike"], pos.opt_type,
                                                     sl["option_close"], d["mult"], d["mr"], 0.5)
                                sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                                bq, sq = calc_s3_size(nav_now, margin_per, sm, s3_ratio, sc)
                                _add_s3_templates(pending_opens, pos.product, pos.opt_type,
                                                  bl, sl, dg, d, pos.expiry, bq, sq)
                continue

            # 到期
            if should_close_expiry(dte):
                closed_ids.add(id(pos))
                for pp in positions:
                    if (pp.product == pos.product and pp.expiry == pos.expiry and
                            pp.opt_type == pos.opt_type and pp.strat == pos.strat and
                            id(pp) not in closed_ids and pp.role != "sell"):
                        closed_ids.add(id(pp))

        # 放入pending
        pending_closes.update(closed_ids)

        # 新开仓信号
        if date in open_on:
            total_margin = sum(p.cur_margin() for p in positions if p.role == "sell")
            s1_margin = sum(p.cur_margin() for p in positions if p.role == "sell" and p.strat == "S1")
            s3_margin = sum(p.cur_margin() for p in positions if p.role == "sell" and p.strat == "S3")

            for pn, exp in open_on[date]:
                if pn not in pdata: continue
                d = pdata[pn]; dg = d["dg"].get(date)
                if dg is None: continue
                ef = dg[dg["expiry_date"] == exp]
                if ef.empty: continue

                iv_pct = _get_iv(pn, date)
                sc = get_iv_scale(iv_pct) if iv_inverse else 1.0
                iv_paused = should_pause_open(iv_pct, iv_open_threshold)

                # S3优先
                if not iv_paused and (not margin_cap or total_margin / nav_now < margin_cap):
                    for ot in ["P", "C"]:
                        if any(p.strat == "S3" and p.product == pn and p.opt_type == ot and p.role == "sell" for p in positions):
                            continue
                        if s3_margin_cap and s3_margin / nav_now > s3_margin_cap: break
                        if margin_cap and total_margin / nav_now > margin_cap: break
                        bl = select_s3_buy(ef, ot)
                        if bl is None: continue
                        sl = select_s3_sell(ef, ot, bl["strike"])
                        if sl is None or bl["option_code"] == sl["option_code"]: continue
                        sm = estimate_margin(sl["spot_close"], sl["strike"], ot,
                                             sl["option_close"], d["mult"], d["mr"], 0.5)
                        bq, sq = calc_s3_size(nav_now, margin_per, sm, s3_ratio, sc)
                        new_m = sm * sq
                        if s3_margin_cap and (s3_margin + new_m) / nav_now > s3_margin_cap: continue
                        if margin_cap and (total_margin + new_m) / nav_now > margin_cap: continue
                        _add_s3_templates(pending_opens, pn, ot, bl, sl, dg, d, exp, bq, sq)
                        total_margin += new_m; s3_margin += new_m

                # S1
                if not iv_paused and (not margin_cap or total_margin / nav_now < margin_cap):
                    for ot in ["P", "C"]:
                        if s1_margin_cap and s1_margin / nav_now > s1_margin_cap: break
                        if margin_cap and total_margin / nav_now > margin_cap: break
                        c = select_s1_sell(ef, ot, d["mult"], d["mr"])
                        if c is None: continue
                        m = estimate_margin(c["spot_close"], c["strike"], ot,
                                            c["option_close"], d["mult"], d["mr"], 0.5)
                        nn = calc_s1_size(nav_now, margin_per, m, sc)
                        if s1_margin_cap and (s1_margin + m * nn) / nav_now > s1_margin_cap: continue
                        if margin_cap and (total_margin + m * nn) / nav_now > margin_cap: continue
                        pending_opens.append({
                            "strat": "S1", "product": pn, "code": c["option_code"],
                            "ot": ot, "strike": c["strike"], "fallback": c["option_close"],
                            "n": nn, "mult": d["mult"], "expiry": exp,
                            "mr": d["mr"], "liq": d["liq"], "role": "sell", "spot": c["spot_close"],
                        })
                        total_margin += m * nn; s1_margin += m * nn
                        pr = select_s1_protect(ef, c)
                        if pr is not None and pr["option_code"] != c["option_code"]:
                            pending_opens.append({
                                "strat": "S1", "product": pn, "code": pr["option_code"],
                                "ot": ot, "strike": pr["strike"], "fallback": pr["option_close"],
                                "n": max(1, nn // 2), "mult": d["mult"], "expiry": exp,
                                "mr": d["mr"], "liq": d["liq"], "role": "buy", "spot": pr["spot_close"],
                            })

                # S4
                if enable_s4 and _s4_prods and pn in _s4_prods:
                    if not any(p.strat == "S4" and p.product == pn and p.expiry == exp for p in positions):
                        for ot in ["P", "C"]:
                            opt = select_s4(ef, ot)
                            if opt is None: continue
                            cost = opt["option_close"] * d["mult"]
                            qty = calc_s4_size(nav_now, s4_prem, len(_s4_prods), cost)
                            pending_opens.append({
                                "strat": "S4", "product": pn, "code": opt["option_code"],
                                "ot": ot, "strike": opt["strike"], "fallback": opt["option_close"],
                                "n": qty, "mult": d["mult"], "expiry": exp,
                                "mr": d["mr"], "liq": "deep_otm", "role": "buy",
                                "spot": opt["spot_close"],
                            })

        # NAV
        cum = (records[-1]["cum_pnl"] if records else 0) + day_pnl
        nav = CAPITAL + cum
        tm = sum(p.cur_margin() for p in positions if p.role == "sell")
        records.append({
            "date": str(date.date()), "daily_pnl": day_pnl, "cum_pnl": cum, "nav": nav,
            "pnl_s1": pnl_s1, "pnl_s3": pnl_s3, "pnl_s4": pnl_s4,
            "margin_pct": tm / nav if nav > 0 else 0,
        })

    return pd.DataFrame(records)


def _add_s3_templates(pending, product, ot, bl, sl, dg, d, exp, bq, sq):
    """生成S3三腿的开仓模板"""
    pending.append({
        "strat": "S3", "product": product, "code": bl["option_code"],
        "ot": ot, "strike": bl["strike"], "fallback": bl["option_close"],
        "n": bq, "mult": d["mult"], "expiry": exp,
        "mr": d["mr"], "liq": d["liq"], "role": "buy", "spot": bl["spot_close"],
    })
    pending.append({
        "strat": "S3", "product": product, "code": sl["option_code"],
        "ot": ot, "strike": sl["strike"], "fallback": sl["option_close"],
        "n": sq, "mult": d["mult"], "expiry": exp,
        "mr": d["mr"], "liq": d["liq"], "role": "sell", "spot": sl["spot_close"],
    })
    pt = select_s3_protect(dg[dg["expiry_date"] == exp] if not isinstance(dg, pd.DataFrame) else dg,
                           ot, sl["strike"], sl["spot_close"])
    if pt is not None and pt["option_code"] != sl["option_code"]:
        pending.append({
            "strat": "S3", "product": product, "code": pt["option_code"],
            "ot": ot, "strike": pt["strike"], "fallback": pt["option_close"],
            "n": sq, "mult": d["mult"], "expiry": exp,
            "mr": d["mr"], "liq": d["liq"], "role": "protect", "spot": pt["spot_close"],
        })


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 80)
    print("  逐日回测（T+1 VWAP执行版）")
    print("=" * 80)

    ranked = scan_and_rank(sort_by="oi")
    top20 = ranked[:20]
    product_list = [r["product_tuple"] for r in top20]
    product_names = [r["name"] for r in top20]

    print(f"\n品种({len(top20)}): {', '.join(product_names[:10])}...")

    t0 = time.time()
    nav_df = run_t1vwap_backtest(
        products=product_list,
        s4_products=product_names,
        enable_s4=True,
        start_date=START_DATE,
    )
    t1 = time.time()
    s = calc_stats(nav_df["nav"].values)
    print(f"\n耗时: {t1-t0:.0f}s")
    print(f"年化{s['ann_return']:+.2%} | 回撤{s['max_dd']:.2%} | "
          f"夏普{s['sharpe']:.2f} | 卡玛{s['calmar']:.2f}")

    nav_df.to_csv(os.path.join(OUTPUT_DIR, "nav_t1vwap_backtest.csv"), index=False)
    print(f"\n数据: {OUTPUT_DIR}/nav_t1vwap_backtest.csv")


if __name__ == "__main__":
    main()
