# M1 Agent 诊断引擎 v6 — 实现指令

M1 核心目标：**以患者症状、体征、检查结果为查询条件，在本地疾病库 + 诊断卡片库中检索匹配，通过 Agent 工具集 + 追问循环，输出 Top 1-3 规范疾病名称。**

## 总体原则
- 所有诊断必须基于本地疾病诊断标准知识库（`diseases_core.json`，1279 条）和诊断卡片库（`data/m1_diagnostic_cards.json`，699 张）。
- 模型仅负责"对比打分+信息缺口判断"，不负责"生成诊断"或"生成诊断标准"。
- 大语言模型基于对症状的理解，先分科、后比对诊断标准，选择最匹配的病名。
- 严禁依赖大语言模型自身的医学知识生成病名或诊断标准。
- 追问循环 ≤ 2 轮，每次追问 ≤ 2 个问题，避免无限循环。

## 输入
- `patient_mentioned_disease`：患者提到的病名或医生初步诊断（**仅作为召回关键词之一，不作诊断依据**）
- `chief_complaint`：主诉
- `symptoms`：症状列表
- `signs`：体征列表
- `labs`：实验室检查列表
- `imaging`：影像学检查列表
- `negative_findings`：明确阴性结果列表
- `duration`：病程
- `onset`：起病方式
- `age` / `gender`：人口学信息
- `_followup_answers`（可选）：上一轮追问的答案，用于追问循环

**同义表达归一化强化匹配**（代码中 `SYNONYM_MAP` / `normalize_text`），例如：
- "喘不上气 / 气紧 / 憋气" → 呼吸困难
- "拉肚子 / 稀便" → 腹泻
- "咽痛 / 喉咙痛" → 咽喉痛

标准化只用于召回和比对，**不得用于生成最终诊断**。

## Agent 工具集

### 工具1：查询本地诊断标准（`_query_local_criteria`）
- **调用时机**：需要获取某个候选疾病的具体诊断标准时
- **输入**：疾病名称（中文/英文/中英混合）
- **输出**：该疾病的 `diagnostic_criteria`、`typical_symptoms`、`differential_diagnosis`
- **匹配策略**：
  1. 精确匹配中文名（`diseaseName_cn`）
  2. 精确匹配英文名（`disease_name`，通过 `name_index`）
  3. 部分子串匹配中文名
  4. 若命中诊断卡片库（`diagnostic_cards`），优先使用卡片中的详细数据
- **若本地无该疾病条目** → 返回 `{"status": "NOT_FOUND_IN_LOCAL_DB"}`

### 工具2：在线查询默沙东/PubMed（`_online_query`）
- **调用时机**：
  1. 工具1返回 `NOT_FOUND_IN_LOCAL_DB` 时
  2. 本地诊断标准与患者表现存在明显差异时
  3. 怀疑存在最新诊断标准或本地标准已过时时
- **输入**：疾病名称 + 检索源（`msd` / `pubmed`）
- **输出**：从权威信源获取的最新诊断标准摘要
- **注意**：获取到的诊断标准自动缓存入 `_online_cache`（6 个月有效期），并自动追加到 `diseases_core.json`

### 便捷入口：`_get_cached_criteria`
- 自动决定使用工具1还是工具2
- 先查本地库（工具1）→ 未找到则在线查询（工具2）

## 知识库结构

### diseases_core.json（1279 条）
| 字段 | 说明 |
|:---|:---|
| `diseaseName_cn` | 疾病中文名 |
| `disease_name` | 疾病英文名 |
| `diagnostic_criteria` | 诊断标准 |
| `typical_symptoms` | 典型症状列表 |
| `differential_diagnosis` | 鉴别诊断列表 |
| `_card_source` | 来源标记（`diagnostic_card` / `diagnostic_card_only`） |

### data/m1_diagnostic_cards.json（699 张诊断卡片）
| 字段 | 说明 |
|:---|:---|
| `sequence` | 序号 001-699 |
| `diseaseName_cn` | 疾病中文名 |
| `disease_name` | 疾病英文名 |
| `diagnostic_criteria` | 逐条诊断标准（结构化） |
| `typical_symptoms` | 典型症状明细 |
| `differential_diagnosis` | 需鉴别的疾病列表 |
| `red_flags` | 红旗征 |
| `specialist_referral_criteria` | 专科转诊标准 |

## 缓存规则
| 缓存 | 路径 | 有效期 | 说明 |
|:---|:---|:---|:---|
| diseases_core.json | 项目根目录 | 永久 | 在线查询结果自动追加 |
| 在线缓存 | `_online_cache`（内存） | 6 个月 | 键：`疾病名|源`，避免重复查询 |
| 诊断标准补齐日志 | `data/disease_cache/criteria_filled_log.json` | 永久 | 已补齐过的疾病不再查询 |

## Agent 推理流程

