#!/usr/bin/env python3
"""从「口服方剂知识库_最终版_原始版面_仅总表.xlsx」批量导入知识库到 data/m2_formula_knowledge.json

用法：
    PYTHONPATH=. python3 scripts/import_oral_formula_master_table.py

数据来源列映射：
    Col 3 (C):  病名【诊断】
    Col 6 (F):  中医证型 <辨证分型>
    Col 7 (G):  基础方剂（含加减触发条件）
    Col 8 (H):  汤剂方案（含方剂名、药物、剂量、疗程）
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"

# 源文件搜索路径（按优先级）
CANDIDATE_PATHS = [
    Path.home() / "Desktop" / "口服方剂知识库_最终版_原始版面_仅总表.xlsx",
    Path.home() / "Desktop" / "知识库备份" / "口服方剂知识库_最终版_原始版面_仅总表.xlsx",
    Path.home() / "Desktop" / "知识库备份" / "口服方剂知识库.xlsx",
]

TARGET_KB = DATA_DIR / "m2_formula_knowledge.json"
BACKUP_DIR = DATA_DIR / "backup"
DEFAULT_BACKUP_NAME = "m2_formula_knowledge_{timestamp}.json"


# ══════════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════════

stats = {
    "total_rows_read": 0,
    "rows_skipped_no_disease": 0,
    "rows_skipped_no_formula": 0,
    "diseases_parsed": 0,
    "syndromes_parsed": 0,
    "modifications_parsed": 0,
    "new_disease_keys": 0,
    "merged_disease_keys": 0,
    "errors": [],
    "missing_keys_log": [],
}


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def find_source_file() -> Path:
    for p in CANDIDATE_PATHS:
        if p.exists():
            print(f"[FOUND] 源文件: {p} ({p.stat().st_size / 1024:.0f} KB)")
            return p
    # 递归搜索桌面
    desktop = Path.home() / "Desktop"
    for f in desktop.rglob("*口服*方剂*总表*"):
        if f.suffix.lower() in (".xlsx", ".csv", ".json", ".txt"):
            print(f"[FOUND] 源文件(递归搜索): {f} ({f.stat().st_size / 1024:.0f} KB)")
            return f
    raise FileNotFoundError(
        f"未找到「口服方剂知识库_最终版_原始版面_仅总表.xlsx」。\n"
        f"请将文件放在桌面或桌面/知识库备份/目录下。\n"
        f"搜索路径: {[str(p) for p in CANDIDATE_PATHS]}"
    )


def parse_disease_name(raw: str) -> str:
    """从各种格式中提取干净的病名"""
    if not raw or not isinstance(raw, str):
        return ""
    raw = raw.strip()
    # 格式1: **主病名： XXX**\n*   别名：... (带**粗体标记)
    m = re.search(r'\*\*主病名[：:]\s*(.+?)(?:\*\*)', raw)
    if m:
        name = m.group(1).strip()
        name = re.sub(r'\s*\*+$', '', name).strip()
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()  # 去除末尾英文
        return name
    # 格式2: 主病名： XXX\n   别名：... (无粗体)
    m = re.search(r'(?:^|\n)主病名[：:]\s*(.+?)(?:\n|$)', raw)
    if m:
        name = m.group(1).strip()
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        return name
    # 格式3: - 主病名: XXX\n   别名: (连字符格式)
    m = re.search(r'-\s*主病名[：:]\s*(.+?)(?:\n|$)', raw)
    if m:
        name = m.group(1).strip()
        name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        return name
    # 格式4: 简单的多行（可能隐含多个疾病名，只取第一个）
    # 如果包含换行和'主病名'，拆开处理
    if '\n' in raw and '主病名' in raw:
        for line in raw.split('\n'):
            line = line.strip()
            if line and '主病名' not in line and '别名' not in line and not line.startswith('-') and not line.startswith('*'):
                return normalize_disease_name(line)
    # 格式5: 简单病名
    name = raw
    # 清理常见后缀：去除末尾的英文括号内容
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
    # 清理不要的标记
    name = re.sub(r'\s*\*+$', '', name).strip()
    return name


def parse_syndrome_name(raw: str) -> str:
    """从 <证型名>： 描述... 中提取证型名"""
    if not raw or not isinstance(raw, str):
        return "", raw or ""
    raw = raw.strip()
    m = re.search(r'<(.+?)>', raw)
    if m:
        name = m.group(1).strip()
        # 提取描述文字（去除 <证型名> 部分）
        desc = re.sub(r'<.*?>\s*[：:]\s*', '', raw, count=1).strip()
        # 如果 description 为空，用剩余部分
        if not desc:
            desc = re.sub(r'<.*?>', '', raw, count=1).strip().lstrip('：:').strip()
        return name, desc
    # 没有 <> 格式，用整段文字的前20字作为证型名，全部作为描述
    short = raw[:30].rstrip('，。；').strip()
    return short, raw


def parse_herbs_from_decoction(decoction_text: str) -> list:
    """从汤剂方案中提取药物列表"""
    if not decoction_text or not isinstance(decoction_text, str):
        return []
    herbs = []
    text = decoction_text.strip()
    # 查找 **用药：** 或 **用药： 后面的内容（可能包含闭合的 **）
    m = re.search(r'\*{0,2}用药\*{0,2}[：:]\s*(?:\*{0,2})?(.*?)(?:\*\*[疗程疗效安全]|$)', text, re.DOTALL)
    if m:
        herb_section = m.group(1)
    else:
        herb_section = text
    # 以空格/逗号/顿号分割
    parts = re.split(r'[,\s，、]+', herb_section)
    for part in parts:
        part = part.strip().lstrip('*').strip()
        if not part:
            continue
        # 匹配 "中药名 剂量" 或 "中药名剂量"
        hm = re.match(r'([\u4e00-\u9fff]{2,5})\s*(\d+[gG](?:\([^)]*\))?)', part)
        if hm:
            herb_name = hm.group(1).strip()
            if herb_name and herb_name not in herbs:
                herbs.append(herb_name)
    # 如果没用剂量匹配，尝试只提取中文药名（可能是修改行）
    if not herbs:
        for token in parts:
            token = token.strip().lstrip('+').strip()
            hm = re.match(r'^([\u4e00-\u9fff]{2,4})$', token)
            if hm:
                herb_name = hm.group(1).strip()
                if herb_name and herb_name not in herbs:
                    herbs.append(herb_name)
            else:
                hm2 = re.match(r'([\u4e00-\u9fff]{2,4})\s*(\d+[gG])', token)
                if hm2:
                    herb_name = hm2.group(1).strip()
                    if herb_name and herb_name not in herbs:
                        herbs.append(herb_name)
    return herbs


def parse_formula_name(decoction_text: str) -> str:
    """从汤剂方案中提取方剂名"""
    if not decoction_text or not isinstance(decoction_text, str):
        return ""
    # **方剂：【麻黄汤】** 或 方剂：【麻黄汤】 或 **方剂：** 【麻杏石甘汤】
    m = re.search(r'\*{0,2}方剂\*{0,2}[：:]\s*(?:\*{0,2}\s*)?【(.+?)】', decoction_text)
    if m:
        return m.group(1)
    # 尝试格式化文本中直接提取
    m = re.search(r'方剂[：:]\s*(?:\*{0,2}\s*)?【(.+?)】', decoction_text)
    if m:
        return m.group(1)
    # 尝试方名： 【XXX】
    m = re.search(r'【(.+?)】', decoction_text)
    if m:
        name = m.group(1).strip()
        # 过滤掉副作用标题
        if '核心加减' not in name and '成药' not in name and '加减' not in name and len(name) <= 20:
            return name
    return ""


def parse_dosage_text(decoction_text: str) -> str:
    """提取完整剂量字符串"""
    if not decoction_text or not isinstance(decoction_text, str):
        return ""
    # 提取 **用药：** 之后到下一个 ** 之前的内容
    m = re.search(r'\*{0,2}用药\*{0,2}[：:]\s*(?:\*{0,2})?(.*?)(?=\*\*|$)', decoction_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def extract_tongue_pulse_from_description(desc: str) -> dict:
    """从辨证描述中提取舌象和脉象"""
    tongue = ""
    pulse = ""
    if not desc:
        return {"tongue": "", "pulse": ""}
    # 寻找 "舌..." 模式
    tm = re.search(r'(舌[^。，；]*?)(?=[。，；]|\s*脉)', desc)
    if tm:
        tongue = tm.group(1).strip()
    # 如果没有找到标准模式，试试更宽松的
    if not tongue:
        tm2 = re.search(r'(舌质[^。，；]*?|舌[^。，；]*?苔[^。，；]*)', desc)
        if tm2:
            tongue = tm2.group(1).strip()
    # 脉象
    pm = re.search(r'(脉[^。，；\n]*?)(?=[。，；\n]|$)', desc)
    if pm:
        pulse = pm.group(1).strip()
    return {"tongue": tongue, "pulse": pulse}


def extract_symptoms_from_description(desc: str) -> list:
    """从辨证描述中提取症状关键词列表（排除舌脉部分）"""
    if not desc:
        return []
    # 去除舌脉部分
    text = re.sub(r'(舌[^。，；]*?)[。，；]', '', desc)
    text = re.sub(r'(脉[^。，；\n]*?)(?=[。，；\n]|$)', '', text)
    # 分割症状
    parts = re.split(r'[，,；;。]', text)
    symptoms = []
    for p in parts:
        p = p.strip()
        if p and len(p) >= 2:
            symptoms.append(p)
    return symptoms[:15]


def extract_pathology_from_description(desc: str) -> str:
    """从辨证描述中提取西医病理/病机关键词"""
    if not desc:
        return ""
    keywords = []
    for kw in ["热", "寒", "痰", "湿", "瘀", "风", "虚", "实", "气滞", "血瘀",
               "阴虚", "阳虚", "气虚", "血虚", "湿热", "寒湿", "风热", "风寒",
               "痰热", "痰湿", "瘀热", "肝火", "心火", "胃火"]:
        if kw in desc:
            keywords.append(kw)
    return "、".join(set(keywords[:6]))


def normalize_disease_name(name: str) -> str:
    """标准化病名：去除首尾空格/括号/多余信息"""
    name = name.strip().rstrip('）)').strip()
    return name


# ══════════════════════════════════════════════════════════════
# 主导入函数
# ══════════════════════════════════════════════════════════════

def import_master_table(source_path: Path) -> dict:
    """读取总表并转换为 m2_formula_knowledge.json 结构"""
    import openpyxl

    print(f"[LOAD] 正在读取 {source_path} ...")
    wb = openpyxl.load_workbook(str(source_path), data_only=True)
    ws = wb['总表']
    total_rows = ws.max_row
    print(f"[LOAD] 总行数: {total_rows}")

    # 已加载的 KB
    if TARGET_KB.exists():
        with open(TARGET_KB, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        print(f"[LOAD] 现有知识库: {len(existing)} 个疾病")
    else:
        existing = {}
        print("[LOAD] 新建知识库")

    result = dict(existing)  # 复制，保留已有
    stats["total_rows_read"] = total_rows

    # 遍历行
    current_disease = ""
    current_syndrome = ""
    current_syndrome_desc = ""
    current_modifications = []

    for i in range(3, total_rows + 1):  # 从第3行开始（行1=header, 行2=科目头）
        col3 = ws.cell(i, 3).value  # 病名
        col6 = ws.cell(i, 6).value  # 证型
        col7 = ws.cell(i, 7).value  # 基础方剂/加减
        col8 = ws.cell(i, 8).value  # 汤剂方案

        c3 = str(col3).strip() if col3 else ""
        c6 = str(col6).strip() if col6 else ""
        c7 = str(col7).strip() if col7 else ""
        c8 = str(col8).strip() if col8 else ""

        # ── 新疾病行 ──
        if c3 and not c6:
            # 保存上一个疾病的最后一个证型
            if current_disease and current_syndrome:
                _finalize_syndrome(result, current_disease, current_syndrome,
                                   current_syndrome_desc, current_modifications)

            disease = parse_disease_name(c3)
            if not disease:
                stats["rows_skipped_no_disease"] += 1
                continue

            current_disease = normalize_disease_name(disease)
            current_syndrome = ""
            current_syndrome_desc = ""
            current_modifications = []
            stats["diseases_parsed"] += 1

            if current_disease in result and result[current_disease].get("syndromes"):
                # 纯新增验证 — 标记但不额外计数（_ensure_syndrome 负责合并）
                pass
            elif current_disease in result:
                # 存在但无 syndromes（空架子）
                stats["merged_disease_keys"] += 1
                result[current_disease]["syndromes"] = {}
            else:
                stats["new_disease_keys"] += 1
                result[current_disease] = {
                    "disease_name": current_disease,
                    "syndromes": {},
                }

        # ── 新证型行（有 <> 证型名） ──
        elif c6 and ('<' in c6 or '：' in c6 or ':' in c6 or '证' in c6 or '型' in c6):
            # 保存上一个证型
            if current_disease and current_syndrome:
                _finalize_syndrome(result, current_disease, current_syndrome,
                                   current_syndrome_desc, current_modifications)

            # 如果 c3 也有内容，说明是疾病+证型在一行（没分割好）
            if c3:
                disease = parse_disease_name(c3)
                if disease:
                    disease = normalize_disease_name(disease)
                    if disease != current_disease:
                        current_disease = disease
                        if current_disease not in result:
                            stats["new_disease_keys"] += 1
                            result[current_disease] = {
                                "disease_name": current_disease,
                                "syndromes": {},
                            }
                        stats["diseases_parsed"] += 1

            syndrome_name, syndrome_desc = parse_syndrome_name(c6)
            current_syndrome = syndrome_name
            current_syndrome_desc = syndrome_desc

            # 初提取汤剂信息
            formula_name = parse_formula_name(c8)
            herbs = parse_herbs_from_decoction(c8)
            dosage_text = parse_dosage_text(c8)

            tongue_pulse = extract_tongue_pulse_from_description(syndrome_desc)
            symptoms = extract_symptoms_from_description(syndrome_desc)
            pathology = extract_pathology_from_description(syndrome_desc)

            _ensure_syndrome(result, current_disease, current_syndrome, {
                "syndrome_name": current_syndrome,
                "trigger": f"<{current_syndrome}>\n{syndrome_desc}" if syndrome_desc else f"<{current_syndrome}>",
                "diagnostic_points": syndrome_desc[:200] if syndrome_desc else "",
                "pathology": pathology,
                "symptoms": symptoms,
                "tongue": tongue_pulse["tongue"],
                "pulse": tongue_pulse["pulse"],
                "formula_name": formula_name,
                "herbs": herbs,
                "full_decoction": dosage_text,
                "clinical_modifications": [],
                "source": "oral_formula_master_table",
            })

            current_modifications = []
            stats["syndromes_parsed"] += 1

            # 如果当前行有汤剂方案且含加减信息，也作为基础加减
            if c8:
                _collect_modifications_from_text(current_modifications, c8)

        # ── 加减行（C7 以 + 开头） ──
        elif c7 and c7.startswith('+') and current_disease and current_syndrome:
            mod_reason = c7.lstrip('+').strip()
            mod_herbs = parse_herbs_from_decoction(c8)
            if mod_herbs or (c8 and '+' in c8):
                current_modifications.append({
                    "herb": mod_herbs[0] if mod_herbs else "",
                    "reason": mod_reason,
                    "matched_symptom": mod_reason,
                    "source": "oral_formula_master_table",
                })
                stats["modifications_parsed"] += 1
            elif c8:
                # 尝试从 C8 提取加减信息
                c8_clean = c8.lstrip('+').strip()
                hm = re.match(r'([\u4e00-\u9fff]{2,4})\s*(\d+[gG])', c8_clean)
                if hm:
                    current_modifications.append({
                        "herb": hm.group(1),
                        "reason": mod_reason,
                        "matched_symptom": mod_reason,
                        "source": "oral_formula_master_table",
                    })
                    stats["modifications_parsed"] += 1
                else:
                    stats["rows_skipped_no_formula"] += 1

        else:
            stats["rows_skipped_no_formula"] += 1

    # 最后一个证型
    if current_disease and current_syndrome:
        _finalize_syndrome(result, current_disease, current_syndrome,
                           current_syndrome_desc, current_modifications)

    wb.close()
    return result


def _ensure_syndrome(kb: dict, disease: str, syndrome_name: str, data: dict):
    """确保证型存在，合并已有字段"""
    if disease not in kb:
        kb[disease] = {"disease_name": disease, "syndromes": {}}
    if "syndromes" not in kb[disease]:
        kb[disease]["syndromes"] = {}
    if syndrome_name in kb[disease]["syndromes"]:
        existing = kb[disease]["syndromes"][syndrome_name]
        # 合并：保留已有的，补充缺失的
        for key, value in data.items():
            if key == "clinical_modifications":
                # 合并加减：已有的加减保留，新增的追加
                existing_mods = existing.get("clinical_modifications", [])
                existing_reasons = {m.get("reason", "") for m in existing_mods if isinstance(m, dict)}
                for new_mod in (value or []):
                    if isinstance(new_mod, dict) and new_mod.get("reason", "") not in existing_reasons:
                        existing_mods.append(new_mod)
                existing["clinical_modifications"] = existing_mods
            elif key == "herbs":
                existing_herbs = existing.get("herbs", [])
                for h in (value or []):
                    if h not in existing_herbs:
                        existing_herbs.append(h)
                existing["herbs"] = existing_herbs
            elif key == "symptoms":
                existing_symptoms = existing.get("symptoms", [])
                for s in (value or []):
                    if s not in existing_symptoms:
                        existing_symptoms.append(s)
                existing["symptoms"] = existing_symptoms[:15]
            elif key == "source":
                # 如果已有 source 且不是 oral_formula_master_table，追加
                if existing.get("source") and "oral_formula_master_table" not in str(existing.get("source", "")):
                    existing["source"] = str(existing.get("source", "")) + "; oral_formula_master_table"
                else:
                    existing["source"] = "oral_formula_master_table"
            else:
                # 仅当已有时不覆盖
                if key not in existing or not existing.get(key):
                    existing[key] = value
        kb[disease]["syndromes"][syndrome_name] = existing
    else:
        kb[disease]["syndromes"][syndrome_name] = data


def _collect_modifications_from_text(mod_list: list, text: str):
    """从汤剂方案的加减描述中提取加减"""
    if not text:
        return
    # 查找 + 开头的加减
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('+'):
            parts = line.lstrip('+').strip().split(None, 1)
            if len(parts) >= 2:
                herb_part = parts[0]
                reason = parts[1] if len(parts) > 1 else ""
                if re.match(r'[\u4e00-\u9fff]{2,4}', herb_part):
                    mod_list.append({
                        "herb": herb_part,
                        "reason": reason[:40],
                        "matched_symptom": reason[:20],
                        "source": "oral_formula_master_table",
                    })


def _finalize_syndrome(kb: dict, disease: str, syndrome_name: str,
                       syndrome_desc: str, modifications: list):
    """将临时收集的加减信息写入已有证型"""
    if disease not in kb or syndrome_name not in kb[disease].get("syndromes", {}):
        return
    syndrome = kb[disease]["syndromes"][syndrome_name]
    existing_mods = syndrome.get("clinical_modifications", []) or []
    existing_reasons = {m.get("reason", "") for m in existing_mods if isinstance(m, dict)}
    for m in modifications:
        if isinstance(m, dict) and m.get("reason", "") not in existing_reasons:
            existing_mods.append(m)
    syndrome["clinical_modifications"] = existing_mods


# ══════════════════════════════════════════════════════════════
# KB 后处理：对齐检查
# ══════════════════════════════════════════════════════════════

def check_alignment(kb: dict) -> list:
    """检查 DISEASE_NAME_MAP 和 m2_fallback 的 value 是否在 KB 中存在"""
    bridge_path = PROJECT_ROOT / "bridge_server.py"
    missing = []

    if bridge_path.exists():
        source = bridge_path.read_text(encoding='utf-8')
        # 只提取 DISEASE_NAME_MAP dict 内部的 value（非 meta 字段、非剂量、非 QA 问句）
        # 定位到 DISEASE_NAME_MAP 定义范围
        dm_start = source.find("DISEASE_NAME_MAP")
        dm_end = source.find("m2_fallback")
        if dm_start < 0:
            dm_start = 0
            dm_end = len(source)
        else:
            if dm_end < 0:
                dm_end = dm_start + 20000
            dm_section = source[dm_start:dm_end]

        # 提取 key: "value" 模式，过滤非疾病名
        all_values = set(re.findall(r':\s*"([^"]+)"', dm_section))
        # 过滤掉已知非疾病名
        skip_patterns = [
            r'^\d+g$', r'^\d+$', r'^\w+$', r'^[gG]$',
            r'诊断结果', r'正在分析', r'未命名', r'未知', r'半年', r'名家医案',
            r'^\u6587\u672c$', r'^\u7f51\u9875$',  # 'text', 'web'
        ]
        skip_exact = {
            "丹皮6g", "人参6g", "升麻6g", "山栀子9g", "当归12g", "木香6g",
            "杏仁9g", "枇杷叶9g", "柴胡6g", "栀子9g", "桑白皮9g", "桔梗6g",
            "橘皮9g", "浙贝母9g", "淡竹叶6g", "淡豆豉6g", "炙麻黄6g",
            "牛蒡子9g", "瓜蒌仁9g", "甘草3g", "生甘草3g", "生石膏30g",
            "白头翁12g", "白术6g", "白芍12g", "百合12g", "知母9g", "秦皮9g",
            "芦根9g", "茯苓12g", "荆芥穗6g", "葛根15g", "薄荷6g",
            "辛夷6g", "连翘9g", "金荞麦15g", "金银花9g", "陈皮6g",
            "鱼腥草15g", "麦冬9g", "麦门冬20g", "黄柏9g", "黄芩9g",
            "黄芪20g", "黄连6g", "diagnosis_result", "not_found", "ok",
            "pong", "processing", "selection_q", "shouyi-cdss-bridge",
            "text", "web",
        }
        skip_prefixes = [
            "您", "有无", "是否", "咳嗽是", "头痛或", "小便有无", "弯腰",
            "除了主要", "近期做过", "皮疹遇热", "疼痛是否", "症状是",
            "症状是否", "活动是否", "请补充", "您对什么",
        ]

        for value in all_values:
            if value in skip_exact:
                continue
            if any(value.startswith(p) for p in skip_prefixes):
                continue
            if re.match(r'^\d+g$', value):
                continue
            if re.match(r'^[\u4e00-\u9fff]{2,4}\d+g$', value):  # 剂量如"丹皮6g"
                continue
            if len(value) > 12:
                continue  # 太长的大概率是QA问句
            if not re.search(r'[\u4e00-\u9fff]', value):
                continue  # 不含中文
            if value in ("", " "):
                continue
            # 规范化：去除两边括号和英文注释
            _clean = value.strip()
            _clean = re.sub(r'\s*\([^)]*\)\s*$', '', _clean).strip()
            _clean = re.sub(r'\s*（[^）]*）\s*$', '', _clean).strip()
            if _clean not in kb and _clean != "":
                missing.append(f"DISEASE_NAME_MAP value: '{value}' (normalized: '{_clean}')")

    return missing


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  口服方剂知识库总表 → m2_formula_knowledge.json 导入工具")
    print("=" * 60)
    print()

    # 1. 定位源文件
    try:
        source_path = find_source_file()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # 2. 备份现有 KB
    if TARGET_KB.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"m2_formula_knowledge_{timestamp}.json"
        backup_path = BACKUP_DIR / backup_name
        shutil.copy2(TARGET_KB, backup_path)
        print(f"[BACKUP] 已备份到: {backup_path}")
        # 同时备份一个 latest 副本
        latest_backup = BACKUP_DIR / "m2_formula_knowledge_latest.json"
        shutil.copy2(TARGET_KB, latest_backup)
        print(f"[BACKUP] 已备份到: {latest_backup}")

    # 3. 导入
    print()
    print("[IMPORT] 开始导入...")
    result = import_master_table(source_path)

    # 4. 写入
    print()
    print(f"[WRITE] 写入 {TARGET_KB} ...")
    TARGET_KB.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET_KB, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[WRITE] 完成. KB 大小: {TARGET_KB.stat().st_size / 1024:.0f} KB")

    # 5. 对齐检查
    print()
    print("=" * 60)
    print("  导入统计")
    print("=" * 60)
    print(f"  读取总行数:            {stats['total_rows_read']}")
    print(f"  跳过无疾病行:           {stats['rows_skipped_no_disease']}")
    print(f"  跳过无方剂行:           {stats['rows_skipped_no_formula']}")
    print(f"  解析疾病数:             {stats['diseases_parsed']}")
    print(f"  解析证型数:             {stats['syndromes_parsed']}")
    print(f"  解析加减条目数:         {stats['modifications_parsed']}")
    print(f"  新增 disease key 数:    {stats['new_disease_keys']}")
    print(f"  合并 disease key 数:    {stats['merged_disease_keys']}")
    print()
    print(f"  KB 总疾病数:            {len(result)}")
    total_syn = sum(len(d.get('syndromes', {})) for d in result.values() if isinstance(d, dict))
    print(f"  KB 总证型数:            {total_syn}")

    # 对齐检查
    missing = check_alignment(result)
    if missing:
        print()
        print(f"[WARN] 以下 DISEASE_NAME_MAP / m2_fallback 的 value 在 KB 中不存在:")
        for m in missing:
            print(f"  ⚠  {m}")
        stats["missing_keys_log"] = missing
    else:
        print()
        print("[OK] DISEASE_NAME_MAP / m2_fallback 全部对齐")

    # 特定疾病检查
    print()
    print("─" * 40)
    print("  特定疾病检查")
    print("─" * 40)
    for disease in ["肺炎", "带状疱疹", "急性上呼吸道感染", "急性胃肠炎", "变应性鼻炎", "喉癌", "缺铁性贫血"]:
        if disease in result:
            synds = result[disease].get("syndromes", {})
            print(f"  ✅ {disease}: {len(synds)} 个证型")
            for sn in list(synds.keys())[:3]:
                s = synds[sn]
                print(f"      - {sn} (方剂={s.get('formula_name','')}, 药={len(s.get('herbs',[]))}味)")
        else:
            print(f"  ❌ {disease}: 不存在")

    print()
    print("[DONE] 导入完成")


if __name__ == "__main__":
    main()
