"""
M4 知识库数据映射模块
======================
职责：
1. 从 m2_formula_knowledge.json（625 个疾病）提取数据
2. 根据疾病名称自动分类并推算病程窗口（onset/significant 天数）
3. 从每个证型的 trigger 字段提取核心/典型症状关键词
4. 为 M4RoutingEngine 提供 DISEASE_WINDOWS 和 core_symptoms 数据
5. 所有逻辑通过代码确定性执行，不需要 LLM 参与

映射策略：
- 疾病名称包含特定关键词 -> 推算窗口
- Disease -> Acute/Subacute/Chronic 分类
- 核心症状从 trigger 中提取

用法：
    from m4_kb_mapper import build_m4_data_from_kb
    windows, core_symptoms = build_m4_data_from_kb("data/m2_formula_knowledge.json")
"""

import json
import re
from typing import Dict, List, Tuple, Optional
from collections import Counter


# ════════════════════════════════════════════════════════════
#  疾病窗口分类推算规则（基于疾病名称关键词）
# ════════════════════════════════════════════════════════════

ACUTE_KEYWORDS = [
    "急性", "急性期", "突发", "骤发", "初起", "新感",
    "感冒", "上呼吸道感染", "中暑", "冻伤", "烧伤",
    "中毒", "外伤", "创伤", "扭伤", "骨折",
    "出血", "咯血", "呕血", "便血", "鼻衄",
    "惊厥", "抽搐", "癫痫发作",
    "热性惊厥", "中耳炎", "肺炎", "支气管炎",
    "扁桃体炎", "咽炎", "喉炎", "会厌炎",
    "结膜炎", "角膜炎", "睑腺炎", "泪囊炎",
    "乳腺炎", "胰腺炎", "胆囊炎", "阑尾炎",
    "肠胃炎", "肠炎", "痢疾", "腹泻",
    "尿路感染", "膀胱炎", "前列腺炎",
    "盆腔炎", "阴道炎", "宫颈炎",
    "皮炎", "荨麻疹", "药疹", "虫咬",
    "带状疱疹", "单纯疱疹", "水痘", "风疹",
    "麻疹", "猩红热", "百日咳", "流感",
    "新冠", "手足口病", "腮腺炎",
    "新生儿黄疸",
    "急性肾小球肾炎", "急性肾损伤",
    "心肌炎", "心包炎",
    "脑膜炎", "脑炎",
    "化脓性", "感染性", "败血症", "脓毒症",
    "气性坏疽", "破伤风",
    "产褥感染",
    "异位妊娠", "前置胎盘",
    "鼻出血", "鼻衄",
    "新生儿缺氧缺血性脑病",
    "小儿热性惊厥",
]

SUBACUTE_KEYWORDS = [
    "亚急性", "迁延", "反复", "复发性",
    "咳嗽", "慢性咳嗽",
    "偏头痛", "头痛",
    "眩晕", "梅尼埃",
    "失眠",
    "焦虑", "抑郁",
    "功能性", "消化不良",
    "胃食管反流", "反流性食管炎",
    "便秘",
    "痤疮", "粉刺",
    "湿疹", "特应性皮炎",
    "玫瑰糠疹", "单纯糠疹",
    "痒疹",
    "荨麻疹性血管炎",
]

CHRONIC_KEYWORDS = [
    "慢性", "慢性期", "久", "老",
    "高血压", "糖尿病", "高脂血症",
    "冠心病", "心衰", "心力衰竭",
    "慢阻肺", "肺心病",
    "肝硬化", "肝炎",
    "肾炎", "肾衰竭",
    "胃炎", "萎缩性胃炎",
    "结肠炎", "克罗恩",
    "关节炎", "类风湿", "骨关节炎",
    "痛风",
    "颈椎病", "腰椎", "椎间盘",
    "骨质疏松",
    "前列腺增生", "前列腺炎",
    "肿瘤", "癌", "瘤", "增生",
    "结节", "息肉",
    "囊肿",
    "白癜风", "银屑病",
    "斑秃",
    "肥胖",
    "脑梗", "脑出血", "中风",
    "痴呆", "阿尔茨海默",
    "帕金森",
    "耳鸣", "耳聋",
    "青光眼", "白内障",
    "视网膜",
    "性早熟", "矮小",
    "多动", "抽动",
    "遗尿", "尿频",
    "汗证",
    "厌食",
    "贫血", "再障",
    "甲亢", "甲减", "甲状腺",
    "系统性红斑狼疮",
    "干燥综合征",
    "硬皮病",
    "皮肌炎",
    "白塞病",
    "天疱疮",
    "肾病综合征",
    "IgA",
    "膜性肾病",
    "月经", "痛经", "闭经", "崩漏",
    "不孕", "不育",
    "更年期",
    "阳痿", "早泄",
    "静脉曲张", "脉管炎",
    "淋巴水肿",
    "褥疮", "压疮",
    "瘘管",
    "痤疮后遗症",
    "萎缩", "退行",
    "肥厚",
    "角化",
    "遗传",
    "性功能障碍",
    "男性迟发性",
    "肌少症", "衰弱",
    "营养不良",
    "佝偻病",
    "发育",
    "牙周病",
    "骨与关节结核",
    "淋巴结结核",
    "精索静脉曲张",
    "心脏瓣膜病",
    "心绞痛",
    "动脉硬化",
    "动脉粥样硬化",
    "下肢动脉硬化闭塞症",
    "血栓闭塞性脉管炎",
    "血管性痴呆",
    "帕金森",
    "运动神经元病",
    "重症肌无力",
    "进行性",
]

