import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from trading_calendar import (  # noqa: E402
    filter_trading_dates,
    load_trading_dates_cache,
    query_trading_dates,
    save_trading_dates_cache,
    trading_dates_cache_path,
)


class TradingCalendarTest(unittest.TestCase):
    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            dates = ["2025-01-02", "2025-01-03"]
            self.assertTrue(save_trading_dates_cache(tmp, dates))
            self.assertTrue(os.path.exists(trading_dates_cache_path(tmp)))
            self.assertEqual(load_trading_dates_cache(tmp), dates)

    def test_filter_trading_dates(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        self.assertEqual(
            filter_trading_dates(dates, start_date="2025-01-03", end_date="2025-01-03"),
            ["2025-01-03"],
        )
        self.assertEqual(filter_trading_dates(dates, end_date="2025-01-03"), dates[:2])

    def test_query_trading_dates(self):
        calls = []

        def fake_select(sql):
            calls.append(sql)
            return pd.DataFrame({"date_str": ["2025-01-02 00:00:00", "2025-01-03"]})

        self.assertEqual(query_trading_dates(fake_select, table_name="option_table"), ["2025-01-02", "2025-01-03"])
        self.assertIn("FROM option_table", calls[0])


if __name__ == "__main__":
    unittest.main()
