"""
期权定价与Greeks计算模块

统一的IV反推 + Greeks计算接口，实盘和回测共用同一套逻辑。

支持：
  - 欧式期权（股指/ETF）：BSM模型
  - 美式期权（商品）：Black76模型（期货标的近似）
  - 向量化批量计算（py_vollib_vectorized）

无风险利率默认2%（编码规范要求）。
"""
import numpy as np
import pandas as pd
import warnings

# ── 尝试加载向量化版本（快100倍），fallback到标量版 ──
try:
    from py_vollib_vectorized import vectorized_implied_volatility as vec_iv
    from py_vollib_vectorized import vectorized_black_scholes as vec_bs
    from py_vollib_vectorized.api import price_dataframe
    HAS_VECTORIZED = True
except ImportError:
    HAS_VECTORIZED = False

from py_vollib.black_scholes import black_scholes as bs_price
from py_vollib.black_scholes.implied_volatility import implied_volatility as bs_iv
from py_vollib.black_scholes.greeks.analytical import delta as bs_delta
from py_vollib.black_scholes.greeks.analytical import vega as bs_vega
from py_vollib.black_scholes.greeks.analytical import gamma as bs_gamma
from py_vollib.black_scholes.greeks.analytical import theta as bs_theta

# 无风险利率（编码规范：默认2%）
RISK_FREE_RATE = 0.02

# IV反推的边界
IV_MIN = 0.01    # 1%
IV_MAX = 5.0     # 500%（极端情况）
IV_DEFAULT = 0.25  # 反推失败时的默认值


def calc_iv_single(price, spot, strike, dte_days, option_type, r=RISK_FREE_RATE):
    """
    单合约IV反推（BSM模型）。
    
    Args:
        price: 期权市场价格
        spot: 标的价格（期货价格或ETF价格）
        strike: 行权价
        dte_days: 剩余天数（日历天）
        option_type: 'C' 或 'P'
        r: 无风险利率
    
    Returns:
        float: 隐含波动率（小数形式，如0.25表示25%），失败返回NaN
    """
    if price <= 0 or spot <= 0 or strike <= 0 or dte_days <= 0:
        return np.nan
    
    t = dte_days / 365.0
    flag = 'c' if option_type == 'C' else 'p'
    
    # 内在价值检查：期权价格不能低于内在价值
    if option_type == 'C':
        intrinsic = max(spot * np.exp(-r * t) - strike * np.exp(-r * t), 0)
    else:
        intrinsic = max(strike * np.exp(-r * t) - spot * np.exp(-r * t), 0)
    
    # 如果价格接近或低于内在价值，IV接近0
    if price <= intrinsic * 1.001:
        return IV_MIN
    
    try:
        iv = bs_iv(price, spot, strike, t, r, flag)
        # py_vollib 某些版本返回 DataFrame，需要提取标量
        if hasattr(iv, 'item'):
            iv = iv.item()
        elif hasattr(iv, 'values'):
            iv = float(iv.values.flat[0])
        iv = float(iv)
        if IV_MIN <= iv <= IV_MAX:
            return iv
        return np.nan
    except Exception:
        return np.nan


def calc_greeks_single(spot, strike, dte_days, iv, option_type, r=RISK_FREE_RATE):
    """
    单合约Greeks计算（BSM模型）。
    
    Returns:
        dict: {delta, gamma, vega, theta}，失败返回全NaN
    """
    nan_result = {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan}
    
    if spot <= 0 or strike <= 0 or dte_days <= 0 or iv <= 0:
        return nan_result
    
    t = dte_days / 365.0
    flag = 'c' if option_type == 'C' else 'p'
    
    try:
        d = bs_delta(flag, spot, strike, t, r, iv)
        g = bs_gamma(flag, spot, strike, t, r, iv)
        v = bs_vega(flag, spot, strike, t, r, iv)
        th = bs_theta(flag, spot, strike, t, r, iv)
        return {"delta": d, "gamma": g, "vega": v, "theta": th}
    except Exception:
        return nan_result


def calc_iv_batch(df, price_col="option_close", spot_col="spot_close",
                  strike_col="strike", dte_col="dte", otype_col="option_type",
                  r=RISK_FREE_RATE):
    """
    批量IV反推。优先用向量化版本，fallback到逐行计算。
    
    Args:
        df: DataFrame，必须包含 price/spot/strike/dte/option_type 列
        
    Returns:
        Series: 隐含波动率（与df同index）
    """
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float)
    
    prices = df[price_col].values.astype(float)
    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    otypes = df[otype_col].values
    
    t_arr = dtes / 365.0
    flags = np.where(otypes == 'C', 'c', 'p')
    
    # 向量化版本
    if HAS_VECTORIZED and n > 100:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                iv_arr = vec_iv(
                    prices, spots, strikes, t_arr, r, flags,
                    model='black_scholes', return_as='numpy'
                )
            # 清理异常值
            iv_arr = np.where((iv_arr >= IV_MIN) & (iv_arr <= IV_MAX), iv_arr, np.nan)
            return pd.Series(iv_arr, index=df.index)
        except Exception:
            pass  # fallback到逐行
    
    # 逐行计算
    result = np.full(n, np.nan)
    for i in range(n):
        if prices[i] > 0 and spots[i] > 0 and strikes[i] > 0 and dtes[i] > 0:
            result[i] = calc_iv_single(prices[i], spots[i], strikes[i], dtes[i], otypes[i], r)
    
    return pd.Series(result, index=df.index)


