import os
import unittest
from pathlib import Path

import full_pipeline
from m2_engine import M2SyndromeSelector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SOURCE = (PROJECT_ROOT / "bridge_server.py").read_text(encoding="utf-8")


class LegacyPrescriptionPathGuardsTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ALLOW_LEGACY_M2_FULL_PIPELINE", None)
        os.environ.pop("ALLOW_LEGACY_DOSAGE_EXTRACTION", None)
        os.environ.pop("ALLOW_LEGACY_FULL_PIPELINE_PRESCRIPTION", None)
        os.environ.pop("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", None)

    def test_m2_full_pipeline_is_blocked_by_default(self):
        m2 = M2SyndromeSelector()
        result = m2.full_pipeline("变应性鼻炎", {"symptoms": ["鼻痒", "喷嚏"]})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertTrue(result["candidate_only"])
        self.assertTrue(result["must_enter_m3"])
        self.assertTrue(result["trace_closure_required"])
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("final_prescription", result)

    def test_m2_extract_dosages_is_blocked_by_default(self):
        m2 = M2SyndromeSelector()
        result = m2._extract_dosages(["麻黄"], "急性上呼吸道感染")
        self.assertTrue(result["_blocked"])
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("麻黄", result)

    def test_full_pipeline_legacy_m3_review_blocks_dosage_output(self):
        result = full_pipeline.m3_review(
            {"main_formula_name": "测试方", "main_herbs": ["麻黄"], "add_herbs": []},
            m3_service=None,
            patient_info={},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("herbs", result)

    def test_full_pipeline_legacy_m4_followup_blocks_instructions(self):
        result = full_pipeline.m4_followup({}, {}, {})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("instructions", result)

    def test_full_pipeline_format_output_blocks_prescription_text(self):
        text = full_pipeline.format_output("p", "1", "男", "", "", {}, {}, {}, [])
        self.assertIn("旧版完整处方展示路径已被安全闸门阻断", text)
        self.assertNotIn("每日1剂", text)
        self.assertNotIn("煎服", text)

    def test_bridge_selection_answers_has_legacy_guard(self):
        self.assertIn("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", BRIDGE_SOURCE)
        self.assertIn("legacy_prescription_path_blocked", BRIDGE_SOURCE)
        self.assertIn("formal_prescription_allowed", BRIDGE_SOURCE)
        self.assertIn("must_enter_m3", BRIDGE_SOURCE)
        self.assertIn("trace_closure_required", BRIDGE_SOURCE)

    def test_bridge_symptom_herb_map_is_behind_guarded_path(self):
        guard_pos = BRIDGE_SOURCE.find("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH")
        map_pos = BRIDGE_SOURCE.find("SYMPTOM_HERB_MAP")
        send_guarded_pos = BRIDGE_SOURCE.find("[WS_SEND_DIAGNOSIS_RESULT_GUARDED]")
        self.assertGreaterEqual(guard_pos, 0)
        self.assertGreaterEqual(map_pos, 0)
        self.assertGreater(send_guarded_pos, guard_pos)
        self.assertLess(map_pos, send_guarded_pos)


if __name__ == "__main__":
    unittest.main()
