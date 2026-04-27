# S1 B0 开仓逻辑实现备忘

## 1. 文档目的

本文档记录当前标准 B0 的实际开仓逻辑，避免后续研究过程中对“B0 到底怎么开仓”产生口径漂移。

当前标准 B0 对应配置文件：

```text
config_s1_baseline_b0_all_products_stop25.json
```

当前标准回测标签：

```text
s1_b0_standard_stop25_allprod_2022_latest
```

本文档描述的是当前代码实现口径，不是未来优化版策略建议。

## 2. 开仓决策时点

系统每日收盘后生成下一交易日待开仓指令。

流程简化为：

```text
当日收盘数据
  -> 更新持仓估值和到期/止损状态
  -> 扫描全品种期权链
  -> 生成 S1 待开仓订单
  -> 下一交易日按当前执行口径成交
```

因此，B0 不是当日看到信号当日成交，而是 T 日收盘生成计划，T+1 日按执行规则成交。

当前执行相关配置：

```text
volume_limit_pct = 1.0
execution_slippage_enabled = true
execution_open_slippage_pct = 0.002
```

## 3. 品种池与扫描顺序

B0 使用当天系统可读到的全部品种，不设置人工白名单。

进入当日扫描的前提是：

- 品种在当天有期权链数据。
- 品种属于当前 product_pool。
- 该品种通过基础上市/观察期过滤。

当前 B0 的产品观察期参数为：

```text
product_min_listing_days = 0
product_observation_months = 0
product_min_daily_oi = 0
```

也就是说，标准 B0 不额外要求上市后观察期。

### 3.1 扫描顺序

B0 模式下，品种顺序是非常朴素的字符串排序：

```python
sorted_products = sorted(product_frames)
```

因此，当前 B0 不按以下因素排序：

- 不按成交量。
- 不按权利金厚度。
- 不按 IV/RV。
- 不按保证金效率。
- 不按板块。
- 不按 ETF/股指/商品优先级。
- 不按最近表现。
- 不按降波状态。

### 3.2 顺序的影响

虽然 B0 的预算设计是品种等权，但扫描顺序仍可能在边际上影响结果。

原因是：

- 组合总保证金上限是 50%。
- 每个品种的合约乘数和单张保证金不同。
- 手数只能取整数。
- 某些品种 P/C 候选不足。
- 当组合接近总保证金上限时，后扫描到的品种更容易因为剩余预算不足而开不进去。

不过，B0 每个品种理论上只拿一份等权预算，不是前面的品种无限吃预算。

## 4. 组合与单品种预算

核心预算参数：

```text
margin_cap = 0.50
s1_margin_cap = 0.50
s1_baseline_equal_weight_products = true
s1_baseline_equal_weight_contracts = true
```

B0 模式下，如果当天有 N 个候选品种，则单品种目标保证金预算为：

```text
单品种预算 = S1 保证金上限 / 当日候选品种数
```

代码口径为：

```text
baseline_product_margin_per = s1_cap / len(candidate_products)
```

每个品种内部，Put 和 Call 两侧各拿一半预算：

```text
单方向预算 = 单品种预算 / 2
```

所以 B0 的设计是：

```text
全品种等权
品种内 P/C 预算各半
方向内合约等权
```

实际保证金使用率可能偏离 50%，原因包括整数手数、候选不足、单张保证金过大、成交量限制和已有仓位占用。

## 5. 到期选择

当前 B0 使用“次近到期”：

```text
s1_expiry_mode = nth_expiry
s1_expiry_rank = 2
```

实际含义：

```text
对同一品种，当天所有有效到期日按 DTE 从小到大排序，取第 2 个到期日。
```

注意：

- 这不是自然月份意义上的“下个月”。
- 商品期权合约月份并不总是严格对应自然次月，因此使用 DTE 排序更稳。
- 如果当天该品种只有 1 个有效到期日，则该品种当天不开仓。
- DTE 必须大于 0。

## 6. P/C 选择

当前 B0 不做择边，默认同时尝试 Put 和 Call：

```text
s1_side_selection_enabled = false
s1_conditional_strangle_enabled = false
s1_trend_confidence_enabled = false
```

因此每天每个品种的次近到期上，系统都会尝试：

```text
Put 一侧
Call 一侧
```

B0 不会因为趋势、动量、IV 环境或 P/C 评分主动只卖某一侧。

实际结果中 P/C 仍会偏离 1:1，因为：

- 某一侧没有合格低 Delta 合约。
- 某一侧权利金不足以覆盖手续费。
- 某一侧单张保证金太高。
- 某一侧成交量限制导致不能成交。
- 某一侧已有仓位、止损或到期结构造成自然偏移。

## 7. 卖腿候选筛选

### 7.1 Put 候选

Put 候选必须满足：

```text
option_type = P
moneyness < 1.0
delta < 0
abs(delta) >= s1_sell_delta_floor
abs(delta) <= s1_sell_delta_cap
option_close >= 0.5
```

当前 B0 参数为：

```text
s1_sell_delta_floor = 0.0
s1_sell_delta_cap = 0.10
```

因此 Put 侧本质是：

```text
卖 OTM Put，abs(delta) <= 0.10，价格不低于 0.5。
```

### 7.2 Call 候选

Call 候选必须满足：

```text
option_type = C
moneyness > 1.0
delta > 0
delta >= s1_sell_delta_floor
delta <= s1_sell_delta_cap
option_close >= 0.5
```

当前 B0 下本质是：

