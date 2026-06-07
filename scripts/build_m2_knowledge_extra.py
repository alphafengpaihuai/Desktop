#!/usr/bin/env python3
"""
Build additional M2 knowledge structures from new backup files.
"""
import os, sys, json, re
import pandas as pd

RAW_DIR = "data/raw/knowledge_backup"
OUT_DIR = "data/m2_knowledge"
os.makedirs(OUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 1. 名医案例库 → m2_case_reference.json
# ═══════════════════════════════════════════════════════════════
def build_case_reference():
    fpath = os.path.join(RAW_DIR, "名医案例库_逻辑链tag.xlsx")
    if not os.path.exists(fpath):
        print("[SKIP] 名医案例库 not found")
        return False

    # Read all at once (88k rows is fine for memory)
    df = pd.read_excel(fpath, sheet_name=0)
    total = len(df)
    print(f"[READ] 名医案例库: {total} rows")

    records = []
    for _, row in df.iterrows():
        disease = str(row.get("病名", "")).strip() if pd.notna(row.get("病名")) else ""
        syndrome = str(row.get("证型", "")).strip() if pd.notna(row.get("证型")) else ""
        formula = str(row.get("方名", "")).strip() if pd.notna(row.get("方名")) else ""
        herbs = str(row.get("组成", "")).strip() if pd.notna(row.get("组成")) else ""
        logic = str(row.get("逻辑链（涵盖症状）", "")).strip() if pd.notna(row.get("逻辑链（涵盖症状）")) else ""
        symptoms = str(row.get("症状；仅作为侧边栏检索，不进入向量库", "")).strip() if pd.notna(row.get("症状；仅作为侧边栏检索，不进入向量库")) else ""
        source_text = str(row.get("病案原文：仅作为侧边栏检索，不进入向量库", "")).strip() if pd.notna(row.get("病案原文：仅作为侧边栏检索，不进入向量库")) else ""
        gender = str(row.get("性别", "")).strip() if pd.notna(row.get("性别")) else ""
        age = str(row.get("年龄", "")).strip() if pd.notna(row.get("年龄")) else ""

        if not disease and not formula:
            continue

        record = {
            "disease_name": disease,
            "gender": gender,
            "age": age,
            "syndrome": syndrome,
            "formula_name": formula,
            "herbs": herbs,
            "logic_chain": logic,
            "symptoms": symptoms,
            "source_text": source_text[:200] if source_text else "",
        }
        records.append(record)

    out = os.path.join(OUT_DIR, "m2_case_reference.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_case_reference.json: {len(records)} entries")
    return True


# ═══════════════════════════════════════════════════════════════
# 2. 古今名医方论 → m2_formula_enhancement.json
# ═══════════════════════════════════════════════════════════════
def build_formula_enhancement():
    fpath = os.path.join(RAW_DIR, "古今名医方论  插入逻辑链 完成.xlsx")
    if not os.path.exists(fpath):
        print("[SKIP] 古今名医方论 not found")
        return False

    df = pd.read_excel(fpath, sheet_name=0)
    total = len(df)
    print(f"[READ] 古今名医方论: {total} rows")

    records = []
    for _, row in df.iterrows():
        herbs = str(row.get("用药", "")).strip() if pd.notna(row.get("用药")) else ""
        diagnosis = str(row.get("诊断", "")).strip() if pd.notna(row.get("诊断")) else ""
        symptoms = str(row.get("症状", "")).strip() if pd.notna(row.get("症状")) else ""
        logic = str(row.get("逻辑链", "")).strip() if pd.notna(row.get("逻辑链")) else ""
        source = str(row.get("来源 ：仅作为侧边栏检索，不进入向量库", "")).strip() if pd.notna(row.get("来源 ：仅作为侧边栏检索，不进入向量库")) else ""
        author = str(row.get("作者 ：仅作为侧边栏检索，不进入向量库", "")).strip() if pd.notna(row.get("作者 ：仅作为侧边栏检索，不进入向量库")) else ""
        original = str(row.get("原文 ：仅作为侧边栏检索，不进入向量库", "")).strip() if pd.notna(row.get("原文 ：仅作为侧边栏检索，不进入向量库")) else ""

        if not herbs and not diagnosis:
            continue

        # Parse diagnosis field which may contain multiple diagnoses
        diag_list = [d.strip() for d in diagnosis.replace(",", "；").split("；") if d.strip()]

        record = {
            "raw_diagnosis": diagnosis,
            "diagnosis_list": diag_list,
            "symptoms": symptoms,
            "herbs": herbs,
            "logic_chain": logic,
            "source": source,
            "author": author,
            "original_text": original[:200] if original else "",
        }
        records.append(record)

    out = os.path.join(OUT_DIR, "m2_formula_enhancement.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_formula_enhancement.json: {len(records)} entries")
    return True


# ═══════════════════════════════════════════════════════════════
# 3. 600疾病诊断标准.txt → m2_disease_diagnostic_criteria.json
# ═══════════════════════════════════════════════════════════════
def build_disease_diagnostic_criteria():
    fpath = os.path.join(RAW_DIR, "600  疾病诊断标准.txt")
    if not os.path.exists(fpath):
        print("[SKIP] 600疾病诊断标准.txt not found")
        return False

    with open(fpath) as f:
        data = json.load(f)

    print(f"[READ] 600疾病诊断标准: {len(data)} diseases")

    records = []
    for item in data:
        dn = item.get("disease_name", "")
        if not dn:
            continue
        record = {
            "disease_name": dn,
            "aliases": item.get("aliases", ""),
            "entry_type": item.get("entry_type", ""),
            "m1_diagnosis_entry_allowed": item.get("m1_diagnosis_entry_allowed", False),
            "diagnostic_criteria": item.get("diagnostic_criteria_or_key_points", ""),
            "typical_symptoms": item.get("typical_symptoms", ""),
            "atypical_symptoms": item.get("atypical_or_special_symptoms", ""),
            "course_and_stage": item.get("course_and_stage", ""),
            "pathophysiology": item.get("pathophysiology", ""),
            "rule_in_features": item.get("rule_in_features", ""),
            "rule_out_features": item.get("rule_out_features", ""),
            "red_flags": item.get("red_flags", ""),
            "potential_risks": item.get("potential_risks", ""),
            "required_checks": item.get("required_checks", ""),
            "differential_diagnoses": item.get("differential_diagnoses", ""),
            "source_urls": item.get("source_urls", ""),
            "source_quality": item.get("source_quality", ""),
            "evidence_level": item.get("evidence_level", ""),
        }
        records.append(record)

    out = os.path.join(OUT_DIR, "m2_disease_diagnostic_criteria.json")
    with open(out, "w") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_disease_diagnostic_criteria.json: {len(records)} entries")
    return True


# ═══════════════════════════════════════════════════════════════
# 4. 600药物成分功效.txt → m2_herb_pharmacology_map.json
# ═══════════════════════════════════════════════════════════════
def build_herb_pharmacology():
    fpath = os.path.join(RAW_DIR, "600 药物成分功效.txt")
    if not os.path.exists(fpath):
        print("[SKIP] 600药物成分功效.txt not found")
        return False

    with open(fpath) as f:
        data = json.load(f)

    records = data.get("herb_records", [])
    print(f"[READ] 600药物成分功效: {len(records)} herb records")

    # Normalize
    normalized = []
    for item in records:
        hn = item.get("herb_name", "")
        if not hn:
            continue
        normalized.append({
            "herb_name": hn,
            "pinyin_name": item.get("pinyin_name", ""),
            "latin_name": item.get("latin_name", ""),
            "properties": item.get("properties", ""),
            "meridians": item.get("meridians", ""),
            "functions": item.get("functions", ""),
            "clinical_application": item.get("clinical_application", ""),
            "pharmacology": item.get("pharmacology", ""),
            "key_components": item.get("key_components", ""),
            "toxicity": item.get("toxicity", ""),
            "cautions": item.get("cautions", ""),
            "dosage": item.get("dosage", ""),
            "evidence_confidence": item.get("evidence_confidence", ""),
            "extraction_quality": item.get("extraction_quality", ""),
        })

    out = os.path.join(OUT_DIR, "m2_herb_pharmacology_map.json")
    with open(out, "w") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    print(f"[OK] m2_herb_pharmacology_map.json: {len(normalized)} herbs")
    return True


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Building Additional M2 Knowledge from New Backup Files")
    print("=" * 60)
    print()

    ok1 = build_case_reference()
    ok2 = build_formula_enhancement()
    ok3 = build_disease_diagnostic_criteria()
    ok4 = build_herb_pharmacology()

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  m2_case_reference.json:             {'OK' if ok1 else 'SKIP'}")
    print(f"  m2_formula_enhancement.json:         {'OK' if ok2 else 'SKIP'}")
    print(f"  m2_disease_diagnostic_criteria.json: {'OK' if ok3 else 'SKIP'}")
    print(f"  m2_herb_pharmacology_map.json:       {'OK' if ok4 else 'SKIP'}")
