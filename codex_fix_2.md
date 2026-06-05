# 守一CDSS M1→M2 链路断层修复任务 (第二轮)

## 本轮目标：修复热象重写覆盖不全 + CHIEF_COMPLAINT_FORCE 逻辑缺陷

修复前测试暴露的问题：
- 张祎宸（3岁男，发热39°C+手心热+舌点刺+扁桃体肥大）：CHIEF_COMPLAINT_FORCE 将诊断从"上气道咳嗽综合征"强制覆盖为"变应性鼻炎"，M2 返回"肺气虚寒/温肺止流丹"（温补方），热象重写因"肺气虚寒"不在冷证关键词列表中而未触发
- 关之仙（72岁女，痰黄绿+苔黄腻）：完全正确

---

## 需修改的文件

### 文件1：bridge_server.py

#### 修改点1：增强 CHIEF_COMPLAINT_FORCE 逻辑（行775-783）

**问题：** 当患者同时有慢性病史（如"过敏性鼻炎"）和急性发热症状时，CHIEF_COMPLAINT_FORCE 会用慢性病名（"变应性鼻炎"）强制覆盖掉 M1 输出的急性病诊断（"上气道咳嗽综合征"），而 M2 知识库中"变应性鼻炎"只有"肺气虚寒/温肺止流丹"一个证型，与急性发热完全不符。

**当前代码（行775-783）：**
```python
if _chief_complaint_disease and disease != DISEASE_NAME_MAP[_chief_complaint_disease]:
    if _chief_complaint_disease not in disease and _chief_complaint_disease not in m1_out:
        _forced_disease = DISEASE_NAME_MAP[_chief_complaint_disease]
        # 检查强制映射的疾病是否存在于知识库中，不存在则跳过
        if _forced_disease in m2.kb:
            print(f"[CHIEF_COMPLAINT_FORCE] 主诉病名:{_chief_complaint_disease} -> {_forced_disease}, 当前disease:{disease}")
            disease = _forced_disease
        else:
            print(f"[CHIEF_COMPLAINT_SKIP] 主诉病名:{_chief_complaint_disease} -> {_forced_disease} 不存在于知识库中，跳过")
```

**修复方案：** 在强制覆盖前增加判断——如果 CHIEF_COMPLAINT 检测到的关键词匹配的是慢性病，而当前 disease 指向急性感染，则跳过覆盖。

具体做法：定义一套"慢性病关键词列表"，当匹配到其中的慢性病时，检查当前 disease 是否包含急性病关键词（"急性"、"感染"、"热"、"上呼吸道"、"肺炎"等），如果有则跳过强制覆盖。

建议添加的代码（在 `_forced_disease = DISEASE_NAME_MAP[_chief_complaint_disease]` 之前或之后）：

```python
# 慢性病关键词：当 CHIEF_COMPLAINT 匹配到这些慢性病时，不覆盖 M1 的急性病诊断
_chronic_disease_keywords = [
    "慢性", "过敏", "鼻炎", "鼻窦炎", "腺样体", "咽炎", "扁桃体肥大",
    "哮喘", "湿疹", "荨麻疹", "高血压", "糖尿病", "冠心病",
    "腰椎", "颈椎", "胃炎", "溃疡", "结石"
]
# 急性病关键词：判断当前 disease 是否指向急性感染/发热
_acute_disease_keywords = [
    "急性", "发热", "感染", "上呼吸道", "肺炎", "支气管炎",
    "咳嗽", "感冒", "呼吸道", "扁桃体炎", "咽炎"
]
_is_chronic = any(kw in _chief_complaint_disease for kw in _chronic_disease_keywords)
_is_acute_current = any(kw in disease for kw in _acute_disease_keywords)
if _is_chronic and _is_acute_current:
    print(f"[CHIEF_COMPLAINT_SKIP_CHRONIC] 主诉病名:{_chief_complaint_disease} 为慢性病，当前disease:{disease} 为急性病，跳过强制覆盖")
```

这段代码应该插入在 `if _forced_disease in m2.kb:` 之前，或者在 `_forced_disease = DISEASE_NAME_MAP[_chief_complaint_disease]` 之后。

---

#### 修改点2：增强 _is_cold_syndrome 的冷证关键词覆盖（行1088）

**问题：** `_is_cold_syndrome` 只检查 `["风寒", "寒邪", "寒凝", "寒饮", "虚寒", "肺气虚寒", "肺寒", "寒湿"]`。但 M2 知识库中还有更多冷证/虚寒证型名，如：
- 慢性鼻窦炎 → "胆腑郁热，上犯窦窍"（不是冷证，不需要）
- 变应性鼻炎 → "肺气虚寒"（已包含）
- 腹泻 (Diarrhea) → "寒湿内盛"
- 腹泻 → "风寒泻"
- 头痛 (Headache) → "风寒头痛"
- 失眠 → "肝郁化火"（不需要）

