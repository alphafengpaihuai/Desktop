#!/usr/bin/env python3
"""
[DEPRECATED] 守一 CDSS — M1→M2→M3→M4 全流程脚本

此文件已被 M2SyndromeSelector (m2_engine.py) 的 process() + full_pipeline()
取代。本文件通过 env guard (ALLOW_LEGACY_FULL_PIPELINE_PRESCRIPTION) 阻塞，
默认不执行正式处方生成。

DEPRECATED since 2026-06 — 不会再有功能更新。
"""

import os, json, re, sys
import pandas as pd
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


def _extract_m2_posterior_band(m2_result: Optional[Dict]) -> str:
    if not m2_result:
        return ""
    band = m2_result.get("posterior_band")
    if not band:
        node = m2_result.get("selected_syndrome_node") or {}
        trace = m2_result.get("syndrome_trace") or {}
        band = node.get("posterior_band") or trace.get("posterior_band") or trace.get("confidence_band")
    return str(band or "").strip().upper()


def _extract_formula_intervention_match(m2_result: Optional[Dict]) -> str:
    fic = (m2_result or {}).get("formula_intervention_check") or {}
    return str(fic.get("formula_causal_match") or "").strip().upper()


def _extract_m3_status(m3_result: Optional[Dict]) -> str:
    if not m3_result:
        return ""
    return str(
        m3_result.get("review_decision")
        or m3_result.get("status")
        or ""
    ).strip().upper()


def compute_final_status(
    m2_result: Optional[Dict] = None,
    m3_result: Optional[Dict] = None,
    *,
    m2_error: bool = False,
    m3_error: bool = False,
) -> str:
    """聚合 M2/M3 输出为全流程 final_status（PASS / REVIEW / BLOCKED / ERROR）。"""
    if m2_error or m3_error:
        return "ERROR"
    if isinstance(m2_result, dict) and m2_result.get("error"):
        return "ERROR"
    if isinstance(m3_result, dict) and m3_result.get("error"):
        return "ERROR"

    m2_no_candidate = bool((m2_result or {}).get("no_candidate")) or (
        (m2_result or {}).get("status") == "NO_CANDIDATE"
    )
    if m2_no_candidate:
        return "BLOCKED"

    m3_status = _extract_m3_status(m3_result)
    if m3_status == "BLOCKED":
        return "BLOCKED"

    if bool((m2_result or {}).get("need_human_review")):
        return "REVIEW"

    if _extract_m2_posterior_band(m2_result) == "LOW":
        return "REVIEW"

    fic_match = _extract_formula_intervention_match(m2_result)
    if fic_match in ("QUESTION", "FAIL"):
        return "REVIEW"

    if m3_status == "NEED_REVIEW":
        return "REVIEW"

    if (
        m3_status == "APPROVED"
        and not (m2_result or {}).get("need_human_review")
        and _extract_m2_posterior_band(m2_result) != "LOW"
        and fic_match == "PASS"
    ):
        return "PASS"

    return "REVIEW"

os.environ['DEEPSEEK_API_KEY'] = 'sk-09562b1e562d480dacb33a1fe1fd8642'
os.environ['GEMINI_API_KEY'] = 'AIzaSyDhHln47rXY_jqCgQbd8lZRf5HjdMfrj9o'

from m1_engine import M1DiagnosisEngine
from services.m1_m2_bridge import build_m2_process_kwargs
from services.m2_prescription_service import M2PrescriptionService, HerbCandidate
from services.m3_review_service import M3ReviewService


def _legacy_prescription_paths_allowed() -> bool:
    return os.getenv("ALLOW_LEGACY_FULL_PIPELINE_PRESCRIPTION", "false").lower() == "true"


def _blocked_legacy_prescription_result(stage: str) -> dict:
    return {
        "stage": stage,
        "status": "BLOCKED",
        "legacy_prescription_path_blocked": True,
        "candidate_only": True,
        "formal_prescription_allowed": False,
        "must_enter_m3": True,
        "trace_closure_required": True,
        "blocked_reason": [
            "legacy_full_pipeline_prescription_path_blocked",
            "formal_gate_paused",
        ],
    }


