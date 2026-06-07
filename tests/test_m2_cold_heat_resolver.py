"""
M2 寒热冲突裁决测试
测试 resolve_cold_heat_conflict_by_pathology 的四条核心规则。
"""
import unittest
from m2_engine import M2SyndromeSelector


class TestColdHeatResolver(unittest.TestCase):
    """寒热冲突裁决单元测试"""

    @classmethod
    def setUpClass(cls):
        cls.engine = M2SyndromeSelector()

    # ── 规则 1: 怕冷 + 痰白清稀 + 舌淡，无明显炎症 → 风寒/寒证方向 ──
    def test_cold_dominant_no_inflammation(self):
        """规则1: 怕冷+痰白清稀+舌淡+无炎症 → 寒证方向"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["怕冷", "咳嗽", "痰白清稀", "鼻塞"],
            negative_findings=[],
            tongue="舌淡苔薄白",
            pulse="脉浮紧",
            labs=[],
            imaging=[],
            pathology={"inferred_pathology_stage": "表证期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "cold")
        self.assertEqual(result["resolved_by"], "pathology_stage")
        self.assertFalse(result["need_human_review"])
        self.assertIn("怕冷/恶寒", result["evidence_for_main"])

    # ── 规则 2: 怕冷 + 口苦 + 苔黄腻 + 黄痰/CRP升高 → 热证方向 ──
    def test_cold_symptoms_with_inflammation(self):
        """规则2: 有寒象但炎症证据明显(黄痰+CRP) → 热证方向"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["怕冷", "咳嗽", "黄痰", "口苦", "发热"],
            negative_findings=[],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
            labs=["WBC 12.5×10^9/L", "CRP 45mg/L", "中性粒 85%"],
            imaging=[],
            pathology={"inferred_pathology_stage": "里热期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "heat")
        self.assertFalse(result["need_human_review"])
        self.assertIn("黄痰/脓痰", result["evidence_for_main"])
        self.assertIn("发热", result["evidence_for_main"])

    # ── 规则 2: 炎症 moderate 但无明确寒象 ──
    def test_inflammation_moderate_heat_direction(self):
        """规则2: moderate炎症 → 热证方向"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["发热38.5℃", "咳嗽", "黄稠痰", "口干"],
            negative_findings=[],
            tongue="舌红苔黄",
            pulse="脉数",
            labs=["WBC 11.2×10^9/L"],
            imaging=[],
            pathology={"inferred_pathology_stage": "急性炎症期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "heat")
        self.assertFalse(result["need_human_review"])

    # ── 规则 3: 恢复期 + 无炎症 → 不得简单寒热互斥 ──
    def test_recovery_stage_no_simple_conflict(self):
        """规则3: 恢复期+无炎症 → cold方向（虚多倾向虚寒/气虚）"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["咳嗽少痰", "乏力", "气短", "自汗"],
            negative_findings=["无发热", "无黄痰"],
            tongue="舌淡",
            pulse="脉细",
            labs=[],
            imaging=["CT复查: 较前吸收好转"],
            pathology={"inferred_pathology_stage": "正气不足期"},
            factor_contradiction_result={
                "soft_conflict": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热错杂可能"}
                ],
                "need_human_review": True,
            },
        )
        # 恢复期+无炎症+虚象 → cold方向（倾向虚寒/气虚）
        self.assertEqual(result["main_direction"], "cold")
        self.assertFalse(result["need_human_review"])

    # ── 规则 4: 证据不足 → 不强行输出置信 ──
    def test_insufficient_evidence_low_confidence(self):
        """规则4: 无炎症证据+无明显寒热 → unknown方向"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="感冒",
            symptoms=["咳嗽", "头痛"],
            negative_findings=[],
            tongue="舌淡红",
            pulse="脉缓",
            labs=[],
            imaging=[],
            pathology={},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "unknown")
        self.assertEqual(result["resolved_by"], "insufficient_evidence")
        self.assertFalse(result["need_human_review"])

    # ── 寒热夹杂但炎症明显 → 主证取热 ──
    def test_mixed_cold_heat_with_inflammation(self):
        """寒热夹杂但炎症证据明显 → 主证取热"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["怕冷", "发热39℃", "黄绿痰", "口苦", "烦躁"],
            negative_findings=[],
            tongue="舌红苔黄腻",
            pulse="脉滑数",
            labs=["WBC 15×10^9/L", "CRP 80mg/L", "PCT 2.5"],
            imaging=["胸片: 右肺下叶片状高密度影，考虑炎症"],
            pathology={"inferred_pathology_stage": "里热期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "heat")
        self.assertFalse(result["need_human_review"])
        self.assertIn("寒象（兼夹）", result["evidence_against"])

    # ── 消耗状态（肿瘤术后） → mixed, 不标记 human_review ──
    def test_consumptive_state_mixed_direction(self):
        """消耗状态(术后/肿瘤) → mixed 方向，不标记 need_human_review"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="喉癌",
            symptoms=["术后", "声音嘶哑", "消瘦", "纳差", "乏力", "咳嗽"],
            negative_findings=["无发热"],
            tongue="舌淡暗",
            pulse="脉细弱",
            labs=[],
            imaging=[],
            pathology={"inferred_pathology_stage": "正气不足期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertIn(result["main_direction"], ("mixed", "cold"))
        self.assertFalse(result["need_human_review"])

    # ── 轻度炎症 + 寒象为主 → 低置信度寒证方向 ──
    def test_mild_inflammation_cold_dominant(self):
        """轻度炎症但寒象为主 → mixed方向（发热+寒象并存，证据不足）"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["怕冷", "咳嗽", "痰白清稀", "发热37.3℃"],
            negative_findings=[],
            tongue="舌淡苔白",
            pulse="脉浮",
            labs=["WBC 8.5×10^9/L（正常）"],
            imaging=[],
            pathology={"inferred_pathology_stage": "表证期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        # 发热37.3℃ + 怕冷 → 轻炎症但寒热并存 → mixed方向（非纯寒）
        self.assertEqual(result["main_direction"], "mixed")
        self.assertEqual(result["severity"], "mild")
        self.assertEqual(result["resolved_by"], "low_confidence")
        self.assertTrue(result["need_human_review"])

    # ── 早搏期 + severe 炎症 → heat 方向 ──
    def test_early_stage_severe_inflammation_heat(self):
        """早期但有 severe 炎症 → heat 方向"""
        result = self.engine.resolve_cold_heat_conflict_by_pathology(
            disease_key="肺炎",
            symptoms=["发热40℃", "咳嗽", "黄绿痰", "胸痛"],
            negative_findings=[],
            tongue="舌红绛苔黄燥",
            pulse="脉洪数",
            labs=["WBC 18×10^9/L", "CRP 120mg/L", "PCT 10"],
            imaging=["CT: 双肺多发斑片状实变影"],
            pathology={"inferred_pathology_stage": "表证期"},
            factor_contradiction_result={
                "strong_against": [
                    {"factor_a": "寒", "factor_b": "热", "reason": "寒热互斥"}
                ],
                "need_human_review": True,
            },
        )
        self.assertEqual(result["main_direction"], "heat")
        self.assertFalse(result["need_human_review"])
        self.assertEqual(result["severity"], "severe")


if __name__ == "__main__":
    unittest.main()
