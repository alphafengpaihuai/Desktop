# M2 中医辨证方药生成 Skill · Code Synced Review

> **说明**：本文档以当前 M2 代码（m2_engine.py + services/m2_symptom_factor_loader.py）真实实现为准，同步补入代码新功能并标注来源。**不写未实现功能**。废弃的旧 prompt 内容不再保留。供你人工审核修改后再做代码适配。

---

## 1. 当前执行链路

```
M1 → primary_disease
   │
   ├── 1. resolve_m2_disease_key           → disease_key
   ├── 2. _load_syndromes                  → 当前病名证型池（空则 NO_CANDIDATE）
   ├── 3. SymptomFactorLoader              → 症状→证素→互斥证据
   ├── 4. 病理阶段判断: _classify_disease_type + _run_pathology_track
   ├── 5. _merge_dual_tracks               → 双轨校对提示（非计分）
   ├── 6. 寒热冲突病理裁决                  → main_direction
   ├── 7. 因果贝叶斯决策树 (§6.4):
   │      _build_causal_chain → _estimate_stage_prior → _update_likelihood_direction
   │      → _calculate_causal_coverage → _run_counterfactual_check
   │      → posterior_band 排序 + selected_syndrome_node
   │      (code: _bayesian_syndrome_adjudicator 及相关 helpers)
   ├── 8. _run_formula_intervention_check  → formula_intervention_check
   ├── 9. LLM 因果复核 (§6.5; M2_DISABLE_LLM=1 跳过)
   ├── 10. M2-1 输出: run_m2_1_syndrome_reasoning
   ├── 11. M2-2 方剂: run_m2_2_formula_candidates (查表失败→_fallback_parse)
   └── 12. M2-3 加减: run_m2_3_modification_candidates
```

**调用方**：

- `force_link_modules.py` → 直接实例化 `M2SyndromeSelector`，调用三个 `run_m2_`* 方法
- `bridge_server.py` → 通过 `unified_m2_pipeline` 调用，读取 `m2_result` 的 `syndrome_trace`/`disease_key`/`selected_syndrome_key`
- `full_pipeline.py` → 完整串联 M1→M2→M3

---

## 2. 输入与 disease_key 解析

### 2.1 输入

```python
primary_disease: str     # 来自 M1 的主病名
symptoms: List[str]      # 患者症状列表
tongue: str              # 舌象
pulse: str               # 脉象
labs: List[str]          # 实验室
imaging: List[str]       # 影像
```

(code: m2_engine.py::run_m2_1_syndrome_reasoning)

### 2.2 Disease Key 解析（代码强制执行+llm 推理校正）

6 级 fallback 优先级（code: m2_engine.py::resolve_m2_disease_key, ~L198）：


| 优先级 | 规则                               | 示例                              |
| --- | -------------------------------- | ------------------------------- |
| 1   | 精确匹配 KB 中 disease key            | 直接命中 key                        |
| 2   | `_DISEASE_NAME_MAP`（代码硬编码 ~69 条） | `"社区获得性肺炎"` → `"肺炎"`            |
| 3   | `_M2_FALLBACK`（代码硬编码 ~24 条）      | `"咳嗽"` → `"支原体肺炎"或“支气管炎”`       |
| 4   | KB 所有 key 做子串匹配                  | `"急性胃肠炎"` → 命中某 key             |
| 5   | 去末尾英文括号再匹配                       | `"痢疾 (Dysentery)"` 去括号 → `"痢疾"` |
| 6   | 兜底：返回原始输入名                       | 找不到返回原值 → 调用方处理 NO_CANDIDATE    |


---

## 3. 证型池与证型节点

### 3.1 加载

```python
def _load_syndromes(self, primary_disease: str) -> Dict:
    key = self.resolve_m2_disease_key(primary_disease)
    data = self.kb.get(key, {})
    return data.get("syndromes", {})
```

(code: m2_engine.py::_load_syndromes, ~L2036)

**约束**：只返回当前 disease_key 下的 syndromes，若 key 不存在 → 空 dict → `run_m2_1()` 输出 `NO_CANDIDATE`。

### 3.2 证型节点结构（KB 定义）

```python
{
    "trigger": "发热，咳嗽，痰黄，口干",
    "symptom": "",
    "tongue": "舌红苔黄",       # 典型舌象
    "pulse": "脉数",           # 典型脉象
    "formula_name": "桑菊饮",  # 绑定方剂
    "herbs": ["桑叶", "菊花", "杏仁", ...],
    "full_decoction": "桑叶7.5g 菊花3g ...",
    "modification_rules": [{"condition": "热甚", "action": "+黄芩 银花"}],
    "clinical_modifications": {
        "symptom_add": [...], "symptom_sub": [...],
    },
    "source_type": "伤寒论|温病条辨|经验方|...",
    "pathology": "热、痰、肺",
    "case_index": [...],
}
```

