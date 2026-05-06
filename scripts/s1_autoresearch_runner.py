#!/usr/bin/env python3
"""Autoresearch runner for S1 option-selling experiments.

This runner is deliberately conservative. In phase 1 it automates experiment
registration, config generation, launch, scoring, auditing, review notes and
report pack generation. It does not edit core engine code automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
EXP_DIR = ROOT / "experiments" / "s1_autoresearch"
IDEA_DIR = EXP_DIR / "ideas"
REVIEW_DIR = EXP_DIR / "reviews"
LOG_DIR = ROOT / "logs"
QUEUE_PATH = EXP_DIR / "experiment_queue.jsonl"
RESULTS_PATH = EXP_DIR / "results.tsv"


LEDGER_HEADER = (
    "timestamp\texperiment_id\ttag\tstatus\tformula_variable\tsample_role\t"
    "start_date\tend_date\ttotal_return\tannual_return\tmax_drawdown\t"
    "sharpe\tcalmar\tvega_pnl\ttheta_pnl\tpremium_retention\tstop_count\t"
    "margin_mean\tbaseline_tag\texcess_total_return\tnotes\n"
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for path in [EXP_DIR, IDEA_DIR, REVIEW_DIR, LOG_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    if not QUEUE_PATH.exists():
        QUEUE_PATH.write_text("", encoding="utf-8")
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text(LEDGER_HEADER, encoding="utf-8")


def read_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_queue() -> List[Dict[str, object]]:
    ensure_dirs()
    records: List[Dict[str, object]] = []
    for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def write_queue(records: Iterable[Dict[str, object]]) -> None:
    ensure_dirs()
    with QUEUE_PATH.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def find_record(experiment_id: str) -> Optional[Dict[str, object]]:
    for record in read_queue():
        if str(record.get("id")) == experiment_id:
            return record
    return None


def update_record(experiment_id: str, **updates: object) -> Dict[str, object]:
    records = read_queue()
    for idx, record in enumerate(records):
        if str(record.get("id")) == experiment_id:
            record.update(updates)
            record["updated_at"] = now_iso()
            records[idx] = record
            write_queue(records)
            return record
    raise SystemExit(f"experiment id not found: {experiment_id}")


def deep_merge(base: Dict[str, object], patch: Dict[str, object]) -> Dict[str, object]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)  # type: ignore[arg-type]
        else:
            merged[key] = value
    return merged


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def resolve_root_path(raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return ROOT / raw


def load_config_chain(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    config = read_json(path)
    parent = config.get("extends")
    if parent:
        parent_path = resolve_root_path(str(parent))
        if parent_path is None:
            raise SystemExit(f"bad extends path in {path}")
        parent_config = load_config_chain(parent_path)
        return deep_merge(parent_config, config)
    return config


def cmd_init(_: argparse.Namespace) -> int:
    ensure_dirs()
    print(f"initialized: {EXP_DIR}")
    return 0


def validate_idea(idea: Dict[str, object]) -> None:
    required = ["id", "tag", "hypothesis", "formula_variable", "base_config", "start_date"]
    missing = [key for key in required if not idea.get(key)]
    if missing:
        raise SystemExit(f"idea missing required fields: {', '.join(missing)}")
    if not isinstance(idea.get("config_patch", {}), dict):
        raise SystemExit("idea.config_patch must be an object")


def cmd_add_idea(args: argparse.Namespace) -> int:
    ensure_dirs()
    idea_path = Path(args.idea)
    if not idea_path.is_absolute():
        idea_path = Path.cwd() / idea_path
    idea = read_json(idea_path)
    validate_idea(idea)
    experiment_id = str(idea["id"])
    if find_record(experiment_id):
        raise SystemExit(f"experiment already exists: {experiment_id}")
    target_path = IDEA_DIR / f"{experiment_id}.json"
    write_json(target_path, idea)
    record = dict(idea)
    record.update(
        {
            "status": "proposed",
            "idea_path": rel_path(target_path),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    records = read_queue()
    records.append(record)
    write_queue(records)
    print(f"added idea: {experiment_id}")
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    ensure_dirs()
    record = find_record(args.id)
    if not record:
        raise SystemExit(f"experiment id not found: {args.id}")
    tag = str(record["tag"])
    base_config_path = resolve_root_path(str(record["base_config"]))
    if base_config_path is None:
        raise SystemExit("base_config is empty")
    patch = record.get("config_patch", {})
    if not isinstance(patch, dict):
        raise SystemExit("config_patch must be an object")
    base_config = load_config_chain(base_config_path)
    config = deep_merge(base_config, patch)
    config["autoresearch_experiment_id"] = record["id"]
    config["autoresearch_hypothesis"] = record["hypothesis"]
    config["autoresearch_formula_variable"] = record.get("formula_variable", "")
    config_name = str(record.get("config_name") or f"config_{tag}.json")
    config_path = ROOT / config_name
    write_json(config_path, config)
    update_record(args.id, status="configured", config=rel_path(config_path))
    print(f"configured: {rel_path(config_path)}")
    return 0


def build_backtest_command(record: Dict[str, object], config_path: Path) -> List[str]:
    cmd = [
        sys.executable,
        str(ROOT / "src" / "toolkit_minute_engine.py"),
        "--start-date",
        str(record["start_date"]),
        "--tag",
        str(record["tag"]),
        "--config",
        str(config_path),
    ]
    if record.get("end_date"):
        cmd += ["--end-date", str(record["end_date"])]
    if record.get("products"):
        cmd += ["--products", str(record["products"])]
    if record.get("verbose"):
        cmd += ["--verbose"]
    return cmd


def cmd_launch(args: argparse.Namespace) -> int:
    ensure_dirs()
    record = find_record(args.id)
    if not record:
        raise SystemExit(f"experiment id not found: {args.id}")
    if not record.get("config"):
        cmd_configure(argparse.Namespace(id=args.id))
        record = find_record(args.id)
        if not record:
            raise SystemExit(f"experiment id not found after configure: {args.id}")
    config_path = resolve_root_path(str(record["config"]))
    if config_path is None or not config_path.exists():
        raise SystemExit(f"config not found: {record.get('config')}")
    cmd = build_backtest_command(record, config_path)
    log_path = LOG_DIR / f"{record['tag']}.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    if args.background:
        log_fh = log_path.open("ab")
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log_fh, stderr=subprocess.STDOUT)
        update_record(
            args.id,
            status="running",
            launched_at=now_iso(),
            pid=proc.pid,
            log=rel_path(log_path),
            command=" ".join(cmd),
        )
        print(f"launched: {record['tag']} pid={proc.pid} log={rel_path(log_path)}")
        return 0
    result = subprocess.run(cmd, cwd=str(ROOT), env=env, check=False)
    status = "completed_process" if result.returncode == 0 else "failed_process"
    update_record(args.id, status=status, returncode=result.returncode, log=rel_path(log_path))
    return result.returncode


def run_subprocess(cmd: List[str], allow_failure: bool = False) -> int:
    result = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if result.returncode and not allow_failure:
        raise SystemExit(result.returncode)
    return result.returncode


def cmd_score(args: argparse.Namespace) -> int:
    ensure_dirs()
    record = find_record(args.id) if args.id else None
    tag = args.tag or (str(record["tag"]) if record else None)
    if not tag:
        raise SystemExit("score requires --tag or --id")
    baseline_tag = args.baseline_tag or (str(record.get("baseline_tag")) if record else None)
    experiment_id = args.id or (str(record.get("id")) if record else tag)
    formula_variable = args.formula_variable or (str(record.get("formula_variable")) if record else "")
    sample_role = args.sample_role or (str(record.get("sample_role")) if record else "")
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "s1_experiment_scorecard.py"),
        "--tag",
        tag,
        "--experiment-id",
        experiment_id,
        "--formula-variable",
        formula_variable,
        "--sample-role",
        sample_role,
        "--write-ledger",
    ]
    if baseline_tag:
        cmd += ["--baseline-tag", baseline_tag]
    if args.status:
        cmd += ["--status", args.status]
    if args.notes:
        cmd += ["--notes", args.notes]
    rc = run_subprocess(cmd)
    if record:
        update_record(str(record["id"]), status="scored", scored_at=now_iso())
    return rc


def cmd_audit(args: argparse.Namespace) -> int:
    ensure_dirs()
    record = find_record(args.id) if args.id else None
    tag = args.tag or (str(record["tag"]) if record else None)
    config = args.config or (str(record.get("config")) if record else None)
    if not tag:
        raise SystemExit("audit requires --tag or --id")
    cmd = [sys.executable, str(ROOT / "scripts" / "s1_experiment_audit.py"), "--tag", tag]
    if config:
        cmd += ["--config", config]
    rc = run_subprocess(cmd, allow_failure=True)
    if record:
        status = "needs_code_fix" if rc == 2 else "audited"
        update_record(str(record["id"]), status=status, audited_at=now_iso(), audit_returncode=rc)
    return rc


def safe_metric(metrics: Dict[str, object], key: str) -> str:
    value = metrics.get(key, "")
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def read_optional_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    return read_json(path)


def make_review_text(
    tag: str,
    record: Optional[Dict[str, object]],
    scorecard: Dict[str, object],
    audit: Dict[str, object],
) -> str:
    metrics = scorecard.get("metrics", {})
    comparison = scorecard.get("comparison", {})
    if not isinstance(metrics, dict):
        metrics = {}
    if not isinstance(comparison, dict):
        comparison = {}
    findings = audit.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    annual = float(metrics.get("annual_return") or 0.0)
    mdd = float(metrics.get("max_drawdown") or 0.0)
    vega = float(metrics.get("vega_pnl") or 0.0)
    theta = float(metrics.get("theta_pnl") or 0.0)
    excess = comparison.get("excess_total_return", "")
    critical_count = sum(1 for item in findings if isinstance(item, dict) and item.get("severity") == "critical")
    target_hit = annual >= 0.06 and mdd >= -0.02 and vega > 0
    if critical_count:
        decision = "needs_rerun_after_code_fix"
    elif target_hit:
        decision = "keep_for_oos_validation"
    elif annual > 0 and vega > 0 and mdd > -0.05:
        decision = "keep_as_candidate"
    else:
        decision = "discard_or_diagnose"
    hypothesis = str(record.get("hypothesis")) if record else ""
    formula_variable = str(record.get("formula_variable")) if record else ""
    sample_role = str(record.get("sample_role")) if record else ""
    lines = [
        f"# S1 Autoresearch Review - {tag}",
        "",
        f"- timestamp: {now_iso()}",
        f"- decision: {decision}",
        f"- 实验假设: {hypothesis}",
        f"- 公式变量: {formula_variable}",
        f"- 样本角色: {sample_role}",
        "",
        "## 主 Agent 审议",
        "",
        (
            "本实验按 S1 收益拆解公式评估："
            "Premium Pool x Deployment Ratio x Retention Rate - Tail / Stop Loss - Cost / Slippage。"
        ),
        f"累计收益为 {safe_metric(metrics, 'total_return')}，年化收益为 {safe_metric(metrics, 'annual_return')}，最大回撤为 {safe_metric(metrics, 'max_drawdown')}。",
        f"相对基准累计超额为 {excess}。",
        "",
        "## 期权策略专家审议",
        "",
        f"Theta PnL 为 {theta:.2f}，Vega PnL 为 {vega:.2f}。",
        "S1 候选不能只看 NAV：必须确认权利金留得住，并且 vega 不能长期为负。",
        "如果实验靠提高权利金池改善 NAV，但同步放大 vega 损耗或止损尾部，它更像风险转移，不应直接视为 alpha。",
        "",
        "## 代码专家审议",
        "",
    ]
    if findings:
        for item in findings[:8]:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('severity', 'info')}] {item.get('code', '')}: {item.get('message', '')}")
    else:
        lines.append("- 自动审计没有发现明确问题。")
    lines += [
        "",
        "## Skeptic 审议",
        "",
        "这份审议不是稳健性证明。候选仍需要共同截止日对比、样本外验证、成本敏感性和尾部时期拆解。",
        "如果样本角色只是 sample 或 stress，不能因为单段表现好就升级为生产规则。",
        "",
        "## 下一轮方向",
        "",
    ]
    if critical_count:
        lines.append("- 先修实现路径并重跑，再解释绩效。")
    elif vega <= 0:
        lines.append("- 优先诊断为什么 short-vol 仍然亏 vega，下一步重点看 forward-vega、vol-of-vol 和止损路径控制。")
    elif annual < 0.06:
        lines.append("- 在不削弱留存率的前提下提高权利金池或部署率，不要第一反应就单纯加杠杆。")
    elif mdd < -0.02:
        lines.append("- 收益引擎可以保留，但下一轮必须转向尾部、止损簇集和组合风险控制。")
    else:
        lines.append("- 可以进入更长样本、样本外和 regime 分层验证。")
    lines.append("")
    return "\n".join(lines)


def cmd_review(args: argparse.Namespace) -> int:
    ensure_dirs()
    record = find_record(args.id) if args.id else None
    tag = args.tag or (str(record["tag"]) if record else None)
    if not tag:
        raise SystemExit("review requires --tag or --id")
    scorecard = read_optional_json(EXP_DIR / f"scorecard_{tag}.json")
    audit = read_optional_json(EXP_DIR / f"audit_{tag}.json")
    review_text = make_review_text(tag, record, scorecard, audit)
    review_path = REVIEW_DIR / f"review_{tag}.md"
    review_path.write_text(review_text, encoding="utf-8")
    if record:
        update_record(str(record["id"]), status="reviewed", reviewed_at=now_iso(), review=rel_path(review_path))
    print(f"review: {rel_path(review_path)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    ensure_dirs()
    tag = args.tag
    analysis_dir = ROOT / "output" / f"analysis_{tag}"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "analyze_backtest_outputs.py"),
        "--tag",
        tag,
        "--out-dir",
        str(analysis_dir),
    ]
    if args.baseline_tag:
        cmd += ["--baseline-tag", args.baseline_tag]
    run_subprocess(cmd, allow_failure=False)
    review_path = REVIEW_DIR / f"review_{tag}.md"
    if not review_path.exists():
        dummy_record = {"tag": tag, "hypothesis": "manual report", "formula_variable": "", "sample_role": ""}
        review_path.write_text(make_review_text(tag, dummy_record, {}, {}), encoding="utf-8")
    docx_path = analysis_dir / f"s1_autoresearch_review_{tag}.docx"
    cmd_docx = [
        sys.executable,
        str(ROOT / "scripts" / "build_s1_report_docx.py"),
        "--tag",
        tag,
        "--markdown",
        str(review_path),
        "--analysis-dir",
        str(analysis_dir),
        "--output-docx",
        str(docx_path),
        "--skip-markdown-images",
    ]
    rc = run_subprocess(cmd_docx, allow_failure=True)
    print(f"analysis_dir: {rel_path(analysis_dir)}")
    print(f"docx: {rel_path(docx_path)}")
    return rc


def cmd_status(args: argparse.Namespace) -> int:
    ensure_dirs()
    records = read_queue()
    if args.id:
        records = [r for r in records if str(r.get("id")) == args.id]
    for record in records:
        tag = str(record.get("tag", ""))
        nav_path = ROOT / "output" / f"nav_{tag}.csv"
        log_path = resolve_root_path(str(record.get("log") or "")) if record.get("log") else None
        print(
            "\t".join(
                [
                    str(record.get("id", "")),
                    str(record.get("status", "")),
                    tag,
                    "nav=yes" if nav_path.exists() else "nav=no",
                    rel_path(log_path) if log_path else "",
                ]
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    add = sub.add_parser("add-idea")
    add.add_argument("idea")
    add.set_defaults(func=cmd_add_idea)
    cfg = sub.add_parser("configure")
    cfg.add_argument("--id", required=True)
    cfg.set_defaults(func=cmd_configure)
    launch = sub.add_parser("launch")
    launch.add_argument("--id", required=True)
    launch.add_argument("--background", action="store_true")
    launch.add_argument("--dry-run", action="store_true")
    launch.set_defaults(func=cmd_launch)
    score = sub.add_parser("score")
    score.add_argument("--id")
    score.add_argument("--tag")
    score.add_argument("--baseline-tag")
    score.add_argument("--formula-variable")
    score.add_argument("--sample-role")
    score.add_argument("--status")
    score.add_argument("--notes")
    score.set_defaults(func=cmd_score)
    audit = sub.add_parser("audit")
    audit.add_argument("--id")
    audit.add_argument("--tag")
    audit.add_argument("--config")
    audit.set_defaults(func=cmd_audit)
    review = sub.add_parser("review")
    review.add_argument("--id")
    review.add_argument("--tag")
    review.set_defaults(func=cmd_review)
    report = sub.add_parser("report")
    report.add_argument("--tag", required=True)
    report.add_argument("--baseline-tag")
    report.set_defaults(func=cmd_report)
    status = sub.add_parser("status")
    status.add_argument("--id")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
