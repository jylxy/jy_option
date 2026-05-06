import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from intraday_execution import (  # noqa: E402
    confirm_intraday_stop_price,
    intraday_stop_required_volume,
    intraday_stop_threshold,
    is_intraday_stop_price_illiquid,
    prefilter_intraday_exit_codes_by_daily_high,
)


class DummyPosition:
    def __init__(self, code, open_price, role="sell", group_id="g1", n=2):
        self.code = code
        self.open_price = open_price
        self.role = role
        self.group_id = group_id
        self.n = n


class IntradayExecutionHelperTest(unittest.TestCase):
    def test_threshold_uses_lowest_short_leg_stop_line(self):
        config = {"premium_stop_multiple": 2.5}
        positions_by_code = {
            "CUC": [
                DummyPosition("CUC", 10.0),
                DummyPosition("CUC", 8.0),
                DummyPosition("CUC", 1.0, role="buy"),
            ]
        }

        self.assertEqual(intraday_stop_threshold(config, "CUC", positions_by_code), 20.0)

    def test_required_volume_uses_max_of_minimum_and_group_ratio(self):
        config = {
            "intraday_stop_min_trade_volume": 3,
            "intraday_stop_min_group_volume_ratio": 0.25,
        }

        self.assertEqual(intraday_stop_required_volume(config, "CUC", {"CUC": 20}), 5)
        self.assertEqual(intraday_stop_required_volume(config, "CUC", {"CUC": 4}), 3)

    def test_daily_high_prefilter_keeps_whole_group_when_short_can_stop(self):
        config = {
            "intraday_stop_daily_high_prefilter_enabled": True,
            "premium_stop_multiple": 2.5,
            "take_profit_enabled": False,
        }
        sell = DummyPosition("CUC", 10.0, role="sell", group_id="g1")
        protect = DummyPosition("CUP", 1.0, role="buy", group_id="g1")

        keep = prefilter_intraday_exit_codes_by_daily_high(
            config=config,
            positions=[sell, protect],
            high_map={"CUC": 25.0, "CUP": 1.2},
            exit_codes={"CUC", "CUP"},
        )

        self.assertEqual(keep, {"CUC", "CUP"})

    def test_daily_high_prefilter_skips_group_when_short_cannot_stop(self):
        config = {
            "intraday_stop_daily_high_prefilter_enabled": True,
            "premium_stop_multiple": 2.5,
            "take_profit_enabled": False,
        }
        sell = DummyPosition("CUC", 10.0, role="sell", group_id="g1")
        protect = DummyPosition("CUP", 1.0, role="buy", group_id="g1")

        keep = prefilter_intraday_exit_codes_by_daily_high(
            config=config,
            positions=[sell, protect],
            high_map={"CUC": 24.9, "CUP": 1.2},
            exit_codes={"CUC", "CUP"},
        )

        self.assertEqual(keep, set())

    def test_illiquid_trigger_is_filtered_without_confirmation(self):
        config = {
            "premium_stop_multiple": 2.5,
            "intraday_stop_liquidity_filter_enabled": True,
            "intraday_stop_min_trade_volume": 3,
            "intraday_stop_min_group_volume_ratio": 0.10,
        }
        positions_by_code = {"CUC": [DummyPosition("CUC", 10.0, n=100)]}

        self.assertTrue(
            is_intraday_stop_price_illiquid(
                config, "CUC", 25.0, 2.0, positions_by_code, {"CUC": 100}
            )
        )

    def test_confirmation_waits_for_observations_and_volume(self):
        config = {
            "premium_stop_multiple": 2.5,
            "intraday_stop_confirmation_enabled": True,
            "intraday_stop_confirmation_observations": 2,
            "intraday_stop_confirmation_use_cumulative_volume": True,
            "intraday_stop_min_trade_volume": 3,
            "intraday_stop_min_group_volume_ratio": 0.10,
        }
        positions_by_code = {"CUC": [DummyPosition("CUC", 10.0, n=20)]}
        pending = {}

        first = confirm_intraday_stop_price(
            config=config,
            code="CUC",
            price=25.0,
            volume=1.0,
            tm="2025-05-02 10:00:00",
            stop_pending=pending,
            positions_by_code=positions_by_code,
            quantity_by_code={"CUC": 20},
        )
        second = confirm_intraday_stop_price(
            config=config,
            code="CUC",
            price=25.5,
            volume=2.0,
            tm="2025-05-02 10:01:00",
            stop_pending=pending,
            positions_by_code=positions_by_code,
            quantity_by_code={"CUC": 20},
        )

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(pending, {})


if __name__ == "__main__":
    unittest.main()
