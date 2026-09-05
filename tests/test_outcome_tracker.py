import tempfile
import unittest
from pathlib import Path

import pandas as pd

from outcome_tracker import update_ledger


class OutcomeTrackerTests(unittest.TestCase):
    def test_same_bar_collision_is_ambiguous(self):
        dates = pd.bdate_range("2025-01-01", periods=4)
        df = pd.DataFrame(
            {
                "Open": [100, 100, 100, 100],
                "High": [101, 101, 110, 101],
                "Low": [99, 99, 90, 99],
                "Close": [100, 100, 100, 100],
                "Volume": [1000] * 4,
            },
            index=dates,
        )
        item = {
            "symbol": "TEST",
            "sector": "Test",
            "data_as_of": "2025-01-01",
            "direction": "LONG",
            "signal_state": "TRIGGER_READY",
            "final_status": "WATCH",
            "final_score": 80,
            "entry_ref": 100,
            "sl": 95,
            "tp1": 105,
            "tp2": 110,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            summary = update_ledger(path, [item], {"TEST.NS": df}, dates[0].date())
            self.assertEqual(summary["signals_total"], 1)
            payload = path.read_text(encoding="utf-8")
            self.assertIn("AMBIGUOUS_SAME_BAR", payload)


if __name__ == "__main__":
    unittest.main()