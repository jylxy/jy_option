import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from margin_model import estimate_margin, resolve_margin_ratio


class MarginModelTest(unittest.TestCase):
    def test_equity_call_margin_uses_seven_percent_floor(self):
        margin = estimate_margin(
            spot=3.0,
            strike=3.3,
            option_type="C",
            option_price=0.05,
            multiplier=10000,
            margin_ratio=0.12,
            exchange="SSE",
        )
        self.assertAlmostEqual(margin, 2600.0)

    def test_equity_put_margin_uses_strike_floor_and_strike_cap(self):
        margin = estimate_margin(
            spot=3.0,
            strike=2.7,
            option_type="P",
            option_price=0.04,
            multiplier=10000,
            margin_ratio=0.12,
            exchange="SZSE",
        )
        self.assertAlmostEqual(margin, 2290.0)

    def test_commodity_margin_deducts_half_otm(self):
        margin = estimate_margin(
            spot=560.0,
            strike=600.0,
            option_type="C",
            option_price=5.0,
            multiplier=1000,
            margin_ratio=0.04,
            exchange="SHFE",
        )
        self.assertAlmostEqual(margin, 16200.0)

    def test_ratio_resolution_priority(self):
        config = {
            "margin_ratio_by_product": {"CU": "8%"},
            "margin_ratio_by_exchange": {"SHFE": 0.06},
        }
        self.assertAlmostEqual(
            resolve_margin_ratio(
                exchange="SHFE",
                product="CU2506",
                config=config,
                data_ratio=0.05,
            ),
            0.08,
        )
        self.assertAlmostEqual(
            resolve_margin_ratio(
                exchange="SHFE",
                product="AU2506",
                config=config,
                data_ratio=0.05,
            ),
            0.05,
        )


if __name__ == "__main__":
    unittest.main()