---

## 4. 症状证素库

### 4.1 加载类

`SymptomFactorLoader`（code: services/m2_symptom_factor_loader.py）

三个配置文件：


| 文件                                                      | 条目数                         | 用途      |
| ------------------------------------------------------- | --------------------------- | ------- |
| `data/m2_knowledge/m2_symptom_factor_map.json`          | 3,476                       | 症状→证素映射 |
| `data/m2_knowledge/m2_factor_contradiction_map.json`    | 10 hard + 4 soft + 18 mixed | 证素互斥矩阵  |
| `data/m2_knowledge/m2_symptom_contradiction_rules.json` | 4 composite + 12 single     | 复合症状规则  |


### 4.2 match_symptoms_to_factors()

```
输入: symptoms[], tongue, pulse
  → 精确匹配 → 子串匹配 → single_symptom_rules → strong_support_rules
输出:
  matched_symptoms[], tcm_factor_evidence{},
  negative_evidence[], unmatched_symptoms[],
  contradiction_result{}       # 占位符（空列表），由 evaluate 填充
```

(code: services/m2_symptom_factor_loader.py::match_symptoms_to_factors, L127)

**注意**：`contradiction_result` 在 match 阶段是占位 dict，所有列表为空。真正的互斥检测在下一步。

### 4.3 否定症状检测（代码强制执行+llm 推理兜底确认修正）

7 个否定前缀：`["无明显", "无明确", "无", "未", "没有", "否认", "不伴"]`

最长前缀优先。匹配后不作为阳性证素证据，记入 `negative_evidence`。

(code: loader::_is_negated, L118)

---

## 5. 互斥/反证规则

### 5.1 evaluate_factor_contradictions()

(code: loader::evaluate_factor_contradictions, L310)

```
输入: tcm_factor_evidence{}

1. hard_conflicts → strong_against[], need_human_review=True
2. soft_conflicts → soft_conflict[]
3. allow_mixed    → allow_mixed[]
4. strong_support_rules → strong_support[] + penalty_to_strong_against[]

输出:
  strong_support[], strong_against[]（异质：含硬冲突 dict + 惩罚 dict）,
  soft_conflict[], allow_mixed[],
  need_human_review: bool
```

### 5.2 注入 patient_info

```python
patient_info["symptom_factor_evidence"] = {
    "matched_symptoms": [...], "tcm_factor_evidence": {...},
    "negative_evidence": [...], "unmatched_symptoms": [...],
}
patient_info["factor_contradiction_result"] = {
    "strong_support": [...], "strong_against": [...],
    "soft_conflict": [...], "allow_mixed": [...],
    "need_human_review": False,
}
```

(code: m2_engine.py::run_m2_1_syndrome_reasoning)

**约束**：症状证素库不直接决定证型选择，仅做证据转换。

---

## 6. 双轨辨证

### 6.1 Track 1：_syndrome_scorer() —— Legacy 证据收集器（不参与最终选型）

(code: m2_engine.py::_syndrome_scorer, L2096 以降级 legacy 保留)

旧版积分式评分器，保留 `candidate_scores[]`/`total_score`/`confidence` 确保旧测试向后兼容。
**当前主链路已不使用其 `total_score` 决定 `selected_syndrome_node`**；该函数仅作为 legacy evidence
collector 传递证据强度提示给 §6.4 决策树，**严禁以总分最高或候选首位作为最终证型裁决依据**。

### 6.2 Track 2: _run_pathology_track()

(code: m2_engine.py::_classify_disease_type, _run_pathology_track)

**疾病性质分类**（关键词列表硬编码+llm 推理兜底）：

- 急性关键词 → `"外感/急性感染"` + `"卫气营血辨证"`
- 慢性关键词 → `"内伤/慢病"` + `"脏腑辨证"`
- 未命中 → `"性质不清"` + 兼看

**病理阶段推断**：

- 实验室/影像提示 → `"急性炎症期"/"免疫反应期"/"器质性改变期"`
- 外感：发热+恶寒→表证期，发热+黄痰→里热期
- 内伤：乏力+气短→正气不足期，刺痛+固定→瘀血内阻期

**病理关键词**：8 组硬编码（风/寒/热/湿/痰/瘀/虚/气滞），为 §6.4 causal_chain 的 `predicted_manifestations` 提供病机语义线索。

### 6.3 _merge_dual_tracks()

(code: m2_engine.py::_merge_dual_tracks, L1358)


