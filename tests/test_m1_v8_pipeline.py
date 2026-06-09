"""M1 v8 pipeline tests — stage, KB主判, LLM参考, M2 gate, signs/symptoms separation."""
import os
import sys
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from m1_engine import M1DiagnosisEngine
from services.m1_m2_bridge import resolve_primary_disease, should_gate_m2


BRONCHITIS_CASE = {
    "patient_mentioned_disease": "支气管炎",
    "chief_complaint": "咳嗽一周",
    "symptoms": [
        "咳嗽一周",
        "晚上咳嗽频繁",
        "鼻涕鼻塞少",
        "咳嗽",
        "咽痛",
        "咽痒",
        "口干",
        "口苦",
    ],
    "signs": ["舌暗红苔白腻", "双肺呼吸音粗"],
    "labs": [],
    "imaging": [],
    "negative_findings": ["大便可", "输液2天咳嗽无明显缓解"],
    "duration": "一周",
    "age": "37",
    "gender": "女",
}

PNEUMONIA_CASE = {
    "patient_mentioned_disease": "肺炎",
    "chief_complaint": "发热咳嗽",
    "symptoms": ["发热", "咳嗽", "黄绿痰"],
    "signs": ["舌红苔黄", "脉数"],
    "labs": ["CRP升高", "血常规示白细胞升高"],
    "imaging": ["胸片示感染"],
    "negative_findings": [],
    "duration": "3天",
    "age": "45",
    "gender": "男",
}

ALLERGIC_RHINITIS_CASE = {
    "chief_complaint": "反复鼻痒喷嚏流涕",
    "symptoms": ["鼻痒", "喷嚏", "清水样流涕", "接触花粉后加重"],
    "negative_findings": ["无发热", "无咳嗽"],
    "duration": "反复",
    "age": "28",
    "gender": "女",
}

INSUFFICIENT_INFO_CASE = {
    "chief_complaint": "咳嗽不舒服",
    "symptoms": [],
}

UTI_SIMPLE_CASE = {
    "chief_complaint": "尿频尿急尿痛",
    "symptoms": ["尿频", "尿急", "尿痛"],
    "negative_findings": ["无发热", "无腰痛"],
    "labs": ["尿白细胞阳性"],
    "duration": "2天",
    "age": "32",
    "gender": "女",
}

UTI_COMPLICATED_CASE = {
    "chief_complaint": "尿痛伴发热",
    "symptoms": ["尿痛", "尿频", "发热", "腰痛", "肾区叩痛"],
    "duration": "1天",
    "age": "45",
    "gender": "男",
}

HYPERTENSION_SIMPLE_CASE = {
    "chief_complaint": "血压升高",
    "symptoms": ["多次血压160/100"],
    "negative_findings": ["无胸痛", "无气促", "无神经缺损"],
    "duration": "数月",
    "age": "58",
    "gender": "男",
}

HYPERTENSION_EMERGENCY_CASE = {
    "chief_complaint": "血压190/120伴胸痛气促",
    "symptoms": ["血压190/120", "胸痛", "气促", "肢体无力"],
    "duration": "数小时",
    "age": "62",
    "gender": "男",
}


