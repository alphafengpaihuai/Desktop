"""Triage low-confidence English disease-name mapping candidates.

Offline only. Does not modify formal mappings or pharmacology caches.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "m1_name_mapping_candidates.json"
OUTPUT = ROOT / "data" / "m1_name_mapping_low_triage.json"


RECOMMENDED_EN = {
    "IgA血管炎性肾炎": ("IgA vasculitis nephritis", "C_manual_high_value", "IgA 血管炎肾脏受累，高价值肾病/免疫病，英文需人工确认是否采用 IgA vasculitis nephritis 或 Henoch-Schonlein purpura nephritis。"),
    "上睑下垂": ("Ptosis", "A_promote_to_medium", "中文 anchor 明确，标准英文病名明确。"),
    "下肢动脉硬化闭塞症": ("Lower extremity arteriosclerosis obliterans", "C_manual_high_value", "外周动脉疾病高价值血管病，英文表述需人工确认与库内 anchor 一致。"),
    "下肢深静脉血栓形成": ("Deep vein thrombosis", "C_manual_high_value", "血栓性疾病高价值，建议确认是否限定 lower extremity DVT。"),
    "不育症": ("Infertility", "B_reject_broad", "过于宽泛，需区分 male/female infertility 或具体病因。"),
    "中心性浆液性脉络膜视网膜病变": ("Central serous chorioretinopathy", "A_promote_to_medium", "眼科标准疾病名明确。"),
    "中耳胆脂瘤": ("Middle ear cholesteatoma", "A_promote_to_medium", "耳科标准疾病名明确。"),
    "中间葡萄膜炎": ("Intermediate uveitis", "A_promote_to_medium", "眼科标准疾病名明确。"),
    "乙状窦血栓性静脉炎": ("Sigmoid sinus thrombophlebitis", "C_manual_high_value", "颅内/耳源性血栓感染相关高价值病种，需人工确认术语。"),
    "乳房结核": ("Breast tuberculosis", "C_manual_high_value", "感染病种，高价值但少见，建议人工确认英文。"),
    "乳腺癌": ("Breast cancer", "C_manual_high_value", "肿瘤高价值病种，英文明确但需确认是否与现有 breast carcinoma anchor 合并。"),
    "乳腺纤维腺瘤": ("Breast fibroadenoma", "A_promote_to_medium", "标准良性肿瘤名明确。"),
    "产后出血": ("Postpartum hemorrhage", "C_manual_high_value", "产科急症/出血高价值，英文明确。"),
    "产褥感染": ("Puerperal infection", "C_manual_high_value", "产科感染高价值，需确认是否用 puerperal sepsis。"),
    "传染性单核细胞增多症": ("Infectious mononucleosis", "C_manual_high_value", "感染病种高价值，英文明确。"),
    "伤寒": ("Typhoid fever", "C_manual_high_value", "感染病种高价值，英文明确。"),
    "体位性低血压": ("Orthostatic hypotension", "A_promote_to_medium", "标准英文病名明确。"),
    "儿童反复呼吸道感染": ("Recurrent respiratory tract infections in children", "C_manual_high_value", "儿科高频病种，高价值，需确认是否作为 syndrome anchor。"),
    "儿童急性扁桃体炎": ("Acute tonsillitis in children", "C_manual_high_value", "儿科感染高频病，英文明确但需确认是否并入 acute tonsillitis。"),
    "儿童抽动障碍": ("Tic disorder in children", "C_manual_high_value", "儿科神经行为高频病，需确认与 Tourette/tic disorder anchor。"),
    "先天性耳前瘘管": ("Congenital preauricular fistula", "A_promote_to_medium", "标准耳科病名明确。"),
    "先天性聋": ("Congenital hearing loss", "A_promote_to_medium", "标准英文病名明确。"),
    "先天性青光眼": ("Congenital glaucoma", "A_promote_to_medium", "标准眼科病名明确。"),
    "冠状动脉粥样硬化性心脏病": ("Coronary atherosclerotic heart disease", "C_manual_high_value", "心血管高价值，需确认是否合并到 Coronary Heart Disease。"),
    "前列腺癌": ("Prostate cancer", "C_manual_high_value", "肿瘤高价值病种，英文明确。"),
    "前庭神经炎": ("Vestibular neuritis", "A_promote_to_medium", "标准英文病名明确。"),
    "前置胎盘": ("Placenta previa", "C_manual_high_value", "产科出血风险高价值，英文明确。"),
    "前置胎盘出血": ("Placenta previa bleeding", "C_manual_high_value", "产科出血高价值，需确认是否并入 placenta previa。"),
    "功能失调性子宫出血": ("Dysfunctional uterine bleeding", "A_promote_to_medium", "常用英文检索名明确，但需注意现代分类可能归入 AUB。"),
    "化脓性中耳乳突炎并发迷路炎": ("Suppurative otitis media and mastoiditis with labyrinthitis", "D_cleanup_needed", "复合并发症名，需拆分或人工确认 anchor。"),
    "化脓性腮腺炎": ("Suppurative parotitis", "C_manual_high_value", "感染病种，英文明确但建议人工确认。"),
    "化脓性骨髓炎": ("Suppurative osteomyelitis", "C_manual_high_value", "感染/骨病高价值，英文需人工确认与 osteomyelitis 合并关系。"),
    "化脓性髋关节炎": ("Suppurative arthritis of the hip", "C_manual_high_value", "感染/关节病高价值，需人工确认术语。"),
    "单纯性甲状腺肿": ("Simple goiter", "A_promote_to_medium", "标准英文病名明确。"),
    "单纯疱疹病毒性角膜炎": ("Herpes simplex keratitis", "C_manual_high_value", "眼科感染高价值，英文明确。"),
    "卵巢恶性肿瘤": ("Malignant ovarian tumor", "C_manual_high_value", "肿瘤高价值，需确认是否并入 ovarian cancer。"),
    "原发性皮肤T细胞淋巴瘤": ("Primary cutaneous T-cell lymphoma", "C_manual_high_value", "肿瘤/血液病高价值，英文明确。"),
    "原发性视网膜色素变性": ("Primary retinitis pigmentosa", "A_promote_to_medium", "标准眼科遗传病名较明确，需确认是否用 retinitis pigmentosa。"),
    "原发性闭角型青光眼": ("Primary angle-closure glaucoma", "A_promote_to_medium", "标准眼科病名明确。"),
    "变应性接触性皮炎": ("Allergic contact dermatitis", "A_promote_to_medium", "标准皮肤病名明确。"),
    "口底部蜂窝组织炎": ("Cellulitis of the floor of the mouth", "C_manual_high_value", "感染/急症高价值，需确认是否指 Ludwig angina。"),
    "口腔念珠菌病": ("Oral candidiasis", "C_manual_high_value", "感染病种高价值，英文明确。"),
    "口腔扁平苔藓": ("Oral lichen planus", "A_promote_to_medium", "标准口腔黏膜病名明确。"),
    "后葡萄膜炎": ("Posterior uveitis", "A_promote_to_medium", "标准眼科病名明确。"),
}


BROAD_TERMS = {
    "上消化道出血": "出血部位/临床事件，病因差异大，药理挖掘需按病因拆分。",
    "下肢慢性溃疡": "慢性溃疡为表现/结局，需按糖尿病足、静脉性溃疡、动脉性溃疡等拆分。",
    "不射精": "症状/功能障碍表现，病因复杂，不宜直接合并。",
    "中毒": "过于宽泛，需按具体毒物/中毒类型拆分。",
    "乳头溢血": "症状/体征，需按导管内乳头状瘤、乳腺癌等病因拆分。",
    "乳房异常发育症": "发育异常大类，需具体化。",
    "乳腺增生症": "临床概念较宽，需确认是否作为正式西医 disease anchor。",
    "乳腺炎": "过宽泛，需区分 lactational/non-lactational/mastitis subtype。",
    "乳腺纤维瘤": "命名可能不规范，需确认是否指 fibroadenoma。",
    "产后便秘": "症状/状态，不建议作为药理病名 anchor。",
    "产后多汗": "症状/状态，不建议作为药理病名 anchor。",
    "产后尿潴留": "产后并发症，需确认是否作为独立疾病 anchor。",
    "产后尿频": "症状，需按感染/膀胱功能等病因拆分。",
    "产后腹痛": "症状，病因差异大。",
    "产后身痛": "症状/综合表现，非标准现代病名。",
    "人工流产术后出血": "事件/并发症，需按病因拆分。",
    "代偿性月经": "术语需人工确认，当前不宜合并。",
    "代指": "残缺/非病名。",
    "便血": "症状，需按病因拆分。",
    "儿童腹痛": "症状，需按病因拆分。",
    "先兆流产": "妊娠状态/风险诊断，需谨慎确认英文和纳入边界。",
    "内伤发热": "非现代西医标准病名，不能进入 M1 英文 PubMed mapping。",
    "内痔": "可有英文名 internal hemorrhoids，但药理挖掘价值和 anchor 需人工确认。",
    "前庭大腺炎": "可映射 Bartholin gland abscess/infection，但需确认是否炎症或囊肿。",
    "包皮龟头炎": "较宽泛，需确认 balanoposthitis/具体感染病因。",
    "单纯糠疹": "术语需人工确认，可能 pityriasis alba。",
    "反流性咳嗽": "症状/综合征，建议并入 GERD-related cough/UACS 等明确 anchor。",
    "口腔溃疡": "症状/体征，需按复发性阿弗他溃疡等拆分。",
    "口臭": "症状，需按口腔/消化/代谢病因拆分。",
}


def main() -> None:
    candidates = json.loads(INPUT.read_text(encoding="utf-8"))
    lows = [item for item in candidates if item.get("mapping_confidence") == "low"]
    triaged = [triage(item) for item in lows]
    OUTPUT.write_text(json.dumps(triaged, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = Counter(item["triage_class"] for item in triaged)
    print(json.dumps({
        "output": str(OUTPUT.relative_to(ROOT)),
        "low_total": len(lows),
        "class_counts": dict(counts),
        "top20_manual": [item["zh_name"] for item in priority_items(triaged)[:20]],
    }, ensure_ascii=False, indent=2))


def triage(item: dict[str, Any]) -> dict[str, Any]:
    zh = item.get("zh_name", "")
    clean = item.get("clean_zh_name") or zh
    clean_key = clean.replace(" ", "")
    recommended = ""
    triage_class = "B_reject_broad"
    reason = BROAD_TERMS.get(clean_key) or BROAD_TERMS.get(zh) or "过于宽泛、症状类或当前缺少可靠英文名，暂不合并。"

    if clean_key in RECOMMENDED_EN:
        recommended, cls, rec_reason = RECOMMENDED_EN[clean_key]
        triage_class = cls
        reason = rec_reason
    elif zh in RECOMMENDED_EN:
        recommended, cls, rec_reason = RECOMMENDED_EN[zh]
        triage_class = cls
        reason = rec_reason
    elif clean_key in BROAD_TERMS:
        triage_class = "B_reject_broad"
        reason = BROAD_TERMS[clean_key]

    if "】" in zh or "【" in zh or "）" in zh and "（" not in zh:
        triage_class = "D_cleanup_needed"
        reason = "原始名称存在污染符号、残缺括号或复合解析问题，需先清洗确认。"
    if clean_key in RECOMMENDED_EN and triage_class != "D_cleanup_needed":
        recommended, triage_class, reason = RECOMMENDED_EN[clean_key]

    return {
        "zh_name": zh,
        "clean_zh_name": clean,
        "current_suggested_en_name": item.get("suggested_en_name", ""),
        "recommended_en_name": recommended,
        "triage_class": triage_class,
        "reason": reason,
        "can_merge_now": False,
        "need_manual_confirm": True,
    }


def priority_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {
        "C_manual_high_value": 0,
        "A_promote_to_medium": 1,
        "D_cleanup_needed": 2,
        "B_reject_broad": 3,
    }
    return sorted(items, key=lambda item: (order.get(item["triage_class"], 9), item["zh_name"]))


if __name__ == "__main__":
    main()
