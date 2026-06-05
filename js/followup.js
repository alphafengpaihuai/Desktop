          function addFollowUpIndicators() {
            console.log('[复诊提醒] 开始检查...');
            const list = document.getElementById('clinic-list');
            if (!list) { console.log('[复诊提醒] 未找到clinic-list'); return; }
            const rows = list.querySelectorAll('[data-patient-row]');
            console.log('[复诊提醒] 找到患者行:', rows.length);
            rows.forEach(row => {
              const id = row.getAttribute('data-patient-id-value');
              const session = (window.clinicSessions || []).find(s => s.id === id);
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
            window.activeClinicSelection = { type: 'patient', id: clinicSessions[0].id };
          }

          // 重新渲染
          renderClinicPanel();
          updateClinicDetailUI();
          
          // 更新患者信息栏
          if (window.activeClinicSelection && window.activeClinicSelection.type === 'patient') {
            const patient = getPatientById(window.activeClinicSelection.id);
            if (patient) {
              updatePatientInfoBar(patient);
            }
          }

          // === 拦截 selectClinicPatient 来注入循证医学内容 ===
          const originalSelectPatient = window._originalSelectClinicPatient || selectClinicPatient;
          window._originalSelectClinicPatient = originalSelectPatient;

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
          if (window.activeClinicSelection && window.activeClinicSelection.type === 'patient') {
            const patient = getPatientById(window.activeClinicSelection.id);
            if (patient) {
              setTimeout(() => {
                window._showEbmContent(patient.id, patient.name);
              }, 500);
            }
          }

          // 显示演示模式提示
          const note = document.getElementById('connection-note');
          if (note) {
            note.innerHTML = '<span style="display:flex;align-items:center;gap:6px;color:#065f46">🔔 复诊提醒 — 如需复诊的患者会显示铃铛图标</span>';
            note.className = 'text-[11px] flex items-center gap-1.5';
          }

          // 更新lucide图标
          if (typeof lucide !== 'undefined' && lucide.createIcons) {
            setTimeout(() => lucide.createIcons(), 50);
          }

          console.log('[复诊提醒] 已加载复诊提醒功能');

