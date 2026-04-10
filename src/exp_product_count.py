"""
实验：品种数量对组合策略表现和容量的影响

按日均成交量从大到小排序品种，从1个品种到N个品种依次运行S1+S3+S4组合回测。
输出净值图、风险收益指标和容量估算。
"""
import sys
import os
import time
import gc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

from scan_all_liquidity import scan_liquidity, PRODUCT_SPECS, NAME_MAP, EXCHANGE_LIMITS, DEFAULT_COMMODITY_LIMIT
from unified_engine_v3 import run_unified_v3, stats, S4_PRODUCTS
from backtest_fast import estimate_margin

# ── 品种映射表：root -> (where_clause, name, mult, mr, liq_category) ──────────
# 复用 exp_expanded_products.py 中验证过的 WHERE 子句模式

PRODUCT_MAP = {
    # 金融
    'HS300':  ("underlying_code = 'HS300'",   "沪深300", 100, 0.10, "financial"),
    'CSI1000':("underlying_code = 'CSI1000'", "中证1000",100, 0.10, "financial"),
    'SSE50':  ("underlying_code = 'SSE50'",   "上证50",  100, 0.10, "financial"),
    # 大商所（单字母根码用数字后缀避免歧义）
    'm':  ("underlying_code LIKE 'm2%' OR underlying_code LIKE 'm26%' OR underlying_code LIKE 'm27%'", "豆粕", 10, 0.05, "commodity_low"),
    'i':  ("underlying_code LIKE 'i2%' OR underlying_code LIKE 'i26%' OR underlying_code LIKE 'i27%'", "铁矿石", 100, 0.05, "commodity_high"),
    'c':  ("underlying_code LIKE 'c2%' OR underlying_code LIKE 'c26%' OR underlying_code LIKE 'c27%'", "玉米", 10, 0.05, "commodity_low"),
    'a':  ("underlying_code LIKE 'a2%' OR underlying_code LIKE 'a26%' OR underlying_code LIKE 'a27%'", "豆一", 10, 0.05, "commodity_low"),
    'b':  ("underlying_code LIKE 'b2%' OR underlying_code LIKE 'b26%' OR underlying_code LIKE 'b27%'", "豆二", 10, 0.05, "commodity_low"),
    'p':  ("underlying_code LIKE 'p2%' OR underlying_code LIKE 'p26%' OR underlying_code LIKE 'p27%'", "棕榈油", 10, 0.05, "commodity_low"),
    'v':  ("underlying_code LIKE 'v2%' OR underlying_code LIKE 'v26%' OR underlying_code LIKE 'v27%'", "PVC", 5, 0.05, "commodity_low"),
    'y':  ("underlying_code LIKE 'y2%' OR underlying_code LIKE 'y26%' OR underlying_code LIKE 'y27%'", "豆油", 10, 0.05, "commodity_low"),
    'l':  ("underlying_code LIKE 'l2%' OR underlying_code LIKE 'l26%' OR underlying_code LIKE 'l27%'", "塑料", 5, 0.05, "commodity_low"),
    'pp': ("underlying_code LIKE 'pp2%' OR underlying_code LIKE 'pp26%' OR underlying_code LIKE 'pp27%'", "PP", 5, 0.05, "commodity_low"),
    'eb': ("underlying_code LIKE 'eb2%' OR underlying_code LIKE 'eb26%' OR underlying_code LIKE 'eb27%'", "苯乙烯", 5, 0.05, "commodity_low"),
    'eg': ("underlying_code LIKE 'eg2%' OR underlying_code LIKE 'eg26%' OR underlying_code LIKE 'eg27%'", "乙二醇", 10, 0.05, "commodity_low"),
    'pg': ("underlying_code LIKE 'pg2%' OR underlying_code LIKE 'pg26%' OR underlying_code LIKE 'pg27%'", "LPG", 20, 0.05, "commodity_low"),
    'jd': ("underlying_code LIKE 'jd2%' OR underlying_code LIKE 'jd26%' OR underlying_code LIKE 'jd27%'", "鸡蛋", 5, 0.05, "commodity_low"),
    'lh': ("underlying_code LIKE 'lh2%' OR underlying_code LIKE 'lh26%' OR underlying_code LIKE 'lh27%'", "生猪", 16, 0.05, "commodity_low"),
    'jm': ("underlying_code LIKE 'jm2%' OR underlying_code LIKE 'jm26%' OR underlying_code LIKE 'jm27%'", "焦煤", 60, 0.05, "commodity_high"),
    'cs': ("underlying_code LIKE 'cs2%' OR underlying_code LIKE 'cs26%' OR underlying_code LIKE 'cs27%'", "淀粉", 10, 0.05, "commodity_low"),
    # 上期所
    'au': ("underlying_code LIKE 'au2%' OR underlying_code LIKE 'au26%'", "沪金", 1000, 0.05, "commodity_high"),
    'ag': ("underlying_code LIKE 'ag2%' OR underlying_code LIKE 'ag26%'", "沪银", 15, 0.05, "commodity_high"),
    'cu': ("underlying_code LIKE 'cu2%' OR underlying_code LIKE 'cu26%'", "沪铜", 5, 0.05, "commodity_high"),
    'al': ("underlying_code LIKE 'al2%' OR underlying_code LIKE 'al26%'", "沪铝", 5, 0.05, "commodity_low"),
    'rb': ("underlying_code LIKE 'rb2%' OR underlying_code LIKE 'rb26%'", "螺纹钢", 10, 0.05, "commodity_low"),
    'ru': ("underlying_code LIKE 'ru2%' OR underlying_code LIKE 'ru26%'", "天然橡胶", 10, 0.05, "commodity_low"),
    'ni': ("underlying_code LIKE 'ni2%' OR underlying_code LIKE 'ni26%'", "沪镍", 1, 0.05, "commodity_high"),
    'zn': ("underlying_code LIKE 'zn2%' OR underlying_code LIKE 'zn26%'", "沪锌", 5, 0.05, "commodity_low"),
    'sn': ("underlying_code LIKE 'sn2%' OR underlying_code LIKE 'sn26%'", "沪锡", 1, 0.05, "commodity_high"),
    'ao': ("underlying_code LIKE 'ao2%' OR underlying_code LIKE 'ao26%'", "氧化铝", 20, 0.05, "commodity_low"),
    'br': ("underlying_code LIKE 'br2%' OR underlying_code LIKE 'br26%'", "丁二烯橡胶", 5, 0.05, "commodity_low"),
    # 上期能源
    'sc': ("underlying_code LIKE 'sc2%' OR underlying_code LIKE 'sc26%'", "原油", 1000, 0.07, "commodity_high"),
    # 郑商所（大写根码，不存在歧义）
    'SA': ("underlying_code LIKE 'SA%'", "纯碱", 20, 0.06, "commodity_low"),
    'TA': ("underlying_code LIKE 'TA%'", "PTA", 5, 0.06, "commodity_low"),
    'MA': ("underlying_code LIKE 'MA%'", "甲醇", 10, 0.06, "commodity_low"),
    'FG': ("underlying_code LIKE 'FG%'", "玻璃", 20, 0.06, "commodity_low"),
    'SR': ("underlying_code LIKE 'SR%'", "白糖", 10, 0.06, "commodity_low"),
    'CF': ("underlying_code LIKE 'CF%'", "棉花", 5, 0.06, "commodity_low"),
    'SM': ("underlying_code LIKE 'SM%'", "锰硅", 5, 0.06, "commodity_low"),
    'SF': ("underlying_code LIKE 'SF%'", "硅铁", 5, 0.06, "commodity_low"),
    'SH': ("underlying_code LIKE 'SH%'", "烧碱", 30, 0.06, "commodity_low"),
    'UR': ("underlying_code LIKE 'UR%'", "尿素", 20, 0.06, "commodity_low"),
    'RM': ("underlying_code LIKE 'RM%'", "菜粕", 10, 0.06, "commodity_low"),
    'OI': ("underlying_code LIKE 'OI%'", "菜油", 10, 0.06, "commodity_low"),
    'PK': ("underlying_code LIKE 'PK%'", "花生", 5, 0.06, "commodity_low"),
    'AP': ("underlying_code LIKE 'AP%'", "苹果", 10, 0.06, "commodity_low"),
    'CJ': ("underlying_code LIKE 'CJ%'", "红枣", 5, 0.06, "commodity_low"),
    'PX': ("underlying_code LIKE 'PX%'", "对二甲苯", 5, 0.06, "commodity_low"),
    'PF': ("underlying_code LIKE 'PF%'", "短纤", 5, 0.06, "commodity_low"),
    # 广期所
    'si': ("underlying_code LIKE 'si2%' OR underlying_code LIKE 'si26%'", "工业硅", 5, 0.05, "commodity_low"),
    'lc': ("underlying_code LIKE 'lc2%' OR underlying_code LIKE 'lc26%'", "碳酸锂", 1, 0.05, "commodity_high"),
}


