import unittest

from m1_engine import M1DiagnosisEngine
from m2_engine import M2SyndromeSelector
from m3_engine import M3ClinicalReviewEngine
from m4_engine import M4RoutingEngine


FORBIDDEN_FORMAL_FIELDS = {
    "prescription_text", "final_formula", "complete_formula", "final_prescription",
    "prescription", "full_formula", "dosage", "dose", "用法", "疗程",
}


def assert_no_formal_fields(testcase, payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            testcase.assertNotIn(key, FORBIDDEN_FORMAL_FIELDS)
            assert_no_formal_fields(testcase, value)
    elif isinstance(payload, list):
        for item in payload:
            assert_no_formal_fields(testcase, item)


class M1M4PromptCodeAlignmentTest(unittest.TestCase):
    def make_m1(self):
        engine = M1DiagnosisEngine()
        engine.deepseek_api_key = ""
        engine.gemini_api_key = ""
        return engine

    def make_m2(self):
        selector = M2SyndromeSelector()
        selector._llm_available = lambda: False
        return selector

    def test_m1_rhinitis_symptoms_prioritize_rhinitis_not_bronchitis(self):
        engine = self.make_m1()
        result = engine.diagnose({
            "chief_complaint": "鼻痒、打喷嚏、流清涕，遇冷加重",
            "symptoms": ["鼻痒", "打喷嚏", "流清涕", "遇冷加重"],
        })
        diagnoses = result["diagnosis_calibration"]["calibrated_diagnosis"]
        self.assertIn(diagnoses[0], {"变应性鼻炎", "慢性鼻炎", "急性鼻炎"})
        self.assertNotEqual(diagnoses[0], "急性支气管炎")
        self.assertTrue(result["diagnosis_boundary_trace"]["local_disease_kb_loaded"])
        self.assertTrue(result["diagnosis_boundary_trace"]["alias_mapping_loaded"])

    def test_m1_light_cough_without_lower_airway_evidence_does_not_force_bronchitis(self):
        engine = self.make_m1()
        result = engine.diagnose({
            "chief_complaint": "咳嗽2天，咽痒，无发热，无咳痰，无气促",
            "symptoms": ["咳嗽", "咽痒", "无发热", "无咳痰", "无气促"],
        })
        diagnoses = result["diagnosis_calibration"]["calibrated_diagnosis"]
        self.assertNotEqual(diagnoses[0], "急性支气管炎")
        self.assertIn(diagnoses[0], {"喉源性咳嗽", "急性上呼吸道感染"})

    def test_m1_insufficient_symptoms_request_more_info(self):
        engine = self.make_m1()
        result = engine.diagnose({"chief_complaint": "不舒服"})
        self.assertEqual(result["status"], "NEED_MORE_INFO")
        self.assertTrue(result["questions"])

    def test_m1_explicit_disease_name_is_standardized_and_passed_downstream(self):
        engine = self.make_m1()
        normalized = engine.standardize_disease_name("过敏性鼻炎")
        self.assertTrue(normalized["in_local_kb"])
        self.assertEqual(normalized["standard_name"], "变应性鼻炎")
        result = engine.diagnose({
            "patient_mentioned_disease": "过敏性鼻炎",
            "chief_complaint": "鼻痒喷嚏流清涕",
            "symptoms": ["鼻痒", "喷嚏", "流清涕"],
        })
        self.assertIn("m2_payload", result)
        self.assertTrue(result["m2_payload"]["primary_disease"])

    def test_m2_1_syndrome_trace_schema_for_cold_cough_context(self):
        selector = self.make_m2()
        result = selector.process(
            primary_disease="急性上呼吸道感染",
            symptoms=["咳嗽", "痰白清稀", "怕冷", "舌淡苔白"],
        )
        trace = result["syndrome_trace"]
        self.assertIn("syndrome_name", trace)
        self.assertIn("evidence", trace)
        self.assertIn("reasoning_summary", trace)
        self.assertIn("confidence", trace)
        self.assertTrue(trace["evidence"])
        self.assertIn("evidence_trace", result)
        assert_no_formal_fields(self, result)

    def test_m2_1_heat_context_has_trace_and_no_prescription_fields(self):
        selector = self.make_m2()
        result = selector.process(
            primary_disease="急性上呼吸道感染",
            symptoms=["发热", "咽痛", "口干", "舌红苔黄"],
        )
        self.assertTrue(result["syndrome_trace"]["syndrome_name"])
        self.assertTrue(result["evidence_trace"])
        assert_no_formal_fields(self, result)

    def test_m2_2_formula_query_and_candidate_contract(self):
        selector = self.make_m2()
        result = selector.process("变应性鼻炎", symptoms=["鼻痒", "喷嚏", "清涕"])
        self.assertTrue(result["candidate_only"])
        self.assertTrue(result["need_m2_3"])
        self.assertTrue(result["must_enter_m3"])
        self.assertFalse(result["no_candidate"])
        self.assertTrue(result["formula_candidates"])
        self.assertEqual(result["formula"]["source"].split(" ")[0], "data/m2_formula_knowledge.json")
        assert_no_formal_fields(self, result)

    def test_m2_2_no_candidate_when_formula_kb_missing(self):
        selector = self.make_m2()
        result = selector.process("不存在的病名", symptoms=["鼻痒"])
        self.assertEqual(result["status"], "NO_CANDIDATE")
        self.assertTrue(result["no_candidate"])
        self.assertEqual(result["formula_candidates"], [])
        self.assertIn("reason", result)
        self.assertIn("searched_terms", result)
        self.assertIn("missing_key", result)
        assert_no_formal_fields(self, result)

    def test_m2_3_modification_candidate_schema_and_no_low_grade(self):
        selector = self.make_m2()
        mods = selector._normalize_modification_candidates(
            [{"herb": "辛夷", "reason": "鼻塞咽痒", "source": "data/m3_herb_knowledge.json"}],
            "变应性鼻炎",
            ["鼻塞", "咽痒"],
        )
        self.assertTrue(mods)
        for item in mods:
            self.assertIn("target_disease", item)
            self.assertIn("target_symptom", item)
            self.assertIn("western_pathology", item)
            self.assertIn("evidence_sources", item)
            self.assertNotIn("low_grade", " ".join(item["evidence_sources"]))
        self.assertTrue(selector.herb_kb)

    def test_m3_real_safety_review_blocks_opposites_and_pregnancy_toxicity(self):
        reviewer = M3ClinicalReviewEngine()
        opposite = reviewer.review(["川乌", "半夏"], {"age": 35})
        self.assertEqual(opposite["review_decision"], "BLOCKED")
        self.assertFalse(opposite["formal_prescription_allowed"])
        self.assertTrue(opposite["safety_issues"])

        pregnant = reviewer.review(["川乌"], {"age": 30, "pregnancy": True})
        self.assertEqual(pregnant["review_decision"], "BLOCKED")
        self.assertIn(pregnant["dosage_review_status"], {"pending_dose_review", "not_applicable"})
        self.assertFalse(pregnant["formal_prescription_allowed"])
        assert_no_formal_fields(self, pregnant)

    def test_m3_children_risk_warning_and_no_dose_claim_completion(self):
        reviewer = M3ClinicalReviewEngine()
        result = reviewer.review(["麻黄"], {"age": 5})
        self.assertTrue(result["special_population"])
        self.assertEqual(result["dosage_review_status"], "pending_dose_review")
        self.assertFalse(result["formal_prescription_allowed"])

    def test_m4_followup_routing_does_not_modify_prescription(self):
        router = M4RoutingEngine()
        worse = router.route(
            initial_diagnosis="急性上呼吸道感染",
            initial_symptoms=["咳嗽"],
            followup_symptoms=["咳嗽加重", "发热加重", "夜间气促明显"],
            days_since_initial=3,
        )
        self.assertIn(worse["routing_decision"]["action"], {"EMERGENCY_STOP", "RETURN_TO_M1", "RETURN_TO_M2"})
        self.assertFalse(worse["formal_prescription_allowed"])
        self.assertEqual(worse["formula_candidates"], [])
        self.assertEqual(worse["modification_candidates"], [])
        assert_no_formal_fields(self, worse)

        improved = router.route(
            initial_diagnosis="急性上呼吸道感染",
            initial_symptoms=["鼻塞", "喷嚏"],
            followup_symptoms=["鼻塞减轻", "喷嚏减轻"],
            followup_feedback="好转",
            days_since_initial=5,
        )
        self.assertIn(improved["routing_decision"]["action"], {"RETURN_TO_M2", "RETURN_TO_M1"})
        self.assertFalse(improved["formal_prescription_allowed"])

    def test_m1_to_m4_chain_keeps_formal_prescription_blocked(self):
        m1 = self.make_m1()
        m2 = self.make_m2()
        m3 = M3ClinicalReviewEngine()
        m4 = M4RoutingEngine()

        m1_result = m1.diagnose({
            "chief_complaint": "鼻痒、喷嚏、流清涕，遇冷加重",
            "symptoms": ["鼻痒", "喷嚏", "流清涕", "遇冷加重"],
        })
        disease = m1_result["m2_payload"]["primary_disease"]
        m2_result = m2.process(disease, symptoms=m1_result["m2_payload"]["symptoms"])
        m3_result = m3.review(m2_result.get("formula", {}).get("herbs", []), {"age": 30})
        m4_result = m4.route(disease, ["鼻痒", "喷嚏"], ["鼻痒减轻", "喷嚏减轻"], "好转")

        self.assertTrue(disease)
        self.assertTrue(m2_result["candidate_only"])
        self.assertFalse(m3_result["formal_prescription_allowed"])
        self.assertFalse(m4_result["formal_prescription_allowed"])
        for payload in [m1_result, m2_result, m3_result, m4_result]:
            assert_no_formal_fields(self, payload)


if __name__ == "__main__":
    unittest.main()
