"""
Archived legacy experiment: 快速卖权回测引擎（内存版）

优化：一次性加载品种全部enriched数据到内存，避免逐笔查数据库。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import pandas as pd
import numpy as np
from dataclasses import dataclass
from margin_model import estimate_margin

DB_PATH = os.environ.get("OPTION_DB_PATH", "benchmark.db")


@dataclass
class BacktestConfig:
    target_delta: float = 0.05       # 目标Delta绝对值（乐得用最虚档，约0.03-0.05）
    delta_tolerance: float = 0.03    # Delta容差
    delta_max: float = 0.15          # Delta上限（超过此值不选）
    select_mode: str = "deepest_otm" # "target_delta" 或 "deepest_otm"（最虚档）
    sell_direction: str = "both"     # "both"=双卖, "put"=只卖Put, "call"=只卖Call, "skew"=基于偏斜动态选
    dte_min: int = 15
    dte_max: int = 90
    dte_target: int = 35
    min_volume: float = 0
    min_option_price: float = 0.5    # 最低权利金（过滤无流动性的合约）
    initial_capital: float = 10_000_000
    fee_per_contract: float = 7.0
    exit_dte: int = 1
    # 仓位管理
    sizing_mode: str = "margin"      # "vega", "leverage", "margin"
    vega_budget: float = 0.01        # 目标Cash Vega（1%）
    max_contracts_per_leg: int = 99999   # 不限制手数
    max_leverage: float = 1.0            # 最大杠杆率（名义本金/账户净值）
    # 保证金模式参数
    target_margin_rate: float = 0.08     # 该品种的保证金占用率上限（默认8%）
    margin_ratio: float = 0.10           # 交易所保证金比例（CFFEX=10%, 商品=5-7%）
    min_guarantee: float = 0.5           # 最低保障系数


def load_product_data(conn, where_clause):
    """一次性加载品种全部enriched数据"""
    df = pd.read_sql(f"""
        SELECT trade_date, option_code, option_type, strike,
               delta, implied_vol, moneyness, dte,
               spot_close, option_close, expiry_date, vega
        FROM mart_option_daily_enriched
        WHERE {where_clause}
          AND implied_vol > 0
          AND pricing_status = 'usable'
          AND option_close > 0
        ORDER BY trade_date, expiry_date, strike
    """, conn)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    return df


def run_backtest(df, config, multiplier=100.0):
    """在内存中运行回测"""
    cfg = config
    delta_lo = cfg.target_delta - cfg.delta_tolerance
    delta_hi = cfg.target_delta + cfg.delta_tolerance

    # 获取所有到期日
    expiry_dates = sorted(df["expiry_date"].unique())

    trades = []
    for expiry in expiry_dates:
        exp_df = df[df["expiry_date"] == expiry]
        if exp_df.empty:
            continue

        # 找开仓日：DTE最接近target的交易日
        candidates = exp_df[(exp_df["dte"] >= cfg.dte_min) & (exp_df["dte"] <= cfg.dte_max)]
        if candidates.empty:
            continue

        dte_by_date = candidates.groupby("trade_date")["dte"].first()
        best_date_idx = (dte_by_date - cfg.dte_target).abs().idxmin()
        open_date = best_date_idx
        open_day_df = candidates[candidates["trade_date"] == open_date]

        # 过滤：权利金太低的不选（无流动性）
        open_day_df = open_day_df[open_day_df["option_close"] >= cfg.min_option_price]

        # 如果是skew模式，先判断本次开仓的方向
        actual_direction = cfg.sell_direction
        if cfg.sell_direction == "skew":
            # 比较OTM Call和OTM Put的平均IV，哪边高卖哪边
            otm_c = open_day_df[
                (open_day_df["option_type"] == "C") &
                (open_day_df["moneyness"] > 1.0) &
                (open_day_df["delta"] > 0) &
                (open_day_df["delta"] < cfg.delta_max)
            ]
            otm_p = open_day_df[
                (open_day_df["option_type"] == "P") &
                (open_day_df["moneyness"] < 1.0) &
                (open_day_df["delta"] < 0) &
                (open_day_df["delta"].abs() < cfg.delta_max)
            ]
            call_iv = otm_c["implied_vol"].mean() if not otm_c.empty else 0
            put_iv = otm_p["implied_vol"].mean() if not otm_p.empty else 0
            actual_direction = "put" if put_iv > call_iv else "call"

        if cfg.select_mode == "deepest_otm":
            # 乐得模式：选最深度虚值的有流动性合约
            selected = []

            # OTM Call（仅当 sell_direction 为 "both" 或 "call" 时）
            if actual_direction in ("both", "call"):
                otm_calls = open_day_df[
                    (open_day_df["option_type"] == "C") &
                    (open_day_df["moneyness"] > 1.0) &
                    (open_day_df["delta"] > 0) &
                    (open_day_df["delta"] < cfg.delta_max)
                ]
                if not otm_calls.empty:
                    best = otm_calls.loc[otm_calls["moneyness"].idxmax()]
                    selected.append(best)

            # OTM Put（仅当 sell_direction 为 "both" 或 "put" 时）
            if actual_direction in ("both", "put"):
                otm_puts = open_day_df[
                    (open_day_df["option_type"] == "P") &
                    (open_day_df["moneyness"] < 1.0) &
                    (open_day_df["delta"] < 0) &
                    (open_day_df["delta"].abs() < cfg.delta_max)
                ]
                if not otm_puts.empty:
                    best = otm_puts.loc[otm_puts["moneyness"].idxmin()]
                    selected.append(best)

        else:
            # target_delta模式：选delta最接近目标的
            delta_lo = cfg.target_delta - cfg.delta_tolerance
            delta_hi = cfg.target_delta + cfg.delta_tolerance
            selected = []

            if actual_direction in ("both", "call"):
                otm_calls = open_day_df[
                    (open_day_df["option_type"] == "C") &
                    (open_day_df["delta"] >= delta_lo) &
                    (open_day_df["delta"] <= delta_hi) &
                    (open_day_df["moneyness"] > 1.0)
                ]
                if not otm_calls.empty:
                    best = otm_calls.iloc[(otm_calls["delta"] - cfg.target_delta).abs().argmin()]
                    selected.append(best)

            if actual_direction in ("both", "put"):
                otm_puts = open_day_df[
                    (open_day_df["option_type"] == "P") &
                    (open_day_df["delta"].abs() >= delta_lo) &
                    (open_day_df["delta"].abs() <= delta_hi) &
                    (open_day_df["moneyness"] < 1.0)
                ]
                if not otm_puts.empty:
                    best = otm_puts.iloc[(otm_puts["delta"].abs() - cfg.target_delta).abs().argmin()]
                    selected.append(best)

        # 计算手数（基于配置的sizing_mode）
        # 确定腿数：单方向=1腿，双卖=2腿
        n_legs = len(selected) if len(selected) > 0 else 1

        for contract in selected:
            option_code = contract["option_code"]
            contract_vega = abs(contract.get("vega", 0)) if "vega" in contract.index else 0

            if cfg.sizing_mode == "margin":
                # 保证金模式：target_margin_rate是该品种的总保证金预算，按腿数分
                margin_per = estimate_margin(
                    contract["spot_close"], contract["strike"],
                    contract["option_type"], contract["option_close"],
                    multiplier, cfg.margin_ratio, cfg.min_guarantee
                )
                if margin_per > 0:
                    n_contracts = int(cfg.target_margin_rate / n_legs * cfg.initial_capital / margin_per)
                else:
                    n_contracts = 1

                # 同时检查Vega约束（不超过vega_budget，也按腿数分）
                if contract_vega > 0 and multiplier > 0:
                    n_by_vega = int(cfg.vega_budget / n_legs * cfg.initial_capital
                                    / (contract_vega * multiplier))
                    n_contracts = min(n_contracts, n_by_vega)

            elif cfg.sizing_mode == "vega":
                # 纯Vega预算模式
                if contract_vega > 0 and multiplier > 0:
                    n_contracts = int(cfg.vega_budget * 0.5 * cfg.initial_capital
                                      / (contract_vega * multiplier))
                else:
                    n_contracts = 99999
                # 杠杆率约束
                notional_per = contract["strike"] * multiplier
                if notional_per > 0:
                    n_by_leverage = int(cfg.max_leverage * 0.5 * cfg.initial_capital / notional_per)
                    n_contracts = min(n_contracts, n_by_leverage)

            else:  # leverage mode
                notional_per = contract["strike"] * multiplier
                if notional_per > 0:
                    n_contracts = int(cfg.max_leverage * 0.5 * cfg.initial_capital / notional_per)
                else:
                    n_contracts = 1

            n_contracts = max(1, min(n_contracts, cfg.max_contracts_per_leg))

            # 记录保证金信息
            margin_per = estimate_margin(
                contract["spot_close"], contract["strike"],
                contract["option_type"], contract["option_close"],
                multiplier, cfg.margin_ratio, cfg.min_guarantee
            )
            total_margin = margin_per * n_contracts
            option_code = contract["option_code"]

            # 找平仓日：该合约最后一个DTE >= exit_dte的交易日
            contract_data = exp_df[exp_df["option_code"] == option_code].sort_values("trade_date")
            close_candidates = contract_data[contract_data["dte"] >= cfg.exit_dte]
            if close_candidates.empty:
                close_candidates = contract_data

            if close_candidates.empty:
                continue

            close_row = close_candidates.iloc[-1]

            premium = contract["option_close"]
            close_price = close_row["option_close"]
            pnl_per_unit = premium - close_price
            fee = cfg.fee_per_contract * 2 / multiplier
            pnl_per_unit -= fee
            total_pnl = pnl_per_unit * multiplier * n_contracts

            trades.append({
                "open_date": str(contract["trade_date"].date()),
                "close_date": str(close_row["trade_date"].date()),
                "expiry_date": str(expiry.date()) if hasattr(expiry, 'date') else str(expiry),
                "option_code": option_code,
                "option_type": contract["option_type"],
                "strike": contract["strike"],
                "spot_at_open": contract["spot_close"],
                "spot_at_close": close_row["spot_close"],
                "premium": premium,
                "close_price": close_price,
                "pnl_per_unit": pnl_per_unit,
                "n_contracts": n_contracts,
                "pnl": total_pnl,
                "delta_at_open": contract["delta"],
                "iv_at_open": contract["implied_vol"],
                "dte_at_open": contract["dte"],
                "moneyness": contract["moneyness"],
                "margin_per_contract": margin_per,
                "total_margin": total_margin,
                "margin_rate": total_margin / cfg.initial_capital,
            })

    return pd.DataFrame(trades)


def calc_stats(trades_df, initial_capital=10_000_000):
    """计算回测统计"""
    if trades_df.empty:
        return {}

    df = trades_df.sort_values("open_date").copy()
    df["cum_pnl"] = df["pnl"].cumsum()

    total_pnl = df["pnl"].sum()
    total_return = total_pnl / initial_capital

    first_date = pd.to_datetime(df["open_date"].iloc[0])
    last_date = pd.to_datetime(df["close_date"].iloc[-1])
    years = max((last_date - first_date).days / 365.25, 0.5)

    ann_return = (1 + total_return) ** (1 / years) - 1

    # 月度PnL
    df["month"] = pd.to_datetime(df["open_date"]).dt.to_period("M")
    monthly_pnl = df.groupby("month")["pnl"].sum()
    monthly_ret = monthly_pnl / initial_capital

    # 最大回撤
    nav = initial_capital + df["cum_pnl"]
    nav_series = pd.concat([pd.Series([initial_capital]), nav]).reset_index(drop=True)
    running_max = nav_series.cummax()
    drawdown = (nav_series - running_max) / running_max
    max_dd = drawdown.min()

    # 胜率
    win_rate = (df["pnl"] > 0).mean()
    avg_win = df[df["pnl"] > 0]["pnl"].mean() if (df["pnl"] > 0).any() else 0
    avg_loss = abs(df[df["pnl"] <= 0]["pnl"].mean()) if (df["pnl"] <= 0).any() else 1
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    monthly_win = (monthly_ret > 0).mean() if len(monthly_ret) > 0 else 0
    ann_vol = monthly_ret.std() * np.sqrt(12) if len(monthly_ret) > 1 else 0
    sharpe = (ann_return - 0.02) / ann_vol if ann_vol > 0 else 0
    skew = monthly_ret.skew() if len(monthly_ret) > 2 else 0
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    return {
        "trades": len(df), "years": round(years, 1),
        "total_return": total_return, "ann_return": ann_return,
        "ann_vol": ann_vol, "max_dd": max_dd,
        "sharpe": sharpe, "calmar": calmar,
        "win_rate": win_rate, "pl_ratio": pl_ratio,
        "monthly_win": monthly_win, "skew": skew,
        "first": str(first_date.date()), "last": str(last_date.date()),
    }


def print_stats(stats, name=""):
    """
    参数: stats, name
    """
    if not stats:
        print(f"  {name}: 无交易")
        return
    print(f"  {name:12s} | {stats['first']}~{stats['last']} | "
          f"{stats['trades']:3d}笔 | "
          f"年化{stats['ann_return']:+6.1%} | "
          f"回撤{stats['max_dd']:6.1%} | "
          f"夏普{stats['sharpe']:5.2f} | "
          f"胜率{stats['win_rate']:4.0%} | "
          f"偏度{stats['skew']:+5.2f}")

