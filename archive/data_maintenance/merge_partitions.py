"""
Archived data-maintenance script: 合并分区小文件 — 每个日期目录下的多个 parquet 文件合并为 1 个

DuckDB PARTITION_BY 为每个原始 row_group 生成独立文件，
导致每个日期目录下有几百个 2-10KB 小文件。
本脚本将它们合并为单个文件，减少文件系统压力。

用法：
    python3 src/merge_partitions.py
    python3 src/merge_partitions.py --type option
    python3 src/merge_partitions.py --dry-run
"""
import os
import sys
import glob
import time
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.environ.get(
    "PARQUET_DATA_DIR",
    "/macro/home/lxy/yy_2_lxy_20260415/"
)


def merge_type(data_type, data_dir, dry_run=False):
    """合并某种数据类型的所有分区目录"""
    import duckdb

    base = os.path.join(data_dir, "partitioned", data_type)
    if not os.path.isdir(base):
        logger.warning("目录不存在: %s", base)
        return

    dirs = sorted([d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))])
    logger.info("%s: %d 个目录", data_type, len(dirs))

    merged_count = 0
    skipped = 0
    t0 = time.time()

    for i, d in enumerate(dirs):
        path = os.path.join(base, d)
        files = glob.glob(os.path.join(path, "*.parquet"))

        if len(files) <= 1:
            skipped += 1
            continue

        if dry_run:
            if (i + 1) % 500 == 0:
                logger.info("  [dry-run %d/%d] %s: %d 个文件", i + 1, len(dirs), d, len(files))
            merged_count += 1
            continue

        try:
            merged_path = os.path.join(path, "_merged.parquet")
            conn = duckdb.connect()
            conn.execute(f"""
                COPY (SELECT * FROM read_parquet('{path}/*.parquet'))
                TO '{merged_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            conn.close()

            # 删除旧文件
            for f in files:
                os.remove(f)
            # 重命名
            os.rename(merged_path, os.path.join(path, "data_0.parquet"))
            merged_count += 1

        except Exception as exc:
            logger.warning("  合并失败 %s: %s", d, exc)

        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            logger.info("  [%d/%d] 已合并 %d, 跳过 %d, 耗时 %.0f 秒",
                        i + 1, len(dirs), merged_count, skipped, elapsed)

    elapsed = time.time() - t0
    logger.info("  %s 完成: 合并 %d, 跳过 %d (已是单文件), 耗时 %.0f 秒",
                data_type, merged_count, skipped, elapsed)


def main():
    parser = argparse.ArgumentParser(description="合并分区小文件")
    parser.add_argument("--type", choices=["option", "futures", "etf", "all"],
                        default="all")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--dry-run", action="store_true", help="只统计不实际合并")
    args = parser.parse_args()

    types = ["option", "futures", "etf"] if args.type == "all" else [args.type]

    t0 = time.time()
    for dt in types:
        merge_type(dt, args.data_dir, args.dry_run)

    logger.info("全部完成, 总耗时 %.0f 秒", time.time() - t0)


if __name__ == "__main__":
    main()
