# M4 复诊路由分发 Agent 代码实现指令

## 总体原则
- M4 是复诊入口的路由分发器，负责判断复诊患者是否仍属于原西医诊断病名范畴。
- 80% 代码 + 20% 模型（仅原病名连续性校验需要 LLM）。
- 危险信号检测、症状对比、病程窗口分析、四象限分类、路由决策均为 **100% 代码执行**。
- 仅原病名连续性校验（`DiagnosisContinuityEvaluator`）需要使用 LLM 做语义比对。
- M4 **不负责追问**——追问统一由 M1 处理。M4 只标记不可用信息并列出询问理由。
- 信息足够 → 直接输出路由结果。信息不足 → 标记 PENDING，建议转 M1。

## Agent 工具集

| 工具 | 类名 | 执行方式 | 职责 |
|:---|:---|:---|:---|
| 工具1 | `DangerSignalDetector` | 100% 代码 | 检测危险信号（硬规则阈值+关键词） |
| 工具2 | `SymptomComparator` | 100% 代码 | 对比初诊与复诊症状，分类+计算改善系数 |
| 工具3 | `WindowChecker` | 100% 代码 | 判断病程窗口位置（M1卡片→类别→默认值） |
| 工具4 | `DiagnosisContinuityEvaluator` | 模型+代码兜底 | 逐条比对原病诊断标准，判断是否仍属原病名 |
| 工具5 | `QuadrantClassifier` | 100% 代码 | 四象限分类（仅 RETURN_TO_M2 时使用） |
| 证据追踪 | `AuditTrace` | 100% 代码 | 记录所有规则命中、中间结果 |

## 主入口流程（`FollowupRouter.route()`）

```
route() 调用
  |
  +-- Step 1：危险信号检测（工具1）
  |     +-- 触发 -> 直接返回 EMERGENCY_STOP（最高优先级）
  |     +-- 未触发 -> 继续
  |
  +-- Step 2：输入校验（_validate_input）
  |     +-- 校验通过 -> 继续
  |     +-- 校验失败 -> 返回 INSUFFICIENT_INFO（标记不可用项）
  |
  +-- Step 3：症状对比（工具2）
  |     +-- 返回改善系数 + 分类症状
  |
  +-- Step 4：病程窗口分析（工具3）
  |     +-- 返回窗口位置（起效/显效/超出）
  |
  +-- Step 5：原病名连续性校验（工具4，调 LLM）
  |     +-- LLM 返回有效 JSON -> 解析
  |     +-- LLM 失败 -> 代码兜底
  |
  +-- Step 6：路由决策（_make_routing_decision，代码硬规则）
  |     +-- RETURN_TO_M2 / RETURN_TO_M1 / EMERGENCY_STOP
  |
  +-- Step 7（仅 RETURN_TO_M2）：四象限分类（工具5）
  |     +-- 返回 Q1-Q4 + 建议动作
  |
  +-- Step 8：构建标准输出（_build_decision）
```

## 路由决策优先级（`_make_routing_decision`）

代码硬规则，不允许使用 LLM：

1. **危险信号触发** -> `EMERGENCY_STOP`
2. **存在新的主要矛盾**（`major_new_problem`）-> `RETURN_TO_M1`
3. **存在冲突证据**（`conflicting_evidence_found`）-> `RETURN_TO_M1`
4. **不再属于原病名**（`still_within_original_disease == false`）-> `RETURN_TO_M1`
5. **低置信度 + 有新发症状** -> `RETURN_TO_M1`
6. **以上皆否** -> `RETURN_TO_M2`

## 四象限分类规则（`QuadrantClassifier.classify`）

| 象限 | 条件 | 建议动作 |
|:---|:---|:---|
| Q1（效佳） | 改善系数 >= 7，无新发/加重 | 守方，微调剂量 |
| Q3（激惹） | 改善系数 >= 3，但出现新发/加重 | 调整，保留君臣，剔除激惹药 |
| Q2（待效） | 未超显效窗口（且不满足 Q1/Q3） | 守方等待 |
| Q4（无效） | 已超显效窗口（且不满足 Q1/Q3） | 更方，重新辨证 |

## 病程窗口规则（`WindowChecker.check`）

优先级（由高到低）：
1. **M1 诊断卡片**中的 `followup_window` 字段（最准确）
2. **疾病类别匹配**：从 `data/m4_followup_window_rules.json` 按关键词分类查找
3. **保守默认值**：起效 7 天 / 显效 14 天（当 `needs_review = true` 时建议医生复核）

## 输入校验规则（`_validate_input`）

以下任一缺失则返回错误信息（触发 INSUFFICIENT_INFO）：
- `initial_diagnosis` 为空 -> "缺少初诊西医病名"
- `initial_symptoms` 为空 -> "缺少初诊症状"
- `followup_symptoms` 为空 -> "缺少复诊病历"
- `days_since_initial` <= 0 -> "缺少或无效的距初诊天数"
- `m1_card` 为 None -> "缺少原病名诊断标准卡片，无法完成连续性校验"

## 缓存规则

M4 不涉及在线查询缓存。在线查询（工具2）的责任在 M1。M4 只使用：
- `data/m4_red_flags.json` — 危险信号阈值和关键词
- `data/m4_followup_window_rules.json` — 病种窗口规则
- M1 诊断卡片（`m1_card` 参数传入）

## 输入输出格式

见 `M4_SOURCE_PROMPT.md` 中的 JSON Schema。

## 禁止
- 进行中医辨证、选方、加减
- 生成追问问题（只标记不可用信息）
- 使用危险信号检测外的 LLM 判断
- 创造新的诊断病名
- 四象限分类使用 LLM（必须代码硬规则）
- 修改 M1/M2 的诊断结果
