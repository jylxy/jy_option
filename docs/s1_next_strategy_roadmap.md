# S1 下一阶段策略改造路线

本文档固化 S1 在 Step7、Step8b、Step8c 之后的下一阶段改造方向。目标不是继续微调单个参数，而是把 S1 从“逐品种规则开仓”升级成“全市场候选池 + 组合风险预算 + 降波窗口放大”的卖波系统。

## 目标

S1 仍然定位为低 Delta 卖权、收 theta、赚波动率风险溢价的日频策略。下一阶段目标为：

- 年化收益贡献尽量接近 4%-6%。
- 最大回撤控制在 2%以内。
- Vega PnL 必须逐步转正，至少不能长期依赖 Delta 或 Residual 盈利。
- 组合画像更接近乐得：多品种、多执行价、小单腿、P/C 随趋势和波动状态偏移、降波确认时敢于提高仓位。

## 当前结论

Step7、Step8b、Step8c 给出的结论很清楚：

- Step7 安全但收益太薄。
- Step8b 放大后暴露 cheap OTM 跳变尾损，典型案例是 `NI2505P118000.SHF`。
- Step8c 用 tail stress guard 压住尾损，但仓位也被压得太薄。

因此下一步不能再简单提高全局保证金、手数或 stress budget。真正要提高的是“每单位 tail risk 的收益质量”，并且只在确认降波的高质量窗口里放大。

## 1. 全市场候选池 + 组合优化

当前问题：现在的开仓更像“按品种逐个扫描，谁先合格谁进”，容易受到扫描顺序、品种先后和局部约束影响，无法保证进入组合的是全市场最优的那一批卖权腿。

下一版方向：

- 每日先生成全市场所有候选腿，而不是逐品种直接开仓。
- 候选范围覆盖商品、ETF、股指期权，但后续评分和预算分市场处理。
- 每个候选腿记录完整字段：品种、板块、相关组、方向、DTE、Delta、权利金、成本、Greeks、IV/RV、VCOS、tail stress、流动性、历史异常报价风险。
- 再由组合层统一排序和填仓。

候选排序不再只看单腿分数，而是看加入组合后的边际贡献：

```text
边际 theta / tail stress
边际 premium / tail stress
边际 vega quality
边际板块/相关组集中度
边际 P/C 与 cash delta 变化
边际保证金占用
```

验收指标：

- 平均活跃品种数从当前约 5-7 提升到 10-15。
- 平均活跃合约数从当前约 10-15 提升到 25-40。
- 新增仓位不再集中在少数扫描靠前品种。
- 每日未成交/未入选候选能解释为评分不够、风险预算不足或流动性不合格。

## 2. 只在确认降波时提高风险预算

当前问题：低波稳定并不等于安全。Step8b 的 NI 案例说明，低波 cheap OTM 在跳空和升波时可能产生不成比例尾损。预算放大不能给 low stable，而应该只给真正的 falling vol carry。

下一版方向：

- 引入或强化 `VCOS`，即 Vol Compression Opportunity Score。
- 只有单品种满足“IV 仍有厚度、IV/合约 IV 正在回落、RV 不升、skew 不恶化、stop cluster 少、流动性稳定”时，才提高预算。
- 组合层 falling release 不能直接泄漏给非 falling 品种。
- `low_stable_vol` 默认不加预算，除非 premium/tail stress 极好且无 VCP 或突破风险。

预算规则：

```text
VCOS 高:
    提高品种 stress budget
    提高候选合约宽度
    优先增加执行价数量
    不优先增加单合约手数

VCOS 中:
    正常预算

VCOS 低:
    小预算或不做

low_stable / VCP:
    原则谨慎，不吃组合 falling release
```

验收指标：

- falling 分层的 PnL、Theta PnL、Vega PnL 显著优于 normal/low/high。
- shock 后进入降波期时，仓位能逐步恢复，而不是长期空仓。
- 低波品种的止损贡献下降。

## 3. Vega 为正变成硬验收

当前问题：如果卖波策略长期 Vega PnL 为负，即使总收益为正，也可能是在靠 Delta、Gamma 路径、Residual 或执行口径赚钱，而不是真正在赚降波和波动率风险溢价。

下一版方向：

- Vega PnL 不再只是复盘字段，而是策略验收指标。
- 每个候选腿记录 forward vega label，例如开仓后 3/5/10 日合约 IV、ATM IV、skew 和实际 Vega PnL。
- 若某类候选长期 forward vega 为负，则降低评分、降低预算或禁入。
- falling 分层必须优先追求 Vega 归因为正。

硬验收：

```text
总 Vega PnL 尽量为正
falling_vol_carry 分层 Vega PnL 必须为正
主要盈利月份不能长期依赖负 Vega
样本外 Vega PnL 不应显著恶化
```

验收指标：

- 输出按 regime、品种、方向、DTE、Delta bucket 的 Vega PnL。
- 输出候选 forward vega hit rate。
- 输出盈利来自 theta/vega/delta/gamma/residual 的比例。

## 4. Stress 不能只用线性 Greeks

当前问题：`3% spot move + 5 vol` 的线性 Greeks 压力损失低估 cheap OTM 的跳空和升波风险。Step8c 的 `premium_loss_multiple` 证明 tail floor 是必要的，但固定倍数过于保守。

下一版方向：

