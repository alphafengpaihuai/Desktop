"""
m2_pattern_match_engine.py
==========================
守一 CDSS — M2 证型校对引擎（V4）

废弃:
  - m2_pathology_syndrome_bridge.py（旧 M2-0）
  - m2_pathology_to_syndrome_rules.json（轴→证型映射）
  - m2_symptom_multi_axis_matrix.json（症状→轴矩阵）
  - AXIS_SEMANTIC_MAP / _resolve_symptom_axes 等

新的 M2 不再匹配 pathology axes。
改为匹配本地 pattern_db（从口服方剂知识库自动构建）。

核心规则:
  1. 西医病名只用于限定证型范围，不能直接决定证型。
  2. 西医病理生理解释只作为证型病机方向的辅助证据，不能单独决定证型。
  3. 证型必须由主症、兼症、舌脉、病程、诱因、排除项共同校对。
  4. 舌脉缺失时，证型最高为 medium。
  5. 主症不完整时，证型最高为 low。
  6. 命中 exclusion_features 时，该证型强制 low。
  7. 不允许模型自由创造证型。
  8. 不允许根据病理轴推证型。
  9. 不允许仅凭西医病名直接给证型。
"""

import json
import os
import re
import hashlib
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field


# ════════════════════════════════════════
# 数据类
# ════════════════════════════════════════

@dataclass
class PatternEntry:
    """证型条目（从 pattern_db 加载）"""
    pattern_id: str
    syndrome_name: str
    western_diseases: List[str] = field(default_factory=list)
    required_features: List[str] = field(default_factory=list)
    supportive_features: List[str] = field(default_factory=list)
    tongue_pulse: Dict[str, List[str]] = field(default_factory=dict)
    western_pathophysiology_compatibility: List[str] = field(default_factory=list)
    pathogenesis_explanation: str = ""
    exclusion_features: List[str] = field(default_factory=list)
    differential_patterns: List[str] = field(default_factory=list)
    formula_name: str = ""
    herbs: List[str] = field(default_factory=list)
    source: str = "口服方剂知识库"


@dataclass
class PatternMatchResult:
    """单个证型匹配结果"""
    pattern_id: str
    syndrome_name: str
    confidence: str  # high / medium / low
    confidence_reasoning: str
    matched_required: List[str] = field(default_factory=list)
    missed_required: List[str] = field(default_factory=list)
    matched_supportive: List[str] = field(default_factory=list)
    tongue_pulse_support: float = 0.0
    tongue_pulse_details: Dict[str, list] = field(default_factory=dict)
    western_pathophysiology_support: List[str] = field(default_factory=list)
    pathogenesis_explanation: str = ""
    exclusion_matches: List[str] = field(default_factory=list)
    formula_name: str = ""
    herbs: List[str] = field(default_factory=list)
    source: str = ""


@dataclass
class M2Input:
    """M2 输入"""
    western_disease: str = ""
    western_disease_candidates: List[dict] = field(default_factory=list)
    western_symptoms: List[str] = field(default_factory=list)
    western_pathophysiology_context: str = ""
    tcm_symptoms: List[str] = field(default_factory=list)
    tongue: str = ""
    pulse: str = ""
    duration: str = ""
    aggravating_factors: List[str] = field(default_factory=list)
    relieving_factors: List[str] = field(default_factory=list)
    patient_age: str = ""
    patient_gender: str = ""


@dataclass
class M2Output:
    """M2 证型校对输出"""
    western_disease_context: str = ""
    top_pattern: Optional[PatternMatchResult] = None
    all_patterns: List[PatternMatchResult] = field(default_factory=list)
    confidence: str = ""
    confidence_reasoning: str = ""
    matched_required_features: List[str] = field(default_factory=list)
    missed_required_features: List[str] = field(default_factory=list)
    matched_supportive_features: List[str] = field(default_factory=list)
    tongue_pulse_support: float = 0.0
    western_pathophysiology_support: List[str] = field(default_factory=list)
    pathogenesis_explanation: str = ""
    exclusion_matches: List[str] = field(default_factory=list)
    differential_patterns: List[dict] = field(default_factory=list)
    missing_info: List[str] = field(default_factory=list)
    formula: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════
