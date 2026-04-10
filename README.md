# 服务器部署包

从整个项目中提取的最小可运行文件集，用于在公司服务器上回测。

## 项目演进脉络

```
Week1 (4.2-4.10)  数据+波动率基础设施
  └─ week1/src/volatility.py ← 基础模块，至今仍被依赖
      │
Week2 (4.13-4.17)  基础假设验证
  └─ week2/src/backtest_fast.py ← estimate_margin()至今仍被所有引擎调用
      │
Week3 (4.20-4.24)  参数优化（10个实验）
      │
Week4 (4.27-5.3)   日频引擎v3 + 全面验证
  └─ week4/src/strategy_engine_v3.py ← 核心层单策略引擎（已验证）
      │
子策略研究 (5月)    S1/S2/S3/S4独立验证
      │
组合策略 (5月)      S1+S3+S4三策略组合
  └─ 组合策略/src/unified_engine_v3.py ← 三策略组合引擎
      │
容量和品种预估       品种扫描+容量分析
  └─ 容量和品种预估/src/exp_product_count.py ← PRODUCT_MAP + scan_and_rank()
  └─ 容量和品种预估/src/scan_all_liquidity.py ← 流动性扫描
      │
策略生成订单及逐日回测  消除前视偏差 + T+1执行
  └─ strategy_rules.py ← 策略规则（无状态函数，规范版）
  └─ daily_backtest_t1vwap.py ← ★最新回测引擎（T+1 VWAP执行）
      │
提升夏普和卡玛       IV过滤、S4改进等优化研究
  └─ exp_s4_improve.py ← S4从2品种扩到20品种+15天持仓限制
      │
模拟盘              实盘运营系统
  └─ run_daily.py ← 每日订单生成器
```

## 最新版本文件清单

### 核心引擎（必须）
| 文件 | 来源 | 说明 |
|------|------|------|
| src/daily_backtest_t1vwap.py | 策略生成订单及逐日回测/ | ★最新回测引擎，T+1 VWAP执行 |
| src/strategy_rules.py | 策略生成订单及逐日回测/ | 策略规则（合约选择、仓位计算、止盈等） |
| src/backtest_fast.py | week2/ | estimate_margin() + load_product_data() |
| src/exp_product_count.py | 容量和品种预估/ | PRODUCT_MAP + scan_and_rank() |
| src/scan_all_liquidity.py | 容量和品种预估/ | 流动性扫描（被exp_product_count依赖） |

### 数据适配（Wind MySQL → SQLite）
| 文件 | 说明 |
|------|------|
| src/build_enriched_from_wind.py | 从Wind MySQL构建benchmark_wind.db |
| src/verify_data.py | 验证构建结果 |

### 组合引擎（参考）
| 文件 | 来源 | 说明 |
|------|------|------|
| src/unified_engine_v3.py | 组合策略/ | 三策略组合引擎（S1+S3+S4） |

### 文档
| 文件 | 说明 |
|------|------|
| docs/strategy_spec.md | 策略完整规格书 |
| docs/组合策略规则.md | 三策略组合规则 |
| config.json | 策略参数配置 |

## 运行步骤

```bash
# 1. 构建数据库（从Wind MySQL读取，生成SQLite）
python3 src/build_enriched_from_wind.py

# 2. 验证数据
python3 src/verify_data.py

# 3. 运行回测
python3 src/daily_backtest_t1vwap.py
```
