# S1 B0 标准基准规范

## 1. 文档定位

本文档是 S1 B0 的标准口径规范，用于后续所有 S1 改进版本的对比、复盘和归因。

B0 不是最终实盘策略，也不是优化后的卖权模型。它是一个刻意保持朴素的研究基准，用来回答：

```text
在全品种、低 Delta、次近到期、50% 保证金目标、真实手续费和保证金率下，
如果不做 IV/RV、降波、趋势、板块、相关性和 stress score 等过滤，
原始卖权暴露本身能贡献多少收益、承担多少回撤？
```

后续任何新版本，如果不能相对 B0 证明增量价值，就不应该被认为是有效升级。

## 2. 版本信息

当前标准 B0 版本：

```text
strategy_version = s1_baseline_b0_standard_stop25
```

配置文件：

```text
config_s1_baseline_b0_all_products_stop25.json
```

正式长回测标签：

```text
s1_b0_standard_stop25_allprod_2022_latest
```

旧标签 `s1_baseline_b0_allprod_2022_latest` 对应标准化前口径，不应再作为最终 B0 结论。

## 3. 策略研究目标

B0 的目标不是追求最高收益，而是构建一条干净基准线。

它主要用于回答：

- 原始低 Delta 卖权溢价是否足够厚。
- 50% 保证金目标下，裸卖组合的自然回撤大概多大。
- 收益主要来自 theta、vega、delta、gamma 还是 residual。
- P/C 结构是否会自然偏移，偏移后风险有多大。
- 全品种等权下，ETF、股指、商品分别贡献多少。
- 后续复杂规则是在创造增量价值，还是只是压低仓位。

最终 S1 研究目标仍是：

```text
长期年化收益：约 6%
最大回撤：小于 2%
收益来源：以 theta 和 vega 为主，而不是长期依赖 delta
组合画像：接近成熟卖方管理人的多品种、多执行价、小单腿、纪律化止损组合
```

B0 允许超过最终回撤目标，因为它的职责是暴露问题，不是直接满足所有约束。

## 4. 策略范围

B0 只启用 S1 卖权策略。

启用项：

- S1 裸卖期权。
- 全品种扫描。
- 真实合约乘数。
- 真实手续费表。
- 真实保证金率表。
- T+1 执行。
- 开仓、平仓和止损滑点。
- 成交量限制。
- 到期内在价值结算。
- 2.5x 权利金硬止损。
- 盘中止损防跳变确认。

禁用项：

- S3 比例价差。
- S4 或其他结构。
- 保护腿。
- IV 预热和 IV 分位过滤。
- IV/RV carry 过滤。
- 降波确认和 forward vega 过滤。
- 趋势动量 P/C 偏移。
- 板块集中度约束。
- 相关性约束。
- cash Greek 上限。
- stress score 候选评分。
- stress budget sizing。
- 止盈。
- 止损后冷却期。
- 止损后重开规则。
- 低 IV 例外规则。

## 5. 数据与市场范围

B0 默认覆盖系统能读到的全部中国场内期权品种，包括：

- 商品期权。
- ETF 期权。
- 股指期权。

品种不需要人工白名单。只要当天存在有效期权链、真实标的价格、合约乘数、手续费和保证金参数，就进入每日扫描。

品种上市观察期在 B0 中关闭：

```text
product_min_listing_days = 0
product_observation_months = 0
product_min_daily_oi = 0
```

这意味着新上市品种只要有数据，就可以被扫描和交易。后续如果要加入观察期，应作为 B1/B2 之后的独立实验，不能混入标准 B0。

## 6. 每日回测流程

每日流程如下：

```text
1. 读取当日期权链、标的价格、成交量、合约信息。
2. 更新已有持仓的估值、保证金和 Greeks。
3. 执行当日止损、到期结算等退出逻辑。
4. 收盘后扫描全品种，生成下一交易日待开仓订单。
5. 下一交易日按执行规则成交。
6. 记录 NAV、持仓、订单、诊断和归因字段。
```

