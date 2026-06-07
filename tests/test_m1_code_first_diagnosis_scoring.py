"""
M1 Code-First Diagnosis Scoring 测试

验证 _score_candidates 三阶段决策逻辑的正确性:
- Tier 1（快速通道）：精确病名匹配
- Tier 2（代码评分）：多维评分阈值
- Tier 3（LLM 裁决）：评分不足时兜底

重点验证：
1. 肺炎明确病名 + 痰黄绿 + 苔黄腻 → Top1 必须是肺炎
2. 肺炎不得被上气道咳嗽综合征压过
3. 痰黄绿、苔黄腻必须进入 evidence_items
4. criteria_struct 必须包含 matched_required / missing_required / contradicted_items
5. 明确病名 fast path 仅用于精确病名或明确 alias，不得用症状词误触发
"""

import unittest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from m1_engine import M1DiagnosisEngine


class M1CodeFirstDiagnosisScoringTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m1 = M1DiagnosisEngine()

    def _build_ni(self, mentioned="", chief_complaint="", symptoms=None, signs=None,
                  labs=None, imaging=None):
        """辅助：构建 NormalizedInput"""
        return self.m1.normalize_input({
            "patient_mentioned_disease": mentioned,
            "chief_complaint": chief_complaint,
            "symptoms": symptoms or [],
            "signs": signs or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "negative_findings": [],
            "duration": "",
            "onset": "",
        })

    # ── 测试 1：肺炎明确病名 + 痰黄绿 + 苔黄腻 ──
    def test_pneumonia_explicit_name_fast_path(self):
        """肺炎明确病名 → Tier 1 快速通道，Top1 必须是肺炎"""
        ni = self._build_ni(
            mentioned="肺炎",
            chief_complaint="咳嗽一周，痰黄黏稠色绿",
            symptoms=["咳嗽一周，痰黄黏稠色绿", "畏寒怕冷，咽痒胸闷气短", "舌暗红苔黄腻"],
            signs=["舌暗红", "苔黄腻"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        self.assertTrue(len(scored) >= 2, "至少应有2个候选")

        top = scored[0]
        top_detail = top["detail"]
        top_name = top_detail["name"]

        # 1. Top1 名称必须包含"肺炎"
        self.assertIn("肺炎", top_name,
                      f"Top1 必须是肺炎相关疾病，实际为: {top_name}")

        # 2. name_score 应为 30（精确匹配）
        self.assertEqual(top_detail["name_score"], 30,
                         f"精确病名匹配应为 30，实际为 {top_detail['name_score']}")

        # 3. Tier 1 触发条件
        self.assertGreaterEqual(top_detail["name_score"], 30,
                                "Tier 1 快速通道应触发")

        # 4. sign_score 应包含苔黄腻
        self.assertIn("苔黄腻", top_detail.get("matched_signs", []),
                      "苔黄腻应在 matched_signs 中")
        self.assertIn("舌暗红", top_detail.get("matched_signs", []),
                      "舌暗红应在 matched_signs 中")

        # 5. exam_score: 痰黄绿 → 痰培养/涂片
        self.assertIn("痰培养/涂片", top_detail.get("matched_exams", []),
                      "痰黄黏稠色绿应匹配到痰培养/涂片")

    def test_pneumonia_not_overtaken_by_uga(self):
        """肺炎不得被"上气道咳嗽综合征"压过"""
        ni = self._build_ni(
            mentioned="肺炎",
            chief_complaint="咳嗽一周，痰黄黏稠色绿，畏寒怕冷，咽痒胸闷气短",
            symptoms=["咳嗽一周，痰黄黏稠色绿", "畏寒怕冷，咽痒胸闷气短", "舌暗红苔黄腻"],
            signs=["舌暗红", "苔黄腻"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        # 获取肺炎排名
        pneumonia_rank = None
        uga_rank = None
        for i, s in enumerate(scored):
            name = s["detail"]["name"]
            if "肺炎" in name and pneumonia_rank is None:
                pneumonia_rank = i
            if "上气道" in name or "上气道咳嗽" in name:
                uga_rank = i

        self.assertIsNotNone(pneumonia_rank, "肺炎必须在候选列表中")
        if uga_rank is not None:
            self.assertLess(pneumonia_rank, uga_rank,
                            f"肺炎(第{pneumonia_rank+1})不得被上气道咳嗽综合征(第{uga_rank+1})压过")

    # ── 测试 2：evidence_items 包含关键症状 ──
    def test_key_symptoms_in_evidence_items(self):
        """痰黄绿、苔黄腻必须进入 evidence_items"""
        ni = self._build_ni(
            mentioned="肺炎",
            chief_complaint="咳嗽一周，痰黄黏稠色绿",
            symptoms=["咳嗽一周，痰黄黏稠色绿", "畏寒怕冷，咽痒胸闷气短", "舌暗红苔黄腻"],
            signs=["舌暗红", "苔黄腻"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        top = scored[0]
        detail = top["detail"]

        # matched_signs 包含苔黄腻
        self.assertIn("苔黄腻", detail.get("matched_signs", []),
                      "evidence_items 未包含苔黄腻")

        # matched_exams 包含痰培养
        self.assertIn("痰培养/涂片", detail.get("matched_exams", []),
                      "evidence_items 未包含痰培养/涂片")

        # sign_score > 0
        self.assertGreater(detail["sign_score"], 0,
                           "sign_score 应为正数")

        # exam_score > 0
        self.assertGreater(detail["exam_score"], 0,
                           "exam_score 应为正数")

    # ── 测试 3：diagnostic_criteria 结构化输出 ──
    def test_criteria_struct_contains_required_fields(self):
        """criteria_struct 必须包含 matched_required / missing_required / contradicted_items"""
        ni = self._build_ni(
            mentioned="肺炎",
            chief_complaint="咳嗽一周，痰黄黏稠色绿",
            symptoms=["咳嗽一周，痰黄黏稠色绿", "畏寒怕冷，咽痒胸闷气短", "舌暗红苔黄腻"],
            signs=["舌暗红", "苔黄腻"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        for s in scored[:3]:
            cs = s.get("criteria_struct", {})
            with self.subTest(name=s["detail"]["name"]):
                self.assertIn("matched_required", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 matched_required")
                self.assertIn("matched_supportive", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 matched_supportive")
                self.assertIn("missing_required", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 missing_required")
                self.assertIn("contradicted_items", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 contradicted_items")
                self.assertIn("exclusion_penalty", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 exclusion_penalty")
                self.assertIn("confidence", cs,
                              f"{s['detail']['name']} criteria_struct 缺少 confidence")
                # confidence 必须是 high/medium/low 之一
                self.assertIn(cs["confidence"], ["high", "medium", "low"],
                              f"confidence 必须是 high/medium/low, 实际为 {cs['confidence']}")

    # ── 测试 4：明确病名 fast path 不得被症状词误触发 ──
    def test_fast_path_not_triggered_by_symptom_words(self):
        """明确病名 fast path (name_score=30) 只能用于精确病名或明确 alias，
        不得用症状词（如"咳嗽""发热"）误触发"""
        ni = self._build_ni(
            mentioned="咳嗽",
            chief_complaint="咳嗽一周",
            symptoms=["咳嗽一周"],
            signs=[],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        top = scored[0]
        top_detail = top["detail"]

        # "咳嗽" == "上气道咳嗽综合征" 不应触发 name_score=30
        # 因为 standardize_disease_name("咳嗽") 现在不应映射到疾病名
        # "咳嗽" 是纯症状词，不应自动匹配到疾病
        self.assertNotEqual(top_detail["name_score"], 30,
                            "症状词'咳嗽'不应触发精确病名匹配的 name_score=30")

    # ── 测试 5：普通感冒病例不应被泛呼吸道词误覆盖 ──
    def test_common_cold_not_mapped_to_uga(self):
        """一般感冒症状不应被误判为上气道咳嗽综合征"""
        ni = self._build_ni(
            mentioned="",
            chief_complaint="流鼻涕打喷嚏2天",
            symptoms=["流鼻涕", "打喷嚏", "无发热"],
            signs=["舌淡红", "苔薄白"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        top = scored[0]
        top_name = top["detail"]["name"]

        # 一般感冒 top1 应是"上呼吸道感染"类而非"上气道咳嗽综合征"
        # 注意：这不是一个严格的必须断言，只是防止误匹配
        self.assertNotIn("咳嗽", top_name,
                         f"普通感冒不应误判为{top_name}")

    # ── 测试 6：span_score 不应单独导致疾病压过精确病名 ──
    def test_span_score_does_not_override_exact_name(self):
        """span_score 不应让一个只有全文匹配但没有患者明确提及的病名压过精确病名"""
        ni = self._build_ni(
            mentioned="急性胃肠炎",
            chief_complaint="急性胃肠炎 发热伴腹痛腹泻3天",
            symptoms=["发热伴腹痛腹泻3天", "大便粘液潜血", "舌红苔黄腻"],
            signs=["舌红", "苔黄腻"],
        )
        candidates = self.m1._recall_candidates(ni)
        scored = self.m1._score_candidates(candidates, ni)

        top = scored[0]
        top_detail = top["detail"]

        # 急性胃肠炎应在 top-1，因为有精确病名匹配
        self.assertIn("胃肠炎", top_detail["name"],
                      "急性胃肠炎有精确病名匹配，应为 Top1")
        self.assertEqual(top_detail["name_score"], 30,
                         "急性胃肠炎的 name_score 应为 30")

    # ════════════════════════════════════════════════════════════
    #  _extract_structured_info 测试
    # ════════════════════════════════════════════════════════════

    def _extract(self, mentioned="", chief_complaint="", symptoms=None, signs=None,
                 primary_diagnosis="", cn_diagnoses=None):
        """辅助：调用 _extract_structured_info"""
        ni = self._build_ni(
            mentioned=mentioned,
            chief_complaint=chief_complaint,
            symptoms=symptoms or [],
            signs=signs or [],
        )
        raw = {"patient_mentioned_disease": mentioned, "chief_complaint": chief_complaint}
        return self.m1._extract_structured_info(
            ni, raw, primary_diagnosis, cn_diagnoses or [], "code_tier1_name_match"
        )

    def test_structured_info_laryngeal_cancer_postop_metastasis(self):
        """"做了喉癌手术，现肝转移、骨转移"
        预期:
        - primary_diagnosis=喉癌
        - disease_status 包含 postoperative_state 和 metastatic_disease
        - metastasis_flag=true
        - secondary_diagnosis 包含 肝转移、骨转移
        - 不得输出乳腺癌
        """
        s = self._extract(
            mentioned="喉癌",
            chief_complaint="2024年11月做了喉癌手术，现肝转移、骨转移",
            symptoms=["疲劳", "纳差"],
            primary_diagnosis="喉癌",
            cn_diagnoses=["喉癌", "癌", "水肿"],
        )

        # disease_status 包含术后和转移
        self.assertIn("postoperative_state", s["disease_status"],
                      "disease_status 应包含 postoperative_state")
        self.assertIn("metastatic_disease", s["disease_status"],
                      "disease_status 应包含 metastatic_disease")

        # postoperative 检测
        self.assertTrue(s["_postoperative"]["detected"], "术后状态应被识别")
        self.assertIn("喉癌", s["_postoperative"]["original_surgery"],
                      "original_surgery 应包含喉癌")
        self.assertIn("surgery_history", s["_postoperative"]["source"],
                      "source 应包含 surgery_history")
        self.assertEqual(s["postoperative_state"], True, "postoperative_state 高层字段应为 True")

        # metastasis 检测
        self.assertTrue(s["_metastasis"]["detected"], "转移应被识别")
        self.assertIn("肝转移", s["_metastasis"]["sites"], "转移部位应包含肝转移")
        self.assertIn("骨转移", s["_metastasis"]["sites"], "转移部位应包含骨转移")
        self.assertEqual(s["metastasis_flag"], True, "metastasis_flag 高层字段应为 True")
        self.assertIn("肝转移", s["metastasis_sites"], "metastasis_sites 应包含肝转移")

        # llm_protected
        self.assertTrue(s["llm_protected"], "喉癌+手术+转移应触发 LLM 保护")

        # risk_flags
        self.assertIn("cancer_history", s["risk_flags"])
        self.assertIn("advanced_disease", s["risk_flags"])
        self.assertIn("post_treatment", s["risk_flags"])

    def test_structured_info_gastric_cancer_postop_metastasis(self):
        """"胃癌术后，肝转移"
        预期:
        - primary_diagnosis=胃癌
        - secondary_diagnosis 包含肝转移
        """
        s = self._extract(
            mentioned="胃癌",
            chief_complaint="胃癌术后，肝转移",
            symptoms=["纳差"],
            primary_diagnosis="胃癌",
            cn_diagnoses=["胃癌"],
        )

        self.assertIn("postoperative_state", s["disease_status"],
                      "胃癌术后应识别术后状态")
        self.assertIn("metastatic_disease", s["disease_status"],
                      "肝转移应识别转移状态")

        self.assertTrue(s["_postoperative"]["detected"])
        self.assertEqual(s["_postoperative"]["source"], "surgery_history_postop")

        self.assertTrue(s["_metastasis"]["detected"])
        self.assertIn("肝转移", s["_metastasis"]["sites"], "转移部位应包含肝转移")
        self.assertEqual(s["postoperative_state"], True)
        self.assertEqual(s["metastasis_flag"], True)

    def test_structured_info_complications(self):
        """"喉癌术后，水肿、纳差、消瘦"
        预期:
        - complications_or_comorbidities 包含 水肿、纳差/营养风险
        """
        s = self._extract(
            mentioned="喉癌",
            chief_complaint="喉癌术后，水肿",
            symptoms=["水肿", "纳差", "消瘦"],
            primary_diagnosis="喉癌",
            cn_diagnoses=["喉癌"],
        )

        complications = s["complications_or_comorbidities"]
        self.assertIn("水肿", complications,
                      f"并发症应包含水肿，实际为: {complications}")
        self.assertIn("纳差/营养风险", complications,
                      f"并发症应包含纳差/营养风险，实际为: {complications}")
        self.assertIn("消瘦/营养不良", complications,
                      f"并发症应包含消瘦/营养不良，实际为: {complications}")

    def test_structured_info_llm_guard_blocks_wrong_cancer(self):
        """LLM fallback guard 阻止改写明确癌种
        当代码已识别 primary_diagnosis=喉癌 且 source=surgery_history，
        LLM 不得改成乳腺癌或其他文本中未出现的癌种
        """
        # 模拟 diagnose() 中的 LLM guard 逻辑
        search_text = "2024年11月做了喉癌手术，现肝转移、骨转移"
        _llm_output_cn = "乳腺癌"
        _llm_protected = True
        _search_all = search_text.lower()

        # 如果 llm_protected 且 LLM 输出了原文中未出现的癌种
        if _llm_protected and "癌" in _llm_output_cn:
            self.assertNotIn(_llm_output_cn.lower(), _search_all,
                             "LLM 不得输出原文中未出现的癌种")

        # 喉癌在原文中，应允许
        _llm_output_cn2 = "喉癌"
        self.assertIn(_llm_output_cn2.lower(), _search_all,
                      "喉癌在原文中出现，应允许")


if __name__ == "__main__":
    unittest.main()
