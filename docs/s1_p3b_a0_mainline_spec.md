# S1 P3B/A0 主线口径说明

日期：2026-05-06

## 1. 当前主线定义

当前 S1 研究主线收敛为 `P3B/A0`，代码配置入口为：

```bash
config_s1_p5_p3b_a0_group_stop15.json
```

该配置继承链为：

```text
config_s1_p5_p3b_a0_group_stop15.json
-> config_s1_baseline_b2_product_tilt075_stop15_ledet_term_pref.json
-> config_s1_baseline_b2_product_tilt075_stop15.json
-> config_s1_baseline_b2_product_tilt075_stop25.json
-> config_s1_baseline_b1_liquidity_oi_rank_stop25.json
-> config_s1_baseline_b0_all_products_stop25.json
```

因此，P3B/A0 不是最早的裸 B0，而是目前被选为主线的“全品种朴素卖权基准 + 流动性/OI 排序 + 品种预算倾斜 + 乐得期限/合约月份偏好 + 组级 1.5x 权利金止损”版本。

这条主线必须由两部分共同定义：

- 配置文件：`config_s1_p5_p3b_a0_group_stop15.json`。
- 固定品种池：`AG, AL, AO, AU, B, CU, EB, EG, FG, I, M, MA, NI, P, RB, RM, RU, SA, SC, SH, SN, TA, ZN`。

品种池已经固化在 `config_s1_p5_p3b_a0_group_stop15.json` 的 `product_pool` 字段里。命令行 `--products` 仍可临时覆盖，但正式主线对比不应随意覆盖。若配置文件和运行时品种池不一致，该次结果不应被标记为 P3B/A0 主线结果。

固定运行命令模板如下：

```bash
python3 src/toolkit_minute_engine.py \
  --config config_s1_p5_p3b_a0_group_stop15.json \
  --start-date 2022-01-01 \
  --end-date 2026-05-06 \
  --tag <tag>
```

如需显式校验品种池，也可以附加同一组 `--products`：

```bash
--products AG,AL,AO,AU,B,CU,EB,EG,FG,I,M,MA,NI,P,RB,RM,RU,SA,SC,SH,SN,TA,ZN
```

## 1.1 A0 与 P3B 的等价边界

`P3B/A0` 的准确含义是：

```text
B2C 品种预算倾斜
+ 乐得期限/合约月份偏好
+ 固定 23 个主流商品品种池
+ 组级 1.5x 权利金止损
```

因此，以下两种结果应当完全一致：

- `config_s1_p5_p3b_a0_group_stop15.json`。
- `config_s1_baseline_b2_product_tilt075_stop15_ledet_term_pref.json` 加同一组 23 品种池，且默认 `s1_stop_close_scope = group`。

以下结果不应被视为 A0 的等价结果：

- `config_s1_p4_p3b_ledet_term_pref_stop20.json`、`stop25.json`、`stop30.json`，因为止损倍数不同。
- 只包含进行中 `nav_*.csv`、但没有最终 `orders_*.csv` 的历史中断回测文件。该类文件可做过程参考，但不能作为最终现金账校验基准。

主线结果必须能通过订单现金账校验：

```text
累计PnL
= 累计卖出开仓权利金
- 累计平仓/止损买回成本
- 当前未平仓义务方负债
- 累计手续费
```

若 NAV 与上述现金账重构不一致，应优先视为回测口径或中断文件问题，而不是策略收益变化。

## 2. 交易目标

P3B/A0 继续服务于 S1 的核心目标：

- 以卖权收权利金为主要收益来源。
- 维持纯卖方框架，不引入保护腿、比例价差或主动 Delta 对冲。
- 使用全品种池中的可交易商品期权为主，优先跟随乐得访谈中确认的品种与期限偏好。
- 通过分散品种、分散执行价梯队和止损机制控制尾部亏损。
- 把后续研究集中在主线可解释改进上，而不是继续堆叠所有历史实验层。

## 3. 当前主线开仓规则

主线开仓仍是日频扫描、次日成交的框架：

