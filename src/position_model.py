"""Position model used by the Toolkit minute backtest engine."""

import numpy as np

from margin_model import estimate_margin


class Position:
    """Single option position with PnL and cash-Greek helpers."""

    __slots__ = [
        "strat", "product", "code", "opt_type", "strike",
        "open_price", "n", "open_date", "mult", "expiry", "mr", "role",
        "prev_price", "cur_price", "cur_spot", "prev_spot", "exchange", "underlying_code",
        "prev_delta", "prev_gamma", "prev_vega", "prev_theta", "prev_iv",
        "cur_delta", "cur_gamma", "cur_vega", "cur_theta", "cur_iv",
        "group_id", "dte", "stress_loss",
    ]

    def __init__(
        self,
        strat,
        product,
        code,
        opt_type,
        strike,
        open_price,
        n,
        open_date,
        mult,
        expiry,
        mr,
        role,
        spot=0,
        exchange="",
        group_id="",
        underlying_code="",
    ):
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
        self.underlying_code = underlying_code
        self.prev_delta = np.nan
        self.prev_gamma = np.nan
        self.prev_vega = np.nan
        self.prev_theta = np.nan
        self.prev_iv = np.nan
        self.cur_delta = 0.0
        self.cur_gamma = 0.0
        self.cur_vega = 0.0
        self.cur_theta = 0.0
        self.cur_iv = 0.0
        self.group_id = group_id
        self.dte = 0
        self.stress_loss = 0.0

    def daily_pnl(self):
        if self.role in ("buy", "protect"):
            return (self.cur_price - self.prev_price) * self.mult * self.n
        return (self.prev_price - self.cur_price) * self.mult * self.n

    def profit_pct(self, fee_per_hand=0):
        if self.role != "sell" or self.open_price <= 0:
            return 0.0
        gross = (self.open_price - self.cur_price) * self.mult
        fee = fee_per_hand * 2
        revenue = self.open_price * self.mult
        return (gross - fee) / revenue if revenue > 0 else 0.0

    def cur_margin(self):
        if self.role != "sell":
            return 0.0
        return estimate_margin(
            self.cur_spot or self.strike,
            self.strike,
            self.opt_type,
            self.cur_price,
            self.mult,
            self.mr,
            0.5,
            exchange=self.exchange,
            product=self.product,
        ) * self.n

    def cash_delta(self):
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_delta * self.mult * self.n * (self.cur_spot or 0)

    def cash_vega(self):
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_vega * self.mult * self.n

    def cash_gamma(self):
        sign = 1 if self.role in ("buy", "protect") else -1
        spot = self.cur_spot or 0
        return sign * self.cur_gamma * self.mult * self.n * spot * spot

    def cash_theta(self):
        sign = 1 if self.role in ("buy", "protect") else -1
        return sign * self.cur_theta * self.mult * self.n

    def pnl_attribution(self, total_pnl=None, dt_days=1.0):
        sign = 1 if self.role in ("buy", "protect") else -1
        total = self.daily_pnl() if total_pnl is None else float(total_pnl)
        ds = (self.cur_spot or 0) - (self.prev_spot or 0)

        def avg_greek(prev_value, cur_value):
            prev_ok = np.isfinite(prev_value)
            cur_ok = np.isfinite(cur_value)
            if prev_ok and cur_ok:
                return 0.5 * (float(prev_value) + float(cur_value))
            if prev_ok:
                return float(prev_value)
            if cur_ok:
                return float(cur_value)
            return np.nan

        d_pnl = sign * self.prev_delta * ds * self.mult * self.n if np.isfinite(self.prev_delta) else 0.0
        gamma_for_attr = avg_greek(self.prev_gamma, self.cur_gamma)
        g_pnl = (
            sign * 0.5 * gamma_for_attr * ds * ds * self.mult * self.n
            if np.isfinite(gamma_for_attr)
            else 0.0
        )
        theta_for_attr = avg_greek(self.prev_theta, self.cur_theta)
        t_pnl = (
            sign * theta_for_attr * float(dt_days) * self.mult * self.n
            if np.isfinite(theta_for_attr)
            else 0.0
        )
        v_pnl = 0.0
        if np.isfinite(self.prev_vega) and np.isfinite(self.prev_iv) and np.isfinite(self.cur_iv):
            d_iv = float(self.cur_iv) - float(self.prev_iv)
            vega_for_attr = avg_greek(self.prev_vega, self.cur_vega)
            if np.isfinite(vega_for_attr):
                v_pnl = sign * vega_for_attr * (d_iv / 0.01) * self.mult * self.n
        residual_pnl = total - d_pnl - g_pnl - t_pnl - v_pnl
        return {
            "delta_pnl": d_pnl,
            "gamma_pnl": g_pnl,
            "theta_pnl": t_pnl,
            "vega_pnl": v_pnl,
            "residual_pnl": residual_pnl,
            "total_pnl": total,
        }
