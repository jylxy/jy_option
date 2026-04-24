import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from data_tables import (  # noqa: E402
    ETF_MINUTE_TABLE,
    FUTURE_MINUTE_TABLE,
    OPTION_INFO_TABLE,
    OPTION_MINUTE_TABLE,
)
from runtime_paths import BASE_DIR, CACHE_DIR, CONFIG_PATH, OUTPUT_DIR  # noqa: E402


class RuntimeConfigTest(unittest.TestCase):
    def test_data_table_names(self):
        self.assertEqual(OPTION_INFO_TABLE, "option_basic_info")
        self.assertEqual(OPTION_MINUTE_TABLE, "option_hf_1min_non_ror")
        self.assertEqual(FUTURE_MINUTE_TABLE, "future_hf_1min")
        self.assertEqual(ETF_MINUTE_TABLE, "etf_hf_1min_non_ror")

    def test_runtime_paths_are_repo_relative(self):
        self.assertTrue(BASE_DIR.endswith("src"))
        self.assertEqual(OUTPUT_DIR, os.path.join(BASE_DIR, "..", "output"))
        self.assertEqual(CONFIG_PATH, os.path.join(BASE_DIR, "..", "config.json"))
        self.assertEqual(CACHE_DIR, os.path.join(OUTPUT_DIR, "cache"))


if __name__ == "__main__":
    unittest.main()
