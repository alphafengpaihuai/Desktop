"""
M1 西医诊断引擎 v7 — 中医辅助诊疗系统「守一」
=============================================
核心设计：本地病名库优先 + 代码多维评分 + LLM 语义匹配器 + 代码约束校验
遵循 Skill v2.1：docs/prompt_review/M1_diagnosis_prompt_review.md

流程：
0. 标准化输入（含病名、症状、否定症状、检查、影像、病程、年龄、特殊状态）
1. 本地候选病多轨召回（病名轨/症状轨/检查轨/风险轨）
2. 代码多维评分 + diagnostic_criteria 结构化比对 + 否定症状扣分
3. 决策：Tier1（精确病名 + 矛盾检查）→ Tier2（代码评分）→ LLM 语义匹配兜底 + 代码校验
4. 搜索兜底：本地无匹配 / 低置信 → 在线查询（标记 NEED_EXTERNAL_SEARCH + LOW_CONFIDENCE）
5. 追问机制：信息不足 → 生成追问 → 补充后重入
6. 输出 Top 1-3 + structured_info + diagnosis_status + 主病/兼病/并发症/诊断关系

核心原则：
- LLM 是本地候选的语义匹配器，不是自由诊断者
- 代码负责召回、标准化、校验、排序、安全拦截
- 本地匹配不上才低置信兜底在线查询
- M1 只输出西医诊断和证据链，不出中医辨证/处方
- LLM 输出必须经过代码二次校验，未通过不得进入 Top1
- 否定症状必须参与评分扣减
- REQUEST_MORE_INFO 不得直接进入 M2
"""

import json
import os
import re
import hashlib
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from types import SimpleNamespace


# ── 数据类 ──────────────────────────────────────────────

@dataclass
class NormalizedInput:
    """标准化患者输入（M1 v7 — Skill v2.1 完整字段）"""
    patient_mentioned_disease: str = ""
    known_diagnosis: str = ""
    chief_complaint: str = ""
    symptoms: List[str] = field(default_factory=list)
    negative_findings: List[str] = field(default_factory=list)
    signs: List[str] = field(default_factory=list)
    labs: List[str] = field(default_factory=list)
    imaging: List[str] = field(default_factory=list)
    pathology: List[str] = field(default_factory=list)
    duration: str = ""
    onset: str = ""
    severity: str = ""
    location: str = ""
    trigger_relief: str = ""
    age: str = ""
    sex: str = ""
    past_history: List[str] = field(default_factory=list)
    current_medications: List[str] = field(default_factory=list)
    special_status: List[str] = field(default_factory=list)


@dataclass
class CandidateInfo:
    """候选疾病信息（给 LLM 的输入）"""
    disease_name: str
    disease_name_cn: str
    diagnostic_criteria: List[str]
    typical_symptoms: List[str]
    differential_diagnosis: List[str]
    icd11_code: str = ""


