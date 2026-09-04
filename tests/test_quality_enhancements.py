import unittest

import numpy as np
import pandas as pd

from quality_enhancements import build_market_context, execution_quality


def frame(close, volume=100000):
    values = np.asarray(close, dtype=float)
    index = pd.bdate_range("2025-01-01", periods=len(values))
    return pd.DataFrame(
        {
            "Open": values,
            "High": values * 1.01,
            "Low": values * 0.99,
            "Close": values,
            "Volume": volume,
        },
        index=index,
    )


class QualityEnhancementTests(unittest.TestCase):
    def test_market_context_reports_breadth(self):
        dfs = {
            "A.NS": frame(np.linspace(100, 140, 80)),
            "B.NS": frame(np.linspace(140, 100, 80)),
        }
        context = build_market_context(
            dfs,
            {
                "A.NS": {"sector": "IT"},
                "B.NS": {"sector": "Banks"},
            },
        )
        self.assertEqual(context["sample_size"], 2)
        self.assertIn(context["breadth"]["trend"], {"BULLISH", "BEARISH", "MIXED"})
        self.assertIn("IT", context["sector_breadth"])

    def test_gap_and_low_volume_are_flagged(self):
        df = frame(np.linspace(100, 110, 80), volume=1000)
        df.iloc[-1, df.columns.get_loc("Open")] = 120
        features, penalty, flags, eligible = execution_quality(df, "LONG")
        self.assertTrue(eligible)
        self.assertGreater(penalty, 0)
        self.assertIn("GAP_RISK", flags)
        self.assertIsNotNone(features["atr_14_pct"])


if __name__ == "__main__":
    unittest.main()