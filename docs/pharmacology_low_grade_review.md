# 低等级药理线索人工复核报告

本报告仅用于人工复核低等级药理线索，不属于正式药理库，不得用于 M2 候选方生成、M2 加减药生成或处方推荐。

- 候选总数：3
- 允许用途：manual_review_only
- 禁止流向：formal_pharmacology_db, m2_formula_candidate, m2_modification_candidate, prescription_generation

## 乳腺癌 / Breast cancer

### glycyrrhetinic acid -> 甘草

- evidence_grade: low
- chain_status: closed
- evidence_context: cell
- review_status: pending_human_review
- allowed_usage: manual_review_only
- blocked_from: formal_pharmacology_db, m2_formula_candidate, m2_modification_candidate, prescription_generation
- matched_pmids: 37011418
- matched_targets: IL-10
- matched_outcomes: inhibit, suppress

matched_titles:
- Glycyrrhetinic acid suppresses breast cancer metastasis by inhibiting M2-like macrophage polarization via activating JNK1/2 signaling.

why_not_formal:

单篇 PMID 内可形成 disease-component-target/outcome 闭链，但只命中 1 个 miner 靶点，且属于癌症机制/模型证据；不可作为正式药理证据或处方候选来源。

### saikosaponin A -> 柴胡

- evidence_grade: low
- chain_status: closed
- evidence_context: animal
- review_status: pending_human_review
- allowed_usage: manual_review_only
- blocked_from: formal_pharmacology_db, m2_formula_candidate, m2_modification_candidate, prescription_generation
- matched_pmids: 31214035
- matched_targets: IL-10
- matched_outcomes: inhibit

matched_titles:
- Saikosaponin A Inhibits Breast Cancer by Regulating Th1/Th2 Balance.

why_not_formal:

同一 PMID 内可形成 Breast cancer + saikosaponin A + immune target/outcome 的闭链，但只命中 1 个 miner 靶点；证据偏机制/动物模型，不能进入正式药理库或处方链路。

### bruceine D -> 鸦胆子

- evidence_grade: low_medium
- chain_status: closed
- evidence_context: cell
- review_status: pending_human_review
- allowed_usage: manual_review_only
- blocked_from: formal_pharmacology_db, m2_formula_candidate, m2_modification_candidate, prescription_generation
- matched_pmids: 38043386
- matched_targets: IL6, TNF
- matched_outcomes: inhibit, suppress

matched_titles:
- Bruceine D suppresses CAF-promoted TNBC metastasis under TNF-α stimulation by inhibiting Notch1-Jagged1/NF-κB(p65) signaling.

why_not_formal:

同一 PMID 覆盖 component、TNF/IL6 与 outcome，但属于 TNBC 机制/细胞证据；最多作为 low_medium 人工留档，不能写入正式药理库或生成推荐药物。
