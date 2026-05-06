import logging
import os
import sys
import tempfile
import unittest

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from s1_shadow_universe import add_b5_shadow_fields, effective_count, hhi, write_b5_candidate_panels  # noqa: E402


class S1ShadowUniverseTest(unittest.TestCase):
    def test_effective_count_and_hhi(self):
        self.assertAlmostEqual(effective_count([1.0, 1.0, 2.0]), 16.0 / 6.0)
        self.assertAlmostEqual(hhi([1.0, 1.0, 2.0]), 0.375)
        self.assertEqual(effective_count([0.0, None]), 0.0)

    def test_write_b5_candidate_panels_creates_expected_outputs(self):
        candidates = pd.DataFrame([
            {
                "signal_date": "2025-05-06",
                "candidate_id": 1,
                "product": "CU",
                "option_type": "P",
                "expiry": "2025-06-20",
                "bucket": "metal",
                "b5_delta_bucket": "0.08_0.10",
                "net_premium_cash_1lot": 100.0,
                "stress_loss": 250.0,
                "margin_estimate": 1000.0,
                "cash_vega": 5.0,
                "cash_gamma": 2.0,
                "cash_theta": 10.0,
                "abs_delta": 0.09,
                "contract_iv_skew_to_atm": 0.01,
                "b4_contract_score": 60.0,
                "b5_theta_per_gamma": 5.0,
                "b5_premium_to_tail_move_loss": 0.5,
                "b5_cooldown_penalty_score": 0.0,
                "b5_delta_ratio_to_cap": 0.9,
                "b5_mom_20d": 0.01,
                "b5_trend_z_20d": 0.2,
                "b5_breakout_distance_up_60d": 0.05,
                "b5_breakout_distance_down_60d": 0.04,
                "b5_atm_iv_mom_5d": -0.01,
                "b5_atm_iv_accel": -0.01,
            }
        ])

        with tempfile.TemporaryDirectory() as tmp:
            write_b5_candidate_panels(
                candidates,
                "unit",
                config={"s1_b5_shadow_factor_extension_enabled": True},
                spot_history={},
                history_series=lambda *_args: pd.Series(dtype=float),
                output_dir=tmp,
                logger=logging.getLogger("test_s1_shadow_universe"),
            )

            expected = [
                "s1_b5_product_panel_unit.csv",
                "s1_b5_product_side_panel_unit.csv",
                "s1_b5_delta_ladder_panel_unit.csv",
                "s1_b5_portfolio_panel_unit.csv",
            ]
            for name in expected:
                self.assertTrue(os.path.exists(os.path.join(tmp, name)), name)

            portfolio = pd.read_csv(os.path.join(tmp, "s1_b5_portfolio_panel_unit.csv"))
            self.assertEqual(int(portfolio.loc[0, "active_product_count"]), 1)
            self.assertAlmostEqual(float(portfolio.loc[0, "top1_product_stress_share"]), 1.0)

    def test_add_b5_shadow_fields_uses_vectorized_loss_metrics(self):
        candidates = pd.DataFrame([
            {
                "option_code": "CU_TEST",
                "abs_delta": 0.09,
                "delta": -0.09,
                "gamma": 0.0002,
                "theta": -2.0,
                "vega": 5.0,
                "option_close": 20.0,
                "multiplier": 5.0,
                "spot_close": 80000.0,
                "dte": 20,
                "rv_ref": 0.20,
                "stress_loss": 100.0,
                "margin": 1000.0,
                "contract_iv": 0.25,
            }
        ])
        dates = pd.date_range("2025-01-01", periods=65, freq="D")
        spots = pd.Series([80000.0 + i for i in range(65)], index=dates)
        ivs = pd.Series(0.20, index=dates)

        def history_series(history, product, value_key):
            values = history[product][value_key]
            dates_ = history[product]["dates"]
            return pd.Series(values, index=pd.to_datetime(dates_))

        out = add_b5_shadow_fields(
            candidates,
            date_str="2025-03-10",
            product="CU",
            option_type="P",
            config={"s1_b5_shadow_factor_extension_enabled": True, "s1_sell_delta_cap": 0.10},
            spot_history={"CU": {"dates": dates.strftime("%Y-%m-%d").tolist(), "spots": spots.tolist()}},
            iv_history={"CU": {"dates": dates.strftime("%Y-%m-%d").tolist(), "ivs": ivs.tolist()}},
            stop_history={},
            stop_side_history={},
            history_series=history_series,
            option_roundtrip_fee=lambda *_args: 1.0,
            normalize_product=lambda product: product,
            is_reentry_blocked=lambda *_args: False,
            last_iv_trend=lambda _product: 0.0,
        )

        self.assertEqual(out.loc[0, "b5_delta_bucket"], "0.08_0.10")
        self.assertGreater(float(out.loc[0, "b5_expected_move_loss_cash"]), 0.0)
        self.assertGreater(float(out.loc[0, "b5_theta_per_vega"]), 0.0)
        self.assertAlmostEqual(float(out.loc[0, "b5_low_price_flag"]), 0.0)


if __name__ == "__main__":
    unittest.main()
