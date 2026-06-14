"""
守一 CDSS 联调桥接服务
=======================
为前端 Clinical Chat.html 提供 WebSocket (端口 8765) + HTTP API (端口 6005)。

启动:  cd medical-diagnosis && source .venv/bin/activate && python bridge_server.py

然后打开 Clinical Chat.html 即可连接。
"""

import asyncio
import json
import os
import re
import sys
import time
import uuid
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# DeepSeek API Key — 优先从环境变量读取，若未设置则使用默认值
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY') or 'sk-09562b1e562d480dacb33a1fe1fd8642'
os.environ['DEEPSEEK_API_KEY'] = DEEPSEEK_KEY

from m1_engine import M1DiagnosisEngine
from m2_engine import M2SyndromeSelector  # legacy, kept for compatibility
from m2_engine_v39 import M2V39Engine
from m3_engine import M3ClinicalReviewEngine
from m4_engine import M4RoutingEngine
from force_link_modules import full_link
from full_pipeline import compute_final_status
from services.bridge_legacy_prescription import (
    apply_legacy_bridge_enrichment,
    build_legacy_dev_prescription_recommendation,
)
from services.m1_m2_bridge import (
    build_m2_process_kwargs,
    extract_labs_imaging_from_texts,
    extract_negative_findings_from_texts,
    legacy_bridge_prescription_path_enabled,
    normalize_string_list,
    resolve_primary_disease,
    should_gate_m2,
)
from services.m1_m2_m3_bridge import (
    build_candidate_prescription_preview,
    build_compact_initial_diagnosis_message,
    extract_m2_result_display,
)
from services.m4_bridge_followup import (
    assess_bridge_followup,
    build_followup_payload,
    clear_followup_mode,
    compute_days_since_initial,
    format_followup_assessment_message,
    get_followup_context,
    load_m1_card_bridge,
    mark_followup_mode,
    save_initial_visit_snapshot,
    suggest_initial_followup_days,
)
from services.quick_ask_engine import QuickAskEngine
from services.clinical_consultation_adapter import (
    ClinicalConsultationAdapter,
    get_previous_confirmed_encounter,
    save_bayesian_encounter,
)
from services.bridge_qa_questions import (
    build_interactive_questions,
    extract_disease_from_symptom,
)

import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

WS_HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 6005

patients_db: dict[str, dict] = {}
chatlog_db: dict[str, list[dict]] = {}
doctor_patients: dict[str, list[str]] = {}

BRIDGE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bridge_patients_db.json")


def load_persisted_state() -> None:
    if not os.path.exists(BRIDGE_DB_PATH):
        return
    try:
        with open(BRIDGE_DB_PATH, encoding="utf-8") as f:
            blob = json.load(f)
        patients_db.update(blob.get("patients_db") or {})
        chatlog_db.update(blob.get("chatlog_db") or {})
        doctor_patients.update(blob.get("doctor_patients") or {})
        snap_count = sum(1 for p in patients_db.values() if p.get("last_initial_visit"))
        print(f"  [持久化] 已加载 {len(patients_db)} 个患者（含 {snap_count} 个初诊快照）")
    except Exception as e:
        print(f"  [持久化] 加载失败: {e}")


