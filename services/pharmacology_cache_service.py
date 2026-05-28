"""
pharmacology_cache_service.py
=============================
守一 CDSS — M2-2 药理加减候选层
接入 disease_pharmacology_cache.json 作为"药理靶向提示层"

集成位置: M2-1 → M2-2 → [pharmacology_addon_candidate_filter] → M3 → M2-3
接入层级: 仅限 M2-2/M2-3 之间

约束：
  1. 禁止接入 M1 诊断逻辑
  2. 禁止影响 M2-1 证型判断
  3. 禁止用药理数据替代主方选择
  4. 禁止自动扩展中药（所有候选药默认 allow_auto_add=false）
  5. 仅 SymMap 数据的药物不得自动加药，只作为 evidence_hint
"""

import json
import os
import re
from typing import Dict, List, Optional, Set, Tuple

# ── 数据分级常量 ──────────────────────────────────────

SOURCE_GRADE_A = "A"  # GPT 人工筛过 + SymMap 也支持
SOURCE_GRADE_B = "B"  # GPT 有明确中药-靶点-禁忌解释
SOURCE_GRADE_C = "C"  # 仅 SymMap 有靶点+中药，缺少完整证型/禁忌
SOURCE_GRADE_D = "D"  # 仅弱关联或空，不允许进入加减

# ── 禁忌词索引（用于快速匹配） ─────────────────────────

CONTRAINDICATION_KEYWORDS = {
    # 注意：心血管相关风险（高血压、冠心病、心律失常、甲亢等）
    # 不从禁忌规则层面自动拦截。因为麻黄等药物在经方中常配石膏/桂枝
    # 等反佐，单因素自动拦截容易误杀。这些风险由 M3 审方层综合判断。
    "孕妇": ["孕妇", "妊娠", "怀孕", "孕期", "孕"],
    "备孕": ["备孕", "备孕期", "计划妊娠"],
    "哺乳": ["哺乳", "哺乳期", "母乳", "哺乳妇女"],
    "儿童": ["儿童", "小儿", "婴幼儿", "新生儿", "儿科"],
    "老年体弱": ["老年", "体弱", "高龄", "年老", "衰弱"],
    "肝功能异常": ["肝功能异常", "肝功异常", "肝损伤", "肝酶升高", "转氨酶升高",
                     "活动性肝病", "胆汁淤积", "肝炎活动期", "肝病"],
    "肾功能异常": ["肾功能异常", "肾功异常", "肾损伤", "肾衰竭", "肾小球滤过率下降",
                     "血肌酐升高", "肾功能不全"],
    "白细胞减少": ["白细胞减少", "白细胞降低", "白细胞低", "粒细胞减少"],
    "骨髓抑制": ["骨髓抑制", "骨髓功能抑制", "造血功能抑制"],
    "严重感染": ["严重感染", "重症感染", "全身感染", "脓毒症", "败血症"],
    "出血倾向": ["出血倾向", "出血风险", "凝血异常", "紫癜", "血小板减少", "凝血障碍"],
    "抗凝药": ["华法林", "利伐沙班", "阿哌沙班", "达比加群", "抗凝治疗", "抗凝药"],
    "抗血小板药": ["阿司匹林", "氯吡格雷", "替格瑞洛", "双联抗血小板", "抗血小板药"],
    "他汀类药物": ["他汀", "阿托伐他汀", "瑞舒伐他汀", "辛伐他汀", "匹伐他汀"],
    "镇静催眠药": ["镇静药", "安眠药", "苯二氮卓", "地西泮", "阿普唑仑", "艾司唑仑"],
    "免疫抑制剂": ["免疫抑制", "环磷酰胺", "环孢素", "他克莫司", "霉酚酸酯",
                     "甲氨蝶呤", "来氟米特", "硫唑嘌呤"],
    # 英文别名映射（用于英文 risk_factors 匹配）
    "pregnancy": ["孕妇", "妊娠", "怀孕", "孕期", "孕", "pregnancy"],
    "pregnant": ["孕妇", "妊娠", "怀孕", "孕期", "孕", "pregnant"],
    "child": ["儿童", "小儿", "婴幼儿", "新生儿", "儿科", "child"],
    "hypertension": ["高血压", "hypertension"],
    "hypertensive": ["高血压", "hypertensive"],
    "bleeding": ["出血倾向", "出血风险", "凝血异常", "紫癜", "血小板减少", "凝血障碍", "bleeding"],
    "hypotension": ["低血压", "hypotension"],
    "renal": ["肾功能异常", "肾功异常", "肾损伤", "肾衰竭", "肾功能不全", "renal", "kidney"],
    "liver": ["肝功能异常", "肝功异常", "肝损伤", "肝酶升高", "liver", "hepatic"],
    "diabetes": ["糖尿病", "diabetes"],
    "bradycardia": ["心动过缓", "bradycardia"],
}

