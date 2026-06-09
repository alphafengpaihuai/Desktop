"""M1→M2 bridge helpers — normalize fields and build stable M2 process kwargs."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


NEGATION_PREFIXES = ("无明显", "无明确", "无", "未", "没有", "否认", "不伴")
LAB_HINTS = ("血常规", "尿常规", "便常规", "生化", "CRP", "降钙素原", "白细胞", "血红蛋白", "潜血", "抗原", "抗体")
IMAGING_HINTS = ("CT", "MRI", "胸片", "X线", "DR", "B超", "超声", "PET", "影像", "片示")


def normalize_string_list(value: Any, default: Optional[List[str]] = None) -> List[str]:
    """Coerce dict/list/str inputs into a deduplicated list of non-empty strings."""
    default = default or []
    if value is None:
        return list(default)
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("、", ",").split(",") if part.strip()]
        return items or list(default)
    if isinstance(value, dict):
        collected: List[str] = []
        for key in ("answer", "text", "value", "symptom", "finding"):
            if value.get(key):
                collected.extend(normalize_string_list(value.get(key)))
        if not collected:
            for item in value.values():
                if isinstance(item, str) and item.strip():
                    collected.append(item.strip())
        return _dedupe(collected) or list(default)
    if isinstance(value, (list, tuple, set)):
        collected: List[str] = []
        for item in value:
            if isinstance(item, dict):
                collected.extend(normalize_string_list(item))
            elif isinstance(item, str) and item.strip():
                collected.append(item.strip())
            elif item is not None:
                collected.append(str(item).strip())
        return _dedupe([x for x in collected if x]) or list(default)
    text = str(value).strip()
    return [text] if text else list(default)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_tongue_pulse(signs: Optional[List[str]], symptoms: Optional[List[str]]) -> Tuple[str, str, List[str]]:
    """Split tongue/pulse text from signs/symptoms; return remaining signs."""
    tongue = ""
    pulse = ""
    remaining: List[str] = []

    for raw in normalize_string_list(signs) + normalize_string_list(symptoms):
        if not tongue and ("舌" in raw or "苔" in raw):
            tongue = raw
            continue
        if not pulse and "脉" in raw:
            pulse = raw
            continue
        remaining.append(raw)
    return tongue, pulse, remaining


def merge_negative_into_symptoms(symptoms: List[str], negative_findings: List[str]) -> List[str]:
    """Ensure negated findings appear in symptom text for M2 loader negation detection."""
    merged = list(symptoms)
    for finding in normalize_string_list(negative_findings):
        if not finding:
            continue
        if any(finding in existing or existing in finding for existing in merged):
            continue
        if any(finding.startswith(prefix) for prefix in NEGATION_PREFIXES):
            merged.append(finding)
        else:
            merged.append(f"无{finding}" if not finding.startswith("无") else finding)
    return _dedupe(merged)


def extract_labs_imaging_from_texts(texts: List[str]) -> Tuple[List[str], List[str]]:
    labs: List[str] = []
    imaging: List[str] = []
    for text in normalize_string_list(texts):
        if any(h in text for h in IMAGING_HINTS):
            imaging.append(text)
        elif any(h in text for h in LAB_HINTS):
            labs.append(text)
    return _dedupe(labs), _dedupe(imaging)


def extract_negative_findings_from_texts(texts: List[str]) -> List[str]:
    negatives: List[str] = []
    for text in normalize_string_list(texts):
        stripped = text.strip()
        if any(stripped.startswith(prefix) for prefix in NEGATION_PREFIXES):
            negatives.append(stripped)
    return _dedupe(negatives)


def resolve_primary_disease(m1_result: Optional[Dict], fallback: str = "") -> str:
    if not isinstance(m1_result, dict):
        return fallback or ""
    payload = m1_result.get("m2_payload") or {}
    for key in ("primary_disease", "primary_diagnosis", "standard_disease_name"):
        value = payload.get(key) or m1_result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    calib = m1_result.get("diagnosis_calibration") or {}
    diag_list = normalize_string_list(calib.get("calibrated_diagnosis"))
    if diag_list:
        return diag_list[0]
    top = m1_result.get("top_diagnoses") or []
    if top:
        first = top[0]
        if isinstance(first, dict):
            return first.get("disease_name", "") or fallback
        return str(first)
    candidates = normalize_string_list(m1_result.get("current_top_candidates"))
    if candidates:
        return candidates[0]
    return fallback or ""


def should_gate_m2(m1_result: Optional[Dict]) -> Tuple[bool, str]:
    if not isinstance(m1_result, dict):
        return False, ""
    status = m1_result.get("diagnosis_status", "")
    legacy = m1_result.get("diagnosis_status_legacy", "")
    emergency = (m1_result.get("emergency_alert") or {}).get("triggered", False)
    blocked_statuses = ("REQUEST_MORE_INFO", "NO_CANDIDATE", "NEED_EXTERNAL_SEARCH")
    if status in blocked_statuses or legacy in blocked_statuses or emergency:
        return True, status or legacy or "REQUEST_MORE_INFO"
    payload = m1_result.get("m2_payload") or {}
    if not payload:
        if status not in ("CONFIRMED", "PROBABLE", "LOW_CONFIDENCE", "PASS"):
            return True, status or "REQUEST_MORE_INFO"
    if payload.get("diagnosis_status") in blocked_statuses:
        return True, payload.get("diagnosis_status")
    return False, ""


def legacy_bridge_prescription_path_enabled() -> bool:
    return os.getenv("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", "false").lower() == "true"


def build_m2_process_kwargs(
    *,
    primary_disease: str,
    m1_result: Optional[Dict] = None,
    symptoms: Optional[List[str]] = None,
    signs: Optional[List[str]] = None,
    labs: Optional[List[str]] = None,
    imaging: Optional[List[str]] = None,
    negative_findings: Optional[List[str]] = None,
    age: str = "",
    weight: str = "",
) -> Dict[str, Any]:
    """Build stable kwargs for M2SyndromeSelector.process()."""
    payload = (m1_result or {}).get("m2_payload") if isinstance(m1_result, dict) else {}
    payload = payload or {}

    merged_symptoms = normalize_string_list(symptoms or payload.get("symptoms"))
    merged_signs = normalize_string_list(signs or payload.get("signs"))
    merged_labs = normalize_string_list(labs or payload.get("labs"))
    merged_imaging = normalize_string_list(imaging or payload.get("imaging"))
    merged_negative = normalize_string_list(negative_findings or payload.get("negative_findings"))

    tongue = str(payload.get("tongue") or "").strip()
    pulse = str(payload.get("pulse") or "").strip()
    extracted_tongue, extracted_pulse, positive_signs = extract_tongue_pulse(merged_signs, [])
    tongue = tongue or extracted_tongue
    pulse = pulse or extracted_pulse
    if not tongue or not pulse:
        t2, p2, _ = extract_tongue_pulse([], merged_symptoms)
        tongue = tongue or t2
        pulse = pulse or p2

    final_symptoms = merge_negative_into_symptoms(merged_symptoms, merged_negative)

    resolved_primary = (
        (primary_disease or "").strip()
        or resolve_primary_disease(m1_result)
    )

    return {
        "primary_disease": resolved_primary,
        "symptoms": final_symptoms,
        "signs": positive_signs,
        "tongue": tongue,
        "pulse": pulse,
        "labs": merged_labs,
        "imaging": merged_imaging,
        "negative_findings": merged_negative,
        "age": str(age or payload.get("age") or "").strip(),
        "weight": str(weight or payload.get("weight") or "").strip(),
    }


def extract_m2_result_display(m2_result: Optional[Dict]) -> Dict[str, Any]:
    if not isinstance(m2_result, dict):
        return {"syndrome_name": "", "formula_name": "", "herbs": []}
    sd = m2_result.get("syndrome_differentiation") or {}
    ss = sd.get("selected_syndrome") or {}
    formula = m2_result.get("formula") or {}
    return {
        "syndrome_name": ss.get("name") or m2_result.get("syndrome_trace", {}).get("syndrome_name", ""),
        "formula_name": formula.get("name") or m2_result.get("bound_formula", {}).get("formula_name", ""),
        "herbs": normalize_string_list(formula.get("herbs") or m2_result.get("base_herbs")),
    }
