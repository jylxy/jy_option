import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from execution_model import apply_execution_slippage  # noqa: E402


class ExecutionModelTest(unittest.TestCase):
    def test_disabled_slippage_keeps_price(self):
        price, slip = apply_execution_slippage(
            10.0,
            "buy_close",
            {"execution_slippage_enabled": False, "execution_slippage_pct": 0.01},
        )

        self.assertEqual(price, 10.0)
        self.assertEqual(slip, 0.0)

    def test_buy_and_sell_get_adverse_prices(self):
        cfg = {"execution_slippage_enabled": True, "execution_slippage_pct": 0.01}

        buy_price, buy_slip = apply_execution_slippage(10.0, "buy_close", cfg)
        sell_price, sell_slip = apply_execution_slippage(10.0, "sell_open", cfg)

        self.assertAlmostEqual(buy_price, 10.1)
        self.assertAlmostEqual(buy_slip, 0.1)
        self.assertAlmostEqual(sell_price, 9.9)
        self.assertAlmostEqual(sell_slip, 0.1)

    def test_stop_and_expiry_rules(self):
        cfg = {
            "execution_slippage_enabled": True,
            "execution_slippage_pct": 0.01,
            "execution_stop_slippage_pct": 0.03,
            "execution_slippage_min_abs": 0.2,
        }

        stop_price, stop_slip = apply_execution_slippage(10.0, "buy_close", cfg, reason="sl_premium")
        expiry_price, expiry_slip = apply_execution_slippage(10.0, "buy_close", cfg, reason="expiry")

        self.assertAlmostEqual(stop_price, 10.3)
        self.assertAlmostEqual(stop_slip, 0.3)
        self.assertEqual(expiry_price, 10.0)
        self.assertEqual(expiry_slip, 0.0)


if __name__ == "__main__":
    unittest.main()
