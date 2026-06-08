"""M2 Skill v1.0 边界合规测试

测试范围 (对应 M2 skill/spec 强制红线)：
1. M2-1 输出不含处方/方剂/剂量
2. M2-2 只输出 formula_candidates，禁止 final_formula/prescription_text/dose
3. M2-2 输出必须 candidate_only=true, need_m2_3=true, must_enter_m3=true
4. M2-3 只输出 modification_candidates，不输出完整处方/剂量/方解
5. 无 source_trace 时拒绝进入下一步
6. 红线字段触发 validator (formal_prescription_allowed != False)
7. 旧病理轴字段不再参与核心判断
8. 输出中包含 spec 必含字段 (disease_key, reverse_audit, inferred_pathology_stage)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector

# ── 被禁止的处方字段 ──
FORBIDDEN_PRESCRIPTION_FIELDS = frozenset({
    "prescription_text", "final_formula", "complete_formula", "final_prescription",
    "prescription", "full_formula", "dosage", "dose", "用法", "疗程",
})

# ── Spec 必含字段 ──
SPEC_MANDATORY_OUTPUT_FIELDS_M2 = frozenset({
    "stage", "primary_disease", "disease_key", "candidate_only",
    "need_m2_3", "must_enter_m3", "no_candidate",
    "formal_prescription_allowed", "reverse_audit",
})

SPEC_MANDATORY_OUTPUT_FIELDS_M2_1 = {
    "stage", "status", "primary_disease", "disease_key",
    "inferred_pathology_stage", "selected_syndrome_key",
    "syndrome_trace", "evidence_trace", "input_trace",
    "reverse_audit", "formal_prescription_allowed",
}

SPEC_MANDATORY_OUTPUT_FIELDS_M2_2 = {
    "stage", "status", "primary_disease", "disease_key",
    "candidate_only", "need_m2_3", "must_enter_m3", "no_candidate",
    "formula_candidates", "reverse_audit", "formal_prescription_allowed",
}

SPEC_MANDATORY_OUTPUT_FIELDS_M2_3 = {
    "stage", "status", "primary_disease", "disease_key",
    "candidate_only", "need_m2_3", "must_enter_m3", "no_candidate",
    "modification_candidates", "reverse_audit", "formal_prescription_allowed",
}


def _assert_no_forbidden_prescription_fields(testcase, obj):
    """递归检查 dict/list 中无禁止的处方字段"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            testcase.assertNotIn(key, FORBIDDEN_PRESCRIPTION_FIELDS,
                                f"输出包含禁止的处方字段: {key}")
            if key == "formal_prescription_allowed":
                testcase.assertIs(value, False,
                                  f"formal_prescription_allowed 应为 False, 实际为 {value}")
            _assert_no_forbidden_prescription_fields(testcase, value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_forbidden_prescription_fields(testcase, item)


class TestM2SpecBoundaries(unittest.TestCase):
    """M2 Skill v1.0 强制边界测试"""

    @classmethod
    def setUpClass(cls):
        cls.m2 = M2SyndromeSelector()
        # 禁用 LLM 以确保测试在无 API 环境下可重复
        cls.m2._llm_available = lambda: False

    # ══════════════════════════════════════════════════════════
    # 边界 1: M2-1 输出不包含处方内容
    # ══════════════════════════════════════════════════════════

    def test_m2_1_no_prescription_fields_anywhere(self):
        """M2-1 输出完全不含处方/方剂/剂量字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
        )
        self.assertNotIn("formula", result,
                         "M2-1 不应包含 formula 字段")
        self.assertNotIn("formula_candidates", result,
                         "M2-1 不应包含 formula_candidates 字段")
        self.assertNotIn("herbs", result,
                         "M2-1 不应包含 herbs 字段")
        _assert_no_forbidden_prescription_fields(self, result)

    def test_m2_1_output_contains_mandatory_fields(self):
        """M2-1 输出包含 spec 必含字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咽痛"],
            tongue="舌红",
            pulse="脉浮数",
        )
        for field in SPEC_MANDATORY_OUTPUT_FIELDS_M2_1:
            self.assertIn(field, result,
                         f"M2-1 输出缺少 spec 必含字段: {field}")

    def test_m2_1_disease_key_is_present(self):
        """M2-1 输出 disease_key 字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
        )
        dk = result.get("disease_key", "")
        self.assertTrue(dk, "disease_key 不应为空")
        self.assertEqual(result.get("stage"), "M2_1")

    def test_m2_1_inferred_pathology_stage(self):
        """M2-1 输出 inferred_pathology_stage (Spec section 11 要求)"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "黄痰"],
            tongue="舌红苔黄",
        )
        self.assertIn("inferred_pathology_stage", result,
                      "M2-1 需包含 inferred_pathology_stage")

    # ══════════════════════════════════════════════════════════
    # 边界 2: M2-2 只输出 formula_candidates
    # ══════════════════════════════════════════════════════════

    def test_m2_2_only_formula_candidates_no_final(self):
        """M2-2 输出只包含 formula_candidates，不包含 final_formula/prescription/dose"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "变应性鼻炎",
            symptoms=["鼻痒", "打喷嚏", "清涕"],
        )
        result = self.m2.run_m2_2_formula_candidates(
            "变应性鼻炎",
            syndrome_trace=m2_1.get("syndrome_trace", {}),
            symptoms=["鼻痒", "打喷嚏", "清涕"],
        )
        self.assertIn("formula_candidates", result,
                      "M2-2 应包含 formula_candidates")
        # formula_candidates 是 list
        self.assertIsInstance(result["formula_candidates"], list,
                              "formula_candidates 应为 list")
        # 没有 final_formula/prescription_text
        _assert_no_forbidden_prescription_fields(self, result)

    def test_m2_2_formula_candidate_has_source_trace(self):
        """formula_candidate 包含 source/syndrome_name 等可审计字段"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咽痛"],
        )
        result = self.m2.run_m2_2_formula_candidates(
            "急性上呼吸道感染",
            syndrome_trace=m2_1.get("syndrome_trace", {}),
            symptoms=["发热", "咽痛"],
        )
        for candidate in result.get("formula_candidates", []):
            self.assertIn("source", candidate,
                          "formula_candidate 需有 source_trace")
            self.assertIn("formula_name", candidate,
                          "formula_candidate 需有 formula_name")
            self.assertIn("disease_name", candidate,
                          "formula_candidate 需有 disease_name")
            self.assertIn("syndrome_name", candidate,
                          "formula_candidate 需有 syndrome_name")

    # ══════════════════════════════════════════════════════════
    # 边界 3: M2-2 输出合同 (candidate_only/need_m2_3/must_enter_m3)
    # ══════════════════════════════════════════════════════════

    def test_m2_2_candidate_contract_enforced(self):
        """M2-2 输出必须 candidate_only=true, need_m2_3=true, must_enter_m3=true"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
        )
        result = self.m2.run_m2_2_formula_candidates(
            "肺炎",
            syndrome_trace=m2_1.get("syndrome_trace", {}),
            symptoms=["发热", "咳嗽"],
        )
        self.assertTrue(result.get("candidate_only"),
                        "M2-2 candidate_only 必须为 True")
        self.assertTrue(result.get("need_m2_3"),
                        "M2-2 need_m2_3 必须为 True")
        self.assertTrue(result.get("must_enter_m3"),
                        "M2-2 must_enter_m3 必须为 True")

    def test_m2_2_mandatory_fields_present(self):
        """M2-2 输出包含 spec 必含字段"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
        )
        result = self.m2.run_m2_2_formula_candidates(
            "肺炎",
            syndrome_trace=m2_1.get("syndrome_trace", {}),
        )
        for field in SPEC_MANDATORY_OUTPUT_FIELDS_M2_2:
            self.assertIn(field, result,
                         f"M2-2 缺少 spec 必含字段: {field}")

    # ══════════════════════════════════════════════════════════
    # 边界 4: M2-3 只输出 modification_candidates
    # ══════════════════════════════════════════════════════════

    def test_m2_3_only_modification_candidates(self):
        """M2-3 只输出 modification_candidates，不输出最终处方"""
        result = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            formula_herbs=["龙胆草", "栀子"],
            symptoms=["口干", "大便不畅"],
            syndrome_name="",
        )
        self.assertIn("modification_candidates", result,
                      "M2-3 应包含 modification_candidates")
        self.assertNotIn("final_formula", result,
                         "M2-3 不应包含 final_formula")
        self.assertNotIn("dosage", result,
                         "M2-3 不应包含 dosage")
        _assert_no_forbidden_prescription_fields(self, result)

    def test_m2_3_modification_has_trace_fields(self):
        """modification_candidates 包含可审计字段 (target_disease/evidence_sources/matched_reason)"""
        result = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            formula_herbs=["龙胆草", "栀子"],
            symptoms=["口干", "口苦", "大便不畅"],
        )
        for mc in result.get("modification_candidates", []):
            self.assertIn("target_disease", mc,
                          "modification_candidate 需有 target_disease")
            self.assertIn("evidence_sources", mc,
                          "modification_candidate 需有 evidence_sources")
            self.assertIn("matched_reason", mc,
                          "modification_candidate 需有 matched_reason")
            self.assertIn("target_symptom", mc,
                          "modification_candidate 需有 target_symptom")

    def test_m2_3_mandatory_fields_present(self):
        """M2-3 输出包含 spec 必含字段"""
        result = self.m2.run_m2_3_modification_candidates(
            primary_disease="肺炎",
            formula_herbs=["麻黄", "杏仁"],
            symptoms=["咳嗽"],
        )
        for field in SPEC_MANDATORY_OUTPUT_FIELDS_M2_3:
            self.assertIn(field, result,
                         f"M2-3 缺少 spec 必含字段: {field}")

    # ══════════════════════════════════════════════════════════
    # 边界 5: 无 source_trace 时拒绝
    # ══════════════════════════════════════════════════════════

    def test_m2_3_no_evidence_returns_no_candidate(self):
        """药物库无证据时返回 no_candidate"""
        result = self.m2.run_m2_3_modification_candidates(
            primary_disease="不存在疾病名_xxxxx_DEBUG",
            formula_herbs=[],
            symptoms=[],
        )
        self.assertTrue(result.get("no_candidate", False),
                        "无证据时应返回 no_candidate")

    # ══════════════════════════════════════════════════════════
    # 边界 6: formal_prescription_allowed 恒为 False
    # ══════════════════════════════════════════════════════════

    def test_formal_prescription_allowed_always_false_in_all_stages(self):
        """所有 M2 阶段 formal_prescription_allowed 必须为 False"""
        for stage_name, runner in [
            ("M2-1", lambda: self.m2.run_m2_1_syndrome_reasoning(
                "急性上呼吸道感染", symptoms=["发热", "咽痛"])),
            ("M2-2", lambda: self.m2.run_m2_2_formula_candidates(
                "急性上呼吸道感染",
                syndrome_trace={"syndrome_name": "风热犯表", "reasoning_summary": "test"},
                symptoms=["发热", "咽痛"])),
            ("M2-3", lambda: self.m2.run_m2_3_modification_candidates(
                "肺炎", formula_herbs=[])),
            ("process", lambda: self.m2.process(
                "急性上呼吸道感染", symptoms=["发热", "咽痛"])),
        ]:
            with self.subTest(stage=stage_name):
                result = runner()
                fpa = result.get("formal_prescription_allowed", None)
                if fpa is not None:
                    self.assertIs(fpa, False,
                                  f"{stage_name} formal_prescription_allowed 应为 False")

    # ══════════════════════════════════════════════════════════
    # 边界 7: 旧病理轴字段不参与核心判断
    # ══════════════════════════════════════════════════════════

    def test_m2_engine_does_not_import_bridge(self):
        """M2SyndromeSelector 不引用废弃的 pathology_syndrome_bridge"""
        # 验证 m2_engine.py 的 import 列表不含废弃路径
        import inspect
        source = inspect.getsource(self.m2.__class__)
        self.assertNotIn("m2_pathology_syndrome_bridge", source,
                         "M2SyndromeSelector 不应引用已废弃的 bridge")
        self.assertNotIn("m2_pattern_match_engine", source,
                         "M2SyndromeSelector 不应引用 m2_pattern_match_engine")

    # ══════════════════════════════════════════════════════════
    # 边界 8: NO_CANDIDATE 路径也有 spec 字段
    # ══════════════════════════════════════════════════════════

    def test_no_candidate_has_disease_key_and_reverse_audit(self):
        """NO_CANDIDATE 情况下仍包含 disease_key 和 reverse_audit"""
        result = self.m2.run_m2_2_formula_candidates(
            primary_disease="完全不存在_DEBUG_ONLY",
            syndrome_trace={},
        )
        self.assertEqual(result.get("status"), "NO_CANDIDATE")
        self.assertIn("disease_key", result,
                      "NO_CANDIDATE 输出需有 disease_key")
        self.assertIn("reverse_audit", result,
                      "NO_CANDIDATE 输出需有 reverse_audit")

    def test_no_candidate_m2_3_has_reverse_audit(self):
        """M2-3 NO_CANDIDATE 包含 reverse_audit"""
        result = self.m2.run_m2_3_modification_candidates(
            primary_disease="不存在疾病",
            formula_herbs=[],
            symptoms=[],
        )
        self.assertIn("reverse_audit", result,
                      "M2-3 no_candidate 需包含 reverse_audit")

    # ══════════════════════════════════════════════════════════
    # 病理轨（Spec Section 5-6）新增边界测试
    # ══════════════════════════════════════════════════════════

    def test_m2_1_output_contains_pathology_based_result(self):
        """M2-1 输出包含病理轨结果字段 pathology_based_result"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄",
            pulse="脉滑数",
        )
        self.assertIn("pathology_based_result", result,
                      "M2-1 应包含 pathology_based_result")
        self.assertIn("traditional_tcm_result", result,
                      "M2-1 应包含 traditional_tcm_result")
        self.assertIn("syndrome_comparison", result,
                      "M2-1 应包含 syndrome_comparison")

    def test_m2_1_pathology_based_result_has_spec_fields(self):
        """pathology_based_result 包含 spec 5.2 要求的字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
            pulse="脉数",
        )
        pbr = result.get("pathology_based_result", {})
        if pbr:
            self.assertEqual(pbr.get("track"), "pathology_based")
            self.assertIn("disease_type", pbr)
            self.assertIn("framework", pbr)
            self.assertIn("inferred_pathology_stage", pbr)
            self.assertIn("tcm_pathogenesis", pbr)
            self.assertIn("candidate_syndrome", pbr)
            self.assertIn("evidence_for", pbr)
            self.assertIn("evidence_against", pbr)
            self.assertIn("confidence", pbr)

    def test_m2_1_traditional_tcm_result_has_spec_fields(self):
        """traditional_tcm_result 包含 spec 5.3 要求的字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄",
        )
        ttr = result.get("traditional_tcm_result", {})
        if ttr:
            self.assertEqual(ttr.get("track"), "traditional_tcm")
            self.assertIn("candidate_syndrome", ttr)
            self.assertIn("tcm_factors", ttr)
            self.assertIn("evidence_for", ttr)
            self.assertIn("confidence", ttr)

    def test_m2_1_syndrome_comparison_has_status(self):
        """syndrome_comparison 包含状态字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
        )
        sc = result.get("syndrome_comparison", {})
        self.assertIn("status", sc)

    def test_m2_1_acute_disease_classified_correctly(self):
        """外感病 disease_type 应为外感/急性感染"""
        disease_type, framework = self.m2._classify_disease_type(
            "肺炎", {"symptoms": ["发热", "咳嗽"]}
        )
        self.assertIn("外感", disease_type)
        self.assertIn("卫气营血", framework)

    def test_m2_1_chronic_disease_classified_correctly(self):
        """慢病 disease_type 应为内伤/慢病"""
        disease_type, framework = self.m2._classify_disease_type(
            "慢性胃炎", {"symptoms": ["胃脘不适", "纳差"]}
        )
        self.assertIn("内伤", disease_type)
        self.assertIn("脏腑", framework)

    def test_m2_1_pathology_track_returns_expected_structure(self):
        """_run_pathology_track 返回结构化病理轨结果"""
        syndromes = {"test_syndrome": {"trigger": "<test>发热咳嗽", "pathology": "热、痰"}}
        result = self.m2._run_pathology_track(
            "肺炎", syndromes,
            {"symptoms": ["发热", "咳嗽", "黄痰"], "tongue": "舌红", "pulse": "脉数"},
            "外感/急性感染", "卫气营血辨证",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.get("track"), "pathology_based")
        self.assertIn("inferred_pathology_stage", result)
        self.assertIn("candidate_syndrome", result)
        self.assertIn("evidence_for", result)
        self.assertIn("evidence_against", result)

    def test_m2_1_dual_track_merge_consistent(self):
        """双轨一致时返回 CONSISTENT"""
        syndromes = {"风热犯肺": {"trigger": "<风热犯肺>发热咳嗽黄痰", "pathology": "热、痰"}}
        pathology = {
            "track": "pathology_based", "disease_type": "外感", "framework": "卫气营血",
            "inferred_pathology_stage": "表证期", "tcm_pathogenesis": "热",
            "candidate_syndrome": "风热犯肺", "evidence_for": [], "evidence_against": [], "confidence": 0.6,
        }
        tcm = {
            "selected_syndrome": {"name": "风热犯肺", "reason": "症状匹配"},
            "matched_symptoms": ["发热"], "matched_tongue_pulse": "舌红",
            "matched_pathology": "热", "confidence": 0.7, "missing_info": "",
        }
        merged = self.m2._merge_dual_tracks(
            "肺炎", syndromes, {"symptoms": ["发热"]},
            pathology, tcm,
        )
        self.assertEqual(merged.get("status"), "CONSISTENT")
        self.assertIn("selected_syndrome_key", merged)
        self.assertGreater(merged.get("confidence", 0), 0)

    def test_m2_1_dual_track_conflict_triggers_needs_review(self):
        """双轨冲突时标记 need_human_review"""
        syndromes = {
            "风寒束表": {"trigger": "<风寒束表>恶寒发热无汗头痛", "pathology": "寒"},
            "肝火犯肺": {"trigger": "<肝火犯肺>咳嗽胸痛烦躁易怒便秘", "pathology": "热、气滞"},
        }
        # 病理轨选风寒（寒），症状轨选肝火犯肺（热）→ 冲突
        pathology = {
            "track": "pathology_based", "disease_type": "外感", "framework": "卫气营血",
            "inferred_pathology_stage": "表寒期", "tcm_pathogenesis": "寒",
            "candidate_syndrome": "风寒束表", "evidence_for": [], "evidence_against": [], "confidence": 0.6,
        }
        tcm = {
            "selected_syndrome": {"name": "肝火犯肺", "reason": "咳嗽胸痛"},
            "matched_symptoms": ["咳嗽", "胸痛"], "matched_tongue_pulse": "",
            "matched_pathology": "热", "confidence": 0.7, "missing_info": "",
        }
        merged = self.m2._merge_dual_tracks(
            "肺炎", syndromes, {"symptoms": ["咳嗽"]},
            pathology, tcm,
        )
        self.assertEqual(merged.get("status"), "CONFLICT")

    # ═══════════════════════════════════════════════════════
    #  _fallback_parse 测试
    # ═══════════════════════════════════════════════════════

    def test_fallback_parse_returns_scorer_result_when_high_confidence(self):
        """_fallback_parse 在 scorer confidence >= 0.5 时返回 scorer 结果"""
        syndromes = {
            "邪犯肺卫（风热）": {"trigger": "发热，咳嗽，痰黄，口干", "formula_name": "桑菊饮"},
            "痰热壅肺": {"trigger": "咳嗽，咳痰黄稠，高热，胸痛", "formula_name": "麻杏石甘汤"},
        }
        symptoms = ["发热", "咳嗽", "痰黄", "口干"]
        result = self.m2._fallback_parse(syndromes, "肺炎", symptoms)
        self.assertIsNotNone(result)
        self.assertIn("selected_syndrome", result)
        self.assertIn("name", result["selected_syndrome"])
        self.assertIn("reason", result["selected_syndrome"])

    def test_fallback_parse_returns_none_when_empty_syndromes(self):
        """空证型池时返回 None"""
        result = self.m2._fallback_parse({}, "肺炎", ["咳嗽"])
        self.assertIsNone(result)

    def test_fallback_parse_does_not_cross_disease_key(self):
        """_fallback_parse 不跨病名选证型"""
        # 模拟一个只有 "肺炎" 证型池的场景
        syndromes = {
            "邪犯肺卫（风寒）": {"trigger": "恶寒，发热，无汗，痰白清稀", "formula_name": "三拗汤"},
        }
        symptoms = ["恶寒", "发热", "无汗", "痰白清稀"]
        result = self.m2._fallback_parse(syndromes, "肺炎", symptoms)
        self.assertIsNotNone(result)
        name = result["selected_syndrome"]["name"]
        self.assertIn(name, syndromes)  # 必须在证型池内
        # 验证不会跨病名（如不应返回"痢疾"的证型）
        self.assertEqual(name, "邪犯肺卫（风寒）")

    def test_fallback_parse_no_llm_call(self):
        """_fallback_parse 不调用 LLM"""
        syndromes = {
            "邪犯肺卫（风热）": {"trigger": "发热，咳嗽，痰黄", "formula_name": "桑菊饮"},
        }
        # 即使 DEEPSEEK_API_KEY 有值，fallback_parse 也不应调用 LLM
        result = self.m2._fallback_parse(syndromes, "肺炎", ["发热", "咳嗽"])
        self.assertIsNotNone(result)
        # 验证结果来自 scorer，而非 LLM
        reason = result["selected_syndrome"].get("reason", "")
        self.assertIn("证型评分器", reason)


if __name__ == "__main__":
    unittest.main()
