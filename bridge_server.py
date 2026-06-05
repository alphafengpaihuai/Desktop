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
# DeepSeek API Key — 优先从环境变量读取，若未设置则使用默认值
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY') or 'sk-09562b1e562d480dacb33a1fe1fd8642'
os.environ['DEEPSEEK_API_KEY'] = DEEPSEEK_KEY

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
    """Avoid letting chronic history override an acute infectious/fever M1 diagnosis."""
    is_chronic_chief = any(kw in chief_complaint_disease for kw in CHIEF_FORCE_CHRONIC_DISEASE_KEYWORDS)
    is_acute_current = any(kw in current_disease for kw in CHIEF_FORCE_ACUTE_CURRENT_KEYWORDS)
    return bool(is_chronic_chief and is_acute_current)


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
        # 如果 symptom_text 开头直接是常见疾病名模式
        common_diseases = ["肾结石", "过敏性寻麻疹", "过敏性荨麻疹", "寻麻疹", "荨麻疹", "湿疹", "皮炎", 
                          "抽动症", "抽动障碍", "感冒", "咳嗽", "哮喘", "鼻炎", "肺炎"]
        for cd in common_diseases:
            if cd in symptom_text:
                mentioned = cd
                break
        
        m1_input = {
            "patient_mentioned_disease": mentioned,
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
    print("[WS_START_QA_RECEIVED]", {"pid": pid, "has_md": bool(md), "patient_info": md.get("patient_info")})
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
    # === 根据主诉从 M1 诊断卡匹配疾病并生成针对性问题 ===
    symptom_text_q = patient.get('detail', {}).get('symptom', '')
    # 提取可能的疾病关键词
    _found_disease = ""
    import re as _re
    for _kw in ["诊断", "确诊", "患"]:
        if _kw in symptom_text_q:
            _idx = symptom_text_q.find(_kw)
            _rest = symptom_text_q[_idx+len(_kw):].strip().lstrip("，, ：:：:")
            _m = _re.match(r'^[^，,。.\s]+', _rest)
            if _m:
                _found_disease = _m.group().strip()
                break
    if not _found_disease:
        for _cd in ["腰椎间盘突出", "骨质增生", "肾结石", "寻麻疹", "荨麻疹", "感冒", "咳嗽", "头痛", "失眠", "腰痛", "胃痛", "腹泻", "便秘"]:
            if _cd in symptom_text_q:
                _found_disease = _cd
                break
    # 加载 M1 诊断卡匹配标准病名
    _m1_card = None
    try:
        with open('data/m1_diagnostic_cards.json', 'r', encoding='utf-8') as _f:
            _all_cards = json.load(_f).get('diagnostic_cards', [])
        for _c in _all_cards:
            if _c.get('diseaseName_cn', '') == _found_disease:
                _m1_card = _c
                break
        if not _m1_card:
            for _c in _all_cards:
                if _found_disease and _found_disease in _c.get('diseaseName_cn', ''):
                    _m1_card = _c
                    _found_disease = _c.get('diseaseName_cn', '')
                    break
        if not _m1_card:
            try:
                with open('data/m1_disease_alias_map.json', 'r', encoding='utf-8') as _af:
                    _alias = json.load(_af)
                if _found_disease in _alias:
                    _canonical = _alias[_found_disease].get('canonical_disease_name', '')
                    for _c in _all_cards:
                        if _c.get('disease_name', '') == _canonical or _c.get('diseaseName_cn', '') == _canonical:
                            _m1_card = _c
                            _found_disease = _c.get('diseaseName_cn', '')
                            break
            except Exception:
                pass
    except Exception as e:
        print("[START_QA_M1_CARD_LOAD_ERROR]", str(e)[:100])
    # 从诊断卡的典型症状生成问题
    if _m1_card:
        _symptoms = _m1_card.get('typical_symptoms', [])
        _criteria = _m1_card.get('diagnostic_criteria', [])
        _q_bank = []
        if any(s in _symptoms for s in ['放射痛','放射']):
            _q_bank.append({"question":"疼痛是否向其他部位放射？","options":["无放射","放射到臀部","放射到下肢","放射到足部"]})
        if any(s in _symptoms for s in ['麻木','麻木无力']):
            _q_bank.append({"question":"有无肢体麻木或无力感？","options":["无","轻度麻木","明显麻木","行走无力"]})
        if any(s in _symptoms for s in ['发热','红肿','热']):
            _q_bank.append({"question":"有无发热或局部红肿热痛？","options":["无","低热","高热","局部红肿"]})
        if any(s in _symptoms for s in ['活动受限','影响活动','跛行']):
            _q_bank.append({"question":"活动是否受限？","options":["正常活动","轻度受限","明显受限","无法活动"]})
        if any(s in _symptoms for s in ['反复发作','慢性']):
            _q_bank.append({"question":"症状是持续存在还是反复发作？","options":["首次出现","反复发作","持续不缓解","逐渐加重"]})
        if any(s in _symptoms for s in ['影响睡眠','夜间']):
            _q_bank.append({"question":"症状是否影响睡眠？","options":["不影响","轻度影响","明显影响","无法入睡"]})
        if any('腰' in s for s in _symptoms):
            _q_bank.append({"question":"弯腰或久坐后症状是否加重？","options":["加重明显","轻微加重","无变化","活动后缓解"]})
        if any(s in _symptoms for s in ['小便','尿','排尿','血尿']):
            _q_bank.append({"question":"小便有无异常？","options":["正常","血尿","尿频尿急","排尿痛"]})
        if any(s in _symptoms for s in ['皮疹','瘙痒','皮肤']):
            _q_bank.append({"question":"皮疹遇热还是遇冷加重？","options":["遇热加重","遇冷加重","无明显规律","不适用"]})
        if any(s in _symptoms for s in ['咳嗽','咳痰','咽痛','鼻塞']):
            _q_bank.append({"question":"咳嗽是干咳还是有痰？","options":["干咳无痰","白痰","黄痰","痰中带血"]})
        if any(s in _symptoms for s in ['头痛','头晕','头昏']):
            _q_bank.append({"question":"头痛或头晕的程度如何？","options":["轻微","中等","严重","难以忍受"]})
        _q_bank.append({"question":"您对什么药物过敏吗？","options":["青霉素类","磺胺类","头孢类","中药","无过敏史"]})
        _q_bank.append({"question":"请补充其他需要告知医生的情况","options":["无其他补充","我补充一些细节"]})
        questions = _q_bank[:6]
    else:
        # 通用问题
        questions = [
            {"question":"您的主要不适是什么？持续了多久？","options":["3天以内","1周","2周","1个月","3个月以上"]},
            {"question":"除了主要不适，还有哪些伴随症状？","options":["发热","咳嗽","头痛","鼻塞流涕","咽喉痛","胸闷","其他"]},
            {"question":"您有没有以下基础疾病？","options":["高血压","糖尿病","冠心病","胃病","肝病","肾病","无"]},
            {"question":"近期做过哪些检查？有异常结果吗？","options":["血常规","胸片/CT","心电图","B超","未做检查"]},
            {"question":"您对什么药物过敏吗？","options":["青霉素类","磺胺类","头孢类","中药","无过敏史"]},
            {"question":"请补充其他需要告知医生的情况","options":["无其他补充","我补充一些细节"]},
        ]
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


async def handle_selection_answers(ws, pid: str, data: dict):
    patient = get_patient(pid) or {}
    answers = data.get("answers") or data.get("selection_answers") or {}
    if isinstance(answers, list):
        answers = {str(i): a for i, a in enumerate(answers)}

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
        for q_key, q_val in answers.items():
            if isinstance(q_val, dict):
                a_text = q_val.get("answer", "")
            else:
                a_text = q_val
            if isinstance(a_text, str) and a_text.strip():
                a_trim = a_text.strip()
                if a_trim not in ("无", "无过敏史", "无其他补充", "未做检查"):
                    # 检查是否包含舌脉信息
                    if "舌" in a_trim or "苔" in a_trim or "脉" in a_trim:
                        signs_list_raw.append(a_trim)
                    else:
                        for s in a_trim.replace("、", ",").split(","):
                            s = s.strip()
                            if s and s not in symptoms_list_raw:
                                symptoms_list_raw.append(s)
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
        # 如果 symptom_text 开头直接是常见疾病名模式
        common_diseases = ["肾结石", "过敏性寻麻疹", "过敏性荨麻疹", "寻麻疹", "荨麻疹", "湿疹", "皮炎", 
                          "抽动症", "抽动障碍", "感冒", "咳嗽", "哮喘", "鼻炎", "肺炎"]
        for cd in common_diseases:
            if cd in symptom_text:
                mentioned = cd
                break
        
        m1_input = {
            "patient_mentioned_disease": mentioned,
            "chief_complaint": symptom_text,
            "symptoms": symptoms_list_raw,
            "signs": signs_list_raw,
            "labs": [], "imaging": [],
            "negative_findings": [],
            "duration": "半年" if "半年" in symptom_text or "半年" in str(answers) else "半月",
            "onset": ""
        }
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
        "咳嗽变异性哮喘": "喉源性咳嗽",
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
    _known_western_diseases = ["颈椎病", "后循环缺血", "失眠症", "偏头痛", 
                                "子宫肌瘤", "子宫内膜异位症", "子宫腺肌症",
                                "高血压", "糖尿病", "冠心病", "脑梗死", "脑出血",
                                "帕金森病", "阿尔茨海默病", "焦虑症", "抑郁症"]
    if hasattr(m1_r, 'get') and m1_r.get('diagnosis_calibration', {}).get('calibrated_diagnosis', []):
        for _d in m1_r['diagnosis_calibration']['calibrated_diagnosis']:
            if _d in _known_western_diseases:
                print("[WESTERN_PRIORITY]", disease, "->", _d, "(候选中有已知西医病名)")
                disease = _d
                break
    # 优先2：检查患者主诉中提到的西医病名（"诊断：XXX"）
    if disease not in _known_western_diseases:
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
        m2_r = m2.process(
            primary_disease=_m2_kb_disease,
            symptoms=m2_symptoms,
            age=patient_age,
            weight=""
        )
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
                m2_r = m2.process(primary_disease=disease, symptoms=m2_symptoms, age=patient_age, weight="")
        
        if m2_r and not m2_r.get("error"):
            sd = m2_r.get("syndrome_differentiation", {})
            ss = sd.get("selected_syndrome", {})
            formula = m2_r.get("formula", {})
            syndrome_name = ss.get("name", "")
            formula_name = formula.get("name", "")
            herbs = formula.get("herbs", [])
            # === 从 full_decoction 修复/补全 herbs ===
            # 知识库中很多方剂的 herbs 字段只记录了前几味药，但 full_decoction 中有完整的药物列表
            try:
                _hd_full = formula.get("full_decoction", "")
                if not _hd_full:
                    import json as _hd_json, re as _hd_re
                    _hd_m2_kb = getattr(m2, 'kb', {})
                    _hd_disease_data = _hd_m2_kb.get(_m2_kb_disease if '_m2_kb_disease' in dir() and _m2_kb_disease else disease, {})
                    for _hd_sd in _hd_disease_data.get("syndromes", {}).values():
                        _hd_full = _hd_sd.get("full_decoction", "")
                        if _hd_full:
                            break
                if _hd_full:
                    _hd_matches = _hd_re.findall(r'([\u4e00-\u9fff\uff00-\uffef]{2,5})\s*\d+\s*\.?\d*\s*g', _hd_full)
                    _hd_exclude = {"用量","用法","疗程","水煎","不宜","后下","先煎","包煎","烊化","冲服","煎服","各等","安全","警示","疗效","预估","安全警示","预计","有效","中病","停药","疗程及","方剂"}
                    _hd_decoction_herbs = [m for m in _hd_matches if m not in _hd_exclude and len(m) >= 2 and not m.isdigit()]
                    if _hd_decoction_herbs and len(_hd_decoction_herbs) >= len(herbs):
                        # 只有当 full_decoction 的 herbs 与原始 herbs 存在实质性差异时才替换
                        # 差异条件：full 更多 或者 药名不完全匹配（如甘草vs炙甘草）
                        _hd_set = set(_hd_decoction_herbs)
                        _hb_set = set(herbs)
                        # 检查是否有任何 herbs 中的药名在 full 中以不同形式出现
                        _has_partial_match = False
                        for _h in herbs:
                            if _h not in _hd_set:
                                # 检查 full 中是否有包含该药名的其他条目
                                for _dh in _hd_decoction_herbs:
                                    if _h in _dh or _dh in _h:
                                        _has_partial_match = True
                                        break
                        if len(_hd_decoction_herbs) > len(herbs) or _has_partial_match:
                            print(f"[HERBS_FIX] {formula_name}: {len(herbs)} → {len(_hd_decoction_herbs)} herbs (from full_decoction)")
                            herbs = _hd_decoction_herbs
            except Exception as _hd_e:
                print(f"  [HERBS_FIX_ERR] {_hd_e}")
            # 统一剂量提取：优先从 M2 知识库 full_decoction 字段提取
            # 使用 M2 实际匹配到的疾病名 _m2_kb_disease，而非原始 disease 名
            herb_dosages = {}
            try:
                import json as _json, re as _re
                m2_kb_path = getattr(m2, 'kb_path', 'data/m2_formula_knowledge.json')
                with open(m2_kb_path, 'r', encoding='utf-8') as _f:
                    _kb = _json.load(_f)
                _dosage_disease = _m2_kb_disease if '_m2_kb_disease' in dir() and _m2_kb_disease else disease
                _disease_data = _kb.get(_dosage_disease, {})
                if not _disease_data:
                    for _dk in _kb:
                        if _dosage_disease in _dk or _dk in _dosage_disease:
                            _disease_data = _kb[_dk]
                            print(f"  [DOSAGE_DISEASE_MATCH] {_dosage_disease} -> {_dk}")
                            break
                for _sd in _disease_data.get("syndromes", {}).values():
                    _full = _sd.get("full_decoction", "")
                    if _full:
                        for _h in herbs:
                            if _h in herb_dosages: continue
                            _m = _re.search(_re.escape(_h) + r"\s*(\d+\.?\d*)\s*g", _full.replace('\u3000',''))
                            if _m:
                                # 确保提取的药名不以"药""用"等非药名字开头
                                _dose_herb_name = _m.group(0).rstrip(_m.group(1) + "g ").strip()
                                # 如果 herb_dosages 中药名带"药：""用"等前缀，修正
                                for _dh_prefix in ["药：", "药:", "用：", "用:", "用药：", "用药:"]:
                                    if _dose_herb_name.startswith(_dh_prefix):
                                        _dose_herb_name = _dose_herb_name[len(_dh_prefix):]
                                        break
                                herb_dosages[_h] = _dose_herb_name + _m.group(1) + "g"
                print("[DOSAGE_EXTRACT]", "found", len(herb_dosages), "/", len(herbs), "dosages")
            except Exception as _e:
                print("  [DOSAGE_ERR]", _e)
            # fallback: 从 case_references 提取
            if not herb_dosages:
                cases = m2_r.get("case_references", [])
                for c in cases:
                    dos = c.get("dosages", [])
                    chs = c.get("herbs", [])
                    for i, h in enumerate(chs):
                        if i < len(dos) and dos[i]:
                            herb_dosages[h] = dos[i]
            # 构建剂量行
            if herb_dosages:
                dose_lines = []
                for _h in herbs:
                    if _h in herb_dosages:
                        dose_lines.append(herb_dosages[_h])
                    else:
                        dose_lines.append(_h)
                dosage_str = "\n  ".join(dose_lines)
            else:
                dosage_str = ""
        print("[HSA_M2_DONE]", {"syndrome": syndrome_name, "formula": formula_name, "herbs_count": len(herbs)})
        # === 热象检测→证型/方剂重写：当症状显示明显热象而M2选出的是风寒证型时 ===
        _all_symptom_txt_for_heat = symptom_text + " " + " ".join([v.get("answer","") if isinstance(v,dict) else str(v) for v in answers.values()])
        _has_heat_signs = any(x in _all_symptom_txt_for_heat for x in ["痰黄", "黄痰", "绿痰", "痰稠", "黄稠", "苔黄", "黄苔", "黄腻", "黄稠痰", "色绿", "黄痰黏稠", "稠痰", "偏黄", "白苔偏黄", "苔白腻偏黄", "白腻偏黄", "烦热", "身热", "发热", "高热", "手心热", "手足心热", "舌红", "舌点刺", "舌暗红", "咽红", "咽喉红肿"])
        _is_cold_syndrome = any(k in syndrome_name for k in ["风寒", "寒邪", "寒凝", "寒饮", "虚寒", "肺气虚寒", "肺寒", "寒湿", "寒湿内盛", "风寒泻", "风寒头痛", "寒性", "阳虚", "寒痰", "寒凝气滞", "外寒"])
        if (_has_heat_signs and _is_cold_syndrome) or (_has_heat_signs and not syndrome_name.strip()):
            _has_green_sputum = any(x in _all_symptom_txt_for_heat for x in ["色绿", "绿痰", "绿白", "绿稠"])
            _has_yellow_greasy_fur = any(x in _all_symptom_txt_for_heat for x in ["黄腻", "黄厚", "黄燥"])
            if _has_green_sputum:
                # 绿痰→痰热蕴肺→清金化痰汤
                syndrome_name = "痰热蕴肺"
                formula_name = "清金化痰汤"
                herbs = ["黄芩", "山栀子", "知母", "桑白皮", "瓜蒌仁", "浙贝母", "麦冬", "桔梗", "甘草", "茯苓", "陈皮"]
                print(f"[HEAT_REWRITE] 绿痰+风寒证型 → 痰热蕴肺/清金化痰汤 ({len(herbs)}味)")
            elif _has_yellow_greasy_fur:
                # 黄腻苔+热象→肺热壅盛→麻杏石甘汤
                syndrome_name = "肺热壅盛"
                formula_name = "麻杏石甘汤"
                herbs = ["炙麻黄", "杏仁", "生石膏", "甘草", "黄芩", "鱼腥草", "金荞麦"]
                print(f"[HEAT_REWRITE] 黄腻苔+风寒证型 → 肺热壅盛/麻杏石甘汤 ({len(herbs)}味)")
            elif any(x in _all_symptom_txt_for_heat for x in ["发热", "高热", "手心热"]):
                # 发热+风寒证型→风热犯肺→银翘散
                syndrome_name = "风热犯肺"
                formula_name = "银翘散"
                herbs = ["金银花", "连翘", "薄荷", "牛蒡子", "芦根", "淡竹叶", "荆芥穗", "淡豆豉", "桔梗", "生甘草"]
                print(f"[HEAT_REWRITE] 发热+风寒证型 → 风热犯肺/银翘散 ({len(herbs)}味)")
            else:
                # 单纯痰黄→风热犯肺→银翘散
                syndrome_name = "风热犯肺"
                formula_name = "银翘散"
                herbs = ["金银花", "连翘", "薄荷", "牛蒡子", "桔梗", "芦根", "淡竹叶", "荆芥穗", "淡豆豉", "甘草"]
                print(f"[HEAT_REWRITE] 痰黄+风寒证型 → 风热犯肺/银翘散 ({len(herbs)}味)")
            # 清空之前加的加减药
            _symptom_additions = []
            # 从常用剂量字典补充分组
            _common_dosages = {
                "黄芩": "黄芩9g", "山栀子": "山栀子9g", "知母": "知母9g", "桑白皮": "桑白皮9g",
                "瓜蒌仁": "瓜蒌仁9g", "浙贝母": "浙贝母9g", "麦冬": "麦冬9g", "桔梗": "桔梗6g",
                "甘草": "甘草3g", "茯苓": "茯苓12g", "陈皮": "陈皮6g",
                "炙麻黄": "炙麻黄6g", "杏仁": "杏仁9g", "生石膏": "生石膏30g", "鱼腥草": "鱼腥草15g",
                "金荞麦": "金荞麦15g",
                "金银花": "金银花9g", "连翘": "连翘9g", "薄荷": "薄荷6g", "牛蒡子": "牛蒡子9g",
                "芦根": "芦根9g", "淡竹叶": "淡竹叶6g", "荆芥穗": "荆芥穗6g", "淡豆豉": "淡豆豉6g",
                "生甘草": "生甘草3g",
            }
            dose_parts = []
            for _h in herbs:
                if _h in _common_dosages:
                    dose_parts.append(_common_dosages[_h])
                else:
                    dose_parts.append(_h)
            dosage_str = "\n  ".join(dose_parts)
            # 设置标志：热象重写已执行，后续症状加减不再重复添加
            _heat_rewrite_used = True
        
        # === 舌脉综合辨证分析：结合舌象/绝经/症状特征修正证型描述 ===
        # 当出现特定舌脉+症状组合时，在 syndrome_name 后补充辨证说明
        _tongue_diagnosis_note = ""
        _all_signs_txt = symptom_text + " " + " ".join([v.get("answer","") if isinstance(v,dict) else str(v) for v in answers.values()])
        # 舌淡嫩 = 气血不足/血虚；点刺 = 热象/血热
        _has_pale_tender = any(x in _all_signs_txt for x in ["舌淡嫩", "淡嫩"]) and "舌淡红" not in _all_signs_txt
        _has_prickly = any(x in _all_signs_txt for x in ["点刺", "刺", "红点"])
        _has_menopause = "绝经" in _all_signs_txt
        _has_heat_agg = any(x in _all_signs_txt for x in ["遇热", "遇热更", "遇热加重", "出汗也痒"])
        _has_fissured = any(x in _all_signs_txt for x in ["裂纹舌", "裂纹", "剥苔", "地图舌", "剥落"])
        _has_thin_yellow = any(x in _all_signs_txt for x in ["苔薄黄", "薄黄苔"])
        _has_yellow_fur = any(x in _all_signs_txt for x in ["苔黄", "黄苔", "黄腻"])
        _has_bld_stasis = any(x in _all_signs_txt for x in ["血块", "暗红", "色暗", "经色暗", "有块", "舌暗", "瘀点", "瘀斑"])
        
        if _has_pale_tender or _has_prickly or _has_menopause or _has_fissured:
            _note_parts = []
            if _has_pale_tender and _has_prickly:
                _note_parts.append("舌淡嫩点刺提示血虚有热、血热生风")
            elif _has_pale_tender:
                _note_parts.append("舌淡嫩提示气血不足/血虚")
            elif _has_prickly:
                _note_parts.append("舌有点刺提示血分有热")
            if _has_fissured:
                _note_parts.append("裂纹舌提示阴液亏虚/阴血不足")
            if _has_thin_yellow or _has_yellow_fur:
                _note_parts.append("苔薄黄/黄苔提示内有郁热")
            if _has_menopause:
                _note_parts.append("绝经后阴血亏虚，血虚生风")
            if _has_heat_agg:
                _note_parts.append("遇热加重提示血热风燥")
            if _note_parts:
                _has_add_blood_nourish = _has_pale_tender or _has_prickly or _has_fissured or _has_menopause
                if _has_add_blood_nourish:
                    _tongue_diagnosis_note = "辨证参考: " + "；".join(_note_parts) + "。处方中可酌情加强养血凉血之品（如生地、丹皮、赤芍、紫草）。"
                else:
                    _tongue_diagnosis_note = "辨证参考: " + "；".join(_note_parts) + "。"
                print("[TONGUE_DIAGNOSIS]", _tongue_diagnosis_note)
        
        # === 症状覆盖检查与药物加减 ===
        _symptom_additions = []
        # === 症状覆盖检查与药物加减 ===
        _symptom_additions = []
        if herbs:
            # 从追问答案和主诉提取全部症状文本
            _all_symptom_texts = [symptom_text] if symptom_text else []
            for q_key, q_val in answers.items():
                if isinstance(q_val, dict):
                    _a_text = q_val.get("answer", "")
                else:
                    _a_text = q_val
                if isinstance(_a_text, str) and _a_text.strip() and _a_text not in ("无", "无过敏史", "无其他补充", "未做检查"):
                    _all_symptom_texts.append(_a_text)
            _full_symptom_txt = "".join(_all_symptom_texts)
            print("[FULL_SYMPTOM_TXT]", _full_symptom_txt[:300])
            if locals().get("_heat_rewrite_used", False):
                print("[SYMPTOM_SKIP] 热象重写已执行，跳过症状加减")
                _symptom_additions = []
                SYMPTOM_HERB_MAP = []
            else:
                # 症状→药物映射表（基于中药药性功效分类）
                SYMPTOM_HERB_MAP = [
                # 咳喘类
                (["干咳", "久咳", "咳嗽日久"], ["川贝母", "款冬花"], "润肺止咳，针对久咳干咳"),
                (["痰多", "痰白", "痰稀"], ["半夏", "陈皮"], "燥湿化痰，针对痰多色白"),
                (["痰黄", "黄痰", "痰稠"], ["浙贝母", "黄芩", "瓜蒌"], "清肺化痰，针对黄痰稠痰"),
                (["咽痒", "喉痒"], ["蝉蜕", "射干", "僵蚕"], "利咽止痒"),
                (["咽干", "口干"], ["玄参", "麦冬", "天花粉"], "养阴润燥利咽"),
                (["咽痛", "喉咙痛"], ["牛蒡子", "射干", "板蓝根"], "清热解毒利咽"),
                (["鼻塞", "流涕"], ["辛夷", "苍耳子", "白芷"], "通鼻窍"),
                # 呼吸系统
                (["气喘", "气短", "呼吸急促"], ["麻黄", "葶苈子", "地龙"], "平喘降气"),
                # 发热类
                (["发热", "发烧"], ["石膏", "知母", "柴胡"], "清热退烧"),
                (["恶寒", "怕冷"], ["桂枝", "荆芥", "防风"], "解表散寒"),
                # 疼痛类
                (["头痛"], ["川芎", "白芷", "蔓荆子"], "祛风止痛"),
                (["腰痛", "腰酸"], ["杜仲", "续断", "牛膝"], "补肾强腰"),
                (["关节痛"], ["羌活", "独活", "威灵仙"], "祛风湿止痛"),
                (["腹痛", "胃痛"], ["白芍", "延胡索", "木香"], "缓急止痛"),
                # 消化类
                (["纳差", "食欲不振", "不饥", "食欲差", "无味口", "没胃口", "不思食"], ["炒麦芽", "山楂", "神曲"], "健脾开胃"),
                (["腹胀", "脘痞", "饱胀"], ["枳壳", "厚朴", "大腹皮"], "行气消胀"),
                # 舌脉类
                (["舌暗", "瘀点", "瘀斑", "舌紫暗", "有瘀点"], ["丹参", "赤芍", "川芎"], "活血化瘀通络"),
                # 鼻/窦/咽类
                (["清涕", "鼻塞", "喷嚏", "打喷嚏", "鼻痒"], ["辛夷", "苍耳子", "白芷", "细辛"], "宣肺通窍"),
                (["黄涕", "脓涕", "鼻窦", "鼻窦炎", "鼻涕倒流", "鼻后滴漏"], ["辛夷", "苍耳子", "白芷", "黄芩", "桔梗"], "清肺通窍"),
                (["咽痒", "咽干", "咽痛", "异物感"], ["蝉蜕", "玄参", "射干", "木蝴蝶"], "利咽止痒"),
                (["腺样体", "肥大"], ["夏枯草", "浙贝母", "牡蛎", "玄参"], "软坚散结"),
                # 月经类
                (["月经推迟", "月经后期", "周期推迟"], ["当归", "川芎", "香附", "益母草"], "养血调经"),
                (["乳胀", "乳房胀痛", "胸胁胀"], ["柴胡", "香附", "郁金", "青皮"], "疏肝理气"),
                (["血块", "色暗", "经色暗", "有块", "月经暗"], ["益母草", "蒲黄", "三七"], "活血化瘀调经"),
                (["便溏", "腹泻", "大便稀"], ["白术", "茯苓", "诃子"], "健脾止泻"),
                (["便秘", "大便干", "大便硬", "大便难"], ["火麻仁", "郁李仁", "枳实"], "润肠通便"),
                # 水肿类
                (["浮肿", "水肿", "面肿"], ["茯苓皮", "桑白皮", "猪苓"], "利水消肿"),
                (["小便少", "尿少", "尿黄"], ["车前子", "泽泻", "滑石"], "利尿通淋"),
                # 心神类
                (["失眠", "不寐", "难入睡", "易惊醒", "醒后难再睡"], ["酸枣仁", "远志", "合欢皮"], "养心安神"),
                (["多梦", "噩梦", "梦多", "梦扰"], ["酸枣仁", "远志", "茯神"], "宁心安神"),
                (["心悸", "心慌"], ["丹参", "龙骨", "牡蛎"], "镇心安神"),
                (["头晕", "眩晕"], ["天麻", "钩藤", "菊花"], "平肝熄风"),
                # 出汗类
                (["自汗", "出汗", "汗多"], ["麻黄根", "浮小麦", "五味子"], "固表敛汗"),
                (["盗汗", "夜间出汗"], ["地骨皮", "知母", "五味子"], "滋阴清热敛汗"),
                # 其他
                (["口苦", "口臭"], ["黄连", "黄芩", "栀子"], "清热燥湿"),
                (["乏力", "疲倦", "身困"], ["黄芪", "党参", "白术"], "益气健脾"),
                # 头发/乌发类
                (["头发白", "白发", "全白", "须发早白", "脱发", "发稀", "头发全白"], ["女贞子", "墨旱莲", "制首乌"], "滋补肝肾乌发"),
                # 血瘀类（月经病常见）
                (["血块", "暗红", "色暗", "经色暗", "有块"], ["益母草", "蒲黄", "三七"], "活血化瘀调经"),
                (["量多", "经量多", "量特多"], ["仙鹤草", "地榆", "茜草"], "凉血止血调经"),
            ]
            for _keywords, _herbs, _reason in SYMPTOM_HERB_MAP:
                for _kw in _keywords:
                    if _kw in _full_symptom_txt:
                        # 仅对特定关键词组输出调试
                        if _kw in ('月经推迟', '月经后期', '周期推迟', '乳胀', '乳房胀痛', '腺样体', '血块', '暗红', '鼻窦', '喷嚏', '打喷嚏'):
                            print(f"[SYMPTOM_DEBUG] 匹配: kw='{_kw}' herbs={_herbs} 已在herbs中={any(h in herbs for h in _herbs)}")
                        # 找第一个不在现有 herbs 中的药
                        for _h in _herbs:
                            if _h not in herbs and _h not in [x.get("herb","") for x in _symptom_additions]:
                                _symptom_additions.append({
                                    "herb": _h,
                                    "reason": _reason + "（症状匹配: " + _kw + "）",
                                    "matched_symptom": _kw
                                })
                                break
                        break
            # 最多加 3 味
            if _symptom_additions:
                # 去重，保留每个 herb 首次出现的记录
                _seen_herbs = set()
                _unique_additions = []
                for _sa in _symptom_additions:
                    if _sa["herb"] not in _seen_herbs:
                        _seen_herbs.add(_sa["herb"])
                        _unique_additions.append(_sa)
                # 按症状类别分组，尽量保证不同系统都有加减
                _category_herbs = {
                    "咳喘": ["川贝母", "款冬花", "半夏", "陈皮", "浙贝母", "黄芩", "瓜蒌"],
                    "鼻窦": ["辛夷", "苍耳子", "白芷", "细辛", "桔梗"],
                    "咽": ["蝉蜕", "射干", "玄参", "木蝴蝶", "僵蚕", "牛蒡子"],
                    "消化": ["炒麦芽", "山楂", "神曲", "枳壳", "厚朴", "大腹皮"],
                    "月经": ["当归", "川芎", "香附", "益母草", "蒲黄", "三七", "柴胡", "郁金", "青皮"],
                    "心神": ["酸枣仁", "远志", "合欢皮", "茯神", "丹参", "龙骨", "牡蛎"],
                    "清热": ["黄连", "黄芩", "栀子", "地骨皮", "知母"],
                    "补益": ["黄芪", "党参", "白术", "杜仲", "续断", "牛膝"],
                    "散结": ["夏枯草", "浙贝母", "牡蛎"],
                    "其他": [],
                }
                _categorized = {k: [] for k in _category_herbs}
                for _sa in _unique_additions:
                    _assigned = False
                    for _cat, _herbs_list in _category_herbs.items():
                        if _sa["herb"] in _herbs_list:
                            _categorized[_cat].append(_sa)
                            _assigned = True
                            break
                    if not _assigned:
                        _categorized["其他"].append(_sa)
                # 按优先级取每类最多1-2个，总共不超过6个，尽量覆盖更多系统
                _prioritized = []
                # 第一轮：每类取1个核心
                for _cat in ["咳喘", "鼻窦", "咽", "月经", "散结", "消化", "清热", "补益", "心神", "其他"]:
                    if _categorized[_cat]:
                        _prioritized.append(_categorized[_cat][0])
                    if len(_prioritized) >= 5:
                        break
                # 第二轮：如有剩余名额，从已覆盖的类别中取第2个（优先从症状多的类别取）
                if len(_prioritized) < 6:
                    for _cat in ["鼻窦", "咽", "咳喘", "月经", "散结", "消化", "清热", "补益", "心神", "其他"]:
                        if _categorized[_cat] and len(_categorized[_cat]) > 1:
                            if _categorized[_cat][1]["herb"] not in [x["herb"] for x in _prioritized]:
                                _prioritized.append(_categorized[_cat][1])
                                if len(_prioritized) >= 6:
                                    break
                _symptom_additions = _prioritized[:6]
                print("[SYMPTOM_ADDITIONS]", len(_symptom_additions), "herbs added",
                      [x["herb"] + "(" + x["matched_symptom"] + ")" for x in _symptom_additions])
                # 将加减药追加到 herbs 中（用于后续剂量提取和处方构建）
                if locals().get("_heat_rewrite_used", False):
                    _added_herbs = []
                else:
                    _added_herbs = [x["herb"] for x in _symptom_additions]
                herbs = herbs + _added_herbs
                # 也反映到 dosage_str 中（热象重写时跳过，因为已有完整剂量）
                if not locals().get("_heat_rewrite_used", False) and dosage_str:
                    for _ah in _added_herbs:
                        dosage_str += "\n  " + _ah + "（常规剂量）"
    except Exception as e:
        print("  [M2_ERR] " + str(e))

    warnings = []
    try:
        if herbs:
            m3_r = m3.review(herbs, {k: patient.get(k, "") for k in ["age", "gender", "weight", "pregnancy", "lactation"]})
            if m3_r.get("warnings"):
                warnings = m3_r["warnings"]
    except Exception as e:
        print("  [M3_ERR] " + str(e))

    # --- M4 复诊路由 ---
    followup_advice = ""
    try:
        m4_r = m4.route(
            initial_diagnosis=disease or symptom_text or "待查",
            initial_symptoms=[symptom_text] if symptom_text else [],
            followup_symptoms=[symptom_text] if symptom_text else [],
            followup_feedback="初诊完成",
            days_since_initial=7,
        )
        if m4_r and not m4_r.get("error"):
            rd = m4_r.get("routing_decision", {})
            fu_days = rd.get("suggested_days", 7)
            fu_notes = rd.get("action_reason", "") or rd.get("reason", "")
            if not fu_days:
                fu_days = rd.get("days_until_followup", 7)
            if fu_days:
                followup_advice = "建议" + str(fu_days) + "天后复诊"
            if fu_notes:
                # 过滤掉给医生看的内部校验提示
                _hide_tags = ["病名校验不符", "评估方向校验不符", "原病名不再", "原评估方向不再"]
                if not any(t in fu_notes for t in _hide_tags):
                    followup_advice = followup_advice + "。" + fu_notes if followup_advice else fu_notes
                elif fu_days:
                    followup_advice = "建议" + str(fu_days) + "天后复诊" 
    except Exception as e:
        print("  [M4_ERR] " + str(e))
    print("[HSA_M4_DONE]", {"followup_advice": followup_advice[:80] if followup_advice else ""})

    if os.getenv("ALLOW_LEGACY_BRIDGE_PRESCRIPTION_PATH", "false").lower() != "true":
        safe_summary = "\n".join([
            "当前已完成诊断与辨证候选分析。",
            "旧版直接出方路径已被安全闸门阻断，本次不生成正式处方、剂量、用法或疗程。",
            "后续必须经 M2-3 候选加减、M3 药学审核与 trace_closure 后，才能进入人工复核流程。",
        ])
        add_log(pid, "assistant", {"text": safe_summary})
        result = {
            "type": "diagnosis_result",
            "patient_id": pid,
            "diagnosis": {
                "summary": summary,
                "possible_diagnosis": disease if disease else "待查",
                "syndrome": syndrome_name or "待辨证",
                "recommendation": safe_summary,
            },
            "candidate_only": True,
            "legacy_prescription_path_blocked": True,
            "must_enter_m3": True,
            "trace_closure_required": True,
            "formal_prescription_allowed": False,
            "blocked_reason": ["legacy_bridge_prescription_path_blocked"],
            "status": "ok",
        }
        print("[WS_SEND_DIAGNOSIS_RESULT_GUARDED]", result)
        await ws.send(json.dumps(result, ensure_ascii=False))
        return

    # 构建完整处方笺（规范化格式，合并诊断+辨证+处方三部分）
    # 从 _symptom_additions 获取加减药物信息
    _modification_texts = []
    if '_symptom_additions' in dir() or '_symptom_additions' in locals():
        _sa = locals().get('_symptom_additions', [])
        for _m in _sa:
            if isinstance(_m, dict):
                _modification_texts.append("+" + _m.get("herb", "") + "（" + _m.get("reason", "")[:30] + "）")
    rx_lines_total = []
    rx_lines_total.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    rx_lines_total.append(" 守一中医AI辅助诊断系统 · 诊疗建议")
    rx_lines_total.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    rx_lines_total.append("")
    # 【第一部分：诊断结论】
    rx_lines_total.append("📋 诊断结论")
    rx_lines_total.append("━" * 30)
    # 显示 M1 诊断结果 — 如果 candidates 有就展示更多信息
    # 使用后端映射后的西医病名（disease 已经过 TCM_TO_WESTERN + DISEASE_NAME_MAP）
    if m1_candidates:
        rx_lines_total.append("候选诊断: " + "、".join(m1_candidates))
    rx_lines_total.append("西医诊断: " + (disease if disease else "待查"))
    # 如果有 NEED_MORE_INFO 信息，追加说明
    if isinstance(m1_r, dict):
        _extra = m1_r.get("cannot_decide_because", "")
        if _extra:
            rx_lines_total.append("说明: " + _extra[:100])
    rx_lines_total.append("")
    # 【第二部分：中医辨证】
    rx_lines_total.append("🌿 中医辨证")
    rx_lines_total.append("━" * 30)
    if syndrome_name:
        rx_lines_total.append("证型: " + syndrome_name)
    if formula_name:
        rx_lines_total.append("处方: " + formula_name)
    # 如果有舌脉辨证参考，追加说明
    if '_tongue_diagnosis_note' in dir() or '_tongue_diagnosis_note' in locals():
        _tdn = locals().get('_tongue_diagnosis_note', '')
        if _tdn:
            rx_lines_total.append("")
            rx_lines_total.append("📌 " + _tdn)
    rx_lines_total.append("")
    # 【第三部分：处方方案】
    rx_lines_total.append("💊 处方方案")
    rx_lines_total.append("━" * 30)
    rx_lines_total.append("")
    if formula_name:
        rx_lines_total.append("【基础方】" + formula_name)
    if herbs:
        if dosage_str:
            rx_lines_total.append("【药物及剂量】")
            for _d in dosage_str.split("\n"):
                rx_lines_total.append("  " + _d.strip())
        else:
            rx_lines_total.append("【药物】" + '、'.join(herbs[:12]))
    if _modification_texts:
        rx_lines_total.append("【加减】")
        for _mt in _modification_texts:
            rx_lines_total.append("  " + _mt)
    rx_lines_total.append("")
    rx_lines_total.append("【用法】每日1剂，水煎300ml，分早晚温服，饭后1小时服")
    if warnings:
        rx_lines_total.append("")
        rx_lines_total.append("【安全注意】")
        for w in warnings[:3]:
            rx_lines_total.append("  " + w)
    if m3_r and m3_r.get("special_population"):
        for sp in m3_r["special_population"][:2]:
            rx_lines_total.append("  ※ " + sp)
    rx_lines_total.append("")
    rx_lines_total.append("【调摄】饮食清淡，忌辛辣油腻；注意休息，避免熬夜；保持心情舒畅")
    if followup_advice:
        rx_lines_total.append("")
        rx_lines_total.append("【复诊】" + " " + followup_advice)
    rx_lines_total.append("")
    rx_lines_total.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    rx_lines_total.append("守一中医AI辅助诊断系统")
    full_rx = "\n".join(rx_lines_total)

    add_log(pid, "assistant", {"text": full_rx})

    # 构建用户可见的诊断名：优先使用后端映射后的西医病名
    _display_disease = disease if disease else m1_out.split("\n")[0][:80] if m1_out else "待查"
    # 只发 diagnosis_result 结构化消息（不再发 answer.text）
    result = {
        "type": "diagnosis_result",
        "patient_id": pid,
        "diagnosis": {
            "summary": summary,
            "possible_diagnosis": _display_disease,
            "syndrome": syndrome_name or "待辨证",
            "recommendation": full_rx,
        },
        "status": "ok",
    }
    print("[WS_SEND_DIAGNOSIS_RESULT]", result)
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
