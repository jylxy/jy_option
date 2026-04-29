# S1 B2c 因子用途重分类：不只做风险预算倾斜

## 1. 背景

B2c 的组合回测显示，品种间权利金质量倾斜可以提高最终 NAV 和权利金留存率。但进一步做因子分层检验后，一个更重要的问题出现了：

```text
B2c 里的因子，不一定都应该进入同一个“预算倾斜函数”。
```

有些因子更像硬筛选条件，有些更适合选腿排序，有些更适合预测止损概率，有些才适合做品种间预算。有些因子如果混在综合分里，反而会被稀释。

因此，下一步不应只是继续调 `s1_b2_tilt_strength`，而应先把因子按经济功能重新分类。

## 2. 我们真正要预测的标签

S1 因子不是预测标的方向，而是预测卖权承保质量。核心标签是：

```text
future_net_pnl_per_premium
future_retained_ratio
future_stop_avoidance
future_stop_loss_avoidance
```

这四个标签对应四类策略问题：

- 这笔权利金最终赚不赚钱。
- 这笔权利金能不能留住。
- 这笔交易中途会不会触发 2.5x 止损。
- 如果触发止损，损耗会不会特别重。

不同因子可能预测不同标签。把它们合成一个预算分数，可能会让信号变钝。

## 3. 初步分层发现

基于 B2c 已成交样本的分层诊断，初步观察如下：

### 3.1 合约级

在合约级，以下因子对 `net_pnl_per_premium` 的 Rank IC 更强：

- `friction_ratio`
- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `cost_liquidity_score`
- `gamma_rent_penalty`

这说明它们更像“单张合约质量”或“执行价质量”因子，不一定应该只在品种预算层使用。

### 3.2 品种-方向级

在品种-方向级，以下因子更适合看预算或方向侧质量：

- `b2_product_score`
- `variance_carry`
- `theta_vega_efficiency`
- `premium_to_iv10_loss`
- `premium_to_stress_loss`

但综合 `premium_quality_score` 本身不一定总是最强，说明 B2c 的综合分可能把强因子和弱因子混在一起。

### 3.3 止损概率

`theta_vega_efficiency` 对 `stop_avoidance` 的解释力更突出。这说明它可能不是单纯收益因子，而更像“止损概率控制因子”。

这对策略设计很重要：

```text
一个因子如果能降低止损概率，未必应该提高收益排序；
它可能更适合控制单笔手数、同方向叠加、临近到期暴露或止损冷却。
```

## 4. 因子用途重分类

### 4.1 硬筛选因子

这些因子应考虑从“排序”升级为“门槛”。

候选：

- `friction_ratio`
- 极低 `premium_to_iv10_loss`
- 极低 `premium_to_stress_loss`

逻辑：

```text
如果手续费、滑点或 IV shock 覆盖明显不够，
这张保险单本身就不值得承保，
不应该只是给低预算，而应直接不做。
```

建议实验：

- FEE1：`friction_ratio` 低分层直接过滤。
- IVS1：`premium_to_iv10_loss` 最差 20% 过滤。
- STRESS1：`premium_to_stress_loss` 最差 20% 过滤。
- COMBO1：以上三个只做红线过滤，不改变预算函数。

### 4.2 选腿排序因子

这些因子更适合同品种同方向内部排序，而不是品种间预算。

候选：

- `premium_to_iv10_loss`
- `premium_to_stress_loss`
- `gamma_rent_penalty`
- `friction_ratio`

逻辑：

```text
在同一个 product + side + expiry 内，
先剔除最差合约，
再按 IV shock / stress 覆盖和 gamma rent 选择执行价。
```

这可能比当前“邻近 Delta + B2 综合排序”更合理。

建议实验：

- LEG1：同品种同方向内，按 `premium_to_iv10_loss` 排序选腿。
- LEG2：按 `premium_to_stress_loss` 排序选腿。
- LEG3：按 `premium_to_iv10_loss - gamma_rent_penalty` 组合选腿。

