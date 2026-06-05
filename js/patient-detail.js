    function updatePatientInfoBar(patient) {
      const infoBar = document.getElementById('patient-info-bar');
      if (!infoBar) return;

      // 在医知快答模式下，不显示患者信息栏
      if (activeMode === 'corpus') {
        infoBar.classList.add('hidden');
        return;
      }

      if (!patient || window.activeClinicSelection.type !== 'patient') {
        infoBar.classList.add('hidden');
        // 没有选中患者时调理笺按钮仍可点击（会提示请先选择患者）
        const prescriptionBtn = document.getElementById('btn-show-prescription');
        if (prescriptionBtn) {
          prescriptionBtn.disabled = false;
          prescriptionBtn.classList.remove('disabled');
          prescriptionBtn.removeAttribute('data-disabled-reason');
        }
        return;
      }

      infoBar.classList.remove('hidden');

      // 更新各项信息（仅保留性别/年龄和主诉）
      const ageEl = document.getElementById('patient-info-age');
      const genderEl = document.getElementById('patient-info-gender');
      const symptomEl = document.getElementById('patient-info-symptom');

      if (ageEl) {
        const age = patient.age || '';
        ageEl.textContent = age || '-';
      }
      if (genderEl) genderEl.textContent = patient.gender || '-';

      // 从 treatment_timeline 数组获取最新症状
      let symptom = patient.summary || '';
      const detail = patient.detail || {};
      const timeline = getTreatmentTimeline(detail);
      if (timeline.length > 0) {
        const lastVisit = timeline[timeline.length - 1];
        if (lastVisit.symptom) symptom = lastVisit.symptom;
      }
      if (symptomEl) {
        symptomEl.textContent = symptom || '-';
        symptomEl.setAttribute('title', symptom);
      }

      // 检查是否有开方记录，用于显示"开始复诊"按钮
      const detailInfo = patient.detail || {};
      const timelineInfo = getTreatmentTimeline(detailInfo);
      let hasTreatmentRecord = false;
      let latestTreatmentTime = 0;
      console.log(`[DEBUG] 检查开方记录: timelineInfo长度=${timelineInfo.length}`, timelineInfo);
      for (let i = timelineInfo.length - 1; i >= 0; i--) {
        const visit = timelineInfo[i];
        if (visitHasTreatmentRecord(visit)) {
          hasTreatmentRecord = true;
          console.log(`[DEBUG] 找到开方记录: visit[${i}]`, {
            visit
          });
          // 获取最后一次开方的时间戳（用于判断是否刚完成开方）
          if (visit.timestamp) {
            latestTreatmentTime = visit.timestamp * 1000; // 转成毫秒
          } else if (visit.visit_date) {
            latestTreatmentTime = visit.visit_date;
          }
          console.log(`[DEBUG] 最后一次开方时间戳: ${latestTreatmentTime}, 当前时间: ${Date.now()}, 时间差: ${Date.now() - latestTreatmentTime}ms`);
          break;
        }
      }
      console.log(`[DEBUG] hasTreatmentRecord=${hasTreatmentRecord}, latestTreatmentTime=${latestTreatmentTime}`);

      // 显示/隐藏"开始复诊"按钮
      // 条件：1. 有开方记录 2. 不在复诊流程中
      // 注意：不再使用时间限制，只要有开方记录就显示按钮
      const followupBtn = document.getElementById('btn-start-followup');
      if (followupBtn) {
        console.log(`[DEBUG] 按钮显示判断: hasTreatmentRecord=${hasTreatmentRecord}, isInFollowupFlow=${isInFollowupFlow}`);

        if (hasTreatmentRecord && !isInFollowupFlow) {
          followupBtn.classList.remove('hidden');
          console.log(`[DEBUG] 显示"开始复诊"按钮`);
        } else {
          followupBtn.classList.add('hidden');
          console.log(`[DEBUG] 隐藏"开始复诊"按钮 (hasTreatmentRecord=${hasTreatmentRecord}, isInFollowupFlow=${isInFollowupFlow})`);
        }
      }

      // 更新调理笺按钮状态
      const prescriptionBtn = document.getElementById('btn-show-prescription');
      // 从 treatment_timeline 数组获取最后一次开方信息；如果为空，兼容旧字段（detailInfo已在上面定义）
      let lastTreatment = null;
      let lastVisitWithTreatment = null;

      // 调试日志
      console.log(`[DEBUG] updatePatientInfoBar: timeline长度=${timelineInfo.length}`, timelineInfo);

      // 从 treatment_timeline 数组中找到最后一次有开方的就诊记录
      if (timelineInfo.length > 0) {
        for (let i = timelineInfo.length - 1; i >= 0; i--) {
          const visit = timelineInfo[i];
          console.log(`[DEBUG] 检查visit[${i}]:`, visit);

          if (visitHasTreatmentRecord(visit)) {
            // 新结构：visit 本身就是一次完整的问诊记录
            lastVisitWithTreatment = visit;
            lastTreatment = buildTreatmentFromVisit(visit, detailInfo);

            console.log(`[DEBUG] 找到最后一次开方:`, lastTreatment);
            break;
          }
        }
      } else {
        // 兼容旧版API：使用 detail.last_treatment 作为最近一次开方
        const lt = detailInfo.last_treatment;
        if (lt) {
          console.log('[DEBUG] 使用 detail.last_treatment 作为开方信息:', lt);
          lastTreatment = lt;

          // 如果 diseases 为空，尝试从顶层字段构造
          if (!lastTreatment.diseases || lastTreatment.diseases.length === 0) {
            if (lastTreatment.disease_name || lastTreatment.prescription || lastTreatment.syndrome || lastTreatment.formula_name) {
              const formulaName = lastTreatment.prescription || lastTreatment.formula_name || '';
              lastTreatment.diseases = [{
                病名: lastTreatment.disease_name || '',
                name: lastTreatment.disease_name || '',
                证型: lastTreatment.syndrome || '',
                syndrome: lastTreatment.syndrome || '',
                prescription: formulaName,
                formula_name: formulaName
              }];
              console.log(`[DEBUG] 从last_treatment顶层字段构造diseases数组:`, lastTreatment.diseases);
            }
          }

          lastVisitWithTreatment = {
            treatment: lt,
            treatment_completed: detailInfo.treatment_completed ?? true,
            disease_name: lt.disease_name,
            syndrome: lt.syndrome,
            prescription: lt.prescription,
            visit_date: lt.timestamp ? lt.timestamp * 1000 : undefined
          };
        } else {
          console.log('[DEBUG] timeline 和 last_treatment 都为空，无法获取开方信息');
        }
      }

      // 调理笺按钮始终可点击，无调理记录时打开空页面
      if (prescriptionBtn) {
        // 更新按钮文本（优先使用当前选择的数据库，而不是lastTreatment中的数据库）
        const prescriptionTitle = getPrescriptionTitle(selectedClinicDb);
        const btnTextEl = document.getElementById('prescription-btn-text');
        if (btnTextEl) {
          btnTextEl.textContent = `展示${prescriptionTitle}`;
        }
        prescriptionBtn.disabled = false;
        prescriptionBtn.classList.remove('disabled');
        prescriptionBtn.removeAttribute('data-disabled-reason');
      }

      // 渲染右侧就诊时间线侧边栏
      renderPatientTimelineSidebar(patient);
    }
    function toggleRightSidebar() {
      const sidebar = document.getElementById('patient-timeline-sidebar');
      const toggleBtn = document.getElementById('sidebar-toggle-btn');
      const toggleIcon = document.getElementById('sidebar-toggle-icon');

      if (!sidebar || !toggleBtn || !toggleIcon) return;

      const isCollapsed = sidebar.classList.contains('collapsed');

      if (isCollapsed) {
        sidebar.classList.remove('collapsed');
        toggleIcon.setAttribute('data-lucide', 'chevron-right');
      } else {
        sidebar.classList.add('collapsed');
        toggleIcon.setAttribute('data-lucide', 'chevron-left');
      }

      lucide.createIcons();
    }

    // 渲染右侧就诊时间线侧边栏（基于 detail.treatment_timeline，每次问诊独立一条）
    function renderPatientTimelineSidebar(patient) {
      const sidebar = document.getElementById('patient-timeline-sidebar');
      const content = document.getElementById('patient-timeline-sidebar-content');

      if (!sidebar || !content) return;

      // 只在患者问诊模式下显示
      if (activeMode !== 'clinic' || !patient) {
        sidebar.classList.add('hidden');
        return;
      }

      sidebar.classList.remove('hidden');

      const detail = patient.detail || {};
      const timeline = Array.isArray(detail.treatment_timeline)
        ? detail.treatment_timeline
        : (Array.isArray(detail.timeline) ? detail.timeline : []);

      if (timeline.length === 0) {
        content.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">暂无跟进记录</div>';
        lucide.createIcons();
        return;
      }

      // 生成HTML：使用参考文件的纵向时间轴样式（tl-node, tl-dot, tl-date, tl-content, tl-title, tl-desc）
      // 参考文件：简单的时间线，每个visit一个tl-node节点
      let html = '';
      timeline.forEach((visit, index) => {
        let ts = 0;
        if (visit.timestamp) {
          ts = visit.timestamp * 1000;
        } else if (visit.visit_date) {
          ts = visit.visit_date;
        }
        const date = new Date(ts || Date.now());
        const dateStr = date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });

        // 构建标题和描述
        // 优先级：开方信息 > 症状信息
        let title = '';
        let desc = '';

        // 如果有开方信息，优先显示开方信息
        if (visit.treatment_completed) {
          // 优先从 visit 顶层读取病名、证型和方剂（冗余字段）
          let diseaseName = visit.disease_name || '';
          let syndrome = visit.syndrome || '';
          let prescription = visit.prescription || '';

          const treatment = visit.treatment || null;

          // 如果 visit 顶层字段为空，从 treatment 顶层读取
          if (!diseaseName && treatment) {
            diseaseName = treatment.disease_name || '';
          }
          if (!syndrome && treatment) {
            syndrome = treatment.syndrome || '';
          }
          if (!prescription && treatment) {
            prescription = treatment.prescription || '';
          }

          // 如果仍然为空，从 diseases[0] 中读取
          const diseasesArr = Array.isArray(visit.diseases)
            ? visit.diseases
            : (treatment && Array.isArray(treatment.diseases) ? treatment.diseases : []);
          if ((!diseaseName || !syndrome || !prescription) &&
              diseasesArr && diseasesArr.length > 0) {
            const firstDisease = diseasesArr[0];
            if (firstDisease && typeof firstDisease === 'object') {
              if (!diseaseName) {
                diseaseName = firstDisease.name || firstDisease.病名 || '';
              }
              if (!syndrome) {
                syndrome = firstDisease.syndrome || firstDisease.证型 || '';
              }
              if (!prescription) {
                prescription = firstDisease.prescription || firstDisease.formula_name || '';
                // 如果仍然没有，从治疗方案中读取
                if (!prescription && firstDisease.治疗方案) {
                  const tp = firstDisease.治疗方案;
                  prescription = tp.辨证选方 || tp.方剂 || tp.方剂名称 || '';
                }
              }
            }
          }

          // 构建显示文本
          if (diseaseName) {
            title = diseaseName;
            if (syndrome) {
              desc = syndrome;
              if (prescription) {
                desc += ` - ${prescription}`;
              }
            } else if (prescription) {
              desc = prescription;
            }
          } else if (diseasesArr && diseasesArr.length > 0) {
            const diseasesStr = diseasesArr.map(d => {
              if (!d || typeof d !== 'object') return '';
              let desc = d.病名 || d.name || '';
              let formula = d.prescription || d.formula_name || '';
              if (!formula && d.治疗方案) {
                const tp = d.治疗方案;
                formula = tp.辨证选方 || tp.方剂 || tp.方剂名称 || '';
              }
              if (formula) {
                desc += ` - ${formula}`;
              }
              return desc;
            }).filter(Boolean).join(', ');
            title = diseasesStr || '已生成方案';
          } else {
            title = '已生成方案';
          }
        } else if (visit.symptom) {
          // 如果没有开方信息，显示症状信息
          title = '评估';
          desc = visit.symptom;
        }

        if (!title && !desc) {
          title = '跟进记录';
        }

        // 使用参考文件的HTML结构
        html += `<div class="tl-node">`;
        html += `<div class="tl-dot"></div>`;
        html += `<div class="tl-date">${escapeHtml(dateStr)}</div>`;
        html += `<div class="tl-content">`;
        html += `<div class="tl-title">${escapeHtml(title || '跟进记录')}</div>`;
        if (desc) {
          html += `<div class="tl-desc">${escapeHtml(desc)}</div>`;
        }
        html += `</div>`; // end tl-content
        html += `</div>`; // end tl-node
      });

      content.innerHTML = html;
      lucide.createIcons();
    }

    // 刷新当前患者信息（从服务器获取最新数据）
    async function refreshCurrentPatientInfo() {
      if (activeMode !== 'clinic' || !window.activeClinicSelection || window.activeClinicSelection.type !== 'patient') {
        return; // 不在问诊模式或没有选中患者
      }

      const patientId = window.activeClinicSelection.id;
      if (!patientId || !currentUser.phone) {
        return;
      }

      try {
        console.log(`[DEBUG] 开始刷新患者信息: patientId=${patientId}`);
        const data = await fetchJson(`${API_BASE}/patients/${encodeURIComponent(patientId)}?doctor_id=${encodeURIComponent(currentUser.phone)}`, {
          method: 'GET'
        });

        console.log(`[DEBUG] 获取到的患者数据:`, data);

        if (data && data.ok && data.patient_info) {
          const updatedPatient = mapPatientForUI(data.patient_info);

          // 调试：打印timeline信息
          const detail = updatedPatient.detail || {};
          const timeline = Array.isArray(detail.timeline) ? detail.timeline : [];
          console.log(`[DEBUG] 患者timeline长度: ${timeline.length}`, timeline);

          // 更新clinicSessions中的患者信息
          const index = clinicSessions.findIndex(p => p.id === patientId);
          if (index >= 0) {
            clinicSessions[index] = updatedPatient;
          }

          // 更新患者信息显示栏
          updatePatientInfoBar(updatedPatient);

          // 如果当前选中的就是这个患者，也更新UI
          if (window.activeClinicSelection.id === patientId) {
            renderClinicPanel();
          }
        } else {
          console.warn(`[DEBUG] 获取患者信息失败: data.ok=${data?.ok}, has_patient_info=${!!data?.patient_info}`);
        }
      } catch (e) {
        console.error('刷新患者信息失败:', e);
      }
    }

