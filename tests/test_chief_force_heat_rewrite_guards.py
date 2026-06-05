import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "bridge_server.py"
BRIDGE_SOURCE = BRIDGE_PATH.read_text(encoding="utf-8")
BRIDGE_AST = ast.parse(BRIDGE_SOURCE)


def _literal_assignment(name):
    for node in ast.walk(BRIDGE_AST):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in bridge_server.py")


CHRONIC_TERMS = _literal_assignment("CHIEF_FORCE_CHRONIC_DISEASE_KEYWORDS")
ACUTE_TERMS = _literal_assignment("CHIEF_FORCE_ACUTE_CURRENT_KEYWORDS")


def _static_skip_chief_force(chief_complaint_disease, current_disease):
    return (
        any(term in chief_complaint_disease for term in CHRONIC_TERMS)
        and any(term in current_disease for term in ACUTE_TERMS)
    )


class ChiefComplaintForceAndHeatRewriteGuardTest(unittest.TestCase):
    def test_chronic_rhinitis_history_does_not_override_acute_airway_diagnosis(self):
        self.assertTrue(
            _static_skip_chief_force("过敏性鼻炎", "上气道咳嗽综合征")
        )
        self.assertTrue(
            _static_skip_chief_force("鼻炎", "急性上呼吸道感染")
        )

    def test_chronic_force_still_allowed_when_current_disease_is_not_acute(self):
        self.assertFalse(
            _static_skip_chief_force("过敏性鼻炎", "慢性鼻炎")
        )

    def test_acute_chief_complaint_is_not_blocked_by_chronic_guard(self):
        self.assertFalse(
            _static_skip_chief_force("急性扁桃体炎", "上气道咳嗽综合征")
        )

    def test_guard_function_exists_in_bridge_server(self):
        self.assertIn("def should_skip_chief_complaint_force", BRIDGE_SOURCE)

    def test_heat_rewrite_covers_partial_yellow_fur_and_vexing_heat(self):
        for term in ["白苔偏黄", "苔白腻偏黄", "白腻偏黄", "烦热", "身热"]:
            self.assertIn(term, BRIDGE_SOURCE)

    def test_cold_syndrome_detection_covers_common_cold_or_yang_deficient_names(self):
        for term in ["寒湿内盛", "风寒泻", "风寒头痛", "阳虚", "寒痰", "寒凝气滞", "外寒"]:
            self.assertIn(term, BRIDGE_SOURCE)

    def test_chief_force_skip_log_is_present(self):
        self.assertIn("CHIEF_COMPLAINT_SKIP_CHRONIC", BRIDGE_SOURCE)


if __name__ == "__main__":
    unittest.main()
