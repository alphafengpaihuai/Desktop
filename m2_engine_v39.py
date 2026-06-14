"""
M2 辨证选方与加减规则引擎 v3.0 — v39 编译包适配版
====================================================

基于 `prompts/m2/M2_SOURCE_PROMPT.md` v3.0（三合一）提示词实现。
严格遵循 v39 运行边库编译包的 6 个知识库 + runtime_rules.json 硬边界。

核心原则：
  - 证型/方剂/加减全部来自库，模型只做匹配、排序、解释
  - 红旗物理熔断优先于任何辨证与方剂候选
  - 医案仅作证据，不触发自动加药
  - 所有方剂/药物/加减必须进入 M3

加载的知识库（位于 data/v39_runtime/）：
  western_tcm_anchor_mapping.json   → Step 1: 西医病名 → 中医二级病名
  syndrome_pathology_edges.json     → Step 2-4: 证型匹配
  modification_rules.json           → Step 7: 加减匹配
  red_flag_rules.json               → Step 8: 红旗熔断
  case_support_edges.json           → Step 9: 医案检索
  knowledge_base/600药物成分功效.txt → 加减药理解释（仅解释，不新增）

接口兼容：
  process() 保持与旧版 M2SyndromeSelector 相同的签名，
  bridge_server.py 可直接切换导入路径。
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from services.m2_semantic_align import (
    build_semantic_followup_questions,
    match_feature_semantic,
    terms_semantically_equivalent,
)


# ── 物理熔断 / 结构化 payload（M1→M2 合同） ─────────────────
class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class HardStopException(Exception):
    """红旗物理熔断：代码层硬阻断，不依赖 LLM 判断。"""

    def __init__(self, message: str, triggered_rules: Optional[List[str]] = None):
        super().__init__(message)
        self.triggered_rules = triggered_rules or []


@dataclass
class PatientData:
    """患者四诊信息（M2 payload 内嵌）。"""
    symptoms: List[str] = field(default_factory=list)
    signs: List[str] = field(default_factory=list)
    tongue: str = ""
    pulse: str = ""
    labs: Union[List[str], Dict[str, str]] = field(default_factory=list)
    imaging: Union[List[str], Dict[str, str]] = field(default_factory=list)
    negative_findings: List[str] = field(default_factory=list)
    comorbidities: List[str] = field(default_factory=list)
    age: Union[int, str] = ""
    sex: str = ""


@dataclass
class M2Payload:
    """M1 放行 M2 时的结构化 payload。"""
    disease_key: str = ""
    stage: str = ""
    differentiation_system: str = "脏腑辨证"
    primary_disease: str = ""
    patient: PatientData = field(default_factory=PatientData)


def _coerce_text_list(val: Any) -> List[str]:
    if isinstance(val, dict):
        return [str(v) for v in val.values() if v]
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if val:
        return [str(val)]
    return []


def patient_from_dict(data: Optional[Dict[str, Any]]) -> PatientData:
    """从 M1 m2_payload 或 raw case 构建 PatientData。"""
    d = data or {}
    return PatientData(
        symptoms=list(d.get("symptoms") or []),
        signs=list(d.get("signs") or []),
        tongue=str(d.get("tongue") or ""),
        pulse=str(d.get("pulse") or ""),
        labs=d.get("labs") or [],
        imaging=d.get("imaging") or [],
        negative_findings=list(d.get("negative_findings") or []),
        comorbidities=list(d.get("comorbidities") or []),
        age=d.get("age", ""),
        sex=str(d.get("sex") or d.get("gender") or ""),
    )


def payload_from_m1_result(m1_result: Dict[str, Any]) -> M2Payload:
    """从 M1 诊断结果提取 M2Payload。"""
    mp = m1_result.get("m2_payload") or {}
    patient_src = dict(mp)
    patient_src.setdefault("age", m1_result.get("age") or "")
    patient_src.setdefault("sex", m1_result.get("sex") or "")
    return M2Payload(
        disease_key=str(mp.get("disease_key") or m1_result.get("disease_key") or ""),
        stage=str(mp.get("stage") or mp.get("stage_key") or m1_result.get("stage") or ""),
        primary_disease=str(
            mp.get("primary_disease")
            or mp.get("disease_name")
            or m1_result.get("primary_diagnosis")
            or m1_result.get("primary_disease")
            or ""
        ),
        patient=patient_from_dict(patient_src),
    )


def payload_from_dict(data: Dict[str, Any]) -> M2Payload:
    """从 dict 构建 M2Payload（兼容 process_payload 直接调用）。"""
    patient = data.get("patient")
    if isinstance(patient, PatientData):
        pd = patient
    elif isinstance(patient, dict):
        pd = patient_from_dict(patient)
    else:
        pd = patient_from_dict(data)
    return M2Payload(
        disease_key=str(data.get("disease_key") or ""),
        stage=str(data.get("stage") or ""),
        differentiation_system=str(data.get("differentiation_system") or "脏腑辨证"),
        primary_disease=str(data.get("primary_disease") or data.get("disease_name") or ""),
        patient=pd,
    )

# ── 路径配置 ──────────────────────────────────────────────
_V40_DIR = os.path.join(os.path.dirname(__file__), "data", "v40_runtime", "v40_table_revision_runtime")
_V39_DIR = os.path.join(os.path.dirname(__file__), "data", "v39_runtime")
# v40 修订的文件
_ANCHOR_MAP_PATH       = os.path.join(_V39_DIR, "western_tcm_anchor_mapping.json")  # 未修改，沿用 v39
_SYNDROME_EDGES_PATH   = os.path.join(_V40_DIR, "v40_syndrome_pathology_edges_repaired.json")
_MOD_RULES_PATH        = os.path.join(_V40_DIR, "v40_modification_rules_repaired.json")
_RED_FLAG_PATH         = os.path.join(_V39_DIR, "red_flag_rules.json")               # 未修改，沿用 v39
_CASE_EDGES_PATH       = os.path.join(_V39_DIR, "case_support_edges.json")           # 未修改，沿用 v39
_PHARMACOLOGY_PATH     = os.path.join(os.path.dirname(__file__), "knowledge_base", "600 药物成分功效.txt")
_M2_RUNTIME_VIEW_PATH  = os.path.join(_V40_DIR, "v40_m2_runtime_view_revised.csv")

MAX_MODIFICATIONS = 4

# ── 中文全称 → 缩写别名（v40 知识库中部分病名使用英文缩写） ──
_CHINESE_TO_ABBREV: Dict[str, str] = {
    "肌萎缩侧索硬化": "als",
    "良性阵发性位置性眩晕": "bppv",
    "慢性阻塞性肺疾病": "copd",
    "胃食管反流病": "gerd",
    "炎症性肠病": "ibd",
    "肠易激综合征": "ibs",
    "急性呼吸窘迫综合征": "ards",
    "多器官功能障碍综合征": "mods",
    "弥散性血管内凝血": "dic",
    "急性冠脉综合征": "acs",
    "短暂性脑缺血发作": "tia",
    "急性肾损伤": "aki",
    "慢性肾病": "ckd",
}

# ── 儿科约束 ──────────────────────────────────────────────
PEDIATRIC_KEY_PATTERNS = ("er_tong", "xiao_er", "pediatric", "child", "儿童", "小儿")
PEDIATRIC_DISEASE_NAMES = ("儿童肺炎", "小儿肺炎", "小儿哮喘", "小儿", "儿童")


def _is_pediatric_disease(key_or_name: str) -> bool:
    """判断 disease_key 或 西医病名 是否为儿科专用。"""
    t = key_or_name.lower()
    return any(p in t for p in PEDIATRIC_KEY_PATTERNS)


def _is_pediatric_age(age_str: str) -> Optional[bool]:
    """从年龄字符串判断是否为儿童。返回 None 表示未知。"""
    if not age_str:
        return None
    # 提取数字
    m = re.search(r"(\d+)", str(age_str))
    if not m:
        return None
    age = int(m.group(1))
    if age <= 0:
        return None
    return age < 18


# ── 处方审计常量 ──────────────────────────────────────────
PHLEGM_HERB_KEYWORDS   = ("半夏", "陈皮", "茯苓", "瓜蒌", "贝母", "桔梗", "竹茹", "浙贝", "前胡", "苏子")
BLOOD_HERB_KEYWORDS    = ("川芎", "红花", "桃仁", "丹参", "赤芍", "当归", "三七", "郁金", "延胡索", "鸡血藤")

# ── 否定前缀 ───────────────────────────────────────────────
_NEGATION_PREFIXES = ["无明显", "无明确", "无", "未", "没有", "否认", "不伴"]


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════
# 语义对齐工具（复用 services/m2_semantic_align + 本地补充）
# ══════════════════════════════════════════════════════════════

_PATIENT_TO_TEMPLATE: Dict[str, str] = {
    "痰黏在嗓子里咳不出来": "痰黏难咯",
    "痰粘在嗓子咳不出": "痰黏难咯",
    "痰咳不出来": "痰黏难咯",
    "痰黏咳不出": "痰黏难咯",
    "胸口堵得慌": "胸闷",
    "胸口发闷": "胸闷",
    "胸口堵": "胸闷",
    "胸发闷": "胸闷",
    "大便稀水一样": "便溏",
    "大便像稀水": "便溏",
    "拉稀": "便溏",
    "拉肚子": "便溏",
    "大便不成形": "便溏",
    "怕冷": "恶寒",
    "发冷": "恶寒",
    "喉咙痛": "咽痛",
    "嗓子疼": "咽痛",
    "咳黄痰": "痰黄",
    "咳白痰": "痰白",
}

_FEATURE_SYNONYMS: Dict[str, List[str]] = {
    "咳嗽": ["咳", "咳嗽", "夜间咳嗽", "干咳", "咳痰", "咳嗽频繁", "咳嗽气急"],
    "痰白": ["痰白", "痰白稀", "痰多色白", "白痰", "痰白偏浊", "痰白稠", "痰白粘稠"],
    "痰黄": ["黄痰", "痰黄", "痰黄稠", "痰黄黏", "黄稠痰", "痰黄黏稠", "痰黄黏稠色黄", "痰多黏稠色黄", "绿色痰"],
    "发热": ["发热", "高热", "发烧", "体温升高", "热", "身热", "发热恶风", "壮热"],
    "胸闷": ["胸闷", "胸满", "胸痞", "胸脘痞闷"],
    "咽痛": ["咽痛", "咽喉痛", "咽痛咽痒", "喉咙痛", "咽痒咽痛", "咽红肿痛"],
    "便溏": ["便溏", "大便溏薄", "大便稀"],
    "口干": ["口干", "口渴", "咽干口渴", "咽干", "烦躁口渴"],
    "苔黄腻": ["苔黄腻", "黄腻", "苔黄", "黄腻苔", "舌苔黄腻", "舌红苔黄腻"],
    "舌红": ["舌红", "舌质红", "舌边尖红", "舌暗红", "舌绛", "舌红苔薄黄"],
    "脉数": ["脉数", "脉滑数", "脉濡数", "脉浮数", "脉细数"],
    "脉浮": ["脉浮", "脉浮数", "脉浮紧"],
    "小便黄": ["小便黄", "小便赤涩", "小便短赤"],
    "乏力": ["乏力", "神疲", "气短", "倦怠"],
    "肢体困重": ["肢体困重", "肢体沉重", "困重", "身重"],
    "食少": ["食少", "纳差", "纳呆", "食欲不振"],
    "头晕": ["头晕", "眩晕", "目眩"],
    "心悸": ["心悸", "心慌", "心跳不安"],
    "畏寒肢冷": ["畏寒肢冷", "形寒肢冷", "肢凉", "肢冷", "怕冷", "恶寒"],
    "鼻塞": ["鼻塞", "鼻塞流涕", "鼻涕鼻塞", "流涕"],
    "痰多": ["痰多", "咳痰量多", "痰多痰粘稠"],
    "黏稠": ["黏稠", "粘稠", "痰粘稠", "痰黏稠"],
}

_VAGUE_PATTERNS: List[re.Pattern] = [
    re.compile(r"^有点.+"),
    re.compile(r"^偶尔.+"),
    re.compile(r"^有时.+"),
    re.compile(r"^可能.+"),
    re.compile(r"^好像.+"),
    re.compile(r"^似乎.+"),
]

_DURATION_SUFFIX = re.compile(r"(\d+[天周月年日小时]+|半年|数年|多年|数月|数天|近期|最近|近日)$")


def _is_vague(phrase: str) -> bool:
    t = phrase.strip()
    if not t:
        return True
    return any(p.search(t) for p in _VAGUE_PATTERNS)


def _normalize_patient_phrase(phrase: str) -> str:
    p = _DURATION_SUFFIX.sub("", phrase.strip())
    return _PATIENT_TO_TEMPLATE.get(p, p)


def _split_patient_text(text: str) -> List[str]:
    t = _DURATION_SUFFIX.sub("", str(text).strip())
    if not t:
        return []
    parts = re.split(r"[，,、；;。\s]+", t)
    return [p.strip() for p in parts if p.strip()]


def _extract_all_patient_phrases(
    symptoms: List[str],
    tongue: str,
    pulse: str,
    signs: Optional[List[str]] = None,
) -> List[str]:
    phrases: List[str] = []
    for lst in (symptoms or [], signs or []):
        for item in lst:
            phrases.extend(_split_patient_text(str(item)))
    if tongue:
        phrases.extend(_split_patient_text(tongue))
    if pulse:
        phrases.extend(_split_patient_text(pulse))
    return phrases


def _negation_filter(phrases: List[str]) -> Tuple[List[str], List[str]]:
    positive: List[str] = []
    negative: List[str] = []
    for p in phrases:
        stripped = p.strip()
        is_neg = any(stripped.startswith(prefix) for prefix in _NEGATION_PREFIXES)
        if is_neg:
            negative.append(stripped)
        else:
            positive.append(stripped)
    return positive, negative


def is_valid_syndrome_edge(edge: Dict) -> bool:
    """判断 syndrome_pathology_edges.json 中的一条 edge 是否为有效候选。

    以下情况视为无效 edge，触发 fallback：
    - syndrome_name 为空
    - pathology_summary 为空且 _formula_name 为空
    - status 为 候选/需人工审核/placeholder，且缺少 required_symptoms/formula/pathology
    - 只有 disease_key，没有 syndrome/formula/pathology
    """
    syn = (edge.get("syndrome_name") or "").strip()
    pathology = (edge.get("pathology_summary") or "").strip()
    evidence = (edge.get("evidence_symptoms") or "").strip()
    formula = (edge.get("_formula_name") or edge.get("formula") or "").strip()
    status = (edge.get("status") or "").strip()
    review_required = edge.get("review_required", False)
    required_symptoms = edge.get("required_symptoms") or edge.get("evidence_symptoms") or ""

    # 1. 证型名为空
    if not syn:
        return False
    # 2. 证型名为占位符（"候选"/"需人工审核"等）—— 一律视为无效，不论是否有 evidence
    if any(syn.startswith(p) for p in ("候选", "需人工审核", "placeholder", "占位", "空壳")):
        return False
    # 3. status 为占位符且缺少核心字段
    if status in ("候选", "需人工审核", "placeholder"):
        if not required_symptoms and not formula and not pathology:
            return False
    # 4. review_required 为 true 且缺少核心字段
    if review_required:
        has_enough = bool(required_symptoms) or bool(formula) or bool(pathology)
        if not has_enough:
            return False
    # 5. 缺少 formula 且缺少 pathology_summary
    if not formula and not pathology:
        return False
    return True


def _semantic_match_feature(
    feature: str,
    patient_phrases: List[str],
    normalized_phrases: List[str],
) -> Tuple[bool, str, str]:
    """返回 (matched, status, matched_phrase)。

    status: exact | synonym | partial | semantic_ambiguous | no_match
    """
    feature_norm = feature.strip()
    all_phrases = patient_phrases + normalized_phrases
    combined_text = " ".join(all_phrases)

    # 精确匹配
    for ph in all_phrases:
        if ph == feature_norm:
            return True, "exact", ph
    # 子串匹配（feature 在患者文本中）
    if feature_norm in combined_text:
        return True, "exact", feature_norm

    # 同义匹配
    for ph in normalized_phrases:
        if terms_semantically_equivalent(ph, feature_norm, _FEATURE_SYNONYMS):
            return True, "synonym", ph

    # 部分匹配：feature 拆分为子短语，用字符 bigram 重叠率判断
    feature_tokens = [t.strip() for t in re.split(r"[、；;,，]", feature_norm) if len(t.strip()) >= 2]
    if feature_tokens:
        match_count = 0
        for t in feature_tokens:
            # 检查子短语是否在 combined_text 中（模糊子串）
            if t in combined_text:
                match_count += 1
            else:
                # 字符 bigram 重叠：取 t 和 combined_text 的 bigram 交集
                t_bigrams = {t[i:i+2] for i in range(len(t)-1)} if len(t) >= 2 else {t}
                text_bigrams = {combined_text[i:i+2] for i in range(len(combined_text)-1)}
                overlap = t_bigrams & text_bigrams
                if len(overlap) >= max(len(t_bigrams) * 0.5, 2):
                    match_count += 1
        ratio = match_count / len(feature_tokens)
        if ratio >= 0.5:
            return True, "partial", f"{match_count}/{len(feature_tokens)} tokens ({ratio:.0%})"

    # 模糊描述检查
    for ph in patient_phrases:
        if _is_vague(ph) and feature_norm in ph:
            return False, "semantic_ambiguous", ph
    return False, "no_match", ""


# ══════════════════════════════════════════════════════════════
# M2V39Engine
# ══════════════════════════════════════════════════════════════

class M2V39Engine:
    """M2 辨证选方引擎 v3.0 — v39 编译包适配版

    输入：M1 的 primary_disease + 患者四诊信息
    流程：Step 1→10，严格按 prompts/m2/M2_SOURCE_PROMPT.md v3.0
    输出：syndrome_result + prescription + case_evidence + guardrail + m3_payload
    """

    def __init__(
        self,
        v39_dir: str = _V39_DIR,
        pharmacology_path: str = _PHARMACOLOGY_PATH,
        runtime_view_path: str = _M2_RUNTIME_VIEW_PATH,
        llm_callback: Optional[Callable[[str], str]] = None,
    ):
        self.v39_dir = v39_dir
        self.llm_callback = llm_callback

        # ── 加载 6 个核心知识库 + runtime_view ──
        print("[M2V39] 加载 v39 运行边库编译包 ...")
        self.anchor_map: Dict = _load_json(_ANCHOR_MAP_PATH)
        print(f"  anchor_map: {len(self.anchor_map)} 个西医病名")

        self.syndrome_edges: List[Dict] = _load_json(_SYNDROME_EDGES_PATH)
        print(f"  syndrome_edges: {len(self.syndrome_edges)} 条")

        self.mod_rules: List[Dict] = _load_json(_MOD_RULES_PATH)
        print(f"  mod_rules: {len(self.mod_rules)} 条")

        self.red_flags: List[Dict] = _load_json(_RED_FLAG_PATH)
        print(f"  red_flags: {len(self.red_flags)} 条")

        self.case_edges: List[Dict] = _load_json(_CASE_EDGES_PATH)
        print(f"  case_edges: {len(self.case_edges)} 条")

        # ── M2 运行视图（方剂/证型一体化 CSV） ──
        self.runtime_view: List[Dict] = self._load_runtime_view(runtime_view_path)
        print(f"  runtime_view: {len(self.runtime_view)} 行")

        # ── 药理学库（仅解释） ──
        self.pharmacology: Dict = {}
        try:
            self.pharmacology = _load_json(pharmacology_path)
            print(f"  pharmacology: {len(self.pharmacology.get('herb_records', []))} 味药")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  pharmacology: 加载失败 ({e})")

        # ── 构建索引 ──
        self._build_indices()

        print("[M2V39] 引擎就绪 ✓")

    @staticmethod
    def _load_runtime_view(path: str) -> List[Dict]:
        rows: List[Dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except FileNotFoundError:
            print(f"  [WARN] m2_runtime_view.csv 未找到: {path}")
        return rows

    # ══════════════════════════════════════════════════════
    # 索引构建
    # ══════════════════════════════════════════════════════

    def _build_indices(self):
        # anchor_map 按 disease_key 索引
        self._anchor_by_key: Dict[str, Dict] = {}
        self._anchor_by_disease_name: Dict[str, Dict] = {}
        for disease_name, entry in self.anchor_map.items():
            dk = (entry.get("disease_key") or "").lower()
            if dk:
                self._anchor_by_key[dk] = entry
            self._anchor_by_disease_name[disease_name] = entry

        # runtime_view 按 (western_disease_key, syndrome_name) 索引
        self._formula_index: Dict[Tuple[str, str], Dict] = {}
        # runtime_view 按 disease_key 和 西医病名 索引 — 用于 edges 空壳时 fallback
        self._runtime_by_disease: Dict[str, List[Dict]] = {}
        # runtime_view 按 disease_key 精确索引
        self._runtime_by_key: Dict[str, List[Dict]] = {}
        for row in self.runtime_view:
            disease_name = (row.get("西医病名") or "").strip()
            dk = (row.get("西医病名key") or "").strip().lower()
            syn = (row.get("证型") or "").strip()
            formula_name = (row.get("方剂") or "").strip()
            formula_herbs = (row.get("方剂组成") or "").strip()
            if dk and syn and (formula_name or formula_herbs):
                self._formula_index[(dk, syn)] = {
                    "formula_name": formula_name,
                    "formula_herbs_raw": formula_herbs,
                }
            # 按 disease_key 和 西医病名双索引
            if dk:
                self._runtime_by_key.setdefault(dk, []).append(row)
                self._runtime_by_disease.setdefault(dk, []).append(row)
            if disease_name:
                self._runtime_by_disease.setdefault(disease_name, []).append(row)

        # syndrome_edges 按 (western_disease_key, tcm_anchor) 索引
        self._syndrome_index: Dict[Tuple[str, str], List[Dict]] = {}
        for edge in self.syndrome_edges:
            key = (edge.get("western_disease_key", "").lower(),
                   edge.get("tcm_anchor", ""))
            self._syndrome_index.setdefault(key, []).append(edge)

        # mod_rules 按 (western_disease_key, syndrome_constraint) 索引（v40 格式）
        self._mod_index: Dict[Tuple[str, str], List[Dict]] = {}
        for rule in self.mod_rules:
            dk = (rule.get("western_disease_key") or rule.get("disease_constraint") or "").strip().lower()
            syn = (rule.get("syndrome_constraint") or "").strip()
            key = (dk, syn)
            self._mod_index.setdefault(key, []).append(rule)

        # case_edges 按 disease_name 索引（case_edges 的 disease_key 是内部 ID）
        self._case_index: Dict[str, List[Dict]] = {}
        for case in self.case_edges:
            disease_name = (case.get("disease_name") or "").strip()
            if disease_name:
                self._case_index.setdefault(disease_name, []).append(case)

        # 红旗 trigger 词索引
        self._rf_triggers: List[Tuple[str, Dict]] = []
        for rf in self.red_flags:
            triggers = [t.strip() for t in (rf.get("trigger", "") or "").split("；") if t.strip()]
            for t in triggers:
                self._rf_triggers.append((t, rf))

    # ══════════════════════════════════════════════════════
    # 主入口 process() — 兼容旧 M2SyndromeSelector 接口
    # ══════════════════════════════════════════════════════

    def process_payload(self, payload: Union[M2Payload, Dict[str, Any]]) -> Dict:
        """结构化 payload 入口（M1 m2_payload 或 M2Payload 对象）。"""
        if isinstance(payload, dict):
            payload = payload_from_dict(payload)
        primary = payload.primary_disease or payload.disease_key
        p = payload.patient
        return self.process(
            primary_disease=primary,
            symptoms=p.symptoms,
            signs=p.signs,
            tongue=p.tongue,
            pulse=p.pulse,
            labs=_coerce_text_list(p.labs),
            imaging=_coerce_text_list(p.imaging),
            negative_findings=p.negative_findings,
            age=str(p.age or ""),
            stage=payload.stage,
        )

    def process_from_m1(self, m1_result: Dict[str, Any]) -> Dict:
        """从 M1 完整诊断结果调用 M2（自动提取 m2_payload）。"""
        mp = payload_from_m1_result(m1_result)
        if not mp.primary_disease and not mp.disease_key:
            return self._build_no_candidate("", "", "M1 未提供 primary_disease/disease_key", {"steps": {}})
        return self.process_payload(mp)

    def process(
        self,
        primary_disease: str,
        symptoms: Optional[List[str]] = None,
        signs: Optional[List[str]] = None,
        tongue: str = "",
        pulse: str = "",
        cold_heat: Optional[List[str]] = None,
        stool_urine: Optional[List[str]] = None,
        sleep: Optional[List[str]] = None,
        appetite: Optional[List[str]] = None,
        labs: Optional[List[str]] = None,
        imaging: Optional[List[str]] = None,
        negative_findings: Optional[List[str]] = None,
        age: str = "",
        weight: str = "",
        m1_safety: Optional[Dict] = None,
        stage: str = "",
    ) -> Dict:
        """兼容旧版 M2SyndromeSelector.process() 签名的入口。

        内部串联 Step 1-10，输出 v3.0 格式。
        """
        symptoms = symptoms or []
        signs = signs or []
        labs = labs or []
        imaging = imaging or []
        negative_findings = negative_findings or []

        # ── 合并患者状态文本（用于红旗匹配） ──
        all_texts = (
            " ".join(str(s) for s in symptoms)
            + " " + " ".join(str(s) for s in signs)
            + " " + " ".join(str(s) for s in labs)
            + " " + " ".join(str(s) for s in imaging)
            + " " + " ".join(str(s) for s in negative_findings)
            + " " + str(tongue) + " " + str(pulse)
            + " " + str(primary_disease) + " " + str(stage)
        )

        disease_key = self._resolve_disease_key(primary_disease, stage)

        return self._run_full_pipeline(
            disease_key=disease_key,
            primary_disease=primary_disease,
            stage=stage,
            symptoms=symptoms,
            signs=signs,
            tongue=tongue,
            pulse=pulse,
            labs=labs,
            imaging=imaging,
            negative_findings=negative_findings,
            all_texts=all_texts,
            age=age,
            m1_safety=m1_safety,
        )

    # ══════════════════════════════════════════════════════
    # disease_key 解析
    # ══════════════════════════════════════════════════════

    def _resolve_disease_key(self, primary_disease: str, stage: str = "") -> str:
        """从 primary_disease 解析 disease_key。

        策略：
        1. 精确匹配 anchor_map 的 key（中文病名）
        2. 精确匹配 anchor_by_disease_name
        3. 子串匹配
        4. 返回原始名
        """
        name = (primary_disease or "").strip()
        if not name:
            return name

        # 精确匹配
        if name in self.anchor_map:
            return (self.anchor_map[name].get("disease_key") or name).lower()
        if name in self._anchor_by_disease_name:
            return (self._anchor_by_disease_name[name].get("disease_key") or name).lower()

        # 中文全名 → 缩写别名（优先于通用子串匹配，避免误匹配）
        alias_key = self._try_chinese_alias(name)
        if alias_key:
            return alias_key

        # 子串匹配
        for dk, entry in self._anchor_by_disease_name.items():
            if name.lower() in dk.lower() or dk.lower() in name.lower():
                return (entry.get("disease_key") or dk).lower()

        return name.lower()

    def _try_chinese_alias(self, chinese_name: str) -> Optional[str]:
        """尝试通过 runtime_view 的 西医病名/西医病名key 找到缩写 key。"""
        # 硬编码中文 → 缩写映射
        lower_name = chinese_name.lower()
        for cn_full, abbr in _CHINESE_TO_ABBREV.items():
            if cn_full.lower() in lower_name or lower_name in cn_full.lower():
                return abbr

        # 通过 runtime_view 的 西医病名 查找
        for rname, rrows in self._runtime_by_disease.items():
            rk = (rrows[0].get("西医病名key") or "").strip().lower() if rrows else ""
            if not rk:
                continue
            if lower_name in rname.lower() or rname.lower() in lower_name:
                return rk

        return None

    # ══════════════════════════════════════════════════════
    # 完整流程
    # ══════════════════════════════════════════════════════

    def _run_full_pipeline(
        self,
        *,
        disease_key: str,
        primary_disease: str,
        stage: str,
        symptoms: List[str],
        signs: List[str],
        tongue: str,
        pulse: str,
        labs: List[str],
        imaging: List[str],
        negative_findings: List[str],
        all_texts: str,
        age: str = "",
        m1_safety: Optional[Dict] = None,
    ) -> Dict:
        self._current_age = age  # 存储年龄，供 fallback 中使用儿科约束
        trace: Dict[str, Any] = {
            "input": {
                "disease_key": disease_key,
                "primary_disease": primary_disease,
                "stage": stage,
                "symptoms_count": len(symptoms),
            },
            "steps": {},
        }

        # ── Step 1: 加载中医二级病名 ──
        step1 = self._step1_load_tcm_anchors(disease_key, primary_disease, stage)
        trace["steps"]["step1_anchors"] = step1

        tcm_anchor = step1.get("selected_anchor", "")
        anchors = step1.get("anchors", [])

        # ── Step 8: 红旗物理熔断（优先执行，先于辨证） ──
        step8 = self._step8_red_flag_check(all_texts, disease_key)
        trace["steps"]["step8_red_flag"] = step8
        if step8.get("triggered"):
            return self._build_blocked_output(disease_key, primary_disease, step8, trace)

        # ── Step 2: 加载候选证型 ──
        step2 = self._step2_load_syndromes(disease_key, tcm_anchor, stage, anchors, primary_disease)
        trace["steps"]["step2_syndromes"] = step2
        candidates = step2.get("candidates", [])

        if not candidates:
            return self._build_no_candidate(disease_key, primary_disease, "无匹配证型", trace)

        # ── Step 3: 语义对齐与强规则初筛 ──
        step3 = self._step3_semantic_filter(candidates, symptoms, signs, tongue, pulse)
        trace["steps"]["step3_filter"] = step3

        # ── Step 4: 排除法剪刀 ──
        step4 = self._step4_exclusion_scissors(candidates, step3)
        trace["steps"]["step4_exclusion"] = step4

        # ── Step 5: LLM 因果复核（可选，规则优先） ──
        step5 = self._step5_llm_review(step4, symptoms, signs, tongue, pulse, disease_key, tcm_anchor)
        trace["steps"]["step5_llm_review"] = step5

        # ── Step 6: 证型锁定 ──
        step6 = self._step6_lock_syndrome(step4, step5, disease_key=disease_key, candidates=candidates)
        trace["steps"]["step6_lock"] = step6

        selected_syndrome = step6.get("selected_syndrome", "")
        confidence = step6.get("confidence", "LOW")
        rule_based = step6.get("rule_based", True)
        llm_opinion = step6.get("llm_opinion", "")
        llm_discrepancy = step6.get("llm_discrepancy")

        # ── Step 7: 加减药物匹配 ──
        step7 = self._step7_modifications(
            selected_syndrome, disease_key, tcm_anchor, stage,
            symptoms, signs, tongue, pulse,
        )
        trace["steps"]["step7_modifications"] = step7

        # ── Step 9: 医案证据检索 ──
        step9 = self._step9_case_evidence(disease_key, selected_syndrome, primary_disease)
        trace["steps"]["step9_cases"] = step9

        # ── Step 10: 处方审计 ──
        all_herbs = []
        for h in step6.get("core_herbs", []):
            all_herbs.append(h.get("name", "") if isinstance(h, dict) else str(h))
        for m in step7.get("modifications", []):
            all_herbs.append(m.get("herb", ""))
        step10 = self._step10_prescription_audit(all_herbs, tongue, pulse, symptoms, signs)
        trace["steps"]["step10_audit"] = step10

        # ── 组装输出 ──
        step2_meta = trace.get("steps", {}).get("step2_syndromes", {})
        return self._build_output(
            disease_key=disease_key,
            primary_disease=primary_disease,
            stage=stage,
            syndrome_result=step6,
            modifications=step7.get("modifications", []),
            case_evidence=step9.get("cases", []),
            audit=step10,
            trace=trace,
            red_flag_triggered=False,
            fallback_used=step2_meta.get("fallback_used", False),
            fallback_source=step2_meta.get("source", ""),
            fallback_reason=step2_meta.get("fallback_reason", ""),
            need_age_confirmation=step2_meta.get("need_age_confirmation", False),
            matched_runtime_key=step2_meta.get("matched_runtime_key", ""),
        )

    # ══════════════════════════════════════════════════════
    # Step 1: 加载中医二级病名
    # ══════════════════════════════════════════════════════

    def _step1_load_tcm_anchors(self, disease_key: str, primary_disease: str, stage: str) -> Dict:
        """从 western_tcm_anchor_mapping.json 获取允许的 tcm_anchors。"""
        entry = self._anchor_by_disease_name.get(primary_disease) or self._anchor_by_key.get(disease_key, {})
        raw_anchors: List[Dict] = entry.get("tcm_anchors", [])

        # 过滤 status = "已通过" 且匹配 stage_constraint
        anchors = []
        for a in raw_anchors:
            if a.get("governance_status") != "已通过":
                continue
            stage_constraint = a.get("stage_constraint", "") or ""
            if stage and stage_constraint:
                allowed_stages = [s.strip() for s in stage_constraint.replace("；", ";").split(";") if s.strip()]
                if stage not in allowed_stages:
                    continue
            anchors.append(a)

        selected = anchors[0].get("anchor", "") if anchors else ""
        confidence = "HIGH" if anchors else "MEDIUM"

        return {
            "anchors": anchors,
            "selected_anchor": selected,
            "confidence": confidence,
            "source": "western_tcm_anchor_mapping.json",
        }

    # ══════════════════════════════════════════════════════
    # Step 2: 加载候选证型
    # ══════════════════════════════════════════════════════

    def _step2_load_syndromes(
        self,
        disease_key: str,
        tcm_anchor: str,
        stage: str,
        anchors: List[Dict],
        original_disease: str = "",
    ) -> Dict:
        """从 syndrome_pathology_edges.json 加载候选证型。

        优先级：
        1. syndrome_pathology_edges.json → is_valid_syndrome_edge() 过滤
        2. 若 edges 为空壳/占位，fallback 到 m2_runtime_view.csv

        Returns:
            {
                "candidates": [...],
                "count": int,
                "source": "syndrome_pathology_edges" | "m2_runtime_view_fallback",
                "fallback_used": bool,
                "fallback_reason": str,
                "need_age_confirmation": bool,
                "matched_runtime_key": str,
            }
        """
        dk_lower = disease_key.lower()
        edge_candidates: List[Dict] = []

        # ── 优先：从 syndrome_pathology_edges.json 搜索 ──
        for edge in self.syndrome_edges:
            if edge.get("western_disease_key", "").lower() != dk_lower:
                continue
            if tcm_anchor and edge.get("tcm_anchor", "") != tcm_anchor:
                continue
            if stage:
                stage_constraint = (edge.get("stage_constraint") or "").replace("；", ";")
                allowed = [s.strip() for s in stage_constraint.split(";") if s.strip()]
                if stage not in allowed:
                    continue
            edge_candidates.append(edge)

        # 若直接匹配为空，尝试只用 disease_key 不加 anchor/stage 筛选
        if not edge_candidates:
            for edge in self.syndrome_edges:
                if edge.get("western_disease_key", "").lower() == dk_lower:
                    edge_candidates.append(edge)

        # 若仍为空，尝试 variant keys（v40 snake_case 改名后 anchor_map 可能未同步）
        if not edge_candidates and original_disease:
            variant_keys = [
                re.sub(r'[()（） ]', '_', original_disease).strip('_').lower(),
                original_disease.lower().replace(' ', '_'),
            ]
            # 匹配 western_disease 字段
            for edge in self.syndrome_edges:
                edge_disease = (edge.get("western_disease") or "").strip()
                if edge_disease == original_disease or original_disease in edge_disease:
                    edge_candidates.append(edge)
            # 匹配 disease_key 字段（子串）
            if not edge_candidates:
                for edge in self.syndrome_edges:
                    ek = edge.get("western_disease_key", "").lower()
                    if original_disease.lower() in ek or ek in original_disease.lower():
                        # 额外检查：排除误匹配（如 '糖尿病' 匹配 '儿童期糖尿病'）
                        if _is_pediatric_disease(ek) and hasattr(self, "_current_age") and _is_pediatric_age(self._current_age) is False:
                            continue
                        edge_candidates.append(edge)
            # 若仍未匹配，尝试通过 runtime_view 架桥：original_disease → runtime_key → edge_key
            if not edge_candidates and original_disease:
                for rname, rrows in self._runtime_by_disease.items():
                    rk = (rrows[0].get("西医病名key") or "").strip().lower() if rrows else ""
                    if (original_disease.lower() in rname.lower() or rname.lower() in original_disease.lower() or
                            original_disease.lower() in rk or rk in original_disease.lower()):
                        # 用 runtime_key 查 edges
                        for edge in self.syndrome_edges:
                            ek = (edge.get("western_disease_key") or "").lower()
                            if ek == rk:
                                if _is_pediatric_disease(ek) and hasattr(self, "_current_age") and _is_pediatric_age(self._current_age) is False:
                                    continue
                                edge_candidates.append(edge)
                        if edge_candidates:
                            break

        # ── 有效性过滤 ──
        valid_edge_candidates = [e for e in edge_candidates if is_valid_syndrome_edge(e)]

        if valid_edge_candidates:
            # 检查儿科约束（edges 路径也需要）
            edge_keys = {e.get("western_disease_key", "") for e in valid_edge_candidates}
            pediatric_keys = {k for k in edge_keys if _is_pediatric_disease(k)}
            is_child = _is_pediatric_age(self._current_age) if hasattr(self, "_current_age") else None
            need_age_confirmation = False
            if pediatric_keys:
                if is_child is False:
                    # 成人 → 移除儿科候选，保留非儿科候选
                    non_pediatric = [e for e in valid_edge_candidates
                                     if not _is_pediatric_disease(e.get("western_disease_key", ""))]
                    if non_pediatric:
                        valid_edge_candidates = non_pediatric
                    else:
                        # 全部是儿科候选 → fallback
                        return self._fallback_runtime_candidates(disease_key, tcm_anchor, stage, anchors, original_disease)
                elif is_child is None:
                    need_age_confirmation = True
            return {
                "candidates": valid_edge_candidates,
                "count": len(valid_edge_candidates),
                "source": "syndrome_pathology_edges",
                "fallback_used": False,
                "fallback_reason": "",
                "need_age_confirmation": need_age_confirmation,
                "matched_runtime_key": "",
            }

        # ── edges 为空壳/占位 → fallback 到 runtime_view ──
        empty_reason = (
            "no edges found"
            if not edge_candidates
            else f"all {len(edge_candidates)} edges are placeholder/empty"
        )
        return self._fallback_runtime_candidates(
            disease_key=disease_key,
            primary_disease=original_disease or "",
            stage=stage,
            tcm_anchor=tcm_anchor,
            empty_reason=empty_reason,
        )

    def _fallback_runtime_candidates(
        self,
        disease_key: str,
        primary_disease: str = "",
        stage: str = "",
        tcm_anchor: str = "",
        empty_reason: str = "",
    ) -> Dict:
        """从 m2_runtime_view.csv 回退加载候选证型。

        匹配优先级：
        ① disease_key 精确匹配（_runtime_by_key）
        ② western_disease_name / 西医病名 精确匹配
        ③ disease_alias / alias_key 匹配（模糊子串）
        ④ tcm_anchor + 西医系统约束匹配
        ⑤ 儿科/成人约束过滤

        Returns:
            {
                "candidates": [...],
                "count": int,
                "source": "m2_runtime_view_fallback",
                "fallback_used": bool,
                "fallback_reason": str,
                "need_age_confirmation": bool,
                "matched_runtime_key": str,
            }
        """
        def _is_all_placeholder(rlist: List[Dict]) -> bool:
            return all(
                not (r.get("证型") or "").strip()
                or (r.get("证型") or "").startswith("候选")
                for r in rlist
            )

        matched_key = ""
        rows: List[Dict] = []
        need_age_confirmation = False

        # ── ① disease_key 精确匹配 ──
        candidate_keys = [primary_disease, disease_key.lower(), disease_key]
        for ck in candidate_keys:
            if not ck:
                continue
            rlist = self._runtime_by_key.get(ck) or self._runtime_by_disease.get(ck)
            if rlist and not _is_all_placeholder(rlist):
                rows = rlist
                matched_key = ck
                break

        # ── ② 西医病名 精确匹配 ──
        if not rows and primary_disease:
            rlist = self._runtime_by_disease.get(primary_disease)
            if rlist and not _is_all_placeholder(rlist):
                rows = rlist
                matched_key = primary_disease

        # ── ③ alias 匹配（双向子串） ──
        if not rows:
            for dname, rlist in self._runtime_by_disease.items():
                if not rlist or _is_all_placeholder(rlist):
                    continue
                # 双向子串匹配
                if dname in primary_disease or primary_disease in dname or dname in disease_key or disease_key in dname:
                    # 检查儿科约束
                    if _is_pediatric_disease(dname):
                        is_child = _is_pediatric_age(self._current_age) if hasattr(self, "_current_age") else None
                        if is_child is False:
                            continue  # 成人，跳过儿科数据
                        if is_child is None:
                            need_age_confirmation = True  # 年龄未知，标记
                    rows = rlist
                    matched_key = dname
                    break

        # ── ④ tcm_anchor 匹配 ──
        if not rows and tcm_anchor:
            for dname, rlist in self._runtime_by_disease.items():
                if not rlist or _is_all_placeholder(rlist):
                    continue
                for r in rlist:
                    row_anchor = (r.get("中医二级病名/症状锚点") or "").strip()
                    if tcm_anchor in row_anchor or row_anchor in tcm_anchor:
                        rows.append(r)
                if rows:
                    matched_key = dname
                    break

        # ── 无匹配 ──
        if not rows:
            self._log_fallback(
                disease_key=disease_key,
                matched_runtime_key="",
                reason=f"no runtime_view match: {empty_reason}",
                need_age_confirmation=False,
            )
            return {
                "candidates": [],
                "count": 0,
                "source": "none",
                "fallback_used": True,
                "fallback_reason": f"syndrome_pathology_edges empty + no runtime_view match. {empty_reason}",
                "need_age_confirmation": False,
                "matched_runtime_key": "",
            }

        # ── ⑤ 儿科/成人约束过滤 ──
        # 如果匹配到的全是 [待标注] 占位符，尝试扩展匹配（同病名变体）
        all_placeholder = all(
            (r.get("证型") or "").strip() == "[待标注]"
            for r in rows
        )
        if all_placeholder and primary_disease:
            # 尝试 "肺炎" → "肺炎 (Pneumonia)" / "(Pneumonia)" 等变体
            related_keys = [
                f"{primary_disease} (Pneumonia)",
                f"（{primary_disease}）",
            ]
            for rk in related_keys:
                ext_rlist = self._runtime_by_disease.get(rk)
                if ext_rlist and any(
                    (r.get("证型") or "").strip() not in ("[待标注]", "") and not (r.get("证型") or "").startswith("候选")
                    for r in ext_rlist
                ):
                    rows = ext_rlist
                    matched_key = rk
                    all_placeholder = False
                    break
            # 也尝试 "急性肺炎" / chronic variant
            if all_placeholder:
                for alt_name, alt_rlist in self._runtime_by_disease.items():
                    if not alt_rlist:
                        continue
                    if all((r.get("证型") or "").strip() == "[待标注]" for r in alt_rlist):
                        continue
                    if primary_disease in alt_name or alt_name in primary_disease:
                        rows = alt_rlist
                        matched_key = alt_name
                        break

        is_pediatric_source = _is_pediatric_disease(matched_key) or any(
            _is_pediatric_disease(r.get("西医病名", "")) for r in rows[:1]
        )
        is_child = _is_pediatric_age(self._current_age) if hasattr(self, "_current_age") else None

        if is_pediatric_source:
            if is_child is False:
                # 成人 → 不允许套用儿科数据
                self._log_fallback(
                    disease_key=disease_key,
                    matched_runtime_key=matched_key,
                    reason=f"pediatric data ('{matched_key}') rejected for adult patient",
                    need_age_confirmation=False,
                )
                return {
                    "candidates": [],
                    "count": 0,
                    "source": "m2_runtime_view_rejected",
                    "fallback_used": True,
                    "fallback_reason": f"儿科数据'{matched_key}'不适用于成人患者。edges 为空壳 ({empty_reason})。",
                    "need_age_confirmation": False,
                    "matched_runtime_key": matched_key,
                }
            if is_child is None:
                need_age_confirmation = True

        # ── 构建候选证型列表（完整字段映射） ──
        candidates: List[Dict] = []
        for row in rows:
            syn = (row.get("证型") or "").strip()
            evidence = (row.get("症状/入选条件") or "").strip()
            exclusion = (row.get("排除条件") or "").strip()
            formula = (row.get("方剂") or "").strip()
            herbs_raw = (row.get("方剂组成") or "").strip()
            modifications_raw = (row.get("加减") or "").strip()
            syndrome_elements = (row.get("证型证素") or "").strip()
            pathology = (row.get("病理/证机概要") or "").strip()
            modern_hint = (row.get("现代病理提示") or "").strip()
            trace_id = (row.get("trace_id") or "").strip()
            review_status = (row.get("review_status") or "").strip()

            # 过滤占位证型和待标注证型
            if not syn or syn.startswith("候选") or syn == "[待标注]":
                continue

            # stage 匹配
            stage_col = (row.get("阶段/状态轴") or "").replace("；", ";")
            if stage and stage_col:
                allowed_stages = [s.strip() for s in stage_col.split(";") if s.strip()]
                if stage not in allowed_stages and "common_phase" not in allowed_stages:
                    continue

            # 解析方剂药物
            parsed_herbs = self._parse_formula_herbs(herbs_raw) if herbs_raw else []
            formula_components = [
                {"name": h.get("name", ""), "dosage": h.get("dosage", "[M3核定]")}
                for h in parsed_herbs
            ]

            # 解析加减
            modifications: List[Dict] = []
            if modifications_raw:
                for m_text in re.split(r"[；;]", modifications_raw):
                    m_text = m_text.strip()
                    if not m_text:
                        continue
                    modifications.append({
                        "raw_text": m_text,
                        "must_enter_m3": True,
                    })

            # 解析 required_symptoms / supportive_symptoms
            evidence_items = [s.strip() for s in re.split(r"[、；;,，]", evidence) if s.strip()]
            # 前3个症状视为 required，其余为 supportive
            required_symptoms = evidence_items[:3] if len(evidence_items) > 3 else evidence_items
            supportive_symptoms = evidence_items[3:] if len(evidence_items) > 3 else []

            # 解析 exclusion_symptoms
            exclusion_symptoms = [s.strip() for s in re.split(r"[、；;,，]", exclusion) if s.strip()]

            candidates.append({
                "edge_id": trace_id,
                "western_disease_key": row.get("西医病名key", ""),
                "western_disease_name": row.get("西医病名", ""),
                "tcm_anchor": row.get("中医二级病名/症状锚点", ""),
                "syndrome_name": syn,
                "syndrome_elements": syndrome_elements,
                "required_symptoms": required_symptoms,
                "supportive_symptoms": supportive_symptoms,
                "exclusion_symptoms": exclusion_symptoms,
                "formula": formula,
                "formula_components": formula_components,
                "modifications": modifications,
                "pathology_summary": pathology,
                "modern_pathology_hint": modern_hint,
                "red_flags": [],  # 将在 Step 8 单独检查
                "must_enter_m3": True,
                "fallback_used": True,
                "fallback_source": "m2_runtime_view.csv",
                "fallback_reason": f"edges empty: {empty_reason}",
                "source_trace_id": trace_id,
                "evidence_symptoms": evidence,
                "exclusion_conditions": exclusion,
                "syndrome_factors": syndrome_elements,
                "stage_constraint": stage_col,
                "_formula_name": formula,
                "_formula_herbs_raw": herbs_raw,
                "_modifications_raw": modifications_raw,
            })

        self._log_fallback(
            disease_key=disease_key,
            matched_runtime_key=matched_key,
            reason=empty_reason,
            need_age_confirmation=need_age_confirmation,
        )

        return {
            "candidates": candidates,
            "count": len(candidates),
            "source": "m2_runtime_view_fallback",
            "fallback_used": True,
            "fallback_reason": f"syndrome_pathology_edges empty or placeholder. {empty_reason}",
            "need_age_confirmation": need_age_confirmation,
            "matched_runtime_key": matched_key,
        }

    @staticmethod
    def _log_fallback(
        disease_key: str,
        matched_runtime_key: str,
        reason: str,
        need_age_confirmation: bool = False,
    ) -> None:
        """记录 M2_RUNTIME_VIEW_FALLBACK 日志事件。"""
        log_event = {
            "event": "M2_RUNTIME_VIEW_FALLBACK",
            "disease_key": disease_key,
            "matched_runtime_key": matched_runtime_key,
            "reason": reason,
            "fallback_source": "m2_runtime_view.csv",
            "need_age_confirmation": need_age_confirmation,
        }
        print(f"[M2V39 Fallback] {json.dumps(log_event, ensure_ascii=False)}")

    # ══════════════════════════════════════════════════════
    # Step 3: 语义对齐与强规则初筛
    # ══════════════════════════════════════════════════════

    def _step3_semantic_filter(
        self,
        candidates: List[Dict],
        symptoms: List[str],
        signs: List[str],
        tongue: str,
        pulse: str,
    ) -> Dict:
        """对每个候选证型做 required / exclusion 匹配。"""
        patient_raw = _extract_all_patient_phrases(symptoms, tongue, pulse, signs)
        positive_phrases, negative_phrases = _negation_filter(patient_raw)
        normalized = [_normalize_patient_phrase(p) for p in positive_phrases]

        results: List[Dict] = []
        passed: List[Dict] = []
        excluded: List[Dict] = []

        for edge in candidates:
            evidence_text = (edge.get("evidence_symptoms") or "").strip()
            exclusion_text = (edge.get("exclusion_conditions") or "").strip()
            syndrome_name = edge.get("syndrome_name", "")

            # 解析 required 症状（用、；分隔）
            required_items = [s.strip() for s in re.split(r"[、；;,，]", evidence_text) if s.strip()]
            # 解析 exclude 条件
            exclude_items = [s.strip() for s in re.split(r"[、；;,，]", exclusion_text) if s.strip()]

            required_met: List[str] = []
            required_missing: List[str] = []
            exclusion_hit: List[str] = []
            semantic_ambiguous: List[str] = []

            for req in required_items:
                matched, status, matched_phrase = _semantic_match_feature(req, positive_phrases, normalized)
                if matched:
                    required_met.append(req)
                elif status == "semantic_ambiguous":
                    semantic_ambiguous.append(req)
                else:
                    required_missing.append(req)

            for exc in exclude_items:
                if not exc or len(exc) < 1:
                    continue
                # 跳过与自身 evidence_symptoms 重叠的排除条件
                if exc in evidence_text or any(exc in req for req in required_items):
                    continue
                # 跳过过短的通用症状（如"咳痰""咳嗽""发热"），避免误伤肺炎类病种
                if len(exc) <= 3 and exc in {"咳嗽", "咳痰", "发热", "恶寒", "高热", "胸闷", "头痛"}:
                    continue
                matched, _, _ = _semantic_match_feature(exc, positive_phrases, normalized)
                if matched:
                    exclusion_hit.append(exc)

            result = {
                "syndrome_name": syndrome_name,
                "edge_id": edge.get("edge_id", ""),
                "edge_western_disease_key": edge.get("western_disease_key", ""),
                "required_met": required_met,
                "required_missing": required_missing,
                "exclusion_hit": exclusion_hit,
                "semantic_ambiguous": semantic_ambiguous,
                "evidence_symptoms": evidence_text,
                "exclusion_conditions": exclusion_text,
                "pathology_summary": edge.get("pathology_summary", ""),
                "syndrome_factors": edge.get("syndrome_factors", ""),
                "modern_pathology_hint": edge.get("modern_pathology_hint", ""),
            }
            results.append(result)

            if exclusion_hit:
                # 单个排除条件命中不足以排除——需 ≥2 个才触发（单个命中是常见噪音）
                if len(exclusion_hit) >= 2:
                    excluded.append({"name": syndrome_name, "reason": f"exclusion({len(exclusion_hit)}): {', '.join(exclusion_hit[:3])}"})
                # 单命中：不排除，继续按 required 判定
            if required_items and len(required_met) == 0 and not (exclusion_hit and len(exclusion_hit) >= 2):
                excluded.append({"name": syndrome_name, "reason": f"required全缺失: {', '.join(required_missing[:3])}"})
            elif not (exclusion_hit and len(exclusion_hit) >= 2) and not (required_items and len(required_met) == 0):
                passed.append(result)

        return {
            "results": results,
            "passed": passed,
            "excluded": excluded,
            "passed_count": len(passed),
            "excluded_count": len(excluded),
        }

    # ══════════════════════════════════════════════════════
    # Step 4: 排除法剪刀
    # ══════════════════════════════════════════════════════

    def _step4_exclusion_scissors(
        self,
        candidates: List[Dict],
        step3: Dict,
    ) -> Dict:
        """多候选时通过互斥规则裁减。"""
        passed = step3.get("passed", [])
        if not passed:
            return {"selected_syndrome": "", "confidence": "LOW", "rule_based": True,
                    "excluded_syndromes": step3.get("excluded", []), "reason": "无候选证型通过初筛"}

        if len(passed) == 1:
            p = passed[0]
            conf = "MEDIUM" if p.get("semantic_ambiguous") else "HIGH"
            return {
                "selected_syndrome": p["syndrome_name"],
                "edge_western_disease_key": p.get("edge_western_disease_key", ""),
                "confidence": conf,
                "rule_based": True,
                "excluded_syndromes": step3.get("excluded", []),
                "traceability": {
                    "required_met": p.get("required_met", []),
                    "exclusion_hit": p.get("exclusion_hit", []),
                    "semantic_ambiguous": p.get("semantic_ambiguous", []),
                },
                "reason": "唯一通过候选",
            }

        # 多候选：按 required_met 数量 + 无 semantic_ambiguous 择优
        scored = []
        for p in passed:
            score = len(p.get("required_met", [])) * 2
            if not p.get("semantic_ambiguous"):
                score += 3
            if p.get("exclusion_hit"):
                score -= 5
            scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)

        best = scored[0][1]
        second = scored[1][1] if len(scored) > 1 else None

        conf = "MEDIUM"
        if second and scored[0][0] > scored[1][0] + 3:
            conf = "HIGH"

        return {
            "selected_syndrome": best["syndrome_name"],
            "edge_western_disease_key": best.get("edge_western_disease_key", ""),
            "confidence": conf,
            "rule_based": True,
            "excluded_syndromes": step3.get("excluded", []),
            "traceability": {
                "required_met": best.get("required_met", []),
                "exclusion_hit": best.get("exclusion_hit", []),
                "semantic_ambiguous": best.get("semantic_ambiguous", []),
            },
            "reason": f"多候选择优（score gap={scored[0][0]-scored[1][0] if second else 'N/A'}）",
        }

    # ══════════════════════════════════════════════════════
    # Step 5: LLM 因果复核
    # ══════════════════════════════════════════════════════

    def _step5_llm_review(
        self,
        step4: Dict,
        symptoms: List[str],
        signs: List[str],
        tongue: str,
        pulse: str,
        disease_key: str,
        tcm_anchor: str,
    ) -> Dict:
        """仅当规则引擎无法唯一确定时调用 LLM。"""
        if not self._llm_available():
            return {"called": False, "reason": "LLM 未配置或已禁用"}

        conf = step4.get("confidence", "LOW")
        if conf == "HIGH":
            return {"called": False, "reason": "规则引擎 HIGH 置信度，跳过 LLM 复核"}

        prompt = self._build_llm_review_prompt(step4, symptoms, signs, tongue, pulse, disease_key, tcm_anchor)
        try:
            raw = self.llm_callback(prompt) if self.llm_callback else ""
            parsed = self._parse_llm_json(raw)
            return {"called": True, "result": parsed, "raw": raw[:500]}
        except Exception as e:
            return {"called": True, "error": str(e)}

    def _build_llm_review_prompt(
        self,
        step4: Dict,
        symptoms: List[str],
        signs: List[str],
        tongue: str,
        pulse: str,
        disease_key: str,
        tcm_anchor: str,
    ) -> str:
        selected = step4.get("selected_syndrome", "")
        trace = step4.get("traceability", {})
        excluded = step4.get("excluded_syndromes", [])
        return f"""你是中医辨证专家，请对以下规则引擎的辨证结果做因果逻辑复核。

