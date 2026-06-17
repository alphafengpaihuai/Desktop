"""V9.2.2P 门店盲测验收 — 30 门店场景 + 10 FAQ。

输出：outputs/v922p_store_blind_test_audit_v1.json
      outputs/v922p_store_blind_test_summary_v1.md
"""

from __future__ import annotations

import json, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base.loader import load_knowledge_base  # noqa: E402
from knowledge_base.triage import triage_customer_complaint  # noqa: E402
from knowledge_base.kb_config import DEFAULT_KB_XLSX  # noqa: E402


def _safe(d: Any):
    if isinstance(d, dict):
        return {str(k): _safe(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_safe(v) for v in d]
    if isinstance(d, (bool, int, float, str, type(None))):
        return d
    return str(d)


AUDIT_FIELDS = [
    "raw_input", "stage", "semantic_facts", "body_region", "schema_key",
    "negated_fields", "unknown_fields", "red_flag_suspect", "asked_questions",
    "matched_red_flag_rules", "red_flag_result", "disease_candidates",
    "disease_confidence", "service_path", "technician_card_permission",
    "customer_card", "comm_id", "faq_id", "customer_message", "audit_reason",
]

CUSTOMER_FORBIDDEN_TERMS = [
    "红旗征", "P0", "P1", "P2", "P3", "马尾风险", "神经缺损",
    "阻断", "禁忌命中", "高危规则", "你得了", "确诊", "包好", "一次根治",
]


def build_store_payload(result: dict, raw_input: str, stage: str) -> dict:
    audit = result.get("audit", {})
    red_check = result.get("red_flag_check", {})
    customer_card = result.get("customer_card", {})

    matched_ids = audit.get("matched_red_flag_rules", [])
    if not matched_ids:
        for h in red_check.get("structured_hits", []):
            rid = h.get("rule_id", "")
            if rid:
                matched_ids.append(rid)

    cust_msg = (
        customer_card.get("safe_description")
        or customer_card.get("what_we_can_do")
        or customer_card.get("result") or ""
    )

    semantic = result.get("semantic", {})

    payload = {
        "raw_input": raw_input,
        "stage": stage,
        "semantic_facts": audit.get("semantic_facts", {}),
        "body_region": result.get("matched_region", audit.get("body_region", "")),
        "schema_key": audit.get("schema_key", result.get("schema_key", "")),
        "negated_fields": audit.get("negated_fields", []),
        "unknown_fields": audit.get("unknown_fields", []),
        "red_flag_suspect": audit.get("red_flag_suspect", []),
        "asked_questions": audit.get("asked_questions", []),
        "matched_red_flag_rules": matched_ids,
        "red_flag_result": audit.get("red_flag_result", audit.get("triage_level", "")),
        "disease_candidates": audit.get("disease_candidates", []),
        "disease_confidence": audit.get("disease_confidence", []),
        "service_path": audit.get("service_path", result.get("route", "")),
        "technician_card_permission": audit.get("technician_card_permission", False),
        "customer_card": customer_card,
        "comm_id": "",
        "faq_id": "",
        "customer_message": cust_msg,
        "audit_reason": audit.get("audit_reason", audit.get("reasoning_summary", "")),
    }
    return _safe(payload)


def faq_match(question: str, faq_library: list[dict]) -> dict | None:
    ql = question.strip().lower()

    variant_map = {
        "什么时候必须去医院": "什么时候必须去医院？",
        "什么时候要去医院": "什么时候必须去医院？",
        "要去医院吗": "要不要马上去医院？",
        "严不严重": "我这个是不是很严重？",
        "严重吗": "我这个是不是很严重？",
        "还要问多久": "为什么还要问这么多？",
        "什么时候好": "多久能缓解？",
        "能好快": "多久能缓解？",
        "能做重按吗": "能不能做重手法？",
        "能按重一点": "能不能做重手法？",
        "手麻或者腿麻": "我有手麻/腿麻是不是很危险？",
        "腿麻或者手麻": "我有手麻/腿麻是不是很危险？",
    }

    search = ql
    for kw, mapped in variant_map.items():
        if kw in ql:
            search = mapped.lower()
            break

    for faq in faq_library:
        pattern = (faq.get("question_pattern") or "").strip().lower()
        if pattern == search or pattern in search or search in pattern:
            return {
                "faq_id": faq.get("faq_id", ""),
                "reply_short": faq.get("reply_short", ""),
                "reply_full": faq.get("reply_full", ""),
                "category": faq.get("category", ""),
                "handoff_condition": faq.get("handoff_condition", ""),
                "evidence_basis": faq.get("evidence_basis", ""),
            }
    return None


def build_faq_payload(question: str, match: dict | None) -> dict:
    payload = {
        "raw_input": question,
        "stage": "customer_qa",
        "semantic_facts": {},
        "body_region": "",
        "schema_key": "",
        "negated_fields": [],
        "unknown_fields": [],
        "red_flag_suspect": [],
        "asked_questions": [],
        "matched_red_flag_rules": [],
        "red_flag_result": "",
        "disease_candidates": [],
        "disease_confidence": [],
        "service_path": "",
        "technician_card_permission": False,
        "customer_card": {},
        "comm_id": "",
        "faq_id": "",
        "customer_message": "",
        "audit_reason": "customer_consultation_faq_match",
    }
    if match:
        payload["faq_id"] = match["faq_id"]
        payload["customer_message"] = match["reply_short"]
        payload["audit_reason"] = f"faq_match:{match.get('category','')}"
    return _safe(payload)


def check_forbidden(text: Any) -> list[str]:
    """检查文本中是否出现顾客端禁词。"""
    found = []
    s = json.dumps(_safe(text), ensure_ascii=False).lower() if not isinstance(text, str) else text.lower()
    for term in CUSTOMER_FORBIDDEN_TERMS:
        if term.lower() in s:
            found.append(term)
    return found


def main():
    kb = load_knowledge_base(DEFAULT_KB_XLSX)
    layer = kb.json_layer
    faq_lib = layer.faq_library

    print(f"KB: {len(kb.diseases)} diseases, {len(faq_lib)} FAQ")

    # ── 30 门店场景 ──
    store_cases = [
        ("S01", "intake", "腰酸腿麻，经常腰痛，想按一下。"),
        ("S02", "intake", "腰痛，腿麻，而且这两天脚有点没力。"),
        ("S03", "intake", "腰痛，最近大小便有点不正常，会阴那里也麻麻的。"),
        ("S04", "intake", "腰骶臀区睡觉时疼痛，白天好像还行。"),
        ("S05", "followup_question", "就是睡久了压着痛，起来活动一下就好了，没有腿麻，没有无力，大小便也正常。"),
        ("S06", "followup_question", "晚上经常痛醒，最近越来越痛，休息也不太缓解。"),
        ("S07", "intake", "尾椎骨痛，隐隐约约，持续半年了。"),
        ("S08", "intake", "下肢疼痛，比较轻，就是想放松一下。"),
        ("S09", "intake", "全身放松，无明显不适，就是理疗卡维护一下。"),
        ("S10", "intake", "我今天想做针灸调理，没什么明显不舒服。"),
        ("S11", "intake", "我坐久了一站起来屁股刺痛，反复几个月了。"),
        ("S12", "intake", "坐飞机回来后，腰臀后侧一片紧痛，腿后侧也紧，没有麻木无力。"),
        ("S13", "intake", "我经期前后容易头痛，颈部很紧，有时头顶也痛。"),
        ("S14", "post_service_feedback", "这次做完只改善三成，没有上次明显。"),
        ("S15", "technician_during_service", "按着按着他说脚好像越来越没力，走路发软。"),
        ("S16", "intake", "颈椎病不舒服，想按肩颈。"),
        ("S17", "intake", "颈肩痛，偶尔手麻，没有无力。"),
        ("S18", "intake", "颈肩痛，手麻，而且手有点拿不稳东西。"),
        ("S19", "intake", "肩周炎，手有点麻，想松一下肩膀。"),
        ("S20", "intake", "膝盖痛，走路有点不稳，偶尔打软腿。"),
        ("S21", "intake", "腰不舒服，说不清楚，就是酸酸胀胀的。"),
        ("S22", "intake", "背腰骶隐隐约约不舒服，定期保养。"),
        ("S23", "intake", "下肢小腿酸胀，比较轻，没有肿胀，没有发热，能正常走路。"),
        ("S24", "intake", "小腿一边突然肿起来，还有点发热发红。"),
        ("S25", "intake", "胸口闷痛，肩背也有点牵着痛，想按一下背。"),
        ("S26", "intake", "昨天摔了一跤，今天腰痛明显，动一下就痛。"),
        ("S27", "intake", "我想做重手法，越重越好，腰背很紧。"),
        ("S28", "intake", "我想拔罐，最近有点感冒发热，背也酸。"),
        ("S29", "post_service_feedback", "连续两次做完都只舒服一点点，第二天又反复。"),
        ("S30", "technician_during_service", "服务过程中顾客突然说头很晕，还恶心想吐。"),
    ]

    store_results = []
    anomalies = []

    for case_id, stage, text in store_cases:
        try:
            result = triage_customer_complaint(text, kb, use_semantic=True, stage=stage)
            payload = build_store_payload(result, text, stage)
            store_results.append({"case": case_id, "payload": payload})

            ft = payload["red_flag_result"]
            sp = payload["service_path"]
            tc = payload["technician_card_permission"]
            print(f"  [{case_id}] triage={ft} path={sp} tech_card={tc}")

            # 异常检测
            if ft == "P3":
                anomalies.append(f"[{case_id}] 输出 P3 — {text[:40]}")
            if sp not in ("hospital", "clinic_review", "continue_question",
                          "disease_based_service", "region_based_service",
                          "store_followup", "permission_review"):
                anomalies.append(f"[{case_id}] service_path 不在枚举集中: {sp}")
            if "body_region_unknown" in payload.get("semantic_facts", {}) and payload["semantic_facts"].get("body_region_unknown"):
                anomalies.append(f"[{case_id}] body_region_unknown=True — {text[:40]}")
            if ft == "P2" and not tc:
                anomalies.append(f"[{case_id}] P2 但 technician_card_permission=false — {text[:40]}")

            # 顾客端禁词检查
            cust_card = payload.get("customer_card", {})
            cust_msg = payload.get("customer_message", "")
            forbidden = check_forbidden(cust_card)
            forbidden += check_forbidden(cust_msg)
            if forbidden:
                anomalies.append(f"[{case_id}] 顾客端禁词: {', '.join(forbidden)}")

            if stage == "technician_during_service" and ft == "P2":
                anomalies.append(f"[{case_id}] 服务中疑似未重新分诊 — {text[:40]}")

        except Exception as e:
            anomalies.append(f"[{case_id}] 运行异常: {e}")
            print(f"  [{case_id}] ERROR: {e}")

    # ── 10 FAQ ──
    faq_cases = [
        ("FAQ01", "我这个是不是很严重？"),
        ("FAQ02", "为什么还要问这么多？"),
        ("FAQ03", "多久能缓解？"),
        ("FAQ04", "能不能做重手法？"),
        ("FAQ05", "今天做完回家要注意什么？"),
        ("FAQ06", "我有手麻或者腿麻，是不是很危险？"),
        ("FAQ07", "夜里痛但白天好点怎么办？"),
        ("FAQ08", "做完更酸正常吗？"),
        ("FAQ09", "理疗卡怎么用更合适？"),
        ("FAQ10", "什么时候必须去医院？"),
    ]

    faq_results = []
    for case_id, question in faq_cases:
        try:
            match = faq_match(question, faq_lib)
            payload = build_faq_payload(question, match)
            faq_results.append({"case": case_id, "payload": payload})
            fid = payload["faq_id"]
            print(f"  [{case_id}] {question[:25]}... -> faq_id={fid}")
            if not fid:
                anomalies.append(f"[{case_id}] FAQ 未命中: {question}")
        except Exception as e:
            anomalies.append(f"[{case_id}] 运行异常: {e}")

    # ── 输出 JSON ──
    out = {
        "kb_version": "V9.2.2P",
        "kb_file": Path(DEFAULT_KB_XLSX).name,
        "store_case_count": len(store_results),
        "faq_case_count": len(faq_results),
        "store_results": store_results,
        "faq_results": faq_results,
    }

    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "v922p_store_blind_test_audit_v1.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nJSON saved to {json_path}")

    # ── 统计 ──
    triage_counts = {}
    path_counts = {}
    tech_false_p2 = []
    for r in store_results:
        p = r["payload"]
        t = p["red_flag_result"]
        s = p["service_path"]
        triage_counts[t] = triage_counts.get(t, 0) + 1
        path_counts[s] = path_counts.get(s, 0) + 1
        if t == "P2" and not p["technician_card_permission"]:
            tech_false_p2.append(r["case"])

    faq_hit = sum(1 for r in faq_results if r["payload"]["faq_id"])

    # ── Markdown 摘要 ──
    summary_lines = [
        f"# V9.2.2P 门店盲测验收摘要",
        f"",
        f"**知识库**: {Path(DEFAULT_KB_XLSX).name}",
        f"**路径**: `{DEFAULT_KB_XLSX}`",
        f"**病种**: {len(kb.diseases)} | FAQ: {len(faq_lib)} | COMM: {len(layer.comm_library)}",
        f"**必备表**: RULE={len(layer.red_flag_rules)} REDFLAG_SUSPECT={len(layer.redflag_suspect_rules)} QUESTION={len(layer.followup_questions)} REVIEW={len(layer.review_rules)}",
        f"",
        f"---",
        f"",
        f"## 一、30 条门店场景分流结果",
        f"",
        f"| # | stage | 输入（前30字） | final_triage | service_path | tech_card |",
        f"|---|-------|-----|-------------|-------------|----------|",
    ]

    for r in store_results:
        p = r["payload"]
        inp = p["raw_input"][:30]
        ft = p["red_flag_result"]
        sp = p["service_path"]
        tc = "allow" if p["technician_card_permission"] else "block"
        summary_lines.append(f"| {r['case']} | {p['stage'][:10]} | {inp} | {ft} | {sp} | {tc} |")

    summary_lines += [
        f"",
        f"### 分流统计",
        f"```",
    ]
    for t in ("P0", "P1", "P2", "pending_question"):
        summary_lines.append(f"  {t}: {triage_counts.get(t, 0)}")
    summary_lines += [
        f"```",
        f"",
        f"### 路径统计",
        f"```",
    ]
    for s in sorted(path_counts):
        summary_lines.append(f"  {s}: {path_counts[s]}")
    summary_lines += [
        f"```",
        f"",
        f"---",
        f"",
        f"## 二、10 条 FAQ 匹配结果",
        f"",
        f"| # | 问题 | faq_id | 回复摘要 |",
        f"|---|------|--------|---------|",
    ]
    for r in faq_results:
        p = r["payload"]
        summary_lines.append(
            f"| {r['case']} | {p['raw_input'][:30]} | {p['faq_id'] or '—'} | {p['customer_message'][:40]} |"
        )

    summary_lines += [
        f"",
        f"FAQ 命中率: {faq_hit}/{len(faq_results)}",
        f"",
        f"---",
        f"",
        f"## 三、异常项",
        f"",
    ]
    if anomalies:
        summary_lines.append(f"共 {len(anomalies)} 项异常：")
        for a in anomalies:
            summary_lines.append(f"- {a}")
    else:
        summary_lines.append("无异常。")

    summary_lines += [
        f"",
        f"---",
        f"",
        f"## 四、关键检查项",
        f"",
        f"| 检查项 | 结果 |",
        f"|---|------|",
        f"| 是否出现 P3 | {'**是** — 发现 {n} 条' if triage_counts.get('P3', 0) else '否'} |",
        f"| 是否出现顾客端禁词 | {'**是**' if any('禁词' in a for a in anomalies) else '否'} |",
        f"| body_region_unknown 场景 | {sum(1 for a in anomalies if 'body_region_unknown' in a)} 条 |",
        f"| P2 但 tech_card=false | {len(tech_false_p2)} 条（{', '.join(tech_false_p2) if tech_false_p2 else '无'}） |",
        f"| 服务中未重新分诊 | {sum(1 for a in anomalies if '未重新分诊' in a)} 条 |",
        f"| FAQ 命中率 | {faq_hit}/{len(faq_results)} |",
    ]

    md_text = "\n".join(summary_lines)
    md_path = out_dir / "v922p_store_blind_test_summary_v1.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    print(f"Markdown saved to {md_path}")
    print(f"\nAnomalies: {len(anomalies)}")


if __name__ == "__main__":
    main()