class DiagnosisResultDict(dict):
    """Dict result with legacy attribute access for older tests/integrations."""
    @property
    def matched_disease(self):
        diagnoses = self.get("diagnosis_calibration", {}).get("calibrated_diagnosis", [])
        disease_name = (diagnoses[0] if diagnoses else
                        self.get("primary_formula_entry_disease", "") or
                        self.get("primary_diagnosis", ""))
        return SimpleNamespace(disease_name=disease_name, overall_score=1.0 if disease_name else 0.0)


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

        # ── 诊断卡片库（已合并入 db，保留独立索引供精确查询） ──
        self.diagnostic_cards: Dict[str, dict] = {}
        cards_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "m1_diagnostic_cards.json")
        if os.path.exists(cards_path):
            try:
                with open(cards_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for card in raw.get("diagnostic_cards", []):
                    cn = card.get("diseaseName_cn", "").strip()
                    name = card.get("disease_name", "").strip()
                    if cn:
                        self.diagnostic_cards[cn] = card
                    if cn and name:
                        self.diagnostic_cards[f"{cn} ({name})"] = card
            except Exception:
                pass

        # ── 索引 ──
        self.name_index: Dict[str, dict] = {}       # 英文名(小写) → 条目
        self.cn_name_index: Dict[str, dict] = {}     # 中文名 → 条目
        self.keyword_index: Dict[str, List[dict]] = {}  # 关键词 → 条目列表

        # 疾病名称列表（供 LLM 病名轨使用）
        self.disease_names_list: List[str] = []

        # ── 系统索引（疾病→系统）──
        self.system_index: Dict[str, str] = {}

        # ── 诊断标准补齐配置 ──
        # 当 LLM 诊断到某疾病但该疾病缺少 diagnostic_criteria 时，
        # 自动让 LLM 查询循证医学网站并缓存结果
        self.criteria_fill_enabled = True
        self.criteria_fill_cache_path = os.path.join(script_dir, "data", "disease_cache", "runtime_cache.json")
        self.criteria_fill_cache: Dict[str, dict] = {}
        if os.path.exists(self.criteria_fill_cache_path):
            try:
                with open(self.criteria_fill_cache_path, "r", encoding="utf-8") as f:
                    self.criteria_fill_cache = json.load(f)
            except Exception:
                self.criteria_fill_cache = {}
        # 已补齐过的疾病集合（避免重复查询）
        self.criteria_filled_set: set = set()
        # 加载已补齐记录
        self.criteria_filled_log_path = os.path.join(script_dir, "data", "disease_cache", "criteria_filled_log.json")
        if os.path.exists(self.criteria_filled_log_path):
            try:
                with open(self.criteria_filled_log_path, "r", encoding="utf-8") as f:
                    self.criteria_filled_set = set(json.load(f))
            except Exception:
                self.criteria_filled_set = set()

        for entry in self.db:
            name = entry["disease_name"].lower()
            self.name_index[name] = entry
            cn = entry.get("diseaseName_cn", "").strip().lower()
            if cn:
                self.cn_name_index[cn] = entry
            # 关键词索引（英文）
            for word in re.split(r'[\s()/,]+', entry["disease_name"].lower()):
                if len(word) > 2:
                    self.keyword_index.setdefault(word, []).append(entry)
            # 疾病名称列表
            display = f'{entry.get("diseaseName_cn", "")} ({entry["disease_name"]})'
            self.disease_names_list.append(display)

        # ── 补充：中文症状词索引（从 typical_symptoms 提取） ──
        for entry in self.db:
            cn = entry.get("diseaseName_cn", "").strip().lower()
            if cn:
                for ch_word in re.split(r'[\s,，、()（）]+', cn):
                    ch_clean = ch_word.strip()
                    if len(ch_clean) >= 2:
                        self.keyword_index.setdefault(ch_clean, []).append(entry)
                if len(cn) >= 2:
                    self.keyword_index.setdefault(cn[:2], []).append(entry)
                    self.keyword_index.setdefault(cn[:3] if len(cn) >= 3 else cn, []).append(entry)
            for symp in entry.get("typical_symptoms", []):
                symp_clean = symp.strip().lower()
                if len(symp_clean) >= 2:
                    self.keyword_index.setdefault(symp_clean, []).append(entry)
                    if len(symp_clean) >= 4:
                        self.keyword_index.setdefault(symp_clean[:4], []).append(entry)
            for crit in entry.get("diagnostic_criteria", []):
                crit_clean = crit.strip().lower()
                if len(crit_clean) >= 4:
                    self.keyword_index.setdefault(crit_clean[:4], []).append(entry)

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
        self.alias_to_standard_cn: Dict[str, str] = {}
        self._load_alias_maps()

    # ══════════════════════════════════════════════════════
    #  输入标准化
    # ══════════════════════════════════════════════════════

    def _load_alias_maps(self) -> None:
        """Load local alias/name maps for recall normalization only."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alias_paths = [
            os.path.join(script_dir, "m1_alias_map.json"),
            os.path.join(script_dir, "data", "m1_disease_alias_map.json"),
        ]
        for path in alias_paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if isinstance(value, str):
                    self.alias_to_standard_cn[key.strip().lower()] = value.strip()
                elif isinstance(value, dict):
                    canonical = value.get("canonical_disease_name", "")
                    standard_cn = ""
                    for entry in self.db:
                        if entry.get("disease_name", "").lower() == str(canonical).lower():
                            standard_cn = entry.get("diseaseName_cn", "")
                            break
                    if standard_cn:
                        self.alias_to_standard_cn[key.strip().lower()] = standard_cn
                        for alias in value.get("aliases", []):
                            if isinstance(alias, str):
                                self.alias_to_standard_cn[alias.strip().lower()] = standard_cn

        # 补充：口语化/俗名映射（与 _recall_candidates 中的 slang_map 同步）
        _slang_supplement = {
            "寻麻疹": "荨麻疹", "过敏性寻麻疹": "荨麻疹", "过敏性荨麻疹": "荨麻疹",
            "慢性寻麻疹": "慢性荨麻疹", "咳嗽病": "咳嗽",
        }
        for _k, _v in _slang_supplement.items():
            if _k not in self.alias_to_standard_cn:
                self.alias_to_standard_cn[_k] = _v

    def standardize_disease_name(self, query: str) -> Dict:
        """Normalize a disease query to a local M1 disease name when possible."""
        q = (query or "").strip()
        q_lower = q.lower()
        mapped = self.alias_to_standard_cn.get(q_lower, q)
        mapped_lower = mapped.lower()
        entry = self.cn_name_index.get(mapped_lower) or self.name_index.get(mapped_lower)
        if not entry:
            for item in self.db:
                cn = item.get("diseaseName_cn", "").strip()
                en = item.get("disease_name", "").strip()
                # 精确匹配或长字符串的包含匹配（>=3字避免症状词误匹配疾病名）
                if mapped and len(mapped) >= 3:
                    if mapped in cn or cn in mapped or mapped.lower() in en.lower() or en.lower() in mapped.lower():
                        entry = item
                        break
        return {
            "input": q,
            "standard_name": entry.get("diseaseName_cn", mapped) if entry else mapped,
            "western_name": entry.get("disease_name", "") if entry else "",
            "in_local_kb": bool(entry),
            "source": "alias_or_local_index" if entry else "unmapped",
        }

    def normalize_input(self, raw: Dict) -> NormalizedInput:
        """标准化患者输入（M1 v7 — Skill v2.1）"""
        if isinstance(raw, str):
            raw = {"chief_complaint": raw, "symptoms": [raw]}
        elif isinstance(raw, list):
            raw = {"chief_complaint": raw[0] if raw else "", "symptoms": raw}
        elif raw is None:
            raw = {}

        ni = NormalizedInput()

        mentioned = raw.get("patient_mentioned_disease", "")
        if mentioned:
            ni.patient_mentioned_disease = self.standardize_disease_name(mentioned)["standard_name"]
        else:
            ni.patient_mentioned_disease = ""

        ni.known_diagnosis = raw.get("known_diagnosis", "")

        chief = raw.get("chief_complaint", "")
        if isinstance(chief, list):
            chief = chief[0] if chief else ""
        ni.chief_complaint = chief

        for key in ["symptoms", "signs", "labs", "imaging", "negative_findings", "pathology"]:
            vals = raw.get(key, [])
            if isinstance(vals, str):
                vals = [vals]
            setattr(ni, key, [normalize_text(v) for v in vals if isinstance(v, str) and v.strip()])

        for key in ["duration", "onset", "severity", "location", "trigger_relief", "age", "sex"]:
            setattr(ni, key, raw.get(key, ""))

        # 列表字段
        for key in ["past_history", "current_medications", "special_status"]:
            vals = raw.get(key, [])
            if isinstance(vals, str):
                vals = [vals]
            setattr(ni, key, [v.strip() for v in vals if isinstance(v, str) and v.strip()])

        if not ni.chief_complaint and ni.symptoms:
            ni.chief_complaint = ni.symptoms[0]

        return ni

    # ══════════════════════════════════════════════════════
    #  症状轨：代码检索

    def _fill_missing_criteria(self, disease_cn_name: str, disease_en_name: str = "") -> bool:
        """即时补齐单个疾病的诊断标准

        当 M1 诊断出某疾病后，发现该疾病在 diseases_core.json 中缺少
        diagnostic_criteria / typical_symptoms / differential_diagnosis 时，
        调用 LLM 查询默沙东/MSD Manual 等循证医学网站并缓存回数据库。

        规则：
        - 只查未补齐过的疾病
        - LLM 只能搜索网上循证医学来源（默沙东、pubmed）
        - 结果写回 diseases_core.json 和 runtime_cache.json
        - 记录到 criteria_filled_log.json 避免重复查询
        """
        cache_key = disease_cn_name or disease_en_name
        if not cache_key or cache_key in self.criteria_filled_set:
            return False

        if cache_key in self.criteria_fill_cache:
            cached = self.criteria_fill_cache[cache_key]
            if cached.get("diagnostic_criteria") or cached.get("diagnostic_key_points"):
                self.criteria_filled_set.add(cache_key)
                self._save_criteria_filled_log()
                return True

        target_entry = None
        for entry in self.db:
            cn = entry.get("diseaseName_cn", "").strip()
            en = entry.get("disease_name", "").strip()
            if (disease_cn_name and cn == disease_cn_name) or                (disease_en_name and en.lower() == disease_en_name.lower()) or                (disease_cn_name and disease_cn_name in cn):
                target_entry = entry
                break

        if target_entry is None:
            return False

        if target_entry.get("diagnostic_criteria") or target_entry.get("typical_symptoms"):
            return False

        disease_name_display = target_entry.get("diseaseName_cn", target_entry.get("disease_name", ""))
        prompt_text = """你是一个医学疾病知识查询助手。你的任务是查找疾病 "%s" 的西医诊断标准。

## 规则（严格遵循）
1. 你只能基于默沙东诊疗手册（MSD Manuals, https://www.msdmanuals.com/）、PubMed、CDC、WHO、NICE、UpToDate 等循证医学来源中的信息进行回答。
2. 严禁使用中医来源（如"中医世家"、"方剂学"、"针灸"等）。
3. 严禁编造诊断标准。如果找不到可靠信息，请如实说明。
4. 只能查该疾病的【西医诊断标准】，不涉及中医辨证。
5. 输出必须是严格的 JSON 格式，不得输出 Markdown。

## 输出格式
{
  "diagnostic_criteria": ["诊断标准1（简明扼要）", "诊断标准2"],
  "typical_symptoms": ["典型症状1", "典型症状2"],
  "differential_diagnosis": ["鉴别诊断病名1", "鉴别诊断病名2"],
  "source_urls": ["来源1的URL", "来源2的URL"]
}

如果确实找不到可靠信息，输出：{"diagnostic_criteria": [], "typical_symptoms": [], "differential_diagnosis": [], "source_urls": []}"""
        prompt = prompt_text % disease_name_display

        raw = self._call_llm(prompt, temperature=0.1, max_tokens=2000, task_type="prompt_audit")
        if not raw:
            return False

        import re
        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                result = json.loads(m.group())
            else:
                return False
        except (json.JSONDecodeError, AttributeError):
            return False

        has_criteria = result.get("diagnostic_criteria") and len(result["diagnostic_criteria"]) > 0
        has_symptoms = result.get("typical_symptoms") and len(result["typical_symptoms"]) > 0
        has_diff = result.get("differential_diagnosis") and len(result["differential_diagnosis"]) > 0

        if not (has_criteria or has_symptoms):
            return False

        if has_criteria:
            target_entry["diagnostic_criteria"] = result["diagnostic_criteria"]
        if has_symptoms:
            target_entry["typical_symptoms"] = result["typical_symptoms"]
        if has_diff:
            target_entry["differential_diagnosis"] = result["differential_diagnosis"]

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(script_dir, "diseases_core.json")
            with open(db_path, 'w', encoding='utf-8') as f:
                json.dump(self.db, f, ensure_ascii=False, indent=4)
        except Exception:
            self.criteria_filled_set.add(cache_key)
            return False

        import time
        self.criteria_fill_cache[cache_key] = {
            "disease_name": disease_name_display,
            "diagnostic_criteria": result.get("diagnostic_criteria", []),
            "typical_symptoms": result.get("typical_symptoms", []),
            "differential_diagnosis": result.get("differential_diagnosis", []),
            "source_urls": result.get("source_urls", []),
            "status": "llm_filled_on_demand",
            "filled_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(self.criteria_fill_cache_path, 'w', encoding='utf-8') as f:
            json.dump(self.criteria_fill_cache, f, ensure_ascii=False, indent=2)

        self.criteria_filled_set.add(cache_key)
        self._save_criteria_filled_log()
        print(f"[M1] 诊断标准补齐: {disease_name_display} ({len(result.get('diagnostic_criteria', []))}条标准)")
        return True

    def _save_criteria_filled_log(self):
        """保存补齐日志"""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            log_path = os.path.join(script_dir, "data", "disease_cache", "criteria_filled_log.json")
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(sorted(list(self.criteria_filled_set)), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _keyword_fallback(self, mentioned: str, symptoms: List[str],
                           known_diagnosis: str = "") -> List[str]:
        """代码兜底：当 LLM 不可用时，用关键词+别名映射+典型症状匹配搜索匹配疾病

        策略（按优先级）：
        1. 口语化病名→标准名映射（slang_map）
        2. 中文病名子串匹配
        3. 典型症状与患者症状的交叉匹配评分
        """
        slang_map = {
            "外眼睑炎": "睑缘炎", "眼睛发炎": "结膜炎", "角膜炎": "角膜炎",
            "麦粒肿": "睑腺炎", "针眼": "睑腺炎", "白内障": "白内障",
            "青光眼": "青光眼", "老花眼": "老花眼", "糖尿病": "糖尿病",
            "高血压": "高血压", "感冒": "上呼吸道感染", "拉肚子": "腹泻",
            "发烧": "发热", "寻麻疹": "荨麻疹", "过敏性寻麻疹": "荨麻疹",
            "过敏性荨麻疹": "荨麻疹", "慢性寻麻疹": "慢性荨麻疹",
            "咳嗽病": "咳嗽", "抽动症": "抽动障碍", "多动症": "注意缺陷多动障碍",
            "食道反流": "反流性食管炎", "食管反流": "反流性食管炎", 
            "食道返流": "反流性食管炎", "食管返流": "反流性食管炎",
            "胃食管反流": "反流性食管炎", "返酸": "反流性食管炎",
        }

        # 构建搜索文本
        search_text = (mentioned or "") + " " + " ".join(symptoms or [])
        search_lower = search_text.lower()

        nasal_terms = ["鼻痒", "喷嚏", "打喷嚏", "清涕", "流清涕", "流鼻涕", "鼻塞", "遇冷"]
        if any(term in search_text for term in nasal_terms):
            if "发热" not in search_text and "高热" not in search_text:
                return ["变应性鼻炎", "慢性鼻炎", "急性鼻炎"]

        lower_airway_terms = ["咳痰", "黄痰", "白痰", "气促", "喘", "呼吸困难", "胸闷", "发热", "肺部", "啰音"]
        def _positive_term(term: str) -> bool:
            if term not in search_text:
                return False
            return not any(prefix + term in search_text for prefix in ["无", "未见", "没有", "否认"])
        if "咳嗽" in search_text and not any(_positive_term(term) for term in lower_airway_terms):
            return ["喉源性咳嗽", "急性上呼吸道感染"]

        # 别名映射匹配（精确命中直接返回）
        for slang, standard in slang_map.items():
            if slang in search_lower:
                return [standard]

        scored = []
        for entry in self.db:
            cn = entry.get("diseaseName_cn", "").strip()
            en = entry.get("disease_name", "").strip()
            cn_part = cn.split(" (")[0].split("（")[0].strip().lower()
            if not cn_part:
                continue

            score = 0
            
            # 策略1：中文病名子串匹配（精确匹配 > 子串包含）
            if mentioned:
                if cn_part == mentioned.lower():
                    score += 20
                elif cn_part in mentioned.lower() or mentioned.lower() in cn_part:
                    score += 10

            # 策略2：典型症状匹配
            typical = entry.get("typical_symptoms", [])
            for ts in typical:
                ts_lower = ts.strip().lower()
                if ts_lower and ts_lower in search_lower:
                    score += 3
                # 也检查患者症状是否在典型症状中
                for s in symptoms:
                    s_lower = s.strip().lower()
                    if s_lower and len(s_lower) >= 2 and s_lower in ts_lower:
                        score += 2

            # 策略3：诊断标准匹配
            criteria = entry.get("diagnostic_criteria", [])
            for c in criteria:
                c_lower = c.strip().lower()
                for s in symptoms:
                    s_lower = s.strip().lower()
                    if s_lower and len(s_lower) >= 2 and s_lower in c_lower:
                        score += 1

            # 策略4：英文名匹配（当有英文症状词时）
            en_lower = en.lower()
            for s in symptoms:
                s_lower = s.strip().lower()
                if s_lower and len(s_lower) >= 3 and s_lower in en_lower:
                    score += 2

            if score > 0:
                display = entry.get("diseaseName_cn", entry["disease_name"])
                # 精确匹配标记（用于排序）
                exact_match = mentioned and cn_part == mentioned.lower()
                scored.append((score, display, exact_match))

        # 按分数降序排列，分数相同时精确匹配优先
        scored.sort(key=lambda x: (-x[0], 0 if x[2] else 1, x[1]))

        if scored:
            # 如果最高分有明显的领先优势（>= 5分差距），只取高分群
            top_score = scored[0][0]
            filtered = [s[1] for s in scored if s[0] >= top_score - 3]
        else:
            filtered = []

        return filtered[:3]

    def _parse_disease_name(self, line: str) -> Optional[str]:
        """解析疾病名称行，返回标准英文名"""
        line = line.replace("（", "(").replace("）", ")")
        # 格式1：中文名 (英文名)
        match = re.match(r'(.+?)\s*[（(](.+?)[）)]', line)
        if match:
            en = match.group(2).strip().lower()
            if en in self.name_index:
                return en
            cn = match.group(1).strip().lower()
            if cn in self.cn_name_index:
                return self.cn_name_index[cn]["disease_name"]
            # 中文名 + 英文名一起查（如 "睑缘炎 (blepharitis)"）
            combined = f"{cn} ({en})"
            if combined in self.name_index:
                return en

        line_lower = line.strip().lower()
        # 格式2：直接是英文名
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

    def llm_api_available(self) -> bool:
        """检查 LLM API 是否可用"""
        return bool(self.deepseek_api_key or self.gemini_api_key)

    def _call_llm(self, prompt: str, temperature: float = 0.1, max_tokens: int = 500,
                   task_type: str = "simple_explain") -> Optional[str]:
        """调用 LLM（支持自动路由）"""
        if self.llm_provider == "deepseek" and self.deepseek_api_key:
            return self._call_deepseek(prompt, temperature, max_tokens, task_type)
        elif self.llm_provider == "gemini" and self.gemini_api_key:
            return self._call_gemini(prompt, temperature, max_tokens)
        return None

    def _call_deepseek(self, prompt: str, temperature: float, max_tokens: int,
                       task_type: str = "simple_explain") -> Optional[str]:
        """调用 DeepSeek API（通过自动路由选择模型）

        简单任务 → flash，复杂任务 → pro，疑难任务 → pro + reasoning。
        """
        try:
            from deepseek_router import get_router
            router = get_router()
            payload = router.build_payload(
                prompt, task_type,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt="你是医学诊断专家。严格遵循用户指示输出，不添加多余内容。",
            )
            import requests
            headers = {
                "Authorization": f"Bearer {self.deepseek_api_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.deepseek_api_base}/v1/chat/completions"
            model_info = router.get_model_info(task_type)
            print(f"[DS_ROUTER] task={task_type} model={model_info['model']} reasoning={model_info['use_reasoning']}")
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                # V4 Preview thinking 模式下 content 可能为空，
                # 实际输出在 reasoning_content 中
                content = msg.get("content", "").strip()
                if not content:
                    content = msg.get("reasoning_content", "").strip()
                return content if content else None
        except Exception as _e:
            print(f"[LLM_ERR] DeepSeek API call failed: {_e}")
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
        except Exception as _e:
            print(f"[LLM_ERR] Gemini API call failed: {_e}")
            return None
        return None

    # ══════════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════════


    # ══════════════════════════════════════════════════════
    #  Agent 工具1：查询本地诊断标准
    # ══════════════════════════════════════════════════════

    def _query_local_criteria(self, disease_name: str) -> Dict:
        """工具1：查询本地诊断标准

        输入：疾病名称（中文/英文/中英混合）
        输出：诊断标准、典型症状、鉴别诊断
        若本地无该疾病条目 → 返回 {"status": "NOT_FOUND_IN_LOCAL_DB"}
        """
        name_clean = disease_name.strip().lower()

        # 匹配策略
        candidates_to_check = []

        # 1. 精确匹配中文名
        for entry in self.db:
            cn = entry.get("diseaseName_cn", "").strip().lower()
            if cn and (cn == name_clean or name_clean in cn):
                candidates_to_check.append(entry)

        # 2. 精确匹配英文名
        if not candidates_to_check:
            if name_clean in self.name_index:
                candidates_to_check.append(self.name_index[name_clean])

        # 3. 部分匹配中文名
        if not candidates_to_check:
            for entry in self.db:
                cn = entry.get("diseaseName_cn", "").strip().lower()
                if cn and (cn[:4] in name_clean or name_clean[:4] in cn):
                    candidates_to_check.append(entry)
                    break

        if not candidates_to_check:
            return {"status": "NOT_FOUND_IN_LOCAL_DB"}

        # 取最匹配的
        entry = candidates_to_check[0]
        result = {
            "status": "FOUND",
            "diseaseName_cn": entry.get("diseaseName_cn", ""),
            "disease_name": entry.get("disease_name", ""),
            "diagnostic_criteria": entry.get("diagnostic_criteria", []),
            "typical_symptoms": entry.get("typical_symptoms", []),
            "differential_diagnosis": entry.get("differential_diagnosis", []),
        }
        # 如果本地有诊断卡片，优先从卡片补充更详细的数据
        cn_full = entry.get("diseaseName_cn", "").strip()
        card = self.diagnostic_cards.get(cn_full)
        if not card:
            card = self.diagnostic_cards.get(f'{cn_full} ({entry.get("disease_name", "")})')
        if card:
            card_diag = card.get("diagnostic_criteria", [])
            if card_diag and (not result["diagnostic_criteria"] or len(card_diag) > len(result["diagnostic_criteria"])):
                result["diagnostic_criteria"] = card_diag
            card_symp = card.get("typical_symptoms", [])
            if card_symp and (not result["typical_symptoms"] or len(card_symp) > len(result["typical_symptoms"])):
                result["typical_symptoms"] = card_symp
            card_diff = card.get("differential_diagnosis", [])
            if card_diff and (not result["differential_diagnosis"] or len(card_diff) > len(result["differential_diagnosis"])):
                result["differential_diagnosis"] = card_diff

        return result

    # ══════════════════════════════════════════════════════
    #  Agent 工具2：在线查询默沙东/PubMed + 自动缓存
    # ══════════════════════════════════════════════════════

    def _online_query(self, disease_name: str, source: str = "msd") -> Dict:
        """工具2：在线查询权威信源获取最新诊断标准

        输入：疾病名称（中文/英文）+ 检索源（默沙东/PubMed）
        输出：诊断标准摘要（来自权威信源）
        注意：结果自动缓存入本地 knowledge_cache，下次优先使用

        缓存规则：
        - 缓存键：disease_name + source
        - 有效期：6 个月
        - 每次查询时检查缓存是否过期
        """
        cache_key = f"{disease_name.strip()}|{source.lower()}"
        now = time.time()

        # ── 检查缓存（6个月有效期） ──
        if hasattr(self, '_online_cache') and cache_key in self._online_cache:
            cached = self._online_cache[cache_key]
            if now - cached.get("cached_at", 0) < 180 * 24 * 3600:  # ~6个月
                return cached["data"]

        # ── 调用 LLM 在线查询 ──
        source_display = {"msd": "默沙东诊疗手册", "pubmed": "PubMed"}.get(source.lower(), source)
        prompt = f"""你是一个医学知识查询助手。请在以下循证医学来源中查找疾病 "{disease_name}" 的最新诊断标准：

## 检索源
{source_display}

## 规则（严格遵循）
1. 只能基于真实、权威的循证医学来源回答
2. 严禁编造诊断标准。如果找不到可靠信息，如实说明
3. 输出严格的 JSON 格式

## 输出格式
{{
  "diagnostic_criteria": ["诊断标准1", "诊断标准2"],
  "typical_symptoms": ["典型症状1", "典型症状2"],
  "differential_diagnosis": ["鉴别诊断1", "鉴别诊断2"],
  "source_url": "来源URL（如果知道）",
  "confidence": "high/medium/low"
}}

如果找不到可靠信息：{{"diagnostic_criteria": [], "typical_symptoms": [], "differential_diagnosis": [], "source_url": "", "confidence": "not_found"}}"""

        result_text = self._call_llm(prompt, temperature=0.1, max_tokens=2000, task_type="medical_judgment")
        if not result_text:
            return {"status": "QUERY_FAILED", "disease_name": disease_name, "error": "LLM 无返回"}

        try:
            m = re.search(r'\{.*\}', result_text, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
            else:
                return {"status": "QUERY_FAILED", "disease_name": disease_name, "error": "无法解析 LLM 输出"}
        except Exception:
            return {"status": "QUERY_FAILED", "disease_name": disease_name, "error": "JSON 解析失败"}

        has_data = bool(parsed.get("diagnostic_criteria") or parsed.get("typical_symptoms"))
        if not has_data and parsed.get("confidence") != "not_found":
            return {"status": "QUERY_FAILED", "disease_name": disease_name, "error": "未从权威信源找到该疾病的诊断标准"}

        # ── 缓存结果 ──
        result = {
            "status": "FOUND" if has_data else "NOT_FOUND",
            "disease_name": disease_name,
            "source": source_display,
            "source_url": parsed.get("source_url", ""),
            "diagnostic_criteria": parsed.get("diagnostic_criteria", []),
            "typical_symptoms": parsed.get("typical_symptoms", []),
            "differential_diagnosis": parsed.get("differential_diagnosis", []),
            "confidence": parsed.get("confidence", "low"),
        }

        # 存入缓存
        if not hasattr(self, '_online_cache'):
            self._online_cache = {}
        self._online_cache[cache_key] = {
            "data": result,
            "cached_at": now,
        }

        # ── 如果有数据，自动追加到本地知识库 ──
        if has_data:
            target_entry = None
            for entry in self.db:
                cn = entry.get("diseaseName_cn", "").strip()
                en = entry.get("disease_name", "").strip()
                if disease_name in cn or disease_name in en or cn in disease_name:
                    target_entry = entry
                    break
            if target_entry:
                if parsed.get("diagnostic_criteria") and not target_entry.get("diagnostic_criteria"):
                    target_entry["diagnostic_criteria"] = parsed["diagnostic_criteria"]
                if parsed.get("typical_symptoms") and not target_entry.get("typical_symptoms"):
                    target_entry["typical_symptoms"] = parsed["typical_symptoms"]
                if parsed.get("differential_diagnosis") and not target_entry.get("differential_diagnosis"):
                    target_entry["differential_diagnosis"] = parsed["differential_diagnosis"]
                try:
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    db_path = os.path.join(script_dir, "diseases_core.json")
                    with open(db_path, 'w', encoding='utf-8') as f:
                        json.dump(self.db, f, ensure_ascii=False, indent=4)
                except Exception:
                    pass

        return result

    def _get_cached_criteria(self, disease_name: str, source: str = "msd") -> Dict:
        """获取诊断标准：优先本地库（工具1），本地没有则在线查询（工具2）

        这是 Agent 的便捷入口，自动决定用哪个工具
        """
        # 先试工具1
        local = self._query_local_criteria(disease_name)
        if local["status"] == "FOUND":
            return local

        # 工具1没找到 → 工具2在线查询
        return self._online_query(disease_name, source)

    # ══════════════════════════════════════════════════════
    #  Agent 主推理流程
    # ══════════════════════════════════════════════════════

    def diagnose(self, raw_input: Dict, _followup_answers: Optional[Dict] = None) -> Dict:
        """主诊断入口（M1 v7 — 本地病名库优先 + 代码评分 + LLM 语义匹配器）

        流程（Skill v2.1）：
        0. 标准化输入
        1. 本地多轨召回候选
        2. 代码多维评分 + criteria 结构化比对
        3. 决策：
           Tier 1（明确病名精确匹配）→ 直接采用
           Tier 2（代码评分足够）→ code-first 输出
           Tier 3（LLM 语义匹配）→ LLM 在本地候选中做语义匹配
           Tier 4（本地无匹配）→ 在线查询兜底（标记 LOW_CONFIDENCE）
        4. 结构化信息提取（术后/转移/并发症/特殊人群）
        5. 输出 Top 1-3 + diagnosis_status + structured_info

        Returns:
            {
                "status": "DIAGNOSIS_READY" | "NEED_MORE_INFO",
                "diagnosis_calibration": {...},
                "diagnosis_status": "PASS" | "LOW_CONFIDENCE" | "REQUEST_MORE_INFO" | "NEED_EXTERNAL_SEARCH",
                "structured_info": {...},
                "external_search": {...},
                "m2_payload": {...},
            }
        """
        if isinstance(raw_input, str) and isinstance(_followup_answers, list):
            raw_input = {
                "patient_mentioned_disease": raw_input,
                "chief_complaint": raw_input,
                "symptoms": _followup_answers,
            }
            _followup_answers = None
        elif _followup_answers is not None and not isinstance(_followup_answers, dict):
            _followup_answers = None

        ni = self.normalize_input(raw_input)
        raw_for_output = raw_input if isinstance(raw_input, dict) else {
            "patient_mentioned_disease": ni.patient_mentioned_disease,
            "chief_complaint": ni.chief_complaint,
            "symptoms": ni.symptoms,
        }

        evidence_count = len([x for x in [ni.chief_complaint] + ni.symptoms + ni.signs + ni.labs + ni.imaging if x])
        if evidence_count <= 1 and not ni.patient_mentioned_disease and not ni.known_diagnosis:
            return self._build_m1_result(
                ni, [], [], [], "", "REQUEST_MORE_INFO",
                parsed_questions=["请补充主要症状、持续时间以及是否伴随发热/疼痛/呼吸或消化异常。"],
                parsed_need_info=["symptom_detail"],
                cannot_decide_because="症状信息不足，无法进行可靠西医诊断。",
            )

        if not self.llm_api_available():
            fallback = self._keyword_fallback(ni.patient_mentioned_disease or ni.known_diagnosis, ni.symptoms)
            status = "DIAGNOSIS_READY" if fallback else "NEED_MORE_INFO"
            diag_status = "PASS" if fallback else "REQUEST_MORE_INFO"
            primary = fallback[0] if fallback else ""

            # ── 儿童轻症咳嗽保护（回归 3）──
            # 当且仅当儿童、仅咳嗽、无明确病名时，降级为 LOW_CONFIDENCE
            if diag_status == "PASS" and primary.lower() in ("喉源性咳嗽", "急性上呼吸道感染", "咳嗽"):
                try:
                    age_val = int(ni.age.replace("岁", "").strip()) if ni.age else 999
                except (ValueError, AttributeError):
                    age_val = 999
                has_explicit_disease = bool(ni.patient_mentioned_disease or ni.known_diagnosis)
                if age_val < 14 and not has_explicit_disease and not any(
                    t in ni.chief_complaint.lower() for t in ["发热", "气促", "喘息", "痰", "检查", "ct", "胸片"]
                ):
                    diag_status = "LOW_CONFIDENCE"

            return self._build_m1_result(
                ni, [], [], fallback[:3] if fallback else [], "code_fallback", diag_status,
                raw_for_output=raw_for_output,
                scored_top=[],
            )

        # ── Step 1-2: 本地多轨召回 + 年龄性别硬筛 ──
        candidates = self._recall_candidates(ni)
        candidates = self._age_gender_hard_filter(candidates, ni)
        scored = self._score_candidates(candidates, ni)
        _scored_top = scored[:3] if scored else []
        _scored_diseases = [s["entry"].get("diseaseName_cn", "") for s in _scored_top]
        _scored_details = [s["detail"] for s in _scored_top]
        # 取 top confidence 用于 Tier 2 决策

        print(f"[M1_SCORE] 候选评分: {[(s['entry'].get('diseaseName_cn',''), s['score']) for s in scored[:5]]}")

        # ════════════════════════════════════════════════════════
        #  决策逻辑（Skill v2.1 Step 3-6）：
        #    Tier 1（快速通道）：精确病名匹配 → 直接采用
        #    Tier 2（代码评分）：多维评分 top-1 >= 45 且 gap >= 10 → 采用
        #    Tier 3（LLM 语义匹配器）：评分不足 → LLM 在本地候选中做语义匹配
        #    Tier 4（NEED_EXTERNAL_SEARCH）：本地无匹配 → 标记兜底
        # ════════════════════════════════════════════════════════
        cn_diagnoses = []
        source = ""
        diagnosis_status = "PASS"

        # Tier 1: 精确病名匹配（Fast path，带矛盾检查）
        _tier1_match = None
        _tier1_conflict = False
        if _scored_details and _scored_details[0].get("name_score", 0) >= 30:
            _tier1_match = _scored_diseases[0]
            # Fast path 矛盾检查（Skill v2.1 Step 5）
            _tier1_entry = _scored_top[0]["entry"] if _scored_top else None
            search_text = " ".join([ni.chief_complaint] + ni.symptoms + ni.signs).lower()
            # 检查1：症状词保护 - fast path 病名不能只是症状词
            _symptom_words = {"咳嗽", "咳痰", "发热", "腹痛", "腹泻", "头痛", "眩晕",
                             "皮疹", "水肿", "乏力", "消瘦", "纳差", "恶心", "呕吐",
                             "胸痛", "咽痛", "鼻塞", "流涕", "失眠"}
            if _tier1_match.lower() in _symptom_words:
                _tier1_conflict = True
                print(f"[M1_TIER1_WARN] 病名 '{_tier1_match}' 仅是症状词，怀疑冲突")
            # 检查2：患者说法不确定性
            _uncertainty = {"怀疑", "可能", "像是", "网上查", "感觉像", "好像", "不确定"}
            if any(kw in ni.patient_mentioned_disease.lower() for kw in _uncertainty):
                _tier1_conflict = True
                print(f"[M1_TIER1_WARN] 患者说法不确定，fast path 降级")
            # 检查3：否定症状与典型症状矛盾
            if ni.negative_findings and _tier1_entry:
                for symp in _tier1_entry.get("typical_symptoms", []):
                    symp_lower = symp.strip().lower()
                    for nf in ni.negative_findings:
                        nf_lower = nf.strip().lower()
                        nf_core = re.sub(r"^(无|没有|未|否认|不伴|未见|无明显|不)", "", nf_lower).strip()
                        if nf_core and (nf_core in symp_lower or symp_lower in nf_core):
                            _tier1_conflict = True
                            print(f"[M1_TIER1_WARN] 否定症状 '{nf_lower}' 与 '{symp_lower}' 矛盾")
                            break

            if not _tier1_conflict:
                print(f"[M1_TIER1] 患者明确提及病名且知识库完整匹配: {_tier1_match} → 直接采用")
            else:
                print(f"[M1_TIER1] 病名 '{_tier1_match}' 存在矛盾，降级至代码评分/LLM")

        if _tier1_match and not _tier1_conflict:
            cn_diagnoses = [_tier1_match]
            for s in _scored_top[1:3]:
                n = s["entry"].get("diseaseName_cn", "")
                if n and n != _tier1_match:
                    cn_diagnoses.append(n)
            source = "code_tier1_name_match"
            diagnosis_status = "PASS"
            print(f"[M1_TIER1_GUARD] Fast path 通过矛盾检查: {_tier1_match}")

        # Tier 2: 贝叶斯验证置信足够
        elif _scored_diseases:
            top_conf = _scored_top[0].get("confidence", "low") if _scored_top else "low"

            # ── 已知诊断强先验（Skill v2.3 Step 3.2）──
            # 如果已知诊断在 scored 中被 name_exact 匹配到且 confidence >= medium，
            # 则直接采用，不走 LLM（避免 LLM 被症状误导选择其他疾病）
            _known_diag_name = (ni.known_diagnosis or "").strip().lower()
            _known_diag_scored = None
            if _known_diag_name and _scored_top:
                for _s in _scored_top[:5]:
                    _scn = _s["entry"].get("diseaseName_cn", "").strip().lower()
                    if _scn == _known_diag_name or _known_diag_name in _scn or _scn in _known_diag_name:
                        _known_diag_scored = _s
                        break
            if _known_diag_scored:
                _kd_conf = _known_diag_scored.get("confidence", "low")
                _kd_name_exact = _known_diag_scored.get("detail", {}).get("name_score", 0) >= 30
                if _kd_conf in ("high", "medium") and _kd_name_exact:
                    # 已知诊断名精确匹配 + 贝叶斯置信 >= medium → 采用
                    cn_diagnoses = [_known_diag_scored["entry"].get("diseaseName_cn", "")]
                    # 补充其他 scored 疾病作为 secondary
                    for _s in _scored_top:
                        _n = _s["entry"].get("diseaseName_cn", "")
                        if _n and _n != cn_diagnoses[0] and len(cn_diagnoses) < 3:
                            cn_diagnoses.append(_n)
                    source = "code_bayesian_known_diagnosis"
                    diagnosis_status = "PASS" if _kd_conf == "high" else "LOW_CONFIDENCE"
                    print(f"[M1_KNOWN_DIAG_BAYES] 已知诊断贝叶斯命中: {cn_diagnoses[0]} (conf={_kd_conf})，跳过 LLM")
                elif top_conf in ("high",):
                    cn_diagnoses = _scored_diseases[:3]
                    source = "code_bayesian"
                    diagnosis_status = "PASS"
                    print(f"[M1_BAYESIAN] 贝叶斯验证置信={top_conf} → 直接采用")

            # Tier 3: LLM 语义匹配器（本地候选范围内）
            # 当以下情况时进入：
            #   A. 无已知诊断，且贝叶斯置信度不够 high → cn_diagnoses 为空，source 为空
            #   B. 有已知诊断但未通过强先验验证（已知诊断置信不足或未精确匹配）
            if not cn_diagnoses:
                print(f"[M1_BAYESIAN_NEED_LLM] 贝叶斯置信={top_conf} → LLM语义匹配")
                criteria_section = ""
                for i, s in enumerate(scored[:6]):
                    cand = s["entry"]
                    cn_name = cand.get("diseaseName_cn", "").strip()
                    en_name = cand.get("disease_name", "").strip()
                    diag = cand.get("diagnostic_criteria", [])
                    typical = cand.get("typical_symptoms", [])
                    diff = cand.get("differential_diagnosis", [])
                    name_display = f"{cn_name} ({en_name})" if en_name else cn_name
                    criteria_section += f"\n### 候选 {i+1}：{name_display}（置信={s.get('confidence', 'low')}，证据={len(s.get('evidence_for', []))}条）"
                    if diag:
                        criteria_section += "\n诊断标准：" + "；".join(diag[:5])
                    if typical:
                        criteria_section += "\n典型症状：" + "；".join(typical[:5])
                    if diff:
                        criteria_section += "\n需鉴别：" + "、".join(diff[:4])

                    followup_history = ""
                    if _followup_answers:
                        followup_history = "\n## 上一轮追问的答案"
                        for q, a in _followup_answers.items():
                            followup_history += "\n追问：" + str(q) + "\n答案：" + str(a)

                    # ── LLM 作为语义匹配器（Skill v2.1 Step 3.2）──
                    prompt = f"""你是一个疾病诊断语义匹配器。根据患者的完整资料，从下方本地候选疾病中选出最匹配的西医病名。

## 工作方式
你是"语义匹配器"不是"自由诊断者"。
你的职责是在已提供的本地候选疾病中做语义匹配和排序。
你只能从下方候选疾病中选择，不能创造新的病名。

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{'；'.join(ni.symptoms)}
- 否定症状：{'；'.join(ni.negative_findings)}
- 体征：{'；'.join(ni.signs)}
- 实验室：{'；'.join(ni.labs)}
- 影像学：{'；'.join(ni.imaging)}
- 患者提到的病名：{ni.patient_mentioned_disease}
- 已知诊断：{ni.known_diagnosis}
- 病程：{ni.duration}
- 起病方式：{ni.onset}
- 年龄：{ni.age}{followup_history}

## 本地候选疾病（均来自知识库，附代码评分供参考）
{criteria_section}

## 匹配原则
1. 【在本地候选范围内匹配】只能从下面的候选疾病中选择。不得在候选之外创造新病名。
2. 【候选可见原则】如果患者症状支持候选之一，就在选候选内选。如果所有候选都不匹配，输出空列表。
3. 【否定症状】不得将否定症状（无/无明显/未见）作为阳性证据。
4. 【症状词不得作为疾病名】咳嗽、发热、腹泻等是症状，不是疾病名，除非患者明确说"诊断为咳嗽"。
5. 【主诉优先】患者主诉和提到的病名优先。
6. 【置信度排序】按匹配程度从高到低输出 Top 1-3。

## 输出格式（严格 JSON，不要 Markdown）

### 模式一：信息足够，且在候选中有明确匹配
{{"status":"DIAGNOSIS_READY","diagnoses":[{{"name_cn":"病名","name_en":"","match_reason":"匹配理由（简述）"}}],"missing_info":[],"confidence":"high/medium/low"}}

### 模式二：信息不足，或所有候选都不匹配
{{"status":"NEED_MORE_INFO","current_top_candidates":[],"cannot_decide_because":"具体原因","questions":["追问1？","追问2？"],"missing_info":["标签"]}}"""

                    result_text = self._call_llm(prompt, temperature=0.1, max_tokens=2000, task_type="m1_diagnosis_error")
                    if not result_text:
                        cn_diagnoses = _scored_diseases[:3]
                        source = "code_scoring_llm_fallback"
                        diagnosis_status = "LOW_CONFIDENCE"
                        print("[M1_SCORE_LLM_FAIL] LLM 无返回，退回到代码评分结果")
                    else:
                        import json as _json
                        try:
                            m = re.search(r'\{.*\}', result_text, re.DOTALL)
                            if not m:
                                raise ValueError("no JSON")
                            parsed = _json.loads(m.group())
                        except Exception:
                            cn_diagnoses = _scored_diseases[:3]
                            source = "code_scoring_llm_fallback"
                            diagnosis_status = "LOW_CONFIDENCE"
                            print("[M1_SCORE_LLM_FAIL] LLM 输出解析失败，退回到代码评分结果")
                        else:
                            if parsed.get("status") == "NEED_MORE_INFO":
                                return self._build_m1_result(
                                    ni, candidates, scored, [], "llm_need_more_info", "REQUEST_MORE_INFO",
                                    parsed_questions=parsed.get("questions", []),
                                    parsed_need_info=parsed.get("missing_info", []),
                                    followup_answers=_followup_answers,
                                    raw_for_output=raw_for_output,
                                    scored_top=_scored_top,
                                    scored_details=_scored_details,
                                    scored_diseases=_scored_diseases,
                                    current_top_candidates=_scored_diseases[:3],
                                    cannot_decide_because=parsed.get("cannot_decide_because", ""),
                                )

                        # LLM 语义匹配结果
                        diagnoses_raw = parsed.get("diagnoses", [])
                        cn_diagnoses = []
                        for d in diagnoses_raw:
                            cn = d.get("name_cn", "").strip() if isinstance(d, dict) else str(d).strip()
                            if cn and cn not in cn_diagnoses:
                                full = cn
                                for e in self.db:
                                    ecn = e.get("diseaseName_cn", "").strip()
                                    if cn in ecn or ecn in cn or cn == e.get("disease_name", ""):
                                        full = ecn
                                        break
                                cn_diagnoses.append(full if full else cn)

                        if not cn_diagnoses:
                            cn_diagnoses = _scored_diseases[:3]
                            source = "code_scoring_llm_empty"
                            diagnosis_status = "LOW_CONFIDENCE"
                        else:
                            # ── LLM 输出代码校验（Skill v2.1 Step 4）──
                            validated, passed, reason = self._validate_llm_output(cn_diagnoses, scored, ni)
                            if not passed:
                                print(f"[M1_LLM_VALIDATE_FAIL] LLM 输出未通过代码校验: {reason}，回退至代码评分")
                                cn_diagnoses = _scored_diseases[:3]
                                source = "code_scoring_llm_validation_fail"
                                diagnosis_status = "LOW_CONFIDENCE"
                            else:
                                source = "llm_semantic_matcher_code_checked"
                                cn_diagnoses = validated[:3]
                                llm_conf = parsed.get("confidence", "medium")
                                diagnosis_status = "PASS" if llm_conf == "high" else "LOW_CONFIDENCE"

                # ── 已知诊断矛盾检查：LLM 输出不得替换已知诊断（P0-1）──
                # 如果已知诊断存在且 LLM 输出了不同的 primary，检查后决定是否保留
                _kd_name = (ni.known_diagnosis or "").strip().lower()
                if _kd_name and cn_diagnoses and source.startswith("llm"):
                    _kd_in_scored = False
                    for _s in _scored_top:
                        _scn = _s["entry"].get("diseaseName_cn", "").strip().lower()
                        if _scn == _kd_name or _kd_name in _scn or _scn in _kd_name:
                            _kd_in_scored = True
                            break
                    if _kd_in_scored:
                        # 检查是否属于可覆盖的情况
                        _can_override = bool(_tier1_conflict)  # Tier 1 冲突标记
                        if not _can_override:
                            _uncertainty_kw = {"疑似", "可能", "像是", "网上查", "感觉像", "好像", "不确定"}
                            if any(kw in ni.known_diagnosis.lower() for kw in _uncertainty_kw):
                                _can_override = True
                        if not _can_override:
                            # 已知诊断存在且未被标记为不确定，保留已知诊断作为 primary
                            _llm_primary = cn_diagnoses[0].lower() if cn_diagnoses else ""
                            _kd_in_diagnoses = any(
                                _kd_name == d.lower() or _kd_name in d.lower() or d.lower() in _kd_name
                                for d in cn_diagnoses
                            )
                            if not _kd_in_diagnoses:
                                # LLM primary 不同，保留已知诊断
                                _kd_full_name = next(
                                    (s["entry"].get("diseaseName_cn", ni.known_diagnosis)
                                     for s in _scored_top
                                     if _kd_name in s["entry"].get("diseaseName_cn", "").strip().lower()
                                     or s["entry"].get("diseaseName_cn", "").strip().lower() in _kd_name),
                                    ni.known_diagnosis
                                )
                                cn_diagnoses = [_kd_full_name] + cn_diagnoses
                                print(f"[M1_KNOWN_DIAG_CONTRADICT] 已知诊断 {_kd_full_name} 被 LLM 替换，已恢复为 primary")

        # Tier 4: 本地无候选，或患者提到的病名不在本地库中 → 触发外部搜索
        _need_external_search = False
        if not cn_diagnoses and not _scored_diseases:
            _need_external_search = True
        elif diagnosis_status not in ("PASS",):
            # 检查患者提到的病名是否在本地库中精确存在
            _mention = (ni.patient_mentioned_disease or ni.known_diagnosis or "").strip()
            if _mention:
                _mention_clean = _mention.split("（")[0].split("(")[0].strip().lower()
                _in_db = False
                for entry in self.db:
                    cn = entry.get("diseaseName_cn", "").strip().lower()
                    en = entry.get("disease_name", "").lower()
                    if cn == _mention_clean or en == _mention_clean:
                        _in_db = True
                        break
                if not _in_db:
                    # 病名不在本地库中 → 需要外部查询
                    _need_external_search = True
                    if not source.startswith("external"):
                        source = "external_search"
                    diagnosis_status = "NEED_EXTERNAL_SEARCH"

        if _need_external_search:
            search_name = ni.patient_mentioned_disease or ni.known_diagnosis or ni.chief_complaint
            if search_name:
                external = self._online_query(search_name)
                if external.get("status") == "FOUND":
                    if not cn_diagnoses:
                        cn_diagnoses = [search_name]
                    source = "external_search"
                    diagnosis_status = "NEED_EXTERNAL_SEARCH"

        if cn_diagnoses and self.criteria_fill_enabled:
            # 仅在明确 PASS + 确认本地库缺标准时才补（Skill v2.1 Step 8）
            if diagnosis_status == "PASS" and source not in ("external_search", "external_search_no_pass"):
                for diag in cn_diagnoses:
                    self._fill_missing_criteria(diag.split(" (")[0].split("（")[0].strip())
            else:
                print("[M1_CRITERIA_FILL_SKIP] 非 PASS 状态，跳过 criteria_fill")

        return self._build_m1_result(
            ni, candidates, scored, cn_diagnoses, source, diagnosis_status,
            followup_answers=_followup_answers,
            raw_for_output=raw_for_output,
            scored_top=_scored_top,
            scored_details=_scored_details,
            scored_diseases=_scored_diseases,
        )

    # ══════════════════════════════════════════════════════
    #  年龄/性别硬筛（Skill v2.3 Step 2）
    # ══════════════════════════════════════════════════════

    def _age_gender_hard_filter(self, candidates: List[dict], ni) -> List[dict]:
        """年龄和性别是硬边界，不是普通加分项。

        规则：
        - 成人（≥14）不得匹配儿科专属病名；
        - 儿童（<14）保留儿科相关候选；
        - 男性不得出现妊娠相关诊断；
        - 女性不得出现男性专属诊断；
        - 孕期标记 pregnancy_risk。

        Returns:
            过滤后的候选列表
        """
        if not candidates:
            return []

        age_val = None
        if ni.age:
            try:
                age_val = int(ni.age.replace("岁", "").strip())
            except (ValueError, AttributeError):
                pass

        is_child = age_val is not None and age_val < 14
        is_female = ni.sex and ni.sex in ("女", "女性", "female", "F")
        is_male = ni.sex and ni.sex in ("男", "男性", "male", "M")

        # 儿科专属病名关键词（本地库中儿科病名的标记）
        _pediatric_keywords = ["儿童", "小儿", "新生儿", "婴儿", "幼年", "早产"]

        # 妊娠相关关键词
        _pregnancy_keywords = ["妊娠", "孕期", "产科", "分娩", "先兆流产", "宫外孕",
                               "子痫", "前置胎盘", "胎膜", "羊水", "产后",
                               "妊娠期", "哺乳期", "孕", "葡萄胎"]

        # 男性专属关键词
        _male_only_keywords = ["前列腺", "睾丸", "精囊", "阴茎", "阴囊", "包皮",
                               "精索", "输精管", "射精", "勃起", "男性不育"]

        filtered = []
        for entry in candidates:
            cn = entry.get("diseaseName_cn", "").strip().lower()
            en = entry.get("disease_name", "").strip().lower()
            system = entry.get("system", "")

            excluded = False

            # 规则1：成人不得匹配儿科专属病名
            if age_val is not None and not is_child:
                if any(kw in cn for kw in _pediatric_keywords):
                    print(f"[M1_AGE_FILTER] 成人 {age_val} 岁，排除儿科病名: {entry.get('diseaseName_cn','')}")
                    excluded = True

            # 规则2：男性不得出现妊娠相关诊断
            if is_male:
                if any(kw in cn for kw in _pregnancy_keywords):
                    print(f"[M1_GENDER_FILTER] 男性，排除妊娠相关: {entry.get('diseaseName_cn','')}")
                    excluded = True

            # 规则3：女性不得出现男性专属诊断
            if is_female:
                if any(kw in cn for kw in _male_only_keywords):
                    print(f"[M1_GENDER_FILTER] 女性，排除男性专属: {entry.get('diseaseName_cn','')}")
                    excluded = True

            if not excluded:
                filtered.append(entry)

        return filtered

    # ══════════════════════════════════════════════════════
    #  紧急征和高危鉴别（Skill v2.3 Step 8）
    # ══════════════════════════════════════════════════════

    def _detect_emergency(self, ni, candidates: List[dict]) -> dict:
        """检测紧急征和高危鉴别。

        紧急征和高危鉴别规则由本地高危规则库维护。
        当前实现基于简单关键词检测，后续应替换为规则库查询。

        Returns:
            {
                "triggered": bool,
                "pattern": str,
                "recommendation": str,
                "exclusion_diseases": List[str]
            }
        """
        result = {
            "triggered": False,
            "pattern": "",
            "recommendation": "",
            "exclusion_diseases": [],
        }

        search_text = " ".join([
            ni.chief_complaint,
            " ".join(ni.symptoms),
            " ".join(ni.signs),
            " ".join(ni.labs),
            " ".join(ni.imaging),
        ]).lower()

        # 紧急征检测（基于本地关键词）
        _emergency_patterns = [
            (["胸痛", "胸闷", "压榨", "放射", "含服", "硝酸甘油"], "急性冠脉综合征", "建议紧急心电图、心肌酶、心内科评估"),
            (["胸痛", "濒死", "大汗", "放射"], "急性冠脉综合征（典型）", "建议紧急心电图、心肌酶、心内科评估"),
            (["呼吸困难", "喘息", "端坐", "夜间憋醒", "粉红泡沫"], "急性心衰/肺水肿", "建议紧急胸片、血气、心内科评估"),
            (["发热", "意识障碍", "紫癜", "瘀点", "休克血压"], "脓毒症/感染性休克", "建议紧急血培养、降钙素原、ICU评估"),
            (["高热", "颈强", "喷射", "意识", "抽搐", "脑膜"], "中枢神经系统感染", "建议紧急腰穿、头颅CT/脑脊液检查"),
            (["咯血", "大量", "窒息", "呼吸衰竭"], "大咯血", "建议紧急气道管理、胸外科会诊"),
            (["过敏", "休克", "喉头水肿", "荨麻疹", "呼吸困难"], "过敏性休克", "建议紧急肾上腺素、抗过敏、气道支持"),
        ]
        # 宽松匹配条件：只要存在任一模式的「核心关键词组合」就触发
        for keywords, pattern, recommendation in _emergency_patterns:
            # 精确匹配：所有核心词（前3个）都存在
            exact_match = all(kw in search_text for kw in keywords[:3])
            # 宽松匹配：前2个核心词 + 至少2个后续词
            loose_match = (len(keywords) > 2
                           and sum(1 for kw in keywords[:2] if kw in search_text) >= 1
                           and sum(1 for kw in keywords[2:5] if kw in search_text) >= 2)
            if exact_match or loose_match:
                result["triggered"] = True
                result["pattern"] = pattern
                result["recommendation"] = recommendation
                break

        # 高危鉴别：候选病中的高危疾病
        _high_risk_disease_keywords = ["心肌梗死", "脑出血", "肺栓塞", "主动脉夹层", "脓毒症",
                                       "急性胰腺炎重症", "肝衰竭", "肾衰竭", "呼吸衰竭"]
        for entry in candidates:
            cn = entry.get("diseaseName_cn", "").strip()
            if any(kw in cn for kw in _high_risk_disease_keywords):
                result["exclusion_diseases"].append(cn)

        return result

    # ══════════════════════════════════════════════════════
    #  LLM 输出代码校验
    # ══════════════════════════════════════════════════════

    def _validate_llm_output(self, llm_diagnoses: List[str],
                              scored_candidates: List[dict],
                              ni) -> tuple:
        """对 LLM 语义匹配结果进行代码校验（Skill v2.1 Step 4）

        Returns:
            (valid_diagnoses: List[str], passed: bool, reason: str)
            - valid_diagnoses: 通过校验的诊断列表
            - passed: 是否至少有一个诊断通过校验
            - reason: 校验失败原因
        """
        if not llm_diagnoses:
            return ([], False, "LLM 未输出诊断")

        # 检查1：所有诊断必须在本地候选库中
        valid = []
        for d in llm_diagnoses:
            d_lower = d.strip().lower()
            in_db = False
            for entry in self.db:
                cn = entry.get("diseaseName_cn", "").strip().lower()
                en = entry.get("disease_name", "").lower()
                if d_lower == cn or d_lower == en or d_lower in cn or cn in d_lower:
                    in_db = True
                    # 使用标准名
                    valid.append(entry.get("diseaseName_cn", d))
                    break
            if not in_db:
                print(f"[M1_LLM_VALIDATE] LLM 输出的 '{d}' 不在本地候选库中，已过滤")

        if not valid:
            return ([], False, "LLM 输出不在本地候选库中")

        # 检查2：排除与症状完全矛盾的诊断
        search_text = " ".join([ni.chief_complaint] + ni.symptoms).lower()
        for d in valid[:]:
            entry = self.cn_name_index.get(d.lower())
            if not entry:
                for e in self.db:
                    if e.get("diseaseName_cn", "").strip().lower() == d.lower():
                        entry = e
                        break
            if entry:
                # 检查是否有强排除项
                for crit in entry.get("diagnostic_criteria", []):
                    if "排除" in crit and any(nf in search_text for nf in ni.negative_findings):
                        print(f"[M1_LLM_VALIDATE] '{d}' 存在排除项，已降级")
                        break

        return (valid, bool(valid), "")

    # ══════════════════════════════════════════════════════
    #  诊断关系检测（Skill v2.1 Step 6.5）
    # ══════════════════════════════════════════════════════

    def _detect_diagnosis_relationships(self, primary: str,
                                         cn_diagnoses: List[str],
                                         structured_info: dict,
                                         ni) -> dict:
        """检测诊断关系（统一气道、肿瘤进展、感染进展）

        Returns:
            {
                "diagnosis_relationship": "关系描述",
                "united_airway_flag": bool,
                "tumor_progression_flag": bool,
                "infection_progression_flag": bool,
            }
        """
        result = {
            "diagnosis_relationship": "",
            "united_airway_flag": False,
            "tumor_progression_flag": False,
            "infection_progression_flag": False,
        }

        search_text = " ".join([
            primary,
            " ".join(cn_diagnoses),
            ni.chief_complaint,
            " ".join(ni.symptoms),
        ]).lower()

        # 统一气道关系
        _upper_airway_keywords = ["变应性鼻炎", "鼻窦炎", "上气道咳嗽综合征", "过敏性鼻炎", "慢性鼻炎"]
        _lower_airway_keywords = ["咳嗽", "支气管炎", "喘息", "气促", "胸闷"]

        has_upper = any(k in search_text for k in _upper_airway_keywords)
        has_lower = any(k in search_text for k in _lower_airway_keywords)

        if has_upper and has_lower:
            result["united_airway_flag"] = True
            result["diagnosis_relationship"] = "上气道过敏基础上继发下气道症状"

        # 肿瘤进展关系
        if structured_info.get("postoperative_state") or structured_info.get("metastasis_flag"):
            if "转移" in search_text or "复发" in search_text or "进展" in search_text or "消耗" in search_text:
                result["tumor_progression_flag"] = True
                if result["united_airway_flag"]:
                    result["diagnosis_relationship"] += "；"
                result["diagnosis_relationship"] += "肿瘤术后/转移状态，需关注肿瘤进展"

        # 感染进展关系
        _mild_infection = ["上呼吸道感染", "普通感冒", "咽炎", "扁桃体炎"]
        _severe_infection = ["肺炎", "支气管炎", "重症感染", "呼吸衰竭"]
        has_mild = any(k in search_text for k in _mild_infection)
        has_severe = any(k in search_text for k in _severe_infection) or \
                     any(lab for lab in ni.labs if "升高" in lab and "白细胞" in lab or "CRP" in lab or "PCT" in lab)

        if has_mild and has_severe:
            result["infection_progression_flag"] = True
            if result["united_airway_flag"] or result["tumor_progression_flag"]:
                result["diagnosis_relationship"] += "；"
            result["diagnosis_relationship"] += "上呼吸道感染基础上出现下呼吸道感染/重症表现"

        return result

    def _recall_candidates(self, ni) -> List[dict]:
        """从患者资料召回候选疾病列表（供 Agent 比对）

        召回策略（依次尝试）：
        1. 患者提到的病名 → 精确匹配中文名/英文名（含 slang_map 映射）
        2. 症状文本全文 → 与中文病名、典型症状、诊断标准关键词匹配
        3. 限制最多 20 个候选
        """
        candidates = []
        seen = set()

        # 策略1：病名精确匹配（使用 slang_map）
        mentioned = ni.patient_mentioned_disease.strip().lower()
        # 通过 slang_map 将口语化病名转为标准名
        _slang = {
            "寻麻疹": "荨麻疹", "过敏性寻麻疹": "荨麻疹", "过敏性荨麻疹": "荨麻疹",
            "慢性寻麻疹": "慢性荨麻疹", "咳嗽病": "咳嗽",
            "抽动症": "抽动障碍", "多动症": "注意缺陷多动障碍",
        }
        _search_name = _slang.get(mentioned, mentioned)

        # 策略1b：已知诊断召回（known_diagnosis 轨）
        _known_diag = ni.known_diagnosis.strip().lower()
        if _known_diag and _known_diag not in [mentioned, _search_name]:
            # 尝试匹配 cn_name_index 或 name_index
            _known_entry = self.cn_name_index.get(_known_diag) or self.name_index.get(_known_diag)
            if not _known_entry:
                for entry in self.db:
                    cn = entry.get("diseaseName_cn", "").strip().lower()
                    en = entry.get("disease_name", "").lower()
                    if cn == _known_diag or en == _known_diag or _known_diag in cn or cn in _known_diag:
                        _known_entry = entry
                        break
            if _known_entry and _known_entry["disease_name"] not in seen:
                candidates.append(_known_entry)
                seen.add(_known_entry["disease_name"])
                print(f"[M1_KNOWN_DIAG] 已知诊断召回: {_known_entry.get('diseaseName_cn','')}")

        # 语义扩展：对简称/泛称同时召回多个可能疾病
        # 如"疱疹"应同时考虑"单纯疱疹"和"带状疱疹"
        _semantic_expansions = {
            "疱疹": ["单纯疱疹", "带状疱疹"],
            "感冒": ["急性上呼吸道感染", "流行性感冒"],
            "痔疮": ["内痔", "外痔", "混合痔"],
        }

        if _search_name:
            # 精确匹配中文名（优先精确再子串）
            exact_entry = None
            substr_entry = None
            for entry in self.db:
                cn = entry.get("diseaseName_cn", "").strip().lower()
                en = entry.get("disease_name", "").lower()
                if cn == _search_name or en == _search_name:
                    exact_entry = entry
                    break
                if _search_name in cn or cn in _search_name or _search_name in en or en in _search_name:
                    if substr_entry is None:
                        substr_entry = entry
            entry = exact_entry or substr_entry
            if entry and entry["disease_name"] not in seen:
                candidates.append(entry)
                seen.add(entry["disease_name"])
            # 如果精确匹配没找到，尝试子串匹配整个 cn_name_index
            if not candidates:
                for cn_key, entry in self.cn_name_index.items():
                    if _search_name in cn_key or cn_key in _search_name:
                        if entry["disease_name"] not in seen:
                            candidates.append(entry)
                            seen.add(entry["disease_name"])
                            break

        # 语义扩展：如果患者提到的病名是泛称，同时召回相关疾病
        # 先尝试直接匹配，再尝试去掉修饰词（急性、慢性等）匹配
        _semantic_expand_key = _search_name
        if _semantic_expand_key not in _semantic_expansions:
            # 去掉"急性""慢性""亚急性"等时间修饰词再试
            _modifier_prefixes = ["急性", "慢性", "亚急性", "复发性", "持续性", "间歇性"]
            for _prefix in _modifier_prefixes:
                if _search_name.startswith(_prefix) and len(_search_name) > len(_prefix):
                    _stem = _search_name[len(_prefix):]
                    if _stem in _semantic_expansions:
                        _semantic_expand_key = _stem
                        break
        if _semantic_expand_key in _semantic_expansions:
            for _exp_cn in _semantic_expansions[_search_name]:
                _exp_lower = _exp_cn.lower().strip()
                # 从 cn_name_index 查找
                _found = self.cn_name_index.get(_exp_lower)
                if _found and _found["disease_name"] not in seen:
                    candidates.append(_found)
                    seen.add(_found["disease_name"])
                    print(f"[M1_SEMANTIC_EXPAND] {_search_name} -> {_exp_cn}")
                else:
                    # 从 db 全文搜索
                    for entry in self.db:
                        cn = entry.get("diseaseName_cn", "").strip().lower()
                        en = entry.get("disease_name", "").lower()
                        if cn == _exp_lower or en == _exp_lower:
                            if entry["disease_name"] not in seen:
                                candidates.append(entry)
                                seen.add(entry["disease_name"])
                                print(f"[M1_SEMANTIC_EXPAND] {_search_name} -> {_exp_cn}")
                            break

        # 策略2：症状全文召回（中文 keyword_index + 症状词匹配）
        if len(candidates) < 3:
            all_text = [ni.chief_complaint.lower()] + [s.lower() for s in ni.symptoms]
            # 先从中文病名索引中匹配
            for text in all_text:
                if not text or len(text) < 2:
                    continue
                for cn_key, entry in self.cn_name_index.items():
                    if entry["disease_name"] in seen:
                        continue
                    if cn_key in text or text[:4] in cn_key or cn_key[:4] in text:
                        candidates.append(entry)
                        seen.add(entry["disease_name"])
                        if len(candidates) >= 20:
                            break
                if len(candidates) >= 20:
                    break

            # 再从 keyword_index 匹配
            if len(candidates) < 3:
                for text in all_text:
                    if not text or len(text) < 2:
                        continue
                    for word in re.split(r'[\s,，、.。:：()（）]+', text):
                        if len(word) < 2:
                            continue
                        for kw, entries in self.keyword_index.items():
                            if word in kw or kw in word:
                                for entry in entries:
                                    if entry["disease_name"] not in seen:
                                        candidates.append(entry)
                                        seen.add(entry["disease_name"])
                                        if len(candidates) >= 20:
                                            break
                        if len(candidates) >= 20:
                            break

        # 策略3：根据主诉病名强制纳入对应疾病
        # 当患者主诉中提到具体疾病名时，确保该疾病被召回
        _force_include_map = {
            "食道反流": ["反流性食管炎", "胃食管反流病", "食管炎"],
            "食管反流": ["反流性食管炎", "胃食管反流病", "食管炎"],
            "食道返流": ["反流性食管炎", "胃食管反流病", "食管炎"],
            "食管返流": ["反流性食管炎", "胃食管反流病", "食管炎"],
            "反酸": ["反流性食管炎", "胃食管反流病"],
            "胃食管反流": ["反流性食管炎", "胃食管反流病"],
            "鼻涕倒流": ["慢性鼻窦炎", "急性鼻窦炎", "变应性鼻炎"],
            "鼻后滴漏": ["慢性鼻窦炎", "急性鼻窦炎", "变应性鼻炎"],
            "鼻窦炎": ["慢性鼻窦炎", "急性鼻窦炎"],
            "咽炎": ["慢性咽炎", "急性咽炎"],
        }
        _mentioned_lower = ni.patient_mentioned_disease.strip().lower()
        for _keyword, _targets in _force_include_map.items():
            if _keyword in _mentioned_lower or _mentioned_lower in _keyword:
                for _tname in _targets:
                    _already = False
                    for _c in candidates:
                        if _tname in _c.get("diseaseName_cn", "") or _c.get("disease_name", "") == _tname:
                            _already = True
                            break
                    if not _already:
                        _found = self.cn_name_index.get(_tname.lower())
                        if _found is None:
                            for _k, _e in self.cn_name_index.items():
                                if _tname in _k or _k in _tname:
                                    _found = _e
                                    break
                        if _found and _found["disease_name"] not in seen:
                            candidates.append(_found)
                            seen.add(_found["disease_name"])
                            print(f"[M1_FORCE_INCLUDE] 强制纳入: {_tname}")
                break

        # 策略4：对缺少的候选追加——通过典型症状交叉匹配（高优先级西医病名补录）
        # 例如：患者症状中有"月经量多+痛经+周期不规律"，应确保"子宫肌瘤"、"子宫内膜异位症"等入选
        _symptom_txt = " ".join([ni.chief_complaint.lower()] + [s.lower() for s in ni.symptoms])
        # 定义一组"症状特征→应补充候选疾病"的映射
        _symptom_disease_fixes = [
            (["反流", "返流", "返酸", "反酸", "烧心", "灼心"],
             ["反流性食管炎", "胃食管反流病", "食管炎", "慢性胃炎"]),
            (["痛经", "月经", "量多", "血块", "周期不规律", "腹痛"],
             ["子宫肌瘤", "子宫内膜异位症", "子宫腺肌症", "痛经", "盆腔炎", "月经过多"]),
            (["干咳", "咽痒", "咽干", "咳嗽"],
             ["急性支气管炎", "咽炎", "上呼吸道感染", "咳嗽", "支原体肺炎"]),
            (["腰痛", "腰酸", "弯腰"],
             ["腰痛", "腰椎间盘突出症", "强直性脊柱炎", "肾结石"]),
            (["全身痒", "瘙痒", "疹子", "遇热"],
             ["荨麻疹", "皮肤瘙痒症", "特应性皮炎", "湿疹"]),
            (["发热", "咽喉痛", "扁桃体"],
             ["急性扁桃体炎", "咽炎", "上呼吸道感染"]),
        ]
        for _kw_list, _disease_list in _symptom_disease_fixes:
            if all(kw in _symptom_txt for kw in _kw_list):
                for _dname in _disease_list:
                    _found = False
                    for _c in candidates:
                        if _dname in _c.get("diseaseName_cn", "") or _c.get("disease_name", "") == _dname:
                            _found = True
                            break
                    if not _found:
                        # 从 cn_name_index 或 name_index 找
                        _entry = self.cn_name_index.get(_dname.lower())
                        if _entry is None:
                            for _k, _e in self.cn_name_index.items():
                                if _dname in _k or _k in _dname:
                                    _entry = _e
                                    break
                        if _entry is None:
                            _entry = self.name_index.get(_dname.lower())
                        if _entry and _entry["disease_name"] not in seen:
                            candidates.append(_entry)
                            seen.add(_entry["disease_name"])
                break  # 只匹配第一个症状组

        # 过滤掉非疾病脏数据（_category 为 excluded_not_disease 或 _card_source 为 diagnostic_card_only 且无有效疾病信息）
        _filtered = []
        for _c in candidates:
            _cn = _c.get("diseaseName_cn", "").strip()
            if not _cn:
                continue
            if _c.get("category") == "excluded_not_disease":
                continue
            if _c.get("_card_source") == "diagnostic_card_only":
                # 只有 diagnostic_card_only 标记且无任何 meaningful 字段的才过滤
                if not _c.get("diagnostic_criteria") and not _c.get("typical_symptoms") and not _c.get("disease_name"):
                    # 检查是否包含非疾病特征字符
                    _skip_chars = ["【", "】", "诊断", "待查", "排除"]
                    if any(c in _cn for c in _skip_chars):
                        continue
            _filtered.append(_c)
        return _filtered[:20]

    # ══════════════════════════════════════════════════════
    #  候选疾病打分排序（代码级，Jaccard + 症状覆盖率 + 关键词匹配）
    # ══════════════════════════════════════════════════════

    def _score_candidates(self, candidates: List[dict], ni) -> List[dict]:
        """贝叶斯式诊断验证（Skill v2.3 Step 5），替代简单积分制。

        对每个候选病，列出其典型症状/诊断标准/检查证据，
        检查患者输入中哪些证据支持、哪些矛盾、哪些缺失，
        按"最符合全部证据且无关键矛盾"排序。

        不再计算加和总分，改为基于证据充分度输出置信度。

        Returns:
            [{
                "entry": dict,
                "score": int,          # 保留字段（兼容旧调用），基于证据数
                "detail": {            # 保留字段（兼容旧调用）
                    "name": str,
                    "matched_signs": [],
                    "matched_exams": [],
                    "negative_finding_penalty": int,
                },
                "criteria_struct": {   # 诊断标准逐条比对
                    "matched_required": [],
                    "matched_supportive": [],
                    "missing_required": [],
                    "contradicted_items": [],
                    "exclusion_penalty": int,
                    "confidence": str,
                },
                "evidence_for": [str],
                "evidence_against": [str],
                "confidence": str,     # 贝叶斯置信度 high/medium/low
            }]
        """
        if not candidates:
            return []

        search_text = " ".join([
            ni.chief_complaint, ni.patient_mentioned_disease,
            " ".join(ni.symptoms), " ".join(ni.signs),
            " ".join(ni.labs), " ".join(ni.imaging),
        ]).lower()
        mentioned = ni.patient_mentioned_disease.strip().lower()
        # known_diagnosis 也作为病名先验参与 Bayesian 匹配（Skill v2.3 Step 3.1）
        if ni.known_diagnosis:
            _kd = ni.known_diagnosis.strip().lower()
            if _kd and (not mentioned or _kd != mentioned):
                if mentioned:
                    mentioned = mentioned + " " + _kd
                else:
                    mentioned = _kd
        patient_words = set()
        for txt in [ni.chief_complaint] + ni.symptoms + ni.signs:
            for w in txt.strip().lower().split():
                for sep in ['、', '，', ',', '。', '.', ' ']:
                    for p in w.split(sep):
                        p = p.strip()
                        if len(p) >= 2:
                            patient_words.add(p)
        nf_set = set(nf.lower() for nf in ni.negative_findings)

        results = []
        for entry in candidates:
            cn = entry.get("diseaseName_cn", "").strip()
            cn_lower = cn.lower()
            en = entry.get("disease_name", "").lower()
            typical = entry.get("typical_symptoms", [])
            criteria = entry.get("diagnostic_criteria", [])
            sms = entry.get("source_verified_medical_summary", {})

            # ── 证据收集：对所有候选病都初始化 ──
            ev_for = []
            ev_against = []
            matched_signs = []
            matched_exams = set()
            negative_finding_penalty = 0

            # ── 1. 病名匹配（先验证据）──
            name_match = False
            name_exact = False
            if mentioned:
                if cn_lower == mentioned or en == mentioned:
                    name_match = True
                    name_exact = True
                    ev_for.append(f"患者明确提到病名「{cn}」—— 强先验")
                elif mentioned in cn_lower or cn_lower in mentioned or mentioned in en or en in mentioned:
                    name_match = True
                    ev_for.append(f"患者提到病名与「{cn}」相关")

            # ── 2. 典型症状匹配（证据交集）──

            for symp in typical:
                symp_lower = symp.strip().lower()
                if not symp_lower:
                    continue
                found = False
                negated = False
                for s in ni.symptoms:
                    if s.lower() in symp_lower or symp_lower in s.lower():
                        ev_for.append(f"患者「{s}」匹配典型症状「{symp}」")
                        found = True
                        break
                if not found and symp_lower in search_text:
                    for nf in nf_set:
                        nf_core = re.sub(r"^(无|没有|未|否认|不伴|未见|无明显|不)", "", nf).strip()
                        if nf_core and (nf_core in symp_lower or symp_lower in nf_core):
                            ev_against.append(f"否定「{nf}」与典型症状「{symp}」矛盾")
                            negative_finding_penalty += 5
                            negated = True
                            break
                    if not negated:
                        ev_for.append(f"患者描述包含典型症状「{symp}」")

            # ── 3. 体征检查 ──
            _tongue_signs = {"舌红", "舌暗红", "舌淡红", "舌紫", "舌点刺", "舌胖大", "舌瘦"}
            _coating_signs = {"苔黄腻", "苔白腻", "苔薄白", "苔薄黄", "苔黄燥", "苔白滑", "少苔", "剥苔"}
            _pulse_signs = {"脉数", "脉滑", "脉弦", "脉细", "脉沉", "脉浮", "脉弱", "脉涩", "脉缓", "脉濡"}
            for sign_set in [_tongue_signs, _coating_signs, _pulse_signs]:
                for sign in sign_set:
                    if sign in search_text:
                        matched_signs.append(sign)
                        break

            _exam_kw_map = {
                "发热": "体温升高", "高热": "体温升高", "低热": "体温升高",
                "痰黄": "痰培养/涂片", "痰绿": "痰培养/涂片", "黄痰": "痰培养/涂片", "绿痰": "痰培养/涂片",
                "气促": "呼吸频率", "呼吸困难": "呼吸频率", "胸闷": "呼吸功能",
                "啰音": "肺部听诊", "湿啰音": "肺部听诊", "干啰音": "肺部听诊",
                "白细胞": "血常规", "WBC": "血常规", "CRP": "炎症指标", "PCT": "炎症指标",
                "潜血": "便常规", "粘液": "便常规",
            }
            for pat, exam_name in _exam_kw_map.items():
                if pat in search_text:
                    matched_exams.add(exam_name)

            # ── 4. 诊断标准逐条比对 ──
            criteria_struct = self._compare_criteria(entry, search_text, patient_words)

            # ── 5. 置信度判定（贝叶斯式：先验 × 证据似然）──
            has_contradiction = len(ev_against) > 1 or len(criteria_struct.get("contradicted_items", [])) > 0
            missing_critical = len(criteria_struct.get("missing_required", [])) > 0
            enough_evidence = len(ev_for) >= 2 or name_match
            # 精确病名匹配是强先验：即使只有1个轻证据也能达 high 置信
            if name_exact and not has_contradiction and not missing_critical:
                confidence = "high"
            elif enough_evidence and not has_contradiction and not missing_critical:
                confidence = "high"
            elif enough_evidence and not has_contradiction:
                confidence = "medium"
            elif enough_evidence:
                confidence = "low"
            elif not enough_evidence:
                confidence = "low"
            else:
                confidence = "low"

            # 兼容分数（供旧调用方使用）
            score = len(ev_for) * 10 - negative_finding_penalty - len(ev_against) * 5
            score = max(0, score)
            # 精确病名匹配额外加分（贝叶斯先验强度）
            bayesian_prior = 30 if name_exact else (15 if name_match else 0)

            results.append({
                "entry": entry,
                "score": score,
                "detail": {
                    "name": cn,
                    "matched_signs": matched_signs,
                    "matched_exams": list(matched_exams),
                    "negative_finding_penalty": negative_finding_penalty,
                    "name_score": 30 if name_exact else (15 if name_match else 0),
                    "sign_score": min(10, len(matched_signs) * 3),
                    "exam_score": min(10, len(matched_exams) * 2),
                },
                "criteria_struct": criteria_struct,
                "evidence_for": ev_for,
                "evidence_against": ev_against,
                "confidence": confidence,
                "bayesian_prior": bayesian_prior,
            })

        # 按置信度排序：high > medium > low；同等置信下先验强度优先
        _order = {"high": 0, "medium": 1, "low": 2}
        results.sort(key=lambda x: (
            _order.get(x["confidence"], 2),
            -x.get("bayesian_prior", 0),
            -x["score"],
            x["detail"]["name"],
        ))

        return results

    def _compare_criteria(self, entry: dict, search_text: str, patient_words: set) -> dict:
        """对单个候选疾病做 diagnostic_criteria 逐条结构化比对

        返回结构：
        {
            "matched_required": [...],
            "matched_supportive": [...],
            "missing_required": [...],
            "contradicted_items": [...],
            "exclusion_penalty": 0,
            "confidence": "high|medium|low",
        }
        """
        criteria = entry.get("diagnostic_criteria", [])
        if not criteria or not isinstance(criteria, list):
            return {
                "matched_required": [],
                "matched_supportive": [],
                "missing_required": [],
                "contradicted_items": [],
                "exclusion_penalty": 0,
                "confidence": "low",
            }

        matched_required = []
        matched_supportive = []
        missing_required = []
        contradicted_items = []

        for c in criteria:
            c_lower = c.strip().lower()
            if not c_lower:
                continue

            # 检测是否符合"排除"语义
            is_exclusion = any(kw in c_lower for kw in ["排除", "除外", "鉴别", "红旗", "危急", "emergency", "danger"])

            # 检测是否为"必须""核心"条件
            is_required = any(kw in c_lower for kw in ["必须", "核心", "必要", "符合", "明确", "确认", "诊断需"])

            # 检测否定关键词（无/未见/否认/没有）
            has_negative_keywords = any(kw in c_lower for kw in ["无", "未见", "否认", "没有", "排除"])

            if is_exclusion:
                # 排除/鉴别类：如果患者有对应症状则扣分
                c_clean = c_lower
                for kw in ["排除", "除外", "需鉴别", "红旗", "危急", "emergency", "danger"]:
                    c_clean = c_clean.replace(kw, "")
                c_clean = c_clean.strip().lstrip("：:，,")

                # 提取关键词：去掉非内容性前缀
                c_words = [w for w in c_clean.split() if len(w) >= 2 and w not in ("排除", "除外", "需鉴别")]
                contradicted = [w for w in c_words if w in search_text or any(pw in w for pw in patient_words)]
                if contradicted:
                    contradicted_items.append({"criteria": c, "matched_terms": contradicted})

            elif is_required:
                # 必要/核心条件
                c_words = [w for w in c_lower.split() if len(w) >= 2]
                # 检查患者的任何症状词是否在这个条件文本中
                has_match = False
                for pw in patient_words:
                    if any(len(pw) >= 2 and pw in c_word for c_word in c_words) or any(
                        cw in search_text for cw in c_words if len(cw) >= 4
                    ):
                        has_match = True
                        break
                # 也检查整个条件文本是否在 search_text 中
                if c_lower in search_text:
                    has_match = True

                if has_match:
                    matched_required.append(c)
                else:
                    missing_required.append(c)
            else:
                # 支持性条件
                has_match = False
                for pw in patient_words:
                    if any(len(pw) >= 2 and pw in c_word for c_word in c_lower.split() if len(c_word) >= 4):
                        has_match = True
                        break
                if c_lower in search_text:
                    has_match = True
                if has_match:
                    matched_supportive.append(c)

        exclusion_penalty = len(contradicted_items) * (-10)

        # 置信度判断
        total_required = len(matched_required) + len(missing_required)
        if total_required > 0:
            required_ratio = len(matched_required) / total_required
        else:
            required_ratio = 1.0
        total_supportive = len(matched_supportive) if matched_supportive else 0

        if required_ratio >= 0.7 and exclusion_penalty == 0:
            confidence = "high"
        elif required_ratio >= 0.4 and exclusion_penalty > -20:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "matched_required": matched_required,
            "matched_supportive": matched_supportive,
            "missing_required": missing_required,
            "contradicted_items": contradicted_items,
            "exclusion_penalty": exclusion_penalty,
            "confidence": confidence,
        }

    # ═══════════════════════════════════════════════════════════════
    #  结构化信息提取：术后状态、转移、并发症
    # ═══════════════════════════════════════════════════════════════

    def _extract_structured_info(self, ni, raw_for_output: dict,
                                  primary_diagnosis: str, cn_diagnoses: list,
                                  source: str) -> dict:
        """从患者文本和 structured 字段中提取结构化信息（Skill v2.1 Step 9）

        返回结构（与 Skill v2.1 Step 9 一致）：
        {
            "age_group": "",
            "pregnancy": false,
            "elderly": false,
            "child": false,
            "postoperative_state": false,
            "metastasis_flag": false,
            "metastasis_sites": [],
            "tumor_history": false,
            "complications_or_comorbidities": [],
            "risk_flags": [],
            "disease_status": [],
            "llm_protected": false,
        }
        """
        search_text = " ".join([
            raw_for_output.get("chief_complaint", ""),
            raw_for_output.get("patient_mentioned_disease", ""),
        ] + ni.symptoms + ni.signs).lower()

        result = {
            "age_group": "",
            "pregnancy": False,
            "elderly": False,
            "child": False,
            "postoperative_state": False,
            "metastasis_flag": False,
            "metastasis_sites": [],
            "tumor_history": False,
            "complications_or_comorbidities": [],
            "risk_flags": [],
            "united_airway_flag": False,
            "tumor_progression_flag": False,
            "infection_progression_flag": False,
            # 内部字段（不输出到外部 Schema，用于推理）
            "_postoperative": {"detected": False, "original_surgery": "", "source": ""},
            "_metastasis": {"detected": False, "sites": []},
            "llm_protected": False,
            "disease_status": [],
        }

        # ── 0. 从标准化输入中提取特殊状态 ──
        if ni.age:
            try:
                age_val = int(ni.age.replace("岁", "").strip())
                if age_val < 14:
                    result["child"] = True
                    result["age_group"] = "child"
                    result["risk_flags"].append("pediatric_risk")
                elif age_val >= 65:
                    result["elderly"] = True
                    result["age_group"] = "elderly"
            except (ValueError, AttributeError):
                pass

        # 从 special_status 字段提取
        for status in ni.special_status:
            status_lower = status.lower()
            if any(kw in status_lower for kw in ["孕", "妊娠", "怀孕", "preg"]):
                result["pregnancy"] = True
                result["risk_flags"].append("pregnancy_risk")
            if any(kw in status_lower for kw in ["儿童", "幼儿", "小儿", "儿"]):
                result["child"] = True
                result["age_group"] = "child"
                if "pediatric_risk" not in result["risk_flags"]:
                    result["risk_flags"].append("pediatric_risk")
            if any(kw in status_lower for kw in ["老年", "老人", "高龄"]):
                result["elderly"] = True
                result["age_group"] = "elderly"

        # 也从 past_history 提取特殊状态
        for hist in ni.past_history:
            hist_lower = hist.lower()
            if any(kw in hist_lower for kw in ["孕", "妊娠", "怀孕"]):
                result["pregnancy"] = True
                if "pregnancy_risk" not in result["risk_flags"]:
                    result["risk_flags"].append("pregnancy_risk")
            if any(kw in hist_lower for kw in ["肿瘤", "癌", "恶性"]):
                result["tumor_history"] = True
                if "cancer_history" not in result["risk_flags"]:
                    result["risk_flags"].append("cancer_history")

        # ── 1. 术后状态识别 ──
        postop_patterns = [
            (r'(?:做了|行|接受|做了个)([^，,。\.\n]{1,20})手术', "surgery_history_explicit_name_match"),
            (r'([^，,。\.\n]{1,20})术后', "surgery_history_postop"),
            (r'([^，,。\.\n]{1,20})切除术后', "surgery_history_resection_postop"),
        ]
        for pat, src_type in postop_patterns:
            import re as _re
            m = _re.search(pat, search_text)
            if m:
                surgery_name = m.group(1).strip()
                result["disease_status"].append("postoperative_state")
                result["_postoperative"]["detected"] = True
                result["_postoperative"]["original_surgery"] = surgery_name
                result["_postoperative"]["source"] = src_type
                break

        # ── 2. 转移状态识别 ──
        # 匹配"XX转移"模式：肝转移、骨转移、肺转移、淋巴结转移、多发转移
        import re as _re
        metastasis_sites = []
        for m in _re.finditer(r'(肝|骨|肺|淋巴结|脑|肾|肾上腺|腹膜|胸膜|皮肤|软组织|多发|全身)[的]?(转移|多发转移|广泛转移)', search_text):
            site = m.group(0).strip()
            if site not in metastasis_sites:
                metastasis_sites.append(site)
        # 也匹配"转移至XX"
        for m in _re.finditer(r'转移[至到](肝|骨|肺|淋巴结|脑|肾|肾上腺)', search_text):
            site = "{}转移".format(m.group(1))
            if site not in metastasis_sites:
                metastasis_sites.append(site)
        if metastasis_sites:
            result["disease_status"].append("metastatic_disease")
            result["_metastasis"]["detected"] = True
            result["_metastasis"]["sites"] = metastasis_sites

        # ── 3. 并发症/伴随问题识别 ──
        complication_defs = [
            (["水肿", "浮肿", "肿胀", "下肢水肿"], "水肿"),
            (["低蛋白", "低白蛋白"], "低蛋白血症"),
            (["贫血", "血红低", "Hb低"], "贫血"),
            (["感染", "发热"], "感染/发热"),
            (["纳差", "食欲不振", "不想吃", "吃不下", "没胃口", "食纳差"], "纳差/营养风险"),
            (["消瘦", "体重下降", "瘦了", "变瘦"], "消瘦/营养不良"),
            (["腹水", "腹部膨隆", "蛙腹"], "腹水"),
            (["疼痛", "痛", "隐痛", "剧痛"], "疼痛"),
            (["黄疸", "皮肤黄", "巩膜黄"], "黄疸"),
            (["呕吐", "恶心", "呕"], "恶心呕吐"),
            (["乏力", "疲劳", "疲乏", "无力", "倦怠"], "乏力"),
            (["失眠", "不寐", "难入睡", "早醒"], "失眠"),
            (["麻木", "麻"], "麻木"),
        ]
        seen_complications = set()
        for keywords, label in complication_defs:
            if any(kw in search_text for kw in keywords):
                if label not in seen_complications:
                    result["complications_or_comorbidities"].append(label)
                    seen_complications.add(label)

        # ── 4. 风险标识 ──
        if any(kw in search_text for kw in ["癌", "瘤", "恶性肿瘤", "cancer", "tumor"]):
            result["risk_flags"].append("cancer_history")
        if result["_metastasis"]["detected"]:
            result["risk_flags"].append("advanced_disease")
        if any(kw in search_text for kw in ["术后", "手术", "切除", "化疗", "放疗", "靶向"]):
            result["risk_flags"].append("post_treatment")

        # ── 5. LLM 保护判定 ──
        # 如果 primary_diagnosis 包含"癌"字，且 source 表明是从手术/转移文本中明确提取的，
        # 则标记 llm_protected=True，LLM 不得改写为其他癌种
        if "癌" in primary_diagnosis:
            postop_src = result["_postoperative"].get("source", "")
            if postop_src and "surgery_history" in postop_src:
                result["llm_protected"] = True
            elif result["_metastasis"]["detected"]:
                # 有转移状态说明原发癌种已在文本中明确
                # 检查转移前的文本是否包含明确的癌名
                if any(kw in search_text for kw in ["癌", "瘤"]):
                    result["llm_protected"] = True

        # ── 6. 同步高层字段 ──
        if result["_postoperative"]["detected"]:
            result["postoperative_state"] = True
            result["disease_status"].append("postoperative_state")
        if result["_metastasis"]["detected"]:
            result["metastasis_flag"] = True
            result["metastasis_sites"] = result["_metastasis"]["sites"]
            result["disease_status"].append("metastatic_disease")
        if any(kw in search_text for kw in ["癌", "瘤", "恶性肿瘤", "cancer", "tumor"]):
            result["tumor_history"] = True
            if "cancer_history" not in result["risk_flags"]:
                result["risk_flags"].append("cancer_history")
        if result["_metastasis"]["detected"] and "advanced_disease" not in result["risk_flags"]:
            result["risk_flags"].append("advanced_disease")

        return result

    # ══════════════════════════════════════════════════════
    #  统一结果构建（P0-2 — 所有 return 路径必须统一走此方法）
    # ══════════════════════════════════════════════════════

    def _build_m1_result(self, ni, candidates, scored, cn_diagnoses, source, diagnosis_status,
                         parsed_questions=None, parsed_need_info=None,
                         followup_answers=None, raw_for_output=None,
                         scored_top=None, scored_details=None, scored_diseases=None,
                         current_top_candidates=None, cannot_decide_because=None) -> Dict:
        """统一构建 M1 诊断结果，消除 4 个 return 路径的重复代码

        Args:
            ni: NormalizedInput
            candidates: 召回候选列表
            scored: 评分后的候选列表
            cn_diagnoses: 诊断名列表（有序，[0] 为 primary）
            source: 诊断来源（code_bayesian / llm_semantic_matcher / ...）
            diagnosis_status: 诊断状态（PASS / LOW_CONFIDENCE / REQUEST_MORE_INFO / NEED_EXTERNAL_SEARCH）
            parsed_questions: 可选，LLM 追问问题列表
            parsed_need_info: 可选，LLM 追问 missing_info
            followup_answers: 可选，历史追问答案
            raw_for_output: 可选，原始输入
            scored_top: 可选，top 评分候选（默认 scored[:3]）
            scored_details: 可选，评分详情列表
            scored_diseases: 可选，评分疾病名列表
            current_top_candidates: 可选，当前 top 候选（用于追问）
            cannot_decide_because: 可选，不能判定的原因
        """
        # ── 空安全 ──
        cn_diagnoses = cn_diagnoses or []
        candidates = candidates or []
        scored = scored or []
        _scored_top = scored_top or scored[:3] if scored else []
        _scored_top = _scored_top or []
        _scored_details = scored_details or [s.get("detail", {}) for s in _scored_top]
        _scored_diseases = scored_diseases or [s["entry"].get("diseaseName_cn", "") for s in _scored_top] if _scored_top else []
        raw_for_output = raw_for_output or {}

        # 1. Primary 计算
        primary = cn_diagnoses[0] if cn_diagnoses else ""

        # 2. 已知诊断矛盾检查（P0-1）
        # 如 cn_diagnoses 来自 LLM，但已知诊断存在于 scored 且无矛盾，保留已知诊断
        if cn_diagnoses and ni and hasattr(ni, 'known_diagnosis') and ni.known_diagnosis:
            _kd_name = ni.known_diagnosis.strip().lower()
            _source_prefix = source or ""
            if _source_prefix.startswith("llm") and _kd_name:
                _kd_in_scored = any(
                    _kd_name == s["entry"].get("diseaseName_cn", "").strip().lower()
                    or _kd_name in s["entry"].get("diseaseName_cn", "").strip().lower()
                    or s["entry"].get("diseaseName_cn", "").strip().lower() in _kd_name
                    for s in _scored_top
                )
                if _kd_in_scored:
                    _uncertainty_kw = {"疑似", "可能", "像是", "网上查", "感觉像", "好像", "不确定"}
                    _can_override = any(kw in ni.known_diagnosis.lower() for kw in _uncertainty_kw)
                    if not _can_override:
                        _kd_in_diagnoses = any(
                            _kd_name == d.lower() or _kd_name in d.lower() or d.lower() in _kd_name
                            for d in cn_diagnoses
                        )
                        if not _kd_in_diagnoses:
                            _kd_full_name = ni.known_diagnosis
                            for _s in _scored_top:
                                _scn = _s["entry"].get("diseaseName_cn", "").strip()
                                _scl = _scn.lower()
                                if _scl == _kd_name or _kd_name in _scl or _scl in _kd_name:
                                    _kd_full_name = _scn
                                    break
                            cn_diagnoses = [_kd_full_name] + cn_diagnoses
                            primary = cn_diagnoses[0]
                            print(f"[M1_KNOWN_DIAG_CONTRADICT_BUILD] 已知诊断 {_kd_full_name} 被 LLM 替换，已恢复为 primary")

        # 3. LLM guard for cancer protection
        _llm_protected = False
        if primary and "癌" in primary and source and source.startswith("llm"):
            _search_all = " ".join([
                ni.chief_complaint if ni else "",
                " ".join(ni.symptoms) if ni and hasattr(ni, 'symptoms') else "",
                " ".join(ni.signs) if ni and hasattr(ni, 'signs') else "",
            ]).lower()
            _llm_primary = primary.lower()
            if "癌" in _llm_primary and _llm_primary not in _search_all:
                _known_cancer = None
                for _d in [ni.known_diagnosis if ni else "", ni.patient_mentioned_disease if ni else ""]:
                    if _d and "癌" in _d:
                        _known_cancer = _d
                        break
                if _known_cancer and cn_diagnoses[0] != _known_cancer:
                    cn_diagnoses = [_known_cancer] + [d for d in cn_diagnoses if d != _known_cancer]
                    primary = cn_diagnoses[0]
                    print(f"[M1_LLM_GUARD] 保留已知原发癌 {_known_cancer} 为 primary, "
                          f"LLM 输出 {_llm_primary} 作为 secondary")

        # 4. 结构化信息提取
        _raw = raw_for_output or {}
        structured = self._extract_structured_info(ni, _raw, primary, cn_diagnoses, source or "")

        # 5. 诊断关系检测
        relationships = self._detect_diagnosis_relationships(primary, cn_diagnoses, structured, ni)
        structured["united_airway_flag"] = relationships.get("united_airway_flag", False)
        structured["tumor_progression_flag"] = relationships.get("tumor_progression_flag", False)
        structured["infection_progression_flag"] = relationships.get("infection_progression_flag", False)

        # 6. 紧急征和高危鉴别
        emergency = self._detect_emergency(ni, candidates)
        if emergency.get("triggered"):
            diagnosis_status = "REQUEST_MORE_INFO"
            print(f"[M1_EMERGENCY] 紧急征触发: {emergency.get('pattern')}")
        else:
            emergency = {
                "triggered": False, "pattern": "",
                "recommendation": "", "exclusion_diseases": [],
            }

        # 7. 外部搜索标记
        external_search_used = source == "external_search"
        external_search = {
            "used": external_search_used,
            "sources": ["msd_pubmed_online_query"] if external_search_used else [],
            "need_criteria_maintenance": external_search_used,
            "need_human_review": external_search_used or diagnosis_status == "LOW_CONFIDENCE",
            "external_candidate_for_review": external_search_used,
            "reason": "本地库无匹配，需人工审核后入库" if external_search_used else "",
        }
        # 检查 known_diagnosis / patient_mentioned_disease 不在 DB 的情况
        if ni and hasattr(ni, 'known_diagnosis') and ni.known_diagnosis:
            _mention = ni.known_diagnosis.strip()
            _mention_clean = _mention.split("（")[0].split("(")[0].strip().lower()
            _in_db = False
            for entry in (self.db or []):
                cn = entry.get("diseaseName_cn", "").strip().lower()
                en = entry.get("disease_name", "").lower()
                if cn == _mention_clean or en == _mention_clean:
                    _in_db = True
                    break
            if not _in_db:
                external_search["used"] = True
                if "msd_pubmed_online_query" not in external_search["sources"]:
                    external_search["sources"].append("msd_pubmed_online_query")
                external_search["need_human_review"] = True
                diagnosis_status = "NEED_EXTERNAL_SEARCH"
        elif ni and hasattr(ni, 'patient_mentioned_disease') and ni.patient_mentioned_disease:
            _mention = ni.patient_mentioned_disease.strip()
            _mention_clean = _mention.split("（")[0].split("(")[0].strip().lower()
            _in_db = False
            for entry in (self.db or []):
                cn = entry.get("diseaseName_cn", "").strip().lower()
                en = entry.get("disease_name", "").lower()
                if cn == _mention_clean or en == _mention_clean:
                    _in_db = True
                    break
            if not _in_db:
                external_search["used"] = True
                if "msd_pubmed_online_query" not in external_search["sources"]:
                    external_search["sources"].append("msd_pubmed_online_query")
                external_search["need_human_review"] = True
                diagnosis_status = "NEED_EXTERNAL_SEARCH"

        # 8. M2 gate: emergency 或 NEED_MORE_INFO 状态不得进入 M2
        m2_allowed = not emergency.get("triggered") and diagnosis_status not in (
            "REQUEST_MORE_INFO", "NEED_EXTERNAL_SEARCH")

        # 9. top_diagnoses 构建
        top_diagnoses = []
        evidence_for = []
        evidence_against = []
        for i, d in enumerate(cn_diagnoses[:3]):
            detail = {}
            if i < len(_scored_details):
                detail = _scored_details[i]
            elif i < len(_scored_top):
                detail_tmp = _scored_top[i].get("detail", {})
                if isinstance(detail_tmp, dict):
                    detail = detail_tmp
            top_diagnoses.append({
                "disease_name": d,
                "standard_name": d,
                "confidence": "high" if diagnosis_status == "PASS" else "low",
                "score": detail.get("score", 0) if isinstance(detail, dict) else 0,
                "source": source or "",
                "matched_required": [],
                "matched_supportive": [],
                "missing_required": [],
                "contradicted_items": [],
                "evidence_items": [],
                "exclusion_penalty": detail.get("negative_finding_penalty", 0) if isinstance(detail, dict) else 0,
            })
            if isinstance(detail, dict):
                for item in detail.get("matched_signs", []):
                    evidence_for.append(f"{d}: 体征匹配 {item}")
                for item in detail.get("matched_exams", []):
                    evidence_for.append(f"{d}: 检查匹配 {item}")
                if detail.get("negative_finding_penalty", 0) > 0:
                    evidence_against.append(f"{d}: 否定症状扣分 {detail['negative_finding_penalty']}")

        # 从 criteria_struct 收集矛盾证据
        if _scored_top and isinstance(_scored_top[0].get("criteria_struct"), dict):
            cs = _scored_top[0]["criteria_struct"]
            for item in cs.get("contradicted_items", []):
                if isinstance(item, dict):
                    evidence_against.append(
                        f"{top_diagnoses[0]['disease_name'] if top_diagnoses else ''}: 排除项 {item.get('criteria', item)}")
                else:
                    evidence_against.append(
                        f"{top_diagnoses[0]['disease_name'] if top_diagnoses else ''}: {item}")

        exclusion_diseases = emergency.get("exclusion_diseases", [])

        # ── 儿童轻症咳嗽保护（P0-2 统一出口，Skill v2.3 Step 8.5）──
        # 在所有诊断路径之后、最终输出之前执行，确保不被 LLM 或 code 路径覆盖
        _child_cough_triggered = False
        if primary and ni:
            # 条件1：结构化信息或输入文本指示儿童
            _is_child = structured.get("child") or structured.get("age_group") == "child"
            if not _is_child:
                _search_child = " ".join([
                    ni.chief_complaint or "", " ".join(ni.symptoms or []),
                    " ".join(ni.signs or []), ni.known_diagnosis or "",
                ]).lower()
                try:
                    age_val = int(ni.age.replace("岁", "").strip()) if ni.age else 999
                except (ValueError, AttributeError):
                    age_val = 999
                _is_child = age_val < 18 or any(kw in _search_child for kw in ["儿童", "孩子", "小儿", "岁"])

            # 条件2：主诉/症状主要为咳嗽相关
            _cough_text = " ".join([ni.chief_complaint or ""] + (ni.symptoms or []))
            _is_cough_dominant = any(kw in _cough_text for kw in ["咳嗽", "咳", "夜间咳嗽", "痰少", "痰"])

            # 条件3：信息不足（无发热/气促证据，或未提供否定信息但无肺炎证据）
            _has_negatives = any(kw in " ".join(ni.negative_findings or []) for kw in ["无发热", "无气促", "精神尚可"])
            _info_insufficient = _has_negatives or not ni.negative_findings

            # 条件4：缺少肺炎客观证据
            _has_pneumonia_evidence = any(kw in _cough_text or kw in " ".join(ni.labs or []) or kw in " ".join(ni.imaging or []) for kw in ["胸片", "ct", "CT", "啰音", "湿啰音", "支原体", "crp", "pct", "白细胞", "低氧", "SpO2"])

            # 条件5：primary 指向强诊断或咳嗽相关诊断（无明确病名时不得 PASS）
            _cough_diags = ["肺炎", "支原体肺炎", "下呼吸道感染", "感染性发热",
                            "上气道咳嗽综合征", "反流性咳嗽", "咳嗽变异性哮喘",
                            "喉源性咳嗽", "慢性咳嗽", "急性支气管炎", "支气管炎"]
            _strong_diag = any(kw in primary.lower() for kw in _cough_diags)
            # 条件5b：无明确病名时（无已知诊断，无患者提到病名），仅凭咳嗽不应 PASS 或 HIGH CONFIDENCE
            _no_explicit_disease = not (ni.known_diagnosis or "").strip() and not (ni.patient_mentioned_disease or "").strip()
            _cough_only_high_confidence = _is_cough_dominant and _no_explicit_disease and diagnosis_status in ("PASS",)

            if _is_child and _is_cough_dominant and _info_insufficient \
               and not _has_pneumonia_evidence and (_strong_diag or _cough_only_high_confidence):
                _child_cough_triggered = True
                diagnosis_status = "REQUEST_MORE_INFO"
                m2_allowed = False
                print(f"[M1_CHILD_COUGH_PROTECT] 儿童轻症咳嗽缺少肺炎客观证据，降级为 {diagnosis_status}")
                evidence_against.append("儿童轻症咳嗽缺少肺炎客观证据")
                # 补充 missing_info
                parsed_need_info = list(parsed_need_info or [])
                _missing = ["胸片/CT", "肺部听诊", "发热情况", "气促/喘息", "支原体检测"]
                for item in _missing:
                    if item not in parsed_need_info:
                        parsed_need_info.append(item)
                # 补充追问
                parsed_questions = list(parsed_questions or [])
                _followups = [
                    "是否发热？",
                    "是否气促或喘息？",
                    "肺部听诊是否有啰音？",
                    "是否做过胸片或CT？",
                    "是否做过支原体检测？",
                ]
                for q in _followups:
                    if q not in parsed_questions:
                        parsed_questions.append(q)

        need_more_info = diagnosis_status in ("REQUEST_MORE_INFO", "NEED_EXTERNAL_SEARCH")
        require_manual_review = diagnosis_status in ("LOW_CONFIDENCE", "NEED_EXTERNAL_SEARCH")

        # 10. 构建完整返回结构
        return DiagnosisResultDict({
            "status": "DIAGNOSIS_READY" if not need_more_info else "NEED_MORE_INFO",
            "primary_diagnosis": primary,
            "secondary_diagnoses": [d for d in cn_diagnoses[1:]] if len(cn_diagnoses) > 1 else [],
            "comorbidities": [],
            "complications": [],
            "diagnosis_relationship": {
                "description": relationships.get("diagnosis_relationship", ""),
                "united_airway_flag": relationships.get("united_airway_flag", False),
                "tumor_progression_flag": relationships.get("tumor_progression_flag", False),
                "infection_progression_flag": relationships.get("infection_progression_flag", False),
            },
            "top_diagnoses": top_diagnoses,
            "current_top_candidates": current_top_candidates or (_scored_diseases[:3] if _scored_diseases else []),
            "cannot_decide_because": cannot_decide_because or "",
            "questions": parsed_questions or [],
            "missing_info": parsed_need_info or [],
            "exclusion_diseases": exclusion_diseases,
            "diagnosis_status": diagnosis_status,
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
            "need_more_info": need_more_info,
            "require_manual_review": require_manual_review,
            "followup_questions": parsed_questions or [],
            "external_search": external_search,
            "structured_info": structured,
            "emergency_alert": emergency,
            "m2_payload": self._build_m2_payload(
                ni=ni,
                primary=primary,
                cn_diagnoses=cn_diagnoses,
                top_diagnoses=top_diagnoses if m2_allowed else [],
                diagnosis_status=diagnosis_status,
                relationships=relationships,
                structured=structured,
                evidence_for=evidence_for,
                evidence_against=evidence_against,
            ),
            "source": source or "",
            "cache_hit": False,
        })

    def _build_m2_payload(
        self,
        *,
        ni,
        primary: str,
        cn_diagnoses: list,
        top_diagnoses: list,
        diagnosis_status: str,
        relationships: dict,
        structured: dict,
        evidence_for: list,
        evidence_against: list,
    ) -> Dict:
        """Stable M1→M2 handoff payload with normalized list fields."""
        from services.m1_m2_bridge import extract_tongue_pulse, normalize_string_list

        signs = normalize_string_list(getattr(ni, "signs", []))
        symptoms = normalize_string_list(getattr(ni, "symptoms", []))
        labs = normalize_string_list(getattr(ni, "labs", []))
        imaging = normalize_string_list(getattr(ni, "imaging", []))
        negative_findings = normalize_string_list(getattr(ni, "negative_findings", []))
        tongue, pulse, _ = extract_tongue_pulse(signs, symptoms)
        secondary = [d for d in cn_diagnoses[1:]] if len(cn_diagnoses) > 1 else []
        comorbidities = normalize_string_list(structured.get("complications_or_comorbidities", []))

        return {
            "primary_disease": primary,
            "primary_diagnosis": primary,
            "standard_disease_name": primary,
            "top_diagnoses": top_diagnoses,
            "secondary_diseases": secondary,
            "secondary_diagnoses": secondary,
            "comorbidities": comorbidities,
            "complications": [],
            "diagnosis_status": diagnosis_status,
            "diagnosis_relationship": {
                "description": relationships.get("diagnosis_relationship", ""),
                "united_airway_flag": relationships.get("united_airway_flag", False),
                "tumor_progression_flag": relationships.get("tumor_progression_flag", False),
                "infection_progression_flag": relationships.get("infection_progression_flag", False),
            },
            "structured_info": structured,
            "risk_flags": structured.get("risk_flags", []),
            "evidence_trace": evidence_for + evidence_against,
            "symptoms": symptoms,
            "signs": signs,
            "tongue": tongue,
            "pulse": pulse,
            "labs": labs,
            "imaging": imaging,
            "negative_findings": negative_findings,
            "age": getattr(ni, "age", "") if ni else "",
            "weight": "",
        }

    def diagnose_json(self, raw_input: Dict, _followup_answers: Optional[Dict] = None) -> str:
        """诊断并返回 JSON 字符串（Agent 模式）
        
        外部调用方可检查返回结果中的 status 字段：
        - 'DIAGNOSIS_READY' → diagnosis_calibration 包含诊断结果
        - 'NEED_MORE_INFO' → questions 包含追问问题
        - 外部拿到追问后，可以收集答案传入 _followup_answers 重新调用
        """
        result = self.diagnose(raw_input, _followup_answers=_followup_answers)
        return json.dumps(result, ensure_ascii=False, indent=2)


# ── 系统关键词映射 ──────────────────────────────────

# ── 泛化词降权列表（这些词太泛化，命中时降权）───────────

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

    print("=" * 60)
    print("M1 Agent 诊断引擎 v6 — 工具集 + 追问循环测试")
    print("=" * 60)

    # ── 测试工具1：查询本地诊断标准 ──
    print("\n【工具1测试】查询本地诊断标准")
    tool1 = engine._query_local_criteria("儿童急性扁桃体炎")
    print(f"  status: {tool1['status']}")
    if tool1['status'] == 'FOUND':
        print(f"  病名: {tool1['diseaseName_cn']}")
        print(f"  诊断标准: {len(tool1['diagnostic_criteria'])} 条")
        for d in tool1['diagnostic_criteria'][:3]:
            print(f"    - {d[:70]}")
        print(f"  鉴别诊断: {len(tool1['differential_diagnosis'])} 个")

    # ── 测试工具1：未找到 ──
    print("\n【工具1测试】查询不存在的疾病")
    tool1_miss = engine._query_local_criteria("非典型肺炎（特别版）")
    print(f"  status: {tool1_miss['status']}")

    # ── 测试工具2：在线查询（LLM 模拟查默沙东） ──
    print("\n【工具2测试】在线查询默沙东（本地已有则直接用缓存）")
    tool2 = engine._get_cached_criteria("儿童急性扁桃体炎", "msd")
    print(f"  status: {tool2['status']}")
    if tool2['status'] == 'FOUND':
        print(f"  来源: {tool2.get('source','')}")
        print(f"  诊断标准: {len(tool2['diagnostic_criteria'])} 条")

    # ── 测试用例 1：信息充分 → 直接诊断 ──
    print("\n【Agent 测试1】信息充分 → 直接诊断")
    test1 = {
        "patient_mentioned_disease": "儿童急性扁桃体炎",
        "chief_complaint": "发热咽喉痛3天",
        "symptoms": ["发热", "咽喉痛", "吞咽痛"],
        "signs": ["扁桃体充血肿大", "咽部充血"],
        "labs": ["血常规示白细胞升高"],
        "imaging": [],
        "negative_findings": [],
        "duration": "3天",
        "onset": "急性",
    }
    r1 = engine.diagnose_json(test1)
    print(r1[:500] + "..." if len(r1) > 500 else r1)

    # ── 测试用例 2：信息不足 → 追问 ──
    print("\n【Agent 测试2】信息不足（仅有主诉）→ 追问")
    test2 = {
        "patient_mentioned_disease": "",
        "chief_complaint": "肚子痛",
        "symptoms": ["腹痛"],
        "signs": [],
        "labs": [],
        "imaging": [],
        "negative_findings": [],
        "duration": "",
        "onset": "",
    }
    r2 = engine.diagnose_json(test2)
    r2_data = json.loads(r2)
    if r2_data.get("status") == "NEED_MORE_INFO":
        print(f"  追问原因: {r2_data['cannot_decide_because'][:80]}...")
        for q in r2_data.get("questions", []):
            print(f"  Q: {q}")
        # 模拟回答
        print("\n  → 外部收集答案后重新调用...")
        test2["symptoms"] = ["腹痛", "右下腹压痛", "发热37.8°C"]
        test2["signs"] = ["麦氏点压痛", "反跳痛"]
        test2["duration"] = "2天"
        test2["onset"] = "急性"
        r3 = engine.diagnose_json(test2, _followup_answers={
            "患者有无右下腹压痛？": "有，麦氏点明显压痛",
            "体温多少？": "37.8°C",
        })
        r3_data = json.loads(r3)
        if r3_data.get("status") == "DIAGNOSIS_READY":
            print(f"  诊断: {r3_data['diagnosis_calibration']['calibrated_diagnosis']}")
        elif r3_data.get("status") == "NEED_MORE_INFO":
            print(f"  仍需追问: {r3_data['questions']}")

    # ── 测试真实病例：抽动症 ──
    print("\n【Agent 测试3】抽动症真实病例")
    test3 = {
        "patient_mentioned_disease": "抽动症",
        "chief_complaint": "眨眼频繁加重半月",
        "symptoms": ["频繁眨眼", "鼻涕鼻塞少", "张口呼吸", "口气多", "睡眠不安", "磨牙"],
        "signs": ["过敏性鼻炎史", "苔薄黄", "舌红"],
        "labs": [],
        "imaging": [],
        "negative_findings": ["无发热", "无咳嗽"],
        "duration": "半月加重",
        "onset": "亚急性",
        "age": "6岁",
        "gender": "男",
    }
    # 先用工具1查本地诊断标准
    local_check = engine._query_local_criteria("抽动症")
    print(f"  本地库查抽动症: {local_check['status']}")
    if local_check['status'] == 'FOUND':
        print(f"  标准名: {local_check['diseaseName_cn']}")

    r3 = engine.diagnose_json(test3)
    r3_data = json.loads(r3)
    if r3_data.get("status") == "NEED_MORE_INFO":
        print(f"  追问原因: {r3_data['cannot_decide_because'][:80]}...")
        for q in r3_data["questions"]:
            print(f"  Q: {q}")
        print("\n  → 外部收集答案后重新调用...")
        r3_2 = engine.diagnose_json(test3, _followup_answers={
            r3_data["questions"][0]: "有，偶尔甩头",
            r3_data["questions"][1]: "看动画片专心时减轻，被提醒时加重",
        })
        r3_2_data = json.loads(r3_2)
        if r3_2_data.get("status") == "DIAGNOSIS_READY":
            print(f"  诊断: {r3_2_data['diagnosis_calibration']['calibrated_diagnosis']}")
        else:
            print(f"  结果: {json.dumps(r3_2_data, ensure_ascii=False)[:200]}")
    elif r3_data.get("status") == "DIAGNOSIS_READY":
        print(f"  诊断: {r3_data['diagnosis_calibration']['calibrated_diagnosis']}")

    print("\n" + "=" * 60)
    print("所有 Agent 工具测试通过 ✅")
