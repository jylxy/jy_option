"""
全品种深虚值期权流动性扫描

扫描数据库中所有品种的深虚值期权（|delta|<0.15）的成交量和持仓量，
用于评估策略容量和品种池优化。
"""
import sqlite3
import sys
import os
import re
import statistics
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark_wind.db")

# 品种名称映射
NAME_MAP = {
    'HS300': '沪深300', 'CSI1000': '中证1000', 'SSE50': '上证50',
    'au': '沪金', 'ag': '沪银', 'cu': '沪铜', 'al': '沪铝',
    'sc': '原油', 'i': '铁矿石', 'rb': '螺纹钢', 'm': '豆粕',
    'SR': '白糖', 'CF': '棉花', 'TA': 'PTA', 'SA': '纯碱',
    'p': '棕榈油', 'eb': '苯乙烯', 'v': 'PVC', 'c': '玉米',
    'y': '豆油', 'eg': '乙二醇', 'pp': 'PP', 'jd': '鸡蛋',
    'l': '塑料', 'a': '豆一', 'b': '豆二', 'si': '工业硅',
    'lc': '碳酸锂', 'ao': '氧化铝', 'pg': 'LPG', 'ni': '沪镍',
    'zn': '沪锌', 'sn': '沪锡', 'jm': '焦煤', 'cs': '淀粉',
    'lh': '生猪', 'br': '丁二烯橡胶', 'FG': '玻璃', 'MA': '甲醇',
    'UR': '尿素', 'SM': '锰硅', 'SF': '硅铁', 'SH': '烧碱',
    'OI': '菜油', 'RM': '菜粕', 'AP': '苹果', 'CJ': '红枣',
    'PK': '花生', 'PX': '对二甲苯', 'CY': '棉纱', 'lg': '液化气',
    'ru': '天然橡胶',
}

# 当前15品种
CURRENT_15 = {'HS300', 'CSI1000', 'SSE50', 'au', 'ag', 'cu', 'al',
              'sc', 'i', 'rb', 'm', 'SR', 'CF', 'TA', 'SA'}

# 品种乘数和保证金比例
PRODUCT_SPECS = {
    'HS300': (100, 0.10), 'CSI1000': (100, 0.10), 'SSE50': (100, 0.10),
    'au': (1000, 0.05), 'ag': (15, 0.05), 'cu': (5, 0.05), 'al': (5, 0.05),
    'sc': (1000, 0.07), 'i': (100, 0.05), 'rb': (10, 0.05), 'm': (10, 0.05),
    'SR': (10, 0.05), 'CF': (5, 0.05), 'TA': (5, 0.06), 'SA': (20, 0.06),
    'p': (10, 0.05), 'eb': (5, 0.05), 'v': (5, 0.05), 'c': (10, 0.05),
    'y': (10, 0.05), 'eg': (10, 0.05), 'pp': (5, 0.05), 'jd': (10, 0.05),
    'l': (5, 0.05), 'a': (10, 0.05), 'b': (10, 0.05), 'si': (5, 0.05),
    'lc': (1, 0.05), 'ao': (20, 0.05), 'pg': (20, 0.05), 'ni': (1, 0.05),
    'zn': (5, 0.05), 'sn': (1, 0.05), 'jm': (60, 0.05), 'cs': (10, 0.05),
    'lh': (16, 0.05), 'lg': (20, 0.05), 'ru': (10, 0.05),
}

# 交易所限仓（保守估计，非做市商客户）
EXCHANGE_LIMITS = {
    'HS300': 5000, 'CSI1000': 5000, 'SSE50': 5000,
}
DEFAULT_COMMODITY_LIMIT = 2000  # 商品期权默认限仓


def extract_root(underlying_code):
    if underlying_code in ('HS300', 'CSI1000', 'SSE50'):
        return underlying_code
    m = re.match(r'^([a-zA-Z]+)', underlying_code)
    return m.group(1) if m else underlying_code


