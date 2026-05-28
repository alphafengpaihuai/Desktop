"""
Parallel RAG pipeline for Shou Yi.

This module implements the new orchestration shape without changing the
existing medical knowledge base:

Patient input
  -> Track A: western differential / red-line risk extraction
  -> Track B: local TCM syndrome semantic retrieval
  -> Cross-check and rerank
  -> strict five-line clinical output
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable


LLMExtractor = Callable[[dict], dict]
LLMReranker = Callable[[dict], dict]


@dataclass
class PatientInput:
    chief_complaint: str = ""
    symptoms: list[str] = field(default_factory=list)
    age: str = ""
    gender: str = ""
    history: str = ""
    current_medications: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [
            self.chief_complaint,
            " ".join(self.symptoms),
            self.history,
            " ".join(self.current_medications),
        ]
        return " ".join(p for p in parts if p).strip()


@dataclass
class SyndromePlan:
    syndrome: str
    formula_name: str
    treatment_principle: str = ""
    core_herbs: list[str] = field(default_factory=list)
    symptom_keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    contraindications: list[str] = field(default_factory=list)
    source: str = "local_rag"


class LocalSyndromeKnowledgeBase:
    """Local-only TCM syndrome/formula retrieval with fuzzy semantic matching."""

    SYNONYMS = {
        "咳嗽": ["咳", "咳嗽", "咳喘", "夜咳", "阵咳"],
        "喘": ["喘", "喘息", "气喘", "哮鸣", "胸闷"],
        "鼻塞": ["鼻塞", "鼻堵", "不通气"],
        "流涕": ["流涕", "鼻涕", "流鼻涕"],
        "痰": ["痰", "咳痰", "有痰", "痰多"],
        "发热": ["发热", "发烧", "高热", "低热"],
        "寒": ["怕冷", "恶寒", "清涕", "白痰"],
        "热": ["咽痛", "黄涕", "黄痰", "口干", "苔黄"],
        "抽动": ["眨眼", "努嘴", "耸肩", "抽动", "秽语"],
    }

    def __init__(self, json_path: str | Path | None = None, fallback_plans: dict[str, dict] | None = None):
        self.json_path = Path(json_path) if json_path else None
        self.plans = self._load_plans(fallback_plans or {})

    def retrieve(self, symptom_clusters: list[str], top_k: int = 5) -> list[dict]:
        query_tokens = expand_terms(symptom_clusters, self.SYNONYMS)
        scored = []
        for plan in self.plans:
            plan_terms = (
                plan.symptom_keywords
                + plan.aliases
                + [plan.syndrome, plan.formula_name, plan.treatment_principle]
            )
            plan_tokens = expand_terms(plan_terms, self.SYNONYMS)
            score = semantic_score(query_tokens, plan_tokens)
            if score > 0:
                scored.append({
                    "syndrome": plan.syndrome,
                    "formula_name": plan.formula_name,
                    "treatment_principle": plan.treatment_principle,
                    "core_herbs": plan.core_herbs,
                    "contraindications": plan.contraindications,
                    "score": round(score, 4),
                    "source": plan.source,
                    "reference_only": False,
                })
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _load_plans(self, fallback_plans: dict[str, dict]) -> list[SyndromePlan]:
        raw_items: list[dict] = []
        if self.json_path and self.json_path.exists():
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            raw_items = data if isinstance(data, list) else list(data.values())
        elif fallback_plans:
            for disease_name, value in fallback_plans.items():
                raw_items.append({
                    "syndrome": disease_name,
                    "formula_name": value.get("main_formula", ""),
                    "treatment_principle": value.get("note", ""),
                    "core_herbs": value.get("base_herbs", []),
                    "symptom_keywords": list(value.get("rag_additions", {}).keys()),
                    "aliases": [disease_name],
                    "contraindications": value.get("contraindications", []),
                })

        plans = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            plans.append(SyndromePlan(
                syndrome=str(item.get("syndrome") or item.get("syndrome_name") or item.get("name") or ""),
                formula_name=str(item.get("formula_name") or item.get("main_formula") or ""),
                treatment_principle=str(item.get("treatment_principle") or item.get("treatment") or ""),
                core_herbs=list(item.get("core_herbs") or item.get("base_herbs") or []),
                symptom_keywords=list(item.get("symptom_keywords") or item.get("symptoms") or []),
                aliases=list(item.get("aliases") or []),
                contraindications=list(item.get("contraindications") or []),
                source=str(item.get("source") or "local_rag"),
            ))
        return [p for p in plans if p.syndrome and p.formula_name]


class ParallelRAGPipeline:
    def __init__(
        self,
        syndrome_kb: LocalSyndromeKnowledgeBase,
        track_a_extractor: LLMExtractor | None = None,
        track_b_extractor: LLMExtractor | None = None,
        reranker: LLMReranker | None = None,
    ):
        self.syndrome_kb = syndrome_kb
        self.track_a_extractor = track_a_extractor
        self.track_b_extractor = track_b_extractor
        self.reranker = reranker

    def run(self, patient: PatientInput | dict) -> dict:
        patient = patient if isinstance(patient, PatientInput) else PatientInput(**patient)
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(self._run_track_a, patient)
            future_b = executor.submit(self._run_track_b, patient)
            track_a = future_a.result()
            track_b = future_b.result()

        checked = self._cross_check_and_rerank(patient, track_a, track_b)
        return {
            "track_a": track_a,
            "track_b": track_b,
            "reranked_candidates": checked["reranked_candidates"],
            "selected_candidate": checked["selected_candidate"],
            "blocked": checked["blocked"],
            "warnings": checked["warnings"],
            "five_line_output": format_five_line_output(patient, track_a, checked),
            "trace": {
                "parallel_routing": True,
                "track_a_scope": "western_differential_and_safety_only",
                "track_b_scope": "local_tcm_rag_only",
                "semantic_match": "synonym_expansion + char_ngram_jaccard + sequence_ratio",
                "strict_output_lines": 5,
            },
        }

    def _run_track_a(self, patient: PatientInput) -> dict:
        if self.track_a_extractor:
            extracted = self.track_a_extractor({"patient_text": patient.text, "patient": patient.__dict__})
        else:
            extracted = default_western_risk_extractor(patient)
        return {
            "differential_diagnoses": list(extracted.get("differential_diagnoses", []))[:5],
            "risk_flags": list(extracted.get("risk_flags", [])),
            "contraindicated_herbs": list(extracted.get("contraindicated_herbs", [])),
            "safety_level": extracted.get("safety_level", "observe"),
            "warning": extracted.get("warning", ""),
            "trace": {
                "track": "A",
                "local_tcm_rag_accessed": False,
                "allowed_knowledge": "modern_medicine_llm_or_online",
            },
        }

    def _run_track_b(self, patient: PatientInput) -> dict:
        if self.track_b_extractor:
            extracted = self.track_b_extractor({"patient_text": patient.text, "patient": patient.__dict__})
            clusters = list(extracted.get("symptom_clusters", []))
        else:
            clusters = default_symptom_cluster_extractor(patient)
        candidates = self.syndrome_kb.retrieve(clusters, top_k=5)
        return {
            "symptom_clusters": clusters,
            "syndrome_candidates": candidates,
            "trace": {
                "track": "B",
                "local_rag_only": True,
                "online_search_allowed": False,
                "general_model_formula_generation_allowed": False,
            },
        }

    def _cross_check_and_rerank(self, patient: PatientInput, track_a: dict, track_b: dict) -> dict:
        candidates = []
        warnings = []
        blocked = False
        contraindicated = set(track_a.get("contraindicated_herbs", []))

        for candidate in track_b.get("syndrome_candidates", []):
            candidate = dict(candidate)
            herbs = set(candidate.get("core_herbs", []))
            hit_herbs = sorted(herbs & contraindicated)
            candidate["contraindication_hits"] = hit_herbs
            candidate["blocked_by_track_a"] = bool(hit_herbs)
            candidate["score_after_safety"] = candidate["score"] * (0.2 if hit_herbs else 1.0)
            if hit_herbs:
                warnings.append(f"{candidate['formula_name']}命中西医禁忌: {','.join(hit_herbs)}")
            candidates.append(candidate)

        candidates = self._rerank(patient, candidates)
        selected = next((c for c in candidates if not c.get("blocked_by_track_a")), candidates[0] if candidates else None)
        if selected and selected.get("blocked_by_track_a"):
            blocked = True
        return {
            "reranked_candidates": candidates,
            "selected_candidate": selected,
            "blocked": blocked,
            "warnings": warnings,
        }

    def _rerank(self, patient: PatientInput, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return []
        if self.reranker:
            llm_result = self.reranker({"patient_text": patient.text, "candidates": candidates})
            weights = llm_result.get("weights", {}) if isinstance(llm_result, dict) else {}
            for candidate in candidates:
                if candidate["syndrome"] in weights:
                    candidate["rerank_weight"] = float(weights[candidate["syndrome"]])
                else:
                    candidate["rerank_weight"] = candidate["score_after_safety"]
        else:
            total = sum(max(c["score_after_safety"], 0.0) for c in candidates) or 1.0
            for candidate in candidates:
                candidate["rerank_weight"] = round(candidate["score_after_safety"] / total, 4)
        candidates.sort(key=lambda item: item["rerank_weight"], reverse=True)
        return candidates[:3]


def default_western_risk_extractor(patient: PatientInput) -> dict:
    text = patient.text
    differentials = []
    risk_flags = []
    contraindicated_herbs = []

    if any(x in text for x in ["喘", "哮鸣", "胸闷"]):
        differentials.extend(["支气管哮喘", "急性支气管炎", "社区获得性肺炎"])
    if any(x in text for x in ["鼻塞", "流涕", "鼻涕"]):
        differentials.extend(["变应性鼻炎", "上气道咳嗽综合征"])
    if "高血压" in text:
        risk_flags.append("高血压")
    if any(x in text for x in ["妊娠", "孕妇", "怀孕"]):
        risk_flags.append("妊娠")
        contraindicated_herbs.extend(["益母草", "川牛膝", "牛膝"])

    return {
        "differential_diagnoses": dedupe(differentials),
        "risk_flags": dedupe(risk_flags),
        "contraindicated_herbs": dedupe(contraindicated_herbs),
        "safety_level": "warning" if risk_flags else "observe",
        "warning": "；".join(risk_flags) if risk_flags else "未见明确西医红线",
    }


def default_symptom_cluster_extractor(patient: PatientInput) -> list[str]:
    tokens = list(patient.symptoms)
    for chunk in re.split(r"[，,。；;\s]+", patient.chief_complaint):
        if chunk:
            tokens.append(chunk)
    return dedupe(tokens)


def expand_terms(terms: Iterable[str], synonyms: dict[str, list[str]]) -> set[str]:
    expanded = set()
    for term in terms:
        t = normalize_text(term)
        if not t:
            continue
        expanded.add(t)
        for key, values in synonyms.items():
            variants = [key] + values
            if any(normalize_text(v) in t or t in normalize_text(v) for v in variants):
                expanded.update(normalize_text(v) for v in variants if normalize_text(v))
    return expanded


def semantic_score(query_terms: set[str], plan_terms: set[str]) -> float:
    if not query_terms or not plan_terms:
        return 0.0
    exact = len(query_terms & plan_terms) / max(len(query_terms), 1)
    fuzzy = max(
        SequenceMatcher(None, q, p).ratio()
        for q in query_terms
        for p in plan_terms
    )
    ngram = char_ngram_jaccard(" ".join(sorted(query_terms)), " ".join(sorted(plan_terms)))
    return (exact * 0.5) + (fuzzy * 0.3) + (ngram * 0.2)


def char_ngram_jaccard(a: str, b: str, n: int = 2) -> float:
    def grams(text: str) -> set[str]:
        text = normalize_text(text)
        if len(text) <= n:
            return {text} if text else set()
        return {text[i:i + n] for i in range(len(text) - n + 1)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def dedupe(items: Iterable[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def format_five_line_output(patient: PatientInput, track_a: dict, checked: dict) -> str:
    selected = checked.get("selected_candidate") or {}
    line1 = patient.chief_complaint or "；".join(patient.symptoms[:4]) or "主诉信息不足"
    line2 = track_a.get("warning") or "未见明确西医红线"
    if checked.get("warnings"):
        line2 = f"{line2}；{checked['warnings'][0]}"
    line3 = selected.get("syndrome") or "本地证型未匹配"
    line4 = selected.get("treatment_principle") or "治法治则待本地库补充"
    herbs = "、".join(selected.get("core_herbs", [])[:8])
    line5 = f"{selected.get('formula_name', '本地方剂未匹配')}：{herbs}" if herbs else selected.get("formula_name", "本地方剂未匹配")
    return "\n".join([line1, line2, line3, line4, line5])
