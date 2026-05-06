# Scripts 清单

本目录只放当前仍可能复用的实验、分析、报告和自动研究脚本。一次性排查脚本完成后应归档到 `archive/src_scratch/`，不要长期留在根目录或 `src/`。

## 实验运行与自动研究

| 脚本 | 用途 |
|---|---|
| `s1_autoresearch_runner.py` | S1 自动研究队列执行器，读取 experiment queue，生成 scorecard、audit 和 review。 |
| `s1_experiment_scorecard.py` | 对单个回测结果生成核心绩效卡：NAV、年化、回撤、Sharpe、Calmar、Greeks、止损等。 |
| `s1_experiment_audit.py` | 对实验结果做审计，标记可能的未来函数、异常成交、数据缺失和逻辑风险。 |
| `launch_p4_p3_p3b_stop_grid.sh` | P4/P3/P3B 与止损倍数组合实验启动脚本。 |
| `launch_p5_stop_mechanism.sh` | P5 止损机制实验启动脚本。 |
| `launch_p6_a0_execution_stress.sh` | P6 A0 实盘口径压力实验启动脚本。 |
| `launch_p6_next_minute_high.sh` | P6 下一分钟最高价止损压力口径启动脚本。 |

## 回测结果分析

| 脚本 | 用途 |
|---|---|
| `analyze_backtest_outputs.py` | 通用回测输出分析，生成 NAV、回撤、保证金、Greeks、PnL attribution 等图表。 |
| `analyze_factor_layers.py` | 因子分层、Rank IC、Q1-Q5、相关性和残差 IC 分析。 |
| `analyze_candidate_universe_layers.py` | candidate universe 原始分层检查。 |
| `analyze_candidate_universe_corrected.py` | 修正口径后的 candidate universe / full shadow 因子检查。 |
| `analyze_s1_product_suitability.py` | S1 品种适配度、流动性、尾部风险和长期可交易性分析。 |
| `analyze_b6_product_selection.py` | B6 品种筛选与残差 IC 相关分析。 |

## 报告生成

| 脚本 | 用途 |
|---|---|
| `build_s1_report_docx.py` | 生成 S1 回测归因报告 Word/飞书导入文档。 |
| `build_factor_layer_report_docx.py` | 生成因子分层检查 Word/飞书导入文档。 |
| `build_b4_formula_research_pack.py` | 基于 Premium Pool 公式生成 B4 研究分析包。 |
| `build_b6_experiment_report.py` | 生成 B6 实验报告。 |
| `build_product_pool_comparison_report.py` | 生成 P3/P3B/品种池对比报告。 |
| `build_stop_loss_sweep_report.py` | 生成止损倍数 sweep 对比报告。 |
| `build_candidate_layer_extra_plots.py` | 为因子报告补充额外图表。 |

## 当前整理建议

1. 保留上述脚本在 `scripts/` 根下，暂不移动到子目录，避免破坏远端启动命令和历史文档引用。
2. 后续如果要拆子目录，建议按 `scripts/analysis/`、`scripts/reports/`、`scripts/launchers/`、`scripts/autoresearch/` 分层，并同步修改所有引用。
3. 新增脚本必须在本文件登记用途；若只是临时排查，请命名为 `tmp_*.py` 并在完成后归档。
4. 报告脚本应优先复用 `skills/` 中的报告范式，避免每次单独写一套图片解释逻辑。
