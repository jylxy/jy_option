"""
Unified IV and Greeks helpers for the backtest engines.

Model routing:
- Index / ETF options default to Black-Scholes.
- Commodity options on futures default to Black-76.

The public function names are kept stable so existing callers continue to work.
"""

import warnings

import numpy as np
import pandas as pd
from scipy.stats import norm

try:
    from py_vollib_vectorized import vectorized_implied_volatility as vec_iv_bs
    from py_vollib_vectorized import vectorized_implied_volatility_black as vec_iv_black
    HAS_VECTORIZED = True
except ImportError:
    HAS_VECTORIZED = False
    vec_iv_bs = None
    vec_iv_black = None

from py_vollib.black.implied_volatility import implied_volatility as black_iv
from py_vollib.black.greeks.analytical import delta as black_delta
from py_vollib.black.greeks.analytical import gamma as black_gamma
from py_vollib.black.greeks.analytical import vega as black_vega
from py_vollib.black.greeks.analytical import theta as black_theta
from py_vollib.black_scholes.implied_volatility import implied_volatility as bs_iv
from py_vollib.black_scholes.greeks.analytical import delta as bs_delta
from py_vollib.black_scholes.greeks.analytical import gamma as bs_gamma
from py_vollib.black_scholes.greeks.analytical import vega as bs_vega
from py_vollib.black_scholes.greeks.analytical import theta as bs_theta


RISK_FREE_RATE = 0.02

IV_MIN = 0.01
IV_MAX = 5.0
IV_DEFAULT = 0.25

BLACK_MODEL_EXCHANGES = frozenset({"SHFE", "INE", "DCE", "CZCE", "GFEX"})


def _normalize_model_name(model):
    if model is None:
        return ""
    text = str(model).strip().lower()
    if text in ("black", "black76", "black_76"):
        return "black"
    if text in ("black_scholes", "bs", "bsm"):
        return "black_scholes"
    return text


def _model_from_exchange(exchange):
    ex = str(exchange or "").strip().upper()
    return "black" if ex in BLACK_MODEL_EXCHANGES else "black_scholes"


def _resolve_single_model(exchange="", model=None):
    normalized = _normalize_model_name(model)
    if normalized:
        return normalized
    return _model_from_exchange(exchange)


def _resolve_models(df, exchange_col="exchange", model=None):
    normalized = _normalize_model_name(model)
    if normalized:
        return np.full(len(df), normalized, dtype=object)

    if exchange_col and exchange_col in df.columns:
        ex = df[exchange_col].fillna("").astype(str).str.upper().values
        return np.where(np.isin(ex, list(BLACK_MODEL_EXCHANGES)), "black", "black_scholes")

    return np.full(len(df), "black_scholes", dtype=object)


def _norm_cdf(x):
    return norm.cdf(x)


