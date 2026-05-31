    function startHeartbeat() {
      // 清除旧的心跳定时器
      if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
      }

      // 如果连接未打开，不启动心跳
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return;
      }

      // 定期发送心跳（ping消息）
      heartbeatInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          try {
            // 发送心跳消息（后端会响应pong）
            ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
            console.log('[HEARTBEAT] 发送心跳包');
          } catch (e) {
            console.warn('[HEARTBEAT] 发送心跳失败:', e);
            // 如果发送失败，停止心跳并尝试重连
            stopHeartbeat();
            if (checkLoginStatus() && currentUser.phone) {
              scheduleReconnect();
            }
          }
        } else {
          // 连接已关闭，停止心跳
          stopHeartbeat();
        }
      }, HEARTBEAT_INTERVAL);
    }

    // 停止心跳机制
    function stopHeartbeat() {
      if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
      }
    }

    // 安排自动重连
    function scheduleReconnect() {
      // 如果已经在重连中，不重复安排
      if (reconnectTimer) {
        return;
      }

      // 如果已达到最大重连次数，停止重连
      if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        console.warn('[RECONNECT] 已达到最大重连次数，停止重连');
        showConnectionStatusBar('连接失败，已达到最大重连次数，请刷新页面重试', 'red');
        return;
      }

      reconnectAttempts++;
      // 指数退避：2s, 4s, 8s, 16s, 30s, 30s, 30s, 30s
      const delay = Math.min(RECONNECT_DELAY_BASE * Math.pow(2, reconnectAttempts - 1), RECONNECT_DELAY_MAX);

      console.log(`[RECONNECT] ${(delay/1000).toFixed(0)}秒后尝试第${reconnectAttempts}次重连（指数退避）...`);
      showConnectionStatusBar(`连接断开，${(delay/1000).toFixed(0)}秒后自动重连（${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS}）...`, 'amber');

      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (checkLoginStatus() && currentUser.phone) {
          console.log(`[RECONNECT] 开始第${reconnectAttempts}次重连...`);
          connectWs();
        } else {
          console.log('[RECONNECT] 用户未登录，取消重连');
          reconnectAttempts = 0;
        }
      }, delay);
    }

    // 取消自动重连
    function cancelReconnect() {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      reconnectAttempts = 0;
    }

    // 确保WebSocket连接（如果断开则自动重连）
    async function ensureWsConnected() {
      // 如果连接正常，直接返回
      if (ws && ws.readyState === WebSocket.OPEN) {
        return true;
      }

      // 如果未登录，不尝试重连
      if (!checkLoginStatus() || !currentUser.phone) {
        return false;
      }

      // 如果正在连接中，等待一下
      if (ws && ws.readyState === WebSocket.CONNECTING) {
        return new Promise((resolve) => {
          const checkInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
              clearInterval(checkInterval);
              resolve(true);
            } else if (ws.readyState === WebSocket.CLOSED) {
              clearInterval(checkInterval);
              resolve(false);
            }
          }, 100);

          // 5秒超时
          setTimeout(() => {
            clearInterval(checkInterval);
            resolve(false);
          }, 5000);
        });
      }

      // 连接已关闭，尝试重连
      console.log('[ENSURE_CONNECT] 连接已断开，尝试重连...');
      showConnectionStatusBar('连接已断开，正在重连...', 'amber');

      // 取消之前的重连计划
      cancelReconnect();

      // 立即尝试连接
      reconnectAttempts = 0; // 重置重连次数
      connectWs();

      // 等待连接建立（最多等待5秒）
      return new Promise((resolve) => {
        let attempts = 0;
        const checkInterval = setInterval(() => {
          attempts++;
          if (ws && ws.readyState === WebSocket.OPEN) {
            clearInterval(checkInterval);
            resolve(true);
          } else if (attempts >= 50 || (ws && ws.readyState === WebSocket.CLOSED)) {
            clearInterval(checkInterval);
            resolve(false);
          }
        }, 100);
      });
    }

    function connectWs() {

      // 取消之前的重连计划
      cancelReconnect();

      // 停止心跳
      stopHeartbeat();

      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {

        ws.close();

      }



      ws = new WebSocket(WS_URL);



      ws.onopen = () => {

        setWsStatus('已连接', 'text-medical-green');

        // 重置重连次数
        reconnectAttempts = 0;

        const note = document.getElementById('connection-note');

        if (note) {
          note.innerHTML = '';
          note.className = 'text-[11px] text-slate-500 flex items-center gap-1.5';
        }

        // 隐藏底部状态栏（连接正常时）
        hideConnectionStatusBar();

        // 启动心跳机制
        startHeartbeat();

        lucide.createIcons();

        // WebSocket连接建立后，如果当前在AI助手频道，发送一个空消息来激活连接
        // 这样后端就能记录连接并主动推送聊天记录
        if (activeMode === 'corpus' && currentPatientId === 'common' && currentUser && currentUser.phone) {
          console.log('[DEBUG] WebSocket连接建立，当前在AI助手频道，发送空消息激活连接以获取聊天记录');
          try {
            // 医知快答拉取 common 聊天记录需要 token，发送前从 localStorage 再读一次
            try {
              const raw = localStorage.getItem('clinical_user');
              if (raw) { const s = JSON.parse(raw); if (s && s.token) currentUser.token = s.token; }
            } catch (e) {}
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
                token: currentUser.token || '',  // 与顶层一致，后端可从此处读取（医知快答由后端调6005需带 token）
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
            console.log('[DEBUG] 已发送激活消息，等待后端推送聊天记录');
          } catch (e) {
            console.warn(`[DEBUG] 发送激活消息失败: ${e}`);
          }
        }

      };



      ws.onclose = () => {

        setWsStatus('未连接', 'text-amber-600');

        // 停止心跳
        stopHeartbeat();

        // WebSocket关闭时，停止AI助手频道定时刷新
        stopAiAssistantRefreshTimer();

        const note = document.getElementById('connection-note');

        if (note) {
          note.innerHTML = '<i data-lucide="x-circle" width="12" class="text-amber-600"></i><span>连接已关闭</span>';
          note.className = 'text-[11px] text-slate-500 flex items-center gap-1.5';
        }

        lucide.createIcons();

        // 如果已登录，尝试自动重连；如果未登录，跳转到登录页
        if (checkLoginStatus() && currentUser.phone) {
          // 已登录状态：安排自动重连
          scheduleReconnect();
        } else {
          // 未登录状态：提示重新登录
          showConnectionStatusBar('连接已关闭，请重新登录', 'amber');
          setTimeout(() => {
            redirectToLogin();
          }, 2000);
        }

      };



      ws.onerror = (error) => {

        setWsStatus('异常', 'text-red-600');

        console.error('[WS_ERROR] WebSocket错误:', error);

        const note = document.getElementById('connection-note');

        if (note) {
          note.innerHTML = '<i data-lucide="alert-triangle" width="12" class="text-red-600"></i><span>连接出现异常</span>';
          note.className = 'text-[11px] text-slate-500 flex items-center gap-1.5';
        }

        lucide.createIcons();

        // 如果已登录，会在onclose中处理重连；如果未登录，提示重新登录
        if (!checkLoginStatus() || !currentUser.phone) {
          showConnectionStatusBar('连接出现异常，请重新登录', 'red');
        }

      };



      ws.onmessage = (event) => {

        try {
          // 处理心跳响应（pong）
          if (event.data === 'pong' || (typeof event.data === 'string' && event.data.trim() === 'pong')) {
            console.log('[HEARTBEAT] 收到心跳响应');
            return;
          }

          // 处理JSON消息
          const data = JSON.parse(event.data);

          // 处理心跳响应（JSON格式）
          if (data.type === 'pong') {
            console.log('[HEARTBEAT] 收到心跳响应');
            return;
          }

          // 处理文件上传响应
          if (data.answer && data.answer.file_upload) {
            const uploadResult = data.answer.file_upload;
            if (uploadResult.success) {
              // 检查是否是私有库文件上传
              if (uploadResult.target_db) {
                // 私有库文件上传成功
                const statusEl = document.getElementById('private-db-file-status');
                if (statusEl) {
                  // 显示文件名，表示当前本地RAG的使用情况
                  statusEl.textContent = `当前使用: ${uploadResult.filename}`;
                  statusEl.className = 'text-[10px] text-green-600 ml-2 font-medium';
                  statusEl.setAttribute('title', `当前本地RAG使用的文件: ${uploadResult.filename}`);
                }
                appendMessage('system', `私有库文件[${uploadResult.filename}]上传成功！已加载到[${uploadResult.target_db}]数据库，当前将使用此文件进行RAG检索。`);
              } else {
                // 普通RAG文件上传
                userUploadedFiles.push({
                  file_id: uploadResult.file_id,
                  filename: uploadResult.filename,
                  upload_time: Date.now()
                });
                renderUserUploadedFiles();
                appendMessage('system', `文件[${uploadResult.filename}]上传成功！已添加到您的RAG数据库。`);
              }
            } else {
              const statusEl = document.getElementById('private-db-file-status');
              if (statusEl && uploadResult.target_db) {
                statusEl.textContent = '上传失败';
                statusEl.className = 'text-[10px] text-red-600 ml-2';
                statusEl.removeAttribute('title');
              }
              appendMessage('system', `文件上传失败：${data.error_msg?.text || '未知错误'}`);
            }
            return; // 文件上传响应不需要显示为普通消息
          }

          let messageContent = null;

          // 处理错误消息
          if (data.error) {
            // 检查是否是认证相关错误
            const errorMsg = String(data.error).toLowerCase();
            if (errorMsg.includes('token') || errorMsg.includes('登录') || errorMsg.includes('认证') || errorMsg.includes('unauthorized')) {
              appendMessage('system', '登录已过期，正在跳转到登录页面...');
              setTimeout(() => {
                redirectToLogin();
              }, 1000);
              return; // 不再显示错误消息
            }
            // 过滤掉 "phone is required" 错误，这是内部错误，不应该显示给用户
            if (errorMsg.includes('phone is required') || errorMsg.includes('phone') && errorMsg.includes('required')) {
              console.warn('[DEBUG] 忽略 phone is required 错误（内部错误）:', data.error);
              return; // 不显示此错误
            }
            messageContent = { text: `错误：${data.error}` };
          }
          // 处理新的JSON格式消息（优先检查 answer 字段）
          else if (data.answer) {
            // 检查状态：如果是 processing 且没有实际内容，则忽略此消息（这是中间状态消息）
            const status = data.status;
            const answer = data.answer;
            const hasContent = answer.text || answer.imgurl || answer.reference ||
                              answer.rag_contents || answer.interactive_qa ||
                              answer.interactive_qa_finished || answer.question_data ||
                              answer.selection_q || answer.type;
            const msgUuid = answer.uuid || answer.chatuuid || null;
            const msgType = answer.type || (answer.selection_q ? 'selection_q' : null);

            // 过滤激活消息的响应：status为ok但没有任何内容，且check.text为"ok"（这是激活消息的响应）
            if (status === 'ok' && !hasContent && data.check && data.check.text === 'ok' && !answer.patient) {
              console.log('[DEBUG] 忽略激活消息的响应（空消息）:', data);
              return;
            }

            // 过滤激活消息的错误响应：如果只有patient错误且没有其他内容
            if (status === 'error' && answer.patient && !answer.patient.ok && !hasContent && !answer.text) {
              console.log('[DEBUG] 忽略激活消息的错误响应:', data);
              return;
            }

            if (status === 'processing' && !hasContent) {
              // 忽略处理中的空消息，这是中间状态消息，不应该显示给用户
              console.log('[DEBUG] 忽略处理中的空消息:', data);
              return;
            }

            // 处理交互性问答相关消息
            if (answer.interactive_qa) {
              // 交互性问答阶段
              const currentRound = answer.current_round || 0;
              const maxRounds = answer.max_rounds || 6;
              messageContent = {
                text: `${answer.text}\n\n[第 ${currentRound}/${maxRounds} 轮问答]`,
                imgurl: answer.imgurl || null,
                reference: answer.reference || null,
                rag_contents: answer.rag_contents || null,
                question_data: answer.question_data || null,  // 添加JSON格式的问题数据
                interactive_qa: true,
                current_round: currentRound,
                max_rounds: maxRounds,
                uuid: msgUuid,
                type: msgType
              };
            } else if (answer.interactive_qa_finished) {
              // 交互性问答完成，进入最终诊断流程
              messageContent = {
                text: answer.text || '交互性问答已完成，正在进入诊断流程...\n\n此步骤时间较长，请耐心等待。',
                imgurl: answer.imgurl || null,
                reference: answer.reference || null,
                rag_contents: answer.rag_contents || null,
                uuid: msgUuid,
                type: msgType
              };
            } else {
              // 常规消息
              messageContent = {
                text: answer.text || '',
                imgurl: answer.imgurl || null,
                selection_q: answer.selection_q || null,
                reference: answer.reference || null,
                rag_contents: answer.rag_contents || null,
                source: answer.source || null,  // 消息来源（"web" 或 "wechat"）
                timestamp: answer.timestamp || null,  // 保存时间戳用于去重
                uuid: msgUuid,
                type: msgType
              };
            }

            // 如果没有任何内容，且不是处理中状态，使用旧格式兼容（用于调试）
            if (!messageContent.text && !messageContent.imgurl && !messageContent.reference) {
              if (status === 'processing') {
                // 处理中状态且无内容，忽略
                console.log('[DEBUG] 忽略处理中的空消息:', data);
                return;
              }
              // 其他状态的空消息，用于调试时显示
              messageContent = { text: JSON.stringify(data, null, 2) };
            }

            // 检查是否是用户消息（从外部系统同步的）
            if (answer.role === 'user') {
              // 这是用户消息，需要作为用户消息显示
              const userMessageContent = {
                text: answer.text || '',
                imgurl: answer.imgurl || null,
                source: answer.source || 'web',  // 默认来源为 "web"
                uuid: msgUuid,
                type: msgType
              };

              // 检查消息属于哪个患者
              const messagePatientId = answer.patient_id || pendingPatientId || null;

              // 特殊处理：医知快答频道的消息应该在医知快答模式下显示
              const isAiAssistantMessage = messagePatientId === 'common';
              const shouldShowAiAssistantMessage = isAiAssistantMessage && activeMode === 'corpus';

              // 如果消息属于某个患者，但当前没有选中该患者，则保存到缓存但不显示
              // 例外：医知快答频道的消息在医知快答模式下应该显示
              if (messagePatientId && currentPatientId !== messagePatientId && !shouldShowAiAssistantMessage) {
                // 保存到缓存但不显示
                if (patientMessagesCache[messagePatientId]) {
                  patientMessagesCache[messagePatientId].push({
                    role: 'user',
                    content: userMessageContent,
                    ts: new Date()
                  });
                } else {
                  patientMessagesCache[messagePatientId] = [{
                    role: 'user',
                    content: userMessageContent,
                    ts: new Date()
                  }];
                }
                savePatientMessagesCache();
                console.log(`[DEBUG] 收到用户消息[${messagePatientId}]，但当前选中的患者是[${currentPatientId}]，已保存到缓存但不显示`);
                return;
              }

              // 如果是医知快答频道的消息，且当前在医知快答模式，确保currentPatientId正确设置
              if (shouldShowAiAssistantMessage && currentPatientId !== 'common') {
                console.log(`[DEBUG] 收到医知快答用户消息，切换到医知快答频道（currentPatientId: ${currentPatientId} -> common）`);
                currentPatientId = 'common';
              }

              // 显示用户消息（传递时间戳用于去重）
              const userTimestamp = answer.timestamp ? (typeof answer.timestamp === 'number' ? new Date(answer.timestamp) : new Date(answer.timestamp)) : null;
              appendMessage('user', userMessageContent, userTimestamp);
              return; // 用户消息已处理，不再继续处理为助手消息
            }
          }
          // 处理直接包含 text/imgurl/reference 的消息
          else if (data.text || data.imgurl || data.reference || data.rag_contents) {
            messageContent = {
              text: data.text || '',
              imgurl: data.imgurl || null,
              reference: data.reference || null,
              rag_contents: data.rag_contents || null
            };
          }
          // 兼容旧格式：纯文本
          else {
            // 尝试查找可能的文本字段
            const possibleText = data.text || data.message || JSON.stringify(data, null, 2);
            messageContent = { text: possibleText };
          }

          // 检查消息属于哪个患者（优先使用后端返回的patient_id）
          const messagePatientId = data.answer?.patient_id || pendingPatientId || null;

          // 特殊处理：医知快答频道的消息（patient_id为"common"）应该在医知快答模式下显示
          // 如果当前在医知快答模式，无论currentPatientId是什么，都应该显示医知快答的消息
          const isAiAssistantMessage = messagePatientId === 'common';
          const shouldShowAiAssistantMessage = isAiAssistantMessage && activeMode === 'corpus';

          // 如果消息属于某个患者，但当前没有选中该患者（新增患者模式或切换到了其他患者），则保存到该患者的缓存但不显示
          // 例外：医知快答频道的消息在医知快答模式下应该显示
          if (messagePatientId && currentPatientId !== messagePatientId && !shouldShowAiAssistantMessage) {
            // 消息属于某个患者，但当前没有选中该患者
            // 保存到该患者的缓存中，但不显示在当前界面
            if (patientMessagesCache[messagePatientId]) {
              patientMessagesCache[messagePatientId].push({
                role: 'assistant',
                content: messageContent,
                ts: new Date()
              });
            } else {
              patientMessagesCache[messagePatientId] = [{
                role: 'assistant',
                content: messageContent,
                ts: new Date()
              }];
            }
            savePatientMessagesCache();
            var msgPId = message.patient_id || message.patientId || message.patient_uuid || message.uuid;
            var activeId = window.currentPatientId;
            if (!activeId && window.activeClinicSelection) activeId = window.activeClinicSelection.id;
            if (!activeId && window.activeClinicSelection && window.activeClinicSelection.patient) activeId = window.activeClinicSelection.patient.id || window.activeClinicSelection.patient.patient_id;
            
            if (!window.currentPatientId && msgPId) {
              window.currentPatientId = msgPId;
              console.log("[WS_AUTO_RESTORE] 自动恢复 currentPatientId:", msgPId);
            }
            if ((!window.activeClinicSelection || (window.activeClinicSelection && window.activeClinicSelection.type == "new")) && msgPId) {
              var foundP = null;
              if (window.clinicSessions) {
                for (var pi = 0; pi < window.clinicSessions.length; pi++) {
                  var pt = window.clinicSessions[pi];
                  if (pt.id === msgPId || pt.patient_id === msgPId || pt.uuid === msgPId) { foundP = pt; break; }
                }
              }
              window.activeClinicSelection = { type: "patient", id: msgPId, patient: foundP || null };
              console.log("[WS_AUTO_RESTORE] 自动恢复 activeClinicSelection:", msgPId);
            }
            
            var finalActiveId = window.currentPatientId || (window.activeClinicSelection ? window.activeClinicSelection.id : null) || msgPId;
            var shouldDisplay = finalActiveId === msgPId;
            
            console.log("[WS_MESSAGE_MATCH]", {
              messagePatientId: msgPId,
              activePatientIdBefore: activeId,
              finalActivePatientId: finalActiveId,
              currentPatientId: window.currentPatientId,
              activeClinicSelection: window.activeClinicSelection ? window.activeClinicSelection.id : null,
              shouldDisplay: shouldDisplay
            });
            
            if (shouldDisplay) {
              console.log("[DEBUG] WS消息匹配当前患者，准备渲染:", msgPId);
            } else {
              if (pendingPatientId === msgPId) { pendingPatientId = null; }
              return;
            }
          }

          // 如果是医知快答频道的消息，且当前在医知快答模式，确保currentPatientId正确设置
          if (shouldShowAiAssistantMessage && currentPatientId !== 'common') {
            console.log(`[DEBUG] 收到医知快答消息，切换到医知快答频道（currentPatientId: ${currentPatientId} -> common）`);
            currentPatientId = 'common';
          }

          // 如果消息属于当前显示的患者（或没有patient_id），正常显示并保存
          // 传递时间戳用于去重
          const assistantTimestamp = messageContent.timestamp ? (typeof messageContent.timestamp === 'number' ? new Date(messageContent.timestamp) : new Date(messageContent.timestamp)) : null;
          appendMessage('assistant', messageContent, assistantTimestamp);

          // 清空pendingPatientId，表示已收到回复
          if (currentPatientId && currentPatientId === messagePatientId) {
            pendingPatientId = null;
          }

          // 如果消息中包含诊疗建议（通过检查消息文本是否包含病名、证型等关键词，或者检查是否有特定的标记）
          // 当收到诊疗建议后，刷新当前患者信息
          if (messageContent) {
            // 检查是否有interactive_qa_finished标记（表示诊疗建议已完成）
            const hasTreatmentFinished = data.answer?.interactive_qa_finished === true || messageContent.interactive_qa_finished === true;
            const text = messageContent.text || '';

            // 更准确地检测诊疗建议格式：检查是否包含【病名】格式（如【急性支气管炎】）
            const hasDiseaseFormat = /【[^】]+】/.test(text);
            // 检查是否包含诊疗建议的关键词（证型、方剂等）
            const hasTreatmentKeywords = hasDiseaseFormat ||
                                        (text.includes('证型') && text.includes('：')) ||
                                        text.includes('证型:') ||
                                        (text.includes('方剂') && (text.includes('：') || text.includes(':'))) ||
                                        text.includes('辨证选方') ||
                                        (text.includes('风寒') && (text.includes('证型') || text.includes('袭'))) ||
                                        (text.includes('支气管') && (text.includes('炎') || text.includes('病')));

            // 兜底逻辑：
            // 在问诊模式下，只要收到“当前患者”的助手回复，且不是交互问答的中间轮次，也尝试刷新一次患者信息，
            // 防止后端已经写入调理信息但文案格式未命中上述关键字时，调理笺按钮无法启用的问题。
            const isClinicPatientMessage = activeMode === 'clinic' && messagePatientId && messagePatientId !== 'common';
            const isInteractiveQAStage = data.answer?.interactive_qa === true;
            const shouldRefresh =
              hasTreatmentFinished ||
              hasTreatmentKeywords ||
              (isClinicPatientMessage && !isInteractiveQAStage);

            if (shouldRefresh) {
              // 如果是在复诊流程中完成开方，清空复诊流程状态
              if (isInFollowupFlow) {
                isInFollowupFlow = false;
                followupSymptom = '';
              }

              // 延迟刷新，使用多次重试机制，确保在保存完成后能刷新成功
              const refreshWithRetry = (retryCount = 0) => {
                const delay = retryCount === 0 ? 3000 : retryCount === 1 ? 5000 : 7000; // 首次3秒，第二次5秒，第三次7秒（延迟更久以确保保存完成）
                setTimeout(() => {
                  refreshCurrentPatientInfo();
                  // 刷新完成后，重新更新患者信息栏以显示"开始复诊"按钮
                  if (currentPatientId) {
                    const patient = getPatientById(currentPatientId);
                    if (patient) {
                      updatePatientInfoBar(patient);
                    }
                  }
                  // 如果还有重试次数，继续重试（最多重试3次）
                  if (retryCount < 2) {
                    refreshWithRetry(retryCount + 1);
                  }
                }, delay);
              };
              refreshWithRetry();
            }
          }

        } catch (e) {

          // JSON解析失败，尝试作为纯文本处理
          appendMessage('assistant', { text: event.data });

        }

      };

    }



    async function ensureConnected() {

      // 检查连接状态，如果断开则尝试重连
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        // 尝试自动重连
        const connected = await ensureWsConnected();
        if (!connected) {
          showConnectionStatusBar('连接失败，请稍后重试', 'red');
          return false;
        }
      }

      if (activeMode === 'clinic' && activeClinicSelection && activeClinicSelection.type === 'new') {

        alert('当前为新增客户，请先填写并保存客户基本信息。');

        return false;

      }

      if (!currentUser.phone) {
        // 自动跳转到登录页面
        if (!ensureLoggedIn()) {
          return false;
        }
      }

      return true;

    }



    async function sendChat() {

      const input = document.getElementById('chat-input');

      if (!input) return;

      const text = input.value.trim();

      if (!text) return;

      // 检查登录状态
      if (!ensureLoggedIn()) return;

      if (!(await ensureConnected())) return;

      // 验证用户信息（防止异步问题）
      if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
        // 尝试重新加载用户信息
        try {
          const raw = localStorage.getItem('clinical_user');
          if (raw) {
            const stored = JSON.parse(raw);
            if (stored && stored.phone) {
              currentUser.phone = stored.phone;
              currentUser.token = stored.token || '';
              currentUser.chatlogId = stored.chatlogId || '0';
            }
          }
        } catch (e) {
          console.error('重新加载用户信息失败:', e);
        }

        // 再次验证（静默处理，不显示错误提示）
        if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
          // 静默失败，不显示错误提示
          console.warn('用户登录信息缺失，无法发送消息');
          return;
        }
      }

      // 医知快答拉取/保存 common 聊天记录依赖 token；发送前若 token 为空则尝试从 localStorage 再读一次
      if (activeMode === 'corpus') {
        try {
          const raw = localStorage.getItem('clinical_user');
          if (raw) {
            const stored = JSON.parse(raw);
            if (stored && stored.token) {
              currentUser.token = stored.token;
              currentUser.chatlogId = stored.chatlogId || currentUser.chatlogId || '0';
            }
          }
        } catch (e) {}
      }
      // 患者问诊模式必须包含数据库选择
      // 确定患者ID：患者问诊模式使用实际患者ID，医知快答模式使用 common
      const patientId = activeMode === 'clinic' && activeClinicSelection.type === 'patient'
        ? activeClinicSelection.id
        : (activeMode === 'corpus' ? 'common' : '');

      const payload = {

        token: currentUser.token || '',  // 医知快答拉取/保存 common 聊天记录必填

        phone: currentUser.phone.trim(),  // 确保去除空格

        chatlog_id: currentUser.chatlogId || '0',

        platform: activeMode === 'clinic' ? 'clinic' : 'corpus',

        message_data: {

          text,

          mode: activeMode,

          corpus_ids: (() => {
            if (activeMode === 'corpus') {
              var ids = [getCorpusDefaultDbId()];
              if (activeCorpusSelection.has('db-doc')) ids.push('db-doc');
              return ids;
            }
            return Array.from(activeCorpusSelection || []);
          })(),
          patient_id: patientId,
          db_choice: activeMode === 'clinic' ? (selectedClinicDb || getClientDefaultDb()) : '',
          default_db: getClientDefaultDb(),
          corpus_default_db: activeMode === 'corpus' ? getClientDefaultDb() : '',
          client_id: getCurrentClientId(),
          // 患者问诊与医知快答均由后端调 6005 时需 token，后端从顶层或此处读取
          token: currentUser.token || ''

        }

      };

      // 最终验证payload（静默处理，不显示错误提示）
      if (!payload.phone || payload.phone.trim() === '') {
        console.warn('手机号无效，无法发送消息');
        return;
      }

      if (!payload.message_data) {
        console.warn('消息数据无效，无法发送消息');
        return;
      }

      // 记录发送消息时的患者ID（用于跟踪等待回复的消息属于哪个患者）
      // 医知快答模式使用 common 作为患者ID
      pendingPatientId = patientId || null;

      appendMessage('user', text);

      input.value = '';
      // 重置输入框高度
      autoResizeChatInput(input);



      try {

        ws.send(JSON.stringify(payload));

      } catch (e) {

        // 在底部状态栏显示连接状态，不添加到聊天记录
        appendMessage('system', `消息发送失败：${e.message}`);
        showConnectionStatusBar('消息发送失败，请检查连接状态。', 'red');

      }

    }



    function quickFill(text) {

      const input = document.getElementById('chat-input');

      if (!input) return;

      input.value = text;

      autoResizeChatInput(input);

      if (!(document.body && document.body.classList.contains('mobile-mode'))) {
        input.focus();
      }

    }

    // 生成文书（调理笺，名称随所选开方库变化）
    async function generateTreatmentPlan() {
      const docTitle = getPrescriptionTitle(selectedClinicDb);
      // 检查登录状态
      if (!ensureLoggedIn()) return;
      if (!(await ensureConnected())) return;

      // 检查是否在问诊模式且有选中的患者
      if (activeMode !== 'clinic' || !activeClinicSelection || activeClinicSelection.type !== 'patient') {
        appendMessage('system', '请先选择客户并完成评估后再生成' + docTitle + '。');
        return;
      }

      const patientId = activeClinicSelection.id;
      if (!patientId) {
        appendMessage('system', '请先选择客户。');
        return;
      }

      // 验证用户信息（静默处理）
      if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
        console.warn('用户登录信息缺失，无法生成' + docTitle);
        return;
      }

      // 发送生成文书请求
      if (ws && ws.readyState === WebSocket.OPEN) {
        const payload = {
          token: currentUser.token || '',
          phone: currentUser.phone.trim(),  // 确保去除空格
          chatlog_id: currentUser.chatlogId || '0',
          platform: 'clinic',
          message_data: {
            text: '',  // 空文本
            mode: 'clinic',
            patient_id: patientId,
            generate_treatment: true,
            db_choice: selectedClinicDb || getClientDefaultDb(),  // 确保有默认值
            default_db: getClientDefaultDb(),
            client_id: getCurrentClientId()
          }
        };

        // 验证payload（静默处理）
        if (!payload.phone || payload.phone.trim() === '' || !payload.message_data) {
          console.warn('生成' + docTitle + '失败：数据验证失败');
          return;
        }

        try {
          ws.send(JSON.stringify(payload));
        } catch (e) {
          appendMessage('system', `生成${docTitle}失败：${e.message}`);
        }
      } else {
        showConnectionStatusBar('WebSocket未连接，无法生成' + docTitle, 'red');
      }
    }



