# M1 诊断鉴别模块实现指令

M1 的核心目标：**以患者症状、体征、检查结果为查询条件，在本地疾病库（diseases_core.json，656 条）中检索匹配最多 1–3 个规范疾病名称。**

## 总体原则
- 所有诊断和鉴别诊断必须基于本地疾病诊断标准知识库（diseases_core.json）。
- 模型仅负责"对比打分"，不负责"生成诊断"。
-大语言模型基于自身对症状的理解，先基于分科，后基于患者的表述理解，选择最匹配的病名
- 严禁依赖大语言模型自身的医学知识生成病名。

## 输入
- patient_mentioned_disease：患者提到的病名或医生初步诊断（**仅作为召回关键词之一，不作诊断依据**）
- 主诉
- 症状列表
- 体征列表
- 实验室检查列表
- 影像学检查列表
- 明确阴性结果列表
- 病程
- 起病方式
- 严重程度
- 症状部位
- 诱因和缓解因素

**同义表达归一化强化匹配**，例如：
- "喘不上气 / 气紧 / 憋气" → 呼吸困难
- "拉肚子 / 稀便" → 腹泻
- "咽痛 / 喉咙痛" → 咽喉痛

标准化只用于召回和比对，不得生成最终诊断。

## 知识库结构

diseases_core.json 中的每条目包含以下关键字段：

| 字段 | 说明 | 覆盖 |
|---|---|---|

| `diseaseName_cn` | 疾病中文名 | 全部 656 条 |
| `diagnostic_criteria` | 诊断标准（必要条件 + 支持证据混合） | 287 条有内容 |
| `typical_symptoms` | 典型症状列表 | 287 条有内容 |
| `differential_diagnosis` | 鉴别诊断要点 | 287 条有内容 |
| `source_verified_medical_summary` | 结构化摘要（含 key_symptom_pattern、key_exam_findings、local_retrieval_keywords） | 287 条有内容 |

**注意：** 369 条空白条目（由知识库补充而来）仅有 `diseaseName_cn` 和 `disease_name`。症状轨检索时必须使用 `diseaseName_cn` 做包含关系匹配作为兜底。

## 流程

### 第一步：标准化输入
将原始输入映射为 NormalizedInput 结构，完成同义表达归一化。

### 第二步：双轨召回

#### 症状轨（代码检索，无 LLM 也运行）
1. 以患者主诉、症状、体征、实验室、影像学为查询条件
2. 从疾病库中匹配 `key_symptom_pattern`、`key_exam_findings`、`local_retrieval_keywords`、`typical_symptoms`、`diagnostic_criteria`
3. 系统粗过滤：从症状推断 1–3 个可能的器官系统，如缩小检索范围
4. 对空白条目（无上述字段）：用 `diseaseName_cn` 与患者症状做包含关系匹配（患者症状包含病名或病名包含症状）
5. 多路分数加权排序，取 top_k 候选

#### 病名轨（LLM 辅助召回，LLM 不可用时用代码兜底）
1. LLM 从 656 条病名中猜 3–5 个最可能的疾病
2. LLM 不可用时：用 `patient_mentioned_disease` 和患者症状关键词，在 656 条病名下做包含关系匹配，取 top 5

### 第三步：合并去重
两条轨道结果合并，以中文名去重。优先保留有诊断标准的条目。

### 第四步：LLM 逻辑裁判
- 接收患者完整资料 + 每个候选病的 `diagnostic_criteria`、`typical_symptoms`、`differential_diagnosis`
- 按以下优先级比对排序：
  1. 患者症状与诊断标准匹配程度
  2. 疾病能同时解释主诉和关键症状
  3. 与相似疾病有明确区分点
- 输出 Top 1–3 个疾病名称
- LLM 不可用时，用代码依据 `key_symptom_pattern` / `local_retrieval_keywords` / 病名包含关系做排序

### 第五步：兜底（无法匹配时）
若全部候选均不匹配（空集），授权联网查询默沙东、pubmed 或其他在线循证医学网页获取最适配病名。

## 输出规范

```json
{
  "diagnosis_calibration": {
    "original_input": "患者提到的病名",
    "calibrated_diagnosis": ["病名1", "病名2", "病名3"]
  },
  "source": "local_db / merck_manual",
  "cache_hit": true
}
```

- 只输出基于性别、年龄，分科最匹配的疾病名称，最多 3 个
- 优先输出更具体、更能解释患者资料的规范疾病名称
- 不输出置信度、不输出推理过程、不输出证型、不输出处方

## 禁止
- ❌ 创造病名
- ❌ 输出本地库外疾病
- ❌ 输出证型、处方、置信度、推理过程
- ❌ 依赖大语言模型自身的医学知识生成诊断
