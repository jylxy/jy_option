import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_side_selection import (  # noqa: E402
    choose_s1_option_sides,
    choose_s1_trend_confidence_sides,
    classify_s1_trend_confidence,
    s1_side_adjusted_score,
    s1_trend_side_adjustment,
)
from strategy_rules import classify_s1_trend_confidence as exported_classify  # noqa: E402


class S1SideSelectionTest(unittest.TestCase):
    def test_classifies_range_and_trend_states(self):
        range_state = classify_s1_trend_confidence(
            [0.001, -0.001, 0.0005, -0.0005, 0.0] * 4,
            trend_threshold=0.02,
            range_threshold=0.01,
        )
        up_state = classify_s1_trend_confidence(
            [0.004] * 20,
            trend_threshold=0.02,
            range_threshold=0.001,
        )

        self.assertEqual(range_state["trend_state"], "range_bound")
        self.assertEqual(up_state["trend_state"], "uptrend")
        self.assertGreater(up_state["trend_confidence"], 0.0)

    def test_range_pressure_promotes_edge_to_direction(self):
        upper_edge = classify_s1_trend_confidence(
            [-0.004, 0.002, -0.003, 0.001, 0.0005] * 3 + [0.004, 0.004, 0.004],
            trend_threshold=0.03,
            range_threshold=0.02,
            range_pressure_enabled=True,
            range_pressure_upper=0.70,
        )

        self.assertEqual(upper_edge["trend_state"], "uptrend")
        self.assertEqual(upper_edge["trend_range_pressure"], "upper")

    def test_trend_side_adjustment_makes_adverse_side_weaker(self):
        call_adj = s1_trend_side_adjustment("C", "uptrend", 1.0)
        put_adj = s1_trend_side_adjustment("P", "uptrend", 1.0)

        self.assertEqual(call_adj["trend_role"], "weak")
        self.assertLess(call_adj["budget_mult"], 1.0)
        self.assertEqual(put_adj["trend_role"], "strong")

    def test_option_side_selection_and_strangle_gate(self):
        side_candidates = {
            "P": {"quality_score": 0.80},
            "C": {"quality_score": 0.78},
        }

        disabled = choose_s1_option_sides(side_candidates, enabled=False)
        selected = choose_s1_option_sides(
            side_candidates,
            enabled=True,
            conditional_strangle_enabled=True,
            current_regime="falling_vol_carry",
            momentum=0.0,
            strangle_max_abs_momentum=0.01,
            strangle_min_score_ratio=0.90,
            strangle_min_adjusted_score=0.30,
        )

        self.assertEqual(disabled, ["P", "C"])
        self.assertEqual(selected, ["P", "C"])

    def test_trend_confidence_side_selection_prefers_strong_side(self):
        side_candidates = {
            "P": {"quality_score": 0.70},
            "C": {"quality_score": 0.80},
        }

        selected = choose_s1_trend_confidence_sides(
            side_candidates,
            trend_state="uptrend",
            current_regime="normal_vol",
            allow_weak_side=False,
        )

        self.assertEqual(selected, ["P"])

    def test_side_adjusted_score_penalizes_adverse_momentum(self):
        put_score = s1_side_adjusted_score(
            {"quality_score": 1.0},
            "P",
            momentum=-0.05,
            momentum_threshold=0.01,
            momentum_penalty=1.0,
        )
        self.assertLess(put_score, 1.0)

    def test_strategy_rules_reexports_classifier(self):
        state = exported_classify([0.004] * 20, trend_threshold=0.02, range_threshold=0.001)
        self.assertEqual(state["trend_state"], "uptrend")


if __name__ == "__main__":
    unittest.main()