- 用 Black76 scenario repricing 作为候选和组合 stress 的主路径。
- 保留权利金倍数 tail floor，但改成分 regime、分产品、分流动性状态。
- 对 low/normal/high 使用更高 tail floor，对 falling 高质量品种允许较低 tail floor。
- 增加 volga/vomma 近似惩罚，识别 IV spike 时非线性亏损。

基础情景：

```text
Spot 0%, IV +3 vol
Spot 0%, IV +8 vol
Put: Spot -3%, IV +5 vol, skew steepen
Put: Spot -6%, IV +10 vol, skew steepen
Call: Spot +3%, IV +5 vol, skew steepen
Call: Spot +6%, IV +10 vol, squeeze
Crash: Spot -8%, IV +15 vol
Upside shock: Spot +8%, IV +12 vol
```

验收指标：

- 单腿、品种、板块、相关组、组合均输出 worst scenario loss。
- 止损真实亏损相对开仓时 tail stress 的倍数下降。
- 最大单日亏损不再由单个 cheap OTM 跳变主导。

## 5. 仓位目标从保证金 cap 改成收益质量 cap

当前问题：只看保证金使用率会误导。Step8b 保证金并不显著高于 Step7，但多出来的一笔尾损足以吞掉收益；Step8c 风险压住后，平均保证金只有约 2%，收益又太薄。

下一版方向：

- 仓位不再以“达到多少保证金”为目标，而以“新增仓位是否提高组合收益质量”为目标。
- 新增合约必须通过边际收益质量检查。
- 组合达到低质量候选阶段时，即使保证金还很空，也不强行开仓。

核心指标：

```text
premium / tail_stress
theta / tail_stress
expected_vega_pnl / tail_stress
net_premium_after_cost / margin
net_premium_after_cost / scenario_loss
```

仓位规则：

```text
先提高候选质量
再提高品种宽度
再提高执行价宽度
最后才提高单合约手数
```

验收指标：

- 平均保证金逐步提高到 15%-25%，但前提是最大回撤和 tail loss 不恶化。
- 平均 `theta/tail_stress` 和 `premium/tail_stress` 不因放大仓位而下降。
- 最大单合约亏损占组合 NAV 的比例受控。

## 6. P/C 偏移必须是组合级风险预算

当前问题：长期更多卖 Put，本质上会变成长期看涨所有品种，这不是纯卖波，而是方向暴露。之前我们已经做了趋势和动量驱动的 P/C 偏移，但还需要上升到组合级预算。

下一版方向：

- P/C 不是固定比例，也不是长期偏 Put。
- 上涨趋势中可以多卖 Put、少卖 Call；下跌趋势中反过来；震荡中可以双卖。
- 但组合层必须约束 cash delta、板块方向暴露和相关组方向暴露。
- 弱侧可以保留，但必须更低 Delta、更高 premium/tail 要求、更小预算。

组合约束：

```text
组合 cash delta 上限
板块 cash delta 上限
相关组 cash delta 上限
单品种同方向 stress 上限
P/C notional、premium、stress 三套比例监控
```

验收指标：

- 组合收益不能长期来自单边看涨或看跌。
- P/C 偏移后，Theta/Vega 贡献提升，而不是 Delta PnL 主导。
- 极端趋势月份中，弱侧亏损不能吞掉强侧 carry。

## 7. ETF/股指期权单独建评分口径

当前问题：ETF 和股指期权在当前全市场排序中不容易进主仓，可能因为权利金、保证金、流动性和 stress 口径与商品不同。很多管理人会重仓 ETF/股指，是因为容量、成交、尾部和组合稳定性更好，而不是因为单腿收益最高。

下一版方向：

- ETF/股指期权不能简单和商品期权放在同一评分尺度里竞争。
- 为 ETF、股指、商品分别建立 market bucket。
- 每个 market bucket 有独立的最低配置目标、最高配置上限、评分标准和成本假设。
- ETF/股指更强调容量、流动性、滑点可控、组合稳定性。
- 商品更强调单品种波动、板块相关性、跳变和流动性风险。

单独评分维度：

```text
ETF/股指:
    流动性权重更高
    容量权重更高
    单腿 premium/tail 可以略低
    组合稳定性加分
    系统性风险和指数 beta 单独约束

商品:
    premium/tail 要求更高
    跳空和异常报价惩罚更高
    板块/相关组约束更强
```

验收指标：

- ETF/股指不再长期被商品挤出候选池。
- ETF/股指贡献更稳定的 theta，但不显著拉低总收益质量。
- 商品贡献更高 carry，但不能主导尾损。

## 下一步实施顺序

建议按以下顺序实现，避免一次性混改导致无法归因：

1. 先做全市场候选池诊断，不改变交易。
2. 加入 VCOS 与 forward vega label 诊断，不改变交易。
3. 接入 Black76 scenario stress 与分 regime tail floor。
4. 做全市场组合排序与填仓，但先保守预算。
5. 将 P/C 偏移提升为组合级 cash delta 和 stress 预算。
6. 单独建立 ETF/股指评分和市场 bucket。
7. 最后才做 falling 高质量窗口的预算放大。

每一步都必须输出：

- 总绩效。
- Greek 归因。
- 分 regime 绩效。
- 分市场 bucket 绩效。
- 分品种/方向/合约尾损。
- 候选漏斗。
- Ledet similarity 指标。
