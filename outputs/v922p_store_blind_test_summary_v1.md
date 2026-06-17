# V9.2.2P 门店盲测验收摘要

**知识库**: 颐而康AI知识库_V9.2.2P_生产合并总基线.xlsx
**路径**: `/Users/fangxuan/Desktop/颐而康运行库/颐而康AI知识库_V9.2.2P_生产合并总基线.xlsx`
**病种**: 361 | FAQ: 22 | COMM: 24
**必备表**: RULE=61 REDFLAG_SUSPECT=17 QUESTION=17 REVIEW=12

---

## 一、30 条门店场景分流结果

| # | stage | 输入（前30字） | final_triage | service_path | tech_card |
|---|-------|-----|-------------|-------------|----------|
| S01 | intake | 腰酸腿麻，经常腰痛，想按一下。 | P2 | disease_based_service | allow |
| S02 | intake | 腰痛，腿麻，而且这两天脚有点没力。 | P1 | clinic_review | block |
| S03 | intake | 腰痛，最近大小便有点不正常，会阴那里也麻麻的。 | P0 | hospital | block |
| S04 | intake | 腰骶臀区睡觉时疼痛，白天好像还行。 | pending_question | continue_question | block |
| S05 | followup_q | 就是睡久了压着痛，起来活动一下就好了，没有腿麻，没有无力，大 | P2 | region_based_service | block |
| S06 | followup_q | 晚上经常痛醒，最近越来越痛，休息也不太缓解。 | P1 | clinic_review | block |
| S07 | intake | 尾椎骨痛，隐隐约约，持续半年了。 | P2 | disease_based_service | allow |
| S08 | intake | 下肢疼痛，比较轻，就是想放松一下。 | P2 | region_based_service | block |
| S09 | intake | 全身放松，无明显不适，就是理疗卡维护一下。 | P2 | store_followup | block |
| S10 | intake | 我今天想做针灸调理，没什么明显不舒服。 | P2 | permission_review | block |
| S11 | intake | 我坐久了一站起来屁股刺痛，反复几个月了。 | P2 | store_followup | block |
| S12 | intake | 坐飞机回来后，腰臀后侧一片紧痛，腿后侧也紧，没有麻木无力。 | P2 | disease_based_service | allow |
| S13 | intake | 我经期前后容易头痛，颈部很紧，有时头顶也痛。 | P2 | region_based_service | block |
| S14 | post_servi | 这次做完只改善三成，没有上次明显。 | P2 | store_followup | block |
| S15 | technician | 按着按着他说脚好像越来越没力，走路发软。 | P1 | clinic_review | block |
| S16 | intake | 颈椎病不舒服，想按肩颈。 | P2 | region_based_service | block |
| S17 | intake | 颈肩痛，偶尔手麻，没有无力。 | P2 | region_based_service | block |
| S18 | intake | 颈肩痛，手麻，而且手有点拿不稳东西。 | P2 | region_based_service | block |
| S19 | intake | 肩周炎，手有点麻，想松一下肩膀。 | P2 | disease_based_service | allow |
| S20 | intake | 膝盖痛，走路有点不稳，偶尔打软腿。 | P1 | clinic_review | block |
| S21 | intake | 腰不舒服，说不清楚，就是酸酸胀胀的。 | P2 | disease_based_service | allow |
| S22 | intake | 背腰骶隐隐约约不舒服，定期保养。 | P2 | disease_based_service | allow |
| S23 | intake | 下肢小腿酸胀，比较轻，没有肿胀，没有发热，能正常走路。 | P2 | region_based_service | block |
| S24 | intake | 小腿一边突然肿起来，还有点发热发红。 | P0 | hospital | block |
| S25 | intake | 胸口闷痛，肩背也有点牵着痛，想按一下背。 | P2 | disease_based_service | allow |
| S26 | intake | 昨天摔了一跤，今天腰痛明显，动一下就痛。 | P1 | clinic_review | block |
| S27 | intake | 我想做重手法，越重越好，腰背很紧。 | P2 | disease_based_service | allow |
| S28 | intake | 我想拔罐，最近有点感冒发热，背也酸。 | P0 | hospital | block |
| S29 | post_servi | 连续两次做完都只舒服一点点，第二天又反复。 | P2 | store_followup | block |
| S30 | technician | 服务过程中顾客突然说头很晕，还恶心想吐。 | P2 | region_based_service | block |

### 分流统计
```
  P0: 3
  P1: 5
  P2: 21
  pending_question: 1
```

### 路径统计
```
  clinic_review: 5
  continue_question: 1
  disease_based_service: 8
  hospital: 3
  permission_review: 1
  region_based_service: 8
  store_followup: 4
```

---

## 二、10 条 FAQ 匹配结果

