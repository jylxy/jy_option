import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_contract_scoring import (  # noqa: E402
    calc_s1_stress_loss,
    s1_forward_vega_quality_filter,
    select_s1_sell,
)


class S1ContractScoringTest(unittest.TestCase):
    def test_select_s1_sell_liquidity_mode_is_deterministic(self):
        rows = pd.DataFrame([
            {
                "option_code": "DELTA_CLOSE",
                "option_type": "P",
                "moneyness": 0.94,
                "delta": -0.070,
                "option_close": 0.80,
                "spot_close": 100.0,
                "strike": 94.0,
                "volume": 10,
                "open_interest": 10,
                "gamma": 0.0010,
                "vega": 0.020,
                "theta": -0.010,
                "exchange": "SHFE",
                "product": "CU",
            },
            {
                "option_code": "LIQUID",
                "option_type": "P",
                "moneyness": 0.92,
                "delta": -0.040,
                "option_close": 0.80,
                "spot_close": 100.0,
                "strike": 92.0,
                "volume": 1000,
                "open_interest": 2000,
                "gamma": 0.0010,
                "vega": 0.020,
                "theta": -0.010,
                "exchange": "SHFE",
                "product": "CU",
            },
        ])

        selected = select_s1_sell(
            rows,
            "P",
            mult=10,
            mr=0.07,
            target_abs_delta=0.07,
            ranking_mode="liquidity_oi",
            exchange="SHFE",
            product="CU",
        )

        self.assertEqual(selected["option_code"], "LIQUID")

    def test_forward_vega_filter_blocks_contract_iv_rise(self):
        candidates = pd.DataFrame([
            {"option_code": "GOOD", "contract_iv": 0.24, "contract_iv_change_5d": -0.020},
            {"option_code": "BAD", "contract_iv": 0.28, "contract_iv_change_5d": 0.010},
        ])

        filtered, stats = s1_forward_vega_quality_filter(
            candidates,
            "C",
            iv_state={"atm_iv": 0.20, "iv_trend": -0.010, "rv_trend": -0.002},
            side_meta={},
            config={
                "s1_forward_vega_filter_enabled": True,
                "s1_forward_vega_contract_iv_lookback": 5,
                "s1_forward_vega_contract_iv_max_change": 0.0,
            },
        )

        self.assertEqual(filtered["option_code"].tolist(), ["GOOD"])
        self.assertEqual(int(stats["skip_forward_vega_contract_iv"]), 1)

    def test_stress_loss_uses_premium_tail_floor(self):
        loss = calc_s1_stress_loss(
            {"option_close": 100.0, "spot_close": 1000.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0},
            "P",
            mult=2,
            premium_loss_multiple=5.0,
        )

        self.assertEqual(loss, 1000.0)


if __name__ == "__main__":
    unittest.main()
