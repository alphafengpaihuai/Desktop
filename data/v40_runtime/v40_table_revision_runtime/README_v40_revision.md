# v40 运行表格批量修订版

生成时间：2026-06-14T11:23:16

本版依据用户提供的 R1-R13 修订规则，对 v39 的 `m2_runtime_view.csv` 与 `syndrome_pathology_edges.json` 进行批量清洗和同步修复。

## 核心变化

- `症状/入选条件`：统一为全角分号 `；` 分隔，删除临时补足标记。
- `排除条件`：删除与入选条件重叠的条目；同病种共享的大型排除列表自动清空；单行保留最多 5 项。
- `方剂组成`：统一全角括号与分号；处理大枣/生姜/钱等非克重剂量；空组成标记为 `[待补充]`。
- `加减`：统一为 `+症状条件：药物剂量` 形式，并生成 `modification_rules`。
- `阶段/状态轴`：统一为 8 类枚举。
- `西医病名key`：去除 ZXNK/JZ/WB 等前缀，转为小写 snake_case；无法标准拼音的条目使用内置字符表+哈希兜底。
- `证型/证素/病理`：清理占位符，标准证型尽量补全证素与证机。
- `syndrome_pathology_edges`：由修订后的 runtime_view 反向生成有效边，避免 v39 空壳边导致 M2 失败。

## 输出文件

- `v40_m2_runtime_view_revised.csv`
- `v40_syndrome_pathology_edges_repaired.json`
- `v40_modification_rules_repaired.json`
- `v40_revision_audit.csv`
- `v40_empty_shell_disease_audit.csv`

## 仍需人工关注

- `[待标注]` 证型：保留为人工标注对象，不进入自动决策。
- `[待补充]` 方剂组成/证型证素：不阻塞索引，但 M2/M3 输出时应标记为 REVIEW。
- 本版是规则化清洗，不替代人工医学审核。
