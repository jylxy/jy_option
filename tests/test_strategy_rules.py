import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from strategy_rules import select_s1_sell  # noqa: E402


class StrategyRulesTest(unittest.TestCase):
    def test_s1_risk_reward_ranking_can_override_target_delta(self):
        rows = pd.DataFrame([
            {
                "option_code": "TARGET",
                "option_type": "P",
                "moneyness": 0.94,
                "delta": -0.070,
                "option_close": 0.60,
                "spot_close": 100.0,
                "strike": 94.0,
                "volume": 100,
                "open_interest": 100,
                "gamma": 0.0010,
                "vega": 0.020,
                "theta": -0.010,
                "exchange": "SHFE",
                "product": "CU",
            },
            {
                "option_code": "RICH",
                "option_type": "P",
                "moneyness": 0.92,
                "delta": -0.040,
                "option_close": 1.20,
                "spot_close": 100.0,
                "strike": 92.0,
                "volume": 100,
                "open_interest": 100,
                "gamma": 0.0005,
                "vega": 0.010,
                "theta": -0.020,
                "exchange": "SHFE",
                "product": "CU",
            },
        ])

        target_delta = select_s1_sell(
            rows,
            "P",
            mult=10,
            mr=0.07,
            target_abs_delta=0.07,
            ranking_mode="target_delta",
            exchange="SHFE",
            product="CU",
        )
        risk_reward = select_s1_sell(
            rows,
            "P",
            mult=10,
            mr=0.07,
            target_abs_delta=0.07,
            ranking_mode="risk_reward",
            exchange="SHFE",
            product="CU",
        )

        self.assertEqual(target_delta["option_code"], "TARGET")
        self.assertEqual(risk_reward["option_code"], "RICH")


if __name__ == "__main__":
    unittest.main()
