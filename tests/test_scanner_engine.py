import unittest

import numpy as np
import pandas as pd

from scanner_engine import (
    approach_features,
    calculate_reference_trade_plan,
    detect_price_action_confirmation,
    first_sweep_index,
    stage_from_distance,
    status_from_score,
)


def frame_from_close(close):
    close = np.asarray(close, dtype=float)
    index = pd.bdate_range("2026-01-01", periods=len(close))
    return pd.DataFrame(
        {
            "Open": close + 0.10,
            "High": close + 0.50,
            "Low": close - 0.50,
            "Close": close,
            "Volume": 1000,
        },
        index=index,
    )


class LiquidityHistoryTests(unittest.TestCase):
    def test_old_sweep_is_detected_not_only_last_15_bars(self):
        df = frame_from_close([95.0] * 50)
        df.iloc[10, df.columns.get_loc("High")] = 101.0
        level = {"side": "BSL", "target_price": 100.0, "formation_idx": 2}
        self.assertEqual(first_sweep_index(df, level), 10)

    def test_formation_candle_is_not_a_false_sweep(self):
        df = frame_from_close([95.0] * 30)
        df.iloc[10, df.columns.get_loc("High")] = 102.0
        level = {"side": "BSL", "target_price": 100.0, "formation_idx": 10}
        self.assertIsNone(first_sweep_index(df, level))


class ApproachTests(unittest.TestCase):
    def test_ssl_approach_is_valid_when_gap_closes(self):
        df = frame_from_close([106, 105.5, 105, 104.5, 104, 103.5, 103, 102.5])
        level = {
            "side": "SSL",
            "target_price": 101.5,
            "prior_near_test_episodes": 0,
        }
        result = approach_features(df, level)
        self.assertTrue(result["valid"])
        self.assertTrue(result["moving_toward"])
        self.assertEqual(result["directional_closes_3"], 3)

    def test_nearby_but_moving_away_is_rejected(self):
        df = frame_from_close([101.6, 101.7, 101.8, 101.9, 102.0, 102.1, 102.2, 102.3])
        level = {
            "side": "SSL",
            "target_price": 101.5,
            "prior_near_test_episodes": 0,
        }
        result = approach_features(df, level)
        self.assertFalse(result["moving_toward"])
        self.assertFalse(result["valid"])

    def test_status_boundaries(self):
        self.assertEqual(status_from_score(85), "PRIME_WATCH")
        self.assertEqual(status_from_score(84), "WATCH")
        self.assertEqual(status_from_score(75), "WATCH")
        self.assertEqual(status_from_score(74), "WAIT")
        self.assertEqual(status_from_score(65), "WAIT")
        self.assertEqual(status_from_score(64), "SKIP")
        self.assertEqual(stage_from_distance(0.30)[0], "AT LEVEL")
        self.assertEqual(stage_from_distance(0.80)[0], "VERY CLOSE")


class PriceActionConfirmationTests(unittest.TestCase):
    def _confirmation_frame(self):
        close = np.array([98.0] * 30)
        df = frame_from_close(close)
        # Create a confirmed internal swing high at bar 20.
        for i, high in {
            17: 97.5, 18: 98.0, 19: 98.5, 20: 100.0,
            21: 98.8, 22: 98.4, 23: 98.0,
        }.items():
            df.iloc[i, df.columns.get_loc("High")] = high
        # SSL sweep and same-candle reclaim at bar 25.
        df.iloc[25] = [96.5, 97.0, 94.5, 96.0, 1000]
        # Bullish displacement.
        df.iloc[26] = [96.0, 98.5, 95.8, 98.2, 1000]
        df.iloc[27] = [98.0, 98.8, 97.7, 98.4, 1000]
        df.iloc[28] = [98.4, 99.2, 98.1, 99.0, 1000]
        # Latest candle breaks the internal swing high (CHoCH).
        df.iloc[29] = [99.0, 101.5, 98.8, 101.0, 1000]
        return df

    def test_long_sweep_reclaim_displacement_and_choch(self):
        df = self._confirmation_frame()
        level = {
            "side": "SSL",
            "target_price": 95.0,
            "zone_low": 95.0,
            "zone_high": 95.2,
            "sweep_idx": 25,
        }
        result = detect_price_action_confirmation(df, level)
        self.assertEqual(result["state"], "TRIGGER_READY")
        self.assertTrue(result["reclaimed"])
        self.assertTrue(result["displacement"])
        self.assertTrue(result["choch_confirmed"])
        self.assertEqual(result["choch_idx"], 29)


class TradeGeometryTests(unittest.TestCase):
    def test_reference_rr_uses_conservative_entry(self):
        target = {
            "side": "SSL",
            "target_price": 100.0,
            "active": True,
            "primary_type": "EQUAL_LOWS",
        }
        levels = [
            target,
            {"side": "BSL", "target_price": 106.0, "active": True, "primary_type": "SWING_HIGH"},
            {"side": "BSL", "target_price": 110.0, "active": True, "primary_type": "52W_HIGH"},
        ]
        plan = calculate_reference_trade_plan(target, levels, current_price=101.0)
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan["entry_ref"], 100.2, places=2)
        self.assertGreaterEqual(plan["rr1"], 2.0)
        self.assertTrue(plan["tp2_structural"])


if __name__ == "__main__":
    unittest.main()
  