def _norm_pdf(x):
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def _bsm_d1_d2(spots, strikes, t, r, sigma):
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(t)
        d1 = (np.log(spots / strikes) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def _black_d1_d2(forwards, strikes, t, sigma):
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(t)
        d1 = (np.log(forwards / strikes) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def calc_iv_single(price, spot, strike, dte_days, option_type, r=RISK_FREE_RATE,
                   exchange="", model=None):
    if price <= 0 or spot <= 0 or strike <= 0 or dte_days <= 0:
        return np.nan

    t = dte_days / 365.0
    flag = "c" if option_type == "C" else "p"
    resolved_model = _resolve_single_model(exchange=exchange, model=model)

    if resolved_model == "black":
        intrinsic = np.exp(-r * t) * max(spot - strike, 0.0) if option_type == "C" else np.exp(-r * t) * max(strike - spot, 0.0)
        if price <= intrinsic * 1.001:
            return IV_MIN
        iv_func = lambda: black_iv(price, spot, strike, r, t, flag)
    else:
        intrinsic = max(spot * np.exp(-r * t) - strike * np.exp(-r * t), 0.0) if option_type == "C" else max(strike * np.exp(-r * t) - spot * np.exp(-r * t), 0.0)
        if price <= intrinsic * 1.001:
            return IV_MIN
        iv_func = lambda: bs_iv(price, spot, strike, t, r, flag)

    try:
        iv = iv_func()
        if hasattr(iv, "item"):
            iv = iv.item()
        elif hasattr(iv, "values"):
            iv = float(iv.values.flat[0])
        iv = float(iv)
        if IV_MIN <= iv <= IV_MAX:
            return iv
        return np.nan
    except Exception:
        return np.nan


def calc_greeks_single(spot, strike, dte_days, iv, option_type, r=RISK_FREE_RATE,
                       exchange="", model=None):
    nan_result = {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan}

    if spot <= 0 or strike <= 0 or dte_days <= 0 or iv <= 0:
        return nan_result

    t = dte_days / 365.0
    flag = "c" if option_type == "C" else "p"
    resolved_model = _resolve_single_model(exchange=exchange, model=model)

    try:
        if resolved_model == "black":
            return {
                "delta": black_delta(flag, spot, strike, t, r, iv),
                "gamma": black_gamma(flag, spot, strike, t, r, iv),
                "vega": black_vega(flag, spot, strike, t, r, iv),
                "theta": black_theta(flag, spot, strike, t, r, iv),
            }
        return {
            "delta": bs_delta(flag, spot, strike, t, r, iv),
            "gamma": bs_gamma(flag, spot, strike, t, r, iv),
            "vega": bs_vega(flag, spot, strike, t, r, iv),
            "theta": bs_theta(flag, spot, strike, t, r, iv),
        }
    except Exception:
        return nan_result


def calc_iv_batch(df, price_col="option_close", spot_col="spot_close",
                  strike_col="strike", dte_col="dte", otype_col="option_type",
                  exchange_col="exchange", model=None, r=RISK_FREE_RATE):
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float)

    prices = df[price_col].values.astype(float)
    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    otypes = df[otype_col].values
    flags = np.where(otypes == "C", "c", "p")
    t_arr = dtes / 365.0
    models = _resolve_models(df, exchange_col=exchange_col, model=model)
    valid = (prices > 0) & (spots > 0) & (strikes > 0) & (dtes > 0)

    result = np.full(n, np.nan)

    if HAS_VECTORIZED and n > 100:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                bs_mask = valid & (models == "black_scholes")
                if bs_mask.any():
                    result[bs_mask] = vec_iv_bs(
                        prices[bs_mask], spots[bs_mask], strikes[bs_mask], t_arr[bs_mask], r, flags[bs_mask],
                        model="black_scholes", return_as="numpy"
                    )

                black_mask = valid & (models == "black")
                if black_mask.any():
                    result[black_mask] = vec_iv_black(
                        prices[black_mask], spots[black_mask], strikes[black_mask], r, t_arr[black_mask], flags[black_mask],
                        return_as="numpy"
                    )

            result = np.where((result >= IV_MIN) & (result <= IV_MAX), result, np.nan)
            return pd.Series(result, index=df.index)
        except Exception:
            pass

    idx = np.where(valid)[0]
    for i in idx:
        result[i] = calc_iv_single(
            prices[i], spots[i], strikes[i], dtes[i], otypes[i], r=r, model=models[i]
        )

    return pd.Series(result, index=df.index)


def calc_option_price_batch(df, spot_col="spot_close", strike_col="strike",
                            dte_col="dte", iv_col="implied_vol",
                            otype_col="option_type", exchange_col="exchange",
                            model=None, r=RISK_FREE_RATE,
                            spot_shift_pct=0.0, iv_shift=0.0):
    """Vectorized theoretical option price for BSM / Black76.

    `iv_shift` is expressed in absolute volatility units: 0.05 means +5 vol
    points. `spot_shift_pct` is a relative shock applied to the underlying or
    futures price before repricing.
    """
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float)

    spots = pd.to_numeric(df[spot_col], errors="coerce").to_numpy(dtype=float)
    strikes = pd.to_numeric(df[strike_col], errors="coerce").to_numpy(dtype=float)
    dtes = pd.to_numeric(df[dte_col], errors="coerce").to_numpy(dtype=float)
    ivs = pd.to_numeric(df[iv_col], errors="coerce").to_numpy(dtype=float)
    otypes = df[otype_col].values
    is_call = (otypes == "C")
    spots = spots * (1.0 + float(spot_shift_pct or 0.0))
    ivs = ivs + float(iv_shift or 0.0)
    t = dtes / 365.0
    models = _resolve_models(df, exchange_col=exchange_col, model=model)

    valid = (
        (spots > 0)
        & (strikes > 0)
        & (dtes > 0)
        & (ivs > 0)
        & np.isfinite(ivs)
    )
    prices = np.full(n, np.nan)
    if not valid.any():
        return pd.Series(prices, index=df.index)

    valid_idx = np.where(valid)[0]
    v_spots = spots[valid]
    v_strikes = strikes[valid]
    v_t = t[valid]
    v_ivs = ivs[valid]
    v_call = is_call[valid]
    v_models = models[valid]

    if (v_models == "black_scholes").any():
        mask = (v_models == "black_scholes")
        sub_idx = valid_idx[mask]
        sub_spots = v_spots[mask]
        sub_strikes = v_strikes[mask]
        sub_t = v_t[mask]
        sub_ivs = v_ivs[mask]
        sub_call = v_call[mask]

        d1, d2 = _bsm_d1_d2(sub_spots, sub_strikes, sub_t, r, sub_ivs)
        exp_rt = np.exp(-r * sub_t)
        call = sub_spots * _norm_cdf(d1) - sub_strikes * exp_rt * _norm_cdf(d2)
        put = sub_strikes * exp_rt * _norm_cdf(-d2) - sub_spots * _norm_cdf(-d1)
        prices[sub_idx] = np.where(sub_call, call, put)

    if (v_models == "black").any():
        mask = (v_models == "black")
        sub_idx = valid_idx[mask]
        sub_forwards = v_spots[mask]
        sub_strikes = v_strikes[mask]
        sub_t = v_t[mask]
        sub_ivs = v_ivs[mask]
        sub_call = v_call[mask]

        d1, d2 = _black_d1_d2(sub_forwards, sub_strikes, sub_t, sub_ivs)
        exp_rt = np.exp(-r * sub_t)
        call = exp_rt * (sub_forwards * _norm_cdf(d1) - sub_strikes * _norm_cdf(d2))
        put = exp_rt * (sub_strikes * _norm_cdf(-d2) - sub_forwards * _norm_cdf(-d1))
        prices[sub_idx] = np.where(sub_call, call, put)

    prices = np.where(prices >= 0, prices, np.nan)
    return pd.Series(prices, index=df.index)


