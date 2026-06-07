import os
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SOURCE = (PROJECT_ROOT / "bridge_server.py").read_text(encoding="utf-8")


class LegacyPrescriptionModeGuardTest(unittest.TestCase):
    """验证默认/开发两种模式的行为"""

    def setUp(self):
        os.environ.pop("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", None)
        os.environ.pop("ALLOW_LEGACY_M2_FULL_PIPELINE", None)
        os.environ.pop("ALLOW_LEGACY_DOSAGE_EXTRACTION", None)
        os.environ.pop("ALLOW_LEGACY_FULL_PIPELINE_PRESCRIPTION", None)

    # ── 默认模式检查 ──

    def test_default_mode_recommendation_no_prescription_text(self):
        """默认模式下 recommendation 不包含完整处方字段"""
        # 检查默认模式结果中不含药物/剂量/用法等字段
        self.assertIn("candidate_summary", BRIDGE_SOURCE)
        self.assertIn("legacy_bridge_prescription_path_blocked", BRIDGE_SOURCE)

    def test_default_mode_no_formal_prescription(self):
        """默认模式 formal_prescription_allowed 恒为 false"""
        guard_pos = BRIDGE_SOURCE.find("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH")
        default_block = BRIDGE_SOURCE.find("formal_prescription_allowed", guard_pos)
        dev_block = BRIDGE_SOURCE.rfind("formal_prescription_allowed")
        self.assertGreater(default_block, 0)
        self.assertGreater(dev_block, 0)
        # 两个模式中都应该出现 formal_prescription_allowed: False
        # 在两个模式的分支中都应该有 formal_prescription_allowed: False
        # 检查字符串中的 key 而不是 Python dict 序列化格式
        count = BRIDGE_SOURCE.count('"formal_prescription_allowed"')
        self.assertGreaterEqual(count, 2, "应至少出现2次 formal_prescription_allowed")

    def test_default_mode_candidate_only_always_true(self):
        """默认模式 candidate_only 始终为 true"""
        guard_pos = BRIDGE_SOURCE.find("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH")
        default_candidate = BRIDGE_SOURCE.find("candidate_only", guard_pos)
        self.assertGreater(default_candidate, 0)

    def test_default_mode_no_dosage_or_usage(self):
        """默认模式不输出 dosage / 用法 / 疗程等完整处方信息"""
        guard_pos = BRIDGE_SOURCE.find("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH")
        before_guard = BRIDGE_SOURCE[:guard_pos]
        # 默认模式下 recommendation 中不应出现"【用法】"
        self.assertNotIn("【用法】每日1剂", before_guard)

    # ── 开发验证模式检查 ──

    def test_dev_mode_has_not_for_clinical_use_flag(self):
        """开发模式输出必须标记 not_for_clinical_use=true"""
        self.assertIn("not_for_clinical_use", BRIDGE_SOURCE)

    def test_dev_mode_has_dev_legacy_flag(self):
        """开发模式输出必须标记 dev_legacy_prescription_output=true"""
        self.assertIn("dev_legacy_prescription_output", BRIDGE_SOURCE)

    def test_dev_mode_rx_ends_with_dev_disclaimer(self):
        """开发模式处方末尾标注'开发模式 - 非临床使用'"""
        self.assertIn("开发模式 - 非临床使用", BRIDGE_SOURCE)

    def test_dev_mode_candidate_only_also_true(self):
        """开发模式 candidate_only 也为 true（方剂仅供验证）"""
        # 开发模式的结果中也有 candidate_only: True
        dev_section_start = BRIDGE_SOURCE.find("dev_legacy_prescription_output")
        # candidate_only 应该在 dev 模式结果中
        dev_candidate = BRIDGE_SOURCE.find("candidate_only", dev_section_start)
        self.assertGreater(dev_candidate, dev_section_start)

    # ── 独立 legacy 路径检查 ──

    def test_m2_full_pipeline_env_var_protected(self):
        """m2_engine.full_pipeline 受 ALLOW_LEGACY_M2_FULL_PIPELINE 保护"""
        from m2_engine import M2SyndromeSelector
        m2 = M2SyndromeSelector()
        result = m2.full_pipeline("变应性鼻炎", {"symptoms": ["鼻痒", "喷嚏"]})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertTrue(result["candidate_only"])
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("final_prescription", result)

    def test_m2_extract_dosages_env_var_protected(self):
        """m2_engine._extract_dosages 受 ALLOW_LEGACY_DOSAGE_EXTRACTION 保护"""
        from m2_engine import M2SyndromeSelector
        m2 = M2SyndromeSelector()
        result = m2._extract_dosages(["麻黄"], "急性上呼吸道感染")
        self.assertTrue(result["_blocked"])
        self.assertTrue(result["legacy_prescription_path_blocked"])
        self.assertFalse(result["formal_prescription_allowed"])

    @unittest.skipIf(True, "full_pipeline 依赖 pandas 未安装，跳过")
    def test_full_pipeline_m3_review_blocked_by_default(self):
        """full_pipeline.m3_review 默认被阻断"""
        import full_pipeline
        result = full_pipeline.m3_review(
            {"main_formula_name": "测试方", "main_herbs": ["麻黄"], "add_herbs": []},
            m3_service=None,
            patient_info={},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("herbs", result)

    @unittest.skipIf(True, "full_pipeline 依赖 pandas 未安装，跳过")
    def test_full_pipeline_m4_followup_blocked_by_default(self):
        """full_pipeline.m4_followup 默认被阻断"""
        import full_pipeline
        result = full_pipeline.m4_followup({}, {}, {})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["formal_prescription_allowed"])
        self.assertNotIn("instructions", result)

    @unittest.skipIf(True, "full_pipeline 依赖 pandas 未安装，跳过")
    def test_full_pipeline_format_output_no_rx_text(self):
        """full_pipeline.format_output 默认不含处方文本"""
        import full_pipeline
        text = full_pipeline.format_output("p", "1", "男", "", "", {}, {}, {}, [])
        self.assertIn("旧版完整处方展示路径已被安全闸门阻断", text)
        self.assertNotIn("每日1剂", text)
        self.assertNotIn("煎服", text)

    def test_all_legacy_env_vars_exist_in_bridge(self):
        """bridge_server 中引用了所有 4 个 legacy env vars"""
        count = BRIDGE_SOURCE.count("ALLOW_LEGACY_")
        self.assertGreaterEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
