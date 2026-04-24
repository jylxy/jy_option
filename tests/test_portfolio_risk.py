import os
import sys
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import portfolio_risk as pr  # noqa: E402


def norm(product):
    return str(product).upper()


def bucket(product):
    product = norm(product)
    if product in {"CU", "AL"}:
        return "metal"
    return "other"


def corr_group(product):
    product = norm(product)
    if product in {"CU", "AL"}:
        return "base_metal"
    return product


class FakePosition:
    def __init__(self, product, margin=0.0, strat="S1", role="sell",
                 cash_delta=0.0, cash_vega=0.0, cash_gamma=0.0,
                 stress_loss=0.0):
        self.product = product
        self.strat = strat
        self.role = role
        self._margin = margin
        self._cash_delta = cash_delta
        self._cash_vega = cash_vega
        self._cash_gamma = cash_gamma
        self.stress_loss = stress_loss

    def cur_margin(self):
        return self._margin

    def cash_delta(self):
        return self._cash_delta

    def cash_vega(self):
        return self._cash_vega

    def cash_gamma(self):
        return self._cash_gamma


class PortfolioRiskTest(unittest.TestCase):
    def test_check_margin_ok_enforces_total_and_strategy_caps(self):
        self.assertTrue(pr.check_margin_ok(100.0, 50.0, 50.0, 1000.0, 0.20, 0.20))
        self.assertFalse(pr.check_margin_ok(180.0, 50.0, 50.0, 1000.0, 0.20, 0.20))
        self.assertFalse(pr.check_margin_ok(100.0, 180.0, 50.0, 1000.0, 0.50, 0.20))
        self.assertFalse(pr.check_margin_ok(100.0, 50.0, 50.0, 0.0, 0.50, 0.20))

    def test_open_greek_and_stress_state_include_pending_sell_orders(self):
        positions = [
            FakePosition("CU", margin=100.0, cash_delta=-10.0, cash_vega=-20.0,
                         cash_gamma=-30.0, stress_loss=40.0)
        ]
        pending = [{
            "role": "sell",
            "product": "AL",
            "cash_vega": -5.0,
            "cash_gamma": -7.0,
            "one_contract_stress_loss": 3.0,
            "n": 4,
        }]

        greek = pr.get_open_greek_state(positions, pending, bucket)
        stress = pr.get_open_stress_loss_state(positions, pending, bucket)

        self.assertEqual(greek["cash_delta"], -10.0)
        self.assertEqual(greek["cash_vega"], -25.0)
        self.assertEqual(greek["cash_gamma"], -37.0)
        self.assertEqual(greek["bucket_vega"]["metal"], -25.0)
        self.assertEqual(stress["stress_loss"], 52.0)
        self.assertEqual(stress["bucket_stress_loss"]["metal"], 52.0)

    def test_passes_portfolio_construction_rejects_product_cap(self):
        cfg = {
            "portfolio_construction_enabled": True,
            "portfolio_product_margin_cap": 0.10,
            "portfolio_bucket_control_enabled": False,
            "portfolio_corr_control_enabled": False,
            "portfolio_stress_gate_enabled": False,
        }
        positions = [FakePosition("CU", margin=90.0)]

        ok = pr.passes_portfolio_construction(
            cfg,
            product="CU",
            nav=1000.0,
            new_margin=20.0,
            positions=positions,
            pending_opens=[],
            spot_history={},
            normalize_product_key=norm,
            get_product_bucket=bucket,
            get_product_corr_group=corr_group,
            budget={},
        )

        self.assertFalse(ok)

    def test_dynamic_corr_control_rejects_highly_correlated_peer(self):
        cfg = {
            "portfolio_construction_enabled": True,
            "portfolio_product_margin_cap": 0.50,
            "portfolio_bucket_control_enabled": False,
            "portfolio_corr_control_enabled": True,
            "portfolio_corr_group_max_active_products": 3,
            "portfolio_dynamic_corr_control_enabled": True,
            "portfolio_corr_window": 20,
            "portfolio_corr_min_periods": 3,
            "portfolio_corr_threshold": 0.90,
            "portfolio_corr_max_high_corr_peers": 0,
            "portfolio_stress_gate_enabled": False,
        }
        dates = [f"2025-05-{i:02d}" for i in range(1, 8)]
        cu = [100, 101, 102, 103, 104, 105, 106]
        al = [200, 202, 204, 206, 208, 210, 212]
        spot_history = {
            "CU": {"dates": dates, "spots": cu},
            "AL": {"dates": dates, "spots": al},
        }
        positions = [FakePosition("CU", margin=50.0)]

        ok = pr.passes_portfolio_construction(
            cfg,
            product="AL",
            nav=1000.0,
            new_margin=10.0,
            positions=positions,
            pending_opens=[],
            spot_history=spot_history,
            normalize_product_key=norm,
            get_product_bucket=bucket,
            get_product_corr_group=corr_group,
            date_str="2025-05-07",
            budget={},
        )

        self.assertFalse(ok)

    def test_cash_vega_and_stress_budget_reject_new_trade(self):
        cfg = {
            "portfolio_cash_vega_cap": 0.01,
            "portfolio_cash_gamma_cap": 0.0,
            "portfolio_bucket_cash_vega_cap": 0.0,
            "portfolio_bucket_cash_gamma_cap": 0.0,
            "portfolio_stress_gate_enabled": True,
            "portfolio_stress_loss_cap": 0.02,
            "portfolio_bucket_stress_loss_cap": 0.0,
            "portfolio_stress_spot_move_pct": 0.03,
            "portfolio_stress_iv_up_points": 5.0,
        }
        greek_state = {
            "cash_delta": 0.0,
            "cash_vega": -5.0,
            "cash_gamma": 0.0,
            "bucket_vega": {"metal": -5.0},
            "bucket_gamma": {"metal": 0.0},
        }
        stress_state = {
            "stress_loss": 0.0,
            "bucket_stress_loss": {"metal": 0.0},
        }

        self.assertFalse(pr.passes_greek_budget(
            cfg,
            product="CU",
            nav=1000.0,
            greek_state=greek_state,
            get_product_bucket=bucket,
            new_cash_vega=-6.0,
        ))
        self.assertFalse(pr.passes_stress_budget(
            cfg,
            nav=1000.0,
            greek_state=greek_state,
            stress_state=stress_state,
            get_product_bucket=bucket,
            product="CU",
            budget={"portfolio_stress_loss_cap": 0.02},
            new_stress_loss=25.0,
        ))

    def test_return_series_and_corr_are_date_aligned(self):
        dates = ["2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04"]
        spot_history = {
            "CU": {"dates": dates, "spots": [100, 101, 102, 103]},
            "AL": {"dates": dates, "spots": [200, 202, 204, 206]},
        }
        cfg = {"portfolio_corr_window": 10, "portfolio_corr_min_periods": 2}

        returns = pr.product_return_series(spot_history, norm, "cu", current_date="2025-05-03")
        corr = pr.recent_product_corr(cfg, spot_history, norm, "CU", "AL", "2025-05-04")

        self.assertEqual(len(returns), 2)
        self.assertTrue(np.isfinite(corr))
        self.assertGreater(corr, 0.99)

    def test_diversify_product_order_round_robins_buckets(self):
        cfg = {"portfolio_bucket_round_robin": True}

        ordered = pr.diversify_product_order(cfg, ["CU", "AL", "AU"], bucket)

        self.assertEqual(ordered, ["CU", "AU", "AL"])


if __name__ == "__main__":
    unittest.main()
