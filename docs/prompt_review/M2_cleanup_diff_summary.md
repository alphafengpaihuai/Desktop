# M2 代码清理 Diff 摘要（冻结候选）

> 配套：`docs/prompt_review/M2_cleanup_plan.md`
> 仅统计本次清理的实际改动（不含此前已存在于工作区的无关未提交改动）。
> `m2_engine.py`：清理前 2982 行 → 清理后 2826 行（净 −156 行）。

---

## 1. 实际改动表

| 文件 | 实际改动 | 删除行数 | 新增行数 | 保留原因 / 备注 |
|------|---------|---------|---------|----------------|
| `m2_engine.py` | 删除 `_with_candidate_contract()`（dead，无调用方） | ~38 | 0 | — |
| `m2_engine.py` | 删除 `_fetch_reference_case_from_llm()`（dead + LLM 查网与 Skill 冲突） | ~85 | 0 | — |
| `m2_engine.py` | 删除 `_fallback_full()`（dead，无调用方） | ~35 | 0 | — |
| `m2_engine.py` | 删除 `import time`（仅被已删除的查网函数使用） | 1 | 0 | — |
| `m2_engine.py` | 重写模块顶部 docstring（去除「提示词驱动/LLM 深度参与」与失实「已删除」注释） | ~9 | ~14 | 改为 code-first 固定链路 + 指向 Skill |
| `m2_engine.py` | 重写类 docstring（去除「LLM 单次调用」流程） | ~5 | ~7 | 改为真实固定双轨链路描述 |
| `prompts/m2/M2_CODE_REVERSE_PROMPT.md` | 删除整个文件 | 248 | 0 | 旧 prompt-first/reverse 设计；引用废弃服务 + 旧输出 schema；review index 标记 DELETED；与 Skill 冲突 |
| `prompts/m2/m2_case_search_rule.md` | 移除 `_fetch_reference_case_from_llm()` 分支与 LLM 查网描述，改为本地知识库 code-first | ~12 | ~6 | 与函数删除同步，保留仍在用的 `_search_cases`/`_generate_modifications` 描述 |

**合计（本次清理）**：`m2_engine.py` 净减约 156 行；prompts 删除 1 文件（248 行）+ 1 文件小幅同步。

---

## 2. 未改动 / 明确保留

| 项 | 原因 |
|----|------|
| `services/m2_symptom_factor_loader.py` | 与 Skill §4/§5 完全一致，无 dead code，未改动 |
| `process()` / `full_pipeline()` / `_extract_dosages()` / `_generate_evidence_based_modifications()` / `_generate_modifications()` / `_search_cases()` / `_normalize_modification_candidates()` | 均有调用方（bridge_server / 测试 / legacy guard），保留 |
| `data/m2_knowledge/` `data/m2_knowledge_staging/` | 无孤立数据需删除，未改动 |
| `data/m2_formula_knowledge.json` | out-of-scope；未改动（验证时临时切 HEAD，验后按 checksum 原样还原） |
| `prompts/m2/M2_SOURCE_PROMPT.md` | review index 标记 DEPRECATED（非 DELETED），保留历史稿；无运行期加载 |
| 所有测试期望 | 未弱化、未修改任何断言 |

---

## 3. 验证结论

| 验证项 | 工作区现状 data | HEAD（committed）data |
|--------|----------------|----------------------|
| M2 单测（129） | 5 失败（**全部为既存、数据驱动**，与本次改动无关） | **129 全过 (OK)** |
| production_acceptance（3） | 1 失败（肺炎痰热，数据驱动） | **3/3 通过 (exit 0)** |
| 新增回归 | **0**（失败集与清理前完全一致） | 0 |
| `[DS_ROUTER]` 日志 | 无 | 无（grep 计数 0） |

- 本次代码清理在两种数据状态下都**未引入任何新失败**。
- 5 个单测失败 + 1 个验收失败的**唯一根因**是 out-of-scope 文件 `data/m2_formula_knowledge.json` 的未提交破坏态（详见 plan §0）。一旦该文件恢复到 committed 版本，全部测试与验收转绿。
