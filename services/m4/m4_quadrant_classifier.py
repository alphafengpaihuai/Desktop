"""
M4 四象限分类器
100% 代码执行，基于改善系数 + 窗口 + 症状变化
"""
from typing import Dict


class QuadrantClassifier:
    """四象限分类 — 纯代码执行"""

    def classify(
        self,
        improvement_score: int,
        exceeded_significant_window: bool,
        has_new_symptoms: bool,
        has_worsened_symptoms: bool,
        new_symptoms: list,
        worsened_symptoms: list,
        unresolved_symptoms: list,
    ) -> Dict:
        """四象限分类

        Q1 效佳：改善 ≥ 7，主症缓解，无新发重要症状
        Q2 待效：改善 ≤ 3，未超显效窗口，无加重和红旗
        Q3 激惹：改善 3-6，但出现新发或轻度加重
        Q4 无效：改善 ≤ 2，已超显效窗口 或 主症无改善/加重但仍属原病名

        代码硬规则，不依赖 LLM
        """
        quadrant = None
        suggested_action = None
        reason = None

        # Q1：效佳
        if improvement_score >= 7 and not has_new_symptoms and not has_worsened_symptoms:
            quadrant = "Q1"
            suggested_action = "守方"
            reason = f"改善系数 {improvement_score}/10，主症缓解，疗效明确"
        # Q3：激惹（改善 ≥ 3 但有新问题）
        elif improvement_score >= 3 and (has_new_symptoms or has_worsened_symptoms):
            quadrant = "Q3"
            suggested_action = "调整"
            reason = f"改善系数 {improvement_score}/10，但出现新问题，考虑药证激惹"
        # Q2：待效（改善不足但未超窗口）
        elif not exceeded_significant_window:
            quadrant = "Q2"
            suggested_action = "守方等待"
            reason = f"改善系数 {improvement_score}/10，尚未超出显效窗口，继续守方观察"
        # Q4：无效
        else:
            quadrant = "Q4"
            suggested_action = "更方"
            reason = f"改善系数 {improvement_score}/10，已超出显效窗口，需要更方"

        return {
            "quadrant": quadrant,
            "reason": reason,
            "suggested_action": suggested_action,
        }
