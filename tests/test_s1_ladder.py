import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from toolkit_minute_engine import ToolkitMinuteEngine  # noqa: E402


class S1LadderShapeTest(unittest.TestCase):
    def make_engine(self, config, regimes):
        engine = ToolkitMinuteEngine.__new__(ToolkitMinuteEngine)
        engine.config = config
        engine._current_vol_regimes = regimes
        return engine

    def test_falling_regime_can_widen_ladder_without_changing_normal_regime(self):
        config = {
            "s1_split_across_neighbor_contracts": True,
            "s1_neighbor_contract_count": 3,
            "s1_neighbor_max_delta_gap": 0.025,
            "s1_trend_ladder_enabled": True,
            "s1_trend_ladder_strong_contract_count": 4,
            "s1_trend_ladder_strong_max_delta_gap": 0.035,
            "s1_regime_ladder_enabled": True,
            "vol_regime_falling_s1_ladder_strong_contract_count": 6,
            "vol_regime_falling_s1_ladder_strong_max_delta_gap": 0.045,
        }
        engine = self.make_engine(
            config,
            {"CU": "falling_vol_carry", "AU": "normal_vol"},
        )

        falling_count, falling_gap = engine._s1_ladder_shape(
            {"trend_role": "strong"},
            product="CU",
        )
        normal_count, normal_gap = engine._s1_ladder_shape(
            {"trend_role": "strong"},
            product="AU",
        )

        self.assertEqual(falling_count, 6)
        self.assertAlmostEqual(falling_gap, 0.045)
        self.assertEqual(normal_count, 4)
        self.assertAlmostEqual(normal_gap, 0.035)


if __name__ == "__main__":
    unittest.main()
