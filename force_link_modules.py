"""
==========================================================================
force_link_modules.py
M2 + M4 数据源强制挂接模块
版本: v1.0
功能:
  ① M2药理学库校对: 从 disease_pharmacology_cache.json 检索当前病名的靶点-药物关系
  ② M2名医病案参考: 从 m2_case_reference.json 检索前3条相似病案
  ③ M4疗程预后: 从 m4_disease_course_prognosis_window_master 自动读取 disease-specific 时间窗
==========================================================================
"""

import json
import os
from typing import List, Dict, Optional, Any
from functools import lru_cache

# ================================================================
# 数据源加载（带缓存，文件一旦读入后台常驻）
# ================================================================

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

@lru_cache(maxsize=1)
def _load_pharmacology() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "disease_pharmacology_cache.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

@lru_cache(maxsize=1)
def _load_pharmacology_mined() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "mined_pharmacology_temp.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f).get("results", {})
    return {}

@lru_cache(maxsize=1)
def _load_case_reference() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "m2_case_reference.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

@lru_cache(maxsize=1)
def _load_prognosis() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "m4_disease_course_prognosis_window_master_001_600_v12_merged.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

@lru_cache(maxsize=1)
def _load_m2_formula() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "m2_formula_knowledge.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

@lru_cache(maxsize=1)
def _load_m3_herb() -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "m3_herb_knowledge.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


# ================================================================
# 功能①: M2 药理学库校对
# ================================================================

def pharmacology_correction(
    disease_names: List[str],
    candidate_herbs: List[str]
) -> Dict[str, Any]:
    pharm_cache = _load_pharmacology()
    pharm_mined = _load_pharmacology_mined()

    found_disease = None
    for dname in disease_names:
        for pk in pharm_cache:
            if dname == pk or dname in pk:
                found_disease = pk
                break
        if found_disease:
            break

    if not found_disease:
        for pk in pharm_cache:
            for dname in disease_names:
                if dname in pk or pk in dname:
                    found_disease = pk
                    break
            if found_disease:
                break

    mined_found = None
    for dname in disease_names:
        if dname in pharm_mined:
            mined_found = dname
            break
        for mk in pharm_mined:
            if dname in mk or mk in dname:
                mined_found = mk
                break
        if mined_found:
            break

    key_targets = []
    evidence_herbs = []

    if found_disease:
        v = pharm_cache[found_disease]
        key_targets = v.get("symmap_targets", []) or v.get("key_targets", [])
        evidence_herbs = v.get("evidence_based_herbs", [])

    if mined_found:
        v = pharm_mined[mined_found]
        mined_targets = v.get("key_targets", [])
        mined_herbs = v.get("evidence_based_herbs", [])
        key_targets = list(set(key_targets + mined_targets))
        evidence_herbs = list(set(
            [h["herb_name"] for h in evidence_herbs] +
            [h.get("herb_name", "") for h in mined_herbs]
        ))

    matched = []
    unmatched = []

    for herb in candidate_herbs:
        herb_found = False
        for eh in evidence_herbs:
            if isinstance(eh, dict):
                if herb in eh.get("herb_name", ""):
                    herb_found = True
                    matched.append({
                        "herb": herb,
                        "mechanism": eh.get("target_mechanism", "")[:100],
                        "syndrome": eh.get("suitable_tcm_syndrome", ""),
                        "contraindication": eh.get("contraindications", "")
                    })
                    break
            elif isinstance(eh, str):
                if herb in eh:
                    herb_found = True
                    matched.append({"herb": herb, "note": "evidence_based"})
                    break
        if not herb_found:
            unmatched.append(herb)

    return {
        "pharm_disease_key": found_disease or mined_found,
        "key_targets": key_targets,
        "matched_herbs": matched,
        "unmatched_herbs": unmatched,
        "evidence_herbs_count": len(evidence_herbs),
        "warning": "药理学库中该病名数据为空" if (not key_targets and not evidence_herbs) else ""
    }


# ================================================================
# 功能②: M2 名医病案参考（取前3条）
# ================================================================

def top3_case_references(
    disease_names: List[str]
) -> List[Dict[str, Any]]:
    cases = _load_case_reference()
    if not cases:
        return []

    results = []

    for key, v in cases.items():
        parts = key.split("|")
        case_disease = parts[0].strip()

        for dn in disease_names:
            if dn == case_disease or dn in case_disease or case_disease in dn:
                match_type = "exact" if dn == case_disease else "fuzzy"
                results.append({
                    "disease_name": v.get("disease_name", ""),
                    "syndrome_name": v.get("syndrome_name", ""),
                    "formula_name": v.get("formula_name", ""),
                    "herbs": v.get("herbs", []),
                    "dosages": v.get("dosages", []),
                    "course": v.get("course", ""),
                    "efficacy": v.get("efficacy", ""),
                    "warning": v.get("warning", ""),
                    "source": v.get("reference_type", v.get("source", "")),
                    "match_key": key,
                    "match_type": match_type
                })
                break

    seen_keys = set()
    unique_results = []
    for r in results:
        if r["match_key"] not in seen_keys:
            seen_keys.add(r["match_key"])
            unique_results.append(r)

    unique_results.sort(key=lambda x: (
        0 if x["match_type"] == "exact" else 1,
        x.get("disease_name", "")
    ))

    return unique_results[:3]


