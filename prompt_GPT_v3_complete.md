# M1 西医诊断知识库补全 Prompt（v3 完整版）

## 任务概述

请为以下 801 个疾病的 JSON 知识库**全面补全临床信息**。每个疾病目前已有 `disease_name`、`entry_type`、`m1_diagnosis_entry_allowed`、`axes[]` 四个核心结构。

你需要做两件事：
1. **新增字段**：补全中文病名、临床表现、诊断、治疗等信息
2. **增强 axes**：给 typical_findings 追加中文同义词

---

## 第一部分：需要补全的字段（每个疾病都必须补）

### 1.1 `diseaseName_cn`（必填）
标准中文病名。如 `"异常子宫出血"`、`"颈椎病"`、`"2型糖尿病"`。

### 1.2 `description`（必填）
**2-4 句话**的临床描述，格式为：**病因/机制 | 核心病理改变 | 典型表现**。

✅ 示例（好）：
> 颈椎病是因颈椎间盘退变、骨质增生、韧带肥厚导致椎管狭窄或神经根/椎动脉受压的一组疾病。临床表现为颈痛、肩背放射痛、上肢麻木，严重时可出现脊髓压迫症状如行走不稳、大小便功能障碍。好发于中老年及长期低头工作者。

❌ 不要这样写（泛化模板）：
> 以骨关节或肌肉软组织疼痛、肿胀、畸形、活动受限或神经放射痛为主要表现的运动系统疾病。

### 1.3 `typical_symptoms`（必填）
**患者真实语言**的症状列表（每条约 3-15 字），**分条列出，不要写成一整句**。覆盖 90% 以上的就诊主诉。

✅ 示例（颈椎病）：
```json
"typical_symptoms": [
  "颈部疼痛",
  "肩背放射痛",
  "上肢麻木",
  "手指麻木",
  "眩晕、天旋地转感",
  "头痛、偏头痛",
  "颈部僵硬",
  "转头时颈痛加重",
  "严重时行走不稳",
  "手部精细动作困难",
  "上肢无力"
]
```

✅ 示例（2型糖尿病）：
```json
"typical_symptoms": [
  "多饮多尿",
  "体重下降",
  "乏力疲劳",
  "视物模糊",
  "反复感染",
  "伤口愈合慢",
  "四肢末端麻木",
  "皮肤瘙痒",
  "口渴"
]
```

❌ 不要这样写（泛化模板）：
> ["局部疼痛、活动受限、肿胀、畸形、神经放射痛或功能障碍", "症状常与外伤、退变、过度使用或炎症相关"]

### 1.4 `differential_diagnosis`（必填）
列出 3-5 个需要鉴别的主要疾病（中文名），且其中应包含**最易混淆的疾病**。

### 1.5 `risk_factors`（必填）
高危因素列表，每个条目 2-10 字。

### 1.6 `pathophysiology`（必填）
简要发病机制，1-2 句话。**不要写成"以内分泌激素……为核心的疾病"这种泛化句式**。

### 1.7 `physical_exam`（必填）
关键体格检查发现，3-6 条。例如 `"颈椎棘突压痛"`、`"Spurling 试验阳性"`、`"Hoffmann 征阳性"`。

### 1.8 `lab_tests`（必填）
关键实验室检查，3-6 条。如果该病主要靠临床诊断，写 `"无明显特异实验室指标"`。

### 1.9 `imaging`（必填）
关键影像学检查，2-5 条。如果不需要，写 `"无明显特异影像学检查"`。

### 1.10 `complications`（选填）
可能的并发症列表，例如 `"脊髓压迫"`、`"猝倒症"`、`"糖尿病足"`。

### 1.11 `prognosis`（选填）
1-2 句话的预后说明。

### 1.12 `epidemiology`（选填）
1 句话的流行病学数据，例如 `"好发于40岁以上人群，女性略多于男性"`。

---

## 第二部分：增强 axes[].typical_findings（最关键！）

### 当前问题
55% 的 typical_findings 是纯英文，导致中文症状无法匹配。

