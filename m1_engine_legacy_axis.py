"""
M1 诊断推理引擎 — 中医辅助诊疗系统「守一」
============================================
功能：、Primary Formula Entry Disease 选择、Uncovered Targets 识别
严格限制：仅输出西医诊断内容，禁止任何中医输出

v2 新增：
- API 语义增强层（对低匹配度的中文症状调用外部 LLM 做语义分析）
- 本地语义缓存（相同症状组合避免重复 API 调用）
"""

import json
import os
import hashlib
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ── 数据类 ──────────────────────────────────────────────

@dataclass
class AxeMatch:
    """单个病理轴匹配结果"""
    axis: str
    db_weight: float
    db_role: str
    db_confidence: str
    findings: List[str] = field(default_factory=list)
    covered: bool = False
    match_score: float = 0.0

@dataclass
class CandidateDiagnosis:
    """候选诊断"""
    disease_name: str
    entry_type: str
    overall_score: float
    primary_axes_covered: float
    total_primary_axes: int
    uncovered_primary_axes: List[str]
    uncovered_axes: List[AxeMatch]
    covered_axes: List[AxeMatch]
    primary_findings: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    needs_external_check: List[str] = field(default_factory=list)
    final_rank_score: float = 0.0
    reason: str = ""

@dataclass
class DiagnosisResult:
    """完整诊断结果"""
    query_disease: str
    query_symptoms: List[str]
    matched_disease: Optional[CandidateDiagnosis] = None
    differential_list: List[CandidateDiagnosis] = field(default_factory=list)
    no_match: bool = False
    not_allowed: bool = False
    error: Optional[str] = None
    red_flags: List[str] = field(default_factory=list)


# ── 引擎核心 ──────────────────────────────────────────────

