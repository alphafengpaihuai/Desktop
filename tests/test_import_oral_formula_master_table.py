"""test_import_oral_formula_master_table.py

测试口服方剂知识库总表导入脚本的功能：
- 能定位并读取总表
- 肺炎不再是 0 syndromes
- 带状疱疹至少有多个证型或证据字段完整
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


# 导入脚本
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.import_oral_formula_master_table import (
    parse_disease_name,
    parse_syndrome_name,
    parse_herbs_from_decoction,
    parse_formula_name,
    extract_tongue_pulse_from_description,
    check_alignment,
)


class TestImportFunctions(unittest.TestCase):
    """测试导入工具函数"""

    def test_parse_disease_name_simple(self):
        """简单病名格式"""
        self.assertEqual(parse_disease_name("肺炎"), "肺炎")
        self.assertEqual(parse_disease_name("带状疱疹"), "带状疱疹")
        self.assertEqual(parse_disease_name(" 感冒 "), "感冒")

    def test_parse_disease_name_bold(self):
        """**主病名** 格式"""
        name = "**主病名： 肺炎**\n*   别名：大叶性肺炎"
        self.assertEqual(parse_disease_name(name), "肺炎")

        name = "**主病名： 肺炎 (Pneumonia)**\n*   别名：大叶性肺炎"
        self.assertEqual(parse_disease_name(name), "肺炎")

        name = "**主病名： 带状疱疹**\n*   别名：Herpes Zoster"
        self.assertEqual(parse_disease_name(name), "带状疱疹")

        name = "**主病名： Hunt综合征**\n*   别名：耳带状疱疹"
        self.assertEqual(parse_disease_name(name), "Hunt综合征")

    def test_parse_disease_name_plain_prefix(self):
        """主病名： 前缀格式"""
        name = "主病名：肺炎 (Pneumonia)\n\n别名：细菌性肺炎"
        self.assertEqual(parse_disease_name(name), "肺炎")

        name = "主病名：胃脘痛\n  别名：胃痛"
        self.assertEqual(parse_disease_name(name), "胃脘痛")

    def test_parse_disease_name_hyphen_prefix(self):
        """- 主病名: 前缀格式"""
        name = "- 主病名: 儿童急性扁桃体炎\n  别名:\n    - 小儿乳蛾"
        self.assertEqual(parse_disease_name(name), "儿童急性扁桃体炎")

        name = "- 主病名: 急性上呼吸道感染\n  别名:\n    - 感冒"
        self.assertEqual(parse_disease_name(name), "急性上呼吸道感染")

    def test_parse_syndrome_name_angle_bracket(self):
        """<> 证型名格式"""
        raw = "<肺经伏热>：鼻黏膜偏红，口干，鼻痒，喷嚏频发"
        name, desc = parse_syndrome_name(raw)
        self.assertEqual(name, "肺经伏热")
        self.assertIn("鼻黏膜偏红", desc)

        raw = "<邪犯肺卫（风寒）>： 恶寒发热，无汗，痰白而稀"
        name, desc = parse_syndrome_name(raw)
        self.assertEqual(name, "邪犯肺卫（风寒）")
        self.assertIn("恶寒发热", desc)

    def test_parse_herbs_from_decoction(self):
        """从汤剂方案提取药物"""
        text = "**用药：**辛夷6g 黄芩9g 栀子9g 麦冬12g 百合12g 生石膏30g(先煎) 知母9g"
        herbs = parse_herbs_from_decoction(text)
        self.assertIn("辛夷", herbs)
        self.assertIn("黄芩", herbs)
        self.assertIn("麦冬", herbs)

    def test_parse_herbs_from_decoction_comma(self):
        """逗号分隔的药物"""
        text = "**用药：**熟地黄20g, 山茱萸12g, 山药12g, 茯苓12g"
        herbs = parse_herbs_from_decoction(text)
        self.assertIn("熟地黄", herbs)
        self.assertIn("茯苓", herbs)

    def test_parse_formula_name(self):
        """从汤剂方案提取方剂名"""
        text = "方剂：【辛夷清肺饮】\n**用药：**辛夷6g"
        self.assertEqual(parse_formula_name(text), "辛夷清肺饮")

        text = "**方剂：** 【麻杏石甘汤】合【《千金》苇茎汤】"
        self.assertEqual(parse_formula_name(text), "麻杏石甘汤")

    def test_extract_tongue_pulse(self):
        """提取舌脉"""
        desc = "舌质红，苔黄腻，脉滑数"
        result = extract_tongue_pulse_from_description(desc)
        self.assertIn("舌质红", result["tongue"])
        self.assertIn("滑数", result["pulse"])

        desc = "舌质淡，苔薄白，脉浮紧，指纹浮红"
        result = extract_tongue_pulse_from_description(desc)
        self.assertIn("舌质淡", result["tongue"])

    def test_parse_formula_name_complex(self):
        """复合方剂名"""
        text = "方剂：【麻杏石甘汤】合【《千金》苇茎汤】"
        name = parse_formula_name(text)
        self.assertEqual(name, "麻杏石甘汤")


class TestAlignmentCheck(unittest.TestCase):
    """检查对齐功能"""

    def setUp(self):
        # 准备测试 KB
        self.test_kb = {
            "肺炎": {"disease_name": "肺炎", "syndromes": {"痰热壅肺": {}}},
            "带状疱疹": {"disease_name": "带状疱疹", "syndromes": {"肝经郁热": {}}},
            "急性上呼吸道感染": {"disease_name": "急性上呼吸道感染", "syndromes": {}},
            "失眠": {"disease_name": "失眠", "syndromes": {}},
            "痢疾 (Dysentery)": {"disease_name": "痢疾 (Dysentery)", "syndromes": {}},
        }

    @patch('scripts.import_oral_formula_master_table.PROJECT_ROOT')
    def test_check_alignment_no_bridge(self, mock_root):
        """无 bridge_server.py 时返回空"""
        mock_root.__truediv__ = lambda self, other: Path(f"/fake/{other}")
        missing = check_alignment(self.test_kb)
        self.assertIsInstance(missing, list)


class TestImportScriptExecution(unittest.TestCase):
    """测试导入脚本的执行结果"""

    def setUp(self):
        self.kb_path = Path(__file__).parent.parent / "data" / "m2_formula_knowledge.json"

    def test_kb_file_exists(self):
        """KB 文件存在"""
        self.assertTrue(self.kb_path.exists(), f"KB 文件不存在: {self.kb_path}")

    def test_kb_has_data(self):
        """KB 有数据"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        self.assertGreater(len(kb), 700, f"KB 疾病数不足: {len(kb)}")

    def test_pneumonia_has_syndromes(self):
        """肺炎不再是 0 syndromes"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        # 检查所有肺炎相关的 key
        for key in kb:
            if '肺炎' in key and key != '支原体肺炎' and '间质性' not in key:
                synds = kb[key].get("syndromes", {})
                if len(synds) > 0:
                    self.assertGreater(len(synds), 2,
                                        f"{key} 应有 >=3 个证型(当前 {len(synds)})")
                    return  # 找到一个有证型的即可
        self.fail("没有找到包含证型的肺炎条目")

    def test_herpes_zoster_has_multiple_syndromes(self):
        """带状疱疹至少有多个证型"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        for key in kb:
            if '带状疱疹' in key:
                synds = kb[key].get("syndromes", {})
                self.assertGreaterEqual(len(synds), 2,
                                         f"带状疱疹应有 >=2 个证型(当前 {len(synds)})")
                return
        self.fail("未找到带状疱疹")

    def test_herpes_zoster_syndrome_has_fields(self):
        """带状疱疹证型有完整字段"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        for key in kb:
            if '带状疱疹' in key:
                synds = kb[key].get("syndromes", {})
                for sn, s in synds.items():
                    self.assertIn("syndrome_name", s,
                                  f"证型 {sn} 缺少 syndrome_name")
                    if s.get("syndrome_name"):
                        # 至少要有部分字段
                        field_count = sum(1 for f in ["formula_name", "herbs", "symptoms"]
                                          if s.get(f))
                        self.assertGreaterEqual(field_count, 1,
                                                f"证型 {sn} 缺少基本字段")
                return

    def test_pneumonia_heat_syndrome_matches(self):
        """肺炎 + 痰黄绿 + 苔黄腻 → 痰热蕴肺或热象相关证型"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        for key in kb:
            if '肺炎' in key and key not in ('支原体肺炎', '间质性肺炎'):
                synds = kb[key].get("syndromes", {})
                for sn in synds:
                    if '痰热' in sn or '热' in sn:
                        return  # 找到热象证型
        self.fail("肺炎未找到痰热/热象相关证型")

    def test_pneumonia_cold_syndrome_matches(self):
        """肺炎 + 痰白清稀 + 怕冷 → 风寒/寒证相关证型"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        for key in kb:
            if '肺炎' in key and key not in ('支原体肺炎', '间质性肺炎'):
                synds = kb[key].get("syndromes", {})
                for sn in synds:
                    if '风寒' in sn or '寒' in sn:
                        return  # 找到寒象证型
        self.fail("肺炎未找到风寒/寒证相关证型")

    def test_herpes_zoster_heat_syndrome(self):
        """带状疱疹 + 单侧簇集水疱 + 灼痛 → 肝经郁热/湿热蕴结"""
        with open(self.kb_path, 'r', encoding='utf-8') as f:
            kb = json.load(f)
        for key in kb:
            if '带状疱疹' in key:
                synds = kb[key].get("syndromes", {})
                for sn in synds:
                    if '肝经郁热' in sn or '湿热' in sn:
                        return
        self.fail("带状疱疹未找到肝经郁热/湿热蕴结证型")


if __name__ == "__main__":
    unittest.main()
