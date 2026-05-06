# S1 B2C Stop 1.5x 品种与板块控制实验设计

生成日期：2026-04-30  
研究对象：S1 日频低 Delta 纯卖权策略  
新基准：`B2C + premium_stop_multiple = 1.5x`  
实验主题：只改变品种池与板块暴露，不改变止损、合约排序、Delta、期限、费用、保证金和执行口径。

## 1. 本轮实验的研究背景

止损倍数实验已经给出阶段性结论：在当前 B2C 框架下，`Stop 1.5x` 相对 `Stop 2.5x / B2C` 和 `Stop 3.5x` 呈现更高收益、更低回撤、更好的超额路径。因此，后续研究先接受 `B2C + Stop 1.5x` 作为新的基准。

接下来不再同时调整多个变量，而是只研究一个问题：

```text
在同一套 B2C 评分、同一止损 1.5x、同一交易成本、同一期限和同一 delta 约束下，
品种池和板块选择是否能进一步改善 S1 的权利金质量、留存率和尾部风险？
```

这一步的目的不是找一个事后最优品种组合，而是判断“品种选择”到底改善了收益公式里的哪一项。

```text
S1 net return
= Premium Pool
× Deployment Ratio
× Retention Rate
- Tail / Stop Loss
- Cost / Slippage
```

品种池变化可能带来四类结果：

1. `Premium Pool` 变薄：少交易一些权利金厚但高风险的品种，收益可能下降但回撤改善。
2. `Retention Rate` 变高：少交易容易止损、跳价、趋势穿透的品种，权利金更能留住。
3. `Tail / Stop Loss` 降低：剔除尾部脆弱品种或板块，最大回撤和止损簇下降。
4. `Cost / Slippage` 改善：只做流动性更好品种，成交和退出更可靠。

## 2. 共同基准参数

所有实验除“品种池 / 板块暴露”外，必须保持一致。

| 模块 | 固定口径 |
| --- | --- |
| 基础配置 | `config_s1_baseline_b2_product_tilt075_stop15.json` |
| 回测区间 | `2022-01-01` 至数据最新日 |
| 初始资金 | `10,000,000` |
| 策略 | 只跑 S1，关闭 S3/S4 |
| 保证金上限 | `s1_margin_cap = 50%` |
| 品种内预算 | 沿用 B2C 的 `s1_b2_product_tilt_enabled = true`，`tilt_strength = 0.75` |
| 合约选择 | 次月口径，`s1_expiry_mode = nth_expiry`，`s1_expiry_rank = 2` |
| Delta | `abs(delta) < 0.10`，不突破硬阈值 |
| 单侧梯队 | 沿用 `s1_baseline_max_contracts_per_side = 5` |
| 最低价格 | 沿用 B1/B2C 口径，`s1_min_option_price = 0.5` |
| 止盈 | 不止盈 |
| 止损 | 权利金上涨到开仓权利金 `1.5x`，并沿用当前跳价确认机制 |
| 到期 | 持有到到期前/到期处理的现有 B0/B2C 口径 |
| 费用 | 期货公司手续费表 + 当前滑点设置 |
| 保证金 | 交易所/期货公司保证金率表 |
| IV 预热 | 沿用当前 B0/B2C 基准口径，不因本轮品种实验改变 |

本轮不允许同时修改：

- 合约评分因子；
- P/C 偏移规则；
- 单品种预算倾斜公式；
- 止损倍数；
- 保证金上限；
- 板块动态风控；
- Tail-HRP 或相关性聚类预算。

## 3. 实验组设计

### P0：新基准，全品种 B2C + Stop 1.5x

用途：作为所有品种实验的统一基准。

| 项目 | 设定 |
| --- | --- |
| Tag | `s1_b2c_stop15_allprod_2022_latest` |
| Config | `config_s1_baseline_b2_product_tilt075_stop15.json` |
| Products | 不传 `--products`，使用全品种池 |
| 目的 | 记录新主基准的完整表现 |

如果本地/远端已有 `s1_b2c_stop15_2022_latest` 完整结果，可直接作为 P0。

### P1：剔除“不适合交易”品种

用途：验证我们之前的品种适配打分表是否能改善组合质量。

来源文件：

```text
output/product_suitability/product_suitability_s1_b5_full_shadow_v1_delta006_012_price05.csv
```

当前 `recommended_tier = Exclude` 的品种为：