def scan_liquidity(start_date='2025-09-01'):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            e.underlying_code, e.option_type,
            e.volume, e.open_interest,
            e.spot_close, e.option_close, ABS(e.delta) as abs_delta
        FROM mart_option_daily_enriched e
        WHERE ABS(e.delta) < 0.15 AND ABS(e.delta) > 0.01
            AND e.option_close >= 0.5
            AND e.dte BETWEEN 15 AND 90
            AND e.trade_date >= ?
            AND ((e.option_type = 'P' AND e.moneyness < 1.0) 
                 OR (e.option_type = 'C' AND e.moneyness > 1.0))
            AND e.volume IS NOT NULL AND e.open_interest IS NOT NULL
    """, (start_date,))

    product_data = defaultdict(lambda: {
        'put_vol': [], 'put_oi': [], 'call_vol': [], 'call_oi': [],
        'spot': [], 'premium': [], 'underlying_codes': set()
    })

    for row in cur.fetchall():
        uc, otype, vol, oi, spot, prem, delta = row
        root = extract_root(uc)
        if otype == 'P':
            product_data[root]['put_vol'].append(vol)
            product_data[root]['put_oi'].append(oi)
        else:
            product_data[root]['call_vol'].append(vol)
            product_data[root]['call_oi'].append(oi)
        product_data[root]['spot'].append(spot)
        product_data[root]['premium'].append(prem)
        product_data[root]['underlying_codes'].add(uc)

    conn.close()

    results = []
    for root, d in product_data.items():
        if len(d['put_vol']) < 10:
            continue
        put_avg_vol = statistics.mean(d['put_vol'])
        put_avg_oi = statistics.mean(d['put_oi'])
        call_avg_vol = statistics.mean(d['call_vol']) if d['call_vol'] else 0
        call_avg_oi = statistics.mean(d['call_oi']) if d['call_oi'] else 0
        avg_spot = statistics.mean(d['spot'])

        min_oi = min(put_avg_oi, call_avg_oi) if call_avg_oi > 0 else put_avg_oi
        min_vol = min(put_avg_vol, call_avg_vol) if call_avg_vol > 0 else put_avg_vol
        oi_limit = int(min_oi * 0.05)
        vol_limit = int(min_vol * 0.10)
        exch_limit = EXCHANGE_LIMITS.get(root, DEFAULT_COMMODITY_LIMIT)
        effective_limit = min(exch_limit, oi_limit, vol_limit)

        results.append({
            'root': root,
            'name': NAME_MAP.get(root, root),
            'put_vol': put_avg_vol, 'put_oi': put_avg_oi,
            'call_vol': call_avg_vol, 'call_oi': call_avg_oi,
            'effective_limit': effective_limit,
            'exch_limit': exch_limit,
            'oi_limit': oi_limit, 'vol_limit': vol_limit,
            'avg_spot': avg_spot,
            'in_current': root in CURRENT_15,
        })

    results.sort(key=lambda x: x['effective_limit'], reverse=True)
    return results


def print_report(results):
    print("=" * 105)
    print("  全品种深虚值期权流动性排名")
    print("  条件：|delta|<0.15, 权利金>=0.5, DTE 15-90天, 最近半年数据")
    print("  约束：持仓≤日均持仓5%, 成交≤日均成交10%, 交易所限仓")
    print("=" * 105)

    print(f"\n{'排名':<4} {'品种':<8} {'代码':<10} {'Put日均量':>9} {'Put日均仓':>9} "
          f"{'Call日均量':>10} {'Call日均仓':>10} {'有效上限':>8} {'当前池':>6}")
    print("-" * 95)
    for i, r in enumerate(results):
        tag = "★" if r['in_current'] else ""
        print(f"{i+1:<4} {r['name']:<8} {r['root']:<10} {r['put_vol']:>9.0f} {r['put_oi']:>9.0f} "
              f"{r['call_vol']:>10.0f} {r['call_oi']:>10.0f} {r['effective_limit']:>8} {tag:>6}")

    # 当前品种排名
    current = [r for r in results if r['in_current']]
    current.sort(key=lambda x: x['effective_limit'], reverse=True)
    print(f"\n{'='*105}")
    print("  当前15品种流动性排名")
    print(f"{'='*105}")
    for i, r in enumerate(current):
        print(f"  {i+1:>2}. {r['name']:<8} 有效上限={r['effective_limit']:>5}手  "
              f"(5%持仓={r['oi_limit']}, 10%成交={r['vol_limit']}, 交易所={r['exch_limit']})")

    # 替代品种
    alt = [r for r in results if not r['in_current'] and r['effective_limit'] >= 10]
    alt.sort(key=lambda x: x['effective_limit'], reverse=True)
    print(f"\n{'='*105}")
    print("  候选替代品种（不在当前池中，有效上限≥10手）")
    print(f"{'='*105}")
    for i, r in enumerate(alt[:15]):
        print(f"  {i+1:>2}. {r['name']:<8} 有效上限={r['effective_limit']:>5}手  "
              f"(5%持仓={r['oi_limit']}, 10%成交={r['vol_limit']})")


if __name__ == "__main__":
    results = scan_liquidity()
    print_report(results)
    sys.stdout.flush()
