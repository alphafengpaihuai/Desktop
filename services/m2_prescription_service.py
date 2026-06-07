"""
m2_prescription_service.py
==========================
[DEPRECATED] 守一 CDSS — M2 处方生成层（V3）

此文件已被 M2SyndromeSelector (m2_engine.py) 取代。
M2SyndromeSelector 已提供所有 M2-1/M2-2/M2-3 功能，
且本服务读取桌面 xlsx 路径、生成 final_prescription 等行为
与 M2 Skill v1.0 spec 冲突。

保留原因：
  - full_pipeline.py (legacy) 仍引用本服务
  - test_m3_default_keep.py 引用 HerbCandidate
  - 供历史审计参考

DEPRECATED since 2026-06 — 不会再有功能更新。
"""

import json
import os
import re
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class HerbCandidate:
    """单味药候选"""
    name: str
    dosage: str = ""
    source: str = ""
    reason: str = ""
    evidence_detail: str = ""
    contraindications: str = ""
    herb_type: str = "base"


@dataclass
class FormulaPrescription:
    """处方"""
    formula_name: str = ""
    base_herbs: List[HerbCandidate] = field(default_factory=list)
    add_herbs: List[HerbCandidate] = field(default_factory=list)
    reference_cases: List[dict] = field(default_factory=list)
    pharmacology_candidates: List[dict] = field(default_factory=list)
    evidence_hints: List[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)


@dataclass
class SimilarCase:
    """相似案例"""
    index: int
    medical_text: str
    illness_name: str
    syndrome: str
    formula_name: str
    formula_composition: str
    age: str
    gender: str
    similarity_score: float
    matched_keywords: List[str] = field(default_factory=list)
    extracted_herbs: List[str] = field(default_factory=list)


# ── 常用方剂中的草药（用于从案例文本提取草药名） ──
COMMON_HERBS = {
    '人参', '三七', '干姜', '大黄', '大枣', '川芎', '川楝子', '川牛膝',
    '女贞子', '山药', '山楂', '山茱萸', '丹参', '乌药', '五味子',
    '天冬', '天麻', '天竺黄', '木瓜', '木香', '车前子',
    '牛膝', '升麻', '太子参', '巴戟天', '火麻仁', '牛黄', '王不留行',
    '生姜', '生地黄', '生石膏', '生甘草', '白芍', '白芷', '白蒺藜',
    '白鲜皮', '白术', '白茅根', '石决明', '石菖蒲', '龙胆草',
    '地龙', '地骨皮', '地榆', '百合', '百部', '当归', '肉苁蓉',
    '肉桂', '防风', '防己', '赤芍', '赤石脂', '车前草', '郁金',
    '延胡索', '何首乌', '制何首乌', '制川乌', '制附片',
    '吴茱萸', '牡丹皮', '牡蛎', '羌活', '芦根', '芒硝', '苍术',
    '厚朴', '决明子', '沉香', '砂仁', '红花', '红参', '胡麻仁',
    '苦参', '苦杏仁', '茯苓', '茯神', '茵陈', '荆芥', '草果',
    '荔枝核', '钩藤', '香附', '鬼箭羽', '党参', '夏枯草', '柴胡',
    '桂枝', '桃仁', '桑叶', '桑枝', '桑寄生', '桑椹', '桔梗',
    '浙贝母', '益母草', '益智仁', '秦艽', '荷叶', '菟丝子', '黄芩',
    '黄连', '黄芪', '黄柏', '栀子', '猪苓', '麻黄',
    '旋覆花', '淫羊藿', '细辛', '菊花', '蝉蜕', '僵蚕',
    '全蝎', '蜈蚣', '地鳖虫', '水蛭', '银杏叶', '远志', '酸枣仁',
    '柏子仁', '合欢皮', '合欢花', '夜交藤', '龙骨', '龟甲', '鳖甲',
    '珍珠母', '磁石', '琥珀', '甘草', '炙甘草', '炙黄芪', '陈皮',
    '法半夏', '姜半夏', '清半夏', '胆南星', '竹茹', '竹沥', '枳实',
    '枳壳', '青皮', '大腹皮', '槟榔', '莱菔子', '神曲', '麦芽',
    '谷芽', '鸡内金', '焦山楂', '焦神曲', '焦麦芽', '莪术', '三棱',
    '阿胶', '熟地黄', '麦冬', '天冬', '玉竹', '沙参', '石斛',
    '枸杞子', '墨旱莲', '黑芝麻', '核桃仁', '蛤蚧',
    '杜仲', '续断', '狗脊', '骨碎补', '补骨脂', '锁阳',
    '沙苑子', '蛇床子', '覆盆子', '金樱子', '莲子',
    '芡实', '乌梅', '诃子', '肉豆蔻', '白豆蔻', '草豆蔻',
    '香橼', '佛手', '玫瑰花', '月季花', '凌霄花', '鸡血藤',
    '忍冬藤', '络石藤', '青风藤', '海风藤', '威灵仙', '独活',
    '藁本', '蔓荆子', '苍耳子', '辛夷', '薄荷', '牛蒡子',
    '桑叶', '葛根', '淡豆豉', '浮萍', '龙骨', '牡蛎', '龟板',
}