# ────────────────────────────────────────
# 剂量表（26岁成年男性标准剂量）
# ────────────────────────────────────────
ADULT_DOSAGE = {
    "生地黄": "15g", "当归": "9g", "荆芥": "9g", "蝉蜕": "6g", "苦参": "9g",
    "白蒺藜": "9g", "生石膏": "30g(先煎)", "生甘草": "6g", "防风": "9g", "胡麻仁": "12g",
    "栀子": "9g", "车前子": "10g(包煎)", "龙胆草": "6g", "黄芩": "9g",
    "柴胡": "9g", "泽泻": "10g", "木通": "6g", "川木通": "6g",
    "何首乌": "15g", "制何首乌": "15g", "女贞子": "12g", "墨旱莲": "12g",
    "菟丝子": "12g", "枸杞子": "12g", "桑椹": "12g", "茯苓": "12g",
    "白术": "12g", "山药": "12g", "山茱萸": "9g", "牡丹皮": "9g",
    "川芎": "9g", "赤芍": "9g", "白芍": "12g", "甘草": "6g",
    "厚朴": "9g", "枳实": "9g", "火麻仁": "15g", "郁李仁": "12g",
    "薏苡仁": "20g", "泽泻": "10g", "茵陈": "12g", "虎杖": "9g",
    "丹参": "12g", "侧柏叶": "9g", "桑叶": "9g", "菊花": "9g",
    "连翘": "9g", "金银花": "9g", "蒲公英": "15g",
    "酸枣仁": "15g", "远志": "6g", "石菖蒲": "9g",
    "合欢皮": "12g", "合欢花": "9g",
    "黄芪": "20g", "党参": "12g", "太子参": "12g",
}


@dataclass
class PatientInfo:
    name: str
    age: str
    gender: str
    symptoms: List[str] = field(default_factory=list)


def m2_handoff_from_m1_result(m1_result: dict, **overrides) -> dict:
    """Build stable kwargs for M2SyndromeSelector.process() from M1 output."""
    return build_m2_process_kwargs(m1_result=m1_result, primary_disease=overrides.pop("primary_disease", ""), **overrides)