B0 的开仓是 T 日收盘生成计划，T+1 日执行，不使用 T+1 之后的未来信息生成信号。

## 7. 品种扫描顺序

B0 模式下，品种按字符串排序扫描：

```text
sorted_products = sorted(product_frames)
```

B0 不按以下维度排序：

- 不按成交量。
- 不按权利金厚度。
- 不按 IV/RV。
- 不按保证金效率。
- 不按板块。
- 不按 ETF、股指、商品优先级。
- 不按近期表现。
- 不按降波状态。

这个顺序是刻意朴素的。它避免在基准里提前引入“聪明排序”，但也意味着当组合接近 50% 保证金上限时，排序会对边际开仓有影响。

后续如果改成按流动性、市场分层或收益效率排序，必须另起版本标签，不能继续叫标准 B0。

## 8. 资金与保证金预算

核心资金参数：

```text
capital = 10,000,000
margin_cap = 0.50
s1_margin_cap = 0.50
margin_per = 0.02
s1_baseline_equal_weight_products = true
s1_baseline_equal_weight_contracts = true
```

预算规则：

```text
当日单品种预算 = S1 保证金上限 / 当日候选品种数
Put 侧预算 = 单品种预算 / 2
Call 侧预算 = 单品种预算 / 2
方向内合约预算 = 方向剩余预算 / 剩余候选合约数
```

因此，标准 B0 是：

```text
全品种等权
品种内 P/C 预算各半
方向内合约等权
```

实际保证金使用率可能低于或略高于 50%，主要由整数手数、单张保证金、候选不足、成交量约束和 NAV 波动造成。

## 9. 到期选择

B0 使用次近到期：

```text
s1_expiry_mode = nth_expiry
s1_expiry_rank = 2
```

定义：

```text
同一品种当天所有有效到期日按 DTE 从小到大排序，取第 2 个到期日。
```

注意：

- 这不是自然月份意义上的“下个月”。
- 商品期权合约月份并不总是对应自然次月，所以用 DTE 排序更稳。
- 如果当天只有 1 个有效到期日，该品种当天不开仓。
- 到期日 DTE 必须大于 0。

## 10. P/C 选择

B0 不做择边，默认同时尝试 Put 和 Call：

```text
s1_side_selection_enabled = false
s1_conditional_strangle_enabled = false
s1_trend_confidence_enabled = false
```

因此，系统每天对每个品种的次近到期尝试：

```text
卖 Put
卖 Call
```

B0 不会因为趋势、动量、IV 环境或评分主动偏向某一侧。

实际 P/C 手数仍可能偏离 1:1，原因包括：

- 某一侧没有合格低 Delta OTM 合约。
- 某一侧权利金不足以覆盖手续费。
- 某一侧单张保证金太高。
- 某一侧成交量限制导致无法成交。
- 某些品种某段时间的合约挂牌结构天然不对称。

B0 的要求是“开仓逻辑不主动偏向”，而不是“最终持仓 P/C 必须完全相等”。

## 11. 候选合约筛选

### 11.1 Put 候选

Put 候选必须满足：

```text
option_type = P
moneyness < 1.0
delta < 0
abs(delta) >= s1_sell_delta_floor
abs(delta) <= s1_sell_delta_cap
option_close >= 0.5
```

当前参数：

```text
s1_sell_delta_floor = 0.0
s1_sell_delta_cap = 0.10
```

即：

```text
卖 OTM Put，abs(delta) <= 0.10，期权价格不低于 0.5。
```

### 11.2 Call 候选

Call 候选必须满足：

```text
option_type = C
moneyness > 1.0
delta > 0
delta >= s1_sell_delta_floor
delta <= s1_sell_delta_cap
option_close >= 0.5
```

即：

```text
卖 OTM Call，delta <= 0.10，期权价格不低于 0.5。
```

### 11.3 流动性候选过滤

当前标准 B0 不在候选层设置最低成交量和最低持仓量：

