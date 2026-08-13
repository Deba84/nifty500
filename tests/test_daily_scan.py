import unittest

from daily_scan import _balanced_select, _entry_line, apply_rule_caps, calculate_breadth


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
        self.assertEqual(breadth["pre_sweep_count"], 6)
        self.assertEqual(breadth["post_sweep_pending_count"], 0)

    def test_very_close_pre_sweep_is_labelled_armed(self):
        item = {"signal_state": "PRE_SWEEP", "stage": "VERY CLOSE", "final_status": "WATCH"}
        self.assertIn("PRE-SWEEP ARMED", _entry_line(item))
        item["stage"] = "CLOSE"
        self.assertIn("EARLY WATCH", _entry_line(item))


class RuleCapTests(unittest.TestCase):
    def test_major_flag_blocks_prime_watch(self):
        item = {
            "setup_score": 90,
            "signal_state": "PRE_SWEEP",
            "quality_flags": ["COUNTER_WEEKLY_STRUCTURE"],
            "confirmation": {},
        }
        result = apply_rule_caps(item)
        self.assertEqual(result["rule_score"], 84)
        self.assertEqual(result["base_status"], "WATCH")

    def test_weak_post_sweep_confirmation_is_wait(self):
        item = {
            "setup_score": 91,
            "signal_state": "RECLAIMED_WAIT_CHOCH",
            "quality_flags": [],
            "confirmation": {"confirmation_score": 38},
        }
        result = apply_rule_caps(item)
        self.assertEqual(result["rule_score"], 74)
        self.assertEqual(result["base_status"], "WAIT")
        self.assertIn("LOW_CONFIRMATION", result["rule_reasons"])

    def test_partial_post_sweep_confirmation_is_watch(self):
        item = {
            "setup_score": 91,
            "signal_state": "RECLAIMED_WAIT_CHOCH",
            "quality_flags": [],
            "confirmation": {"confirmation_score": 57},
        }
        result = apply_rule_caps(item)
        self.assertEqual(result["rule_score"], 84)
        self.assertEqual(result["base_status"], "WATCH")


if __name__ == "__main__":
    unittest.main()
      
