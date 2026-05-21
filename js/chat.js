    function appendMessage(role, content, timestamp = null) {
      // 支持两种格式：
      // 1. 字符串：纯文本消息（兼容旧格式）
      // 2. 对象：{ text, imgurl, reference } 格式
      const messageData = normalizeMessageContent(typeof content === 'string'
        ? { text: content }
        : (content || {}), role);

      // 支持新版字段：uuid/type/selection_q（用于去重与展示）
      const msgUuid = messageData.uuid || messageData.chatuuid || messageData.unique_id || null;
      const msgType = messageData.type || null;

      // 如果提供了时间戳，使用提供的时间戳；否则使用当前时间
      const msgTimestamp = timestamp ? (typeof timestamp === 'number' ? new Date(timestamp) : timestamp) : new Date();

      // 检查消息是否已存在（通过时间戳和内容去重，避免重复显示）
      // 对于AI助手频道，如果文本内容完全相同，就认为是重复消息
      const messageText = messageData.text || '';
      const messageImgUrl = messageData.imgurl || '';
      const existingMessage = messages.find(msg => {
        if (msgUuid && msg.uuid && msg.uuid === msgUuid) return true;
        // 检查角色是否相同
        if (msg.role !== role) return false;

        // 检查内容是否相同
        const msgContent = typeof msg.content === 'string' ? { text: msg.content } : (msg.content || {});
        const msgText = msgContent.text || '';
        const msgImgUrl = msgContent.imgurl || '';

        // 对于AI助手频道，如果文本内容完全相同，就认为是重复消息（不管时间戳）
        if (currentPatientId === 'common') {
          if (messageText && msgText && messageText.trim() === msgText.trim()) {
            return true;
          }
          if (messageImgUrl && msgImgUrl && messageImgUrl === msgImgUrl) {
            return true;
          }
        } else {
          // 对于其他频道，使用时间戳和内容进行去重
          if (messageText && msgText && messageText === msgText) {
            // 文本内容相同，检查时间戳（允许5秒内的误差，因为可能来自不同来源）
            if (msg.ts && msgTimestamp) {
              const timeDiff = Math.abs((msg.ts instanceof Date ? msg.ts.getTime() : new Date(msg.ts).getTime()) -
                                       (msgTimestamp instanceof Date ? msgTimestamp.getTime() : new Date(msgTimestamp).getTime()));
              // 如果时间戳相差小于5秒，认为是同一条消息
              if (timeDiff < 5000) {
                return true;
              }
            } else if (!msg.ts && !msgTimestamp) {
              // 都没有时间戳，只比较内容
              return true;
            }
          }

          // 检查图片URL是否相同
          if (messageImgUrl && msgImgUrl && messageImgUrl === msgImgUrl) {
            return true;
          }
        }

        return false;
      });

      if (existingMessage) {
        console.log('[DEBUG] 消息已存在，跳过重复添加:', { role, text: messageText.substring(0, 50) });
        return; // 消息已存在，不重复添加
      }

      messages.push({
        role,
        content: messageData,
        uuid: msgUuid || undefined,
        type: msgType || undefined,
        ts: msgTimestamp
      });

      // 如果有当前病人ID，更新缓存（每次添加消息都保存）
      if (currentPatientId) {
        // 深拷贝消息数组，避免引用问题
        patientMessagesCache[currentPatientId] = messages.map(msg => {
          const msgCopy = { ...msg };
          // 确保content字段格式正确
          if (msgCopy.content && typeof msgCopy.content === 'string') {
            msgCopy.content = { text: msgCopy.content };
          }
          // 确保时间戳是可序列化的
          if (msgCopy.ts instanceof Date) {
            msgCopy.ts = msgCopy.ts.toISOString();
          }
          return msgCopy;
        });
        savePatientMessagesCache();
        console.log(`[DEBUG] 更新患者[${currentPatientId}]的消息缓存，共${messages.length}条`);
      }

      renderMessages();

      // 如果是助手消息，更新右侧参考文献侧边栏
      if (role === 'assistant') {
        updateSidebarReferencesFromMessages();
      }
    }



    // 检测URL并转换为链接（使用拦截机制防止广告跳转）
    function detectAndConvertLinks(text) {
      if (!text) return '';
      const urlRegex = /(https?:\/\/[^\s]+)/g;
      return text.replace(urlRegex, (match) => {
        const refType = detectReferenceType(match);
        const cleanedUrl = cleanReferenceUrl(match, refType);
        const refTypeJson = refType ? JSON.stringify(refType) : '';
        return `<a href="javascript:void(0)"
                   data-original-url="${escapeHtml(match)}"
                   data-ref-type="${escapeHtml(refTypeJson)}"
                   onclick="handleReferenceLinkClick(event, this.getAttribute('data-original-url'), this.getAttribute('data-ref-type')); return false;"
                   target="_blank" rel="noopener noreferrer"
                   class="text-blue-600 hover:text-blue-800 underline cursor-pointer"
                   title="${escapeHtml(cleanedUrl || match)}">${escapeHtml(match)}</a>`;
      });
    }

    // 截断长URL，只显示部分
    function truncateUrl(url, maxLength = 50) {
      if (!url || url.length <= maxLength) return url;
      // 提取域名部分
      try {
        const urlObj = new URL(url);
        const domain = urlObj.hostname;
        const path = urlObj.pathname;
        // 如果域名+路径太长，只显示域名和路径前一部分
        if (url.length > maxLength) {
          const remaining = maxLength - domain.length - 10; // 留一些空间给省略号
          if (remaining > 0 && path.length > remaining) {
            return `${domain}${path.substring(0, remaining)}...`;
          }
          return `${domain}...`;
        }
        return url;
      } catch (e) {
        // 如果不是有效URL，直接截断
        return url.length > maxLength ? url.substring(0, maxLength) + '...' : url;
      }
    }

    // 规范化聊天文本：移除空行与仅空格行
    function normalizeMessageText(rawText) {
      if (!rawText) return '';
      return String(rawText)
        .split(/\r?\n/)
        .filter(line => line.trim() !== '')
        .join('\n');
    }

    function stripLeakedRagPromptText(rawText) {
      let text = String(rawText || '');
      if (!text) return '';

      const hasRagPromptMarker = /【(?:RAG)?参考信息】|【用户问题】|RAG\s*JSON|RAGJSON|===== 参考文献|知识库参考|相关信息：/i.test(text);
      if (!hasRagPromptMarker) return text;

      // 如果模型把“参考信息 + 用户问题”的提示词一起吐回来了，先只剥离参考信息块。
      text = text.replace(/(?:^|\n)【(?:RAG)?参考信息】[\s\S]*?(?=\n\s*【用户问题】|\n\s*用户问题\s*[:：]|$)/gi, '\n');
      text = text.replace(/(?:^|\n)参考信息\s*[:：][\s\S]*?(?=\n\s*用户问题\s*[:：]|$)/gi, '\n');

      // 去掉残留的“用户问题”块，避免把内部拼装格式或用户原问题展示成助手回复。
      text = text.replace(/(?:^|\n)\s*【用户问题】[\s\S]*?(?=\n\s*(?:答复|回答|建议|分析|处理建议|方案)\s*[:：]|$)/g, '\n');
      text = text.replace(/(?:^|\n)\s*用户问题\s*[:：][\s\S]*?(?=\n\s*(?:答复|回答|建议|分析|处理建议|方案)\s*[:：]|$)/g, '\n');
      text = text.replace(/(?:^|\n)\s*【用户问题】\s*/g, '\n');
      text = text.replace(/(?:^|\n)\s*用户问题\s*[:：]\s*/g, '\n');

      // 若仍然出现明显的 RAG JSON 泄漏，从该标记开始截断，保留前面的真实回复。
      text = text.replace(/\n?[^。\n]*RAG\s*JSON[^。\n]*[:：][\s\S]*$/i, '');
      text = text.replace(/\n?[^。\n]*RAGJSON[^。\n]*[:：][\s\S]*$/i, '');

      return text.trim();
    }

    function extractReferencesFromText(rawText) {
      const text = stripLeakedRagPromptText(typeof rawText === 'string' ? rawText : '');
      if (!text) return { text: '', references: [] };

      const markerPatterns = [
        /【参考文献】/i,
        /\[参考文献\]/i,
        /(?:参考来源|参考资料|参考链接|参考文献)\s*[:：]?\s*\n?/i,
        /References?\s*[:：]?\s*\n?/i
      ];

      let markerMatch = null;
      for (const pattern of markerPatterns) {
        const matched = pattern.exec(text);
        if (matched && (!markerMatch || matched.index > markerMatch.index)) {
          markerMatch = matched;
        }
      }

      if (!markerMatch) {
        return { text, references: [] };
      }

      const refStart = markerMatch.index;
      const refBodyStart = markerMatch.index + markerMatch[0].length;
      const cleanText = text.slice(0, refStart).trim();
      const refSection = text.slice(refBodyStart).trim();
      const references = [];

      const pushRef = (value) => {
        const normalizedValue = String(value || '').replace(/\s+/g, ' ').trim();
        if (!normalizedValue) return;
        if (!references.includes(normalizedValue)) references.push(normalizedValue);
      };

      const urlMatches = refSection.match(/https?:\/\/[^\s)]+/gi) || [];
      urlMatches.forEach(pushRef);

      refSection.split(/\r?\n/).forEach(line => {
        const cleanedLine = line
          .replace(/^[\s>*\-•]+/, '')
          .replace(/^\d+[.)、]\s*/, '')
          .replace(/^URL\s*[:：]\s*/i, '')
          .trim();
        if (!cleanedLine) return;
        if (/^(参考文献|参考来源|参考资料|参考链接)$/i.test(cleanedLine)) return;
        pushRef(cleanedLine);
      });

      return { text: cleanText, references };
    }

    function normalizeReferenceList(rawReferences, rawText = '', ragContents = []) {
      const references = [];

      const pushRef = (value) => {
        const normalizedValue = String(value || '').replace(/\s+/g, ' ').trim();
        if (!normalizedValue) return;
        if (!references.includes(normalizedValue)) references.push(normalizedValue);
      };

      const visitReference = (value) => {
        if (!value) return;
        if (Array.isArray(value)) {
          value.forEach(visitReference);
          return;
        }
        if (typeof value === 'string') {
          const extracted = extractReferencesFromText(value);
          if (extracted.references.length > 0) {
            extracted.references.forEach(pushRef);
          } else {
            value
              .split(/\r?\n/)
              .map(line => line.trim())
              .filter(Boolean)
              .forEach(pushRef);
          }
          return;
        }
        if (typeof value === 'object') {
          const refUrl = value.url || value.link || value.href || value.source || '';
          const refTitle = value.title || value.name || value.label || value.db_name || value.source_name || '';
          const refText = value.text || value.content || value.snippet || value.summary || value.desc || '';
          if (refUrl && refTitle) {
            pushRef(`${refTitle} ${refUrl}`);
            return;
          }
          if (refUrl) {
            pushRef(refUrl);
            return;
          }
          if (refTitle && refText) {
            pushRef(`来自[${refTitle}]：${String(refText).slice(0, 120)}`);
            return;
          }
          if (refTitle) {
            pushRef(refTitle);
            return;
          }
          if (refText) {
            pushRef(String(refText).slice(0, 120));
          }
        }
      };

      visitReference(rawReferences);

      const extractedFromText = extractReferencesFromText(rawText);
      extractedFromText.references.forEach(pushRef);

      if (references.length === 0 && Array.isArray(ragContents)) {
        ragContents.slice(0, 5).forEach(item => visitReference(item));
      }

      return references.slice(0, 10);
    }

    function shouldApplyQingdaReplyTextRules(role) {
      return role === 'assistant' && activeMode === 'clinic' && selectedClinicDb === '清大方案';
    }

    function normalizeQingdaReplyText(value, role) {
      if (!shouldApplyQingdaReplyTextRules(role) || value == null) return value;
      return String(value).replace(/(^|[^\d])10g\b/g, function(_, prefix) {
        return prefix + '15g';
      });
    }

    function normalizeMessageContent(rawContent, role = null) {
      const content = (rawContent && typeof rawContent === 'object')
        ? { ...rawContent }
        : { text: typeof rawContent === 'string' ? rawContent : '' };

      const originalText = normalizeQingdaReplyText(stripLeakedRagPromptText(content.text || ''), role);
      const extracted = extractReferencesFromText(originalText);
      content.text = extracted.text;

      const normalizedReferences = normalizeReferenceList(
        [content.reference || [], extracted.references],
        originalText,
        content.rag_contents || []
      );
      content.reference = normalizedReferences.length > 0 ? normalizedReferences : null;

      return content;
    }

    // 检测参考文献来源类型
    function detectReferenceType(ref) {
      if (!ref) return null;

      const lowerRef = ref.toLowerCase();
      const urlMatch = ref.match(/(https?:\/\/[^\s]+)/i);
      const url = urlMatch ? urlMatch[1].toLowerCase() : ref.toLowerCase();

      // 解析域名，方便精确识别来源站点
      let hostname = '';
      try {
        const urlObj = new URL(urlMatch ? urlMatch[1] : ref);
        hostname = urlObj.hostname.toLowerCase();
      } catch (e) {
        // 不是标准URL时忽略，后续用字符串包含判断
        hostname = '';
      }

      // 检测10万中医病案（优先检测，避免被识别为守一RAG）
      // 检查多种可能的格式：包含"10万"、"病案"、"中医病案"等关键词
      // 或者包含bingan10w相关的URL（包括bingan10w.local）
      if (lowerRef.includes('10万') || lowerRef.includes('病案') || lowerRef.includes('中医病案') ||
          lowerRef.includes('10万中医') || url.includes('bingan10w') || hostname.includes('bingan10w') ||
          url.includes('bingan10w.local')) {
        return {
          type: 'casebase',
          name: '10万中医病案',
          color: 'bg-amber-100 text-amber-800 border-amber-300',
          icon: '📋'
        };
      }

      // 检测默沙东
      // 主要来源：msdmanuals.cn
      if (
        hostname.includes('msdmanuals.cn') ||
        url.includes('msdmanuals.cn') ||
        url.includes('msd') ||
        lowerRef.includes('msd') ||
        lowerRef.includes('默沙东')
      ) {
        return {
          type: 'msd',
          name: '默沙东',
          color: 'bg-blue-100 text-blue-800 border-blue-300',
          icon: '🌐'
        };
      }

      // 检测中医世家（zysj.com.cn）
      if (
        hostname.includes('zysj.com.cn') ||
        url.includes('zysj.com.cn') ||
        lowerRef.includes('中医世家') ||
        lowerRef.includes('zysj')
      ) {
        return {
          type: 'zysj',
          name: '中医世家',
          color: 'bg-rose-100 text-rose-800 border-rose-300',
          icon: '🌿'
        };
      }

      // 检测 Uptodate
      if (
        hostname.includes('uptodate.cn') ||
        url.includes('uptodate.cn') ||
        url.includes('uptodate') ||
        lowerRef.includes('uptodate')
      ) {
        return {
          type: 'uptodate',
          name: 'UpToDate',
          color: 'bg-medical-green-light text-medical-green-dark border-medical-green-light',
          icon: '📚'
        };
      }

      // 检测中医药数据库（tcmbank.cn）
      if (
        hostname.includes('tcmbank.cn') ||
        url.includes('tcmbank.cn') ||
        lowerRef.includes('中医药数据库') ||
        lowerRef.includes('tcmbank')
      ) {
        return {
          type: 'tcmbank',
          name: '中医药数据库',
          color: 'bg-medical-green-light text-medical-green-dark border-medical-green-light',
          icon: '🧪'
        };
      }

      // 检测守一RAG（本地文件，通常不是http链接，或者是特定标识）
      // 注意：排除10万中医病案，避免误识别
      const isCasebaseRef = lowerRef.includes('10万') || lowerRef.includes('病案') || lowerRef.includes('中医病案') ||
                           url.includes('bingan10w') || hostname.includes('bingan10w') || url.includes('bingan10w.local');
      const isRagLikeRef = lowerRef.includes('rag') ||
        lowerRef.includes('守一') ||
        lowerRef.includes('本地') ||
        lowerRef.includes('知识库') ||
        lowerRef.includes('自有库') ||
        lowerRef.includes('用户上传文件') ||
        /来自\[[^\]]+\]/.test(ref);
      if (!isCasebaseRef && (!/^https?:\/\//i.test(ref) || isRagLikeRef)) {
        return {
          type: 'rag',
          name: '守一RAG',
          color: 'bg-purple-100 text-purple-800 border-purple-300',
          icon: '💡'
        };
      }

      return null;
    }

    // 清理和修复参考链接URL，拦截广告跳转
    function cleanReferenceUrl(originalUrl, refType) {
      if (!originalUrl) return originalUrl;

      try {
        const urlObj = new URL(originalUrl);
        const hostname = urlObj.hostname.toLowerCase();

        // 处理中医世家（zysj.com.cn）- 移除广告参数
        if (refType?.type === 'zysj' || hostname.includes('zysj.com.cn')) {
          // 移除常见的广告和追踪参数
          const adParams = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                           'ref', 'from', 'source', 'ad', 'adid', 'clickid', 'gclid', 'fbclid',
                           'affiliate', 'partner', 'campaign', 'tracking', 'track'];

          adParams.forEach(param => {
            urlObj.searchParams.delete(param);
          });

          // 确保直接指向内容页，移除可能的跳转参数
          const pathname = urlObj.pathname;
          // 如果路径包含跳转标识，尝试提取真实URL
          if (pathname.includes('/redirect') || pathname.includes('/jump') || pathname.includes('/go')) {
            const realUrl = urlObj.searchParams.get('url') || urlObj.searchParams.get('target') || urlObj.searchParams.get('link');
            if (realUrl) {
              try {
                const realUrlObj = new URL(realUrl);
                if (realUrlObj.hostname.includes('zysj.com.cn')) {
                  // 使用真实URL，但继续清理参数
                  adParams.forEach(param => {
                    realUrlObj.searchParams.delete(param);
                  });
                  return realUrlObj.toString();
                }
              } catch (e) {
                // 如果解析失败，继续使用原URL
              }
            }
          }

          return urlObj.toString();
        }

        // 处理默沙东（msdmanuals.cn）- 修复404问题
        if (refType?.type === 'msd' || hostname.includes('msdmanuals.cn')) {
          // 如果URL是搜索页面，尝试转换为内容页
          if (urlObj.pathname.includes('/searchresults')) {
            const query = urlObj.searchParams.get('query');
            if (query) {
              // 尝试构建专业版搜索URL
              return `https://www.msdmanuals.cn/professional/searchresults?query=${encodeURIComponent(query)}`;
            }
          }

          // 如果URL路径不完整或可能导致404，尝试修复
          if (urlObj.pathname === '/' || !urlObj.pathname || urlObj.pathname.length < 2) {
            // 如果有查询参数，构建搜索URL
            const query = urlObj.searchParams.get('query') || urlObj.searchParams.get('q');
            if (query) {
              return `https://www.msdmanuals.cn/professional/searchresults?query=${encodeURIComponent(query)}`;
            }
            // 否则返回专业版首页
            return 'https://www.msdmanuals.cn/professional';
          }

          // 移除可能的广告参数
          const adParams = ['utm_source', 'utm_medium', 'utm_campaign', 'ref', 'from'];
          adParams.forEach(param => {
            urlObj.searchParams.delete(param);
          });

          return urlObj.toString();
        }

        // 其他URL也移除常见广告参数
        const adParams = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                         'ref', 'from', 'source', 'gclid', 'fbclid'];
        adParams.forEach(param => {
          urlObj.searchParams.delete(param);
        });

        return urlObj.toString();
      } catch (e) {
        // URL解析失败，返回原URL
        console.warn('URL清理失败:', e, originalUrl);
        return originalUrl;
      }
    }

    // 处理参考链接点击，拦截广告跳转
    function handleReferenceLinkClick(event, originalUrl, refTypeStr) {
      event.preventDefault();
      event.stopPropagation();

      // 如果refTypeStr是字符串，尝试解析；否则从URL自动检测
      let refType = null;
      if (refTypeStr && refTypeStr !== 'null') {
        try {
          // 尝试解析JSON字符串
          refType = JSON.parse(refTypeStr.replace(/&quot;/g, '"'));
        } catch (e) {
          // 解析失败，从URL自动检测
          refType = detectReferenceType(originalUrl);
        }
      } else {
        // 从URL自动检测类型
        refType = detectReferenceType(originalUrl);
      }

      const cleanedUrl = cleanReferenceUrl(originalUrl, refType);

      // 在新窗口打开清理后的URL
      if (cleanedUrl) {
        window.open(cleanedUrl, '_blank', 'noopener,noreferrer');
      } else {
        console.warn('清理后的URL为空，使用原URL:', originalUrl);
        window.open(originalUrl, '_blank', 'noopener,noreferrer');
      }
    }

    // 渲染单个参考文献项
    function renderReference(ref, refIndex = 0) {
      if (!ref) return '';

      const refType = detectReferenceType(ref);

      // 构建标签HTML
      let badgeHtml = '';
      if (refType) {
        badgeHtml = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border ${refType.color}">
          ${refType.icon} ${refType.name}
        </span>`;
      }

      // 检测是否是10万中医病案，需要折叠功能
      // 检查方式：1. 通过refType检测 2. 通过文本内容检测（包含"10万"、"病案"等关键词）3. 通过URL检测（bingan10w.local）
      const hasCasebaseUrl = ref && /bingan10w\.local/i.test(ref);
      const hasCasebaseKeywords = ref && (ref.includes('10万') || ref.includes('病案') || ref.includes('中医病案'));
      const isCasebase = (refType && refType.type === 'casebase') || hasCasebaseUrl || hasCasebaseKeywords;
      const maxPreviewLength = 30; // 默认显示前30个字

      // 如果是10万中医病案，无论长度如何都应用折叠功能（如果超过30字）
      if (isCasebase) {
        // 10万中医病案：添加折叠功能
        const refId = `ref-casebase-${Date.now()}-${refIndex}`;

        // 移除URL（如果有），只保留文本内容用于显示
        let displayText = ref;
        if (hasCasebaseUrl) {
          // 移除URL部分
          displayText = ref.replace(/\s+https?:\/\/[^\s]+/gi, '').trim();
        }

        const shouldCollapse = displayText.length > maxPreviewLength;
        const previewText = shouldCollapse ? displayText.substring(0, maxPreviewLength) : displayText;
        const fullText = displayText;

        // 强制设置为casebase类型（确保显示正确的标签）
        const finalBadgeHtml = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border bg-amber-100 text-amber-800 border-amber-300">
          📋 10万中医病案
        </span>`;

        if (shouldCollapse) {
          // 需要折叠：显示预览和展开按钮
          return `<div class="flex items-start gap-2">
            <div class="flex-shrink-0">${finalBadgeHtml}</div>
            <div class="flex-1 min-w-0">
              <div class="cursor-pointer select-none hover:bg-slate-50 rounded px-1 py-0.5 -mx-1 -my-0.5 transition-colors" onclick="toggleReferenceExpand('${refId}')">
                <span id="${refId}-preview" class="break-words">${escapeHtml(previewText)}<span class="text-blue-600 hover:text-blue-800">...</span></span>
                <span id="${refId}-full" class="hidden break-words">${escapeHtml(fullText)}</span>
                <span id="${refId}-toggle" class="text-sm text-blue-600 hover:text-blue-800 ml-1 font-medium">[展开]</span>
              </div>
            </div>
          </div>`;
        } else {
          // 不需要折叠：直接显示全文
          return `<div class="flex items-start gap-2">
            <div class="flex-shrink-0">${finalBadgeHtml}</div>
            <div class="flex-1 min-w-0 break-words">${escapeHtml(fullText)}</div>
          </div>`;
        }
      }

      // 检测文本中是否包含URL（格式："内容来自 https://..."）
      const urlMatch = ref.match(/(https?:\/\/[^\s]+)/i);

      if (urlMatch) {
        const fullUrl = urlMatch[1];
        const cleanedUrl = cleanReferenceUrl(fullUrl, refType);
        const displayUrl = truncateUrl(fullUrl, 50); // 减少显示长度，为标签留空间
        const beforeUrl = ref.substring(0, urlMatch.index);
        const afterUrl = ref.substring(urlMatch.index + fullUrl.length);

        // 使用onclick拦截点击，防止广告跳转
        const linkId = `ref-link-${Date.now()}-${refIndex}`;
        const refTypeJson = refType ? JSON.stringify(refType) : '';
        return `<div class="flex items-start gap-2">
          ${badgeHtml ? `<div class="flex-shrink-0">${badgeHtml}</div>` : ''}
          <div class="flex-1 min-w-0">
            ${escapeHtml(beforeUrl)}
            <a href="javascript:void(0)"
               data-original-url="${escapeHtml(fullUrl)}"
               data-ref-type="${escapeHtml(refTypeJson)}"
               onclick="handleReferenceLinkClick(event, this.getAttribute('data-original-url'), this.getAttribute('data-ref-type')); return false;"
               class="text-blue-600 hover:text-blue-800 underline break-all cursor-pointer"
               title="${escapeHtml(cleanedUrl || fullUrl)}">${escapeHtml(displayUrl)}</a>
            ${escapeHtml(afterUrl)}
          </div>
        </div>`;
      }

      // 如果是纯URL
      const isUrl = /^https?:\/\//i.test(ref);
      if (isUrl) {
        const cleanedUrl = cleanReferenceUrl(ref, refType);
        const displayUrl = truncateUrl(ref, 50);
        // 使用onclick拦截点击，防止广告跳转
        const refTypeJson = refType ? JSON.stringify(refType) : '';
        return `<div class="flex items-start gap-2">
          ${badgeHtml ? `<div class="flex-shrink-0">${badgeHtml}</div>` : ''}
          <div class="flex-1 min-w-0">
            <a href="javascript:void(0)"
               data-original-url="${escapeHtml(ref)}"
               data-ref-type="${escapeHtml(refTypeJson)}"
               onclick="handleReferenceLinkClick(event, this.getAttribute('data-original-url'), this.getAttribute('data-ref-type')); return false;"
               target="_blank" rel="noopener noreferrer"
               class="text-blue-600 hover:text-blue-800 underline break-all cursor-pointer"
               title="${escapeHtml(cleanedUrl || ref)}">${escapeHtml(displayUrl)}</a>
          </div>
        </div>`;
      }

      // 普通文本，检测是否有链接
      const textWithLinks = detectAndConvertLinks(escapeHtml(ref));
      return `<div class="flex items-start gap-2">
        ${badgeHtml ? `<div class="flex-shrink-0">${badgeHtml}</div>` : ''}
        <div class="flex-1 min-w-0 break-words">${textWithLinks}</div>
      </div>`;
    }

    // 切换参考文献展开/折叠状态
    function toggleReferenceExpand(refId) {
      const previewEl = document.getElementById(`${refId}-preview`);
      const fullEl = document.getElementById(`${refId}-full`);
      const toggleEl = document.getElementById(`${refId}-toggle`);

      if (!previewEl || !fullEl || !toggleEl) return;

      // 检查当前状态（通过检查fullEl是否隐藏）
      const isExpanded = !fullEl.classList.contains('hidden');

      if (isExpanded) {
        // 折叠：显示预览，隐藏全文
        previewEl.classList.remove('hidden');
        fullEl.classList.add('hidden');
        toggleEl.textContent = '[展开]';
      } else {
        // 展开：隐藏预览，显示全文
        previewEl.classList.add('hidden');
        fullEl.classList.remove('hidden');
        toggleEl.textContent = '[折叠]';
      }
    }

    // 渲染RAG内容项（缩略形式）
    function renderRagContent(ragItem) {
      if (!ragItem) return '';

      const dbName = ragItem.db_name || '未知数据库';
      const content = ragItem.content || '';
      const url = ragItem.url || '';

      // 截断内容，只显示前150字符
      const maxLength = 150;
      const truncatedContent = content.length > maxLength
        ? content.substring(0, maxLength) + '...'
        : content;

      let html = `<div class="flex items-start gap-2">
        <div class="flex-shrink-0">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-medical-green-light text-medical-green-dark border border-medical-green-light">
            📚 ${escapeHtml(dbName)}
          </span>
        </div>
        <div class="flex-1 min-w-0">
          <div class="text-sm text-slate-600 line-clamp-2 break-words">${escapeHtml(truncatedContent)}</div>`;

      // 如果有URL，添加链接（使用拦截机制防止广告跳转）
      if (url) {
        const displayUrl = truncateUrl(url, 40);
        const refType = detectReferenceType(url);
        const cleanedUrl = cleanReferenceUrl(url, refType);
        const refTypeJson = refType ? JSON.stringify(refType) : '';
        html += `<a href="javascript:void(0)"
                   data-original-url="${escapeHtml(url)}"
                   data-ref-type="${escapeHtml(refTypeJson)}"
                   onclick="handleReferenceLinkClick(event, this.getAttribute('data-original-url'), this.getAttribute('data-ref-type')); return false;"
                   class="text-xs text-blue-600 hover:text-blue-800 underline mt-1 block cursor-pointer"
                   title="${escapeHtml(cleanedUrl || url)}">${escapeHtml(displayUrl)}</a>`;
      }

      html += `</div></div>`;
      return html;
    }

    // 渲染交互式问题（选择题和问答题）
    // 渲染交互式问题（选择题和问答题）
    function renderInteractiveQuestions(questionData) {
      if (!questionData || typeof questionData !== 'object') {
        return '';
      }

      const questionEntries = Object.entries(questionData);
      const questionCount = questionEntries.length;

      // 整体区域更紧凑：缩小行间距和外边距
      let html = `<div class="interactive-questions space-y-2 mt-2" data-question-count="${questionCount}">`;
      if (questionCount > 1) {
        html += `<div class="interactive-question-summary">共 ${questionCount} 个问题，可上下滑动查看</div>`;
      }

      // 遍历所有问题
      questionEntries.forEach(([question, options], index) => {
        question = normalizeComplianceText(question);
        if (Array.isArray(options)) {
          options = options.map(option => normalizeComplianceText(option));
        }
        const hasOptions = Array.isArray(options) && options.length > 0;
        const questionId = `question-${Date.now()}-${index}`;

        // 缩小卡片和标题字号
        html += `<div class="interactive-question-card border border-slate-200 rounded-md px-2 py-1.5 bg-slate-50">`;

        // 问题与选项分成两行：第一行问题，第二行选项按钮
        html += `<div class="flex flex-col gap-2">`;

        // 第一行：问题
        html += `<div class="interactive-question-title text-[11px] font-medium text-slate-900">${escapeHtml(question)}</div>`;

        // 第二行：选项按钮
        if (hasOptions) {
          // 选择题：第二行展示选项按钮（可换行）
          html += `<div class="interactive-option-list flex flex-wrap gap-1.5">`;
          options.forEach((option, optIndex) => {
            const optionId = `${questionId}-option-${optIndex}`;
            // 使用data属性存储选项文本，避免转义问题
            const optionTextSafe = escapeHtml(option);
            const questionTextSafe = escapeHtml(question);
            html += `<button
              onclick="selectQuestionOption(this)"
              data-option="${optionTextSafe}"
              data-question="${questionTextSafe}"
              class="interactive-option-btn px-2 py-0.5 text-[10px] font-medium rounded border border-medical-green-light bg-white text-medical-green-dark hover:bg-medical-green-pale hover:border-green-500 transition-colors"
              id="${optionId}">
              ${escapeHtml(option)}
            </button>`;
          });
          html += `</div>`; // 结束选项按钮容器
        } else {
          // 问答题：显示提示文本（用户可以手动输入，字号略小）
          html += `<div class="text-[11px] text-slate-500">请在下方输入框中回答此问题</div>`;
        }

        html += `</div>`; // 结束"问题+选项"容器
        html += `</div>`; // 结束卡片
      });

      html += '</div>';
      return html;
    }

    // 处理选择题选项点击
    function buildQuestionAnswer(question, optionText) {
      if (!question) return optionText;
      const q = String(question).trim();
      // 确保问题以问号结尾，如果没有则添加问号
      const questionWithQ = q.endsWith('?') || q.endsWith('？') ? q : q + '?';
      return `[${questionWithQ} 答复:${optionText}]`;
    }

    function selectQuestionOption(buttonElement) {
      const input = document.getElementById('chat-input');
      if (!input || !buttonElement) return;

      // 从按钮的data属性或文本内容获取选项
      const optionText = (buttonElement.getAttribute('data-option') || buttonElement.textContent.trim()).replace(/&quot;/g, '"');
      const questionText = (buttonElement.getAttribute('data-question') || '').replace(/&quot;/g, '"');

      // 特殊处理：点击"展示调理笺"时，直接打开调理笺页（不填入输入框、不发送消息）
      if (/^展示/.test(optionText) && (optionText.includes('笺') || optionText.includes('方案'))) {
        showPrescriptionPage();
        // 高亮按钮
        buttonElement.classList.remove('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
        buttonElement.classList.add('bg-medical-green', 'text-white', 'border-medical-green');
        setTimeout(() => {
          buttonElement.classList.remove('bg-medical-green', 'text-white', 'border-medical-green');
          buttonElement.classList.add('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
        }, 500);
        return;
      }

      const combinedText = buildQuestionAnswer(questionText, optionText);
      const isMobileMode = document.body && document.body.classList.contains('mobile-mode');

      if (isMobileMode) {
        if (input.value.trim() === '') {
          input.value = combinedText;
        } else {
          input.value += '，' + combinedText;
        }
        autoResizeChatInput(input);
        input.blur();
        if (document.activeElement && typeof document.activeElement.blur === 'function') {
          document.activeElement.blur();
        }

        buttonElement.classList.remove('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
        buttonElement.classList.add('bg-medical-green', 'text-white', 'border-medical-green');
        setTimeout(() => {
          buttonElement.classList.remove('bg-medical-green', 'text-white', 'border-medical-green');
          buttonElement.classList.add('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
        }, 500);
        return;
      }

      // 如果输入框为空，直接填入选项；如果有内容，追加
      if (input.value.trim() === '') {
        input.value = combinedText;
      } else {
        input.value += '，' + combinedText;
      }

      // 聚焦到输入框
      input.focus();

      // 高亮显示已选中的按钮（增强用户体验）
      buttonElement.classList.remove('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
      buttonElement.classList.add('bg-medical-green', 'text-white', 'border-medical-green');
      setTimeout(() => {
        buttonElement.classList.remove('bg-medical-green', 'text-white', 'border-medical-green');
        buttonElement.classList.add('bg-white', 'text-medical-green-dark', 'border-medical-green-light');
      }, 500);
    }

    // 将参考文献按品类分组展示（带配色/图标与展开折叠）
    function renderSidebarReferences() {
      const container = document.getElementById('sidebar-references-content');
      if (!container) return;

      // 仅在问诊模式且选中患者时显示内容；否则显示占位
      if (activeMode !== 'clinic' || !currentPatientId) {
        container.innerHTML = '<div class="text-lg text-slate-400 text-center py-4">暂无参考文献</div>';
        return;
      }

      const refs = sidebarReferences || [];
      if (!refs.length) {
        container.innerHTML = '<div class="text-sm text-slate-400 text-center py-4">暂无参考文献</div>';
        return;
      }

      // 只展示最近10条
      const latest = refs.slice(-10);

      const buckets = {
        msd: [],          // 默沙东
        zysj: [],         // 中医世家
        casebase: [],     // 10万中医病案
        rag: [],          // 守一RAG
        other: []         // 其他
      };

      latest.forEach((item, idx) => {
        const ref = item.text;
        const refType = detectReferenceType(ref);
        const key = refType?.type === 'msd' ? 'msd'
                  : refType?.type === 'zysj' ? 'zysj'
                  : refType?.type === 'casebase' ? 'casebase'
                  : refType?.type === 'rag' ? 'rag'
                  : 'other';
        buckets[key].push({ text: ref, idx });
      });

      const titleMap = {
        msd: '默沙东',
        zysj: '中医世家',
        casebase: '10万中医病案',
        rag: '守一RAG',
        other: '其他'
      };

      const headerStyle = {
        msd: 'bg-blue-50 text-blue-700 border-blue-100',
        zysj: 'bg-emerald-50 text-emerald-700 border-emerald-100',
        casebase: 'bg-amber-50 text-amber-700 border-amber-100',
        rag: 'bg-purple-50 text-purple-700 border-purple-100',
        other: 'bg-slate-50 text-slate-700 border-slate-200'
      };

      const iconMap = {
        msd: '🌐',
        zysj: '🌿',
        casebase: '📋',
        rag: '💡',
        other: '📚'
      };

      // 单条渲染，含展开/折叠（使用已有 toggleReferenceExpand）
      const renderRefItem = (text, globalIdx) => {
        const maxPreview = 60;
        const preview = text.slice(0, maxPreview);
        const needsFold = text.length > maxPreview;
        const id = `ref-${Date.now()}-${globalIdx}`;
        if (!needsFold) {
          return `<div class="text-base text-slate-700 leading-5 break-words">${detectAndConvertLinks(escapeHtml(text))}</div>`;
        }
        return `<div class="text-base text-slate-700 leading-5 break-words">
          <div class="cursor-pointer select-none" onclick="toggleReferenceExpand('${id}')">
            <span id="${id}-preview">${detectAndConvertLinks(escapeHtml(preview))}<span class="text-blue-600">...</span></span>
            <span id="${id}-full" class="hidden">${detectAndConvertLinks(escapeHtml(text))}</span>
            <span id="${id}-toggle" class="text-sm text-blue-600 ml-1 font-medium">[展开]</span>
          </div>
        </div>`;
      };

      const order = ['msd', 'zysj', 'casebase', 'rag', 'other'];
      let html = '';

      order.forEach(key => {
        const list = buckets[key];
        if (!list.length) return;
        html += `<div class="mb-3 rounded-md bg-white border border-slate-200 shadow-sm">`;
        html += `<div class="px-2.5 py-2 border-b border-slate-200 text-base font-semibold flex items-center gap-2 ${headerStyle[key]}">`;
        html += `<span>${iconMap[key]}</span><span>${titleMap[key]}</span>`;
        html += `</div>`;
        html += `<div class="px-2.5 py-2 space-y-2">`;
        list.forEach((item, idx) => {
          const isLast = idx === list.length - 1;
          html += `<div class="pb-2 ${isLast ? '' : 'border-b border-slate-100'}">`;
          html += renderRefItem(item.text, item.idx);
          html += `</div>`;
        });
        html += `</div></div>`;
      });

      container.innerHTML = html || '<div class="text-lg text-slate-400 text-center py-4">暂无参考文献</div>';

      if (window.lucide) {
        window.lucide.createIcons();
      }
    }

    // 从当前患者的消息中抽取参考文献，更新侧边栏（只保留最近10条）
    function updateSidebarReferencesFromMessages() {
      // 仅在问诊模式且有当前患者时处理
      if (activeMode !== 'clinic' || !currentPatientId) {
        sidebarReferences = [];
        renderSidebarReferences();
        return;
      }

      const refs = [];
      messages.forEach(msg => {
        if (!msg || msg.role !== 'assistant') return;
        const content = normalizeMessageContent(typeof msg.content === 'string'
          ? { text: msg.content }
          : (msg.content || {}), msg.role);
        const list = content.reference || [];
        if (Array.isArray(list)) {
          list.forEach(ref => {
            if (ref) {
              refs.push({
                text: ref,
                ts: msg.ts || null
              });
            }
          });
        }
      });

      // 只保留最近10条
      sidebarReferences = refs.slice(-10);
      renderSidebarReferences();
    }

    // 自动调整聊天输入框高度（多行时自动换行并增高）- 固定最大高度避免跳动
    function autoResizeChatInput(el) {
      if (!el) return;

      // 先重置为最小高度，再按内容自适应
      el.style.height = 'auto';
      el.style.height = '44px'; // 固定最小高度

      const maxHeight = 120; // 最多增高到约 4~5 行（减少高度避免过大）
      const scrollHeight = el.scrollHeight;
      const newHeight = Math.min(Math.max(scrollHeight, 44), maxHeight);

      // 使用平滑过渡避免突然跳动
      el.style.transition = 'height 0.15s ease-out';
      el.style.height = newHeight + 'px';

      // 不动态调整父容器高度，保持固定高度避免跳动
      // 父容器已经有固定最小高度，不需要动态调整
    }

    function renderMessages() {

      const container = document.getElementById('chat-container');

      if (!container) return;

      // 预处理：将连续的助手 selection_q 消息合并为一条「问题+选择答案」格式（与实时问诊的 question_data 一致）
      const displayMessages = [];
      for (let i = 0; i < messages.length; i++) {
        const msg = messages[i];
        const msgType = msg.type || (msg.content && typeof msg.content === 'object' ? msg.content.type : null) || 'text';
        if (msg.role === 'assistant' && msgType === 'selection_q') {
          const run = [msg];
          while (i + 1 < messages.length) {
            const next = messages[i + 1];
            const nextType = next.type || (next.content && typeof next.content === 'object' ? next.content.type : null) || 'text';
            if (next.role === 'assistant' && nextType === 'selection_q') {
              run.push(next);
              i++;
            } else {
              break;
            }
          }
          const questionData = {};
          run.forEach(m => {
            const c = m.content && typeof m.content === 'object' ? m.content : {};
            const sq = c.selection_q && typeof c.selection_q === 'object' ? c.selection_q : (c.q != null ? { q: c.q, sel: c.sel || [] } : { q: '', sel: [] });
            const qText = (sq.q || '').trim();
            const selArr = Array.isArray(sq.sel) ? sq.sel : [];
            if (qText) questionData[qText] = selArr;
          });
          if (Object.keys(questionData).length > 0) {
            displayMessages.push({
              role: 'assistant',
              type: 'interactive_qa',
              content: { question_data: questionData, interactive_qa: true },
              ts: run[0].ts
            });
          } else {
            run.forEach(m => displayMessages.push(m));
          }
          continue;
        }
        displayMessages.push(msg);
      }

      container.innerHTML = displayMessages.map((msg, index) => {

        const isUser = msg.role === 'user';
        const isSystem = msg.role === 'system';

        const align = isUser ? 'justify-end' : 'justify-start';

        const bubbleCls = isUser

          ? 'bg-gradient-to-br from-slate-800 to-slate-900 text-white rounded-2xl rounded-tr-sm'

          : isSystem

          ? 'bg-gradient-to-br from-slate-100 to-slate-50 border border-slate-300 text-slate-700 rounded-2xl'

          : 'bg-white border border-slate-200 text-slate-900 rounded-2xl rounded-tl-sm shadow-sm';

        // 处理时间戳：可能是 Date 对象、数字时间戳或字符串
        let timeStr = '-';
        if (msg.ts) {
          try {
            if (msg.ts instanceof Date) {
              timeStr = msg.ts.toLocaleTimeString();
            } else if (typeof msg.ts === 'string') {
              // 从 localStorage 加载的字符串，需要转换为 Date
              const date = new Date(msg.ts);
              if (!isNaN(date.getTime())) {
                timeStr = date.toLocaleTimeString();
              }
            } else if (typeof msg.ts === 'number') {
              // 数字时间戳
              const date = new Date(msg.ts > 1000000000000 ? msg.ts : msg.ts * 1000);
              if (!isNaN(date.getTime())) {
                timeStr = date.toLocaleTimeString();
              }
            }
          } catch (e) {
            console.warn('时间格式化失败:', e, msg.ts);
            timeStr = '-';
          }
        }

        // 获取消息内容（可能是字符串或对象）
        const content = normalizeMessageContent(typeof msg.content === 'string'
          ? { text: msg.content }
          : msg.content || {}, msg.role);
        const source = content.source || null;  // 消息来源（"web" 或 "wechat"），selection_q 也可能需要
        const msgType = msg.type || content.type || 'text';
        const ragContents = content.rag_contents || [];  // 在分支外定义，避免 selection_q 时未定义

        let contentHtml = '';

        // 合并后的「问题+选择答案」：用与实时问诊相同的卡片样式渲染
        if (msgType === 'interactive_qa' && content.question_data && typeof content.question_data === 'object') {
          contentHtml += renderInteractiveQuestions(content.question_data);
        } else if (msgType === 'selection_q') {
          // 单条 selection_q（与交互式问题一致：点击选项填入「[问题 答复:选项]」，由用户发送后写入聊天记录并显示）
          const sq = content.selection_q && typeof content.selection_q === 'object'
            ? content.selection_q
            : (content && typeof content === 'object' ? content : { q: '', sel: [] });
          const qText = normalizeComplianceText(sq.q || '');
          const selArr = Array.isArray(sq.sel) ? sq.sel.map(opt => normalizeComplianceText(opt)) : [];
          contentHtml += `<div class="interactive-questions single-question"><div class="interactive-question-card border border-slate-200 rounded-md px-2 py-1.5 bg-slate-50"><div class="interactive-question-title whitespace-pre-wrap break-words text-[13px] leading-6">${escapeHtml(qText)}</div>`;
          if (selArr.length > 0) {
            contentHtml += `<div class="interactive-option-list mt-3 space-y-2">` + selArr.map(opt => {
              const safe = String(opt || '');
              return `<button class="interactive-option-btn w-full text-left px-3 py-2 rounded-lg border border-slate-200 hover:border-medical-green hover:bg-medical-green-pale transition-colors text-[12px]" onclick="selectQuestionOption(this)" data-question="${escapeHtml(qText)}" data-option="${escapeHtml(safe)}">${escapeHtml(safe)}</button>`;
            }).join('') + `</div>`;
          }
          contentHtml += `</div></div>`;
        } else {
        // 对于 Markdown 内容，保留原始文本（包括空行）；对于普通文本，使用 normalizeMessageText
        let rawText = normalizeComplianceText(content.text || '');

        const isMarkdownText = isMarkdown(rawText);
        const hasHtmlContent = containsHtml(rawText);
        // 对于 HTML 和 Markdown 内容，保留原始文本；对于普通文本，使用 normalizeMessageText
        const text = (hasHtmlContent || isMarkdownText) ? rawText : normalizeMessageText(rawText);
        const imgurl = content.imgurl || '';
        const references = content.reference || [];
        const questionData = content.question_data || null;  // JSON格式的问题数据
        const isInteractiveQA = content.interactive_qa || false;

        // 图片（优先显示，在文本之前）
        if (imgurl) {
          contentHtml += `<div class="mb-2 p-1.5 rounded-xl shadow-md" style="background: linear-gradient(to bottom right, var(--primary-green), var(--primary-green-darker));">
            <img src="${escapeHtml(imgurl)}" alt="图片" class="max-w-full max-h-96 rounded-md bg-white shadow-sm border border-white/60" onerror="this.style.display='none'" />
          </div>`;
        }

        // 交互式问题（如果有JSON格式的问题数据，优先显示交互式问题）
        if (questionData && typeof questionData === 'object' && !isUser && !isSystem) {
          // 解析并渲染交互式问题
          const questionsHtml = renderInteractiveQuestions(questionData);
          contentHtml += questionsHtml;
          // 如果有轮次信息，显示在问题下方
          if (isInteractiveQA && content.current_round && content.max_rounds) {
            contentHtml += `<div class="text-xs text-slate-500 mt-3 text-center">[第 ${content.current_round}/${content.max_rounds} 轮问答]</div>`;
          }
        } else if (text) {
          // 文本内容（当没有交互式问题时显示）
          if (isUser || isSystem) {
            // 用户和系统消息：纯文本，不处理链接和Markdown，整体字号略小
            contentHtml += `<div class="whitespace-pre-wrap break-words text-[13px] leading-6">${escapeHtml(text)}</div>`;
          } else {
            // 助手消息：检测并渲染 Markdown 格式或 HTML 格式 - AI分析结果添加宣纸底纹
            if (containsHtml(text)) {
              // HTML 格式：直接渲染 HTML，添加宣纸底纹
              contentHtml += `<div class="html-content text-[13px] leading-6 bg-rice-paper rounded-lg p-3 border border-amber-100">
                ${sanitizeHtml(text)}
              </div>`;
            } else if (isMarkdownText) {
              // Markdown 格式：使用专门的样式，添加宣纸底纹
              const markdownHtml = renderMarkdown(text);
              contentHtml += `<div class="markdown-content text-[13px] leading-6 prose prose-sm max-w-none bg-rice-paper rounded-lg p-3 border border-amber-100">
                ${markdownHtml}
              </div>`;
            } else {
              // 普通文本：可以包含链接，添加宣纸底纹
              contentHtml += `<div class="whitespace-pre-wrap break-words text-[13px] leading-6 bg-rice-paper rounded-lg p-3 border border-amber-100">${detectAndConvertLinks(text)}</div>`;
            }
          }
        }
        } // end selection_q else

        // RAG内容引用（缩略展示）：问诊模式已有右侧参考栏，避免把检索片段夹在回复气泡里。
        const shouldShowInlineRag = (
          typeof activeMode !== 'undefined' &&
          activeMode === 'corpus' &&
          typeof currentPatientId !== 'undefined' &&
          currentPatientId !== 'common'
        );
        if (ragContents && ragContents.length > 0 && !isUser && !isSystem && shouldShowInlineRag) {
          contentHtml += `<div class="mt-3 pt-2 border-t border-slate-200 bg-rice-paper rounded-lg p-3">
            <div class="text-[10px] font-semibold text-slate-600 mb-2 uppercase tracking-wider">知识库参考</div>
            <div class="space-y-2 max-h-48 overflow-y-auto custom-scrollbar">
              ${ragContents.slice(0, 5).map(ragItem => `
                <div class="text-[11px] bg-white/80 rounded-lg px-3 py-2 border border-slate-200 hover:bg-white transition-colors">
                  ${renderRagContent(ragItem)}
                </div>
              `).join('')}
              ${ragContents.length > 5 ? `<div class="text-[10px] text-slate-500 text-center py-1">还有 ${ragContents.length - 5} 条参考内容...</div>` : ''}
            </div>
          </div>`;
        }

        const messageClass = isUser ? 'message-user' : isSystem ? '' : 'message-assistant';

        return `

          <div class="flex ${align} ${messageClass}">

            <div class="max-w-[80%]">

              <div class="mb-1.5 text-[10px] text-slate-400 ${isUser ? 'text-right' : ''} flex items-center gap-1.5 ${isUser ? 'justify-end' : ''}">

                ${isUser ? '<i data-lucide="user" width="10"></i><span>我</span>' : isSystem ? '<i data-lucide="info" width="10"></i><span>系统</span>' : '<i data-lucide="bot" width="10"></i><span>助手</span>'}
                <span>·</span>
                <span>${timeStr}</span>

              </div>

              <div class="px-3.5 py-2.5 text-[13px] leading-6 shadow-md rounded-2xl ${bubbleCls} message-bubble">

                ${contentHtml || '<div class="text-slate-400 italic">（空消息）</div>'}

              </div>

              ${source === 'wechat' ? `<div class="mt-1 text-[10px] text-slate-400 ${isUser ? 'text-right' : 'text-left'}">同步自微信</div>` : ''}

            </div>

          </div>

        `;

      }).join('');

      // 重新创建图标
      lucide.createIcons();

      container.scrollTop = container.scrollHeight;

    }

    // selection_q：点击选项后自动填入并发送
    function handleSelectionQAnswer(optionText) {
      try {
        const chatInput = document.getElementById('chat-input');
        if (chatInput) {
          chatInput.value = String(optionText || '');
          autoResizeChatInput(chatInput);
        }
        sendChat();
      } catch (e) {
        console.error('handleSelectionQAnswer error:', e);
      }
    }



    function escapeHtml(str) {

      return String(str)

        .replace(/&/g, '&amp;')

        .replace(/</g, '&lt;')

        .replace(/>/g, '&gt;')

        .replace(/"/g, '&quot;')

        .replace(/'/g, '&#39;');

    }

    function normalizeComplianceText(value) {
      if (value == null) return value;
      return String(value)
        .replace(/病名诊断/g, '评估结论')
        .replace(/中医证型/g, '体质倾向')
        .replace(/开方医师/g, '咨询顾问')
        .replace(/遵医嘱/g, '遵照指引')
        .replace(/\bRp\s*[:：]/gi, '方案组合：')
        .replace(/\bR\s*P\s*[:：]/gi, '方案组合：')
        .replace(/病名/g, '评估方向')
        .replace(/证型/g, '体质倾向')
        .replace(/辨证/g, '体质识别')
        .replace(/医嘱/g, '指引');
    }

    // 检测文本是否包含 HTML 标签
    function sanitizeHtml(html) {
      html = normalizeComplianceText(html);
      const template = document.createElement('template');
      template.innerHTML = String(html || '');

      const allowedTags = new Set([
        'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3', 'h4',
        'hr', 'i', 'li', 'ol', 'p', 'pre', 'span', 'strong', 'table', 'tbody',
        'td', 'th', 'thead', 'tr', 'u', 'ul'
      ]);
      const blockedTags = new Set(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'form', 'input', 'button']);
      const allowedAttrs = new Set(['href', 'title', 'target', 'rel', 'colspan', 'rowspan']);

      const isSafeUrl = (value) => {
        const url = String(value || '').trim();
        return /^(https?:|mailto:|tel:|#|\/)/i.test(url) && !/^javascript:/i.test(url);
      };

      const clean = (node) => {
        Array.from(node.children).forEach((el) => {
          const tag = el.tagName.toLowerCase();

          if (blockedTags.has(tag)) {
            el.remove();
            return;
          }

          if (!allowedTags.has(tag)) {
            el.replaceWith(document.createTextNode(el.textContent || ''));
            return;
          }

          Array.from(el.attributes).forEach((attr) => {
            const name = attr.name.toLowerCase();
            const value = attr.value || '';
            if (!allowedAttrs.has(name) || name.startsWith('on') || /^javascript:/i.test(value)) {
              el.removeAttribute(attr.name);
              return;
            }
            if (name === 'href') {
              if (!isSafeUrl(value)) {
                el.removeAttribute(attr.name);
                return;
              }
              el.setAttribute('target', '_blank');
              el.setAttribute('rel', 'noopener noreferrer');
            }
          });

          clean(el);
        });
      };

      clean(template.content);
      return template.innerHTML;
    }

    function containsHtml(text) {
      if (!text || typeof text !== 'string') return false;
      // 检测常见的 HTML 标签
      const htmlPattern = /<[a-z][\s\S]*?>/i;
      return htmlPattern.test(text);
    }

    // 检测文本是否为 Markdown 格式
    function isMarkdown(text) {
      if (!text || typeof text !== 'string') return false;

      // 如果包含 HTML 标签，优先认为是 HTML 内容
      if (containsHtml(text)) {
        return false; // HTML 内容不当作 Markdown 处理
      }

      // 检测常见的 Markdown 标记
      const markdownPatterns = [
        /^#{1,6}\s/,                    // 标题 (# ## ###)
        /\*\*.*?\*\*/,                  // 粗体 (**text**)
        /\*.*?\*/,                      // 斜体 (*text*)
        /\[.*?\]\(.*?\)/,               // 链接 [text](url)
        /^[-*+]\s/,                     // 无序列表
        /^\d+\.\s/,                     // 有序列表
        /^>\s/,                         // 引用 (> text)
        /```[\s\S]*?```/,               // 代码块
        /`[^`]+`/,                      // 行内代码
        /\|.*?\|/,                      // 表格
        /^---+$/,                       // 分隔线
        /!\[.*?\]\(.*?\)/               // 图片 ![alt](url)
      ];

      // 如果包含多个 Markdown 标记，认为是 Markdown
      let markdownCount = 0;
      for (const pattern of markdownPatterns) {
        if (pattern.test(text)) {
          markdownCount++;
        }
      }

      // 包含至少2个 Markdown 标记，或者包含代码块/表格等复杂结构
      return markdownCount >= 2 || /```[\s\S]*?```/.test(text) || /\|.*?\|.*?\|/.test(text);
    }

    // 渲染 Markdown 为 HTML
    function renderMarkdown(text) {
      if (!text || typeof text !== 'string') return '';
      text = normalizeComplianceText(text);

      // 检查 marked 库是否可用
      if (typeof marked !== 'undefined') {
        try {
          // 配置 marked 选项
          if (marked.setOptions) {
            marked.setOptions({
              breaks: true,        // 支持换行
              gfm: true,           // GitHub Flavored Markdown
              headerIds: false,    // 不生成标题ID
              mangle: false        // 不混淆邮箱地址
            });
          }
          return sanitizeHtml(marked.parse(text));
        } catch (e) {
          console.error('Markdown 渲染失败:', e);
          return escapeHtml(text);
        }
      } else {
        // 如果 marked 库未加载，返回转义的纯文本
        console.warn('marked 库未加载，无法渲染 Markdown');
        return escapeHtml(text);
      }
    }



