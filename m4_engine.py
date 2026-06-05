"""
M4 复诊路由分发模块
====================
职责：
1. 症状对比 → 计算改善系数（代码执行）
2. 病程窗口判断 → 从知识库读取（代码执行）
3. 病名校验 → LLM 辅助判断（代码框架 + LLM 兜底）
4. 路由决策 → 综合前三步结果（代码执行）
5. 危险信号检测 → 代码执行（阈值判断）

数据来源：
- 疾病病程窗口与核心症状：从 m2_formula_knowledge.json 提取（通过 m4_kb_mapper）
- 当知识库加载失败或无该疾病时，自动回退到硬编码兜底数据
"""
import json
import re
from typing import Dict, List, Optional, Tuple

# ── 危险信号的阈值表 ─────────────
DANGER_THRESHOLDS = {
    "sbp_high": 180,  # 收缩压 ≥ 180
    "sbp_low": 85,    # 收缩压 ≤ 85（出血性休克风险）
    "hr_high": 130,   # 心率 ≥ 130
    "hr_low": 45,     # 心率 ≤ 45
    "temp_high": 40,  # 体温 ≥ 40
    "spo2_low": 90,   # 血氧 ≤ 90
}

DANGER_KEYWORDS = [
    "急性胸痛", "意识改变", "意识下降", "大出血",
    "重度呼吸困难", "呼吸困难", "胸痛",
    "气促", "夜间气促", "喘憋", "发热加重",
    "大量出血", "大出血", "阴道大量出血",
    "吐血", "咯血", "便血",
]

# ── 硬编码兜底窗口（当知识库无此疾病时使用） ─────────────
LEGACY_DISEASE_WINDOWS = {
    "上呼吸道感染": {"onset": 3, "significant": 7},
    "急性支气管炎": {"onset": 5, "significant": 10},
    "急性咽炎": {"onset": 3, "significant": 7},
    "急性扁桃体炎": {"onset": 3, "significant": 7},
    "睑缘炎": {"onset": 5, "significant": 10},
    "睑腺炎": {"onset": 5, "significant": 10},
    "流行性感冒": {"onset": 3, "significant": 7},
    "社区获得性肺炎": {"onset": 5, "significant": 14},
    "咳嗽": {"onset": 7, "significant": 14},
    "慢性咳嗽": {"onset": 7, "significant": 14},
    "亚急性咳嗽": {"onset": 5, "significant": 14},
    "失眠": {"onset": 14, "significant": 30},
    "高血压": {"onset": 14, "significant": 30},
    "糖尿病": {"onset": 30, "significant": 60},
    "湿疹": {"onset": 14, "significant": 28},
    "荨麻疹": {"onset": 7, "significant": 14},
    "过敏性鼻炎": {"onset": 7, "significant": 21},
    "头痛": {"onset": 7, "significant": 14},
    "偏头痛": {"onset": 7, "significant": 14},
    "腰痛": {"onset": 7, "significant": 21},
    "颈椎病": {"onset": 14, "significant": 30},
    "膝关节炎": {"onset": 14, "significant": 30},
    "腹泻": {"onset": 3, "significant": 7},
    "功能性消化不良": {"onset": 7, "significant": 21},
    "便秘": {"onset": 7, "significant": 21},
    "慢性胃炎": {"onset": 14, "significant": 28},
    "胃食管反流": {"onset": 7, "significant": 21},
    "焦虑": {"onset": 14, "significant": 30},
    "抑郁": {"onset": 14, "significant": 42},
}

