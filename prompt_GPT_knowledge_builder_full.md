# M1 西医诊断知识库补全 Prompt

## 任务概述

请为以下 801 个疾病的 JSON 知识库补全信息。目前每个条目只有 `disease_name`、`entry_type`、`m1_diagnosis_entry_allowed` 和 `axes` 四个字段。需要补全以下缺失内容。

请根据你的医学知识（参考 MSD Manual、uptodate 等标准临床资源）逐条补全。

---

## 一、需要补全/修改的字段（每个疾病）

### 1. `diseaseName_cn`（必填）
中文标准病名。

### 2. `description`（必填）
1-2 句话概述该疾病的核心定义，简明扼要。

### 3. `typical_symptoms`（必填，新增字段）
患者常见的主诉/症状列表，**用中文描述**。每个症状用 2-8 个字的短语。
例如：`["眩晕、天旋地转感", "颈部疼痛伴肩背放射痛", "活动后胸闷胸痛"]`

### 4. `physical_exam`（选填）
关键体格检查发现。

### 5. `lab_tests`（选填）
关键实验室检查。

### 6. `imaging`（选填）
关键影像学检查。

### 7. `differential_diagnosis`（必填）
3-5 个需要鉴别的主要疾病名称（写中文病名）。

### 8. `risk_factors`（选填）
高危因素列表。

### 9. `pathophysiology`（选填）
简要发病机制（1-2句话）。

### 10. `epidemiology`（选填）
流行病学数据。

---

## 二、需要补全 `axes` 中的 `typical_findings`（最关键！）

**问题：** 目前的 `typical_findings` 55% 是纯英文，导致中文症状无法匹配。需要给每条英文 finding **追加中文同义词**。

**要求：** 每条 typical_finding 的格式改为 `英文 (中文)` 或直接追加中文同义词，例如：

```json
"typical_findings": [
  "dizziness / vertigo (眩晕、头晕)",
  "neck pain (颈痛、颈部疼痛)",
  "limited range of motion (颈部活动受限)",
  "vertebrobasilar insufficiency symptoms (椎基底动脉供血不足表现)"
]
```

### 已有同义词映射表（可直接复用）

| 英文 | 中文 |
|------|------|
| dizziness | 眩晕、头晕 |
| headache | 头痛 |
| cough | 咳嗽 |
| fever | 发热、发烧 |
| fatigue | 乏力、疲劳 |
| nausea | 恶心 |
| vomiting | 呕吐 |
| pain | 疼痛 |
| edema | 水肿、浮肿 |
| palpitations | 心悸、心慌 |
| dyspnea | 呼吸困难、气短 |
| insomnia | 失眠、入睡困难 |
| constipation | 便秘 |
| diarrhea | 腹泻 |
| numbness | 麻木 |
| tremor | 震颤 |
| syncope | 晕厥 |
| tinnitus | 耳鸣 |
| hypertension | 高血压 |
| hypotension | 低血压 |
| tachycardia | 心动过速 |
| bradycardia | 心动过缓 |
| seizure | 癫痫发作、抽搐 |
| bleeding | 出血 |
| anemia | 贫血 |
| rash | 皮疹 |
| pruritus | 瘙痒 |
| hematuria | 血尿 |
| proteinuria | 蛋白尿 |
| jaundice | 黄疸 |

### 以下 47 个常见中文症状词目前完全缺失，请根据疾病特征补充到对应疾病的对应轴中

`发烧、怕热、头晕、头胀、头重、眼花、视力模糊、口臭、口腔溃疡、气喘、心慌、胃痛、嗳气、食欲差、纳差、便血、颈痛、颈椎、腰酸、麻木、抽筋、颈A胀、脖子、肩膀痛、失眠、多梦、惊醒、嗜睡、烦躁、抑郁、荨麻疹、皮肤干燥、月经不调、月经量多、月经量少、痛经、闭经、带下、夜尿多、小便黄、苔白、苔黄、苔腻、苔薄、舌暗、舌红、舌淡`

---

## 三、36 个病理轴与典型中文症状对应表（供参考）