def estimate_practical_capacity(pool, margin_per=0.02, s1_max_hands=20, s3_sell_limit=30,
                                target_margin_pct=0.50, relaxed=False):
    """
    私募实际容量估算

    逻辑：不是"找到0品种超限的AUM"，而是：
    1. 计算每品种在流动性约束下的最大可承载保证金
    2. S1: 每品种每方向 sell_hands + protect_hands/2，sell_hands = min(理论手数, 手数上限, 流动性上限)
    3. S3: 每品种每方向 buy+sell+protect 结构
    4. 加总所有品种可用保证金 → 反推最大AUM

    参数:
        pool: scan_liquidity() 返回的品种列表
        relaxed: True=私募可接受假设(10%持仓/20%成交)，False=保守假设(5%/10%)
    返回:
        max_aum (元), detail_dict
    """
    total_s1_margin = 0
    total_s3_margin = 0
    details = []

    for r in pool:
        root = r['root']
        mult, mr = PRODUCT_SPECS.get(root, (10, 0.05))
        avg_spot = r['avg_spot']
        margin_per_hand = estimate_margin(avg_spot, avg_spot * 0.9, 'P', avg_spot * 0.01, mult, mr, 0.5)
        if margin_per_hand <= 0:
            continue

        # 流动性上限
        if relaxed:
            # 私募可接受：10%持仓, 20%成交
            min_oi = min(r['put_oi'], r['call_oi']) if r['call_oi'] > 0 else r['put_oi']
            min_vol = min(r['put_vol'], r['call_vol']) if r['call_vol'] > 0 else r['put_vol']
            oi_limit = int(min_oi * 0.10)
            vol_limit = int(min_vol * 0.20)
        else:
            oi_limit = int(min(r['put_oi'], r['call_oi']) * 0.05) if r['call_oi'] > 0 else int(r['put_oi'] * 0.05)
            vol_limit = int(min(r['put_vol'], r['call_vol']) * 0.10) if r['call_vol'] > 0 else int(r['put_vol'] * 0.10)
        exch_limit = EXCHANGE_LIMITS.get(root, DEFAULT_COMMODITY_LIMIT)
        liq_limit = min(exch_limit, oi_limit, vol_limit)

        # S1: 每方向卖腿手数，受手数上限和流动性双重约束，双卖=2方向
        s1_hands_per_dir = min(s1_max_hands, liq_limit)
        s1_margin_product = s1_hands_per_dir * margin_per_hand * 2  # 双卖

        # S3: 卖腿手数受限，买1:卖3结构
        s3_sell_per_dir = min(s3_sell_limit, liq_limit)
        s3_margin_product = s3_sell_per_dir * margin_per_hand * 2  # 双卖

        total_s1_margin += s1_margin_product
        total_s3_margin += s3_margin_product

        details.append({
            'name': r['name'], 'root': root,
            'liq_limit': liq_limit,
            's1_hands': s1_hands_per_dir, 's3_sell': s3_sell_per_dir,
            's1_margin': s1_margin_product,
            's3_margin': s3_margin_product,
            'margin_per_hand': margin_per_hand,
        })

    # 组合保证金 = S1 + S3
    total_margin = total_s1_margin + total_s3_margin

    # AUM = total_margin / target_margin_pct
    # 即：策略能提供的保证金 / 目标保证金利用率 = 最大可管理规模
    max_aum = total_margin / target_margin_pct if target_margin_pct > 0 else 0

    return max_aum, {
        's1_margin': total_s1_margin,
        's3_margin': total_s3_margin,
        'total_margin': total_margin,
        'details': details,
    }


