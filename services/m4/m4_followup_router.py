"""
M4 复诊路由分发模块（主入口）
80% 代码 + 20% 模型
"""
import json
from typing import Dict, List, Optional, Tuple

from .m4_danger_signal_detector import DangerSignalDetector
from .m4_symptom_comparator import SymptomComparator
from .m4_window_checker import WindowChecker
from .m4_diagnosis_continuity_evaluator import DiagnosisContinuityEvaluator
from .m4_quadrant_classifier import QuadrantClassifier
from .m4_audit_trace import AuditTrace


class FollowupRouter:
    """M4 复诊路由分发主引擎

    流程：
    1. 危险信号检测（代码，最高优先级）
    2. 输入校验（代码）→ 校验失败返回 INSUFFICIENT_INFO（PENDING）
    3. 症状对比（代码）
    4. 病程窗口判断（代码）
    5. 原病名连续性校验（模型，基于 M1 卡片）
    6. 最终路由决策（代码兜底）
    7. 四象限分类（代码，仅 RETURN_TO_M2）
    8. evidence_trace（代码）
    """

    def __init__(self):
        self.danger_detector = DangerSignalDetector()
        self.comparator = SymptomComparator()
        self.window_checker = WindowChecker()
        self.continuity_evaluator = DiagnosisContinuityEvaluator()
        self.quadrant_classifier = QuadrantClassifier()
        self.trace = AuditTrace()

    def route(
        self,
        initial_diagnosis: str,
        initial_symptoms: List[str],
        followup_symptoms: List[str],
        days_since_initial: int,
        initial_signs: Optional[List[str]] = None,
        followup_signs: Optional[List[str]] = None,
        patient_feedback: str = "",
        vital_signs: Optional[Dict] = None,
        new_complaints: Optional[List[str]] = None,
        m1_card: Optional[Dict] = None,
        labs: Optional[Dict] = None,
        initial_labs: Optional[Dict] = None,
        unavailable_info: Optional[List[str]] = None,
        required_inquiries: Optional[List[str]] = None,
    ) -> Dict:
        """主路由方法

        Args:
            ...其他参数见上方签名
            unavailable_info: 不可用的信息列表（信息不足时标记）
            required_inquiries: 必须询问的理由列表（供 M1 追问参考）

        Returns: 标准输出格式
        """
        self.trace.reset()

        # ════════════════════════════════════════════════
        # Step 1：危险信号检测（代码，最高优先级）
        # 必须在输入校验之前执行 —— 即使缺少 m1_card 也要拦截危险信号
        # ════════════════════════════════════════════════
        danger_result = self.danger_detector.detect(
            vital_signs=vital_signs,
            symptoms=followup_symptoms,
            new_complaints=new_complaints,
            labs=labs,
        )
        self.trace.add_rule("danger_signal_detection", f"triggered={danger_result['triggered']}")
        if danger_result["triggered"]:
            result = self._build_decision(
                routing_action="EMERGENCY_STOP",
                routing_reason="命中危险信号，建议立即急诊",
                visit_comparison=None,
                window_result=None,
                validation_result=None,
                danger_result=danger_result,
                quadrant=None,
            )
            result["status"] = "EMERGENCY"
            return result

        # ════════════════════════════════════════════════
        # Step 2：输入校验（代码）
        # ════════════════════════════════════════════════
        # 注意：危险信号已在 Step 1 拦截，这里只校验基本流程完整性
        validation_error = self._validate_input(
            initial_diagnosis, initial_symptoms, followup_symptoms,
            days_since_initial, m1_card
        )
        if validation_error:
            return self._build_insufficient_response(
                error_message=validation_error,
                danger_result=danger_result,
            )

        self.trace.add_initial_field("western_diagnosis")
        self.trace.add_initial_field("symptoms")
        self.trace.add_followup_field("symptoms")
        self.trace.add_followup_field("days_since_initial")
        if m1_card:
            for f in ["required_criteria_zh", "supportive_features_zh", "differential_diagnosis_zh"]:
                if f in m1_card:
                    self.trace.add_m1_card_field(f)

        # ════════════════════════════════════════════════
        # Step 3：症状对比（代码）
        # ════════════════════════════════════════════════
        comparison = self.comparator.compare(
            initial_symptoms=initial_symptoms,
            followup_symptoms=followup_symptoms,
            patient_feedback=patient_feedback,
        )
        self.trace.add_rule("symptom_comparison",
            f"score={comparison['improvement_score']}, "
            f"improved={len(comparison['improved_symptoms'])}, "
            f"new={len(comparison['new_symptoms'])}"
        )

        # ════════════════════════════════════════════════
        # Step 4：病程窗口判断（代码）
        # ════════════════════════════════════════════════
        window_result = self.window_checker.check(
            disease=initial_diagnosis,
            days=days_since_initial,
            m1_card=m1_card,
        )
        self.trace.add_rule("window_check",
            f"source={window_result['window_source']}, "
            f"onset={window_result['expected_onset_days']}d, "
            f"significant={window_result['expected_significant_days']}d"
        )
        if window_result["needs_review"]:
            self.trace.add_note(f"病程窗口使用默认值（{window_result['window_source']}），建议医生复核")

        # ════════════════════════════════════════════════
        # Step 5：原病名连续性校验（模型，基于 M1 卡片）
        # ════════════════════════════════════════════════
        validation_result = self.continuity_evaluator.evaluate(
            original_diagnosis=initial_diagnosis,
            m1_card=m1_card,
            initial_symptoms=initial_symptoms,
            followup_symptoms=followup_symptoms,
            new_symptoms=comparison["new_symptoms"],
            worsened_symptoms=comparison["worsened_symptoms"],
            initial_signs=initial_signs,
            followup_signs=followup_signs,
        )
        self.trace.set_llm_summary(validation_result)
        self.trace.add_rule("diagnosis_continuity_evaluation",
            f"still_valid={validation_result.get('still_within_original_disease', False)}, "
            f"confidence={validation_result.get('confidence', 'low')}"
        )

        # ════════════════════════════════════════════════
        # Step 6：最终路由决策（代码兜底）
        # ════════════════════════════════════════════════
        routing_decision = self._make_routing_decision(
            validation_result=validation_result,
            danger_result=danger_result,
        )
        self.trace.add_rule("routing_decision",
            f"action={routing_decision['action']}, "
            f"reason={routing_decision['reason']}"
        )

        # ════════════════════════════════════════════════
        # Step 7：四象限分类（代码，仅 RETURN_TO_M2）
        # ════════════════════════════════════════════════
        quadrant_result = None
        if routing_decision["action"] == "RETURN_TO_M2":
            quadrant_result = self.quadrant_classifier.classify(
                improvement_score=comparison["improvement_score"],
                exceeded_significant_window=window_result["exceeded_significant_window"],
                has_new_symptoms=len(comparison["new_symptoms"]) > 0,
                has_worsened_symptoms=len(comparison["worsened_symptoms"]) > 0,
                new_symptoms=comparison["new_symptoms"],
                worsened_symptoms=comparison["worsened_symptoms"],
                unresolved_symptoms=comparison["persistent_symptoms"],
            )
            routing_decision["quadrant"] = quadrant_result["quadrant"]
            routing_decision["suggested_action"] = quadrant_result["suggested_action"]
            routing_decision["reason"] = quadrant_result["reason"]
            self.trace.add_rule("quadrant_classification",
                f"quadrant={quadrant_result['quadrant']}, "
                f"suggest={quadrant_result['suggested_action']}"
            )

        result = self._build_decision(
            routing_action=routing_decision["action"],
            routing_reason=routing_decision["reason"],
            visit_comparison=comparison,
            window_result=window_result,
            validation_result=validation_result,
            danger_result=danger_result,
            quadrant=routing_decision.get("quadrant", None),
            suggested_action=routing_decision.get("suggested_action", None),
        )
        result["status"] = "READY"
        return result

    # ══════════════════════════════════════════════════════
    #  辅助方法
    # ══════════════════════════════════════════════════════

    def _validate_input(
        self,
        initial_diagnosis: str,
        initial_symptoms: List[str],
        followup_symptoms: List[str],
        days_since_initial: int,
        m1_card: Optional[Dict],
    ) -> Optional[str]:
        """输入校验 — 代码执行"""
        if not initial_diagnosis or not initial_diagnosis.strip():
            return "缺少初诊西医病名"
        if not initial_symptoms:
            return "缺少初诊症状"
        if not followup_symptoms:
            return "缺少复诊病历"
        if days_since_initial <= 0:
            return "缺少或无效的距初诊天数"
        if not m1_card:
            return "缺少原病名诊断标准卡片，无法完成连续性校验"
        return None

    def _make_routing_decision(self, validation_result: Dict, danger_result: Dict) -> Dict:
        """最终路由决策 — 代码兜底裁决

        优先级（代码硬规则）：
        1. 危险信号触发 → EMERGENCY_STOP
        2. major_new_problem → RETURN_TO_M1
        3. conflicting_evidence → RETURN_TO_M1
        4. still_within_original_disease = false → RETURN_TO_M1
        5. confidence = low 且存在新发症状 → RETURN_TO_M1
        6. 否则 → RETURN_TO_M2
        """
        # 优先级 1
        if danger_result.get("triggered", False):
            return {"action": "EMERGENCY_STOP", "reason": "危险信号触发，建议立即急诊", "quadrant": None, "suggested_action": "急诊"}

        # 优先级 2
        if validation_result.get("major_new_problem", False):
            return {"action": "RETURN_TO_M1", "reason": "出现新的主要矛盾，原病名不再主导临床表现", "quadrant": None, "suggested_action": "重新诊断"}

        # 优先级 3
        if validation_result.get("conflicting_evidence_found", False):
            return {"action": "RETURN_TO_M1", "reason": "出现与原病名冲突的客观证据", "quadrant": None, "suggested_action": "重新诊断"}

        # 优先级 4
        if not validation_result.get("still_within_original_disease", True):
            return {"action": "RETURN_TO_M1", "reason": "复诊表现不再符合原西医病名范畴", "quadrant": None, "suggested_action": "重新诊断"}

        # 优先级 5
        unexplained = validation_result.get("unexplained_new_symptoms", [])
        if validation_result.get("confidence", "high") == "low" and len(unexplained) > 0:
            return {"action": "RETURN_TO_M1", "reason": f"模型低置信度且存在无法解释的新发症状：{unexplained}", "quadrant": None, "suggested_action": "重新诊断"}

        # 优先级 6
        return {"action": "RETURN_TO_M2", "reason": "复诊表现仍属于原西医病名范畴", "quadrant": None, "suggested_action": ""}

    def _build_insufficient_response(
        self,
        error_message: str,
        danger_result: Optional[Dict] = None,
    ) -> Dict:
        """构建信息不足响应（INSUFFICIENT_INFO 模式）

        当输入校验失败时调用。标记不可用信息，列出必须询问的理由。
        路由决策标记为 PENDING，建议转 M1 追问后重新进入 M4。
        """""
        # 从错误信息推断不可用项和询问理由
        if "初诊西医病名" in error_message:
            unavailable = ["初诊西医病名缺失"]
            inquiries = ["缺少初诊西医病名，必须询问初诊时由 M1 确定的诊断"]
        elif "初诊症状" in error_message:
            unavailable = ["初诊症状缺失"]
            inquiries = ["缺少初诊症状记录，必须询问初诊时的具体症状表现"]
        elif "复诊病历" in error_message:
            unavailable = ["复诊症状缺失"]
            inquiries = ["缺少复诊症状记录，必须询问复诊时患者的当前症状"]
        elif "距初诊天数" in error_message:
            unavailable = ["距初诊天数无效"]
            inquiries = ["缺少复诊距初诊的天数，必须确认患者上次就诊日期"]
        elif "诊断标准卡片" in error_message:
            unavailable = ["原病名诊断标准卡片缺失"]
            inquiries = ["缺少原病名诊断标准，必须通过 M1 查询本地库或在缓存中检索"]
            route_action = "RETURN_TO_M1"
            route_reason = "缺少原病名诊断标准，需返回 M1 补齐诊断依据后再进入 M4"
        else:
            unavailable = [error_message]
            inquiries = ["信息不完整，需要通过 M1 追问补充"]
            route_action = "PENDING"
            route_reason = "信息不足以做出路由决策，建议转 M1 追问后重新进入 M4"

        if "route_action" not in locals():
            route_action = "PENDING"
            route_reason = "信息不足以做出路由决策，建议转 M1 追问后重新进入 M4"

        trace_info = self.trace.build()
        return {
            "status": "INSUFFICIENT_INFO",
            "unavailable_info": unavailable,
            "required_inquiries": inquiries,
            "routing_decision": {
                "action": route_action,
                "reason": route_reason,
            },
            "danger_signals": danger_result or {"triggered": False, "signals": [], "recommendation": ""},
            "evidence_trace": trace_info,
        }

    def _build_decision(
        self,
        routing_action: str,
        routing_reason: str,
        visit_comparison: Optional[Dict],
        window_result: Optional[Dict],
        validation_result: Optional[Dict],
        danger_result: Optional[Dict],
        quadrant: Optional[str] = None,
        suggested_action: Optional[str] = None,
    ) -> Dict:
        """构建标准输出（信息足够时）— 代码执行"""
        # 默认空值
        empty_comparison = {
            "improved_symptoms": [], "worsened_symptoms": [],
            "persistent_symptoms": [], "new_symptoms": [],
            "resolved_symptoms": [], "improvement_score": 0,
        }
        empty_window = {
            "disease": "", "expected_onset_days": 0, "expected_significant_days": 0,
            "current_day": 0, "within_onset_window": True,
            "within_significant_window": True, "exceeded_significant_window": False,
            "window_source": "", "needs_review": False,
        }
        empty_validation = {
            "original_diagnosis": "",
            "still_valid": True, "new_symptoms_explained": True,
            "conflicting_evidence_found": False, "major_new_problem": False,
            "confidence": "low",
            "evidence_for": [], "evidence_against": [],
            "unexplained_new_symptoms": [],
        }
        empty_danger = {"triggered": False, "signals": [], "recommendation": ""}

        # 填充 validation
        if validation_result:
            validated = {
                "original_diagnosis": validation_result.get("original_diagnosis", ""),
                "still_valid": validation_result.get("still_within_original_disease", True),
                "new_symptoms_explained": validation_result.get("new_symptoms_explained_by_original_disease", True),
                "conflicting_evidence_found": validation_result.get("conflicting_evidence_found", False),
                "major_new_problem": validation_result.get("major_new_problem", False),
                "confidence": validation_result.get("confidence", "low"),
                "evidence_for": validation_result.get("evidence_for_original_diagnosis", []),
                "evidence_against": validation_result.get("evidence_against_original_diagnosis", []),
                "unexplained_new_symptoms": validation_result.get("unexplained_new_symptoms", []),
            }
        else:
            validated = empty_validation

        # 如果输入校验失败，original_diagnosis 已知
        if not validated["original_diagnosis"]:
            validated["original_diagnosis"] = ""  # 从外部传入？暂时不用

        # 四象限
        if routing_action != "RETURN_TO_M2":
            quadrant_out = None
            suggested_out = None
            if routing_action == "EMERGENCY_STOP":
                suggested_out = "急诊"
            elif routing_action == "RETURN_TO_M1":
                suggested_out = "重新诊断"
        else:
            quadrant_out = quadrant
            suggested_out = suggested_action

        return {
            "visit_comparison": visit_comparison or empty_comparison,
            "disease_window": window_result or empty_window,
            "disease_name_validation": validated,
            "routing_decision": {
                "action": routing_action,
                "reason": routing_reason,
                "quadrant": quadrant_out,
                "suggested_action": suggested_out,
            },
            "danger_signals": danger_result or empty_danger,
            "evidence_trace": self.trace.build(),
        }
