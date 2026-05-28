"""Audit SymMap relationship availability and rebuild pharmacology cache.

This script deliberately treats SMDE/SMHB/SMTT/SMIT/SMMS/SMTS as entity or
search-key tables unless an explicit relationship edge can be inferred from
columns. It does not invent herb-target-disease chains when pairwise edge
tables are absent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SYMMAP_DIR = DATA / "symmap_v2_download"
INVENTORY_PATH = DATA / "symmap_relationship_inventory.json"
OLD_CACHE_PATH = DATA / "disease_pharmacology_cache.json"
REBUILT_CACHE_PATH = DATA / "disease_pharmacology_cache_rebuilt.json"
SYMMAP_API_CACHE_PATH = DATA / "disease_cache" / "symmap_online_mapping.json"
MANIFEST_PATH = DATA / "disease_cache" / "disease_manifest.json"
CORE_PATH = ROOT / "diseases_core.json"


RELATIONSHIP_TYPES = {
    "herb_ingredient": ("herb", "ingredient"),
    "ingredient_target": ("ingredient", "target"),
    "target_disease": ("target", "disease"),
    "herb_disease": ("herb", "disease"),
    "symptom_disease": ("symptom", "disease"),
    "symptom_herb": ("symptom", "herb"),
}


def main() -> None:
    inventory = build_inventory()
    INVENTORY_PATH.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    rebuilt = rebuild_cache(inventory)
    REBUILT_CACHE_PATH.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2), encoding="utf-8")

    usable = sum(1 for item in inventory if item.get("usable_for_disease_target_herb_mapping"))
    sufficient = sum(1 for item in rebuilt.values() if item.get("evidence_confidence") != "Insufficient")
    print(json.dumps({
        "inventory_path": str(INVENTORY_PATH.relative_to(ROOT)),
        "inventory_files": len(inventory),
        "usable_inventory_files": usable,
        "rebuilt_cache_path": str(REBUILT_CACHE_PATH.relative_to(ROOT)),
        "rebuilt_cache_entries": len(rebuilt),
        "entries_with_clear_symmap_relationship": sufficient,
        "entries_insufficient": len(rebuilt) - sufficient,
    }, ensure_ascii=False, indent=2))


def build_inventory() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(SYMMAP_DIR.glob("*.xlsx")):
        columns, row_count = inspect_xlsx(path)
        inferred, usable, reason = infer_relationship(path.name, columns)
        items.append({
            "file_path": str(path.relative_to(ROOT)),
            "file_type": "xlsx",
            "columns": columns,
            "row_count": row_count,
            "relationship_inferred": inferred,
            "usable_for_disease_target_herb_mapping": usable,
            "reason": reason,
        })

    if SYMMAP_API_CACHE_PATH.exists():
        data = load_json(SYMMAP_API_CACHE_PATH, {})
        sample = next(iter(data.values()), {}) if isinstance(data, dict) and data else {}
        columns = sorted(sample.keys()) if isinstance(sample, dict) else []
        has_targets = any(v.get("molecular_targets") for v in data.values()) if isinstance(data, dict) else False
        has_herbs = any(v.get("effective_herbs") for v in data.values()) if isinstance(data, dict) else False
        items.append({
            "file_path": str(SYMMAP_API_CACHE_PATH.relative_to(ROOT)),
            "file_type": "json_api_cache",
            "columns": columns,
            "row_count": len(data) if isinstance(data, dict) else 0,
            "relationship_inferred": ["disease-target/gene", "disease-herb"],
            "usable_for_disease_target_herb_mapping": bool(has_targets or has_herbs),
            "reason": (
                "SymMap related_components API cache contains disease-keyed molecular_targets "
                "and/or effective_herbs. This is usable only as direct disease→target/gene "
                "and disease→herb evidence; it cannot infer herb→ingredient→target chains."
            ),
        })

    return items


def inspect_xlsx(path: Path) -> tuple[list[str], int]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, ())
    columns = [str(col).strip() for col in header if col is not None]
    return columns, max((sheet.max_row or 1) - 1, 0)


def infer_relationship(file_name: str, columns: list[str]) -> tuple[list[str], bool, str]:
    lowered = {col.lower() for col in columns}
    name = file_name.lower()

    if "key file" in name:
        return [], False, "Search-key/alias table only; maps entity IDs to search terms, not biomedical relationships."

    inferred: list[str] = []
    for rel_name, required_tokens in RELATIONSHIP_TYPES.items():
        if all(any(token in col for col in lowered) for token in required_tokens):
            inferred.append(rel_name.replace("_", "-"))

    if "symmap v2.0, smit file" in name:
        return [], False, (
            "Ingredient entity table. It has Mol_id and ingredient attributes, but no Herb_id, "
            "Gene_id/Target_id, Disease_id, or symptom ID edge columns."
        )
    if "symmap v2.0, smde file" in name:
        return [], False, "Disease entity table; no target/gene, herb, ingredient, or symptom relationship columns."
    if "symmap v2.0, smhb file" in name:
        return [], False, "Herb entity table; no ingredient, target/gene, disease, or symptom relationship columns."
    if "symmap v2.0, smtt file" in name:
        return [], False, "Target/gene entity table; no disease, ingredient, herb, or symptom relationship columns."
    if "symmap v2.0, smms file" in name:
        return [], False, "Modern medicine symptom entity table; no disease or herb relationship columns."
    if "symmap v2.0, smts file" in name or "symmap v2.0, smsy file" in name:
        return [], False, "TCM symptom/syndrome entity table; not a symptom-disease or symptom-herb edge table."

    if inferred:
        return inferred, True, "Columns contain both endpoint entity identifiers/names for an explicit relationship."
    return [], False, "No recognized pairwise relationship endpoint columns."


def rebuild_cache(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    old_cache = load_json(OLD_CACHE_PATH, {})
    symmap_cache = load_json(SYMMAP_API_CACHE_PATH, {})
    disease_names = collect_disease_names(old_cache, symmap_cache)

    direct_symmap = {
        name: value for name, value in symmap_cache.items()
        if isinstance(value, dict)
    } if isinstance(symmap_cache, dict) else {}

    rebuilt: dict[str, Any] = {}
    for disease_name in sorted(disease_names):
        api = direct_symmap.get(disease_name, {})
        direct_targets = unique_strings(api.get("molecular_targets", []))
        direct_herbs = unique_strings(api.get("effective_herbs", []))
        old = old_cache.get(disease_name, {}) if isinstance(old_cache, dict) else {}

        confidence = "Insufficient"
        relationship_path = []
        source = "symmap_relationship_rebuild"
        targets: list[str] = []
        herbs: list[str] = []
        if direct_herbs and direct_targets:
            relationship_path.append("disease → target/gene")
            targets = direct_targets
        if direct_herbs:
            relationship_path.append("disease → herb")
            herbs = direct_herbs
        if relationship_path:
            confidence = "DirectSymMap"
            source = "symmap_related_components_direct"

        rebuilt[disease_name] = {
            "source": source,
            "western_disease_name": disease_name,
            "symmap_disease_id": api.get("symmap_disease_id", ""),
            "symmap_matched_name": api.get("symmap_matched_name", ""),
            "relationship_paths_used": relationship_path,
            "key_targets": targets,
            "evidence_based_herbs": herbs,
            "evidence_confidence": confidence,
            "verification_recommendation": (
                "Direct SymMap API relationship present; still requires clinical review before use."
                if confidence != "Insufficient"
                else "No explicit SymMap relationship chain found. Do not infer targets or herbs."
            ),
            "matched_our_diseases": old.get("matched_our_diseases", [disease_name]) if isinstance(old, dict) else [disease_name],
            "symmap_targets": targets,
            "symmap_herbs": herbs,
            "gpt_whitelist_supplement": build_gpt_supplement(old, has_direct_symmap=bool(relationship_path)),
            "relationship_inventory_source": str(INVENTORY_PATH.relative_to(ROOT)),
            "rebuilt_at": now_iso(),
        }
    return rebuilt


def build_gpt_supplement(old: Any, *, has_direct_symmap: bool) -> dict[str, Any]:
    if not isinstance(old, dict):
        return {"used": False, "reason": "No prior GPT pharmacology card."}
    old_source = str(old.get("source", ""))
    old_targets = unique_strings(old.get("key_targets", []))
    old_herbs = unique_strings(old.get("evidence_based_herbs", []))
    if not old_targets and not old_herbs:
        return {"used": False, "reason": "Prior GPT card has no targets or herbs."}
    return {
        "used": False,
        "reason": (
            "Prior GPT pharmacology content retained only as review metadata; not merged into "
            "key_targets/evidence_based_herbs because this rebuild requires explicit SymMap relationships."
        ),
        "prior_source": old_source,
        "available_target_count": len(old_targets),
        "available_herb_count": len(old_herbs),
        "direct_symmap_present": has_direct_symmap,
    }


def collect_disease_names(old_cache: Any, symmap_cache: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(old_cache, dict):
        names.update(str(key).strip() for key in old_cache if str(key).strip())
    if isinstance(symmap_cache, dict):
        names.update(str(key).strip() for key in symmap_cache if str(key).strip())

    manifest = load_json(MANIFEST_PATH, {})
    if isinstance(manifest, dict):
        for item in manifest.get("diseases", []):
            if isinstance(item, dict):
                name = str(item.get("standard_western_name", "")).strip()
                if name:
                    names.add(name)

    core = load_json(CORE_PATH, [])
    if isinstance(core, list):
        for item in core:
            if not isinstance(item, dict):
                continue
            for field in ("diseaseName_cn", "disease_name"):
                name = str(item.get(field, "")).strip()
                if name:
                    names.add(name)
                    break
    return names


def unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