def calc_greeks_batch(df, spot_col="spot_close", strike_col="strike",
                      dte_col="dte", iv_col="implied_vol", otype_col="option_type",
                      exchange_col="exchange", model=None, r=RISK_FREE_RATE):
    if len(df) > 100:
        return calc_greeks_batch_vectorized(
            df,
            spot_col=spot_col,
            strike_col=strike_col,
            dte_col=dte_col,
            iv_col=iv_col,
            otype_col=otype_col,
            exchange_col=exchange_col,
            model=model,
            r=r,
        )

    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=["delta", "gamma", "vega", "theta"])

    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    ivs = df[iv_col].values.astype(float)
    otypes = df[otype_col].values
    models = _resolve_models(df, exchange_col=exchange_col, model=model)

    deltas = np.full(n, np.nan)
    gammas = np.full(n, np.nan)
    vegas = np.full(n, np.nan)
    thetas = np.full(n, np.nan)

    valid = (spots > 0) & (strikes > 0) & (dtes > 0) & (ivs > 0) & np.isfinite(ivs)

    for i in np.where(valid)[0]:
        g = calc_greeks_single(
            spots[i], strikes[i], dtes[i], ivs[i], otypes[i], r=r, model=models[i]
        )
        deltas[i] = g["delta"]
        gammas[i] = g["gamma"]
        vegas[i] = g["vega"]
        thetas[i] = g["theta"]

    return pd.DataFrame({
        "delta": deltas,
        "gamma": gammas,
        "vega": vegas,
        "theta": thetas,
    }, index=df.index)