def scan_and_rank(sort_by="volume"):
    """扫描所有品种，按指定指标从大到小排序
    sort_by: "volume"=日均成交量, "oi"=日均持仓量
    """
    results = scan_liquidity()
    ranked = []
    for r in results:
        root = r['root']
        if root not in PRODUCT_MAP:
            continue
        total_vol = r['put_vol'] + r['call_vol']
        total_oi = r['put_oi'] + r['call_oi']
        where, name, mult, mr, liq = PRODUCT_MAP[root]
        ranked.append({
            'root': root,
            'name': name,
            'daily_vol': total_vol,
            'daily_oi': total_oi,
            'put_vol': r['put_vol'],
            'call_vol': r['call_vol'],
            'put_oi': r['put_oi'],
            'call_oi': r['call_oi'],
            'effective_limit': r['effective_limit'],
            'avg_spot': r['avg_spot'],
            'product_tuple': (where, name, mult, mr, liq),
        })
    if sort_by == "oi":
        ranked.sort(key=lambda x: x['daily_oi'], reverse=True)
    else:
        ranked.sort(key=lambda x: x['daily_vol'], reverse=True)
    return ranked


def run_experiment(ranked, n_values, start_date="2024-01-01"):
    """对每个 N 值运行回测，收集结果"""
    results = []
    for n in n_values:
        if n > len(ranked):
            break
        top_n = ranked[:n]
        product_list = [r['product_tuple'] for r in top_n]
        product_names = [r['name'] for r in top_n]

        # S4品种：只包含在 top_n 中且属于默认 S4 列表的
        s4_in_topn = [name for name in product_names if name in S4_PRODUCTS]

        t0 = time.time()
        print(f"\n{'='*80}")
        print(f"  N={n}: {', '.join(product_names[:8])}{'...' if n>8 else ''}")
        print(f"  S4品种: {s4_in_topn if s4_in_topn else '(无)'}")
        print(f"{'='*80}")

        nav_df = run_unified_v3(
            products=product_list,
            s4_products=s4_in_topn if s4_in_topn else None,
            enable_s4=bool(s4_in_topn),
            use_slip=True,
            start_date=start_date,
        )
        elapsed = time.time() - t0

        s = stats(nav_df)
        if not s:
            print(f"  [跳过] 数据不足")
            continue

        # 容量估算（私募实际方法）
        pool_roots = {r['root'] for r in top_n}
        liq_results = scan_liquidity()
        pool = [r for r in liq_results if r['root'] in pool_roots]
        cap_conservative, cap_detail_c = estimate_practical_capacity(pool, relaxed=False)
        cap_relaxed, cap_detail_r = estimate_practical_capacity(pool, relaxed=True)

        print(f"  耗时: {elapsed:.0f}s | 年化: {s['ann_return']:+.1%} | "
              f"回撤: {s['max_dd']:.1%} | 夏普: {s['sharpe']:.2f} | "
              f"容量(保守): {cap_conservative/10000:,.0f}万 | 容量(私募): {cap_relaxed/10000:,.0f}万")

        results.append({
            'n': n,
            'products': product_names,
            'nav_df': nav_df,
            'stats': s,
            'capacity_conservative': cap_conservative,
            'capacity_relaxed': cap_relaxed,
            'cap_detail': cap_detail_r,
            'elapsed': elapsed,
        })
        gc.collect()

    return results