### 4.3 品种-方向预算因子

这些因子才适合预算倾斜。

候选：

- `variance_carry`
- `theta_vega_efficiency`
- `b2_product_score`
- 后续 B3 的 `b3_iv_shock_score`
- 后续 B3 的 `b3_vomma_score`

逻辑：

```text
预算层应该决定今天哪些品种/方向多承保，
而不是替代合约选择。
```

建议实验：

- B2_budget_slim：预算层只保留品种-方向级 IC 稳定的因子。
- B2_leg_quality：合约级因子只用于选腿，不进入品种预算。

### 4.4 止损概率因子

这些因子不一定提高平均收益，但可以控制中途止损。

候选：

- `theta_vega_efficiency`
- `gamma_rent_penalty`
- `premium_to_stress_loss`
- `premium_to_iv10_loss`

用途：

- 控制单合约最大手数。
- 控制同品种同方向多执行价铺单数量。
- 控制临近到期暴露。
- 触发更严格的止损确认。
- 止损后重开时作为冷却解除条件之一。

建议实验：

- STOP1：低 `theta_vega_efficiency` 分层降低手数。
- STOP2：低 `theta_vega_efficiency` + 高 `gamma_rent_penalty` 禁止新增邻近执行价。
- STOP3：止损后重开必须满足 `theta_vega_efficiency` 不在低分层。

### 4.5 环境调制因子

这些因子不应直接高/低排序，而应与环境组合使用。

候选：

- `b3_vol_of_vol_proxy`
- `b3_vov_trend`
- `vol_regime`
- `contract_iv_change_5d`

逻辑：

```text
低 VOV 不一定好，可能只是低保费；
高 VOV 不一定坏，可能是卖方保费最肥的时候；
关键是高 VOV 是否还在加速，还是已经稳定/回落。
```

建议实验：

- VOV_INV1：直接反向测试高 VOV 分层。
- VOV_SAFE1：只在高 VOV 且 `vov_trend <= 1` 时加权。
- VOV_RISK1：高 VOV 且 `vov_trend > 1` 时降权或禁做。

## 5. 下一版策略含义

B2c 的下一版不应是：

```text
继续调大/调小综合预算倾斜强度。
```

更合理的结构是：

```text
1. 红线过滤：先排除明显不值得承保的合约。
2. 选腿排序：在同品种同方向内选风险补偿最好的执行价。
3. 品种预算：只用真正适合横截面预算的因子分配 product-side 额度。
4. 止损概率控制：对高止损概率层降低手数或限制铺单。
5. 环境调制：在降波、高保费且风险不再加速时增加预算。
```

这比单一综合分更符合卖权承保业务。

## 6. 最优先实验

建议优先做三组：

### Experiment A：红线过滤

在 B2c 基础上，只增加：

```text
friction_ratio 最差层过滤；
premium_to_iv10_loss 最差层过滤；
premium_to_stress_loss 最差层过滤。
```

目标：减少明显低质量权利金，不明显降低毛权利金发行速度。

### Experiment B：选腿重排

不改变品种预算，只改变同品种同方向内合约排序：

```text
先按 IV shock coverage / stress coverage / gamma rent 选腿，
再按原规则决定手数。
```

目标：验证合约级强 IC 能否转化为策略收益。

### Experiment C：预算瘦身

把 B2c 预算分数改成更少、更纯的品种-方向因子：

```text
variance_carry
theta_vega_efficiency
b2_product_score
```

同时把合约级因子移到选腿和过滤层。

目标：避免综合分稀释强因子。

## 7. 重要限制

当前分层检验基于已成交样本，不是完整候选池。它回答的是：

```text
在策略实际交易过的样本里，哪些因子有解释力？
```

它暂时不能完全回答：

```text
如果某些候选未成交，它们的未来表现是否也符合分层规律？
```

如果上述实验显示有效，后续应让回测引擎落盘完整候选池，做 all-candidate universe factor test。