```text
s1_min_volume = 0
s1_min_oi = 0
```

但执行层保留成交量限制：

```text
volume_limit_pct = 1.0
```

因此，B0 目前不是“完全不考虑成交”，而是：

```text
候选筛选阶段不因成交量剔除；
实际成交阶段受当天可成交量约束。
```

后续如果加入 `volume >= 5/10/20`、`OI >= 50/100/200`，应定义为 B1 流动性版本。

### 11.4 费用过滤

当前标准 B0 设置：

```text
s1_min_premium_fee_multiple = 1.0
```

候选必须满足：

```text
option_close * multiplier >= open_fee + close_fee
```

即单张合约权利金现金额至少覆盖一开一平的往返手续费。

这条规则用于避免卖出权利金太薄、手续费已经吞掉收益空间的 cheap OTM 合约。

## 12. 候选排序与执行价数量

当前参数：

```text
s1_target_abs_delta = 0.10
s1_baseline_max_contracts_per_side = 5
```

候选排序以接近 0.10 Delta 为核心：

```text
delta_dist = abs(abs(delta) - 0.10)
delta_dist 越小越靠前
```

在同等条件下，代码使用稳定排序，并以 `option_code` 作为最终 tie-breaker，保证同样数据下结果可复现。

每个品种、每个方向、每个扫描日最多取 5 个合约：

```text
Put 最多 5 个执行价
Call 最多 5 个执行价
```

这意味着标准 B0 不是卖出所有 `abs(delta)<=0.10` 的合约，而是只卖每侧最接近 0.10 Delta 的最多 5 个执行价。

如果未来测试每侧 10 个或 all，必须另起版本，例如：

```text
B0-10
B0-all
```

## 13. 手数计算

对每个候选合约，先计算单张保证金。

方向内候选按顺序逐个处理：

```text
剩余候选数 = 当前方向还未处理的候选数量
单合约预算 = 当前方向剩余保证金预算 / 剩余候选数
target_qty = floor(单合约预算 / 单张保证金)
target_qty = max(1, target_qty)
```

随后通过组合保证金约束计算最终可开手数。

如果最终可开手数为 0，则跳过该合约。

## 14. 加仓规则

当前标准 B0 允许同品种、同方向、同到期继续加仓：

```text
s1_allow_add_same_side = true
s1_allow_add_same_expiry = true
```

含义：

- 同一品种已经有 S1 Put 仓位，后续交易日仍可继续开 Put。
- 同一品种已经有 S1 Call 仓位，后续交易日仍可继续开 Call。
- 同一品种同一到期已经有仓位，后续仍可继续开。

真正限制继续加仓的是：

- 组合总保证金 50%。
- S1 总保证金 50%。
- 当日品种等权预算。
- 当日方向预算。
- 合格候选是否存在。
- 成交量限制。
- 单张保证金和整数手数。

## 15. 组合约束

每笔开仓前检查：

```text
组合总保证金 + 新仓保证金 <= margin_cap * NAV
S1 保证金 + 新仓保证金 <= s1_margin_cap * NAV
```

当前：

```text
margin_cap = 0.50
s1_margin_cap = 0.50
```

标准 B0 关闭组合风控模块：

```text
portfolio_construction_enabled = false
portfolio_bucket_control_enabled = false
portfolio_corr_control_enabled = false
portfolio_dynamic_corr_control_enabled = false
portfolio_stress_gate_enabled = false
portfolio_cash_vega_cap = 0.0
portfolio_cash_gamma_cap = 0.0
portfolio_product_margin_cap = 0.0
portfolio_product_side_margin_cap = 0.0
portfolio_bucket_margin_cap = 0.0
portfolio_corr_group_margin_cap = 0.0
portfolio_contract_lot_cap = 0
portfolio_budget_brake_enabled = false
```

因此 B0 不会因为板块、相关性、cash gamma、cash vega、单品种上限或 stress budget 主动限制开仓。

## 16. 执行与成交

