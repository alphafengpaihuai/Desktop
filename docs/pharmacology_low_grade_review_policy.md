# 低等级药理线索人工复核标准

## 模块定位

低等级药理候选隔离层只用于人工复核机制线索，不属于正式药理库，不是处方证据，不得用于 M2 候选方生成、M2 加减药生成或处方推荐。

该隔离层不得接入 M2/M3/M4 自动链路，不得作为 `evidence_based_herbs` 来源，不得自动改变 `disease_pharmacology_cache_rebuilt.json`，也不得影响任何处方生成、加减药生成或安全审核结论。

## 证据等级定义

- `low`: 有可追溯 PMID，存在疾病、component/herb、target 或 outcome 的部分机制线索，但证据链弱，通常只命中单靶点、动物/细胞模型或上下文不完整。
- `low_medium`: 同一疾病下 component/herb、target、outcome 的链路更完整，或同一 PMID 中命中多个关键要素，但仍属于体外、动物、机制研究，不能作为临床疗效证据。
- `manual_review`: 存在可疑线索，但证据链尚未闭合，或来源、成分、药材映射、研究类型需要人工判断。
- `rejected`: 证据不可追溯、链路不成立、存在错误映射、方剂被错误拆分、或仅有网络药理/分子对接等不足证据。
- `upgrade_candidate`: 经人工复核后，认为可进入 evidence patch candidate 的候选。它仍不是正式药理库证据，也不得直接进入 M2 或处方推荐。

## 可升级条件

低等级线索若要升级为更高等级 evidence patch candidate，必须至少同时满足：

- 同一疾病；
- 同一 component 或 herb；
- 同一 PMID 或明确同一研究链路；
- component/herb + target + outcome 在同一证据链内；
- 不是单纯网络药理；
- 不是单纯分子对接；
- 癌症体外实验不得直接升级为 clinical high；
- 有清楚的 `why_upgrade` 字段；
- 有人工 reviewer 记录；
- 有可追溯的 PMID、title、evidence_context 和 review decision。

## 不可升级条件

以下情况必须继续保留为 `low` 或标记为 `rejected`：

- 不同 PMID 强行拼接 component、target、outcome；
- 只有疾病名命中；
- 只有成分名命中；
- 只有网络药理/分子对接；
- 网络药理/分子对接不得单独升级；
- 方剂名被拆成单味药；
- 癌症细胞实验被外推为临床疗效；
- 无明确靶点或转归；
- 无 PMID 或证据来源不可追溯；
- 成分来源药材不明确，或存在安全边界但未复核；
- evidence_context 与疾病、target、outcome 不在同一研究链路内。

## 人工复核字段

人工复核记录建议使用以下字段：

```json
{
  "reviewer": "",
  "review_date": "",
  "decision": "keep_low|upgrade_candidate|reject|needs_more_evidence",
  "why": "",
  "why_upgrade": "",
  "allowed_next_step": "stay_quarantine|evidence_patch_candidate|discard",
  "still_blocked_from": [
    "formal_pharmacology_db",
    "m2_formula_candidate",
    "m2_modification_candidate",
    "prescription_generation"
  ]
}
```

`still_blocked_from` 必须始终保留，直到经过单独的 evidence patch 审查、正式库审查和安全规则审查。人工复核为 `upgrade_candidate` 也不代表可以进入正式药理库。

## 升级后仍不得直接进入处方

即使某条低等级线索被人工判定为 `upgrade_candidate`，它也只能进入 evidence patch candidate。它不得直接进入正式药理库，不得进入 M2 候选方生成，不得进入 M2 加减药生成，不得进入 prescription_generation。

任何从 `upgrade_candidate` 到正式药理库的转移，都必须另行经过：

- evidence patch 审查；
- 疾病、component/herb、target、outcome 同链路核验；
- 安全边界审查；
- 人工 reviewer 签名；
- before/after 审计记录。

## 审计留痕要求

每一次人工复核必须保留：

- 原始 low grade candidate；
- PMID；
- title；
- evidence_context；
- review decision；
- reviewer；
- review date；
- why；
- before/after 状态；
- 是否仍 blocked_from formal_pharmacology_db、m2_formula_candidate、m2_modification_candidate、prescription_generation。

不得覆盖原始候选记录。所有复核记录必须追加保存，便于回溯谁在何时基于什么证据做出判断。
