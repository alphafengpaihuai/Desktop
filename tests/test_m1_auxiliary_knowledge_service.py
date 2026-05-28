import json
import os
import unittest
from pathlib import Path

from m1_engine import M1DiagnosisEngine
from scripts.audit_m1_auxiliary_knowledge import audit
from services.m1.m1_auxiliary_knowledge_service import (
    DATA_DIR,
    M1AuxiliaryKnowledgeService,
    build_m1_auxiliary_context,
)


FORBIDDEN_TCM_TERMS = [
    "证型", "病机", "治则", "方剂", "中药", "君臣佐使", "调理", "辨证",
    "寒热虚实", "脏腑辨证",
]

JSON_FILES = [
    DATA_DIR / "m1_disease_alias_map.json",
    DATA_DIR / "m1_chief_complaint_differential_map.json",
    DATA_DIR / "m1_red_flag_rules.json",
    DATA_DIR / "m1_clinical_feature_interpreter.json",
]


class M1AuxiliaryKnowledgeServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = M1AuxiliaryKnowledgeService()
        cls.core_names = {
            entry["disease_name"]
            for entry in json.loads(Path("diseases_core.json").read_text(encoding="utf-8"))
        }

    def test_alias_normalizes_af(self):
        result = self.service.normalize_disease_name("房颤")
        self.assertEqual(result["canonical_disease_name"], "Atrial Fibrillation")
        self.assertIn(result["mapping_status"], {"in_m1", "not_in_m1"})
        self.assertEqual(result["diagnostic_weight"], 0)

    def test_chest_pain_differentials_include_must_not_miss(self):
        result = self.service.get_differentials_by_complaint("胸痛")
        must_not_miss = result["must_not_miss"]
        self.assertIn("Acute Coronary Syndrome", must_not_miss)
        self.assertIn("Aortic Dissection", must_not_miss)
        self.assertIn("Pulmonary Embolism", must_not_miss)

    def test_red_flag_chest_pain_sweating_hypotension(self):
        result = self.service.detect_red_flags("胸痛 大汗 低血压")
        self.assertTrue(result)
        self.assertEqual(result[0]["severity"], "emergency")
        self.assertIn(result[0]["action"], {"need_external_check", "urgent_review", "block_auto_m2", "warning_only"})
        self.assertTrue(result[0]["block_m2_auto_flow"])

    def test_troponin_maps_cardiovascular_axis_without_direct_ami_diagnosis(self):
        result = self.service.interpret_clinical_features(["肌钙蛋白升高"])
        self.assertTrue(result)
        axes = {axis["axis"] for item in result for axis in item["mapped_axes"]}
        self.assertIn("cardiovascular_or_circulatory_risk", axes)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertIn("不能单独等同于急性心肌梗死", blob)

    def test_d_dimer_warns_thrombosis_but_not_direct_pe_diagnosis(self):
        result = self.service.interpret_clinical_features(["D-二聚体升高"])
        self.assertTrue(result)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertIn("血栓风险", blob)
        self.assertIn("不能单独诊断肺栓塞", blob)

    def test_no_tcm_content_in_new_json_or_output(self):
        blobs = []
        for path in JSON_FILES:
            blobs.append(path.read_text(encoding="utf-8"))
        output = self.service.enrich_m1_input({
            "chief_complaint": "胸痛",
            "symptoms": ["大汗", "低血压"],
            "labs": ["肌钙蛋白升高"],
            "diagnosis": "房颤",
        })
        blobs.append(json.dumps(output, ensure_ascii=False))
        blob = "\n".join(blobs)
        for term in FORBIDDEN_TCM_TERMS:
            self.assertNotIn(term, blob)

    def test_default_disabled_does_not_affect_original_m1_output(self):
        old_env = os.environ.pop("M1_AUX_KNOWLEDGE_ENABLED", None)
        try:
            self.assertEqual(build_m1_auxiliary_context({"chief_complaint": "胸痛"}), {})
            engine = M1DiagnosisEngine()
            before = engine.diagnose("Atrial Fibrillation", ["心悸"])
            after = engine.diagnose("Atrial Fibrillation", ["心悸"])
            self.assertEqual(before.matched_disease.disease_name, after.matched_disease.disease_name)
            self.assertEqual(before.matched_disease.overall_score, after.matched_disease.overall_score)
        finally:
            if old_env is not None:
                os.environ["M1_AUX_KNOWLEDGE_ENABLED"] = old_env

    def test_enabled_builds_auxiliary_context(self):
        old_env = os.environ.get("M1_AUX_KNOWLEDGE_ENABLED")
        os.environ["M1_AUX_KNOWLEDGE_ENABLED"] = "true"
        try:
            context = build_m1_auxiliary_context({
                "chief_complaint": "胸痛",
                "symptoms": ["大汗", "低血压"],
                "labs": ["肌钙蛋白升高"],
                "diagnosis": "房颤",
            })
        finally:
            if old_env is None:
                os.environ.pop("M1_AUX_KNOWLEDGE_ENABLED", None)
            else:
                os.environ["M1_AUX_KNOWLEDGE_ENABLED"] = old_env
        self.assertTrue(context["trace"]["enabled"])
        self.assertTrue(context["need_external_check"])
        self.assertTrue(context["block_m2_auto_flow"])

    def test_json_files_are_valid(self):
        for path in JSON_FILES:
            with self.subTest(path=path):
                self.assertTrue(path.exists())
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_disease_mapping_status_audit(self):
        for path in JSON_FILES:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._check_mapping_status(data)

    def test_audit_script_passes(self):
        result = audit()
        self.assertTrue(result["ok"], msg=json.dumps(result, ensure_ascii=False, indent=2))

    def _check_mapping_status(self, value):
        if isinstance(value, dict):
            if "disease_name" in value and "mapping_status" in value:
                expected = "in_m1" if value["disease_name"] in self.core_names else "not_in_m1"
                self.assertEqual(value["mapping_status"], expected, msg=value["disease_name"])
            if "canonical_disease_name" in value and "mapping_status" in value:
                expected = "in_m1" if value["canonical_disease_name"] in self.core_names else "not_in_m1"
                self.assertEqual(value["mapping_status"], expected, msg=value["canonical_disease_name"])
            for child in value.values():
                self._check_mapping_status(child)
        elif isinstance(value, list):
            for child in value:
                self._check_mapping_status(child)


if __name__ == "__main__":
    unittest.main()