# 模式库构建器
# ════════════════════════════════════════

class PatternDBBuilder:
    """从口服方剂知识库 xlsx 自动构建 pattern_db"""

    def __init__(self, xlsx_path: str = None):
        if xlsx_path is None:
            xlsx_path = '/Users/fangxuan/Desktop/知识库备份/口服方剂知识库.xlsx'
        self.xlsx_path = xlsx_path
        self.patterns: Dict[str, PatternEntry] = {}
        self._disease_to_patterns: Dict[str, List[str]] = {}

    def build(self) -> Dict[str, PatternEntry]:
        import pandas as pd
        df = pd.read_excel(self.xlsx_path)
        valid = df[df['病名【诊断】'].notna()].copy()

        for _, row in valid.iterrows():
            disease_block = str(row['病名【诊断】'])
            syndrome_text = str(row['中医证型 <辨证分型>']) if pd.notna(row['中医证型 <辨证分型>']) else ''
            formula_text = str(row['汤剂方案']) if pd.notna(row['汤剂方案']) else ''

            syndrome_name, syndrome_desc = self._parse_syndrome_text(syndrome_text)
            if not syndrome_name:
                continue

            disease_names = self._extract_disease_names(disease_block)
            required, supportive, tongue_pulse = self._parse_syndrome_description(syndrome_desc)
            formula_name, herbs = self._parse_formula_text(formula_text)

            pattern_id = self._make_pattern_id(disease_names[0] if disease_names else '', syndrome_name)

            entry = PatternEntry(
                pattern_id=pattern_id,
                syndrome_name=syndrome_name,
                western_diseases=disease_names,
                required_features=required,
                supportive_features=supportive,
                tongue_pulse=tongue_pulse,
                western_pathophysiology_compatibility=disease_names[:3],
                pathogenesis_explanation=syndrome_name,
                formula_name=formula_name,
                herbs=herbs,
            )
            self.patterns[pattern_id] = entry

            for dn in disease_names:
                ndn = dn.strip().lower()
                if ndn not in self._disease_to_patterns:
                    self._disease_to_patterns[ndn] = []
                self._disease_to_patterns[ndn].append(pattern_id)

        return self.patterns

    def _parse_syndrome_text(self, text: str) -> Tuple[str, str]:
        m = re.search(r'<([^>]+)>', text)
        if not m:
            return '', ''
        name = m.group(1).strip()
        desc = text[m.end():].strip().lstrip('：:')
        return name, desc

    def _extract_disease_names(self, block: str) -> List[str]:
        names = []
        for line in block.split('\n'):
            line = line.strip()
            for prefix in ['**主病名：', '**主病名:', '- 主病名：', '- 主病名:',
                           '主病名：', '主病名:', '◎ 中医病名:']:
                if prefix in line:
                    name = line.split(prefix)[-1].split('\n')[0].strip().rstrip('*').strip()
                    if name and name not in names:
                        names.append(name)
                    break
        for line in block.split('\n'):
            line = line.strip()
            if '别名' in line and ('：' in line or ':' in line):
                sep = '：' if '：' in line else ':'
                for alias in line.split(sep)[-1].replace('，', ',').replace('、', ',').split(','):
                    alias = alias.strip().rstrip('*').strip()
                    if alias and len(alias) >= 2 and alias not in names:
                        names.append(alias)
        extras = []
        for name in names:
            for prefix in ['儿童', '小儿', '新生儿', '婴幼儿', '老年', '成人']:
                if name.startswith(prefix) and len(name) > len(prefix) + 2:
                    v = name[len(prefix):]
                    if v not in names and v not in extras:
                        extras.append(v)
            if '(' in name and ')' in name:
                base = name.split('(')[0].strip()
                if base and base not in names and base not in extras:
                    extras.append(base)
        names.extend(extras)
        return names

    def _parse_syndrome_description(self, desc: str) -> Tuple[List[str], List[str], dict]:
        if not desc:
            return [], [], {"tongue": [], "pulse": [], "pediatric_finger": []}
        tp = {"tongue": [], "pulse": [], "pediatric_finger": []}
        symptoms = []
        for part in re.split(r'[，,。.；;]', desc):
            part = part.strip()
            if not part:
                continue
            if any(c in part for c in ['舌', '苔']):
                items = [m.group() for m in re.finditer(r'[舌苔][质色苔薄白黄腻滑干燥润]*[红白黄淡暗紫]*', part)]
                tp["tongue"].extend(items or [part])
            elif '指纹' in part:
                tp["pediatric_finger"].append(part)
            elif '脉' in part:
                items = re.findall(r'脉[浮沉迟数滑涩弦紧缓弱]*', part)
                tp["pulse"].extend(items or [part])
            else:
                symptoms.append(part)
        # 改进的划分：required = 核心症状（简洁明确的），supportive = 描述性/修饰性语句
        # 用长度+语义判断
        required = []
        supportive = []
        KEY_SYMPTOM_TERMS = {'发热', '恶寒', '恶风', '头痛', '咳嗽', '咽痛', '咽痒',
            '鼻塞', '流涕', '胸痛', '胸闷', '腹痛', '腹泻', '恶心', '呕吐',
            '气喘', '气促', '心悸', '水肿', '黄疸', '皮疹', '瘙痒',
            '疼痛', '红肿', '肿胀', '溃疡', '出血', '瘀斑',
            '扁桃体肿大', '喉核赤肿', '喉核肿大', '脓点', '白膜',
            '少尿', '多尿', '血尿', '便秘', '便血',
            '乏力', '消瘦', '失眠', '烦躁', '口渴', '口干',
            '吞咽困难', '吞咽不利', '声音嘶哑', '失音',
            '关节痛', '腰痛', '背痛', '肢体麻木', '活动不利',
            '发热恶风', '发热恶寒', '寒热往来', '但热不寒',
            '有汗', '无汗', '自汗', '盗汗',
            '大便干', '大便溏', '小便黄', '小便清长',
            '纳差', '纳呆', '厌食', '多食',
            '眩晕', '耳鸣', '耳聋', '目眩', '目赤',
            '面色苍白', '面色萎黄', '面色红赤', '面色晦暗',
        }
        for s in symptoms:
            s_stripped = s.strip()
            if any(kw in s_stripped for kw in KEY_SYMPTOM_TERMS):
                required.append(s_stripped)
            elif len(s_stripped) <= 6:
                required.append(s_stripped)
            else:
                supportive.append(s_stripped)
        required = list(dict.fromkeys(required))
        supportive = list(dict.fromkeys(supportive))
        # required 只保留最核心的前5个症状，其余归入 supportive
        if len(required) > 5:
            supportive = required[5:] + supportive
            required = required[:5]
        return required, supportive, tp

    def _parse_formula_text(self, text: str) -> Tuple[str, List[str]]:
        if not text or text in ('nan', '', '无'):
            return '', []
        m = re.search(r'【([^】]+)】', text)
        formula_name = m.group(1).strip() if m else ''
        COMMON_HERBS = {
            '人参', '三七', '干姜', '大黄', '大枣', '川芎', '川牛膝',
            '女贞子', '山药', '山楂', '山茱萸', '丹参', '乌药', '五味子',
            '天冬', '天麻', '木瓜', '木香', '车前子', '牛膝', '升麻',
            '太子参', '巴戟天', '火麻仁', '生姜', '生地黄', '生石膏', '生甘草',
            '白芍', '白芷', '白术', '白茅根', '石决明', '石菖蒲', '龙胆草',
            '地龙', '地骨皮', '百合', '当归', '肉苁蓉', '肉桂', '防风',
            '赤芍', '延胡索', '何首乌', '吴茱萸', '牡丹皮', '牡蛎', '羌活',
            '苍术', '厚朴', '茯苓', '茯神', '茵陈', '荆芥', '钩藤', '香附',
            '党参', '夏枯草', '柴胡', '桂枝', '桃仁', '桑叶', '桑枝', '桑寄生',
            '桔梗', '浙贝母', '益母草', '菟丝子', '黄芩', '黄连', '黄芪', '黄柏',
            '栀子', '猪苓', '麻黄', '淫羊藿', '细辛', '菊花', '蝉蜕', '僵蚕',
            '全蝎', '远志', '酸枣仁', '柏子仁', '合欢皮', '龙骨', '龟甲', '鳖甲',
            '甘草', '炙甘草', '炙黄芪', '陈皮', '法半夏', '姜半夏', '竹茹', '枳实',
            '枳壳', '青皮', '大腹皮', '槟榔', '莱菔子', '神曲', '麦芽', '鸡内金',
            '阿胶', '熟地黄', '麦冬', '玉竹', '沙参', '石斛', '枸杞子',
            '杜仲', '续断', '补骨脂', '金樱子', '莲子', '芡实', '乌梅',
            '佛手', '玫瑰花', '鸡血藤', '独活', '薄荷', '牛蒡子', '葛根', '淡豆豉',
            '射干', '马勃', '板蓝根', '金银花', '连翘', '紫菀', '百部', '白前',
            '白芥子', '苏子', '葶苈子', '款冬花', '瓜蒌', '薤白', '半夏',
            '天南星', '白附子', '皂角刺', '瓦楞子', '海蛤壳', '海浮石', '青礞石',
        }
        herbs = []
        for name in re.findall(r'([\u4e00-\u9fff]{2,4})', text):
            exc = {'方剂', '用药', '疗程', '起始连用', '每日', '水煎分次温服',
                   '疗效预估', '安全警示', '婴幼儿', '过敏体质', '脾胃虚寒',
                   '孕妇', '哺乳期', '方药', '处方', '加减', '先煎', '后下',
                   '包煎', '烊化', '冲服', '分服', '顿服', '布包', '少量频服'}
            if name not in exc and len(name) >= 2 and name in COMMON_HERBS and name not in herbs:
                herbs.append(name)
        return formula_name, herbs

    def _make_pattern_id(self, disease: str, syndrome: str) -> str:
        raw = f"{disease}||{syndrome}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def find_patterns_by_disease(self, disease_name: str) -> List[PatternEntry]:
        norm = disease_name.strip().lower()
        pids = self._disease_to_patterns.get(norm, [])
        if not pids:
            for dn, ids in self._disease_to_patterns.items():
                if dn in norm or norm in dn:
                    pids.extend(ids)
        pids = list(dict.fromkeys(pids))
        return [self.patterns[pid] for pid in pids if pid in self.patterns]


