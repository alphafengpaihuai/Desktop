import os
import unittest

from m3_engine import M3ClinicalReviewEngine


class M3HighRiskTumorSafetyGuardTest(unittest.TestCase):
    """验证 M3 高风险肿瘤病例的安全审核"""

    def setUp(self):
        # 清理可能影响测试的 env var
        os.environ.pop("ALLOW_LEGACY_M2_FULL_PIPELINE", None)
        os.environ.pop("ALLOW_LEGACY_DOSAGE_EXTRACTION", None)
        os.environ.pop("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", None)
        self.m3 = M3ClinicalReviewEngine()

    # ── 高风险肿瘤病例识别 ──

    def test_laryngeal_cancer_diagnosis_triggers_high_risk(self):
        """诊断名含"癌"触发 high_risk_case=true"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": ""}
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        self.assertTrue(result["require_manual_review"])
        self.assertIn("诊断含【癌】关键词", str(result["high_risk_reasons"]))

    def test_tumor_with_postop_triggers_high_risk(self):
        """患者有"术后"风险因素触发 high_risk_case=true"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": "",
                   "symptom_text": "2024年11月做了喉癌手术"}
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        # 诊断含"癌"也是高风险的触发条件，两个都算
        has_cancer_or_postop = any(("癌" in r or "术后" in r) for r in result["high_risk_reasons"])
        self.assertTrue(has_cancer_or_postop, f"high_risk_reasons 应包含癌或术后: {result['high_risk_reasons']}")

    def test_tumor_with_metastasis_triggers_high_risk(self):
        """患者有"转移"风险因素触发 high_risk_case=true"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": "",
                   "symptom_text": "肝转移骨转移"}
        result = self.m3.review(herbs, patient, formula_name="",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        self.assertIn("转移", str(result["high_risk_reasons"]))

    def test_tumor_with_edema_triggers_high_risk(self):
        """患者有"水肿"风险因素触发 high_risk_case=true"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": "",
                   "symptom_text": "近二天下肢水肿"}
        result = self.m3.review(herbs, patient, formula_name="",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        self.assertIn("水肿", str(result["high_risk_reasons"]))

    def test_tumor_with_nacha_triggers_high_risk(self):
        """患者有"纳差"风险因素触发 high_risk_case=true"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": "",
                   "symptom_text": "纳差消瘦乏力"}
        result = self.m3.review(herbs, patient, formula_name="",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        self.assertIn("纳差", str(result["high_risk_reasons"]))

    # ── 活血破血药检测 ──

    def test_blood_activating_herbs_detected(self):
        """活血破血药（桃仁、红花、三棱、莪术、水蛭、王不留行）被检测"""
        herbs = ["桃仁", "红花", "三棱", "莪术", "水蛭", "王不留行", "黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": ""}
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        self.assertTrue(result["blood_activating_herbs"])
        for _h in ["桃仁", "红花", "三棱", "莪术", "水蛭", "王不留行"]:
            self.assertIn(_h, result["blood_activating_herbs"])
        # high_risk_case 应该为 true（诊断含"癌"+活血破血药）
        self.assertTrue(result["high_risk_case"])
        self.assertTrue(result["require_manual_review"])

    def test_blood_activating_adds_safety_issue(self):
        """活血破血药触发 safety_issues 条目"""
        herbs = ["桃仁", "红花", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": ""}
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        issues = result["safety_issues"]
        blood_issues = [i for i in issues if i["type"] == "blood_activating_herbs"]
        self.assertGreater(len(blood_issues), 0)
        self.assertIn("活血破血药", blood_issues[0]["message"])

    # ── 叶有亮病例综合验证 ──

    def test_ye_you_liang_full_case_high_risk(self):
        """叶有亮完整病例：喉癌术后+肝转移+骨转移+水肿+活血破血药→高风险管理"""
        herbs = ["桃仁", "红花", "赤芍", "当归", "生地黄", "玄参", "柴胡",
                 "枳壳", "桔梗", "生甘草", "三棱", "莪术", "水蛭", "丹皮",
                 "王不留行", "黄连", "麦冬", "茯苓皮"]
        patient = {
            "age": "57", "gender": "男", "weight": "",
            "symptom_text": "2024年11月做了喉癌手术，2025年6月发现肝转移，"
                           "骨转移。近二天下肢水肿，食纳差，消瘦",
        }
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        # high_risk_case 必须为 true
        self.assertTrue(result["high_risk_case"],
                        "叶有亮病例应标记为 high_risk_case=true")
        # require_manual_review 必须为 true
        self.assertTrue(result["require_manual_review"],
                        "叶有亮病例应标记 require_manual_review=true")
        # safety_issues 不能为空
        self.assertGreater(len(result["safety_issues"]), 0,
                           "叶有亮病例 safety_issues 不应为空")
        # 应有 high_risk_tumor 类型的 issue
        issue_types = [i["type"] for i in result["safety_issues"]]
        self.assertIn("high_risk_tumor", issue_types,
                      "叶有亮病例应触发 high_risk_tumor 类型")
        # 应有 blood_activating_herbs 类型
        self.assertIn("blood_activating_herbs", issue_types,
                      "叶有亮病例应触发 blood_activating_herbs 类型")

    # ── 非高风险病例不应误报 ──

    def test_non_tumor_case_no_high_risk(self):
        """普通感冒不应触发 high_risk_case"""
        herbs = ["金银花", "连翘", "薄荷", "甘草"]
        patient = {"age": "25", "gender": "女", "weight": "",
                   "symptom_text": "发热咽痛咳嗽2天"}
        result = self.m3.review(herbs, patient, formula_name="银翘散",
                                dosage_str="", diagnosis="急性上呼吸道感染")
        self.assertFalse(result["high_risk_case"],
                         "普通感冒不应标记为 high_risk_case")
        self.assertFalse(result["require_manual_review"],
                         "普通感冒不应 require_manual_review")
        self.assertEqual(len(result["blood_activating_herbs"]), 0,
                         "普通感冒不应检出活血破血药")

    def test_non_tumor_but_blood_herbs_still_flagged(self):
        """非肿瘤但有活血破血药也标记 require_manual_review"""
        herbs = ["桃仁", "红花", "甘草"]
        patient = {"age": "30", "gender": "女", "weight": "",
                   "symptom_text": "跌打损伤"}
        result = self.m3.review(herbs, patient, formula_name="",
                                dosage_str="", diagnosis="软组织损伤")
        # 非高风险但有活血破血药，仍然 require_manual_review
        self.assertTrue(result["high_risk_case"])
        self.assertTrue(result["require_manual_review"])

    # ── formal_prescription_allowed 始终 false ──

    def test_formal_prescription_allowed_always_false(self):
        """M3 review 始终返回 formal_prescription_allowed=false"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "57", "gender": "男", "weight": "",
                   "symptom_text": "喉癌术后"}
        result = self.m3.review(herbs, patient, formula_name="会厌逐瘀汤",
                                dosage_str="", diagnosis="喉癌")
        self.assertFalse(result["formal_prescription_allowed"])

        # 普通病例也 false
        herbs2 = ["金银花", "连翘", "甘草"]
        patient2 = {"age": "25", "gender": "女", "weight": ""}
        result2 = self.m3.review(herbs2, patient2, formula_name="银翘散",
                                 dosage_str="", diagnosis="急性上呼吸道感染")
        self.assertFalse(result2["formal_prescription_allowed"])


if __name__ == "__main__":
    unittest.main()
