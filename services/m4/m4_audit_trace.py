"""
M4 审计追踪器
100% 代码执行，记录所有判断字段、触发规则、模型输出
"""
from typing import Dict, List, Optional


class AuditTrace:
    """审计追踪 — 纯代码执行"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.used_initial_visit_fields = []
        self.used_followup_visit_fields = []
        self.used_m1_card_fields = []
        self.triggered_rules = []
        self.llm_validation_summary = {}
        self.audit_notes = []

    def add_initial_field(self, field: str):
        if field not in self.used_initial_visit_fields:
            self.used_initial_visit_fields.append(field)

    def add_followup_field(self, field: str):
        if field not in self.used_followup_visit_fields:
            self.used_followup_visit_fields.append(field)

    def add_m1_card_field(self, field: str):
        if field not in self.used_m1_card_fields:
            self.used_m1_card_fields.append(field)

    def add_rule(self, rule_name: str, detail: str = ""):
        entry = {"rule": rule_name, "detail": detail}
        if entry not in self.triggered_rules:
            self.triggered_rules.append(entry)

    def set_llm_summary(self, summary: Dict):
        self.llm_validation_summary = summary

    def add_note(self, note: str):
        self.audit_notes.append(note)

    def build(self) -> Dict:
        return {
            "used_initial_visit_fields": self.used_initial_visit_fields,
            "used_followup_visit_fields": self.used_followup_visit_fields,
            "used_m1_card_fields": self.used_m1_card_fields,
            "triggered_rules": self.triggered_rules,
            "llm_validation_summary": self.llm_validation_summary,
            "audit_notes": self.audit_notes,
        }
