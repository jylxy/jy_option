import os
import sys
import tempfile
import unittest
from collections import defaultdict

import numpy as np
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from iv_warmup import (  # noqa: E402
    attach_warmup_contract_columns,
    build_daily_warmup_iv,
    fill_missing_spot_with_pcp,
    get_warmup_contract_codes,
    load_iv_warmup_cache,
    save_iv_warmup_cache,
)


class FakeContractInfo:
    def __init__(self):
        self._codes = {
            "CU": ["CU_C_100", "CU_P_100", "CU_BAD", "CU_FAR"],
            "AU": ["AU_C_100"],
        }
        self._cache = {
            "CU_C_100": {
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "C",
                "product_root": "CU",
                "underlying_code": "CU2506.SHF",
            },
            "CU_P_100": {
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "P",
                "product_root": "CU",
                "underlying_code": "CU2506.SHF",
            },
            "CU_BAD": {
                "expiry_date": "not-a-date",
                "strike": 100.0,
                "option_type": "C",
                "product_root": "CU",
                "underlying_code": "CU2506.SHF",
            },
            "CU_FAR": {
                "expiry_date": "2026-01-20",
                "strike": 100.0,
                "option_type": "P",
                "product_root": "CU",
                "underlying_code": "CU2506.SHF",
            },
            "AU_C_100": {
                "expiry_date": "2025-06-20",
                "strike": 0.0,
                "option_type": "C",
                "product_root": "AU",
                "underlying_code": "AU2506.SHF",
            },
        }

    def get_product_codes(self, product):
        return self._codes.get(product, [])

    def lookup(self, code):
        return self._cache.get(code)


class IVWarmupTest(unittest.TestCase):
    def test_get_warmup_contract_codes_uses_expiry_window_and_cache(self):
        ci = FakeContractInfo()
        cache = {}

        codes = get_warmup_contract_codes(
            ["CU"],
            ["2025-05-06", "2025-05-30"],
            ci,
            max_dte=60,
            cache=cache,
        )

        self.assertEqual(codes, ["CU_C_100", "CU_P_100"])
        self.assertEqual(
            get_warmup_contract_codes(["CU"], ["2025-05-06", "2025-05-30"], ci, max_dte=60, cache=cache),
            codes,
        )

    def test_attach_warmup_contract_columns_drops_invalid_metadata(self):
        ci = FakeContractInfo()
        raw = pd.DataFrame([
            {"ths_code": "CU_C_100", "last_close": 1.0},
            {"ths_code": "AU_C_100", "last_close": 1.0},
            {"ths_code": "UNKNOWN", "last_close": 1.0},
        ])

        out = attach_warmup_contract_columns(raw, ci._cache)

        self.assertEqual(out["ths_code"].tolist(), ["CU_C_100"])
        self.assertEqual(out["product"].iloc[0], "CU")
        self.assertEqual(out["underlying_code"].iloc[0], "CU2506.SHF")

    def test_fill_missing_spot_with_pcp_does_not_invent_spot_without_pair(self):
        src = pd.DataFrame([
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "C",
                "last_close": 6.0,
                "total_volume": 10,
                "spot": np.nan,
            },
        ])

        out, pcp = fill_missing_spot_with_pcp(src, risk_free_rate=0.0)

        self.assertTrue(pcp.empty)
        self.assertTrue(pd.isna(out["spot"].iloc[0]))

    def test_fill_missing_spot_with_pcp_fills_only_valid_pairs(self):
        src = pd.DataFrame([
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "C",
                "last_close": 6.0,
                "total_volume": 10,
                "spot": np.nan,
            },
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "P",
                "last_close": 4.0,
                "total_volume": 8,
                "spot": np.nan,
            },
        ])

        out, pcp = fill_missing_spot_with_pcp(src, risk_free_rate=0.0)

        self.assertEqual(len(pcp), 1)
        self.assertTrue((out["spot"] == 102.0).all())

    def test_build_daily_warmup_iv_uses_calc_batch_result(self):
        src = pd.DataFrame([
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 100.0,
                "option_type": "C",
                "last_close": 6.0,
                "spot": 100.0,
            },
            {
                "trade_date": "2025-05-06",
                "product": "CU",
                "expiry_date": "2025-06-20",
                "strike": 101.0,
                "option_type": "P",
                "last_close": 5.0,
                "spot": 100.0,
            },
        ])

        daily, n_atm = build_daily_warmup_iv(src, lambda _df, **_kwargs: pd.Series([0.2, 0.4]))

        self.assertEqual(n_atm, 2)
        self.assertEqual(len(daily), 1)
        self.assertAlmostEqual(daily["iv"].iloc[0], 0.3)

    def test_warmup_cache_roundtrip(self):
        iv_history = defaultdict(lambda: {"dates": [], "ivs": []})
        spot_history = defaultdict(lambda: {"dates": [], "spots": []})
        iv_history["CU"] = {"dates": ["2025-05-06"], "ivs": [0.2]}
        spot_history["CU"] = {"dates": ["2025-05-06"], "spots": [80000.0]}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "iv_warmup_cache.json")
            self.assertTrue(save_iv_warmup_cache(path, "2025-05-30", iv_history, spot_history, n_days=10))

            loaded_iv = defaultdict(lambda: {"dates": [], "ivs": []})
            loaded_spot = defaultdict(lambda: {"dates": [], "spots": []})
            cached = load_iv_warmup_cache(path, {"CU", "AU"}, loaded_iv, loaded_spot, "2025-05-20")

        self.assertEqual(cached, {"CU"})
        self.assertEqual(loaded_iv["CU"]["ivs"], [0.2])
        self.assertEqual(loaded_spot["CU"]["spots"], [80000.0])

    def test_warmup_cache_roundtrip_with_skipped_products(self):
        iv_history = defaultdict(lambda: {"dates": [], "ivs": []})
        spot_history = defaultdict(lambda: {"dates": [], "spots": []})
        iv_history["CU"] = {"dates": ["2025-05-06"], "ivs": [0.2]}
        spot_history["CU"] = {"dates": ["2025-05-06"], "spots": [80000.0]}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "iv_warmup_cache.json")
            self.assertTrue(save_iv_warmup_cache(
                path,
                "2025-05-30",
                iv_history,
                spot_history,
                n_days=10,
                skipped_products={"BU", "ZC"},
            ))

            loaded_iv = defaultdict(lambda: {"dates": [], "ivs": []})
            loaded_spot = defaultdict(lambda: {"dates": [], "spots": []})
            cached, skipped = load_iv_warmup_cache(
                path,
                {"CU", "AU", "BU", "ZC"},
                loaded_iv,
                loaded_spot,
                "2025-05-20",
                return_skipped=True,
            )

        self.assertEqual(cached, {"CU"})
        self.assertEqual(skipped, {"BU", "ZC"})


if __name__ == "__main__":
    unittest.main()
