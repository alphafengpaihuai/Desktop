    async function saveNewPatient(startQa = false) {
// 统一患者ID规范化
function normalizePatientId(patient) {
  if (!patient) return null;
  var pid = patient.id || patient.patient_id || patient.uuid || 'local_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  patient.id = pid;
  patient.patient_id = pid;
  patient.uuid = pid;
  return pid;
}

// 合并患者入列表（不覆盖已有患者）
function mergePatientIntoList(newPatient) {
  window.allPatients = Array.isArray(window.allPatients) ? window.allPatients : [];
  window.clinicSessions = Array.isArray(window.clinicSessions) ? window.clinicSessions : [];
  var pid2 = normalizePatientId(newPatient);
  if (!pid2) return null;
  var merge = function(list) {
    var i = list.length;
    while (i--) {
      if (list[i].id === pid2 || list[i].patient_id === pid2 || list[i].uuid === pid2) {
        var merged = list.slice();
        merged[i] = Object.assign({}, merged[i], newPatient);
        return merged;
      }
    }
    var result = [newPatient];
    var j = 0;
    while (j < list.length) { result.push(list[j]); j++; }
    return result;
  };
  window.allPatients = merge(window.allPatients);
  window.clinicSessions = merge(window.clinicSessions);
  return pid2;
}


      const nameEl = document.getElementById('new-patient-name');

      const ageEl = document.getElementById('new-patient-age');

      const genderEl = document.getElementById('new-patient-gender');

      const symptomEl = document.getElementById('new-patient-symptom');

      const phoneEl = document.getElementById('new-patient-phone');

      const noteEl = document.getElementById('new-patient-note');

      const errorEl = document.getElementById('new-patient-error');

      if (!nameEl || !ageEl || !genderEl || !symptomEl || !errorEl) return;



      const name = nameEl.value.trim();

      const age = ageEl.value.trim();

      const gender = genderEl.value.trim();

      const symptom = symptomEl.value.trim();

      const phone = phoneEl ? phoneEl.value.trim() : '';

      const note = noteEl ? noteEl.value.trim() : '';



      if (!name || !age || !gender || !symptom) {

        errorEl.textContent = '请完整填写姓名、年龄、性别和症状信息。';

        return;

      }

      // 验证用户登录状态（静默处理，不显示错误提示）
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
              // 如果重新加载成功，继续执行
              if (currentUser.phone && currentUser.phone.trim() !== '') {
                // 成功，继续
              } else {
                console.warn('用户登录信息缺失，无法保存患者');
                return;
              }
            } else {
              console.warn('用户登录信息缺失，无法保存患者');
              return;
            }
          } else {
            console.warn('用户登录信息缺失，无法保存患者');
            return;
          }
        } catch (e) {
          console.error('重新加载用户信息失败:', e);
          return;
        }
      }



      errorEl.textContent = '';



      // 将年龄转换为字符串格式（如 "24岁"）
      const ageStr = age.includes('岁') ? age : `${age}岁`;
      // 提取年龄数字（用于兼容旧的 birth 字段）
      const ageNum = parseInt(age.replace('岁', '')) || 0;

      const payload = {
        doctor_id: currentUser.phone,
        name,
        age: ageStr,  // 新字段：年龄字符串
        birth: ageNum,  // 兼容字段：年龄数字（放在 birth 中，用于兼容旧 API）
        gender,
        memo: note,
        detail: {
          symptom,
          client_id: getCurrentClientId(),
          phone: phone || ''  // 患者手机号可选，如果为空则传空字符串
        }
      };

      let newPatient = null;
      try {
        newPatient = await createPatientOnServer(payload);
      } catch (err) {
        errorEl.textContent = `保存失败：${err.message}`;
        return;
      }

      const summary = newPatient.summary || (symptom.length > 40 ? symptom.slice(0, 40) + '…' : symptom);
      newPatient = { ...newPatient, summary };

      const patientId = newPatient.id || newPatient.patient_id || newPatient.uuid || 'local_' + Date.now();
      newPatient.id = patientId;
      newPatient.patient_id = patientId;
      newPatient.uuid = patientId;

      // 将新患者添加到所有患者列表的开头
      allPatients = [newPatient, ...allPatients];
      // 应用当前搜索（如果有）
      applyPatientSearch(patientSearchQuery);

      activeClinicSelection = { type: 'patient', id: patientId };

      // 切换消息：新患者默认没有聊天记录，不要在这里阻塞等待云端 chatlog
      // 否则一旦远端 chatlog 接口卡住，会导致“保存并开始问诊”没有任何反应且后端收不到WS消息。
      if (currentPatientId && currentPatientId !== newPatient.id) {
        try {
          if (messages.length > 0) {
            patientMessagesCache[currentPatientId] = [...messages];
            savePatientMessagesCache();
            console.log(`[DEBUG] 保存患者[${currentPatientId}]的消息，共${messages.length}条`);
          }
        } catch (e) {
          console.warn('[DEBUG] 保存旧患者消息缓存失败:', e);
        }
      }
      currentPatientId = newPatient.id;
      messages.length = 0;
      renderMessages();
      updateSidebarReferencesFromMessages();
      // 预创建缓存条目，避免后续推送到来时需要分支判断
      if (!patientMessagesCache[newPatient.id]) {
        patientMessagesCache[newPatient.id] = [];
        savePatientMessagesCache();
      }

      // 更新患者信息显示栏
      updatePatientInfoBar(newPatient);



      // 清空表单

      nameEl.value = '';

      ageEl.value = '';

      genderEl.value = '';
      if (phoneEl) phoneEl.value = '';

      if (noteEl) noteEl.value = '';

      symptomEl.value = '';



      renderClinicPanel();

      updateClinicDetailUI();

      // 使患者信息栏、侧边栏等根据当前选中患者正确显示
      updateModeSpecificUI();

      // 从服务端刷新患者列表，保证“之前创建的患者”也能显示
      try {
        await syncPatients(false);
        // 若服务端尚未返回新患者，把新患者重新加回列表，避免选中项丢失
        if (!clinicSessions.find(p => p.id === newPatient.id)) {
          allPatients = [newPatient, ...allPatients];
          applyPatientSearch(patientSearchQuery);
        }
        activeClinicSelection = { type: 'patient', id: patientId };
        renderClinicPanel();
        updateClinicDetailUI();
        updateModeSpecificUI();
      } catch (e) {
        console.warn('[DEBUG] 创建患者后刷新列表失败（已忽略）:', e);
      }

      console.log('[CREATE_PATIENT_DONE]', {
        newPatient: newPatient,
        patientId: patientId,
        currentPatientId: window.currentPatientId
      });

      console.log('[START_ASSESSMENT_REQUEST]', {
        patientId: patientId,
        newPatient: newPatient,
        currentPatientId: window.currentPatientId,
        activeClinicSelection: window.activeClinicSelection
      });

      if (startQa) {
        // 保存并开始问诊：立即启动交互性问答
        appendMessage('system', `已创建新客户：${name}，正在生成第一个问题...`);

        // 再次验证用户信息（静默处理）
        if (!currentUser || !currentUser.phone || currentUser.phone.trim() === '') {
          console.warn('用户登录信息缺失，无法启动交互性问答');
          return;
        }

        // 验证患者ID（静默处理）
        if (!patientId || typeof patientId !== 'string') {
          console.error('[START_ASSESSMENT_ABORT] patientId异常', { patientId: patientId, newPatient: newPatient });
          return;
        }

        console.log('[DEBUG] start_interactive_qa: 准备启动', {
          ws_readyState: ws ? ws.readyState : null,
          phone: currentUser.phone,
          patient_id: patientId,
          db_choice: selectedClinicDb || getClientDefaultDb()
        });

        // 先确保 WebSocket 已连接（可能页面刚加载或重连中），否则后端收不到消息
        const connected = await ensureWsConnected();
        console.log('[DEBUG] start_interactive_qa: ensureWsConnected结果', {
          connected,
          ws_readyState: ws ? ws.readyState : null
        });
        if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
          errorEl.textContent = 'WebSocket未连接，无法启动交互性问答';
          showConnectionStatusBar('WebSocket未连接，请稍候再试或刷新页面', 'red');
          return;
        }

        // 立即启动交互性问答，后端会立即返回第一个问题
        // 启动问诊前强制设置当前患者状态
        window.currentPatientId = patientId;
        window.activeClinicSelection = window.activeClinicSelection || {};
        window.activeClinicSelection.type = "patient";
        window.activeClinicSelection.id = patientId;
        window.activeClinicSelection.patient = newPatient;

        // 启动问诊前强制设置当前患者状态
        window.currentPatientId = patientId;
        window.activeClinicSelection = window.activeClinicSelection || {};
        window.activeClinicSelection.type = "patient";
        window.activeClinicSelection.id = patientId;
        window.activeClinicSelection.patient = newPatient;

        pendingPatientId = patientId;

        const payload = {
          token: currentUser.token || '',
          phone: currentUser.phone.trim(),
          chatlog_id: currentUser.chatlogId || '0',
          platform: 'clinic',
          message_data: {
            text: '',
            mode: 'clinic',
            patient_id: patientId,
            start_interactive_qa: true,
            db_choice: selectedClinicDb || getClientDefaultDb(),
            default_db: getClientDefaultDb(),
            client_id: getCurrentClientId(),
            patient_info: newPatient,
            debug_ts: Date.now()
          }
        };

        if (!payload.phone || payload.phone.trim() === '') {
          console.warn('手机号无效，无法启动问诊');
          return;
        }
        if (!payload.message_data) {
          console.warn('消息数据无效，无法启动问诊');
          return;
        }

        try {
          console.log('[DEBUG] start_interactive_qa: 即将发送WS消息', {
            ws_readyState: ws ? ws.readyState : null,
            phone: payload.phone,
            patient_id: payload.message_data.patient_id,
            start_interactive_qa: payload.message_data.start_interactive_qa,
            db_choice: payload.message_data.db_choice,
            debug_ts: payload.message_data.debug_ts
          });
          // 发送前再次确保当前患者状态（防止同步操作覆盖）
            window.currentPatientId = patientId;
            window.activeClinicSelection = window.activeClinicSelection || {};
            window.activeClinicSelection.type = "patient";
            window.activeClinicSelection.id = patientId;
          ws.send(JSON.stringify(payload));
        } catch (e) {
          errorEl.textContent = `启动交互性问答失败：${e.message}`;
          appendMessage('system', `启动交互性问答失败：${e.message}`);
        }
      } else {
        // 只保存：不启动问诊流程
        appendMessage('system', `已保存新客户：${name}。点击「保存并开始评估」或选择该客户后开始评估。`);
      }

    }



    // 页面卸载时清理资源
    window.addEventListener('beforeunload', () => {
      // 停止心跳
      stopHeartbeat();
      // 取消重连
      cancelReconnect();
      // 关闭WebSocket连接
      if (ws) {
        ws.close();
      }
    });

    window.addEventListener('load', () => {

      lucide.createIcons();



      // 从登录页读取用户信息

      let stored = null;

      try {

        const raw = localStorage.getItem('clinical_user');

        if (raw) {

          stored = JSON.parse(raw);

        }

      } catch (e) {

        // 解析失败时视为未登录

      }



      const note = document.getElementById('connection-note');

      // 演示模式：在检查登录前注入模拟用户
      if (!stored || !stored.phone) {
        try {
          stored = {
            phone: 'demo_doctor',
            token: 'demo_token',
            chatlogId: '0',
            token_expires_at: Date.now() + 99999999999,
            defaultModel: 'gpt-4'
          };
          localStorage.setItem('clinical_user', JSON.stringify(stored));
        } catch(e) {}
      }

      if (!stored || !stored.phone || !stored.token || !isClinicalSessionValid(stored)) {

        if (note) {
          note.innerHTML = '<i data-lucide="alert-circle" width="12" class="text-amber-600"></i><span>未检测到有效登录信息，将跳转到登录页面。</span>';
          note.className = 'text-[11px] text-slate-500 flex items-center gap-1.5';
        }

        appendMessage('system', '未检测到有效登录信息，将跳转到登录页面。');
        lucide.createIcons();

        setTimeout(() => {

          redirectToLogin();

        }, 800);

        return;

      }



      currentUser.phone = stored.phone;

      currentUser.token = stored.token || '';

      currentUser.chatlogId = stored.chatlogId || '0';

      // 刷新会话截止时间：每次成功进入工作台即续期 365 天（与 login 页写入策略一致）
      try {
        stored.token_expires_at = Date.now() + CLINICAL_SESSION_MS;
        localStorage.setItem('clinical_user', JSON.stringify(stored));
      } catch (e) {
        console.warn('续写登录有效期失败', e);
      }

      // 读取保存的模型选择
      if (stored.defaultModel) {
        selectedClinicModel = stored.defaultModel;
      }

      updateHeaderUser();

      if (note) {
        note.innerHTML = '';
        note.className = 'text-[11px] text-slate-500 flex items-center gap-1.5';
      }

      lucide.createIcons();



      // 渲染侧边栏数据并默认进入问诊模式

      renderClinicPanel();

      renderCorpusPanel();

      // 恢复上次模式（医知快答/患者问诊），刷新后保持
      const savedMode = (localStorage.getItem(getClientStorageKey('last_active_mode')) === 'corpus') ? 'corpus' : 'clinic';
      switchMode(savedMode);

      // 从localStorage读取数据库选择（个人设置页面保存的）
      loadClinicDbFromSettings();

      // 确保按钮文本已更新（延迟一下确保DOM已加载）
      setTimeout(() => {
        updateClinicDependentLabels();
        updateHeaderUser(); // 更新头部用户信息（包括数据库名称）
      }, 100);

      // 初始化模型选择器（使用保存的模型选择，如果没有则使用默认值）
      selectClinicModel(selectedClinicModel);

      // 建立 WebSocket 连接

      connectWs();

      // 同步医生名下病案（同步后会更新患者信息显示并恢复消息）
      // 注意：syncPatients 内部已经处理了恢复消息和更新患者信息栏的逻辑
      syncPatients(true);

      // === 演示模式：当后端不可用时，使用本地模拟数据 ===
      setTimeout(function checkAndInjectMockData() {
        console.log('[演示模式] 检查中... allPatients.length=' + allPatients.length + ' _mockDataInjected=' + window._mockDataInjected);
        if (allPatients.length === 0 && !window._mockDataInjected) {
          window._mockDataInjected = true;
          
          // === 循证医学知识库 ===
          const ebmLibrary = {
            '失眠': {
              title: '失眠（Insomnia）— 循证参考',
              definition: '失眠是指尽管有充足的睡眠机会和环境，仍持续存在入睡困难、睡眠维持困难或早醒，并导致日间功能受损。',
              epidemiology: '中国成人失眠患病率约 15-30%，其中慢性失眠约 10-15%。女性发病率约为男性的1.4倍，老年人发病率更高。',
              guidelines: [
                { source: '《中国成人失眠诊断与治疗指南(2023版)》', recommendation: '认知行为疗法（CBT-I）为慢性失眠的首选治疗方案，药物治疗应短期使用。' },
                { source: '《中医失眠诊疗专家共识》', recommendation: '辨证论治：肝郁化火证选龙胆泻肝汤，心脾两虚证选归脾汤，阴虚火旺证选黄连阿胶汤。' },
                { source: '美国睡眠医学会（AASM）指南', recommendation: '推荐使用失眠认知行为疗法(CBT-I)作为一线治疗方案。' }
              ],
              drugReference: [
                { drug: '褪黑素', dosage: '0.5-5mg qn', evidence: '短期使用(≤3个月)对睡眠时相延迟有效' },
                { drug: '右佐匹克隆', dosage: '1-3mg qn', evidence: 'A级推荐，可缩短入睡时间' },
                { drug: '酸枣仁汤', dosage: '按方加减', evidence: '多项RCT显示可改善睡眠质量，PSQI评分降低' }
              ]
            },
            '胃病': {
              title: '功能性消化不良（FD）— 循证参考',
              definition: '功能性消化不良是指存在餐后饱胀不适、早饱、上腹痛、上腹烧灼感等症状之一，经检查排除器质性病变。',
              epidemiology: '全球患病率约 10-30%，中国约 18-23%。女性略多于男性，与焦虑抑郁状态显著相关。',
              guidelines: [
                { source: '《中国功能性消化不良专家共识(2022)》', recommendation: '根除Hp治疗对部分患者有效；促动力药、质子泵抑制剂为一线用药。' },
                { source: '《中医脾胃病诊疗指南》', recommendation: '肝胃不和证：柴胡疏肝散；脾胃虚弱证：香砂六君子汤。' },
                { source: '罗马Ⅳ标准', recommendation: '诊断需满足餐后不适综合征(PDS)或上腹痛综合征(EPS)标准。' }
              ],
              drugReference: [
                { drug: '多潘立酮', dosage: '10mg tid', evidence: '促进胃动力，改善餐后饱胀' },
                { drug: '奥美拉唑', dosage: '20mg qd', evidence: '对EPS型FD有效' },
                { drug: '香砂六君子汤', dosage: '按方加减', evidence: 'Meta分析显示可改善胃排空功能' }
              ]
            },
            '月经不调': {
              title: '月经失调— 循证参考',
              definition: '月经失调包括月经周期、经期、经量的异常，常见类型有闭经、月经过少、月经稀发、崩漏等。',
              epidemiology: '育龄期女性中约 20-30% 存在不同程度的月经失调。多囊卵巢综合征(PCOS)是最常见病因之一。',
              guidelines: [
                { source: '《中国异常子宫出血诊疗指南(2022版)》', recommendation: '首先排除妊娠相关出血，根据PALM-COEIN分类制定个体化方案。' },
                { source: '《中医妇科学》', recommendation: '肾虚证选左归丸，肝郁证选逍遥散，血瘀证选桃红四物汤。' },
                { source: 'FIGO异常子宫出血分类系统', recommendation: '结构性病因(PALM)与非结构性病因(COEIN)需分别处理。' }
              ],
              drugReference: [
                { drug: '地屈孕酮', dosage: '10-20mg qd', evidence: '调节月经周期，改善黄体功能不全' },
                { drug: '短效避孕药', dosage: '按周期服用', evidence: '调整周期，改善经量过多' },
                { drug: '逍遥散', dosage: '按方加减', evidence: '改善肝郁型月经不调，调节下丘脑-垂体-卵巢轴' }
              ]
            },
            '高血压': {
              title: '高血压（Hypertension）— 循证参考',
              definition: '高血压指在未使用降压药物的情况下，非同日3次测量诊室血压，SBP≥140mmHg和/或DBP≥90mmHg。',
              epidemiology: '中国成人高血压患病率约 27.5%（约2.45亿），知晓率约51.6%，控制率约16.8%。',
              guidelines: [
                { source: '《中国高血压防治指南(2022年)》', recommendation: '降压目标：一般患者<140/90mmHg，可耐受者<130/80mmHg。' },
                { source: '欧洲心脏病学会(ESC)指南', recommendation: '起始联合治疗作为大多数患者的优选方案。' },
                { source: '《中医高血压诊疗专家共识》', recommendation: '肝阳上亢证选天麻钩藤饮，阴虚阳亢证选镇肝熄风汤。' }
              ],
              drugReference: [
                { drug: '氨氯地平', dosage: '5-10mg qd', evidence: 'CCB类，降压效果稳定，中国人反应良好' },
                { drug: '缬沙坦', dosage: '80-160mg qd', evidence: 'ARB类，保护靶器官，耐受性好' },
                { drug: '天麻钩藤饮', dosage: '按方加减', evidence: 'Meta分析显示联合西药降压效果优于单用西药' }
              ]
            },
            '痤疮': {
              title: '痤疮（Acne Vulgaris）— 循证参考',
              definition: '痤疮是一种累及毛囊皮脂腺的慢性炎症性疾病，以粉刺、丘疹、脓疱、结节、囊肿为特征。',
              epidemiology: '中国青少年患病率约 45-70%，约 20% 的成年人持续存在痤疮。好发于面部、胸背部。',
              guidelines: [
                { source: '《中国痤疮治疗指南(2022版)》', recommendation: '分级治疗：轻度-外用维A酸类；中度-联合口服抗生素/异维A酸。' },
                { source: '中医诊疗指南', recommendation: '肺经风热证选枇杷清肺饮，肠胃湿热证选茵陈蒿汤，痰瘀互结证选海藻玉壶汤。' },
                { source: '欧洲皮肤病论坛(EADV)指南', recommendation: '强调早期干预预防瘢痕形成，联合治疗优于单药。' }
              ],
              drugReference: [
                { drug: '阿达帕林凝胶', dosage: '每晚1次', evidence: '维A酸类，改善角化异常，抗炎' },
                { drug: '米诺环素', dosage: '50-100mg bid', evidence: '四环素类，抑制痤疮丙酸杆菌' },
                { drug: '枇杷清肺饮', dosage: '按方加减', evidence: '改善肺经风热型痤疮，抑制皮脂分泌' }
              ]
            }
          };

          // 为每个患者关联循证关键词
          const patientEbmMap = {
            'mock_张丽华_35_女_001': '失眠',
            'mock_李明_42_男_002': '胃病',
            'mock_王芳_28_女_003': '月经不调',
            'mock_陈建国_55_男_004': '高血压',
            'mock_刘小红_31_女_005': '痤疮'
          };

          const mockPatients = [
            {
              id: 'mock_张丽华_35_女_001',
              name: '张丽华',
              age: 35,
              gender: '女',
              firstVisit: Date.now() - 86400000 * 3,
              lastVisit: Date.now() - 3600000 * 2,
              visitCount: 3,
              summary: '失眠多梦半年余，入睡困难，易惊醒，伴心悸、头晕，面色少华，舌淡苔薄白，脉细弱。平素工作压力大，情绪易焦虑。',
              detail: {
                gender: '女',
                age: 35,
                symptom: '失眠多梦半年余，入睡困难，易惊醒，伴心悸、头晕，面色少华',
                treatment_timeline: [
                  {
                    date: new Date(Date.now() - 86400000 * 7).toISOString(),
                    treatment: '酸枣仁汤合归脾汤加减',
                    completed: true
                  }
                ]
              }
            },
            {
              id: 'mock_李明_42_男_002',
              name: '李明',
              age: 42,
              gender: '男',
              firstVisit: Date.now() - 86400000 * 7,
              lastVisit: Date.now() - 3600000 * 5,
              visitCount: 2,
              summary: '胃脘胀痛反复发作2年，食后尤甚，嗳气反酸，纳差，大便溏薄，日2-3次。舌质淡红，苔白腻，脉弦滑。',
              detail: {
                gender: '男',
                age: 42,
                symptom: '胃脘胀痛反复发作2年，食后尤甚，嗳气反酸',
                treatment_timeline: [
                  {
                    timestamp: Math.floor((Date.now() - 86400000 * 14) / 1000),
                    symptom: '胃脘胀痛反复发作2年，食后尤甚，嗳气反酸，纳差，大便溏薄',
                    treatment_completed: true,
                    disease_name: '胃脘痛',
                    syndrome: '肝胃不和证',
                    prescription: '香砂六君子汤合左金丸加减'
                  },
                  {
                    timestamp: Math.floor((Date.now() - 86400000 * 3) / 1000),
                    symptom: '药后胃脘胀痛减轻，嗳气减少，仍纳差，大便偏溏，日1-2次。舌淡红苔薄白，脉细',
                    treatment_completed: true,
                    disease_name: '胃痞病',
                    syndrome: '脾气虚证',
                    prescription: '参苓白术散加减'
                  }
                ]
              }
            },
            {
              id: 'mock_王芳_28_女_003',
              name: '王芳',
              age: 28,
              gender: '女',
              firstVisit: Date.now() - 86400000 * 1,
              lastVisit: Date.now() - 3600000,
              visitCount: 1,
              summary: '月经不调半年，月经推迟7-15天，量少色暗，伴痛经，经前乳胀。平素手足不温，畏寒。舌淡暗苔白，脉沉迟。',
              detail: {
                gender: '女',
                age: 28,
                symptom: '月经不调半年，月经推迟7-15天，量少色暗，伴痛经',
                treatment_timeline: []
              }
            },
            {
              id: 'mock_陈建国_55_男_004',
              name: '陈建国',
              age: 55,
              gender: '男',
              firstVisit: Date.now() - 86400000 * 30,
              lastVisit: Date.now() - 86400000 * 1,
              visitCount: 5,
              summary: '高血压病史3年，头晕头胀反复发作，面色红赤，性情急躁，口苦咽干，大便干结，2-3日一行。舌红苔黄，脉弦数。',
              detail: {
                gender: '男',
                age: 55,
                symptom: '头晕头胀反复发作，面色红赤，性情急躁',
                treatment_timeline: [
                  {
                    timestamp: Math.floor((Date.now() - 86400000 * 21) / 1000),
                    symptom: '头晕头胀，面色红赤，性情急躁，口苦咽干，大便干结',
                    treatment_completed: true,
                    disease_name: '眩晕病',
                    syndrome: '肝阳上亢证',
                    prescription: '天麻钩藤饮加减'
                  },
                  {
                    timestamp: Math.floor((Date.now() - 86400000 * 7) / 1000),
                    symptom: '头晕减轻，仍觉头胀，血压波动，睡眠欠佳',
                    treatment_completed: true,
                    disease_name: '眩晕病',
                    syndrome: '阴虚阳亢证',
                    prescription: '镇肝熄风汤加减'
                  }
                ]
              }
            },
            {
              id: 'mock_刘小红_31_女_005',
              name: '刘小红',
              age: 31,
              gender: '女',
              firstVisit: Date.now() - 86400000 * 14,
              lastVisit: Date.now() - 3600000 * 8,
              visitCount: 2,
              summary: '面部痤疮反复发作3个月，以口周及下颌为甚，色红有脓头，伴口干口臭，大便粘滞。舌红苔黄腻，脉滑数。',
              detail: {
                gender: '女',
                age: 31,
                symptom: '面部痤疮反复发作3个月，口周及下颌为甚',
                treatment_timeline: []
              }
            }
          ];

          // 注入模拟患者数据
          allPatients = mockPatients;
          
          // 为模拟患者添加处方天数（prescriptionDays）和复诊提醒
          const now = Date.now();
          const followUpMap = {
            'mock_张丽华_35_女_001': { days: 7, daysOverdue: 0 },   // 今日复诊
            'mock_李明_42_男_002': { days: 7, daysOverdue: 2 },    // 超期2天
            'mock_王芳_28_女_003': { days: 14, daysOverdue: -13 }, // 还有13天
            'mock_陈建国_55_男_004': { days: 7, daysOverdue: -4 }, // 还有4天
            'mock_刘小红_31_女_005': { days: 7, daysOverdue: 14 }  // 超期14天
          };
          
          clinicSessions = mockPatients.map(p => {
            const fu = followUpMap[p.id] || { days: 7, daysOverdue: 0 };
            const daysOverdue = fu.daysOverdue;
            const needsFollowUp = daysOverdue >= 0;
            // 反推 nextFollowupDate 和 mockLastVisit，使逻辑自洽
            const nextFollowupDate = now - daysOverdue * 86400000;
            const mockLastVisit = nextFollowupDate - fu.days * 86400000;
            return {
              id: p.id,
              name: p.name,
              age: p.age,
              gender: p.gender,
              firstVisit: p.firstVisit,
              lastVisit: mockLastVisit,
              visitCount: p.visitCount,
              summary: p.summary,
              detail: p.detail,
              prescriptionDays: fu.days,
              nextFollowupDate: nextFollowupDate,
              needsFollowUp: needsFollowUp,
              daysOverdue: Math.max(0, daysOverdue)
            };
          });
          
          // 按复诊优先级排序：需要复诊的排前面，超期越久越靠前
          clinicSessions.sort((a, b) => {
            if (a.needsFollowUp && !b.needsFollowUp) return -1;
            if (!a.needsFollowUp && b.needsFollowUp) return 1;
            if (a.needsFollowUp && b.needsFollowUp) {
              return (b.daysOverdue || 0) - (a.daysOverdue || 0);
            }
            return (b.lastVisit || 0) - (a.lastVisit || 0);
          });

          console.log('[演示模式] 注入完成，准备添加复诊提醒');
          
          // 自动选中第一个患者，触发循证内容填充到右侧面板
          if (clinicSessions.length > 0) {
            activeClinicSelection = { type: 'patient', id: clinicSessions[0].id };
            const firstPatient = getPatientById(activeClinicSelection.id);
            if (firstPatient) {
              setTimeout(() => {
                window._showEbmContent(firstPatient.id, firstPatient.name);
              }, 300);
            }
          }
          
          // 延迟执行，等待 DOM 渲染完成
          // 钩子：在 renderClinicPanel 渲染后，为需要复诊的患者添加铃铛图标
          function addFollowUpIndicators() {
            console.log('[复诊提醒] 开始检查...');
            const list = document.getElementById('clinic-list');
            if (!list) { console.log('[复诊提醒] 未找到clinic-list'); return; }
            const rows = list.querySelectorAll('[data-patient-row]');
            console.log('[复诊提醒] 找到患者行:', rows.length);
            rows.forEach(row => {
              const id = row.getAttribute('data-patient-id-value');
              const session = clinicSessions.find(s => s.id === id);
              console.log('[复诊提醒] 患者:', id, session ? session.name : 'not found');
              if (session && session.needsFollowUp) {
                if (row.querySelector('.followup-bell-btn')) return; // 已添加过
                
                // 找到删除按钮或行尾作为插入位置
                let insertPos = row.querySelector('button[onclick*="deleteClinicPatient"]');
                if (!insertPos) {
                  // 如果没有删除按钮，在行末尾添加
                  const actionsDiv = document.createElement('div');
                  actionsDiv.className = 'flex items-center gap-1 flex-shrink-0';
                  row.appendChild(actionsDiv);
                  insertPos = actionsDiv;
                }
                
                const bellBtn = document.createElement('button');
                bellBtn.className = 'followup-bell-btn px-2 py-0.5 flex items-center justify-center text-amber-600 bg-amber-50 border border-amber-200 hover:bg-amber-100 transition-all rounded-lg flex-shrink-0';
                bellBtn.title = session.daysOverdue > 0 ? `已超期${session.daysOverdue}天，点击发送复诊提醒` : '今日应复诊，点击发送提醒';
                bellBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';
                bellBtn.onclick = function(e) {
                  e.stopPropagation();
                  showFollowUpSMS(session);
                };
                
                // 插入铃铛
                if (insertPos.tagName === 'DIV') {
                  insertPos.prepend(bellBtn);
                } else {
                  row.insertBefore(bellBtn, insertPos);
                }
                
                // 添加复诊标记
                if (session.daysOverdue > 0) {
                  const metaDiv = row.querySelector('[data-patient-timeline-meta]');
                  if (metaDiv) {
                    const overdueBadge = document.createElement('span');
                    overdueBadge.className = 'text-[10px] font-medium text-orange-600 bg-orange-50 px-1 rounded whitespace-nowrap';
                    overdueBadge.textContent = `超期${session.daysOverdue}天`;
                    metaDiv.appendChild(overdueBadge);
                  }
                } else {
                  const metaDiv = row.querySelector('[data-patient-timeline-meta]');
                  if (metaDiv) {
                    const todayBadge = document.createElement('span');
                    todayBadge.className = 'text-[10px] font-medium text-amber-600 bg-amber-50 px-1 rounded whitespace-nowrap';
                    todayBadge.textContent = '今日复诊';
                    metaDiv.appendChild(todayBadge);
                  }
                }
              }
            });
          }
          
          // 延迟执行，等待 DOM 渲染完成
          setTimeout(addFollowUpIndicators, 500);
          
          // 额外保险：监听 DOM 变化，确保铃铛始终存在
          let bellCheckCount = 0;
          const bellCheckTimer = setInterval(function() {
            addFollowUpIndicators();
            bellCheckCount++;
            if (bellCheckCount > 20) clearInterval(bellCheckTimer); // 最多检查20次（10秒）
          }, 500);

          // 复诊短信生成与弹窗
          window.showFollowUpSMS = function(session) {
            const patient = allPatients.find(p => p.id === session.id);
            if (!patient) return;
            
            const name = patient.name;
            const summary = patient.summary || '';
            const daysAgo = Math.floor((Date.now() - session.lastVisit) / 86400000);
            const daysText = session.daysOverdue > 0 ? `已超期${session.daysOverdue}天` : '今日应复诊';
            
            // 根据病情生成短信内容
            const symptomShort = summary.slice(0, 60);
            const smsText = `【守一医鉴】尊敬的${name}，您好！您于${daysAgo}天前因"${symptomShort}"来诊，处方${session.prescriptionDays}剂。现已到复诊时间（${daysText}），为保障疗效，建议您尽快安排复诊，以便医生根据您的最新情况调整方案。如有疑问请致电诊所。祝您安康！`;
            
            // 创建弹窗
            const existingOverlay = document.getElementById('followup-sms-overlay');
            if (existingOverlay) existingOverlay.remove();
            
            const overlay = document.createElement('div');
            overlay.id = 'followup-sms-overlay';
            overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.35);z-index:9999;display:flex;align-items:center;justify-content:center;';
            
            const modal = document.createElement('div');
            modal.style.cssText = 'background:white;border-radius:12px;padding:24px;max-width:480px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.15);';
            
            modal.innerHTML = `
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
                <span style="font-size:15px;font-weight:600;color:#1e293b;">复诊提醒 · ${name}</span>
                <span style="margin-left:auto;font-size:11px;color:#d97706;background:#fffbeb;padding:2px 8px;border-radius:4px;">${daysText}</span>
              </div>
              <div style="background:#f8fafc;border-radius:8px;padding:14px;margin-bottom:14px;font-size:13px;line-height:1.7;color:#334155;border:1px solid #e2e8f0;">
                ${smsText}
              </div>
              <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button id="sms-copy-btn" style="padding:7px 16px;border-radius:6px;border:1px solid #e2e8f0;background:white;color:#475569;font-size:12px;cursor:pointer;">📋 复制短信</button>
                <button id="sms-close-btn" style="padding:7px 16px;border-radius:6px;border:none;background:var(--msd-primary);color:white;font-size:12px;cursor:pointer;">关闭</button>
              </div>
            `;
            
            overlay.appendChild(modal);
            document.body.appendChild(overlay);
            
            document.getElementById('sms-close-btn').onclick = () => overlay.remove();
            document.getElementById('sms-copy-btn').onclick = () => {
              navigator.clipboard.writeText(smsText).then(() => {
                const btn = document.getElementById('sms-copy-btn');
                btn.textContent = '✅ 已复制';
                setTimeout(() => { btn.textContent = '📋 复制短信'; }, 2000);
              });
            };
            overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
          };

          // 选中第一个患者
          if (clinicSessions.length > 0) {
            activeClinicSelection = { type: 'patient', id: clinicSessions[0].id };
          }

          // 重新渲染
          renderClinicPanel();
          updateClinicDetailUI();
          
          // 更新患者信息栏
          if (activeClinicSelection && activeClinicSelection.type === 'patient') {
            const patient = getPatientById(activeClinicSelection.id);
            if (patient) {
              updatePatientInfoBar(patient);
            }
          }

          // === 拦截 selectClinicPatient 来注入循证医学内容 ===
          const originalSelectPatient = window._originalSelectClinicPatient || selectClinicPatient;
          window._originalSelectClinicPatient = originalSelectPatient;

          // 重写 selectClinicPatient：调用原函数后再显示循证内容
          selectClinicPatient = async function(patientId) {
            // 调用原函数
            await originalSelectPatient(patientId);
            // 显示循证内容
            const patient = getPatientById(patientId);
            if (patient) {
              setTimeout(() => {
                window._showEbmContent(patientId, patient.name);
              }, 300);
            }
          };

          window._showEbmContent = function(patientId, patientName) {
            const keyword = patientEbmMap[patientId];
            if (!keyword || !ebmLibrary[keyword]) return;
            
            const ebm = ebmLibrary[keyword];
            
            // 不改变聊天消息，只填充右侧循证依据侧边栏
            const guidelineText = ebm.guidelines.map(g => 
              `**${g.source}**\n> ${g.recommendation}`
            ).join('\n\n');
            
            const drugText = ebm.drugReference.map(d => 
              `| ${d.drug} | ${d.dosage} | ${d.evidence} |`
            ).join('\n');
            
            // 构建循证内容 HTML
            const ebmHtml = `
              <div class="mb-3 rounded-md bg-white border border-slate-200 shadow-sm">
                <div class="px-2.5 py-2 border-b border-slate-200 text-base font-semibold flex items-center gap-2 bg-blue-50 text-blue-700 border-blue-100">
                  <span>📋</span><span>概述</span>
                </div>
                <div class="px-2.5 py-2 space-y-2">
                  <div class="text-base text-slate-700 leading-5 break-words">
                    <p class="mb-1"><strong>定义</strong><br>${ebm.definition}</p>
                    <p><strong>流行病学</strong><br>${ebm.epidemiology}</p>
                  </div>
                </div>
              </div>
              <div class="mb-3 rounded-md bg-white border border-slate-200 shadow-sm">
                <div class="px-2.5 py-2 border-b border-slate-200 text-base font-semibold flex items-center gap-2 bg-emerald-50 text-emerald-700 border-emerald-100">
                  <span>📚</span><span>指南推荐</span>
                </div>
                <div class="px-2.5 py-2 space-y-2">
                  ${ebm.guidelines.map((g, i) => `
                    <div class="pb-2 ${i < ebm.guidelines.length - 1 ? 'border-b border-slate-100' : ''}">
                      <div class="text-base text-slate-700 leading-5 break-words">
                        <p class="mb-1"><strong>${g.source}</strong></p>
                        <p>> ${g.recommendation}</p>
                      </div>
                    </div>
                  `).join('')}
                </div>
              </div>
              <div class="mb-3 rounded-md bg-white border border-slate-200 shadow-sm">
                <div class="px-2.5 py-2 border-b border-slate-200 text-base font-semibold flex items-center gap-2 bg-amber-50 text-amber-700 border-amber-100">
                  <span>💊</span><span>药物参考</span>
                </div>
                <div class="px-2.5 py-2 space-y-1">
                  ${ebm.drugReference.map((d, i) => `
                    <div class="pb-1.5 ${i < ebm.drugReference.length - 1 ? 'border-b border-slate-100' : ''}">
                      <div class="text-base text-slate-700 leading-5 break-words">
                        <p><strong>${d.drug}</strong> <span class="text-slate-500">${d.dosage}</span></p>
                        <p class="text-slate-600 text-base">${d.evidence}</p>
                      </div>
                    </div>
                  `).join('')}
                </div>
              </div>
            `;
            
            const container = document.getElementById('sidebar-references-content');
            if (container) {
              container.innerHTML = ebmHtml;
            }
          };

          // 为当前选中的患者显示循证内容
          if (activeClinicSelection && activeClinicSelection.type === 'patient') {
            const patient = getPatientById(activeClinicSelection.id);
            if (patient) {
              setTimeout(() => {
                window._showEbmContent(patient.id, patient.name);
              }, 500);
            }
          }

          // 显示演示模式提示
          const note = document.getElementById('connection-note');
          if (note) {
            note.innerHTML = '<span style="display:flex;align-items:center;gap:6px"><svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span style="color:#065f46">📖 演示模式 — 点击患者可查看循证医学参考</span></span>';
            note.className = 'text-[11px] flex items-center gap-1.5';
          }

          // 更新lucide图标
          if (typeof lucide !== 'undefined' && lucide.createIcons) {
            setTimeout(() => lucide.createIcons(), 50);
          }

          console.log('[演示模式] 已注入5位模拟患者数据，含循证医学内容');
        }
      }, 3000); // 等待3秒，给syncPatients时间尝试连接后端

    });

