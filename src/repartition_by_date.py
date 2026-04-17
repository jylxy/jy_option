"""
按日期重分区 Parquet — 一次性脚本

用 DuckDB 将按品种分区的大 Parquet 文件重写为按日期分区的小文件：
  原始: OPTION1MINRESULT.parquet (32GB, 64亿行, 按品种分区)
  输出: partitioned/option/date=2024-01-02.parquet (~50-200MB)
        partitioned/option/date=2024-01-03.parquet
        ...

每个日期一个文件，load_day 直接读对应文件，< 1 秒。

用法：
    python3 src/repartition_by_date.py
    python3 src/repartition_by_date.py --type option   # 只处理期权
    python3 src/repartition_by_date.py --type futures   # 只处理期货
    python3 src/repartition_by_date.py --start 2024-01-01  # 只处理2024年以后
"""
import os
import sys
import time
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.environ.get(
    "PARQUET_DATA_DIR",
    "/macro/home/lxy/yy_2_lxy_20260415/"
)
DEFAULT_OUTPUT_DIR = os.path.join(DEFAULT_DATA_DIR, "partitioned")

FILES = {
    "option": "OPTION1MINRESULT.parquet",
    "futures": "FUTURE1MINRESULT.parquet",
    "etf": "ETF1MINRESULT.parquet",
}


def repartition(data_type, data_dir, output_dir, start_date=None):
    """用 DuckDB 按日期重分区一个 Parquet 文件"""
    import duckdb

    filename = FILES[data_type]
    src_path = os.path.join(data_dir, filename)
    dst_dir = os.path.join(output_dir, data_type)

    if not os.path.exists(src_path):
        logger.error("源文件不存在: %s", src_path)
        return

    os.makedirs(dst_dir, exist_ok=True)
    logger.info("重分区 %s: %s → %s", data_type, src_path, dst_dir)

    conn = duckdb.connect()

    # 获取所有日期
    t0 = time.time()
    date_filter = f"AND datetime >= '{start_date} 00:00'" if start_date else ""
    dates = conn.execute(f"""
        SELECT DISTINCT SUBSTR(datetime, 1, 10) as dt
        FROM read_parquet('{src_path}')
        WHERE datetime IS NOT NULL {date_filter}
        ORDER BY dt
    """).fetchall()
    dates = [d[0] for d in dates if d[0] and len(d[0]) == 10]
    logger.info("  共 %d 个日期, 扫描耗时 %.0f 秒", len(dates), time.time() - t0)

    # 逐日期导出
    for i, date_str in enumerate(dates):
        dst_path = os.path.join(dst_dir, f"date={date_str}.parquet")
        if os.path.exists(dst_path):
            continue  # 跳过已存在的（支持断点续传）

        t1 = time.time()
        next_date = date_str[:8] + str(int(date_str[8:]) + 1).zfill(2)
        # 用简单的次日计算（DuckDB 会处理月末）
        conn.execute(f"""
            COPY (
                SELECT * FROM read_parquet('{src_path}')
                WHERE datetime >= '{date_str} 00:00'
                  AND datetime < '{date_str} 24:00'
            ) TO '{dst_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

        elapsed = time.time() - t1
        if (i + 1) % 50 == 0 or elapsed > 30:
            logger.info("  [%d/%d] %s — %.1f 秒", i + 1, len(dates), date_str, elapsed)

    conn.close()
    logger.info("  完成: %d 个日期文件", len(dates))


def main():
    parser = argparse.ArgumentParser(description="按日期重分区 Parquet")
    parser.add_argument("--type", choices=["option", "futures", "etf", "all"],
                        default="all", help="处理哪种数据")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    args = parser.parse_args()

    types = list(FILES.keys()) if args.type == "all" else [args.type]

    t0 = time.time()
    for dt in types:
        repartition(dt, args.data_dir, args.output_dir, args.start)

    logger.info("全部完成, 总耗时 %.0f 秒", time.time() - t0)


if __name__ == "__main__":
    main()
