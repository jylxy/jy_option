import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from portfolio_diagnostics import build_portfolio_diagnostics_records  # noqa: E402


class DummyPosition:
    strat = "S1"
    role = "sell"
    opt_type = "P"
    product = "CU"
    code = "CU_P_1"
    n = 2
    open_price = 10.0
    cur_price = 6.0
    mult = 5
    stress_loss = 80.0

    def cur_margin(self):
        return 1000.0

    def cash_vega(self):
        return -20.0

    def cash_gamma(self):
        return -30.0

    def cash_theta(self):
        return 5.0


class PortfolioDiagnosticsTest(unittest.TestCase):
    def test_builds_bucket_corr_and_s1_product_side_records(self):
        records = build_portfolio_diagnostics_records(
            positions=[DummyPosition()],
            config={
                "portfolio_bucket_max_active_products": 3,
                "portfolio_corr_group_max_active_products": 2,
                "portfolio_contract_lot_cap": 10,
            },
            budget={
                "bucket_margin_cap": 0.25,
                "portfolio_bucket_stress_loss_cap": 0.03,
                "corr_group_margin_cap": 0.20,
                "corr_group_stress_loss_cap": 0.02,
                "product_side_margin_cap": 0.10,
                "product_side_stress_loss_cap": 0.01,
                "contract_stress_loss_cap": 0.005,
            },
            date_str="2025-05-06",
            nav=10000.0,
            current_vol_regimes={"CU": "falling_vol_carry"},
            current_portfolio_regime="normal",
            normalize_product_key=lambda x: str(x).upper(),
            get_product_bucket=lambda _product: "metal",
            get_product_corr_group=lambda _product: "base_metal",
        )

        by_scope = {row["scope"]: row for row in records}
        self.assertEqual(set(by_scope), {"bucket", "corr_group", "s1_product_side"})
        self.assertEqual(by_scope["bucket"]["name"], "metal")
        self.assertEqual(by_scope["corr_group"]["name"], "base_metal")
        self.assertEqual(by_scope["s1_product_side"]["name"], "CU:P")
        self.assertEqual(by_scope["s1_product_side"]["open_premium"], 100.0)
        self.assertEqual(by_scope["s1_product_side"]["current_liability"], 60.0)
        self.assertEqual(by_scope["s1_product_side"]["unrealized_premium"], 40.0)
        self.assertAlmostEqual(by_scope["s1_product_side"]["stress_loss_pct"], 0.008)


if __name__ == "__main__":
    unittest.main()
