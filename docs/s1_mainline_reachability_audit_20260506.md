# S1 P3B/A0 主线可达性审计

日期：2026-05-06

## 1. 审计结论

当前主线为 `P3B/A0`，其配置入口为 `config_s1_p5_p3b_a0_group_stop15.json`。

本轮审计结论：

- P3B/A0 仍依赖 B0/B1/B2C，因此 `s1_budget_tilt.py` 和 B2 品种预算倾斜不能归档。
- P3B/A0 不依赖 falling、B3、B4、B5、B6、full shadow 和 autoresearch 的交易逻辑。
- 脚本层已经可以先做目录分层，不改变任何交易逻辑。
- 根目录配置文件暂不移动，因为 `extends` 继承链和历史远端启动命令仍直接引用根目录路径。

## 2. 当前主线配置链

| 层级 | 配置文件 | 当前作用 |
|---|---|---|
| A0 | `config_s1_p5_p3b_a0_group_stop15.json` | P5 A0 组级 1.5x 止损主线 |
| P3B | `config_s1_baseline_b2_product_tilt075_stop15_ledet_term_pref.json` | 乐得期限偏好 |
| B2 stop15 | `config_s1_baseline_b2_product_tilt075_stop15.json` | 止损改为 1.5x |
| B2 tilt075 | `config_s1_baseline_b2_product_tilt075_stop25.json` | 品种预算倾斜强度 0.75 |
| B1 | `config_s1_baseline_b1_liquidity_oi_rank_stop25.json` | 流动性/OI 排序和最低价过滤 |
| B0 | `config_s1_baseline_b0_all_products_stop25.json` | 全品种、次月、delta <= 0.1、无保护腿基础规则 |

## 3. 主线保留逻辑

| 逻辑 | 是否主线 | 说明 |
|---|---|---|
| B0 全品种纯卖权 | 是 | P3B/A0 基础 |
| B1 流动性/OI 排序 | 是 | 当前可交易性口径 |
| B2 品种预算倾斜 | 是 | P3B/A0 当前继承链使用 |
| 乐得期限偏好 | 是 | P3B 的核心差异 |
| P5 A0 组级止损 | 是 | 当前主线止损机制 |
| P6 实盘口径压力 | 诊断 | 同一主线的鲁棒性压力测试，不是默认交易规则 |
| B3/B4/B5/B6 因子 | 否 | 后续可作为实验模块或研究证据保留 |
| full shadow | 否 | 因子研究工具，不应常驻主线回测 |
| autoresearch | 否 | 研究系统工具，不应污染主回测路径 |
| falling framework | 否 | 当前主线未启用 |
| forward vega filter | 否 | 当前主线未启用 |

## 4. 第一轮已执行整理

脚本从单层目录整理为：

| 子目录 | 内容 |
|---|---|
| `scripts/analysis/` | 回测输出、因子分层、candidate universe、品种适配度分析 |
| `scripts/reports/` | S1、因子、B4、B6、P3/P3B、止损 sweep 报告生成 |
| `scripts/launchers/` | P4/P5/P6 历史实验启动脚本 |
| `scripts/autoresearch/` | autoresearch runner、scorecard、audit |
| `scripts/s1_cli.py` | 统一入口，继续负责发现和调用常用脚本 |

这一步只改变脚本组织和入口路径，不改变交易逻辑。

## 5. 待抽离主引擎逻辑

以下仍在 `toolkit_minute_engine.py` 中，但不属于 P3B/A0 默认交易路径：

| 逻辑块 | 建议目标模块 | 处理优先级 |
|---|---|---|
| B5 full shadow 字段、候选输出 | `s1_shadow_universe.py` | 高 |
| B3/B4/B6 候选评分字段 | `s1_experimental_scoring.py` | 高 |
| candidate universe 面板输出 | `s1_shadow_universe.py` | 高 |
| falling / forward vega 过滤 | `s1_experimental_filters.py` | 中 |
| autoresearch 相关输出 | 保持在 `scripts/autoresearch/` | 中 |

抽离完成后，P3B/A0 默认回测应只计算主线所需字段，shadow 和实验评分只在配置显式开启时调用。

## 6. 脚本归档原则

当前脚本不是直接删除，而是按用途分层。

后续若某个脚本满足以下条件，可再移动到 `archive/scripts/`：

- 已经有正式报告固化结果。
- 不再被 `scripts/s1_cli.py` 注册。
- 不再被当前主线、P6 压力测试或报告流程调用。
- 文档中保留了历史复现命令或输出 tag。

## 7. 下一步建议

下一步优先拆 `toolkit_minute_engine.py` 的 shadow / experimental scoring 逻辑。原因是这些代码体量大、字段多、主线默认不用，且容易让后续审计误以为 P3B/A0 使用了复杂因子。

