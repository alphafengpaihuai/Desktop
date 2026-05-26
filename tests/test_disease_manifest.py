import json
import tempfile
import unittest
from pathlib import Path

import disease_manifest_builder as manifest_builder
import external_disease_knowledge_fetcher as fetcher


FORBIDDEN_TCM = ["证型", "病机", "方剂", "中药", "治法", "治则", "剂量", "处方"]


class DiseaseManifestAndCacheTest(unittest.TestCase):
    def test_extract_manifest_from_excel(self):
        manifest = manifest_builder.build_disease_manifest()
        self.assertGreater(manifest["raw_disease_cell_count"], 0)
        self.assertGreater(manifest["cleaned_disease_count"], 0)
        self.assertTrue(manifest["diseases"])

    def test_manifest_has_no_tcm_formula(self):
        manifest = json.loads(manifest_builder.MANIFEST_PATH.read_text(encoding="utf-8"))
        blob = json.dumps(manifest["diseases"], ensure_ascii=False)
        for term in ["方剂", "中药", "治法", "剂量", "处方"]:
            self.assertNotIn(term, blob)

    def test_alias_and_standard_name_present(self):
        manifest = json.loads(manifest_builder.MANIFEST_PATH.read_text(encoding="utf-8"))
        names = {d["standard_western_name"] for d in manifest["diseases"]}
        self.assertIn("变应性鼻炎", names)
        rhinitis = next(d for d in manifest["diseases"] if d["standard_western_name"] == "变应性鼻炎")
        self.assertIn("过敏性鼻炎", rhinitis["aliases"])

    def test_pending_cards_schema(self):
        fetcher.seed_pending_cards(["急性支气管炎", "变应性鼻炎", "支原体肺炎"])
        cards = json.loads(fetcher.PENDING_PATH.read_text(encoding="utf-8"))
        required = {
            "disease_name", "aliases", "source_raw_names", "department_or_category",
            "entry_type", "m1_diagnosis_entry_allowed", "diagnostic_key_points",
            "rule_in_features", "rule_out_features", "differential_diagnoses",
            "required_checks", "red_flags", "typical_age_or_population",
            "typical_course", "pathology_axes", "must_not_be_primary_when",
            "need_external_check_if", "source_summary", "source_urls",
            "confidence", "status", "created_at", "updated_at",
        }
        seeded_names = {"急性支气管炎", "变应性鼻炎", "支原体肺炎"}
        for card in [c for c in cards if c.get("disease_name") in seeded_names]:
            self.assertTrue(required.issubset(card.keys()), msg=card.get("disease_name"))
            self.assertGreaterEqual(len(card["diagnostic_key_points"]), 3)
            self.assertGreaterEqual(len(card["rule_in_features"]), 3)
            self.assertGreaterEqual(len(card["rule_out_features"]), 2)
            self.assertGreaterEqual(len(card["differential_diagnoses"]), 3)
            self.assertGreaterEqual(len(card["required_checks"]), 2)
            self.assertGreaterEqual(len(card["red_flags"]), 2)
            self.assertGreaterEqual(len(card["pathology_axes"]), 2)
            self.assertLessEqual(len(card["pathology_axes"]), 6)
            self.assertTrue(card["source_urls"] or card["source_summary"])

    def test_no_tcm_content_in_pending_cards(self):
        cards = json.loads(fetcher.PENDING_PATH.read_text(encoding="utf-8"))
        blob = json.dumps(cards, ensure_ascii=False)
        for term in FORBIDDEN_TCM:
            self.assertNotIn(term, blob)

    def test_cache_reuse(self):
        before = _trace_count(fetcher.TRACE_PATH)
        first = fetcher.fetch_or_get_cached_disease_card("急性支气管炎")
        second = fetcher.fetch_or_get_cached_disease_card("急性支气管炎")
        after = _trace_count(fetcher.TRACE_PATH)
        self.assertEqual(first["disease_name"], second["disease_name"])
        self.assertFalse(second["_external_fetch_triggered"])
        self.assertEqual(before, after)

    def test_external_fetch_trace_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _patch_fetcher_paths(Path(tmp))
            try:
                card = fetcher.fetch_or_get_cached_disease_card(
                    "急性鼻炎",
                    missing_fields=["diagnostic_key_points", "rule_in_features"],
                    trigger_reason="unit_test_trace",
                )
                self.assertTrue(card["_external_fetch_triggered"])
                lines = fetcher.TRACE_PATH.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                event = json.loads(lines[0])
                self.assertEqual(event["disease_name"], "急性鼻炎")
            finally:
                _restore_fetcher_paths(old)


def _trace_count(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    return len(text.splitlines()) if text else 0


def _patch_fetcher_paths(root: Path):
    old = {
        "CACHE_DIR": fetcher.CACHE_DIR,
        "CONFIRMED_PATH": fetcher.CONFIRMED_PATH,
        "PENDING_PATH": fetcher.PENDING_PATH,
        "RUNTIME_PATH": fetcher.RUNTIME_PATH,
        "TRACE_PATH": fetcher.TRACE_PATH,
        "PROGRESS_PATH": fetcher.PROGRESS_PATH,
    }
    fetcher.CACHE_DIR = root / "data" / "disease_cache"
    fetcher.CONFIRMED_PATH = fetcher.CACHE_DIR / "confirmed_disease_cards.json"
    fetcher.PENDING_PATH = fetcher.CACHE_DIR / "pending_disease_cards.json"
    fetcher.RUNTIME_PATH = fetcher.CACHE_DIR / "runtime_cache.json"
    fetcher.TRACE_PATH = fetcher.CACHE_DIR / "external_fetch_trace.jsonl"
    fetcher.PROGRESS_PATH = fetcher.CACHE_DIR / "enrichment_progress.json"
    return old


def _restore_fetcher_paths(old):
    for key, value in old.items():
        setattr(fetcher, key, value)


if __name__ == "__main__":
    unittest.main()
