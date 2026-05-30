    function renderClinicPanel() {

      const list = document.getElementById('clinic-list');

      const count = document.getElementById('clinic-count');

      if (!list || !count) {
        console.warn('[DEBUG] renderClinicPanel: list或count元素未找到');
        return;
      }

      // 按复诊优先级排序：需要复诊的排前面，超期越久越靠前；其次按最后就诊时间
      const sortedSessions = [...(clinicSessions || [])].sort((a, b) => {
        a = a || {}; b = b || {};
        const aPriority = (a.needsFollowUp || false) ? (a.daysOverdue || 0) + 1000 : 0;
        const bPriority = (b.needsFollowUp || false) ? (b.daysOverdue || 0) + 1000 : 0;
        if (aPriority !== bPriority) return bPriority - aPriority;
        const timeA = a.lastVisit || a.firstVisit || 0;
        const timeB = b.lastVisit || b.firstVisit || 0;
        return timeB - timeA;
      });

      // 如果有搜索，显示所有搜索结果；否则只显示前10个
      const items = patientSearchQuery ? sortedSessions : sortedSessions.slice(0, 10);

      console.log(`[DEBUG] renderClinicPanel: clinicSessions.length=${clinicSessions.length}, items.length=${items.length}, patientSearchQuery="${patientSearchQuery}"`);

      count.textContent = patientSearchQuery ? `${items.length} / ${allPatients.length}` : items.length;

      const isNewActive = activeClinicSelection && activeClinicSelection.type === 'new';
      const newCls = isNewActive
        ? 'border-medical-green shadow-md'
        : 'border-slate-300 text-slate-600 hover:border-medical-green hover:text-medical-green-dark hover:shadow-sm';
      const newStyle = isNewActive
        ? 'background: linear-gradient(to right, var(--primary-green-pale), var(--primary-green-lighter)); color: var(--primary-green-darker);'
        : '';

      // 新增患者按钮放在最上方
      let html = `
        <div class="px-4 py-3 border-b border-slate-200">
          <button class="w-full text-left text-[11px] font-medium rounded-lg border border-dashed ${newCls} flex items-center justify-between px-2 py-1" style="${newStyle}" onclick="selectNewClinicPatient()">
            <span class="flex items-center gap-1.5">
              <i data-lucide="user-plus" width="12"></i>
              <span>新增患者</span>
            </span>
          </button>
        </div>
      `;

      // 如果没有患者，显示空状态
      if (items.length === 0) {
        html += `
          <div class="px-4 py-8 text-center">
            <div class="text-slate-400 text-sm mb-2">暂无患者</div>
            <div class="text-xs text-slate-400">点击上方"新增患者"按钮创建新患者</div>
          </div>
        `;
      } else {
        // 如果有患者，遍历渲染
        items.forEach(p => {

        const isActive = activeClinicSelection &&
          activeClinicSelection.type === 'patient' &&
          activeClinicSelection.id === p.id;

        const hasHighRisk = typeof p.memo === 'string' && p.memo.includes('高危');

        // 获取患者姓名首字
        const nameInitial = p.name && p.name.length > 0 ? p.name[0] : '?';

        // 获取患者编号尾数（取ID的最后4位数字）
        const patientId = p.id || '';
        const patientIdTail = patientId.length >= 4 ? patientId.slice(-4) : patientId;

        // 选中项：与整体配色一致的主题绿底色 + 左边框（#005F60 系）
        const rowBgClass = isActive
          ? 'border-l-4 border-medical-green'
          : 'border-l-4 border-l-transparent hover:bg-white hover:border-l-slate-200';
        const rowBgStyle = isActive ? 'background-color: rgba(0, 95, 96, 0.14);' : '';

        // 根据激活状态设置头像样式
        const avatarClass = isActive
          ? 'bg-medical-green text-white ring-2 ring-medical-green ring-offset-2'
          : 'bg-slate-200 text-slate-500 border-2 border-white shadow-sm group-hover:bg-slate-300';

        // 格式化时间显示
        const firstVisitStr = p.firstVisit ? formatDateShort(p.firstVisit) : '-';
        const lastVisitRelativeStr = p.lastVisit ? formatRelativeTime(p.lastVisit) : (p.firstVisit ? formatRelativeTime(p.firstVisit) : '-');
        const ageGenderStr = (p.gender || '') + (p.age ? ' ' + p.age : '');
        const visitCount = p.visitCount || 0;
        const visitCountStr = visitCount > 0 ? `咨询${visitCount}次` : '首次咨询';

        // 高亮主诉关键词
        const summaryText = p.summary || (p.memo || '暂无症状');
        const highlightedSummary = highlightSymptomKeywords(summaryText);
        const safePatientId = escapeHtml(patientId);

        html += `
          <div data-patient-row="true" data-patient-id-value="${safePatientId}" class="group px-4 py-3 flex items-center gap-3 cursor-pointer transition-all ${rowBgClass}" style="${rowBgStyle}" onclick="selectClinicPatient(this.getAttribute('data-patient-id-value'))">
            <div class="flex flex-col items-center gap-0.5 flex-shrink-0">
              <div data-patient-avatar="true" class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold transition-all ${avatarClass}">${escapeHtml(nameInitial)}</div>
              <div data-patient-id="true" class="text-[8px] text-slate-400 whitespace-nowrap text-center leading-tight">
                <div>客户编号：</div>
                <div class="font-medium">${escapeHtml(patientIdTail)}</div>
              </div>
            </div>
            <div class="flex-1 overflow-hidden min-w-0">
              <div class="flex items-center justify-between gap-2 mb-0.5">
                <span data-patient-name="true" class="text-sm font-bold ${isActive ? 'text-slate-900' : 'text-slate-700'} truncate">${escapeHtml(p.name)}</span>
                <div class="flex items-center gap-1.5 flex-shrink-0">
                  ${hasHighRisk ? '<span class="text-[9px] px-1 bg-red-100 text-red-600 rounded">高危</span>' : ''}
                  <span class="text-[10px] text-slate-500 whitespace-nowrap">${escapeHtml(ageGenderStr)}</span>
                </div>
              </div>
              <div data-patient-timeline-meta="true" class="flex items-center gap-2 text-[10px] text-slate-400 mt-0.5 flex-wrap">
                <span data-patient-visits="true" class="whitespace-nowrap">${escapeHtml(visitCountStr)}</span>
                <span data-patient-divider="primary" class="text-slate-300">|</span>
                <span data-patient-last-visit="true" class="whitespace-nowrap">${escapeHtml(lastVisitRelativeStr)}</span>
                ${firstVisitStr !== '-' ? `<span class="text-slate-300">|</span><span class="whitespace-nowrap">首次咨询：${escapeHtml(firstVisitStr)}</span>` : ''}
              </div>
              <div data-patient-summary="true" class="text-[11px] truncate mt-0.5 ${isActive ? 'text-slate-600' : 'text-slate-400 group-hover:text-slate-500'}">${highlightedSummary}</div>
            </div>
            <button class="px-2 flex items-center justify-center text-slate-600 hover:text-red-500 transition-all hover:bg-red-50 rounded-lg flex-shrink-0"
                    onclick="deleteClinicPatient(this.closest('[data-patient-row]').getAttribute('data-patient-id-value'), event); event.stopPropagation();" title="删除该记录">
              <i data-lucide="trash-2" width="14" class="icon-bounce"></i>
            </button>
          </div>
        `;
        });
      }

      list.innerHTML = html;

      // 渲染侧边栏图标

      lucide.createIcons();

    }



    function renderCorpusPanel() {

      const list = document.getElementById('corpus-list');

      const count = document.getElementById('corpus-count');

      if (!list) return;

      const items = corpusSources.slice(0, 10);

      if (count) count.textContent = items.length;

      let html = '';

      items.forEach(s => {

        const checked = activeCorpusSelection.has(s.id);

        const rowCls = checked ? 'bg-medical-green-light border-l-2 border-medical-green' : 'bg-white';

        html += `

          <button class="w-full text-left px-4 py-3 border-b border-slate-200 hover:bg-white ${rowCls}"

                  onclick="toggleCorpusSelection('${s.id}')">

            <div class="flex items-center justify-between mb-1">

              <div class="flex items-center gap-2">

                <span class="inline-flex items-center justify-center w-4 h-4 rounded border text-[10px] ${checked ? 'bg-medical-green border-medical-green text-white' : 'border-slate-300 text-transparent'}">

                  ✓

                </span>

                <span class="text-sm font-medium text-slate-900">${escapeHtml(s.name)}</span>

              </div>

              <span class="text-[11px] text-slate-400">${escapeHtml(s.id)}</span>

            </div>

            <div class="text-[11px] text-slate-500 mt-0.5 line-clamp-2">${escapeHtml(s.description)}</div>

          </button>

        `;

      });

      list.innerHTML = html;

      lucide.createIcons();

      // 渲染用户上传的文件列表
      renderUserUploadedFiles();

    }



    function toggleCorpusSelection(id) {

      if (activeCorpusSelection.has(id)) {

        activeCorpusSelection.delete(id);

      } else {

        activeCorpusSelection.add(id);

      }

      renderCorpusPanel();

    }

    // 处理文件上传
    async function handleFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;

      // 验证文件类型
      const allowedExtensions = ['.docx', '.doc', '.txt', '.xlsx', '.xls'];
      const fileExtension = '.' + file.name.split('.').pop().toLowerCase();

      if (!allowedExtensions.includes(fileExtension)) {
        alert(`不支持的文件类型：${fileExtension}\n支持的类型：${allowedExtensions.join(', ')}`);
        event.target.value = ''; // 清空文件选择
        return;
      }

      // 检查文件大小（限制为10MB）
      const maxSize = 10 * 1024 * 1024; // 10MB
      if (file.size > maxSize) {
        alert(`文件太大，最大支持10MB，当前文件大小：${(file.size / 1024 / 1024).toFixed(2)}MB`);
        event.target.value = '';
        return;
      }

      // 显示上传中提示
      appendMessage('system', `正在上传文件：${file.name}...`);

      try {
        // 读取文件为base64
        const reader = new FileReader();
        reader.onload = async function(e) {
          const base64Content = e.target.result.split(',')[1]; // 移除data:type;base64,前缀

          // 确定文件类型
          let fileType = fileExtension.substring(1); // 去掉点号

          // 确保连接正常（如果断开则自动重连）
          const connected = await ensureWsConnected();
          if (!connected) {
            showConnectionStatusBar('错误：无法连接到服务器，请稍后重试', 'red');
            event.target.value = '';
            return;
          }

          const payload = {
            token: currentUser.token || '',
            phone: currentUser.phone,
            chatlog_id: currentUser.chatlogId || '0',
            platform: 'corpus',
            message_data: {
              file_upload: {
                type: 'rag_file',
                filename: file.name,
                file_type: fileType,
                file_content: base64Content
              }
            }
          };

          try {
            ws.send(JSON.stringify(payload));
            // 等待服务器响应，响应会在ws.onmessage中处理
          } catch (err) {
            appendMessage('system', `文件上传失败：${err.message}`);
            event.target.value = '';
          }
        };

        reader.onerror = function() {
          appendMessage('system', '文件读取失败，请重试');
          event.target.value = '';
        };

        reader.readAsDataURL(file);

      } catch (error) {
        appendMessage('system', `文件上传错误：${error.message}`);
        event.target.value = '';
      }
    }

    // 处理私有库文件上传（用于开方数据库）
    async function handlePrivateDbFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;

      // 验证文件类型（只支持txt）
      const fileExtension = '.' + file.name.split('.').pop().toLowerCase();

      if (fileExtension !== '.txt') {
        alert(`私有库只支持上传TXT文件，不支持：${fileExtension}`);
        event.target.value = ''; // 清空文件选择
        return;
      }

      // 检查文件大小（限制为10MB）
      const maxSize = 10 * 1024 * 1024; // 10MB
      if (file.size > maxSize) {
        alert(`文件太大，最大支持10MB，当前文件大小：${(file.size / 1024 / 1024).toFixed(2)}MB`);
        event.target.value = '';
        return;
      }

      // 显示上传中提示
      const statusEl = document.getElementById('private-db-file-status');
      if (statusEl) {
        statusEl.textContent = '上传中...';
        statusEl.className = 'text-[10px] text-blue-600 ml-2';
      }
      appendMessage('system', `正在上传私有库文件：${file.name}...`);

      try {
        // 读取文件为base64
        const reader = new FileReader();
        reader.onload = async function(e) {
          const base64Content = e.target.result.split(',')[1]; // 移除data:type;base64,前缀

          // 确保连接正常（如果断开则自动重连）
          const connected = await ensureWsConnected();
          if (!connected) {
            appendMessage('system', '错误：无法连接到服务器，请稍后重试');
            if (statusEl) {
              statusEl.textContent = '连接失败';
              statusEl.className = 'text-[10px] text-red-600 ml-2';
            }
            event.target.value = '';
            return;
          }

          const payload = {
            token: currentUser.token || '',
            phone: currentUser.phone,
            chatlog_id: currentUser.chatlogId || '0',
            platform: 'clinic',
            message_data: {
              file_upload: {
                type: 'private_db_file',
                filename: file.name,
                file_type: 'txt',
                file_content: base64Content,
                target_db: selectedClinicDb || '通识库1'  // 使用当前选择的数据库
              }
            }
          };

          try {
            ws.send(JSON.stringify(payload));
            // 等待服务器响应，响应会在ws.onmessage中处理
          } catch (err) {
            appendMessage('system', `文件上传失败：${err.message}`);
            if (statusEl) {
              statusEl.textContent = '上传失败';
              statusEl.className = 'text-[10px] text-red-600 ml-2';
            }
            event.target.value = '';
          }
        };

        reader.onerror = function() {
          appendMessage('system', '文件读取失败，请重试');
          if (statusEl) {
            statusEl.textContent = '读取失败';
            statusEl.className = 'text-[10px] text-red-600 ml-2';
          }
          event.target.value = '';
        };

        reader.readAsDataURL(file);

      } catch (error) {
        appendMessage('system', `文件上传错误：${error.message}`);
        if (statusEl) {
          statusEl.textContent = '上传错误';
          statusEl.className = 'text-[10px] text-red-600 ml-2';
        }
        event.target.value = '';
      }
    }

    // 医生自有数据库（doc ragdb）：上传 TXT 并覆盖保存到云端
    async function handleDocRagdbUpload(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;

      if (!ensureLoggedIn()) {
        event.target.value = '';
        return;
      }
      if (!currentUser.phone) {
        appendMessage('system', '未登录，无法上传医生自有库');
        event.target.value = '';
        return;
      }

      const statusEl = document.getElementById('doc-ragdb-status');
      try {
        const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
        if (ext !== '.txt') {
          alert('医生自有库仅支持 .txt 文件');
          event.target.value = '';
          return;
        }
        const maxSize = 10 * 1024 * 1024;
        if (file.size > maxSize) {
          alert(`文件太大，最大支持10MB，当前：${(file.size / 1024 / 1024).toFixed(2)}MB`);
          event.target.value = '';
          return;
        }
        if (statusEl) {
          statusEl.textContent = '上传中...';
          statusEl.className = 'text-[10px] text-blue-600 whitespace-nowrap';
        }

        const text = await file.text();
        const doctorId = encodeURIComponent(currentUser.phone);
        const data = await fetchJson(`${API_BASE}/update_doc_ragdb/${doctorId}`, {
          method: 'POST',
          body: JSON.stringify({ text })
        });

        const bytes = data.bytes || 0;
        if (statusEl) {
          statusEl.textContent = `已上传 ${(bytes / 1024).toFixed(1)}KB`;
          statusEl.className = 'text-[10px] text-green-600 whitespace-nowrap';
        }
        appendMessage('system', `医生自有库已上传（覆盖保存）。大小：${(bytes / 1024).toFixed(1)}KB`);
        loadDocRagdbPreview();
      } catch (e) {
        console.error('handleDocRagdbUpload error:', e);
        if (statusEl) {
          statusEl.textContent = '上传失败';
          statusEl.className = 'text-[10px] text-red-600 whitespace-nowrap';
        }
        appendMessage('system', `医生自有库上传失败：${e.message}`);
      } finally {
        event.target.value = '';
      }
    }

    /** 进入医知快答板块时拉取医生自有库内容，左侧整块区域显示预览（多行、可滚动，尽量填满左侧） */
    async function loadDocRagdbPreview() {
      const wrap = document.getElementById('doc-ragdb-preview');
      const textEl = document.getElementById('doc-ragdb-preview-text');
      if (!wrap || !textEl) return;
      if (!currentUser || !currentUser.phone) {
        wrap.style.display = 'none';
        return;
      }
      if (!currentUser.token) {
        wrap.style.display = 'none';
        appendMessage('system', '未登录或 token 已失效，无法加载医生自有库预览');
        return;
      }
      try {
        const url = `${API_BASE}/get_doc_ragdb/${encodeURIComponent(currentUser.phone)}`;
        const resp = await fetch(url, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${currentUser.token}`, 'Content-Type': 'application/json' },
          body: '{}'
        });
        const raw = resp.status === 200 ? await resp.text() : '';
        const text = (raw || '').trim();
        if (text.length > 0) {
          textEl.textContent = text;
          wrap.title = text.length > 200 ? text.slice(0, 200) + '…' : text;
          wrap.style.display = 'flex';
        } else {
          textEl.textContent = '';
          wrap.style.display = 'none';
          if (resp.status !== 200) {
            appendMessage('system', '医生自有库加载失败（' + resp.status + '），请检查网络或重新登录');
          }
        }
      } catch (e) {
        wrap.style.display = 'none';
        appendMessage('system', '医生自有库加载失败，请检查网络或重新登录');
      }
    }

    /** 医知快答：开启新对话 = 清空 common 聊天记录（仅传 patient_id=common） */
    async function clearCorpusChatAndStartNew() {
      if (activeMode !== 'corpus' || currentPatientId !== 'common') {
        appendMessage('system', '仅可在医知快答模式下使用「开启新对话」');
        return;
      }
      if (!currentUser || !currentUser.phone || !currentUser.token) {
        appendMessage('system', '请先登录');
        return;
      }
      const patientId = 'common'; // 仅医知快答，只清 common
      const apiPatientId = getApiPatientId(patientId);
      try {
        const url = `${API_BASE}/clear_patients_chatlog/${encodeURIComponent(apiPatientId)}`;
        const resp = await fetch(url, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${currentUser.token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ doctor_id: currentUser.phone })
        });
        const data = resp.ok ? await resp.json().catch(() => ({})) : {};
        if (data.ok) {
          patientMessagesCache['common'] = [];
          messages.length = 0;
          savePatientMessagesCache();
          renderMessages();
          appendMessage('system', '已开启新对话');
        } else {
          appendMessage('system', '清空失败：' + (data.error || resp.status));
        }
      } catch (e) {
        appendMessage('system', '清空失败：' + (e.message || String(e)));
      }
    }

    function onToggleDocRagdb() {
      const cb = document.getElementById('toggle-doc-ragdb');
      const enabled = !!(cb && cb.checked);
      if (enabled) {
        activeCorpusSelection.add('db-doc');
      } else {
        activeCorpusSelection.delete('db-doc');
      }
      try { localStorage.setItem(getClientStorageKey('clinical_corpus_use_doc_ragdb'), enabled ? 'true' : 'false'); } catch (e) {}
      renderCorpusPanel();
    }

    // 从 localStorage 恢复医知快答「启用自有库」勾选状态（刷新后保持）
    function restoreCorpusDocRagdbCheckbox() {
      try {
        const saved = localStorage.getItem(getClientStorageKey('clinical_corpus_use_doc_ragdb'));
        if (saved === 'true' || saved === '1') {
          activeCorpusSelection.add('db-doc');
        } else {
          activeCorpusSelection.delete('db-doc');
        }
        const cb = document.getElementById('toggle-doc-ragdb');
        if (cb) cb.checked = activeCorpusSelection.has('db-doc');
      } catch (e) {}
    }

    // 渲染用户上传的文件列表
    function renderUserUploadedFiles() {
      const container = document.getElementById('user-uploaded-files');
      if (!container) return;

      if (userUploadedFiles.length === 0) {
        container.innerHTML = '<div class="text-[10px] text-slate-400 text-center py-1">暂无上传文件</div>';
        return;
      }

      container.innerHTML = userUploadedFiles.map(file => `
        <div class="flex items-center justify-between px-2 py-1 bg-white rounded border border-slate-200 hover:bg-slate-50">
          <div class="flex-1 min-w-0">
            <div class="text-[11px] font-medium text-slate-700 truncate" title="${escapeHtml(file.filename)}">
              📎 ${escapeHtml(file.filename)}
            </div>
          </div>
          <button data-file-id="${escapeHtml(file.file_id)}" onclick="deleteUploadedFile(this.getAttribute('data-file-id'))" class="ml-2 text-red-500 hover:text-red-700 text-[10px]" title="删除">
            <i data-lucide="x" width="12"></i>
          </button>
        </div>
      `).join('');

      lucide.createIcons();
    }

    // 删除上传的文件（前端移除，后端需要重新连接才能完全清理）
    function deleteUploadedFile(fileId) {
      if (!confirm('确认删除该文件吗？文件将在下次重新连接时从服务器端移除。')) return;

      // 从列表中移除
      userUploadedFiles = userUploadedFiles.filter(f => f.file_id !== fileId);
      renderUserUploadedFiles();

      appendMessage('system', '文件已从列表中移除');
    }

    // 通用图像上传逻辑（点击选择和拖拽共用）
    async function uploadImageFile(file) {
      if (!file) return;

      // 检查登录状态
      if (!ensureLoggedIn()) {
        return;
      }

      // 验证文件类型（只支持图像）
      if (!file.type.startsWith('image/')) {
        alert('只支持上传图像文件');
        return;
      }

      // 检查文件大小（限制为10MB）
      const maxSize = 10 * 1024 * 1024; // 10MB
      if (file.size > maxSize) {
        alert(`图像太大，最大支持10MB，当前文件大小：${(file.size / 1024 / 1024).toFixed(2)}MB`);
        return;
      }

      // 确保连接正常（如果断开则自动重连）
      const connected = await ensureWsConnected();
      if (!connected) {
        showConnectionStatusBar('错误：无法连接到服务器，请稍后重试', 'red');
        return;
      }

      // 显示上传中提示
      appendMessage('system', `正在上传图像：${file.name}...`);

      try {
        // 读取文件为base64
        const reader = new FileReader();
        reader.onload = async function(e) {
          // 再次检查连接状态（文件读取可能需要时间）
          if (!ws || ws.readyState !== WebSocket.OPEN) {
            const reconnected = await ensureWsConnected();
            if (!reconnected) {
              appendMessage('system', '图像上传失败：连接已断开，无法上传');
              return;
            }
          }

          const base64Content = e.target.result.split(',')[1]; // 移除data:type;base64,前缀

          // 先显示用户上传的图像预览
          const imageUrl = e.target.result; // 完整的data URL用于预览
          appendMessage('user', { text: `上传图像：${file.name}`, imgurl: imageUrl });

          // 记录发送消息时的患者ID（用于跟踪等待回复的消息属于哪个患者）
          // 医知快答模式使用 common 作为患者ID
          const sentPatientId = activeMode === 'clinic' && activeClinicSelection.type === 'patient'
            ? activeClinicSelection.id
            : (activeMode === 'corpus' ? 'common' : null);
          pendingPatientId = sentPatientId;

          const payload = {
            token: currentUser.token || '',
            phone: currentUser.phone,
            chatlog_id: currentUser.chatlogId || '0',
            platform: activeMode === 'clinic' ? 'clinic' : 'corpus',
            message_data: {
              image: {
                content: base64Content
              },
              mode: activeMode,
              patient_id: sentPatientId || '',
              db_choice: activeMode === 'clinic' ? selectedClinicDb : ''
            }
          };

          try {
            ws.send(JSON.stringify(payload));
            // 等待服务器响应，响应会在ws.onmessage中处理
          } catch (err) {
            appendMessage('system', `图像上传失败：${err.message}`);
          }
        };

        reader.onerror = function() {
          appendMessage('system', '图像读取失败，请重试');
        };

        reader.readAsDataURL(file);

      } catch (error) {
        appendMessage('system', `图像上传错误：${error.message}`);
      }
    }

    // 处理图像上传（通过文件选择输入）
    async function handleImageUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      try {
        await uploadImageFile(file);
      } finally {
        // 重置input的值，避免同一文件无法重复选择
        event.target.value = '';
      }
    }

    // 聊天窗口拖拽上传图片
    function setupChatDragAndDrop() {
      const chatSection = document.getElementById('chat-container');
      if (!chatSection) return;

      const preventDefaults = (e) => {
        e.preventDefault();
        e.stopPropagation();
      };

      const addHighlight = () => {
        chatSection.classList.add('border-2', 'border-dashed', 'border-purple-400');
      };

      const removeHighlight = () => {
        chatSection.classList.remove('border-2', 'border-dashed', 'border-purple-400');
      };

      ['dragenter', 'dragover'].forEach(eventName => {
        chatSection.addEventListener(eventName, (e) => {
          preventDefaults(e);
          addHighlight();
        });
      });

      ['dragleave', 'drop'].forEach(eventName => {
        chatSection.addEventListener(eventName, (e) => {
          preventDefaults(e);
          removeHighlight();
        });
      });

      chatSection.addEventListener('drop', async (e) => {
        preventDefaults(e);
        removeHighlight();

        const dt = e.dataTransfer;
        const files = dt && dt.files ? Array.from(dt.files) : [];
        if (!files.length) return;

        // 只处理第一张图片
        const imageFile = files.find(f => f.type && f.type.startsWith('image/'));
        if (!imageFile) {
          appendMessage('system', '当前仅支持拖拽图片到聊天窗口上传');
          return;
        }

        await uploadImageFile(imageFile);
      });
    }

    // 页面加载完成后：应用客户配置、启用拖拽上传
    document.addEventListener('DOMContentLoaded', function() {
      var cfg = window.CLIENT_CONFIG;
      if (cfg && cfg.header_brand) {
        var el = document.getElementById('header-brand');
        if (el) el.textContent = cfg.header_brand;
      }
      updateClinicDependentLabels();
      setupChatDragAndDrop();
    });

    async function selectClinicPatient(id) {

      activeClinicSelection = { type: 'patient', id };

      // 保存当前选中的患者ID
      saveLastSelectedPatient(id);

      // 确保隐藏新增患者表单
      const formSection = document.getElementById('clinic-form-section');
      if (formSection) {
        formSection.classList.add('hidden');
      }

      // 切换病人消息（会从云端 chatlog 拉取并按 uuid 去重）
      await switchPatientMessages(id);
      // 启动该患者聊天记录定时刷新（每5秒），便于其他端新消息同步
      startClinicChatlogRefreshTimer();

      // 清空pendingPatientId，因为已经切换到新患者
      pendingPatientId = null;

      // 懒加载：拉取该患者的最新详情（含问诊记录），并更新 UI
      await refreshCurrentPatientInfo();

      // 找到选中的患者信息（refresh 后已包含聊天记录 + 问诊结果）
      const patient = getPatientById(id);
      if (patient) {
        updatePatientInfoBar(patient);

        // 1) 是否有聊天记录（API 已通过 switchPatientMessages 加载到 messages）
        const hasChat = messages.length > 0;

        // 2) 是否有问诊结果（开方记录：timeline 或 treatment_timeline 中任一条有 treatment_completed）
        const detailCheck = patient.detail || {};
        const hasResult = getTreatmentTimeline(detailCheck).some(visitHasTreatmentRecord);

        // 仅当「既无聊天记录也无问诊结果」时，视为新患者，进入问诊模式并自动启动交互性问答
        const isNewPatientMode = !hasChat && !hasResult;

        if (isNewPatientMode) {
          // 新患者：无聊天、无问诊结果 → 自动启动首次问诊流程
          const hasInteractiveQA = messages.some(msg => {
            if (msg.role !== 'assistant') return false;
            const content = typeof msg.content === 'string' ? { text: msg.content } : (msg.content || {});
            return content.interactive_qa === true || (content.question_data != null);
          });

          if (ws && ws.readyState === WebSocket.OPEN) {
            if (!hasInteractiveQA) {
              appendMessage('system', `已选择客户：${patient.name}，正在生成第一个问题...`);
              pendingPatientId = patient.id;
              if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
                console.warn('用户登录信息缺失，无法启动交互性问答');
                return;
              }
              const payload = {
                token: currentUser.token || '',
                phone: currentUser.phone.trim(),
                chatlog_id: currentUser.chatlogId || '0',
                platform: 'clinic',
                message_data: {
                  text: '',
                  mode: 'clinic',
                  patient_id: patient.id,
                  start_interactive_qa: true,
                  db_choice: selectedClinicDb || getClientDefaultDb(),
                  default_db: getClientDefaultDb(),
                  client_id: getCurrentClientId(),
                  patient_info: patient
                }
              };
              if (!payload.phone || payload.phone.trim() === '' || !payload.message_data) {
                console.warn('启动交互性问答失败：数据验证失败');
                return;
              }
              try {
                ws.send(JSON.stringify(payload));
              } catch (e) {
                appendMessage('system', `启动交互性问答失败：${e.message}`);
              }
            } else {
              console.log(`[DEBUG] 患者[${patient.id}]已在交互性问答流程中，不重新启动`);
            }
          } else {
            if (!hasInteractiveQA) {
              showConnectionStatusBar('WebSocket未连接，请等待连接后自动开始评估', 'amber');
            }
          }
        } else {
          // 有聊天记录或问诊结果：不进入问诊模式，只展示已有内容
          if (hasChat && !hasResult) {
            appendMessage('system', `已选择客户：${patient.name}。可继续对话或点击「生成${getPrescriptionTitle(selectedClinicDb)}」完成文书生成。`);
          } else if (hasResult && !hasChat) {
            appendMessage('system', `已选择客户：${patient.name}。您可以直接聊天，或点击「记录跟进」启动新的跟进流程。`);
          } else {
            appendMessage('system', `已选择客户：${patient.name}。可直接聊天或点击「记录跟进」。`);
          }
        }
      } else {
        updatePatientInfoBar(null);
      }

      renderClinicPanel();

      updateClinicDetailUI();

    }



    function selectNewClinicPatient() {
      // 先保存当前患者的消息（如果当前有选中的患者）
      if (currentPatientId) {
        if (messages.length > 0) {
          patientMessagesCache[currentPatientId] = [...messages];
          savePatientMessagesCache();
          console.log(`[DEBUG] 保存患者[${currentPatientId}]的消息，共${messages.length}条`);
        }
      }

      // 清空当前患者ID和消息
      currentPatientId = null;
      messages.length = 0;
      renderMessages();

      // 清空复诊流程状态
      isInFollowupFlow = false;
      followupSymptom = '';

      // 隐藏复诊症状输入表单
      const followupSection = document.getElementById('followup-symptom-section');
      if (followupSection) followupSection.classList.add('hidden');

      stopClinicChatlogRefreshTimer();
      activeClinicSelection = { type: 'new' };
      saveLastSelectedPatient(null);

      // 隐藏患者信息栏
      updatePatientInfoBar(null);

      renderClinicPanel();

      updateClinicDetailUI();

    }



    // 计算字符串相似度（使用简单的编辑距离算法）
    function calculateSimilarity(str1, str2) {
      if (!str1 || !str2) return 0;
      const s1 = str1.toLowerCase();
      const s2 = str2.toLowerCase();

      // 如果完全匹配
      if (s1 === s2) return 1;

      // 如果包含关系
      if (s1.includes(s2) || s2.includes(s1)) {
        return Math.max(s1.length, s2.length) / Math.min(s1.length, s2.length) * 0.8;
      }

      // 计算编辑距离（Levenshtein距离）
      const len1 = s1.length;
      const len2 = s2.length;
      const matrix = [];

      for (let i = 0; i <= len1; i++) {
        matrix[i] = [i];
      }
      for (let j = 0; j <= len2; j++) {
        matrix[0][j] = j;
      }

      for (let i = 1; i <= len1; i++) {
        for (let j = 1; j <= len2; j++) {
          if (s1[i - 1] === s2[j - 1]) {
            matrix[i][j] = matrix[i - 1][j - 1];
          } else {
            matrix[i][j] = Math.min(
              matrix[i - 1][j] + 1,
              matrix[i][j - 1] + 1,
              matrix[i - 1][j - 1] + 1
            );
          }
        }
      }

      const distance = matrix[len1][len2];
      const maxLen = Math.max(len1, len2);
      return 1 - (distance / maxLen);
    }

    // 应用患者搜索
    function applyPatientSearch(query) {
      patientSearchQuery = query;

      if (!query || query.trim() === '') {
        // 没有搜索，显示所有患者（但只显示前10个）
        clinicSessions = [...allPatients];  // 使用展开运算符创建新数组，确保是独立的副本
      } else {
        // 有搜索，进行模糊匹配
        const searchTerm = query.trim().toLowerCase();
        clinicSessions = allPatients.filter(patient => {
          const patientName = (patient.name || '').toLowerCase();
          const similarity = calculateSimilarity(patientName, searchTerm);
          return similarity > 0.5 || patientName.includes(searchTerm);
        });
      }

      console.log(`[DEBUG] applyPatientSearch: query="${query}", allPatients.length=${allPatients.length}, clinicSessions.length=${clinicSessions.length}`);
      renderClinicPanel();
    }

    // 搜索患者
    function searchPatients(query) {
      applyPatientSearch(query);
    }

    async function deleteClinicPatient(id, event) {

      if (event && event.stopPropagation) {

        event.stopPropagation();

      }

      if (!confirm('确认删除该记录吗？')) return;

      if (currentUser.phone && id) {
        try {
          await deletePatientOnServer(id);
        } catch (err) {
          appendMessage('system', `删除失败：${err.message}`);
          return;
        }
      }

      clinicSessions = clinicSessions.filter(p => p.id !== id);
      allPatients = allPatients.filter(p => p.id !== id);
      allPatients = allPatients.filter(p => p.id !== id);

      // 如果删除的是当前选中的患者，同步更新聊天窗口：跳转到下一患者或清空
      if (activeClinicSelection.type === 'patient' && activeClinicSelection.id === id) {
        // 删除该病人的消息缓存
        if (patientMessagesCache[id]) {
          delete patientMessagesCache[id];
          savePatientMessagesCache();
        }
        // 清空当前聊天列表并重绘（避免仍显示已删患者的记录）
        messages.length = 0;
        currentPatientId = null;
        renderMessages();

        if (clinicSessions.length > 0) {
          // 自动跳转到下一个患者并加载其聊天记录
          activeClinicSelection = { type: 'patient', id: clinicSessions[0].id };
          const patient = clinicSessions.find(p => p.id === activeClinicSelection.id);
          await switchPatientMessages(activeClinicSelection.id, patient);
          updatePatientInfoBar(patient);
          saveLastSelectedPatient(activeClinicSelection.id);
          startClinicChatlogRefreshTimer();
        } else {
          // 没有患者：清空选中、侧栏参考文献并停止患者聊天刷新
          stopClinicChatlogRefreshTimer();
          activeClinicSelection = { type: 'new' };
          saveLastSelectedPatient(null);
          updatePatientInfoBar(null);
          sidebarReferences = [];
          renderSidebarReferences();
        }
      }

      renderClinicPanel();
      updateClinicDetailUI();

    }



    // 启动心跳机制
