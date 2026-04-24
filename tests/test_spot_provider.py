import os
import sys
import unittest

import pandas as pd


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from spot_provider import (
    build_pcp_spot_frame,
    build_underlying_alias_map,
    estimate_spot_pcp,
    map_alias_spot_frame,
    resolve_alias_value_map,
    spot_tables_for_codes,
)


class SpotProviderTest(unittest.TestCase):
    def test_underlying_alias_map_prefers_exact_then_continuous(self):
        aliases = build_underlying_alias_map(["BU2506.SHF", "510050.SH"])
        self.assertEqual(aliases["BU2506.SHF"], ["BU2506.SHF", "BUZL.SHF"])
        self.assertEqual(aliases["510050.SH"], ["510050.SH"])

    def test_spot_table_routing(self):
        self.assertEqual(spot_tables_for_codes(["510050.SH", "159915.SZ"]), ["etf_hf_1min_non_ror"])
        self.assertEqual(spot_tables_for_codes(["CU2506.SHF"]), ["future_hf_1min"])
        self.assertEqual(
            spot_tables_for_codes(["CU2506.SHF", "510050.SH"]),
            ["future_hf_1min", "etf_hf_1min_non_ror"],
        )

    def test_map_alias_spot_frame(self):
        alias_map = build_underlying_alias_map(["BU2506.SHF"])
        raw = pd.DataFrame([
            {"ths_code": "BUZL.SHF", "time": "09:31:00", "close": 100.0},
            {"ths_code": "BU2506.SHF", "time": "09:31:00", "close": 101.0},
            {"ths_code": "BUZL.SHF", "time": "09:32:00", "close": 102.0},
        ])
        out = map_alias_spot_frame(
            raw,
            alias_map,
            lookup_col="ths_code",
            value_col="close",
            sort_cols=["time"],
        )
        values = dict(zip(out["time"], out["spot"]))
        self.assertEqual(values["09:31:00"], 101.0)
        self.assertEqual(values["09:32:00"], 102.0)

    def test_resolve_alias_value_map(self):
        alias_map = build_underlying_alias_map(["BU2506.SHF"])
        self.assertEqual(
            resolve_alias_value_map({"BUZL.SHF": 99.0}, alias_map),
            {"BU2506.SHF": 99.0},
        )
        self.assertEqual(
            resolve_alias_value_map({"BU2506.SHF": 101.0, "BUZL.SHF": 99.0}, alias_map),
            {"BU2506.SHF": 101.0},
        )

    def test_estimate_spot_pcp(self):
        group = pd.DataFrame([
            {"option_type": "C", "strike": 100.0, "option_close": 6.0, "volume": 10, "dte": 30},
            {"option_type": "P", "strike": 100.0, "option_close": 4.0, "volume": 8, "dte": 30},
        ])
        self.assertAlmostEqual(estimate_spot_pcp(group, risk_free_rate=0.0), 102.0)

    def test_build_pcp_spot_frame(self):
        src = pd.DataFrame([
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "C",
                "last_close": 6.0,
                "total_volume": 10,
            },
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "P",
                "last_close": 4.0,
                "total_volume": 8,
            },
        ])
        out = build_pcp_spot_frame(src, risk_free_rate=0.0)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out["spot_pcp"].iloc[0], 102.0)


if __name__ == "__main__":
    unittest.main()
