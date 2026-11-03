    let currentAccountId = localStorage.getItem('wackup_selected_account') || null;
    let availableAccounts = [];
    let allChats = [];
    let currentMode = 'chats';
    let searchDebounceTimer = null;
    let selectedChatId = null;
    let activeMediaFilter = null;
    let currentMessages = [];
    let filteredMessages = [];
    let renderedCount = 0;
    const PAGE_SIZE = 300;
    let lastSyncData = {};

    // LAN / Colaboración en equipo (Fase 4) y Estado de Espacios de Trabajo (Fase 3)
    let currentWorkspaceView = 'conversations';
    let currentProfile = 'full';
    let allWorkspaceTemplates = [];
    let systemLogsLines = [];
    let selectedChatAssociatedIds = [];
    let myOperatorName = localStorage.getItem('wackup_operator_name') || 'Operador';
    let myOfficePin = localStorage.getItem('wackup_office_pin') || '';
    let myClientId = 'web_' + Math.random().toString(36).substring(2, 10);
    let sseEventSource = null;
    let presenceHeartbeatTimer = null;
    let activePresenceMap = {};
    let expectedNotesUpdatedAt = null;
    let currentKanbanAssignedFilter = 'all';
    let knownOperatorsList = [];

    const _origFetch = window.fetch.bind(window);
    async function authFetch(resource, init = {}) {
      const opts = { ...init };
      const headers = new Headers(opts.headers || {});
      if (myOfficePin) {
        headers.set('X-WAckup-Pin', myOfficePin);
      }
      if (myOperatorName) {
        headers.set('X-WAckup-Operator', myOperatorName);
      }
      opts.headers = headers;

      const res = await _origFetch(resource, opts);
      if (res.status === 401) {
        showLanPinModal();
      }
      return res;
    }
    window.fetch = authFetch;

    function withAccount(url) {
      if (!currentAccountId) return url;
      const sep = url.includes('?') ? '&' : '?';
      return `${url}${sep}account_id=${encodeURIComponent(currentAccountId)}`;
    }

    const AVATAR_COLORS = [
      '#00a884', '#0284c7', '#7c3aed', '#db2777', 
      '#ea580c', '#16a34a', '#059669', '#4f46e5'
    ];
    const SENDER_COLORS = [
      '#1f7a63', '#1e88e5', '#8e24aa', '#e53935', 
      '#fb8c00', '#00acc1', '#43a047', '#3949ab'
    ];

    function safeDomId(prefix, rawId) {
      if (!rawId) return prefix + 'unknown';
      return prefix + encodeURIComponent(String(rawId)).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function updateThemeMenuLabel(isDark) {
      const iconEl = document.getElementById('themeMenuIcon');
      const textEl = document.getElementById('themeMenuText');
      if (iconEl) iconEl.textContent = isDark ? '☀️' : '🌙';
      if (textEl) textEl.textContent = isDark ? 'Modo claro' : 'Modo oscuro';
    }

    function toggleTheme() {
      const isDark = document.body.classList.toggle('dark');
      localStorage.setItem('wa_backup_theme', isDark ? 'dark' : 'light');
      updateThemeMenuLabel(isDark);
    }

    function initTheme() {
      const saved = localStorage.getItem('wa_backup_theme');
      const isDark = saved === 'dark';
      if (isDark) {
        document.body.classList.add('dark');
      }
      updateThemeMenuLabel(isDark);
    }

    function closeAllDropdowns() {
      document.querySelectorAll('.dropdown-menu.open').forEach(el => el.classList.remove('open'));
      document.querySelectorAll('.btn-kebab.active').forEach(el => el.classList.remove('active'));
    }

    function toggleDropdown(menuId, btnEl, event) {
      if (event) event.stopPropagation();
      const menu = document.getElementById(menuId);
      if (!menu) return;
      const isOpen = menu.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) {
        menu.classList.add('open');
        if (btnEl) btnEl.classList.add('active');
      }
    }

    /* Gestión y selector de múltiples cuentas */
    async function loadAccountsList() {
      try {
        const res = await fetch('/api/accounts');
        availableAccounts = await res.json();
        renderAccountSelector();
      } catch (e) {
        console.error('Error cargando cuentas:', e);
      }
    }

    function renderAccountSelector() {
      const container = document.getElementById('accountSelectorContainer');
      const label = document.getElementById('currentAccountLabel');
      const arrow = document.getElementById('accountSelectorArrow');
      const dropdown = document.getElementById('accountDropdown');
      if (!container || !label || !dropdown) return;

      if (!availableAccounts || availableAccounts.length === 0) {
        container.style.display = 'none';
        return;
      }

      let activeAcc = availableAccounts.find(a => a.id === currentAccountId);
      if (!activeAcc || (activeAcc.id === 'default' && availableAccounts.some(a => a.id !== 'default'))) {
        const realAcc = availableAccounts.find(a => a.id !== 'default') || availableAccounts[0];
        activeAcc = realAcc;
        currentAccountId = activeAcc ? activeAcc.id : null;
        if (currentAccountId) {
          localStorage.setItem('wackup_selected_account', currentAccountId);
        }
      }

      container.style.display = 'flex';
      const displayName = activeAcc.name || formatPhoneNumber(activeAcc.phone) || activeAcc.id;
      label.textContent = displayName;
      label.title = `Cuenta activa: ${displayName} (${activeAcc.id})`;

      if (activeAcc) {
        updateProfileUIElements(activeAcc.workspace_profile, activeAcc.workspace_profile_label);
      }

      arrow.style.display = availableAccounts.length > 1 ? 'inline' : 'none';

      dropdown.innerHTML = '';
      availableAccounts.forEach(acc => {
        const item = document.createElement('div');
        const isActive = acc.id === currentAccountId;
        item.className = 'account-dropdown-item' + (isActive ? ' active' : '');
        item.onclick = (e) => {
          e.stopPropagation();
          selectAccount(acc.id);
        };

        const titleText = acc.name || formatPhoneNumber(acc.phone) || acc.id;
        const phoneSubtitle = (acc.phone && acc.name && acc.name !== acc.phone) ? formatPhoneNumber(acc.phone) : (acc.id !== titleText ? acc.id : '');
        const msgsSubtitle = `${(acc.total_messages || 0).toLocaleString()} msgs · ${acc.total_chats || 0} chats`;

        item.innerHTML = `
          <div class="account-item-title">
            <span>${escapeHtml(titleText)}</span>
            ${isActive ? '<span style="color: var(--accent); font-size: 11px; font-weight: 600;">✓ Activa</span>' : ''}
          </div>
          <div class="account-item-subtitle">${phoneSubtitle ? escapeHtml(phoneSubtitle) + ' · ' : ''}${msgsSubtitle}</div>
        `;
        dropdown.appendChild(item);
      });
    }

    function toggleAccountDropdown(event) {
      if (event) event.stopPropagation();
      if (!availableAccounts || availableAccounts.length <= 1) return;
      const menu = document.getElementById('accountDropdown');
      if (!menu) return;
      const isOpen = menu.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) {
        menu.classList.add('open');
      }
    }

    async function selectAccount(accountId) {
      closeAllDropdowns();
      if (currentAccountId === accountId) return;
      currentAccountId = accountId;
      localStorage.setItem('wackup_selected_account', accountId);
      selectedChatId = null;
      allChats = [];
      currentMessages = [];
      filteredMessages = [];

      document.getElementById('chat-header').style.display = 'none';
      document.getElementById('chat-filter-bar').style.display = 'none';
      document.getElementById('messages-container').style.display = 'none';
      const copilotDrawerEl = document.getElementById('chat-copilot-drawer');
      if (copilotDrawerEl) copilotDrawerEl.style.display = 'none';
      const copilotBackdropEl = document.getElementById('copilot-backdrop');
      if (copilotBackdropEl) copilotBackdropEl.style.display = 'none';
      document.getElementById('empty-state').style.display = 'flex';

      renderAccountSelector();
      showToast(`Cambiando a cuenta: ${accountId}...`);
      await updateSyncStatus();
      await loadChats();
    }

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.menu-container') && !e.target.closest('.account-selector-container')) {
        closeAllDropdowns();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeAllDropdowns();
        if (window.innerWidth < 1440 && typeof isChatCopilotOpen !== 'undefined' && isChatCopilotOpen) {
          toggleChatCopilot(false);
        }
        return;
      }

      // Atajos del App Rail: Alt+1, Alt+2, Alt+3, Alt+4
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        if (e.key === '1') {
          e.preventDefault();
          switchWorkspaceView('conversations');
        } else if (e.key === '2' && currentProfile !== 'backup_only') {
          e.preventDefault();
          switchWorkspaceView('crm');
        } else if (e.key === '3' && currentProfile !== 'backup_only') {
          e.preventDefault();
          switchWorkspaceView('templates');
        } else if (e.key === '4') {
          e.preventDefault();
          switchWorkspaceView('system');
        }
        return;
      }

      // Evitar interceptar atajos mientras el usuario escribe en campos editables
      const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : '';
      const isEditable = e.target && (e.target.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT');
      if (isEditable) return;
      if (e.ctrlKey || e.altKey || e.metaKey) return;

      const key = e.key.toUpperCase();
      if (key === 'R') {
        e.preventDefault();
        promptRestartSystem();
      } else if (key === 'P') {
        e.preventDefault();
        cycleWorkspaceProfileUI();
      } else if (key === 'U') {
        e.preventDefault();
        checkSystemUpdates();
      }
    });

    function toggleChatFilterBar() {
      const bar = document.getElementById('chat-filter-bar');
      if (!bar) return;
      const isHidden = bar.style.display === 'none' || !bar.style.display;
      if (isHidden) {
        bar.style.display = 'flex';
        const menuText = document.getElementById('chatFilterMenuText');
        if (menuText) menuText.textContent = 'Ocultar barra de filtros';
      } else {
        closeChatFilterBar();
      }
    }

    function closeChatFilterBar() {
      const bar = document.getElementById('chat-filter-bar');
      if (bar) bar.style.display = 'none';
      const menuText = document.getElementById('chatFilterMenuText');
      if (menuText) menuText.textContent = 'Buscar por fecha y multimedia';
      if (activeMediaFilter !== null || document.getElementById('filterDateFrom').value || document.getElementById('filterDateTo').value) {
        activeMediaFilter = null;
        document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach((el, idx) => el.classList.toggle('active', idx === 0));
        document.getElementById('filterDateFrom').value = '';
        document.getElementById('filterDateTo').value = '';
        applyClientFilters();
      }
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 3500);
    }

    function updateProfileUIElements(profile, label) {
      currentProfile = profile || 'full';
      const isBackupOnly = currentProfile === 'backup_only';
      const displayLabel = label || currentProfile || 'Suite Completa';
      const menuLabel = document.getElementById('menuProfileLabel');
      if (menuLabel) {
        menuLabel.textContent = `Cambiar perfil: ${displayLabel} (P)`;
      }
      const crmPill = document.getElementById('crmProfilePill');
      if (crmPill) {
        crmPill.textContent = displayLabel;
      }
      const copilotPill = document.getElementById('copilotProfilePill');
      if (copilotPill) {
        copilotPill.textContent = currentProfile.toUpperCase();
      }

      // Ocultar módulos CRM/Plantillas/Copiloto cuando el perfil es backup_only
      const railCrm = document.getElementById('railBtnCrm');
      const railTpl = document.getElementById('railBtnTemplates');
      const btnCopilot = document.getElementById('btnChatCopilot');
      if (railCrm) railCrm.style.display = isBackupOnly ? 'none' : 'flex';
      if (railTpl) railTpl.style.display = isBackupOnly ? 'none' : 'flex';
      if (btnCopilot) btnCopilot.style.display = isBackupOnly ? 'none' : 'inline-flex';

      if (isBackupOnly) {
        if (currentWorkspaceView === 'crm' || currentWorkspaceView === 'templates') {
          switchWorkspaceView('conversations');
        }
        if (typeof isChatCopilotOpen !== 'undefined' && isChatCopilotOpen) {
          toggleChatCopilot(false);
        }
      }

      // Actualizar estado activo de tarjetas de perfil en vista Sistema
      document.querySelectorAll('.profile-card-option').forEach(el => el.classList.remove('active'));
      const activeCard = document.getElementById('profCard-' + currentProfile);
      if (activeCard) activeCard.classList.add('active');

      const secTicket = document.getElementById('copilotSecTicket');
      const secSales = document.getElementById('copilotSecSales');
      if (secTicket) secTicket.style.display = (currentProfile === 'full' || currentProfile === 'support') ? 'flex' : 'none';
      if (secSales) secSales.style.display = (currentProfile === 'full' || currentProfile === 'sales') ? 'flex' : 'none';
    }

    async function promptRestartSystem() {
      if (!confirm("¿Deseas reiniciar el visor de WAckup?")) return;
      showToast("⚡ Reiniciando visor...");
      try {
        await fetch('/api/system/restart', { method: 'POST' });
      } catch (err) {
        // En reinicio, la conexión HTTP puede interrumpirse inmediatamente
      }
      showToast("⚡ Reiniciando visor... Se reconectará automáticamente.");
    }

    async function cycleWorkspaceProfileUI() {
      showToast("👤 Cambiando perfil de trabajo...");
      try {
        const res = await fetch(withAccount('/api/profile/cycle'), { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
          const label = data.profile_label || data.profile;
          updateProfileUIElements(data.profile, label);

          // Actualizar lista de cuentas en memoria si aplica
          if (typeof availableAccounts !== 'undefined' && availableAccounts && currentAccountId) {
            const acc = availableAccounts.find(a => a.id === currentAccountId);
            if (acc) {
              acc.workspace_profile = data.profile;
              acc.workspace_profile_label = label;
            }
          }

          // Actualizar panel CRM si está visible
          const crmView = document.getElementById('view-crm') || document.getElementById('crm-view');
          if ((currentWorkspaceView === 'crm' || (crmView && crmView.style.display !== 'none')) && typeof loadCrmDashboard === 'function') {
            loadCrmDashboard();
          }

          // Actualizar copiloto drawer si está visible y hay un chat seleccionado
          if (typeof isChatCopilotOpen !== 'undefined' && isChatCopilotOpen && selectedChatId && typeof loadChatCopilotContext === 'function') {
            loadChatCopilotContext(selectedChatId);
          }

          showToast(`👤 Perfil activo: ${label}`);
        } else {
          showToast("Error al cambiar perfil: " + (data.error || "Desconocido"));
        }
      } catch (err) {
        console.error("Error cambiando perfil:", err);
        showToast("Error al cambiar perfil: " + (err.message || "desconocido"));
      }
    }

    let latestUpdateInfo = null;

    async function checkSystemUpdates() {
      showToast("🔄 Consultando actualizaciones en GitHub...");
      try {
        const res = await authFetch('/api/system/update/check');
        const data = await res.json();
        if (data.ok) {
          latestUpdateInfo = data;
          if (data.status === 'update_available') {
            openUpdateModal(data);
          } else if (data.status === 'dev_branch') {
            showToast(`⚙️ Modo desarrollo (${data.branch || 'rama'}). Actualizaciones pausadas.`);
          } else {
            showToast(`✓ WAckup está actualizado (${data.current_version || 'v3.0.0'}).`);
          }
        } else {
          showToast(`⚠️ ${data.message || 'No se pudo conectar con GitHub'}`);
        }
      } catch (err) {
        showToast("⚠️ Error al verificar actualizaciones");
      }
    }

    function openUpdateModal(info) {
      const modal = document.getElementById('updateModal');
      if (!modal) return;
      const curEl = document.getElementById('updateCurrentVer');
      if (curEl) curEl.textContent = `v${info.current_version || '3.0.0'}`;
      const latEl = document.getElementById('updateLatestVer');
      if (latEl) latEl.textContent = `v${info.latest_version || '3.0.0'}`;
      const notesEl = document.getElementById('updateNotesContainer');
      if (notesEl) notesEl.textContent = info.release_notes || "Mejoras de rendimiento, estabilidad y nuevas funciones.";
      const prog = document.getElementById('updateProgressBox');
      if (prog) prog.style.display = 'none';
      const acts = document.getElementById('updateModalActions');
      if (acts) acts.style.display = 'flex';
      const btn = document.getElementById('btnApplyUpdate');
      if (btn) {
        btn.disabled = false;
        btn.textContent = `⬇️ Actualizar a v${info.latest_version || '3.0.0'}`;
      }
      modal.style.display = 'flex';
    }

    function closeUpdateModal() {
      const modal = document.getElementById('updateModal');
      if (modal) modal.style.display = 'none';
    }

    async function applySystemUpdate() {
      const btn = document.getElementById('btnApplyUpdate');
      const progress = document.getElementById('updateProgressBox');
      if (btn) btn.disabled = true;
      if (progress) {
        progress.style.display = 'block';
        progress.textContent = '⏳ Descargando paquete y aplicando actualización... Por favor no cierres la ventana.';
      }

      try {
        const body = latestUpdateInfo && latestUpdateInfo.download_url ? { download_url: latestUpdateInfo.download_url } : {};
        const res = await authFetch('/api/system/update/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await res.json();
        if (data && data.ok) {
          if (progress) {
            progress.textContent = '✅ ¡Actualización aplicada con éxito! Reiniciando servidor local...';
          }
          showToast('✓ Actualización completada. Recargando visor...');
          setTimeout(() => {
            window.location.reload();
          }, 2600);
        } else {
          if (progress) {
            progress.textContent = `⚠️ Error: ${data.error || 'No se pudo aplicar la actualización'}`;
          }
          if (btn) btn.disabled = false;
        }
      } catch (err) {
        if (progress) {
          progress.textContent = '⚠️ Error de conexión durante la actualización.';
        }
        if (btn) btn.disabled = false;
      }
    }

    function getColorForName(name, palette = AVATAR_COLORS) {
      if (!name) return palette[0];
      let hash = 0;
      for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
      return palette[Math.abs(hash) % palette.length];
    }

    function isBinaryOrBase64(text) {
      if (!text) return false;
      const t = text.trim();
      if (t.startsWith('data:image') || t.includes('/9j/')) return true;
      if (/^[0-9a-zA-Z._+-]+@(s\\.whatsapp\\.net|lid|g\\.us|c\\.us|broadcast|newsletter)$/i.test(t)) return true;
      if (/@(s\\.whatsapp\\.net|lid|g\\.us|c\\.us|broadcast)\\b/i.test(t) && !t.includes(' ')) return true;
      if (t.length > 35 && !/\\s/.test(t) && /^[-A-Za-z0-9+/=_*&\"'()%:;$^]+$/.test(t)) return true;
      return false;
    }

    function cleanText(text) {
      if (!text) return '';
      if (isBinaryOrBase64(text)) return '';
      let t = text.replace(/\ufffd/g, '').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '').trim();
      t = t.replace(/^[A-Za-z0-9]{1,2}\r?\n[0-9!-/:-@\[-`{-~](?=[A-Za-z"'])/u, '');
      t = t.replace(/^[A-Za-z0-9]{1,2}\r?\n[A-Za-z](?=[A-Za-z])/u, '');
      if (isBinaryOrBase64(t)) return '';
      return t.trim();
    }

    function formatLocalDate(ts, fallbackStr) {
      if (ts) {
        const d = new Date(ts * 1000);
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
      }
      return fallbackStr ? fallbackStr.split(' ')[0] : 'Desconocida';
    }

    function formatLocalTime(ts, fallbackStr) {
      if (ts) {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      }
      return fallbackStr && fallbackStr.includes(' ') ? fallbackStr.split(' ')[1].substring(0, 5) : '';
    }

    function isHumanContactName(name, jid) {
      if (!name) return false;
      const str = String(name).trim();
      if (!str) return false;
      if (jid && str.toLowerCase() === String(jid).trim().toLowerCase()) return false;
      const lower = str.toLowerCase();
      if (lower.endsWith('@lid') || lower.endsWith('@c.us') || lower.endsWith('@s.whatsapp.net') || lower.endsWith('@g.us') || lower.endsWith('@broadcast')) {
        return false;
      }
      const digitsOnly = str.replace(/\D/g, '');
      if (digitsOnly === str && str.length >= 12) {
        return false;
      }
      return true;
    }

    function formatPhoneNumber(rawPhone) {
      if (!rawPhone) return '';
      const raw = String(rawPhone).split('@')[0].replace(/\D/g, '');
      if (!raw) return '';
      if (/^549\d{10}$/.test(raw)) {
        return `+54 9 ${raw.substring(3, 6)} ${raw.substring(6, 9)}-${raw.substring(9)}`;
      }
      if (/^54\d{10}$/.test(raw)) {
        return `+54 ${raw.substring(2, 5)} ${raw.substring(5, 8)}-${raw.substring(8)}`;
      }
      if (/^1\d{10}$/.test(raw)) {
        return `+1 ${raw.substring(1, 4)} ${raw.substring(4, 7)}-${raw.substring(7)}`;
      }
      if (/^\d{7,15}$/.test(raw)) {
        return `+${raw}`;
      }
      return raw;
    }

    function formatFriendlyName(name, jid, phone) {
      if (isHumanContactName(name, jid)) {
        return name;
      }
      if (phone) {
        const formatted = formatPhoneNumber(phone);
        if (formatted) return formatted;
      }
      const raw = (name || jid || '').split('@')[0];
      const isLid = (jid && String(jid).endsWith('@lid')) || (name && String(name).endsWith('@lid'));
      if (!isLid && raw) {
        const formattedRaw = formatPhoneNumber(raw);
        if (formattedRaw) return formattedRaw;
      }
      if (phone) return formatPhoneNumber(phone);
      return raw || 'Desconocido';
    }

    function formatPreview(lastMsg, lastTipo) {
      const clean = cleanText(lastMsg);
      if (clean) return clean;
      if (lastTipo === 'image') return '📷 Foto';
      if (lastTipo === 'video') return '🎬 Video';
      if (lastTipo === 'ptt') return '🎤 Mensaje de voz';
      if (lastTipo === 'audio') return '🎵 Audio';
      if (lastTipo === 'document') return '📄 Documento';
      if (lastTipo === 'sticker') return '💟 Sticker';
      if (lastTipo === 'album') return '📷 Álbum de fotos';
      if (lastTipo === 'revoked') return '🚫 Este mensaje fue eliminado';
      if (lastTipo === 'location') return '📍 Ubicación';
      if (lastTipo === 'vcard') return '👤 Contacto';
      if (lastTipo === 'poll_creation') return '📊 Encuesta';
      if (lastTipo === 'interactive' || lastTipo === 'list' || lastTipo === 'rich_response') return '💬 Mensaje interactivo';
      if (lastTipo === 'groups_v4_invite') return '👥 Invitación de grupo';
      return 'Sin mensajes';
    }

    let syncIntervalId = null;

    async function init() {
      initTheme();
      await loadAccountsList();
      await updateSyncStatus();
      await loadChats();
      await loadNetworkSettings();
      await loadSystemInfo();
      checkOnboardingStatus();
      connectSSE();
      // Si todavía no hay chats respaldados, consultar cada 2s para que la pantalla se actualice de inmediato al conectar WhatsApp Web
      startSyncPolling((!allChats || allChats.length === 0) ? 2000 : 4000);

      // Soporte para abrir chat y mensaje específico desde URL (?chat_id=...&msg_id=...)
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const urlChatId = urlParams.get('chat_id');
        const urlMsgId = urlParams.get('msg_id');
        if (urlChatId && Array.isArray(allChats)) {
          const matchedChat = allChats.find(c => c.chat_id === urlChatId || (c.canonical_id && c.canonical_id === urlChatId));
          if (matchedChat) {
            selectChat(matchedChat, urlMsgId);
          } else {
            selectChat({ chat_id: urlChatId, nombre: urlChatId }, urlMsgId);
          }
        }
      } catch (e) {
        console.warn('Error procesando parámetros de URL en el visor:', e);
      }
    }

    let selectedOnboardingProfile = 'full';
    let obExtPollingInterval = null;
    let cachedSystemInfo = null;

    async function loadSystemInfo() {
      try {
        const res = await authFetch('/api/system/info');
        const data = await res.json();
        if (data && data.ok) {
          cachedSystemInfo = data;
          const verEl = document.getElementById('systemAppVersion');
          if (verEl) verEl.textContent = `v${data.version || '3.0.0'}`;

          const extInp = document.getElementById('extPathInput');
          if (extInp && data.extension_path) extInp.value = data.extension_path;

          const obExtInp = document.getElementById('obExtPathInput');
          if (obExtInp && data.extension_path) obExtInp.value = data.extension_path;

          updateShortcutButtonsUI(data);
        }
      } catch (e) {
        console.warn("Error cargando info del sistema:", e);
      }
    }

    function updateShortcutButtonsUI(info) {
      const btnStartup = document.getElementById('btnSystemStartup');
      if (btnStartup && info) {
        btnStartup.textContent = info.startup_shortcut_exists ? '✅ Autoarranque Activado' : '🚀 Autoarranque Windows';
      }
      const btnObStartup = document.getElementById('btnObStartup');
      if (btnObStartup && info) {
        btnObStartup.textContent = info.startup_shortcut_exists ? '✅ Autoarranque Activado' : 'Activar Autoarranque';
      }
      const btnObDesk = document.getElementById('btnObDesktop');
      if (btnObDesk && info) {
        btnObDesk.textContent = info.desktop_shortcut_exists ? '✅ Acceso Creado' : 'Crear Acceso';
      }
    }

    async function checkOnboardingStatus() {
      try {
        const res = await authFetch('/api/system/onboarding');
        const data = await res.json();
        if (data && data.ok && data.onboarding_completed === false) {
          openOnboardingModal(1);
        }
      } catch (e) {
        console.warn("Error verificando onboarding:", e);
      }
    }

    function openOnboardingModal(step = 1) {
      const modal = document.getElementById('onboardingModal');
      if (!modal) return;
      modal.style.display = 'flex';
      goToOnboardingStep(step);
      loadSystemInfo();
    }

    function closeOnboardingModal() {
      const modal = document.getElementById('onboardingModal');
      if (modal) modal.style.display = 'none';
      stopOnboardingExtPolling();
    }

    function goToOnboardingStep(step) {
      for (let i = 1; i <= 3; i++) {
        const content = document.getElementById(`obStepContent-${i}`);
        const pill = document.getElementById(`obStepPill-${i}`);
        if (content) content.style.display = (i === step) ? 'block' : 'none';
        if (pill) {
          if (i === step) pill.classList.add('active');
          else pill.classList.remove('active');
        }
      }
      if (step === 2) {
        startOnboardingExtPolling();
      } else {
        stopOnboardingExtPolling();
      }
    }

    function selectOnboardingProfile(profile) {
      selectedOnboardingProfile = profile;
      document.querySelectorAll('.ob-profile-card').forEach(card => {
        if (card.getAttribute('data-profile') === profile) {
          card.classList.add('selected');
        } else {
          card.classList.remove('selected');
        }
      });
    }

    function copyOnboardingExtPath() {
      const inp = document.getElementById('obExtPathInput');
      const val = inp ? inp.value : (cachedSystemInfo ? cachedSystemInfo.extension_path : '');
      if (val) {
        navigator.clipboard.writeText(val).then(() => {
          showToast('📋 Ruta de la extensión copiada al portapapeles');
        }).catch(() => {
          showToast('⚠️ No se pudo copiar automáticamente. Selecciónala manualmente.');
        });
      }
    }

    function startOnboardingExtPolling() {
      stopOnboardingExtPolling();
      checkOnboardingExtConnection();
      obExtPollingInterval = setInterval(checkOnboardingExtConnection, 2000);
    }

    function stopOnboardingExtPolling() {
      if (obExtPollingInterval) {
        clearInterval(obExtPollingInterval);
        obExtPollingInterval = null;
      }
    }

    async function checkOnboardingExtConnection() {
      try {
        const res = await authFetch('/api/system/onboarding');
        const data = await res.json();
        const badge = document.getElementById('obLiveStatusBadge');
        if (!badge) return;
        if (data && data.extension_connected) {
          badge.classList.add('connected');
          badge.innerHTML = '<span>✅</span> ¡Extensión detectada y conectada con éxito!';
        } else {
          badge.classList.remove('connected');
          badge.innerHTML = '<span class="ob-status-spinner">⏳</span> Esperando conexión de la extensión... Abre WhatsApp Web en tu navegador.';
        }
      } catch (e) {}
    }

    async function createOnboardingShortcut(action) {
      try {
        const res = await authFetch('/api/system/shortcuts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: action, enable: true })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast(`✓ ${data.message || 'Acceso directo creado'}`);
          await loadSystemInfo();
        } else {
          showToast(`⚠️ ${data.error || 'No se pudo crear el acceso'}`);
        }
      } catch (e) {
        showToast('⚠️ Error al comunicarse con el servidor');
      }
    }

    async function createWindowsShortcut(action) {
      await createOnboardingShortcut(action);
    }

    async function toggleWindowsStartupShortcut() {
      const isEnabled = cachedSystemInfo && cachedSystemInfo.startup_shortcut_exists;
      try {
        const res = await authFetch('/api/system/shortcuts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'startup', enable: !isEnabled })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast(`✓ ${data.message}`);
          await loadSystemInfo();
        } else {
          showToast(`⚠️ ${data.error || 'Error al cambiar autoarranque'}`);
        }
      } catch (e) {
        showToast('⚠️ Error al configurar autoarranque');
      }
    }

    async function completeOnboarding() {
      try {
        const res = await authFetch('/api/system/onboarding', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            completed: true,
            profile: selectedOnboardingProfile
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          closeOnboardingModal();
          showToast('🎉 ¡Bienvenido a WAckup! Todo listo para operar.');
          if (typeof setWorkspaceProfile === 'function') {
            setWorkspaceProfile(selectedOnboardingProfile);
          }
        }
      } catch (e) {
        closeOnboardingModal();
      }
    }

    function startSyncPolling(intervalMs) {
      if (syncIntervalId) clearInterval(syncIntervalId);
      syncIntervalId = setInterval(updateSyncStatus, intervalMs);
    }

    let currentServerSessionId = null;
    let wasServerOffline = false;
    let offlineReconnectWorker = null;
    let offlineProbeInFlight = false;

    function stopOfflineReconnectWorker() {
      if (offlineReconnectWorker) {
        try { offlineReconnectWorker.terminate(); } catch (e) {}
        offlineReconnectWorker = null;
      }
    }

    function startOfflineReconnectWorker() {
      if (offlineReconnectWorker) return;
      const probeOnce = async () => {
        if (offlineProbeInFlight) return;
        offlineProbeInFlight = true;
        try {
          const res = await fetch(withAccount(`/api/sync/status?viewer=1&client_id=${encodeURIComponent(myClientId)}`), { cache: 'no-store' });
          if (res && res.ok) {
            stopOfflineReconnectWorker();
            updateSyncStatus();
          }
        } catch (e) {
          // El servidor sigue apagado; reintentará en el próximo pulso
        } finally {
          offlineProbeInFlight = false;
        }
      };

      try {
        // Un Web Worker dedicado no sufre el throttling de 60s de Chrome en pestañas en segundo plano
        const blob = new Blob(
          ['setInterval(function(){ postMessage("tick"); }, 800);'],
          { type: 'application/javascript' }
        );
        const workerUrl = URL.createObjectURL(blob);
        offlineReconnectWorker = new Worker(workerUrl);
        URL.revokeObjectURL(workerUrl);
        offlineReconnectWorker.onmessage = probeOnce;
      } catch (e) {
        const fallbackId = setInterval(probeOnce, 1000);
        offlineReconnectWorker = { terminate: () => clearInterval(fallbackId) };
      }
    }

    async function updateSyncStatus() {
      try {
        const res = await fetch(withAccount(`/api/sync/status?viewer=1&client_id=${encodeURIComponent(myClientId)}`));
        const data = await res.json();
        stopOfflineReconnectWorker();

        // Detección de reinicio del servidor: recargar la pestaña activa en vez de duplicarla
        if (data && data.server_session_id) {
          if (currentServerSessionId === null) {
            currentServerSessionId = data.server_session_id;
          } else if (currentServerSessionId !== data.server_session_id) {
            console.log("Servidor reiniciado. Actualizando pestaña actual...");
            window.location.reload();
            return;
          }
        }
        if (wasServerOffline) {
          wasServerOffline = false;
          console.log("Servidor reconectado. Actualizando pestaña actual...");
          window.location.reload();
          return;
        }

        const prevLastTs = lastSyncData.last_timestamp;
        const prevTotalMsgs = lastSyncData.total_messages;
        const prevTotalChats = lastSyncData.total_chats;
        const prevValidMsgs = lastSyncData.valid_messages;
        const prevDownloadedMedia = lastSyncData.downloaded_media;
        const prevExpiredMedia = lastSyncData.expired_media;
        const hadNoChats = (!allChats || allChats.length === 0);

        lastSyncData = data;

        // Si no teníamos cuenta activa pero ahora la API nos indica una (o se crearon nuevas cuentas), refrescar selector
        if (data.account_id && (!currentAccountId || (availableAccounts.length === 0 && ((data.total_messages || 0) > 0 || (data.total_chats || 0) > 0)))) {
          await loadAccountsList();
        }

        const syncStatusEl = document.getElementById('syncStatusText');
        if (syncStatusEl) {
          syncStatusEl.textContent = `${(data.total_messages || 0).toLocaleString()} msgs`;
        }

        const dbStatsEl = document.getElementById('dbStatsText');
        if (dbStatsEl) {
          dbStatsEl.textContent = 
            `Base de datos: ${(data.total_messages || 0).toLocaleString()} mensajes | ${data.total_chats || 0} chats | ${data.downloaded_media || 0} archivos multimedia (${data.pending_media || 0} en cola)`;
        }

        if (document.getElementById('media-modal') && document.getElementById('media-modal').style.display === 'flex') {
          renderMediaStats(data);
        }

        const messagesChanged = prevLastTs !== undefined && (
          data.last_timestamp !== prevLastTs || 
          data.total_messages !== prevTotalMsgs || 
          data.valid_messages !== prevValidMsgs
        );
        const chatsChanged = prevTotalChats !== undefined && (data.total_chats !== prevTotalChats);
        const mediaChanged = prevDownloadedMedia !== undefined && (
          data.downloaded_media !== prevDownloadedMedia || 
          data.expired_media !== prevExpiredMedia
        );
        const firstDataArrived = hadNoChats && ((data.total_chats || 0) > 0 || (data.total_messages || 0) > 0);

        // Si llegaron chats o mensajes nuevos, recargar la lista de chats automáticamente
        if (messagesChanged || chatsChanged || firstDataArrived) {
          await loadChats();
          // Al pasar de 0 a tener chats, ajustar la frecuencia de refresco a 4s
          if (hadNoChats && allChats && allChats.length > 0) {
            startSyncPolling(4000);
          }
        }

        // Si el chat abierto recibió nuevos mensajes o completó descargas multimedia, refrescarlo sin perder la posición de lectura
        if ((messagesChanged || mediaChanged) && selectedChatId) {
          await refreshActiveChatSilently();
        }
      } catch (e) {
        wasServerOffline = true;
        startOfflineReconnectWorker();
        console.error('Error al actualizar estado:', e);
        const syncStatusEl = document.getElementById('syncStatusText');
        if (syncStatusEl) syncStatusEl.textContent = 'Desconectado';
      }
    }

    async function refreshActiveChatSilently() {
      if (!selectedChatId) return;
      const container = document.getElementById('messages-container');
      // No interrumpir si el usuario está reproduciendo un audio o video
      const isPlayingMedia = Array.from(container.querySelectorAll('audio, video')).some(el => !el.paused);
      if (isPlayingMedia) return;

      const chatIdSnapshot = selectedChatId;
      try {
        const res = await fetch(withAccount(`/api/messages?chat_id=${encodeURIComponent(chatIdSnapshot)}`));
        const freshMessages = await res.json();
        if (selectedChatId !== chatIdSnapshot) return;

        let hasChanged = freshMessages.length !== currentMessages.length;
        if (!hasChanged) {
          for (let i = freshMessages.length - 1; i >= 0; i--) {
            const a = freshMessages[i];
            const b = currentMessages[i];
            if (a.msg_id !== b.msg_id || a.archivo_local !== b.archivo_local || a.estado_multimedia !== b.estado_multimedia || a.contenido !== b.contenido || a.reenviado !== b.reenviado) {
              hasChanged = true;
              break;
            }
          }
        }
        if (!hasChanged) return;

        const wasNearBottom = (container.scrollHeight - container.scrollTop - container.clientHeight) < 80;
        const prevScrollTop = container.scrollTop;
        const prevFilteredLen = filteredMessages.length;

        currentMessages = freshMessages;
        filteredMessages = computeFilteredMessages(currentMessages);
        const delta = Math.max(0, filteredMessages.length - prevFilteredLen);
        renderedCount = Math.max(PAGE_SIZE, renderedCount + delta);

        renderMessages(filteredMessages, null, !wasNearBottom);
        if (!wasNearBottom) {
          container.scrollTop = prevScrollTop;
        }
      } catch (e) {
        // Ignorar errores transitorios en segundo plano
      }
    }

    async function loadChats() {
      try {
        const prevCount = allChats ? allChats.length : 0;
        const res = await fetch(withAccount('/api/chats'));
        allChats = await res.json();
        if (document.getElementById('searchInput') && document.getElementById('searchInput').value.trim()) {
          handleSearchInput();
        } else {
          renderChats(allChats);
        }
        updateEmptyState();
        if (prevCount === 0 && allChats && allChats.length > 0) {
          showToast('¡WhatsApp Web sincronizado con éxito! Conversaciones listas.');
        }
      } catch (e) {
        console.error('Error cargando chats:', e);
      }
    }

    function updateEmptyState() {
      const emptyStateEl = document.getElementById('empty-state');
      if (!emptyStateEl || selectedChatId) return;

      if (!allChats || allChats.length === 0) {
        emptyStateEl.innerHTML = `
          <div class="onboarding-box">
            <span class="onboarding-badge">🚀 Guía de Primer Inicio</span>
            <h3>Servidor Activo y Listo</h3>
            <p class="onboarding-desc">Para comenzar a respaldar tus conversaciones de forma segura y privada, completa estos sencillos pasos:</p>
            
            <div class="onboarding-steps">
              <div class="onboarding-step">
                <div class="step-num">1</div>
                <div class="step-content">
                  <strong>Servidor local en funcionamiento</strong>
                  <span>El motor SQLite está activo y escuchando en <code>http://127.0.0.1:8080</code>.</span>
                </div>
              </div>

              <div class="onboarding-step">
                <div class="step-num">2</div>
                <div class="step-content">
                  <strong>Carga la extensión en tu navegador (Chrome / Edge / Brave)</strong>
                  <span>Abre <code>chrome://extensions</code>, activa el <em>Modo de desarrollador</em> y haz clic en <em>Cargar descomprimida</em> seleccionando esta carpeta:</span>
                  <div class="path-copy-row">
                    <input type="text" id="extPathInput" class="path-copy-input" readonly value="Cargando ruta..." />
                    <button type="button" class="btn-copy-path" onclick="copyExtensionPath()">📋 Copiar ruta</button>
                  </div>
                </div>
              </div>

              <div class="onboarding-step">
                <div class="step-num">3</div>
                <div class="step-content">
                  <strong>Abre WhatsApp Web</strong>
                  <span>Ingresa a <a href="https://web.whatsapp.com" target="_blank" style="color: var(--accent); font-weight:600; text-decoration: underline;">web.whatsapp.com</a>. Tus mensajes comenzarán a respaldarse de forma pasiva e incremental.</span>
                </div>
              </div>
            </div>

            <div class="onboarding-live-status">
              <span class="live-pulse-dot"></span>
              <span>Esperando primera sincronización desde WhatsApp Web... La pantalla se actualizará automáticamente sin recargar.</span>
            </div>
          </div>
        `;
        fetchExtensionPath();
      } else {
        emptyStateEl.innerHTML = `
          <h2>WAckup</h2>
          <p>Selecciona una conversación a la izquierda o utiliza el buscador indexado de texto completo.</p>
          <p id="dbStatsText" style="margin-top: 14px; font-size: 13px; color: var(--text-time);"></p>
        `;
        const statsEl = document.getElementById('dbStatsText');
        if (statsEl && lastSyncData && lastSyncData.total_messages !== undefined) {
          statsEl.textContent = `Base de datos: ${(lastSyncData.total_messages || 0).toLocaleString()} mensajes | ${lastSyncData.total_chats || 0} chats | ${lastSyncData.downloaded_media || 0} archivos multimedia (${lastSyncData.pending_media || 0} en cola)`;
        }
      }
    }

    let cachedExtensionPath = '';
    async function fetchExtensionPath() {
      try {
        if (!cachedExtensionPath) {
          const res = await fetch('/api/extension/info');
          const data = await res.json();
          cachedExtensionPath = data.extension_path || '';
        }
        const input = document.getElementById('extPathInput');
        if (input) input.value = cachedExtensionPath;
      } catch (e) {}
    }

    async function copyExtensionPath() {
      if (!cachedExtensionPath) {
        await fetchExtensionPath();
      }
      if (cachedExtensionPath) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(cachedExtensionPath);
        } else {
          const input = document.getElementById('extPathInput');
          if (input) {
            input.select();
            document.execCommand('copy');
          }
        }
        showToast('Ruta de la extensión copiada al portapapeles.');
      }
    }

    function renderChats(chats) {
      const list = document.getElementById('chat-list');
      list.innerHTML = '';
      if (!chats || chats.length === 0) {
        list.innerHTML = '<li style="padding: 24px 14px; color: var(--text-time); text-align: center; font-size: 13px; line-height: 1.5;">Sin conversaciones respaldadas aún.<br>Sigue la guía en pantalla.</li>';
        return;
      }
      chats.forEach(chat => {
        const li = document.createElement('li');
        li.className = 'chat-item' + (chat.chat_id === selectedChatId ? ' active' : '');
        li.id = safeDomId('chat-', chat.chat_id);
        li.onclick = () => selectChat(chat);
        
        const friendlyTitle = formatFriendlyName(chat.display_name, chat.chat_id, chat.phone_number);
        const initial = (friendlyTitle || '?')[0].toUpperCase();
        const avatarBg = getColorForName(friendlyTitle);

        let timeStr = '';
        if (chat.last_ts) {
          const d = new Date(chat.last_ts * 1000);
          timeStr = d.toLocaleDateString() === new Date().toLocaleDateString() 
            ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : d.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
        }

        const previewText = formatPreview(chat.last_msg, chat.last_tipo);
        const tags = Array.isArray(chat.tags) ? chat.tags : [];
        const tagsHtml = tags.length > 0 
          ? `<div class="chat-tags-row">${tags.slice(0, 3).map(t => `<span class="chat-tag-pill" onclick="event.stopPropagation(); filterByTag('${escapeHtml(t)}');" title="Filtrar conversaciones por etiqueta: ${escapeHtml(t)}">🏷️ ${escapeHtml(t)}</span>`).join('')}${tags.length > 3 ? `<span class="chat-tag-pill more" title="${escapeHtml(tags.slice(3).join(', '))}">+${tags.length - 3}</span>` : ''}</div>`
          : '';

        li.innerHTML = `
          <div class="chat-avatar" style="background-color: ${avatarBg};">${initial}</div>
          <div class="chat-info">
            <div class="chat-header-row">
              <span class="chat-name">${escapeHtml(friendlyTitle)}</span>
              <span class="chat-time">${timeStr}</span>
            </div>
            <div class="chat-preview">${escapeHtml(previewText)}</div>
            ${tagsHtml}
          </div>
        `;
        list.appendChild(li);
      });
    }

    function filterByTag(tagName) {
      if (!tagName) return;
      const cleanTag = String(tagName).trim().replace(/^[#🏷️\s]+/, '');
      if (!cleanTag) return;
      if (typeof currentWorkspaceView !== 'undefined' && currentWorkspaceView !== 'conversations') {
        switchWorkspaceView('conversations');
      }
      const input = document.getElementById('searchInput');
      if (input) {
        input.value = '#' + cleanTag;
        handleSearchInput();
        input.focus();
      }
    }
    window.filterByTag = filterByTag;

    function setSearchMode(mode) {
      currentMode = mode;
      handleSearchInput();
    }

    function closeSearchAndRestoreChats(targetChatId = null) {
      const input = document.getElementById('searchInput');
      if (input) input.value = '';
      const clearBtn = document.getElementById('searchClearBtn');
      if (clearBtn) clearBtn.style.display = 'none';

      const chatList = document.getElementById('chat-list');
      const unifiedResults = document.getElementById('unified-search-results');
      const contactsList = document.getElementById('unified-contacts-list');
      const messagesList = document.getElementById('search-results-list');

      if (unifiedResults) unifiedResults.style.display = 'none';
      if (contactsList) contactsList.innerHTML = '';
      if (messagesList) messagesList.innerHTML = '';
      if (chatList) {
        chatList.style.display = 'block';
        if (chatList.children.length === 0 && allChats && allChats.length > 0) {
          renderChats(allChats);
        }
      }

      const activeId = targetChatId || selectedChatId;
      if (activeId) {
        document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
        const activeEl = document.getElementById(safeDomId('chat-', activeId));
        if (activeEl) {
          activeEl.classList.add('active');
          activeEl.scrollIntoView({ block: 'nearest' });
        }
      }
    }

    function clearSearchInput() {
      closeSearchAndRestoreChats(selectedChatId);
      const input = document.getElementById('searchInput');
      if (input) input.focus();
    }

    function handleSearchInput() {
      const input = document.getElementById('searchInput');
      if (!input) return;
      const query = input.value.trim();
      const clearBtn = document.getElementById('searchClearBtn');
      if (clearBtn) {
        clearBtn.style.display = input.value.length > 0 ? 'flex' : 'none';
      }

      const chatList = document.getElementById('chat-list');
      const unifiedResults = document.getElementById('unified-search-results');
      const contactsList = document.getElementById('unified-contacts-list');
      const messagesList = document.getElementById('search-results-list');

      if (!query) {
        closeSearchAndRestoreChats(selectedChatId);
        return;
      }

      if (chatList) chatList.style.display = 'none';
      if (unifiedResults) unifiedResults.style.display = 'block';

      // 1. Filtrado instantáneo de Contactos y Grupos (incluye etiquetas)
      const q = query.toLowerCase();
      let isTagOnlySearch = false;
      let targetTag = q;
      if (q.startsWith('#')) {
        isTagOnlySearch = true;
        targetTag = q.slice(1).trim();
      } else if (q.startsWith('tag:')) {
        isTagOnlySearch = true;
        targetTag = q.slice(4).trim();
      }

      const matchedContacts = allChats.filter(c => {
        const tags = Array.isArray(c.tags) ? c.tags : [];
        const hasMatchingTag = tags.some(t => {
          const tLower = (t || '').toLowerCase();
          return isTagOnlySearch ? (targetTag === '' || tLower.includes(targetTag)) : tLower.includes(q);
        });

        if (isTagOnlySearch) {
          return hasMatchingTag;
        }

        const name = (c.display_name || '').toLowerCase();
        const id = (c.chat_id || '').toLowerCase();
        const last = (c.last_msg || '').toLowerCase();
        const phone = (c.phone_number || '').toLowerCase();
        const phoneFmt = formatPhoneNumber(c.phone_number).toLowerCase();
        return name.includes(q) || id.includes(q) || last.includes(q) || phone.includes(q) || phoneFmt.includes(q) || hasMatchingTag;
      });

      if (contactsList) {
        contactsList.innerHTML = '';
        if (matchedContacts.length === 0) {
          contactsList.innerHTML = isTagOnlySearch
            ? `<li style="padding: 14px; font-size: 12px; color: var(--text-muted); text-align: center;">Ningún contacto tiene la etiqueta "${escapeHtml(targetTag)}"</li>`
            : '<li style="padding: 10px 14px; font-size: 12px; color: var(--text-muted);">Sin contactos o grupos coincidentes</li>';
        } else {
          const limit = isTagOnlySearch ? 100 : 20;
          matchedContacts.slice(0, limit).forEach(chat => {
            const li = document.createElement('li');
            li.className = 'unified-contact-item';
            const title = formatFriendlyName(chat.display_name, chat.chat_id, chat.phone_number);
            const initial = title ? title.charAt(0).toUpperCase() : '?';
            const color = getColorForName(title);
            const tags = Array.isArray(chat.tags) ? chat.tags : [];
            const tagsHtml = tags.length > 0 
              ? `<div class="chat-tags-row" style="margin-top: 3px;">${tags.slice(0, 3).map(t => `<span class="chat-tag-pill" onclick="event.stopPropagation(); filterByTag('${escapeHtml(t)}');" title="Filtrar por: ${escapeHtml(t)}">🏷️ ${escapeHtml(t)}</span>`).join('')}${tags.length > 3 ? `<span class="chat-tag-pill more" title="${escapeHtml(tags.slice(3).join(', '))}">+${tags.length - 3}</span>` : ''}</div>`
              : '';
            li.onclick = () => {
              closeSearchAndRestoreChats(chat.chat_id);
              selectChat(chat);
            };
            li.innerHTML = `
              <div class="unified-contact-avatar" style="background: ${color}">${escapeHtml(initial)}</div>
              <div class="unified-contact-details">
                <div class="unified-contact-name">${escapeHtml(title)}</div>
                <div class="unified-contact-sub">${escapeHtml(chat.last_msg || formatPhoneNumber(chat.phone_number) || chat.chat_id)}</div>
                ${tagsHtml}
              </div>
            `;
            contactsList.appendChild(li);
          });
        }
      }

      // 2. Búsqueda en todo el historial con FTS5 (debounce 300ms)
      clearTimeout(searchDebounceTimer);
      if (isTagOnlySearch) {
        if (messagesList) {
          messagesList.innerHTML = `<li style="padding: 14px; font-size: 12px; color: var(--text-muted); text-align: center;">🏷️ Filtrando contactos con etiqueta <b>${escapeHtml(targetTag || 'todas')}</b> (${matchedContacts.length} encontrados)</li>`;
        }
      } else {
        if (messagesList) {
          messagesList.innerHTML = '<li style="padding: 14px; font-size: 12px; color: var(--text-time); text-align: center;">Buscando en mensajes indexados (FTS5)...</li>';
        }
        searchDebounceTimer = setTimeout(() => performGlobalSearch(query), 300);
      }
    }

    async function performGlobalSearch(query) {
      const list = document.getElementById('search-results-list');
      if (!list) return;
      try {
        const res = await fetch(withAccount(`/api/search?q=${encodeURIComponent(query)}`));
        const results = await res.json();
        list.innerHTML = '';
        if (!results || results.length === 0) {
          list.innerHTML = '<li style="padding: 14px; font-size: 12px; color: var(--text-muted); text-align: center;">No se encontraron mensajes coincidentes en el historial.</li>';
          return;
        }
        results.forEach(item => {
          const li = document.createElement('li');
          li.className = 'search-item';
          const knownChat = allChats.find(c => c.chat_id === item.chat_id);
          const title = formatFriendlyName(item.chat_nombre, item.chat_id, item.phone_number || knownChat?.phone_number);
          li.onclick = () => {
            let chat = allChats.find(c => c.chat_id === item.chat_id);
            if (!chat) {
              chat = {
                chat_id: item.chat_id,
                display_name: title,
                last_msg: item.contenido,
                last_ts: item.timestamp,
                last_tipo: item.tipo
              };
              allChats.unshift(chat);
              renderChats(allChats);
            }
            closeSearchAndRestoreChats(chat.chat_id);
            selectChat(chat, item.msg_id);
          };
          li.innerHTML = `
            <div class="search-chat-title">${escapeHtml(title)}</div>
            <div class="search-snippet">${item.snippet || escapeHtml(cleanText(item.contenido || ''))}</div>
            <div class="search-date">${escapeHtml(item.fecha_utc || '')}</div>
          `;
          list.appendChild(li);
        });
      } catch (e) {
        list.innerHTML = '<li style="padding: 14px; font-size: 12px; color: #ef4444; text-align: center;">Error al consultar índice FTS5.</li>';
      }
    }

    async function selectChat(chat, scrollToMsgId = null) {
      if (!chat || !chat.chat_id) return;

      // Asegurar que si la búsqueda estaba abierta o con texto, se cierre y restaure la lista de chats
      const searchInputEl = document.getElementById('searchInput');
      if (searchInputEl && searchInputEl.value.trim().length > 0) {
        closeSearchAndRestoreChats(chat.chat_id);
      }

      selectedChatId = chat.chat_id;
      startPresenceHeartbeat(chat.chat_id);
      updatePresenceBanners();
      activeMediaFilter = null;
      document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach((el, idx) => el.classList.toggle('active', idx === 0));
      document.getElementById('filterDateFrom').value = '';
      document.getElementById('filterDateTo').value = '';
      const menuText = document.getElementById('chatFilterMenuText');
      if (menuText) menuText.textContent = 'Buscar por fecha y multimedia';

      document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
      let activeEl = document.getElementById(safeDomId('chat-', chat.chat_id));
      if (!activeEl && allChats) {
        if (!allChats.some(c => c.chat_id === chat.chat_id)) {
          allChats.unshift(chat);
        }
        renderChats(allChats);
        activeEl = document.getElementById(safeDomId('chat-', chat.chat_id));
      }
      if (activeEl) {
        activeEl.classList.add('active');
        activeEl.scrollIntoView({ block: 'nearest' });
      }

      document.getElementById('empty-state').style.display = 'none';
      document.getElementById('chat-header').style.display = 'flex';
      document.getElementById('chat-filter-bar').style.display = 'none';
      document.getElementById('messages-container').style.display = 'flex';

      // Sincronizar estado del Copiloto en el Visor
      selectedChatAssociatedIds = [chat.chat_id];
      const copilotDrawer = document.getElementById('chat-copilot-drawer');
      const copilotBackdrop = document.getElementById('copilot-backdrop');
      const btnCopilot = document.getElementById('btnChatCopilot');
      if (isChatCopilotOpen && currentProfile !== 'backup_only') {
        if (copilotDrawer) copilotDrawer.style.display = 'flex';
        if (copilotBackdrop) copilotBackdrop.style.display = 'block';
        if (btnCopilot) btnCopilot.classList.add('active');
        loadChatCopilotContext(chat.chat_id);
      } else {
        if (copilotDrawer) copilotDrawer.style.display = 'none';
        if (copilotBackdrop) copilotBackdrop.style.display = 'none';
        if (btnCopilot) btnCopilot.classList.remove('active');
      }

      const isGroup = chat.chat_id.endsWith('@g.us');
      const isNewsletter = chat.chat_id.endsWith('@newsletter');
      const knownChat = allChats.find(c => c.chat_id === chat.chat_id) || chat;
      const rawPhone = (!isGroup && !isNewsletter)
        ? (chat.phone_number || knownChat.phone_number || ((chat.chat_id.endsWith('@c.us') || chat.chat_id.endsWith('@s.whatsapp.net')) ? chat.chat_id.split('@')[0] : null))
        : null;

      const friendlyTitle = formatFriendlyName(chat.display_name, chat.chat_id, rawPhone);
      document.getElementById('headerTitle').textContent = friendlyTitle;

      const headerSubEl = document.getElementById('headerSub');
      if (isGroup) {
        headerSubEl.textContent = 'Grupo de WhatsApp';
      } else if (isNewsletter) {
        headerSubEl.textContent = 'Canal de WhatsApp';
      } else if (rawPhone) {
        const formattedPhone = formatPhoneNumber(rawPhone);
        headerSubEl.innerHTML = `
          <span class="header-phone-row">
            <span class="header-phone-text">${escapeHtml(formattedPhone)}</span>
            <button type="button" class="btn-copy-phone" onclick="copyChatPhone('${escapeHtml(formattedPhone)}', this)" title="Copiar número de teléfono">
              <svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
              <span class="copy-label">Copiar</span>
            </button>
          </span>
        `;
      } else {
        headerSubEl.textContent = 'Conversación directa';
      }
      
      const avatarEl = document.getElementById('headerAvatar');
      avatarEl.textContent = (friendlyTitle || '?')[0].toUpperCase();
      avatarEl.style.backgroundColor = getColorForName(friendlyTitle);

      const container = document.getElementById('messages-container');
      container.innerHTML = '<p style="text-align: center; color: var(--text-muted); margin-top: 24px;">Cargando mensajes...</p>';

      const res = await fetch(withAccount(`/api/messages?chat_id=${encodeURIComponent(chat.chat_id)}`));
      currentMessages = await res.json();
      filteredMessages = currentMessages;

      if (scrollToMsgId) {
        const targetIdx = filteredMessages.findIndex(m => m.msg_id === scrollToMsgId);
        if (targetIdx !== -1) {
          renderedCount = Math.max(PAGE_SIZE, filteredMessages.length - targetIdx + 50);
        } else {
          renderedCount = PAGE_SIZE;
        }
      } else {
        renderedCount = PAGE_SIZE;
      }

      renderMessages(filteredMessages, scrollToMsgId);
    }

    function loadOlderMessages() {
      const container = document.getElementById('messages-container');
      const prevScrollHeight = container.scrollHeight;
      renderedCount += PAGE_SIZE;
      renderMessages(filteredMessages, null, true);
      container.scrollTop = container.scrollHeight - prevScrollHeight;
    }

    function highlightBubble(el) {
      if (!el) return;
      const container = document.getElementById('messages-container');
      if (container) {
        const containerRect = container.getBoundingClientRect();
        const elRect = el.getBoundingClientRect();
        const currentScrollTop = container.scrollTop;
        const targetScrollTop = currentScrollTop + (elRect.top - containerRect.top) - (containerRect.height / 2) + (elRect.height / 2);
        container.scrollTo({ top: Math.max(0, targetScrollTop), behavior: 'smooth' });
      } else {
        el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      }
      el.style.transition = 'outline 0.2s ease, box-shadow 0.2s ease';
      el.style.outline = '3px solid var(--accent)';
      el.style.boxShadow = '0 0 16px rgba(0, 168, 132, 0.6)';
      setTimeout(() => {
        el.style.outline = 'none';
        el.style.boxShadow = 'none';
      }, 2500);
    }

    function findMessageById(rawId) {
      if (!rawId) return null;
      const cleanRaw = String(rawId).trim();
      // 1. Coincidencia exacta o por subcadena/stanzaId en filteredMessages
      let found = filteredMessages.find(m => m.msg_id === cleanRaw);
      if (found) return found;

      found = filteredMessages.find(m => m.msg_id && (m.msg_id.includes(cleanRaw) || cleanRaw.includes(m.msg_id)));
      if (found) return found;

      // 2. Si está en currentMessages pero oculto por filtros de fecha/medios
      found = currentMessages.find(m => m.msg_id === cleanRaw || (m.msg_id && (m.msg_id.includes(cleanRaw) || cleanRaw.includes(m.msg_id))));
      if (found) {
        closeChatFilterBar();
        return found;
      }
      return null;
    }

    function scrollToMessage(rawId) {
      if (!rawId) return;
      const targetMsg = findMessageById(rawId);
      if (!targetMsg) {
        const directEl = document.getElementById(safeDomId('msg-', rawId));
        if (directEl) {
          highlightBubble(directEl);
        } else {
          showToast('El mensaje citado no se encuentra en el historial descargado.');
        }
        return;
      }

      const actualId = targetMsg.msg_id;
      const idx = filteredMessages.findIndex(m => m.msg_id === actualId);

      if (idx !== -1) {
        const startIdx = Math.max(0, filteredMessages.length - renderedCount);
        if (idx < startIdx) {
          // El mensaje está más arriba en el historial paginado: cargar los mensajes necesarios
          renderedCount = Math.max(renderedCount, filteredMessages.length - idx + 50);
          renderMessages(filteredMessages, actualId);
          return;
        }
      }

      const targetEl = document.getElementById(safeDomId('msg-', actualId));
      if (targetEl) {
        highlightBubble(targetEl);
      } else if (idx !== -1) {
        renderedCount = Math.max(renderedCount, filteredMessages.length - idx + 50);
        renderMessages(filteredMessages, actualId);
      }
    }

    function renderMessages(messages, scrollToMsgId = null, preserveScroll = false) {
      const container = document.getElementById('messages-container');
      container.innerHTML = '';
      let lastDate = null;
      const isGroup = selectedChatId && selectedChatId.endsWith('@g.us');

      if (messages.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-time); margin-top: 30px;">No hay mensajes coincidentes con los filtros.</p>';
        return;
      }

      const startIdx = Math.max(0, messages.length - renderedCount);
      const visibleMessages = messages.slice(startIdx);

      if (startIdx > 0) {
        const loadMoreBtn = document.createElement('button');
        loadMoreBtn.className = 'btn-load-more';
        loadMoreBtn.textContent = `⬆️ Cargar mensajes anteriores (${startIdx.toLocaleString()} restantes)`;
        loadMoreBtn.onclick = loadOlderMessages;
        container.appendChild(loadMoreBtn);
      }

      const fragment = document.createDocumentFragment();

      visibleMessages.forEach(m => {
        if (['e2e_notification', 'notification_template', 'gp2', 'protocol', 'ciphertext'].includes(m.tipo)) {
          return;
        }
        const cleanBody = cleanText(m.contenido);
        const hasMedia = Boolean(m.archivo_local || m.estado_multimedia === 'expirado' || (m.thumbnail && m.thumbnail.startsWith('data:image')));
        if (!cleanBody && !hasMedia && !m.quoted_body) {
          // Si es un mensaje cuyo contenido era un JID o basura cifrada aún pendiente de re-sincronizar
          return;
        }

        const msgDate = formatLocalDate(m.timestamp, m.fecha_utc);
        if (msgDate !== lastDate) {
          const dateDiv = document.createElement('div');
          dateDiv.className = 'date-badge';
          dateDiv.textContent = msgDate;
          fragment.appendChild(dateDiv);
          lastDate = msgDate;
        }

        const isMine = Boolean(m.es_mio) || (m.msg_id && m.msg_id.startsWith('true_'));

        const bubble = document.createElement('div');
        bubble.className = `msg-bubble ${isMine ? 'msg-outgoing' : 'msg-incoming'}`;
        if (m.msg_id) bubble.id = safeDomId('msg-', m.msg_id);

        let senderHtml = '';
        if (isGroup && !isMine) {
          const senderDisplayName = formatFriendlyName(m.remitente_nombre, m.remitente_jid);
          const senderColor = getColorForName(senderDisplayName, SENDER_COLORS);
          senderHtml = `<div class="msg-sender" style="color: ${senderColor};">${escapeHtml(senderDisplayName)}</div>`;
        }

        let forwardedHtml = '';
        if (m.reenviado && m.reenviado > 0) {
          const isMany = m.reenviado >= 5;
          const fwdLabel = isMany ? 'Reenviado muchas veces' : 'Reenviado';
          const fwdIcon = isMany
            ? `<svg viewBox="0 0 20 16"><path d="M8.5 3.5L13 8l-4.5 4.5V9.8c-3.2 0-5.4 1.1-6.8 3.7 0.7-3.6 2.6-6.2 6.8-6.9V3.5zm5.5 0L18.5 8 14 12.5v-2l2.5-2.5L14 5.5v-2z"/></svg>`
            : `<svg viewBox="0 0 16 16"><path d="M9.5 3.5L14 8l-4.5 4.5V9.8c-3.5 0-6 1.1-7.5 3.7 0.8-3.6 2.9-6.2 7.5-6.9V3.5z"/></svg>`;
          forwardedHtml = `<div class="msg-forwarded">${fwdIcon}<span>${fwdLabel}</span></div>`;
        }

        let quoteHtml = '';
        if (m.quoted_body || m.quoted_msg_id) {
          const qAuthor = m.quoted_author ? formatFriendlyName(m.quoted_author, m.quoted_author) : 'Mensaje citado';
          const qText = cleanText(m.quoted_body) || '📎 Archivo / Mensaje citado';
          const clickAttr = m.quoted_msg_id ? `onclick="scrollToMessage(this.dataset.quotedId)" data-quoted-id="${escapeHtml(m.quoted_msg_id)}" title="Ir al mensaje citado"` : '';
          quoteHtml = `
            <div class="msg-quote" ${clickAttr}>
              <div class="msg-quote-author">${escapeHtml(qAuthor)}</div>
              <div class="msg-quote-text">${escapeHtml(qText)}</div>
            </div>`;
        }

        let mediaHtml = '';
        if (m.archivo_local) {
          if (m.tipo === 'image' || m.tipo === 'sticker' || (m.mimetype && m.mimetype.startsWith('image/'))) {
            mediaHtml = `<div class="media-container"><img class="media-img" src="/${m.archivo_local}" onclick="openModal(this.src)" loading="lazy"></div>`;
          } else if (m.tipo === 'audio' || m.tipo === 'ptt' || (m.mimetype && m.mimetype.startsWith('audio/'))) {
            mediaHtml = `
              <div class="media-container audio-wrapper">
                <audio class="media-audio" controls preload="metadata" src="/${m.archivo_local}" onended="playNextAudio(this)"></audio>
                <button class="btn-speed" onclick="toggleAudioSpeed(this)">1x</button>
              </div>`;
          } else if (m.tipo === 'video' || (m.mimetype && m.mimetype.startsWith('video/'))) {
            mediaHtml = `<div class="media-container"><video class="media-video" controls preload="metadata" src="/${m.archivo_local}"></video></div>`;
          } else {
            const fileName = m.archivo_local.split('/').pop();
            mediaHtml = `<div class="media-container"><a class="media-doc" href="/${m.archivo_local}" download><span class="media-doc-icon">📄</span><span>${escapeHtml(fileName)}</span></a></div>`;
          }
        } else if (m.estado_multimedia === 'expirado') {
          mediaHtml = `<div class="media-expired"><span>⚠️</span><span>[Archivo multimedia expirado en servidores de WhatsApp]</span></div>`;
        } else if (m.thumbnail && m.thumbnail.startsWith('data:image')) {
          mediaHtml = `<div class="media-container"><img class="media-img" src="${m.thumbnail}" onclick="openModal(this.src)"></div>`;
        }

        const timeStr = formatLocalTime(m.timestamp, m.fecha_utc);
        const checkHtml = isMine ? '<span class="msg-check">✓✓</span>' : '';

        bubble.innerHTML = `
          ${senderHtml}
          ${forwardedHtml}
          ${quoteHtml}
          ${mediaHtml}
          ${cleanBody ? `<div class="msg-text">${escapeHtml(cleanBody)}</div>` : ''}
          <div class="msg-footer">
            <span>${timeStr}</span>
            ${checkHtml}
          </div>
        `;
        fragment.appendChild(bubble);
      });

      container.appendChild(fragment);

      if (scrollToMsgId) {
        const target = document.getElementById(safeDomId('msg-', scrollToMsgId));
        if (target) {
          highlightBubble(target);
          return;
        }
      }
      if (!preserveScroll) {
        container.scrollTop = container.scrollHeight;
      }
    }

    // Filtros multimedia en el chat
    function setMediaFilter(type, el) {
      activeMediaFilter = type;
      document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach(pill => pill.classList.remove('active'));
      el.classList.add('active');
      applyClientFilters();
    }

    function applyDateFilter() {
      applyClientFilters();
    }

    function computeFilteredMessages(messages) {
      const fromVal = document.getElementById('filterDateFrom').value;
      const toVal = document.getElementById('filterDateTo').value;

      let filtered = messages;

      if (activeMediaFilter) {
        if (activeMediaFilter === 'audio') {
          filtered = filtered.filter(m => m.tipo === 'audio' || m.tipo === 'ptt' || (m.mimetype && m.mimetype.startsWith('audio/')));
        } else if (activeMediaFilter === 'image') {
          filtered = filtered.filter(m => m.tipo === 'image' || (m.mimetype && m.mimetype.startsWith('image/')));
        } else if (activeMediaFilter === 'video') {
          filtered = filtered.filter(m => m.tipo === 'video' || (m.mimetype && m.mimetype.startsWith('video/')));
        } else if (activeMediaFilter === 'document') {
          filtered = filtered.filter(m => m.tipo === 'document' || (m.mimetype && !m.mimetype.startsWith('image/') && !m.mimetype.startsWith('audio/') && !m.mimetype.startsWith('video/')));
        }
      }

      if (fromVal) {
        const fromTs = new Date(fromVal + 'T00:00:00').getTime() / 1000;
        filtered = filtered.filter(m => (m.timestamp || 0) >= fromTs);
      }
      if (toVal) {
        const toTs = new Date(toVal + 'T23:59:59').getTime() / 1000;
        filtered = filtered.filter(m => (m.timestamp || 0) <= toTs);
      }
      return filtered;
    }

    function applyClientFilters() {
      filteredMessages = computeFilteredMessages(currentMessages);
      renderedCount = PAGE_SIZE;
      renderMessages(filteredMessages);
    }

    // Control de velocidad y reproducción continua de audios
    function toggleAudioSpeed(btn) {
      const wrapper = btn.closest('.audio-wrapper');
      const audio = wrapper.querySelector('audio');
      if (!audio) return;

      const speeds = [1, 1.5, 2];
      const current = audio.playbackRate;
      let nextIdx = (speeds.indexOf(current) + 1) % speeds.length;
      audio.playbackRate = speeds[nextIdx];
      btn.textContent = speeds[nextIdx] + 'x';
    }

    function playNextAudio(currentAudio) {
      const allAudios = Array.from(document.querySelectorAll('.media-audio'));
      const idx = allAudios.indexOf(currentAudio);
      if (idx !== -1 && idx + 1 < allAudios.length) {
        const next = allAudios[idx + 1];
        next.currentTime = 0;
        next.play();
        next.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }

    // Exportación de Chat a HTML y Contactos a CSV
    function exportCurrentChat(embedMedia = false) {
      if (!selectedChatId) return;
      const embedParam = embedMedia ? '&embed=1' : '';
      window.open(withAccount(`/api/export/html?chat_id=${encodeURIComponent(selectedChatId)}${embedParam}`), '_blank');
    }

    function exportContactsCsv() {
      showToast('Descargando contactos en formato CSV...');
      window.location.href = withAccount('/api/export/contacts');
    }

    function openSyncLogs() {
      fetch('/api/logs/open', { method: 'POST' }).then(() => {
        showToast('Ventana de logs abierta.');
      }).catch(() => {
        window.open('/logs', '_blank');
      });
    }

    async function copyChatPhone(phoneText, btnEl) {
      if (!phoneText) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(phoneText);
        } else {
          const ta = document.createElement('textarea');
          ta.value = phoneText;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        if (btnEl) {
          const labelEl = btnEl.querySelector('.copy-label');
          btnEl.classList.add('copied');
          if (labelEl) labelEl.textContent = '✓ Copiado';
          setTimeout(() => {
            btnEl.classList.remove('copied');
            if (labelEl) labelEl.textContent = 'Copiar';
          }, 2000);
        }
        showToast(`📋 Número copiado: ${phoneText}`);
      } catch (e) {
        showToast('Error al copiar el número al portapapeles');
      }
    }

    // Gestión de Multimedia
    async function openMediaModal() {
      const modal = document.getElementById('media-modal');
      if (modal) modal.style.display = 'flex';
      await updateSyncStatus();
      renderMediaStats(lastSyncData);
    }

    function closeMediaModal() {
      const modal = document.getElementById('media-modal');
      if (modal) modal.style.display = 'none';
    }

    function renderMediaStats(data) {
      if (!data) return;
      const dlEl = document.getElementById('statMediaDownloaded');
      const pdEl = document.getElementById('statMediaPending');
      const flEl = document.getElementById('statMediaFailed');
      const exEl = document.getElementById('statMediaExpired');
      if (dlEl) dlEl.textContent = (data.downloaded_media || 0).toLocaleString();
      if (pdEl) pdEl.textContent = (data.pending_media || 0).toLocaleString();
      if (flEl) flEl.textContent = (data.failed_media || 0).toLocaleString();
      if (exEl) exEl.textContent = (data.expired_media || 0).toLocaleString();
    }

    async function retryFailedMedia() {
      showToast('Reencolando descargas multimedia fallidas...');
      try {
        const res = await fetch(withAccount('/api/media/retry'), { method: 'POST' });
        const data = await res.json();
        showToast(`Se reencolaron ${data.reset_count || 0} archivos para descarga.`);
        await updateSyncStatus();
        renderMediaStats(lastSyncData);
      } catch (e) {
        showToast('Error al reintentar descargas multimedia');
      }
    }

    // Gestión de Backups
    async function openBackupModal() {
      const modal = document.getElementById('backup-modal');
      if (modal) modal.style.display = 'flex';
      await loadBackupList();
    }

    function closeBackupModal() {
      const modal = document.getElementById('backup-modal');
      if (modal) modal.style.display = 'none';
    }

    async function loadBackupList() {
      const container = document.getElementById('backupListContainer');
      if (!container) return;
      container.innerHTML = '<p style="padding: 14px; color: var(--text-time); text-align: center;">Cargando respaldos...</p>';
      try {
        const res = await fetch(withAccount('/api/backup/list'));
        const backups = await res.json();
        if (backups.length === 0) {
          container.innerHTML = '<p style="padding: 14px; color: var(--text-time); text-align: center;">No hay copias de seguridad manuales aún.</p>';
          return;
        }
        container.innerHTML = '';
        backups.forEach(b => {
          const div = document.createElement('div');
          div.className = 'backup-item';
          const downloadUrl = withAccount(`/api/backup/download?file=${encodeURIComponent(b.filename)}`);
          div.innerHTML = `
            <div>
              <strong>${escapeHtml(b.filename)}</strong>
              <div style="color: var(--text-time); font-size: 11px;">${b.created_at} · ${b.size_mb} MB</div>
            </div>
            <a href="${downloadUrl}" class="btn-chat-action" style="text-decoration: none;">Descargar</a>
          `;
          container.appendChild(div);
        });
      } catch (e) {
        container.innerHTML = '<p style="padding: 14px; color: #d32f2f; text-align: center;">Error al cargar lista de backups.</p>';
      }
    }

    async function triggerManualBackup() {
      showToast('Creando copia de seguridad consistente de SQLite...');
      try {
        const res = await fetch(withAccount('/api/backup/create'), { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`¡Backup creado con éxito! (${data.backup.size_mb} MB)`);
          await loadBackupList();
        } else {
          showToast('Error al crear backup: ' + (data.error || 'Desconocido'));
        }
      } catch (e) {
        showToast('Error de conexión al generar backup');
      }
    }

    function openModal(src) {
      const modal = document.getElementById('image-modal');
      document.getElementById('modalImg').src = src;
      modal.style.display = 'flex';
    }

    function escapeHtml(text) {
      if (!text) return '';
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    init();


    // =========================================================================
    // COPILOTO & MINI-CRM EN EL VISOR DE CHAT
    // =========================================================================
    let isChatCopilotOpen = localStorage.getItem('wackup_copilot_open') === 'true';
    let currentCopilotData = null;
    let copilotNotesDebounceTimer = null;
    let copilotCurrentTicket = 'none';
    let copilotCurrentStage = 'none';

    function toggleChatCopilot(forceState = null) {
      if (currentProfile === 'backup_only') {
        showToast('ℹ️ El Copiloto CRM no está disponible en el perfil "Solo Respaldo".');
        return;
      }
      if (forceState !== null) {
        isChatCopilotOpen = forceState;
      } else {
        isChatCopilotOpen = !isChatCopilotOpen;
      }
      localStorage.setItem('wackup_copilot_open', isChatCopilotOpen ? 'true' : 'false');

      const drawer = document.getElementById('chat-copilot-drawer');
      const backdrop = document.getElementById('copilot-backdrop');
      const btn = document.getElementById('btnChatCopilot');
      if (drawer) drawer.style.display = isChatCopilotOpen ? 'flex' : 'none';
      if (backdrop) backdrop.style.display = isChatCopilotOpen ? 'block' : 'none';
      if (btn) btn.classList.toggle('active', isChatCopilotOpen);

      if (isChatCopilotOpen && selectedChatId) {
        loadChatCopilotContext(selectedChatId);
      }
    }

    async function loadChatCopilotContext(chatId = null) {
      const targetId = chatId || selectedChatId;
      if (!targetId) return;

      const slaEl = document.getElementById('copilotSlaVal');
      const pillEl = document.getElementById('copilotProfilePill');
      const notesEl = document.getElementById('copilotNotesTextarea');
      const valInput = document.getElementById('copilotValueInput');
      const followInput = document.getElementById('copilotFollowUpInput');
      const feedbackEl = document.getElementById('copilotNotesFeedback');
      if (feedbackEl) feedbackEl.textContent = '';

      try {
        const res = await fetch(withAccount(`/api/copilot/context?chat_id=${encodeURIComponent(targetId)}`));
        const data = await res.json();
        currentCopilotData = data;

        if (targetId === selectedChatId) {
          selectedChatAssociatedIds = Array.isArray(data.associated_chat_ids) ? [...data.associated_chat_ids] : [targetId];
          if (data.canonical_id && !selectedChatAssociatedIds.includes(data.canonical_id)) {
            selectedChatAssociatedIds.push(data.canonical_id);
          }
          if (!selectedChatAssociatedIds.includes(targetId)) {
            selectedChatAssociatedIds.push(targetId);
          }
          updatePresenceBanners();
        }

        const crm = data.crm || {};
        const profile = data.profile || 'full';

        // 1. Perfil y visibilidad de secciones
        if (pillEl) pillEl.textContent = profile.toUpperCase();
        const secTicket = document.getElementById('copilotSecTicket');
        const secSales = document.getElementById('copilotSecSales');
        if (secTicket) secTicket.style.display = (profile === 'full' || profile === 'support') ? 'flex' : 'none';
        if (secSales) secSales.style.display = (profile === 'full' || profile === 'sales') ? 'flex' : 'none';

        // 2. SLA
        if (slaEl) slaEl.textContent = data.sla || 'Sin datos suficientes';

        // 3. Ticket status
        copilotCurrentTicket = crm.ticket_status || 'none';
        ['open', 'pending', 'resolved', 'none'].forEach(st => {
          const btn = document.getElementById(`btnTicket-${st}`);
          if (btn) btn.classList.toggle('active', copilotCurrentTicket === st);
        });

        // 4. Sales stage
        copilotCurrentStage = crm.sales_stage || 'none';
        ['lead', 'quoted', 'won', 'lost', 'none'].forEach(st => {
          const btn = document.getElementById(`btnStage-${st}`);
          if (btn) btn.classList.toggle('active', copilotCurrentStage === st);
        });

        if (valInput) valInput.value = (crm.estimated_value && crm.estimated_value > 0) ? crm.estimated_value : '';
        if (followInput) followInput.value = crm.follow_up_date || '';

        // 5. Notas privadas (con control de concurrencia optimista)
        if (notesEl) notesEl.value = crm.notes || '';
        expectedNotesUpdatedAt = crm.notes_updated_at || null;

        // 5b. Responsable asignado
        const assignedSel = document.getElementById('copilotAssignedSelect');
        if (assignedSel) {
          assignedSel.value = crm.assigned_to || '';
        }

        // 5c. Bitácora de notas de equipo
        renderCopilotNotesLog(crm.notes_log || []);

        // 6. Entidades extraídas (CUIT, Email, CBU/Alias)
        renderCopilotEntities(data.extracted_info);

        // 7. Precios cotizados históricos
        renderCopilotPrices(data.quoted_prices);

        // 8. Plantillas rápidas
        renderCopilotTemplates(data.templates);

      } catch (e) {
        console.error('Error al cargar contexto del Copiloto:', e);
      }
    }

    function renderCopilotEntities(info) {
      const container = document.getElementById('copilotEntitiesContainer');
      if (!container) return;
      if (!info) {
        container.innerHTML = '<div style="font-size: 11.5px; color: var(--text-muted); padding: 4px 0;">Sin datos detectados en este chat.</div>';
        return;
      }

      let rowsHtml = '';
      const emails = info.emails || [];
      const taxIds = info.tax_ids || [];
      const bankDetails = info.bank_details || [];

      emails.forEach(email => {
        rowsHtml += `
          <div class="copilot-entity-item">
            <span class="copilot-entity-label">✉️ Email</span>
            <span class="copilot-entity-val" title="${escapeHtml(email)}">${escapeHtml(email)}</span>
            <button type="button" class="copilot-copy-btn" onclick="copyCopilotText('${escapeHtml(email)}', 'Email')">📋</button>
          </div>
        `;
      });

      taxIds.forEach(tax => {
        rowsHtml += `
          <div class="copilot-entity-item">
            <span class="copilot-entity-label">🆔 CUIT/DNI</span>
            <span class="copilot-entity-val">${escapeHtml(tax)}</span>
            <button type="button" class="copilot-copy-btn" onclick="copyCopilotText('${escapeHtml(tax)}', 'Identificación')">📋</button>
          </div>
        `;
      });

      bankDetails.forEach(bank => {
        rowsHtml += `
          <div class="copilot-entity-item">
            <span class="copilot-entity-label">🏦 CBU/Alias</span>
            <span class="copilot-entity-val" title="${escapeHtml(bank)}">${escapeHtml(bank)}</span>
            <button type="button" class="copilot-copy-btn" onclick="copyCopilotText('${escapeHtml(bank)}', 'Dato bancario')">📋</button>
          </div>
        `;
      });

      if (!rowsHtml) {
        container.innerHTML = '<div style="font-size: 11.5px; color: var(--text-muted); padding: 4px 0;">Sin emails, CUITs ni CBUs detectados.</div>';
      } else {
        container.innerHTML = rowsHtml;
      }
    }

    function renderCopilotPrices(prices) {
      const container = document.getElementById('copilotPricesContainer');
      if (!container) return;
      if (!prices || prices.length === 0) {
        container.innerHTML = '<div style="font-size: 11.5px; color: var(--text-muted); padding: 4px 0;">Sin cotizaciones monetarias registradas.</div>';
        return;
      }

      container.innerHTML = prices.slice(0, 5).map(p => {
        const who = p.es_mio ? 'Operador' : 'Cliente';
        const dateStr = p.date ? p.date.substring(0, 10) : '';
        return `
          <div class="copilot-price-item">
            <div>
              <span class="copilot-price-val">$ ${Number(p.price || 0).toLocaleString()}</span>
              <span class="copilot-price-meta"> · ${who}</span>
            </div>
            <span class="copilot-price-meta">${dateStr}</span>
          </div>
        `;
      }).join('');
    }

    function renderCopilotTemplates(templates) {
      const container = document.getElementById('copilotTemplatesContainer');
      if (!container) return;
      if (!templates || templates.length === 0) {
        container.innerHTML = '<div style="font-size: 11.5px; color: var(--text-muted); padding: 4px 0;">Sin plantillas disponibles.</div>';
        return;
      }

      container.innerHTML = templates.map(t => {
        return `
          <button type="button" class="copilot-template-btn" onclick="copyCopilotTemplate('${escapeHtml(t.body)}')">
            <span style="font-weight: 700; color: #38bdf8; font-family: monospace;">${escapeHtml(t.shortcut)}</span>
            <span style="font-weight: 600; color: var(--text-primary);">${escapeHtml(t.title)}</span>
            <span style="font-size: 11px; color: var(--text-muted);">📋 Copiar</span>
          </button>
        `;
      }).join('');
    }

    function copyCopilotText(text, label) {
      navigator.clipboard.writeText(text).then(() => {
        showToast(`✅ ${label} copiado al portapapeles`);
      });
    }

    function copyCopilotTemplate(templateBody) {
      const chat = allChats.find(c => c.chat_id === selectedChatId);
      const name = chat ? (chat.display_name.split(' ')[0] || 'Estimado/a') : 'Estimado/a';

      const hr = new Date().getHours();
      let saludo = 'Buen día';
      if (hr >= 12 && hr < 20) saludo = 'Buenas tardes';
      else if (hr >= 20 || hr < 6) saludo = 'Buenas noches';

      const now = new Date();
      const dd = String(now.getDate()).padStart(2, '0');
      const mm = String(now.getMonth() + 1).padStart(2, '0');
      const yyyy = now.getFullYear();
      const fecha = `${dd}/${mm}/${yyyy}`;

      let processed = templateBody
        .replace(/{nombre}/gi, name)
        .replace(/{saludo}/gi, saludo)
        .replace(/{fecha}/gi, fecha);

      navigator.clipboard.writeText(processed).then(() => {
        showToast('✅ Respuesta rápida copiada al portapapeles');
      });
    }

    function handleCopilotNotesInput() {
      const feedback = document.getElementById('copilotNotesFeedback');
      if (feedback) feedback.textContent = 'Escribiendo...';

      if (copilotNotesDebounceTimer) clearTimeout(copilotNotesDebounceTimer);
      copilotNotesDebounceTimer = setTimeout(saveCopilotNotesImmediate, 600);
    }

    async function saveCopilotNotesImmediate() {
      if (copilotNotesDebounceTimer) clearTimeout(copilotNotesDebounceTimer);
      if (!selectedChatId) return;

      const textarea = document.getElementById('copilotNotesTextarea');
      const feedback = document.getElementById('copilotNotesFeedback');
      const notes = textarea ? textarea.value : '';

      if (feedback) {
        feedback.textContent = 'Guardando...';
        feedback.style.color = 'var(--text-time)';
      }

      try {
        const payload = {
          account_id: currentAccountId,
          chat_id: selectedChatId,
          notes: notes,
          updated_by: myOperatorName
        };
        if (expectedNotesUpdatedAt) {
          payload.expected_updated_at = expectedNotesUpdatedAt;
        }

        const res = await fetch('/api/copilot/notes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (res.status === 409) {
          const conflictData = await res.json();
          if (feedback) {
            feedback.textContent = '⚠️ Modificado por ' + (conflictData.current_updated_by || 'otro operador');
            feedback.style.color = '#f59e0b';
          }
          showToast('⚠️ Conflicto: Otro operador editó esta nota. Actualizando con su contenido...');
          if (textarea) textarea.value = conflictData.current_notes || '';
          expectedNotesUpdatedAt = conflictData.updated_at || null;
          return;
        }

        const data = await res.json();
        if (data && data.ok) {
          expectedNotesUpdatedAt = data.notes_updated_at || new Date().toISOString();
          if (feedback) {
            feedback.textContent = 'Guardado ✓';
            feedback.style.color = 'var(--text-time)';
          }
          flashCopilotSaveIndicator();
        }
      } catch (e) {
        if (feedback) {
          feedback.textContent = 'Error al guardar';
          feedback.style.color = '#ef4444';
        }
      }
    }

    async function setCopilotTicket(status) {
      if (!selectedChatId) return;
      copilotCurrentTicket = status;
      ['open', 'pending', 'resolved', 'none'].forEach(st => {
        const btn = document.getElementById(`btnTicket-${st}`);
        if (btn) btn.classList.toggle('active', status === st);
      });

      try {
        const res = await fetch('/api/support/ticket', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: currentAccountId,
            chat_id: selectedChatId,
            ticket_status: status
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          flashCopilotSaveIndicator();
          showToast(`Ticket: ${status.toUpperCase()} actualizado ✓`);
        }
      } catch (e) {
        showToast('Error al actualizar ticket');
      }
    }

    function setCopilotStage(stage) {
      copilotCurrentStage = stage;
      ['lead', 'quoted', 'won', 'lost', 'none'].forEach(st => {
        const btn = document.getElementById(`btnStage-${st}`);
        if (btn) btn.classList.toggle('active', stage === st);
      });
      saveCopilotPipeline();
    }

    async function saveCopilotPipeline() {
      if (!selectedChatId) return;
      const valInput = document.getElementById('copilotValueInput');
      const followInput = document.getElementById('copilotFollowUpInput');

      const val = valInput && valInput.value ? parseFloat(valInput.value) : 0.0;
      const follow = followInput && followInput.value ? followInput.value : null;

      try {
        const res = await fetch('/api/sales/pipeline', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: currentAccountId,
            chat_id: selectedChatId,
            sales_stage: copilotCurrentStage,
            estimated_value: val,
            follow_up_date: follow
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          flashCopilotSaveIndicator();
          showToast('Embudo comercial guardado ✓');
        }
      } catch (e) {
        showToast('Error al guardar embudo comercial');
      }
    }

    function flashCopilotSaveIndicator() {
      const ind = document.getElementById('copilotSaveIndicator');
      if (ind) {
        ind.style.display = 'flex';
        setTimeout(() => { ind.style.display = 'none'; }, 2000);
      }
    }

    // =========================================================================
    // FASE 4: FRONTEND DEL CENTRO DE GESTIÓN & CRM
    // =========================================================================
    let isCrmViewActive = false;
    let activeCrmTab = 'kanban';
    let activeKanbanType = 'sales';

    function toggleCrmView() {
      const targetView = currentWorkspaceView === 'crm' ? 'conversations' : 'crm';
      switchWorkspaceView(targetView);
    }

    async function loadCrmDashboard() {
      // 1. Cargar perfil de la cuenta
      try {
        const resProf = await fetch(withAccount('/api/profile'));
        const profData = await resProf.json();
        const pill = document.getElementById("crmProfilePill");
        if (pill) {
          pill.textContent = profData.profile_label || profData.profile || "Suite Completa";
        }
      } catch (e) {}

      // Actualizar label de cuenta en el CRM navbar
      const accLabel = document.getElementById("currentAccountLabel");
      const crmAccLabel = document.getElementById("crmAccountLabel");
      if (accLabel && crmAccLabel) {
        crmAccLabel.textContent = accLabel.textContent;
      }

      // 2. Cargar pestaña activa
      switchCrmTab(activeCrmTab);
    }

    function switchCrmTab(tabName) {
      activeCrmTab = tabName;
      const tabs = ['kanban', 'rfm', 'sla', 'demand', 'templates'];
      tabs.forEach(t => {
        const btn = document.getElementById(`crmTabBtn-${t}`);
        const sec = document.getElementById(`crmSection-${t}`);
        if (btn) btn.classList.toggle('active', t === tabName);
        if (sec) sec.style.display = (t === tabName) ? 'block' : 'none';
      });

      if (tabName === 'kanban') loadKanbanBoard(activeKanbanType);
      else if (tabName === 'rfm') loadRfmDormant();
      else if (tabName === 'sla') loadSupportMetrics();
      else if (tabName === 'demand') loadDemandThermometer();
      else if (tabName === 'templates') loadTemplatesManager();
    }

    function setKanbanType(type) {
      activeKanbanType = type;
      const btnSales = document.getElementById("btnKanbanSales");
      const btnSupp = document.getElementById("btnKanbanSupport");
      if (btnSales) btnSales.classList.toggle('active', type === 'sales');
      if (btnSupp) btnSupp.classList.toggle('active', type === 'support');
      loadKanbanBoard(type);
    }

    async function loadKanbanBoard(type = 'sales') {
      const container = document.getElementById("kanbanColumnsContainer");
      const statsBar = document.getElementById("kanbanStatsBar");
      if (!container) return;
      container.innerHTML = '<div style="padding: 30px; text-align: center; color: var(--text-muted);">Cargando columnas del tablero...</div>';

      try {
        const res = await fetch(withAccount(`/api/crm/kanban?type=${encodeURIComponent(type)}`));
        const data = await res.json();
        const columns = data.columns || [];

        let totalCards = 0;
        let totalMoney = 0;
        columns.forEach(col => {
          totalCards += col.count || 0;
          totalMoney += col.total_value || 0;
        });

        if (statsBar) {
          if (type === 'sales') {
            statsBar.innerHTML = `
              <span>Oportunidades: <strong>${totalCards}</strong></span>
              <span>•</span>
              <span>Pipeline Total: <strong style="color: var(--accent);">$ ${totalMoney.toLocaleString()}</strong></span>
            `;
          } else {
            statsBar.innerHTML = `
              <span>Tickets en gestión: <strong>${totalCards}</strong></span>
            `;
          }
        }

        container.innerHTML = columns.map(col => {
          let cards = col.cards || [];
          if (currentKanbanAssignedFilter === 'mine') {
            cards = cards.filter(c => c.assigned_to === myOperatorName);
          } else if (currentKanbanAssignedFilter === 'unassigned') {
            cards = cards.filter(c => !c.assigned_to);
          }

          const sumStr = (type === 'sales' && col.total_value > 0) ? `<span class="kanban-col-sum">$ ${col.total_value.toLocaleString()}</span>` : '';
          const cardsHtml = (cards && cards.length > 0) ? cards.map(card => {
            const valStr = (card.estimated_value && card.estimated_value > 0) ? `<span class="kanban-card-val">$ ${card.estimated_value.toLocaleString()}</span>` : '';
            const followStr = card.follow_up_date ? `<span>📅 ${escapeHtml(card.follow_up_date)}</span>` : '';
            const assignBadge = card.assigned_to ? `<span class="badge-operator" title="Responsable: ${escapeHtml(card.assigned_to)}" style="font-size:11px; background:var(--bg-tag); color:var(--text-tag); padding:2px 6px; border-radius:4px;">👤 ${escapeHtml(card.assigned_to)}</span>` : '';
            const stageBtns = (type === 'sales') ? `
              <select class="btn-card-action" onchange="moveKanbanCard('${escapeHtml(card.chat_id)}', this.value, null)" title="Cambiar etapa">
                <option value="lead" ${card.sales_stage === 'lead' ? 'selected' : ''}>🎯 Lead</option>
                <option value="quoted" ${card.sales_stage === 'quoted' ? 'selected' : ''}>💬 Cotiz.</option>
                <option value="won" ${card.sales_stage === 'won' ? 'selected' : ''}>🟢 Ganada</option>
                <option value="lost" ${card.sales_stage === 'lost' ? 'selected' : ''}>🔴 Perdida</option>
              </select>
            ` : `
              <select class="btn-card-action" onchange="moveKanbanCard('${escapeHtml(card.chat_id)}', null, this.value)" title="Cambiar ticket">
                <option value="open" ${card.ticket_status === 'open' ? 'selected' : ''}>🟢 Abierto</option>
                <option value="pending" ${card.ticket_status === 'pending' ? 'selected' : ''}>🟡 En Espera</option>
                <option value="resolved" ${card.ticket_status === 'resolved' ? 'selected' : ''}>⚪ Resuelto</option>
              </select>
            `;

            return `
              <div class="kanban-card">
                <div class="kanban-card-top">
                  <span class="kanban-card-name" title="${escapeHtml(card.display_name)}">${escapeHtml(card.display_name)}</span>
                  ${valStr}
                </div>
                <div class="kanban-card-msg">${escapeHtml(card.last_message || 'Sin mensajes')}</div>
                <div class="kanban-card-meta">
                  <span>${escapeHtml(card.last_activity_str || '')}</span>
                  ${assignBadge}
                  ${followStr}
                </div>
                <div class="kanban-card-actions">
                  ${stageBtns}
                  <button class="btn-card-action btn-card-chat" onclick="openChatFromCrm('${escapeHtml(card.chat_id)}')">💬 Ver Chat</button>
                </div>
              </div>
            `;
          }).join('') : `<div style="font-size: 11.5px; color: var(--text-muted); text-align: center; padding: 24px 0;">Sin contactos en esta etapa.</div>`;

          return `
            <div class="kanban-col">
              <div class="kanban-col-header">
                <div class="kanban-col-title">
                  <span>${escapeHtml(col.title)}</span>
                  <span class="kanban-col-badge">${cards.length}</span>
                </div>
                ${sumStr}
              </div>
              <div class="kanban-card-list">
                ${cardsHtml}
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        container.innerHTML = '<div style="padding: 30px; text-align: center; color: #ef4444;">Error al cargar el tablero Kanban.</div>';
      }
    }

    async function moveKanbanCard(chatId, salesStage, ticketStatus) {
      try {
        const body = {
          account_id: currentAccountId,
          chat_id: chatId
        };
        if (salesStage) body.sales_stage = salesStage;
        if (ticketStatus) body.ticket_status = ticketStatus;

        const res = await fetch('/api/crm/kanban/move', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast('Etapa actualizada con éxito ✓');
          loadKanbanBoard(activeKanbanType);
        }
      } catch (e) {
        showToast('Error al mover contacto');
      }
    }

    async function loadRfmDormant() {
      const container = document.getElementById("rfmCardsContainer");
      if (!container) return;
      container.innerHTML = '<div style="padding: 30px; text-align: center; color: var(--text-muted);">Consultando clientes inactivos...</div>';

      try {
        const res = await fetch(withAccount('/api/crm/rfm/dormant?days=30&limit=5'));
        const data = await res.json();
        const clients = data.clients || [];

        if (clients.length === 0) {
          container.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--accent); font-weight: 600;">¡Excelente! No hay clientes valiosos dormidos o sin contactar.</div>';
          return;
        }

        container.innerHTML = clients.map((c, idx) => {
          return `
            <div class="rfm-card">
              <div class="rfm-card-header">
                <div>
                  <div class="rfm-card-title">${escapeHtml(c.display_name)}</div>
                  <div class="rfm-card-sub">${c.phone_number ? '+' + escapeHtml(c.phone_number) : escapeHtml(c.chat_id)} • <strong>${c.total_messages} msgs</strong></div>
                </div>
                <span class="rfm-dormant-badge">${c.days_inactive} días inactivo</span>
              </div>
              <div style="font-size: 11px; color: var(--text-muted);">Último contacto (${escapeHtml(c.last_message_date)}):</div>
              <div style="font-size: 11.5px; color: var(--text-secondary); line-height: 1.35; font-style: italic;">"${escapeHtml(c.last_message || 'Sin texto')}"</div>
              <div style="font-size: 11px; font-weight: 600; color: var(--text-primary); margin-top: 4px;">Mensaje de reactivación sugerido (1 a 1):</div>
              <div class="rfm-msg-box" id="rfmMsgText-${idx}">${escapeHtml(c.suggested_message)}</div>
              <div class="rfm-actions">
                <button class="btn-rfm-copy" onclick="copyRfmGreeting(${idx})">📋 Copiar Saludo</button>
                <button class="btn-rfm-open" onclick="openChatFromCrm('${escapeHtml(c.chat_id)}')">💬 Abrir Chat</button>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        container.innerHTML = '<div style="padding: 30px; text-align: center; color: #ef4444;">Error al consultar clientes inactivos.</div>';
      }
    }

    function copyRfmGreeting(idx) {
      const el = document.getElementById(`rfmMsgText-${idx}`);
      if (el) {
        navigator.clipboard.writeText(el.textContent.trim()).then(() => {
          showToast('✅ Saludo copiado al portapapeles');
        }).catch(() => {
          showToast('✅ Saludo copiado');
        });
      }
    }

    async function loadSupportMetrics() {
      const kpiSla = document.getElementById("kpiSlaText");
      const kpiOpen = document.getElementById("kpiTicketsOpen");
      const kpiRes = document.getElementById("kpiTicketsResolved");
      const peakBadge = document.getElementById("heatmapPeakBadge");
      const container = document.getElementById("heatmapContainer");

      try {
        const res = await fetch(withAccount('/api/crm/support/metrics'));
        const data = await res.json();

        if (kpiSla) kpiSla.textContent = data.global_sla || '--';
        const t = data.tickets || {};
        if (kpiOpen) kpiOpen.textContent = (t.open || 0) + (t.pending || 0);
        if (kpiRes) kpiRes.textContent = t.resolved || 0;

        const hm = data.heatmap || {};
        if (peakBadge) peakBadge.textContent = 'Horario pico: ' + (hm.peak_hour || 'Sin datos');

        if (container && hm.grid) {
          const days = hm.days || ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"];
          const maxVal = hm.max_cell || 1;

          let thHours = '';
          for (let h = 0; h < 24; h++) {
            thHours += `<th class="heatmap-th-hour">${h}h</th>`;
          }

          let rowsHtml = '';
          days.forEach((dayName, dIdx) => {
            let cells = '';
            for (let h = 0; h < 24; h++) {
              const count = hm.grid[dIdx][h] || 0;
              const ratio = count / maxVal;
              let bg = 'rgba(134, 150, 160, 0.08)';
              if (count > 0) {
                bg = `rgba(0, 168, 132, ${Math.max(0.2, Math.min(1.0, ratio * 1.1))})`;
              }
              cells += `<td class="heatmap-cell" style="background: ${bg};" title="${dayName} a las ${h}:00 hs: ${count} mensajes">${count > 0 ? count : ''}</td>`;
            }
            rowsHtml += `
              <tr>
                <td class="heatmap-th-day">${escapeHtml(dayName)}</td>
                ${cells}
              </tr>
            `;
          });

          container.innerHTML = `
            <table class="heatmap-table">
              <thead>
                <tr>
                  <th></th>
                  ${thHours}
                </tr>
              </thead>
              <tbody>
                ${rowsHtml}
              </tbody>
            </table>
          `;
        }
      } catch (e) {
        if (container) container.innerHTML = '<div style="padding: 20px; color: #ef4444;">Error al cargar métricas de soporte.</div>';
      }
    }

    async function loadDemandThermometer() {
      const cloud = document.getElementById("demandCloudContainer");
      const tbody = document.getElementById("demandTableBody");
      if (!cloud || !tbody) return;

      cloud.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">Extrayendo conceptos clave...</div>';
      tbody.innerHTML = '';

      try {
        const res = await fetch(withAccount('/api/crm/demand?days=60&limit=25'));
        const data = await res.json();
        const terms = data.top_terms || [];

        if (terms.length === 0) {
          cloud.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">Sin datos suficientes de mensajes en este período.</div>';
          return;
        }

        cloud.innerHTML = terms.map(t => {
          return `
            <div class="demand-badge" onclick="searchDemandTerm('${escapeHtml(t.term)}')" title="Buscar '${escapeHtml(t.term)}' en chats">
              <span># ${escapeHtml(t.term)}</span>
              <span class="demand-badge-count">${t.count}</span>
            </div>
          `;
        }).join('');

        tbody.innerHTML = terms.map(t => {
          return `
            <tr>
              <td style="font-weight: 700; color: var(--accent);">${escapeHtml(t.term)}</td>
              <td>${t.count} menciones</td>
              <td><span style="background: rgba(0,168,132,0.1); color: var(--accent); padding: 2px 6px; border-radius: 4px; font-weight: 600;">${t.percentage}%</span></td>
              <td><button class="btn-card-action" onclick="searchDemandTerm('${escapeHtml(t.term)}')">🔍 Buscar en FTS5</button></td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        cloud.innerHTML = '<div style="color: #ef4444; font-size: 13px;">Error al cargar termómetro de demanda.</div>';
      }
    }

    function searchDemandTerm(term) {
      switchWorkspaceView('conversations');
      setSearchMode('global');
      const input = document.getElementById("searchInput");
      if (input) {
        input.value = term;
        handleSearchInput();
      }
    }

    async function loadTemplatesManager() {
      const tbody = document.getElementById("crmTemplatesTableBody");
      if (!tbody) return;
      await loadTemplatesWorkspace();
    }

    function copyTemplateDirect(text) {
      navigator.clipboard.writeText(text).then(() => {
        showToast('✅ Plantilla copiada al portapapeles');
      });
    }

    function openChatFromCrm(chatId) {
      switchWorkspaceView('conversations');
      const chat = allChats.find(c => c.chat_id === chatId);
      if (chat) {
        selectChat(chat);
      } else {
        selectChat({ chat_id: chatId, display_name: chatId });
      }
    }



    // ==========================================
    // Gestión de Etiquetas de Contacto (Fase 1)
    // ==========================================
    function renderCopilotTags(tags) {
      const container = document.getElementById('copilotTagsContainer');
      if (!container) return;
      if (!tags || tags.length === 0) {
        container.innerHTML = '<div style="font-size: 11.5px; color: var(--text-muted);">Sin etiquetas</div>';
        return;
      }
      container.innerHTML = tags.map(t => {
        const rawTag = (t && typeof t === 'object') ? (t.tag || '') : String(t || '');
        const tagName = escapeHtml(rawTag);
        if (!tagName) return '';
        return `
          <span class="tag-pill" style="display: inline-flex; align-items: center; gap: 4px; background: var(--bg-hover); border: 1px solid var(--border-color); padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500;">
            <span onclick="filterByTag('${tagName}')" style="cursor: pointer;" title="Filtrar conversaciones con la etiqueta '${tagName}'">🏷️ ${tagName}</span>
            <span onclick="removeCopilotTag('${tagName}')" style="cursor: pointer; opacity: 0.6; font-weight: bold; margin-left: 2px;" title="Eliminar etiqueta">×</span>
          </span>
        `;
      }).join('');
    }

    async function addCopilotTagPrompt() {
      if (!selectedChatId) return;
      const tag = prompt("Ingresa el nombre de la nueva etiqueta:");
      if (!tag || !tag.trim()) return;

      try {
        const res = await fetch(withAccount('/api/copilot/tags'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: selectedChatId,
            tag: tag.trim(),
            action: 'add'
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          renderCopilotTags(data.tags);
          const newTags = (data.tags || []).map(t => (t && typeof t === 'object') ? t.tag : t);
          if (allChats) {
            const chat = allChats.find(c => c.chat_id === selectedChatId || (c.canonical_id && c.canonical_id === selectedChatId));
            if (chat) {
              chat.tags = newTags;
              renderChats(allChats);
            }
          }
          showToast(`✅ Etiqueta "${tag.trim()}" agregada`);
        }
      } catch (e) {
        console.error("Error al añadir etiqueta:", e);
      }
    }

    async function removeCopilotTag(tag) {
      if (!selectedChatId || !tag) return;
      try {
        const res = await fetch(withAccount('/api/copilot/tags'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: selectedChatId,
            tag: tag,
            action: 'remove'
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          renderCopilotTags(data.tags);
          const newTags = (data.tags || []).map(t => (t && typeof t === 'object') ? t.tag : t);
          if (allChats) {
            const chat = allChats.find(c => c.chat_id === selectedChatId || (c.canonical_id && c.canonical_id === selectedChatId));
            if (chat) {
              chat.tags = newTags;
              renderChats(allChats);
            }
          }
          showToast(`🗑️ Etiqueta "${tag}" eliminada`);
        }
      } catch (e) {
        console.error("Error al eliminar etiqueta:", e);
      }
    }

    /* ========================================================
       FASE 3: FUNCIONES DE ESPACIOS DE TRABAJO (APP RAIL)
       ======================================================== */

    function switchWorkspaceView(viewId) {
      currentWorkspaceView = viewId;
      const views = ['conversations', 'crm', 'templates', 'system'];

      views.forEach(v => {
        const el = document.getElementById('view-' + v);
        if (el) {
          el.style.display = (v === viewId) ? 'flex' : 'none';
        }
      });

      document.querySelectorAll('.app-rail .rail-btn').forEach(btn => {
        if (btn.id !== 'railThemeBtn') btn.classList.remove('active');
      });

      const activeBtnId = 'railBtn' + viewId.charAt(0).toUpperCase() + viewId.slice(1);
      const activeBtn = document.getElementById(activeBtnId);
      if (activeBtn) activeBtn.classList.add('active');

      if (viewId === 'conversations') {
        closeSearchAndRestoreChats(selectedChatId);
      } else if (viewId === 'crm') {
        loadCrmDashboard();
      } else if (viewId === 'templates') {
        loadTemplatesWorkspace();
      } else if (viewId === 'system') {
        loadSystemWorkspace();
      }
    }

    async function setWorkspaceProfile(newProfile) {
      try {
        const res = await fetch(withAccount('/api/profile'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ profile: newProfile, account_id: currentAccountId })
        });
        const data = await res.json();
        if (data && data.ok) {
          const label = data.profile_label || data.profile_name || data.profile;
          updateProfileUIElements(data.profile, label);
          if (typeof availableAccounts !== 'undefined' && availableAccounts && currentAccountId) {
            const acc = availableAccounts.find(a => a.id === currentAccountId);
            if (acc) {
              acc.workspace_profile = data.profile;
              acc.workspace_profile_label = label;
            }
          }
          showToast(`✓ Perfil cambiado a: ${label}`);
        }
      } catch (e) {
        showToast('Error al cambiar perfil de trabajo');
      }
    }

    /* Gestor de Plantillas Rápidas & Etiquetas */
    async function loadTemplatesWorkspace() {
      const tbody = document.getElementById('crmTemplatesTableBody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">Cargando respuestas rápidas...</td></tr>';

      try {
        const res = await fetch(withAccount('/api/support/templates'));
        const data = await res.json();
        if (data && data.templates) {
          allWorkspaceTemplates = data.templates;
          renderTemplatesWorkspaceTable(allWorkspaceTemplates);
        }
      } catch (e) {
        if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #ef4444; padding: 24px;">Error al cargar plantillas.</td></tr>';
      }

      loadAllWorkspaceTags();
    }

    function renderTemplatesWorkspaceTable(templates) {
      const tbody = document.getElementById('crmTemplatesTableBody');
      if (!tbody) return;
      if (!templates || templates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">No hay plantillas creadas. Haz clic en "+ Nueva Plantilla".</td></tr>';
        return;
      }

      tbody.innerHTML = templates.map(tpl => {
        const pBadge = tpl.profile_type === 'support' ? '<span class="status-badge" style="background: rgba(56,189,248,0.15); color: #0284c7;">Atención</span>' :
                       tpl.profile_type === 'sales' ? '<span class="status-badge" style="background: rgba(16,185,129,0.15); color: #059669;">Ventas</span>' :
                       '<span class="status-badge">Ambos</span>';

        const formattedBody = escapeHtml(tpl.body || '')
          .replace(/{nombre}/g, '<b style="color: #0284c7;">{nombre}</b>')
          .replace(/{saludo}/g, '<b style="color: #059669;">{saludo}</b>')
          .replace(/{fecha}/g, '<b style="color: #7c3aed;">{fecha}</b>');

        return `
          <tr>
            <td><code style="font-weight: 700; color: var(--accent);">${escapeHtml(tpl.shortcut || '')}</code></td>
            <td style="font-weight: 600;">${escapeHtml(tpl.title || '')}</td>
            <td>${pBadge}</td>
            <td style="max-width: 380px; font-size: 12px; line-height: 1.4;">${formattedBody}</td>
            <td style="text-align: right; white-space: nowrap;">
              <button class="copilot-copy-btn" onclick="copyTemplateDirect('${escapeHtml(tpl.body || '').replace(/'/g, "\'")}')" title="Copiar al portapapeles">📋</button>
              <button class="copilot-copy-btn" onclick="openEditTemplateDialog(${tpl.id})" title="Editar plantilla">✏️</button>
              <button class="copilot-copy-btn" onclick="deleteTemplate(${tpl.id})" title="Eliminar plantilla" style="color: #ef4444;">🗑️</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function filterTemplatesView(filterType, btnEl) {
      document.querySelectorAll('.templates-filter-pills .filter-pill').forEach(b => b.classList.remove('active'));
      if (btnEl) btnEl.classList.add('active');

      if (filterType === 'all') {
        renderTemplatesWorkspaceTable(allWorkspaceTemplates);
      } else {
        const filtered = allWorkspaceTemplates.filter(t => t.profile_type === filterType || t.profile_type === 'both');
        renderTemplatesWorkspaceTable(filtered);
      }
    }

    async function loadAllWorkspaceTags() {
      const container = document.getElementById('allTagsContainer');
      if (!container) return;
      try {
        const res = await fetch(withAccount('/api/copilot/tags/all'));
        const data = await res.json();
        if (data && data.tags) {
          if (data.tags.length === 0) {
            container.innerHTML = '<span style="color: var(--text-muted); font-size: 13px;">No hay etiquetas registradas en ningún contacto aún.</span>';
            return;
          }
          container.innerHTML = data.tags.map(t => `
            <div class="tag-badge-pill" onclick="filterByTag('${escapeHtml(t.tag)}')" title="Filtrar conversaciones con la etiqueta '${escapeHtml(t.tag)}'">
              <span>🏷️ ${escapeHtml(t.tag)}</span>
              <span class="tag-badge-count" title="${t.count} chat(s)">${t.count}</span>
            </div>
          `).join('');
        }
      } catch (e) {
        container.innerHTML = '<span style="color: var(--text-muted); font-size: 13px;">No se pudieron cargar etiquetas.</span>';
      }
    }

    function openNewTemplateDialog() {
      document.getElementById('templateModalTitle').textContent = 'Nueva Respuesta Rápida';
      document.getElementById('tplEditId').value = '';
      document.getElementById('tplEditShortcut').value = '/';
      document.getElementById('tplEditTitle').value = '';
      document.getElementById('tplEditProfile').value = 'both';
      document.getElementById('tplEditBody').value = '';
      updateTemplateLivePreview();
      const modal = document.getElementById('template-modal');
      if (modal) modal.style.display = 'flex';
      setTimeout(() => {
        const sInput = document.getElementById('tplEditShortcut');
        if (sInput) sInput.focus();
      }, 50);
    }

    function openEditTemplateDialog(id) {
      const tpl = allWorkspaceTemplates.find(t => t.id === id);
      if (!tpl) return;
      document.getElementById('templateModalTitle').textContent = 'Editar Respuesta Rápida';
      document.getElementById('tplEditId').value = tpl.id;
      document.getElementById('tplEditShortcut').value = tpl.shortcut || '';
      document.getElementById('tplEditTitle').value = tpl.title || '';
      document.getElementById('tplEditProfile').value = tpl.profile_type || 'both';
      document.getElementById('tplEditBody').value = tpl.body || '';
      updateTemplateLivePreview();
      const modal = document.getElementById('template-modal');
      if (modal) modal.style.display = 'flex';
    }

    function closeTemplateDialog() {
      const modal = document.getElementById('template-modal');
      if (modal) modal.style.display = 'none';
    }

    function insertTemplateVariable(varName) {
      const textarea = document.getElementById('tplEditBody');
      if (!textarea) return;
      const start = textarea.selectionStart || 0;
      const end = textarea.selectionEnd || 0;
      const text = textarea.value;
      textarea.value = text.substring(0, start) + varName + text.substring(end);
      textarea.selectionStart = textarea.selectionEnd = start + varName.length;
      textarea.focus();
      updateTemplateLivePreview();
    }

    function updateTemplateLivePreview() {
      const bodyText = (document.getElementById('tplEditBody') || {}).value || '';
      const previewBox = document.getElementById('tplLivePreviewBox');
      if (!previewBox) return;
      if (!bodyText.trim()) {
        previewBox.textContent = 'Escribe en el campo de texto para ver la vista previa...';
        return;
      }
      const now = new Date();
      const hours = now.getHours();
      const saludo = hours < 12 ? 'Buenos días' : (hours < 19 ? 'Buenas tardes' : 'Buenas noches');
      const fecha = now.toLocaleDateString();
      const nombre = 'Juan Pérez';

      let rendered = bodyText
        .replace(/{nombre}/g, nombre)
        .replace(/{saludo}/g, saludo)
        .replace(/{fecha}/g, fecha);

      previewBox.textContent = rendered;
    }

    async function handleSaveTemplateSubmit(event) {
      event.preventDefault();
      const id = document.getElementById('tplEditId').value;
      const shortcut = document.getElementById('tplEditShortcut').value.trim();
      const title = document.getElementById('tplEditTitle').value.trim();
      const profile_type = document.getElementById('tplEditProfile').value;
      const body = document.getElementById('tplEditBody').value.trim();

      if (!shortcut || !title || !body) {
        alert('Por favor completa todos los campos.');
        return;
      }

      try {
        const payload = {
          shortcut: shortcut.startsWith('/') ? shortcut : '/' + shortcut,
          title,
          body,
          profile_type
        };
        if (id) payload.id = parseInt(id, 10);

        const res = await fetch(withAccount('/api/support/templates'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data && data.ok) {
          closeTemplateDialog();
          showToast('✓ Plantilla guardada exitosamente');
          await loadTemplatesWorkspace();
        } else {
          alert('Error al guardar plantilla: ' + (data.error || 'Respuesta inválida'));
        }
      } catch (e) {
        alert('Error al guardar plantilla: ' + e);
      }
    }

    async function deleteTemplate(id) {
      if (!confirm('¿Estás seguro de que deseas eliminar esta plantilla rápida?')) return;
      try {
        const res = await fetch(withAccount('/api/support/templates/delete'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast('✓ Plantilla eliminada');
          await loadTemplatesWorkspace();
        } else {
          alert('Error al eliminar plantilla: ' + (data.error || ''));
        }
      } catch (e) {
        alert('Error al eliminar plantilla: ' + e);
      }
    }

    /* Espacio de Sistema y Red */
    async function loadSystemWorkspace() {
      const profile = currentProfile || 'full';
      document.querySelectorAll('.profile-card-option').forEach(el => el.classList.remove('active'));
      const activeCard = document.getElementById('profCard-' + profile);
      if (activeCard) activeCard.classList.add('active');

      try {
        const res = await fetch(withAccount('/api/sync/status'));
        const statusData = await res.json();
        if (statusData) {
          renderMediaStats(statusData.media || statusData);
        }
      } catch (e) {}

      loadBackupList();
      fetchExtensionPath();
      loadSystemLogs();
      loadNetworkSettings();
    }

    async function loadSystemLogs() {
      const terminal = document.getElementById('systemLogsTerminal');
      if (!terminal) return;
      try {
        const res = await fetch(withAccount('/api/logs'));
        const data = await res.json();
        if (data && data.logs) {
          systemLogsLines = data.logs;
          renderSystemLogs();
        }
      } catch (e) {
        terminal.innerHTML = '<div style="color: #ef4444; padding: 10px;">Error al cargar registros del servidor.</div>';
      }
    }

    function renderSystemLogs() {
      const terminal = document.getElementById('systemLogsTerminal');
      if (!terminal) return;
      const filterInput = document.getElementById('logsFilterInput');
      const q = filterInput ? filterInput.value.trim().toLowerCase() : '';

      let lines = systemLogsLines;
      if (q) {
        lines = lines.filter(l => l.toLowerCase().includes(q));
      }

      if (lines.length === 0) {
        terminal.innerHTML = '<div style="color: var(--text-muted); padding: 10px;">No hay registros coincidentes.</div>';
        return;
      }

      terminal.innerHTML = lines.map(line => {
        let coloredLine = escapeHtml(line);
        if (line.includes('[Servidor]')) {
          coloredLine = `<span style="color: #38bdf8;">${coloredLine}</span>`;
        } else if (line.includes('[Update Error]') || line.includes('[Error]')) {
          coloredLine = `<span style="color: #f87171; font-weight: bold;">${coloredLine}</span>`;
        } else if (line.includes('[OK]') || line.includes('guardados')) {
          coloredLine = `<span style="color: #4ade80;">${coloredLine}</span>`;
        } else {
          coloredLine = `<span style="color: #cbd5e1;">${coloredLine}</span>`;
        }
        return coloredLine;
      }).join('\n');

      const autoScrollCheck = document.getElementById('logsAutoScrollCheck');
      if (autoScrollCheck && autoScrollCheck.checked) {
        terminal.scrollTop = terminal.scrollHeight;
      }
    }

    function filterSystemLogs() {
      renderSystemLogs();
    }

    async function triggerProcessMedia() {
      try {
        const res = await fetch(withAccount('/api/media/process'), { method: 'POST' });
        const data = await res.json();
        showToast('✓ Procesamiento de multimedia iniciado en segundo plano');
      } catch (e) {
        showToast('Error al iniciar procesamiento de multimedia');
      }
    }

    // Auto-refresco periódico de logs en vivo
    setInterval(() => {
      if (currentWorkspaceView === 'system') {
        loadSystemLogs();
      }
    }, 5000);


    // ==========================================
    // MODO EQUIPO Y COLABORACIÓN LAN (FASE 4)
    // ==========================================

    function setKanbanAssignedFilter(filter) {
      currentKanbanAssignedFilter = filter;
      ['all', 'mine', 'unassigned'].forEach(f => {
        const btn = document.getElementById('btnKanbanAssign-' + f);
        if (btn) btn.classList.toggle('active', f === filter);
      });
      loadKanbanBoard(activeKanbanType);
    }

    // Bitácora de equipo
    function renderCopilotNotesLog(logItems) {
      const container = document.getElementById('copilotNotesLogContainer');
      if (!container) return;
      if (!logItems || logItems.length === 0) {
        container.innerHTML = '<div style="font-size: 11px; color: var(--text-muted); text-align: center; padding: 8px 0;">Sin notas en la bitácora aún.</div>';
        return;
      }
      container.innerHTML = logItems.map(item => {
        const author = escapeHtml(item.author || 'Operador');
        const timeStr = item.created_at ? (formatLocalDate(item.created_at) + ' ' + formatLocalTime(item.created_at)) : '';
        const content = escapeHtml(item.content || '');
        const noteId = item.id;
        return `
          <div class="note-log-item" id="note-item-${noteId}">
            <div class="note-log-header">
              <span class="note-log-author">👤 ${author}</span>
              <span class="note-log-time">${timeStr}</span>
              <button type="button" class="btn-note-delete" onclick="deleteCopilotNoteLog(${noteId})" title="Eliminar nota">×</button>
            </div>
            <div class="note-log-body">${content}</div>
          </div>
        `;
      }).join('');
    }

    async function handleNoteLogKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const input = document.getElementById('copilotNoteLogInput');
        if (!input) return;
        const content = input.value.trim();
        if (!content || !selectedChatId) return;

        try {
          const res = await fetch('/api/copilot/notes/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              account_id: currentAccountId,
              chat_id: selectedChatId,
              author: myOperatorName,
              content: content
            })
          });
          const data = await res.json();
          if (data && data.ok) {
            input.value = '';
            if (data.notes_log) {
              renderCopilotNotesLog(data.notes_log);
            } else {
              loadChatCopilotContext(selectedChatId);
            }
          }
        } catch (err) {
          console.error('Error agregando nota a bitácora:', err);
        }
      }
    }

    async function deleteCopilotNoteLog(noteId) {
      if (!confirm('¿Eliminar esta nota de la bitácora?')) return;
      try {
        const res = await fetch('/api/copilot/notes/log/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: currentAccountId,
            chat_id: selectedChatId,
            note_id: noteId
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          if (data.notes_log) {
            renderCopilotNotesLog(data.notes_log);
          } else {
            loadChatCopilotContext(selectedChatId);
          }
        }
      } catch (err) {
        console.error('Error eliminando nota de bitácora:', err);
      }
    }

    // Asignación de responsable
    async function saveCopilotAssignment() {
      const sel = document.getElementById('copilotAssignedSelect');
      if (!sel || !selectedChatId) return;
      const assignedTo = sel.value;

      try {
        const res = await fetch('/api/copilot/assignment', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_id: currentAccountId,
            chat_id: selectedChatId,
            assigned_to: assignedTo,
            updated_by: myOperatorName
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast('✓ Responsable asignado: ' + (assignedTo || 'Sin asignar'));
        }
      } catch (err) {
        console.error('Error asignando responsable:', err);
      }
    }

    // Semáforo Anti-Colisión y Presencia
    async function sendPresenceHeartbeat(chatId) {
      if (!chatId) return;
      try {
        await _origFetch('/api/presence/heartbeat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(myOfficePin ? { 'X-WAckup-Pin': myOfficePin } : {}),
            ...(myOperatorName ? { 'X-WAckup-Operator': myOperatorName } : {})
          },
          body: JSON.stringify({
            chat_id: chatId,
            client_id: myClientId,
            operator: myOperatorName
          })
        });
      } catch (e) {}
    }

    function startPresenceHeartbeat(chatId) {
      if (presenceHeartbeatTimer) clearInterval(presenceHeartbeatTimer);
      sendPresenceHeartbeat(chatId);
      presenceHeartbeatTimer = setInterval(() => {
        if (selectedChatId === chatId) {
          sendPresenceHeartbeat(chatId);
        }
      }, 12000);
    }

    function stopPresenceHeartbeat() {
      if (presenceHeartbeatTimer) {
        clearInterval(presenceHeartbeatTimer);
        presenceHeartbeatTimer = null;
      }
      if (selectedChatId) {
        try {
          _origFetch('/api/presence/leave', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(myOfficePin ? { 'X-WAckup-Pin': myOfficePin } : {}),
              ...(myOperatorName ? { 'X-WAckup-Operator': myOperatorName } : {})
            },
            body: JSON.stringify({
              chat_id: selectedChatId,
              client_id: myClientId
            })
          });
        } catch (e) {}
      }
    }

    function updatePresenceBanners() {
      const chatBanner = document.getElementById('chatPresenceBanner');
      const drawerBanner = document.getElementById('drawerPresenceBanner');
      const chatUserSpan = document.getElementById('chatPresenceUser');
      const drawerUserSpan = document.getElementById('drawerPresenceUser');

      if (!selectedChatId || !activePresenceMap) {
        if (chatBanner) chatBanner.style.display = 'none';
        if (drawerBanner) drawerBanner.style.display = 'none';
        return;
      }

      const candidateIds = (selectedChatAssociatedIds && selectedChatAssociatedIds.length > 0)
        ? selectedChatAssociatedIds
        : [selectedChatId];

      const otherOperators = [];
      candidateIds.forEach(cid => {
        const entry = activePresenceMap[cid];
        if (!entry) return;
        const viewers = Array.isArray(entry)
          ? entry
          : (Array.isArray(entry.viewers) ? entry.viewers : [entry]);
        viewers.forEach(o => {
          if (!o) return;
          const isMe = (o.client_id && o.client_id === myClientId) ||
            (myOperatorName && myOperatorName !== 'Operador' && o.operator === myOperatorName);
          if (!isMe && o.operator) {
            otherOperators.push(o.operator);
          }
        });
      });

      const uniqueOperators = [...new Set(otherOperators)];

      if (uniqueOperators.length > 0) {
        const text = uniqueOperators.join(', ');
        if (chatUserSpan) chatUserSpan.textContent = text;
        if (drawerUserSpan) drawerUserSpan.textContent = text;
        if (chatBanner) chatBanner.style.display = 'flex';
        if (drawerBanner) drawerBanner.style.display = 'flex';
      } else {
        if (chatBanner) chatBanner.style.display = 'none';
        if (drawerBanner) drawerBanner.style.display = 'none';
      }
    }

    // SSE EventSource en tiempo real
    function connectSSE() {
      if (sseEventSource) {
        try { sseEventSource.close(); } catch (e) {}
        sseEventSource = null;
      }

      const sseUrl = `/api/events?client_id=${encodeURIComponent(myClientId)}&operator=${encodeURIComponent(myOperatorName)}${myOfficePin ? '&pin=' + encodeURIComponent(myOfficePin) : ''}`;

      try {
        sseEventSource = new EventSource(sseUrl);

        sseEventSource.onopen = () => {
          console.log('[SSE] Conectado a canal de eventos en tiempo real');
          stopOfflineReconnectWorker();
          if (wasServerOffline) {
            updateSyncStatus();
          }
        };

        const onEvent = (e) => {
          try {
            const msg = JSON.parse(e.data);
            handleSSEEvent(msg);
          } catch (err) {}
        };

        sseEventSource.onmessage = onEvent;
        sseEventSource.addEventListener('connected', onEvent);
        sseEventSource.addEventListener('presence_update', onEvent);
        sseEventSource.addEventListener('crm_update', onEvent);
        sseEventSource.addEventListener('sync_update', onEvent);
        sseEventSource.addEventListener('profile_update', onEvent);
        sseEventSource.addEventListener('focus_viewer', () => {
          try { window.focus(); } catch (e) {}
          try {
            window.postMessage({ source: 'wackup-viewer', action: 'focus_my_tab' }, '*');
          } catch (e) {}
        });

        sseEventSource.onerror = () => {
          // EventSource reintenta automáticamente; activar sondeo rápido en Worker para que el servidor detecte la pestaña al volver
          wasServerOffline = true;
          startOfflineReconnectWorker();
        };
      } catch (err) {
        console.warn('[SSE] EventSource no disponible:', err);
      }
    }

    // Confirmar al servidor cuando la extensión activó y enfocó esta pestaña exitosamente
    window.addEventListener('message', (ev) => {
      if (ev.source !== window || !ev.data || ev.data.source !== 'wackup-viewer-ext') return;
      if (ev.data.action === 'tab_focused_ok') {
        fetch('/api/viewer/focused', { method: 'POST' }).catch(() => {});
      }
    });

    // Coordinación entre pestañas cuando se abre desde System Tray (?from_tray=1) en navegadores sin extensión
    (function setupSingleViewerTrayChannel() {
      try {
        if (typeof BroadcastChannel === 'undefined') return;
        const bc = new BroadcastChannel('wackup_viewer_channel');
        bc.onmessage = (ev) => {
          if (ev.data && ev.data.type === 'tray_claimed_viewer' && ev.data.clientId !== myClientId) {
            stopOfflineReconnectWorker();
            if (sseEventSource) {
              try { sseEventSource.close(); } catch (e) {}
              sseEventSource = null;
            }
            if (syncIntervalId) {
              clearInterval(syncIntervalId);
              syncIntervalId = null;
            }
            try { window.close(); } catch (e) {}
          }
        };

        const params = new URLSearchParams(window.location.search);
        if (params.get('from_tray') === '1') {
          bc.postMessage({ type: 'tray_claimed_viewer', clientId: myClientId });
          params.delete('from_tray');
          const cleanQs = params.toString();
          const cleanUrl = window.location.pathname + (cleanQs ? '?' + cleanQs : '') + window.location.hash;
          window.history.replaceState(null, '', cleanUrl);
        }
      } catch (e) {}
    })();

    // Notificar inmediatamente al servidor si el usuario cierra la pestaña del Visor Web
    window.addEventListener('pagehide', () => {
      try {
        if (navigator.sendBeacon) {
          const leaveUrl = '/api/presence/leave' + (myOfficePin ? '?pin=' + encodeURIComponent(myOfficePin) : '');
          const payload = JSON.stringify({
            client_id: myClientId,
            chat_id: selectedChatId || null,
            tab_closed: true
          });
          navigator.sendBeacon(leaveUrl, new Blob([payload], { type: 'application/json' }));
        }
      } catch (e) {}
    });

    function handleSSEEvent(msg) {
      if (!msg || !msg.event) return;

      switch (msg.event) {
        case 'connected':
          if (msg.profile && (!msg.account_id || msg.account_id === currentAccountId)) {
            const label = msg.profile_label || msg.profile;
            updateProfileUIElements(msg.profile, label);
            if (typeof availableAccounts !== 'undefined' && availableAccounts) {
              const acc = availableAccounts.find(a => a.id === (msg.account_id || currentAccountId));
              if (acc) {
                acc.workspace_profile = msg.profile;
                acc.workspace_profile_label = label;
              }
            }
          }
        case 'presence_update':
          activePresenceMap = msg.active_presence || msg.presence || {};
          updatePresenceBanners();
          renderTeamOperatorsList();
          break;

        case 'tags_updated': {
          const targetId = msg.chat_id;
          const newTags = (msg.tags || []).map(t => (t && typeof t === 'object') ? t.tag : t);
          if (allChats) {
            const chat = allChats.find(c => c.chat_id === targetId || (c.canonical_id && c.canonical_id === targetId));
            if (chat) {
              chat.tags = newTags;
              const searchInput = document.getElementById('searchInput');
              if (searchInput && searchInput.value.trim()) {
                handleSearchInput();
              } else {
                renderChats(allChats);
              }
            }
          }
          const matchesActive = selectedChatId && (
            selectedChatId === targetId ||
            (selectedChatAssociatedIds && selectedChatAssociatedIds.includes(targetId))
          );
          if (matchesActive) {
            renderCopilotTags(msg.tags || []);
          }
          if (currentWorkspaceView === 'templates') {
            loadAllWorkspaceTags();
          }
          break;
        }

        case 'crm_update': {
          const matchesActiveChat = selectedChatId && (
            !msg.chat_id ||
            msg.chat_id === selectedChatId ||
            (selectedChatAssociatedIds && selectedChatAssociatedIds.includes(msg.chat_id))
          );
          if (matchesActiveChat) {
            if (msg.action === 'note_log' && msg.notes_log) {
              renderCopilotNotesLog(msg.notes_log);
            } else {
              loadChatCopilotContext(selectedChatId);
            }
          }
          if (currentWorkspaceView === 'crm') {
            loadKanbanBoard(activeKanbanType);
          }
          break;
        }

        case 'sync_update':
          if (selectedChatId) {
            refreshActiveChatSilently();
          }
          loadChats();
          break;

        case 'profile_update': {
          if (msg.profile && (!msg.account_id || msg.account_id === currentAccountId)) {
            const label = msg.profile_label || msg.profile;
            updateProfileUIElements(msg.profile, label);
            const acc = accountsList.find(a => a.id === (msg.account_id || currentAccountId));
            if (acc) {
              acc.workspace_profile = msg.profile;
              acc.workspace_profile_label = label;
            }
          }
          break;
        }
      }
    }

    // Configuración de Red LAN y PIN de Oficina
    async function loadNetworkSettings() {
      try {
        const res = await fetch(withAccount('/api/system/network'));
        const data = await res.json();
        if (!data) return;

        const isLan = data.network_mode === 'lan';
        const statusEl = document.getElementById('netModeStatus');
        const btnToggle = document.getElementById('btnToggleNetMode');
        const pinEl = document.getElementById('netPinDisplay');
        const opEl = document.getElementById('myOperatorNameDisplay');
        const ipsList = document.getElementById('lanHostIpsList');

        if (statusEl) {
          statusEl.textContent = isLan ? 'Red Local (0.0.0.0)' : 'Solo esta PC (127.0.0.1)';
          statusEl.style.color = isLan ? '#10b981' : 'var(--text-primary)';
        }
        if (btnToggle) {
          btnToggle.textContent = isLan ? 'Cambiar a Modo Solo PC' : 'Cambiar a Modo Red Local';
        }
        if (pinEl) {
          pinEl.textContent = data.lan_pin || '------';
        }
        if (opEl) {
          opEl.textContent = myOperatorName;
        }

        if (ipsList) {
          const ips = data.host_ips || data.local_ips || [];
          const port = data.port || 8080;
          if (ips.length === 0) {
            ipsList.innerHTML = '<span style="font-size: 12px; color: var(--text-muted);">No se detectaron interfaces de red adicionales.</span>';
          } else {
            ipsList.innerHTML = ips.map(ip => `
              <div style="display: flex; align-items: center; justify-content: space-between; background: var(--bg-card); padding: 6px 10px; border-radius: 6px; font-size: 12px; border: 1px solid var(--border-subtle);">
                <code>http://${escapeHtml(ip)}:${port}</code>
                <button type="button" class="btn-card-action" style="padding: 2px 8px; font-size: 11px;" onclick="navigator.clipboard.writeText('http://${escapeHtml(ip)}:${port}'); showToast('✓ Dirección copiada');">Copiar</button>
              </div>
            `).join('');
          }
        }

        knownOperatorsList = data.known_operators || [];
        const savedOp = localStorage.getItem('wackup_operator_name');
        if ((!savedOp || savedOp === 'Operador' || myOperatorName === 'Operador') && knownOperatorsList.length > 0) {
          myOperatorName = knownOperatorsList[0];
          localStorage.setItem('wackup_operator_name', myOperatorName);
          const opEl = document.getElementById('myOperatorNameDisplay');
          if (opEl) opEl.textContent = myOperatorName;
        }
        updateCopilotOperatorOptions();
        renderTeamOperatorsList();

      } catch (err) {
        console.error('Error al cargar configuración de red:', err);
      }
    }

    function isOperatorOnline(opName) {
      if (!activePresenceMap || !opName) return false;
      const cleanTarget = opName.trim().toLowerCase();
      for (const chatId in activePresenceMap) {
        const entry = activePresenceMap[chatId];
        if (!entry) continue;
        const viewers = Array.isArray(entry) ? entry : (Array.isArray(entry.viewers) ? entry.viewers : [entry]);
        for (const v of viewers) {
          if (v && v.operator && v.operator.trim().toLowerCase() === cleanTarget) {
            return true;
          }
        }
      }
      return false;
    }

    function renderTeamOperatorsList() {
      const tbody = document.getElementById('teamOperatorsTableBody');
      if (!tbody) return;

      if (!knownOperatorsList || knownOperatorsList.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 22px;">
              No hay operadores registrados aún.<br>
              <span style="font-size: 11.5px;">Haz clic en <b>"+ Nuevo Operador"</b> para dar de alta al equipo.</span>
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = knownOperatorsList.map(op => {
        const isThisPc = (myOperatorName && myOperatorName.trim().toLowerCase() === op.trim().toLowerCase());
        const online = isOperatorOnline(op);
        const presenceBadge = online
          ? '<span style="display:inline-flex; align-items:center; gap:5px; font-size:12px; color:#10b981; font-weight:600;"><span style="width:8px; height:8px; border-radius:50%; background:#10b981; display:inline-block;"></span> En línea</span>'
          : '<span style="display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--text-muted);"><span style="width:8px; height:8px; border-radius:50%; background:var(--text-muted); opacity:0.6; display:inline-block;"></span> Inactivo</span>';

        const pcBadge = isThisPc
          ? '<span style="background:rgba(0,168,132,0.15); color:#00a884; border:1px solid rgba(0,168,132,0.3); padding:3px 10px; border-radius:12px; font-size:11.5px; font-weight:600;">✓ Activo en esta PC</span>'
          : `<button type="button" class="btn-card-action" style="padding:3px 10px; font-size:11.5px;" onclick="setThisPcOperator('${escapeHtml(op)}')">Usar aquí</button>`;

        const kickBtn = online
          ? `<button type="button" class="btn-card-action" title="Desconectar sesión y liberar operador" style="padding:4px 8px; font-size:12px; margin-right:4px; color:#f59e0b; border-color:rgba(245,158,11,0.3);" onclick="kickOperatorAdmin('${escapeHtml(op)}')">⚡ Liberar</button>`
          : '';

        return `
          <tr>
            <td style="text-align: center; font-size: 16px;">👤</td>
            <td style="font-weight: 600; color: var(--text-primary); font-size: 13.5px;">${escapeHtml(op)}</td>
            <td>${presenceBadge}</td>
            <td>${pcBadge}</td>
            <td style="text-align: right; white-space: nowrap;">
              ${kickBtn}
              <button type="button" class="btn-card-action" title="Renombrar operador" style="padding:4px 8px; font-size:12px; margin-right:4px;" onclick="openEditOperatorDialog('${escapeHtml(op)}')">✏️</button>
              <button type="button" class="btn-card-action btn-danger-action" title="Eliminar operador" style="padding:4px 8px; font-size:12px; color:#ef4444;" onclick="deleteOperator('${escapeHtml(op)}')">🗑️</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function openAddOperatorDialog() {
      const name = prompt('Nombre o alias del nuevo operador (ej. Carolina):');
      if (!name || !name.trim()) return;
      const cleanName = name.trim();

      if (knownOperatorsList.some(o => o.toLowerCase() === cleanName.toLowerCase())) {
        alert('Ya existe un operador registrado con el nombre "' + cleanName + '".');
        return;
      }

      try {
        const res = await authFetch('/api/system/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ add_operator: cleanName })
        });
        const data = await res.json();
        if (data && data.ok) {
          knownOperatorsList = data.settings?.known_operators || [];
          if (!myOperatorName || myOperatorName === 'Operador') {
            setThisPcOperator(cleanName, false);
          }
          renderTeamOperatorsList();
          updateCopilotOperatorOptions();
          showToast('✓ Operador agregado: ' + cleanName);
        } else {
          showToast(data.error || 'Error al agregar operador');
        }
      } catch (err) {
        showToast('Error de comunicación con el servidor');
      }
    }

    async function openEditOperatorDialog(oldName) {
      const newName = prompt(`Modificar nombre de "${oldName}" a:`, oldName);
      if (!newName || !newName.trim() || newName.trim() === oldName) return;
      const cleanNewName = newName.trim();

      if (knownOperatorsList.some(o => o.toLowerCase() === cleanNewName.toLowerCase() && o.toLowerCase() !== oldName.toLowerCase())) {
        alert('Ya existe otro operador registrado con el nombre "' + cleanNewName + '".');
        return;
      }

      try {
        const res = await authFetch('/api/system/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            rename_operator: { old: oldName, new: cleanNewName }
          })
        });
        const data = await res.json();
        if (data && data.ok) {
          knownOperatorsList = data.settings?.known_operators || [];
          if (myOperatorName && myOperatorName.toLowerCase() === oldName.toLowerCase()) {
            myOperatorName = cleanNewName;
            localStorage.setItem('wackup_operator_name', myOperatorName);
            const opEl = document.getElementById('myOperatorNameDisplay');
            if (opEl) opEl.textContent = myOperatorName;
          }
          renderTeamOperatorsList();
          updateCopilotOperatorOptions();
          showToast(`✓ Operador renombrado a "${cleanNewName}"`);
          if (selectedChatId) sendPresenceHeartbeat(selectedChatId);
        } else {
          showToast(data.error || 'Error al modificar operador');
        }
      } catch (err) {
        showToast('Error de comunicación con el servidor');
      }
    }

    async function deleteOperator(opName) {
      if (!confirm(`¿Estás seguro de que deseas eliminar al operador "${opName}" del equipo?\nYa no aparecerá en el Copiloto ni se podrá seleccionar al ingresar por red.`)) {
        return;
      }

      try {
        const res = await authFetch('/api/system/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ remove_operator: opName })
        });
        const data = await res.json();
        if (data && data.ok) {
          knownOperatorsList = data.settings?.known_operators || [];
          if (myOperatorName && myOperatorName.toLowerCase() === opName.toLowerCase()) {
            myOperatorName = knownOperatorsList.length > 0 ? knownOperatorsList[0] : 'Operador';
            localStorage.setItem('wackup_operator_name', myOperatorName);
            const opEl = document.getElementById('myOperatorNameDisplay');
            if (opEl) opEl.textContent = myOperatorName;
          }
          renderTeamOperatorsList();
          updateCopilotOperatorOptions();
          showToast(`✓ Operador "${opName}" eliminado`);
        } else {
          showToast(data.error || 'Error al eliminar operador');
        }
      } catch (err) {
        showToast('Error de comunicación con el servidor');
      }
    }

    async function kickOperatorAdmin(opName) {
      if (!opName) return;
      if (!confirm(`¿Estás seguro de que deseas forzar la desconexión y liberar la sesión activa de "${opName}"?`)) {
        return;
      }
      try {
        const res = await authFetch('/api/presence/kick', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ operator: opName })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast(`✓ Sesión de "${opName}" liberada con éxito`);
          renderTeamOperatorsList();
        } else {
          showToast(data.error || 'Error al liberar sesión');
        }
      } catch (e) {
        showToast('Error de red al intentar liberar operador');
      }
    }
    window.kickOperatorAdmin = kickOperatorAdmin;

    function setThisPcOperator(opName, notify = true) {
      if (!opName) return;
      myOperatorName = opName;
      localStorage.setItem('wackup_operator_name', myOperatorName);
      const opEl = document.getElementById('myOperatorNameDisplay');
      if (opEl) opEl.textContent = myOperatorName;
      renderTeamOperatorsList();
      updateCopilotOperatorOptions();
      if (notify) showToast(`✓ Operador activo en esta PC: ${myOperatorName}`);
      if (selectedChatId) sendPresenceHeartbeat(selectedChatId);
    }

    function updateCopilotOperatorOptions() {
      const sel = document.getElementById('copilotAssignedSelect');
      if (!sel) return;
      const currentVal = sel.value;
      const ops = [...knownOperatorsList];
      if (myOperatorName && myOperatorName !== 'Operador' && !ops.some(o => o.toLowerCase() === myOperatorName.toLowerCase())) {
        ops.unshift(myOperatorName);
      }

      let html = '<option value="">Sin asignar</option>';
      ops.forEach(op => {
        html += `<option value="${escapeHtml(op)}">${escapeHtml(op)}</option>`;
      });
      sel.innerHTML = html;
      if (currentVal) sel.value = currentVal;
    }

    async function toggleNetworkMode() {
      const statusEl = document.getElementById('netModeStatus');
      const isCurrentlyLan = statusEl && statusEl.textContent.includes('Red Local');
      const nextMode = isCurrentlyLan ? 'local' : 'lan';

      const confirmMsg = nextMode === 'lan'
        ? '¿Deseas activar el Modo Red Local (0.0.0.0)?\nCualquier compañero en la oficina podrá acceder al visor y al copiloto ingresando el PIN.'
        : '¿Deseas volver a Modo Solo PC (127.0.0.1)?\nEl visor solo responderá a peticiones locales de esta computadora.';

      if (!confirm(confirmMsg)) return;

      try {
        const res = await fetch('/api/system/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ network_mode: nextMode })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast('✓ Modo de red actualizado a ' + (nextMode === 'lan' ? 'Red Local' : 'Solo PC'));
          await loadNetworkSettings();
          if (confirm('El modo de red fue actualizado.\n¿Deseas reiniciar el servicio ahora para aplicar la escucha en ' + (nextMode === 'lan' ? '0.0.0.0 (Red Local)' : '127.0.0.1 (Solo esta PC)') + '?')) {
            try { await fetch('/api/system/restart', { method: 'POST' }); } catch (e) {}
            showToast('⚡ Reiniciando servicio...');
          }
        }
      } catch (err) {
        showToast('Error al cambiar modo de red');
      }
    }

    async function promptEditPin() {
      const pinEl = document.getElementById('netPinDisplay');
      const currentPin = pinEl ? pinEl.textContent.trim() : '';
      const newPin = prompt('Nuevo PIN de Oficina (4 a 8 dígitos numéricos):', currentPin !== '------' ? currentPin : '123456');
      if (!newPin) return;

      if (!/^\d{4,8}$/.test(newPin.trim())) {
        alert('El PIN debe contener únicamente entre 4 y 8 dígitos numéricos.');
        return;
      }

      try {
        const res = await fetch('/api/system/network', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lan_pin: newPin.trim() })
        });
        const data = await res.json();
        if (data && data.ok) {
          showToast('✓ PIN de oficina actualizado');
          loadNetworkSettings();
        }
      } catch (err) {
        showToast('Error al actualizar PIN');
      }
    }

    function promptSetMyOperator() {
      if (!knownOperatorsList || knownOperatorsList.length === 0) {
        alert('No hay operadores dados de alta aún.\nPor favor agrega primero operadores en la sección "Operadores y Equipo de Trabajo".');
        const card = document.getElementById('cardTeamOperators');
        if (card) card.scrollIntoView({ behavior: 'smooth' });
        return;
      }

      const listStr = knownOperatorsList.map((op, idx) => `${idx + 1}. ${op}`).join('\n');
      const ans = prompt(`Selecciona el operador de esta PC:\n\n${listStr}\n\nIngresa el número o nombre:`, myOperatorName);
      if (!ans || !ans.trim()) return;

      const num = parseInt(ans.trim(), 10);
      let selected = null;
      if (!isNaN(num) && num >= 1 && num <= knownOperatorsList.length) {
        selected = knownOperatorsList[num - 1];
      } else {
        selected = knownOperatorsList.find(o => o.toLowerCase() === ans.trim().toLowerCase());
      }

      if (selected) {
        setThisPcOperator(selected);
      } else {
        alert('Operador no encontrado en la lista de operadores registrados.');
      }
    }

    async function showLanPinModal(errMsg = '') {
      const modal = document.getElementById('lanPinModal');
      const pinInput = document.getElementById('modalLanPinInput');
      const opSelect = document.getElementById('modalOperatorSelect');
      const errEl = document.getElementById('lanPinErrorMsg');

      if (modal) modal.style.display = 'flex';
      if (pinInput && !pinInput.value) pinInput.value = myOfficePin;

      try {
        const res = await _origFetch('/api/system/network');
        const netData = await res.json();
        if (netData && Array.isArray(netData.known_operators)) {
          knownOperatorsList = netData.known_operators;
        }
      } catch (e) {}

      if (opSelect) {
        if (!knownOperatorsList || knownOperatorsList.length === 0) {
          opSelect.innerHTML = '<option value="">(Sin operadores dados de alta en PC principal)</option>';
        } else {
          opSelect.innerHTML = '<option value="">-- Selecciona tu nombre --</option>' +
            knownOperatorsList.map(op => `<option value="${escapeHtml(op)}">${escapeHtml(op)}</option>`).join('');
          if (myOperatorName && knownOperatorsList.some(o => o.toLowerCase() === myOperatorName.toLowerCase())) {
            opSelect.value = myOperatorName;
          }
        }
      }

      if (errEl) {
        if (errMsg) {
          errEl.textContent = errMsg;
          errEl.style.display = 'block';
        } else {
          errEl.style.display = 'none';
        }
      }
    }

    function hideLanPinModal() {
      const modal = document.getElementById('lanPinModal');
      if (modal) modal.style.display = 'none';
    }

    async function submitLanPinAuth() {
      const pinInput = document.getElementById('modalLanPinInput');
      const opSelect = document.getElementById('modalOperatorSelect');
      const errEl = document.getElementById('lanPinErrorMsg');

      const pin = pinInput ? pinInput.value.trim() : '';
      const op = opSelect ? opSelect.value.trim() : '';

      if (!op) {
        if (errEl) {
          errEl.textContent = 'Por favor selecciona tu nombre de operador de la lista.';
          errEl.style.display = 'block';
        }
        return;
      }

      if (!pin) {
        if (errEl) {
          errEl.textContent = 'Por favor ingresa el PIN de oficina.';
          errEl.style.display = 'block';
        }
        return;
      }

      try {
        const res = await _origFetch('/api/auth/verify_pin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin: pin, operator: op })
        });
        const data = await res.json();
        if (data && data.ok) {
          myOfficePin = pin;
          myOperatorName = op;
          localStorage.setItem('wackup_office_pin', myOfficePin);
          localStorage.setItem('wackup_operator_name', myOperatorName);
          hideLanPinModal();
          showToast('✓ Conectado exitosamente como ' + myOperatorName);
          connectSSE();
          init();
        } else {
          if (errEl) {
            errEl.textContent = data.error || 'PIN incorrecto. Consulta el PIN en la PC principal.';
            errEl.style.display = 'block';
          }
        }
      } catch (err) {
        if (errEl) {
          errEl.textContent = 'Error de conexión con el servidor.';
          errEl.style.display = 'block';
        }
      }
    }

    window.addEventListener('beforeunload', stopPresenceHeartbeat);