def calc_greeks_batch_vectorized(df, spot_col="spot_close", strike_col="strike",
                                 dte_col="dte", iv_col="implied_vol",
                                 otype_col="option_type", exchange_col="exchange",
                                 model=None, r=RISK_FREE_RATE):
    n = len(df)
    if n == 0:
        return pd.DataFrame(columns=["delta", "gamma", "vega", "theta"])

    spots = df[spot_col].values.astype(float)
    strikes = df[strike_col].values.astype(float)
    dtes = df[dte_col].values.astype(float)
    ivs = df[iv_col].values.astype(float)
    otypes = df[otype_col].values
    is_call = (otypes == "C")
    t = dtes / 365.0
    models = _resolve_models(df, exchange_col=exchange_col, model=model)

    valid = (spots > 0) & (strikes > 0) & (dtes > 0) & (ivs > 0) & np.isfinite(ivs)

    deltas = np.full(n, np.nan)
    gammas = np.full(n, np.nan)
    vegas = np.full(n, np.nan)
    thetas = np.full(n, np.nan)

    if not valid.any():
        return pd.DataFrame({
            "delta": deltas,
            "gamma": gammas,
            "vega": vegas,
            "theta": thetas,
        }, index=df.index)

    valid_idx = np.where(valid)[0]
    v_spots = spots[valid]
    v_strikes = strikes[valid]
    v_t = t[valid]
    v_ivs = ivs[valid]
    v_call = is_call[valid]
    v_models = models[valid]

    if (v_models == "black_scholes").any():
        mask = (v_models == "black_scholes")
        sub_idx = valid_idx[mask]
        sub_spots = v_spots[mask]
        sub_strikes = v_strikes[mask]
        sub_t = v_t[mask]
        sub_ivs = v_ivs[mask]
        sub_call = v_call[mask]

        d1, d2 = _bsm_d1_d2(sub_spots, sub_strikes, sub_t, r, sub_ivs)
        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        npd1 = _norm_pdf(d1)
        sqrt_t = np.sqrt(sub_t)
        exp_rt = np.exp(-r * sub_t)

        deltas[sub_idx] = np.where(sub_call, nd1, nd1 - 1.0)
        gammas[sub_idx] = npd1 / (sub_spots * sub_ivs * sqrt_t)
        vegas[sub_idx] = sub_spots * npd1 * sqrt_t * 0.01

        term1 = -(sub_spots * npd1 * sub_ivs) / (2.0 * sqrt_t)
        theta_call = term1 - r * sub_strikes * exp_rt * nd2
        theta_put = term1 + r * sub_strikes * exp_rt * _norm_cdf(-d2)
        thetas[sub_idx] = np.where(sub_call, theta_call, theta_put) / 365.0

    if (v_models == "black").any():
        mask = (v_models == "black")
        sub_idx = valid_idx[mask]
        sub_forwards = v_spots[mask]
        sub_strikes = v_strikes[mask]
        sub_t = v_t[mask]
        sub_ivs = v_ivs[mask]
        sub_call = v_call[mask]

        d1, d2 = _black_d1_d2(sub_forwards, sub_strikes, sub_t, sub_ivs)
        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        npd1 = _norm_pdf(d1)
        sqrt_t = np.sqrt(sub_t)
        exp_rt = np.exp(-r * sub_t)

        deltas[sub_idx] = np.where(sub_call, exp_rt * nd1, -exp_rt * _norm_cdf(-d1))
        gammas[sub_idx] = exp_rt * npd1 / (sub_forwards * sub_ivs * sqrt_t)
        vegas[sub_idx] = sub_forwards * exp_rt * npd1 * sqrt_t * 0.01

        first_term = sub_forwards * exp_rt * npd1 * sub_ivs / (2.0 * sqrt_t)
        theta_call = -(first_term - r * sub_forwards * exp_rt * nd1 + r * sub_strikes * exp_rt * nd2) / 365.0
        theta_put = (-first_term - r * sub_forwards * exp_rt * _norm_cdf(-d1) + r * sub_strikes * exp_rt * _norm_cdf(-d2)) / 365.0
        thetas[sub_idx] = np.where(sub_call, theta_call, theta_put)

    return pd.DataFrame({
        "delta": deltas,
        "gamma": gammas,
        "vega": vegas,
        "theta": thetas,
    }, index=df.index)
