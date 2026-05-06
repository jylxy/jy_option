import os
import sys
import unittest
from collections import defaultdict
from types import ModuleType

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import portfolio_risk as pr  # noqa: E402

toolkit_module = ModuleType("toolkit")
selector_module = ModuleType("toolkit.selector")
selector_module.select_bars_sql = lambda *args, **kwargs: None
toolkit_module.selector = selector_module
sys.modules.setdefault("toolkit", toolkit_module)
sys.modules.setdefault("toolkit.selector", selector_module)

from toolkit_minute_engine import ToolkitMinuteEngine  # noqa: E402


class ToolkitMinuteEngineCacheTest(unittest.TestCase):
    def _engine(self):
        engine = ToolkitMinuteEngine.__new__(ToolkitMinuteEngine)
        engine.config = {
            "portfolio_corr_window": 4,
            "portfolio_corr_min_periods": 2,
        }
        engine._spot_history = defaultdict(lambda: {"dates": [], "spots": []})
        engine._spot_history["CU"] = {
            "dates": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            "spots": [100.0, 101.0, 99.0, 102.0],
        }
        engine._spot_history["AL"] = {
            "dates": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            "spots": [50.0, 50.5, 50.0, 51.0],
        }
        engine._product_return_series_cache = {}
        engine._recent_product_corr_cache = {}
        return engine

    def test_cached_return_series_matches_portfolio_helper(self):
        engine = self._engine()

        actual = engine._get_product_return_series("cu", current_date="2025-01-04")
        expected = pr.product_return_series(
            engine._spot_history,
            engine._normalize_product_key,
            "CU",
            current_date="2025-01-04",
        )

        pd.testing.assert_series_equal(actual, expected)
        self.assertEqual(len(engine._product_return_series_cache), 1)
        self.assertIs(actual, engine._get_product_return_series("CU", current_date="2025-01-04"))

    def test_cached_recent_corr_matches_portfolio_helper(self):
        engine = self._engine()

        actual = engine._get_recent_product_corr("CU", "AL", "2025-01-04")
        expected = pr.recent_product_corr(
            engine.config,
            engine._spot_history,
            engine._normalize_product_key,
            "CU",
            "AL",
            "2025-01-04",
        )

        self.assertTrue(np.isfinite(actual))
        self.assertAlmostEqual(actual, expected)
        self.assertEqual(len(engine._recent_product_corr_cache), 1)


if __name__ == "__main__":
    unittest.main()
