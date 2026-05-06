# Tests 目录说明

当前正式回测入口已经切换到 `src/toolkit_minute_engine.py`。原来的 `tests/test_integration.py` 依赖旧版 `true_minute_engine.py`、`parquet_loader.py`、`iv_surface.py` 和 `intraday_monitor.py`，这些模块已经归档，因此该测试也同步移动到 `archive/legacy_tests/test_true_minute_integration.py`。

本目录仍保留当前主路径的一批模块级测试，覆盖配置、成本、保证金、预算、组合风控、IV warmup、品种生命周期、spot 映射、策略规则等。后续新增代码时，应继续补小单测，而不是只依赖长周期回测验证。

下一批优先补强：

1. `stop_policy.py`：单合约止损、同代码止损、整组止损、分层止损。
2. `intraday_execution.py`：已覆盖日高预筛、阈值、流动性和确认逻辑；后续补触发分钟、下一分钟最高价、异常价确认、缺失分钟数据回退。
3. `open_execution.py`：全日成交量约束、延迟开仓拆分、开仓审计行。
4. `s1_pending_open.py`：待开仓字段完整性和预算覆盖顺序。
5. `margin_model.py`：商品、ETF、股指期权保证金公式口径。
6. `strategy_rules.py`：候选排序、预算倾斜、低价过滤、P/C 侧倾斜。

建议每次重构至少补一个最小单元测试，避免主引擎继续变成只能靠长周期回测验证的黑盒。
