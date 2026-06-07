"""
M2 离线钉子病例临时验收脚本（只读模式，不修代码）
==================================================
运行：DEEPSEEK_API_KEY="" PYTHONPATH=. python3 tests/m2_nail_cases_temp.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH"] = "false"
os.environ["ALLOW_LEGACY_M2_FULL_PIPELINE"] = "false"
os.environ["ALLOW_LEGACY_DOSAGE_EXTRACTION"] = "false"

from m2_engine import M2SyndromeSelector

m2 = M2SyndromeSelector()

# ═══════════════════════════════════════════════════════════
#  10 个钉子病例定义
# ═══════════════════════════════════════════════════════════

NAIL_CASES = [
    {
        "id": 1,
        "label": "肺炎痰热",
        "primary_disease": "肺炎",
        "symptoms": ["发热", "咳嗽", "咳黄绿痰", "胸痛", "口干口苦"],
        "patient_info": {
            "tongue": "舌红苔黄腻",
            "pulse": "脉滑数",
            "cold_heat": ["发热"],
        },
        "expect": "痰热壅肺 / 麻杏石甘汤类",
        "expect_syndrome_include": "痰热",
        "expect_formula_include": "麻杏石甘汤",
        "must_not_be": "风热",
    },
    {
        "id": 2,
        "label": "肺炎风寒",
        "primary_disease": "肺炎",
        "symptoms": ["咳嗽", "痰白清稀", "怕冷", "鼻塞", "流清涕"],
        "patient_info": {
            "tongue": "舌淡苔薄白",
            "pulse": "脉浮紧",
            "cold_heat": ["恶寒"],
            "negative_findings": ["无明显口干", "无发热"],
        },
        "expect": "风寒 / 三拗汤类",
        "expect_syndrome_include": "风寒",
        "expect_formula_include": "三拗汤",
        "must_not_be": "风热",
    },
    {
        "id": 3,
        "label": "肺炎恢复期",
        "primary_disease": "肺炎",
        "symptoms": ["咳嗽少痰", "乏力", "气短", "自汗"],
        "patient_info": {
            "tongue": "舌淡",
            "pulse": "脉弱",
            "negative_findings": ["无发热", "无黄痰"],
        },
        "expect": "正虚邪恋或肺脾气虚，不得仍选痰热",
        "must_not_be": "痰热",
    },
    {
        "id": 4,
        "label": "急性胃肠炎湿热",
        "primary_disease": "急性胃肠炎",
        "symptoms": ["腹痛", "腹泻", "大便黏液", "发热", "口渴", "小便黄"],
        "patient_info": {
            "tongue": "舌红苔黄腻",
            "pulse": "脉滑数",
            "cold_heat": ["发热"],
            "stool_urine": ["腹泻"],
        },
        "expect": "湿热相关 / 白头翁汤类",
        "expect_syndrome_include": "湿",
        "must_not_be": "虚寒",
    },
    {
        "id": 5,
        "label": "急性胃肠炎虚寒",
        "primary_disease": "急性胃肠炎",
        "symptoms": ["久泻", "畏寒", "腹痛喜按", "完谷不化"],
        "patient_info": {
            "tongue": "舌淡",
            "pulse": "脉沉迟",
            "cold_heat": ["畏寒"],
            "negative_findings": ["无发热", "无口干"],
        },
        "expect": "虚寒/脾肾阳虚，不得选湿热痢",
        "must_not_be": "湿热",
    },
    {
        "id": 6,
        "label": "带状疱疹急性期",
        "primary_disease": "带状疱疹",
        "symptoms": ["灼热疼痛", "口苦", "烦躁"],
        "patient_info": {
            "tongue": "舌红苔黄",
            "pulse": "脉弦数",
            "cold_heat": ["发热"],
        },
        "expect": "肝经郁热 / 龙胆泻肝汤类",
        "expect_syndrome_include": "肝",
        "must_not_be": "寒",
    },
    {
        "id": 7,
        "label": "带状疱疹后遗痛",
        "primary_disease": "带状疱疹",
        "symptoms": ["皮疹已退", "刺痛", "夜间明显", "局部麻木"],
        "patient_info": {
            "tongue": "舌暗",
            "pulse": "脉细涩",
            "negative_findings": ["无发热", "无红肿"],
        },
        "expect": "气滞血瘀 / 活血通络方向",
        "must_not_be": "热毒",
    },
    {
        "id": 8,
        "label": "喉癌术后转移",
        "primary_disease": "喉癌",
        "symptoms": ["声音嘶哑", "吞咽困难", "颈部肿块", "消瘦", "纳差", "下肢水肿"],
        "patient_info": {
            "age": "57",
            "tongue": "舌暗红",
            "pulse": "脉细涩",
            "cold_heat": [],
            "stool_urine": [],
            "sleep": [],
            "appetite": ["纳差", "消瘦"],
        },
        "expect": "肿瘤转移消耗相关节点，必须标记需要进入 M3",
        "expect_needs_manual_review": True,
    },
    {
        "id": 9,
        "label": "寒热冲突病例",
        "primary_disease": "肺炎",
        "symptoms": ["怕冷", "痰白清稀", "口苦"],
        "patient_info": {
            "tongue": "舌淡苔黄腻",  # 典型寒热错杂舌象
            "pulse": "脉弦",
            "cold_heat": ["恶寒"],
        },
        "expect": "标记 need_human_review 或寒热错杂，不得高置信单一寒证/热证",
        "expect_need_human_review": True,
    },
    {
        "id": 10,
        "label": "否定症状病例",
        "primary_disease": "肺炎",
        "symptoms": ["咳嗽", "无痰", "无发热", "无口干"],
        "patient_info": {
            "tongue": "舌淡",
            "pulse": "脉缓",
            "cold_heat": [],
            "negative_findings": ["无痰", "无发热", "无口干"],
        },
        "expect": "不得强判痰热，不得把否定症状当阳性证据",
        "must_not_be": "痰热",
    },
]


# ═══════════════════════════════════════════════════════════
#  执行验收
# ═══════════════════════════════════════════════════════════

def check_syndrome_from_kb(m2_inst, disease_key: str, syndrome_name: str) -> dict:
    """Check if syndrome is from the correct disease KB pool"""
    syndromes = m2_inst._load_syndromes(disease_key)
    return {
        "in_disease_pool": syndrome_name in syndromes if syndromes else False,
        "disease_pool_size": len(syndromes) if syndromes else 0,
        "pool_keys": list(syndromes.keys())[:5] if syndromes else [],
    }


def run_nail_case(case):
    cid = case["id"]
    label = case["label"]
    disease = case["primary_disease"]
    symptoms = case["symptoms"]
    pi = case["patient_info"]

    m2_1 = m2.run_m2_1_syndrome_reasoning(
        primary_disease=disease,
        symptoms=symptoms,
        tongue=pi.get("tongue", ""),
        pulse=pi.get("pulse", ""),
        cold_heat=pi.get("cold_heat", []),
        stool_urine=pi.get("stool_urine", []),
        sleep=pi.get("sleep", []),
        appetite=pi.get("appetite", []),
        labs=pi.get("labs", []),
        age=pi.get("age", ""),
    )

    syndrome_key = m2_1.get("selected_syndrome_key", "")
    disease_key = m2_1.get("disease_key", "")
    syndrome_name = m2_1.get("syndrome_trace", {}).get("syndrome_name", "")
    confidence = m2_1.get("syndrome_trace", {}).get("confidence", 0)
    need_human = m2_1.get("need_human_review", False)
    needs_manual = m2_1.get("needs_manual_review", False)
    it = m2_1.get("internal_trace", {})

    # M2-2 拿方剂
    syndrome_trace = m2_1.get("syndrome_trace", {})
    m2_2 = m2.run_m2_2_formula_candidates(
        primary_disease=disease,
        syndrome_trace=syndrome_trace,
        symptoms=symptoms,
    )
    formula_names = [c.get("formula_name", "") for c in m2_2.get("formula_candidates", [])]

    # M2-3 去重
    first_herbs = m2_2.get("formula_candidates", [{}])[0].get("herbs", []) if m2_2.get("formula_candidates") else []
    m2_3 = m2.run_m2_3_modification_candidates(
        primary_disease=disease,
        formula_herbs=first_herbs,
        symptoms=symptoms,
        syndrome_name=syndrome_key,
    )
    mods = [m.get("herb_name", "") for m in m2_3.get("modification_candidates", [])[:3]]

    # 证型池验证
    pool_check = check_syndrome_from_kb(m2, disease_key, syndrome_key)

    # 预期匹配
    expect_ok = True
    errors = []

    if case.get("must_not_be"):
        if case["must_not_be"] in syndrome_name or case["must_not_be"] in syndrome_key:
            expect_ok = False
            errors.append(f"不应含'{case['must_not_be']}'，实际='{syndrome_name}'")

    if case.get("expect_syndrome_include"):
        if case["expect_syndrome_include"] not in syndrome_name and case["expect_syndrome_include"] not in syndrome_key:
            expect_ok = False
            errors.append(f"应含'{case['expect_syndrome_include']}'，实际='{syndrome_name}'")

    if case.get("expect_formula_include"):
        found_formula = any(case["expect_formula_include"] in fn for fn in formula_names)
        if not found_formula:
            expect_ok = False
            errors.append(f"方剂应含'{case['expect_formula_include']}'，实际={formula_names}")

    if case.get("expect_need_human_review"):
        if not need_human:
            expect_ok = False
            errors.append(f"应标记 need_human_review，实际=False")

    if case.get("expect_needs_manual_review"):
        if not needs_manual:
            expect_ok = False
            errors.append(f"应标记 needs_manual_review，实际=False")

    # 双轨状态
    sc = m2_1.get("syndrome_comparison", {})
    merge_status = sc.get("status", "N/A")

    # 症状证素库使用
    sf_used = it.get("symptom_factor_kb_used", False)
    sf_evidence = it.get("symptom_factor_evidence", {})
    contradiction = it.get("factor_contradiction_result", {})

    # 评分是否同分靠顺序
    scores = m2_1.get("syndrome_trace", {}).get("candidate_scores", [])
    tied_top = False
    if len(scores) >= 2:
        tied_top = abs(scores[0]["score"] - scores[1]["score"]) < 0.5 and scores[0]["score"] > 0

    # M2-3 reverse_audit
    reverse_audit = m2_3.get("reverse_audit", {}) or m2_3.get("reverse_audit")

    return {
        "case": f"病例{cid} {label}",
        "disease_key": disease_key,
        "syndrome_comparison": merge_status,
        "pathology_based_result": m2_1.get("pathology_based_result", {}).get("candidate_syndrome", "N/A") if m2_1.get("pathology_based_result") else "N/A",
        "traditional_tcm_result": m2_1.get("traditional_tcm_result", {}).get("candidate_syndrome", "N/A") if m2_1.get("traditional_tcm_result") else "N/A",
        "selected_syndrome": f"{syndrome_name} ({syndrome_key})",
        "confidence": confidence,
        "bound_formula": "、".join(formula_names) if formula_names else "无",
        "need_human_review": str(need_human),
        "needs_manual_review": str(needs_manual),
        "added_herbs_mods": "、".join(mods) if mods else "无",
        "reverse_audit": bool(reverse_audit),
        "in_disease_pool": str(pool_check["in_disease_pool"]),
        "pool_size": str(pool_check["disease_pool_size"]),
        "sf_kb_used": str(sf_used),
        "sf_evidence": json.dumps(sf_evidence, ensure_ascii=False),
        "contradiction_against": str(contradiction.get("strong_against", [])),
        "contradiction_support": str(contradiction.get("strong_support", [])),
        "contradiction_need_review": str(contradiction.get("need_human_review", False)),
        "tied_top_two": str(tied_top),
        "expect_ok": "✅" if expect_ok else "❌",
        "errors": "; ".join(errors),
    }


print("\n" + "=" * 120)
print("  M2 离线钉子病例验收")
print("=" * 120)

results = []
for case in NAIL_CASES:
    result = run_nail_case(case)
    results.append(result)

# 输出详细表格
summary_header = (
    f"{'case':<24} | {'disease_key':<18} | {'pathology':<14} | {'traditional':<18} | {'status':<10} | "
    f"{'selected_syndrome':<26} | {'conf':<6} | {'formula':<16} | {'human_rev':<6} | {'pool':<6} | {'SF':<5} | {'tied':<5} | {'expect':<6}"
)
print("\n" + summary_header)
print("-" * len(summary_header))

for r in results:
    line = (
        f"{r['case']:<24} | {r['disease_key']:<18} | {r['pathology_based_result']:<14} | {r['traditional_tcm_result']:<18} | {r['syndrome_comparison']:<10} | "
        f"{r['selected_syndrome']:<26} | {r['confidence']:<6} | {r['bound_formula']:<16} | {r['need_human_review']:<6} | {r['in_disease_pool']:<6} | {r['sf_kb_used']:<5} | {r['tied_top_two']:<5} | {r['expect_ok']:<6}"
    )
    print(line)

# 错误详情
print("\n\n" + "=" * 120)
print("  不合格病例详情")
print("=" * 120)
failed = [r for r in results if r["expect_ok"] == "❌"]
if failed:
    for r in failed:
        print(f"\n  ❌ {r['case']}: {r['errors']}")
        print(f"      selected_syndrome: {r['selected_syndrome']}")
        print(f"      formula: {r['bound_formula']}")
        print(f"      confidence: {r['confidence']}")
        print(f"      need_human_review: {r['need_human_review']}")
        print(f"      contradiction_against: {r['contradiction_against']}")
        print(f"      contradiction_support: {r['contradiction_support']}")
        print(f"      tied_top_two: {r['tied_top_two']}")
else:
    print("\n  全部通过 ✅")


# 重点标记汇总
print("\n\n" + "=" * 120)
print("  重点标记汇总")
print("=" * 120)
for r in results:
    print(f"\n  {r['case']}:")
    print(f"    - 是否跨病名选证型: {'否' if r['in_disease_pool'] == 'True' else '是（危险！'}（pool_size={r['pool_size']}）")
    print(f"    - 是否用了症状证素库: {r['sf_kb_used']}")
    print(f"    - 是否用了互斥/反证规则: {'是' if r['contradiction_against'] != '[]' or r['contradiction_support'] != '[]' else '否'}")
    print(f"    - 是否同分靠顺序选择: {r['tied_top_two']}")
    print(f"    - 方剂是否来自 selected_syndrome_node: {'是' if r['bound_formula'] != '无' else '无方剂'}")
    print(f"    - 是否误用否定症状: 见 sf_evidence={r['sf_evidence'][:80]}...")
    if r["expect_ok"] == "❌":
        print(f"    - ⚠️ 不符合预期: {r['errors']}")

print(f"\n\n{'='*60}")
print(f"  总病例: {len(results)}, 通过: {sum(1 for r in results if r['expect_ok'] == '✅')}, 未通过: {sum(1 for r in results if r['expect_ok'] == '❌')}")
print(f"{'='*60}")
