"""
M4 危险信号检测器
100% 代码执行，硬规则阈值判断
不允许交给大模型判断
"""
import json
import os
from typing import Dict, List, Optional, Tuple


class DangerSignalDetector:
    """危险信号检测器 — 纯代码执行"""

    def __init__(self, red_flags_path: str = "data/m4_red_flags.json"):
        self.rules = self._load_rules(red_flags_path)

    def _load_rules(self, path: str) -> Dict:
        """加载危险信号规则"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            # 内置默认规则
            return self._default_rules()

    def _default_rules(self) -> Dict:
        return {
            "thresholds": {
                "spo2": {"max": 90, "operator": "le"},
                "sbp": {"max": 180, "operator": "ge"},
                "dbp": {"max": 110, "operator": "ge"},
                "sbp_low": {"min": 80, "operator": "le"},
                "hr": {"max": 130, "operator": "ge"},
                "hr_low": {"min": 45, "operator": "le"},
                "temp": {"max": 40, "operator": "ge"},
            },
            "keyword_signals": [
                "急性胸痛", "胸痛", "意识改变", "意识模糊",
                "大出血", "大量出血", "阴道大量出血", "吐血", "咯血", "便血", "黑便", "呕血",
                "重度呼吸困难", "呼吸困难",
                "偏瘫", "失语", "抽搐", "休克表现",
                "妊娠期大量出血", "妊娠期剧烈腹痛",
            ],
        }

    def detect(
        self,
        vital_signs: Optional[Dict],
        symptoms: List[str],
        new_complaints: Optional[List[str]] = None,
        labs: Optional[Dict] = None,
    ) -> Dict:
        """检测危险信号 — 纯代码执行

        Args:
            vital_signs: {"bp": "130/80", "hr": 75, "temp": 36.5, "spo2": 98}
            symptoms: 当前症状列表
            new_complaints: 新主诉
            labs: 实验室检查结果

        Returns:
            {"triggered": bool, "signals": [], "recommendation": str}
        """
        signals = []
        rules = self.rules.get("thresholds", {})

        # ── 生命体征阈值检测 ──
        if vital_signs:
            # 血压
            bp = vital_signs.get("bp", "")
            if bp and "/" in str(bp):
                parts = str(bp).split("/")
                try:
                    sbp = float(parts[0])
                    dbp = float(parts[1]) if len(parts) > 1 else 0

                    sbp_high = rules.get("sbp", {}).get("max", 180)
                    dbp_high = rules.get("dbp", {}).get("max", 110)
                    sbp_low = rules.get("sbp_low", {}).get("min", 80)

                    if sbp >= sbp_high:
                        signals.append(f"收缩压 {sbp:.0f}mmHg ≥ {sbp_high}")
                    if dbp >= dbp_high:
                        signals.append(f"舒张压 {dbp:.0f}mmHg ≥ {dbp_high}")
                    if sbp <= sbp_low:
                        signals.append(f"收缩压 {sbp:.0f}mmHg ≤ {sbp_low}")
                except (ValueError, IndexError):
                    pass

            # 心率
            hr = vital_signs.get("hr")
            if hr is not None:
                try:
                    hr_val = float(hr)
                    hr_max = rules.get("hr", {}).get("max", 130)
                    hr_min = rules.get("hr_low", {}).get("min", 45)
                    if hr_val >= hr_max:
                        signals.append(f"心率 {hr_val:.0f}bpm ≥ {hr_max}")
                    if hr_val <= hr_min:
                        signals.append(f"心率 {hr_val:.0f}bpm ≤ {hr_min}")
                except (ValueError, TypeError):
                    pass

            # 体温
            temp = vital_signs.get("temp")
            if temp is not None:
                try:
                    temp_val = float(temp)
                    temp_max = rules.get("temp", {}).get("max", 40)
                    if temp_val >= temp_max:
                        signals.append(f"体温 {temp_val:.1f}℃ ≥ {temp_max}")
                except (ValueError, TypeError):
                    pass

            # 血氧
            spo2 = vital_signs.get("spo2")
            if spo2 is not None:
                try:
                    spo2_val = float(spo2)
                    spo2_min = rules.get("spo2", {}).get("max", 90)
                    if spo2_val <= spo2_min:
                        signals.append(f"血氧 {spo2_val:.0f}% ≤ {spo2_min}%")
                except (ValueError, TypeError):
                    pass

        # ── 关键词检测 ──
        all_text = " ".join(symptoms or [])
        if new_complaints:
            all_text += " " + " ".join(new_complaints)
        all_text_lower = all_text.lower()

        keywords = self.rules.get("keyword_signals", [])
        for kw in keywords:
            if kw in all_text_lower or kw in all_text:
                signals.append(f"主诉包含：{kw}")

        # ── 实验室检查危险值 ──
        if labs:
            hb = labs.get("hemoglobin")
            if hb is not None:
                try:
                    hb_val = float(hb)
                    if hb_val < 60:
                        signals.append(f"血红蛋白 {hb_val}g/L < 60（重度贫血）")
                except (ValueError, TypeError):
                    pass

        triggered = len(signals) > 0
        return {
            "triggered": triggered,
            "signals": list(set(signals)),  # 去重
            "recommendation": "建议立即急诊或上级医院评估" if triggered else "",
        }
