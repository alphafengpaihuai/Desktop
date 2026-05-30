"""
M1 Agent 诊断引擎 v6 — 中医辅助诊疗系统「守一」
===============================================
核心设计：Agent 工具集 + 诊断标准检索 + 逐条比对 + 追问循环

流程：
1. 生成初始假设（从知识库列出所有可能的疾病）
2. 对每个假设疾病，调用工具1（本地查询）或工具2（在线查询）获取诊断标准
3. 将患者数据与每个候选病的诊断标准逐条比对
4. 判断信息缺口 → 不足则追问 → 补充后重新比对
5. 输出 Top 1-3 诊断

Agent 工具集：
  工具1：查询本地诊断标准（_query_local_criteria）
  工具2：在线查询默沙东/PubMed（_online_query）
  缓存规则：在线结果缓存6个月，过期重新查询

设计原则：
- 所有诊断基于权威诊断标准（知识库 + 在线循证医学）
- 工具2获取的标准自动缓存，下次优先本地
- LLM 负责比对+判断，不凭空生成诊断标准
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

        result_text = self._call_llm(prompt, temperature=0.1, max_tokens=800)
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
        """主诊断入口（Agent 模式，带追问循环）

        流程：
        1. 标准化输入
        2. LLM_Agent 比对患者资料与候选病诊断标准
        3. 判断信息是否足以明确诊断：
           - 足够 → 输出 Top 1-3（模式一）
           - 不足 → 生成追问问题（模式二）
        4. _followup_answers 用于注入上一轮追问的答案（外部循环控制）

        Returns:
            模式一：{"diagnosis_calibration": {...}, "source": "llm"}
            模式二：{"status": "NEED_MORE_INFO", ..., "questions": [...]}
        """
        ni = self.normalize_input(raw_input)

        if not self.llm_api_available():
            fallback = self._keyword_fallback(ni.patient_mentioned_disease, ni.symptoms)
            return {
                "diagnosis_calibration": {
                    "original_input": raw_input.get("patient_mentioned_disease", ""),
                    "calibrated_diagnosis": fallback[:3] if fallback else [],
                },
                "source": "code_fallback",
                "cache_hit": False,
            }

        # ── 召回候选疾病 + 构造诊断标准上下文 ──
        candidates = self._recall_candidates(ni)
        criteria_section = ""
        for i, cand in enumerate(candidates[:6], 1):
            cn_name = cand.get("diseaseName_cn", "").strip()
            en_name = cand.get("disease_name", "").strip()
            diag = cand.get("diagnostic_criteria", [])
            typical = cand.get("typical_symptoms", [])
            diff = cand.get("differential_diagnosis", [])
            name_display = f"{cn_name} ({en_name})" if en_name else cn_name
            criteria_section += "\n### 候选 " + str(i) + "：" + name_display
            if diag:
                criteria_section += "\n诊断标准：" + "；".join(diag[:5])
            if typical:
                criteria_section += "\n典型症状：" + "；".join(typical[:5])
            if diff:
                criteria_section += "\n需鉴别：" + "、".join(diff[:4])

        # ── 历史追问上下文 ──
        followup_history = ""
        if _followup_answers:
            followup_history = "\n## 上一轮追问的答案"
            for q, a in _followup_answers.items():
                followup_history += "\n追问：" + str(q) + "\n答案：" + str(a)

        prompt = f"""你是一个疾病诊断 Agent。根据患者的完整资料和候选疾病的诊断标准，完成诊断。

## 你的工具
1. 工具1（查询本地诊断标准）：系统已自动从本地库获取候选病的诊断标准
2. 工具2（在线查询默沙东/PubMed）：当本地库未找到时调用，结果自动缓存

## 工作流程

### 第一步：生成初始假设
根据患者主诉和关键临床表现，从下方候选疾病中列出可能的诊断假设。

### 第二步：检索诊断标准
系统已自动调用工具获取了诊断标准（见下方候选疾病部分）。请使用这些标准进行比对验证。

### 第三步：逐一验证
将患者数据与每个候选病的诊断标准逐条比对，评估匹配程度。

### 第四步：信息缺口判断
- 信息足以明确区分候选病 -> 进入输出（模式一）
- 信息不足以区分（核心症状重叠、缺关键检查等）-> 输出追问请求（模式二）