病名: {disease_key} / 中医锚点: {tcm_anchor}
患者症状: {'、'.join(symptoms)}
体征: {'、'.join(signs)}
舌象: {tongue}  脉象: {pulse}

规则引擎选择的证型: {selected}
匹配的必要症状: {', '.join(trace.get('required_met', []))}
模糊项: {', '.join(trace.get('semantic_ambiguous', []))}
已排除的证型: {', '.join(e.get('name', '') for e in excluded)}

复核要点：
1. 锁定的证型是否与患者关键症状（特别是舌脉）存在冲突？
2. 证型对应的病理机制是否能合理解释患者的主要临床表现？
3. 是否存在任何指向其他证型的强证据被忽略？

输出 JSON（仅 JSON，无其他文字）：
{{"pass": true, "conflict_detected": "", "suggestion": ""}}"""

    def _parse_llm_json(self, raw: str) -> Dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r'\{[^{}]*"pass"[^{}]*\}', raw, re.DOTALL)
            if m:
                return json.loads(m.group())
            return {"pass": True, "conflict_detected": "", "suggestion": "", "parse_error": True}

    def _llm_available(self) -> bool:
        if os.environ.get("M2_DISABLE_LLM", "").lower() in ("1", "true", "yes"):
            return False
        return bool(self.llm_callback or os.environ.get("DEEPSEEK_API_KEY"))

    # ══════════════════════════════════════════════════════
    # Step 6: 证型锁定与方剂提取
    # ══════════════════════════════════════════════════════

    def _step6_lock_syndrome(self, step4: Dict, step5: Dict, disease_key: str = "", candidates: List[Dict] = None) -> Dict:
        conf = step4.get("confidence", "LOW")
        llm_opinion = ""
        llm_discrepancy = None
        selected_syn = step4.get("selected_syndrome", "")

        if step5.get("called"):
            result = step5.get("result", {})
            if result.get("pass") is False:
                llm_opinion = "LLM 复核未通过"
                llm_discrepancy = result.get("conflict_detected", "")
                if conf == "HIGH":
                    conf = "MEDIUM"
            else:
                llm_opinion = "一致"
                if result.get("pass"):
                    if conf == "MEDIUM":
                        conf = "HIGH"

        # 从 runtime_view 查方剂（先用 edge 的原生 disease_key，fallback 到解析的 disease_key）
        edge_dk = step4.get("edge_western_disease_key", "").strip().lower()
        base_formula = ""
        core_herbs: List[Dict] = []
        formula_entry = None
        # 尝试 edge 的 native key
        if edge_dk:
            formula_entry = self._formula_index.get((edge_dk, selected_syn))
        # fallback 到解析的 disease_key
        if not formula_entry:
            formula_entry = self._formula_index.get((disease_key.lower(), selected_syn))
        # fallback: 用候选行自带的 formula 数据
        if not formula_entry and candidates:
            for c in candidates:
                if c.get("syndrome_name") == selected_syn and c.get("_formula_name"):
                    base_formula = c["_formula_name"]
                    core_herbs = self._parse_formula_herbs(c.get("_formula_herbs_raw", ""))
                    break
        if formula_entry:
            base_formula = formula_entry["formula_name"]
            core_herbs = self._parse_formula_herbs(formula_entry["formula_herbs_raw"])

        return {
            "selected_syndrome": selected_syn,
            "confidence": conf,
            "rule_based": step4.get("rule_based", True),
            "llm_opinion": llm_opinion or "未调用",
            "llm_discrepancy": llm_discrepancy,
            "excluded_syndromes": step4.get("excluded_syndromes", []),
            "traceability": step4.get("traceability", {}),
            "core_herbs": core_herbs,
            "base_formula": base_formula,
            "reason": step4.get("reason", ""),
        }

    @staticmethod
    def _parse_formula_herbs(raw: str) -> List[Dict]:
        """解析方剂组成字符串，提取药物名和剂量。

        输入示例: "二妙散: 黄柏(各15g)、苍术(各15g)"
                   "麻杏石甘汤: 炙麻黄6g；生石膏30g（先煎）；炒杏仁9g"
        输出: [{"name": "黄柏", "dosage": "15g"}, ...]
        """
        herbs: List[Dict] = []
        if not raw:
            return herbs
        # 去掉方名前缀（":"或"："前的内容）
        if "：" in raw:
            content = raw.split("：", 1)[-1]
        elif ":" in raw:
            content = raw.split(":", 1)[-1]
        else:
            content = raw
        content = content.strip()
        if not content:
            return herbs

        # 按分隔符拆分
        parts = re.split(r"[、；;]", content)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 提取制备说明 "(先煎)", "(包煎)" 等（只取全角/半角括号内容）
            prep_note = ""
            m_prep = re.search(r"[（(]([先煎后下包煎烊化冲服另煎]+)[）)]", part)
            if m_prep:
                prep_note = m_prep.group(1)
                part = part[:m_prep.start()] + part[m_prep.end():]
                part = part.strip()

            # 模式1: 药名(剂量) e.g. "黄柏(15g)" 或 "黄柏(各15g)"
            m = re.match(r'^(.+?)\((.+?)\)$', part)
            if m:
                name = m.group(1).strip()
                dosage = m.group(2).strip()
                dosage = dosage.replace("各", "").replace("(", "").replace(")", "").strip()
                if name:
                    herbs.append({"name": name, "dosage": dosage or "[M3核定]"})
                continue
            # 模式2: 药名 剂量 e.g. "炙麻黄6g"（制备已在上面剥离）
            m2 = re.match(r'^(.+?)(\d+\.?\d*\s*g)$', part)
            if m2:
                name = m2.group(1).strip()
                dosage = m2.group(2).strip()
                if name:
                    herbs.append({"name": name, "dosage": dosage})
                continue
            # 无法解析，存为未知
            if len(part) > 1:
                herbs.append({"name": part, "dosage": "[M3核定]"})
        return herbs

    # ══════════════════════════════════════════════════════
    # Step 7: 加减药物匹配
    # ══════════════════════════════════════════════════════

    def _step7_modifications(
        self,
        selected_syndrome: str,
        disease_key: str,
        tcm_anchor: str,
        stage: str,
        symptoms: List[str],
        signs: List[str],
        tongue: str,
        pulse: str,
    ) -> Dict:
        """从 modification_rules 匹配加减规则（v40 格式兼容）。"""
        matched: List[Dict] = []

        # 使用索引加速：先精确匹配 (disease_key, selected_syndrome)
        candidate_rules: List[Dict] = []
        index_key = (disease_key.lower(), selected_syndrome)
        candidate_rules.extend(self._mod_index.get(index_key, []))
        # 也试无 syndrome_constraint 的全局规则
        global_key = (disease_key.lower(), "")
        candidate_rules.extend(self._mod_index.get(global_key, []))
        # fallback: 遍历所有规则（如果索引为空）
        if not candidate_rules:
            candidate_rules = self.mod_rules

        for rule in candidate_rules:
            syn_constraint = rule.get("syndrome_constraint", "")
            dk = (rule.get("western_disease_key") or rule.get("disease_constraint") or "").strip().lower()
            rule_anchor = (rule.get("tcm_anchor") or rule.get("tcm_anchor_constraint") or "").strip()

            # 证型约束
            if syn_constraint and syn_constraint != selected_syndrome:
                continue
            # 病名约束
            if dk and dk not in (disease_key.lower(), ""):
                continue
            # 锚点约束
            if rule_anchor and tcm_anchor and rule_anchor != tcm_anchor:
                continue

            trigger_symptom = (rule.get("trigger_symptom") or "").strip()
            add_herb = (rule.get("add_herb") or "").strip()
            dosage = (rule.get("dosage") or "").strip()

            if not trigger_symptom or not add_herb:
                continue

            # 合成 raw_modification_text（v40 无此字段）
            raw_text = rule.get("raw_modification_text") or ""
            if not raw_text:
                raw_text = f"+{trigger_symptom}：{add_herb}{dosage}"

            # 匹配患者症状
            hit = self._match_trigger(trigger_symptom, symptoms, signs)
            if not hit:
                continue

            source = (rule.get("source") or "").strip()
            source_level = "A" if "教材" in source else ("B" if "医案" in source else "C")

            matched.append({
                "herb": add_herb,
                "dosage": dosage or "[M3核定]",
                "reason": raw_text,
                "source": source_level,
                "source_detail": source,
                "rule_id": rule.get("rule_id", ""),
                "must_enter_m3": rule.get("must_enter_m3", True),
            })

        # 去重
        seen = set()
        unique: List[Dict] = []
        for m in matched:
            key = m["herb"]
            if key not in seen:
                seen.add(key)
                unique.append(m)

        # 按 source 优先级排序：A > B > C
        priority = {"A": 0, "B": 1, "C": 2}
        unique.sort(key=lambda x: priority.get(x.get("source", "C"), 3))

        # 裁剪到 MAX_MODIFICATIONS
        if len(unique) > MAX_MODIFICATIONS:
            unique = unique[:MAX_MODIFICATIONS]

        return {"modifications": unique, "count": len(unique)}

    @staticmethod
    def _match_trigger(trigger: str, symptoms: List[str], signs: List[str]) -> bool:
        """检查 trigger_symptom 是否出现在患者症状/体征中。"""
        if not trigger:
            return False
        all_symptoms = symptoms + signs
        # 直接子串
        for s in all_symptoms:
            if trigger in s:
                return True
        # 分拆 trigger 后逐段匹配
        for part in re.split(r"[、，；;：:+]", trigger):
            part = part.strip()
            if len(part) < 2:
                continue
            for s in all_symptoms:
                if part in s:
                    return True
        return False

    # ══════════════════════════════════════════════════════
    # Step 8: 红旗物理熔断
    # ══════════════════════════════════════════════════════

    def _step8_red_flag_check(self, all_texts: str, disease_key: str) -> Dict:
        """遍历 red_flag_rules.json，检查红旗触发。"""
        triggered: List[Dict] = []
        for trigger_text, rule in self._rf_triggers:
            if not trigger_text:
                continue
            # 中英文分号分隔的多个关键词
            keywords = [t.strip() for t in re.split(r"[；;]", trigger_text) if t.strip()]
            for kw in keywords:
                if kw and kw in all_texts:
                    triggered.append(rule)
                    break

        if triggered:
            # 取最高 action 级别
            actions = [r.get("action", "") for r in triggered]
            if "EMERGENCY_STOP" in actions:
                action = "EMERGENCY_STOP"
            else:
                action = actions[0] if actions else "WARN"

            return {
                "triggered": True,
                "action": action,
                "rules": triggered[:5],
                "count": len(triggered),
                "message": f"红旗触发：{', '.join(r.get('rule_id', '') for r in triggered[:5])}，建议紧急就医。",
            }

        return {"triggered": False, "action": "NONE", "rules": [], "count": 0}

    # ══════════════════════════════════════════════════════
    # Step 9: 医案证据检索
    # ══════════════════════════════════════════════════════

    def _step9_case_evidence(
        self,
        disease_key: str,
        selected_syndrome: str,
        primary_disease: str,
    ) -> Dict:
        """检索 case_support_edges.json 获取名医案例摘要。

        匹配策略：按 disease_name 精确匹配（case_edges 的 disease_name 是中文病名）。
        """
        cases: List[Dict] = []

        # 尝试多种匹配策略
        for case in self.case_edges:
            c_disease = (case.get("disease_name") or "").strip()
            syn_name = case.get("syndrome_name") or ""

            # 用 primary_disease 匹配 case 的 disease_name
            if primary_disease and primary_disease not in c_disease and c_disease not in primary_disease:
                continue

            # 证型模糊匹配
            if selected_syndrome and selected_syndrome not in syn_name:
                continue

            cases.append({
                "edge_id": case.get("support_edge_id", ""),
                "disease_name": c_disease,
                "syndrome_name": syn_name,
                "top_formulas": case.get("top_formulas", ""),
                "case_summary": case.get("case_summary", ""),
                "support_role": case.get("support_role", ""),
            })

        return {"cases": cases[:3], "total": len(cases)}

    # ══════════════════════════════════════════════════════
    # Step 10: 处方审计
    # ══════════════════════════════════════════════════════

    def _step10_prescription_audit(
        self,
        herbs: List[str],
        tongue: str,
        pulse: str,
        symptoms: List[str],
        signs: List[str],
    ) -> Dict:
        warnings: List[str] = []
        all_text = " ".join(herbs)

        # 舌腻 → 化痰药
        if "腻" in tongue and not any(k in all_text for k in PHLEGM_HERB_KEYWORDS):
            warnings.append("舌苔腻，缺少化痰药（建议：半夏、陈皮、茯苓等）")

        # 舌暗/瘀点 → 活血药
        if any(k in tongue for k in ("暗", "紫", "瘀")) and not any(k in all_text for k in BLOOD_HERB_KEYWORDS):
            warnings.append("舌象提示血瘀，缺少活血药（建议：丹参、桃仁、川芎等）")

        return {"warnings": warnings, "passed": len(warnings) == 0}

    # ══════════════════════════════════════════════════════
    # 输出组装
    # ══════════════════════════════════════════════════════

    def _build_output(
        self,
        *,
        disease_key: str,
        primary_disease: str,
        stage: str,
        syndrome_result: Dict,
        modifications: List[Dict],
        case_evidence: List[Dict],
        audit: Dict,
        trace: Dict,
        red_flag_triggered: bool,
        fallback_used: bool = False,
        fallback_source: str = "",
        fallback_reason: str = "",
        need_age_confirmation: bool = False,
        matched_runtime_key: str = "",
    ) -> Dict:
        confidence = syndrome_result.get("confidence", "LOW")
        status = "PASS" if confidence != "LOW" else "REVIEW"
        if need_age_confirmation:
            status = "REVIEW"
        if fallback_used and not syndrome_result.get("selected_syndrome"):
            status = "REVIEW"
        return {
            "disease_key": disease_key,
            "primary_disease": primary_disease,
            "stage": stage,
            "syndrome_result": {
                "selected_syndrome": syndrome_result.get("selected_syndrome", ""),
                "confidence": confidence,
                "rule_based": syndrome_result.get("rule_based", True),
                "llm_opinion": syndrome_result.get("llm_opinion", ""),
                "llm_discrepancy": syndrome_result.get("llm_discrepancy"),
                "excluded_syndromes": syndrome_result.get("excluded_syndromes", []),
                "traceability": syndrome_result.get("traceability", {}),
            },
            "prescription": {
                "base_formula": syndrome_result.get("base_formula", ""),
                "core_herbs": syndrome_result.get("core_herbs", []),
                "modifications": modifications,
            },
            "case_evidence": case_evidence,
            "guardrail": {
                "red_flag_triggered": red_flag_triggered,
                "m3_required": True,
                "needs_human_review": confidence == "LOW" or need_age_confirmation,
                "audit_warnings": audit.get("warnings", []),
            },
            "m3_payload": {
                "diagnosis": f"{primary_disease}（{stage or '待分期'}）",
                "syndrome": syndrome_result.get("selected_syndrome", ""),
                "formula": syndrome_result.get("base_formula", ""),
                "herbs": [
                    m.get("herb", "") + " " + m.get("dosage", "")
                    for m in modifications
                ],
            },
            "fallback_used": fallback_used,
            "fallback_source": fallback_source,
            "fallback_reason": fallback_reason,
            "need_age_confirmation": need_age_confirmation,
            "matched_runtime_key": matched_runtime_key,
            "trace": trace,
            # 兼容旧字段
            "candidate_only": True,
            "must_enter_m3": True,
            "formal_prescription_allowed": False,
            "no_candidate": False,
            "status": status,
        }

    def _build_blocked_output(
        self,
        disease_key: str,
        primary_disease: str,
        step8: Dict,
        trace: Dict,
    ) -> Dict:
        return {
            "disease_key": disease_key,
            "primary_disease": primary_disease,
            "status": "SAFETY_BLOCK",
            "syndrome_result": {},
            "prescription": {"base_formula": "", "core_herbs": [], "modifications": []},
            "case_evidence": [],
            "guardrail": {
                "red_flag_triggered": True,
                "m3_required": True,
                "needs_human_review": True,
                "block_message": step8.get("message", ""),
            },
            "m3_payload": {},
            "trace": trace,
            "candidate_only": True,
            "must_enter_m3": True,
            "formal_prescription_allowed": False,
            "no_candidate": True,
        }

    def _build_no_candidate(
        self,
        disease_key: str,
        primary_disease: str,
        reason: str,
        trace: Dict,
    ) -> Dict:
        return {
            "disease_key": disease_key,
            "primary_disease": primary_disease,
            "status": "NO_CANDIDATE",
            "syndrome_result": {},
            "prescription": {"base_formula": "", "core_herbs": [], "modifications": []},
            "case_evidence": [],
            "guardrail": {
                "red_flag_triggered": False,
                "m3_required": True,
                "needs_human_review": True,
            },
            "m3_payload": {},
            "trace": trace,
            "no_candidate": True,
            "candidate_only": True,
            "must_enter_m3": True,
            "formal_prescription_allowed": False,
            "reason": reason,
        }


# ── 模块别名（便于切换导入） ────────────────────────────────
M2Engine = M2V39Engine


if __name__ == "__main__":
    engine = M2V39Engine()
    payload = M2Payload(
        disease_key="acute_bronchitis",
        stage="acute_phase",
        primary_disease="急性支气管炎",
        patient=PatientData(
            symptoms=["咳嗽", "痰黄黏稠", "咽痛", "发热"],
            signs=["咽部充血", "双肺呼吸音粗"],
            tongue="舌边尖红，苔薄黄",
            pulse="脉浮数",
            age=35,
            sex="男",
        ),
    )
    result = engine.process_payload(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
