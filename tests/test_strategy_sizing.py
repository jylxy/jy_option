import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from strategy_sizing import (  # noqa: E402
    calc_s1_size,
    calc_s1_stress_size,
    calc_s3_size_v2,
    calc_s4_size,
)


class StrategySizingTest(unittest.TestCase):
    def test_s1_margin_size_has_minimum_one_lot(self):
        self.assertEqual(calc_s1_size(1_000_000, 0.02, 0.0, 1.0), 1)
        self.assertEqual(calc_s1_size(1_000_000, 0.02, 10_000, 1.0), 1)

    def test_s1_stress_size_caps_quantity(self):
        self.assertEqual(
            calc_s1_stress_size(
                nav=1_000_000,
                stress_budget_pct=0.01,
                one_contract_stress_loss=1000,
                max_qty=5,
            ),
            5,
        )
        self.assertEqual(calc_s1_stress_size(1_000_000, 0.01, float("nan")), 0)

    def test_s3_size_v2_chooses_first_ratio_passing_credit_tolerance(self):
        selected = calc_s3_size_v2(
            nav=1_000_000,
            margin_per=0.02,
            sell_margin=10_000,
            buy_premium=3.0,
            sell_premium=2.0,
            mult=10,
            iv_scale=1.0,
            ratio_candidates=(2, 3),
            net_premium_tolerance=0.0,
        )

        self.assertEqual(selected, (1, 2, 2))

    def test_s4_size_respects_max_hands(self):
        self.assertEqual(calc_s4_size(10_000_000, 0.01, 1, 1000, max_hands=5), 5)


if __name__ == "__main__":
    unittest.main()
