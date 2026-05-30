# M1 诊断标准补齐规则（含 Agent 工具2：在线查询）

## 两种补齐机制

### 机制A：按需补齐（`_fill_missing_criteria`）
当 M1 诊断完成后，若目标疾病缺少诊断标准，自动调用 LLM 查询并写回本地库。

### 机制B：Agent 工具2（`_online_query`）
在 Agent 推理过程中主动调用：
- 工具1（本地查询）返回 `NOT_FOUND_IN_LOCAL_DB` 时
- 本地标准与患者表现存在明显差异时
- 怀疑标准过时时

## 触发条件（机制A）

当 **M1 诊断引擎** 完成一次 LLM 诊断后，若满足**全部**以下条件，自动触发补齐：

1. LLM 成功返回了诊断结果（至少 1 个疾病名，`source == "llm"`）
2. 该疾病在 `diseases_core.json` 中**缺少以下任一字段**：
   - `diagnostic_criteria`（诊断标准）
   - `typical_symptoms`（典型症状）
3. 该疾病**未被补齐过**（`criteria_filled_log.json` 中没有记录）

## 执行流程（机制A）

```
M1 diagnose() 完成 LLM 诊断
  │
  +-- 检查 criteria_fill_enabled (默认 True)
  │
  +-- 对每个诊断疾病名：
  │     +-- 去重检查（criteria_filled_set / criteria_filled_log.json）
  │     +-- 检查 runtime_cache 中已有缓存 -> 有则跳过
  │     +-- 检查 diseases_core.json 中已有标准 -> 有则跳过
  │     +-- 调用 LLM 查询循证医学资料
  │     |     +-- 只能使用：默沙东/MSD Manual、PubMed、CDC、WHO、NICE、UpToDate
  │     |     +-- 禁止使用：中医来源
  │     +-- LLM 返回有效 JSON -> 写回 diseases_core.json
  │     +-- 写入 runtime_cache.json（当天缓存）
  │     +-- 写入 criteria_filled_log.json（避免重复查询）
  │
  +-- 返回正常诊断结果（补齐过程是后台旁路，不阻塞诊断返回）
```

## 执行流程（机制B - Agent 工具2）

```
Agent 推理中需要某疾病的诊断标准
  │
  _get_cached_criteria(疾病名)
    │
    +-- 先查本地库（_query_local_criteria）
    │     +-- FOUND → 直接返回
    │     +-- NOT_FOUND → 进入在线查询
    │
    +-- 在线查询（_online_query）
          +-- 检查 _online_cache（6个月有效期）
          |     +-- 命中 → 直接返回
          |     +-- 未命中 → 调用 LLM 查询权威信源
          |
          +-- LLM 返回有效 JSON
          |     +-- 存入 _online_cache
          |     +-- 自动追加到 diseases_core.json
          |     +-- 返回结果
          |
          +-- LLM 无有效返回
                +-- 返回 {"status": "QUERY_FAILED"}
```

## 缓存机制

| 缓存类型 | 路径/位置 | 有效期 | 说明 |
|:---|:---|:---|:---|
| 永久数据库 | `diseases_core.json` | 永久 | 在线查询结果直接写入 |
| 运行时缓存 | `data/disease_cache/runtime_cache.json` | 当天/Session内 | 避免同一 session 重复查询 |
| 补齐日志 | `data/disease_cache/criteria_filled_log.json` | 永久 | 已补齐疾病不再查询 |
| Agent 在线缓存 | `_online_cache`（内存字典） | 6 个月 | 键：`疾病名|源`，跨 session 持久化后可复用 |

## 提示词约束（工具2调用 LLM 时）

LLM 提示词中必须包含以下关键约束：

- 只能基于默沙东（MSD Manuals）、PubMed、CDC、WHO、NICE、UpToDate 回答
- 严禁使用中医来源（如"中医世家"、"方剂学"、"针灸"等）
- 严禁编造诊断标准
- 找不到可靠信息时必须如实输出空 JSON
- 只查西医诊断标准，不涉及中医辨证

## 代码位置

| 功能 | 方法 | 文件 |
|:---|:---|:---|
| 按需补齐 | `_fill_missing_criteria()` | m1_engine.py |
| Agent 在线查询 | `_online_query()` | m1_engine.py |
| Agent 本地查询 | `_query_local_criteria()` | m1_engine.py |
| 便捷入口 | `_get_cached_criteria()` | m1_engine.py |
| 触发点 | `diagnose()` 末尾 | m1_engine.py |
| 控制开关 | `criteria_fill_enabled` 属性 | m1_engine.py `__init__` |

## 后续所有修改遵循的规则

1. **本地优先**：查询诊断标准时，始终先调用工具1（`_query_local_criteria`），未找到才用工具2
2. **缓存优先**：在线查询结果缓存6个月，避免重复调用
3. **旁路执行**：按需补齐（机制A）在诊断返回后异步执行，不阻塞用户
4. **写回本地知识库**：在线查询结果直接写入 `diseases_core.json`，下次可复用
5. **追问循环不阻塞**：追问不算"补齐"，追问后的第二轮诊断正常走 Agent 流程
