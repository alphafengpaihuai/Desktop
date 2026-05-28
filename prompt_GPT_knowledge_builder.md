# M1 西医诊断知识库补全 Prompt

## 任务概述

请为以下 801 个疾病的 JSON 知识库补全信息。目前每个条目只有 `disease_name`、`entry_type`、`m1_diagnosis_entry_allowed` 和 `axes` 四个字段。需要补全以下缺失内容。

---

## 一、需要补全的字段（每个疾病）

### 1. `diseaseName_cn`（必填）
中文标准病名。

### 2. `description`（必填）
1-2 句话概述该疾病的核心定义，简明扼要。

### 3. `typical_symptoms`（必填）
患者常见的主诉/症状列表，**用中文描述**，例如：
- "眩晕、天旋地转感"
- "颈部疼痛伴肩背放射痛"
- "活动后胸闷胸痛"

### 4. `physical_exam`（选填）
关键体格检查发现。

### 5. `lab_tests`（选填）
关键实验室检查。

### 6. `imaging`（选填）
关键影像学检查。

### 7. `differential_diagnosis`（必填）
3-5 个需要鉴别的主要疾病名称。

### 8. `risk_factors`（选填）
高危因素。

### 9. `pathophysiology`（选填）
简要发病机制。

### 10. `epidemiology`（选填）
流行病学数据。

---

## 二、需要补全的 `axes` 中的 `typical_findings`（最关键！）

### 问题
目前的 `typical_findings` **55% 是纯英文**，导致中文症状无法匹配。需要给每条英文 finding **追加中文同义词**。

### 要求
每条 typical_finding 的格式改为 `英文 (中文)`，例如：

```json
"typical_findings": [
  "dizziness / vertigo (眩晕、头晕)",
  "neck pain (颈痛、颈部疼痛)",
  "limited range of motion (颈部活动受限)",
  "vertebrobasilar insufficiency symptoms (椎基底动脉供血不足表现)"
]
```

### 同义词映射表（已有的，可直接复用）
```
dizziness → 眩晕、头晕
headache → 头痛
cough → 咳嗽
fever → 发热、发烧
fatigue → 乏力、疲劳
nausea → 恶心
vomiting → 呕吐
pain → 疼痛
edema → 水肿、浮肿
palpitations → 心悸、心慌
dyspnea → 呼吸困难、气短
insomnia → 失眠、入睡困难
constipation → 便秘
diarrhea → 腹泻
numbness → 麻木
tremor → 震颤
syncope → 晕厥
tinnitus → 耳鸣
hypertension → 高血压
hypotension → 低血压
tachycardia → 心动过速
bradycardia → 心动过缓
seizure → 癫痫发作、抽搐
bleeding → 出血
anemia → 贫血
rash → 皮疹
pruritus → 瘙痒
hematuria → 血尿
proteinuria → 蛋白尿
jaundice → 黄疸
```

### 其他常见患者描述词（需主动补充到对应病的 findings 中）

以下 47 个常见中文词**目前完全没有在任何 typical_findings 中出现**，请根据疾病特征，补充到对应疾病的对应轴中：

`发烧、怕热、头晕、头胀、头重、眼花、视力模糊、口臭、口腔溃疡、气喘、心慌、胃痛、嗳气、食欲差、纳差、便血、颈痛、颈椎、腰酸、麻木、抽筋、颈A胀、脖子、肩膀痛、失眠、多梦、惊醒、嗜睡、烦躁、抑郁、荨麻疹、皮肤干燥、月经不调、月经量多、月经量少、痛经、闭经、带下、夜尿多、小便黄、苔白、苔黄、苔腻、苔薄、舌暗、舌红、舌淡`

---

## 三、补全典型症状到疾病轴的对照表（供参考）

下面是 36 个病理轴及其对应的典型中文症状描述：

| 病理轴名 | 典型中文症状 |
|---------|-----------|
| infection_or_inflammation | 发热、红肿、疼痛、分泌物增多 |
| vascular_or_microcirculatory | 头晕、眩晕、供血不足、血压异常 |
| structural_abnormality | 结构异常、占位、压迫 |
| endocrine_or_hormonal_dysregulation | 月经紊乱、情绪波动、代谢异常 |
| neoplastic_or_proliferative | 肿块、异常增生 |
| immune_abnormality | 过敏、自身免疫表现 |
| cardiovascular_or_circulatory_risk | 胸痛、胸闷、心悸 |
| respiratory_or_lower_airway | 咳嗽、咳痰、气喘 |
| digestive_or_gastrointestinal | 腹痛、腹胀、腹泻、便秘 |
| neurological | 头痛、头晕、麻木、震颤 |
| musculoskeletal | 关节痛、肌肉痛、僵硬 |
| psychiatric_or_cognitive | 失眠、焦虑、抑郁、记忆力下降 |
| renal_or_urinary | 尿频、尿急、尿痛、水肿 |
| metabolic_or_endocrine | 体重变化、多饮多尿、怕冷怕热 |
| hematologic | 贫血、出血、淤青 |
| dermatologic | 皮疹、瘙痒、干燥 |
| obstetric_or_gynecologic | 月经异常、白带异常 |

---

## 四、输出格式

请保持原有的 JSON 结构不变，只补全以下内容：

1. 新增字段：`diseaseName_cn`、`description`、`typical_symptoms`、`differential_diagnosis`、`risk_factors`、`pathophysiology`（选填 `physical_exam`, `lab_tests`, `imaging`, `epidemiology`）
2. 修改 `axes[]` 中每条 `typical_findings`：给纯英文条目追加 `(中文)` 同义词