def persist_state() -> None:
    try:
        os.makedirs(os.path.dirname(BRIDGE_DB_PATH), exist_ok=True)
        with open(BRIDGE_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "patients_db": patients_db,
                    "chatlog_db": chatlog_db,
                    "doctor_patients": doctor_patients,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        print(f"  [持久化] 保存失败: {e}")


m1 = None
m2 = None
m3 = None
m4 = None
quick_ask_engine = None
consultation_adapter = None

CHIEF_FORCE_CHRONIC_DISEASE_KEYWORDS = [
    "慢性", "过敏", "鼻炎", "鼻窦炎", "腺样体", "咽炎", "扁桃体肥大",
    "哮喘", "湿疹", "荨麻疹", "高血压", "糖尿病", "冠心病",
    "腰椎", "颈椎", "胃炎", "溃疡", "结石",
]

CHIEF_FORCE_ACUTE_CURRENT_KEYWORDS = [
    "急性", "发热", "感染", "上呼吸道", "肺炎", "支气管炎",
    "咳嗽", "感冒", "呼吸道", "扁桃体炎", "咽炎",
]


def should_skip_chief_complaint_force(chief_complaint_disease: str, current_disease: str) -> bool:
    """Avoid letting chronic history or symptom-keywords override a more specific M1 diagnosis."""
    # 慢性病关键词跳过
    is_chronic_chief = any(kw in chief_complaint_disease for kw in CHIEF_FORCE_CHRONIC_DISEASE_KEYWORDS)
    is_acute_current = any(kw in current_disease for kw in CHIEF_FORCE_ACUTE_CURRENT_KEYWORDS)
    if is_chronic_chief and is_acute_current:
        return True
    # 纯症状关键词跳过：M1 已给出更具体的诊断时，不被"腹泻""咳嗽"等泛词覆盖
    _symptom_only_keywords = ["腹泻", "腹痛", "咳嗽", "发热", "呕吐", "头痛", "头晕", "鼻塞", "咽痛"]
    if chief_complaint_disease in _symptom_only_keywords:
        print(f"[CHIEF_COMPLAINT_SKIP_SYMPTOM] 主诉病名:{chief_complaint_disease} 为症状类关键词，跳过覆盖")
        return True
    return bool(is_chronic_chief and is_acute_current)


def init():
    global m1, m2, m3, m4, quick_ask_engine, consultation_adapter
    load_persisted_state()
    print("  [引擎] M1 诊断...")
    m1 = M1DiagnosisEngine()
    print("  [引擎] M2 辨证选方 (v39 运行边库)...")
    m2 = M2V39Engine()
    print("  [引擎] M3 药学审核...")
    m3 = M3ClinicalReviewEngine()
    print("  [引擎] M4 复诊路由...")
    m4 = M4RoutingEngine()
    print("  [引擎] 医知快问（旁路）...")
    quick_ask_engine = QuickAskEngine()
    consultation_adapter = ClinicalConsultationAdapter(m1, m2, m3)
    print("  [引擎] 临床问诊适配器（双入口 M1-M4）已挂载 ✓")
    print("  [引擎] 全部就绪 ✓")


def is_corpus_quick_ask(pid: str, md: dict, data: Optional[dict] = None) -> bool:
    """医知快答（corpus/common）走 QuickAsk 旁路，不进入 M1-M4。"""
    mode = str((md or {}).get("mode") or "").strip()
    platform = str((data or {}).get("platform") or "").strip()
    actual_pid = str(pid or (md or {}).get("patient_id") or "").strip()
    return actual_pid == "common" or mode == "corpus" or platform == "corpus"


def _format_quick_ask_reply(result: dict) -> str:
    """将 QuickAsk 结构化结果格式化为前端可读文本。"""
    lines = ["【医知快问 · 知识参考，非正式诊断/处方】"]
    safety = result.get("safety_gate") or {}
    status = safety.get("status", "ALLOW_REFERENCE")
    if status == "BLOCK_FORMULA":
        lines.append("⚠️ 安全提示：存在高风险信号，不建议给出方剂参考，请及时就医。")
    elif status == "CAUTION":
        lines.append("⚠️ 请注意特殊人群/用药风险，以下信息仅供医师审核参考。")
    if safety.get("reasons"):
        lines.append("安全提醒：" + "；".join(safety["reasons"]))

    body = str(result.get("answer_text") or "").strip()
    if body:
        lines.append(body)

    intent = result.get("intent", "")
    if intent == "famous_case_search":
        cases = result.get("similar_cases") or []
        if cases:
            lines.append("\n【相似医案】")
            for c in cases[:3]:
                lines.append(
                    f"• {c.get('disease_name', '')} / {c.get('syndrome', '')} — {c.get('formula', '')}"
                )
    elif intent == "formula_info":
        if "【参考方剂" not in body:
            formulas = result.get("reference_formulas") or []
            if formulas:
                lines.append("\n【方剂参考 · REFERENCE_ONLY】")
                for fm in formulas[:2]:
                    deco = str(fm.get("full_decoction") or "").strip()
                    if deco:
                        lines.append(f"• {fm.get('formula_name', '')}（{fm.get('matched_syndrome') or fm.get('syndrome_name', '')}）\n{deco}")
                    else:
                        herbs = fm.get("core_herbs") or fm.get("herbs") or []
                        herb_txt = "、".join(herbs[:6]) if herbs else "（组成见知识库）"
                        lines.append(
                            f"• {fm.get('formula_name', '')}（{fm.get('matched_syndrome') or fm.get('syndrome_name', '')}）{herb_txt}"
                        )
    elif intent == "disease_inquiry":
        cases = result.get("similar_cases") or []
        if cases and "【病案参考】" not in body:
            lines.append("\n【病案参考】")
            for c in cases[:3]:
                lines.append(
                    f"• {c.get('disease_name', '')} / {c.get('syndrome', '')} — {c.get('formula', '')}"
                )

    if result.get("recommend_formal_chain"):
        lines.append("\n💡 信息较完整，建议进入「患者记录」正式问诊链路进一步辨证。")

    lines.append("\n— 以上为知识库检索参考，不构成处方或随访方案。")
    return "\n".join(lines)


def run_clinical_consultation(body: dict) -> dict:
    """双入口临床问诊：右侧自由快问 / 左侧结构化，统一走 M1-M4。"""
    global consultation_adapter, m1, m2, m3
    if m1 is None or m2 is None or m3 is None:
        init()
    if consultation_adapter is None:
        consultation_adapter = ClinicalConsultationAdapter(m1, m2, m3)

    mode = str(body.get("mode") or "free_bayesian").strip()
    pid = str(body.get("patientId") or "").strip()
    patient = get_patient(pid) if pid else None

    if body.get("action") == "get_confirmed_encounter":
        if not patient:
            return {"ok": False, "error": "patient not found", "channel": "clinical_consultation"}
        prev = get_previous_confirmed_encounter(patient)
        if not prev:
            return {
                "ok": True,
                "channel": "clinical_consultation",
                "has_confirmed": False,
                "message": "当前患者暂无正式问诊记录，请先建立初诊记录。",
            }
        return {"ok": True, "channel": "clinical_consultation", "has_confirmed": True, "previousEncounter": prev}

    if body.get("action") == "save_draft":
        if not patient:
            return {"ok": False, "error": "patient not found", "channel": "clinical_consultation"}
        result = body.get("bayesianResult") or body.get("result") or {}
        status = str(body.get("encounterStatus") or "draft").strip()
        if status not in ("knowledge_only", "draft", "confirmed"):
            status = "draft"
        entry = save_bayesian_encounter(
            patient,
            result,
            raw_text=str(body.get("rawText") or ""),
            status=status,
        )
        persist_state()
        return {"ok": True, "channel": "clinical_consultation", "saved": entry, "encounterStatus": status}

    if body.get("followup") or body.get("action") == "followup":
        if not patient:
            return {"ok": False, "error": "patient not found", "channel": "clinical_consultation"}
        prev = get_previous_confirmed_encounter(patient)
        if not prev:
            return {
                "ok": False,
                "channel": "clinical_consultation",
                "error": "NO_CONFIRMED_ENCOUNTER",
                "message": "当前患者暂无正式问诊记录，请先建立初诊记录。",
            }
        try:
            result = consultation_adapter.run_followup(body, patient, prev)
        except Exception as e:
            return {"ok": False, "channel": "clinical_consultation", "error": str(e)}
        output_adapter = result.get("outputAdapter") or "chat_card"
        return {
            "ok": True,
            "channel": "free_bayesian" if output_adapter == "chat_card" else "clinical_consultation",
            "status": "ok",
            "reference_only": output_adapter == "chat_card",
            "outputAdapter": output_adapter,
            "clinicalCorePrompt": (result.get("clinicalCoreContext") or {}).get("prompt_id"),
            "bayesian_quick_result": result,
            "consultation_result": result,
            "answer": {"text": result.get("finalText", ""), "role": "assistant", "timestamp": time.time()},
        }

    try:
        result = consultation_adapter.run_consultation(body, patient=patient)
    except Exception as e:
        print(f"  [ClinicalConsultation ERR] {e}")
        return {"ok": False, "channel": "clinical_consultation", "error": str(e)}

    output_adapter = result.get("outputAdapter") or "chat_card"
    core_ctx = result.get("clinicalCoreContext") or {}
    base = {
        "ok": True,
        "status": "ok",
        "mode": result.get("mode") or mode,
        "outputAdapter": output_adapter,
        "clinicalCorePrompt": core_ctx.get("prompt_id"),
        "clinicalCoreContext": core_ctx,
        "structuredExtract": result.get("structuredExtract"),
        "answer": {
            "text": result.get("finalText", ""),
            "role": "assistant",
            "timestamp": time.time(),
            "source": "clinical_consultation",
        },
    }
    if output_adapter == "strict_structured":
        base.update({
            "channel": "clinical_consultation",
            "reference_only": False,
            "consultation_result": result,
            "encounter": result.get("encounter"),
            "encounterStatus": result.get("encounterStatus", "draft"),
        })
    else:
        base.update({
            "channel": "free_bayesian",
            "reference_only": True,
            "bayesian_quick_result": result,
            "encounterStatus": result.get("encounterStatus", "knowledge_only"),
        })
    return base


def run_quick_ask(query: str) -> dict:
    """医知快问旁路：不写入患者库、不调用 M1-M4。"""
    global quick_ask_engine
    if quick_ask_engine is None:
        quick_ask_engine = QuickAskEngine()
    q = str(query or "").strip()
    if not q:
        return {
            "ok": False,
            "channel": "quick_ask",
            "error": "query is required",
            "status": "error",
        }
    result = quick_ask_engine.ask(q)
    result.pop("final_prescription", None)
    reply_text = _format_quick_ask_reply(result)
    return {
        "ok": True,
        "channel": "quick_ask",
        "status": "ok",
        "reference_only": True,
        "query": q,
        "answer": {
            "text": reply_text,
            "role": "assistant",
            "timestamp": time.time(),
            "source": "quick_ask",
        },
        "quick_ask": result,
    }


async def chat_quick_ask(ws, pid: str, text: str, md: dict):
    """医知快答 WS 处理：QuickAsk 旁路，仅写 common 聊天缓存。"""
    actual_pid = pid or "common"
    await ws.send(json.dumps({"answer": {"text": "正在检索知识库..."}, "status": "processing"}, ensure_ascii=False))
    await asyncio.sleep(0.15)
    try:
        payload = run_quick_ask(text)
        reply = payload.get("answer", {}).get("text", "")
        add_log(actual_pid, "assistant", {"text": reply})
        await ws.send(json.dumps({
            "answer": {
                "text": reply,
                "patient_id": actual_pid,
                "timestamp": time.time(),
                "source": "quick_ask",
            },
            "channel": "quick_ask",
            "reference_only": True,
            "quick_ask": payload.get("quick_ask"),
            "status": "ok",
        }, ensure_ascii=False))
    except Exception as e:
        print(f"  [QuickAsk ERR] {e}")
        err = "医知快问暂时不可用，请稍后重试。"
        add_log(actual_pid, "assistant", {"text": err})
        await ws.send(json.dumps({
            "answer": {"text": err, "patient_id": actual_pid, "timestamp": time.time()},
            "status": "error",
        }, ensure_ascii=False))


def get_patient(pid: str) -> Optional[dict]:
    return patients_db.get(pid)


def ensure_patient(pid: str, doc: str = "") -> dict:
    if pid not in patients_db:
        patients_db[pid] = {
            "patient_id": pid, "name": "未知患者",
            "age": "", "gender": "", "phone": "", "memo": "",
            "detail": {}, "created_at": time.time(), "doctor_id": doc,
        }
    if doc and pid not in doctor_patients.setdefault(doc, []):
        doctor_patients[doc].append(pid)
    return patients_db[pid]


def get_log(pid: str) -> list:
    return chatlog_db.setdefault(pid, [])


def _parse_followup_days(advice: str) -> int:
    m = re.search(r"建议(\d+)天后", advice or "")
    if m:
        return max(1, min(int(m.group(1)), 30))
    return 7


def record_initial_visit_to_patient(
    pid: str,
    *,
    disease: str,
    syndrome_name: str,
    formula_name: str,
    symptom_text: str,
    followup_days: int,
    herb_items: Optional[list] = None,
    final_status: str = "",
) -> None:
    """初诊完成后写入患者就诊时间线，供前端「记录跟进」与复诊提醒使用。"""
    patient = get_patient(pid)
    if not patient:
        return
    detail = patient.setdefault("detail", {})
    timeline = detail.setdefault("treatment_timeline", [])
    now_ms = int(time.time() * 1000)
    visit = {
        "timestamp": time.time(),
        "visit_date": now_ms,
        "symptom": symptom_text or "",
        "disease_name": disease or "",
        "syndrome": syndrome_name or "",
        "formula_name": formula_name or "",
        "prescription": formula_name or "",
        "treatment_completed": True,
        "final_status": final_status or "",
        "followup_days": followup_days,
        "treatment": {
            "timestamp": time.time(),
            "disease_name": disease or "",
            "syndrome": syndrome_name or "",
            "prescription": formula_name or "",
            "diseases": [{
                "病名": disease or "",
                "name": disease or "",
                "证型": syndrome_name or "",
                "syndrome": syndrome_name or "",
                "prescription": formula_name or "",
                "formula_name": formula_name or "",
                "治疗方案": {"辨证选方": formula_name or "", "方剂": formula_name or ""},
            }],
        },
    }
    if herb_items:
        visit["herb_items"] = herb_items
    timeline.append(visit)
    patient["last_diagnosis_summary"] = {
        "disease": disease,
        "syndrome": syndrome_name,
        "formula": formula_name,
        "herb_items": herb_items or [],
        "followup_days": followup_days,
        "final_status": final_status,
    }
    detail["last_treatment"] = visit["treatment"]
    patient["prescriptionDays"] = followup_days
    patient["nextFollowupDate"] = now_ms + followup_days * 86400000
    patient["lastVisit"] = now_ms
    patient["needsFollowUp"] = False
    patient["daysOverdue"] = 0


def add_log(pid: str, role: str, content: dict):
    log = get_log(pid)
    m = {
        "chatuuid": str(uuid.uuid4()),
        "from": role, "role": role,
        "content": content.get("text", ""),
        "timestamp": time.time(),
        "type": "text", "source": "web",
    }
    if content.get("imgurl"):
        m["type"] = "image"
        m["content"] = content["imgurl"]
    if content.get("selection_q"):
        m["type"] = "selection_q"
    log.append(m)
    return m


# ── HTTP ──

class HTTPHandler(BaseHTTPRequestHandler):
    def _cors(self):
        # 允许的前端来源
        origin = self.headers.get("Origin", "")
        allowed_origins = [
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
        ]
        if origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
    def _json(self, d, s=200):
        self.send_response(s); self._cors()
        self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode("utf-8"))
    def _body(self):
        l = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(l)) if l else {}
    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if p in ("", "/"):
            self._json({"ok": True, "service": "shouyi-cdss-bridge"})
        elif p == "/patients":
            doc = q.get("doctor_id", [""])[0]
            ids = doctor_patients.get(doc, [])
            all_p = list(patients_db.values())
            self._json({"ok": True, "patients": all_p if not ids else [get_patient(i) for i in ids if get_patient(i)] or all_p})
        elif p.startswith("/get_patients_chatlog"):
            pid = p.split("/")[-1]
            self._chatlog(pid)
        elif p.startswith("/patients/"):
            pid = p.split("/")[-1]
            patient = get_patient(pid)
            if patient:
                self._json({"ok": True, "patient_info": patient})
            else:
                self._json({"ok": False, "error": "patient not found"}, 404)
        elif p == "/quick_ask":
            q = q.get("query", [""])[0]
            payload = run_quick_ask(q)
            self._json(payload, 200 if payload.get("ok") else 400)
        elif p == "/clinical_consultation":
            pid = q.get("patient_id", [""])[0]
            action = q.get("action", ["get_confirmed_encounter"])[0]
            payload = run_clinical_consultation({"action": action, "patientId": pid})
            self._json(payload, 200 if payload.get("ok") else 400)
        else:
            self._json({"ok": False, "error": "not_found"}, 404)
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        b = self._body()
        if p == "/patients":
            self._create_patient(b)
        elif p.startswith("/get_patients_chatlog"):
            pid = p.split("/")[-1]
            self._chatlog(pid)
        elif p.startswith("/clear_patients_chatlog"):
            pid = p.split("/")[-1]
            actual = pid.replace("__hanfang","").replace("__qingda","")
            if actual.startswith("common"): actual = "common"
            chatlog_db[actual] = []
            self._json({"ok": True})
        elif p == "/quick_ask":
            payload = run_quick_ask(b.get("query", ""))
            self._json(payload, 200 if payload.get("ok") else 400)
        elif p == "/clinical_consultation":
            payload = run_clinical_consultation(b)
            self._json(payload, 200 if payload.get("ok") else 400)
        elif p == "/e2e/seed_snapshot":
            if os.environ.get("BRIDGE_E2E_SEED") != "1":
                self._json({"ok": False, "error": "BRIDGE_E2E_SEED not enabled"}, 403)
                return
            pid = b.get("patient_id", "")
            if not pid or not get_patient(pid):
                self._json({"ok": False, "error": "patient not found"}, 404)
                return
            save_initial_visit_snapshot(
                patients_db,
                pid,
                disease=b.get("disease", "急性支气管炎"),
                initial_symptoms=b.get("initial_symptoms") or ["咳嗽", "恶寒", "痰白稀"],
                m1_r=b.get("m1_r") or {
                    "primary_diagnosis": "急性支气管炎",
                    "disease_key": "acute_bronchitis",
                    "stage": "acute",
                },
                m2_r=b.get("m2_r") or {"selected_syndrome": "风寒袭肺", "draft_prescription": {}},
                m3_r=b.get("m3_r") or {"m3_status": "PASS"},
                syndrome_name=b.get("syndrome_name", "风寒袭肺"),
                formula_name=b.get("formula_name", "三拗汤合止嗽散"),
                patient_age=str(b.get("age") or get_patient(pid).get("age", "")),
            )
            days_ago = int(b.get("days_since_initial") or 3)
            snap = patients_db[pid].get("last_initial_visit")
            if snap:
                snap["saved_at"] = time.time() - days_ago * 86400
            persist_state()
            self._json({"ok": True, "patient_id": pid, "seeded": True})
        else:
            self._json({"ok": False, "error": "not_found"}, 404)
    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        if p.startswith("/patients"):
            pid = p.split("/")[-1] if len(p.split("/")) > 2 else ""
            patients_db.pop(pid, None); chatlog_db.pop(pid, None)
            for d in list(doctor_patients.keys()):
                if pid in doctor_patients[d]: doctor_patients[d].remove(pid)
            self._json({"ok": True})
        else:
            self._json({"ok": False}, 404)
    def _create_patient(self, body):
        pid = body.get("patient_id") or f"p_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        doc = body.get("doctor_id", "")
        p = ensure_patient(pid, doc)
        for k in ("name","age","gender","phone","memo"):
            if body.get(k): p[k] = body[k]
        if body.get("detail"): p["detail"] = body["detail"]
        persist_state()
        self._json({"ok": True, "patient_id": pid, "patient_info": p})
    def _chatlog(self, pid):
        actual = pid.replace("__hanfang","").replace("__qingda","")
        if actual.startswith("common"): actual = "common"
        self._json({"ok": True, "messages": get_log(actual)})


