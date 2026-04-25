# S1 v2 日频卖权策略设计文档

本文档固化 S1 下一版策略设计。S1 仍然定位为低 Delta 卖权、收取 theta 和波动率风险溢价的日频策略，但不再把“满足若干硬条件即可卖”作为核心，而是升级为：

```text
硬过滤
-> 候选质量评分
-> 波动状态评分
-> Greek 情景压力测试
-> 动态 stress budget sizing
-> 退出、冷却、降级和复盘归因
```

策略目标不变：在可接受尾部风险下，提高 S1 对组合的稳定收益贡献。新增一个硬质量目标：**S1 的 vega 归因需要为正**。这不是要求每天 vega 都赚钱，而是要求在主要样本、样本外和关键分层中，S1 不能长期依赖 delta 或 residual 赚钱，同时持续承担负 vega 损失。

## 1. 策略定位

S1 的收益来源应是：

```text
theta 收益
+ IV 回落或波动风险溢价兑现带来的 vega 收益
- gamma 路径损失
+/- delta 偶然方向收益或亏损
+/- skew、vanna、volga、报价和残差影响
- 交易成本
```

因此 S1 的核心问题不是“IV 明天会不会跌”，而是：

```text
当前这份 theta 和 short vega 暴露，是否足够补偿未来 RV、IV spike、skew steepen、gamma 路径和流动性成本？
```

我们不追求每笔都盈利，也不追求最高胜率。S1 要追求的是：长期 theta/vega carry 为正，左尾损失受控，最大亏损不能吃掉过多月份的 carry。

## 2. 当前实验给出的约束

截至 F4 系列，项目已有几个明确结论：

- `risk_reward` 候选评分有效。它显著改善了基础规则的止损、回撤和收益形态，应保留为候选排序核心。
- 单合约颗粒度上限有效。`F4b` 将单合约最大手数压到 `20` 后，单腿尾损和回撤明显下降，应作为后续基准组件。
- 趋势联动多执行价略有效。`F4c` 相比 `F4b` 小幅提高收益和 Sharpe，但它不是决定性突破。
- 简单放宽相关组宽度无效。`F4a` 说明活跃品种不足不是单一相关组 cap 问题。
- 简单收紧震荡无效。`F4f` 砍掉了好 carry，却没有解决 `range Call` 尾损。
- 简单降波 ramp 无效。`F4e-lite` 把 `falling_vol_carry` 和 `low_stable_vol` 一起放大，结果收益下降、回撤扩大。
- `falling_vol_carry` 是目前最清晰的正收益环境。`low_stable_vol` 不是天然安全状态，尤其是 `low_stable + range_bound + Call`。
- `range_bound / neutral / Call` 是当前最需要单独处理的坏结构之一。它即使 Delta 很低，也可能在假突破或 IV 回抽时亏损。

这些结论决定了下一版不能先粗暴加仓，而要先把“可卖的降波”和“不可轻易放大的低波”分开。

## 3. 总目标与硬约束

### 3.1 收益和回撤目标

组合层长期目标不变：

```text
组合年化收益目标：约 6%
组合最大回撤目标：不超过 2%
```

S1 作为卖权 carry 引擎的阶段目标：

```text
S1 年化贡献目标：3.5% - 5.0%
S1 最大回撤目标：1.2% - 1.8%
S1 Sharpe：至少 1.2
S1 Calmar：至少 2.0
```

如果 S1 为了提高收益而需要承担超过上述范围的尾部风险，则该收益不应被视为合格收益。

### 3.2 新增 vega 目标

S1 必须满足：

```text
总 vega_pnl > 0
falling_vol_carry 分层 vega_pnl > 0
主要正收益月份中 vega_pnl 不能长期为负
样本外 vega_pnl 不应显著为负
```

如果总收益为正但 vega 归因为负，需要进一步判断：

- 是否靠 delta 方向暴露赚钱。
- 是否靠 residual、异常报价或执行口径赚钱。
- 是否卖到了“低波薄权利金”，导致 theta 正但 vega/gamma 尾损更大。
- 是否 Black76/Greeks/IV 计算口径仍有问题。

只有当 theta 为正、vega 为正、gamma/delta 尾损受控时，S1 才能被视为真正的卖波 carry 策略。

## 4. 状态定义

S1 v2 不再把所有低波或降波都视为同一种安全状态。至少分为四类：

```text
1. Falling Vol Carry
   IV 仍有厚度，IV/RV carry 为正，IV 或 vol-of-vol 开始回落，RV 未重新抬升。

2. Normal Carry
   波动正常，IV/RV carry 为正，但没有明显降波红利。

3. Low Stable Vol
   IV 已经处于低位且平稳。该状态不等于安全，通常 theta 薄，不能自动加预算。

4. High Rising / Stress Vol
   IV、RV、skew 或相关性正在抬升。原则上不新开裸卖，已有仓位优先降风险。
```

关键原则：

```text
只在 Falling Vol Carry 里主动加预算。
Low Stable Vol 不加预算。
High Rising / Stress Vol 不新开裸卖。
```

## 5. Vol Compression Opportunity Score

