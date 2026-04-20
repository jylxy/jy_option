"""
盘中监控模块 — Greeks 汇总、阈值检查、止盈、应急保护、买卖价差

盘中处理频率分两层：
  - 每分钟：更新持仓价格 + 应急保护检查（OTM% 阈值，时效性最高）
  - 每 N 分钟（intraday_greeks_interval，默认15）：止盈检查 + IV/Greeks 更新 + Greeks 风控

这样在性能和精度之间取得平衡：深虚值期权的 Greeks 盘中变化极小，
15 分钟足够捕捉有意义的变化；但应急保护需要最快响应。
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)


class IntradayMonitor:
    """盘中 Greeks 监控与风控"""

    def __init__(self, config):
        """
        Args:
            config: dict，包含：
              greeks_delta_hard: float  — cash_delta 硬限（默认 0.20 = 20% NAV）
              greeks_vega_hard: float   — cash_vega 硬限（默认 0.02 = 2% NAV）
              greeks_vega_warn: float   — cash_vega 预警（默认 0.015 = 1.5% NAV）
              s3_protect_trigger_otm_pct: float — S3 应急保护触发阈值（默认 5.0）
              intraday_greeks_interval: int — Greeks/止盈 更新间隔（分钟，默认15）
              fee: float — 每手手续费（元）
        """
        self.delta_hard = config.get("greeks_delta_hard", 0.05)
        self.delta_target = config.get("greeks_delta_target", 0.03)
        self.vega_hard = config.get("greeks_vega_hard", 0.01)
        self.vega_target = config.get("greeks_vega_target", 0.007)
        self.vega_warn = config.get("greeks_vega_warn", 0.008)
        self.protect_trigger_otm = config.get("s3_protect_trigger_otm_pct", 5.0)
        self.interval = config.get("intraday_greeks_interval", 15)
        self.fee_per_hand = config.get("fee", 3)

    def should_update_greeks(self, minute_index):
        """
        判断当前分钟是否需要更新 IV/Greeks 和检查止盈。

        按 intraday_greeks_interval 间隔执行。
        minute_index=0 时始终执行（开盘第一分钟）。
        """
        if minute_index == 0:
            return True
        return (minute_index % self.interval) == 0

    def should_check_emergency(self):
        """应急保护每分钟都检查，始终返回 True"""
        return True

    def check_stop_profit(self, position, iv_pct=None):
        """
        检查单个卖腿持仓是否触发止盈。

        仅比较价格，不需要 Greeks。
        按 intraday_greeks_interval 间隔执行（与 Greeks 更新同步）。
        iv_pct: 当前品种的 IV 分位数（因子6：动态止盈阈值）

        Returns:
            bool: 是否触发止盈
        """
        if position.role != "sell" or position.open_price <= 0:
            return False

        from strategy_rules import should_take_profit_s1, should_take_profit_s3

        # 计算扣除手续费后的净利润率
        gross_pnl = (position.open_price - position.cur_price) * position.mult
        fee_cost = self.fee_per_hand * 2  # 开仓+平仓
        net_pnl = gross_pnl - fee_cost
        revenue = position.open_price * position.mult
        profit_pct = net_pnl / revenue if revenue > 0 else 0

        dte = position.dte if hasattr(position, "dte") else 999

        if position.strat == "S1":
            return should_take_profit_s1(profit_pct, dte, iv_pct=iv_pct)
        elif position.strat == "S3":
            return should_take_profit_s3(profit_pct, dte, iv_pct=iv_pct)
        return False

    def aggregate_greeks(self, positions, nav):
        """
        汇总组合级 Greeks。

        Returns:
            dict: {cash_delta, cash_gamma, cash_vega, cash_theta,
                   abs_cash_delta_pct, abs_cash_vega_pct}
        """
        cd = sum(p.cash_delta() for p in positions)
        cg = sum(p.cash_gamma() for p in positions)
        cv = sum(p.cash_vega() for p in positions)
        ct = sum(p.cash_theta() for p in positions)

        safe_nav = max(nav, 1.0)
        return {
            "cash_delta": cd,
            "cash_gamma": cg,
            "cash_vega": cv,
            "cash_theta": ct,
            "abs_cash_delta_pct": abs(cd) / safe_nav,
            "abs_cash_vega_pct": abs(cv) / safe_nav,
        }

    def check_greeks_breach(self, greeks_snapshot, nav):
        """
        检查 Greeks 是否超限。

        Returns:
            list[dict]: 超限事件列表，每个 {type, value, threshold, pct}
        """
        breaches = []
        safe_nav = max(nav, 1.0)

        delta_pct = abs(greeks_snapshot["cash_delta"]) / safe_nav
        if delta_pct > self.delta_hard:
            breaches.append({
                "type": "delta",
                "value": greeks_snapshot["cash_delta"],
                "threshold": self.delta_hard,
                "pct": delta_pct,
            })

        vega_pct = abs(greeks_snapshot["cash_vega"]) / safe_nav
        if vega_pct > self.vega_hard:
            breaches.append({
                "type": "vega",
                "value": greeks_snapshot["cash_vega"],
                "threshold": self.vega_hard,
                "pct": vega_pct,
            })

        return breaches

    def is_vega_paused(self, greeks_snapshot, nav):
        """检查 Vega 是否达到预警线（暂停新开仓）"""
        safe_nav = max(nav, 1.0)
        vega_pct = abs(greeks_snapshot.get("cash_vega", 0)) / safe_nav
        return vega_pct > self.vega_warn

    def check_emergency_protect(self, position, spot_price):
        """
        检查 S3 卖腿是否需要应急保护。

        当卖腿 OTM% 降至 protect_trigger_otm 以下时返回 True。
        """
        if position.strat != "S3" or position.role != "sell":
            return False
        if spot_price <= 0:
            return False

        from strategy_rules import check_emergency_protect
        return check_emergency_protect(
            position.strike, spot_price, position.opt_type,
            trigger_otm_pct=self.protect_trigger_otm
        )

    def select_positions_to_reduce(self, positions, breach_type, nav):
        """
        选择需要减仓的持仓（按 delta/vega 贡献排序，平整组）。

        超过硬限时触发，按卖腿的 |delta|/|vega| 降序选组，
        把同 group_id 的所有腿（卖腿+买腿+保护腿）一起平掉，
        直到降至 target 以下。

        Returns:
            list: 需要平仓的 position 列表（包含整组）
        """
        if breach_type == "delta":
            key_fn = lambda p: abs(p.cash_delta())
            target = self.delta_target
            current_fn = lambda pos_list: abs(sum(p.cash_delta() for p in pos_list))
        elif breach_type == "vega":
            key_fn = lambda p: abs(p.cash_vega())
            target = self.vega_target
            current_fn = lambda pos_list: abs(sum(p.cash_vega() for p in pos_list))
        else:
            return []

        safe_nav = max(nav, 1.0)

        # 按卖腿的贡献排序，但平仓时带上整组
        sell_positions = [p for p in positions if p.role == "sell"]
        sell_positions.sort(key=key_fn, reverse=True)

        to_close = []
        closed_groups = set()
        remaining = list(positions)

        for pos in sell_positions:
            if current_fn(remaining) / safe_nav <= target:
                break

            gid = pos.group_id
            if gid and gid in closed_groups:
                continue  # 这个组已经被选中了
            closed_groups.add(gid)

            # 找出同组所有腿
            if gid:
                group_members = [p for p in remaining if p.group_id == gid]
            else:
                group_members = [pos]

            to_close.extend(group_members)
            remaining = [p for p in remaining if p not in group_members]

        return to_close


    def get_delta_preferred_order(self, positions, nav):
        """
        根据当前净 Cash Delta 方向，返回 S1 开仓的 Put/Call 优先顺序。

        净 Delta > 0（偏多）→ 优先卖 Call（增加负 delta）→ ["C", "P"]
        净 Delta < 0（偏空）→ 优先卖 Put（增加正 delta）→ ["P", "C"]
        净 Delta ≈ 0         → 默认 ["P", "C"]

        这是预防性 Delta 平衡：通过调整开仓方向把 Delta 控制在 ±5% 以内，
        而不是等超限后被动平仓。
        """
        cd = sum(p.cash_delta() for p in positions)
        safe_nav = max(nav, 1.0)
        delta_pct = cd / safe_nav

        if delta_pct > 0.01:   # 偏多 1% 以上 → 优先卖 Call
            return ["C", "P"]
        elif delta_pct < -0.01:  # 偏空 1% 以上 → 优先卖 Put
            return ["P", "C"]
        else:
            return ["P", "C"]  # 中性时默认卖 Put

    def should_skip_direction(self, positions, nav, opt_type):
        """
        检查是否应该跳过某个方向的开仓。

        如果当前 Delta 已经接近硬限（>= target），且新开仓会加剧偏离，则跳过。
        卖 Put → 增加正 delta（偏多方向）
        卖 Call → 增加负 delta（偏空方向）
        """
        cd = sum(p.cash_delta() for p in positions)
        safe_nav = max(nav, 1.0)
        delta_pct = cd / safe_nav

        if opt_type == "P" and delta_pct >= self.delta_target:
            # 已经偏多到 target，不再卖 Put（会继续增加正 delta）
            return True
        if opt_type == "C" and delta_pct <= -self.delta_target:
            # 已经偏空到 target，不再卖 Call（会继续增加负 delta）
            return True
        return False


    # ── 买卖价差计算 ──────────────────────────────────────────────────────────

    def calc_spread(self, close_price, contract_code, contract_master,
                    spread_mode="tick"):
        """
        计算买卖价差。

        tick 模式：spread = max(2 × min_price_tick, close × 0.002)
        pct 模式：spread = close × spread_pct（默认 0.002）
        none 模式：spread = 0
        """
        if spread_mode == "none" or close_price <= 0:
            return 0.0

        if spread_mode == "tick" and contract_master is not None:
            info = contract_master.lookup(contract_code)
            if info and info.get("min_price_tick", 0) > 0:
                tick = info["min_price_tick"]
                return max(2 * tick, close_price * 0.002)

        # pct 模式或 tick 模式 fallback
        return close_price * 0.002

    @staticmethod
    def apply_spread(price, direction, spread):
        """
        施加买卖价差。

        买入：price + spread/2
        卖出：price - spread/2
        """
        half = spread / 2.0
        if direction in ("buy", "protect"):
            return price + half
        elif direction == "sell":
            return price - half
        return price
