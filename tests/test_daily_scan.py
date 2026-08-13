import unittest

from daily_scan import _balanced_select, calculate_breadth


class BalancedBudgetTests(unittest.TestCase):
    def candidate(self, index, state):
        return {
            "symbol": f"S{index}",
            "signal_state": state,
            "technical_score": 80 - (index % 20),
            "final_score": 80 - (index % 20),
            "distance_pct": 0.5,
            "confirmation": {"confirmation_score": 50},
            "direction": "LONG" if index % 2 == 0 else "SHORT",
            "final_status": "WATCH",
            "sector": "Test",
            "score_details": {"htf_context": {"weekly": 6}},
            "data_as_of": "2026-08-13",
        }

    def test_post_sweep_cannot_crowd_out_pre_sweep(self):
        post = [self.candidate(i, "RECLAIMED_WAIT_CHOCH") for i in range(100)]
        pre = [self.candidate(100 + i, "PRE_SWEEP") for i in range(100)]
        selected = _balanced_select(post + pre, total_limit=40, post_limit=12)
        self.assertEqual(len(selected), 40)
        self.assertEqual(sum(x["signal_state"] != "PRE_SWEEP" for x in selected), 12)
        self.assertEqual(sum(x["signal_state"] == "PRE_SWEEP" for x in selected), 28)

    def test_breadth_is_scanner_flow_not_market_bias(self):
        items = [self.candidate(i, "PRE_SWEEP") for i in range(6)]
        breadth = calculate_breadth(items)
        self.assertEqual(breadth["scope"], "scanner candidates; not whole-market direction")
        self.assertEqual(breadth["total_passed"], 6)


if __name__ == "__main__":
    unittest.main()
      
