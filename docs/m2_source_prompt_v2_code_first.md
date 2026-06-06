# M2 Syndrome Selector — v2 Code-First Spec

> **Status**: Active (supersedes `prompts/m2/M2_SOURCE_PROMPT.md` v1)
> **Effective**: 2026-06-06
>
> ⚠ This spec replaces the Prompt-First v1 design.
> The old spec assumed LLM is the primary judge; v2 makes code the primary scorer
> and restricts LLM to fallback / explanation / tie-breaker only.

---

## 1. Module Positioning

M2 = Syndrome Differentiation & Formula Selection module.

**Pipeline position:**
```
M1 (diagnosis) → M2-1 (syndrome scoring) → M2-2 (formula candidates)
   → M2-3 (modification candidates) → M3 (pharmaceutical review)
```

**M2 is split into three independent sub-stages:**
- **M2-1** — syndrome scoring (code-first)
- **M2-2** — formula candidate lookup (code-first)
- **M2-3** — modification candidate lookup (code-first)

Each sub-stage has its own entry point. M2-1 must always run first; its output
(`syndrome_trace`) feeds into M2-2, which feeds into M2-3.

---

## 2. Core Principles

### 2.1 Code-First Syndrome Scorer

- M2-1 **must** load all syndromes under the resolved disease key from
  `data/m2_formula_knowledge.json`.
- M2-1 **must** score every syndrome using multi-dimensional matching:
  symptoms, tongue, pulse, pathology (痰/湿/瘀/热/寒), cold/heat.
- M2-1 **must** output a ranked list of `candidate_scores`.
- M2-1 **must** output the Top 1 syndrome as `syndrome_name`.
- M2-1 **may** output internal debug fields (`confidence`, `candidate_scores`,
  `evidence_trace`, `matched_symptoms`, `matched_pathology`,
  `matched_tongue_pulse`) — these are for internal/audit use only.
- M2-1 **must not** output formula/herbs/dosage/prescription text.
  Those belong to M2-2 and beyond.

### 2.2 LLM Is Fallback Only

- **Primary path**: code-first scorer selects the syndrome.
- LLM is invoked **only** when:
  - Code-first confidence is low (below threshold).
  - Top 2 syndrome scores are within a narrow margin (tie-breaker).
  - Information conflict exists (e.g. cold symptoms + hot tongue).
  - An explanation in natural language is requested.
- When LLM disagrees with a high-confidence code-first Top 1, the
  code-first result **must** prevail unless there is clear evidence the
  scorer made a mistake (e.g. key mismatch, data corruption).
- **API unavailable**: code-first scorer continues to work independently.
  No syndrome/formula output is blocked.

### 2.3 Knowledge-Base-Bound Selection

- All syndromes, formulas, and herbs **must** come from
  `data/m2_formula_knowledge.json`.
- LLM must never invent syndromes, formulas, or herbs outside the KB.
- Code scorer must never create synthetic syndrome names.

### 2.4 No Unauthorized Expansion

- AI must not expand system boundaries without a spec update.
- No adding of new modules (trace audit, pipeline, pharmacology) unless
  explicitly specified in this document.

---

## 3. Input Format

```json
{
  "primary_disease": "西医病名（来自 M1 或手工指定）",
  "symptoms": [],
  "signs": [],
  "tongue": "",
  "pulse": "",
  "cold_heat": [],
  "stool_urine": [],
  "sleep": [],
  "appetite": [],
  "labs": [],
  "imaging": [],
  "age": "",
  "weight": ""
}
```

### Field Notes

| Field | Notes |
|-------|-------|
| `primary_disease` | Resolved through `resolve_m2_disease_key()`. The resolver applies exact match → DISEASE_NAME_MAP → m2_fallback → substring → clean-parentheses → raw return. |
| `tongue` | Supports full tongue description, e.g. `"舌淡，苔薄白"`. Scorer extracts both 舌 and 苔 parts. |
| `pulse` | Full pulse description, e.g. `"脉浮紧"`. |

---

## 4. Knowledge Base

### Location
`data/m2_formula_knowledge.json`

### Structure
```json
{
  "疾病名": {
    "disease_name": "疾病名",
    "syndromes": {
      "证型名": {
        "syndrome_name": "证型名",
        "trigger": "证型触发条件（自然语言描述）",
        "formula_name": "方剂名称",
        "herbs": ["药1", "药2"]
      }
    }
  }
}
```

---

## 5. M2-1 Scoring Dimensions

Total score = symptom_match (0-40) + tongue_match (0-20) + pulse_match (0-15)
         + pathology_match (0-15) + cold_heat_match (0-10)

### 5.1 Symptom Match (0-40pt)
- Uses structured `symptoms` field from KB syndrome data when available.
- Character overlap matching (min 50% ratio) for fuzzy matches.
- Trigger text fallback for symptoms not matched by structured data.
- Bigram matching as a secondary fallback.

### 5.2 Tongue Match (0-20pt)
- Both 舌质 and 苔质 components are extracted from patient input.
- Structured KB `tongue` field is matched preferentially.
- Color keywords (红/淡/暗), 苔 keywords (白/黄/腻/薄/厚) are scored.

### 5.3 Pulse Match (0-15pt)
- Structural KB `pulse` field matching.
- Keyword matching (浮/沉/数/迟/弦/滑/细/涩/紧).

### 5.4 Pathology Match (0-15pt)
- Six axis keywords: 热/寒/痰/湿/瘀/气滞.
- Patient heat/cold dominance is determined first; pathology scoring respects it.
- Negation-aware: "无明显口干" does not match heat keywords.