```text
SC, JM, I, AU, PD, LG, PT, PB, PP, PX, BU, ZC, EG, PF, CS
```

实验口径：

```text
P1 universe = 全品种 - Exclude tier
```

建议 tag：

```text
s1_b2c_stop15_exclude_unsuitable_2022_latest
```

需要特别说明：

1. 这个实验是“模型/因子筛选型 blacklist”，不是最终实盘黑名单。
2. 该名单来自我们的 full shadow 与适配性评分，存在样本内成分，不能直接视为独立外部先验。
3. 其中 `SC/AU/I/EG` 与乐得名单有冲突，因此 P1 与 P3 的对比非常重要。如果 P1 明显改善但 P3 也改善，说明“外部经验名单”和“模型 blacklist”可能各自捕捉了不同维度。
4. 如果 P1 表现变差，不能直接否定品种适配表，可能说明评分表过度惩罚了流动性/微结构，但这些品种在组合中仍提供重要权利金池。

### P2：每日只交易流动性排名前 20 的品种

用途：验证“更可交易、更主流”的品种池是否提高留存率并降低滑点/尾部噪声。

实验口径：

```text
全品种池中，每个交易日按当前日可观测的次月低 delta 合约成交量 + OI 打分，
只扫描排名前 20 的品种。
```

实现建议：

```json
{
  "extends": "config_s1_baseline_b2_product_tilt075_stop15.json",
  "strategy_version": "s1_b2c_stop15_liquidity_top20",
  "s1_baseline_product_ranking_mode": "liquidity_oi",
  "daily_scan_top_n": 20
}
```

建议 tag：

```text
s1_b2c_stop15_liq_top20_2022_latest
```

这个实验的经济含义：

- 可能改善 `Cost / Slippage`；
- 可能提高退出可靠性；
- 可能降低低流动性深虚合约的假止损；
- 但也可能损失部分小品种的 `Premium Pool`；
- 如果收益下降但回撤改善，需要看 Calmar 和止损损失是否改善；
- 如果收益和回撤同时改善，说明“只做主流流动性品种”可能是最强的第一层品种过滤。

### P3：乐得主流品种名单

用途：验证外部成熟管理人经验名单在我们 B2C + Stop 1.5x 框架下是否更接近“可交易卖方组合”。

用户提供的原始描述：

```text
主流的选一下
有色 铜 铝 zn 锡 镍
乙二醇
双月的金
近月白银
化工建材 pta ma eb 烧碱 sa 玻璃 氧化铝
原油 主力月的农产品 豆菜粕 豆二 棕榈油
主力月螺纹
近月石头
橡胶限仓 但还行
其他品种多少要等行情
```

本轮为了控制变量，只做“品种池”映射，不在第一轮修改到期月份规则。因此“金双月、白银近月、农产品主力月、螺纹主力月、铁矿近月”等经验，先记录为后续 P3b/P4 的期限结构扩展，不在 P3 主实验中启用。

P3 主实验品种池：

| 板块 | 中文 | 代码 |
| --- | --- | --- |
| 有色 | 铜、铝、锌、锡、镍 | `CU, AL, ZN, SN, NI` |
| 贵金属 | 黄金、白银 | `AU, AG` |
| 能源 | 原油 | `SC` |
| 化工建材 | PTA、甲醇、苯乙烯、烧碱、纯碱、玻璃、氧化铝、乙二醇 | `TA, MA, EB, SH, SA, FG, AO, EG` |
| 黑色 | 螺纹、铁矿 | `RB, I` |
| 农产品 | 豆粕、菜粕、豆二、棕榈油 | `M, RM, B, P` |
| 橡胶 | 天胶 | `RU` |

完整代码列表：

```text
CU, AL, ZN, SN, NI, AU, AG, EG, TA, MA, EB, SH, SA, FG, AO, SC, M, RM, B, P, RB, I, RU
```

共 `23` 个品种。

建议 tag：

```text
s1_b2c_stop15_ledet_mainstream_2022_latest
```

需要特别说明：

