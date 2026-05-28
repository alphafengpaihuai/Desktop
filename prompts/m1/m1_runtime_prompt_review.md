# M1 Runtime Prompt Review｜当前运行规则人工审查稿

> 本文件用于人工审查 M1 当前运行规则。
> 当前 M1 已从"病理轴匹配型"重构为"诊断标准比对型"。
> 本文件不直接参与运行时决策，除非后续明确接入。

---

## 1. 当前状态总览

| 项目 | 当前状态 | 证据位置 | 是否需要人工检查 |
|---|---|---|---|
| 是否存在独立运行时 prompt 文件 | 否。没有独立的 M1 prompt markdown 被读取 | m1_engine.py 无任何 .md 文件读取逻辑 | 是 |
| 是否主要由代码逻辑驱动 | 是。诊断规则编码在 m1_engine.py 中 | m1_engine.py (1415 行) | 是 |
| 是否调用 LLM | 否。diagnose() 主流程无 LLM 调用 | _call_llm_api() 未被任何上游调用 | 否 |
| LLM 是否仅作为症状标准化预留 | 是。normalize_symptoms() 使用本地映射，不调 LLM | normalize_symptoms() 行 1283-1306 | 是 |
| 是否仍含 axes / 病理轴主流程 | 否。所有 axes 引擎代码已删除 | grep 确认 10 项 axes 残留均为 0 | 是 |
| 是否已转为 criteria-based matching | 是。核心逻辑基于 required/supportive/exclusion_criteria | _evaluate_disease_by_criteria() 行 625 | 是 |

---

## 2. M1 模块定位

M1 是西医诊断校准模块，职责是：

1. 接收患者输入的病名、主诉、症状、体征、检查结果；
2. 从本地疾病诊断标准库中召回候选疾病；
3. 按 required_criteria / supportive_criteria / exclusion_criteria 逐条比对；
4. 识别红线风险；
5. 输出西医候选诊断、置信度、缺失信息和排除依据；
6. 不输出中医证型；
7. 不输出处方；
8. 不参与 M2 证型最终选择。

---

## 3. M1 输入字段

M1 输入应包括：

- `patient_mentioned_disease` — 患者提到的疾病名
- `chief_complaint` — 主诉（取 symptoms 第一项）
- `symptoms` — 西医症状列表（不含舌苔脉象）
- `signs` — 查体发现
- `vitals` — 生命体征
- `labs` — 实验室检查
- `imaging` — 影像学
- `history` — 病史
- `negative_findings` — 阴性发现
- `tcm_features_for_m2` — 舌、苔、脉等中医特征（不参与 M1 诊断评分）

**说明：** 舌、苔、脉、寒热喜恶、口苦口干、大便、小便等可进入 `tcm_features_for_m2`，但不得参与 M1 西医诊断评分。

### JSON 示例

```json
{
  "patient_mentioned_disease": "",
  "chief_complaint": [],
  "symptoms": [],
  "signs": [],
  "vitals": {},
  "labs": [],
  "imaging": [],
  "history": [],
  "negative_findings": [],
  "tcm_features_for_m2": []
}
```

---

## 4. M1 核心执行流程

```
患者输入
  │
  ▼
_normalize_patient_input
  │  将舌苔脉象分流到 tcm_features_for_m2
  │  将西医症状分类到 symptoms / signs / labs / imaging / history
  ▼
find_disease (或 search_diseases)
  │  精确查找 + 中文映射 + 别名映射
  ▼
_evaluate_disease_by_criteria
  │  检查 required_criteria / supportive_criteria / exclusion_criteria
  │  如缺失 → schema_missing → fallback 匹配
  │  如完整 → 逐条匹配
  ▼
  │  match required_criteria
  │  match supportive_criteria
  │  match exclusion_criteria
  ▼
_compute_diagnostic_score
  │  新评分公式（不含 axes 权重）
  │  required_ratio × 0.5 + supportive_ratio × 0.3
  │  − exclusion_penalty + objective_bonus + symptom_bonus
  ▼
_assign_confidence
  │  qualitative confidence matrix
  ▼
_detect_red_flags
  │  检查红旗信号
  ▼
_generate_differentials_by_criteria
  │  使用 differential_diagnosis 列表 + search_diseases
  ▼
输出 DiagnosisResult
```