### 要求
对每条 **纯英文** 的 typical_finding，**追加中文翻译**。格式为 `英文原文（中文翻译）`。

### 核心约束（非常重要！）
1. **不要删除**任何现有的 typical_finding
2. **不要新增**任何 typical_finding（已在旧数据中的保留不动）
3. **不要修改已有的中文内容**（如果某条 already 有中文，保留原样）
4. **只对纯英文的条目追加中文翻译**

### 格式示例

| 修改前 | 修改后 |
|--------|--------|
| `"heavy menstrual bleeding (>80mL/cycle)"` | `"heavy menstrual bleeding (>80mL/cycle)（月经量过多>80mL/周期）"` |
| `"osteophyte formation"` | `"osteophyte formation（骨赘形成）"` |
| `"limited range of motion"` | `"limited range of motion（活动受限）"` |
| `"vertebrobasilar insufficiency symptoms (dizziness, visual changes)"` | `"vertebrobasilar insufficiency symptoms (dizziness, visual changes)（椎基底动脉供血不足：眩晕、视物模糊）"` |
| `"hyperglycemia"` | `"hyperglycemia（高血糖）"` |

### 已有同义词映射表（可直接复用，但请根据上下文选择最准确的中文）

| 英文 | 中文（可选多个） |
|------|-----------------|
| dizziness | 眩晕、头晕 |
| headache | 头痛 |
| cough | 咳嗽 |
| fever | 发热、发烧 |
| fatigue | 乏力、疲劳 |
| nausea | 恶心 |
| vomiting | 呕吐 |
| pain | 疼痛、痛 |
| edema | 水肿、浮肿 |
| palpitations | 心悸、心慌 |
| dyspnea | 呼吸困难、气短 |
| insomnia | 失眠、入睡困难 |
| constipation | 便秘 |
| diarrhea | 腹泻 |
| numbness | 麻木、发麻 |
| tremor | 震颤、发抖 |
| syncope | 晕厥 |
| tinnitus | 耳鸣 |
| hypertension | 高血压 |
| hypotension | 低血压 |
| tachycardia | 心动过速、心跳过快 |
| bradycardia | 心动过缓、心跳过慢 |
| seizure | 癫痫发作、抽搐 |
| bleeding | 出血 |
| anemia | 贫血 |
| rash | 皮疹 |
| pruritus | 瘙痒、痒 |
| hematuria | 血尿 |
| proteinuria | 蛋白尿 |
| jaundice | 黄疸 |
| vertigo | 眩晕、天旋地转 |
| chest pain | 胸痛、胸闷胸痛 |
| weight loss | 体重下降、消瘦 |
| weight gain | 体重增加、发胖 |
| polyuria | 多尿 |
| polydipsia | 多饮、烦渴 |
| blurred vision | 视物模糊、视力下降 |
| back pain | 腰背痛 |
| joint pain | 关节痛 |
| muscle weakness | 肌无力、肢体无力 |

---

## 第三部分：不要犯的典型错误

下面是上批 GPT 输出中发现的常见问题，**请务必避免**：

### ❌ 错误1：typical_symptoms 写成泛化模板
**错误表现**：
```json
"typical_symptoms": ["局部疼痛、活动受限、肿胀、畸形", "症状常与外伤、退变或炎症相关"]
```
**正确做法**：
```json
"typical_symptoms": ["颈部疼痛", "肩背放射痛", "上肢麻木", "眩晕"]
```

### ❌ 错误2：description 写成科室分类模板
**错误表现**：
> "以内分泌激素、血糖、脂代谢、电解质或体重变化异常为核心的疾病。诊断依赖实验室复核和病因分层。"

**正确做法**：
> "2型糖尿病是因胰岛素抵抗和/或胰岛素分泌不足导致慢性高血糖的代谢性疾病。典型表现为多饮、多尿、多食、体重下降。长期高血糖可导致微血管并发症（视网膜病变、肾病、神经病变）和大血管并发症（心脑血管疾病）。"

