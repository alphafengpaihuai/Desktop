from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
CORE_CANDIDATES = [DATA_DIR / "diseases_core.json", BASE_DIR / "diseases_core.json"]
ALIAS_INPUT_CANDIDATES = [DATA_DIR / "m1_disease_alias_map.json", DATA_DIR / "m1_alias_map.json"]

ALIAS_PATCH_PATH = DATA_DIR / "m1_alias_map_patch.json"
CONTENT_PATCH_PATH = DATA_DIR / "m1_disease_content_patch.json"
ALIAS_AUDIT_PATH = REPORT_DIR / "m1_alias_audit_report.json"
TEMPLATE_AUDIT_PATH = REPORT_DIR / "m1_template_like_content_audit.json"
QUALITY_SCORE_PATH = REPORT_DIR / "m1_801_quality_score_report.json"

FORBIDDEN_TCM_TERMS = [
    "证型", "病机", "治则", "方剂", "中药", "君臣佐使", "辨证",
    "寒热虚实", "脏腑辨证", "调理", "肝郁", "脾虚", "湿热", "气滞", "血瘀", "痰湿",
]

TEMPLATE_TERMS = [
    "主要系统症状", "病程变化", "功能受限", "红旗表现", "全身状态改变",
    "全身症状", "疼痛或不适", "复发或进展", "受累系统查体", "严重程度评估",
    "并发症体征", "红旗体征筛查", "专科查体按需", "病因相关专项检查",
    "危急值筛查按需", "受累系统影像", "超声/CT/MRI按病种", "内镜或功能检查按需",
    "症状-体征-检查一致", "需要进一步复核", "核心临床模式",
]

GENERIC_DIFFERENTIALS = [
    "感染性疾病", "炎症或自身免疫病", "肿瘤性疾病", "代谢或药物因素",
    "功能性疾病", "急危重症鉴别",
]

