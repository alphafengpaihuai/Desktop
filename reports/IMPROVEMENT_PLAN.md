# 医疗诊断 CDSS - 改进优先级清单

## 优先级 1️⃣：安全关键（本周内完成）

### P1.1 API 密钥安全
- [ ] **立即轮换暴露的密钥**
  - [ ] 更新 DEEPSEEK_API_KEY
  - [ ] 更新 GEMINI_API_KEY
  - [ ] 检查是否有其他暴露的凭证
- [ ] **创建 .env 环境配置**
  ```bash
  # .env 示例
  DEEPSEEK_API_KEY=sk_xxx
  GEMINI_API_KEY=AIzaSy_xxx
  DATABASE_URL=postgresql://user:pass@host/db
  ALLOWED_ORIGINS=http://localhost:5000,https://yourdomain.com
  ```
- [ ] **更新代码加载方式**
  ```python
  from dotenv import load_dotenv
  import os
  
  load_dotenv()
  DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
  ```
- [ ] **添加到 .gitignore**
  ```
  .env
  .env.local
  *.key
  *.pem
  ```
- [ ] **代码审查**：搜索所有硬编码的 API 密钥
  ```bash
  grep -r "sk-" . --include="*.py" --include="*.js"
  grep -r "AIzaSy" . --include="*.py" --include="*.js"
  ```

### P1.2 CORS 安全性
- [ ] **修改 CORS 策略** [bridge_server.py L99]
  ```python
  def _cors(self):
      allowed_origins = os.environ.get(
          "ALLOWED_ORIGINS", 
          "http://localhost:5000"
      ).split(",")
      
      origin = self.headers.get("Origin", "")
      if origin in allowed_origins:
          self.send_header("Access-Control-Allow-Origin", origin)
          self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
          self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
  ```
- [ ] **配置生产环境**
  ```bash
  export ALLOWED_ORIGINS="https://yourdomain.com,https://clinic.yourdomain.com"
  ```

### P1.3 请求验证
- [ ] **添加请求体大小限制** [bridge_server.py]
  ```python
  MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB
  
  def _body(self):
      length = int(self.headers.get('Content-Length', 0))
      if length > MAX_REQUEST_SIZE:
          self.send_error(413, "Request body too large")
          return None
      return self.rfile.read(length)
  ```
- [ ] **验证 JSON 格式**
  ```python
  def do_POST(self):
      body = self._body()
      if not body:
          return
      
      try:
          data = json.loads(body)
      except json.JSONDecodeError:
          self.send_error(400, "Invalid JSON")
          return
  ```

### P1.4 输入验证框架
- [ ] **创建 validators.py**
  ```python
  # backend/utils/validators.py
  MAX_SYMPTOM_LENGTH = 500
  MAX_SYMPTOMS_COUNT = 50
  
  def validate_symptoms(symptoms):
      if not isinstance(symptoms, list):
          raise ValueError("symptoms must be a list")
      
      if len(symptoms) > MAX_SYMPTOMS_COUNT:
          raise ValueError(f"Too many symptoms: {len(symptoms)}")
      
      for s in symptoms:
          if not isinstance(s, str):
              raise ValueError("Each symptom must be a string")
          if len(s) > MAX_SYMPTOM_LENGTH:
              raise ValueError(f"Symptom too long")
      
      return symptoms
  ```
- [ ] **在 M1 中应用**
  ```python
  from utils.validators import validate_symptoms
  
  def normalize_input(self, raw: Dict) -> NormalizedInput:
      symptoms = validate_symptoms(raw.get("symptoms", []))
      # ... rest of code
  ```

---

## 优先级 2️⃣：功能完整性（1-2周内完成）

### P2.1 错误处理完善
- [ ] **统一错误响应格式**
  ```python
  # backend/api/error_handler.py
  class ApiError(Exception):
      def __init__(self, code: str, message: str, status_code: int = 400):
          self.code = code
          self.message = message
          self.status_code = status_code
      
      def to_dict(self):
          return {
              "error": True,
              "error_code": self.code,
              "message": self.message,
              "timestamp": time.time()
          }
  ```
- [ ] **修改 bridge_server 错误处理** [L256-268]
  ```python
  async def chat(ws, pid: str, text: str, md: dict):
      try:
          m1_result = m1.diagnose(...)
          if not m1_result:
              await ws.send(json.dumps({
                  "status": "error",
                  "error_code": "M1_NO_DIAGNOSIS",
                  "message": "未能生成诊断，请补充更多症状信息"
              }))
              return
      except Exception as e:
          logger.exception("M1 diagnosis failed")
          await ws.send(json.dumps({
              "status": "error",
              "error_code": "M1_EXCEPTION",
              "message": "诊断引擎异常，请稍后重试"
          }))
          return
  ```