| 状态           | 条件            | 行为                                                               |
| ------------ | ------------- | ---------------------------------------------------------------- |
| CONSISTENT   | 同一证型或包含关系     | 作为 §6.4 决策树的**双轨一致先验提示**：显著提高 commonness 估计                      |
| NEAR         | Jaccard > 0.3 | 作为 §6.4 决策树的**双轨相近先验提示**：轻度提高 commonness                         |
| CONFLICT     | Jaccard ≤ 0.3 | **不纳入 commonness 提升**；双轨冲突本身作为 **counterfactual 输入**，降低两轨候选的先验倾向 |
| SINGLE_TRACK | 任一轨无数据        | 以单轨结果为 §6.4 输入，无交叉校对影响                                           |


> 旧版 `_merge_dual_tracks` 输出的 `confidence` 字段仅供旧测试向后兼容；双轨状态仅作为 §6.4 决策树的先验/反事实输入信号，不是计分裁决逻辑。

### 6.4 因果解释型贝叶斯辨证裁决（Causal-explanation Bayesian differentiation）

(code: m2_engine.py::_bayesian_syndrome_adjudicator, _estimate_stage_prior,
_build_causal_chain, _calculate_causal_coverage, _run_counterfactual_check,
_update_likelihood_from_evidence, _rank_syndrome_posteriors)

证型选择依据：**某证型的病机（pathogenesis）能否在因果上解释当前
西医病名 / 病程阶段 / 症状 / 舌脉 / 实验室 / 反证**。`selected_syndrome_node` 必须由本裁决器输出。

流程：

```
当前 disease_key 证型池
  → 构建病机因果链 (_build_causal_chain)
  → 估计阶段先验 (_estimate_stage_prior)
  → 计算因果覆盖度 (_calculate_causal_coverage)
  → 更新似然 (_update_likelihood_from_evidence)
  → 头部候选反事实检验 (_run_counterfactual_check)
  → 后验融合产生 posterior_band (_rank_syndrome_posteriors): 推理链（非数字分值）
  → 双轨交叉校对 (§6.3) → 寒热裁决 (§7) → LLM 复核 (§6.5) → selected_syndrome_node
```

#### 6.4.1 病机因果链 `causal_chain`（每个候选证型一条）

回答"**这条病机为什么会生成这些症状**"，而不是罗列症状命中：

```python
{
  "syndrome_name": "",          # 候选证型名
  "pathology_stage": "",        # 西医病程/病理阶段（来自病理轨）
  "tcm_pathogenesis": "",       # 中医病机（如：痰热壅肺 / 风寒束肺 / 中阳虚衰）
  "mechanism": "",              # 病机如何在因果上生成表现的解释链
  "predicted_manifestations": [],  # 该病机应当生成的表现（病机→预测症状/舌脉/实验室）
  "observed_support": [],          # 预测表现中被当前病情证实的项
  "unexplained_findings": [],      # 当前病情中该病机无法解释的关键项
  "contradicted_findings": []      # 与该病机相矛盾的项（强反证）
}
```

**规则**：

- **无 causal_chain 的候选不得获得 HIGH 置信**（causal_chain 缺失 → 至多 MEDIUM/LOW）。
- causal_chain 必须解释"病机→症状的生成关系"，**不得**退化为症状命中清单。

#### 6.4.2 先验 `stage_prior`（**禁止仅用 1/N**）

`_estimate_stage_prior` 的先验必须综合：disease_key、当前病理阶段、证型节点的适用阶段、
该证型在此病名下的常见度（commonness）、节点来源可靠度（source reliability）、
以及该候选**是否与当前病程/双轨方向匹配**。
**均匀先验（1/N）仅作 fallback，不得作为主策略。**

#### 6.4.3 似然方向（likelihood_direction，**非 scorer 总分**）

`likelihood_direction` 用于描述"该证型在当前病情下的证据支持方向"——**不使用数字分值**，
仅使用定性方向：

- `STRONG_UP`：因果链充分解释主症/关键病理/舌脉/实验室，无关键未解释项
- `UP`：因果链解释大部分表现，有少量未解释但无关紧要
- `NEUTRAL`：因果链可解释，但不是最优匹配
- `DOWN`：因果链只能解释少数表现，存在部分未解释
- `STRONG_DOWN`：因果链基本不成立，存在关键未解释/强反证

同时输出：

- `causal_coverage = explained_key_findings / total_key_findings`（仅作辅助参考，不单独决定选型）
- `evidence_for`（该因果链支持的阳性项清单）
- `evidence_against`（该因果链无法解释或矛盾的项清单）

#### 6.4.4 反事实检验 `counterfactual_check`（头部候选）

