"""Conservative web-mining helper for insufficient pharmacology cache entries.

The script is intentionally strict:
- It never edits disease_pharmacology_cache_rebuilt.json.
- It writes progress to data/mined_pharmacology_temp.json every N diseases.
- It only promotes a disease from Insufficient when PubMed evidence supports
  both key targets and evidence-based oral herbs in target-specific searches.
- If evidence is weak, the disease remains Insufficient with an audit reason.

Example:
    python3 scripts/agent_pharmacology_miner.py --limit 5 --dry-run
    python3 scripts/agent_pharmacology_miner.py --limit 5 --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
INPUT_CACHE = ROOT / "data" / "disease_pharmacology_cache_rebuilt.json"
TEMP_OUTPUT = ROOT / "data" / "mined_pharmacology_temp.json"
TRACE_OUTPUT = ROOT / "data" / "mined_pharmacology_trace.jsonl"
FAILED_QUERY_OUTPUT = ROOT / "data" / "mined_pharmacology_failed_queries.jsonl"
COMPONENT_SYNONYM_PATCH_PATH = ROOT / "data" / "pharmacology_component_synonym_patch_breast_cancer.json"
NAME_MAPPING_PATH = ROOT / "m1_name_mapping.json"
DATA_NAME_MAPPING_PATH = ROOT / "data" / "m1_name_mapping.json"
ALIAS_PATCH_PATH = ROOT / "data" / "m1_alias_map_patch.json"
ROOT_ALIAS_MAP_PATH = ROOT / "m1_alias_map.json"
AUX_ALIAS_MAP_PATH = ROOT / "data" / "m1_disease_alias_map.json"

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_SAVE_EVERY = 10
DEFAULT_CONCURRENCY = 3
DEFAULT_TIMEOUT = 20


# Keep this list deliberately small and high-signal. Candidates are only used
# when they appear in PubMed evidence for the disease query.
TARGET_ALIASES: dict[str, list[str]] = {
    "TNF": ["TNF", "TNF-alpha", "tumor necrosis factor"],
    "IL6": ["IL6", "IL-6", "interleukin-6"],
    "IL1B": ["IL1B", "IL-1beta", "interleukin-1 beta"],
    "IL10": ["IL10", "IL-10", "interleukin-10"],
    "TGFB1": ["TGFB1", "TGF-beta", "transforming growth factor beta"],
    "IFNG": ["IFNG", "IFN-gamma", "interferon gamma"],
    "VEGFA": ["VEGFA", "VEGF", "vascular endothelial growth factor"],
    "EGFR": ["EGFR", "epidermal growth factor receptor"],
    "TP53": ["TP53", "p53"],
    "PTGS2": ["PTGS2", "COX-2", "cyclooxygenase-2"],
    "NOS2": ["NOS2", "iNOS", "inducible nitric oxide synthase"],
    "NFKB1": ["NFKB1", "NF-kappaB", "NF-κB"],
    "MAPK1": ["MAPK1", "ERK2"],
    "MAPK3": ["MAPK3", "ERK1"],
    "AKT1": ["AKT1", "protein kinase B"],
    "STAT3": ["STAT3", "signal transducer and activator of transcription 3"],
    "JAK2": ["JAK2", "Janus kinase 2"],
    "ESR1": ["ESR1", "estrogen receptor alpha"],
    "AR": ["androgen receptor", "AR"],
    "INS": ["INS", "insulin"],
    "ACE": ["ACE", "angiotensin-converting enzyme"],
    "ACE2": ["ACE2", "angiotensin-converting enzyme 2"],
    "MMP9": ["MMP9", "matrix metalloproteinase-9"],
    "MMP2": ["MMP2", "matrix metalloproteinase-2"],
    "HIF1A": ["HIF1A", "HIF-1alpha", "hypoxia-inducible factor 1-alpha"],
    "CRP": ["CRP", "C-reactive protein"],
}


# Oral single-herb names with common English/phytochemical anchors. This is a
# recall dictionary, not evidence by itself.
HERB_ALIASES: dict[str, list[str]] = {
    "黄芩": ["Scutellaria baicalensis", "baicalin", "baicalein", "wogonin"],
    "黄连": ["Coptis chinensis", "berberine", "coptisine"],
    "黄芪": ["Astragalus membranaceus", "astragaloside", "astragalus"],
    "丹参": ["Salvia miltiorrhiza", "tanshinone", "salvianolic acid"],
    "赤芍": ["Paeonia lactiflora", "paeoniflorin"],
    "白芍": ["Paeonia lactiflora", "paeoniflorin"],
    "甘草": ["Glycyrrhiza uralensis", "glycyrrhizin", "licorice"],
    "姜黄": ["Curcuma longa", "curcumin", "turmeric"],
    "金银花": ["Lonicera japonica", "chlorogenic acid", "honeysuckle"],
    "连翘": ["Forsythia suspensa", "forsythiaside", "forsythin"],
    "鱼腥草": ["Houttuynia cordata", "houttuynia"],
    "板蓝根": ["Isatis indigotica", "indirubin", "indigo naturalis"],
    "苦参": ["Sophora flavescens", "matrine", "oxymatrine"],
    "三七": ["Panax notoginseng", "notoginsenoside"],
    "人参": ["Panax ginseng", "ginsenoside"],
    "红景天": ["Rhodiola rosea", "salidroside"],
    "银杏叶": ["Ginkgo biloba", "ginkgolide", "bilobalide"],
    "雷公藤": ["Tripterygium wilfordii", "triptolide"],
    "虎杖": ["Polygonum cuspidatum", "resveratrol", "polydatin"],
    "大黄": ["Rheum palmatum", "emodin", "rhein"],
    "枸杞子": ["Lycium barbarum", "lycium"],
    "淫羊藿": ["Epimedium", "icariin"],
    "葛根": ["Pueraria lobata", "puerarin"],
    "山楂": ["Crataegus pinnatifida", "hawthorn"],
}


TCM_QUERY_TERMS = [
    '"Traditional Chinese Medicine"',
    '"Chinese herbal medicine"',
    "herb",
    "phytochemical",
    "natural product",
]

ACTION_TERMS = [
    "inhibit",
    "inhibits",
    "inhibited",
    "inhibition",
    "suppress",
    "suppresses",
    "suppressed",
    "reduce",
    "reduces",
    "reduced",
    "decrease",
    "decreases",
    "decreased",
    "downregulate",
    "down-regulate",
    "downregulated",
    "upregulate",
    "up-regulate",
    "upregulated",
    "regulate",
    "regulated",
    "modulate",
    "modulated",
    "attenuate",
    "attenuated",
    "activate",
    "activated",
    "expression",
    "release",
    "secretion",
]


@dataclass
class PubMedArticle:
    pmid: str
    title: str
    abstract: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.abstract}"


def main() -> None:
    args = parse_args()
    cache = load_json(INPUT_CACHE, {})
    progress = load_json(TEMP_OUTPUT, {"results": {}, "metadata": {}})
    insufficient = [
        (name, entry) for name, entry in cache.items()
        if isinstance(entry, dict) and entry.get("evidence_confidence") == "Insufficient"
    ]
    if args.names_file:
        selected_names = load_selected_names(Path(args.names_file))
        insufficient = [(name, entry) for name, entry in insufficient if name in selected_names]
    if args.offset:
        insufficient = insufficient[args.offset:]
    if args.limit:
        insufficient = insufficient[:args.limit]

    print(f"Input cache: {INPUT_CACHE}")
    print(f"Insufficient diseases selected: {len(insufficient)}")
    print(f"Dry run: {args.dry_run}")
    print(f"Output temp: {TEMP_OUTPUT}")
    print(f"Trace: {TRACE_OUTPUT}")

    results = asyncio.run(process_all(insufficient, args, progress))
    if args.write:
        save_progress(progress, force=True)
    else:
        print("Dry-run complete. Re-run with --write to persist results.")

    promoted = sum(1 for item in results if item.get("evidence_confidence") == "High")
    print(json.dumps({
        "processed": len(results),
        "high_confidence_found": promoted,
        "kept_insufficient": len(results) - promoted,
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine conservative PubMed pharmacology evidence.")
    parser.add_argument("--limit", type=int, default=5, help="Process at most N insufficient diseases.")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N insufficient diseases.")
    parser.add_argument("--names-file", default="", help="Optional JSON list of exact disease names to process.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--save-every", type=int, default=DEFAULT_SAVE_EVERY)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retmax-disease", type=int, default=20)
    parser.add_argument("--retmax-target-herb", type=int, default=8)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--write", action="store_true", help="Persist progress to data/mined_pharmacology_temp.json.")
    args = parser.parse_args()
    if args.write:
        args.dry_run = False
    return args


def load_selected_names(path: Path) -> set[str]:
    if not path.is_absolute():
        path = ROOT / path
    data = load_json(path, [])
    if isinstance(data, list):
        return {str(item).strip() for item in data if str(item).strip()}
    if isinstance(data, dict):
        values = data.get("names", [])
        if isinstance(values, list):
            return {str(item).strip() for item in values if str(item).strip()}
    return set()


async def process_all(
    diseases: list[tuple[str, dict[str, Any]]],
    args: argparse.Namespace,
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(args.concurrency, 1))
    results: list[dict[str, Any]] = []
    processed = 0

    async def run_one(index: int, disease_name: str, entry: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await asyncio.to_thread(mine_one_disease, index, disease_name, entry, args)

    tasks = [
        asyncio.create_task(run_one(index, name, entry))
        for index, (name, entry) in enumerate(diseases, start=1)
    ]
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        processed += 1
        progress.setdefault("results", {})[result["western_disease_name"]] = result
        progress["metadata"] = {
            "updated_at": now_iso(),
            "source": "Agent_Web_Mining",
            "strict_policy": "No verified target-herb PubMed evidence means Insufficient.",
            "processed_in_this_run": processed,
        }
        append_trace(result)
        if not args.dry_run and processed % max(args.save_every, 1) == 0:
            save_progress(progress)
    return results


def mine_one_disease(
    index: int,
    disease_name: str,
    entry: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    print(f"[{index}] Mining {disease_name}", flush=True)
    pubmed_disease_name = ""
    try:
        pubmed_disease_name = resolve_pubmed_disease_name(disease_name)
        if not pubmed_disease_name:
            return insufficient_result(
                disease_name,
                "No reliable English PubMed disease query name.",
                failure_type="missing_pubmed_name",
            )

        disease_query = build_disease_target_query(pubmed_disease_name)
        disease_articles = pubmed_search_fetch(disease_query, args.retmax_disease, args.timeout)
        target_scores = score_targets(disease_articles)
        key_targets = [target for target, _score in target_scores[:5]]
        if len(key_targets) < 3:
            append_failed_query(build_failed_query_record(
                zh_name=disease_name,
                en_name=pubmed_disease_name,
                query_stage="disease_pubmed",
                query=disease_query,
                target_terms=all_target_terms(),
                articles=disease_articles,
                blocked_reason="no_target_or_outcome",
                recommendation_hint="keep_empty",
            ))
            return insufficient_result(
                disease_name,
                "Fewer than 3 PubMed-supported target candidates.",
                disease_articles,
                pubmed_disease_name=pubmed_disease_name,
                failure_type="weak_target_evidence",
            )

        herb_evidence = mine_herbs_for_targets(disease_name, pubmed_disease_name, key_targets, args)
        if not herb_evidence:
            append_failed_query(build_failed_query_record(
                zh_name=disease_name,
                en_name=pubmed_disease_name,
                query_stage="disease_pubmed",
                query=disease_query,
                target_terms=all_target_terms(),
                articles=disease_articles,
                blocked_reason="weak_herb_evidence",
                recommendation_hint="review_query_strategy",
            ))
            return insufficient_result(
                disease_name,
                "No PubMed target-herb evidence found.",
                disease_articles,
                key_targets,
                pubmed_disease_name=pubmed_disease_name,
                failure_type="weak_herb_evidence",
            )

        return {
            "western_disease_name": disease_name,
            "disease_pathway": summarize_pathway(key_targets),
            "key_targets": key_targets[:5],
            "evidence_based_herbs": herb_evidence[:5],
            "evidence_confidence": "High",
            "source": "Agent_Web_Mining",
            "source_records": {
                "disease_target_query": disease_query,
                "pubmed_disease_name": pubmed_disease_name,
                "disease_pmids": [article.pmid for article in disease_articles[:10]],
                "target_scores": [{"target": t, "score": s} for t, s in target_scores[:10]],
            },
            "mining_policy": "Targets and herbs require PubMed co-occurrence evidence; no inference-only additions.",
            "mined_at": now_iso(),
        }
    except Exception as exc:
        return {
            "western_disease_name": disease_name,
            "disease_pathway": "",
            "key_targets": [],
            "evidence_based_herbs": [],
            "evidence_confidence": "Insufficient",
            "source": "Agent_Web_Mining",
            "failure_reason": f"{type(exc).__name__}: {exc}",
            "failure_type": "network_or_parser_error",
            "source_records": {
                "pubmed_disease_name": pubmed_disease_name,
            },
            "mined_at": now_iso(),
        }


def build_disease_target_query(disease_name: str) -> str:
    target_terms = " OR ".join(all_target_terms())
    return f'("{disease_name}") AND ({target_terms})'


def all_target_terms() -> list[str]:
    return sorted({alias for aliases in TARGET_ALIASES.values() for alias in aliases})


def resolve_pubmed_disease_name(disease_name: str) -> str:
    resolved = resolve_pubmed_disease_mapping(disease_name)
    return resolved.get("suggested_en_name", "")


def resolve_pubmed_disease_mapping(disease_name: str) -> dict[str, str]:
    """Resolve a local disease label to a conservative English PubMed query.

    Order:
    1. exact / cleaned name in m1_name_mapping.json
    2. exact / cleaned diseaseName_cn or aliases in m1_alias_map_patch.json
    3. auxiliary disease alias maps
    4. non-CJK disease name itself

    No automatic translation is performed.
    """
    raw = str(disease_name or "").strip()
    cleaned = clean_disease_label(raw)
    names_to_try = [raw, cleaned]

    for path in (NAME_MAPPING_PATH, DATA_NAME_MAPPING_PATH):
        mapping = load_json(path, {})
        if not isinstance(mapping, dict):
            continue
        for name in names_to_try:
            mapped = valid_english(mapping.get(name, ""))
            if mapped:
                return resolved_mapping(raw, cleaned, mapped, "existing_mapping", f"exact match in {path.name}")

    alias_patch_hit = resolve_from_alias_patch(names_to_try)
    if alias_patch_hit:
        return alias_patch_hit

    aux_hit = resolve_from_aux_alias_maps(names_to_try)
    if aux_hit:
        return aux_hit

    if raw and not has_cjk(raw):
        return resolved_mapping(raw, cleaned, raw, "non_cjk_input", "input contains no CJK characters")
    return resolved_mapping(raw, cleaned, "", "manual_needed", "no reliable local English mapping")


def resolve_from_alias_patch(names_to_try: list[str]) -> dict[str, str]:
    patch = load_json(ALIAS_PATCH_PATH, [])
    if not isinstance(patch, list):
        return {}
    normalized_try = {normalize_name(name) for name in names_to_try if name}
    for entry in patch:
        if not isinstance(entry, dict):
            continue
        aliases = entry.get("aliases", {}) if isinstance(entry.get("aliases"), dict) else {}
        zh_values = [
            entry.get("diseaseName_cn", ""),
            entry.get("canonical_disease_name", ""),
            *aliases.get("zh_standard", []),
            *aliases.get("zh_common", []),
            *aliases.get("zh_clinical_short", []),
            *aliases.get("possible_misspellings", []),
        ]
        if not normalized_try.intersection(normalize_name(value) for value in zh_values if value):
            continue
        en_values = aliases.get("en_standard", [])
        for en_name in en_values:
            valid = valid_english(en_name)
            if valid:
                return resolved_mapping(
                    names_to_try[0],
                    names_to_try[-1],
                    valid,
                    "existing_alias_patch",
                    "matched m1_alias_map_patch.json en_standard",
                )
    return {}


def resolve_from_aux_alias_maps(names_to_try: list[str]) -> dict[str, str]:
    aux = load_json(AUX_ALIAS_MAP_PATH, {})
    if isinstance(aux, dict):
        for name in names_to_try:
            row = aux.get(name)
            if isinstance(row, dict):
                valid = valid_english(row.get("canonical_disease_name", ""))
                if valid:
                    return resolved_mapping(name, clean_disease_label(name), valid, "existing_aux_alias", "matched data/m1_disease_alias_map.json")
            for key, value in aux.items():
                if not isinstance(value, dict):
                    continue
                aliases = value.get("aliases", [])
                if name in aliases:
                    valid = valid_english(value.get("canonical_disease_name", ""))
                    if valid:
                        return resolved_mapping(name, clean_disease_label(name), valid, "existing_aux_alias", "matched auxiliary alias list")

    root_alias = load_json(ROOT_ALIAS_MAP_PATH, {})
    if isinstance(root_alias, dict):
        for name in names_to_try:
            mapped = root_alias.get(name) or root_alias.get(name.lower())
            valid = valid_english(mapped)
            if valid:
                return resolved_mapping(name, clean_disease_label(name), valid, "existing_alias_map", "matched root m1_alias_map.json")
    return {}


def resolved_mapping(raw: str, cleaned: str, en_name: str, source: str, reason: str) -> dict[str, str]:
    return {
        "zh_name": raw,
        "clean_zh_name": cleaned,
        "suggested_en_name": en_name,
        "source": source,
        "reason": reason,
    }


def clean_disease_label(name: str) -> str:
    text = str(name or "").strip()
    text = re.sub(r"【[^】]*[A-Za-z][^】]*】", "", text)
    text = re.sub(r"\[[^\]]*[A-Za-z][^\]]*\]", "", text)
    text = re.sub(r"\([^)]*[A-Za-z][^)]*\)", "", text)
    text = re.sub(r"（[^）]*[A-Za-z][^）]*）", "", text)
    text = text.replace("】", " ").replace("【", " ")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+[A-Za-z][A-Za-z -]*$", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip(" ，,;；:：")


def normalize_name(name: str) -> str:
    return clean_disease_label(name).lower().replace(" ", "").replace("-", "")


def valid_english(value: Any) -> str:
    text = str(value or "").strip()
    if not text or has_cjk(text):
        return ""
    if not re.search(r"[A-Za-z]", text):
        return ""
    if len(text) < 4:
        return ""
    return text


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def score_targets(articles: list[PubMedArticle]) -> list[tuple[str, int]]:
    scores: Counter[str] = Counter()
    for article in articles:
        text = article.text
        lower = text.lower()
        for target, aliases in TARGET_ALIASES.items():
            if any(contains_alias(text, alias) for alias in aliases):
                title_bonus = 2 if any(contains_alias(article.title, alias) for alias in aliases) else 0
                scores[target] += 1 + title_bonus
    return scores.most_common()


def mine_herbs_for_targets(
    zh_name: str,
    disease_name: str,
    key_targets: list[str],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    evidence_by_herb: dict[str, dict[str, Any]] = {}
    herb_aliases_map = load_effective_herb_aliases()
    for target in key_targets[:5]:
        aliases = TARGET_ALIASES.get(target, [target])
        target_query = " OR ".join(f'"{alias}"' for alias in aliases)
        herb_query = " OR ".join(TCM_QUERY_TERMS)
        action_query = " OR ".join(ACTION_TERMS[:12])
        query = f'("{disease_name}") AND ({target_query}) AND ({herb_query}) AND ({action_query})'
        articles = pubmed_search_fetch(query, args.retmax_target_herb, args.timeout)
        matched = collect_query_matches(articles, aliases, herb_aliases_map)
        blocked_reason = infer_target_herb_blocked_reason(articles, matched)
        append_failed_query(build_failed_query_record(
            zh_name=zh_name,
            en_name=disease_name,
            query_stage="target_herb",
            query=query,
            target_terms=aliases,
            herb_terms=TCM_QUERY_TERMS,
            component_terms=all_component_terms(herb_aliases_map),
            outcome_terms=ACTION_TERMS[:12],
            articles=articles,
            matched_herb_terms=matched["herb_terms"],
            matched_component_terms=matched["component_terms"],
            matched_target_terms=matched["target_terms"],
            matched_outcome_terms=matched["outcome_terms"],
            blocked_reason=blocked_reason,
            recommendation_hint=recommendation_for_blocked_reason(blocked_reason),
        ))
        for article in articles:
            if not title_matches_disease(article.title, disease_name):
                continue
            if not contains_action_term(article.text):
                continue
            for herb, herb_aliases in herb_aliases_map.items():
                matched_alias = next((alias for alias in herb_aliases if contains_alias(article.text, alias)), "")
                if not matched_alias:
                    continue
                if not any(contains_alias(article.text, alias) for alias in aliases):
                    continue
                item = evidence_by_herb.setdefault(herb, {
                    "herb": herb,
                    "mechanism": "",
                    "targets": [],
                    "evidence_pmids": [],
                    "matched_terms": [],
                    "evidence_titles": [],
                })
                if target not in item["targets"]:
                    item["targets"].append(target)
                if article.pmid not in item["evidence_pmids"]:
                    item["evidence_pmids"].append(article.pmid)
                if matched_alias not in item["matched_terms"]:
                    item["matched_terms"].append(matched_alias)
                if article.title and article.title not in item["evidence_titles"]:
                    item["evidence_titles"].append(article.title)

    results: list[dict[str, Any]] = []
    for item in evidence_by_herb.values():
        if len(item["targets"]) < 2:
            continue
        targets = item["targets"]
        matched = item["matched_terms"]
        item["mechanism"] = f"{'、'.join(matched[:3])} 文献中与 {'、'.join(targets[:3])} 靶点共同出现，需人工复核具体调控方向。"
        results.append(item)
    results.sort(key=lambda item: (len(item["targets"]), len(item["evidence_pmids"])), reverse=True)
    return results


def load_effective_herb_aliases() -> dict[str, list[str]]:
    aliases = {herb: list(values) for herb, values in HERB_ALIASES.items()}
    patch = load_json(COMPONENT_SYNONYM_PATCH_PATH, [])
    if not isinstance(patch, list):
        return aliases
    for row in patch:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "reviewed_candidate":
            continue
        if row.get("scope") != "miner_recognition_only":
            continue
        herb = str(row.get("mapped_herb", "")).strip()
        component = str(row.get("component", "")).strip()
        if not herb or not component:
            continue
        values = aliases.setdefault(herb, [])
        if component not in values:
            values.append(component)
    return aliases


def all_component_terms(herb_aliases_map: dict[str, list[str]] | None = None) -> list[str]:
    source = herb_aliases_map or HERB_ALIASES
    terms: list[str] = []
    for aliases in source.values():
        terms.extend(aliases)
    return sorted(set(terms), key=str.lower)


def collect_query_matches(
    articles: list[PubMedArticle],
    target_aliases: list[str],
    herb_aliases_map: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    aliases_source = herb_aliases_map or HERB_ALIASES
    herb_matches: set[str] = set()
    component_matches: set[str] = set()
    target_matches: set[str] = set()
    outcome_matches: set[str] = set()
    for article in articles:
        text = article.text
        for aliases in aliases_source.values():
            for alias in aliases:
                if contains_alias(text, alias):
                    component_matches.add(alias)
        for herb, aliases in aliases_source.items():
            if any(contains_alias(text, alias) for alias in aliases):
                herb_matches.add(herb)
        for alias in target_aliases:
            if contains_alias(text, alias):
                target_matches.add(alias)
        lower = text.lower()
        for term in ACTION_TERMS[:12]:
            if term in lower:
                outcome_matches.add(term)
    return {
        "herb_terms": sorted(herb_matches),
        "component_terms": sorted(component_matches, key=str.lower),
        "target_terms": sorted(target_matches, key=str.lower),
        "outcome_terms": sorted(outcome_matches, key=str.lower),
    }


def infer_target_herb_blocked_reason(articles: list[PubMedArticle], matches: dict[str, list[str]]) -> str:
    if not articles:
        return "no_hit"
    if not matches["target_terms"] or not matches["outcome_terms"]:
        return "no_target_or_outcome"
    if not matches["herb_terms"]:
        return "no_herb_term"
    if not matches["component_terms"]:
        return "no_component_term"
    return "parser_miss_suspected"


def recommendation_for_blocked_reason(reason: str) -> str:
    if reason == "no_herb_term":
        return "review_herb_synonym"
    if reason == "no_component_term":
        return "review_component_synonym"
    if reason == "parser_miss_suspected":
        return "review_parser"
    if reason == "no_hit":
        return "keep_empty"
    return "review_query_strategy"


def build_failed_query_record(
    *,
    zh_name: str,
    en_name: str,
    query_stage: str,
    query: str,
    target_terms: list[str] | None = None,
    herb_terms: list[str] | None = None,
    component_terms: list[str] | None = None,
    outcome_terms: list[str] | None = None,
    articles: list[PubMedArticle] | None = None,
    matched_herb_terms: list[str] | None = None,
    matched_component_terms: list[str] | None = None,
    matched_target_terms: list[str] | None = None,
    matched_outcome_terms: list[str] | None = None,
    blocked_reason: str = "weak_herb_evidence",
    recommendation_hint: str = "keep_empty",
) -> dict[str, Any]:
    samples = list(articles or [])[:5]
    return {
        "zh_name": zh_name,
        "en_name": en_name,
        "query_stage": query_stage,
        "query": query,
        "target_terms": target_terms or [],
        "herb_terms": herb_terms or [],
        "component_terms": component_terms or [],
        "outcome_terms": outcome_terms or [],
        "pubmed_hit_count": len(articles or []),
        "sample_pmids": [article.pmid for article in samples],
        "sample_titles": [article.title for article in samples],
        "sample_abstract_snippets": [snippet(article.abstract) for article in samples],
        "matched_herb_terms": matched_herb_terms or [],
        "matched_component_terms": matched_component_terms or [],
        "matched_target_terms": matched_target_terms or [],
        "matched_outcome_terms": matched_outcome_terms or [],
        "blocked_reason": blocked_reason,
        "recommendation_hint": recommendation_hint,
        "logged_at": now_iso(),
    }


def snippet(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def contains_alias(text: str, alias: str) -> bool:
    if not text or not alias:
        return False
    if re.fullmatch(r"[A-Z0-9]{2,5}", alias):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text) is not None
    if len(alias) <= 3:
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=re.I) is not None
    return alias.lower() in text.lower()


def contains_action_term(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in ACTION_TERMS)


def title_matches_disease(title: str, disease_name: str) -> bool:
    title_lower = title.lower()
    disease_lower = disease_name.lower()
    if disease_lower in title_lower:
        return True
    tokens = [
        token for token in re.split(r"[^a-z0-9]+", disease_lower)
        if len(token) >= 3 and token not in {"and", "the", "with", "type", "disease", "mellitus"}
    ]
    if not tokens:
        return False
    required = 1 if len(tokens) == 1 else 2
    return sum(1 for token in tokens if token in title_lower) >= required


def pubmed_search_fetch(query: str, retmax: int, timeout: int) -> list[PubMedArticle]:
    ids = pubmed_esearch(query, retmax, timeout)
    if not ids:
        return []
    return pubmed_efetch(ids, timeout)


def pubmed_esearch(query: str, retmax: int, timeout: int) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": str(retmax),
        "sort": "relevance",
    }
    url = f"{NCBI_BASE}/esearch.fcgi?{urllib.parse.urlencode(params)}"
    payload = http_get_json(url, timeout)
    return payload.get("esearchresult", {}).get("idlist", [])


def pubmed_efetch(pmids: list[str], timeout: int) -> list[PubMedArticle]:
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    url = f"{NCBI_BASE}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    text = http_get_text(url, timeout)
    return parse_pubmed_xml(text)


def http_get_json(url: str, timeout: int) -> dict[str, Any]:
    text = http_get_text(url, timeout)
    return json.loads(text)


def http_get_text(url: str, timeout: int) -> str:
    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "ShouYi-M1-PharmacologyMiner/0.1"},
            )
            response.raise_for_status()
            return response.text
        except Exception as requests_error:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "ShouYi-M1-PharmacologyMiner/0.1"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except Exception:
                last_error = requests_error
            if attempt == 3:
                raise last_error
            time.sleep(1.5 * attempt)
    return ""


def parse_pubmed_xml(xml_text: str) -> list[PubMedArticle]:
    articles: list[PubMedArticle] = []
    for block in re.findall(r"<PubmedArticle>(.*?)</PubmedArticle>", xml_text, flags=re.S):
        pmid = first_match(block, r"<PMID[^>]*>(.*?)</PMID>")
        title = strip_xml(first_match(block, r"<ArticleTitle>(.*?)</ArticleTitle>"))
        abstract_parts = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", block, flags=re.S)
        abstract = " ".join(strip_xml(part) for part in abstract_parts)
        if pmid and (title or abstract):
            articles.append(PubMedArticle(pmid=pmid, title=title, abstract=abstract))
    return articles


def first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def strip_xml(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def insufficient_result(
    disease_name: str,
    reason: str,
    disease_articles: list[PubMedArticle] | None = None,
    key_targets: list[str] | None = None,
    pubmed_disease_name: str = "",
    failure_type: str = "insufficient_evidence",
) -> dict[str, Any]:
    return {
        "western_disease_name": disease_name,
        "disease_pathway": "",
        "key_targets": key_targets or [],
        "evidence_based_herbs": [],
        "evidence_confidence": "Insufficient",
        "source": "Agent_Web_Mining",
        "failure_reason": reason,
        "failure_type": failure_type,
        "source_records": {
            "pubmed_disease_name": pubmed_disease_name,
            "disease_pmids": [article.pmid for article in (disease_articles or [])[:10]],
        },
        "mined_at": now_iso(),
    }


def summarize_pathway(targets: list[str]) -> str:
    inflammatory = {"TNF", "IL6", "IL1B", "NFKB1", "PTGS2", "NOS2", "CRP"}
    vascular = {"VEGFA", "ACE", "ACE2", "MMP9", "MMP2"}
    endocrine = {"INS", "ESR1", "AR"}
    if inflammatory.intersection(targets):
        return "炎症免疫通路"
    if vascular.intersection(targets):
        return "血管重塑通路"
    if endocrine.intersection(targets):
        return "内分泌代谢通路"
    return "多靶点病理通路"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_progress(progress: dict[str, Any], *, force: bool = False) -> None:
    TEMP_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = TEMP_OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TEMP_OUTPUT)
    if force:
        print(f"Saved: {TEMP_OUTPUT}")


def append_trace(result: dict[str, Any]) -> None:
    TRACE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_OUTPUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")


def append_failed_query(record: dict[str, Any]) -> None:
    FAILED_QUERY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with FAILED_QUERY_OUTPUT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
