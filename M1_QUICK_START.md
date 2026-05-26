# M1 西医诊断模块 — 快速入门

## 启动方式

```bash
# 交互模式（推荐）
python3 m1_interface.py

# 批量测试
python3 m1_interface.py --batch

# 查看疾病详情
python3 m1_interface.py --detail "Acute Bronchitis"
python3 m1_interface.py --detail "急性支气管炎"

# 直接使用引擎
python3 -c "
from m1_engine import M1DiagnosisEngine
engine = M1DiagnosisEngine()
result = engine.diagnose('急性支气管炎', ['咳嗽', '发热', '咳痰'])
print(engine.format_diagnosis_result(result))
"
```

## 交互命令

| 命令 | 说明 |
|------|------|
| `diagnose` 或 `diag` | 开始诊断评估 |
| `list` 或 `ls` | 列出所有疾病（可加关键词过滤） |
| `search` | 模糊搜索疾病 |
| `detail` | 查看疾病的病理轴详情 |
| `help` | 帮助信息 |
| `quit` / `exit` | 退出 |

## diagnose 输入格式

```
诊> 疾病名 | 症状1, 症状2, 症状3, ...
```

示例：
```
诊> Acute Bronchitis | acute cough, low-grade fever, sore throat
诊> 急性支气管炎 | 咳嗽, 发热, 咽痛
诊> 社区获得性肺炎 | cough, fever >38.5, dyspnea
诊> 类风湿关节炎 | 晨僵, 关节肿痛, RF阳性
```

## 关键设计原则

1. **M1 阶段仅输出西医内容** — 禁止中医病名、证型、方剂、中药、治则
2. **病理轴匹配** — 基于 36 个病理轴和症状/体征的覆盖度评估
3. **Primary Formula Entry Disease** — 选择 primary 轴覆盖度最好的诊断
4. **Uncovered Targets** — 识别未被患者症状覆盖的重要病理轴

## 数据来源

- `diseases_core.json` — 240 条结构化数据
  - 218 个标准西医疾病
  - 13 个西医综合征
  - 9 个症状/体征
- `m1_name_mapping.json` — 66 个中英文疾病名映射
- 欠款清单中还有约 20 条待补充疾病

## 欠款清单

以下疾病需要补充到数据库中：
- Diabetes Mellitus (2型糖尿病)
- Chronic Renal Failure (慢性肾功能衰竭)
- Viral Hepatitis (病毒性肝炎)
- Cirrhosis (肝硬化)
- Nonalcoholic Fatty Liver Disease (脂肪肝)
- Cluster Headache (丛集性头痛)
- Panic Disorder (惊恐障碍)
- Insomnia (失眠症)
- Sjogren Syndrome (干燥综合征)
- Pulmonary Tuberculosis (肺结核)
- more...
