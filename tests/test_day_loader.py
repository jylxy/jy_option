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

from day_loader import ToolkitDayLoader  # noqa: E402


class FakeContractInfo:
    pass


class DayLoaderTest(unittest.TestCase):
    def test_clear_cache_preserves_requested_dates(self):
        loader = ToolkitDayLoader(FakeContractInfo())
        loader._day_cache = {"2025-01-02": pd.DataFrame({"x": [1]}), "2025-01-03": pd.DataFrame({"x": [2]})}
        loader._daily_agg_cache = {"2025-01-02": pd.DataFrame({"x": [1]}), "2025-01-03": pd.DataFrame({"x": [2]})}
        loader._spot_daily_cache = {"2025-01-02": {"CU": 1.0}, "2025-01-03": {"CU": 2.0}}

        loader.clear_cache(keep_dates={"2025-01-03"})

        self.assertEqual(list(loader._day_cache.keys()), ["2025-01-03"])
        self.assertEqual(list(loader._daily_agg_cache.keys()), ["2025-01-03"])
        self.assertEqual(list(loader._spot_daily_cache.keys()), ["2025-01-03"])

    def test_spot_tables_for_codes(self):
        loader = ToolkitDayLoader(FakeContractInfo())
        self.assertEqual(loader._spot_tables_for_codes(["510300.SH"]), ["etf_hf_1min_non_ror"])
        self.assertEqual(loader._spot_tables_for_codes(["CU2506.SHF"]), ["future_hf_1min"])

    def test_empty_inputs_return_empty_frames(self):
        loader = ToolkitDayLoader(FakeContractInfo())
        self.assertTrue(loader.load_spot_day_minute("2025-01-02", []).empty)
        self.assertTrue(loader.load_day_minute("2025-01-02", code_list=[]).empty)


if __name__ == "__main__":
    unittest.main()