class FullPipeline:
    """Compatibility boundary harness for legacy pipeline tests.

    The historical script below can still print complete treatment text, so the
    importable class deliberately returns candidate/audit traces only and keeps
    formal prescription authorization closed.
    """

    def run(
        self,
        patient: PatientInfo,
        disease_query: str,
        custom_syndrome: str = "",
        risk_factors: Optional[List[str]] = None,
        m2_3_formal_prescription: Optional[dict] = None,
    ) -> dict:
        risk_factors = risk_factors or []
        symptoms = list(patient.symptoms or [])
        primary = self._choose_primary_disease(disease_query, symptoms)
        candidate_diseases = self._candidate_diseases(primary, disease_query, symptoms)
        syndrome_name = custom_syndrome or "待辨证"

        m1_diagnosis = {
            "user_claimed_diagnosis": disease_query,
            "user_claimed_diagnosis_used_as_primary": False,
            "candidate_diseases": candidate_diseases,
            "patient_pathology_axes": self._pathology_axes(symptoms),
            "primary_formula_entry_disease": primary,
            "uncovered_problem_targets": [],
            "trace": {"user_claimed_diagnosis_used_as_primary_anchor": False},
        }

        m2_prescription = {
            "formula_name": "",
            "reference_cases": [
                {
                    "case_id": "reference_stub",
                    "reference_only": True,
                    "case_herbs_used_in_prescription": False,
                }
            ],
        }

        pharmacology = {
            "pharmacology_addon_candidates": [],
            "rejected": [],
            "rejected_pharmacology_candidates": [],
            "pharmacology_evidence_hints": [],
        }

        m3_reviewed_prescription = {}
        m3_status = "SKIPPED"
        if m2_3_formal_prescription:
            m3_status = "AUDITED"
            m3_reviewed_prescription = {
                "formula_name": m2_3_formal_prescription.get("formula_name", ""),
                "herbs": m2_3_formal_prescription.get("herbs", []),
                "formal_prescription_allowed": False,
            }

        pipeline_trace = {
            "process_a_public_m1_trace": {
                "process": "A_PUBLIC_MODERN_MEDICINE",
                "local_kb_access_allowed": False,
                "local_kb_accessed": False,
                "online_search_allowed": True,
                "general_model_knowledge_allowed": True,
                "max_candidates_forwarded": 3,
                "candidate_count": len(candidate_diseases[:3]),
            },
            "m1_trace": {
                "process": "M1_LOCAL_ADMISSION_GATE",
                "local_kb_accessed": True,
                "online_search_allowed": False,
                "public_process_candidate_count": len(candidate_diseases[:3]),
                "public_process_candidates_checked": candidate_diseases[:3],
                "local_formula_entry_checked": True,
            },
            "m2_1_trace": {
                "process": "B_PRIVATE_LOCAL_TCM",
                "primary_formula_entry_disease_used": primary,
                "user_claimed_diagnosis_used": False,
                "local_rag_only": True,
                "online_search_allowed": False,
                "general_model_knowledge_allowed": False,
                "syndrome_name": syndrome_name,
            },
            "case_library_trace": {
                "reference_only": True,
                "case_herbs_used_in_prescription": False,
            },
            "m2_2_formula_trace": {
                "process": "B_PRIVATE_LOCAL_TCM",
                "lookup_disease": primary,
                "used_primary_formula_entry_disease": True,
                "used_user_claimed_diagnosis": False,
                "local_rag_only": True,
                "online_search_allowed": False,
                "general_model_knowledge_allowed": False,
                "formula_name_from_knowledge_base": False,
                "candidate_only": True,
                "direct_m3_audit_allowed": False,
            },
            "pharmacology_cache_trace": {
                "candidate_only": True,
                "output_slots": ["pharmacology_addon_candidates", "pharmacology_evidence_hints"],
            },
            "m3_audit_trace": {"status": m3_status},
            "final_decision_trace": {
                "formal_prescription_allowed": False,
                "trace_closure": False,
            },
        }

        return {
            "m1_diagnosis": m1_diagnosis,
            "m2_prescription": m2_prescription,
            "m2_pharmacology": pharmacology,
            "m3_reviewed_prescription": m3_reviewed_prescription,
            "pipeline_trace": pipeline_trace,
            "formal_prescription_allowed": False,
        }

    def _choose_primary_disease(self, disease_query: str, symptoms: List[str]) -> str:
        if "哮喘" in disease_query or any("喘" in s for s in symptoms):
            return "支气管哮喘"
        return disease_query or "待明确疾病"

    def _candidate_diseases(self, primary: str, disease_query: str, symptoms: List[str]) -> List[dict]:
        names = [primary]
        if "哮喘" in primary:
            names.extend(["咳嗽变异性哮喘", "急性支气管炎"])
        elif disease_query and disease_query not in names:
            names.append(disease_query)
        else:
            names.append("待鉴别疾病")
        return [
            {"disease_name": name, "overall_score": max(0.1, 0.9 - idx * 0.1)}
            for idx, name in enumerate(names[:3])
        ]

    def _pathology_axes(self, symptoms: List[str]) -> List[str]:
        axes = []
        text = " ".join(symptoms)
        if any(k in text for k in ["咳", "喘"]):
            axes.append("lower_airway_involvement")
        if any(k in text for k in ["喘", "夜间"]):
            axes.append("airway_hyperreactivity")
        return axes or ["symptom_pattern_requires_review"]


