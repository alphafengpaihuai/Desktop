"""
M4 复诊路由分发模块 — 完整测试
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = 'sk-e1ec28ff01d946518d033daa91fcd222'

from services.m4 import FollowupRouter

router = FollowupRouter()
passed = 0
failed = 0


def run_test(name, result, checks):
    global passed, failed
    ok = True
    for key, expected in checks.items():
        # 支持点号路径
        parts = key.split(".")
        val = result
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, "KEY_NOT_FOUND")
            else:
                val = "KEY_NOT_FOUND"
                break
        if val != expected:
            ok = False
            print(f"    ❌ {key}: expected={expected}, got={val}")
    if ok:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name}")
        failed += 1


print("=" * 70)
print("M4 复诊路由分发模块 — 测试套件")
print("=" * 70)

# ── 测试 1：危险信号中断（SpO₂ = 88%） ──
print("\n📌 Test 1: 危险信号中断 — SpO₂=88%")
result = router.route(
    initial_diagnosis="上呼吸道感染",
    initial_symptoms=["鼻塞", "咽痛", "低热"],
    followup_symptoms=["鼻塞", "咽痛", "呼吸困难"],
    days_since_initial=3,
    vital_signs={"bp": "120/80", "hr": 85, "temp": 37.5, "spo2": 88},
    m1_card={"required_criteria_zh": ["鼻塞", "咽痛"], "supportive_features_zh": []},
)
run_test("SpO₂ 88% → EMERGENCY_STOP", result, {
    "routing_decision.action": "EMERGENCY_STOP",
    "danger_signals.triggered": True,
})

# ── 测试 2：原病名仍成立，返回 M2 ──
print("\n📌 Test 2: 原病名仍成立 → RETURN_TO_M2")
result = router.route(
    initial_diagnosis="上呼吸道感染",
    initial_symptoms=["鼻塞", "咽痛", "低热"],
    followup_symptoms=["咽痛减轻", "鼻塞残留"],
    days_since_initial=5,
    patient_feedback="好了一些",
    vital_signs={"bp": "120/75", "hr": 72, "temp": 36.5},
    m1_card={"required_criteria_zh": ["鼻塞", "咽痛", "发热"], "supportive_features_zh": []},
)
run_test("好转 → RETURN_TO_M2", result, {
    "routing_decision.action": "RETURN_TO_M2",
})

# ── 测试 3：新主诉出现（便血/黑便）→ RETURN_TO_M1 ──
print("\n📌 Test 3: 新主诉出现（便血/黑便）→ RETURN_TO_M1")
result = router.route(
    initial_diagnosis="失眠",
    initial_symptoms=["入睡困难", "多梦", "早醒"],
    followup_symptoms=["入睡改善", "便血", "黑便"],
    days_since_initial=14,
    patient_feedback="睡眠好了一些，但大便有血",
    vital_signs={"bp": "110/70", "hr": 72, "temp": 36.5},
    m1_card={"required_criteria_zh": ["入睡困难", "多梦", "早醒"], "supportive_features_zh": []},
)
run_test("便血/黑便 → EMERGENCY_STOP", result, {
    "routing_decision.action": "EMERGENCY_STOP",
    "danger_signals.triggered": True,
})

# ── 测试 4：超显效窗口，无改善 → Q4 ──
print("\n📌 Test 4: 超显效窗口且无改善 → RETURN_TO_M2 + Q4")
result = router.route(
    initial_diagnosis="咳嗽",
    initial_symptoms=["咳嗽", "咳痰"],
    followup_symptoms=["咳嗽"],
    days_since_initial=30,
    patient_feedback="没变化",
    vital_signs={"bp": "120/75", "hr": 70, "temp": 36.5},
    m1_card={"required_criteria_zh": ["咳嗽", "咳痰"], "supportive_features_zh": []},
)
run_test("30天无改善 → Q4", result, {
    "routing_decision.action": "RETURN_TO_M2",
    "routing_decision.quadrant": "Q4",
    "routing_decision.suggested_action": "更方",
})

# ── 测试 5：模型低置信度 + 新发症状 → RETURN_TO_M1 ──
print("\n📌 Test 5: 模型低置信度兜底 → RETURN_TO_M1")
# 不传 m1_card，直接触发低置信度
result = router.route(
    initial_diagnosis="失眠",
    initial_symptoms=["入睡困难"],
    followup_symptoms=["入睡改善", "胃痛", "反酸"],
    days_since_initial=14,
    patient_feedback="睡眠好了但胃不舒服",
    vital_signs={"bp": "120/75", "hr": 70, "temp": 36.5},
    # 不传 m1_card
)
run_test("无 m1_card → RETURN_TO_M1", result, {
    "routing_decision.action": "RETURN_TO_M1",
})

# ── 测试 6：缺失 M1 诊断卡片 ──
print("\n📌 Test 6: 缺失 M1 诊断卡片 → RETURN_TO_M1")
result = router.route(
    initial_diagnosis="失眠",
    initial_symptoms=["入睡困难", "多梦"],
    followup_symptoms=["入睡改善", "多梦减少"],
    days_since_initial=14,
    patient_feedback="好了一些",
    vital_signs={"bp": "120/75", "hr": 70, "temp": 36.5},
)
run_test("缺失卡片 → RETURN_TO_M1", result, {
    "routing_decision.action": "RETURN_TO_M1",
})

# ── 测试 7：严重高血压 + 胸痛 → EMERGENCY_STOP ──
print("\n📌 Test 7: 严重高血压 + 胸痛 → EMERGENCY_STOP")
result = router.route(
    initial_diagnosis="高血压",
    initial_symptoms=["头晕", "头痛", "颈项强"],
    followup_symptoms=["胸痛", "头痛加重", "气短"],
    days_since_initial=7,
    patient_feedback="不好",
    vital_signs={"bp": "195/110", "hr": 115, "temp": 36.8},
    m1_card={"required_criteria_zh": ["头晕", "头痛"], "supportive_features_zh": []},
)
run_test("高血压+胸痛 → EMERGENCY_STOP", result, {
    "routing_decision.action": "EMERGENCY_STOP",
    "danger_signals.triggered": True,
})

# ── 测试 8：Q1 效佳（改善显著） ──
print("\n📌 Test 8: Q1 效佳")
result = router.route(
    initial_diagnosis="上呼吸道感染",
    initial_symptoms=["鼻塞", "咽痛", "咳嗽", "发热"],
    followup_symptoms=["咽痛好转", "鼻塞好转"],
    days_since_initial=7,
    patient_feedback="好多了",
    vital_signs={"bp": "115/75", "hr": 70, "temp": 36.5},
    m1_card={"required_criteria_zh": ["鼻塞", "咽痛", "咳嗽", "发热"], "supportive_features_zh": []},
)
run_test("改善显著 → Q1", result, {
    "routing_decision.action": "RETURN_TO_M2",
    "routing_decision.quadrant": "Q1",
    "routing_decision.suggested_action": "守方",
})

# ── 测试 9：Q2 待效（未超窗口，改善不足） ──
print("\n📌 Test 9: Q2 待效")
result = router.route(
    initial_diagnosis="失眠",
    initial_symptoms=["入睡困难", "多梦"],
    followup_symptoms=["入睡困难"],
    days_since_initial=5,
    patient_feedback="没变化",
    vital_signs={"bp": "115/75", "hr": 70, "temp": 36.5},
    m1_card={"required_criteria_zh": ["入睡困难", "多梦"], "supportive_features_zh": []},
)
run_test("未超窗口无改善 → Q2", result, {
    "routing_decision.action": "RETURN_TO_M2",
    "routing_decision.quadrant": "Q2",
    "routing_decision.suggested_action": "守方等待",
})

# ── 测试 10：Q3 激惹（改善但有新问题） ──
print("\n📌 Test 10: Q3 激惹")
result = router.route(
    initial_diagnosis="咳嗽",
    initial_symptoms=["咳嗽", "咳痰"],
    followup_symptoms=["咳嗽减轻", "胃痛"],
    days_since_initial=7,
    patient_feedback="咳嗽好了但胃不舒服",
    vital_signs={"bp": "115/75", "hr": 70, "temp": 36.5},
    m1_card={"required_criteria_zh": ["咳嗽", "咳痰"], "supportive_features_zh": []},
)
run_test("改善+新问题 → Q3", result, {
    "routing_decision.action": "RETURN_TO_M2",
    "routing_decision.quadrant": "Q3",
    "routing_decision.suggested_action": "调整",
})

# ── 测试 11：evidence_trace 审计追踪存在 ──
print("\n📌 Test 11: evidence_trace 审计追踪")
result = router.route(
    initial_diagnosis="咳嗽",
    initial_symptoms=["咳嗽", "咳痰"],
    followup_symptoms=["咳嗽减轻"],
    days_since_initial=7,
    patient_feedback="好了一些",
    vital_signs={"bp": "120/75", "hr": 70, "temp": 36.5},
    m1_card={"required_criteria_zh": ["咳嗽", "咳痰"], "supportive_features_zh": []},
)
trace = result.get("evidence_trace", {})
has_rules = len(trace.get("triggered_rules", [])) > 0
has_fields = len(trace.get("used_initial_visit_fields", [])) > 0
print(f"  triggered_rules: {trace.get('triggered_rules', [])}")
print(f"  used_initial_fields: {trace.get('used_initial_visit_fields', [])}")
if has_rules and has_fields:
    print("  ✅ evidence_trace 存在且完整")
    passed += 1
else:
    print("  ❌ evidence_trace 不完整")
    failed += 1

# ── 测试 12：现实病例 — 44岁腺肌瘤 ──
print("\n📌 Test 12: 现实病例 — 44岁腺肌瘤")
result = router.route(
    initial_diagnosis="子宫腺肌症",
    initial_symptoms=[
        "痛经", "月经周期不规律", "月经量特多", "月经暗红",
        "大小血块", "腰轻度胀痛", "腹特痛",
    ],
    followup_symptoms=["月经量减少", "腹痛减轻", "腰痛好转"],
    days_since_initial=14,
    patient_feedback="好了一些，月经量没以前那么多了",
    vital_signs={"bp": "115/75", "hr": 76, "temp": 36.5},
    m1_card={
        "required_criteria_zh": ["痛经", "月经量多", "月经周期不规律", "经期延长"],
        "supportive_features_zh": ["子宫增大", "血清CA125升高"],
    },
)
run_test("腺肌瘤有好转 → RETURN_TO_M2", result, {
    "routing_decision.action": "RETURN_TO_M2",
})
print(f"  改善系数: {result['visit_comparison']['improvement_score']}/10")
print(f"  象限: {result['routing_decision']['quadrant']}")
print(f"  建议: {result['routing_decision']['suggested_action']}")

# ── 总结 ──
print(f"\n{'='*70}")
print(f"测试完成: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
if failed == 0:
    print("✅ 全部通过")
else:
    print("❌ 部分失败")