1. P3 是外部经验先验，不是我们因子表内生得到的名单，过拟合风险相对 P1 更低。
2. P3 中有多个品种被 P1 的适配表列为 `Exclude` 或 `Observe`，例如 `SC/AU/I/EG/B/RB` 等。这正是本轮实验的价值：看“实盘主流经验”是否能战胜我们基于 shadow outcome 的适配评分。
3. P3 若表现好，说明我们的适配评分可能过度惩罚了微结构或单品种历史结果，而忽略了主流品种的容量和可执行价值。
4. P3 若表现差，需要进一步拆板块：可能不是乐得名单错，而是我们仍按“次月统一规则”交易，未使用他们口径里的双月金、近月银、主力月农产品等期限经验。

### P3B：乐得主流名单 + 乐得期限偏好

用途：把乐得原话里除品种名单以外的期限经验也直接落成一个对照版本。

与 P3 的区别：

```text
P3  = 只测试乐得主流品种池，仍沿用统一次月 / nth_expiry = 2。
P3B = 测试乐得主流品种池 + 乐得提到的品种级期限偏好。
```

这条线不再是严格的“只改变品种池”实验，而是“乐得经验复刻”实验。它非常重要，但结论要和 P1/P2/P3 分开解释：如果 P3B 好于 P3，说明乐得名单的价值可能不只来自品种选择，还来自不同品种不同期限的交易经验。

#### P3B 期限规则

| 品种组 | 代码 | P3 口径 | P3B 口径 | 解释 |
| --- | --- | --- | --- | --- |
| 黄金 | `AU` | 统一次月 | 2/4/6/8/10 月合约 | 乐得原话“双月的金”。这里“双月”不是更远一档，而是交易偶数月份合约。黄金合约月份选择可能与产业习惯、活跃度和交割月节奏有关。 |
| 白银 | `AG` | 统一次月 | 近月 / 第一到期 | 乐得原话“近月白银”。白银短端权利金和流动性可能更集中。 |
| 农产品 | `M, RM, B, P` | 统一次月 | 主力月 | 乐得原话“主力月的农产品”。农产品不同月份活跃度差异大，主力月可能比机械次月更可交易。 |
| 螺纹 | `RB` | 统一次月 | 主力月 | 乐得原话“主力月螺纹”。黑色期限结构强，主力月流动性更重要。 |
| 铁矿 | `I` | 统一次月 | 近月 / 第一到期 | 乐得原话“近月石头”。铁矿短端活跃，但 gamma 和跳价风险也要单独监控。 |
| 其他乐得名单品种 | 其余 P3 品种 | 统一次月 | 统一次月 | 暂不额外改变期限。 |

#### P3B 实现口径建议

当前引擎的 `s1_expiry_mode` 是全局参数，因此 P3B 需要新增一个轻量的“品种级 expiry override”能力，避免为不同品种分拆多个回测再手工合并。

建议配置字段：

```json
{
  "s1_product_expiry_overrides": {
    "AU": {"mode": "allowed_contract_months", "months": [2, 4, 6, 8, 10]},
    "AG": {"mode": "nth_expiry", "expiry_rank": 1},
    "I":  {"mode": "nth_expiry", "expiry_rank": 1},
    "RB": {"mode": "main_month"},
    "M":  {"mode": "main_month"},
    "RM": {"mode": "main_month"},
    "B":  {"mode": "main_month"},
    "P":  {"mode": "main_month"}
  }
}
```

其中：

- `nth_expiry = 1` 表示近月；
- `nth_expiry = 2` 表示当前 B2C 默认次月；
- `allowed_contract_months` 表示只在指定月份合约里再按当前可交易到期顺序选择；
- `main_month` 需要用当日该品种期权链或标的期货数据识别成交量/OI 最大的到期月份。

如果第一版实现 `main_month` 或 `allowed_contract_months` 成本较高，可以先做 P3B-lite：

```text
AU -> 在可用黄金合约中筛选 2/4/6/8/10 月合约；若当日无合格双月合约则跳过黄金
AG/I -> expiry_rank 1
RB/M/RM/B/P -> 仍用 expiry_rank 2
```

然后在 P3B-full 中补齐主力月逻辑。

建议 tag：

```text
s1_b2c_stop15_ledet_term_pref_2022_latest
```

#### P3B 的解释边界

P3B 如果优于 P3，不能简单说“品种池更好”，而应写成：

```text
乐得经验中的期限选择提高了部分品种的可交易权利金池、流动性和权利金留存率。
```

P3B 如果弱于 P3，也不能直接否定乐得期限经验，需先检查：