```python
{
  "if_syndrome_true_expected": [],     # 若该证成立，应出现的表现
  "if_syndrome_true_unexpected": [],   # 若该证成立，不应出现的表现
  "missing_expected_findings": [],     # 应出现却缺失的项
  "violated_expectations": [],         # 出现了不应出现的项（违例）
  "counterfactual_result": "PASS|QUESTION|FAIL"
}
```

**规则**：

- 存在关键违例（violated_expectations 命中强反证）→ **不得 HIGH**；
- `counterfactual_result == FAIL` → **不得作为 selected_syndrome_node**（被裁决器排除）；
- `counterfactual_result == QUESTION` → 仅可 MEDIUM/LOW。

#### 6.4.5 证据分层（evidence stratification）

- **causal_evidence**（因果/病机证据）：基于病名的病程阶段、炎症证据、痰质、舌苔、实验室；
- **manifestation_evidence**（表现证据）：主症 病名相关/兼症 病名不相关；
- **weak_evidence**（弱证据）：乏力、倦怠、全身性非特异症状或生理表现。

**规则**：

- **weak_evidence 单独不得决定证型**；
- **causal_evidence 权重高于普通表现证据**；
- **关键少数证据**（key minority evidence）可改变判断路径，不被多数普通症状覆盖。

#### 6.4.6 硬约束

- 只在当前 disease_key 的证型池内计算先验/似然/后验/因果链，**不跨病名**。
- **否定症状（negation）不得计为阳性证据**（沿用 loader 的 negative_evidence 与 scorer 的否定前缀检测）。
- **强反证（counter-evidence / 寒热硬互斥 / 反事实违例）可降权或阻断（block）某候选**。
- **单一低置信候选不得被强制选中**（single low-confidence → 标记 LOW / need_human_review，不伪装高置信）。
- 寒热冲突未裁清（mixed/unknown）时，后验最高候选的 `posterior_band` 不得为 HIGH。
- **旧版 `_syndrome_scorer` 的 `total_score` 仅为 legacy 向后兼容字段，不得参与选型**。

#### 6.4.7 每个候选证型至少输出（**仅使用 band，无数字分值**）

```python
{
    "syndrome_key": "...",
    "syndrome_name": "...",
    "prior_band": "HIGH | MEDIUM | LOW",       # 非均匀阶段先验 band
    "prior_reason": "病名常见度+病程阶段+节点适用阶段+来源可靠度+病程匹配",
    "likelihood_direction": "STRONG_UP | UP | NEUTRAL | DOWN | STRONG_DOWN",
    "likelihood_evidence": "解释力来源（关键阳性项/主症/舌脉/实验室/证素/未解释/反证）",
    "causal_coverage": 0.0,                    # 仅作解释覆盖参考（explained/total），不参与分数裁决
    "causal_chain": {...},                     # §6.4.1
    "counterfactual_check": {...},             # §6.4.4（头部候选；非头部可为占位）
    "evidence_for": [...],                     # 支持本候选的证据
    "evidence_against": [...],                 # 反对本候选的证据/反证/否定
    "missing_info": "...",                     # 关键缺失信息
    "posterior_band": "HIGH | MEDIUM | LOW | BLOCKED",  # 融合所有因子后的后验 band
    "posterior_rank": 1,                       # 同 band 内顺位（1=首位）
    "blocked": false,                          # 是否被强反证/反事实 FAIL 阻断
}
```

裁决器输出 `adjudicated_syndrome_key`（= `selected_syndrome_node`）/ `adjudicated_confidence_band`
（即选中节点的 `posterior_band`）/ `need_human_review`，写入 `bayesian_differentiation` trace。
`causal_chain` 与 `counterfactual_check` 同时写入候选与 `internal_trace.causal_differentiation`（离线亦产出）。

#### 6.4.8 方剂干预验证 `formula_intervention_check`

选中证型后，验证其绑定方剂（bound_formula）是否干预该证型 causal_chain 的关键病机。
回答"**这个方剂是否能从病机角度治疗选中的证型**"——而非仅检查方名匹配。

```python
{
    "formula_name": "",                         # 绑定方剂名
    "formula_intervention_target": [],          # 方剂的核心干预靶点（如清热/化痰/散寒）
    "matched_pathogenesis_targets": [],         # 方剂靶点命中 causal_chain 关键病机的项
    "unmatched_pathogenesis_targets": [],       # causal_chain 中本方未覆盖的病机
    "formula_causal_match": "PASS | QUESTION | FAIL"
}
```

**规则**：

- `PASS`：方剂核心干预与 causal_chain 关键病机一致。
- `QUESTION`：方剂部分覆盖病机，部分未覆盖（可标记 missing_info）。
- `FAIL`：方剂方向与 causal_chain 关键病机不一致或偏离 → 需人工复核。
- `M2_DISABLE_LLM=1` 时仅本地运行，不依赖 LLM。