B0 使用当前分钟执行口径。

关键参数：

```text
execution_slippage_enabled = true
execution_open_slippage_pct = 0.002
execution_close_slippage_pct = 0.002
execution_stop_slippage_pct = 0.005
execution_slippage_apply_to_expiry = false
volume_limit_pct = 1.0
skip_same_day_exit_for_vwap_opens = true
```

解释：

- 开仓和平仓有滑点。
- 止损滑点更保守。
- 到期结算不施加滑点。
- 开仓成交受成交量约束。
- 使用 VWAP 开仓时，避免同日开仓同日退出造成不现实路径。

## 17. 退出规则

### 17.1 止盈

B0 不止盈：

```text
take_profit_enabled = false
```

即使已赚取大部分权利金，只要没有触发止损或到期，也继续持有。

### 17.2 权利金止损

B0 唯一提前退出规则是权利金硬止损：

```text
premium_stop_multiple = 2.5
premium_stop_requires_daily_iv_non_decrease = false
```

含义：

```text
当期权价格上涨到开仓权利金的 2.5 倍时，触发止损。
```

该止损不要求 IV 没在下降，属于硬止损。

### 17.3 止损防跳变

B0 保留盘中止损确认：

```text
intraday_stop_liquidity_filter_enabled = true
intraday_stop_min_trade_volume = 3
intraday_stop_min_group_volume_ratio = 0.1
intraday_stop_confirmation_enabled = true
intraday_stop_confirmation_observations = 2
intraday_stop_confirmation_use_full_minutes = true
intraday_stop_confirmation_revert_ratio = 0.98
intraday_stop_confirmation_max_minutes = 30
intraday_stop_confirmation_use_cumulative_volume = true
```

目标是避免深虚低流动性合约因为瞬时异常报价触发假止损。

### 17.4 到期处理

B0 默认持有到期。

到期处理：

- 虚值到期：期权价值归零，卖方保留权利金。
- 实值到期：按内在价值结算，亏损计入 NAV。

当前相关参数：

```text
pre_expiry_exit_dte = 1
expiry_dte = 1
```

B0 不做临近到期提前滚仓或主动平仓。

## 18. 再开仓与冷却

B0 不启用止损后冷却和重开规则：

```text
reentry_plan_enabled = false
cooldown_days_after_stop = 0
```

因此，如果一个品种某方向止损，只要后续仍满足开仓条件，理论上可以重新进入。

## 19. Greeks 与归因

B0 不用 Greeks 做开仓或强平约束：

```text
greeks_exit_enabled = false
greeks_vega_warn = 1.0
```

但系统仍记录并输出组合 Greeks 和 PnL 归因，用于事后分析：

- cash_delta
- cash_vega
- cash_gamma
- delta_pnl
- gamma_pnl
- theta_pnl
- vega_pnl
- residual_pnl

注意：B0 是裸卖基准，理论上组合通常是 short vega 和 short gamma。后续优化目标之一，是让策略在择时版本里改善 Vega PnL，而不是仅依赖方向收益。

## 20. 输出文件与诊断

标准 B0 长回测应至少输出：

```text
output/nav_<tag>.csv
output/orders_<tag>.csv
output/diagnostics_<tag>.csv
output/report_<tag>.md
```

其中 `<tag>` 推荐为：

```text
s1_b0_standard_stop25_allprod_2022_latest
```

必须检查的结果：

- 年化收益、累计收益、最大回撤、Sharpe、Calmar。
- 年度、季度、月份收益。
- 平均保证金、最高保证金、当前保证金。
- 活跃品种数、活跃合约数、持仓手数。
- P/C 手数比、P/C 权利金、P/C PnL。
- ETF、股指、商品三类市场分别贡献。
- 到期虚值归零次数。
- 到期实值亏损。
- 止损次数、止损品种、止损成交偏离。
- 手续费和滑点成本。
- Greek 归因中的 theta、vega、delta、gamma、residual。
- 2025 年 4 月和 7 月等升波阶段表现。