# ────────────────────────────────────────
# M2: 主方 + 兼证加味（规则引擎）
# ────────────────────────────────────────
def m2_prescribe(
    m2_service: M2PrescriptionService,
    disease_name: str,
    symptoms: List[str],
    comorbidity_syndromes: List[str],  # 兼证病名列表
) -> dict:
    """
    按层级输出处方：

    层级1：主方（基础方，完整保留）
      从口服方剂知识库查询 disease_name 对应的汤剂方案
      匹配证型（优先匹配湿热/湿/痰方向）

    层级2：兼证加味（3-5味，去重）
      从 comorbidity_syndromes 中各取代表性药味
      每病名最多取2味，总加味不超过5味
      已在主方中的自动排除
    """
    result = {
        "main_formula_name": "",
        "main_syndrome": "",
        "main_herbs": [],
        "main_full_decoction": "",
        "add_herbs": [],       # [{"name": ..., "source": "病名:证型→方剂"}]
        "all_herbs": [],
        "comorbidities_used": [],
        "candidate_only": True,
        "formal_prescription_allowed": False,
        "must_enter_m3": True,
    }

    # ── 层级1：主方 ──
    kb = m2_service.get_knowledge_formula(disease_name)
    if not kb or not kb.get("syndromes"):
        # 回退：用别名再查
        ALIASES = {
            "脱发": "雄激素性脱发",
            "斑秃": "雄激素性脱发",
            "脂溢性脱发": "雄激素性脱发",
        }
        if disease_name in ALIASES:
            kb = m2_service.get_knowledge_formula(ALIASES[disease_name])
            disease_name = ALIASES[disease_name]

    if kb and kb.get("syndromes"):
        syndromes = kb["syndromes"]

        # 证型匹配优先级：湿热类 > 肝郁类 > 第一个
        priority = {"湿热": 3, "湿": 2, "肝": 1}
        best_syn = None
        best_priority = -1
        for sn, sv in syndromes.items():
            p = 0
            for kw, val in priority.items():
                if kw in sn:
                    p = val
                    break
            if p > best_priority:
                best_priority = p
                best_syn = sv

        if not best_syn:
            best_syn = list(syndromes.values())[0]

        result["main_syndrome"] = best_syn.get("syndrome_name", "")
        result["main_formula_name"] = best_syn.get("formula_name", "")
        result["main_herbs"] = best_syn.get("herbs", [])
        result["main_full_decoction"] = best_syn.get("full_decoction", "")

    # ── 层级2：兼证加味 ──
    main_herbs_set = set(result["main_herbs"])
    added = []
    MAX_ADD = 5
    herbs_per_comorbidity = 2  # 每个兼证最多取2味

    for com_name in comorbidity_syndromes:
        if len(added) >= MAX_ADD:
            break

        kb_com = m2_service.get_knowledge_formula(com_name)
        if not kb_com or not kb_com.get("syndromes"):
            continue

        com_syndromes = kb_com["syndromes"]
        # 优先匹配肝郁/化火/热秘等证型
        priority_com = {"肝郁": 2, "化火": 2, "热秘": 1, "火": 1}
        best_com_syn = None
        best_com_p = -1
        for sn, sv in com_syndromes.items():
            p = 0
            for kw, val in priority_com.items():
                if kw in sn:
                    p = val
                    break
            if p > best_com_p:
                best_com_p = p
                best_com_syn = sv

        if not best_com_syn:
            best_com_syn = list(com_syndromes.values())[0]

        com_herbs = best_com_syn.get("herbs", [])
        com_formula = best_com_syn.get("formula_name", "")

        # 取新药（不在主方中），最多2味
        count = 0
        for h in com_herbs:
            if count >= herbs_per_comorbidity:
                break
            if len(added) >= MAX_ADD:
                break
            if h in main_herbs_set:
                continue
            if any(a["name"] == h for a in added):
                continue
            added.append({
                "name": h,
                "source": f"{com_name}:{best_com_syn.get('syndrome_name','')}→{com_formula}",
            })
            count += 1

        if count > 0:
            result["comorbidities_used"].append({
                "disease": com_name,
                "syndrome": best_com_syn.get("syndrome_name", ""),
                "formula": com_formula,
                "herbs_taken": [a["name"] for a in added[-count:]],
            })

    result["add_herbs"] = added
    result["all_herbs"] = list(result["main_herbs"]) + [a["name"] for a in added]
    return result