### ❌ 错误3：typical_findings 被简化/泛化
**错误表现**：
- 旧：`"osteophyte formation"` → 错误：`"deformity (畸形)"`
- 旧：`"vertebral artery compression"` → 错误：`"mass effect or compression (占位或压迫)"`

**正确做法**：保留原文，只追加中文翻译→ `"osteophyte formation（骨赘形成）"`

### ❌ 错误4：GPT 自创字段
不要添加 `_generation_note`、`_source_existing_card_matched` 等内部标记字段。

### ❌ 错误5：修改 entry_type
保持原有的 `entry_type`、`m1_diagnosis_entry_allowed` 不变。

---

## 第四部分：36 个病理轴与典型中文发现对照（供 typical_findings 翻译参考）

| 病理轴名 | 典型英文 finding → 中文翻译 |
|---------|---------------------------|
| `infection_or_inflammation` | inflammation (炎症), infection (感染), abscess (脓肿), purulent discharge (脓性分泌物), leukocytosis (白细胞增多) |
| `vascular_or_microcirculatory` | ischemia (缺血), microvascular dysfunction (微血管功能障碍), vasospasm (血管痉挛), thrombosis (血栓形成) |
| `structural_abnormality` | stenosis (狭窄), hypertrophy (肥大/增生), herniation (疝出/突出), deformity (畸形), compress/compression (压迫) |
| `endocrine_or_hormonal_dysregulation` | hormone imbalance (激素紊乱), menstrual irregularity (月经不调), thyroid dysfunction (甲状腺功能异常) |
| `neoplastic_or_proliferative` | tumor/mass (肿瘤/肿块), hyperplasia (增生), malignancy (恶性肿瘤), metastasis (转移) |
| `immune_abnormality` | autoantibody (自身抗体), immune complex deposition (免疫复合物沉积), hypersensitivity (超敏反应) |
| `cardiovascular_or_circulatory_risk` | atherosclerosis (动脉粥样硬化), plaque (斑块), coronary stenosis (冠脉狭窄) |
| `lower_airway_involvement` | bronchoconstriction (支气管收缩), airway inflammation (气道炎症), mucus hypersecretion (黏液高分泌) |
| `upper_airway_involvement` | nasal congestion (鼻塞), rhinorrhea (流涕), sinusitis (鼻窦炎) |
| `digestive_reflux_or_dysfunction` | reflux (反流), dysmotility (动力障碍), malabsorption (吸收不良) |
| `coagulation_or_bleeding_disorder` | coagulopathy (凝血功能障碍), thrombocytopenia (血小板减少), hemorrhage (出血) |
| `hematopoietic_or_marrow_dysfunction` | bone marrow failure (骨髓衰竭), cytopenia (血细胞减少), dysplasia (发育异常) |
| `metabolic_or_insulin_resistance` | insulin resistance (胰岛素抵抗), hyperglycemia (高血糖), dyslipidemia (血脂异常) |
| `bone_or_mineral_metabolism_disorder` | osteoporosis (骨质疏松), hypercalcemia (高钙血症), osteopenia (骨量减少) |
| `neurodegeneration_or_demyelination` | demyelination (脱髓鞘), axonal loss (轴突丢失), gliosis (胶质增生) |
| `neurotransmitter_or_synaptic_dysfunction` | dopamine deficiency (多巴胺缺乏), serotonin imbalance (5-羟色胺失衡) |
| `smooth_muscle_spasm_or_pain` | muscle spasm (肌肉痉挛), spasticity (痉挛状态), rigidity (僵硬) |
| `connective_tissue_or_pelvic_floor_dysfunction` | connective tissue disorder (结缔组织病), pelvic relaxation (盆底松弛) |
| `mucosal_barrier_dysfunction` | mucosal erosion (黏膜糜烂), ulceration (溃疡), barrier disruption (屏障破坏) |
| `fibrosis_or_tissue_remodeling` | fibrosis (纤维化), scarring (瘢痕形成), remodeling (重塑) |
| `autonomic_or_neurovegetative_dysfunction` | autonomic dysfunction (自主神经功能紊乱), orthostatic hypotension (体位性低血压) |
| `hepatic_or_biliary_dysfunction` | liver dysfunction (肝功能障碍), cholestasis (胆汁淤积), cirrhosis (肝硬化) |
| `urinary_renal_risk` | renal impairment (肾功能损害), urinary obstruction (尿路梗阻), glomerular injury (肾小球损伤) |
| `electrophysiological_or_ion_channel_dysfunction` | arrhythmia (心律失常), conduction block (传导阻滞), ion channel disorder (离子通道病) |
| `vestibular_or_balance` | vestibular dysfunction (前庭功能障碍), nystagmus (眼震), balance disorder (平衡障碍) |
| `visual_or_ocular_neuropathy` | optic atrophy (视神经萎缩), visual field defect (视野缺损), retinal damage (视网膜损伤) |

