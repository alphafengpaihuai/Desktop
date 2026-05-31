"""
M4 症状对比器
100% 代码执行，逐项对比症状变化
"""
import re
from typing import Dict, List, Tuple


class SymptomComparator:
    """症状对比器 — 纯代码执行"""

    IMPROVE_KEYWORDS = ["好转", "减轻", "缓解", "改善", "消失", "减少", "变小", "变浅"]
    WORSEN_KEYWORDS = ["加重", "恶化", "加剧", "更痛", "更差", "增多", "变大", "变深"]
    RESOLVE_KEYWORDS = ["消失", "已无", "不痛", "不痒", "痊愈", "好了"]

    def compare(
        self,
        initial_symptoms: List[str],
        followup_symptoms: List[str],
        patient_feedback: str = "",
    ) -> Dict:
        """对比初诊与复诊症状，分类并计算改善系数

        Returns:
            {
                "improved_symptoms": [],
                "worsened_symptoms": [],
                "persistent_symptoms": [],
                "new_symptoms": [],
                "resolved_symptoms": [],
                "improvement_score": 0-10
            }
        """
        improved = []
        worsened = []
        persistent = []
        new_symptoms = []
        resolved = []

        # 复诊症状归一化处理
        followup_norm = [s.strip() for s in followup_symptoms if s.strip()]

        for s in followup_norm:
            s_lower = s.lower()

            # 关键词分类
            if any(kw in s_lower for kw in self.RESOLVE_KEYWORDS):
                resolved.append(s)
                continue
            if any(kw in s_lower for kw in self.IMPROVE_KEYWORDS):
                improved.append(s)
                continue
            if any(kw in s_lower for kw in self.WORSEN_KEYWORDS):
                worsened.append(s)
                continue

            # 子串匹配：初诊中是否包含该症状
            matched = False
            for init_s in initial_symptoms:
                init_lower = init_s.lower()
                # 双向子串匹配
                if s_lower in init_lower or init_lower in s_lower:
                    persistent.append(s)
                    matched = True
                    break
            if not matched:
                new_symptoms.append(s)

        # 改善系数计算
        score = self._calculate_score(
            improved, worsened, persistent, resolved, new_symptoms, initial_symptoms, patient_feedback
        )

        return {
            "improved_symptoms": improved,
            "worsened_symptoms": worsened,
            "persistent_symptoms": persistent,
            "new_symptoms": new_symptoms,
            "resolved_symptoms": resolved,
            "improvement_score": score,
        }

    def _calculate_score(
        self,
        improved: List[str],
        worsened: List[str],
        persistent: List[str],
        resolved: List[str],
        new_symptoms: List[str],
        initial_symptoms: List[str],
        feedback: str,
    ) -> int:
        """计算 0-10 改善系数 — 纯代码"""
        total_initial = len(initial_symptoms)
        if total_initial == 0:
            return 0

        # 症状分值
        base = 5  # 基准分
        base += len(resolved) * 2      # 消失 +2
        base += len(improved) * 1      # 改善 +1
        base -= len(worsened) * 1      # 加重 -1
        base -= len(new_symptoms) * 1  # 新发 -1

        # 残留扣分（有残留且无改善时）
        if persistent and not improved and not resolved:
            base -= 2

        # 主观反馈
        if feedback:
            fb_lower = feedback.lower()
            if any(kw in fb_lower for kw in ["好", "改善", "缓解", "有效", "不错"]):
                base += 2
            elif any(kw in fb_lower for kw in ["没变", "无变化", "一样", "差不多"]):
                base += 0
            elif any(kw in fb_lower for kw in ["加重", "更差", "不行", "恶化"]):
                base -= 2

        return max(0, min(10, base))