CURATED_ALIASES = {
    "Atrial Fibrillation": {
        "zh_standard": ["心房颤动"],
        "zh_common": ["房颤"],
        "zh_clinical_short": ["房颤"],
        "en_abbreviation": ["AF", "Afib"],
    },
    "Acute Coronary Syndrome": {
        "zh_standard": ["急性冠脉综合征", "急性冠状动脉综合征"],
        "zh_common": ["急性冠脉事件"],
        "zh_clinical_short": ["ACS"],
        "en_abbreviation": ["ACS"],
    },
    "Acute Myocardial Infarction": {
        "zh_standard": ["急性心肌梗死"],
        "zh_common": ["心梗"],
        "zh_clinical_short": ["AMI"],
        "en_abbreviation": ["AMI", "MI"],
    },
    "Chronic Obstructive Pulmonary Disease": {
        "zh_standard": ["慢性阻塞性肺疾病"],
        "zh_common": ["慢阻肺"],
        "zh_clinical_short": ["COPD"],
        "en_abbreviation": ["COPD"],
    },
    "Gastroesophageal Reflux Disease": {
        "zh_standard": ["胃食管反流病"],
        "zh_common": ["胃食管反流", "反酸烧心"],
        "zh_clinical_short": ["GERD"],
        "en_abbreviation": ["GERD"],
    },
    "Pulmonary Embolism": {
        "zh_standard": ["肺栓塞"],
        "zh_common": ["肺动脉栓塞"],
        "zh_clinical_short": ["PE"],
        "en_abbreviation": ["PE"],
    },
    "Transient Ischemic Attack": {
        "zh_standard": ["短暂性脑缺血发作"],
        "zh_common": ["小中风"],
        "zh_clinical_short": ["TIA"],
        "en_abbreviation": ["TIA"],
    },
    "Chronic Kidney Disease": {
        "zh_standard": ["慢性肾脏病"],
        "zh_common": ["慢性肾病"],
        "zh_clinical_short": ["CKD"],
        "en_abbreviation": ["CKD"],
    },
    "Acute Kidney Injury": {
        "zh_standard": ["急性肾损伤"],
        "zh_common": ["急性肾衰"],
        "zh_clinical_short": ["AKI"],
        "en_abbreviation": ["AKI"],
    },
    "Inflammatory Bowel Disease": {
        "zh_standard": ["炎症性肠病"],
        "zh_common": [],
        "zh_clinical_short": ["IBD"],
        "en_abbreviation": ["IBD"],
    },
    "Ulcerative Colitis": {
        "zh_standard": ["溃疡性结肠炎"],
        "zh_common": [],
        "zh_clinical_short": ["UC"],
        "en_abbreviation": ["UC"],
    },
    "Crohn Disease": {
        "zh_standard": ["克罗恩病"],
        "zh_common": [],
        "zh_clinical_short": ["CD"],
        "en_abbreviation": ["CD"],
    },
    "Systemic Lupus Erythematosus": {
        "zh_standard": ["系统性红斑狼疮"],
        "zh_common": ["狼疮"],
        "zh_clinical_short": ["SLE"],
        "en_abbreviation": ["SLE"],
    },
    "Rheumatoid Arthritis": {
        "zh_standard": ["类风湿关节炎"],
        "zh_common": ["类风湿"],
        "zh_clinical_short": ["RA"],
        "en_abbreviation": ["RA"],
    },
    "Diabetic Ketoacidosis": {
        "zh_standard": ["糖尿病酮症酸中毒"],
        "zh_common": [],
        "zh_clinical_short": ["DKA"],
        "en_abbreviation": ["DKA"],
    },
    "Hyperosmolar Hyperglycemic State": {
        "zh_standard": ["高渗高血糖状态"],
        "zh_common": ["高渗状态"],
        "zh_clinical_short": ["HHS"],
        "en_abbreviation": ["HHS"],
    },
    "Guillain-Barre Syndrome": {
        "zh_standard": ["吉兰-巴雷综合征"],
        "zh_common": ["格林巴利综合征"],
        "zh_clinical_short": ["GBS"],
        "en_abbreviation": ["GBS"],
    },
    "Subarachnoid Hemorrhage": {
        "zh_standard": ["蛛网膜下腔出血"],
        "zh_common": [],
        "zh_clinical_short": ["SAH"],
        "en_abbreviation": ["SAH"],
    },
    "Disseminated Intravascular Coagulation": {
        "zh_standard": ["弥散性血管内凝血"],
        "zh_common": [],
        "zh_clinical_short": ["DIC"],
        "en_abbreviation": ["DIC"],
    },
    "Thrombotic Thrombocytopenic Purpura": {
        "zh_standard": ["血栓性血小板减少性紫癜"],
        "zh_common": [],
        "zh_clinical_short": ["TTP"],
        "en_abbreviation": ["TTP"],
    },
    "Hemolytic Uremic Syndrome": {
        "zh_standard": ["溶血尿毒综合征"],
        "zh_common": [],
        "zh_clinical_short": ["HUS"],
        "en_abbreviation": ["HUS"],
    },
}

AMBIGUOUS_ALIASES = [
    {
        "alias": "心梗",
        "ambiguous": True,
        "candidate_names": ["Acute Myocardial Infarction", "Acute Coronary Syndrome"],
        "resolution_rule": "需要结合胸痛特征、心电图、肌钙蛋白动态变化和影像/冠脉证据，别名只用于召回。"
    },
    {
        "alias": "中风",
        "ambiguous": True,
        "candidate_names": ["Stroke", "Transient Ischemic Attack", "Intracranial Hemorrhage"],
        "resolution_rule": "需要结合起病方式、神经定位体征和影像检查，别名只用于召回。"
    },
    {
        "alias": "胃炎",
        "ambiguous": True,
        "candidate_names": ["Acute Gastritis", "Chronic Gastritis"],
        "resolution_rule": "需要结合病程、上腹痛节律、内镜和幽门螺杆菌等证据，别名只用于召回。"
    },
    {
        "alias": "肠炎",
        "ambiguous": True,
        "candidate_names": ["Gastroenteritis", "Inflammatory Bowel Disease", "Ulcerative Colitis", "Crohn Disease"],
        "resolution_rule": "需要结合急慢性病程、发热、血便、粪便检查、内镜和炎症指标，别名只用于召回。"
    },
]


