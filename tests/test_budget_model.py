import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from budget_model import (
    execution_budget_for_item,
    get_effective_open_budget,
    normalize_open_budget,
    pending_budget_fields,
)


class BudgetModelTest(unittest.TestCase):
    def test_normalize_caps_nested_limits(self):
        budget = normalize_open_budget({
            "margin_cap": 0.30,
            "s1_margin_cap": 0.50,
            "s3_margin_cap": 0.40,
            "margin_per": 0.02,
            "product_margin_cap": 0.25,
            "bucket_margin_cap": 0.18,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_bucket_stress_loss_cap": 0.05,
            "s1_stress_loss_budget_pct": 0.04,
        })
        self.assertEqual(budget["s1_margin_cap"], 0.30)
        self.assertEqual(budget["s3_margin_cap"], 0.30)
        self.assertEqual(budget["product_margin_cap"], 0.18)
        self.assertEqual(budget["portfolio_bucket_stress_loss_cap"], 0.03)
        self.assertEqual(budget["s1_stress_loss_budget_pct"], 0.03)

    def test_regime_budget_and_brake(self):
        config = {
            "vol_regime_sizing_enabled": True,
            "margin_cap": 0.50,
            "s1_margin_cap": 0.25,
            "s3_margin_cap": 0.25,
            "portfolio_product_margin_cap": 0.08,
            "portfolio_bucket_margin_cap": 0.18,
            "portfolio_stress_loss_cap": 0.03,
            "s1_stress_loss_budget_pct": 0.001,
            "vol_regime_falling_margin_cap": 0.40,
            "vol_regime_falling_s1_margin_cap": 0.35,
            "vol_regime_normal_margin_cap": 0.25,
            "vol_regime_normal_s1_margin_cap": 0.25,
            "portfolio_dd_pause_falling": 0.008,
            "portfolio_dd_reduce_limit": 0.012,
            "portfolio_dd_reduce_scale": 0.50,
            "portfolio_budget_brake_enabled": True,
        }
        budget = get_effective_open_budget(
            config,
            portfolio_regime="falling_vol_carry",
            drawdown=0.013,
            recent_stop_count=0,
        )
        self.assertEqual(budget["portfolio_regime"], "falling_vol_carry_paused")
        self.assertAlmostEqual(budget["margin_cap"], 0.125)
        self.assertAlmostEqual(budget["s1_margin_cap"], 0.125)
        self.assertEqual(budget["risk_scale"], 0.50)
        self.assertIn("dd_pause_falling", budget["brake_reason"])
        self.assertIn("dd_reduce", budget["brake_reason"])

    def test_execution_budget_policy_min_signal_current(self):
        current = normalize_open_budget({
            "margin_cap": 0.40,
            "s1_margin_cap": 0.30,
            "s3_margin_cap": 0.20,
            "margin_per": 0.02,
            "product_margin_cap": 0.10,
            "bucket_margin_cap": 0.20,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_bucket_stress_loss_cap": 0.02,
            "s1_stress_loss_budget_pct": 0.001,
        })
        item = {
            "strat": "S1",
            "effective_margin_cap": 0.35,
            "effective_strategy_margin_cap": 0.25,
            "effective_product_margin_cap": 0.12,
            "effective_bucket_margin_cap": 0.15,
            "effective_stress_loss_cap": 0.02,
            "effective_bucket_stress_loss_cap": 0.03,
            "effective_s1_stress_budget_pct": 0.0008,
        }
        budget = execution_budget_for_item(
            item,
            current,
            {"portfolio_execution_budget_policy": "min_signal_current"},
        )
        self.assertAlmostEqual(budget["margin_cap"], 0.35)
        self.assertAlmostEqual(budget["s1_margin_cap"], 0.25)
        self.assertAlmostEqual(budget["product_margin_cap"], 0.10)
        self.assertAlmostEqual(budget["bucket_margin_cap"], 0.15)
        self.assertAlmostEqual(budget["portfolio_stress_loss_cap"], 0.02)
        self.assertAlmostEqual(budget["portfolio_bucket_stress_loss_cap"], 0.02)
        self.assertAlmostEqual(budget["s1_stress_loss_budget_pct"], 0.0008)

    def test_pending_budget_fields(self):
        fields = pending_budget_fields(
            {"margin_cap": 0.4, "product_margin_cap": 0.1, "risk_scale": 0.5},
            strategy_cap=0.25,
        )
        self.assertEqual(fields["effective_margin_cap"], 0.4)
        self.assertEqual(fields["effective_strategy_margin_cap"], 0.25)
        self.assertEqual(fields["effective_product_margin_cap"], 0.1)
        self.assertEqual(fields["open_budget_risk_scale"], 0.5)


if __name__ == "__main__":
    unittest.main()