### 6.5 受约束 LLM 因果复核

(code: m2_engine.py::_llm_sanity_check_syndrome_result, _build_llm_sanity_prompt,
_parse_llm_sanity_result；prompt 源文件: `prompts/m2/m2_bayesian_differentiation.md`)

本地贝叶斯（§6.4）得出后验排序后，运行**一次** LLM 复核：在限定病名/病程阶段/候选池/
causal_chain/counterfactual_check 范围内做因果偏差校验并产出鉴别叙述。
**最终选型仍受本地硬约束裁决**。

#### 6.5.0 LLM 因果复核必须检查

- 本地 selected_syndrome 的 **causal_chain 是否成立**；
- 同池内是否存在**更能完整解释整体病情**的证型；
- 是否存在**关键未解释症状**；
- 本地 **counterfactual_check 是否 FAIL**（若 FAIL，本地已不得选中）；
- 节点绑定方剂是否**自然由该证型推导**而来。
- **不得**跨 disease_key、自造证型/方剂、覆盖本地硬约束。

#### 6.5.1 LLM 输入

primary_disease、disease_key、病程/病理阶段、证型池（含 syndrome_name / trigger）、
每个候选的 evidence_for / evidence_against、后验排序（posterior_band / posterior_rank / blocked）、
traditional_tcm_result 候选、factor_contradiction_result、
selected_syndrome_node、节点绑定方剂（只读）。

#### 6.5.2 LLM 推理要求（贝叶斯式，非积分制）

LLM 必须按"贝叶斯式辨证鉴别"推理（详见 `prompts/m2/m2_bayesian_differentiation.md`）：

1. 确认候选证型池与各候选在该病名/病程阶段下的初始倾向（高/中/低 + 理由）；
2. 通读完整病情，不得只看核对表；
3. 基于核对表阳性项做**定性**贝叶斯更新（仅用 ↑↑ / ↑ / → / ↓ / ↓↓，**不得编造精确概率**）；
4. 区分阳性项的辨证价值：主病机 / 病程阶段 / 背景体质 / 兼夹 / 路径改变 / 需追问；
5. 识别**关键改道表现**（少数但显著改变判断路径的发现），不得被多数普通症状覆盖；
6. 做 2–4 组相邻证型鉴别（共同点 / 决定性区别 / 当前更支持谁 / 为什么不是另一个）；
7. 给出最终辨证结论与理由，并自我校对（是否按数量、是否忽略阶段/关键少数、是否越池等）。

**严禁**：按阳性项数量判断、积分制、编造精确概率、重新抽取证据原子、脱离完整原文、
忽略病程阶段、忽略关键少数、生成证型池外证型、输出处方剂量、替代医生最终判断。

#### 6.5.3 LLM 输出（双段：富文本叙述 + 末尾机读 JSON）

为兼顾"丰富鉴别"与"代码可确定性消费"，LLM 必须输出**两部分**：

- **(a) 富文本贝叶斯鉴别**（叙述/表格，对应授权 prompt 输出格式的 6 节：总体判断 /
候选贝叶斯式更新表 / 关键改道表现 / 相邻证型鉴别 / 最终辨证结论 / 结论理由）；
- **(b) 末尾机读 JSON 块**（**最后输出**，代码只解析此块；解析器容忍其前的任意叙述文本）：

```json
{
  "llm_review_result": "ACCEPT|QUESTION|REJECT",
  "preferred_syndrome_from_pool": "",
  "reason": "",
  "detected_bias": [],
  "missing_evidence": [],
  "must_not_select": [],
  "posterior_ranking": [
    {"syndrome": "", "possibility_change": "↑↑|↑|→|↓|↓↓", "confidence_band": "HIGH|MEDIUM|LOW"}
  ],
  "key_pivot_findings": [],
  "adjacent_differentiation": [
    {"pair": ["", ""], "key_difference": "", "current_supports": "", "why_not_other": ""}
  ]
}
```

字段语义：前 6 字段沿用旧契约（解析必读）；`preferred_syndrome_from_pool` 与
`posterior_ranking[].syndrome` **只能取自当前 disease_key 证型池**。

#### 6.5.4 LLM 约束（硬规则）

- 不得提出当前 disease_key 证型池**之外**的证型（含 posterior_ranking / preferred）；
- 不得自造方剂 / 药物 / 剂量 / 用法 / 疗程；
- 不得绕过 disease_key / 证型池 / 互斥矩阵 / 节点绑定方剂；
- 不得以"阳性项最多 / 积分最高"为主要理由；不得编造精确概率。

