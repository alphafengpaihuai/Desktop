"""
m2_multi_axis_matrix_builder.py
================================
守一 CDSS — M2-0 症状多轴触发矩阵生成器

问题：
  规则库中每个 pathology_axis 只写了自己的 possible_tcm_pathogenesis，
  缺乏症状（特别是症状组合）到多轴并集的映射。
  靠临床案例一个一个补太慢。

方案：
  1. 从现有规则库的 allowed_when/not_allowed_when 中提取症状→轴关联
  2. 自动计算症状之间的共现关系（co-occurrence matrix）
  3. 用 LLM 批量校验和补充矩阵缺漏（可选）
  4. 输出独立 JSON 文件 services/m2_symptom_multi_axis_matrix.json
  5. 桥接层加载此矩阵，替代硬编码的 SYMPTOM_AXIS_TRIGGERS
"""

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional


class M2MultiAxisMatrixBuilder:
    """
    症状多轴触发矩阵生成器

    输出: services/m2_symptom_multi_axis_matrix.json
    """

    RULES_PATH = "data/m2_pathology_to_syndrome_rules.json"
    OUTPUT_PATH = "services/m2_symptom_multi_axis_matrix.json"

    AXIS_DISPLAY_MAP = {
        "infection_or_inflammation": "感染/炎症",
        "allergic_th2_inflammation": "过敏/Th2型炎症",
        "autoimmune_or_immune_complex": "自身免疫/免疫复合物",
        "mucosal_barrier_dysfunction": "黏膜屏障功能障碍",
        "lower_airway_involvement": "下气道受累",
        "airway_hyperreactivity": "气道高反应性",
        "secretion_or_retention": "分泌/潴留异常",
        "cardiovascular_or_circulatory_risk": "心血管/循环风险",
        "vascular_or_microcirculatory": "血管/微循环",
        "coagulation_or_bleeding_disorder": "凝血/出血障碍",
        "hematopoietic_or_marrow_dysfunction": "造血/骨髓功能障碍",
        "endocrine_or_hormonal_dysregulation": "内分泌/激素失调",
        "metabolic_or_insulin_resistance": "代谢/胰岛素抵抗",
        "digestive_reflux_or_dysfunction": "消化/反流功能障碍",
        "hepatic_or_biliary_dysfunction": "肝/胆功能障碍",
        "urinary_renal_risk": "泌尿/肾风险",
        "structural_abnormality": "结构异常",
        "neoplastic_or_proliferative": "肿瘤/增殖性病变",
        "fibrosis_or_tissue_remodeling": "纤维化/组织重塑",
        "immune_abnormality": "免疫异常",
        "stress_related": "应激相关",
        "neurotransmitter_or_synaptic_dysfunction": "神经递质/突触功能障碍",
    }

    def __init__(self, rules_path: Optional[str] = None):
        self.rules_path = rules_path or self.RULES_PATH
        self.rules = self._load_rules()
        self.matrix = {
            "symptom_keywords": {},
            "symptom_combinations": [],
            "co_occurrence": {},
            "_meta": {},
        }

    def _load_rules(self) -> dict:
        try:
            with open(self.rules_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"\u26a0 \u89c4\u5219\u5e93\u52a0\u8f7d\u5931\u8d25: {e}")
            return {}

    # ── 步骤1: 提取症状→轴映射 ──

    def _extract_all_allowed_keywords(self) -> Dict[str, Dict]:
        """
        遍历规则库，提取症状关键词→轴/病机映射

        返回结构:
        {
            "症状关键词": {
                "axes": {
                    "轴名": { "relevance": "direct", "possible_pathogenesis": [...], "source": "rule_allowed_when" },
                    ...
                },
                "co_occurring_symptoms": set(),
                "source": set(),
            }
        }
        """
        symptom_to_axes = defaultdict(lambda: {
            "axes": {},
            "co_occurring_symptoms": set(),
            "source": set(),
        })

        for axis_name, axis_data in self.rules.items():
            if axis_name.startswith('_'):
                continue

            for rule in axis_data.get("possible_tcm_pathogenesis", []):
                path_name = rule["pathogenesis"]

                # allowed_when -> 正向映射
                for kw in rule.get("allowed_when", []):
                    if axis_name not in symptom_to_axes[kw]["axes"]:
                        symptom_to_axes[kw]["axes"][axis_name] = {
                            "relevance": "direct",
                            "possible_pathogenesis": [],
                            "source": "rule_allowed_when",
                        }
                    if path_name not in symptom_to_axes[kw]["axes"][axis_name]["possible_pathogenesis"]:
                        symptom_to_axes[kw]["axes"][axis_name]["possible_pathogenesis"].append(path_name)
                    symptom_to_axes[kw]["source"].add("rule_allowed_when")

                # not_allowed_when -> 负向映射
                for kw in rule.get("not_allowed_when", []):
                    symptom_to_axes[kw]["axes"][axis_name] = {
                        "relevance": "negative",
                        "possible_pathogenesis": [],
                        "source": "rule_not_allowed_when",
                    }
                    symptom_to_axes[kw]["source"].add("rule_not_allowed_when")

        # 共现: 同一轴 allowed_when 内的症状两两共现
        for axis_name, axis_data in self.rules.items():
            if axis_name.startswith('_'):
                continue
            for rule in axis_data.get("possible_tcm_pathogenesis", []):
                allowed = rule.get("allowed_when", [])
                for k1 in allowed:
                    for k2 in allowed:
                        if k1 != k2:
                            symptom_to_axes[k1]["co_occurring_symptoms"].add(k2)

        return dict(symptom_to_axes)

    # ── 步骤2: 生成症状组合 ──

    def _generate_combinations(self, symptom_to_axes: Dict) -> List[dict]:
        """从共现关系生成症状组合的多轴映射"""
        combinations = []
        seen = set()

        for symptom, data in symptom_to_axes.items():
            if not data["axes"]:
                continue
            co_list = list(data["co_occurring_symptoms"])[:5]
            if co_list:
                combo_symptoms = [symptom] + co_list[:3]
            else:
                combo_symptoms = [symptom]

            key = "|".join(sorted(set(combo_symptoms)))
            if key in seen:
                continue
            seen.add(key)

            # 计算该组合会命中的轴
            axes_hit = {}
            total_pathogenesis = []

            for s in combo_symptoms:
                if s in symptom_to_axes:
                    for ax_name, ax_info in symptom_to_axes[s]["axes"].items():
                        if isinstance(ax_info, dict) and ax_info.get("relevance") != "negative":
                            if ax_name not in axes_hit:
                                axes_hit[ax_name] = 0
                            axes_hit[ax_name] += 1
                            for p in ax_info.get("possible_pathogenesis", []):
                                if p not in total_pathogenesis:
                                    total_pathogenesis.append(p)

            if not axes_hit:
                continue

            sorted_axes = sorted(axes_hit.keys(), key=lambda a: -axes_hit[a])

            # 主证建议
            primary_suggestion = ""
            if sorted_axes and combo_symptoms:
                first_s = combo_symptoms[0]
                if first_s in symptom_to_axes:
                    ax_data = symptom_to_axes[first_s]["axes"].get(sorted_axes[0])
                    if isinstance(ax_data, dict):
                        pgs = ax_data.get("possible_pathogenesis", [])
                        if pgs:
                            primary_suggestion = pgs[0]

            combinations.append({
                "symptoms": combo_symptoms,
                "axes": sorted_axes,
                "combined_pathogenesis": total_pathogenesis,
                "primary_suggestion": primary_suggestion,
                "confidence": round(min(1.0, 0.3 + len(sorted_axes) * 0.15), 2),
            })

        combinations.sort(key=lambda c: -c["confidence"])
        return combinations[:100]

    # ── 步骤3: 共现矩阵 ──

    def _build_co_occurrence_matrix(self, symptom_to_axes: Dict) -> Dict[str, Dict[str, int]]:
        co_matrix = defaultdict(lambda: defaultdict(int))
        for symptom, data in symptom_to_axes.items():
            for co in data.get("co_occurring_symptoms", []):
                co_matrix[symptom][co] += 1
                co_matrix[co][symptom] += 1
        return {k: dict(v) for k, v in co_matrix.items()}

    # ── 主流程 ──

    def build_full_matrix(self) -> dict:
        print("构建症状多轴触发矩阵...")

        symptom_to_axes = self._extract_all_allowed_keywords()
        print(f"  步骤1: 提取到 {len(symptom_to_axes)} 个症状关键词")

        # 构建 symptom_keywords（可序列化格式）
        symptom_keywords = {}
        for symptom, data in symptom_to_axes.items():
            symptom_keywords[symptom] = {
                "axes": {
                    ax: {
                        "relevance": info.get("relevance", "direct"),
                        "possible_pathogenesis": info.get("possible_pathogenesis", []),
                        "source": info.get("source", "rule_allowed_when"),
                    }
                    for ax, info in data["axes"].items()
                },
                "co_occurring_symptoms": sorted(data.get("co_occurring_symptoms", [])),
                "source": sorted(data.get("source", [])),
            }
        self.matrix["symptom_keywords"] = symptom_keywords

        combinations = self._generate_combinations(symptom_to_axes)
        self.matrix["symptom_combinations"] = combinations
        print(f"  步骤2: 生成 {len(combinations)} 个症状组合")

        co_matrix = self._build_co_occurrence_matrix(symptom_to_axes)
        self.matrix["co_occurrence"] = co_matrix
        print(f"  步骤3: 共现矩阵包含 {len(co_matrix)} 个症状节点")

        axis_count = len([k for k in self.rules if not k.startswith('_')])
        import datetime
        self.matrix["_meta"] = {
            "total_symptom_keywords": len(symptom_keywords),
            "total_combinations": len(combinations),
            "source_axes": axis_count,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        print(f"\n\u2705 \u77e9\u9635\u6784\u5efa\u5b8c\u6210: {len(symptom_keywords)}\u5173\u952e\u8bcd x {len(combinations)}\u7ec4\u5408 x {axis_count}\u8f74")
        return self.matrix

    def save(self, output_path: Optional[str] = None):
        path = output_path or self.OUTPUT_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        blob = json.dumps(self.matrix, ensure_ascii=False, indent=2)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(blob)
        print(f"\u2705 \u77e9\u9635\u5df2\u4fdd\u5b58: {path} ({len(blob)} \u5b57\u8282)")

    def diagnose_symptoms(self, symptoms: List[str]) -> dict:
        """
        用矩阵诊断一组症状的轴映射。
        供桥接层调用。
        """
        if not self.matrix.get("symptom_keywords"):
            return {"matched_axes": [], "possible_pathogenesis": [], "unmatched_symptoms": symptoms}

        matched_axes = {}
        possible_pathogenesis = []
        unmatched = []

        for symptom in symptoms:
            found = False
            for kw, data in self.matrix["symptom_keywords"].items():
                if kw in symptom or symptom in kw:
                    for ax_name, ax_info in data.get("axes", {}).items():
                        if isinstance(ax_info, dict) and ax_info.get("relevance") != "negative":
                            if ax_name not in matched_axes:
                                matched_axes[ax_name] = {"relevance": ax_info["relevance"], "pathogenesis": []}
                            for p in ax_info.get("possible_pathogenesis", []):
                                if p not in matched_axes[ax_name]["pathogenesis"]:
                                    matched_axes[ax_name]["pathogenesis"].append(p)
                    found = True
                    break
            if not found:
                unmatched.append(symptom)

        for ax_info in matched_axes.values():
            for p in ax_info["pathogenesis"]:
                if p not in possible_pathogenesis:
                    possible_pathogenesis.append(p)

        matched_combinations = []
        for combo in self.matrix.get("symptom_combinations", []):
            overlap = set(combo["symptoms"]) & set(symptoms)
            if len(overlap) >= 2:
                matched_combinations.append({
                    "matched_symptoms": list(overlap),
                    "all_in_combo": combo["symptoms"],
                    "suggested_axes": combo["axes"],
                    "suggested_pathogenesis": combo["combined_pathogenesis"],
                    "primary_suggestion": combo.get("primary_suggestion", ""),
                    "confidence": combo.get("confidence", 0.5),
                })

        matched_combinations.sort(key=lambda c: -c["confidence"])

        return {
            "matched_axes": [{"axis": k, **v} for k, v in sorted(matched_axes.items())],
            "matched_axis_names": list(matched_axes.keys()),
            "possible_pathogenesis": possible_pathogenesis,
            "unmatched_symptoms": unmatched,
            "matched_combinations": matched_combinations[:5],
        }


def build_and_save() -> M2MultiAxisMatrixBuilder:
    builder = M2MultiAxisMatrixBuilder()
    builder.build_full_matrix()
    builder.save()
    return builder


if __name__ == "__main__":
    builder = build_and_save()

    # 测试
    tests = [
        ["眨眼", "努嘴", "苔白腻", "注意力不集中"],
        ["脱发", "头发油", "苔黄腻", "噩梦"],
        ["胸痛", "胸闷", "气短", "运动后加重"],
        ["紫癜", "发热", "关节痛", "苔黄腻"],
    ]
    for test_symptoms in tests:
        result = builder.diagnose_symptoms(test_symptoms)
        print(f"\n\u6d4b\u8bd5: {test_symptoms}")
        print(f"  \u8f74: {result['matched_axis_names']}")
        print(f"  \u75c5\u673a: {result['possible_pathogenesis'][:6]}")
        for c in result["matched_combinations"][:2]:
            print(f"  \u7ec4\u5408\u5339\u914d: {c['matched_symptoms']} \u2192 {c['primary_suggestion']} (conf={c['confidence']})")
