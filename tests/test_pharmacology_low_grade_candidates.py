import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOW_GRADE_PATH = ROOT / "data" / "pharmacology_low_grade_candidates.jsonl"
FORMAL_CACHE_PATH = ROOT / "data" / "disease_pharmacology_cache_rebuilt.json"

REQUIRED_BLOCKS = {
    "formal_pharmacology_db",
    "m2_formula_candidate",
    "m2_modification_candidate",
    "prescription_generation",
}


def write_low_grade_candidate_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PharmacologyLowGradeCandidatesTest(unittest.TestCase):
    def test_low_grade_candidate_writer_outputs_jsonl(self):
        row = {
            "disease": "乳腺癌",
            "en_name": "Breast cancer",
            "candidate_term": "bruceine D",
            "term_type": "component",
            "mapped_herb": "鸦胆子",
            "evidence_grade": "low_medium",
            "chain_status": "closed",
            "matched_pmids": ["38043386"],
            "matched_titles": [],
            "matched_targets": ["TNF"],
            "matched_outcomes": ["suppress"],
            "evidence_context": "cell",
            "why_not_formal": "Low-grade mechanism evidence only.",
            "allowed_usage": "manual_review_only",
            "blocked_from": sorted(REQUIRED_BLOCKS),
            "source_file": "test",
            "review_status": "pending_human_review",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "low_grade.jsonl"
            write_low_grade_candidate_jsonl(path, [row])
            loaded = read_jsonl(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["evidence_grade"], "low_medium")
        self.assertEqual(loaded[0]["allowed_usage"], "manual_review_only")
        self.assertTrue(REQUIRED_BLOCKS.issubset(set(loaded[0]["blocked_from"])))

    def test_low_grade_candidates_are_manual_review_only(self):
        rows = read_jsonl(LOW_GRADE_PATH)
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            self.assertIn(row["evidence_grade"], {"low", "low_medium", "manual_review"})
            self.assertEqual(row["allowed_usage"], "manual_review_only")
            self.assertTrue(REQUIRED_BLOCKS.issubset(set(row["blocked_from"])))

    def test_low_grade_candidates_not_written_to_formal_cache(self):
        rows = read_jsonl(LOW_GRADE_PATH)
        formal_cache = json.loads(FORMAL_CACHE_PATH.read_text(encoding="utf-8"))
        breast_cancer = formal_cache.get("乳腺癌", {})
        formal_herbs = {
            item.get("herb") if isinstance(item, dict) else str(item)
            for item in breast_cancer.get("evidence_based_herbs", [])
        }
        low_grade_herbs = {row["mapped_herb"] for row in rows}

        self.assertEqual(breast_cancer.get("evidence_confidence"), "Insufficient")
        self.assertFalse(low_grade_herbs.intersection(formal_herbs))


if __name__ == "__main__":
    unittest.main()