#### 6.5.5 复核结果处理（代码确定性消费）

代码用 `_parse_llm_sanity_result()` 读取**末尾 JSON 块**（容忍前置叙述/```json 围栏/多对花括号，
取最后一个含 `llm_review_result` 的合法对象），校验越池后按 `llm_review_result` 处理：

- `ACCEPT` → 保留本地结果（`applied_action=keep_local`）。
- `QUESTION` → 保留本地结果，但**降低 confidence_band 并标记 need_human_review**。
- `REJECT` → 仅可在当前证型池内、未被 §6.4 阻断、且通过本地硬约束的前提下改选
`preferred_syndrome_from_pool`；若无合法替代 → `REJECT_LOW_CONFIDENCE`（降为 LOW + 复核）。
- 越池的 `preferred_syndrome_from_pool` / `posterior_ranking` 项 → 记入 `detected_bias` 并忽略。

`posterior_ranking` / `key_pivot_findings` / `adjacent_differentiation` 作为 trace 写入
`llm_sanity_check`，不改变选型（仅 ACCEPT/QUESTION/REJECT 影响选型与置信度）。

### 6.6 离线模式

若 `M2_DISABLE_LLM=1`（或无 `DEEPSEEK_API_KEY`）：
**跳过 §6.5 的 LLM 因果复核**，但**仍在本地完整运行 causal_chain、counterfactual_check、formula_intervention_check**
（仅不发起 LLM 调用）。`_llm_sanity_check_syndrome_result` 稳定返回本地结果
（`llm_review_result="ACCEPT"`，`llm_called=False`）。
测试（`M2_DISABLE_LLM=1`）始终走此路径，无 DS_ROUTER 日志。

---

## 7. 寒热冲突病理裁决

### 7.1 触发条件

`_is_cold_heat_conflict(factor_contradiction_result)` → 检查 `strong_against` 含寒↔热或寒↔温硬互斥。

(code: m2_engine.py::_is_cold_heat_conflict)

### 7.2 炎症证据评估

`_has_inflammation_evidence(symptoms, tongue, pulse, labs, imaging)`（code: m2_engine.py L1485）

7 维度评估：发热/黄痰/咽痛/舌象/脉象/实验室/影像。各维度综合判定 `severity`：


| 严重度      | 依据                  |
| -------- | ------------------- |
| none     | 无炎症相关证据             |
| mild     | 少量间接证据              |
| moderate | 明确 2+ 个维度炎症表现       |
| severe   | 明确 3+ 个及以上维度+实验室/影像 |


> **该评估不是计分**，是综合病机判断：黄痰+舌红苔黄+发热 → 里热证据充分；
> 仅乏力低热 → 炎症线索弱。用于 §7.3 裁决寒热方向：moderate/severe 支持 heat 主方向，
> none/mild 则支持"非热"或"寒"。

### 7.3 4 条裁决规则

(code: m2_engine.py::resolve_cold_heat_conflict_by_pathology, L1577)


| 规则  | 条件                 | 裁决方向        | need_human_review |
| --- | ------------------ | ----------- | ----------------- |
| R1  | 初期/表证期 + 无炎症 + 寒象  | cold        | false             |
| R2  | 炎症 moderate/severe | heat（寒象为兼夹） | false             |
| R3  | 恢复期/久病/术后/肿瘤       | 按病理阶段       | false             |
| R4a | 轻度炎症 + 纯寒象         | cold（低置信度）  | false             |
| R4b | 轻度炎症 + 寒热并存        | mixed       | true              |
| R4c | 无炎症 + 无寒热证据        | unknown     | false             |
| 兜底  | 其他                 | mixed       | true              |


### 7.4 集成

- CONSISTENT/NEAR：裁决方向与选中证型一致 → 清 need_human_review；不一致 → 保留
- CONFLICT：先裁出 main_direction，再按方向筛选证型池
（heat → 含「热/火/温/痰热/湿热」；cold → 含「寒/风寒/凉」；mixed → 全池），
在过滤池上 **RE-ENTER 因果贝叶斯裁决器**（`_bayesian_syndrome_adjudicator`，§6.4）
选出 `adjudicated_syndrome_key`，直接复用为本例最终 `bayesian_differentiation`。
  - `posterior_band` 由裁决器输出，**不硬编码**。
  - 过滤池为空 / 裁决器无合格候选 → 返回 NO_CANDIDATE 或单轨低置信兜底。
  - 离线 `M2_DISABLE_LLM=1` 下完全本地运行。

---

## 8. 证型节点选择

### 8.1 M2-1 主流程

(code: m2_engine.py::run_m2_1_syndrome_reasoning, L470)

```
1. resolve disease_key → _load_syndromes → 空则 NO_CANDIDATE
2. SymptomFactorLoader → factor_evidence
3. 病理阶段判断: _classify_disease_type + _run_pathology_track
4. _merge_dual_tracks → 双轨校对先验提示
5. 寒热冲突 → resolve_cold_heat_conflict_by_pathology → main_direction
6. 因果决策树 (§6.4 _bayesian_syndrome_adjudicator) → posterior_band + selected_syndrome_node
7. _run_formula_intervention_check → formula_intervention_check
8. LLM 因果复核 (§6.5; M2_DISABLE_LLM=1 跳过)
9. 组装 M2-1 输出
```

### 8.2 选中约束

- 必须在当前 disease_key 的证型池内（先验/似然/后验/因果链/LLM 均不得越池）
- `selected_syndrome_node` 由因果贝叶斯决策树（§6.4）输出；双轨合并仅作先验/交叉校对输入
- `posterior_band` 融合推理链得出，不使用数字分值
- 寒热冲突未裁清 → `posterior_band` 不得 HIGH
- 强反证 / 反事实 FAIL 可阻断候选；counterfactual FAIL 不得选中且 band 不得 > LOW
- 无 causal_chain 不得 HIGH；单一低置信不得强制选中；否定症状不计阳性
- LLM 仅 ACCEPT/QUESTION/REJECT，不自由生成
- `_fallback_parse` 仅用于 M2-2 查表兜底（非 M2-1 選択）

---

## 9. 节点方剂读取

### 9.1 M2-2 流程

(code: m2_engine.py::run_m2_2_formula_candidates, L703)

```
1. resolve disease_key + _load_syndromes
2. 定位证型 key: name==key → name in trigger → _fallback_parse
3. 从选中证型取 formula_name + herbs
4. 为空 → NO_CANDIDATE（"缺少绑定方剂"）
```

### 9.2 _fallback_parse（legacy M2-2 查表兜底，不参与 M2-1 证型裁决）

(code: m2_engine.py::_fallback_parse, L2065)

- 不调 LLM
- 构造最小 patient_info，调 `_syndrome_scorer`（legacy evidence collector）
- 基于 scorer 输出的 `matched_symptoms`/`matched_tongue_pulse` 等字段确定候选
- 若 scorer 输出不可靠（无匹配或唯一候选覆盖不足），返回 None 或 `need_human_review=True`
- **本条路径仅用于 M2-2 查表失败时的恢复，不参与 M2-1 证型裁决**

### 9.3 M2-2 输出约束

- `candidate_only: True`, `must_enter_m3: True`
- 当前始终单候选

---

## 10. 节点内加减

### 10.1 M2-3 流程

(code: m2_engine.py::run_m2_3_modification_candidates, L786)

3 级策略：


| 优先级 | 数据源                                                   |
| --- | ----------------------------------------------------- |
| 1   | KB 中 clinical_modifications                           |
| 2   | data/m2_case_reference.json + data/m2_case_cache.json |
| 3   | data/m3_herb_knowledge.json                           |


去重：base herbs 已有的不重复加。最多 6 条。

### 10.2 M2-3 输出约束

- `candidate_only: True`, `must_enter_m3: True`
- `modification_candidates` 最多 6 条

---

## 11. 病案/方论/药物标签库边界


| 文件                            | 用途                 |
| ----------------------------- | ------------------ |
| `data/m2_case_reference.json` | 名医病案参考库（M2-3 策略 2） |
| `data/m2_case_cache.json`     | LLM 查询结果缓存         |
| `data/m2_case_used_log.json`  | 已推荐病案日志            |
| `data/m3_herb_knowledge.json` | 药物→症状映射（M2-3 策略 3） |


(code: m2_engine.py::_search_cases, run_m2_3_modification_candidates)

---

## 12. NO_CANDIDATE 与 need_human_review

两个独立标记：


| 标记                    | 触发条件                                   | 含义          |
| --------------------- | -------------------------------------- | ----------- |
| `need_human_review`   | 证素硬互斥（寒热/寒温等），寒热冲突裁决为 mixed/unknown    | 证型/方向有冲突需复核 |
| `needs_manual_review` | 高风险疾病名（癌/恶性/梗死/出血/栓塞/HIGH_RISK 列表）     | 疾病本身高风险     |
| `NO_CANDIDATE`        | 证型池空 / 缺少绑定方剂 / 证型在 KB 找不到 / 寒热冲突无合适候选 | 无法生成候选      |


---

## 13. M2-1 / M2-2 / M2-3 输出

（各阶段具体 schema 字段同 M2_freeze_candidate_skill.md §12，此处略）

---

## 14. M3 移交与正式处方禁止

```python
"candidate_only": True,           # 始终
"must_enter_m3": True,            # 始终
"formal_prescription_allowed": False, # 始终
```

禁止的处方字段（`_strip_forbidden_prescription_fields` 递归过滤）：

```
prescription_text, final_formula, complete_formula, final_prescription,
prescription, full_formula, dosage, dose, 用法, 疗程
```

(code: m2_engine.py 多处)

---

## 15. Legacy guard 与 LLM 离线隔离

### 15.1 Legacy guard

`full_pipeline()` 受 `ALLOW_LEGACY_M2_FULL_PIPELINE` 环境变量保护：

```
未设置 或 非 "true" → 返回 {status: "BLOCKED", legacy_prescription_path_blocked: True}
```

(code: m2_engine.py::full_pipeline, L2681)

### 15.2 LLM 离线隔离

```python
def _llm_available(self) -> bool:
    if os.environ.get("M2_DISABLE_LLM", "").lower() in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("DEEPSEEK_API_KEY"))
