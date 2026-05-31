# 医疗诊断 CDSS 前后端审计报告
**生成日期**：2026年5月31日

---

## 📋 执行摘要

### 整体评分

| 维度 | 评分 | 状态 |
|------|------|------|
| **功能完整性** | 6/10 | 🟡 |
| **代码质量** | 5/10 | 🟡 |
| **安全性** | 3/10 | 🔴 |
| **可维护性** | 4/10 | 🟡 |
| **性能** | 6/10 | 🟡 |
| **可靠性** | 5/10 | 🟡 |
| **测试覆盖** | 1/10 | 🔴 |
| **文档** | 4/10 | 🟡 |

**总体评价**：项目具有良好的医学诊疗逻辑架构，但在工程质量、安全性和可维护性方面需要重大投入。

---

## 🔴 严重问题（需立即修复）

### 1. API 密钥暴露
- **位置**：[bridge_server.py L20](bridge_server.py#L20)、[full_pipeline.py L8-9](full_pipeline.py#L8-9)
- **风险**：所有访问者都可盗用 API 密钥
- **立即行动**：
  - 轮换所有暴露的密钥
  - 迁移至环境变量或密钥管理服务

### 2. 患者数据仅存内存
- **位置**：[bridge_server.py L38-40](bridge_server.py#L38-40)
- **风险**：服务器重启丧失所有数据，违反医疗合规
- **建议**：集成数据库（PostgreSQL/MySQL）

### 3. CORS 过度宽松
- **位置**：[bridge_server.py L99](bridge_server.py#L99)
- **风险**：允许任何域跨域访问
- **修复**：限制允许的域名列表

### 4. 缺乏请求大小限制
- **位置**：[bridge_server.py](bridge_server.py)
- **风险**：易被 DoS 攻击
- **建议**：添加请求体大小验证

---

## 🟠 高风险问题（1-2周内修复）

### 后端

#### 错误处理不完整
**位置**：[bridge_server.py L256-268](bridge_server.py#L256-268)
```python
# 问题：M2/M3 处理失败被吞掉
try:
    m2_r = m2.process(...)
except Exception as e:
    print(f"  M2 err: {e}")  # ❌ 用户不知道发生了什么
```
**影响**：用户收到误导性消息，无法了解实际故障
**改进**：详见审计报告第1.1节

#### LLM 调用无超时和重试
**位置**：[m2_engine.py](m2_engine.py)
```python
llm_result = self._call_llm(prompt)  # ❌ 可能无限等待
```
**影响**：网络问题导致流程卡住
**解决方案**：实现超时（30s）+ 指数退避重试

#### 文件操作异常处理不足
**位置**：[m1_engine.py L104-137](m1_engine.py#L104-137)
```python
with open(db_path, "r", encoding="utf-8") as f:
    self.db: List[dict] = json.load(f)  # ❌ 无异常处理
```
**影响**：知识库加载失败导致应用崩溃
**建议**：添加文件不存在、JSON 解析失败的处理

#### 输入验证不充分
**位置**：[m1_engine.py L202-237](m1_engine.py#L202-237)
**问题**：
- 不检查字符串长度
- 不验证数据类型严格性
- 无 SQL/HTML 注入检查
**建议**：实现完整的输入验证层

### 前端

#### WebSocket 连接竞态条件
**位置**：[js/websocket.js L126-149](js/websocket.js#L126-149)
```javascript
function connectWs() {
    if (ws && ws.readyState === WebSocket.CONNECTING) {
        return;  // ❌ 可能多个并发连接
    }
}
```
**改进**：添加连接锁（`connectionInProgress` flag）

#### 缺乏请求超时
**位置**：[js/api.js L2-40](js/api.js#L2-40)
```javascript
const resp = await fetch(url, ...);  // ❌ 无超时
```
**改进**：使用 `AbortController` 实现超时

---

## 🟡 中等优先级问题

### 性能问题

#### 1. 缓存机制不完善
- **位置**：[m1_engine.py L141-167](m1_engine.py#L141-167)
- **问题**：缓存损坏时无通知、无过期机制、无原子性写入
- **建议**：实现版本化缓存 + TTL + 原子写入

#### 2. M3 十八反十九畏检查性能低
- **位置**：[m3_engine.py L45-97](m3_engine.py#L45-97)
- **问题**：每次查询都是 O(n)，未利用哈希表
- **改进**：在初始化时预构建标准化映射表

#### 3. M3 毒性检查未考虑剂量
- **位置**：[m3_engine.py L63-75](m3_engine.py#L63-75)
- **问题**：仅检查药物是否有毒，不检查实际剂量
- **改进**：根据剂量和患者特征评估风险

#### 4. 前端消息列表内存泄漏
- **位置**：[js/clinic.js](js/clinic.js)
- **问题**：使用 `innerHTML` 导致事件监听器未清理
- **改进**：使用事件委托替代直接监听

### 集成问题

#### 1. M4 硬编码数据与知识库同步
- **位置**：[m4_engine.py L18-62](m4_engine.py#L18-62)
- **问题**：维护两份数据副本，容易不同步
- **建议**：完全依赖知识库，删除硬编码

#### 2. M4 危险信号阈值硬编码
- **位置**：[m4_engine.py L10-16](m4_engine.py#L10-16)
- **问题**：阈值应根据年龄、性别、基础疾病调整
- **改进**：实现分层阈值系统

#### 3. 数据流转缺少验证
- **位置**：[full_pipeline.py L200+](full_pipeline.py)
- **问题**：M1→M2→M3→M4 的数据转换未验证
- **建议**：在每个界面添加验证

---

## 📊 详细分析

### 后端评分

| 模块 | 当前评分 | 关键问题 |
|------|--------|---------|
| M1 诊断引擎 | 6/10 | 缓存机制弱、异常处理不足 |
| M2 选方引擎 | 5/10 | LLM 调用无超时、递归保护不足 |
| M3 审方引擎 | 5/10 | 性能低、规则不完善 |
| M4 路由引擎 | 4/10 | 硬编码数据、规则单一 |
| 服务层 | 6/10 | 搜索逻辑复杂、未优化 |
| API Server | 3/10 | 安全漏洞多、错误处理不足 |

### 前端评分

| 模块 | 当前评分 | 关键问题 |
|------|--------|---------|
| WebSocket | 6/10 | 竞态条件、错误处理不足 |
| 聊天界面 | 5/10 | 去重逻辑复杂、内存泄漏风险 |
| API 客户端 | 4/10 | 无超时、缺乏重试 |
| 患者管理 | 5/10 | 搜索性能低、内存泄漏 |
| 工具函数 | 6/10 | XSS 风险可能存在 |

---

## 🔧 改进建议

### 第一阶段（立即修复，安全关键）
**时间**：本周内
**检查清单**：
- [ ] 轮换所有暴露的 API 密钥
- [ ] 迁移密钥至环境变量（`.env` + 环境变量）
- [ ] 添加请求体大小限制
- [ ] 实现基本的身份验证中间件
- [ ] 添加 CORS 白名单

### 第二阶段（功能完整性）
**时间**：1-2周
**优先级任务**：
- [ ] 实现患者数据数据库持久化
- [ ] 完整的错误处理和前端通知
- [ ] LLM 调用超时和重试机制
- [ ] 修复 WebSocket 竞态条件
- [ ] 编写 API 文档

### 第三阶段（质量提升）
**时间**：1个月
**改进方向**：
- [ ] 结构化日志和集中式监控
- [ ] 单元测试（目标 >80% 覆盖率）
- [ ] 集成测试（M1→M4 流程）
- [ ] 前端 E2E 测试
- [ ] 代码重组织和模块化
- [ ] 性能优化（缓存、索引）

### 第四阶段（持续改进）
**时间**：1-3个月
**方向**：
- [ ] 医学规则更新和验证
- [ ] 前端用户体验优化
- [ ] 医学合规审计日志
- [ ] 微服务架构重构

---

## 📁 建议的代码结构

```
medical-diagnosis/
├── backend/
│   ├── core/              # 诊疗引擎
│   │   ├── m1_engine.py
│   │   ├── m2_engine.py
│   │   ├── m3_engine.py
│   │   ├── m4_engine.py
│   │   └── full_pipeline.py
│   ├── api/               # API 层
│   │   ├── handlers/
│   │   ├── middleware/    # 认证、错误处理
│   │   └── server.py
│   ├── models/            # 数据模型
│   ├── services/          # 业务逻辑
│   ├── utils/             # 工具函数
│   └── tests/
├── frontend/
│   ├── components/
│   ├── api/
│   ├── utils/
│   └── tests/
├── data/
├── docs/
├── .env.example
├── docker-compose.yml
└── README.md
```

---

## 📝 快速参考

### 安全漏洞快速修复

**修复 1：保护 API 密钥**
```python
# 旧代码（危险）
os.environ['DEEPSEEK_API_KEY'] = 'sk-xxx'

# 新代码（安全）
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get('DEEPSEEK_API_KEY')
if not api_key:
    raise ValueError("DEEPSEEK_API_KEY not configured")
```

**修复 2：验证请求体**
```python
def _validate_request(self, body: bytes, max_size: int = 1024*1024):
    if len(body) > max_size:
        raise ValueError(f"Request too large: {len(body)} > {max_size}")
    
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")
```

**修复 3：添加 CORS 白名单**
```python
def _cors(self):
    allowed = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5000").split(",")
    origin = self.headers.get("Origin", "")
    
    if origin in allowed:
        self.send_header("Access-Control-Allow-Origin", origin)
```

---

## 🎯 关键指标监控

### 建议的监控项
1. **API 响应时间**：目标 <2s（P95）
2. **错误率**：目标 <1%
3. **缓存命中率**：目标 >70%
4. **数据库查询时间**：目标 <500ms（P95）
5. **WebSocket 连接稳定性**：目标 99% 正常运行时间

---

## 📚 相关文档

- [完整审计代码清单](#) - 详细的代码问题和解决方案
- [API 设计指南](#) - 待编写
- [测试计划](#) - 待编写
- [部署指南](#) - 待编写

---

**审计员**：GitHub Copilot  
**审计日期**：2026年5月31日  
**下次审计建议**：3个月后或在完成第一阶段改进后
