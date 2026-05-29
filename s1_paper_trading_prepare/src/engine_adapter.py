"""Adapter around the locked ToolkitMinuteEngine for paper-trading orders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .config_snapshot import apply_primary_strategy_only, important_rules, load_effective_config
from .diagnostics import diagnostics_frame, write_csv, write_json
from .paths import DEFAULT_OUTPUT_DIR, DEFAULT_PAPER_CONFIG, ensure_server_deploy_importable, resolve_path
from .schemas import ORDER_FRONT_COLUMNS, PaperTradingRunRequest


@dataclass(frozen=True)
class PaperTradingRunResult:
    signal_date: str
    execute_date: str
    orders_path: Path | None
    diagnostics_path: Path | None
    audit_path: Path | None
    orders: pd.DataFrame
    diagnostics: pd.DataFrame
    meta: dict[str, Any]


def _frontload_columns(frame: pd.DataFrame, front_columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=front_columns)
    ordered = [col for col in front_columns if col in frame.columns]
    rest = [col for col in frame.columns if col not in ordered]
    return frame.loc[:, ordered + rest]


def pending_items_to_orders(pending_items: list[dict[str, Any]], signal_date: str, execute_date: str) -> pd.DataFrame:
    """Convert engine pending-open items into reviewable paper orders."""
    items = [item for item in pending_items if str(item.get("strat", "")).upper() == "S1"]
    if not items:
        return pd.DataFrame(columns=ORDER_FRONT_COLUMNS)

    frame = pd.DataFrame(items)
    frame["signal_date"] = str(signal_date)[:10]
    frame["execute_date"] = str(execute_date)[:10]
    frame["order_status"] = "pending_manual_review"
    frame["action"] = frame.get("role", "").astype(str).str.lower().map(
        {"sell": "open_sell", "buy": "open_buy"}
    ).fillna("open")
    frame["strategy"] = frame.get("strat", "S1")
    if "opt_type" in frame.columns:
        frame["option_type"] = frame["opt_type"]
    if "n" in frame.columns:
        frame["quantity"] = frame["n"]
    if "ref_price" in frame.columns:
        frame["signal_ref_price"] = frame["ref_price"]
    return _frontload_columns(frame, ORDER_FRONT_COLUMNS)


def _next_trading_date(engine: Any, signal_date: str, lookahead_days: int = 21) -> str:
    """Resolve T+1 outside the replay window used to generate T signals."""
    signal = str(signal_date)[:10]
    end = (pd.Timestamp(signal) + pd.Timedelta(days=lookahead_days)).strftime("%Y-%m-%d")

    dates: list[str] = []
    try:
        from day_loader import ToolkitDayLoader

        fresh_loader = ToolkitDayLoader(engine.ci)
        dates = [str(date)[:10] for date in fresh_loader.get_trading_dates(signal, end)]
    except Exception:
        dates = []

    if not dates:
        try:
            dates = [str(date)[:10] for date in engine.loader.get_trading_dates(signal, end)]
        except Exception:
            dates = []

    for date in sorted(set(dates)):
        if date > signal:
            return date

    try:
        shifted = str(engine._shift_trading_date(signal, 1))[:10]
        if shifted > signal:
            return shifted
    except Exception:
        pass
    return signal


class PaperTradingReplayEngineMixin:
    """Suppress historical output writes while preserving in-memory results."""

    def _output_results(self, nav_df, orders_df, stats, tag, elapsed):  # noqa: D401
        self._paper_nav_df = nav_df
        self._paper_orders_df = orders_df
        self._paper_stats = stats
        self._paper_tag = tag
        self._paper_elapsed = elapsed


class PaperTradingOrderGenerator:
    """Generate T+1 paper orders using the exact locked S1 backtest path."""

    def __init__(self, config_path: str | Path | None = None, output_dir: str | Path | None = None):
        ensure_server_deploy_importable()
        from toolkit_minute_engine import ToolkitMinuteEngine

        class PaperTradingReplayEngine(PaperTradingReplayEngineMixin, ToolkitMinuteEngine):
            pass

        self.engine_cls = PaperTradingReplayEngine
        self.config_path = resolve_path(config_path, default=DEFAULT_PAPER_CONFIG)
        self.output_dir = resolve_path(output_dir, default=DEFAULT_OUTPUT_DIR)

    def generate(self, request: PaperTradingRunRequest) -> PaperTradingRunResult:
        signal_date = str(request.signal_date)[:10]
        replay_start = str(request.replay_start_date or signal_date)[:10]
        config_path = resolve_path(request.config_path, default=self.config_path)
        output_dir = resolve_path(request.output_dir, default=self.output_dir)
        tag = request.tag or f"s1_paper_{signal_date.replace('-', '')}"

        snapshot = load_effective_config(config_path)
        engine = self.engine_cls(config_path=str(config_path))
        engine.config = apply_primary_strategy_only(engine.config)
        products = list(request.products) if request.products else None
        run_result = engine.run(
            start_date=replay_start,
            end_date=signal_date,
            products=products,
            tag=f"{tag}_compat_replay",
        )
        execute_date = _next_trading_date(engine, signal_date)
        orders = pending_items_to_orders(list(getattr(engine, "_pending_opens", []) or []), signal_date, execute_date)
        diagnostics = diagnostics_frame(list(getattr(engine, "diagnostics_records", []) or []), signal_date)

        orders_path = output_dir / "orders" / f"orders_{tag}.csv"
        diagnostics_path = output_dir / "diagnostics" / f"diagnostics_{tag}.csv"
        audit_path = output_dir / "audit" / f"audit_{tag}.json"
        meta = {
            "tag": tag,
            "signal_date": signal_date,
            "execute_date": execute_date,
            "replay_start_date": replay_start,
            "products": products,
            "config_path": str(config_path),
            "config_sha256": snapshot.sha256,
            "generated_order_count": int(len(orders)),
            "diagnostic_row_count": int(len(diagnostics)),
            "engine_stats": run_result.get("stats", {}) if isinstance(run_result, dict) else {},
            "important_rules": important_rules(snapshot.config),
            "notes": [
                "Orders are generated by the locked ToolkitMinuteEngine code path.",
                "The ending engine pending-open queue is interpreted as T+1 paper-trading planned orders.",
            ],
        }

        if request.write_outputs:
            write_csv(orders_path, orders)
            write_csv(diagnostics_path, diagnostics)
            write_json(audit_path, meta)
        else:
            orders_path = diagnostics_path = audit_path = None

        return PaperTradingRunResult(
            signal_date=signal_date,
            execute_date=execute_date,
            orders_path=orders_path,
            diagnostics_path=diagnostics_path,
            audit_path=audit_path,
            orders=orders,
            diagnostics=diagnostics,
            meta=meta,
        )
