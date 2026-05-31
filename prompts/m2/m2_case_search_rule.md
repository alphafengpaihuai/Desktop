# M2 病案检索与加减控制规则

## 触发条件

M2 完成辨证选方后，自动触发病案检索：

1. **必选**：基于辨出的 `disease_name + syndrome_name` 检索病案参考库
2. **可选**：当内置病案库无匹配时，LLM 查询循证医学网站获取病案参考

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
  |     |
  |     +-- 若库中无匹配：
  |           +-- _fetch_reference_case_from_llm()
  |                 +-- 查循证医学网站（默沙东、PubMed、CNKI等）
  |                 +-- 结果缓存到 case_cache.json
  |                 +-- 记录到 case_used_log.json
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
4. 无病案参考或 LLM 不可用时，modifications 为空数组
5. 禁止模型自由创造加减药物

## 后续所有修改遵循的规则

1. **先按病名匹配，再按证型匹配**：检索病案时，病名权重最高
2. **选择 1-3 个病案**：用于参考辨证和用药方案
3. **药物加减控制在 3 味左右**：不能超过 3 味
4. **循证来源优先**：LLM 查询时只能查默沙东、PubMed、CNKI 等
5. **缓存优先**：已查过的病案不再重复查询