1. 我们的 `双月/近月/主力月` 映射是否真的贴近乐得口径；
2. `main_month` 是否用了未来成交量或未来 OI；
3. 近月白银和近月铁矿是否因为 gamma 过高导致止损增加；
4. 黄金偶数月份合约是否因为可交易窗口不足、theta 效率下降或流动性分布不同导致收益变薄；
5. 农产品主力月是否改善流动性但减少可卖合约数量。

## 4. 板块口径

本轮先不启用新的板块风控上限，但所有实验必须输出同一套板块诊断，避免只看品种层结论。

建议板块映射：

| 板块 | 品种 |
| --- | --- |
| 有色 | `CU, AL, ZN, SN, NI, PB, AO` |
| 贵金属 | `AU, AG` |
| 能源 | `SC, FU, BU, LU, PG` |
| 化工 | `TA, MA, EB, EG, PP, L, V, PF, PX, SA, SH, UR` |
| 建材黑色 | `RB, HC, I, JM, J, FG, SM, SF` |
| 农产品油脂油料 | `M, RM, A, B, P, Y, OI, C, CS` |
| 软商品 | `SR, CF, AP, CJ, PK` |
| 橡胶 | `RU, BR` |
| 股指/ETF | `IO, MO, HO, 510050, 510300, 510500, 588000` 等 |
| 其他/新上市 | 其余按数据表补齐 |

本轮报告中至少要输出：

- 平均板块保证金占比；
- 峰值板块保证金占比；
- 板块 stop PnL；
- 板块 open premium；
- 板块 premium retention；
- 板块最大单日亏损贡献；
- Top 3 板块集中度；
- 单日最大板块占比是否超过 40%。

如果某实验因为品种池缩小导致单板块集中度过高，不能只看 NAV 更好，还要标记为“可能牺牲分散度换收益”。

## 5. 评价指标

所有实验相对 P0 比较。

### 5.1 总体绩效

| 指标 | 目的 |
| --- | --- |
| NAV / 累计收益 / 年化收益 | 是否提高收益 |
| 年化波动 / Sharpe / Sortino | 收益是否稳定 |
| 最大回撤 / Calmar | 是否改善卖方核心风险 |
| 最差单日 / 最差 5 日 | 是否降低左尾 |
| 回撤修复天数 | 是否更像乐得的净值画像 |

### 5.2 卖权质量

| 指标 | 对应公式 |
| --- | --- |
| 新开仓净权利金 / NAV | Premium Pool / Deployment |
| 权利金留存率 | Retention Rate |
| S1 PnL / 新开仓权利金 | Retention Rate |
| 止损 PnL / 新开仓权利金 | Tail / Stop Loss |
| Vega PnL / 新开仓权利金 | Vega quality |
| Gamma PnL / 新开仓权利金 | Gamma quality |
| 手续费 / 新开仓权利金 | Cost |

### 5.3 品种与板块

| 指标 | 目的 |
| --- | --- |
| 平均活跃品种数 | 是否过窄 |
| 平均合约数 / 每品种合约数 | 是否更接近乐得的分散梯队 |
| Top 10 品种 open premium 占比 | 品种集中度 |
| Top 3 板块保证金占比 | 板块集中度 |
| 亏损 Top 品种是否集中 | 尾部是否来自少数品种 |
| 股指/ETF/商品占比 | 是否偏离主流管理人画像 |

## 6. 关键判断标准

### 6.1 P1 的判断

如果 P1 相对 P0：

- 收益不低于 P0；
- 最大回撤下降；
- 止损 PnL 下降；
- 权利金留存率提高；

则说明“不适合交易品种剔除”有价值，可以成为第一层 hard filter。

如果 P1 收益显著下降但回撤也下降，则说明该 blacklist 更像防守型过滤，适合风险预算收缩而不是常态黑名单。

如果 P1 收益和回撤都变差，则说明原适配评分过度内生或惩罚错位，不应直接进入交易。

### 6.2 P2 的判断

如果 P2 相对 P0：

- NAV 接近或更高；
- 回撤更低；
- 止损次数和止损金额下降；
- 手续费/滑点占权利金比例下降；

则说明流动性前 20 是非常强的实盘友好过滤。

如果 P2 回撤改善但收益下降，要计算 `Premium Pool` 下降幅度。如果权利金池下降过多，后续可从 Top20 扩到 Top30/Top40。

### 6.3 P3 的判断

如果 P3 相对 P0：

