"""
sse_hub.py - Bus de eventos en tiempo real (Server-Sent Events) y Semáforo Anti-Colisión para WAckup.
Permite comunicación instantánea entre todas las instancias del Visor Web y sidePanels de la LAN:
- Semáforo visual anti-colisión cuando dos compañeros visualizan la misma conversación.
- Notificaciones en vivo de actualización de tickets, notas, etiquetas y embudo CRM (< 1s).
- Sincronización inteligente de lotes entre múltiples terminales de WhatsApp Web.
"""

import json
import time
import queue
import logging
import threading
from typing import Dict, Any, List, Optional

logger = logging.getLogger("wackup.sse")

class SSEHub:
    """Administrador centralizado thread-safe para Server-Sent Events y Presencia de Operadores."""

    def __init__(self, presence_ttl: float = 25.0, ping_interval: float = 15.0):
        self.presence_ttl = presence_ttl
        self.ping_interval = ping_interval
        self._subscribers: List[queue.Queue] = []
        self._subscribers_lock = threading.Lock()
        
        # Estructura: { canonical_chat_id: { client_id: { "operator": str, "source": str, "expires_at": float } } }
        self._presence: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._presence_lock = threading.Lock()
        
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._start_worker()

    def _start_worker(self):
        """Inicia el hilo en segundo plano para ping periódico y limpieza de presencias expiradas."""
        self._worker_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="WAckup-SSEHeartbeat"
        )
        self._worker_thread.start()

    def subscribe(self, client_info: Optional[Dict[str, Any]] = None) -> queue.Queue:
        """Registra un nuevo suscriptor SSE y devuelve su cola de mensajes thread-safe."""
        q = queue.Queue(maxsize=100)
        with self._subscribers_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        """Elimina un suscriptor desconectado."""
        with self._subscribers_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def broadcast(self, event_name: str, data: Any):
        """
        Emite un evento estructurado SSE a todos los clientes conectados.
        Formato:
        event: {event_name}
        data: {json_str}
        """
        if self._stop_event.is_set():
            return

        if isinstance(data, dict) and "event" not in data:
            data["event"] = event_name

        payload = f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        with self._subscribers_lock:
            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead_queues.append(q)
                except Exception:
                    dead_queues.append(q)

            for dq in dead_queues:
                if dq in self._subscribers:
                    self._subscribers.remove(dq)

    def broadcast_comment(self, comment: str):
        """Envía un comentario SSE (ej. ': ping') para mantener vivo el socket HTTP."""
        if self._stop_event.is_set():
            return
        payload = f": {comment}\n\n"
        with self._subscribers_lock:
            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead_queues.append(q)
                except Exception:
                    dead_queues.append(q)

            for dq in dead_queues:
                if dq in self._subscribers:
                    self._subscribers.remove(dq)

    def record_presence(self, chat_id: str, operator: str, client_id: str, source: str = "visor"):
        """
        Registra el latido (heartbeat) de un operador enfocado en un chat.
        TTL: 25 segundos. Dispara un evento 'presence_update' en el bus SSE.
        """
        if not chat_id:
            return

        clean_chat = str(chat_id).strip()
        op_name = (operator or "Operador").strip()
        c_id = str(client_id).strip() if client_id else f"client_{int(time.time()*1000)}"
        src = "whatsapp_web" if source == "whatsapp_web" else "visor"
        now = time.time()
        expires_at = now + self.presence_ttl

        presence_changed = False
        with self._presence_lock:
            if clean_chat not in self._presence:
                self._presence[clean_chat] = {}

            old_entry = self._presence[clean_chat].get(c_id)
            if not old_entry or old_entry.get("operator") != op_name:
                presence_changed = True

            self._presence[clean_chat][c_id] = {
                "operator": op_name,
                "client_id": c_id,
                "source": src,
                "expires_at": expires_at,
                "chat_id": clean_chat
            }

        # Notificar a los suscriptores si hay cambios o refrescar
        active_map = self.get_active_presence()
        self.broadcast("presence_update", {
            "chat_id": clean_chat,
            "operator": op_name,
            "client_id": c_id,
            "source": src,
            "active_presence": active_map
        })

    def remove_presence(self, client_id: str, chat_id: Optional[str] = None):
        """Remueve la presencia de un cliente al cerrar pestaña o cambiar de chat."""
        if not client_id:
            return
        changed = False
        with self._presence_lock:
            chats_to_check = [chat_id] if chat_id else list(self._presence.keys())
            for ch in chats_to_check:
                if ch in self._presence and client_id in self._presence[ch]:
                    del self._presence[ch][client_id]
                    if not self._presence[ch]:
                        del self._presence[ch]
                    changed = True

        if changed:
            active_map = self.get_active_presence()
            self.broadcast("presence_update", {
                "chat_id": chat_id or "",
                "removed_client_id": client_id,
                "active_presence": active_map
            })

    def get_active_presence(self) -> Dict[str, Any]:
        """
        Devuelve el mapa limpio de presencia activa sin entradas vencidas:
        { chat_id: [ {"operator": "Ana", "client_id": "...", "source": "...", "expires_at": ...} ] }
        Y una clave singular representativa por chat para fácil consumo.
        """
        now = time.time()
        result: Dict[str, Any] = {}
        with self._presence_lock:
            for ch, viewers in list(self._presence.items()):
                active_viewers = [
                    v for v in viewers.values()
                    if v.get("expires_at", 0) > now
                ]
                if active_viewers:
                    # Incluir lista completa y el primer operador activo
                    first = active_viewers[0]
                    result[ch] = {
                        "operator": first["operator"],
                        "client_id": first["client_id"],
                        "source": first["source"],
                        "expires_at": first["expires_at"],
                        "viewers": active_viewers,
                        "count": len(active_viewers)
                    }
        return result

    def _purge_expired_presence(self) -> bool:
        """Elimina entradas cuya vigencia (TTL) haya expirado. Retorna True si hubo purgas."""
        now = time.time()
        purged = False
        with self._presence_lock:
            for ch in list(self._presence.keys()):
                viewers = self._presence[ch]
                expired_keys = [
                    cid for cid, v in viewers.items()
                    if v.get("expires_at", 0) <= now
                ]
                for cid in expired_keys:
                    del viewers[cid]
                    purged = True
                if not viewers:
                    del self._presence[ch]
                    purged = True
        return purged

    def _heartbeat_loop(self):
        """Ciclo continuo que envía comentarios ping y purga presencias expiradas cada 15s."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.ping_interval)
            if self._stop_event.is_set():
                break

            # 1. Purgar presencia
            if self._purge_expired_presence():
                active_map = self.get_active_presence()
                self.broadcast("presence_update", {
                    "event": "presence_expired",
                    "active_presence": active_map
                })

            # 2. Enviar ping heartbeat
            self.broadcast_comment("ping")

    def shutdown(self):
        """Finaliza el hub, despierta todos los clientes SSE y vacía recursos de manera inmediata."""
        self._stop_event.set()
        with self._subscribers_lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(None)  # Centinela de fin
                except Exception:
                    pass
            self._subscribers.clear()


# Instancia singleton compartida de SSE Hub
_HUB_INSTANCE: Optional[SSEHub] = None
_HUB_LOCK = threading.Lock()

def get_sse_hub() -> SSEHub:
    """Devuelve la instancia global singleton de SSEHub."""
    global _HUB_INSTANCE
    with _HUB_LOCK:
        if _HUB_INSTANCE is None:
            _HUB_INSTANCE = SSEHub()
        return _HUB_INSTANCE
