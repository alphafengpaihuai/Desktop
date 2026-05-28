"""Read-only auxiliary knowledge for M1 western diagnosis.

The service is disabled by default. It only returns extra context for M1 and
never rewrites the existing M1 result or disease database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
CORE_DB = BASE_DIR / "diseases_core.json"

ALIAS_PATH = DATA_DIR / "m1_disease_alias_map.json"
COMPLAINT_PATH = DATA_DIR / "m1_chief_complaint_differential_map.json"
RED_FLAG_PATH = DATA_DIR / "m1_red_flag_rules.json"
FEATURE_PATH = DATA_DIR / "m1_clinical_feature_interpreter.json"


class M1AuxiliaryKnowledgeService:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.paths = {
            "alias_map": self.data_dir / "m1_disease_alias_map.json",
            "complaint_map": self.data_dir / "m1_chief_complaint_differential_map.json",
            "red_flag_rules": self.data_dir / "m1_red_flag_rules.json",
            "feature_interpreter": self.data_dir / "m1_clinical_feature_interpreter.json",
        }
        self.knowledge = self.load_auxiliary_knowledge()

    def load_auxiliary_knowledge(self) -> dict[str, Any]:
        loaded = {}
        for key, path in self.paths.items():
            with path.open("r", encoding="utf-8") as f:
                loaded[key] = json.load(f)
        return loaded

    def normalize_disease_name(self, query: str) -> dict:
        q = _norm(query)
        for display_name, item in self.knowledge["alias_map"].items():
            names = [display_name, item.get("canonical_disease_name", "")]
            names.extend(item.get("aliases", []))
            if q in {_norm(name) for name in names if name}:
                return {
                    "query": query,
                    "matched_key": display_name,
                    "canonical_disease_name": item.get("canonical_disease_name", ""),
                    "aliases": item.get("aliases", []),
                    "mapping_status": item.get("mapping_status", ""),
                    "match_priority": item.get("match_priority", 0),
                    "source_quality": item.get("source_quality", ""),
                    "notes": item.get("notes", ""),
                    "diagnostic_weight": 0,
                }
        return {}

    def get_differentials_by_complaint(self, complaint: str) -> dict:
        q = _norm(complaint)
        for key, item in self.knowledge["complaint_map"].items():
            candidates = [key, item.get("normalized_complaint", "")]
            if q in {_norm(x) for x in candidates if x}:
                return item
        for key, item in self.knowledge["complaint_map"].items():
            if q and (q in _norm(key) or _norm(key) in q):
                return item
        return {}

    def detect_red_flags(self, patient_text_or_features: str | list[str] | dict) -> list[dict]:
        text = _text_blob(patient_text_or_features)
        triggered = []
        for rule_id, rule in self.knowledge["red_flag_rules"].items():
            hits = [term for term in rule.get("trigger_terms", []) if term and term in text]
            if hits:
                item = dict(rule)
                item["rule_id"] = rule_id
                item["matched_terms"] = hits
                triggered.append(item)
        severity_rank = {"emergency": 3, "urgent": 2, "warning": 1}
        triggered.sort(key=lambda item: severity_rank.get(item.get("severity", ""), 0), reverse=True)
        return triggered

    def interpret_clinical_features(self, features: str | list[str] | dict) -> list[dict]:
        text = _text_blob(features)
        results = []
        for feature_id, item in self.knowledge["feature_interpreter"].items():
            hits = [alias for alias in item.get("feature_aliases", []) if alias and alias in text]
            if hits:
                row = dict(item)
                row["feature_id"] = feature_id
                row["matched_aliases"] = hits
                results.append(row)
        return results

    def enrich_m1_input(self, patient_info: dict) -> dict:
        patient_info = patient_info or {}
        text = _text_blob(patient_info)
        diagnosis_query = (
            patient_info.get("diagnosis")
            or patient_info.get("m1_diagnosis")
            or patient_info.get("disease_name")
            or ""
        )
        complaint = patient_info.get("chief_complaint") or patient_info.get("complaint") or ""

        alias_match = self.normalize_disease_name(diagnosis_query) if diagnosis_query else {}
        complaint_diff = self.get_differentials_by_complaint(complaint) if complaint else {}
        red_flags = self.detect_red_flags(text)
        feature_interpretations = self.interpret_clinical_features(text)

        suggested_axes = []
        for item in feature_interpretations:
            for axis in item.get("mapped_axes", []):
                axis_name = axis.get("axis")
                if axis_name and axis_name not in suggested_axes:
                    suggested_axes.append(axis_name)

        must_not_miss = []
        if complaint_diff:
            must_not_miss.extend(complaint_diff.get("must_not_miss", []))
        for rule in red_flags:
            for disease in rule.get("risk_direction", []):
                name = disease.get("disease_name") if isinstance(disease, dict) else disease
                if name and name not in must_not_miss:
                    must_not_miss.append(name)

        return {
            "normalized_disease_query": alias_match.get("canonical_disease_name", ""),
            "alias_matches": [alias_match] if alias_match else [],
            "chief_complaint_differentials": [complaint_diff] if complaint_diff else [],
            "red_flags": red_flags,
            "clinical_feature_interpretations": feature_interpretations,
            "suggested_pathology_axes": suggested_axes,
            "must_not_miss_diseases": must_not_miss,
            "need_external_check": bool(red_flags or any(i.get("red_flag_relevant") for i in feature_interpretations)),
            "block_m2_auto_flow": any(flag.get("block_m2_auto_flow") for flag in red_flags),
            "trace": {
                "enabled": True,
                "data_files_loaded": [str(path) for path in self.paths.values()],
                "warnings": [],
            },
        }


def build_m1_auxiliary_context(patient_info: dict) -> dict:
    if os.getenv("M1_AUX_KNOWLEDGE_ENABLED", "false").lower() != "true":
        return {}
    return M1AuxiliaryKnowledgeService().enrich_m1_input(patient_info)


def _norm(text: str) -> str:
    return "".join(str(text or "").lower().replace("（", "(").replace("）", ")").split())


def _text_blob(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_text_blob(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_text_blob(v) for v in value.values())
    return str(value or "")
