import unittest

from services.m2_prescription_service import HerbCandidate
from services.m3_review_service import M3ReviewService


TIANMA_GOUTENG_BASE = [
    "天麻", "钩藤", "石决明", "栀子", "黄芩", "川牛膝",
    "杜仲", "益母草", "桑寄生", "夜交藤", "茯神",
]


class M3DefaultKeepRegressionTest(unittest.TestCase):
    def setUp(self):
        self.m3 = M3ReviewService()

    def test_tianma_gouteng_base_formula_all_kept_without_hard_contraindication(self):
        rx = self.m3.review(
            formula_name="天麻钩藤饮加减",
            base_herbs=[HerbCandidate(name=h, source="formula") for h in TIANMA_GOUTENG_BASE],
            add_herbs=[
                HerbCandidate(name="蝉蜕", source="case_rag", evidence_detail="RAG: 抽动眨眼"),
                HerbCandidate(name="菊花", source="case_rag", evidence_detail="RAG: 结膜充血"),
            ],
            patient_info={"age": "9岁", "gender": "男", "risk_factors": []},
        )
        kept_names = [h.name for h in rx.herbs]
        for name in TIANMA_GOUTENG_BASE:
            self.assertIn(name, kept_names)
        self.assertEqual(rx.removed_herbs, [])
        self.assertTrue(all(h.herb_layer == "core_formula_herb" and h.locked for h in rx.herbs[:11]))
        self.assertTrue(all(h.action in M3ReviewService.ALLOWED_ACTIONS for h in rx.herbs))

    def test_uncertain_pharmacology_never_removes_base_herb(self):
        rx = self.m3.review(
            formula_name="天麻钩藤饮加减",
            base_herbs=[
                HerbCandidate(name="杜仲", source="formula"),
                HerbCandidate(name="桑寄生", source="formula"),
                HerbCandidate(name="益母草", source="formula"),
            ],
            add_herbs=[],
            patient_info={"age": "9岁", "gender": "男", "risk_factors": []},
        )
        self.assertEqual([h.name for h in rx.herbs], ["杜仲", "桑寄生", "益母草"])
        self.assertEqual(rx.removed_herbs, [])
        self.assertTrue(all(h.action == "keep" for h in rx.herbs))
        self.assertTrue(all(h.removal_evidence_grade == "D" for h in rx.herbs))

    def test_only_s_or_a_evidence_auto_removes_base_herb(self):
        pregnancy_rx = self.m3.review(
            formula_name="测试方",
            base_herbs=[HerbCandidate(name="益母草", source="formula")],
            add_herbs=[],
            patient_info={"age": "30岁", "gender": "女", "risk_factors": ["pregnancy"]},
        )
        self.assertEqual(pregnancy_rx.herbs, [])
        self.assertEqual(pregnancy_rx.removed_herbs[0]["action"], "remove_due_to_hard_contraindication")
        self.assertEqual(pregnancy_rx.removed_herbs[0]["removal_evidence_grade"], "S")

        review_rx = self.m3.review(
            formula_name="测试方",
            base_herbs=[HerbCandidate(name="甘草", source="formula")],
            add_herbs=[],
            patient_info={"age": "45岁", "gender": "男", "risk_factors": ["高血压"]},
        )
        self.assertEqual([h.name for h in review_rx.herbs], ["甘草"])
        self.assertEqual(review_rx.removed_herbs, [])
        self.assertEqual(review_rx.herbs[0].action, "flag_review")
        self.assertEqual(review_rx.herbs[0].removal_evidence_grade, "B")

    def test_rag_addon_limit_and_evidence_required(self):
        add_herbs = [
            HerbCandidate(name=f"加味{i}", source="case_rag", evidence_detail=f"RAG:{i}")
            for i in range(6)
        ]
        add_herbs.append(HerbCandidate(name="无证据加味", source="case_rag", evidence_detail=""))
        rx = self.m3.review(
            formula_name="测试方",
            base_herbs=[HerbCandidate(name="天麻", source="formula")],
            add_herbs=add_herbs,
            patient_info={"age": "9岁", "gender": "男", "risk_factors": []},
        )
        addon_names = [h.name for h in rx.herbs if h.herb_layer == "rag_addon_herb"]
        self.assertEqual(len(addon_names), 5)
        self.assertTrue(any(a["name"] == "加味5" and a["action"] == "reject_addon" for a in rx.audit_actions))
        self.assertTrue(any(a["name"] == "无证据加味" and a["action"] == "reject_addon" for a in rx.audit_actions))

    def test_formula_mismatch_requires_reselection_not_pruning(self):
        rx = self.m3.review(
            formula_name="天麻钩藤饮加减",
            base_herbs=[HerbCandidate(name=h, source="formula") for h in TIANMA_GOUTENG_BASE],
            add_herbs=[],
            patient_info={
                "age": "9岁",
                "gender": "男",
                "risk_factors": [],
                "formula_overall_mismatch": True,
            },
        )
        self.assertTrue(rx.formula_reselection_required)
        self.assertEqual(rx.formula_reselection_reason, "主方整体证型不匹配，应重新选方，而非裁剪原方。")
        self.assertEqual([h.name for h in rx.herbs], TIANMA_GOUTENG_BASE)
        self.assertEqual(rx.removed_herbs, [])


if __name__ == "__main__":
    unittest.main()