# ── 硬编码兜底核心症状（当知识库无此疾病时使用） ─────────────
LEGACY_CORE_SYMPTOMS = {
    "睑缘炎": ["眼睑红肿", "眼痒", "眼痛", "眼睑充血"],
    "睑腺炎": ["眼睑红肿", "眼部疼痛", "眼睑结节", "眼睑压痛"],
    "上呼吸道感染": ["咳嗽", "流鼻涕", "咽痛", "发热", "鼻塞"],
    "急性支气管炎": ["咳嗽", "咳痰", "胸闷", "气促"],
    "社区获得性肺炎": ["发热", "咳嗽", "咳痰", "呼吸困难", "胸痛"],
    "高血压": ["头晕", "头痛", "颈项强", "耳鸣", "心悸"],
    "糖尿病": ["口渴", "多饮", "多尿", "消瘦", "疲乏"],
    "失眠": ["入睡困难", "多梦", "早醒", "睡眠浅", "白天困倦"],
    "湿疹": ["皮疹", "瘙痒", "红斑", "渗液", "皮肤干燥"],
    "荨麻疹": ["风团", "瘙痒", "红斑", "突然发作"],
    "慢性胃炎": ["胃痛", "腹胀", "反酸", "嗳气", "食欲减退"],
    "功能性消化不良": ["腹胀", "早饱", "嗳气", "胃痛", "恶心"],
    "腹泻": ["腹泻", "腹痛", "大便稀", "次数增多"],
    "便秘": ["便秘", "排便困难", "大便干结", "排便次数减少"],
    "咳嗽": ["咳嗽", "咳痰", "咽痒"],
    "偏头痛": ["头痛", "恶心", "畏光", "畏声", "搏动性头痛"],
    "腰痛": ["腰痛", "活动受限", "久坐加重"],
    "颈椎病": ["颈痛", "肩膀痛", "头晕", "手麻"],
    "过敏性鼻炎": ["打喷嚏", "流清涕", "鼻塞", "鼻痒"],
    "焦虑": ["紧张", "焦虑", "心悸", "失眠", "坐立不安"],
    "抑郁": ["情绪低落", "兴趣减退", "疲乏", "失眠", "食欲减退"],
}

# ── 默认窗口 ─────────────
DEFAULT_WINDOW = {"onset": 7, "significant": 14}


