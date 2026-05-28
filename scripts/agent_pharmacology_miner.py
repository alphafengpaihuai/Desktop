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
NAME_MAPPING_PATH = ROOT / "m1_name_mapping.json"

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
            return insufficient_result(
                disease_name,
                "Fewer than 3 PubMed-supported target candidates.",
                disease_articles,
                pubmed_disease_name=pubmed_disease_name,
                failure_type="weak_target_evidence",
            )

        herb_evidence = mine_herbs_for_targets(pubmed_disease_name, key_targets, args)
        if not herb_evidence:
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
    target_terms = " OR ".join(sorted({alias for aliases in TARGET_ALIASES.values() for alias in aliases}))
    return f'("{disease_name}") AND ({target_terms})'


def resolve_pubmed_disease_name(disease_name: str) -> str:
    if has_cjk(disease_name):
        mapping = load_json(NAME_MAPPING_PATH, {})
        mapped = str(mapping.get(disease_name, "")).strip() if isinstance(mapping, dict) else ""
        if mapped and not has_cjk(mapped):
            return mapped
        return ""
    return disease_name.strip()


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


def mine_herbs_for_targets(disease_name: str, key_targets: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    evidence_by_herb: dict[str, dict[str, Any]] = {}
    for target in key_targets[:5]:
        aliases = TARGET_ALIASES.get(target, [target])
        target_query = " OR ".join(f'"{alias}"' for alias in aliases)
        herb_query = " OR ".join(TCM_QUERY_TERMS)
        action_query = " OR ".join(ACTION_TERMS[:12])
        query = f'("{disease_name}") AND ({target_query}) AND ({herb_query}) AND ({action_query})'
        articles = pubmed_search_fetch(query, args.retmax_target_herb, args.timeout)
        for article in articles:
            if not title_matches_disease(article.title, disease_name):
                continue
            if not contains_action_term(article.text):
                continue
            for herb, herb_aliases in HERB_ALIASES.items():
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