def run_http():
    HTTPServer(("0.0.0.0", HTTP_PORT), HTTPHandler).serve_forever()


# ── WS ──

async def ws_handler(ws):
    cid = id(ws)
    print(f"  [WS] 新连接: {cid}")
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "ping":
                await ws.send(json.dumps({"type":"pong","timestamp":time.time()}))
                continue

            if data.get("type") == "quick_ask":
                payload = run_quick_ask(data.get("query") or data.get("text", ""))
                await ws.send(json.dumps(payload, ensure_ascii=False))
                continue

            if data.get("type") == "clinical_consultation":
                payload = run_clinical_consultation(data)
                await ws.send(json.dumps(payload, ensure_ascii=False))
                continue

            md = data.get("message_data", {})
            pid = md.get("patient_id", "")
            text = md.get("text", "")

            # --- 处理 structured selection answers ---
            if data.get("type") == "selection_answers" or data.get("selection_answers"):
                print("[WS_RECEIVE_SELECTION_ANSWERS]", data)
                # selection_answers 可能直接携带 patient_id（前端直接发送），也可能在 message_data 中
                sa_pid = data.get("patient_id") or pid
                await handle_selection_answers(ws, sa_pid, data)
                continue

            if md.get("activate_only"):
                log = get_log(pid or "common")
                for m in log[-20:]:
                    await ws.send(json.dumps({
                        "answer": {
                            "text": m.get("content",""),
                            "role": m.get("role","user"),
                            "patient_id": pid or "common",
                            "timestamp": m.get("timestamp", time.time()),
                            "uuid": m.get("chatuuid",""),
                        },
                        "status": "ok", "check": {"text": "ok"}
                    }, ensure_ascii=False))
                continue

            if md.get("generate_treatment"):
                await gen_treatment(ws, pid, md)
                continue
            if md.get("start_interactive_qa"):
                await start_qa(ws, pid, md)
                continue
            if text:
                add_log(pid or "common", "user", {"text": text})
                if is_corpus_quick_ask(pid, md, data):
                    print(f"  [QuickAsk ROUTE] corpus 消息 -> QuickAsk: {text[:80]}")
                    await chat_quick_ask(ws, pid or "common", text, md)
                else:
                    await chat(ws, pid, text, md)
    except Exception as e:
        print(f"  [WS] 断开 {cid}: {e}")


async def chat(ws, pid: str, text: str, md: dict):
    # 医知快答不得进入 M1-M4 正式链路（双保险，防止路由遗漏）
    if is_corpus_quick_ask(pid, md):
        print(f"  [QuickAsk ROUTE] chat() 重定向旁路 pid={pid or 'common'}")
        await chat_quick_ask(ws, pid or "common", text, md)
        return
    patient = get_patient(pid) or {}
    await ws.send(json.dumps({"answer":{"text":"正在分析..."},"status":"processing"}, ensure_ascii=False))
    await asyncio.sleep(0.3)

    try:
        symptoms = [s.strip() for s in text.replace("，",",").split(",") if s.strip()]
        chief = symptoms[0] if symptoms else text
        symptom_text = text

        # 尝试从主诉中提取患者提到的疾病名
        mentioned = ""
        mention_keywords = ["诊断", "确诊", "考虑", "考虑为", "患"]
        for kw in mention_keywords:
            if kw in symptom_text:
                idx = symptom_text.find(kw)
                rest = symptom_text[idx+len(kw):].strip()
                # 去掉冒号等前缀
                rest = rest.lstrip("：:：:")
                # 取第一个逗号/句号/空格/换行前的内容
                import re
                match = re.match(r'^[^，,。.\s]+', rest)
                if match:
                    mentioned = match.group().strip()
                    break
        # 如果上述关键词未提取到，尝试"手术"关键词——病名在"手术"之前
        if not mentioned and "手术" in symptom_text:
            idx = symptom_text.find("手术")
            # 取"手术"前最多8个字
            before = symptom_text[max(0, idx-8):idx].strip()
            # 从后往前取最后一个"了""的"等之后的内容
            for sep in ["了", "的", "做", "行", "接受"]:
                if sep in before:
                    before = before[before.rfind(sep)+len(sep):].strip()
                    break
            # 去掉标点
            before = before.rstrip("，, 、")
            if before and len(before) >= 2:
                mentioned = before
        if not mentioned:
            mentioned = extract_disease_from_symptom(symptom_text)
        
        m1_input = {
            "patient_mentioned_disease": mentioned,
            "chief_complaint": chief,
            "symptoms": symptoms,
            "signs": [], "labs": [], "imaging": [],
            "negative_findings": [], "duration": "", "onset": "",
        }
        m1_r = m1.diagnose(m1_input)

        # ── M1→M2 门控（Skill v2.3 Step：进入规则）──
        _gate_blocked, m1_diagnosis_status = should_gate_m2(m1_r)
        m1_emergency = m1_r.get("emergency_alert", {}).get("triggered", False) if isinstance(m1_r, dict) else False

        if _gate_blocked or m1_emergency:
            print(f"[M1_GATE] 诊断状态={m1_diagnosis_status}，紧急征={m1_emergency}，阻断进入 M2")

        disease = resolve_primary_disease(m1_r, chief)
        m1_out = "暂无结果"
        if isinstance(m1_r, dict):
            primary = m1_r.get("primary_diagnosis", "")
            diag_list = m1_r.get("top_diagnoses", [])
            if not diag_list:
                diag_list = m1_r.get("diagnosis_calibration", {}).get("calibrated_diagnosis", [])
            if diag_list:
                disease = disease or primary or (diag_list[0]["disease_name"] if isinstance(diag_list[0], dict) else diag_list[0])
            m1_out = " -> ".join([d["disease_name"] if isinstance(d, dict) else str(d) for d in diag_list[:3]])
        if not disease:
            disease = chief

        # ── 门控阻断标记 ──
        _gate_blocked = _gate_blocked or m1_emergency

        link = {}
        if disease:
            link = full_link([disease])

        parts = [f"【诊断分析】\n{m1_out[:800]}"]
        if link.get("prognosis"):
            w = link["prognosis"].get("matched_window",{})
            parts.append(f"\n【疗程参考】\n首次反应: {w.get('首次反应','待查')}\n显著改善: {w.get('显著改善','待查')}\n稳定控制: {w.get('稳定控制','待查')}")
        if link.get("top3_cases"):
            parts.append("\n【病案参考】")
            for c in link["top3_cases"][:2]:
                parts.append(f"• {c.get('formula', c.get('syndrome', '参考病案'))}")
        reply = "\n\n".join(parts)

        # 门控检查：门控状态下跳过 M2
        try:
            if _gate_blocked:
                print(f"[M1_GATE] 跳过 M2（诊断状态={m1_diagnosis_status}）")
                m2_r = None
            else:
                m2_kwargs = build_m2_process_kwargs(
                    primary_disease=disease,
                    m1_result=m1_r if isinstance(m1_r, dict) else None,
                    symptoms=symptoms,
                    age=patient.get("age", ""),
                    weight=patient.get("weight", ""),
                )
                m2_r = m2.process(**m2_kwargs)
            if m2_r and not m2_r.get("error"):
                display = extract_m2_result_display(m2_r)
                ss_name = display.get("syndrome_name", "")
                formula_name = display.get("formula_name", "")
                if ss_name and formula_name:
                    reply += f"\n\n【辨证处方】\n证型: {ss_name}\n方剂: {formula_name}"
                    herbs = display.get("herbs", [])
                    if herbs:
                        reply += f"\n药物: {'、'.join(herbs[:8])}"
        except Exception as e:
            print(f"  M2 err: {e}")

        try:
            herbs = m2_r.get("formula",{}).get("herbs",[]) if m2_r else []
            if herbs:
                m3_r = m3.review(herbs, {k: patient.get(k,"") for k in ["age","gender","weight","pregnancy","lactation"]})
                if m3_r.get("warnings"):
                    reply += f"\n\n【用药警示】\n" + "\n".join(m3_r["warnings"][:3])
        except Exception as e:
            print(f"  M3 err: {e}")

    except Exception as e:
        print(f"  [ERR] {e}")
        reply = f"已收到您的描述。\n\n主诉: {text}"

    add_log(pid, "assistant", {"text": reply})
    await ws.send(json.dumps({"answer":{"text":reply,"patient_id":pid,"timestamp":time.time()},"status":"ok"}, ensure_ascii=False))


