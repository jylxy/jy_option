import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from product_lifecycle import (  # noqa: E402
    coerce_trade_date_str,
    load_first_trade_cache,
    product_first_trade_cache_path,
    product_observation_ready,
    save_first_trade_cache,
    update_first_trade_dates,
    update_first_trade_dates_from_frame,
)


class ProductLifecycleTest(unittest.TestCase):
    def test_coerce_trade_date_str(self):
        self.assertEqual(coerce_trade_date_str("2025/05/06"), "2025-05-06")
        self.assertEqual(coerce_trade_date_str("bad-date"), "")
        self.assertEqual(coerce_trade_date_str(None), "")

    def test_update_first_trade_dates_keeps_earliest_date(self):
        dates = {"CU": "2025-05-02"}
        changed = update_first_trade_dates(dates, ["cu", "au"], "2025-05-01")
        self.assertTrue(changed)
        self.assertEqual(dates["CU"], "2025-05-01")
        self.assertEqual(dates["AU"], "2025-05-01")

    def test_update_first_trade_dates_from_frame(self):
        dates = {}
        df = pd.DataFrame({
            "product": ["cu", "CU", "au"],
            "trade_date": ["2025-05-03", "2025-05-01", "2025-05-02"],
        })
        changed = update_first_trade_dates_from_frame(dates, df)
        self.assertTrue(changed)
        self.assertEqual(dates, {"AU": "2025-05-02", "CU": "2025-05-01"})

    def test_product_observation_ready_months_and_days(self):
        dates = {"CU": "2025-01-15"}
        self.assertFalse(product_observation_ready(dates, "cu", "2025-03-31", observation_months=3))
        self.assertTrue(product_observation_ready(dates, "cu", "2025-04-15", observation_months=3))
        self.assertFalse(product_observation_ready(dates, "cu", "2025-02-13", min_listing_days=30))
        self.assertTrue(product_observation_ready(dates, "cu", "2025-02-14", min_listing_days=30))
        self.assertTrue(product_observation_ready(dates, "zn", "2025-01-16", observation_months=3))

    def test_cache_roundtrip_normalizes_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = product_first_trade_cache_path(tmp)
            self.assertTrue(save_first_trade_cache(path, {"cu": "2025-05-01", "AU": "2025/05/02"}))
            self.assertEqual(load_first_trade_cache(path), {"AU": "2025-05-02", "CU": "2025-05-01"})


if __name__ == "__main__":
    unittest.main()
