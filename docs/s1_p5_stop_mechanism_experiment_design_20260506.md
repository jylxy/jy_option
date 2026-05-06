# S1 P5 止损机制实验设计

## 1. 研究背景

P4 的结果已经比较明确：在当前 S1 合约筛选、P3/P3B 品种池和执行口径下，单纯放宽权利金止损倍数并没有改善策略。

截至 2026-03-31：

| 版本 | 1.5X 总收益 | 2.0X 相对超额 | 2.5X 相对超额 | 3.0X 相对超额 | 不止损相对超额 |
|---|---:|---:|---:|---:|---:|
| P3 | 26.57% | -0.67% | -2.58% | -8.30% | -3.95% |
| P3B | 30.11% | -5.81% | -8.12% | -11.85% | -19.60% |

这说明当前策略的问题不应该继续用“更宽止损”解决。更合理的方向是：

1. 保留 1.5X 的早期风险控制能力。
2. 降低被同组 ladder 连带平仓的无效损耗。
3. 在真风险恶化时仍保留 2.5X 硬止损。

## 2. 当前实现审计

当前 S1 开仓时的 `group_id` 为：

```text
S1_{product}_{option_type}_{expiry}_{open_date}
```

因此当前止损不是“同品种同方向所有历史仓位全平”，而是：

```text
同一天开仓
+ 同品种
+ 同方向
+ 同到期日
的一组 ladder 合约一起平仓
```

当前逻辑优点是简单、保护快、能快速降低同组短 Gamma / 短 Vega 暴露。缺点是只要同组中一个合约触发 1.5X，同组其他未触发合约也会被平掉，可能降低权利金留存率。

## 3. 核心研究问题

本轮 P5 要回答三个问题：

1. 当前 group 止损是否过度保守？
2. 单合约止损是否能提高 Retention Rate，同时不显著放大 Tail / Stop Loss？
3. 分层止损是否能保留 1.5X 的风险控制，又减少无效平仓？

用 S1 收益公式表达：