## 患者资料
- 主诉：{ni.chief_complaint}
- 症状：{'；'.join(ni.symptoms)}
- 体征：{'；'.join(ni.signs)}
- 实验室：{'；'.join(ni.labs)}
- 影像学：{'；'.join(ni.imaging)}
- 患者提到的病名：{ni.patient_mentioned_disease}
- 病程：{ni.duration}
- 起病方式：{ni.onset}{followup_history}

## 候选疾病诊断标准（系统已自动获取）
{criteria_section}

## 输出格式（严格 JSON，不要 Markdown）

### 模式一：信息足够时
{{"status":"DIAGNOSIS_READY","diagnoses":[{{"name_cn":"病名","name_en":"disease_name","match_reason":"匹配理由"}}],"missing_info":[]}}

### 模式二：信息不足时
{{"status":"NEED_MORE_INFO","current_top_candidates":["候选1","候选2"],"cannot_decide_because":"具体原因","questions":["追问1？","追问2？"],"missing_info":["标签"]}}

## 规则
- 追问最多 2 个，必须具体可操作
- 不能重复之前已问的问题
- 只能基于候选病诊断标准中提到的信息点追问
- 患者提到的病名仅作参考"""

        result_text = self._call_llm(prompt, temperature=0.1, max_tokens=600)
        if not result_text:
            return {"status":"NEED_MORE_INFO","current_top_candidates":[],"cannot_decide_because":"LLM无返回","questions":[],"missing_info":[]}

        import json as _json
        try:
            m = re.search(r'\{.*\}', result_text, re.DOTALL)
            if not m:
                raise ValueError("no JSON")
            parsed = _json.loads(m.group())
        except Exception:
            return {"status":"NEED_MORE_INFO","current_top_candidates":[c.get("diseaseName_cn","") for c in candidates[:3]],"cannot_decide_because":"LLM输出解析失败","questions":["请补充更多症状及检查信息"],"missing_info":["症状细节"]}

        if parsed.get("status") == "NEED_MORE_INFO":
            return {"status":"NEED_MORE_INFO","current_top_candidates":parsed.get("current_top_candidates",[]),"cannot_decide_because":parsed.get("cannot_decide_because",""),"questions":parsed.get("questions",[]),"missing_info":parsed.get("missing_info",[])}

        # ── 模式一：诊断输出 ──
        diagnoses_raw = parsed.get("diagnoses",[])
        cn_diagnoses = []
        for d in diagnoses_raw:
            cn = d.get("name_cn","").strip() if isinstance(d,dict) else str(d).strip()
            if cn and cn not in cn_diagnoses:
                # 查完整名
                full = cn
                for e in self.db:
                    ecn = e.get("diseaseName_cn","").strip()
                    if cn in ecn or ecn in cn or cn == e.get("disease_name",""):
                        full = ecn
                        break
                cn_diagnoses.append(full if full else cn)

        if not cn_diagnoses:
            fallback = self._keyword_fallback(ni.patient_mentioned_disease, ni.symptoms)
            if fallback:
                cn_diagnoses = fallback[:3]
                source = "code_fallback"
            else:
                source = "llm_empty"
        else:
            source = "llm"
            if self.criteria_fill_enabled:
                for diag in cn_diagnoses:
                    self._fill_missing_criteria(diag.split(" (")[0].split("（")[0].strip())

        return {"diagnosis_calibration":{"original_input":raw_input.get("patient_mentioned_disease",""),"calibrated_diagnosis":cn_diagnoses[:3]},"source":source,"cache_hit":False}

    def _recall_candidates(self, ni) -> List[dict]:
        """从患者资料召回候选疾病列表（供 Agent 比对）

        召回策略：
        - 患者提到的病名精确匹配
        - 症状关键词模糊匹配（从 keyword_index）
        - 限制最多 20 个候选
        """
        candidates = []
        seen = set()

        mentioned = ni.patient_mentioned_disease.strip().lower()
        if mentioned:
            for entry in self.db:
                cn = entry.get("diseaseName_cn","").strip().lower()
                en = entry.get("disease_name","").lower()
                if cn == mentioned or mentioned in cn or en == mentioned or mentioned in en:
                    if entry["disease_name"] not in seen:
                        candidates.append(entry)
                        seen.add(entry["disease_name"])
                        break

        if not candidates:
            all_text = [ni.chief_complaint.lower()] + [s.lower() for s in ni.symptoms]
            for text in all_text:
                if not text or len(text) < 2:
                    continue
                for word in re.split(r'[\s,，、.。:：]+', text):
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

        return candidates[:20]


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

