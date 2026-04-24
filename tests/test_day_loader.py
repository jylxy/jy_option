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

import day_loader as day_loader_module  # noqa: E402
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

    def test_load_spot_day_minute_can_filter_times(self):
        loader = ToolkitDayLoader(FakeContractInfo())
        loader._spot_tables_for_codes = lambda _codes: ["etf_table"]
        seen_sql = []

        def fake_select(sql):
            seen_sql.append(sql)
            return pd.DataFrame([
                {"ths_code": "510300.SH", "time": "10:00:00", "close": 3.5},
            ])

        old_select = day_loader_module.select_bars_sql
        day_loader_module.select_bars_sql = fake_select
        try:
            out = loader.load_spot_day_minute(
                "2025-01-02",
                ["510300.SH"],
                time_list=["10:00:00", "10:15:00"],
            )
        finally:
            day_loader_module.select_bars_sql = old_select

        self.assertFalse(out.empty)
        self.assertIn("time IN ('10:00:00', '10:15:00')", seen_sql[0])

    def test_preload_spot_daily_close_batch_populates_cache(self):
        loader = ToolkitDayLoader(FakeContractInfo())
        calls = []

        def fake_query(table_name, dates, code_list_sql):
            calls.append((table_name, tuple(dates), code_list_sql))
            return pd.DataFrame([
                {"trade_date": "2025-01-02", "ths_code": "510300.SH", "last_close": 3.5},
                {"trade_date": "2025-01-03", "ths_code": "510300.SH", "last_close": 3.6},
            ])

        loader._spot_tables_for_codes = lambda _codes: ["etf_table"]
        loader._query_spot_daily_table_batch = fake_query
        loader._query_spot_daily_table = lambda *_args: self.fail("spot batch cache was not used")

        loader._preload_spot_daily_close_batch(["2025-01-02", "2025-01-03"], ["510300.SH"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(loader._get_spot_daily_close_map("2025-01-02", ["510300.SH"]), {"510300.SH": 3.5})
        self.assertEqual(loader._get_spot_daily_close_map("2025-01-03", ["510300.SH"]), {"510300.SH": 3.6})


if __name__ == "__main__":
    unittest.main()
