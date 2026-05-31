# 上下文存盘 — 下一会话继续

## 当前状态（2026-05-31 会话结束）

### 已完成
1. **新增患者闭环** ✅
   - `saveNewPatient()` 中生成稳定 `patientId`
   - `[CREATE_PATIENT_DONE]` 日志输出
   - 患者合并入列表而非覆盖（`mergePatientIntoList` 工具函数）

2. **旧患者保留** ✅
   - `normalizePatientId()` 统一ID规范化
   - `mergePatientIntoList()` 按ID去重合并，不覆盖
   - `allPatients.length=6`，`clinicSessions.length=6`

3. **问诊启动日志** ✅
   - `[START_ASSESSMENT_REQUEST]` 日志
   - `[START_ASSESSMENT_ABORT]` 兜底
   - WS 消息发送成功

### 待解决（明天继续）

#### 问题：WS 回复不显示
- Console 可见：后端返回了 `local_xxx` 的消息
- 但 `window.currentPatientId` 在 WS 发送过程中丢失（被 `syncPatients` 或其他异步操作覆盖为 null）
- `websocket.js` 中收到消息时 `currentPatientId` 为 null，导致 `"当前选中的患者是 null，所以不显示"`

#### 当前修复尝试
- `mock-data.js` 中在 `ws.send` 前加了 `window.currentPatientId = patientId`
- `websocket.js` 中加了 `WS_AUTO_RESTORE` 和 `WS_MESSAGE_MATCH` 日志
- **但需要验证是否生效**（可能被缓存阻止）

## 修改的文件

### `js/mock-data.js`
- 在 `saveNewPatient` 前添加了 `normalizePatientId()` 和 `mergePatientIntoList()` 工具函数
- `{ ...newPatient, summary }` 后添加了 `patientId` 定义
- 添加了 `[CREATE_PATIENT_DONE]` 和 `[START_ASSESSMENT_REQUEST]` 日志
- 启动问诊前强制设置 `window.currentPatientId`
- 使用 `patientId` 替代 `newPatient.id`

### `js/websocket.js`
- 在消息归属判断处添加自动恢复逻辑（`WS_AUTO_RESTORE`）
- 添加 `[WS_MESSAGE_MATCH]` 日志

### `js/api.js`
- `syncPatients()` 合并逻辑改为使用 `mergePatientIntoList` 按ID合并

## 验收清单
- [ ] 新增患者后旧患者保留
- [ ] `[CREATE_PATIENT_DONE]` patientId 有值
- [ ] `[START_ASSESSMENT_REQUEST]` patientId 有值  
- [ ] WS 发送成功
- [ ] WS 回复渲染到聊天区（核心待修）
- [ ] `[WS_AUTO_RESTORE]` 出现
- [ ] `[WS_MESSAGE_MATCH]` shouldDisplay = true

## 启动命令
```bash
cd /Users/fangxuan/projects/medical-diagnosis
python3 -m http.server 6005
```
浏览器打开: http://localhost:6005/Clinical%20Chat.html
