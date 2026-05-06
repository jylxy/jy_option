import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from result_output import (  # noqa: E402
    build_nav_snapshot_record,
    roll_position_previous_marks,
    write_backtest_outputs,
    write_nav_progress,
    write_orders_only,
)


class DummyPosition:
    strat = "S1"
    role = "sell"
    cur_price = 1.2
    cur_spot = 100.0
    cur_iv = 0.2
    cur_delta = 0.1
    cur_gamma = 0.01
    cur_vega = 0.2
    cur_theta = -0.01

    def daily_pnl(self):
        return 10.0

    def pnl_attribution(self):
        return {
            "delta_pnl": 1.0,
            "gamma_pnl": 2.0,
            "theta_pnl": 3.0,
            "vega_pnl": 4.0,
            "residual_pnl": 0.0,
        }

    def cur_margin(self):
        return 500.0

    def cash_delta(self):
        return 100.0

    def cash_vega(self):
        return -50.0

    def cash_gamma(self):
        return -10.0


class ResultOutputTest(unittest.TestCase):
    def test_write_backtest_outputs(self):
        nav_df = pd.DataFrame({
            "date": ["2025-01-02", "2025-01-03"],
            "s1_pnl": [10.0, -2.0],
            "s3_pnl": [0.0, 0.0],
            "s4_pnl": [0.0, 0.0],
            "fee": [1.0, 1.0],
            "delta_pnl": [3.0, 2.0],
            "gamma_pnl": [1.0, 1.0],
            "theta_pnl": [4.0, 4.0],
            "vega_pnl": [-1.0, -1.0],
            "residual_pnl": [3.0, -8.0],
        })
        orders_df = pd.DataFrame({"date": ["2025-01-02"], "action": ["open"]})
        diagnostics = [{"date": "2025-01-02", "n_positions": 1}]
        stats = {"total_return": 0.01, "max_drawdown": -0.002}

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_backtest_outputs(
                nav_df,
                orders_df,
                diagnostics,
                stats,
                tag="unit",
                elapsed=12.3,
                output_dir=tmp,
            )
            for path in paths.values():
                self.assertTrue(os.path.exists(path))

            with open(paths["report"], "r", encoding="utf-8") as f:
                report = f.read()
            self.assertIn("# 回测报告 — unit", report)
            self.assertIn("| total_return | 0.0100 |", report)
            self.assertIn("| Theta | 8 |", report)
            self.assertIn("| Residual | -5 |", report)

    def test_empty_nav_still_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_backtest_outputs(
                pd.DataFrame(),
                pd.DataFrame(),
                [],
                {"total_return": 0.0},
                tag="empty",
                elapsed=0,
                output_dir=tmp,
            )
            with open(paths["report"], "r", encoding="utf-8") as f:
                report = f.read()
            self.assertIn("**日期**: N/A", report)

    def test_progress_and_orders_only_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            nav_path = write_nav_progress([{"date": "2025-01-02", "nav": 1.0}], "unit", output_dir=tmp)
            orders_path = write_orders_only(pd.DataFrame({"date": ["2025-01-02"]}), "unit", output_dir=tmp)

            self.assertTrue(os.path.exists(nav_path))
            self.assertTrue(os.path.exists(orders_path))


    def test_build_nav_snapshot_record_and_roll_marks(self):
        pos = DummyPosition()
        record, nav = build_nav_snapshot_record(
            "2025-01-02",
            positions=[pos],
            nav_records=[],
            capital=1_000_000,
            day_realized={"pnl": 5.0, "fee": 1.0, "s1": 5.0, "s3": 0.0, "s4": 0.0},
            day_attr_realized={
                "delta_pnl": 0.5,
                "gamma_pnl": 0.0,
                "theta_pnl": 0.0,
                "vega_pnl": 0.0,
                "residual_pnl": 0.0,
            },
            current_open_budget={"margin_cap": 0.5, "risk_scale": 1.0},
            effective_open_budget={},
            current_vol_regime_counts={"normal_vol": 1},
            current_iv_state={"CU": {"is_structural_low_iv": True}},
            current_portfolio_regime="normal_vol",
            stress_state={"stress_loss": 1000.0},
            s1_shape={"s1_ledet_similarity_score": 42.0},
            config={},
        )

        self.assertEqual(nav, 1_000_014.0)
        self.assertEqual(record["s1_pnl"], 15.0)
        self.assertEqual(record["delta_pnl"], 1.5)
        self.assertEqual(record["structural_low_iv_products"], 1)
        self.assertEqual(record["s1_ledet_similarity_score"], 42.0)

        roll_position_previous_marks([pos])
        self.assertEqual(pos.prev_price, pos.cur_price)
        self.assertEqual(pos.prev_theta, pos.cur_theta)


if __name__ == "__main__":
    unittest.main()