```

(code: m2_engine.py::_llm_available, _call_llm)

**测试时**`M2_DISABLE_LLM=1` 保证所有 M2 测试离线运行（§6.6），无真实 DeepSeek/router 调用。

---

## 16. 当前测试覆盖


| 文件                                         | 测试数     | 覆盖范围                                     |
| ------------------------------------------ | ------- | ---------------------------------------- |
| test_m2_spec_boundaries.py                 | 29      | 输出 schema 合规、红线字段、_fallback_parse        |
| test_m2_1_syndrome_scoring.py              | 20      | 八纲、痰热/风热权重、否定症状（legacy scorer）           |
| test_m2_production_display_trace_filter.py | 16      | trace 过滤                                 |
| test_m2_prompt_v2_alignment.py             | 13      | prompt 对齐                                |
| test_m2_symptom_factor_loader.py           | 13      | 证素映射、否定、互斥                               |
| test_m2_3_modification_knowledge_query.py  | 10      | 加减候选                                     |
| test_m2_cold_heat_resolver.py              | 9       | 寒热冲突 4 规则                                |
| test_m2_knowledge_conversion.py            | 9       | KB 结构完整性                                 |
| test_m2_formula_knowledge_integrity.py     | 6       | 方剂完整性                                    |
| test_m2_split_entrypoints.py               | 4       | 拆分 entrypoint                            |
| test_m2_pathology_syndrome_bridge.py       | 0       | 无测试方法                                    |
| test_m2_causal_bayesian_differentiation.py | 8       | 因果链/反事实/非均匀先验/否定/裁决器拥有选型（§6.4 升级）        |
| **合计**                                     | **137** | **验证：137/137 OK · 3/3 通过 · DS_ROUTER 0** |


---

## 17. 当前 KB gap

**文件**：`data/m2_knowledge_staging/m2_kb_patch_candidates.json`

13 个候选：


| ID        | Target Key     | 缺失证型               | 状态             |
| --------- | -------------- | ------------------ | -------------- |
| GAP-01~09 | 痢疾 (Dysentery) | 虚寒痢等 9 个           | 已从 `痢疾` key 合并 |
| GAP-10~12 | 肺炎 (Pneumonia) | 邪犯肺卫（风热）/痰热壅肺/正虚邪恋 | 仅在 `肺炎` key 存在 |
| GAP-13    | 肺炎 (Pneumonia) | 肺脾气虚（MEDIUM）       | 仅儿童肺炎，成人底方待确认  |


**临床影响**：`DISEASE_NAME_MAP` 优先命中 `肺炎` key（无括号），临床不受影响。

---

## 18. 人工审核清单

- §1 执行链路是否准确反映真实调用顺序？
- §2 disease_key 解析 6 级规则是否完整？
- §3 证型节点结构是否与 KB 定义一致？
- §4~5 症状证素库与互斥规则描述是否准确？
- §6 双轨辨证是否准确（legacy scorer 仅作 evidence collector，主决策为 §6.4）？
- §7 寒热冲突 4 条规则和集成逻辑是否正确？
- §8~9 证型选择 + _fallback_parse 是否准确？
- §10 M2-3 三级策略和去重是否正确？
- §11 病案/药物库边界是否完整？
- §12 NO_CANDIDATE / need_human_review / needs_manual_review 区分是否正确？
- §13 三个输出 schema 是否与代码一致？
- §14 M3 handoff 和 formal_prescription_allowed 约束是否完整？
- §15 Legacy guard + LLM 隔离是否准确？
- §16 测试清单是否与当前测试结论一致？
- §17 KB gap 状态是否准确？