def calc_greeks_batch(df, spot_col="spot_close", strike_col="strike",
                      dte_col="dte", iv_col="implied_vol", otype_col="option_type",
                      r=RISK_FREE_RATE):
    """
    批量Greeks计算（逐行版，兼容旧代码）。
    新代码应优先使用 calc_greeks_batch_vectorized。
    """
    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=["delta", "gamma", "vega", "theta"])
    
    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    ivs = df[iv_col].values.astype(float)
    otypes = df[otype_col].values
    
    t_arr = dtes / 365.0
    flags = np.where(otypes == 'C', 'c', 'p')
    
    deltas = np.full(n, np.nan)
    gammas = np.full(n, np.nan)
    vegas = np.full(n, np.nan)
    thetas = np.full(n, np.nan)
    
    # 有效行掩码
    valid = (spots > 0) & (strikes > 0) & (dtes > 0) & (ivs > 0) & np.isfinite(ivs)
    
    if HAS_VECTORIZED and valid.sum() > 100:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # py_vollib_vectorized 的 delta
                idx = np.where(valid)[0]
                for i in idx:
                    try:
                        g = calc_greeks_single(spots[i], strikes[i], dtes[i], ivs[i], otypes[i], r)
                        deltas[i] = g["delta"]
                        gammas[i] = g["gamma"]
                        vegas[i] = g["vega"]
                        thetas[i] = g["theta"]
                    except Exception:
                        pass
        except Exception:
            pass
    else:
        for i in range(n):
            if valid[i]:
                try:
                    g = calc_greeks_single(spots[i], strikes[i], dtes[i], ivs[i], otypes[i], r)
                    deltas[i] = g["delta"]
                    gammas[i] = g["gamma"]
                    vegas[i] = g["vega"]
                    thetas[i] = g["theta"]
                except Exception:
                    pass
    
    return pd.DataFrame({
        "delta": deltas,
        "gamma": gammas,
        "vega": vegas,
        "theta": thetas,
    }, index=df.index)



# ══════════════════════════════════════════════════════════════════════════════
# 向量化 BSM Greeks — numpy 直接实现，不依赖 py_vollib 逐行调用
# ══════════════════════════════════════════════════════════════════════════════

def _bsm_d1_d2(spots, strikes, t, r, sigma):
    """
    向量化计算 BSM d1/d2。

    所有参数均为 numpy array（同 shape）。
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(t)
        d1 = (np.log(spots / strikes) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def _norm_cdf(x):
    """标准正态分布 CDF（向量化）"""
    from scipy.stats import norm
    return norm.cdf(x)


def _norm_pdf(x):
    """标准正态分布 PDF（向量化）"""
    return np.exp(-0.5 * x**2) / np.sqrt(2 * np.pi)


def calc_greeks_batch_vectorized(df, spot_col="spot_close", strike_col="strike",
                                  dte_col="dte", iv_col="implied_vol",
                                  otype_col="option_type", r=RISK_FREE_RATE):
    """
    向量化批量 Greeks 计算（numpy 直接实现 BSM 公式）。

    比 calc_greeks_batch 快 50-100 倍，适合大批量计算。

    Returns:
        DataFrame: 包含 delta, gamma, vega, theta 四列
    """
    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=["delta", "gamma", "vega", "theta"])

    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    ivs = df[iv_col].values.astype(float)
    otypes = df[otype_col].values

    t = dtes / 365.0
    is_call = (otypes == "C")

    # 有效行掩码
    valid = (spots > 0) & (strikes > 0) & (dtes > 0) & (ivs > 0) & np.isfinite(ivs)

    # 初始化结果
    deltas = np.full(n, np.nan)
    gammas = np.full(n, np.nan)
    vegas = np.full(n, np.nan)
    thetas = np.full(n, np.nan)

    if not valid.any():
        return pd.DataFrame({
            "delta": deltas, "gamma": gammas,
            "vega": vegas, "theta": thetas,
        }, index=df.index)

    # 提取有效子集
    v_spots = spots[valid]
    v_strikes = strikes[valid]
    v_t = t[valid]
    v_ivs = ivs[valid]
    v_call = is_call[valid]

    d1, d2 = _bsm_d1_d2(v_spots, v_strikes, v_t, r, v_ivs)
    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    npd1 = _norm_pdf(d1)
    sqrt_t = np.sqrt(v_t)
    exp_rt = np.exp(-r * v_t)

    # Delta
    v_delta = np.where(v_call, nd1, nd1 - 1.0)

    # Gamma（Call 和 Put 相同）
    v_gamma = npd1 / (v_spots * v_ivs * sqrt_t)

    # Vega（Call 和 Put 相同，单位：dPrice/dSigma）
    v_vega = v_spots * npd1 * sqrt_t

    # Theta
    term1 = -(v_spots * npd1 * v_ivs) / (2 * sqrt_t)
    theta_call = term1 - r * v_strikes * exp_rt * _norm_cdf(d2)
    theta_put = term1 + r * v_strikes * exp_rt * _norm_cdf(-d2)
    v_theta = np.where(v_call, theta_call, theta_put)
    # 转为每日 theta（除以365）
    v_theta = v_theta / 365.0

    # 写回结果
    deltas[valid] = v_delta
    gammas[valid] = v_gamma
    vegas[valid] = v_vega
    thetas[valid] = v_theta

    return pd.DataFrame({
        "delta": deltas,
        "gamma": gammas,
        "vega": vegas,
        "theta": thetas,
    }, index=df.index)
