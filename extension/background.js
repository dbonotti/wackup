// WAckup 3.0 - Service Worker de la extensión (Manifest V3)
// Gestiona el sidePanel nativo, configuración local/LAN, badge de estado y llamadas seguras.

let config = {
  serverUrl: "http://127.0.0.1:8080",
  officePin: "",
  operatorName: "",
  sessionToken: ""
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
// 1. CONFIGURACIÓN DEL SIDEPANEL NATIVO (ESPECÍFICO PARA WHATSAPP WEB)
// =========================================================================
if (chrome.sidePanel && typeof chrome.sidePanel.setPanelBehavior === "function") {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.warn("[WAckup SW] Error al configurar sidePanel behavior:", err));
}

// Deshabilitar el panel lateral global por defecto para que no se muestre en pestañas ajenas
if (chrome.sidePanel && typeof chrome.sidePanel.setOptions === "function") {
  chrome.sidePanel.setOptions({ enabled: false })
    .catch((err) => console.warn("[WAckup SW] Error al deshabilitar sidePanel global:", err));
}

// Configurar el panel lateral exclusivamente cuando la pestaña navega a WhatsApp Web
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const currentUrl = tab?.url || changeInfo.url;
  if (!currentUrl || !chrome.sidePanel || typeof chrome.sidePanel.setOptions !== "function") return;
  try {
    const parsed = new URL(currentUrl);
    if (parsed.hostname === "web.whatsapp.com") {
      await chrome.sidePanel.setOptions({
        tabId: tabId,
        path: "sidepanel/sidepanel.html",
        enabled: true
      });
    } else {
      await chrome.sidePanel.setOptions({
        tabId: tabId,
        enabled: false
      });
    }
  } catch (e) {}
});

// Inicializar exclusivamente las pestañas abiertas de WhatsApp Web al arrancar el Service Worker
chrome.tabs.query({ url: ["https://web.whatsapp.com/*", "http://web.whatsapp.com/*"] }, (tabs) => {
  if (Array.isArray(tabs) && chrome.sidePanel && typeof chrome.sidePanel.setOptions === "function") {
    tabs.forEach(async (tab) => {
      if (tab.id) {
        try {
          await chrome.sidePanel.setOptions({
            tabId: tab.id,
            path: "sidepanel/sidepanel.html",
            enabled: true
          });
        } catch (e) {}
      }
    });
  }
});