class M2PrescriptionService:
    """M2 处方生成服务"""

    FORMULA_KNOWLEDGE_PATH = "data/m2_formula_knowledge.json"

    def __init__(self, cases_path: str = None, formula_knowledge_path: str = None):
        if cases_path is None:
            cases_path = '/Users/fangxuan/Documents/去重后案例库.xlsx'
        self.cases_path = cases_path
        self.cases_df = None
        self._formula_knowledge = None
        # 优先使用桌面知识库 xlsx（含完整别名和方剂）
        if formula_knowledge_path is None:
            desktop_xlsx = '/Users/fangxuan/Desktop/知识库备份/口服方剂知识库.xlsx'
            if os.path.exists(desktop_xlsx):
                self.formula_knowledge_path = desktop_xlsx
            else:
                self.formula_knowledge_path = 'data/m2_formula_knowledge.json'
        else:
            self.formula_knowledge_path = formula_knowledge_path
        self._load_cases()

    # ── 知识库加载 ──

    def _get_formula_knowledge(self) -> dict:
        """从口服方剂知识库加载（优先桌面 xlsx，回退 JSON）"""
        if self._formula_knowledge is not None:
            return self._formula_knowledge
        try:
            if self.formula_knowledge_path.endswith('.xlsx'):
                # 从桌面 xlsx 读取（含完整别名和方剂）
                import pandas as pd
                df = pd.read_excel(self.formula_knowledge_path)
                valid = df[df['病名【诊断】'].notna()].copy()
                self._formula_knowledge = {}
                for _, row in valid.iterrows():
                    disease_block = str(row['病名【诊断】'])
                    syndrome_text = str(row['中医证型 <辨证分型>']) if pd.notna(row['中医证型 <辨证分型>']) else ''
                    formula_text = str(row['汤剂方案']) if pd.notna(row['汤剂方案']) else ''
                    
                    disease_names = self._extract_disease_names(disease_block)
                    if not disease_names:
                        continue
                    
                    formula_info = self._extract_formula_from_text(formula_text)
                    if not formula_info[0] and not formula_info[1]:
                        continue
                    
                    syndrome_name = self._extract_syndrome_name(syndrome_text)
                    
                    for dn in disease_names:
                        if dn not in self._formula_knowledge:
                            self._formula_knowledge[dn] = {
                                'disease_name': dn,
                                'syndromes': {},
                                '_source': self.formula_knowledge_path,
                            }
                        if syndrome_name:
                            sid = syndrome_name
                            if sid not in self._formula_knowledge[dn]['syndromes']:
                                self._formula_knowledge[dn]['syndromes'][sid] = {
                                    'syndrome_name': syndrome_name,
                                    'formula_name': formula_info[0],
                                    'herbs': formula_info[1],
                                }
                print(f"[M2] ✅ 加载知识库: {len(self._formula_knowledge)} 个病名（来自桌面 xlsx）")
            else:
                with open(self.formula_knowledge_path, 'r', encoding='utf-8') as f:
                    self._formula_knowledge = json.load(f)
                print(f"[M2] ✅ 加载知识库: {len(self._formula_knowledge)} 个病名（来自 JSON）")
        except Exception as e:
            print(f"[M2] ⚠ 知识库加载失败: {e}，回退空库")
            self._formula_knowledge = {}
        return self._formula_knowledge

    @staticmethod
    def _extract_disease_names(disease_block: str) -> List[str]:
        """从知识库病名字段提取所有病名（主病名+别名）"""
        names = []
        for line in disease_block.split('\n'):
            line = line.strip()
            for prefix in ['主病名：', '主病名:', '-主病名：', '-主病名:', '**主病名：', '**主病名:']:
                if prefix in line:
                    name = line.split(prefix)[-1].split('*')[0].strip().rstrip('*').strip()
                    if name and name not in names:
                        names.append(name)
                    break
        for line in disease_block.split('\n'):
            line = line.strip()
            if '别名' in line and ('：' in line or ':' in line):
                sep = '：' if '：' in line else ':'
                alias_part = line.split(sep)[-1]
                for alias in alias_part.replace('，', ',').replace('、', ',').split(','):
                    alias = alias.strip().rstrip('*').strip()
                    if alias and len(alias) >= 2 and alias not in names:
                        names.append(alias)
        return list(set(names))

    @staticmethod
    def _extract_syndrome_name(syndrome_text: str) -> str:
        """从证型文本提取证型名（<...>中的内容）"""
        import re
        m = re.search(r'<([^>]+)>', syndrome_text)
        if m:
            return m.group(1).strip()
        first_line = syndrome_text.split('\n')[0].strip()
        if first_line and len(first_line) <= 20:
            return first_line
        return ''

    @staticmethod
    def _extract_formula_from_text(formula_text: str) -> Tuple[str, List[str]]:
        """从汤剂方案提取方剂名和草药"""
        import re
        if not formula_text or formula_text in ('nan', '', '无'):
            return '', []
        
        formula_name = ''
        m = re.search(r'方剂[：:]\s*【?([^】\n]+)】?', formula_text)
        if m:
            formula_name = m.group(1).strip()
        if not formula_name:
            m = re.search(r'【([^】]+)】', formula_text)
            if m:
                formula_name = m.group(1).strip()
        
        herb_text = ''
        for pattern in ['用药[：:].*', '\*\*用药[：:]\*\*.*']:
            m = re.search(pattern, formula_text)
            if m:
                herb_text = m.group()
                break
        if not herb_text:
            if formula_name:
                parts = formula_text.split(formula_name, 1)
                herb_text = parts[1] if len(parts) > 1 else formula_text
            else:
                herb_text = formula_text
        
        from .m2_prescription_service import COMMON_HERBS
        herbs = []
        for name in re.findall(r'([一-鿿]{2,4})', herb_text):
            exclude = {'方剂', '用药', '疗程', '起始连用', '每日',
                       '水煮分次温服', '疗效预估', '安全警示',
                       '孼幼儿', '过敏体质', '脾胃虚寒',
                       '孕妇', '哺乳期', '方药', '处方', '加减'}
            if name not in exclude and len(name) >= 2 and name in COMMON_HERBS:
                if name not in herbs:
                    herbs.append(name)
        
        return formula_name, herbs

    def _load_cases(self):
        try:
            self.cases_df = pd.read_excel(self.cases_path)
            print(f"[M2] ✅ 加载案例库: {len(self.cases_df)} 条")
        except Exception as e:
            print(f"[M2] ❌ 加载案例库失败: {e}")
            self.cases_df = pd.DataFrame()

    # ── 案例检索（V3 规则） ──

    def find_similar_cases(self, disease_name: str, symptoms: List[str], top_k: int = 3) -> List[SimilarCase]:
        """
        检索规则：
          1. 病名匹配 tcmMedicineDiagnose / wcmMedicineDiagnose / illnessName
          2. 证型匹配 tcmSyndromeTypeDiagnose（有则加分）
          3. 不匹配症状
        """
        if self.cases_df is None or len(self.cases_df) == 0:
            return []

        disease_keywords = self._extract_disease_keywords(disease_name)
        syndrome_keywords = self._extract_syndrome_from_symptoms(symptoms)

        # 病名匹配
        mask = pd.Series([False] * len(self.cases_df))
        for kw in disease_keywords:
            if len(kw) < 2:
                continue
            if 'tcmMedicineDiagnose' in self.cases_df.columns:
                mask |= self.cases_df['tcmMedicineDiagnose'].astype(str).str.contains(kw, na=False, regex=False)
            if 'wcmMedicineDiagnose' in self.cases_df.columns:
                mask |= self.cases_df['wcmMedicineDiagnose'].astype(str).str.contains(kw, na=False, regex=False)
            if 'illnessName' in self.cases_df.columns:
                mask |= self.cases_df['illnessName'].astype(str).str.contains(kw, na=False, regex=False)

        candidates = self.cases_df[mask].copy()
        if len(candidates) == 0:
            return []

        candidates = candidates.drop_duplicates(subset=['id'])

        # 评分
        scored = []
        for idx, row in candidates.iterrows():
            tcm_diag = str(row.get('tcmMedicineDiagnose', ''))
            wcm_diag = str(row.get('wcmMedicineDiagnose', ''))
            illness = str(row.get('illnessName', ''))
            syndrome_text = str(row.get('tcmSyndromeTypeDiagnose', ''))
            formula_text = str(row.get('formulaComposition', ''))
            formula_name = str(row.get('formulaName', ''))

            score = 0.0
            matched_kw = []

            for kw in disease_keywords:
                if kw in tcm_diag:
                    score += 5.0
                    matched_kw.append(f"中医诊断:{kw}")
                if kw in wcm_diag:
                    score += 4.0
                    matched_kw.append(f"西医诊断:{kw}")
                if kw in illness:
                    score += 3.0
                    matched_kw.append(f"病名:{kw}")

            if syndrome_keywords:
                for sk in syndrome_keywords:
                    if sk in syndrome_text:
                        score += 2.0
                        matched_kw.append(f"证型:{sk}")

            if formula_name and formula_name != 'nan':
                score += 1.0
            if formula_text and formula_text.strip() and formula_text != 'nan':
                score += 1.0

            if score > 0.5:
                extracted = self._extract_herbs_from_case(formula_text)
                case = SimilarCase(
                    index=idx,
                    medical_text=str(row.get('medicalText', ''))[:300],
                    illness_name=illness,
                    syndrome=syndrome_text,
                    formula_name=formula_name,
                    formula_composition=formula_text,
                    age=str(row.get('patientAge', '')),
                    gender=str(row.get('patientGender', '')),
                    similarity_score=round(score, 2),
                    matched_keywords=matched_kw[:6],
                    extracted_herbs=extracted,
                )
                scored.append(case)

        scored.sort(key=lambda c: c.similarity_score, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _extract_syndrome_from_symptoms(symptoms: List[str]) -> List[str]:
        SYNDROME_KEYWORDS = [
            "血热风燥", "肝肾不足", "气血两虚", "气滞血瘀", "血虚风燥",
            "脾胃湿热", "湿热蕴结", "肝郁化火", "肝郁气滞", "阴虚火旺",
            "心脾两虚", "心肾不交", "痰热扰心", "瘀血阻络", "湿热下注",
            "寒湿困脾", "湿热中阻", "食滞胃肠", "热毒壅盛", "热入营血",
            "风寒束表", "风热犯表", "阳虚水泛", "肝阳上亢", "痰浊内阻",
            "心血瘀阻", "寒凝心脉", "痰浊化热", "心肾阳虚", "肺脾气虚",
            "肺肾阴虚", "脾肾阳虚", "气虚痰恋",
        ]
        symptom_text = " ".join(symptoms)
        return [sk for sk in SYNDROME_KEYWORDS if sk in symptom_text]

    @staticmethod
    def _extract_herbs_from_case(formula_text: str) -> List[str]:
        if not formula_text or formula_text == 'nan':
            return []
        parts = re.split(r'[,，、；;]', formula_text)
        herbs = []
        for p in parts:
            p = p.strip()
            m = re.match(r'^([\u4e00-\u9fff]{2,4})', p)
            if m:
                name = m.group(1)
                exclude = {'方药', '处方', '方剂', '加减', '治疗', '诊断', '患者',
                          '每日', '服用', '口服', '煎服', '冲服', '加入', '先煎',
                          '后下', '包煎', '烊化', '冲服', '分服', '顿服'}
                if name not in exclude and len(name) >= 2 and name in COMMON_HERBS:
                    herbs.append(name)
        return list(set(herbs))[:15]

    # ── 知识库配方查询 ──

    def get_knowledge_formula(self, disease_name: str, alternate_names: List[str] = None) -> dict:
        """从口服方剂知识库中查询（支持别名链和多源搜索）
        
        Args:
            disease_name: 主查询名（可能为英文或中文）
            alternate_names: 备选名列表，来自 M1 匹配的中文名/别名
        """
        knowledge = self._get_formula_knowledge()

        # 构建搜索名列表
        search_names = [disease_name]
        if alternate_names:
            for n in alternate_names:
                if n and n not in search_names:
                    search_names.append(n)

        # 1. 精确匹配
        for name in search_names:
            if name in knowledge:
                return knowledge[name]

        # 2. 模糊匹配（双向包含，避免过短匹配）
        for name in search_names:
            for key, val in knowledge.items():
                if (key in name or name in key) and len(key) >= 2 and len(name) >= 2:
                    # 长度比例不要太悬殊（避免 "发" in "脱发" -> 误匹配）
                    if min(len(key), len(name)) / max(len(key), len(name)) >= 0.3:
                        return val

        # 3. 通过 m1_name_mapping.json 反查中文名
        import os
        mapping_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'm1_name_mapping.json')
        if os.path.exists(mapping_path):
            with open(mapping_path, 'r', encoding='utf-8') as f:
                cn_to_en = json.load(f)
            for cn, en in cn_to_en.items():
                if cn in search_names or en.lower() in [s.lower() for s in search_names]:
                    for key, val in knowledge.items():
                        if cn in key or key in cn or en.lower() in key.lower():
                            return val

        # 4. 静态别名映射表
        ALIAS_MAP = {
            "脱发": "斑秃",
            "脂溢性脱发": "雄激素性脱发",
            "冠心病": "冠状动脉粥样硬化性心脏病",
            "外眼睑炎": "睑腺炎 (Hordeolum)",
            "外睑腺炎": "睑腺炎 (Hordeolum)",
            "麦粒肿": "睑腺炎 (Hordeolum)",
            "睑腺炎": "睑腺炎 (Hordeolum)",
            "眼睑炎": "睑腺炎 (Hordeolum)",
            "过敏性结膜炎": "过敏性结膜炎 (Allergic Conjunctivitis)",
            "结膜炎": "急性细菌性结膜炎 (Acute Bacterial Conjunctivitis)",
            "Acute Tonsillitis": ["急淋性扁桃体炎", "急性扁桃体炎", "儿童急性扁桃体炎", "急性化脓性扁桃体炎"],
            "急性扁桃体炎": ["儿童急性扁桃体炎", "急性化脓性扁桃体炎", "Acute Tonsillitis"],
            "感染性发热": ["急性上呼吸道感染", "急性支气管炎", "感染相关性发热"],
            "感冒": ["急性上呼吸道感染"],
            "上呼吸道感染": ["急性上呼吸道感染", "感冒"],
        }
        for name in search_names:
            if name in ALIAS_MAP:
                aliases = ALIAS_MAP[name]
                if isinstance(aliases, str):
                    aliases = [aliases]
                for alias in aliases:
                    if alias in knowledge:
                        return knowledge[alias]
                    for key, val in knowledge.items():
                        if alias in key or key in alias:
                            return val

        return {}

    # ── 处方生成 ──

    def generate_prescription(
        self,
        disease_name: str,
        symptoms: List[str],
        current_syndrome: str = "",
        pharmacology_candidates: List[dict] = None,
        pharmacology_hints: List[dict] = None,
    ) -> FormulaPrescription:
        if pharmacology_candidates is None:
            pharmacology_candidates = []
        if pharmacology_hints is None:
            pharmacology_hints = []

        result = FormulaPrescription()

        # Step 1: 知识库主方
        knowledge = self.get_knowledge_formula(disease_name)

        if knowledge and knowledge.get("syndromes"):
            syndromes = knowledge["syndromes"]
            matched_syndrome = None
            if current_syndrome:
                for sn, sv in syndromes.items():
                    if sn in current_syndrome or current_syndrome in sn:
                        matched_syndrome = sv
                        result.formula_name = sv["formula_name"]
                        break
            if not matched_syndrome:
                matched_syndrome = list(syndromes.values())[0]
                result.formula_name = matched_syndrome["formula_name"]

            base_herb_names = matched_syndrome.get("herbs", [])
            result.base_herbs = [
                HerbCandidate(name=h, dosage="", source="oral_knowledge_base",
                              reason=f"知识库方剂: {result.formula_name}",
                              herb_type="base")
                for h in base_herb_names
            ]

            result.trace["knowledge_matched"] = True
            result.trace["knowledge_disease"] = knowledge.get("disease_name", disease_name)
            result.trace["knowledge_syndrome"] = matched_syndrome.get("syndrome_name", "")
            result.trace["knowledge_formula"] = result.formula_name
        else:
            result.trace["knowledge_matched"] = False
            result.trace["knowledge_note"] = f"知识库未找到病名: {disease_name}"

        # Step 2: 相似案例检索（V3规则）
        similar_cases = self.find_similar_cases(disease_name, symptoms, top_k=3)
        result.reference_cases = [
            {
                "index": c.index,
                "reference_only": True,
                "case_herbs_used_in_prescription": False,
                "illness_name": c.illness_name,
                "syndrome": c.syndrome,
                "formula_name": c.formula_name,
                "formula_composition": c.formula_composition,
                "similarity_score": c.similarity_score,
                "matched_keywords": c.matched_keywords,
                "extracted_herbs": c.extracted_herbs,
                "text_preview": c.medical_text[:200],
            }
            for c in similar_cases
        ]

        result.pharmacology_candidates = pharmacology_candidates
        result.evidence_hints = pharmacology_hints
        result.trace.update({
            "candidate_only": True,
            "base_herbs_count": len(result.base_herbs),
            "add_herbs_count": len(result.add_herbs),
            "similar_cases_count": len(result.reference_cases),
            "pharmacology_candidates_count": len(pharmacology_candidates),
        })

        return result

    @staticmethod
    def _extract_disease_keywords(disease_name: str) -> List[str]:
        keywords = [disease_name]
        for sep in ['(', '（']:
            if sep in disease_name:
                keywords.append(disease_name.split(sep)[0].strip())
        ALIAS_MAP = {
            "斑秃": ["斑秃", "脱发", "油风"],
            "脱发": ["斑秃", "脱发"],
            "雄激素性脱发": ["雄激素", "脂溢性脱发", "脱发"],
            "冠心病": ["冠状动脉", "冠心病", "胸痹"],
            "失眠": ["失眠", "不寐"],
            "痤疮": ["痤疮", "粉刺"],
            "便秘": ["便秘"],
        }
        if disease_name in ALIAS_MAP:
            keywords.extend(ALIAS_MAP[disease_name])
        return list(set(k for k in keywords if len(k) >= 2))


def get_m2_prescription_service(cases_path: str = None) -> M2PrescriptionService:
    return M2PrescriptionService(cases_path=cases_path)