---

## 第五部分：801 个疾病列表

Abnormal Uterine Bleeding
Acne Vulgaris
Acute Bronchitis
Acute Coronary Syndrome
Acute Glomerulonephritis
Acute Kidney Injury
Acute Myocardial Infarction
Acute Pancreatitis
Acute Respiratory Distress Syndrome
Acute Sinusitis
Acute Tonsillitis
Adenomyosis
Adhesive Capsulitis
Adult-Onset Still Disease
Allergic Contact Dermatitis
Allergic Rhinitis
Amblyopia
Amenorrhea
Ankylosing Spondylitis
Aplastic Anemia
Atherosclerosis
Atopic Dermatitis
Atrial Fibrillation
Bacterial Vaginosis
Bipolar Disorder
Bronchial Asthma
Bronchiectasis
Cardiac Arrhythmia
Cerebral Hemorrhage
Cerebral Infarction
Cervical Spondylosis
Cervicitis
Cholecystitis
Cholelithiasis
Chronic Bronchitis
Chronic Diarrhea
Chronic Gastritis
Chronic Heart Failure
Chronic Hepatitis B
Chronic Hepatitis C
Chronic Kidney Disease
Chronic Obstructive Pulmonary Disease
Chronic Pancreatitis
Chronic Rhinitis
Chronic Sinusitis
Chronic Urticaria
Community-Acquired Pneumonia
Contact Dermatitis
Coronary Artery Disease
Crohn Disease
Drowning
Dysmenorrhea
Eczema
Endometriosis
Epilepsy
Essential Hypertension
Febrile Seizure
Fracture
Functional Constipation
Functional Dyspepsia
Gastroesophageal Reflux Disease
Generalized Anxiety Disorder
Gout
Hand, Foot, and Mouth Disease
Hashimoto Thyroiditis
Heart Failure
Herpes Simplex
Herpes Zoster
Hyperlipidemia
Hyperthyroidism
Hypothyroidism
IgA Vasculitis
Inflammatory Bowel Disease
Insomnia Disorder
Interstitial Lung Disease
Iron Deficiency Anemia
Irritable Bowel Syndrome
Kawasaki Disease
Leptospirosis
Liver Cirrhosis
Lower Extremity Varicose Veins
Lumbar Disc Herniation
Major Depressive Disorder
Malaria
Menopausal Syndrome
Migraine
Myocarditis
Nephrolithiasis
Nephrotic Syndrome
Neurodermatitis
Obsessive-Compulsive Disorder
Ocular Trauma
Onychomycosis
Osteoarthritis
Osteoporosis
Ovarian Cyst
Parkinson Disease
Pediatric Acute Diarrhea
Pediatric Bronchopneumonia
Pediatric Seizure
Pelvic Inflammatory Disease
Peptic Ulcer Disease
Pneumonia
Poisoning
Polycystic Ovary Syndrome
Post-Traumatic Stress Disorder
Psoriasis
Pterygium
Pulmonary Embolism
Pulmonary Fibrosis
Pyelonephritis
Refractive Error
Rheumatoid Arthritis
Schizophrenia
Seborrheic Dermatitis
Sepsis
Shock
Sjögren Syndrome
Somatic Symptom Disorder
Stable Angina Pectoris
Strabismus
Subacute Thyroiditis
Systemic Lupus Erythematosus
Systemic Sclerosis
Tendinitis
Tenosynovitis
Tension-Type Headache
Thyroid Nodule
Tinea Manuum
Tinea Pedis
Transient Ischemic Attack
Type 2 Diabetes Mellitus
Ulcerative Colitis
Unstable Angina
Urinary Tract Infection
Urticaria
Uterine Leiomyoma
Vaginitis
Varicella
Vulvovaginal Candidiasis
乳腺增生症
乳腺炎
乳腺癌
乳腺纤维腺瘤
产后出血
产褥感染
传染性单核细胞增多症
伤寒
先兆流产
分泌性中耳炎
前列腺炎
前列腺癌
前庭神经炎
前置胎盘
前葡萄膜炎
勃起功能障碍
包皮龟头炎
单纯疱疹病毒性角膜炎
口腔念珠菌病
口腔溃疡
复发性阿弗他溃疡
外耳道炎
妊娠剧吐
妊娠期糖尿病
妊娠期高血压疾病
子痫前期
巩膜炎
布鲁菌病
干眼症
年龄相关性黄斑变性
异位妊娠
急性中耳炎
急性乳腺炎
急性牙髓炎
急性闭角型青光眼
感音神经性听力损失
慢性前列腺炎/慢性盆腔疼痛综合征
慢性化脓性中耳炎
新生儿肺炎
新生儿败血症
新生儿黄疸
早产
早泄
梅毒
流行性乙型脑炎
流行性感冒
流行性腮腺炎
淋病
牙周炎
牙龈炎
猩红热
玻璃体后脱离
玻璃体混浊
男性不育症
病毒性结膜炎
登革热
白内障
百日咳
睑缘炎
睾丸扭转
睾丸炎
突发性耳聋
精索静脉曲张
糖尿病视网膜病变
细菌性痢疾
细菌性结膜炎
结核病
结膜炎
缺血性视神经病变
耳硬化症
耳鸣
肠结核
肺结核
胎儿生长受限
胎盘早剥
良性前列腺增生
艾滋病
莱姆病
葡萄膜炎
表层巩膜炎
视神经炎
视网膜脱离
角膜溃疡
角膜炎
过敏性结膜炎
阴茎硬结症
附睾炎
隐睾
霍乱
霰粒肿
青光眼
鞘膜积液
颞下颌关节紊乱病
风疹
麦粒肿
麻疹
鼓膜穿孔
鼻中隔偏曲
鼻出血
鼻息肉
Anovulatory Uterine Bleeding (AUB-O)
Endometrial Polyp
Endometrial Hyperplasia
Hyperprolactinemia
HIV Infection
Pulmonary Tuberculosis
Syphilis
Gonorrhea
Hemophilia
Thrombocytopenia
Pericarditis
Cardiomyopathy
Pleural Effusion
Pneumothorax
Polymyalgia Rheumatica
Anorexia Nervosa
Bulimia Nervosa
慢性鼻炎
上气道咳嗽综合征
支气管哮喘
咳嗽变异性哮喘
社区获得性肺炎
支原体肺炎
慢性阻塞性肺疾病
毛细支气管炎
腺样体肥大
阻塞性睡眠呼吸暂停
急性咽炎
新型冠状病毒感染
手足口病
胃食管反流病
慢性胃炎
功能性消化不良
肠易激综合征
功能性便秘
急性胃肠炎
炎症性肠病
胆石症
原发性高血压
冠心病
稳定型心绞痛
急性心肌梗死
2型糖尿病
1型糖尿病
尿路感染
膀胱炎
泌尿系结石
贫血
脑梗死
短暂性脑缺血发作
Hepatocellular Carcinoma
Chronic Pharyngitis
变应性鼻炎
急性鼻炎
急性鼻窦炎
慢性鼻窦炎
儿童哮喘
急性支气管炎
慢性支气管炎
儿童肺炎
儿童支气管肺炎
支气管扩张
肺栓塞
肺纤维化
慢性咽炎
急性喉炎
慢性喉炎
会厌炎
急性扁桃体炎
慢性扁桃体炎
扁桃体肥大
水痘
疱疹性咽峡炎
疱疹性口炎
幽门螺杆菌感染
消化性溃疡
慢性腹泻
克罗恩病
溃疡性结肠炎
胆囊炎
急性胰腺炎
慢性胰腺炎
肝硬化
慢性乙型肝炎
非酒精性脂肪性肝病
急性阑尾炎
肠梗阻
憩室炎
痔
肛裂
结直肠癌
消化道出血
急性冠脉综合征
心力衰竭
心房颤动
动脉粥样硬化
心肌炎
心包炎
室上性心动过速
室性心动过速
深静脉血栓形成
下肢静脉曲张
高脂血症
糖尿病酮症酸中毒
低血糖
甲状腺功能亢进症
甲状腺功能减退症
桥本甲状腺炎
甲状腺结节
肥胖症
代谢综合征
骨质疏松症
库欣综合征
Addison病
高尿酸血症
痛风
肾盂肾炎
慢性肾脏病
急性肾损伤
肾病综合征
肾小球肾炎
急性前列腺炎
尿失禁
血尿
缺铁性贫血
巨幼细胞性贫血
再生障碍性贫血
溶血性贫血
急性白血病
慢性髓性白血病
慢性淋巴细胞白血病
淋巴瘤
多发性骨髓瘤
免疫性血小板减少症
血友病
弥散性血管内凝血
中性粒细胞减少症
类风湿关节炎
骨关节炎
系统性红斑狼疮
强直性脊柱炎
干燥综合征
系统性硬化症
IgA血管炎
川崎病
荨麻疹
特应性皮炎
接触性皮炎
痤疮
银屑病
带状疱疹
单纯疱疹
足癣
甲癣
脂溢性皮炎
蜂窝织炎
脓疱疮
疥疮
偏头痛
紧张型头痛
脑出血
癫痫
帕金森病
阿尔茨海默病
三叉神经痛
面神经麻痹
坐骨神经痛
重症肌无力
多发性硬化
急性细菌性脑膜炎
病毒性脑炎
抑郁障碍
广泛性焦虑障碍
失眠障碍
惊恐障碍
躯体症状障碍
子宫肌瘤
子宫内膜异位症
卵巢囊肿
多囊卵巢综合征
阴道炎
盆腔炎性疾病
痛经
闭经
更年期综合征
异常子宫出血
宫颈炎
腰椎间盘突出症
颈椎病
骨折
肩周炎
肌腱炎
腱鞘炎
低血压
中暑
热射病
新生儿缺氧缺血性脑病
鹅口疮
低钠血症
脓毒症
感染性休克
菌血症
呼吸道合胞病毒感染
腺病毒感染
急性喉气管支气管炎
病毒性肺炎
吸入性肺炎
医院获得性肺炎
肺脓肿
胸腔积液
气胸
慢性咳嗽
声带功能障碍
过敏性支气管肺曲霉病
职业性哮喘
过敏反应
食物过敏
药物过敏
玫瑰疹
传染性红斑
诺如病毒胃肠炎
轮状病毒胃肠炎
沙门菌感染
志贺菌病
弯曲菌感染
艰难梭菌相关性腹泻
疟疾
钩端螺旋体病
衣原体感染
人类免疫缺陷病毒感染
食管炎
吞咽困难
贲门失弛缓症
乳糖不耐受
乳糜泻
吸收不良综合征
急性病毒性肝炎
慢性丙型肝炎
酒精相关性肝病
药物性肝损伤
急性胆管炎
胆总管结石
胃癌
肝细胞癌
胰腺癌
肝性脑病
腹水
胆汁淤积
继发性高血压
高血压急症
主动脉夹层
主动脉瓣狭窄
二尖瓣反流
二尖瓣狭窄
感染性心内膜炎
扩张型心肌病
肥厚型心肌病
外周动脉疾病
慢性静脉功能不全
淋巴水肿
晕厥
心悸
Graves病
亚急性甲状腺炎
甲状腺癌
高渗高血糖状态
糖尿病肾病
糖尿病周围神经病变
高钙血症
低钙血症
高钠血症
高钾血症
低钾血症
抗利尿激素分泌异常综合征
甲状旁腺功能亢进症
甲状旁腺功能减退症
IgA肾病
多囊肾病
肾积水
尿潴留
急性间质性肾炎
急性肾小管坏死
膀胱癌
肾细胞癌
地中海贫血
葡萄糖-6-磷酸脱氢酶缺乏症
恶性贫血
慢性病性贫血
血栓性血小板减少性紫癜
溶血尿毒综合征
血管性血友病
霍奇金淋巴瘤
非霍奇金淋巴瘤
真性红细胞增多症
原发性血小板增多症
骨髓增生异常综合征
皮肌炎
多发性肌炎
风湿性多肌痛
反应性关节炎
银屑病关节炎
化脓性关节炎
纤维肌痛
结节性多动脉炎
巨细胞动脉炎
白塞病
玫瑰痤疮
白癜风
斑秃
毛囊炎
体癣
股癣
玫瑰糠疹
药疹
史蒂文斯-约翰逊综合征
丹毒
坏死性筋膜炎
皮肤脓肿
黑色素瘤
基底细胞癌
鳞状细胞癌
蛛网膜下腔出血
脑肿瘤
周围神经病变
良性阵发性位置性眩晕
梅尼埃病
吉兰-巴雷综合征
肌萎缩侧索硬化
脊髓压迫症
腕管综合征
丛集性头痛
谵妄
双相障碍
精神分裂症
创伤后应激障碍
强迫障碍
注意缺陷多动障碍
孤独症谱系障碍
神经性厌食症
酒精使用障碍
子宫腺肌症
宫颈癌
子宫内膜癌
卵巢癌
外阴阴道假丝酵母菌病
细菌性阴道病
滴虫性阴道炎
前庭大腺囊肿
骨髓炎
滑囊炎
跖筋膜炎
肩袖损伤
半月板损伤
前交叉韧带损伤
脊柱侧弯
急性腰扭伤
肋软骨炎
急性鼻咽炎
急性会厌炎
眩晕
急性结膜炎
急性胃炎
胆道蛔虫症
肠套叠
肠系膜淋巴结炎
急性腹膜炎
肝脓肿
门静脉高压症
食管静脉曲张
房性早搏
室性早搏
病态窦房结综合征
房室传导阻滞
肺动脉高压
先天性心脏病
川崎病冠状动脉病变
低镁血症
高镁血症
低磷血症
高磷血症
痛风性肾病
急性膀胱过度活动症
间质性膀胱炎
急性附睾炎
子痫
胎膜早破
早产临产
羊水栓塞
胎儿窘迫
自然流产
葡萄胎
原发性开角型青光眼
翼状胬肉
传导性听力损失
胆脂瘤
嗅觉障碍
口腔癌
喉癌
食管癌
肺癌
胆囊癌
胆管癌
肾癌
睾丸癌
急性淋巴细胞白血病
急性髓系白血病
慢性粒单核细胞白血病
镰状细胞病
遗传性球形红细胞增多症
铁粒幼细胞性贫血
叶酸缺乏症
维生素B12缺乏症
维生素D缺乏症
坏血病
维生素K缺乏症
蛋白质能量营养不良
脱水
代谢性酸中毒
代谢性碱中毒
呼吸性酸中毒
呼吸性碱中毒
乳酸酸中毒
横纹肌溶解症
急性呼吸窘迫综合征
急性低氧性呼吸衰竭
慢性呼吸衰竭
休克
低容量性休克
心源性休克
过敏性休克
急性中毒
一氧化碳中毒
有机磷中毒
对乙酰氨基酚中毒
酒精中毒
阿片类药物中毒
镇静催眠药中毒
铁中毒
铅中毒
冻伤
低体温症
烧伤
电击伤
溺水
急性高山病
脑震荡
创伤性脑损伤
颅内压增高
脑积水
痴呆
血管性痴呆
路易体痴呆
正常压力脑积水
小脑共济失调
儿童脑瘫
发育迟缓
语言发育迟缓
抽动障碍
遗尿症
儿童便秘
儿童腹泻病
儿童缺铁性贫血
儿童生长迟缓
性早熟
矮小症
肥胖儿童代谢异常
新生儿低血糖
早产儿呼吸窘迫综合征
新生儿坏死性小肠结肠炎
新生儿惊厥
新生儿溶血病
先天性甲状腺功能减退症
苯丙酮尿症
糖原累积病
黏多糖贮积症
法洛四联症
室间隔缺损
房间隔缺损
动脉导管未闭
肠易激相关腹痛
食物蛋白诱导小肠结肠炎综合征
乳糜泻相关贫血
药物热
不明原因发热
慢性疲劳综合征
纤维肌痛伴疲劳
肌筋膜疼痛综合征
梨状肌综合征
跟腱炎
网球肘
高尔夫球肘
膝骨关节炎
髋关节骨关节炎
椎管狭窄
骨质疏松性骨折
股骨头坏死
滑膜炎
多汗症
冻疮
鸡眼
胼胝
寻常疣
跖疣
传染性软疣
疤痕疙瘩
脂肪瘤
表皮样囊肿
压疮
慢性下肢溃疡
过敏性接触性皮炎
刺激性接触性皮炎
日光性皮炎
多形红斑
结节性红斑
天疱疮
大疱性类天疱疮
甲沟炎
嵌甲
念珠菌性皮炎
花斑癣
头癣
过敏性紫癜
成人斯蒂尔病
复发性多软骨炎
混合性结缔组织病
抗磷脂综合征
系统性血管炎
ANCA相关性血管炎
干燥综合征相关肺病
狼疮性肾炎
类风湿肺病
低血容量
水中毒
高氯性酸中毒
酮症酸中毒非糖尿病性
尿崩症
腺垂体功能减退症
泌乳素瘤
肢端肥大症
甲状腺危象
黏液性水肿昏迷
肾上腺危象
嗜铬细胞瘤
原发性醛固酮增多症
肾小管酸中毒
肾静脉血栓形成
肾动脉狭窄
梗阻性尿路病
膀胱过度活动症
慢性盆腔疼痛综合征
包茎
尿道下裂
肾盂输尿管连接部梗阻
肺水肿
肺不张
纵隔肿瘤
眼眶蜂窝织炎
急性虹膜睫状体炎
胆囊息肉
脂肪泻
药物性肾损伤

