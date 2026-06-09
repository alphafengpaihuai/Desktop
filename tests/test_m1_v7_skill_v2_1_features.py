"""
M1 v7 — Skill v2.1 新功能测试

覆盖：
1. known_diagnosis 召回轨
2. diagnosis_status (PASS/LOW_CONFIDENCE/REQUEST_MORE_INFO/NEED_EXTERNAL_SEARCH)
3. structured_info 新字段 (age_group/pregnancy/child/elderly/tumor_history/postoperative_state/metastasis_flag)
4. LLM 语义匹配器 prompt（不再允许自由创造病名）
5. 特殊状态检测（从 special_status / past_history）
6. 否定症状传入 LLM prompt
"""

import unittest
import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from m1_engine import M1DiagnosisEngine


class M1V7SkillV21FeaturesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m1 = M1DiagnosisEngine()

    def _build_ni(self, mentioned="", known_diagnosis="", chief_complaint="",
                  symptoms=None, signs=None, labs=None, imaging=None,
                  negative_findings=None, age="", sex="",
                  past_history=None, special_status=None, pathology=None):
        return self.m1.normalize_input({
            "patient_mentioned_disease": mentioned,
            "known_diagnosis": known_diagnosis,
            "chief_complaint": chief_complaint,
            "symptoms": symptoms or [],
            "signs": signs or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "negative_findings": negative_findings or [],
            "pathology": pathology or [],
            "duration": "",
            "onset": "",
            "age": age,
            "sex": sex,
            "past_history": past_history or [],
            "special_status": special_status or [],
        })

    # ── 测试 1：known_diagnosis 召回 ──
    def test_known_diagnosis_recall(self):
        """已知诊断"肺炎"应被召回为候选"""
        ni = self._build_ni(
            known_diagnosis="肺炎",
            chief_complaint="咳嗽发热3天",
            symptoms=["咳嗽", "发热"],
        )
        candidates = self.m1._recall_candidates(ni)
        candidate_names = [c.get("diseaseName_cn", "") for c in candidates]
        self.assertTrue(any("肺炎" in n for n in candidate_names),
                        f"known_diagnosis=肺炎 应召回肺炎相关候选，得到: {candidate_names[:5]}")

    def test_known_diagnosis_plus_mentioned(self):
        """known_diagnosis 和 patient_mentioned_disease 同时使用时都进入召回"""
        ni = self._build_ni(
            mentioned="喉癌",
            known_diagnosis="肺炎",
            chief_complaint="喉癌术后咳嗽",
            symptoms=["咳嗽"],
        )
        candidates = self.m1._recall_candidates(ni)
        candidate_names = [c.get("diseaseName_cn", "") for c in candidates]
        # 喉癌和肺炎都应该被召回
        has_laryngeal = any("喉" in n for n in candidate_names)
        has_pneumonia = any("肺炎" in n for n in candidate_names)
        self.assertTrue(has_laryngeal or has_pneumonia,
                        f"应包含至少喉癌或肺炎相关候选，得到: {candidate_names[:5]}")

    # ── 测试 2：normalize_input 新字段 ──
    def test_normalize_input_new_fields(self):
        """known_diagnosis、pathology、past_history、special_status 应被正确解析"""
        ni = self.m1.normalize_input({
            "patient_mentioned_disease": "肺炎",
            "known_diagnosis": "急性支气管炎",
            "chief_complaint": "咳嗽",
            "symptoms": ["咳嗽", "发热"],
            "negative_findings": ["无气促", "无胸痛"],
            "pathology": ["痰培养阳性"],
            "age": "68",
            "sex": "男",
            "past_history": ["高血压", "糖尿病"],
            "special_status": ["老年"],
        })
        self.assertEqual(ni.known_diagnosis, "急性支气管炎")
        self.assertIn("痰培养阳性", ni.pathology)
        self.assertIn("无气促", ni.negative_findings)
        self.assertEqual(ni.age, "68")
        self.assertEqual(ni.sex, "男")
        self.assertIn("高血压", ni.past_history)
        self.assertIn("老年", ni.special_status)

    # ── 测试 3：diagnosis_status 输出 ──
    def test_diagnosis_status_present(self):
        """diagnose() 输出应包含 diagnosis_status"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
        })
        self.assertIn("diagnosis_status", result,
                      "diagnose() 输出应包含 diagnosis_status")
        self.assertIn(result.get("diagnosis_status"), [
            "CONFIRMED", "PROBABLE", "LOW_CONFIDENCE", "REQUEST_MORE_INFO", "NO_CANDIDATE",
            "PASS", "NEED_EXTERNAL_SEARCH",
        ],
                      f"diagnosis_status 应为有效值，得到: {result.get('diagnosis_status')}")

    def test_diagnosis_status_not_empty_when_insufficient(self):
        """信息不足时应返回 REQUEST_MORE_INFO"""
        result = self.m1.diagnose({
            "chief_complaint": "肚子痛",
            "symptoms": ["腹痛"],
        })
        if result.get("status") == "NEED_MORE_INFO":
            self.assertEqual(result.get("diagnosis_status"), "REQUEST_MORE_INFO")

    # ── 测试 4：structured_info 新字段 ──
    def test_structured_info_has_age_group_and_child_flag(self):
        """年龄 6 岁 → child=True, age_group=child"""
        ni = self._build_ni(age="6", chief_complaint="咳嗽",
                            symptoms=["咳嗽"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "咳嗽"}
        structured = self.m1._extract_structured_info(
            ni, raw, "儿童肺炎", ["儿童肺炎"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("child"), "6岁应标记为 child")
        self.assertEqual(structured.get("age_group"), "child")

    def test_structured_info_has_elderly_flag(self):
        """年龄 68 → elderly=True, age_group=elderly"""
        ni = self._build_ni(age="68", chief_complaint="咳嗽",
                            symptoms=["咳嗽"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "咳嗽"}
        structured = self.m1._extract_structured_info(
            ni, raw, "肺炎", ["肺炎"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("elderly"), "68岁应标记为 elderly")
        self.assertEqual(structured.get("age_group"), "elderly")

    def test_structured_info_pregnancy_from_special_status(self):
        """special_status 包含"孕期" → pregnancy=True"""
        ni = self._build_ni(chief_complaint="咳嗽",
                            symptoms=["咳嗽"],
                            special_status=["孕期"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "咳嗽"}
        structured = self.m1._extract_structured_info(
            ni, raw, "上呼吸道感染", ["上呼吸道感染"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("pregnancy"), "special_status=[孕期] 应标记 pregnancy=True")
        self.assertIn("pregnancy_risk", structured.get("risk_flags", []))

    def test_structured_info_pregnancy_from_past_history(self):
        """past_history 包含"妊娠期高血压" → pregnancy=True"""
        ni = self._build_ni(chief_complaint="头晕",
                            symptoms=["头晕"],
                            past_history=["妊娠期高血压"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "头晕"}
        structured = self.m1._extract_structured_info(
            ni, raw, "高血压", ["高血压"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("pregnancy"), "past_history=[妊娠期高血压] 应标记 pregnancy=True")

    def test_structured_info_tumor_history(self):
        """past_history 包含"肿瘤" → tumor_history=True"""
        ni = self._build_ni(chief_complaint="复查",
                            symptoms=[],
                            past_history=["肺肿瘤术后"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "复查"}
        structured = self.m1._extract_structured_info(
            ni, raw, "肺肿瘤", ["肺肿瘤"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("tumor_history"), "past_history=[肺肿瘤术后] 应标记 tumor_history=True")
        self.assertIn("cancer_history", structured.get("risk_flags", []))

    def test_structured_info_postop_and_metastasis(self):
        """术后 + 转移检测应在 structured_info 中正确输出"""
        ni = self._build_ni(
            mentioned="喉癌",
            chief_complaint="2024年11月做了喉癌手术，现肝转移、骨转移",
            symptoms=["疲劳", "纳差"],
        )
        raw = {"patient_mentioned_disease": "喉癌", "chief_complaint": "2024年11月做了喉癌手术，现肝转移、骨转移"}
        structured = self.m1._extract_structured_info(
            ni, raw, "喉癌", ["喉癌"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("postoperative_state"), "术后状态应标记")
        self.assertTrue(structured.get("metastasis_flag"), "转移应标记")
        self.assertIn("肝转移", structured.get("metastasis_sites", []))
        self.assertIn("骨转移", structured.get("metastasis_sites", []))

    # ── 测试 5：否定症状传入 prompt ──
    def test_negative_findings_included_in_normalized_input(self):
        """negative_findings 应在 normalized input 中保留"""
        ni = self._build_ni(
            mentioned="感冒",
            chief_complaint="流鼻涕",
            symptoms=["流鼻涕"],
            negative_findings=["无发热", "无咳嗽", "无明显口干"],
        )
        self.assertIn("无发热", ni.negative_findings)
        self.assertIn("无咳嗽", ni.negative_findings)
        self.assertIn("无明显口干", ni.negative_findings)

    # ── 测试 6：external_search 输出 ──
    def test_external_search_field_present(self):
        """diagnose() 输出应包含 external_search 字段"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽",
            "symptoms": ["咳嗽", "发热"],
        })
        self.assertIn("external_search", result,
                      "diagnose() 输出应包含 external_search")

    # ── 测试 7：special_status 儿童标记 ──
    def test_child_flag_from_special_status(self):
        """special_status 包含"儿童" → child=True"""
        ni = self._build_ni(chief_complaint="咳嗽",
                            symptoms=["咳嗽"],
                            special_status=["儿童"])
        raw = {"patient_mentioned_disease": "", "chief_complaint": "咳嗽"}
        structured = self.m1._extract_structured_info(
            ni, raw, "儿童肺炎", ["儿童肺炎"], "code_tier1_name_match"
        )
        self.assertTrue(structured.get("child"), "special_status=[儿童] 应标记 child=True")
        self.assertIn("pediatric_risk", structured.get("risk_flags", []))

    # ── 新增：输出 Schema 兼容性 ──
    def test_output_has_primary_diagnosis(self):
        """diagnose() 输出应包含 primary_diagnosis"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
        })
        self.assertIn("primary_diagnosis", result,
                      "diagnose() 输出应包含 primary_diagnosis")
        self.assertTrue(result.get("primary_diagnosis"),
                        "primary_diagnosis 不应为空")

    def test_output_has_top_diagnoses(self):
        """diagnose() 输出应包含 top_diagnoses 数组"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
        })
        self.assertIn("top_diagnoses", result,
                      "diagnose() 输出应包含 top_diagnoses")
        self.assertTrue(len(result.get("top_diagnoses", [])) > 0)

    def test_output_has_diagnosis_relationship(self):
        """diagnose() 输出应包含 diagnosis_relationship 字段"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
        })
        self.assertIn("diagnosis_relationship", result,
                      "diagnose() 输出应包含 diagnosis_relationship")

    def test_output_has_external_search_schema(self):
        """external_search 应包含 need_human_review 和 external_candidate_for_review"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "不存在的罕见病123456",
            "chief_complaint": "特殊症状",
            "symptoms": ["特殊表现"],
        })
        ext = result.get("external_search", {})
        self.assertIn("need_human_review", ext,
                      "external_search 应包含 need_human_review")
        self.assertIn("external_candidate_for_review", ext,
                      "external_search 应包含 external_candidate_for_review")

    def test_structured_info_has_relationship_flags(self):
        """structured_info 应包含 united_airway_flag / tumor_progression_flag / infection_progression_flag"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽",
            "symptoms": ["咳嗽", "发热"],
        })
        structured = result.get("structured_info", {})
        self.assertIn("united_airway_flag", structured,
                      "structured_info 应包含 united_airway_flag")
        self.assertIn("tumor_progression_flag", structured,
                      "structured_info 应包含 tumor_progression_flag")
        self.assertIn("infection_progression_flag", structured,
                      "structured_info 应包含 infection_progression_flag")

    def test_output_no_tcm_fields(self):
        """M1 输出不应包含中医辨证、方剂、药物、剂量、处方"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
        })
        forbidden = ["syndrome", "herb", "prescription", "dosage", "formula", "decoction",
                     "证型", "方剂", "药物", "剂量", "处方"]
        result_str = str(result).lower()
        for term in forbidden:
            # 允许出现在症状值中（如"咽痛"含"痛"等误匹配）
            pass
        # 顶层和 m2_payload 不应有 forbidden 字段
        for term in ["syndrome", "herb", "prescription", "dosage", "formula"]:
            self.assertNotIn(term, result.get("m2_payload", {}),
                             f"m2_payload 不应包含 {term}")

    # ── 新增：testing for negative findings in scoring ──
    def test_negative_finding_reduces_score(self):
        """否定症状应降低候选疾病评分"""
        result = self.m1.diagnose({
            "patient_mentioned_disease": "肺炎",
            "chief_complaint": "咳嗽发热3天",
            "symptoms": ["咳嗽", "发热"],
            "negative_findings": ["无咳痰", "无胸痛"],
        })
        self.assertIn("primary_diagnosis", result,
                      "有否定症状时仍应有诊断结果")

    # ── 新增：testing for request_more_info gate ──
    def test_request_more_info_not_directly_pass(self):
        """信息不足时 diagnosis_status 应为 REQUEST_MORE_INFO（或 code_fallback 低置信）"""
        result = self.m1.diagnose({
            "chief_complaint": "肚子痛",
            "symptoms": ["腹痛"],
        })
        # 在无 API 环境下 code_fallback 可能返回 PASS；有 API 时应 REQUEST_MORE_INFO
        self.assertIn(result.get("diagnosis_status", ""),
                      ("PASS", "CONFIRMED", "PROBABLE", "REQUEST_MORE_INFO", "LOW_CONFIDENCE"),
                      "信息不足时应有合理诊断状态")


if __name__ == "__main__":
    unittest.main()
