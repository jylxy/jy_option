import os
import sys
import unittest

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s3_rules import (  # noqa: E402
    select_s3_buy,
    select_s3_buy_by_otm,
    select_s3_protect_by_otm,
    select_s3_sell_by_otm,
)
from strategy_rules import select_s3_buy_by_otm as exported_select_s3_buy_by_otm  # noqa: E402


class S3RulesTest(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {
                "option_code": "P_BUY",
                "option_type": "P",
                "moneyness": 0.95,
                "delta": -0.15,
                "option_close": 6.0,
                "strike": 95.0,
                "volume": 10,
                "open_interest": 100,
            },
            {
                "option_code": "P_SELL",
                "option_type": "P",
                "moneyness": 0.90,
                "delta": -0.08,
                "option_close": 3.0,
                "strike": 90.0,
                "volume": 20,
                "open_interest": 120,
            },
            {
                "option_code": "P_PROTECT",
                "option_type": "P",
                "moneyness": 0.84,
                "delta": -0.03,
                "option_close": 0.8,
                "strike": 84.0,
                "volume": 30,
                "open_interest": 150,
            },
            {
                "option_code": "C_OTHER",
                "option_type": "C",
                "moneyness": 1.05,
                "delta": 0.15,
                "option_close": 5.0,
                "strike": 105.0,
                "volume": 10,
                "open_interest": 100,
            },
        ])

    def test_legacy_delta_buy_selects_target_delta(self):
        row = select_s3_buy(self.rows, "P")
        self.assertEqual(row["option_code"], "P_BUY")

    def test_otm_buy_sell_protect_chain(self):
        buy = select_s3_buy_by_otm(
            self.rows,
            "P",
            100.0,
            target_otm_pct=5.0,
            otm_range=(3.0, 7.0),
        )
        sell = select_s3_sell_by_otm(
            self.rows,
            "P",
            100.0,
            buy["strike"],
            target_otm_pct=10.0,
            otm_range=(7.0, 13.0),
        )
        protect = select_s3_protect_by_otm(
            self.rows,
            "P",
            100.0,
            sell["strike"],
            target_otm_pct=16.0,
            otm_range=(14.0, 18.0),
        )

        self.assertEqual(buy["option_code"], "P_BUY")
        self.assertEqual(sell["option_code"], "P_SELL")
        self.assertEqual(protect["option_code"], "P_PROTECT")

    def test_strategy_rules_reexports_s3_selector(self):
        row = exported_select_s3_buy_by_otm(self.rows, "P", 100.0)
        self.assertEqual(row["option_code"], "P_BUY")


if __name__ == "__main__":
    unittest.main()