class M1DiagnosisEngine:
    """M1 西医诊断推理引擎"""

    # 病理轴角色权重系数（用于评分）
    ROLE_WEIGHT_MULTIPLIER = {
        "primary": 1.0,
        "location": 0.85,
        "secondary": 0.7,
        "pathogenesis": 0.65,
        "trigger": 0.55,
        "risk": 0.4,
        "stage_related": 0.3,
    }

    # 置信度权重系数
    CONFIDENCE_MULTIPLIER = {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.4,
    }

    def __init__(self, db_path: Optional[str] = None):
        """初始化引擎，加载数据库"""
        if db_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(script_dir, "diseases_core.json")

        with open(db_path, "r", encoding="utf-8") as f:
            self.db: List[dict] = json.load(f)
        self.db_path_origin = db_path

        # 加载中英文名称映射
        self.cn_to_en: Dict[str, str] = {}
        mapping_path = os.path.join(os.path.dirname(db_path), "m1_name_mapping.json")
        if os.path.exists(mapping_path):
            with open(mapping_path, "r", encoding="utf-8") as f:
                self.cn_to_en = json.load(f)

        # 建立索引：疾病名 → 条目
        self.name_index: Dict[str, dict] = {}
        self.keyword_index: Dict[str, List[dict]] = {}

        for entry in self.db:
            name = entry["disease_name"].lower()
            self.name_index[name] = entry
            for word in name.replace("(", " ").replace(")", " ").replace("/", " ").split():
                if len(word) > 2:
                    if word not in self.keyword_index:
                        self.keyword_index[word] = []
                    self.keyword_index[word].append(entry)

        # 可用病理轴列表
        self.available_axes: set = set()
        for entry in self.db:
            for axe in entry.get("axes", []):
                if axe.get("axis"):
                    self.available_axes.add(axe["axis"])

        # ── 语义缓存（API 增强结果缓存） ──
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.semantic_cache_path = os.path.join(script_dir, "m1_semantic_cache.json")
        self.semantic_cache: Dict[str, dict] = {}
        if os.path.exists(self.semantic_cache_path):
            try:
                with open(self.semantic_cache_path, "r", encoding="utf-8") as f:
                    self.semantic_cache = json.load(f)
            except:
                self.semantic_cache = {}
        self.api_enabled = True  # 可外部设置为 False 来关闭 API
        # ── 疾病别名映射表（临床俗称 → 标准名） ──
        self.alias_map_path = os.path.join(script_dir, "m1_alias_map.json")
        self.alias_map: Dict[str, str] = {}
        if os.path.exists(self.alias_map_path):
            try:
                with open(self.alias_map_path, "r", encoding="utf-8") as f:
                    self.alias_map = json.load(f)
            except:
                self.alias_map = {}
        # ── 口语化映射库（口语→标准术语映射，与 diseases_core 分离） ──
        self.colloquial_map_path = os.path.join(script_dir, "m1_colloquial_mapping.json")
        self.colloquial_map: Dict[str, dict] = {}
        if os.path.exists(self.colloquial_map_path):
            try:
                with open(self.colloquial_map_path, "r", encoding="utf-8") as f:
                    self.colloquial_map = json.load(f)
            except:
                self.colloquial_map = {}
        # 自动检测环境变量中的 API key（支持 DeepSeek / Gemini 双 provider）
        self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.deepseek_api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.llm_provider = os.environ.get("LLM_PROVIDER", "deepseek")  # "deepseek" | "gemini"
        # 如果设置了 GEMINI_API_KEY 且未显式指定 provider，自动切换到 gemini
        if self.gemini_api_key and not os.environ.get("LLM_PROVIDER") and not self.deepseek_api_key:
            self.llm_provider = "gemini"
        # ── 知识库上传配置（语义分析结果入库，供所有用户共享） ──
        self.knowledge_base_url = os.environ.get("KNOWLEDGE_BASE_URL", "")
        self.knowledge_base_token = os.environ.get("KNOWLEDGE_BASE_TOKEN", "")

    # ── 查找部分 ───────────────────────────────────────

    def find_disease(self, disease_name: str, allow_msd_fallback: bool = True) -> Optional[dict]:
        """精确查找疾病（支持中英文 + 别名映射 + 默沙东回退）
        allow_msd_fallback：是否允许默沙东回退（鉴别诊断时禁用，避免大量API调用）
        """
        key = disease_name.strip().lower()
        # 1. 直接匹配英文名
        if key in self.name_index:
            return self.name_index[key]
        # 2. 中文名映射
        if key in self.cn_to_en:
            en_key = self.cn_to_en[key].lower()
            if en_key in self.name_index:
                return self.name_index[en_key]
        # 3. 别名映射（临床俗称 → 标准名）
        if hasattr(self, 'alias_map') and self.alias_map:
            mapped = self.alias_map.get(key)
            if mapped:
                # 可能映射到英文名
                mk = mapped.lower()
                if mk in self.name_index:
                    return self.name_index[mk]
                # 可能映射到中文名
                if mk in self.cn_to_en:
                    en_key = self.cn_to_en[mk].lower()
                    if en_key in self.name_index:
                        return self.name_index[en_key]
                # 在库里搜中文名匹配
                for entry in self.db:
                    cn = entry.get('diseaseName_cn', '').lower()
                    if cn == mk:
                        return entry
        # 4. 回退：查默沙东（仅当启用了 API 且允许回退时触发）
        if not allow_msd_fallback:
            return None
        if self.api_enabled and hasattr(self, '_try_fetch_from_msd') and allow_msd_fallback:
            # 禁用默沙东临时添加（避免临时条目干扰诊断）
            pass
        return None

    def search_diseases(self, query: str) -> List[dict]:
        """模糊搜索疾病（支持中英文）"""
        query = query.strip().lower()
        if not query:
            return []
        
        # 快速过滤：纯数字、太短、无意义字符组合 ≠ 疾病名
        if len(query) < 3:
            return []
        alpha_chars = [c for c in query if c.isalpha()]
        if not alpha_chars:
            return []

        if query in self.name_index:
            return [self.name_index[query]]

        results: List[dict] = []
        seen = set()

        # 部分匹配（中英文都适用）—— 要求实质性名称重叠
        query_words = set(q for q in query.replace("(", " ").replace(")", " ").replace("/", " ").split() if len(q) >= 3)
        for name, entry in self.name_index.items():
            if query in name or name in query:
                # 对于长名称（>10字符），检查重叠是否足够有意义
                if len(query) >= 10 and len(name) >= 10:
                    overlap = set(query) & set(name)
                    # 至少 50% 的字符重叠
                    if len(overlap) >= min(len(set(query)), len(set(name))) * 0.5 or query[:6] in name or name[:6] in query:
                        if id(entry) not in seen:
                            results.append(entry)
                            seen.add(id(entry))
                else:
                    if id(entry) not in seen:
                        results.append(entry)
                        seen.add(id(entry))

        # 英文关键词匹配
        query_words = query.replace("(", " ").replace(")", " ").replace("/", " ").split()
        # 额外拆分：将连在一起的字母数字拆开（如"disease12345"→"disease","12345"）
        refined_words = []
        for w in query_words:
            parts = []
            current = ""
            for c in w:
                if c.isalpha() and (not current or current[-1].isalpha()):
                    current += c
                elif c.isdigit() and (not current or current[-1].isdigit()):
                    current += c
                else:
                    if current: parts.append(current)
                    current = c
            if current: parts.append(current)
            refined_words.extend(parts)
        query_words = refined_words
        
        # 疾病名中常见"泛称后缀" — 如果查询词几乎等价于这些泛称则跳过
        GENERIC_NAME_TOKENS = {'disease', 'syndrome', 'disorder', 'infection', 'condition', 'failure'}
        for word in query_words:
            if len(word) <= 2:
                continue
            # 跳过纯数字/标点关键词
            if all(c.isdigit() or not c.isalnum() for c in word):
                continue
            # 跳过本身是泛称词的查询（如仅输入"disease"）
            if word in GENERIC_NAME_TOKENS:
                continue
            for keyword, entries in self.keyword_index.items():
                if len(keyword) < 3:
                    continue
                # 双向包含匹配，但限制长度比例（防止"nonexistentdisease"因含"disease"而误匹配）
                if word in keyword:
                    # 查询词 ≤ 关键词（如"stroke" in "Stroke Rehabilitation"）
                    if len(keyword) >= len(word) * 0.8:
                        for entry in entries:
                            if id(entry) not in seen:
                                results.append(entry)
                                seen.add(id(entry))
                elif keyword in word:
                    # 关键词在查询词中 — 关键词必须占查询词的大部分
                    overlap_ratio = len(keyword) / len(word)
                    if overlap_ratio >= 0.6:  # 查询词大部分由该关键词组成
                        for entry in entries:
                            if id(entry) not in seen:
                                results.append(entry)
                                seen.add(id(entry))

        # 中文搜索增强：通过映射反向查找 + 字符模糊匹配
        if any('\u4e00' <= c <= '\u9fff' for c in query):
            # 1. 通过中文映射反向查找
            for cn_name, en_name in self.cn_to_en.items():
                if id(self.name_index.get(en_name.lower())) in seen:
                    continue
                if query in cn_name or cn_name in query:
                    entry = self.name_index.get(en_name.lower())
                    if entry and id(entry) not in seen:
                        results.append(entry)
                        seen.add(id(entry))

            # 2. 字符模糊匹配（处理中文分词特性）
            query_chars = set(c for c in query if '\u4e00' <= c <= '\u9fff')
            if query_chars:
                for name, entry in self.name_index.items():
                    if id(entry) in seen:
                        continue
                    name_chars = set(c for c in name if '\u4e00' <= c <= '\u9fff')
                    if query_chars & name_chars and len(query_chars & name_chars) >= max(2, len(query_chars) // 2):
                        results.append(entry)
                        seen.add(id(entry))

        return results

    # ── 核心诊断逻辑 ──────────────────────────────────

    def diagnose(
        self,
        disease_name: str,
        symptoms: List[str],
        max_differentials: int = 5,
        match_threshold: float = 0.15,
    ) -> DiagnosisResult:
        """主诊断入口"""
        result = DiagnosisResult(
            query_disease=disease_name,
            query_symptoms=symptoms,
        )

        disease_entry = self.find_disease(disease_name)

        if disease_entry is None:
            suggestions = self.search_diseases(disease_name)
            if suggestions:
                candidates = []
                for d in suggestions[:max_differentials]:
                    candidate = self._evaluate_candidate(d, symptoms)
                    if candidate.overall_score > 0:
                        candidates.append(candidate)
                candidates.sort(key=lambda c: c.overall_score, reverse=True)
                if candidates and candidates[0].overall_score >= match_threshold:
                    result.matched_disease = candidates[0]
                    result.query_disease = result.matched_disease.disease_name
                    result.differential_list = candidates
                else:
                    result.no_match = True
                    if candidates:
                        result.differential_list = candidates
                    else:
                        result.error = f"未找到疾病「{disease_name}」"
            else:
                result.no_match = True
                result.error = f"未找到疾病「{disease_name}」"
            # 即使 find_disease 未精确命中，也尝试语义增强
            if result.matched_disease and self.api_enabled:
                needs_semantic = self._detect_colloquial_symptoms(symptoms)
                if needs_semantic:
                    best_entry = self.name_index.get(result.matched_disease.disease_name.lower())
                    if not best_entry:
                        for e in self.db:
                            if e["disease_name"].lower() == result.matched_disease.disease_name.lower():
                                best_entry = e
                                break
                    if best_entry:
                        enhanced = self._semantic_enhance(best_entry, symptoms)
                        if enhanced:
                            result.matched_disease = enhanced["candidate"]
                            # 全库重评分：找出症状匹配最好的疾病（不受关键词限制）
                            better = self._find_best_match_across_db(symptoms)
                            if better and better.overall_score > result.matched_disease.overall_score:
                                result.differential_list.insert(0, result.matched_disease)
                                result.matched_disease = better
                                result.query_disease = better.disease_name
                                # 去重
                                seen_names = set()
                                deduped = []
                                for d in result.differential_list:
                                    if d.disease_name not in seen_names:
                                        seen_names.add(d.disease_name)
                                        deduped.append(d)
                                result.differential_list = deduped
            # 最终检查：如果 matched_disease 得分仍然 < 阈值，视为无匹配
            if result.matched_disease and result.matched_disease.overall_score < match_threshold:
                result.differential_list = [result.matched_disease] + result.differential_list
                result.matched_disease = None
                result.no_match = True
            return result

        if not disease_entry.get("m1_diagnosis_entry_allowed", True):
            result.not_allowed = True
            result.error = (
                f"「{disease_entry['disease_name']}」是{self._entry_type_cn(disease_entry['entry_type'])}，"
                f"不允许作为 M1 诊断入口。请查找其背后的根本病因。"
            )
            return result

        candidate = self._evaluate_candidate(disease_entry, symptoms)
        result.matched_disease = candidate

        differentials = self._generate_differentials(disease_entry, symptoms)
        result.differential_list = differentials[:max_differentials]

        # ── 红旗信号检测 ──
        result.red_flags = self._detect_red_flags(symptoms)

        # ── API 语义增强（检测到口语化/非标准表达时触发） ──
        if self.api_enabled:
            needs_semantic = self._detect_colloquial_symptoms(symptoms)
            if needs_semantic:
                enhanced = self._semantic_enhance(disease_entry, symptoms)
                if enhanced:
                    result.matched_disease = enhanced["candidate"]
                    result.differential_list = enhanced.get("differentials", result.differential_list)

        # ── LLM 临床推理动态排序（v3.1） ──
        all_candidates = [result.matched_disease] + result.differential_list if result.matched_disease else result.differential_list
        all_candidates = [c for c in all_candidates if c is not None]
        if len(all_candidates) >= 2:
            ranked = self._rank_candidates_v2_reasoning(
                all_candidates, symptoms, result.red_flags
            )
            if ranked:
                result.matched_disease = ranked[0]
                result.differential_list = ranked[1:]

        return result

    def _evaluate_candidate(self, entry: dict, symptoms: List[str]) -> CandidateDiagnosis:
        """评估一个候选诊断条目"""
        disease_name = entry["disease_name"]
        axes = entry.get("axes", [])
        entry_type = entry.get("entry_type", "")
        symptoms_lower = [s.strip().lower() for s in symptoms]

        total_weight = 0.0
        weighted_match = 0.0
        primary_axes = 0
        primary_covered = 0
        uncovered_primary = []
        covered_list: List[AxeMatch] = []
        uncovered_list: List[AxeMatch] = []
        all_findings: List[str] = []

        for axe_data in axes:
            axis_name = axe_data.get("axis", "")
            role = axe_data.get("role", "secondary")
            weight = axe_data.get("weight", 0.5)
            if weight is None:
                weight = 0.5
            confidence = axe_data.get("confidence", "medium")
            typical_findings = axe_data.get("typical_findings", [])

            match_score, matched_findings = self._match_axis(
                axis_name, typical_findings, symptoms_lower,
                typical_symptoms=entry.get("typical_symptoms", [])
            )

            role_mult = self.ROLE_WEIGHT_MULTIPLIER.get(role, 0.5)
            effective_weight = weight * role_mult

            total_weight += effective_weight
            weighted_match += effective_weight * match_score

            axe_match = AxeMatch(
                axis=axis_name,
                db_weight=weight,
                db_role=role,
                db_confidence=confidence,
                findings=matched_findings,
                covered=match_score > 0.3,
                match_score=match_score,
            )

            if match_score > 0.3:
                covered_list.append(axe_match)
                all_findings.extend(matched_findings)
            else:
                uncovered_list.append(axe_match)

            if role == "primary":
                primary_axes += 1
                if match_score > 0.3:
                    primary_covered += 1
                else:
                    uncovered_primary.append(axis_name)

        overall_score = weighted_match / total_weight if total_weight > 0 else 0.0
        primary_ratio = primary_covered / primary_axes if primary_axes > 0 else 0.0

        warnings = self._generate_warnings(
            entry, overall_score, primary_ratio, uncovered_primary, entry_type
        )

        candidate = CandidateDiagnosis(
            disease_name=disease_name,
            entry_type=entry_type,
            overall_score=overall_score,
            primary_axes_covered=primary_ratio,
            total_primary_axes=primary_axes,
            uncovered_primary_axes=uncovered_primary,
            uncovered_axes=uncovered_list,
            covered_axes=covered_list,
            primary_findings=list(set(all_findings)),
            warnings=warnings,
            needs_external_check=entry.get("need_external_check_if", []),
        )

        return candidate

    def _match_axis(
        self, axis_name: str, typical_findings: List[str], symptoms: List[str],
        typical_symptoms: Optional[List[str]] = None,
    ) -> Tuple[float, List[str]]:
        """匹配单个病理轴，返回 (分数, 匹配到的表现)
        
        分层匹配原则：
        1. 文字匹配（本方法）：仅匹配标准术语对标准术语
           - 如"瘙痒"→"瘙痒"、"红斑"→"红斑"、"颈痛"→"颈痛"
           - 依靠同义词映射（SYNONYM_MAP）处理中英文标准术语间转译
           - 不负责口语/模糊表达匹配
        2. 语义匹配（_semantic_enhance）：处理所有口语化、模糊表达、非标准用语
           - 如"颈A胀"→vertebral artery compression、"后脑勺沉"→cervical dizziness
           - 通过 LLM 理解语义后回写 typical_symptoms
        
        同时搜索 typical_findings（轴级表现）和 typical_symptoms（疾病级症状）。
        """
        # 合并 typical_findings 和 typical_symptoms 一起搜索
        combined_findings = list(typical_findings)
        if typical_symptoms:
            for s in typical_symptoms:
                if s not in combined_findings:
                    combined_findings.append(s)
        # 补充：从口语化映射库中查找当前疾病的相关标准术语
        if hasattr(self, 'colloquial_map') and self.colloquial_map:
            for key, mapping in self.colloquial_map.items():
                if mapping.get("disease_name") == axis_name.split("_")[0]:  # 粗略匹配
                    pass  # 暂不实现跨轴匹配
                # 更精确：检查 mapping 的 disease_name 是否与当前 entry 匹配
        if not combined_findings or not symptoms:
            return 0.0, []

        # 内置中英文医学同义词映射
        SYNONYM_MAP = {
            "cough": ["cough", "咳嗽", "咳", "coughing"],
            "fever": ["fever", "发热", "发烧", "pyrexia", "高温", "elevated temperature"],
            "low-grade fever": ["low-grade fever", "低热", "低烧", "subfebrile"],
            "sputum": ["sputum", "痰", "phlegm", "咳痰", "mucus"],
            "productive cough": ["productive cough", "咳痰", "湿咳", "cough with sputum"],
            "sore throat": ["sore throat", "咽痛", "喉咙痛", "pharyngalgia", "pharyngeal pain"],
            "chest pain": ["chest pain", "胸痛", "chest tightness", "胸闷", "胸骨后疼痛"],
            "dyspnea": ["dyspnea", "呼吸困难", "气短", "shortness of breath", "喘", "呼吸急促"],
            "wheezing": ["wheezing", "喘息", "哮鸣", "wheeze"],
            "headache": ["headache", "头痛", "cephalalgia"],
            "dizziness": ["dizziness", "头晕", "眩晕", "vertigo", "dizzy"],
            "nausea": ["nausea", "恶心", "nauseous"],
            "vomiting": ["vomiting", "呕吐", "vomit", "emesis"],
            "fatigue": ["fatigue", "乏力", "疲劳", "疲倦", "tiredness", "weakness", "无力"],
            "edema": ["edema", "水肿", "浮肿", "肿胀", "swelling"],
            "pain": ["pain", "疼痛", "痛", "ache"],
            "palpitations": ["palpitations", "心悸", "心慌", "heart racing"],
            "anemia": ["anemia", "贫血"],
            "bleeding": ["bleeding", "出血", "hemorrhage", "hemorrhagic"],
            "hypertension": ["hypertension", "高血压", "high blood pressure", "elevated BP"],
            "hypotension": ["hypotension", "低血压", "low blood pressure"],
            "tachycardia": ["tachycardia", "心动过速", "heart racing", "fast heart rate"],
            "bradycardia": ["bradycardia", "心动过缓", "slow heart rate"],
            "jaundice": ["jaundice", "黄疸", "icterus"],
            "diarrhea": ["diarrhea", "腹泻", "拉肚子", "loose stools"],
            "constipation": ["constipation", "便秘", "constipated"],
            "dysuria": ["dysuria", "尿痛", "排尿困难", "painful urination"],
            "urinary frequency": ["urinary frequency", "尿频", "frequent urination"],
            "hematuria": ["hematuria", "血尿", "blood in urine"],
            "proteinuria": ["proteinuria", "蛋白尿", "protein in urine"],
            "rash": ["rash", "皮疹", "红斑", "eruption", "皮肤疹"],
            "pruritus": ["pruritus", "瘙痒", "itch", "itching"],
            "insomnia": ["insomnia", "失眠", "入睡困难", "sleep disorder"],
            "weight loss": ["weight loss", "体重下降", "消瘦", "减肥"],
            "weight gain": ["weight gain", "体重增加", "肥胖"],
            "fever >38.5": ["fever >38.5", "高热", "高烧", "high fever", "体温>38.5"],
            "dysphagia": ["dysphagia", "吞咽困难", "difficulty swallowing"],
            "syncope": ["syncope", "晕厥", "昏倒", "fainting", "loss of consciousness"],
            "hypoxia": ["hypoxia", "缺氧", "低氧", "low oxygen"],
            "cyanosis": ["cyanosis", "发绀", "紫绀"],
            "tremor": ["tremor", "震颤", "抖动", "shaking"],
            "seizure": ["seizure", "癫痫发作", "抽搐", "convulsion", "惊厥"],
            "paralysis": ["paralysis", "瘫痪", "麻痹", "无力"],
            "numbness": ["numbness", "麻木", "感觉减退"],
            "blurred vision": ["blurred vision", "视力模糊", "视物模糊", "vision blurred"],
            "tinnitus": ["tinnitus", "耳鸣"],
            "hearing loss": ["hearing loss", "听力下降", "听力丧失", "耳聋"],
            "nasal congestion": ["nasal congestion", "鼻塞", "congestion"],
            "rhinorrhea": ["rhinorrhea", "流涕", "流鼻涕", "runny nose", "鼻漏"],
            "sneezing": ["sneezing", "打喷嚏", "喷嚏", "sneeze"],
        }

        # 病史/病程类上下文映射（处理"肝硬化15年"→"肝硬化"这类匹配）
        HISTORY_CONTEXT_MAP = {
            "肝硬化": ["肝硬化", "liver cirrhosis", "hepatic cirrhosis", "cirrhosis"],
            "乙肝": ["乙肝", "HBV", "hepatitis B", "HBsAg", "乙型肝炎"],
            "高血压": ["高血压", "hypertension", "high blood pressure", "BP高"],
            "糖尿病": ["糖尿病", "diabetes", "DM", "高血糖"],
            "冠心病": ["冠心病", "冠心病史", "CAD", "coronary artery disease"],
            "多年": ["多年", "慢性", "数十年", "长期", "久病", "病史", "史", "年", "year"],
            "吸烟": ["吸烟", "smoking", "烟", "smoker", "tobacco"],
            "饮酒": ["饮酒", "drinking", "酒精", "alcohol", "酗酒"],
        }

        # 对每个 symptom 扩展同义词集
        def expand_synonyms(text: str) -> set:
            """扩展一个词的所有同义词"""
            results = {text.lower()}
            text_lower = text.lower()
            for key, synonyms in SYNONYM_MAP.items():
                if text_lower in synonyms or any(s in text_lower or text_lower in s for s in synonyms):
                    results.update(synonyms)
            return results

        # 提取病史上下文（如"肝硬化15年"→"肝硬化"）
        def extract_context(symptom: str) -> set:
            """从病史描述中提取主题词"""
            results = set()
            text_lower = symptom.lower()
            for key, synonyms in HISTORY_CONTEXT_MAP.items():
                if key in text_lower:
                    results.update(synonyms)
            return results

        matched = []
        findings_lower = [f.lower() for f in typical_findings]

        for symptom in symptoms:
            symptom_synonyms = expand_synonyms(symptom)
            symptom_context = extract_context(symptom)
            all_expanded = symptom_synonyms | symptom_context

            for i, finding in enumerate(findings_lower):
                # 直接包含匹配（含同义词展开 + 病史上下文）
                # 精确包含校验：要求 s 和 finding 共享至少2个连续中文或3个英文
                direct_hit = False
                for s in all_expanded:
                    if len(s) < 2:
                        continue
                    # 英文：精确包含（s在finding中或finding在s中），要求长度≥3
                    if all(c.isascii() for c in s):
                        if (s in finding or finding in s) and len(s) >= 3 and len(finding) >= 3:
                            direct_hit = True
                            break
                    else:
                        # 中文：要求一方完整包含另一方，且包含部分至少2个字
                        s_cn = ''.join(c for c in s if '一' <= c <= '鿿')
                        f_cn = ''.join(c for c in finding if '一' <= c <= '鿿')
                        if s_cn and f_cn:
                            if len(s_cn) >= 3 and s_cn in f_cn:
                                direct_hit = True
                                break
                            if len(f_cn) >= 3 and f_cn in s_cn:
                                direct_hit = True
                                break
                            # 共享至少3个连续中文（如"咽喉充血" in "咽黏膜充血"不行，但"扁桃体" in "扁桃体肿大"可以）
                            for c_len in range(min(3, min(len(s_cn), len(f_cn))), 0, -1):
                                for k in range(len(s_cn) - c_len + 1):
                                    substr = s_cn[k:k+c_len]
                                    if len(substr) >= 2 and substr in f_cn:
                                        direct_hit = True
                                        break
                                if direct_hit:
                                    break
                        if direct_hit:
                            break
                if direct_hit:
                    if typical_findings[i] not in matched:
                        matched.append(typical_findings[i])
                    break
                # 词级别匹配（含上下文词）
                # 更精确的词汇匹配：要求共享一个有意义的词汇（长度≥2的医学词）
                all_words = set()
                for w in all_expanded:
                    all_words.update(w.split())
                symptom_words = set(symptom.lower().split())
                finding_words = set(finding.split())
                combined = symptom_words | all_words
                common = combined & finding_words
        
                # 过滤只含通用短词的匹配（如"痛"、"发热"单独匹配）
                # 要求至少匹配一个长度≥3的词，或至少2个长度≥2的词
                generic_short = {'痛', '疼', '发热', '发烧', '咳嗽', '咳', '痰', '血',
                                '肿', '水肿', '头痛', '头晕', '乏力', '恶心', '呕吐',
                                '心慌', '胸闷', '气短', '气喘', '失眠', '多梦',
                                '腰痛', '背痛', '腹痛', '疼痛', '关节痛', '头痛',
                                '牙痛', '咽痛', '胸痛', '腹痛', '腰痛'}
                meaningful_matches = {w for w in common if len(w) >= 3 or w not in generic_short}
                if not meaningful_matches:
                    continue  # 仅通用短词匹配，跳过
                if len(meaningful_matches) >= max(1, min(len(combined), len(finding_words)) * 0.25):
                    if typical_findings[i] not in matched:
                        matched.append(typical_findings[i])
                    break
        if not matched:
            return 0.0, []

        ratio = len(matched) / len(typical_findings)
        score = min(1.0, ratio * 1.5)
        return score, list(set(matched))

    def _find_best_match_across_db(self, symptoms: List[str], top_n: int = 10) -> Optional[CandidateDiagnosis]:
        """对全库所有允许的疾病按症状评分，返回最佳匹配（不考虑原始查询关键词）
        用于当 search_diseases 没有命中正确疾病时做兜底。
        """
        best = None
        best_score = 0.0
        scored = []
        for entry in self.db:
            if not entry.get("m1_diagnosis_entry_allowed", True):
                continue
            candidate = self._evaluate_candidate(entry, symptoms)
            if candidate.overall_score > 0.15:
                scored.append(candidate)
        scored.sort(key=lambda c: c.overall_score, reverse=True)
        if scored:
            for c in scored[:3]:
                print(f"  [全库重评分] {c.disease_name:40s} score={c.overall_score:.4f}")
            best = scored[0]
        return best

    def _generate_differentials(
        self, primary_entry: dict, symptoms: List[str]
    ) -> List[CandidateDiagnosis]:
        """生成鉴别诊断列表"""
        primary_name = primary_entry["disease_name"].lower()

        candidates: List[CandidateDiagnosis] = []
        seen_names = {primary_name}

        primary_axes_names = {a["axis"] for a in primary_entry.get("axes", []) if a.get("axis")}

        for entry in self.db:
            name = entry["disease_name"].lower()
            if name in seen_names:
                continue
            if not entry.get("m1_diagnosis_entry_allowed", True):
                continue

            entry_axes_names = {a["axis"] for a in entry.get("axes", []) if a.get("axis")}

            if primary_axes_names & entry_axes_names:
                candidate = self._evaluate_candidate(entry, symptoms)
                if candidate.overall_score > 0.1:
                    candidates.append(candidate)
                    seen_names.add(name)

        candidates.sort(key=lambda c: c.overall_score, reverse=True)
        return candidates

    def _generate_warnings(
        self,
        entry: dict,
        overall_score: float,
        primary_ratio: float,
        uncovered_primary: List[str],
        entry_type: str,
    ) -> List[str]:
        """生成警告信息"""
        warnings = []

        if overall_score < 0.3:
            warnings.append(f"总体病理轴匹配度偏低（{overall_score:.2f}），请谨慎考虑此诊断")
        elif overall_score < 0.5:
            warnings.append(f"病理轴匹配度一般（{overall_score:.2f}），需要更多证据支持")

        if uncovered_primary:
            axes_str = "、".join(uncovered_primary)
            warnings.append(f"以下主要病理轴未被患者症状覆盖: {axes_str}")

        if entry_type in ("symptom_or_sign",):
            warnings.append(
                f"「{entry['disease_name']}」是一个{self._entry_type_cn(entry_type)}，"
                f"需要进一步查找根本病因"
            )

        must_not = entry.get("must_not_be_primary_when", [])
        for condition in must_not:
            warnings.append(f"需排除: {condition}")

        return warnings


    # -- Red Flag Detection (v3.1) --

    RED_FLAG_PATTERNS = {
        "chest_pain_critical": {
            "name": "High-risk Chest Pain",
            "patterns": ["胸痛", "大汗", "ST段抬高", "st elevation", "心肌梗死", "心梗", "急性冠脉", "胸骨后压榨", "放射至左肩"],
            "must_not_miss": ["acute_myocardial_infarction", "急性心肌梗死", "acute coronary syndrome", "unstable angina", "aortic dissection", "主动脉夹层"],
            "severity": "critical",
        },
        "stroke": {
            "name": "Stroke",
            "patterns": ["突发", "偏瘫", "口角歪斜", "言语不清", "意识障碍", "肢体无力", "面瘫", "一侧肢体"],
            "must_not_miss": ["stroke", "脑卒中", "脑梗死", "cerebral infarction", "脑出血", "intracerebral hemorrhage"],
            "severity": "critical",
        },
        "sepsis": {
            "name": "Sepsis/Septic Shock",
            "patterns": ["高热", "寒战", "意识模糊", "低血压", "呼吸急促", "脓毒", "休克", "体温>39"],
            "must_not_miss": ["sepsis", "脓毒症", "septic shock", "感染性休克"],
            "severity": "critical",
        },
        "meningitis": {
            "name": "Meningitis",
            "patterns": ["头痛剧烈", "颈强直", "喷射性呕吐", "意识改变", "脑膜刺激征"],
            "must_not_miss": ["meningitis", "脑膜炎", "bacterial meningitis"],
            "severity": "critical",
        },
        "pulmonary_embolism": {
            "name": "Pulmonary Embolism",
            "patterns": ["突发呼吸困难", "咯血", "D二聚体升高", "下肢深静脉血栓", "氧饱和度下降"],
            "must_not_miss": ["pulmonary embolism", "肺栓塞"],
            "severity": "critical",
        },
        "respiratory_failure": {
            "name": "Respiratory Failure",
            "patterns": ["呼吸困难", "发绀", "氧饱和度<90", "PO2<60", "呼吸急促>30"],
            "must_not_miss": ["respiratory failure", "呼吸衰竭"],
            "severity": "high",
        },
        "gi_bleeding": {
            "name": "GI Bleeding",
            "patterns": ["呕血", "黑便", "便血", "柏油样便", "血红蛋白下降", "血便"],
            "must_not_miss": ["upper gi bleeding", "上消化道出血", "lower gi bleeding"],
            "severity": "high",
        },
        "renal_failure": {
            "name": "Acute Kidney Injury",
            "patterns": ["少尿", "无尿", "肌酐升高", "血钾升高", "水肿"],
            "must_not_miss": ["acute kidney injury", "急性肾损伤", "acute renal failure"],
            "severity": "high",
        },
    }

    RED_FLAG_TO_DISEASE_NAMES = {
        "chest_pain_critical": ["Acute Myocardial Infarction", "Myocardial Infarction", "Acute Coronary Syndrome", "Unstable Angina", "Aortic Dissection", "急性心肌梗死", "不稳定心绞痛", "主动脉夹层"],
        "stroke": ["Stroke", "Cerebral Infarction", "Intracerebral Hemorrhage", "脑卒中", "脑梗死", "脑出血"],
        "sepsis": ["Sepsis", "Septic Shock", "脓毒症", "感染性休克"],
        "meningitis": ["Meningitis", "脑膜炎"],
        "pulmonary_embolism": ["Pulmonary Embolism", "肺栓塞"],
        "respiratory_failure": ["Respiratory Failure", "呼吸衰竭"],
        "gi_bleeding": ["Upper GI Bleeding", "Lower GI Bleeding", "上消化道出血", "下消化道出血"],
        "renal_failure": ["Acute Kidney Injury", "Acute Renal Failure", "急性肾损伤", "急性肾衰竭"],
    }

    def _detect_red_flags(self, symptoms: List[str]) -> List[str]:
        """Detect red flag signals from patient symptoms"""
        if not symptoms:
            return []
        text = " ".join(s.lower() for s in symptoms)
        flags = []
        for flag_id, cfg in self.RED_FLAG_PATTERNS.items():
            for pattern in cfg["patterns"]:
                if pattern.lower() in text:
                    flags.append(f"[{cfg['severity'].upper()}] {cfg['name']}: {pattern}")
                    break
        return flags

    # -- LLM Clinical Reasoning Dynamic Ranking (v3.1) --

    def _rank_candidates_v2_reasoning(
        self,
        candidates: List[CandidateDiagnosis],
        symptoms: List[str],
        red_flags: List[str],
    ) -> List[CandidateDiagnosis]:
        """Main controller: LLM clinical reasoning dynamic sorting.
        Fallback to initial confidence sort if API unavailable or only 1 candidate.
        """
        # 限制送入 LLM 的候选数量（最多 15 个），避免 prompt 过大
        llm_candidates = candidates[:15]
        if len(candidates) < 2 or (not self.deepseek_api_key and not self.gemini_api_key):
            for c in candidates:
                c.final_rank_score = c.overall_score
                c.reason = ""
            candidates.sort(key=lambda c: c.overall_score, reverse=True)
            return candidates

        prompt = self._build_ranking_reasoning_prompt(llm_candidates, symptoms, red_flags)
        llm_result = self._call_llm_ranking_api(prompt)

        if not llm_result:
            for c in candidates:
                c.final_rank_score = c.overall_score
            candidates.sort(key=lambda c: c.overall_score, reverse=True)
            return candidates

        ranked = self._apply_llm_ranking(llm_candidates, llm_result)
        if ranked:
            return ranked

        for c in candidates:
            c.final_rank_score = c.overall_score
        candidates.sort(key=lambda c: c.overall_score, reverse=True)
        return candidates

    def _build_ranking_reasoning_prompt(
        self,
        candidates: List[CandidateDiagnosis],
        symptoms: List[str],
        red_flags: List[str],
    ) -> str:
        """Build ranking reasoning prompt with patient info, candidates summary,
        and must-not-miss emphasis. Output strict JSON format.
        """
        prompt = "You are a senior clinician responsible for clinical reasoning ranking of differential diagnoses.\n\n"
        prompt += f"[PATIENT INFO]\nSymptoms: {', '.join(symptoms)}\n"

        if red_flags:
            prompt += "Red Flags (Must Not Miss):\n"
            for rf in red_flags:
                prompt += f"  - {rf}\n"
            prompt += "\nWARNING: Diseases matching these red flags MUST be prioritized! Missing them could be fatal.\n\n"

        prompt += f"[CANDIDATE DIAGNOSES SUMMARY] {len(candidates)} candidates, sorted by initial match score:\n\n"
        for i, c in enumerate(candidates):
            prompt += f"Candidate {i+1}: {c.disease_name}\n"
            prompt += f"  Initial Score: {c.overall_score:.2f}\n"
            prompt += f"  Type: {c.entry_type}\n"
            prompt += f"  Primary Axes Coverage: {c.primary_axes_covered:.0%} ({c.total_primary_axes - len(c.uncovered_primary_axes)}/{c.total_primary_axes})\n"
            if c.covered_axes:
                covered_names = [ax.axis for ax in c.covered_axes[:3]]
                prompt += f"  Covered Axes: {', '.join(covered_names)}\n"
            if c.uncovered_primary_axes:
                prompt += f"  Uncovered Primary Axes: {', '.join(c.uncovered_primary_axes)}\n"
            if c.warnings:
                prompt += f"  Warnings: {'; '.join(c.warnings[:3])}\n"
            prompt += "\n"

        prompt += "[RANKING CRITERIA]\n"
        prompt += "Re-rank based on (from most to least likely):\n"
        prompt += "1. MUST-NOT-MISS HAZARD: If a candidate matches red flag symptoms, rank it highest\n"
        prompt += "2. Clinical Causal Logic: Which diagnosis best explains ALL symptoms comprehensively?\n"
        prompt += "3. Initial Match Score: From local knowledge base matching, as reference\n"
        prompt += "4. Uncovered Primary Axes: Missing key evidence reduces confidence\n\n"
        prompt += "Output JSON exactly:\n"
        prompt += '{\n'
        prompt += '  "ranking": [\n'
        prompt += '    {\n'
        prompt += '      "disease_name": "disease english name",\n'
        prompt += '      "rank": 1,\n'
        prompt += '      "score": 0.0-1.0,\n'
        prompt += '      "reasoning": "Clinical reasoning why this rank"\n'
        prompt += '    },\n'
        prompt += '    ...\n'
        prompt += '  ]\n'
        prompt += '}\n\n'
        prompt += f"Must include ALL {len(candidates)} candidates. ranking array length = {len(candidates)}. rank starts from 1 consecutively."

        return prompt

    def _call_llm_ranking_api(self, prompt: str) -> Optional[dict]:
        """Call LLM ranking API with temperature=0.5. Supports DeepSeek / Gemini."""
        import urllib.request, urllib.error

        if self.llm_provider == "gemini" and self.gemini_api_key:
            # Gemini API
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
            payload = json.dumps({
                "contents": [{
                    "role": "user",
                    "parts": [{"text": prompt}]
                }],
                "systemInstruction": {
                    "parts": [{"text": "You are a senior clinician responsible for clinical reasoning ranking of differential diagnoses. Output strict JSON format."}]
                },
                "generationConfig": {
                    "temperature": 0.5,
                    "maxOutputTokens": 8192,
                    "responseMimeType": "application/json",
                }
            })
            req = urllib.request.Request(url, data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = json.loads(resp.read())
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                cleaned = text.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                parsed = json.loads(cleaned)
                ranking = parsed.get("ranking", [])
                if ranking and len(ranking) > 0:
                    print(f"  [v3.1] Gemini clinical ranking: {len(ranking)} candidates")
                    for r in ranking[:3]:
                        print(f"      #{r.get('rank')} {r.get('disease_name','?'):30s} score={r.get('score',0):.2f}")
                    return parsed
                print(f"  [v3.1] WARNING: Gemini ranking returned empty")
                return None
            except urllib.error.HTTPError as e:
                print(f"  [v3.1] ERROR: Gemini HTTP {e.code}: {e.read().decode()[:300]}")
                return None
            except Exception as e:
                print(f"  [v3.1] ERROR: Gemini API: {e}")
                return None

        elif self.deepseek_api_key:
            url = f"{self.deepseek_api_base}/chat/completions"
            payload = json.dumps({
                "model": self.deepseek_model,
                "messages": [
                    {"role": "system", "content": "You are a senior clinician responsible for clinical reasoning ranking of differential diagnoses. Output strict JSON format."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.5,
                "max_tokens": 3000,
                "response_format": {"type": "json_object"},
            })
            req = urllib.request.Request(url, data=payload.encode("utf-8"))
            req.add_header("Authorization", f"Bearer {self.deepseek_api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                content = result["choices"][0]["message"]["content"]
                cleaned = content.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                parsed = json.loads(cleaned)
                ranking = parsed.get("ranking", [])
                if ranking and len(ranking) > 0:
                    print(f"  [v3.1] DeepSeek clinical ranking: {len(ranking)} candidates")
                    for r in ranking[:3]:
                        print(f"      #{r.get('rank')} {r.get('disease_name','?'):30s} score={r.get('score',0):.2f}")
                    return parsed
                print(f"  [v3.1] WARNING: DeepSeek ranking returned empty")
                return None
            except urllib.error.HTTPError as e:
                print(f"  [v3.1] ERROR: DeepSeek ranking HTTP {e.code}")
                return None
            except Exception as e:
                print(f"  [v3.1] ERROR: DeepSeek ranking API: {e}")
                return None
        else:
            return None

    def _apply_llm_ranking(
        self,
        candidates: List[CandidateDiagnosis],
        llm_result: dict,
    ) -> List[CandidateDiagnosis]:
        """Map LLM ranking result back to CandidateDiagnosis objects"""
        ranking = llm_result.get("ranking", [])
        if not ranking:
            return []

        rank_map = {}
        for entry in ranking:
            name = entry.get("disease_name", "").lower().strip()
            rank_map[name] = entry

        for c in candidates:
            key = c.disease_name.lower().strip()
            entry = rank_map.get(key)
            if entry:
                c.final_rank_score = entry.get("score", c.overall_score)
                c.reason = entry.get("reasoning", "")
            else:
                c.final_rank_score = c.overall_score
                c.reason = "LLM did not provide ranking reasoning"

        candidates.sort(key=lambda c: c.final_rank_score, reverse=True)
        return candidates


    def _entry_type_cn(self, entry_type: str) -> str:
        mapping = {
            "standard_western_disease": "标准西医疾病",
            "western_syndrome": "西医综合征",
            "symptom_or_sign": "症状/体征",
            "risk_condition": "风险状态",
            "lab_or_imaging_finding": "实验室/影像学发现",
        }
        return mapping.get(entry_type, entry_type)

    # ── 辅助方法 ──────────────────────────────────────

    def get_available_diseases(self) -> List[str]:
        return [
            entry["disease_name"]
            for entry in self.db
            if entry.get("m1_diagnosis_entry_allowed", True)
        ]

    def get_disease_detail(self, disease_name: str) -> Optional[dict]:
        return self.find_disease(disease_name)

    # ── 语义增强（API 调用 + 本地缓存） ──────────────────

    def _make_cache_key(self, disease_name: str, symptoms: List[str]) -> str:
        """生成缓存 key"""
        raw = disease_name.lower() + "||" + "|".join(sorted(s.lower() for s in symptoms))
        return hashlib.md5(raw.encode()).hexdigest()

    def _semantic_enhance(self, entry: dict, symptoms: List[str]) -> Optional[dict]:
        """对中文症状进行语义增强匹配
        流程：检查缓存 → 未命中则构建 prompt → 调 LLM API → 解析结果 → 写缓存
        """
        disease_name = entry["disease_name"]
        cache_key = self._make_cache_key(disease_name, symptoms)

        # 1. 查缓存（永久有效）
        if cache_key in self.semantic_cache:
            return self.semantic_cache[cache_key].get("result")

        # 2. 构建 prompt，发给 LLM
        prompt = self._build_semantic_prompt(entry, symptoms)
        llm_result = self._call_llm_api(prompt)

        if not llm_result:
            return None

        # 3. 解析 LLM 返回 -> 更新 candidate
        enhanced_candidate = self._apply_semantic_result(entry, symptoms, llm_result)
        if not enhanced_candidate:
            return None

        # 4. 写缓存
        self.semantic_cache[cache_key] = {
            "disease_name": disease_name,
            "symptoms": symptoms,
            "result": {
                "candidate": enhanced_candidate,
                "differentials": [],
            },
            "timestamp": time.time(),
        }
        try:
            with open(self.semantic_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.semantic_cache, f, ensure_ascii=False, indent=2)
        except:
            pass

        # 5. 将口语化/非标准症状写入映射库（不污染 diseases_core.json）
        self._merge_colloquial_into_map(entry, symptoms, llm_result)

        return self.semantic_cache[cache_key]["result"]

    def _build_semantic_prompt(self, entry: dict, symptoms: List[str]) -> str:
        """构建发给 LLM 的语义分析 prompt"""
        disease_name = entry["disease_name"]
        disease_cn = entry.get("diseaseName_cn", "")

        # 提取疾病适用人群线索
        disease_lower = disease_name.lower()
        disease_cn_lower = disease_cn.lower() if disease_cn else ""
        pop_hints = []
        female_kw = ["puerperal", "产褥", "postpartum", "产后", "gestational", "妊娠",
                     "obstetric", "产科", "gynecolog", "妇科", "uterine", "子宫",
                     "ovarian", "卵巢", "cervical", "宫颈", "vaginal", "阴道",
                     "menstrual", "月经", "premenstrual", "经期", "menopause", "绝经",
                     "breast", "乳腺", "placenta", "胎盘", "fetal", "胎儿",
                     "endometri", "子宫内膜", "myoma", "肌瘤", "pregnant", "怀孕"]
        male_kw = ["prostat", "前列腺", "testicular", "睾丸", "penile", "阴茎",
                   "semen", "精液", "spermatic", "精索", "scrotal", "阴囊", "男性"]
        ped_kw = ["pediatric", "child", "儿童", "小儿", "infant", "新生儿", "adolescent", "青少年"]
        if any(k in disease_lower or k in disease_cn_lower for k in female_kw):
            pop_hints.append("适用人群: 女性（妇科/产科/生殖系统相关）")
        if any(k in disease_lower or k in disease_cn_lower for k in male_kw):
            pop_hints.append("适用人群: 男性（男性生殖系统相关）")
        if any(k in disease_lower or k in disease_cn_lower for k in ped_kw):
            pop_hints.append("适用人群: 儿童/青少年")

        prompt_parts = []
        prompt_parts.append("你是一个临床医学语义分析专家。请分析以下患者症状与疾病标准表现之间的语义匹配关系。")
        prompt_parts.append("")
        prompt_parts.append(f"【疾病】{disease_name}（{disease_cn}）")
        if pop_hints:
            prompt_parts.append(" — " + "；".join(pop_hints))
        prompt_parts.append(f"【患者症状】{', '.join(symptoms)}")
        prompt_parts.append("")
        prompt_parts.append("【患者信息】（请特别注意人口学特征与疾病的匹配性）")
        prompt_parts.append("- 注意：如果疾病有明显的人群适用限制（如妇科病只发生于女性、")
        prompt_parts.append("男性病只发生于男性），而症状中没有相应特征，应大幅降低匹配度。")
        prompt_parts.append("")
        prompt_parts.append("【疾病的标准病理轴与典型表现】")
        for ax in entry.get("axes", []):
            axis_name = ax.get("axis", "")
            role = ax.get("role", "secondary")
            findings = ax.get("typical_findings", [])
            prompt_parts.append("")
            prompt_parts.append(f"轴: {axis_name} (role: {role})")
            for f in findings[:5]:
                prompt_parts.append(f"  - {f}")
        prompt_parts.append("")
        prompt_parts.append("【任务】")
        prompt_parts.append("逐个判断每个患者症状是否语义上匹配该病的某个典型表现。注意：")
        prompt_parts.append('1. 中文症状（如\"颈A胀\"）可以匹配英文/中文的典型表现（如\"vertebral artery compression\"）')
        prompt_parts.append("2. 老年、慢病史、手术史等病史类信息也视为匹配线索")
        prompt_parts.append("3. 匹配不需要字面相同，语义相关即可")
        prompt_parts.append("4. 【关键】请严格评估疾病的人群适用性：对于有明确性别/年龄限制的疾病")
        prompt_parts.append("（如产褥感染只发生于产后女性、前列腺疾病只发生于男性），")
        prompt_parts.append("如果患者症状中缺乏相应特征，应给予较低的 suggested_overall_score")
        prompt_parts.append("")
        prompt_parts.append("请输出 JSON 格式（不要其他文字）：")
        prompt_parts.append('{')
        prompt_parts.append('  "axis_matches": {')
        prompt_parts.append('    "轴名1": {')
        prompt_parts.append('      "matched_symptoms": ["症状1", "症状2"],')
        prompt_parts.append('      "matched_findings": ["对应的finding原文"],')
        prompt_parts.append('      "match_score": 0.0~1.0')
        prompt_parts.append('    },')
        prompt_parts.append('    ...')
        prompt_parts.append('  },')
        prompt_parts.append('  "overall_assessment": "一句话评估（含人群适用性判断）",')
        prompt_parts.append('  "suggested_overall_score": 0.0~1.0,')
        prompt_parts.append('  "population_match": "yes|no|partial",')
        prompt_parts.append('  "population_note": "人群匹配说明"')
        prompt_parts.append('}')

        prompt = "\n".join(prompt_parts)
        return prompt


    def _call_llm_api(self, prompt: str) -> Optional[dict]:
        """调用 LLM API。支持 DeepSeek / Gemini，也可被子类重写。"""
        import urllib.request, urllib.error

        if self.llm_provider == "gemini" and self.gemini_api_key:
            # Gemini API
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
            payload = json.dumps({
                "contents": [{
                    "role": "user",
                    "parts": [{"text": prompt}]
                }],
                "systemInstruction": {
                    "parts": [{"text": "你是一个临床医学语义分析专家。分析患者症状与疾病标准表现的语义匹配关系，只输出 JSON，不要其他文字。"}]
                },
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 2000,
                    "responseMimeType": "application/json",
                }
            })
            req = urllib.request.Request(url, data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = json.loads(resp.read())
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                print(f"  ✅ Gemini 语义增强: score={parsed.get('suggested_overall_score', 'N/A')}")
                return parsed
            except urllib.error.HTTPError as e:
                print(f"  ❌ Gemini HTTP {e.code}: {e.read().decode()[:300]}")
                return None
            except Exception as e:
                print(f"  ❌ Gemini API 错误: {e}")
                return None

        elif self.deepseek_api_key:
            url = f"{self.deepseek_api_base}/chat/completions"
            payload = json.dumps({
                "model": self.deepseek_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个临床医学语义分析专家。分析患者症状与疾病标准表现的语义匹配关系，只输出 JSON，不要其他文字。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            })
            req = urllib.request.Request(url, data=payload.encode("utf-8"))
            req.add_header("Authorization", f"Bearer {self.deepseek_api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read())
                content = result["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                print(f"  ✅ DeepSeek 语义增强: score={parsed.get('suggested_overall_score', 'N/A')}")
                return parsed
            except urllib.error.HTTPError as e:
                print(f"  ❌ DeepSeek HTTP {e.code}: {e.read().decode()[:200]}")
                return None
            except Exception as e:
                print(f"  ❌ DeepSeek API 错误: {e}")
                return None
        else:
            return None

    def _apply_semantic_result(self, entry: dict, symptoms: List[str], llm_result: dict) -> Optional[CandidateDiagnosis]:
        """将 LLM 返回的语义分析结果应用到 candidate 上"""
        if not llm_result:
            return None

        axis_matches = llm_result.get("axis_matches", {})
        suggested_score = llm_result.get("suggested_overall_score", 0.0)

        # 重新评估 candidate
        candidate = self._evaluate_candidate(entry, symptoms)
        old_score = candidate.overall_score
        if abs(suggested_score - candidate.overall_score) > 0.1:
            candidate.overall_score = suggested_score
            # 清除旧的"匹配度"相关警告
            candidate.warnings = [
                w for w in candidate.warnings 
                if "匹配度" not in w
            ]
            direction = "提升" if suggested_score > old_score else "降低"
            candidate.warnings.append(
                f"✅ 语义分析修正: 匹配度从 {old_score:.2f} {direction}至 {suggested_score:.2f}"
            )

        # 对已覆盖的轴，补充 LLM 判断的匹配发现
        for ax_match in candidate.covered_axes:
            ax_name = ax_match.axis
            if ax_name in axis_matches:
                llm_findings = axis_matches[ax_name].get("matched_findings", [])
                for f in llm_findings:
                    if f not in ax_match.findings:
                        ax_match.findings.append(f)
                llm_score = axis_matches[ax_name].get("match_score", 0.0)
                if llm_score > ax_match.match_score:
                    ax_match.match_score = llm_score

        # 对未覆盖的轴，如果 LLM 认为匹配上了，标记为已覆盖
        for ax_match in candidate.uncovered_axes:
            ax_name = ax_match.axis
            if ax_name in axis_matches:
                llm_findings = axis_matches[ax_name].get("matched_findings", [])
                for f in llm_findings:
                    if f not in ax_match.findings:
                        ax_match.findings.append(f)
                llm_score = axis_matches[ax_name].get("match_score", 0.0)
                if llm_score > 0.3:
                    ax_match.covered = True
                    ax_match.match_score = llm_score
                    candidate.covered_axes.append(ax_match)

        # 清理：从 uncovered_axes 移除已经移到 covered 的
        candidate.uncovered_axes = [
            ax for ax in candidate.uncovered_axes if not ax.covered
        ]

        # 重新计算 primary 覆盖率
        total_primary = candidate.total_primary_axes
        covered_primary = sum(
            1 for ax in candidate.covered_axes if ax.db_role == "primary"
        )
        candidate.primary_axes_covered = covered_primary / total_primary if total_primary > 0 else 0.0

        return candidate

    def _detect_colloquial_symptoms(self, symptoms: List[str]) -> bool:
        """检测是否存在口语化/非标准表达，触发语义分析。

        语义分析负责处理以下场景：
        - 口语化描述（"颈A胀"、"后脑勺沉"、"像戴紧箍咒"）
        - 模糊表述（"没精神"、"身上没劲"、"不太舒服"）
        - 中医特有描述（"舌暗"、"苔薄黄"、"脉弦"）
        - 复合描述（"一动就喘"、"吃不下饭"、"干一点活就累"）
        - 数字/单位混合（"大便2-3天一次"、"体温37.2"）
        
        文字匹配仅处理标准术语（"瘙痒"、"红斑"、"颈痛"、"发热"）。
        """
        if not symptoms:
            return False

        # 必须含中文
        has_chinese = any('\u4e00' <= c <= '\u9fff' for s in symptoms for c in s)
        if not has_chinese:
            return False

        # 检查是否有非标准特征
        import re
        for s in symptoms:
            # 含字母数字混合（"颈A胀"、"VAS8分"）
            if re.search(r'[\u4e00-\u9fff]+[A-Za-z]+|[A-Za-z]+[\u4e00-\u9fff]+', s):
                return True
            # 含标点或特殊符号的复合句（"一动就喘"、"吃不下饭"）
            if re.search(r'[的了的就很还又再才都]', s):
                return True
            # 含数字的描述（"大便2-3天一次"、"体温37.2"）
            if re.search(r'[\u4e00-\u9fff].*\d', s) or re.search(r'\d.*[\u4e00-\u9fff]', s):
                return True
            # 长度超过8个字的描述性句子
            if len(s) >= 8:
                return True
            # 中医特有描述（舌/苔/脉相关）
            if any(c in s for c in ['舌', '苔', '脉']):
                return True
            # 口语化语气词
            if any(c in s for c in ['了', '啊', '吧', '呢', '吗']):
                return True

        return False

    def _try_fetch_from_msd(self, disease_name: str):
        """当本地找不到疾病时，尝试用 LLM 查询默沙东诊疗手册补一个临时条目。
        不影响 diseases_core.json（永久库），仅存到临时内存。
        临时条目使用通用 axes 结构，确保 _evaluate_candidate 能正确匹配。
        """
        if not self.deepseek_api_key and not self.gemini_api_key:
            return
        
        import urllib.request, urllib.error
        
        # 让 LLM 从默沙东获取疾病信息
        prompt = (
            f"用户查询的疾病「{disease_name}」不在本地知识库中。"
            f"请根据默沙东诊疗手册（Merck Manual）的专业知识，提供该疾病的以下信息（JSON格式）：\n"
            f"1. standard_name：该疾病的标准英文名\n"
            f"2. diseaseName_cn：标准中文名\n"
            f"3. typical_symptoms：典型症状列表（5-8条具体症状，用中文）\n"
            f"4. entry_type：疾病类型（standard_western_disease 或 western_syndrome）\n"
            f"5. brief_description：一句话描述\n"
            f"6. primary_axes：该疾病的核心病理轴列表，如 [\"structural_abnormality\", \"immune_abnormality\"] 等\n"
            f"7. relevant_findings：每个病理轴的典型表现（dict格式：轴名→[表现1, 表现2, ...]）\n\n"
            f"只输出 JSON，不要其他文字。如果无法确定，返回 {{\"error\": \"unknown\"}}"
        )
        
        url = f"{self.deepseek_api_base}/chat/completions"
        payload = json.dumps({
            "model": self.deepseek_model,
            "messages": [
                {"role": "system", "content": "你是一个临床医学专家，基于默沙东诊疗手册的知识回答问题。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"}
        })
        
        req = urllib.request.Request(url, data=payload.encode("utf-8"))
        req.add_header("Authorization", f"Bearer {self.deepseek_api_key}")
        req.add_header("Content-Type", "application/json")
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            content_raw = result["choices"][0]["message"]["content"]
            info = json.loads(content_raw)
            
            if "error" in info:
                return
            
            # 创建一个临时条目加到 db 中
            standard_name = info.get("standard_name", disease_name)
            # 检查是否已存在
            for entry in self.db:
                if entry["disease_name"] == standard_name:
                    return
            
            # 构建 axes 结构
            primary_axes = info.get("primary_axes", ["structural_abnormality"])
            relevant_findings = info.get("relevant_findings", {})
            temp_symptoms = info.get("typical_symptoms", [])
            
            axes = []
            for ax_name in primary_axes:
                findings = relevant_findings.get(ax_name, temp_symptoms[:3])
                axes.append({
                    "axis": ax_name,
                    "role": "primary",
                    "weight": 0.70,
                    "confidence": "medium",
                    "typical_findings": findings,
                })
            # 至少一个 primary 轴
            if not axes:
                axes.append({
                    "axis": "structural_abnormality",
                    "role": "primary",
                    "weight": 0.70,
                    "confidence": "low",
                    "typical_findings": temp_symptoms[:3],
                })
            
            temp_entry = {
                "disease_name": standard_name,
                "entry_type": info.get("entry_type", "standard_western_disease"),
                "m1_diagnosis_entry_allowed": True,
                "diseaseName_cn": info.get("diseaseName_cn", ""),
                "description": info.get("brief_description", ""),
                "typical_symptoms": temp_symptoms,
                "axes": axes,
                "source": "msd_manual_fetch",
            }
            self.db.append(temp_entry)
            # 也更新索引
            self.name_index[standard_name.lower()] = temp_entry
            print(f"  📡 已从默沙东获取并临时添加: {standard_name} ({info.get('diseaseName_cn','')}) [{len(axes)}轴]")
            
        except Exception as e:
            # 静默失败
            pass

    def clean_colloquial_map(self, min_count: int = 2, max_age_days: int = 30, auto_promote_threshold: int = 10):
        """清洗口语化映射库。

        策略：
        - count < min_count：低频口语表达，删除（可能只是某用户随口说的）
        - count >= auto_promote_threshold：高频表达，可升级到标准库（需人工审核）
        - min_count ≤ count < auto_promote_threshold：保留

        Args:
            min_count: 保留的最低频次（默认2次）
            max_age_days: 保留的最长天数（默认30天，0表示不过期）
            auto_promote_threshold: 自动升级阈值（默认10次，达到后建议审核追加到标准库）
        
        Returns:
            dict: {"deleted": N, "promoted": [disease_name, ...], "remaining": N}
        """
        if not hasattr(self, 'colloquial_map') or not self.colloquial_map:
            print("  ⚠ 口语化映射库为空，无需清洗")
            return {"deleted": 0, "promoted": [], "remaining": 0}

        now = time.time()
        to_delete = []
        to_promote = []

        for key, mapping in list(self.colloquial_map.items()):
            count = mapping.get("count", 0)
            timestamp = mapping.get("timestamp", now)

            # 检查是否需要删除（低频 + 超期）
            age_days = (now - timestamp) / 86400
            if count < min_count:
                # 超过有效期的低频条目才删除，防止刚写入就被删
                if max_age_days > 0 and age_days > max_age_days:
                    to_delete.append(key)
                elif max_age_days == 0:
                    to_delete.append(key)  # 不过期，按频次直接删
            # 高频可升级
            elif count >= auto_promote_threshold:
                to_promote.append(mapping)

        # 删除
        for key in to_delete:
            del self.colloquial_map[key]

        # 提示升级（不自动写入标准库，需人工审核）
        if to_promote:
            print("  ⚠ 以下口语化表达频次已达标，建议审核后追加到 diseases_core.json:")
            for m in to_promote:
                print(f"    [{m['count']}次] {m['disease_name']}: '{m['colloquial_symptom']}'")

        # 写回
        try:
            with open(self.colloquial_map_path, "w", encoding="utf-8") as f:
                json.dump(self.colloquial_map, f, ensure_ascii=False, indent=2)
        except:
            pass

        result = {
            "deleted": len(to_delete),
            "promoted": [m["disease_name"] for m in to_promote],
            "remaining": len(self.colloquial_map),
        }
        print(f"  🧹 清洗完成: 删除{result['deleted']}条, 可升级{len(to_promote)}条, 剩余{result['remaining']}条")
        return result

    def _merge_colloquial_into_map(self, entry: dict, symptoms: List[str], llm_result: dict):
        """将口语化/非标准症状写入映射库（m1_colloquial_mapping.json），
        不污染 diseases_core.json 的标准典型症状库。

        映射库结构：
            "{disease_name}||{colloquial_symptom}": {
                "disease_name": "...",
                "colloquial_symptom": "...",
                "standard_term": "...",  # LLM 认为对应的标准术语
                "count": N,             # 出现频次，越高越有可能升级为标准库
                "source": "api_enhance"
            }

        分层原则：
        - diseases_core.json：仅存标准、权威的典型症状（手动审核）
        - m1_colloquial_mapping.json：存口语→标准术语的映射
        - 映射库频次达到阈值后可升级到标准库
        """
        if not llm_result:
            return

        axis_matches = llm_result.get("axis_matches", {})
        disease_name = entry["disease_name"]

        updated = 0
        for ax_name, ax_data in axis_matches.items():
            if ax_data.get("match_score", 0) < 0.3:
                continue
            for s in ax_data.get("matched_symptoms", []):
                # 只处理含中文的、有实质内容的、非标准术语的症状
                if not any('\u4e00' <= c <= '\u9fff' for c in s) or len(s) < 2:
                    continue
                # 跳过已在 standard库 中的术语
                if any(s in d.get("typical_symptoms", []) for d in self.db if d.get("disease_name") == disease_name):
                    continue

                key = f"{disease_name}||{s}"
                if key in self.colloquial_map:
                    self.colloquial_map[key]["count"] += 1
                else:
                    # 尝试从 LLM 结果中提取对应的标准术语
                    matched_findings = ax_data.get("matched_findings", [])
                    standard_term = matched_findings[0] if matched_findings else ""
                    self.colloquial_map[key] = {
                        "disease_name": disease_name,
                        "colloquial_symptom": s,
                        "standard_term": standard_term,
                        "count": 1,
                        "timestamp": time.time(),
                        "source": "api_enhance",
                    }
                    updated += 1

        if updated:
            try:
                with open(self.colloquial_map_path, "w", encoding="utf-8") as f:
                    json.dump(self.colloquial_map, f, ensure_ascii=False, indent=2)
                print(f"  📝 已新增 {updated} 条口语化映射（累计 {len(self.colloquial_map)} 条）")
            except:
                pass

    def _upload_to_knowledge_base(
        self, disease_name: str, symptoms: List[str], result: dict
    ):
        """将语义分析结果上传到后端知识库，供所有用户共享。
        通过环境变量 KNOWLEDGE_BASE_URL 配置上传地址。
        """
        if not self.knowledge_base_url:
            return

        import urllib.request, urllib.error

        payload = json.dumps({
            "source": "m1_semantic",
            "disease_name": disease_name,
            "symptoms": symptoms,
            "result": result,
        })

        req = urllib.request.Request(
            self.knowledge_base_url,
            data=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.knowledge_base_token}",
            } if self.knowledge_base_token else {
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    print(f"  📤 已上传到知识库: {disease_name}")
        except urllib.error.HTTPError as e:
            print(f"  ⚠ 知识库上传失败 HTTP {e.code}: {e.read().decode()[:100]}")
        except Exception as e:
            # 静默失败，不影响主流程
            pass

    def format_diagnosis_result(self, result: DiagnosisResult) -> str:
        """格式化诊断结果为树状决策路径格式"""
        lines = []
        lines.append("=" * 60)
        lines.append(f"  【守一·M1 西医诊断评估】")
        lines.append(f"  查询疾病: {result.query_disease}")
        lines.append(f"  患者表现: {', '.join(result.query_symptoms)}")
        lines.append("=" * 60)

        if result.error:
            lines.append(f"\n❌ {result.error}")
            return "\n".join(lines)

        if result.not_allowed:
            lines.append(f"\n🚫 不允许作为 M1 诊断入口")
            lines.append(f"   原因: {result.error}")
            lines.append(f"\n   提示: 请查找背后的根本病因")
            return "\n".join(lines)

        if result.no_match:
            lines.append(f"\n❌ 未找到匹配疾病")
            if result.differential_list:
                lines.append(f"\n   您是否在找以下疾病？")
                for i, diff in enumerate(result.differential_list[:5], 1):
                    lines.append(f"     {i}. {diff.disease_name} (匹配度: {diff.overall_score:.2f})")
            return "\n".join(lines)

        cd = result.matched_disease
        if cd is None:
            lines.append("\n❌ 未能生成诊断评估")
            return "\n".join(lines)

        # ── 决策树核心输出 ──
        score_icon = "✅" if cd.overall_score >= 0.5 else ("⚠" if cd.overall_score >= 0.3 else "❌")
        
        lines.append(f"\n  ① {result.query_disease}")
        lines.append(f"     ├─ 匹配度: {cd.overall_score:.2f} {score_icon}")
        lines.append(f"     ├─ 类型: {self._entry_type_cn(cd.entry_type)}")
        lines.append(f"     └─ 主要轴覆盖: {cd.primary_axes_covered:.0%} ({cd.total_primary_axes - len(cd.uncovered_primary_axes)}/{cd.total_primary_axes})")

        # 已覆盖的病理轴
        if cd.covered_axes:
            lines.append(f"     ├─ 已覆盖的病理轴:")
            for i, ax in enumerate(cd.covered_axes):
                is_last_ax = (i == len(cd.covered_axes) - 1)
                ax_prefix = "└" if is_last_ax else "├"
                lines.append(f"     │   {ax_prefix}─ {ax.axis} (role: {ax.db_role}, w={ax.db_weight:.2f})")
                if ax.findings:
                    findings_str = ", ".join(ax.findings[:3])
                    lines.append(f"     │   │   └─ 匹配: {findings_str}")

        # 未覆盖的病理轴
        if cd.uncovered_axes:
            has_content_after = bool(cd.warnings or cd.needs_external_check or result.differential_list)
            lines.append(f"     ├─ 未覆盖的病理轴:")
            for i, ax in enumerate(cd.uncovered_axes):
                is_last_ua = (i == len(cd.uncovered_axes) - 1)
                ua_prefix = "├" if (not is_last_ua or has_content_after) else "└"
                lines.append(f"     │   {ua_prefix}─ {ax.axis} ({ax.db_role}, w={ax.db_weight:.2f})")

        # 注意事项
        if cd.warnings:
            lines.append(f"     ├─ 注意事项:")
            for i, w in enumerate(cd.warnings):
                has_content_after = bool(cd.needs_external_check or result.differential_list)
                is_last_w = (i == len(cd.warnings) - 1)
                w_prefix = "├" if (not is_last_w or has_content_after) else "└"
                lines.append(f"     │   {w_prefix}─ {w}")

        # 建议检查
        if cd.needs_external_check:
            has_diff = bool(result.differential_list)
            lines.append(f"     ├─ 建议检查:")
            for i, check in enumerate(cd.needs_external_check):
                is_last_c = (i == len(cd.needs_external_check) - 1)
                c_prefix = "├" if (not is_last_c or has_diff) else "└"
                lines.append(f"     │   {c_prefix}─ {check}")

        # 鉴别诊断
        if result.differential_list:
            lines.append(f"     └─ 鉴别诊断:")
            count = 0
            for diff in result.differential_list:
                if diff.disease_name == cd.disease_name:
                    continue
                count += 1
                if count > 5:
                    break
                diff_icon = "✅" if diff.overall_score >= 0.5 else ("⚠" if diff.overall_score >= 0.3 else "❌")
                diff_items = [d for d in result.differential_list if d.disease_name != cd.disease_name]
                is_last_diff = (count == min(5, len(diff_items)))
                diff_prefix = "└" if is_last_diff else "├"
                lines.append(f"         {diff_prefix}─ {diff.disease_name} → {diff.overall_score:.2f} {diff_icon}")
                if diff.uncovered_primary_axes:
                    lines.append(f"             └─ 缺主要轴: {', '.join(diff.uncovered_primary_axes)}")
                elif diff.warnings:
                    lines.append(f"             └─ {diff.warnings[0]}")

        lines.append(f"\n{'─' * 60}")
        if cd.overall_score >= 0.6 and cd.primary_axes_covered >= 0.6:
            lines.append("结论: ✅ 该诊断有较强的病理轴支持")
        elif cd.overall_score >= 0.4:
            lines.append("结论: ⚠ 该诊断有一定参考价值，建议结合临床进一步验证")
        else:
            lines.append("结论: ❌ 病理轴匹配不足，请考虑其他诊断方向")

        lines.append(f"{'=' * 60}")
        lines.append("⚠ M1 阶段: 仅限西医诊断与鉴别诊断，禁止输出任何中医内容")

        return "\n".join(lines)


# ── 独立使用 ──────────────────────────────────────────

if __name__ == "__main__":
    engine = M1DiagnosisEngine()
    print(f"已加载 {len(engine.db)} 条疾病数据")
    print(f"可用病理轴: {len(engine.available_axes)} 个")

    result = engine.diagnose(
        "Acute Bronchitis",
        ["acute cough", "low-grade fever", "cough with sputum", "sore throat"]
    )
    print(engine.format_diagnosis_result(result))