### 不使用以下内容

- ⛔ 不使用 axes
- ⛔ 不使用 pathology_axes
- ⛔ 不使用 primary_axes_covered
- ⛔ 不使用 axis_matches
- ⛔ 不使用病理轴覆盖率评分

---

## 5. 诊断标准比对规则

### required_criteria

required_criteria 用于判断该疾病诊断是否具备核心必要条件。若 required_criteria 缺失或关键必要项未满足，该疾病不得判定为 high。

匹配逻辑：
- 精确匹配患者文本（symptoms / signs / labs / imaging / history）
- 包含匹配（长度 ≥ 3）
- 同义词映射匹配（SYNONYM_MAP）
- 若同时存在 diagnostic_criteria 但无 required_criteria，降级使用 diagnostic_criteria 作为参考，同时输出 warning

### supportive_criteria

supportive_criteria 用于判断是否存在支持该疾病的症状、体征或客观检查。客观证据包括 lab、imaging、vital、physical_exam 等。

匹配方式同 required_criteria，但不计入缺失项。

### exclusion_criteria

exclusion_criteria 用于判断是否存在排除该疾病的证据。若命中 exclusion_criteria，该疾病 confidence 应强制降级，通常不得 high。

匹配方式：匹配到患者文本 = 命中排除项（证据表明应排除该诊断）。

### 证据类型分类

| 证据类型 | 匹配关键词 |
|---|---|
| lab | 血常规、生化、crp、esr、wbc、培养等 |
| imaging | 胸片、CT、MRI、超声、心电图等 |
| sign | 查体、体征、听诊、啰音、皮疹等 |
| vital | 血压、体温、心率、呼吸、SpO₂ |
| history | 病史、既往、史、history of |
| negative | 无、阴性、absence of |
| symptom | 以上均不匹配时默认 |

---

## 6. 置信度矩阵

### high

必须同时满足：
- required_criteria 核心必要条件完整；
- 有足够客观支持证据（objective_support_count ≥ 2）；
- 无 exclusion_matches；
- 诊断得分 ≥ 0.5。

### medium

符合以下情况之一：
- 核心必要条件部分匹配（≥ 半数），但关键检查缺失；
- 有支持证据，但客观证据不足（objective_support_count < 2）；
- 无排除项；
- 诊断得分 ≥ 0.3。

### low

符合以下情况之一：
- required_criteria 不完整；
- 仅有非特异症状；
- 缺少客观支持；
- 命中 exclusion_criteria；
- 患者信息极简；
- schema_missing（降级为 low 而非 none）。

### none

符合以下情况之一：
- 疾病不在本地库；
- 疾病名不规范；
- 患者数据与诊断标准根本冲突；
- 诊断得分过低（< 0.1）；
- 必要条件均未匹配。

> **人工修订区：置信度规则**
>
> - [ ] 当前 high/medium/low/none 的阈值是否需要调整？
> - [ ] objective_support_count ≥ 2 是否合理？
> - [ ] 是否需要增加"必须无红旗未排除"条件？
> - [ ] schema_missing 降级为 none 是否过于严格？

---

## 7. schema_missing 规则

当疾病条目缺少以下任一结构时，应标记 schema_missing：

- required_criteria
- supportive_criteria

schema_missing 时必须输出：
- `schema_missing = true`
- `warnings` 中包含 "缺少标准诊断结构" 或 "缺少 required_criteria 结构，已降级使用 diagnostic_criteria"

schema_missing 疾病不得判定为 high，当前降级为 none。

### 人工检查点

- [ ] schema_missing 是否稳定写入 warnings？
- [ ] schema_missing 是否进入 DiagnosisResult？（当前仅在 matched_disease 上标记）
- [ ] schema_missing_diseases 是否能被上游 trace 捕获？
- [ ] schema_missing 是否不会回退到 axes 评分？
- [ ] 降级使用 diagnostic_criteria 时是否输出了 warning？

---

## 8. 红线规则

### 当前红线列表

