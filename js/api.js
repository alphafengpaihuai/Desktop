    async function fetchJson(url, options = {}) {
      // 演示模式：当未连接后端时返回空数据
      if (window._mockDataInjected) {
        console.log('[演示模式] fetchJson 已拦截:', url);
        // 根据 URL 返回合适的空数据结构
        if (url.includes('/patients')) {
          return { patients: [] };
        }
        if (url.includes('/chatlog')) {
          return { messages: [] };
        }
        if (url.includes('/patient_detail')) {
          return { detail: {} };
        }
        return { ok: true, data: {} };
      }

      // 检查登录状态
      if (!ensureLoggedIn()) {
        throw new Error('未登录，请先登录');
      }

      const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
      if (currentUser.token) {
        headers['Authorization'] = `Bearer ${currentUser.token}`;
      }
      const resp = await fetch(url, {
        credentials: 'include',
        ...options,
        headers
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (e) {
        throw new Error('无法解析服务端返回');
      }

      // 处理认证错误（401/403），自动跳转到登录页
      if (resp.status === 401 || resp.status === 403) {
        appendMessage('system', '登录已过期，正在跳转到登录页面...');
        setTimeout(() => {
          redirectToLogin();
        }, 500);
        throw new Error('登录已过期，请重新登录');
      }

      if (resp.status !== 200 || data.ok === false) {
        throw new Error(data.error || `请求失败，状态码 ${resp.status}`);
      }
      return data;
    }

    async function syncPatients(showMsg = false) {
      if (!currentUser.phone) {
        clinicSessions = [];
        allPatients = [];
        activeClinicSelection = { type: 'new' };
        renderClinicPanel();
        updateClinicDetailUI();
        return;
      }
      clinicLoading = true;
      try {
        const data = await fetchJson(`${API_BASE}/patients?doctor_id=${encodeURIComponent(currentUser.phone)}`, { method: 'GET' });
        // 注意：不要清空 detail！
        // detail 内含格式化问诊记录（last_treatment / treatment_timeline / symptom_record 等），调理笺依赖它。
        // “懒加载”只针对聊天记录 chatlog（已改为新接口按 patient 单独拉取），以及选中患者时可 refresh 获取最新 detail。
        const patients = Array.isArray(data.patients) ? data.patients : [];
        const filtered = patients
          .map(p => mapPatientForUI(p))
          .filter(p => {
            const detail = p && typeof p.detail === 'object' ? p.detail : {};
            const taggedClientId = String(
              detail.client_id || detail.clientId || p.client_id || p.clientId || ''
            ).trim();
            return !taggedClientId || taggedClientId === getCurrentClientId();
          });
        allPatients = Array.isArray(filtered) ? filtered : [];  // 保存所有患者
        console.log(`[DEBUG] syncPatients: 获取到${allPatients.length}个患者`, allPatients);
        // 应用当前搜索（如果有）
        applyPatientSearch(patientSearchQuery);
        console.log(`[DEBUG] syncPatients: applyPatientSearch后，clinicSessions.length=${clinicSessions.length}`, clinicSessions);
        // 如果之前没有选中患者，或者选中的患者不在完整列表中，尝试恢复上次选中的患者。
        // 搜索结果为空时不能清空当前选中患者，否则顶部复诊/调理笺按钮会被误隐藏。
        if (!activeClinicSelection || !activeClinicSelection.id || activeClinicSelection.type === 'new' || !getPatientById(activeClinicSelection.id)) {
          // 尝试恢复上次选中的患者
          loadLastSelectedPatient();
          // 检查上次选中的患者是否还在列表中
          if (activeClinicSelection.id && getPatientById(activeClinicSelection.id)) {
            // 上次选中的患者还在，继续使用
          } else if (allPatients.length > 0) {
            // 上次选中的患者不在，选择第一个患者
            activeClinicSelection = { type: 'patient', id: allPatients[0].id };
            saveLastSelectedPatient(allPatients[0].id);
          } else {
            // 没有患者，选择新增患者模式
            activeClinicSelection = { type: 'new' };
            saveLastSelectedPatient(null);
          }
        }
        renderClinicPanel();
        updateClinicDetailUI();
        // 仅在患者问诊模式下恢复患者消息；若当前是医知快答模式，不覆盖聊天内容（避免刷新后内容被切回患者）
        if (activeMode === 'clinic') {
          if (activeClinicSelection && activeClinicSelection.type === 'patient' && activeClinicSelection.id) {
            const patient = getPatientById(activeClinicSelection.id);
            if (patient) {
              try {
                await switchPatientMessages(activeClinicSelection.id, patient);
              } catch (e) {
                console.warn('[DEBUG] syncPatients: 恢复聊天消息失败（已忽略）:', e);
              }
              try {
                await refreshCurrentPatientInfo();
              } catch (e) {
                console.warn('[DEBUG] syncPatients: 刷新患者详情失败（已忽略）:', e);
              }
              const updated = getPatientById(activeClinicSelection.id) || patient;
              updatePatientInfoBar(updated);
            }
          } else {
            currentPatientId = null;
            messages.length = 0;
            try {
              renderMessages();
            } catch (e) {
              console.warn('[DEBUG] syncPatients: renderMessages失败（已忽略）:', e);
            }
            updatePatientInfoBar(null);
          }
        }
      } catch (err) {
        console.warn('[DEBUG] 同步病案失败，保留当前患者选择与按钮状态:', err);
        renderClinicPanel();
        updateClinicDetailUI();
        if (activeMode === 'clinic' && activeClinicSelection && activeClinicSelection.type === 'patient') {
          const currentPatient = getPatientById(activeClinicSelection.id);
          if (currentPatient) updatePatientInfoBar(currentPatient);
        }
        appendMessage('system', `同步病案失败：${err.message}`);
      } finally {
        clinicLoading = false;
      }
    }

    async function createPatientOnServer(payload) {
      const data = await fetchJson(`${API_BASE}/patients`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      // 兼容多种后端返回：patient、patient_info、或顶层 patient_id。
      const raw = {
        ...payload,
        ...(data.patient_info || {}),
        ...(data.patient || {})
      };
      if (raw.patient_id == null && data.patient_id != null) raw.patient_id = data.patient_id;
      if (raw.id == null && data.id != null) raw.id = data.id;
      const patient = mapPatientForUI(raw);
      return patient;
    }

    async function deletePatientOnServer(patientId) {
      await fetchJson(`${API_BASE}/patients/${encodeURIComponent(patientId)}?doctor_id=${encodeURIComponent(currentUser.phone)}`, {
        method: 'DELETE'
      });
    }

    // 启动AI助手频道定时刷新
    function startAiAssistantRefreshTimer() {
      // 先清除已有的定时器（如果存在）
      stopAiAssistantRefreshTimer();

      console.log('[DEBUG] 启动AI助手频道定时刷新（每5秒）');

      // 立即执行一次
      refreshAiAssistantMessages();

      // 每5秒执行一次
      aiAssistantRefreshTimer = setInterval(() => {
        refreshAiAssistantMessages();
      }, 5000);
    }

    // 停止AI助手频道定时刷新
    function stopAiAssistantRefreshTimer() {
      if (aiAssistantRefreshTimer) {
        console.log('[DEBUG] 停止AI助手频道定时刷新');
        clearInterval(aiAssistantRefreshTimer);
        aiAssistantRefreshTimer = null;
      }
    }

    // 启动患者模式聊天记录定时刷新（每5秒拉取当前选中患者的云端 chatlog）
    function startClinicChatlogRefreshTimer() {
      stopClinicChatlogRefreshTimer();
      if (!activeClinicSelection || activeClinicSelection.type !== 'patient' || !activeClinicSelection.id) {
        return;
      }
      console.log('[DEBUG] 启动患者模式聊天记录定时刷新（每5秒）');
      function tick() {
        if (activeMode !== 'clinic' || !currentPatientId || currentPatientId !== activeClinicSelection.id) return;
        if (!currentUser || !currentUser.phone) return;
        refreshClinicPatientMessages();
      }
      tick();
      clinicChatlogRefreshTimer = setInterval(tick, 5000);
    }

    function stopClinicChatlogRefreshTimer() {
      if (clinicChatlogRefreshTimer) {
        console.log('[DEBUG] 停止患者模式聊天记录定时刷新');
        clearInterval(clinicChatlogRefreshTimer);
        clinicChatlogRefreshTimer = null;
      }
    }

    // 刷新当前选中患者的聊天记录：与云端比对，只追加新消息，避免整页重绘导致闪烁
    async function refreshClinicPatientMessages() {
      if (activeMode !== 'clinic' || !currentPatientId || !activeClinicSelection || activeClinicSelection.type !== 'patient' || activeClinicSelection.id !== currentPatientId) {
        return;
      }
      try {
        const raw = await fetchPatientChatlog(currentPatientId);
        const cloudMessages = normalizeChatlogMessages(raw);
        if (cloudMessages.length === 0) return;
        const existingIds = new Set(messages.map(m => (m && m.uuid) || ''));
        const norm = (msg) => String((msg && msg.content && msg.content.text) || '').trim();
        const newOnes = cloudMessages.filter(m => {
          if (!m || !m.uuid) return false;
          if (existingIds.has(m.uuid)) return false;
          const role = m.role || 'user';
          const text = norm(m);
          const isDupByContent = messages.some(ex => ex.role === role && norm(ex) === text);
          return !isDupByContent;
        });
        if (newOnes.length === 0) return;
        const getTs = (msg) => (msg && msg.ts != null) ? (msg.ts instanceof Date ? msg.ts.getTime() : (typeof msg.ts === 'number' ? (msg.ts < 1e12 ? msg.ts * 1000 : msg.ts) : 0)) : 0;
        messages.push(...newOnes);
        messages.sort((a, b) => getTs(a) - getTs(b));
        renderMessages();
      } catch (e) {
        console.warn('[DEBUG] 定时刷新患者聊天记录失败:', e);
      }
    }

    // 刷新AI助手频道的聊天记录
    // 与患者模式一致：使用 HTTP 直接拉取 get_patients_chatlog/common，不依赖 WebSocket
    // 这样 append_common_chatlog 写入的数据能可靠同步到前端（即使 WebSocket 未连接）
    async function refreshAiAssistantMessages() {
      // 只在AI助手频道时刷新
      if (activeMode !== 'corpus' || currentPatientId !== 'common') {
        return;
      }

      // 检查用户登录状态和 phone
      if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
        console.warn('[DEBUG] 未登录或phone为空，跳过AI助手频道刷新');
        return;
      }

      try {
        // 1) 使用 HTTP 直接拉取（与患者模式 refreshClinicPatientMessages 一致）
        const raw = await fetchPatientChatlog('common');
        const cloudMessages = normalizeChatlogMessages(raw);
        if (cloudMessages.length > 0) {
          const existingIds = new Set(messages.map(m => (m && m.uuid) || ''));
          const norm = (msg) => String((msg && msg.content && (msg.content.text || msg.content.imgurl || '')) || '').trim();
          const newOnes = cloudMessages.filter(m => {
            if (!m || !m.uuid) return false;
            if (existingIds.has(m.uuid)) return false;
            const role = m.role || 'user';
            const text = norm(m);
            const imgurl = (m.content && m.content.imgurl) || '';
            const isDupByContent = messages.some(ex => {
              if (ex.role !== role) return false;
              const exText = norm(ex);
              const exImg = (ex.content && ex.content.imgurl) || '';
              return (text && exText && text === exText) || (imgurl && exImg && imgurl === exImg);
            });
            return !isDupByContent;
          });
          if (newOnes.length > 0) {
            const getTs = (msg) => (msg && msg.ts != null) ? (msg.ts instanceof Date ? msg.ts.getTime() : (typeof msg.ts === 'number' ? (msg.ts < 1e12 ? msg.ts * 1000 : msg.ts) : 0)) : 0;
            messages.push(...newOnes);
            messages.sort((a, b) => getTs(a) - getTs(b));
            renderMessages();
            console.log(`[DEBUG] 医知快答刷新：从云端追加 ${newOnes.length} 条新消息`);
          }
        }
        // 2) 若 WebSocket 已连接，同时发送激活消息（触发后端推送，保持兼容）
        if (ws && ws.readyState === WebSocket.OPEN) {
          try {
            const raw = localStorage.getItem('clinical_user');
            if (raw) { const s = JSON.parse(raw); if (s && s.token) currentUser.token = s.token; }
          } catch (e) {}
          ws.send(JSON.stringify({
            phone: currentUser.phone,
            token: currentUser.token || '',
            chatlog_id: currentUser.chatlogId || '0',
            platform: 'corpus',
            message_data: {
              text: '',
              mode: 'corpus',
              patient_id: 'common',
              activate_only: true,
              token: currentUser.token || '',
              client_id: getCurrentClientId(),
              default_db: getClientDefaultDb(),
              corpus_default_db: getClientDefaultDb(),
              corpus_ids: (() => {
                var ids = [getCorpusDefaultDbId()];
                if (activeCorpusSelection && activeCorpusSelection.has('db-doc')) ids.push('db-doc');
                return ids;
              })()
            }
          }));
        }
      } catch (e) {
        console.warn(`[DEBUG] 定时刷新AI助手频道聊天记录失败: ${e}`);
      }
    }

    // 从后端获取AI助手频道的聊天记录
    // 注意：不再通过病案管理指令获取，而是通过激活消息触发后端推送
    async function loadAiAssistantMessages() {
      if (!currentUser || !currentUser.phone) {
        console.warn('[DEBUG] 未登录，无法获取AI助手频道聊天记录');
        return null;
      }

      // 通过 WebSocket 发送激活消息来触发后端推送聊天记录
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          console.log(`[DEBUG] 通过WebSocket发送激活消息以获取AI助手频道聊天记录`);
          try { const raw = localStorage.getItem('clinical_user'); if (raw) { const s = JSON.parse(raw); if (s && s.token) currentUser.token = s.token; } } catch (e) {}
          const activatePayload = {
            phone: currentUser.phone,
            token: currentUser.token || '',
            chatlog_id: currentUser.chatlogId || '0',
            platform: 'corpus',
            message_data: {
              text: '',
              mode: 'corpus',
              patient_id: 'common',
              activate_only: true,
              token: currentUser.token || '',
              client_id: getCurrentClientId(),
              default_db: getClientDefaultDb(),
              corpus_default_db: getClientDefaultDb(),
              corpus_ids: (() => {
                var ids = [getCorpusDefaultDbId()];
                if (activeCorpusSelection && activeCorpusSelection.has('db-doc')) ids.push('db-doc');
                return ids;
              })()
            }
          };
          ws.send(JSON.stringify(activatePayload));

          // 返回 null，因为聊天记录会通过 WebSocket 消息推送，不需要等待响应
          return null;
        } catch (e) {
          console.error('[DEBUG] 通过WebSocket发送激活消息失败:', e);
        }
      }

      // 如果 WebSocket 未连接，返回 null（无法获取）
      console.warn('[DEBUG] WebSocket未连接，无法获取AI助手频道聊天记录');
      return null;
    }