# ================================================================
# 功能③: M4 疗程预后自动挂接
# ================================================================

PROGNOSIS_INDEX = None

def _build_prognosis_index():
    global PROGNOSIS_INDEX
    if PROGNOSIS_INDEX is not None:
        return PROGNOSIS_INDEX

    data = _load_prognosis()
    cards = data.get("course_prognosis_cards", [])

    index = {}
    alias_map = {}

    for card in cards:
        name = card.get("diseaseName_cn", "")
        index[name] = card
        aliases = card.get("aliases", [])
        for alias in aliases:
            alias_map[alias] = name

    PROGNOSIS_INDEX = {"index": index, "aliases": alias_map, "total": len(cards), "total_unique": len(index)}
    return PROGNOSIS_INDEX


def _find_disease_in_prognosis(disease_names: List[str]) -> Optional[str]:
    idx = _build_prognosis_index()
    index = idx["index"]
    aliases = idx["aliases"]

    for dn in disease_names:
        if dn in index:
            return dn
        if dn in aliases:
            return aliases[dn]

    for dn in disease_names:
        for idx_name in index:
            if dn in idx_name or idx_name in dn:
                return idx_name
    return None


def disease_prognosis(disease_names: List[str]) -> Dict[str, Any]:
    matched_key = _find_disease_in_prognosis(disease_names)
    if not matched_key:
        return _fallback_prognosis(disease_names)

    idx = _build_prognosis_index()
    card = idx["index"][matched_key]

    nc = card.get("natural_course", {})
    trw = card.get("treatment_response_window", {})
    cdr = card.get("course_deviation_reasons", {})
    routing = card.get("m4_routing_policy", {})

    return {
        "found": True,
        "disease_key": matched_key,
        "prognosis_name_cn": card.get("diseaseName_cn", matched_key),
        "course_category": card.get("course_category", ""),
        "treatment_response_window": {
            "expected_first_response_days": trw.get("expected_first_response_days", "未知"),
            "expected_main_symptom_response_days": trw.get("expected_main_symptom_response_days", "未知"),
            "expected_stable_response_days": trw.get("expected_stable_response_days", "未知"),
            "disease_type": trw.get("disease_type", "unknown")
        },
        "natural_course": {
            "onset_improvement_days": nc.get("typical_course", {}).get("onset_improvement_days", "未知"),
            "significant_improvement_days": nc.get("typical_course", {}).get("significant_improvement_days", "未知"),
            "expected_resolution_days": nc.get("typical_course", {}).get("expected_resolution_days", "未知"),
            "residual_symptoms": nc.get("typical_course", {}).get("residual_symptoms", [])
        },
        "course_deviation": {
            "still_acceptable_if": cdr.get("still_acceptable_if", []),
            "suggests_not_controlled": cdr.get("suggests_original_disease_not_controlled", []),
            "suggests_complication": cdr.get("suggests_complication_or_new_problem", []),
        },
        "m4_routing": {
            "return_to_m2_if": routing.get("return_to_m2_if", []),
            "return_to_m1_if": routing.get("return_to_m1_if", []),
            "emergency_stop_if": routing.get("emergency_stop_if", []),
            "do_not_overreact_if": routing.get("do_not_overreact_if", []),
        }
    }


def _fallback_prognosis(disease_names: List[str]) -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, "m4_followup_window_rules.json")
    if os.path.exists(path):
        with open(path) as f:
            generic_rules = json.load(f)

        fallback = {
            "found": False,
            "disease_key": disease_names[0] if disease_names else "unknown",
            "prognosis_name_cn": disease_names[0] if disease_names else "未知",
            "course_category": "fallback_to_generic",
            "treatment_response_window": {
                "expected_first_response_days": "通用规则（详情见fallback_type）",
                "expected_main_symptom_response_days": "通用规则（详情见fallback_type）",
                "expected_stable_response_days": "",
                "disease_type": "generic_fallback"
            },
            "fallback_type": "acute_infection",
            "fallback_note": ""
        }

        keyword_to_type = {
            "咳嗽": ("acute_infection", "急性感染"),
            "咽炎": ("acute_infection", "急性感染"),
            "支气管": ("respiratory", "呼吸系统"),
            "痛经": ("gynecology_cycle_related", "妇科周期相关"),
            "月经": ("gynecology_cycle_related", "妇科周期相关"),
            "腺肌": ("chronic_disease", "慢性病"),
            "子宫": ("chronic_disease", "慢性病"),
            "腰": ("pain_musculoskeletal", "肌肉骨骼"),
            "胃": ("gastroenterology", "消化系统"),
            "皮肤": ("skin_disease", "皮肤病"),
            "头痛": ("neurological", "神经系统"),
        }

        matched_type = None
        for dn in disease_names:
            for kw, (ftype, fdesc) in keyword_to_type.items():
                if kw in dn:
                    matched_type = ftype
                    fallback["fallback_type"] = matched_type
                    fallback["fallback_note"] = f"回退至通用规则: {fdesc}"
                    break
            if matched_type:
                break

        if matched_type and matched_type in generic_rules:
            gr = generic_rules[matched_type]
            fallback["treatment_response_window"]["expected_first_response_days"] = f"{gr['expected_onset_days']}天（通用）"
            fallback["treatment_response_window"]["expected_main_symptom_response_days"] = f"{gr['expected_significant_days']}天（通用）"
            fallback["treatment_response_window"]["disease_type"] = matched_type
            fallback["natural_course"] = {
                "onset_improvement_days": f"{gr['expected_onset_days']}天",
                "significant_improvement_days": f"{gr['expected_significant_days']}天",
                "expected_resolution_days": "通用规则无数据",
                "residual_symptoms": []
            }
        return fallback

    return {"found": False, "disease_key": disease_names[0] if disease_names else "unknown", "note": "无可用预后数据"}