| 触发线索 | 严重等级 | 示例 pattern |
|---|---|---|
| 胸痛 | HIGH | 胸痛、胸闷、chest pain |
| 呼吸困难 | HIGH | 呼吸困难、气短、dyspnea |
| 意识改变 | HIGH | 意识模糊、昏迷、altered mental status |
| 咯血 | HIGH | 咯血、咳血、hemoptysis |
| 急性腹痛 | HIGH | 急性腹痛、剧烈腹痛 |
| 单侧肢体无力 | HIGH | 偏瘫、单侧无力、hemiplegia |
| 呕血 | HIGH | 呕血、吐血、hematemesis |
| 低血压/休克 | HIGH | 低血压、休克、hypotension |
| 低氧 | HIGH | 低氧、SpO₂低、hypoxia |
| 癫痫/抽搐 | HIGH | 癫痫、抽搐、seizure |

### 红线规则

- 红线规则用于安全提示和诊断降级。
- 若红线疾病无法排除，普通解释性诊断不得判定为 high。
- 红线信号在输出中以 `[HIGH]` 标记。
- 去重逻辑：每个 flag_id 只输出一次。

> **人工修订区：红线规则**
>
> - [ ] 当前红线仅作为提示，未影响 confidence 计算。是否需要让红线影响 confidence？
> - [ ] 是否需要增加"当存在 HIGH 红线时，confidence 自动降级"规则？
> - [ ] 是否需要增加红线对应的必须排除检查项？

---

## 9. LLM 使用边界

当前 M1 主流程不调用 LLM 进行诊断。

LLM 仅可作为未来"症状标准化"预留能力。

### LLM 禁止

- 新增疾病；
- 生成诊断；
- 决定 confidence；
- 修改 required/supportive/exclusion 匹配结果；
- 回写 diseases_core.json；
- 生成 axes；
- 生成 pathology_axes；
- 生成 axis_matches。

### 当前相关函数状态

| 函数名 | 是否被 diagnose() 调用 | 当前用途 | 是否含 prompt | 是否需要人工检查 |
|---|---|---|---|---|
| normalize_symptoms | 否 | 仅使用 alias_map + colloquial_map 本地映射 | 否 | 是。确认不接入 diagnose() |
| _call_llm_api | 否 | LLM API 调用封装（预留） | 否。prompt 由调用者传入 | 是。确认不被 diagnose() 调用 |
| _call_deepseek_api | 否 | DeepSeek API 调用（预留） | 否 | 是。确认不被 diagnose() 调用 |
| _call_gemini_api | 否 | Gemini API 调用（预留） | 否 | 是。确认不被 diagnose() 调用 |

---

## 10. 已废弃旧逻辑

以下内容不允许恢复到 M1 主流程：

- `AxeMatch` — 数据类（已删除）
- `axes` — 引擎中使用（已删除，数据库中的 axes 字段保留作为数据结构）
- `pathology_axes` — 引擎中使用（已删除）
- `primary_axes_covered` — 字段（已删除）
- `uncovered_primary_axes` — 字段（已删除）
- `covered_axes` — 字段（已删除）
- `uncovered_axes` — 字段（已删除）
- `_match_axis()` — 方法（已删除）
- `axis_matches` — 候选诊断字段（已删除）
- 病理轴评分 — 评分公式（已删除）
- 病理轴 prompt — 任何涉及轴覆盖率的提示词（已删除）
- 基于 axes 交集生成 differential_list — 逻辑（已删除）
- 基于 axes 覆盖率决定 overall_score — 逻辑（已删除）

---

## 11. 当前 M1 输出字段

### 输出字段清单

- `matched_disease` — 最匹配的候选诊断
- `differential_list` — 鉴别诊断列表
- `no_match` — 是否未找到匹配
- `not_allowed` — 是否不允许作为入口
- `error` — 错误信息
- `red_flags` — 红旗信号
- `schema_missing` — 是否缺失诊断标准结构
- `schema_missing_diseases` — 缺失结构的疾病名列表

### matched_disease 字段（CandidateDiagnosis）

- `disease_name`
- `entry_type`
- `diagnostic_score` — 0.0 ~ 1.0
- `confidence` — high / medium / low / none
- `required_matches` — 已匹配的必要条件列表
- `missing_required` — 未匹配的必要条件列表
- `supportive_matches` — 已匹配的支持条件列表
- `exclusion_matches` — 命中的排除条件列表
- `objective_support_count` — 客观证据计数
- `symptom_support_count` — 症状匹配计数
- `red_flags` — 候选级别红线
- `missing_checks` — 建议补充检查
- `confidence_reasoning` — 置信度依据文本
- `warnings` — 警告列表

