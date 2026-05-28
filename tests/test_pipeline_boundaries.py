import json
import tempfile
import unittest
from pathlib import Path

from full_pipeline import FullPipeline, PatientInfo
from services.pharmacology_cache_service import PharmacologyCacheService


class PipelineBoundaryRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = FullPipeline()
        cls.asthma_result = cls.pipeline.run(
            patient=PatientInfo(
                name="qa",
                age="8岁",
                gender="男",
                symptoms=["咳嗽", "喘息", "气喘", "高血压"],
            ),
            disease_query="支气管哮喘",
            custom_syndrome="风寒袭肺",
            risk_factors=["高血压"],
        )

    def test_m1_does_not_trust_user_claimed_diagnosis(self):
        m1 = self.asthma_result["m1_diagnosis"]
        self.assertEqual(m1["user_claimed_diagnosis"], "支气管哮喘")
        self.assertFalse(m1["user_claimed_diagnosis_used_as_primary"])
        self.assertFalse(m1["trace"]["user_claimed_diagnosis_used_as_primary_anchor"])

    def test_process_a_public_m1_never_accesses_local_kb(self):
        trace = self.asthma_result["pipeline_trace"]["process_a_public_m1_trace"]
        self.assertEqual(trace["process"], "A_PUBLIC_MODERN_MEDICINE")
        self.assertFalse(trace["local_kb_access_allowed"])
        self.assertFalse(trace["local_kb_accessed"])
        self.assertTrue(trace["online_search_allowed"])
        self.assertTrue(trace["general_model_knowledge_allowed"])
        self.assertEqual(trace["max_candidates_forwarded"], 3)
        self.assertLessEqual(trace["candidate_count"], 3)

    def test_m1_local_admission_gate_uses_public_candidates_against_local_kb(self):
        trace = self.asthma_result["pipeline_trace"]["m1_trace"]
        self.assertEqual(trace["process"], "M1_LOCAL_ADMISSION_GATE")
        self.assertTrue(trace["local_kb_accessed"])
        self.assertFalse(trace["online_search_allowed"])
        self.assertGreaterEqual(trace["public_process_candidate_count"], 1)
        self.assertLessEqual(trace["public_process_candidate_count"], 3)
        self.assertLessEqual(len(trace["public_process_candidates_checked"]), 3)
        self.assertTrue(trace["local_formula_entry_checked"])

    def test_m1_generates_multiple_candidate_diseases(self):
        candidates = self.asthma_result["m1_diagnosis"]["candidate_diseases"]
        self.assertGreaterEqual(len(candidates), 2)
        self.assertTrue(any("哮喘" in c["disease_name"] for c in candidates))

    def test_m1_patient_pathology_axes_generated(self):
        m1 = self.asthma_result["m1_diagnosis"]
        self.assertTrue(m1["patient_pathology_axes"])
        self.assertIn("primary_formula_entry_disease", m1)
        self.assertIn("uncovered_problem_targets", m1)

    def test_primary_formula_entry_from_kb_and_axis_coverage(self):
        m1 = self.asthma_result["m1_diagnosis"]
        self.assertEqual(m1["primary_formula_entry_disease"], m1["candidate_diseases"][0]["disease_name"])
        self.assertGreater(m1["candidate_diseases"][0]["overall_score"], 0)

    def test_m2_1_uses_primary_formula_entry_disease(self):
        trace = self.asthma_result["pipeline_trace"]["m2_1_trace"]
        self.assertEqual(trace["primary_formula_entry_disease_used"], self.asthma_result["m1_diagnosis"]["primary_formula_entry_disease"])
        self.assertFalse(trace["user_claimed_diagnosis_used"])
        self.assertEqual(trace["process"], "B_PRIVATE_LOCAL_TCM")
        self.assertTrue(trace["local_rag_only"])
        self.assertFalse(trace["online_search_allowed"])
        self.assertFalse(trace["general_model_knowledge_allowed"])

    def test_m2_2_uses_primary_formula_entry_disease_for_rag(self):
        trace = self.asthma_result["pipeline_trace"]["m2_2_formula_trace"]
        self.assertEqual(trace["lookup_disease"], self.asthma_result["m1_diagnosis"]["primary_formula_entry_disease"])
        self.assertTrue(trace["used_primary_formula_entry_disease"])
        self.assertFalse(trace["used_user_claimed_diagnosis"])
        self.assertEqual(trace["process"], "B_PRIVATE_LOCAL_TCM")
        self.assertTrue(trace["local_rag_only"])
        self.assertFalse(trace["online_search_allowed"])
        self.assertFalse(trace["general_model_knowledge_allowed"])

    def test_formula_name_must_exist_in_knowledge_base(self):
        trace = self.asthma_result["pipeline_trace"]["m2_2_formula_trace"]
        if not trace["formula_name_from_knowledge_base"]:
            self.assertEqual(self.asthma_result["m2_prescription"]["formula_name"], "")

    def test_case_library_reference_only(self):
        trace = self.asthma_result["pipeline_trace"]["case_library_trace"]
        self.assertTrue(trace["reference_only"])
        self.assertFalse(trace["case_herbs_used_in_prescription"])
        for case in self.asthma_result["m2_prescription"]["reference_cases"]:
            self.assertTrue(case["reference_only"])
            self.assertFalse(case["case_herbs_used_in_prescription"])

    def test_pharmacology_cache_only_as_addon_candidate(self):
        trace = self.asthma_result["pipeline_trace"]["pharmacology_cache_trace"]
        self.assertTrue(trace["candidate_only"])
        self.assertIn("pharmacology_addon_candidates", trace["output_slots"])
        self.assertFalse(self.asthma_result["m3_reviewed_prescription"])

    def test_symmap_only_never_auto_adds_herb(self):
        service = _service_with_cache({
            "SymMap病": {
                "western_disease_name": "SymMap病",
                "molecular_targets": ["IL4"],
                "effective_herbs": ["麻黄"],
                "target_count": 1,
                "herb_count": 1,
            }
        })
        result = service.extract_pharmacology_candidates("SymMap病", "风寒袭肺", [])
        self.assertEqual(result["pharmacology_addon_candidates"], [])
        self.assertTrue(result["pharmacology_evidence_hints"])
        self.assertFalse(result["pharmacology_evidence_hints"][0]["allow_auto_add"])

    def test_m2_2_candidate_only_not_formal_m3(self):
        trace = self.asthma_result["pipeline_trace"]
        self.assertTrue(trace["m2_2_formula_trace"]["candidate_only"])
        self.assertFalse(trace["m2_2_formula_trace"]["direct_m3_audit_allowed"])
        self.assertEqual(trace["m3_audit_trace"]["status"], "SKIPPED")

    def test_m3_audits_only_m2_3_formal_prescription(self):
        no_formal = self.asthma_result["pipeline_trace"]["m3_audit_trace"]
        self.assertEqual(no_formal["status"], "SKIPPED")
        audited = self.pipeline.run(
            patient=PatientInfo("formal", "8岁", "男", symptoms=["咳嗽", "喘息"]),
            disease_query="支气管哮喘",
            custom_syndrome="风寒袭肺",
            m2_3_formal_prescription={"formula_name": "测试正式方", "herbs": [{"name": "桂枝"}]},
        )
        self.assertEqual(audited["pipeline_trace"]["m3_audit_trace"]["status"], "AUDITED")
        self.assertTrue(audited["m3_reviewed_prescription"]["herbs"])

    def test_asthma_hypertension_does_not_directly_block_mahuang(self):
        rejected = self.asthma_result["m2_pharmacology"]["rejected"]
        self.assertFalse(any("麻黄" in r["herb_name"] and r["matched_contraindication"] == "高血压" for r in rejected))
        service = _service_with_cache({
            "支气管哮喘": {
                "western_disease_name": "支气管哮喘",
                "disease_pathway": "airway hyperresponsiveness",
                "key_targets": ["ADRB2"],
                "evidence_based_herbs": [{
                    "herb_name": "麻黄",
                    "suitable_tcm_syndrome": "风寒袭肺",
                    "contraindications": "高血压、冠心病、心律失常者慎用。",
                    "target_mechanism": "bronchodilation",
                }],
            }
        })
        result = service.extract_pharmacology_candidates("支气管哮喘", "风寒袭肺", ["高血压"])
        self.assertEqual(result["rejected_pharmacology_candidates"], [])
        self.assertTrue(any(c["herb_name"] == "麻黄" for c in result["pharmacology_addon_candidates"]))

    def test_dyslipidemia_liver_disease_blocks_red_yeast_rice(self):
        service = _service_with_cache({
            "血脂异常": {
                "western_disease_name": "血脂异常",
                "disease_pathway": "lipid metabolism",
                "key_targets": ["HMGCR"],
                "evidence_based_herbs": [{
                    "herb_name": "红曲",
                    "suitable_tcm_syndrome": "痰浊阻滞",
                    "contraindications": "活动性肝病、肝功能异常、转氨酶升高者禁用或慎用。",
                    "target_mechanism": "HMGCR related lipid regulation",
                }],
            }
        })
        result = service.extract_pharmacology_candidates("血脂异常", "痰浊阻滞", ["肝功能异常"])
        self.assertTrue(any(r["herb_name"] == "红曲" for r in result["rejected_pharmacology_candidates"]))

    def test_ra_leigongteng_manual_review_only(self):
        service = _service_with_cache({
            "类风湿关节炎": {
                "western_disease_name": "类风湿关节炎",
                "disease_pathway": "immune inflammation",
                "key_targets": ["TNF"],
                "evidence_based_herbs": [{
                    "herb_name": "雷公藤",
                    "suitable_tcm_syndrome": "痹证",
                    "contraindications": "肝肾功能异常、孕妇、白细胞减少者禁用。",
                    "target_mechanism": "immune modulation",
                }],
            }
        })
        result = service.extract_pharmacology_candidates("类风湿关节炎", "痹证", [])
        self.assertEqual(result["rejected_pharmacology_candidates"], [])
        self.assertTrue(result["pharmacology_addon_candidates"])
        candidate = result["pharmacology_addon_candidates"][0]
        self.assertFalse(candidate["allow_auto_add"])
        self.assertTrue(candidate["need_manual_review"])

    def test_pipeline_trace_has_required_sections(self):
        required = {
            "process_a_public_m1_trace",
            "m1_trace",
            "m2_1_trace",
            "case_library_trace",
            "m2_2_formula_trace",
            "pharmacology_cache_trace",
            "m3_audit_trace",
            "final_decision_trace",
        }
        self.assertTrue(required.issubset(self.asthma_result["pipeline_trace"].keys()))


def _service_with_cache(cache: dict) -> PharmacologyCacheService:
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        alias_path = Path(tmp) / "alias.json"
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        alias_path.write_text("{}", encoding="utf-8")
        service = PharmacologyCacheService(cache_path=str(cache_path), alias_path=str(alias_path))
    return service


if __name__ == "__main__":
    unittest.main()
