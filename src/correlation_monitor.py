"""
品种相关性监控模块

功能：
1. 计算品种间标的收益率相关性矩阵
2. 板块集中度分析
3. 有效分散度计算
4. 尾部相关性（极端行情下的条件相关性）
5. 相关性变化趋势监控

用法：
    # 独立运行：生成相关性报告
    python 分钟级数据回测/src/correlation_monitor.py

    # 在回测引擎中调用
    from correlation_monitor import CorrelationMonitor
    cm = CorrelationMonitor(pdata)
    report = cm.daily_check(date, positions)
"""
import sys
import os
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from collections import defaultdict

from backtest_fast import load_product_data
from exp_product_count import scan_and_rank, EXCHANGE_OF

DB_PATH = os.environ.get("OPTION_DB_PATH", "benchmark.db")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

# 板块分类
SECTOR_MAP = {
    "豆粕": "农产品", "玉米": "农产品", "鸡蛋": "农产品", "豆一": "农产品",
    "豆油": "油脂", "棕榈油": "油脂", "菜油": "油脂",
    "淀粉": "农产品", "生猪": "农产品", "豆二": "农产品",
    "铁矿石": "黑色", "螺纹钢": "黑色", "焦煤": "黑色",
    "沪铜": "有色", "沪铝": "有色", "沪锌": "有色", "沪镍": "有色",
    "沪锡": "有色", "氧化铝": "有色",
    "沪金": "贵金属", "沪银": "贵金属",
    "原油": "能源",
    "PVC": "化工", "PP": "化工", "塑料": "化工", "苯乙烯": "化工",
    "乙二醇": "化工", "PTA": "化工", "甲醇": "化工", "纯碱": "化工",
    "玻璃": "化工", "LPG": "化工",
    "白糖": "软商品", "棉花": "软商品",
    "中证1000": "金融", "沪深300": "金融", "上证50": "金融",
    "工业硅": "新材料", "碳酸锂": "新材料",
    "天然橡胶": "化工", "丁二烯橡胶": "化工",
    # ETF期权
    "300ETF沪": "金融", "50ETF": "金融", "500ETF沪": "金融",
    "科创50ETF华夏": "金融", "300ETF深": "金融", "创业板ETF": "金融",
    # 其他商品
    "烧碱": "化工", "尿素": "化工", "菜粕": "农产品",
    "花生": "农产品", "苹果": "农产品", "红枣": "农产品",
    "对二甲苯": "化工", "短纤": "化工",
    "锰硅": "黑色", "硅铁": "黑色",
}


class CorrelationMonitor:
    """品种相关性监控"""

    def __init__(self, pdata, window=60):
        """
        Args:
            pdata: dict[name] -> {"df": DataFrame, ...} 品种数据
            window: 滚动相关性窗口（交易日）
        """
        self.window = window
        self.spot_returns = self._build_spot_returns(pdata)
        self._corr_cache = {}

    def _build_spot_returns(self, pdata):
        """从品种数据中提取标的日收益率序列"""
        spot_series = {}
        for name, d in pdata.items():
            df = d["df"]
            # 每日取一个spot_close（去重）
            daily_spot = df.groupby("trade_date")["spot_close"].first()
            daily_spot = daily_spot.sort_index()
            daily_spot = daily_spot[daily_spot > 0]
            if len(daily_spot) > 20:
                spot_series[name] = daily_spot.pct_change().dropna()

        if not spot_series:
            return pd.DataFrame()
        return pd.DataFrame(spot_series)

    def correlation_matrix(self, end_date=None, window=None):
        """
        计算截止end_date的滚动相关性矩阵

        Returns:
            pd.DataFrame: N×N相关性矩阵
        """
        w = window or self.window
        df = self.spot_returns
        if end_date is not None:
            df = df[df.index <= end_date]
        if len(df) < w:
            df_window = df
        else:
            df_window = df.iloc[-w:]
        return df_window.corr()

    def high_correlation_pairs(self, end_date=None, threshold=0.7):
        """
        找出相关系数>threshold的品种对

        Returns:
            list of (name1, name2, corr)
        """
        corr = self.correlation_matrix(end_date)
        pairs = []
        names = corr.columns.tolist()
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                c = corr.iloc[i, j]
                if abs(c) > threshold:
                    pairs.append((names[i], names[j], round(c, 3)))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        return pairs

    def sector_concentration(self, positions):
        """
        计算板块集中度（基于保证金权重）

        Args:
            positions: list of Position objects

        Returns:
            dict[sector] -> margin_pct
        """
        sector_margin = defaultdict(float)
        total_margin = 0
        for pos in positions:
            if pos.role != "sell":
                continue
            m = pos.cur_margin()
            sector = SECTOR_MAP.get(pos.product, "其他")
            sector_margin[sector] += m
            total_margin += m

        if total_margin == 0:
            return {}
        return {s: round(m / total_margin, 4) for s, m in
                sorted(sector_margin.items(), key=lambda x: -x[1])}

    def effective_diversification(self, positions):
        """
        计算有效分散度 = 1 / HHI（基于保证金权重）

        HHI = Σ(weight_i²)
        有效品种数 = 1/HHI

        20品种均匀分配 → 有效品种数=20
        1品种占100% → 有效品种数=1
        """
        product_margin = defaultdict(float)
        total_margin = 0
        for pos in positions:
            if pos.role != "sell":
                continue
            m = pos.cur_margin()
            product_margin[pos.product] += m
            total_margin += m

        if total_margin == 0:
            return 0

        hhi = sum((m / total_margin) ** 2 for m in product_margin.values())
        return round(1 / hhi, 1) if hhi > 0 else 0

    def tail_correlation(self, end_date=None, threshold_pct=-2.0):
        """
        尾部相关性：标的日跌幅>threshold时的条件相关性

        Args:
            threshold_pct: 跌幅阈值（如-2.0表示日跌幅>2%）

        Returns:
            pd.DataFrame: 条件相关性矩阵
        """
        df = self.spot_returns
        if end_date is not None:
            df = df[df.index <= end_date]

        # 找出任一品种跌幅>threshold的日期
        stress_mask = (df < threshold_pct / 100).any(axis=1)
        stress_days = df[stress_mask]

        if len(stress_days) < 10:
            return pd.DataFrame()  # 样本不足

        return stress_days.corr()

    def daily_check(self, date, positions, nav):
        """
        每日相关性检查，返回预警信息

        Returns:
            dict with keys: high_pairs, sector_conc, effective_n, warnings
        """
        result = {
            "date": str(date)[:10],
            "high_pairs": [],
            "sector_concentration": {},
            "effective_n": 0,
            "warnings": [],
        }

        # 高相关品种对
        pairs = self.high_correlation_pairs(date, threshold=0.7)
        result["high_pairs"] = pairs
        if pairs:
            result["warnings"].append(
                f"高相关品种对({len(pairs)}组): " +
                ", ".join(f"{a}-{b}({c:.2f})" for a, b, c in pairs[:3]))

        # 板块集中度
        sc = self.sector_concentration(positions)
        result["sector_concentration"] = sc
        for sector, pct in sc.items():
            if pct > 0.5:
                result["warnings"].append(f"板块集中度过高: {sector} {pct:.0%}")

        # 有效分散度
        eff_n = self.effective_diversification(positions)
        result["effective_n"] = eff_n
        if eff_n < 8:
            result["warnings"].append(f"有效分散度不足: {eff_n:.1f}（建议>8）")

        return result