| # | 问题 | faq_id | 回复摘要 |
|---|------|--------|---------|
| FAQ01 | 我这个是不是很严重？ | FAQ001 | 多数酸紧不一定重，先看有没有几个需要先确认的情况。 |
| FAQ02 | 为什么还要问这么多？ | FAQ003 | 多问几句，是为了帮你选更合适的方式和力度。 |
| FAQ03 | 多久能缓解？ | FAQ004 | 通常看当次放松感和接下来1–3天变化，不能承诺一次解决。 |
| FAQ04 | 能不能做重手法？ | FAQ005 | 不建议一上来就重手法，先看身体反应更稳。 |
| FAQ05 | 今天做完回家要注意什么？ | FAQ006 | 先轻活动、避免久坐久站和剧烈运动，观察24–72小时。 |
| FAQ06 | 我有手麻或者腿麻，是不是很危险？ | FAQ010 | 麻不一定严重，关键要看有没有没力、走路发软或大小便变化。 |
| FAQ07 | 夜里痛但白天好点怎么办？ | FAQ011 | 先分清是压着痛，还是夜里痛醒、休息也不缓解。 |
| FAQ08 | 做完更酸正常吗？ | FAQ013 | 轻微酸胀可以观察，明显加重或异常变化要及时反馈。 |
| FAQ09 | 理疗卡怎么用更合适？ | FAQ014 | 更建议按身体反馈和诱因记录来用。 |
| FAQ10 | 什么时候必须去医院？ | FAQ020 | 出现明显异常变化时，不建议继续门店处理。 |

FAQ 命中率: 10/10

---

## 三、异常项

共 32 项异常：
- [S01] body_region_unknown=True — 腰酸腿麻，经常腰痛，想按一下。
- [S02] body_region_unknown=True — 腰痛，腿麻，而且这两天脚有点没力。
- [S03] body_region_unknown=True — 腰痛，最近大小便有点不正常，会阴那里也麻麻的。
- [S04] body_region_unknown=True — 腰骶臀区睡觉时疼痛，白天好像还行。
- [S05] P2 但 technician_card_permission=false — 就是睡久了压着痛，起来活动一下就好了，没有腿麻，没有无力，大小便也正常。
- [S08] P2 但 technician_card_permission=false — 下肢疼痛，比较轻，就是想放松一下。
- [S09] P2 但 technician_card_permission=false — 全身放松，无明显不适，就是理疗卡维护一下。
- [S10] P2 但 technician_card_permission=false — 我今天想做针灸调理，没什么明显不舒服。
- [S11] P2 但 technician_card_permission=false — 我坐久了一站起来屁股刺痛，反复几个月了。
- [S12] body_region_unknown=True — 坐飞机回来后，腰臀后侧一片紧痛，腿后侧也紧，没有麻木无力。
- [S13] body_region_unknown=True — 我经期前后容易头痛，颈部很紧，有时头顶也痛。
- [S13] P2 但 technician_card_permission=false — 我经期前后容易头痛，颈部很紧，有时头顶也痛。
- [S14] P2 但 technician_card_permission=false — 这次做完只改善三成，没有上次明显。
- [S15] body_region_unknown=True — 按着按着他说脚好像越来越没力，走路发软。
- [S16] P2 但 technician_card_permission=false — 颈椎病不舒服，想按肩颈。
- [S17] body_region_unknown=True — 颈肩痛，偶尔手麻，没有无力。
- [S17] P2 但 technician_card_permission=false — 颈肩痛，偶尔手麻，没有无力。
- [S18] body_region_unknown=True — 颈肩痛，手麻，而且手有点拿不稳东西。
- [S18] P2 但 technician_card_permission=false — 颈肩痛，手麻，而且手有点拿不稳东西。
- [S19] body_region_unknown=True — 肩周炎，手有点麻，想松一下肩膀。
- [S20] body_region_unknown=True — 膝盖痛，走路有点不稳，偶尔打软腿。
- [S21] body_region_unknown=True — 腰不舒服，说不清楚，就是酸酸胀胀的。
- [S22] body_region_unknown=True — 背腰骶隐隐约约不舒服，定期保养。
- [S23] P2 但 technician_card_permission=false — 下肢小腿酸胀，比较轻，没有肿胀，没有发热，能正常走路。
- [S25] body_region_unknown=True — 胸口闷痛，肩背也有点牵着痛，想按一下背。
- [S26] body_region_unknown=True — 昨天摔了一跤，今天腰痛明显，动一下就痛。
- [S27] body_region_unknown=True — 我想做重手法，越重越好，腰背很紧。
- [S28] body_region_unknown=True — 我想拔罐，最近有点感冒发热，背也酸。
- [S29] P2 但 technician_card_permission=false — 连续两次做完都只舒服一点点，第二天又反复。
- [S30] body_region_unknown=True — 服务过程中顾客突然说头很晕，还恶心想吐。
- [S30] P2 但 technician_card_permission=false — 服务过程中顾客突然说头很晕，还恶心想吐。
- [S30] 服务中疑似未重新分诊 — 服务过程中顾客突然说头很晕，还恶心想吐。

---

## 四、关键检查项

| 检查项 | 结果 |
|---|------|
| 是否出现 P3 | 否 |
| 是否出现顾客端禁词 | 否 |
| body_region_unknown 场景 | 18 条 |
| P2 但 tech_card=false | 13 条（S05, S08, S09, S10, S11, S13, S14, S16, S17, S18, S23, S29, S30） |
| 服务中未重新分诊 | 1 条 |
| FAQ 命中率 | 10/10 |