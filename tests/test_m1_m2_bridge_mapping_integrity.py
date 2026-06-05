import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge_server.py"
M2_KB = ROOT / "data" / "m2_formula_knowledge.json"


def _load_m2_kb():
    return json.loads(M2_KB.read_text(encoding="utf-8"))


def _dict_assignments(name):
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                yield node


def _literal_dict(name):
    assignments = list(_dict_assignments(name))
    if not assignments:
        raise AssertionError(f"{name} dict not found in bridge_server.py")
    return ast.literal_eval(assignments[0].value)


def _all_literal_pairs(name):
    for assignment in _dict_assignments(name):
        for key_node, value_node in zip(assignment.value.keys, assignment.value.values):
            key = ast.literal_eval(key_node)
            value = ast.literal_eval(value_node)
            yield key, value


class M1M2BridgeMappingIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = _load_m2_kb()
        cls.disease_map = _literal_dict("DISEASE_NAME_MAP")
        cls.m2_fallback = _literal_dict("m2_fallback")

    def test_disease_name_map_targets_exist_in_m2_knowledge(self):
        broken = {
            source: target
            for source, target in _all_literal_pairs("DISEASE_NAME_MAP")
            if target not in self.kb
        }
        self.assertEqual(broken, {})

    def test_m2_fallback_targets_exist_in_m2_knowledge(self):
        broken = {
            source: target
            for source, target in _all_literal_pairs("m2_fallback")
            if target not in self.kb
        }
        self.assertEqual(broken, {})

    def test_high_risk_disease_map_repairs_are_stable(self):
        expected = {
            "不寐": "失眠",
            "失眠": "失眠",
            "丛集性头痛": "头痛 (Headache)",
            "偏头痛": "头痛 (Headache)",
            "紧张型头痛": "头痛 (Headache)",
            "头痛": "头痛 (Headache)",
            "冠心病": "冠状动脉粥样硬化性心脏病 (Coronary Atherosclerotic Heart Disease)",
            "后循环缺血": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "眩晕": "眩晕 (Vertigo)",
            "脑供血不足": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "咳嗽变异性哮喘": "喉源性咳嗽",
            "哮喘": "喉源性咳嗽",
            "耳石症": "眩晕 (Vertigo)",
            "腹泻": "腹泻 (Diarrhea)",
            "过敏性鼻炎": "变应性鼻炎",
            "鼻炎": "慢性鼻炎",
        }
        for source, target in expected.items():
            self.assertEqual(self.disease_map[source], target)
            self.assertIn(target, self.kb)

    def test_high_risk_fallback_repairs_are_stable(self):
        expected = {
            "不寐": "失眠",
            "失眠": "失眠",
            "丛集性头痛": "头痛 (Headache)",
            "偏头痛": "头痛 (Headache)",
            "紧张型头痛": "头痛 (Headache)",
            "头痛": "头痛 (Headache)",
            "后循环缺血": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "脑供血不足": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "腹泻病": "腹泻 (Diarrhea)",
            "尿频": "非淋菌性尿道炎",
            "尿道炎": "非淋菌性尿道炎",
        }
        for source, target in expected.items():
            self.assertEqual(self.m2_fallback[source], target)
            self.assertIn(target, self.kb)

    def test_no_forced_mapping_to_missing_pediatric_allergic_rhinitis(self):
        self.assertNotIn("儿童过敏性鼻炎", set(self.disease_map.values()))
        self.assertNotIn("儿童过敏性鼻炎", set(self.m2_fallback.values()))

    def test_uri_has_wind_heat_syndrome_under_same_disease_key(self):
        uri = self.kb["急性上呼吸道感染"]
        syndromes = uri["syndromes"]
        self.assertIn("风寒感冒", syndromes)
        self.assertIn("风热犯表", syndromes)
        self.assertEqual(syndromes["风热犯表"]["formula_name"], "银翘散")
        self.assertIn("金银花", syndromes["风热犯表"]["herbs"])


if __name__ == "__main__":
    unittest.main()
