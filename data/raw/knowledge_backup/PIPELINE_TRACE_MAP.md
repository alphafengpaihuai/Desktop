# Pipeline Trace Map

本文件记录一次正式处方在工程上必须闭合的 trace。当前审计阶段不新增业务逻辑；若任一 trace 缺失，正式处方应保持禁止状态。

## Formal Prescription Trace Closure

一次正式处方至少需要以下链路全部存在、可追溯、且未被下游覆盖：

1. M1 diagnosis trace
   - 输入：主诉、现病史、既往史、年龄、妊娠/儿童/肝肾异常等安全信号。
   - 输出：西医诊断候选、病名-症状一致性、危险信号、是否需要 external_check。
   - 闭合条件：病名不可直接采信；危险信号必须留痕并进入后续安全门。

2. Symptom-pathology trace
   - 输入：当前症状、舌脉、病程变化、既往病名。
   - 输出：症状与病理方向、禁忌方向、冲突方向。
   - 闭合条件：旧病名不得在无当前症状支持时直接贡献药物。

3. Syndrome trace
   - 输入：M2-1 辨证、中医病机、八纲、脏腑轴、证素。
   - 输出：候选证型、轴一致性、Validation Gate 结果。
   - 闭合条件：多轴激活必须有依据；安全信号不得被辨证覆盖。

4. RAG formula trace
   - 输入：M1/M2-1/M2 Gate 约束、RAG 命中项、候选方来源。
   - 输出：候选方、source、source_row/source_id、candidate_only。
   - 闭合条件：RAG 全失配时不得自由组方；病案库、药理库、OpenEvidence 只能作为候选或校验来源。

5. Modification evidence trace
   - 输入：基础候选方、M2-3 加减候选、药理/病理证据。
   - 输出：候选药、target_symptom、target_disease、evidence_sources、candidate_only。
   - 闭合条件：缺少 evidence_sources、target_symptom 或 target_disease 的候选不得进入正式处方。

6. M3 audit trace
   - 输入：候选方、加减候选、剂量、禁忌、儿童/妊娠/肝肾异常等风险。
   - 输出：审核状态、阻断理由、剂量/配伍/禁忌审查记录。
   - 闭合条件：M3 未通过时不得生成或展示正式处方。

7. formal_prescription_allowed trace
   - 输入：完整 trace closure、M3 审核通过、无阻断项。
   - 输出：formal_prescription_allowed。
   - 闭合条件：该字段只能由最终安全门设置为 true；任何 WebSocket 包装层、M2-2、M2-3、RAG、病案库或药理库均不得私自置 true。

## Current Branch Status

- 当前审计未发现统一的 `run_full_pipeline` / `pipeline_orchestrator.py` 文件。
- WebSocket 侧当前只应返回候选/阻断状态，不能视为完整正式处方链路。
- M2-2 prompt 仍存在正式处方语义，需要在后续修复为 candidate-only schema。
- M2-3 已有 `evidence_sources` 缺失拒绝测试覆盖，但 `target_disease` 与 `target_symptom` 仍有开放风险。
- 旧版 `m2_formula_generator.generate_formula()` 仍可直接调用，属于 legacy 绕行风险。

## Required Failure Behavior

当以下任一条件出现时，`formal_prescription_allowed` 必须为 `false`：

- M1 诊断 trace 缺失或病名-症状明显不匹配。
- Validation Gate 未通过或安全信号未处理。
- RAG 无 precise/可信命中且模型试图自由组方。
- M2-2 或 M2-3 输出 `final_formula`、`prescription_text`、`direct_prescription` 等 final 语义字段。
- 加减候选缺 `evidence_sources`、`target_symptom` 或 `target_disease`。
- M3 未执行、未通过或返回阻断。
- 儿童、妊娠、肝肾异常、低氧发绀、黑便冷汗等风险未进入药学/安全审核。

