#!/usr/bin/env python3
"""Audit S1 autoresearch experiments for implementation and result risks."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd


CRITICAL_PATTERNS = [
    ("intraday_exit_skips_daily_stop", "intraday_exit_done may disable daily stop fallback."),
]


def load_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_config_chain(config_path: Path, root: Path) -> Dict[str, object]:
    config = load_json(config_path)
    parent = config.get("extends")
    if parent:
        parent_path = root / str(parent)
        merged = load_config_chain(parent_path, root)
        merged.update(config)
        return merged
    return config


def source_contains(root: Path, rel: str, pattern: str) -> bool:
    path = root / rel
    if not path.exists():
        return False
    return pattern in path.read_text(encoding="utf-8", errors="ignore")


def audit_source(root: Path) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    engine = root / "src" / "toolkit_minute_engine.py"
    text = engine.read_text(encoding="utf-8", errors="ignore") if engine.exists() else ""
    if (
        "intraday_exit_done = self._process_intraday_exits" in text
        and "run_risk_and_tp=not intraday_exit_done" in text
        and "return closed_any" not in text
    ):
        findings.append({
            "severity": "critical",
            "code": "intraday_exit_skips_daily_stop",
            "message": "盘中扫描即使没有真实平仓，也可能关闭日频止损兜底。",
        })
    return findings


def audit_config(config: Dict[str, object], config_path: Path) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    if not config:
        findings.append({"severity": "critical", "code": "missing_config", "message": f"缺少配置文件: {config_path}"})
        return findings
    if config.get("s1_layered_stop_enabled"):
        levels = config.get("s1_layered_stop_levels") or []
        if not levels:
            findings.append({"severity": "critical", "code": "layered_stop_no_levels", "message": "已启用分层止损，但没有配置止损层级。"})
        for level in levels:
            action = str(level.get("action", "close")).lower()
            scope = str(level.get("scope", "contract")).lower()
            multiple = float(level.get("multiple", 0.0) or 0.0)
            if action == "warn":
                findings.append({"severity": "warning", "code": "warn_level", "message": f"{multiple}x 的 warn 层级不会降低仓位风险。"})
            if action == "reduce" and scope == "contract":
                findings.append({"severity": "warning", "code": "contract_reduce_tail", "message": f"{multiple}x 单合约减仓可能仍留下同品种同侧尾部暴露。"})
    if float(config.get("premium_stop_multiple", 0.0) or 0.0) <= 0 and not config.get("s1_layered_stop_enabled"):
        findings.append({"severity": "warning", "code": "no_stop", "message": "权利金止损看起来被关闭。"})
    return findings


def audit_outputs(root: Path, tag: str) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    nav_path = root / "output" / f"nav_{tag}.csv"
    if not nav_path.exists():
        findings.append({"severity": "warning", "code": "missing_nav", "message": f"未找到 NAV 文件: {nav_path}"})
        return findings
    df = pd.read_csv(nav_path)
    if df.empty:
        findings.append({"severity": "critical", "code": "empty_nav", "message": "NAV 文件为空。"})
        return findings
    if "date" in df.columns and df["date"].duplicated().any():
        findings.append({"severity": "critical", "code": "duplicate_nav_dates", "message": "NAV 存在重复日期。"})
    nav = pd.to_numeric(df.get("nav"), errors="coerce")
    if nav.isna().any():
        findings.append({"severity": "critical", "code": "nav_nan", "message": "NAV 存在 NaN。"})
    dd = nav / nav.cummax() - 1.0
    mdd = float(dd.min())
    if np.isfinite(mdd) and abs(mdd) > 0.05:
        findings.append({"severity": "warning", "code": "large_drawdown", "message": f"最大回撤超过 5%: {mdd:.2%}"})
    if "margin_used" in df.columns:
        margin_ratio = pd.to_numeric(df["margin_used"], errors="coerce") / nav.replace(0, np.nan)
        max_margin = float(margin_ratio.max())
        if np.isfinite(max_margin) and max_margin > 0.65:
            findings.append({"severity": "warning", "code": "margin_spike", "message": f"保证金使用率超过 65%: {max_margin:.2%}"})
    if "vega_pnl" in df.columns:
        vega = float(pd.to_numeric(df["vega_pnl"], errors="coerce").fillna(0.0).sum())
        if vega < 0:
            findings.append({"severity": "warning", "code": "negative_vega_pnl", "message": f"Vega PnL 为负: {vega:.0f}"})
    return findings


def audit_status(findings: List[Dict[str, str]]) -> str:
    if any(f["severity"] == "critical" for f in findings):
        return "needs_code_fix"
    if findings:
        return "warning"
    return "pass"


def write_report(path: Path, tag: str, config_path: Path, findings: List[Dict[str, str]], status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# S1 experiment audit - {tag}",
        "",
        f"- Time: {datetime.now().isoformat(timespec='seconds')}",
        f"- Config: `{config_path}`",
        f"- Status: `{status}`",
        "",
        "## Findings",
        "",
    ]
    if not findings:
        lines.append("No audit findings.")
    else:
        lines.append("| Severity | Code | Message |")
        lines.append("|---|---|---|")
        for f in findings:
            lines.append(f"| {f['severity']} | `{f['code']}` | {f['message']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit S1 autoresearch experiment.")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--config", default="")
    parser.add_argument("--root", default=".")
    parser.add_argument("--results-dir", default="experiments/s1_autoresearch")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    config_path = root / args.config if args.config else Path()
    config = load_config_chain(config_path, root) if args.config else {}
    findings = []
    findings.extend(audit_source(root))
    if args.config:
        findings.extend(audit_config(config, config_path))
    findings.extend(audit_outputs(root, args.tag))
    status = audit_status(findings)
    payload = {
        "tag": args.tag,
        "config": str(config_path) if args.config else "",
        "status": status,
        "findings": findings,
    }
    results_dir = root / args.results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"audit_{args.tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(results_dir / "reviews" / f"audit_{args.tag}.md", args.tag, config_path, findings, status)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status != "needs_code_fix" else 2


if __name__ == "__main__":
    raise SystemExit(main())
