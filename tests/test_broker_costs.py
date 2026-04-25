import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from broker_costs import (
    broker_margin_ratio_for_product,
    option_fee_code_candidates,
    resolve_option_fee,
    resolve_option_roundtrip_fee,
)


class BrokerCostsTest(unittest.TestCase):
    def test_margin_maps_index_options_to_index_futures(self):
        self.assertAlmostEqual(broker_margin_ratio_for_product("IO"), 0.12)
        self.assertAlmostEqual(broker_margin_ratio_for_product("HO2506"), 0.12)
        self.assertAlmostEqual(broker_margin_ratio_for_product("MO2506"), 0.12)

    def test_margin_maps_commodity_roots(self):
        self.assertAlmostEqual(broker_margin_ratio_for_product("ag2506"), 0.16)
        self.assertAlmostEqual(broker_margin_ratio_for_product("ZC"), 0.50)

    def test_fee_code_candidates_cover_czce_and_other_exchanges(self):
        self.assertEqual(option_fee_code_candidates("CF", "P")[:2], ["CFP", "CF"])
        self.assertIn("CU_O", option_fee_code_candidates("cu", "C"))

    def test_resolve_fee_uses_speculative_broker_table(self):
        self.assertAlmostEqual(resolve_option_fee({}, "CU", "C", "open"), 5.0)
        self.assertAlmostEqual(resolve_option_fee({}, "JD", "P", "close"), 0.5)
        self.assertAlmostEqual(resolve_option_fee({}, "IO", "P", "open"), 15.0)
        self.assertAlmostEqual(resolve_option_fee({}, "IO", "P", "exercise"), 1.0)

    def test_resolve_fee_falls_back_to_scalar_for_uncovered_products(self):
        cfg = {"fee": 3.0}
        self.assertAlmostEqual(resolve_option_fee(cfg, "510300", "P", "open"), 3.0)

    def test_resolve_fee_accepts_product_override(self):
        cfg = {"option_fee_by_product": {"IO": {"open": 8, "close": 7}}}
        self.assertAlmostEqual(resolve_option_fee(cfg, "IO", "P", "open"), 8.0)
        self.assertAlmostEqual(resolve_option_roundtrip_fee(cfg, "IO", "P"), 15.0)


if __name__ == "__main__":
    unittest.main()
