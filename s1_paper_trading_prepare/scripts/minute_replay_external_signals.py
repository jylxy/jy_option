"""Replay S1 external daily intents through the Toolkit minute engine.

The script is deliberately narrow: signal generation remains in the S1 daily
pipeline, while this adapter delegates fills, marking, margin, expiry, and the
overlay portfolio loss stop to the existing Toolkit minute engine.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import logging
import math
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SERVER_SRC = ROOT / "server_deploy" / "src"
DEFAULT_CONFIG = ROOT / "s1_paper_trading_prepare" / "configs" / "s1_paper_mainline.json"
DEFAULT_SIGNALS = (
    ROOT
    / "s1_paper_trading_prepare"
    / "data"
    / "external_signals"
    / "broad_sector_margin45_new075_plus_iv95_pullback_overlay002_open_signals.csv"
)

EXCHANGE_TO_SUFFIX = {
    "DCE": "DCE",
    "SHFE": "SHF",
    "CZCE": "CZC",
    "INE": "INE",
    "GFEX": "GFE",
    "CFFEX": "CFE",
    "SSE": "SH",
    "SZSE": "SZ",
}
TOOLKIT_SUFFIXES = set(EXCHANGE_TO_SUFFIX.values())

EXTERNAL_META_FIELDS = (
    "external_signal_id",
    "external_signal_date",
    "external_intended_entry_date",
    "external_execution_mode",
    "external_source_contract_code",
    "external_entry_reason",
    "external_side_rule",
    "external_budget_group",
    "external_target_premium_pct",
    "external_target_premium_cash",
    "external_signal_entry_price",
    "external_daily_margin_cash",
    "external_one_lot_margin_cash",
    "external_selected_side_iv_pressure",
    "external_other_side_iv_pressure",
    "external_side_iv_pressure_diff",
    "external_overlay_signal_rule",
    "external_overlay_iv_percentile_lag4",
    "external_overlay_atm_iv_lag1",
    "external_pending_carry_days",
    "external_pending_last_defer_reason",
    "external_rerouted",
    "external_reroute_from_code",
    "external_reroute_from_strike",
    "external_reroute_rank",
    "external_reroute_reason",
    "external_reroute_original_qty",
    "external_reroute_qty_cap",
    "external_reroute_contract_cap",
    "external_reroute_contracts_used",
    "external_reroute_qty_used",
)

EXTERNAL_TEXT_FIELDS = {
    "external_signal_id",
    "external_signal_date",
    "external_intended_entry_date",
    "external_execution_mode",
    "external_source_contract_code",
    "external_entry_reason",
    "external_side_rule",
    "external_budget_group",
    "external_overlay_signal_rule",
    "external_pending_last_defer_reason",
    "external_reroute_from_code",
    "external_reroute_reason",
}


def resolve_repo_path(value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def safe_int(value: Any, default: int = 0) -> int:
    val = safe_float(value, np.nan)
    if not np.isfinite(val):
        return default
    return int(val)


def normalize_signal_date(value: Any) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def to_toolkit_code(contract_code: Any, exchange: Any = "") -> str:
    raw = str(contract_code or "").strip()
    if not raw:
        return ""
    if "." in raw:
        suffix = raw.rsplit(".", 1)[-1].upper()
        if suffix in TOOLKIT_SUFFIXES:
            return raw.upper()
        prefix, short_name = raw.split(".", 1)
        suffix = EXCHANGE_TO_SUFFIX.get(prefix.upper())
        if suffix:
            return f"{short_name.upper()}.{suffix}"
    suffix = EXCHANGE_TO_SUFFIX.get(str(exchange or "").upper())
    if suffix:
        return f"{raw.upper()}.{suffix}"
    return raw.upper()


def load_signal_schedule(path: Path, signal_date_column: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"signal schedule not found: {path}")
    df = pd.read_csv(path)
    required = {
        signal_date_column,
        "product",
        "exchange",
        "contract_code",
        "option_type",
        "target_expiry",
        "strike",
        "spot_close",
        "qty",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"signal schedule missing columns: {missing}")
    out = df.copy()
    out["external_signal_date"] = out[signal_date_column].map(normalize_signal_date)
    out["toolkit_code"] = [
        to_toolkit_code(code, exchange)
        for code, exchange in zip(out["contract_code"], out["exchange"])
    ]
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").fillna(0).astype(int)
    out = out[(out["external_signal_date"] != "") & (out["toolkit_code"] != "") & (out["qty"] > 0)].copy()
    out = out.sort_values(["external_signal_date", "product", "option_type", "toolkit_code"], kind="mergesort")
    out.insert(0, "external_signal_id", [f"extsig_{i:06d}" for i in range(1, len(out) + 1)])
    return out.reset_index(drop=True)


def import_toolkit_components():
    sys.path.insert(0, str(SERVER_SRC))
    install_s1_only_import_guards()
    import open_execution  # type: ignore
    from margin_model import estimate_margin  # type: ignore
    from toolkit_minute_engine import ToolkitMinuteEngine  # type: ignore

    open_execution.OPEN_ITEM_AUDIT_FIELDS = tuple(
        dict.fromkeys(tuple(open_execution.OPEN_ITEM_AUDIT_FIELDS) + EXTERNAL_META_FIELDS)
    )
    open_execution.OPEN_ITEM_TEXT_FIELDS = frozenset(
        set(open_execution.OPEN_ITEM_TEXT_FIELDS) | EXTERNAL_TEXT_FIELDS
    )
    return ToolkitMinuteEngine, estimate_margin


def install_s1_only_import_guards() -> None:
    for module_name, function_name in (
        ("ed1_event_strategy", "queue_ed1_opens"),
        ("ed2_event_long_strategy", "queue_ed2_opens"),
    ):
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)

        def _noop(*args, **kwargs):  # noqa: ANN001, ANN202
            return 0

        setattr(module, function_name, _noop)
        sys.modules[module_name] = module


def make_external_engine_class(base_cls, estimate_margin_func):
    from execution_model import apply_execution_slippage  # type: ignore
    from open_execution import (  # type: ignore
        build_open_execution_context,
        build_open_order_record,
        estimate_open_fill_price,
        scale_deferred_open_item,
        split_open_quantity,
    )

    class ExternalIntentMinuteReplayEngine(base_cls):
        def __init__(self, config_path: Path, signals: pd.DataFrame, same_day_execution: bool = False):
            super().__init__(config_path=str(config_path))
            self.config["enable_s1"] = True
            for family in ("3", "4"):
                self.config[f"enable_s{family}"] = False
                self.config[f"s{family}_margin_cap"] = 0.0
            self.external_signals = signals.copy()
            self.same_day_execution = bool(same_day_execution)
            self._signals_by_queue_date: dict[str, list[pd.Series]] = defaultdict(list)
            self._unqueued_signals: list[dict[str, Any]] = []
            self._unresolved_signal_codes: list[dict[str, Any]] = []
            self._queued_signal_count = 0

        def _set_position_mark_meta(
            self,
            pos,
            *,
            date_str: str,
            option_price: float | None = None,
            spot_price: float | None = None,
            option_source: str = "",
            spot_source: str = "",
        ) -> None:
            meta = getattr(pos, "entry_meta", {}) or {}
            if option_price is not None and np.isfinite(option_price) and option_price >= 0:
                meta["external_last_option_mark_date"] = date_str
                meta["external_last_option_mark_price"] = float(option_price)
                meta["external_last_option_mark_source"] = option_source or "unknown"
            if spot_price is not None and np.isfinite(spot_price) and spot_price > 0:
                meta["external_last_spot_mark_date"] = date_str
                meta["external_last_spot_mark_price"] = float(spot_price)
                meta["external_last_spot_mark_source"] = spot_source or "unknown"
            pos.entry_meta = meta

        def _fresh_option_mark_ok(self, pos, date_str: str) -> bool:
            meta = getattr(pos, "entry_meta", {}) or {}
            if str(meta.get("external_last_option_mark_date", ""))[:10] != str(date_str)[:10]:
                return False
            source = str(meta.get("external_last_option_mark_source", "") or "")
            if source == "open_execution" and not bool(self.config.get("external_exit_accept_open_execution_mark", False)):
                return False
            return True

        def _require_fresh_option_marks(self, positions, date_str: str, reason: str) -> bool:
            if not bool(self.config.get("external_exit_require_fresh_mark", True)):
                return True
            stale = []
            for pos in positions:
                if not self._fresh_option_mark_ok(pos, date_str):
                    meta = getattr(pos, "entry_meta", {}) or {}
                    stale.append({
                        "product": getattr(pos, "product", ""),
                        "code": getattr(pos, "code", ""),
                        "last_mark_date": str(meta.get("external_last_option_mark_date", "")),
                        "last_mark_source": str(meta.get("external_last_option_mark_source", "")),
                    })
            if not stale:
                return True
            self.diagnostics_records.append({
                "date": date_str,
                "scope": "external_intent_minute_replay",
                "name": "exit_blocked_stale_option_mark",
                "reason": reason,
                "blocked_positions": len(stale),
                "sample": stale[:20],
            })
            return False

        def products_from_signals(self) -> list[str]:
            return sorted({str(x).upper() for x in self.external_signals["product"].dropna().unique()})

        def prepare_signal_schedule(self, trading_dates: list[str]) -> None:
            date_set = set(trading_dates)
            prev_map = {
                date: trading_dates[i - 1]
                for i, date in enumerate(trading_dates)
                if i > 0
            }
            self._signals_by_queue_date.clear()
            self._unqueued_signals.clear()
            for row in self.external_signals.itertuples(index=False):
                signal_date = str(getattr(row, "external_signal_date"))
                if self.same_day_execution:
                    queue_date = prev_map.get(signal_date, "")
                    mode = "same_day_diagnostic_replay"
                else:
                    queue_date = signal_date
                    mode = "T_plus_1"
                row_dict = row._asdict()
                row_dict["external_execution_mode"] = mode
                if not queue_date or queue_date not in date_set:
                    row_dict["unqueued_reason"] = "queue_date_not_in_backtest_window"
                    self._unqueued_signals.append(row_dict)
                    continue
                self._signals_by_queue_date[queue_date].append(pd.Series(row_dict))
            self._queued_signal_count = sum(len(v) for v in self._signals_by_queue_date.values())

        def _entry_meta_from_item(self, item):
            meta = super()._entry_meta_from_item(item)
            for key in EXTERNAL_META_FIELDS:
                meta[key] = item.get(key, np.nan)
            return meta

        def _prefilter_intraday_exit_codes_by_daily_high(self, date_str, exit_codes):
            if self.config.get("external_replay_disable_intraday_exit_scans", True):
                return set()
            return super()._prefilter_intraday_exit_codes_by_daily_high(date_str, exit_codes)

        def _pending_open_execution_risk_ok(self, item, code, actual_n, price, nav, date_str):
            if item.get("role") != "sell":
                return True
            new_margin = estimate_margin_func(
                item.get("spot", 0.0),
                item.get("strike", 0.0),
                item.get("opt_type", ""),
                price,
                item.get("mult", 0.0),
                item.get("mr", None),
                0.5,
                exchange=item.get("exchange"),
                product=item.get("product"),
            ) * int(actual_n or 0)
            total_margin = self._get_open_sell_margin_total(include_pending=False)
            margin_cap = float(self.config.get("margin_cap", 0.7) or 0.7)
            if total_margin + new_margin > float(nav) * margin_cap + 1e-9:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "skip_open_total_margin_cap",
                    "product": item.get("product", ""),
                    "option_type": item.get("opt_type", ""),
                    "code": code,
                    "new_margin": new_margin,
                    "total_margin_before": total_margin,
                    "margin_cap": margin_cap,
                    "external_signal_id": item.get("external_signal_id", ""),
                })
                return False
            return True

        def _external_pending_cancel_dte_le(self) -> float:
            return float(self.config.get("external_pending_cancel_dte_le", 0) or 0)

        def _external_pending_max_carry_days(self) -> int:
            return int(self.config.get("external_pending_max_carry_trading_days", 5) or 5)

        def _external_execution_dte(self, item: dict[str, Any], date_str: str) -> float:
            try:
                dte = self.ci.calc_dte(str(item.get("code") or ""), datetime.strptime(date_str, "%Y-%m-%d").date())
            except Exception:  # noqa: BLE001 - diagnostics should never break replay.
                dte = np.nan
            if np.isfinite(safe_float(dte, np.nan)):
                return float(dte)
            expiry = pd.to_datetime(item.get("expiry"), errors="coerce")
            current = pd.to_datetime(date_str, errors="coerce")
            if pd.notna(expiry) and pd.notna(current):
                return float((expiry.date() - current.date()).days)
            return np.nan

        def _append_external_deferred(
            self,
            deferred: list[dict[str, Any]],
            item: dict[str, Any],
            date_str: str,
            reason: str,
            quantity: int | None = None,
            **extra: Any,
        ) -> None:
            if not bool(self.config.get("external_open_keep_pending_without_price", True)):
                return
            dte = self._external_execution_dte(item, date_str)
            cancel_dte = self._external_pending_cancel_dte_le()
            if np.isfinite(dte) and dte <= cancel_dte:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "cancel_pending_expired_before_open",
                    "product": item.get("product", ""),
                    "option_type": item.get("opt_type", ""),
                    "code": item.get("code", ""),
                    "external_signal_id": item.get("external_signal_id", ""),
                    "execution_dte": dte,
                    "reason": reason,
                })
                return

            carry_days = int(item.get("external_pending_carry_days", 0) or 0) + 1
            max_carry_days = self._external_pending_max_carry_days()
            if max_carry_days >= 0 and carry_days > max_carry_days:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "cancel_pending_max_carry_days",
                    "product": item.get("product", ""),
                    "option_type": item.get("opt_type", ""),
                    "code": item.get("code", ""),
                    "external_signal_id": item.get("external_signal_id", ""),
                    "carry_days": carry_days,
                    "max_carry_days": max_carry_days,
                    "reason": reason,
                })
                return

            deferred_item = dict(item)
            if quantity is not None:
                deferred_item = scale_deferred_open_item(deferred_item, int(quantity))
            deferred_item["external_pending_carry_days"] = carry_days
            deferred_item["external_pending_last_defer_reason"] = reason
            deferred.append(deferred_item)
            self.diagnostics_records.append({
                "date": date_str,
                "scope": "external_intent_minute_replay",
                "name": "defer_pending_open",
                "product": item.get("product", ""),
                "option_type": item.get("opt_type", ""),
                "code": item.get("code", ""),
                "external_signal_id": item.get("external_signal_id", ""),
                "remaining_n": deferred_item.get("n", 0),
                "carry_days": carry_days,
                "reason": reason,
                **extra,
            })

        def _update_positions_from_daily(self, daily_df, date_str):
            option_marks: dict[str, float] = {}
            spot_by_underlying: dict[str, float] = {}
            spot_by_product: dict[str, float] = {}
            if daily_df is not None and not daily_df.empty:
                if {"option_code", "option_close"}.issubset(daily_df.columns):
                    valid_prices = daily_df.loc[
                        pd.to_numeric(daily_df["option_close"], errors="coerce").gt(0),
                        ["option_code", "option_close"],
                    ].dropna(subset=["option_code"])
                    if not valid_prices.empty:
                        valid_prices = valid_prices.drop_duplicates("option_code", keep="last")
                        option_marks = {
                            str(row.option_code): float(row.option_close)
                            for row in valid_prices.itertuples(index=False)
                        }
                if "spot_close" in daily_df.columns:
                    valid_spots = daily_df.loc[
                        pd.to_numeric(daily_df["spot_close"], errors="coerce").gt(0)
                    ].copy()
                    if not valid_spots.empty and "underlying_code" in valid_spots.columns:
                        by_underlying = valid_spots[
                            valid_spots["underlying_code"].notna()
                            & valid_spots["underlying_code"].astype(str).ne("")
                        ][["underlying_code", "spot_close"]].drop_duplicates("underlying_code", keep="last")
                        spot_by_underlying = {
                            str(row.underlying_code): float(row.spot_close)
                            for row in by_underlying.itertuples(index=False)
                        }
                    if not valid_spots.empty and "product" in valid_spots.columns:
                        by_product = valid_spots[
                            valid_spots["product"].notna()
                            & valid_spots["product"].astype(str).ne("")
                        ][["product", "spot_close"]].drop_duplicates("product", keep="last")
                        spot_by_product = {
                            str(row.product).upper(): float(row.spot_close)
                            for row in by_product.itertuples(index=False)
                        }

            super()._update_positions_from_daily(daily_df, date_str)

            for pos in self.positions:
                option_price = option_marks.get(getattr(pos, "code", ""))
                underlying_code = str(getattr(pos, "underlying_code", "") or "")
                product = str(getattr(pos, "product", "") or "").upper()
                spot_price = np.nan
                if underlying_code and underlying_code in spot_by_underlying:
                    spot_price = spot_by_underlying[underlying_code]
                elif product in spot_by_product:
                    spot_price = spot_by_product[product]

                if option_price is not None:
                    self._set_position_mark_meta(
                        pos,
                        date_str=date_str,
                        option_price=option_price,
                        option_source="daily_option_close",
                    )
                elif bool(self.config.get("external_mark_missing_diagnostics_enabled", True)):
                    meta = getattr(pos, "entry_meta", {}) or {}
                    self.diagnostics_records.append({
                        "date": date_str,
                        "scope": "external_intent_minute_replay",
                        "name": "position_option_mark_missing",
                        "product": getattr(pos, "product", ""),
                        "code": getattr(pos, "code", ""),
                        "last_mark_date": meta.get("external_last_option_mark_date", ""),
                        "last_mark_source": meta.get("external_last_option_mark_source", ""),
                    })
                if np.isfinite(spot_price) and spot_price > 0:
                    self._set_position_mark_meta(
                        pos,
                        date_str=date_str,
                        spot_price=spot_price,
                        spot_source="daily_underlying_close",
                    )

        def _farther_contract_candidates(self, item: dict[str, Any]) -> list[dict[str, Any]]:
            product = str(item.get("product", "") or "").upper()
            opt_type = str(item.get("opt_type", "") or "").upper()[:1]
            expiry = normalize_signal_date(item.get("expiry", ""))
            strike = safe_float(item.get("strike", np.nan), np.nan)
            mult = safe_float(item.get("mult", np.nan), np.nan)
            if not product or opt_type not in {"P", "C"} or not expiry or not np.isfinite(strike):
                return []
            candidates = []
            for code in self.ci.get_product_codes(product) or []:
                if code == item.get("code"):
                    continue
                info = self.ci.lookup(code) or {}
                if str(info.get("option_type", "")).upper()[:1] != opt_type:
                    continue
                if normalize_signal_date(info.get("expiry_date", "")) != expiry:
                    continue
                cand_strike = safe_float(info.get("strike", np.nan), np.nan)
                cand_mult = safe_float(info.get("multiplier", np.nan), np.nan)
                if not np.isfinite(cand_strike):
                    continue
                if np.isfinite(mult) and np.isfinite(cand_mult) and abs(cand_mult - mult) > 1e-9:
                    continue
                if opt_type == "P" and cand_strike >= strike:
                    continue
                if opt_type == "C" and cand_strike <= strike:
                    continue
                candidates.append({
                    "code": code,
                    "strike": cand_strike,
                    "info": info,
                    "distance": abs(cand_strike - strike),
                })
            reverse = opt_type == "P"
            return sorted(candidates, key=lambda x: x["strike"], reverse=reverse)

        def _execute_external_open_item(
            self,
            item: dict[str, Any],
            code: str,
            actual_n: int,
            price: float,
            raw_execution_price: float,
            execution_slippage: float,
            date_str: str,
            nav: float,
        ) -> bool:
            if actual_n <= 0:
                return False
            if not self._pending_open_execution_risk_ok(item, code, actual_n, price, nav, date_str):
                return False
            (
                pos,
                open_fee_per_contract,
                close_fee_per_contract,
                roundtrip_fee_per_contract,
                slippage_cash,
            ) = self._build_position_from_pending_open(
                item,
                code,
                actual_n,
                price,
                raw_execution_price,
                execution_slippage,
                date_str,
            )
            self._set_position_mark_meta(
                pos,
                date_str=date_str,
                option_price=price,
                spot_price=safe_float(item.get("spot", np.nan), np.nan),
                option_source="open_execution",
                spot_source="signal_snapshot",
            )
            self.positions.append(pos)
            open_fee = open_fee_per_contract * actual_n
            self._day_realized["fee"] += open_fee
            gross_premium_cash = float(price) * float(item["mult"]) * float(actual_n)
            net_premium_cash = (
                gross_premium_cash - open_fee
                if item["role"] == "sell"
                else -gross_premium_cash - open_fee
            )
            open_margin = pos.cur_margin() if item["role"] == "sell" else 0.0
            self.orders.append(build_open_order_record(
                date_str=date_str,
                item=item,
                code=code,
                actual_n=actual_n,
                price=price,
                raw_execution_price=raw_execution_price,
                execution_slippage=execution_slippage,
                slippage_cash=slippage_cash,
                open_fee=open_fee,
                open_fee_per_contract=open_fee_per_contract,
                close_fee_per_contract=close_fee_per_contract,
                roundtrip_fee_per_contract=roundtrip_fee_per_contract,
                pos=pos,
                open_margin=open_margin,
                gross_premium_cash=gross_premium_cash,
                net_premium_cash=net_premium_cash,
            ))
            return True

        def _try_reroute_farther_contracts(
            self,
            item: dict[str, Any],
            remaining_qty: int,
            date_str: str,
            nav: float,
            reason: str,
        ) -> tuple[int, int]:
            if remaining_qty <= 0 or not bool(self.config.get("external_open_farther_contract_reroute_enabled", False)):
                return 0, 0

            contract_cap = int(self.config.get("external_open_farther_contract_max_contracts", 2) or 0)
            scan_cap = int(self.config.get("external_open_farther_contract_max_scan_contracts", 10) or 10)
            qty_pct_cap = float(self.config.get("external_open_farther_contract_max_qty_pct", 0.35) or 0.0)
            qty_abs_cap = int(self.config.get("external_open_farther_contract_max_qty", 0) or 0)
            min_volume = float(self.config.get("external_open_farther_contract_min_volume", 1) or 0)
            if contract_cap <= 0 or scan_cap <= 0 or qty_pct_cap <= 0:
                return 0, 0

            original_qty = int(item.get("external_reroute_original_qty", item.get("n", 0)) or 0)
            if original_qty <= 0:
                original_qty = int(item.get("n", 0) or 0)
            contracts_used = int(item.get("external_reroute_contracts_used", 0) or 0)
            qty_used = int(item.get("external_reroute_qty_used", 0) or 0)
            remaining_contract_slots = max(contract_cap - contracts_used, 0)
            if remaining_contract_slots <= 0:
                return 0, 0
            qty_cap = int(original_qty * qty_pct_cap)
            if qty_abs_cap > 0:
                qty_cap = min(qty_cap, qty_abs_cap)
            qty_cap = max(qty_cap - qty_used, 0)
            if qty_cap <= 0:
                return 0, 0

            candidates = self._farther_contract_candidates(item)[:scan_cap]
            if not candidates:
                return 0, 0

            alt_codes = [c["code"] for c in candidates]
            try:
                alt_minute = self.loader.load_day_minute(date_str, code_list=alt_codes)
            except Exception as exc:  # noqa: BLE001 - keep replay moving, but record audit.
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "reroute_farther_contract_minute_load_failed",
                    "product": item.get("product", ""),
                    "option_type": item.get("opt_type", ""),
                    "code": item.get("code", ""),
                    "external_signal_id": item.get("external_signal_id", ""),
                    "error": str(exc),
                })
                return 0, 0
            alt_context = build_open_execution_context(alt_minute)
            open_price_mode = str(self.config.get("open_execution_price_mode", "vwap") or "vwap").lower()
            open_price_col = str(self.config.get("open_execution_price_col", "close") or "close")
            vol_limit_pct = self.config.get("volume_limit_pct", 0.10)
            open_volume_limit_enabled = bool(self.config.get("open_execution_volume_limit_enabled", True))

            total_rerouted = 0
            contracts_opened = 0
            for rank, cand in enumerate(candidates, start=1):
                if contracts_opened >= remaining_contract_slots or total_rerouted >= min(remaining_qty, qty_cap):
                    break
                code = cand["code"]
                bars = alt_context.bars_by_code.get(code)
                price = estimate_open_fill_price(bars, mode=open_price_mode, price_col=open_price_col)
                if price <= 0:
                    continue
                today_vol = alt_context.day_volume.get(code, 0)
                if safe_float(today_vol, 0.0) < min_volume:
                    continue
                raw_execution_price = price
                open_action = "sell_open" if item["role"] == "sell" else "buy_open"
                price, execution_slippage = apply_execution_slippage(raw_execution_price, open_action, self.config)
                if price <= 0 or not self._pending_open_execution_price_ok(item, price):
                    continue
                qty_left = min(remaining_qty - total_rerouted, qty_cap - total_rerouted)
                actual_n, _ = split_open_quantity(
                    qty_left,
                    today_vol,
                    vol_limit_pct,
                    min_today_volume=min_volume,
                    volume_limit_enabled=open_volume_limit_enabled,
                )
                if actual_n <= 0:
                    continue
                info = cand["info"]
                reroute_item = dict(item)
                reroute_item.update({
                    "code": code,
                    "strike": cand["strike"],
                    "mult": safe_float(info.get("multiplier", item.get("mult", np.nan)), item.get("mult", np.nan)),
                    "expiry": normalize_signal_date(info.get("expiry_date", item.get("expiry", ""))),
                    "exchange": info.get("exchange", item.get("exchange", "")),
                    "underlying_code": info.get("underlying_code", item.get("underlying_code", "")),
                    "external_rerouted": True,
                    "external_reroute_from_code": item.get("code", ""),
                    "external_reroute_from_strike": item.get("strike", np.nan),
                    "external_reroute_rank": rank,
                    "external_reroute_reason": reason,
                    "external_reroute_original_qty": original_qty,
                    "external_reroute_qty_cap": qty_cap,
                    "external_reroute_contract_cap": contract_cap,
                    "external_reroute_contracts_used": contracts_used + contracts_opened + 1,
                    "external_reroute_qty_used": qty_used + total_rerouted + actual_n,
                })
                if self._execute_external_open_item(
                    reroute_item,
                    code,
                    actual_n,
                    price,
                    raw_execution_price,
                    execution_slippage,
                    date_str,
                    nav,
                ):
                    total_rerouted += actual_n
                    contracts_opened += 1
                    self.diagnostics_records.append({
                        "date": date_str,
                        "scope": "external_intent_minute_replay",
                        "name": "reroute_farther_contract_opened",
                        "product": item.get("product", ""),
                        "option_type": item.get("opt_type", ""),
                        "code": code,
                        "from_code": item.get("code", ""),
                        "external_signal_id": item.get("external_signal_id", ""),
                        "quantity": actual_n,
                        "rank": rank,
                        "reason": reason,
                    })
            return total_rerouted, contracts_opened

        def _execute_pending_opens(self, minute_df, date_str):
            if not self._pending_opens:
                return
            nav = self.capital + (self.nav_records[-1]["cum_pnl"] if self.nav_records else 0)
            vol_limit_pct = self.config.get("volume_limit_pct", 0.10)
            min_open_volume = float(self.config.get("s1_min_volume", 0) or 0)
            open_volume_limit_enabled = bool(self.config.get("open_execution_volume_limit_enabled", True))
            open_price_mode = str(self.config.get("open_execution_price_mode", "vwap") or "vwap").lower()
            open_price_col = str(self.config.get("open_execution_price_col", "close") or "close")
            executed = 0
            deferred: list[dict[str, Any]] = []
            open_context = build_open_execution_context(minute_df)

            for item in self._pending_opens:
                code = item["code"]
                if not self._s1_pending_open_execution_allowed(item, date_str):
                    self.diagnostics_records.append({
                        "date": date_str,
                        "scope": "s1_deferred_intent",
                        "name": "cancel_stale_before_execution",
                        "product": item.get("product", ""),
                        "option_type": item.get("opt_type", ""),
                        "code": code,
                        "signal_date": item.get("signal_date", ""),
                        "last_validation_date": item.get("s1_deferred_intent_last_validation_date", ""),
                        "remaining_n": item.get("n", 0),
                    })
                    self._bump_s1_funnel("pending_intents_cancel_stale_before_execution")
                    continue
                if not self._pending_open_entry_dte_ok(item, date_str):
                    continue

                code_bars = open_context.bars_by_code.get(code)
                price = estimate_open_fill_price(code_bars, mode=open_price_mode, price_col=open_price_col)
                if price <= 0:
                    rerouted, opened_contracts = self._try_reroute_farther_contracts(
                        item,
                        int(item.get("n", 0) or 0),
                        date_str,
                        nav,
                        "original_no_valid_minute_price",
                    )
                    if rerouted > 0:
                        executed += opened_contracts
                    remaining = int(item.get("n", 0) or 0) - rerouted
                    if remaining > 0:
                        next_item = dict(item)
                        next_item["external_reroute_contracts_used"] = int(item.get("external_reroute_contracts_used", 0) or 0) + opened_contracts
                        next_item["external_reroute_qty_used"] = int(item.get("external_reroute_qty_used", 0) or 0) + rerouted
                        self._append_external_deferred(
                            deferred,
                            next_item,
                            date_str,
                            "no_valid_minute_price",
                            quantity=remaining,
                            rerouted_qty=rerouted,
                        )
                    continue

                raw_execution_price = price
                open_action = "sell_open" if item["role"] == "sell" else "buy_open"
                price, execution_slippage = apply_execution_slippage(
                    raw_execution_price,
                    open_action,
                    self.config,
                )
                if price <= 0:
                    self._append_external_deferred(deferred, item, date_str, "non_positive_slipped_price")
                    continue
                if not self._pending_open_execution_price_ok(item, price):
                    self._append_external_deferred(deferred, item, date_str, "execution_price_floor_failed")
                    continue

                target_n = int(item["n"])
                today_vol = open_context.day_volume.get(code, 0)
                actual_n, remaining_n = split_open_quantity(
                    target_n,
                    today_vol,
                    vol_limit_pct,
                    min_today_volume=min_open_volume,
                    volume_limit_enabled=open_volume_limit_enabled,
                )

                if actual_n > 0 and self._execute_external_open_item(
                    item,
                    code,
                    actual_n,
                    price,
                    raw_execution_price,
                    execution_slippage,
                    date_str,
                    nav,
                ):
                    executed += 1

                if remaining_n > 0:
                    rerouted, opened_contracts = self._try_reroute_farther_contracts(
                        item,
                        remaining_n,
                        date_str,
                        nav,
                        "original_volume_cap_remaining",
                    )
                    if rerouted > 0:
                        executed += opened_contracts
                    still_remaining = remaining_n - rerouted
                    if still_remaining > 0:
                        next_item = dict(item)
                        next_item["external_reroute_contracts_used"] = int(item.get("external_reroute_contracts_used", 0) or 0) + opened_contracts
                        next_item["external_reroute_qty_used"] = int(item.get("external_reroute_qty_used", 0) or 0) + rerouted
                        self._append_external_deferred(
                            deferred,
                            next_item,
                            date_str,
                            "volume_cap_remaining",
                            quantity=still_remaining,
                            today_volume=today_vol,
                            executed_qty=actual_n,
                            rerouted_qty=rerouted,
                        )

            self._pending_opens = deferred
            if executed:
                logging.getLogger(__name__).debug("T+1 external execution %d open rows, deferred %d", executed, len(deferred))

        def _process_daily_decision(self, daily_df, date_str, product_pool, run_risk_and_tp=True):
            fee = float(self.config.get("fee", 3) or 0.0)
            self._apply_exit_rules(
                date_str,
                fee,
                product_iv_pcts={},
                check_greeks=False,
                check_tp=False,
                check_expiry=False,
            )
            self._apply_external_expiry(date_str, fee)
            self._apply_overlay_portfolio_loss_stop(date_str, fee)
            for row in self._signals_by_queue_date.get(date_str, []):
                item = self._build_external_pending_item(row, date_str)
                if item is not None:
                    self._pending_opens.append(item)

        def _apply_external_expiry(self, date_str: str, fee: float) -> None:
            expiry_dte = int(self.config.get("expiry_dte", 0) or 0)
            try:
                current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return
            to_close = []
            for pos in list(self.positions):
                dte = self.ci.calc_dte(pos.code, current_date)
                if dte >= 0:
                    pos.dte = dte
                if dte > expiry_dte:
                    continue
                spot, spot_source, spot_lookup_code = self._expiry_spot_close(pos, date_str)
                if not np.isfinite(spot) or spot <= 0:
                    self.diagnostics_records.append({
                        "date": date_str,
                        "scope": "external_intent_minute_replay",
                        "name": "expiry_settlement_blocked_missing_spot",
                        "product": getattr(pos, "product", ""),
                        "code": getattr(pos, "code", ""),
                        "underlying_code": getattr(pos, "underlying_code", ""),
                        "expiry": str(getattr(pos, "expiry", ""))[:10],
                        "spot_source": spot_source,
                        "spot_lookup_code": spot_lookup_code,
                    })
                    continue
                pos.cur_spot = float(spot)
                if pos.opt_type == "C":
                    intrinsic = max(float(pos.cur_spot) - float(pos.strike), 0.0)
                else:
                    intrinsic = max(float(pos.strike) - float(pos.cur_spot), 0.0)
                pos.cur_price = intrinsic
                self._set_position_mark_meta(
                    pos,
                    date_str=date_str,
                    option_price=intrinsic,
                    spot_price=spot,
                    option_source="expiry_intrinsic",
                    spot_source=spot_source or "expiry_underlying_close",
                )
                meta = getattr(pos, "entry_meta", {}) or {}
                meta["external_expiry_spot_source"] = spot_source or "unknown"
                meta["external_expiry_spot_lookup_code"] = spot_lookup_code or ""
                pos.entry_meta = meta
                to_close.append(pos)
            if to_close:
                self._close_positions(to_close, date_str, "expiry", fee)

        def _query_expiry_future_daily_price(self, date_str: str, underlying_code: str) -> tuple[float, str, str]:
            try:
                from query_filters import quote_sql_literal  # type: ignore
                from spot_provider import build_underlying_alias_map  # type: ignore
                from toolkit.selector import select_bars_sql  # type: ignore
            except Exception:
                return np.nan, "future_daily_quote_import_failed", ""

            suffix = str(underlying_code).rsplit(".", 1)[-1].upper() if "." in str(underlying_code) else ""
            if suffix in {"SH", "SZ"}:
                return np.nan, "future_daily_quote_skipped_etf_underlying", ""

            alias_map = build_underlying_alias_map([underlying_code])
            lookup_codes = list(alias_map.get(underlying_code, []))
            if not lookup_codes:
                return np.nan, "future_daily_quote_no_alias", ""

            code_sql = ", ".join(quote_sql_literal(code) for code in lookup_codes)
            date_sql = quote_sql_literal(str(date_str)[:10])
            tables = (
                "future_daily_quote",
                "future_daily_quote_local",
                "future_history_quote",
            )
            for table_name in tables:
                query = f"""
                    SELECT
                        ths_code,
                        toFloat64OrZero(toString(settlement)) AS settlement,
                        toFloat64OrZero(toString(close)) AS close
                    FROM {table_name}
                    WHERE toString(date) = {date_sql}
                      AND ths_code IN ({code_sql})
                """
                try:
                    frame = select_bars_sql(query)
                except Exception as exc:
                    self.diagnostics_records.append({
                        "date": date_str,
                        "scope": "external_intent_minute_replay",
                        "name": "expiry_future_daily_quote_lookup_failed",
                        "underlying_code": underlying_code,
                        "table": table_name,
                        "error": str(exc),
                    })
                    continue
                if frame is None or frame.empty:
                    continue
                frame = frame.copy()
                frame["settlement"] = pd.to_numeric(frame.get("settlement"), errors="coerce")
                frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
                for lookup_code in lookup_codes:
                    rows = frame[frame["ths_code"].astype(str) == lookup_code]
                    if rows.empty:
                        continue
                    row = rows.iloc[-1]
                    settlement = safe_float(row.get("settlement"), np.nan)
                    if np.isfinite(settlement) and settlement > 0:
                        return settlement, f"expiry_future_daily_settlement:{table_name}", lookup_code
                    close = safe_float(row.get("close"), np.nan)
                    if np.isfinite(close) and close > 0:
                        return close, f"expiry_future_daily_close:{table_name}", lookup_code

            return np.nan, "future_daily_quote_missing", ""

        def _expiry_spot_close(self, pos, date_str: str) -> tuple[float, str, str]:
            info = self.ci.lookup(pos.code) or {}
            underlying_code = getattr(pos, "underlying_code", "") or info.get("underlying_code", "")
            if not underlying_code:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "expiry_underlying_code_missing",
                    "product": getattr(pos, "product", ""),
                    "code": getattr(pos, "code", ""),
                })
                return np.nan, "underlying_code_missing", ""
            future_price, future_source, lookup_code = self._query_expiry_future_daily_price(date_str, underlying_code)
            if np.isfinite(future_price) and future_price > 0:
                return future_price, future_source, lookup_code
            try:
                if hasattr(self.loader, "_get_spot_daily_ohlc_maps"):
                    spot_map, _, _ = self.loader._get_spot_daily_ohlc_maps(date_str, [underlying_code])
                else:
                    spot_map = self.loader._get_spot_daily_close_map(date_str, [underlying_code])
            except Exception as exc:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "expiry_spot_lookup_failed",
                    "product": getattr(pos, "product", ""),
                    "code": getattr(pos, "code", ""),
                    "underlying_code": underlying_code,
                    "error": str(exc),
                })
                return np.nan, "underlying_daily_close_lookup_failed", ""
            spot = safe_float(spot_map.get(underlying_code, np.nan), np.nan)
            if not np.isfinite(spot) or spot <= 0:
                self.diagnostics_records.append({
                    "date": date_str,
                    "scope": "external_intent_minute_replay",
                    "name": "expiry_spot_missing",
                    "product": getattr(pos, "product", ""),
                    "code": getattr(pos, "code", ""),
                    "underlying_code": underlying_code,
                    "previous_spot": safe_float(getattr(pos, "cur_spot", np.nan), np.nan),
                    "future_daily_source": future_source,
                })
                return np.nan, "underlying_daily_close_missing", ""
            return spot, "expiry_underlying_daily_close", underlying_code

        def _apply_overlay_portfolio_loss_stop(self, date_str: str, fee: float) -> None:
            stop_pct = float(
                self.config.get("iv_pullback_sidecar", {}).get(
                    "portfolio_loss_stop_pct_nav",
                    self.config.get("overlay_portfolio_loss_stop_pct_nav", 0.002),
                )
                or 0.0
            )
            if stop_pct <= 0 or not self.positions:
                return
            overlay_positions = [
                pos for pos in self.positions
                if getattr(pos, "role", "") == "sell"
                and (getattr(pos, "entry_meta", {}) or {}).get("external_entry_reason") == "iv_extreme_overlay"
            ]
            if not overlay_positions:
                return
            if not self._require_fresh_option_marks(
                overlay_positions,
                date_str,
                "external_overlay_portfolio_loss_stop",
            ):
                return
            nav = max(self._current_nav(), 1.0)
            pnl_by_pos = {
                pos: (float(pos.open_price or 0.0) - float(pos.cur_price or 0.0)) * float(pos.mult or 0.0) * int(pos.n or 0)
                for pos in overlay_positions
            }
            overlay_unrealized = sum(pnl_by_pos.values())
            if overlay_unrealized >= -stop_pct * nav:
                return
            losing = [
                pos for pos, pnl in pnl_by_pos.items()
                if pnl < 0 and not (self.config.get("skip_same_day_exit_for_vwap_opens", True) and pos.open_date == date_str)
            ]
            if not losing:
                return
            self.diagnostics_records.append({
                "date": date_str,
                "scope": "external_intent_minute_replay",
                "name": "overlay_portfolio_loss_stop",
                "overlay_unrealized_pnl": overlay_unrealized,
                "nav": nav,
                "threshold_cash": -stop_pct * nav,
                "closed_positions": len(losing),
            })
            self._close_positions(
                losing,
                date_str,
                "external_overlay_portfolio_loss_stop",
                fee,
                exec_time="close",
            )

        def _build_external_pending_item(self, row: pd.Series, queue_date: str) -> dict[str, Any] | None:
            code = str(row.get("toolkit_code", "") or "")
            info = self.ci.lookup(code) or {}
            if not info:
                self._unresolved_signal_codes.append({
                    "external_signal_id": row.get("external_signal_id", ""),
                    "source_contract_code": row.get("contract_code", ""),
                    "toolkit_code": code,
                    "queue_date": queue_date,
                })
            product = str(row.get("product", info.get("product_root", "")) or "").upper()
            opt_type = str(row.get("option_type", info.get("option_type", "")) or "").upper()[:1]
            exchange = str(row.get("exchange", info.get("exchange", "")) or info.get("exchange", "")).upper()
            if exchange in EXCHANGE_TO_SUFFIX:
                exchange = {
                    "SHFE": "SHFE",
                    "CZCE": "CZCE",
                    "GFEX": "GFEX",
                    "CFFEX": "CFFEX",
                }.get(exchange, exchange)
            expiry = normalize_signal_date(row.get("target_expiry", info.get("expiry_date", "")))
            strike = safe_float(row.get("strike", info.get("strike", np.nan)), np.nan)
            mult = safe_float(info.get("multiplier", np.nan), np.nan)
            if not np.isfinite(mult) or mult <= 0:
                mult = safe_float(row.get("multiplier", np.nan), np.nan)
            if not np.isfinite(mult) or mult <= 0:
                self._unresolved_signal_codes.append({
                    "external_signal_id": row.get("external_signal_id", ""),
                    "source_contract_code": row.get("contract_code", ""),
                    "toolkit_code": code,
                    "queue_date": queue_date,
                    "reason": "missing_multiplier",
                })
                return None
            underlying_code = info.get("underlying_code", "")
            margin_ratio = self.ci.get_margin_ratio(
                exchange=exchange,
                product=product,
                underlying_code=underlying_code,
                config=self.config,
            )
            entry_reason = str(row.get("entry_reason", "external_signal") or "external_signal")
            entry_date = str(row.get("external_signal_date", "") or "")
            group_id = f"EXT|{entry_reason}|{product}|{opt_type}|{expiry}|{entry_date}"
            qty = safe_int(row.get("qty", 0), 0)
            item = {
                "code": code,
                "role": "sell",
                "strat": "S1",
                "product": product,
                "opt_type": opt_type,
                "strike": strike,
                "n": qty,
                "mult": mult,
                "expiry": expiry,
                "mr": margin_ratio,
                "spot": safe_float(row.get("spot_close", np.nan), np.nan),
                "exchange": exchange,
                "underlying_code": underlying_code,
                "group_id": group_id,
                "signal_date": queue_date,
                "ref_price": safe_float(row.get("entry_price", np.nan), np.nan),
                "abs_delta": abs(safe_float(row.get("delta", np.nan), np.nan)),
                "delta": safe_float(row.get("delta", np.nan), np.nan),
                "volume": safe_float(row.get("volume", np.nan), np.nan),
                "open_interest": safe_float(row.get("close_oi", np.nan), np.nan),
                "one_contract_margin": safe_float(row.get("one_lot_margin_cash", np.nan), np.nan),
                "one_contract_stress_loss": 0.0,
                "cash_vega": 0.0,
                "cash_gamma": 0.0,
                "external_signal_id": row.get("external_signal_id", ""),
                "external_signal_date": queue_date,
                "external_intended_entry_date": entry_date,
                "external_execution_mode": row.get("external_execution_mode", ""),
                "external_source_contract_code": row.get("contract_code", ""),
                "external_entry_reason": entry_reason,
                "external_side_rule": row.get("side_rule", ""),
                "external_budget_group": row.get("budget_group", ""),
                "external_target_premium_pct": safe_float(row.get("target_premium_pct", np.nan), np.nan),
                "external_target_premium_cash": safe_float(row.get("target_premium_cash", np.nan), np.nan),
                "external_signal_entry_price": safe_float(row.get("entry_price", np.nan), np.nan),
                "external_daily_margin_cash": safe_float(row.get("margin_cash", np.nan), np.nan),
                "external_one_lot_margin_cash": safe_float(row.get("one_lot_margin_cash", np.nan), np.nan),
                "external_selected_side_iv_pressure": safe_float(row.get("selected_side_iv_pressure", np.nan), np.nan),
                "external_other_side_iv_pressure": safe_float(row.get("other_side_iv_pressure", np.nan), np.nan),
                "external_side_iv_pressure_diff": safe_float(row.get("side_iv_pressure_diff", np.nan), np.nan),
                "external_overlay_signal_rule": row.get("overlay_signal_rule", ""),
                "external_overlay_iv_percentile_lag4": safe_float(row.get("overlay_iv_percentile_lag4", np.nan), np.nan),
                "external_overlay_atm_iv_lag1": safe_float(row.get("overlay_atm_iv_lag1", np.nan), np.nan),
            }
            if not code or qty <= 0 or opt_type not in {"P", "C"} or not expiry or not np.isfinite(strike):
                self._unresolved_signal_codes.append({
                    "external_signal_id": row.get("external_signal_id", ""),
                    "source_contract_code": row.get("contract_code", ""),
                    "toolkit_code": code,
                    "queue_date": queue_date,
                    "reason": "invalid_pending_item",
                })
                return None
            return item

        def write_external_replay_audit(self, tag: str) -> None:
            output_dir = ROOT / "server_deploy" / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            orders = pd.DataFrame(self.orders)
            if not orders.empty and "external_signal_id" in orders.columns:
                open_orders = orders[orders["action"].astype(str).str.startswith("open_")].copy()
                executed_ids = set(open_orders["external_signal_id"].dropna().astype(str))
            else:
                open_orders = pd.DataFrame()
                executed_ids = set()
            intended_ids = set(self.external_signals["external_signal_id"].astype(str))
            summary = pd.DataFrame([{
                "tag": tag,
                "intended_signals": len(intended_ids),
                "queued_signals": self._queued_signal_count,
                "executed_open_orders": len(open_orders),
                "executed_unique_signals": len(executed_ids),
                "unexecuted_unique_signals": len(intended_ids - executed_ids),
                "unqueued_signals": len(self._unqueued_signals),
                "unresolved_signal_code_rows": len(self._unresolved_signal_codes),
                "same_day_execution": self.same_day_execution,
            }])
            summary_path = output_dir / f"external_replay_summary_{tag}.csv"
            summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
            if self._unqueued_signals:
                pd.DataFrame(self._unqueued_signals).to_csv(
                    output_dir / f"external_replay_unqueued_{tag}.csv",
                    index=False,
                    encoding="utf-8-sig",
                )
            if self._unresolved_signal_codes:
                pd.DataFrame(self._unresolved_signal_codes).drop_duplicates().to_csv(
                    output_dir / f"external_replay_code_audit_{tag}.csv",
                    index=False,
                    encoding="utf-8-sig",
                )
            unexecuted = self.external_signals[
                ~self.external_signals["external_signal_id"].astype(str).isin(executed_ids)
            ].copy()
            if not unexecuted.empty:
                unexecuted.to_csv(
                    output_dir / f"external_replay_unexecuted_{tag}.csv",
                    index=False,
                    encoding="utf-8-sig",
                )
            print("\n=== External intent replay audit ===")
            print(summary.to_string(index=False))

    return ExternalIntentMinuteReplayEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay S1 external daily intents with Toolkit minute execution.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    parser.add_argument("--signal-date-column", default="entry_date")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", default="2026-03-31")
    parser.add_argument("--products", default="")
    parser.add_argument("--tag", default="s1_reverse_overlay002_extintent_minute_20260531")
    parser.add_argument(
        "--same-day-execution",
        action="store_true",
        help="Research replay only: queue each signal one trading day earlier so fills occur on the signal CSV date.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

    config_path = resolve_repo_path(args.config, DEFAULT_CONFIG)
    signals_path = resolve_repo_path(args.signals, DEFAULT_SIGNALS)
    signals = load_signal_schedule(signals_path, args.signal_date_column)

    base_cls, estimate_margin_func = import_toolkit_components()
    engine_cls = make_external_engine_class(base_cls, estimate_margin_func)
    engine = engine_cls(config_path=config_path, signals=signals, same_day_execution=args.same_day_execution)
    trading_dates = engine.loader.get_trading_dates(args.start_date, args.end_date)
    engine.prepare_signal_schedule(trading_dates)

    if args.products.strip():
        products = [x.strip().upper() for x in args.products.split(",") if x.strip()]
    else:
        products = engine.products_from_signals()
    result = engine.run(start_date=args.start_date, end_date=args.end_date, products=products, tag=args.tag)
    engine.write_external_replay_audit(args.tag)

    stats = result.get("stats", {}) if isinstance(result, dict) else {}
    if stats:
        print("\n=== Minute replay stats ===")
        for key in ["total_return", "annual_return", "max_drawdown", "sharpe", "win_rate"]:
            if key in stats:
                print(f"{key}: {stats[key]:.6f}")


if __name__ == "__main__":
    main()
