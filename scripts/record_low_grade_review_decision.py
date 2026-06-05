"""Append human review decisions for quarantined low-grade pharmacology clues.

This tool only writes a review-decision JSONL. It does not read or modify the
formal pharmacology cache, M2, M3, synonym tables, or prescription logic.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "pharmacology_low_grade_review_decisions.jsonl"

ALLOWED_DECISIONS = {"keep_low", "upgrade_candidate", "reject", "needs_more_evidence"}
ALLOWED_NEXT_STEPS = {"stay_quarantine", "evidence_patch_candidate", "discard"}
ALLOWED_ORIGINAL_GRADES = {"low", "low_medium", "manual_review"}
REQUIRED_BLOCKED_FROM = {
    "formal_pharmacology_db",
    "m2_formula_candidate",
    "m2_modification_candidate",
    "prescription_generation",
}


def main() -> None:
    args = parse_args()
    record = build_record_from_args(args)
    append_review_decision(record, Path(args.output))
    print(json.dumps({"written": str(Path(args.output)), "decision_id": record["decision_id"]}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a low-grade pharmacology human review decision.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--source-candidate-id", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--en-name", required=True)
    parser.add_argument("--candidate-term", required=True)
    parser.add_argument("--mapped-herb", required=True)
    parser.add_argument("--original-evidence-grade", required=True, choices=sorted(ALLOWED_ORIGINAL_GRADES))
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--review-date", default=date.today().isoformat())
    parser.add_argument("--decision", required=True, choices=sorted(ALLOWED_DECISIONS))
    parser.add_argument("--why", required=True)
    parser.add_argument("--allowed-next-step", required=True, choices=sorted(ALLOWED_NEXT_STEPS))
    parser.add_argument("--still-blocked-from", nargs="+", default=sorted(REQUIRED_BLOCKED_FROM))
    parser.add_argument("--example", action="store_true")
    return parser.parse_args()


def build_record_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "decision_id": args.decision_id,
        "source_candidate_id": args.source_candidate_id,
        "disease": args.disease,
        "en_name": args.en_name,
        "candidate_term": args.candidate_term,
        "mapped_herb": args.mapped_herb,
        "original_evidence_grade": args.original_evidence_grade,
        "reviewer": args.reviewer,
        "review_date": args.review_date,
        "decision": args.decision,
        "why": args.why,
        "allowed_next_step": args.allowed_next_step,
        "still_blocked_from": args.still_blocked_from,
        "example": bool(args.example),
    }


def append_review_decision(record: dict[str, Any], output_path: Path = DEFAULT_OUTPUT) -> None:
    validate_review_decision(record)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def validate_review_decision(record: dict[str, Any]) -> None:
    decision = record.get("decision")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")

    next_step = record.get("allowed_next_step")
    if next_step not in ALLOWED_NEXT_STEPS:
        raise ValueError(f"invalid allowed_next_step: {next_step}")

    grade = record.get("original_evidence_grade")
    if grade not in ALLOWED_ORIGINAL_GRADES:
        raise ValueError(f"invalid original_evidence_grade: {grade}")

    blocked_from = set(record.get("still_blocked_from") or [])
    missing_blocks = sorted(REQUIRED_BLOCKED_FROM - blocked_from)
    if missing_blocks:
        raise ValueError(f"still_blocked_from missing required blocks: {missing_blocks}")

    required_text_fields = [
        "decision_id",
        "source_candidate_id",
        "disease",
        "en_name",
        "candidate_term",
        "mapped_herb",
        "reviewer",
        "review_date",
        "why",
    ]
    missing_text = [field for field in required_text_fields if not str(record.get(field, "")).strip()]
    if missing_text:
        raise ValueError(f"missing required fields: {missing_text}")


if __name__ == "__main__":
    main()
