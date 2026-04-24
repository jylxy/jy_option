# S1 当前配置与实现审计

本文档记录 2026-04-24 对 S1 策略当前配置、代码路径和实验基准的审计结论。它用于避免后续实验把“已经实现但未启用”“配置存在但未接入”和“真正未实现”混在一起。

## 结论摘要

- `config_s1_risk_reward_v1.json` 仍作为当前收益风险评分基准。
- E1 不是主线升级实验，而是反向消融实验：它只保留 `IV/RV carry >= 0`，故意拆掉了完整 falling framework。
- 当前 repo 中已经存在完整 falling/carry 入场框架，但它主要体现在 `config_s1_only_stop25_noprotect_volregime.json`，没有和 V1 的 `risk_reward` 候选评分合并。
- 下一轮先不混入 falling framework，先做 V1 的单变量实验：止损倍数从 `1.8x` 调整到 `2.5x`。

## 当前主要配置分工

### V1 风险收益评分基准

文件：[config_s1_risk_reward_v1.json](../config_s1_risk_reward_v1.json)

- 已启用 `s1_ranking_mode = risk_reward`。
- 已启用 `s1_use_stress_sizing`。
- 已启用组合层 `portfolio_stress_gate_enabled`。
- 已启用同品种同方向新增与相邻合约分散。
- 未启用 `s1_falling_framework_enabled`。
- 未启用合约自身 IV 不上行过滤。
- 当前硬止损为 `premium_stop_multiple = 1.8`。

这版是当前最好的基准配置，适合做单变量消融和止损倍数实验。

### 完整 falling/carry 框架旧配置

文件：[config_s1_only_stop25_noprotect_volregime.json](../config_s1_only_stop25_noprotect_volregime.json)

- 已启用 `s1_falling_framework_enabled`。
- 已启用 `s1_require_risk_release_entry`。
- 已启用 `s1_require_contract_iv_not_rising`。
- 已启用 vol regime sizing 和 regime-specific budget。
- 已启用结构性低 IV 例外。
- 已启用更完整的组合层 stress、bucket、cash vega/gamma 控制。
- 硬止损为 `2.5x`。
- 但它没有启用 V1 的 `risk_reward` 候选评分。
- 它是 `noprotect` 版本，保护腿逻辑与 V1 不完全一致。

这版说明很多 falling/carry 逻辑已经实现，但还不能直接替代 V1 基准。

### E1 carry-only 反向消融

文件：[config_s1_risk_reward_e1_carry.json](../config_s1_risk_reward_e1_carry.json)

- 在 V1 上只保留 `IV/RV carry >= 0`。
- 显式关闭 `s1_entry_check_vol_trend`。
- 显式关闭 `s1_entry_block_high_rising_regime`。
- 显式关闭 `s1_prioritize_products_by_regime`。
- 不代表完整 falling framework 的效果。

E1 的意义是证明“只看非负 carry 不够”，不应用它否定完整 falling framework。

## 已接入代码路径

### S1 入场框架

文件：[vol_regime.py](../src/vol_regime.py)

- `passes_s1_falling_framework_entry` 已接入 IV/RV spread、IV/RV ratio、IV/RV trend、高波/止损冷却禁入和 risk release。
- `passes_s1_risk_release_entry` 已接入更严格的 IV/RV carry、IV pct、IV trend、RV trend 和结构性低 IV 例外。
- `classify_product_vol_regime_base` 已接入 falling、low stable、normal、high rising、post-stop cooldown 分类。

### 合约自身 IV 趋势过滤

文件：[toolkit_minute_engine.py](../src/toolkit_minute_engine.py)

- `_prepare_s1_selection_frame` 已把合约自身 `contract_iv_change_1d` 和 `contract_price_change_1d` 接入候选过滤。
- 该过滤由 `s1_require_contract_iv_not_rising` 和 `s1_require_contract_price_not_rising` 控制。

### 候选评分

文件：[strategy_rules.py](../src/strategy_rules.py)

- `select_s1_sell` 已支持 `risk_reward` 排序。
- 排序指标包括 `premium_stress`、`theta_stress`、`premium_margin`、流动性分数和 delta 距离。
- 支持 gamma/vega penalty。
- Delta 小于 `0.10` 仍是硬约束。

### stress sizing 与组合风控

文件：[portfolio_risk.py](../src/portfolio_risk.py)、[budget_model.py](../src/budget_model.py)

- 已支持单笔 `stress_loss` sizing。
- 已支持组合 stress cap、bucket stress cap、product/bucket margin cap。
- 已支持 cash vega/cash gamma 组合约束。
- 已支持静态相关组和动态相关过滤。
- 已支持基于组合 vol regime 的 open budget 覆盖。
- 已支持 drawdown 和 stop cluster brake。

### 止损、冷却和重开

文件：[toolkit_minute_engine.py](../src/toolkit_minute_engine.py)、[vol_regime.py](../src/vol_regime.py)

- 已支持权利金倍数止损。
- 已支持 `premium_stop_requires_daily_iv_non_decrease`。
- 已支持止损后冷却和重复止损延长冷却。
- 已支持 S1 重开时要求 falling regime 或日度 IV 回落。
- 尚未完整实现“重复止损后降低该品种预算、提高 premium/stress 要求、收紧 delta cap”的分级降权。

## 需要特别注意的结构问题

当前 S1 在每个合格品种/到期上会依次尝试 `P` 和 `C` 两侧。这意味着现在更接近“默认尝试双卖，再由候选过滤和组合约束挡住”，而不是文档中要求的“只有在中性或降波环境下条件双卖”。

后续如果评估双卖，应当新增显式开关和环境条件，而不是继续让双卖隐含在默认循环里。

## 下一步实验基准

当前先以 `config_s1_risk_reward_v1.json` 为基准，只做止损倍数单变量实验：

- 基准：V1。
- 唯一变化：`premium_stop_multiple` 从 `1.8` 调整到 `2.5`。
- 不打开 falling framework。
- 不改变候选评分。
- 不改变 stress sizing。
- 不改变组合风控。
- 不改变同品种多合约规则。

该实验用于回答：更宽的硬止损是否能降低假止损和来回打脸，同时不显著放大最大回撤。
