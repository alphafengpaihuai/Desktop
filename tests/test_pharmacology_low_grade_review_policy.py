import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "pharmacology_low_grade_review_policy.md"


class PharmacologyLowGradeReviewPolicyTest(unittest.TestCase):
    def test_policy_document_exists_and_contains_safety_boundaries(self):
        self.assertTrue(POLICY_PATH.exists())
        text = POLICY_PATH.read_text(encoding="utf-8")

        required_phrases = [
            "不属于正式药理库",
            "不得用于 M2",
            "处方推荐",
            "人工复核",
            "upgrade_candidate",
            "still_blocked_from",
            "癌症体外实验不得直接升级为 clinical high",
            "网络药理/分子对接不得单独升级",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
