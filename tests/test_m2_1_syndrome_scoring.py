"""测试 M2-1 证型评分器雏形

覆盖范围：
1. 多证型疾病 + 热象 → 优先热证方向
2. 多证型疾病 + 寒象 → 偏寒证方向
3. 单病名单证型 → 仍输出 candidate_scores，不得跳过 trace
4. M2-1 输出不得包含方剂/药物/剂量/处方
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector


class TestM2SyndromeScoring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m2 = M2SyndromeSelector()

    # ── 多证型 + 热象 → 优先热证 ──

    def test_acute_uri_heat_syndrome_preferred(self):
        """急性上呼吸道感染 + 发热黄痰苔黄腻 → 优先风热犯表"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽", "咳黄痰", "咽痛", "口干"],
            tongue="舌尖红苔薄黄",
            pulse="脉浮数",
            cold_heat=["发热"],
        )
        st = result.get("syndrome_trace", {})
        self.assertIn("syndrome_name", st)
        self.assertIn("candidate_scores", st)
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           "多证型疾病应输出 candidate_scores")
        # 验证有热象匹配记录
        has_heat_syndrome = any("热" in (s.get("syndrome_name", "") or "")
                                for s in cs)
        self.assertTrue(has_heat_syndrome,
                        f"热象病例应包含热证候选: {[s['syndrome_name'] for s in cs]}")
        # 风热犯表应比风寒感冒得分高
        fengre_score = None
        fenghan_score = None
        for s in cs:
            if "风热" in s.get("syndrome_name", ""):
                fengre_score = s["score"]
            if "风寒" in s.get("syndrome_name", ""):
                fenghan_score = s["score"]
        if fengre_score is not None and fenghan_score is not None:
            self.assertGreater(fengre_score, fenghan_score,
                               "热象病例中风热犯表得分应高于风寒感冒")

    # ── 多证型 + 寒象 → 偏寒证 ──

    def test_acute_uri_cold_syndrome_preferred(self):
        """急性上呼吸道感染 + 怕冷痰白清稀 → 偏风寒感冒"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["怕冷", "咳嗽", "咳白痰", "痰清稀", "鼻塞", "流清涕"],
            tongue="舌淡苔薄白",
            pulse="脉浮紧",
            cold_heat=["恶寒"],
        )
        st = result.get("syndrome_trace", {})
        self.assertIn("syndrome_name", st)
        self.assertIn("candidate_scores", st)
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           "多证型疾病应输出 candidate_scores")
        # 验证有寒象匹配记录
        has_cold_syndrome = any("寒" in (s.get("syndrome_name", "") or "")
                                for s in cs)
        self.assertTrue(has_cold_syndrome,
                        f"寒象病例应包含寒证候选: {[s['syndrome_name'] for s in cs]}")
        # 风寒感冒应比风热犯表得分高
        fenghan_score = None
        fengre_score = None
        for s in cs:
            if "风寒" in s.get("syndrome_name", ""):
                fenghan_score = s["score"]
            if "风热" in s.get("syndrome_name", ""):
                fengre_score = s["score"]
        if fenghan_score is not None and fengre_score is not None:
            self.assertGreater(fenghan_score, fengre_score,
                               "寒象病例中风寒感冒得分应高于风热犯表")

    # ── 单病名单证型 ──

    def test_single_syndrome_has_scores(self):
        """单病名单证型仍输出 candidate_scores，不得跳过 trace"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "带状疱疹",
            symptoms=["颈部红斑刺痛", "口干", "口苦"],
            tongue="舌红苔薄黄",
            pulse="脉弦",
        )
        st = result.get("syndrome_trace", {})
        self.assertIn("syndrome_name", st)
        self.assertIn("candidate_scores", st)
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           "单证型也应有 candidate_scores")
        # 带状疱疹有 3 个证型，但只有评分最高的被选为主证型
        top = cs[0]
        self.assertIn("syndrome_name", top)
        self.assertIn(top["syndrome_name"],
                      ["肝经郁热", "脾虚湿蕴", "气滞血瘀（带状疱疹后遗痛）"],
                      f"带状疱疹主证型应匹配已知证型，实际={top['syndrome_name']}")

    # ── 单证型 confidence 非零 ──

    def test_single_syndrome_confidence_nonzero(self):
        """单证型 confidence 应大于 0"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "带状疱疹",
            symptoms=["颈部红斑刺痛", "口干"],
            tongue="舌红苔薄黄",
        )
        st = result.get("syndrome_trace", {})
        conf = st.get("confidence", 0)
        self.assertGreater(conf, 0.0,
                           f"单证型 confidence 应 > 0，实际={conf}")

    # ── candidate_scores 包含匹配详情 ──

    def test_candidate_scores_has_detail(self):
        """candidate_scores 每项包含细分维度得分"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽", "黄痰", "咽痛"],
            tongue="舌尖红苔薄黄",
            pulse="脉浮数",
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0)
        first = cs[0]
        for key in ["syndrome_name", "score", "confidence", "matched_symptoms",
                     "symptom_match", "tongue_match", "pulse_match",
                     "pathology_match", "cold_heat_match"]:
            self.assertIn(key, first, f"candidate_scores 应包含 {key}")

    # ── matched_symptoms 输出患者症状匹配 ──

    def test_matched_symptoms_contains_patient_symptoms(self):
        """matched_symptoms 包含患者匹配的症状关键词"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌尖红",
            pulse="脉浮",
        )
        st = result.get("syndrome_trace", {})
        ms = st.get("matched_symptoms", [])
        self.assertGreater(len(ms), 0,
                           f"应有匹配症状，实际={ms}")
        # 至少一个患者症状在 matched_symptoms 中出现
        self.assertTrue(any(kw in str(ms) for kw in ["热", "咳", "黄"]),
                        f"matched_symptoms 应包含发热/咳嗽/黄痰等关键词: {ms}")

    # ── matched_pathology ──

    def test_matched_pathology_for_heat(self):
        """热象病例的 matched_pathology 包含'热'"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "口干", "黄痰"],
            tongue="舌红苔黄",
            pulse="脉数",
            cold_heat=["发热"],
        )
        st = result.get("syndrome_trace", {})
        mp = st.get("matched_pathology", "")
        self.assertIn("热", mp,
                       f"热象病例 matched_pathology 应含'热'，实际='{mp}'")

    # ── missing_info ──

    def test_missing_info_when_no_tongue(self):
        """缺少舌象时 missing_info 包含提示"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽"],
            tongue="",
            pulse="",
        )
        st = result.get("syndrome_trace", {})
        mi = st.get("missing_info", "")
        # missing_info 可能为字符串（评分器）或列表（LLM），都能提示缺失信息
        if isinstance(mi, str):
            self.assertTrue("舌" in mi or "脉" in mi or "症状" in mi,
                            f"缺少舌脉时 missing_info 应提示，实际='{mi}'")
        elif isinstance(mi, list):
            has_any = any("舌" in str(item) or "脉" in str(item) or "症状" in str(item)
                          for item in mi)
            self.assertTrue(has_any,
                            f"缺少舌脉时 missing_info 应提示，实际={mi}")

    # ── 多证型排序 ──

    def test_multi_syndrome_ranking(self):
        """多证型按得分降序排列"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "抽动障碍",
            symptoms=["耸肩", "眨眼", "急躁易怒", "声音高亢"],
            tongue="舌红",
            pulse="脉弦数",
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreaterEqual(len(cs), 6,
                                f"抽动障碍应有 6 个证型候选，实际={len(cs)}")
        # 验证降序
        scores = [s["score"] for s in cs]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         f"candidate_scores 应按分降序: {scores}")

    # ── M2-1 禁止输出 ──

    def test_m2_1_no_forbidden_fields(self):
        """M2-1 输出不得包含 formula/herbs/dosage/prescription_text/final_formula/用法/疗程"""
        forbidden = {"formula", "herbs", "dosage", "dose",
                     "prescription_text", "final_formula", "用法", "疗程"}
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌尖红",
            pulse="脉浮数",
        )
        for key in forbidden:
            self.assertNotIn(key, result,
                             f"M2-1 不应包含 '{key}'")
        st = result.get("syndrome_trace", {})
        for key in forbidden:
            self.assertNotIn(key, st,
                             f"syndrome_trace 不应包含 '{key}'")

    def test_m2_1_no_forbidden_when_single_syndrome(self):
        """单证型时 M2-1 也不得包含方剂/药物/剂量"""
        forbidden = {"formula", "herbs", "dosage", "dose",
                     "prescription_text", "final_formula", "用法", "疗程"}
        result = self.m2.run_m2_1_syndrome_reasoning(
            "带状疱疹",
            symptoms=["刺痛", "口干"],
            tongue="舌红",
        )
        for key in forbidden:
            self.assertNotIn(key, result,
                             f"M2-1(单证型)不应包含 '{key}'")

    def test_m2_1_formal_prescription_always_false(self):
        """M2-1 formal_prescription_allowed 恒为 false"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咳嗽", "黄痰"],
            tongue="舌尖红",
            pulse="脉浮数",
        )
        self.assertFalse(result.get("formal_prescription_allowed"))

    # ══════════════════════════════════════════════════════
    #  新增测试：急性胃肠炎 → 痢疾 key resolver + 湿热证型
    # ══════════════════════════════════════════════════════

    def test_resolve_m2_disease_key_acute_enteritis(self):
        """resolve_m2_disease_key('急性胃肠炎') → '痢疾 (Dysentery)' 或 '痢疾'"""
        key = self.m2.resolve_m2_disease_key("急性胃肠炎")
        self.assertIn(key, ["痢疾 (Dysentery)", "痢疾"],
                      f"急性胃肠炎应解析为痢疾相关 key，实际='{key}'")

    def test_resolve_m2_disease_key_pneumonia(self):
        """resolve_m2_disease_key('肺炎') → '肺炎'（精确匹配）"""
        key = self.m2.resolve_m2_disease_key("肺炎")
        self.assertEqual(key, "肺炎")

    def test_resolve_m2_disease_key_handles_m2_fallback(self):
        """resolve_m2_disease_key('咳嗽') → m2_fallback 解析"""
        key = self.m2.resolve_m2_disease_key("咳嗽")
        self.assertIn(key, ["支原体肺炎", "急性支气管炎"],
                      f"咳嗽应通过 m2_fallback 解析，实际='{key}'")

    def test_acute_enteritis_resolved_to_dysentery_no_zero_syndromes(self):
        """急性胃肠炎经 key resolver 后应找到痢疾下的证型，候选 > 0 且 Top1 为湿热相关"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性胃肠炎",
            symptoms=["发热", "腹痛", "腹泻", "大便黏液", "里急后重"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
            cold_heat=["发热"],
        )
        st = result.get("syndrome_trace", {})
        # 验证 input_trace 包含 resolved key
        it = result.get("input_trace", {})
        resolved = it.get("resolved_m2_key", "")
        self.assertIn(resolved, ["痢疾 (Dysentery)", "痢疾"],
                      f"M2-1 input_trace 应含 resolved_m2_key 且为痢疾，实际='{resolved}'")
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           f"急性胃肠炎经解析后候选证型数量应 > 0，实际={len(cs)}")
        # Top1 应为湿热痢 / 湿热蕴肠 / 胃肠湿热
        top_name = cs[0].get("syndrome_name", "")
        self.assertTrue(
            any(kw in top_name for kw in ["湿热", "湿", "痢"]),
            f"急性胃肠炎 Top1 应为湿热/痢相关证型，实际='{top_name}'"
        )
        # 不应返回 NO_CANDIDATE
        self.assertNotEqual(result.get("status"), "NO_CANDIDATE",
                            "急性胃肠炎不应返回 NO_CANDIDATE")

    # ══════════════════════════════════════════════════════
    #  新增测试：无明显口干 → 不误判热象
    # ══════════════════════════════════════════════════════

    def test_pneumonia_wind_cold_with_wumingxian_kougan_not_heat(self):
        """肺炎风寒 + '无明显口干、怕冷、痰白清稀' → '无明显口干'不计入热象 → Top1 为邪犯肺卫（风寒）"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["怕冷", "咳嗽", "痰白清稀", "鼻塞", "流清涕", "无明显口干", "不渴"],
            tongue="舌淡苔薄白",
            pulse="脉浮紧",
            cold_heat=["恶寒"],
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           f"肺炎风寒应有候选证型，实际={len(cs)}")
        top_name = cs[0].get("syndrome_name", "")
        self.assertIn("风寒", top_name,
                      f"无明显口干不误判热象，Top1 应为风寒证，实际='{top_name}'")

    # ══════════════════════════════════════════════════════
    #  新增测试：明确热象仍正确识别
    # ══════════════════════════════════════════════════════

    def test_pneumonia_heat_phlegm_correctly_identified(self):
        """肺炎痰热 + '口干口苦、痰黄绿、苔黄腻' → 热象仍正确识别 → Top1 为热证"""
        result = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "痰黄绿", "口干", "口苦", "胸闷", "气短"],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
            cold_heat=["发热"],
        )
        st = result.get("syndrome_trace", {})
        cs = st.get("candidate_scores", [])
        self.assertGreater(len(cs), 0,
                           f"肺炎痰热应有候选证型，实际={len(cs)}")
        top_name = cs[0].get("syndrome_name", "")
        self.assertTrue(
            any(kw in top_name for kw in ["痰热", "热", "黄"]),
            f"肺炎痰热 Top1 应为热/痰热相关证型，实际='{top_name}'"
        )

    # ══════════════════════════════════════════════════════
    #  新增测试：resolve_m2_disease_key 单项测试
    # ══════════════════════════════════════════════════════

    def test_resolve_m2_disease_key_exact_match(self):
        """精确匹配 KB key"""
        self.assertEqual(self.m2.resolve_m2_disease_key("肺炎"), "肺炎")
        self.assertEqual(self.m2.resolve_m2_disease_key("带状疱疹"), "带状疱疹")

    def test_resolve_m2_disease_key_substring_match(self):
        """子串匹配（通过 DISEASE_NAME_MAP）"""
        # 感冒 → DISEASE_NAME_MAP → 急性上呼吸道感染
        self.assertEqual(self.m2.resolve_m2_disease_key("感冒"), "急性上呼吸道感染")
        # 咳嗽 → m2_fallback → 支原体肺炎 或 急性支气管炎
        key = self.m2.resolve_m2_disease_key("咳嗽")
        self.assertIn(key, ["支原体肺炎", "急性支气管炎"])


if __name__ == "__main__":
    unittest.main()