async def start_qa(ws, pid: str, md: dict):
    print("[WS_START_QA_RECEIVED]", {"pid": pid, "has_md": bool(md), "patient_info": md.get("patient_info"), "start_followup": md.get("start_followup")})
    if md.get("start_followup") and md.get("followup_symptom"):
        mark_followup_mode(patients_db, pid, md.get("followup_symptom", ""))
        print("[M4_FOLLOWUP_MODE]", {"pid": pid, "symptom": md.get("followup_symptom", "")[:80]})
    patient = get_patient(pid) or {}
    # 如果 patient 没有 detail.symptom，尝试从 md 中的 patient_info 补充
    if (not patient.get("detail") or not patient.get("detail", {}).get("symptom")):
        pi = md.get("patient_info")
        if isinstance(pi, dict):
            for k in ("name","age","gender","phone","memo"):
                if pi.get(k) and not patient.get(k):
                    patient[k] = pi[k]
            if pi.get("detail"):
                patient["detail"] = pi["detail"]
            elif pi.get("symptom"):
                patient.setdefault("detail", {})["symptom"] = pi["symptom"]
            patients_db[pid] = patient
            print("[START_QA_PATIENT_UPDATED]", {"pid": pid, "symptom": patient.get("detail",{}).get("symptom","")[:50]})
    # === 根据主诉病名 + 诊断库 must_ask_questions 生成针对性问题 ===
    symptom_text_q = patient.get('detail', {}).get('symptom', '')
    _found_disease = extract_disease_from_symptom(symptom_text_q)
    _m1_card = None
    try:
        with open('data/m1_diagnostic_cards.json', 'r', encoding='utf-8') as _f:
            _all_cards = json.load(_f).get('diagnostic_cards', [])
        for _c in _all_cards:
            if _c.get('diseaseName_cn', '') == _found_disease:
                _m1_card = _c
                break
        if not _m1_card and _found_disease:
            for _c in _all_cards:
                if _found_disease in str(_c.get('diseaseName_cn', '')):
                    _m1_card = _c
                    break
    except Exception as e:
        print("[START_QA_M1_CARD_LOAD_ERROR]", str(e)[:100])
    _found_disease, questions = build_interactive_questions(symptom_text_q, _m1_card, limit=6)
    print("[START_QA_DISEASE]", {"found": _found_disease, "first_q": questions[0]["question"] if questions else ""})
    welcome = f"您好！已启动智能问诊。\n\n患者: {patient.get('name','新患者')} | {patient.get('gender','')} {patient.get('age','')}岁\n\n请回答以下问题："
    add_log(pid, "assistant", {"text": welcome})
    for q in questions:
        sq = {"q": q["question"], "sel": q["options"]}
        add_log(pid, "assistant", {"text": q["question"], "selection_q": sq})
    response = {
        "type": "selection_q",
        "patient_id": pid,
        "questions": questions,
        "status": "ok",
    }
    print("[WS_SEND_SELECTION_Q]", {"pid": pid, "question_count": len(questions), "first_q": questions[0]["question"] if questions else ""})
    await ws.send(json.dumps(response, ensure_ascii=False))


async def handle_followup_visit(
    ws,
    pid: str,
    patient: dict,
    answers: dict,
    followup_ctx: dict,
    snapshot: dict,
):
    """复诊：M4 评估 → 按路由决定是否回流 M1/M2/M3。"""
    followup_symptom = followup_ctx.get("symptom", "")
    patient_age = patient.get("age", "")
    days = compute_days_since_initial(snapshot)
    payload = build_followup_payload(followup_symptom, answers, days)

    print("[M4_FOLLOWUP_ASSESS_START]", {"pid": pid, "days": days, "lines": len(payload.get("symptom_lines", []))})
    try:
        assessment = assess_bridge_followup(
            snapshot,
            payload,
            patient_age=str(patient_age or ""),
        )
    except Exception as e:
        print("[M4_FOLLOWUP_ASSESS_ERR]", str(e))
        clear_followup_mode(patients_db, pid)
        await ws.send(json.dumps({
            "answer": {"text": f"复诊评估异常：{e}", "patient_id": pid},
            "status": "error",
        }, ensure_ascii=False))
        return

    clear_followup_mode(patients_db, pid)
    m4_text = format_followup_assessment_message(assessment)
    print("[M4_FOLLOWUP_ASSESS_DONE]", {
        "pid": pid,
        "status": assessment.get("followup_status"),
        "m1": assessment.get("need_reenter_m1"),
        "m2": assessment.get("need_reenter_m2"),
        "m3": assessment.get("need_reenter_m3"),
    })

    need_m1 = bool(assessment.get("need_reenter_m1"))
    need_m2 = bool(assessment.get("need_reenter_m2"))
    need_m3 = bool(assessment.get("need_reenter_m3"))

    # 好转稳定：仅返回 M4 随访结论，不进入改方
    if not need_m1 and not need_m2 and not need_m3:
        add_log(pid, "assistant", {"text": m4_text})
        await ws.send(json.dumps({
            "type": "followup_result",
            "patient_id": pid,
            "followup_assessment": assessment,
            "answer": {
                "text": m4_text,
                "patient_id": pid,
                "interactive_qa_finished": True,
                "timestamp": time.time(),
            },
            "status": "ok",
        }, ensure_ascii=False))
        persist_state()
        return

    extra_lines = []
    disease = (snapshot.get("initial_m1") or {}).get("primary_diagnosis", "")
    syndrome_name = (snapshot.get("initial_m2") or {}).get("selected_syndrome", "")
    formula_name = (snapshot.get("initial_m2") or {}).get("base_formula", "")
    stored_m2 = snapshot.get("m2_result") if isinstance(snapshot.get("m2_result"), dict) else None

    followup_symptoms = payload.get("symptom_lines") or [followup_symptom]
    m2_r = stored_m2
    m3_r = None

    try:
        if need_m3 and not need_m1 and not need_m2:
            if stored_m2:
                from services.m1_m2_m3_bridge import build_m3_patient_context
                m3_r = m3.review_m2_handoff(
                    stored_m2,
                    patient=build_m3_patient_context(None, stored_m2, {
                        k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]
                    } | {"symptom_text": followup_symptom}),
                    diagnosis=disease,
                )
                extra_lines.append("已触发 M3 安全复核。")
            else:
                extra_lines.append("缺少初诊 M2 快照，无法自动进入 M3，请人工复核。")

        elif need_m2 and not need_m1:
            m2_kwargs = build_m2_process_kwargs(
                primary_disease=disease,
                m1_result={"primary_diagnosis": disease},
                symptoms=followup_symptoms,
                age=patient_age,
                weight=patient.get("weight", ""),
            )
            m2_r = m2.process(**m2_kwargs)
            if m2_r and not m2_r.get("error"):
                display = extract_m2_result_display(m2_r)
                syndrome_name = display.get("syndrome_name") or syndrome_name
                formula_name = display.get("formula_name") or formula_name
                extra_lines.append(f"已回流 M2 重新辨证：{syndrome_name or '待辨证'}")
                m3_r = m3.review_m2_handoff(
                    m2_r,
                    patient={k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]}
                    | {"symptom_text": followup_symptom},
                    diagnosis=disease,
                )
            else:
                extra_lines.append("M2 回流失败，请人工处理。")

        elif need_m1:
            m1_input = {
                "chief_complaint": followup_symptom,
                "symptoms": followup_symptoms,
                "signs": [], "labs": [], "imaging": [],
                "negative_findings": [], "duration": f"{days}天", "onset": "",
            }
            m1_r = m1.diagnose(m1_input)
            disease = resolve_primary_disease(m1_r, followup_symptom) or disease
            extra_lines.append(f"已回流 M1 重新诊断：{disease}")
            m2_kwargs = build_m2_process_kwargs(
                primary_disease=disease,
                m1_result=m1_r if isinstance(m1_r, dict) else None,
                symptoms=followup_symptoms,
                age=patient_age,
                weight=patient.get("weight", ""),
            )
            m2_r = m2.process(**m2_kwargs)
            if m2_r and not m2_r.get("error"):
                display = extract_m2_result_display(m2_r)
                syndrome_name = display.get("syndrome_name") or syndrome_name
                formula_name = display.get("formula_name") or formula_name
                m3_r = m3.review_m2_handoff(
                    m2_r,
                    patient={k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]}
                    | {"symptom_text": followup_symptom},
                    diagnosis=disease,
                )
    except Exception as e:
        print("[M4_FOLLOWUP_REROUTE_ERR]", str(e))
        extra_lines.append(f"后续模块调用异常：{e}")

    if m2_r and not m2_r.get("error"):
        save_initial_visit_snapshot(
            patients_db,
            pid,
            disease=disease,
            initial_symptoms=followup_symptoms,
            m1_r={"primary_diagnosis": disease},
            m2_r=m2_r,
            m3_r=m3_r,
            syndrome_name=syndrome_name,
            formula_name=formula_name,
            patient_age=str(patient_age or ""),
        )
        persist_state()

    full_text = m4_text
    if extra_lines:
        full_text += "\n\n" + "\n".join(extra_lines)
    if syndrome_name or formula_name:
        full_text += f"\n\n当前辨证参考：{syndrome_name or '待辨证'}"
        if formula_name:
            full_text += f"\n候选方：{formula_name}"

    add_log(pid, "assistant", {"text": full_text})
    await ws.send(json.dumps({
        "type": "followup_result",
        "patient_id": pid,
        "followup_assessment": assessment,
        "answer": {
            "text": full_text,
            "patient_id": pid,
            "interactive_qa_finished": True,
            "timestamp": time.time(),
        },
        "formal_prescription_allowed": False,
        "status": "ok",
    }, ensure_ascii=False))
    persist_state()


