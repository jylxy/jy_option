# S1 Autoresearch

这个目录保存 S1 自动研究系统的实验队列、结果和审议记录。

## 文件

| 文件 | 说明 |
|---|---|
| `experiment_queue.jsonl` | 待运行或已运行实验，每行一个 JSON |
| `results.tsv` | 实验评分总表 |
| `round_review_protocol.md` | 每轮联合审议协议 |
| `ideas/` | 单个实验 idea 文件 |
| `reviews/` | 每轮实验审议 Markdown |

## 基本命令

```bash
python3 scripts/s1_autoresearch_runner.py init
python3 scripts/s1_autoresearch_runner.py add-idea experiments/s1_autoresearch/ideas/example.json
python3 scripts/s1_autoresearch_runner.py configure --id <experiment_id>
python3 scripts/s1_autoresearch_runner.py launch --id <experiment_id> --background
python3 scripts/s1_autoresearch_runner.py score --id <experiment_id>
python3 scripts/s1_autoresearch_runner.py audit --id <experiment_id>
python3 scripts/s1_autoresearch_runner.py review --id <experiment_id>
python3 scripts/s1_autoresearch_runner.py report --tag <tag> --baseline-tag <baseline_tag>
```

## 决策原则

不要只看 NAV。必须同时看：

- 是否改善 `Premium Pool × Deployment × Retention - Tail/Stop - Cost` 公式中的明确变量。
- 是否超过 A0/B2C 等当前基准。
- 是否满足年化 6%、最大回撤小于 2%、vega PnL 为正。
- 是否存在未来函数、成交口径、止损口径、保证金口径或数据异常。
