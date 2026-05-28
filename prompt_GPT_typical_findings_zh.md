# 任务：给 typical_findings 追加中文同义词（仅此而已）

## 要求

对于 diseases_core.json 中每条 disease.axes[].typical_findings：

1. **逐条处理，不增不减不改**
2. 如果一条 finding **已经有中文**（含中文字符），**跳过，不改**
3. 如果一条 finding **只有英文**，在末尾追加 `（中文翻译）`
4. 中文翻译要求：**医学术语准确**，用临床常用的中文表述

## 格式示例

```
修改前："heavy menstrual bleeding (>80mL/cycle)"
修改后："heavy menstrual bleeding (>80mL/cycle)（月经量过多，>80mL/周期）"

修改前："osteophyte formation"
修改后："osteophyte formation（骨赘形成）"

修改前："limited range of motion"
修改后："limited range of motion（活动受限）"

修改前："vertebrobasilar insufficiency symptoms (dizziness, visual changes)"
修改后："vertebrobasilar insufficiency symptoms (dizziness, visual changes)（椎基底动脉供血不足表现：眩晕、视物模糊）"
```

## 重要原则

1. **不要新增 finding**（不增加条目数）
2. **不要删除 finding**（不减少条目数）
3. **不要修改已有的中文内容**（如果原来就有中文，保留原样）
4. **不要修改 finding 的英文部分**（只追加中文翻译）
5. **整个 JSON 的其他字段不动**

## 输出

直接输出完整的 JSON 文件（801 条），只修改了 typical_findings 中纯英文条目的中文追加。
