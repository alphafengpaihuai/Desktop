"""
M1 诊断引擎综合测试套件
"""

from m1_engine import M1DiagnosisEngine


def run_tests():
    engine = M1DiagnosisEngine()
    passed = 0
    failed = 0

    def test(name, disease, symptoms, checks):
        nonlocal passed, failed
        result = engine.diagnose(disease, symptoms)
        ok = True
        for check in checks:
            if check == "matched":
                if result.matched_disease is None:
                    print(f"  ✗ {name}: 期望匹配到疾病，但未匹配")
                    ok = False
            elif check == "no_match":
                if not result.no_match:
                    print(f"  ✗ {name}: 期望不匹配，但匹配到了 {result.matched_disease.disease_name if result.matched_disease else '?'}")
                    ok = False
            elif check == "not_allowed":
                if not result.not_allowed:
                    print(f"  ✗ {name}: 期望不允许入口，但被允许了")
                    ok = False
            elif check.startswith("score>"):
                threshold = float(check.split(">")[1])
                if result.matched_disease and result.matched_disease.overall_score < threshold:
                    print(f"  ✗ {name}: 期望分数>{threshold}，实际 {result.matched_disease.overall_score:.2f}")
                    ok = False
            elif check.startswith("diffs>"):
                threshold = int(check.split(">")[1])
                if len(result.differential_list) < threshold:
                    print(f"  ✗ {name}: 期望至少{threshold}个鉴别诊断，实际 {len(result.differential_list)}")
                    ok = False

        if ok:
            passed += 1
            disease_show = result.matched_disease.disease_name if result.matched_disease else "(none)"
            print(f"  ✓ {name} [{disease_show}]")
        else:
            failed += 1

    print("=" * 60)
    print("M1 诊断引擎测试套件")
    print("=" * 60)

    # ── 基本匹配测试 ──
    print("\n【基本匹配测试】")
    test("急性支气管炎(中入中症)", "急性支气管炎",
         ["急性咳嗽", "低热", "咳痰", "咽痛"],
         ["matched", "score>0.2", "diffs>2"])

    test("Acute Bronchitis(英入英症)", "Acute Bronchitis",
         ["acute cough", "low-grade fever", "cough with sputum", "sore throat"],
         ["matched", "score>0.5", "diffs>2"])

    test("社区获得性肺炎(中入)", "社区获得性肺炎",
         ["cough", "fever >38.5", "dyspnea", "productive sputum"],
         ["matched", "score>0.2"])

    test("Bronchial Asthma(中入)", "支气管哮喘",
         ["wheezing", "dyspnea", "nocturnal cough", "chest tightness"],
         ["matched", "score>0.15"])

    # ── 心血管测试 ──
    print("\n【心血管疾病测试】")
    test("Acute Myocardial Infarction", "Acute Myocardial Infarction",
         ["acute chest pain", "troponin rise and fall", "ST elevation", "regional wall motion abnormality"],
         ["matched", "score>0.2", "diffs>2"])

    test("房颤(中入)", "房颤",
         ["palpitations", "irregularly irregular rhythm", "dyspnea"],
         ["matched", "score>0.2"])

    test("Essential Hypertension", "Essential Hypertension",
         ["elevated BP >140/90", "chronic hypertension"],
         ["matched", "score>0.2"])

    # ── 消化系统测试 ──
    print("\n【消化系统测试】")
    test("慢性胃炎(中入)", "慢性胃炎",
         ["上腹不适", "消化不良", "反酸", "恶心"],
         ["matched", "score>0.15"])

    test("Acute Pancreatitis", "Acute Pancreatitis",
         ["severe epigastric pain radiating to back", "elevated lipase >3x ULN", "nausea", "vomiting"],
         ["matched", "score>0.2"])

    # ── 精神心理测试 ──
    print("\n【精神心理测试】")
    test("Major Depressive Disorder", "Major Depressive Disorder",
         ["depressed mood", "anhedonia", "fatigue", "insomnia", "weight loss"],
         ["matched", "score>0.2"])

    test("Generalized Anxiety Disorder", "Generalized Anxiety Disorder",
         ["excessive worry", "restlessness", "fatigue", "muscle tension", "sleep disturbance"],
         ["matched", "score>0.2"])

    # ── 风湿免疫测试 ──
    print("\n【风湿免疫测试】")
    test("类风湿关节炎(中入)", "类风湿关节炎",
         ["morning stiffness >30min", "swollen joints", "joint pain", "elevated CRP"],
         ["matched", "score>0.2"])

    # ── 边界条件测试 ──
    print("\n【边界条件测试】")
    test("不允许入口", "Amenorrhea",
         ["secondary amenorrhea"],
         ["not_allowed"])

    test("未知疾病", "NonExistentDisease12345",
         ["fever"],
         ["no_match"])

    # ── 未输入症状 ──
    test("无症状输入", "Acute Bronchitis", [],
         ["matched"])

    # ── 输出格式检查 ──
    print("\n【格式输出检查】")
    result = engine.diagnose("Acute Bronchitis", ["cough", "fever"])
    output = engine.format_diagnosis_result(result)
    # 确保没有输出中医诊断内容（免责声明中的"中医"是允许的）
    # 错误的中医输出：输出病名/证型/方剂/中药
    tcm_forbidden = ["证型", "方剂", "中药", "治则", "调理", "阴阳", "气血", "脏腑", "寒热", 
                     "病机", "辨证", "中成药", "针灸", "推拿", "草药", "汤药"]
    found_tcm = [kw for kw in tcm_forbidden if kw in output]
    if found_tcm:
        print(f"  ✗ 输出包含禁止的中医内容: {found_tcm}")
        failed += 1
    else:
        print("  ✓ 输出未包含禁止的中医诊断内容")
        passed += 1

    # 确保包含关键信息
    for key in ["①", "病理轴", "鉴别诊断", "注意事项", "M1 阶段"]:
        if key not in output:
            print(f"  ✗ 输出缺少关键部分: {key}")
            failed += 1
            break
    else:
        print("  ✓ 输出包含所有关键部分")
        passed += 1

    # ── 总结 ──
    print(f"\n{'=' * 60}")
    print(f"测试结果: {passed} 通过, {failed} 失败, 共 {passed+failed} 项")
    print(f"{'=' * 60}")
    return failed == 0


if __name__ == "__main__":
    run_tests()
