"""
m2_pathology_syndrome_bridge.py
===============================
[DEPRECATED] 守一 CDSS — M2-0 病理锚点桥接层

此文件已被 m2_pattern_match_engine.py (V4) 自述为取代对象，
且 m2_pattern_match_engine.py 本身也未接入主链路。
当前活跃 M2 引擎为 M2SyndromeSelector (m2_engine.py)。

核心职责（历史）：
  1. 读取 M1 输出的西医诊断和病理轴
  2. 通过本地 JSON 规则库将病理轴映射到允许的中医病机候选
  3. 输出辨证约束

DEPRECATED since 2026-06 — 不会再有功能更新。
保留供 reports/trace_audit.py 和 tests 使用。
"""

import json
import os
import re
import urllib.request
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict


# ════════════════════════════════════════
# 数据类
# ════════════════════════════════════════

@dataclass
class PathologySummary:
    """M1 病理归纳"""
    primary_disease: str = ""
    primary_pathology_axes: List[str] = field(default_factory=list)
    all_matched_axes: List[dict] = field(default_factory=list)
    uncovered_axes: List[str] = field(default_factory=list)
    pathology_interpretation: str = ""
    missing_evidence: List[str] = field(default_factory=list)
    excluded_or_uncertain_pathologies: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    needs_external_check: List[str] = field(default_factory=list)


@dataclass
class TcmPathogenesisCandidate:
    """中医病机候选"""
    pathogenesis: str
    role: str  # primary_candidate / secondary_candidate / excluded
    supporting_features: List[str] = field(default_factory=list)
    conflicting_features: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source_axes: List[str] = field(default_factory=list)


@dataclass
class SyndromeConstraint:
    """辨证约束"""
    allowed_main_syndromes: List[str] = field(default_factory=list)
    not_allowed_as_main: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ComorbidityConstraint:
    """兼证约束"""
    allowed_as_comorbidity: List[str] = field(default_factory=list)
    max_add_herbs: int = 5
    reason: str = ""


@dataclass
class DecisionGate:
    """决策门"""
    can_enter_m2_formula_selection: bool = True
    needs_manual_review: bool = False
    reason: str = ""


@dataclass
class BridgeContext:
    """M2-0 桥接完整输出"""
    western_pathology_summary: PathologySummary = field(default_factory=PathologySummary)
    tcm_pathogenesis_candidates: List[TcmPathogenesisCandidate] = field(default_factory=list)
    main_syndrome_constraint: SyndromeConstraint = field(default_factory=SyndromeConstraint)
    comorbidity_syndrome_constraints: Dict[str, ComorbidityConstraint] = field(default_factory=dict)
    hidden_symptom_questions: List[str] = field(default_factory=list)
    decision_gate: DecisionGate = field(default_factory=DecisionGate)
    trace: dict = field(default_factory=lambda: {"rules_matched": [], "warnings": []})


# ════════════════════════════════════════
# 桥接引擎
# ════════════════════════════════════════

