import json
import tempfile
import unittest
from pathlib import Path

from scripts.record_low_grade_review_decision import (
    REQUIRED_BLOCKED_FROM,
    append_review_decision,
    validate_review_decision,
)


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CACHE_PATH = ROOT / "data" / "disease_pharmacology_cache_rebuilt.json"


def valid_record(**overrides):
    record = {
        "decision_id": "decision-test-001",
        "source_candidate_id": "low-grade-test-001",
        "disease": "乳腺癌",
        "en_name": "Breast cancer",
        "candidate_term": "bruceine D",
        "mapped_herb": "鸦胆子",
        "original_evidence_grade": "low_medium",
        "reviewer": "unit_test_reviewer",
        "review_date": "2026-06-03",
        "decision": "upgrade_candidate",
        "why": "Mechanism-only evidence remains quarantined.",
        "allowed_next_step": "evidence_patch_candidate",
        "still_blocked_from": sorted(REQUIRED_BLOCKED_FROM),
        "example": False,
    }
    record.update(overrides)
    return record


class RecordLowGradeReviewDecisionTest(unittest.TestCase):
    def test_valid_decision_can_write_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "decisions.jsonl"
            append_review_decision(valid_record(), output)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "upgrade_candidate")
        self.assertTrue(REQUIRED_BLOCKED_FROM.issubset(set(rows[0]["still_blocked_from"])))

    def test_invalid_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_review_decision(valid_record(decision="approve_formal"))

    def test_missing_required_block_is_rejected(self):
        missing_prescription_block = sorted(REQUIRED_BLOCKED_FROM - {"prescription_generation"})
        with self.assertRaises(ValueError):
            validate_review_decision(valid_record(still_blocked_from=missing_prescription_block))

    def test_upgrade_candidate_still_blocks_prescription_generation(self):
        record = valid_record(decision="upgrade_candidate", allowed_next_step="evidence_patch_candidate")
        validate_review_decision(record)
        self.assertIn("prescription_generation", record["still_blocked_from"])

    def test_script_does_not_write_formal_pharmacology_cache(self):
        before = FORMAL_CACHE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            append_review_decision(valid_record(), Path(tmpdir) / "decisions.jsonl")
        after = FORMAL_CACHE_PATH.read_text(encoding="utf-8")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
