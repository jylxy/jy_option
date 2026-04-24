import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from result_output import write_backtest_outputs  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