## 21. B0 已知局限

B0 的局限是研究设计的一部分，不是 bug。

已知局限包括：

- 不判断波动率贵贱。
- 不判断未来 realized volatility 是否抬升。
- 不判断 IV 是否处于下降通道。
- 不做流动性候选过滤。
- 不做板块和相关性约束。
- 不限制 cash delta、cash gamma 和 cash vega。
- 不区分 ETF、股指、商品的不同风险属性。
- 不止盈，可能保留收益风险比已经很差的尾部仓位。
- 每侧最多 5 个合约，可能低估更宽执行价梯队的效果。
- 品种按代码排序，边际资金分配可能受字符串顺序影响。
- 在某些阶段 P/C 会自然偏移，形成方向暴露。

后续版本应围绕这些局限逐一做增量实验。

## 22. 已确认的性能优化待办

当前 B0 全周期回测越往后越慢，主要来自可交易品种、持仓数量和分钟级止损确认的计算量增加。为了不影响正在运行的标准 B0 结果口径，以下优化应在本轮跑完后单独实现和验证。

第一优先优化：

- 用日内最高价先预筛止损候选。
- 对卖方持仓，若当日期权日内最高价未触及 `entry_premium * premium_stop_multiple`，则该合约当天不进入分钟级止损扫描。
- 只有通过日线预筛的合约，才继续读取分钟数据并执行现有的跳价过滤、连续确认和成交口径。
- 该优化只减少无效分钟扫描，不改变 `2.5x` 硬止损、异常跳价过滤、成交滑点和最终平仓规则。
- 优化完成后必须用同一小样本对比优化前后的 NAV、止损订单和逐日持仓，确认结果完全一致或仅存在可解释的浮点误差。

这项优化的目标是把 B0 的全品种长周期回测速度明显压缩，同时保持策略结果口径不变。

## 23. 后续对比版本命名建议

后续所有版本都应清晰标注相对 B0 改了什么。

建议：

```text
B0-standard：当前标准 B0，top 5，无法主动择时。
B0-10：每侧最多 10 个执行价，其余不变。
B0-all：每侧所有 abs(delta)<=0.10 合约，其余不变。
B1-light-liquidity：B0 + 轻度成交量/OI 候选过滤。
B1-mid-liquidity：B0 + 中度成交量/OI 候选过滤。
B1-strict-liquidity：B0 + 严格成交量/OI 候选过滤。
B2-regime：B1 基础上加入最小波动率环境过滤。
B3-risk-budget：加入组合风险预算和板块/相关性约束。
```

命名原则：

```text
一次只改变一个主要变量；
每个变量必须能解释；
不能把多个规则混在一个标签里，然后声称策略变好了。
```

## 24. 后续版本必须相对 B0 回答的问题

任何新版本都必须回答：

- 收益是否提高。
- 最大回撤是否降低。
- Calmar 是否改善。
- Sharpe 是否改善。
- 保证金使用率是否明显下降。
- 单位保证金收益是否改善。
- P/C 偏移是否降低。
- cash delta 是否更稳定。
- Vega PnL 是否改善。
- 止损次数是否下降。
- cheap OTM 跳价止损是否下降。
- 活跃品种和合约是否过度减少。
- 关键升波月份是否更稳。
- 是否更接近乐得画像。

如果一个版本只是通过少开仓降低回撤，但收益也明显消失，则不是有效升级。

如果一个版本收益更高，但靠单方向、单品种或单时期承担更大尾部，则也不是有效升级。

## 25. 标准 B0 一句话定义

标准 B0 是一个全品种、代码顺序扫描、次近到期、P/C 双侧、低 Delta、每侧最多 5 个执行价、品种等权、P/C 预算各半、真实费用和保证金、50% 保证金目标、持有到期、2.5x 权利金硬止损、无 IV/RV/趋势/流动性候选过滤/组合风控的朴素卖权基准。

它是后续所有 S1 卖权策略升级的共同参照系。
