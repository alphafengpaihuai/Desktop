# M2 辨证选方与加减模块实现指令（循证匹配版）

## 总体原则
- 证型、方剂、加减药物全部来自预先构建的"证型知识库"（`data/m2_formula_knowledge.json`，625 种疾病，每病下含多个证型+绑定方剂+药物组成）。
- 模型仅负责"双维度匹配打分"和"加减规则匹配"，不负责生成任何新的证型、方剂或药物。
- 严禁依赖大语言模型自身的医学知识进行辨证或开方。
- 辨证结果必须可追溯至知识库中的具体证型模板，选方与加减必须可追溯至具体规则条目。

## 输入
- M1 输出的西医病名（primary + secondary）+ 诊断置信度
- 患者症状、体征、舌脉、理化指标（从 M1 的 NormalizedInput 传入）
- 患者年龄、体重
- 复诊反馈（如有，含前次主诉、改善系数、残留/新发症状）

## 现有知识库结构（data/m2_formula_knowledge.json）

### 顶层结构
```
{
  "疾病中文名1": {
    "disease_name": "疾病中文名",
    "syndromes": {
      "证型1": {
        "syndrome_name": "证型名称",
        "trigger": "证型触发条件（自然语言描述，含症状、舌脉等）",
        "formula_name": "方剂名称",
        "full_decoction": "完整处方（含药物、剂量、疗程、疗效预估、安全警示）",
        "herbs": ["药1", "药2", "药3"]
      },
      "证型2": { ... }
    }
  },
  ...
}
```

### 字段说明
- disease_name：西医病名（中文），与 M1 输出的 diseaseName_cn 对应
- syndromes：该病下所有常见证型
  - syndrome_name：证型名称（如"急性期-风热犯咽"、"气阴两虚"）
  - trigger：证型触发条件（自然语言段落，包含主症、兼症、舌脉、病程等）
  - formula_name：绑定方剂名称
  - full_decoction：完整处方文本（药物、剂量、疗程、疗效预估、安全警示）
  - herbs：处方中的药物列表

### 当前数据现状
- **625 个疾病**，每个疾病 1-8 个证型
- 数据来源：教材/指南/经典方剂
- **暂缺字段（需在实现中扩展）**：
  - 缺少 modification_rules（加减规则）→ 需从 herbs 反推或从 full_decoction 解析
  - 缺少 pathology_labels（病理标签）→ 需从 M1 病理轴映射或从 trigger 提取
  - 缺少 tongue / pulse（舌脉独立字段）→ 需从 trigger 文本中 NER 提取
  - 缺少 evidence_level / source（证据层级）→ 需在现有数据上补充标注

## 现有代码结构（services/m2_*）

### m2_pathology_syndrome_bridge.py（M2-0 病理锚点桥接层）
- 状态：V3，已有完整代码（899 行）
- 职责：读取 M1 病理轴 → 通过 m2_pathology_to_syndrome_rules.json 映射到中医病机候选
- 输出：辨证约束（allowed_main_syndromes, not_allowed_as_main）
- 依赖：data/m2_pathology_to_syndrome_rules.json（25 条病理轴→证型映射规则）
- 注意：此模块基于"病理轴"映射，与新的"双维度匹配"思路不同，需审查是否保留

### m2_pattern_match_engine.py（M2 证型校对引擎 V4）
- 状态：V4，已有完整代码（510 行）
- 职责：从本地 pattern_db（口服方剂知识库自动构建）匹配证型
- 弃用：旧的 m2_pathology_syndrome_bridge.py 和 pathology_to_syndrome_rules.json
- 核心规则：
  - 西医病名只用于限定证型范围
  - 证型必须由主症、兼症、舌脉、病程、诱因、排除项共同校对
  - 舌脉缺失 → 最高 medium；主症不完整 → 最高 low
  - 命中 exclusion_features → 强制 low
  - 不允许根据病理轴推证型，不允许模型自由创造证型

### m2_prescription_service.py（M2 处方生成层 V3）
- 状态：V3，已有完整代码（559 行）
- 职责：生成完整处方（基础方 + 加味）
- 规则：
  - 基础方完整保留（除非严重禁忌）
  - 加味 ≤ 5 味，必须有 RAG 证据
  - 案例搜索：先匹配病名再匹配证型（不匹配症状）

