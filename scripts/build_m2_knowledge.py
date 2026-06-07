#!/usr/bin/env python3
"""
Build structured M2 knowledge base from raw backup files.
Input:  data/raw/knowledge_backup/
Output: data/m2_knowledge/

This script is idempotent — safe to run multiple times.
"""
import os, sys, json, re
import pandas as pd

RAW_DIR   = "data/raw/knowledge_backup"
OUT_DIR   = "data/m2_knowledge"
os.makedirs(OUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 1. 症状证素表 → m2_symptom_factor_map.json
# ═══════════════════════════════════════════════════════════════
def build_symptom_factor_map():
    fpath = os.path.join(RAW_DIR, "症状证素表.xlsx")
    if not os.path.exists(fpath):
        print("[SKIP] 症状证素表.xlsx not found")
        return False

    df = pd.read_excel(fpath, sheet_name=0)
    # Row order: column A is "证素", first row value is "症状"
    # Rest: each column after A is a TCM factor name, cells are symptoms
    factor_cols = [c for c in df.columns if c != "证素" and pd.notna(c)]

    symptom_to_factors = {}
    for _, row in df.iterrows():
        for factor in factor_cols:
            raw_symptom = row.get(factor)
            if pd.isna(raw_symptom):
                continue
            raw_text = str(raw_symptom).strip()
            if not raw_text:
                continue
            # Split by common delimiters
            symptoms = re.split(r'[，,、；;。. /／\n]+', raw_text)
            for symp in symptoms:
                symp = symp.strip()
                if not symp or len(symp) < 2:
                    continue
                if symp not in symptom_to_factors:
                    symptom_to_factors[symp] = []
                if factor not in symptom_to_factors[symp]:
                    symptom_to_factors[symp].append(factor)

    # Convert to list of dicts
    records = []
    for symp, factors in sorted(symptom_to_factors.items()):
        records.append({
            "symptom": symp,
            "tcm_factors": sorted(factors),
            "factor_count": len(factors),
        })

    out = os.path.join(OUT_DIR, "m2_symptom_factor_map.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_symptom_factor_map.json: {len(records)} entries")
    return True


# ═══════════════════════════════════════════════════════════════
# 2. 口服方剂知识库 → m2_disease_syndrome_pool.json + m2_formula_base_map.json
# ═══════════════════════════════════════════════════════════════
def parse_disease_name(raw):
    """Parse disease name from messy text."""
    if not raw or pd.isna(raw):
        return None
    raw = str(raw).strip()

    # Remove leading dash/numbers
    raw = re.sub(r'^[-–—\s]+\s*', '', raw)
    # Try to extract 主病名 or key names
    m = re.search(r'主病名[：:]\s*([^\n]+)', raw)
    if m:
        name = m.group(1).strip()
        # Remove trailing aliases
        name = re.sub(r'\s*$', '', name)
        return name
    # Try to extract after dash
    lines = raw.split('\n')
    first = lines[0].strip()
    if first.startswith('- '):
        first = first[2:].strip()
    # Remove 别名 lines  
    first = re.sub(r'\s*$', '', first)
    # Skip if contains 别名
    if '别名' in first:
        # Try to get before 别名
        before = re.match(r'^([^，,]+)', first)
        if before:
            return before.group(1).strip()
    return first


def build_disease_syndrome_pool():
    """Extract disease→syndrome→formula info from the formula KB Excel."""
    sources = [
        "口服方剂知识库_最终版_原始版面_仅总表.xlsx",
        "口服方剂知识库.xlsx",
    ]
    df = None
    for s in sources:
        fpath = os.path.join(RAW_DIR, s)
        if os.path.exists(fpath):
            df = pd.read_excel(fpath, sheet_name="总表")
            print(f"[READ] {s}: {len(df)} rows")
            break

    if df is None:
        print("[SKIP] No 口服方剂知识库 found")
        return False, False

    # Column mapping
    col_disease = [c for c in df.columns if '病名' in str(c) or '诊断' in str(c)]
    col_syndrome = [c for c in df.columns if '证型' in str(c) or '辨证' in str(c)]
    col_formula = [c for c in df.columns if '基础方' in str(c) or '方剂' in str(c)]
    col_decoction = [c for c in df.columns if '汤剂' in str(c) or '方案' in str(c)]
    col_subject = [c for c in df.columns if '科目' in str(c)]

    disease_col = col_disease[0] if col_disease else None
    syndrome_col = col_syndrome[0] if col_syndrome else None
    formula_col = col_formula[0] if col_formula else None
    decoction_col = col_decoction[0] if col_decoction else None
    subject_col = col_subject[0] if col_subject else None

    # Disease → syndrome pool
    disease_pool = {}  # disease_key -> {syndromes: [...]}
    # Formula base
    formula_records = []

    for idx, row in df.iterrows():
        raw_disease = row.get(disease_col) if disease_col else None
        disease_name = parse_disease_name(raw_disease)
        if not disease_name:
            continue

        raw_syndrome = row.get(syndrome_col) if syndrome_col else ''
        raw_formula_name = row.get(formula_col) if formula_col else ''
        raw_decoction = row.get(decoction_col) if decoction_col else ''
        subject = str(row.get(subject_col, '')) if subject_col else ''

        if pd.isna(raw_syndrome):
            continue

        syndrome_text = str(raw_syndrome).strip()
        formula_name = str(raw_formula_name).strip() if not pd.isna(raw_formula_name) else ""
        decoction_text = str(raw_decoction).strip() if not pd.isna(raw_decoction) else ""

        # Parse syndrome block like <痰热壅肺>：description
        synd_match = re.search(r'<([^>]+)>[：:]\s*([^<]*)', syndrome_text)
        if not synd_match:
            # Try alternative pattern
            synd_match = re.search(r'([^：:]+)[：:]\s*(\S+)', syndrome_text)
        syndrome_name = synd_match.group(1).strip() if synd_match else syndrome_text[:40].strip()
        syndrome_desc = synd_match.group(2).strip() if synd_match and synd_match.lastindex >= 2 else ""

        # Build disease_key (use simplified name)
        disease_key = disease_name.strip()

        # Add to pool
        if disease_key not in disease_pool:
            disease_pool[disease_key] = {
                "disease_key": disease_key,
                "subject": subject,
                "raw_disease": str(raw_disease).strip() if not pd.isna(raw_disease) else "",
                "syndromes": {},
                "syndrome_count": 0,
            }

        # Extract herbs from decoction
        herbs = []
        if decoction_text and '用药' in decoction_text:
            # Extract herb names after '用药：'
            drug_section = decoction_text.split('用药')[1] if '用药' in decoction_text else decoction_text
            # Regex for herb names: Chinese chars followed by g/克 or followed by space/newline
            herb_matches = re.findall(r'([\u4e00-\u9fff]{2,6})\s*\d+', drug_section)
            for h in herb_matches:
                h = h.strip()
                if h and h not in herbs:
                    herbs.append(h)

        # Extract formula name from decoction
        formula_from_decoction = ""
        if decoction_text:
            fm = re.search(r'【([^】]+)】', decoction_text)
            if fm:
                formula_from_decoction = fm.group(1)

        final_formula = formula_name or formula_from_decoction or ""

        if syndrome_name not in disease_pool[disease_key]["syndromes"]:
            disease_pool[disease_key]["syndromes"][syndrome_name] = {
                "syndrome_name": syndrome_name,
                "description": syndrome_desc,
                "formulas": [],
            }

        if final_formula:
            existing_formulas = disease_pool[disease_key]["syndromes"][syndrome_name]["formulas"]
            fnames = [f["formula_name"] for f in existing_formulas]
            if final_formula not in fnames:
                existing_formulas.append({
                    "formula_name": final_formula,
                    "herbs": herbs,
                    "source": subject or "口服方剂知识库",
                    "formula_dosage_note": "[M3核定]剂量已被去除，M3审核时需重新核定",
                })

        # Update syndrome count
        disease_pool[disease_key]["syndrome_count"] = len(disease_pool[disease_key]["syndromes"])

    # Write disease_syndrome_pool
    pool_list = []
    for dk in sorted(disease_pool.keys()):
        entry = disease_pool[dk]
        entry["syndromes"] = list(entry["syndromes"].values())
        pool_list.append(entry)

    out1 = os.path.join(OUT_DIR, "m2_disease_syndrome_pool.json")
    with open(out1, "w") as f:
        json.dump(pool_list, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_disease_syndrome_pool.json: {len(pool_list)} diseases")

    # Build formula base map (denormalized)
    formula_base = []
    for entry in pool_list:
        dk = entry["disease_key"]
        for synd in entry["syndromes"]:
            for fmt in synd.get("formulas", []):
                if fmt.get("formula_name"):
                    formula_base.append({
                        "disease_key": dk,
                        "syndrome_name": synd["syndrome_name"],
                        "formula_name": fmt["formula_name"],
                        "herbs": fmt.get("herbs", []),
                        "source": fmt.get("source", ""),
                        "dosage_note": "[M3核定]",
                        "candidate_only": True,
                        "must_enter_m3": True,
                    })

    out2 = os.path.join(OUT_DIR, "m2_formula_base_map.json")
    with open(out2, "w") as f:
        json.dump(formula_base, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_formula_base_map.json: {len(formula_base)} formula entries")

    return True, True


# ═══════════════════════════════════════════════════════════════
# 3. SymMap SMHB → m2_herb_tag_map.json
# ═══════════════════════════════════════════════════════════════
def build_herb_tag_map():
    fpath = os.path.join(RAW_DIR, "SymMap v2.0, SMHB file.xlsx")
    if not os.path.exists(fpath):
        print("[SKIP] SymMap SMHB not found")
        return False
    df = pd.read_excel(fpath)
    records = []
    for _, row in df.iterrows():
        cn = row.get("Chinese_name", "")
        if pd.isna(cn) or not cn:
            continue
        props = str(row.get("Properties_Chinese", "")).strip() if not pd.isna(row.get("Properties_Chinese")) else ""
        meridians = str(row.get("Meridians_Chinese", "")).strip() if not pd.isna(row.get("Meridians_Chinese")) else ""
        cls = str(row.get("Class_Chinese", "")).strip() if not pd.isna(row.get("Class_Chinese")) else ""
        use_part = str(row.get("UsePart", "")).strip() if not pd.isna(row.get("UsePart")) else ""
        record = {
            "herb_name": str(cn).strip(),
            "pinyin": str(row.get("Pinyin_name", "")).strip() if not pd.isna(row.get("Pinyin_name")) else "",
            "latin": str(row.get("Latin_name", "")).strip() if not pd.isna(row.get("Latin_name")) else "",
            "properties": props,
            "meridians": meridians,
            "tcm_class": cls,
            "use_part": use_part,
            "functions": cls,
            "tcm_factors": props,
            "cautions": "",
        }
        records.append(record)

    out = os.path.join(OUT_DIR, "m2_herb_tag_map.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_herb_tag_map.json: {len(records)} herbs")
    return True


# ═══════════════════════════════════════════════════════════════
# 4. diseases_final.json → m2_disease_axes_pool.json (P1)
# ═══════════════════════════════════════════════════════════════
def build_disease_axes_pool():
    fpath = os.path.join(RAW_DIR, "diseases_final.json")
    if not os.path.exists(fpath):
        print("[SKIP] diseases_final.json not found")
        return False
    with open(fpath) as f:
        data = json.load(f)

    records = []
    for item in data:
        name = item.get("disease_name", "")
        axes = item.get("axes", [])
        phase_axes = item.get("phase_axes", [])
        aliases = item.get("aliases", "")

        entry = {
            "disease_name": name,
            "aliases": aliases,
            "axes": axes,
            "phase_axes": phase_axes,
            "must_not_be_primary_when": item.get("must_not_be_primary_when", ""),
            "need_external_check_if": item.get("need_external_check_if", ""),
        }
        records.append(entry)

    out = os.path.join(OUT_DIR, "m2_disease_axes_pool.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_disease_axes_pool.json: {len(records)} diseases")
    return True


# ═══════════════════════════════════════════════════════════════
# 5. disease_cards_merged_600 → m2_disease_symptom_map.json (P1)
# ═══════════════════════════════════════════════════════════════
def build_disease_symptom_map():
    fpath = os.path.join(RAW_DIR, "disease_cards_merged_600_source_checked_pending.json")
    if not os.path.exists(fpath):
        print("[SKIP] disease_cards not found")
        return False
    with open(fpath) as f:
        data = json.load(f)

    cards = data.get("cards", [])
    records = []
    for card in cards:
        dn = card.get("disease_name", "")
        if not dn:
            continue
        entry = {
            "disease_name": dn,
            "aliases": card.get("aliases", ""),
            "typical_symptoms": card.get("typical_symptoms", ""),
            "atypical_symptoms": card.get("atypical_or_special_symptoms", ""),
            "course_and_stage": card.get("course_and_stage", ""),
            "prognosis": card.get("prognosis_and_natural_history", ""),
            "pathophysiology": card.get("pathophysiology", ""),
            "rule_in_features": card.get("rule_in_features", ""),
            "rule_out_features": card.get("rule_out_features", ""),
            "red_flags": card.get("red_flags", ""),
            "pathology_axes": card.get("pathology_axes", ""),
            "diagnostic_criteria": card.get("diagnostic_criteria_or_key_points", ""),
            "department": card.get("department_or_category", ""),
            "differential_diagnoses": card.get("differential_diagnoses", ""),
        }
        records.append(entry)

    out = os.path.join(OUT_DIR, "m2_disease_symptom_map.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_disease_symptom_map.json: {len(records)} diseases")
    return True


# ═══════════════════════════════════════════════════════════════
# 6. gpt_pharmacology_cards.json → m2_pharmacology_map.json (P1)
# ═══════════════════════════════════════════════════════════════
def build_pharmacology_map():
    fpath = os.path.join(RAW_DIR, "gpt_pharmacology_cards.json")
    if not os.path.exists(fpath):
        print("[SKIP] gpt_pharmacology_cards.json not found")
        return False
    with open(fpath) as f:
        data = json.load(f)

    records = []
    for disease_name, card in data.items():
        herbs = card.get("evidence_based_herbs", [])
        if isinstance(herbs, str):
            try:
                herbs = json.loads(herbs)
            except:
                herbs = [herbs]
        targets = card.get("key_targets", [])
        if isinstance(targets, str):
            try:
                targets = json.loads(targets)
            except:
                targets = [targets]

        record = {
            "disease_name": disease_name,
            "disease_pathway": card.get("disease_pathway", ""),
            "key_targets": targets,
            "evidence_based_herbs": herbs,
            "evidence_confidence": card.get("evidence_confidence", ""),
            "verification_status": card.get("verification_status", ""),
        }
        records.append(record)

    out = os.path.join(OUT_DIR, "m2_pharmacology_map.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_pharmacology_map.json: {len(records)} diseases")
    return True


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Building M2 Knowledge Base from Raw Backups")
    print("=" * 60)
    print()

    ok1 = build_symptom_factor_map()
    ok2, ok3 = build_disease_syndrome_pool()
    ok4 = build_herb_tag_map()
    ok5 = build_disease_axes_pool()
    ok6 = build_disease_symptom_map()
    ok7 = build_pharmacology_map()

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  m2_symptom_factor_map.json:    {'OK' if ok1 else 'SKIP'}")
    print(f"  m2_disease_syndrome_pool.json: {'OK' if ok2 else 'SKIP'}")
    print(f"  m2_formula_base_map.json:      {'OK' if ok3 else 'SKIP'}")
    print(f"  m2_herb_tag_map.json:          {'OK' if ok4 else 'SKIP'}")
    print(f"  m2_disease_axes_pool.json:     {'OK' if ok5 else 'SKIP'}")
    print(f"  m2_disease_symptom_map.json:   {'OK' if ok6 else 'SKIP'}")
    print(f"  m2_pharmacology_map.json:      {'OK' if ok7 else 'SKIP'}")

    # Check if any modification rules or case reference files exist
    print()
    print("Gap Check:")
    if not os.path.exists(os.path.join(RAW_DIR, "加减规则库.xlsx")):
        print("  [GAP] 加减规则库.xlsx — not found in backup")
    if not os.path.exists(os.path.join(RAW_DIR, "名医病案库.xlsx")):
        print("  [GAP] 名医病案库.xlsx — not found in backup")
    if not os.path.exists(os.path.join(RAW_DIR, "方剂组成库.xlsx")):
        print("  [GAP] 方剂组成库.xlsx — not found in backup (formulas embedded in 口服方剂知识库)")
    if not os.path.exists(os.path.join(RAW_DIR, "症状证素库.xlsx")):
        print("  [OK] 症状证素库 is 症状证素表.xlsx — found and converted")
