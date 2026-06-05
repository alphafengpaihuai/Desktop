import unittest

from m2_engine import M2SyndromeSelector


FORBIDDEN_FIELDS = {
    "prescription_text", "final_formula", "complete_formula", "final_prescription",
    "prescription", "full_formula", "dosage", "dose", "用法", "疗程",
}


def assert_no_forbidden(testcase, value):
    if isinstance(value, dict):
        for key, item in value.items():
            testcase.assertNotIn(key, FORBIDDEN_FIELDS)
            if key == "formal_prescription_allowed":
                testcase.assertFalse(item)
            assert_no_forbidden(testcase, item)
    elif isinstance(value, list):
        for item in value:
            assert_no_forbidden(testcase, item)


class M2SplitEntrypointsTest(unittest.TestCase):
    def setUp(self):
        self.m2 = M2SyndromeSelector()
        self.m2._llm_available = lambda: False

    def test_m2_1_syndrome_reasoning_outputs_trace_only(self):
        result = self.m2.run_m2_1_syndrome_reasoning(
            "急性上呼吸道感染",
            symptoms=["发热", "咽痛", "怕冷"],
        )
        self.assertEqual(result["stage"], "M2_1")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("syndrome_trace", result)
        self.assertIn("evidence_trace", result)
        self.assertNotIn("formula", result)
        self.assertNotIn("formula_candidates", result)
        assert_no_forbidden(self, result)

    def test_m2_2_formula_candidates_outputs_candidate_contract(self):
        m2_1 = self.m2.run_m2_1_syndrome_reasoning(
            "变应性鼻炎",
            symptoms=["鼻痒", "打喷嚏", "清涕"],
        )
        result = self.m2.run_m2_2_formula_candidates(
            "变应性鼻炎",
            syndrome_trace=m2_1["syndrome_trace"],
            symptoms=["鼻痒", "打喷嚏", "清涕"],
        )
        self.assertEqual(result["stage"], "M2_2")
        self.assertTrue(result["candidate_only"])
        self.assertTrue(result["need_m2_3"])
        self.assertTrue(result["must_enter_m3"])
        self.assertFalse(result["no_candidate"])
        self.assertTrue(result["formula_candidates"])
        self.assertNotIn("formula", result)
        assert_no_forbidden(self, result)

    def test_m2_2_missing_formula_kb_returns_no_candidate(self):
        result = self.m2.run_m2_2_formula_candidates(
            "不存在的测试病名",
            syndrome_trace={"syndrome_name": "不存在证型"},
            symptoms=["测试症状"],
        )
        self.assertEqual(result["status"], "NO_CANDIDATE")
        self.assertTrue(result["candidate_only"])
        self.assertTrue(result["need_m2_3"])
        self.assertTrue(result["must_enter_m3"])
        self.assertTrue(result["no_candidate"])
        self.assertEqual(result["formula_candidates"], [])
        self.assertIn("reason", result)
        self.assertIn("searched_terms", result)
        self.assertIn("missing_key", result)
        assert_no_forbidden(self, result)

    def test_process_remains_compatible_without_formal_prescription(self):
        result = self.m2.process("变应性鼻炎", symptoms=["鼻痒", "喷嚏", "清涕"])
        self.assertEqual(result["status"], "PASS")
        self.assertIn("syndrome_trace", result)
        self.assertIn("formula_candidates", result)
        self.assertTrue(result["candidate_only"])
        self.assertTrue(result["need_m2_3"])
        self.assertTrue(result["must_enter_m3"])
        self.assertFalse(result.get("formal_prescription_allowed", False))
        assert_no_forbidden(self, result)


if __name__ == "__main__":
    unittest.main()
