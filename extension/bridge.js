// Puente de comunicación entre el contexto de la página (MAIN) y el Background Service Worker
(function() {
  'use strict';

  const PAGE_ORIGIN = "https://web.whatsapp.com";

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== PAGE_ORIGIN) return;
    if (!event.data) return;

    // Solicitud de recarga en caliente de la extensión y la pestaña
    if (event.data.type === "BACKUPWA_RELOAD_EXT") {
      try {
        chrome.runtime.sendMessage({ action: "reload_extension" });
      } catch (e) {
        console.warn("[BackupWA] Error al solicitar recarga de extensión:", e);
      }
      return;
    }

    if (event.data.type !== "BACKUPWA_REQ") return;

    const { reqId, endpoint, options } = event.data;
    if (typeof reqId !== "string" || typeof endpoint !== "string") return;

    chrome.runtime.sendMessage({
      action: "backend_fetch",
      endpoint: endpoint,
      options: options
    }, (response) => {
      // Inyectar la versión de la extensión actualmente cargada en el navegador
      if (response && response.data && typeof response.data === "object") {
        try {
          response.data.installed_extension_version = chrome.runtime.getManifest().version;
        } catch (e) {}
      }

      // Responder solo al origen de WhatsApp Web (nunca a "*")
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
})();
