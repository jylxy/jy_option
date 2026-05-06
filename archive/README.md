# Server Deploy Archive

这个目录保存从 `src/` 根目录移出的历史脚本和旧引擎代码。

归档原则：

- 不再被当前 `toolkit_minute_engine.py` 主路径引用。
- 保留源码，方便追溯旧实验或数据处理方法。
- 不作为生产回测入口维护。

当前生产入口仍是：

```bash
python3 src/toolkit_minute_engine.py
```

## 目录

| 目录 | 内容 |
|---|---|
| `src_scratch/` | 一次性检查脚本，例如 theta/vega/LIKE/debug 检查 |
| `data_maintenance/` | 历史 Parquet 分区维护脚本 |
| `legacy_experiments/` | 旧版实验脚本和内存版回测 |
| `true_minute_engine/` | 旧 Parquet true-minute engine 及其依赖 |
