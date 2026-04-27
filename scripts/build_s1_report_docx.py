#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a Feishu-import friendly DOCX report for S1 backtest analysis.

The script intentionally keeps the source file UTF-8 and reads/writes all text
with explicit encodings. It converts a Markdown report into DOCX, then appends
standard chart chapters with embedded PNGs and deterministic commentary.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

try:
    import pandas as pd
except ImportError:  # pragma: no cover - script is expected to run in Codex runtime.
    pd = None

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt
except ImportError as exc:  # pragma: no cover
    raise SystemExit("python-docx is required. Use the bundled Codex Python runtime.") from exc


CHART_SPECS = [
    {
        "file": "01_nav_drawdown.png",
        "title": "净值曲线与最大回撤",
        "read": "这张图同时看收益斜率和回撤深度，重点观察收益是否平滑、回撤是否集中在少数事件日。",
        "meaning": "如果净值主要靠长时间小幅爬升、少数日期大幅回撤，说明策略本质仍是卖尾部保险，需要继续控制跳跃和升波阶段。",
    },
    {
        "file": "02_margin_positions.png",
        "title": "保证金使用率与持仓数量",
        "read": "这张图看仓位是否真正打满、持仓品种和合约数是否稳定，以及回撤期是否被动降仓。",
        "meaning": "若保证金稳定但收益不高，问题通常不在仓位太低，而在单位风险的 theta 质量、gamma/vega 吞噬和合约选择效率。",
    },
    {
        "file": "03_greeks_timeseries.png",
        "title": "组合 Greeks 时序",
        "read": "这张图看 cash delta、cash gamma、cash vega 是否长期偏向某一方向，以及尾部日期前是否已经暴露过高。",
        "meaning": "卖权策略可以接受 short gamma 和 short vega，但必须确认这些暴露获得了足够 theta 补偿，否则就是低质量卖权。",
    },
    {
        "file": "04_pnl_attribution.png",
        "title": "PnL 归因时序",
        "read": "这张图拆解 delta、gamma、theta、vega 和 residual 的累计贡献，判断策略到底赚的是 carry 还是方向。",
        "meaning": "目标画像应是 theta 稳定为正、vega 在降波段贡献为正、gamma 亏损受控；如果 vega 长期为负，说明入场环境或 IV 口径仍需优化。",
    },
    {
        "file": "05_daily_pnl_tail.png",
        "title": "日度 PnL 左尾",
        "read": "这张图看最差日期是否集中在升波、跳空或系统性事件里，以及单日亏损是否吞掉多月 theta。",
        "meaning": "卖方策略的真实质量不由胜率决定，而由最差日、最差周和回撤修复速度决定。",
    },
    {
        "file": "06_premium_pc_structure.png",
        "title": "权利金和 P/C 结构",
        "read": "这张图看 Put/Call 暴露是否稳定、是否出现单侧拥挤，以及 open premium 与 liability 是否同步变化。",
        "meaning": "P/C 极端偏离会把卖波策略变成隐含方向策略，后续应加入趋势置信度、单侧预算和再平衡规则。",
    },
    {
        "file": "07_vol_regime_exposure.png",
        "title": "波动状态暴露",
        "read": "这张图看仓位是否集中在 falling、low、normal、high、post-stop 等状态，以及不同状态下的收益质量。",
        "meaning": "如果 falling 状态仓位不足，策略会错过最适合卖方的降波收益；如果 low-vol 状态仓位过重，则容易在波动切换时受伤。",
    },
    {
        "file": "08_calendar_returns.png",
        "title": "月度收益热力图",
        "read": "这张图看收益是否跨年份稳定，还是集中在少数月份；同时定位关键回撤月份。",
        "meaning": "稳定卖权策略不需要每月赚钱，但不能让少数月份反复吃掉全年 theta。",
    },
    {
        "file": "09_product_share_top10.png",
        "title": "Top10 品种持仓占比",
        "read": "这张图看真实仓位是否被少数品种主导，以及品种集中度是否随时间升高。",
        "meaning": "全品种扫描不等于真正分散，如果成交和 Delta 规则导致实际只集中在少数商品，组合仍然有板块和相关性尾部风险。",
    },
    {
        "file": "10_order_action_summary.png",
        "title": "订单动作汇总",
        "read": "这张图看开仓、到期、止损、期末持仓等动作比例，判断收益主要来自持有到期还是频繁止损。",
        "meaning": "若止损占比过高，说明入场筛选或异常报价过滤不足；若几乎全到期，则要重点检查到期前 gamma 风险。",
    },
    {
        "file": "11_close_event_timeline.png",
        "title": "平仓事件时间线",
        "read": "这张图看止损是否簇集、是否集中在回撤月份，以及止损后策略是否过快重开同类风险。",
        "meaning": "止损簇集是卖方策略的核心风险信号，后续冷却期和波动回落确认应围绕它设计。",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build S1 backtest DOCX report with embedded charts.")
    parser.add_argument("--tag", required=True, help="Backtest tag, e.g. s1_b0_standard_stop25_allprod_2022_latest.")
    parser.add_argument("--markdown", required=True, type=Path, help="Source Markdown report.")
    parser.add_argument("--analysis-dir", type=Path, help="Analysis directory. Default: output/analysis_<tag>.")
    parser.add_argument("--output-docx", type=Path, help="Output DOCX path.")
    parser.add_argument("--title", default=None, help="Document title override.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root.")
    parser.add_argument("--skip-existing-chart-section", action="store_true", help="Do not append chart chapters.")
    return parser.parse_args()


def fmt_num(value: object, decimals: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(number):
        return "N/A"
    return f"{number:,.{decimals}f}"


def fmt_pct(value: object, decimals: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(number):
        return "N/A"
    return f"{number * 100:.{decimals}f}%"


def safe_read_csv(path: Path) -> Optional["pd.DataFrame"]:
    if pd is None or not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def read_metrics_csv(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[0] if rows else {}


def set_cell_text(cell, text: str) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def set_default_style(doc: Document) -> None:
    styles = doc.styles
    for style_name in ("Normal", "Body Text"):
        if style_name in styles:
            style = styles[style_name]
            style.font.name = "Microsoft YaHei"
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            style.font.size = Pt(10.5)
    for style_name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        if style_name in styles:
            style = styles[style_name]
            style.font.name = "Microsoft YaHei"
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def add_paragraph(doc: Document, text: str, style: Optional[str] = None):
    paragraph = doc.add_paragraph(style=style)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = text.replace("`", "")
    run = paragraph.add_run(text)
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    return paragraph


def add_table(doc: Document, rows: Sequence[Sequence[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    if width > 6:
        add_paragraph(doc, "（宽表已自动转为摘要列表，便于飞书阅读。）")
        headers = [cell.strip() for cell in rows[0]]
        for row in rows[1:]:
            parts = []
            for col_idx, header in enumerate(headers):
                if col_idx >= len(row):
                    continue
                value = row[col_idx].strip()
                if value:
                    parts.append(f"{header}: {value}")
            if parts:
                add_paragraph(doc, "；".join(parts), style="List Bullet")
        return
    table = doc.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    for row_idx, row in enumerate(rows):
        for col_idx in range(width):
            value = row[col_idx] if col_idx < len(row) else ""
            set_cell_text(table.cell(row_idx, col_idx), value)
            if row_idx == 0:
                for run in table.cell(row_idx, col_idx).paragraphs[0].runs:
                    run.bold = True


def is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip().strip("|").strip()
    return bool(stripped) and all(ch in "-:| " for ch in stripped)


def parse_table(lines: Sequence[str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in lines:
        if is_markdown_table_separator(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def add_markdown_to_doc(doc: Document, markdown_path: Path) -> None:
    text = markdown_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].rstrip()
        if not line:
            idx += 1
            continue

        if line.startswith("|"):
            table_lines = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                table_lines.append(lines[idx])
                idx += 1
            add_table(doc, parse_table(table_lines))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading_match:
            level = min(len(heading_match.group(1)), 3)
            title = heading_match.group(2).replace("`", "")
            if level == 1:
                paragraph = doc.add_heading(title, level=0)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                doc.add_heading(title, level=level - 1)
            idx += 1
            continue

        if line.startswith("- "):
            add_paragraph(doc, line[2:], style="List Bullet")
            idx += 1
            continue

        add_paragraph(doc, line)
        idx += 1


def summarize_nav(nav_df: Optional["pd.DataFrame"]) -> Dict[str, str]:
    if nav_df is None or nav_df.empty:
        return {}
    result: Dict[str, str] = {}
    for col in [
        "s1_active_sell_products",
        "s1_active_sell_contracts",
        "s1_active_sell_lots",
        "s1_short_open_premium_pct",
        "s1_short_liability_pct",
        "s1_put_call_lot_ratio",
        "s1_call_lot_share",
    ]:
        if col in nav_df.columns:
            result[f"{col}_mean"] = fmt_num(nav_df[col].mean(), 2)
            result[f"{col}_max"] = fmt_num(nav_df[col].max(), 2)
    if "s1_put_call_lot_ratio" in nav_df.columns:
        ratio = nav_df["s1_put_call_lot_ratio"].replace([math.inf, -math.inf], pd.NA).dropna()
        if len(ratio):
            result["pc_gt_2_days"] = str(int((ratio > 2).sum()))
            result["pc_gt_5_days"] = str(int((ratio > 5).sum()))
    return result


def get_worst_month(analysis_dir: Path) -> str:
    monthly = safe_read_csv(analysis_dir / "monthly_returns.csv")
    if monthly is None or monthly.empty:
        return "N/A"
    ret_col = "return" if "return" in monthly.columns else monthly.columns[-1]
    date_col = "month" if "month" in monthly.columns else monthly.columns[0]
    worst = monthly.sort_values(ret_col).iloc[0]
    return f"{worst[date_col]} ({fmt_pct(worst[ret_col])})"


def get_top_products(analysis_dir: Path) -> str:
    products = safe_read_csv(analysis_dir / "open_sell_product_summary.csv")
    if products is None or products.empty:
        return "N/A"
    lots_col = "open_sell_lots" if "open_sell_lots" in products.columns else products.columns[-1]
    name_col = "product" if "product" in products.columns else products.columns[0]
    top = products.sort_values(lots_col, ascending=False).head(5)
    return "、".join(f"{row[name_col]}({fmt_num(row[lots_col], 0)}手)" for _, row in top.iterrows())


def get_stop_products(analysis_dir: Path) -> str:
    stops = safe_read_csv(analysis_dir / "stop_loss_product_summary.csv")
    if stops is None or stops.empty:
        return "N/A"
    pnl_col = "net_pnl" if "net_pnl" in stops.columns else stops.columns[-1]
    name_col = "product" if "product" in stops.columns else stops.columns[0]
    top = stops.sort_values(pnl_col).head(5)
    return "、".join(f"{row[name_col]}({fmt_num(row[pnl_col], 0)})" for _, row in top.iterrows())


def get_core_close_reason(analysis_dir: Path, tag: str) -> str:
    audit_path = analysis_dir / "core_audit" / f"core_audit_{tag}.csv"
    audit = safe_read_csv(audit_path)
    if audit is None or audit.empty or "close_reason" not in audit.columns:
        return "N/A"
    audit = audit.dropna(subset=["close_reason"])
    audit = audit[audit["close_reason"].astype(str).str.strip() != ""]
    if audit.empty:
        return "N/A"
    grouped = audit.groupby("close_reason", dropna=False)["net_pnl"].agg(["count", "sum"]).sort_values("sum")
    parts = []
    for reason, row in grouped.iterrows():
        parts.append(f"{reason}: {int(row['count'])}笔, PnL {fmt_num(row['sum'], 0)}")
    return "；".join(parts)


def build_context(tag: str, analysis_dir: Path, repo_root: Path) -> Dict[str, str]:
    metrics = read_metrics_csv(analysis_dir / "summary_metrics.csv")
    nav_path = repo_root / "output" / f"nav_{tag}.csv"
    nav = safe_read_csv(nav_path)
    nav_stats = summarize_nav(nav)
    context = {
        "total_return": fmt_pct(metrics.get("total_return")),
        "cagr": fmt_pct(metrics.get("cagr")),
        "max_drawdown": fmt_pct(metrics.get("max_drawdown")),
        "ann_vol": fmt_pct(metrics.get("ann_vol")),
        "raw_sharpe": fmt_num(metrics.get("sharpe"), 2),
        "avg_margin": fmt_pct(metrics.get("avg_margin_pct")),
        "max_margin": fmt_pct(metrics.get("max_margin_pct")),
        "cum_theta": fmt_num(metrics.get("cum_theta_pnl"), 0),
        "cum_vega": fmt_num(metrics.get("cum_vega_pnl"), 0),
        "cum_gamma": fmt_num(metrics.get("cum_gamma_pnl"), 0),
        "cum_delta": fmt_num(metrics.get("cum_delta_pnl"), 0),
        "cum_residual": fmt_num(metrics.get("cum_residual_pnl"), 0),
        "worst_day": fmt_pct(metrics.get("worst_day_return")),
        "worst_month": get_worst_month(analysis_dir),
        "top_products": get_top_products(analysis_dir),
        "stop_products": get_stop_products(analysis_dir),
        "close_reason": get_core_close_reason(analysis_dir, tag),
    }
    context.update(nav_stats)
    return context


def chart_observation(chart_file: str, ctx: Dict[str, str]) -> str:
    if chart_file == "01_nav_drawdown.png":
        return (
            f"本次累计收益 {ctx.get('total_return')}，年化 {ctx.get('cagr')}，"
            f"最大回撤 {ctx.get('max_drawdown')}，最差单日 {ctx.get('worst_day')}。"
            "收益存在但斜率偏低，回撤相对目标偏深。"
        )
    if chart_file == "02_margin_positions.png":
        return (
            f"平均保证金使用率 {ctx.get('avg_margin')}，峰值 {ctx.get('max_margin')}；"
            f"平均活跃品种 {ctx.get('s1_active_sell_products_mean', 'N/A')}，"
            f"平均活跃合约 {ctx.get('s1_active_sell_contracts_mean', 'N/A')}。"
            "这说明 B0 并不是明显没用仓位，而是单位仓位产出不足。"
        )
    if chart_file in ("03_greeks_timeseries.png", "04_pnl_attribution.png"):
        return (
            f"Theta 累计 {ctx.get('cum_theta')}，Vega 累计 {ctx.get('cum_vega')}，"
            f"Gamma 累计 {ctx.get('cum_gamma')}，Delta 累计 {ctx.get('cum_delta')}，"
            f"Residual 累计 {ctx.get('cum_residual')}。"
            "卖方 carry 很明确，但 vega/gamma 消耗太大。"
        )
    if chart_file == "05_daily_pnl_tail.png":
        return f"最差单日收益 {ctx.get('worst_day')}，最差月份为 {ctx.get('worst_month')}。需要检查这些日期是否对应升波、跳空和止损簇集。"
    if chart_file == "06_premium_pc_structure.png":
        return (
            f"P/C 比均值 {ctx.get('s1_put_call_lot_ratio_mean', 'N/A')}，峰值 {ctx.get('s1_put_call_lot_ratio_max', 'N/A')}；"
            f"P/C>2 的天数 {ctx.get('pc_gt_2_days', 'N/A')}，P/C>5 的天数 {ctx.get('pc_gt_5_days', 'N/A')}。"
            "这提示 B0 虽然定义为双侧卖权，但实际暴露会阶段性单侧化。"
        )
    if chart_file == "08_calendar_returns.png":
        return f"月度维度最差月份为 {ctx.get('worst_month')}。这类月份应作为后续规则优化的压力样本，而不是被平均收益掩盖。"
    if chart_file == "09_product_share_top10.png":
        return f"开仓手数最高的品种为 {ctx.get('top_products')}。如果这些品种集中在同一板块，需要用板块和相关性预算约束。"
    if chart_file in ("10_order_action_summary.png", "11_close_event_timeline.png"):
        return f"平仓路径摘要：{ctx.get('close_reason')}。止损亏损集中品种包括 {ctx.get('stop_products')}。"
    return "该图用于补充观察策略结构与收益路径，具体结论应结合主文指标和交易流水共同判断。"


def add_chart_section(doc: Document, tag: str, analysis_dir: Path, repo_root: Path) -> None:
    context = build_context(tag, analysis_dir, repo_root)
    doc.add_page_break()
    doc.add_heading("图表解读与飞书展示版附录", level=1)
    add_paragraph(
        doc,
        "本节把分析包中的标准图表嵌入文档，并在每张图后给出读图方式、"
        "本次观察和策略含义。导入飞书后，这一节可以直接作为管理层汇报材料使用。",
    )

    for idx, spec in enumerate(CHART_SPECS, start=1):
        image_path = analysis_dir / spec["file"]
        if not image_path.exists():
            continue
        doc.add_heading(f"图 {idx}：{spec['title']}", level=2)
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run()
        run.add_picture(str(image_path), width=Inches(6.3))
        caption = doc.add_paragraph(f"图 {idx}：{spec['title']}（来源：{image_path.name}）")
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_paragraph(doc, f"怎么看：{spec['read']}")
        add_paragraph(doc, f"本次观察：{chart_observation(spec['file'], context)}")
        add_paragraph(doc, f"策略含义：{spec['meaning']}")


def add_metadata(doc: Document, title: str, tag: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(f"版本：{tag}")
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(10)

    section = doc.sections[0]
    header = section.header.paragraphs[0]
    header.text = title
    footer = section.footer.paragraphs[0]
    footer.text = "S1 backtest attribution report"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER


def set_table_borders(doc: Document) -> None:
    for table in doc.tables:
        tbl = table._tbl
        tbl_pr = tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = OxmlElement(f"w:{border_name}")
            border.set(qn("w:val"), "single")
            border.set(qn("w:sz"), "4")
            border.set(qn("w:space"), "0")
            border.set(qn("w:color"), "D9E2F3")
            borders.append(border)
        tbl_pr.append(borders)


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    analysis_dir = (args.analysis_dir or (repo_root / "output" / f"analysis_{args.tag}")).resolve()
    output_docx = args.output_docx or (analysis_dir / f"{args.tag}_feishu_report.docx")
    title = args.title or "S1 回测归因与策略复盘"

    if not args.markdown.exists():
        raise SystemExit(f"Markdown report not found: {args.markdown}")
    if not analysis_dir.exists():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    doc = Document()
    set_default_style(doc)
    add_markdown_to_doc(doc, args.markdown)
    add_metadata(doc, title, args.tag)
    if not args.skip_existing_chart_section:
        add_chart_section(doc, args.tag, analysis_dir, repo_root)
    set_table_borders(doc)
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_docx)
    print(str(output_docx))


if __name__ == "__main__":
    main()
