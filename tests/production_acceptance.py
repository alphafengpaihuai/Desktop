"""
M2-only 生产模式验收脚本
========================
默认只运行到 M2-3，跳过 M3.review（由独立 M3 验收脚本覆盖）。
输出 M1诊断、M2-1证型、M2-2方剂候选、M2-3加减候选、是否符合预期。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH"] = "false"
os.environ["ALLOW_LEGACY_M2_FULL_PIPELINE"] = "false"
os.environ["ALLOW_LEGACY_DOSAGE_EXTRACTION"] = "false"

from m2_engine import M2SyndromeSelector

m2 = M2SyndromeSelector()

FORBIDDEN = {"prescription_text", "final_formula", "complete_formula", "dosage", "dose", "用法", "疗程"}


def run_case(label, primary_disease, symptoms, patient_info, m1_diagnosis="",
             expected_syndrome="", expected_formula="", expected_mods=None):
    """M2-only 评估：M2-1→M2-2→M2-3，跳过 M3"""
    print(f"\n{'='*70}")
    print(f"  病例：{label}")
    print(f"{'='*70}")

    # M2-1 辨证
    m2_1 = m2.run_m2_1_syndrome_reasoning(
        primary_disease=primary_disease,
        symptoms=symptoms,
        tongue=patient_info.get("tongue", ""),
        pulse=patient_info.get("pulse", ""),
        cold_heat=patient_info.get("cold_heat", []),
        stool_urine=patient_info.get("stool_urine", []),
        sleep=patient_info.get("sleep", []),
        appetite=patient_info.get("appetite", []),
        age=patient_info.get("age", ""),
    )
    syndrome_name = m2_1.get("syndrome_trace", {}).get("syndrome_name", "")
    syndrome_key = m2_1.get("selected_syndrome_key", "")
    disease_type = m2_1.get("disease_type", "")
    framework = m2_1.get("differentiation_framework", "")
    print(f"  M1诊断: {m1_diagnosis}")
    print(f"  M2-1证型: {syndrome_name}")
    print(f"  M2-1 disease_type: {disease_type}")
    print(f"  M2-1 differentiation_framework: {framework}")
    # 双轨信息
    sc = m2_1.get("syndrome_comparison", {})
    if sc.get("status"):
        print(f"  M2-1 双轨状态: {sc['status']}")

    # M2-2 候选方
    m2_2 = m2.run_m2_2_formula_candidates(
        primary_disease=primary_disease,
        syndrome_trace=m2_1.get("syndrome_trace", {}),
        symptoms=symptoms,
    )
    formula_candidates = m2_2.get("formula_candidates", [])
    candidate_names = [c.get("formula_name", "") for c in formula_candidates]
    print(f"  M2-2候选方: {candidate_names}")
    print(f"  M2-2 candidate_only: {m2_2.get('candidate_only')}")
    print(f"  M2-2 must_enter_m3: {m2_2.get('must_enter_m3')}")

    # M2-3 加减候选
    first_herbs = formula_candidates[0].get("herbs", []) if formula_candidates else []
    m2_3 = m2.run_m2_3_modification_candidates(
        primary_disease=primary_disease,
        formula_herbs=first_herbs,
        symptoms=symptoms,
        syndrome_name=syndrome_key,
    )
    mods = m2_3.get("modification_candidates", [])
    mod_names = [m.get("herb_name", "") for m in mods[:4]]
    if not mod_names and m2_3.get("no_candidate"):
        mod_summary = "无候选 (no_candidate)"
    else:
        mod_summary = "、".join(mod_names) if mod_names else "无"
    print(f"  M2-3加减候选: {mod_summary}")

    # 检查 M2 各阶段禁止字段
    found_forbidden = []
    for d, dname in [(m2_1, "M2-1"), (m2_2, "M2-2"), (m2_3, "M2-3")]:
        for key in FORBIDDEN:
            if key in d:
                found_forbidden.append(f"{dname}.{key}")
    has_forbidden = len(found_forbidden) > 0

    fp_allowed = bool(
        m2_1.get("formal_prescription_allowed") or
        m2_2.get("formal_prescription_allowed") or
        m2_3.get("formal_prescription_allowed")
    )

    # 预期判断
    syndrome_match = (not expected_syndrome) or (expected_syndrome in syndrome_name)
    formula_match = (not expected_formula) or \
        any(expected_formula in n for n in candidate_names)
    mods_match = True
    if expected_mods:
        mods_match = any(em in "".join(mod_names) for em in expected_mods)

    case_pass = not has_forbidden and not fp_allowed and syndrome_match
    status = "✅ 通过" if case_pass else "❌ 不通过"

    result = {
        "病例": label,
        "M1诊断": m1_diagnosis,
        "M2-1证型": syndrome_name,
        "M2-1双轨状态": sc.get("status", ""),
        "M2-2候选方": "、".join(candidate_names) if candidate_names else "无",
        "M2-2候选合同": f"candidate_only={m2_2.get('candidate_only')}, "
                       f"must_enter_m3={m2_2.get('must_enter_m3')}",
        "M2-3加减候选": mod_summary,
        "禁止处方字段": "有" if has_forbidden else "无",
        "formal_prescription_allowed": str(fp_allowed),
        "证型符合预期": "✅" if syndrome_match else "❌",
        "方剂符合预期": "✅" if formula_match else "❌",
        "状态": status,
    }

    print(f"  禁止处方字段: {found_forbidden if has_forbidden else '无'}")
    print(f"  formal_prescription_allowed: {fp_allowed}")
    print(f"  证型符合预期: {'✅' if syndrome_match else '❌'}")
    print(f"  方剂符合预期: {'✅' if formula_match else '❌'}")
    print(f"  {status}")

    return result


results = []

# 病例1：肺炎痰热
# 预期：痰热壅肺方向 + 麻杏石甘汤类底方
r1 = run_case(
    label="肺炎痰热",
    primary_disease="肺炎",
    symptoms=["发热", "咳嗽", "咳黄绿痰", "胸痛", "口干口苦"],
    patient_info={
        "age": "45", "gender": "男",
        "tongue": "舌红苔黄腻",
        "pulse": "脉滑数",
        "cold_heat": ["发热"],
        "stool_urine": [],
        "sleep": [],
        "appetite": [],
    },
    m1_diagnosis="肺炎",
    expected_syndrome="痰热",
    expected_formula="麻杏石甘汤",
)
results.append(r1)

# 病例2：急性胃肠炎湿热
r2 = run_case(
    label="急性胃肠炎湿热",
    primary_disease="急性胃肠炎",
    symptoms=["腹泻", "腹痛", "恶心", "口干口苦", "大便黏滞不爽"],
    patient_info={
        "age": "32", "gender": "女",
        "tongue": "舌红苔黄腻",
        "pulse": "脉濡数",
        "cold_heat": ["发热"],
        "stool_urine": ["腹泻"],
        "sleep": [],
        "appetite": ["纳差"],
    },
    m1_diagnosis="急性胃肠炎",
    expected_syndrome="湿热",
)
results.append(r2)

# 病例3：喉癌（高风险验证）
r3 = run_case(
    label="喉癌高风险",
    primary_disease="喉癌",
    symptoms=["声音嘶哑", "吞咽困难", "颈部肿块"],
    patient_info={
        "age": "57", "gender": "男", "weight": "",
        "tongue": "舌暗红",
        "pulse": "脉细涩",
        "cold_heat": [],
        "stool_urine": [],
        "sleep": [],
        "appetite": ["纳差", "消瘦"],
        "symptom_text": "2024年11月做了喉癌手术，2025年6月发现肝转移、骨转移。近二天下肢水肿，食纳差，消瘦",
    },
    m1_diagnosis="喉癌",
    expected_syndrome="",
)
results.append(r3)

# 输出验收表格
print(f"\n\n{'='*70}")
print(f"  生产模式验收汇总表（M2-only）")
print(f"{'='*70}")

header = (
    f"{'病例':<16} | {'M1诊断':<10} | {'M2-1证型':<18} | {'双轨状态':<12} "
    f"| {'M2-2候选方':<22} | {'M2-3加减':<30} | {'证型匹配':<8} | {'方剂匹配':<8} | {'状态':<8}"
)
sep = "-" * len(header)
print(header)
print(sep)
for r in results:
    line = (
        f"{r['病例']:<16} | {r['M1诊断']:<10} | {r['M2-1证型']:<18} | {r['M2-1双轨状态']:<12} "
        f"| {r['M2-2候选方']:<22} | {r['M2-3加减候选']:<30} "
        f"| {r['证型符合预期']:<8} | {r['方剂符合预期']:<8} | {r['状态']:<8}"
    )
    print(line)
