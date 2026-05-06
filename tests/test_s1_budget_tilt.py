import os
import sys
import unittest

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_budget_tilt import compute_b2_product_budget_map  # noqa: E402


def rank_high(series):
    return pd.to_numeric(series, errors="coerce").rank(pct=True).fillna(0.5)


def rank_low(series):
    return 1.0 - pd.to_numeric(series, errors="coerce").rank(pct=True).fillna(0.5)


class S1BudgetTiltTest(unittest.TestCase):
    def test_computes_equal_budget_when_tilt_strength_zero(self):
        side_df = pd.DataFrame([
            {
                "product": "CU",
                "option_type": "P",
                "candidate_count": 2,
                "variance_carry": 0.10,
                "breakeven_cushion_iv": 2.0,
                "breakeven_cushion_rv": 2.0,
                "premium_to_iv5_loss": 3.0,
                "premium_to_iv10_loss": 3.0,
                "premium_to_stress_loss": 1.5,
                "theta_vega_efficiency": 0.3,
                "gamma_rent_penalty": 0.2,
                "friction_ratio": 0.1,
            },
            {
                "product": "AL",
                "option_type": "P",
                "candidate_count": 2,
                "variance_carry": 0.01,
                "breakeven_cushion_iv": 1.0,
                "breakeven_cushion_rv": 1.0,
                "premium_to_iv5_loss": 1.0,
                "premium_to_iv10_loss": 1.0,
                "premium_to_stress_loss": 0.5,
                "theta_vega_efficiency": 0.1,
                "gamma_rent_penalty": 0.5,
                "friction_ratio": 0.3,
            },
        ])

        out_df, budget_map, meta_map, diagnostics = compute_b2_product_budget_map(
            side_df=side_df,
            candidate_products=["CU", "AL"],
            total_budget_pct=0.50,
            config={"s1_b2_tilt_strength": 0.0},
            date_str="2025-05-06",
            nav=10000.0,
            rank_high=rank_high,
            rank_low=rank_low,
        )

        self.assertIn("b2_side_score", out_df.columns)
        self.assertAlmostEqual(budget_map["CU"], 0.25)
        self.assertAlmostEqual(budget_map["AL"], 0.25)
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(meta_map["CU"]["candidate_count"], 2)

    def test_tilts_budget_toward_higher_quality_product(self):
        side_df = pd.DataFrame([
            {
                "product": "CU",
                "option_type": "P",
                "candidate_count": 2,
                "variance_carry": 0.10,
                "breakeven_cushion_iv": 2.0,
                "breakeven_cushion_rv": 2.0,
                "premium_to_iv5_loss": 3.0,
                "premium_to_iv10_loss": 3.0,
                "premium_to_stress_loss": 1.5,
                "theta_vega_efficiency": 0.3,
                "gamma_rent_penalty": 0.2,
                "friction_ratio": 0.1,
            },
            {
                "product": "AL",
                "option_type": "P",
                "candidate_count": 2,
                "variance_carry": 0.01,
                "breakeven_cushion_iv": 1.0,
                "breakeven_cushion_rv": 1.0,
                "premium_to_iv5_loss": 1.0,
                "premium_to_iv10_loss": 1.0,
                "premium_to_stress_loss": 0.5,
                "theta_vega_efficiency": 0.1,
                "gamma_rent_penalty": 0.5,
                "friction_ratio": 0.3,
            },
        ])

        _out_df, budget_map, _meta_map, _diagnostics = compute_b2_product_budget_map(
            side_df=side_df,
            candidate_products=["CU", "AL"],
            total_budget_pct=0.50,
            config={"s1_b2_tilt_strength": 1.0, "s1_b2_floor_weight": 0.0},
            date_str="2025-05-06",
            nav=10000.0,
            rank_high=rank_high,
            rank_low=rank_low,
        )

        self.assertGreater(budget_map["CU"], budget_map["AL"])
        self.assertAlmostEqual(sum(budget_map.values()), 0.50)


if __name__ == "__main__":
    unittest.main()
