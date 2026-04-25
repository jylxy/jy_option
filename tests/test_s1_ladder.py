import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from toolkit_minute_engine import ToolkitMinuteEngine  # noqa: E402


class S1LadderShapeTest(unittest.TestCase):
    def make_engine(self, config, regimes):
        engine = ToolkitMinuteEngine.__new__(ToolkitMinuteEngine)
        engine.config = config
        engine._current_vol_regimes = regimes
        return engine

    def test_falling_regime_can_widen_ladder_without_changing_normal_regime(self):
        config = {
            "s1_split_across_neighbor_contracts": True,
            "s1_neighbor_contract_count": 3,
            "s1_neighbor_max_delta_gap": 0.025,
            "s1_trend_ladder_enabled": True,
            "s1_trend_ladder_strong_contract_count": 4,
            "s1_trend_ladder_strong_max_delta_gap": 0.035,
            "s1_regime_ladder_enabled": True,
            "vol_regime_falling_s1_ladder_strong_contract_count": 6,
            "vol_regime_falling_s1_ladder_strong_max_delta_gap": 0.045,
        }
        engine = self.make_engine(
            config,
            {"CU": "falling_vol_carry", "AU": "normal_vol"},
        )

        falling_count, falling_gap = engine._s1_ladder_shape(
            {"trend_role": "strong"},
            product="CU",
        )
        normal_count, normal_gap = engine._s1_ladder_shape(
            {"trend_role": "strong"},
            product="AU",
        )

        self.assertEqual(falling_count, 6)
        self.assertAlmostEqual(falling_gap, 0.045)
        self.assertEqual(normal_count, 4)
        self.assertAlmostEqual(normal_gap, 0.035)

    def test_falling_regime_can_raise_product_budget_caps(self):
        config = {
            "s1_product_regime_budget_overrides_enabled": True,
            "s1_product_regime_budget_override_prefixes": ["falling"],
            "vol_regime_falling_product_margin_cap": 0.12,
            "vol_regime_falling_bucket_margin_cap": 0.24,
            "vol_regime_falling_bucket_stress_loss_cap": 0.006,
        }
        engine = self.make_engine(
            config,
            {"CU": "falling_vol_carry", "AU": "normal_vol"},
        )
        base_budget = {
            "margin_cap": 0.50,
            "s1_margin_cap": 0.25,
            "product_margin_cap": 0.08,
            "bucket_margin_cap": 0.18,
            "portfolio_stress_loss_cap": 0.015,
            "portfolio_bucket_stress_loss_cap": 0.004,
            "s1_stress_loss_budget_pct": 0.0012,
        }

        falling_budget = engine._product_regime_open_budget("CU", base_budget)
        normal_budget = engine._product_regime_open_budget("AU", base_budget)

        self.assertAlmostEqual(falling_budget["product_margin_cap"], 0.12)
        self.assertAlmostEqual(falling_budget["bucket_margin_cap"], 0.24)
        self.assertAlmostEqual(falling_budget["portfolio_bucket_stress_loss_cap"], 0.006)
        self.assertAlmostEqual(normal_budget["product_margin_cap"], 0.08)
        self.assertAlmostEqual(normal_budget["bucket_margin_cap"], 0.18)
        self.assertAlmostEqual(normal_budget["portfolio_bucket_stress_loss_cap"], 0.004)

    def test_structural_low_caution_reduces_non_falling_stress_budget(self):
        config = {
            "s1_product_regime_stress_budget_enabled": True,
            "vol_regime_low_s1_stress_loss_budget_pct": 0.0015,
            "vol_regime_falling_s1_stress_loss_budget_pct": 0.004,
            "low_iv_structural_caution_enabled": True,
            "low_iv_structural_s1_stress_budget_mult": 0.5,
        }
        engine = self.make_engine(
            config,
            {"CU": "low_stable_vol", "AU": "falling_vol_carry"},
        )
        engine._current_open_budget = {"risk_scale": 1.0}

        low_budget = engine._product_s1_stress_budget_pct(
            "CU",
            0.0012,
            iv_state={"is_structural_low_iv": True},
        )
        falling_budget = engine._product_s1_stress_budget_pct(
            "AU",
            0.0012,
            iv_state={"is_structural_low_iv": True},
        )

        self.assertAlmostEqual(low_budget, 0.00075)
        self.assertAlmostEqual(falling_budget, 0.004)


if __name__ == "__main__":
    unittest.main()
