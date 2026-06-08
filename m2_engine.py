"""
M2 辨证选方引擎 — 中医辅助诊疗系统「守一」
================================================
权威规格：docs/prompt_review/M2_skill_code_synced_for_review.md（Skill，source of truth）。

核心原则：code-first 固定链路。证型/方剂/加减全部来自知识库，代码执行确定性规则；
LLM 仅为可选增强且离线隔离（M2_DISABLE_LLM / 无 DEEPSEEK_API_KEY 时完全跳过）。

固定链路（Skill §1）：
  resolve_m2_disease_key → _load_syndromes → SymptomFactorLoader
  → 双轨(Track1 _syndrome_scorer / Track2 _run_pathology_track) → _merge_dual_tracks
  → resolve_cold_heat_conflict_by_pathology → run_m2_1 / run_m2_2 / run_m2_3
"""
import os
import json
import re
from typing import Dict, List, Optional

from services.m2_symptom_factor_loader import SymptomFactorLoader


# ══════════════════════════════════════════════════════════════════════
#  因果解释型贝叶斯辨证 (Skill §6.4) — 共享常量
# ══════════════════════════════════════════════════════════════════════

# 否定前缀（与 scorer / loader 一致）
_M2_NEGATION_PREFIXES = ["无明显", "无明确", "不伴", "没有", "否认", "无", "未", "不"]

# 病机 → 预测表现（病机如何在因果上生成症状/舌脉/实验室）
_M2_PATHOGENESIS_PREDICTIONS = {
    "热": ["发热", "口干", "口苦", "烦躁", "苔黄", "舌红", "小便黄", "大便干", "脉数", "黄痰"],
    "寒": ["畏寒", "怕冷", "恶寒", "清稀", "苔白", "舌淡", "肢冷", "脉迟", "脉紧", "痰白"],
    "痰": ["咳嗽", "咳痰", "痰多", "苔腻", "脉滑", "胸闷", "喉中痰鸣"],
    "湿": ["苔腻", "便溏", "困重", "头重", "纳呆", "黏滞", "脉濡", "肢体困重"],
    "瘀": ["刺痛", "固定痛", "舌暗", "舌紫", "瘀斑", "脉涩", "肿块"],
    "虚": ["乏力", "气短", "神疲", "自汗", "食少", "喜按", "隐痛", "脉细弱"],
    "风": ["恶风", "鼻塞", "流涕", "咽痒", "头痛", "脉浮"],
    "气滞": ["胀痛", "胸闷", "善太息", "脉弦", "走窜"],
    "风寒": ["恶寒", "无汗", "痰白", "清稀", "脉浮紧", "鼻塞"],
    "风热": ["发热重", "微恶风", "咽痛", "舌边尖红", "脉浮数", "痰黄"],
    "湿热": ["苔黄腻", "肛门灼热", "小便短赤", "脉濡数", "黏滞"],
    "痰热": ["黄痰", "黄稠痰", "苔黄腻", "舌红", "脉滑数", "高热", "胸膈痞满"],
}

# 定义性热象/寒象证据（用于反事实违例判定，必须为患者阳性出现）
_M2_DEFINING_HEAT_FINDINGS = [
    "黄痰", "黄绿痰", "黄稠痰", "黄黏痰", "脓痰", "铁锈色",
    "苔黄", "黄腻", "苔黄腻", "舌红", "舌绛", "舌红绛", "口苦",
]
_M2_DEFINING_COLD_FINDINGS = [
    "痰白清稀", "白痰", "清稀", "泡沫痰",
    "苔白", "舌淡", "舌淡苔白", "畏寒", "怕冷", "形寒", "肢冷", "四肢不温",
]

# 证据分层（Skill §6.4.5）
_M2_CAUSAL_EVIDENCE_KEYWORDS = [
    # 痰质 / 舌苔 / 炎症 / 病理阶段 / 实验室
    "黄痰", "黄绿痰", "黄稠痰", "黄黏痰", "脓痰", "铁锈色",
    "痰白清稀", "白痰", "清稀", "泡沫痰",
    "苔黄", "黄腻", "苔黄腻", "舌红", "舌绛", "舌红绛",
    "苔白", "苔白腻", "舌淡", "苔腻", "厚腻",
    "发热", "高热", "壮热", "身热", "畏寒", "怕冷", "恶寒", "形寒", "肢冷", "四肢不温",
    "口苦", "口干", "口渴", "不渴",
    "白细胞", "中性", "crp", "pct", "降钙", "浸润", "实变", "渗出",
    "脉数", "滑数", "洪数", "脉迟", "脉紧", "脉沉", "脉细弱", "脉滑", "濡数",
    "完谷不化", "久泻", "五更泻", "滑脱不禁",
]
_M2_MANIFESTATION_EVIDENCE_KEYWORDS = [
    "咳嗽", "咳痰", "气促", "气短", "胸闷", "胸痛", "胸膈痞满",
    "腹痛", "腹泻", "里急后重", "下痢", "脓血", "黏液", "大便",
    "鼻塞", "流涕", "咽痛", "头痛", "喜按", "喜温", "隐痛",
    "恶心", "呕吐", "小便", "烦躁", "声音嘶哑", "吞咽", "肿块",
]
_M2_WEAK_EVIDENCE_KEYWORDS = [
    "乏力", "倦怠", "神疲", "身楚", "头身困重", "身重", "精神不振", "不适",
    "纳差", "纳呆", "食少", "消瘦",
]


