"""
M4 病程预后窗口加载器
====================
从 m4_disease_course_prognosis_window_master 文件加载专家整理的
病程预后数据，提供三层窗口（首次/主症/稳定）和路由策略。

数据优先级（在 M4RoutingEngine 中）：
  1. 预后文件（专家逐病编写，最精准） ← 本模块
  2. 知识库映射（m4_kb_mapper 规则推算）
  3. 遗留硬编码兜底

用法：
    from m4_prognosis_loader import load_prognosis_data, PrognosisCard

    loader = load_prognosis_data()
    card = loader.get_card("急性上呼吸道感染")
    if card:
        print(card.treatment_response_window)
        print(card.m4_routing_policy)
"""

import json
import re
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════
#  数据结构定义
# ════════════════════════════════════════════════════════════

@dataclass
class TreatmentResponseWindow:
    """治疗响应窗口"""
    first_response_days: str        # 首次响应（如 "2-3"）
    main_response_days: str         # 主症响应（如 "5-7"）
    stable_response_days: str       # 稳定响应（如 "7-14"）
    disease_type: str = ""          # 疾病类型标签
    interpretation: dict = field(default_factory=dict)  # M4窗口解读
    
    # 解析后的数字值（用于代码判断）
    first_days_max: int = 0         # 首次响应上限（onset）
    main_days_max: int = 0          # 主症响应上限（significant）
    stable_days_max: int = 0        # 稳定响应上限


@dataclass
class NaturalCourse:
    """自然病程"""
    best_onset: str
    best_significant: str
    best_resolution: str
    typical_onset: str
    typical_significant: str
    typical_resolution: str
    residual_symptoms: List[str]
    slow_delayed: str
    slow_prolonged: str
    best_zh: str = ""
    typical_zh: str = ""
    slow_zh: str = ""


@dataclass
class CourseDeviation:
    """病程偏离原因"""
    still_acceptable: List[str]
    not_controlled: List[str]
    complication_or_new: List[str]
    wrong_diagnosis: List[str]
    adherence_or_external: List[str]


@dataclass
class M4RoutingPolicy:
    """M4路由策略"""
    return_to_m2_if: List[str]
    return_to_m1_if: List[str]
    emergency_stop_if: List[str]
    do_not_overreact_if: List[str]
    must_recheck_or_refer_if: List[str]


@dataclass
class PrognosisCard:
    """完整病程预后卡片"""
    disease_name: str
    course_category: str
    treatment_window: TreatmentResponseWindow
    natural_course: Optional[NaturalCourse] = None
    deviation: Optional[CourseDeviation] = None
    routing_policy: Optional[M4RoutingPolicy] = None
    m4_usage_policy: Optional[List[str]] = None
    overall_prognosis: str = ""


# ════════════════════════════════════════════════════════════
#  核心：数字提取
# ════════════════════════════════════════════════════════════

