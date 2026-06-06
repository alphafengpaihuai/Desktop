"""测试 M2 v2 Code-First Spec 合规性

测试内容：
1. M2-1 不依赖 LLM 也能选证型（API 可用性无关）
2. LLM 不得覆盖高置信 code-first Top1（当前无 LLM 调用，自动满足）
3. candidate_scores 存在于 internal trace
4. 生产展示不得暴露工程字段（_strip_internal_trace_fields）
5. M2-1 不输出 formula/herbs/dosage
6. M2-2 才输出 formula_candidates
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector


class TestM2PromptV2Alignment(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m2 = M2SyndromeSelector()

    # ── 1. M2-1 不依赖 LLM 也能选证型 ──

    def test_m2_1_runs_without_llm(self):
        """M2-1 在无 API 环境下仍能输出证型选择"""
        # 用 will 模式：不会因无 API 而报错或返回空
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄",
            pulse="脉数",
        )
        self.assertIn("status", result)
        st = result.get("syndrome_trace", {})
        self.assertIn("syndrome_name", st)
        self.assertTrue(st["syndrome_name"], "无 API 环境下 M2-1 应输出证型名称")

    def test_m2_1_no_api_no_error(self):
        """M2-1 无 LLM 可用时不应抛出异常"""
        try:
            result = self.m2.run_m2_1_syndrome_reasoning(
                "急性上呼吸道感染",
                symptoms=["咽痛"],
                tongue="舌红",
            )
            self.assertEqual(result.get("status"), "PASS")
        except Exception as e:
            self.fail(f"M2-1 在无 LLM 环境下抛异常: {e}")

    # ── 2. candidate_scores 存在于 internal trace ──

    def test_candidate_scores_in_trace(self):
        """candidate_scores 存在于 syndrome_trace 中"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄",
            pulse="脉数",
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           "candidate_scores 应存在于 syndrome_trace 中")
        first = cs[0]
        self.assertIn("syndrome_name", first)
        self.assertIn("score", first)
        self.assertIn("confidence", first)

    def test_candidate_scores_has_scoring_dims(self):
        """candidate_scores 每项包含细分维度"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0)
        first = cs[0]
        for key in ["symptom_match", "tongue_match", "pathology_match",
                     "cold_heat_match", "matched_symptoms", "matched_pathology"]:
            self.assertIn(key, first, f"candidate_scores 应包含 {key}")

    # ── 3. 生产展示不得暴露工程字段 ──

    def test_strip_internal_trace_removes_candidate_scores(self):
        """_strip_internal_trace_fields 移除 candidate_scores"""
        raw = {
            "syndrome_trace": {
                "syndrome_name": "风热犯表",
                "candidate_scores": [{"syndrome_name": "风热犯表", "score": 30}],
                "matched_symptoms": ["发热"],
            },
            "status": "PASS",
        }
        cleaned = self.m2._strip_internal_trace_fields(raw)
        st = cleaned.get("syndrome_trace", {})
        self.assertNotIn("candidate_scores", st,
                         "生产展示应移除 candidate_scores")
        self.assertNotIn("matched_symptoms", st,
                         "生产展示应移除 matched_symptoms")
        self.assertIn("syndrome_name", st,
                      "生产展示应保留 syndrome_name")

    def test_strip_internal_trace_preserves_core_fields(self):
        """_strip_internal_trace_fields 保留核心展示字段"""
        raw = {
            "syndrome_trace": {
                "syndrome_name": "肝经郁热",
                "candidate_scores": [],
                "reasoning_summary": "匹配依据",
            },
            "primary_disease": "带状疱疹",
            "status": "PASS",
        }
        cleaned = self.m2._strip_internal_trace_fields(raw)
        st = cleaned.get("syndrome_trace", {})
        self.assertIn("syndrome_name", st, "syndrome_name 应保留")
        self.assertIn("reasoning_summary", st, "reasoning_summary 应保留")
        self.assertEqual(cleaned.get("primary_disease"), "带状疱疹",
                         "primary_disease 应保留")
        self.assertEqual(cleaned.get("status"), "PASS", "status 应保留")

    # ── 4. M2-1 不输出 formula/herbs/dosage ──

    def test_m2_1_no_formula(self):
        """M2-1 输出不含 formula"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertNotIn("formula", result,
                         "M2-1 不应包含 formula")

    def test_m2_1_no_herbs(self):
        """M2-1 输出不含 herbs"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertNotIn("herbs", result,
                         "M2-1 不应包含 herbs")

    def test_m2_1_no_dosage(self):
        """M2-1 输出不含 dosage/dose"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertNotIn("dosage", result)
        self.assertNotIn("dose", result)

    def test_m2_1_strips_forbidden_from_nested(self):
        """M2-1 _strip_forbidden_prescription_fields 递归清理嵌套字段"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "带状疱疹",
            symptoms=["刺痛"],
            tongue="舌红",
        )
        # 确保任何层级的 dosage 都被移除
        self._assert_no_forbidden_recursive(result)

    def _assert_no_forbidden_recursive(self, obj, path=""):
        forbidden = {"formula", "herbs", "dosage", "dose",
                     "prescription_text", "final_formula", "用法", "疗程"}
        if isinstance(obj, dict):
            for key, val in obj.items():
                self.assertNotIn(key, forbidden,
                                 f"路径 {path}.{key} 包含禁用字段")
                self._assert_no_forbidden_recursive(val, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._assert_no_forbidden_recursive(item, f"{path}[{i}]")

    # ── 5. M2-2 才输出 formula_candidates ──

    def test_m2_1_has_no_formula_candidates(self):
        """M2-1 输出不含 formula_candidates"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertNotIn("formula_candidates", result,
                         "M2-1 不应包含 formula_candidates")

    def test_m2_2_outputs_formula_candidates(self):
        """M2-2 输出包含 formula_candidates"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌红苔黄",
            pulse="脉数",
        )
        st = m2_1.get("syndrome_trace", {})
        result = self.m2.run_m2_2_formula_candidates(
            "肺炎",
            syndrome_trace=st,
            symptoms=["发热", "咳嗽", "黄痰"],
        )
        fc = result.get("formula_candidates", [])
        self.assertGreater(len(fc), 0,
                           "M2-2 应输出 formula_candidates")
        first = fc[0]
        self.assertIn("formula_name", first,
                      "formula_candidates 应包含 formula_name")
        self.assertIn("herbs", first,
                      "formula_candidates 应包含 herbs")

    def test_m2_2_formula_candidate_has_required_fields(self):
        """M2-2 formula_candidates 包含必需字段"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        st = m2_1.get("syndrome_trace", {})
        result = self.m2.run_m2_2_formula_candidates(
            "急性上呼吸道感染",
            syndrome_trace=st,
            symptoms=["发热", "咳嗽"],
        )
        fc = result.get("formula_candidates", [])
        self.assertGreater(len(fc), 0)
        required = {"disease_name", "syndrome_name", "formula_name", "herbs", "source"}
        for key in required:
            self.assertIn(key, fc[0],
                          f"formula_candidate 应包含 {key}")


if __name__ == "__main__":
    unittest.main()