---

## 第六部分：输出格式

1. 输出一个 **完整的 JSON 数组**，包含全部 801 条
2. **保持与输入文件相同的字段顺序**（原有字段在前，新增字段在后）
3. **不要添加内部标记字段**（如 `_generation_note`、`_source_existing_card_matched`）
4. 使用 **UTF-8 编码**
5. 不要有任何额外文本或解释，只输出 JSON

---

## 第七部分：质量自查清单

请在提交前逐条确认：

- [ ] `diseaseName_cn` ✅ 801/801 完整
- [ ] `description` ✅ 2-4 句话，不是泛化模板
- [ ] `typical_symptoms` ✅ 每条 3-15 字，覆盖患者真实语言
- [ ] `differential_diagnosis` ✅ 3-5 个，含最易混淆疾病
- [ ] `physical_exam` ✅ 3-6 条具体体征
- [ ] `lab_tests` ✅ 3-6 条具体化验
- [ ] `imaging` ✅ 2-5 条
- [ ] `risk_factors` ✅ 3-5 个
- [ ] `pathophysiology` ✅ 不是泛化句式
- [ ] `complications` ✅ 如果有并发症的话
- [ ] `axes[].typical_findings` ✅ 所有纯英文条目已追加中文翻译
- [ ] `entry_type` ✅ 未被修改
- [ ] 无 `_generation_note` 等自创字段