class M2SyndromeSelector:
    """M2 辨证选方模块（code-first 固定双轨链路）

    输入：M1 的 primary_disease + 患者临床信息
    流程：disease_key 解析 → 锁定当前病证型池 → 症状证素证据 → 双轨辨证合并
          → 寒热冲突病理裁决 → M2-1 辨证 / M2-2 候选方 / M2-3 加减候选
    输出：selected_syndrome_node + 节点绑定方剂 + 节点内加减候选（candidate_only，须进 M3）
    """

    def __init__(self, kb_path: str = "data/m2_formula_knowledge.json",
                 herb_kb_path: str = "data/m3_herb_knowledge.json"):
        with open(kb_path, "r") as f:
            self.kb: Dict = json.load(f)

        # ── 🔒 加载时自动补全 herbs：若 full_decoction 的药物多于 herbs 字段，自动同步 ──
        self._auto_fill_herbs_from_decoction()

        self.herb_kb = {}
        try:
            with open(herb_kb_path, "r") as f:
                self.herb_kb = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 症状证素加载器 ──
        self.symptom_factor_loader = SymptomFactorLoader()

        # ── 病案参考库（内置） ──
        self.case_reference: Dict[str, dict] = {}
        self.case_kb_path = "data/m2_case_reference.json"
        try:
            with open(self.case_kb_path, "r") as f:
                self.case_reference = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 循证病案缓存（LLM 查询结果） ──
        self.case_cache_path = "data/m2_case_cache.json"
        self.case_cache: Dict[str, dict] = {}
        try:
            with open(self.case_cache_path, "r") as f:
                self.case_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 已引用过的病案日志（避免重复推荐） ──
        self.case_used_log_path = "data/m2_case_used_log.json"
        self.case_used_log: list = []
        try:
            with open(self.case_used_log_path, "r") as f:
                self.case_used_log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ══════════════════════════════════════════════════════
    #  统一 M2 disease key resolver
    # ══════════════════════════════════════════════════════

    _DISEASE_NAME_MAP = {
        "多发性抽动症": "抽动障碍",
        "Tourette综合征": "抽动障碍",
        "慢性荨麻疹": "荨麻疹",
        "胆碱能性荨麻疹": "荨麻疹",
        "过敏性荨麻疹": "荨麻疹",
        "皮肤瘙痒症": "皮肤瘙痒症",
        "老年性皮肤瘙痒症": "皮肤瘙痒症",
        "上气道咳嗽综合征": "急性上呼吸道感染",
        "支原体肺炎": "支原体肺炎",
        "肺炎": "肺炎",
        "社区获得性肺炎": "肺炎",
        "重症肺炎": "肺炎",
        "支气管肺炎": "肺炎",
        "咳嗽变异性哮喘": "喉源性咳嗽",
        "支气管炎": "急性支气管炎",
        "急性支气管炎": "急性支气管炎",
        "上呼吸道感染": "急性上呼吸道感染",
        "小儿抽动": "抽动障碍",
        "儿童抽动障碍": "抽动障碍",
        "儿童抽动症": "抽动障碍",
        "感冒": "急性上呼吸道感染",
        "过敏性鼻炎": "变应性鼻炎",
        "鼻炎": "慢性鼻炎",
        "扁桃体炎": "儿童急性扁桃体炎",
        "急性扁桃体炎": "急性化脓性扁桃体炎",
        "急性咽炎": "急性咽炎",
        "感染性发热": "感染性发热",
        "偏头痛": "头痛",
        "紧张型头痛": "头痛",
        "丛集性头痛": "头痛",
        "冠心病": "冠状动脉粥样硬化性心脏病",
        "急性上呼吸道感染": "急性上呼吸道感染",
        "新生儿黄疸": "新生儿黄疸",
        "反复呼吸道感染": "小儿反复呼吸道感染",
        "哮喘": "喉源性咳嗽",
        "腹泻": "腹泻",
        "消化不良": "小儿消化不良",
        "急性胃肠炎": "痢疾 (Dysentery)",
        "急性肠胃炎": "痢疾 (Dysentery)",
        "胃肠炎": "痢疾 (Dysentery)",
        "子宫腺肌病": "子宫腺肌症",
        "子宫腺肌症": "子宫腺肌症",
        "子宫内膜异位": "子宫内膜异位症",
        "子宫肌瘤": "子宫肌瘤",
        "月经过多": "月经过多",
        "月经后期": "月经后期",
        "月经先期": "月经先期",
        "痛经": "痛经",
        "眩晕": "眩晕",
        "不寐": "失眠",
        "失眠": "失眠",
        "头痛": "头痛",
        "颈椎病": "颈椎病",
        "脑供血不足": "短暂性脑缺血发作",
        "后循环缺血": "短暂性脑缺血发作",
        "耳石症": "眩晕",
        "食道反流": "反流性食管炎",
        "食管反流": "反流性食管炎",
        "食道返流": "反流性食管炎",
        "食管返流": "反流性食管炎",
        "反流性食管炎": "反流性食管炎",
        "胃食管反流病": "反流性食管炎",
        "鼻涕倒流": "慢性鼻窦炎",
        "鼻后滴漏": "慢性鼻窦炎",
        "鼻窦炎": "慢性鼻窦炎",
        "慢性鼻窦炎": "慢性鼻窦炎",
        "急性鼻窦炎": "慢性鼻窦炎",
    }

    _M2_FALLBACK = {
        "咳嗽": "支原体肺炎",
        "咳嗽病": "急性支气管炎",
        "发热": "急性上呼吸道感染",
        "急性扁桃体炎": "急性化脓性扁桃体炎",
        "急性咽炎": "急性咽炎",
        "感染性发热": "感染性发热",
        "肾结石": "尿石症",
        "尿石症": "尿石症",
        "腹痛": "小儿消化不良",
        "呕吐": "小儿消化不良",
        "腹泻病": "腹泻",
        "尿频": "非淋菌性尿道炎",
        "尿道炎": "非淋菌性尿道炎",
        "慢性尿道炎": "非淋菌性尿道炎",
        "尿路感染": "尿路感染",
        "热淋": "非淋菌性尿道炎",
        "淋证": "非淋菌性尿道炎",
        "前列腺炎": "慢性前列腺炎",
        "阳痿": "阳痿",
        "早泄": "早泄",
        "遗精": "非淋菌性尿道炎",
        "喉癌术后": "喉癌",
        "胰腺癌": "胰腺癌",
        "结肠癌": "结肠癌",
    }

    _HIGH_RISK_DISEASES = {
        "喉癌", "肺癌", "肝癌", "胃癌", "胰腺癌", "结肠癌", "食管癌",
        "恶性淋巴瘤", "白血病", "恶性黑色素瘤", "骨肉瘤",
        "急性心肌梗死", "脑出血", "脑梗死急性期",
        "感染性心内膜炎", "肺栓塞",
    }

    def _is_high_risk_disease(self, disease_name: str) -> bool:
        """判断是否为高风险疾病（需人工复核）"""
        if not disease_name:
            return False
        resolved = self.resolve_m2_disease_key(disease_name)
        if resolved in self._HIGH_RISK_DISEASES:
            return True
        # 也检查子串匹配
        for hr in self._HIGH_RISK_DISEASES:
            if hr in resolved or resolved in hr:
                return True
        # 检查原始疾病名是否含癌/瘤/恶性/梗死/出血
        _risk_keywords = ["癌", "恶性", "梗死", "出血", "栓塞"]
        for kw in _risk_keywords:
            if kw in disease_name:
                return True
        return False

    def resolve_m2_disease_key(self, disease_name: str) -> str:
        """统一 M2 disease key 解析器。

        解析优先级：
        1. 精确匹配 KB
        2. DISEASE_NAME_MAP
        3. m2_fallback
        4. 子串匹配（disease in kb_key 或 kb_key in disease）
        5. 去除英文括号后的匹配
        6. 返回原始名称（由调用方处理 no_candidate）
        """
        if not disease_name or not isinstance(disease_name, str):
            return disease_name

        name = disease_name.strip()
        if not name:
            return name

        # 1. 精确匹配 KB
        if name in self.kb:
            return name

        # 2. DISEASE_NAME_MAP
        mapped = self._DISEASE_NAME_MAP.get(name, "")
        if mapped and mapped in self.kb:
            return mapped

        # 3. m2_fallback
        fb = self._M2_FALLBACK.get(name, "")
        if fb and fb in self.kb:
            return fb

        # 4. 子串匹配
        for kb_key in self.kb:
            if name in kb_key or kb_key in name:
                return kb_key

        # 5. 去除末尾英文括号再匹配
        clean = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        if clean and clean != name:
            # 递归但不无限递归（只做一次）
            if clean in self.kb:
                return clean
            for kb_key in self.kb:
                if clean in kb_key or kb_key in clean:
                    return kb_key

        # 6. 找不到，返回原始名，调用方处理 no_candidate
        return name

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
    ) -> Dict:
        """兼容入口：内部串联 M2-1 辨证 trace 与 M2-2 候选方合同。"""
        m2_1 = self.run_m2_1_syndrome_reasoning(
            primary_disease=primary_disease,
            symptoms=symptoms,
            signs=signs,
            tongue=tongue,
            pulse=pulse,
            cold_heat=cold_heat,
            stool_urine=stool_urine,
            sleep=sleep,
            appetite=appetite,
            labs=labs,
            imaging=imaging,
            negative_findings=negative_findings,
            age=age,
            weight=weight,
        )
        if m2_1.get("status") == "NO_CANDIDATE":
            return m2_1

        m2_2 = self.run_m2_2_formula_candidates(
            primary_disease=primary_disease,
            syndrome_trace=m2_1.get("syndrome_trace", {}),
            symptoms=symptoms or [],
        )
        if m2_2.get("status") == "NO_CANDIDATE":
            m2_2["syndrome_trace"] = m2_1.get("syndrome_trace", {})
            m2_2["evidence_trace"] = m2_1.get("evidence_trace", [])
            return m2_2

        first_candidate = (m2_2.get("formula_candidates") or [{}])[0]
        formula = {
            "name": first_candidate.get("formula_name", ""),
            "herbs": first_candidate.get("herbs", []),
            "source": first_candidate.get("source", "data/m2_formula_knowledge.json"),
        }
        cases = self._search_cases(primary_disease, m2_1.get("selected_syndrome_key", ""))
        patient_info = {
            "symptoms": symptoms or [],
            "signs": signs or [],
            "tongue": tongue,
            "pulse": pulse,
            "cold_heat": cold_heat or [],
            "stool_urine": stool_urine or [],
            "sleep": sleep or [],
            "appetite": appetite or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "negative_findings": negative_findings or [],
            "age": age,
            "weight": weight,
        }
        modifications = self._generate_modifications(
            primary_disease,
            m2_1.get("selected_syndrome_key", ""),
            formula.get("herbs", []),
            cases,
            patient_info,
        )
        result = {
            "primary_disease": primary_disease,
            "status": "PASS",
            "stage": "M2",
            "disease_key": m2_1.get("input_trace", {}).get("resolved_m2_key", ""),
            "inferred_pathology_stage": m2_1.get("syndrome_trace", {}).get("matched_pathology", ""),
            "syndrome_differentiation": {
                "selected_syndrome": {
                    "name": m2_1.get("syndrome_trace", {}).get("syndrome_name", ""),
                    "reason": m2_1.get("syndrome_trace", {}).get("reasoning_summary", ""),
                },
                "differentiation_framework": m2_1.get("differentiation_framework", "脏腑"),
            },
            "selected_syndrome": m2_1.get("selected_syndrome_key", ""),
            "bound_formula": {
                "formula_name": formula.get("name", ""),
                "herbs": formula.get("herbs", []),
                "source": formula.get("source", ""),
            },
            "base_herbs": formula.get("herbs", []),
            "formula": formula,
            "formula_candidates": m2_2.get("formula_candidates", []),
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": False,
            "modifications": modifications,
            "modification_candidates": self._normalize_modification_candidates(
                modifications,
                primary_disease,
                symptoms or [],
            ),
            "added_herbs": [],
            "removed_herbs": [],
            "final_herbs": formula.get("herbs", []),
            "draft_dosage": "[M3核定]",
            "modification_reason": [],
            "case_references": cases,
            "syndrome_trace": m2_1.get("syndrome_trace", {}),
            "evidence_trace": m2_1.get("evidence_trace", []),
            "input_trace": m2_1.get("input_trace", {}),
            "reverse_audit": {},
            "needs_manual_review": self._is_high_risk_disease(primary_disease),
            "formal_prescription_allowed": False,
            "prescription_draft": True,
            "missing_key": "",
            "need_human_review": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def _strip_forbidden_prescription_fields(self, value):
        forbidden = {
            "prescription_text", "final_formula", "complete_formula", "final_prescription",
            "prescription", "full_formula", "dosage", "dose", "用法", "疗程",
        }
        if isinstance(value, dict):
            cleaned = {}
            removed = []
            for key, item in value.items():
                if key in forbidden:
                    removed.append(key)
                    continue
                if key == "formal_prescription_allowed":
                    cleaned[key] = False
                    if item is True:
                        removed.append(key)
                    continue
                cleaned[key] = self._strip_forbidden_prescription_fields(item)
            if removed:
                existing = cleaned.get("forbidden_fields_removed", [])
                cleaned["forbidden_fields_removed"] = sorted(set(existing + removed))
                cleaned["legacy_prescription_path_blocked"] = True
            return cleaned
        if isinstance(value, list):
            return [self._strip_forbidden_prescription_fields(item) for item in value]
        return value

    def _strip_internal_trace_fields(self, value):
        """移除内部 trace 字段，仅保留生产展示所需的字段。
        内部字段仍可在完整 JSON 响应中供调试/审计使用，
        但生产展示层应调用此方法过滤后再渲染。
        """
        internal_fields = {
            "candidate_scores", "evidence_trace", "input_trace",
            "matched_symptoms", "matched_pathology", "matched_tongue_pulse",
        }
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                if key in internal_fields:
                    continue
                cleaned[key] = self._strip_internal_trace_fields(item)
            return cleaned
        if isinstance(value, list):
            return [self._strip_internal_trace_fields(item) for item in value]
        return value

    def run_m2_1_syndrome_reasoning(
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
    ) -> Dict:
        """M2-1 独立入口：只做辨证 trace，不输出方剂、处方、剂量。"""
        symptoms = symptoms or []
        negative_findings = negative_findings or []
        # 解析 M2 disease key
        resolved_disease = self.resolve_m2_disease_key(primary_disease)
        input_trace = {
            "m1_primary_disease_received": primary_disease,
            "resolved_m2_key": resolved_disease,
            "symptoms_received": symptoms,
            "knowledge_source": "data/m2_formula_knowledge.json",
            "stage": "M2_1",
        }
        syndromes = self._load_syndromes(resolved_disease)
        if not syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=resolved_disease,
                reason=f"'{primary_disease}' 经解析为 '{resolved_disease}'，但知识库无证型数据",
                missing_key=resolved_disease,
                searched_terms=[primary_disease, resolved_disease],
                input_trace=input_trace,
            ))

        patient_info = {
            "symptoms": symptoms,
            "signs": signs or [],
            "tongue": tongue,
            "pulse": pulse,
            "cold_heat": cold_heat or [],
            "stool_urine": stool_urine or [],
            "sleep": sleep or [],
            "appetite": appetite or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "negative_findings": negative_findings,
            "age": age,
            "weight": weight,
        }

        # ── 症状证素库映射（M2-1 强制步骤） ──
        symptom_factor_evidence = self.symptom_factor_loader.match_symptoms_to_factors(
            symptoms=symptoms,
            tongue=tongue,
            pulse=pulse,
            negative_findings=patient_info.get("negative_findings", []),
        )
        factor_contradiction_result = self.symptom_factor_loader.evaluate_factor_contradictions(
            symptom_factor_evidence.get("tcm_factor_evidence", {}),
            symptoms=symptoms,
            tongue=tongue,
            pulse=pulse,
        )
        # 将证素证据注入 patient_info，供后续双轨使用
        patient_info["symptom_factor_evidence"] = symptom_factor_evidence
        patient_info["factor_contradiction_result"] = factor_contradiction_result

        # ── Track 1: 症状证素辨证轨 (Section 5.3) ──
        traditional_tcm_result = self._syndrome_scorer(primary_disease, syndromes, patient_info)

        # ── Track 2: 病名病理辨证轨 (Section 5.2) ──
        disease_type, framework = self._classify_disease_type(primary_disease, patient_info)
        pathology_based_result = self._run_pathology_track(
            resolved_disease, syndromes, patient_info, disease_type, framework,
        )

        parsed = None
        if self._llm_available():
            llm_result = self._call_llm(self._build_prompt(primary_disease, syndromes, patient_info, disease_type, framework))
            parsed = self._parse_llm_result(llm_result, syndromes) if llm_result else None

        # ── Merge dual tracks (Section 6) ──
        merged = self._merge_dual_tracks(
            primary_disease, syndromes, patient_info,
            pathology_based_result, traditional_tcm_result,
        )

        # ── 寒热冲突裁决：若证素检测到寒热互斥，由病理阶段/炎症证据裁决 ──
        cold_heat_resolution = None
        # 寒热冲突定向后在过滤池上 RE-ENTER 的因果贝叶斯裁决结果（若有，替代全池裁决）
        conflict_adjudicator_result = None
        if self._is_cold_heat_conflict(factor_contradiction_result):
            cold_heat_resolution = self.resolve_cold_heat_conflict_by_pathology(
                disease_key=resolved_disease,
                symptoms=symptoms,
                negative_findings=patient_info.get("negative_findings", []),
                tongue=tongue or "",
                pulse=pulse or "",
                labs=labs or [],
                imaging=imaging or [],
                pathology=pathology_based_result,
                factor_contradiction_result=factor_contradiction_result,
            )

        # 双轨一致/相近时取合并结果，否则 fallback 原有逻辑
        if merged.get("status") == "CONSISTENT" or merged.get("status") == "NEAR":
            selected_key = merged.get("selected_syndrome_key", "")
            reason = merged.get("reasoning_summary", "双轨辨证合并")
            syndrome_display = merged.get("syndrome_name", selected_key)
            confidence = merged.get("confidence", 0.5)
            matched_pathology = merged.get("matched_pathology", "")
            matched_symptoms = merged.get("matched_symptoms", [])
            matched_tongue_pulse = merged.get("matched_tongue_pulse", "")
            missing_info = merged.get("missing_info", "")

            # 寒热冲突：若裁决为热证方向但选了寒证，或反之，需调整
            need_human_review = bool(factor_contradiction_result and
                                     factor_contradiction_result.get("need_human_review", False))
            if cold_heat_resolution and need_human_review:
                main_dir = cold_heat_resolution.get("main_direction", "unknown")
                is_cold_syndrome = any(kw in syndrome_display for kw in ["风寒", "寒", "凉"])
                is_heat_syndrome = any(kw in syndrome_display for kw in ["热", "火", "温"])
                if main_dir == "heat" and is_cold_syndrome and not is_heat_syndrome:
                    # 裁决为热但选了寒 → 降权，标记为冲突
                    need_human_review = True
                    reason += f"；寒热冲突裁决→{main_dir}, 但当前选{selected_key}"
                elif main_dir == "cold" and is_heat_syndrome and not is_cold_syndrome:
                    need_human_review = True
                    reason += f"；寒热冲突裁决→{main_dir}, 但当前选{selected_key}"
                elif main_dir in ("mixed", "unknown"):
                    pass  # 混合方向不调整
                else:
                    # 裁决方向与所选证型方向一致，清除 need_human_review
                    need_human_review = False
                    reason += f"；寒热冲突已裁决→{main_dir}, 当前选型方向一致"
        elif merged.get("status") == "CONFLICT":
            # 冲突时: 寒热冲突优先用 pathology resolver 裁决
            if cold_heat_resolution:
                main_dir = cold_heat_resolution.get("main_direction", "unknown")
                # 用裁决方向筛选证型池（heat→含热/火/温/痰热/湿热；cold→含寒/风寒/凉）
                filtered_syndromes = {}
                for name, data in syndromes.items():
                    hay = (str(data.get("trigger", "")) + " "
                           + self._syndrome_display_name(name, data) + " " + name).lower()
                    if main_dir == "heat" and any(kw in hay for kw in ["热", "火", "温", "痰热", "湿热"]):
                        filtered_syndromes[name] = data
                    elif main_dir == "cold" and any(kw in hay for kw in ["寒", "风寒", "凉"]):
                        filtered_syndromes[name] = data
                    elif main_dir == "mixed":
                        filtered_syndromes[name] = data

                # 寒热冲突定向后：score 不直接定型。在过滤池上 RE-ENTER 因果贝叶斯裁决器选证型。
                conflict_adj = None
                if filtered_syndromes and main_dir != "unknown":
                    conflict_adj = self._bayesian_syndrome_adjudicator(
                        primary_disease=primary_disease,
                        disease_key=resolved_disease,
                        syndromes=filtered_syndromes,
                        patient_info=patient_info,
                        scorer_result=traditional_tcm_result,
                        pathology_result=pathology_based_result,
                        factor_contradiction_result=factor_contradiction_result,
                        merged=merged,
                        cold_heat_resolution=cold_heat_resolution,
                    )
                _adj_key = conflict_adj.get("adjudicated_syndrome_key", "") if conflict_adj else ""
                _adj_eligible = bool(conflict_adj) and any(
                    c.get("eligible") for c in conflict_adj.get("candidates", []))
                if conflict_adj and _adj_key and _adj_key in filtered_syndromes and _adj_eligible:
                    # 裁决器在过滤池内给出合格候选 → 由其决定证型与 confidence_band（不取最高分、不硬编码 0.6）
                    conflict_adjudicator_result = conflict_adj
                    selected_key = _adj_key
                    syndrome_data = syndromes.get(selected_key, {})
                    syndrome_display = self._syndrome_display_name(selected_key, syndrome_data)
                    matched_pathology = pathology_based_result.get("inferred_pathology_stage", "") if pathology_based_result else ""
                    matched_symptoms = []
                    matched_tongue_pulse = ""
                    missing_info = ""
                    reason = (f"寒热冲突裁决→{main_dir}，过滤池再入因果贝叶斯裁决器选 {selected_key}"
                              f"（{conflict_adj.get('adjudication_basis', '')}）")
                    # confidence_band 取自裁决器；numeric confidence 由 band 派生，不再硬编码
                    _adj_band = conflict_adj.get("adjudicated_confidence_band", "LOW")
                    confidence = {"HIGH": 0.85, "MEDIUM": 0.6, "LOW": 0.4}.get(_adj_band, 0.4)
                    need_human_review = bool(cold_heat_resolution.get("need_human_review", False)
                                             or conflict_adj.get("need_human_review", False))
                    parsed = traditional_tcm_result
                else:
                    # 过滤池为空 / 方向 unknown / 裁决器无合格候选 → 不强行取分，按低置信兜底（need_human_review）
                    parsed = parsed or traditional_tcm_result
                    if not parsed:
                        return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                            primary_disease=primary_disease,
                            reason=f"寒热冲突裁决→{main_dir}，过滤池再入裁决器无合格候选",
                            missing_key=primary_disease,
                            searched_terms=[primary_disease, main_dir],
                            input_trace=input_trace,
                        ))
                    selected_key = parsed.get("selected_syndrome", {}).get("name", "")
                    syndrome_data = syndromes.get(selected_key, {})
                    nm = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
                    syndrome_display = nm.group(1) if nm else selected_key
                    reason = f"寒热冲突裁决后无合格候选，单轨低置信兜底: {parsed.get('selected_syndrome', {}).get('reason', '')}"
                    confidence = 0.3  # 不强行定型，降低置信度
                    matched_pathology = parsed.get("matched_pathology", "") or ""
                    matched_symptoms = parsed.get("matched_symptoms", []) or []
                    matched_tongue_pulse = parsed.get("matched_tongue_pulse", "") or ""
                    missing_info = parsed.get("missing_info", "") or ""
                    need_human_review = True
            else:
                # 非寒热冲突，用原有单轨逻辑
                parsed = parsed or traditional_tcm_result
                if not parsed:
                    return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                        primary_disease=primary_disease,
                        reason="双轨冲突且无兜底",
                        missing_key=primary_disease,
                        searched_terms=[primary_disease],
                        input_trace=input_trace,
                    ))
                selected_key = parsed.get("selected_syndrome", {}).get("name", "")
                syndrome_data = syndromes.get(selected_key, {})
                nm = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
                syndrome_display = nm.group(1) if nm else selected_key
                reason = parsed.get("selected_syndrome", {}).get("reason", "") or parsed.get("reasoning", "") or "单轨兜底"
                confidence = parsed.get("confidence", 0.5)
                matched_pathology = parsed.get("matched_pathology", "") or merged.get("matched_pathology", "")
                matched_symptoms = parsed.get("matched_symptoms", []) or []
                matched_tongue_pulse = parsed.get("matched_tongue_pulse", "") or ""
                missing_info = parsed.get("missing_info", "") or ""
                need_human_review = True
        else:
            # 无双轨数据时的原有 fallback
            if not parsed:
                parsed = traditional_tcm_result
            if not parsed:
                return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                    primary_disease=primary_disease,
                    reason="LLM 与代码评分均无法得出辨证结果",
                    missing_key=primary_disease,
                    searched_terms=[primary_disease],
                    input_trace=input_trace,
                ))
            selected = parsed.get("selected_syndrome", {})
            selected_key = selected.get("name", "")
            syndrome_data = syndromes.get(selected_key, {})
            nm = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
            syndrome_display = nm.group(1) if nm else selected_key
            reason = selected.get("reason") or parsed.get("reasoning") or "代码证型评分匹配"
            if traditional_tcm_result:
                parsed.setdefault("candidate_scores", traditional_tcm_result.get("candidate_scores", []))
                parsed.setdefault("matched_symptoms", traditional_tcm_result.get("matched_symptoms", []))
                parsed.setdefault("matched_tongue_pulse", traditional_tcm_result.get("matched_tongue_pulse", ""))
                parsed.setdefault("matched_pathology", traditional_tcm_result.get("matched_pathology", ""))
                parsed.setdefault("confidence", traditional_tcm_result.get("confidence", 0.5))
            candidate_scores = parsed.get("candidate_scores", [])
            matched_symptoms = parsed.get("matched_symptoms", []) or []
            matched_tongue_pulse = parsed.get("matched_tongue_pulse", "") or ""
            matched_pathology = parsed.get("matched_pathology", "") or ""
            missing_info = parsed.get("missing_info", "") or ""
            confidence = parsed.get("confidence", 0.5) if parsed.get("confidence") else 0.5
            need_human_review = bool(factor_contradiction_result and
                                     factor_contradiction_result.get("need_human_review", False))

        # ── Skill §6.4 因果解释型贝叶斯辨证裁决：selected_syndrome_node 由裁决器输出 ──
        # 寒热冲突已在过滤池上 RE-ENTER 裁决器（conflict_adjudicator_result），直接复用其结果，
        # 使 confidence_band 与选中节点均源自定向后的因果裁决，避免再用全池裁决覆盖定向结论。
        if conflict_adjudicator_result is not None:
            bayesian_result = conflict_adjudicator_result
        else:
            bayesian_result = self._bayesian_syndrome_adjudicator(
                primary_disease=primary_disease,
                disease_key=resolved_disease,
                syndromes=syndromes,
                patient_info=patient_info,
                scorer_result=traditional_tcm_result,
                pathology_result=pathology_based_result,
                factor_contradiction_result=factor_contradiction_result,
                merged=merged,
                cold_heat_resolution=cold_heat_resolution,
            )
        _band_by_key = {c["syndrome_key"]: c["confidence_band"]
                        for c in bayesian_result.get("candidates", [])}
        _cand_by_key = {c["syndrome_key"]: c for c in bayesian_result.get("candidates", [])}

        # selected_syndrome_node 的最终归属：因果贝叶斯裁决器（score 仅为 likelihood_component）。
        # 双轨初选仅作裁决器输入/交叉校对；裁决器确认或（因反事实/阻断）改选。
        _dual_track_selected_key = selected_key
        _adjudicated_key = bayesian_result.get("adjudicated_syndrome_key", "")
        _adjudication_basis = bayesian_result.get("adjudication_basis", "")
        if _adjudicated_key and _adjudicated_key in syndromes:
            if _adjudicated_key != selected_key:
                # 裁决器改选（如双轨初选被反事实/强反证排除）→ 同步选中节点派生字段
                selected_key = _adjudicated_key
                _sd = syndromes.get(selected_key, {})
                _nm = re.search(r'<(.+?)>', str(_sd.get("trigger", "")))
                syndrome_display = _nm.group(1) if _nm else selected_key
                _adj_cand = _cand_by_key.get(selected_key, {})
                _adj_score = next((cs for cs in (traditional_tcm_result or {}).get("candidate_scores", [])
                                   if cs.get("syndrome_name") == syndrome_display), {})
                matched_symptoms = _adj_score.get("matched_symptoms", matched_symptoms) or matched_symptoms
                matched_tongue_pulse = _adj_score.get("matched_tongue_pulse", matched_tongue_pulse) or matched_tongue_pulse
                if _adj_score.get("matched_pathology"):
                    matched_pathology = _adj_score.get("matched_pathology")
                reason += (f"；因果贝叶斯裁决改选→{selected_key}"
                           f"（{_adjudication_basis}）")
        # confidence_band：取裁决器对选中节点的因果 band
        confidence_band = bayesian_result.get(
            "adjudicated_confidence_band",
            _band_by_key.get(selected_key, bayesian_result.get("top_confidence_band", "MEDIUM")))
        # 贝叶斯标记的复核需求并入（additive，不下调既有 True）
        need_human_review = bool(need_human_review or bayesian_result.get("need_human_review"))

        # 节点绑定方剂名（只读，仅供 §6.5 LLM 复核上下文；不写入 M2-1 处方字段）
        _bound_formula_name = str(syndromes.get(selected_key, {}).get("formula_name", "")) if selected_key else ""

        # ── Skill §6.5 受约束 LLM 复核（M2_DISABLE_LLM=1 时跳过，§6.6 离线稳定返回本地结果） ──
        llm_sanity_check = self._llm_sanity_check_syndrome_result(
            primary_disease=primary_disease,
            disease_key=resolved_disease,
            syndromes=syndromes,
            pathology_based_result=pathology_based_result,
            traditional_tcm_result=traditional_tcm_result,
            bayesian_result=bayesian_result,
            selected_syndrome_key=selected_key,
            bound_formula_name=_bound_formula_name,
            factor_contradiction_result=factor_contradiction_result,
            patient_info=patient_info,
        )
        # 仅当真实调用 LLM 时才据其复核结论调整（离线/测试不触发，保持本地结果稳定）
        if llm_sanity_check.get("llm_called"):
            _llm_review = llm_sanity_check.get("llm_review_result", "ACCEPT")
            _action = llm_sanity_check.get("applied_action", "keep_local")
            _new_key = llm_sanity_check.get("selected_syndrome_key", selected_key)
            if _action == "llm_reject_switch_within_pool" and _new_key in syndromes and _new_key != selected_key:
                selected_key = _new_key
                _sd = syndromes.get(selected_key, {})
                _nm = re.search(r'<(.+?)>', str(_sd.get("trigger", "")))
                syndrome_display = _nm.group(1) if _nm else selected_key
                reason += f"；LLM复核REJECT→改选池内 {selected_key}"
                confidence_band = _band_by_key.get(selected_key, confidence_band)
            if _llm_review == "QUESTION":
                confidence_band = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}.get(confidence_band, "LOW")
                need_human_review = True
            if _action == "reject_but_no_valid_alternative_low_confidence":
                confidence_band = "LOW"
                need_human_review = True

        # ── Skill §6.4.8 方剂干预验证（始终本地运行，不依赖 LLM） ──
        _selected_sd = syndromes.get(selected_key, {})
        _selected_cc = bayesian_result.get("selected_syndrome_node", {})
        _selected_chain = (_selected_cc.get("causal_chain", {})
                          if _selected_cc else bayesian_result.get("candidates", [{}])[0].get("causal_chain", {}) if bayesian_result.get("candidates") else {})
        formula_intervention_check = self._run_formula_intervention_check(
            selected_key, _selected_sd, _selected_chain)

        result = {
            "stage": "M2_1",
            "status": "PASS",
            "primary_disease": primary_disease,
            "disease_key": resolved_disease,
            "disease_type": disease_type,
            "differentiation_framework": framework,
            "inferred_pathology_stage": matched_pathology,
            "selected_syndrome_key": selected_key,
            "pathology_based_result": self._clean_track_result(pathology_based_result),
            "traditional_tcm_result": self._clean_track_result(traditional_tcm_result),
            "syndrome_comparison": {
                "status": merged.get("status", "SINGLE_TRACK"),
                "differential_syndrome": merged.get("differential_syndrome", ""),
                "conflict_reason": merged.get("conflict_reason", ""),
            },
            "syndrome_trace": {
                "syndrome_name": syndrome_display,
                "confidence": confidence,
                "confidence_band": confidence_band,
                "matched_symptoms": matched_symptoms[:6],
                "matched_pathology": matched_pathology,
                "matched_tongue_pulse": matched_tongue_pulse,
                "missing_info": missing_info,
                "candidate_scores": traditional_tcm_result.get("candidate_scores", []) if traditional_tcm_result else [],
                "reasoning_summary": reason,
            },
            # ── Skill §6.4/§6.5 新增（ADDITIVE，向后兼容） ──
            "selected_syndrome_node": {
                "syndrome_key": selected_key,
                "syndrome_name": syndrome_display,
                "prior_band": (_cand_by_key.get(selected_key, {}).get("prior_band")),
                "posterior_band": (_cand_by_key.get(selected_key, {}).get("posterior_band")),
                "likelihood_direction": (_cand_by_key.get(selected_key, {}).get("likelihood_direction")),
                "confidence_band": confidence_band,
                "posterior_rank": (_cand_by_key.get(selected_key, {}).get("posterior_rank")),
                "posterior": (_cand_by_key.get(selected_key, {}).get("posterior")),
                "causal_coverage": (_cand_by_key.get(selected_key, {}).get("causal_coverage")),
                # 因果链与反事实检验写入 M2-1 输出（Skill §6.4.1/6.4.4）
                "causal_chain": (_cand_by_key.get(selected_key, {}).get("causal_chain", {})),
                "counterfactual_check": (_cand_by_key.get(selected_key, {}).get("counterfactual_check", {})),
                "adjudicated_by": "causal_bayesian_adjudicator",
                "adjudication_basis": _adjudication_basis,
                "dual_track_preliminary_key": _dual_track_selected_key,
            },
            "bayesian_differentiation": bayesian_result,
            "formula_intervention_check": formula_intervention_check,
            "llm_sanity_check": llm_sanity_check,
            "evidence_trace": [
                {"source": "patient_symptom", "text": s}
                for s in symptoms if isinstance(s, str) and s.strip()
            ],
            "input_trace": input_trace,
            "reverse_audit": {},
            "formal_prescription_allowed": False,
            "needs_manual_review": self._is_high_risk_disease(primary_disease) or need_human_review,
            "need_human_review": need_human_review,
            "internal_trace": {
                "symptom_factor_kb_used": True,
                "symptom_factor_evidence": symptom_factor_evidence.get("tcm_factor_evidence", {}),
                "factor_contradiction_result": factor_contradiction_result,
                "symptom_kb_source": "症状证素表.xlsx",
                # ── Skill §6.4 因果辨证 trace（离线亦产出 causal_chain/counterfactual_check） ──
                "causal_differentiation": {
                    "prior_type": bayesian_result.get("prior_type", ""),
                    "posterior_fusion": bayesian_result.get("posterior_fusion", ""),
                    "adjudicated_syndrome_key": bayesian_result.get("adjudicated_syndrome_key", ""),
                    "adjudication_basis": _adjudication_basis,
                    "counterfactual_failed_syndromes": bayesian_result.get("counterfactual_failed_syndromes", []),
                    "blocked_syndromes": bayesian_result.get("blocked_syndromes", []),
                    "selected_causal_chain": (_cand_by_key.get(selected_key, {}).get("causal_chain", {})),
                    "selected_counterfactual_check": (_cand_by_key.get(selected_key, {}).get("counterfactual_check", {})),
                    "candidates_causal": [
                        {
                            "syndrome_key": c["syndrome_key"],
                            "syndrome_name": c["syndrome_name"],
                            "posterior_rank": c["posterior_rank"],
                            "causal_coverage": c.get("causal_coverage"),
                            "counterfactual_result": c.get("counterfactual_check", {}).get("counterfactual_result"),
                            "confidence_band": c.get("confidence_band"),
                            "blocked": c.get("blocked"),
                            "eligible": c.get("eligible"),
                        }
                        for c in bayesian_result.get("candidates", [])
                    ],
                },
            },
        }
        return self._strip_forbidden_prescription_fields(result)

    def run_m2_2_formula_candidates(
        self,
        primary_disease: str,
        syndrome_trace: Optional[Dict] = None,
        symptoms: Optional[List[str]] = None,
    ) -> Dict:
        """M2-2 独立入口：只查方剂候选，不输出正式处方。"""
        symptoms = symptoms or []
        syndrome_trace = syndrome_trace or {}
        resolved_disease = self.resolve_m2_disease_key(primary_disease)
        input_trace = {
            "m1_primary_disease_received": primary_disease,
            "resolved_m2_key": resolved_disease,
            "symptoms_received": symptoms,
            "knowledge_source": "data/m2_formula_knowledge.json",
            "stage": "M2_2",
        }
        syndromes = self._load_syndromes(resolved_disease)
        if not syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=resolved_disease,
                reason=f"M2-2 '{primary_disease}' -> '{resolved_disease}' 无证型数据",
                missing_key=resolved_disease,
                searched_terms=[primary_disease, resolved_disease],
                input_trace=input_trace,
            ))

        syndrome_name = syndrome_trace.get("syndrome_name", "")
        selected_key = ""
        for key, data in syndromes.items():
            trigger = str(data.get("trigger", ""))
            if syndrome_name and (syndrome_name == key or syndrome_name in trigger):
                selected_key = key
                break
        if not selected_key:
            fallback = self._fallback_parse(syndromes, primary_disease, symptoms)
            selected_key = (fallback or {}).get("selected_syndrome", {}).get("name", "")
        if not selected_key or selected_key not in syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"'{primary_disease}' 未找到可匹配证型对应方剂",
                missing_key=syndrome_name or primary_disease,
                searched_terms=[primary_disease, syndrome_name],
                input_trace=input_trace,
            ))

        syndrome_data = syndromes[selected_key]
        formula_name = syndrome_data.get("formula_name", "")
        herbs = syndrome_data.get("herbs", [])
        if not formula_name:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"证型 '{selected_key}' 缺少绑定方剂",
                missing_key=selected_key,
                searched_terms=[primary_disease, syndrome_name, selected_key],
                input_trace=input_trace,
            ))

        name_match = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
        display = syndrome_name or (name_match.group(1) if name_match else selected_key)
        result = {
            "stage": "M2_2",
            "status": "PASS",
            "primary_disease": primary_disease,
            "disease_key": resolved_disease,
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": False,
            "formula_candidates": [{
                "disease_name": primary_disease,
                "syndrome_name": display,
                "formula_name": formula_name,
                "herbs": herbs,
                "source": "data/m2_formula_knowledge.json",
            }],
            "reverse_audit": {},
            "searched_terms": [primary_disease, syndrome_name, selected_key],
            "input_trace": input_trace,
            "formal_prescription_allowed": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def run_m2_3_modification_candidates(
        self,
        primary_disease: str,
        formula_herbs: Optional[List[str]] = None,
        symptoms: Optional[List[str]] = None,
        syndrome_name: str = "",
        case_references: Optional[List[Dict]] = None,
    ) -> Dict:
        """M2-3 独立入口：基于证据源查询加减候选，不输出正式处方。"""
        symptoms = symptoms or []
        formula_herbs = formula_herbs or []
        case_references = case_references or []
        resolved_disease = self.resolve_m2_disease_key(primary_disease)
        input_trace = {
            "m1_primary_disease_received": primary_disease,
            "resolved_m2_key": resolved_disease,
            "symptoms_received": symptoms,
            "knowledge_source": "data/m2_formula_knowledge.json, data/m3_herb_knowledge.json, data/m2_case_cache.json",
            "stage": "M2_3",
        }

        # ── 策略1：知识库内显式加减规则（临床加减） ──
        explicit_additions = self._load_explicit_modifications(resolved_disease, syndrome_name)
        # ── 策略2：循证病案库查询 ──
        case_modifications = self._load_case_modifications(resolved_disease, syndrome_name)
        # ── 策略3：症状-药物映射（herb_kb） ──
        herb_kb_modifications = self._load_herb_kb_modifications(resolved_disease, symptoms)

        modifications = []
        seen_herbs = set(formula_herbs)

        # 合并所有候选，去重
        for src_list, src_label in [
            (explicit_additions, "显式加减规则"),
            (case_modifications, "名医病案"),
            (herb_kb_modifications, "药物知识库"),
        ]:
            for item in src_list:
                herb = item.get("herb_name") or item.get("herb", "")
                if not herb or herb in seen_herbs:
                    continue
                seen_herbs.add(herb)
                evidence = item.get("evidence_sources") or item.get("source") or src_label
                if isinstance(evidence, str):
                    evidence = [evidence]
                modifications.append({
                    "herb_name": herb,
                    "target_disease": primary_disease,
                    "target_symptom": item.get("target_symptom") or item.get("matched_symptom") or "；".join(symptoms[:3]),
                    "western_pathology": item.get("western_pathology") or item.get("western_pathology_target") or "symptom_targeted_support",
                    "evidence_sources": evidence,
                    "matched_reason": item.get("reason") or item.get("matched_reason") or src_label,
                    "must_enter_m3": True,
                    "stage": "M2_3",
                })

        if not modifications:
            return self._build_m2_3_no_candidate(
                primary_disease=primary_disease,
                syndrome_name=syndrome_name,
                symptoms=symptoms,
                input_trace=input_trace,
            )

        result = {
            "stage": "M2_3",
            "status": "PASS",
            "primary_disease": primary_disease,
            "disease_key": resolved_disease,
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": False,
            "modification_candidates": modifications[:6],
            "reverse_audit": {},
            "searched_terms": [primary_disease, syndrome_name] + (symptoms or [])[:3],
            "input_trace": input_trace,
            "formal_prescription_allowed": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def _build_m2_3_no_candidate(self, primary_disease: str, syndrome_name: str,
                                 symptoms: List[str], input_trace: Dict) -> Dict:
        return {
            "primary_disease": primary_disease,
            "status": "NO_CANDIDATE",
            "stage": "M2_3",
            "disease_key": primary_disease,
            "modification_candidates": [],
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": True,
            "formal_prescription_allowed": False,
            "reason": "药物库无可追溯证据",
            "searched_terms": [primary_disease, syndrome_name] + (symptoms or [])[:3],
            "input_trace": input_trace,
            "reverse_audit": {},
            "needs_manual_review": self._is_high_risk_disease(primary_disease),
        }

    def _load_explicit_modifications(self, disease_name: str, syndrome_name: str) -> List[Dict]:
        """从知识库加载显式加减规则（临床加减）"""
        disease_data = self.kb.get(disease_name, {})
        if not disease_data:
            return []
        # 证型级加减
        syndrome_key = syndrome_name
        if syndrome_key not in disease_data.get("syndromes", {}):
            # 尝试按 trigger 匹配
            for key, data in disease_data.get("syndromes", {}).items():
                if syndrome_name and (syndrome_name == key or syndrome_name in str(data.get("trigger", ""))):
                    syndrome_key = key
                    break
        syndrome_data = disease_data.get("syndromes", {}).get(syndrome_key, {})
        modifications = syndrome_data.get("临床加减", [])
        if not modifications:
            # 尝试 full_decoction 提取
            full = syndrome_data.get("full_decoction", "")
            if full:
                modifications = self._parse_modifications_from_decoction(full)
        result = []
        for mod in (modifications or []):
            if isinstance(mod, str):
                result.append({
                    "herb_name": mod,
                    "reason": "知识库临床加减",
                    "source": f"data/m2_formula_knowledge.json/{disease_name}/syndromes/{syndrome_key}/临床加减",
                })
            elif isinstance(mod, dict):
                result.append({
                    "herb_name": mod.get("herb", mod.get("herb_name", "")),
                    "reason": mod.get("reason", "知识库临床加减"),
                    "target_symptom": mod.get("target_symptom", ""),
                    "western_pathology": mod.get("western_pathology", ""),
                    "source": f"data/m2_formula_knowledge.json/{disease_name}/syndromes/{syndrome_key}/临床加减",
                })
        return result

    def _parse_modifications_from_decoction(self, full_decoction: str) -> List[Dict]:
        """从 full_decoction 的加减注释解析加减药物"""
        import re
        modifications = []
        # 匹配 "甚加XX"、"加XX"
        pattern = re.compile(r"(甚加|加)([^\d，。、；,\s]{2,4})")
        for m in pattern.finditer(full_decoction):
            herb = m.group(2).strip()
            if herb:
                modifications.append({
                    "herb": herb,
                    "reason": "知识库 full_decoction 加减提示",
                    "source": "data/m2_formula_knowledge.json/full_decoction",
                })
        return modifications

    def _load_case_modifications(self, disease_name: str, syndrome_name: str) -> List[Dict]:
        """从病案缓存加载加减数据"""
        modifications = []
        # 先查 m2_case_cache.json
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "m2_case_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                exact_key = f"{disease_name}|{syndrome_name}"
                entry = cache_data.get(exact_key)
                if not entry:
                    for ck, cv in cache_data.items():
                        ck_disease = ck.split("|")[0].strip().lower()
                        if ck_disease == disease_name.strip().lower():
                            entry = cv
                            break
                if entry:
                    for cm in (entry.get("modifications", []) or []):
                        ch = cm.get("herb", "").strip()
                        if ch:
                            modifications.append({
                                "herb_name": ch,
                                "reason": cm.get("reason", "名医病案加减")[:30],
                                "evidence_sources": [entry.get("source", "m2_case_cache.json")],
                                "target_symptom": cm.get("reason", "")[:20],
                            })
            except Exception:
                pass
        if modifications:
            return modifications
        # 再查 m2_case_reference.json
        ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "m2_case_reference.json")
        if os.path.exists(ref_path):
            try:
                with open(ref_path, "r", encoding="utf-8") as f:
                    ref_data = json.load(f)
                for key, entry in ref_data.items():
                    if disease_name.lower() in key.lower():
                        for cm in (entry.get("modifications", []) or []):
                            ch = cm.get("herb", "").strip()
                            if ch:
                                modifications.append({
                                    "herb_name": ch,
                                    "reason": cm.get("reason", "知识库内置病案加减")[:30],
                                    "evidence_sources": [entry.get("source", "m2_case_reference.json")],
                                    "target_symptom": cm.get("reason", "")[:20],
                                })
            except Exception:
                pass
        return modifications[:6]

    def _load_herb_kb_modifications(self, disease_name: str, symptoms: List[str]) -> List[Dict]:
        """从 herb_kb 加载症状-药物映射加减候选"""
        if not self.herb_kb or not isinstance(self.herb_kb, dict):
            return []
        modifications = []
        for symptom in symptoms:
            if not symptom or not isinstance(symptom, str):
                continue
            for hname, hinfo in self.herb_kb.items():
                if not isinstance(hinfo, dict):
                    continue
                indications = hinfo.get("indications") or hinfo.get("主治") or ""
                if isinstance(indications, str) and any(symptom in indications for symptom in [symptom]):
                    modifications.append({
                        "herb_name": hname,
                        "reason": f"herb_kb 主治包含'{symptom}'",
                        "evidence_sources": [f"data/m3_herb_knowledge.json/{hname}"],
                        "target_symptom": symptom,
                        "western_pathology": hinfo.get("现代药理", "symptom_targeted_support"),
                    })
                    break
        return modifications

    def _build_no_candidate(self, primary_disease: str, reason: str, missing_key: str,
                            searched_terms: List[str], input_trace: Optional[Dict] = None) -> Dict:
        return {
            "primary_disease": primary_disease,
            "status": "NO_CANDIDATE",
            "disease_key": missing_key or primary_disease,
            "formula_candidates": [],
            "modification_candidates": [],
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": True,
            "reason": reason,
            "reverse_audit": {},
            "prescription_draft": False,
            "need_human_review": True,
            "searched_terms": searched_terms,
            "missing_key": missing_key,
            "syndrome_trace": {
                "syndrome_name": "",
                "evidence": [],
                "reasoning_summary": reason,
                "confidence": 0.0,
            },
            "evidence_trace": [],
            "input_trace": input_trace or {},
            "needs_manual_review": True,
        }

    def _normalize_modification_candidates(self, modifications: List[Dict],
                                           disease_name: str,
                                           symptoms: List[str]) -> List[Dict]:
        normalized = []
        symptom_text = "；".join(symptoms or [])
        for item in modifications or []:
            if not isinstance(item, dict):
                continue
            herb = item.get("herb", "")
            if not herb:
                continue
            evidence = item.get("source") or item.get("evidence_sources") or "case_reference_or_herb_kb"
            if isinstance(evidence, str):
                evidence = [evidence]
            normalized.append({
                "herb": herb,
                "action": item.get("action", "candidate_add"),
                "reason": item.get("reason", ""),
                "target_disease": item.get("target_disease", disease_name),
                "target_symptom": item.get("target_symptom", item.get("matched_symptom", symptom_text)),
                "western_pathology": item.get("western_pathology", item.get("western_pathology_target", "symptom_targeted_support")),
                "evidence_sources": evidence,
            })
        return normalized

    # ══════════════════════════════════════════════════════
    #  双轨辨证 — 病名病理辨证轨 (Spec Section 5)
    # ══════════════════════════════════════════════════════

    _ACUTE_INFECTION_KEYWORDS = [
        "肺炎", "感染", "扁桃", "上呼吸道", "支气管", "发热", "鼻窦",
        "咽炎", "喉炎", "气管炎", "会厌炎", "中耳炎", "腮腺炎",
        "脑膜炎", "阑尾炎", "胆囊炎", "尿路感染", "肠炎",
    ]
    _CHRONIC_KEYWORDS = [
        "慢性", "虚劳", "肿瘤", "癌", "术后", "综合征", "功能",
        "退行", "增生", "肥大", "硬化", "纤维化",
    ]

    def _classify_disease_type(self, primary_disease: str,
                                patient_info: Dict) -> tuple:
        """Spec 5.1: 先判疾病性质和辨证框架

        Returns:
            (disease_type, framework):
                disease_type: "外感/急性感染" | "内伤/慢病" | "性质不清"
                framework: "卫气营血辨证" | "脏腑辨证" | "混合辨证"
        """
        disease_lower = primary_disease.lower()

        # 急性/感染性
        if any(kw in disease_lower for kw in self._ACUTE_INFECTION_KEYWORDS):
            disease_type = "外感/急性感染"
            framework = "卫气营血辨证"
        # 慢性/内伤
        elif any(kw in disease_lower for kw in self._CHRONIC_KEYWORDS):
            disease_type = "内伤/慢病"
            framework = "脏腑辨证"
        else:
            # 通过症状和病程辅助判断
            symptoms = patient_info.get("symptoms", []) or []
            combined = " ".join(s.lower() for s in symptoms if isinstance(s, str))
            acute_hints = ["发热", "恶寒", "恶风", "起病急", "突发", "2天", "3天",
                           "1周", "近日", "新发"]
            chronic_hints = ["反复", "多年", "长期", "日久", "间断", "时发",
                             "遇劳", "遇寒", "晨起", "每年"]
            acute_score = sum(1 for h in acute_hints if h in combined)
            chronic_score = sum(1 for h in chronic_hints if h in combined)
            labs = patient_info.get("labs", []) or []
            if any("白细" in str(l) or "CRP" in str(l) or "中性" in str(l) for l in labs):
                acute_score += 2
            if acute_score > chronic_score:
                disease_type = "外感/急性感染"
                framework = "卫气营血辨证"
            elif chronic_score > acute_score:
                disease_type = "内伤/慢病"
                framework = "脏腑辨证"
            else:
                disease_type = "性质不清"
                framework = "卫气营血辨证与脏腑辨证兼看"

        return disease_type, framework

    def _run_pathology_track(self, disease_key: str, syndromes: Dict,
                              patient_info: Dict,
                              disease_type: str, framework: str) -> Optional[Dict]:
        """Spec 5.2: 病名病理辨证轨

        根据西医病名、病程、病理阶段、检查证据、主症和舌脉，
        判断当前病理状态，并映射为中医病机特征。

        Returns:
            dict with pathology-based track result, or None if insufficient data.
        """
        symptoms = patient_info.get("symptoms", []) or []
        tongue = patient_info.get("tongue", "") or ""
        pulse = patient_info.get("pulse", "") or ""
        labs = patient_info.get("labs", []) or []
        imaging = patient_info.get("imaging", []) or []
        combined = " ".join(s.lower() for s in symptoms if isinstance(s, str))

        # 从实验室/检查证据提取病理阶段线索
        pathology_hints = []
        if any("白细" in str(l) or "中性" in str(l) or "CRP" in str(l) for l in labs):
            pathology_hints.append("急性炎症期")
        if any("淋" in str(l) for l in labs):
            pathology_hints.append("免疫反应期")
        if any("影像" in str(x) or "影" in str(x) or "浸润" in str(x) for x in imaging):
            pathology_hints.append("器质性改变期")
        if any("肿" in str(x) or "占位" in str(x) or "结节" in str(x) for x in imaging):
            pathology_hints.append("占位性病变期")

        # 从症状提取病理倾向关键词
        # NOTE: 咳嗽、苔腻、苔黄这些非特异性症状不作为核心病理匹配指标，
        # 避免 风→咳嗽 导致所有咳嗽病例都获得"风"打分
        _pathology_to_disease_kw = {
            "风": ["恶风", "鼻塞", "流涕", "咽痒", "突发", "起病急", "瘙痒"],
            "寒": ["恶寒", "怕冷", "清稀", "不渴", "苔白", "痰白"],
            "热": ["发热", "黄痰", "口干", "咽痛", "舌红", "苔黄", "烦躁"],
            "湿": ["苔腻", "困重", "纳呆", "便溏", "水肿", "头重"],
            "痰": ["咳嗽", "痰多", "苔腻", "脉滑", "喉中痰鸣", "咳痰"],
            "瘀": ["刺痛", "固定痛", "舌暗", "瘀斑", "舌下", "青紫"],
            "虚": ["乏力", "气短", "自汗", "盗汗", "畏寒", "五心烦热"],
            "气滞": ["胀痛", "走窜", "叹气", "抑郁", "脉弦", "胸闷"],
        }

        inferred_pathology_stage = ""
        if pathology_hints:
            inferred_pathology_stage = "；".join(pathology_hints)
        elif disease_type == "外感/急性感染":
            if "发热" in combined and "恶寒" in combined:
                inferred_pathology_stage = "表证期"
            elif "发热" in combined and ("黄痰" in combined or "黄涕" in combined):
                inferred_pathology_stage = "里热期"
            else:
                inferred_pathology_stage = "表证期"
        elif disease_type == "内伤/慢病":
            if "乏力" in combined or "气短" in combined:
                inferred_pathology_stage = "正气不足期"
            elif "刺痛" in combined or "固定" in combined:
                inferred_pathology_stage = "瘀血内阻期"
            else:
                inferred_pathology_stage = "功能失调期"

        # 匹配病理关键词到证型
        matched_pathology_keywords = []
        for patho_type, kws in _pathology_to_disease_kw.items():
            if any(kw in combined for kw in kws):
                matched_pathology_keywords.append(patho_type)

        # 从证型池中筛选与当前病理倾向匹配的证型
        evidence_for = []
        evidence_against = []
        candidate_syndrome = ""
        best_match_score = -1

        for name, data in syndromes.items():
            trigger = str(data.get("trigger", "")).lower()
            syndrome_pathology = str(data.get("pathology", "")).lower()
            syndrome_text = trigger + " " + syndrome_pathology

            score = 0
            matched_items = []
            for pk in matched_pathology_keywords:
                if pk in syndrome_text:
                    score += 2
                    matched_items.append(pk)
            # 框架匹配加分
            if framework == "卫气营血辨证" and any(kw in syndrome_text for kw in ["卫", "气分", "营", "血分", "热"]):
                score += 1
            if framework == "脏腑辨证" and any(kw in syndrome_text for kw in ["脾", "肺", "肾", "肝", "心", "胃", "肠"]):
                score += 1

            # 病理阶段匹配加分/扣分
            if inferred_pathology_stage == "里热期":
                # 里热期 → 表证证型（风热/风寒）降分，里热证（痰热壅肺等）加分
                if "邪犯肺卫" in trigger or "表" in trigger:
                    score -= 2
                elif "痰热" in trigger or "热壅" in trigger or "气分" in trigger or "营" in trigger:
                    score += 1
            elif inferred_pathology_stage == "表证期":
                # 表证期 → 表证证型加分
                if "邪犯肺卫" in trigger:
                    score += 1

            if score > 0:
                evidence_for.append({
                    "syndrome": name,
                    "matched_pathology_factors": matched_items,
                    "score": score,
                })
                if score > best_match_score:
                    best_match_score = score
                    candidate_syndrome = name

        # evidence_against: 证型中的症状与患者否定症状冲突
        negatives = patient_info.get("negative_findings", []) or []
        neg_combined = " ".join(n.lower() for n in negatives if isinstance(n, str))
        if neg_combined:
            for name, data in syndromes.items():
                trigger = str(data.get("trigger", "")).lower()
                for neg in ["不热", "不渴", "不红", "不痛", "无汗", "无"]:
                    if neg in trigger and neg not in neg_combined:
                        continue
                    if neg in trigger and neg in neg_combined:
                        evidence_against.append({
                            "syndrome": name,
                            "reason": f"患者{neg}与证型{neg}不一致",
                        })

        if not candidate_syndrome and not evidence_for:
            # 无病理匹配, 尝试最顶层证型
            syndrome_list = list(syndromes.keys())
            if syndrome_list:
                candidate_syndrome = syndrome_list[0]
                evidence_for.append({
                    "syndrome": candidate_syndrome,
                    "matched_pathology_factors": ["无明确病理匹配，取首候选"],
                    "score": 0,
                })

        # ── 痰热证据后处理：黄痰/黄绿痰 + 苔腻/苔黄腻 + 口苦/口干 三项齐备 → 强制推荐痰热壅肺 ──
        _has_yellow_sputum = any(kw in combined for kw in ["黄痰", "黄绿痰", "黄稠痰", "黄黏痰", "铁锈色", "咳黄"])
        _has_heat_tongue = any(kw in combined for kw in ["苔黄", "黄苔", "黄腻", "舌红"])
        _has_bitter_or_dry = any(kw in combined for kw in ["口苦", "口干", "口燥", "渴"])
        _has_greasy_tongue = any(kw in combined for kw in ["苔腻", "黄腻", "厚腻"])
        if _has_yellow_sputum and _has_heat_tongue and (_has_bitter_or_dry or _has_greasy_tongue):
            for ev in evidence_for:
                if "痰热" in ev["syndrome"] or "热壅" in ev["syndrome"] or "痰" in ev["syndrome"] and "热" in ev["syndrome"]:
                    ev["score"] += 3
                    if ev["score"] > best_match_score:
                        best_match_score = ev["score"]
                        candidate_syndrome = ev["syndrome"]

        confidence = min(0.9, 0.3 + best_match_score * 0.15) if best_match_score > 0 else 0.2

        return {
            "track": "pathology_based",
            "disease_type": disease_type,
            "framework": framework,
            "inferred_pathology_stage": inferred_pathology_stage,
            "tcm_pathogenesis": "、".join(matched_pathology_keywords),
            "candidate_syndrome": candidate_syndrome,
            "evidence_for": evidence_for[:5],
            "evidence_against": evidence_against[:3],
            "confidence": round(confidence, 2),
        }

    def _clean_track_result(self, track_result: Optional[Dict]) -> Optional[Dict]:
        """清理轨结果：只保留外部可见字段，移除 scorer 的内部字段"""
        if not track_result:
            return None
        if track_result.get("track") == "pathology_based":
            return {
                "track": track_result["track"],
                "disease_type": track_result["disease_type"],
                "framework": track_result["framework"],
                "inferred_pathology_stage": track_result["inferred_pathology_stage"],
                "tcm_pathogenesis": track_result["tcm_pathogenesis"],
                "candidate_syndrome": track_result["candidate_syndrome"],
                "evidence_for": track_result["evidence_for"],
                "evidence_against": track_result["evidence_against"],
                "confidence": track_result["confidence"],
            }
        # traditional_tcm track (scorer output)
        return {
            "track": "traditional_tcm",
            "bagang": {},
            "tcm_factors": track_result.get("matched_pathology", "").split("、") if track_result.get("matched_pathology") else [],
            "candidate_syndrome": track_result.get("selected_syndrome", {}).get("name", ""),
            "evidence_for": track_result.get("matched_symptoms", [])[:5],
            "evidence_against": [],
            "confidence": track_result.get("confidence", 0.0),
        }

    def _merge_dual_tracks(self, primary_disease: str, syndromes: Dict,
                            patient_info: Dict,
                            pathology_track: Optional[Dict],
                            tcm_track: Optional[Dict]) -> Dict:
        """Spec 6: 双轨校对与节点选择

        比较病理轨与症状证素轨结果：

        - 两轨一致: 直接选共同证型
        - 两轨相近: 选更能解释病理阶段者
        - 两轨冲突: 标记 need_human_review

        Returns:
            dict with merge results
        """
        if not pathology_track or not tcm_track:
            return {"status": "SINGLE_TRACK"}

        patho_syndrome = pathology_track.get("candidate_syndrome", "")
        tcm_syndrome = tcm_track.get("selected_syndrome", {}).get("name", "")

        if not patho_syndrome and not tcm_syndrome:
            return {"status": "SINGLE_TRACK"}

        # 归一化证型名
        def normalize_syndrome_name(name, syndromes_dict):
            for key in syndromes_dict:
                trigger = str(syndromes_dict[key].get("trigger", ""))
                if name == key or name in trigger or key in name:
                    return key
            return name

        patho_key = normalize_syndrome_name(patho_syndrome, syndromes)
        tcm_key = normalize_syndrome_name(tcm_syndrome, syndromes)

        if patho_key == tcm_key or (patho_key and tcm_key and patho_key in tcm_key):
            # 一致：取共同证型
            syndrome_data = syndromes.get(patho_key, {})
            nm = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
            display = nm.group(1) if nm else patho_key

            matched_pathology = pathology_track.get("inferred_pathology_stage", "")
            conf = max(
                pathology_track.get("confidence", 0),
                tcm_track.get("confidence", 0),
            )

            return {
                "status": "CONSISTENT",
                "selected_syndrome_key": patho_key,
                "syndrome_name": display,
                "confidence": conf,
                "matched_pathology": matched_pathology,
                "matched_symptoms": tcm_track.get("matched_symptoms", []) or [],
                "matched_tongue_pulse": tcm_track.get("matched_tongue_pulse", "") or "",
                "missing_info": tcm_track.get("missing_info", "") or "",
                "reasoning_summary": f"双轨一致：病理轨→{patho_syndrome}，症状轨→{tcm_syndrome}",
            }

        # 相近：计算证型文本相似度
        patho_data = syndromes.get(patho_key, {})
        tcm_data = syndromes.get(tcm_key, {})
        patho_text = (str(patho_data.get("trigger", "")) + " " +
                      str(patho_data.get("pathology", ""))).lower()
        tcm_text = (str(tcm_data.get("trigger", "")) + " " +
                    str(tcm_data.get("pathology", ""))).lower()

        # 简单文本重叠作为相近判断
        overlap = set(patho_text) & set(tcm_text)
        patho_chars = set(patho_text)
        tcm_chars = set(tcm_text)
        jaccard = len(overlap) / max(len(patho_chars | tcm_chars), 1)

        if jaccard > 0.3:
            # 相近：选置信度更高的
            patho_conf = pathology_track.get("confidence", 0)
            tcm_conf = tcm_track.get("confidence", 0)

            if patho_conf >= tcm_conf:
                winner_key = patho_key
                winner_syndrome = patho_syndrome
                loser_syndrome = tcm_syndrome
                reason = f"双轨相近，病理轨置信度更高({patho_conf}≥{tcm_conf})"
                matched_pathology = pathology_track.get("inferred_pathology_stage", "")
            else:
                winner_key = tcm_key
                winner_syndrome = tcm_syndrome
                loser_syndrome = patho_syndrome
                reason = f"双轨相近，症状轨置信度更高({tcm_conf}>{patho_conf})"
                matched_pathology = pathology_track.get("inferred_pathology_stage", "")

            syndrome_data = syndromes.get(winner_key, {})
            nm = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
            display = nm.group(1) if nm else winner_key

            return {
                "status": "NEAR",
                "selected_syndrome_key": winner_key,
                "syndrome_name": display,
                "confidence": max(patho_conf, tcm_conf),
                "matched_pathology": matched_pathology,
                "matched_symptoms": tcm_track.get("matched_symptoms", []) or [],
                "matched_tongue_pulse": tcm_track.get("matched_tongue_pulse", "") or "",
                "missing_info": tcm_track.get("missing_info", "") or "",
                "reasoning_summary": reason,
                "differential_syndrome": loser_syndrome,
            }

        # 冲突
        return {
            "status": "CONFLICT",
            "selected_syndrome_key": tcm_key or patho_key,
            "syndrome_name": tcm_syndrome or patho_syndrome,
            "confidence": max(
                pathology_track.get("confidence", 0),
                tcm_track.get("confidence", 0),
            ),
            "matched_pathology": pathology_track.get("inferred_pathology_stage", ""),
            "matched_symptoms": [],
            "matched_tongue_pulse": "",
            "missing_info": "双轨冲突，需人工复核",
            "reasoning_summary": f"双轨冲突：病理轨→{patho_syndrome}，症状轨→{tcm_syndrome}",
            "conflict_reason": f"两轨选择证型不一致且文本相似度低(jaccard={jaccard:.2f})",
            "differential_syndrome": patho_syndrome if patho_syndrome != tcm_syndrome else "",
        }

    @staticmethod
    def _has_inflammation_evidence(symptoms: List[str], tongue: str, pulse: str,
                                    labs: List[str], imaging: List[str]) -> dict:
        """评估炎症/热证证据的强度和类型

        Returns:
            {
                "has_inflammation": bool,
                "severity": "none" | "mild" | "moderate" | "severe",
                "evidence": [str],
                "evidence_type": ["fever"|"sputum"|"tongue"|"lab"|"imaging"|"throat"]
            }
        """
        combined = " ".join(s.lower() for s in symptoms if isinstance(s, str))
        evidence = []
        evidence_type = []

        # 发热
        if any(kw in combined for kw in ["发热", "高热", "低热", "身热", "发烧"]):
            evidence.append("发热")
            evidence_type.append("fever")

        # 黄痰/脓痰
        if any(kw in combined for kw in ["黄痰", "黄绿痰", "黄稠痰", "黄黏痰", "脓痰", "铁锈色"]):
            evidence.append("黄痰/脓痰")
            evidence_type.append("sputum")

        # 咽痛
        if any(kw in combined for kw in ["咽痛", "喉痛", "吞咽痛"]):
            evidence.append("咽痛")
            evidence_type.append("throat")

        # 舌象热证
        tongue_lower = tongue.lower() if tongue else ""
        if any(kw in tongue_lower for kw in ["苔黄", "黄苔", "黄腻", "舌红", "绛"]):
            evidence.append(tongue)
            evidence_type.append("tongue")

        # 脉象热证
        pulse_lower = pulse.lower() if pulse else ""
        if any(kw in pulse_lower for kw in ["数", "滑数", "洪", "实"]):
            evidence.append(pulse)
            evidence_type.append("pulse")

        # 实验室
        lab_text = " ".join(l.lower() for l in labs if isinstance(l, str))
        if any(kw in lab_text for kw in ["白细", "中性", "crp", "pct", "降钙"]):
            for l in labs:
                if any(kw in str(l).lower() for kw in ["白细", "中性", "crp", "pct", "降钙"]):
                    evidence.append(str(l))
            evidence_type.append("lab")

        # 影像
        imaging_text = " ".join(x.lower() for x in imaging if isinstance(x, str))
        if any(kw in imaging_text for kw in ["浸润", "渗出", "炎症", "感染", "实变"]):
            for x in imaging:
                if any(kw in str(x).lower() for kw in ["浸润", "渗出", "炎症", "感染", "实变"]):
                    evidence.append(str(x))
            evidence_type.append("imaging")

        severity = "none"
        if evidence:
            # Count distinct evidence types
            type_count = len(set(evidence_type))
            if type_count >= 3 or len(evidence) >= 4:
                severity = "severe"
            elif type_count >= 2 or len(evidence) >= 2:
                severity = "moderate"
            else:
                severity = "mild"

        return {
            "has_inflammation": len(evidence) > 0,
            "severity": severity,
            "evidence": evidence,
            "evidence_type": list(set(evidence_type)),
        }

    @staticmethod
    def _is_cold_heat_conflict(factor_contradiction_result: Optional[Dict]) -> bool:
        """判断 factor_contradiction_result 是否包含寒热冲突"""
        if not factor_contradiction_result:
            return False
        strong_against = factor_contradiction_result.get("strong_against", [])
        for item in strong_against:
            if isinstance(item, dict):
                fa = item.get("factor_a", "")
                fb = item.get("factor_b", "")
                if ("寒" in {fa, fb} and "热" in {fa, fb}) or \
                   ("寒" in {fa, fb} and "温" in {fa, fb}):
                    return True
        return False

    def resolve_cold_heat_conflict_by_pathology(
        self,
        disease_key: str,
        symptoms: List[str],
        negative_findings: Optional[List[str]],
        tongue: str,
        pulse: str,
        labs: Optional[List[str]],
        imaging: Optional[List[str]],
        pathology: Optional[Dict],
        factor_contradiction_result: Optional[Dict],
    ) -> Dict:
        """寒热冲突时，由病理阶段和客观炎症证据裁决主证方向

        规则:
        1. 初期/表证期，无明显炎症 → 风寒/寒证方向
        2. 炎症反应明显 → 热证/痰热/湿热方向
        3. 恢复期/久病/术后/肿瘤 → 按病理阶段判断，虚可为主
        4. 证据不足 → 返回 LOW_CONFIDENCE

        Returns:
            {
                "conflict_type": "cold_heat",
                "resolved_by": "pathology_stage",
                "main_direction": "cold" | "heat" | "mixed" | "unknown",
                "reasoning": str,
                "evidence_for_main": [str],
                "evidence_against": [str],
                "need_human_review": False
            }
        """
        symptoms = symptoms or []
        negative_findings = negative_findings or []
        labs = labs or []
        imaging = imaging or []
        combined = " ".join(s.lower() for s in symptoms if isinstance(s, str))

        # ── 炎症证据评估 ──
        inflame = self._has_inflammation_evidence(symptoms, tongue, pulse, labs, imaging)

        # ── 病理阶段线索 ──
        inferred_stage = ""
        if pathology:
            inferred_stage = pathology.get("inferred_pathology_stage", "")

        # 从症状推断病程
        is_early_stage = any(kw in combined for kw in ["起病", "初起", "新发", "1天", "2天", "3天", "突发"])
        is_chronic = any(kw in combined for kw in ["反复", "多年", "日久", "反复发作", "长期", "术后", "久病"])
        is_recovery = any(kw in combined for kw in ["恢复期", "后期", "好转", "减轻", "余邪未清"])
        is_consumptive = any(kw in combined for kw in ["乏力", "消瘦", "纳差", "术后", "肿瘤", "癌", "放化疗"])

        # 寒象证据
        has_cold_signs = any(kw in combined for kw in ["怕冷", "恶寒", "畏寒", "寒战", "喜温", "喜暖"])
        has_cold_sputum = any(kw in combined for kw in ["痰白清稀", "白痰", "清稀", "泡沫痰"])
        has_cold_tongue = "舌淡" in (tongue.lower() if tongue else "") or "苔白" in (tongue.lower() if tongue else "")

        # 热象证据（排除否定症状）
        _neg_prefixes = ["无", "未", "不伴", "否认", "没有"]
        def _pos_match(kw: str, text: str) -> bool:
            if kw not in text:
                return False
            for np_ in sorted(_neg_prefixes, key=len, reverse=True):
                if np_ + kw in text:
                    return False
            return True

        has_heat_signs = any(_pos_match(kw, combined) for kw in ["发热", "口苦", "口干", "口渴", "烦躁"])
        has_heat_sputum = any(kw in combined for kw in ["黄痰", "黄绿痰", "黄稠痰", "脓痰", "黄黏痰"])
        has_heat_tongue = any(kw in (tongue.lower() if tongue else "") for kw in ["苔黄", "黄苔", "黄腻", "舌红", "绛"])
        has_heat_pulse = any(kw in (pulse.lower() if pulse else "") for kw in ["数", "滑数", "洪"])

        # ── 规则 1: 初期/表证期 + 无明显炎症 → 风寒/寒证方向 ──
        if (is_early_stage or "表证期" in inferred_stage) and not inflame["has_inflammation"]:
            if has_cold_signs or has_cold_sputum or has_cold_tongue:
                return {
                    "conflict_type": "cold_heat",
                    "resolved_by": "pathology_stage",
                    "main_direction": "cold",
                    "severity": inflame["severity"],
                    "reasoning": f"初起/表证期({inferred_stage})，无炎症证据，寒象为主",
                    "evidence_for_main": (["怕冷/恶寒"] if has_cold_signs else []) + (["痰白清稀"] if has_cold_sputum else []) + (["舌淡苔白"] if has_cold_tongue else []),
                    "evidence_against": inflame["evidence"],
                    "need_human_review": False,
                }

        # ── 规则 2: 炎症反应明显 → 热证/痰热/湿热方向 ──
        if inflame["has_inflammation"] and inflame["severity"] in ("moderate", "severe"):
            evidence_for = list(inflame["evidence"])
            evidence_against = []
            if has_cold_signs:
                evidence_against.append("寒象（兼夹）")
            return {
                "conflict_type": "cold_heat",
                "resolved_by": "pathology_stage",
                "main_direction": "heat",
                "severity": inflame["severity"],
                "reasoning": f"炎症证据明确(severity={inflame['severity']})，主证取热，寒象为兼夹",
                "evidence_for_main": evidence_for,
                "evidence_against": evidence_against,
                "need_human_review": False,
            }

        # ── 规则 3: 恢复期/久病/术后/肿瘤/消耗状态 ──
        if is_recovery or is_consumptive or (inferred_stage and "正气不足" in inferred_stage):
            # 恢复期不得单纯寒热互斥
            has_deficiency = any(kw in combined for kw in ["乏力", "气短", "自汗", "盗汗", "纳差"])
            main_direction = "mixed"
            if has_deficiency and not inflame["has_inflammation"]:
                main_direction = "cold"  # 虚多倾向虚寒/气虚
            elif inflame["has_inflammation"]:
                main_direction = "heat"  # 有炎症仍以热为主
            evidence_against_list = inflame["evidence"] if inflame["has_inflammation"] else ["无明显炎症反应"]
            return {
                "conflict_type": "cold_heat",
                "resolved_by": "pathology_stage",
                "main_direction": main_direction,
                "severity": inflame["severity"],
                "reasoning": f"恢复期/消耗状态，按病理阶段判断，不得简单寒热互斥",
                "evidence_for_main": ["乏力/虚象"] if has_deficiency else [],
                "evidence_against": evidence_against_list,
                "need_human_review": False,
            }

        # ── 规则 4: 轻度炎症但证据不足 ──
        if inflame["severity"] == "mild":
            if has_cold_signs and not has_heat_signs:
                # 轻度炎症但寒象为主
                return {
                    "conflict_type": "cold_heat",
                    "resolved_by": "low_confidence",
                    "main_direction": "cold",
                    "severity": "mild",
                    "reasoning": "轻度炎症，寒象为主，倾向寒证方向（低置信度）",
                    "evidence_for_main": ["怕冷/恶寒"] if has_cold_signs else [],
                    "evidence_against": inflame["evidence"],
                    "need_human_review": False,
                }
            # 既有寒象又有热象但炎症轻 → 混合
            return {
                "conflict_type": "cold_heat",
                "resolved_by": "low_confidence",
                "main_direction": "mixed",
                "severity": "mild",
                "reasoning": "寒热夹杂，炎症证据不足，需人工复核确认主证方向",
                "evidence_for_main": (["怕冷/恶寒"] if has_cold_signs else []) +
                                     (["黄痰"] if has_heat_sputum else []),
                "evidence_against": [],
                "need_human_review": True,
            }

        # ── 完全无炎症证据 ──
        if not inflame["has_inflammation"] and not has_cold_signs and not has_heat_signs:
            return {
                "conflict_type": "cold_heat",
                "resolved_by": "insufficient_evidence",
                "main_direction": "unknown",
                "severity": "none",
                "reasoning": "寒热证据均不清晰，无法裁决",
                "evidence_for_main": [],
                "evidence_against": [],
                "need_human_review": False,
            }

        # 默认兜底：混合
        return {
            "conflict_type": "cold_heat",
            "resolved_by": "default",
            "main_direction": "mixed",
            "severity": inflame["severity"],
            "reasoning": "寒热证据并存，无明确病理阶段倾向",
            "evidence_for_main": [],
            "evidence_against": [],
            "need_human_review": True,
        }

    def _build_prompt(self, primary_disease, syndromes, patient_info,
                       disease_type: str = "", framework: str = "") -> str:
        """按 M2 提示词 8.1 节构建 prompt"""
        syndrome_candidates = ""
        for name, data in syndromes.items():
            trigger = data.get("trigger", "无描述")
            syndrome_candidates += f"\n### {name}\n{trigger}\n"

        herb_ref = self._build_herb_reference()

        s = patient_info.get("symptoms", []) or []

        # 辨证框架上下文
        framework_context = ""
        if disease_type:
            framework_context = f"""
## 疾病性质与辨证框架（由病理轨判定）
疾病性质：{disease_type}
推荐辨证框架：{framework}
"""
        return f"""你是一位中医辨证专家。请根据以下患者信息和候选证型，完成辨证。

## 患者信息
西医主病名：{primary_disease}
症状：{'；'.join(s)}
舌象：{patient_info.get('tongue', '')}
脉象：{patient_info.get('pulse', '')}
寒热：{'；'.join(patient_info.get('cold_heat', []) or [])}
二便：{'；'.join(patient_info.get('stool_urine', []) or [])}
年龄：{patient_info.get('age', '')}
{framework_context}
## 辨证框架规则
- 急性起病、发热恶寒、感染性表现 -> 卫气营血辨证
- 慢性反复、功能失调、体质调理 -> 脏腑辨证
- 两者兼有 -> 先判外感阶段，再用脏腑辨证

## 候选证型（均来自知识库）
{syndrome_candidates}

## 常用药物性味归经参考（供加减时参考）
{herb_ref}

请完成以下任务：
1. 判断应使用哪种辨证框架
2. 从候选证型中选出最匹配的 1 个证型，简述理由

只输出 JSON，不要输出其他内容：
{{
  "differentiation_framework": "卫气营血/脏腑/混合",
  "selected_syndrome": {{
    "name": "证型名称",
    "reason": "匹配理由简述"
  }},
  "missing_info": ["缺失的关键信息列表"]
}}"""

    def _build_herb_reference(self) -> str:
        """构建常用药物性味归经参考"""
        if not self.herb_kb:
            return "（无药物参考数据）"
        lines = []
        common_herbs = [
            "麻黄", "桂枝", "柴胡", "黄芩", "黄连", "金银花", "连翘",
            "蒲公英", "板蓝根", "大青叶", "石膏", "知母", "栀子",
            "龙胆草", "生地黄", "玄参", "牡丹皮", "赤芍", "白芍",
            "当归", "川芎", "丹参", "桃仁", "红花", "牛膝",
            "半夏", "陈皮", "枳壳", "厚朴", "苍术", "白术",
            "茯苓", "泽泻", "车前子", "茵陈", "金钱草",
            "附子", "肉桂", "干姜", "吴茱萸", "细辛",
            "人参", "黄芪", "党参", "山药", "甘草",
            "麦冬", "五味子", "枸杞子", "菊花", "薄荷",
            "防风", "荆芥", "白芷", "葛根",
        ]
        for h in common_herbs:
            info = self.herb_kb.get(h)
            if info:
                props = "、".join(info.get("properties", []))
                meridians = "、".join(info.get("meridians", []))
                cls = info.get("class", "")
                lines.append(f"  {h}：性味【{props}】归经【{meridians}】功效分类【{cls}】")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════
    #  病案检索（新增）
    # ══════════════════════════════════════════════════════

    def _search_cases(self, disease_name: str, syndrome_name: str = "") -> list:
        """根据病名和证型检索病案参考库

        规则：
        - 优先精确匹配（病名+证型）
        - 再模糊匹配（病名部分匹配）
        - 最多返回 3 条
        """
        matched = []

        # 1. 精确匹配：病名|证型
        for key, case in self.case_reference.items():
            if len(matched) >= 3:
                break
            parts = key.split("|")
            if len(parts) == 2:
                cd, cs = parts
                if cd == disease_name and (not syndrome_name or cs == syndrome_name):
                    matched.append(case)

        # 2. 按病名匹配
        if len(matched) < 3:
            for key, case in self.case_reference.items():
                if len(matched) >= 3:
                    break
                parts = key.split("|")
                if len(parts) == 2 and parts[0] == disease_name and case not in matched:
                    matched.append(case)

        # 3. 按部分病名匹配
        if len(matched) < 3:
            dl = disease_name.lower()
            for key, case in self.case_reference.items():
                if len(matched) >= 3:
                    break
                parts = key.split("|")
                if len(parts) == 2:
                    cd = parts[0]
                    if dl in cd.lower() or cd.lower() in dl:
                        if case not in matched:
                            matched.append(case)

        return matched[:3]

    def _generate_modifications(self, disease_name: str, syndrome_name: str,
                                 base_herbs: list, cases: list,
                                 patient_info: dict) -> list:
        """基于病案参考和患者情况生成加减药物建议

        规则：
        - 加减药物只能在参考病案的 modifications 中选取
        - 总加减药物数 <= 3 味
        - 不能与 base_herbs 重复
        - 根据患者症状匹配加减理由
        """
        if not cases or not self._llm_available():
            return []

        # 收集所有可用的加减候选
        candidates = []
        for case in cases:
            mods = case.get("modifications", []) or []
            for m in mods:
                if isinstance(m, dict):
                    herb = m.get("herb", "")
                    reason = m.get("reason", "")
                    if herb and herb not in base_herbs:
                        candidates.append({"herb": herb, "reason": reason, "source": case.get("source", "")})

        # 去重
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["herb"] not in seen:
                seen.add(c["herb"])
                unique_candidates.append(c)

        # 最多取 3 味
        return unique_candidates[:3]

    # ══════════════════════════════════════════════════════
    #  解析与兜底
    # ══════════════════════════════════════════════════════

    def _parse_llm_result(self, text: str, syndromes: Dict) -> Optional[Dict]:
        """从 LLM 返回文本中解析 JSON"""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return None
        try:
            parsed = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        name = parsed.get("selected_syndrome", {}).get("name", "")
        if name not in syndromes:
            for key, data in syndromes.items():
                trigger = data.get("trigger", "")
                if name in trigger:
                    parsed["selected_syndrome"]["name"] = key
                    name = key
                    break
            else:
                return None

        framework = parsed.get("differentiation_framework", "")
        valid = {"卫气营血", "脏腑", "混合"}
        if framework not in valid:
            parsed["differentiation_framework"] = "脏腑"

        return parsed

    def _load_syndromes(self, primary_disease: str) -> Dict:
        """从知识库加载某个疾病下的所有候选证型"""
        disease_data = self.kb.get(primary_disease)
        if not disease_data:
            return {}
        return disease_data.get("syndromes", {})

    def _llm_available(self) -> bool:
        # M2_DISABLE_LLM 环境变量可使 M2 跳过所有 LLM 调用（测试隔离用）
        if os.environ.get("M2_DISABLE_LLM", "").strip() in ("1", "true", "yes"):
            return False
        try:
            from m1_engine import M1DiagnosisEngine
            engine = M1DiagnosisEngine()
            return engine.llm_api_available()
        except Exception:
            return False

    def _call_llm(self, prompt: str) -> Optional[str]:
        if not self._llm_available():
            return None
        try:
            from m1_engine import M1DiagnosisEngine
            engine = M1DiagnosisEngine()
            return engine._call_llm(prompt, temperature=0.1, max_tokens=500)
        except Exception as _e:
            print(f"[LLM_ERR] M2 _call_llm failed: {_e}")
            return None

    def _fallback_parse(self, syndromes: Dict, primary_disease: str,
                        symptoms: List[str]) -> Optional[Dict]:
        """代码兜底：LLM 解析失败时使用 _syndrome_scorer 选择证型。

        规则：
        - 只在当前 disease_key 的 syndromes 内选择；
        - 不调用 LLM；
        - 不跨病名选证型；
        - 不自造证型/方剂/药物；
        - 若 scorer confidence >= 0.5，返回 scorer 结果；
        - 若候选只有 1 个且 confidence < 0.5，返回 None；
        - 若 contradiction_result 有 need_human_review，保留标记。

        Returns:
            scorer 结果 dict，或 None（无法选出可靠证型时）
        """
        if not syndromes:
            return None

        # 构造最小 patient_info
        patient_info = {
            "symptoms": symptoms or [],
            "signs": [],
            "tongue": "",
            "pulse": "",
            "cold_heat": [],
            "stool_urine": [],
            "sleep": [],
            "appetite": [],
            "labs": [],
            "imaging": [],
            "age": "",
            "weight": "",
        }

        scorer_result = self._syndrome_scorer(primary_disease, syndromes, patient_info)
        if not scorer_result:
            return None

        confidence = scorer_result.get("confidence", 0.0)
        selected = scorer_result.get("selected_syndrome", {})
        candidate_scores = scorer_result.get("candidate_scores", [])

        if not selected.get("name"):
            return None

        # 若 confidence >= 0.5，直接返回
        if confidence >= 0.5:
            return scorer_result

        # 若候选只有 1 个且 confidence < 0.5，不可靠，返回 None
        if len(candidate_scores) <= 1:
            return None

        # 多个候选但 confidence < 0.5：返回 scorer 但标记需人工复核
        scorer_result["needs_manual_review"] = True
        if "need_human_review" not in scorer_result:
            scorer_result["need_human_review"] = True
        return scorer_result

    def _syndrome_scorer(self, primary_disease: str, syndromes: Dict,
                         patient_info: Dict) -> Optional[Dict]:
        """证据似然估计器（evidence likelihood estimator）—— 非最终裁决者。

        Skill §6.1：本方法遍历证型池，按多维度计算每个候选证型的**证据似然强度**，
        作为以下用途：证据强度 / 似然贡献 / 候选排序辅助 / 贝叶斯后验输入。
        它**不是**最终证型裁决者——"总分最高"不得作为唯一选型依据；最终证型由
        §6.4 贝叶斯辨证裁决（_bayesian_syndrome_adjudicator）+ §6.5 受约束 LLM 复核确定。

        似然维度（归一化到 0~1 的 confidence 作为后验输入）：
        - 症状关键词匹配 (40pt)
        - 舌象匹配 (20pt)
        - 脉象匹配 (15pt)
        - 病理产物匹配 (痰/湿/瘀/热/寒) (15pt)
        - 寒热虚实匹配 (10pt)
        """
        if not syndromes:
            return None

        import re

        symptoms = patient_info.get("symptoms", []) or []
        tongue = patient_info.get("tongue", "") or ""
        pulse = patient_info.get("pulse", "") or ""
        cold_heat = patient_info.get("cold_heat", []) or []
        stool_urine = patient_info.get("stool_urine", []) or []
        appetite = patient_info.get("appetite", []) or []

        # 构建患者所有文本特征（用于匹配）
        _all_patient_text = " ".join(
            [s for s in symptoms if isinstance(s, str)] +
            [tongue, pulse] +
            [c for c in cold_heat if isinstance(c, str)] +
            [s for s in stool_urine if isinstance(s, str)] +
            [a for a in appetite if isinstance(a, str)]
        ).lower()

        # 专用病理产物/病机关键词列表
        _pathology_heat_keywords = ["热", "黄", "数", "渴", "烦躁", "便秘", "尿黄", "苔黄", "口苦"]
        _pathology_cold_keywords = ["寒", "白", "淡", "迟", "紧", "清稀", "不渴", "畏寒", "肢冷", "苔白"]
        _pathology_phlegm_keywords = ["痰", "腻", "滑", "咳", "浊", "黏", "胸", "黄绿痰", "黄稠痰", "黄黏痰"]
        _pathology_dampness_keywords = ["湿", "苔腻", "黄腻", "厚", "濡", "水肿", "困重", "纳呆", "便溏"]
        _pathology_blood_stasis_keywords = ["瘀", "紫", "暗", "涩", "刺痛", "肿块", "舌有瘀点", "舌下", "癥"]
        _pathology_qi_stagnation_keywords = ["胀", "闷", "痛", "叹气", "抑郁", "善太息", "脉弦"]

        # 提取患者舌象关键词
        _patient_tongue_keywords = set()
        for kw in re.findall(r'[舌苔薄白黄腻厚燥滑润干红绛紫暗淡青]*', tongue):
            if kw:
                _patient_tongue_keywords.add(kw)
        # 从舌质/苔标准的描述中提取（捕获舌和苔两部分）
        _tongue_patterns = re.findall(r'(舌[^，。；,]*?)(?=[，。；,])', tongue)
        if not _tongue_patterns:
            _tongue_patterns = re.findall(r'(舌[^，。；,;]*)', tongue)
        # 也捕获苔质部分
        _moss_patterns = re.findall(r'(苔[^，。；,;]*)', tongue)
        _patient_tongue_full = (" ".join(_tongue_patterns) + " " + " ".join(_moss_patterns)).lower().strip()
        if not _patient_tongue_full or _patient_tongue_full == " ":
            _patient_tongue_full = tongue.lower()

        # 提取患者脉象关键词
        _pulse_patterns = re.findall(r'(脉[^，。；,;]*)', pulse)
        _patient_pulse_full = " ".join(_pulse_patterns).lower()
        if not _patient_pulse_full:
            _patient_pulse_full = pulse.lower()

        # 对每个证型评分
        candidate_scores = []
        for name, data in syndromes.items():
            trigger = str(data.get("trigger", "")).lower()
            display_name_match = re.search(r'<(.+?)>', trigger)
            display_name = display_name_match.group(1) if display_name_match else name

            score = 0.0
            matched_symptoms_list = []
            matched_pathology_list = []
            matched_tongue_pulse_items = []
            total_possible = 0

            # ── 维度1：症状关键词匹配 (0-40pt) ──
            symptom_score = 0.0
            syndrome_symptoms = data.get("symptoms", [])
            if isinstance(syndrome_symptoms, list) and len(syndrome_symptoms) > 0:
                matched_count = 0
                # Track which patient symptoms matched via structured symptoms
                struct_matched_set = set()
                for ps in symptoms:
                    if isinstance(ps, str) and ps.strip():
                        ps_lower = ps.lower()
                        matched_any = False
                        for ss in syndrome_symptoms:
                            if isinstance(ss, str):
                                ss_lower = ss.lower()
                                # 支持子串和反向匹配
                                if ss_lower in ps_lower or ps_lower in ss_lower:
                                    matched_count += 1
                                    matched_any = True
                                    break
                                # 关键字符匹配（如"痰白清稀" vs "痰白而稀"）
                                ps_chars = set(ps_lower.replace(' ', ''))
                                ss_chars = set(ss_lower.replace(' ', ''))
                                overlap = ps_chars & ss_chars
                                min_len = min(len(ps_chars), len(ss_chars))
                                if min_len > 0 and len(overlap) / min_len >= 0.5:
                                    matched_count += 1
                                    matched_any = True
                                    break
                        if matched_any:
                            if ps not in matched_symptoms_list:
                                matched_symptoms_list.append(ps)
                            struct_matched_set.add(ps)
                # trigger 文本 fallback：对未匹配到的患者症状再尝试匹配 trigger
                trigger_text_no_bracket = re.sub(r'<.*?>', '', trigger)
                for ps in symptoms:
                    if isinstance(ps, str) and ps.strip() and ps not in struct_matched_set:
                        ps_lower = ps.lower()
                        if ps_lower in trigger_text_no_bracket:
                            matched_count += 1
                            if ps not in matched_symptoms_list:
                                matched_symptoms_list.append(ps)
                        else:
                            # 双字及以上关键字符匹配（如"怕冷"与"恶寒"无直接子串，取双字片段匹配）
                            for i in range(len(ps_lower) - 1):
                                bigram = ps_lower[i:i+2]
                                if bigram in trigger_text_no_bracket:
                                    matched_count += 1
                                    if ps not in matched_symptoms_list:
                                        matched_symptoms_list.append(ps)
                                    break
                if matched_count > 0:
                    symptom_score = min(40.0, matched_count * 12.0)
                else:
                    symptom_score = 1.0
            else:
                symptom_keywords = set()
                for s in symptoms:
                    if isinstance(s, str) and s.strip():
                        for part in re.findall(r'[^\s，,]+', s.strip()):
                            if len(part) >= 2:
                                symptom_keywords.add(part.lower())
                trigger_text_no_bracket = re.sub(r'<.*?>', '', trigger)
                matched_count = 0
                for kw in symptom_keywords:
                    if kw in trigger_text_no_bracket:
                        matched_count += 1
                        if kw not in matched_symptoms_list:
                            matched_symptoms_list.append(kw)
                if symptom_keywords:
                    symptom_score = min(40.0, (matched_count / max(len(symptom_keywords), 1)) * 40.0)

            # ── 维度2：舌象匹配 (0-20pt) ──
            tongue_score = 0.0
            if _patient_tongue_full:
                tongue_hits = 0
                # 使用证型的结构化 tongue 字段优先
                syndrome_tongue = data.get("tongue", "")
                syndrome_pulse = data.get("pulse", "")
                if syndrome_tongue:
                    # 结构化舌象匹配
                    if "红" in syndrome_tongue and "红" in _patient_tongue_full and "不红" not in syndrome_tongue:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("舌红")
                    if "淡" in syndrome_tongue and "淡" in _patient_tongue_full:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("舌淡")
                    if "暗" in syndrome_tongue and "暗" in _patient_tongue_full:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("舌暗")
                    if "紫" in syndrome_tongue and "紫" in _patient_tongue_full:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("舌紫")
                    if "胖" in syndrome_tongue and "胖" in _patient_tongue_full:
                        tongue_hits += 1
                    if "齿痕" in syndrome_tongue and "齿痕" in _patient_tongue_full:
                        tongue_hits += 1
                    if "腻" in syndrome_tongue and "腻" in _patient_tongue_full:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("苔腻")
                    if "黄" in syndrome_tongue and "黄" in _patient_tongue_full and "不黄" not in syndrome_tongue:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("苔黄")
                    if "白" in syndrome_tongue and "白" in _patient_tongue_full and "不白" not in syndrome_tongue:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("苔白")
                    if "薄" in syndrome_tongue and "薄" in _patient_tongue_full:
                        tongue_hits += 1
                        matched_tongue_pulse_items.append("苔薄")
                    if "燥" in syndrome_tongue and ("燥" in _patient_tongue_full or "干" in _patient_tongue_full):
                        tongue_hits += 1
                else:
                    # Fallback: trigger 文本舌象匹配（原有逻辑）
                    tongue_checks = [
                        ("舌红", "舌红" in trigger or "舌质红" in trigger),
                        ("舌淡", "舌淡" in trigger or "舌质淡" in trigger),
                        ("舌紫", "舌紫" in trigger or "舌暗红" in trigger or "舌有瘀点" in trigger),
                        ("苔黄", "苔黄" in trigger),
                        ("苔白", "苔白" in trigger),
                        ("苔腻", "苔腻" in trigger),
                        ("苔薄", "苔薄" in trigger),
                        ("苔厚", "苔厚" in trigger),
                        ("胖大", "胖大" in trigger or "齿痕" in trigger),
                        ("瘀斑", "瘀斑" in trigger or "瘀点" in trigger),
                    ]
                    for label, present in tongue_checks:
                        if label in _patient_tongue_full:
                            if present:
                                tongue_hits += 1
                                matched_tongue_pulse_items.append(label)
                tongue_score = min(20.0, tongue_hits * 3.0)
                if not matched_tongue_pulse_items and "舌" in _patient_tongue_full:
                    tongue_score = 2.0
                    # 患者有舌象但无匹配 — 给一个基础分
                    tongue_score = 2.0

            # ── 维度3：脉象匹配 (0-15pt) ──
            pulse_score = 0.0
            if _patient_pulse_full:
                pulse_hits = 0
                pulse_checks = [
                    ("脉浮", "脉浮" in trigger),
                    ("脉沉", "脉沉" in trigger),
                    ("脉数", "脉数" in trigger),
                    ("脉迟", "脉迟" in trigger),
                    ("脉滑", "脉滑" in trigger),
                    ("脉涩", "脉涩" in trigger),
                    ("脉弦", "脉弦" in trigger),
                    ("脉细", "脉细" in trigger),
                    ("脉弱", "脉弱" in trigger),
                    ("脉濡", "脉濡" in trigger),
                    ("脉缓", "脉缓" in trigger),
                    ("脉紧", "脉紧" in trigger),
                    ("脉有力", "脉有力" in trigger or "脉实" in trigger),
                    ("脉无力", "脉无力" in trigger or "脉虚" in trigger),
                ]
                for label, present in pulse_checks:
                    if label in _patient_pulse_full:
                        if present:
                            pulse_hits += 1
                            if label not in matched_tongue_pulse_items:
                                matched_tongue_pulse_items.append(label)
                pulse_score = min(15.0, pulse_hits * 2.5)
                if not pulse_hits and "脉" in _patient_pulse_full:
                    pulse_score = 1.0

            # ── 维度4：病理产物匹配 (0-15pt) ──
            pathology_score = 0.0
            pathology_hits = 0

            # 否定前缀检测函数
            _negation_prefixes = ["无明显", "无明确", "无", "未", "没有", "否认", "不伴"]
            def _is_negated(kw: str, text: str) -> bool:
                for np_ in sorted(_negation_prefixes, key=len, reverse=True):
                    if np_ + kw in text:
                        return True
                return False

            # 判断患者整体偏寒还是偏热（用于互斥逻辑），考虑否定前缀
            def _heat_kw_match(kw: str, text: str) -> bool:
                return kw in text and not _is_negated(kw, text)
            def _cold_kw_match(kw: str, text: str) -> bool:
                return kw in text and not _is_negated(kw, text)

            _patient_heat_count = sum(1 for kw in _pathology_heat_keywords if _heat_kw_match(kw, _all_patient_text))
            _patient_cold_count = sum(1 for kw in _pathology_cold_keywords if _cold_kw_match(kw, _all_patient_text))
            _patient_is_heat = _patient_heat_count > _patient_cold_count
            _patient_is_cold = _patient_cold_count > _patient_heat_count

            # 热 — 只在患者偏热时匹配
            if _patient_heat_count > 0 and not _patient_is_cold:
                if any(kw in trigger for kw in ["热", "黄", "数"]):
                    pathology_hits += 1
                    matched_pathology_list.append("热")
                elif "不" not in trigger[:20]:
                    # 不明确否定热的情况下也尝试匹配
                    if "热" in trigger:
                        pathology_hits += 1
                        matched_pathology_list.append("热")
            # 寒 — 只在患者偏寒时匹配
            if _patient_cold_count > 0 and not _patient_is_heat:
                if any(kw in trigger for kw in ["寒", "白", "淡"]):
                    pathology_hits += 1
                    matched_pathology_list.append("寒")
            # 痰
            if any(kw in _all_patient_text for kw in _pathology_phlegm_keywords):
                if "痰" in trigger:
                    pathology_hits += 1
                    matched_pathology_list.append("痰")
            # 湿
            if any(kw in _all_patient_text for kw in _pathology_dampness_keywords):
                if any(kw in trigger for kw in ["湿", "腻", "濡"]):
                    pathology_hits += 1
                    matched_pathology_list.append("湿")
            # 瘀
            if any(kw in _all_patient_text for kw in _pathology_blood_stasis_keywords):
                if any(kw in trigger for kw in ["瘀", "紫", "暗", "涩"]):
                    pathology_hits += 1
                    matched_pathology_list.append("瘀")
            # 气滞
            if any(kw in _all_patient_text for kw in _pathology_qi_stagnation_keywords):
                # 气滞触发词需要更精确匹配（有弦/胀/闷/叹气等明确词才算）
                qi_hit = False
                for qi_kw in ["胀", "痛（走窜）", "叹气", "抑郁", "善太息"]:
                    if qi_kw in _all_patient_text:
                        qi_hit = True
                        break
                if not qi_hit:
                    # "闷" 需要结合上下文，只对有"胸闷""胸胁胀满"等匹配
                    if "胸闷" in _all_patient_text or "胸胁" in _all_patient_text:
                        qi_hit = "闷" in trigger
                    elif "脉弦" in _patient_pulse_full:
                        qi_hit = True
                    else:
                        qi_hit = "弦" in trigger
                if qi_hit:
                    pathology_hits += 1
                    matched_pathology_list.append("气滞")
            pathology_score = min(15.0, pathology_hits * 3.0)

            # ── 维度5：寒热虚实匹配 (0-10pt) ──
            cold_heat_score = 0.0
            has_cold_coldheat = False
            has_hot_coldheat = False
            for ch in cold_heat:
                chl = ch.lower() if isinstance(ch, str) else ""
                if "热" in chl or "烧" in chl:
                    has_hot_coldheat = True
                    if "热" in trigger:
                        cold_heat_score += 3.0
                if "寒" in chl or "冷" in chl or "恶" in chl:
                    has_cold_coldheat = True
                    if "寒" in trigger:
                        cold_heat_score += 3.0
                if "虚" in chl and "虚" in trigger:
                    cold_heat_score += 2.0
                if ("实" in chl or "盛" in chl) and ("实" in trigger or "盛" in trigger):
                    cold_heat_score += 2.0
            cold_heat_score = min(10.0, cold_heat_score)

            # 寒热互斥惩罚：患者明确偏寒时降低纯热证得分
            if (_patient_is_cold or has_cold_coldheat) and ("热" in trigger and "寒" not in trigger and "风热" not in trigger):
                cold_heat_score = max(0, cold_heat_score - 5.0)
            # 患者明确偏热时降低纯寒证得分（用 display_name 判断寒热属性，避免 trigger 中病理标记干扰）
            if (_patient_is_heat or has_hot_coldheat):
                is_plain_cold = ("寒" in display_name and "风热" not in display_name and "热" not in display_name)
                if is_plain_cold:
                    cold_heat_score = max(0, cold_heat_score - 5.0)

            # 特异性词惩罚（使用否定感知匹配）
            heat_specific = ["口干", "口苦", "黄痰", "黄涕", "舌红", "烦躁", "灼痛"]
            cold_specific = ["怕冷", "恶寒", "清稀", "苔白", "舌淡", "不渴", "肢冷"]
            _matched_cold_specific = [cs for cs in cold_specific
                                      if cs in _all_patient_text and not _is_negated(cs, _all_patient_text)]
            _matched_heat_specific = [hs for hs in heat_specific
                                      if hs in _all_patient_text and not _is_negated(hs, _all_patient_text)]
            # 热特异性词 → 纯寒证或风热证之外降分（用 display_name 判断）
            if _matched_heat_specific:
                has_heat_in_display = "热" in display_name or "风热" in display_name
                if not has_heat_in_display:
                    cold_heat_score = max(0, cold_heat_score - 2.0)
            # 寒特异性词 → 纯热证扣分；若有 2+ 项寒特异词，也扣热性证型（包括风热）
            if _matched_cold_specific:
                # 使用证型显示名判断寒热属性，避免触发词中的对比描述干扰
                is_cold_syndrome = "寒" in display_name and "风热" not in display_name
                if not is_cold_syndrome:
                    cold_heat_score = max(0, cold_heat_score - 2.0)
                # 患者有 2+ 项典型寒象 → 热性证型（包括风热）额外扣分
                if len(_matched_cold_specific) >= 2 and not is_cold_syndrome:
                    cold_heat_score = max(0, cold_heat_score - 3.0)

            # ── 总分（寒热匹配计入） ──
            total_score = symptom_score + tongue_score + pulse_score + pathology_score + cold_heat_score

            # ── 症状证素库注入（M2-1 强制步骤） ──
            symptom_factor_evidence = patient_info.get("symptom_factor_evidence", {})
            factor_contradiction = patient_info.get("factor_contradiction_result", {})
            if symptom_factor_evidence:
                tcm_factors = symptom_factor_evidence.get("tcm_factor_evidence", {})
                present_factors = list(tcm_factors.keys())
                # 证素证据辅助评分：如果当前证型的 display_name 包含患者匹配的证素，加分
                for factor in present_factors:
                    if factor in trigger or factor in display_name:
                        total_score += 1.0
                # 反证规则：强反对的证素出现在当前证型中 → 降分
                for against in factor_contradiction.get("strong_against", []):
                    if isinstance(against, dict):
                        af = against.get("factor_a", "")
                        bf = against.get("factor_b", "")
                        if af in display_name or bf in display_name or af in trigger or bf in trigger:
                            total_score -= 3.0
                    elif isinstance(against, str):
                        # plain string "factor" from penalty
                        if against in display_name or against in trigger:
                            total_score -= 5.0
                # 强支持的证素 → 当前证型含该证素则加分
                for support in factor_contradiction.get("strong_support", []):
                    if support in display_name or support in trigger:
                        total_score += 3.0
                # 若 symptom_factor_evidence 中有黄痰/黄绿痰+苔黄+口干等热象证据，
                # 且当前证型名为"风寒" → 强降分
                _has_yellow_phlegm_factor = "痰" in tcm_factors and "热" in tcm_factors
                _has_cold_syndrome = "风寒" in display_name
                # 需使用否定感知，防止"无明显口干"误判
                def _pos_match(kw, text):
                    return kw in text and not _is_negated(kw, text)
                _patient_has_yellow = any(_pos_match(kw, _all_patient_text) for kw in ["黄痰", "黄绿痰", "黄稠", "咽痛", "苔黄"])
                _patient_has_dry = any(_pos_match(kw, _all_patient_text) for kw in ["口干", "口苦", "口渴"])
                if _has_cold_syndrome and (_patient_has_yellow or _patient_has_dry):
                    total_score -= 5.0

            # 证型全名匹配导致的额外总分调整（证型名含"风热"但患者偏寒，或含"风寒"但患者偏热）
            if has_cold_coldheat and "风热" in display_name:
                total_score = max(0, total_score - 3.0)
            if has_hot_coldheat and "风寒" in display_name:
                total_score = max(0, total_score - 3.0)

            # 虚实证型偏好：当患者有明确实热症状（黄痰、苔黄腻等），虚证证型降分
            _patient_has_heat_phlegm = any(kw in _all_patient_text for kw in ["黄痰", "黄稠", "铁锈色", "黄腻", "黄绿痰", "黄黏痰", "黄稠痰"])
            if _patient_has_heat_phlegm and "虚" in trigger:
                total_score -= 5.0
            # 当患者有明确气虚证候（气短、神疲）且证型含"虚"，加分
            _patient_has_qi_xu = any(kw in _all_patient_text for kw in ["气短", "神疲", "乏力", "自汗"])
            if _patient_has_qi_xu and "虚" in trigger:
                total_score += 3.0
            # 热象证型偏好：当患者同时有热象+黄痰，提升含"痰""热"证型
            if _patient_has_heat_phlegm and "痰" in trigger and "热" in trigger:
                total_score += 3.0

            # ── 痰热综合证据加分：多个热痰相关指标同时出现时更强支持痰热壅肺 ──
            _patient_has_bitter = any(kw in _all_patient_text for kw in ["口苦", "口干口苦"])
            _patient_has_dry_mouth = any(kw in _all_patient_text for kw in ["口干", "口燥", "渴"])
            _patient_has_yellow_tongue = any(kw in _all_patient_text for kw in ["苔黄", "黄苔", "舌红", "舌质红"])
            _patient_has_greasy_tongue = any(kw in _all_patient_text for kw in ["苔腻", "黄腻", "厚腻"])

            # 复合热痰证据计数：黄痰 + 苔黄腻 + 口干/口苦
            _heat_phlegm_composite_score = sum([
                _patient_has_heat_phlegm,
                _patient_has_yellow_tongue and _patient_has_greasy_tongue,
                _patient_has_bitter or _patient_has_dry_mouth,
            ])
            if _heat_phlegm_composite_score >= 2:
                # 有2项以上痰热证据 → 痰热证型额外加分，推动"痰+热"类证型占优
                if any(kw in trigger for kw in ["痰热", "热痰", "痰+热"]) or ("痰" in trigger and "热" in trigger):
                    total_score += 5.0
                # (反证规则) 同时有2项以上痰热证据 → 风热表证降分
                if "风热" in display_name:
                    total_score -= 8.0
            # 特别规则：黄痰/黄绿痰 + 苔黄腻/舌红 + 口苦/口干 三项齐备 → 强反证
            if _patient_has_heat_phlegm and (_patient_has_bitter or _patient_has_dry_mouth) and _patient_has_greasy_tongue:
                if "风热" in display_name:
                    total_score -= 8.0
            confidence = round(min(1.0, total_score / 60.0), 2)  # 60分以上 = 高置信度

            # 当所有维度得分为0时，给最低分避免排序混乱
            if total_score == 0 and len(syndromes) > 0:
                total_score = 0.1

            candidate_scores.append({
                "syndrome_name": display_name,
                "score": round(total_score, 1),
                "confidence": confidence,
                "matched_symptoms": matched_symptoms_list[:6],
                "matched_pathology": "、".join(set(matched_pathology_list)) if matched_pathology_list else "",
                "matched_tongue_pulse": "，".join(matched_tongue_pulse_items[:6]) if matched_tongue_pulse_items else "",
                "symptom_match": round(symptom_score, 1),
                "tongue_match": round(tongue_score, 1),
                "pulse_match": round(pulse_score, 1),
                "pathology_match": round(pathology_score, 1),
                "cold_heat_match": round(cold_heat_score, 1),
            })

        # 按得分降序排列
        candidate_scores.sort(key=lambda x: x["score"], reverse=True)

        if not candidate_scores:
            return None

        best = candidate_scores[0]
        best_syndrome_name = best["syndrome_name"]

        # 找到最佳证型对应的原始 key
        best_key = None
        for name, data in syndromes.items():
            dn = re.search(r'<(.+?)>', str(data.get("trigger", "")))
            display_n = dn.group(1) if dn else name
            if display_n == best_syndrome_name or name == best_syndrome_name:
                best_key = name
                break
        if not best_key:
            best_key = list(syndromes.keys())[0]

        # 构建缺失信息
        missing = []
        if not tongue.strip():
            missing.append("舌象")
        if not pulse.strip():
            missing.append("脉象")
        if not symptoms:
            missing.append("症状")

        matched_pathology_str = best.get("matched_pathology", "")
        matched_tongue_pulse_str = best.get("matched_tongue_pulse", "")
        matched_symptoms_str = best.get("matched_symptoms", [])

        return {
            "selected_syndrome": {
                "name": best_key,
                "reason": f"证型评分器: {best_syndrome_name} (总分{best['score']}，置信度{best['confidence']})",
            },
            "differentiation_framework": "脏腑辨证",
            "candidate_scores": candidate_scores,
            "matched_symptoms": matched_symptoms_str,
            "matched_tongue_pulse": matched_tongue_pulse_str,
            "matched_pathology": matched_pathology_str,
            "missing_info": "；".join(missing) if missing else "",
            "confidence": best["confidence"],
            "reasoning": f"证型评分器最佳匹配: {best_syndrome_name} (症状={best['symptom_match']}, "
                         f"舌象={best['tongue_match']}, 脉象={best['pulse_match']}, "
                         f"病理={best['pathology_match']}, 寒热={best['cold_heat_match']})",
        }

    # ══════════════════════════════════════════════════════
    #  贝叶斯辨证裁决 (Skill §6.4) — 取代"总分最高=最终证型"
    # ══════════════════════════════════════════════════════

    @staticmethod
    def _syndrome_display_name(name: str, data: Dict) -> str:
        """从证型 trigger 的 <...> 标记提取展示名，无标记则用 key 名。"""
        m = re.search(r'<(.+?)>', str(data.get("trigger", "")))
        return m.group(1) if m else name

    # ──────────────────────────────────────────────────────
    #  因果解释型贝叶斯辨证 — 证据/病机链/反事实 (Skill §6.4)
    # ──────────────────────────────────────────────────────

    @staticmethod
    def _m2_is_negated(kw: str, text: str) -> bool:
        """否定前缀检测（最长前缀优先），与 scorer/loader 一致。"""
        for np_ in sorted(_M2_NEGATION_PREFIXES, key=len, reverse=True):
            if np_ + kw in text:
                return True
        return False

    def _m2_pos_match(self, kw: str, text: str) -> bool:
        """阳性出现：出现且未被否定前缀否定（否定症状不计为阳性证据）。"""
        return bool(kw) and kw in text and not self._m2_is_negated(kw, text)

    def _patient_combined_text(self, patient_info: Dict) -> str:
        """汇总患者全部文本特征（小写），用于因果证据匹配。"""
        parts: List[str] = []
        for field in ("symptoms", "signs", "cold_heat", "stool_urine",
                      "appetite", "sleep", "labs", "imaging"):
            for x in (patient_info.get(field, []) or []):
                if isinstance(x, str):
                    parts.append(x)
        for field in ("tongue", "pulse"):
            v = patient_info.get(field, "")
            if isinstance(v, str) and v:
                parts.append(v)
        return " ".join(parts).lower()

    def _extract_patient_findings(self, patient_info: Dict) -> Dict:
        """Skill §6.4.5：抽取并分层患者证据（causal / manifestation / weak）。

        否定项不计为阳性证据。返回 key_findings = causal + manifestation（去重，
        弱证据不计入关键发现），并标记主症 chief_symptom。
        """
        text = self._patient_combined_text(patient_info)
        causal = [kw for kw in _M2_CAUSAL_EVIDENCE_KEYWORDS if self._m2_pos_match(kw, text)]
        manifestation = [kw for kw in _M2_MANIFESTATION_EVIDENCE_KEYWORDS if self._m2_pos_match(kw, text)]
        weak = [kw for kw in _M2_WEAK_EVIDENCE_KEYWORDS if self._m2_pos_match(kw, text)]

        # 主症：首个非否定患者症状
        chief = ""
        for s in (patient_info.get("symptoms", []) or []):
            if isinstance(s, str) and s.strip() and not self._m2_is_negated(s.strip().lower(), text):
                chief = s.strip()
                break

        # key_findings：因果证据优先 + 表现证据（去重；弱证据排除）
        key_findings: List[str] = []
        for kw in causal + manifestation:
            if kw not in key_findings:
                key_findings.append(kw)

        heat_findings = [kw for kw in _M2_DEFINING_HEAT_FINDINGS if self._m2_pos_match(kw, text)]
        cold_findings = [kw for kw in _M2_DEFINING_COLD_FINDINGS if self._m2_pos_match(kw, text)]

        return {
            "text": text,
            "causal_evidence": causal,
            "manifestation_evidence": manifestation,
            "weak_evidence": weak,
            "key_findings": key_findings,
            "chief_symptom": chief,
            "defining_heat_findings": heat_findings,
            "defining_cold_findings": cold_findings,
        }

    @staticmethod
    def _syndrome_direction(disp: str, pathology: str) -> str:
        """判断候选证型寒热方向：heat / cold / neutral（用 display + pathology）。"""
        blob = f"{disp} {pathology}"
        is_heat = any(kw in blob for kw in ["热", "火", "湿热", "痰热"]) and "寒" not in disp
        is_cold = (("寒" in disp or "风寒" in disp or "虚寒" in disp)
                   and "热" not in disp and "风热" not in disp)
        if is_heat and not is_cold:
            return "heat"
        if is_cold and not is_heat:
            return "cold"
        return "neutral"

    def _candidate_predicted_manifestations(self, key: str, data: Dict, disp: str) -> List[str]:
        """由证型节点（症状/舌脉/病机）推导『该病机应当生成的表现』。"""
        predicted: List[str] = []

        def _add(tok: str):
            tok = (tok or "").strip()
            if tok and tok not in predicted:
                predicted.append(tok)

        for s in (data.get("symptoms", []) or []):
            if isinstance(s, str):
                _add(s.strip().lower())
        for fld in ("tongue", "pulse"):
            v = str(data.get(fld, "")).lower()
            if v:
                _add(v)
        # 病机因子展开
        pathology = str(data.get("pathology", ""))
        for factor in re.split(r"[、,，\s]+", pathology):
            factor = factor.strip()
            for tok in _M2_PATHOGENESIS_PREDICTIONS.get(factor, []):
                _add(tok.lower())
        # 展示名中的病机字（痰热壅肺 → 痰/热）
        for factor, toks in _M2_PATHOGENESIS_PREDICTIONS.items():
            if factor in disp:
                for tok in toks:
                    _add(tok.lower())
        return predicted

    def _build_causal_chain(self, key: str, data: Dict, disp: str,
                            patient_findings: Dict, pathology_result: Optional[Dict]) -> Dict:
        """Skill §6.4.1：构建病机因果链——解释『该病机为何生成这些表现』。"""
        text = patient_findings["text"]
        predicted = self._candidate_predicted_manifestations(key, data, disp)
        pathology = str(data.get("pathology", ""))
        direction = self._syndrome_direction(disp, pathology)

        # 观测支持：预测表现中被患者阳性证实的项
        observed_support: List[str] = []
        for tok in predicted:
            # 预测 token 可能是长描述；按子串/双字片段匹配患者文本
            if self._m2_pos_match(tok, text):
                observed_support.append(tok)
            else:
                for i in range(len(tok) - 1):
                    bigram = tok[i:i + 2]
                    if len(bigram) == 2 and bigram in text and not self._m2_is_negated(bigram, text):
                        observed_support.append(tok)
                        break
        observed_support = list(dict.fromkeys(observed_support))

        # 未解释关键发现：患者 key_findings 中本病机预测集无法覆盖的项
        unexplained: List[str] = []
        pred_blob = " ".join(predicted)
        for kf in patient_findings["key_findings"]:
            covered = kf in pred_blob or any(kf in p or p in kf for p in predicted)
            if not covered:
                unexplained.append(kf)

        # 被反驳发现：与本病机方向相反的定义性证据（强反证）
        contradicted: List[str] = []
        if direction == "heat":
            contradicted = list(patient_findings["defining_cold_findings"])
        elif direction == "cold":
            contradicted = list(patient_findings["defining_heat_findings"])

        stage = ""
        if pathology_result:
            stage = (pathology_result.get("inferred_pathology_stage")
                     or pathology_result.get("pathology_stage") or "")

        pathogenesis = "、".join([f for f in re.split(r"[、,，\s]+", pathology) if f]) or disp
        mechanism = (
            f"病机【{pathogenesis}】→ 当其作用于该病程阶段({stage or '未明确'})时，"
            f"应生成 {('、'.join(predicted[:6]) or '相应表现')}；"
            f"当前病情证实 {('、'.join(observed_support[:6]) or '少量')} 项"
        )
        if contradicted:
            mechanism += f"；但存在与该病机方向相反的证据：{('、'.join(contradicted[:4]))}"

        return {
            "syndrome_name": disp,
            "pathology_stage": stage,
            "tcm_pathogenesis": pathogenesis,
            "mechanism": mechanism,
            "predicted_manifestations": predicted[:12],
            "observed_support": observed_support[:10],
            "unexplained_findings": unexplained[:8],
            "contradicted_findings": contradicted[:6],
            "direction": direction,
        }

    @staticmethod
    def _calculate_causal_coverage(causal_chain: Dict, patient_findings: Dict) -> float:
        """Skill §6.4.3：causal_coverage = explained_key_findings / total_key_findings。"""
        total = len(patient_findings["key_findings"])
        if total == 0:
            return 0.0
        unexplained = len(causal_chain.get("unexplained_findings", []))
        explained = max(0, total - unexplained)
        return round(explained / total, 4)

    def _run_counterfactual_check(self, key: str, data: Dict, disp: str,
                                  causal_chain: Dict, patient_findings: Dict,
                                  cold_heat_resolution: Optional[Dict]) -> Dict:
        """Skill §6.4.4：反事实检验——若该证成立应见/不应见，对比当前病情。

        - 出现『不应见』的定义性反向证据 → violated_expectations → FAIL；
        - 应见的核心证据缺失较多 → QUESTION；
        - 寒热裁决方向与本候选方向冲突 → FAIL。
        """
        direction = causal_chain.get("direction", "neutral")
        text = patient_findings["text"]

        expected = list(causal_chain.get("predicted_manifestations", []))[:8]
        observed = set(causal_chain.get("observed_support", []))

        # 若该证成立，不应出现的定义性反向证据
        if direction == "heat":
            unexpected_pool = _M2_DEFINING_COLD_FINDINGS
            dir_support = patient_findings["defining_heat_findings"]
        elif direction == "cold":
            unexpected_pool = _M2_DEFINING_HEAT_FINDINGS
            dir_support = patient_findings["defining_cold_findings"]
        else:
            unexpected_pool = []
            dir_support = None  # 中性证型不依赖寒热定义性证据
        if_true_unexpected = list(unexpected_pool)
        violated = [kw for kw in unexpected_pool if self._m2_pos_match(kw, text)]

        # 寒热裁决方向冲突 → 视为违例
        main_dir = (cold_heat_resolution or {}).get("main_direction", "")
        dir_conflict = (main_dir == "heat" and direction == "cold") or \
                       (main_dir == "cold" and direction == "heat")

        # missing_expected_findings：声称该方向但核心(定义性)证据缺失
        missing_expected = []
        if direction in ("heat", "cold") and not dir_support:
            missing_expected = [e for e in expected if e not in observed][:6]

        result = "PASS"
        if violated or dir_conflict:
            # 出现定义性反向证据 / 寒热裁决冲突 → 反事实失败
            result = "FAIL"
        elif direction in ("heat", "cold") and not dir_support:
            # 声称寒/热方向，但患者无该方向的任何定义性证据 → 核心病机未被支持
            result = "QUESTION"

        return {
            "syndrome_name": disp,
            "if_syndrome_true_expected": expected,
            "if_syndrome_true_unexpected": if_true_unexpected[:6],
            "missing_expected_findings": missing_expected,
            "violated_expectations": violated[:6] + (["寒热裁决方向冲突"] if dir_conflict else []),
            "counterfactual_result": result,
        }

    # ══════════════════════════════════════════════════════
    #  §6.4.8 方剂干预验证
    # ══════════════════════════════════════════════════════

    def _run_formula_intervention_check(self, syndrome_key: str, syndrome_data: Dict,
                                         causal_chain: Dict) -> Dict:
        """Skill §6.4.8：验证绑定方剂是否干预 causal_chain 的关键病机。"""
        formula_name = str(syndrome_data.get("formula_name", ""))
        herbs = syndrome_data.get("herbs", [])
        if not formula_name:
            return {
                "formula_name": "",
                "formula_intervention_target": [],
                "matched_pathogenesis_targets": [],
                "unmatched_pathogenesis_targets": [],
                "formula_causal_match": "QUESTION",
                "reason": "无绑定方剂",
            }

        # 方剂靶点推断（基于方名关键词和药物性味功效）
        formula_targets = []
        # 方名本身含有强烈靶点语义
        if any(kw in formula_name for kw in ["清热", "解毒", "泻心", "白虎", "清营"]):
            formula_targets.append("清热")
        if any(kw in formula_name for kw in ["温", "暖肝", "回阳", "四逆"]):
            formula_targets.append("散寒")
        # 已知名方靶点（KB herbal list 可能不完整，以方名为准）
        if formula_name in ("麻杏石甘汤",):
            formula_targets.append("清热")
            formula_targets.append("化痰")
        if formula_name in ("银翘散", "桑菊饮", "黄连解毒汤"):
            formula_targets.append("清热")
        if formula_name in ("二陈汤", "温胆汤", "清气化痰丸"):
            formula_targets.append("化痰")
        formula_text = (formula_name + " " + " ".join(herbs[:10])).lower()
        formula_text = (formula_name + " " + " ".join(herbs[:10])).lower()
        if any(kw in formula_text for kw in ["石膏", "知母", "黄芩", "黄连", "黄柏"]):
            formula_targets.append("清热")
        if any(kw in formula_text for kw in ["银花", "连翘", "金银花", "板蓝", "大青"]):
            formula_targets.append("清热")
        if any(kw in formula_text for kw in ["瓜蒌", "浙贝", "川贝", "半夏", "桔梗", "胆南星", "竹茹", "天竺黄"]):
            formula_targets.append("化痰")
        if any(kw in formula_text for kw in ["干姜", "附子", "肉桂", "桂枝"]):
            formula_targets.append("散寒")
        if any(kw in formula_text for kw in ["麻黄", "细辛"]):
            # 麻黄也可散寒, 但考察整体方名：麻杏石甘汤以"石"(石膏)清热为核心
            formula_targets.append("散寒")
        if any(kw in formula_text for kw in ["黄芪", "人参", "党参", "太子参"]):
            formula_targets.append("补气")
        if any(kw in formula_text for kw in ["麦冬", "生地", "沙参", "玉竹", "玄参", "天花粉"]):
            formula_targets.append("养阴")
        if any(kw in formula_text for kw in ["桃仁", "红花", "川芎", "丹参", "赤芍", "归尾"]):
            formula_targets.append("活血")
        if any(kw in formula_text for kw in ["茯苓", "泽泻", "薏苡", "苍术", "猪苓"]):
            formula_targets.append("祛湿")
        if any(kw in formula_text for kw in ["柴胡", "枳壳", "陈皮", "木香", "香附"]):
            formula_targets.append("行气")

        # 去重
        formula_targets = list(dict.fromkeys(formula_targets))

        # causal_chain 的病机关键靶点
        mechanism = str(causal_chain.get("mechanism", ""))
        mech_lower = mechanism.lower()
        patho_targets = []
        if any(kw in mech_lower for kw in ["热", "火", "温"]):
            patho_targets.append("清热")
        if any(kw in mech_lower for kw in ["痰", "湿"]):
            patho_targets.append("化痰")
        if any(kw in mech_lower for kw in ["寒", "凉"]):
            patho_targets.append("散寒")
        if any(kw in mech_lower for kw in ["气虚", "阳虚", "脾虚", "肺虚"]):
            patho_targets.append("补气")
        if any(kw in mech_lower for kw in ["阴虚", "阴亏", "津亏"]):
            patho_targets.append("养阴")
        if any(kw in mech_lower for kw in ["瘀", "血"]):
            patho_targets.append("活血")
        if any(kw in mech_lower for kw in ["湿", "水肿", "泄泻"]):
            patho_targets.append("祛湿")
        if any(kw in mech_lower for kw in ["气滞", "郁"]):
            patho_targets.append("行气")

        matched = [t for t in patho_targets if t in formula_targets]
        unmatched = [t for t in patho_targets if t not in formula_targets]

        if matched and not unmatched:
            match_result = "PASS"
        elif matched and unmatched:
            match_result = "QUESTION"
        else:
            match_result = "FAIL"

        return {
            "formula_name": formula_name,
            "formula_intervention_target": formula_targets,
            "matched_pathogenesis_targets": matched,
            "unmatched_pathogenesis_targets": unmatched,
            "formula_causal_match": match_result,
        }

    def _estimate_stage_prior(self, syndromes: Dict, disease_key: str,
                              patient_info: Dict, pathology_result: Optional[Dict],
                              merged: Optional[Dict], cold_heat_resolution: Optional[Dict],
                              patient_findings: Dict) -> Dict[str, Dict]:
        """Skill §6.4.2：非均匀阶段先验。

        综合：病名常见度（池内次序）、当前病理阶段、节点适用阶段、节点来源可靠度、
        与当前病程/双轨方向是否匹配。均匀先验仅作 fallback。
        """
        keys = list(syndromes.keys())
        n = len(keys)
        prior: Dict[str, Dict] = {}
        if n == 0:
            return prior

        stage = ""
        if pathology_result:
            stage = (pathology_result.get("inferred_pathology_stage")
                     or pathology_result.get("pathology_stage") or "")
        merged_key = (merged or {}).get("selected_syndrome_key", "")
        main_dir = (cold_heat_resolution or {}).get("main_direction", "")
        # 患者主导寒热方向（无显式冲突时，用定义性证据数量推断）
        if not main_dir or main_dir in ("mixed", "unknown"):
            n_heat = len(patient_findings["defining_heat_findings"])
            n_cold = len(patient_findings["defining_cold_findings"])
            patient_dir = "heat" if n_heat > n_cold else ("cold" if n_cold > n_heat else "")
        else:
            patient_dir = main_dir

        weights: Dict[str, float] = {}
        reasons: Dict[str, List[str]] = {}
        for idx, k in enumerate(keys):
            data = syndromes[k]
            disp = self._syndrome_display_name(k, data)
            trigger = str(data.get("trigger", "")).lower()
            pathology = str(data.get("pathology", ""))
            w = 1.0
            rs: List[str] = []

            # 1) 常见度：池内靠前的证型通常更常见/典型
            commonness = 1.0 + max(0.0, (0.25 - 0.04 * idx))
            w *= commonness
            rs.append(f"常见度×{commonness:.2f}")

            # 2) 节点适用阶段 vs 当前病程阶段
            node_stage = ""
            if any(kw in trigger for kw in ["初起", "初期", "表证", "恶寒发热", "浮"]):
                node_stage = "表证期"
            elif any(kw in trigger for kw in ["高热", "壅", "壮热", "里热", "腑实"]):
                node_stage = "里热期"
            elif any(kw in trigger for kw in ["正虚", "气短神疲", "干咳少痰", "日久", "缠绵"]):
                node_stage = "正气不足期"
            elif any(kw in trigger for kw in ["脱", "竭", "厥", "微欲绝"]):
                node_stage = "危重期"
            if stage and node_stage:
                if node_stage in stage or stage in node_stage or \
                   (("炎症" in stage or "里热" in stage) and node_stage == "里热期") or \
                   (("表证" in stage) and node_stage == "表证期"):
                    w *= 1.5
                    rs.append("阶段匹配×1.5")
                else:
                    w *= 0.75
                    rs.append("阶段不符×0.75")

            # 3) 与当前病程寒热方向匹配
            direction = self._syndrome_direction(disp, pathology)
            if patient_dir and direction != "neutral":
                if direction == patient_dir:
                    w *= 1.4
                    rs.append(f"病程方向匹配({patient_dir})×1.4")
                else:
                    w *= 0.5
                    rs.append(f"病程方向相反×0.5")

            # 4) 双轨方向匹配（与 §6.3 合并结果一致 → 提升先验）
            if merged_key and k == merged_key:
                w *= 1.6
                rs.append("双轨方向匹配×1.6")

            # 5) 来源可靠度
            src = str(data.get("source", "")) or str(data.get("source_type", ""))
            if src.strip():
                w *= 1.1
                rs.append("来源可考×1.1")

            weights[k] = max(w, 1e-6)
            reasons[k] = rs

        total = sum(weights.values())
        uniform = all(abs(weights[k] - weights[keys[0]]) < 1e-9 for k in keys)
        prior_type = "uniform_fallback" if uniform else "stage_weighted"
        for k in keys:
            p = (weights[k] / total) if total > 0 else (1.0 / n)
            prior[k] = {
                "prior": round(p, 4),
                "prior_reason": (
                    f"阶段先验[{prior_type}]: " + "，".join(reasons[k])
                    if not uniform else f"证型池均匀 fallback (1/{n})"
                ),
                "prior_type": prior_type,
            }
        return prior

    def _build_syndrome_prior(self, syndromes: Dict) -> Dict[str, Dict]:
        """Skill §6.4：构建证型先验（池内均匀先验 prior = 1/N）。

        只在当前 disease_key 的证型池内构建，不跨病名。
        """
        keys = list(syndromes.keys())
        n = len(keys)
        prior: Dict[str, Dict] = {}
        if n == 0:
            return prior
        p = 1.0 / n
        for k in keys:
            prior[k] = {
                "prior": round(p, 4),
                "prior_reason": f"证型池均匀先验 (1/{n})",
            }
        return prior

    def _update_likelihood_from_evidence(self, syndromes: Dict, scorer_result: Optional[Dict],
                                         pathology_result: Optional[Dict],
                                         factor_contradiction_result: Optional[Dict],
                                         cold_heat_resolution: Optional[Dict],
                                         patient_info: Dict) -> Dict[str, Dict]:
        """Skill §6.4：从证据更新各候选证型的似然。

        似然来源：_syndrome_scorer 的 candidate_scores（症状/舌脉/证素似然分）。
        - 否定症状不计为阳性证据（已由 scorer/loader 在上游处理，这里仅作 evidence_against 记录）；
        - 强反证（寒热硬互斥/惩罚证素）降权；
        - 病理裁决方向与候选寒热属性冲突 → 阻断（block）。

        Returns:
            {key: {likelihood, likelihood_evidence, evidence_for, evidence_against,
                   blocked, raw_score}}
        """
        result: Dict[str, Dict] = {}

        score_by_name: Dict[str, Dict] = {}
        for cs in (scorer_result or {}).get("candidate_scores", []):
            score_by_name[cs.get("syndrome_name", "")] = cs

        patho_evidence: Dict[str, Dict] = {}
        for ev in (pathology_result or {}).get("evidence_for", []):
            patho_evidence[ev.get("syndrome", "")] = ev

        strong_against = (factor_contradiction_result or {}).get("strong_against", [])
        main_dir = (cold_heat_resolution or {}).get("main_direction", "")
        neg_evidence = (patient_info.get("symptom_factor_evidence", {}) or {}).get("negative_evidence", [])

        # 似然归一化基准
        max_score = 0.0
        for k, data in syndromes.items():
            disp = self._syndrome_display_name(k, data)
            cs = score_by_name.get(disp) or score_by_name.get(k) or {}
            raw = float(cs.get("score", 0.0))
            if raw > max_score:
                max_score = raw

        for k, data in syndromes.items():
            disp = self._syndrome_display_name(k, data)
            cs = score_by_name.get(disp) or score_by_name.get(k) or {}
            raw_score = float(cs.get("score", 0.0))
            likelihood = (raw_score / max_score) if max_score > 0 else (1.0 / max(len(syndromes), 1))
            likelihood = max(0.0001, likelihood)

            evidence_for = list(cs.get("matched_symptoms", []) or [])
            mtp = cs.get("matched_tongue_pulse", "")
            if mtp:
                evidence_for.append(mtp)
            patho_ev = patho_evidence.get(k) or patho_evidence.get(disp)
            if patho_ev and patho_ev.get("matched_pathology_factors"):
                evidence_for.append("病理:" + "、".join(str(x) for x in patho_ev["matched_pathology_factors"]))

            evidence_against: List[str] = []
            blocked = False
            penalty = 1.0

            trigger = str(data.get("trigger", "")).lower()

            # 强反证：strong_against 证素出现在候选名/trigger → 降权
            for ag in strong_against:
                if isinstance(ag, dict):
                    for f in (ag.get("factor_a", ""), ag.get("factor_b", "")):
                        if f and (f in disp or f in trigger):
                            evidence_against.append(f"强反证证素'{f}'与候选冲突")
                            penalty = min(penalty, 0.3)
                elif isinstance(ag, str) and ag and (ag in disp or ag in trigger):
                    evidence_against.append(f"惩罚证素'{ag}'出现于候选")
                    penalty = min(penalty, 0.2)

            # 寒热病机裁决：方向与候选寒热属性冲突 → 阻断
            is_cold_syn = ("寒" in disp or "风寒" in disp) and ("热" not in disp and "风热" not in disp)
            is_heat_syn = any(kw in disp for kw in ["热", "火", "湿热", "痰热"]) and "寒" not in disp
            if main_dir == "heat" and is_cold_syn:
                evidence_against.append("病理裁决方向=热，本候选为寒证 → 阻断")
                blocked = True
            elif main_dir == "cold" and is_heat_syn:
                evidence_against.append("病理裁决方向=寒，本候选为热证 → 阻断")
                blocked = True

            # contradiction_penalty 作为独立后验因子（不再直接乘入 likelihood），
            # 由 §6.4 adjudicator 在后验融合时单独乘入（避免与因果/反事实因子重复计数）。
            contradiction_penalty = 0.01 if blocked else penalty

            if neg_evidence:
                evidence_against.append(
                    "否定症状不计为阳性证据: " +
                    "、".join(str(n.get("matched_entry", n.get("symptom", ""))) for n in neg_evidence[:3])
                )

            result[k] = {
                # likelihood_component：scorer 似然强度（归一化），仅作后验的一个因子
                "likelihood": round(likelihood, 4),
                "likelihood_evidence": (
                    f"likelihood_component 似然分={raw_score} (症状={cs.get('symptom_match', 0)}, "
                    f"舌脉={cs.get('tongue_match', 0)}/{cs.get('pulse_match', 0)}, "
                    f"病理={cs.get('pathology_match', 0)}, 寒热={cs.get('cold_heat_match', 0)})"
                ),
                "evidence_for": evidence_for[:6],
                "evidence_against": evidence_against[:6],
                "blocked": blocked,
                "contradiction_penalty": round(contradiction_penalty, 4),
                "raw_score": raw_score,
            }
        return result

    def _rank_syndrome_posteriors(self, prior: Dict, likelihood: Dict):
        """Skill §6.4：后验排序 posterior ∝ prior × likelihood，归一化并按后验降序。"""
        unnorm: Dict[str, float] = {}
        for k in prior:
            p = prior[k]["prior"]
            l = likelihood.get(k, {}).get("likelihood", 0.0)
            unnorm[k] = p * l
        total = sum(unnorm.values())
        posteriors = {k: (v / total if total > 0 else 0.0) for k, v in unnorm.items()}
        ranked = sorted(posteriors.items(), key=lambda kv: kv[1], reverse=True)
        return ranked, posteriors

    def _bayesian_syndrome_adjudicator(self, primary_disease: str, disease_key: str,
                                       syndromes: Dict, patient_info: Dict,
                                       scorer_result: Optional[Dict],
                                       pathology_result: Optional[Dict],
                                       factor_contradiction_result: Optional[Dict],
                                       merged: Optional[Dict],
                                       cold_heat_resolution: Optional[Dict] = None) -> Dict:
        """Skill §6.4：贝叶斯辨证裁决（ADDITIVE 层）。

        在当前 disease_key 证型池内：构建先验 → 更新似然 → 后验排序 →
        与双轨合并结果交叉校对 → 输出 confidence_band / posterior_rank / evidence。

        本层提供后验排序与 confidence_band，可降权/阻断强反证候选、防止单一低置信
        候选被伪装为高置信；不破坏既有 selected_syndrome_key（向后兼容）。
        """
        if not syndromes:
            return {
                "method": "causal_bayesian_differentiation",
                "disease_key": disease_key,
                "prior_type": "uniform_fallback",
                "candidates": [],
                "adjudicated_syndrome_key": "",
                "adjudicated_syndrome_name": "",
                "adjudicated_confidence_band": "LOW",
                "adjudication_basis": "empty_pool",
                "selected_syndrome_node": None,
                "top_syndrome_key": "",
                "top_syndrome_name": "",
                "top_confidence_band": "LOW",
                "blocked_syndromes": [],
                "counterfactual_failed_syndromes": [],
                "need_human_review": True,
                "single_low_confidence_not_forced": False,
            }

        # ── 患者证据分层（一次） ──
        patient_findings = self._extract_patient_findings(patient_info)

        # ── 非均匀阶段先验（§6.4.2，禁止仅 1/N） ──
        prior = self._estimate_stage_prior(
            syndromes, disease_key, patient_info, pathology_result,
            merged, cold_heat_resolution, patient_findings,
        )
        prior_type_label = (next(iter(prior.values()), {}).get("prior_type", "stage_weighted")
                            if prior else "uniform_fallback")

        # ── likelihood_component（scorer 似然，仅作一个后验因子，§6.4.3） ──
        likelihood = self._update_likelihood_from_evidence(
            syndromes, scorer_result, pathology_result,
            factor_contradiction_result, cold_heat_resolution, patient_info,
        )

        # ── 每候选：病机因果链 / 因果覆盖度 / 反事实检验（§6.4.1/6.4.3/6.4.4） ──
        causal_chains: Dict[str, Dict] = {}
        coverage_map: Dict[str, float] = {}
        counterfactuals: Dict[str, Dict] = {}
        eff_likelihood: Dict[str, Dict] = {}
        for k, data in syndromes.items():
            disp = self._syndrome_display_name(k, data)
            chain = self._build_causal_chain(k, data, disp, patient_findings, pathology_result)
            coverage = self._calculate_causal_coverage(chain, patient_findings)
            cf = self._run_counterfactual_check(k, data, disp, chain, patient_findings,
                                                cold_heat_resolution)
            causal_chains[k] = chain
            coverage_map[k] = coverage
            counterfactuals[k] = cf

            lk = likelihood.get(k, {})
            base_l = lk.get("likelihood", 0.0001)
            penalty = lk.get("contradiction_penalty", 1.0)
            cf_result = cf["counterfactual_result"]
            cf_factor = {"PASS": 1.0, "QUESTION": 0.5, "FAIL": 0.05}.get(cf_result, 1.0)
            coverage_factor = 0.3 + 0.7 * coverage  # 覆盖度映射为 [0.3, 1.0] 乘子
            # 后验融合：stage_prior × likelihood_component × causal_coverage × contradiction_penalty × counterfactual
            eff = base_l * coverage_factor * penalty * cf_factor
            eff_likelihood[k] = {"likelihood": max(eff, 1e-9)}

        ranked, posteriors = self._rank_syndrome_posteriors(prior, eff_likelihood)

        base_missing = (scorer_result or {}).get("missing_info", "") or ""

        top_key = ranked[0][0] if ranked else ""
        top_prob = ranked[0][1] if ranked else 0.0
        second_prob = ranked[1][1] if len(ranked) > 1 else 0.0
        gap = top_prob - second_prob

        cold_heat_unresolved = bool(
            cold_heat_resolution and
            (cold_heat_resolution.get("main_direction") in ("mixed", "unknown")
             or cold_heat_resolution.get("need_human_review"))
        )
        factor_review = bool(factor_contradiction_result and
                             factor_contradiction_result.get("need_human_review"))

        single_candidate = len(syndromes) == 1

        def _eligible(key: str) -> bool:
            """候选可作为 selected_syndrome_node：未阻断 且 反事实非 FAIL。"""
            if likelihood.get(key, {}).get("blocked", False):
                return False
            if counterfactuals.get(key, {}).get("counterfactual_result") == "FAIL":
                return False
            return True

        def _band_for(key: str, rank: int, prob: float) -> str:
            lk = likelihood.get(key, {})
            blocked = lk.get("blocked", False)
            cf_result = counterfactuals.get(key, {}).get("counterfactual_result", "PASS")
            coverage = coverage_map.get(key, 0.0)
            has_chain = bool(causal_chains.get(key, {}).get("predicted_manifestations"))
            if blocked or cf_result == "FAIL":
                return "LOW"            # 反事实 FAIL/阻断 → 不得高于 LOW
            # 单一低置信不得伪装高置信
            if single_candidate and coverage < 0.3:
                return "LOW"
            if rank != 1:
                if cf_result == "QUESTION":
                    return "LOW"
                return "MEDIUM" if prob >= 0.30 else "LOW"
            # 首位候选
            if cold_heat_unresolved:
                return "LOW"            # 寒热未裁清 → 不得 HIGH
            if cf_result == "QUESTION":
                return "MEDIUM"         # 反事实存疑 → 至多 MEDIUM
            if not has_chain:
                return "MEDIUM"         # 无因果链 → 不得 HIGH
            if factor_review:
                return "MEDIUM"
            if (top_prob >= 0.42 and gap >= 0.12 and coverage >= 0.5):
                return "HIGH"          # HIGH 需反事实 PASS + 因果链 + 因果覆盖度 ≥ 0.5
            if top_prob >= 0.30:
                return "MEDIUM"
            return "LOW"

        candidates = []
        rank_index = 0
        for key, prob in ranked:
            rank_index += 1
            data = syndromes.get(key, {})
            disp = self._syndrome_display_name(key, data)
            lk = likelihood.get(key, {})
            chain = causal_chains.get(key, {})
            # 否定症状不得作为阳性证据：过滤本身以否定前缀开头、或在病情中被否定的项（§6.4.6）
            _ptext = patient_findings["text"]

            def _is_neg_evidence(e: str) -> bool:
                el = str(e).strip().lower()
                if not el:
                    return True
                for np_ in _M2_NEGATION_PREFIXES:
                    if el.startswith(np_):
                        return True
                # 去掉「病理:」前缀后再判断核心词是否被否定
                core = el.split(":", 1)[-1].split("：", 1)[-1]
                return self._m2_is_negated(core, _ptext)

            evidence_for = [e for e in (lk.get("evidence_for", []) or [])
                            if not _is_neg_evidence(e)]
            evidence_against = list(lk.get("evidence_against", []))
            if chain.get("contradicted_findings"):
                evidence_against.append("因果反证: " + "、".join(chain["contradicted_findings"][:4]))
            if chain.get("unexplained_findings"):
                evidence_against.append("未解释关键发现: " + "、".join(chain["unexplained_findings"][:4]))
            # 似然方向：定性 band（§6.4.3，非数字分值）
            p_heat = len(patient_findings.get("defining_heat_findings", []))
            p_cold = len(patient_findings.get("defining_cold_findings", []))
            n_ef = len(evidence_for)
            n_ea = len(evidence_against)
            n_key = max(1, p_heat + p_cold + n_ef + n_ea)
            _key_support = (len(chain.get("observed_support", []))
                            + n_ef - n_ea) / n_key
            if _key_support >= 0.5:
                lh_dir = "STRONG_UP"
            elif _key_support >= 0.2:
                lh_dir = "UP"
            elif _key_support >= -0.2:
                lh_dir = "NEUTRAL"
            elif _key_support >= -0.5:
                lh_dir = "DOWN"
            else:
                lh_dir = "STRONG_DOWN"

            _prior_val = prior.get(key, {}).get("prior", 0.0)
            if _prior_val >= 0.6:
                pb = "HIGH"
            elif _prior_val >= 0.3:
                pb = "MEDIUM"
            else:
                pb = "LOW"

            candidates.append({
                "syndrome_key": key,
                "syndrome_name": disp,
                "prior_band": pb,
                "prior": _prior_val,                           # 向后兼容
                "prior_reason": prior.get(key, {}).get("prior_reason", ""),
                "likelihood_direction": lh_dir,
                "likelihood": lk.get("likelihood", 0.0),        # 向后兼容
                "likelihood_evidence": lk.get("likelihood_evidence", ""),
                "causal_coverage": coverage_map.get(key, 0.0),
                "causal_chain": chain,
                "counterfactual_check": counterfactuals.get(key, {}),
                "posterior_band": _band_for(key, rank_index, prob),
                "posterior": round(prob, 4),                    # 向后兼容
                "posterior_rank": rank_index,
                "confidence_band": _band_for(key, rank_index, prob),  # 向后兼容
                "evidence_for": evidence_for,
                "evidence_against": evidence_against[:8],
                "missing_info": base_missing,
                "blocked": lk.get("blocked", False),
                "eligible": _eligible(key),
            })

        # ── 裁决：selected_syndrome_node 由因果贝叶斯裁决器输出（§6.4 + 7） ──
        eligible_ranked = [c for c in candidates if c["eligible"]]
        merged_key = (merged or {}).get("selected_syndrome_key", "")
        merged_in_pool = merged_key in syndromes
        prelim_eligible = merged_in_pool and _eligible(merged_key)

        adjudication_basis = ""
        if prelim_eligible:
            # 双轨初选通过因果链 + 反事实检验（未 FAIL/未阻断）→ 裁决器确认（向后兼容）。
            adjudicated_key = merged_key
            adjudication_basis = "confirm_dual_track"
        elif eligible_ranked:
            # 双轨初选被反事实 FAIL/强反证阻断排除 → 改选后验最高的可用候选（因果改道）
            adjudicated_key = eligible_ranked[0]["syndrome_key"]
            adjudication_basis = "switch_to_best_eligible_after_counterfactual"
        else:
            # 无可用候选（全部 FAIL/阻断）→ 保留后验首位但强制低置信 + 复核
            adjudicated_key = top_key
            adjudication_basis = "no_eligible_candidate_low_confidence"

        adjudicated = next((c for c in candidates if c["syndrome_key"] == adjudicated_key), None)
        adjudicated_band = adjudicated["posterior_band"] if adjudicated else "LOW"
        # 单候选低覆盖度不得强制高置信（§6.4.6）
        single_low = single_candidate and (adjudicated or {}).get("causal_coverage", 0.0) < 0.3
        no_eligible = not eligible_ranked
        if single_low or no_eligible:
            adjudicated_band = "LOW"

        top_band = candidates[0]["confidence_band"] if candidates else "LOW"
        blocked_syndromes = [c["syndrome_key"] for c in candidates if c["blocked"]]
        counterfactual_failed = [c["syndrome_key"] for c in candidates
                                 if c["counterfactual_check"].get("counterfactual_result") == "FAIL"]
        need_human_review = bool(cold_heat_unresolved or single_low or no_eligible
                                 or adjudication_basis == "switch_to_best_eligible_after_counterfactual"
                                 or adjudication_basis == "no_eligible_candidate_low_confidence")

        selected_node = None
        if adjudicated:
            selected_node = {
                "syndrome_key": adjudicated_key,
                "syndrome_name": adjudicated["syndrome_name"],
                "prior_band": adjudicated["prior_band"],
                "posterior_band": adjudicated["posterior_band"],
                "likelihood_direction": adjudicated["likelihood_direction"],
                "confidence_band": adjudicated_band,
                "posterior": adjudicated["posterior"],
                "posterior_rank": adjudicated["posterior_rank"],
                "causal_coverage": adjudicated["causal_coverage"],
                "causal_chain": adjudicated["causal_chain"],
                "counterfactual_check": adjudicated["counterfactual_check"],
                "adjudication_basis": adjudication_basis,
                "adjudicated_by": "causal_bayesian_adjudicator",
            }

        return {
            "method": "causal_bayesian_differentiation",
            "disease_key": disease_key,
            "prior_type": prior_type_label,
            "posterior_fusion": ("stage_prior × likelihood_component × causal_coverage "
                                 "× contradiction_penalty × counterfactual_result"),
            "candidates": candidates[:8],
            "adjudicated_syndrome_key": adjudicated_key,
            "adjudicated_syndrome_name": (adjudicated["syndrome_name"] if adjudicated else ""),
            "adjudicated_confidence_band": adjudicated_band,
            "adjudication_basis": adjudication_basis,
            "selected_syndrome_node": selected_node,
            "top_syndrome_key": top_key,
            "top_syndrome_name": candidates[0]["syndrome_name"] if candidates else "",
            "top_confidence_band": top_band,
            "blocked_syndromes": blocked_syndromes,
            "counterfactual_failed_syndromes": counterfactual_failed,
            "need_human_review": need_human_review,
            "single_low_confidence_not_forced": single_low,
            "cross_check_with_dual_track": {
                "dual_track_status": (merged or {}).get("status", "SINGLE_TRACK"),
                "dual_track_selected": merged_key,
                "bayesian_top": top_key,
                "adjudicated": adjudicated_key,
                "agreement": merged_key == adjudicated_key,
            },
        }

    # ══════════════════════════════════════════════════════
    #  受约束 LLM 复核 (Skill §6.5) — bias-checker，非自由辨证器
    # ══════════════════════════════════════════════════════

    def _llm_sanity_check_syndrome_result(self, primary_disease: str, disease_key: str,
                                          syndromes: Dict,
                                          pathology_based_result: Optional[Dict],
                                          traditional_tcm_result: Optional[Dict],
                                          bayesian_result: Optional[Dict],
                                          selected_syndrome_key: str,
                                          bound_formula_name: str = "",
                                          factor_contradiction_result: Optional[Dict] = None,
                                          patient_info: Optional[Dict] = None) -> Dict:
        """Skill §6.5：受约束 LLM 贝叶斯式辨证鉴别复核（受约束偏差检查器，不自由辨证）。

        - M2_DISABLE_LLM=1 或无 DEEPSEEK_API_KEY：跳过，稳定返回本地结果
          (llm_review_result=ACCEPT, llm_called=False)，不发起任何 LLM/router 调用（§6.6）。
          §6.5.3 新增的 posterior_ranking/key_pivot_findings/adjacent_differentiation
          离线时以空列表占位（schema 一致，向后兼容）。
        - 实际调用时：LLM 产出富文本贝叶斯鉴别 + 末尾机读 JSON；代码只解析末尾 JSON 块，
          校验 preferred_syndrome_from_pool / posterior_ranking 必须在当前池内；
          LLM 不得覆盖硬约束、不得自造证型/方剂；REJECT 仅可在池内且通过本地硬约束时改选。
        """
        local_pool = list(syndromes.keys())
        local_pool_display = [self._syndrome_display_name(k, syndromes[k]) for k in local_pool]

        offline_result = {
            "llm_review_result": "ACCEPT",
            "preferred_syndrome_from_pool": "",
            "reason": "M2_DISABLE_LLM 或无 DEEPSEEK_API_KEY：跳过 LLM 复核，保留本地贝叶斯结果",
            "detected_bias": [],
            "missing_evidence": [],
            "must_not_select": [],
            # §6.5.3 新增字段（离线占位，仅真实 LLM 可用时由 LLM 产出）
            "posterior_ranking": [],
            "key_pivot_findings": [],
            "adjacent_differentiation": [],
            "llm_called": False,
            "selected_syndrome_key": selected_syndrome_key,
            "applied_action": "keep_local",
        }

        if not self._llm_available():
            return offline_result

        prompt = self._build_llm_sanity_prompt(
            primary_disease, disease_key, syndromes,
            pathology_based_result, traditional_tcm_result, bayesian_result,
            selected_syndrome_key, bound_formula_name, factor_contradiction_result,
            patient_info or {},
        )
        raw = self._call_llm(prompt)
        if not raw:
            res = dict(offline_result)
            res.update({"reason": "LLM 无返回，保留本地结果", "llm_called": True})
            return res

        parsed = self._parse_llm_sanity_result(raw)
        if not parsed:
            res = dict(offline_result)
            res.update({"reason": "LLM 输出解析失败，保留本地结果", "llm_called": True})
            return res

        parsed["llm_called"] = True
        parsed.setdefault("detected_bias", [])
        parsed.setdefault("missing_evidence", [])
        parsed.setdefault("must_not_select", [])
        parsed.setdefault("posterior_ranking", [])
        parsed.setdefault("key_pivot_findings", [])
        parsed.setdefault("adjacent_differentiation", [])
        review = parsed.get("llm_review_result", "ACCEPT")
        preferred = parsed.get("preferred_syndrome_from_pool", "")

        # 越池校验：preferred 必须在当前 disease_key 池内
        if preferred and preferred not in local_pool_display and preferred not in local_pool:
            parsed["detected_bias"].append(
                f"LLM 越池建议'{preferred}'已被忽略（不在当前 disease_key 池）")
            preferred = ""
            parsed["preferred_syndrome_from_pool"] = ""

        # 越池校验：posterior_ranking 仅保留池内证型（防止 LLM 越池/造证型）
        _pool_set = set(local_pool) | set(local_pool_display)
        _clean_ranking = []
        for item in parsed.get("posterior_ranking", []):
            if isinstance(item, dict) and item.get("syndrome") in _pool_set:
                if str(item.get("possibility_change", "")) in ("↑↑", "↑", "→", "↓", "↓↓"):
                    _clean_ranking.append(item)
                else:
                    item["possibility_change"] = "→"
                    _clean_ranking.append(item)
            elif isinstance(item, dict) and item.get("syndrome"):
                parsed["detected_bias"].append(
                    f"LLM posterior_ranking 越池项'{item.get('syndrome')}'已被忽略")
        parsed["posterior_ranking"] = _clean_ranking

        if review == "ACCEPT":
            parsed["selected_syndrome_key"] = selected_syndrome_key
            parsed["applied_action"] = "keep_local"
        elif review == "QUESTION":
            parsed["selected_syndrome_key"] = selected_syndrome_key
            parsed["applied_action"] = "keep_local_lower_confidence"
        elif review == "REJECT":
            alt_key = ""
            if preferred:
                for k in local_pool:
                    if self._syndrome_display_name(k, syndromes[k]) == preferred or k == preferred:
                        alt_key = k
                        break
            blocked = set((bayesian_result or {}).get("blocked_syndromes", []))
            if alt_key and alt_key not in blocked:
                parsed["selected_syndrome_key"] = alt_key
                parsed["applied_action"] = "llm_reject_switch_within_pool"
            else:
                # 本地硬约束不支持替代 → LOW_CONFIDENCE
                parsed["selected_syndrome_key"] = selected_syndrome_key
                parsed["applied_action"] = "reject_but_no_valid_alternative_low_confidence"
                parsed["llm_review_result"] = "REJECT_LOW_CONFIDENCE"
        else:
            parsed["selected_syndrome_key"] = selected_syndrome_key
            parsed["applied_action"] = "keep_local"
        return parsed

    def _load_bayesian_diff_template(self) -> str:
        """加载 §6.5 贝叶斯式辨证鉴别 prompt 母版（prompts/m2/m2_bayesian_differentiation.md）。

        母版缺失时退回内联精简框架，保证离线/异常环境仍可构造可用 prompt。结果在实例上缓存。
        """
        cached = getattr(self, "_bayesian_diff_template", None)
        if cached is not None:
            return cached
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "prompts", "m2", "m2_bayesian_differentiation.md")
        template = ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception:
            template = (
                "你是中医临床辅助辨证推理助手。在已知西医病名、当前病程阶段、候选证型范围和核对表"
                "阳性项的基础上，进行『贝叶斯式辨证鉴别』：不要按阳性项数量判断、不要积分制、不要"
                "编造精确概率（仅用 ↑↑/↑/→/↓/↓↓ 定性更新）；只在候选证型池内辨证；识别关键改道"
                "表现；做相邻证型鉴别；最后给出最能解释病机链的证型。你是受约束的偏差检查器，"
                "不得越池、不得自造方剂/药物/剂量/用法/疗程。"
            )
        self._bayesian_diff_template = template
        return template

    def _build_llm_sanity_prompt(self, primary_disease, disease_key, syndromes,
                                 pathology_result, tcm_result, bayesian_result,
                                 selected_syndrome_key, bound_formula_name,
                                 factor_contradiction_result, patient_info) -> str:
        """构造受约束 LLM 贝叶斯式辨证鉴别 prompt（Skill §6.5 输入/输出契约）。

        组成：母版框架（prompts/m2/m2_bayesian_differentiation.md）
            + 运行期上下文（病名/病程阶段/完整病情原文/候选池含核对表阳性项与本地后验）
            + 末尾机读 JSON 契约。
        """
        patient_info = patient_info or {}
        template = self._load_bayesian_diff_template()

        # 候选池（含 trigger 鉴别要点 + 核对表阳性项[evidence_for] + 反证[evidence_against] + 本地后验）
        bayes_by_key = {c.get("syndrome_key"): c for c in (bayesian_result or {}).get("candidates", [])}
        pool_lines = []
        pool_names = []
        for k, data in (syndromes or {}).items():
            disp = self._syndrome_display_name(k, data)
            pool_names.append(disp)
            bc = bayes_by_key.get(k, {})
            trigger = str(data.get("trigger", "")).strip()
            pos = "、".join(str(x) for x in bc.get("evidence_for", [])) or "（无）"
            neg = "、".join(str(x) for x in bc.get("evidence_against", [])) or "（无）"
            _chain = bc.get("causal_chain", {}) or {}
            _cf = bc.get("counterfactual_check", {}) or {}
            pool_lines.append(
                f"- 证型【{disp}】(key={k})\n"
                f"    鉴别要点/trigger: {trigger}\n"
                f"    核对表阳性项: {pos}\n"
                f"    反证/否定项: {neg}\n"
                f"    病机因果链: {_chain.get('mechanism', '（无）')}\n"
                f"    因果覆盖度: {bc.get('causal_coverage')} "
                f"未解释关键项: {('、'.join(_chain.get('unexplained_findings', [])) or '（无）')} "
                f"被反驳项: {('、'.join(_chain.get('contradicted_findings', [])) or '（无）')}\n"
                f"    反事实检验: {_cf.get('counterfactual_result', 'N/A')} "
                f"(违例:{('、'.join(_cf.get('violated_expectations', [])) or '无')})\n"
                f"    本地后验: rank={bc.get('posterior_rank')} "
                f"band={bc.get('confidence_band')} blocked={bc.get('blocked')}"
            )
        pool_block = "\n".join(pool_lines) if pool_lines else "（证型池为空）"

        # 完整病情原文（不重新抽取证据原子，保留语境）
        def _fmt(v):
            if isinstance(v, (list, tuple)):
                return "、".join(str(x) for x in v if str(x).strip()) or "（无）"
            return str(v).strip() or "（无）"
        case_block = (
            f"症状: {_fmt(patient_info.get('symptoms'))}\n"
            f"体征: {_fmt(patient_info.get('signs'))}\n"
            f"舌象: {_fmt(patient_info.get('tongue'))}\n"
            f"脉象: {_fmt(patient_info.get('pulse'))}\n"
            f"寒热: {_fmt(patient_info.get('cold_heat'))}\n"
            f"二便: {_fmt(patient_info.get('stool_urine'))}\n"
            f"实验室: {_fmt(patient_info.get('labs'))}\n"
            f"影像: {_fmt(patient_info.get('imaging'))}\n"
            f"年龄: {_fmt(patient_info.get('age'))}"
        )

        stage = ""
        if pathology_result:
            stage = (pathology_result.get("pathology_stage")
                     or pathology_result.get("inferred_pathology_stage")
                     or pathology_result.get("disease_nature") or "")

        contract = (
            '{"llm_review_result": "ACCEPT|QUESTION|REJECT", '
            '"preferred_syndrome_from_pool": "", "reason": "", '
            '"detected_bias": [], "missing_evidence": [], "must_not_select": [], '
            '"posterior_ranking": [{"syndrome": "", "possibility_change": "↑↑|↑|→|↓|↓↓", '
            '"confidence_band": "HIGH|MEDIUM|LOW"}], '
            '"key_pivot_findings": [], '
            '"adjacent_differentiation": [{"pair": ["", ""], "key_difference": "", '
            '"current_supports": "", "why_not_other": ""}]}'
        )

        return f"""{template}

────────────────────────── 运行期上下文 ──────────────────────────
## 1. 西医病名 / 病程阶段
西医主病名: {primary_disease}
disease_key: {disease_key}
当前病程/病理阶段: {stage or "（未明确，请结合病情判断）"}

## 2. 完整病情原文（请整体阅读，禁止只看核对表）
{case_block}

## 3. 当前 disease_key 候选证型池（只能在此池内辨证；含核对表阳性项与本地后验）
{pool_block}

## 4. 本地双轨/裁决参考（用于偏差校验，不得替代完整病情）
病理轨结果: {json.dumps(pathology_result or {}, ensure_ascii=False)[:600]}
证素轨候选: {json.dumps((tcm_result or {}).get('candidate_scores', [])[:3], ensure_ascii=False)[:500]}
证素互斥结果: {json.dumps(factor_contradiction_result or {}, ensure_ascii=False)[:500]}
本地选中证型(selected_syndrome_node): {selected_syndrome_key}
节点绑定方剂(只读，不得修改/不得输出剂量): {bound_formula_name}

## 5. 因果复核要点（§6.5.0，必须核对）
- 选中证型的【病机因果链】是否成立（病机能否解释当前病程/症状/舌脉/实验室）？
- 同池内是否存在【更能完整解释整体病情】的证型？
- 是否存在【关键未解释症状】（见各候选 未解释关键项）？
- 选中证型的【反事实检验】是否 FAIL（FAIL 者本地已不得选中，请据此校验）？
- 节点绑定方剂是否【自然由该证型节点推导】而来？
（你不得跨 disease_key、不得自造证型/方剂/剂量、不得覆盖本地 block / 反事实 FAIL / 寒热裁决）

────────────────────────── 输出要求 ──────────────────────────
先输出【富文本贝叶斯鉴别】（输出格式 6 节），然后在**最后**仅输出**一个** JSON 代码块
（其余说明放在 JSON 之前）。JSON 必须为以下结构，且 preferred_syndrome_from_pool 与
posterior_ranking[].syndrome 只能取自上面证型池：

```json
{contract}
```"""

    def _parse_llm_sanity_result(self, text: str) -> Optional[Dict]:
        """解析受约束 LLM 复核【末尾机读 JSON 块】，容忍其前的任意叙述文本。

        策略（鲁棒）：
        1. 优先提取 ```json ... ``` / ``` ... ``` 围栏内的对象；
        2. 否则从文本中扫描所有平衡花括号对象；
        3. 取**最后一个**含 llm_review_result 的合法 JSON 对象（贝叶斯叙述在前、JSON 在末尾）；
        4. 校验 llm_review_result 合法，非法则归一为 ACCEPT。
        """
        if not text:
            return None

        candidates: List[str] = []
        # 1. 围栏代码块
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE):
            candidates.append(m.group(1))
        # 2. 平衡花括号扫描（覆盖无围栏情形）
        candidates.extend(self._extract_balanced_json_objects(text))

        parsed_obj: Optional[Dict] = None
        for chunk in candidates:  # 保留出现顺序，最后一个合法对象覆盖前者 → 取末尾 JSON
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "llm_review_result" in obj:
                parsed_obj = obj

        if parsed_obj is None:
            return None

        review = str(parsed_obj.get("llm_review_result", "")).upper()
        if review not in ("ACCEPT", "QUESTION", "REJECT"):
            review = "ACCEPT"
        parsed_obj["llm_review_result"] = review
        return parsed_obj

    @staticmethod
    def _extract_balanced_json_objects(text: str) -> List[str]:
        """扫描文本中所有顶层平衡的 {...} 子串（按出现顺序），用于鲁棒提取末尾 JSON 块。"""
        objects: List[str] = []
        depth = 0
        start = -1
        in_str = False
        escape = False
        quote = ""
        for i, ch in enumerate(text):
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in ('"', "'"):
                in_str = True
                quote = ch
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        objects.append(text[start:i + 1])
                        start = -1
        return objects

