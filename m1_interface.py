"""
M1 西医诊断交互界面 — 中医辅助诊疗系统「守一」
=============================================
交互式命令行，支持多轮诊断查询
"""

import sys
import json
from m1_engine import M1DiagnosisEngine


def print_header():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║        守一 · M1 西医辅助诊断系统              ║")
    print("║    240 疾病 · 36 病理轴 · 病理覆盖匹配          ║")
    print("╚══════════════════════════════════════════════════╝")
    print()


def list_diseases(engine, keyword: str = ""):
    """列出疾病"""
    diseases = engine.get_available_diseases()
    if keyword:
        diseases = [d for d in diseases if keyword.lower() in d.lower()]

    print(f"\n共 {len(diseases)} 条可用疾病:")
    print("-" * 50)
    for i, d in enumerate(diseases, 1):
        print(f"  {i:3d}. {d}")
    print()


def show_disease_detail(engine, disease_name: str):
    """显示疾病详情"""
    entry = engine.get_disease_detail(disease_name)
    if not entry:
        print(f"\n❌ 未找到疾病: {disease_name}")
        return

    print(f"\n{'─' * 50}")
    print(f"  疾病: {entry['disease_name']}")
    print(f"  类型: {engine._entry_type_cn(entry['entry_type'])}")
    print(f"  允许 M1 入口: {'是' if entry.get('m1_diagnosis_entry_allowed') else '否'}")
    print()

    axes = entry.get("axes", [])
    if axes:
        print(f"  病理轴 ({len(axes)}):")
        for a in axes:
            findings = a.get("typical_findings", [])
            print(f"    [{a.get('role', '?')}] {a['axis']} "
                  f"(w={a.get('weight', '?')}, conf={a.get('confidence', '?')})")
            if findings:
                for f in findings[:3]:
                    print(f"      → {f}")
                if len(findings) > 3:
                    print(f"      ... +{len(findings)-3} more")

    must_not = entry.get("must_not_be_primary_when", [])
    if must_not:
        print(f"\n  排除条件:")
        for m in must_not:
            print(f"    ⚠ {m}")

    checks = entry.get("need_external_check_if", [])
    if checks:
        print(f"\n  建议外部检查:")
        for c in checks:
            print(f"    🔬 {c}")

    notes = entry.get("notes", "")
    if notes:
        print(f"\n  备注: {notes[:200]}")
    print(f"{'─' * 50}\n")


def interactive_mode():
    """交互模式"""
    engine = M1DiagnosisEngine()
    print_header()
    print(f"✓ 已加载 {len(engine.db)} 条疾病数据, {len(engine.available_axes)} 个病理轴")
    print()

    while True:
        print()
        print("支持的命令:")
        print("  diagnose    — 开始一次诊断评估")
        print("  list        — 列出所有疾病")
        print("  search      — 搜索疾病")
        print("  detail      — 查看疾病详情")
        print("  help        — 显示帮助")
        print("  quit/exit   — 退出")
        print()

        cmd = input("m1> ").strip().lower()

        if cmd in ("quit", "exit", "q"):
            print("再见。")
            break

        elif cmd in ("list", "ls"):
            keyword = input("关键词过滤（直接回车列出全部）: ").strip()
            list_diseases(engine, keyword)

        elif cmd == "search":
            query = input("输入搜索词: ").strip()
            if query:
                results = engine.search_diseases(query)
                if results:
                    print(f"\n找到 {len(results)} 个匹配:")
                    for i, r in enumerate(results[:20], 1):
                        print(f"  {i}. {r['disease_name']} ({engine._entry_type_cn(r['entry_type'])})")
                else:
                    print("❌ 未找到匹配")

        elif cmd == "detail":
            name = input("输入疾病名: ").strip()
            if name:
                show_disease_detail(engine, name)

        elif cmd in ("diagnose", "diag"):
            do_diagnosis(engine)

        elif cmd == "help":
            print()
            print("  diagnose  疾病名 | 症状1, 症状2, ...")
            print("    例如: diagnose Acute Bronchitis | cough, fever, sputum")
            print()
            print("  list      列出所有可用疾病")
            print("  search    模糊搜索疾病")
            print("  detail    查看疾病详情")
            print()

        else:
            print(f"未知命令: {cmd}")


def do_diagnosis(engine: M1DiagnosisEngine):
    """执行一次诊断"""
    print()
    print("输入格式: 疾病名 | 症状1, 症状2, 症状3, ...")
    print("  例如: Acute Bronchitis | acute cough, low-grade fever, sore throat")
    print()

    raw = input("诊> ").strip()
    if not raw:
        return

    if "|" in raw:
        parts = raw.split("|", 1)
        disease_name = parts[0].strip()
        symptoms_str = parts[1].strip()
        symptoms = [s.strip() for s in symptoms_str.split(",") if s.strip()]
    else:
        # 假设第一项是疾病名
        tokens = raw.split()
        disease_name = tokens[0] if tokens else ""
        symptoms = tokens[1:] if len(tokens) > 1 else []
        if not symptoms:
            symptoms = input("输入症状/体征（逗号分隔）: ").strip().split(",")
            symptoms = [s.strip() for s in symptoms if s.strip()]

    if not disease_name:
        print("❌ 请输入疾病名")
        return

    if not symptoms:
        print("⚠ 未输入患者表现，诊断将仅基于数据库信息")
        symptoms = []

    print(f"\n⏳ 正在评估「{disease_name}」...")
    result = engine.diagnose(disease_name, symptoms)
    print(engine.format_diagnosis_result(result))


def batch_test(engine: M1DiagnosisEngine):
    """运行一组预设测试用例"""
    test_cases = [
        ("Acute Bronchitis", ["acute cough", "low-grade fever", "cough with sputum"]),
        ("Community-Acquired Pneumonia", ["cough", "fever >38.5", "dyspnea", "productive sputum", "chest pain"]),
        ("Bronchial Asthma", ["wheezing", "dyspnea", "nocturnal cough", "chest tightness"]),
        ("Acute Coronary Syndrome", ["acute chest pain", "troponin elevation", "ST elevation on ECG"]),
        ("Panic Disorder", ["palpitations", "shortness of breath", "dizziness", "fear of dying"]),
        ("Allergic Rhinitis", ["sneezing", "watery rhinorrhea", "nasal congestion", "itchy eyes"]),
        ("Bacterial Vaginosis", ["thin grayish discharge", "fishy odor", "vaginal pH>4.5"]),
        ("Urinary Tract Infection", ["dysuria", "urinary frequency", "suprapubic pain"]),
    ]

    print("\n" + "=" * 60)
    print("批量测试用例演示")
    print("=" * 60)

    for disease, symptoms in test_cases:
        print(f"\n{'#' * 60}")
        print(f"疾病: {disease}")
        print(f"症状: {symptoms}")
        result = engine.diagnose(disease, symptoms)
        print(engine.format_diagnosis_result(result))
        print(f"\n{'#' * 60}")


if __name__ == "__main__":
    if "--batch" in sys.argv:
        engine = M1DiagnosisEngine()
        batch_test(engine)
    elif "--detail" in sys.argv:
        engine = M1DiagnosisEngine()
        if len(sys.argv) > 2:
            show_disease_detail(engine, " ".join(sys.argv[2:]))
    else:
        interactive_mode()