def generate_report(db_path=DB_PATH):
    """独立运行：生成完整的相关性分析报告"""
    print("=" * 60)
    print("  品种相关性分析报告")
    print("=" * 60)

    # 加载数据
    ranked = scan_and_rank(sort_by="oi")
    top20 = ranked[:20]

    conn = sqlite3.connect(db_path)
    pdata = {}
    for r in top20:
        where, name, mult, mr, liq = r["product_tuple"]
        df = load_product_data(conn, where)
        if not df.empty:
            pdata[name] = {"df": df, "mult": mult, "mr": mr, "liq": liq}
    conn.close()

    print(f"\n品种数: {len(pdata)}")

    cm = CorrelationMonitor(pdata, window=60)

    # 1. 相关性矩阵
    corr = cm.correlation_matrix()
    print(f"\n一、60日滚动相关性矩阵（{len(corr)}×{len(corr)}）")
    print(f"  平均相关系数: {corr.values[np.triu_indices_from(corr.values, k=1)].mean():.3f}")

    # 2. 高相关品种对
    pairs = cm.high_correlation_pairs(threshold=0.6)
    print(f"\n二、高相关品种对（>0.6）: {len(pairs)}组")
    for a, b, c in pairs[:15]:
        sa = SECTOR_MAP.get(a, "?")
        sb = SECTOR_MAP.get(b, "?")
        print(f"  {a}({sa}) - {b}({sb}): {c:.3f}")

    # 3. 板块内平均相关性
    print(f"\n三、板块内平均相关性")
    sectors = defaultdict(list)
    for name in corr.columns:
        sectors[SECTOR_MAP.get(name, "其他")].append(name)

    for sector, names in sorted(sectors.items()):
        if len(names) < 2:
            continue
        sub_corr = corr.loc[names, names]
        avg = sub_corr.values[np.triu_indices_from(sub_corr.values, k=1)].mean()
        print(f"  {sector}({len(names)}品种): 平均相关性 {avg:.3f}  品种: {', '.join(names)}")

    # 4. 尾部相关性
    tail = cm.tail_correlation(threshold_pct=-2.0)
    if not tail.empty:
        avg_tail = tail.values[np.triu_indices_from(tail.values, k=1)].mean()
        avg_normal = corr.values[np.triu_indices_from(corr.values, k=1)].mean()
        print(f"\n四、尾部相关性（日跌幅>2%时）")
        print(f"  正常时期平均相关性: {avg_normal:.3f}")
        print(f"  尾部时期平均相关性: {avg_tail:.3f}")
        print(f"  尾部相关性放大倍数: {avg_tail/avg_normal:.1f}x" if avg_normal > 0 else "")

    # 保存
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    corr.to_csv(os.path.join(OUTPUT_DIR, "correlation_matrix.csv"))
    print(f"\n相关性矩阵已保存: output/correlation_matrix.csv")


if __name__ == "__main__":
    generate_report()