# ================================================================
# 功能④: 一站式挂接
# ================================================================

def full_link(disease_names: List[str], candidate_herbs: Optional[List[str]] = None) -> Dict[str, Any]:
    result = {}

    fk = _load_m2_formula()
    pool = set()
    for dn in disease_names:
        for fk_name, fk_val in fk.items():
            if dn == fk_name or dn in fk_name or fk_name in dn:
                syndromes = fk_val.get("syndromes", {})
                for sname, sinfo in syndromes.items():
                    pool.update(sinfo.get("herbs", []))
    pool = sorted(pool)
    result["m2_disease_pool"] = pool

    herbs_for_pharm = candidate_herbs or pool

    result["pharmacology"] = pharmacology_correction(disease_names, herbs_for_pharm)
    result["top3_cases"] = top3_case_references(disease_names)
    result["prognosis"] = disease_prognosis(disease_names)

    hk = _load_m3_herb()
    herb_check = {}
    for herb in herbs_for_pharm:
        matched = any(
            herb == h.get("herb_name", "").split("(")[0].strip()
            for h in result["pharmacology"].get("matched_herbs", [])
        )
        herb_check[herb] = {
            "in_pharm_lib": matched,
            "in_case_lib": any(herb in case.get("herbs", []) for case in result.get("top3_cases", [])),
            "in_pool": herb in pool,
            "in_m3_lib": bool(hk.get(herb))
        }
    result["herb_pharm_check"] = herb_check

    return result


# ================================================================
# 测试入口
# ================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("数据源强制挂接模块 — 自检")
    print("=" * 80)

    print("\n\n【测试1】子宫腺肌症 + 痛经")
    r1 = full_link(["子宫腺肌症", "痛经", "月经过多"])
    print(f"  药理学: {'✅ 已挂接' if r1['pharmacology']['warning'] else '✅ 已挂接（数据为空）'}")
    print(f"  病案前3: {len(r1['top3_cases'])}条")
    print(f"  疗程预后: {'✅ 已挂接' if r1['prognosis']['found'] else '⬜ 回退通用'}")
    print(f"  药物池: {len(r1['m2_disease_pool'])}味")

    print("\n\n【测试2】急性支气管炎 + 咽炎 + 咳嗽")
    r2 = full_link(["急性支气管炎", "急性咽炎", "喉源性咳嗽"])
    print(f"  药理学: {'⬜ 数据为空' if r2['pharmacology']['warning'] else '✅ 已挂接'}")
    print(f"  病案前3: {len(r2['top3_cases'])}条")
    if r2['top3_cases']:
        for i, c in enumerate(r2['top3_cases']):
            print(f"    病案{i+1}: {c['disease_name']}|{c['syndrome_name']} -> {c['formula_name']}")
    print(f"  疗程预后: {'✅ 已挂接' if r2['prognosis']['found'] else '⬜ 回退通用'}")
    if r2['prognosis']['found']:
        tw = r2['prognosis']['treatment_response_window']
        print(f"    首次反应: {tw['expected_first_response_days']}")
        print(f"    主症反应: {tw['expected_main_symptom_response_days']}")
    else:
        print(f"    回退类型: {r2['prognosis'].get('fallback_type','')}")

    print("\n\n【测试3】流行性感冒（验证回退）")
    r3 = full_link(["流行性感冒"])
    print(f"  疗程预后: {'⬜ 回退通用' if not r3['prognosis']['found'] else '✅ 已挂接'}")
    tw3 = r3['prognosis']['treatment_response_window']
    print(f"    首次反应: {tw3['expected_first_response_days']}")
    print(f"    主症反应: {tw3['expected_main_symptom_response_days']}")

    print("\n\n" + "=" * 80)
    print("自检完成 — 模块已就绪")
    print("=" * 80)
