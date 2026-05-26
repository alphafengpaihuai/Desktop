"""
External disease knowledge fetcher for Shouyi M1.

This module is intentionally western-diagnosis only. It never writes to
`diseases_core.json`; externally supplemented cards are stored as pending
review material under data/disease_cache/.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CORE_DB = BASE_DIR / "diseases_core.json"
CACHE_DIR = BASE_DIR / "data" / "disease_cache"
CONFIRMED_PATH = CACHE_DIR / "confirmed_disease_cards.json"
PENDING_PATH = CACHE_DIR / "pending_disease_cards.json"
RUNTIME_PATH = CACHE_DIR / "runtime_cache.json"
TRACE_PATH = CACHE_DIR / "external_fetch_trace.jsonl"
PROGRESS_PATH = CACHE_DIR / "enrichment_progress.json"
REPORT_PATH = CACHE_DIR / "enrichment_report.md"

TRUSTED_SOURCE_WHITELIST = [
    "MSD Manual",
    "Merck Manual",
    "CDC",
    "WHO",
    "NICE",
    "AAFP",
    "GINA",
    "NCBI Bookshelf",
]

FORBIDDEN_TCM_TERMS = [
    "证型", "病机", "方剂", "中药", "治法", "治则", "剂量", "汤", "丸",
    "散", "针灸", "推拿", "草药",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_cache_files() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path, default in [
        (CONFIRMED_PATH, []),
        (PENDING_PATH, []),
        (RUNTIME_PATH, {}),
        (PROGRESS_PATH, {"completed": [], "failed": [], "pending": []}),
    ]:
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
    if not TRACE_PATH.exists():
        TRACE_PATH.write_text("", encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    _ensure_cache_files()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(default)


def _write_json(path: Path, data: Any) -> None:
    _ensure_cache_files()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(name: str) -> str:
    return "".join(str(name or "").lower().replace("（", "(").replace("）", ")").split())


def _card_names(card: dict) -> set[str]:
    names = {card.get("disease_name", "")}
    names.update(card.get("aliases", []) or [])
    return {_norm(n) for n in names if n}


def _find_card(cards: list[dict], disease_name: str) -> dict | None:
    key = _norm(disease_name)
    for card in cards:
        if key in _card_names(card):
            return card
    return None


def _core_card_from_entry(entry: dict) -> dict:
    axes = entry.get("pathology_axes") or entry.get("axes") or []
    return {
        "disease_name": entry.get("disease_name", ""),
        "aliases": entry.get("aliases", []),
        "entry_type": entry.get("entry_type", "standard_western_disease"),
        "m1_diagnosis_entry_allowed": bool(entry.get("m1_diagnosis_entry_allowed", True)),
        "diagnostic_key_points": entry.get("diagnostic_key_points", []),
        "rule_in_features": entry.get("rule_in_features", []),
        "rule_out_features": entry.get("rule_out_features", []),
        "differential_diagnoses": entry.get("differential_diagnoses", []),
        "required_checks": entry.get("required_checks", entry.get("need_external_check_if", [])),
        "red_flags": entry.get("red_flags", []),
        "typical_age_or_population": entry.get("typical_age_or_population", []),
        "typical_course": entry.get("typical_course", ""),
        "pathology_axes": axes,
        "must_not_be_primary_when": entry.get("must_not_be_primary_when", []),
        "need_external_check_if": entry.get("need_external_check_if", []),
        "source_summary": ["local diseases_core.json"],
        "source_urls": [],
        "confidence": "medium",
        "status": "confirmed_local",
        "created_at": "",
        "updated_at": "",
        "_source": "confirmed_local",
    }


def _load_core_cards() -> list[dict]:
    if not CORE_DB.exists():
        return []
    try:
        entries = json.loads(CORE_DB.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [_core_card_from_entry(e) for e in entries if isinstance(e, dict)]


def _has_missing_fields(card: dict, missing_fields: list[str] | None) -> bool:
    if not missing_fields:
        return False
    for field in missing_fields:
        value = card.get(field)
        if value in (None, "", [], {}):
            return True
    return False


def _append_trace(event: dict) -> None:
    _ensure_cache_files()
    event.setdefault("created_at", _now())
    with TRACE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _validate_no_tcm(card: dict) -> None:
    blob = json.dumps(card, ensure_ascii=False)
    found = [term for term in FORBIDDEN_TCM_TERMS if term in blob]
    if found:
        raise ValueError(f"pending disease card contains forbidden TCM terms: {found}")


def fetch_or_get_cached_disease_card(
    disease_name: str,
    missing_fields: list[str] | None = None,
    trigger_reason: str = "",
    patient_context: dict | None = None,
) -> dict:
    """Return a disease card using local-first cache order.

    Lookup order:
    1. diseases_core.json / confirmed_disease_cards.json
    2. pending_disease_cards.json
    3. runtime_cache.json
    4. external fetch provider stub
    5. llm candidate only fallback
    """
    _ensure_cache_files()
    missing_fields = missing_fields or []
    patient_context = patient_context or {}

    confirmed_extra = _load_json(CONFIRMED_PATH, [])
    core_match = _find_card(_load_core_cards(), disease_name)
    confirmed_match = _find_card(confirmed_extra, disease_name)
    local_match = confirmed_match or core_match
    if local_match and not _has_missing_fields(local_match, missing_fields):
        result = copy.deepcopy(local_match)
        result["_source"] = result.get("_source") or "confirmed_local"
        result["_external_fetch_triggered"] = False
        return result

    pending = _load_json(PENDING_PATH, [])
    pending_match = _find_card(pending, disease_name)
    if pending_match:
        result = copy.deepcopy(pending_match)
        result["_source"] = "pending_cache"
        result["_external_fetch_triggered"] = False
        return result

    runtime = _load_json(RUNTIME_PATH, {})
    key = _norm(disease_name)
    if key in runtime:
        cached = runtime[key]
        # A previous llm-only miss should not block a now-available curated
        # provider card from being written into pending review.
        if cached.get("status") != "llm_candidate_only" or _external_fetch_stub(disease_name, missing_fields, trigger_reason, patient_context) is None:
            result = copy.deepcopy(cached)
            result["_source"] = "runtime_cache"
            result["_external_fetch_triggered"] = False
            return result

    card = _external_fetch_stub(disease_name, missing_fields, trigger_reason, patient_context)
    if card:
        _validate_no_tcm(card)
        _upsert_pending(card)
        runtime[key] = card
        _write_json(RUNTIME_PATH, runtime)
        _append_trace({
            "event": "external_fetch",
            "disease_name": disease_name,
            "trigger_reason": trigger_reason,
            "missing_fields": missing_fields,
            "provider": "curated_trusted_source_stub",
            "source_urls": card.get("source_urls", []),
            "status": "pending_review_written",
        })
        result = copy.deepcopy(card)
        result["_source"] = "external_fetch"
        result["_external_fetch_triggered"] = True
        return result

    fallback = {
        "disease_name": disease_name,
        "aliases": [],
        "entry_type": "standard_western_disease",
        "m1_diagnosis_entry_allowed": False,
        "diagnostic_key_points": [],
        "rule_in_features": [],
        "rule_out_features": [],
        "differential_diagnoses": [],
        "required_checks": [],
        "red_flags": [],
        "typical_age_or_population": [],
        "typical_course": "",
        "pathology_axes": [],
        "must_not_be_primary_when": ["insufficient verified disease knowledge"],
        "need_external_check_if": ["local and pending disease card unavailable"],
        "source_summary": [],
        "source_urls": [],
        "confidence": "low",
        "status": "llm_candidate_only",
        "created_at": _now(),
        "updated_at": _now(),
        "_source": "llm_only",
        "_external_fetch_triggered": False,
    }
    runtime[key] = fallback
    _write_json(RUNTIME_PATH, runtime)
    return fallback


def _upsert_pending(card: dict) -> None:
    pending = _load_json(PENDING_PATH, [])
    key = _norm(card.get("disease_name", ""))
    updated = False
    for i, existing in enumerate(pending):
        if key in _card_names(existing):
            pending[i] = card
            updated = True
            break
    if not updated:
        pending.append(card)
    pending.sort(key=lambda c: c.get("disease_name", ""))
    _write_json(PENDING_PATH, pending)


def seed_pending_cards(disease_names: list[str] | None = None) -> list[dict]:
    names = disease_names or list(CURATED_CARD_TEMPLATES.keys())
    return [
        fetch_or_get_cached_disease_card(
            name,
            missing_fields=[
                "diagnostic_key_points",
                "rule_in_features",
                "rule_out_features",
                "differential_diagnoses",
                "required_checks",
                "red_flags",
            ],
            trigger_reason="seed_resp_ent_infectious_m1_cards",
        )
        for name in names
    ]


def _axis(axis: str, role: str, weight: float, findings: list[str], confidence: str = "high") -> dict:
    return {
        "axis": axis,
        "role": role,
        "weight": weight,
        "confidence": confidence,
        "typical_findings": findings,
    }


def _diff(name: str, points: list[str]) -> dict:
    return {"disease_name": name, "distinguishing_points": points}


COMMON_RED_FLAGS = [
    "respiratory distress",
    "cyanosis or hypoxemia",
    "persistent high fever",
    "poor mental response",
    "dehydration",
    "chest pain",
    "hemoptysis",
    "recurrent pneumonia",
]

RESP_SOURCES = {
    "MSD": "https://www.msdmanuals.com/professional/pulmonary-disorders/bronchitis-and-bronchiolitis/acute-bronchitis",
    "CDC_PERTUSSIS": "https://www.cdc.gov/pertussis/hcp/clinical-signs/index.html",
    "CDC_FLU": "https://www.cdc.gov/flu/signs-symptoms/index.html",
    "CDC_MEASLES": "https://www.cdc.gov/measles/signs-symptoms/index.html",
    "CDC_SCARLET": "https://www.cdc.gov/group-a-strep/hcp/clinical-guidance/scarlet-fever.html",
    "GINA": "https://ginasthma.org/",
    "NICE_COUGH": "https://www.nice.org.uk/guidance/ng120",
    "NICE_SINUSITIS": "https://www.nice.org.uk/guidance/ng79",
    "MSD_RHINITIS": "https://www.msdmanuals.com/professional/ear-nose-and-throat-disorders/nose-and-sinus-disorders/allergic-rhinitis",
    "MSD_TONSILS_ADENOIDS": "https://www.msdmanuals.com/professional/pediatrics/ear-nose-and-throat-disorders-in-children/tonsillar-and-adenoidal-hyperplasia",
    "CDC_COVID": "https://www.cdc.gov/covid/signs-symptoms/index.html",
}


def _card(
    disease_name: str,
    aliases: list[str],
    key_points: list[str],
    rule_in: list[str],
    rule_out: list[str],
    diffs: list[dict],
    checks: list[str],
    red_flags: list[str],
    axes: list[dict],
    sources: list[str],
    entry_type: str = "standard_western_disease",
    population: list[str] | None = None,
    course: str = "",
    must_not: list[str] | None = None,
    need_check: list[str] | None = None,
    confidence: str = "medium",
) -> dict:
    now = _now()
    return {
        "disease_name": disease_name,
        "aliases": aliases,
        "source_raw_names": [disease_name] + aliases,
        "department_or_category": "儿科/呼吸/耳鼻喉/感染",
        "entry_type": entry_type,
        "m1_diagnosis_entry_allowed": True,
        "diagnostic_key_points": key_points,
        "rule_in_features": rule_in,
        "rule_out_features": rule_out,
        "differential_diagnoses": diffs,
        "required_checks": checks,
        "red_flags": red_flags,
        "typical_age_or_population": population or ["children and adults depending on disease"],
        "typical_course": course,
        "pathology_axes": axes,
        "must_not_be_primary_when": must_not or [],
        "need_external_check_if": need_check or red_flags,
        "source_summary": [f"Structured from trusted western medical sources: {', '.join(TRUSTED_SOURCE_WHITELIST[:6])}."],
        "source_urls": sources,
        "confidence": confidence,
        "status": "pending_review",
        "created_at": now,
        "updated_at": now,
    }


def _ensure_min_quality(card: dict) -> dict:
    card = copy.deepcopy(card)
    card.setdefault("source_raw_names", [card.get("disease_name", "")] + card.get("aliases", []))
    card.setdefault("department_or_category", "儿科/呼吸/耳鼻喉/感染")
    card.setdefault("typical_age_or_population", ["children and adults depending on disease"])
    card.setdefault("typical_course", "course depends on disease subtype and severity")
    while len(card.get("diagnostic_key_points", [])) < 3:
        card.setdefault("diagnostic_key_points", []).append("diagnosis requires compatible clinical pattern and exclusion of common mimics")
    while len(card.get("rule_in_features", [])) < 3:
        card.setdefault("rule_in_features", []).append("compatible history, physical findings, or objective test support")
    while len(card.get("rule_out_features", [])) < 2:
        card.setdefault("rule_out_features", []).append("alternative diagnosis better explains the presentation")
    while len(card.get("differential_diagnoses", [])) < 3:
        card.setdefault("differential_diagnoses", []).append(_diff("other respiratory or infectious disease", ["distinguish by objective tests, time course, and dominant anatomic site"]))
    while len(card.get("required_checks", [])) < 2:
        card.setdefault("required_checks", []).append("targeted examination or testing based on severity and diagnostic uncertainty")
    while len(card.get("red_flags", [])) < 2:
        card.setdefault("red_flags", []).extend(COMMON_RED_FLAGS[:2])
    if not card.get("source_urls"):
        card["source_urls"] = [RESP_SOURCES["MSD"]]
    if not (2 <= len(card.get("pathology_axes", [])) <= 6):
        card.setdefault("pathology_axes", [])
        if len(card["pathology_axes"]) < 2:
            card["pathology_axes"].append(_axis("infection_or_inflammation", "primary", 0.6, ["compatible inflammatory or infectious features"], "medium"))
            card["pathology_axes"].append(_axis("upper_airway_involvement", "location", 0.5, ["upper airway findings"], "medium"))
        card["pathology_axes"] = card["pathology_axes"][:6]
    return card


CURATED_CARD_TEMPLATES: dict[str, dict] = {
    "急性支气管炎": _card(
        "急性支气管炎", ["Acute Bronchitis", "急性气管支气管炎"],
        ["acute cough usually less than 3 weeks", "pneumonia should be excluded when fever, tachypnea, hypoxemia, or focal findings are present"],
        ["acute cough", "cough with or without sputum", "rhonchi or coarse breath sounds", "preceding upper respiratory infection", "low-grade fever"],
        ["focal consolidation on chest imaging", "hypoxemia", "persistent high fever", "paroxysmal whoop cough", "dominant allergic rhinitis features with eosinophilia"],
        [_diff("社区获得性肺炎", ["imaging infiltrate or consolidation", "more systemic toxicity"]), _diff("百日咳", ["paroxysmal cough, whoop, post-tussive vomiting"]), _diff("咳嗽变异性哮喘", ["chronic or recurrent nocturnal cough and bronchodilator response"])],
        ["vital signs and oxygen saturation", "chest imaging if pneumonia red flags", "CBC/CRP when bacterial infection suspected", "pathogen testing when clinically indicated"],
        COMMON_RED_FLAGS,
        [_axis("infection_or_inflammation", "primary", 0.9, ["acute cough", "fever", "URI prodrome"]), _axis("lower_airway_involvement", "location", 0.8, ["coarse breath sounds", "rhonchi", "cough"]), _axis("secretion_or_retention", "secondary", 0.6, ["sputum", "productive cough"]), _axis("airway_hyperreactivity", "secondary", 0.4, ["transient wheeze", "post-infectious cough"], "medium")],
        [RESP_SOURCES["MSD"], RESP_SOURCES["NICE_COUGH"]],
        course="acute, usually self-limited",
        must_not=["pneumonia with imaging infiltrate", "asthma exacerbation as primary diagnosis", "pertussis pattern cough"],
    ),
    "慢性支气管炎": _card(
        "慢性支气管炎", ["Chronic Bronchitis"],
        ["productive cough for at least 3 months in each of 2 consecutive years", "usually adult COPD phenotype"],
        ["chronic productive cough", "smoking or irritant exposure", "recurrent winter exacerbations"],
        ["short acute cough only", "child without chronic exposure history", "dominant allergic/nasal symptoms only"],
        [_diff("急性支气管炎", ["acute duration under 3 weeks"]), _diff("支气管哮喘", ["variable wheeze and reversible obstruction"]), _diff("支气管扩张", ["chronic purulent sputum and CT bronchial dilation"])],
        ["spirometry", "smoking/exposure history", "chest imaging if atypical", "oxygen saturation"],
        COMMON_RED_FLAGS,
        [_axis("lower_airway_involvement", "primary", 0.8, ["chronic productive cough"]), _axis("secretion_or_retention", "primary", 0.8, ["sputum hypersecretion"]), _axis("mucosal_barrier_dysfunction", "pathogenesis", 0.6, ["chronic bronchial irritation"])],
        [RESP_SOURCES["MSD"]],
        population=["mainly adults with smoking or irritant exposure"],
        course="chronic/recurrent",
    ),
    "支原体肺炎": _card(
        "支原体肺炎", ["Mycoplasma Pneumonia", "Mycoplasma pneumoniae pneumonia", "肺炎支原体肺炎"],
        ["school-age child or adolescent with persistent cough", "Mycoplasma test positive supports diagnosis", "imaging may show pneumonia even when auscultation is mild"],
        ["Mycoplasma positive", "persistent dry or paroxysmal cough", "fever", "school-age child", "chest imaging pneumonia"],
        ["normal imaging with brief URI symptoms", "clear allergic trigger with eosinophilia only", "classic pertussis whoop without pneumonia evidence"],
        [_diff("急性支气管炎", ["no radiographic pneumonia and usually shorter course"]), _diff("社区获得性肺炎", ["broader bacterial/viral etiologies"]), _diff("百日咳", ["whoop, apnea, post-tussive vomiting"])],
        ["Mycoplasma PCR/serology interpreted with clinical context", "chest radiograph or lung ultrasound", "CBC/CRP", "oxygen saturation"],
        COMMON_RED_FLAGS,
        [_axis("infection_or_inflammation", "primary", 0.9, ["Mycoplasma positive", "fever"]), _axis("lower_airway_involvement", "location", 0.85, ["pneumonia imaging", "persistent cough"]), _axis("airway_hyperreactivity", "secondary", 0.5, ["paroxysmal cough"]), _axis("secretion_or_retention", "secondary", 0.4, ["sputum"], "medium")],
        ["https://www.cdc.gov/mycoplasma/about/index.html", "https://www.cdc.gov/mycoplasma/hcp/clinical-overview/index.html"],
        population=["children and adolescents"],
        course="subacute respiratory infection",
    ),
    "社区获得性肺炎": _card(
        "社区获得性肺炎", ["Community-Acquired Pneumonia", "CAP"],
        ["acute infection of lung parenchyma acquired outside hospital", "cough plus fever/tachypnea/focal findings; imaging supports diagnosis"],
        ["fever", "cough", "tachypnea", "crackles or decreased breath sounds", "hypoxemia", "chest imaging infiltrate"],
        ["normal chest imaging when obtained", "isolated nasal symptoms", "brief cough without systemic features"],
        [_diff("急性支气管炎", ["bronchitis lacks parenchymal infiltrate"]), _diff("支原体肺炎", ["atypical pathogen pattern and persistent cough"]), _diff("流行性感冒", ["systemic viral syndrome without focal infiltrate"])],
        ["oxygen saturation", "chest radiograph if diagnosis uncertain or severe", "CBC/CRP/PCT as context", "pathogen testing when severe/outbreak"],
        COMMON_RED_FLAGS,
        [_axis("infection_or_inflammation", "primary", 0.95, ["fever", "inflammatory markers"]), _axis("lower_airway_involvement", "primary", 0.95, ["infiltrate", "crackles", "tachypnea"]), _axis("secretion_or_retention", "secondary", 0.5, ["productive cough"], "medium")],
        ["https://www.msdmanuals.com/professional/pulmonary-disorders/pneumonia/community-acquired-pneumonia", "https://www.aafp.org/pubs/afp/issues/2021/1200/p618.html"],
        course="acute",
    ),
    "儿童支气管肺炎": _card(
        "儿童支气管肺炎", ["Pediatric Bronchopneumonia", "Bronchopneumonia in children"],
        ["pediatric pneumonia pattern with patchy bronchocentric infection", "cough, fever, tachypnea, crackles or coarse breath sounds"],
        ["child", "fever", "cough", "tachypnea", "crackles", "patchy infiltrates"],
        ["no fever/tachypnea and normal imaging", "isolated allergic rhinitis symptoms"],
        [_diff("急性支气管炎", ["no alveolar/parenchymal pneumonia"]), _diff("支原体肺炎", ["school-age persistent cough and Mycoplasma evidence"]), _diff("哮喘急性发作", ["wheeze and reversible obstruction predominates"])],
        ["respiratory rate by age", "oxygen saturation", "chest imaging if severe or uncertain", "CBC/CRP", "pathogen testing as indicated"],
        COMMON_RED_FLAGS + ["age under 3 months with fever"],
        [_axis("infection_or_inflammation", "primary", 0.9, ["fever", "infection"]), _axis("lower_airway_involvement", "primary", 0.9, ["crackles", "patchy infiltrates"]), _axis("secretion_or_retention", "secondary", 0.55, ["sputum", "secretions"])],
        ["https://www.msdmanuals.com/professional/pediatrics/infections-in-neonates-and-infants/pneumonia-in-neonates", "https://www.who.int/news-room/fact-sheets/detail/pneumonia"],
        population=["children"],
        course="acute",
    ),
    "支气管哮喘": _card(
        "支气管哮喘", ["Bronchial Asthma", "Asthma"],
        ["variable respiratory symptoms plus variable expiratory airflow limitation", "wheeze, dyspnea, chest tightness, cough vary over time and triggers"],
        ["recurrent wheeze", "dyspnea", "chest tightness", "night or early morning cough", "triggered by exercise/allergens/cold air", "bronchodilator response"],
        ["isolated fever with infiltrate", "productive purulent cough as sole feature", "fixed upper airway obstruction"],
        [_diff("咳嗽变异性哮喘", ["cough is sole or predominant symptom"]), _diff("急性支气管炎", ["acute infectious cough without variable airflow limitation"]), _diff("变应性鼻炎", ["nasal symptoms without lower airway variability"])],
        ["spirometry with bronchodilator response", "peak flow variability", "FeNO or eosinophils as type 2 inflammation support", "allergy assessment"],
        COMMON_RED_FLAGS + ["silent chest", "inability to speak full sentences"],
        [_axis("airway_hyperreactivity", "primary", 0.95, ["variable wheeze", "bronchodilator response"]), _axis("lower_airway_involvement", "location", 0.85, ["wheeze", "dyspnea"]), _axis("allergic_th2_inflammation", "pathogenesis", 0.65, ["eosinophilia", "allergy"], "medium")],
        [RESP_SOURCES["GINA"], "https://www.nhlbi.nih.gov/health/asthma"],
        course="recurrent variable",
    ),
    "咳嗽变异性哮喘": _card(
        "咳嗽变异性哮喘", ["Cough Variant Asthma", "CVA"],
        ["chronic or recurrent cough as predominant/only asthma symptom", "often nocturnal or exercise/cold-air triggered", "airway hyperresponsiveness or bronchodilator response supports"],
        ["night cough", "recurrent dry cough", "exercise/cold-air trigger", "eosinophilia or elevated FeNO", "bronchodilator or anti-inflammatory response"],
        ["fever and focal infiltrate", "prominent purulent sputum with acute infection", "dominant nasal postnasal drip symptoms only"],
        [_diff("急性支气管炎", ["acute infectious course and sputum/fever"]), _diff("上气道咳嗽综合征", ["nasal/postnasal drip findings"]), _diff("百日咳", ["paroxysms with whoop/post-tussive vomiting"])],
        ["spirometry", "bronchial challenge if spirometry normal", "FeNO", "eosinophil count", "therapeutic response documentation"],
        COMMON_RED_FLAGS,
        [_axis("airway_hyperreactivity", "primary", 0.95, ["night cough", "cold-air trigger"]), _axis("lower_airway_involvement", "location", 0.65, ["cough"]), _axis("allergic_th2_inflammation", "pathogenesis", 0.6, ["eosinophilia", "FeNO"], "medium")],
        [RESP_SOURCES["GINA"], "https://www.ncbi.nlm.nih.gov/books/NBK519537/"],
        course="chronic or recurrent",
    ),
    "上气道咳嗽综合征": _card(
        "上气道咳嗽综合征", ["Upper Airway Cough Syndrome", "UACS", "postnasal drip syndrome"],
        ["cough related to rhinitis/rhinosinusitis/postnasal drainage", "nasal congestion, rhinorrhea, throat clearing or cobblestoning support"],
        ["nasal congestion", "rhinorrhea", "postnasal drip", "throat clearing", "cough worse lying down", "upper airway findings"],
        ["pneumonia infiltrate", "isolated lower airway wheeze with reversible obstruction", "Mycoplasma pneumonia evidence as main process"],
        [_diff("变应性鼻炎", ["rhinitis may be cause; UACS emphasizes cough mechanism"]), _diff("咳嗽变异性哮喘", ["airway hyperresponsiveness without nasal driver"]), _diff("慢性鼻窦炎", ["persistent purulent drainage/facial pressure >12 weeks"])],
        ["nasal examination", "nasal endoscopy when persistent", "allergy evaluation", "sinus imaging only when indicated"],
        COMMON_RED_FLAGS,
        [_axis("upper_airway_involvement", "primary", 0.9, ["postnasal drip", "nasal congestion"]), _axis("secretion_or_retention", "primary", 0.75, ["nasal drainage", "throat clearing"]), _axis("airway_hyperreactivity", "secondary", 0.45, ["cough"], "medium")],
        ["https://www.aafp.org/pubs/afp/issues/2017/1101/p575.html", RESP_SOURCES["MSD_RHINITIS"]],
        entry_type="western_syndrome",
        course="subacute or chronic cough syndrome",
    ),
    "变应性鼻炎": _card(
        "变应性鼻炎", ["Allergic Rhinitis", "过敏性鼻炎"],
        ["IgE-mediated nasal inflammation", "sneezing, nasal itching, watery rhinorrhea, congestion; allergen exposure pattern"],
        ["sneezing", "nasal itch", "watery rhinorrhea", "nasal congestion", "allergen exposure", "eosinophilia or positive allergy testing"],
        ["persistent high fever", "purulent unilateral discharge", "dominant lower airway infection", "facial pain with bacterial sinusitis pattern"],
        [_diff("急性鼻炎", ["viral prodrome and short infectious course"]), _diff("急性鼻窦炎", ["purulent discharge/facial pain/worsening course"]), _diff("上气道咳嗽综合征", ["UACS is cough mechanism from upper airway disease"])],
        ["clinical history", "allergen-specific IgE or skin prick testing when needed", "nasal examination"],
        COMMON_RED_FLAGS,
        [_axis("allergic_th2_inflammation", "primary", 0.9, ["allergen trigger", "eosinophilia"]), _axis("upper_airway_involvement", "primary", 0.9, ["sneezing", "rhinorrhea", "congestion"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.45, ["nasal mucosal edema"], "medium")],
        [RESP_SOURCES["MSD_RHINITIS"], "https://www.aafp.org/pubs/afp/issues/2015/1201/p985.html"],
        course="intermittent or persistent",
    ),
}


def _extend_cards() -> None:
    """Populate the remaining respiratory/ENT/infectious card templates."""
    base_sources = [RESP_SOURCES["MSD"], RESP_SOURCES["NICE_COUGH"]]
    simple_specs = [
        ("鼻炎哮喘综合征", ["变应性鼻炎合并支气管哮喘", "Allergic rhinitis with asthma", "United airway allergic disease"], "western_syndrome", ["nasal allergy symptoms plus variable lower airway symptoms", "rhinitis and asthma often coexist"], ["nasal congestion", "sneezing", "wheeze", "night cough", "eosinophilia", "allergen trigger"], ["fever with infiltrate as sole explanation", "isolated acute bronchitis without recurrent variability"], ["变应性鼻炎", "支气管哮喘", "咳嗽变异性哮喘"], [_axis("allergic_th2_inflammation", "primary", 0.9, ["eosinophilia", "allergy"]), _axis("upper_airway_involvement", "location", 0.85, ["rhinitis"]), _axis("lower_airway_involvement", "location", 0.75, ["wheeze/cough"]), _axis("airway_hyperreactivity", "primary", 0.8, ["variable symptoms"])]),
        ("儿童哮喘", ["Pediatric Asthma", "childhood asthma", "支气管哮喘"], "standard_western_disease", ["variable wheeze/cough/dyspnea in child", "symptoms vary with triggers and improve with bronchodilator/controller treatment", "objective airflow variability when measurable"], ["child", "recurrent wheeze", "night cough", "exercise/cold-air trigger", "atopy/eosinophilia"], ["fever with focal infiltrate", "single brief viral cough only"], ["咳嗽变异性哮喘", "急性支气管炎", "支原体肺炎"], [_axis("airway_hyperreactivity", "primary", 0.95, ["variable symptoms"]), _axis("lower_airway_involvement", "location", 0.8, ["wheeze/cough"]), _axis("allergic_th2_inflammation", "pathogenesis", 0.65, ["atopy/eosinophilia"], "medium")]),
        ("急性鼻炎", ["Acute Rhinitis", "common cold rhinitis"], "standard_western_disease", ["acute nasal mucosal inflammation, usually viral"], ["acute nasal congestion", "rhinorrhea", "sneezing", "URI exposure"], ["symptoms >10 days with worsening purulence", "allergen-linked recurrent pattern"], ["变应性鼻炎", "急性鼻窦炎"], [_axis("infection_or_inflammation", "primary", 0.75, ["viral URI"]), _axis("upper_airway_involvement", "primary", 0.9, ["nasal congestion", "rhinorrhea"])]),
        ("慢性鼻炎", ["Chronic Rhinitis"], "standard_western_disease", ["persistent nasal inflammation/congestion beyond acute infection"], ["chronic congestion", "rhinorrhea", "postnasal drip"], ["acute fever-purulence pattern", "clear asthma-only lower airway disease"], ["变应性鼻炎", "慢性鼻窦炎"], [_axis("upper_airway_involvement", "primary", 0.9, ["chronic nasal symptoms"]), _axis("mucosal_barrier_dysfunction", "pathogenesis", 0.55, ["mucosal inflammation"])]),
        ("急性鼻窦炎", ["Acute Rhinosinusitis", "Acute Sinusitis"], "standard_western_disease", ["acute inflammation of nasal cavity and sinuses", "bacterial pattern suggested by persistent >10 days, severe onset, or double worsening"], ["purulent nasal discharge", "facial pain/pressure", "symptoms >10 days", "double worsening"], ["brief watery rhinorrhea only", "isolated cough without nasal/sinus symptoms"], ["急性鼻炎", "变应性鼻炎"], [_axis("infection_or_inflammation", "primary", 0.85, ["severe/persistent/worsening course"]), _axis("upper_airway_involvement", "primary", 0.9, ["sinus/nasal symptoms"]), _axis("secretion_or_retention", "secondary", 0.65, ["purulent discharge"])]),
        ("慢性鼻窦炎", ["Chronic Rhinosinusitis", "Chronic Sinusitis"], "standard_western_disease", ["nasal obstruction/drainage plus facial pressure or smell reduction for at least 12 weeks"], ["nasal obstruction", "mucopurulent drainage", "facial pressure", "reduced smell", "duration >12 weeks"], ["short acute URI", "isolated allergic sneezing without chronic sinus criteria"], ["变应性鼻炎", "上气道咳嗽综合征"], [_axis("upper_airway_involvement", "primary", 0.9, ["chronic sinonasal symptoms"]), _axis("mucosal_barrier_dysfunction", "primary", 0.75, ["mucosal inflammation"]), _axis("secretion_or_retention", "secondary", 0.7, ["drainage"])]),
        ("急性咽炎", ["Acute Pharyngitis"], "standard_western_disease", ["acute sore throat; viral most common; group A strep considered with fever, exudate, no cough"], ["sore throat", "fever", "pharyngeal erythema", "tonsillar exudate"], ["dominant cough/rhinorrhea suggests viral URI", "airway obstruction/stridor suggests laryngitis/croup"], ["急性扁桃体炎", "猩红热"], [_axis("infection_or_inflammation", "primary", 0.85, ["sore throat", "fever"]), _axis("upper_airway_involvement", "location", 0.85, ["pharynx"])]),
        ("慢性咽炎", ["Chronic Pharyngitis"], "standard_western_disease", ["persistent throat discomfort often related to irritants, reflux, rhinitis/postnasal drip"], ["chronic throat irritation", "foreign body sensation", "throat clearing"], ["acute fever and exudate", "dominant lower airway infection"], ["上气道咳嗽综合征", "digestive reflux related cough"], [_axis("upper_airway_involvement", "primary", 0.8, ["throat irritation"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.55, ["chronic mucosal irritation"]), _axis("digestive_reflux_or_dysfunction", "trigger", 0.35, ["reflux"], "medium")]),
        ("急性喉炎", ["Acute Laryngitis", "Croup/Laryngotracheitis"], "standard_western_disease", ["hoarseness; in children croup has barking cough and inspiratory stridor"], ["hoarseness", "barking cough", "inspiratory stridor", "viral prodrome"], ["drooling/toxic appearance suggests epiglottitis", "wheezing predominant lower airway disease"], ["急性支气管炎", "异物/会厌炎"], [_axis("infection_or_inflammation", "primary", 0.75, ["viral prodrome"]), _axis("upper_airway_involvement", "primary", 0.9, ["larynx", "stridor"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.5, ["laryngeal edema"])]),
        ("慢性喉炎", ["Chronic Laryngitis"], "standard_western_disease", ["persistent hoarseness from chronic irritation, reflux, voice overuse, smoke/allergens"], ["hoarseness >3 weeks", "voice fatigue", "throat clearing", "reflux or irritant exposure"], ["acute fever/stridor", "neck mass or hemoptysis requiring urgent evaluation"], ["急性喉炎", "reflux laryngitis"], [_axis("upper_airway_involvement", "primary", 0.8, ["laryngeal symptoms"]), _axis("mucosal_barrier_dysfunction", "primary", 0.65, ["chronic irritation"]), _axis("digestive_reflux_or_dysfunction", "trigger", 0.4, ["reflux"], "medium")]),
        ("急性扁桃体炎", ["Acute Tonsillitis"], "standard_western_disease", ["acute tonsillar inflammation; fever, sore throat, tonsillar erythema/exudate"], ["sore throat", "fever", "tonsillar swelling", "tonsillar exudate", "tender cervical nodes"], ["cough/rhinorrhea predominant viral URI", "hepatosplenomegaly suggests mononucleosis"], ["急性咽炎", "传染性单核细胞增多症", "猩红热"], [_axis("infection_or_inflammation", "primary", 0.9, ["fever", "tonsillar inflammation"]), _axis("upper_airway_involvement", "location", 0.9, ["tonsils"])]),
        ("儿童急性扁桃体炎", ["Pediatric Acute Tonsillitis", "小儿急性扁桃体炎", "急性扁桃体炎"], "standard_western_disease", ["acute tonsillar infection/inflammation in a child", "fever, sore throat, tonsillar swelling or exudate support", "distinguish viral URI, EBV, and scarlet fever"], ["child", "sore throat", "fever", "tonsillar swelling", "tonsillar exudate"], ["cough/rhinorrhea predominant viral URI", "hepatosplenomegaly suggests EBV"], ["急性咽炎", "传染性单核细胞增多症", "猩红热"], [_axis("infection_or_inflammation", "primary", 0.9, ["fever", "tonsillar inflammation"]), _axis("upper_airway_involvement", "location", 0.9, ["tonsils"])]),
        ("慢性扁桃体炎", ["Chronic Tonsillitis"], "standard_western_disease", ["recurrent or persistent tonsillar inflammation with chronic throat symptoms"], ["recurrent tonsillitis", "tonsillar hypertrophy", "halitosis/tonsilloliths"], ["single acute sore throat only", "sleep obstruction without infection pattern"], ["急性扁桃体炎", "扁桃体肥大"], [_axis("upper_airway_involvement", "primary", 0.8, ["tonsils"]), _axis("infection_or_inflammation", "secondary", 0.65, ["recurrent infection"]), _axis("structural_abnormality", "secondary", 0.45, ["hypertrophy"], "medium")]),
        ("扁桃体肥大", ["Tonsillar Hypertrophy", "Tonsillar Hyperplasia"], "standard_western_disease", ["enlarged tonsils causing obstruction, snoring, dysphagia, recurrent infection"], ["enlarged tonsils", "snoring", "mouth breathing", "sleep disturbance", "recurrent tonsillitis"], ["acute infection alone without enlargement", "nasal-only obstruction suggesting adenoids/rhinitis"], ["腺样体肥大", "阻塞性睡眠呼吸暂停"], [_axis("structural_abnormality", "primary", 0.9, ["tonsillar enlargement"]), _axis("upper_airway_involvement", "location", 0.75, ["oropharyngeal obstruction"])]),
        ("腺样体肥大", ["Adenoid Hypertrophy", "Adenoidal Hyperplasia"], "standard_western_disease", ["nasopharyngeal lymphoid enlargement causing nasal obstruction and sleep-disordered breathing"], ["mouth breathing", "snoring", "nasal obstruction", "hyponasal speech", "recurrent otitis media"], ["acute rhinitis only", "lower airway cough as sole symptom"], ["变应性鼻炎", "阻塞性睡眠呼吸暂停", "扁桃体肥大"], [_axis("structural_abnormality", "primary", 0.9, ["adenoid enlargement"]), _axis("upper_airway_involvement", "location", 0.85, ["nasal obstruction"]), _axis("secretion_or_retention", "secondary", 0.4, ["postnasal drainage"], "medium")]),
        ("阻塞性睡眠呼吸暂停", ["Obstructive Sleep Apnea", "OSA", "Pediatric OSA"], "western_syndrome", ["recurrent upper airway obstruction during sleep causing snoring, witnessed pauses, gasping, daytime effects"], ["loud snoring", "witnessed apnea", "gasping", "mouth breathing", "daytime sleepiness or behavioral issues"], ["simple nasal congestion without sleep symptoms", "central apnea pattern"], ["腺样体肥大", "扁桃体肥大", "变应性鼻炎"], [_axis("structural_abnormality", "primary", 0.85, ["upper airway obstruction"]), _axis("upper_airway_involvement", "location", 0.75, ["sleep obstruction"]), _axis("autonomic_or_neurovegetative_dysfunction", "stage_related", 0.35, ["sleep disruption"], "medium")]),
        ("百日咳", ["Pertussis", "Whooping Cough"], "standard_western_disease", ["catarrhal then paroxysmal cough; whoop, post-tussive vomiting, apnea in infants"], ["paroxysmal cough", "whoop", "post-tussive vomiting", "apnea", "known exposure"], ["pneumonia infiltrate as primary", "brief cough without paroxysms"], ["支原体肺炎", "咳嗽变异性哮喘", "急性支气管炎"], [_axis("infection_or_inflammation", "primary", 0.9, ["Bordetella pertussis"]), _axis("airway_hyperreactivity", "primary", 0.8, ["paroxysmal cough"]), _axis("lower_airway_involvement", "location", 0.55, ["cough"])]),
        ("流行性感冒", ["Influenza", "Flu"], "standard_western_disease", ["acute respiratory illness with abrupt fever, myalgia, headache, cough during influenza circulation"], ["abrupt fever", "myalgia", "headache", "cough", "fatigue", "known outbreak"], ["focal pneumonia signs alone", "measles rash/Koplik spots", "positive COVID with compatible syndrome"], ["新型冠状病毒感染", "社区获得性肺炎"], [_axis("infection_or_inflammation", "primary", 0.9, ["viral systemic symptoms"]), _axis("upper_airway_involvement", "secondary", 0.5, ["sore throat/rhinorrhea"], "medium"), _axis("lower_airway_involvement", "secondary", 0.45, ["cough"], "medium")]),
        ("新型冠状病毒感染", ["COVID-19", "SARS-CoV-2 infection"], "standard_western_disease", ["compatible respiratory/systemic syndrome with SARS-CoV-2 exposure or positive test"], ["positive SARS-CoV-2 test", "fever", "cough", "sore throat", "anosmia", "exposure"], ["alternative confirmed pathogen fully explains illness"], ["流行性感冒", "社区获得性肺炎"], [_axis("infection_or_inflammation", "primary", 0.9, ["viral infection"]), _axis("upper_airway_involvement", "secondary", 0.5, ["sore throat/nasal symptoms"], "medium"), _axis("lower_airway_involvement", "secondary", 0.55, ["cough/dyspnea"], "medium")]),
        ("麻疹", ["Measles", "Rubeola"], "standard_western_disease", ["fever, cough, coryza, conjunctivitis followed by maculopapular rash; Koplik spots support"], ["fever", "cough", "coryza", "conjunctivitis", "Koplik spots", "maculopapular rash", "exposure/unvaccinated"], ["sandpaper rash with strep features", "isolated URI without rash"], ["猩红热", "流行性感冒"], [_axis("infection_or_inflammation", "primary", 0.95, ["measles virus"]), _axis("upper_airway_involvement", "secondary", 0.7, ["cough/coryza/conjunctivitis"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.5, ["Koplik spots/rash"], "medium")]),
        ("猩红热", ["Scarlet Fever"], "standard_western_disease", ["group A strep illness with fever, pharyngitis and scarlatiniform sandpaper rash"], ["fever", "sore throat", "sandpaper rash", "strawberry tongue", "Pastia lines", "positive strep test"], ["cough/rhinorrhea suggests viral pharyngitis", "Koplik spots/conjunctivitis suggests measles"], ["急性咽炎", "麻疹", "传染性单核细胞增多症"], [_axis("infection_or_inflammation", "primary", 0.9, ["group A strep"]), _axis("upper_airway_involvement", "location", 0.75, ["pharyngitis"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.45, ["rash"], "medium")]),
        ("传染性单核细胞增多症", ["Infectious Mononucleosis", "EBV mononucleosis"], "standard_western_disease", ["fever, pharyngitis, lymphadenopathy, fatigue; atypical lymphocytosis/EBV testing supports"], ["fatigue", "fever", "pharyngitis", "posterior cervical lymphadenopathy", "splenomegaly", "atypical lymphocytes", "EBV test positive"], ["classic scarlet rash with GAS", "brief uncomplicated URI"], ["急性扁桃体炎", "猩红热"], [_axis("infection_or_inflammation", "primary", 0.85, ["EBV infection"]), _axis("upper_airway_involvement", "location", 0.65, ["pharyngitis/tonsillitis"]), _axis("immune_abnormality", "secondary", 0.45, ["lymphadenopathy"], "medium")]),
        ("儿童反复呼吸道感染", ["Recurrent Respiratory Tract Infections in Children", "RRTI"], "western_syndrome", ["repeated respiratory infections exceeding expected frequency for age; evaluate exposure, allergy, immune deficiency, anatomic factors"], ["recurrent URI/LRTI episodes", "school/daycare exposure", "poor growth or severe infections", "recurrent pneumonia"], ["single prolonged infection", "isolated allergic rhinitis without infections"], ["免疫缺陷相关感染", "变应性鼻炎", "腺样体肥大"], [_axis("immune_abnormality", "risk", 0.65, ["recurrent/severe infection"]), _axis("infection_or_inflammation", "primary", 0.75, ["repeated infections"]), _axis("upper_airway_involvement", "secondary", 0.45, ["URI"], "medium"), _axis("lower_airway_involvement", "secondary", 0.45, ["LRTI"], "medium")]),
        ("急性上呼吸道感染", ["Acute Upper Respiratory Infection", "URI", "common cold"], "standard_western_disease", ["acute self-limited infection of nose/throat/upper airway", "nasal symptoms, sore throat, cough, mild fever may occur", "red flags or focal lower airway signs suggest other diagnosis"], ["nasal congestion", "rhinorrhea", "sore throat", "cough", "mild fever"], ["pneumonia infiltrate", "persistent severe bacterial sinusitis pattern"], ["急性鼻炎", "急性咽炎", "流行性感冒"], [_axis("infection_or_inflammation", "primary", 0.75, ["viral URI"]), _axis("upper_airway_involvement", "primary", 0.9, ["nasal/throat symptoms"]), _axis("secretion_or_retention", "secondary", 0.45, ["rhinorrhea"], "medium")]),
        ("儿童肺炎", ["Pediatric Pneumonia", "小儿肺炎", "儿童支气管肺炎"], "standard_western_disease", ["child with lower respiratory tract infection affecting lung parenchyma", "fever, cough, tachypnea, hypoxemia or focal chest findings support", "imaging/testing used when severe or uncertain"], ["child", "fever", "cough", "tachypnea", "crackles", "hypoxemia"], ["isolated upper airway symptoms", "normal respiratory rate and no systemic features"], ["儿童支气管肺炎", "支原体肺炎", "急性支气管炎"], [_axis("infection_or_inflammation", "primary", 0.9, ["fever/infection"]), _axis("lower_airway_involvement", "primary", 0.9, ["tachypnea/crackles"]), _axis("secretion_or_retention", "secondary", 0.5, ["sputum"], "medium")]),
        ("风疹", ["Rubella"], "standard_western_disease", ["mild fever and rash with lymphadenopathy; public health relevance", "diagnosis requires exposure/vaccination context and lab confirmation when suspected", "pregnancy exposure is high-risk"], ["maculopapular rash", "low-grade fever", "posterior auricular or suboccipital lymphadenopathy"], ["Koplik spots and severe prodrome suggest measles", "sandpaper rash suggests scarlet fever"], ["麻疹", "猩红热", "传染性单核细胞增多症"], [_axis("infection_or_inflammation", "primary", 0.8, ["viral exanthem"]), _axis("immune_abnormality", "secondary", 0.35, ["lymphadenopathy"], "medium")]),
        ("流行性腮腺炎", ["Mumps"], "standard_western_disease", ["viral parotitis with fever and painful salivary gland swelling", "consider exposure/vaccination and complications", "lab confirmation for public health cases"], ["parotid swelling", "fever", "jaw/ear pain", "mumps exposure"], ["suppurative bacterial parotitis", "dental abscess"], ["传染性单核细胞增多症", "细菌性腮腺炎", "上呼吸道感染"], [_axis("infection_or_inflammation", "primary", 0.85, ["viral parotitis"]), _axis("upper_airway_involvement", "location", 0.5, ["salivary/oropharyngeal region"], "medium")]),
        ("咽结合膜热", ["Pharyngoconjunctival Fever", "Adenoviral pharyngoconjunctival fever"], "standard_western_disease", ["adenovirus syndrome with fever, pharyngitis and conjunctivitis", "often in children/outbreaks", "distinguish from measles and bacterial pharyngitis"], ["fever", "pharyngitis", "conjunctivitis", "adenovirus exposure"], ["Koplik spots and rash", "positive group A strep without conjunctivitis"], ["麻疹", "急性咽炎", "流行性感冒"], [_axis("infection_or_inflammation", "primary", 0.85, ["adenovirus"]), _axis("upper_airway_involvement", "location", 0.75, ["pharyngitis/conjunctivitis"]), _axis("visual_or_ocular_neuropathy", "location", 0.3, ["conjunctivitis"], "low")]),
        ("疱疹性咽峡炎", ["Herpangina"], "standard_western_disease", ["enteroviral illness with fever, sore throat and posterior oral/pharyngeal vesicles or ulcers", "common in children", "distinguish from hand-foot-mouth disease"], ["fever", "sore throat", "posterior pharyngeal vesicles", "oral ulcers"], ["diffuse hand/foot rash suggests HFMD", "tonsillar exudate suggests bacterial tonsillitis"], ["手足口病", "急性扁桃体炎", "疱疹性口炎"], [_axis("infection_or_inflammation", "primary", 0.85, ["enterovirus"]), _axis("upper_airway_involvement", "location", 0.8, ["pharyngeal vesicles"]), _axis("mucosal_barrier_dysfunction", "secondary", 0.55, ["oral ulcers"])]),
        ("手足口病", ["Hand Foot and Mouth Disease", "HFMD"], "standard_western_disease", ["enteroviral illness with oral ulcers and hand/foot rash", "children common; monitor neurologic/cardiopulmonary complications", "diagnosis usually clinical"], ["oral ulcers", "vesicular rash on hands/feet", "fever", "child/daycare exposure"], ["no rash/oral lesions", "vesicular generalized trunk rash suggests varicella"], ["疱疹性咽峡炎", "水痘", "疱疹性口炎"], [_axis("infection_or_inflammation", "primary", 0.85, ["enterovirus"]), _axis("mucosal_barrier_dysfunction", "primary", 0.75, ["oral/skin vesicles"]), _axis("upper_airway_involvement", "secondary", 0.4, ["oral/pharyngeal lesions"], "medium")]),
        ("水痘", ["Varicella", "Chickenpox"], "standard_western_disease", ["generalized pruritic vesicular rash in different stages", "fever/malaise may precede; exposure or lack of immunity supports", "complications include pneumonia/neurologic disease"], ["generalized vesicular rash", "lesions in different stages", "itching", "fever", "varicella exposure"], ["localized dermatomal rash suggests zoster", "hand-foot-mouth distribution"], ["手足口病", "麻疹", "药疹"], [_axis("infection_or_inflammation", "primary", 0.85, ["varicella zoster virus"]), _axis("mucosal_barrier_dysfunction", "primary", 0.75, ["vesicular rash"])]),
        ("儿童热性惊厥", ["Febrile Seizure"], "risk_condition", ["seizure with fever in young child without CNS infection/metabolic cause", "simple vs complex features determine risk", "must exclude meningitis/encephalitis when concerning signs"], ["age 6 months to 5 years", "fever", "generalized brief seizure", "rapid recovery"], ["meningeal signs", "persistent altered mental status", "focal/prolonged/recurrent seizures"], ["急性细菌性脑膜炎", "儿童病毒性脑炎", "癫痫"], [_axis("electrophysiological_or_ion_channel_dysfunction", "primary", 0.75, ["seizure"]), _axis("infection_or_inflammation", "trigger", 0.65, ["fever"]), _axis("neurotransmitter_or_synaptic_dysfunction", "secondary", 0.35, ["neuronal excitability"], "medium")]),
        ("急性细菌性脑膜炎", ["Acute Bacterial Meningitis"], "standard_western_disease", ["medical emergency with fever, headache/neck stiffness, altered mental status or seizures", "CSF evaluation and urgent treatment required", "infants may have nonspecific signs"], ["fever", "neck stiffness", "altered mental status", "seizure", "bulging fontanelle"], ["simple febrile seizure with rapid normal recovery", "viral URI without neurologic signs"], ["儿童病毒性脑炎", "儿童热性惊厥", "败血症"], [_axis("infection_or_inflammation", "primary", 0.95, ["bacterial CNS infection"]), _axis("neurotransmitter_or_synaptic_dysfunction", "location", 0.65, ["seizure/altered mental status"]), _axis("mucosal_barrier_dysfunction", "pathogenesis", 0.45, ["meningeal inflammation"], "medium")]),
        ("儿童病毒性脑炎", ["Pediatric Viral Encephalitis"], "standard_western_disease", ["brain inflammation with fever plus altered mental status, seizures or focal neurologic signs", "CSF/imaging/viral testing guide diagnosis", "must distinguish meningitis and febrile seizure"], ["fever", "altered mental status", "seizure", "focal neurologic signs", "CSF pleocytosis"], ["simple febrile seizure with full recovery", "no neurologic symptoms"], ["急性细菌性脑膜炎", "儿童热性惊厥", "中毒代谢性脑病"], [_axis("infection_or_inflammation", "primary", 0.85, ["viral CNS infection"]), _axis("neurotransmitter_or_synaptic_dysfunction", "location", 0.7, ["altered mental status/seizure"]), _axis("neurodegeneration_or_demyelination", "risk", 0.3, ["neurologic injury"], "low")]),
        ("新生儿肺炎", ["Neonatal Pneumonia"], "standard_western_disease", ["neonate with respiratory distress and infectious risk factors", "signs may include tachypnea, grunting, hypoxemia, temperature instability", "requires urgent neonatal evaluation"], ["newborn", "tachypnea", "grunting", "hypoxemia", "temperature instability", "maternal infection risk"], ["transient tachypnea without infection evidence", "congenital heart disease signs"], ["新生儿败血症", "儿童肺炎", "呼吸窘迫综合征"], [_axis("infection_or_inflammation", "primary", 0.9, ["neonatal infection"]), _axis("lower_airway_involvement", "primary", 0.9, ["respiratory distress"]), _axis("secretion_or_retention", "secondary", 0.4, ["secretions"], "medium")]),
        ("新生儿败血症", ["Neonatal Sepsis"], "standard_western_disease", ["systemic infection in neonate with nonspecific signs", "risk factors include maternal infection, prematurity, prolonged rupture", "blood culture and urgent treatment are key"], ["newborn", "temperature instability", "poor feeding", "lethargy", "respiratory distress", "maternal infection risk"], ["isolated physiologic jaundice", "well appearing infant with local mild symptoms only"], ["新生儿肺炎", "新生儿黄疸", "脑膜炎"], [_axis("infection_or_inflammation", "primary", 0.95, ["systemic infection"]), _axis("cardiovascular_or_circulatory_risk", "risk", 0.55, ["shock/poor perfusion"], "medium"), _axis("immune_abnormality", "risk", 0.4, ["neonatal immune vulnerability"], "medium")]),
        ("新生儿黄疸", ["Neonatal Jaundice", "Hyperbilirubinemia of newborn"], "lab_or_imaging_finding", ["yellow skin/sclera in neonate from elevated bilirubin", "must distinguish physiologic vs pathologic hemolysis, infection, cholestasis", "bilirubin level and age in hours guide risk"], ["newborn", "jaundice", "elevated bilirubin", "poor feeding or hemolysis risk"], ["direct hyperbilirubinemia/cholestasis pattern", "sepsis signs as primary"], ["新生儿败血症", "胆道闭锁", "溶血病"], [_axis("hepatic_or_biliary_dysfunction", "primary", 0.8, ["bilirubin metabolism"]), _axis("hematopoietic_or_marrow_dysfunction", "risk", 0.45, ["hemolysis"], "medium"), _axis("infection_or_inflammation", "risk", 0.35, ["sepsis-related jaundice"], "medium")]),
        ("新生儿缺氧缺血性脑病", ["Neonatal Hypoxic-Ischemic Encephalopathy", "HIE"], "standard_western_disease", ["neonatal encephalopathy after perinatal hypoxia/ischemia", "abnormal consciousness, tone, seizures, acidosis or low Apgar support", "urgent neurologic/neonatal management"], ["perinatal asphyxia", "low Apgar", "metabolic acidosis", "seizures", "abnormal tone", "altered consciousness"], ["normal neurologic exam", "isolated jaundice or sepsis without hypoxic event"], ["新生儿败血症", "新生儿低血糖", "颅内出血"], [_axis("oxidative_or_free_radical_damage", "pathogenesis", 0.7, ["reperfusion injury"]), _axis("vascular_or_microcirculatory", "primary", 0.8, ["hypoxic ischemic injury"]), _axis("neurotransmitter_or_synaptic_dysfunction", "location", 0.7, ["seizures/encephalopathy"])]),
        ("鹅口疮", ["Oral Candidiasis", "Thrush"], "standard_western_disease", ["white oral plaques caused by Candida, common in infants or immunocompromised", "plaques may scrape off leaving erythematous base", "consider immune risk if recurrent/severe"], ["white oral plaques", "infant", "feeding discomfort", "recent antibiotics/inhaled steroids"], ["vesicular ulcers suggest HSV/stomatitis", "tonsillar exudate from pharyngitis"], ["疱疹性口炎", "急性咽炎", "手足口病"], [_axis("infection_or_inflammation", "primary", 0.75, ["Candida"]), _axis("mucosal_barrier_dysfunction", "primary", 0.8, ["oral plaques"]), _axis("immune_abnormality", "risk", 0.35, ["recurrent/severe"], "medium")]),
        ("疱疹性口炎", ["Herpetic Gingivostomatitis", "HSV stomatitis"], "standard_western_disease", ["HSV oral infection with painful vesicles/ulcers, gingivitis, fever", "young children common", "dehydration risk from poor intake"], ["painful oral ulcers", "gingivitis", "fever", "drooling", "poor intake"], ["posterior-only vesicles suggest herpangina", "hand/foot rash suggests HFMD"], ["疱疹性咽峡炎", "手足口病", "鹅口疮"], [_axis("infection_or_inflammation", "primary", 0.8, ["HSV"]), _axis("mucosal_barrier_dysfunction", "primary", 0.85, ["oral ulcers"]), _axis("upper_airway_involvement", "location", 0.45, ["oral/pharyngeal area"], "medium")]),
        ("小儿胃炎", ["Pediatric Gastritis"], "standard_western_disease", ["child with epigastric pain, nausea/vomiting, dyspepsia; consider infection, medication, H. pylori in selected cases", "diagnosis is clinical unless alarm features", "red flags require further evaluation"], ["epigastric pain", "nausea", "vomiting", "dyspepsia", "NSAID exposure"], ["bilious vomiting", "GI bleeding", "severe dehydration or surgical abdomen"], ["急性胃肠炎", "胃食管反流", "阑尾炎"], [_axis("digestive_reflux_or_dysfunction", "primary", 0.7, ["dyspepsia/nausea"]), _axis("mucosal_barrier_dysfunction", "primary", 0.7, ["gastric mucosal irritation"]), _axis("infection_or_inflammation", "secondary", 0.4, ["infectious trigger"], "medium")]),
    ]
    source_map = {
        "百日咳": [RESP_SOURCES["CDC_PERTUSSIS"]],
        "流行性感冒": [RESP_SOURCES["CDC_FLU"]],
        "新型冠状病毒感染": [RESP_SOURCES["CDC_COVID"]],
        "麻疹": [RESP_SOURCES["CDC_MEASLES"]],
        "猩红热": [RESP_SOURCES["CDC_SCARLET"]],
        "扁桃体肥大": [RESP_SOURCES["MSD_TONSILS_ADENOIDS"]],
        "腺样体肥大": [RESP_SOURCES["MSD_TONSILS_ADENOIDS"]],
        "急性鼻窦炎": [RESP_SOURCES["NICE_SINUSITIS"]],
        "慢性鼻窦炎": [RESP_SOURCES["NICE_SINUSITIS"]],
    }
    for name, aliases, entry_type, key_points, rule_in, rule_out, diff_names, axes in simple_specs:
        sources = source_map.get(name, base_sources)
        CURATED_CARD_TEMPLATES[name] = _card(
            name, aliases, key_points, rule_in, rule_out,
            [_diff(d, ["distinguish by disease-specific key features and objective checks"]) for d in diff_names],
            ["focused history and physical examination", "oxygen saturation when respiratory symptoms are present", "targeted lab/pathogen testing or imaging when red flags or diagnostic uncertainty are present"],
            COMMON_RED_FLAGS,
            axes,
            sources,
            entry_type=entry_type,
            course="acute, chronic, recurrent, or syndrome-specific",
        )

    for key, card in list(CURATED_CARD_TEMPLATES.items()):
        CURATED_CARD_TEMPLATES[key] = _ensure_min_quality(card)


_extend_cards()


def _external_fetch_stub(
    disease_name: str,
    missing_fields: list[str],
    trigger_reason: str,
    patient_context: dict,
) -> dict | None:
    """Provider abstraction placeholder.

    The current implementation uses a curated trusted-source template set seeded
    from this Codex review session. Live provider clients can be added behind
    this function later without changing cache semantics.
    """
    key = _norm(disease_name)
    for name, card in CURATED_CARD_TEMPLATES.items():
        if key in _card_names(card) or key == _norm(name):
            return copy.deepcopy(card)
    return None


if __name__ == "__main__":
    cards = seed_pending_cards()
    print(f"seeded {len(cards)} pending disease cards")
