// WAckup 3.0 - Service Worker de la extensión (Manifest V3)
// Gestiona el sidePanel nativo, configuración local/LAN, badge de estado y llamadas seguras.

let config = {
  serverUrl: "http://127.0.0.1:8080",
  officePin: "",
  operatorName: ""
};

let lastActiveChat = {
  chatQuery: null,
  accountId: null,
  accountPhone: null,
  accountName: null,
  updatedAt: 0
};

let lastSyncStatus = {
  state: "waiting",
  message: "Iniciando...",
  totalSaved: 0,
  accountInfo: null,
  updatedAt: 0
};

let lastUnansweredCount = 0;

// =========================================================================
// 1. CONFIGURACIÓN DEL SIDEPANEL NATIVO
// =========================================================================
if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === "function") {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.warn("[WAckup SW] Error al configurar sidePanel behavior:", err));
}

// Cargar configuración inicial desde chrome.storage.local
chrome.storage.local.get(["serverUrl", "officePin", "operatorName"], (res) => {
  if (res && res.serverUrl) config.serverUrl = res.serverUrl.replace(/\/+$/, "");
  if (res && res.officePin) config.officePin = res.officePin;
  if (res && res.operatorName) config.operatorName = res.operatorName;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local") {
    if (changes.serverUrl) config.serverUrl = (changes.serverUrl.newValue || "http://127.0.0.1:8080").replace(/\/+$/, "");
    if (changes.officePin) config.officePin = changes.officePin.newValue || "";
    if (changes.operatorName) config.operatorName = changes.operatorName.newValue || "";
  }
});

// =========================================================================
// 2. GESTIÓN DEL BADGE DEL ICONO DE LA EXTENSIÓN
// =========================================================================
function updateBadge(status, text) {
  try {
    if (!chrome.action) return;

    if (status === "syncing") {
      chrome.action.setBadgeText({ text: "SYNC" });
      chrome.action.setBadgeBackgroundColor({ color: "#2563eb" }); // Azul
    } else if (status === "error" || status === "offline") {
      chrome.action.setBadgeText({ text: "OFF" });
      chrome.action.setBadgeBackgroundColor({ color: "#dc2626" }); // Rojo
    } else if (status === "synced" || status === "ok") {
      const count = typeof text === "number" ? (text > 99 ? "99+" : (text > 0 ? String(text) : "")) : (text || "");
      chrome.action.setBadgeText({ text: count });
      chrome.action.setBadgeBackgroundColor({ color: "#10b981" }); // Verde esmeralda
    } else {
      chrome.action.setBadgeText({ text: "" });
    }
  } catch (e) {
    console.warn("[WAckup SW] Error al actualizar badge:", e);
  }
}

