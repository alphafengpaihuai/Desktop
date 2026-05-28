"""Batch-fetch SymMap herb and target mappings for local disease manifest.

This is the full collection script. It uses the verified SymMap parameters:

1. POST /search/ with {"table_name": "Disease", "key": disease_name}
2. POST /related_components/ with {"rrid": Disease_id, "table_name": "Herb"}
3. POST /related_components/ with {"rrid": Disease_id, "table_name": "Gene"}

The script is intentionally defensive: every disease is isolated in a broad
try/except, failures are logged, and successful results are flushed to disk.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


BASE_URL = "http://www.symmap.org"
SEARCH_URL = f"{BASE_URL}/search/"
RELATED_COMPONENTS_URL = f"{BASE_URL}/related_components/"

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data" / "disease_cache"
MANIFEST_PATH = CACHE_DIR / "disease_manifest.json"
OUTPUT_PATH = CACHE_DIR / "symmap_online_mapping.json"
FAILED_LOG_PATH = CACHE_DIR / "symmap_failed.log"

REQUEST_TIMEOUT = 15
SLEEP_SECONDS = 3
SAVE_EVERY_SUCCESS = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
}


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    diseases = load_diseases()
    mapping = load_existing_mapping()
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Loaded diseases: {len(diseases)}")
    print(f"Existing mapped diseases: {len(mapping)}")
    print(f"Output: {OUTPUT_PATH}")
    print(f"Failed log: {FAILED_LOG_PATH}")

    success_since_save = 0
    for index, disease in enumerate(diseases, start=1):
        time.sleep(SLEEP_SECONDS)
        search_key = disease["search_key"]
        search_keys = disease["search_keys"]
        display_name = disease["western_disease"]

        print(f"[{index}/{len(diseases)}] 正在抓取: {display_name} | key={search_key}", flush=True)

        if display_name in mapping and mapping[display_name].get("symmap_disease_id"):
            print(f"  SKIP already mapped: {mapping[display_name].get('symmap_disease_id')}", flush=True)
            continue

        try:
            disease_id = ""
            matched_name = ""
            used_search_key = search_key
            for attempt_index, candidate_key in enumerate(search_keys, start=1):
                print(f"  -> 尝试搜索关键字: {candidate_key}", flush=True)
                disease_id, matched_name = fetch_disease_id(session, candidate_key)
                if disease_id:
                    used_search_key = candidate_key
                    print(f"  ✅ 命中！使用的关键字是: {used_search_key}, ID: {disease_id}", flush=True)
                    break
                if attempt_index < len(search_keys):
                    time.sleep(1)
            if not disease_id:
                print("  ❌ 该疾病所有中英文名均未命中，跳过。", flush=True)
                raise ValueError(f"Disease_id not found from SymMap search keys={search_keys}")

            herbs_rows = fetch_related_rows(session, disease_id, "Herb")
            gene_rows = fetch_related_rows(session, disease_id, "Gene")

            effective_herbs = unique_nonempty(
                row.get("Chinese_name") or row.get("Pinyin_name") or row.get("Latin_name")
                for row in herbs_rows
            )
            molecular_targets = unique_nonempty(
                row.get("Gene_symbol") or row.get("Gene_name") or row.get("Gene_id")
                for row in gene_rows
            )

            mapping[display_name] = {
                "western_disease": display_name,
                "search_key": used_search_key,
                "attempted_search_keys": search_keys,
                "symmap_disease_id": disease_id,
                "symmap_matched_name": matched_name,
                "molecular_targets": molecular_targets,
                "effective_herbs": effective_herbs,
                "target_count": len(molecular_targets),
                "herb_count": len(effective_herbs),
                "target_rows": compact_gene_rows(gene_rows),
                "herb_rows": compact_herb_rows(herbs_rows),
                "evidence_source": "SymMap V2.0 API",
                "fetched_at": now_iso(),
            }
            success_since_save += 1
            print(
                f"  OK {disease_id}: targets={len(molecular_targets)} herbs={len(effective_herbs)}",
                flush=True,
            )

            if success_since_save >= SAVE_EVERY_SUCCESS:
                save_mapping(mapping)
                success_since_save = 0

        except Exception as exc:
            message = f"[{index}/{len(diseases)}] {display_name} | key={search_key} | {type(exc).__name__}: {exc}"
            print(f"  Warning: {message}", flush=True)
            append_failed_log(message)
            continue

    save_mapping(mapping)
    print(f"Done. Total mapped diseases: {len(mapping)}")


def load_diseases() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in manifest.get("diseases", []):
        standard = str(item.get("standard_western_name", "")).strip()
        aliases = [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()]
        search_key = standard or (aliases[0] if aliases else "")
        if not search_key:
            continue
        display_name = standard or search_key
        if display_name in seen:
            continue
        seen.add(display_name)
        result.append({
            "western_disease": display_name,
            "search_key": search_key,
            "search_keys": unique_nonempty([search_key, *aliases]),
        })
    return result


def load_existing_mapping() -> dict[str, Any]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        backup = OUTPUT_PATH.with_suffix(f".corrupt.{int(time.time())}.json")
        OUTPUT_PATH.rename(backup)
        append_failed_log(f"Existing output JSON was corrupt and moved to {backup}")
        return {}


def save_mapping(mapping: dict[str, Any]) -> None:
    tmp_path = OUTPUT_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(OUTPUT_PATH)


def append_failed_log(message: str) -> None:
    FAILED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FAILED_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {message}\n")


def fetch_disease_id(session: requests.Session, key: str) -> tuple[str, str]:
    response = session.post(
        SEARCH_URL,
        data={"table_name": "Disease", "key": key},
        headers={"Referer": SEARCH_URL, "X-Requested-With": "XMLHttpRequest"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = json.loads(response.text)
    rows = payload.get("data", [])
    if not rows:
        return "", ""

    exact = [
        row for row in rows
        if str(row.get("Disease_name", "")).strip().lower() == key.lower()
    ]
    selected = exact[0] if exact else rows[0]
    return str(selected.get("Disease_id", "")).strip(), str(selected.get("Disease_name", "")).strip()


def fetch_related_rows(session: requests.Session, disease_id: str, table_name: str) -> list[dict[str, Any]]:
    response = session.post(
        RELATED_COMPONENTS_URL,
        data={"rrid": disease_id, "table_name": table_name, "filter": "0"},
        headers={
            "Referer": f"{BASE_URL}/detail/{disease_id}",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = json.loads(response.text)
    rows = payload.get("data", [])
    return rows if isinstance(rows, list) else []


def unique_nonempty(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def compact_herb_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "Herb_id", "Chinese_name", "Pinyin_name", "Latin_name", "English_name",
        "Value", "P_value", "FDR(BH)", "FDR(Bonferroni)", "Relationship",
    ]
    return [{field: row.get(field) for field in fields if field in row} for row in rows]


def compact_gene_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "Gene_id", "Gene_symbol", "Gene_name", "Protein_name", "UniProt_id", "Ensembl_id",
    ]
    return [{field: row.get(field) for field in fields if field in row} for row in rows]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
