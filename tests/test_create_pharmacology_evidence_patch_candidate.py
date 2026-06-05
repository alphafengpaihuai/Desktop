import json
import tempfile
import unittest
from pathlib import Path

from scripts.create_pharmacology_evidence_patch_candidate import (
    REQUIRED_BLOCKED_FROM,
    append_patch_candidate,
    create_patch_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CACHE_PATH = ROOT / "data" / "disease_pharmacology_cache_rebuilt.json"


def low_grade_candidate(**overrides):
    row = {
        "disease": "乳腺癌",
        "en_name": "Breast cancer",
        "candidate_term": "bruceine D",
        "term_type": "component",
        "mapped_herb": "鸦胆子",
        "evidence_grade": "low_medium",
        "chain_status": "closed",
        "matched_pmids": ["38043386"],
        "matched_titles": ["Bruceine D suppresses CAF-promoted TNBC metastasis."],
        "matched_targets": ["TNF", "IL6"],
        "matched_outcomes": ["suppress", "inhibit"],
        "evidence_context": "cell",
        "why_not_formal": "Mechanism-only evidence.",
        "allowed_usage": "manual_review_only",
        "blocked_from": sorted(REQUIRED_BLOCKED_FROM),
        "source_file": "test",
        "review_status": "pending_human_review",
    }
    row.update(overrides)
    return row


def review_decision(**overrides):
    row = {
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
        "why": "Evidence chain is closed but low grade.",
        "allowed_next_step": "evidence_patch_candidate",
        "still_blocked_from": sorted(REQUIRED_BLOCKED_FROM),
        "example": False,
    }
    row.update(overrides)
    return row


def make_patch(candidate=None, decision=None, proposed_grade="low_medium", reason="Closed low-grade chain."):
    return create_patch_candidate(
        low_grade_candidate=candidate or low_grade_candidate(),
        review_decision=decision or review_decision(),
        patch_candidate_id="patch-test-001",
        proposed_evidence_grade=proposed_grade,
        upgrade_reason=reason,
    )


class CreatePharmacologyEvidencePatchCandidateTest(unittest.TestCase):
    def test_valid_upgrade_candidate_can_generate_patch(self):
        patch = make_patch()
        self.assertEqual(patch["allowed_usage"], "evidence_patch_review_only")
        self.assertEqual(patch["approval_status"], "pending_second_review")
        self.assertTrue(REQUIRED_BLOCKED_FROM.issubset(set(patch["blocked_from"])))

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "patch_candidates.jsonl"
            append_patch_candidate(patch, output)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["proposed_evidence_grade"], "low_medium")

    def test_keep_low_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            make_patch(decision=review_decision(decision="keep_low", allowed_next_step="stay_quarantine"))

    def test_high_proposed_grade_is_rejected(self):
        with self.assertRaises(ValueError):
            make_patch(proposed_grade="high")

    def test_missing_pmid_is_rejected(self):
        with self.assertRaises(ValueError):
            make_patch(candidate=low_grade_candidate(matched_pmids=[]))

    def test_missing_upgrade_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            make_patch(reason="")

    def test_incomplete_blocked_from_is_rejected(self):
        incomplete = sorted(REQUIRED_BLOCKED_FROM - {"prescription_generation"})
        with self.assertRaises(ValueError):
            make_patch(decision=review_decision(still_blocked_from=incomplete))

    def test_patch_still_blocks_formal_m2_and_prescription(self):
        patch = make_patch()
        self.assertEqual(patch["allowed_usage"], "evidence_patch_review_only")
        self.assertTrue(REQUIRED_BLOCKED_FROM.issubset(set(patch["blocked_from"])))

    def test_script_does_not_write_formal_pharmacology_cache(self):
        before = FORMAL_CACHE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            append_patch_candidate(make_patch(), Path(tmpdir) / "patch_candidates.jsonl")
        after = FORMAL_CACHE_PATH.read_text(encoding="utf-8")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
