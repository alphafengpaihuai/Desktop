# M2 病案检索与加减控制规则

## 触发条件

M2 完成辨证选方后，自动触发病案检索：

1. **必选**：基于辨出的 `disease_name + syndrome_name` 检索病案参考库（本地知识库，code-first，不调用 LLM）

## 病案检索流程

```
M2 process() 完成辨证选方
  |
  +-- Step 6: 检索病案参考
  |     +-- _search_cases(disease, syndrome)
  |     |     +-- 精确匹配：disease|syndrome
  |     |     +-- 病名匹配：disease
  |     |     +-- 部分匹配：disease 子串
  |     |     +-- 最多 3 条
  |     |     +-- 库中无匹配时返回空（不再 LLM 查网，已与固定链路对齐）
  |
  +-- Step 7: 药物加减控制
  |     +-- _generate_modifications()
  |     +-- 从病案参考的 modifications 中提取
  |     +-- 去重、排除 base_herbs 中已有药物
  |     +-- 最多 3 味
  |
  +-- 输出含 case_references + modifications
```

## 数据文件

| 文件 | 路径 | 作用 |
|------|------|------|
| m2_case_reference.json | data/ | 内置病案参考库（从 full_decoction 结构化提取） |
| m2_case_cache.json | data/ | LLM 查询病案缓存 |
| m2_case_used_log.json | data/ | 已引用病案日志 |

## 输出格式变化

M2 新增两个输出字段：

```json
{
  "...": "...原有字段...",
  "case_references": [
    {
      "disease_name": "儿童急性扁桃体炎",
      "syndrome_name": "急性期-风热犯咽",
      "formula_name": "银翘马勃散",
      "herbs": ["金银花", "连翘", "马勃", ...],
      "dosages": ["金银花12g", "连翘12g", ...],
      "course": "起始连用5剂",
      "efficacy": "用药3-5天后发热明显减轻",
      "warning": "10岁以下儿童减半服用",
      "reference_type": "知识库内置",
      "source": "data/m2_formula_knowledge.json"
    }
  ],
  "modifications": [
    {"herb": "加味药名", "reason": "加减理由", "source": "参考来源"}
  ]
}
```

## 药物加减控制规则

1. 加减药物 **必须** 来自参考病案的 modifications 字段
2. 加减药物 **不能** 与 base_herbs 重复
3. **总计 <= 3 味**
4. 无病案参考时，modifications 为空数组
5. 禁止模型自由创造加减药物

## 后续所有修改遵循的规则

1. **先按病名匹配，再按证型匹配**：检索病案时，病名权重最高
2. **选择 1-3 个病案**：用于参考辨证和用药方案
3. **药物加减控制在 3 味左右**：不能超过 3 味
4. **本地知识库优先**：病案来源为 `data/m2_case_reference.json` / `data/m2_case_cache.json`（code-first，不调用 LLM）
