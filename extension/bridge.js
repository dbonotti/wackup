// WAckup 3.0 - Puente de comunicación aislado (ISOLATED World)
// Cero modificaciones al DOM de WhatsApp Web. Detector 100% pasivo de solo lectura.
(function() {
  'use strict';

  const PAGE_ORIGIN = "https://web.whatsapp.com";

  let lastDetectedChatQuery = null;
  let lastDetectedAccountId = null;
  let titleObserver = null;

  // Lista negra de títulos de interfaz que no representan contactos ni grupos
  const BLACKLIST_TITLES = new Set([
    "whatsapp", "buscar", "buscar...", "menú", "menu", "más opciones",
    "opciones", "llamada", "videollamada", "adjuntar", "foto del perfil",
    "foto de perfil", "info. del chat", "información del chat", "audio",
    "video", "cargando...", "esperando conversación..."
  ]);

  function isCleanChatTitle(t) {
    if (!t || typeof t !== "string") return false;
    const clean = t.trim();
    if (clean.length < 1 || clean.length > 80) return false;
    const lower = clean.toLowerCase();
    if (BLACKLIST_TITLES.has(lower)) return false;
    if (lower.startsWith("últ. vez") || lower.startsWith("last seen") || lower.startsWith("en línea") || lower.startsWith("online")) return false;
    return true;
  }

  function getActiveAccountInfo() {
    let rawWid = "";
    try {
      rawWid = window.localStorage.getItem("last-wid-md") || window.localStorage.getItem("last-wid") || "";
      if (typeof rawWid === "string" && rawWid.startsWith('"') && rawWid.endsWith('"')) {
        rawWid = JSON.parse(rawWid);
      }
    } catch (e) {}

    let phone = "";
    if (rawWid) {
      const parts = String(rawWid).split("@")[0].split(":");
      const digits = parts[0].replace(/\D/g, "");
      if (digits.length >= 7) {
        phone = digits;
      }
    }

    let name = "";
    try {
      name = window.localStorage.getItem("me-display-name") || window.localStorage.getItem("pushname") || "";
      if (typeof name === "string" && name.startsWith('"') && name.endsWith('"')) {
        name = JSON.parse(name);
      }
    } catch (e) {}

    const accountId = phone || (rawWid ? String(rawWid).split("@")[0].replace(/[^a-zA-Z0-9_\-]/g, "") : "default");

    return {
      id: accountId,
      phone: phone,
      name: name || (phone ? `+${phone}` : "Cuenta WhatsApp"),
      wid: rawWid
    };
  }

  function detectActiveChatTitle() {
    // 1. Cabecera principal de la conversación activa en WhatsApp Web
    const mainHeader = document.querySelector("#main header");
    if (mainHeader) {
      const contactBtn = mainHeader.querySelector('div[role="button"]:not([title="Buscar..."]):not([title="Menú"])');
      const rootEl = contactBtn || mainHeader;
      const spans = rootEl.querySelectorAll('span[dir="auto"]');
      for (const s of spans) {
        const t = (s.getAttribute("title") || s.textContent || "").trim();
        if (isCleanChatTitle(t)) {
          return t;
        }
      }
    }

    // 2. Fila seleccionada en el panel de chats (#pane-side)
    const activeRow = document.querySelector('#pane-side div[aria-selected="true"], #pane-side div[data-testid="cell-frame-container"][aria-selected="true"]');
    if (activeRow) {
      const spans = activeRow.querySelectorAll('span[dir="auto"], span[title]');
      for (const s of spans) {
        const t = (s.getAttribute("title") || s.textContent || "").trim();
        if (isCleanChatTitle(t)) {
          return t;
        }
      }
    }

    // 3. Título de la pestaña de WhatsApp Web ("Nombre - WhatsApp")
    const docTitle = (document.title || "").replace(/^\(\d+\+?\)\s*/, "").trim();
    const match = docTitle.match(/^(.+?)\s*-\s*WhatsApp/i);
    if (match && match[1]) {
      const t = match[1].trim();
      if (isCleanChatTitle(t)) {
        return t;
      }
    }

    return null;
  }

  function notifyActiveChatChanged(force = false) {
    const chatTitle = detectActiveChatTitle();
    const accountInfo = getActiveAccountInfo();

    const chatChanged = chatTitle && chatTitle !== lastDetectedChatQuery;
    const accountChanged = accountInfo.id && accountInfo.id !== lastDetectedAccountId;

    if (chatChanged || accountChanged || force) {
      if (chatTitle) lastDetectedChatQuery = chatTitle;
      if (accountInfo.id) lastDetectedAccountId = accountInfo.id;

      try {
        chrome.runtime.sendMessage({
          action: "active_chat_changed",
          chatQuery: chatTitle || lastDetectedChatQuery,
          accountId: accountInfo.id,
          accountPhone: accountInfo.phone,
          accountName: accountInfo.name
        }).catch(() => {});
      } catch (e) {}
    }
  }

  function setupPassiveDetector() {
    // Escucha pasiva de clics en la lista de chats (captura pura, sin alterar DOM ni eventos)
    document.addEventListener("click", (e) => {
      const row = e.target && e.target.closest && e.target.closest('#pane-side div[role="row"], #pane-side div[role="listitem"], #pane-side div[data-testid="cell-frame-container"]');
      if (row) {
        const spans = row.querySelectorAll('span[dir="auto"], span[title]');
        for (const s of spans) {
          const t = (s.getAttribute("title") || s.textContent || "").trim();
          if (isCleanChatTitle(t)) {
            lastDetectedChatQuery = t;
            notifyActiveChatChanged(true);
            break;
          }
        }
      }
      setTimeout(() => notifyActiveChatChanged(), 200);
      setTimeout(() => notifyActiveChatChanged(), 600);
    }, { passive: true, capture: true });

    // Observar cambios pasivos en el título de la pestaña
    const titleTag = document.querySelector("title");
    if (titleTag && !titleObserver) {
      titleObserver = new MutationObserver(() => {
        notifyActiveChatChanged();
      });
      titleObserver.observe(titleTag, { childList: true, characterData: true, subtree: true });
    }

    // Chequeo periódico de baja frecuencia (cada 2 segundos) para cambios desatendidos
    setInterval(() => {
      notifyActiveChatChanged();
    }, 2000);
  }

  // =========================================================================
  // CANAL DE COMUNICACIÓN CON crypto_sync.js (MAIN)
  // =========================================================================
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== PAGE_ORIGIN) return;
    if (!event.data) return;

    // 1. Notificación de estado de sincronización
    if (event.data.type === "BACKUPWA_STATUS") {
      try {
        chrome.runtime.sendMessage({
          action: "sync_status_changed",
          state: event.data.state,
          message: event.data.message,
          totalSaved: event.data.totalSaved,
          accountInfo: event.data.accountInfo
        }).catch(() => {});
      } catch (e) {}
      return;
    }

    // 2. Información de versión de la extensión en disco
    if (event.data.type === "BACKUPWA_VERSION_INFO") {
      try {
        chrome.runtime.sendMessage({
          action: "extension_version_info",
          diskVer: event.data.diskVer,
          installedVer: event.data.installedVer
        }).catch(() => {});
      } catch (e) {}
      return;
    }

    // 3. Solicitud de recarga de extensión
    if (event.data.type === "BACKUPWA_RELOAD_EXT") {
      try {
        chrome.runtime.sendMessage({ action: "reload_extension" });
      } catch (e) {
        console.warn("[WAckup] Error al solicitar recarga:", e);
      }
      return;
    }

    // 4. Petición HTTP al backend local
    if (event.data.type !== "BACKUPWA_REQ") return;

    const { reqId, endpoint, options } = event.data;
    if (typeof reqId !== "string" || typeof endpoint !== "string") return;

    chrome.runtime.sendMessage({
      action: "backend_fetch",
      endpoint: endpoint,
      options: options
    }, (response) => {
      if (response && response.data && typeof response.data === "object") {
        try {
          response.data.installed_extension_version = chrome.runtime.getManifest().version;
        } catch (e) {}
      }

      if (chrome.runtime.lastError) {
        window.postMessage({
          type: "BACKUPWA_RESP",
          reqId: reqId,
          error: chrome.runtime.lastError.message
        }, PAGE_ORIGIN);
      } else {
        window.postMessage({
          type: "BACKUPWA_RESP",
          reqId: reqId,
          response: response
        }, PAGE_ORIGIN);
      }
    });
  });

  // =========================================================================
  // ESCUCHA DE COMANDOS DESDE BACKGROUND / SIDEPANEL
  // =========================================================================
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "trigger_manual_sync") {
      window.postMessage({
        type: "BACKUPWA_TRIGGER_SYNC",
        forceAll: Boolean(request.forceAll)
      }, PAGE_ORIGIN);
      sendResponse({ ok: true });
      return true;
    }

    if (request.action === "get_active_chat") {
      const chatTitle = detectActiveChatTitle() || lastDetectedChatQuery;
      const accountInfo = getActiveAccountInfo();
      sendResponse({
        chatQuery: chatTitle,
        accountInfo: accountInfo
      });
      return false;
    }
  });

  // Inicializar detector pasivo al cargar
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupPassiveDetector);
  } else {
    setupPassiveDetector();
  }

})();