- 收益接近或更高；
- 回撤更低；
- 板块更均衡；
- 止损簇下降；
- 活跃品种数仍足够；

则说明外部经验名单是一个强先验，可以作为后续实盘模拟盘的候选主池。

如果 P3 表现差，不直接否定乐得名单，而要继续拆成：

1. 是否因为我们没有按他们的“近月/主力月/双月金”交易；
2. 是否因为某些板块如能源/黑色/化工在我们的止损和 B2C 排序下不适配；
3. 是否因为品种少导致板块集中。

## 7. 建议执行顺序

第一步：确认 P0 作为新基准。

```powershell
python src/toolkit_minute_engine.py `
  --start-date 2022-01-01 `
  --tag s1_b2c_stop15_allprod_2022_latest `
  --config config_s1_baseline_b2_product_tilt075_stop15.json
```

如果已有同口径完整结果，则只做校验，不重跑。

第二步：跑 P1、P2、P3、P3B 四条线。

P1：

```powershell
python src/toolkit_minute_engine.py `
  --start-date 2022-01-01 `
  --tag s1_b2c_stop15_exclude_unsuitable_2022_latest `
  --config config_s1_baseline_b2_product_tilt075_stop15.json `
  --products <全品种剔除 Exclude tier 后的列表>
```

P2：

```powershell
python src/toolkit_minute_engine.py `
  --start-date 2022-01-01 `
  --tag s1_b2c_stop15_liq_top20_2022_latest `
  --config config_s1_baseline_b2_product_tilt075_stop15_liq_top20.json
```

P3：

```powershell
python src/toolkit_minute_engine.py `
  --start-date 2022-01-01 `
  --tag s1_b2c_stop15_ledet_mainstream_2022_latest `
  --config config_s1_baseline_b2_product_tilt075_stop15.json `
  --products CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU
```

P3B：

```powershell
python src/toolkit_minute_engine.py `
  --start-date 2022-01-01 `
  --tag s1_b2c_stop15_ledet_term_pref_2022_latest `
  --config config_s1_baseline_b2_product_tilt075_stop15_ledet_term_pref.json `
  --products CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU
```

第三步：统一生成对比报告。

报告必须包含：

- P0/P1/P2/P3/P3B NAV 叠加；
- 相对 P0 超额曲线；
- 回撤曲线；
- 年度收益；
- 止损次数、止损金额、止损后最终归零比例；
- 权利金池与留存率；
- Greek PnL；
- 品种贡献 Top/Bottom；
- 板块贡献与集中度；
- P/C 结构；
- 与乐得名单的重合和差异解释。

## 8. 本轮实验不做但需要记录的扩展

以下内容先不在本轮主实验中启用，避免变量混乱。

1. 乐得名单里的期限偏好：
   - 双月黄金；
   - 近月白银；
   - 主力月农产品；
   - 主力月螺纹；
   - 近月铁矿。

2. 板块硬上限：
   - 单板块保证金上限；
   - 单板块 stress loss 上限；
   - 单板块止损簇熔断。

3. Tail-HRP：
   - 用全市场尾部相关 cluster 做组合预算。

4. 动态行情名单：
   - “其他品种多少要等行情”可以后续作为 regime-dependent product enable list。

这些更适合在 P0/P1/P2/P3 跑完后进入下一轮。

## 9. 预期结论形态

这三条线各自回答不同问题。

| 实验 | 核心问题 | 如果成功，说明什么 |
| --- | --- | --- |
| P1 剔除不适合品种 | 我们的品种评分表能否做 hard filter？ | 内生数据因子可以识别坏品种 |
| P2 流动性前 20 | 主流流动性是否足以提升实盘可交易性？ | 低流动性噪声/假止损是重要损耗来源 |
| P3 乐得名单 | 外部成熟管理人经验是否优于纯量化筛选？ | 品种池先验和产业经验有真实价值 |
| P3B 乐得名单 + 期限偏好 | 乐得经验是否来自“品种 + 期限”的组合？ | 品种期限结构本身是卖方收益质量变量 |

最终不一定三选一。更可能的下一版是：

```text
主池 = 乐得主流名单 ∩ 流动性合格
观察池 = 适配评分 Conditional/Observe 且行情质量改善的品种
禁做池 = 适配评分 Exclude 且外部经验也不支持的品种
```

这比简单“全品种扫描”更接近实盘卖方组合，也更容易解释给投委会和风控。
