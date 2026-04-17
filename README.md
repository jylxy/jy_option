# 服务器部署包 — 逐分钟回测引擎 v3

从 Parquet 分钟线数据源（64亿行期权+2.3亿行期货+3.6亿行ETF）直接读取数据，
逐分钟处理事件循环，盘中止盈/Greeks风控/应急保护。

## 运行方式

```bash
# 标准运行
python src/true_minute_engine.py --start-date 2024-01-01

# 指定结束日期
python src/true_minute_engine.py --start-date 2024-01-01 --end-date 2026-03-31

# 详细日志
python src/true_minute_engine.py --start-date 2024-01-01 --verbose

# 自定义配置
python src/true_minute_engine.py --start-date 2024-01-01 --config /path/to/config.json
```

## 集成测试

```bash
python tests/test_integration.py          # 基础测试
python tests/test_integration.py --full   # 含多日连续测试
```

## 数据源

Parquet 文件（Spark 导出，所有列为 string 类型）：

| 文件 | 大小 | 内容 |
|------|------|------|
| OPTION1MINRESULT.parquet | 32GB | 期权分钟K线（64亿行） |
| FUTURE1MINRESULT.parquet | 5GB | 期货分钟K线（2.3亿行） |
| ETF1MINRESULT.parquet | 4.5GB | ETF分钟K线（3.6亿行） |
| CONTRACTINFORESULT.parquet | 1.9MB | 合约属性（19.5万行） |

默认路径：`/macro/home/lxy/yy_2_lxy_20260415/`
可通过环境变量 `PARQUET_DATA_DIR` 或 config.json 的 `parquet_data_dir` 覆盖。

## 文件清单

### 引擎核心

| 文件 | 说明 |
|------|------|
| `src/true_minute_engine.py` | 主引擎：逐分钟事件循环、持仓管理、开仓决策、NAV输出 |
| `src/parquet_loader.py` | 数据加载：ContractMaster + ParquetDayLoader + DaySlice |
| `src/iv_surface.py` | IV Smile 曲线：二次多项式拟合 + IV_Residual 选腿增强 |
| `src/intraday_monitor.py` | 盘中监控：Greeks汇总/阈值检查/止盈/应急保护/买卖价差 |

### 复用模块

| 文件 | 说明 |
|------|------|
| `src/option_calc.py` | IV反推 + Greeks计算（BSM，含numpy向量化版本） |
| `src/strategy_rules.py` | 策略规则纯函数（选腿/手数/止盈/应急保护） |
| `src/backtest_fast.py` | 保证金计算（按交易所区分公式） |
| `src/exp_product_count.py` | 品种映射 PRODUCT_MAP + 动态排名 |

### 配置与测试

| 文件 | 说明 |
|------|------|
| `config.json` | 策略参数（含 intraday_greeks_interval=15、spread_mode 等） |
| `tests/test_integration.py` | 集成测试（7个用例） |

## 盘中处理频率

| 检查项 | 频率 | 说明 |
|--------|------|------|
| 持仓价格更新 | 每分钟 | 用 Minute_Bar close 更新 cur_price/cur_spot |
| 应急保护 | 每分钟 | S3 卖腿 OTM% 阈值检查，时效性最高 |
| 止盈检查 | 每15分钟 | 扣手续费后净利润率，可配置 |
| IV/Greeks更新 | 每15分钟 | 反推IV → 更新delta/gamma/vega/theta |
| Greeks风控 | 每15分钟 | cash_delta/vega 超限触发减仓 |

## 依赖

```
python >= 3.10
numpy
pandas
pyarrow
scipy
py_vollib
py_vollib_vectorized  # 可选，加速IV反推
```

## 服务器环境

- Ubuntu 22.04, Python 3.10
- H200 256GB RAM
- 峰值内存 ~3GB（远低于限制）
