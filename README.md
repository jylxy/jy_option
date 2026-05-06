# S1 期权卖权策略回测与研究工程

本目录是当前 S1 卖权策略的主要代码仓库。当前生产级回测入口是 `src/toolkit_minute_engine.py`，历史 `true_minute_engine.py`、Parquet 直读引擎和早期试验脚本已经归档到 `archive/`。

## 当前主入口

```bash
python3 src/toolkit_minute_engine.py \
  --config config_s1_baseline_b2_product_tilt075_stop15.json \
  --start-date 2022-01-01 \
  --end-date 2026-05-06 \
  --tag s1_example
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--config` | 回测配置文件，当前多数实验配置仍放在仓库根目录。 |
| `--start-date` / `--end-date` | 回测日期范围。 |
| `--tag` | 输出目录和日志识别标签。 |
| `--verbose` | 输出更详细日志。 |

## 目录结构

| 目录 | 当前用途 | 整理原则 |
|---|---|---|
| `src/` | 回测引擎和可复用业务模块。 | 只保留当前主路径会调用的代码。历史引擎、一次性脚本不再放这里。 |
| `scripts/` | 实验启动、结果分析、报告生成、autoresearch 辅助脚本。 | 保留，但必须在 `scripts/README.md` 中登记用途。 |
| `docs/` | 策略设计、实验方案、审计报告、结构整理记录。 | 研究结论和实验设计统一沉淀在这里。 |
| `experiments/` | 自动研究系统、审计、scorecard、review 输出。 | 作为实验元数据和自动研究工作区。 |
| `output/` | 回测落盘结果、图表、报告中间产物。 | 不应作为核心代码依赖来源。 |
| `logs/` | 本地运行日志。 | 可清理，但不要影响正在运行的远端实验。 |
| `skills/` | 项目内报告或研究 skills。 | 用于复用报告写作和研究范式。 |
| `archive/` | 历史引擎、旧脚本、数据维护脚本和 scratch。 | 只读追溯，不作为当前主路径。 |
| `tests/` | 当前主路径待补测试清单。 | 旧 true-minute 集成测试已归档，后续每轮结构性改动都应补最小测试。 |

## 当前核心模块

| 模块 | 职责 |
|---|---|
| `toolkit_minute_engine.py` | 当前主回测调度器：日循环、开仓、盘中止损、估值、输出。 |
| `strategy_rules.py` | S1 策略规则、候选评分、预算倾斜、止损/开仓参数读取。 |
| `portfolio_risk.py` | 板块、相关组、Greeks、stress budget、保证金上限。 |
| `budget_model.py` | 开仓预算和分配逻辑。 |
| `vol_regime.py` | 波动率状态、冷却期、重开规则、低波/降波判断。 |
| `margin_model.py` | 交易所保证金估算。 |
| `broker_costs.py` | 手续费和券商成本表。 |
| `execution_model.py` | 开平仓滑点和成交口径。 |
| `intraday_execution.py` | 盘中止损成交价、下一分钟价格、持仓索引等执行辅助。 |
| `open_execution.py` | 待开仓成交、全日成交量约束、延期成交拆分。 |
| `s1_pending_open.py` | S1 待开仓记录构造。 |
| `stop_policy.py` | S1 止损范围、分层止损和同组/单合约止损规则。 |
| `option_calc.py` | Black76/IV/Greeks 计算。 |
| `spot_provider.py` | 真实标的价格映射。 |
| `contract_provider.py` | 合约基础信息和乘数。 |
| `day_loader.py` | Toolkit 数据读取。 |
| `daily_aggregation.py` | 日频聚合。 |
| `iv_warmup.py` | IV 预热逻辑。 |
| `result_output.py` | NAV、订单、诊断和图表原始数据输出。 |
| `product_taxonomy.py` | 品种板块、相关组、分类。 |
| `product_lifecycle.py` | 上市观察期和品种生命周期。 |

## 脚本管理

脚本清单见 `scripts/README.md`。新增脚本必须满足：

1. 能说明用途：实验启动、结果分析、报告生成、审计、数据维护或 scratch。
2. 如果是一次性脚本，完成后应移动到 `archive/src_scratch/` 或合并进正式分析脚本。
3. 如果是实验启动脚本，必须写清楚依赖的配置文件和输出 tag。
4. 如果会修改核心配置或远端任务，必须先确认没有正在运行的冲突实验。

## 当前整理原则

根目录仍保留大量 `config_*.json`，这是为了兼容远端启动脚本和历史实验记录。后续可以逐步迁移到 `configs/`，但需要同步修改 launch 脚本、autoresearch 队列和文档引用后再做。

当前优先级：

1. 继续把 `toolkit_minute_engine.py` 中的长函数拆成可测试的小模块。
2. 保持远端正在运行实验的核心代码稳定，不在实验中途改远端引擎。
3. 把所有新实验先登记到 `docs/`，再写配置和启动脚本。
4. 把报告生成、因子分析、实验审计统一放在 `scripts/` 和 `skills/` 中，避免散落在根目录。

## 验证

结构性改动后至少执行：

```bash
python -m py_compile src/*.py scripts/*.py
```

如果改动涉及主回测逻辑，还需要在远端或本地做小样本 smoke backtest，并对比关键字段：NAV、持仓数、止损数、保证金、权利金、Greeks 和 PnL attribution。
