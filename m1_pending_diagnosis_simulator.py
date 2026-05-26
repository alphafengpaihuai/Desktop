"""Small M1 scoring simulation using pending disease cards."""

from __future__ import annotations

import json
from pathlib import Path

from external_disease_knowledge_fetcher import PENDING_PATH, fetch_or_get_cached_disease_card


WANG_XIYA_PRIORITY = [
    "急性支气管炎",
    "支原体肺炎",
    "变应性鼻炎",
    "上气道咳嗽综合征",
    "咳嗽变异性哮喘",
    "鼻炎哮喘综合征",
    "扁桃体肥大",
    "腺样体肥大",
]


def _tokens(patient_text: str) -> set[str]:
    return {t for t in [
        "鼻塞", "流涕", "鼻涕", "咳嗽", "夜间咳嗽", "夜咳", "阵发性",
        "连续咳嗽", "咳嗽重浊", "有痰", "痰", "扁桃体肥大",
        "双肺呼吸音粗", "支原体", "支原体感染", "嗜酸粒细胞增高",
        "嗜酸", "儿童", "4岁",
    ] if t in patient_text}


def _score_card(card: dict, patient_text: str) -> dict:
    tokens = _tokens(patient_text)
    blob_rule_in = " ".join(card.get("rule_in_features", []))
    blob_axes = json.dumps(card.get("pathology_axes", []), ensure_ascii=False)
    rule_hits = [t for t in tokens if t in blob_rule_in or t in blob_axes]
    axes = [a.get("axis") for a in card.get("pathology_axes", []) if a.get("axis")]
    score = min(100.0, len(rule_hits) * 10 + len(set(axes)) * 4)
    if "支原体" in patient_text and "支原体" in json.dumps(card, ensure_ascii=False):
        score += 25
    if "嗜酸" in patient_text and "allergic_th2_inflammation" in axes:
        score += 18
    if "双肺呼吸音粗" in patient_text and "lower_airway_involvement" in axes:
        score += 15
    if "鼻涕鼻塞" in patient_text and "upper_airway_involvement" in axes:
        score += 15
    return {
        "disease_name": card.get("disease_name"),
        "score": round(score, 1),
        "source": card.get("_source", "pending_cache"),
        "rule_in": card.get("rule_in_features", [])[:6],
        "rule_out": card.get("rule_out_features", [])[:4],
        "missing_checks": card.get("required_checks", [])[:5],
        "covered_axes": axes,
    }


def simulate_wang_xiya() -> dict:
    patient_text = (
        "4岁，女。鼻涕鼻塞咳嗽一周余，晚上咳嗽频繁。"
        "阵发性连续咳嗽，咳嗽重浊有痰，扁桃体肥大。"
        "双肺呼吸音粗。支原体感染，嗜酸粒细胞增高。"
    )
    candidates = [
        _score_card(
            fetch_or_get_cached_disease_card(name, trigger_reason="wang_xiya_simulation"),
            patient_text,
        )
        for name in WANG_XIYA_PRIORITY
    ]
    candidates.sort(key=lambda x: x["score"], reverse=True)
    all_axes = sorted({axis for c in candidates for axis in c["covered_axes"]})
    top_gap_close = len(candidates) > 1 and candidates[1]["score"] >= candidates[0]["score"] * 0.9
    result = {
        "candidate_scores": candidates,
        "patient_pathology_axes": all_axes,
        "uncovered_problem_targets": [
            "upper airway symptoms",
            "lower airway cough/coarse breath sounds",
            "allergic/eosinophilic signal",
            "Mycoplasma/infectious signal",
            "airway hyperreactivity/night cough",
            "secretion or sputum burden",
        ],
        "external_check_required": bool(top_gap_close or "支原体感染" in patient_text),
        "human_review_required": True,
        "selection_reason": "pending cards provide differential candidates; primary should not be copied from user-entered bronchitis.",
    }
    return result


if __name__ == "__main__":
    print(json.dumps(simulate_wang_xiya(), ensure_ascii=False, indent=2))
