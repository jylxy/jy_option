"""
Archived legacy engine dependency: IV Smile 曲线构建模块

每日收盘时为每个品种的每个到期月构建 IV Smile 曲线（沿 moneyness 方向拟合），
计算 IV_Residual 作为选腿附加因子。

商品期权每个品种每月只有一个到期日，因此不需要拟合 DTE 维度的曲面，
只需沿 moneyness 方向拟合一条二次多项式曲线即可。

拟合公式：IV = a₀ + a₁·m + a₂·m²
  其中 m = ln(K/S) 为 moneyness
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 拟合有效性阈值
MIN_CONTRACTS = 5   # 最少合约数
MIN_R_SQUARED = 0.3  # 最低 R²


class IVSmile:
    """单品种单到期月的 IV Smile 曲线"""

    def __init__(self, product, expiry_month, date_str):
        """
        Args:
            product: 品种根码，如 "m"
            expiry_month: 到期月份，如 "2409"
            date_str: 交易日期 "YYYY-MM-DD"
        """
        self.product = product
        self.expiry_month = expiry_month
        self.date_str = date_str
        self._coeffs = None   # 二次多项式系数 [a0, a1, a2]
        self._r_squared = 0.0
        self._n_contracts = 0
        self._valid = False

    def fit(self, contracts_df):
        """
        从日频合约数据拟合 IV Smile 曲线。

        输入 DataFrame 需包含列：strike, implied_vol, spot_close
        拟合方法：二次多项式 IV = a₀ + a₁·m + a₂·m²

        Returns:
            float: R² 拟合优度
        """
        # 过滤有效数据
        valid = contracts_df[
            contracts_df["implied_vol"].notna() &
            (contracts_df["implied_vol"] > 0) &
            (contracts_df["spot_close"] > 0) &
            (contracts_df["strike"] > 0)
        ].copy()

        self._n_contracts = len(valid)
        if self._n_contracts < MIN_CONTRACTS:
            self._valid = False
            return 0.0

        # 计算 moneyness = ln(K/S)
        valid["m"] = np.log(valid["strike"] / valid["spot_close"])
        iv = valid["implied_vol"].values
        m = valid["m"].values

        try:
            # 二次多项式拟合：IV = a0 + a1*m + a2*m^2
            coeffs = np.polyfit(m, iv, 2)  # [a2, a1, a0]（numpy 高次在前）
            self._coeffs = coeffs

            # 计算 R²
            iv_fitted = np.polyval(coeffs, m)
            ss_res = np.sum((iv - iv_fitted) ** 2)
            ss_tot = np.sum((iv - np.mean(iv)) ** 2)
            if ss_tot < 1e-10:
                # 所有 IV 几乎相同，拟合无意义
                self._r_squared = 0.0
            else:
                self._r_squared = 1.0 - ss_res / ss_tot

            self._valid = self._r_squared >= MIN_R_SQUARED
        except (np.linalg.LinAlgError, ValueError) as exc:
            logger.debug("  IV Smile 拟合失败 %s/%s: %s",
                         self.product, self.expiry_month, exc)
            self._valid = False
            self._r_squared = 0.0

        return self._r_squared

    def get_fitted_iv(self, moneyness):
        """
        查询拟合曲线上的理论 IV。

        Args:
            moneyness: ln(K/S)
        Returns:
            float: 理论 IV，曲线无效时返回 NaN
        """
        if not self._valid or self._coeffs is None:
            return np.nan
        return float(np.polyval(self._coeffs, moneyness))

    def calc_residual(self, actual_iv, moneyness):
        """
        IV_Residual = actual_iv - fitted_iv。
        正值表示 IV 偏高（卖出有利）。
        """
        fitted = self.get_fitted_iv(moneyness)
        if np.isnan(fitted):
            return np.nan
        return actual_iv - fitted

    def calc_residuals_batch(self, contracts_df):
        """
        批量计算 IV_Residual。

        输入 DataFrame 需包含列：strike, implied_vol, spot_close
        Returns:
            pd.Series: IV_Residual（与 df 同 index）
        """
        if not self._valid or self._coeffs is None:
            return pd.Series(np.nan, index=contracts_df.index)

        m = np.log(contracts_df["strike"] / contracts_df["spot_close"].clip(lower=1e-10))
        fitted = np.polyval(self._coeffs, m.values)
        return contracts_df["implied_vol"] - fitted

    @property
    def r_squared(self):
        """拟合 R² 值"""
        return self._r_squared

    @property
    def is_valid(self):
        """曲线是否有效（合约数 ≥ 5 且 R² > 0.3）"""
        return self._valid

    @property
    def n_contracts(self):
        """参与拟合的合约数"""
        return self._n_contracts


# ══════════════════════════════════════════════════════════════════════════════
# 批量构建函数
# ══════════════════════════════════════════════════════════════════════════════

def build_iv_smiles(daily_df, products, date_str):
    """
    为多个品种的每个到期月批量构建 IV Smile 曲线。

    Args:
        daily_df: aggregate_daily() 输出的 DataFrame，
                  需包含 product, expiry_date, strike, implied_vol, spot_close
        products: 品种根码列表，如 ["m", "cu", "rb"]
        date_str: 交易日期 "YYYY-MM-DD"

    Returns:
        dict: {product_name: {expiry_month_str: IVSmile}}
    """
    result = {}

    if daily_df is None or daily_df.empty:
        return result

    for product in products:
        prod_df = daily_df[daily_df["product"] == product]
        if prod_df.empty:
            continue

        product_smiles = {}

        # 按到期日分组（每个到期日一条 Smile）
        if "expiry_date" not in prod_df.columns:
            continue

        for expiry, exp_df in prod_df.groupby("expiry_date"):
            expiry_str = str(expiry)[:7].replace("-", "")  # "2024-09" → "202409"
            if len(expiry_str) < 4:
                continue

            smile = IVSmile(product, expiry_str, date_str)
            r2 = smile.fit(exp_df)

            if smile.is_valid:
                product_smiles[expiry_str] = smile
                logger.debug("  IV Smile %s/%s: R²=%.3f, n=%d",
                             product, expiry_str, r2, smile.n_contracts)

        if product_smiles:
            result[product] = product_smiles

    return result


def get_iv_residual(smiles_dict, product, expiry_date, strike, spot_close, implied_vol):
    """
    便捷函数：从 smiles_dict 中查询某合约的 IV_Residual。

    Args:
        smiles_dict: build_iv_smiles() 的返回值
        product: 品种根码
        expiry_date: 到期日（date 或 str）
        strike: 行权价
        spot_close: 标的价格
        implied_vol: 实际 IV

    Returns:
        float: IV_Residual，无可用 Smile 时返回 NaN
    """
    if product not in smiles_dict:
        return np.nan

    expiry_str = str(expiry_date)[:7].replace("-", "")
    product_smiles = smiles_dict[product]

    if expiry_str not in product_smiles:
        return np.nan

    smile = product_smiles[expiry_str]
    if not smile.is_valid or spot_close <= 0:
        return np.nan

    moneyness = np.log(strike / spot_close)
    return smile.calc_residual(implied_vol, moneyness)
