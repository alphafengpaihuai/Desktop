"""测试 M2-1 internal trace 不泄漏至生产展示文本

测试内容：
1. 运行一个肺炎痰热病例；
2. internal raw result 中允许存在 candidate_scores / evidence_trace / matched_symptoms；
3. production display text 中不得出现：
   - candidate_scores, evidence_trace, input_trace
   - matched_symptoms, matched_pathology, matched_tongue_pulse
   - confidence_score
4. production display text 只允许展示：
   - 诊断, 证型, 简要辨证依据, 是否需人工复核
5. formal_prescription_allowed = false；
6. 不输出正式处方字段。
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector


def _simulate_production_display(m2_raw_result: dict) -> str:
    """模拟生产展示层的文本渲染。

    生产展示只取以下字段构造纯文本：
    - primary_disease → 诊断
    - syndrome_trace.syndrome_name → 证型
    - syndrome_trace.reasoning_summary → 简要辨证依据
    - needs_manual_review → 是否需人工复核
    """
    lines = []
    disease = m2_raw_result.get("primary_disease", "")
    if disease:
        lines.append(f"诊断：{disease}")

    st = m2_raw_result.get("syndrome_trace", {}) or {}
    syndrome = st.get("syndrome_name", "")
    if syndrome:
        lines.append(f"证型：{syndrome}")

    reason = st.get("reasoning_summary", "")
    if reason:
        lines.append(f"辨证依据：{reason}")

    needs_review = m2_raw_result.get("needs_manual_review", m2_raw_result.get("syndrome_trace", {}).get("needs_manual_review", False))
    lines.append(f"需人工复核：{'是' if needs_review else '否'}")

    return "\n".join(lines)


# 生产展示文本中禁止出现的 trace 字段关键词
FORBIDDEN_TRACE_KEYWORDS = [
    "candidate_scores",
    "evidence_trace",
    "input_trace",
    "matched_symptoms",
    "matched_pathology",
    "matched_tongue_pulse",
    "confidence_score",
]


class TestM2ProductionDisplayTraceFilter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m2 = M2SyndromeSelector()

    # ── 1. internal raw result 允许存在 trace 字段 ──

    def test_internal_raw_contains_candidate_scores(self):
        """internal raw result 允许存在 candidate_scores"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿", "口干", "口苦"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
        )
        st = raw.get("syndrome_trace", {})
        self.assertIn("candidate_scores", st,
                      "internal raw 应允许存在 candidate_scores")
        cs = st["candidate_scores"]
        self.assertGreater(len(cs), 0, "candidate_scores 应为非空列表")
        self.assertIn("score", cs[0],
                      "candidate_scores 每项应含 score")

    def test_internal_raw_contains_evidence_trace(self):
        """internal raw result 允许存在 evidence_trace"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿"],
            tongue="舌红苔黄腻",
        )
        self.assertIn("evidence_trace", raw,
                      "internal raw 应允许存在 evidence_trace")
        self.assertIsInstance(raw["evidence_trace"], list)

    def test_internal_raw_contains_matched_symptoms(self):
        """syndrome_trace 可含 matched_symptoms"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        st = raw.get("syndrome_trace", {})
        self.assertIn("matched_symptoms", st,
                      "internal syndrome_trace 可含 matched_symptoms")

    # ── 2. production display text 不得出现 trace 字段 ──

    def test_production_display_no_candidate_scores(self):
        """production display text 不含 candidate_scores"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿", "口干", "口苦"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
        )
        text = _simulate_production_display(raw)
        self.assertNotIn("candidate_scores", text,
                         "生产展示文本不应含 candidate_scores")
        self.assertNotIn("score", text,
                         "生产展示文本不应含 score")

    def test_production_display_no_evidence_trace(self):
        """production display text 不含 evidence_trace"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        self.assertNotIn("evidence_trace", text)

    def test_production_display_no_input_trace(self):
        """production display text 不含 input_trace"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        self.assertNotIn("input_trace", text)

    def test_production_display_no_matched_fields(self):
        """production display text 不含 matched_symptoms / matched_pathology / matched_tongue_pulse"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
        )
        text = _simulate_production_display(raw)
        for kw in ["matched_symptoms", "matched_pathology", "matched_tongue_pulse"]:
            self.assertNotIn(kw, text,
                             f"生产展示文本不应含 {kw}")

    def test_production_display_no_confidence_score(self):
        """production display text 不含 confidence_score"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        # "confidence" 可能出现在字段名中，只检查 "confidence_score" 和 "confidence" 直接拼接
        self.assertNotIn("confidence_score", text,
                         "生产展示文本不应含 confidence_score")

    def test_production_display_no_trace_keywords_bruteforce(self):
        """production display text 暴力搜索禁止关键词（含JSON序列化）"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
        )
        text = _simulate_production_display(raw)
        for kw in FORBIDDEN_TRACE_KEYWORDS:
            self.assertNotIn(kw, text,
                             f"生产展示文本不应含 '{kw}'")

    # ── 3. production display 只能展示允许字段 ──

    def test_production_display_contains_diagnosis(self):
        """production display text 包含诊断"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        self.assertIn("诊断", text,
                      "生产展示应包含诊断信息")

    def test_production_display_contains_syndrome(self):
        """production display text 包含证型"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        self.assertIn("证型", text,
                      "生产展示应包含证型")

    def test_production_display_contains_manual_review_flag(self):
        """production display text 包含是否需人工复核"""
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        )
        text = _simulate_production_display(raw)
        self.assertIn("需人工复核", text,
                      "生产展示应包含是否需人工复核")

    # ── 4. formal_prescription_allowed = false ──

    def test_formal_prescription_not_allowed(self):
        """formal_prescription_allowed 恒为 false（M2 各入口）"""
        # M2-1
        r1 = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertFalse(r1.get("formal_prescription_allowed", True),
                         "M2-1 formal_prescription_allowed 应为 false")

        # M2-2（从 M2-1 trace 接续）
        st = r1.get("syndrome_trace", {})
        r2 = self.m2.run_m2_2_formula_candidates(
            "肺炎",
            syndrome_trace=st,
            symptoms=["发热"],
        )
        self.assertFalse(r2.get("formal_prescription_allowed", True),
                         "M2-2 formal_prescription_allowed 应为 false")

        # process()
        rp = self.m2.process(
            primary_disease="肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        self.assertFalse(rp.get("formal_prescription_allowed", True),
                         "M2 process() formal_prescription_allowed 应为 false")

    # ── 5. 不输出正式处方字段 ──

    def test_m2_1_no_prescription_fields(self):
        """M2-1 不输出正式处方字段"""
        forbidden = {"dosage", "dose", "prescription_text",
                     "final_formula", "用法", "疗程"}
        raw = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热"],
            tongue="舌红",
        )
        for key in forbidden:
            self.assertNotIn(key, raw,
                             f"M2-1 不应包含 '{key}'")
        # M2-1 也不应包含 formula/herbs（这些是 M2-2 的范畴）
        self.assertNotIn("formula", raw,
                         "M2-1 不应包含 formula 字段")

    def test_m2_2_contains_herbs_but_no_dosage(self):
        """M2-2 可含 herbs 但不应含 dosage / dose / 用法 / 疗程"""
        st = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽"],
            tongue="舌红",
        ).get("syndrome_trace", {})
        r2 = self.m2.run_m2_2_formula_candidates(
            "肺炎",
            syndrome_trace=st,
            symptoms=["发热", "咳嗽"],
        )
        fc = r2.get("formula_candidates", [])
        if fc:
            herbs = fc[0].get("herbs", [])
            # herbs 存在是允许的（M2-2 范畴）
            # 但 dosage 不应出现
            self.assertNotIn("dosage", r2)
            self.assertNotIn("dose", r2)
            self.assertNotIn("用法", r2)
            self.assertNotIn("疗程", r2)

    # ── 6. _strip_internal_trace_fields 方法正确工作 ──

    def test_strip_internal_trace_removes_trace_fields(self):
        """_strip_internal_trace_fields 移除了所有内部 trace 字段"""
        raw = {
            "primary_disease": "肺炎",
            "status": "PASS",
            "syndrome_trace": {
                "syndrome_name": "痰热壅肺",
                "reasoning_summary": "匹配",
                "candidate_scores": [{"syndrome_name": "痰热壅肺", "score": 30}],
                "matched_symptoms": ["发热"],
                "matched_pathology": "热",
                "matched_tongue_pulse": "苔黄",
            },
            "evidence_trace": [{"source": "patient_symptom", "text": "发热"}],
            "input_trace": {"stage": "M2_1"},
            "formal_prescription_allowed": False,
        }
        cleaned = self.m2._strip_internal_trace_fields(raw)

        # 保留字段
        self.assertEqual(cleaned.get("primary_disease"), "肺炎")
        self.assertEqual(cleaned.get("status"), "PASS")
        st = cleaned.get("syndrome_trace", {})
        self.assertEqual(st.get("syndrome_name"), "痰热壅肺")
        self.assertEqual(st.get("reasoning_summary"), "匹配")

        # 移除字段
        self.assertNotIn("evidence_trace", cleaned)
        self.assertNotIn("input_trace", cleaned)
        self.assertNotIn("candidate_scores", st)
        self.assertNotIn("matched_symptoms", st)
        self.assertNotIn("matched_pathology", st)
        self.assertNotIn("matched_tongue_pulse", st)


if __name__ == "__main__":
    unittest.main()
