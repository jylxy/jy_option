"""
统一四策略回测引擎 v3

修复与原版S1的差异：
  1. 数据加载加pricing_status='usable'过滤
  2. 止盈后立即重开（同一天），重开用完整margin_per
  3. 双卖时每方向仓位=margin_per/2（仅新开仓）
  4. NAV=capital+累积daily MTM（与原版一致，不扣手续费）
  5. 不检查S1已有持仓，允许同品种不同到期日重叠
  6. 保证金上限只计卖腿（与原版一致）
  7. 所有开平仓可选滑点
  8. S3/S4策略可选开关
  9. 止盈重开前检查保证金上限（修复保证金超限bug）
  10. S2波动率套利策略
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import norm, percentileofscore
from collections import defaultdict
from backtest_fast import load_product_data, estimate_margin

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")
CAPITAL = 10_000_000
FEE = 14  # 开+平

SLIPPAGE = {"financial": 0.002, "commodity_high": 0.002,
            "commodity_low": 0.002, "deep_otm": 0.002}

RISK_FREE = 0.02

# ── BSM Greeks (inline for S2) ──────────────────────────────────────────────
def bsm_greeks(S, K, T, r, sigma, option_type="P"):
    if T <= 1e-6 or sigma <= 1e-6 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = S * norm.pdf(d1) * np.sqrt(T) / 100  # IV变动1%的价格变化
    if option_type == "C":
        delta = norm.cdf(d1)
    else:
        delta = norm.cdf(d1) - 1
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": 0.0}

def find_vega_neutral_ratio(buy_g, sell_g, target_vega_ratio=-0.3):
    bv = buy_g["vega"]; sv = sell_g["vega"]
    if sv < 1e-8: return None
    target = target_vega_ratio * bv
    ratio = (bv - target) / sv
    return max(1, int(round(ratio)))

PRODUCTS_15 = [
    ("underlying_code = 'HS300'", "沪深300", 100, 0.10, "financial"),
    ("underlying_code = 'CSI1000'", "中证1000", 100, 0.10, "financial"),
    ("underlying_code = 'SSE50'", "上证50", 100, 0.10, "financial"),
    ("underlying_code LIKE 'au%'", "沪金", 1000, 0.05, "commodity_high"),
    ("underlying_code LIKE 'cu%'", "沪铜", 5, 0.05, "commodity_high"),
    ("underlying_code LIKE 'sc%'", "原油", 1000, 0.07, "commodity_high"),
    ("underlying_code LIKE 'ag%'", "沪银", 15, 0.05, "commodity_high"),
    ("underlying_code LIKE 'al%'", "沪铝", 5, 0.05, "commodity_low"),
    ("underlying_code LIKE 'i%'", "铁矿石", 100, 0.05, "commodity_high"),
    ("underlying_code LIKE 'rb%'", "螺纹钢", 10, 0.05, "commodity_low"),
    ("underlying_code LIKE 'm2%'", "豆粕", 10, 0.05, "commodity_low"),
    ("underlying_code LIKE 'TA%'", "PTA", 5, 0.05, "commodity_low"),
    ("underlying_code LIKE 'SA%'", "纯碱", 20, 0.05, "commodity_low"),
    ("underlying_code LIKE 'SR%'", "白糖", 10, 0.05, "commodity_low"),
    ("underlying_code LIKE 'CF%'", "棉花", 5, 0.05, "commodity_low"),
]
S4_PRODUCTS = ["中证1000", "沪金", "原油", "沪铜"]


def slip(price, direction, liq):
    s = SLIPPAGE.get(liq, 0.01)
    return price * (1+s) if direction == "buy" else price * (1-s)


class Pos:
    __slots__ = ["strat","product","code","opt_type","strike","open_price","n",
                 "open_date","mult","expiry","mr","liq","role",
                 "prev_price","cur_price","cur_spot"]
    def __init__(s, strat, product, code, ot, strike, op, n, od, mult, exp, mr, liq, role="sell", spot=0):
        s.strat=strat; s.product=product; s.code=code; s.opt_type=ot
        s.strike=strike; s.open_price=op; s.n=n; s.open_date=od
        s.mult=mult; s.expiry=exp; s.mr=mr; s.liq=liq; s.role=role
        s.prev_price=op; s.cur_price=op; s.cur_spot=spot
    def daily_pnl(s):
        if s.role in ("buy","protect"):
            return (s.cur_price - s.prev_price) * s.mult * s.n
        return (s.prev_price - s.cur_price) * s.mult * s.n
    def total_pnl(s):
        if s.role in ("buy","protect"):
            return (s.cur_price - s.open_price) * s.mult * s.n
        return (s.open_price - s.cur_price) * s.mult * s.n
    def profit_pct(s):
        return (s.open_price - s.cur_price)/s.open_price if s.role=="sell" and s.open_price>0 else 0
    def cur_margin(s):
        if s.role=="sell":
            return estimate_margin(s.cur_spot or s.strike, s.strike, s.opt_type,
                                   s.cur_price, s.mult, s.mr, 0.5) * s.n
        return 0


# 合约选择函数（与原版一致）
def sel_s1_sell(day_df, ot, mult, mr):
    if ot == "P":
        c = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&
                   (day_df["delta"]<0)&(day_df["delta"].abs()<0.15)&(day_df["option_close"]>=0.5)]
    else:
        c = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&
                   (day_df["delta"]>0)&(day_df["delta"]<0.15)&(day_df["option_close"]>=0.5)]
    if c.empty: return None
    c = c.copy()
    c["margin"] = c.apply(lambda r: estimate_margin(r["spot_close"],r["strike"],ot,r["option_close"],mult,mr,0.5), axis=1)
    c["eff"] = c["option_close"]*mult/c["margin"]
    return c.loc[c["eff"].idxmax()]

def sel_s1_protect(day_df, sell_row):
    ot = sell_row["option_type"]
    if ot == "P":
        p = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&
                   (day_df["delta"]<0)&(day_df["delta"].abs()<0.25)&
                   (day_df["option_close"]>=0.5)&(day_df["strike"]>sell_row["strike"])]
        if p.empty: return None
        return p.loc[p["delta"].abs().idxmax()]
    else:
        p = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&
                   (day_df["delta"]>0)&(day_df["delta"]<0.25)&
                   (day_df["option_close"]>=0.5)&(day_df["strike"]<sell_row["strike"])]
        if p.empty: return None
        return p.loc[p["delta"].idxmax()]

def sel_s3_buy(day_df, ot):
    if ot == "P":
        c = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&
                   (day_df["delta"]<0)&(day_df["delta"].abs()>=0.10)&
                   (day_df["delta"].abs()<=0.20)&(day_df["option_close"]>=0.5)]
    else:
        c = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&
                   (day_df["delta"]>0)&(day_df["delta"]>=0.10)&
                   (day_df["delta"]<=0.20)&(day_df["option_close"]>=0.5)]
    if c.empty: return None
    c = c.copy(); c["dd"] = (c["delta"].abs()-0.15).abs()
    return c.loc[c["dd"].idxmin()]

def sel_s3_sell(day_df, ot, buy_k):
    if ot == "P":
        c = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&
                   (day_df["delta"]<0)&(day_df["delta"].abs()>=0.05)&
                   (day_df["delta"].abs()<=0.15)&(day_df["option_close"]>=0.5)&(day_df["strike"]<buy_k)]
    else:
        c = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&
                   (day_df["delta"]>0)&(day_df["delta"]>=0.05)&
                   (day_df["delta"]<=0.15)&(day_df["option_close"]>=0.5)&(day_df["strike"]>buy_k)]
    if c.empty: return None
    return c.loc[c["option_close"].idxmax()]

def sel_s3_protect(day_df, ot, sell_k, spot):
    if ot == "P":
        c = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&
                   (day_df["option_close"]>=0.1)&(day_df["strike"]<sell_k)]
        if c.empty: return None
        tgt = sell_k - (spot-sell_k)*0.5
    else:
        c = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&
                   (day_df["option_close"]>=0.1)&(day_df["strike"]>sell_k)]
        if c.empty: return None
        tgt = sell_k + (sell_k-spot)*0.5
    c = c.copy(); c["d"] = (c["strike"]-tgt).abs()
    return c.loc[c["d"].idxmin()]

def sel_s4(day_df, ot):
    if ot == "P":
        c = day_df[(day_df["option_type"]=="P")&(day_df["moneyness"]<1.0)&(day_df["option_close"]>=0.1)]
        return c.loc[c["moneyness"].idxmin()] if not c.empty else None
    else:
        c = day_df[(day_df["option_type"]=="C")&(day_df["moneyness"]>1.0)&(day_df["option_close"]>=0.1)]
        return c.loc[c["moneyness"].idxmax()] if not c.empty else None



def run_unified_v3(margin_per=0.02, s1_tp=0.40, s3_tp=0.30, iv_inverse=True,
                   margin_cap=0.50, s1_margin_cap=0.25, s3_margin_cap=0.25,
                   s3_ratio=3, s4_prem=0.005,
                   enable_s1=True, enable_s2=False, enable_s3=True, enable_s4=True,
                   s2_iv_entry=90, s2_iv_exit=50, s2_max_hold=5, s2_prem_pct=0.03,
                   s2_max_products=3, use_slip=True, start_date=None,
                   products=None, s4_products=None):
    print("加载数据...", end="", flush=True)
    conn = sqlite3.connect(DB_PATH)
    pdata = {}
    product_list = products if products is not None else PRODUCTS_15
    for where, name, mult, mr, liq in product_list:
        df = load_product_data(conn, where)  # 差异1修复：用原版数据加载
        if df.empty or len(df)<100: continue
        pdata[name] = {"df":df,"mult":mult,"mr":mr,"liq":liq,
                       "dg":{d:g for d,g in df.groupby("trade_date")},
                       "idx":df.set_index(["trade_date","option_code"])}
    conn.close()
    print(f" {len(pdata)}品种")

    # IV分位数（S1 iv_inverse + S2都需要）
    iv_pcts = {}
    for name, d in pdata.items():
        df = d["df"]
        atm = df[(df["moneyness"].between(0.95,1.05))&(df["dte"].between(15,90))&(df["implied_vol"]>0)]
        if not atm.empty:
            s = atm.groupby("trade_date")["implied_vol"].mean()
            iv_pcts[name] = s.rolling(252,min_periods=60).apply(
                lambda x: percentileofscore(x, x.iloc[-1], kind='rank'))

    all_dates = sorted(set().union(*(d["dg"].keys() for d in pdata.values())))
    if start_date:
        start_dt = pd.Timestamp(start_date)
        all_dates = [d for d in all_dates if d >= start_dt]
    open_on = defaultdict(list)
    for name, d in pdata.items():
        for exp in d["df"]["expiry_date"].unique():
            ed = d["df"][(d["df"]["expiry_date"]==exp)&(d["df"]["dte"]>=15)&(d["df"]["dte"]<=90)]
            if ed.empty: continue
            dte_d = ed.groupby("trade_date")["dte"].first()
            open_on[(dte_d-35).abs().idxmin()].append((name, exp))

    positions = []
    trades = []  # 交易记录（仅记录，不影响NAV）
    records = []

    for date in all_dates:
        # 当前NAV（用于仓位计算，替代固定CAPITAL）
        nav_now = CAPITAL + (records[-1]["cum_pnl"] if records else 0)

        # === MTM更新 ===
        day_pnl = 0.0
        pnl_s1 = 0.0; pnl_s2 = 0.0; pnl_s3 = 0.0; pnl_s4 = 0.0
        for pos in positions:
            k = (date, pos.code)
            pos.prev_price = pos.cur_price
            if k in pdata[pos.product]["idx"].index:
                row = pdata[pos.product]["idx"].loc[k]
                if isinstance(row, pd.DataFrame): row = row.iloc[0]
                pos.cur_price = row["option_close"]
                pos.cur_spot = row["spot_close"]
            elif date >= pos.expiry:
                # 到期日无数据：期权价格归零（到期作废或已交割）
                pos.cur_price = 0.0
            # else: 非到期日无数据，保持上一次价格不变
            dp = pos.daily_pnl()
            day_pnl += dp
            if pos.strat=="S1": pnl_s1 += dp
            elif pos.strat=="S2": pnl_s2 += dp
            elif pos.strat=="S3": pnl_s3 += dp
            elif pos.strat=="S4": pnl_s4 += dp

        # === 止盈+到期 ===
        closed = set()
        new_re = []  # 止盈重开的新仓位

        for pos in positions:
            if pos.role in ("buy","protect"):
                d = pdata.get(pos.product)
                if d is None: continue
                dg = d["dg"].get(date)
                if dg is None:
                    if date >= pos.expiry:
                        trades.append(pos.total_pnl() - FEE*pos.n)
                        closed.add(id(pos))
                    continue
                er = dg[dg["expiry_date"]==pos.expiry]
                if er.empty:
                    if date >= pos.expiry:
                        trades.append(pos.total_pnl() - FEE*pos.n)
                        closed.add(id(pos))
                    continue
                if er["dte"].iloc[0] <= 1:
                    trades.append(pos.total_pnl() - FEE*pos.n)
                    closed.add(id(pos))
                continue

            # 卖腿
            d = pdata.get(pos.product)
            if d is None: continue
            dg = d["dg"].get(date)
            if dg is None:
                if date >= pos.expiry:
                    trades.append(pos.total_pnl() - FEE*pos.n)
                    closed.add(id(pos))
                    for pp in positions:
                        if pp.product==pos.product and pp.expiry==pos.expiry and pp.opt_type==pos.opt_type and pp.strat==pos.strat and id(pp) not in closed and pp.role!="sell":
                            trades.append(pp.total_pnl() - FEE*pp.n)
                            closed.add(id(pp))
                continue
            er = dg[dg["expiry_date"]==pos.expiry]
            if er.empty:
                if date >= pos.expiry:
                    trades.append(pos.total_pnl() - FEE*pos.n)
                    closed.add(id(pos))
                continue
            dte = er["dte"].iloc[0]

            # 止盈
            tp = s1_tp if pos.strat=="S1" else s3_tp if pos.strat=="S3" else None
            if tp and pos.open_price>0 and dte>5 and pos.profit_pct()>=tp:
                trades.append(pos.total_pnl() - FEE*pos.n)
                closed.add(id(pos))
                for pp in positions:
                    if pp.product==pos.product and pp.expiry==pos.expiry and pp.opt_type==pos.opt_type and pp.strat==pos.strat and id(pp) not in closed and pp.role!="sell":
                        trades.append(pp.total_pnl() - FEE*pp.n)
                        closed.add(id(pp))
                # 差异2修复：止盈后立即重开
                if dte > 10 and pos.strat == "S1":
                    c = sel_s1_sell(dg, pos.opt_type, d["mult"], d["mr"])
                    if c is not None and c["option_code"] != pos.code:
                        sc = 1.0
                        if iv_inverse and pos.product in iv_pcts and date in iv_pcts[pos.product].index:
                            v = iv_pcts[pos.product].loc[date]
                            if pd.notna(v) and v > 75: sc = 0.5
                        m = estimate_margin(c["spot_close"],c["strike"],c["option_type"],c["option_close"],d["mult"],d["mr"],0.5)
                        nn = max(1, int(margin_per/2*sc*nav_now/m)) if m>0 else 1  # /2: 双卖每方向分一半
                        # 检查S1独立上限和组合总上限
                        cur_total_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and id(p) not in closed)
                        cur_total_margin += sum(p.cur_margin() for p in new_re if p.role=="sell")
                        cur_s1_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S1" and id(p) not in closed)
                        cur_s1_margin += sum(p.cur_margin() for p in new_re if p.role=="sell" and p.strat=="S1")
                        new_m = m * nn
                        if (margin_cap and (cur_total_margin + new_m) / nav_now > margin_cap) or \
                           (s1_margin_cap and (cur_s1_margin + new_m) / nav_now > s1_margin_cap):
                            pass  # 加上新仓位后超限，不重开
                        else:
                            op = slip(c["option_close"],"sell",d["liq"]) if use_slip else c["option_close"]
                            new_re.append(Pos("S1",pos.product,c["option_code"],c["option_type"],c["strike"],op,nn,date,d["mult"],pos.expiry,d["mr"],d["liq"],"sell",c["spot_close"]))
                            pr = sel_s1_protect(dg, c)
                            if pr is not None and pr["option_code"]!=c["option_code"]:
                                pn_q = max(1, nn//2)
                                bop = slip(pr["option_close"],"buy",d["liq"]) if use_slip else pr["option_close"]
                                new_re.append(Pos("S1",pos.product,pr["option_code"],pr["option_type"],pr["strike"],bop,pn_q,date,d["mult"],pos.expiry,d["mr"],d["liq"],"buy",pr["spot_close"]))
                # S3止盈重开
                if dte > 10 and pos.strat == "S3":
                    bl = sel_s3_buy(dg, pos.opt_type)
                    if bl is not None:
                        sl = sel_s3_sell(dg, pos.opt_type, bl["strike"])
                        if sl is not None and sl["option_code"]!=bl["option_code"]:
                            sm = estimate_margin(sl["spot_close"],sl["strike"],pos.opt_type,sl["option_close"],d["mult"],d["mr"],0.5)
                            sc = 1.0
                            if iv_inverse and pos.product in iv_pcts and date in iv_pcts[pos.product].index:
                                v = iv_pcts[pos.product].loc[date]
                                if pd.notna(v) and v > 75: sc = 0.5
                            bq = max(1, int((margin_per/2)*sc*nav_now/(sm*s3_ratio)))
                            sq = bq * s3_ratio
                            new_m = sm * sq
                            # 检查S3独立上限和组合总上限
                            cur_total_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and id(p) not in closed)
                            cur_total_margin += sum(p.cur_margin() for p in new_re if p.role=="sell")
                            cur_s3_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S3" and id(p) not in closed)
                            cur_s3_margin += sum(p.cur_margin() for p in new_re if p.role=="sell" and p.strat=="S3")
                            if (margin_cap and (cur_total_margin + new_m) / nav_now > margin_cap) or \
                               (s3_margin_cap and (cur_s3_margin + new_m) / nav_now > s3_margin_cap):
                                pass  # 加上新仓位后超限，不重开
                            else:
                                new_re.append(Pos("S3",pos.product,bl["option_code"],pos.opt_type,bl["strike"],
                                    slip(bl["option_close"],"buy",d["liq"]) if use_slip else bl["option_close"],
                                    bq,date,d["mult"],pos.expiry,d["mr"],d["liq"],"buy",bl["spot_close"]))
                                new_re.append(Pos("S3",pos.product,sl["option_code"],pos.opt_type,sl["strike"],
                                    slip(sl["option_close"],"sell",d["liq"]) if use_slip else sl["option_close"],
                                    sq,date,d["mult"],pos.expiry,d["mr"],d["liq"],"sell",sl["spot_close"]))
                                pt = sel_s3_protect(dg, pos.opt_type, sl["strike"], sl["spot_close"])
                                if pt is not None and pt["option_code"]!=sl["option_code"]:
                                    new_re.append(Pos("S3",pos.product,pt["option_code"],pos.opt_type,pt["strike"],
                                        slip(pt["option_close"],"buy",d["liq"]) if use_slip else pt["option_close"],
                                        sq,date,d["mult"],pos.expiry,d["mr"],d["liq"],"protect",pt["spot_close"]))
                continue

            if dte <= 1:
                trades.append(pos.total_pnl() - FEE*pos.n)
                closed.add(id(pos))
                for pp in positions:
                    if pp.product==pos.product and pp.expiry==pos.expiry and pp.opt_type==pos.opt_type and pp.strat==pos.strat and id(pp) not in closed and pp.role!="sell":
                        trades.append(pp.total_pnl() - FEE*pp.n)
                        closed.add(id(pp))

        # === S2平仓检查 ===
        if enable_s2:
            for pos in positions:
                if pos.strat != "S2" or id(pos) in closed:
                    continue
                if pos.role != "buy":
                    continue  # S2只检查买腿，卖腿跟随平仓
                hold_days = (date - pos.open_date).days
                should_close = False
                # IV回落
                if pos.product in iv_pcts:
                    ps = iv_pcts[pos.product]
                    if date in ps.index and pd.notna(ps.loc[date]):
                        if ps.loc[date] <= s2_iv_exit:
                            should_close = True
                # 超时
                if hold_days >= s2_max_hold:
                    should_close = True
                # DTE <= 2
                d = pdata.get(pos.product)
                if d:
                    dg = d["dg"].get(date)
                    if dg is not None:
                        er = dg[dg["expiry_date"]==pos.expiry]
                        if not er.empty and er["dte"].iloc[0] <= 2:
                            should_close = True
                    elif date >= pos.expiry:
                        should_close = True
                if should_close:
                    trades.append(pos.total_pnl() - FEE*pos.n)
                    closed.add(id(pos))
                    # 平掉同组S2卖腿
                    for pp in positions:
                        if pp.strat=="S2" and pp.product==pos.product and pp.expiry==pos.expiry and id(pp) not in closed and pp.role=="sell":
                            trades.append(pp.total_pnl() - FEE*pp.n)
                            closed.add(id(pp))

        if closed: positions = [p for p in positions if id(p) not in closed]
        positions.extend(new_re)

        # === 新开仓 ===
        if date in open_on:
            total_margin = sum(p.cur_margin() for p in positions if p.role=="sell") if margin_cap else 0
            s1_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S1")
            s3_margin = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S3")
            for pn, exp in open_on[date]:
                if pn not in pdata: continue
                d = pdata[pn]; dg = d["dg"].get(date)
                if dg is None: continue
                ef = dg[dg["expiry_date"]==exp]
                if ef.empty: continue

                sc = 1.0
                if iv_inverse and pn in iv_pcts and date in iv_pcts[pn].index:
                    v = iv_pcts[pn].loc[date]
                    if pd.notna(v) and v > 75: sc = 0.5

                # S3优先 — 受S3独立上限和组合总上限双重约束
                if enable_s3 and (not margin_cap or total_margin/nav_now < margin_cap):
                    for ot in ["P","C"]:
                        if any(p.strat=="S3" and p.product==pn and p.opt_type==ot and p.role=="sell" for p in positions): continue
                        if s3_margin_cap and s3_margin/nav_now > s3_margin_cap: break
                        if margin_cap and total_margin/nav_now > margin_cap: break
                        bl = sel_s3_buy(ef, ot)
                        if bl is None: continue
                        sl = sel_s3_sell(ef, ot, bl["strike"])
                        if sl is None: continue
                        if bl["option_code"]==sl["option_code"]: continue
                        sm = estimate_margin(sl["spot_close"],sl["strike"],ot,sl["option_close"],d["mult"],d["mr"],0.5)
                        pl = margin_per / 2
                        bq = max(1, int(pl*sc*nav_now/(sm*s3_ratio)))
                        sq = bq * s3_ratio
                        new_m = sm * sq
                        if s3_margin_cap and (s3_margin+new_m)/nav_now > s3_margin_cap: continue
                        if margin_cap and (total_margin+new_m)/nav_now > margin_cap: continue
                        positions.append(Pos("S3",pn,bl["option_code"],ot,bl["strike"],
                            slip(bl["option_close"],"buy",d["liq"]) if use_slip else bl["option_close"],
                            bq,date,d["mult"],exp,d["mr"],d["liq"],"buy",bl["spot_close"]))
                        positions.append(Pos("S3",pn,sl["option_code"],ot,sl["strike"],
                            slip(sl["option_close"],"sell",d["liq"]) if use_slip else sl["option_close"],
                            sq,date,d["mult"],exp,d["mr"],d["liq"],"sell",sl["spot_close"]))
                        total_margin += new_m
                        s3_margin += new_m
                        pt = sel_s3_protect(ef, ot, sl["strike"], sl["spot_close"])
                        if pt is not None and pt["option_code"]!=sl["option_code"]:
                            positions.append(Pos("S3",pn,pt["option_code"],ot,pt["strike"],
                                slip(pt["option_close"],"buy",d["liq"]) if use_slip else pt["option_close"],
                                sq,date,d["mult"],exp,d["mr"],d["liq"],"protect",pt["spot_close"]))

                # S1 — 受S1独立上限和组合总上限双重约束
                if enable_s1 and (not margin_cap or total_margin/nav_now < margin_cap):
                    for ot in ["P","C"]:
                        if s1_margin_cap and s1_margin/nav_now > s1_margin_cap: break
                        if margin_cap and total_margin/nav_now > margin_cap: break
                        c = sel_s1_sell(ef, ot, d["mult"], d["mr"])
                        if c is None: continue
                        m = estimate_margin(c["spot_close"],c["strike"],ot,c["option_close"],d["mult"],d["mr"],0.5)
                        pl = margin_per / 2  # 双卖每方向分一半
                        nn = max(1, int(pl*sc*nav_now/m)) if m>0 else 1
                        if s1_margin_cap and (s1_margin+m*nn)/nav_now > s1_margin_cap: continue
                        if margin_cap and (total_margin+m*nn)/nav_now > margin_cap: continue
                        op = slip(c["option_close"],"sell",d["liq"]) if use_slip else c["option_close"]
                        positions.append(Pos("S1",pn,c["option_code"],ot,c["strike"],op,nn,date,d["mult"],exp,d["mr"],d["liq"],"sell",c["spot_close"]))
                        total_margin += m*nn
                        s1_margin += m*nn
                        pr = sel_s1_protect(ef, c)
                        if pr is not None and pr["option_code"]!=c["option_code"]:
                            pq = max(1, nn//2)
                            bop = slip(pr["option_close"],"buy",d["liq"]) if use_slip else pr["option_close"]
                            positions.append(Pos("S1",pn,pr["option_code"],ot,pr["strike"],bop,pq,date,d["mult"],exp,d["mr"],d["liq"],"buy",pr["spot_close"]))

                # S4 — 尾部保护，严格控制权利金支出
                _s4_prods = s4_products if s4_products is not None else S4_PRODUCTS
                if enable_s4 and _s4_prods and pn in _s4_prods:
                    if not any(p.strat=="S4" and p.product==pn and p.expiry==exp for p in positions):
                        for ot in ["P","C"]:
                            opt = sel_s4(ef, ot)
                            if opt is None: continue
                            budget = nav_now*s4_prem/len(_s4_prods)/2
                            cost = opt["option_close"]*d["mult"]
                            qty = max(1, int(budget/cost)) if cost>0 else 1
                            qty = min(qty, 5)  # 严格上限5手，控制极端行情波动
                            positions.append(Pos("S4",pn,opt["option_code"],ot,opt["strike"],
                                slip(opt["option_close"],"buy","deep_otm") if use_slip else opt["option_close"],
                                qty,date,d["mult"],exp,d["mr"],"deep_otm","buy",opt["spot_close"]))

        # === S2 波动率套利 新开仓（不依赖open_on，每日检查）===
        if enable_s2:
            s2_active_products = set(p.product for p in positions if p.strat=="S2")
            for name, d in pdata.items():
                if name not in iv_pcts:
                    continue
                ps = iv_pcts[name]
                if date not in ps.index or pd.isna(ps.loc[date]):
                    continue
                if ps.loc[date] < s2_iv_entry:
                    continue
                # 该品种当前无S2持仓
                if name in s2_active_products:
                    continue
                # 最多同时s2_max_products个品种
                if len(s2_active_products) >= s2_max_products:
                    break
                dg = d["dg"].get(date)
                if dg is None:
                    continue
                # 找合适的到期日（DTE 20-60天）的OTM Put，过滤IV异常
                puts = dg[(dg["option_type"]=="P")&(dg["moneyness"]<1.0)&
                          (dg["delta"]<0)&(dg["implied_vol"]>0)&(dg["implied_vol"]<2.0)&
                          (dg["dte"].between(20,60))&(dg["option_close"]>=0.5)]
                if puts.empty:
                    continue
                best_expiry = puts.groupby("expiry_date")["dte"].first()
                target_exp = (best_expiry - 35).abs().idxmin()
                exp_puts = puts[puts["expiry_date"]==target_exp]
                if len(exp_puts) < 3:
                    continue
                spot = exp_puts["spot_close"].iloc[0]
                dte_val = exp_puts["dte"].iloc[0]
                T = dte_val / 252
                # 买腿：|Delta| 0.15-0.35，选最接近0.15的
                buy_cands = exp_puts[(exp_puts["delta"].abs()>=0.15)&(exp_puts["delta"].abs()<=0.35)]
                if buy_cands.empty:
                    continue
                buy_row = buy_cands.loc[(buy_cands["delta"].abs()-0.15).abs().idxmin()]
                # 卖腿：|Delta| 0.02-0.08，选最虚值的，过滤IV异常
                sell_cands = exp_puts[(exp_puts["delta"].abs()>=0.02)&(exp_puts["delta"].abs()<=0.08)
                                      &(exp_puts["implied_vol"]<2.0)]  # IV>200%视为脏数据
                if sell_cands.empty:
                    continue
                sell_row = sell_cands.loc[sell_cands["moneyness"].idxmin()]
                if buy_row["option_code"]==sell_row["option_code"]:
                    continue
                # 过滤买腿IV异常
                if buy_row["implied_vol"] > 2.0:
                    continue
                # BSM Greeks
                buy_g = bsm_greeks(spot, buy_row["strike"], T, RISK_FREE, buy_row["implied_vol"], "P")
                sell_g = bsm_greeks(spot, sell_row["strike"], T, RISK_FREE, sell_row["implied_vol"], "P")
                # Vega中性比例（目标Vega = -30% × 买腿Vega）
                ratio = find_vega_neutral_ratio(buy_g, sell_g, -0.3)
                if ratio is None or ratio < 2 or ratio > 10:  # 上限从30降到10
                    continue
                # 验证组合Gamma > 0
                combo_gamma = buy_g["gamma"] - ratio * sell_g["gamma"]
                if combo_gamma <= 0:
                    continue
                # 手数：买腿权利金 ≤ 净值s2_prem_pct，上限20手
                buy_premium = buy_row["option_close"] * d["mult"]
                max_buy_n = max(1, int(s2_prem_pct * nav_now / buy_premium)) if buy_premium > 0 else 1
                buy_n = min(max_buy_n, 20)
                sell_n = buy_n * ratio
                # S2卖腿保证金上限：单品种不超5%资金
                sell_margin = estimate_margin(spot, sell_row["strike"], "P",
                                              sell_row["option_close"], d["mult"], d["mr"], 0.5)
                s2_sell_margin = sell_margin * sell_n
                if s2_sell_margin > nav_now * 0.05:
                    sell_n = max(1, int(nav_now * 0.05 / sell_margin))
                    buy_n = max(1, sell_n // ratio)
                    sell_n = buy_n * ratio
                    if sell_n < 2:
                        continue
                liq = d["liq"]
                bop = slip(buy_row["option_close"],"buy",liq) if use_slip else buy_row["option_close"]
                sop = slip(sell_row["option_close"],"sell",liq) if use_slip else sell_row["option_close"]
                positions.append(Pos("S2",name,buy_row["option_code"],"P",buy_row["strike"],
                    bop,buy_n,date,d["mult"],target_exp,d["mr"],liq,"buy",spot))
                positions.append(Pos("S2",name,sell_row["option_code"],"P",sell_row["strike"],
                    sop,sell_n,date,d["mult"],target_exp,d["mr"],liq,"sell",spot))
                s2_active_products.add(name)

        # NAV = capital + 累积daily MTM（与原版一致，不扣手续费）
        cum = (records[-1]["cum_pnl"] if records else 0) + day_pnl
        nav = CAPITAL + cum
        tm = sum(p.cur_margin() for p in positions if p.role=="sell")
        tm_s1 = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S1")
        tm_s3 = sum(p.cur_margin() for p in positions if p.role=="sell" and p.strat=="S3")
        records.append({
            "date": str(date.date()), "daily_pnl": day_pnl, "cum_pnl": cum, "nav": nav,
            "pnl_s1": pnl_s1, "pnl_s2": pnl_s2, "pnl_s3": pnl_s3, "pnl_s4": pnl_s4,
            "margin_pct": tm/nav,
            "margin_s1_pct": tm_s1/nav,
            "margin_s3_pct": tm_s3/nav,
            "n_s1": sum(1 for p in positions if p.strat=="S1" and p.role=="sell"),
            "n_s2": sum(1 for p in positions if p.strat=="S2" and p.role=="buy"),
            "n_s3": sum(1 for p in positions if p.strat=="S3" and p.role=="sell"),
            "n_s4": sum(1 for p in positions if p.strat=="S4"),
        })

    return pd.DataFrame(records)


def stats(nav_df):
    if nav_df.empty or len(nav_df)<10: return {}
    nav = nav_df["nav"].values
    dr = np.diff(nav)/nav[:-1]
    yrs = max(len(nav)/252, 0.5)
    ar = (nav[-1]/nav[0])**(1/yrs)-1
    vol = np.std(dr)*np.sqrt(252)
    sr = (ar-0.02)/vol if vol>0 else 0
    pk = np.maximum.accumulate(nav)
    mdd = ((nav-pk)/pk).min()
    cal = ar/abs(mdd) if mdd!=0 else 0
    return {"ann_return":ar,"ann_vol":vol,"max_dd":mdd,"sharpe":sr,"calmar":cal}


if __name__ == "__main__":
    os.makedirs("组合策略/output", exist_ok=True)
    SD = "2024-01-01"

    # S1 only 无滑点
    print("=== S1 only 无滑点 ===")
    nav1 = run_unified_v3(enable_s1=True, enable_s3=False, enable_s4=False, use_slip=False, start_date=SD)
    s = stats(nav1)
    print(f"年化:{s['ann_return']:+.1%} 回撤:{s['max_dd']:.1%} 夏普:{s['sharpe']:.2f} 卡玛:{s['calmar']:.2f}")
    print(f"保证金: mean={nav1['margin_pct'].mean():.1%} max={nav1['margin_pct'].max():.1%}")

    # S1 only 有滑点
    print("\n=== S1 only 有滑点 ===")
    nav1s = run_unified_v3(enable_s1=True, enable_s3=False, enable_s4=False, use_slip=True, start_date=SD)
    s = stats(nav1s)
    print(f"年化:{s['ann_return']:+.1%} 回撤:{s['max_dd']:.1%} 夏普:{s['sharpe']:.2f} 卡玛:{s['calmar']:.2f}")
    print(f"保证金: max={nav1s['margin_pct'].max():.1%}")

    # 三策略组合（S1+S3+S4）有滑点
    print("\n=== 三策略组合（S1+S3+S4）有滑点 ===")
    nav3 = run_unified_v3(use_slip=True, start_date=SD)
    s = stats(nav3)
    print(f"年化:{s['ann_return']:+.1%} 回撤:{s['max_dd']:.1%} 夏普:{s['sharpe']:.2f} 卡玛:{s['calmar']:.2f}")
    print(f"保证金: 总mean={nav3['margin_pct'].mean():.1%} 总max={nav3['margin_pct'].max():.1%}")
    print(f"  S1: mean={nav3['margin_s1_pct'].mean():.1%} max={nav3['margin_s1_pct'].max():.1%}")
    print(f"  S3: mean={nav3['margin_s3_pct'].mean():.1%} max={nav3['margin_s3_pct'].max():.1%}")
    nav3.to_csv("组合策略/output/nav_unified_v3.csv", index=False)

    # 三策略组合 无滑点
    print("\n=== 三策略组合（S1+S3+S4）无滑点 ===")
    nav3n = run_unified_v3(use_slip=False, start_date=SD)
    s = stats(nav3n)
    print(f"年化:{s['ann_return']:+.1%} 回撤:{s['max_dd']:.1%} 夏普:{s['sharpe']:.2f} 卡玛:{s['calmar']:.2f}")
    print(f"保证金: max={nav3n['margin_pct'].max():.1%}")