### JSON 示例

```json
{
  "matched_disease": {
    "disease_name": "",
    "entry_type": "",
    "confidence": "",
    "diagnostic_score": 0.0,
    "required_matches": [],
    "missing_required": [],
    "supportive_matches": [],
    "exclusion_matches": [],
    "objective_support_count": 0,
    "symptom_support_count": 0,
    "red_flags": [],
    "missing_checks": [],
    "confidence_reasoning": "",
    "warnings": []
  },
  "differential_list": [],
  "no_match": false,
  "not_allowed": false,
  "error": null,
  "red_flags": [],
  "schema_missing": false,
  "schema_missing_diseases": []
}
```

---

## 12. Token 成本标记

| 规则/模块 | 当前承载位置 | 是否运行时加载 | 是否进入 LLM prompt | token_runtime_cost | 说明 |
|---|---|---|---|---|---|
| criteria 匹配 | m1_engine.py `_match_criterion()` | 是 | 否 | none | 纯代码逻辑 |
| 评分公式 | m1_engine.py `_compute_diagnostic_score()` | 是 | 否 | none | 纯代码逻辑 |
| 置信度矩阵 | m1_engine.py `_assign_confidence()` | 是 | 否 | none | 纯代码逻辑 |
| 红线检测 | m1_engine.py `_detect_red_flags()` | 是 | 否 | none | 纯代码逻辑 |
| 中医分流 | m1_engine.py `_normalize_patient_input()` | 是 | 否 | none | 纯代码逻辑 |
| 症状标准化 | m1_engine.py `normalize_symptoms()` | 否（预留） | 否 | none（目前） | 本地映射，不调 LLM |
| LLM API 调用 | m1_engine.py `_call_llm_api()` | 否（预留） | 否（目前） | none（目前） | 未被 diagnose() 调用 |
| 疾病库 JSON | diseases_core.json | 是 | 否 | none | 纯数据文件 |
| 本审查文件 | prompts/m1/m1_runtime_prompt_review.md | 否 | 否 | none | 人工审查用 |

**说明：** 代码化规则本身不消耗 LLM token。只有被拼接进 LLM 请求的内容，才消耗运行时 token。Markdown 人工审查文件不等于运行时 prompt。

---

## 13. Prompt Registry 初版

| 文件/函数 | 类型 | 状态 | 是否运行时调用 | 是否含旧病理轴 | 处理建议 |
|---|---|---|---|---|---|
| m1_engine.py | Python 模块 | active | 是 | 否（已清理） | 继续维护 |
| normalize_symptoms | 函数 | active（预留） | 否 | 否 | 保留为预留入口，不接入 diagnose() |
| _call_llm_api | 函数 | legacy（预留） | 否 | 否 | 保留但不得被 diagnose() 调用 |
| _call_deepseek_api | 函数 | legacy（预留） | 否 | 否 | 保留但不得被 diagnose() 调用 |
| _call_gemini_api | 函数 | legacy（预留） | 否 | 否 | 保留但不得被 diagnose() 调用 |
| prompt_GPT_knowledge_builder.md | Markdown 提示词 | legacy | 否 | 是（含 axes） | 标记为历史知识库构建提示词，不参与运行时 |
| prompt_GPT_knowledge_builder_full.md | Markdown 提示词 | legacy | 否 | 是（含 axes） | 标记为历史知识库构建提示词，不参与运行时 |
| prompt_GPT_typical_findings_zh.md | Markdown 提示词 | legacy | 否 | 是（含 axes） | 标记为历史知识库构建提示词，不参与运行时 |
| prompt_GPT_v3_complete.md | Markdown 提示词 | legacy | 否 | 是（含 axes） | 标记为历史知识库构建提示词，不参与运行时 |
| m1_runtime_prompt_review.md | Markdown 审查文稿 | manual_review_needed | 否 | 否 | 人工审查用，不直接参与运行时 |

### 状态说明

- **active** — 当前运行中
- **legacy** — 保留但未接入主流程
- **deprecated** — 已废弃，不应使用
- **code_migrated** — 已从提示词迁移为代码逻辑
- **manual_review_needed** — 待人工审查

---

*本文档生成于 M1 v2 重构完成后。所有规则基于 m1_engine.py 代码逻辑整理，不改变运行时行为。*
