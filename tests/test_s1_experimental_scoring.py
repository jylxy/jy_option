import os
import sys
import unittest

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_experimental_scoring import apply_s1_b6_candidate_ranking, s1_b6_enabled  # noqa: E402


def rank_high(series):
    return pd.to_numeric(series, errors="coerce").rank(pct=True).fillna(0.5)


def rank_low(series):
    return 1.0 - pd.to_numeric(series, errors="coerce").rank(pct=True).fillna(0.5)


class S1ExperimentalScoringTest(unittest.TestCase):
    def test_b6_enabled_detects_any_b6_switch(self):
        self.assertFalse(s1_b6_enabled({"s1_ranking_mode": "liquidity_oi"}))
        self.assertTrue(s1_b6_enabled({"s1_ranking_mode": "b6"}))
        self.assertTrue(s1_b6_enabled({"s1_b6_product_tilt_enabled": True}))

    def test_b6_ranking_scores_and_sorts_candidates(self):
        candidates = pd.DataFrame([
            {
                "option_code": "LOW",
                "premium_to_stress_loss": 0.5,
                "premium_to_iv10_loss": 0.4,
                "b5_theta_per_vega": 0.3,
                "b5_theta_per_gamma": 0.2,
                "b5_premium_to_tail_move_loss": 0.4,
                "b3_vomma_loss_ratio": 0.8,
                "premium_yield_margin": 0.1,
                "open_interest": 10,
                "volume": 10,
            },
            {
                "option_code": "HIGH",
                "premium_to_stress_loss": 2.0,
                "premium_to_iv10_loss": 1.5,
                "b5_theta_per_vega": 1.2,
                "b5_theta_per_gamma": 1.1,
                "b5_premium_to_tail_move_loss": 1.4,
                "b3_vomma_loss_ratio": 0.2,
                "premium_yield_margin": 0.4,
                "open_interest": 5,
                "volume": 5,
            },
        ])

        ranked = apply_s1_b6_candidate_ranking(
            candidates,
            config={},
            rank_high=rank_high,
            rank_low=rank_low,
        )

        self.assertEqual(ranked.iloc[0]["option_code"], "HIGH")
        self.assertIn("b6_contract_score", ranked.columns)
        self.assertTrue((ranked["quality_score"] == ranked["b6_contract_score"]).all())

    def test_b6_hard_filter_removes_low_net_premium(self):
        candidates = pd.DataFrame([
            {"option_code": "A", "net_premium_cash": 1.0, "friction_ratio": 0.1},
            {"option_code": "B", "net_premium_cash": 10.0, "friction_ratio": 0.1},
        ])

        filtered = apply_s1_b6_candidate_ranking(
            candidates,
            config={"s1_b6_hard_filter_enabled": True, "s1_b6_min_net_premium_cash": 5.0},
            rank_high=rank_high,
            rank_low=rank_low,
        )

        self.assertEqual(filtered["option_code"].tolist(), ["B"])


if __name__ == "__main__":
    unittest.main()