为捕捉真正的降波期，引入 `Vol Compression Opportunity Score`，简称 `VCOS`。它用于判断“是不是值得给更多风险预算的降波环境”。

### 5.1 正向条件

`VCOS` 应奖励：

- `IV/RV carry > 0`，且扣除成本后仍为正。
- 当前 IV 分位不低，或至少权利金相对 stress loss 仍厚。
- ATM IV、目标合约 IV 或曲面 proxy 正在回落。
- RV5/RV20 不再抬升，最好开始回落。
- vol-of-vol 下降。
- skew 没有继续 steepen。
- 最近止损 cluster 少。
- 成交质量稳定，异常报价少。
- 没有重大事件窗口。

### 5.2 负向条件

`VCOS` 应惩罚：

- IV 已经很低，权利金很薄。
- IV 虽下降，但 RV 正在上升。
- put skew 或 call skew 快速 steepen。
- 标的刚突破震荡区间，尤其是 `range_bound + Call`。
- 合约流动性差、报价跳变、spread/mid 过宽。
- 最近同品种或同板块连续止损。
- 组合已有过高 gamma、vega 或 stress loss 集中。

### 5.3 使用方式

```text
VCOS 高：
    允许提高 S1 stress budget。
    优先提高活跃品种数和多执行价铺开。
    不优先提高单合约手数。

VCOS 中：
    正常预算。

VCOS 低：
    小预算或不做。

VCOS 低且 Low Stable Vol：
    不因低波稳定而加仓。
```

## 6. 候选评分体系

每个候选腿至少生成四类评分。

### 6.1 Richness Score

衡量权利金是否足够厚：

```text
Richness Score =
    premium / stress_loss
  + theta / stress_loss
  + premium / margin
  + IV/RV carry
  + term / skew richness
  - transaction cost penalty
```

原则：

- 不能只看 Delta。
- 不能只看权利金绝对值。
- 权利金小于手续费或成本缓冲时不做。
- `premium/stress` 和 `theta/stress` 太低的腿，即使 Delta 很低也不应开。

### 6.2 Regime Score

衡量当前环境是否适合卖：

```text
Regime Score =
    vol compression quality
  - RV acceleration risk
  - trend breakout risk
  - gap risk
  - skew steepening risk
  - stop cluster risk
  - correlation spike risk
```

### 6.3 Greek Safety Score

衡量候选腿和组合是否容易失控：

```text
Greek Safety Score =
    theta / scenario_loss
  - gamma concentration penalty
  - vega concentration penalty
  - vomma / volga convexity penalty
  - delta drift penalty
  - short strike concentration penalty
```

### 6.4 Liquidity / Execution Score

衡量能不能真实成交：

- bid-ask 或替代 spread 过宽则降权或跳过。
- volume、OI、分钟成交稳定性不足则降权或跳过。
- 价格跳变后很快回落的异常报价不能直接触发止损或开仓。
- 执行价格偏离信号价格过大时降低成交假设质量。

## 7. Vomma / Volga 的使用方式

S1 v2 可以引入 vomma/volga，但它不应单独作为开仓 alpha，而应作为 **short vol 非线性风险惩罚**。

直觉：

```text
short vega 在 IV 小幅下降时赚钱；
但 IV spike 时，vega 暴露可能因为 vomma/volga 非线性放大。
```

因此每个候选腿应计算或近似：

```text
linear_vega_loss = PnL(IV + 5 vol)
convex_vega_loss = PnL(IV + 10 vol) - 2 * PnL(IV + 5 vol)
volga_penalty = max(0, -convex_vega_loss)
```

如果 `volga_penalty / premium` 或 `volga_penalty / stress_budget` 太高，则该候选需要：

- 降低排序分。
- 降低可开手数。
- 只允许在 `VCOS` 很高时开。
- 或切换为更远 Delta / defined-risk 结构。

## 8. 情景压力测试

S1 v2 的开仓不应只依赖静态 Greeks，应使用 Black76 repricing 做候选和组合情景压力。

基础情景：

```text
Base: 当前 spot, 当前 IV
V1: spot 0%, IV +3 vol
V2: spot 0%, IV +8 vol
D1: spot -2%, IV +3 vol, put skew steepen
D2: spot -5%, IV +8 vol, put skew steepen
U1: spot +2%, IV +3 vol, call skew steepen
U2: spot +5%, IV +8 vol, call skew steepen
Crash: spot -8%, IV +15 vol, crash skew
Upside Shock: spot +8%, IV +10 vol, call squeeze
```

开仓通过条件：

```text
单腿 worst scenario loss <= 单腿预算
单品种 worst scenario loss <= 单品种预算
组合 worst scenario loss <= 组合预算
加仓后 cash gamma / cash vega / stress loss 不超限
加仓后同到期、同板块、同相关组不超限
```

情景压力应优先用于 sizing，而不是只做事后展示。

## 9. 结构选择

S1 当前主线仍是低 Delta 卖权，不默认切换为保护价差。但结构应支持按状态降级：