### 5.5 Cold/Heat Match (0-10pt)
- patient's `cold_heat` field directly rewards matching trigger keywords.
- Cold-specific symptoms (怕冷/恶寒/清稀/苔白/舌淡/不渴/肢冷) penalize
  non-cold syndromes, with stronger penalty for 2+ cold-specific matches.
- Heat-specific symptoms (口干/口苦/黄痰/黄涕/舌红/烦躁/灼痛) penalize
  non-heat syndromes.
- Negation-aware throughout.

### 5.6 Additional Adjustments
- Heat-phlegm bonus: when patient has 黄痰/黄稠/铁锈色 and syndrome
  contains 痰+热, +3pt.
- Qi deficiency bonus: when patient has 气短/神疲/乏力 and syndrome
  contains 虚, +3pt.
- Deficiency penalty: when patient has heat-phlegm and syndrome
  contains 虚, -5pt.

---

## 6. LLM Integration Rules

### 6.1 When to call LLM

| Condition | Action |
|-----------|--------|
| Code-first Top1 confidence ≥ 0.6 | LLM not needed; code-first result stands |
| Code-first Top1 confidence < 0.6 | Optionally call LLM for fallback |
| Top1 vs Top2 score diff < 3.0 | Optionally call LLM as tie-breaker |
| Conflicting signals (e.g. cold+heat tied) | Optionally call LLM for disambiguation |
| User or upstream requests explanation | LLM can generate natural-language reason |

### 6.2 LLM output override rules

- **LLM must NOT override a code-first Top 1** when code-first confidence ≥ 0.6.
- When code-first confidence < 0.6, LLM output may replace the Top1, provided
  the selected syndrome exists in the KB.
- LLM must never invent syndromes, formulas, or herbs.
- LLM output is always merged with code-first `candidate_scores` if available.

---

## 7. Output Format

### 7.1 M2-1 Output — Syndrome Trace (internal production format)

```json
{
  "stage": "M2_1",
  "status": "PASS",
  "primary_disease": "西医主病名",
  "syndrome_trace": {
    "syndrome_name": "选证型名称",
    "confidence": 0.5,
    "matched_symptoms": [],
    "matched_pathology": "",
    "matched_tongue_pulse": "",
    "missing_info": "",
    "candidate_scores": [
      {
        "syndrome_name": "证型名",
        "score": 30.0,
        "confidence": 0.5,
        "matched_symptoms": [],
        "matched_pathology": "",
        "matched_tongue_pulse": "",
        "symptom_match": 24.0,
        "tongue_match": 3.0,
        "pulse_match": 0.0,
        "pathology_match": 3.0,
        "cold_heat_match": 0.0
      }
    ],
    "reasoning_summary": "简要选证依据"
  },
  "input_trace": { /* resolved_key, symptoms_received, stage */ },
  "formal_prescription_allowed": false
}
```

**Rules:**
- `candidate_scores` and `matched_*` fields are **internal/audit only**.
- **Production display** must show only: syndrome_name, reasoning_summary,
  whether manual review is needed. All `candidate_scores` and internal fields
  must be filtered out before rendering to the end user.

### 7.2 M2-2 Output — Formula Candidates

```json
{
  "stage": "M2_2",
  "status": "PASS",
  "candidate_only": true,
  "formula_candidates": [
    {
      "disease_name": "病名",
      "syndrome_name": "证型名",
      "formula_name": "方剂名",
      "herbs": ["药1", "药2"],
      "source": "data/m2_formula_knowledge.json"
    }
  ]
}
```

### 7.3 M2-3 Output — Modification Candidates

```json
{
  "stage": "M2_3",
  "status": "PASS",
  "modification_candidates": [
    {
      "herb_name": "药名",
      "target_disease": "目标病名",
      "target_symptom": "目标症状",
      "evidence_sources": ["来源1"]
    }
  ]
}
```

---

## 8. Prohibited

### ❌ Explicitly Forbidden (v2)

1. **LLM must not act as primary syndrome judge.** Code scorer is primary.
2. **LLM must not invent KB-outside syndromes, formulas, or herbs.**
3. **M2-1 must not output formula/herbs/dosage/prescription text.**
4. **M2-1 must not render candidate_scores into production display text.**
   Internal/debug fields are for audit only.
5. **Must not override high-confidence code-first Top1 with LLM output.**
6. **Must not skip syndrome loading.** All syndromes under the resolved
   disease key must be loaded and scored.
7. **Must not disable code-first scorer when API is unavailable.**

### ✅ Explicitly Allowed (v2)

1. ✅ Code-first multi-dimensional scoring with internal trace fields.
2. ✅ `confidence`, `candidate_scores`, `evidence_trace` in internal output.
3. ✅ `matched_symptoms`, `matched_pathology`, `matched_tongue_pulse` in trace.
4. ✅ `resolve_m2_disease_key` multi-strategy fallback (exact → DISEASE_NAME_MAP
   → m2_fallback → substring → clean parentheses).
5. ✅ Negation-aware cold/heat detection.
6. ✅ LLM fallback only when confidence low or tie-breaking needed.
7. ✅ Code-first scorer runs with or without API.
8. ✅ M2-2 independently outputs formula candidates.
9. ✅ M2-3 independently outputs modification candidates.

---

## 9. Version History

| Version | Date | Changes |
|---------|------|---------|
| v2 | 2026-06-06 | Initial code-first spec. Supersedes v1 prompt-first design. |
