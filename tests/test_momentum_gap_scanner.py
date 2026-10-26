"""Tests for the 2-Day Continuous Big Move + Gap Scanner."""

import unittest
import pandas as pd
from momentum_gap_scanner import detect_momentum_gap, format_momentum_alert


class TestMomentumGapScanner(unittest.TestCase):
    def setUp(self):
        self.info = {
            "symbol": "RELIANCE",
            "name": "Reliance Industries Ltd.",
            "sector": "Oil Gas & Consumable Fuels",
        }

    def _create_df(self, prices):
        """Helper to create OHLC DataFrame from list of (Open, High, Low, Close, Volume)."""
        dates = pd.date_range(end="2025-01-10", periods=len(prices), freq="D")
        df = pd.DataFrame(prices, columns=["Open", "High", "Low", "Close", "Volume"], index=dates)
        return df

    def test_bullish_2day_move_with_full_gap_up(self):
        # Day 0: C=100
        # Day 1: O=101, H=104, L=100, C=103 (+3.0%)
        # Day 2: O=105 (Full gap up, L2=105 > H1=104), H=108, L=105, C=107 (+3.88%)
        prices = [
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [101, 104, 100, 103, 1000],
            [105, 108, 105, 107, 2500],
        ]
        df = self._create_df(prices)
        result = detect_momentum_gap(df, self.info, min_day_move=2.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["direction"], "BULLISH")
        self.assertTrue(result["has_gap"])
        self.assertEqual(result["gap_type"], "FULL_GAP_UP")
        self.assertGreater(result["day1_change_pct"], 0)
        self.assertGreater(result["day2_change_pct"], 0)

    def test_bearish_2day_move_with_body_gap_down(self):
        # Day 0: C=100
        # Day 1: O=99, H=100, L=96, C=96 (-4.0%)
        # Day 2: O=94 (Body gap down vs C1=96), H=95, L=90, C=91 (-5.2%)
        prices = [
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [99, 100, 96, 96, 1000],
            [94, 95, 90, 91, 1500],
        ]
        df = self._create_df(prices)
        result = detect_momentum_gap(df, self.info, min_day_move=2.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["direction"], "BEARISH")
        self.assertTrue(result["has_gap"])
        self.assertIn("GAP_DOWN", result["gap_type"])

    def test_opposite_direction_days_are_rejected(self):
        # Day 1: +3%, Day 2: -2.5%
        prices = [
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [101, 104, 100, 103, 1000],
            [103, 103, 99, 100.4, 1000],
        ]
        df = self._create_df(prices)
        result = detect_momentum_gap(df, self.info, min_day_move=2.0)
        self.assertIsNone(result)

    def test_small_moves_are_rejected(self):
        # Day 1: +0.5%, Day 2: +0.5%
        prices = [
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [100, 102, 98, 100, 1000],
            [100, 101, 99.5, 100.5, 1000],
            [100.5, 101.5, 100.3, 101.0, 1000],
        ]
        df = self._create_df(prices)
        result = detect_momentum_gap(df, self.info, min_day_move=2.0, min_cum_move=4.5)
        self.assertIsNone(result)

    def test_format_momentum_alert(self):
        item = {
            "symbol": "RELIANCE",
            "name": "Reliance Industries Ltd.",
            "sector": "Oil Gas & Consumable Fuels",
            "price": 1250.5,
            "direction": "BULLISH",
            "day1_change_pct": 3.2,
            "day2_change_pct": 2.8,
            "two_day_return_pct": 6.09,
            "has_gap": True,
            "gap_type": "FULL_GAP_UP",
            "gap_pct": 1.1,
            "vol_ratio": 2.1,
            "score": 32.18,
            "date": "2025-01-10",
        }
        text = format_momentum_alert(item, 1)
        self.assertIn("RELIANCE", text)
        self.assertIn("FULL GAP UP", text)
        self.assertIn("6.09%", text)
        self.assertIn("TradingView Chart", text)


if __name__ == "__main__":
    unittest.main()