PEDIATRIC_KEYWORDS = [
    "小儿", "儿童", "婴儿", "新生儿", "幼儿",
    "佝偻病",
]

WINDOW_CONFIG = {
    "acute": {"onset": 3, "significant": 7},
    "subacute": {"onset": 7, "significant": 14},
    "chronic": {"onset": 14, "significant": 30},
    "pediatric_acute": {"onset": 2, "significant": 5},
    "pediatric_chronic": {"onset": 7, "significant": 21},
}

DEFAULT_WINDOW = {"onset": 7, "significant": 14}


def get_short_name(disease_name: str) -> str:
    """获取疾病短名（去掉括号内的英文/拉丁文注释）"""
    return disease_name.split(" (")[0].split("（")[0].strip()


def classify_disease_window(disease_name: str) -> Dict:
    """根据疾病名称自动分类并推算病程窗口"""
    short = get_short_name(disease_name)

    is_pediatric = any(kw in short for kw in PEDIATRIC_KEYWORDS)
    is_acute = any(kw in short for kw in ACUTE_KEYWORDS)

    if is_acute:
        if is_pediatric:
            return dict(WINDOW_CONFIG["pediatric_acute"])
        return dict(WINDOW_CONFIG["acute"])

    is_chronic = any(kw in short for kw in CHRONIC_KEYWORDS)
    if is_chronic:
        if is_pediatric:
            return dict(WINDOW_CONFIG["pediatric_chronic"])
        return dict(WINDOW_CONFIG["chronic"])

    is_subacute = any(kw in short for kw in SUBACUTE_KEYWORDS)
    if is_subacute:
        return dict(WINDOW_CONFIG["subacute"])

    return dict(DEFAULT_WINDOW)


# ════════════════════════════════════════════════════════════
#  核心症状提取（从 trigger 中提取）
# ════════════════════════════════════════════════════════════

SYMPTOM_WHITELIST = [
    # 全身症状
    "发热", "恶寒", "恶风", "寒战", "畏寒", "身热不扬",
    "头痛", "头晕", "眩晕",
    "乏力", "疲劳", "倦怠", "神疲", "懒言", "少气",
    "消瘦", "肥胖", "体重增加", "体重下降",
    "自汗", "盗汗", "多汗", "无汗",
    "浮肿", "水肿", "肿胀",
    "五心烦热", "潮热", "烘热",
    # 呼吸/咳嗽
    "咳嗽", "咳痰", "干咳", "呛咳",
    "痰多", "痰少", "痰黄", "痰白", "痰稀", "痰稠", "泡沫痰",
    "气喘", "气促", "气急", "气短", "呼吸困难", "胸闷",
    "咽痛", "咽痒", "咽干", "咽喉肿痛", "吞咽困难", "声音嘶哑",
    "鼻塞", "流涕", "清涕", "黄涕", "喷嚏", "鼻痒", "鼻衄",
    # 消化
    "口渴", "口干", "口苦", "口臭", "口疮", "口糜",
    "恶心", "呕吐", "嗳气", "反酸", "烧心", "呃逆",
    "腹痛", "腹胀", "胃痛", "胃胀", "胃脘痛", "胃脘胀满",
    "腹泻", "便秘", "便溏", "大便干", "大便稀", "便血", "里急后重",
    "纳呆", "纳差", "食欲不振", "多食", "善饥",
    # 泌尿/生殖
    "尿频", "尿急", "尿痛", "尿黄", "尿少", "尿浊", "尿血",
    "遗尿", "小便不利", "小便清长",
    "月经不调", "痛经", "闭经", "崩漏", "经期延长", "月经过多",
    "带下", "白带", "黄带",
    "阳痿", "早泄", "遗精",
    # 心血管
    "心悸", "心慌", "胸痛", "胸闷", "胸胁胀满",
    # 神经/精神
    "失眠", "多梦", "早醒", "入睡困难", "睡眠不宁",
    "烦躁", "焦虑", "易怒", "抑郁", "情绪低落", "急躁",
    "抽搐", "惊厥", "震颤", "麻木", "肢体拘急",
    "抽动", "多动", "注意力不集中",
    # 皮肤
    "皮疹", "瘙痒", "红斑", "丘疹", "水疱", "脓疱", "风团",
    "糜烂", "渗液", "结痂", "脱屑", "鳞屑", "苔藓化",
    "色斑", "白斑", "黑斑", "黄褐斑",
    # 肌肉骨骼
    "关节痛", "关节肿", "关节红肿", "关节屈伸不利",
    "腰痛", "颈痛", "肩痛", "背痛", "肢体疼痛",
    "腰膝酸软", "下肢无力",
    # 眼耳
    "目赤", "目干", "目痒", "畏光", "流泪", "视力模糊", "视力下降",
    "耳鸣", "耳聋", "听力下降",
    "眼睑红肿", "眼睑下垂",
    # 儿科
    "夜啼", "磨牙", "流涎",
]


