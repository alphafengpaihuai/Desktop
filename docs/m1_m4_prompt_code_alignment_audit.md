# M1-M4 Prompt/Spec 与代码执行一致性审计

更新时间：2026-06-04

## 审计矩阵

| 模块 | prompt/spec 要求 | 当前代码入口 | 是否真实执行 | 数据源 | 是否传给下游 | 测试覆盖 | 问题 | 修复动作 |
|---|---|---|---|---|---|---|---|---|
| M1 诊断 | 真实读取疾病库/标准库 | `m1_engine.M1DiagnosisEngine.__init__` | fixed | `diseases_core.json`, `data/m1_diagnostic_cards.json` | 是，新增 `m2_payload` | `test_m1_to_m4_chain_keeps_formal_prescription_blocked` | 原先输出缺少稳定下游 payload | `diagnose()` 增加 `primary_formula_entry_disease` 与 `m2_payload` |
| M1 alias | 病名标准化、alias 映射 | `standardize_disease_name()` | fixed | `m1_alias_map.json`, `data/m1_disease_alias_map.json` | 是 | `test_m1_explicit_disease_name_is_standardized_and_passed_downstream` | 原先 alias 未形成稳定代码入口 | 新增 alias 加载与标准化方法 |
| M1 诊断边界 | 症状与病名一致性校验，鼻炎不得误推支气管炎 | `_keyword_fallback()` | fixed | 本地疾病库 + 症状关键词 | 是 | `test_m1_rhinitis_symptoms_prioritize_rhinitis_not_bronchitis`, `test_m1_light_cough_without_lower_airway_evidence_does_not_force_bronchitis` | `无咳痰/无气促` 曾可能被当作阳性下呼吸道证据 | 增加鼻炎优先与否定词保护 |
| M1 低置信追问 | 信息不足必须 REQUEST_MORE_INFO | `diagnose()` | fixed | 患者输入 | 是 | `test_m1_insufficient_symptoms_request_more_info` | 症状过少时缺少稳定状态 | 增加 `NEED_MORE_INFO` 标准输出 |
| M1→M2 bridge | DISEASE_NAME_MAP/m2_fallback 必须命中 M2 KB | `bridge_server.py` | fixed | `data/m2_formula_knowledge.json` | 是 | `test_m1_m2_bridge_mapping_integrity` | 冠心病→头痛、头痛→偏头痛、儿童过敏性鼻炎断链 | 修复映射目标，新增静态完整性测试 |
| CHIEF_COMPLAINT_FORCE | 不得用慢性病史覆盖急性发热/感染诊断 | `should_skip_chief_complaint_force()` | fixed | 代码关键词规则 | 是 | `test_chief_force_heat_rewrite_guards` | 过敏性鼻炎曾覆盖上气道急性诊断 | 增加慢性病/急性病保护 |
| M2-1 辨证 | 输出 `syndrome_trace/evidence_trace` | `m2_engine.M2SyndromeSelector.process` | fixed | `data/m2_formula_knowledge.json` | 是，传给 M2-2/M2-3 合同字段 | `test_m2_1_syndrome_trace_schema_for_cold_cough_context`, `test_m2_1_heat_context_has_trace_and_no_prescription_fields` | 原先只有 `syndrome_differentiation`，trace 不稳定 | 新增 `syndrome_trace` 与 `evidence_trace` |
| M2-1 禁止方药剂量 | M2-1 不得输出处方字段 | `process()` 外层合同 | fixed | 无 | 是 | 同上 | 原入口为辨证选方混合模块，存在语义混杂 | 测试递归禁止 formal 字段 |
| M2-2 方剂候选 | 按 M1 标准病名 + 证型查方剂库 | `_load_syndromes()` / `process()` | fixed | `data/m2_formula_knowledge.json` | 是 | `test_m2_2_formula_query_and_candidate_contract` | 缺候选合同字段 | 增加 `candidate_only/need_m2_3/must_enter_m3/formula_candidates` |
| M2-2 no_candidate | 无匹配不得胡编 | `_build_no_candidate()` | fixed | M2 KB key | 是 | `test_m2_2_no_candidate_when_formula_kb_missing` | 原先返回 `error`，结构不统一 | 新增标准 `NO_CANDIDATE` 结构 |
| M2-3 加减候选 | 每味加减药需 target/evidence 字段 | `_normalize_modification_candidates()` | fixed | 病案/药物知识源 | 是 | `test_m2_3_modification_candidate_schema_and_no_low_grade` | 原 `modifications` 只有 herb/reason/source | 增加 target_disease/target_symptom/western_pathology/evidence_sources |
| M2-3 low_grade 隔离 | 不读取 low_grade candidates | `M2SyndromeSelector` | fixed | 未读取 | 是 | `test_m2_3_modification_candidate_schema_and_no_low_grade` | 需测试约束 | 测试确认 evidence_sources 不来自 low_grade |
| M3 药学审核 | 十八反十九畏、毒性药、孕妇/儿童风险 | `m3_engine.M3ClinicalReviewEngine.review` | fixed | `data/m3_herb_knowledge.json` + 代码矩阵 | 是 | `test_m3_real_safety_review_blocks_opposites_and_pregnancy_toxicity`, `test_m3_children_risk_warning_and_no_dose_claim_completion` | 原先 warnings 存在但缺结构化 safety_issues/review_passed | 增加 `safety_issues/review_passed/dosage_review_status` |
| M3 不放行处方 | M3 不单独允许正式处方 | `review()` | fixed | 代码常量 | 是 | M3 测试 | 原先缺显式字段 | 强制 `formal_prescription_allowed=False` |
| M4 复诊 | 只做疗效/恶化/病程窗口/转诊路由 | `m4_engine.M4RoutingEngine.route` | fixed | `m2_formula_knowledge.json`, `m4_kb_mapper`, 代码阈值 | 无处方下游 | `test_m4_followup_routing_does_not_modify_prescription` | 恶化发热气促需安全路由 | 增加气促/发热加重危险词，输出显式不放行字段 |
| M4 禁止改方 | 不输出 formula/modification candidates | `_build_result()` | fixed | 无 | 是 | `test_m4_followup_routing_does_not_modify_prescription` | 原先无显式空数组 | 输出固定空数组与 `formal_prescription_allowed=False` |
| M4 缺诊断卡路由 | 诊断标准缺失应回 M1 补齐，不应继续改方 | `services/m4/m4_followup_router._build_insufficient_response` | fixed | M1 诊断卡/输入校验 | 是，返回 M1 | `tests/test_m4_followup_router.py` | 缺 M1 卡片时旧代码返回 `PENDING`，脚本测试红字 | 缺原病名诊断标准卡片时 `routing_decision.action=RETURN_TO_M1` |
| M1 辅助知识映射 | `mapping_status` 必须与 M1 疾病库一致 | M1 auxiliary JSON + audit script | fixed | `diseases_core.json` | 是 | `test_m1_auxiliary_knowledge_service` | 已存在疾病仍标 `not_in_m1` | 修正 Appendicitis、Upper GI Bleeding、Urinary Retention、Diabetes Mellitus 等映射状态 |
| Excel 缓存构建 | 默认 Excel 不在本机时不阻断回归 | `build_disease_manifest`, `build_excel_diagnostic_details` | fixed | 已审核缓存 `data/disease_cache/*` | 是 | `test_disease_manifest`, `test_excel_disease_detail_extractor` | 桌面 Excel 缺失导致全量测试 error | 默认路径缺失且缓存存在时复用缓存；显式路径仍严格报错 |
| 旧 FullPipeline 边界 | 旧边界测试应可导入且不得放行正式处方 | `full_pipeline.FullPipeline` | fixed | 兼容 shim / pipeline trace | 是 | `test_pipeline_boundaries` | `FullPipeline` 符号缺失，旧脚本函数仍含剂量文本 | 新增测试兼容类，仅返回候选/审计 trace，`formal_prescription_allowed=False` |

