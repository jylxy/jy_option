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
            "product_side_margin_cap": 0.22,
            "bucket_margin_cap": 0.18,
            "corr_group_margin_cap": 0.20,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_bucket_stress_loss_cap": 0.05,
            "product_side_stress_loss_cap": 0.04,
            "corr_group_stress_loss_cap": 0.05,
            "contract_stress_loss_cap": 0.04,
            "s1_stress_loss_budget_pct": 0.04,
        })
        self.assertEqual(budget["s1_margin_cap"], 0.30)
        self.assertEqual(budget["s3_margin_cap"], 0.30)
        self.assertEqual(budget["product_margin_cap"], 0.18)
        self.assertEqual(budget["product_side_margin_cap"], 0.18)
        self.assertEqual(budget["corr_group_margin_cap"], 0.20)
        self.assertEqual(budget["portfolio_bucket_stress_loss_cap"], 0.03)
        self.assertEqual(budget["product_side_stress_loss_cap"], 0.03)
        self.assertEqual(budget["corr_group_stress_loss_cap"], 0.03)
        self.assertEqual(budget["contract_stress_loss_cap"], 0.03)
        self.assertEqual(budget["s1_stress_loss_budget_pct"], 0.03)

    def test_regime_budget_and_brake(self):
        config = {
            "vol_regime_sizing_enabled": True,
            "margin_cap": 0.50,
            "s1_margin_cap": 0.25,
            "s3_margin_cap": 0.25,
            "portfolio_product_margin_cap": 0.08,
            "portfolio_product_side_margin_cap": 0.05,
            "portfolio_bucket_margin_cap": 0.18,
            "portfolio_corr_group_margin_cap": 0.12,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_product_side_stress_loss_cap": 0.004,
            "portfolio_corr_group_stress_loss_cap": 0.006,
            "portfolio_contract_stress_loss_cap": 0.002,
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
            "product_side_margin_cap": 0.06,
            "bucket_margin_cap": 0.20,
            "corr_group_margin_cap": 0.15,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_bucket_stress_loss_cap": 0.02,
            "product_side_stress_loss_cap": 0.015,
            "corr_group_stress_loss_cap": 0.018,
            "contract_stress_loss_cap": 0.012,
            "s1_stress_loss_budget_pct": 0.001,
        })
        item = {
            "strat": "S1",
            "effective_margin_cap": 0.35,
            "effective_strategy_margin_cap": 0.25,
            "effective_product_margin_cap": 0.12,
            "effective_product_side_margin_cap": 0.05,
            "effective_bucket_margin_cap": 0.15,
            "effective_corr_group_margin_cap": 0.20,
            "effective_stress_loss_cap": 0.02,
            "effective_bucket_stress_loss_cap": 0.03,
            "effective_product_side_stress_loss_cap": 0.010,
            "effective_corr_group_stress_loss_cap": 0.016,
            "effective_contract_stress_loss_cap": 0.008,
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
        self.assertAlmostEqual(budget["product_side_margin_cap"], 0.05)
        self.assertAlmostEqual(budget["bucket_margin_cap"], 0.15)
        self.assertAlmostEqual(budget["corr_group_margin_cap"], 0.15)
        self.assertAlmostEqual(budget["portfolio_stress_loss_cap"], 0.02)
        self.assertAlmostEqual(budget["portfolio_bucket_stress_loss_cap"], 0.02)
        self.assertAlmostEqual(budget["product_side_stress_loss_cap"], 0.010)
        self.assertAlmostEqual(budget["corr_group_stress_loss_cap"], 0.016)
        self.assertAlmostEqual(budget["contract_stress_loss_cap"], 0.008)
        self.assertAlmostEqual(budget["s1_stress_loss_budget_pct"], 0.0008)

    def test_execution_budget_can_preserve_signal_product_overrides(self):
        current = normalize_open_budget({
            "margin_cap": 0.40,
            "s1_margin_cap": 0.25,
            "margin_per": 0.02,
            "product_margin_cap": 0.08,
            "product_side_margin_cap": 0.04,
            "bucket_margin_cap": 0.18,
            "corr_group_margin_cap": 0.10,
            "portfolio_stress_loss_cap": 0.03,
            "portfolio_bucket_stress_loss_cap": 0.004,
            "product_side_stress_loss_cap": 0.002,
            "corr_group_stress_loss_cap": 0.003,
            "contract_stress_loss_cap": 0.0015,
            "s1_stress_loss_budget_pct": 0.001,
        })
        item = {
            "strat": "S1",
            "effective_margin_cap": 0.40,
            "effective_strategy_margin_cap": 0.25,
            "effective_product_margin_cap": 0.12,
            "effective_product_side_margin_cap": 0.07,
            "effective_bucket_margin_cap": 0.24,
            "effective_corr_group_margin_cap": 0.16,
            "effective_stress_loss_cap": 0.02,
            "effective_bucket_stress_loss_cap": 0.006,
            "effective_product_side_stress_loss_cap": 0.005,
            "effective_corr_group_stress_loss_cap": 0.006,
            "effective_contract_stress_loss_cap": 0.004,
            "effective_s1_stress_budget_pct": 0.0008,
        }
        budget = execution_budget_for_item(
            item,
            current,
            {
                "portfolio_execution_budget_policy": "min_signal_current",
                "portfolio_execution_allow_signal_product_overrides": True,
            },
        )

        self.assertAlmostEqual(budget["margin_cap"], 0.40)
        self.assertAlmostEqual(budget["s1_margin_cap"], 0.25)
        self.assertAlmostEqual(budget["product_margin_cap"], 0.12)
        self.assertAlmostEqual(budget["product_side_margin_cap"], 0.07)
        self.assertAlmostEqual(budget["bucket_margin_cap"], 0.24)
        self.assertAlmostEqual(budget["corr_group_margin_cap"], 0.16)
        self.assertAlmostEqual(budget["portfolio_stress_loss_cap"], 0.02)
        self.assertAlmostEqual(budget["portfolio_bucket_stress_loss_cap"], 0.006)
        self.assertAlmostEqual(budget["product_side_stress_loss_cap"], 0.005)
        self.assertAlmostEqual(budget["corr_group_stress_loss_cap"], 0.006)
        self.assertAlmostEqual(budget["contract_stress_loss_cap"], 0.004)
        self.assertAlmostEqual(budget["s1_stress_loss_budget_pct"], 0.0008)

    def test_pending_budget_fields(self):
        fields = pending_budget_fields(
            {
                "margin_cap": 0.4,
                "product_margin_cap": 0.1,
                "product_side_margin_cap": 0.05,
                "corr_group_margin_cap": 0.12,
                "product_side_stress_loss_cap": 0.004,
                "corr_group_stress_loss_cap": 0.006,
                "contract_stress_loss_cap": 0.002,
                "risk_scale": 0.5,
            },
            strategy_cap=0.25,
        )
        self.assertEqual(fields["effective_margin_cap"], 0.4)
        self.assertEqual(fields["effective_strategy_margin_cap"], 0.25)
        self.assertEqual(fields["effective_product_margin_cap"], 0.1)
        self.assertEqual(fields["effective_product_side_margin_cap"], 0.05)
        self.assertEqual(fields["effective_corr_group_margin_cap"], 0.12)
        self.assertEqual(fields["effective_product_side_stress_loss_cap"], 0.004)
        self.assertEqual(fields["effective_corr_group_stress_loss_cap"], 0.006)
        self.assertEqual(fields["effective_contract_stress_loss_cap"], 0.002)
        self.assertEqual(fields["open_budget_risk_scale"], 0.5)


if __name__ == "__main__":
    unittest.main()