| 病理轴名 | 典型中文症状 |
|---------|-----------|
| infection_or_inflammation | 发热、红肿、疼痛、分泌物增多 |
| vascular_or_microcirculatory | 头晕、眩晕、供血不足、血压异常 |
| structural_abnormality | 结构异常、占位、压迫 |
| endocrine_or_hormonal_dysregulation | 月经紊乱、情绪波动、代谢异常 |
| neoplastic_or_proliferative | 肿块、异常增生 |
| immune_abnormality | 过敏、自身免疫表现 |
| cardiovascular_or_circulatory_risk | 胸痛、胸闷、心悸 |
| lower_airway_involvement | 咳嗽、咳痰、气喘 |
| upper_airway_involvement | 鼻塞、流涕、咽痛 |
| digestive_reflux_or_dysfunction | 腹痛、腹胀、腹泻、便秘 |
| mucosal_barrier_dysfunction | 黏膜损伤、溃疡 |
| fibrosis_or_tissue_remodeling | 纤维化、瘢痕形成 |
| coagulation_or_bleeding_disorder | 出血、瘀斑 |
| hematopoietic_or_marrow_dysfunction | 贫血、血细胞减少 |
| metabolic_or_insulin_resistance | 体重变化、多饮多尿 |
| bone_or_mineral_metabolism_disorder | 骨痛、骨折 |
| neurotransmitter_or_synaptic_dysfunction | 失眠、焦虑、抑郁 |
| neurodegeneration_or_demyelination | 认知下降、运动障碍 |
| neurovascular_or_cortical_spreading | 先兆症状、头痛 |
| autonomic_or_neurovegetative_dysfunction | 出汗异常、体位性低血压 |
| smooth_muscle_spasm_or_pain | 肌肉痉挛、疼痛 |
| connective_tissue_or_pelvic_floor_dysfunction | 关节痛、脱垂 |
| placental_insufficiency_or_dysfunction | 胎儿生长受限、胎盘异常 |
| hepatic_or_biliary_dysfunction | 黄疸、腹痛 |
| secretion_or_retention | 分泌物、潴留 |
| urinary_renal_risk | 尿频、尿急、水肿 |
| electrophysiological_or_ion_channel_dysfunction | 心律失常 |
| neurohormonal_activation | 心衰表现 |
| oxidative_or_free_radical_damage | 氧化损伤相关 |
| hypothalamic_pituitary_axis_dysfunction | 内分泌紊乱 |
| allergic_th2_inflammation | 过敏、哮喘 |
| autoimmune_or_immune_complex | 自身免疫表现 |
| vestibular_or_balance | 眩晕、平衡障碍 |
| visual_or_ocular_neuropathy | 视力下降、视野缺损 |

---

## 四、输出格式

保持原有的 JSON 结构不变，只补全/修改以下内容：

1. **新增字段：** `diseaseName_cn`、`description`、`typical_symptoms`、`differential_diagnosis`、`risk_factors`、`pathophysiology`
2. **可选字段：** `physical_exam`、`lab_tests`、`imaging`、`epidemiology`
3. **修改：** `axes[].typical_findings` 中给纯英文条目追加 `(中文)` 同义词

### 完整示例（Cervical Spondylosis）

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
        "肩膀痛",
        "脖子僵硬"
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
        "眩晕",
        "头重",
        "颈A胀"
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

## 五、重要原则

1. **典型症状要覆盖中文患者常用描述**——不要只写医学术语，还要写患者会说的话
2. **同义词要准确**——"眩晕"≠"头晕"，"头重"≠"头痛"，"心悸"≠"心慌"
3. **舌头/脉象类中医术语**（苔白/苔黄/舌暗/舌红等）只出现在对应的中西医结合疾病中，不要随意添加
4. **保持原有的 `entry_type`、`m1_diagnosis_entry_allowed` 不变**
5. **保持原有的 `axes` 结构（axis/role/weight/confidence）不变**
6. **只修改和新增字段，不要删除任何现有内容**
7. **`typical_symptoms` 与 `axes[].typical_findings` 不同**：前者是患者主诉快速匹配入口，后者是病理轴对应的客观发现
8. **每个字段都使用 UTF-8 编码**

---

## 六、801 个疾病列表（请逐条处理，输出完整 JSON）

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

## 七、输出说明

1. 输出一个**完整的 JSON 数组** `[ { ... }, { ... }, ... ]`，包含全部 801 条记录
2. 保持与原始文件相同的顺序
3. 所有字段使用 **UTF-8 编码**
4. 不要添加任何额外的解释或标记，只输出 JSON 本身
5. 每条疾病记录必须包含完整的 `axes` 字段（即使未修改）