# ════════════════════════════════════════
# M2 证型校对引擎
# ════════════════════════════════════════

class M2PatternMatchEngine:
    """M2 证型校对引擎（V4）"""

    def __init__(self, xlsx_path: str = None):
        self._builder = PatternDBBuilder(xlsx_path=xlsx_path)
        self._patterns: Dict[str, PatternEntry] = {}
        self._disease_to_patterns: Dict[str, List[str]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._patterns = self._builder.build()
            self._disease_to_patterns = self._builder._disease_to_patterns
            self._loaded = True

    def evaluate(self, input_data: M2Input) -> M2Output:
        self._ensure_loaded()
        output = M2Output()
        western_disease = input_data.western_disease
        all_text = self._build_patient_text(input_data)

        candidates = self._find_candidates(western_disease, input_data)
        if not candidates:
            output.confidence = "low"
            output.confidence_reasoning = f"知识库中无「{western_disease}」对应的证型记录"
            return output

        results = []
        for pattern in candidates:
            results.append(self._match_single(pattern, input_data, all_text))

        def sort_key(r: PatternMatchResult):
            rk = {"high": 3, "medium": 2, "low": 1}
            return (rk.get(r.confidence, 0), len(r.matched_required), r.tongue_pulse_support)
        results.sort(key=sort_key, reverse=True)

        output.western_disease_context = western_disease
        output.all_patterns = results

        if results:
            top = results[0]
            output.top_pattern = top
            output.confidence = top.confidence
            output.confidence_reasoning = top.confidence_reasoning
            output.matched_required_features = top.matched_required
            output.missed_required_features = top.missed_required
            output.matched_supportive_features = top.matched_supportive
            output.tongue_pulse_support = top.tongue_pulse_support
            output.western_pathophysiology_support = top.western_pathophysiology_support
            output.pathogenesis_explanation = top.pathogenesis_explanation
            output.exclusion_matches = top.exclusion_matches
            output.formula = {"formula_name": top.formula_name, "herbs": top.herbs, "source": top.source}
            for r in results[1:]:
                output.differential_patterns.append({
                    "syndrome_name": r.syndrome_name, "pattern_id": r.pattern_id,
                    "confidence": r.confidence, "reason": r.confidence_reasoning,
                    "missed_required": r.missed_required,
                })

        output.missing_info = self._check_missing(input_data)
        return output

    def _build_patient_text(self, d: M2Input) -> str:
        parts = []
        parts.extend(d.western_symptoms)
        parts.extend(d.tcm_symptoms)
        if d.tongue: parts.append(d.tongue)
        if d.pulse: parts.append(d.pulse)
        if d.duration: parts.append(d.duration)
        parts.extend(d.aggravating_factors)
        parts.extend(d.relieving_factors)
        if d.western_pathophysiology_context: parts.append(d.western_pathophysiology_context)
        return " ".join(parts)

    def _find_candidates(self, wd: str, d: M2Input) -> List[PatternEntry]:
        cand = self._builder.find_patterns_by_disease(wd)
        if not cand:
            for cd in d.western_disease_candidates:
                dn = cd.get("disease_name", "")
                if dn and dn != wd:
                    cand.extend(self._builder.find_patterns_by_disease(dn))
        seen = set()
        dedup = []
        for c in cand:
            if c.pattern_id not in seen:
                seen.add(c.pattern_id)
                dedup.append(c)
        return dedup

    def _match_single(self, p: PatternEntry, d: M2Input, text: str) -> PatternMatchResult:
        res = PatternMatchResult(pattern_id=p.pattern_id, syndrome_name=p.syndrome_name,
            confidence="low", confidence_reasoning="", formula_name=p.formula_name,
            herbs=p.herbs, source=p.source, pathogenesis_explanation=p.pathogenesis_explanation)
        tl = text.lower()
        tt = d.tongue.lower() if d.tongue else ""
        pt = d.pulse.lower() if d.pulse else ""

        SYNONYM_MAP = {
            '咽喉疼痛': ['咽痛', '咽喉痛', '喉咙痛', '喉痛'],
            '咽痛': ['咽喉疼痛', '咽喉痛', '喉咙痛', '喉痛'],
            '喉核赤肿': ['扁桃体肿大', '扁桃体红肿', '喉核红肿', '咽红', '喉核'],
            '喉核': ['扁桃体', '扁桃体肿大'],
            '咽痒': ['咽喉痒', '喉咙痒', '喉痒'],
            '鼻塞流涕': ['鼻塞', '流涕', '流鼻涕'],
            '头痛身痛': ['头痛', '身痛', '全身痛', '肌肉痛'],
            '咳嗽': ['咳', '咳嗽'],
            '发热': ['发烧', '发热', '体温高'],
            '恶风': ['怕风', '恶风'],
            '恶寒': ['怕冷', '恶寒', '畏寒'],
            '吞咽不利': ['吞咽困难', '吞咽痛', '咽痛', '吞咽'],
            '脓点': ['脓栓', '化脓', '脓', '脓性'],
        }
        def fuzzy_match(feature: str, target: str) -> bool:
            fl = feature.lower().strip()
            if fl in target:
                return True
            # 同义词映射
            for kw, syns in SYNONYM_MAP.items():
                if kw in fl or fl in kw:
                    if any(s in target for s in syns):
                        return True
            # 子词匹配
            fl_words = [w for w in fl if '一' <= w <= '鿿' and w not in '的得了就很还又再才都']
            target_words = set(target)
            if len(fl_words) >= 2:
                matched_chars = sum(1 for c in fl_words if c in target_words)
                if matched_chars >= len(fl_words) * 0.6:
                    return True
            return False

        mr = [f for f in p.required_features if fuzzy_match(f, tl)]
        miss = [f for f in p.required_features if not fuzzy_match(f, tl)]
        res.matched_required = mr; res.missed_required = miss
        rr = len(mr) / len(p.required_features) if p.required_features else 0

        if rr < 0.5:
            res.confidence = "low"
            res.confidence_reasoning = f"主症匹配不足 ({len(mr)}/{len(p.required_features)})"
            return res

        res.matched_supportive = [f for f in p.supportive_features if fuzzy_match(f, tl)]
        tp = p.tongue_pulse
        tm = [t for t in tp.get("tongue", []) if fuzzy_match(t, tt) or fuzzy_match(t, tl)]
        pm = [x for x in tp.get("pulse", []) if fuzzy_match(x, pt) or fuzzy_match(x, tl)]
        fm = [x for x in tp.get("pediatric_finger", []) if fuzzy_match(x, tl)]
        total = len(tp.get("tongue", [])) + len(tp.get("pulse", [])) + len(tp.get("pediatric_finger", []))
        mtp = len(tm) + len(pm) + len(fm)
        tr = mtp / total if total > 0 else 0
        res.tongue_pulse_support = tr
        res.tongue_pulse_details = {"matched_tongue": tm, "matched_pulse": pm, "matched_finger": fm}
        has_t = bool(tt.strip())
        # 脉象可忽略，不作为信心限制条件
        tp_miss = not has_t

        ex = [e for e in p.exclusion_features if e.lower() in tl]
        res.exclusion_matches = ex
        if ex:
            res.confidence = "low"
            res.confidence_reasoning = f"命中排除项: {'; '.join(ex)}"
            return res

        res.western_pathophysiology_support = [c for c in p.western_pathophysiology_compatibility if c.lower() in tl]
        sr = len(res.matched_supportive) / len(p.supportive_features) if p.supportive_features else 0.5

        if tp_miss:
            res.confidence = "medium" if rr >= 0.8 and sr >= 0.3 else "low"
            res.confidence_reasoning = f"主症{'良好' if rr>=0.8 else '部分'}匹配，舌脉缺失→{res.confidence}"
        elif tr >= 0.5 and rr >= 0.8:
            res.confidence = "high"
            res.confidence_reasoning = f"主症{len(mr)}/{len(p.required_features)}，舌脉{mtp}/{total}→high"
        elif rr >= 0.8:
            res.confidence = "medium"
            res.confidence_reasoning = f"主症充分，舌脉部分({mtp}/{total})→medium"
        else:
            res.confidence = "low"
            res.confidence_reasoning = f"主症不足({len(mr)}/{len(p.required_features)})→low"
        return res

    def _check_missing(self, d: M2Input) -> List[str]:
        m = []
        if not d.tongue.strip(): m.append("缺少舌象")
        if not d.pulse.strip(): m.append("缺少脉象")
        if not d.tcm_symptoms: m.append("缺少中医症状")
        if not d.duration: m.append("缺少病程")
        return m


def get_m2_engine(xlsx_path: str = None) -> M2PatternMatchEngine:
    return M2PatternMatchEngine(xlsx_path=xlsx_path)
