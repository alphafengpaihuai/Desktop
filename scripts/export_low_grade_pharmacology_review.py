"""Export read-only review reports for low-grade pharmacology candidates.

The exported reports are for human review only. They are not connected to M2,
M3, the formal pharmacology cache, or prescription generation.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_JSONL = ROOT / "data" / "pharmacology_low_grade_candidates.jsonl"
OUTPUT_JSON = ROOT / "data" / "pharmacology_low_grade_review.json"
OUTPUT_MD = ROOT / "docs" / "pharmacology_low_grade_review.md"

SAFETY_NOTICE = (
    "本报告仅用于人工复核低等级药理线索，不属于正式药理库，不得用于 M2 "
    "候选方生成、M2 加减药生成或处方推荐。"
)

REQUIRED_BLOCKS = {
    "formal_pharmacology_db",
    "m2_formula_candidate",
    "m2_modification_candidate",
    "prescription_generation",
}


def main() -> None:
    rows = read_low_grade_candidates(INPUT_JSONL)
    report = build_review_report(rows)
    write_json_report(report, OUTPUT_JSON)
    write_markdown_report(report, OUTPUT_MD)
    print(json.dumps({
        "input": str(INPUT_JSONL),
        "json_report": str(OUTPUT_JSON),
        "markdown_report": str(OUTPUT_MD),
        "candidate_count": report["candidate_count"],
    }, ensure_ascii=False, indent=2))


def read_low_grade_candidates(path: Path = INPUT_JSONL) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_review_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warnings: list[str] = []
    for row in rows:
        missing_blocks = sorted(REQUIRED_BLOCKS - set(row.get("blocked_from", [])))
        if missing_blocks:
            warnings.append(f"{row.get('disease', '')}/{row.get('candidate_term', '')} missing blocked_from: {missing_blocks}")
        grouped[str(row.get("disease", ""))].append(normalize_review_row(row))

    return {
        "report_type": "low_grade_pharmacology_review",
        "safety_notice": SAFETY_NOTICE,
        "candidate_count": len(rows),
        "allowed_usage": "manual_review_only",
        "must_not_connect_to": sorted(REQUIRED_BLOCKS),
        "groups": [
            {
                "disease": disease,
                "en_name": first_non_empty(items, "en_name"),
                "candidates": items,
            }
            for disease, items in sorted(grouped.items())
        ],
        "warnings": warnings,
    }


def normalize_review_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "disease",
        "en_name",
        "candidate_term",
        "mapped_herb",
        "evidence_grade",
        "chain_status",
        "matched_pmids",
        "matched_titles",
        "matched_targets",
        "matched_outcomes",
        "evidence_context",
        "why_not_formal",
        "allowed_usage",
        "blocked_from",
        "review_status",
    ]
    return {key: row.get(key, [] if key.startswith("matched_") or key == "blocked_from" else "") for key in keys}


def first_non_empty(items: list[dict[str, Any]], key: str) -> str:
    for item in items:
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return ""


def write_json_report(report: dict[str, Any], path: Path = OUTPUT_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_report(report: dict[str, Any], path: Path = OUTPUT_MD) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 低等级药理线索人工复核报告",
        "",
        SAFETY_NOTICE,
        "",
        f"- 候选总数：{report.get('candidate_count', 0)}",
        "- 允许用途：manual_review_only",
        "- 禁止流向：formal_pharmacology_db, m2_formula_candidate, m2_modification_candidate, prescription_generation",
        "",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["## Schema Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    for group in report.get("groups", []):
        lines.extend([
            f"## {group.get('disease', '')} / {group.get('en_name', '')}",
            "",
        ])
        for candidate in group.get("candidates", []):
            lines.extend([
                f"### {candidate.get('candidate_term', '')} -> {candidate.get('mapped_herb', '')}",
                "",
                f"- evidence_grade: {candidate.get('evidence_grade', '')}",
                f"- chain_status: {candidate.get('chain_status', '')}",
                f"- evidence_context: {candidate.get('evidence_context', '')}",
                f"- review_status: {candidate.get('review_status', '')}",
                f"- allowed_usage: {candidate.get('allowed_usage', '')}",
                f"- blocked_from: {', '.join(candidate.get('blocked_from', []))}",
                f"- matched_pmids: {', '.join(candidate.get('matched_pmids', []))}",
                f"- matched_targets: {', '.join(candidate.get('matched_targets', []))}",
                f"- matched_outcomes: {', '.join(candidate.get('matched_outcomes', []))}",
                "",
                "matched_titles:",
            ])
            titles = candidate.get("matched_titles") or []
            lines.extend(f"- {title}" for title in titles)
            lines.extend([
                "",
                "why_not_formal:",
                "",
                candidate.get("why_not_formal", ""),
                "",
            ])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
