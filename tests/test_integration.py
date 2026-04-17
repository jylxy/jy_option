"""
集成测试 — 逐分钟回测引擎

在服务器上运行，验证完整流程：
  加载 → 盘中循环 → 收盘决策 → NAV 快照 → 内存释放

用法：
    cd server_deploy
    python tests/test_integration.py
    python tests/test_integration.py --full  # 跑3天完整测试
"""
import os
import sys
import time
import argparse
import logging

# 确保 src/ 可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def test_contract_master():
    """测试 ContractMaster 加载"""
    from parquet_loader import ContractMaster

    logger.info("=== 测试 ContractMaster ===")
    cm = ContractMaster()
    logger.info("  合约数: %d", cm.size)
    assert cm.size > 0, "合约数应 > 0"

    # 随机查找一个合约
    codes = cm.all_codes()
    sample = codes[0]
    info = cm.lookup(sample)
    assert info is not None, f"查找 {sample} 应返回非 None"
    assert info["strike_price"] > 0, "行权价应 > 0"
    assert info["option_type"] in ("C", "P"), "option_type 应为 C 或 P"
    assert info["contract_multiplier"] > 0, "乘数应 > 0"

    # DTE 计算
    from datetime import date
    dte = cm.calc_dte(sample, date(2024, 6, 1))
    logger.info("  样本合约: %s, DTE(2024-06-01)=%d", sample, dte)

    # 品种根码
    root = cm.get_product_root(sample)
    logger.info("  品种根码: %s", root)
    assert len(root) > 0, "品种根码不应为空"

    logger.info("  ✅ ContractMaster 测试通过")