```
临床资料输入
  │
  ▼
第一步：标准化输入（normalize_input）
  │  同义表达归一化，字段分类
  ▼
第二步：生成初始假设（_recall_candidates）
  │  1. 患者提到的病名精确匹配
  │  2. 症状关键词模糊匹配（keyword_index）
  │  3. 限制最多 20 个候选
  ▼
第三步：检索诊断标准（每个候选病）
  │  _get_cached_criteria(候选病名)
  │    ├─ 工具1（本地查询）
  │    └─ 工具2（在线查询，6个月缓存）
  ▼
第四步：LLM Agent 逐一验证 + 信息缺口判断
  │  比对患者资料与每个候选病的诊断标准
  │  ├─ 信息足以区分 → 模式一（DIAGNOSIS_READY）
  │  └─ 信息不足 → 模式二（NEED_MORE_INFO）
  │       ↓ 生成 1-2 个追问问题
  │       ↓ 外部收集答案 → _followup_answers
  │       ↓ 返回第三步重新比对（≤ 2 轮）
  ▼
第五步：输出
  模式一：{"diagnosis_calibration": {"calibrated_diagnosis": ["病名1", ...]}, "source": "llm"}
  模式二：{"status": "NEED_MORE_INFO", "questions": ["追问1？", ...]}
```

## 追问循环规则
- **触发条件**：LLM Agent 比对后发现信息不足以在候选病之间做出明确区分
  - 核心症状重叠（如不同腹痛病因共享同一症状）
  - 缺少关键检查结果（如体温、血常规、影像学）
  - 缺少鉴别所需的关键病史（如发作诱因、缓解因素）
- **追问数量**：每次 ≤ 2 个问题
- **追问内容**：必须具体、可操作、基于候选病诊断标准中提到的信息点
- **不能重复**：本轮追问不能与上一轮（`_followup_answers` 中已有的问题）重复
- **循环终止**：最多进行 2 轮追问循环。2 轮后仍不足以区分时，输出当前最佳猜测
- **追问嵌入**：在 LLM prompt 的"患者资料"部分末尾追加 `_followup_answers` 内容

## 输出规范

### 模式一：信息足够时
```json
{
  "diagnosis_calibration": {
    "original_input": "患者提到的病名",
    "calibrated_diagnosis": ["病名1", "病名2"]
  },
  "source": "llm",
  "cache_hit": false
}
```

### 模式二：信息不足时
```json
{
  "status": "NEED_MORE_INFO",
  "current_top_candidates": ["候选病名1", "候选病名2"],
  "cannot_decide_because": "两者共享核心症状XX，缺少XX信息可区分",
  "questions": ["追问1：患者有无XX？", "追问2：XX检查结果？"],
  "missing_info": ["缺失信息标签1", "缺失信息标签2"]
}
```

## 兜底（LLM 不可用时）
当 LLM API 不可用时（`llm_api_available() == False`）：
- 使用 `_keyword_fallback()` 做关键词模糊搜索
- 使用口语化病名→标准名映射表（`slang_map`）
- 输出格式同模式一，`source` 标记为 `"code_fallback"`

## 禁止
- ❌ 创造病名或诊断标准
- ❌ 输出本地库外的疾病
- ❌ 输出证型、处方、置信度、推理过程
- ❌ 依赖大语言模型自身的医学知识生成诊断
- ❌ 追问循环超过 2 轮
- ❌ 单次追问超过 2 个问题
- ❌ 重复上一轮已问过的问题

## 代码文件对应关系

| 提示词步骤 | 对应方法 | 位置 |
|:---|:---|:---|
| 标准化输入 | `normalize_input()` | m1_engine.py 第 226 行 |
| 同义归一化 | `normalize_text()` + `SYNONYM_MAP` | 文件头部 |
| 生成初始假设 | `_recall_candidates()` | m1_engine.py 第 892 行 |
| 工具1：本地查询 | `_query_local_criteria()` | m1_engine.py 第 555 行 |
| 工具2：在线查询 | `_online_query()` | m1_engine.py 第 621 行 |
| 便捷入口 | `_get_cached_criteria()` | m1_engine.py 第 728 行 |
| Agent 诊断主流程 | `diagnose()` | m1_engine.py 第 745 行 |
| 关键词兜底 | `_keyword_fallback()` | m1_engine.py 第 378 行 |
| 诊断标准补齐 | `_fill_missing_criteria()` | m1_engine.py 第 254 行 |
| JSON 输出 | `diagnose_json()` | m1_engine.py 第 936 行 |
| 诊断卡片库 | `self.diagnostic_cards` | m1_engine.py `__init__` 中加载 |
| 主数据源 | `data/m1_diagnostic_cards.json` | 699 张诊断卡片 |
| 合并核心库 | `diseases_core.json` | 1279 条（含卡片数据） |
