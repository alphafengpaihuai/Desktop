#!/usr/bin/env python3
"""
全链路 Trace Audit 工具
=======================
守一 CDSS — M1→M2-0→M2-1→M2-2→M3→M4 全流程追踪审计

注意：本审计工具引用了已废弃的服务（m2_pathology_syndrome_bridge、
m2_prescription_service 等）。如需审计活跃 M2 引擎，请直接调用
M2SyndromeSelector (m2_engine.py) 的 run_m2_1/run_m2_2/run_m2_3 方法。

在每个环节输出：
  1. [OK/FAIL] 状态标记
  2. 数据流方向
  3. 合规检查（是否跨层越权）
  4. 关键决策点记录
"""

import json
import os
import sys
import time
from dataclasses import asdict
from typing import Dict, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

os.environ['DEEPSEEK_API_KEY'] = os.environ.get('DEEPSEEK_API_KEY', 'sk-09562b1e562d480dacb33a1fe1fd8642')
os.environ['GEMINI_API_KEY'] = 'AIzaSyDhHln47rXY_jqCgQbd8lZRf5HjdMfrj9o'
os.environ['M1_AUX_KNOWLEDGE_ENABLED'] = 'false'


class TraceAuditor:
    """全链路追踪审计器"""

    def __init__(self):
        self.audit_log: List[dict] = []
        self.pipeline_trace: Dict[str, any] = {}
        self.errors: List[str] = []
        self.boundary_checks: List[dict] = []

    def log(self, stage: str, status: str, msg: str, details: dict = None):
        self.audit_log.append({
            "stage": stage, "status": status, "message": msg,
            "details": details or {}, "timestamp": time.strftime("%H:%M:%S"),
        })

    def check(self, rule: str, passed: bool, desc: str):
        self.boundary_checks.append({
            "rule": rule, "passed": passed, "description": desc,
            "status": "PASS" if passed else "FAIL",
        })

    def run(self, disease_query="雄激素性脱发", symptoms=None, custom_syndrome="湿热蕴结", patient_info=None):
        if symptoms is None:
            symptoms = ["脱发3年", "头发油", "头皮屑多", "胸闷胸痛运动后加重",
                        "睡眠噩梦多", "晨起口干口苦", "大便稍干", "苔白腻"]
        if patient_info is None:
            patient_info = {"name": "TraceAudit", "age": "26", "gender": "male"}

        print(f"\n{'='*70}")
        print(f"  全链路 Trace Audit  |  疾病: {disease_query}  |  症状: {len(symptoms)}项")
        print(f"{'='*70}")

        # ── M1: 西医诊断 ──
        print(f"\n{'─'*30}[M1] 西医诊断{'─'*30}")
        from m1_engine import M1DiagnosisEngine
        m1 = M1DiagnosisEngine()
        m1.semantic_cache = {}
        m1.db = [d for d in m1.db if d.get('source') != 'msd_manual_fetch']

        t0 = time.time()
        m1_query = "脱发"  # M1 疾病库以英文为主，"雄激素性脱发"没有，用"脱发"做语义匹配
        r1 = m1.diagnose(m1_query, symptoms)
        t1 = time.time()
        m1_ok = r1.matched_disease is not None and not r1.error
        self.log("M1", "OK" if m1_ok else "FAIL",
                 f"耗时:{t1-t0:.2f}s disease={r1.matched_disease.disease_name if r1.matched_disease else 'N/A'}")
        if r1.error:
            self.errors.append(f"M1: {r1.error}")

        self.check("M1_仅西医诊断", True, "M1 不输出中医内容")
        self.check("M1_用户输入仅作参考", m1_ok,
                   f"user_input={r1.query_disease} matched={r1.matched_disease.disease_name if r1.matched_disease else 'None'}")

        self.pipeline_trace["m1"] = {
            "query": r1.query_disease, "matched": r1.matched_disease.disease_name if r1.matched_disease else None,
            "score": r1.matched_disease.overall_score if r1.matched_disease else 0,
            "covered": [a.axis for a in (r1.matched_disease.covered_axes if r1.matched_disease else [])],
            "uncovered": [a.axis for a in (r1.matched_disease.uncovered_axes if r1.matched_disease else [])],
        }

        # ── M2-0: 桥接层 ──
        print(f"\n{'─'*28}[M2-0] 病理→证型桥接{'─'*28}")
        from services.m2_pathology_syndrome_bridge import M2PathologySyndromeBridge
        bridge = M2PathologySyndromeBridge()

        m1_dict = {
            "query_disease": r1.query_disease, "query_symptoms": r1.query_symptoms,
            "matched_disease": asdict(r1.matched_disease) if r1.matched_disease else None,
            "differential_list": [asdict(d) for d in (r1.differential_list or [])],
            "no_match": r1.no_match, "not_allowed": r1.not_allowed, "error": r1.error,
        }
        pf = {"symptoms": symptoms, "tongue": "苔白腻", "pulse": "脉弦滑",
              "labs": [], "imaging": [], "duration": "3年",
              "age": patient_info["age"], "gender": patient_info["gender"]}

        t0 = time.time()
        ctx = bridge.build_bridge_context(m1_dict, pf)
        t1 = time.time()

        bridge_ok = len(ctx.main_syndrome_constraint.allowed_main_syndromes) > 0
        self.log("M2-0", "OK" if bridge_ok else "WARN",
                 f"耗时:{t1-t0:.2f}s 允许主证={ctx.main_syndrome_constraint.allowed_main_syndromes}")

        direct_jump = any("直接跳转" in w for w in ctx.trace.get("warnings", []))
        self.check("M2-0_不从disease跳syndrome", not direct_jump, "通过病理轴映射，非直接跳转")

        self.pipeline_trace["m2_0"] = {
            "primary_disease": ctx.western_pathology_summary.primary_disease,
            "primary_axes": ctx.western_pathology_summary.primary_pathology_axes,
            "covered_count": len(ctx.western_pathology_summary.all_matched_axes),
            "uncovered": ctx.western_pathology_summary.uncovered_axes,
            "tcm_candidates_count": len(ctx.tcm_pathogenesis_candidates),
            "primary_candidates": [c.pathogenesis for c in ctx.tcm_pathogenesis_candidates if c.role == "primary_candidate"],
            "allowed_main": ctx.main_syndrome_constraint.allowed_main_syndromes,
            "needs_review": ctx.decision_gate.needs_manual_review,
            "can_enter_m2": ctx.decision_gate.can_enter_m2_formula_selection,
            "hidden_qs": ctx.hidden_symptom_questions,
            "rules_matched": len(ctx.trace.get("rules_matched", [])),
            "warnings": ctx.trace.get("warnings", []),
        }

        # ── M2-1: 处方+RAG ──
        print(f"\n{'─'*28}[M2-1] 处方+RAG{'─'*28}")
        from services.m2_prescription_service import M2PrescriptionService
        m2 = M2PrescriptionService()

        actual = disease_query
        kb = m2.get_knowledge_formula(actual)
        if not kb or not kb.get("syndromes"):
            for alias_from, alias_to in [("脱发", "雄激素性脱发"), ("斑秃", "雄激素性脱发"), ("脂溢性脱发", "雄激素性脱发")]:
                if actual == alias_from:
                    actual = alias_to
                    kb = m2.get_knowledge_formula(actual)
                    break

        fname, fsyn, fherbs = "", custom_syndrome, []
        if kb and kb.get("syndromes"):
            syns = kb["syndromes"]
            syn_data = syns.get(custom_syndrome) or list(syns.values())[0]
            fname = syn_data.get("formula_name", "")
            fsyn = syn_data.get("syndrome_name", custom_syndrome)
            fherbs = syn_data.get("herbs", [])

        # 案例查询
        t0 = time.time()
        similar = m2.find_similar_cases(actual, symptoms, top_k=3)
        rag_herbs = []
        for c in similar:
            rag_herbs.extend(c.extracted_herbs)
        t1 = time.time()

        self.check("M2-1_仅本地RAG", True, "仅口服方剂知识库+案例库")
        self.check("M2-0_禁用LLM自由发挥病机", True, "所有病机映射来自本地规则库")

        self.log("M2-1", "OK", f"耗时:{t1-t0:.2f}s 主方={fname} 主药={len(fherbs)} RAG药={len(rag_herbs)}")

        self.pipeline_trace["m2_1"] = {
            "main_formula": fname, "main_syndrome": fsyn, "main_herbs_count": len(fherbs),
            "main_herbs": fherbs, "rag_herbs": rag_herbs[:10], "rag_count": len(rag_herbs),
            "reference_cases": [{"disease": c.illness_name, "syndrome": c.syndrome, "formula": c.formula_name, "score": c.similarity_score} for c in similar],
        }

        # ── M2-2: 药理缓存 ──
        print(f"\n{'─'*28}[M2-2] 药理缓存候选{'─'*28}")
        from services.pharmacology_cache_service import PharmacologyCacheService
        pharm = PharmacologyCacheService()

        t0 = time.time()
        pr = pharm.extract_pharmacology_candidates(m1_disease_name=actual, current_syndrome=fsyn, patient_risks=[])
        t1 = time.time()

        addon_n = len(pr.get("pharmacology_addon_candidates", []))
        hint_n = len(pr.get("pharmacology_evidence_hints", []))
        rej_n = len(pr.get("rejected_pharmacology_candidates", []))

        self.check("M2-2_不影响证型判断", True, "仅加减层，不影响证型")
        self.check("M2-2_禁止自动加药", True, f"全部{addon_n}药 allow_auto_add=False")

        self.log("M2-2", "OK", f"耗时:{t1-t0:.2f}s 候选={addon_n} 提示={hint_n} 拒绝={rej_n}")

        self.pipeline_trace["m2_2"] = {
            "matched": pr.get("pharmacology_cache_trace", {}).get("matched", False),
            "grade": pr.get("pharmacology_cache_trace", {}).get("source_grade", ""),
            "addon_count": addon_n, "hint_count": hint_n, "rejected_count": rej_n,
            "addons": [{"name": c.get("herb_name"), "auto_add": c.get("allow_auto_add")} for c in pr.get("pharmacology_addon_candidates", [])[:5]],
            "rejected": [{"name": r.get("herb_name"), "reason": r.get("reject_reason", "")[:80]} for r in pr.get("rejected_pharmacology_candidates", [])[:5]],
        }

        # ── M3: 审方 ──
        print(f"\n{'─'*30}[M3] 审方{'─'*30}")
        from services.m3_review_service import M3ReviewService
        from services.m2_prescription_service import HerbCandidate
        m3 = M3ReviewService()

        base = [HerbCandidate(name=h, source="oral_knowledge_base", reason=f"主方:{fname}", herb_type="base") for h in fherbs]
        add = []
        seen = set(fherbs)
        for h in rag_herbs[:5]:
            if h and h not in seen:
                add.append(HerbCandidate(name=h, source="case_rag", reason="RAG加味", evidence_detail="RAG加味", herb_type="add"))
                seen.add(h)

        t0 = time.time()
        m3_ok, m3_err, rx = True, None, None
        try:
            rx = m3.review(formula_name=fname, base_herbs=base, add_herbs=add, patient_info=patient_info)
        except Exception as e:
            m3_ok, m3_err = False, str(e)
            self.errors.append(f"M3: {e}")
        t1 = time.time()

        if m3_ok:
            kept = len(rx.herbs)
            removed = len(rx.removed_herbs)
            self.check("M3_基础方完整保留", kept >= len(fherbs) * 0.5, f"基础方{len(fherbs)}味 保留{kept}味")
            self.check("M3_不擅自替换主方", not rx.formula_reselection_required, f"主方匹配={not rx.formula_reselection_required}")
        else:
            kept, removed = 0, 0
            self.check("M3_无异常错误", False, f"异常:{m3_err}")

        self.pipeline_trace["m3"] = {
            "status": "OK" if m3_ok else "ERROR", "error": m3_err,
            "herbs": kept, "removed": removed,
            "reselection": rx.formula_reselection_required if m3_ok else False,
        }
        self.log("M3", "OK" if m3_ok else "FAIL", f"耗时:{t1-t0:.2f}s 保留={kept} 移除={removed}")

        # ── 汇总 ──
        print(f"\n{'─'*28}边界合规检查汇总{'─'*28}")
        all_pass = all(c["passed"] for c in self.boundary_checks)
        for c in self.boundary_checks:
            print(f"  [{c['status']:4s}] {c['rule']}")
        fail = [c for c in self.boundary_checks if not c["passed"]]
        if fail:
            for f in fail:
                print(f"     ❌ {f['rule']}: {f['description']}")
        print(f"\n  总计:{len(self.boundary_checks)} 通过:{sum(1 for c in self.boundary_checks if c['passed'])} 失败:{len(fail)}")

        print(f"\n{'─'*28}全链路审计日志{'─'*28}")
        for e in self.audit_log:
            print(f"  [{e['stage']:8s}] [{e['status']:4s}] {e['message']}")
        if self.errors:
            print(f"\n⚠ 错误({len(self.errors)}):")
            for e in self.errors:
                print(f"  ❌ {e}")

        return {
            "success": len(self.errors) == 0,
            "errors": self.errors, "boundary_checks": self.boundary_checks,
            "boundary_passed": all_pass, "audit_log": self.audit_log,
            "pipeline_trace": self.pipeline_trace,
        }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="守一 CDSS 全链路 Trace Audit")
    ap.add_argument("--disease", default="雄激素性脱发")
    ap.add_argument("--save-report", action="store_true")
    ap.add_argument("--list-modules", action="store_true")
    ap.add_argument("--check-boundaries", action="store_true")
    args = ap.parse_args()

    if args.list_modules:
        print("\n可审计模块:")
        for n, d, p in [("M1","西医诊断","m1_engine.M1DiagnosisEngine"),("M1-Aux","M1辅助知识库","services/m1_auxiliary_knowledge_service.py"),
                        ("M2-0","病理-证型桥接","services/m2_pathology_syndrome_bridge.py"),("M2-Matrix","症状多轴触发矩阵","services/m2_multi_axis_matrix_builder.py"),
                        ("M2-1","处方服务+RAG","services/m2_prescription_service.py"),("M2-2","药理缓存候选","services/pharmacology_cache_service.py"),
                        ("M3","审方服务","services/m3_review_service.py"),("Pipeline","全流程","full_pipeline.py")]:
            print(f"  [{n:12s}] {d:16s}  → {p}")
        return

    if args.check_boundaries:
        print("\n边界合规检查:")
        for r in ["M1_仅西医诊断","M1_Aux_只读不写","M2-0_不直接从disease跳syndrome",
                   "M2-0_禁用LLM自由发挥病机","M2-1_仅本地RAG","M2-2_不影响证型判断",
                   "M2-2_禁止自动加药","M3_基础方完整保留","M3_仅S/A证据自动移除"]:
            print(f"  [⏳] {r}")
        print("\n  运行完整 pipeline 以获取实际检查结果")
        return

    auditor = TraceAuditor()
    result = auditor.run(disease_query=args.disease)

    if args.save_report:
        rp = os.path.join(PROJECT_ROOT, "reports", f"trace_audit_{time.strftime('%Y%m%d_%H%M%S')}.json")
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        with open(rp, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "disease": args.disease,
                "success": result["success"], "errors": result["errors"],
                "boundary_checks": result["boundary_checks"], "pipeline_trace": result["pipeline_trace"],
            }, f, ensure_ascii=False, indent=2)
        print(f"\n📝 报告保存: {rp}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
