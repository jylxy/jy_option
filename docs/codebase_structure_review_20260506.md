# server_deploy 代码结构体检与整理计划

日期：2026-05-06

## 1. 本轮结论

当前 `server_deploy` 已经从早期“所有东西都塞进 `src/`”的状态，逐步变成了一个可维护的研究工程。但仍有三个明显问题：

1. 根目录配置文件很多，B0-B6、P3-P6、falling framework、shadow universe 等配置混在一起。
2. `scripts/` 有分析、报告、启动、审计、自动研究脚本，但之前没有清单，新人很难判断哪个该用。
3. `toolkit_minute_engine.py` 仍然承担了过多职责，虽然已经拆出多个模块，但主引擎还可以继续瘦身。

本轮先做低风险整理：

| 动作 | 状态 | 说明 |
|---|---|---|
| 重写根目录 `README.md` | 已完成 | 修复乱码，并指向当前真实入口 `src/toolkit_minute_engine.py`。 |
| 新增 `scripts/README.md` | 已完成 | 把脚本分为实验运行、分析、报告三类。 |
| 重写本结构体检文档 | 已完成 | 给后续迁移和清理提供清晰边界。 |
| 移动配置文件 | 暂缓 | 远端实验和 launch 脚本依赖根目录配置，不能贸然移动。 |
| 删除 pycache / 临时文件 | 暂缓 | 可以做，但应在一次明确的清理提交里完成。 |
| 归档旧版集成测试 | 已完成 | 原 `tests/test_integration.py` 依赖旧版 true-minute 引擎，已移到 `archive/legacy_tests/`。 |

## 2. 当前目录职责

| 目录 | 职责 | 当前判断 |
|---|---|---|
| `src/` | 主回测引擎和可复用业务模块。 | 应保持精简，只放当前主路径会调用的代码。 |
| `scripts/` | 实验启动、结果分析、报告生成、自动研究辅助。 | 合理，但需要持续维护脚本清单。 |
| `docs/` | 策略设计、实验方案、审计报告、结构整理文档。 | 合理，是研究沉淀主目录。 |
| `experiments/` | 自动研究系统输出、scorecard、audit、review。 | 合理，不应手动混入核心代码。 |
| `output/` | 回测结果和图表产物。 | 输出目录，不作为代码依赖。 |
| `logs/` | 日志。 | 可清理，但不要影响正在运行的任务。 |
| `skills/` | 报告和研究范式复用。 | 合理，后续报告应优先走 skills。 |
| `archive/` | 历史引擎、旧脚本、数据维护脚本、scratch。 | 合理，作为追溯区。 |
| `tests/` | 当前主路径模块级测试。 | 已有一批单测，但还需要补强止损、成交、开仓和候选排序。 |

## 3. src 当前模块地图

| 模块 | 当前职责 |
|---|---|
| `toolkit_minute_engine.py` | 主调度器：日循环、候选生成调度、开平仓、盘中止损、估值、输出。 |
| `strategy_rules.py` | S1 规则、候选评分、预算倾斜、止损/开仓参数读取。 |
| `portfolio_risk.py` | 板块、相关组、cash Greeks、stress budget、保证金上限。 |
| `budget_model.py` | 开仓预算和资金分配。 |
| `vol_regime.py` | 波动率状态、冷却期、重开规则、低波/降波判断。 |
| `intraday_execution.py` | 盘中止损成交价、下一分钟价格、持仓索引。 |
| `open_execution.py` | 待开仓成交、全日成交量约束、延期成交拆分。 |
| `s1_pending_open.py` | S1 待开仓记录构造。 |
| `stop_policy.py` | S1 止损范围、分层止损、同组/单合约止损规则。 |
| `execution_model.py` | 通用滑点和成交口径。 |
| `broker_costs.py` | 手续费与券商成本。 |
| `margin_model.py` | 交易所保证金估算。 |
| `option_calc.py` | Black76、IV、Greeks。 |
| `spot_provider.py` | 真实标的价格映射。 |
| `contract_provider.py` | 合约信息和乘数。 |
| `day_loader.py` | Toolkit 数据读取。 |
| `daily_aggregation.py` | 日频聚合。 |
| `iv_warmup.py` | IV 预热。 |
| `position_model.py` | 持仓对象。 |
| `result_output.py` | NAV、订单、诊断输出。 |
| `product_taxonomy.py` | 品种板块和相关组。 |
| `product_lifecycle.py` | 上市观察期。 |
| `query_filters.py` | SQL / 品种池过滤。 |
| `trading_calendar.py` | 交易日历。 |
| `runtime_paths.py` | 路径解析。 |
| `data_tables.py` | Toolkit 表名。 |
| `config_loader.py` | 配置加载。 |
| `audit_s1_core.py` | S1 核心归因审计工具。 |

## 4. scripts 当前分类

