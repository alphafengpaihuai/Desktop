"""
M4 原病名连续性校验器
模型负责基于 M1 诊断标准卡片做语义比对
代码负责输入校验 + JSON 解析 + 重试 + 兜底
"""
import json
import os
import re
from typing import Dict, Optional


# LLM 提示词路径
PROMPT_PATH = "prompts/m4_diagnosis_continuity_prompt.txt"


class DiagnosisContinuityEvaluator:
    """原病名连续性校验器

    模型部分：基于 M1 诊断卡片判断复诊表现是否仍属原病名
    代码部分：输入校验、JSON 解析、重试、兜底
    """

    def __init__(self):
        self.prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        try:
            with open(PROMPT_PATH, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return self._default_prompt()

    def _default_prompt(self) -> str:
        return """你是 M4 复诊路由模块中的"原病名连续性校验器"。
你只能基于输入中的：1. 原西医病名；2. M1 诊断标准卡片；3. 初诊病历；4. 复诊病历；进行判断。
严禁使用模型自身医学知识扩展新诊断。
严禁辨证。严禁选方。严禁加减药。严禁给治疗建议。
你的任务不是诊断新病，而是判断：复诊表现是否仍属于原西医病名范畴。
判断规则：
- 如果复诊症状、体征、检查仍能被原病名诊断标准解释，输出 RETURN_TO_M2。
- 如果原主诉已退居次要，新主诉成为主要矛盾，输出 RETURN_TO_M1。
- 如果新发症状无法由原病名解释，输出 RETURN_TO_M1。
- 如果出现与原病名明显冲突的客观证据，输出 RETURN_TO_M1。
- 如果出现危险信号，输出 EMERGENCY_STOP。
- 如果证据不足，输出 RETURN_TO_M1，并标注 confidence = low。
必须列出 evidence_for_original_diagnosis 和 evidence_against_original_diagnosis。
不得输出 Markdown。只能输出 JSON。"""

    def evaluate(
        self,
        original_diagnosis: str,
        m1_card: Optional[Dict],
        initial_symptoms: list,
        followup_symptoms: list,
        new_symptoms: list,
        worsened_symptoms: list,
        initial_signs: list = None,
        followup_signs: list = None,
    ) -> Dict:
        """执行原病名连续性校验

        代码部分：
        - 如果缺少 m1_card，直接返回 confidence=low 的兜底结果
        - 如果 LLM 返回非 JSON，重试一次，仍失败则返回兜底

        Args:
            original_diagnosis: 原西医病名
            m1_card: M1 诊断标准卡片
            ...症状列表

        Returns:
            {
                "still_within_original_disease": bool,
                "new_symptoms_explained_by_original_disease": bool,
                "conflicting_evidence_found": bool,
                "major_new_problem": bool,
                "evidence_for_original_diagnosis": [],
                "evidence_against_original_diagnosis": [],
                "unexplained_new_symptoms": [],
                "suggested_route": "RETURN_TO_M2 / RETURN_TO_M1 / EMERGENCY_STOP",
                "confidence": "high / medium / low"
            }
        """
        # ── 代码校验：缺少 M1 诊断卡片 → 兜底 ──
        if not m1_card:
            return {
                "still_within_original_disease": False,
                "new_symptoms_explained_by_original_disease": False,
                "conflicting_evidence_found": True,
                "major_new_problem": True,
                "evidence_for_original_diagnosis": [],
                "evidence_against_original_diagnosis": ["缺少 M1 诊断标准卡片，无法完成连续性校验"],
                "unexplained_new_symptoms": new_symptoms,
                "suggested_route": "RETURN_TO_M1",
                "confidence": "low",
            }

        # ── 构造 LLM 输入 ──
        llm_input = {
            "original_diagnosis": original_diagnosis,
            "m1_diagnostic_card": {
                "required_criteria_zh": m1_card.get("required_criteria_zh", []),
                "supportive_features_zh": m1_card.get("supportive_features_zh", []),
                "differential_diagnosis_zh": m1_card.get("differential_diagnosis_zh", []),
                "differential_distinction_zh": m1_card.get("differential_distinction_zh", []),
                "red_flags_zh": m1_card.get("red_flags_zh", []),
                "common_check_terms_zh": m1_card.get("common_check_terms_zh", []),
            },
            "initial_visit": {
                "symptoms": initial_symptoms,
                "signs": initial_signs or [],
            },
            "followup_visit": {
                "symptoms": followup_symptoms,
                "signs": followup_signs or [],
                "new_symptoms": new_symptoms,
                "worsened_symptoms": worsened_symptoms,
            },
        }

        prompt = self.prompt_template + "\n\n## 输入\n" + json.dumps(llm_input, ensure_ascii=False, indent=2)
        prompt += "\n\n## 输出（只输出 JSON）"

        # ── 调用 LLM，最多重试 1 次 ──
        result = None
        for attempt in range(2):
            raw = self._call_llm(prompt)
            if raw:
                parsed = self._parse_llm_response(raw)
                if parsed:
                    result = parsed
                    break

        # ── LLM 失败返回 → 代码兜底 ──
        if not result:
            return {
                "still_within_original_disease": True,  # 保守假设
                "new_symptoms_explained_by_original_disease": True,
                "conflicting_evidence_found": False,
                "major_new_problem": len(new_symptoms) > 3,  # 新症状多时有可能是新问题
                "evidence_for_original_diagnosis": ["LLM 不可用，默认假设仍属原病名"],
                "evidence_against_original_diagnosis": [] if not new_symptoms else [f"存在 {len(new_symptoms)} 个新发症状需关注"],
                "unexplained_new_symptoms": new_symptoms if len(new_symptoms) > 3 else [],
                "suggested_route": "RETURN_TO_M1" if len(new_symptoms) > 3 else "RETURN_TO_M2",
                "confidence": "low",
            }

        return result

    def _parse_llm_response(self, raw: str) -> Optional[Dict]:
        """解析 LLM 返回 JSON"""
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except (json.JSONDecodeError, AttributeError):
            return None
        return None

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM（复用 M1 的接口）"""
        try:
            from m1_engine import M1DiagnosisEngine
            return M1DiagnosisEngine()._call_llm(prompt, temperature=0.1, max_tokens=600)
        except Exception:
            return None
