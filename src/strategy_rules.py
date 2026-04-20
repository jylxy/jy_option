"""
策略规则模块：从 unified_engine_v3.py 提取的纯策略逻辑

所有函数均为无状态函数，不依赖全局变量或外部状态。
可被 daily_backtest.py 和 order_generator.py 共同调用。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore
from backtest_fast import estimate_margin

# ── 默认参数 ──────────────────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    "capital": 10_000_000,
    "margin_per": 0.02,
    "margin_cap": 0.50,
    "s1_margin_cap": 0.25,
    "s3_margin_cap": 0.25,
    "s1_tp": 0.40,
    "s3_tp": 0.30,
    "s3_ratio_candidates": [2, 3],       # S3买卖比例候选（优先小比例）
    "s3_buy_otm_pct": 5.0,               # S3买腿目标OTM%
    "s3_sell_otm_pct": 10.0,             # S3卖腿目标OTM%
    "s3_protect_otm_pct": 15.0,          # S3保护腿目标OTM%
    "s3_buy_otm_range": (3.0, 7.0),      # S3买腿OTM%筛选范围
    "s3_sell_otm_range": (7.0, 13.0),    # S3卖腿OTM%筛选范围
    "s3_protect_otm_range": (12.0, 20.0),# S3保护腿OTM%筛选范围
    "s3_net_premium_tolerance": 0.3,     # 零成本容忍度（允许亏损买腿成本的30%）
    "s3_protect_trigger_otm_pct": 5.0,   # 应急保护触发阈值（卖腿OTM%降至此值以下）
    "s4_prem": 0.005,
    "s4_max_hands": 5,
    "s4_max_hold": 30,  # S4 fallback持仓天数（主要用DTE<10退出）
    "iv_inverse": True,
    "iv_window": 252,
    "iv_min_periods": 60,
    "iv_threshold": 75,
    "iv_open_threshold": 80,
    "dte_target": 35,
    "dte_min": 15,
    "dte_max": 90,
    "tp_min_dte": 5,
    "reopen_min_dte": 10,
    "expiry_dte": 1,
    "fee": 3,
    "vwap_window": 10,
    "hedge_enabled": True,
    "hedge_scope": "family_net",
    "hedge_rebalance": "daily_t1_vwap",
    "hedge_target_cash_delta_pct": 0.0,
    "hedge_rounding": "min_abs_residual",
    "hedge_cost_mode": "none",
    "greeks_vega_warn": 0.008,
    "greeks_vega_hard": 0.01,
    "greeks_delta_hard": 0.10,
    "greeks_delta_target": 0.07,
    "greeks_vega_target": 0.007,
    # 品种准入
    "product_min_listing_days": 180,
    "product_min_daily_oi": 500,
    "product_observation_months": 3,
}


# ── 合约选择函数 ──────────────────────────────────────────────────────────────

def select_s1_sell(day_df, option_type, mult, mr, min_volume=0, min_oi=0,
                   iv_residual_weight=0.3):
    """
    S1卖腿选择：深虚值、|delta|<0.15、premium>=0.5、效率最高

    因子5：IV_Residual 加权选腿。IV_Residual > 0 表示该合约 IV 高于 smile 拟合值，
    Theta 衰减更快，优先选择。score = eff * (1 + weight * iv_residual)

    min_volume: 最低日成交量（0=不过滤）
    min_oi: 最低持仓量（0=不过滤）
    iv_residual_weight: IV_Residual 在选腿评分中的权重（默认0.3）
    返回: pd.Series (选中的行) 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() < 0.15) &
            (day_df["option_close"] >= 0.5)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] < 0.15) &
            (day_df["option_close"] >= 0.5)
        ]
    if c.empty:
        return None
    # 流动性过滤
    if min_volume > 0 and "volume" in c.columns:
        c = c[c["volume"] >= min_volume]
    if min_oi > 0 and "open_interest" in c.columns:
        c = c[c["open_interest"] >= min_oi]
    if c.empty:
        return None
    c = c.copy()
    c["margin"] = c.apply(
        lambda r: estimate_margin(r["spot_close"], r["strike"], option_type,
                                  r["option_close"], mult, mr, 0.5), axis=1)
    c["eff"] = c["option_close"] * mult / c["margin"]
    # 因子5：IV_Residual 加权（IV_Residual > 0 = IV 偏高，Theta 衰减更快）
    if "iv_residual" in c.columns and iv_residual_weight > 0:
        iv_res = c["iv_residual"].fillna(0).clip(-1, 1)
        c["score"] = c["eff"] * (1 + iv_residual_weight * iv_res)
    else:
        c["score"] = c["eff"]
    return c.loc[c["score"].idxmax()]