- [ ] **前端错误处理升级** [js/api.js]
  ```javascript
  async function fetchJson(url, options = {}) {
      try {
          const data = await fetchJsonWithTimeout(url, options);
          if (data.error) {
              throw new ApiError(data.error_code, data.message);
          }
          return data;
      } catch (error) {
          if (error instanceof ApiError) {
              showErrorMessage(error.message, error.code);
          } else {
              showErrorMessage('请求失败，请重试', 'UNKNOWN_ERROR');
          }
          throw error;
      }
  }
  ```

### P2.2 LLM 调用超时和重试
- [ ] **实现重试机制** [m2_engine.py]
  ```python
  import asyncio
  from typing import Optional, Any
  
  async def call_llm_with_retry(
      self, 
      prompt: str, 
      max_retries: int = 3,
      timeout: int = 30
  ) -> Optional[str]:
      for attempt in range(max_retries):
          try:
              result = await asyncio.wait_for(
                  self._call_llm_async(prompt),
                  timeout=timeout
              )
              return result
          except asyncio.TimeoutError:
              if attempt < max_retries - 1:
                  wait_time = 2 ** attempt  # 指数退避
                  logger.warning(f"LLM timeout, retrying in {wait_time}s...")
                  await asyncio.sleep(wait_time)
              else:
                  logger.error("LLM call failed after max retries")
                  return None
          except Exception as e:
              logger.error(f"LLM error on attempt {attempt+1}: {e}")
              if attempt == max_retries - 1:
                  return None
  ```

### P2.3 WebSocket 竞态条件修复
- [ ] **添加连接锁** [js/websocket.js]
  ```javascript
  let connectionInProgress = false;
  
  async function ensureWsConnected() {
      // 已连接
      if (ws && ws.readyState === WebSocket.OPEN) {
          return true;
      }
      
      // 连接中，等待
      if (connectionInProgress) {
          return new Promise((resolve) => {
              const checkInterval = setInterval(() => {
                  if (ws && ws.readyState === WebSocket.OPEN) {
                      clearInterval(checkInterval);
                      resolve(true);
                  }
              }, 100);
          });
      }
      
      connectionInProgress = true;
      try {
          connectWs();
          await waitForConnection(5000);
          return true;
      } finally {
          connectionInProgress = false;
      }
  }
  ```

### P2.4 患者数据持久化
- [ ] **选择数据库** - 推荐 PostgreSQL
  ```bash
  docker run --name medical-db \
    -e POSTGRES_PASSWORD=secure_password \
    -e POSTGRES_DB=medical_db \
    -p 5432:5432 \
    -d postgres:15
  ```
- [ ] **创建数据模型** [backend/models/patient.py]
  ```python
  from sqlalchemy import Column, String, Integer, DateTime, JSON
  from datetime import datetime
  
  class Patient(Base):
      __tablename__ = "patients"
      
      id = Column(String(36), primary_key=True)
      name = Column(String(100))
      age = Column(Integer)
      gender = Column(String(1))  # M/F/O
      phone = Column(String(20))
      
      chief_complaint = Column(String(500))
      medical_history = Column(JSON)
      allergies = Column(JSON)
      
      created_at = Column(DateTime, default=datetime.utcnow)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
      doctor_id = Column(String(36))
  ```
- [ ] **迁移现有数据**
  ```python
  # scripts/migrate_to_db.py
  for pid, patient in patients_db.items():
      db_patient = Patient(**patient)
      session.add(db_patient)
  session.commit()
  ```
- [ ] **更新 bridge_server 使用数据库**
  ```python
  from models import Patient
  
  def ensure_patient(pid: str, doc: str = "") -> Patient:
      patient = session.query(Patient).filter_by(id=pid).first()
      if not patient:
          patient = Patient(patient_id=pid, doctor_id=doc)
          session.add(patient)
          session.commit()
      return patient
  ```

### P2.5 请求超时实现
- [ ] **前端 Fetch 超时** [js/api.js]
  ```javascript
  async function fetchWithTimeout(url, options = {}) {
      const timeout = options.timeout || 30000;
      const controller = new AbortController();
      const id = setTimeout(() => controller.abort(), timeout);
      
      try {
          const response = await fetch(url, {
              ...options,
              signal: controller.signal
          });
          
          if (!response.ok) {
              throw new Error(`HTTP ${response.status}`);
          }
          
          return response.json();
      } catch (error) {
          if (error.name === 'AbortError') {
              throw new Error(`请求超时（${timeout}ms）`);
          }
          throw error;
      } finally {
          clearTimeout(id);
      }
  }
  ```

---

## 优先级 3️⃣：代码质量（1个月内完成）

### P3.1 结构化日志
- [ ] **配置后端日志** [backend/utils/logger.py]
  ```python
  import logging
  import json
  from pythonjsonlogger import jsonlogger
  
  def setup_logging():
      logger = logging.getLogger()
      handler = logging.StreamHandler()
      formatter = jsonlogger.JsonFormatter()
      handler.setFormatter(formatter)
      logger.addHandler(handler)
      logger.setLevel(logging.INFO)
      return logger
  
  logger = setup_logging()
  
  # 使用方式
  logger.info("M1 diagnosis started", extra={
      "patient_id": pid,
      "symptoms_count": len(symptoms),
      "timestamp": time.time()
  })
  ```
