
    // ======== 基本配置 ========
    // 从配置文件读取服务器IP（localData/server_ip.txt）
    const SERVER_IP = window.SERVER_CONFIG?.ip || 'localhost';
    const WS_PORT = window.SERVER_CONFIG?.wsPort || 8765;
    function resolveWsUrl() {
      const configured = String(window.SERVER_CONFIG?.wsUrl || '').trim();
      if (configured) return configured;
      const configuredPath = String(window.SERVER_CONFIG?.wsPath || '').trim();
      const wsPath = configuredPath || '/ws/';
      if (location.protocol === 'https:') {
        return `wss://${location.host}${wsPath.startsWith('/') ? wsPath : `/${wsPath}`}`;
      }
      return `ws://${SERVER_IP}:${WS_PORT}`;
    }
    const WS_URL = resolveWsUrl();
    function resolveApiBase(configuredBase) {
      const raw = String(configuredBase || '').trim();
      if (raw) {
        const normalized = raw.replace(/\/+$/, '');
        if (location.protocol === 'https:' && /^http:\/\//i.test(normalized)) {
          try {
            const url = new URL(normalized);
            if (!['localhost', '127.0.0.1', '::1'].includes(url.hostname)) {
              return `${location.origin}/api`;
            }
          } catch (e) {
            return `${location.origin}/api`;
          }
        }
        return normalized;
      }
      if (location.protocol === 'http:' || location.protocol === 'https:') {
        if (location.protocol === 'https:') return `${location.origin}/api`;
        return `${location.protocol}//${location.hostname}:6005`;
      }
      return 'http://114.132.188.240:6005';
    }
    const API_BASE = resolveApiBase(window.SERVER_CONFIG?.apiBase);



    let ws = null;
    let heartbeatInterval = null; // 心跳定时器
    let reconnectTimer = null; // 重连定时器
    let reconnectAttempts = 0; // 重连尝试次数
    const MAX_RECONNECT_ATTEMPTS = 8; // 最大重连次数
    const RECONNECT_DELAY_BASE = 2000; // 基础重连延迟（毫秒）
    const RECONNECT_DELAY_MAX = 30000; // 最大重连延迟（毫秒）
    const HEARTBEAT_INTERVAL = 30000; // 心跳间隔30秒（后端ping_interval是120秒，前端30秒更安全）

    let activeMode = 'clinic'; // clinic: 患者问诊模式；corpus: 数据库闲聊模式

    // AI助手频道定时刷新定时器
    let aiAssistantRefreshTimer = null;
    let clinicChatlogRefreshTimer = null;

    const CLIENT_DB_META = {
      hanfang: { client_id: 'hanfang', internal_db: 'hanfang', default_db: '汉方方案', display_name: '汉方', corpus_db_id: 'db-02' },
      qingda: { client_id: 'qingda', internal_db: 'qingda', default_db: '清大方案', display_name: '清大', corpus_db_id: 'db-04' },
      koufu: { client_id: 'koufu', internal_db: 'koufu', default_db: '口服汤剂', display_name: '口服', corpus_db_id: 'db-01' },
      buchang: { client_id: 'buchang', internal_db: 'buchang', default_db: '步长方案', display_name: '步长', corpus_db_id: 'db-03' }
    };

    function normalizeClientKey(value) {
      return String(value || '').trim().toLowerCase();
    }

    function getClientMeta() {
      const cfg = window.CLIENT_CONFIG || {};
      const candidates = [cfg.client_id, cfg.internal_db].map(normalizeClientKey).filter(Boolean);
      const cfgDefaultDb = String(cfg.default_db || '').trim();
      if (cfgDefaultDb) {
        Object.keys(CLIENT_DB_META).forEach(key => {
          if (CLIENT_DB_META[key].default_db === cfgDefaultDb) candidates.unshift(key);
        });
      }
      for (const key of candidates) {
        if (CLIENT_DB_META[key]) return CLIENT_DB_META[key];
      }
      return CLIENT_DB_META.hanfang;
    }

    function getCurrentClientId() {
      const cfg = window.CLIENT_CONFIG || {};
      const clientId = normalizeClientKey(cfg.client_id);
      if (clientId && CLIENT_DB_META[clientId]) {
        return clientId;
      }
      const internalDb = normalizeClientKey(cfg.internal_db);
      if (internalDb && CLIENT_DB_META[internalDb]) {
        return internalDb;
      }
      return getClientMeta().client_id;
    }

    function getClientDefaultDb() {
      if (window.CLIENT_CONFIG && window.CLIENT_CONFIG.default_db) {
        return String(window.CLIENT_CONFIG.default_db).trim() || getClientMeta().default_db;
      }
      return getClientMeta().default_db;
    }

    function getCorpusDefaultDbId() {
      const map = {
        '汉方方案': 'db-02',
        '口服汤剂': 'db-01',
        '清大方案': 'db-04',
        '步长方案': 'db-03'
      };
      return map[getClientDefaultDb()] || getClientMeta().corpus_db_id || 'db-02';
    }

    function normalizeClinicDbChoice(dbName, fallbackToDefault = true) {
      const rawDbName = String(dbName || '').trim();
      const clientDefaultDb = getClientDefaultDb();
      if (!rawDbName) return fallbackToDefault ? clientDefaultDb : '';
      if (rawDbName === clientDefaultDb || rawDbName === '用户自定义' || rawDbName === '通识库1') {
        return rawDbName;
      }
      console.warn(`[DEBUG] 忽略非当前客户数据库选择: ${rawDbName} -> ${clientDefaultDb}`);
      return fallbackToDefault ? clientDefaultDb : '';
    }

    function getClientStorageKey(baseKey) {
      return `${baseKey}_${getCurrentClientId()}`;
    }

    function getApiPatientId(patientId) {
      const rawPatientId = String(patientId || '').trim();
      if (!rawPatientId) return '';
      return rawPatientId === 'common' ? `common__${getCurrentClientId()}` : rawPatientId;
    }

    // 患者问诊模式：开方数据库选择（从localStorage读取，默认从 CLIENT_CONFIG）
    let selectedClinicDb = normalizeClinicDbChoice(getClientDefaultDb());

    /** 与 config/client_configs.json 中各库 display_name 一致，用于工作台标题等（非接口字段） */
    const CLINIC_DB_LINE_DISPLAY = {
      '汉方方案': '汉方',
      '清大方案': '清大',
      '口服汤剂': '口服',
      '步长方案': '步长'
    };

    function getClinicDbLineDisplayName(dbName) {
      if (!dbName) dbName = selectedClinicDb;
      var cfg = window.CLIENT_CONFIG;
      if (cfg && dbName && cfg.default_db && dbName === cfg.default_db && cfg.display_name) {
        return cfg.display_name;
      }
      if (dbName && CLINIC_DB_LINE_DISPLAY[dbName]) return CLINIC_DB_LINE_DISPLAY[dbName];
      if (dbName === '用户自定义') {
        var cfg0 = window.CLIENT_CONFIG;
        return (cfg0 && cfg0.display_name) ? cfg0.display_name : '自定义';
      }
      return (cfg && cfg.display_name) ? cfg.display_name : getClientMeta().display_name;
    }

    function getWorkbenchMainTitle() {
      return getClinicDbLineDisplayName() + '智能体质评估台';
    }

    /** 当前选中开方库对应的对外文书名（调理笺），与 client_configs 各库的 prescription_title 对齐 */
    function getPrescriptionTitle(dbName) {
      if (!dbName) dbName = selectedClinicDb;
      var cfg = window.CLIENT_CONFIG;
      var byDb = {
        '汉方方案': '调理笺',
        '清大方案': '调理笺',
        '口服汤剂': '调理笺',
        '步长方案': '调理笺'
      };
      if (dbName && Object.prototype.hasOwnProperty.call(byDb, dbName)) {
        return byDb[dbName];
      }
      if (dbName === '用户自定义' && cfg && cfg.prescription_title) {
        return cfg.prescription_title;
      }
      if (cfg && dbName && (dbName === cfg.default_db || (cfg.display_name && dbName.includes(cfg.display_name)) || (cfg.internal_db && String(dbName).toLowerCase().includes(cfg.internal_db)))) {
        return cfg.prescription_title || '方案';
      }
      return (cfg && cfg.prescription_title) || '方案';
    }

    /** 根据当前客户配置库刷新：顶栏工作台名、展示文书按钮、生成文书按钮 */
    function updateClinicDependentLabels() {
      var wt = document.getElementById('header-workbench-title');
      if (wt) wt.textContent = getWorkbenchMainTitle();
      updatePrescriptionButtonText();
      var pt = getPrescriptionTitle(selectedClinicDb);
      var lg = document.getElementById('btn-generate-treatment-text-lg');
      var md = document.getElementById('btn-generate-treatment-text-md');
      var sm = document.getElementById('btn-generate-treatment-text-sm');
      if (lg) lg.textContent = '结束问答并生成' + pt;
      if (md) md.textContent = '生成' + pt;
      if (sm) sm.textContent = '生成' + pt;
    }

    // 患者问诊模式：模型选择（默认deepseek-chat）
    let selectedClinicModel = 'deepseek-chat'; // 默认选择deepseek-chat

    // 真实数据：近期问诊患者（登录后从后端获取，失败时不再注入默认示例）
    const clinicFallback = [];
    let clinicSessions = [];  // 当前显示的患者列表（可能是搜索后的结果）
    let allPatients = [];  // 所有患者（从后端获取的完整列表）
    let clinicLoading = false;
    let patientSearchQuery = '';  // 当前搜索关键词



    // 医知快答仅保留顶部「医生自有数据库」勾选，下列列表不再展示
    const corpusSources = [];



    // 数据库多选状态：存储已选中的数据库 id 集合

    let activeCorpusSelection = new Set();

    // 用户上传的文件列表：存储用户上传的文件信息
    let userUploadedFiles = []; // [{file_id: str, filename: str, upload_time: number}]

    // 联网搜索开关状态（医知快答模式）
    let onlineSearchEnabled = false;

    // 复诊流程状态
    let isInFollowupFlow = false;  // 是否处于复诊流程中
    let followupSymptom = '';  // 复诊时输入的新症状



    // 问诊模式下左侧的选择状态：{ type: 'patient' | 'new', id?: string }

    // 问诊模式下左侧的选择状态：{ type: 'patient' | 'new', id?: string }
    window.activeClinicSelection = window.activeClinicSelection || { type: 'new', id: null };

    // 页面加载时立即从 localStorage 恢复上次选中的患者，避免刷新后选中项丢失
    try {
      const lastId = localStorage.getItem(getClientStorageKey('last_selected_patient_id'));
      if (lastId && String(lastId).trim()) {
        window.activeClinicSelection = { type: 'patient', id: lastId.trim() };
      }
    } catch (e) { /* ignore */ }

    // 从localStorage恢复上次选中的患者ID（syncPatients 内会再次调用以与列表合并）
    function loadLastSelectedPatient() {
      try {
        const lastPatientId = localStorage.getItem(getClientStorageKey('last_selected_patient_id'));
        if (lastPatientId) {
          window.activeClinicSelection = { type: 'patient', id: lastPatientId };
        }
      } catch (e) {
        console.error('加载上次选中的患者失败:', e);
      }
    }

    // 保存当前选中的患者ID到localStorage
    function saveLastSelectedPatient(patientId) {
      try {
        if (patientId) {
          localStorage.setItem(getClientStorageKey('last_selected_patient_id'), patientId);
        } else {
          localStorage.removeItem(getClientStorageKey('last_selected_patient_id'));
        }
      } catch (e) {
        console.error('保存上次选中的患者失败:', e);
      }
    }

    // 初始化：加载上次选中的患者
    loadLastSelectedPatient();



    let currentUser = {

      phone: 'demo',

      token: 'demo-token',

      chatlogId: '0'

    };



    const messages = [];

    // 病人消息缓存：{ patient_id: [messages] }
    let patientMessagesCache = {};
    // 参考文献侧边栏缓存：当前患者最近10条
    let sidebarReferences = [];

    // 当前病人的ID
    window.currentPatientId = window.currentPatientId || null;

    function getPatientById(patientId) {
      const id = String(patientId || '').trim();
      if (!id) return null;
      return clinicSessions.find(p => p && p.id === id) ||
        allPatients.find(p => p && p.id === id) ||
        null;
    }

    function getTreatmentTimeline(detail) {
      const d = detail && typeof detail === 'object' ? detail : {};
      return Array.isArray(d.treatment_timeline)
        ? d.treatment_timeline
        : (Array.isArray(d.timeline) ? d.timeline : []);
    }

    function getFormulaNameFromPlan(plan) {
      if (!plan || typeof plan !== 'object') return '';
      return plan.辨证选方 || plan.方剂 || plan.方剂名称 || plan.formula || plan.formula_name || plan.prescription || '';
    }

    function visitHasTreatmentRecord(visit) {
      if (!visit || typeof visit !== 'object') return false;
      const treatment = visit.treatment && typeof visit.treatment === 'object' ? visit.treatment : null;
      const diseases = Array.isArray(visit.diseases)
        ? visit.diseases
        : (treatment && Array.isArray(treatment.diseases) ? treatment.diseases : []);
      const plan = visit.治疗方案 || visit.treatment_plan || (treatment && (treatment.治疗方案 || treatment.treatment_plan));
      return visit.treatment_completed === true ||
        !!treatment ||
        !!(visit.disease_name || visit.syndrome || visit.prescription || visit.formula_name || visit.方剂名称) ||
        diseases.length > 0 ||
        !!getFormulaNameFromPlan(plan);
    }

    function buildTreatmentFromVisit(visit, detailInfo = {}) {
      if (!visit || typeof visit !== 'object') return null;
      const treatment = visit.treatment && typeof visit.treatment === 'object' ? { ...visit.treatment } : {};
      treatment.timestamp = treatment.timestamp || visit.timestamp;
      treatment.database = treatment.database || visit.database || detailInfo.database || getClientDefaultDb();
      treatment.disease_name = treatment.disease_name || visit.disease_name || '';
      treatment.syndrome = treatment.syndrome || visit.syndrome || '';
      treatment.prescription = treatment.prescription || visit.prescription || visit.formula_name || '';

      if (!Array.isArray(treatment.diseases) || treatment.diseases.length === 0) {
        if (Array.isArray(visit.diseases) && visit.diseases.length > 0) {
          treatment.diseases = visit.diseases;
        }
      }

      if (!Array.isArray(treatment.diseases) || treatment.diseases.length === 0) {
        const plan = visit.治疗方案 || visit.treatment_plan || treatment.治疗方案 || treatment.treatment_plan || {};
        const formulaName = treatment.prescription || getFormulaNameFromPlan(plan);
        if (treatment.disease_name || treatment.syndrome || formulaName || (plan && Object.keys(plan).length > 0)) {
          treatment.prescription = formulaName || treatment.prescription || '';
          treatment.diseases = [{
            病名: treatment.disease_name || '',
            name: treatment.disease_name || '',
            证型: treatment.syndrome || '',
            syndrome: treatment.syndrome || '',
            prescription: treatment.prescription || '',
            formula_name: treatment.prescription || '',
            治疗方案: plan && Object.keys(plan).length > 0
              ? plan
              : (treatment.prescription ? { 辨证选方: treatment.prescription, 方剂: treatment.prescription } : {})
          }];
        }
      }

      return visitHasTreatmentRecord(visit) || (Array.isArray(treatment.diseases) && treatment.diseases.length > 0)
        ? treatment
        : null;
    }

    // 最近一次发送消息时的患者ID（用于跟踪等待回复的消息属于哪个患者）
    let pendingPatientId = null;

    // 从localStorage加载病人消息缓存
    function loadPatientMessagesCache() {
      try {
        const cached = localStorage.getItem(getClientStorageKey('patient_messages_cache'));
        if (cached) {
          patientMessagesCache = JSON.parse(cached);
        }
      } catch (e) {
        console.error('加载病人消息缓存失败:', e);
        patientMessagesCache = {};
      }
    }

    // 保存病人消息缓存到localStorage
    function savePatientMessagesCache() {
      try {
        localStorage.setItem(getClientStorageKey('patient_messages_cache'), JSON.stringify(patientMessagesCache));
      } catch (e) {
        console.error('保存病人消息缓存失败:', e);
      }
    }

    async function fetchPatientChatlog(patientId) {
      if (!currentUser.phone) return [];
      const apiPatientId = getApiPatientId(patientId || '');
      const data = await fetchJson(`${API_BASE}/get_patients_chatlog/${encodeURIComponent(apiPatientId)}`, {
        method: 'POST',
        body: JSON.stringify({ doctor_id: currentUser.phone })
      });
      return Array.isArray(data.messages) ? data.messages : [];
    }

    function normalizeChatlogMessages(rawMessages) {
      const uniq = new Map(); // dedupeKey -> msg（每个 key 只保留一条，保证同一 uuid 只显示一次）
      const sorted = Array.isArray(rawMessages) ? rawMessages.slice() : [];
      sorted.sort((a, b) => {
        const ta = (a && typeof a.timestamp === 'number') ? a.timestamp : 0;
        const tb = (b && typeof b.timestamp === 'number') ? b.timestamp : 0;
        return ta - tb;
      });

      sorted.forEach((m, index) => {
        if (!m || typeof m !== 'object') return;
        const rawUuid = (m.chatuuid || m.uuid || '').toString().trim();
        const type = m.type || 'text';
        // 服务端约定 from 为 user/system；缺失或非法时按 user 处理，避免用户输入被当成助手或丢弃
        const from = (m.from === 'user' || m.from === 'system') ? m.from : 'user';
        const role = from === 'user' ? 'user' : 'assistant';
        const t = m.timestamp;
        const ts = t != null && t !== 0
          ? (t >= 1e12 ? new Date(t) : new Date(t * 1000))
          : new Date();

        let content = {};
        if (type === 'image') {
          content = normalizeMessageContent({ text: '', imgurl: typeof m.content === 'string' ? m.content : '' }, role);
        } else if (type === 'selection_q') {
          content = normalizeMessageContent({ selection_q: (m.content && typeof m.content === 'object') ? m.content : { q: '', sel: [] } }, role);
        } else {
          content = normalizeMessageContent({
            text: typeof m.content === 'string' ? m.content : (m.content && typeof m.content === 'object' && m.content.text != null ? m.content.text : '')
          }, role);
        }

        // 去重 key：有 chatuuid/uuid 则只用它（同 uuid 只保留第一条）；无则用 fallback，对 selection_q 用内容 key 避免重复显示
        let dedupeKey = rawUuid;
        if (!dedupeKey) {
          if (type === 'selection_q' && content.selection_q && typeof content.selection_q === 'object') {
            const q = (content.selection_q.q || '').trim();
            const sel = Array.isArray(content.selection_q.sel) ? content.selection_q.sel : [];
            dedupeKey = `sel_${q.slice(0, 80)}_${sel.join(',')}`;
          } else {
            dedupeKey = `msg-${index}-${m.timestamp ?? index}`;
          }
        }
        if (uniq.has(dedupeKey)) return; // 同一 key 只保留一条
        const uuid = rawUuid || dedupeKey;
        uniq.set(dedupeKey, { role, type, uuid, content, ts });
      });
      return Array.from(uniq.values());
    }

    // 切换病人时，保存当前病人消息并加载新病人消息（按 uuid 去重，云端优先）
    async function switchPatientMessages(newPatientId, patientData = null) {
      // 保存当前病人的消息（确保在切换前保存）
      if (window.currentPatientId && window.currentPatientId !== newPatientId) {
        // 始终保存当前消息状态到缓存
        patientMessagesCache[window.currentPatientId] = messages.map(msg => {
          const msgCopy = { ...msg };
          // 确保content字段格式正确（只有当content是字符串时才转换，不要覆盖已有的对象）
          if (msgCopy.content && typeof msgCopy.content === 'string') {
            msgCopy.content = { text: msgCopy.content };
          } else if (!msgCopy.content) {
            // 如果没有content字段，创建一个空对象
            msgCopy.content = {};
          }
          // 确保时间戳是可序列化的
          if (msgCopy.ts instanceof Date) {
            msgCopy.ts = msgCopy.ts.toISOString();
          }
          return msgCopy;
        });
        savePatientMessagesCache();
        console.log(`[DEBUG] 保存患者[${window.currentPatientId}]的消息，共${messages.length}条`);
      }

      // 切换到新病人
      const oldPatientId = window.currentPatientId;
      window.currentPatientId = newPatientId;

      // 加载新病人的消息
      // 优先级：1. 云端 chatlog（新接口），2. localStorage 缓存
      messages.length = 0;

      let loaded = false;
      try {
        const raw = await fetchPatientChatlog(newPatientId);
        // 调试：打印收到的聊天记录，确认是否有 chatuuid/uuid、是否有重复
        console.log(`[DEBUG] 收到云端聊天记录 条数=${raw.length}`, raw.map((m, i) => ({
          i,
          chatuuid: m && m.chatuuid,
          uuid: m && m.uuid,
          type: m && m.type,
          from: m && m.from,
          ts: m && m.timestamp,
          contentPreview: m && m.content != null ? (typeof m.content === 'string' ? m.content.slice(0, 40) : JSON.stringify(m.content).slice(0, 60)) : ''
        })));
        const cloudMessages = normalizeChatlogMessages(raw);
        console.log(`[DEBUG] 归一化后 条数=${cloudMessages.length} uuid列表=`, cloudMessages.map(m => m.uuid));
        if (cloudMessages.length > 0) {
          // 合并缓存：云端优先，再追加缓存中“云端没有”的消息（解决“刚发的提问切换回来不见”）
          const cloudIds = new Set(cloudMessages.map(m => String((m && m.uuid) || '').trim()));
          const normText = (msg) => String((msg && msg.content && msg.content.text) || '').trim();
          const cached = (newPatientId && patientMessagesCache[newPatientId]) ? patientMessagesCache[newPatientId] : [];
          const cacheOnly = cached.filter(m => {
            if (!m || typeof m !== 'object') return false;
            const uid = String((m.uuid || '').trim());
            if (uid && !cloudIds.has(uid)) return true;
            // 无 uuid 的缓存条：只要云端没有同角色+同内容的重复项就保留，避免刷新/重登/切页后用户内容丢失
            if (!uid) {
              const role = m.role || 'user';
              const text = normText(m);
              const sameInCloud = cloudMessages.some(c => (c.role || '') === role && normText(c) === text);
              return !sameInCloud;
            }
            return false;
          }).map(msg => {
            const m = { ...msg };
            if (m.ts && typeof m.ts === 'string') {
              const d = new Date(m.ts);
              m.ts = !isNaN(d.getTime()) ? d : m.ts;
            }
            if (m.content && typeof m.content === 'string') m.content = { text: m.content };
            if (!m.content) m.content = {};
            return m;
          });
          const merged = [...cloudMessages, ...cacheOnly];
          const getTs = (msg) => (msg && msg.ts != null) ? (msg.ts instanceof Date ? msg.ts.getTime() : (typeof msg.ts === 'number' ? (msg.ts < 1e12 ? msg.ts * 1000 : msg.ts) : new Date(msg.ts).getTime() || 0)) : 0;
          merged.sort((a, b) => getTs(a) - getTs(b));
          messages.push(...merged);
          loaded = true;
          patientMessagesCache[newPatientId] = merged.map(m => ({
            role: m.role,
            type: m.type,
            uuid: m.uuid,
            content: m.content,
            ts: (m.ts instanceof Date) ? m.ts.toISOString() : m.ts
          }));
          savePatientMessagesCache();
          if (cacheOnly.length > 0) console.log(`[DEBUG] 从云端加载患者[${newPatientId}]并合并缓存${cacheOnly.length}条，共${merged.length}条`);
          else console.log(`[DEBUG] 从云端加载患者[${newPatientId}]的聊天记录，共${cloudMessages.length}条`);
        }
      } catch (e) {
        console.warn(`[DEBUG] 从云端加载患者[${newPatientId}]聊天记录失败:`, e);
      }

      // 如果云端没有加载到，尝试从 localStorage 缓存读取
      if (!loaded && newPatientId && patientMessagesCache[newPatientId] && patientMessagesCache[newPatientId].length > 0) {
        console.log(`[DEBUG] 从localStorage缓存加载患者[${newPatientId}]的消息，共${patientMessagesCache[newPatientId].length}条`);

        // 恢复消息时，确保时间戳格式正确
        const cachedMessages = patientMessagesCache[newPatientId].map(msg => {
          // 深拷贝消息对象，避免修改缓存
          const msgCopy = { ...msg };
          if (msgCopy.ts && typeof msgCopy.ts === 'string') {
            // 将字符串时间戳转换为 Date 对象（如果可能）
            const date = new Date(msgCopy.ts);
            if (!isNaN(date.getTime())) {
              msgCopy.ts = date;
            }
          }
          // 确保content字段正确（只有当content是字符串时才转换，不要覆盖已有的对象）
          if (msgCopy.content && typeof msgCopy.content === 'string') {
            msgCopy.content = { text: msgCopy.content };
          } else if (!msgCopy.content) {
            // 如果没有content字段，创建一个空对象
            msgCopy.content = {};
          }
          return msgCopy;
        });
        messages.push(...cachedMessages);
      } else if (newPatientId === 'common' && !patientData) {
        // 特殊处理：AI助手频道，如果patientData为null，尝试从后端获取
        console.log(`[DEBUG] 切换到AI助手频道，尝试从后端获取聊天记录`);
        loadAiAssistantMessages().then(aiAssistantData => {
          if (aiAssistantData && aiAssistantData.detail && aiAssistantData.detail.chatlog) {
            // 如果获取到数据，重新调用 switchPatientMessages 并传入数据
            switchPatientMessages('common', aiAssistantData);
          }
        }).catch(err => {
          console.warn(`[DEBUG] 获取AI助手频道聊天记录失败: ${err}，将使用缓存`);
        });
      } else if (!loaded) {
        // 云端和缓存都没有加载到，才视为无聊天记录并清空（已从云端加载时不要清空，否则会抹掉刚加载的 12 条）
        console.log(`[DEBUG] 患者[${newPatientId}]没有聊天记录`);
        messages.length = 0;
      }

      // 如果newPatientId为null或特殊值，确保清空消息
      if (!newPatientId || newPatientId === 'null' || newPatientId === 'undefined') {
        messages.length = 0;
      }

      renderMessages();

      // 切换患者后，根据该患者的消息重建参考文献列表
      updateSidebarReferencesFromMessages();
    }

    // 初始化：加载缓存
    loadPatientMessagesCache();

    function patientSummary(patient) {
      if (!patient) return '';
      if (typeof patient.summary === 'string' && patient.summary) return patient.summary;
      if (typeof patient.memo === 'string' && patient.memo) return patient.memo;
      const detail = patient.detail || {};
      if (typeof detail.symptom === 'string' && detail.symptom) return detail.symptom;
      return '';
    }

    // 获取患者首诊时间（从treatment_timeline第一条记录）
    function getFirstVisitTime(patient) {
      const detail = patient.detail || {};
      const timeline = Array.isArray(detail.treatment_timeline)
        ? detail.treatment_timeline
        : (Array.isArray(detail.timeline) ? detail.timeline : []);

      if (timeline.length > 0 && timeline[0].timestamp) {
        return timeline[0].timestamp;
      }

      // 如果没有timeline，尝试从patient_id中提取时间戳（格式：doctor_id_timestamp）
      const patientId = patient.patient_id || patient.id || '';
      const parts = patientId.split('_');
      if (parts.length > 1) {
        const lastPart = parts[parts.length - 1];
        const ts = parseInt(lastPart);
        if (!isNaN(ts) && ts > 1000000000) {
          // 如果是秒时间戳，转换为毫秒
          return ts < 1000000000000 ? ts * 1000 : ts;
        }
      }

      return null;
    }

    // 获取患者最后就诊时间（从treatment_timeline最后一条记录）
    function getLastVisitTime(patient) {
      const detail = patient.detail || {};
      const timeline = Array.isArray(detail.treatment_timeline)
        ? detail.treatment_timeline
        : (Array.isArray(detail.timeline) ? detail.timeline : []);

      if (timeline.length > 0) {
        // 从后往前找，找到最后一条有timestamp的记录
        for (let i = timeline.length - 1; i >= 0; i--) {
          if (timeline[i].timestamp) {
            return timeline[i].timestamp;
          }
        }
      }

      return null;
    }

    // 格式化日期为简短格式（月/日）
    function formatDateShort(timestamp) {
      if (!timestamp) return '-';
      try {
        const ts = typeof timestamp === 'number'
          ? (timestamp < 1000000000000 ? timestamp * 1000 : timestamp)
          : parseInt(timestamp) * (timestamp < 1000000000000 ? 1000 : 1);
        const date = new Date(ts);
        if (isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit'
        });
      } catch (e) {
        return '-';
      }
    }

    // 格式化相对时间（如"3天前"、"1周前"等）
    function formatRelativeTime(timestamp) {
      if (!timestamp) return '-';
      try {
        const ts = typeof timestamp === 'number'
          ? (timestamp < 1000000000000 ? timestamp * 1000 : timestamp)
          : parseInt(timestamp) * (timestamp < 1000000000000 ? 1000 : 1);
        const date = new Date(ts);
        if (isNaN(date.getTime())) return '-';

        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        const diffWeeks = Math.floor(diffDays / 7);
        const diffMonths = Math.floor(diffDays / 30);
        const diffYears = Math.floor(diffDays / 365);

        if (diffMins < 1) {
          return '刚刚';
        } else if (diffMins < 60) {
          return `${diffMins}分钟前`;
        } else if (diffHours < 24) {
          return `${diffHours}小时前`;
        } else if (diffDays < 7) {
          return `${diffDays}天前`;
        } else if (diffWeeks < 4) {
          return `${diffWeeks}周前`;
        } else if (diffMonths < 12) {
          return `${diffMonths}个月前`;
        } else {
          return `${diffYears}年前`;
        }
      } catch (e) {
        return '-';
      }
    }

    // 获取患者就诊次数
    function getVisitCount(patient) {
      const detail = patient.detail || {};
      const timeline = Array.isArray(detail.treatment_timeline)
        ? detail.treatment_timeline
        : (Array.isArray(detail.timeline) ? detail.timeline : []);

      // 统计有timestamp的记录数
      let count = 0;
      for (let i = 0; i < timeline.length; i++) {
        if (timeline[i].timestamp) {
          count++;
        }
      }

      // 如果没有timeline但有首诊时间，至少算1次
      if (count === 0 && getFirstVisitTime(patient)) {
        count = 1;
      }

      return count;
    }

    // 高亮主诉关键词
    function highlightSymptomKeywords(text) {
      if (!text) return '';

      // 常见症状关键词列表
      const keywords = [
        '咳嗽', '发热', '头痛', '腹痛', '腹泻', '便秘', '失眠', '心悸',
        '胸闷', '气短', '乏力', '头晕', '恶心', '呕吐', '食欲', '口干',
        '口苦', '咽痛', '鼻塞', '流涕', '关节', '肌肉', '皮肤', '瘙痒',
        '疼痛', '肿胀', '出血', '月经', '白带', '尿频', '尿急', '腰痛',
        '背痛', '胸痛', '腹痛', '胃痛', '肝痛', '肾痛', '眼痛', '耳痛'
      ];

      let highlightedText = escapeHtml(text);

      // 对每个关键词进行高亮
      keywords.forEach(keyword => {
        const regex = new RegExp(`(${keyword})`, 'gi');
        highlightedText = highlightedText.replace(regex, '<span class="font-semibold text-medical-green">$1</span>');
      });

      return highlightedText;
    }

    function mapPatientForUI(p) {
      return {
        id: p.patient_id || p.id,
        name: p.name || '未命名',
        summary: patientSummary(p),
        age: p.age || '',
        gender: p.gender,
        memo: p.memo || '',
        detail: p.detail || {},
        lastVisit: p.lastVisit || p.last_visit || getLastVisitTime(p),  // 上次就诊时间
        firstVisit: getFirstVisitTime(p),  // 首诊时间
        visitCount: getVisitCount(p)  // 就诊次数
      };
    }

    // 格式化日期时间
    function formatDateTime(timestamp) {
      if (!timestamp) return '-';
      try {
        const date = new Date(timestamp);
        if (isNaN(date.getTime())) {
          // 如果是数字格式的时间戳
          const ts = typeof timestamp === 'string' ? parseInt(timestamp) : timestamp;
          if (ts > 1000000000000) {
            // 毫秒时间戳
            return new Date(ts).toLocaleString('zh-CN', {
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit'
            });
          } else if (ts > 1000000000) {
            // 秒时间戳
            return new Date(ts * 1000).toLocaleString('zh-CN', {
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit'
            });
          }
        } else {
          return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
          });
        }
      } catch (e) {
        return '-';
      }
      return '-';
    }

    // 精简时间显示：月/日 时:分
    function formatDateTimeShort(timestamp) {
      if (!timestamp) return '-';
      try {
        const date = new Date(timestamp);
        if (isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit'
        });
      } catch (e) {
        return '-';
      }
    }

    function isToday(ts) {
      if (!ts) return false;
      const d = new Date(ts);
      const now = new Date();
      return d.getFullYear() === now.getFullYear() &&
             d.getMonth() === now.getMonth() &&
             d.getDate() === now.getDate();
    }

    function latestTimelineTs(detail) {
      if (!detail || typeof detail !== 'object') return null;
      // 使用新的 treatment_timeline 结构（每次问诊独立一条），向后兼容旧的 timeline
      const timeline = Array.isArray(detail.treatment_timeline)
        ? detail.treatment_timeline
        : (Array.isArray(detail.timeline) ? detail.timeline : []);
      if (timeline.length === 0) return null;
      // 返回最后一次就诊的时间戳（新的结构使用 timestamp 秒级时间戳）
      const lastVisit = timeline[timeline.length - 1];
      if (lastVisit.timestamp) {
        return lastVisit.timestamp * 1000; // 转成毫秒
      }
      // 旧结构可能使用 visit_date（毫秒）
      return lastVisit.visit_date || null;
    }

    function hideTimeline() {
      const container = document.getElementById('patient-timeline');
      const content = document.getElementById('patient-timeline-content');
      if (container) container.classList.add('hidden');
      if (content) content.innerHTML = '';
    }

    function truncateText(text, maxLength = 120) {
      if (!text) return '';
      if (text.length <= maxLength) return text;
      return text.slice(0, maxLength) + '…';
    }

    function renderTimeline(detail) {
      // 这个函数已不再使用，因为时间线已经改为右侧侧边栏显示
      // 保留此函数以防其他地方调用，但不做任何操作
      hideTimeline();
    }

    // 更新患者信息显示栏