# ── 药理加减候选的自动准入边界 ───────────────────────
# 本层只决定“药理加减候选”能否进入候选池，不评价经方原方配伍。
# 麻黄类不在本层做单药禁忌直拦，避免破坏麻黄配石膏等经典配伍。
EXEMPT_RISKS = {"高血压", "冠心病", "心律失常", "甲亢", "心动过速", "房颤", "早搏"}
STRICT_PHARMACOLOGY_ADDON_BLOCK_RISKS = set()
MAHUANG_FAMILY = {"麻黄", "炙麻黄", "蜜麻黄", "生麻黄", "Ephedra"}


# ── 服务类 ─────────────────────────────────────────────

class PharmacologyCacheService:
    """药理缓存服务 — M2-2 加减候选层"""

    def __init__(self, cache_path: str = None, alias_path: str = None):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(script_dir)
        if cache_path is None:
            cache_path = os.path.join(project_dir, "data", "disease_pharmacology_cache.json")
        if alias_path is None:
            alias_path = os.path.join(project_dir, "m1_name_mapping.json")
        self.cache_path = cache_path
        self.cache: Dict[str, dict] = {}
        self.alias: Dict[str, str] = {}
        self.cache_loaded = False
        self._load_cache(cache_path)
        self._load_alias(alias_path)
        self._build_normalized_index()

    def _load_cache(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
            self.cache_loaded = True
        except Exception as e:
            print(f"[pharmacology_cache] ⚠ 加载失败: {e}")
            self.cache = {}
            self.cache_loaded = False

    def _load_alias(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.alias = json.load(f)
        except Exception:
            self.alias = {}

    def _build_normalized_index(self):
        self._name_index: Dict[str, str] = {}
        for key in self.cache:
            norm = self._normalize(key)
            if norm:
                self._name_index[norm] = key
            entry = self.cache[key]
            wdn = entry.get("western_disease_name", "")
            if wdn and wdn != key:
                wdn_norm = self._normalize(wdn)
                if wdn_norm and wdn_norm not in self._name_index:
                    self._name_index[wdn_norm] = key
        for cn, en in self.alias.items():
            for name in [cn, en]:
                norm = self._normalize(name)
                if norm and norm not in self._name_index:
                    for cache_key in self.cache:
                        ck_norm = self._normalize(cache_key)
                        if ck_norm == norm:
                            self._name_index[norm] = cache_key
                            break
                        wdn = self.cache[cache_key].get("western_disease_name", "")
                        wdn_norm = self._normalize(wdn)
                        if wdn_norm == norm:
                            self._name_index[norm] = cache_key
                            break

    @staticmethod
    def _normalize(name: str) -> str:
        if not name or not isinstance(name, str):
            return ""
        n = name.lower().strip()
        n = re.sub(r'\(.*?\)', '', n)
        n = re.sub(r'（.*?）', '', n)
        n = re.sub(r'\s+', '', n)
        return n

    def normalize_disease_name(self, name: str) -> str:
        return self._normalize(name)

    def match_pharmacology_by_m1_disease(self, m1_disease_name: str) -> Tuple[str, dict, str]:
        if not self.cache_loaded or not self.cache:
            return None, None, "no_cache"
        if not m1_disease_name:
            return None, None, "no_disease_match"
        norm_query = self._normalize(m1_disease_name)
        if norm_query in self._name_index:
            key = self._name_index[norm_query]
            if key and key in self.cache:
                return key, self.cache[key], "matched"
        for source_name in [m1_disease_name, m1_disease_name.lower()]:
            if source_name in self.alias:
                en_name = self.alias[source_name]
                en_norm = self._normalize(en_name)
                if en_norm in self._name_index:
                    key = self._name_index[en_norm]
                    if key and key in self.cache:
                        return key, self.cache[key], "matched"
        best_key = None
        best_len = 0
        for cache_key in self.cache:
            cache_norm = self._normalize(cache_key)
            if not cache_norm:
                continue
            if norm_query in cache_norm or cache_norm in norm_query:
                overlap = len(set(norm_query) & set(cache_norm))
                if overlap > best_len:
                    best_len = overlap
                    best_key = cache_key
            wdn = self.cache[cache_key].get("western_disease_name", "")
            wdn_norm = self._normalize(wdn)
            if wdn_norm and (norm_query in wdn_norm or wdn_norm in norm_query):
                overlap = len(set(norm_query) & set(wdn_norm))
                if overlap > best_len:
                    best_len = overlap
                    best_key = cache_key
        if best_key and best_len >= max(2, len(norm_query) * 0.4):
            return best_key, self.cache[best_key], "matched"
        return None, None, "no_disease_match"

    def get_source_grade(self, entry: dict) -> str:
        gpt_has = (len(entry.get("key_targets", [])) > 0 or
                   len(entry.get("evidence_based_herbs", [])) > 0 or
                   bool(entry.get("disease_pathway", "").strip()))
        symmap_has = (entry.get("target_count", 0) > 0 or
                      entry.get("herb_count", 0) > 0 or
                      len(entry.get("molecular_targets", [])) > 0)
        if gpt_has and symmap_has:
            return SOURCE_GRADE_A
        if gpt_has:
            return SOURCE_GRADE_B
        if symmap_has:
            return SOURCE_GRADE_C
        return SOURCE_GRADE_D

    def check_contraindications(self, herb_item, patient_risks: List[str]) -> Tuple[bool, str]:
        text = ""
        herb_name = ""
        if isinstance(herb_item, dict):
            text = herb_item.get("contraindications", "")
            herb_name = herb_item.get("herb_name", "")
        elif isinstance(herb_item, str):
            text = herb_item
            herb_name = herb_item
        if self._is_mahuang_family(herb_name):
            return False, ""
        if not text or not patient_risks:
            return False, ""
        text_lower = text.lower()
        for risk in patient_risks:
            risk_lower = risk.lower().strip()
            if risk_lower in STRICT_PHARMACOLOGY_ADDON_BLOCK_RISKS:
                if risk_lower in text_lower:
                    return True, risk
            if risk_lower in EXEMPT_RISKS:
                continue
            keywords = CONTRAINDICATION_KEYWORDS.get(risk_lower, [risk_lower])
            for kw in keywords:
                if kw.lower() in text_lower:
                    return True, risk
        return False, ""

    @staticmethod
    def _is_mahuang_family(herb_name: str) -> bool:
        return any(name in str(herb_name) for name in MAHUANG_FAMILY)

    def check_tcm_syndrome_compatibility(self, herb_item, current_syndrome: str) -> bool:
        suitable = ""
        if isinstance(herb_item, dict):
            suitable = herb_item.get("suitable_tcm_syndrome", "")
        if not suitable or not current_syndrome:
            return True
        # 精确匹配：当前证型必须在适宜证型列表中
        import re
        suitable_list = re.split(r'[、，,]', suitable)
        suitable_list = [s.strip() for s in suitable_list if s.strip()]
        for s in suitable_list:
            if current_syndrome in s or s in current_syndrome:
                return True
        # 宽松模式：如果不匹配但也不冲突（无明确禁忌词），返回 False 以阻止自动入方
        # 但不会标记为严重冲突
        return False

    def extract_pharmacology_candidates(
        self, m1_disease_name: str, current_syndrome: str = "", patient_risks: List[str] = None
    ) -> dict:
        if patient_risks is None:
            patient_risks = []
        result = {
            "pharmacology_addon_candidates": [],
            "pharmacology_evidence_hints": [],
            "rejected_pharmacology_candidates": [],
            "pharmacology_cache_trace": self._build_trace(loaded=False),
        }
        cache_key, cache_entry, skip_reason = self.match_pharmacology_by_m1_disease(m1_disease_name)
        if skip_reason != "matched" or cache_entry is None:
            result["pharmacology_cache_trace"] = self._build_trace(
                loaded=self.cache_loaded, matched=False, skip_reason=skip_reason,
                matched_disease_name=m1_disease_name,
            )
            return result
        grade = self.get_source_grade(cache_entry)
        has_targets = (cache_entry.get("target_count", 0) > 0 or
                       len(cache_entry.get("key_targets", [])) > 0)
        has_herbs = (cache_entry.get("herb_count", 0) > 0 or
                     len(cache_entry.get("evidence_based_herbs", [])) > 0)
        key_targets_list = cache_entry.get("key_targets", [])
        if not key_targets_list:
            key_targets_list = cache_entry.get("molecular_targets", [])[:6]
        herb_count = cache_entry.get("herb_count", 0)
        if herb_count == 0:
            herb_count = len(cache_entry.get("evidence_based_herbs", []))
        trace = self._build_trace(
            loaded=self.cache_loaded, matched=True, matched_disease_name=cache_key,
            source_grade=grade, key_targets_count=len(key_targets_list), herbs_count=herb_count,
        )
        result["pharmacology_cache_trace"] = trace
        if not has_herbs:
            if has_targets or grade in (SOURCE_GRADE_C, SOURCE_GRADE_D):
                result["pharmacology_evidence_hints"].append({
                    "disease_name": cache_key,
                    "key_targets": key_targets_list[:10],
                    "herbs_from_symmap": cache_entry.get("effective_herbs", [])[:6],
                    "herb_rows_preview": [
                        {"Chinese_name": h.get("Chinese_name", ""), "Pinyin_name": h.get("Pinyin_name", "")}
                        for h in cache_entry.get("herb_rows", [])[:6]
                    ],
                    "note": "仅作为网络药理候选，不自动入方" if grade == SOURCE_GRADE_C else "无可用中药数据",
                })
            trace["evidence_hints_count"] = len(result["pharmacology_evidence_hints"])
            return result
        if grade in (SOURCE_GRADE_A, SOURCE_GRADE_B):
            candidates = self._build_addon_candidates(cache_entry, cache_key, grade, current_syndrome, patient_risks)
            result["pharmacology_addon_candidates"] = candidates["accepted"]
            result["rejected_pharmacology_candidates"] = candidates["rejected"]
            trace["accepted_candidates_count"] = len(candidates["accepted"])
            trace["rejected_candidates_count"] = len(candidates["rejected"])
        if grade == SOURCE_GRADE_C:
            hint = {
                "disease_name": cache_key,
                "key_targets": cache_entry.get("molecular_targets", [])[:10],
                "herbs_from_symmap": cache_entry.get("effective_herbs", [])[:10],
                "herb_rows": [h for h in cache_entry.get("herb_rows", [])[:6]],
                "note": "仅作为网络药理候选，不自动入方",
                "source_type": "network_pharmacology_candidate",
                "allow_auto_add": False,
                "need_manual_review": True,
            }
            result["pharmacology_evidence_hints"].append(hint)
            trace["evidence_hints_count"] = len(result["pharmacology_evidence_hints"])
        return result

    def _build_addon_candidates(self, entry, disease_name: str, grade: str,
                                 current_syndrome: str, patient_risks: List[str]) -> dict:
        accepted = []
        rejected = []
        herbs = entry.get("evidence_based_herbs", [])
        for herb in herbs:
            if not isinstance(herb, dict):
                continue
            herb_name = herb.get("herb_name", "未知中药")
            contraindications = herb.get("contraindications", "")
            mechanism = herb.get("target_mechanism", "")
            suitable_syndrome = herb.get("suitable_tcm_syndrome", "")
            is_blocked, matched_contra = self.check_contraindications(herb, patient_risks)
            if is_blocked:
                rejected.append({
                    "herb_name": herb_name,
                    "reject_reason": f"禁忌命中: {matched_contra}",
                    "matched_contraindication": matched_contra,
                    "source_disease": disease_name,
                })
                continue
            if not self.check_tcm_syndrome_compatibility(herb, current_syndrome):
                rejected.append({
                    "herb_name": herb_name,
                    "reject_reason": f"证型不符: suitable=[{suitable_syndrome}], current=[{current_syndrome}]",
                    "matched_contraindication": "tcm_syndrome_not_matched",
                })
                continue
            accepted.append({
                "herb_name": herb_name,
                "source_disease": disease_name,
                "source_grade": grade,
                "source_type": "gpt_pharmacology_card",
                "matched_targets": entry.get("key_targets", []),
                "target_mechanism": mechanism,
                "suitable_tcm_syndrome": suitable_syndrome,
                "contraindications": contraindications,
                "allow_auto_add": False,
                "need_manual_review": True,
                "reason": f"GPT药理卡匹配({grade}级), 证型: {suitable_syndrome}",
            })
        return {"accepted": accepted, "rejected": rejected}

    def _build_trace(self, loaded=False, matched=False, matched_disease_name="",
                     source_grade="", key_targets_count=0, herbs_count=0,
                     accepted_candidates_count=0, evidence_hints_count=0,
                     rejected_candidates_count=0, skip_reason="") -> dict:
        return {
            "cache_loaded": loaded,
            "matched": matched,
            "matched_disease_name": matched_disease_name,
            "source_grade": source_grade,
            "key_targets_count": key_targets_count,
            "herbs_count": herbs_count,
            "accepted_candidates_count": accepted_candidates_count,
            "evidence_hints_count": evidence_hints_count,
            "rejected_candidates_count": rejected_candidates_count,
            "skip_reason": skip_reason,
        }


def load_pharmacology_cache(path: str = None) -> PharmacologyCacheService:
    return PharmacologyCacheService(cache_path=path)