def main() -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    core_path = next((p for p in CORE_CANDIDATES if p.exists()), None)
    if core_path is None:
        raise FileNotFoundError("diseases_core.json not found")
    diseases = json.loads(core_path.read_text(encoding="utf-8"))
    disease_names = {item["disease_name"] for item in diseases}
    allowed_axes = {axis["axis"] for item in diseases for axis in item.get("axes", []) if axis.get("axis")}

    alias_patch = build_alias_patch(diseases, disease_names)
    alias_audit = audit_alias_patch(alias_patch, disease_names)
    template_audit = audit_template_like_content(diseases)
    content_patch = build_content_patch(diseases, template_audit, allowed_axes)
    quality_report = build_quality_report(diseases, alias_patch, template_audit)

    write_json(ALIAS_PATCH_PATH, alias_patch)
    write_json(ALIAS_AUDIT_PATH, alias_audit)
    write_json(TEMPLATE_AUDIT_PATH, template_audit)
    write_json(CONTENT_PATCH_PATH, content_patch)
    write_json(QUALITY_SCORE_PATH, quality_report)

    result = {
        "ok": True,
        "core_path": str(core_path),
        "alias_patch_count": len(alias_patch),
        "ambiguous_alias_count": sum(len(x.get("ambiguous_aliases", [])) for x in alias_patch),
        "not_in_m1_count": alias_audit["not_in_m1_count"],
        "template_like_problem_count": len(template_audit),
        "high_severity_count": sum(1 for x in template_audit if x["severity"] == "high"),
        "medium_severity_count": sum(1 for x in template_audit if x["severity"] == "medium"),
        "quality_grade_counts": dict(Counter(x["quality_grade"] for x in quality_report)),
        "outputs": [
            str(ALIAS_PATCH_PATH),
            str(ALIAS_AUDIT_PATH),
            str(TEMPLATE_AUDIT_PATH),
            str(CONTENT_PATCH_PATH),
            str(QUALITY_SCORE_PATH),
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def build_alias_patch(diseases: list[dict], disease_names: set[str]) -> list[dict]:
    patch = []
    for item in diseases:
        name = item["disease_name"]
        cn = clean_text(item.get("diseaseName_cn", ""))
        curated = CURATED_ALIASES.get(name, {})
        aliases = {
            "zh_standard": dedupe([cn] + curated.get("zh_standard", [])),
            "zh_common": dedupe(curated.get("zh_common", [])),
            "zh_clinical_short": dedupe(curated.get("zh_clinical_short", [])),
            "en_standard": dedupe([name] + curated.get("en_standard", [])),
            "en_abbreviation": dedupe(curated.get("en_abbreviation", [])),
            "possible_misspellings": dedupe(curated.get("possible_misspellings", [])),
        }
        entry = {
            "canonical_disease_name": name,
            "diseaseName_cn": cn,
            "mapping_status": "in_m1",
            "aliases": aliases,
            "match_priority": 0.95 if name in CURATED_ALIASES else 0.75,
            "diagnostic_use_policy": "alias_only_for_recall_not_for_diagnosis",
            "notes": "别名只用于召回和归一，不直接提高诊断置信度。",
            "source_quality": "A",
        }
        ambiguous = []
        for row in AMBIGUOUS_ALIASES:
            candidates = [
                {"disease_name": candidate, "mapping_status": "in_m1" if candidate in disease_names else "not_in_m1"}
                for candidate in row["candidate_names"]
            ]
            if name in row["candidate_names"]:
                ambiguous.append({
                    "alias": row["alias"],
                    "ambiguous": True,
                    "candidate_diseases": candidates,
                    "resolution_rule": row["resolution_rule"],
                })
        if ambiguous:
            entry["ambiguous_aliases"] = ambiguous
        patch.append(entry)
    return patch


def audit_alias_patch(alias_patch: list[dict], disease_names: set[str]) -> dict:
    alias_to_entries = defaultdict(list)
    not_in_m1 = 0
    errors = []
    ambiguous_count = 0
    for entry in alias_patch:
        if entry["canonical_disease_name"] not in disease_names:
            errors.append(f"canonical not in M1: {entry['canonical_disease_name']}")
        for bucket, values in entry["aliases"].items():
            for alias in values:
                alias_to_entries[norm(alias)].append(entry["canonical_disease_name"])
        for row in entry.get("ambiguous_aliases", []):
            ambiguous_count += 1
            for candidate in row.get("candidate_diseases", []):
                if candidate.get("mapping_status") == "not_in_m1":
                    not_in_m1 += 1
    duplicates = {
        alias: sorted(set(names))
        for alias, names in alias_to_entries.items()
        if alias and len(set(names)) > 1
    }
    return {
        "ok": not errors,
        "errors": errors,
        "duplicate_aliases": duplicates,
        "ambiguous_alias_count": ambiguous_count,
        "not_in_m1_count": not_in_m1,
        "alias_entry_count": len(alias_patch),
        "forbidden_terms_found": find_forbidden_terms(alias_patch),
    }


def audit_template_like_content(diseases: list[dict]) -> list[dict]:
    problems = []
    for item in diseases:
        problem_fields = []
        examples = []
        score_hits = 0
        for field in ["typical_symptoms", "physical_exam", "lab_tests", "imaging", "differential_diagnosis"]:
            values = as_list(item.get(field))
            hits = [v for v in values if is_template_text(v)]
            if hits:
                problem_fields.append(field)
                examples.extend(hits[:3])
                score_hits += len(hits)
        axis_hits = []
        for axis in item.get("axes", []):
            for finding in axis.get("typical_findings", []):
                if is_template_text(finding):
                    axis_hits.append(finding)
        if axis_hits:
            problem_fields.append("axes.typical_findings")
            examples.extend(axis_hits[:3])
            score_hits += len(axis_hits)
        if generic_differential_ratio(item.get("differential_diagnosis", [])) >= 0.35:
            problem_fields.append("differential_diagnosis")
            examples.extend([x for x in item.get("differential_diagnosis", []) if x in GENERIC_DIFFERENTIALS][:3])
            score_hits += 3
        if source_summary_weak(item):
            problem_fields.append("source_verified_medical_summary")
            examples.append("summary lacks enough disease-specific keywords")
            score_hits += 2
        if problem_fields:
            severity = "high" if score_hits >= 8 else ("medium" if score_hits >= 3 else "low")
            ptype = "template_like" if any(ex in TEMPLATE_TERMS for ex in examples) else "too_generic"
            if "axes.typical_findings" in problem_fields:
                ptype = "weak_axes"
            problems.append({
                "disease_name": item.get("disease_name", ""),
                "diseaseName_cn": item.get("diseaseName_cn", ""),
                "problem_fields": sorted(set(problem_fields)),
                "problem_type": ptype,
                "severity": severity,
                "examples": dedupe([str(x) for x in examples])[:8],
                "recommended_fix": "生成人工复核补丁，替换模板项为疾病特异症状、体征、检查和具体鉴别疾病。",
                "needs_authoritative_lookup": severity in {"high", "medium"},
            })
    return problems


def build_content_patch(diseases: list[dict], template_audit: list[dict], allowed_axes: set[str]) -> list[dict]:
    problem_names = {p["disease_name"]: p for p in template_audit if p["severity"] in {"high", "medium"}}
    patches = []
    for item in diseases:
        if item["disease_name"] not in problem_names:
            continue
        cleaned_symptoms = clean_list(item.get("typical_symptoms", []))
        cleaned_exam = clean_list(item.get("physical_exam", []))
        cleaned_labs = clean_list(item.get("lab_tests", []))
        cleaned_imaging = clean_list(item.get("imaging", []))
        cleaned_diff = [x for x in clean_list(item.get("differential_diagnosis", [])) if x not in GENERIC_DIFFERENTIALS]
        axes_patch = []
        for axis in item.get("axes", []):
            axis_name = axis.get("axis")
            if axis_name not in allowed_axes:
                continue
            findings = clean_list(axis.get("typical_findings", []))
            if findings:
                axes_patch.append({
                    "axis": axis_name,
                    "role": axis.get("role", "secondary"),
                    "weight": axis.get("weight", 0.0),
                    "typical_findings": findings[:8],
                })
        patches.append({
            "disease_name": item.get("disease_name", ""),
            "diseaseName_cn": item.get("diseaseName_cn", ""),
            "patch_fields": {
                "typical_symptoms": ensure_minimum(cleaned_symptoms, item, "symptom")[:10],
                "physical_exam": ensure_minimum(cleaned_exam, item, "exam")[:8],
                "lab_tests": ensure_minimum(cleaned_labs, item, "lab")[:8],
                "imaging": ensure_minimum(cleaned_imaging, item, "imaging")[:6],
                "differential_diagnosis": ensure_minimum(cleaned_diff, item, "differential")[:8],
                "axes_patch": axes_patch[:5],
                "source_verified_medical_summary_patch": {
                    "key_symptom_pattern": summarize_values(cleaned_symptoms),
                    "key_exam_findings": summarize_values(cleaned_exam),
                    "key_tests_or_imaging": summarize_values(cleaned_labs + cleaned_imaging),
                    "differential_focus": summarize_values(cleaned_diff),
                    "pathophysiology_focus": summarize_values(clean_list(axis_finding_values(item))),
                },
            },
            "source_quality": "A",
            "source_basis": ["existing diseases_core structured fields", "template-removal heuristic audit"],
            "merge_policy": "manual_review_required",
            "needs_manual_review": True,
        })
    return patches


def build_quality_report(diseases: list[dict], alias_patch: list[dict], template_audit: list[dict]) -> list[dict]:
    alias_by_name = {x["canonical_disease_name"]: x for x in alias_patch}
    problem_by_name = {x["disease_name"]: x for x in template_audit}
    report = []
    for item in diseases:
        name = item["disease_name"]
        alias = alias_by_name.get(name, {})
        alias_count = sum(len(v) for v in alias.get("aliases", {}).values())
        alias_score = min(100, 40 + alias_count * 12 + (15 if alias.get("ambiguous_aliases") else 0))
        symptom_score = specificity_score(item.get("typical_symptoms", []), min_items=4)
        axis_score = axis_specificity_score(item.get("axes", []))
        diff_score = differential_score(item.get("differential_diagnosis", []))
        lab_img_score = round((specificity_score(item.get("lab_tests", []), 2) + specificity_score(item.get("imaging", []), 1)) / 2)
        penalty = {"high": 25, "medium": 12, "low": 5}.get(problem_by_name.get(name, {}).get("severity", ""), 0)
        overall = max(0, round((alias_score * 0.15 + symptom_score * 0.25 + axis_score * 0.2 + diff_score * 0.2 + lab_img_score * 0.2) - penalty))
        grade = "A" if overall >= 85 else ("B" if overall >= 70 else ("C" if overall >= 50 else "D"))
        weaknesses = []
        if alias_score < 70:
            weaknesses.append("alias_coverage")
        if symptom_score < 70:
            weaknesses.append("symptom_specificity")
        if axis_score < 70:
            weaknesses.append("axis_specificity")
        if diff_score < 70:
            weaknesses.append("differential_quality")
        if lab_img_score < 70:
            weaknesses.append("lab_imaging_quality")
        if name in problem_by_name:
            weaknesses.append(problem_by_name[name]["problem_type"])
        action = "no_action"
        if grade == "B":
            action = "alias_only" if weaknesses == ["alias_coverage"] else "content_patch"
        elif grade == "C":
            action = "content_patch"
        elif grade == "D":
            action = "manual_review"
        report.append({
            "disease_name": name,
            "diseaseName_cn": item.get("diseaseName_cn", ""),
            "alias_coverage_score": alias_score,
            "symptom_specificity_score": symptom_score,
            "axis_specificity_score": axis_score,
            "differential_quality_score": diff_score,
            "lab_imaging_quality_score": lab_img_score,
            "overall_score": overall,
            "quality_grade": grade,
            "main_weakness": weaknesses,
            "recommended_action": action,
        })
    return report


def specificity_score(values: Any, min_items: int = 3) -> int:
    vals = as_list(values)
    if not vals:
        return 20
    clean = [v for v in vals if not is_template_text(v)]
    generic = len(vals) - len(clean)
    base = min(100, 35 + len(clean) * 12)
    return max(0, min(100, base - generic * 8))


def axis_specificity_score(axes: list[dict]) -> int:
    if not axes:
        return 20
    findings = [f for axis in axes for f in axis.get("typical_findings", [])]
    return specificity_score(findings, min_items=4)


def differential_score(values: Any) -> int:
    vals = as_list(values)
    if not vals:
        return 20
    generic = sum(1 for v in vals if v in GENERIC_DIFFERENTIALS or is_template_text(v))
    concrete = len(vals) - generic
    return max(0, min(100, 35 + concrete * 12 - generic * 10))


def source_summary_weak(item: dict) -> bool:
    text = str(item.get("source_verified_medical_summary", ""))
    if not text:
        return False
    cn = str(item.get("diseaseName_cn", ""))
    en = str(item.get("disease_name", ""))
    tokens = [x for x in [cn, en] if x]
    return len(text) > 120 and not any(t in text for t in tokens)


def is_template_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return text in TEMPLATE_TERMS or any(term == text for term in TEMPLATE_TERMS)


def generic_differential_ratio(values: Any) -> float:
    vals = as_list(values)
    if not vals:
        return 1.0
    return sum(1 for v in vals if v in GENERIC_DIFFERENTIALS) / len(vals)


def clean_list(values: Any) -> list[str]:
    return [clean_text(v) for v in as_list(values) if clean_text(v) and not is_template_text(v)]


def axis_finding_values(item: dict) -> list[str]:
    return [f for axis in item.get("axes", []) for f in axis.get("typical_findings", [])]


def ensure_minimum(values: list[str], item: dict, kind: str) -> list[str]:
    if values:
        return dedupe(values)
    axes = clean_list(axis_finding_values(item))
    if kind == "symptom" and axes:
        return dedupe(axes[:6])
    if kind == "exam":
        return ["focused physical examination", "vital signs assessment"]
    if kind == "lab":
        return ["disease-directed laboratory tests"]
    if kind == "imaging":
        return ["disease-directed imaging when clinically indicated"]
    if kind == "differential":
        return ["specific differential diagnosis requires manual review"]
    return []


def summarize_values(values: list[str]) -> str:
    return "；".join(dedupe(values)[:5]) if values else "needs manual review"


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if value in (None, ""):
        return []
    return [str(value)]


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    for term in FORBIDDEN_TCM_TERMS:
        text = text.replace(term, "")
    return text


def dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        v = clean_text(value)
        key = norm(v)
        if v and key not in seen:
            out.append(v)
            seen.add(key)
    return out


def norm(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def find_forbidden_terms(obj: Any) -> list[str]:
    blob = json.dumps(obj, ensure_ascii=False)
    return [term for term in FORBIDDEN_TCM_TERMS if term in blob]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    forbidden = [term for term in FORBIDDEN_TCM_TERMS if term in text]
    if forbidden:
        raise ValueError(f"{path} would contain forbidden terms: {forbidden}")
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
