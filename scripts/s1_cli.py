"""Unified command router for S1 research scripts.

The individual scripts remain in place for backward compatibility. This router
adds discoverability and gives us a stable surface if scripts are later moved
into subdirectories.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import runpy
import sys


SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ScriptEntry:
    name: str
    category: str
    path: str
    description: str


SCRIPT_REGISTRY = [
    ScriptEntry(
        "analyze-backtest",
        "analysis",
        "analyze_backtest_outputs.py",
        "通用回测输出分析，生成 NAV、回撤、保证金、Greeks、PnL attribution 图表。",
    ),
    ScriptEntry(
        "analyze-factors",
        "analysis",
        "analyze_factor_layers.py",
        "因子分层、Rank IC、Q1-Q5、相关性和残差 IC 分析。",
    ),
    ScriptEntry(
        "analyze-candidate-layers",
        "analysis",
        "analyze_candidate_universe_layers.py",
        "candidate universe 原始分层检查。",
    ),
    ScriptEntry(
        "analyze-candidate-corrected",
        "analysis",
        "analyze_candidate_universe_corrected.py",
        "修正口径后的 candidate universe / full shadow 因子检查。",
    ),
    ScriptEntry(
        "analyze-products",
        "analysis",
        "analyze_s1_product_suitability.py",
        "S1 品种适配度、流动性、尾部风险和长期可交易性分析。",
    ),
    ScriptEntry(
        "analyze-b6-products",
        "analysis",
        "analyze_b6_product_selection.py",
        "B6 品种筛选与残差 IC 相关分析。",
    ),
    ScriptEntry(
        "report-s1",
        "reports",
        "build_s1_report_docx.py",
        "生成 S1 回测归因报告 Word / 飞书导入文档。",
    ),
    ScriptEntry(
        "report-factor-layer",
        "reports",
        "build_factor_layer_report_docx.py",
        "生成因子分层检查 Word / 飞书导入文档。",
    ),
    ScriptEntry(
        "report-b4-formula",
        "reports",
        "build_b4_formula_research_pack.py",
        "基于 Premium Pool 公式生成 B4 研究分析包。",
    ),
    ScriptEntry(
        "report-b6",
        "reports",
        "build_b6_experiment_report.py",
        "生成 B6 实验报告。",
    ),
    ScriptEntry(
        "report-product-pool",
        "reports",
        "build_product_pool_comparison_report.py",
        "生成 P3/P3B/品种池对比报告。",
    ),
    ScriptEntry(
        "report-stop-sweep",
        "reports",
        "build_stop_loss_sweep_report.py",
        "生成止损倍数 sweep 对比报告。",
    ),
    ScriptEntry(
        "plot-candidate-extra",
        "reports",
        "build_candidate_layer_extra_plots.py",
        "为因子报告补充额外图表。",
    ),
    ScriptEntry(
        "scorecard",
        "autoresearch",
        "s1_experiment_scorecard.py",
        "对单个回测结果生成核心绩效卡。",
    ),
    ScriptEntry(
        "audit",
        "autoresearch",
        "s1_experiment_audit.py",
        "对实验结果做审计，标记实现、数据和逻辑风险。",
    ),
    ScriptEntry(
        "autoresearch",
        "autoresearch",
        "s1_autoresearch_runner.py",
        "S1 自动研究队列执行器。",
    ),
]


def registry_by_name() -> dict[str, ScriptEntry]:
    return {entry.name: entry for entry in SCRIPT_REGISTRY}


def print_registry(category: str | None = None) -> None:
    entries = [entry for entry in SCRIPT_REGISTRY if category in (None, entry.category)]
    width = max((len(entry.name) for entry in entries), default=4)
    current_category = None
    for entry in sorted(entries, key=lambda x: (x.category, x.name)):
        if entry.category != current_category:
            current_category = entry.category
            print(f"\n[{current_category}]")
        print(f"  {entry.name:<{width}}  {entry.path:<42} {entry.description}")


def run_entry(entry: ScriptEntry, argv: list[str]) -> None:
    path = SCRIPT_DIR / entry.path
    if not path.exists():
        raise SystemExit(f"脚本不存在: {path}")
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(path), *argv]
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = old_argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S1 research script router. Use --list to discover commands.",
    )
    parser.add_argument("command", nargs="?", help="registered script command")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the script")
    parser.add_argument("--list", action="store_true", help="list registered commands")
    parser.add_argument(
        "--category",
        choices=sorted({entry.category for entry in SCRIPT_REGISTRY}),
        help="filter --list by category",
    )
    ns = parser.parse_args(argv)

    if ns.list or not ns.command:
        print_registry(ns.category)
        return 0

    entries = registry_by_name()
    entry = entries.get(ns.command)
    if entry is None:
        print(f"未知命令: {ns.command}\n")
        print_registry(ns.category)
        return 2
    run_entry(entry, ns.args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
