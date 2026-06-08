"""M2 因果解释型贝叶斯辨证（Skill §6.4 升级）测试

覆盖：
1. 肺炎痰热：黄绿痰+舌红苔黄腻+口苦 → causal_chain 支持痰热壅肺；反事实否定风寒/纯风热。
2. 肺炎风寒：痰白清稀+怕冷+舌淡苔白 → causal_chain 支持风寒；痰热 counterfactual FAIL。
3. 急性胃肠炎虚寒：久泻+畏寒+腹痛喜按+完谷不化+舌淡 → 非湿热痢；虚寒方向 LOW/MEDIUM。
4. 寒热冲突：不得高置信单一寒/热；counterfactual_check 存在或 LOW band。
5. 否定症状：'无痰、无发热、无口干' 不得计为阳性证据。

结构性：
- causal_chain / counterfactual_check 写入 M2-1 输出与 internal_trace；
- prior 非均匀（不再 1/N）；
- score 仅为 likelihood_component；
- selected_syndrome_node 由因果贝叶斯裁决器输出。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from m2_engine import M2SyndromeSelector


def _cand(bd, key=None, name_sub=None):
    for c in bd.get("candidates", []):
        if key is not None and c["syndrome_key"] == key:
            return c
        if name_sub is not None and name_sub in c["syndrome_name"]:
            return c
    return None


class TestM2CausalBayesianDifferentiation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["M2_DISABLE_LLM"] = "1"
        os.environ["DEEPSEEK_API_KEY"] = ""
        cls.m2 = M2SyndromeSelector()

    # ── 病例1：肺炎痰热 ──
    def test_pneumonia_phlegm_heat_causal_support(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "咳黄绿痰", "胸痛", "口干口苦"],
            tongue="舌红苔黄腻", pulse="脉滑数", cold_heat=["发热"],
        )
        self.assertEqual(r.get("selected_syndrome_key"), "痰热壅肺",
                         f"痰热病例应裁决为痰热壅肺，实际={r.get('selected_syndrome_key')}")
        node = r["selected_syndrome_node"]
        chain = node["causal_chain"]
        self.assertTrue(chain.get("predicted_manifestations"),
                        "选中节点应有 causal_chain.predicted_manifestations")
        self.assertTrue(("痰" in chain.get("tcm_pathogenesis", "")) or
                        ("热" in chain.get("tcm_pathogenesis", "")),
                        f"痰热壅肺病机应含痰/热: {chain.get('tcm_pathogenesis')}")
        bd = r["bayesian_differentiation"]
        # 反事实否定风寒
        fenghan = _cand(bd, key="邪犯肺卫（风寒）")
        self.assertIsNotNone(fenghan)
        self.assertEqual(fenghan["counterfactual_check"]["counterfactual_result"], "FAIL",
                         "黄绿痰+舌红苔黄腻下风寒应反事实 FAIL")
        # 痰热壅肺 后验排名应高于（小于）风热
        tanre = _cand(bd, key="痰热壅肺")
        fengre = _cand(bd, key="邪犯肺卫（风热）")
        self.assertIsNotNone(tanre)
        if fengre is not None:
            self.assertLess(tanre["posterior_rank"], fengre["posterior_rank"],
                            "痰热壅肺后验应高于纯风热表证")
        self.assertNotEqual(node["confidence_band"], "LOW")

    # ── 病例2：肺炎风寒 ──
    def test_pneumonia_wind_cold_causal_support(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["怕冷", "咳嗽", "痰白清稀", "鼻塞", "流清涕"],
            tongue="舌淡苔薄白", pulse="脉浮紧", cold_heat=["恶寒"],
        )
        self.assertIn("风寒", r.get("selected_syndrome_key", ""),
                      f"风寒病例应裁决为风寒证，实际={r.get('selected_syndrome_key')}")
        node = r["selected_syndrome_node"]
        self.assertEqual(node["counterfactual_check"]["counterfactual_result"], "PASS",
                         "风寒证在典型寒象下反事实应 PASS")
        bd = r["bayesian_differentiation"]
        tanre = _cand(bd, key="痰热壅肺")
        self.assertIsNotNone(tanre)
        self.assertEqual(tanre["counterfactual_check"]["counterfactual_result"], "FAIL",
                         "痰白清稀+舌淡苔白下痰热壅肺应反事实 FAIL")

    # ── 病例3：急性胃肠炎虚寒 ──
    def test_acute_enteritis_cold_deficiency_not_damp_heat(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "急性胃肠炎",
            symptoms=["久泻", "畏寒", "腹痛喜按", "完谷不化", "四肢不温"],
            tongue="舌淡苔薄白", pulse="脉沉细弱",
        )
        sel = r.get("selected_syndrome_key", "")
        self.assertNotIn("湿热", sel, f"久泻畏寒虚寒象不应裁为湿热痢，实际={sel}")
        self.assertTrue(("寒" in sel) or ("虚" in sel),
                        f"应为寒/虚方向证型，实际={sel}")
        node = r["selected_syndrome_node"]
        self.assertIn(node["confidence_band"], ("LOW", "MEDIUM"),
                      f"虚寒方向置信度应为 LOW/MEDIUM（非 HIGH），实际={node['confidence_band']}")
        bd = r["bayesian_differentiation"]
        damp_heat = _cand(bd, key="湿热痢")
        if damp_heat is not None:
            self.assertEqual(damp_heat["counterfactual_check"]["counterfactual_result"], "FAIL",
                             "虚寒象下湿热痢应反事实 FAIL")

    # ── 病例4：寒热冲突 ──
    def test_cold_heat_conflict_not_high_confidence(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["怕冷", "发热", "咳黄绿痰", "痰白清稀", "口苦"],
            tongue="舌淡苔黄", pulse="脉浮", cold_heat=["恶寒", "发热"],
        )
        node = r["selected_syndrome_node"]
        bd = r["bayesian_differentiation"]
        self.assertNotEqual(node["confidence_band"], "HIGH",
                            "寒热冲突不得高置信单一证型")
        self.assertNotEqual(bd.get("top_confidence_band"), "HIGH")
        # counterfactual_check 必须存在；或 need_human_review
        self.assertTrue(node.get("counterfactual_check"),
                        "寒热冲突时选中节点应保留 counterfactual_check")
        self.assertTrue(r.get("need_human_review"),
                        "寒热冲突应标记 need_human_review")

    # ── 病例5：否定症状不计为阳性证据 ──
    def test_negation_not_positive_evidence(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["咳嗽", "无痰", "无发热", "无口干", "乏力"],
            tongue="舌淡", pulse="脉细",
        )
        sel = r.get("selected_syndrome_key", "")
        bd = r["bayesian_differentiation"]
        c = _cand(bd, key=sel)
        self.assertIsNotNone(c)
        negated = ["无痰", "无发热", "无口干"]
        for n in negated:
            self.assertNotIn(n, str(c.get("evidence_for", [])),
                             f"否定症状 '{n}' 不得出现在 evidence_for")
            self.assertNotIn(n, str(c["causal_chain"].get("observed_support", [])),
                             f"否定症状 '{n}' 不得出现在 observed_support")
        # 无热象阳性证据时，不得把热证作为高置信选中
        if "热" in sel and "寒" not in sel:
            self.assertNotEqual(r["selected_syndrome_node"]["confidence_band"], "HIGH",
                                "全为否定热象时不得高置信热证")

    # ── 结构性：causal_chain / counterfactual 写入输出与 internal_trace ──
    def test_causal_chain_in_output_and_internal_trace(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "咳黄绿痰", "口干口苦"],
            tongue="舌红苔黄腻", pulse="脉滑数", cold_heat=["发热"],
        )
        node = r["selected_syndrome_node"]
        self.assertIn("causal_chain", node)
        self.assertIn("counterfactual_check", node)
        self.assertTrue(node["causal_chain"].get("predicted_manifestations"))
        self.assertIn("counterfactual_result", node["counterfactual_check"])
        cd = r["internal_trace"]["causal_differentiation"]
        self.assertIn("selected_causal_chain", cd)
        self.assertIn("selected_counterfactual_check", cd)
        self.assertTrue(cd["candidates_causal"], "internal_trace 应含 candidates_causal")

    # ── 结构性：prior 非均匀 ──
    def test_prior_not_uniform(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "咳黄绿痰"],
            tongue="舌红苔黄腻", pulse="脉滑数", cold_heat=["发热"],
        )
        bd = r["bayesian_differentiation"]
        self.assertEqual(bd.get("prior_type"), "stage_weighted",
                         "多证型病名 prior 应为非均匀阶段先验")
        priors = [c["prior"] for c in bd["candidates"]]
        self.assertGreater(len(set(round(p, 4) for p in priors)), 1,
                           "先验不应全部相等（禁止仅 1/N）")

    # ── 结构性：score 仅为 likelihood_component + 裁决器拥有选型 ──
    def test_score_is_only_likelihood_component_and_adjudicator_owns(self):
        r = self.m2.run_m2_1_syndrome_reasoning(
            "肺炎",
            symptoms=["发热", "咳嗽", "咳黄绿痰", "口干口苦"],
            tongue="舌红苔黄腻", pulse="脉滑数", cold_heat=["发热"],
        )
        bd = r["bayesian_differentiation"]
        # 后验融合公式声明含五因子
        self.assertIn("causal_coverage", bd.get("posterior_fusion", ""))
        self.assertIn("counterfactual", bd.get("posterior_fusion", ""))
        for c in bd["candidates"]:
            self.assertIn("causal_coverage", c)
            self.assertIn("counterfactual_check", c)
            self.assertIn("likelihood_component", c.get("likelihood_evidence", ""))
        # selected_syndrome_node 由因果裁决器输出
        self.assertEqual(bd.get("adjudicated_syndrome_key"), r.get("selected_syndrome_key"),
                         "selected_syndrome_key 必须等于裁决器 adjudicated_syndrome_key")
        self.assertEqual(r["selected_syndrome_node"].get("adjudicated_by"),
                         "causal_bayesian_adjudicator")
        self.assertEqual(bd.get("method"), "causal_bayesian_differentiation")


if __name__ == "__main__":
    unittest.main()