// =========================================================================
// 3. LISTA BLANCA ESTRICTA DE ENDPOINTS PERMITIDOS
// =========================================================================
const ALLOWED_ENDPOINTS = [
  { method: "GET", pattern: /^\/api\/sync\/status(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/sync\/?$/ },
  { method: "GET", pattern: /^\/api\/accounts(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/profile(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/profile(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/profile\/cycle(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/copilot\/context(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/notes\/?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/tags\/?$/ },
  { method: "GET", pattern: /^\/api\/support\/unanswered(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/support\/ticket\/?$/ },
  { method: "GET", pattern: /^\/api\/support\/templates(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/support\/templates\/?$/ },
  { method: "POST", pattern: /^\/api\/sales\/pipeline\/?$/ },
  { method: "GET", pattern: /^\/api\/sales\/radar(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/crm\/rfm\/dormant(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/crm\/support\/metrics(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/search(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/extension\/info(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/presence(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/presence\/heartbeat\/?$/ },
  { method: "POST", pattern: /^\/api\/presence\/leave\/?$/ },
  { method: "GET", pattern: /^\/api\/copilot\/notes\/log(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/notes\/log\/?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/notes\/log\/delete\/?$/ },
  { method: "DELETE", pattern: /^\/api\/copilot\/notes\/log(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/assignment\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/network(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/system\/network\/?$/ },
  { method: "POST", pattern: /^\/api\/auth\/verify_pin\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/info(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/system\/onboarding(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/system\/onboarding\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/update\/check(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/system\/update\/apply\/?$/ }
];

function isAllowedRequest(endpoint, method) {
  if (typeof endpoint !== "string") return false;
  return ALLOWED_ENDPOINTS.some(rule => rule.method === method && rule.pattern.test(endpoint));
}

// =========================================================================
// 4. DISPATCHER DE MENSAJES
// =========================================================================
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (!request || typeof request !== "object") return;

  // Recarga en caliente asistida de la extensión
  if (request.action === "reload_extension") {
    sendResponse({ ok: true });
    setTimeout(() => {
      chrome.runtime.reload();
    }, 150);
    return;
  }

  // Notificación de cambio de chat activo (desde bridge.js)
  if (request.action === "active_chat_changed") {
    lastActiveChat = {
      chatQuery: request.chatQuery,
      accountId: request.accountId,
      accountPhone: request.accountPhone,
      accountName: request.accountName,
      updatedAt: Date.now()
    };
    sendResponse({ ok: true });
    return;
  }

  // Consulta del chat activo actual (desde sidepanel.js)
  if (request.action === "get_active_chat") {
    sendResponse(lastActiveChat);
    return;
  }

  // Notificación de estado de sincronización (desde bridge.js o sidepanel.js)
  if (request.action === "sync_status_changed") {
    lastSyncStatus = {
      state: request.state,
      message: request.message,
      totalSaved: request.totalSaved || 0,
      accountInfo: request.accountInfo || null,
      updatedAt: Date.now()
    };

    if (request.state === "syncing") {
      updateBadge("syncing");
    } else if (request.state === "error") {
      updateBadge("error");
    } else if (request.state === "synced" || request.state === "ready") {
      updateBadge("synced", lastUnansweredCount > 0 ? lastUnansweredCount : "");
    }

    sendResponse({ ok: true });
    return;
  }

  // Actualización del conteo de pendientes para el badge
  if (request.action === "update_unanswered_count") {
    lastUnansweredCount = Number(request.count) || 0;
    if (lastSyncStatus.state !== "syncing" && lastSyncStatus.state !== "error") {
      updateBadge("synced", lastUnansweredCount > 0 ? lastUnansweredCount : "");
    }
    sendResponse({ ok: true });
    return;
  }

  // Consulta del último estado de sincronización conocido
  if (request.action === "get_sync_status") {
    sendResponse(lastSyncStatus);
    return;
  }

  // Reenvío de trigger manual hacia pestañas de WhatsApp Web
  if (request.action === "trigger_manual_sync") {
    chrome.tabs.query({ url: "https://web.whatsapp.com/*" }, (tabs) => {
      for (const tab of tabs) {
        chrome.tabs.sendMessage(tab.id, {
          action: "trigger_manual_sync",
          forceAll: Boolean(request.forceAll)
        }).catch(() => {});
      }
    });
    sendResponse({ ok: true });
    return;
  }

  // Enfocar pestaña remitente del Visor Web (desde viewer_bridge.js, incluso con SidePanel cerrado)
  if (request.action === "focus_my_tab") {
    if (sender && sender.tab && sender.tab.id) {
      chrome.tabs.update(sender.tab.id, { active: true }, () => {
        if (sender.tab.windowId) {
          chrome.windows.update(sender.tab.windowId, { focused: true }).catch(() => {});
        }
        sendResponse({ ok: true });
      });
      return true;
    }
    sendResponse({ ok: false });
    return;
  }

  // Enfocar pestaña existente del Visor Web sin abrir duplicados
  if (request.action === "focus_viewer_tab") {
    const base = (config.serverUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
    chrome.tabs.query({ url: ["http://127.0.0.1/*", "http://localhost/*"] }, (tabs) => {
      const list = Array.isArray(tabs) ? tabs : [];
      const match = list.find(t => t.url && t.url.startsWith(base)) || list[0];
      if (match && match.id) {
        chrome.tabs.update(match.id, { active: true }, () => {
          if (match.windowId) {
            chrome.windows.update(match.windowId, { focused: true }).catch(() => {});
          }
        });
      }
    });
    sendResponse({ ok: true });
    return;
  }

  // Obtener / guardar configuración
  if (request.action === "get_config") {
    sendResponse({ ok: true, config });
    return;
  }

  if (request.action === "save_config") {
    const newCfg = request.config || {};
    const toSave = {};
    if (typeof newCfg.serverUrl === "string") toSave.serverUrl = newCfg.serverUrl.trim().replace(/\/+$/, "");
    if (typeof newCfg.officePin === "string") toSave.officePin = newCfg.officePin.trim();
    if (typeof newCfg.operatorName === "string") toSave.operatorName = newCfg.operatorName.trim();

    chrome.storage.local.set(toSave, () => {
      Object.assign(config, toSave);
      sendResponse({ ok: true, config });
    });
    return true;
  }

  // Proxy seguro de peticiones HTTP al backend local/LAN
  if (request.action === "backend_fetch") {
    // Permitir mensajes tanto de WhatsApp Web (content script) como del sidePanel nativo
    const isExtensionOrigin = sender.id === chrome.runtime.id;
    const senderUrl = (sender && (sender.url || (sender.tab && sender.tab.url))) || "";
    const isWhatsApp = senderUrl.startsWith("https://web.whatsapp.com/");

    if (!isExtensionOrigin && !isWhatsApp) {
      sendResponse({ ok: false, error: "Origen no permitido" });
      return;
    }

    const opts = request.options || {};
    const method = (opts.method || "GET").toUpperCase();
    if (!isAllowedRequest(request.endpoint, method)) {
      sendResponse({ ok: false, error: "Endpoint no permitido: " + request.endpoint });
      return;
    }

    const headers = { "Accept": "application/json" };
    if (config.officePin) {
      headers["X-WAckup-Pin"] = config.officePin;
      headers["X-Office-PIN"] = config.officePin;
    }
    if (config.operatorName) {
      headers["X-WAckup-Operator"] = config.operatorName;
      headers["X-Operator-Name"] = config.operatorName;
    }

    const fetchOptions = { method, headers };
    if (method === "POST") {
      fetchOptions.headers["Content-Type"] = "application/json";
      fetchOptions.body = typeof opts.body === "string" ? opts.body : (opts.body ? JSON.stringify(opts.body) : "");
    }

    const targetUrl = config.serverUrl + request.endpoint;

    fetch(targetUrl, fetchOptions)
      .then(async (res) => {
        const text = await res.text();
        let json = null;
        try {
          json = JSON.parse(text);
        } catch (e) {
          json = null;
        }

        sendResponse({
          ok: res.ok,
          status: res.status,
          data: json !== null ? json : text
        });
      })
      .catch((err) => {
        updateBadge("offline");
        sendResponse({
          ok: false,
          error: err.message || "Error al conectar con el servidor local"
        });
      });

    return true; // Mantiene el canal abierto para respuesta asíncrona
  }
});
