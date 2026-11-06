// WAckup 3.0 - Copiloto CRM y Mini-CRM en Native SidePanel
// Cero inyecciones en el DOM de WhatsApp Web. Interacción 100% pasiva y portapapeles humano.

(function() {
  'use strict';

  let currentAccountId = "default";
  let currentAccountName = "";
  let currentAccountPhone = "";
  let currentChatQuery = null;
  let currentChatId = null;
  let currentProfile = "full";
  const profileLabels = {
    "backup_only": "Solo Respaldo",
    "support": "Soporte",
    "sales": "Ventas",
    "full": "CRM Completo"
  };
  let currentSlaMinutes = null;
  let operatorName = "";
  let currentServerUrl = "http://127.0.0.1:8080";
  let currentOfficePin = "";
  let currentAssociatedIds = [];
  let currentContactDisplayName = "";
  let sseSource = null;
  const clientId = "sp_" + Math.random().toString(36).substring(2, 9);
  let expectedNotesUpdatedAt = 0;
  let activePresenceMap = {};
  let currentAssignFilter = "all";
  let knownOperators = [];

  let notesDebounceTimer = null;
  let pipelineDebounceTimer = null;
  let templatesList = [];
  let currentRadarMode = "unanswered"; // "unanswered" | "sales"
  let latestDiskVersion = null;

  // =========================================================================
  // COMUNICACIÓN CON EL SERVICE WORKER
  // =========================================================================
  function apiFetch(endpoint, options = {}, extra = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        action: "backend_fetch",
        endpoint: endpoint,
        options: options,
        serverUrl: extra.serverUrl,
        pin: extra.pin,
        operator: extra.operator
      }, (res) => {
        if (chrome.runtime.lastError) {
          return reject(new Error(chrome.runtime.lastError.message));
        }
        if (!res || !res.ok) {
          const msg = (res && res.error)
            || (res && res.data && res.data.error)
            || (res && res.status ? `HTTP ${res.status}` : "Error en petición al servidor");
          const err = new Error(msg);
          err.status = res ? res.status : 0;
          err.data = res ? res.data : null;
          err.conflict = Boolean(res && (res.status === 409 || (res.data && res.data.conflict)));
          return reject(err);
        }
        resolve(res.data);
      });
    });
  }

  // =========================================================================
  // FEEDBACK Y PORTAPAPELES
  // =========================================================================
  let toastHideTimer = null;

  function showToast(message, options = {}) {
    const toast = document.getElementById("toast-notification");
    if (!toast) return;

    if (toastHideTimer) {
      clearTimeout(toastHideTimer);
      toastHideTimer = null;
    }

    const hasAction = Boolean(options && options.actionLabel && typeof options.onAction === "function");
    const duration = (options && options.duration) ? options.duration : (hasAction ? 5000 : 2500);

    if (hasAction) {
      toast.classList.add("toast-interactive");
      toast.innerHTML = `
        <span class="toast-msg">${escapeHtml(message)}</span>
        <button type="button" class="toast-undo-btn">${escapeHtml(options.actionLabel)}</button>
      `;
      const btn = toast.querySelector(".toast-undo-btn");
      if (btn) {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (toastHideTimer) {
            clearTimeout(toastHideTimer);
            toastHideTimer = null;
          }
          toast.style.display = "none";
          toast.classList.remove("toast-interactive");
          options.onAction();
        });
      }
    } else {
      toast.classList.remove("toast-interactive");
      toast.textContent = message;
    }

    toast.style.display = hasAction ? "flex" : "block";
    toast.style.animation = "none";
    void toast.offsetWidth; // Reflow
    toast.style.animation = "";

    toastHideTimer = setTimeout(() => {
      toast.style.display = "none";
      toast.classList.remove("toast-interactive");
      toastHideTimer = null;
    }, duration);
  }

  async function copyToClipboard(text, successMsg = "¡Copiado! Pega con Ctrl+V en WhatsApp") {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast(successMsg);
    } catch (err) {
      console.warn("[WAckup] Error al copiar al portapapeles:", err);
    }
  }

  function isHumanContactName(name, jid) {
    if (!name) return false;
    const str = String(name).trim();
    if (!str) return false;
    if (jid && str.toLowerCase() === String(jid).trim().toLowerCase()) return false;
    const lower = str.toLowerCase();
    if (lower.endsWith("@lid") || lower.endsWith("@c.us") || lower.endsWith("@s.whatsapp.net") || lower.endsWith("@g.us") || lower.endsWith("@broadcast")) {
      return false;
    }
    const digitsOnly = str.replace(/\D/g, "");
    if (digitsOnly === str && str.length >= 12) {
      return false;
    }
    return true;
  }

  function formatPhoneNumber(rawPhone) {
    if (!rawPhone) return "";
    const raw = String(rawPhone).split("@")[0].replace(/\D/g, "");
    if (!raw) return "";
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

  // =========================================================================
  // NAVEGACIÓN ENTRE PESTAÑAS
  // =========================================================================
  function setupTabs() {
    const tabButtons = document.querySelectorAll(".tab-btn");
    tabButtons.forEach(btn => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-tab");
        tabButtons.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");

        document.querySelectorAll(".tab-pane").forEach(pane => {
          pane.classList.remove("active");
          if (pane.id === targetId) {
            pane.classList.add("active");
          }
        });

        if (targetId === "tab-radar") {
          loadRadarData();
        }
      });
    });
  }

  // =========================================================================
  // ESTADO DE SINCRONIZACIÓN Y CABECERA (COMPACTA)
  // =========================================================================
  let lastSyncState = "ready";
  let lastSyncMessage = "Conectando...";
  let lastTotalSaved = null;

  function updateHeaderStatus(state, message, totalSaved = null) {
    if (state) lastSyncState = state;
    if (message) lastSyncMessage = message;
    if (totalSaved !== null && totalSaved !== undefined) lastTotalSaved = totalSaved;

    const dot = document.getElementById("header-status-dot");
    const text = document.getElementById("header-status-text");
    const msgs = document.getElementById("header-stats-msgs");

    if (dot) {
      dot.className = "status-dot " + (lastSyncState || "ready");
      const tooltipParts = [];
      if (lastSyncMessage) tooltipParts.push(lastSyncMessage);
      if (lastTotalSaved !== null && lastTotalSaved !== undefined && Number(lastTotalSaved) > 0) {
        tooltipParts.push(`${Number(lastTotalSaved).toLocaleString()} msgs`);
      }
      if (currentAccountPhone) {
        tooltipParts.push(`Cuenta: +${currentAccountPhone}`);
      }
      dot.title = tooltipParts.join(" · ") || "Estado: Listo";
    }
    if (text && message) {
      text.textContent = message;
    }
    if (msgs && totalSaved !== null && totalSaved !== undefined) {
      msgs.textContent = `${totalSaved} msgs`;
    }
  }

  function updateAccountUI(info) {
    if (!info) return;
    const prevId = currentAccountId;
    if (info.id) currentAccountId = info.id;
    if (info.name) currentAccountName = info.name;
    if (info.phone) currentAccountPhone = info.phone;

    const nameEl = document.getElementById("header-account-name");
    const phoneEl = document.getElementById("header-account-phone");

    if (nameEl) nameEl.textContent = currentAccountName || (currentAccountPhone ? `+${currentAccountPhone}` : "Cuenta WhatsApp");
    if (phoneEl) phoneEl.textContent = currentAccountPhone ? `(+${currentAccountPhone})` : "";

    // Actualizar tooltip del punto de estado con los datos de cuenta
    updateHeaderStatus(lastSyncState, lastSyncMessage, lastTotalSaved);

    if (info.id && (prevId === "default" || prevId !== info.id)) {
      loadWorkspaceProfile();
    }
  }

  // =========================================================================
  // PERFIL DE ESPACIO DE TRABAJO (backup_only vs crm)
  // =========================================================================
  async function loadWorkspaceProfile() {
    try {
      const data = await apiFetch(`/api/profile?account_id=${encodeURIComponent(currentAccountId)}`);
      if (data && data.profile) {
        currentProfile = data.profile;
      }
      applyProfileUI();
    } catch (e) {
      console.warn("[WAckup] Error al cargar perfil:", e);
    }
  }

  function applyProfileUI() {
    const badge = document.getElementById("profile-badge");
    const viewBackupOnly = document.getElementById("view-backup-only");
    const viewCrm = document.getElementById("view-crm");
    const secSupport = document.getElementById("sec-support-ticket");
    const secSales = document.getElementById("sec-sales-pipeline");

    if (badge) badge.textContent = profileLabels[currentProfile] || "CRM";

    if (currentProfile === "backup_only") {
      if (viewBackupOnly) viewBackupOnly.style.display = "block";
      if (viewCrm) viewCrm.style.display = "none";
      return;
    }

    if (viewBackupOnly) viewBackupOnly.style.display = "none";
    if (viewCrm) viewCrm.style.display = "flex";

    if (secSupport) {
      secSupport.style.display = (currentProfile === "support" || currentProfile === "full") ? "block" : "none";
    }
    if (secSales) {
      secSales.style.display = (currentProfile === "sales" || currentProfile === "full") ? "block" : "none";
    }
  }

  // =========================================================================
  // CARGA DE CONTEXTO DEL CHAT ACTIVO
  // =========================================================================
  async function loadChatContext(chatQuery) {
    if (!chatQuery) {
      document.getElementById("no-chat-notice").style.display = "block";
      return;
    }
    document.getElementById("no-chat-notice").style.display = "none";
    currentChatQuery = chatQuery;

    try {
      const data = await apiFetch(`/api/copilot/context?chat_id=${encodeURIComponent(chatQuery)}&account_id=${encodeURIComponent(currentAccountId)}`);
      if (!data) return;

      currentChatId = data.chat_id || chatQuery;
      currentAssociatedIds = Array.isArray(data.associated_chat_ids) ? [...data.associated_chat_ids] : [currentChatId];
      if (data.canonical_id && !currentAssociatedIds.includes(data.canonical_id)) {
        currentAssociatedIds.push(data.canonical_id);
      }
      if (!currentAssociatedIds.includes(currentChatId)) {
        currentAssociatedIds.push(currentChatId);
      }

      // Actualizar tarjeta de contacto
      const nameEl = document.getElementById("contact-name");
      const phoneEl = document.getElementById("contact-phone");
      const phoneTextEl = document.getElementById("contact-phone-text");
      const slaEl = document.getElementById("contact-sla");
      const typeIconEl = document.getElementById("contact-type-icon");

      const rawName = data.display_name || data.contact_name;
      const phoneNum = data.phone_number || data.contact_number || "";
      const isHuman = isHumanContactName(rawName, data.chat_id);
      const isGroup = Boolean(data.is_group || (currentChatId && currentChatId.endsWith("@g.us")));
      const isNewsletter = Boolean(data.is_newsletter || (currentChatId && currentChatId.endsWith("@newsletter")));
      const totalMsgs = (data.stats && typeof data.stats.total_messages === "number") ? data.stats.total_messages : 0;

      const displayName = isHuman ? rawName : (phoneNum ? formatPhoneNumber(phoneNum) : (rawName || chatQuery));
      currentContactDisplayName = displayName;

      if (typeIconEl) {
        if (isGroup) {
          typeIconEl.textContent = "👥";
          typeIconEl.title = "Grupo de WhatsApp";
        } else if (isNewsletter) {
          typeIconEl.textContent = "📢";
          typeIconEl.title = "Canal de WhatsApp";
        } else {
          typeIconEl.textContent = "👤";
          typeIconEl.title = "Contacto individual";
        }
      }

      if (nameEl) {
        nameEl.textContent = displayName;
        nameEl.title = displayName;
      }

      if (isGroup) {
        const groupLabel = totalMsgs > 0 ? `👥 Grupo · ${totalMsgs} msgs` : "👥 Grupo de WhatsApp";
        if (phoneTextEl) {
          phoneTextEl.textContent = groupLabel;
        } else if (phoneEl) {
          phoneEl.textContent = groupLabel;
        }
        if (phoneEl) {
          phoneEl.setAttribute("data-phone", currentChatId);
          phoneEl.setAttribute("data-copy-label", `ID de grupo copiado: ${currentChatId}`);
          phoneEl.title = `ID de grupo: ${currentChatId} (Clic para copiar)`;
          phoneEl.classList.add("is-group");
          phoneEl.classList.remove("is-newsletter");
        }
      } else if (isNewsletter) {
        const newsLabel = totalMsgs > 0 ? `📢 Canal · ${totalMsgs} msgs` : "📢 Canal de WhatsApp";
        if (phoneTextEl) {
          phoneTextEl.textContent = newsLabel;
        } else if (phoneEl) {
          phoneEl.textContent = newsLabel;
        }
        if (phoneEl) {
          phoneEl.setAttribute("data-phone", currentChatId);
          phoneEl.setAttribute("data-copy-label", `ID de canal copiado: ${currentChatId}`);
          phoneEl.title = `ID de canal: ${currentChatId} (Clic para copiar)`;
          phoneEl.classList.add("is-newsletter");
          phoneEl.classList.remove("is-group");
        }
      } else {
        const formattedPhone = phoneNum ? formatPhoneNumber(phoneNum) : (data.chat_id || "");
        if (phoneTextEl) {
          phoneTextEl.textContent = formattedPhone || "--";
        } else if (phoneEl) {
          phoneEl.textContent = formattedPhone || "--";
        }
        if (phoneEl) {
          const cleanPhone = phoneNum ? String(phoneNum).replace(/\D/g, "") : formattedPhone;
          phoneEl.setAttribute("data-phone", cleanPhone);
          phoneEl.setAttribute("data-copy-label", `Teléfono copiado: ${cleanPhone}`);
          phoneEl.title = cleanPhone ? `Clic para copiar teléfono: ${cleanPhone}` : "Sin teléfono";
          phoneEl.classList.remove("is-group", "is-newsletter");
        }
      }

      if (slaEl) {
        const slaLabel = data.sla || (data.sla_minutes !== null && data.sla_minutes !== undefined ? `~${data.sla_minutes}m` : null);
        if (slaLabel && !String(slaLabel).toLowerCase().includes("sin datos")) {
          currentSlaMinutes = data.sla_minutes;
          const cleanSla = String(slaLabel).replace(/^SLA:\s*/i, "").trim();
          slaEl.textContent = `⏱️ ${cleanSla}`;
          slaEl.title = `Tiempo promedio de respuesta (SLA): ${cleanSla}`;
          slaEl.style.display = "inline-block";
        } else {
          slaEl.style.display = "none";
        }
      }

      // Limpiar búsqueda anterior del chat
      const searchInput = document.getElementById("input-search-chat");
      const searchClearBtn = document.getElementById("btn-search-clear");
      const searchRes = document.getElementById("chat-search-results");
      const navBar = document.getElementById("search-nav-bar");
      const navPrev = document.getElementById("btn-search-prev");
      const navNext = document.getElementById("btn-search-next");
      if (searchInput) searchInput.value = "";
      if (searchClearBtn) searchClearBtn.style.display = "none";
      if (searchRes) searchRes.innerHTML = "";
      if (navBar) navBar.style.display = "none";
      if (navPrev) navPrev.disabled = true;
      if (navNext) navNext.disabled = true;
      currentSearchResults = [];
      currentSearchIndex = -1;
      currentSearchQuery = "";

      // Perfil si vino en contexto
      if (data.profile && data.profile !== currentProfile) {
        currentProfile = data.profile;
        applyProfileUI();
      }

      // Etiquetas
      renderTags(data.tags || []);

      const crmMeta = data.crm || {};

      // Banner si el contacto fue excluido permanentemente del Radar
      const ignoredBanner = document.getElementById("radar-ignored-banner");
      const isRadarIgnored = Boolean(Number(crmMeta.radar_ignored || data.radar_ignored || 0));
      if (ignoredBanner) {
        ignoredBanner.style.display = isRadarIgnored ? "flex" : "none";
      }

      // Ticket
      const ticketStatus = crmMeta.ticket_status || data.ticket_status || "none";
      document.querySelectorAll(".btn-ticket").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-status") === ticketStatus);
      });

      // Pipeline comercial
      const stageSelect = document.getElementById("select-sales-stage");
      const dealInput = document.getElementById("input-deal-value");
      const followupInput = document.getElementById("input-followup-date");

      const rawStage = crmMeta.sales_stage || data.sales_stage || "none";
      const legacyStageMap = { propuesta: "quoted", ganado: "won", perdido: "lost" };
      const stageVal = legacyStageMap[rawStage] || rawStage;
      const estVal = crmMeta.estimated_value !== undefined ? crmMeta.estimated_value : data.deal_value;
      const followVal = crmMeta.follow_up_date || data.followup_date || "";

      if (stageSelect) stageSelect.value = stageVal;
      document.querySelectorAll(".btn-sales-stage").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-stage") === stageVal);
      });
      if (dealInput) dealInput.value = (estVal && Number(estVal) > 0) ? estVal : "";
      if (followupInput) followupInput.value = followVal;

      // Responsable asignado
      const assignedVal = data.assigned_to || crmMeta.assigned_to || "";
      const assignSel = document.getElementById("select-assigned-to");
      if (assignSel) {
        populateAssigneeOptions(assignSel, assignedVal);
      }

      // Notas privadas (Nota Fijada con bloqueo optimista)
      const notesEl = document.getElementById("contact-notes");
      expectedNotesUpdatedAt = crmMeta.notes_updated_at || crmMeta.updated_at || data.updated_at || 0;
      if (notesEl) {
        notesEl.value = crmMeta.notes !== undefined ? crmMeta.notes : (data.notes || "");
        const statusEl = document.getElementById("notes-status");
        if (statusEl) statusEl.textContent = "Guardado ✓";
      }

      // Bitácora de Equipo (Hilo Rápido)
      renderNotesLog(crmMeta.notes_log || data.notes_log || []);

      // Presencia inmediata al abrir chat
      updatePresenceBanner(activePresenceMap);
      sendPresenceHeartbeat();
    } catch (err) {
      console.warn("[WAckup] Error al cargar contexto de chat:", err);
    }
  }

  // =========================================================================
  // GESTIÓN DE ETIQUETAS
  // =========================================================================
  function renderTags(tags) {
    const container = document.getElementById("tags-list");
    if (!container) return;
    container.innerHTML = "";

    if (!Array.isArray(tags) || tags.length === 0) {
      container.innerHTML = '<span class="empty-subtext">Sin etiquetas</span>';
      return;
    }

    tags.forEach(t => {
      const tagText = (t && typeof t === "object") ? (t.tag || "") : String(t || "");
      if (!tagText) return;
      const pill = document.createElement("span");
      pill.className = "tag-pill";
      pill.innerHTML = `<span>${escapeHtml(tagText)}</span><span class="tag-remove" title="Eliminar etiqueta" data-tag="${escapeHtml(tagText)}">&times;</span>`;
      container.appendChild(pill);
    });

    container.querySelectorAll(".tag-remove").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const tag = btn.getAttribute("data-tag");
        await removeTag(tag);
      });
    });
  }

  async function addTag(tag) {
    if (!tag || !currentChatId) return;
    try {
      await apiFetch("/api/copilot/tags", {
        method: "POST",
        body: {
          account_id: currentAccountId,
          chat_id: currentChatId,
          tag: tag.trim(),
          action: "add"
        }
      });
      showToast(`Etiqueta "${tag}" agregada`);
      loadChatContext(currentChatQuery);
    } catch (e) {
      showToast("Error al agregar etiqueta");
    }
  }

  async function removeTag(tag) {
    if (!tag || !currentChatId) return;
    try {
      await apiFetch("/api/copilot/tags", {
        method: "POST",
        body: {
          account_id: currentAccountId,
          chat_id: currentChatId,
          tag: tag,
          action: "remove"
        }
      });
      showToast(`Etiqueta "${tag}" eliminada`);
      loadChatContext(currentChatQuery);
    } catch (e) {
      showToast("Error al eliminar etiqueta");
    }
  }

  // =========================================================================
  // NOTAS Y PIPELINE (CON DEBOUNCE)
  // =========================================================================
  function setupNotesDebounce() {
    const notesEl = document.getElementById("contact-notes");
    const statusEl = document.getElementById("notes-status");
    if (!notesEl) return;

    notesEl.addEventListener("input", () => {
      if (statusEl) statusEl.textContent = "Guardando...";
      clearTimeout(notesDebounceTimer);
      notesDebounceTimer = setTimeout(async () => {
        if (!currentChatId) return;
        try {
          const res = await apiFetch("/api/copilot/notes", {
            method: "POST",
            body: {
              account_id: currentAccountId,
              chat_id: currentChatId,
              notes: notesEl.value,
              expected_updated_at: expectedNotesUpdatedAt,
              operator_name: operatorName,
              updated_by: operatorName || "Operador"
            }
          });
          if (res && (res.notes_updated_at || res.updated_at)) {
            expectedNotesUpdatedAt = res.notes_updated_at || res.updated_at;
          }
          if (statusEl) statusEl.textContent = "Guardado ✓";
        } catch (e) {
          const errText = (e && e.message) || "";
          if ((e && e.status === 409) || errText.includes("409") || errText.includes("Conflicto") || (e && e.conflict)) {
            if (e && e.data && e.data.current_notes !== undefined) {
              notesEl.value = e.data.current_notes;
              expectedNotesUpdatedAt = e.data.updated_at || expectedNotesUpdatedAt;
            }
            if (statusEl) statusEl.textContent = "⚠️ Conflicto resuelto";
            showToast("⚠️ Conflicto: otro compañero modificó la nota. Refrescando...");
            loadChatContext(currentChatQuery);
          } else {
            if (statusEl) statusEl.textContent = "Error al guardar";
          }
        }
      }, 600);
    });
  }

  // =========================================================================
  // BITÁCORA COLABORATIVA Y ASIGNACIÓN (FASE 4)
  // =========================================================================
  function renderNotesLog(notes) {
    const container = document.getElementById("notes-log-list");
    if (!container) return;
    container.innerHTML = "";
    if (!Array.isArray(notes) || notes.length === 0) {
      container.innerHTML = '<span class="empty-subtext">Sin notas registradas</span>';
      return;
    }
    notes.forEach(n => {
      const entry = document.createElement("div");
      entry.className = "log-entry";
      const d = n.created_at ? new Date(n.created_at * 1000) : new Date();
      const timeStr = `${d.getDate().toString().padStart(2, '0')}/${(d.getMonth()+1).toString().padStart(2, '0')} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
      entry.innerHTML = `
        <div class="log-meta">
          <span class="log-author">${escapeHtml(n.author || 'Operador')}</span>
          <span>${timeStr} <span class="log-del" data-id="${n.id}" title="Eliminar entrada">✕</span></span>
        </div>
        <div class="log-body">${escapeHtml(n.content || '')}</div>
      `;
      const delBtn = entry.querySelector(".log-del");
      if (delBtn) {
        delBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          try {
            await apiFetch("/api/copilot/notes/log/delete", {
              method: "POST",
              body: { id: n.id, note_id: n.id, chat_id: currentChatId, author: operatorName, account_id: currentAccountId }
            });
            loadChatContext(currentChatQuery);
          } catch (err) {
            showToast("Error al eliminar nota");
          }
        });
      }
      container.appendChild(entry);
    });
  }

  function setupNotesLogInput() {
    const input = document.getElementById("input-new-note-log");
    if (!input) return;
    input.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && input.value.trim() && currentChatId) {
        const text = input.value.trim();
        input.value = "";
        try {
          await apiFetch("/api/copilot/notes/log", {
            method: "POST",
            body: {
              account_id: currentAccountId,
              chat_id: currentChatId,
              author: operatorName || "Operador",
              content: text
            }
          });
          showToast("Nota agregada a la bitácora");
          loadChatContext(currentChatQuery);
        } catch (err) {
          showToast("Error al registrar nota");
        }
      }
    });
  }

  function populateAssigneeOptions(sel, currentVal) {
    if (!sel) return;
    sel.innerHTML = '<option value="">Sin asignar</option>';
    const ops = new Set();
    if (operatorName) ops.add(operatorName);
    knownOperators.forEach(op => { if (op) ops.add(op); });

    ops.forEach(op => {
      const opt = document.createElement("option");
      opt.value = op;
      opt.textContent = (op === operatorName) ? `${op} (Yo)` : op;
      if (op === currentVal) opt.selected = true;
      sel.appendChild(opt);
    });

    if (currentVal && !ops.has(currentVal)) {
      const opt = document.createElement("option");
      opt.value = currentVal;
      opt.textContent = currentVal;
      opt.selected = true;
      sel.appendChild(opt);
    }
  }

  function setupContactPhoneCopy() {
    const phoneBtn = document.getElementById("contact-phone");
    if (!phoneBtn) return;
    phoneBtn.addEventListener("click", () => {
      const phoneTextEl = document.getElementById("contact-phone-text");
      const phoneToCopy = phoneBtn.getAttribute("data-phone") || (phoneTextEl ? phoneTextEl.textContent : phoneBtn.textContent).trim();
      const copyLabel = phoneBtn.getAttribute("data-copy-label") || `Copiado: ${phoneToCopy}`;
      if (phoneToCopy && phoneToCopy !== "--" && !phoneToCopy.includes("Cargando")) {
        copyToClipboard(phoneToCopy, copyLabel);
        phoneBtn.classList.add("copied");
        setTimeout(() => {
          phoneBtn.classList.remove("copied");
        }, 1200);
      }
    });
  }

  function setupAssignmentSelect() {
    const sel = document.getElementById("select-assigned-to");
    if (!sel) return;
    sel.addEventListener("change", async () => {
      if (!currentChatId) return;
      const assignedVal = sel.value;
      try {
        await apiFetch("/api/copilot/assignment", {
          method: "POST",
          body: {
            account_id: currentAccountId,
            chat_id: currentChatId,
            assigned_to: assignedVal,
            operator_name: operatorName
          }
        });
        showToast(assignedVal ? `Asignado a ${assignedVal}` : "Desasignado");
      } catch (err) {
        showToast("Error al actualizar asignación");
      }
    });
  }

  async function sendPresenceHeartbeat() {
    if (!currentChatId) return;
    try {
      await apiFetch("/api/presence/heartbeat", {
        method: "POST",
        body: {
          account_id: currentAccountId,
          chat_id: currentChatId,
          operator: operatorName || "Operador",
          client_id: clientId,
          source: "whatsapp_web"
        }
      });
      const res = await apiFetch("/api/presence");
      if (res && (res.active_presence || res.presence)) {
        activePresenceMap = res.active_presence || res.presence;
        updatePresenceBanner(activePresenceMap);
      }
    } catch (e) {}
  }

  function startPresenceHeartbeat() {
    sendPresenceHeartbeat();
    setInterval(sendPresenceHeartbeat, 12000);
    window.addEventListener("beforeunload", () => {
      if (currentChatId) {
        apiFetch("/api/presence/leave", {
          method: "POST",
          body: { chat_id: currentChatId, client_id: clientId }
        }).catch(() => {});
      }
    });
  }

  function connectSidePanelSSE() {
    if (sseSource) {
      try { sseSource.close(); } catch (e) {}
      sseSource = null;
    }
    const baseUrl = (currentServerUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
    const url = `${baseUrl}/api/events?client_id=${encodeURIComponent(clientId)}&operator=${encodeURIComponent(operatorName || "Operador")}${currentOfficePin ? "&pin=" + encodeURIComponent(currentOfficePin) : ""}`;
    try {
      sseSource = new EventSource(url);
      const handleEvt = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (!msg || !msg.event) return;
          if (msg.event === "connected" || msg.event === "presence_update") {
            activePresenceMap = msg.active_presence || msg.presence || {};
            if (msg.active_operator && (!operatorName || operatorName === "Operador")) {
              operatorName = msg.active_operator;
              chrome.runtime.sendMessage({
                action: "save_config",
                config: { operatorName: msg.active_operator }
              });
            }
            updatePresenceBanner(activePresenceMap);
            if (msg.event === "connected" && msg.profile && msg.profile !== currentProfile) {
              currentProfile = msg.profile;
              applyProfileUI();
            }
          } else if (msg.event === "active_operator_updated" && msg.active_operator) {
            const newOp = msg.active_operator;
            if (!operatorName || operatorName === "Operador") {
              operatorName = newOp;
              chrome.runtime.sendMessage({
                action: "save_config",
                config: { operatorName: newOp }
              });
              updatePresenceBanner(activePresenceMap);
            }
          } else if (msg.event === "crm_update") {
            const matchesCurrent = currentChatId && (
              !msg.chat_id ||
              msg.chat_id === currentChatId ||
              currentAssociatedIds.includes(msg.chat_id)
            );
            if (matchesCurrent && currentChatQuery) {
              loadChatContext(currentChatQuery);
            }
            const radarPane = document.getElementById("tab-radar");
            if (radarPane && radarPane.classList.contains("active")) {
              loadRadarData();
            }
          } else if (msg.event === "sync_update") {
            const radarPane = document.getElementById("tab-radar");
            if (radarPane && radarPane.classList.contains("active")) {
              loadRadarData();
            }
          } else if (msg.event === "profile_update") {
            if (!msg.account_id || msg.account_id === currentAccountId || currentAccountId === "default") {
              if (msg.profile && msg.profile !== currentProfile) {
                currentProfile = msg.profile;
                applyProfileUI();
                showToast(`Perfil actualizado: ${profileLabels[currentProfile] || currentProfile}`);
              }
            }
          }
        } catch (err) {}
      };
      sseSource.onmessage = handleEvt;
      sseSource.addEventListener("connected", handleEvt);
      sseSource.addEventListener("presence_update", handleEvt);
      sseSource.addEventListener("active_operator_updated", handleEvt);
      sseSource.addEventListener("crm_update", handleEvt);
      sseSource.addEventListener("sync_update", handleEvt);
      sseSource.addEventListener("profile_update", handleEvt);
      sseSource.addEventListener("focus_viewer", () => {
        chrome.runtime.sendMessage({ action: "focus_viewer_tab" }, () => {
          void chrome.runtime.lastError;
        });
      });
      sseSource.onerror = () => {};
    } catch (e) {}
  }

  function updatePresenceBanner(presenceMap) {
    const banner = document.getElementById("presence-banner");
    const textEl = document.getElementById("presence-text");
    if (!banner || !textEl || !currentChatId || !presenceMap) {
      if (banner) banner.style.display = "none";
      return;
    }
    const candidateIds = currentAssociatedIds.length > 0 ? currentAssociatedIds : [currentChatId];
    const otherNames = [];
    candidateIds.forEach(cid => {
      const chatEntry = presenceMap[cid];
      if (!chatEntry) return;
      const viewers = Array.isArray(chatEntry) ? chatEntry : (chatEntry.viewers || [chatEntry]);
      viewers.forEach(v => {
        if (!v) return;
        const op = (v.operator || "").trim();
        const isMe = (v.client_id && v.client_id === clientId) ||
          (operatorName && op && op.toLowerCase() === operatorName.toLowerCase()) ||
          (!op || op.toLowerCase() === "operador");
        if (!isMe && op) {
          otherNames.push(op);
        }
      });
    });
    const uniqueOthers = [...new Set(otherNames)];
    if (uniqueOthers.length > 0) {
      textEl.innerHTML = `<b>${escapeHtml(uniqueOthers.join(", "))}</b> está viendo este chat ahora`;
      banner.style.display = "flex";
    } else {
      banner.style.display = "none";
    }
  }

  function setupAssignFilterButtons() {
    document.querySelectorAll(".btn-assign-filter").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".btn-assign-filter").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentAssignFilter = btn.getAttribute("data-assign") || "all";
        loadRadarData();
      });
    });
  }

  function setupTicketButtons() {
    document.querySelectorAll(".btn-ticket").forEach(btn => {
      btn.addEventListener("click", async () => {
        if (!currentChatId) return;
        const newStatus = btn.getAttribute("data-status");
        document.querySelectorAll(".btn-ticket").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");

        try {
          await apiFetch("/api/support/ticket", {
            method: "POST",
            body: {
              account_id: currentAccountId,
              chat_id: currentChatId,
              ticket_status: newStatus
            }
          });
          showToast(newStatus === "none" ? "Estado de ticket limpiado" : `Ticket marcado como ${newStatus}`);
        } catch (e) {
          showToast("Error al actualizar ticket");
        }
      });
    });
  }

  function setupPipelineInputs() {
    const stageSelect = document.getElementById("select-sales-stage");
    const dealInput = document.getElementById("input-deal-value");
    const followupInput = document.getElementById("input-followup-date");

    const savePipeline = () => {
      clearTimeout(pipelineDebounceTimer);
      pipelineDebounceTimer = setTimeout(async () => {
        if (!currentChatId) return;
        try {
          await apiFetch("/api/sales/pipeline", {
            method: "POST",
            body: {
              account_id: currentAccountId,
              chat_id: currentChatId,
              sales_stage: stageSelect ? stageSelect.value : "none",
              estimated_value: parseFloat(dealInput ? dealInput.value : 0) || 0,
              follow_up_date: followupInput ? followupInput.value || "" : ""
            }
          });
          showToast("Embudo comercial actualizado");
        } catch (e) {
          showToast("Error al guardar embudo");
        }
      }, 500);
    };

    document.querySelectorAll(".btn-sales-stage").forEach(btn => {
      btn.addEventListener("click", () => {
        const stage = btn.getAttribute("data-stage") || "none";
        if (stageSelect) stageSelect.value = stage;
        document.querySelectorAll(".btn-sales-stage").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        savePipeline();
      });
    });

    if (stageSelect) stageSelect.addEventListener("change", () => {
      const stage = stageSelect.value;
      document.querySelectorAll(".btn-sales-stage").forEach(b => {
        b.classList.toggle("active", b.getAttribute("data-stage") === stage);
      });
      savePipeline();
    });
    if (dealInput) dealInput.addEventListener("input", savePipeline);
    if (followupInput) followupInput.addEventListener("change", savePipeline);
  }

  // =========================================================================
  // PLANTILLAS RÁPIDAS (COPIA AL PORTAPAPELES)
  // =========================================================================
  async function loadTemplates() {
    try {
      const data = await apiFetch(`/api/support/templates?account_id=${encodeURIComponent(currentAccountId)}`);
      templatesList = Array.isArray(data) ? data : (data && data.templates ? data.templates : []);
      renderTemplates(templatesList);
    } catch (e) {
      console.warn("[WAckup] Error al cargar plantillas:", e);
    }
  }

  function interpolateTemplateVariables(rawBody) {
    if (!rawBody) return "";
    const firstName = currentContactDisplayName
      ? (String(currentContactDisplayName).trim().split(/\s+/)[0] || "Estimado/a")
      : "Estimado/a";
    const now = new Date();
    const hr = now.getHours();
    const saludo = hr < 12 ? "Buen día" : (hr < 20 ? "Buenas tardes" : "Buenas noches");
    const dd = String(now.getDate()).padStart(2, "0");
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const fecha = `${dd}/${mm}/${now.getFullYear()}`;
    return String(rawBody)
      .replace(/\{nombre\}/gi, firstName)
      .replace(/\{saludo\}/gi, saludo)
      .replace(/\{fecha\}/gi, fecha);
  }

  function renderTemplates(list) {
    const container = document.getElementById("templates-list");
    if (!container) return;
    container.innerHTML = "";

    if (!Array.isArray(list) || list.length === 0) {
      container.innerHTML = '<span class="empty-subtext">No hay plantillas disponibles.</span>';
      return;
    }

    list.forEach(tpl => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "copilot-template-btn";
      card.innerHTML = `
        <span class="tpl-btn-shortcut">${escapeHtml(tpl.shortcut || "")}</span>
        <span class="tpl-btn-title">${escapeHtml(tpl.title || "Plantilla")}</span>
        <span class="tpl-btn-copy">📋 Copiar</span>
      `;
      card.addEventListener("click", () => {
        const rendered = interpolateTemplateVariables(tpl.body || "");
        copyToClipboard(rendered, `Copiado: "${tpl.title}". Pega con Ctrl+V`);
      });
      container.appendChild(card);
    });
  }

  function setupTemplateFilter() {
    const input = document.getElementById("input-filter-templates");
    if (!input) return;
    input.addEventListener("input", () => {
      const q = input.value.toLowerCase().trim();
      if (!q) {
        renderTemplates(templatesList);
        return;
      }
      const filtered = templatesList.filter(t => 
        (t.title && t.title.toLowerCase().includes(q)) || 
        (t.body && t.body.toLowerCase().includes(q))
      );
      renderTemplates(filtered);
    });
  }

  // =========================================================================
  // BÚSQUEDA FTS5 EN CONVERSACIÓN (EN PESTAÑA GESTIÓN)
  // =========================================================================
  let currentSearchResults = [];
  let currentSearchIndex = -1;
  let currentSearchQuery = "";

  function setupChatSearch() {
    const input = document.getElementById("input-search-chat");
    const btn = document.getElementById("btn-search-chat");
    const btnClear = document.getElementById("btn-search-clear");
    const btnPrev = document.getElementById("btn-search-prev");
    const btnNext = document.getElementById("btn-search-next");
    const resultsContainer = document.getElementById("chat-search-results");
    const navBar = document.getElementById("search-nav-bar");
    const navCount = document.getElementById("search-nav-count");

    const clearSearch = () => {
      if (input) input.value = "";
      if (btnClear) btnClear.style.display = "none";
      if (resultsContainer) resultsContainer.innerHTML = "";
      if (navBar) navBar.style.display = "none";
      currentSearchResults = [];
      currentSearchIndex = -1;
      currentSearchQuery = "";
      if (btnPrev) btnPrev.disabled = true;
      if (btnNext) btnNext.disabled = true;
      if (input) input.focus();
    };

    const updateNavUI = () => {
      if (!navBar || !navCount) return;
      if (!currentSearchResults || currentSearchResults.length === 0) {
        navBar.style.display = "none";
        if (btnPrev) btnPrev.disabled = true;
        if (btnNext) btnNext.disabled = true;
        return;
      }
      navBar.style.display = "flex";
      navCount.textContent = `Coincidencia ${currentSearchIndex + 1} de ${currentSearchResults.length}`;
      const hasMultiple = currentSearchResults.length > 1;
      if (btnPrev) btnPrev.disabled = !hasMultiple;
      if (btnNext) btnNext.disabled = !hasMultiple;

      // Resaltar elemento activo en la lista y scrollear suavemente hacia él
      if (resultsContainer) {
        const items = resultsContainer.querySelectorAll(".search-result-item");
        items.forEach((it, idx) => {
          if (idx === currentSearchIndex) {
            it.classList.add("active-match");
            it.scrollIntoView({ behavior: "smooth", block: "nearest" });
          } else {
            it.classList.remove("active-match");
          }
        });
      }
    };

    const navigateMatch = (direction) => {
      if (!currentSearchResults || currentSearchResults.length === 0) return;
      if (direction === "prev" || direction === "up") {
        // Hacia arriba en la lista visual de resultados (coincidencia anterior, hacia índice 0)
        if (currentSearchIndex > 0) {
          currentSearchIndex--;
          updateNavUI();
        } else if (currentSearchResults.length > 1) {
          // Ciclar al final de la lista
          currentSearchIndex = currentSearchResults.length - 1;
          updateNavUI();
        }
      } else if (direction === "next" || direction === "down") {
        // Hacia abajo en la lista visual de resultados (coincidencia siguiente, hacia índice final)
        if (currentSearchIndex < currentSearchResults.length - 1) {
          currentSearchIndex++;
          updateNavUI();
        } else if (currentSearchResults.length > 1) {
          // Ciclar al inicio de la lista
          currentSearchIndex = 0;
          updateNavUI();
        }
      }
    };

    const doSearch = async (forceNew = false) => {
      const q = (input && input.value || "").trim();
      if (!q) {
        clearSearch();
        if (resultsContainer) resultsContainer.innerHTML = '<div class="empty-subtext">Ingresa una palabra o término para buscar.</div>';
        return;
      }

      if (btnClear) btnClear.style.display = "inline-flex";

      // Si es la misma consulta y ya tenemos resultados, Enter navega hacia abajo a la siguiente coincidencia
      if (!forceNew && q.toLowerCase() === currentSearchQuery.toLowerCase() && currentSearchResults.length > 0) {
        navigateMatch("next");
        return;
      }

      currentSearchQuery = q;
      const targetChat = (currentAssociatedIds && currentAssociatedIds.length > 0)
        ? currentAssociatedIds.join(",")
        : (currentChatId || currentChatQuery || "");

      if (!targetChat) {
        if (resultsContainer) resultsContainer.innerHTML = '<div class="empty-subtext">Abre una conversación en WhatsApp Web para buscar en sus mensajes.</div>';
        return;
      }

      if (resultsContainer) resultsContainer.innerHTML = '<div class="empty-subtext">Buscando...</div>';
      try {
        const data = await apiFetch(`/api/search?q=${encodeURIComponent(q)}&chat_id=${encodeURIComponent(targetChat)}&account_id=${encodeURIComponent(currentAccountId)}&limit=100`);
        const matches = Array.isArray(data) ? data : (data && data.results ? data.results : []);
        currentSearchResults = matches;
        currentSearchIndex = matches.length > 0 ? 0 : -1;
        renderSearchResults(matches, q);
        updateNavUI();
      } catch (e) {
        if (resultsContainer) resultsContainer.innerHTML = `<div class="empty-subtext">Error en la búsqueda: ${escapeHtml(e.message || "No se pudo conectar")}</div>`;
        currentSearchResults = [];
        currentSearchIndex = -1;
        updateNavUI();
      }
    };

    if (btn) btn.addEventListener("click", () => doSearch(true));
    if (btnClear) btnClear.addEventListener("click", clearSearch);
    if (btnPrev) btnPrev.addEventListener("click", () => navigateMatch("prev"));
    if (btnNext) btnNext.addEventListener("click", () => navigateMatch("next"));

    if (input) {
      input.addEventListener("input", () => {
        const hasText = (input.value || "").trim().length > 0;
        if (btnClear) btnClear.style.display = hasText ? "inline-flex" : "none";
      });

      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (e.shiftKey) {
            navigateMatch("prev");
          } else {
            doSearch(false);
          }
        } else if (e.key === "Escape") {
          clearSearch();
        }
      });
    }
  }

  function renderSearchResults(results, query) {
    const container = document.getElementById("chat-search-results");
    if (!container) return;
    container.innerHTML = "";

    if (!Array.isArray(results) || results.length === 0) {
      container.innerHTML = '<div class="empty-subtext">No se encontraron mensajes que coincidan en esta conversación.</div>';
      return;
    }

    const infoHeader = document.createElement("div");
    infoHeader.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; font-size:11px; color:var(--text-muted);">
        <span>Se encontraron <strong>${results.length}</strong> mensaje(s):</span>
        <span style="font-size:10px; color:var(--text-dim);">▲ Recientes a antiguos ▼</span>
      </div>
    `;
    container.appendChild(infoHeader);

    results.forEach((m, idx) => {
      const item = document.createElement("div");
      item.className = "search-result-item" + (idx === currentSearchIndex ? " active-match" : "");
      item.dataset.index = idx;

      const sender = m.es_mio ? "Tú" : (m.remitente_nombre || m.sender_name || currentContactDisplayName || "Contacto");
      const dateStr = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "";
      const text = m.contenido || m.snippet || "";
      const displaySnippet = m.snippet || m.contenido || "";
      const rawChatId = m.chat_id || currentChatId || "";

      item.innerHTML = `
        <div class="search-result-meta">
          <strong>${escapeHtml(sender)}</strong>
          <span>${escapeHtml(dateStr)}</span>
        </div>
        <div class="search-result-snippet">${highlightQuery(displaySnippet, query)}</div>
        <div class="search-result-actions">
          <button type="button" class="btn-res-action btn-res-copy" title="Copiar texto al portapapeles">📋 Copiar</button>
          <button type="button" class="btn-res-action btn-res-open" title="Ver conversación completa y contexto en el Visor Web">↗ Ver en Visor</button>
        </div>
      `;

      const copyBtn = item.querySelector(".btn-res-copy");
      if (copyBtn) {
        copyBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          copyToClipboard(text, "Mensaje copiado al portapapeles ✓");
        });
      }

      const openBtn = item.querySelector(".btn-res-open");
      if (openBtn) {
        openBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          openViewer(rawChatId, m.msg_id);
        });
      }

      item.addEventListener("click", () => {
        currentSearchIndex = idx;
        const navCount = document.getElementById("search-nav-count");
        if (navCount) navCount.textContent = `Coincidencia ${idx + 1} de ${results.length}`;
        const btnPrev = document.getElementById("btn-search-prev");
        const btnNext = document.getElementById("btn-search-next");
        const hasMultiple = results.length > 1;
        if (btnPrev) btnPrev.disabled = !hasMultiple;
        if (btnNext) btnNext.disabled = !hasMultiple;
        container.querySelectorAll(".search-result-item").forEach((el, i) => {
          el.classList.toggle("active-match", i === idx);
        });
      });

      container.appendChild(item);
    });

    // Consejo útil al pie sobre scroll en WhatsApp Web para cargar más historial
    const hintFooter = document.createElement("div");
    hintFooter.className = "search-results-footer-hint";
    hintFooter.innerHTML = `
      <span>💡 <em>¿Buscas mensajes más antiguos?</em> Haz scroll hacia arriba en el chat de WhatsApp Web para que descargue el historial previo y se respalde automáticamente.</span>
    `;
    container.appendChild(hintFooter);
  }

  // =========================================================================
  // PESTAÑA 3: RADAR (SIN RESPONDER / SEGUIMIENTOS VENTAS)
  // =========================================================================
  function canDismissRadarItem(item) {
    const assigned = ((item && item.assigned_to) || "").trim();
    if (!assigned) return true;
    const me = (operatorName || "").trim();
    return Boolean(me) && assigned.toLowerCase() === me.toLowerCase();
  }

  function closeAllRadarReasonMenus(exceptEl = null) {
    document.querySelectorAll(".radar-item.menu-open").forEach(card => {
      if (card === exceptEl) return;
      card.classList.remove("menu-open");
      const menu = card.querySelector(".radar-reason-menu");
      if (menu) menu.style.display = "none";
      const moreBtn = card.querySelector(".btn-radar-more");
      if (moreBtn) {
        moreBtn.classList.remove("active");
        moreBtn.textContent = "⋯";
      }
      const salesBtn = card.querySelector(".btn-radar-sales-act");
      if (salesBtn) {
        salesBtn.classList.remove("active");
      }
    });
  }

  async function executeRadarDismiss(item, mode, reason, cardEl, toastSummary) {
    if (!item || !item.chat_id) return;
    if (!canDismissRadarItem(item)) {
      showToast(`🔒 Este chat está asignado a ${item.assigned_to}`);
      return;
    }

    if (cardEl) {
      cardEl.classList.add("dismissing");
    }

    try {
      const res = await apiFetch("/api/radar/dismiss", {
        method: "POST",
        body: {
          account_id: currentAccountId,
          chat_id: item.chat_id,
          mode: mode,
          reason: reason,
          operator_name: operatorName
        }
      });

      await loadRadarData();
      if (currentChatId && (currentChatId === item.chat_id || currentChatId === res.chat_id || currentAssociatedIds.includes(item.chat_id))) {
        if (currentChatQuery) loadChatContext(currentChatQuery);
      }

      const prevState = res && res.prev_state ? res.prev_state : null;
      const targetChatId = (res && res.chat_id) || item.chat_id;

      if (prevState) {
        showToast(toastSummary || "Chat descartado del Radar", {
          actionLabel: "Deshacer",
          duration: 5000,
          onAction: async () => {
            try {
              await apiFetch("/api/radar/undo", {
                method: "POST",
                body: {
                  account_id: currentAccountId,
                  chat_id: targetChatId,
                  prev_state: prevState,
                  operator_name: operatorName
                }
              });
              showToast("↩️ Acción deshecha");
              await loadRadarData();
              if (currentChatId && (currentChatId === targetChatId || currentAssociatedIds.includes(targetChatId))) {
                if (currentChatQuery) loadChatContext(currentChatQuery);
              }
            } catch (undoErr) {
              showToast(undoErr.message || "No se pudo deshacer");
            }
          }
        });
      } else {
        showToast(toastSummary || "Chat actualizado");
      }
    } catch (err) {
      if (cardEl) {
        cardEl.classList.remove("dismissing");
      }
      showToast(err.message || "No se pudo descartar el chat");
    }
  }

  function setupRadarIgnoredBanner() {
    const btnReactivate = document.getElementById("btn-reactivate-radar");
    if (!btnReactivate) return;
    btnReactivate.addEventListener("click", async () => {
      if (!currentChatId) return;
      try {
        await apiFetch("/api/radar/dismiss", {
          method: "POST",
          body: {
            account_id: currentAccountId,
            chat_id: currentChatId,
            mode: "unanswered",
            reason: "reactivate_radar",
            operator_name: operatorName
          }
        });
        const banner = document.getElementById("radar-ignored-banner");
        if (banner) banner.style.display = "none";
        showToast("🔔 Contacto reactivado en el Radar");
        if (currentChatQuery) loadChatContext(currentChatQuery);
        loadRadarData();
      } catch (err) {
        showToast(err.message || "Error al reactivar en el Radar");
      }
    });
  }

  function setupRadarTabs() {
    const btnUnanswered = document.getElementById("btn-radar-unanswered");
    const btnSales = document.getElementById("btn-radar-sales");

    if (btnUnanswered) {
      btnUnanswered.addEventListener("click", () => {
        currentRadarMode = "unanswered";
        btnUnanswered.classList.add("active");
        if (btnSales) btnSales.classList.remove("active");
        loadRadarData();
      });
    }

    if (btnSales) {
      btnSales.addEventListener("click", () => {
        currentRadarMode = "sales";
        btnSales.classList.add("active");
        if (btnUnanswered) btnUnanswered.classList.remove("active");
        loadRadarData();
      });
    }

    // Cerrar menús de motivos de descarte al hacer clic fuera o presionar Escape
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".radar-item")) {
        closeAllRadarReasonMenus();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeAllRadarReasonMenus();
      }
    });
  }

  async function loadRadarData() {
    const container = document.getElementById("radar-items-container");
    if (!container) return;
    container.innerHTML = '<div class="empty-subtext">Cargando radar...</div>';

    try {
      if (currentRadarMode === "unanswered") {
        const data = await apiFetch(`/api/support/unanswered?account_id=${encodeURIComponent(currentAccountId)}&days=7`);
        const items = Array.isArray(data) ? data : (data && (data.chats || data.unanswered) ? (data.chats || data.unanswered) : []);

        const countBadge = document.getElementById("count-unanswered");
        if (countBadge) countBadge.textContent = items.length;

        const tabBadge = document.getElementById("radar-count-badge");
        if (tabBadge) {
          tabBadge.textContent = items.length;
          tabBadge.style.display = items.length > 0 ? "inline-block" : "none";
        }

        // Notificar al background para el Badge de la barra del navegador
        chrome.runtime.sendMessage({ action: "update_unanswered_count", count: items.length }).catch(() => {});

        renderRadarUnanswered(items);
      } else {
        const data = await apiFetch(`/api/sales/radar?account_id=${encodeURIComponent(currentAccountId)}`);
        const items = Array.isArray(data) ? data : (data && (data.leads || data.radar) ? (data.leads || data.radar) : []);
        
        const countBadge = document.getElementById("count-sales");
        if (countBadge) countBadge.textContent = items.length;

        renderRadarSales(items);
      }
    } catch (e) {
      container.innerHTML = '<div class="empty-subtext">Error al cargar radar.</div>';
    }
  }

  function renderRadarUnanswered(items) {
    const container = document.getElementById("radar-items-container");
    if (!container) return;
    container.innerHTML = "";

    let filtered = items || [];
    if (currentAssignFilter === "mine") {
      filtered = filtered.filter(it => it.assigned_to && it.assigned_to.toLowerCase() === (operatorName || "").toLowerCase());
    } else if (currentAssignFilter === "unassigned") {
      filtered = filtered.filter(it => !it.assigned_to);
    }

    // Deduplicación defensiva
    const seen = new Set();
    filtered = filtered.filter(it => {
      const key = it.chat_id || it.display_name;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    if (!filtered || filtered.length === 0) {
      container.innerHTML = '<div class="empty-subtext">🎉 ¡Excelente! No tienes mensajes pendientes de respuesta (últimos 7 días).</div>';
      return;
    }

    filtered.forEach(item => {
      const el = document.createElement("div");
      el.className = "radar-item";
      const rawName = item.display_name || item.nombre || item.contact_name;
      const phone = item.phone_number;
      const isHuman = isHumanContactName(rawName, item.chat_id);
      const name = isHuman ? rawName : (phone ? formatPhoneNumber(phone) : (rawName || item.chat_id));
      const copyQuery = (isHuman && rawName) ? rawName : (phone ? String(phone).replace(/\D/g, "") : (name || item.chat_id));
      const snippet = item.last_message || item.ultimo_mensaje || "(Mensaje multimedia/vacío)";
      const waitTime = item.waiting_str || (item.minutos_espera ? `Hace ${item.minutos_espera}m` : (item.espera || ""));
      const assignBadge = item.assigned_to ? `<span class="badge-assign" style="font-size:10px; color:#38bdf8;">👤 ${escapeHtml(item.assigned_to)}</span>` : "";
      const canDismiss = canDismissRadarItem(item);

      const actionsHtml = canDismiss
        ? `
          <button type="button" class="btn-radar-dismiss" title="Descartar pendiente (No requiere respuesta)">✓</button>
          <button type="button" class="btn-radar-more" title="Más opciones de descarte o exclusión">⋯</button>
        `
        : `<button type="button" class="btn-radar-locked" title="Asignado a ${escapeHtml(item.assigned_to)} — Solo su responsable puede descartarlo">🔒</button>`;

      el.innerHTML = `
        <div class="radar-item-top">
          <span class="radar-item-name" title="${escapeHtml(name)}">${escapeHtml(name)} ${assignBadge}</span>
          <div class="radar-item-right">
            <span class="radar-item-time">${escapeHtml(waitTime)}</span>
            <span class="radar-item-actions">${actionsHtml}</span>
          </div>
        </div>
        <div class="radar-item-snippet">${escapeHtml(snippet)}</div>
        ${canDismiss ? `
        <div class="radar-reason-menu" style="display:none;">
          <div class="radar-reason-label">Descartar con motivo (se registra en bitácora):</div>
          <div class="radar-reason-grid">
            <button type="button" class="btn-radar-reason success" data-reason="no_reply_needed" data-toast="✓ Descartado (No requiere respuesta)">👍 No requiere resp.</button>
            <button type="button" class="btn-radar-reason" data-reason="resolved_other_channel" data-toast="📞 Descartado (Atendido por otra vía)">📞 Otra vía</button>
            <button type="button" class="btn-radar-reason" data-reason="ignore_personal" data-toast="🏠 Contacto excluido (Personal)">🏠 Personal / Ignorar</button>
            <button type="button" class="btn-radar-reason danger" data-reason="ignore_spam" data-toast="🗑️ Contacto excluido (Spam)">🗑️ Spam / Equiv.</button>
          </div>
        </div>
        ` : ""}
      `;

      const quickBtn = el.querySelector(".btn-radar-dismiss");
      if (quickBtn) {
        quickBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          executeRadarDismiss(item, "unanswered", "no_reply_needed", el, "✓ Descartado (No requiere respuesta)");
        });
      }

      const moreBtn = el.querySelector(".btn-radar-more");
      const menuEl = el.querySelector(".radar-reason-menu");
      if (moreBtn && menuEl) {
        moreBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          const isOpen = menuEl.style.display !== "none";
          closeAllRadarReasonMenus(el);
          if (isOpen) {
            menuEl.style.display = "none";
            el.classList.remove("menu-open");
            moreBtn.classList.remove("active");
            moreBtn.textContent = "⋯";
          } else {
            menuEl.style.display = "block";
            el.classList.add("menu-open");
            moreBtn.classList.add("active");
            moreBtn.textContent = "✕";
            menuEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        });

        menuEl.addEventListener("click", (e) => e.stopPropagation());
        menuEl.querySelectorAll(".btn-radar-reason").forEach(rBtn => {
          rBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const rCode = rBtn.getAttribute("data-reason");
            const rToast = rBtn.getAttribute("data-toast");
            executeRadarDismiss(item, "unanswered", rCode, el, rToast);
          });
        });
      }

      const lockBtn = el.querySelector(".btn-radar-locked");
      if (lockBtn) {
        lockBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          showToast(`🔒 Asignado a ${item.assigned_to} (solo su responsable puede descartarlo)`);
        });
      }

      el.addEventListener("click", () => {
        loadChatContext(item.chat_id || copyQuery);
        copyToClipboard(copyQuery, `Contacto "${copyQuery}" copiado para buscar en WhatsApp Web`);
      });

      container.appendChild(el);
    });
  }

  function renderRadarSales(items) {
    const container = document.getElementById("radar-items-container");
    if (!container) return;
    container.innerHTML = "";

    let filtered = items || [];
    if (currentAssignFilter === "mine") {
      filtered = filtered.filter(it => it.assigned_to && it.assigned_to.toLowerCase() === (operatorName || "").toLowerCase());
    } else if (currentAssignFilter === "unassigned") {
      filtered = filtered.filter(it => !it.assigned_to);
    }

    // Deduplicación defensiva
    const seen = new Set();
    filtered = filtered.filter(it => {
      const key = it.chat_id || it.display_name;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    if (!filtered || filtered.length === 0) {
      container.innerHTML = '<div class="empty-subtext">No hay seguimientos de ventas pendientes hoy.</div>';
      return;
    }

    const stageLabels = {
      lead: "Lead",
      quoted: "Cotiz.",
      propuesta: "Cotiz.",
      contactado: "En conv.",
      negociacion: "Negoc.",
      none: "48h+"
    };

    filtered.forEach(item => {
      const el = document.createElement("div");
      el.className = "radar-item";
      const rawName = item.display_name || item.contact_name;
      const phone = item.phone_number;
      const isHuman = isHumanContactName(rawName, item.chat_id);
      const name = isHuman ? rawName : (phone ? formatPhoneNumber(phone) : (rawName || item.chat_id));
      const copyQuery = (isHuman && rawName) ? rawName : (phone ? String(phone).replace(/\D/g, "") : (name || item.chat_id));
      const rawStage = item.sales_stage || "lead";
      const stage = stageLabels[rawStage] || rawStage;
      const value = item.estimated_value ? `$${item.estimated_value}` : "";
      const assignBadge = item.assigned_to ? `<span class="badge-assign" style="font-size:10px; color:#38bdf8;">👤 ${escapeHtml(item.assigned_to)}</span>` : "";
      const canDismiss = canDismissRadarItem(item);

      const actionsHtml = canDismiss
        ? `<button type="button" class="btn-radar-sales-act" title="Resolver o posponer seguimiento comercial">✓▾</button>`
        : `<button type="button" class="btn-radar-locked" title="Asignado a ${escapeHtml(item.assigned_to)} — Solo su responsable puede resolverlo">🔒</button>`;

      el.innerHTML = `
        <div class="radar-item-top">
          <span class="radar-item-name" title="${escapeHtml(name)}">${escapeHtml(name)} ${assignBadge}</span>
          <div class="radar-item-right">
            <span class="radar-item-time">${escapeHtml(stage)} ${escapeHtml(value)}</span>
            <span class="radar-item-actions">${actionsHtml}</span>
          </div>
        </div>
        <div class="radar-item-snippet">${escapeHtml(item.last_message || item.notes || "Sin notas")}</div>
        ${canDismiss ? `
        <div class="radar-reason-menu" style="display:none;">
          <div class="radar-reason-label">Resolver oportunidad (actualiza embudo y bitácora):</div>
          <div class="radar-reason-grid">
            <button type="button" class="btn-radar-reason success" data-reason="sale_won" data-toast="✅ Marcado como Venta ganada">✅ Venta ganada</button>
            <button type="button" class="btn-radar-reason danger" data-reason="sale_lost" data-toast="❌ Presupuesto descartado (Perdida)">❌ Rechazado / Perd.</button>
            <button type="button" class="btn-radar-reason" data-reason="postpone_3d" data-toast="⏳ Seguimiento pospuesto +3 días">⏳ Posponer +3 días</button>
            <button type="button" class="btn-radar-reason" data-reason="not_a_quote" data-toast="🚫 Quitado de seguimientos">🚫 No era presup.</button>
            <button type="button" class="btn-radar-reason full-col" data-reason="ignore_personal" data-toast="🏠 Contacto excluido permanentemente">🏠 Contacto personal / Ignorar siempre</button>
          </div>
        </div>
        ` : ""}
      `;

      const salesBtn = el.querySelector(".btn-radar-sales-act");
      const menuEl = el.querySelector(".radar-reason-menu");
      if (salesBtn && menuEl) {
        salesBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          const isOpen = menuEl.style.display !== "none";
          closeAllRadarReasonMenus(el);
          if (isOpen) {
            menuEl.style.display = "none";
            el.classList.remove("menu-open");
            salesBtn.classList.remove("active");
          } else {
            menuEl.style.display = "block";
            el.classList.add("menu-open");
            salesBtn.classList.add("active");
            menuEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        });

        menuEl.addEventListener("click", (e) => e.stopPropagation());
        menuEl.querySelectorAll(".btn-radar-reason").forEach(rBtn => {
          rBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const rCode = rBtn.getAttribute("data-reason");
            const rToast = rBtn.getAttribute("data-toast");
            executeRadarDismiss(item, "sales", rCode, el, rToast);
          });
        });
      }

      const lockBtn = el.querySelector(".btn-radar-locked");
      if (lockBtn) {
        lockBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          showToast(`🔒 Asignado a ${item.assigned_to} (solo su responsable puede resolverlo)`);
        });
      }

      el.addEventListener("click", () => {
        loadChatContext(item.chat_id || copyQuery);
        copyToClipboard(copyQuery, `Contacto "${copyQuery}" copiado para buscar en WhatsApp Web`);
      });

      container.appendChild(el);
    });
  }

  // =========================================================================
  // CONFIGURACIÓN DE CONEXIÓN LOCAL / LAN
  // =========================================================================
  function setupConnectionConfig() {
    const toggleBtn = document.getElementById("btn-toggle-settings");
    const closeBtn = document.getElementById("btn-close-settings");
    const panel = document.getElementById("settings-panel");
    const backdrop = document.getElementById("settings-backdrop");
    const saveBtn = document.getElementById("btn-save-cfg");
    const feedback = document.getElementById("cfg-feedback");
    const extraTools = document.getElementById("cfg-extra-tools");

    const checkBtn = document.getElementById("btn-check-server");
    const opSec = document.getElementById("cfg-operator-section");
    const opSelect = document.getElementById("cfg-operator-select");
    const statusBox = document.getElementById("cfg-operator-status-box");
    const statusMsg = document.getElementById("cfg-operator-status-msg");
    const kickBtn = document.getElementById("btn-kick-operator");
    const urlInput = document.getElementById("cfg-server-url");
    const pinInput = document.getElementById("cfg-office-pin");
    let lastOnlineOps = [];

    function updateSaveButtonState() {
      if (!saveBtn) return;
      const selectedOp = opSelect ? opSelect.value.trim() : "";
      saveBtn.disabled = !selectedOp;
    }

    function resetUnverifiedState() {
      if (opSec) opSec.style.display = "none";
      if (saveBtn) {
        saveBtn.style.display = "none";
        saveBtn.disabled = true;
      }
      if (extraTools) extraTools.style.display = "none";
    }

    if (urlInput) {
      urlInput.addEventListener("input", () => {
        resetUnverifiedState();
        if (feedback) feedback.style.display = "none";
      });
      urlInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          checkServerAndLoadOperators(true);
        }
      });
    }

    if (pinInput) {
      pinInput.addEventListener("input", () => {
        resetUnverifiedState();
        if (feedback) feedback.style.display = "none";
      });
      pinInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          checkServerAndLoadOperators(true);
        }
      });
    }

    async function checkServerAndLoadOperators(showFeedback = true) {
      const sUrl = (urlInput ? urlInput.value.trim() : "http://127.0.0.1:8080").replace(/\/+$/, "");
      const sPin = pinInput ? pinInput.value.trim() : "";

      if (checkBtn) {
        checkBtn.disabled = true;
        checkBtn.textContent = "⏳ Conectando...";
      }

      try {
        const data = await apiFetch("/api/auth/verify_pin", {
          method: "POST",
          body: { pin: sPin }
        }, { serverUrl: sUrl, pin: sPin });

        if (!data || !data.ok) {
          resetUnverifiedState();
          if (feedback) {
            feedback.style.display = "block";
            feedback.style.color = "#ef4444";
            feedback.textContent = `❌ ${data?.error || "PIN de oficina incorrecto o no autorizado."}`;
          }
          return false;
        }

        const knownOps = Array.isArray(data.known_operators) ? data.known_operators : [];
        lastOnlineOps = Array.isArray(data.online_operators) ? data.online_operators.map(o => o.toLowerCase()) : [];
        knownOperators = knownOps;

        if (opSelect) {
          opSelect.innerHTML = '<option value="">-- Selecciona tu nombre --</option>';
          if (knownOps.length === 0) {
            const opt = document.createElement("option");
            opt.value = operatorName || "Operador";
            opt.textContent = `👤 ${operatorName || "Operador"} (Predeterminado)`;
            opt.selected = true;
            opSelect.appendChild(opt);
          } else {
            knownOps.forEach(op => {
              const isOnline = lastOnlineOps.includes(op.toLowerCase());
              const isCurrent = (operatorName && operatorName.toLowerCase() === op.toLowerCase());
              const opt = document.createElement("option");
              opt.value = op;
              opt.textContent = isOnline ? `👤 ${op} (En línea)` : `👤 ${op}`;
              if (isCurrent) opt.selected = true;
              opSelect.appendChild(opt);
            });
          }

          opSelect.onchange = () => {
            updateOperatorInUseWarning();
            updateSaveButtonState();
          };
        }

        if (opSec) opSec.style.display = "block";
        if (saveBtn) saveBtn.style.display = "block";
        updateOperatorInUseWarning();
        updateSaveButtonState();

        if (extraTools && operatorName) {
          extraTools.style.display = "block";
        }

        if (feedback && showFeedback) {
          feedback.style.display = "block";
          feedback.style.color = "#10b981";
          feedback.textContent = `✓ Conexión establecida. ${knownOps.length} operador(es) detectados.`;
          setTimeout(() => { if (feedback) feedback.style.display = "none"; }, 4000);
        }

        return true;
      } catch (err) {
        resetUnverifiedState();
        if (feedback) {
          feedback.style.display = "block";
          feedback.style.color = "#ef4444";
          const errDetail = err.message || "Error al conectar";
          feedback.textContent = `❌ Error de conexión: ${errDetail}`;
        }
        return false;
      } finally {
        if (checkBtn) {
          checkBtn.disabled = false;
          checkBtn.textContent = "🔌 Conectar y Cargar Operadores";
        }
      }
    }

    function updateOperatorInUseWarning() {
      if (!opSelect || !statusBox) return;
      const selectedOp = opSelect.value.trim();
      if (!selectedOp) {
        statusBox.style.display = "none";
        return;
      }

      const isOnline = lastOnlineOps.includes(selectedOp.toLowerCase());
      if (isOnline) {
        statusBox.className = "cfg-status-box warn mt-1";
        statusBox.style.display = "block";
        if (statusMsg) {
          statusMsg.innerHTML = `⚠️ <b>${escapeHtml(selectedOp)}</b> ya figura activo en otro equipo.<br>Si eres tú en otra ventana puedes continuar; si quedó colgado puedes liberarlo:`;
        }
        if (kickBtn) {
          kickBtn.style.display = "block";
          kickBtn.onclick = () => kickStuckOperator(selectedOp);
        }
      } else {
        statusBox.style.display = "none";
        if (kickBtn) kickBtn.style.display = "none";
      }
    }

    async function kickStuckOperator(opName) {
      if (!opName) return;
      const sUrl = (urlInput ? urlInput.value.trim() : "http://127.0.0.1:8080").replace(/\/+$/, "");
      const sPin = pinInput ? pinInput.value.trim() : "";

      try {
        const data = await apiFetch("/api/presence/kick", {
          method: "POST",
          body: { operator: opName }
        }, { serverUrl: sUrl, pin: sPin });

        if (data && data.ok) {
          showToast(`✓ Sesión previa de ${opName} liberada`);
          await checkServerAndLoadOperators(false);
        } else {
          showToast(data?.error || "Error al liberar sesión");
        }
      } catch (e) {
        showToast("Error de conexión al liberar sesión");
      }
    }

    if (checkBtn) {
      checkBtn.addEventListener("click", () => checkServerAndLoadOperators(true));
    }

    function openSettings() {
      if (panel) panel.style.display = "block";
      if (backdrop) backdrop.style.display = "block";
      if (toggleBtn) toggleBtn.classList.add("active");

      // Cargar config actual por si cambió en segundo plano
      chrome.runtime.sendMessage({ action: "get_config" }, (res) => {
        if (res && res.config) {
          operatorName = res.config.operatorName || "";
          currentServerUrl = res.config.serverUrl || "http://127.0.0.1:8080";
          currentOfficePin = res.config.officePin || "";

          if (urlInput) urlInput.value = currentServerUrl;
          if (pinInput) pinInput.value = currentOfficePin;

          // Si ya hay un operador guardado previamente en este equipo, verificar automáticamente
          if (operatorName && (currentOfficePin || currentServerUrl.includes("127.0.0.1") || currentServerUrl.includes("localhost"))) {
            checkServerAndLoadOperators(false);
          } else {
            // Cliente nuevo o sin operador seleccionado: mostrar sólo opciones básicas
            resetUnverifiedState();
          }
        } else {
          resetUnverifiedState();
        }
      });
    }

    function closeSettings() {
      if (panel) panel.style.display = "none";
      if (backdrop) backdrop.style.display = "none";
      if (toggleBtn) toggleBtn.classList.remove("active");
    }

    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        const isOpen = panel && panel.style.display !== "none";
        if (isOpen) {
          closeSettings();
        } else {
          openSettings();
        }
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener("click", closeSettings);
    }

    if (backdrop) {
      backdrop.addEventListener("click", closeSettings);
    }

    // Cerrar con Escape si está abierto
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panel && panel.style.display !== "none") {
        closeSettings();
      }
    });

    // Cargar config actual desde el service worker al inicio
    chrome.runtime.sendMessage({ action: "get_config" }, (res) => {
      const prevOp = operatorName;
      if (res && res.config) {
        operatorName = res.config.operatorName || "";
        currentServerUrl = res.config.serverUrl || "http://127.0.0.1:8080";
        currentOfficePin = res.config.officePin || "";

        if (urlInput) urlInput.value = currentServerUrl;
        if (pinInput) pinInput.value = currentOfficePin;
      }
      connectSidePanelSSE();
      if (operatorName !== prevOp) {
        loadRadarData();
      }
    });

    // Cargar operadores conocidos desde la red si es posible
    apiFetch("/api/system/network").then(netData => {
      if (netData) {
        if (Array.isArray(netData.known_operators)) {
          knownOperators = netData.known_operators;
          const sel = document.getElementById("select-assigned-to");
          if (sel) populateAssigneeOptions(sel, sel.value);
        }
        const activeOp = netData.active_operator || (netData.known_operators && netData.known_operators[0]) || "";
        const isLocalHost = !netData.needs_pin && (currentServerUrl.includes("127.0.0.1") || currentServerUrl.includes("localhost"));
        if (isLocalHost && (!operatorName || operatorName === "Operador") && activeOp) {
          operatorName = activeOp;
          chrome.runtime.sendMessage({
            action: "save_config",
            config: { operatorName: activeOp }
          });
        }
      }
    }).catch(() => {});

    if (saveBtn) {
      saveBtn.addEventListener("click", () => {
        const selectedOp = opSelect ? opSelect.value.trim() : "";
        if (!selectedOp) {
          showToast("⚠️ Por favor selecciona tu nombre de operador");
          if (opSelect) opSelect.focus();
          return;
        }

        const newConfig = {
          serverUrl: urlInput ? urlInput.value.trim() : "http://127.0.0.1:8080",
          officePin: pinInput ? pinInput.value.trim() : "",
          operatorName: selectedOp
        };

        operatorName = newConfig.operatorName;
        currentServerUrl = newConfig.serverUrl;
        currentOfficePin = newConfig.officePin;

        chrome.runtime.sendMessage({ action: "save_config", config: newConfig }, () => {
          if (feedback) {
            feedback.style.display = "block";
            feedback.style.color = "#10b981";
            feedback.textContent = `Conectado como ${operatorName} ✓`;
            setTimeout(() => { feedback.style.display = "none"; }, 3500);
          }
          if (extraTools) {
            extraTools.style.display = "block";
          }
          showToast(`✓ Conectado como ${operatorName}`);
          closeSettings();
          connectSidePanelSSE();
          loadWorkspaceProfile();
          loadTemplates();
          loadRadarData();
          if (currentChatQuery) loadChatContext(currentChatQuery);
        });
      });
    }
  }

  // =========================================================================
  // ACCESO DIRECTO AL VISOR WEB
  // =========================================================================
  function openViewer(targetChatId = null, targetMsgId = null) {
    const base = (currentServerUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
    const params = new URLSearchParams();
    if (targetChatId) params.set("chat_id", targetChatId);
    if (targetMsgId) params.set("msg_id", targetMsgId);
    if (currentAccountId && currentAccountId !== "default") params.set("account_id", currentAccountId);
    if (currentOfficePin) params.set("pin", currentOfficePin);
    if (operatorName && operatorName !== "Operador") params.set("operator", operatorName);

    const qs = params.toString();
    const finalUrl = `${base}/index.html${qs ? "?" + qs : ""}`;

    chrome.runtime.sendMessage({
      action: "open_viewer",
      url: finalUrl,
      baseUrl: base
    }, (res) => {
      if (chrome.runtime.lastError || !res || !res.ok) {
        window.open(finalUrl, "_blank");
      }
    });
  }

  // =========================================================================
  // BOTONES RÁPIDOS DE CABECERA Y ACTUALIZACIÓN
  // =========================================================================
  function setupHeaderButtons() {
    const dot = document.getElementById("header-status-dot");
    const btnSyncRecent = document.getElementById("btn-sync-recent");
    const btnSyncAll = document.getElementById("btn-sync-all");
    const btnSyncAllFull = document.getElementById("btn-sync-all-full");
    const btnBackupManual = document.getElementById("btn-backup-manual");
    const btnUpdateExt = document.getElementById("btn-update-ext");
    const btnAddTag = document.getElementById("btn-add-tag");
    const badgeProfile = document.getElementById("profile-badge");
    const btnOpenViewer = document.getElementById("btn-open-viewer");
    const btnOpenViewerCfg = document.getElementById("btn-open-viewer-cfg");
    const btnOpenViewerEmpty = document.getElementById("btn-open-viewer-empty");
    const btnOpenViewerChat = document.getElementById("btn-open-viewer-chat");

    if (btnOpenViewer) {
      btnOpenViewer.addEventListener("click", () => {
        openViewer(currentChatId);
      });
    }

    if (btnOpenViewerCfg) {
      btnOpenViewerCfg.addEventListener("click", () => {
        openViewer(currentChatId);
        const panel = document.getElementById("settings-panel");
        const backdrop = document.getElementById("settings-backdrop");
        const toggleBtn = document.getElementById("btn-toggle-settings");
        if (panel) panel.style.display = "none";
        if (backdrop) backdrop.style.display = "none";
        if (toggleBtn) toggleBtn.classList.remove("active");
      });
    }

    if (btnOpenViewerEmpty) {
      btnOpenViewerEmpty.addEventListener("click", () => {
        openViewer();
      });
    }

    if (btnOpenViewerChat) {
      btnOpenViewerChat.addEventListener("click", () => {
        openViewer(currentChatId);
      });
    }

    if (dot) {
      dot.addEventListener("click", () => {
        showToast(dot.title || `Estado: ${lastSyncMessage}`);
      });
    }

    if (badgeProfile) {
      badgeProfile.style.cursor = "pointer";
      badgeProfile.title = "Clic para cambiar perfil de trabajo (o actualizar)";
      badgeProfile.addEventListener("click", async () => {
        try {
          const res = await apiFetch(`/api/profile/cycle?account_id=${encodeURIComponent(currentAccountId)}`, {
            method: "POST"
          });
          if (res && res.profile) {
            currentProfile = res.profile;
            applyProfileUI();
            showToast(`Perfil: ${profileLabels[currentProfile] || currentProfile}`);
          } else {
            await loadWorkspaceProfile();
            showToast(`Perfil: ${profileLabels[currentProfile] || currentProfile}`);
          }
        } catch (e) {
          await loadWorkspaceProfile();
          showToast(`Perfil: ${profileLabels[currentProfile] || currentProfile}`);
        }
      });
    }

    if (btnSyncRecent) {
      btnSyncRecent.addEventListener("click", (e) => {
        const forceAll = Boolean(e.shiftKey);
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll });
        showToast(forceAll ? "Sincronizando historial completo..." : "Sincronizando novedades...");
      });
    }

    if (btnSyncAll) {
      btnSyncAll.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: true });
        showToast("Sincronizando historial completo...");
      });
    }

    if (btnSyncAllFull) {
      btnSyncAllFull.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: true });
        showToast("Sincronizando historial completo...");
        const panel = document.getElementById("settings-panel");
        const backdrop = document.getElementById("settings-backdrop");
        const toggleBtn = document.getElementById("btn-toggle-settings");
        if (panel) panel.style.display = "none";
        if (backdrop) backdrop.style.display = "none";
        if (toggleBtn) toggleBtn.classList.remove("active");
      });
    }

  // =========================================================================
  // MODALES Y DIÁLOGOS ELEGANTES DE SIDEPANEL
  // =========================================================================
  let spModalResolver = null;
  let spModalValidator = null;

  function closeSidePanelModal(result = null) {
    const overlay = document.getElementById("sp-modal-overlay");
    if (overlay) overlay.style.display = "none";
    if (spModalResolver) {
      const resolve = spModalResolver;
      spModalResolver = null;
      spModalValidator = null;
      resolve(result);
    }
  }

  function confirmSidePanelModal() {
    const overlay = document.getElementById("sp-modal-overlay");
    if (!overlay || overlay.style.display === "none") return;

    const inputGroup = document.getElementById("sp-modal-input-group");
    const inputEl = document.getElementById("sp-modal-input");
    const errorEl = document.getElementById("sp-modal-error");

    let val = true;
    if (inputGroup && inputGroup.style.display !== "none") {
      val = inputEl ? inputEl.value.trim() : "";
      if (spModalValidator) {
        const err = spModalValidator(val);
        if (err) {
          if (errorEl) {
            errorEl.textContent = err;
            errorEl.style.display = "block";
          }
          if (inputEl) inputEl.focus();
          return;
        }
      }
    }

    closeSidePanelModal(val);
  }

  let spModalListenersAttached = false;
  function setupSidePanelModalListeners() {
    if (spModalListenersAttached) return;
    spModalListenersAttached = true;

    const overlay = document.getElementById("sp-modal-overlay");
    const btnClose = document.getElementById("sp-modal-close");
    const btnCancel = document.getElementById("sp-modal-cancel");
    const btnConfirm = document.getElementById("sp-modal-confirm");
    const inputEl = document.getElementById("sp-modal-input");

    if (btnClose) btnClose.addEventListener("click", () => closeSidePanelModal(null));
    if (btnCancel) btnCancel.addEventListener("click", () => closeSidePanelModal(null));
    if (btnConfirm) btnConfirm.addEventListener("click", confirmSidePanelModal);

    if (overlay) {
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeSidePanelModal(null);
      });
    }

    if (inputEl) {
      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          confirmSidePanelModal();
        } else if (e.key === "Escape") {
          e.preventDefault();
          closeSidePanelModal(null);
        }
      });
      inputEl.addEventListener("input", () => {
        const err = document.getElementById("sp-modal-error");
        if (err) err.style.display = "none";
      });
    }

    window.addEventListener("keydown", (e) => {
      const ov = document.getElementById("sp-modal-overlay");
      if (ov && ov.style.display === "flex" && e.key === "Escape") {
        e.preventDefault();
        closeSidePanelModal(null);
      }
    });
  }

  function showSidePanelConfirm({ title = "Confirmar", message = "", confirmText = "Aceptar", cancelText = "Cancelar", isDanger = false }) {
    setupSidePanelModalListeners();
    return new Promise((resolve) => {
      spModalResolver = resolve;
      spModalValidator = null;

      const overlay = document.getElementById("sp-modal-overlay");
      const titleEl = document.getElementById("sp-modal-title");
      const msgEl = document.getElementById("sp-modal-message");
      const inputGroup = document.getElementById("sp-modal-input-group");
      const errorEl = document.getElementById("sp-modal-error");
      const btnCancel = document.getElementById("sp-modal-cancel");
      const btnConfirm = document.getElementById("sp-modal-confirm");

      if (titleEl) titleEl.textContent = title;
      if (msgEl) msgEl.innerHTML = message;
      if (inputGroup) inputGroup.style.display = "none";
      if (errorEl) { errorEl.textContent = ""; errorEl.style.display = "none"; }

      if (btnCancel) {
        btnCancel.style.display = "inline-flex";
        btnCancel.textContent = cancelText;
      }
      if (btnConfirm) {
        btnConfirm.textContent = confirmText;
        btnConfirm.className = isDanger ? "btn-primary btn-modal-danger-sp" : "btn-primary";
      }

      if (overlay) {
        overlay.style.display = "flex";
        setTimeout(() => btnConfirm && btnConfirm.focus(), 50);
      }
    });
  }

  function showSidePanelPrompt({ title = "Ingresar dato", message = "", placeholder = "", defaultValue = "", confirmText = "Aceptar", cancelText = "Cancelar", validator = null }) {
    setupSidePanelModalListeners();
    return new Promise((resolve) => {
      spModalResolver = resolve;
      spModalValidator = validator;

      const overlay = document.getElementById("sp-modal-overlay");
      const titleEl = document.getElementById("sp-modal-title");
      const msgEl = document.getElementById("sp-modal-message");
      const inputGroup = document.getElementById("sp-modal-input-group");
      const inputEl = document.getElementById("sp-modal-input");
      const errorEl = document.getElementById("sp-modal-error");
      const btnCancel = document.getElementById("sp-modal-cancel");
      const btnConfirm = document.getElementById("sp-modal-confirm");

      if (titleEl) titleEl.textContent = title;
      if (msgEl) msgEl.innerHTML = message;
      if (inputGroup) inputGroup.style.display = "block";
      if (inputEl) {
        inputEl.placeholder = placeholder;
        inputEl.value = defaultValue || "";
      }
      if (errorEl) { errorEl.textContent = ""; errorEl.style.display = "none"; }

      if (btnCancel) {
        btnCancel.style.display = "inline-flex";
        btnCancel.textContent = cancelText;
      }
      if (btnConfirm) {
        btnConfirm.textContent = confirmText;
        btnConfirm.className = "btn-primary";
      }

      if (overlay) {
        overlay.style.display = "flex";
        setTimeout(() => {
          if (inputEl) {
            inputEl.focus();
            inputEl.select();
          }
        }, 50);
      }
    });
  }

    if (btnBackupManual) {
      btnBackupManual.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: false });
        showToast("Sincronizando...");
      });
    }

    if (btnUpdateExt) {
      btnUpdateExt.addEventListener("click", async () => {
        const verText = latestDiskVersion ? ` a la versión <b>v${escapeHtml(latestDiskVersion)}</b>` : "";
        const confirmed = await showSidePanelConfirm({
          title: "Actualizar WAckup",
          message: `Nueva versión disponible de WAckup${verText}.<br><br>` +
            `Para aplicar la actualización y reactivar la sincronización, se recargará la extensión y la pestaña de WhatsApp Web automáticamente.<br><br>` +
            `¿Deseas continuar?`,
          confirmText: "Actualizar Ahora"
        });
        if (!confirmed) return;

        showToast("Actualizando y recargando WhatsApp Web...");
        chrome.runtime.sendMessage({ action: "reload_extension", reloadWhatsAppTabs: true });
      });
    }

    if (btnAddTag) {
      btnAddTag.addEventListener("click", async () => {
        const tag = await showSidePanelPrompt({
          title: "Nueva Etiqueta",
          message: "Ingresa el nombre de la etiqueta para este contacto:",
          placeholder: "ej: cliente, vip, proveedor...",
          confirmText: "Agregar",
          validator: (val) => {
            if (!val || !val.trim()) return "El nombre de la etiqueta no puede estar vacío.";
            return null;
          }
        });
        if (tag && tag.trim()) addTag(tag.trim());
      });
    }
  }

  // =========================================================================
  // ESCUCHA DE MENSAJES DESDE BRIDGE / SERVICE WORKER
  // =========================================================================
  function setupMessageListener() {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (!request || typeof request !== "object") return;

      // Cambio de chat activo en WhatsApp Web
      if (request.action === "active_chat_changed") {
        if (request.accountId) {
          updateAccountUI({
            id: request.accountId,
            phone: request.accountPhone,
            name: request.accountName
          });
        }
        if (request.chatQuery) {
          loadChatContext(request.chatQuery);
        }
        sendResponse({ ok: true });
        return;
      }

      // Cambio en estado de sincronización
      if (request.action === "sync_status_changed") {
        updateHeaderStatus(request.state, request.message, request.totalSaved);
        if (request.accountInfo) {
          updateAccountUI(request.accountInfo);
        }
        sendResponse({ ok: true });
        return;
      }

      // Aviso de actualización de extensión
      if (request.action === "extension_version_info") {
        const btnUpdate = document.getElementById("btn-update-ext");
        if (btnUpdate && request.diskVer && request.installedVer && request.diskVer !== request.installedVer) {
          latestDiskVersion = request.diskVer;
          btnUpdate.style.display = "inline-flex";
          btnUpdate.textContent = `🔄 v${request.diskVer}`;
          btnUpdate.title = `Actualizar extensión a v${request.diskVer} (instalada: v${request.installedVer})`;
        }
        sendResponse({ ok: true });
        return;
      }
    });
  }

  // =========================================================================
  // UTILIDADES
  // =========================================================================
  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function highlightQuery(text, query) {
    if (!text) return "";
    // Si el texto ya contiene marcas HTML de resaltado (<mark> de snippet FTS5), preservarlas directamente
    if (text.includes("<mark>") && text.includes("</mark>")) {
      return text;
    }
    const escaped = escapeHtml(text);
    if (!query) return escaped;
    const cleanQ = query.trim();
    if (!cleanQ) return escaped;
    // Escapar caracteres especiales de regex para evitar SyntaxError al buscar signos, precios, etc.
    const escapedRegex = cleanQ.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    try {
      const reg = new RegExp(`(${escapeHtml(cleanQ)}|${escapedRegex})`, "gi");
      return escaped.replace(reg, "<mark>$1</mark>");
    } catch (e) {
      return escaped;
    }
  }

  // =========================================================================
  // INICIALIZACIÓN
  // =========================================================================
  async function init() {
    setupTabs();
    setupHeaderButtons();
    setupNotesDebounce();
    setupNotesLogInput();
    setupContactPhoneCopy();
    setupAssignmentSelect();
    setupTicketButtons();
    setupPipelineInputs();
    setupTemplateFilter();
    setupChatSearch();
    setupRadarTabs();
    setupRadarIgnoredBanner();
    setupAssignFilterButtons();
    setupConnectionConfig();
    setupMessageListener();
    startPresenceHeartbeat();

    // Obtener último estado de sincronización conocido
    chrome.runtime.sendMessage({ action: "get_sync_status" }, (res) => {
      if (res) {
        updateHeaderStatus(res.state, res.message, res.totalSaved);
        if (res.accountInfo) updateAccountUI(res.accountInfo);
      }
    });

    // Cargar perfil y plantillas
    await loadWorkspaceProfile();
    await loadTemplates();

    // Obtener chat activo inicial si ya está abierto en la pestaña
    chrome.runtime.sendMessage({ action: "get_active_chat" }, (res) => {
      if (res && res.chatQuery) {
        if (res.accountId) {
          updateAccountUI({
            id: res.accountId,
            phone: res.accountPhone,
            name: res.accountName
          });
        }
        loadChatContext(res.chatQuery);
      }
    });

    // Cargar radar inicial
    loadRadarData();
  }

  document.addEventListener("DOMContentLoaded", init);

})();
