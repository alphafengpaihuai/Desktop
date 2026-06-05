"""Offline review for mined pharmacology temp results.

No PubMed calls are made here. The script:
1. Builds data/m1_name_mapping_candidates.json for missing English names.
2. Adds evidence_grade to current high-confidence temp results for manual audit.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from agent_pharmacology_miner import (
    TEMP_OUTPUT,
    clean_disease_label,
    has_cjk,
    load_json,
    resolve_pubmed_disease_mapping,
    valid_english,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = ROOT / "data" / "m1_name_mapping_candidates.json"


SPECIAL_MAPPING_CANDIDATES = {
    "IgA血管炎/过敏性紫癜": {
        "suggested_en_name": "IgA Vasculitis",
        "mapping_confidence": "high",
        "source": "existing_alias_patch",
        "reason": "等价别名包括 Henoch-Schonlein purpura / Henoch-Schönlein purpura；需人工确认后写入正式 mapping。",
    },
    "不稳定型心绞痛": {
        "suggested_en_name": "Unstable Angina",
        "mapping_confidence": "high",
        "source": "existing_alias_patch",
        "reason": "本地 alias patch 已有可靠英文标准名。",
    },
    "变应性接触性皮炎": {
        "suggested_en_name": "Allergic Contact Dermatitis",
        "mapping_confidence": "high",
        "source": "existing_alias_patch",
        "reason": "本地 alias patch 已有可靠英文标准名。",
    },
    "儿童急性腹泻": {
        "suggested_en_name": "Acute Gastroenteritis",
        "mapping_confidence": "medium",
        "source": "manual_needed",
        "reason": "需确认当前 anchor 是急性腹泻症状还是急性胃肠炎诊断；备选为 Acute Diarrhea in Children。",
    },
    "中毒": {
        "suggested_en_name": "Poisoning",
        "mapping_confidence": "low",
        "source": "manual_needed",
        "reason": "病名过于宽泛，建议按具体中毒类型拆分后再用于 PubMed mining。",
    },
    "Hunt综合征": {
        "suggested_en_name": "Ramsay Hunt syndrome",
        "mapping_confidence": "medium",
        "source": "manual_needed",
        "reason": "Hunt 不可用；需确认是否指 Ramsay Hunt syndrome。",
    },
    "Vogt-小柳-原田综合征": {
        "suggested_en_name": "Vogt-Koyanagi-Harada disease",
        "mapping_confidence": "high",
        "source": "manual_needed",
        "reason": "常用别名 Vogt-Koyanagi-Harada syndrome；需人工确认中文 anchor。",
    },
    "丹毒】 【erysipelas": {
        "clean_zh_name": "丹毒",
        "suggested_en_name": "Erysipelas",
        "mapping_confidence": "high",
        "source": "string_cleanup",
        "reason": "原始中文名存在解析污染，应清洗后映射。",
    },
}


def main() -> None:
    temp = load_json(TEMP_OUTPUT, {"results": {}})
    results = temp.get("results", {}) if isinstance(temp, dict) else {}

    candidates = build_mapping_candidates(results)
    CANDIDATES_PATH.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    grade_counter = add_evidence_grades(results)
    temp["results"] = results
    temp.setdefault("metadata", {})["evidence_grade_review"] = {
        "reviewed_high_confidence_count": sum(grade_counter.values()),
        "grade_distribution": dict(grade_counter),
        "policy": "Network pharmacology/molecular docking alone is not graded high.",
    }
    TEMP_OUTPUT.write_text(json.dumps(temp, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "mapping_candidates_path": str(CANDIDATES_PATH.relative_to(ROOT)),
        "mapping_candidates_count": len(candidates),
        "mapping_confidence_distribution": dict(Counter(item["mapping_confidence"] for item in candidates)),
        "evidence_grade_distribution": dict(grade_counter),
    }, ensure_ascii=False, indent=2))


def build_mapping_candidates(results: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for zh_name, result in results.items():
        if not isinstance(result, dict) or result.get("failure_type") != "missing_pubmed_name":
            continue
        if zh_name in seen:
            continue
        seen.add(zh_name)
        candidates.append(build_candidate(zh_name))
    return candidates


def build_candidate(zh_name: str) -> dict[str, str]:
    cleaned = clean_disease_label(zh_name)
    base = {
        "zh_name": zh_name,
        "clean_zh_name": cleaned,
        "current_en_name": "",
        "suggested_en_name": "",
        "mapping_confidence": "low",
        "source": "manual_needed",
        "reason": "当前 resolver 无可靠非中文英文名；需人工审核。",
        "status": "pending_review",
    }

    special = SPECIAL_MAPPING_CANDIDATES.get(zh_name)
    if special:
        base.update(special)
        base["clean_zh_name"] = special.get("clean_zh_name", cleaned)
        return base

    resolved = resolve_pubmed_disease_mapping(zh_name)
    suggested = valid_english(resolved.get("suggested_en_name", ""))
    if suggested:
        base.update({
            "suggested_en_name": suggested,
            "mapping_confidence": "high" if resolved.get("source") in {"existing_mapping", "existing_alias_patch"} else "medium",
            "source": resolved.get("source", "existing_mapping"),
            "reason": resolved.get("reason", "local mapping candidate"),
        })
    return base


def add_evidence_grades(results: dict[str, Any]) -> Counter:
    counter: Counter[str] = Counter()
    for result in results.values():
        if not isinstance(result, dict) or result.get("evidence_confidence") != "High":
            continue
        grade = grade_result(result)
        result["evidence_grade"] = grade
        result["evidence_grade_reason"] = grade_reason(result, grade)
        counter[grade] += 1
    return counter


def grade_result(result: dict[str, Any]) -> str:
    titles = collect_titles(result)
    text = " ".join(titles).lower()
    if any(term in text for term in ["randomized controlled trial", "clinical trial", "systematic review"]):
        return "high"
    if "meta-analysis" in text and not any(term in text for term in ["network pharmacology", "molecular docking"]):
        return "high"
    if any(term in text for term in ["network pharmacology", "molecular docking"]):
        return "low_medium"
    if any(term in text for term in ["rats", "rat ", "mice", "mouse", "induced", "model"]):
        return "medium_high"
    if any(term in text for term in ["cell", "cells", "cytotoxicity", "proliferation", "invasion", "migration"]):
        return "medium"
    if any(term in text for term in ["review", "mechanism", "mechanisms"]):
        return "medium"
    return "low"


def grade_reason(result: dict[str, Any], grade: str) -> str:
    titles = collect_titles(result)
    joined = " | ".join(titles[:3])
    if grade == "high":
        return f"标题提示临床研究/RCT/系统综述证据：{joined}"
    if grade == "medium_high":
        return f"标题提示动物实验或明确疾病模型，不能写成临床有效：{joined}"
    if grade == "medium":
        return f"标题提示机制/细胞/综述证据，外推有限：{joined}"
    if grade == "low_medium":
        return f"网络药理/分子对接或少量实验验证，不可单独作为 high：{joined}"
    return f"标题相关但证据链不足：{joined}"


def collect_titles(result: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for herb in result.get("evidence_based_herbs", []):
        if not isinstance(herb, dict):
            continue
        for title in herb.get("evidence_titles", []):
            if isinstance(title, str) and title not in titles:
                titles.append(title)
    return titles


if __name__ == "__main__":
    main()
