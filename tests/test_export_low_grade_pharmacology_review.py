import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_low_grade_pharmacology_review import (
    REQUIRED_BLOCKS,
    build_review_report,
    read_low_grade_candidates,
    write_json_report,
    write_markdown_report,
)


class ExportLowGradePharmacologyReviewTest(unittest.TestCase):
    def test_export_low_grade_review_reports(self):
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
            root = Path(tmpdir)
            input_path = root / "candidates.jsonl"
            json_path = root / "review.json"
            md_path = root / "review.md"
            input_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

            rows = read_low_grade_candidates(input_path)
            report = build_review_report(rows)
            write_json_report(report, json_path)
            write_markdown_report(report, md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            loaded_report = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(len(rows), 1)
        self.assertEqual(loaded_report["candidate_count"], 1)
        self.assertIn("不得用于 M2", markdown)
        candidate = loaded_report["groups"][0]["candidates"][0]
        self.assertTrue(REQUIRED_BLOCKS.issubset(set(candidate["blocked_from"])))


if __name__ == "__main__":
    unittest.main()
