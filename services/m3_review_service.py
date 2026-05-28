"""
m3_review_service.py (V3)
=========================
守一 CDSS — M3 审方筛选层

核心规则（基于西医/药理学）：
  1. 基础方完整保留，不做功效层面的砍药
  2. 只有以下情况才能减味：
     a) ✘ **药理学明确禁忌**：如孕妇禁用、严重肝肾毒性、与西药联用禁忌
     b) ✘ **严重毒副作用**：超过安全剂量范围
     c) ✘ **药力冲突**：同功效药叠加可能引发药力过强
  3. 加味 ≤5 味且必须有 RAG 证据
  4. 输出每条处方的药理学依据/警告
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class FinalHerb:
    name: str
    dosage: str
    category: str
    source: str
    reason: str
    evidence_detail: str = ""
    contraindications: str = ""
    pharmacology_note: str = ""
    herb_layer: str = ""
    locked: bool = False
    action: str = "keep"
    removal_evidence_grade: str = "D"


@dataclass
class FinalPrescription:
    formula_name: str
    herbs: List[FinalHerb] = field(default_factory=list)
    total_herbs_count: int = 0
    removed_herbs: List[dict] = field(default_factory=list)
    audit_actions: List[dict] = field(default_factory=list)
    reference_summary: str = ""
    note: str = ""
    reviewed: bool = True
    formula_reselection_required: bool = False
    formula_reselection_reason: str = ""


class M3ReviewService:
    """
    M3 审方：基于药理学依据做安全性评估

    审方策略：
      - 默认保留所有基础方药
      - 只在有明确药理学冲突时才标记或移除
      - 移除须输出"药理学禁忌依据"
    """

    # ── 药理学禁忌检查 ──
    PHARMACOLOGY_CONTRAS = {
        # (药名, 病症/场景) -> 禁忌说明
        "益母草": {
            "pregnancy": "孕妇禁用（兴奋子宫平滑肌，有流产风险）",
            "bleeding_disorder": "出血性疾病慎用（抑制血小板聚集）",
        },
        "牛膝": {
            "pregnancy": "孕妇禁用（兴奋子宫）",
        },
        "川牛膝": {
            "pregnancy": "孕妇禁用（兴奋子宫）",
        },
        "杜仲": {
            "pregnancy": "慎用（传统认为安胎，但大剂量有降压作用）",
            "hypotension": "低血压患者慎用（降压作用）",
        },
        "桑寄生": {
            "pregnancy": "传统安胎药，无明确禁忌",
        },
        "附子": {
            "cardiac_toxicity": "心脏毒性，心律失常禁用",
            "pregnancy": "孕妇禁用",
        },
        "细辛": {
            "renal_impairment": "肾功能不全禁用（马兜铃酸肾病风险）",
            "overdose": "不过钱（>3g有呼吸抑制风险）",
        },
        "全蝎": {
            "child_overdose": "儿童≤2g/日（神经毒性，过量致呼吸抑制）",
            "pregnancy": "孕妇禁用",
        },
        "蜈蚣": {
            "child_overdose": "儿童≤1g/日（神经毒性）",
            "pregnancy": "孕妇禁用",
        },
        "甘草": {
            "hypertension": "高血压慎用（假性醛固酮增多症，升压）",
            "hypokalemia": "低钾血症慎用（排钾作用）",
            "edema": "水肿患者慎用（水钠潴留）",
        },
        "大黄": {
            "pregnancy": "孕妇禁用（刺激肠道，引起盆腔充血）",
            "gastrointestinal": "肠道梗阻禁用",
        },
        "黄连": {
            "cold_deficiency": "虚寒证慎用（大苦大寒伤胃）",
            "long_term": "长期服用致肠道菌群失调",
        },
        "黄芩": {
            "cold_deficiency": "脾胃虚寒慎用",
        },
        "栀子": {
            "cold_deficiency": "脾胃虚寒慎用（苦寒伤胃）",
            "diarrhea": "便溏者慎用",
        },
        "石决明": {
            "hypotension": "低血压慎用（有降压报道）",
            "digestion": "脾胃虚寒者慎用",
        },
        "天麻": {
            "bradycardia": "心动过缓慎用（减慢心率）",
            "pregnancy": "慎用（动物实验有致畸报道，临床尚不明确）",
        },
        "钩藤": {
            "hypotension": "低血压慎用（有降压作用）",
            "bradycardia": "心动过缓慎用",
        },
        "夜交藤": {
            "none": "无明显药理学禁忌",
        },
        "茯神": {
            "none": "无明显药理学禁忌",
        },
    }

    # ── 共存疾病/用药禁忌匹配 ──
    COMORBIDITY_CHECKS = [
        # (疾病名/风险因素, 受影响中药, 说明)
        ("pregnancy", ["益母草", "川牛膝", "牛膝", "全蝎", "蜈蚣", "大黄", "附子", "红花", "桃仁", "三棱", "莪术", "水蛭", "虻虫"], "妊娠禁忌"),
        ("pregnant", ["益母草", "川牛膝", "牛膝", "全蝎", "蜈蚣", "大黄", "附子"], "妊娠禁忌"),
        ("hypertension", ["甘草"], "高血压慎用/禁用"),
        ("hypertensive", ["甘草"], "高血压慎用/禁用"),
        ("hypotension", ["杜仲", "钩藤", "天麻", "石决明"], "低血压慎用（降压作用）"),
        ("低血压", ["杜仲", "钩藤", "天麻", "石决明"], "低血压慎用（降压作用）"),
        ("高血压", ["甘草"], "高血压慎用/禁用"),
        ("renal", ["细辛", "木通", "关木通", "防己", "马兜铃"], "肾毒性风险"),
        ("肾功能不全", ["细辛", "木通", "关木通", "防己", "马兜铃"], "肾毒性风险"),
        ("liver", ["雷公藤", "黄药子", "川楝子", "苍耳子"], "肝毒性风险"),
        ("肝功能异常", ["雷公藤", "黄药子", "川楝子", "苍耳子"], "肝毒性风险"),
        ("bleeding", ["益母草", "丹参", "赤芍", "红花", "桃仁", "水蛭", "虻虫", "三棱", "莪术"], "出血倾向慎用（抗凝/抗血小板）"),
        ("出血倾向", ["益母草", "丹参", "赤芍", "红花", "桃仁", "水蛭", "虻虫", "三棱", "莪术"], "出血倾向慎用（抗凝/抗血小板）"),
        ("diabetes", ["甘草"], "糖尿病慎用（假性醛固酮增多症可能影响血糖）"),
        ("糖尿病", ["甘草"], "糖尿病慎用（影响血糖代谢）"),
        ("bradycardia", ["天麻", "钩藤"], "心动过缓慎用（减慢心率）"),
        ("心动过缓", ["天麻", "钩藤"], "心动过缓慎用（减慢心率）"),
        ("child", ["全蝎", "蜈蚣", "细辛", "附子"], "儿童需减量"),
        ("儿童", ["全蝎", "蜈蚣", "细辛", "附子"], "儿童需减量"),
    ]

    # ── 毒性/安全性 ──
    TOXIC_HERBS = {
        "全蝎": {"toxicity": "有毒", "max_daily": "2g(儿童)", "note": "神经毒性，过量致呼吸抑制"},
        "蜈蚣": {"toxicity": "有毒", "max_daily": "1g(儿童)", "note": "神经毒性，儿童需减量"},
        "细辛": {"toxicity": "小毒", "max_daily": "3g", "note": "马兜铃酸肾病风险"},
        "白附子": {"toxicity": "有毒", "max_daily": "6g", "note": "儿童减量"},
        "天南星": {"toxicity": "有毒", "max_daily": "9g", "note": "姜制减毒"},
        "苦杏仁": {"toxicity": "小毒", "max_daily": "9g", "note": "氢氰酸风险"},
    }

    CHILD_DOSAGE = {
        "天麻": "6g", "钩藤": "9g", "蝉蜕": "5g", "僵蚕": "5g",
        "全蝎": "2g", "白芍": "9g", "甘草": "3g", "栀子": "5g",
        "黄芩": "6g", "川牛膝": "6g", "杜仲": "9g", "益母草": "9g",
        "桑寄生": "9g", "夜交藤": "9g", "茯神": "9g", "石决明": "15g",
        "菊花": "6g", "远志": "5g", "石菖蒲": "6g", "桑叶": "6g",
        "决明子": "6g", "法半夏": "5g", "陈皮": "5g", "竹茹": "6g",
        "枳实": "5g", "黄连": "3g", "胆南星": "3g", "天竺黄": "5g",
        "茯苓": "9g", "白术": "9g", "酸枣仁": "9g", "龟甲": "9g",
        "龙骨": "15g", "太子参": "9g", "山药": "9g", "鸡内金": "6g",
        "枸杞子": "6g", "五味子": "3g", "生地黄": "9g", "麦冬": "6g",
    }

    def __init__(self):
        pass

    ALLOWED_ACTIONS = {
        "keep",
        "keep_with_note",
        "flag_review",
        "reject_addon",
        "remove_due_to_hard_contraindication",
    }

    @staticmethod
    def _risk_matches(check_key: str, risk_factor: str) -> bool:
        ck = check_key.lower()
        rf = risk_factor.lower()
        return ck in rf or rf in ck

    @staticmethod
    def _herb_matches(herb_name: str, target_name: str) -> bool:
        return target_name in herb_name or herb_name in target_name

    def classify_removal_evidence(self, herb_name: str, risk_factors: List[str]) -> Tuple[str, str]:
        """
        返回 (grade, reason)。
        S/A 可自动删除；B/C 仅 flag_review；D 不处理。
        """
        for risk in risk_factors:
            for check_key, affected_herbs, reason in self.COMORBIDITY_CHECKS:
                if not self._risk_matches(check_key, risk):
                    continue
                if not any(self._herb_matches(herb_name, h) for h in affected_herbs):
                    continue
                contra_text = self.PHARMACOLOGY_CONTRAS.get(herb_name, {})
                joined = "；".join(contra_text.values()) if isinstance(contra_text, dict) else ""
                if "妊娠禁忌" in reason or "孕妇禁用" in joined:
                    return "S", f"{reason}: {risk}"
                if "肝毒性" in reason or "肾毒性" in reason or ("禁用" in joined and "慎用" not in joined):
                    return "A", f"{reason}: {risk}"
                return "B", f"{reason}: {risk}"
        return "D", ""

    @staticmethod
    def _layer_for_source(source: str, is_base: bool) -> str:
        if is_base:
            return "core_formula_herb"
        if source in {"case_rag", "rag", "symptom_add"}:
            return "rag_addon_herb"
        if source == "pharmacology":
            return "pharmacology_addon_herb"
        return "auxiliary_formula_herb"

    @staticmethod
    def _action_for_grade(grade: str, has_note: bool = False) -> str:
        if grade in {"S", "A"}:
            return "remove_due_to_hard_contraindication"
        if grade in {"B", "C"}:
            return "flag_review"
        if has_note:
            return "keep_with_note"
        return "keep"

    def get_pharmacology_note(self, herb_name: str, risk_factors: List[str] = None) -> List[str]:
        """获取药理学注释（非禁止，仅供医师参考）"""
        if risk_factors is None:
            risk_factors = []
        notes = []

        if herb_name in self.PHARMACOLOGY_CONTRAS:
            contras = self.PHARMACOLOGY_CONTRAS[herb_name]
            for key, note in contras.items():
                # 是否匹配风险因素
                for rf in risk_factors:
                    if key.lower() in rf.lower() or rf.lower() in key.lower():
                        notes.append(f"⚠ 药理学提示: 患者有「{rf}」→ {note}")
                if key == "none":
                    notes.append("✅ 无明显药理学禁忌")
                else:
                    # 作为常规药理注释（非禁忌，供医师知情）
                    pass

        return notes

    def check_toxicity(self, herb_name: str) -> Optional[dict]:
        for name, info in self.TOXIC_HERBS.items():
            if name in herb_name or herb_name in name:
                return info
        return None

    def get_dosage(self, herb_name: str) -> str:
        if herb_name in self.CHILD_DOSAGE:
            return self.CHILD_DOSAGE[herb_name]
        for key, val in self.CHILD_DOSAGE.items():
            if key in herb_name or herb_name in key:
                return val
        return "6g"

    def review(
        self,
        formula_name: str,
        base_herbs: List,
        add_herbs: List,
        pharmacology_candidates: List[dict] = None,
        reference_cases: List[dict] = None,
        patient_info: dict = None,
    ) -> FinalPrescription:
        """
        M3 审方：基于药理学安全性

        核心策略：
          - 基础方默认全部保留
          - 仅当药理学明确禁忌（如孕妇用益母草）才标记移除
          - 移除须输出具体药理学依据
          - 加味 ≤5 味，全部来源标注
        """
        if patient_info is None:
            patient_info = {"age": "9岁", "gender": "男"}
        if pharmacology_candidates is None:
            pharmacology_candidates = []
        if add_herbs is None:
            add_herbs = []
        if base_herbs is None:
            base_herbs = []

        # 识别风险因素（从 patient_info 和 pharmacology_candidates 提取）
        risk_factors = []
        if "risk_factors" in patient_info:
            risk_factors = patient_info["risk_factors"]
        age = patient_info.get("age", "")
        if "儿" in age or "岁" in age:
            risk_factors.append("child")
        gender = patient_info.get("gender", "")
        if gender in ("女", "female"):
            # 如果有提到妊娠，才加 pregnancy；常规不加
            pass

        # Step 1: 收集所有药（base 在前，add 在后）
        removed_herbs = []
        audit_actions = []
        all_herbs_raw = []
        seen_names = set()
        formula_reselection_required = bool(
            patient_info.get("formula_reselection_required")
            or patient_info.get("formula_overall_mismatch")
        )
        formula_reselection_reason = (
            "主方整体证型不匹配，应重新选方，而非裁剪原方。"
            if formula_reselection_required else ""
        )

        # 处理基础方
        for h in base_herbs:
            name = h.name if hasattr(h, 'name') else h.get('name', str(h))
            if name in seen_names:
                continue
            seen_names.add(name)

            herb_layer = self._layer_for_source("formula", is_base=True)
            pharma_notes = self.get_pharmacology_note(name, risk_factors)
            toxicity = self.check_toxicity(name)
            grade, hard_reason = self.classify_removal_evidence(name, risk_factors)
            action = self._action_for_grade(grade, bool(pharma_notes or toxicity))

            if action == "remove_due_to_hard_contraindication":
                removed_herbs.append({
                    "name": name,
                    "source": "formula",
                    "herb_layer": herb_layer,
                    "locked": True,
                    "action": action,
                    "removal_evidence_grade": grade,
                    "reason": hard_reason,
                    "type": "hard_contraindication",
                })
                audit_actions.append(removed_herbs[-1])
                continue

            toxicity_note = ""
            if toxicity:
                toxicity_note = f"{toxicity['toxicity']}，{toxicity['note']}"

            all_herbs_raw.append({
                "name": name,
                "category": "君" if len(all_herbs_raw) <= 2 else ("臣" if len(all_herbs_raw) <= 6 else "佐"),
                "source": "formula",
                "reason": f"基础方: {formula_name}",
                "evidence_detail": "",
                "pharmacology_note": "; ".join(pharma_notes) if pharma_notes else "",
                "toxicity_note": toxicity_note,
                "herb_layer": herb_layer,
                "locked": True,
                "action": action,
                "removal_evidence_grade": grade,
            })
            audit_actions.append({
                "name": name,
                "herb_layer": herb_layer,
                "locked": True,
                "action": action,
                "removal_evidence_grade": grade,
                "reason": hard_reason or "基础方默认保留",
            })

        # 处理加味
        accepted_addons = 0
        for h in add_herbs:
            name = h.name if hasattr(h, 'name') else h.get('name', str(h))
            if not name or name == 'None' or name == '':
                audit_actions.append({"name": "空名", "herb_layer": "unknown", "locked": False, "action": "reject_addon", "removal_evidence_grade": "D", "reason": "药名为空"})
                continue
            if name in seen_names:
                continue
            source = h.source if hasattr(h, 'source') else h.get('source', '')
            herb_layer = self._layer_for_source(source, is_base=False)
            evidence = h.evidence_detail if hasattr(h, 'evidence_detail') else h.get('evidence_detail', '')
            if herb_layer in {"rag_addon_herb", "pharmacology_addon_herb"} and not evidence:
                audit_actions.append({
                    "name": name,
                    "herb_layer": herb_layer,
                    "locked": False,
                    "action": "reject_addon",
                    "removal_evidence_grade": "D",
                    "reason": "加味缺少来源证据",
                })
                continue
            if accepted_addons >= 5:
                audit_actions.append({
                    "name": name,
                    "herb_layer": herb_layer,
                    "locked": False,
                    "action": "reject_addon",
                    "removal_evidence_grade": "D",
                    "reason": "RAG/加味超过5味上限",
                })
                continue

            pharma_notes = self.get_pharmacology_note(name, risk_factors)
            toxicity = self.check_toxicity(name)
            grade, hard_reason = self.classify_removal_evidence(name, risk_factors)
            action = self._action_for_grade(grade, bool(pharma_notes or toxicity))

            if action == "remove_due_to_hard_contraindication":
                removed_herbs.append({
                    "name": name,
                    "source": source,
                    "herb_layer": herb_layer,
                    "locked": False,
                    "action": action,
                    "removal_evidence_grade": grade,
                    "reason": hard_reason,
                    "type": "hard_contraindication",
                })
                audit_actions.append(removed_herbs[-1])
                continue
            seen_names.add(name)
            accepted_addons += 1

            toxicity_note = ""
            if toxicity:
                toxicity_note = f"{toxicity['toxicity']}，{toxicity['note']}"
                # 不自动移除，只标记

            all_herbs_raw.append({
                "name": name,
                "category": "使" if len(all_herbs_raw) >= 10 else "佐",
                "source": source,
                "reason": h.reason if hasattr(h, 'reason') else h.get('reason', ''),
                "evidence_detail": evidence,
                "pharmacology_note": "; ".join(pharma_notes) if pharma_notes else "",
                "toxicity_note": toxicity_note,
                "herb_layer": herb_layer,
                "locked": False,
                "action": action,
                "removal_evidence_grade": grade,
            })
            audit_actions.append({
                "name": name,
                "herb_layer": herb_layer,
                "locked": False,
                "action": action,
                "removal_evidence_grade": grade,
                "reason": hard_reason or "加味证据通过",
            })

        # Step 2: 分配剂量
        for h in all_herbs_raw:
            h["dosage"] = self.get_dosage(h["name"])

        # Step 3: 构建最终处方
        final_herbs = []
        for h in all_herbs_raw:
            evidence_str = h.get("evidence_detail", "")
            pharm_str = h.get("pharmacology_note", "")
            tox_str = h.get("toxicity_note", "")
            combined_note = "; ".join(filter(None, [pharm_str, tox_str]))

            final_herbs.append(FinalHerb(
                name=h["name"],
                dosage=h["dosage"],
                category=h["category"],
                source=h["source"],
                reason=h["reason"],
                evidence_detail=evidence_str,
                pharmacology_note=combined_note,
                herb_layer=h["herb_layer"],
                locked=h["locked"],
                action=h["action"],
                removal_evidence_grade=h["removal_evidence_grade"],
            ))

        # 参考摘要
        ref_summary = ""
        if reference_cases:
            case_strs = []
            for i, c in enumerate(reference_cases[:3]):
                illness = c.get('illness_name', '')
                herbs = c.get('extracted_herbs', [])
                herb_str = ', '.join(herbs[:5]) if herbs else c.get('formula_composition', '')[:40]
                case_strs.append(f"案{i+1}: {illness} | 药: {herb_str}")
            ref_summary = "; ".join(case_strs)

        # 备注
        notes = []
        total = len(final_herbs)
        notes.append(f"药味总数: {total}（基础方{len(base_herbs)}味 + 加味{len(add_herbs)}味）")

        if removed_herbs:
            for rh in removed_herbs:
                notes.append(f"🛑 药理学禁忌移除: {rh['name']} — {rh['reason']}")
        if formula_reselection_required:
            notes.append(formula_reselection_reason)

        # 检查各药理学注释
        for h in final_herbs:
            if h.pharmacology_note:
                notes.append(f"💊 药理提示: {h.name} — {h.pharmacology_note}")

        return FinalPrescription(
            formula_name=formula_name,
            herbs=final_herbs,
            total_herbs_count=total,
            removed_herbs=removed_herbs,
            audit_actions=audit_actions,
            reference_summary=ref_summary,
            note="；".join(notes) if notes else "审方通过，无特殊注意事项。",
            formula_reselection_required=formula_reselection_required,
            formula_reselection_reason=formula_reselection_reason,
        )


def get_m3_review_service() -> M3ReviewService:
    return M3ReviewService()