def select_s1_protect(day_df, sell_row):
    """
    S1保护腿选择：|delta|<0.25、更靠近平值、选|delta|最大

    返回: pd.Series 或 None
    """
    ot = sell_row["option_type"]
    if ot == "P":
        p = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() < 0.25) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] > sell_row["strike"])
        ]
        if p.empty:
            return None
        return p.loc[p["delta"].abs().idxmax()]
    else:
        p = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] < 0.25) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] < sell_row["strike"])
        ]
        if p.empty:
            return None
        return p.loc[p["delta"].idxmax()]


def select_s3_buy(day_df, option_type):
    """
    S3买腿选择：|delta| 0.10-0.20，选最接近0.15

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() >= 0.10) &
            (day_df["delta"].abs() <= 0.20) &
            (day_df["option_close"] >= 0.5)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] >= 0.10) &
            (day_df["delta"] <= 0.20) &
            (day_df["option_close"] >= 0.5)
        ]
    if c.empty:
        return None
    c = c.copy()
    c["dd"] = (c["delta"].abs() - 0.15).abs()
    return c.loc[c["dd"].idxmin()]


def select_s3_sell(day_df, option_type, buy_strike):
    """
    S3卖腿选择：|delta| 0.05-0.15，比买腿更虚值，选权利金最高

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["delta"] < 0) &
            (day_df["delta"].abs() >= 0.05) &
            (day_df["delta"].abs() <= 0.15) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] < buy_strike)
        ]
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["delta"] > 0) &
            (day_df["delta"] >= 0.05) &
            (day_df["delta"] <= 0.15) &
            (day_df["option_close"] >= 0.5) &
            (day_df["strike"] > buy_strike)
        ]
    if c.empty:
        return None
    return c.loc[c["option_close"].idxmax()]


def select_s3_protect(day_df, option_type, sell_strike, spot):
    """
    S3保护腿选择：比卖腿更虚值，行权价最接近target

    target_strike:
      Put:  sell_k - (spot - sell_k) × 0.5
      Call: sell_k + (sell_k - spot) × 0.5

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["option_close"] >= 0.1) &
            (day_df["strike"] < sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike - (spot - sell_strike) * 0.5
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["option_close"] >= 0.1) &
            (day_df["strike"] > sell_strike)
        ]
        if c.empty:
            return None
        tgt = sell_strike + (sell_strike - spot) * 0.5
    c = c.copy()
    c["d"] = (c["strike"] - tgt).abs()
    return c.loc[c["d"].idxmin()]


# ── S3 OTM%选腿函数（v2）────────────────────────────────────────────────────

def select_s3_buy_by_otm(day_df, option_type, spot_close,
                          target_otm_pct=5.0, otm_range=(3.0, 7.0),
                          min_premium=0.5):
    """S3买腿选择（v2）：按OTM%筛选，选最接近target_otm_pct的合约

    参数:
        day_df: 当日该品种该到期日的期权数据
        option_type: "P" 或 "C"
        spot_close: 标的收盘价（同月期货价格）
        target_otm_pct: 目标OTM%，默认5.0
        otm_range: OTM%筛选范围，默认(3.0, 7.0)
        min_premium: 最低权利金，默认0.5
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return c.loc[c["dist"].idxmin()]


