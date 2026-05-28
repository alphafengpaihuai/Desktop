from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
REPORT_PATH = REPORT_DIR / "m1_auxiliary_knowledge_audit_report.json"
CORE_DB = BASE_DIR / "diseases_core.json"

FILES = {
    "alias_map": DATA_DIR / "m1_disease_alias_map.json",
    "complaint_map": DATA_DIR / "m1_chief_complaint_differential_map.json",
    "red_flag_rules": DATA_DIR / "m1_red_flag_rules.json",
    "feature_interpreter": DATA_DIR / "m1_clinical_feature_interpreter.json",
}

FORBIDDEN_TCM_TERMS = [
    "证型", "病机", "治则", "方剂", "中药", "君臣佐使", "调理", "辨证",
    "寒热虚实", "脏腑辨证",
]

ALLOWED_ACTIONS = {"need_external_check", "urgent_review", "block_auto_m2", "warning_only"}


def main() -> dict:
    report = audit()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def audit() -> dict:
    allowed_axes, disease_names = _load_core_metadata()
    report = {
        "ok": True,
        "files": {},
        "errors": [],
        "warnings": [],
        "counts": {},
    }

    loaded = {}
    for name, path in FILES.items():
        item = {"exists": path.exists(), "json_valid": False}
        if not path.exists():
            report["errors"].append(f"missing file: {path}")
            report["ok"] = False
            report["files"][name] = item
            continue
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
            item["json_valid"] = True
            report["counts"][name] = len(loaded[name])
        except Exception as exc:
            report["errors"].append(f"invalid json {path}: {exc}")
            report["ok"] = False
        report["files"][name] = item

    for name, data in loaded.items():
        blob = json.dumps(data, ensure_ascii=False)
        found_terms = [term for term in FORBIDDEN_TCM_TERMS if term in blob]
        if found_terms:
            report["errors"].append(f"{name} contains forbidden TCM terms: {found_terms}")
            report["ok"] = False

    _audit_source_quality(loaded, report)
    _audit_alias_map(loaded.get("alias_map", {}), disease_names, report)
    _audit_complaint_map(loaded.get("complaint_map", {}), disease_names, report)
    _audit_red_flags(loaded.get("red_flag_rules", {}), disease_names, report)
    _audit_features(loaded.get("feature_interpreter", {}), allowed_axes, disease_names, report)
    return report


def _load_core_metadata() -> tuple[set[str], set[str]]:
    entries = json.loads(CORE_DB.read_text(encoding="utf-8"))
    axes = {axis.get("axis") for entry in entries for axis in entry.get("axes", []) if axis.get("axis")}
    diseases = {entry.get("disease_name") for entry in entries if entry.get("disease_name")}
    return axes, diseases


def _audit_source_quality(loaded: dict[str, Any], report: dict) -> None:
    for file_name, data in loaded.items():
        for key, item in _iter_records(data):
            if not isinstance(item, dict):
                continue
            if "source_quality" not in item:
                report["errors"].append(f"{file_name}.{key} missing source_quality")
                report["ok"] = False


def _audit_alias_map(data: dict, disease_names: set[str], report: dict) -> None:
    for key, item in data.items():
        canonical = item.get("canonical_disease_name", "")
        expected = "in_m1" if canonical in disease_names else "not_in_m1"
        if item.get("mapping_status") != expected:
            report["errors"].append(f"alias {key} mapping_status should be {expected}")
            report["ok"] = False


def _audit_complaint_map(data: dict, disease_names: set[str], report: dict) -> None:
    for key, item in data.items():
        for field in ["normalized_complaint", "must_consider", "common_differentials", "red_flags", "first_checks", "source_quality"]:
            if field not in item:
                report["errors"].append(f"complaint {key} missing {field}")
                report["ok"] = False
        for row in item.get("must_consider", []) + item.get("common_differentials", []):
            _audit_disease_ref(row, disease_names, report, f"complaint {key}")


def _audit_red_flags(data: dict, disease_names: set[str], report: dict) -> None:
    for key, item in data.items():
        if item.get("action") not in ALLOWED_ACTIONS:
            report["errors"].append(f"red flag {key} invalid action: {item.get('action')}")
            report["ok"] = False
        for row in item.get("risk_direction", []):
            _audit_disease_ref(row, disease_names, report, f"red flag {key}")


def _audit_features(data: dict, allowed_axes: set[str], disease_names: set[str], report: dict) -> None:
    for key, item in data.items():
        for axis in item.get("mapped_axes", []):
            if axis.get("axis") not in allowed_axes:
                report["errors"].append(f"feature {key} invalid axis: {axis.get('axis')}")
                report["ok"] = False
        for row in item.get("suggested_diseases", []):
            _audit_disease_ref(row, disease_names, report, f"feature {key}")


def _audit_disease_ref(row: Any, disease_names: set[str], report: dict, where: str) -> None:
    if isinstance(row, str):
        report["errors"].append(f"{where} disease ref must include mapping_status: {row}")
        report["ok"] = False
        return
    name = row.get("disease_name", "")
    expected = "in_m1" if name in disease_names else "not_in_m1"
    if row.get("mapping_status") != expected:
        report["errors"].append(f"{where} {name} mapping_status should be {expected}")
        report["ok"] = False


def _iter_records(data: Any):
    if isinstance(data, dict):
        yield from data.items()
    elif isinstance(data, list):
        for i, item in enumerate(data):
            yield str(i), item


if __name__ == "__main__":
    main()
