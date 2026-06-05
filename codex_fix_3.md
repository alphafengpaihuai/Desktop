# 守一CDSS M1→M2 链路断层修复任务 (第三轮)

## 目标：修复"急性胃肠炎"断链 + 热象重写消化系统路径错误

### 测试暴露的问题

王沐晨（8岁男，急性胃肠炎，发热伴腹痛腹泻3天，最高40°C，大便粘液潜血，舌红苔黄腻）

当前流程：
```
M1 → "急性胃肠炎"（正确）
DISEASE_MAP 无 "急性胃肠炎" 条目 → 保持原样
M2 检索 "急性胃肠炎" → 知识库中无此 key → syndrome_name=""（空证型）
热象重写触发：_has_heat_signs=True 且 syndrome_name 为空
→ 进入 _has_yellow_greasy_fur 分支（检测到"黄腻"）
→ **肺热壅盛/麻杏石甘汤** ❌（呼吸系统方案，不适合肠道湿热）
```

**根因：** 热象重写的"黄腻苔→肺热壅盛/麻杏石甘汤"路径是为肺热咳嗽患者设计的，但对于"急性胃肠炎+苔黄腻"的患者，正确方向是**肠道湿热→葛根芩连汤或白头翁汤**。

---

## 需修改的文件

### 文件1：bridge_server.py

#### 修改点1：DISEASE_NAME_MAP 补充"急性胃肠炎"

在 DISEASE_NAME_MAP 字典（行688-767）中增加：

```python
"急性胃肠炎": "痢疾 (Dysentery)",
"急性肠胃炎": "痢疾 (Dysentery)",
"胃肠炎": "痢疾 (Dysentery)",
```

因为知识库中最匹配急性胃肠炎（有粘液、潜血、腹痛、发热）的是"痢疾 (Dysentery)" → 湿热痢 → 白头翁汤。

#### 修改点2：热象重写增加消化系统方向判断（行1087行附近）

在热象重写代码的 `_has_heat_signs` 检测之后，现有的路径：

```python
if _has_green_sputum:
    # 绿痰→痰热蕴肺→清金化痰汤
elif _has_yellow_greasy_fur:
    # 黄腻苔→肺热壅盛→麻杏石甘汤
elif any(x in ... for x in ["发热", "高热", "手心热"]):
    # 发热+风寒证型→风热犯肺→银翘散
else:
    # 单纯痰黄→风热犯肺→银翘散
```

**需要在 `_has_yellow_greasy_fur` 分支之前加入消化系统分支判断：**

```python
# 补充：检测消化系统症状关键词（腹泻/腹痛/粘液/潜血/痢疾/便溏等）
_digestive_keywords = ["腹泻", "腹痛", "粘液", "潜血", "痢疾", "便溏", "便血", "肠炎", "胃肠", "水样便", "里急后重"]
_has_digestive_symptoms = any(kw in _all_symptom_txt_for_heat for kw in _digestive_keywords)

# 如果同时有消化系统症状 + 黄腻苔，走肠道湿热方向
if _has_digestive_symptoms and (_has_yellow_greasy_fur or any(kw in _all_symptom_txt_for_heat for kw in ["苔黄", "舌红", "黄腻"])):
    syndrome_name = "湿热蕴肠（湿热痢）"
    formula_name = "葛根芩连汤合白头翁汤"
    herbs = ["葛根", "黄芩", "黄连", "白头翁", "秦皮", "黄柏", "木香", "白芍", "甘草"]
    _common_digestive_dosages = {
        "葛根": "葛根15g", "黄芩": "黄芩9g", "黄连": "黄连6g", "白头翁": "白头翁12g",
        "秦皮": "秦皮9g", "黄柏": "黄柏9g", "木香": "木香6g", "白芍": "白芍12g", "甘草": "甘草3g"
    }
    dose_parts = []
    for _h in herbs:
        if _h in _common_digestive_dosages:
            dose_parts.append(_common_digestive_dosages[_h])
        else:
            dose_parts.append(_h)
    dosage_str = "\n  ".join(dose_parts)
    _heat_rewrite_used = True
    print(f"[HEAT_REWRITE_DIGESTIVE] 腹泻+苔黄腻 → 湿热蕴肠/葛根芩连汤合白头翁汤 ({len(herbs)}味)")

# 原有分支保持不变...
elif _has_green_sputum:
    ...
```

**具体的插入位置**：在 `_has_heat_signs` 检测之后、`_is_cold_syndrome` 判断之前，或者在 `_is_cold_syndrome` 条件块内部的最开头。

建议的结构（用 `# --- 消化系统方向 ---` 注释标记）：

```python
if (_has_heat_signs and _is_cold_syndrome) or (_has_heat_signs and not syndrome_name.strip()):
    
    # --- 消化系统方向（腹泻+苔黄腻/舌红） ---
    _digestive_keywords = ["腹泻", "腹痛", "粘液", "潜血", "痢疾", "便溏", "便血", "肠炎", "胃肠", "水样便", "里急后重"]
    _has_digestive = any(k in _all_symptom_txt_for_heat for k in _digestive_keywords)
    _has_digestive_heat_fur = any(k in _all_symptom_txt_for_heat for k in ["苔黄", "黄腻", "舌红"])
    if _has_digestive and (_has_yellow_greasy_fur or _has_digestive_heat_fur):
        syndrome_name = "湿热蕴肠（湿热痢）"
        formula_name = "葛根芩连汤合白头翁汤"
        herbs = ["葛根", "黄芩", "黄连", "白头翁", "秦皮", "黄柏", "木香", "白芍", "甘草"]
        # ... 剂量设置
        _heat_rewrite_used = True
        print("[HEAT_REWRITE_DIGESTIVE] ...")
    
    # --- 呼吸系统方向（绿痰） ---
    elif _has_green_sputum:
        ...
    
    # --- 呼吸系统方向（黄腻苔） ---
    elif _has_yellow_greasy_fur:
        ...
    
    # --- 发热方向 ---
    elif any(x in ... for x in ["发热", "高热", "手心热"]):
        ...
    
    # --- 默认热象方向 ---
    else:
        ...
```

---

## 修复后验证

重启 bridge_server.py，测试：

**王沐晨（8岁男，急性胃肠炎 + 发热腹痛腹泻 + 粘液潜血 + 苔黄腻）**

预期流程：
1. M1 → "急性胃肠炎"
2. DISEASE_MAP → "痢疾 (Dysentery)"
3. M2 → 找到"湿热痢/白头翁汤"
4. 热象重写消化系统分支 → 确认"湿热蕴肠/葛根芩连汤合白头翁汤"
5. 最终输出：西医诊断"痢疾 (Dysentery)"，证型"湿热蕴肠（湿热痢）"，处方含葛根芩连汤白头翁汤加减

**张祎宸 + 关之仙 回归测试不变。**