def select_s3_sell_by_otm(day_df, option_type, spot_close, buy_strike,
                           target_otm_pct=10.0, otm_range=(7.0, 13.0),
                           min_premium=0.5):
    """S3卖腿选择（v2）：按OTM%筛选，比买腿更虚值，选权利金最高

    参数:
        buy_strike: 买腿行权价，卖腿必须比买腿更虚值
        其余同 select_s3_buy_by_otm
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["strike"] < buy_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["strike"] > buy_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    return c.loc[c["option_close"].idxmax()]


def select_s3_protect_by_otm(day_df, option_type, spot_close, sell_strike,
                              target_otm_pct=15.0, otm_range=(12.0, 20.0),
                              min_premium=0.1):
    """S3保护腿选择（v2）：应急保护触发时，按OTM%筛选，比卖腿更虚值，选最接近target

    参数:
        sell_strike: 卖腿行权价，保护腿必须比卖腿更虚值
        min_premium: 最低权利金，默认0.1（保护腿可以更便宜）
        其余同 select_s3_buy_by_otm
    返回: pd.Series（选中行）或 None
    """
    if spot_close <= 0:
        return None
    df = day_df.copy()
    df["otm_pct"] = abs(1 - df["strike"] / spot_close) * 100
    if option_type == "P":
        c = df[(df["option_type"] == "P") &
               (df["strike"] < spot_close) &
               (df["strike"] < sell_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    else:
        c = df[(df["option_type"] == "C") &
               (df["strike"] > spot_close) &
               (df["strike"] > sell_strike) &
               (df["otm_pct"] >= otm_range[0]) &
               (df["otm_pct"] <= otm_range[1]) &
               (df["option_close"] >= min_premium)]
    if c.empty:
        return None
    c = c.copy()
    c["dist"] = (c["otm_pct"] - target_otm_pct).abs()
    return c.loc[c["dist"].idxmin()]


def select_s4(day_df, option_type):
    """
    S4尾部对冲选择：最深虚值，premium>=0.1

    返回: pd.Series 或 None
    """
    if option_type == "P":
        c = day_df[
            (day_df["option_type"] == "P") &
            (day_df["moneyness"] < 1.0) &
            (day_df["option_close"] >= 0.1)
        ]
        return c.loc[c["moneyness"].idxmin()] if not c.empty else None
    else:
        c = day_df[
            (day_df["option_type"] == "C") &
            (day_df["moneyness"] > 1.0) &
            (day_df["option_close"] >= 0.1)
        ]
        return c.loc[c["moneyness"].idxmax()] if not c.empty else None


# ── 手数计算 ──────────────────────────────────────────────────────────────────

def calc_s1_size(nav, margin_per, single_margin, iv_scale):
    """S1每方向卖腿手数 = nav × margin_per/2 × iv_scale / 单手保证金"""
    if single_margin <= 0:
        return 1
    return max(1, int(nav * margin_per / 2 * iv_scale / single_margin))


def calc_s3_size(nav, margin_per, sell_margin, s3_ratio, iv_scale):
    """
    S3手数计算
    买腿手数 = nav × margin_per/2 × iv_scale / (单手卖腿保证金 × ratio)
    卖腿手数 = 买腿 × ratio
    """
    if sell_margin <= 0:
        return 1, s3_ratio
    buy_qty = max(1, int(nav * margin_per / 2 * iv_scale / (sell_margin * s3_ratio)))
    sell_qty = buy_qty * s3_ratio
    return buy_qty, sell_qty


def calc_s4_size(nav, s4_prem, n_s4_products, cost_per_hand, max_hands=5):
    """S4每方向手数，预算 = nav × s4_prem / 品种数 / 2方向"""
    if n_s4_products <= 0 or cost_per_hand <= 0:
        return 1
    budget = nav * s4_prem / n_s4_products / 2
    qty = max(1, int(budget / cost_per_hand))
    return min(qty, max_hands)


def calc_s3_size_v2(nav, margin_per, sell_margin, buy_premium,
                     sell_premium, mult, iv_scale,
                     ratio_candidates=(2, 3), net_premium_tolerance=0.3):
    """
    S3手数计算（v2）：灵活比例 + 零成本进场约束

    先尝试小比例(1:2)，若净权利金不够覆盖买腿成本再尝试大比例(1:3)。
    返回 (buy_qty, sell_qty, chosen_ratio) 或 None（无法满足零成本约束时）
    """
    if sell_margin <= 0 or buy_premium <= 0 or sell_premium <= 0:
        return None
    for ratio in sorted(ratio_candidates):
        buy_qty = max(1, int(nav * margin_per / 2 * iv_scale
                             / (sell_margin * ratio)))
        sell_qty = buy_qty * ratio
        # 零成本检查：net_premium = sell收入 - buy成本
        buy_cost = buy_premium * mult * buy_qty
        sell_income = sell_premium * mult * sell_qty
        net_premium = sell_income - buy_cost
        # 容忍范围：net_premium >= -buy_cost × tolerance
        if net_premium >= -buy_cost * net_premium_tolerance:
            return buy_qty, sell_qty, ratio
    return None


def check_emergency_protect(sell_strike, spot_close, option_type,
                             trigger_otm_pct=5.0):
    """
    检查卖腿是否接近平值，需要触发应急保护。

    当卖腿OTM%降至trigger_otm_pct以下时返回True。
    Put端：spot下跌使put卖腿接近平值
    Call端：spot上涨使call卖腿接近平值
    """
    if spot_close <= 0:
        return False
    current_otm_pct = abs(1 - sell_strike / spot_close) * 100
    return current_otm_pct < trigger_otm_pct


# ── IV分位数 ──────────────────────────────────────────────────────────────────

def calc_iv_percentile(iv_series, current_date, window=252, min_periods=60):
    """
    计算IV分位数（因果窗口：只使用截止current_date的数据）

    参数:
        iv_series: pd.Series，index=trade_date, value=ATM平均implied_vol
        current_date: 当前日期
        window: 滚动窗口大小
        min_periods: 最少数据点
    返回:
        float 分位数(0-100) 或 NaN
    """
    # 只使用截止当前日期的数据
    causal = iv_series[iv_series.index <= current_date]
    if len(causal) < min_periods:
        return np.nan
    recent = causal.iloc[-window:] if len(causal) > window else causal
    current_val = recent.iloc[-1]
    return percentileofscore(recent.values, current_val, kind='rank')


def calc_iv_percentile_batch(iv_series, window=252, min_periods=60):
    """
    批量计算IV分位数（rolling，与unified_engine_v3.py一致）

    返回: pd.Series，index=trade_date, value=percentile
    """
    return iv_series.rolling(window, min_periods=min_periods).apply(
        lambda x: percentileofscore(x, x.iloc[-1], kind='rank'))


def get_iv_scale(iv_pct, threshold=75):
    """
    因子1：波动率自适应仓位缩放

    iv_pct <= threshold: scale = 1.0（满仓）
    iv_pct > threshold:  scale = 1 - iv_pct/200（线性缩减）
    最低不低于 0.3（保留30%仓位）

    示例：iv_pct=80 → scale=0.6, iv_pct=90 → scale=0.55, iv_pct=100 → scale=0.5
    """
    if pd.isna(iv_pct) or iv_pct <= threshold:
        return 1.0
    scale = 1.0 - iv_pct / 200.0
    return max(scale, 0.3)


def should_pause_open(iv_pct, iv_open_threshold=80):
    """
    IV环境过滤：IV分位>阈值时暂停S1/S3新开仓（含止盈重开）。
    S4不受影响（买方策略，高波时更有价值）。

    回测验证：IV>80%暂停使夏普从1.13提升到1.47（+30%），
    回撤从-8.03%降到-7.05%，年化反而提升到+22.50%。
    """
    if pd.isna(iv_pct):
        return False  # 无数据时不过滤
    return iv_pct > iv_open_threshold


# ── ATM IV提取 ────────────────────────────────────────────────────────────────

def extract_atm_iv_series(product_df):
    """
    从品种全量数据中提取ATM隐含波动率时间序列

    筛选: moneyness ∈ [0.95, 1.05], dte ∈ [15, 90], implied_vol > 0
    返回: pd.Series，index=trade_date, value=daily_mean_atm_iv
    """
    atm = product_df[
        (product_df["moneyness"].between(0.95, 1.05)) &
        (product_df["dte"].between(15, 90)) &
        (product_df["implied_vol"] > 0)
    ]
    if atm.empty:
        return pd.Series(dtype=float)
    return atm.groupby("trade_date")["implied_vol"].mean()


# ── 信号判断 ──────────────────────────────────────────────────────────────────

def should_open_new(product_df_today, dte_target=35, dte_min=15, dte_max=90):
    """
    检查今日是否应该触发开仓（无前视偏差版本）

    逻辑：检查今日数据中，是否存在到期日使得DTE最接近dte_target
    且该到期日此前未出现过更近的DTE（即今天是最佳开仓日）。

    返回: list of expiry_date (需要开仓的到期日)
    """
    if product_df_today.empty:
        return []

    result = []
    for exp in product_df_today["expiry_date"].unique():
        exp_data = product_df_today[product_df_today["expiry_date"] == exp]
        if exp_data.empty:
            continue
        dte = exp_data["dte"].iloc[0]
        if dte < dte_min or dte > dte_max:
            continue
        # 只在DTE最接近target的那一天开仓
        # 逻辑：如果 |dte - target| <= |dte-1 - target|，说明今天或之前是最佳日
        # 简化为：dte <= target + 0.5（即DTE从远到近，第一次到达target附近时触发）
        # 更精确的做法：检查明天DTE是否会更近 → dte - 1更接近target吗？
        dist_today = abs(dte - dte_target)
        dist_tomorrow = abs(dte - 1 - dte_target)
        if dist_today <= dist_tomorrow:
            # 今天是最佳开仓日（明天会更远离target）
            result.append(exp)
    return result


def should_take_profit_s1(profit_pct, dte, tp=0.40, min_dte=5, iv_pct=None):
    """
    S1止盈判断（因子6：动态止盈阈值）

    高IV(>75分位): 止盈上移10%（让利润跑，高IV环境Theta衰减更快）
    低IV(<25分位): 止盈下移10%（快速落袋，低IV环境收益有限）
    """
    adj_tp = tp
    if iv_pct is not None and not pd.isna(iv_pct):
        if iv_pct > 75:
            adj_tp = tp * 1.1  # 高IV: 40% → 44%
        elif iv_pct < 25:
            adj_tp = tp * 0.9  # 低IV: 40% → 36%
    return profit_pct >= adj_tp and dte > min_dte


def should_take_profit_s3(profit_pct, dte, tp=0.30, min_dte=5, iv_pct=None):
    """
    S3止盈判断（因子6：动态止盈阈值）

    高IV(>75分位): 止盈上移10%
    低IV(<25分位): 止盈下移10%
    """
    adj_tp = tp
    if iv_pct is not None and not pd.isna(iv_pct):
        if iv_pct > 75:
            adj_tp = tp * 1.1  # 高IV: 30% → 33%
        elif iv_pct < 25:
            adj_tp = tp * 0.9  # 低IV: 30% → 27%
    return profit_pct >= adj_tp and dte > min_dte


def should_close_expiry(dte, threshold=1):
    """到期平仓判断"""
    return dte <= threshold


def can_reopen(dte, min_dte=10):
    """止盈后是否可以重开"""
    return dte > min_dte


# ── 保证金检查 ────────────────────────────────────────────────────────────────

def check_margin_ok(cur_total_margin, cur_strategy_margin, new_margin,
                    nav, margin_cap=0.50, strategy_cap=0.25):
    """
    检查新增保证金是否超限

    参数:
        cur_total_margin: 当前所有策略卖腿保证金之和
        cur_strategy_margin: 当前该策略的卖腿保证金之和
        new_margin: 新增保证金
        nav: 当前NAV
        margin_cap: 组合总保证金上限
        strategy_cap: 策略独立保证金上限
    """
    if margin_cap and (cur_total_margin + new_margin) / nav > margin_cap:
        return False
    if strategy_cap and (cur_strategy_margin + new_margin) / nav > strategy_cap:
        return False
    return True


# ── 滑点 ──────────────────────────────────────────────────────────────────────

def apply_slippage(price, direction, slippage=0.002):
    """
    施加滑点
    direction: 'buy' or 'sell'
    """
    if direction == "buy":
        return price * (1 + slippage)
    else:
        return price * (1 - slippage)


# ── NAV & 统计 ────────────────────────────────────────────────────────────────

def calc_stats(nav_array):
    """计算风险收益指标（与unified_engine_v3.stats一致）"""
    if len(nav_array) < 10:
        return {}
    dr = np.diff(nav_array) / nav_array[:-1]
    yrs = max(len(nav_array) / 252, 0.5)
    ar = (nav_array[-1] / nav_array[0]) ** (1 / yrs) - 1
    vol = np.std(dr) * np.sqrt(252)
    sr = (ar - 0.02) / vol if vol > 0 else 0
    pk = np.maximum.accumulate(nav_array)
    mdd = ((nav_array - pk) / pk).min()
    cal = ar / abs(mdd) if mdd != 0 else 0
    return {"ann_return": ar, "ann_vol": vol, "max_dd": mdd, "sharpe": sr, "calmar": cal}


# ── T+1 VWAP执行价格 ─────────────────────────────────────────────────────────

def load_t1_price_index(db_path="benchmark.db"):
    """
    加载所有合约的OHLC数据，构建T+1日执行价格索引。
    返回: dict[(trade_date, option_code)] -> {"open":, "high":, "low":, "close":, "typical":}
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    bars = pd.read_sql(
        "SELECT trade_date, option_code, open, high, low, close "
        "FROM stg_option_daily_bar WHERE volume > 0 AND open > 0",
        conn)
    conn.close()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["typical"] = (bars["high"] + bars["low"] + bars["close"]) / 3
    return bars.set_index(["trade_date", "option_code"])


def get_t1_execution_price(t1_idx, next_date, option_code, fallback_price):
    """
    获取T+1日VWAP执行价格。
    如果T+1日该合约无数据，返回fallback_price（T日收盘价）。
    """
    if t1_idx is None or next_date is None:
        return fallback_price
    key = (next_date, option_code)
    if key not in t1_idx.index:
        return fallback_price
    row = t1_idx.loc[key]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    typical = row["typical"]
    if pd.notna(typical) and typical > 0:
        return typical
    return fallback_price