详见 `scripts/README.md`。当前 21 个脚本可以分成四组：

| 类别 | 脚本数 | 说明 |
|---|---:|---|
| 实验运行与自动研究 | 7 | queue、scorecard、audit、launchers。 |
| 回测结果分析 | 6 | NAV、因子、candidate universe、品种筛选。 |
| 报告生成 | 7 | S1 报告、因子报告、B4/B6/P3/止损 sweep 报告。 |
| 辅助图表 | 1 | candidate layer extra plots。 |

## 5. 暂时不要动的部分

| 对象 | 原因 |
|---|---|
| 根目录 `config_*.json` | 远端实验、launch 脚本和历史报告引用这些文件名。贸然移动会让复现实验变难。 |
| `experiments/s1_autoresearch/` | 自动研究系统正在使用，且有 heartbeat 监控。 |
| `output/` 中未汇总结果 | 当前仍用于报告和对比分析。 |
| 远端核心引擎代码 | 后台 P5/P6 等实验仍在跑，本地可整理，远端不应中途改主引擎。 |

## 6. 可以安全清理的候选

| 对象 | 建议 |
|---|---|
| 根目录 `tmp_stop_zero_analysis.py`、`tmp_stop_zero_analysis_fast.py` | 移动到 `archive/src_scratch/`，保留追溯但不放根目录。 |
| `__pycache__/` 和 `.pyc` | 可删除，不影响源码；建议单独清理，不和逻辑改动混在一个提交。 |
| 过期日志 | 可按日期归档或清理。 |
| 长期不用的输出图和中间 csv | 等对应报告确认后再清理，避免丢失复盘依据。 |

## 7. 测试状态

原 `tests/test_integration.py` 是旧版 true-minute / Parquet 引擎测试，已经不对应当前 `toolkit_minute_engine.py` 主路径。本轮已将它归档到 `archive/legacy_tests/test_true_minute_integration.py`，并新增 `tests/README.md` 说明现有测试与下一批应补测试。

当前 `tests/` 仍保留配置、成本、保证金、预算、组合风控、IV warmup、品种生命周期、spot 映射、策略规则等模块级测试。

当前测试缺口：

| 测试对象 | 重要性 |
|---|---|
| 止损范围与分层止损 | P5/P6 实验高度依赖，必须单测。 |
| 盘中成交价压力口径 | 实盘可实现性判断依赖，必须单测。 |
| 开仓成交量约束和延期成交 | 影响回测真实性和速度，必须单测。 |
| 待开仓字段构造 | 字段很多，适合用单测防止重构漏字段。 |
| 保证金模型 | 直接影响仓位和收益率，应持续回归。 |

## 8. 下一轮代码结构建议

优先继续拆 `toolkit_minute_engine.py`，但每次只拆一类职责，并保持可验证：

1. `s1_candidate_builder.py`：候选池生成、DTE/Delta/流动性/价格过滤、候选排序。
2. `s1_open_engine.py`：开仓调度、预算裁剪、待开仓队列写入。
3. `intraday_stop_engine.py`：盘中止损扫描、异常跳价确认、成交价压力口径。
4. `s1_diagnostics.py`：funnel、premium、Greeks、止损和持仓诊断输出。
5. `experiment_registry.py`：配置/tag/实验族群元数据，减少根目录配置靠文件名理解。

## 9. 性能优化优先级

| 优先级 | 方向 | 原因 |
|---|---|---|
| P0 | 用日内最高价预筛止损，未触发合约不进分钟扫描。 | 止损倍数实验中，盘中扫描是主要慢点。 |
| P1 | 减少 `_process_intraday_exits` 的 groupby/copy 次数。 | 全品种长周期回测中重复开销明显。 |
| P1 | pending open 全日 VWAP/TWAP 计算缓存。 | 同日多个开仓候选会重复访问分钟数据。 |
| P2 | diagnostics lite/full 开关。 | 正式回测不一定需要全量 shadow 和图表字段。 |
| P2 | candidate universe 输出独立开关。 | full shadow 是研究任务，不应拖慢普通回测。 |

## 10. 验证要求

任何结构性改动后至少做三层验证：

1. `python -m py_compile src/*.py scripts/*.py`
2. 小样本回测 smoke test，对比 NAV、持仓数、止损数、保证金、权利金、Greeks。
3. 如果改了成交、止损或预算逻辑，需要与改动前同配置同日期对齐比较，确认差异来自预期逻辑而不是重构误差。

## 11. 当前建议

下一步可以做一个“安全清理提交”：

1. 只更新 README 和脚本清单。
2. 把根目录临时脚本移入 `archive/src_scratch/`。
3. 删除本地 `__pycache__`。
4. 不移动 configs，不改远端核心代码，不影响当前后台实验。

然后再做第二个“结构拆分提交”，继续拆 `toolkit_minute_engine.py`。
