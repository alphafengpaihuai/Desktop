"""急性支气管炎联调修复 — 因果贝叶斯 / M2-2 空方 / M2-3 节点加减。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from m2_engine import M2SyndromeSelector
from m3_engine import M3ClinicalReviewEngine


class TestAcuteBronchitisWindColdWithConstraintHeat(unittest.TestCase):
    """本病案：急性一周 + 风寒夹郁热，不得推气阴两虚。"""

    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m2 = M2SyndromeSelector()

    def test_acute_bronchitis_wind_cold_with_constraint_heat_not_qiyin(self):
        symptoms = [
            "咳嗽一周",
            "夜间咳嗽重",
            "支气管炎",
            "鼻塞少",
            "咽痛",
            "咽痒",
            "口干",
            "口苦",
            "输液2天无效",
        ]
        signs = ["舌暗红苔白腻"]
        labs = ["双肺呼吸音粗"]

        result = self.m2.process(
            primary_disease="急性支气管炎",
            symptoms=symptoms,
            signs=signs,
            labs=labs,
            age="37",
        )

        self.assertEqual(result.get("status"), "PASS")
        self.assertTrue(result.get("candidate_only"))
        self.assertTrue(result.get("must_enter_m3"))
        self.assertFalse(result.get("formal_prescription_allowed"))

        syndrome = (
            (result.get("syndrome_differentiation") or {})
            .get("selected_syndrome", {})
            .get("name")
            or (result.get("syndrome_trace") or {}).get("syndrome_name", "")
        )
        self.assertNotIn(syndrome, ("气阴两虚", "阴虚咳嗽"))
        self.assertIn(
            syndrome,
            ("风寒袭肺", "痰湿咳嗽", "痰热咳嗽", "风热犯肺"),
            msg=f"unexpected syndrome: {syndrome}",
        )

        posterior_band = result.get("posterior_band") or (
            (result.get("selected_syndrome_node") or {}).get("posterior_band")
        )
        self.assertIn(posterior_band, ("LOW", "MEDIUM", None))
        if posterior_band:
            self.assertNotEqual(posterior_band, "HIGH")

        herbs = (result.get("bound_formula") or {}).get("herbs") or []
        self.assertGreaterEqual(len(herbs), 3)


class TestM2_2EmptyHerbsReturnsNoCandidate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m2 = M2SyndromeSelector()

    def test_m2_2_empty_herbs_returns_no_candidate_or_review(self):
        out = self.m2.run_m2_2_formula_candidates(
            primary_disease="急性支气管炎",
            syndrome_trace={"syndrome_name": "气阴两虚"},
            symptoms=["咳嗽"],
        )
        self.assertEqual(out.get("status"), "NO_CANDIDATE")
        self.assertTrue(out.get("no_candidate"))
        self.assertTrue(out.get("candidate_only"))
        self.assertTrue(out.get("must_enter_m3"))
        self.assertFalse(out.get("formal_prescription_allowed"))


class TestM3EmptyHerbsNotApproved(unittest.TestCase):

    def setUp(self):
        os.environ.pop("ALLOW_LEGACY_M2_FULL_PIPELINE", None)
        self.m3 = M3ClinicalReviewEngine()

    def test_m3_empty_herbs_not_approved(self):
        result = self.m3.review(
            herbs=[],
            patient={"age": "37", "gender": "女"},
            formula_name="麦门冬汤",
            diagnosis="急性支气管炎",
        )
        self.assertNotEqual(result["review_decision"], "APPROVED")
        self.assertFalse(result["review_passed"])
        empty_issues = [i for i in result["safety_issues"] if i["type"] == "empty_formula_herbs"]
        self.assertGreater(len(empty_issues), 0)
        self.assertFalse(result.get("formal_prescription_allowed"))


class TestM2_3UsesNodeClinicalModificationsFirst(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m2 = M2SyndromeSelector()

    def test_m2_3_uses_node_clinical_modifications_first(self):
        symptoms = [
            "咳嗽一周",
            "咽痛",
            "咽痒",
            "口干",
            "口苦",
            "鼻塞",
            "苔白腻",
        ]
        base_herbs = ["麻黄", "杏仁", "荆芥", "紫菀", "百部", "白前", "陈皮", "桔梗", "甘草"]
        out = self.m2.run_m2_3_modification_candidates(
            primary_disease="急性支气管炎",
            formula_herbs=base_herbs,
            symptoms=symptoms,
            syndrome_name="风寒袭肺",
        )
        self.assertEqual(out.get("status"), "PASS")
        mods = out.get("modification_candidates") or []
        self.assertGreater(len(mods), 0)
        sources = {m.get("source") for m in mods}
        self.assertIn("node_clinical_modifications", sources)
        herb_names = {m.get("herb_name") for m in mods}
        self.assertTrue(
            herb_names & {"鱼腥草", "辛夷", "法半夏", "瓜蒌皮"},
            msg=f"expected node mods, got {herb_names}",
        )
        for m in mods:
            if m.get("source") == "node_clinical_modifications":
                self.assertTrue(m.get("target_symptom") or m.get("matched_rule"))
                self.assertTrue(m.get("matched_reason"))


if __name__ == "__main__":
    unittest.main()
