import os
import sys
import unittest

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_experimental_scoring import (  # noqa: E402
    add_b3_candidate_fields,
    apply_s1_b6_candidate_ranking,
    b3_product_side_budget_overlay,
    b4_product_side_budget_overlay,
    b6_product_budget_overlay,
    b6_product_side_budget_overlay,
    contract_iv_vov_from_history,
    s1_b6_enabled,
    term_structure_features,
)


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

    def test_b3_candidate_fields_and_term_structure(self):
        prod_df = pd.DataFrame([
            {"expiry_date": "2025-06-20", "dte": 20, "moneyness": 1.00, "implied_vol": 0.20},
            {"expiry_date": "2025-07-20", "dte": 50, "moneyness": 1.01, "implied_vol": 0.24},
        ])
        term = term_structure_features(prod_df, "2025-06-20")
        self.assertIn("b3_term_structure_pressure", term)

        history = {
            "OPT1": {"ivs": [0.20, 0.21, 0.23, 0.22, 0.26, 0.27]},
        }
        self.assertGreater(contract_iv_vov_from_history(history, "OPT1", lookback=5), 0.0)

        candidates = pd.DataFrame([{
            "option_code": "OPT1",
            "entry_iv_trend": 0.01,
            "contract_iv_change_1d": 0.02,
            "contract_iv_change_5d": 0.03,
            "premium_to_iv5_loss": 1.5,
            "premium_to_iv10_loss": 1.0,
            "premium_to_stress_loss": 0.8,
            "iv_shock_loss_5_cash": 20.0,
            "iv_shock_loss_10_cash": 60.0,
            "net_premium_cash": 100.0,
        }])
        out = add_b3_candidate_fields(
            candidates,
            "CU",
            "P",
            config={},
            current_iv_state={},
            contract_iv_vov=lambda code, lookback: contract_iv_vov_from_history(history, code, lookback),
            term_features=term,
        )

        self.assertIn("b3_forward_variance_pressure", out.columns)
        self.assertIn("b3_vol_of_vol_proxy", out.columns)
        self.assertGreater(float(out.loc[0, "b3_iv_shock_coverage"]), 0.0)
        self.assertGreaterEqual(float(out.loc[0, "b3_vomma_cash"]), 0.0)

    def test_b3_and_b4_overlays_return_diagnostics(self):
        side_df = pd.DataFrame([
            {
                "product": "CU",
                "option_type": "P",
                "b2_side_score": 80.0,
                "b3_forward_variance_pressure": 0.01,
                "b3_vol_of_vol_proxy": 0.01,
                "b3_iv_shock_coverage": 2.0,
                "b3_joint_stress_coverage": 1.5,
                "b3_vomma_loss_ratio": 0.1,
                "b3_skew_steepening": 0.0,
                "premium_to_iv10_loss": 2.0,
                "premium_to_stress_loss": 1.5,
                "premium_yield_margin": 0.5,
                "gamma_rent_penalty": 0.1,
                "breakeven_cushion_iv": 0.03,
                "breakeven_cushion_rv": 0.04,
            },
            {
                "product": "AL",
                "option_type": "C",
                "b2_side_score": 60.0,
                "b3_forward_variance_pressure": 0.04,
                "b3_vol_of_vol_proxy": 0.04,
                "b3_iv_shock_coverage": 0.5,
                "b3_joint_stress_coverage": 0.4,
                "b3_vomma_loss_ratio": 0.5,
                "b3_skew_steepening": 0.02,
                "premium_to_iv10_loss": 0.5,
                "premium_to_stress_loss": 0.4,
                "premium_yield_margin": 0.2,
                "gamma_rent_penalty": 0.5,
                "breakeven_cushion_iv": 0.01,
                "breakeven_cushion_rv": 0.01,
            },
        ])

        b3 = b3_product_side_budget_overlay(
            side_df,
            {"CU": 0.2, "AL": 0.2},
            0.4,
            "2025-05-06",
            10000.0,
            config={"s1_b3_weight_forward_variance": 0.2},
            rank_high=rank_high,
            rank_low=rank_low,
        )
        self.assertEqual(len(b3["diagnostics"]), 2)
        self.assertIn("CU", b3["product_budget_map"])

        b4 = b4_product_side_budget_overlay(
            side_df,
            {"CU": 0.2, "AL": 0.2},
            0.4,
            "2025-05-06",
            10000.0,
            config={},
            rank_high=rank_high,
            rank_low=rank_low,
        )
        self.assertEqual(len(b4["diagnostics"]), 2)
        self.assertIn("AL", b4["side_meta_map"])

    def test_b6_product_budget_overlay_returns_diagnostics(self):
        side_df = pd.DataFrame([
            {
                "product": "CU",
                "option_type": "P",
                "candidate_count": 2,
                "b5_theta_per_vega": 2.0,
                "premium_to_stress_loss": 2.0,
                "b5_theta_per_gamma": 2.0,
                "b5_range_expansion_proxy_20d": 0.2,
                "gamma_rent_penalty": 0.2,
                "volume": 10,
                "open_interest": 20,
            },
            {
                "product": "AL",
                "option_type": "P",
                "candidate_count": 2,
                "b5_theta_per_vega": 0.5,
                "premium_to_stress_loss": 0.5,
                "b5_theta_per_gamma": 0.5,
                "b5_range_expansion_proxy_20d": 2.0,
                "gamma_rent_penalty": 2.0,
                "volume": 10,
                "open_interest": 20,
            },
        ])

        overlay = b6_product_budget_overlay(
            side_df,
            ["CU", "AL"],
            0.5,
            "2025-05-06",
            10000.0,
            config={"s1_b6_product_tilt_strength": 1.0},
            rank_high=rank_high,
            rank_low=rank_low,
            weighted_average=lambda frame, column: float(pd.to_numeric(frame[column]).mean()),
        )

        self.assertGreater(overlay["product_budget_map"]["CU"], overlay["product_budget_map"]["AL"])
        self.assertEqual(len(overlay["diagnostics"]), 2)

    def test_b6_side_overlay_returns_side_budgets_and_diagnostics(self):
        side_df = pd.DataFrame([
            {
                "product": "CU",
                "option_type": "P",
                "b5_theta_per_vega": 2.0,
                "premium_to_stress_loss": 2.0,
                "b5_theta_per_gamma": 2.0,
                "premium_yield_margin": 2.0,
                "b5_premium_per_vega": 2.0,
                "gamma_rent_penalty": 0.1,
                "b5_trend_z_20d": 0.0,
                "b5_breakout_distance_up_60d": 0.2,
                "b5_breakout_distance_down_60d": 0.2,
                "b3_skew_steepening": 0.0,
                "b5_cooldown_penalty_score": 0.0,
            },
            {
                "product": "CU",
                "option_type": "C",
                "b5_theta_per_vega": 1.0,
                "premium_to_stress_loss": 1.0,
                "b5_theta_per_gamma": 1.0,
                "premium_yield_margin": 1.0,
                "b5_premium_per_vega": 1.0,
                "gamma_rent_penalty": 0.2,
                "b5_trend_z_20d": 0.0,
                "b5_breakout_distance_up_60d": 0.2,
                "b5_breakout_distance_down_60d": 0.2,
                "b3_skew_steepening": 0.0,
                "b5_cooldown_penalty_score": 0.0,
            },
        ])

        overlay = b6_product_side_budget_overlay(
            side_df,
            {"CU": 0.2},
            0.2,
            "2025-05-06",
            10000.0,
            config={"s1_b6_side_tilt_strength": 1.0},
            rank_high=rank_high,
            rank_low=rank_low,
        )

        side_sum = sum(
            meta["b6_side_final_budget_pct"]
            for meta in overlay["side_meta_map"]["CU"].values()
        )
        self.assertAlmostEqual(overlay["product_budget_map"]["CU"], side_sum)
        self.assertLessEqual(overlay["product_budget_map"]["CU"], 0.2)
        self.assertEqual(len(overlay["diagnostics"]), 2)


if __name__ == "__main__":
    unittest.main()
