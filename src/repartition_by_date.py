"""
按日期重分区 Parquet — 一次性脚本

用 DuckDB 将按品种分区的大 Parquet 文件重写为按日期分区的小文件。

两种模式：
  fast模式（默认）：DuckDB COPY ... PARTITION_BY，一次扫描全部写出，最快
  safe模式（--safe）：逐日期查询写出，内存占用小但慢（每天扫描一次全量）

用法：
    python3 src/repartition_by_date.py                    # 全量，fast模式
    python3 src/repartition_by_date.py --start 2024-01-01 # 只处理2024+
    python3 src/repartition_by_date.py --type option       # 只处理期权
    python3 src/repartition_by_date.py --safe              # 低内存模式
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


def repartition_fast(data_type, data_dir, output_dir, start_date=None):
    """
    快速模式：DuckDB 一次扫描，按日期分组写出。

    原理：DuckDB 的 COPY ... PARTITION_BY 会自动按指定列分区写出，
    只需扫描一次源文件。服务器有 2TB 内存，完全够用。
    """
    import duckdb

    filename = FILES[data_type]
    src_path = os.path.join(data_dir, filename)
    dst_dir = os.path.join(output_dir, data_type)

    if not os.path.exists(src_path):
        logger.error("源文件不存在: %s", src_path)
        return

    os.makedirs(dst_dir, exist_ok=True)
    logger.info("[FAST] 重分区 %s: %s → %s", data_type, src_path, dst_dir)

    conn = duckdb.connect()
    # 设置内存和线程
    conn.execute("SET memory_limit='200GB'")
    conn.execute("SET threads TO 16")

    t0 = time.time()
    date_filter = f"WHERE datetime >= '{start_date} 00:00'" if start_date else ""

    # 一次扫描，按 trade_date 分区写出
    # 先添加 trade_date 列（datetime 前10个字符），然后 PARTITION_BY
    logger.info("  开始分区写出（一次扫描）...")
    conn.execute(f"""
        COPY (
            SELECT *, SUBSTR(datetime, 1, 10) AS trade_date
            FROM read_parquet('{src_path}')
            {date_filter}
        ) TO '{dst_dir}'
        (FORMAT PARQUET, PARTITION_BY (trade_date), COMPRESSION ZSTD,
         OVERWRITE_OR_IGNORE)
    """)

    elapsed = time.time() - t0
    # 统计生成的文件数
    n_files = 0
    for root, dirs, files in os.walk(dst_dir):
        n_files += len([f for f in files if f.endswith(".parquet")])

    conn.close()
    logger.info("  完成: %d 个分区文件, 耗时 %.0f 秒 (%.1f 分钟)",
                n_files, elapsed, elapsed / 60)


def repartition_safe(data_type, data_dir, output_dir, start_date=None):
    """
    安全模式：逐日期查询写出，内存占用小但慢。
    支持断点续传（跳过已存在的文件）。
    """
    import duckdb

    filename = FILES[data_type]
    src_path = os.path.join(data_dir, filename)
    dst_dir = os.path.join(output_dir, data_type)

    if not os.path.exists(src_path):
        logger.error("源文件不存在: %s", src_path)
        return

    os.makedirs(dst_dir, exist_ok=True)
    logger.info("[SAFE] 重分区 %s: %s → %s", data_type, src_path, dst_dir)

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
    skipped = 0
    for i, date_str in enumerate(dates):
        # 检查是否已存在（DuckDB PARTITION_BY 生成的目录结构）
        part_dir = os.path.join(dst_dir, f"trade_date={date_str}")
        legacy_path = os.path.join(dst_dir, f"date={date_str}.parquet")
        if os.path.exists(part_dir) or os.path.exists(legacy_path):
            skipped += 1
            continue

        t1 = time.time()
        conn.execute(f"""
            COPY (
                SELECT *, '{date_str}' AS trade_date
                FROM read_parquet('{src_path}')
                WHERE datetime >= '{date_str} 00:00'
                  AND datetime < '{date_str} 24:00'
            ) TO '{dst_dir}'
            (FORMAT PARQUET, PARTITION_BY (trade_date), COMPRESSION ZSTD,
             OVERWRITE_OR_IGNORE)
        """)

        elapsed = time.time() - t1
        if (i + 1) % 50 == 0 or elapsed > 30:
            logger.info("  [%d/%d] %s — %.1f 秒 (跳过 %d)",
                        i + 1, len(dates), date_str, elapsed, skipped)

    conn.close()
    logger.info("  完成: %d 个日期, 跳过 %d 个已存在", len(dates), skipped)


def main():
    parser = argparse.ArgumentParser(description="按日期重分区 Parquet")
    parser.add_argument("--type", choices=["option", "futures", "etf", "all"],
                        default="all", help="处理哪种数据")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--safe", action="store_true",
                        help="安全模式（逐日期，低内存，慢）")
    args = parser.parse_args()

    types = list(FILES.keys()) if args.type == "all" else [args.type]
    fn = repartition_safe if args.safe else repartition_fast

    t0 = time.time()
    for dt in types:
        fn(dt, args.data_dir, args.output_dir, args.start)

    logger.info("全部完成, 总耗时 %.0f 秒 (%.1f 分钟)", 
                time.time() - t0, (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
