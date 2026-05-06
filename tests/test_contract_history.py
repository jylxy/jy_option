import os
import sys
import unittest
from collections import defaultdict

import numpy as np
import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from contract_history import (  # noqa: E402
    contract_trend_state_from_history,
    update_contract_iv_history,
)


class ContractHistoryTest(unittest.TestCase):
    def test_update_and_trend_state_match_expected_changes(self):
        history = defaultdict(lambda: {"dates": [], "ivs": [], "prices": []})
        for date, iv, price in [
            ("2025-01-01", 0.20, 10.0),
            ("2025-01-02", 0.22, 11.0),
            ("2025-01-03", 0.21, 10.5),
            ("2025-01-04", 0.25, 12.0),
            ("2025-01-05", 0.24, 13.0),
            ("2025-01-06", 0.27, 14.0),
        ]:
            df = pd.DataFrame([{
                "option_code": "OPT1",
                "implied_vol": iv,
                "option_close": price,
            }])
            self.assertTrue(update_contract_iv_history(history, df, date))

        state = contract_trend_state_from_history(history, "OPT1")

        self.assertAlmostEqual(state["contract_iv"], 0.27)
        self.assertAlmostEqual(state["contract_iv_change_1d"], 0.03)
        self.assertAlmostEqual(state["contract_iv_change_3d"], 0.06)
        self.assertAlmostEqual(state["contract_iv_change_5d"], 0.07)
        self.assertAlmostEqual(state["contract_price"], 14.0)
        self.assertAlmostEqual(state["contract_price_change_1d"], 14.0 / 13.0 - 1.0)
        self.assertAlmostEqual(state["contract_price_change_3d"], 14.0 / 10.5 - 1.0)
        self.assertAlmostEqual(state["contract_price_change_5d"], 14.0 / 10.0 - 1.0)

    def test_invalid_or_missing_history_returns_nan_state(self):
        history = defaultdict(lambda: {"dates": [], "ivs": [], "prices": []})
        bad = pd.DataFrame([{
            "option_code": "OPT1",
            "implied_vol": np.nan,
            "option_close": 10.0,
        }])
        self.assertFalse(update_contract_iv_history(history, bad, "2025-01-01"))

        state = contract_trend_state_from_history(history, "MISSING")
        self.assertTrue(np.isnan(state["contract_iv"]))
        self.assertTrue(np.isnan(state["contract_price_change_1d"]))


if __name__ == "__main__":
    unittest.main()