// Cargar configuración inicial desde chrome.storage.local
chrome.storage.local.get(["serverUrl", "officePin", "operatorName", "sessionToken"], (res) => {
  if (res && res.serverUrl) config.serverUrl = res.serverUrl.replace(/\/+$/, "");
  if (res && res.officePin) config.officePin = res.officePin;
  if (res && res.operatorName) config.operatorName = res.operatorName;
  if (res && res.sessionToken) config.sessionToken = res.sessionToken;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local") {
    if (changes.serverUrl) config.serverUrl = (changes.serverUrl.newValue || "http://127.0.0.1:8080").replace(/\/+$/, "");
    if (changes.officePin) config.officePin = changes.officePin.newValue || "";
    if (changes.operatorName) config.operatorName = changes.operatorName.newValue || "";
    if (changes.sessionToken) config.sessionToken = changes.sessionToken.newValue || "";
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
  { method: "POST", pattern: /^\/api\/radar\/dismiss\/?$/ },
  { method: "POST", pattern: /^\/api\/radar\/undo\/?$/ },
  { method: "GET", pattern: /^\/api\/crm\/rfm\/dormant(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/crm\/support\/metrics(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/search(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/extension\/info(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/presence(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/presence\/heartbeat\/?$/ },
  { method: "POST", pattern: /^\/api\/presence\/leave\/?$/ },
  { method: "POST", pattern: /^\/api\/presence\/kick\/?$/ },
  { method: "GET", pattern: /^\/api\/copilot\/notes\/log(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/notes\/log\/?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/notes\/log\/delete\/?$/ },
  { method: "DELETE", pattern: /^\/api\/copilot\/notes\/log(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/copilot\/assignment\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/network(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/system\/network\/?$/ },
  { method: "POST", pattern: /^\/api\/auth\/verify_pin\/?$/ },
  { method: "POST", pattern: /^\/api\/auth\/handoff\/?$/ },
  { method: "POST", pattern: /^\/api\/auth\/redeem_handoff\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/info(\?.*)?$/ },
  { method: "GET", pattern: /^\/api\/system\/onboarding(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/system\/onboarding\/?$/ },
  { method: "GET", pattern: /^\/api\/system\/update\/check(\?.*)?$/ }
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

  // Recarga en caliente asistida de la extensión con recarga sincronizada de pestañas
  if (request.action === "reload_extension") {
    if (sender && sender.tab) {
      sendResponse({ ok: false, error: "No permitido desde content script" });
      return;
    }
    sendResponse({ ok: true });
    const shouldReloadTabs = request.reloadWhatsAppTabs !== false;

    if (shouldReloadTabs) {
      try {
        chrome.tabs.query({ url: ["https://web.whatsapp.com/*", "http://web.whatsapp.com/*"] }, (tabs) => {
          if (Array.isArray(tabs) && tabs.length > 0) {
            tabs.forEach((tab) => {
              try {
                if (tab.id) chrome.tabs.reload(tab.id);
              } catch (e) {
                console.warn("[WAckup SW] Error al recargar pestaña de WhatsApp Web:", e);
              }
            });
          }
          setTimeout(() => {
            chrome.runtime.reload();
          }, 300);
        });
      } catch (err) {
        console.warn("[WAckup SW] Error en query de pestañas para recarga:", err);
        setTimeout(() => {
          chrome.runtime.reload();
        }, 150);
      }
    } else {
      setTimeout(() => {
        chrome.runtime.reload();
      }, 150);
    }
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

  // Enfocar pestaña existente del Visor Web o abrirla si no existe
  if (request.action === "focus_viewer_tab" || request.action === "open_viewer") {
    const base = (request.baseUrl || config.serverUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
    const targetUrl = request.url || `${base}/index.html`;
    const searchPatterns = [`${base}/*`];
    if (base.includes("127.0.0.1") || base.includes("localhost")) {
      searchPatterns.push("http://127.0.0.1/*", "http://localhost/*");
    }

    chrome.tabs.query({ url: searchPatterns }, (tabs) => {
      const list = Array.isArray(tabs) ? tabs : [];
      const match = list.find(t => t.url && t.url.startsWith(base)) || list[0];
      if (match && match.id) {
        const updateInfo = { active: true };
        if (request.url && match.url !== request.url) {
          updateInfo.url = request.url;
        }
        chrome.tabs.update(match.id, updateInfo, () => {
          if (match.windowId) {
            chrome.windows.update(match.windowId, { focused: true }).catch(() => {});
          }
          sendResponse({ ok: true, reopened: true });
        });
      } else {
        chrome.tabs.create({ url: targetUrl }, () => {
          sendResponse({ ok: true, created: true });
        });
      }
    });
    return true;
  }

  // Obtener / guardar configuración
  if (request.action === "get_config") {
    if (sender && sender.tab) {
      sendResponse({ ok: true, config: { serverUrl: config.serverUrl } });
      return;
    }
    sendResponse({ ok: true, config });
    return;
  }

  if (request.action === "save_config") {
    if (sender && sender.tab) {
      sendResponse({ ok: false, error: "No permitido desde content script" });
      return;
    }
    const newCfg = request.config || {};
    const toSave = {};
    if (typeof newCfg.serverUrl === "string") toSave.serverUrl = newCfg.serverUrl.trim().replace(/\/+$/, "");
    if (typeof newCfg.officePin === "string") toSave.officePin = newCfg.officePin.trim();
    if (typeof newCfg.operatorName === "string") toSave.operatorName = newCfg.operatorName.trim();
    if (typeof newCfg.sessionToken === "string") toSave.sessionToken = newCfg.sessionToken.trim();

    chrome.storage.local.set(toSave, () => {
      Object.assign(config, toSave);
      sendResponse({ ok: true, config });
    });
    return true;
  }

  // Proxy seguro de peticiones HTTP al backend local/LAN
  if (request.action === "backend_fetch") {
    const fromContentScript = Boolean(sender && sender.tab);
    if (fromContentScript) {
      // Content scripts (bridge.js en web.whatsapp.com) solo pueden consultar estado o enviar sincronización
      const isSyncStatus = request.endpoint.startsWith("/api/sync/status") && (!request.options || !request.options.method || request.options.method.toUpperCase() === "GET");
      const isSyncPost = request.endpoint === "/api/sync" && request.options && request.options.method && request.options.method.toUpperCase() === "POST";
      if (!isSyncStatus && !isSyncPost) {
        sendResponse({ ok: false, error: "Acceso no autorizado desde content script" });
        return;
      }
    } else {
      const isExtensionOrigin = sender.id === chrome.runtime.id;
      if (!isExtensionOrigin) {
        sendResponse({ ok: false, error: "Origen no permitido" });
        return;
      }
    }

    const opts = request.options || {};
    const method = (opts.method || "GET").toUpperCase();
    if (!isAllowedRequest(request.endpoint, method)) {
      sendResponse({ ok: false, error: "Endpoint no permitido: " + request.endpoint });
      return;
    }

    const headers = { "Accept": "application/json" };
    const effectiveSession = request.sessionToken || config.sessionToken;
    if (effectiveSession) {
      headers["X-WAckup-Session"] = effectiveSession;
    }
    const effectivePin = request.pin !== undefined ? request.pin : config.officePin;
    if (effectivePin) {
      headers["X-WAckup-Pin"] = effectivePin;
      headers["X-Office-PIN"] = effectivePin;
    }
    const effectiveOp = request.operator !== undefined ? request.operator : config.operatorName;
    if (effectiveOp) {
      headers["X-WAckup-Operator"] = effectiveOp;
      headers["X-Operator-Name"] = effectiveOp;
    }

    const fetchOptions = { method, headers };
    if (method === "POST") {
      fetchOptions.headers["Content-Type"] = "application/json";
      fetchOptions.body = typeof opts.body === "string" ? opts.body : (opts.body ? JSON.stringify(opts.body) : "");
    }

    const base = (request.serverUrl || config.serverUrl || "http://127.0.0.1:8080").replace(/\/+$/, "");
    const targetUrl = base + request.endpoint;

    fetch(targetUrl, fetchOptions)
      .then(async (res) => {
        const text = await res.text();
        let json = null;
        try {
          json = JSON.parse(text);
        } catch (e) {
          json = null;
        }

        if (res.ok && json && json.session_token) {
          config.sessionToken = json.session_token;
          chrome.storage.local.set({ sessionToken: json.session_token });
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
