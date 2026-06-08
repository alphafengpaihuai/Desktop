"""final_status 全流程安全闸门。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from full_pipeline import compute_final_status


class TestFinalStatusGate(unittest.TestCase):

    def test_final_status_review_when_m2_need_human_review(self):
        m2 = {
            "status": "PASS",
            "need_human_review": True,
            "posterior_band": "MEDIUM",
            "formula_intervention_check": {"formula_causal_match": "PASS"},
        }
        m3 = {"review_decision": "APPROVED", "review_passed": True}
        self.assertEqual(compute_final_status(m2, m3), "REVIEW")

    def test_final_status_review_when_posterior_band_low_even_m3_approved(self):
        m2 = {
            "status": "PASS",
            "need_human_review": False,
            "posterior_band": "LOW",
            "selected_syndrome_node": {"posterior_band": "LOW"},
            "formula_intervention_check": {"formula_causal_match": "PASS"},
        }
        m3 = {"review_decision": "APPROVED", "review_passed": True}
        self.assertEqual(compute_final_status(m2, m3), "REVIEW")

    def test_final_status_pass_only_when_m2_ok_and_m3_approved(self):
        m2 = {
            "status": "PASS",
            "need_human_review": False,
            "posterior_band": "MEDIUM",
            "formula_intervention_check": {"formula_causal_match": "PASS"},
        }
        m3 = {"review_decision": "APPROVED", "review_passed": True}
        self.assertEqual(compute_final_status(m2, m3), "PASS")

    def test_final_status_blocked_when_m2_no_candidate(self):
        m2 = {"status": "NO_CANDIDATE", "no_candidate": True}
        m3 = {"review_decision": "APPROVED"}
        self.assertEqual(compute_final_status(m2, m3), "BLOCKED")

    def test_final_status_blocked_when_m3_blocked(self):
        m2 = {
            "status": "PASS",
            "need_human_review": False,
            "posterior_band": "HIGH",
            "formula_intervention_check": {"formula_causal_match": "PASS"},
        }
        m3 = {"review_decision": "BLOCKED"}
        self.assertEqual(compute_final_status(m2, m3), "BLOCKED")


if __name__ == "__main__":
    unittest.main()