```text
卖 OTM Call，delta <= 0.10，价格不低于 0.5。
```

### 7.3 成交量和持仓量过滤

当前 B0 不设置最低成交量和最低持仓量：

```text
s1_min_volume = 0
s1_min_oi = 0
```

但最终成交仍会受执行层成交量限制影响：

```text
volume_limit_pct = 1.0
```

也就是说，候选筛选阶段不因成交量剔除，但真实开仓成交阶段不能超过可成交量约束。

### 7.4 费用过滤

当前 B0 设置：

```text
s1_min_premium_fee_multiple = 1.0
```

候选必须满足：

```text
option_close * multiplier >= open_fee + close_fee
```

即单张合约权利金现金额至少覆盖一开一平的往返手续费。

这会过滤掉权利金太薄的 cheap OTM 合约。

## 8. 候选排序与 Top 5

当前 B0 使用：

```text
s1_target_abs_delta = 0.10
s1_baseline_max_contracts_per_side = 5
```

候选排序以接近 `abs(delta)=0.10` 为核心：

```text
delta_dist = abs(abs(delta) - 0.10)
delta_dist 越小越靠前
```

在同等条件下，排序使用稳定排序，并用 `option_code` 作为最终 tie-breaker，保证结果可复现。

每个品种、每个方向、每个扫描日最多取前 5 个合约：

```text
Put 最多 5 个执行价
Call 最多 5 个执行价
```

这意味着 B0 不是“所有 `abs(delta)<=0.10` 的合约全卖”，而是只卖每侧最接近 0.10 delta 的最多 5 个执行价。

## 9. 手数计算

对每个品种、每个方向，系统先得到最多 5 个候选合约。

如果方向内有 N 个候选合约，则方向剩余预算按剩余候选合约等权切分：

```text
单合约预算 = 当前方向剩余保证金预算 / 剩余候选合约数
```

然后按单张保证金计算手数：

```text
target_qty = floor(单合约预算 / 单张保证金)
```

如果预算足够小，系统仍会至少尝试 1 手：

```text
target_qty = max(1, floor(单合约预算 / 单张保证金))
```

但最终手数还要经过组合约束检查。

## 10. 组合约束检查

每个候选合约实际开仓前，会检查：

```text
组合总保证金 + 新仓保证金 <= margin_cap * NAV
S1 保证金 + 新仓保证金 <= s1_margin_cap * NAV
```

当前 B0 下：

```text
margin_cap = 0.50
s1_margin_cap = 0.50
```

因此，B0 最终不会主动超过 50% 总保证金和 50% S1 保证金约束。

当前 B0 关闭了组合风控模块：

```text
portfolio_construction_enabled = false
portfolio_bucket_control_enabled = false
portfolio_corr_control_enabled = false
portfolio_stress_gate_enabled = false
```

所以不会因为板块、相关性、cash gamma、cash vega 或 stress budget 主动限制开仓。

## 11. 已有仓位与加仓

当前 B0 允许同一品种、同一方向、同一到期继续加仓：

```text
s1_allow_add_same_side = true
s1_allow_add_same_expiry = true
```

含义：

- 同一品种已经有 S1 Put 仓位，后续交易日仍可继续开 Put。
- 同一品种已经有 S1 Call 仓位，后续交易日仍可继续开 Call。
- 同一品种同一到期已经有仓位，后续仍可继续开。

真正限制加仓的是：

- 组合总保证金 50%。
- S1 总保证金 50%。
- 当日该品种等权预算。
- 当日该方向预算。
- 候选是否存在。
- 成交量限制。
- 单张保证金和整数手数。

## 12. B0 明确不做的开仓判断

当前标准 B0 开仓时明确不做：

- 不看 IV 分位。
- 不看 IV/RV carry。
- 不看 IV 是否下降。
- 不看 RV 是否上升。
- 不看 forward vega。
- 不看趋势和动量。
- 不做 P/C 主动偏移。
- 不做板块分散排序。
- 不做相关性排序。
- 不做 cash Greeks 限制。
- 不做 stress score 排序。
- 不按 premium/stress 或 theta/stress 排序。
- 不按成交量优先选择品种。

B0 的价值正是保留这种朴素性，用来观察原始卖权暴露本身的收益和风险。

## 13. 当前实现的一句话总结

当前 B0 每天对全品种按代码排序扫描，选择每个品种的次近到期，同时尝试卖 Put 和 Call；每侧筛选 OTM、价格不低于 0.5、Delta 不超过 0.10、权利金覆盖往返手续费的合约；按接近 0.10 Delta 排序，每侧最多取 5 个执行价；品种等权、P/C 预算各半、合约等权分配保证金；允许同品种同方向同到期后续继续加仓，直到组合 50% 保证金约束、成交量限制或候选不足挡住。

## 14. 后续若要调整 B0，需要特别记录

后续如果进行 B0 敏感性实验，应单独记录以下变化：

- 每侧最大合约数从 5 改为 10 或 all。
- 品种排序从代码排序改为流动性、市场分层或权利金效率排序。
- 到期从次近到期改为近月、目标 DTE 或多到期并行。
- P/C 从预算各半改为按候选数、保证金、权利金或趋势动态分配。
- 候选排序从接近 0.10 Delta 改为 premium/margin、theta/margin 或 premium/stress。
- 是否加入最低成交量、最低 OI、bid-ask 或异常报价过滤。

这些变化都会改变 B0 的基准含义，不能和标准 B0 混在同一个标签下比较。
