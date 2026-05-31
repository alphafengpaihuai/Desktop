# M2 辨证选方模块 — 源提示词（Prompt-First 母版）

> ⚠ 本文件是 M2 模块的提示词规则源。
> 所有 M2 代码必须严格依据本提示词实现。
> 未在本提示词中声明的功能，不得在代码中实现。
> 本提示词已彻底移除病理轴（pathology_axis）。

---

## 一、模块定位

M2 = 辨证选方模块。

**在整条链路中的位置：**
M1（症状 → 病名检索 → Top 3）→ 取 Top 1 主病名 → **M2（主病名 → 证型 → 方剂）** → M3（审方）

**M2 只做一件事：**
给定一个西医主病名（来自 M1 的 Top 1），在该病名下的证型池中，根据患者症状、舌脉选择最匹配的证型，输出绑定方剂。

> **重要：** M1 与 M2 的病名已完全对齐。M1 的疾病库（diseases_core.json）以 M2 知识库（data/m2_formula_knowledge.json）为准，M1 中不在知识库的病名已被删除。M1 输出的 Top 1 主病名一定在知识库中存在，M2 不需要做病名模糊匹配兜底。

**M2 的输入：**
- M1 输出的最匹配病名（1 个）
- 患者症状、舌脉、寒热、二便、病程等临床信息

**M2 的输出：**
- 证型（来自 `data/m2_formula_knowledge.json` 中该病名下的证型）
- 方剂（来自 `data/m2_formula_knowledge.json` 中该证型的绑定）
- 药物列表

**M2 不负责：**
- ❌ 病名匹配（M1 负责）
- ❌ 最终安全审方（M3 负责）
- ❌ 剂量安全判断（M3 负责）
- ❌ 禁忌、毒性审核（M3 负责）
- ❌ 复杂加减
- ❌ 复诊四象限
- ❌ 药理分析
- ❌ Trace Audit / Prompt Registry / Pipeline
- ❌ 病理轴映射

---

## 二、核心原则

### 2.1 Prompt-First 架构
提示词是规则源；代码只是少量执行增强；本地知识库是候选与证据来源；大语言模型 API 是语义匹配与最终裁判。

### 2.2 模型只做选择题
- 证型**必须**来自 `data/m2_formula_knowledge.json` 中该主病名下的证型
- 方剂**必须**来自 `data/m2_formula_knowledge.json` 中该证型的绑定
- 药物**必须**来自知识库或预先构建的加减规则库
- 模型不得自由创造证型、方剂、药物

### 2.3 API 依赖
本项目默认必须接入大语言模型 API。
API 不可用时，M2 不做辨证推理，不输出证型、方剂、药物。

### 2.4 禁止自行扩展
不允许 AI 自行扩展系统边界。
不允许先写大量代码再反推提示词。

---

## 三、输入格式