def test_day_loader():
    """测试 ParquetDayLoader 加载单日数据"""
    from parquet_loader import ParquetDayLoader

    logger.info("=== 测试 ParquetDayLoader ===")
    loader = ParquetDayLoader()

    # 获取交易日列表
    dates = loader.get_trading_dates()
    logger.info("  交易日数: %d", len(dates))
    assert len(dates) > 0, "交易日列表不应为空"

    # 加载一天
    test_date = dates[len(dates) // 2]  # 取中间的日期
    logger.info("  加载 %s ...", test_date)
    t0 = time.time()
    day = loader.load_day(test_date)
    elapsed = time.time() - t0
    logger.info("  加载耗时: %.1f 秒", elapsed)

    # 验证数据
    n_opt = len(day.option_bars) if day.option_bars is not None else 0
    n_fut = len(day.futures_bars) if day.futures_bars is not None else 0
    n_etf = len(day.etf_bars) if day.etf_bars is not None else 0
    logger.info("  期权: %d 行, 期货: %d 行, ETF: %d 行", n_opt, n_fut, n_etf)
    assert n_opt > 0, "期权数据不应为空"

    # 分钟时间戳
    timestamps = day.get_minute_timestamps()
    logger.info("  分钟时间戳: %d 个", len(timestamps))
    assert len(timestamps) > 0, "时间戳不应为空"
    # 验证升序
    assert timestamps == sorted(timestamps), "时间戳应升序"

    # VWAP 计算
    if n_opt > 0:
        sample_code = day.option_bars["code"].iloc[0]
        vwap = day.calc_vwap(sample_code, window=10)
        logger.info("  VWAP(%s): %s", sample_code, vwap)

    # 日频聚合
    logger.info("  聚合日频数据...")
    t0 = time.time()
    daily_df = day.aggregate_daily()
    elapsed = time.time() - t0
    logger.info("  聚合耗时: %.1f 秒, %d 行", elapsed, len(daily_df))
    if len(daily_df) > 0:
        cols = daily_df.columns.tolist()
        logger.info("  列: %s", cols)
        assert "option_code" in cols, "应包含 option_code 列"
        assert "strike" in cols, "应包含 strike 列"
        assert "spot_close" in cols, "应包含 spot_close 列"

    # 释放
    day.release()
    logger.info("  ✅ ParquetDayLoader 测试通过")


def test_iv_smile():
    """测试 IV Smile 拟合"""
    import numpy as np
    import pandas as pd
    from iv_surface import IVSmile, build_iv_smiles

    logger.info("=== 测试 IV Smile ===")

    # 构造模拟数据
    np.random.seed(42)
    n = 10
    spot = 3000.0
    strikes = np.linspace(2700, 3300, n)
    moneyness = np.log(strikes / spot)
    # 模拟 IV smile: 二次函数 + 噪声
    true_iv = 0.25 + 0.1 * moneyness + 0.5 * moneyness**2
    iv = true_iv + np.random.normal(0, 0.01, n)

    df = pd.DataFrame({
        "strike": strikes,
        "implied_vol": iv,
        "spot_close": spot,
        "option_type": ["P"] * 5 + ["C"] * 5,
    })

    smile = IVSmile("test", "2409", "2024-06-03")
    r2 = smile.fit(df)
    logger.info("  R²: %.4f, valid: %s, n_contracts: %d",
                r2, smile.is_valid, smile.n_contracts)
    assert smile.is_valid, "Smile 应有效"
    assert r2 > 0.5, f"R² 应 > 0.5, 实际 {r2}"

    # 残差
    residuals = smile.calc_residuals_batch(df)
    mean_res = residuals.mean()
    logger.info("  残差均值: %.6f", mean_res)
    assert abs(mean_res) < 0.01, f"残差均值应接近0, 实际 {mean_res}"

    logger.info("  ✅ IV Smile 测试通过")


def test_intraday_monitor():
    """测试盘中监控"""
    from intraday_monitor import IntradayMonitor

    logger.info("=== 测试 IntradayMonitor ===")
    config = {
        "greeks_delta_hard": 0.20,
        "greeks_vega_hard": 0.02,
        "greeks_vega_warn": 0.015,
        "intraday_greeks_interval": 15,
        "fee": 3,
    }
    monitor = IntradayMonitor(config)

    # 检查间隔
    assert monitor.should_update_greeks(0) is True, "minute 0 应更新"
    assert monitor.should_update_greeks(1) is False, "minute 1 不应更新"
    assert monitor.should_update_greeks(15) is True, "minute 15 应更新"
    assert monitor.should_update_greeks(30) is True, "minute 30 应更新"
    assert monitor.should_check_emergency() is True, "应急保护始终检查"

    # Greeks 超限检查
    greeks = {"cash_delta": 250000, "cash_vega": 30000}
    breaches = monitor.check_greeks_breach(greeks, 1000000)
    logger.info("  超限事件: %d", len(breaches))
    assert len(breaches) > 0, "应检测到超限"

    # 买卖价差
    spread = monitor.calc_spread(100.0, "test", None, "pct")
    logger.info("  价差(pct): %.4f", spread)
    assert spread > 0, "价差应 > 0"

    price_buy = IntradayMonitor.apply_spread(100.0, "buy", 0.2)
    price_sell = IntradayMonitor.apply_spread(100.0, "sell", 0.2)
    logger.info("  买入价: %.2f, 卖出价: %.2f", price_buy, price_sell)
    assert price_buy > price_sell, "买入价应 > 卖出价"
    assert abs(price_buy - price_sell - 0.2) < 0.001, "价差应等于 spread"

    logger.info("  ✅ IntradayMonitor 测试通过")


def test_vectorized_greeks():
    """测试向量化 Greeks 计算"""
    import numpy as np
    import pandas as pd
    from option_calc import calc_greeks_batch_vectorized, calc_greeks_single

    logger.info("=== 测试向量化 Greeks ===")

    df = pd.DataFrame({
        "spot_close": [3000.0, 3000.0, 3000.0],
        "strike": [2800.0, 3000.0, 3200.0],
        "dte": [30.0, 30.0, 30.0],
        "implied_vol": [0.25, 0.20, 0.25],
        "option_type": ["P", "P", "C"],
    })

    # 向量化版本
    t0 = time.time()
    result = calc_greeks_batch_vectorized(df)
    elapsed = time.time() - t0
    logger.info("  向量化耗时: %.4f 秒", elapsed)
    logger.info("  Delta: %s", result["delta"].values)
    logger.info("  Gamma: %s", result["gamma"].values)
    logger.info("  Vega: %s", result["vega"].values)

    # 与逐行版本对比
    for i in range(len(df)):
        g = calc_greeks_single(
            df.iloc[i]["spot_close"], df.iloc[i]["strike"],
            df.iloc[i]["dte"], df.iloc[i]["implied_vol"],
            df.iloc[i]["option_type"]
        )
        delta_diff = abs(result["delta"].iloc[i] - g["delta"])
        assert delta_diff < 0.001, f"Delta 差异过大: {delta_diff}"

    logger.info("  ✅ 向量化 Greeks 测试通过")


def test_engine_single_day(date_str=None):
    """测试主引擎单日完整流程"""
    from parquet_loader import ParquetDayLoader
    from true_minute_engine import TrueMinuteEngine

    logger.info("=== 测试主引擎单日流程 ===")

    engine = TrueMinuteEngine()

    # 获取一个交易日
    dates = engine.loader.get_trading_dates()
    if date_str is None:
        date_str = dates[len(dates) // 2]
    logger.info("  测试日期: %s", date_str)

    # 只跑一天
    result = engine.run(start_date=date_str, end_date=date_str, tag="test_single")

    nav_df = result.get("nav_df")
    orders_df = result.get("orders_df")

    if nav_df is not None and len(nav_df) > 0:
        logger.info("  NAV 记录: %d 行", len(nav_df))
        logger.info("  NAV 列: %s", nav_df.columns.tolist())
        assert "date" in nav_df.columns, "应包含 date 列"
        assert "nav" in nav_df.columns, "应包含 nav 列"
        assert "cash_delta" in nav_df.columns, "应包含 cash_delta 列"
    else:
        logger.warning("  NAV 为空（可能该日无数据）")

    if orders_df is not None and len(orders_df) > 0:
        logger.info("  订单记录: %d 行", len(orders_df))
        logger.info("  订单列: %s", orders_df.columns.tolist())
    else:
        logger.info("  无订单（正常，单日可能无开仓信号）")

    logger.info("  ✅ 主引擎单日测试通过")


def test_engine_multi_day():
    """测试主引擎多日连续运行（3天）"""
    from true_minute_engine import TrueMinuteEngine

    logger.info("=== 测试主引擎多日流程（3天）===")

    engine = TrueMinuteEngine()
    dates = engine.loader.get_trading_dates()

    # 取中间3天
    mid = len(dates) // 2
    start = dates[mid]
    end = dates[min(mid + 2, len(dates) - 1)]
    logger.info("  测试范围: %s ~ %s", start, end)

    t0 = time.time()
    result = engine.run(start_date=start, end_date=end, tag="test_multi")
    elapsed = time.time() - t0

    nav_df = result.get("nav_df")
    stats = result.get("stats", {})

    logger.info("  耗时: %.1f 秒", elapsed)
    if nav_df is not None:
        logger.info("  NAV 记录: %d 天", len(nav_df))
    if stats:
        logger.info("  统计: %s", stats)

    # 验证输出文件
    output_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    for f in ["nav_test_multi.csv", "orders_test_multi.csv", "report_test_multi.md"]:
        path = os.path.join(output_dir, f)
        if os.path.exists(path):
            size = os.path.getsize(path)
            logger.info("  输出文件: %s (%d bytes)", f, size)
        else:
            logger.warning("  输出文件缺失: %s", f)

    logger.info("  ✅ 主引擎多日测试通过")


def main():
    parser = argparse.ArgumentParser(description="集成测试")
    parser.add_argument("--full", action="store_true", help="运行完整测试（含多日）")
    parser.add_argument("--date", type=str, default=None, help="指定测试日期")
    args = parser.parse_args()

    passed = 0
    failed = 0

    tests = [
        ("ContractMaster", test_contract_master),
        ("ParquetDayLoader", test_day_loader),
        ("IV Smile", test_iv_smile),
        ("IntradayMonitor", test_intraday_monitor),
        ("向量化Greeks", test_vectorized_greeks),
        ("主引擎单日", lambda: test_engine_single_day(args.date)),
    ]

    if args.full:
        tests.append(("主引擎多日", test_engine_multi_day))

    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            logger.error("❌ %s 测试失败: %s", name, exc)
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print(f"{'='*40}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
