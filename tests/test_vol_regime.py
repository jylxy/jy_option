import os
import sys
import unittest
from collections import defaultdict
from types import SimpleNamespace

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import vol_regime as vr  # noqa: E402


def norm(product):
    return str(product).upper()


def shift_calendar(date_str, offset):
    return (pd.Timestamp(date_str) + pd.Timedelta(days=int(offset))).strftime("%Y-%m-%d")


class VolRegimeTest(unittest.TestCase):
    def base_config(self):
        return {
            "cooldown_days_after_stop": 1,
            "cooldown_repeat_lookback_days": 20,
            "cooldown_repeat_threshold": 2,
            "cooldown_repeat_extra_days": 2,
            "s1_reentry_require_falling_regime": True,
            "s1_reentry_require_daily_iv_drop": False,
            "s3_reentry_require_falling_regime": False,
            "s3_reentry_require_daily_iv_drop": True,
            "vol_regime_min_iv_rv_spread": 0.02,
            "vol_regime_min_iv_rv_ratio": 1.10,
            "vol_regime_falling_iv_pct_min": 25,
            "vol_regime_falling_iv_pct_max": 95,
            "vol_regime_falling_iv_trend": -0.01,
            "vol_regime_falling_rv_trend_max": 0.01,
            "vol_regime_high_iv_pct": 75,
            "vol_regime_low_iv_pct": 45,
            "vol_regime_high_iv_trend": 0.03,
            "vol_regime_high_rv_trend": 0.05,
            "vol_regime_max_low_rv_trend": 0.02,
            "vol_regime_max_low_iv_trend": 0.00,
            "vol_regime_portfolio_high_ratio": 0.25,
            "vol_regime_portfolio_falling_ratio": 0.25,
            "vol_regime_portfolio_low_ratio": 0.50,
        }

    def test_register_reentry_plan_extends_cooldown_after_repeated_stops(self):
        cfg = self.base_config()
        stop_history = defaultdict(list)
        reentry_plans = {}
        pos = SimpleNamespace(
            strat="S1",
            product="cu",
            opt_type="P",
            cur_delta=-0.12,
            cur_spot=100.0,
            strike=90.0,
        )

        first = vr.register_reentry_plan(
            pos,
            "2025-05-06",
            config=cfg,
            stop_history=stop_history,
            reentry_plans=reentry_plans,
            shift_trading_date=shift_calendar,
            normalize_product_key=norm,
        )
        second = vr.register_reentry_plan(
            pos,
            "2025-05-07",
            config=cfg,
            stop_history=stop_history,
            reentry_plans=reentry_plans,
            shift_trading_date=shift_calendar,
            normalize_product_key=norm,
        )

        self.assertEqual(first["earliest_date"], "2025-05-07")
        self.assertEqual(second["cooldown_days"], 3)
        self.assertEqual(second["earliest_date"], "2025-05-10")
        self.assertEqual(sorted(reentry_plans), [("S1", "cu")])

    def test_s1_reentry_requires_falling_regime_after_cooldown(self):
        cfg = self.base_config()
        plan = {"earliest_date": "2025-05-08"}
        iv_history = {"CU": {"dates": ["2025-05-06", "2025-05-07"], "ivs": [0.30, 0.28]}}
        normal_state = {
            "iv_pct": 55,
            "iv_rv_spread": 0.03,
            "iv_rv_ratio": 1.2,
            "iv_trend": 0.00,
            "rv_trend": 0.00,
        }
        falling_state = dict(normal_state, iv_trend=-0.02)

        self.assertTrue(vr.reentry_plan_blocks(
            cfg,
            plan=plan,
            strat="S1",
            product="CU",
            date_str="2025-05-07",
            iv_history=iv_history,
            current_iv_state={"CU": falling_state},
        ))
        self.assertTrue(vr.reentry_plan_blocks(
            cfg,
            plan=plan,
            strat="S1",
            product="CU",
            date_str="2025-05-08",
            iv_history=iv_history,
            current_iv_state={"CU": normal_state},
        ))
        self.assertFalse(vr.reentry_plan_blocks(
            cfg,
            plan=plan,
            strat="S1",
            product="CU",
            date_str="2025-05-08",
            iv_history=iv_history,
            current_iv_state={"CU": falling_state},
        ))

    def test_refresh_vol_regime_marks_blocked_product_as_post_stop(self):
        cfg = self.base_config()
        states = {
            "CU": {
                "iv_pct": 55,
                "iv_rv_spread": 0.03,
                "iv_rv_ratio": 1.2,
                "iv_trend": -0.02,
                "rv_trend": 0.0,
            },
            "AU": {
                "iv_pct": 35,
                "iv_rv_spread": 0.03,
                "iv_rv_ratio": 1.2,
                "iv_trend": -0.001,
                "rv_trend": 0.0,
            },
        }
        plans = {("S1", "CU"): {"earliest_date": "2025-05-10"}}

        regimes, counts, portfolio = vr.refresh_vol_regime_state(
            cfg,
            current_iv_state=states,
            reentry_plans=plans,
            iv_history={"CU": {"dates": ["2025-05-06", "2025-05-07"], "ivs": [0.30, 0.28]}},
            normalize_product_key=norm,
            date_str="2025-05-08",
        )

        self.assertEqual(regimes["CU"], vr.POST_STOP_COOLDOWN)
        self.assertEqual(regimes["AU"], vr.LOW_STABLE_VOL)
        self.assertEqual(counts[vr.POST_STOP_COOLDOWN], 1)
        self.assertEqual(portfolio, vr.LOW_STABLE_VOL)

    def test_structural_low_iv_product_can_raise_low_vol_multiplier(self):
        cfg = self.base_config()
        cfg.update({
            "vol_regime_sizing_enabled": True,
            "low_iv_structural_auto_enabled": True,
            "low_iv_structural_min_history": 20,
            "low_iv_structural_max_median_iv": 0.24,
            "low_iv_structural_max_iv_std": 0.08,
            "low_iv_structural_margin_per_mult": 1.25,
            "vol_regime_low_margin_per_mult": 1.12,
            "iv_window": 252,
        })
        iv_history = {"CU": {"dates": [f"2025-01-{i:02d}" for i in range(1, 22)], "ivs": [0.18] * 21}}
        current_state = {
            "CU": {
                "iv_pct": 20,
                "iv_rv_spread": 0.03,
                "iv_rv_ratio": 1.2,
            }
        }

        mult = vr.product_margin_per_multiplier(
            cfg,
            product="CU",
            current_vol_regimes={"CU": vr.LOW_STABLE_VOL},
            iv_history=iv_history,
            current_iv_state=current_state,
            normalize_product_key=norm,
        )

        self.assertEqual(mult, 1.25)

    def test_recent_stop_count_uses_configured_cluster_window(self):
        cfg = {"portfolio_stop_cluster_lookback_days": 5}
        stop_history = {
            "CU": ["2025-05-01", "2025-05-04"],
            "AU": ["2025-04-20", "2025-05-06"],
        }

        self.assertEqual(vr.recent_stop_count(cfg, stop_history, "2025-05-07"), 2)

    def test_s1_entry_can_isolate_positive_carry_only(self):
        cfg = self.base_config()
        cfg.update({
            "s1_falling_framework_enabled": True,
            "vol_regime_min_iv_rv_spread": 0.0,
            "vol_regime_min_iv_rv_ratio": 1.0,
            "s1_entry_check_vol_trend": False,
            "s1_entry_block_high_rising_regime": False,
        })
        state = {
            "iv_rv_spread": 0.01,
            "iv_rv_ratio": 1.02,
            "iv_trend": 0.20,
            "rv_trend": 0.20,
        }

        self.assertTrue(vr.passes_s1_falling_framework_entry(
            cfg,
            product="CU",
            iv_state=state,
            current_vol_regimes={"CU": vr.HIGH_RISING_VOL},
            iv_history={},
        ))
        self.assertFalse(vr.passes_s1_falling_framework_entry(
            cfg,
            product="CU",
            iv_state={**state, "iv_rv_spread": -0.001},
            current_vol_regimes={"CU": vr.HIGH_RISING_VOL},
            iv_history={},
        ))

    def test_s1_regime_prioritization_can_be_disabled(self):
        cfg = self.base_config()
        cfg.update({
            "s1_falling_framework_enabled": True,
            "s1_prioritize_products_by_regime": False,
        })

        products = ["CU", "AU"]
        ordered = vr.prioritize_products_by_regime(
            cfg,
            products,
            {"CU": vr.HIGH_RISING_VOL, "AU": vr.FALLING_VOL_CARRY},
        )

        self.assertEqual(ordered, products)


if __name__ == "__main__":
    unittest.main()
