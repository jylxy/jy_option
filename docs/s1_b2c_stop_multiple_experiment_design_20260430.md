# S1 B2C 止损倍数朴素实验设计

生成日期：2026-04-30

## 1. 实验目的

此前 shadow / 止损后归零检查显示，按 `2.5x` 权利金止损触发的合约中，有相当高比例最终会归零或大幅回落。这说明当前硬止损可能在一部分“短期跳价、低流动性噪音、IV 暂时冲击”场景下过早离场。

本实验只回答一个问题：

```text
在 B2C 结构完全不变的情况下，权利金止损倍数从严格到宽松，是否改善长期收益、回撤、Greek 损耗和权利金留存？
```

## 2. 固定基准

基准配置：

```text
config_s1_baseline_b2_product_tilt075_stop25.json
```

固定不变的内容：

- B2C 品种质量预算倾斜：`s1_b2_tilt_strength = 0.75`
- B1 流动性 / 持仓量排序基线
- 全品种池
- 次月口径：继承 B0 的 `s1_expiry_mode = nth_expiry`、`s1_expiry_rank = 2`
- 卖权 delta：继承 B0 的 `s1_sell_delta_cap = 0.10`
- 价格底线：继承 B1 的 `s1_min_option_price = 0.5`
- 不启用保护腿、不启用盈利止盈、不启用 IV warmup
- `premium_stop_requires_daily_iv_non_decrease = false`

唯一变化：

```text
premium_stop_multiple
```

## 3. 实验档位

| 档位 | 配置文件 | 建议 tag |
| --- | --- | --- |
| 1.0x | `config_s1_baseline_b2_product_tilt075_stop10.json` | `s1_b2c_stop10_2022_latest` |
| 1.5x | `config_s1_baseline_b2_product_tilt075_stop15.json` | `s1_b2c_stop15_2022_latest` |
| 2.0x | `config_s1_baseline_b2_product_tilt075_stop20.json` | `s1_b2c_stop20_2022_latest` |
| 2.5x | `config_s1_baseline_b2_product_tilt075_stop25.json` | `s1_b2c_stop25_2022_latest` |
| 3.0x | `config_s1_baseline_b2_product_tilt075_stop30.json` | `s1_b2c_stop30_2022_latest` |
| 3.5x | `config_s1_baseline_b2_product_tilt075_stop35.json` | `s1_b2c_stop35_2022_latest` |
| 4.0x | `config_s1_baseline_b2_product_tilt075_stop40.json` | `s1_b2c_stop40_2022_latest` |
| 4.5x | `config_s1_baseline_b2_product_tilt075_stop45.json` | `s1_b2c_stop45_2022_latest` |
| 5.0x | `config_s1_baseline_b2_product_tilt075_stop50.json` | `s1_b2c_stop50_2022_latest` |
| 不止损 | `config_s1_baseline_b2_product_tilt075_nostop.json` | `s1_b2c_nostop_2022_latest` |

`no_stop` 的实现方式是：

```json
"premium_stop_multiple": 0.0
```

代码中 `premium_stop_multiple <= 0` 会直接关闭权利金止损。

## 4. 评估指标

必须统一使用同一时间区间、同一交易日截止日比较：

- NAV、累计收益、年化收益
- 年化波动、Sharpe、Calmar
- 最大回撤、最差单日、回撤持续期
- 止损次数、止损后最终归零比例、止损后反转比例
- 到期实值比例
- 权利金留存率：实际净收益 / 开仓权利金
- `theta_pnl`、`vega_pnl`、`gamma_pnl`、`delta_pnl`、`residual_pnl`
- 当前与峰值保证金占用、stress loss 占用
- P/C 结构、活跃品种数、活跃合约数

## 5. 预期解释框架

如果止损越宽收益越高、回撤没有显著恶化：

```text
当前 2.5x 可能仍然过早，很多止损属于噪音止损或可恢复的 IV/成交价跳变。
```

如果止损越宽收益提高但回撤显著恶化：

```text
止损有风险控制价值，但需要结合价格异常过滤、IV 状态和 gamma/到期风险，而不是简单放宽。
```

如果不止损显著更好：

```text
说明 S1 更接近“持有到期收保险费”，止损规则主要在损害权利金留存；但仍要额外检查尾部月份和保证金压力。
```

如果严格止损更好：

```text
说明当前卖方左尾确实需要快速切断，之前“止损后归零”可能只覆盖局部样本，不足以代表组合层风险。
```

## 6. 注意事项

- 这组实验不改变合约选择、品种预算、流动性、保证金或组合风控，只做止损倍数单变量实验。
- 不止损不是最终策略建议，只是上界诊断，用来判断硬止损本身是否有负贡献。
- 若宽止损改善收益但恶化 gamma，应进一步做“异常价确认 + 到期前风险退出 + gamma rent 约束”的组合实验。