- [ ] **配置前端日志**
  ```javascript
  class Logger {
      log(level, message, context = {}) {
          const entry = {
              timestamp: new Date().toISOString(),
              level,
              message,
              ...context
          };
          
          // 发送到后端日志服务
          if (navigator.sendBeacon) {
              navigator.sendBeacon('/api/logs', JSON.stringify(entry));
          }
          console.log(`[${level}]`, message, context);
      }
  }
  
  window.logger = new Logger();
  ```

### P3.2 单元测试框架
- [ ] **后端测试** [tests/test_m1_engine.py]
  ```python
  import pytest
  from core.m1_engine import M1DiagnosisEngine
  
  @pytest.fixture
  def m1():
      return M1DiagnosisEngine()
  
  def test_m1_basic_diagnosis(m1):
      result = m1.diagnose({
          "chief_complaint": "咳嗽",
          "symptoms": ["咳嗽", "咳痰"],
          "signs": [],
          "labs": [],
          "imaging": []
      })
      
      assert result is not None
      assert "calibrated_diagnosis" in result
      assert len(result["calibrated_diagnosis"]) > 0
  
  def test_m1_invalid_input(m1):
      with pytest.raises(ValueError):
          m1.diagnose({"symptoms": ["x" * 1000]})
  
  def test_m1_empty_input(m1):
      result = m1.diagnose({})
      assert result.get("error") is not None
  ```
- [ ] **前端测试** - 待定（推荐 Jest）

### P3.3 性能优化
- [ ] **M3 性能改进** [m3_engine.py]
  ```python
  class M3ClinicalReviewEngine:
      def __init__(self, ...):
          self.herb_name_map = {}
          self._build_normalized_map()
      
      def _build_normalized_map(self):
          """预构建标准化映射表，避免重复计算"""
          for std, variants in SYNONYM_MAP.items():
              for v in variants:
                  self.herb_name_map[v] = std
      
      def _normalize_herb_names(self, herbs: List[str]) -> List[str]:
          """O(n) 时间复杂度"""
          return [self.herb_name_map.get(h, h) for h in herbs]
  ```
- [ ] **M4 删除硬编码数据** [m4_engine.py]
  ```python
  def _get_disease_window(self, disease_name: str) -> dict:
      # 1. 检查知识库
      if disease_name in self.window_kb:
          return self.window_kb[disease_name]
      
      # 2. 使用默认值
      logger.warning(f"No window data for {disease_name}")
      return {"onset": 7, "significant": 14}
  ```
- [ ] **前端缓存** [js/api.js]
  ```javascript
  class ApiCache {
      constructor(ttl = 300000) {  // 5分钟
          this.cache = new Map();
          this.ttl = ttl;
      }
      
      get(key) {
          const item = this.cache.get(key);
          if (!item) return null;
          if (Date.now() - item.timestamp > this.ttl) {
              this.cache.delete(key);
              return null;
          }
          return item.value;
      }
      
      set(key, value) {
          this.cache.set(key, { value, timestamp: Date.now() });
      }
  }
  ```

### P3.4 代码重组织
- [ ] **创建新的目录结构**
  ```bash
  mkdir -p backend/{core,api,models,services,utils,tests}
  mkdir -p frontend/{components,api,utils,tests}
  ```
- [ ] **逐步迁移代码**
  - [ ] 移动引擎到 `backend/core/`
  - [ ] 移动服务到 `backend/services/`
  - [ ] 创建 `backend/api/server.py`
  - [ ] 创建 `backend/models/` 数据模型

---

## 优先级 4️⃣：持续改进（1-3个月）

### P4.1 高级监控
- [ ] 实现 Prometheus 指标收集
- [ ] 添加 APM（应用性能监控）
- [ ] 设置告警规则

### P4.2 医学规则升级
- [ ] 审查和更新 M1-M4 的诊断规则
- [ ] 与医学专家验证

### P4.3 前端 UX 优化
- [ ] 改进加载状态显示
- [ ] 添加进度指示
- [ ] 优化响应式设计

---

## 📊 进度跟踪模板

```markdown
### 周报 - 2026年6月1日

**本周完成**
- [x] P1.1 轮换 API 密钥
- [x] P1.2 修改 CORS 策略
- [ ] P1.3 请求体大小限制 (进行中)

**本周遇到的问题**
- 需要通知所有使用 API 的客户端更新密钥

**下周计划**
- 完成 P1.3 和 P1.4
- 开始 P2.1 错误处理

**风险**
- 无
```

---

## 🚀 快速启动指令

### 第一周建议任务列表
```bash
# 1. 创建环境配置
cp .env.example .env
# 编辑 .env，填入真实的 API 密钥

# 2. 更新代码加载方式
# 编辑 bridge_server.py 和 full_pipeline.py

# 3. 测试部署
python bridge_server.py

# 4. 验证
curl http://localhost:6005/health
```

---

**最后更新**：2026年5月31日  
**维护者**：GitHub Copilot  
**审计周期**：每月