当前列表已经比较全面，但可以增加一些通用冷性证型的判别。关键在于：**当热象重写触发时，当前代码只对 `_is_cold_syndrome` 为 True 或 syndrome_name 为空时生效。还需要考虑"肺气虚寒"之外的其他虚寒证型。**

建议将 `_is_cold_syndrome` 扩展为：
```python
_is_cold_syndrome = any(k in syndrome_name for k in [
    "风寒", "寒邪", "寒凝", "寒饮", "虚寒", "肺气虚寒", "肺寒", 
    "寒湿", "寒湿内盛", "风寒泻", "风寒头痛", "寒性", "阳虚",
    "寒痰", "寒凝气滞", "外寒"
])
```

当前代码已是 `["风寒", "寒邪", "寒凝", "寒饮", "虚寒", "肺气虚寒", "肺寒", "寒湿"]`，基本够用，可以再加 `"寒湿内盛"`、`"风寒泻"`、`"风寒头痛"`、`"阳虚"` 这几个。

---

#### 修改点3：热象重写中增加"偏黄苔"的识别路径（行1087-1103）

**问题：** 当前热象重写路径有：
1. `_has_green_sputum` → 绿痰 → 痰热蕴肺/清金化痰汤
2. `_has_yellow_greasy_fur` → 黄腻苔 → 肺热壅盛/麻杏石甘汤
3. `any(发热/高热/手心热)` → 风热犯肺/银翘散
4. else → 单纯痰黄 → 风热犯肺/银翘散

这些路径已经覆盖了主要场景。但是否还需要增加"夹痰湿夹热"的理路？比如张祎宸的"苔白腻偏黄"既不是纯"黄腻"也不是纯"发热"，代码中的"偏黄"关键词已经在 `_has_heat_signs` 中，但 `_has_yellow_greasy_fur` 检查的是 `["黄腻", "黄厚", "黄燥"]`，"偏黄"不属于这些。不过"发热"检测已经能匹配这个病例，所以这个问题已解决。

#### 修改点4：症状加减的发热条目不重复（已存在）

当前代码在 SYMPTOM_HERB_MAP 中有 `(["发热", "发烧"], ["石膏", "知母", "柴胡"], "清热退烧")`，已被正确应用在输出中（张祎宸的方案中有了"石膏（清热退烧）"）。

---

#### 修改点5（重要）：增加对"非感染性慢性病→温补方"场景的 CHIEF_COMPLAINT_FORCE 保护

除了修改点1的慢性病跳过逻辑外，还需要考虑：即使跳过 CHIEF_COMPLAINT_FORCE，`m2_fallback` 也可能因为"咳嗽"等关键词误跳到不合适的疾病。但目前张祎宸的病例中，M1 输出"上气道咳嗽综合征"在 DISEASE_NAME_MAP 中映射为"急性上呼吸道感染"，DISEASE_MAP 正确执行了，问题出在 CHIEF_COMPLAINT_FORCE 覆盖之后。

---

### 修复后验证方法

重启 bridge_server.py，测试两个病例：

**测试1（张祎宸，pid=codex_v1）**
预期流程：
1. M1 → "上气道咳嗽综合征" ✅
2. CHIEF_COMPLAINT_FORCE 检测到"过敏性鼻炎"→"变应性鼻炎"，但因是慢性病且当前是急性发热，跳过覆盖 ✅
3. DISEASE_MAP "上气道咳嗽综合征"→"急性上呼吸道感染" ✅
4. M2 → "风寒感冒/荆防败毒散"（知识库默认） ✅
5. 热象重写（检测到"发热/手心热"）→ "风热犯肺/银翘散" ✅
6. 最终处方：西医诊断"急性上呼吸道感染"，证型"风热犯肺"，处方"银翘散" ✅

**测试2（关之仙，pid=codex_v2）**
预期流程（已通过）：
1. M1 → "社区获得性肺炎" ✅
2. DISEASE_MAP → "肺炎 (Pneumonia)" ✅
3. M2 → "邪犯肺卫（风寒）/三拗汤" ✅
4. 热象重写（检测到绿痰）→ "痰热蕴肺/清金化痰汤" ✅

---

## 总结：待修复点

| 优先级 | 位置 | 问题 | 修复方式 |
|--------|------|------|---------|
| P0 | bridge_server.py 行775-783 | CHIEF_COMPLAINT_FORCE 用慢性病名覆盖急性病诊断 | 添加慢性病关键词检测，跳过覆盖 |
| P1 | bridge_server.py 行1088 | _is_cold_syndrome 关键词不够全面 | 增加"寒湿内盛""风寒泻""风寒头痛""阳虚"等 |
| P2 | bridge_server.py 行1087 | _has_heat_signs 可考虑补充 | 当前已基本完善，可再补"白苔偏黄""烦热"等 |
