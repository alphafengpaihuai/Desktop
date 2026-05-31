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
import sys
import time
import uuid
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['DEEPSEEK_API_KEY'] = 'sk-09562b1e562d480dacb33a1fe1fd8642'

from m1_engine import M1DiagnosisEngine
from m2_engine import M2SyndromeSelector
from m3_engine import M3ClinicalReviewEngine
from m4_engine import M4RoutingEngine
from force_link_modules import full_link

import websockets
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

WS_HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 6005

patients_db: dict[str, dict] = {}
chatlog_db: dict[str, list[dict]] = {}
doctor_patients: dict[str, list[str]] = {}

m1 = None
m2 = None
m3 = None
m4 = None


def init():
    global m1, m2, m3, m4
    print("  [引擎] M1 诊断...")
    m1 = M1DiagnosisEngine()
    print("  [引擎] M2 辨证选方...")
    m2 = M2SyndromeSelector()
    print("  [引擎] M3 药学审核...")
    m3 = M3ClinicalReviewEngine()
    print("  [引擎] M4 复诊路由...")
    m4 = M4RoutingEngine()
    print("  [引擎] 全部就绪 ✓")


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

            md = data.get("message_data", {})
            pid = md.get("patient_id", "")
            text = md.get("text", "")

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
                add_log(pid, "user", {"text": text})
                await chat(ws, pid, text, md)
    except Exception as e:
        print(f"  [WS] 断开 {cid}: {e}")


async def chat(ws, pid: str, text: str, md: dict):
    patient = get_patient(pid) or {}
    await ws.send(json.dumps({"answer":{"text":"正在分析..."},"status":"processing"}, ensure_ascii=False))
    await asyncio.sleep(0.3)

    try:
        symptoms = [s.strip() for s in text.replace("，",",").split(",") if s.strip()]
        chief = symptoms[0] if symptoms else text

        m1_input = {
            "patient_mentioned_disease": "",
            "chief_complaint": chief,
            "symptoms": symptoms,
            "signs": [], "labs": [], "imaging": [],
            "negative_findings": [], "duration": "", "onset": "",
        }
        m1_r = m1.diagnose(m1_input)

        disease = ""
        m1_out = "暂无结果"
        if isinstance(m1_r, dict):
            calib = m1_r.get("diagnosis_calibration", {})
            diag_list = calib.get("calibrated_diagnosis", [])
            if diag_list:
                disease = diag_list[0]
            m1_out = calib.get("original_input", "") + " -> " + "、".join(diag_list) if diag_list else "暂无结果"
        if not disease:
            disease = chief

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

        try:
            m2_r = m2.process(
                primary_disease=disease,
                symptoms=symptoms,
                age=patient.get("age",""),
                weight=""
            )
            if m2_r and not m2_r.get("error"):
                sd = m2_r.get("syndrome_differentiation",{})
                ss = sd.get("selected_syndrome",{})
                formula = m2_r.get("formula",{})
                if ss.get("name") and formula.get("name"):
                    reply += f"\n\n【辨证处方】\n证型: {ss['name']}\n方剂: {formula['name']}"
                    herbs = formula.get("herbs",[])
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
    patient = get_patient(pid) or {}
    questions = [
        {"q":"您的主要不适是什么？持续了多久？","sel":["3天以内","1周","2周","1个月","3个月以上"]},
        {"q":"除了主要不适，还有哪些伴随症状？","sel":["发热","咳嗽","头痛","鼻塞流涕","咽喉痛","胸闷","其他"]},
        {"q":"您有没有以下基础疾病？","sel":["高血压","糖尿病","冠心病","胃病","肝病","肾病","无"]},
        {"q":"近期做过哪些检查？有异常结果吗？","sel":["血常规","胸片/CT","心电图","B超","未做检查"]},
        {"q":"您对什么药物过敏吗？","sel":["青霉素类","磺胺类","头孢类","中药","无过敏史"]},
        {"q":"请补充其他需要告知医生的情况","sel":["无其他补充","我补充一些细节"]},
    ]
    welcome = f"您好！已启动智能问诊。\n\n患者: {patient.get('name','新患者')} | {patient.get('gender','')} {patient.get('age','')}岁\n\n请回答以下问题："
    add_log(pid, "assistant", {"text": welcome})
    await ws.send(json.dumps({"answer":{"text":welcome,"patient_id":pid,"timestamp":time.time()},"status":"ok"}, ensure_ascii=False))
    await asyncio.sleep(0.5)
    for q in questions:
        sq = {"q": q["q"], "sel": q["sel"]}
        add_log(pid, "assistant", {"text": q["q"], "selection_q": sq})
        await ws.send(json.dumps({"answer":{"selection_q":sq,"patient_id":pid,"timestamp":time.time(),"type":"selection_q"},"status":"ok"}, ensure_ascii=False))
        await asyncio.sleep(0.8)
    final = "以上6个问题已全部发送，请逐一回答。回答完成后将为您生成辨证分析和处方建议。"
    add_log(pid, "assistant", {"text": final})
    await ws.send(json.dumps({"answer":{"text":final,"patient_id":pid,"timestamp":time.time()},"status":"ok"}, ensure_ascii=False))


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
