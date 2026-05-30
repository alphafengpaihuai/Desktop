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

        raw = self._call_llm(prompt, temperature=0.1, max_tokens=800)
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

    def _keyword_fallback(self, mentioned: str, symptoms: List[str]) -> List[str]:
        """代码兜底：当 LLM 不可用时，用关键词+别名映射模糊搜索匹配疾病
        
        策略：
        1. 口语化病名→标准名映射表（如"外眼睑炎"→"睑缘炎"）
        2. 关键词模糊匹配（支持子串匹配）
        3. 症状关键词辅助评分
        """
        # 口语化病名→标准名映射（覆盖常见不匹配场景）
        slang_map = {
            "外眼睑炎": "睑缘炎",
            "眼睛发炎": "结膜炎",
            "角膜炎": "角膜炎",
            "麦粒肿": "睑腺炎",
            "针眼": "睑腺炎",
            "白内障": "白内障",
            "青光眼": "青光眼",
            "老花眼": "老花眼",
            "糖尿病": "糖尿病",
            "高血压": "高血压",
            "感冒": "上呼吸道感染",
            "拉肚子": "腹泻",
            "发烧": "发热",
        }
        
        # 构建搜索关键词
        keywords = set()
        search_text = (mentioned or "") + " " + " ".join(symptoms or [])
        search_lower = search_text.lower()
        
        # 添加映射后的标准名
        for slang, standard in slang_map.items():
            if slang in search_lower:
                keywords.add(standard.lower())
        
        # 添加原词中的有意义的词（≥2个字）
        for w in re.split(r'[\s()（）,，、.:：]+', search_lower):
            if len(w) >= 2:
                keywords.add(w)
        
        scored = []
        for entry in self.db:
            name = entry["disease_name"].lower()
            cn = entry.get("diseaseName_cn", entry["disease_name"]).lower()
            cn_part = cn.split(" (")[0].split("（")[0]  # 只取中文部分
            
            score = 0
            for kw in keywords:
                # 中文部分子串匹配（最高权重）
                if len(kw) >= 2 and kw in cn_part:
                    score += 3
                # 英文部分子串匹配
                if len(kw) >= 3 and kw in name:
                    score += 2
                # 完整中文名匹配
                if cn_part == kw:
                    score += 5
                # 症状关键词（较短的词）匹配
                if 2 <= len(kw) <= 3 and kw in cn_part:
                    score += 2
            
            if score > 0:
                display = entry.get("diseaseName_cn", entry["disease_name"])
                scored.append((score, display))
        
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [s[1] for s in scored[:3]]

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
        2. LLM 从疾病列表中直接选出 Top 1-3（始终有 LLM 深度参与）

        Returns:
            JSON 格式的诊断结果
        """
        ni = self.normalize_input(raw_input)

        if not self.llm_api_available():
            # LLM 不可用 → 走代码兜底关键词匹配
            fallback = self._keyword_fallback(ni.patient_mentioned_disease, ni.symptoms)
            return {
                "diagnosis_calibration": {
                    "original_input": raw_input.get("patient_mentioned_disease", ""),
                    "calibrated_diagnosis": fallback[:3] if fallback else [],
                },
                "source": "code_fallback",
                "cache_hit": False,
            }

        disease_list = "\n".join([
            f'{e.get("diseaseName_cn", e["disease_name"])} ({e["disease_name"]})'
            for e in self.db if e.get("diseaseName_cn")
        ])

        prompt = f"""你是一个疾病诊断助手。根据患者的完整资料，从以下疾病列表中选出最符合的 1-3 个疾病名称。

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{'；'.join(ni.symptoms)}
- 体征：{'；'.join(ni.signs)}
- 实验室：{'；'.join(ni.labs)}
- 影像学：{'；'.join(ni.imaging)}
- 患者提到的病名：{ni.patient_mentioned_disease}
- 病程：{ni.duration}
- 起病方式：{ni.onset}

## 疾病列表（共 {len(self.db)} 种）
{disease_list}

## 要求
1. 逐一分析患者资料，选出最符合的 Top 1-3 个疾病
2. 患者提到的病名仅作参考，不作为诊断依据
3. 输出格式：中文病名（英文病名），每行一个，按匹配度从高到低排列
4. 只输出疾病全称，不输出解释、不输出置信度、不输出推理过程
5. 信息不足时输出"无匹配" """

        result_text = self._call_llm(prompt, temperature=0.1, max_tokens=300)

        diagnoses = []
        if result_text:
            for line in result_text.strip().split("\n"):
                line = line.strip().strip('\'"').strip("-").strip()
                if not line or line == "无匹配":
                    continue
                parsed = self._parse_disease_name(line)
                if parsed and parsed not in diagnoses:
                    diagnoses.append(parsed)

        # 输出转中文（取纯中文名）
        def _to_cn_name(d: str) -> str:
            dl = d.lower()
            # 直接匹配英文名
            if dl in self.name_index:
                cn = self.name_index[dl].get("diseaseName_cn", "")
            # 直接匹配中文名
            elif dl in self.cn_name_index:
                cn = self.cn_name_index[dl].get("diseaseName_cn", "")
            else:
                # 部分匹配：搜 name_index 中是否包含
                for name, entry in self.name_index.items():
                    if dl in name or name in dl:
                        cn = entry.get("diseaseName_cn", "")
                        break
                else:
                    cn = d
            # 从 "睑缘炎 (Blepharitis)" 中提取纯中文名
            cn = cn.split(" (")[0].split("（")[0] if cn else d
            return cn.strip()

        cn_diagnoses = [_to_cn_name(d) for d in diagnoses[:3]]
        # M1→M2 接口：如知识库以 "中文名 (英文名)" 为键，保留完整格式
        cn_diagnoses_full = []
        for d in diagnoses[:3]:
            dl = d.lower()
            full_name = None
            if dl in self.name_index:
                full_name = self.name_index[dl].get("diseaseName_cn", "")
            else:
                for name, entry in self.name_index.items():
                    if dl in name or name in dl:
                        full_name = entry.get("diseaseName_cn", "")
                        break
            cn_diagnoses_full.append(full_name if full_name else d)
        # 兼容：既输出纯中文名（m2 旧版），也提供完整名
        # 将完整名写回 calibrated_diagnosis
        # 因为知识库 key 是完整格式如 "睑缘炎 (Blepharitis)"
        if all(cn_diagnoses_full):
            cn_diagnoses = cn_diagnoses_full

        # ── LLM 兜底：如果 LLM 未返回有效结果，用代码做模糊搜索 ──
        if not cn_diagnoses:
            fallback = self._keyword_fallback(ni.patient_mentioned_disease, ni.symptoms)
            if fallback:
                cn_diagnoses = fallback[:3]
                cn_diagnoses_full = cn_diagnoses
                source = "code_fallback"
            else:
                source = "llm_empty"
        else:
            source = "llm"

        # ── 诊断标准自动补齐 ──
        # 如果诊断出疾病且该疾病缺少 diagnostic_criteria，触发 LLM 查询补齐
        if self.criteria_fill_enabled and cn_diagnoses and source == "llm":
            for diag in cn_diagnoses:
                diag_clean = diag.split(" (")[0].split("（")[0].strip()
                self._fill_missing_criteria(diag_clean)

        return {
            "diagnosis_calibration": {
                "original_input": raw_input.get("patient_mentioned_disease", ""),
                "calibrated_diagnosis": cn_diagnoses,
            },
            "source": source,
            "cache_hit": False,
        }

    def diagnose_json(self, raw_input: Dict) -> str:
        """诊断并返回 JSON 字符串"""
        result = self.diagnose(raw_input)
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