def plot_nav_overlay(results, ranked, output_dir, sort_tag="vol", sort_label="日均成交量"):
    """绘制不同品种数量的净值对比图"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), height_ratios=[3, 1])

    colors = plt.cm.viridis(np.linspace(0.1, 0.9, len(results)))
    for i, r in enumerate(results):
        nav = r['nav_df']
        dates = pd.to_datetime(nav['date'])
        norm_nav = nav['nav'] / nav['nav'].iloc[0]
        lw = 2.5 if r['n'] == 15 else 1.5
        ax1.plot(dates, norm_nav, color=colors[i], linewidth=lw,
                 label=f"{r['n']}品种", alpha=0.85)

    ax1.set_title(f"组合净值随品种数量变化 (S1+S3+S4, {sort_label}排序)", fontsize=14, fontweight='bold')
    ax1.set_ylabel("归一化净值")
    ax1.legend(loc='upper left', ncol=3, fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5)

    # 回撤图
    for i, r in enumerate(results):
        nav = r['nav_df']
        dates = pd.to_datetime(nav['date'])
        nav_vals = nav['nav'].values
        peak = np.maximum.accumulate(nav_vals)
        dd = (nav_vals - peak) / peak
        ax2.fill_between(dates, dd, 0, color=colors[i], alpha=0.15)
        lw = 2.0 if r['n'] == 15 else 1.0
        ax2.plot(dates, dd, color=colors[i], linewidth=lw, alpha=0.7)

    ax2.set_ylabel("回撤")
    ax2.set_xlabel("日期")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:.0%}'))
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = os.path.join(output_dir, f"chart_product_count_nav_{sort_tag}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [图表] {path}")


def plot_metrics(results, output_dir, sort_tag="vol", sort_label="日均成交量"):
    """绘制指标随品种数量变化图"""
    ns = [r['n'] for r in results]
    ann_ret = [r['stats']['ann_return'] for r in results]
    ann_vol = [r['stats']['ann_vol'] for r in results]
    max_dd = [abs(r['stats']['max_dd']) for r in results]
    sharpe = [r['stats']['sharpe'] for r in results]
    calmar = [r['stats']['calmar'] for r in results]
    capacity_c = [r['capacity_conservative'] / 10000 for r in results]
    capacity_r = [r['capacity_relaxed'] / 10000 for r in results]

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))

    # 年化收益
    ax = axes[0, 0]
    ax.bar(ns, [x*100 for x in ann_ret], color='steelblue', alpha=0.8)
    ax.set_title("年化收益率 (%)")
    ax.set_xlabel("品种数")
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

    # 最大回撤
    ax = axes[0, 1]
    ax.bar(ns, [x*100 for x in max_dd], color='indianred', alpha=0.8)
    ax.set_title("最大回撤 (%)")
    ax.set_xlabel("品种数")

    # 夏普比率
    ax = axes[0, 2]
    ax.bar(ns, sharpe, color='seagreen', alpha=0.8)
    ax.set_title("夏普比率")
    ax.set_xlabel("品种数")

    # 卡玛比率
    ax = axes[1, 0]
    ax.bar(ns, calmar, color='darkorange', alpha=0.8)
    ax.set_title("卡玛比率")
    ax.set_xlabel("品种数")

    # 年化波动率
    ax = axes[1, 1]
    ax.bar(ns, [x*100 for x in ann_vol], color='mediumpurple', alpha=0.8)
    ax.set_title("年化波动率 (%)")
    ax.set_xlabel("品种数")

    # 策略容量（双档）
    ax = axes[1, 2]
    width = 0.35
    x_pos = np.arange(len(ns))
    ax.bar(x_pos - width/2, capacity_c, width, color='teal', alpha=0.6, label='保守(5%/10%)')
    ax.bar(x_pos + width/2, capacity_r, width, color='coral', alpha=0.8, label='私募(10%/20%)')
    ax.set_title("策略容量 (万元)")
    ax.set_xlabel("品种数")
    ax.set_xticks(x_pos)
    ax.set_xticklabels(ns)
    ax.legend(fontsize=8)

    fig.suptitle(f"风险收益指标随品种数量变化 (S1+S3+S4, {sort_label}排序)", fontsize=14, fontweight='bold')
    plt.tight_layout()
    path = os.path.join(output_dir, f"chart_product_count_metrics_{sort_tag}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [图表] {path}")


def generate_report(ranked, results, output_dir, sort_by="volume", sort_tag="vol", sort_label="日均成交量"):
    """生成 Markdown 报告"""
    lines = []
    lines.append(f"# 品种数量对组合策略表现的影响（{sort_label}排序）")
    lines.append("")
    lines.append(f"**日期**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append(f"**策略**: S1(垂直价差保护卖权) + S3(蝶式比例价差) + S4(尾部博弈)")
    lines.append(f"**回测起始**: 2024-01-01")
    lines.append(f"**排序依据**: 深虚值期权{sort_label}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 品种排名表
    lines.append("## 一、品种流动性排名")
    lines.append("")
    if sort_by == "oi":
        lines.append("| 排名 | 品种 | 代码 | Put日均仓 | Call日均仓 | 合计日均仓 | 有效上限(手) |")
        lines.append("|------|------|------|----------|----------|----------|------------|")
        for i, r in enumerate(ranked):
            lines.append(f"| {i+1} | {r['name']} | {r['root']} | "
                         f"{r['put_oi']:,.0f} | {r['call_oi']:,.0f} | "
                         f"{r['daily_oi']:,.0f} | {r['effective_limit']} |")
    else:
        lines.append("| 排名 | 品种 | 代码 | Put日均量 | Call日均量 | 合计日均量 | 有效上限(手) |")
        lines.append("|------|------|------|----------|----------|----------|------------|")
        for i, r in enumerate(ranked):
            lines.append(f"| {i+1} | {r['name']} | {r['root']} | "
                         f"{r['put_vol']:,.0f} | {r['call_vol']:,.0f} | "
                         f"{r['daily_vol']:,.0f} | {r['effective_limit']} |")
    lines.append("")
    lines.append(f"共 {len(ranked)} 个品种有深虚值期权数据。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 风险收益对比表
    lines.append("## 二、风险收益指标对比")
    lines.append("")
    lines.append("| 品种数 | 年化收益 | 年化波动 | 最大回撤 | 夏普比率 | 卡玛比率 | 容量-保守(万) | 容量-私募(万) | 品种列表 |")
    lines.append("|--------|---------|---------|---------|---------|---------|-------------|-------------|---------|")
    for r in results:
        s = r['stats']
        prods = ', '.join(r['products'][:5])
        if len(r['products']) > 5:
            prods += f' 等{len(r["products"])}个'
        lines.append(f"| {r['n']} | {s['ann_return']:+.1%} | {s['ann_vol']:.1%} | "
                     f"{s['max_dd']:.1%} | {s['sharpe']:.2f} | {s['calmar']:.2f} | "
                     f"{r['capacity_conservative']/10000:,.0f} | {r['capacity_relaxed']/10000:,.0f} | {prods} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 关键发现
    lines.append("## 三、关键发现")
    lines.append("")

    if len(results) >= 2:
        sharpes = [(r['n'], r['stats']['sharpe']) for r in results]
        best = max(sharpes, key=lambda x: x[1])
        lines.append(f"- **最优品种数**: {best[0]}个品种时夏普比率最高({best[1]:.2f})")

        rets = [(r['n'], r['stats']['ann_return']) for r in results]
        best_ret = max(rets, key=lambda x: x[1])
        lines.append(f"- **最高收益**: {best_ret[0]}个品种时年化收益最高({best_ret[1]:+.1%})")

        dds = [(r['n'], abs(r['stats']['max_dd'])) for r in results]
        min_dd = min(dds, key=lambda x: x[1])
        lines.append(f"- **最小回撤**: {min_dd[0]}个品种时最大回撤最小({-min_dd[1]:.1%})")

        caps = [(r['n'], r['capacity_relaxed']/10000) for r in results]
        max_cap = max(caps, key=lambda x: x[1])
        lines.append(f"- **最大容量(私募)**: {max_cap[0]}个品种时策略容量最大({max_cap[1]:,.0f}万)")

    lines.append("")
    lines.append("---")
    lines.append("")

    # 容量估算说明
    lines.append("## 四、容量估算方法说明")
    lines.append("")
    lines.append("**估算逻辑**：")
    lines.append("1. 每品种可承载的保证金 = min(手数上限, 流动性上限) x 单手保证金 x 2方向(双卖)")
    lines.append("2. S1手数上限20手/方向，S3卖腿上限30手/方向")
    lines.append("3. 全品种可用保证金加总 / 目标保证金利用率(50%) = 最大可管理AUM")
    lines.append("")
    lines.append("**两档假设**：")
    lines.append("- 保守假设：持仓≤5%日均持仓, 成交≤10%日均成交")
    lines.append("- 私募可接受：持仓≤10%日均持仓, 成交≤20%日均成交（会有一定冲击成本）")
    lines.append("")
    if results:
        best = max(results, key=lambda r: r['capacity_relaxed'])
        lines.append(f"**当前最大容量品种组合**: {best['n']}个品种，私募假设下约 **{best['capacity_relaxed']/10000:,.0f}万** "
                     f"(保守假设 {best['capacity_conservative']/10000:,.0f}万)")
        lines.append("")
        # 输出最大容量方案的品种明细
        lines.append("**品种明细**：")
        lines.append("")
        lines.append("| 品种 | 流动性上限(手) | S1手数/方向 | S3卖腿/方向 | S1保证金(万) | S3保证金(万) |")
        lines.append("|------|-------------|-----------|-----------|------------|------------|")
        for d in best['cap_detail']['details']:
            lines.append(f"| {d['name']} | {d['liq_limit']} | {d['s1_hands']} | {d['s3_sell']} | "
                         f"{d['s1_margin']/10000:,.1f} | {d['s3_margin']/10000:,.1f} |")
        lines.append(f"| **合计** | | | | **{best['cap_detail']['s1_margin']/10000:,.1f}** | **{best['cap_detail']['s3_margin']/10000:,.1f}** |")
        lines.append(f"| **总保证金** | | | | **{best['cap_detail']['total_margin']/10000:,.1f}万** | |")
        lines.append(f"| **÷50%利用率→AUM** | | | | **{best['capacity_relaxed']/10000:,.0f}万** | |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 四、图表")
    lines.append("")
    lines.append(f"![净值对比](chart_product_count_nav_{sort_tag}.png)")
    lines.append("")
    lines.append(f"![指标对比](chart_product_count_metrics_{sort_tag}.png)")
    lines.append("")

    path = os.path.join(output_dir, f"experiment_product_count_{sort_tag}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [报告] {path}")


def main(sort_by="volume"):
    sort_label = "日均持仓量(Put+Call)" if sort_by == "oi" else "日均成交量(Put+Call)"
    sort_tag = "oi" if sort_by == "oi" else "vol"
    output_dir = "容量和品种预估/output"
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 80)
    print("  实验：品种数量对组合策略表现和容量的影响")
    print(f"  策略: S1+S3+S4 | 排序: {sort_label}")
    print("=" * 80)

    # Step 1: 扫描和排序
    print("\n[1/4] 扫描品种流动性...")
    ranked = scan_and_rank(sort_by=sort_by)
    print(f"  共 {len(ranked)} 个品种")
    col_name = "日均持仓量" if sort_by == "oi" else "日均成交量"
    col_key = "daily_oi" if sort_by == "oi" else "daily_vol"
    print(f"\n  {'排名':<4} {'品种':<8} {col_name:>10}")
    print("  " + "-" * 30)
    for i, r in enumerate(ranked[:30]):
        print(f"  {i+1:<4} {r['name']:<8} {r[col_key]:>10,.0f}")

    # Step 2: 确定测试的 N 值
    max_n = min(len(ranked), 30)
    n_values = sorted(set([1, 3, 5, 8, 10, 15, 20, 25, max_n]) & set(range(1, max_n+1)))
    print(f"\n[2/4] 将测试以下品种数: {n_values}")

    # Step 3: 运行回测
    print(f"\n[3/4] 开始回测...")
    t_total = time.time()
    results = run_experiment(ranked, n_values)
    print(f"\n  总耗时: {time.time()-t_total:.0f}s")

    # Step 4: 生成图表和报告
    print(f"\n[4/4] 生成图表和报告...")
    if results:
        plot_nav_overlay(results, ranked, output_dir, sort_tag=sort_tag, sort_label=sort_label)
        plot_metrics(results, output_dir, sort_tag=sort_tag, sort_label=sort_label)
        generate_report(ranked, results, output_dir, sort_by=sort_by, sort_tag=sort_tag, sort_label=sort_label)

    # 打印摘要
    print(f"\n{'='*80}")
    print(f"  实验完成 (排序: {sort_label})")
    print(f"{'='*80}")
    print(f"\n  {'N':>3} | {'年化':>7} | {'回撤':>7} | {'夏普':>5} | {'卡玛':>5} | {'容量-保守':>9} | {'容量-私募':>9}")
    print(f"  {'-'*65}")
    for r in results:
        s = r['stats']
        print(f"  {r['n']:3d} | {s['ann_return']:+6.1%} | {s['max_dd']:6.1%} | "
              f"{s['sharpe']:5.2f} | {s['calmar']:5.2f} | "
              f"{r['capacity_conservative']/10000:>8,.0f}万 | {r['capacity_relaxed']/10000:>8,.0f}万")


if __name__ == "__main__":
    sort = "volume"
    if len(sys.argv) > 1 and sys.argv[1] in ("oi", "volume"):
        sort = sys.argv[1]
    main(sort_by=sort)