### 示例输出

```json
{
  "disease_name": "Cervical Spondylosis",
  "entry_type": "standard_western_disease",
  "m1_diagnosis_entry_allowed": true,
  "diseaseName_cn": "颈椎病",
  "description": "颈椎退行性变引起的颈椎结构异常，可压迫神经根、脊髓或椎动脉，导致颈痛、肢体麻木、眩晕等症状。",
  "typical_symptoms": ["颈部疼痛", "肩背放射痛", "上肢麻木", "眩晕", "头痛", "颈部活动受限"],
  "differential_diagnosis": ["腰椎间盘突出症", "肩周炎", "腕管综合征", "脊髓压迫症"],
  "risk_factors": ["年龄>40岁", "长期低头工作", "颈椎外伤史"],
  "pathophysiology": "颈椎间盘退变、骨质增生、韧带肥厚导致椎管狭窄或神经根/椎动脉受压迫",
  "axes": [
    {
      "axis": "structural_abnormality",
      "role": "primary",
      "weight": 0.95,
      "confidence": "high",
      "typical_findings": [
        "disc degeneration (椎间盘退变)",
        "osteophyte formation (骨赘形成)",
        "facet joint hypertrophy (关节突关节肥大)",
        "spinal canal stenosis on MRI/CT (MRI/CT示椎管狭窄)",
        "颈椎生理曲度变直或反弓"
      ]
    },
    {
      "axis": "smooth_muscle_spasm_or_pain",
      "role": "primary",
      "weight": 0.8,
      "confidence": "high",
      "typical_findings": [
        "neck pain (颈痛、颈部疼痛)",
        "cervical muscle spasm (颈肌痉挛)",
        "limited range of motion (颈部活动受限)",
        "referred pain to shoulders/arms (肩臂放射痛)",
        "肩膀痛", "脖子僵硬"
      ]
    },
    {
      "axis": "neurodegeneration_or_demyelination",
      "role": "secondary",
      "weight": 0.65,
      "confidence": "medium",
      "typical_findings": [
        "cervical radiculopathy (颈神经根病变)",
        "dermatomal numbness/weakness (皮节分布麻木/无力)",
        "myelopathy signs (脊髓病变体征)",
        "上肢麻木"
      ]
    },
    {
      "axis": "vascular_or_microcirculatory",
      "role": "secondary",
      "weight": 0.4,
      "confidence": "low",
      "typical_findings": [
        "vertebral artery compression (椎动脉受压)",
        "vertebrobasilar insufficiency symptoms, dizziness, vertigo (椎基底动脉供血不足表现、眩晕、头晕)",
        "眩晕", "头重", "颈A胀"
      ]
    }
  ],
  "need_external_check_if": [
    "myelopathy signs present (urgent MRI)",
    "progressive neurological deficit",
    "upper motor neuron signs",
    "bowel/bladder dysfunction"
  ],
  "must_not_be_primary_when": [
    "cervical disc herniation with acute radiculopathy as primary diagnosis",
    "spinal cord tumor not excluded",
    "cervical fracture/dislocation",
    "infection (epidural abscess, osteomyelitis)"
  ]
}
```

---

## 五、处理清单

共 **801 个疾病**，请逐条处理。输出为一个完整的 JSON 文件。

### 重要原则
1. **典型症状要覆盖中文患者常用描述**——不要只写医学术语，还要写患者会说的话
2. **同义词要准确**——"眩晕"≠"头晕"，"头重"≠"头痛"
3. **舌头、脉象的中医术语**（苔白/苔黄/舌暗/舌红等）只出现在对应的中西医结合疾病中，不要随意添加
4. **保持原有的 entry_type / m1_diagnosis_entry_allowed 不变**
5. **保持原有的 axes 结构（axis/role/weight/confidence）不变**
6. 只修改和新增字段，不要删除任何现有内容

---

## 六、关于 `typical_symptoms` 字段的补充说明

`typical_symptoms` 是**新增字段**，不是从 axes 中抽取的，它的用途是：

1. **快速症状匹配**——当 M1 引擎无法通过病理轴匹配到疾病时，会直接搜索 `typical_symptoms` 列表
2. **必须覆盖该疾病 90% 以上的常见患者主诉**——参考 uptodate / MSD Manual / 临床路径
3. **每个症状用 2-8 个字的短语**，不要写成句子
4. **如果某个症状在多个轴中出现，只写一次**到 `typical_symptoms` 即可

### 示例
```json
"typical_symptoms": [
  "眩晕", "天旋地转感", "恶心呕吐", "眼球震颤",
  "颈部疼痛", "头痛", "上肢麻木", "耳鸣"
]
```

---

## 七、分批处理策略

801 个疾病请按以下方式分批输出：

1. 每批输出 **50 个疾病**（完整的 JSON 片段）
2. 每批开头标注 `### Batch 1/16 (disease 1-50)` 等标记
3. 所有批次处理完成后，最终给出一个**可直接复制粘贴的完整 JSON**

---

## 八、质量检查清单

在输出前请确认每条记录满足：
- [ ] `diseaseName_cn` 不为空
- [ ] `description` 不为空（1-2句话）
- [ ] `typical_symptoms` 不为空（至少3条）
- [ ] `differential_diagnosis` 不为空（至少2条）
- [ ] 所有纯英文 `typical_findings` 已追加中文同义词
- [ ] 47 个常见中文症状词（见第二节）已根据疾病特征补充到对应位置
- [ ] 舌头/脉象类中医术语仅限中医相关疾病使用