### m2_multi_axis_matrix_builder.py（症状多轴触发矩阵生成器）
- 状态：已有完整代码（349 行）
- 职责：从病理→证型规则库自动构建症状→轴→证型矩阵
- 输出：services/m2_symptom_multi_axis_matrix.json

### m3_review_service.py（M3 审方筛选层 V3）
- 状态：已有完整代码（533 行）
- 职责：基于本地药理学库，进行审核，禁忌检查，证据校验

## 在线匹配流程

### Step 1：加载候选证型池
- 根据 M1 确定的西医病名（diseaseName_cn），从 data/m2_formula_knowledge.json 调取该病名下所有证型。
- 若 M1 输出多个诊断（primary + secondary），合并所有对应的证型池，去重。
- 若该病名在知识库中不存在，按以下兜底顺序查找：
  1. 疾病名称的同义词/别名映射（m1_name_mapping.json）
  2. 疾病名称的 ICD-11 上位概念
  3. 若仍无匹配，输出 missing_evidence，标记 needs_manual_review=true

### Step 2：双维度匹配打分

#### 维度一：传统辨证匹配
- 将患者症状/舌脉与每个证型模板的 trigger 文本进行语义比对。
- 从 trigger 文本中 NER 提取：
  - 主症（核心症状，如"咽痛剧烈"、"发热恶风"）
  - 舌象（如"舌质红，苔薄白"）
  - 脉象（如"脉浮数"）
- 匹配规则：
  - 主症命中 ≥ 50% → 必要条件满足
  - 舌脉命中 +10 分加分
  - 患者症状与主症冲突 → 该证型大幅降分
- 输出传统辨证匹配得分（0.0-1.0）

#### 维度二：病理标签匹配
- 从 M1 的病理轴（primary_pathology_axes）和患者临床表现，推导病理过程。
- 从 trigger 文本和 disease_name 提取病理标签（如"细菌感染"、"炎症反应"、"支气管痉挛"）。
- 匹配度越高，得分越高。
- 输出病理标签匹配得分（0.0-1.0）

#### 综合评分
- 综合得分 = 传统辨证得分 × 0.6 + 病理标签得分 × 0.4
- 若舌脉缺失 → 置信度上限为 medium
- 若主症不完整 → 置信度上限为 low

### Step 3：锁定最佳证型与绑定方剂
- 选取综合评分最高的证型作为主证型。
- 从 full_decoction 提取方剂基础组成，作为主方。
- 置信度规则：
  - high：主症全中（≥80%），舌脉相符，综合得分 ≥ 0.8
  - medium：主症部份命中（50-80%），或舌脉轻微不符，综合得分 ≥ 0.5
  - low：主症命中 < 50%，综合得分 < 0.5

### Step 4：加减药物匹配
- 当前知识库暂缺 modification_rules，加减药物按以下方式处理：
  1. 优先使用知识库内置：若 syndrome 下存在 modification_rules，按规则匹配
  2. fallback 方式：LLM 根据患者剩余症状，从 herbs 列表以外的药味中，按药理学靶点匹配 ≤ 4 味药
  3. 禁忌检查：通过 M3 审方层（m3_review_service.py）检查药物禁忌
- 若已有丰富的 modification_rules，按 evidence_level 优先级（A > B > C）采纳：
  - 来源 A：教材/指南推荐加减
  - 来源 B：医案高频关联（6 万例清洗医案挖掘）
  - 来源 C：药理学靶点-药物映射
- 总计增加药物数 ≤ 4 味。

### Step 5：复诊处理（如适用）
- 若有复诊反馈，先判断是否属于同一病程（同一西医病名，治疗窗口内）。
- 四象限决策：
  - Q1（效佳，改善 ≥ 3）：守方，仅微调剂量，去无用药
  - Q2（待效，改善 ≤ 2 但未超显效窗口）：守方等待
  - Q3（激惹，部分改善但出现禁忌）：调整，保留君/臣药，剔激惹药
  - Q4（无效/误判）：更方，返回 Step 1
- 守方或调整时，君/臣药不得删除。

