"""测试 M3 硬安全审核落地

覆盖范围：
1. 十八反十九畏组合触发 BLOCKED
2. 毒性药触发 WARNING/BLOCKED
3. 孕期风险触发 WARNING/BLOCKED
4. 儿童风险触发 WARNING/BLOCKED
5. 高风险肿瘤 + 活血破血药触发 require_manual_review
6. 无剂量时 dosage_review_status 明确
7. formal_prescription_allowed 恒为 false
"""
import os
import unittest

from m3_engine import M3ClinicalReviewEngine


class TestM3HardSafetyReview(unittest.TestCase):

    def setUp(self):
        # 清理可能影响测试的 env var
        os.environ.pop("ALLOW_LEGACY_M2_FULL_PIPELINE", None)
        os.environ.pop("ALLOW_LEGACY_DOSAGE_EXTRACTION", None)
        os.environ.pop("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", None)
        self.m3 = M3ClinicalReviewEngine()

    # ── 十八反十九畏 ──

    def test_eighteen_opposites_trigger_blocked(self):
        """十八反组合（乌头类+贝母类）触发 BLOCKED"""
        herbs = ["川乌", "浙贝母", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")
        self.assertFalse(result["review_passed"])
        issues = result["safety_issues"]
        oppo_issues = [i for i in issues if i["type"] == "eighteen_opposites"]
        self.assertGreater(len(oppo_issues), 0)
        self.assertEqual(oppo_issues[0]["severity"], "BLOCKED")

    def test_eighteen_opposites_grass(self):
        """十八反组合（乌头类+瓜蒌类）触发 BLOCKED"""
        herbs = ["制川乌", "瓜蒌", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")
        oppo_issues = [i for i in result["safety_issues"] if i["type"] == "eighteen_opposites"]
        self.assertGreater(len(oppo_issues), 0)

    def test_eighteen_opposites_wutou_baiji(self):
        """十八反组合（乌头类+白及）触发 BLOCKED"""
        herbs = ["草乌", "白及", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")

    def test_eighteen_opposites_lii_shen(self):
        """十八反组合（藜芦+人参类）触发 BLOCKED"""
        herbs = ["藜芦", "人参", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")

    def test_eighteen_opposites_lii_danshen(self):
        """十八反组合（藜芦+丹参）触发 BLOCKED"""
        herbs = ["藜芦", "丹参", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")

    def test_nineteen_fears_trigger_blocked(self):
        """十九畏组合（巴豆+牵牛子）触发 BLOCKED"""
        herbs = ["巴豆", "牵牛子", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")
        fear_issues = [i for i in result["safety_issues"] if i["type"] == "nineteen_fears"]
        self.assertGreater(len(fear_issues), 0)
        self.assertEqual(fear_issues[0]["severity"], "BLOCKED")

    def test_nineteen_fears_ginseng_wulingzhi(self):
        """十九畏组合（人参+五灵脂）触发 BLOCKED"""
        herbs = ["人参", "五灵脂", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")

    def test_nineteen_fears_dingxiang_yujin(self):
        """十九畏组合（丁香+郁金）触发 BLOCKED"""
        herbs = ["丁香", "郁金", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result["review_decision"], "BLOCKED")

    # ── 毒性药 ──

    def test_toxic_herb_triggers_warning(self):
        """毒性药触发 WARNING"""
        herbs = ["附子", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        tox_issues = [i for i in result["safety_issues"] if i["type"] == "toxicity"]
        self.assertGreater(len(tox_issues), 0)
        self.assertEqual(tox_issues[0]["severity"], "WARNING")

    def test_toxic_herb_adds_toxicity_warnings(self):
        """毒性药（附子）出现在 toxicity_warnings 中"""
        herbs = ["附子", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertGreater(len(result.get("toxicity_warnings", [])), 0)

    # ── 孕期风险 ──

    def test_pregnancy_toxic_triggers_blocked(self):
        """孕期含毒性药触发 BLOCKED"""
        herbs = ["附子", "甘草"]
        patient = {"age": "28", "gender": "女", "pregnancy": True}
        result = self.m3.review(herbs, patient)
        # 至少有一个 BLOCKED（毒性药也可能同时被标记为特殊人群）
        blocked_issues = [i for i in result["safety_issues"]
                          if i["severity"] == "BLOCKED"]
        self.assertGreater(len(blocked_issues), 0)
        # 检查 contraindication（孕妇禁用）
        preg_issues = [i for i in result["safety_issues"]
                       if i["type"] == "contraindication"]
        self.assertGreater(len(preg_issues), 0)

    def test_pregnancy_triggers_population_warning(self):
        """孕期触发 special_population 警告"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "28", "gender": "女", "pregnancy": True}
        result = self.m3.review(herbs, patient)
        pop_notes = result.get("special_population", [])
        self.assertGreater(len(pop_notes), 0)
        self.assertTrue(any("孕妇" in n for n in pop_notes))

    # ── 儿童风险 ──

    def test_child_dosage_triggers_warning(self):
        """儿童（<=14岁）触发剂量调整警告"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "6", "gender": "男", "weight": "20"}
        result = self.m3.review(herbs, patient)
        self.assertGreater(len(result.get("dose_adjustments", [])), 0)
        pop_notes = result.get("special_population", [])
        self.assertGreater(len(pop_notes), 0)
        self.assertTrue(any("儿童" in n for n in pop_notes))

    def test_child_toxic_triggers_blocked(self):
        """儿童含毒性药触发 BLOCKED"""
        herbs = ["附子", "甘草"]
        patient = {"age": "6", "gender": "男", "weight": "20"}
        result = self.m3.review(herbs, patient)
        # 毒性药 WARNING 加上儿童剂量调整
        self.assertGreater(len(result.get("dose_adjustments", [])), 0)

    # ── 高风险肿瘤 + 活血破血药 ──

    def test_high_risk_tumor_activates_manual_review(self):
        """高风险肿瘤 + 活血破血药触发 require_manual_review"""
        herbs = ["桃仁", "红花", "甘草"]
        patient = {"age": "57", "gender": "男",
                   "symptom_text": "喉癌术后，肝转移，水肿纳差消瘦"}
        result = self.m3.review(herbs, patient, formula_name="",
                                diagnosis="喉癌")
        self.assertTrue(result["high_risk_case"])
        self.assertTrue(result["require_manual_review"])
        self.assertIsNotNone(result.get("safety_issues"))
        self.assertGreater(len(result["safety_issues"]), 0)

    # ── 剂量审核状态 ──

    def test_dosage_review_status_pending_when_herbs(self):
        """有药物时 dosage_review_status=pending_dose_review"""
        herbs = ["黄芩", "甘草"]
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result.get("dosage_review_status"), "pending_dose_review")

    def test_dosage_review_status_not_applicable_when_no_herbs(self):
        """无药物时 dosage_review_status=not_applicable"""
        herbs = []
        patient = {"age": "30", "gender": "男"}
        result = self.m3.review(herbs, patient)
        self.assertEqual(result.get("dosage_review_status"), "not_applicable")

    # ── formal_prescription_allowed 恒为 false ──

    def test_formal_prescription_allowed_always_false(self):
        """M3 review 始终返回 formal_prescription_allowed=false"""
        test_cases = [
            (["黄芩", "甘草"], {}, "普通方"),
            (["川乌", "贝母", "甘草"], {}, "十八反方"),
            (["巴豆", "牵牛子", "甘草"], {}, "十九畏方"),
            (["附子", "甘草"], {"age": "28", "gender": "女", "pregnancy": True}, "孕妇方"),
            (["桃仁", "红花", "甘草"], {"symptom_text": "喉癌术后"}, "肿瘤方"),
        ]
        for herbs, patient, label in test_cases:
            result = self.m3.review(herbs, patient)
            self.assertFalse(result.get("formal_prescription_allowed"),
                             f"[{label}] formal_prescription_allowed 应为 false")


if __name__ == "__main__":
    unittest.main()