class M4RoutingEngine:
    """M4 复诊路由分发引擎

    数据来源优先级（逐级回退）：
    1. 预后文件（m4_disease_course_prognosis_window, 450个疾病专家卡片）
       → 提供三层窗口（首次/主症/稳定）和疾病定制路由策略
    2. 知识库 m2_formula_knowledge.json（625 个疾病，通过 m4_kb_mapper 规则推算）
       → 提供窗口和核心症状
    3. 硬编码兜底数据（LEGACY_* 常量）
       → 提供常见病的窗口和核心症状
    """

    def __init__(self, kb_path: str = "data/m2_formula_knowledge.json",
                 prognosis_path: str = None):
        """初始化

        Args:
            kb_path: 知识库路径。如果提供，会从知识库动态加载
                     疾病窗口和核心症状数据。
                     如果无法加载或疾病不在知识库中，自动回退到硬编码数据。
            prognosis_path: 预后文件路径（m4_disease_course_prognosis_window）。
                            如果不指定，会自动搜索默认位置。
        """
        # 初始化各层数据源
        self.window_kb = dict(LEGACY_DISEASE_WINDOWS)
        self.core_symptoms = dict(LEGACY_CORE_SYMPTOMS)

        # 尝试从知识库加载（覆盖/补充硬编码数据）
        self._load_from_knowledge_base(kb_path)

        # 尝试从预后文件加载（覆盖知识库数据，专家编写更精准）
        self._prognosis_loader = self._load_prognosis_data(prognosis_path)
        if self._prognosis_loader:
            # 预后文件覆盖窗口数据
            for name in self._prognosis_loader.disease_names:
                wd = self._prognosis_loader.get_window_dict(name)
                if wd:
                    self.window_kb[name] = wd

    def _load_prognosis_data(self, prognosis_path: str = None):
        """加载预后文件数据"""
        try:
            from m4_prognosis_loader import load_prognosis_data
            loader = load_prognosis_data(prognosis_path)
            return loader
        except (ImportError, FileNotFoundError, Exception):
            return None

    def _load_from_knowledge_base(self, kb_path: str):
        """从知识库动态加载疾病窗口和核心症状数据"""
        try:
            from m4_kb_mapper import build_m4_data_from_kb
            kb_windows, kb_symptoms = build_m4_data_from_kb(kb_path)
        except (ImportError, FileNotFoundError, json.JSONDecodeError) as e:
            return

        if not kb_windows:
            return

        # 知识库数据覆盖硬编码数据（知识库更全、更准确）
        self.window_kb.update(kb_windows)
        self.core_symptoms.update(kb_symptoms)

    # ══════════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════════

    def route(
        self,
        initial_diagnosis: str,
        initial_symptoms: List[str],
        followup_symptoms: List[str],
        followup_feedback: str = "",
        days_since_initial: int = 7,
        vital_signs: Optional[Dict] = None,
        new_complaints: Optional[List[str]] = None,
    ) -> Dict:
        """复诊路由分发

        Args:
            initial_diagnosis: 初诊西医病名（如"睑缘炎 (Blepharitis)"）
            initial_symptoms: 初诊症状列表
            followup_symptoms: 复诊症状列表
            followup_feedback: 患者主观反馈（如"好了一些""没变化""加重了"）
            days_since_initial: 距初诊天数
            vital_signs: 生命体征，如 {"bp": "130/80", "hr": 75, "temp": 36.5, "spo2": 98}
            new_complaints: 新主诉（如果有）

        Returns:
            路由决策结果（JSON）
        """
        # Step 0：危险信号检测（优先级最高）
        danger = self._check_danger_signals(vital_signs, followup_symptoms, new_complaints)
        if danger["triggered"]:
            return self._build_result(
                danger_signals=danger,
                routing_decision={
                    "action": "EMERGENCY_STOP",
                    "reason": "危险信号触发，建议立即急诊",
                    "quadrant": "",
                    "suggested_action": "",
                },
            )

        # Step 1：症状对比 + 改善系数
        comparison = self._compare_symptoms(initial_symptoms, followup_symptoms, followup_feedback)

        # Step 2：病程窗口判断
        window = self._determine_window(initial_diagnosis, days_since_initial)

        # Step 3：病名校验（LLM 兜底）
        validation = self._validate_diagnosis(initial_diagnosis, followup_symptoms, new_complaints)

        # Step 4：路由决策
        routing = self._make_routing_decision(comparison, window, validation)

        return self._build_result(
            visit_comparison=comparison,
            disease_window=window,
            disease_name_validation=validation,
            routing_decision=routing,
            danger_signals=danger,
        )

    # ══════════════════════════════════════════════════════
    #  Step 0：危险信号检测（代码执行，100%）
    # ══════════════════════════════════════════════════════

    def _check_danger_signals(self, vital_signs: Optional[Dict], symptoms: List[str],
                              new_complaints: Optional[List[str]]) -> Dict:
        """代码执行：根据阈值和关键词检测危险信号

        危险信号来源：
        1. 通用阈值（生命体征、DANGER_KEYWORDS）
        2. 预后文件中该疾病的 emergency_stop_if 定制条件（如果有）
        """
        signals = []

        # 生命体征阈值检测
        if vital_signs:
            bp = vital_signs.get("bp", "")
            if bp and "/" in str(bp):
                parts = str(bp).split("/")
                try:
                    sbp = float(parts[0])
                    if sbp >= DANGER_THRESHOLDS["sbp_high"]:
                        signals.append(f"收缩压 {sbp}mmHg ≥ {DANGER_THRESHOLDS['sbp_high']}")
                    elif sbp <= DANGER_THRESHOLDS["sbp_low"]:
                        signals.append(f"收缩压 {sbp}mmHg ≤ {DANGER_THRESHOLDS['sbp_low']}")
                except (ValueError, IndexError):
                    pass

            hr = vital_signs.get("hr")
            if hr is not None:
                try:
                    hr = float(hr)
                    if hr >= DANGER_THRESHOLDS["hr_high"]:
                        signals.append(f"心率 {hr}bpm ≥ {DANGER_THRESHOLDS['hr_high']}")
                    elif hr <= DANGER_THRESHOLDS["hr_low"]:
                        signals.append(f"心率 {hr}bpm ≤ {DANGER_THRESHOLDS['hr_low']}")
                except (ValueError, TypeError):
                    pass

            temp = vital_signs.get("temp")
            if temp is not None:
                try:
                    temp = float(temp)
                    if temp >= DANGER_THRESHOLDS["temp_high"]:
                        signals.append(f"体温 {temp}℃ ≥ {DANGER_THRESHOLDS['temp_high']}")
                except (ValueError, TypeError):
                    pass

            spo2 = vital_signs.get("spo2")
            if spo2 is not None:
                try:
                    spo2 = float(spo2)
                    if spo2 <= DANGER_THRESHOLDS["spo2_low"]:
                        signals.append(f"血氧 {spo2}% ≤ {DANGER_THRESHOLDS['spo2_low']}")
                except (ValueError, TypeError):
                    pass

        # 关键词检测
        all_text = " ".join(symptoms or []) + " " + " ".join(new_complaints or [])
        for kw in DANGER_KEYWORDS:
            if kw in all_text:
                signals.append(f"主诉包含：{kw}")

        return {
            "triggered": len(signals) > 0,
            "signals": signals,
            "recommendation": "建议立即急诊就诊" if signals else "",
        }

    # ══════════════════════════════════════════════════════
    #  Step 1：症状对比（代码执行，100%）
    # ══════════════════════════════════════════════════════

    def _compare_symptoms(self, initial: List[str], followup: List[str],
                          feedback: str = "") -> Dict:
        """代码执行：逐项对比初诊和复诊症状"""
        initial_set = set(s.lower() for s in initial)
        followup_set = set(s.lower() for s in followup)

        # 分类
        improved = []
        worsened = []
        persistent = []
        new = []

        for s in followup:
            s_lower = s.lower()
            # 带"好转""减轻""消失"等关键词的，判断为改善
            if any(kw in s_lower for kw in ["好转", "减轻", "消失", "改善", "缓解"]):
                improved.append(s)
            # 带"加重""恶化""加剧"等关键词的，判断为加重
            elif any(kw in s_lower for kw in ["加重", "恶化", "加剧", "更痛", "更差"]):
                worsened.append(s)
            else:
                # 出现在初诊中，但没有好转/加重标记 → 残留
                matched = False
                for init_s in initial_set:
                    # 子串匹配（如"咳嗽"和"咳嗽有好转"）
                    if s_lower in init_s or init_s in s_lower:
                        persistent.append(s)
                        matched = True
                        break
                if not matched:
                    new.append(s)

        # 改善系数计算
        total_initial = len(initial_set)
        if total_initial == 0:
            score = 0
        else:
            # 主观反馈权重
            feedback_score = 0
            if feedback:
                fb_lower = feedback.lower()
                if any(kw in fb_lower for kw in ["好", "改善", "缓解", "有效"]):
                    feedback_score = 3
                elif any(kw in fb_lower for kw in ["没变", "无变化", "一样", "无效"]):
                    feedback_score = 0
                elif any(kw in fb_lower for kw in ["加重", "更差", "不行", "恶化"]):
                    feedback_score = -3

            # 客观改善比例
            improved_count = len(improved)
            persistent_count = len(persistent)
            worsened_count = len(worsened)

            # 基数：残留症状占比越低，分数越高
            if total_initial == 0:
                base = 5
            else:
                # (改善 + 加重抵消) / 总初诊数 * 10
                net_improved = improved_count - worsened_count
                base = int((net_improved / total_initial) * 10) + 5

            score = base + feedback_score

            # 如果所有症状都是残留且无改善，分数降至最低（0-2分）
            if persistent_count == total_initial and improved_count == 0:
                score = max(0, min(2, score))
            # 如果有残留且无改善，拉低分数
            elif persistent_count > 0 and improved_count == 0:
                score = max(0, score - 2)

        return {
            "improved_symptoms": improved,
            "worsened_symptoms": worsened,
            "persistent_symptoms": persistent,
            "new_symptoms": new,
            "improvement_score": max(0, min(10, score)),
        }

    # ══════════════════════════════════════════════════════
    #  Step 2：病程窗口判断（代码执行，100%）
    # ══════════════════════════════════════════════════════

    def _determine_window(self, diagnosis: str, days: int) -> Dict:
        """代码执行：从预后文件或知识库读取疾病窗口数据

        数据优先级：
        1. 预后文件的三层窗口（first/main/stable）
        2. 知识库窗口（onset/significant）
        3. 默认窗口
        """
        disease_clean = diagnosis.split(" (")[0].split("（")[0].strip()

        # 优先从预后文件获取窗口
        onset_days = 0
        significant_days = 0
        stable_days = 0
        prognosis_available = False
        prognosis_interpretation = {}

        if self._prognosis_loader:
            card = self._prognosis_loader.get_card(diagnosis)
            if not card:
                card = self._prognosis_loader.get_card(disease_clean)
            if card:
                tw = card.treatment_window
                if tw.first_days_max > 0:
                    onset_days = tw.first_days_max
                if tw.main_days_max > 0:
                    significant_days = tw.main_days_max
                if tw.stable_days_max > 0:
                    stable_days = tw.stable_days_max
                prognosis_available = (onset_days > 0 and significant_days > 0)
                prognosis_interpretation = tw.interpretation

        if not prognosis_available:
            # 回退到知识库窗口
            window = self.window_kb.get(disease_clean, DEFAULT_WINDOW)
            onset_days = window["onset"]
            significant_days = window["significant"]

        return {
            "disease": diagnosis,
            "onset_window_days": onset_days,
            "significant_window_days": significant_days,
            "stable_window_days": stable_days if stable_days > 0 else None,
            "current_day": days,
            "within_window": days < significant_days,
            "exceeded_window": days >= significant_days,
            "window_source": "prognosis_file" if prognosis_available else "knowledge_base",
            "prognosis_interpretation": prognosis_interpretation if prognosis_interpretation else None,
        }

    # ══════════════════════════════════════════════════════
    #  Step 3：病名校验（代码框架 + LLM 兜底）
    # ══════════════════════════════════════════════════════

    def _validate_diagnosis(self, original_diagnosis: str,
                            followup_symptoms: List[str],
                            new_complaints: Optional[List[str]]) -> Dict:
        """病名校验：代码做关键词匹配，LLM 兜底复杂判断

        匹配规则：
        1. 对复诊症状进行清洗（去除"好转""减轻""加重"等修饰词），提取疾病核心词
        2. 将清洗后的词与知识库中该疾病的核心症状做交叉匹配
        3. 核心症状匹配 >= 1 个即认为仍符合原病名（宽容策略，因为复诊时部分症状已缓解）
        4. 但有一种情况依然返回 false：原症状已全部消失（evidence_for中全来自好转项），
           且新发症状（evidence_against >= 2 且都不是好转形式）完全不符合原病名
        5. 知识库无数据时 LLM 兜底
        """
        # 判断一个症状是否"好转/消失"
        def is_improved_form(symptom: str) -> bool:
            s = symptom.lower()
            for kw in ["好转", "减轻", "缓解", "消失", "改善", "好了", "减轻了"]:
                if kw in s:
                    return True
            return False

        # 清洗症状的辅助函数：去除好转/加重修饰词，提取疾病核心词
        def clean_symptom(symptom: str) -> str:
            s = symptom
            for prefix in ["减轻", "缓解", "好转", "消失", "改善",
                           "加重", "恶化", "加剧", "更痛", "更差",
                           "没有", "未", "有点", "有些"]:
                if s.startswith(prefix) and len(s) > len(prefix):
                    s = s[len(prefix):]
                    break
            for suffix in ["减轻", "缓解", "好转", "消失", "改善",
                           "加重", "恶化", "加剧"]:
                if s.endswith(suffix) and len(s) > len(suffix):
                    s = s[:-len(suffix)]
                    break
            return s.strip()

        disease_clean = original_diagnosis.split(" (")[0].split("（")[0].strip()

        # 收集所有复诊主诉
        all_complaints = [s for s in followup_symptoms if s]
        if new_complaints:
            all_complaints.extend(new_complaints)

        # 清洗后的核心症状词
        cleaned_terms = set()
        # 记录清洗前的原始描述，用于判断是否"好转"
        original_forms = {}
        for s in all_complaints:
            cleaned = clean_symptom(s)
            if len(cleaned) >= 2:
                cleaned_terms.add(cleaned)
                original_forms[cleaned] = s  # 保留原始形式

        # 获取该疾病的核心症状
        core_symptoms = self._get_core_symptoms(disease_clean)

        if core_symptoms:
            # 宽松匹配
            evidence_for = []
            for cs in core_symptoms:
                for term in cleaned_terms:
                    if cs.lower() in term.lower() or term.lower() in cs.lower():
                        evidence_for.append(cs)
                        break
            evidence_for = list(dict.fromkeys(evidence_for))

            # 判断 evidence_for 是否全部来自"已好转"的症状
            all_evidence_from_improved = True
            for cs in evidence_for:
                # 找到匹配的原始症状
                found_improved = False
                for term in cleaned_terms:
                    if cs.lower() in term.lower() or term.lower() in cs.lower():
                        orig = original_forms.get(term, term)
                        if is_improved_form(orig):
                            found_improved = True
                            break
                if not found_improved:
                    all_evidence_from_improved = False
                    break

            # 计算 evidence_against（完全无法匹配核心症状的清洗后词）
            evidence_against = []
            for term in cleaned_terms:
                is_matched = False
                for cs in core_symptoms:
                    if cs.lower() in term.lower() or term.lower() in cs.lower():
                        is_matched = True
                        break
                if not is_matched:
                    evidence_against.append(term)

            # 综合判断 still_valid
            if len(evidence_for) >= 1:
                # 特殊情况：所有匹配上的核心症状都来自"已好转"描述，
                # 且存在 >= 2 个无法解释的新症状 → 仍判为 invalid
                if all_evidence_from_improved and len(evidence_against) >= 2:
                    still_valid = False
                else:
                    still_valid = True
            else:
                still_valid = False
        else:
            # 知识库无核心症状数据 → LLM 兜底
            llm_result = self._llm_validate_diagnosis(original_diagnosis, list(cleaned_terms))
            if llm_result:
                return llm_result
            still_valid = True
            evidence_for = ["LLM 不可用，默认认为仍符合原病名"]
            evidence_against = []

        return {
            "original_diagnosis": original_diagnosis,
            "still_valid": still_valid,
            "evidence_for": evidence_for,
            "evidence_against": evidence_against[:5],
            "new_symptoms_explained": still_valid,
        }

    def _get_core_symptoms(self, disease: str) -> List[str]:
        """从知识库获取某疾病的核心/典型症状

        数据来源优先级：
        1. 通过 m4_kb_mapper 从知识库 trigger 中提取的症状（self.core_symptoms）
        2. 硬编码兜底数据（LEGACY_CORE_SYMPTOMS）
        3. 空列表（使用 LLM 兜底）
        """
        # 尝试精确匹配
        if disease in self.core_symptoms:
            return self.core_symptoms[disease]

        # 尝试短名匹配（去括号英文）
        short = disease.split(" (")[0].split("（")[0].strip()
        if short in self.core_symptoms:
            return self.core_symptoms[short]

        # 尝试部分匹配（疾病名中包含的关键词）
        for key in self.core_symptoms:
            if key in disease or disease in key:
                return self.core_symptoms[key]

        return []

    def _llm_validate_diagnosis(self, original_diagnosis: str,
                                new_symptoms: List[str]) -> Optional[Dict]:
        """LLM 兜底：病名校验"""
        prompt = f"""你是一名临床医生。判断复诊患者的临床表现是否仍符合原西医病名。

## 原西医病名
{original_diagnosis}

## 复诊时所有症状/主诉
{'、'.join(new_symptoms)}

## 判断规则
1. 这些症状能否用"{original_diagnosis}"解释？
2. 有无明显不符合该诊断的症状？
3. 是否需要考虑其他疾病？

## 输出 JSON（只输出 JSON，不要其他文字）
{{
  "still_valid": true/false,
  "evidence_for": ["支持原病名的依据1", "依据2"],
  "evidence_against": ["反对的依据1", "依据2"],
  "reasoning": "简要推理过程"
}}"""

        result = self._call_llm(prompt)
        if result:
            try:
                m = re.search(r"\{.*\}", result, re.DOTALL)
                parsed = json.loads(m.group())
                return {
                    "original_diagnosis": original_diagnosis,
                    "still_valid": parsed.get("still_valid", True),
                    "evidence_for": parsed.get("evidence_for", []),
                    "evidence_against": parsed.get("evidence_against", [])[:5],
                    "new_symptoms_explained": parsed.get("still_valid", True),
                }
            except (json.JSONDecodeError, AttributeError, KeyError):
                pass
        return None

    # ══════════════════════════════════════════════════════
    #  Step 4：路由决策（代码执行，100%）
    # ══════════════════════════════════════════════════════

    def _make_routing_decision(self, comparison: Dict, window: Dict,
                               validation: Dict) -> Dict:
        """代码执行：综合前三步，确定路由方向

        如果预后文件有该疾病的定制路由策略（M4RoutingPolicy），
        则用定制策略增强决策理由。
        """
        score = comparison["improvement_score"]
        within_window = window["within_window"]
        still_valid = validation["still_valid"]
        has_new = len(comparison["new_symptoms"]) > 0
        has_worsened = len(comparison["worsened_symptoms"]) > 0

        # 获取预后文件中的定制路由策略（如果有）
        prognosis_guidance = self._get_prognosis_guidance(window.get("disease", ""))

        # 规则引擎
        if not still_valid:
            return {
                "action": "RETURN_TO_M1",
                "reason": f"病名校验不符：原病名不再符合当前临床表现",
                "quadrant": "",
                "suggested_action": "",
                "prognosis_guidance": prognosis_guidance,
            }

        # Q3（激惹）：有新发或加重症状（不论改善多少）
        if has_new or has_worsened:
            return {
                "action": "RETURN_TO_M2",
                "reason": f"改善系数 {score}/10，出现新问题{'和' if has_new and has_worsened else ''}加重症状，考虑药证激惹",
                "quadrant": "Q3",
                "suggested_action": "调整",
                "prognosis_guidance": prognosis_guidance,
            }
        # Q1（效佳）：改善 ≥ 3，无新增/加重
        if score >= 3:
            return {
                "action": "RETURN_TO_M2",
                "reason": f"改善系数 {score}/10，主症缓解，疗效明确",
                "quadrant": "Q1",
                "suggested_action": "守方",
                "prognosis_guidance": prognosis_guidance,
            }
        # Q2（待效）：改善不足且无新问题，未超显效窗口
        if not window["exceeded_window"]:
            return {
                "action": "RETURN_TO_M2",
                "reason": f"改善系数 {score}/10，尚未超出显效窗口（{window['significant_window_days']}天），继续守方观察",
                "quadrant": "Q2",
                "suggested_action": "守方等待",
                "prognosis_guidance": prognosis_guidance,
            }
        # Q4（无效）：已超显效窗口，改善不足
        return {
            "action": "RETURN_TO_M2",
            "reason": f"改善系数 {score}/10，已超出显效窗口（{window['significant_window_days']}天），需要更方",
            "quadrant": "Q4",
            "suggested_action": "更方",
            "prognosis_guidance": prognosis_guidance,
        }

    def _get_prognosis_guidance(self, diagnosis: str) -> Optional[Dict]:
        """获取预后文件中的疾病路由指导（如果有）"""
        if not self._prognosis_loader:
            return None

        card = self._prognosis_loader.get_card(diagnosis)
        if not card:
            return None

        routing = card.routing_policy
        if not routing:
            return None

        return {
            "disease": card.disease_name,
            "return_to_m2_if": routing.return_to_m2_if,
            "return_to_m1_if": routing.return_to_m1_if,
            "emergency_stop_if": routing.emergency_stop_if,
            "do_not_overreact_if": routing.do_not_overreact_if,
            "must_recheck_or_refer_if": routing.must_recheck_or_refer_if,
            "course_category": card.course_category,
        }

    # ══════════════════════════════════════════════════════
    #  辅助方法
    # ══════════════════════════════════════════════════════

    def _build_result(self, **kwargs) -> Dict:
        """构建标准输出"""
        return {
            "visit_comparison": kwargs.get("visit_comparison", {}),
            "disease_window": kwargs.get("disease_window", {}),
            "disease_name_validation": kwargs.get("disease_name_validation", {}),
            "routing_decision": kwargs.get("routing_decision", {}),
            "danger_signals": kwargs.get("danger_signals", {"triggered": False, "signals": []}),
            "formal_prescription_allowed": False,
            "formula_candidates": [],
            "modification_candidates": [],
        }

    def _call_llm(self, prompt: str) -> Optional[str]:
        try:
            from m1_engine import M1DiagnosisEngine
            return M1DiagnosisEngine()._call_llm(prompt, temperature=0.1, max_tokens=500)
        except Exception:
            return None