## 仍 partial / blocked 的事项

| 项目 | 状态 | blocking_reason | 影响范围 | 下一步代码位置 |
|---|---|---|---|---|
| M2-1 与 M2-2 仍在同一个 `process()` 入口 | partial | 当前旧架构是“辨证选方”混合入口，拆分会影响 bridge 调用 | 语义层边界仍靠合同字段和测试守住 | `m2_engine.py` 可后续拆成 `select_syndrome()` 与 `select_formula_candidates()` |
| M2-3 初诊加减候选仍偏弱 | partial | 现有加减来源主要是病案/硬编码症状药物映射，非完整药物证据矩阵 | 部分初诊场景可能返回空加减候选 | `m2_engine._generate_modifications`, `_generate_evidence_based_modifications` |
| M3 剂量审核 | partial | 当前未接收结构化剂量输入，不能声称完成剂量审核 | 只能返回 `pending_dose_review` | `m3_engine.review` 后续可增加 structured dosage input |
| 全量测试 | fixed | 已修复默认 Excel 缓存回退、FullPipeline 兼容导出、M1 auxiliary mapping_status、M4 缺卡路由 | 全量回归通过 | `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'` |

## dead_code_candidates

```json
[
  {
    "file": "m2_engine.py",
    "symbol": "M2SyndromeSelector.full_pipeline",
    "reason": "会组装 final_prescription 和 herbs_with_dosage，可能绕过 candidate-only / M3 / trace closure 语义。",
    "is_still_imported": false,
    "risk_if_deleted": "未知旧调用可能依赖；直接删除风险中等。",
    "recommended_action": "wrap_with_guard"
  },
  {
    "file": "m2_engine.py",
    "symbol": "_extract_dosages",
    "reason": "只被 full_pipeline 使用，属于正式处方剂量链路风险点。",
    "is_still_imported": false,
    "risk_if_deleted": "若旧 full_pipeline 仍被人工调用会受影响。",
    "recommended_action": "deprecate"
  },
  {
    "file": "bridge_server.py",
    "symbol": "handle_selection_answers full_rx assembly",
    "reason": "直接拼装处方方案、用法和剂量文本；与当前 formal gate 暂停策略不一致。",
    "is_still_imported": true,
    "risk_if_deleted": "前端旧诊疗建议展示可能依赖。",
    "recommended_action": "wrap_with_guard"
  },
  {
    "file": "bridge_server.py",
    "symbol": "SYMPTOM_HERB_MAP",
    "reason": "直接把症状映射为加减药并拼入 herbs，绕过 M2-3 候选与 M3 前置审计语义。",
    "is_still_imported": true,
    "risk_if_deleted": "旧 WebSocket 处方生成显示可能变化。",
    "recommended_action": "wrap_with_guard"
  },
  {
    "file": "full_pipeline.py",
    "symbol": "m2_prescribe/m3_review/m4_followup/format_output/main",
    "reason": "旧脚本函数会拼接剂量、用法和完整随访文本；已新增 `FullPipeline` 兼容 shim 供边界测试使用，但旧脚本主流程仍不应接入正式链路。",
    "is_still_imported": true,
    "risk_if_deleted": "历史手工脚本或测试兼容可能受影响。",
    "recommended_action": "wrap_with_guard"
  }
]
```

## 安全结论

- `formal_prescription_allowed=true`：未允许。
- `prescription_text/final_formula/dosage/用法/疗程`：M1-M4 engine 层测试禁止输出。
- 正式药理库：未写入。
- low_grade candidates：未接入 M2/M3 来源。
- M3/trace_closure：本轮未推进正式处方授权，因此不存在绕过授权。
