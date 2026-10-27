// Service Worker de la extensión (inmune a la Content Security Policy de WhatsApp Web)
const BACKEND_BASE = "http://127.0.0.1:8080";

// Lista blanca de endpoints permitidos: el puente NO debe funcionar como proxy genérico,
// ya que cualquier script de la página de WhatsApp Web puede enviar mensajes al bridge.
const ALLOWED_ENDPOINTS = [
  { method: "GET", pattern: /^\/api\/sync\/status(\?.*)?$/ },
  { method: "POST", pattern: /^\/api\/sync\/?$/ }
];

function isAllowedRequest(endpoint, method) {
  if (typeof endpoint !== "string") return false;
  return ALLOWED_ENDPOINTS.some(rule => rule.method === method && rule.pattern.test(endpoint));
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // Manejador de recarga asistida de la extensión en caliente
  if (request.action === "reload_extension") {
    const senderUrl = (sender && (sender.url || (sender.tab && sender.tab.url))) || "";
    if (!senderUrl.startsWith("https://web.whatsapp.com/")) {
      sendResponse({ ok: false, error: "Origen no permitido" });
      return;
    }
    sendResponse({ ok: true });
    setTimeout(() => {
      chrome.runtime.reload();
    }, 150);
    return;
  }

  if (request.action !== "backend_fetch") return;

  // Solo aceptar mensajes provenientes de pestañas de WhatsApp Web
  const senderUrl = (sender && (sender.url || (sender.tab && sender.tab.url))) || "";
  if (!senderUrl.startsWith("https://web.whatsapp.com/")) {
    sendResponse({ ok: false, error: "Origen no permitido" });
    return;
  }

  const opts = request.options || {};
  const method = (opts.method || "GET").toUpperCase();
  if (!isAllowedRequest(request.endpoint, method)) {
    sendResponse({ ok: false, error: "Endpoint no permitido" });
    return;
  }

  // Reconstruir las opciones con un conjunto mínimo y controlado de campos
  const options = { method };
  if (method === "POST") {
    options.headers = { "Content-Type": "application/json" };
    options.body = typeof opts.body === "string" ? opts.body : (opts.body ? JSON.stringify(opts.body) : "");
  }

  fetch(BACKEND_BASE + request.endpoint, options)
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
      sendResponse({
        ok: false,
        error: err.message || "Error al conectar con el servidor local"
      });
    });

  return true; // Mantiene el canal abierto para la respuesta asíncrona
});
