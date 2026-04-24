import os
import sys
import unittest
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from daily_aggregation import (  # noqa: E402
    aggregate_minute_daily,
    attach_contract_columns,
    calc_dte_from_expiry,
    enrich_daily_with_spot_iv_delta,
    normalize_preloaded_daily_agg,
)


class FakeContractInfo:
    def __init__(self):
        self._cache = {
            "CUC": {
                "strike": 100.0,
                "option_type": "C",
                "expiry_date": "2025-06-30",
                "product_root": "CU",
                "exchange": "SHFE",
                "multiplier": 5,
                "underlying_code": "CU2506.SHF",
            },
            "CUP": {
                "strike": 100.0,
                "option_type": "P",
                "expiry_date": "2025-06-30",
                "product_root": "CU",
                "exchange": "SHFE",
                "multiplier": 5,
                "underlying_code": "CU2506.SHF",
            },
            "OLD": {
                "strike": 100.0,
                "option_type": "C",
                "expiry_date": "2025-04-30",
                "product_root": "CU",
                "exchange": "SHFE",
                "multiplier": 5,
                "underlying_code": "CU2504.SHF",
            },
        }

    def lookup(self, code):
        return self._cache.get(code)

    def calc_dte(self, code, current_date):
        expiry = pd.Timestamp(self._cache[code]["expiry_date"]).date()
        return (expiry - current_date).days


class DailyAggregationTest(unittest.TestCase):
    def setUp(self):
        self.ci = FakeContractInfo()

    def test_calc_dte_from_expiry(self):
        self.assertEqual(calc_dte_from_expiry("2025-06-30", date(2025, 6, 1)), 29)
        self.assertEqual(calc_dte_from_expiry("bad", date(2025, 6, 1)), -1)

    def test_attach_contract_columns(self):
        raw = pd.DataFrame({"ths_code": ["CUC", "CUP"]})
        df = attach_contract_columns(raw, self.ci)
        self.assertEqual(df["product"].tolist(), ["CU", "CU"])
        self.assertEqual(df["option_type"].tolist(), ["C", "P"])
        self.assertEqual(df["multiplier"].tolist(), [5.0, 5.0])

    def test_normalize_preloaded_daily_agg_filters_expired_and_renames(self):
        raw = pd.DataFrame({
            "ths_code": ["CUC", "OLD"],
            "last_close": [10.0, 8.0],
            "total_volume": [20, 10],
            "last_oi": [100, 50],
            "strike": [100.0, 100.0],
            "option_type": ["C", "C"],
            "expiry_date": ["2025-06-30", "2025-04-30"],
            "product": ["CU", "CU"],
            "exchange": ["SHFE", "SHFE"],
            "multiplier": [5, 5],
        })
        df = normalize_preloaded_daily_agg(raw, "2025-06-01", self.ci)
        self.assertEqual(df["option_code"].tolist(), ["CUC"])
        self.assertIn("option_close", df.columns)
        self.assertEqual(df.iloc[0]["underlying_code"], "CU2506.SHF")

    def test_aggregate_minute_daily_uses_last_positive_volume_bar(self):
        minute = pd.DataFrame({
            "ths_code": ["CUC", "CUC", "CUP"],
            "time": ["09:01:00", "09:02:00", "09:01:00"],
            "close": [5.0, 6.0, 4.0],
            "volume": [0, 3, 0],
            "open_interest": [10, 12, 8],
        })
        df = aggregate_minute_daily(minute, "2025-06-01", self.ci)
        call = df[df["option_code"] == "CUC"].iloc[0]
        put = df[df["option_code"] == "CUP"].iloc[0]
        self.assertEqual(call["option_close"], 6.0)
        self.assertEqual(call["volume"], 3)
        self.assertEqual(call["open_interest"], 12)
        self.assertEqual(put["option_close"], 4.0)
        self.assertEqual(put["volume"], 0)

    def test_enrich_daily_with_spot_iv_delta_uses_real_spot_and_pcp_fallback(self):
        df = pd.DataFrame({
            "option_code": ["CUC", "CUP"],
            "strike": [100.0, 100.0],
            "option_type": ["C", "P"],
            "option_close": [101.0, 1.0],
            "dte": [30, 30],
            "volume": [1, 1],
            "open_interest": [10, 10],
            "product": ["CU", "CU"],
            "exchange": ["SHFE", "SHFE"],
            "expiry_date": ["2025-06-30", "2025-06-30"],
            "multiplier": [5, 5],
            "underlying_code": ["CU2506.SHF", "CU2506.SHF"],
        })
        enriched = enrich_daily_with_spot_iv_delta(df, spot_map={}, risk_free_rate=0.0)
        self.assertTrue(np.allclose(enriched["spot_close"], 200.0))
        self.assertTrue(np.allclose(enriched["moneyness"], 0.5))
        self.assertTrue(enriched["implied_vol"].isna().all())
        self.assertTrue(enriched["delta"].isna().all())


if __name__ == "__main__":
    unittest.main()