## 证据关联与禁止项
- 方剂出处必须明确列出治疗该西医病名或证型的直接证据层级
- ✅ 允许的推理：
  - "某方在《XX》教材中推荐用于 XX 病 XX 证型"
  - "某方在 XX 指南中作为 XX 病 XX 证型的推荐方"
- ❌ 禁止的推理：
  - "某方出自《医林改错》，故可治某病"（声望背书）
  - "A 方治 B 病，B 病与 C 病病机相同，故 A 方治 C 病"（无证据关联）
  - "黄芪补气、当归活血，故可治某病"（拆方无证据推理）
- 当教科书原方无效时，首选在原方基础上加减，而非直接跳方。

## 输出格式

严格按以下 JSON Schema 输出：

```json
{
  "syndrome_differentiation": {
    "disease": "西医病名",
    "candidate_syndromes": [
      {
        "name": "证型名称",
        "traditional_score": 0.85,
        "pathology_score": 0.72,
        "final_score": 0.80,
        "confidence": "high",
        "evidence": {
          "traditional_match": {"matched": ["咽痛", "发热"], "missed": ["恶风"]},
          "pathology_match": {"matched_labels": ["炎症反应"], "unmatched": []}
        }
      }
    ],
    "selected_syndrome": {
      "name": "最佳证型",
      "confidence": "high",
      "reason": "主症全中，舌脉相符"
    }
  },
  "formula_selection": {
    "name": "方剂名称",
    "source": "出处及证据层级",
    "base_composition": [
      {"herb": "药名", "dosage": "剂量（待M3核定）", "role": "君/臣/佐/使"}
    ]
  },
  "modifications": [
    {
      "herb": "药名",
      "action": "add/remove/adjust",
      "dosage": "剂量（待M3核定）",
      "reason": "靶点/机制",
      "evidence_level": "A/B/C",
      "trigger_condition": "匹配的触发条件"
    }
  ],
  "follow_up_decision": {
    "applicable": false,
    "quadrant": "Q1/Q2/Q3/Q4",
    "action": "守方/守方等待/调整/更方",
    "reason": "简述依据"
  },
  "guardrail": {
    "overridden_syndrome": false,
    "forced_formula_jump": false,
    "evidence_violation": false,
    "notes": []
  }
}
```

## 关键规则汇总
1. **模型只做选择题**：证型、方剂、加减药均从知识库中选取，不生成新内容。
2. **双维度匹配**：传统辨证（从 trigger 文本 NER 提取主症/舌脉）+ 病理标签（从 M1 病理轴推导），两者共同决定证型得分。
3. **加减药物限制**：总数 ≤ 4 味，按证据等级和匹配度择优选取。
4. **证据层级**：教科书/指南 > 临床研究 > 经典关联 > 原方加减。
5. **禁止声望背书**：方剂出处不等于疗效证据。
6. **无效时优先加减**：在原方基础上优化，而非随意跳方。
7. **复诊四象限**：根据改善系数和病程窗口，决定守方/调整/更方。
8. **可追溯性**：所有输出均可回溯至知识库中的具体条目。
9. **与 M1 对接**：通过 diseaseName_cn 字段与 M1 输出对接；M1 的 NormalizedInput（symptoms, signs, labs, tongue, pulse）作为 M2 输入。
10. **与 M3 对接**：M2 输出处方传递给 M3 审方层进行药理学审核。

## 代码文件对应关系

| 提示词步骤 | 对应代码文件 | 现有状态 |
|---|---|---|
| Step 1：加载候选证型池 | services/m2_pathology_syndrome_bridge.py | V3 已有代码，需审查是否适配新结构 |
| Step 2：双维度匹配 | services/m2_pattern_match_engine.py | V4 已有代码，核心逻辑接近 |
| Step 3：锁定证型+方剂 | services/m2_pattern_match_engine.py | V4 已有代码 |
| Step 4：加减药物 | services/m2_prescription_service.py | V3 已有代码 |
| Step 5：复诊处理 | 待实现 | 缺失，需新增 |
| 药理学审核 | services/m3_review_service.py | V3 已有代码 |
| 数据源 | data/m2_formula_knowledge.json | 625 个疾病，缺 modification_rules/pathology_labels |
