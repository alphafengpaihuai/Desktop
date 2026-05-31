"""
M3 临床药学审核引擎 v3
=======================
职责（只做三件事，不删药不改方）：
1. 十八反十九畏检查 → LLM 清洗炮制别名 + 代码精确匹配
2. 剂量按年龄/体重调整 + 特殊人群处理 → 代码执行
3. 毒性药物标注警告 → 代码执行（数据源：m3_herb_knowledge.json）
"""
import json
import re
from typing import Dict, List, Optional, Tuple


# ── 十八反基矩阵（原始药名，不含炮制） ─────────────
OPPOSITE_GROUPS = [
    ("乌头类", {"川乌", "草乌", "附子", "天雄"}),
    ("贝母类", {"川贝母", "浙贝母", "平贝母", "湖北贝母"}),
    ("瓜蒌类", {"瓜蒌", "瓜蒌皮", "瓜蒌仁", "天花粉"}),
    ("半夏类", {"半夏"}),
    ("白蔹", {"白蔹"}),
    ("白及", {"白及"}),
    ("藜芦", {"藜芦"}),
    ("人参类", {"人参", "西洋参", "党参", "太子参", "沙参"}),
    ("丹参", {"丹参"}),
    ("玄参", {"玄参"}),
    ("苦参", {"苦参"}),
    ("细辛", {"细辛"}),
    ("芍药类", {"白芍", "赤芍"}),
]

# 十八反关系对
OPPOSITE_PAIRS = [
    ("乌头类", "贝母类"), ("乌头类", "瓜蒌类"), ("乌头类", "半夏类"),
    ("乌头类", "白蔹"), ("乌头类", "白及"),
    ("藜芦", "人参类"), ("藜芦", "丹参"), ("藜芦", "玄参"),
    ("藜芦", "苦参"), ("藜芦", "细辛"), ("藜芦", "芍药类"),
]

# 十九畏关系对
FEAR_PAIRS = [
    ("硫黄", "朴硝"), ("水银", "砒霜"), ("狼毒", "密陀僧"),
    ("巴豆", "牵牛子"), ("丁香", "郁金"), ("牙硝", "三棱"),
    ("川乌", "犀角"), ("草乌", "犀角"), ("人参", "五灵脂"),
    ("官桂", "赤石脂"),
]


