"""
test_alpha_p1_backlog_fixes.py
==============================
验证三个 P1 backlog 修复：
1. P1-1 (C01): 风热上感不再误判风寒感冒
2. P1-2 (C08): 咳嗽不再误映射为支原体肺炎
3. P1-3 (C09): 孕期触发 M3 特殊人群审核和 manual_review

运行: pytest tests/test_alpha_p1_backlog_fixes.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestP1_1_C01FengReShangGan:
    """P1-1: 风热上感不误判风寒感冒"""

    def setup_method(self):
        from m2_engine import M2SyndromeSelector
        self.selector = M2SyndromeSelector()
        disease_data = self.selector.kb.get("急性上呼吸道感染", {})
        self.syndromes = disease_data.get("syndromes", {})
        self.patient_info = {
            "symptoms": ["发热39℃", "鼻塞流涕", "咽痛", "手心热"],
            "tongue": "舌尖红，苔白腻偏黄",
            "pulse": "",
            "cold_heat": [],
            "stool_urine": [],
            "appetite": [],
            "pathology": "",
        }

    def test_c01_best_is_heat_syndrome(self):
        """风热上感患者首选应为热性/风热证型"""
        result = self.selector._syndrome_scorer(
            primary_disease="急性上呼吸道感染",
            syndromes=self.syndromes,
            patient_info=self.patient_info,
        )
        candidates = result.get("candidate_scores", [])
        assert candidates, "应该有候选证型"
        best = candidates[0]["syndrome_name"]
        assert "风热" in best or "热" in best, (
            f"首选应为风热/热证，但得到: {best}"
        )

    def test_c01_feng_han_lower_than_feng_re(self):
        """风寒感冒得分应低于风热感冒"""
        result = self.selector._syndrome_scorer(
            primary_disease="急性上呼吸道感染",
            syndromes=self.syndromes,
            patient_info=self.patient_info,
        )
        candidates = {c["syndrome_name"]: c["score"] for c in result.get("candidate_scores", [])}
        feng_han = candidates.get("风寒感冒", 0)
        feng_re = candidates.get("风热感冒", 0)
        assert feng_re > feng_han, (
            f"风热感冒({feng_re}) 应高于 风寒感冒({feng_han})"
        )

    def test_c01_heat_auto_bonus_applied(self):
        """患者偏热时，热性证型应获得 cold_heat 加分"""
        result = self.selector._syndrome_scorer(
            primary_disease="急性上呼吸道感染",
            syndromes=self.syndromes,
            patient_info=self.patient_info,
        )
        candidates = {c["syndrome_name"]: c for c in result.get("candidate_scores", [])}
        feng_re = candidates.get("风热感冒", {})
        assert feng_re.get("cold_heat_match", 0) > 0, (
            "风热感冒应获得冷热加分"
        )

    def test_c01_cold_tongue_not_penalized_for_yellow_coating(self):
        """苔白腻偏黄（有偏黄）不应视为寒象"""
        from m2_engine import M2SyndromeSelector
        import re

        _negation_prefixes = ["无明显", "无明确", "无", "未", "没有", "否认", "不伴"]
        def _is_negated(kw, text):
            for np_ in sorted(_negation_prefixes, key=len, reverse=True):
                if np_ + kw in text:
                    return True
            return False

        all_text = "发热39℃ 鼻塞流涕 咽痛 手心热 舌尖红，苔白腻偏黄"
        cold_specific = ["怕冷", "恶寒", "清稀", "舌淡", "不渴", "肢冷"]
        matched_cold = [cs for cs in cold_specific if cs in all_text and not _is_negated(cs, all_text)]

        def _has_cold_tongue_coating(text):
            if "苔白" not in text:
                return False
            if "苔黄" in text or "偏黄" in text or "微黄" in text:
                return False
            return True

        assert not _has_cold_tongue_coating(all_text), (
            "苔白腻偏黄不应视为寒象舌苔"
        )
        # 苔白不再自动加入 cold_specific
        assert "苔白" not in matched_cold, (
            "苔白伴偏黄不应加入寒特异词"
        )


class TestP1_2_C08KeSuMapping:
    """P1-2: 咳嗽不误映射为支原体肺炎"""

    def setup_method(self):
        from m2_engine import M2SyndromeSelector
        self.selector = M2SyndromeSelector()

    def test_cough_returns_empty(self):
        """resolve_m2_disease_key('咳嗽') 应返回空字符串"""
        key = self.selector.resolve_m2_disease_key("咳嗽")
        assert key == "", f"咳嗽应返回空字符串，得到: '{key}'"

    def test_cough_bing_returns_empty(self):
        """resolve_m2_disease_key('咳嗽病') 应返回空字符串"""
        key = self.selector.resolve_m2_disease_key("咳嗽病")
        assert key == "", f"咳嗽病应返回空字符串，得到: '{key}'"

    def test_fever_returns_empty(self):
        """resolve_m2_disease_key('发热') 应返回空字符串"""
        key = self.selector.resolve_m2_disease_key("发热")
        assert key == "", f"发热应返回空字符串，得到: '{key}'"

    def test_normal_disease_still_resolves(self):
        """正常西医病名解析不受影响"""
        assert self.selector.resolve_m2_disease_key("急性扁桃体炎") == "急性化脓性扁桃体炎"
        assert self.selector.resolve_m2_disease_key("阳痿") == "阳痿"
        assert self.selector.resolve_m2_disease_key("肾结石") == "尿石症"

    def test_cough_causes_no_candidate_in_m2_1(self):
        """咳嗽作为 only primary_disease 时，M2-1 应返回 no_candidate"""
        result = self.selector.run_m2_1_syndrome_reasoning(
            primary_disease="咳嗽",
            symptoms=["咳嗽", "夜间明显"],
        )
        # 应返回 no_candidate
        assert result.get("no_candidate", False), "咳嗽应导致无候选证型"
        # 不应解析出支原体肺炎
        resolved = result.get("input_trace", {}).get("resolved_m2_key", "N/A")
        assert "支原体肺炎" not in str(resolved), (
            f"咳嗽不应映射到支原体肺炎: {resolved}"
        )


class TestP1_3_C09PregnancyM3:
    """P1-3: 孕期触发 M3 特殊人群审核"""

    def setup_method(self):
        from m3_engine import M3ClinicalReviewEngine
        self.m3 = M3ClinicalReviewEngine()

    def test_pregnancy_triggers_special_population(self):
        """孕期患者应产生 special_population 提示"""
        result = self.m3.review(["辛夷", "白芷", "防风", "黄芪", "白术"], {
            "age": 28, "gender": "女", "pregnancy": True,
        }, formula_name="玉屏风散", diagnosis="变应性鼻炎")
        pop_notes = result.get("special_population", [])
        has_preg = any("孕妇" in p for p in pop_notes)
        assert has_preg, f"应包含孕妇提示，但得到: {pop_notes}"

    def test_pregnancy_sets_require_manual_review(self):
        """孕期患者应设置 require_manual_review=True"""
        result = self.m3.review(["辛夷", "白芷"], {
            "age": 28, "gender": "女", "pregnancy": True,
        }, formula_name="test", diagnosis="鼻炎")
        assert result.get("require_manual_review", False) is True, (
            "孕期应触发人工审核"
        )

    def test_pregnancy_contraindicated_herb_detected(self):
        """妊娠禁忌药（如益母草）应被标记禁用"""
        result = self.m3.review(["益母草", "黄芪"], {
            "age": 28, "gender": "女", "pregnancy": True,
        }, formula_name="test", diagnosis="鼻炎")
        dose_adjs = result.get("dose_adjustments", [])
        preg_issues = [d for d in dose_adjs if d.get("age_group") == "孕妇"]
        assert len(preg_issues) > 0, "应有妊娠禁忌标记"
        # 益母草应在禁忌列表中
        herb_names = [d["herb"] for d in preg_issues]
        assert "益母草" in herb_names, f"益母草应被标记: {herb_names}"

    def test_non_pregnant_no_manual_review(self):
        """非孕期女性患者不应强制 manual_review"""
        result = self.m3.review(["辛夷", "白芷"], {
            "age": 28, "gender": "女", "pregnancy": False,
        }, formula_name="test", diagnosis="鼻炎")
        assert result.get("require_manual_review", False) is False, (
            "非孕期不应强制人工审核"
        )

    def test_pregnancy_without_contra_herbs_still_requires_review(self):
        """即使处方中没有妊娠禁忌药，孕期也应标记人工审核"""
        result = self.m3.review(["辛夷", "白芷", "防风"], {
            "age": 28, "gender": "女", "pregnancy": True,
        }, formula_name="test", diagnosis="鼻炎")
        assert result.get("require_manual_review", False) is True
        pop = result.get("special_population", [])
        assert any("孕妇" in p for p in pop), f"应有孕妇提示: {pop}"


class TestP1_Regression:
    """回归测试：确保已有功能不受影响"""

    def setup_method(self):
        from m2_engine import M2SyndromeSelector
        self.selector = M2SyndromeSelector()

    def test_syndrome_scorer_still_works_for_pneumonia(self):
        """肺炎的证型评分仍正常工作"""
        disease_data = self.selector.kb.get("肺炎", {})
        syndromes = disease_data.get("syndromes", {})
        if not syndromes:
            pytest.skip("知识库无肺炎数据")

        from m2_engine import M2SyndromeSelector
        result = self.selector._syndrome_scorer(
            primary_disease="肺炎",
            syndromes=syndromes,
            patient_info={
                "symptoms": ["发热", "咳嗽", "黄痰", "气喘"],
                "tongue": "舌红，苔黄腻",
                "pulse": "脉滑数",
                "cold_heat": [],
                "stool_urine": [],
                "appetite": [],
                "pathology": "",
            },
        )
        candidates = result.get("candidate_scores", [])
        assert len(candidates) > 0, "肺炎应有候选证型"
        best = candidates[0]
        assert best["score"] > 0, f"最佳证型得分应>0: {best['score']}"

    def test_m3_regular_review_no_false_pregnancy(self):
        """非孕期患者不应显示孕妇提示"""
        from m3_engine import M3ClinicalReviewEngine
        m3 = M3ClinicalReviewEngine()
        result = m3.review(["麻黄", "杏仁", "甘草"], {
            "age": 35, "gender": "男", "pregnancy": False,
        }, formula_name="三拗汤", diagnosis="咳嗽")
        pop = result.get("special_population", [])
        assert not any("孕妇" in p for p in pop), f"不应有孕妇提示: {pop}"
        assert result.get("require_manual_review", False) is False
