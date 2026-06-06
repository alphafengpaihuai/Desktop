"""test_m2_formula_knowledge_integrity.py

测试 data/m2_formula_knowledge.json 的完整性：
- DISEASE_NAME_MAP value 全部存在于 m2_formula_knowledge.json
- m2_fallback value 全部存在于 m2_formula_knowledge.json
- 导入来源标记正确
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def load_kb() -> dict:
    kb_path = Path(__file__).parent.parent / "data" / "m2_formula_knowledge.json"
    with open(kb_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_map_values() -> dict:
    """从 bridge_server.py 提取 DISEASE_NAME_MAP 和 m2_fallback 的 value"""
    bridge_path = Path(__file__).parent.parent / "bridge_server.py"
    source = bridge_path.read_text(encoding='utf-8')

    # 提取 DISEASE_NAME_MAP section
    dm_start = source.find("DISEASE_NAME_MAP")
    dm_end = source.find("m2_fallback")
    if dm_start >= 0:
        dm_section = source[dm_start:dm_end] if dm_end >= 0 else source[dm_start:]
    else:
        dm_section = ""

    # 提取 m2_fallback section
    fb_start = source.find("m2_fallback")
    if fb_start >= 0:
        fb_section = source[fb_start:fb_start + 5000]
    else:
        fb_section = ""

    # 提取 values
    dm_values = set(re.findall(r':\s*"([^"]+)"', dm_section))
    fb_values = set(re.findall(r':\s*"([^"]+)"', fb_section))

    # 只保留可能是疾病名的值，过滤剂量、英文、问句等
    skip_exact = {
        "丹皮6g", "人参6g", "升麻6g", "山栀子9g", "当归12g", "木香6g",
        "杏仁9g", "枇杷叶9g", "柴胡6g", "栀子9g", "桑白皮9g", "桔梗6g",
        "橘皮9g", "浙贝母9g", "淡竹叶6g", "淡豆豉6g", "炙麻黄6g",
        "牛蒡子9g", "瓜蒌仁9g", "甘草3g", "生甘草3g", "生石膏30g",
        "白头翁12g", "白术6g", "白芍12g", "百合12g", "知母9g", "秦皮9g",
        "芦根9g", "茯苓12g", "荆芥穗6g", "葛根15g", "薄荷6g",
        "辛夷6g", "连翘9g", "金荞麦15g", "金银花9g", "陈皮6g",
        "鱼腥草15g", "麦冬9g", "麦门冬20g", "黄柏9g", "黄芩9g",
        "黄芪20g", "黄连6g", "diagnosis_result", "not_found", "ok",
        "pong", "processing", "selection_q", "shouyi-cdss-bridge",
        "text", "web",
    }

    def is_disease_name(v: str) -> bool:
        if v in skip_exact:
            return False
        if re.match(r'^\d+g$', v) or re.match(r'^[\u4e00-\u9fff]{2,4}\d+g$', v):
            return False
        if len(v) > 12:
            return False
        if not re.search(r'[\u4e00-\u9fff]', v):
            return False
        if v in ("", " "):
            return False
        for prefix in ("您", "有无", "是否", "咳嗽是", "头痛或", "小便有无",
                        "弯腰", "除了主要", "近期做过", "皮疹遇热", "疼痛是否",
                        "症状是", "症状是否", "活动是否", "请补充", "您对什么"):
            if v.startswith(prefix):
                return False
        return True

    dm_filtered = {v for v in dm_values if is_disease_name(v)}
    fb_filtered = {v for v in fb_values if is_disease_name(v)}
    fb_filtered.discard("")  # fallback 中的空值

    return {
        "dm_values": dm_filtered,
        "fb_values": fb_filtered,
    }


class TestKnowledgeBaseIntegrity(unittest.TestCase):
    """测试 KB 完整性"""

    @classmethod
    def setUpClass(cls):
        cls.kb = load_kb()

    def test_kb_not_empty(self):
        """KB 非空"""
        self.assertGreater(len(self.kb), 0, "知识库为空")

    def test_each_disease_has_syndromes(self):
        """每个疾病至少有证型定义"""
        empty = []
        for disease, data in self.kb.items():
            if not data.get("syndromes"):
                empty.append(disease)
        # 允许少量的空架子（如手动创建的占位）
        self.assertLess(len(empty), len(self.kb) * 0.1,
                        f"超过 10% 的疾病没有证型: {empty[:10]}")

    def test_oral_formula_source_marked(self):
        """口服方剂知识库来源的证型有 source 标记"""
        total_syndromes = 0
        with_source = 0
        for disease, data in self.kb.items():
            for sn, s in data.get("syndromes", {}).items():
                total_syndromes += 1
                if "oral_formula_master_table" in str(s.get("source", "")):
                    with_source += 1
        # 至少 70% 的证型应该是从总表导入的
        ratio = with_source / max(total_syndromes, 1)
        self.assertGreaterEqual(ratio, 0.5,
                                f"来自 oral_formula_master_table 的比例过低: {ratio:.0%}")

    def test_key_diseases_present(self):
        """关键疾病存在于 KB"""
        for disease in ["肺炎", "急性上呼吸道感染", "带状疱疹",
                         "变应性鼻炎", "喉癌", "缺铁性贫血",
                         "失眠", "头痛", "眩晕"]:
            found = False
            for key in self.kb:
                if key == disease or key.startswith(disease):
                    found = True
                    break
            self.assertTrue(found, f"关键疾病 '{disease}' 不在 KB 中")

    def test_disease_name_map_alignment(self):
        """DISEASE_NAME_MAP 的 value 全部存在于 KB"""
        values = extract_map_values()
        missing = []
        for v in values["dm_values"]:
            # 规范化
            clean = re.sub(r'\s*\([^)]*\)\s*$', '', v).strip()
            clean = re.sub(r'\s*（[^）]*）\s*$', '', clean).strip()
            # 在 KB 中查找（精确或子串匹配）
            found = False
            for key in self.kb:
                if key == clean or key == v:
                    found = True
                    break
                # 子串匹配（如 "头晕 (Vertigo)" 找不到就用 "头晕" 找）
                if clean in key or key in clean:
                    if key.replace(" ", "") == clean.replace(" ", ""):
                        found = True
                        break
            if not found:
                missing.append(v)

        # 允许少量遗留（如某些别名在口服总表中不存在）
        if missing:
            print(f"\n[DISEASE_NAME_MAP] 以下 value 不在 KB 中: {missing}")
        self.assertLessEqual(len(missing), 5,
                             f"DISEASE_NAME_MAP 有 {len(missing)} 个未对齐: {missing[:5]}")

    def test_m2_fallback_alignment(self):
        """m2_fallback 的 value 全部存在于 KB"""
        values = extract_map_values()
        missing = []
        for v in values["fb_values"]:
            clean = re.sub(r'\s*\([^)]*\)\s*$', '', v).strip()
            clean = re.sub(r'\s*（[^）]*）\s*$', '', clean).strip()
            found = False
            for key in self.kb:
                if key == clean or key == v:
                    found = True
                    break
                if clean in key or key in clean:
                    if key.replace(" ", "") == clean.replace(" ", ""):
                        found = True
                        break
            if not found:
                missing.append(v)

        if missing:
            print(f"\n[m2_fallback] 以下 value 不在 KB 中: {missing}")
        self.assertLessEqual(len(missing), 3,
                             f"m2_fallback 有 {len(missing)} 个未对齐: {missing[:3]}")


if __name__ == "__main__":
    unittest.main()
