"""
M1 诊断推理引擎 v4 — 中医辅助诊疗系统「守一」
===============================================
核心设计：双轨召回 + LLM 逻辑裁判 + 默沙东回退

流程：
1. 双轨召回（症状轨代码检索 + 病名轨 LLM 猜测）
2. LLM 作为逻辑裁判，对比患者资料与候选病诊断标准
3. 若 LLM 无法匹配 → 查默沙东 → 缓存
4. 输出 Top 1-3 疾病名称（不输出置信度、推理过程）

设计原则：
- 所有诊断基于疾病诊断标准知识库（diseases_core.json）
- 模型仅负责对比打分，不负责生成诊断
- 文本匹配用 LLM 更好，代码负责召回和兜底
"""

import json
import os
import re
import hashlib
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class NormalizedInput:
    """标准化患者输入"""
    patient_mentioned_disease: str = ""
    chief_complaint: str = ""
    symptoms: List[str] = field(default_factory=list)
    signs: List[str] = field(default_factory=list)
    labs: List[str] = field(default_factory=list)
    imaging: List[str] = field(default_factory=list)
    negative_findings: List[str] = field(default_factory=list)
    duration: str = ""
    onset: str = ""
    severity: str = ""
    location: str = ""
    trigger_relief: str = ""


@dataclass
class CandidateInfo:
    """候选疾病信息（给 LLM 的输入）"""
    disease_name: str
    disease_name_cn: str
    diagnostic_criteria: List[str]
    typical_symptoms: List[str]
    differential_diagnosis: List[str]
    icd11_code: str = ""


# ── 同义词映射（仅用于症状轨代码检索，不用于诊断）────────

SYNONYM_MAP: Dict[str, List[str]] = {
    "呼吸困难": ["呼吸困难", "气短", "喘不上气", "气紧", "憋气", "呼吸急促", "dyspnea"],
    "腹泻": ["腹泻", "拉肚子", "稀便", "diarrhea"],
    "眩晕": ["眩晕", "头晕转圈", "天旋地转", "vertigo"],
    "胸痛": ["胸痛", "胸口压着痛", "胸骨后痛", "chest pain"],
    "咳嗽": ["咳嗽", "咳", "cough"],
    "发热": ["发热", "发烧", "fever"],
    "头痛": ["头痛", "头胀痛", "headache"],
    "恶心": ["恶心", "nausea", "想吐"],
    "呕吐": ["呕吐", "vomit", "吐"],
    "乏力": ["乏力", "疲劳", "疲倦", "无力", "fatigue"],
    "心悸": ["心悸", "心慌", "palpitations"],
    "咯血": ["咯血", "咳血", "hemoptysis"],
    "腹痛": ["腹痛", "肚子痛", "abdominal pain", "腹部疼痛"],
    "抽搐": ["抽搐", "癫痫", "惊厥", "seizure"],
    "皮疹": ["皮疹", "红斑", "皮肤疹", "rash"],
    "黄疸": ["黄疸", "jaundice"],
    "血尿": ["血尿", "hematuria"],
    "消瘦": ["消瘦", "体重下降", "weight loss"],
    "咽喉痛": ["咽喉痛", "咽痛", "喉咙痛", "sore throat"],
    "鼻塞": ["鼻塞", "nasal congestion"],
    "流涕": ["流涕", "流鼻涕", "鼻漏", "runny nose"],
    "喘鸣": ["喘鸣", "喘息", "哮鸣", "wheezing"],
    "水肿": ["水肿", "浮肿", "肿胀", "edema"],
}

NORMALIZATION_MAP: Dict[str, str] = {}
for std, variants in SYNONYM_MAP.items():
    for v in variants:
        NORMALIZATION_MAP[v] = std


def normalize_text(text: str) -> str:
    """归一化为标准术语"""
    t = text.strip().lower()
    return NORMALIZATION_MAP.get(t, text.strip())


# ── 引擎核心 ─────────────────────────────────────────────

