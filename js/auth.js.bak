    function setWsStatus(text, colorCls) {

      const el = document.getElementById('ws-status');

      if (!el) return;

      const icons = {
        '已连接': 'wifi',
        '未连接': 'wifi-off',
        '异常': 'alert-circle',
        '连接中': 'loader'
      };

      const icon = icons[text] || 'radio';
      const iconColor = colorCls.includes('green') ? 'text-medical-green' :
                       colorCls.includes('amber') ? 'text-amber-600' :
                       colorCls.includes('red') ? 'text-red-600' : 'text-slate-600';

      el.innerHTML = `<i data-lucide="${icon}" width="12" class="${iconColor}"></i><span>${escapeHtml(text)}</span>`;
      el.className = 'text-xs font-medium flex items-center gap-1.5 justify-end status-indicator ' + colorCls;

      lucide.createIcons();

    }



    function updateHeaderUser() {

      const headerUserTextEl = document.getElementById('header-user-text');
      const headerDbNameEl = document.getElementById('header-db-name');
      const sidebarEl = document.getElementById('sidebar-user');

      const text = currentUser.phone ? `已登录：${currentUser.phone}` : '未登录';

      if (headerUserTextEl) {
        headerUserTextEl.textContent = text;
      }

      // 显示当前选择的数据库名称（仅在已登录且处于问诊模式时显示）
      if (headerDbNameEl) {
        if (currentUser.phone && activeMode === 'clinic' && selectedClinicDb) {
          headerDbNameEl.textContent = selectedClinicDb;
          headerDbNameEl.style.display = '';
        } else {
          headerDbNameEl.style.display = 'none';
        }
      }

      if (sidebarEl) sidebarEl.textContent = currentUser.phone || '未登录';

    }



    // 数据库由 web/server_config.js?v=20260506-koufu1 的 CLIENT_CONFIG.default_db 决定。
    function loadClinicDbFromSettings() {
      try {
        selectedClinicDb = normalizeClinicDbChoice(getClientDefaultDb());
        const raw = localStorage.getItem('clinical_user');
        if (raw) {
          const stored = JSON.parse(raw);
          if (Object.prototype.hasOwnProperty.call(stored, 'defaultDb')) {
            delete stored.defaultDb;
            localStorage.setItem('clinical_user', JSON.stringify(stored));
          }
        }
        console.log(`[DEBUG] 使用客户配置数据库: ${selectedClinicDb}`);
        updateClinicDependentLabels();
        updateHeaderUser();
      } catch (e) {
        console.error('读取客户配置数据库失败:', e);
      }
    }

    // 保留函数以兼容旧代码；实际数据库仍以当前客户配置为准。
    function selectClinicDb(dbName) {
      selectedClinicDb = normalizeClinicDbChoice(dbName || getClientDefaultDb());
      try {
        const raw = localStorage.getItem('clinical_user');
        if (raw) {
          const stored = JSON.parse(raw);
          delete stored.defaultDb;
          localStorage.setItem('clinical_user', JSON.stringify(stored));
        }
      } catch (e) {
        console.error('清理本地数据库选择失败:', e);
      }
      // 更新与开方库相关的对外文案
      updateClinicDependentLabels();
      // 更新头部用户信息（包括数据库名称）
      updateHeaderUser();
    }

    // 更新调理笺按钮文本
    function updatePrescriptionButtonText() {
      const btnTextEl = document.getElementById('prescription-btn-text');
      if (btnTextEl) {
        const prescriptionTitle = getPrescriptionTitle(selectedClinicDb);
        btnTextEl.textContent = `展示${prescriptionTitle}`;
        console.log(`[DEBUG] 更新调理笺按钮文本: ${selectedClinicDb} -> ${prescriptionTitle}`);
      } else {
        console.warn('[DEBUG] 调理笺按钮文本元素不存在');
      }
    }

    function selectClinicModel(modelName) {
      // 设置模型选择（从localStorage读取或使用默认值）
      // 模型选择现在在个人设置页面进行，这里只负责设置变量
      selectedClinicModel = modelName;
    }

    function updateClinicDetailUI() {

      const formSection = document.getElementById('clinic-form-section');
      const chatInput = document.getElementById('chat-input');

      const hint = document.getElementById('chat-mode-hint');

      const selection = window.activeClinicSelection;
      const isNewClinic = window.activeMode === 'clinic' && selection && selection.type === 'new' && !window.currentPatientId;

      if (formSection) {

        if (isNewClinic) {

          formSection.classList.remove('hidden');

        } else {

          formSection.classList.add('hidden');

        }

      }

      // 数据库选择器已移除，现在在个人设置页面进行选择
      // 不再显示/隐藏数据库选择器

      if (chatInput) {

        chatInput.disabled = isNewClinic;

        if (isNewClinic) {

          chatInput.classList.add('bg-slate-100', 'cursor-not-allowed');

          chatInput.placeholder = '请先在上方填写并保存客户基本信息，再开始评估';

        } else {

          chatInput.classList.remove('bg-slate-100', 'cursor-not-allowed');

          chatInput.placeholder = '请输入要发送给助手的内容';

        }

      }

      if (hint) {

        hint.textContent = isNewClinic

          ? '当前为新增客户，请先完善信息并保存，随后开启评估。'

          : '输入消息后回车，或点击发送';

      }

    }



    // 检查登录状态
    const CLINICAL_SESSION_MS = 365 * 24 * 60 * 60 * 1000;

    function isClinicalSessionValid(stored) {
      if (!stored || !stored.phone || !stored.token) return false;
      const exp = stored.token_expires_at;
      if (exp == null || exp === undefined) return true;
      return Number(exp) > Date.now();
    }

    function checkLoginStatus() {
      return true;
    }

    // 跳转到登录页面（已禁用，仅用于页面调试）
    function redirectToLogin() {
      console.warn('登录跳转已禁用，继续使用演示用户。');
    }

    // 检查并处理未登录情况（已禁用）
    function ensureLoggedIn() {
      return true;
    }

      // 根据模式控制UI元素的显示/隐藏
      function updateModeSpecificUI() {
        const infoBar = document.getElementById('patient-info-bar');
        const btnGenerateTreatment = document.getElementById('btn-generate-treatment');
        const btnOnlineSearchToggle = document.getElementById('btn-online-search-toggle');
        const prescriptionBtn = document.getElementById('btn-show-prescription');
        const followupSection = document.getElementById('followup-symptom-section');

        // 在医知快答模式下，隐藏复诊症状输入表单
        if (followupSection && activeMode === 'corpus') {
          followupSection.classList.add('hidden');
        }

        // 控制患者信息栏和侧边栏
        const timelineSidebar = document.getElementById('patient-timeline-sidebar');
        if (infoBar) {
          if (window.activeMode === 'corpus') {
            // 医知快答模式下，隐藏患者信息栏和侧边栏
            infoBar.classList.add('hidden');
            if (prescriptionBtn) {
              prescriptionBtn.disabled = false;
              prescriptionBtn.classList.add('disabled');
              prescriptionBtn.setAttribute('data-disabled-reason', '请先选择客户');
            }
            if (timelineSidebar) timelineSidebar.classList.add('hidden');
          } else {
            // 患者问诊模式下，根据是否有选中患者来决定是否显示
            // updatePatientInfoBar 函数会处理具体显示逻辑，包括侧边栏
            if (activeClinicSelection && activeClinicSelection.type === 'patient' && activeClinicSelection.id) {
              const patient = getPatientById(activeClinicSelection.id);
              if (patient) {
                updatePatientInfoBar(patient);
                // 侧边栏会在updatePatientInfoBar中通过renderPatientTimelineSidebar控制
              } else {
                infoBar.classList.add('hidden');
                if (prescriptionBtn) {
                  prescriptionBtn.disabled = false;
                  prescriptionBtn.classList.add('disabled');
                  prescriptionBtn.setAttribute('data-disabled-reason', '请先选择客户');
                }
                if (timelineSidebar) timelineSidebar.classList.add('hidden');
              }
            } else {
              infoBar.classList.add('hidden');
              if (prescriptionBtn) {
                prescriptionBtn.disabled = false;
                prescriptionBtn.classList.add('disabled');
                prescriptionBtn.setAttribute('data-disabled-reason', '请先选择客户');
              }
              if (timelineSidebar) timelineSidebar.classList.add('hidden');
            }
          }
        }

      // 控制按钮显示/隐藏
      if (btnGenerateTreatment) {
        if (window.activeMode === 'clinic') {
          // 患者问诊模式：显示生成诊疗建议按钮
          btnGenerateTreatment.classList.remove('hidden');
        } else {
          // 医知快答模式：隐藏生成诊疗建议按钮
          btnGenerateTreatment.classList.add('hidden');
        }
      }

      if (btnOnlineSearchToggle) {
        if (window.activeMode === 'corpus') {
          // 医知快答模式：显示联网搜索开关按钮
          btnOnlineSearchToggle.classList.remove('hidden');
          updateOnlineSearchButtonState();
        } else {
          // 患者问诊模式：隐藏联网搜索开关按钮
          btnOnlineSearchToggle.classList.add('hidden');
        }
      }
    }

    // 处理调理笺按钮点击（始终可点击，无调理记录时打开空页面）
    function handlePrescriptionButtonClick(event) {
      const btn = document.getElementById('btn-show-prescription');
      if (!btn) return;
      // 始终执行打开调理笺页，无调理记录时页面会留空展示
      showPrescriptionPage();
    }

    // 在新窗口展示调理笺页面（使用当前选中患者ID）
    function showPrescriptionPage() {
      if (activeMode !== 'clinic' || !activeClinicSelection || activeClinicSelection.type !== 'patient') {
        showTopNotificationBar('请先在患者记录模式下选择一位已生成' + getPrescriptionTitle(selectedClinicDb) + '的患者。', 'amber');
        return;
      }
      const patientId = activeClinicSelection.id;
      if (!patientId) {
        showTopNotificationBar('当前未选中有效客户，无法展示' + getPrescriptionTitle(selectedClinicDb) + '。', 'amber');
        return;
      }
      const url = `prescription.html?patient_id=${encodeURIComponent(patientId)}`;
      window.open(url, '_blank');
    }

    // 更新联网搜索按钮的视觉状态
    function updateOnlineSearchButtonState() {
      const btn = document.getElementById('btn-online-search-toggle');
      const icon = document.getElementById('icon-online-search');
      if (!btn || !icon) return;

      if (onlineSearchEnabled) {
        // 开启状态：蓝色高亮
        btn.className = 'h-8 px-3 flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white text-xs font-medium transition-all shadow-lg hover:shadow-xl whitespace-nowrap opacity-100';
        icon.setAttribute('data-lucide', 'search');
      } else {
        // 关闭状态：灰色半透明
        btn.className = 'h-8 px-3 flex items-center justify-center gap-1.5 rounded-lg bg-slate-400 hover:bg-slate-500 text-white text-xs font-medium transition-all shadow-sm whitespace-nowrap opacity-50';
        icon.setAttribute('data-lucide', 'search');
      }
      // 重新初始化图标
      if (window.lucide) {
        window.lucide.createIcons();
      }
    }

    // 切换联网搜索开关
    function toggleOnlineSearch() {
      onlineSearchEnabled = !onlineSearchEnabled;
      updateOnlineSearchButtonState();
      // TODO: 后续接入真实联网搜索功能
    }

    // 显示底部连接状态栏
    function showConnectionStatusBar(message, type = 'amber') {
      const statusBar = document.getElementById('connection-status-bar');
      const statusText = document.getElementById('connection-status-text');

      if (!statusBar || !statusText) return;

      // 根据类型设置颜色
      if (type === 'red') {
        statusBar.className = 'fixed bottom-0 left-0 right-0 bg-gradient-to-r from-red-500 to-red-600 text-white px-4 py-2 shadow-lg z-50';
      } else {
        statusBar.className = 'fixed bottom-0 left-0 right-0 bg-gradient-to-r from-amber-500 to-amber-600 text-white px-4 py-2 shadow-lg z-50';
      }

      statusText.textContent = message;
      statusBar.classList.remove('hidden');

      // 重新初始化图标
      lucide.createIcons();
    }

    // 隐藏底部连接状态栏
    function hideConnectionStatusBar() {
      const statusBar = document.getElementById('connection-status-bar');
      if (statusBar) {
        statusBar.classList.add('hidden');
      }
    }

    // 显示顶部提示栏
    function showTopNotificationBar(message, type = 'amber') {
      const notificationBar = document.getElementById('top-notification-bar');
      const notificationText = document.getElementById('top-notification-text');

      if (!notificationBar || !notificationText) return;

      // 根据类型设置颜色
      if (type === 'red') {
        notificationBar.className = 'fixed top-0 left-0 right-0 bg-gradient-to-r from-red-500 to-red-600 text-white px-4 py-2 shadow-lg z-50';
      } else if (type === 'blue') {
        notificationBar.className = 'fixed top-0 left-0 right-0 bg-gradient-to-r from-blue-500 to-blue-600 text-white px-4 py-2 shadow-lg z-50';
      } else {
        notificationBar.className = 'fixed top-0 left-0 right-0 bg-gradient-to-r from-amber-500 to-amber-600 text-white px-4 py-2 shadow-lg z-50';
      }

      notificationText.textContent = message;
      notificationBar.classList.remove('hidden');

      // 重新初始化图标
      lucide.createIcons();

      // 5秒后自动隐藏
      setTimeout(() => {
        hideTopNotificationBar();
      }, 5000);
    }

    // 隐藏顶部提示栏
    function hideTopNotificationBar() {
      const notificationBar = document.getElementById('top-notification-bar');
      if (notificationBar) {
        notificationBar.classList.add('hidden');
      }
    }

    // 显示复诊症状输入表单
    function showFollowupSymptomForm() {
      // 检查是否在问诊模式且有选中的患者
      if (activeMode !== 'clinic' || !activeClinicSelection || activeClinicSelection.type !== 'patient') {
        appendMessage('system', '请先选择客户。');
        return;
      }

      // 检查WebSocket连接
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        showConnectionStatusBar('WebSocket未连接，无法开始记录跟进。', 'red');
        return;
      }

      // 显示复诊症状输入表单
      const followupSection = document.getElementById('followup-symptom-section');
      const symptomInput = document.getElementById('followup-symptom-input');
      const errorEl = document.getElementById('followup-symptom-error');

      if (followupSection && symptomInput) {
        followupSection.classList.remove('hidden');
        symptomInput.value = '';
        if (errorEl) errorEl.textContent = '';
        symptomInput.focus();
      }
    }

    // 取消复诊症状输入表单
    function cancelFollowupSymptomForm() {
      const followupSection = document.getElementById('followup-symptom-section');
      const symptomInput = document.getElementById('followup-symptom-input');
      const errorEl = document.getElementById('followup-symptom-error');

      if (followupSection) followupSection.classList.add('hidden');
      if (symptomInput) symptomInput.value = '';
      if (errorEl) errorEl.textContent = '';
      isInFollowupFlow = false;
      followupSymptom = '';

      // 重新更新患者信息栏，以显示"开始复诊"按钮
      if (activeClinicSelection && activeClinicSelection.type === 'patient' && activeClinicSelection.id) {
        const patient = getPatientById(activeClinicSelection.id);
        if (patient) {
          updatePatientInfoBar(patient);
        }
      }
    }

    // 确认复诊症状并开始复诊流程
    function confirmFollowupSymptom() {
      // 检查是否在问诊模式且有选中的患者
      if (activeMode !== 'clinic' || !activeClinicSelection || activeClinicSelection.type !== 'patient') {
        appendMessage('system', '请先选择客户。');
        return;
      }

      const patientId = activeClinicSelection.id;
      if (!patientId) {
        appendMessage('system', '请先选择客户。');
        return;
      }

      // 获取症状输入
      const symptomInput = document.getElementById('followup-symptom-input');
      const errorEl = document.getElementById('followup-symptom-error');

      if (!symptomInput) return;

      const symptom = symptomInput.value.trim();
      if (!symptom) {
        if (errorEl) errorEl.textContent = '请输入近期的身体状态变化。';
        return;
      }

      // 检查WebSocket连接
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        showConnectionStatusBar('WebSocket未连接，无法开始记录跟进', 'red');
        if (errorEl) errorEl.textContent = 'WebSocket未连接，无法开始记录跟进。';
        return;
      }

      // 获取患者信息
      const patient = getPatientById(patientId);
      if (!patient) {
        if (errorEl) errorEl.textContent = '无法找到患者信息。';
        return;
      }

      // 清空错误提示
      if (errorEl) errorEl.textContent = '';

      // 设置复诊流程状态
      isInFollowupFlow = true;
      followupSymptom = symptom;

      // 隐藏复诊症状输入表单
      const followupSection = document.getElementById('followup-symptom-section');
      if (followupSection) followupSection.classList.add('hidden');

      // 隐藏"开始复诊"按钮
      const followupBtn = document.getElementById('btn-start-followup');
      if (followupBtn) followupBtn.classList.add('hidden');

      // 不再清空当前消息，保留首诊及既往对话记录，让复诊在同一上下文中继续
      // 仅追加一条系统提示，标记复诊开始
      appendMessage('system', `已开始跟进记录，身体状态：${symptom}。正在生成第一个问题...`);

      // 记录发送消息时的患者ID
      pendingPatientId = patientId;

      // 验证用户信息（静默处理）
      if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
        console.warn('用户登录信息缺失，无法启动复诊流程');
        isInFollowupFlow = false;
        followupSymptom = '';
        return;
      }

      // 发送启动交互性问答的请求，包含复诊症状
      const payload = {
        token: currentUser.token || '',
        phone: currentUser.phone.trim(),  // 确保去除空格
        chatlog_id: currentUser.chatlogId || '0',
        platform: 'clinic',
        message_data: {
          text: '',  // 空文本，仅用于触发交互性问答
          mode: 'clinic',
          patient_id: patientId,
          start_interactive_qa: true,  // 标记启动交互性问答
          start_followup: true,  // 标记这是复诊流程
          followup_symptom: symptom,  // 复诊症状
          db_choice: selectedClinicDb || getClientDefaultDb(),  // 确保有默认值
          default_db: getClientDefaultDb(),
          client_id: getCurrentClientId(),
          patient_info: patient  // 传递患者信息
        }
      };

      // 验证payload（静默处理）
      if (!payload.phone || payload.phone.trim() === '' || !payload.message_data) {
        console.warn('启动复诊流程失败：数据验证失败');
        isInFollowupFlow = false;
        followupSymptom = '';
        return;
      }

      try {
        ws.send(JSON.stringify(payload));
      } catch (e) {
        appendMessage('system', `启动跟进记录失败：${e.message}`);
        isInFollowupFlow = false;
        followupSymptom = '';
      }
    }

    async function switchMode(mode) {

      activeMode = mode;
      try { localStorage.setItem(getClientStorageKey('last_active_mode'), mode); } catch (e) {}

      const clinicPanel = document.getElementById('clinic-panel');

      const corpusPanel = document.getElementById('corpus-panel');

      const tabClinic = document.getElementById('mode-tab-clinic');

      const tabCorpus = document.getElementById('mode-tab-corpus');

      if (!clinicPanel || !corpusPanel || !tabClinic || !tabCorpus) return;

      // 保存当前消息（在切换模式前）
      if (currentPatientId && messages.length > 0) {
        patientMessagesCache[currentPatientId] = messages.map(msg => {
          const msgCopy = { ...msg };
          if (msgCopy.content && typeof msgCopy.content === 'string') {
            msgCopy.content = { text: msgCopy.content };
          }
          if (msgCopy.ts instanceof Date) {
            msgCopy.ts = msgCopy.ts.toISOString();
          }
          return msgCopy;
        });
        savePatientMessagesCache();
        console.log(`[DEBUG] 切换模式前保存患者[${currentPatientId}]的消息，共${messages.length}条`);
      }

      if (mode === 'clinic') {

        clinicPanel.classList.remove('hidden');

        corpusPanel.classList.add('hidden');

        // 选中状态：绿色背景，白色字
        tabClinic.style.background = 'var(--msd-primary)';
        tabClinic.style.color = 'white';

        // 未选中状态：白色背景，绿色字
        tabCorpus.style.background = 'white';
        tabCorpus.style.color = 'var(--msd-primary)';

        // 停止AI助手频道定时刷新（如果正在运行）
        stopAiAssistantRefreshTimer();

        // 切换到患者问诊模式：恢复之前选中的患者消息（如果有）
        if (activeClinicSelection && activeClinicSelection.type === 'patient' && activeClinicSelection.id) {
          const patientId = activeClinicSelection.id;
          const patient = getPatientById(patientId);
          await switchPatientMessages(patientId, patient);
          // 启动患者模式聊天记录定时刷新（每5秒），便于其他端上传的新消息同步展示
          startClinicChatlogRefreshTimer();
        } else {
          // 没有选中患者，清空消息并停止患者聊天刷新
          stopClinicChatlogRefreshTimer();
          currentPatientId = null;
          messages.length = 0;
          renderMessages();
        }

        // 更新头部用户信息（包括数据库名称）
        updateHeaderUser();

      } else {

        corpusPanel.classList.remove('hidden');

        clinicPanel.classList.add('hidden');

        // 选中状态：绿色背景，白色字
        tabCorpus.style.background = 'var(--msd-primary)';
        tabCorpus.style.color = 'white';

        // 未选中状态：白色背景，绿色字
        tabClinic.style.background = 'white';
        tabClinic.style.color = 'var(--msd-primary)';

        // 停止患者模式聊天记录定时刷新
        stopClinicChatlogRefreshTimer();
        // 切换到医知快答模式：加载医知快答的消息（使用 common 作为特殊患者ID）
        await switchPatientMessages('common', null);
        // 恢复「医知快答启用自有库」勾选状态（本地缓存，刷新后保持）
        restoreCorpusDocRagdbCheckbox();
        // 启动医知快答频道定时刷新（每5秒请求一次聊天记录）
        startAiAssistantRefreshTimer();
        // 拉取医生自有库内容并展示前 100 字预览
        loadDocRagdbPreview();

        // 更新头部用户信息（切换到医知快答模式时隐藏数据库名称）
        updateHeaderUser();

      }

      updateClinicDependentLabels();
      updateClinicDetailUI();  // 这会处理数据库选择器的显示/隐藏

      // 根据模式控制患者信息栏和快捷按钮的显示
      updateModeSpecificUI();

    }



