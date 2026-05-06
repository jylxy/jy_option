# Scripts 清单

本目录只放当前仍可能复用的实验、分析、报告和自动研究脚本。一次性排查脚本完成后应归档到 `archive/src_scratch/`，不要长期留在根目录或 `src/`。

## 统一入口

优先使用统一入口查看和调用脚本：

```bash
python scripts/s1_cli.py --list
python scripts/s1_cli.py --list --category reports
python scripts/s1_cli.py analyze-backtest --help
python scripts/s1_cli.py report-s1 --help
```

常用脚本已经按功能移动到 `analysis/`、`reports/`、`launchers/`、`autoresearch/` 子目录。优先通过 `s1_cli.py` 调用；如需直接运行单脚本，请使用子目录路径。

## 实验运行与自动研究

| 脚本 | 用途 |
|---|---|
| `s1_cli.py` | 统一脚本入口和命令注册表。 |
| `autoresearch/s1_autoresearch_runner.py` | S1 自动研究队列执行器，读取 experiment queue，生成 scorecard、audit 和 review。 |
| `autoresearch/s1_experiment_scorecard.py` | 对单个回测结果生成核心绩效卡：NAV、年化、回撤、Sharpe、Calmar、Greeks、止损等。 |
| `autoresearch/s1_experiment_audit.py` | 对实验结果做审计，标记可能的未来函数、异常成交、数据缺失和逻辑风险。 |
| `launchers/launch_p4_p3_p3b_stop_grid.sh` | P4/P3/P3B 与止损倍数组合实验启动脚本。 |
| `launchers/launch_p5_stop_mechanism.sh` | P5 止损机制实验启动脚本。 |
| `launchers/launch_p6_a0_execution_stress.sh` | P6 A0 实盘口径压力实验启动脚本。 |
| `launchers/launch_p6_next_minute_high.sh` | P6 下一分钟最高价止损压力口径启动脚本。 |

## 回测结果分析

| 脚本 | 用途 |
|---|---|
| `analysis/analyze_backtest_outputs.py` | 通用回测输出分析，生成 NAV、回撤、保证金、Greeks、PnL attribution 等图表。 |
| `analysis/analyze_factor_layers.py` | 因子分层、Rank IC、Q1-Q5、相关性和残差 IC 分析。 |
| `analysis/analyze_candidate_universe_layers.py` | candidate universe 原始分层检查。 |
| `analysis/analyze_candidate_universe_corrected.py` | 修正口径后的 candidate universe / full shadow 因子检查。 |
| `analysis/analyze_s1_product_suitability.py` | S1 品种适配度、流动性、尾部风险和长期可交易性分析。 |
| `analysis/analyze_b6_product_selection.py` | B6 品种筛选与残差 IC 相关分析。 |

## 报告生成

| 脚本 | 用途 |
|---|---|
| `reports/build_s1_report_docx.py` | 生成 S1 回测归因报告 Word/飞书导入文档。 |
| `reports/build_factor_layer_report_docx.py` | 生成因子分层检查 Word/飞书导入文档。 |
| `reports/build_b4_formula_research_pack.py` | 基于 Premium Pool 公式生成 B4 研究分析包。 |
| `reports/build_b6_experiment_report.py` | 生成 B6 实验报告。 |
| `reports/build_product_pool_comparison_report.py` | 生成 P3/P3B/品种池对比报告。 |
| `reports/build_stop_loss_sweep_report.py` | 生成止损倍数 sweep 对比报告。 |
| `reports/build_candidate_layer_extra_plots.py` | 为因子报告补充额外图表。 |

## 当前整理建议

1. `scripts/` 根目录只保留 `s1_cli.py` 和本 README。
2. 新增脚本必须按功能放入 `analysis/`、`reports/`、`launchers/`、`autoresearch/` 或明确归档到 `archive/`。
3. 新增可复用脚本必须登记到 `s1_cli.py`；若只是临时排查，请命名为 `tmp_*.py` 并在完成后归档。
4. 报告脚本应优先复用 `skills/` 中的报告范式，避免每次单独写一套图片解释逻辑。
5. 历史命令如果仍写作 `python scripts/foo.py`，需要改为 `python scripts/<category>/foo.py` 或使用 `python scripts/s1_cli.py <command>`。
