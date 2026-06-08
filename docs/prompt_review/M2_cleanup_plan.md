# M2 代码清理计划（冻结候选对齐）

> 目标：删除 M2 中 dead/legacy/redundant/与 Skill 冲突 的代码，使 **M2 代码 / Skill 文档 / 测试** 三者完全一致。
> Skill 来源（source of truth）：`docs/prompt_review/M2_skill_code_synced_for_review.md`
> 原则：保守。每条删除至少满足一条删除依据；存疑则保留并注明原因。

---

## 0. 前置发现：基线并非全绿（与本次清理无关）

在**未做任何改动**前，工作区基线已有失败，根因是 **out-of-scope 文件** `data/m2_formula_knowledge.json` 在工作区被破坏（未提交改动）：

- HEAD 版本：929 个疾病 key，含精确 `肺炎` key，3213 证型中 2918 个有 `oral_formula_master_table` source。
- 工作区版本：625 个疾病 key，**无精确 `肺炎` key**（仅 `肺炎 (Pneumonia)`/`儿童肺炎`/`支原体肺炎`），620 证型 **0 个**有 source 标记。

由此导致的**既存失败（非本次清理引入）**：

| 失败项 | 类型 | 根因 |
|--------|------|------|
| `test_resolve_m2_disease_key_exact_match` | 单测 | `肺炎` 精确 key 缺失 |
| `test_resolve_m2_disease_key_pneumonia` | 单测 | 同上 |
| `test_pneumonia_heat_phlegm_correctly_identified` | 单测 | `肺炎` 误解析到其它肺炎 key，证型池错 |
| `test_disease_name_map_alignment` | 单测 | DISEASE_NAME_MAP value 不在被裁剪的 KB 中 |
| `test_oral_formula_source_marked` | 单测 | source 标记被裁剪 |
| production_acceptance「肺炎痰热」 | 验收 | `肺炎` 误解析，选到「风寒闭肺」 |

**结论**：该文件**不在本次允许修改范围**（scope 仅含 `data/m2_knowledge/`、`data/m2_knowledge_staging/`），且属于无关的未提交中间态。本次清理**不修改**该文件。验证本次代码改动是否引入回归时，临时将该文件切回 HEAD 版本进行对照，验证后**原样还原**。最终建议用户将其恢复/重建（见报告）。

---

## 1. 删除/修改清单

| 文件 | 拟删除/修改内容 | 原因 | 对应 Skill 条款 | 风险 | 是否需要测试覆盖 |
|------|----------------|------|----------------|------|-----------------|
| `m2_engine.py` | 删除方法 `_with_candidate_contract()`（约 L1045–1081） | 全仓库无任何调用方（grep 仅命中定义），dead code | 不在 Skill §1 固定链路；§13 输出由 `process`/`run_m2_*` 直接组装 | 低（无调用方，无测试引用） | 否（删除后跑全量回归） |
| `m2_engine.py` | 删除方法 `_fetch_reference_case_from_llm()`（约 L1882–1966） | 全仓库无任何调用方（仅文档引用），且为 LLM 自由查网病案逻辑，与 Skill 的固定 code-first 链路冲突（§1、§11 M2-3 三级策略不含"LLM 查网"） | 与 §1/§11 冲突；§15 LLM 仅可选离线隔离 | 低（无调用方，无测试引用） | 否 |
| `m2_engine.py` | 删除方法 `_fallback_full()`（约 L2125–2159） | 全仓库无任何调用方，dead code（M2-2 用的是 `_fallback_parse`） | §9.2 仅保留 `_fallback_parse`；`_fallback_full` 不在链路 | 低（无调用方） | 否 |
| `m2_engine.py` | 重写模块顶部 docstring（L1–9）：删除「提示词驱动，LLM 深度参与」与「已删除 M2_CODE_REVERSE_PROMPT.md」的失实注释 | 与 Skill 冲突（Skill 为 code-first 固定双轨链路，LLM 离线隔离/可选）；且「已删除」注释失实（文件实际仍在） | §1 固定链路、§15 LLM 离线隔离 | 极低（仅注释） | 否 |
| `m2_engine.py` | 更新类 docstring（L19–25）「流程：LLM 单次调用…」为真实固定链路描述 | 同上，描述与实际 code-first 双轨不符 | §1、§6 双轨 | 极低（仅注释） | 否 |
| `prompts/m2/M2_CODE_REVERSE_PROMPT.md` | 删除整个文件 | 旧 prompt-first/reverse 设计：引用已废弃服务（`m2_pathology_syndrome_bridge`/`m2_pattern_match_engine` 等）、含 LLM 自由加减 fallback、输出 schema（`candidate_syndromes`/`formula_selection`/`follow_up_decision`/`guardrail`）与现行 M2-1/2/3 合同冲突；`PROMPT_REVIEW_INDEX.md` 标记为 ❌ DELETED，代码注释亦称已删 | Skill 序言「废弃旧 prompt 内容不再保留」；与 §1/§13 冲突 | 低（无代码加载该文件，仅注释引用） | 否 |
| `prompts/m2/m2_case_search_rule.md` | 删除其中 `_fetch_reference_case_from_llm()` 分支描述（L8、L22–26），保留 `_search_cases`/`_generate_modifications`（仍被 `process()` 使用） | 该函数本次被删除，文档需同步去除失效分支 | §11 病案边界 | 极低（仅文档） | 否 |

---

## 2. 明确保留（含原因）

| 保留项 | 原因 |
|--------|------|
| `process()` | 被 `bridge_server.py`（3 处）及多个测试调用，兼容入口，必须保留 |
| `full_pipeline()` / `_extract_dosages()` / `_generate_evidence_based_modifications()` | legacy guard（`ALLOW_LEGACY_*` 环境变量），Skill §15.1 明确文档化；被 `test_legacy_prescription_*` 守卫测试覆盖 |
| `_generate_modifications()` / `_search_cases()` / `_normalize_modification_candidates()` | 被 `process()` 与测试 `test_m1_m4_prompt_code_alignment` 使用 |
| `_build_prompt()` / `_build_herb_reference()` / `_parse_llm_result()` / `_call_llm()` / `_llm_available()` | Skill §8.1/§15 描述的 LLM 可选路径；`M2_DISABLE_LLM` 测试隔离依赖 |
| `_auto_fill_herbs_from_decoction()` | `__init__` 调用，数据装载期补全 herbs，核心 |
| `prompts/m2/M2_SOURCE_PROMPT.md` | `PROMPT_REVIEW_INDEX.md` 标记为 ⛔ DEPRECATED（**非** DELETED），保留为历史审查稿；无代码运行期加载 |
| `data/m2_formula_knowledge.json` | out-of-scope；不修改 |
| `services/m2_symptom_factor_loader.py` 全部 | 与 Skill §4/§5 完全一致，无 dead code |
| 所有现行通过的 M2 nail-case 测试期望 | 不弱化、不修改测试期望 |

---

## 3. 删除依据满足性自检

每条删除均满足「至少一条」：

- `_with_candidate_contract` / `_fallback_full`：**当前代码未调用**（grep 全仓库验证）。
- `_fetch_reference_case_from_llm`：**未调用** + **与 Skill 冲突**（LLM 自由查网，不在固定链路）。
- `M2_CODE_REVERSE_PROMPT.md`：**与 Skill 冲突** + **已被 v2 code-first 取代** + 仓库自身 review index 标记 DELETED。
- docstring/文档修改：**与 Skill 冲突**（描述失实/prompt-first）。
