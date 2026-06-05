"""
M2 辨证选方引擎 v1 — 中医辅助诊疗系统「守一」
================================================
基于 M2_SOURCE_PROMPT.md 实现。
核心原则：提示词驱动，LLM 深度参与，代码仅执行确定性规则。
"""
import os
import json
import re
import time
from typing import Dict, List, Optional


class M2SyndromeSelector:
    """M2 辨证选方模块

    输入：M1 的 primary_disease + 患者临床信息
    流程：LLM 单次调用 → 选证型 → 读绑定方剂 → 检索病案参考 → 药物加减控制
    输出：证型 + 方剂 + herbs + 病案参考 + 加减建议
    """

    def __init__(self, kb_path: str = "data/m2_formula_knowledge.json",
                 herb_kb_path: str = "data/m3_herb_knowledge.json"):
        with open(kb_path, "r") as f:
            self.kb: Dict = json.load(f)

        # ── 🔒 加载时自动补全 herbs：若 full_decoction 的药物多于 herbs 字段，自动同步 ──
        self._auto_fill_herbs_from_decoction()

        self.herb_kb = {}
        try:
            with open(herb_kb_path, "r") as f:
                self.herb_kb = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 病案参考库（内置） ──
        self.case_reference: Dict[str, dict] = {}
        self.case_kb_path = "data/m2_case_reference.json"
        try:
            with open(self.case_kb_path, "r") as f:
                self.case_reference = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 循证病案缓存（LLM 查询结果） ──
        self.case_cache_path = "data/m2_case_cache.json"
        self.case_cache: Dict[str, dict] = {}
        try:
            with open(self.case_cache_path, "r") as f:
                self.case_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # ── 已引用过的病案日志（避免重复推荐） ──
        self.case_used_log_path = "data/m2_case_used_log.json"
        self.case_used_log: list = []
        try:
            with open(self.case_used_log_path, "r") as f:
                self.case_used_log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ══════════════════════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════════════════════

    def process(
        self,
        primary_disease: str,
        symptoms: Optional[List[str]] = None,
        signs: Optional[List[str]] = None,
        tongue: str = "",
        pulse: str = "",
        cold_heat: Optional[List[str]] = None,
        stool_urine: Optional[List[str]] = None,
        sleep: Optional[List[str]] = None,
        appetite: Optional[List[str]] = None,
        labs: Optional[List[str]] = None,
        imaging: Optional[List[str]] = None,
        age: str = "",
        weight: str = "",
    ) -> Dict:
        """兼容入口：内部串联 M2-1 辨证 trace 与 M2-2 候选方合同。"""
        m2_1 = self.run_m2_1_syndrome_reasoning(
            primary_disease=primary_disease,
            symptoms=symptoms,
            signs=signs,
            tongue=tongue,
            pulse=pulse,
            cold_heat=cold_heat,
            stool_urine=stool_urine,
            sleep=sleep,
            appetite=appetite,
            labs=labs,
            imaging=imaging,
            age=age,
            weight=weight,
        )
        if m2_1.get("status") == "NO_CANDIDATE":
            return m2_1

        m2_2 = self.run_m2_2_formula_candidates(
            primary_disease=primary_disease,
            syndrome_trace=m2_1.get("syndrome_trace", {}),
            symptoms=symptoms or [],
        )
        if m2_2.get("status") == "NO_CANDIDATE":
            m2_2["syndrome_trace"] = m2_1.get("syndrome_trace", {})
            m2_2["evidence_trace"] = m2_1.get("evidence_trace", [])
            return m2_2

        first_candidate = (m2_2.get("formula_candidates") or [{}])[0]
        formula = {
            "name": first_candidate.get("formula_name", ""),
            "herbs": first_candidate.get("herbs", []),
            "source": first_candidate.get("source", "data/m2_formula_knowledge.json"),
        }
        cases = self._search_cases(primary_disease, m2_1.get("selected_syndrome_key", ""))
        patient_info = {
            "symptoms": symptoms or [],
            "signs": signs or [],
            "tongue": tongue,
            "pulse": pulse,
            "cold_heat": cold_heat or [],
            "stool_urine": stool_urine or [],
            "sleep": sleep or [],
            "appetite": appetite or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "age": age,
            "weight": weight,
        }
        modifications = self._generate_modifications(
            primary_disease,
            m2_1.get("selected_syndrome_key", ""),
            formula.get("herbs", []),
            cases,
            patient_info,
        )
        result = {
            "primary_disease": primary_disease,
            "status": "PASS",
            "syndrome_differentiation": {
                "selected_syndrome": {
                    "name": m2_1.get("syndrome_trace", {}).get("syndrome_name", ""),
                    "reason": m2_1.get("syndrome_trace", {}).get("reasoning_summary", ""),
                },
                "differentiation_framework": m2_1.get("differentiation_framework", "脏腑"),
            },
            "formula": formula,
            "formula_candidates": m2_2.get("formula_candidates", []),
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": False,
            "modifications": modifications,
            "modification_candidates": self._normalize_modification_candidates(
                modifications,
                primary_disease,
                symptoms or [],
            ),
            "case_references": cases,
            "syndrome_trace": m2_1.get("syndrome_trace", {}),
            "evidence_trace": m2_1.get("evidence_trace", []),
            "input_trace": m2_1.get("input_trace", {}),
            "needs_manual_review": False,
            "formal_prescription_allowed": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def _strip_forbidden_prescription_fields(self, value):
        forbidden = {
            "prescription_text", "final_formula", "complete_formula", "final_prescription",
            "prescription", "full_formula", "dosage", "dose", "用法", "疗程",
        }
        if isinstance(value, dict):
            cleaned = {}
            removed = []
            for key, item in value.items():
                if key in forbidden:
                    removed.append(key)
                    continue
                if key == "formal_prescription_allowed":
                    cleaned[key] = False
                    if item is True:
                        removed.append(key)
                    continue
                cleaned[key] = self._strip_forbidden_prescription_fields(item)
            if removed:
                existing = cleaned.get("forbidden_fields_removed", [])
                cleaned["forbidden_fields_removed"] = sorted(set(existing + removed))
                cleaned["legacy_prescription_path_blocked"] = True
            return cleaned
        if isinstance(value, list):
            return [self._strip_forbidden_prescription_fields(item) for item in value]
        return value

    def run_m2_1_syndrome_reasoning(
        self,
        primary_disease: str,
        symptoms: Optional[List[str]] = None,
        signs: Optional[List[str]] = None,
        tongue: str = "",
        pulse: str = "",
        cold_heat: Optional[List[str]] = None,
        stool_urine: Optional[List[str]] = None,
        sleep: Optional[List[str]] = None,
        appetite: Optional[List[str]] = None,
        labs: Optional[List[str]] = None,
        imaging: Optional[List[str]] = None,
        age: str = "",
        weight: str = "",
    ) -> Dict:
        """M2-1 独立入口：只做辨证 trace，不输出方剂、处方、剂量。"""
        symptoms = symptoms or []
        input_trace = {
            "m1_primary_disease_received": primary_disease,
            "symptoms_received": symptoms,
            "knowledge_source": "data/m2_formula_knowledge.json",
            "stage": "M2_1",
        }
        syndromes = self._load_syndromes(primary_disease)
        if not syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"'{primary_disease}' 不在知识库或下无证型数据",
                missing_key=primary_disease,
                searched_terms=[primary_disease],
                input_trace=input_trace,
            ))

        patient_info = {
            "symptoms": symptoms,
            "signs": signs or [],
            "tongue": tongue,
            "pulse": pulse,
            "cold_heat": cold_heat or [],
            "stool_urine": stool_urine or [],
            "sleep": sleep or [],
            "appetite": appetite or [],
            "labs": labs or [],
            "imaging": imaging or [],
            "age": age,
            "weight": weight,
        }
        parsed = None
        if self._llm_available():
            llm_result = self._call_llm(self._build_prompt(primary_disease, syndromes, patient_info))
            parsed = self._parse_llm_result(llm_result, syndromes) if llm_result else None
        if not parsed:
            parsed = self._fallback_parse(syndromes, primary_disease, symptoms)
        if not parsed:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason="LLM 与代码兜底均无法得出辨证结果",
                missing_key=primary_disease,
                searched_terms=[primary_disease],
                input_trace=input_trace,
            ))

        selected = parsed.get("selected_syndrome", {})
        selected_key = selected.get("name", "")
        syndrome_data = syndromes.get(selected_key, {})
        name_match = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
        syndrome_display = name_match.group(1) if name_match else selected_key
        reason = selected.get("reason") or parsed.get("reasoning") or selected.get("trigger") or "知识库证型匹配"
        result = {
            "stage": "M2_1",
            "status": "PASS",
            "primary_disease": primary_disease,
            "selected_syndrome_key": selected_key,
            "differentiation_framework": parsed.get("differentiation_framework", "脏腑"),
            "syndrome_trace": {
                "syndrome_name": syndrome_display,
                "evidence": [s for s in symptoms if isinstance(s, str) and s.strip()],
                "reasoning_summary": reason,
                "confidence": 0.7 if syndrome_display else 0.0,
            },
            "evidence_trace": [
                {"source": "patient_symptom", "text": s}
                for s in symptoms if isinstance(s, str) and s.strip()
            ],
            "input_trace": input_trace,
            "formal_prescription_allowed": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def run_m2_2_formula_candidates(
        self,
        primary_disease: str,
        syndrome_trace: Optional[Dict] = None,
        symptoms: Optional[List[str]] = None,
    ) -> Dict:
        """M2-2 独立入口：只查方剂候选，不输出正式处方。"""
        symptoms = symptoms or []
        syndrome_trace = syndrome_trace or {}
        input_trace = {
            "m1_primary_disease_received": primary_disease,
            "symptoms_received": symptoms,
            "knowledge_source": "data/m2_formula_knowledge.json",
            "stage": "M2_2",
        }
        syndromes = self._load_syndromes(primary_disease)
        if not syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"'{primary_disease}' 不在知识库或下无证型数据",
                missing_key=primary_disease,
                searched_terms=[primary_disease],
                input_trace=input_trace,
            ))

        syndrome_name = syndrome_trace.get("syndrome_name", "")
        selected_key = ""
        for key, data in syndromes.items():
            trigger = str(data.get("trigger", ""))
            if syndrome_name and (syndrome_name == key or syndrome_name in trigger):
                selected_key = key
                break
        if not selected_key:
            fallback = self._fallback_parse(syndromes, primary_disease, symptoms)
            selected_key = (fallback or {}).get("selected_syndrome", {}).get("name", "")
        if not selected_key or selected_key not in syndromes:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"'{primary_disease}' 未找到可匹配证型对应方剂",
                missing_key=syndrome_name or primary_disease,
                searched_terms=[primary_disease, syndrome_name],
                input_trace=input_trace,
            ))

        syndrome_data = syndromes[selected_key]
        formula_name = syndrome_data.get("formula_name", "")
        herbs = syndrome_data.get("herbs", [])
        if not formula_name:
            return self._strip_forbidden_prescription_fields(self._build_no_candidate(
                primary_disease=primary_disease,
                reason=f"证型 '{selected_key}' 缺少绑定方剂",
                missing_key=selected_key,
                searched_terms=[primary_disease, syndrome_name, selected_key],
                input_trace=input_trace,
            ))

        name_match = re.search(r'<(.+?)>', str(syndrome_data.get("trigger", "")))
        display = syndrome_name or (name_match.group(1) if name_match else selected_key)
        result = {
            "stage": "M2_2",
            "status": "PASS",
            "primary_disease": primary_disease,
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": False,
            "formula_candidates": [{
                "disease_name": primary_disease,
                "syndrome_name": display,
                "formula_name": formula_name,
                "herbs": herbs,
                "source": "data/m2_formula_knowledge.json",
            }],
            "searched_terms": [primary_disease, syndrome_name, selected_key],
            "input_trace": input_trace,
            "formal_prescription_allowed": False,
        }
        return self._strip_forbidden_prescription_fields(result)

    def _build_no_candidate(self, primary_disease: str, reason: str, missing_key: str,
                            searched_terms: List[str], input_trace: Optional[Dict] = None) -> Dict:
        return {
            "primary_disease": primary_disease,
            "status": "NO_CANDIDATE",
            "formula_candidates": [],
            "modification_candidates": [],
            "candidate_only": True,
            "need_m2_3": True,
            "must_enter_m3": True,
            "no_candidate": True,
            "reason": reason,
            "searched_terms": searched_terms,
            "missing_key": missing_key,
            "syndrome_trace": {
                "syndrome_name": "",
                "evidence": [],
                "reasoning_summary": reason,
                "confidence": 0.0,
            },
            "evidence_trace": [],
            "input_trace": input_trace or {},
            "needs_manual_review": True,
        }

    def _with_candidate_contract(self, result: Dict, primary_disease: str,
                                 symptoms: List[str], input_trace: Dict) -> Dict:
        selected = result.get("syndrome_differentiation", {}).get("selected_syndrome", {})
        syndrome_name = selected.get("name", "")
        reason = selected.get("reason", "") or selected.get("trigger", "") or "知识库证型匹配"
        result["status"] = result.get("status", "PASS")
        result["candidate_only"] = True
        result["need_m2_3"] = True
        result["must_enter_m3"] = True
        result["no_candidate"] = False
        result["formula_candidates"] = [{
            "disease_name": primary_disease,
            "syndrome_name": syndrome_name,
            "formula_name": result.get("formula", {}).get("name", ""),
            "source": result.get("formula", {}).get("source", "data/m2_formula_knowledge.json"),
        }] if result.get("formula", {}).get("name") else []
        result["syndrome_trace"] = {
            "syndrome_name": syndrome_name,
            "evidence": [s for s in symptoms if isinstance(s, str) and s.strip()],
            "reasoning_summary": reason,
            "confidence": 0.7 if syndrome_name else 0.0,
        }
        result["evidence_trace"] = [
            {"source": "patient_symptom", "text": s}
            for s in symptoms if isinstance(s, str) and s.strip()
        ]
        result["input_trace"] = input_trace
        result["modification_candidates"] = self._normalize_modification_candidates(
            result.get("modifications", []),
            primary_disease,
            symptoms,
        )
        return result

    def _normalize_modification_candidates(self, modifications: List[Dict],
                                           disease_name: str,
                                           symptoms: List[str]) -> List[Dict]:
        normalized = []
        symptom_text = "；".join(symptoms or [])
        for item in modifications or []:
            if not isinstance(item, dict):
                continue
            herb = item.get("herb", "")
            if not herb:
                continue
            evidence = item.get("source") or item.get("evidence_sources") or "case_reference_or_herb_kb"
            if isinstance(evidence, str):
                evidence = [evidence]
            normalized.append({
                "herb": herb,
                "action": item.get("action", "candidate_add"),
                "reason": item.get("reason", ""),
                "target_disease": item.get("target_disease", disease_name),
                "target_symptom": item.get("target_symptom", item.get("matched_symptom", symptom_text)),
                "western_pathology": item.get("western_pathology", item.get("western_pathology_target", "symptom_targeted_support")),
                "evidence_sources": evidence,
            })
        return normalized

    # ══════════════════════════════════════════════════════
    #  Prompt 构建
    # ══════════════════════════════════════════════════════

    def _build_prompt(self, primary_disease, syndromes, patient_info) -> str:
        """按 M2 提示词 8.1 节构建 prompt"""

        syndrome_candidates = ""
        for name, data in syndromes.items():
            trigger = data.get("trigger", "无描述")
            syndrome_candidates += f"\n### {name}\n{trigger}\n"

        herb_ref = self._build_herb_reference()

        s = patient_info.get("symptoms", []) or []
        return f"""你是一位中医辨证专家。请根据以下患者信息和候选证型，完成辨证。

## 患者信息
西医主病名：{primary_disease}
症状：{'；'.join(s)}
舌象：{patient_info.get('tongue', '')}
脉象：{patient_info.get('pulse', '')}
寒热：{'；'.join(patient_info.get('cold_heat', []) or [])}
二便：{'；'.join(patient_info.get('stool_urine', []) or [])}
年龄：{patient_info.get('age', '')}

## 辨证框架规则
- 急性起病、发热恶寒、感染性表现 -> 卫气营血辨证
- 慢性反复、功能失调、体质调理 -> 脏腑辨证
- 两者兼有 -> 先判外感阶段，再用脏腑辨证

## 候选证型（均来自知识库）
{syndrome_candidates}

## 常用药物性味归经参考（供加减时参考）
{herb_ref}

请完成以下任务：
1. 判断应使用哪种辨证框架
2. 从候选证型中选出最匹配的 1 个证型，简述理由

只输出 JSON，不要输出其他内容：
{{
  "differentiation_framework": "卫气营血/脏腑/混合",
  "selected_syndrome": {{
    "name": "证型名称",
    "reason": "匹配理由简述"
  }},
  "missing_info": ["缺失的关键信息列表"]
}}"""

    def _build_herb_reference(self) -> str:
        """构建常用药物性味归经参考"""
        if not self.herb_kb:
            return "（无药物参考数据）"
        lines = []
        common_herbs = [
            "麻黄", "桂枝", "柴胡", "黄芩", "黄连", "金银花", "连翘",
            "蒲公英", "板蓝根", "大青叶", "石膏", "知母", "栀子",
            "龙胆草", "生地黄", "玄参", "牡丹皮", "赤芍", "白芍",
            "当归", "川芎", "丹参", "桃仁", "红花", "牛膝",
            "半夏", "陈皮", "枳壳", "厚朴", "苍术", "白术",
            "茯苓", "泽泻", "车前子", "茵陈", "金钱草",
            "附子", "肉桂", "干姜", "吴茱萸", "细辛",
            "人参", "黄芪", "党参", "山药", "甘草",
            "麦冬", "五味子", "枸杞子", "菊花", "薄荷",
            "防风", "荆芥", "白芷", "葛根",
        ]
        for h in common_herbs:
            info = self.herb_kb.get(h)
            if info:
                props = "、".join(info.get("properties", []))
                meridians = "、".join(info.get("meridians", []))
                cls = info.get("class", "")
                lines.append(f"  {h}：性味【{props}】归经【{meridians}】功效分类【{cls}】")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════
    #  病案检索（新增）
    # ══════════════════════════════════════════════════════

    def _search_cases(self, disease_name: str, syndrome_name: str = "") -> list:
        """根据病名和证型检索病案参考库

        规则：
        - 优先精确匹配（病名+证型）
        - 再模糊匹配（病名部分匹配）
        - 最多返回 3 条
        """
        matched = []

        # 1. 精确匹配：病名|证型
        for key, case in self.case_reference.items():
            if len(matched) >= 3:
                break
            parts = key.split("|")
            if len(parts) == 2:
                cd, cs = parts
                if cd == disease_name and (not syndrome_name or cs == syndrome_name):
                    matched.append(case)

        # 2. 按病名匹配
        if len(matched) < 3:
            for key, case in self.case_reference.items():
                if len(matched) >= 3:
                    break
                parts = key.split("|")
                if len(parts) == 2 and parts[0] == disease_name and case not in matched:
                    matched.append(case)

        # 3. 按部分病名匹配
        if len(matched) < 3:
            dl = disease_name.lower()
            for key, case in self.case_reference.items():
                if len(matched) >= 3:
                    break
                parts = key.split("|")
                if len(parts) == 2:
                    cd = parts[0]
                    if dl in cd.lower() or cd.lower() in dl:
                        if case not in matched:
                            matched.append(case)

        return matched[:3]

    def _fetch_reference_case_from_llm(self, disease_name: str, syndrome_name: str,
                                        patient_info: dict) -> Optional[dict]:
        """当病案库中无匹配时，让 LLM 查询循证医学网站获取病案参考

        规则：
        - 只能查默沙东、PubMed 等循证来源
        - 禁止编造病案
        - 结果缓存到 case_cache
        - 药物加减控制在 3 味左右
        """
        cache_key = f"{disease_name}|{syndrome_name}"

        if cache_key in self.case_cache:
            return self.case_cache[cache_key]

        if not self._llm_available():
            return None

        symptom_text = ";".join(patient_info.get("symptoms", []) or [])[:200]

        prompt_lines = [
            '你是一个中医医学知识助手。你的任务是查找疾病 "' + disease_name + '" 合并证型 "' + syndrome_name + '" 的真实中医病案参考。',
            '## 规则（严格遵循）',
            '1. 你只能基于默沙东诊疗手册（MSD Manuals）、PubMed、中国知网（CNKI）、万方、维普等循证医学来源中的真实文献病案进行回答。',
            '2. 严禁编造病案。如果找不到可靠信息，请如实说明。',
            '3. 输出必须是严格的 JSON 格式，不得输出 Markdown。',
            '## 患者当前情况（供参考）',
            '症状：' + symptom_text,
            '## 输出格式',
            '{',
            '  "disease_name": "西医病名",',
            '  "syndrome_name": "证型名称",',
            '  "formula_name": "方剂名称",',
            '  "herbs": ["药1", "药2", "药3"],',
            '  "modifications": [{"herb": "加味药名", "reason": "加减理由"}],',
            '  "source": "参考来源描述",',
            '  "key_points": "该病案的关键辨证要点"',
            '}',
            '如果确实找不到可靠信息，输出：{"disease_name": "", "syndrome_name": "", "formula_name": "", "herbs": [], "modifications": [], "source": "", "key_points": ""}',
        ]
        prompt = "\n".join(prompt_lines)

        try:
            from m1_engine import M1DiagnosisEngine
            engine = M1DiagnosisEngine()
            raw = engine._call_llm(prompt, temperature=0.1, max_tokens=600)
        except Exception:
            return None

        if not raw:
            return None

        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                result = json.loads(m.group())
            else:
                return None
        except (json.JSONDecodeError, AttributeError):
            return None

        if not result.get("herbs"):
            return None

        # 缓存
        self.case_cache[cache_key] = result
        try:
            with open(self.case_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.case_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        self.case_used_log.append({
            "disease": disease_name,
            "syndrome": syndrome_name,
            "source": result.get("source", "llm_fetched"),
            "fetched_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        })
        try:
            with open(self.case_used_log_path, 'w', encoding='utf-8') as f:
                json.dump(self.case_used_log[-100:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return result

    def _generate_modifications(self, disease_name: str, syndrome_name: str,
                                 base_herbs: list, cases: list,
                                 patient_info: dict) -> list:
        """基于病案参考和患者情况生成加减药物建议

        规则：
        - 加减药物只能在参考病案的 modifications 中选取
        - 总加减药物数 <= 3 味
        - 不能与 base_herbs 重复
        - 根据患者症状匹配加减理由
        """
        if not cases or not self._llm_available():
            return []

        # 收集所有可用的加减候选
        candidates = []
        for case in cases:
            mods = case.get("modifications", []) or []
            for m in mods:
                if isinstance(m, dict):
                    herb = m.get("herb", "")
                    reason = m.get("reason", "")
                    if herb and herb not in base_herbs:
                        candidates.append({"herb": herb, "reason": reason, "source": case.get("source", "")})

        # 去重
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["herb"] not in seen:
                seen.add(c["herb"])
                unique_candidates.append(c)

        # 最多取 3 味
        return unique_candidates[:3]

    # ══════════════════════════════════════════════════════
    #  解析与兜底
    # ══════════════════════════════════════════════════════

    def _parse_llm_result(self, text: str, syndromes: Dict) -> Optional[Dict]:
        """从 LLM 返回文本中解析 JSON"""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return None
        try:
            parsed = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        name = parsed.get("selected_syndrome", {}).get("name", "")
        if name not in syndromes:
            for key, data in syndromes.items():
                trigger = data.get("trigger", "")
                if name in trigger:
                    parsed["selected_syndrome"]["name"] = key
                    name = key
                    break
            else:
                return None

        framework = parsed.get("differentiation_framework", "")
        valid = {"卫气营血", "脏腑", "混合"}
        if framework not in valid:
            parsed["differentiation_framework"] = "脏腑"

        return parsed

    def _load_syndromes(self, primary_disease: str) -> Dict:
        """从知识库加载某个疾病下的所有候选证型"""
        disease_data = self.kb.get(primary_disease)
        if not disease_data:
            return {}
        return disease_data.get("syndromes", {})

    def _llm_available(self) -> bool:
        try:
            from m1_engine import M1DiagnosisEngine
            engine = M1DiagnosisEngine()
            return engine.llm_api_available()
        except Exception:
            return False

    def _call_llm(self, prompt: str) -> Optional[str]:
        try:
            from m1_engine import M1DiagnosisEngine
            engine = M1DiagnosisEngine()
            return engine._call_llm(prompt, temperature=0.1, max_tokens=500)
        except Exception:
            return None

    def _fallback_full(self, primary_disease, syndromes, symptoms) -> Dict:
        """完整兜底：代码匹配 + 病案检索"""
        fallback = self._fallback_parse(syndromes, primary_disease, symptoms)
        if not fallback:
            return {
                "primary_disease": primary_disease,
                "error": "LLM + 代码兜底均无法得出辨证结果",
                "needs_manual_review": True,
            }

        selected_name = fallback["selected_syndrome"]["name"]
        syndrome_data = syndromes.get(selected_name, {})

        # 病案检索
        cases = self._search_cases(primary_disease, selected_name)

        return {
            "primary_disease": primary_disease,
            "syndrome_differentiation": {
                "selected_syndrome": {
                    "name": selected_name,
                    "reason": fallback.get("reasoning", "代码兜底匹配"),
                },
                "differentiation_framework": fallback.get("differentiation_framework", "脏腑"),
            },
            "formula": {
                "name": syndrome_data.get("formula_name", fallback.get("formula", {}).get("name", "")),
                "herbs": syndrome_data.get("herbs", fallback.get("formula", {}).get("herbs", [])),
                "source": "data/m2_formula_knowledge.json （代码兜底）",
            },
            "modifications": [],
            "case_references": cases,
            "needs_manual_review": True,
            "missing_info": [],
        }

    def _fallback_parse(self, syndromes: Dict, primary_disease: str, symptoms: List[str]) -> Optional[Dict]:
        """代码兜底：取第一个匹配 trigger 关键词的证型"""
        if not syndromes:
            return None

        symptoms_lower = [s.lower() for s in (symptoms or [])]
        disease_lower = primary_disease.lower() if primary_disease else ""

        best_score = 0
        best_syndrome = None
        for name, data in syndromes.items():
            trigger = str(data.get("trigger", "")).lower()
            score = 0
            for s in symptoms_lower:
                if s and s in trigger:
                    score += 1
            if disease_lower and disease_lower in trigger:
                score += 2
            if score > best_score:
                best_score = score
                best_syndrome = name

        if not best_syndrome:
            best_syndrome = list(syndromes.keys())[0]

        data = syndromes[best_syndrome]
        formulas = data.get("formulas", [])
        if formulas:
            formula = formulas[0]
            herbs = formula.get("herbs", [])
            if isinstance(herbs, str):
                herbs = [h.strip() for h in herbs.replace("、", ",").split(",") if h.strip()]
            formula_name = formula.get("name_cn", formula.get("name", ""))
        else:
            formula_name = data.get("formula_name", "")
            herbs = data.get("herbs", [])

        return {
            "selected_syndrome": {"name": best_syndrome, "trigger": data.get("trigger", "")},
            "differentiation_framework": data.get("framework", "脏腑辨证"),
            "formula": {"name": formula_name, "herbs": herbs},
            "reasoning": "代码兜底匹配",
        }

# ══════════════════════════════════════════════════════
#  M2+M3 串联全流程
# ══════════════════════════════════════════════════════


    def full_pipeline(self, primary_disease: str, patient_info: dict) -> Dict:
        """M2+M3 串联全流程：辨证 -> 加减 -> M3审核 -> 最终处方
        patient_info 支持字段:
            symptoms, signs, tongue, pulse, cold_heat, stool_urine,
            sleep, appetite, labs, imaging, age, weight,
            pregnancy (bool), lactation (bool)
        """
        if os.getenv("ALLOW_LEGACY_M2_FULL_PIPELINE", "false").lower() != "true":
            return {
                "pipeline": "m2+m3_full",
                "status": "BLOCKED",
                "legacy_prescription_path_blocked": True,
                "candidate_only": True,
                "must_enter_m3": True,
                "trace_closure_required": True,
                "formal_prescription_allowed": False,
                "blocked_reason": [
                    "legacy_full_pipeline_contains_final_prescription_and_dosage",
                    "formal_gate_paused",
                ],
            }
        # ── 1. M2 辨证选方 ──
        m2_result = self.process(
            primary_disease=primary_disease,
            symptoms=patient_info.get("symptoms"),
            signs=patient_info.get("signs"),
            tongue=patient_info.get("tongue", ""),
            pulse=patient_info.get("pulse", ""),
            cold_heat=patient_info.get("cold_heat"),
            stool_urine=patient_info.get("stool_urine"),
            sleep=patient_info.get("sleep"),
            appetite=patient_info.get("appetite"),
            labs=patient_info.get("labs"),
            imaging=patient_info.get("imaging"),
            age=patient_info.get("age", ""),
            weight=patient_info.get("weight", ""),
        )

        if m2_result.get("error"):
            return m2_result

        base_herbs = m2_result["formula"]["herbs"]

        # ── 2. 基于症状 + 药理库进行加减 ──
        modifications = self._generate_evidence_based_modifications(
            base_herbs, patient_info.get("symptoms", []) or [],
        )

        final_herbs = list(base_herbs)
        for m in modifications:
            if m["herb"] not in final_herbs:
                final_herbs.append(m["herb"])

        # ── 3. M3 安全审核 ──
        from m3_engine import M3ClinicalReviewEngine
        reviewer = M3ClinicalReviewEngine()
        m3_result = reviewer.review(final_herbs, {
            "age": patient_info.get("age", 0),
            "weight": patient_info.get("weight", 0),
            "pregnancy": patient_info.get("pregnancy", False),
            "lactation": patient_info.get("lactation", False),
        })

        # ── 4. 提取剂量 ──
        dosages = self._extract_dosages(base_herbs, primary_disease)

        # ── 5. 组装最终处方 ──
        final_herbs_dosage = {}
        for h in base_herbs:
            final_herbs_dosage[h] = dosages.get(h, "常规剂量")
        for m in modifications:
            if m["herb"] not in final_herbs_dosage:
                final_herbs_dosage[m["herb"]] = m.get("dosage", "常规剂量")

        warnings = list(m3_result.get("warnings", []))
        for t in m3_result.get("toxicity_warnings", []):
            note = t.get("note", "")
            if note:
                warnings.append(note)

        return {
            "pipeline": "m2+m3_full",
            "patient": {
                "age": patient_info.get("age", ""),
                "diagnosis": primary_disease,
                "tongue": patient_info.get("tongue", ""),
                "pulse": patient_info.get("pulse", ""),
            },
            "syndrome_differentiation": m2_result.get("syndrome_differentiation", {}),
            "base_formula": {
                "name": m2_result["formula"]["name"],
                "herbs_with_dosage": {h: dosages.get(h, "常规剂量") for h in base_herbs},
                "source": m2_result["formula"]["source"],
            },
            "modifications": modifications,
            "final_prescription": {
                "herbs_with_dosage": final_herbs_dosage,
                "total_herbs": len(final_herbs),
            },
            "m3_review": {
                "eighteen_opposites": m3_result.get("eighteen_opposites", []),
                "nineteen_fears": m3_result.get("nineteen_fears", []),
                "toxicity_warnings": m3_result.get("toxicity_warnings", []),
                "dose_adjustments": m3_result.get("dose_adjustments", []),
                "special_population": m3_result.get("special_population", []),
                "warnings": warnings,
                "review_decision": m3_result.get("review_decision", "APPROVED"),
            },
            "case_references": m2_result.get("case_references", []),
            "missing_info": m2_result.get("missing_info", []),
        }

    def _generate_evidence_based_modifications(self, base_herbs: list, patient_symptoms: list) -> list:
        """基于证据的加减药物生成（症状 -> 药效分类匹配）
        规则：
        - 加减药从预定义的 SYMPTOM_TO_HERB_CANDIDATES 中选取
        - 不重复、不在 base_herbs 中
        - 最多 3 味
        - 不创造新药物
        """
        SYMPTOM_TO_HERB_CANDIDATES = {
            "口干口苦": [
                {"herb": "黄连", "dosage": "6g", "reason": "清胃热、燥湿，对口干口苦、苔黄腻效佳", "class": "清热燥湿药"},
                {"herb": "黄芩", "dosage": "9g", "reason": "清上焦热，助解口干口苦", "class": "清热燥湿药"},
            ],
            "烧心": [
                {"herb": "黄连", "dosage": "6g", "reason": "清胃热，抑制胃酸，缓解烧心", "class": "清热燥湿药"},
                {"herb": "煅瓦楞子", "dosage": "15g(先煎)", "reason": "制酸止痛，缓解烧心", "class": "制酸药"},
            ],
            "大便不畅": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，通腹导滞", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气燥湿，消胀通便", "class": "化湿药"},
            ],
            "排气多": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消胀，减少排气", "class": "理气药"},
                {"herb": "木香", "dosage": "6g", "reason": "行气止痛，调中导滞", "class": "理气药"},
            ],
            "饱胀": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，针对饱胀堵闷", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气除满，消胀", "class": "化湿药"},
                {"herb": "砂仁", "dosage": "6g(后下)", "reason": "化湿开胃，行气宽中", "class": "化湿药"},
            ],
            "堵闷": [
                {"herb": "枳实", "dosage": "10g", "reason": "行气消痞，针对堵闷不适", "class": "理气药"},
                {"herb": "厚朴", "dosage": "10g", "reason": "行气除满", "class": "化湿药"},
            ],
        }

        selected = []
        seen_herbs = set(base_herbs)

        for symptom, candidates in SYMPTOM_TO_HERB_CANDIDATES.items():
            for ps in patient_symptoms:
                if symptom not in ps and ps not in symptom:
                    continue
                for c in candidates:
                    if c["herb"] in seen_herbs:
                        continue
                    seen_herbs.add(c["herb"])
                    selected.append({
                        "herb": c["herb"],
                        "dosage": c.get("dosage", "常规剂量"),
                        "action": "add",
                        "reason": c["reason"],
                        "matched_symptom": symptom,
                    })
                    break  # 每个症状只加一味药
                break

        return selected[:3]

    def _extract_dosages(self, herbs: list, primary_disease: str) -> dict:
        """从知识库 full_decoction 中提取药物剂量"""
        if os.getenv("ALLOW_LEGACY_DOSAGE_EXTRACTION", "false").lower() != "true":
            return {
                "_blocked": True,
                "legacy_prescription_path_blocked": True,
                "formal_prescription_allowed": False,
            }
        dosages = {}
        disease_data = self.kb.get(primary_disease, {})
        if not disease_data:
            return {h: "常规剂量" for h in herbs}

        import re
        for syndrome_data in disease_data.get("syndromes", {}).values():
            full = syndrome_data.get("full_decoction", "")
            if not full:
                continue
            for h in herbs:
                if h in dosages:
                    continue
                m = re.search(re.escape(h) + r"(\\d+[gG])", full)
                if m:
                    dosages[h] = m.group(1)

        for h in herbs:
            if h not in dosages:
                dosages[h] = "常规剂量"
        return dosages

    def _auto_fill_herbs_from_decoction(self):
        """加载知识库时，从 full_decoction 自动补全缺失的药物

        规则：
        - 如果 full_decoction 中存在药物但 herbs 字段缺失，自动补全
        - 使用提取的基名（去炮制前缀）
        - 仅在初始化时执行一次，不修改磁盘文件
        - 防止 future 新增知识库数据时 herbs 字段不全
        """
        import re

        non_herb_keywords = {
            '方剂', '用药', '疗程', '疗效',
            '安全', '警示', '注意', '建议',
            '加减', '预估', '起始', '每日',
            '水炖', '温服', '饭后', '饭前',
            '情况', '缓解', '改善', '消失',
            '恢复', '跟踪', '方法', '用法',
        }

        def _extract(full_text: str) -> list:
            pattern = re.compile(r'([一-鿿]{2,4})(?:\d+\.?\d*[gG])')
            seen = set()
            result = []
            for m in pattern.finditer(full_text):
                herb = m.group(1)
                for prefix in ['麟炒', '醋', '酒', '盐',
                               '蜜', '姜', '炙', '炒',
                               '熅', '熈', '焦', '生']:
                    if herb.startswith(prefix) and len(herb) > len(prefix):
                        herb = herb[len(prefix):]
                        break
                if herb in non_herb_keywords:
                    continue
                if herb not in seen:
                    seen.add(herb)
                    result.append(herb)
            return result

        for disease_data in self.kb.values():
            if not isinstance(disease_data, dict):
                continue
            syndromes = disease_data.get('syndromes', {})
            if not syndromes:
                continue
            for syndrome_data in syndromes.values():
                if not isinstance(syndrome_data, dict):
                    continue
                full = syndrome_data.get('full_decoction', '')
                current = syndrome_data.get('herbs', [])
                if not full or not current:
                    continue
                extracted = _extract(full)
                if len(extracted) > len(current):
                    # 补全：保留已有药物顺序，在后面追加缺失的
                    current_set = set(current)
                    additions = [h for h in extracted if h not in current_set]
                    if additions:
                        syndrome_data['herbs'] = current + additions



    # ══════════════════════════════════════════════════════
    #  命令行入口
    # ══════════════════════════════════════════════════════



if __name__ == "__main__":
    import os
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-e1ec28ff01d946518d033daa91fcd222")

    selector = M2SyndromeSelector()

    # 测试用例：儿童急性扁桃体炎
    result = selector.process(
        primary_disease="儿童急性扁桃体炎",
        symptoms=["发热", "咽喉疼痛", "吞咽不利", "鼻塞流涕"],
        tongue="舌质红，苔薄白",
        pulse="脉浮数",
        cold_heat=["发热", "恶风"],
        age="5岁",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 测试串联流程
    result2 = selector.full_pipeline(
        primary_disease="慢性胃炎 (Chronic Gastritis)",
        patient_info={
            "symptoms": ["胃脘不适", "剑突处饱胀感", "堵闷不适", "酒后烧心", "排气多", "口干口苦", "大便不畅"],
            "tongue": "舌红，苔黄腻偏黑",
            "pulse": "",
            "cold_heat": ["无寒热"],
            "stool_urine": ["大便不畅"],
            "age": 51,
            "weight": 70,
            "pregnancy": False,
            "lactation": False,
        }
    )
    print(json.dumps(result2, ensure_ascii=False, indent=2))
