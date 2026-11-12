// WAckup 3.0 - Puente ligero exclusivo para el Visor Web local (127.0.0.1 / localhost)
// Permite activar la pestaña existente del visor desde el ícono de la bandeja del sistema sin duplicar pestañas.

(function() {
  "use strict";

  window.addEventListener("message", (event) => {
    if (event.source !== window || !event.data || event.data.source !== "wackup-viewer") return;
    if (event.data.action === "focus_my_tab") {
      try {
        chrome.runtime.sendMessage({ action: "focus_my_tab" }, (res) => {
          void chrome.runtime.lastError;
          if (res && res.ok) {
            window.postMessage({ source: "wackup-viewer-ext", action: "tab_focused_ok" }, window.location.origin);
          }
        });
      } catch (e) {}
    }
  });
})();
