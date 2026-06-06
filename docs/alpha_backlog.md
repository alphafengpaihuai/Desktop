# Alpha Backlog

生成日期: 2026-06-06

## P0（本轮已修）

- **C10 喉癌高风险** → `run_m2_1_syndrome_reasoning` 和 `process()` 的 `needs_manual_review` 从硬编码 `False` 改为 `self._is_high_risk_disease(primary_disease)`，新增 `_HIGH_RISK_DISEASES` 集合（含 癌/恶性/梗死/出血/栓塞 等）和 `_is_high_risk_disease()` 方法。

## P1（非阻断 — 进入下一轮）

- 儿童咳嗽（C08）：`咳嗽 -> 支原体肺炎` 映射未必准确，"咳嗽"应视为症状而非病名，需 M1 提供更精确的病名。
- 孕期鼻塞流涕（C09）：`妊娠期合并上呼吸道感染` 作为孕期专用病名，M3 应增加妊娠禁忌审核（当前孕期 case 的禁忌检查依赖于 patient_info 中的 pregnancy 字段，需确认该字段被正确传递）。
- 带状疱疹后遗神经痛（C06）：仅 1 个证型选到 `气滞血瘀（带状疱疹后遗痛）`，评分区分度有限（confidence 较低）。
- C01 急性上呼吸道感染风热患者：M2-1 选证为"风寒感冒"而非"风热犯表"，提示 scorer 在典型风热症（发热、黄痰、口干）下仍可能偏向风寒。需复查评分逻辑。

## P2（优化 — 后续迭代）

- confidence 评分：多数病例在 0.3-0.6 之间，部分病例评分偏低但无更好候选。
- M2-3 加减候选：目前多数病例无加减候选（NO_CANDIDATE），后续需补充 modification_rules 数据。
- 生产展示层 `_strip_internal_trace_fields` 当前仅在 test 中验证，需在正式渲染路径中调用。
- M3 审方目前仅检查药理学禁忌。对于喉癌等重病，需增加 efficacy/pathology 层面的审方逻辑。
- `_HIGH_RISK_DISEASES` 列表当前为静态硬编码，后续可改为可配置（如 knowledge_base 中的 disease_risk 字段）。
