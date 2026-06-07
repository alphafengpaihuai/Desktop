#!/usr/bin/env python3
"""
[DEPRECATED] 守一 CDSS — M1→M2→M3→M4 全流程脚本 (备份)

全量备份，不做任何修改。
"""

import os, json, re, sys
import pandas as pd
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

os.environ['DEEPSEEK_API_KEY'] = 'sk-09562b1e562d480dacb33a1fe1fd8642'
os.environ['GEMINI_API_KEY'] = 'AIzaSyDhHln47rXY_jqCgQbd8lZRf5HjdMfrj9o'

from m1_engine import M1DiagnosisEngine
from services.m2_prescription_service import M2PrescriptionService, HerbCandidate
from services.m3_review_service import M3ReviewService


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