def extract_core_symptoms_from_trigger(trigger_text: str) -> List[str]:
    """从证型 trigger 描述中提取核心症状关键词

    规则：
    1. 在症状白名单中匹配
    2. 保持出现顺序
    3. 去重
    4. 最多返回 15 个
    """
    if not trigger_text:
        return []

    seen = set()
    result = []

    for sym in SYMPTOM_WHITELIST:
        if sym in trigger_text and sym not in seen:
            seen.add(sym)
            result.append(sym)

    return result[:15]


# ════════════════════════════════════════════════════════════
#  主构建函数
# ════════════════════════════════════════════════════════════

def build_m4_data_from_kb(kb_path: str = "data/m2_formula_knowledge.json") -> Tuple[Dict, Dict]:
    """从知识库构建 M4 所需的窗口数据和核心症状数据

    Returns:
        (disease_windows, core_symptoms)
        - disease_windows: 键=疾病名(原始名或短名), 值={"onset": int, "significant": int}
        - core_symptoms: 键=疾病名(原始名或短名), 值=[症状1, 症状2, ...]
    """
    try:
        with open(kb_path, "r") as f:
            kb = json.load(f)
    except FileNotFoundError:
        import os
        alt_path = os.path.join(os.path.dirname(__file__), kb_path)
        with open(alt_path, "r") as f:
            kb = json.load(f)

    disease_windows = {}
    core_symptoms = {}

    for disease_name, disease_data in kb.items():
        if not isinstance(disease_data, dict):
            continue

        short_name = get_short_name(disease_name)

        # 窗口判断
        window = classify_disease_window(disease_name)
        disease_windows[short_name] = window
        disease_windows[disease_name] = window

        # 核心症状提取
        syndromes = disease_data.get("syndromes", {})
        if not syndromes:
            continue

        all_symptoms = []
        for syndrome_data in syndromes.values():
            if not isinstance(syndrome_data, dict):
                continue
            trigger = syndrome_data.get("trigger", "")
            extracted = extract_core_symptoms_from_trigger(trigger)
            all_symptoms.extend(extracted)

        if all_symptoms:
            seen = set()
            unique = []
            for s in all_symptoms:
                if s not in seen:
                    seen.add(s)
                    unique.append(s)
            core_symptoms[short_name] = unique[:15]
            core_symptoms[disease_name] = unique[:15]

    return disease_windows, core_symptoms


# ════════════════════════════════════════════════════════════
#  独立测试入口
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os

    script_dir = os.path.dirname(os.path.abspath(__file__))
    kb_path = os.path.join(script_dir, "data", "m2_formula_knowledge.json")

    windows, core_symptoms = build_m4_data_from_kb(kb_path)

    print(f"=== 疾病窗口数据 ===")
    print(f"共 {len(windows)} 条窗口数据")
    print()

    samples = [
        "急性上呼吸道感染", "急性支气管炎", "儿童肺炎",
        "慢性胃炎", "高血压", "头痛",
        "咳嗽", "糖尿病", "小儿反复呼吸道感染",
        "抽动障碍", "荨麻疹", "带状疱疹",
        "失眠", "便秘", "新生儿黄疸",
        "湿疹", "银屑病", "甲状腺功能亢进症",
        "注意缺陷多动障碍", "小儿汗证",
    ]
    for s in samples:
        w = windows.get(s, "Not found")
        sym = core_symptoms.get(s, [])
        print(f"  {s}")
        print(f"    窗口: {w}")
        print(f"    症状: {sym[:6]}")

    print()
    print(f"=== 统计 ===")
    type_count = Counter()
    for name in windows:
        short = get_short_name(name)
        if name == short:
            w = windows[name]
            key = f"急性(3/7)" if w == WINDOW_CONFIG["acute"] else \
                  f"儿科急性(2/5)" if w == WINDOW_CONFIG["pediatric_acute"] else \
                  f"慢性(14/30)" if w == WINDOW_CONFIG["chronic"] else \
                  f"儿科慢性(7/21)" if w == WINDOW_CONFIG["pediatric_chronic"] else \
                  f"亚急性(7/14)" if w == WINDOW_CONFIG["subacute"] else \
                  f"默认(7/14)" if w == DEFAULT_WINDOW else \
                  f"其他({w['onset']}/{w['significant']})"
            type_count[key] += 1

    for cat, count in sorted(type_count.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    has_symptoms = sum(1 for v in core_symptoms.values() if v)
    total_diseases = len([k for k in windows if k == get_short_name(k)])
    print(f"\n  疾病总数: {total_diseases}")
    print(f"  有核心症状的疾病: {has_symptoms}")
    print(f"  总窗口条目(含别名): {len(windows)} 条")
