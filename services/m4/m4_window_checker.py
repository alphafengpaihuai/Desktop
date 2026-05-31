"""
M4 病程窗口判断器
100% 代码执行，从本地规则库读取
"""
import json
import os
import re
from typing import Dict, Optional


class WindowChecker:
    """病程窗口判断 — 纯代码执行"""

    def __init__(self, rules_path: str = "data/m4_followup_window_rules.json"):
        self.rules = self._load_rules(rules_path)

    def _load_rules(self, path: str) -> Dict:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    # 类别关键词映射
    CATEGORY_KEYWORDS = {
        "acute_infection": ["感冒", "上呼吸道感染", "急性咽炎", "急性扁桃体炎", "流感",
                            "急性支气管炎", "急性鼻炎", "急性喉炎"],
        "subacute_cough": ["咳嗽", "亚急性咳嗽", "慢性咳嗽"],
        "respiratory": ["肺炎", "支气管炎", "哮喘", "慢性阻塞性肺病", "支气管扩张"],
        "chronic_disease": ["高血压", "糖尿病", "失眠", "焦虑", "抑郁", "痴呆", "帕金森",
                            "甲状腺功能减退", "甲状腺功能亢进", "高脂血症"],
        "pain_musculoskeletal": ["腰痛", "颈椎病", "膝关节炎", "肩周炎", "背痛",
                                 "关节痛", "肌肉痛", "骨关节炎"],
        "skin_disease": ["湿疹", "荨麻疹", "皮炎", "银屑病", "痤疮", "带状疱疹", "癣"],
        "gynecology_cycle_related": ["痛经", "月经不调", "子宫腺肌症", "子宫内膜异位症",
                                     "多囊卵巢综合征", "经前期综合征", "围绝经期综合征"],
        "gastroenterology": ["胃炎", "胃食管反流", "功能性消化不良", "腹泻", "便秘",
                             "肠易激综合征", "慢性胃炎", "消化性溃疡"],
        "neurological": ["头痛", "偏头痛", "眩晕", "三叉神经痛", "面瘫",
                         "失眠", "神经痛", "耳鸣"],
    }

    def check(self, disease: str, days: int, m1_card: Optional[Dict] = None) -> Dict:
        """判断病程窗口位置

        Args:
            disease: 原西医病名
            days: 距初诊天数
            m1_card: M1 诊断卡片（可选，如果有则优先使用卡片中的窗口字段）

        Returns:
            {
                "disease": "",
                "expected_onset_days": 0,
                "expected_significant_days": 0,
                "current_day": 0,
                "within_onset_window": true,
                "within_significant_window": true,
                "exceeded_significant_window": false,
                "window_source": "",
                "needs_review": false
            }
        """
        disease_clean = disease.split(" (")[0].split("（")[0].strip()

        # 优先级1：M1 卡片中有 followup_window 字段
        if m1_card and "followup_window" in m1_card:
            fw = m1_card["followup_window"]
            onset = fw.get("expected_onset_days", 7)
            significant = fw.get("expected_significant_days", 14)
            source = "m1_card"
            needs_review = False
        else:
            # 优先级2：按疾病类别匹配
            category = self._classify_disease(disease_clean)
            if category and category in self.rules:
                window = self.rules[category]
                onset = window["expected_onset_days"]
                significant = window["expected_significant_days"]
                source = f"default_by_category:{category}"
                needs_review = True  # 默认值需要医生复核
            else:
                # 优先级3：保守默认值
                onset = 7
                significant = 14
                source = "default_generic"
                needs_review = True

        return {
            "disease": disease,
            "expected_onset_days": onset,
            "expected_significant_days": significant,
            "current_day": days,
            "within_onset_window": days < onset,
            "within_significant_window": days < significant,
            "exceeded_significant_window": days >= significant,
            "window_source": source,
            "needs_review": needs_review,
        }

    def _classify_disease(self, disease: str) -> Optional[str]:
        """按疾病名分类"""
        disease_lower = disease.lower()
        best_category = None
        best_len = 0
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in disease_lower or disease_lower in kw.lower():
                    if len(kw) > best_len:
                        best_len = len(kw)
                        best_category = category
        return best_category
