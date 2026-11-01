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

  // =========================================================================
  // COMUNICACIÓN CON EL SERVICE WORKER
  // =========================================================================
  function apiFetch(endpoint, options = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({
        action: "backend_fetch",
        endpoint: endpoint,
        options: options
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
  function showToast(message) {
    const toast = document.getElementById("toast-notification");
    if (!toast) return;
    toast.textContent = message;
    toast.style.display = "block";
    toast.style.animation = "none";
    void toast.offsetWidth; // Reflow
    toast.style.animation = "";
    setTimeout(() => {
      toast.style.display = "none";
    }, 2500);
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
  // ESTADO DE SINCRONIZACIÓN Y CABECERA
  // =========================================================================
  function updateHeaderStatus(state, message, totalSaved = null) {
    const dot = document.getElementById("header-status-dot");
    const text = document.getElementById("header-status-text");
    const msgs = document.getElementById("header-stats-msgs");

    if (dot) {
      dot.className = "status-dot " + (state || "ready");
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
    if (info.id) currentAccountId = info.id;
    if (info.name) currentAccountName = info.name;
    if (info.phone) currentAccountPhone = info.phone;

    const nameEl = document.getElementById("header-account-name");
    const phoneEl = document.getElementById("header-account-phone");

    if (nameEl) nameEl.textContent = currentAccountName || (currentAccountPhone ? `+${currentAccountPhone}` : "Cuenta WhatsApp");
    if (phoneEl) phoneEl.textContent = currentAccountPhone ? `(+${currentAccountPhone})` : "";
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

    const profileLabels = {
      "backup_only": "Solo Respaldo",
      "support": "Soporte",
      "sales": "Ventas",
      "full": "CRM Completo"
    };

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
      const slaEl = document.getElementById("contact-sla");

      const displayName = data.display_name || data.contact_name || chatQuery;
      const phoneNum = data.phone_number || data.contact_number || "";
      currentContactDisplayName = displayName;

      if (nameEl) nameEl.textContent = displayName;
      if (phoneEl) phoneEl.textContent = phoneNum ? `+${String(phoneNum).replace(/^\+/, "")}` : (data.chat_id || "");

      if (slaEl) {
        const slaLabel = data.sla || (data.sla_minutes !== null && data.sla_minutes !== undefined ? `~${data.sla_minutes}m` : null);
        if (slaLabel && !String(slaLabel).toLowerCase().includes("sin datos")) {
          currentSlaMinutes = data.sla_minutes;
          slaEl.textContent = `SLA: ${slaLabel}`;
          slaEl.style.display = "inline-block";
        } else {
          slaEl.style.display = "none";
        }
      }

      // Perfil si vino en contexto
      if (data.profile && data.profile !== currentProfile) {
        currentProfile = data.profile;
        applyProfileUI();
      }

      // Etiquetas
      renderTags(data.tags || []);

      const crmMeta = data.crm || {};

      // Ticket
      const ticketStatus = crmMeta.ticket_status || data.ticket_status || "none";
      document.querySelectorAll(".btn-ticket").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-status") === ticketStatus);
      });

      // Pipeline comercial
      const stageSelect = document.getElementById("select-sales-stage");
      const dealInput = document.getElementById("input-deal-value");
      const followupInput = document.getElementById("input-followup-date");

      const stageVal = crmMeta.sales_stage || data.sales_stage || "none";
      const estVal = crmMeta.estimated_value !== undefined ? crmMeta.estimated_value : data.deal_value;
      const followVal = crmMeta.follow_up_date || data.followup_date || "";

      if (stageSelect) stageSelect.value = stageVal;
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

      // Ficha: Entidades comerciales
      renderEntities(data.extracted_info || data.detected_entities || {});

      // Ficha: Precios cotizados
      renderPrices(data.quoted_prices || data.detected_prices || []);

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
            updatePresenceBanner(activePresenceMap);
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
          }
        } catch (err) {}
      };
      sseSource.onmessage = handleEvt;
      sseSource.addEventListener("connected", handleEvt);
      sseSource.addEventListener("presence_update", handleEvt);
      sseSource.addEventListener("crm_update", handleEvt);
      sseSource.addEventListener("sync_update", handleEvt);
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
        const isMe = (v.client_id && v.client_id === clientId) ||
          (operatorName && v.operator && v.operator === operatorName);
        if (!isMe && v.operator) {
          otherNames.push(v.operator);
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
          showToast(`Ticket marcado como ${newStatus}`);
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
              sales_stage: stageSelect.value,
              estimated_value: parseFloat(dealInput.value) || 0,
              follow_up_date: followupInput.value || null
            }
          });
          showToast("Pipeline comercial actualizado");
        } catch (e) {
          showToast("Error al guardar pipeline");
        }
      }, 500);
    };

    if (stageSelect) stageSelect.addEventListener("change", savePipeline);
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
      container.innerHTML = '<span class="empty-subtext">No hay plantillas configuradas.</span>';
      return;
    }

    list.forEach(tpl => {
      const card = document.createElement("div");
      card.className = "template-card";
      card.innerHTML = `
        <div class="template-title">${escapeHtml(tpl.title || "Plantilla")}</div>
        <div class="template-snippet">${escapeHtml(tpl.body || "")}</div>
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
  // PESTAÑA 2: FICHA (ENTIDADES, PRECIOS, BÚSQUEDA FTS5)
  // =========================================================================
  function renderEntities(entities) {
    const container = document.getElementById("entities-container");
    if (!container) return;
    container.innerHTML = "";

    const chips = [];
    if (Array.isArray(entities.emails)) entities.emails.forEach(v => chips.push({ type: "Email", val: v }));
    if (Array.isArray(entities.tax_ids)) entities.tax_ids.forEach(v => chips.push({ type: "CUIT/DNI", val: v }));
    if (Array.isArray(entities.bank_details)) entities.bank_details.forEach(v => chips.push({ type: "CBU/Alias", val: v }));
    if (Array.isArray(entities.cuit)) entities.cuit.forEach(v => chips.push({ type: "CUIT/CUIL", val: v }));
    if (Array.isArray(entities.dni)) entities.dni.forEach(v => chips.push({ type: "DNI", val: v }));
    if (Array.isArray(entities.email)) entities.email.forEach(v => chips.push({ type: "Email", val: v }));
    if (Array.isArray(entities.cbu)) entities.cbu.forEach(v => chips.push({ type: "CBU/CVU", val: v }));
    if (Array.isArray(entities.alias)) entities.alias.forEach(v => chips.push({ type: "Alias", val: v }));

    if (chips.length === 0) {
      container.innerHTML = '<div class="empty-subtext">No se detectaron CUITs, DNIs, emails ni cuentas bancarias en este chat.</div>';
      return;
    }

    chips.forEach(c => {
      const chipEl = document.createElement("div");
      chipEl.className = "entity-chip";
      chipEl.innerHTML = `
        <span class="entity-type">${escapeHtml(c.type)}</span>
        <span class="entity-value">${escapeHtml(c.val)}</span>
      `;
      chipEl.addEventListener("click", () => {
        copyToClipboard(c.val, `Copiado ${c.type}: ${c.val}`);
      });
      container.appendChild(chipEl);
    });
  }

  function renderPrices(prices) {
    const container = document.getElementById("prices-history-list");
    if (!container) return;
    container.innerHTML = "";

    if (!Array.isArray(prices) || prices.length === 0) {
      container.innerHTML = '<div class="empty-subtext">Sin precios detectados.</div>';
      return;
    }

    prices.forEach(p => {
      const item = document.createElement("div");
      item.className = "price-item";
      let valStr = "";
      if (p && typeof p === "object") {
        const rawNum = p.price !== undefined ? p.price : p.amount;
        valStr = p.amount_formatted || (rawNum !== undefined ? `$${Number(rawNum).toLocaleString()}` : "");
      } else {
        valStr = `$${p}`;
      }
      const dateStr = (p && (p.date_str || (p.date ? String(p.date).substring(0, 10) : ""))) || "";
      item.innerHTML = `
        <span class="price-val">${escapeHtml(valStr)}</span>
        <span class="price-date">${escapeHtml(dateStr)}</span>
      `;
      item.addEventListener("click", () => {
        copyToClipboard(valStr, `Copiado precio: ${valStr}`);
      });
      container.appendChild(item);
    });
  }

  function setupChatSearch() {
    const input = document.getElementById("input-search-chat");
    const btn = document.getElementById("btn-search-chat");
    const resultsContainer = document.getElementById("chat-search-results");

    const doSearch = async () => {
      const q = (input && input.value || "").trim();
      if (!q || !currentChatId) return;

      resultsContainer.innerHTML = '<div class="empty-subtext">Buscando...</div>';
      try {
        const data = await apiFetch(`/api/search?q=${encodeURIComponent(q)}&chat_id=${encodeURIComponent(currentChatId)}&account_id=${encodeURIComponent(currentAccountId)}`);
        const matches = Array.isArray(data) ? data : (data && data.results ? data.results : []);
        renderSearchResults(matches, q);
      } catch (e) {
        resultsContainer.innerHTML = '<div class="empty-subtext">Error en la búsqueda.</div>';
      }
    };

    if (btn) btn.addEventListener("click", doSearch);
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  }

  function renderSearchResults(results, query) {
    const container = document.getElementById("chat-search-results");
    if (!container) return;
    container.innerHTML = "";

    if (!Array.isArray(results) || results.length === 0) {
      container.innerHTML = '<div class="empty-subtext">No se encontraron mensajes que coincidan.</div>';
      return;
    }

    results.forEach(m => {
      const item = document.createElement("div");
      item.className = "search-result-item";
      const sender = m.es_mio ? "Tú" : (m.sender_name || "Contacto");
      const dateStr = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "";
      const text = m.contenido || m.snippet || "";

      item.innerHTML = `
        <div class="search-result-meta">
          <strong>${escapeHtml(sender)}</strong>
          <span>${escapeHtml(dateStr)}</span>
        </div>
        <div class="search-result-snippet">${highlightQuery(text, query)}</div>
      `;
      item.addEventListener("click", () => {
        copyToClipboard(text, "Mensaje copiado al portapapeles");
      });
      container.appendChild(item);
    });
  }

  // =========================================================================
  // PESTAÑA 3: RADAR (SIN RESPONDER / SEGUIMIENTOS VENTAS)
  // =========================================================================
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
  }

  async function loadRadarData() {
    const container = document.getElementById("radar-items-container");
    if (!container) return;
    container.innerHTML = '<div class="empty-subtext">Cargando radar...</div>';

    try {
      if (currentRadarMode === "unanswered") {
        const data = await apiFetch(`/api/support/unanswered?account_id=${encodeURIComponent(currentAccountId)}`);
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

    if (!filtered || filtered.length === 0) {
      container.innerHTML = '<div class="empty-subtext">🎉 ¡Excelente! No tienes mensajes pendientes de respuesta.</div>';
      return;
    }

    filtered.forEach(item => {
      const el = document.createElement("div");
      el.className = "radar-item";
      const name = item.display_name || item.nombre || item.contact_name || item.chat_id;
      const snippet = item.last_message || item.ultimo_mensaje || "(Mensaje multimedia/vacío)";
      const waitTime = item.waiting_str || (item.minutos_espera ? `Hace ${item.minutos_espera}m` : (item.espera || ""));
      const assignBadge = item.assigned_to ? `<span class="badge-assign" style="font-size:10px; color:#38bdf8;">👤 ${escapeHtml(item.assigned_to)}</span>` : "";

      el.innerHTML = `
        <div class="radar-item-top">
          <span class="radar-item-name">${escapeHtml(name)} ${assignBadge}</span>
          <span class="radar-item-time">${escapeHtml(waitTime)}</span>
        </div>
        <div class="radar-item-snippet">${escapeHtml(snippet)}</div>
      `;

      el.addEventListener("click", () => {
        loadChatContext(item.chat_id || name);
        copyToClipboard(name, `Contacto "${name}" copiado para buscar en WhatsApp Web`);
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

    if (!filtered || filtered.length === 0) {
      container.innerHTML = '<div class="empty-subtext">No hay seguimientos de ventas pendientes hoy.</div>';
      return;
    }

    filtered.forEach(item => {
      const el = document.createElement("div");
      el.className = "radar-item";
      const name = item.display_name || item.contact_name || item.chat_id;
      const stage = item.sales_stage || "lead";
      const value = item.estimated_value ? `$${item.estimated_value}` : "";
      const assignBadge = item.assigned_to ? `<span class="badge-assign" style="font-size:10px; color:#38bdf8;">👤 ${escapeHtml(item.assigned_to)}</span>` : "";

      el.innerHTML = `
        <div class="radar-item-top">
          <span class="radar-item-name">${escapeHtml(name)} ${assignBadge}</span>
          <span class="radar-item-time">${escapeHtml(stage)} ${escapeHtml(value)}</span>
        </div>
        <div class="radar-item-snippet">${escapeHtml(item.last_message || item.notes || "Sin notas")}</div>
      `;

      el.addEventListener("click", () => {
        loadChatContext(item.chat_id || name);
        copyToClipboard(name, `Contacto "${name}" copiado para buscar en WhatsApp Web`);
      });

      container.appendChild(el);
    });
  }

  // =========================================================================
  // CONFIGURACIÓN DE CONEXIÓN LOCAL / LAN
  // =========================================================================
  function setupConnectionConfig() {
    const toggleBtn = document.getElementById("btn-toggle-connection");
    const body = document.getElementById("connection-body");
    const arrow = document.getElementById("accordion-arrow");
    const saveBtn = document.getElementById("btn-save-cfg");
    const feedback = document.getElementById("cfg-feedback");

    if (toggleBtn && body) {
      toggleBtn.addEventListener("click", () => {
        const isClosed = body.style.display === "none";
        body.style.display = isClosed ? "block" : "none";
        if (arrow) arrow.textContent = isClosed ? "▲" : "▼";
      });
    }

    // Cargar config actual desde el service worker
    chrome.runtime.sendMessage({ action: "get_config" }, (res) => {
      if (res && res.config) {
        operatorName = res.config.operatorName || "";
        currentServerUrl = res.config.serverUrl || "http://127.0.0.1:8080";
        currentOfficePin = res.config.officePin || "";
        const urlInput = document.getElementById("cfg-server-url");
        const pinInput = document.getElementById("cfg-office-pin");
        const opInput = document.getElementById("cfg-operator-name");

        if (urlInput) urlInput.value = currentServerUrl;
        if (pinInput) pinInput.value = currentOfficePin;
        if (opInput) opInput.value = operatorName;
      }
      connectSidePanelSSE();
    });

    // Cargar operadores conocidos desde la red si es posible
    apiFetch("/api/system/network").then(netData => {
      if (netData && Array.isArray(netData.known_operators)) {
        knownOperators = netData.known_operators;
        const sel = document.getElementById("select-assigned-to");
        if (sel) populateAssigneeOptions(sel, sel.value);
      }
    }).catch(() => {});

    if (saveBtn) {
      saveBtn.addEventListener("click", () => {
        const urlInput = document.getElementById("cfg-server-url");
        const pinInput = document.getElementById("cfg-office-pin");
        const opInput = document.getElementById("cfg-operator-name");

        const newConfig = {
          serverUrl: urlInput ? urlInput.value.trim() : "http://127.0.0.1:8080",
          officePin: pinInput ? pinInput.value.trim() : "",
          operatorName: opInput ? opInput.value.trim() : ""
        };

        operatorName = newConfig.operatorName;
        currentServerUrl = newConfig.serverUrl;
        currentOfficePin = newConfig.officePin;

        chrome.runtime.sendMessage({ action: "save_config", config: newConfig }, () => {
          if (feedback) {
            feedback.style.display = "block";
            feedback.textContent = "Configuración guardada correctamente ✓";
            setTimeout(() => { feedback.style.display = "none"; }, 3000);
          }
          showToast("Configuración guardada");
          connectSidePanelSSE();
          loadWorkspaceProfile();
          loadTemplates();
          if (currentChatQuery) loadChatContext(currentChatQuery);
        });
      });
    }
  }

  // =========================================================================
  // BOTONES RÁPIDOS DE CABECERA Y ACTUALIZACIÓN
  // =========================================================================
  function setupHeaderButtons() {
    const btnSyncRecent = document.getElementById("btn-sync-recent");
    const btnSyncAll = document.getElementById("btn-sync-all");
    const btnBackupManual = document.getElementById("btn-backup-manual");
    const btnUpdateExt = document.getElementById("btn-update-ext");
    const btnAddTag = document.getElementById("btn-add-tag");

    if (btnSyncRecent) {
      btnSyncRecent.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: false });
        showToast("Sincronizando novedades...");
      });
    }

    if (btnSyncAll) {
      btnSyncAll.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: true });
        showToast("Sincronizando historial completo...");
      });
    }

    if (btnBackupManual) {
      btnBackupManual.addEventListener("click", () => {
        chrome.runtime.sendMessage({ action: "trigger_manual_sync", forceAll: false });
        showToast("Sincronizando...");
      });
    }

    if (btnUpdateExt) {
      btnUpdateExt.addEventListener("click", () => {
        showToast("Actualizando extensión...");
        chrome.runtime.sendMessage({ action: "reload_extension" });
      });
    }

    if (btnAddTag) {
      btnAddTag.addEventListener("click", () => {
        const tag = prompt("Ingresa el nombre de la nueva etiqueta:");
        if (tag) addTag(tag);
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
    const escaped = escapeHtml(text);
    if (!query) return escaped;
    const cleanQ = escapeHtml(query.trim());
    const reg = new RegExp(`(${cleanQ})`, "gi");
    return escaped.replace(reg, "<mark>$1</mark>");
  }

  // =========================================================================
  // INICIALIZACIÓN
  // =========================================================================
  async function init() {
    setupTabs();
    setupHeaderButtons();
    setupNotesDebounce();
    setupNotesLogInput();
    setupAssignmentSelect();
    setupTicketButtons();
    setupPipelineInputs();
    setupTemplateFilter();
    setupChatSearch();
    setupRadarTabs();
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