class M1DiagnosisEngine:
    """M1 诊断推理引擎 v4 — 双轨召回 + LLM 逻辑裁判"""

    def __init__(self, db_path: Optional[str] = None):
        """初始化引擎"""
        if db_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(script_dir, "diseases_core.json")

        with open(db_path, "r", encoding="utf-8") as f:
            self.db: List[dict] = json.load(f)

        # ── 索引 ──
        self.name_index: Dict[str, dict] = {}       # 英文名(小写) → 条目
        self.cn_name_index: Dict[str, dict] = {}     # 中文名 → 条目
        self.keyword_index: Dict[str, List[dict]] = {}  # 关键词 → 条目列表

        # 疾病名称列表（供 LLM 病名轨使用）
        self.disease_names_list: List[str] = []

        # ── 系统索引（疾病→系统）──
        self.system_index: Dict[str, str] = {}

        for entry in self.db:
            name = entry["disease_name"].lower()
            self.name_index[name] = entry
            cn = entry.get("diseaseName_cn", "").strip().lower()
            if cn:
                self.cn_name_index[cn] = entry
            # 关键词索引
            for word in re.split(r'[\s()/,]+', entry["disease_name"].lower()):
                if len(word) > 2:
                    self.keyword_index.setdefault(word, []).append(entry)
            # 疾病名称列表
            display = f'{entry.get("diseaseName_cn", "")} ({entry["disease_name"]})'
            self.disease_names_list.append(display)

            # 提取系统分类
            sm = entry.get("source_verified_medical_summary", {})
            defn = sm.get("definition_and_core_problem", "")
            m = re.search(r"是(.+?)相关诊断条目", defn)
            if m:
                self.system_index[name] = m.group(1).strip()
            else:
                self.system_index[name] = "综合"

        # ── 默沙东缓存 ──
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.merck_cache_path = os.path.join(script_dir, "m1_semantic_cache.json")
        self.merck_cache: Dict[str, dict] = {}
        if os.path.exists(self.merck_cache_path):
            try:
                with open(self.merck_cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                for k, v in cached.items():
                    if isinstance(v, dict) and v.get("result"):
                        self.merck_cache[k] = v
            except Exception:
                self.merck_cache = {}

        # ── LLM 配置 ──
        self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.deepseek_api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")

        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        self.llm_provider = os.environ.get("LLM_PROVIDER", "deepseek")
        if self.gemini_api_key and not os.environ.get("LLM_PROVIDER") and not self.deepseek_api_key:
            self.llm_provider = "gemini"
        if self.deepseek_api_key:
            self.llm_provider = "deepseek"

        # ── 疾病名缓存（供 LLM 病名轨复用，避免重复读取文件）──
        self._disease_names_txt: Optional[str] = None

    # ══════════════════════════════════════════════════════
    #  输入标准化
    # ══════════════════════════════════════════════════════

    def normalize_input(self, raw: Dict) -> NormalizedInput:
        """标准化患者输入"""
        ni = NormalizedInput()

        ni.patient_mentioned_disease = raw.get("patient_mentioned_disease", "")

        chief = raw.get("chief_complaint", "")
        if isinstance(chief, list):
            chief = chief[0] if chief else ""
        ni.chief_complaint = chief

        for key in ["symptoms", "signs", "labs", "imaging", "negative_findings"]:
            vals = raw.get(key, [])
            if isinstance(vals, str):
                vals = [vals]
            setattr(ni, key, [normalize_text(v) for v in vals if isinstance(v, str) and v.strip()])

        for key in ["duration", "onset", "severity", "location", "trigger_relief"]:
            setattr(ni, key, raw.get(key, ""))

        if not ni.chief_complaint and ni.symptoms:
            ni.chief_complaint = ni.symptoms[0]

        return ni

    # ══════════════════════════════════════════════════════
    #  症状轨：代码检索
    # ══════════════════════════════════════════════════════

    def _retrieve_by_symptom_track(self, ni: NormalizedInput, top_k: int = 20) -> List[dict]:
        """症状轨：多路召回 + 关键特征重排

        多路召回：
        1. 病名/中文名精确/模糊匹配
        2. 主诉匹配
        3. 症状/体征/检查匹配（key_symptom_pattern / key_exam_findings / local_retrieval_keywords）
        4. 语义匹配（diagnostic_criteria / typical_symptoms 中包含多个患者症状）

        重排（得分叠加）：
        - key_symptom_pattern / key_exam_findings 命中 → 高权重
        - chief_complaint 命中典型症状 → 中权重
        - 多症状同时命中 → 加分
        - 仅命中泛化词 → 降权×0.1
        - 仅命中"发热、乏力、疼痛"等单一泛化症状 → 降权
        - 与主诉器官系统无关 → 降权（但不硬过滤）

        返回 top_k 候选给 LLM 裁判。
        """
        if self.llm_api_available():
            return self._retrieve_by_symptom_track_v2(ni, top_k)
        else:
            return self._retrieve_by_symptom_track_fallback(ni, top_k)

    def _retrieve_by_symptom_track_v2(self, ni: NormalizedInput, top_k: int = 20) -> List[dict]:
        """多路召回 v2（LLM 可用时的智能版本）"""
        patient_texts = set()
        for key in ["symptoms", "signs", "labs", "imaging"]:
            for t in getattr(ni, key, []):
                patient_texts.add(t.lower().strip())
        patient_texts.discard("")
        if not patient_texts:
            return []

        patient_disease = ni.patient_mentioned_disease.lower().strip()
        chief = ni.chief_complaint.lower().strip()

        # ── 系统粗过滤：从症状/主诉推断最可能的 1-3 个系统 ──
        patient_systems = self._infer_systems(ni, patient_texts, chief)
        for s in patient_systems:
            pass  # 用于调试

        # ── 每条路召回的候选集合 ──
        candidates = {}  # disease_name_lower → {entry, score, reasons}

        # 路1：病名/中文名精确/模糊匹配（最高权重）
        if patient_disease:
            for entry in self.db:
                # 系统粗过滤：只检索相关系统的疾病
                en = entry["disease_name"].lower()
                if patient_systems and self.system_index.get(en, "综合") not in patient_systems:
                    continue
                dn = en
                cn = entry.get("diseaseName_cn", "").lower()
                if not cn:
                    continue
                # 精确匹配
                if patient_disease == dn or patient_disease == cn:
                    candidates.setdefault(dn, {"entry": entry, "score": 0, "reasons": []})
                    candidates[dn]["score"] += 10
                    candidates[dn]["reasons"].append("病名精确匹配")
                # 包含匹配
                elif (len(patient_disease) >= 2 and
                      (patient_disease in dn or patient_disease in cn or dn in patient_disease or cn in patient_disease)):
                    candidates.setdefault(dn, {"entry": entry, "score": 0, "reasons": []})
                    candidates[dn]["score"] += 6
                    candidates[dn]["reasons"].append("病名模糊匹配")

        # 路2：主诉匹配 key_symptom_pattern（中权重）
        if chief:
            for entry in self.db:
                # 系统粗过滤
                if patient_systems and self.system_index.get(entry["disease_name"].lower(), "综合") not in patient_systems:
                    continue
                ksp = entry.get("source_verified_medical_summary", {}).get("key_symptom_pattern", "")
                ts_list = entry.get("typical_symptoms", [])
                dc_list = entry.get("diagnostic_criteria", [])
                dn = entry["disease_name"].lower()

                all_text = (ksp + " " + " ".join(ts_list) + " " + " ".join(dc_list)).lower()
                if chief in all_text or any(s in all_text for s in re.split(r"[，；、,;]", chief) if len(s) >= 2):
                    candidates.setdefault(dn, {"entry": entry, "score": 0, "reasons": []})
                    candidates[dn]["score"] += 5
                    candidates[dn]["reasons"].append("主诉命中典型症状")

        # 路3：症状/体征/检查匹配 key_exam_findings / local_retrieval_keywords（高权重）
        for pt in patient_texts:
            for entry in self.db:
                # 系统粗过滤
                if patient_systems and self.system_index.get(entry["disease_name"].lower(), "综合") not in patient_systems:
                    continue
                sm = entry.get("source_verified_medical_summary", {})
                ksp = sm.get("key_symptom_pattern", "").lower()
                kef = sm.get("key_exam_findings", "").lower()
                keywords = [k.lower() for k in sm.get("local_retrieval_keywords", [])]
                ts_list = [t.lower() for t in entry.get("typical_symptoms", [])]
                dn = entry["disease_name"].lower()

                # 检查命中
                hit_weight = 0
                reason = ""
                if pt in ksp or pt in kef:
                    hit_weight = 7
                    reason = f"关键特征命中: {pt}"
                elif any(pt in kw or kw in pt for kw in keywords):
                    hit_weight = 6
                    reason = f"局部检索词命中: {pt}"
                elif any(pt == ts or pt in ts or ts in pt for ts in ts_list):
                    # 检查是否泛化词
                    if pt in HIGH_FREQ_GENERIC_TERMS:
                        hit_weight = 1
                        reason = f"泛化词命中: {pt}"
                    else:
                        hit_weight = 4
                        reason = f"典型症状命中: {pt}"

                if hit_weight > 0:
                    candidates.setdefault(dn, {"entry": entry, "score": 0, "reasons": []})
                    candidates[dn]["score"] += hit_weight
                    candidates[dn]["reasons"].append(reason)

        # 路4：diagnostic_criteria 语义召回（低权重，仅当多个患者症状同时命中）
        for entry in self.db:
            # 系统粗过滤
            if patient_systems and self.system_index.get(entry["disease_name"].lower(), "综合") not in patient_systems:
                continue
            dc_list = [d.lower() for d in entry.get("diagnostic_criteria", [])]
            dn = entry["disease_name"].lower()
            dc_text = " ".join(dc_list)
            pts_hit = sum(1 for pt in patient_texts if len(pt) >= 2 and (pt in dc_text))
            if pts_hit >= 2:
                candidates.setdefault(dn, {"entry": entry, "score": 0, "reasons": []})
                candidates[dn]["score"] += pts_hit * 2  # 每命中一个症状 +2
                candidates[dn]["reasons"].append(f"诊断标准多症状命中({pts_hit}个)")

        # ── 重排 ──
        # 先确定主诉的器官系统方向（用于跨系统降权）
        chief_systems = self._infer_systems_from_text(chief + " " + " ".join(patient_texts))

        scored = []
        for dn, data in candidates.items():
            entry = data["entry"]
            raw_score = data["score"]
            reasons = list(set(data["reasons"]))
            final_score = float(raw_score)

            # 1. key_findings 命中 → 加分（已在上面加过了）

            # 2. 多症状同时命中 → 额外加分
            hit_symptoms = set()
            for r in reasons:
                if "命中" in r:
                    for pt in patient_texts:
                        if pt in r:
                            hit_symptoms.add(pt)
            if len(hit_symptoms) >= 3:
                final_score += 5
            elif len(hit_symptoms) >= 2:
                final_score += 2

            # 3. 仅命中泛化词且没有关键特征命中 → 降权
            generic_only = all("泛化词命中" in r or "诊断标准多症状命中" in r for r in reasons)
            if generic_only and not any("关键特征命中" in r or "局部检索词命中" in r or "病名" in r or "主诉命中" in r for r in reasons):
                final_score *= 0.2

            # 4. 与主诉器官系统无关 → 降权
            dn_text = (entry["disease_name"] + " " + entry.get("diseaseName_cn", "")).lower()
            sm_text = entry.get("source_verified_medical_summary", {}).get("key_symptom_pattern", "").lower()
            disease_all_text = dn_text + " " + sm_text
            system_overlap = 0
            for sys, kws in SYSTEM_KEYWORDS.items():
                if sys in chief_systems:
                    if any(kw in disease_all_text for kw in kws):
                        system_overlap += 1
            if system_overlap == 0:
                final_score *= 0.5  # 完全无关降权50%
                reasons.append("系统无关降权")

            # 5. 只有2个或以下泛化词命中 → 大幅度降权
            if len(reasons) <= 2 and all("泛化词" in r or "诊断标准多症状命中" in r or "系统无关" in r for r in reasons):
                final_score *= 0.3

            scored.append((final_score, entry, reasons))

        # 排序输出
        scored.sort(key=lambda x: -x[0])
        return [entry for _, entry, _ in scored[:top_k]]

    def _retrieve_by_symptom_track_fallback(self, ni: NormalizedInput, top_k: int = 20) -> List[dict]:
        """无 LLM 时的兜底版本（简化版多路召回，不用LLM也能运行）"""
        patient_texts = set()
        for key in ["symptoms", "signs", "labs", "imaging"]:
            for t in getattr(ni, key, []):
                patient_texts.add(t.lower().strip())
        patient_texts.discard("")
        if not patient_texts:
            return []

        patient_disease = ni.patient_mentioned_disease.lower().strip()
        chief = ni.chief_complaint.lower().strip()

        # 系统粗过滤
        patient_systems = self._infer_systems(ni, patient_texts, chief)

        scored = []
        for entry in self.db:
            if patient_systems and self.system_index.get(entry["disease_name"].lower(), "综合") not in patient_systems:
                continue
            dn = entry["disease_name"].lower()
            cn = entry.get("diseaseName_cn", "").lower()
            sm = entry.get("source_verified_medical_summary", {})
            ksp = sm.get("key_symptom_pattern", "").lower()
            kef = sm.get("key_exam_findings", "").lower()
            keywords = [k.lower() for k in sm.get("local_retrieval_keywords", [])]
            ts_list = [t.lower() for t in entry.get("typical_symptoms", [])]
            dc_list = [d.lower() for d in entry.get("diagnostic_criteria", [])]
            all_text = (ksp + " " + kef + " " + " ".join(keywords) + " " +
                        " ".join(ts_list) + " " + " ".join(dc_list))

            score = 0

            # 病名匹配
            if patient_disease and cn:
                if patient_disease == dn or patient_disease == cn:
                    score += 12
                elif len(patient_disease) >= 2 and (patient_disease in dn or dn in patient_disease):
                    score += 8

            # 主诉匹配
            if chief and (chief in ksp or any(chief in ts for ts in ts_list)):
                score += 5

            # 关键特征匹配
            for pt in patient_texts:
                if pt in ksp or pt in kef:
                    score += 6
                elif any(pt in kw or kw in pt for kw in keywords):
                    score += 5
                elif any(pt == ts or (len(pt) >= 2 and pt in ts) for ts in ts_list):
                    if pt not in HIGH_FREQ_GENERIC_TERMS:
                        score += 3
                    else:
                        score += 0.5

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [entry for _, entry in scored[:top_k]]

    def _infer_systems_from_text(self, text: str) -> set:
        """从文本推断涉及的器官系统"""
        systems = set()
        text_lower = text.lower()
        for sys_name, kws in SYSTEM_KEYWORDS.items():
            if any(kw in text_lower for kw in kws):
                systems.add(sys_name)
        return systems or {"全身"}

    def _infer_systems(self, ni, patient_texts: set, chief: str) -> set:
        """极简系统推断：从症状/主诉/病名推断最可能的 1-3 个系统"""
        # 从患者所有文本中提取关键词
        all_text = (chief + " " + " ".join(patient_texts) + " " +
                    ni.patient_mentioned_disease).lower()

        # 症状→系统信号
        sys_signals = {}
        for sys_name, kws in SYSTEM_KEYWORDS.items():
            hits = sum(1 for kw in kws if kw in all_text)
            if hits > 0:
                sys_signals[sys_name] = hits

        if not sys_signals:
            # 完全没信号时从症状推断
            system_from_text = self._infer_systems_from_text(all_text)
            if system_from_text != {"全身"}:
                return system_from_text
            # 还是没信号→所有系统（不过滤）
            return set(self.system_index.values())

        # 取命中次数最多的 1-3 个系统
        ranked = sorted(sys_signals.items(), key=lambda x: -x[1])
        top_systems = {s for s, _ in ranked[:3]}

        # 如果"全身"/"综合"有命中，仅在患者没有明确系统归属时才加入
        whole_body = {"全身", "综合"}
        if len(top_systems) == 0 or (len(top_systems) == 1 and "全身" in top_systems):
            for wb in whole_body:
                if wb.lower() in all_text or any(wb in s for s in ranked[:5]):
                    top_systems.add(wb)

        # 如果系统分数差异很大 + 第一名明显 > 第三名，只保留前1-2个
        if len(ranked) >= 3:
            ratio = ranked[0][1] / max(ranked[2][1], 1)
            if ratio > 5 and ranked[0][1] >= 3:
                top_systems = {s for s, _ in ranked[:2]}

        return top_systems



    # ══════════════════════════════════════════════════════
    #  病名轨：LLM 猜 3-5 个病名
    # ══════════════════════════════════════════════════════

    def _get_disease_names_text(self) -> str:
        """获取疾病名称列表文本（供 LLM 选择）"""
        if self._disease_names_txt is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m1_disease_names.txt")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._disease_names_txt = f.read()
            else:
                self._disease_names_txt = "\n".join(self.disease_names_list)
        return self._disease_names_txt

    def _retrieve_by_disease_name_track(self, ni: NormalizedInput) -> List[dict]:
        """病名轨：LLM 根据患者资料猜 3-5 个病名

        第一步：让 LLM 从主诉推断涉及的系统（如呼吸系统、消化系统），缩小范围
        第二步：在缩小后的列表中选 3-5 个病名
        """
        if not self.llm_api_available():
            return []

        # 第一步：让 LLM 先缩小系统范围
        system_prompt = f"""根据患者主诉和症状，判断最可能涉及的 1-2 个器官系统。

主诉：{ni.chief_complaint}
症状：{'；'.join(ni.symptoms + ni.signs + ni.labs)}

请从以下列表中选择最相关的 1-2 个系统（只输出系统名，每行一个）：
呼吸系统、循环系统、消化系统、神经系统、泌尿系统、内分泌系统、血液系统、
骨骼肌肉系统、皮肤系统、免疫系统、生殖系统、精神心理、全身性/感染性"""

        system_result = self._call_llm(system_prompt, temperature=0.1, max_tokens=50)

        # 用系统名或症状关键词过滤疾病列表
        system_kw_map = {
            "呼吸": ["呼吸", "肺", "支气管", "肺炎", "哮喘", "咳嗽", "respiratory", "pulmonary", "lung"],
            "循环": ["心", "血管", "冠脉", "心律", "cardiac", "heart", "vascular"],
            "消化": ["胃", "肠", "肝", "胰", "胆", "食管", "gastr", "hepat", "enter", "pancrea"],
            "神经": ["神经", "脑", "卒中", "癫痫", "偏头痛", "neuro", "cerebr", "stroke"],
            "泌尿": ["肾", "尿", "膀胱", "renal", "nephr", "urinar"],
            "内分泌": ["甲状腺", "糖尿", "肾上腺", "激素", "thyroid", "diabet", "adrenal"],
            "血液": ["贫血", "血液", "白血", "淋巴", "凝血", "anemia", "hemato"],
            "皮肤": ["皮肤", "皮疹", "湿疹", "银屑", "dermat", "skin", "rash"],
            "全身": ["感染", "发热", "败血", "病毒", "细菌", "fever", "infect", "sepsis"],
        }

        narrowed_keywords = []
        if system_result:
            for line in system_result.strip().split("\n"):
                for sys_name, kws in system_kw_map.items():
                    if sys_name in line:
                        narrowed_keywords.extend(kws)

        # 如果 LLM 缩小范围失败，用症状关键词
        if not narrowed_keywords:
            narrowed_keywords = [s.lower() for s in ni.symptoms + [ni.chief_complaint] if s]

        # 用关键词过滤疾病列表
        filtered_names = []
        for display in self.disease_names_list:
            display_lower = display.lower()
            if any(kw in display_lower for kw in narrowed_keywords):
                filtered_names.append(display)

        if not filtered_names or len(filtered_names) > 200:
            filtered_names = self.disease_names_list[:100]

        disease_list = "\n".join(filtered_names)

        prompt = f"""你是一个疾病名称猜测助手。从下面的疾病列表中，选出最可能符合患者病情的 3-5 个疾病名称。

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{'；'.join(ni.symptoms)}
- 体征：{'；'.join(ni.signs)}
- 实验室：{'；'.join(ni.labs)}
- 影像学：{'；'.join(ni.imaging)}
- 患者提到的病名：{ni.patient_mentioned_disease}
- 病程：{ni.duration}

## 疾病列表
{disease_list}

## 要求
1. 只输出疾病全称（中文名 (英文名)），每行一个
2. 严格从列表中选，不要创造列表中不存在的病名
3. 患者提到的病名在列表中则优先选
4. 按可能性从高到低排列
5. 信息不足无法判断时输出"无匹配"
6. 不要输出任何其他文字"""

        result = self._call_llm(prompt, temperature=0.2)
        if not result:
            return []

        # 解析 LLM 返回的病名
        candidates = []
        seen = set()
        for line in result.strip().split("\n"):
            line = line.strip().strip('"').strip("'").strip("-").strip()
            if not line or line == "无匹配":
                continue

            match = re.match(r'(.+?)\s*[（(](.+?)[）)]', line)
            if match:
                cn = match.group(1).strip()
                en = match.group(2).strip().lower()
                if en in self.name_index and en not in seen:
                    candidates.append(self.name_index[en])
                    seen.add(en)
                elif cn.lower() in self.cn_name_index:
                    entry = self.cn_name_index[cn.lower()]
                    if entry["disease_name"].lower() not in seen:
                        candidates.append(entry)
                        seen.add(entry["disease_name"].lower())
            else:
                line_lower = line.lower()
                if line_lower in self.name_index and line_lower not in seen:
                    candidates.append(self.name_index[line_lower])
                    seen.add(line_lower)
                elif line_lower in self.cn_name_index:
                    entry = self.cn_name_index[line_lower]
                    if entry["disease_name"].lower() not in seen:
                        candidates.append(entry)
                        seen.add(entry["disease_name"].lower())

        return candidates

    # ══════════════════════════════════════════════════════
    #  LLM 逻辑裁判：选 Top 1-3        return candidates

    # ══════════════════════════════════════════════════════
    #  LLM 逻辑裁判：选 Top 1-3
    # ══════════════════════════════════════════════════════

    def _llm_select_top_diagnoses(self, ni: NormalizedInput, candidates: List[dict]) -> List[str]:
        """LLM 作为逻辑裁判，选出 Top 1-3 疾病名称"""
        if not candidates:
            return []

        # 构建候选疾病信息
        candidate_sections = []
        for i, entry in enumerate(candidates[:12]):  # 最多给 LLM 12 个候选
            cn = entry.get("diseaseName_cn", "")
            display_name = f'{cn} ({entry["disease_name"]})' if cn else entry["disease_name"]
            criteria = entry.get("diagnostic_criteria", [])
            typical = entry.get("typical_symptoms", [])
            diff_dx = entry.get("differential_diagnosis", [])

            section = f"""### 候选 {i+1}：{display_name}
- 诊断标准：{'；'.join(criteria[:8])}
- 典型症状：{'；'.join(typical[:6])}
- 鉴别诊断：{'；'.join(diff_dx[:5])}"""
            candidate_sections.append(section)

        prompt = f"""你是一名诊断专家。请根据患者资料和候选疾病的诊断标准，选出最符合患者病情的 Top 1-3 个疾病。

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{', '.join(ni.symptoms)}
- 体征：{', '.join(ni.signs)}
- 实验室检查：{', '.join(ni.labs)}
- 影像学：{', '.join(ni.imaging)}
- 患者提到的病名：{ni.patient_mentioned_disease}
- 病程：{ni.duration}
- 起病方式：{ni.onset}

## 候选疾病及其诊断标准
{chr(10).join(candidate_sections)}

## 你的任务
1. 逐一对比患者资料与每个候选病的诊断标准
2. 按匹配程度从高到低排序
3. 选出最符合的 Top 1-3 个疾病名称
4. 如果最高匹配与其他候选差距显著，可只输出 1 个
5. 如果所有候选都不匹配，输出"无匹配"

## 输出要求
- 只输出疾病名称（中文名 (英文名) 格式），每行一个
- 按匹配度从高到低排列
- 不要输出任何其他文字、解释或推理过程
- 不要输出列表中不存在的病名"""

        result = self._call_llm(prompt, temperature=0.1)
        if not result:
            return []

        # 解析结果
        diagnoses = []
        for line in result.strip().split("\n"):
            line = line.strip().strip('"').strip("'").strip("-").strip()
            if not line or line == "无匹配":
                continue

            # 尝试解析
            parsed_name = self._parse_disease_name(line)
            if parsed_name and parsed_name not in diagnoses:
                diagnoses.append(parsed_name)

        return diagnoses[:3]

    def _parse_disease_name(self, line: str) -> Optional[str]:
        """解析疾病名称行，返回标准英文名"""
        # 格式1：中文名 (英文名)
        match = re.match(r'(.+?)\s*[（(](.+?)[）)]', line)
        if match:
            en = match.group(2).strip()
            if en.lower() in self.name_index:
                return en
            cn = match.group(1).strip().lower()
            if cn in self.cn_name_index:
                return self.cn_name_index[cn]["disease_name"]

        # 格式2：直接是英文名
        line_lower = line.strip().lower()
        if line_lower in self.name_index:
            return self.name_index[line_lower]["disease_name"]

        # 格式3：直接是中文名
        if line_lower in self.cn_name_index:
            return self.cn_name_index[line_lower]["disease_name"]

        # 格式4：部分匹配
        for name, entry in self.name_index.items():
            if line_lower in name or name in line_lower:
                return entry["disease_name"]

        return None

    # ══════════════════════════════════════════════════════
    #  代码兜底排序（当 LLM 不可用时使用）
    # ══════════════════════════════════════════════════════

    def _score_by_code(self, ni: NormalizedInput, candidates: List[dict]) -> List[str]:
        """代码兜底排序：基于文本匹配度排序输出 Top 1-3"""
        if not candidates:
            return []

        patient_texts = set()
        for key in ["symptoms", "signs", "labs", "imaging"]:
            for t in getattr(ni, key, []):
                patient_texts.add(t.lower().strip())
        patient_texts.discard("")

        if not patient_texts:
            # 只有一个候选时直接返回
            return [candidates[0]["disease_name"]]

        scored = []
        for entry in candidates:
            entry_texts = []
            for field in ["diagnostic_criteria", "typical_symptoms"]:
                for item in entry.get(field, []):
                    if isinstance(item, str):
                        entry_texts.append(item.lower().strip())

            score = 0
            necessary_matches = 0
            for pt in patient_texts:
                for et in entry_texts:
                    if not et:
                        continue
                    if pt == et:
                        score += 3
                        necessary_matches += 1
                    elif len(pt) >= 2 and len(et) >= 2 and (pt in et or et in pt):
                        score += 2
                        necessary_matches += 1
                    elif is_synonym_match(pt, et):
                        score += 2
                        necessary_matches += 1

            scored.append((score, necessary_matches, entry["disease_name"]))

        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [name for _, _, name in scored[:3]]

    # ══════════════════════════════════════════════════════
    #  兜底：查默沙东
    # ══════════════════════════════════════════════════════

    def _search_merck_manual(self, ni: NormalizedInput) -> Optional[str]:
        """查默沙东手册获取最适配病名"""
        # 构造缓存键
        cache_key = hashlib.md5(
            (ni.chief_complaint + "|" + "|".join(ni.symptoms)).encode()
        ).hexdigest()

        if cache_key in self.merck_cache:
            cached = self.merck_cache[cache_key]
            if isinstance(cached, dict) and cached.get("result"):
                return cached["result"]

        prompt = f"""根据患者资料，在默沙东诊疗手册专业版中检索最适配的疾病名称。

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{', '.join(ni.symptoms)}
- 体征：{', '.join(ni.signs)}
- 实验室：{', '.join(ni.labs)}
- 影像学：{', '.join(ni.imaging)}

## 要求
1. 给出最可能的 1-3 个疾病名称（中文名 (英文名) 格式）
2. 每行一个，按可能性从高到低排列
3. 如果无法判断，输出"未找到匹配"

## 输出格式
疾病中文名 (Disease English Name)"""

        result = self._call_llm(prompt, temperature=0.15)
        if result:
            self.merck_cache[cache_key] = {
                "result": result,
                "timestamp": time.time(),
            }
            self._save_merck_cache()

        return result

    def _save_merck_cache(self):
        """保存默沙东缓存"""
        try:
            with open(self.merck_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.merck_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  LLM 调用
    # ══════════════════════════════════════════════════════

    def llm_api_available(self) -> bool:
        """检查 LLM API 是否可用"""
        return bool(self.deepseek_api_key or self.gemini_api_key)

    def _call_llm(self, prompt: str, temperature: float = 0.1, max_tokens: int = 500) -> Optional[str]:
        """调用 LLM"""
        if self.llm_provider == "deepseek" and self.deepseek_api_key:
            return self._call_deepseek(prompt, temperature, max_tokens)
        elif self.llm_provider == "gemini" and self.gemini_api_key:
            return self._call_gemini(prompt, temperature, max_tokens)
        return None

    def _call_deepseek(self, prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
        """调用 DeepSeek API"""
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.deepseek_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.deepseek_model,
                "messages": [
                    {"role": "system", "content": "你是医学诊断专家。严格遵循用户指示输出，不添加多余内容。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            url = f"{self.deepseek_api_base}/v1/chat/completions"
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
        return None

    def _call_gemini(self, prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
        """调用 Gemini API"""
        try:
            import requests
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.gemini_model}:generateContent?key={self.gemini_api_key}"
            )
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
        except Exception:
            return None
        return None

    # ══════════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════════

    def diagnose(self, raw_input: Dict) -> Dict:
        """主诊断入口

        流程：
        1. 标准化输入
        2. 双轨召回（症状轨 + 病名轨）
        3. LLM 逻辑裁判选 Top 1-3
        4. 若无法匹配 → 查默沙东
        5. 输出结果

        Returns:
            JSON 格式的诊断结果
        """
        # 1. 标准化
        ni = self.normalize_input(raw_input)

        # 2. 双轨召回
        symptom_candidates = self._retrieve_by_symptom_track(ni)
        name_candidates = self._retrieve_by_disease_name_track(ni)

        # 合并去重（优先保留有具体标准的版本）
        # 同一疾病可能有英文版（具体标准）和中文版（泛化标准），
        # 用中文名做去重 key，优先保留有具体诊断标准的条目
        def _has_specific_criteria(entry) -> bool:
            """检查疾病条目是否有非泛化的具体诊断标准"""
            generic_templates = {
                "症状和病程符合该病核心临床模式",
                "查体、实验室或影像证据支持",
                "排除同系统常见相似疾病",
                "出现危急值或红旗表现时必须触发外部临床评估",
            }
            dc = entry.get("diagnostic_criteria", [])
            for criterion in dc:
                if criterion not in generic_templates:
                    return True
            return False

        seen = {}
        all_candidates = []
        for entry in symptom_candidates + name_candidates:
            # 用中文名作去重 key
            cn = entry.get("diseaseName_cn", "").strip()
            if not cn:
                cn = entry["disease_name"].replace("_", " ").strip()
            dedup_key = cn.lower()

            if dedup_key in seen:
                existing_entry = seen[dedup_key]
                # 如果新来的有具体标准而现有的没有，替换
                if _has_specific_criteria(entry) and not _has_specific_criteria(existing_entry):
                    all_candidates.remove(existing_entry)
                    all_candidates.append(entry)
                    seen[dedup_key] = entry
            else:
                all_candidates.append(entry)
                seen[dedup_key] = entry

        # 3. 合并后重排序：具体标准 > 泛化标准，且按关键特征匹配数排序
        def _has_specific_criteria(entry) -> bool:
            generic_templates = {
                "症状和病程符合该病核心临床模式",
                "查体、实验室或影像证据支持",
                "排除同系统常见相似疾病",
                "出现危急值或红旗表现时必须触发外部临床评估",
            }
            dc = entry.get("diagnostic_criteria", [])
            for criterion in dc:
                if criterion not in generic_templates:
                    return True
            return False

        def _count_key_matches(entry, ni) -> int:
            """统计患者的症状/体征在疾病关键特征中的命中数"""
            patient_texts = set()
            for key in ["symptoms", "signs", "labs", "imaging"]:
                for t in getattr(ni, key, []):
                    patient_texts.add(t.lower().strip())
            patient_texts.discard("")
            sm = entry.get("source_verified_medical_summary", {})
            ksp = sm.get("key_symptom_pattern", "").lower()
            kef = sm.get("key_exam_findings", "").lower()
            keywords = [k.lower() for k in sm.get("local_retrieval_keywords", [])]
            hits = 0
            for pt in patient_texts:
                if pt in ksp or pt in kef:
                    hits += 1
                elif any(pt in kw or kw in pt for kw in keywords):
                    hits += 0.5
            return hits

        # 先按关键特征匹配数降序，同分时有具体标准的优先
        all_candidates.sort(key=lambda e: (
            _count_key_matches(e, ni) + (1 if _has_specific_criteria(e) else 0),
            -bool(_has_specific_criteria(e)),
        ), reverse=True)

        # 4. LLM 逻辑裁判
        diagnoses = []
        source = "local_cache"
        cache_hit = True

        if all_candidates:
            diagnoses = self._llm_select_top_diagnoses(ni, all_candidates)

        # 4. 如果 LLM 无法匹配 → 查默沙东
        if not diagnoses:
            merck_result = self._search_merck_manual(ni)
            if merck_result:
                for line in merck_result.strip().split("\n"):
                    parsed = self._parse_disease_name(line.strip())
                    if parsed and parsed not in diagnoses:
                        diagnoses.append(parsed)
                source = "merck_manual"
                cache_hit = False
            else:
                # 真·无匹配
                pass

        # 3b. 如果 LLM 不可用或返回空，用代码兜底排序
        if not diagnoses and all_candidates:
            diagnoses = self._score_by_code(ni, all_candidates)

        # 4. 如果仍然无法匹配 → 查默沙东
        if not diagnoses:
            merck_result = self._search_merck_manual(ni)
            if merck_result:
                for line in merck_result.strip().split("\n"):
                    parsed = self._parse_disease_name(line.strip())
                    if parsed and parsed not in diagnoses:
                        diagnoses.append(parsed)
                source = "merck_manual"
                cache_hit = False

        # 5. 构建输出
        result = {
            "diagnosis_calibration": {
                "original_input": raw_input.get("patient_mentioned_disease", ""),
                "calibrated_diagnosis": diagnoses[:3],
            },
            "source": source,
            "cache_hit": cache_hit,
        }

        return result

    def diagnose_json(self, raw_input: Dict) -> str:
        """诊断并返回 JSON 字符串"""
        result = self.diagnose(raw_input)
        return json.dumps(result, ensure_ascii=False, indent=2)


# ── 系统关键词映射 ──────────────────────────────────

SYSTEM_KEYWORDS = {
    "呼吸": ["咳嗽", "咳痰", "咽痛", "咽喉", "扁桃体", "鼻塞", "流涕", "鼻出血",
             "呼吸困难", "肺", "支气管", "气管", "肺炎", "感冒", "流感",
             "发热", "咳痰或气促", "运动耐量下降",
             "pulmon", "respir", "bronch", "pneumon", "tonsil"],
    "耳鼻喉": ["耳", "鼻", "咽", "喉", "扁桃体", "中耳", "鼻窦",
               "tonsil", "pharyn", "laryn", "nasal", "sinus", "otitis"],
    "循环": ["心", "胸痛", "胸闷", "心悸", "气短", "冠脉", "血管", "cardio", "vascular",
             "下肢水肿", "水肿或晕厥", "活动后", "心绞痛", "心肌", "心律不齐"],
    "消化": ["腹痛", "胃", "肠", "肝", "胆", "胰", "食管", "阑尾", "腹泻", "恶心", "呕吐",
             "gastr", "enter", "hepat", "pancrea", "chol", "append"],
    "神经": ["头痛", "头晕", "眩晕", "脑", "神经", "脊髓", "癫痫", "偏头痛",
             "cerebr", "neuro", "seizure"],
    "泌尿": ["血尿", "肾", "尿", "膀胱", "renal", "nephr", "urinar"],
    "皮肤": ["皮疹", "皮肤", "红斑", "dermat", "rash"],
    "全身": ["乏力", "全身", "感染", "fatigue"],
}

# ── 泛化词降权列表（这些词太泛化，命中时降权）───────────

HIGH_FREQ_GENERIC_TERMS: set = {
    "主要系统症状", "病程变化", "疼痛或不适", "功能受限",
    "全身症状", "复发或进展", "红旗表现",
    "症状和病程符合该病核心临床模式", "查体、实验室或影像证据支持",
    "排除同系统常见相似疾病", "出现危急值或红旗表现时必须触发外部临床评估",
    "存在危急值或红旗表现时必须触发外部临床评估",
    "典型症状和体征", "实验室或影像证据支持",
    "根据严重程度和红旗信号决定是否急诊/专科评估",
    "生命体征不稳", "呼吸窘迫", "灌注不良", "意识状态下降",
    "皮肤湿冷/花斑", "原发病灶查体",
    "生命体征", "受累系统查体", "严重程度评估", "并发症体征", "红旗体征筛查",
    "主要系统症状", "病程变化", "疼痛或不适", "功能受限", "全身症状", "复发或进展",
}

# ── 同义词匹配工具函数 ──────────────────────────────────

def is_synonym_match(a: str, b: str) -> bool:
    """检查两个文本是否同义"""
    if not a or not b:
        return False
    a_lower = a.strip().lower()
    b_lower = b.strip().lower()
    if a_lower == b_lower:
        return True
    for variants in SYNONYM_MAP.values():
        if a_lower in variants and b_lower in variants:
            return True
    return False


# ══════════════════════════════════════════════════════
#  命令行入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    engine = M1DiagnosisEngine()

    # 测试用例
    test_input = {
        "patient_mentioned_disease": "咳嗽",
        "chief_complaint": "咳嗽伴发热3天",
        "symptoms": ["咳嗽", "咳痰", "发热", "咽喉痛"],
        "signs": ["咽部充血"],
        "labs": ["血常规示白细胞升高"],
        "imaging": [],
        "negative_findings": [],
        "duration": "3天",
        "onset": "急性",
    }

    result = engine.diagnose_json(test_input)
    print(result)