```json
{
  "primary_disease": "M1 输出的最匹配西医病名（Top 1）",
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
  "weight": "",
  "follow_up": null
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `primary_disease` | M1 输出的最匹配西医病名（仅 1 个），M2 以该病名为键查询知识库。M1 与 M2 病名已完全对齐，该值一定在知识库中存在。 |
| `symptoms` | 患者症状列表（如 ["咽痛", "发热", "咳嗽"]） |
| `signs` | 体征列表 |
| `tongue` | 舌象描述 |
| `pulse` | 脉象描述 |
| `cold_heat` | 寒热情况 |
| `stool_urine` | 二便情况 |
| `sleep` | 睡眠情况 |
| `appetite` | 食欲情况 |
| `labs` | 实验室检查结果 |
| `imaging` | 影像学检查结果 |
| `age` | 年龄 |
| `weight` | 体重 |
| `follow_up` | 复诊反馈（首诊为 null） |

---

## 四、知识库结构

### 4.1 知识库位置
`data/m2_formula_knowledge.json`

包含 625 个疾病，每病名下辖多个证型。M2 以 `primary_disease` 为键直接访问知识库，无需模糊匹配兜底。

### 4.2 数据结构
```json
{
  "疾病中文名": {
    "disease_name": "疾病中文名",
    "syndromes": {
      "证型名称": {
        "syndrome_name": "证型名称",
        "trigger": "证型触发条件（自然语言描述，含症状、舌脉等）",
        "formula_name": "方剂名称",
        "full_decoction": "完整处方（含药物、剂量、疗程、疗效预估、安全警示）",
        "herbs": ["药1", "药2", "药3"]
      }
    }
  }
}
```

### 4.3 加减规则库（如有）
若存在 modification_rules（加减规则），加减药物优先从规则库匹配。
若无规则库，第一版不做复杂加减。

---

## 五、辨证框架规则

### 5.1 框架选择

M2 先判断辨证框架。此判断由 LLM 根据患者临床表现做出：

| 临床表现 | 优先辨证框架 |
|---|---|
| 急性起病、发热恶寒、咽痛咳嗽、鼻塞流涕、感染性或急性炎症表现 | **卫气营血辨证**（顺序：卫分→气分→营分→血分） |
| 慢性病、反复发作、功能失调、体质调理、复诊巩固 | **脏腑辨证** |
| 急性感染与慢性体质并存 | 先判外感阶段 → 再用脏腑辨证处理体质和兼证 |

### 5.2 框架约束
无论采用哪种框架，最终证型、方剂和加减均**必须**来自 `data/m2_formula_knowledge.json` 中该主病名下的证型，不得自行创造。

---

## 六、主流程（Step-by-Step）

### Step 1：根据主病名加载候选证型池
- 以 `primary_disease` 为主键，在 `data/m2_formula_knowledge.json` 中查找该病名下的所有证型
- M1 与 M2 病名已完全对齐，该病名一定在知识库中存在，无需兜底

### Step 2：LLM 辨证选型（单次调用）
- 将候选证型列表（含每个证型的 `trigger` 文本）与患者信息一起发送给 LLM
- LLM 在同一调用中完成：
  - 辨证框架选择（卫气营血 / 脏腑 / 混合）
  - 根据 trigger 文本的语义，比对患者症状、舌象、脉象，选出最匹配的 Top 1 证型
- **注意：** trigger 是自然语言文本，不要尝试用代码做结构化解析，直接由 LLM 做语义匹配
- 输出理由（简述匹配依据）

### Step 3：读取绑定方剂和 herbs
- 根据选中的证型，读取其绑定的 `formula_name` 和 `herbs`

### Step 4：按知识库加减规则或候选加减库选择加减药（可选）
- 第一版 M2 不做复杂加减
- 如需加减，只能从 `modification_rules` 或预先构建的加减候选库中选择
- **加减药不得从 herbs 列表以外自由选药**
- 总计增加药物数 ≤ 4 味

### Step 5：输出给 M3 审方
- 输出格式见第七章

---

## 七、输出格式

```json
{
  "primary_disease": "西医主病名",
  "syndrome_differentiation": {
    "selected_syndrome": {
      "name": "证型名称",
      "reason": "匹配理由简述"
    },
    "differentiation_framework": "卫气营血/脏腑/混合"
  },
  "formula": {
    "name": "方剂名称",
    "herbs": ["药1", "药2", "药3"],
    "source": "data/m2_formula_knowledge.json"
  },
  "modifications": [],
  "needs_manual_review": false,
  "missing_info": []
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `primary_disease` | M1 给出的西医主病名（透传，不做修改） |
| `selected_syndrome.name` | LLM 选中的最匹配证型 |
| `selected_syndrome.reason` | 简述匹配依据 |
| `differentiation_framework` | 辨证框架类型 |
| `formula.name` | 绑定方剂名称 |
| `formula.herbs` | 方剂药物列表 |
| `modifications` | 加减药物列表（第一版可为空数组） |
| `needs_manual_review` | 是否需要人工复核（正常为 false；仅当知识库加载异常时标记） |
| `missing_info` | 缺失的关键临床信息列表（如缺舌象、缺脉象、缺症状描述等） |

---

## 八、LLM 调用 prompt 模板

### 8.1 单次调用的 prompt 结构

M2 对 LLM 的调用为**单次**，包含两部分判断：
1. 辨证框架选择（卫气营血 / 脏腑 / 混合）
2. 从候选证型中选出最匹配的 Top 1

**Prompt 模板：**

```
你是一位中医辨证专家。请根据以下患者信息和候选证型，完成辨证。

## 患者信息
西医主病名：{primary_disease}
症状：{symptoms}
舌象：{tongue}
脉象：{pulse}
寒热：{cold_heat}
二便：{stool_urine}
年龄：{age}
病程：{follow_up}

## 辨证框架规则
- 急性起病、发热恶寒、感染性表现 → 卫气营血辨证
- 慢性反复、功能失调、体质调理 → 脏腑辨证
- 两者兼有 → 先判外感阶段，再用脏腑辨证

## 候选证型（均来自知识库）
{遍历每个候选证型，输出 syndrome_name 和 trigger 文本}

请完成以下任务：
1. 判断应使用哪种辨证框架
2. 从候选证型中选出最匹配的 1 个证型，简述理由

只输出 JSON，不要输出其他内容：
{{
  "differentiation_framework": "卫气营血/脏腑/混合",
  "selected_syndrome": {{
    "name": "证型名称",
    "reason": "匹配理由简述"
  }}
}}
```

### 8.2 代码注意事项
- 将候选证型的 trigger 文本完整拼接进 prompt，不截断
- 不要对 trigger 做结构化解析
- LLM 返回 JSON 后，直接从知识库读取绑定方剂和 herbs

---

## 九、禁止事项

### ❌ 明确禁止
1. **禁止使用病理轴** — 不得使用 pathology_axis、primary_axes、covered_axes、uncovered_axes、多轴触发矩阵
2. **禁止调用 m2_pathology_syndrome_bridge.py 主流程**
3. **禁止 M2 自己做病名检索** — 病名由 M1 给出，M2 只在该病名下的证型池中选择
4. **禁止模型自由创造证型、方剂、药物**
5. **禁止模型从 herbs 列表外自由选药做加减**
6. **禁止输出 confidence / confidence_score / 置信度**
7. **禁止输出 evidence / full JSON trace**
8. **禁止模型做最终剂量安全判断**（M3 负责）
9. **禁止 M2 做 M3 审方工作**
10. **禁止新增 Trace Audit、Prompt Registry、Pipeline**
11. **禁止新增药理分析模块**
12. **禁止新增前端功能**
13. **禁止自行扩展系统**

### ✅ 允许
1. ✅ 以 M1 的 primary_disease 为键，加载知识库中该病名下的证型池
2. ✅ LLM 从该病名的候选证型池中选择最匹配证型
3. ✅ 读取绑定方剂和药物列表
4. ✅ 从 modification_rules 或加减候选库选择加减药（第一版可选）
5. ✅ 输出证型名称、方剂名称、药物列表

---

## 十、第一版 M2 范围（最小可跑版本）

### 第一版实现内容
- ✅ 接收 M1 的 primary_disease
- ✅ 加载该病名在知识库中的所有证型
- ✅ LLM 从候选证型中选择 Top 1
- ✅ 读取绑定方剂 + herbs
- ✅ 输出证型 + 方剂 + herbs

### 第一版不做内容
- ❌ 不做复杂加减
- ❌ 不做复诊四象限
- ❌ 不做 M3 审方
- ❌ 不做药理分析
- ❌ 不做 Trace Audit / Prompt Registry / Pipeline
- ❌ 不做病理轴

---

## 十一、与 M1 / M3 的接口关系

```
M1（已冻结）                      M2（本模块）                   M3（后续模块）
   │                                │                              │
   ├─ primary_disease ─────────────►│                              │
   │   (Top 1 病名)                 │  加载该病名下的证型池        │
   │                                │  ┌──────────────────┐       │
   ├─ symptoms/tongue/pulse ───────►│  │ LLM 单次调用     │       │
   │                                │  │ · 判断辨证框架   │       │
   │                                │  │ · 选 Top 1 证型  │       │
   │                                │  │ → 读绑定方剂     │       │
   │                                │  └──────────────────┘       │
   │                                │                              │
   │                                ├─ syndrome ──────────────────►│
   │                                ├─ formula ───────────────────►│
   │                                ├─ herbs ─────────────────────►│
```

---

## 十二、变更历史

| 版本 | 日期 | 变更内容 |
|---|---|---|
| v1 | 待定 | 初始版本。彻底移除病理轴。M1 与 M2 病名完全对齐。M2 接收 M1 的 Top 1 主病名，在该病名下做辨证选方。 |