```text
S1 net return
= Premium Pool × Deployment Ratio × Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

P5 主要希望改善 `Retention Rate` 和 `Tail / Stop Loss`，但不能以显著牺牲 `Premium Pool`、放大最大回撤或增加不可接受的交易成本为代价。

## 4. 实验原则

### 4.1 控制变量

除止损机制外，其余保持 P3B 1.5X 主线不变。

主战场选择 P3B，原因是：

- P3B 是当前最优主线。
- P3B 对止损机制最敏感。
- P3B 的 P4 结果显示宽止损明显恶化，说明止损机制有优化价值，但不能简单放宽倍数。

P3 仅作为第二阶段稳健性验证。

### 4.2 不使用未来信息

止损判断只能使用当前分钟及此前可见数据：

- 当前分钟期权成交/收盘价格。
- 当前分钟成交量。
- 已有持仓的开仓价、开仓日期、数量、group_id。
- 已有日内确认状态。

不能使用未来分钟价格、未来日收盘、未来是否到期归零等信息决定是否止损。

### 4.3 保留异常价与流动性确认

P5 必须继续沿用当前已有的：

- 日内最高价预筛。
- 低成交量止损过滤。
- 连续观察确认。
- 单笔/累计成交量确认。
- 同日 VWAP 开仓不立即盘中平仓保护。

P5 只改变“触发后平哪些仓位、平多少仓位”，不改变异常价确认机制。

## 5. 实验 A：止损粒度实验

### 5.1 实验目的

验证当前 group 止损是否把未触发的同组 ladder 合约过早平掉。

### 5.2 实验矩阵

| 实验 | 止损倍数 | 止损范围 | 说明 |
|---|---:|---|---|
| A0 | 1.5X | group | 当前基准，复用 P3B 1.5X |
| A1 | 1.5X | contract | 只平触发止损的单个持仓 |
| A2 | 1.5X | same_code | 若同一合约存在多批次持仓，则平同代码持仓 |
| A3 | 1.5X | group_no_ladder | 仅平同 group 中价格也达到 1.5X 的合约 |

### 5.3 推荐优先级

第一优先级是 A1 和 A3。

A1 是最干净的单合约实验，回答“完全逐笔止损是否更好”。

A3 更贴近交易直觉：同组里没触发的合约不平，但如果同组多个合约都达到止损线，则一起平。它比 A1 更稳健，也比当前 group 更细。

A2 用于检查多批次同代码持仓是否需要统一处理。若同代码多批次很少，A2 可以不跑。

### 5.4 预期结果解释

| 结果 | 解释 |
|---|---|
| A1/A3 收益提高，回撤不变或下降 | 当前 group 止损过度保守，值得升级 |
| A1 收益提高但回撤变大，A3 更平衡 | 应采用 A3 作为主线 |
| A1/A3 收益下降、回撤变大 | 当前 group 止损虽然粗，但有效控制同组风险 |
| A1/A3 止损率下降但尾部亏损增加 | 说明被连带平仓的腿本来是在保护尾部，不应完全拆开 |

## 6. 实验 B：分层止损实验

### 6.1 实验目的

P4 证明简单 2.5X/3.0X 宽止损不优，但 1.5X group 又可能过早。分层止损的目标是：

```text
1.5X = 风险预警与轻量减仓
2.0X = 单合约风险确认
2.5X = 最终硬止损
```

### 6.2 实验矩阵

| 实验 | 1.5X | 2.0X | 2.5X | 说明 |
|---|---|---|---|---|
| B0 | group 全平 | 无 | 无 | 当前基准，复用 P3B 1.5X |
| B1 | 触发合约减半 | 无 | 触发合约剩余全平 | 最简单分层 |
| B2 | 触发合约减半 | 触发合约全平 | 同 group 剩余硬平 | 推荐主测版本 |
| B3 | 仅预警不交易 | 触发合约减半 | 触发合约全平 | 更宽容版本 |
| B4 | 触发合约全平 | 无 | 同 group 剩余硬平 | 单合约先走、极端再清组 |

### 6.3 推荐优先级

第一轮只跑 B2 和 B4。

B2 是最合理的实盘化版本：

- 1.5X 先减半，减少风险但不完全放弃 theta。
- 2.0X 单合约全平，承认真风险继续恶化。
- 2.5X 同组硬平，避免同组风险失控。

B4 是更简洁的版本：

- 1.5X 触发合约全平。
- 2.5X 同组剩余硬平。

B4 的执行更简单，若效果接近 B2，后续更适合实盘。

## 7. 参数化实现要求

需要新增配置，但默认保持当前行为不变。

```json
{
  "s1_stop_close_scope": "group",
  "s1_layered_stop_enabled": false,
  "s1_layered_stop_levels": [
    {
      "multiple": 1.5,
      "action": "reduce",
      "ratio": 0.5,
      "scope": "contract"
    },
    {
      "multiple": 2.0,
      "action": "close",
      "scope": "contract"
    },
    {
      "multiple": 2.5,
      "action": "close",
      "scope": "group"
    }
  ]
}
```

### 7.1 `s1_stop_close_scope`

| 值 | 含义 |
|---|---|
| `group` | 当前行为，平同 `group_id` 全部持仓 |
| `contract` | 只平触发持仓 |
| `same_code` | 平同一合约代码的持仓 |
| `triggered_in_group` | 平同 group 中也达到止损线的合约 |

### 7.2 `s1_layered_stop_levels`

每一层包括：

| 字段 | 含义 |
|---|---|
| `multiple` | 相对开仓权利金倍数 |
| `action` | `warn`、`reduce`、`close` |
| `ratio` | `reduce` 时减仓比例 |
| `scope` | `contract`、`same_code`、`triggered_in_group`、`group` |

### 7.3 减仓实现约束

若持仓数量无法按比例精确拆分：

- `reduce 0.5` 使用向上取整，至少减 1 手。
- 减仓后剩余持仓应保留原始开仓价、开仓日期、entry Greeks 和 entry meta。
- 订单记录中需要能区分 `sl_s1_reduce`、`sl_s1_close`、`sl_s1_hard`。

## 8. 第一轮实验组合

第一轮只跑 P3B，避免实验过多。

| 实验名 | 基准 | 配置 |
|---|---|---|
| P5_A0 | P3B 1.5X | 已完成，当前 group 全平 |
| P5_A1 | P3B contract stop | `s1_stop_close_scope=contract`, `premium_stop_multiple=1.5` |
| P5_A3 | P3B triggered-in-group stop | `s1_stop_close_scope=triggered_in_group`, `premium_stop_multiple=1.5` |
| P5_B2 | P3B layered main | 1.5X contract reduce 50%，2.0X contract close，2.5X group close |
| P5_B4 | P3B simple layered | 1.5X contract close，2.5X group close |

如果第一轮确认有效，再补跑 P3：

| 实验名 | 用途 |
|---|---|
| P5_P3_A1 | 验证单合约止损在 P3 是否同样有效 |
| P5_P3_B2 | 验证分层止损在 P3 是否同样有效 |

## 9. 评价指标

### 9.1 总体绩效

| 指标 | 目标 |
|---|---|
| 总收益 / 年化收益 | 不低于 P3B 1.5X，至少不能明显低于 |
| 最大回撤 | 不高于 P3B 1.5X 太多，容忍上限建议 2.3% |
| Sharpe / Calmar | 不低于 P3B 1.5X |
| 最差单日 / 最差月 | 不能显著恶化 |

### 9.2 收益拆解

| 指标 | 目标 |
|---|---|
| Open Premium Pool | 不应因为止损机制下降 |
| Deployment Ratio | 不应因为占用过多亏损仓位导致新仓减少 |
| Retention Rate | 应明显提高 |
| Stop Loss / Open Premium | 应下降或不升 |
| Cost / Slippage | 不应因分层减仓大幅上升 |

### 9.3 止损质量

| 指标 | 解释 |
|---|---|
| `sl_rows / open_rows` | 止损频率 |
| `sl_loss_rows / open_rows` | 真实亏损止损频率 |
| `sl_loss_cash / open_premium` | 真实止损损耗 |
| `premium_retained_pct` | 止损后的权利金留存 |
| `sl_gain_rows / sl_rows` | 被止损规则处理但正收益的比例 |
| `post_stop_otm_expiry_ratio` | 若不止损，最终归零或虚值到期的比例 |
| `post_stop_adverse_ratio` | 止损后继续不利移动比例 |

### 9.4 组内连带平仓分析

P5 必须新增一个诊断表：

```text
triggered contract
group mates closed
group mates not triggered at close time
group mate later max price
group mate final expiry value
group mate hypothetical pnl if held
```

它要回答：

```text
当前 group 止损平掉的未触发合约，事后看是保护了尾部，还是损失了 theta？
```

### 9.5 Greek 归因

| 指标 | 目标 |
|---|---|
| Theta PnL | 不应明显下降 |
| Vega PnL | 不能更负，最好改善 |
| Gamma PnL | 不能因留下同组仓位而显著恶化 |
| Delta PnL | 不能让策略更像方向交易 |

## 10. 通过与否决标准

### 10.1 可升级为主线

满足以下条件之一：

1. 收益高于 P3B 1.5X，最大回撤不高于 2.3%。
2. 收益接近 P3B 1.5X，但最大回撤下降或止损频率显著下降。
3. Retention Rate 明显提升，且 Vega/Gamma 损耗不扩大。

### 10.2 只能作为诊断

出现以下情况：

- 收益略高，但最大回撤明显超过 2.5%。
- 止损频率下降，但单次止损损耗上升。
- 分层止损增加过多交易成本。
- 改善只集中在少数月份或少数品种。

### 10.3 应否决

出现以下情况：

- 收益低于 P3B 1.5X 且回撤更大。
- Vega/Gamma 损耗显著扩大。
- 2022 年 7 月、2025 年 4 月、2026 年 3 月等压力期明显恶化。
- 组内未平仓腿在压力期继续恶化，说明 group 止损有必要。

## 11. 推荐执行顺序

第一阶段：实现参数化，不改变默认行为。

第二阶段：先跑 2022-01-01 至 2023-03-31 小样本，覆盖 2022 冲击和修复期。

第三阶段：若小样本不显著恶化，再跑 2022-01-01 至最新全样本。

第四阶段：用报告脚本输出 P5 专项分析，必须包括：

- NAV / 超额曲线。
- 回撤曲线。
- 止损频率与止损损耗。
- `product × side × delta bucket` 止损地图。
- 组内连带平仓事后分析。
- Greek PnL 差异。
- Premium Pool / Retention / Tail Loss 拆解。

## 12. 当前先验判断

我的先验排序是：

```text
B2 > A3 > B4 > A1 > A0
```

理由：

- A0 当前表现最好，但可能牺牲了部分 Retention。
- A1 最干净，但可能留下过多同组尾部风险。
- A3 比 A1 更稳健，只平同组中真正触发的腿。
- B4 执行简单，实盘友好。
- B2 最符合风险分层逻辑，但实现和交易记录更复杂。

如果 B2 或 A3 能在 P3B 上跑赢 A0，下一步 S1 主线就应从“单一 1.5X group 止损”升级为“细粒度止损 + 硬止损兜底”。
