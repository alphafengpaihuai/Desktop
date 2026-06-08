"""
M2 症状证素加载器 — 连接症状证素表与双轨辨证
===============================================
职责：
- 读取 m2_symptom_factor_map.json（症状→证素映射）
- 读取 m2_factor_contradiction_map.json（证素互斥/反证规则）
- 读取 m2_symptom_contradiction_rules.json（复合症状反证规则）
- match_symptoms_to_factors: 症状+舌脉 → tcm_factor_evidence
- evaluate_factor_contradictions: factor_evidence → contradiction_result

使用方式：
    loader = SymptomFactorLoader()
    evidence = loader.match_symptoms_to_factors(symptoms, tongue, pulse)
    contradiction = loader.evaluate_factor_contradictions(evidence)
"""
import json
import os
import re
from typing import Dict, List, Optional, Tuple


class SymptomFactorLoader:
    """症状证素加载器 — 只做证据转换，不参与证型选择"""

    def __init__(self,
                 symptom_factor_path: str = "data/m2_knowledge/m2_symptom_factor_map.json",
                 contradiction_path: str = "data/m2_knowledge/m2_factor_contradiction_map.json",
                 symptom_rule_path: str = "data/m2_knowledge/m2_symptom_contradiction_rules.json"):
        self.symptom_factor_path = symptom_factor_path
        self.contradiction_path = contradiction_path
        self.symptom_rule_path = symptom_rule_path

        # 症状→证素映射表: symptom -> [factor1, factor2, ...]
        self.symptom_factor_map: Dict[str, List[str]] = {}
        # 否定症状集合
        self.negative_symptoms: set = set()
        # 症状逐字符索引（用于子串匹配）
        self._symptom_index: Dict[str, str] = {}

        self._load_symptom_factor_map()

        # 互斥规则
        self.hard_conflicts: List[dict] = []
        self.soft_conflicts: List[dict] = []
        self.allow_mixed: List[dict] = []
        self.strong_support_rules: List[dict] = []
        self.single_symptom_rules: List[dict] = []

        self._load_contradiction_map()
        self._load_symptom_rules()

    def _load_symptom_factor_map(self):
        if not os.path.exists(self.symptom_factor_path):
            print(f"[WARN] Symptom factor map not found: {self.symptom_factor_path}")
            return
        with open(self.symptom_factor_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            symptom = entry["raw_symptom"]
            factors = entry["tcm_factors"]
            self.symptom_factor_map[symptom] = factors
            if entry.get("is_negative", False):
                self.negative_symptoms.add(symptom)
            # Build char-index: first 2+ chars as key
            for i in range(len(symptom) - 1):
                key = symptom[i:i+2]
                if key not in self._symptom_index:
                    self._symptom_index[key] = symptom

    def _load_contradiction_map(self):
        if not os.path.exists(self.contradiction_path):
            print(f"[WARN] Factor contradiction map not found: {self.contradiction_path}")
            return
        with open(self.contradiction_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.hard_conflicts = data.get("hard_conflicts", [])
        self.soft_conflicts = data.get("soft_conflicts", [])
        self.allow_mixed = data.get("allow_mixed", [])

    def _load_symptom_rules(self):
        if not os.path.exists(self.symptom_rule_path):
            print(f"[WARN] Symptom contradiction rules not found: {self.symptom_rule_path}")
            return
        with open(self.symptom_rule_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.strong_support_rules = data.get("strong_support_rules", [])
        self.single_symptom_rules = data.get("single_symptom_rules", [])

    # ══════════════════════════════════════════════════════════
    #  公共 API
    # ══════════════════════════════════════════════════════════

    def load_symptom_factor_map(self) -> Dict[str, List[str]]:
        """返回 symptom → [factor] 的只读副本"""
        return dict(self.symptom_factor_map)

    def load_factor_contradiction_map(self) -> Dict:
        """返回互斥规则"""
        return {
            "hard_conflicts": self.hard_conflicts,
            "soft_conflicts": self.soft_conflicts,
            "allow_mixed": self.allow_mixed,
        }

    def load_symptom_contradiction_rules(self) -> Dict:
        """返回症状反证规则"""
        return {
            "strong_support_rules": self.strong_support_rules,
            "single_symptom_rules": self.single_symptom_rules,
        }

    @staticmethod
    def _normalize_text(text: str) -> str:
        """归一化文本，去除标点、空格"""
        text = re.sub(r'[，。；、\s]', '', text)
        return text

    @staticmethod
    def _is_negated(target: str, full_text: str) -> bool:
        """检查 target 是否被 full_text 中的否定前缀修饰"""
        negation_prefixes = ["无明显", "无明确", "无", "未", "没有", "否认", "不伴"]
        for np_ in sorted(negation_prefixes, key=len, reverse=True):
            if np_ + target in full_text.replace(" ", ""):
                return True
        return False

    def match_symptoms_to_factors(
        self,
        symptoms: Optional[List[str]] = None,
        tongue: str = "",
        pulse: str = "",
        negative_findings: Optional[List[str]] = None,
    ) -> Dict:
        """
        将症状/舌脉转为证素证据。

        Returns:
            {
                "matched_symptoms": [...],      # 成功映射的症状
                "tcm_factor_evidence": {         # 证素→证据症状列表
                    "热": ["发热", "舌红苔黄"],
                    "痰": ["黄痰", "苔腻"]
                },
                "negative_evidence": [...],      # 被否定排除的症状
                "unmatched_symptoms": [...],     # 未能映射的症状
                "contradiction_result": {         # 空占位，由 evaluate 填充
                    "strong_support": [],
                    "strong_against": [],
                    "soft_conflict": [],
                    "allow_mixed": [],
                    "need_human_review": False
                }
            }
        """
        symptoms = symptoms or []
        negative_findings = negative_findings or []

        # 合并所有文本用于否定检测
        combined_text = self._normalize_text(" ".join(symptoms))
        combined_text += " " + self._normalize_text(tongue)
        combined_text += " " + self._normalize_text(pulse)

        # 收集所有要分析的文本片段
        all_text_pieces: List[Tuple[str, str]] = []  # (raw_text, source)
        for s in symptoms:
            if s and isinstance(s, str):
                all_text_pieces.append((s.strip(), "symptom"))
        if tongue:
            all_text_pieces.append((tongue.strip(), "tongue"))
        if pulse:
            all_text_pieces.append((pulse.strip(), "pulse"))

        tcm_factor_evidence: Dict[str, List[str]] = {}
        matched_symptoms = []
        negative_evidence = []
        unmatched_symptoms = []

        # ── 步骤1：精确短语匹配 ──
        matched_set = set()
        symptom_names = sorted(self.symptom_factor_map.keys(), key=len, reverse=True)

        for raw_text, source in all_text_pieces:
            normalized = self._normalize_text(raw_text)
            best_match = None
            best_factors = []

            # 先尝试精确匹配
            for sym_name in symptom_names:
                sym_normalized = self._normalize_text(sym_name)
                if sym_normalized in normalized or normalized in sym_normalized:
                    best_match = sym_name
                    best_factors = self.symptom_factor_map[sym_name]
                    break

            if best_match:
                # 检查否定
                if self._is_negated(best_match, combined_text):
                    negative_evidence.append({
                        "symptom": raw_text,
                        "matched_entry": best_match,
                        "reason": "symptom negated by context",
                    })
                    continue

                matched_set.add(best_match)
                matched_symptoms.append(raw_text)
                for factor in best_factors:
                    if factor not in tcm_factor_evidence:
                        tcm_factor_evidence[factor] = []
                    if raw_text not in tcm_factor_evidence[factor]:
                        tcm_factor_evidence[factor].append(raw_text)
            else:
                # 子串匹配
                found = False
                for sym_name in symptom_names:
                    if sym_name in normalized:
                        if self._is_negated(sym_name, combined_text):
                            negative_evidence.append({
                                "symptom": raw_text,
                                "matched_entry": sym_name,
                                "reason": f"symptom '{sym_name}' negated",
                            })
                            negative_evidence.append({
                                "symptom": raw_text,
                                "matched_entry": sym_name,
                                "reason": f"substring '{sym_name}' in '{raw_text}' negated",
                            })
                            continue
                        matched_set.add(sym_name)
                        matched_symptoms.append(raw_text)
                        for factor in self.symptom_factor_map[sym_name]:
                            if factor not in tcm_factor_evidence:
                                tcm_factor_evidence[factor] = []
                            if raw_text not in tcm_factor_evidence[factor]:
                                tcm_factor_evidence[factor].append(raw_text)
                        found = True
                        break

                if not found:
                    unmatched_symptoms.append(raw_text)

        # ── 步骤2：单症状规则（补充证素） ──
        for rule in self.single_symptom_rules:
            rule_symptom = rule.get("symptom", "")
            if not rule_symptom:
                continue
            if rule_symptom in combined_text and not self._is_negated(rule_symptom, combined_text):
                for f in rule.get("strong_factors", []):
                    if f not in tcm_factor_evidence:
                        tcm_factor_evidence[f] = []
                    if rule_symptom not in tcm_factor_evidence[f]:
                        tcm_factor_evidence[f].append(rule_symptom)

        # ── 步骤3：复合强支持规则 ──
        for rule in self.strong_support_rules:
            pattern = rule.get("symptom_pattern", {})
            condition = rule.get("condition", "all")

            must_symptoms = pattern.get("must_include", [])
            must_tongue = pattern.get("must_include_tongue", [])
            must_other = pattern.get("must_include_other", [])

            hits = 0
            total = 0

            if must_symptoms:
                total += 1
                if any(kw in combined_text for kw in must_symptoms):
                    hits += 1
            if must_tongue:
                total += 1
                if any(kw in combined_text for kw in must_tongue):
                    hits += 1
            if must_other:
                total += 1
                if any(kw in combined_text for kw in must_other):
                    hits += 1

            if condition == "pattern_2_of_3" and hits >= min(2, total):
                for f in rule.get("strong_support_factors", []):
                    if f not in tcm_factor_evidence:
                        tcm_factor_evidence[f] = []
                    tc = rule.get("name", "")
                    tag = f"[{tc}]"
                    if tag not in tcm_factor_evidence[f]:
                        tcm_factor_evidence[f].append(tag)
            elif condition == "all" and hits == total:
                for f in rule.get("strong_support_factors", []):
                    if f not in tcm_factor_evidence:
                        tcm_factor_evidence[f] = []
                    tc = rule.get("name", "")
                    tag = f"[{tc}]"
                    if tag not in tcm_factor_evidence[f]:
                        tcm_factor_evidence[f].append(tag)

        return {
            "matched_symptoms": matched_symptoms,
            "tcm_factor_evidence": tcm_factor_evidence,
            "negative_evidence": negative_evidence,
            "unmatched_symptoms": unmatched_symptoms,
            "contradiction_result": {
                "strong_support": [],
                "strong_against": [],
                "soft_conflict": [],
                "allow_mixed": [],
                "need_human_review": False,
            },
        }

    def evaluate_factor_contradictions(
        self,
        tcm_factor_evidence: Dict[str, List[str]],
        symptoms: Optional[List[str]] = None,
        tongue: str = "",
        pulse: str = "",
        negative_findings: Optional[List[str]] = None,
        syndrome_names: Optional[List[str]] = None,
    ) -> Dict:
        """
        评估证素间的互斥 / 反证关系。

        Returns:
            {
                "strong_support": [...],   # 强支持的证素方向
                "strong_against": [...],   # 强反对的证素方向
                "soft_conflict": [...],    # 需要关注的冲突
                "allow_mixed": [...],      # 允许夹杂的证素组合
                "need_human_review": False # 是否需要人工审核
            }
        """
        result = {
            "strong_support": [],
            "strong_against": [],
            "soft_conflict": [],
            "allow_mixed": [],
            "need_human_review": False,
        }

        if not tcm_factor_evidence:
            return result

        present_factors = list(tcm_factor_evidence.keys())

        # ── Hard conflicts ──
        for conflict in self.hard_conflicts:
            fa = conflict["factor_a"]
            fb = conflict["factor_b"]
            if fa in present_factors and fb in present_factors:
                result["strong_against"].append({
                    "factor_a": fa,
                    "factor_b": fb,
                    "reason": conflict.get("reason", ""),
                })
                result["need_human_review"] = True

        # ── Soft conflicts ──
        for conflict in self.soft_conflicts:
            fa = conflict["factor_a"]
            fb = conflict["factor_b"]
            if fa in present_factors and fb in present_factors:
                result["soft_conflict"].append({
                    "factor_a": fa,
                    "factor_b": fb,
                    "reason": conflict.get("reason", ""),
                })

        # ── Allow mixed ──
        for mix in self.allow_mixed:
            factors = mix.get("factors", [])
            if len(factors) >= 2 and all(f in present_factors for f in factors):
                result["allow_mixed"].append({
                    "factors": factors,
                    "reason": mix.get("reason", ""),
                })

        # ── Strong support directions (from composite rules) ──
        combined_text = " ".join(tcm_factor_evidence.keys()).lower()
        combined_text += " " + self._normalize_text(tongue)
        combined_text += " " + self._normalize_text(" ".join(symptoms or []))

        for rule in self.strong_support_rules:
            pattern = rule.get("symptom_pattern", {})
            penalty = rule.get("penalty_syndromes", [])
            condition = rule.get("condition", "all")

            must_symptoms = pattern.get("must_include", [])
            must_tongue = pattern.get("must_include_tongue", [])
            must_other = pattern.get("must_include_other", [])

            hits = 0
            total = 0

            if must_symptoms:
                total += 1
                if any(kw in combined_text for kw in must_symptoms):
                    hits += 1
            if must_tongue:
                total += 1
                if any(kw in combined_text for kw in must_tongue):
                    hits += 1
            if must_other:
                total += 1
                if any(kw in combined_text for kw in must_other):
                    hits += 1

            triggered = False
            if condition == "pattern_2_of_3" and hits >= min(2, total):
                triggered = True
            elif condition == "all" and hits == total:
                triggered = True

            if triggered:
                for f in rule.get("strong_support_factors", []):
                    if f not in result["strong_support"]:
                        result["strong_support"].append(f)
                for p in penalty:
                    if p not in result["strong_against"]:
                        result["strong_against"].append({
                            "factor": p,
                            "reason": rule.get("logical", ""),
                        })

        return result
