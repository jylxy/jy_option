import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from position_model import Position  # noqa: E402


def make_position(role="sell"):
    return Position(
        strat="S1",
        product="CU",
        code="CUC",
        opt_type="C",
        strike=100.0,
        open_price=10.0,
        n=2,
        open_date="2025-05-01",
        mult=5,
        expiry="2025-06-30",
        mr=0.10,
        role=role,
        spot=100.0,
        exchange="SHFE",
    )


class PositionModelTest(unittest.TestCase):
    def test_daily_pnl_signs(self):
        sell = make_position("sell")
        sell.prev_price = 10.0
        sell.cur_price = 8.0
        self.assertEqual(sell.daily_pnl(), 20.0)

        buy = make_position("buy")
        buy.prev_price = 10.0
        buy.cur_price = 12.0
        self.assertEqual(buy.daily_pnl(), 20.0)

    def test_cash_greeks_use_role_sign(self):
        pos = make_position("sell")
        pos.cur_delta = 0.2
        pos.cur_vega = 3.0
        pos.cur_gamma = 0.01
        pos.cur_theta = -0.5
        self.assertEqual(pos.cash_delta(), -200.0)
        self.assertEqual(pos.cash_vega(), -30.0)
        self.assertEqual(pos.cash_gamma(), -1000.0)
        self.assertEqual(pos.cash_theta(), 5.0)

    def test_cur_margin_only_for_sell(self):
        sell = make_position("sell")
        self.assertGreater(sell.cur_margin(), 0)
        buy = make_position("buy")
        self.assertEqual(buy.cur_margin(), 0.0)

    def test_pnl_attribution_balances_to_total(self):
        pos = make_position("sell")
        pos.prev_spot = 100.0
        pos.cur_spot = 101.0
        pos.prev_delta = 0.1
        pos.prev_gamma = 0.01
        pos.cur_gamma = 0.01
        pos.prev_theta = -0.2
        pos.cur_theta = -0.2
        pos.prev_vega = 2.0
        pos.cur_vega = 2.0
        pos.prev_iv = 0.30
        pos.cur_iv = 0.31
        attr = pos.pnl_attribution(total_pnl=10.0)
        parts = attr["delta_pnl"] + attr["gamma_pnl"] + attr["theta_pnl"] + attr["vega_pnl"] + attr["residual_pnl"]
        self.assertAlmostEqual(parts, attr["total_pnl"])


if __name__ == "__main__":
    unittest.main()