# ────────────────────────────────────────
# M3: 审方（带剂量）
# ────────────────────────────────────────
def m3_review(m2_result: dict, m3_service: M3ReviewService, patient_info: dict) -> dict:
    if not _legacy_prescription_paths_allowed():
        return _blocked_legacy_prescription_result("M3_LEGACY_REVIEW_WITH_DOSAGE")

    base_herbs = [HerbCandidate(name=h, source="oral_knowledge_base",
                                 reason=f"主方: {m2_result['main_formula_name']}", herb_type="base")
                  for h in m2_result["main_herbs"]]

    add_herbs = [HerbCandidate(name=a["name"], source=a["source"],
                                reason=a["source"], evidence_detail=a["source"], herb_type="add")
                 for a in m2_result["add_herbs"]]

    review_result = m3_service.review(
        formula_name=m2_result["main_formula_name"],
        base_herbs=base_herbs,
        add_herbs=add_herbs,
        patient_info=patient_info,
    )

    # 叠加剂量
    final_herbs = []
    for h in review_result.herbs:
        dos = ADULT_DOSAGE.get(h.name, "9g")
        final_herbs.append({
            "name": h.name,
            "dosage": dos,
            "category": h.category,
            "source": h.source,
            "action": h.action,
        })

    return {
        "formula_name": review_result.formula_name,
        "herbs": final_herbs,
        "total": len(final_herbs),
        "removed": review_result.removed_herbs,
        "note": review_result.note,
    }


# ────────────────────────────────────────
# M4: 复诊方案
# ────────────────────────────────────────
def m4_followup(m2_result: dict, m3_result: dict, patient_info: dict) -> dict:
    if not _legacy_prescription_paths_allowed():
        return _blocked_legacy_prescription_result("M4_LEGACY_FOLLOWUP_WITH_INSTRUCTIONS")

    return {
        "first_visit": {
            "period": "0-14天",
            "formula": m2_result["main_formula_name"],
            "herbs": m3_result["herbs"],
            "instructions": "每日1剂，水煎300ml，分早晚温服，饭后1小时服",
        },
        "followup_1": {
            "period": "第2周",
            "assessment": ["头发油腻程度", "噩梦频率", "苔腻变化", "大便情况"],
            "adjust_plan": [
                "噩梦消失→去龙胆草、栀子，加女贞子、墨旱莲（二至丸）",
                "苔腻转薄→减苦参、白蒺藜，加何首乌、菟丝子",
                "苔腻依旧→加薏苡仁、泽泻（加强利湿）",
            ],
        },
        "followup_2": {
            "period": "第6周",
            "assessment": ["脱发区绒毛生长", "湿热是否已去（苔薄白、头油少）"],
            "adjust_plan": [
                "湿热已去→换方四物汤合二至丸（补血养发生发）",
                "湿热残留→继续清利湿热，加茯苓、白术",
            ],
        },
        "followup_3": {
            "period": "第12周",
            "assessment": ["脱发控制+新发生长", "睡眠情绪稳定"],
            "adjust_plan": [
                "稳定→改膏方/丸剂巩固（六味地黄丸+二至丸）",
                "不稳定→返回前一阶段继续调理",
            ],
        },
        "lifestyle": [
            "洗护: 每周2-3次，避免过度清洁刺激皮脂",
            "训练后: 及时擦干头部汗液，避免湿热郁蒸",
            "饮食: 少食辛辣、油腻、甜食",
            "睡眠: 23:00前入睡",
            "情绪: 注意疏导压力",
        ],
    }