class M2PathologySyndromeBridge:
    """
    M2-0 病理锚点桥接层

    用法：
        bridge = M2PathologySyndromeBridge()
        ctx = bridge.build_bridge_context(m1_result, patient_features)

        if ctx.decision_gate.needs_manual_review:
            # 进入人工复核流程
            ...

        # 后续 M2 只能在 allowed_main_syndromes 中选择主证
    """

    # ── 默认规则库路径 ──
    RULES_PATH = "data/m2_pathology_to_syndrome_rules.json"
    STAGING_RULES_PATH = "data/m2_pathology_staging_rules.json"

    # ── 西医病理轴→中文释义（LLM prompt 用） ──
    AXIS_SEMANTIC_MAP = {
        "infection_or_inflammation": "感染/炎症反应",
        "allergic_th2_inflammation": "过敏/Th2型炎症",
        "autoimmune_or_immune_complex": "自身免疫/免疫复合物沉积",
        "mucosal_barrier_dysfunction": "黏膜屏障功能障碍",
        "lower_airway_involvement": "下气道受累",
        "airway_hyperreactivity": "气道高反应性",
        "secretion_or_retention": "分泌/潴留异常",
        "cardiovascular_or_circulatory_risk": "心血管/循环系统风险",
        "vascular_or_microcirculatory": "血管/微循环障碍",
        "coagulation_or_bleeding_disorder": "凝血/出血功能障碍",
        "hematopoietic_or_marrow_dysfunction": "造血/骨髓功能障碍",
        "endocrine_or_hormonal_dysregulation": "内分泌/激素失调",
        "metabolic_or_insulin_resistance": "代谢/胰岛素抵抗",
        "digestive_reflux_or_dysfunction": "消化/反流功能障碍",
        "hepatic_or_biliary_dysfunction": "肝/胆功能障碍",
        "urinary_renal_risk": "泌尿/肾脏风险",
        "structural_abnormality": "结构/形态异常",
        "neoplastic_or_proliferative": "肿瘤/增殖性病变",
        "fibrosis_or_tissue_remodeling": "纤维化/组织重塑",
        "immune_abnormality": "免疫异常/免疫调节紊乱",
        "stress_related": "应激/神经精神相关",
        "neurotransmitter_or_synaptic_dysfunction": "神经递质/突触功能障碍",
    }

    # ── 病名→默认病理轴映射（回退用） ──
    DISEASE_TO_DEFAULT_AXES = {
        "脱发": ["structural_abnormality", "endocrine_or_hormonal_dysregulation"],
        "雄激素性脱发": ["structural_abnormality", "endocrine_or_hormonal_dysregulation", "metabolic_or_insulin_resistance"],
        "斑秃": ["structural_abnormality", "immune_abnormality", "stress_related"],
        "失眠": ["stress_related", "endocrine_or_hormonal_dysregulation"],
        "便秘": ["digestive_reflux_or_dysfunction", "metabolic_or_insulin_resistance"],
        "痤疮": ["infection_or_inflammation", "endocrine_or_hormonal_dysregulation"],
        "冠心病": ["cardiovascular_or_circulatory_risk", "structural_abnormality"],
        "高血压": ["cardiovascular_or_circulatory_risk", "vascular_or_microcirculatory"],
        "糖尿病": ["metabolic_or_insulin_resistance", "endocrine_or_hormonal_dysregulation"],
        "上呼吸道感染": ["infection_or_inflammation"],
    }

    def __init__(self, rules_path: Optional[str] = None):
        self.rules_path = rules_path or self.RULES_PATH
        self._rules = None
        self._matrix = None
        self._matrix_loaded = False

    def _load_rules(self) -> dict:
        if self._rules is not None:
            return self._rules
        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                self._rules = json.load(f)
        except Exception as e:
            print(f"[M2-0] ⚠ 规则加载失败: {e}，使用空规则")
            self._rules = {}
        return self._rules

    def _load_multi_axis_matrix(self) -> dict:
        """加载症状多轴触发矩阵（替代硬编码 SYMPTOM_AXIS_TRIGGERS）"""
        if self._matrix_loaded:
            return self._matrix or {}
        matrix_path = "services/m2_symptom_multi_axis_matrix.json"
        if not os.path.exists(matrix_path):
            print(f"[M2-0] ⚠ 多轴矩阵文件不存在: {matrix_path}，回退硬编码触发器")
            self._matrix_loaded = True
            return {}
        try:
            with open(matrix_path, 'r', encoding='utf-8') as f:
                self._matrix = json.load(f)
            print(f"[M2-0] ✅ 加载多轴矩阵: {len(self._matrix.get('symptom_keywords', {}))}个关键词")
        except Exception as e:
            print(f"[M2-0] ⚠ 多轴矩阵加载失败: {e}，回退硬编码触发器")
            self._matrix = {}
        self._matrix_loaded = True
        return self._matrix

    def _resolve_symptom_axes(self, full_text: str) -> List[str]:
        """
        用多轴矩阵解析症状关键词→轴映射
        回退机制：
          1. 优先使用多轴矩阵（从规则库自动生成）
          2. 若矩阵不可用，回退硬编码 SYMPTOM_AXIS_TRIGGERS
        """
        matrix = self._load_multi_axis_matrix()
        keyword_map = matrix.get("symptom_keywords", {})

        extra_axes = set()

        for kw, data in keyword_map.items():
            if kw in full_text:
                for ax_name, ax_info in data.get("axes", {}).items():
                    if isinstance(ax_info, dict) and ax_info.get("relevance") != "negative":
                        extra_axes.add(ax_name)

        # 回退: 硬编码触发器
        if not extra_axes:
            SYMPTOM_AXIS_TRIGGERS = {
                "头发油": ["metabolic_or_insulin_resistance"],
                "头皮屑": ["metabolic_or_insulin_resistance"],
                "苔白腻": ["metabolic_or_insulin_resistance", "infection_or_inflammation"],
                "苔黄腻": ["infection_or_inflammation", "metabolic_or_insulin_resistance"],
                "口苦": ["hepatic_or_biliary_dysfunction", "stress_related"],
                "噩梦": ["stress_related"],
                "怕冷": ["endocrine_or_hormonal_dysregulation"],
                "乏力": ["hematopoietic_or_marrow_dysfunction", "immune_abnormality"],
                "胸痛": ["cardiovascular_or_circulatory_risk"],
                "胸闷": ["cardiovascular_or_circulatory_risk"],
                "紫癜": ["autoimmune_or_immune_complex", "coagulation_or_bleeding_disorder"],
                "发热": ["infection_or_inflammation"],
                "关节痛": ["autoimmune_or_immune_complex", "infection_or_inflammation"],
                "眨眼": ["neurotransmitter_or_synaptic_dysfunction", "stress_related"],
                "努嘴": ["neurotransmitter_or_synaptic_dysfunction"],
                "抽动": ["neurotransmitter_or_synaptic_dysfunction", "stress_related"],
                "挤眼睛": ["neurotransmitter_or_synaptic_dysfunction"],
                "注意力不集中": ["neurotransmitter_or_synaptic_dysfunction", "immune_abnormality"],
                "结膜充血": ["infection_or_inflammation", "structural_abnormality"],
                "眼袋大": ["structural_abnormality"],
                "熬夜": ["stress_related", "endocrine_or_hormonal_dysregulation"],
            }
            for kw, trigger_axes in SYMPTOM_AXIS_TRIGGERS.items():
                if kw in full_text:
                    for ax in trigger_axes:
                        extra_axes.add(ax)
            if extra_axes:
                print(f"[M2-0] ⚠ 使用硬编码回退触发器: {list(extra_axes)[:5]}...")

        return list(extra_axes)

    def _get_covered_axes(self, m1_result: dict) -> List[dict]:
        """从 M1 结果提取已覆盖的病理轴"""
        axes = []
        # M1 的 matched_disease 中有 covered_axes
        matched = m1_result.get("matched_disease")
        if matched and isinstance(matched, dict):
            for ax in matched.get("covered_axes", []):
                ax_name = ax.get("axis", "") if isinstance(ax, dict) else getattr(ax, "axis", "")
                ax_role = ax.get("db_role", "") if isinstance(ax, dict) else getattr(ax, "db_role", "")
                ax_weight = ax.get("db_weight", 0.5) if isinstance(ax, dict) else getattr(ax, "db_weight", 0.5)
                findings = ax.get("findings", []) if isinstance(ax, dict) else getattr(ax, "findings", [])
                if ax_name:
                    axes.append({
                        "axis": ax_name,
                        "role": ax_role,
                        "weight": ax_weight,
                        "findings": findings,
                    })
        return axes

    def _get_uncovered_axes(self, m1_result: dict) -> List[str]:
        """从 M1 结果提取未覆盖的病理轴"""
        unmatched = []
        matched = m1_result.get("matched_disease")
        if matched and isinstance(matched, dict):
            for ax in matched.get("uncovered_axes", []):
                ax_name = ax.get("axis", "") if isinstance(ax, dict) else getattr(ax, "axis", "")
                if ax_name:
                    unmatched.append(ax_name)
            # 也尝试从 uncovered_primary_axes 获取
            for ax_name in matched.get("uncovered_primary_axes", []):
                if ax_name not in unmatched:
                    unmatched.append(ax_name)
        return unmatched

    def _get_risk_flags(self, m1_result: dict) -> List[str]:
        """提取风险标记"""
        flags = []
        matched = m1_result.get("matched_disease")
        if matched and isinstance(matched, dict):
            for w in matched.get("warnings", []):
                if any(kw in w for kw in ["红旗", "排除", "不能解释", "高危"]):
                    flags.append(w)
        flags.extend(m1_result.get("risk_flags", []))
        return flags

    def _get_needs_external_check(self, m1_result: dict) -> List[str]:
        matched = m1_result.get("matched_disease")
        if matched and isinstance(matched, dict):
            return matched.get("needs_external_check", [])
        return []

    def summarize_western_pathology(
        self,
        m1_result: dict,
        patient_features: dict
    ) -> PathologySummary:
        """步骤1：归纳西医病理"""
        summary = PathologySummary()

        # 主诊断
        matched = m1_result.get("matched_disease")
        if matched and isinstance(matched, dict):
            summary.primary_disease = matched.get("disease_name", m1_result.get("query_disease", ""))
        else:
            summary.primary_disease = m1_result.get("query_disease", "")

        # 覆盖和未覆盖病理轴
        covered = self._get_covered_axes(m1_result)
        uncovered = self._get_uncovered_axes(m1_result)

        summary.all_matched_axes = covered
        summary.uncovered_axes = uncovered
        summary.primary_pathology_axes = [ax["axis"] for ax in covered if ax["role"] == "primary"]
        if not summary.primary_pathology_axes and covered:
            summary.primary_pathology_axes = [covered[0]["axis"]]
        if not summary.primary_pathology_axes:
            # 回退：从 disease 默认轴获取
            disease_name = summary.primary_disease
            for key, default_axes in self.DISEASE_TO_DEFAULT_AXES.items():
                if key in disease_name or disease_name in key:
                    summary.primary_pathology_axes = default_axes
                    break
            if not summary.primary_pathology_axes:
                summary.primary_pathology_axes = ["structural_abnormality"]

        # 病理归纳描述
        axes_desc = []
        for ax in covered:
            findings = ax.get("findings", [])
            finding_str = ", ".join(findings[:3]) if findings else "匹配"
            axes_desc.append(f"{ax['axis']}({ax['role']}:{finding_str})")
        summary.pathology_interpretation = f"主要病理轴: {', '.join(summary.primary_pathology_axes)}; 覆盖: {'; '.join(axes_desc)}"

        # 缺失证据
        if uncovered:
            summary.missing_evidence.append(f"未覆盖病理轴: {', '.join(uncovered)}")

        # 红旗标记
        summary.risk_flags = self._get_risk_flags(m1_result)
        summary.needs_external_check = self._get_needs_external_check(m1_result)

        return summary

    def infer_tcm_pathogenesis_candidates(
        self,
        pathology_summary: PathologySummary,
        patient_features: dict
    ) -> List[TcmPathogenesisCandidate]:
        """步骤2：病理轴→中医病机候选

        规则：
          - 每个病理轴映射到规则库中的 possible_tcm_pathogenesis
          - 用 patient_features 中的症状匹配 allowed_when/not_allowed_when
          - 计算 confidence
        """
        rules = self._load_rules()
        if not rules:
            return []

        symptoms_str = " ".join(patient_features.get("symptoms", []))
        tongue = patient_features.get("tongue", "")
        pulse = patient_features.get("pulse", "")
        full_text = f"{symptoms_str} {tongue} {pulse}"

        candidates = []  # list of TcmPathogenesisCandidate
        seen_pathogenesis = {}
        matched_rules = []

        # 遍历所有涉及的病理轴
        all_axes = [ax["axis"] for ax in pathology_summary.all_matched_axes]
        all_axes.extend(pathology_summary.primary_pathology_axes)
        all_axes = list(set(all_axes))

        # 症状关键词→额外轴映射（使用多轴触发矩阵）
        extra_axes_from_matrix = self._resolve_symptom_axes(full_text)
        for ax in extra_axes_from_matrix:
            if ax not in all_axes:
                all_axes.append(ax)

        for axis_name in all_axes:
            if axis_name not in rules:
                continue

            axis_rules = rules[axis_name].get("possible_tcm_pathogenesis", [])

            for rule in axis_rules:
                pathogenesis = rule["pathogenesis"]

                # 检查 allowed_when
                allowed = []
                for cond in rule.get("allowed_when", []):
                    if cond in full_text:
                        allowed.append(cond)

                # 检查 not_allowed_when
                conflicted = []
                for cond in rule.get("not_allowed_when", []):
                    if cond in full_text:
                        conflicted.append(cond)

                default_role = rule.get("default_role", "secondary_candidate")

                # 计算 confidence
                if allowed and not conflicted:
                    # 有支持特征，无冲突
                    confidence = min(1.0, 0.3 + len(allowed) * 0.2)
                    role = default_role
                elif allowed and conflicted:
                    # 既有支持又有冲突 → 低置信度
                    confidence = max(0.05, min(0.4, len(allowed) * 0.15 - len(conflicted) * 0.1))
                    role = "secondary_candidate"
                elif not allowed and not conflicted:
                    # 无支持也无冲突 → 理论可能但证据不足
                    confidence = 0.1
                    role = "secondary_candidate"
                else:
                    # 只有冲突
                    confidence = 0.0
                    role = "excluded"

                # 去重合并
                if pathogenesis in seen_pathogenesis:
                    existing = seen_pathogenesis[pathogenesis]
                    existing.supporting_features.extend(allowed)
                    existing.conflicting_features.extend(conflicted)
                    existing.source_axes.append(axis_name)
                    # 取最高 confidence
                    if confidence > existing.confidence:
                        existing.confidence = confidence
                    # role 升级（primary > secondary > excluded）
                    if role == "primary_candidate" and existing.role != "primary_candidate":
                        existing.role = "primary_candidate"
                    elif role == "secondary_candidate" and existing.role == "excluded":
                        existing.role = "secondary_candidate"
                else:
                    candidate = TcmPathogenesisCandidate(
                        pathogenesis=pathogenesis,
                        role=role,
                        supporting_features=list(set(allowed)),
                        conflicting_features=list(set(conflicted)),
                        confidence=confidence,
                        source_axes=[axis_name],
                    )
                    candidates.append(candidate)
                    seen_pathogenesis[pathogenesis] = candidate

                if allowed or conflicted:
                    matched_rules.append(f"{axis_name}→{pathogenesis}: allow={allowed}, conflict={conflicted}")

        # 排序：primary > secondary > excluded, 同角色按 confidence 降序
        role_order = {"primary_candidate": 0, "secondary_candidate": 1, "excluded": 2}
        candidates.sort(key=lambda c: (role_order.get(c.role, 9), -c.confidence))

        # 存 trace
        self._last_matched_rules = matched_rules


        # -- LLM 推理补充（当有不在规则库中的轴时） --
        rules = self._load_rules()
        has_unmapped = any(ax not in rules for ax in all_axes)
        primary_count = sum(1 for c in candidates if c.role == 'primary_candidate')

        if has_unmapped or primary_count < 2:
            llm_candidates = self._llm_infer_pathogenesis(
                all_axes=all_axes,
                full_text=full_text,
                existing_candidates=candidates,
                reason=f'unmapped={has_unmapped}, primary={primary_count}',
            )
            for lc in llm_candidates:
                if lc.pathogenesis not in seen_pathogenesis:
                    candidates.append(lc)
                    seen_pathogenesis[lc.pathogenesis] = lc
                    matched_rules.append(f'LLM推理: {lc.pathogenesis} ({lc.confidence})')
                elif lc.role == 'primary_candidate':
                    existing = seen_pathogenesis[lc.pathogenesis]
                    if existing.role != 'primary_candidate':
                        existing.role = 'primary_candidate'
                        existing.supporting_features.append('LLM升级为primary')

            llm_primary = [c for c in candidates if any('LLM' in f for f in c.supporting_features) and c.role == 'primary_candidate']
            non_llm_primary = [c for c in candidates if c.role == 'primary_candidate' and c not in llm_primary]
            others = [c for c in candidates if c.role != 'primary_candidate']
            candidates = llm_primary + non_llm_primary + others

            primary_count = sum(1 for c in candidates if c.role == 'primary_candidate')
            if primary_count == 0 and len(candidates) > 0:
                candidates.sort(key=lambda c: -c.confidence)
                for c in candidates[:3]:
                    if c.role == 'secondary_candidate' and c.confidence >= 0.2:
                        c.role = 'primary_candidate'
                        c.supporting_features.append('自动提升: 无primary候选')
        return candidates

    def build_syndrome_constraints(
        self,
        pathogenesis_candidates: List[TcmPathogenesisCandidate]
    ) -> SyndromeConstraint:
        """步骤3：从病机候选构建辨证约束"""
        constraint = SyndromeConstraint()

        primary_candidates = [c for c in pathogenesis_candidates if c.role == "primary_candidate" and c.confidence >= 0.2]
        secondary_candidates = [c for c in pathogenesis_candidates if c.role == "secondary_candidate" and c.confidence >= 0.1]
        excluded = [c for c in pathogenesis_candidates if c.role == "excluded"]

        # 允许的辨证方向
        if primary_candidates:
            # 取前3个高置信度的 primary
            constraint.allowed_main_syndromes = list(set(
                c.pathogenesis for c in primary_candidates[:3]
            ))

        if not constraint.allowed_main_syndromes:
            # 无 primary → 用最高置信度的 secondary
            if secondary_candidates:
                constraint.allowed_main_syndromes = [secondary_candidates[0].pathogenesis]

        # 明确排除的
        constraint.not_allowed_as_main = list(set(
            c.pathogenesis for c in excluded
        ))

        # 原因描述
        if constraint.allowed_main_syndromes:
            constraint.reason = f"病理轴映射支持: {'、'.join(constraint.allowed_main_syndromes)}"
        else:
            constraint.reason = "证据不足，未能确定允许的辨证方向"

        return constraint

    def build_comorbidity_constraints(
        self,
        pathogenesis_candidates: List[TcmPathogenesisCandidate],
        patient_features: dict
    ) -> Dict[str, ComorbidityConstraint]:
        """步骤4：兼证约束"""
        constraints = {}

        # 兼证候选：secondary_candidate 中 confidence < 0.5 的
        secondary = [c for c in pathogenesis_candidates
                     if c.role == "secondary_candidate" and c.confidence >= 0.1 and c.confidence < 0.5]

        # 提取兼证病名
        symptoms = patient_features.get("symptoms", [])
        syndrome_keywords = {
            "失眠": ["噩梦", "失眠", "入睡困难", "多梦", "夜惊"],
            "便秘": ["便秘", "大便干", "排便困难", "大便"],
            "口苦": ["口苦"],
            "口干": ["口干"],
            "焦虑": ["焦虑", "紧张", "压力"],
        }

        for synd_name, keywords in syndrome_keywords.items():
            if any(kw in " ".join(symptoms) for kw in keywords):
                constraint = ComorbidityConstraint(
                    allowed_as_comorbidity=[synd_name],
                    max_add_herbs=5,
                    reason=f"症状包含「{synd_name}」相关表现，允许作为兼证加味"
                )
                constraints[synd_name] = constraint

        return constraints

    def _llm_infer_pathogenesis(
        self,
        all_axes: List[str],
        full_text: str,
        existing_candidates: List[TcmPathogenesisCandidate],
        reason: str = "",
    ) -> List[TcmPathogenesisCandidate]:
        """
        LLM 推理主链路：对输入的病理轴和患者症状推理病机候选

        设计原则:
          - 主链路：规则库不足时实时推理
          - 输出可以写入暂存库（staging_rules.json），供后续人工校验入库
          - 置信度 > 0.6 的推理可以在下次直接复用
        """
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return []

        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        # 只传规则库中置信度最高的前3个primary候选（减少干扰）
        existing_pg = [c.pathogenesis for c in existing_candidates if c.role == "primary_candidate"][:3] or [c.pathogenesis for c in existing_candidates[:3]]

        # 构建轴的语义信息
        axis_descriptions = []
        for ax in all_axes:
            desc = self.AXIS_SEMANTIC_MAP.get(ax, ax)
            in_rules = "是" if ax in self._load_rules() else "否"
            axis_descriptions.append(f"- {ax}（{desc}）[已在规则库: {in_rules}]")

        prompt = (
            "你是一位中西医结合辨证专家。请根据患者的西医病理轴和临床表现，推理其中医病机候选。\n"
            "\n"
            "规则:\n"
            "1. 输出中医病机名词，如：肝阳化风、风痰上扰、湿热蕴结、肝肾阴虚、气滞血瘀、气血不足\n"
            "2. 每个病机给出推理依据（一句话关联西医病理轴和症状）\n"
            "3. 如果某个病机可能是主要病机，标注 role=primary_candidate；兼证/次证标 secondary_candidate\n"
            "4. 置信度 0.0-1.0，primary 不应低于 0.5\n"
            "5. 不要输出已有候选的重复病机\n"
            "6. 如果规则库已有候选不合理，你是最终判断者，可以提出更合理的病机并标为 primary_candidate\n"
            f"\n"
            f"已有候选病机(仅primary): {[p for p in existing_pg]}\n"
            "\n"
            f"患者表现: {full_text}\n"
            "\n"
            "相关西医病理轴（含规则库状态）:\n"
            f"{chr(10).join(axis_descriptions)}\n"
            "\n"
            "请按JSON格式输出:\n"
            '{"pathogenesis_candidates": [\n'
            '  {"pathogenesis": "病机名", "role": "primary_candidate/secondary_candidate", "reason": "推理依据", "confidence": 0.8, "related_axes": ["轴名"]},\n'
            "]}\n"
        )
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "你是中西医结合辨证专家。严格输出JSON，不输出方药。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        })

        try:
            req = urllib.request.Request(
                f"{api_base}/chat/completions",
                data=payload.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw_response = resp.read()
                result = json.loads(raw_response)
            response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not response_text or response_text.strip() == "":
                print("[M2-0] ⚠ LLM 返回空内容")
                return []
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            parsed = json.loads(cleaned)
            inferences = parsed.get("pathogenesis_candidates", [])
        except Exception as e:
            print(f"[M2-0] ⚠ LLM 病机推理失败: {e}")
            import traceback
            traceback.print_exc()
            return []

        candidates = []
        for inf in inferences:
            pg = inf.get("pathogenesis", "")
            if not pg or len(pg) < 2 or pg in existing_pg:
                continue
            role = inf.get("role", "secondary_candidate")
            if role not in ("primary_candidate", "secondary_candidate", "excluded"):
                role = "secondary_candidate"
            conf = min(max(inf.get("confidence", 0.3), 0.1), 1.0)
            reason_text = inf.get("reason", "")
            related = inf.get("related_axes", all_axes[:1])

            cand = TcmPathogenesisCandidate(
                pathogenesis=pg,
                role=role,
                supporting_features=[f"LLM: {reason_text}"] if reason_text else [],
                confidence=round(conf, 2),
                source_axes=related if isinstance(related, list) else all_axes[:1],
            )
            candidates.append(cand)

        # 写入暂存库（供后续人工校验）
        if candidates:
            self._save_to_staging_rules(all_axes, candidates, full_text)

        print(f"[M2-0] 🤖 LLM 病机推理: {len(candidates)}个候选")
        for c in candidates[:5]:
            print(f"       [{c.role:22s}] {c.pathogenesis:12s} conf={c.confidence:.2f}")
        return candidates

    def _save_to_staging_rules(
        self,
        axes: List[str],
        candidates: List[TcmPathogenesisCandidate],
        full_text: str,
    ) -> None:
        """
        将 LLM 推理结果写入暂存库（staging_rules.json）
        暂存库用于后续人工校验，只有校验通过后才移入正式规则库。
        避免低置信度 LLM 输出污染正式规则库。
        """
        staging_path = self.STAGING_RULES_PATH
        try:
            if os.path.exists(staging_path):
                with open(staging_path, 'r', encoding='utf-8') as f:
                    staging = json.load(f)
            else:
                staging = {"entries": [], "_meta": {"total": 0}}
        except Exception:
            staging = {"entries": [], "_meta": {"total": 0}}

        import datetime
        entry = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "axes": list(set(axes)),
            "patient_text_preview": full_text[:100],
            "llm_inferred_candidates": [
                {
                    "pathogenesis": c.pathogenesis,
                    "role": c.role,
                    "confidence": c.confidence,
                    "reason": c.supporting_features[0] if c.supporting_features else "",
                    "related_axes": c.source_axes,
                }
                for c in candidates
            ],
            "verified": False,  # 人工校验标记
            "notes": "",
        }
        staging["entries"].append(entry)
        staging["_meta"]["total"] = len(staging["entries"])

        os.makedirs(os.path.dirname(staging_path), exist_ok=True)
        with open(staging_path, 'w', encoding='utf-8') as f:
            json.dump(staging, f, ensure_ascii=False, indent=2)
        print(f"[M2-0] \U0001f4e5 LLM 推理已写入暂存库 ({len(candidates)}\u6761)")

    def generate_hidden_symptom_questions(
        self,
        pathology_summary: PathologySummary,
        patient_features: dict
    ) -> List[str]:
        """步骤5：基于病理轴生成隐匿症状追问"""
        rules = self._load_rules()
        questions = []

        all_axes = list(pathology_summary.primary_pathology_axes)
        all_axes.extend([ax["axis"] for ax in pathology_summary.all_matched_axes])
        all_axes.extend(pathology_summary.uncovered_axes)
        all_axes = list(set(all_axes))

        # 对未覆盖的轴，追问其规则中的问题
        for axis_name in all_axes:
            if axis_name not in rules:
                continue
            axis_questions = rules[axis_name].get("hidden_symptom_questions", [])
            for q in axis_questions:
                if q not in questions:
                    questions.append(q)

        return questions

    def validate_m2_entry_gate(
        self,
        pathology_summary: PathologySummary,
        syndrome_constraint: SyndromeConstraint,
        patient_features: dict
    ) -> DecisionGate:
        """步骤6：决策门——能否进入 M2 方剂选择"""
        gate = DecisionGate(can_enter_m2_formula_selection=True)

        # 条件1：有红旗标记
        if pathology_summary.risk_flags:
            gate.needs_manual_review = True
            gate.reason += f"M1 有风险标记: {'; '.join(pathology_summary.risk_flags[:3])}. "

        # 条件2：需外部检查排除高危
        if pathology_summary.needs_external_check:
            gate.needs_manual_review = True
            gate.reason += f"M1 建议外部检查: {'; '.join(pathology_summary.needs_external_check[:3])}. "

        # 条件3：无允许的主证
        if not syndrome_constraint.allowed_main_syndromes:
            gate.needs_manual_review = True
            gate.reason += "病理轴无法确认主证方向. "

        # 条件4：心血管轴未覆盖但有胸痛
        symptoms = patient_features.get("symptoms", [])
        has_chest_pain = any("胸痛" in s or "胸闷" in s for s in symptoms)
        cv_covered = any("cardiovascular" in ax["axis"] for ax in pathology_summary.all_matched_axes)
        if has_chest_pain and not cv_covered:
            gate.needs_manual_review = True
            gate.reason += "有胸痛症状但心血管病理轴未覆盖，需排除心脏原因后再行中医辨证. "

        # 条件5：M1 诊断为"不匹配"
        if pathology_summary.uncovered_axes and len(pathology_summary.uncovered_axes) >= 2:
            if not gate.needs_manual_review:
                gate.needs_manual_review = True
                gate.reason += f"多个病理轴未覆盖({', '.join(pathology_summary.uncovered_axes[:3])}). "

        if gate.needs_manual_review and not gate.reason:
            gate.reason = "需人工复核确认辨证方向"

        if not gate.needs_manual_review:
            gate.can_enter_m2_formula_selection = True

        return gate

    def build_bridge_context(
        self,
        m1_result: dict,
        patient_features: dict
    ) -> BridgeContext:
        """
        【主入口】构建 M2-0 桥接上下文

        Args:
            m1_result: M1 诊断结果 dict
                - query_disease: str
                - query_symptoms: List[str]
                - matched_disease: CandidateDiagnosis (dataclass or dict)
                - differential_list: List[CandidateDiagnosis]
            patient_features: 患者特征
                - symptoms: List[str]
                - tongue: str
                - pulse: str
                - labs: List[str]
                - imaging: List[str]
                - duration: str
                - age: str
                - gender: str

        Returns:
            BridgeContext
        """
        ctx = BridgeContext()
        trace_warnings = []
        trace_rules = []

        # 步骤1：归纳
        ctx.western_pathology_summary = self.summarize_western_pathology(m1_result, patient_features)
        trace_rules.append(f"primary_disease={ctx.western_pathology_summary.primary_disease}")
        trace_rules.append(f"primary_axes={ctx.western_pathology_summary.primary_pathology_axes}")

        # 步骤2：病机候选
        ctx.tcm_pathogenesis_candidates = self.infer_tcm_pathogenesis_candidates(
            ctx.western_pathology_summary, patient_features
        )
        candidates_primary = [c for c in ctx.tcm_pathogenesis_candidates if c.role == "primary_candidate"]
        candidates_secondary = [c for c in ctx.tcm_pathogenesis_candidates if c.role == "secondary_candidate"]
        trace_rules.append(f"pathogenesis_candidates: primary={len(candidates_primary)}, secondary={len(candidates_secondary)}")

        # 步骤3：辨证约束
        ctx.main_syndrome_constraint = self.build_syndrome_constraints(ctx.tcm_pathogenesis_candidates)
        trace_rules.append(f"allowed_main={ctx.main_syndrome_constraint.allowed_main_syndromes}")

        # 步骤4：兼证约束
        ctx.comorbidity_syndrome_constraints = self.build_comorbidity_constraints(
            ctx.tcm_pathogenesis_candidates, patient_features
        )

        # 步骤5：隐匿追问
        ctx.hidden_symptom_questions = self.generate_hidden_symptom_questions(
            ctx.western_pathology_summary, patient_features
        )

        # 步骤6：决策门
        ctx.decision_gate = self.validate_m2_entry_gate(
            ctx.western_pathology_summary, ctx.main_syndrome_constraint, patient_features
        )

        # 记录 trace
        if hasattr(self, '_last_matched_rules'):
            trace_rules.extend(self._last_matched_rules)

        # 检查"禁止 disease_name→syndrome 直接跳转"
        disease_name = m1_result.get("query_disease", "")
        syndrome_constraint = ctx.main_syndrome_constraint
        if not syndrome_constraint.allowed_main_syndromes and disease_name:
            trace_warnings.append(f"桥接层未找到从「{disease_name}」到任何证型的路径，禁止直接跳转")
        elif syndrome_constraint.allowed_main_syndromes:
            trace_warnings.append(f"桥接层成功约束: {disease_name} → {syndrome_constraint.allowed_main_syndromes}")

        ctx.trace["rules_matched"] = trace_rules[:30]
        ctx.trace["warnings"] = trace_warnings

        return ctx


def get_bridge() -> M2PathologySyndromeBridge:
    """获取桥接层实例"""
    return M2PathologySyndromeBridge()