```text
Falling Vol Carry:
    可以做低 Delta 裸卖或类裸卖。
    可增加品种宽度和执行价梯队。

Normal Carry:
    正常低 Delta 裸卖，预算保守。

Low Stable Vol:
    不加预算。
    只做 premium/stress 足够厚的腿。
    range Call 需要额外限制。

High Rising / Stress Vol:
    原则上不新开裸卖。
    后续可研究 defined-risk credit spread，但不作为当前主线。
```

### 9.1 Range Call 特别规则

基于 F4c/F4f 结果，`range_bound + Call` 需要单独约束：

```text
range_bound + Call 只在 falling_vol_carry 或高 VCOS 时允许。
low_stable_vol + range_bound + Call 默认不加预算，必要时直接跳过。
```

若保留该腿，必须满足更严格条件：

- 更高 `premium/stress`。
- 更高 `theta/stress`。
- 更低 Delta cap。
- 更高 liquidity score。
- 更低单腿 scenario loss。
- 无异常报价或跳价风险。

## 10. 仓位与风险预算

仓位不按权利金绝对值决定，而按风险调整后的 carry 和压力亏损决定。

```text
raw_size = base_budget * score_multiplier * regime_multiplier * VCOS_multiplier

final_size = min(
    raw_size,
    margin_limit_size,
    stress_limit_size,
    scenario_loss_limit_size,
    cash_gamma_limit_size,
    cash_vega_limit_size,
    liquidity_limit_size,
    concentration_limit_size
)
```

预算原则：

- 降波期先增加 breadth，再增加单腿数量。
- 单合约手数上限默认保留 `20`。
- 若要提高到 `25` 或 `30`，只允许在 `VCOS` 高且 scenario loss 通过时。
- 单品种、板块、相关组和组合总 stress budget 必须统一裁剪。
- 重复止损的品种进入降级状态，不能因为再次降波信号就立即恢复满预算。

## 11. 退出、冷却与重开

### 11.1 硬止损

当前硬止损仍使用权利金倍数，后续可继续实验 `2.5x` 与状态联动版本。

原则：

```text
权利金止损不是唯一退出条件。
如果亏损同时伴随 regime 变差、IV spike、skew steepen 或 scenario loss 超限，应更坚决减仓。
```

### 11.2 冷却期

止损后不应仅等待固定天数。重开必须满足：

- 该品种 IV 不再上升，最好开始回落。
- RV 不再抬升。
- skew 没有继续恶化。
- 同方向没有连续止损 cluster。
- 新候选重新通过 score 和 scenario test。

重复止损后，冷却期延长，预算下降，准入阈值提高。

### 11.3 资本效率退出

后续应研究资本效率退出，而不是简单恢复机械止盈：

```text
remaining premium / scenario_loss 过低
theta / stress_loss 变差
DTE 临近且 gamma 风险上升
持仓已赚大部分权利金但继续持有的风险回报恶化
```

这些条件触发时可以提前平仓或滚仓，但滚仓必须重新通过全部准入规则。

## 12. 归因与验收

每次回测必须输出：

- 年化收益、总收益、最大回撤、最差单日、Sharpe、Calmar。
- 平均/最大保证金使用率。
- 平均/最大 stress 使用率。
- 开仓数、平仓数、止损次数、止损亏损。
- 按 product、bucket、corr_group、option_type 的 PnL。
- 按 vol regime、trend_state、VCOS 分层 PnL。
- 按 action 的 PnL：止损、换月、到期、资本效率退出。
- Greek 归因：delta、gamma、theta、vega、residual。
- vega_pnl 是否为正，尤其是主收益分层是否为正。
- 候选漏斗：每天每一层过滤掉多少候选。
- 异常报价触发、跳价确认和执行偏离。

验收标准：

```text
收益和回撤达到目标区间。
vega_pnl > 0。
theta_pnl > 0。
gamma/delta 尾损不吞噬大部分 theta/vega。
最大单日亏损不吃掉数月 carry。
样本外表现不显著劣化。
参数变化不导致结果剧烈翻转。
```

如果收益达标但 vega 为负，不能通过。该结果应被视为方向暴露、残差或实现口径驱动，而不是合格 S1 卖波收益。

## 13. 下一轮实现顺序

建议按以下顺序推进，不一次性混改：

```text
Step 1: 候选漏斗与四类评分诊断
    先输出 Richness、Regime、Greek Safety、Liquidity、VCOS，不改变交易。

Step 2: Range Call 侧别过滤
    以 F4c 为基准，只限制 low_stable/range Call。

Step 3: Falling-only ramp
    只在 VCOS 高的 falling_vol_carry 中提高预算，不放大 low_stable_vol。

Step 4: Black76 scenario stress sizing
    用场景最坏亏损替代或增强当前单一 stress_loss。

Step 5: Volga/vomma penalty
    对 IV spike 非线性风险高的腿降权或降 size。

Step 6: 资本效率退出
    在不破坏主 carry 的前提下提高 Calmar。

Step 7: 样本外和长周期验证
    固定参数后跑更长年份，不再只看 2025 年 3-6 月。
```

这一顺序的目的，是先证明评分能解释好坏仓，再让评分参与交易，最后才扩大预算。