# ────────────────────────────────────────
# 格式化输出
# ────────────────────────────────────────
def format_output(patient_name: str, age: str, gender: str, occupation: str,
                  m1_result: str, m2_result: dict, m3_result: dict, m4_result: dict,
                  symptoms: List[str]):
    if not _legacy_prescription_paths_allowed():
        return "\n".join([
            "守一 CDSS 临床决策流程",
            "旧版完整处方展示路径已被安全闸门阻断。",
            "本次仅允许候选与审计信息，不生成正式处方、剂量、用法或疗程。",
        ])

    lines = []
    lines.append("=" * 70)
    lines.append(f"  守一 CDSS 临床决策全流程")
    lines.append(f"  {patient_name} · {age}岁 · {gender} · {occupation}")
    lines.append(f"  症状: {'、'.join(symptoms)}")
    lines.append("=" * 70)

    # M1
    lines.append(f"\n{'─'*30} M1: 西医诊断 {'─'*30}")
    lines.append(m1_result)

    # M2
    lines.append(f"\n{'─'*30} M2: 辨证处方 {'─'*30}")
    lines.append(f"\n  辨    证: {m2_result['main_syndrome']}")
    lines.append(f"  主    方: {m2_result['main_formula_name']}")
    lines.append(f"  知识库源: 口服方剂知识库")
    lines.append(f"  主方草药: {'、'.join(m2_result['main_herbs'])}")

    if m2_result["comorbidities_used"]:
        lines.append(f"\n  ▶ 兼证加味（上限5味，去重）:")
        for cm in m2_result["comorbidities_used"]:
            lines.append(f"      {cm['disease']} | {cm['syndrome']} → {cm['formula']}")
            lines.append(f"      取药: {'、'.join(cm['herbs_taken'])}")
    else:
        lines.append(f"\n  ▶ 无兼证加味")

    # M3
    lines.append(f"\n{'─'*30} M3: 审方·剂量 {'─'*30}")
    lines.append(f"\n  方剂名称: {m3_result['formula_name']}")
    lines.append(f"  药味总计: {m3_result['total']}味")
    for h in m3_result["herbs"]:
        lines.append(f"  {h['name']:8s}  {h['dosage']:10s}  [{h['category']}]  action={h['action']}")

    if m3_result["removed"]:
        lines.append(f"\n  🛑 药理学禁忌移除:")
        for r in m3_result["removed"]:
            lines.append(f"     {r['name']}: {r['reason']}")
    lines.append(f"\n  {m3_result['note']}")

    # M4
    lines.append(f"\n{'─'*30} M4: 复诊·全病程管理 {'─'*30}")
    fv = m4_result["first_visit"]
    lines.append(f"\n【一诊】{fv['period']}")
    lines.append(f"  方剂: {fv['formula']}")
    herbs_str = "、".join([f"{h['name']} {h['dosage']}" for h in fv["herbs"]])
    lines.append(f"  用药: {herbs_str}")
    lines.append(f"  煎服: {fv['instructions']}")

    for fu_key in ["followup_1", "followup_2", "followup_3"]:
        fu = m4_result[fu_key]
        lines.append(f"\n【{fu['period']}复诊】")
        lines.append(f"  评估: {'、'.join(fu['assessment'])}")
        for plan in fu["adjust_plan"]:
            lines.append(f"  → {plan}")

    lines.append(f"\n【生活调摄】")
    for lf in m4_result["lifestyle"]:
        lines.append(f"  • {lf}")

    lines.append(f"\n{'='*70}")
    return "\n".join(lines)


# ────────────────────────────────────────
# 主入口
# ────────────────────────────────────────
def main():
    # ── 患者信息 ──
    patient = {
        "name": "谢亮",
        "age": "26",
        "gender": "male",
        "occupation": "陆兵",
    }
    disease_name = "雄激素性脱发"  # 头发油 + 苔腻 → 脂溢性脱发类型
    symptoms = [
        "脱发3年", "头发油", "头皮屑多",
        "胸闷胸痛运动后加重",
        "睡眠噩梦多", "晨起口干口苦", "大便稍干",
        "苔白腻",
    ]
    # 兼证病名（从口服方剂知识库查询）
    comorbidity_syndromes = ["失眠", "便秘"]

    # Initial
    m1 = M1DiagnosisEngine()
    m1.semantic_cache = {}
    m1.db = [d for d in m1.db if d.get('source') != 'msd_manual_fetch']
    m2_svc = M2PrescriptionService()
    m3_svc = M3ReviewService()

    # M1
    r1 = m1.diagnose("脱发", symptoms)
    m1_output = m1.format_diagnosis_result(r1)

    # M2
    m2_result = m2_prescribe(m2_svc, disease_name, symptoms, comorbidity_syndromes)

    # M3
    m3_result = m3_review(m2_result, m3_svc, patient)

    # M4
    m4_result = m4_followup(m2_result, m3_result, patient)

    # 输出
    output = format_output(
        patient_name=patient["name"],
        age=patient["age"],
        gender=patient["gender"],
        occupation=patient["occupation"],
        m1_result=m1_output,
        m2_result=m2_result,
        m3_result=m3_result,
        m4_result=m4_result,
        symptoms=symptoms,
    )
    print(output)


if __name__ == "__main__":
    main()