# ══════════════════════════════════════════════════════
#  M2+M3 串联全流程
# ══════════════════════════════════════════════════════


    def full_pipeline(self, primary_disease: str, patient_info: dict) -> Dict:
        """M2+M3 串联全流程：辨证 -> 加减 -> M3审核 -> 最终处方
        patient_info 支持字段:
            symptoms, signs, tongue, pulse, cold_heat, stool_urine,
            sleep, appetite, labs, imaging, age, weight,
            pregnancy (bool), lactation (bool)
        """
        if os.getenv("ALLOW_LEGACY_M2_FULL_PIPELINE", "false").lower() != "true":
            return {
                "pipeline": "m2+m3_full",
                "status": "BLOCKED",
                "legacy_prescription_path_blocked": True,
                "candidate_only": True,
                "must_enter_m3": True,
                "trace_closure_required": True,
                "formal_prescription_allowed": False,
                "blocked_reason": [
                    "legacy_full_pipeline_contains_final_prescription_and_dosage",
                    "formal_gate_paused",
                ],
            }
        # ── 1. M2 辨证选方 ──
        m2_result = self.process(
            primary_disease=primary_disease,
            symptoms=patient_info.get("symptoms"),
            signs=patient_info.get("signs"),
            tongue=patient_info.get("tongue", ""),
            pulse=patient_info.get("pulse", ""),
            cold_heat=patient_info.get("cold_heat"),
            stool_urine=patient_info.get("stool_urine"),
            sleep=patient_info.get("sleep"),
            appetite=patient_info.get("appetite"),
            labs=patient_info.get("labs"),
            imaging=patient_info.get("imaging"),
            age=patient_info.get("age", ""),
            weight=patient_info.get("weight", ""),
        )

        if m2_result.get("error"):
            return m2_result

        base_herbs = m2_result["formula"]["herbs"]

        # ── 2. 基于症状 + 药理库进行加减 ──
        modifications = self._generate_evidence_based_modifications(
            base_herbs, patient_info.get("symptoms", []) or [],
        )

        final_herbs = list(base_herbs)
        for m in modifications:
            if m["herb"] not in final_herbs:
                final_herbs.append(m["herb"])

        # ── 3. M3 安全审核 ──
        from m3_engine import M3ClinicalReviewEngine
        reviewer = M3ClinicalReviewEngine()
        m3_result = reviewer.review(final_herbs, {
            "age": patient_info.get("age", 0),
            "weight": patient_info.get("weight", 0),
            "pregnancy": patient_info.get("pregnancy", False),
            "lactation": patient_info.get("lactation", False),
        })

        # ── 4. 提取剂量 ──
        dosages = self._extract_dosages(base_herbs, primary_disease)

        # ── 5. 组装最终处方 ──
        final_herbs_dosage = {}
        for h in base_herbs:
            final_herbs_dosage[h] = dosages.get(h, "常规剂量")
        for m in modifications:
            if m["herb"] not in final_herbs_dosage:
                final_herbs_dosage[m["herb"]] = m.get("dosage", "常规剂量")

        warnings = list(m3_result.get("warnings", []))
        for t in m3_result.get("toxicity_warnings", []):
            note = t.get("note", "")
            if note:
                warnings.append(note)

        return {
            "pipeline": "m2+m3_full",
            "patient": {
                "age": patient_info.get("age", ""),
                "diagnosis": primary_disease,
                "tongue": patient_info.get("tongue", ""),
                "pulse": patient_info.get("pulse", ""),
            },
            "syndrome_differentiation": m2_result.get("syndrome_differentiation", {}),
            "base_formula": {
                "name": m2_result["formula"]["name"],
                "herbs_with_dosage": {h: dosages.get(h, "常规剂量") for h in base_herbs},
                "source": m2_result["formula"]["source"],
            },
            "modifications": modifications,
            "final_prescription": {
                "herbs_with_dosage": final_herbs_dosage,
                "total_herbs": len(final_herbs),
            },
            "m3_review": {
                "eighteen_opposites": m3_result.get("eighteen_opposites", []),
                "nineteen_fears": m3_result.get("nineteen_fears", []),
                "toxicity_warnings": m3_result.get("toxicity_warnings", []),
                "dose_adjustments": m3_result.get("dose_adjustments", []),
                "special_population": m3_result.get("special_population", []),
                "warnings": warnings,
                "review_decision": m3_result.get("review_decision", "APPROVED"),
            },
            "case_references": m2_result.get("case_references", []),
            "missing_info": m2_result.get("missing_info", []),
        }

    def _generate_evidence_based_modifications(self, base_herbs: list, patient_symptoms: list) -> list:
        """基于证据的加减药物生成（症状 -> 药效分类匹配）
        规则：
        - 加减药从预定义的 SYMPTOM_TO_HERB_CANDIDATES 中选取
        - 不重复、不在 base_herbs 中
        - 最多 3 味
        - 不创造新药物
        """
        SYMPTOM_TO_HERB_CANDIDATES = {
            "口干口苦": [
                {"herb": "黄连", "dosage": "6g", "reason": "清胃热、燥湿，对口干口苦、苔黄腻效佳", "class": "清热燥湿药"},
                {"herb": "黄芩", "dosage": "9g", "reason": "清上焦热，助解口干口苦", "class": "清热燥湿药"},
            ],
            "烧心": [
                {"herb": "黄连", "dosage": "6g", "reason": "清胃热，抑制胃酸，缓解烧心", "class": "清热燥湿药"},
                {"herb": "煅瓦楞子", "dosage": "15g(先煎)", "reason": "制酸止痛，缓解烧心", "class": "制酸药"},
            ],
            "大便不畅": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，通腹导滞", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气燥湿，消胀通便", "class": "化湿药"},
            ],
            "排气多": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消胀，减少排气", "class": "理气药"},
                {"herb": "木香", "dosage": "6g", "reason": "行气止痛，调中导滞", "class": "理气药"},
            ],
            "饱胀": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，针对饱胀堵闷", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气除满，消胀", "class": "化湿药"},
                {"herb": "砂仁", "dosage": "6g(后下)", "reason": "化湿开胃，行气宽中", "class": "化湿药"},
            ],
            "堵闷": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，针对堵闷不适", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气除满", "class": "化湿药"},
            ],
        }

        selected = []
        seen_herbs = set(base_herbs)

        for symptom, candidates in SYMPTOM_TO_HERB_CANDIDATES.items():
            for ps in patient_symptoms:
                if symptom not in ps and ps not in symptom:
                    continue
                for c in candidates:
                    if c["herb"] in seen_herbs:
                        continue
                    seen_herbs.add(c["herb"])
                    selected.append({
                        "herb": c["herb"],
                        "dosage": c.get("dosage", "常规剂量"),
                        "action": "add",
                        "reason": c["reason"],
                        "matched_symptom": symptom,
                    })
                    break  # 每个症状只加一味药
                break

        return selected[:3]

    def _extract_dosages(self, herbs: list, primary_disease: str) -> dict:
        """从知识库 full_decoction 中提取药物剂量"""
        if os.getenv("ALLOW_LEGACY_DOSAGE_EXTRACTION", "false").lower() != "true":
            return {
                "_blocked": True,
                "legacy_prescription_path_blocked": True,
                "formal_prescription_allowed": False,
            }
        dosages = {}
        disease_data = self.kb.get(primary_disease, {})
        if not disease_data:
            return {h: "常规剂量" for h in herbs}

        import re
        for syndrome_data in disease_data.get("syndromes", {}).values():
            full = syndrome_data.get("full_decoction", "")
            if not full:
                continue
            for h in herbs:
                if h in dosages:
                    continue
                m = re.search(re.escape(h) + r"(\\d+[gG])", full)
                if m:
                    dosages[h] = m.group(1)

        for h in herbs:
            if h not in dosages:
                dosages[h] = "常规剂量"
        return dosages

    def _auto_fill_herbs_from_decoction(self):
        """加载知识库时，从 full_decoction 自动补全缺失的药物

        规则：
        - 如果 full_decoction 中存在药物但 herbs 字段缺失，自动补全
        - 使用提取的基名（去炮制前缀）
        - 仅在初始化时执行一次，不修改磁盘文件
        - 防止 future 新增知识库数据时 herbs 字段不全
        """
        import re

        non_herb_keywords = {
            '方剂', '用药', '疗程', '疗效',
            '安全', '警示', '注意', '建议',
            '加减', '预估', '起始', '每日',
            '水炖', '温服', '饭后', '饭前',
            '情况', '缓解', '改善', '消失',
            '恢复', '跟踪', '方法', '用法',
        }

        def _extract(full_text: str) -> list:
            pattern = re.compile(r'([一-鿿]{2,4})(?:\d+\.?\d*[gG])')
            seen = set()
            result = []
            for m in pattern.finditer(full_text):
                herb = m.group(1)
                for prefix in ['麟炒', '醋', '酒', '盐',
                               '蜜', '姜', '炙', '炒',
                               '熅', '熈', '焦', '生']:
                    if herb.startswith(prefix) and len(herb) > len(prefix):
                        herb = herb[len(prefix):]
                        break
                if herb in non_herb_keywords:
                    continue
                if herb not in seen:
                    seen.add(herb)
                    result.append(herb)
            return result

        for disease_data in self.kb.values():
            if not isinstance(disease_data, dict):
                continue
            syndromes = disease_data.get('syndromes', {})
            if not syndromes:
                continue
            for syndrome_data in syndromes.values():
                if not isinstance(syndrome_data, dict):
                    continue
                full = syndrome_data.get('full_decoction', '')
                current = syndrome_data.get('herbs', [])
                if not full or not current:
                    continue
                extracted = _extract(full)
                if len(extracted) > len(current):
                    # 补全：保留已有药物顺序，在后面追加缺失的
                    current_set = set(current)
                    additions = [h for h in extracted if h not in current_set]
                    if additions:
                        syndrome_data['herbs'] = current + additions



    # ══════════════════════════════════════════════════════
    #  命令行入口
    # ══════════════════════════════════════════════════════



if __name__ == "__main__":
    import os
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-e1ec28ff01d946518d033daa91fcd222")

    selector = M2SyndromeSelector()

    # 测试用例：儿童急性扁桃体炎
    result = selector.process(
        primary_disease="儿童急性扁桃体炎",
        symptoms=["发热", "咽喉疼痛", "吞咽不利", "鼻塞流涕"],
        tongue="舌质红，苔薄白",
        pulse="脉浮数",
        cold_heat=["发热", "恶风"],
        age="5岁",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 测试串联流程
    result2 = selector.full_pipeline(
        primary_disease="慢性胃炎 (Chronic Gastritis)",
        patient_info={
            "symptoms": ["胃脘不适", "剑突处饱胀感", "堵闷不适", "酒后烧心", "排气多", "口干口苦", "大便不畅"],
            "tongue": "舌红，苔黄腻偏黑",
            "pulse": "",
            "cold_heat": ["无寒热"],
            "stool_urine": ["大便不畅"],
            "age": 51,
            "weight": 70,
            "pregnancy": False,
            "lactation": False,
        }
    )
    print(json.dumps(result2, ensure_ascii=False, indent=2))