class TestM1V8Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m1 = M1DiagnosisEngine()
        # Save original DB content for P0-4 assertion
        cls._orig_db_json = json.dumps(cls.m1.db, ensure_ascii=False, sort_keys=True)

    def test_acute_bronchitis_stage_and_payload(self):
        """P0-1: 无API key仍走v8 KB主判；P0-2: signs不混symptoms"""
        result = self.m1.diagnose(BRONCHITIS_CASE)
        primary = resolve_primary_disease(result)
        self.assertNotEqual(primary, "变应性鼻炎")
        self.assertIn(primary, ["急性支气管炎", "支气管炎"])
        self.assertIn(result.get("stage"), ["急性发作期", "急性/亚急性早期"])
        payload = result.get("m2_payload") or {}
        self.assertTrue(payload)
        self.assertEqual(payload.get("primary_disease"), primary)
        self.assertIn("disease_key", payload)
        self.assertIn("stage", payload)
        self.assertIn("evidence_trace", payload)

        # P0-2: signs 应包含 双肺呼吸音粗
        signs = payload.get("signs", [])
        self.assertIn("双肺呼吸音粗", signs)

        # P0-2: signs 不应包含 symptoms 中的词语
        symptom_words = ["咳嗽", "鼻塞", "咽痛", "咽痒", "口干", "口苦"]
        for sw in symptom_words:
            for s in signs:
                if sw in s:
                    # 双肺呼吸音粗 不应含症状词
                    if "双肺" not in s:
                        self.fail(f"signs 不应包含症状词 '{sw}', 实际在signs中发现: {s}")

    def test_pneumonia_phlegm_heat_payload(self):
        """社区获得性肺炎：primary=肺炎，stage=急性炎症期，payload 含 labs/imaging"""
        result = self.m1.diagnose(PNEUMONIA_CASE)
        primary = resolve_primary_disease(result)
        self.assertIn("肺炎", primary)
        self.assertEqual(result.get("stage"), "急性炎症期")
        payload = result.get("m2_payload") or {}
        self.assertTrue(payload.get("labs"))
        self.assertTrue(payload.get("imaging"))
        self.assertIn("舌", payload.get("tongue", ""))
        self.assertIn("脉", payload.get("pulse", ""))

    def test_insufficient_info_blocks_m2(self):
        """信息不足: REQUEST_MORE_INFO, m2_payload={}, should_gate_m2=True"""
        result = self.m1.diagnose({"chief_complaint": "咳嗽不舒服", "symptoms": []})
        self.assertEqual(result.get("diagnosis_status"), "REQUEST_MORE_INFO")
        blocked, _ = should_gate_m2(result)
        self.assertTrue(blocked)
        self.assertEqual(result.get("m2_payload"), {})

    def test_llm_discrepancy_not_adopted(self):
        """LLM 不一致时主判仍为KB"""
        with patch.object(self.m1, "llm_api_available", return_value=True), patch.object(
            self.m1,
            "_get_llm_reference_diagnosis",
            return_value={"opinion": "变应性鼻炎", "names": ["变应性鼻炎"], "discrepancy": None},
        ):
            result = self.m1.diagnose(BRONCHITIS_CASE)
        primary = resolve_primary_disease(result)
        self.assertNotEqual(primary, "变应性鼻炎")
        self.assertIsNotNone((result.get("llm_reference") or {}).get("discrepancy"))
        self.assertNotEqual(result.get("diagnosis_status"), "CONFIRMED")

    def test_negative_findings_not_positive_symptoms(self):
        """negative_findings 不混入 positive symptoms"""
        result = self.m1.diagnose({
            "chief_complaint": "咳嗽",
            "symptoms": ["咳嗽", "无发热", "无胸痛"],
            "negative_findings": ["无发热", "无胸痛"],
            "duration": "3天",
        })
        payload = result.get("m2_payload") or {}
        symptoms = payload.get("symptoms") or result.get("structured_info", {}).get("symptoms") or []
        joined = " ".join(symptoms)
        self.assertNotIn("无发热", joined)
        self.assertNotIn("无胸痛", joined)
        negatives = payload.get("negative_findings") or []
        self.assertTrue(any("无发热" in n for n in negatives))

    # ── P0-3 高风险场景测试 ──

    def test_allergic_rhinitis_no_false_diagnosis(self):
        """变应性鼻炎：不得误判为严重颅内/血栓疾病"""
        result = self.m1.diagnose(ALLERGIC_RHINITIS_CASE)
        primary = resolve_primary_disease(result)
        self.assertIn(primary, ["变应性鼻炎", "过敏性鼻炎", "慢性鼻炎", "急性鼻炎"])
        # 不得误判为严重疾病
        forbidden = ["乙状窦血栓", "颅内", "血栓", "静脉炎", "脑炎", "脑膜炎"]
        for f in forbidden:
            self.assertNotIn(f, primary, f"primary_disease 不应包含严重病名 '{f}': actual={primary}")

    def test_uti_simple_lower(self):
        """单纯下尿路感染：primary=尿路感染, stage=lower_uti"""
        result = self.m1.diagnose(UTI_SIMPLE_CASE)
        primary = resolve_primary_disease(result)
        self.assertIn("尿路感染", primary)
        payload = result.get("m2_payload") or {}
        self.assertIn("lower_uti", payload.get("stage", ""))
        # 不误判为症状名
        forbidden_names = ["不射精", "尿频症", "尿频"]
        for fn in forbidden_names:
            self.assertNotEqual(primary, fn, f"primary 不应等于 '{fn}'")
        # 有 lab 证据
        self.assertTrue(payload.get("labs"))

    def test_uti_complicated_high_risk(self):
        """高风险尿路感染：触发 emergency / need_human_review / gate M2"""
        result = self.m1.diagnose(UTI_COMPLICATED_CASE)
        emergency = result.get("emergency_alert") or {}
        payload = result.get("m2_payload") or {}
        red_flags = result.get("red_flags") or []
        gate_blocked, gate_reason = should_gate_m2(result)

        # 应触发 emergency 或 re 至少一项
        self.assertTrue(
            emergency.get("triggered") or result.get("need_human_review") or gate_blocked,
            "高风险UTI应触发emergency或need_human_review或gate M2",
        )
        # primary 应为尿路感染/肾盂肾炎方向
        primary = resolve_primary_disease(result)
        self.assertIn("尿路" in primary or "肾盂" in primary or "感染" in primary,
                      [True], f"primary 应倾向尿路/肾盂/感染方向, actual={primary}")

    def test_hypertension_simple(self):
        """高血压（轻度）：primary=高血压, stage=stage_2_hypertension, 不触发急症"""
        result = self.m1.diagnose(HYPERTENSION_SIMPLE_CASE)
        primary = resolve_primary_disease(result)
        self.assertIn("高血压" in primary or "hypertension" in primary.lower(),
                      [True], f"primary 应包含高血压, actual={primary}")
        stage = result.get("stage", "")
        self.assertIn("stage_2", stage)
        emergency = result.get("emergency_alert") or {}
        self.assertFalse(emergency.get("triggered"), "轻微高血压不应触发紧急征")
        # 可进入 M2
        payload = result.get("m2_payload") or {}
        self.assertTrue(payload, "高血压应构建 m2_payload 进入 M2")

    def test_hypertension_emergency_gates_m2(self):
        """高血压急症：red_flags 非空, need_human_review=True, 不得 CONFIRMED, gate M2"""
        result = self.m1.diagnose(HYPERTENSION_EMERGENCY_CASE)
        emergency = result.get("emergency_alert") or {}
        red_flags = result.get("red_flags") or []
        payload = result.get("m2_payload") or {}
        gate_blocked, gate_reason = should_gate_m2(result)

        self.assertTrue(emergency.get("triggered"), "高血压急症应触发 emergency_alert")
        self.assertEqual(result.get("diagnosis_status"), "REQUEST_MORE_INFO",
                         "高血压急症 diagnosis_status 不应为 CONFIRMED")
        self.assertTrue(gate_blocked, "高血压急症应 gate M2")
        # 不得误判为 ARDS/肺源性心脏病
        primary = resolve_primary_disease(result)
        forbidden = ["慢性肺源性心脏病", "急性呼吸窘迫综合征", "慢性阻塞性"]
        for f in forbidden:
            self.assertNotIn(f, primary, f"primary 不应为 '{f}'")

    # ── P0-4: 正式库保护 ──

    def test_diagnose_does_not_write_core_db(self):
        """diagnose() 不应修改正式 diseases_core.json"""
        current_db_json = json.dumps(self.m1.db, ensure_ascii=False, sort_keys=True)
        self.assertEqual(
            current_db_json, self._orig_db_json,
            "diseases_core.json 在 diagnose() 后被修改！P0-4 要求禁止写正式库",
        )


if __name__ == "__main__":
    unittest.main()
