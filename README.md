# 服务器部署包（分钟级回测引擎 v2）

从整个项目中提取的最小可运行文件集，用于在公司服务器上回测。

**当前主引擎**: `minute_backtest.py`（分钟级VWAP执行 + Greeks监控 + 买卖价差模拟）
**数据源**: `option_daily_agg.db` + `contract_master.db`（服务器IT团队已准备好清洗后的分钟级聚合数据）

> ⚠️ 旧版日频引擎 `daily_backtest_t1vwap.py` 已弃用，保留在 src/ 中仅供参考。

## 运行方式

```bash
# 标准运行（使用聚合数据库，默认VWAP 10分钟窗口）
python src/minute_backtest.py --start 2024-01-01

# 指定VWAP窗口和执行方法
python src/minute_backtest.py --start 2024-01-01 --vwap-window 15 --exec-method twap

# 启用盘中止盈
python src/minute_backtest.py --start 2024-01-01 --intraday-tp

# 退化为旧版benchmark.db模式（对比基准）
python src/minute_backtest.py --start 2024-01-01 --no-agg-db

# 禁用对冲层
python src/minute_backtest.py --start 2024-01-01 --no-hedge
```

## 数据源说明

服务器IT团队已准备好清洗后的分钟级数据，聚合为以下数据库：

| 数据库 | 说明 |
|--------|------|
| `option_daily_agg.db` | 期权日频聚合数据（OHLCV + 分时段VWAP + 买卖价差代理） |
| `contract_master.db` | 合约属性（行权价、到期日、期权类型、乘数、标的代码） |

引擎自动从聚合数据库计算 IV/Delta/Gamma/Vega/Theta（BSM模型），不再依赖 `benchmark.db`。

## 文件清单

### 核心引擎（当前版本）

| 文件 | 说明 |
|------|------|
| `src/minute_backtest.py` | ★ 主回测引擎（分钟级VWAP执行、T+1订单模拟、Greeks监控、对冲层、压力测试） |
| `src/strategy_rules.py` | 策略规则（合约选择、仓位计算、止盈、S3 v2 OTM%选腿函数） |
| `src/option_calc.py` | IV反推 + Greeks批量计算（BSM模型，支持向量化加速） |
| `src/data_loader_v2.py` | 聚合数据库加载器（替代旧版 load_product_data，自算IV/Greeks） |
| `src/correlation_monitor.py` | 品种相关性监控（相关性矩阵、板块集中度、有效分散度、尾部相关性） |
| `src/backtest_fast.py` | 基础工具（estimate_margin 按交易所区分公式、load_product_data） |
| `src/exp_product_count.py` | 品种映射表 PRODUCT_MAP（含ETF期权）+ scan_and_rank / scan_and_rank_v2 |
| `src/scan_all_liquidity.py` | 全品种深虚值期权流动性扫描 |

### 配置与文档

| 文件 | 说明 |
|------|------|
| `config.json` | 策略参数配置（v2.0_minute_greeks） |
| `docs/strategy_spec.md` | 策略完整规格书（v2.1） |
| `docs/组合策略规则.md` | 三策略组合规则 |

### 旧版文件（已弃用，仅供参考）

| 文件 | 说明 |
|------|------|
| `src/daily_backtest_t1vwap.py` | [已弃用] 旧版日频回测引擎（T+1 VWAP执行，基于 benchmark.db） |
| `src/daily_backtest.py` | [已弃用] 更早期的日频回测引擎 |
| `src/unified_engine_v3.py` | [已弃用] 三策略组合引擎（S1+S3+S4） |
| `src/build_enriched_from_wind.py` | [已弃用] 从Wind MySQL构建 benchmark_wind.db |
| `src/verify_data.py` | [已弃用] 验证 benchmark.db 数据 |

## 版本演进

```
日频引擎 v1 (daily_backtest.py)
  └─ 日频引擎 v2 (daily_backtest_t1vwap.py)  ← T+1 VWAP执行
      └─ ★ 分钟级引擎 v2 (minute_backtest.py)  ← 当前版本
           - 数据源: option_daily_agg.db + contract_master.db
           - 执行: 精确窗口VWAP（5/10/15/30分钟可选）
           - 新增: 自算IV/Greeks、买卖价差模拟、成交量约束
           - 新增: 组合Greeks实时监控 + 压力测试矩阵
           - 新增: 品种相关性 + 板块集中度监控
           - 新增: S3 v2 OTM%选腿 + 应急蝶式保护
           - 新增: 按交易所区分保证金公式
           - 新增: ETF期权品种支持
```