- `enable_s1 = true`，`enable_s3 = false`，`enable_s4 = false`。
- 默认只卖期权，不买保护腿：`s1_protect_enabled = false`。
- 允许同品种同方向继续加仓，由组合和保证金约束控制。
- Delta 硬约束为 `abs(delta) <= 0.10`。
- 默认每侧最多挑选 5 个合约梯队。
- 合约选择基础版本来自 B0/B1：次月、流动性和持仓量排序、最低期权价格过滤。
- 品种预算使用 B2C 的 `s1_b2_product_tilt_enabled = true`，当前主线强度为 `s1_b2_tilt_strength = 0.75`。
- 乐得期限偏好覆盖若干品种：
  - `AU`：只选 2/4/6/8/10 月，且仍尊重 DTE 边界。
  - `AG`、`I`：近月偏好，仍尊重 DTE 边界。
  - `RB`、`M`、`RM`、`B`、`P`：主力月偏好，仍尊重 DTE 边界。

这里需要特别区分“乐得期限偏好”和“期限结构因子”：

- 当前 P3B/A0 已经实现的是乐得访谈口径下的期限/合约月份偏好，也就是对特定品种覆盖开仓到期选择。
- 当前 P3B/A0 没有启用 B3/B4 中的 term structure slope、near/far IV pressure 或期限结构打分。
- 因此，如果“期限结构”指近远月 IV 曲线斜率、期限价差或 forward variance pressure，那么它没有进入当前主线；如果“期限结构”指 AU 双月、AG/I 近月、部分品种主力月这样的合约月份偏好，那么它已经进入当前主线。
- 后续若要重新引入真正的期限结构因子，应作为独立控制变量实验，不应和 P3B/A0 基准混在一起。

## 4. 当前主线退出与止损

主线止损采用 P5 A0 版本：

- 权利金止损倍数为 `1.5x`。
- 止损范围为 `group`，即同品种同方向触发后按组级处理。
- 不启用分层止损：`s1_layered_stop_enabled = false`。
- 不要求 IV 不回落才止损：`premium_stop_requires_daily_iv_non_decrease = false`。
- 不启用止盈：`take_profit_enabled = false`。
- 到期/临近到期按基准逻辑提前处理，避免最后一日 gamma 风险和行权处理混乱。

## 5. 当前主线执行与费用口径

P3B/A0 继续沿用已经修正过的实盘友好口径：

- 使用券商/期货公司费用表：`option_fee_use_broker_table = true`。
- 使用券商/期货公司保证金率表：`margin_ratio_use_broker_table = true`。
- 默认开平仓有滑点：`execution_slippage_enabled = true`。
- 开仓、平仓、止损滑点分开配置。
- 盘中风险检查间隔为 15 分钟。
- 止损使用日内最高价预筛，未触及阈值的合约不进入分钟扫描。
- 盘中止损带成交量与确认过滤，避免单笔异常成交价导致假止损。

## 6. 当前主线保留模块

以下模块属于当前主线依赖，应继续保留在主路径：

- `toolkit_minute_engine.py`：主调度器，后续继续拆小，但仍是入口。
- `strategy_rules.py`：S1 合约筛选与规则入口，后续应继续瘦身。
- `s1_budget_tilt.py`：B2C 品种预算倾斜，当前主线需要。
- `stop_policy.py`：P5 A0 组级止损范围和后续止损机制实验。
- `intraday_execution.py`：盘中止损成交、下一分钟口径和预筛。
- `open_execution.py`：开仓成交和成交量约束。
- `margin_model.py`、`broker_costs.py`、`execution_model.py`：保证金、费用与滑点口径。
- `option_calc.py`、`spot_provider.py`、`contract_provider.py`：Greeks、真实标的和合约信息。
- `portfolio_diagnostics.py`：主线回测后分析仍需要的诊断输出。

## 7. 应从主引擎隔离的研究层

以下逻辑不属于 P3B/A0 的日常主线，但需要作为研究证据保留：

- falling framework / forward vega / volatility regime sizing。
- B3 clean vega 系列。
- B4 去共线合约排序系列。
- B5 full shadow 因子扩展。
- B6 残差因子与品种筛选。
- autoresearch 自动研究队列。
- 历史报告生成和一次性实验启动脚本。

处理原则不是直接删除，而是：

```text
主引擎不常驻、实验模块可复现、文档保留结论、脚本通过统一入口可找到。
```

## 8. 主线后续优化边界

下一阶段优化应优先围绕 P3B/A0 做控制变量实验：

- P3B/A0 的实盘口径压力测试。
- 组级止损 vs 单合约止损 vs 分层止损。
- 品种池与板块约束。
- 保证金使用率与执行容量。
- 主线性能优化，尤其是开仓候选生成、日频聚合和分钟止损扫描。

暂不应继续把 B3/B4/B5/B6 的所有因子直接塞回主线。若要重新启用，必须先证明它改善了以下公式中的明确变量：

```text
Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```
