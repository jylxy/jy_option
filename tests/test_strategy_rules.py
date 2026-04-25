import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from strategy_rules import (  # noqa: E402
    choose_s1_option_sides,
    choose_s1_trend_confidence_sides,
    classify_s1_trend_confidence,
    s1_trend_side_adjustment,
    s1_forward_vega_quality_filter,
    select_s1_sell,
)


class StrategyRulesTest(unittest.TestCase):
    def test_s1_risk_reward_ranking_can_override_target_delta(self):
        rows = pd.DataFrame([
            {
                "option_code": "TARGET",
                "option_type": "P",
                "moneyness": 0.94,
                "delta": -0.070,
                "option_close": 0.60,
                "spot_close": 100.0,
                "strike": 94.0,
                "volume": 100,
                "open_interest": 100,
                "gamma": 0.0010,
                "vega": 0.020,
                "theta": -0.010,
                "exchange": "SHFE",
                "product": "CU",
            },
            {
                "option_code": "RICH",
                "option_type": "P",
                "moneyness": 0.92,
                "delta": -0.040,
                "option_close": 1.20,
                "spot_close": 100.0,
                "strike": 92.0,
                "volume": 100,
                "open_interest": 100,
                "gamma": 0.0005,
                "vega": 0.010,
                "theta": -0.020,
                "exchange": "SHFE",
                "product": "CU",
            },
        ])

        target_delta = select_s1_sell(
            rows,
            "P",
            mult=10,
            mr=0.07,
            target_abs_delta=0.07,
            ranking_mode="target_delta",
            exchange="SHFE",
            product="CU",
        )
        risk_reward = select_s1_sell(
            rows,
            "P",
            mult=10,
            mr=0.07,
            target_abs_delta=0.07,
            ranking_mode="risk_reward",
            exchange="SHFE",
            product="CU",
        )

        self.assertEqual(target_delta["option_code"], "TARGET")
        self.assertEqual(risk_reward["option_code"], "RICH")

    def test_s1_side_selection_prefers_better_adjusted_side(self):
        side_candidates = {
            "P": {"quality_score": 0.60},
            "C": {"quality_score": 0.50},
        }

        selected = choose_s1_option_sides(
            side_candidates,
            enabled=True,
            conditional_strangle_enabled=False,
            momentum=-0.05,
            momentum_threshold=0.02,
            momentum_penalty=1.0,
        )

        self.assertEqual(selected, ["C"])

    def test_s1_conditional_strangle_requires_neutral_momentum_and_close_scores(self):
        side_candidates = {
            "P": {"quality_score": 0.60},
            "C": {"quality_score": 0.57},
        }

        selected = choose_s1_option_sides(
            side_candidates,
            enabled=True,
            conditional_strangle_enabled=True,
            current_regime="falling_vol_carry",
            momentum=0.002,
            strangle_max_abs_momentum=0.015,
            strangle_min_score_ratio=0.90,
            strangle_min_adjusted_score=0.35,
        )

        self.assertEqual(selected, ["P", "C"])

        directional = choose_s1_option_sides(
            side_candidates,
            enabled=True,
            conditional_strangle_enabled=True,
            current_regime="falling_vol_carry",
            momentum=0.04,
            strangle_max_abs_momentum=0.015,
            strangle_min_score_ratio=0.90,
            strangle_min_adjusted_score=0.35,
        )

        self.assertEqual(directional, ["P"])

    def test_s1_trend_confidence_classifies_range_and_trend(self):
        range_state = classify_s1_trend_confidence(
            [0.001, -0.001, 0.0005, -0.0005, 0.0] * 4,
            min_history=5,
            trend_threshold=0.018,
            range_threshold=0.010,
        )
        up_state = classify_s1_trend_confidence(
            [0.004] * 20,
            min_history=5,
            trend_threshold=0.018,
            range_threshold=0.010,
        )

        self.assertEqual(range_state["trend_state"], "range_bound")
        self.assertEqual(up_state["trend_state"], "uptrend")
        self.assertGreater(up_state["trend_confidence"], 0.0)

    def test_s1_trend_range_pressure_reclassifies_range_edges(self):
        upper_edge = classify_s1_trend_confidence(
            [-0.004, 0.002, -0.003, 0.001, 0.0005] * 3 + [0.004, 0.004, 0.004],
            min_history=5,
            trend_threshold=0.018,
            range_threshold=0.020,
            range_pressure_enabled=True,
            range_pressure_lookback=18,
            range_pressure_upper=0.75,
            range_pressure_lower=0.25,
            range_pressure_min_short_ret=0.004,
        )
        lower_edge = classify_s1_trend_confidence(
            [0.004, -0.002, 0.003, -0.001, -0.0005] * 3 + [-0.004, -0.004, -0.004],
            min_history=5,
            trend_threshold=0.018,
            range_threshold=0.020,
            range_pressure_enabled=True,
            range_pressure_lookback=18,
            range_pressure_upper=0.75,
            range_pressure_lower=0.25,
            range_pressure_min_short_ret=0.004,
        )

        self.assertEqual(upper_edge["trend_state"], "uptrend")
        self.assertEqual(upper_edge["trend_range_pressure"], "upper")
        self.assertEqual(lower_edge["trend_state"], "downtrend")
        self.assertEqual(lower_edge["trend_range_pressure"], "lower")

    def test_s1_trend_side_adjustment_makes_uptrend_call_weaker(self):
        call_adj = s1_trend_side_adjustment(
            "C",
            "uptrend",
            1.0,
            weak_delta_cap=0.06,
            weak_score_mult=0.60,
            weak_budget_mult=0.50,
        )
        put_adj = s1_trend_side_adjustment("P", "uptrend", 1.0)

        self.assertEqual(call_adj["trend_role"], "weak")
        self.assertEqual(call_adj["delta_cap"], 0.06)
        self.assertLess(call_adj["score_mult"], 1.0)
        self.assertLess(call_adj["budget_mult"], 1.0)
        self.assertEqual(put_adj["trend_role"], "strong")

    def test_s1_trend_choice_allows_range_strangle_and_trend_weak_side(self):
        side_candidates = {
            "P": {"quality_score": 0.60},
            "C": {"quality_score": 0.57},
        }
        range_choice = choose_s1_trend_confidence_sides(
            side_candidates,
            trend_state="range_bound",
            current_regime="falling_vol_carry",
            conditional_strangle_enabled=True,
            strangle_states=["range_bound"],
            strangle_min_score_ratio=0.90,
            strangle_min_adjusted_score=0.35,
        )
        uptrend_choice = choose_s1_trend_confidence_sides(
            side_candidates,
            trend_state="uptrend",
            current_regime="falling_vol_carry",
            conditional_strangle_enabled=True,
            allow_weak_side=True,
            weak_side_min_score_ratio=0.90,
            strangle_min_adjusted_score=0.35,
        )

        self.assertEqual(range_choice, ["P", "C"])
        self.assertEqual(uptrend_choice, ["P", "C"])

    def test_forward_vega_filter_blocks_wing_iv_steepening(self):
        candidates = pd.DataFrame([
            {
                "option_code": "GOOD",
                "contract_iv": 0.24,
                "contract_iv_change_5d": -0.020,
                "contract_price_change_1d": -0.10,
            },
            {
                "option_code": "BAD",
                "contract_iv": 0.28,
                "contract_iv_change_5d": 0.004,
                "contract_price_change_1d": 0.05,
            },
        ])

        filtered, stats = s1_forward_vega_quality_filter(
            candidates,
            "C",
            iv_state={"atm_iv": 0.20, "iv_trend": -0.010, "rv_trend": -0.002},
            side_meta={},
            config={
                "s1_forward_vega_filter_enabled": True,
                "s1_forward_vega_contract_iv_lookback": 5,
                "s1_forward_vega_contract_iv_max_change": 0.0,
                "s1_forward_vega_max_skew_steepen": 0.005,
            },
        )

        self.assertEqual(filtered["option_code"].tolist(), ["GOOD"])
        self.assertEqual(int(stats["skip_forward_vega_contract_iv"]), 1)

    def test_forward_vega_filter_blocks_structural_low_breakout_pressure(self):
        candidates = pd.DataFrame([
            {
                "option_code": "VCP_CALL",
                "contract_iv": 0.15,
                "contract_iv_change_5d": -0.010,
            }
        ])

        filtered, stats = s1_forward_vega_quality_filter(
            candidates,
            "C",
            iv_state={
                "atm_iv": 0.14,
                "iv_trend": -0.010,
                "rv_trend": 0.002,
                "is_structural_low_iv": True,
                "vol_regime": "low_stable_vol",
            },
            side_meta={
                "trend_state": "uptrend",
                "trend_confidence": 0.70,
                "trend_range_pressure": "upper",
            },
            config={
                "s1_forward_vega_filter_enabled": True,
                "s1_forward_vega_contract_iv_lookback": 5,
                "s1_forward_vega_block_structural_low_breakout": True,
            },
        )

        self.assertTrue(filtered.empty)
        self.assertEqual(int(stats["skip_forward_vega_vcp"]), 1)


if __name__ == "__main__":
    unittest.main()
