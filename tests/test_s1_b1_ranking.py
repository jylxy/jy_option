import os
import sys
import types
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

toolkit_module = types.ModuleType("toolkit")
selector_module = types.ModuleType("toolkit.selector")
selector_module.select_bars_sql = lambda _sql: pd.DataFrame()
sys.modules.setdefault("toolkit", toolkit_module)
sys.modules.setdefault("toolkit.selector", selector_module)

from toolkit_minute_engine import ToolkitMinuteEngine  # noqa: E402


class S1B1RankingTest(unittest.TestCase):
    def make_engine(self):
        engine = ToolkitMinuteEngine.__new__(ToolkitMinuteEngine)
        engine.config = {
            "s1_baseline_product_ranking_mode": "liquidity_oi",
            "s1_expiry_mode": "nth_expiry",
            "s1_expiry_rank": 2,
            "s1_sell_delta_floor": 0.0,
            "s1_sell_delta_cap": 0.10,
            "s1_min_premium_fee_multiple": 0.0,
            "s1_min_volume": 0,
            "s1_min_oi": 0,
            "option_fee_use_broker_table": False,
            "fee": 0.0,
        }
        return engine

    def product_frame(self, product, volume, oi):
        return pd.DataFrame([
            {
                "product": product,
                "expiry_date": "2025-05-26",
                "dte": 5,
                "option_type": "P",
                "moneyness": 0.92,
                "delta": -0.05,
                "option_close": 1.0,
                "volume": 1,
                "open_interest": 1,
                "multiplier": 10,
            },
            {
                "product": product,
                "expiry_date": "2025-06-26",
                "dte": 35,
                "option_type": "P",
                "moneyness": 0.92,
                "delta": -0.05,
                "option_close": 1.0,
                "volume": volume,
                "open_interest": oi,
                "multiplier": 10,
            },
        ])

    def test_b1_product_order_prefers_more_liquid_eligible_contracts(self):
        engine = self.make_engine()
        frames = {
            "AA": self.product_frame("AA", volume=1, oi=1),
            "ZZ": self.product_frame("ZZ", volume=1000, oi=2000),
        }

        ordered = engine._baseline_product_order(frames)

        self.assertEqual(ordered, ["ZZ", "AA"])

    def test_default_baseline_product_order_remains_code_sorted(self):
        engine = self.make_engine()
        engine.config["s1_baseline_product_ranking_mode"] = "code"
        frames = {
            "ZZ": self.product_frame("ZZ", volume=1000, oi=2000),
            "AA": self.product_frame("AA", volume=1, oi=1),
        }

        ordered = engine._baseline_product_order(frames)

        self.assertEqual(ordered, ["AA", "ZZ"])


if __name__ == "__main__":
    unittest.main()
