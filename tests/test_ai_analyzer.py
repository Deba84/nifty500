import unittest

from ai_analyzer import _review_schema, apply_review, validate_review_payload


class AIValidationTests(unittest.TestCase):
    def stock(self):
        return {
            "symbol": "TEST",
            "setup_score": 85,
            "final_score": 85,
            "rr1": 2.2,
            "fund_score": 3,
            "quality_flags": ["BORDERLINE_RR"],
            "score_breakdown": {"data_integrity": 5},
        }

    def test_supported_caution_is_kept(self):
        payload = {
            "review_version": "v6.2",
            "reviews": [
                {
                    "symbol": "TEST",
                    "action": "CAUTION",
                    "reason_codes": ["BORDERLINE_RR"],
                    "evidence_refs": ["rr1", "setup_score"],
                    "commentary_bn": "TP1 R:R সীমার কাছাকাছি, তাই confirmation কঠোরভাবে যাচাই করুন।",
                    "risks_bn": ["CHoCH ব্যর্থ হলে entry নয়।"],
                }
            ],
            "breadth_commentary_bn": "এটি শুধু scanner setup flow।",
        }
        reviews, _, errors = validate_review_payload(payload, [self.stock()])
        self.assertEqual(reviews["TEST"]["action"], "CAUTION")
        self.assertEqual(errors, [])

    def test_unsupported_caution_cannot_downgrade(self):
        payload = {
            "review_version": "v6.2",
            "reviews": [
                {
                    "symbol": "TEST",
                    "action": "CAUTION",
                    "reason_codes": ["FAST_APPROACH"],
                    "evidence_refs": ["rr1", "setup_score"],
                    "commentary_bn": "সতর্ক থাকুন।",
                    "risks_bn": ["CHoCH দরকার।"],
                }
            ],
            "breadth_commentary_bn": "Scanner flow।",
        }
        reviews, _, errors = validate_review_payload(payload, [self.stock()])
        self.assertEqual(reviews["TEST"]["action"], "KEEP")
        self.assertTrue(any("unsupported caution" in e for e in errors))

    def test_buy_now_claim_is_suppressed(self):
        payload = {
            "review_version": "v6.2",
            "reviews": [
                {
                    "symbol": "TEST",
                    "action": "KEEP",
                    "reason_codes": ["NONE"],
                    "evidence_refs": ["rr1", "setup_score"],
                    "commentary_bn": "BUY NOW — নিশ্চিত লাভ হবে।",
                    "risks_bn": ["কোনো ঝুঁকি নেই।"],
                }
            ],
            "breadth_commentary_bn": "Scanner flow।",
        }
        reviews, _, errors = validate_review_payload(payload, [self.stock()])
        self.assertEqual(reviews["TEST"]["commentary"], "")
        self.assertTrue(any("unsafe" in e for e in errors))

    def test_strict_schema_requires_every_symbol_key(self):
        schema = _review_schema(["AAA", "BBB"])
        reviews_schema = schema["properties"]["reviews"]
        self.assertEqual(set(reviews_schema["required"]), {"AAA", "BBB"})
        self.assertEqual(set(reviews_schema["properties"]), {"AAA", "BBB"})
        self.assertFalse(reviews_schema["additionalProperties"])

    def test_symbol_keyed_payload_validates_all_reviews(self):
        first = self.stock()
        second = dict(self.stock(), symbol="TEST2", rr1=3.0, quality_flags=[])
        payload = {
            "review_version": "v6.2.1",
            "reviews": {
                "TEST": {
                    "symbol": "TEST", "action": "KEEP", "reason_codes": ["NONE"],
                    "evidence_refs": ["rr1", "setup_score"],
                    "commentary_bn": "সরবরাহ করা তথ্য অনুযায়ী setup watch করা যায়।",
                    "risks_bn": ["CHoCH না হলে entry নয়।"],
                },
                "TEST2": {
                    "symbol": "TEST2", "action": "KEEP", "reason_codes": ["NONE"],
                    "evidence_refs": ["rr1", "setup_score"],
                    "commentary_bn": "Liquidity evidence পরিষ্কার, confirmation অপেক্ষা করুন।",
                    "risks_bn": ["Price structure verify করুন।"],
                },
            },
        }
        reviews, breadth, errors = validate_review_payload(payload, [first, second])
        self.assertEqual(set(reviews), {"TEST", "TEST2"})
        self.assertEqual(breadth, "")
        self.assertEqual(errors, [])

    def test_post_sweep_pre_sweep_text_is_suppressed(self):
        stock = dict(self.stock(), signal_state="RECLAIMED_WAIT_CHOCH")
        payload = {
            "review_version": "v6.2.1",
            "reviews": {
                "TEST": {
                    "symbol": "TEST", "action": "KEEP", "reason_codes": ["NONE"],
                    "evidence_refs": ["rr1", "signal_state"],
                    "commentary_bn": "এটি প্রি-সুইপ setup।",
                    "risks_bn": ["Entry নয়।"],
                }
            },
        }
        reviews, _, errors = validate_review_payload(payload, [stock])
        self.assertEqual(reviews["TEST"]["commentary"], "")
        self.assertTrue(any("suppressed" in error for error in errors))

    def test_ai_can_only_apply_five_point_downgrade(self):
        stock = self.stock()
        review = {
            "action": "CAUTION",
            "reason_codes": ["BORDERLINE_RR"],
            "evidence_refs": ["rr1", "setup_score"],
            "commentary": "সতর্কতা।",
            "risks": ["CHoCH প্রয়োজন।"],
        }
        result = apply_review(stock, review)
        self.assertEqual(result["final_score"], 80)
        self.assertEqual(result["final_status"], "WATCH")


if __name__ == "__main__":
    unittest.main()
  