class M3ClinicalReviewEngine:
    """M3 临床药学审核引擎 v3 — 只做安全审核，不做方向裁剪"""

    def __init__(self, herb_knowledge_path: str = "data/m3_herb_knowledge.json"):
        self.herb_kb = self._load_json(herb_knowledge_path, default={})

    # ══════════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════════

    def review(self, herbs: List[str], patient: Dict) -> Dict:
        """审核处方安全性"""
        warnings = []

        # 1. 十八反十九畏
        oppo, fear, alias_prompt = self._check_eighteen_nineteen(herbs)
        for h1, h2 in oppo:
            warnings.append(f"⚠ 十八反：{h1} 与 {h2} 相反，标注风险")
        for h1, h2 in fear:
            warnings.append(f"⚠ 十九畏：{h1} 与 {h2} 相畏，标注风险")

        # 2. 毒性药物（从知识库读取）
        toxicity = []
        for h in herbs:
            info = self.herb_kb.get(h, {})
            tox = info.get("toxicity", None)
            if tox:
                toxicity.append({
                    "herb": h,
                    "level": tox.get("level", ""),
                    "max_dose": tox.get("max_dose", ""),
                    "note": tox.get("note", ""),
                    "monitoring": tox.get("monitoring", ["需监测肝肾功能"]),
                })

        # 3. 剂量调整 + 特殊人群（合并为一个方法）
        dose_notes, pop_notes = self._check_population_dosage(herbs, patient)

        return {
            "review_decision": "APPROVED",
            "eighteen_opposites": [{"herb_a": h1, "herb_b": h2} for h1, h2 in oppo],
            "nineteen_fears": [{"herb_a": h1, "herb_b": h2} for h1, h2 in fear],
            "toxicity_warnings": toxicity,
            "dose_adjustments": dose_notes,
            "special_population": pop_notes,
            "alias_warnings": alias_prompt,
            "warnings": warnings,
            "m2_return_needed": bool(oppo or fear),
        }

    # ══════════════════════════════════════════════════════
    #  十八反十九畏（LLM 清洗别名 + 代码精确匹配）
    # ══════════════════════════════════════════════════════

    def _check_eighteen_nineteen(self, herbs: List[str]) -> Tuple[List, List, str]:
        """先用 LLM 将炮制别名标准化为基名，再用集合匹配"""
        alias_prompt = ""
        base_herbs = self._normalize_herb_names(herbs)
        if base_herbs != herbs:
            alias_prompt = f"炮制名映射：{' → '.join(f'{a}→{b}' for a, b in zip(herbs, base_herbs) if a != b)}"

        herb_set = set(base_herbs)

        # 构建基名到组的映射
        herb_to_group = {}
        for group_name, herbs_in_group in OPPOSITE_GROUPS:
            for h in herbs_in_group:
                herb_to_group[h] = group_name

        # 检测十八反
        opposite_conflicts = []
        found_groups = set()
        for h in herb_set:
            g = herb_to_group.get(h)
            if g:
                found_groups.add((h, g))

        herb_in_groups = {h: g for h, g in found_groups}
        herb_names = list(herb_in_groups.keys())

        for i in range(len(herb_names)):
            for j in range(i + 1, len(herb_names)):
                h1, g1 = herb_names[i], herb_in_groups[herb_names[i]]
                h2, g2 = herb_names[j], herb_in_groups[herb_names[j]]
                if (g1, g2) in OPPOSITE_PAIRS or (g2, g1) in OPPOSITE_PAIRS:
                    opposite_conflicts.append((h1, h2))

        # 检测十九畏
        fear_conflicts = []
        for h1, h2 in FEAR_PAIRS:
            if h1 in herb_set and h2 in herb_set:
                fear_conflicts.append((h1, h2))

        return opposite_conflicts, fear_conflicts, alias_prompt

    def _normalize_herb_names(self, herbs: List[str]) -> List[str]:
        """将炮制/别名标准化为基名——先用代码映射，再用 LLM 兜底"""
        # 代码映射表
        alias_map = {
            "制川乌": "川乌", "制草乌": "草乌", "制附子": "附子",
            "淡附片": "附子", "炮附子": "附子", "黑顺片": "附子",
            "白附片": "附子", "盐附子": "附子",
            "法半夏": "半夏", "姜半夏": "半夏", "清半夏": "半夏",
            "法夏": "半夏",
            "制南星": "天南星", "胆南星": "天南星",
            "炒白芍": "白芍", "酒白芍": "白芍", "醋白芍": "白芍",
            "炒白术": "白术",
            "炙黄芪": "黄芪", "生黄芪": "黄芪",
            "炙甘草": "甘草", "生甘草": "甘草",
            "炒甘草": "甘草",
            "醋鳖甲": "鳖甲", "生鳖甲": "鳖甲",
            "煅龙骨": "龙骨", "生龙骨": "龙骨",
            "煅牡蛎": "牡蛎", "生牡蛎": "牡蛎",
            "炒酸枣仁": "酸枣仁",
            "炒麦芽": "麦芽", "焦麦芽": "麦芽",
            "炒谷芽": "谷芽", "焦谷芽": "谷芽",
            "炒山楂": "山楂", "焦山楂": "山楂",
            "炒神曲": "神曲", "焦神曲": "神曲",
            "炒鸡内金": "鸡内金",
            "炒薏苡仁": "薏苡仁", "麸炒薏苡仁": "薏苡仁",
            "盐黄柏": "黄柏", "酒黄柏": "黄柏", "黄柏炭": "黄柏",
            "酒大黄": "大黄", "熟大黄": "大黄", "大黄炭": "大黄",
            "醋香附": "香附",
            "酒当归": "当归", "当归身": "当归", "当归尾": "当归",
            "酒川芎": "川芎",
            # 熟地黄/生地黄功效不同，不映射
            "酒黄芩": "黄芩",
            "蜜麻黄": "麻黄", "炙麻黄": "麻黄",
            "炒牛蒡子": "牛蒡子",
            "炒紫苏子": "紫苏子",
            "炒莱菔子": "莱菔子",
            "炒芥子": "白芥子",
            "炒王不留行": "王不留行",
            "醋延胡索": "延胡索",
            "醋乳香": "乳香",
            "醋没药": "没药",
            "煅石膏": "石膏",
            "蜜紫菀": "紫菀",
            "蜜款冬花": "款冬花",
            "蜜百部": "百部",
            "蜜枇杷叶": "枇杷叶",
            # 常见药物名标准化
            "龙胆草": "龙胆",
            "生石膏": "石膏",
            "生地黄": "生地黄",  # 保留原样
            "熟地黄": "熟地黄",  # 保留原样
            # 补充：更多炮制名
            "生川乌": "川乌", "生草乌": "草乌",
            "生半夏": "半夏", "竹沥半夏": "半夏",
            "生天南星": "天南星",
            "生附子": "附子", "炮天雄": "天雄",
            "酒白芍": "白芍",
            "炒甘草": "甘草", "蜜甘草": "甘草",
            "姜黄连": "黄连", "酒黄连": "黄连", "吴黄连": "黄连",
            "姜竹茹": "竹茹",
            "姜厚朴": "厚朴",
            "酒山茱萸": "山茱萸",
            "醋五味子": "五味子",
            "蜜远志": "远志",
            "炒枳壳": "枳壳", "麸炒枳壳": "枳壳",
            "炒枳实": "枳实",
            "炮姜": "干姜",
            "煨肉蔻": "肉豆蔻",
            "煨木香": "木香",
            "醋莪术": "莪术",
            "醋三棱": "三棱",
            "酒女贞子": "女贞子",
            "盐杜仲": "杜仲",
            "炒蒲黄": "蒲黄", "蒲黄炭": "蒲黄",
            "炒地榆": "地榆", "地榆炭": "地榆",
            "炒槐花": "槐花", "槐花炭": "槐花",
        }

        result = []
        need_llm = False
        for h in herbs:
            if h in alias_map:
                result.append(alias_map[h])
            elif h in self.herb_kb:
                result.append(h)
            else:
                result.append(h)
                need_llm = True

        # 如有无法识别的药名（≥2个），才调用 LLM 做别名清洗
        # 仅1个未知药名时，LLM 调用成本高收益低，直接保留原样
        if need_llm and self._llm_available() and len([h for h in herbs if h not in alias_map and h not in self.herb_kb]) >= 2:
            llm_result = self._llm_normalize(herbs, result)
            if llm_result:
                result = llm_result

        return result

    def _llm_normalize(self, raw_herbs: List[str], code_mapped: List[str]) -> Optional[List[str]]:
        """LLM 兜底：清洗未知炮制别名"""
        unknowns = {r for r, m in zip(raw_herbs, code_mapped) if r != m}
        prompt = f"""你是一位中药师。标准化以下中药炮制名为基名。

## 药名
{'、'.join(raw_herbs)}

## 规则
- 制川乌→川乌，法半夏→半夏，炒白芍→白芍，依此类推
- 基名必须是《中药学》教材标准名

## 输出 JSON
{{"mappings": [{{"raw": "原药名", "base": "基名"}}]}}"""

        result = self._call_llm(prompt)
        if not result:
            return None
        try:
            m = re.search(r"\{.*\}", result, re.DOTALL)
            parsed = json.loads(m.group())
            mappings = {item["raw"]: item["base"] for item in parsed.get("mappings", [])}
            # 只对未知的进行映射
            mapped = []
            for r in raw_herbs:
                if r in mappings:
                    mapped.append(mappings[r])
                else:
                    mapped.append(r)
            return mapped
        except (json.JSONDecodeError, AttributeError, KeyError):
            return None

    # ══════════════════════════════════════════════════════
    #  剂量调整 + 特殊人群（合并为一个方法）
    # ══════════════════════════════════════════════════════

    def _check_population_dosage(self, herbs: List[str], patient: Dict) -> Tuple[List[Dict], List[str]]:
        """剂量调整建议 + 特殊人群提醒（合并，避免重复判断）"""
        dose_notes = []
        pop_notes = []

        try:
            age = float(patient.get("age", 0) or 0)
            weight = float(patient.get("weight", 0) or 0)
        except (ValueError, TypeError):
            age, weight = 0, 0

        pregnancy = patient.get("pregnancy", False)
        lactation = patient.get("lactation", False)

        # 儿童
        if 0 < age <= 14:
            if weight > 0:
                ratio = weight / 60
            elif age <= 1:
                ratio = 0.25
            elif age <= 3:
                ratio = 0.25 + (age - 1) * 0.04  # 1-3岁 0.25~0.33
            elif age <= 7:
                ratio = 0.33 + (age - 3) * 0.0425  # 3-7岁 0.33~0.50
            elif age <= 14:
                ratio = 0.50 + (age - 7) * 0.024  # 7-14岁 0.50~0.67
            else:
                ratio = 0.67
            ratio = min(max(ratio, 0.2), 1.0)
            for h in herbs:
                dose_notes.append({
                    "herb": h, "age_group": "儿童", "age": age,
                    "dosage_ratio": f"建议按成人量的{ratio:.0%}折算",
                    "note": "器官发育未成熟，毒性药物减量或禁用",
                })
            pop_notes.append(f"儿童({age}岁)，剂量按{'体重' if weight > 0 else '年龄'}折算为成人量的{ratio:.0%}")

        # 老年人
        elif age >= 65:
            for h in herbs:
                info = self.herb_kb.get(h, {})
                if info.get("toxicity"):
                    dose_notes.append({
                        "herb": h, "age_group": "老年人", "age": age,
                        "dosage_ratio": "建议按成人量的2/3-1/2",
                        "note": f"毒性药 {h} 应减量，注意肝肾功能",
                    })
                    break
            pop_notes.append(f"老年人({age}岁)，肝肾功能减退者酌减，注意多药联用风险")

        # 孕妇
        if pregnancy:
            for h in herbs:
                info = self.herb_kb.get(h, {})
                tox = info.get("toxicity", {})
                if tox:
                    dose_notes.append({
                        "herb": h, "age_group": "孕妇",
                        "dosage_ratio": "禁用",
                        "note": f"孕妇禁用毒性药 {h}",
                    })
            pop_notes.append("孕妇，严格避免致畸/致流产药物")

        # 哺乳期
        if lactation:
            pop_notes.append("哺乳期，注意药物透乳风险")

        return dose_notes, pop_notes

    # ══════════════════════════════════════════════════════
    #  辅助
    # ══════════════════════════════════════════════════════

    def _load_json(self, path: str, default: Optional[Dict] = None) -> Dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default or {}

    def _llm_available(self):
        try:
            from m1_engine import M1DiagnosisEngine
            return M1DiagnosisEngine().llm_api_available()
        except Exception:
            return False

    def _call_llm(self, prompt: str) -> Optional[str]:
        try:
            from m1_engine import M1DiagnosisEngine
            return M1DiagnosisEngine()._call_llm(prompt, temperature=0.1, max_tokens=500)
        except Exception:
            return None
