"""M1 咳嗽主轴优先 — 支气管炎不得被轻症鼻炎覆盖。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from m1_engine import M1DiagnosisEngine
from services.m1_m2_bridge import resolve_primary_disease


BRONCHITIS_CASE_INPUT = {
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
    "signs": ["舌暗红苔白腻"],
    "labs": ["双肺呼吸音粗"],
    "imaging": [],
    "negative_findings": ["大便可", "输液2天咳嗽无明显缓解"],
    "duration": "一周",
    "onset": "",
    "age": "37",
    "gender": "女",
}


class TestM1BronchitisCoughPriority(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m1 = M1DiagnosisEngine()

    def test_m1_bronchitis_cough_not_allergic_rhinitis(self):
        result = self.m1.diagnose(BRONCHITIS_CASE_INPUT)
        primary = resolve_primary_disease(result)

        self.assertNotEqual(primary, "变应性鼻炎")
        self.assertIn(
            primary,
            ["急性支气管炎", "支气管炎", "急性咳嗽", "咳嗽"],
            f"primary_disease 应指向下气道咳嗽入口，实际: {primary}",
        )

        secondary = (
            result.get("secondary_diagnoses")
            or (result.get("m2_payload") or {}).get("secondary_diseases")
            or []
        )
        if any("鼻炎" in str(d) for d in secondary):
            self.assertNotEqual(primary, "变应性鼻炎")

        relationship = result.get("diagnosis_relationship") or {}
        self.assertTrue(relationship.get("united_airway_flag"))
        self.assertTrue(
            result.get("require_manual_review") or result.get("need_human_review"),
            "夜间咳嗽/输液无效/郁热舌象应触发人工复核",
        )

        top_conf = [d.get("confidence") for d in (result.get("top_diagnoses") or [])[:1]]
        if top_conf:
            self.assertNotEqual(top_conf[0], "high")


if __name__ == "__main__":
    unittest.main()