async def handle_selection_answers(ws, pid: str, data: dict):
    patient = get_patient(pid) or {}
    answers = data.get("answers") or data.get("selection_answers") or {}
    if isinstance(answers, list):
        answers = {str(i): a for i, a in enumerate(answers)}

    followup_ctx = get_followup_context(patient, data)
    snapshot = patient.get("last_initial_visit")
    if followup_ctx:
        if not snapshot:
            clear_followup_mode(patients_db, pid)
            msg = "未找到该患者的初诊快照，请先完成一次完整初诊后再复诊。"
            add_log(pid, "assistant", {"text": msg})
            await ws.send(json.dumps({
                "answer": {"text": msg, "patient_id": pid, "interactive_qa_finished": True},
                "status": "ok",
            }, ensure_ascii=False))
            return
        await handle_followup_visit(ws, pid, patient, answers, followup_ctx, snapshot)
        return

    patient_name = patient.get('name', '未知患者')
    patient_age = patient.get('age', '')
    patient_gender = patient.get('gender', '')
    detail = patient.get('detail', {})
    symptom_text = detail.get('symptom', '')

    answer_lines = []
    if isinstance(answers, dict):
        for q_key, q_val in answers.items():
            if isinstance(q_val, dict):
                q_text = q_val.get('question', q_key)
                a_text = q_val.get('answer', q_val)
            else:
                q_text = q_key
                a_text = q_val
            if isinstance(a_text, list):
                a_text = "、".join(a_text)
            if isinstance(q_text, str) and isinstance(a_text, str):
                answer_lines.append('•' + ' ' + q_text + '：' + a_text)

    summary_lines = []
    summary_lines.append("患者基本信息: " + patient_name + ", " + patient_gender + ", " + patient_age)
    summary_lines.append("主诉: " + symptom_text)
    if answer_lines:
        summary_lines.append("追问结果:")
        summary_lines.extend(answer_lines)
    summary = "\n".join(summary_lines)

    # ── 分科过滤工具：根据患者年龄和性别过滤候选诊断 ──
    def _filter_by_department(disease_list, age_str, gender_str):
        """
        根据患者年龄和性别，从候选诊断列表中过滤出分科匹配的疾病。
        过滤规则：
        - 儿科（儿童/小儿/婴儿/新生儿/幼儿关键词）：年龄≤14岁
        - 妇科（子宫/卵巢/月经/带下/妊娠/乳腺等）：女性
        - 男科（前列腺/精索/阳痿/早泄/阴茎/睾丸等）：男性
        - 通用：其他所有疾病
        """
        age = 0
        try:
            age = int(''.join(c for c in str(age_str) if c.isdigit() or c == '.'))
        except:
            age = 0
        is_female = gender_str == "女"
        
        # 科室关键词定义
        _pediatric = ['儿童', '小儿', '婴儿', '新生儿', '幼儿']
        _gynecology = ['子宫', '卵巢', '输卵管', '月经', '带下', '阴道', '盆腔', 
                       '乳腺', '乳房', '更年', '绝经', '宫颈', '崩漏', '经期', 
                       '经行', '痛经', '闭经', '白带', '妊娠', '产后', '胎',
                       '乳', '阴痒', '外阴', '处女膜', '宫颈']
        _andrology = ['前列腺', '精索', '精囊', '精液', '阳痿', '早泄', 
                      '阴茎', '睾丸', '附睾', '阴囊', '输精管', '前列腺']
        
        # 先判断患者的科室归属
        patient_departments = set()
        if age <= 14:
            patient_departments.add('儿科')
        if is_female:
            patient_departments.add('妇科')
        else:
            patient_departments.add('男科')
        patient_departments.add('通用')  # 通用始终可召回
        
        filtered = []
        for d in disease_list:
            dep = '通用'
            if any(kw in d for kw in _pediatric):
                dep = '儿科'
            elif any(kw in d for kw in _gynecology):
                dep = '妇科'
            elif any(kw in d for kw in _andrology):
                dep = '男科'
            # 只在患者所属科室或通用中保留
            if dep in patient_departments or dep == '通用':
                filtered.append(d)
            else:
                print(f"  [DEPT_FILTER] 排除: '{d}'（科室:{dep}，患者科室:{patient_departments}）")
        return filtered

    disease = ""
    m1_out = "暂无结果"
    m1_candidates = []
    print("[HSA_M1_START]", {"symptom": symptom_text[:60] if symptom_text else ""})
    try:
        # 从追问答案中提取更多症状
        symptoms_list_raw = [symptom_text] if symptom_text else []
        signs_list_raw = []
        answer_texts = []
        for q_key, q_val in answers.items():
            if isinstance(q_val, dict):
                a_text = q_val.get("answer", "")
            else:
                a_text = q_val
            if isinstance(a_text, str) and a_text.strip():
                a_trim = a_text.strip()
                answer_texts.append(a_trim)
                if a_trim not in ("无", "无过敏史", "无其他补充", "未做检查"):
                    # 检查是否包含舌脉信息
                    if "舌" in a_trim or "苔" in a_trim or "脉" in a_trim:
                        signs_list_raw.append(a_trim)
                    else:
                        for s in a_trim.replace("、", ",").split(","):
                            s = s.strip()
                            if s and s not in symptoms_list_raw:
                                symptoms_list_raw.append(s)
        labs_list_raw, imaging_list_raw = extract_labs_imaging_from_texts(answer_texts)
        negative_list_raw = extract_negative_findings_from_texts(answer_texts)
        # 尝试从主诉中提取患者提到的疾病名
        mentioned = ""
        mention_keywords = ["诊断", "确诊", "考虑", "考虑为", "患"]
        for kw in mention_keywords:
            if kw in symptom_text:
                idx = symptom_text.find(kw)
                rest = symptom_text[idx+len(kw):].strip()
                # 去掉冒号等前缀
                rest = rest.lstrip("：:：:")
                # 取第一个逗号/句号/空格/换行前的内容
                import re
                match = re.match(r'^[^，,。.\s]+', rest)
                if match:
                    mentioned = match.group().strip()
                    break
        # 如果上述关键词未提取到，尝试"手术"关键词——病名在"手术"之前
        if not mentioned and "手术" in symptom_text:
            idx = symptom_text.find("手术")
            # 取"手术"前最多8个字
            before = symptom_text[max(0, idx-8):idx].strip()
            # 从后往前取最后一个"了""的"等之后的内容
            for sep in ["了", "的", "做", "行", "接受"]:
                if sep in before:
                    before = before[before.rfind(sep)+len(sep):].strip()
                    break
            # 去掉标点
            before = before.rstrip("，, 、")
            if before and len(before) >= 2:
                mentioned = before
        if not mentioned:
            mentioned = extract_disease_from_symptom(symptom_text)
        
        m1_input = {
            "patient_mentioned_disease": mentioned,
            "chief_complaint": symptom_text,
            "symptoms": symptoms_list_raw,
            "signs": signs_list_raw,
            "labs": labs_list_raw,
            "imaging": imaging_list_raw,
            "negative_findings": negative_list_raw,
            "duration": "半年" if "半年" in symptom_text or "半年" in str(answers) else "半月",
            "onset": "",
        }
        patient_snapshot = patient.get("m1_evidence_followup_state") or patient.get("m1_bayesian_state") or {}
        if isinstance(patient_snapshot, dict) and patient_snapshot:
            m1_input["evidence_followup_state"] = patient_snapshot
            m1_input["question_round"] = patient_snapshot.get("question_round", 1)
        m1_input["require_differential_followup"] = True
        # 将追问答案转为 _followup_answers，传给 M1 做二次验证
        _followup_answers = {}
        for q_key, q_val in answers.items():
            if isinstance(q_val, dict):
                _q = q_val.get("question", q_key)
                _a = q_val.get("answer", "")
            else:
                _q = q_key
                _a = q_val
            if isinstance(_a, str) and _a.strip():
                _followup_answers[_q] = _a
        m1_r = m1.diagnose(m1_input, _followup_answers=_followup_answers if _followup_answers else None)
        if isinstance(m1_r, dict) and m1_r.get("evidence_followup_state"):
            patient["m1_evidence_followup_state"] = m1_r.get("evidence_followup_state")
            patients_db[pid] = patient
        if isinstance(m1_r, dict):
            calib = m1_r.get("diagnosis_calibration", {})
            diag_list = calib.get("calibrated_diagnosis", [])
            if diag_list:
                disease = diag_list[0]
                m1_out = "、".join(diag_list)
            else:
                # NEED_MORE_INFO 状态 — 提取候选诊断
                candidates = m1_r.get("current_top_candidates", [])
                if candidates:
                    disease = candidates[0]
                    m1_candidates = candidates
                    m1_out = "候选诊断: " + "、".join(candidates)
                    if m1_r.get("cannot_decide_because"):
                        m1_out += "\n待确认: " + m1_r["cannot_decide_because"]
                else:
                    m1_out = calib.get("original_input", "暂无结果")
        print("[HSA_M1_DONE]", {"disease": disease, "m1_out": m1_out[:100]})
        if not disease:
            disease = symptom_text or "待查"
    except Exception as e:
        print("  [M1_ERR] " + str(e))
        import traceback
        traceback.print_exc()
        disease = symptom_text or "待查"
        m1_out = "M1 诊断异常: " + str(e)

    # ── 分科过滤：根据患者年龄/性别排除不匹配的候选诊断 ──
    if m1_candidates and len(m1_candidates) > 0:
        _before_filter = m1_candidates.copy()
        _filtered_candidates = _filter_by_department(m1_candidates, patient_age, patient_gender)
        if len(_filtered_candidates) != len(_before_filter):
            print("[DEPT_FILTER_CANDIDATES] 过滤前:" + str(_before_filter) + " 过滤后:" + str(_filtered_candidates))
            m1_candidates = _filtered_candidates
            if _filtered_candidates:
                disease = _filtered_candidates[0]
                m1_out = "候选诊断: " + "、".join(_filtered_candidates)
    # 同时对 disease（首选诊断）做分科过滤
    if disease:
        _disease_filtered = _filter_by_department([disease], patient_age, patient_gender)
        if not _disease_filtered:
            print(f"[DEPT_FILTER_DISEASE] 首选诊断 '{disease}' 被排除，m1_candidates={m1_candidates}")
            # 回退到过滤后的候选列表
            if m1_candidates:
                disease = m1_candidates[0]
                print(f"  [DEPT_FILTER_DISEASE] 回退到: {disease}")
    
    # 疾病名映射：M1 输出 → M2 知识库标准疾病名
    # ── 症状特征增强映射：根据患者症状描述精化疾病名 ──
    _symptom_feature_map = {
        # 妇科：绿白带/腥臭味 → 滴虫阴道炎
        "绿白带": ("滴虫阴道炎", "阴道炎"),
        "腥臭": ("滴虫阴道炎", "阴道炎"),
        "腥臭味": ("滴虫阴道炎", "阴道炎"),
        "白带增多": ("滴虫阴道炎", "阴道炎"),
        # 男科
        "尿频尿急": ("非淋菌性尿道炎", "尿路感染 (Urinary Tract Infection)"),
        "尿痛": ("非淋菌性尿道炎", "尿路感染 (Urinary Tract Infection)"),
    }
    for _sf_symptom, (_sf_target, _sf_fallback) in _symptom_feature_map.items():
        if _sf_symptom in symptom_text or _sf_symptom in str(answers):
            if disease != _sf_target:
                # 仅当当前 disease 是较泛化的疾病名时才强制映射
                _sf_generic = ["阴道炎", "尿道炎", "尿路感染", "盆腔炎", "宫颈炎"]
                if disease in _sf_generic or not disease:
                    print(f"[FEATURE_MAP] symptom='{_sf_symptom}' disease={disease} -> {_sf_target}")
                    disease = _sf_target
                    break
    
    DISEASE_NAME_MAP = {
        "多发性抽动症": "抽动障碍",
        "Tourette综合征": "抽动障碍",
        "慢性荨麻疹": "荨麻疹",
        "胆碱能性荨麻疹": "荨麻疹",
        "过敏性荨麻疹": "荨麻疹",
        "皮肤瘙痒症": "皮肤瘙痒症",
        "老年性皮肤瘙痒症": "皮肤瘙痒症",
        "上气道咳嗽综合征": "急性上呼吸道感染",
        "支原体肺炎": "支原体肺炎",
        "肺炎": "肺炎 (Pneumonia)",
        "社区获得性肺炎": "肺炎 (Pneumonia)",
        "重症肺炎": "肺炎 (Pneumonia)",
        "支气管肺炎": "肺炎 (Pneumonia)",
        "社区获得性肺炎": "肺炎 (Pneumonia)",
        "重症肺炎": "肺炎 (Pneumonia)",
        "支气管肺炎": "肺炎 (Pneumonia)",
        "支气管炎": "急性支气管炎",
        "急性支气管炎": "急性支气管炎",
        "上呼吸道感染": "急性上呼吸道感染",
        "小儿抽动": "抽动障碍",
        "儿童抽动障碍": "抽动障碍",
        "儿童抽动症": "抽动障碍",
        "上呼吸道感染": "急性上呼吸道感染",
        "感冒": "急性上呼吸道感染",
        "支气管炎": "急性支气管炎",
        "过敏性鼻炎": "变应性鼻炎",
        "鼻炎": "慢性鼻炎",
        "扁桃体炎": "儿童急性扁桃体炎",
        "急性扁桃体炎": "急性化脓性扁桃体炎",
        "急性咽炎": "急性咽炎",
        "感染性发热": "感染性发热",
        "偏头痛": "头痛 (Headache)",
        "紧张型头痛": "头痛 (Headache)",
        "丛集性头痛": "头痛 (Headache)",
        "冠心病": "冠状动脉粥样硬化性心脏病 (Coronary Atherosclerotic Heart Disease)",
        "急性上呼吸道感染": "急性上呼吸道感染",
        "肺炎": "肺炎 (Pneumonia)",
        "社区获得性肺炎": "肺炎 (Pneumonia)",
        "重症肺炎": "肺炎 (Pneumonia)",
        "支气管肺炎": "肺炎 (Pneumonia)",
        "新生儿黄疸": "新生儿黄疸",
        "反复呼吸道感染": "小儿反复呼吸道感染",
        "哮喘": "喉源性咳嗽",
        "腹泻": "腹泻 (Diarrhea)",
        "消化不良": "小儿消化不良",
        # 急性胃肠炎 → 痢疾（湿热痢可匹配粘液/潜血/腹痛）
        "急性胃肠炎": "痢疾 (Dysentery)",
        "急性肠胃炎": "痢疾 (Dysentery)",
        "胃肠炎": "痢疾 (Dysentery)",
        # 妇科病名补充
        "子宫腺肌病": "子宫腺肌症",
        "子宫腺肌症": "子宫腺肌症",
        "子宫内膜异位": "子宫内膜异位症",
        "子宫肌瘤": "子宫肌瘤",
        "月经过多": "月经过多",
        "月经后期": "月经后期",
        "月经先期": "月经先期",
        "痛经": "痛经",
        # 神经/脑病类补充
        "眩晕": "眩晕 (Vertigo)",
        "不寐": "失眠",
        "失眠": "失眠",
        "头痛": "头痛 (Headache)",
        "颈椎病": "颈椎病 (Cervical Spondylosis)",
        "脑供血不足": "短暂性脑缺血发作 (Transient Ischemic Attack)",
        "后循环缺血": "短暂性脑缺血发作 (Transient Ischemic Attack)",
        "耳石症": "眩晕 (Vertigo)",
        # 消化系补充
        "食道反流": "反流性食管炎 (Reflux Esophagitis)",
        "食管反流": "反流性食管炎 (Reflux Esophagitis)",
        "食道返流": "反流性食管炎 (Reflux Esophagitis)",
        "食管返流": "反流性食管炎 (Reflux Esophagitis)",
        "反流性食管炎": "反流性食管炎 (Reflux Esophagitis)",
        "胃食管反流病": "反流性食管炎 (Reflux Esophagitis)",
        # 鼻/咽/呼吸道补充
        "上气道咳嗽综合征": "急性上呼吸道感染",
        "鼻涕倒流": "慢性鼻窦炎",
        "鼻后滴漏": "慢性鼻窦炎",
        "鼻窦炎": "慢性鼻窦炎",
        "慢性鼻窦炎": "慢性鼻窦炎",
        "急性鼻窦炎": "慢性鼻窦炎",
    }
    # 优先0：主诉病名强制映射——如果 m1 输出的诊断完全偏离主诉，强制纠正
    # 检查 symptom_text 中是否有明确的疾病主诉，并匹配 DISEASE_NAME_MAP
    _chief_complaint_disease = ""
    for _cd, _cm in DISEASE_NAME_MAP.items():
        if _cd in symptom_text:
            _chief_complaint_disease = _cd
            break
    if _chief_complaint_disease and disease != DISEASE_NAME_MAP[_chief_complaint_disease]:
        if _chief_complaint_disease not in disease and _chief_complaint_disease not in m1_out:
            _forced_disease = DISEASE_NAME_MAP[_chief_complaint_disease]
            if should_skip_chief_complaint_force(_chief_complaint_disease, disease):
                print(f"[CHIEF_COMPLAINT_SKIP_CHRONIC] 主诉病名:{_chief_complaint_disease} 为慢性病，当前disease:{disease} 为急性病，跳过强制覆盖")
            # 检查强制映射的疾病是否存在于知识库中，不存在则跳过
            elif _forced_disease in m2.kb:
                print(f"[CHIEF_COMPLAINT_FORCE] 主诉病名:{_chief_complaint_disease} -> {_forced_disease}, 当前disease:{disease}")
                disease = _forced_disease
            else:
                print(f"[CHIEF_COMPLAINT_SKIP] 主诉病名:{_chief_complaint_disease} -> {_forced_disease} 不存在于知识库中，跳过")
    
    # 优先1：如果 m1 输出的候选诊断中包含已知的西医病名，直接优先使用
    # 检查 m1_r 中的 calibrated_diagnosis 或 candidates
    # 注意：仅当当前 disease 不是明确的西医病名时才触发
    # （否则像“肺炎”被正确识别后，不应被列表中的其他候选如“偏头痛”覆盖）
    _known_western_diseases = ["颈椎病", "后循环缺血", "失眠症", "偏头痛", 
                                "子宫肌瘤", "子宫内膜异位症", "子宫腺肌症",
                                "高血压", "糖尿病", "冠心病", "脑梗死", "脑出血",
                                "帕金森病", "阿尔茨海默病", "焦虑症", "抑郁症"]
    # 判断当前 disease 是否已经是明确的西医病名
    # 包括：已知西医病名列表、带特定后缀的器质性疾病、以及完整的疾病名称
    _disease_is_clear_western = (
        disease in _known_western_diseases
        or any(kw in disease for kw in ["肺炎", "支气管", "胃炎", "肠炎", "肝炎", "肾炎"])
        or "癌" in disease
        or "瘤" in disease
        # 如果 disease 已经在知识库中有明确条目（说明是标准病名），不应被候选列表症状名覆盖
        or (hasattr(m2, 'kb') and disease in m2.kb)
    )
    if not _disease_is_clear_western:
        if hasattr(m1_r, 'get') and m1_r.get('diagnosis_calibration', {}).get('calibrated_diagnosis', []):
            for _d in m1_r['diagnosis_calibration']['calibrated_diagnosis']:
                if _d in _known_western_diseases:
                    print("[WESTERN_PRIORITY]", disease, "->", _d, "(候选中有已知西医病名)")
                    disease = _d
                    break
    # 优先2：检查患者主诉中提到的西医病名（"诊断：XXX"）
    # 注意：仅当 M1 输出为症状描述或空时，才用文本中的西医病名覆盖
    # 避免既往史中的慢性病（如"高血压"在既往史列表中）覆盖 M1 已准确识别的急性病诊断
    _m1_top_from_text = symptom_text[:20]  # 用于检查 M1 是否准确识别了主诉
    _m1_output_seems_reasonable = (
        disease in m1_out[:50]  # M1 输出的诊断在它的结果文本中出现
        and disease not in ("咳嗽", "发热", "腹泻", "腹痛", "头痛")
    )
    if disease not in _known_western_diseases and not _m1_output_seems_reasonable:
        for _wd in _known_western_diseases:
            if _wd in symptom_text:
                print("[SYMPTOM_WESTERN]", disease, "->", _wd, "(主诉中有西医病名)")
                disease = _wd
                break
    # 中医症状名→西医病名映射（当 LLM 输出中医症状名时，优先映射为西医病名）
    # 规则：如果患者主诉中有明确的西医诊断关键字，优先使用
    _western_disease_priority = {
        "痛经": "子宫腺肌症",      # 有腺肌瘤病史
        "眩晕": "眩晕 (Vertigo)",   # 眩晕/耳石等先桥接到 M2 已有眩晕入口
        "头痛": "头痛 (Headache)",  # 参考颈椎病/紧张型头痛
        "颈痛": "颈椎病",          # 颈椎病
        "腰痛": "腰椎间盘突出症",  # 腰痛
        "咳嗽": "急性支气管炎",    # 咳嗽
        "水肿": "慢性肾脏病",      # 水肿
        "胃痛": "慢性胃炎",        # 胃痛
        "腹泻": "肠易激综合征",    # 腹泻
        "便秘": "功能性便秘",      # 便秘
        "失眠": "失眠",            # 失眠
        "心悸": "心律失常",        # 心悸
        "胸痹": "冠状动脉粥样硬化性心脏病", # 胸痹
        "喘证": "慢性阻塞性肺疾病", # 喘证
        "淋证": "尿路感染",        # 淋证
        "痹证": "骨关节炎",        # 痹证
        "不寐": "失眠",            # 不寐
    }
    # 检查患者症状文本中是否有更明确的西医诊断依据
    _symptom_txt_lower = symptom_text.lower()
    for _tcm_name, _western_name in _western_disease_priority.items():
        if disease == _tcm_name:
            # 如果有相关西医诊断依据，优先用西医病名
            _western_hints = {
                "眩晕": ["脑供血", "供血不足", "颈A", "椎A", "颈椎", "耳石", "后循环"],
                "痛经": ["腺肌", "肌瘤", "子宫", "B超", "彩超", "超声"],
                "头痛": ["偏头痛", "紧张", "颈椎"],
                "失眠": ["失眠"],
                "不寐": ["失眠"],
            }
            _hints = _western_hints.get(_tcm_name, ["腺肌", "肌瘤", "子宫", "B超"])
            if any(h in _symptom_txt_lower for h in _hints):
                print("[TCM_TO_WESTERN]", disease, "->", _western_name, "(有西医依据)")
                disease = _western_name
                break
    mapped_disease = DISEASE_NAME_MAP.get(disease, "")
    if mapped_disease:
        print("[DISEASE_MAP]", disease, "->", mapped_disease)
        disease = mapped_disease

    syndrome_name = ""
    formula_name = ""
    herbs = []
    dosage_str = ""
    _symptom_additions = []
    _tongue_diagnosis_note = ""
    _disease_for_m4 = ""
    _m4_symptom_list = []
    m2_r = None
    m3_r = None
    try:
        # M2 输入：使用更丰富的症状列表
        m2_symptoms = [symptom_text] if symptom_text else []
        for q_key, q_val in answers.items():
            if isinstance(q_val, dict):
                a_text = q_val.get("answer", "")
            else:
                a_text = q_val
            if isinstance(a_text, str) and a_text.strip() and a_text not in ("无", "无过敏史", "无其他补充", "未做检查"):
                # 拆解逗号分隔的多个症状
                for s in a_text.replace("、", ",").split(","):
                    s = s.strip()
                    if s and s not in m2_symptoms:
                        m2_symptoms.append(s)
        # M2 知识库模糊匹配：disease 可能不是 kb 中的精确 key
        # 遍历所有 kb keys 做子串匹配
        _m2_kb_disease = disease
        if not hasattr(m2, 'kb'):
            _tmp_syndromes = {}
        else:
            _tmp_syndromes = m2.kb.get(disease)
        if not _tmp_syndromes:
            # 尝试子串匹配（加入分科过滤）
            _kb_all = list(m2.kb.keys())
            # 先按分科过滤排序：非儿科/非男科（与患者不匹配的科室）放在最后
            _filtered_kb_keys = _filter_by_department(_kb_all, patient_age, patient_gender)
            for _kb_key in _filtered_kb_keys:
                if disease in _kb_key or _kb_key in disease:
                    _tmp_syndromes = m2.kb.get(_kb_key)
                    if _tmp_syndromes:
                        print("[M2_KB_MATCH]", disease, "->", _kb_key)
                        _m2_kb_disease = _kb_key
                        break
            # 如果过滤后没找到，才从全部疾病中搜索（避免漏掉真匹配）
            if not _tmp_syndromes:
                for _kb_key in _kb_all:
                    if disease in _kb_key or _kb_key in disease:
                        _tmp_syndromes = m2.kb.get(_kb_key)
                        if _tmp_syndromes:
                            print("[M2_KB_MATCH](fallback)", disease, "->", _kb_key)
                            _m2_kb_disease = _kb_key
                            break
        m2_kwargs = build_m2_process_kwargs(
            primary_disease=_m2_kb_disease,
            m1_result=m1_r if isinstance(m1_r, dict) else None,
            symptoms=m2_symptoms,
            signs=signs_list_raw,
            labs=labs_list_raw,
            imaging=imaging_list_raw,
            negative_findings=negative_list_raw,
            age=patient_age,
            weight="",
        )
        m2_r = m2.process(**m2_kwargs)
        # 如果 M2 找不到该疾病名，尝试兜底映射
        m2_fallback = {
            "咳嗽": "支原体肺炎",
            "咳嗽病": "急性支气管炎",
            "发热": "急性上呼吸道感染",
            "头痛": "急性上呼吸道感染",
            "急性扁桃体炎": "急性化脓性扁桃体炎",
            "急性咽炎": "急性咽炎",
            "感染性发热": "感染性发热",
            "偏头痛": "头痛 (Headache)",
            "紧张型头痛": "头痛 (Headache)",
            "丛集性头痛": "头痛 (Headache)",
            "肾结石": "尿石症",
            "尿石症": "尿石症",
            "腹痛": "小儿消化不良",
            "呕吐": "小儿消化不良",
            "腹泻病": "腹泻 (Diarrhea)",
        "尿频": "非淋菌性尿道炎",
        "尿道炎": "非淋菌性尿道炎",
        "慢性尿道炎": "非淋菌性尿道炎",
        "尿路感染": "尿路感染 (Urinary Tract Infection)",
        "热淋": "非淋菌性尿道炎",
        "淋证": "非淋菌性尿道炎",
        # 泌尿道系补充
        "尿频": "非淋菌性尿道炎",
        "尿道炎": "非淋菌性尿道炎",
        "慢性尿道炎": "非淋菌性尿道炎",
        "尿路感染": "尿路感染 (Urinary Tract Infection)",
        "淋证": "非淋菌性尿道炎",
        "热淋": "非淋菌性尿道炎",
            # 妇科病名补充
            "子宫腺肌病": "子宫腺肌症",
            "子宫腺肌症": "子宫腺肌症",
            "子宫内膜异位症": "子宫内膜异位症",
            "子宫肌瘤": "子宫肌瘤",
            "月经不调": "月经过多",
            "月经过多": "月经过多",
            "月经后期": "月经后期",
            "月经先期": "月经先期",
            # 神经/脑病类补充
            "眩晕": "眩晕 (Vertigo)",
            "不寐": "失眠",
            "失眠": "失眠",
            "头痛": "头痛 (Headache)",
            "颈椎病": "颈椎病 (Cervical Spondylosis)",
            "后循环缺血": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "脑供血不足": "短暂性脑缺血发作 (Transient Ischemic Attack)",
            "食道反流": "反流性食管炎 (Reflux Esophagitis)",
            "食管反流": "反流性食管炎 (Reflux Esophagitis)",
            "食道返流": "反流性食管炎 (Reflux Esophagitis)",
            "食管返流": "反流性食管炎 (Reflux Esophagitis)",
            "上气道咳嗽综合征": "急性上呼吸道感染",
            "鼻涕倒流": "慢性鼻窦炎",
            "鼻后滴漏": "慢性鼻窦炎",
            "鼻窦炎": "慢性鼻窦炎",
            "慢性鼻窦炎": "慢性鼻窦炎",
            "急性鼻窦炎": "慢性鼻窦炎",
        }
        # M2 fallback 也使用分科过滤——避免召回不匹配科室的疾病
        _filtered_m2_fallback = {}
        for _fb_src, _fb_tgt in m2_fallback.items():
            _fb_check = _filter_by_department([_fb_tgt], patient_age, patient_gender)
            if _fb_check:
                _filtered_m2_fallback[_fb_src] = _fb_tgt
            else:
                print(f"  [DEPT_FILTER_FALLBACK] 排除 fallback: {_fb_src} -> {_fb_tgt}")
        m2_fallback = _filtered_m2_fallback
        
        # 如果映射后仍找不到，检查 symptom_text 中的更具体疾病名
        if m2_r and m2_r.get("error"):
            # 先从主诉中提取更具体的疾病信息
            symptom_keywords = {
                "支原体肺炎": "支原体肺炎",
                "肺炎": "肺炎 (Pneumonia)",
                "支气管炎": "急性支气管炎",
            }
            found_disease = ""
            for kw, dn in symptom_keywords.items():
                if kw in symptom_text:
                    found_disease = dn
                    break
            if not found_disease:
                found_disease = m2_fallback.get(disease, "")
            if found_disease:
                print("[M2_FALLBACK]", disease, "->", found_disease)
                disease = found_disease
                m2_r = m2.process(**{**m2_kwargs, "primary_disease": found_disease})
        
        if m2_r and not m2_r.get("error"):
            display = extract_m2_result_display(m2_r)
            syndrome_name = display["syndrome_name"]
            formula_name = display["formula_name"]
            herbs = display["herbs"]

        _legacy_enrichment = apply_legacy_bridge_enrichment(
            enabled=legacy_bridge_prescription_path_enabled(),
            m2=m2,
            m2_r=m2_r,
            m2_kb_disease=_m2_kb_disease if "_m2_kb_disease" in dir() else disease,
            disease=disease,
            syndrome_name=syndrome_name,
            formula_name=formula_name,
            herbs=herbs,
            dosage_str=dosage_str,
            symptom_text=symptom_text,
            answers=answers,
            m2_symptoms=m2_symptoms if "m2_symptoms" in dir() else ([symptom_text] if symptom_text else []),
        )
        syndrome_name = _legacy_enrichment.syndrome_name
        formula_name = _legacy_enrichment.formula_name
        herbs = _legacy_enrichment.herbs
        dosage_str = _legacy_enrichment.dosage_str
        _tongue_diagnosis_note = _legacy_enrichment.tongue_diagnosis_note
        _symptom_additions = _legacy_enrichment.symptom_additions
        _disease_for_m4 = _legacy_enrichment.disease_for_m4
        _m4_symptom_list = _legacy_enrichment.m4_symptom_list
        print("[HSA_M2_DONE]", {"syndrome": syndrome_name, "formula": formula_name, "herbs_count": len(herbs)})

    except Exception as e:
        print("  [M2_ERR] " + str(e))

    warnings = []
    try:
        from services.m1_m2_m3_bridge import build_m3_patient_context
        if m2_r and (m2_r.get("draft_prescription") or m2_r.get("source_closure_path")):
            m3_r = m3.review_m2_handoff(
                m2_r,
                patient=build_m3_patient_context(None, m2_r, {
                    k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]
                } | {"symptom_text": symptom_text}),
                diagnosis=disease,
            )
        elif herbs:
            m3_r = m3.review(herbs, {k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]} | {"symptom_text": symptom_text},
                             formula_name=formula_name, dosage_str=dosage_str, diagnosis=disease)
        else:
            m3_r = None
        if m3_r and m3_r.get("warnings"):
            warnings = m3_r["warnings"]
    except Exception as e:
        print("  [M3_ERR] " + str(e))

    # --- M4 初诊：仅病程窗口建议 + 保存初诊快照（不做伪复诊路由）---
    followup_advice = ""
    try:
        _m4_disease_name = _disease_for_m4 if '_disease_for_m4' in dir() and _disease_for_m4 else (disease or symptom_text or "待查")
        _m4_initial_symptoms = _m4_symptom_list if '_m4_symptom_list' in dir() and _m4_symptom_list else ([symptom_text] if symptom_text else [])
        _m4_card = load_m1_card_bridge(_m4_disease_name)
        _fu_days, _fu_note = suggest_initial_followup_days(_m4_disease_name, _m4_card)
        followup_advice = f"建议{_fu_days}天后复诊"
        if _fu_note:
            followup_advice += _fu_note

        save_initial_visit_snapshot(
            patients_db,
            pid,
            disease=_m4_disease_name,
            initial_symptoms=_m4_initial_symptoms,
            m1_r=m1_r if isinstance(m1_r, dict) else {"primary_diagnosis": _m4_disease_name},
            m2_r=m2_r if isinstance(m2_r, dict) else None,
            m3_r=m3_r if isinstance(m3_r, dict) else None,
            syndrome_name=syndrome_name,
            formula_name=formula_name,
            patient_age=str(patient_age or ""),
        )
        print("[M4_INITIAL_SNAPSHOT_SAVED]", {"pid": pid, "disease": _m4_disease_name, "symptoms": len(_m4_initial_symptoms)})
        persist_state()
    except Exception as e:
        print("  [M4_ERR] " + str(e))
    print("[HSA_M4_DONE]", {"followup_advice": followup_advice[:80] if followup_advice else ""})

    # ══════════════════════════════════════════════════════════════
    #  模式选择：默认模式 vs 开发验证模式
    #  ─ 默认模式（ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH != true）:
    #     不输出 prescription_text / final_formula / dosage / 用法 / 疗程
    #     只输出 candidate_summary
    #     formal_prescription_allowed = false
    #  ─ 开发验证模式（ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH == true）:
    #     允许完整方剂/剂量输出
    #     输出标记 dev_legacy_prescription_output=true, not_for_clinical_use=true
    # ══════════════════════════════════════════════════════════════
    _legacy_dev_mode = os.getenv("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", "false").lower() == "true"

    final_status = compute_final_status(m2_r, m3_r)

    _rx_preview = build_candidate_prescription_preview(
        m2_r if isinstance(m2_r, dict) else None,
        primary_disease=_m2_kb_disease if "_m2_kb_disease" in dir() else disease,
        symptoms=m2_symptoms if "m2_symptoms" in dir() else ([symptom_text] if symptom_text else []),
        symptom_text=symptom_text,
        signs=signs_list_raw if "signs_list_raw" in dir() else [],
        stage=str((m1_r or {}).get("stage") or "") if isinstance(m1_r, dict) else "",
    )
    _m1_extra = ""
    if isinstance(m1_r, dict):
        _m1_extra = str(m1_r.get("cannot_decide_because") or "")
        if m1_candidates and not disease:
            _m1_extra = _m1_extra or "、".join(m1_candidates)
    candidate_summary = build_compact_initial_diagnosis_message(
        disease=disease if disease else "待查",
        syndrome_name=syndrome_name,
        formula_name=formula_name,
        herb_items=_rx_preview.get("herb_items"),
        final_status=final_status,
        m3_result=m3_r if isinstance(m3_r, dict) else None,
        followup_advice=followup_advice,
        m1_extra=_m1_extra,
    )
    try:
        record_initial_visit_to_patient(
            pid,
            disease=_m4_disease_name if "_m4_disease_name" in dir() else (disease or ""),
            syndrome_name=syndrome_name,
            formula_name=formula_name,
            symptom_text=symptom_text,
            followup_days=_parse_followup_days(followup_advice),
            herb_items=_rx_preview.get("herb_items"),
            final_status=final_status,
        )
        persist_state()
    except Exception as e:
        print("  [PATIENT_VISIT_RECORD_ERR]", str(e))
    if '_tongue_diagnosis_note' in dir() or '_tongue_diagnosis_note' in locals():
        _tdn = locals().get('_tongue_diagnosis_note', '')
        if _tdn:
            candidate_summary += "\n舌脉提示：" + _tdn

    if not _legacy_dev_mode:
        add_log(pid, "assistant", {"text": candidate_summary})
        result = {
            "type": "diagnosis_result",
            "patient_id": pid,
            "diagnosis": {
                "summary": summary,
                "possible_diagnosis": disease if disease else "待查",
                "syndrome": syndrome_name or "待辨证",
                "recommendation": candidate_summary,
                "candidate_summary": {
                    "diagnosis": disease if disease else "待查",
                    "syndrome": syndrome_name or "待辨证",
                    "formula_name": formula_name or "",
                    "herbs": _rx_preview.get("herbs") or [],
                    "herb_items": _rx_preview.get("herb_items") or [],
                    "herb_count": _rx_preview.get("herb_count") or len(herbs) if herbs else 0,
                },
            },
            "candidate_only": True,
            "legacy_prescription_path_blocked": True,
            "must_enter_m3": True,
            "trace_closure_required": True,
            "formal_prescription_allowed": False,
            "blocked_reason": ["legacy_bridge_prescription_path_blocked"],
            "final_status": final_status,
            "followup_advice": followup_advice,
            "m3_review": {
                "decision": (m3_r or {}).get("review_decision") or (m3_r or {}).get("m3_status") or "",
                "warnings": (m3_r or {}).get("warnings") or [],
            } if isinstance(m3_r, dict) else None,
            "status": "ok",
        }
        print("[WS_SEND_DIAGNOSIS_RESULT_DEFAULT]", result)
        await ws.send(json.dumps(result, ensure_ascii=False))
        return

    # ── 开发验证模式：允许完整方剂/剂量输出，但标记为 dev only ──
    full_rx = build_legacy_dev_prescription_recommendation(
        candidate_summary=candidate_summary,
        disease=disease,
        formula_name=formula_name,
        herbs=herbs,
        dosage_str=dosage_str,
        symptom_additions=_symptom_additions,
        warnings=warnings,
        m3_r=m3_r,
        followup_advice=followup_advice,
    )
    add_log(pid, "assistant", {"text": full_rx})

    _display_disease = disease if disease else m1_out.split("\n")[0][:80] if m1_out else "待查"
    result = {
        "type": "diagnosis_result",
        "patient_id": pid,
        "diagnosis": {
            "summary": summary,
            "possible_diagnosis": _display_disease,
            "syndrome": syndrome_name or "待辨证",
            "recommendation": full_rx,
        },
        "dev_legacy_prescription_output": True,
        "not_for_clinical_use": True,
        "formal_prescription_allowed": False,
        "candidate_only": True,
        "final_status": final_status,
        "followup_advice": followup_advice,
        "status": "ok",
    }
    print("[WS_SEND_DIAGNOSIS_RESULT_GUARDED]", {"legacy_dev_mode": True, "patient_id": pid})
    print("[WS_SEND_DIAGNOSIS_RESULT_DEV]", result)
    await ws.send(json.dumps(result, ensure_ascii=False))


