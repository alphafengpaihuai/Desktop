"""M1 v8 helpers — stage inference, KB judgment, LLM reference, status mapping."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


V8_DIAGNOSIS_STATUSES = (
    "CONFIRMED",
    "PROBABLE",
    "LOW_CONFIDENCE",
    "REQUEST_MORE_INFO",
    "NO_CANDIDATE",
)

LEGACY_STATUS_MAP = {
    "CONFIRMED": "PASS",
    "PROBABLE": "LOW_CONFIDENCE",
    "LOW_CONFIDENCE": "LOW_CONFIDENCE",
    "REQUEST_MORE_INFO": "REQUEST_MORE_INFO",
    "NO_CANDIDATE": "NEED_EXTERNAL_SEARCH",
}

HIGH_UPDATE_DISEASE_KEYWORDS = ("癌", "肿瘤", "感染", "新冠", "流感", "支原体", "耐药")
STABLE_DISEASE_HIGH_UPDATE_YEARS = 3
STABLE_DISEASE_NORMAL_YEARS = 5


def legacy_diagnosis_status(v8_status: str) -> str:
    return LEGACY_STATUS_MAP.get(v8_status, v8_status)


def clinical_text_blob(ni) -> str:
    parts = [
        getattr(ni, "chief_complaint", "") or "",
        getattr(ni, "patient_mentioned_disease", "") or "",
        getattr(ni, "known_diagnosis", "") or "",
        " ".join(getattr(ni, "symptoms", []) or []),
        " ".join(getattr(ni, "signs", []) or []),
        " ".join(getattr(ni, "labs", []) or []),
        " ".join(getattr(ni, "imaging", []) or []),
        " ".join(getattr(ni, "negative_findings", []) or []),
        getattr(ni, "duration", "") or "",
        getattr(ni, "onset", "") or "",
        getattr(ni, "severity", "") or "",
    ]
    return " ".join(p for p in parts if p).lower()


def resolve_disease_key(primary: str, resolver=None) -> str:
    primary = (primary or "").strip()
    if not primary:
        return ""
    if resolver:
        try:
            resolved = resolver(primary)
            if resolved:
                return resolved
        except Exception:
            pass
    return primary.split(" (")[0].split("（")[0].strip()


def infer_stage(primary: str, ni, entry: Optional[dict] = None) -> Tuple[str, str, str]:
    """Return (stage, stage_confidence, stage_reason)."""
    text = clinical_text_blob(ni)
    cn = (primary or "").split(" (")[0].split("（")[0].strip()

    if "支气管炎" in cn:
        if any(k in text for k in ("一周", "1周", "数天", "3天", "5天", "7天", "急性", "夜间")):
            return "急性发作期", "MEDIUM", "病程≤2周且以急性咳嗽/夜间加重为主"
        return "急性/亚急性早期", "LOW", "下气道急性受累，分期依据有限"

    if "肺炎" in cn:
        inflammatory = any(
            k in text
            for k in ("发热", "黄痰", "黄绿", "绿痰", "crp", "降钙素原", "白细胞", "感染", "斑片", "浸润")
        )
        if inflammatory:
            return "急性炎症期", "HIGH", "发热/脓痰/炎症指标或影像支持急性炎症"
        return "急性炎症期", "MEDIUM", "肺炎诊断成立，急性炎症分期证据部分缺失"

    # 高血压分期（P0-3）
    if any(k in cn for k in ("高血压", "hypertension")):
        has_bp = re.search(r'(?:血压|bp)\s*[：:]*\s*(\d+)\s*[/／]\s*(\d+)', text)
        if has_bp:
            sys_bp, dia_bp = int(has_bp.group(1)), int(has_bp.group(2))
        else:
            sys_bp, dia_bp = 0, 0
        # 先检查急症特征
        emergency_text = any(k in text for k in ("胸痛", "气促", "神经缺损", "肢体无力", "意识异常", "视物异常", "少尿"))
        if sys_bp >= 180 and dia_bp >= 120 and emergency_text:
            return "hypertensive_emergency_suspected", "HIGH", "血压≥180/120伴靶器官损伤表现，高度怀疑高血压急症"
        if sys_bp >= 160 or dia_bp >= 100:
            return "stage_2_hypertension", "MEDIUM", "收缩压≥160或舒张压≥100，符合高血压2级"
        if sys_bp >= 140 or dia_bp >= 90:
            return "stage_1_hypertension", "MEDIUM", "收缩压≥140或舒张压≥90，符合高血压1级"
        return "hypertension", "LOW", "血压数据不足，按高血压诊断处理"

    # 尿路感染分期（P0-3）
    if any(k in cn for k in ("尿路感染", "肾盂肾炎", "urinary tract infection", "pyelonephritis")):
        # 仅从症状/体征/检查中判断，不混入 negative_findings（含"无发热"等否定词）
        pos_text = " ".join([
            getattr(ni, "chief_complaint", "") or "",
            " ".join(getattr(ni, "symptoms", []) or []),
            " ".join(getattr(ni, "signs", []) or []),
            " ".join(getattr(ni, "labs", []) or []),
            " ".join(getattr(ni, "imaging", []) or []),
        ]).lower()
        has_uti_upper = any(k in pos_text for k in ("发热", "腰痛", "肾区叩痛", "寒战"))
        has_uti_lower = any(k in pos_text for k in ("尿频", "尿急", "尿痛", "尿白细胞"))
        if has_uti_upper:
            return "upper_uti_or_complicated", "HIGH", "发热/腰痛/肾区叩痛提示上尿路感染或复杂UTI"
        if has_uti_lower:
            return "lower_uti", "MEDIUM", "下尿路刺激征为主，无上尿路感染证据"
        return "lower_uti", "LOW", "UTI表现但分期依据不足"

    stages = (entry or {}).get("stages") or []
    if isinstance(stages, list) and stages:
        first = stages[0]
        if isinstance(first, dict):
            return first.get("name", ""), "LOW", "采用知识库默认首期"
        if isinstance(first, str):
            return first, "LOW", "采用知识库默认首期"

    return "", "LOW", ""


def infer_severity(ni, stage: str, red_flags: List[str]) -> str:
    text = clinical_text_blob(ni)
    if red_flags:
        return "severe"
    if any(k in text for k in ("高热", "呼吸困难", "低氧", "气促", "休克")):
        return "moderate"
    if stage:
        return "mild"
    return ""


def map_v8_confidence(bayesian_conf: str, has_discrepancy: bool = False) -> str:
    if has_discrepancy:
        return "LOW"
    if bayesian_conf == "high":
        return "HIGH"
    if bayesian_conf == "medium":
        return "MEDIUM"
    return "LOW"


def map_v8_diagnosis_status(
    *,
    kb_confidence: str,
    has_primary: bool,
    request_more_info: bool,
    all_excluded: bool,
    has_discrepancy: bool,
) -> str:
    if request_more_info:
        return "REQUEST_MORE_INFO"
    if all_excluded or not has_primary:
        return "NO_CANDIDATE"
    if has_discrepancy:
        return "LOW_CONFIDENCE"
    if kb_confidence == "high":
        return "CONFIRMED"
    if kb_confidence == "medium":
        return "PROBABLE"
    return "LOW_CONFIDENCE"


def names_match(a: str, b: str) -> bool:
    a = (a or "").split(" (")[0].split("（")[0].strip().lower()
    b = (b or "").split(" (")[0].split("（")[0].strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def dual_version_check(kb_primary: str, llm_names: List[str]) -> Dict[str, Any]:
    llm_names = [n for n in (llm_names or []) if n and n != "OUT_OF_SCOPE_CANDIDATE"]
    if not llm_names:
        return {
            "opinion": "",
            "discrepancy": None,
            "online_arbitration_needed": False,
        }
    opinion = "；".join(llm_names[:3])
    consistent = any(names_match(kb_primary, name) for name in llm_names)
    if consistent:
        return {
            "opinion": opinion,
            "discrepancy": None,
            "online_arbitration_needed": False,
        }
    return {
        "opinion": opinion,
        "discrepancy": {
            "kb_primary": kb_primary,
            "llm_reference": llm_names[:3],
            "reason": "LLM参考诊断与知识库主判不一致",
        },
        "online_arbitration_needed": True,
    }


def needs_online_staleness_review(entry: Optional[dict]) -> bool:
    if not entry:
        return False
    cn = entry.get("diseaseName_cn", "") or ""
    checked_at = entry.get("source_checked_at") or entry.get("last_updated") or ""
    if not checked_at:
        return False
    try:
        year = int(str(checked_at)[:4])
    except ValueError:
        return False
    from datetime import datetime

    age_years = datetime.now().year - year
    if any(k in cn for k in HIGH_UPDATE_DISEASE_KEYWORDS):
        return age_years > STABLE_DISEASE_HIGH_UPDATE_YEARS
    return age_years > STABLE_DISEASE_NORMAL_YEARS


def aggregate_kb_evidence(scored_item: Optional[dict]) -> Dict[str, List[str]]:
    if not scored_item:
        return {
            "required_criteria_met": [],
            "required_criteria_missing": [],
            "supportive_evidence": [],
            "exclusion_triggered": [],
            "similar_diseases_excluded": [],
        }
    cs = scored_item.get("criteria_struct") or {}
    contradicted = []
    for item in cs.get("contradicted_items", []) or []:
        if isinstance(item, dict):
            contradicted.append(str(item.get("criteria", item)))
        else:
            contradicted.append(str(item))
    supportive = list(cs.get("matched_supportive", []) or [])
    for ev in scored_item.get("evidence_for", []) or []:
        if ev not in supportive:
            supportive.append(ev)
    return {
        "required_criteria_met": list(cs.get("matched_required", []) or []),
        "required_criteria_missing": list(cs.get("missing_required", []) or []),
        "supportive_evidence": supportive,
        "exclusion_triggered": contradicted,
        "similar_diseases_excluded": [],
    }


def merge_candidate_tracks(code_candidates: List[dict], llm_pool: List[str], db: List[dict]) -> List[dict]:
    merged: List[dict] = []
    seen = set()

    def _add(entry: dict):
        key = entry.get("disease_name") or entry.get("diseaseName_cn", "")
        if key and key not in seen:
            seen.add(key)
            merged.append(entry)

    for entry in code_candidates or []:
        _add(entry)

    for name in llm_pool or []:
        if not name or name == "OUT_OF_SCOPE_CANDIDATE":
            continue
        name_lower = name.lower()
        for entry in db:
            cn = entry.get("diseaseName_cn", "").strip()
            en = entry.get("disease_name", "").strip()
            if names_match(name, cn) or names_match(name, en):
                _add(entry)
                break

    merged.sort(
        key=lambda e: (
            0 if e.get("diagnostic_criteria") else 1,
            e.get("diseaseName_cn", ""),
        )
    )
    return merged


def apply_prefilter_penalty(candidates: List[dict], ni) -> List[dict]:
    """Soft downweight — never delete candidates."""
    age_val = None
    if getattr(ni, "age", ""):
        try:
            age_val = int(str(ni.age).replace("岁", "").strip())
        except (ValueError, AttributeError):
            age_val = None

    is_child = age_val is not None and age_val < 14
    is_female = getattr(ni, "sex", "") in ("女", "女性", "female", "F")
    is_male = getattr(ni, "sex", "") in ("男", "男性", "male", "M")
    text = clinical_text_blob(ni)

    pediatric_keywords = ("儿童", "小儿", "新生儿", "婴儿", "幼年", "早产")
    pregnancy_keywords = ("妊娠", "孕期", "产科", "分娩", "先兆流产", "宫外孕", "子痫", "前置胎盘", "产后", "孕")
    male_only_keywords = ("前列腺", "睾丸", "精囊", "阴茎", "阴囊", "包皮", "精索", "输精管", "男性不育")

    out = []
    for entry in candidates or []:
        item = dict(entry)
        penalty = 0
        cn = item.get("diseaseName_cn", "").strip().lower()

        if age_val is not None and not is_child and any(k in cn for k in pediatric_keywords):
            penalty += 25
        if is_male and any(k in cn for k in pregnancy_keywords):
            penalty += 40
        if is_female and any(k in cn for k in male_only_keywords):
            penalty += 40

        duration = getattr(ni, "duration", "") or ""
        if duration and any(k in duration for k in ("年", "月")) and any(k in cn for k in ("急性", "突发")):
            penalty += 10
        if getattr(ni, "location", "") and getattr(ni, "location", "") not in text:
            penalty += 5

        item["_prefilter_penalty"] = penalty
        out.append(item)
    return out


def filter_positive_symptoms(symptoms: List[str], negative_findings: List[str]) -> List[str]:
    """Ensure negated findings are not duplicated as positive symptoms."""
    negatives = set()
    for nf in negative_findings or []:
        nf = nf.strip()
        if not nf:
            continue
        core = re.sub(r"^(无明显|无明确|无|未|没有|否认|不伴)", "", nf).strip()
        if core:
            negatives.add(core.lower())
            negatives.add(nf.lower())

    cleaned = []
    for symptom in symptoms or []:
        s = symptom.strip()
        if not s:
            continue
        s_lower = s.lower()
        if any(s_lower.startswith(p) for p in ("无", "未", "没有", "否认", "不伴", "无明显")):
            continue
        if s_lower in negatives:
            continue
        if any(n in s_lower or s_lower in n for n in negatives if len(n) >= 2):
            continue
        cleaned.append(s)
    return cleaned
