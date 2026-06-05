"""Create quarantined pharmacology evidence patch candidates.

This script promotes nothing into the formal pharmacology database. It only
appends review-only candidates that still remain blocked from M2, prescription
generation, and the formal pharmacology cache.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "pharmacology_evidence_patch_candidates.jsonl"

ALLOWED_PROPOSED_GRADES = {"low_medium", "medium", "medium_high"}
ALLOWED_ORIGINAL_GRADES = {"low", "low_medium", "manual_review"}
REQUIRED_BLOCKED_FROM = {
    "formal_pharmacology_db",
    "m2_formula_candidate",
    "m2_modification_candidate",
    "prescription_generation",
}


def main() -> None:
    args = parse_args()
    low_grade_candidate = load_json(Path(args.low_grade_candidate))
    review_decision = load_json(Path(args.review_decision))
    patch = create_patch_candidate(
        low_grade_candidate=low_grade_candidate,
        review_decision=review_decision,
        patch_candidate_id=args.patch_candidate_id,
        proposed_evidence_grade=args.proposed_evidence_grade,
        upgrade_reason=args.upgrade_reason,
        notes=args.notes,
    )
    append_patch_candidate(patch, Path(args.output))
    print(json.dumps({"written": str(Path(args.output)), "patch_candidate_id": patch["patch_candidate_id"]}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a quarantined pharmacology evidence patch candidate.")
    parser.add_argument("--low-grade-candidate", required=True, help="Path to one JSON object.")
    parser.add_argument("--review-decision", required=True, help="Path to one JSON object.")
    parser.add_argument("--patch-candidate-id", required=True)
    parser.add_argument("--proposed-evidence-grade", required=True)
    parser.add_argument("--upgrade-reason", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def create_patch_candidate(
    *,
    low_grade_candidate: dict[str, Any],
    review_decision: dict[str, Any],
    patch_candidate_id: str,
    proposed_evidence_grade: str,
    upgrade_reason: str,
    notes: str = "",
) -> dict[str, Any]:
    validate_upgrade_inputs(low_grade_candidate, review_decision, proposed_evidence_grade, upgrade_reason)
    return {
        "patch_candidate_id": patch_candidate_id,
        "source_low_grade_candidate_id": review_decision.get("source_candidate_id", ""),
        "source_review_decision_id": review_decision.get("decision_id", ""),
        "disease": low_grade_candidate.get("disease", ""),
        "en_name": low_grade_candidate.get("en_name", ""),
        "candidate_term": low_grade_candidate.get("candidate_term", ""),
        "term_type": low_grade_candidate.get("term_type", "unknown"),
        "mapped_herb": low_grade_candidate.get("mapped_herb", ""),
        "proposed_evidence_grade": proposed_evidence_grade,
        "original_evidence_grade": low_grade_candidate.get("evidence_grade", ""),
        "upgrade_reason": upgrade_reason,
        "matched_pmids": low_grade_candidate.get("matched_pmids", []),
        "matched_titles": low_grade_candidate.get("matched_titles", []),
        "matched_targets": low_grade_candidate.get("matched_targets", []),
        "matched_outcomes": low_grade_candidate.get("matched_outcomes", []),
        "evidence_context": low_grade_candidate.get("evidence_context", "unknown"),
        "chain_status": low_grade_candidate.get("chain_status", "not_closed"),
        "reviewer": review_decision.get("reviewer", ""),
        "review_date": review_decision.get("review_date", ""),
        "approval_status": "pending_second_review",
        "allowed_usage": "evidence_patch_review_only",
        "blocked_from": sorted(REQUIRED_BLOCKED_FROM),
        "notes": notes,
    }


def validate_upgrade_inputs(
    low_grade_candidate: dict[str, Any],
    review_decision: dict[str, Any],
    proposed_evidence_grade: str,
    upgrade_reason: str,
) -> None:
    if review_decision.get("decision") != "upgrade_candidate":
        raise ValueError("review decision must be upgrade_candidate")
    if review_decision.get("allowed_next_step") != "evidence_patch_candidate":
        raise ValueError("allowed_next_step must be evidence_patch_candidate")
    ensure_blocks(review_decision.get("still_blocked_from", []), "review_decision.still_blocked_from")

    if proposed_evidence_grade not in ALLOWED_PROPOSED_GRADES:
        raise ValueError(f"invalid proposed_evidence_grade: {proposed_evidence_grade}")
    if proposed_evidence_grade == "high":
        raise ValueError("proposed_evidence_grade=high is forbidden")

    if low_grade_candidate.get("evidence_grade") not in ALLOWED_ORIGINAL_GRADES:
        raise ValueError(f"invalid original low grade: {low_grade_candidate.get('evidence_grade')}")
    ensure_blocks(low_grade_candidate.get("blocked_from", []), "low_grade_candidate.blocked_from")

    if not low_grade_candidate.get("matched_pmids"):
        raise ValueError("matched_pmids are required")
    if not str(upgrade_reason or "").strip():
        raise ValueError("upgrade_reason is required")
    if low_grade_candidate.get("term_type") == "formula":
        raise ValueError("formula cannot be split into single-herb evidence patch candidates")
    if low_grade_candidate.get("chain_status") != "closed":
        raise ValueError("chain_status must be closed")

    context = low_grade_candidate.get("evidence_context", "")
    if proposed_evidence_grade == "medium_high" and context in {"network_pharmacology", "molecular_docking"}:
        raise ValueError("network pharmacology or molecular docking cannot be upgraded to medium_high alone")


def ensure_blocks(values: list[str], label: str) -> None:
    missing = sorted(REQUIRED_BLOCKED_FROM - set(values or []))
    if missing:
        raise ValueError(f"{label} missing required blocks: {missing}")


def append_patch_candidate(record: dict[str, Any], output_path: Path = DEFAULT_OUTPUT) -> None:
    validate_patch_candidate(record)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def validate_patch_candidate(record: dict[str, Any]) -> None:
    if record.get("proposed_evidence_grade") not in ALLOWED_PROPOSED_GRADES:
        raise ValueError("invalid proposed_evidence_grade")
    if record.get("allowed_usage") != "evidence_patch_review_only":
        raise ValueError("allowed_usage must be evidence_patch_review_only")
    ensure_blocks(record.get("blocked_from", []), "patch_candidate.blocked_from")
    if record.get("approval_status") != "pending_second_review":
        raise ValueError("approval_status must be pending_second_review")


if __name__ == "__main__":
    main()