async def gen_treatment(ws, pid: str, md: dict):
    patient = get_patient(pid) or {}
    text = f"""【{patient.get('name','患者')} · 调理笺】

━━━━━━━━━━━━━━━━━━━━

一、诊断结论
已在问诊流程中完成辨证分析。

二、处方方案
待完成交互式问诊后生成完整处方。

三、煎服方法
• 每日1剂，水煎300ml
• 分早晚温服，饭后1小时服

四、生活调摄
• 饮食清淡，忌辛辣油腻
• 注意休息，避免熬夜
• 保持心情舒畅

五、复诊建议
• 建议7-14天后复诊
• 如有不适及时就诊

━━━━━━━━━━━━━━━━━━━━
守一中医AI辅助诊断系统
"""
    add_log(pid, "assistant", {"text": text})
    await ws.send(json.dumps({"answer":{"text":text,"patient_id":pid,"timestamp":time.time()},"status":"ok"}, ensure_ascii=False))


async def main():
    print("=" * 60)
    print("  守一 CDSS 联调桥接服务")
    print("=" * 60)
    init()

    # 启动 HTTP
    t = threading.Thread(target=run_http, daemon=True)
    t.start()
    print(f"\n  🌐 HTTP API  → http://localhost:{HTTP_PORT}")
    print(f"  🌐 WebSocket → ws://localhost:{WS_PORT}")
    print(f"\n  打开 Clinical Chat.html 即可连接。\n")

    # 启动 WS
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  已停止\n")
    except Exception as e:
        print(f"\n  启动失败: {e}")
        import traceback
        traceback.print_exc()
