#!/usr/bin/env python3
"""Test M2 knowledge conversion files."""
import os, json, unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M2_KNOWLEDGE_DIR = os.path.join(BASE_DIR, "data", "m2_knowledge")


class TestM2KnowledgeConversion(unittest.TestCase):
    """Verify that all generated M2 knowledge files exist and are parseable."""

    def setUp(self):
        self.assertTrue(
            os.path.isdir(M2_KNOWLEDGE_DIR),
            f"data/m2_knowledge/ directory not found at {M2_KNOWLEDGE_DIR}",
        )

    def _load_json(self, filename):
        path = os.path.join(M2_KNOWLEDGE_DIR, filename)
        self.assertTrue(os.path.exists(path), f"Missing: {filename}")
        with open(path) as f:
            data = json.load(f)
        return data

    def _check_list(self, data, min_len=1):
        self.assertIsInstance(data, list, f"Expected list, got {type(data)}")
        self.assertGreaterEqual(len(data), min_len, f"Expected >= {min_len} items")

    def test_dir_exists(self):
        files = os.listdir(M2_KNOWLEDGE_DIR)
        self.assertGreater(len(files), 0, "m2_knowledge/ is empty")

    def test_symptom_factor_map(self):
        data = self._load_json("m2_symptom_factor_map.json")
        self._check_list(data, 100)
        first = data[0]
        # Accept either 'symptom' or 'raw_symptom' key
        has_key = "symptom" in first or "raw_symptom" in first
        self.assertTrue(has_key, f"Missing symptom key, got keys={list(first.keys())}")
        self.assertIn("tcm_factors", first, "Missing 'tcm_factors' key")
        self.assertIsInstance(first["tcm_factors"], list)
        self.assertGreater(len(first["tcm_factors"]), 0, "Symptom has no tcm_factors")

    def test_disease_syndrome_pool(self):
        data = self._load_json("m2_disease_syndrome_pool.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("disease_key", first, "Missing 'disease_key'")
        self.assertIn("syndromes", first, "Missing 'syndromes' array")
        self.assertIsInstance(first["syndromes"], list)

    def test_formula_base_map(self):
        data = self._load_json("m2_formula_base_map.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("formula_name", first, "Missing 'formula_name'")
        self.assertIn("disease_key", first, "Missing 'disease_key'")
        self.assertIn("syndrome_name", first, "Missing 'syndrome_name'")
        self.assertIn("candidate_only", first, "Missing 'candidate_only'")
        self.assertTrue(first.get("candidate_only"), "candidate_only should be True")
        self.assertIn("must_enter_m3", first, "Missing 'must_enter_m3'")
        self.assertTrue(first.get("must_enter_m3"), "must_enter_m3 should be True")

    def test_herb_tag_map(self):
        data = self._load_json("m2_herb_tag_map.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("herb_name", first, "Missing 'herb_name'")

    def test_disease_axes_pool(self):
        data = self._load_json("m2_disease_axes_pool.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("disease_name", first, "Missing 'disease_name'")
        self.assertIn("axes", first, "Missing 'axes'")

    def test_disease_symptom_map(self):
        data = self._load_json("m2_disease_symptom_map.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("disease_name", first, "Missing 'disease_name'")
        self.assertIn("typical_symptoms", first, "Missing 'typical_symptoms'")

    def test_pharmacology_map(self):
        data = self._load_json("m2_pharmacology_map.json")
        self._check_list(data, 10)
        first = data[0]
        self.assertIn("disease_name", first, "Missing 'disease_name'")
        self.assertIn("evidence_based_herbs", first, "Missing 'evidence_based_herbs'")

    def test_no_empty_jsons(self):
        for fname in os.listdir(M2_KNOWLEDGE_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(M2_KNOWLEDGE_DIR, fname)
            stat = os.path.getsize(path)
            self.assertGreater(stat, 100, f"{fname} is too small ({stat} bytes)")
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                self.assertGreater(len(data), 0, f"{fname} is an empty list")


if __name__ == "__main__":
    unittest.main()
