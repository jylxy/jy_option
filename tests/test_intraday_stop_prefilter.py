import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

toolkit_module = types.ModuleType("toolkit")
selector_module = types.ModuleType("toolkit.selector")
selector_module.select_bars_sql = lambda _sql: None
sys.modules.setdefault("toolkit", toolkit_module)
sys.modules.setdefault("toolkit.selector", selector_module)

from position_model import Position  # noqa: E402
from toolkit_minute_engine import ToolkitMinuteEngine  # noqa: E402


class DummyLoader:
    def __init__(self, high_map):
        self.high_map = high_map

    def get_daily_option_high_map(self, date_str):
        return self.high_map


def make_position(code, open_price=10.0, role="sell", group_id="g1"):
    return Position(
        strat="S1",
        product="CU",
        code=code,
        opt_type="C",
        strike=100.0,
        open_price=open_price,
        n=2,
        open_date="2025-05-01",
        mult=5,
        expiry="2025-06-30",
        mr=0.10,
        role=role,
        spot=100.0,
        exchange="SHFE",
        group_id=group_id,
    )


class IntradayStopPrefilterTest(unittest.TestCase):
    def make_engine(self, high_map, positions, extra_config=None):
        engine = ToolkitMinuteEngine.__new__(ToolkitMinuteEngine)
        config = {
            "intraday_stop_daily_high_prefilter_enabled": True,
            "premium_stop_multiple": 2.5,
            "take_profit_enabled": False,
        }
        if extra_config:
            config.update(extra_config)
        engine.config = config
        engine.loader = DummyLoader(high_map)
        engine.positions = positions
        return engine

    def test_skips_group_when_daily_high_never_reaches_stop(self):
        sell = make_position("CUC", open_price=10.0, role="sell", group_id="g1")
        buy = make_position("CUP", open_price=1.0, role="protect", group_id="g1")
        engine = self.make_engine({"CUC": 24.9, "CUP": 3.0}, [sell, buy])

        keep = engine._prefilter_intraday_exit_codes_by_daily_high(
            "2025-05-02",
            {"CUC", "CUP"},
        )

        self.assertEqual(keep, set())

    def test_keeps_whole_group_when_any_sell_leg_reaches_stop(self):
        sell = make_position("CUC", open_price=10.0, role="sell", group_id="g1")
        buy = make_position("CUP", open_price=1.0, role="protect", group_id="g1")
        engine = self.make_engine({"CUC": 25.0, "CUP": 3.0}, [sell, buy])

        keep = engine._prefilter_intraday_exit_codes_by_daily_high(
            "2025-05-02",
            {"CUC", "CUP"},
        )

        self.assertEqual(keep, {"CUC", "CUP"})

    def test_missing_daily_high_keeps_code_conservatively(self):
        sell = make_position("CUC", open_price=10.0, role="sell", group_id="g1")
        engine = self.make_engine({}, [sell])

        keep = engine._prefilter_intraday_exit_codes_by_daily_high(
            "2025-05-02",
            {"CUC"},
        )

        self.assertEqual(keep, {"CUC"})

    def test_take_profit_disables_prefilter(self):
        sell = make_position("CUC", open_price=10.0, role="sell", group_id="g1")
        engine = self.make_engine(
            {"CUC": 1.0},
            [sell],
            extra_config={"take_profit_enabled": True},
        )

        keep = engine._prefilter_intraday_exit_codes_by_daily_high(
            "2025-05-02",
            {"CUC"},
        )

        self.assertEqual(keep, {"CUC"})


if __name__ == "__main__":
    unittest.main()