def parse_range_to_max(value: str) -> Optional[int]:
    """
    将窗口字符串解析为数字上限
    
    规则：
    - "2-3" → 3
    - "5-7" → 7
    - "7-14" → 14
    - "24-48小时" → 2（小时→天取整）
    - "2-4周" → 28（周→天）
    - "3-6个月" → 180（月→天，按30天计）
    - "不适用" / "按单次病种" / "" → None
    
    Returns:
        int（天数上限），若无法解析返回 None
    """
    if not value:
        return None
    
    value = value.strip()
    
    # 特殊字符串
    for skip_kw in ["不适用", "按单次", "按病种", "急性期小时级"]:
        if skip_kw in value:
            return None
    
    # 提取数字范围
    # 匹配 "-" 分隔的数字（如 "2-3天"、"24-48小时"、"2-4周"）
    match = re.match(r'(\d+)\s*[-~]\s*(\d+)\s*(天|小时|周|月)?', value)
    if not match:
        # 尝试单个数字（如 "72小时"）
        match = re.match(r'(\d+)\s*(天|小时|周|月)?', value)
        if not match:
            return None
        num = int(match.group(1))
        unit = match.group(2) or '天'
    else:
        num = int(match.group(2))  # 取上限
        unit = match.group(3) or '天'
    
    # 单位转换
    if unit == '小时':
        return max(1, num // 24)  # 小时转天，向上取整
    elif unit == '周':
        return num * 7
    elif unit == '月':
        return num * 30
    else:
        return num


# ════════════════════════════════════════════════════════════
#  主加载函数
# ════════════════════════════════════════════════════════════

class PrognosisDataLoader:
    """病程预后数据加载器"""
    
    def __init__(self, filepath: str = None):
        """初始化并加载数据
        
        Args:
            filepath: 预后JSON文件路径。
                      如果为None，自动搜索默认位置。
        """
        self._cards: Dict[str, PrognosisCard] = {}
        self._load_cards(filepath)
        self._build_aliases()
    
    def _find_file(self, filepath: Optional[str]) -> str:
        """自动搜索预后文件"""
        if filepath and os.path.exists(filepath):
            return filepath
        
        # 搜索策略
        search_paths = [
            os.path.join(os.path.dirname(__file__), 
                         "m4_disease_course_prognosis_window_master_001_450_v9_merged.txt"),
            "/Users/fangxuan/Downloads/m4_disease_course_prognosis_window_master_001_450_v9_merged.txt",
        ]
        for p in search_paths:
            if os.path.exists(p):
                return p
        
        raise FileNotFoundError(
            f"预后文件未找到。已搜索路径: {search_paths}"
        )
    
    def _load_cards(self, filepath: Optional[str]):
        """加载并解析所有预后卡片"""
        fp = self._find_file(filepath)
        with open(fp, 'r') as f:
            data = json.load(f)
        
        raw_cards = data.get('course_prognosis_cards', [])
        
        for raw in raw_cards:
            try:
                card = self._parse_card(raw)
                if card:
                    self._cards[card.disease_name] = card
            except Exception as e:
                # 单卡片解析失败不影响其他卡片
                continue
    
    def _parse_card(self, raw: dict) -> Optional[PrognosisCard]:
        """解析单张卡片"""
        disease_name = raw.get('diseaseName_cn', '')
        if not disease_name:
            return None
        
        # 治疗响应窗口
        trw_raw = raw.get('treatment_response_window', {})
        first = trw_raw.get('expected_first_response_days', '')
        main = trw_raw.get('expected_main_symptom_response_days', '')
        stable = trw_raw.get('expected_stable_response_days', '')
        
        trw = TreatmentResponseWindow(
            first_response_days=first,
            main_response_days=main,
            stable_response_days=stable,
            disease_type=trw_raw.get('disease_type', ''),
            interpretation=trw_raw.get('interpretation_for_m4', {}),
            first_days_max=parse_range_to_max(first) or 0,
            main_days_max=parse_range_to_max(main) or 0,
            stable_days_max=parse_range_to_max(stable) or 0,
        )
        
        # 自然病程
        nc_raw = raw.get('natural_course', {})
        def get_nested(d, *keys):
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k, {})
                else:
                    return ''
            return d if isinstance(d, str) else ''
        
        nc = NaturalCourse(
            best_onset=get_nested(nc_raw, 'best_case_course', 'onset_improvement_days'),
            best_significant=get_nested(nc_raw, 'best_case_course', 'significant_improvement_days'),
            best_resolution=get_nested(nc_raw, 'best_case_course', 'near_resolution_days'),
            typical_onset=get_nested(nc_raw, 'typical_course', 'onset_improvement_days'),
            typical_significant=get_nested(nc_raw, 'typical_course', 'significant_improvement_days'),
            typical_resolution=get_nested(nc_raw, 'typical_course', 'expected_resolution_days'),
            residual_symptoms=nc_raw.get('typical_course', {}).get('residual_symptoms', []) if isinstance(nc_raw.get('typical_course'), dict) else [],
            slow_delayed=get_nested(nc_raw, 'slow_or_poor_course', 'delayed_improvement_days'),
            slow_prolonged=get_nested(nc_raw, 'slow_or_poor_course', 'prolonged_resolution_days'),
            best_zh=nc_raw.get('best_case_course', {}).get('description_zh', '') if isinstance(nc_raw.get('best_case_course'), dict) else '',
            typical_zh=nc_raw.get('typical_course', {}).get('description_zh', '') if isinstance(nc_raw.get('typical_course'), dict) else '',
            slow_zh=nc_raw.get('slow_or_poor_course', {}).get('description_zh', '') if isinstance(nc_raw.get('slow_or_poor_course'), dict) else '',
        )
        
        # 病程偏离原因
        dev_raw = raw.get('course_deviation_reasons', {})
        dev = CourseDeviation(
            still_acceptable=dev_raw.get('still_acceptable_if', []),
            not_controlled=dev_raw.get('suggests_original_disease_not_controlled', []),
            complication_or_new=dev_raw.get('suggests_complication_or_new_problem', []),
            wrong_diagnosis=dev_raw.get('suggests_wrong_diagnosis', []),
            adherence_or_external=dev_raw.get('suggests_adherence_or_external_factor', []),
        )
        
        # M4路由策略
        route_raw = raw.get('m4_routing_policy', {})
        routing = M4RoutingPolicy(
            return_to_m2_if=route_raw.get('return_to_m2_if', []),
            return_to_m1_if=route_raw.get('return_to_m1_if', []),
            emergency_stop_if=route_raw.get('emergency_stop_if', []),
            do_not_overreact_if=route_raw.get('do_not_overreact_if', []),
            must_recheck_or_refer_if=route_raw.get('must_recheck_or_refer_if', []),
        )
        
        # M4使用策略
        usage_raw = raw.get('m4_usage_policy', {})
        usage = [
            *usage_raw.get('can_use_for_route', []),
            *[f"【不可用】{x}" for x in usage_raw.get('cannot_use_for_route', [])],
        ] if usage_raw else []
        
        # 客观预后
        prog_raw = raw.get('objective_prognosis', {})
        overall = prog_raw.get('overall_prognosis_zh', '') if isinstance(prog_raw, dict) else ''
        
        return PrognosisCard(
            disease_name=disease_name,
            course_category=raw.get('course_category', ''),
            treatment_window=trw,
            natural_course=nc,
            deviation=dev,
            routing_policy=routing,
            m4_usage_policy=usage,
            overall_prognosis=overall,
        )
    
    def _build_aliases(self):
        """构建别名映射
        
        支持多种形式的别名匹配：
        - 去括号短名： "高血压 (Hypertension)" 入参 → "高血压" 卡片名
        - 儿科前缀匹配：KB中"急性扁桃体炎" → 卡片"儿童急性扁桃体炎"
        - 包容性匹配：卡片名包含/被包含在入参中
        """
        aliases = {}
        
        # 1. 去括号短名（卡片自身的短名）
        for name in list(self._cards.keys()):
            short = name.split("（")[0].split(" (")[0].strip()
            if short != name and short not in self._cards:
                aliases[short] = self._cards[name]
        
        # 2. 儿科前缀匹配
        for name in list(self._cards.keys()):
            for prefix in ["儿童", "小儿", "新生儿", "婴儿"]:
                if name.startswith(prefix):
                    without_prefix = name[len(prefix):]
                    if without_prefix and without_prefix not in self._cards and without_prefix not in aliases:
                        aliases[without_prefix] = self._cards[name]
        
        # 3. 反向：卡片名是"XX"，入参可能是"XX病/症/炎"等
        #   如卡片"支气管肺炎"，入参可能是"支气管肺炎病"
        #   但这种情况由 get_card 的部分匹配处理
        
        self._aliases = aliases
    
    def get_card(self, disease_name: str) -> Optional[PrognosisCard]:
        """按疾病名获取预后卡片
        
        匹配策略（按优先级）：
        1. 精确匹配（卡片名）
        2. 别名匹配（_aliases）
        3. 入参去括号短名匹配
        4. 入参去前缀匹配（去掉"儿童/小儿/新生儿"等）
        5. 部分包含匹配（卡片名包含入参或反之）
        """
        # Step 1: 精确匹配
        if disease_name in self._cards:
            return self._cards[disease_name]
        
        # Step 2: 别名匹配
        if disease_name in self._aliases:
            return self._aliases[disease_name]
        
        # Step 3: 入参去括号短名
        short = disease_name.split("（")[0].split(" (")[0].strip()
        if short != disease_name:
            if short in self._cards:
                return self._cards[short]
            if short in self._aliases:
                return self._aliases[short]
        
        # Step 4: 入参去儿科前缀
        cleaned = short
        for prefix in ["儿童", "小儿", "新生儿", "婴儿", "老年", "老年人"]:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                without = cleaned[len(prefix):]
                if without in self._cards:
                    return self._cards[without]
                if without in self._aliases:
                    return self._aliases[without]
        
        # Step 5: 部分包含匹配（仅用于儿科前缀等确定性场景）
        # 原则：只有当查询名可以被视为卡片名的**前缀版**时才匹配。
        #   如"急性扁桃体炎"→"儿童急性扁桃体炎"（加儿科前缀）
        #   拒绝"高血压"→"妊娠期高血压"（添加了不同含义的前缀）
        #
        # 实现：当查询名是卡片名的子串时，检查差集是否仅为已知的无害前缀
        
        # 已知的无害前缀（加入后不改变疾病含义）
        HARMLESS_PREFIXES = ["儿童", "小儿", "新生儿", "婴儿", "老年", "老年人"]
        
        # 4字以内、仅前缀差异的匹配（卡片名 = 前缀 + 查询名）
        for name in self._cards:
            if cleaned in name and name != cleaned:
                diff = name.replace(cleaned, "", 1).strip()
                # 差集必须在开头且是已知无害前缀
                if name.startswith(diff) and diff in HARMLESS_PREFIXES:
                    return self._cards[name]
                # 也检查以"性"结尾的差异（如"感染性发热"→"发热"不行）
                # 这个太宽泛了，不做
        
        # 查询名包含卡片名（如"儿童支气管肺炎"→卡片"支气管肺炎"）
        for name in self._cards:
            if name in cleaned and cleaned != name:
                diff = cleaned.replace(name, "", 1).strip()
                if cleaned.endswith(name) or cleaned.startswith(name):
                    # 差集必须是已知无害前缀
                    if diff in HARMLESS_PREFIXES:
                        return self._cards[name]
        
        return None
    
    def get_window_dict(self, disease_name: str) -> Optional[Dict]:
        """获取疾病窗口字典（兼容 m4_kb_mapper 格式）
        
        Returns:
            {"onset": int, "significant": int} 或 None
        """
        card = self.get_card(disease_name)
        if not card:
            return None
        
        tw = card.treatment_window
        if tw.first_days_max > 0 and tw.main_days_max > 0:
            return {"onset": tw.first_days_max, "significant": tw.main_days_max}
        return None
    
    def has_card(self, disease_name: str) -> bool:
        """检查疾病是否有预后卡片"""
        return self.get_card(disease_name) is not None
    
    @property
    def card_count(self) -> int:
        return len(self._cards)
    
    @property
    def disease_names(self) -> List[str]:
        return list(self._cards.keys())
    
    def get_all_cards(self) -> Dict[str, PrognosisCard]:
        return dict(self._cards)


