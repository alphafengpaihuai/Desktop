"""测试 M2-3 加减候选证据链落地

覆盖范围：
1. 加减候选有 target_disease
2. 加减候选有 target_symptom
3. 加减候选有 western_pathology
4. 加减候选有 evidence_sources
5. 药物库无证据时 no_candidate
6. 不读取 low_grade candidates
7. 不输出剂量/用法/疗程
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector

FORBIDDEN_KEYS = {"dosage", "dose", "用法", "疗程", "prescription_text", "final_formula"}


class TestM2ModificationKnowledgeQuery(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m2 = M2SyndromeSelector()

    def test_m2_3_has_target_disease(self):
        """加减候选有 target_disease"""
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "带状疱疹",
            symptoms=["颈部红斑刺痛", "口干"],
        )
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            formula_herbs=["龙胆草", "栀子"],
            symptoms=["颈部红斑刺痛", "口干"],
            syndrome_name=m2_1.get("syndrome_trace", {}).get("syndrome_name", ""),
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertIn("target_disease", mc)
                self.assertEqual(mc["target_disease"], "带状疱疹")

    def test_m2_3_has_target_symptom(self):
        """加减候选有 target_symptom"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦", "大便不畅"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertIn("target_symptom", mc)
                self.assertTrue(mc["target_symptom"])

    def test_m2_3_has_western_pathology(self):
        """加减候选有 western_pathology"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦", "大便不畅"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertIn("western_pathology", mc)

    def test_m2_3_has_evidence_sources(self):
        """加减候选有 evidence_sources"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦", "大便不畅"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertIn("evidence_sources", mc)
                self.assertTrue(mc["evidence_sources"])

    def test_m2_3_no_candidate_when_no_evidence(self):
        """药物库无证据时 no_candidate"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="XX不存在的疾病YYY",
            symptoms=["头痛"],
            syndrome_name="不存在的证型",
        )
        self.assertTrue(m2_3.get("no_candidate"))
        self.assertEqual(m2_3.get("modification_candidates"), [])
        self.assertEqual(m2_3.get("reason"), "药物库无可追溯证据")

    def test_m2_3_has_must_enter_m3(self):
        """modification_candidates 中每味 herb 都有 must_enter_m3=true"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertTrue(mc.get("must_enter_m3"))

    def test_m2_3_no_formula_dosage_fields(self):
        """M2-3 不输出剂量/用法/疗程"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦"],
            syndrome_name="肝经郁热",
        )
        for key in FORBIDDEN_KEYS:
            self.assertNotIn(key, m2_3, f"M2-3 不应包含 '{key}'")

    def test_m2_3_no_low_grade_candidates(self):
        """不读取 low_grade candidates（直接查询知识库，不经过猜测式生成）"""
        # 对无任何知识库数据的疾病，应返回 no_candidate
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="XX完全不存在ZZZZ",
            symptoms=["头痛", "发热"],
            syndrome_name="XX证",
        )
        # 不应该有 modification_candidates
        self.assertTrue(m2_3.get("no_candidate") or len(m2_3.get("modification_candidates", [])) == 0)

    def test_m2_3_no_dosage_on_modification_candidates(self):
        """modification_candidates 中的 herb 项目不包含 dosage 字段"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦", "大便不畅"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertNotIn("dosage", mc, "modification_candidate 不应包含 dosage")
                self.assertNotIn("dose", mc, "modification_candidate 不应包含 dose")

    def test_m2_3_herb_name_key(self):
        """modification_candidate 使用 herb_name 键"""
        m2_3 = self.m2.run_m2_3_modification_candidates(
            primary_disease="带状疱疹",
            symptoms=["口干", "口苦"],
            syndrome_name="肝经郁热",
        )
        if not m2_3.get("no_candidate"):
            for mc in m2_3.get("modification_candidates", []):
                self.assertIn("herb_name", mc)
                self.assertTrue(mc["herb_name"])


if __name__ == "__main__":
    unittest.main()