# ════════════════════════════════════════════════════════════
#  便捷函数
# ════════════════════════════════════════════════════════════

_default_loader: Optional[PrognosisDataLoader] = None


def load_prognosis_data(filepath: str = None) -> PrognosisDataLoader:
    """获取预后数据加载器（单例）"""
    global _default_loader
    if _default_loader is None or filepath is not None:
        _default_loader = PrognosisDataLoader(filepath)
    return _default_loader


def get_prognosis_card(disease_name: str) -> Optional[PrognosisCard]:
    """便捷函数：获取预后卡片"""
    loader = load_prognosis_data()
    return loader.get_card(disease_name)


# ════════════════════════════════════════════════════════════
#  独立测试
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = load_prognosis_data()
    print(f"加载了 {loader.card_count} 张预后卡片")
    print()

    # 测试几个疾病
    for name in ["急性上呼吸道感染", "高血压", "带状疱疹", "慢性胃炎", "儿童肺炎", "湿疹"]:
        card = loader.get_card(name)
        if card:
            tw = card.treatment_window
            print(f"=== {name} ===")
            print(f"  窗口: 首次={tw.first_response_days}, 主症={tw.main_response_days}, 稳定={tw.stable_response_days}")
            print(f"  解析: onset={tw.first_days_max}, significant={tw.main_days_max}")
            print(f"  路由→M2: {card.routing_policy.return_to_m2_if[:3]}...")
            print(f"  紧急停止: {card.routing_policy.emergency_stop_if}")
            wd = loader.get_window_dict(name)
            print(f"  窗口字典: {wd}")
            print()
    
    # 未匹配测试
    for name in ["咳嗽", "失眠"]:
        card = loader.get_card(name)
        print(f"{name}: {'有卡片' if card else '无卡片'}")
    
    # KB别名匹配测试
    for name in ["高血压 (Hypertension)", "慢性胃炎 (Chronic Gastritis)"]:
        card = loader.get_card(name)
        print(f"{name}: {'有卡片' if card else '无卡片'}")
    
    # 覆盖统计
    with open("data/m2_formula_knowledge.json") as f:
        kb = json.load(f)
    
    kb_short = set()
    for k in kb:
        s = k.split("（")[0].split(" (")[0].strip()
        kb_short.add(s)
    
    overlap = sum(1 for s in kb_short if loader.has_card(s))
    print(f"\nKB疾病数: {len(kb_short)}")
    print(f"预后文件中: {loader.card_count}")
    print(f"与KB重叠: {overlap}")
    print(f"KB中无预后卡片: {len(kb_short) - overlap}")
