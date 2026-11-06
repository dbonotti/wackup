import sqlite3
import json
import os
import sys
import shutil
import threading
import subprocess
import time
import re
import csv
import io
import base64
import mimetypes
import webbrowser
import traceback
import html
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime

# En Windows con pythonw.exe, sys.stdout y sys.stderr se inicializan como None.
# Redirigir a os.devnull para prevenir caídas ('NoneType' object has no attribute 'write')
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        sys.stdout = io.StringIO()
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        sys.stderr = io.StringIO()

import db_manager
import media_manager
import config
import sse_hub
import updater
from config import (
    BASE_DIR, DATA_DIR, DB_FILE, MEDIA_DIR, BACKUP_DIR, LOG_DIR,
    SYNC_LOG_PATH, ensure_data_dirs, migrate_legacy_data
)

PORT = 8080
_last_sync_epoch = 0
_server_instance = None
_server_stopping = False
_is_restarting = False

def append_sync_log(entry_text):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(SYNC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry_text + "\n")
    except Exception:
        pass

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

def get_html_ui():
    """Retorna el contenido de web/index.html de forma dinámica."""
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<!DOCTYPE html><html><body><h1>WAckup Visor</h1></body></html>"

# Mantener compatibilidad con scripts o tests que lean HTML_UI
HTML_UI = get_html_ui()

def _file_to_data_uri(rel_path, default_mime="application/octet-stream", max_size_mb=15):
    """Convierte un archivo local en un Data URI base64 si existe y no supera el límite."""
    try:
        safe_rel = rel_path.lstrip("/").replace("/", os.sep)
        full_path = os.path.join(BASE_DIR, safe_rel)
        if not os.path.isfile(full_path):
            alt_path = os.path.join(DATA_DIR, safe_rel)
            if os.path.isfile(alt_path):
                full_path = alt_path
            else:
                return None
        if os.path.getsize(full_path) > max_size_mb * 1024 * 1024:
            return None
        mime, _ = mimetypes.guess_type(full_path)
        mime = mime or default_mime
        with open(full_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

def generate_chat_html_export(chat_id, embed_media=False, db_path=None):
    """Genera un archivo HTML autocontenido para imprimir o archivar."""
    conn = db_manager.get_connection(db_path or config.get_default_db())
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            c.nombre AS chat_nombre,
            meta.custom_name AS custom_name,
            ct.nombre AS ct_nombre,
            ct.push_name AS ct_push,
            ct.numero AS ct_numero,
            ct_can.nombre AS ct_can_nombre,
            ct_can.push_name AS ct_can_push,
            ct_can.numero AS ct_can_numero,
            ca.canonical_id
        FROM (SELECT ? AS chat_id) m
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        LEFT JOIN crm_contact_meta meta ON m.chat_id = meta.chat_id
        LEFT JOIN contactos ct ON m.chat_id = ct.contact_id
        LEFT JOIN contact_aliases ca ON m.chat_id = ca.alias_id
        LEFT JOIN contactos ct_can ON ca.canonical_id = ct_can.contact_id
    """, (chat_id,))
    row = cur.fetchone()
    if row:
        row_d = dict(row)
        phone = (
            (chat_id.split("@")[0] if ("@c.us" in chat_id or "@s.whatsapp.net" in chat_id) else None)
            or row_d.get("ct_numero")
            or row_d.get("ct_can_numero")
            or (row_d.get("canonical_id", "").split("@")[0] if row_d.get("canonical_id") and ("@c.us" in row_d["canonical_id"] or "@s.whatsapp.net" in row_d["canonical_id"]) else None)
        )
        display_name = db_manager.get_best_contact_display(
            custom_name=row_d.get("custom_name"),
            contact_name=row_d.get("ct_nombre") or row_d.get("ct_can_nombre") or row_d.get("chat_nombre"),
            push_name=row_d.get("ct_push") or row_d.get("ct_can_push"),
            phone_number=phone,
            chat_id=chat_id
        )
    else:
        display_name = chat_id

    contact_map = db_manager.build_contact_map(cur)
    cur.execute("""
        SELECT 
            m.msg_id, m.es_mio, m.fecha_utc, m.timestamp, m.tipo, m.contenido,
            m.archivo_local, m.mimetype, m.thumbnail,
            m.quoted_msg_id, m.quoted_author, m.quoted_body, m.reenviado, m.eliminado,
            COALESCE(ct.nombre, ct.push_name, m.remitente) AS remitente_nombre,
            m.remitente AS remitente_jid
        FROM mensajes m
        LEFT JOIN contactos ct ON m.remitente = ct.contact_id
        WHERE m.chat_id = ?
        ORDER BY m.timestamp ASC
    """, (chat_id,))
    messages = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Mapa para resolver citas que no traían el texto incrustado
    msg_map = {}
    for m in messages:
        mid = m.get("msg_id") or ""
        msg_map[mid] = m
        for p in mid.split("_"):
            if len(p) >= 12:
                msg_map[p] = m

    is_group = chat_id.endswith("@g.us")
    
    html_parts = [f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Respaldo: {html.escape(display_name)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #111b21; }}
    .container {{ max-width: 800px; margin: 0 auto; background: #efeae2; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    .header {{ background: #00a884; color: #ffffff; padding: 18px 24px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .header p {{ margin: 4px 0 0 0; font-size: 13px; opacity: 0.9; }}
    .messages {{ padding: 20px; display: flex; flex-direction: column; gap: 6px; }}
    .date-badge {{ align-self: center; background: #ffffff; color: #54656f; font-size: 11px; padding: 4px 12px; border-radius: 6px; margin: 10px 0; }}
    .msg {{ max-width: 70%; padding: 7px 10px; border-radius: 8px; font-size: 14px; line-height: 19px; box-shadow: 0 1px 0.5px rgba(0,0,0,0.13); word-break: break-word; }}
    .incoming {{ align-self: flex-start; background: #ffffff; }}
    .outgoing {{ align-self: flex-end; background: #d9fdd3; }}
    .sender {{ font-size: 12px; font-weight: bold; color: #1f7a63; margin-bottom: 2px; }}
    .forwarded {{ font-size: 12px; font-style: italic; color: #667781; margin-bottom: 2px; }}
    .quote {{ background: rgba(0,0,0,0.05); border-left: 3px solid #00a884; padding: 3px 7px; border-radius: 4px; font-size: 12px; color: #54656f; margin-bottom: 4px; }}
    .time {{ font-size: 10.5px; color: #667781; text-align: right; margin-top: 4px; }}
    .media-img {{ max-width: 300px; border-radius: 6px; display: block; margin-bottom: 4px; }}
    @media print {{ body {{ background: #fff; padding: 0; }} .container {{ box-shadow: none; border-radius: 0; }} }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{html.escape(display_name)}</h1>
      <p>{len(messages)} mensajes respaldados · Exportado el {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
    </div>
    <div class="messages">
"""]

    last_date = None
    for m in messages:
        msg_date = m["fecha_utc"].split(" ")[0] if m.get("fecha_utc") else "Fecha desconocida"
        if msg_date != last_date:
            html_parts.append(f'<div class="date-badge">{msg_date}</div>')
            last_date = msg_date

        is_mine = bool(m.get("es_mio")) or (m.get("msg_id", "").startswith("true_"))
        cls = "outgoing" if is_mine else "incoming"
        sender_html = ""
        if is_group and not is_mine:
            name = m["remitente_nombre"] or m["remitente_jid"] or "Desconocido"
            sender_html = f'<div class="sender">{html.escape(name)}</div>'

        forwarded_html = ""
        if m.get("reenviado") and m["reenviado"] > 0:
            fwd_text = "↪↪ Reenviado muchas veces" if m["reenviado"] >= 5 else "↪ Reenviado"
            forwarded_html = f'<div class="forwarded">{fwd_text}</div>'

        quote_html = ""
        q_body = m.get("quoted_body")
        if not q_body and m.get("quoted_msg_id"):
            qid = m["quoted_msg_id"]
            if qid in msg_map and msg_map[qid].get("contenido"):
                q_body = msg_map[qid]["contenido"]
        if q_body:
            q_body_resolved = db_manager.resolve_mentions_in_text(q_body, contact_map)
            quote_html = f'<div class="quote">{html.escape(q_body_resolved)}</div>'

        media_html = ""
        if m.get("archivo_local"):
            rel_url = f'/{m["archivo_local"]}'
            src_uri = (_file_to_data_uri(m["archivo_local"], m.get("mimetype") or "application/octet-stream") if embed_media else None) or rel_url
            if m.get("tipo") in ("image", "sticker"):
                media_html = f'<img class="media-img" src="{src_uri}">'
            elif m.get("tipo") in ("audio", "ptt"):
                media_html = f'<audio controls src="{src_uri}"></audio>'
            else:
                media_html = f'<div>📄 <a href="{rel_url}">{html.escape(m["archivo_local"].split("/")[-1])}</a></div>'
        elif m.get("thumbnail") and str(m.get("thumbnail")).startswith("data:image"):
            media_html = f'<img class="media-img" src="{m["thumbnail"]}">'

        time_str = m["fecha_utc"].split(" ")[1][:5] if m.get("fecha_utc") else ""
        raw_c = m.get("contenido") or ""
        content_clean = "" if db_manager.is_binary_or_base64_content(m.get("tipo"), raw_c) else db_manager.resolve_mentions_in_text(raw_c.replace("\ufffd", "").strip(), contact_map)

        is_del = bool(m.get("eliminado") or m.get("tipo") == "revoked")
        del_badge = '<div class="deleted-badge" style="font-size: 11px; font-weight: 600; color: #dc2626; margin-bottom: 3px;">🚫 Eliminado</div>' if (is_del and m.get("tipo") != "revoked") else ""
        text_style = ' style="text-decoration: line-through; text-decoration-color: #dc2626;"' if (is_del and m.get("tipo") != "revoked") else ""

        html_parts.append(f"""
        <div class="msg {cls}">
          {sender_html}
          {forwarded_html}
          {del_badge}
          {quote_html}
          {media_html}
          {f'<div{text_style}>{html.escape(content_clean)}</div>' if content_clean else ''}
          <div class="time">{time_str}</div>
        </div>
        """)

    html_parts.append("""
    </div>
  </div>
</body>
</html>
""")
    return "".join(html_parts)

_sync_lock = threading.Lock()
_ui_lock = threading.Lock()
_sync_batch_count = 0
_total_messages_synced = 0
_last_sync_timestamp = None
_last_sync_msgs_count = 0
_is_saving_sync = False
_last_sync_account = ""
_initial_total_msgs = 0
_anim_frame = 0
_last_sync_status_text = "[●] 🟡 Esperando a WhatsApp Web... (Abre WhatsApp en tu navegador)"
_last_update_status_text = "Presiona [U] para comprobar"
_update_in_progress = False
_server_url = None
_server_instance = None
_exit_code = 0
SERVER_SESSION_ID = f"sess_{int(time.time() * 1000)}"
_last_client_ping = 0
_viewer_lock = threading.Lock()
_active_web_sse_clients = 0
_active_web_viewers = {}
_web_sse_queues = {}
_last_web_viewer_ping = 0.0

def record_web_viewer_ping(client_id=None):
    """Registra actividad reciente proveniente específicamente de una pestaña abierta del Visor Web."""
    global _last_web_viewer_ping
    now = time.time()
    with _viewer_lock:
        _last_web_viewer_ping = now
        if client_id:
            _active_web_viewers[client_id] = now

def remove_web_viewer(client_id):
    """Da de baja inmediatamente una pestaña del Visor Web cuando se cierra en el navegador."""
    global _last_web_viewer_ping
    if not client_id:
        return
    q_to_wake = None
    with _viewer_lock:
        _active_web_viewers.pop(client_id, None)
        q_to_wake = _web_sse_queues.pop(client_id, None)
        now = time.time()
        stale = [cid for cid, ts in _active_web_viewers.items() if (now - ts) > 15.0]
        for cid in stale:
            _active_web_viewers.pop(cid, None)
        if not _active_web_viewers and not _web_sse_queues and _active_web_sse_clients <= 0:
            _last_web_viewer_ping = 0.0
    if q_to_wake is not None:
        try:
            q_to_wake.put_nowait(None)
        except Exception:
            pass

def has_active_web_viewer(max_age=15.0):
    """Indica si existe al menos una pestaña del Visor Web conectada por SSE o activa recientemente."""
    now = time.time()
    with _viewer_lock:
        stale = [cid for cid, ts in _active_web_viewers.items() if (now - ts) > max_age]
        for cid in stale:
            _active_web_viewers.pop(cid, None)
        if _active_web_sse_clients > 0 or len(_web_sse_queues) > 0 or len(_active_web_viewers) > 0:
            return True
        return _last_web_viewer_ping > 0 and (now - _last_web_viewer_ping) <= max_age

_last_viewer_focused_ack = 0.0

def record_viewer_focused_ack():
    global _last_viewer_focused_ack
    with _viewer_lock:
        _last_viewer_focused_ack = time.time()

def focus_viewer_window_win32():
    """
    Trae al frente la ventana del navegador cuya pestaña activa sea 'WAckup - Visor'.
    Usa firmas Win32 de 64 bits explícitas y AttachThreadInput para sortear el bloqueo de foco de Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        exact_matches = []

        def _enum_cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = (title_buf.value or "").lower()

            # Coincidir exclusivamente con el título real del Visor Web (<title>WAckup - Visor</title>)
            if "wackup - visor" in title:
                exact_matches.append(hwnd)
            return True

        cb_ref = WNDENUMPROC(_enum_cb)
        user32.EnumWindows(cb_ref, 0)

        if not exact_matches:
            return False

        target_hwnd = exact_matches[0]
        SW_RESTORE = 9
        if user32.IsIconic(target_hwnd):
            user32.ShowWindow(target_hwnd, SW_RESTORE)

        fg_hwnd = user32.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        attached = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            user32.keybd_event(0, 0, 0, 0)
            user32.SetForegroundWindow(target_hwnd)
            user32.BringWindowToTop(target_hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
        return True
    except Exception:
        return False

def open_or_focus_viewer(url):
    """
    Muestra el Visor Web de forma 100% determinista y sin acumular pestañas:
    1) Si 'WAckup - Visor' ya es la pestaña visible en alguna ventana del navegador -> la trae al frente al instante.
    2) Si está abierta en segundo plano detrás de otra pestaña -> solicita a la extensión activar esa pestaña y enfoca la ventana.
    3) Si tras 320 ms no pudo enfocarse (ej. navegador sin extensión o pestaña cerrada) -> abre con '?from_tray=1' y cierra cualquier pestaña previa oculta vía BroadcastChannel.
    """
    base_url = url.rstrip("/")
    if not has_active_web_viewer(max_age=15.0):
        webbrowser.open(base_url, new=0, autoraise=True)
        return True

    # Caso 1: La pestaña del visor ya es la pestaña activa en su ventana del navegador
    if focus_viewer_window_win32():
        return False

    # Caso 2: Está oculta detrás de otra pestaña -> pedir cambio de pestaña vía SSE + extensión
    req_ts = time.time()
    try:
        sse_hub.get_sse_hub().broadcast("focus_viewer", {"timestamp": int(req_ts * 1000)})
    except Exception:
        pass

    deadline = time.time() + 0.35
    while time.time() < deadline:
        time.sleep(0.05)
        with _viewer_lock:
            acked = (_last_viewer_focused_ack >= req_ts)
        if focus_viewer_window_win32() or acked:
            focus_viewer_window_win32()
            return False

    # Caso 3: Fallback garantizado -> abrir en primer plano y pedir por BroadcastChannel cerrar la pestaña oculta anterior
    webbrowser.open(f"{base_url}/?from_tray=1", new=0, autoraise=True)
    return True

def _running_instance_has_active_viewer(port):
    """Consulta a una instancia ya activa en `port` si ya tiene una pestaña del visor abierta."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/sync/status?internal_probe=1",
            headers={"Origin": f"http://127.0.0.1:{port}"}
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                return bool(payload.get("active_web_viewer"))
    except Exception:
        pass
    return False

def get_disk_extension_version():
    """Lee la versión de la extensión declarada en extension/manifest.json."""
    try:
        manifest_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension", "manifest.json")
        if os.path.isfile(manifest_file):
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("version", "1.0.0")
    except Exception:
        pass
    return "1.0.0"

def run_git_cmd(args, timeout=15):
    """Ejecuta un comando de Git garantizando safe.directory y devolviendo CompletedProcess."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = ["git", "-c", "safe.directory=*"] + args
    try:
        res = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 and "dubious ownership" in (res.stderr or ""):
            safe_path = base_dir.replace("\\", "/")
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", safe_path], capture_output=True)
            res = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True, timeout=timeout)
        return res
    except Exception as e:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))

def render_dashboard(url=None):
    """Dibuja el panel de control completo sin scroll vertical en la consola."""
    global _server_url
    with _ui_lock:
        if url:
            _server_url = url
        current_url = _server_url or f"http://127.0.0.1:{PORT}"
        os.system("cls" if os.name == "nt" else "clear")
        lines = [
            "========================================================================",
            "                   WACKUP - PANEL DE CONTROL & VISOR                    ",
            "========================================================================",
            f"  Dirección local:       {current_url}",
            f"  Perfil de trabajo:     {config.get_account_profile().upper()}",
            "",
            "  Atajos en esta consola:",
            "    [V]        Abrir el visor en el navegador",
            "    [L]        Abrir ventana de logs de sincronización en vivo",
            "    [U]        Buscar y aplicar actualizaciones de GitHub",
            "    [R]        Reiniciar el visor (recargar cambios)",
            "    [P]        Cambiar perfil de uso (Backup / Soporte / Ventas / Full)",
            "    [Ctrl + C] Detener el servidor",
            "========================================================================",
            f"  Actualizaciones:       {_last_update_status_text}",
            "========================================================================",
            f"  Estado: {_last_sync_status_text}"
        ]
        # Dejar el cursor al final de la última línea con \r para actualizaciones directas
        if sys.stdout and hasattr(sys.stdout, "write"):
            try:
                sys.stdout.write("\n".join(lines) + "\r")
                sys.stdout.flush()
            except Exception:
                pass

def get_system_update_status():
    """Comprueba el estado de actualizaciones mediante el actualizador nativo HTTPS."""
    try:
        return updater.check_for_updates()
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "message": str(e)
        }


def check_shortcut_exists(action: str) -> bool:
    """Verifica si existe el acceso directo en Escritorio o Inicio."""
    if sys.platform != "win32":
        return False
    try:
        if action == "desktop":
            user_profile = os.environ.get("USERPROFILE", "")
            desk = os.path.join(user_profile, "Desktop", "WAckup.lnk")
            onedrive_desk = os.path.join(user_profile, "OneDrive", "Escritorio", "WAckup.lnk")
            onedrive_desk_en = os.path.join(user_profile, "OneDrive", "Desktop", "WAckup.lnk")
            return os.path.isfile(desk) or os.path.isfile(onedrive_desk) or os.path.isfile(onedrive_desk_en)
        elif action == "startup":
            appdata = os.environ.get("APPDATA", "")
            startup = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup", "WAckup.lnk")
            return os.path.isfile(startup)
    except Exception:
        pass
    return False


def manage_windows_shortcut(action: str, enable: bool = True) -> dict:
    """
    Crea o elimina accesos directos en el Escritorio o Inicio de Windows
    usando PowerShell WScript.Shell estándar sin dependencias externas.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "Acción disponible únicamente en sistemas Windows."}

    base_dir = os.path.dirname(os.path.abspath(__file__))
    bat_path = os.path.join(base_dir, "iniciar_visor.bat")
    icon_path = os.path.join(base_dir, "assets", "favicon.jpg")

    target = bat_path

    if action == "desktop":
        ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$desk = $ws.SpecialFolders("Desktop")
$sc = $ws.CreateShortcut("$desk\\WAckup.lnk")
$sc.TargetPath = "{target}"
$sc.WorkingDirectory = "{base_dir}"
$sc.WindowStyle = 7
$sc.Description = "WAckup 3.0 - Copiloto CRM y Respaldo Local"
if (Test-Path "{icon_path}") {{ $sc.IconLocation = "{icon_path},0" }}
$sc.Save()
'''
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                                 capture_output=True, text=True, timeout=8)
            if res.returncode == 0:
                return {"ok": True, "message": "Acceso directo en el Escritorio creado exitosamente."}
            else:
                return {"ok": False, "error": f"Error PowerShell: {res.stderr.strip()}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "startup":
        if enable:
            ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$startup = $ws.SpecialFolders("Startup")
$sc = $ws.CreateShortcut("$startup\\WAckup.lnk")
$sc.TargetPath = "{target}"
$sc.WorkingDirectory = "{base_dir}"
$sc.WindowStyle = 7
$sc.Description = "WAckup 3.0 Autoarranque"
if (Test-Path "{icon_path}") {{ $sc.IconLocation = "{icon_path},0" }}
$sc.Save()
'''
            try:
                res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                                     capture_output=True, text=True, timeout=8)
                if res.returncode == 0:
                    return {"ok": True, "message": "Inicio automático con Windows activado correctamente."}
                else:
                    return {"ok": False, "error": f"Error PowerShell: {res.stderr.strip()}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        else:
            ps_script = '''
$ws = New-Object -ComObject WScript.Shell
$startup = $ws.SpecialFolders("Startup")
$path = "$startup\\WAckup.lnk"
if (Test-Path $path) { Remove-Item $path -Force }
'''
            try:
                res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                                     capture_output=True, text=True, timeout=8)
                return {"ok": True, "message": "Inicio automático desactivado."}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Acción de atajo no reconocida: '{action}'"}

def check_and_apply_update(url):
    """Verifica y aplica actualizaciones nativas por HTTPS (sin requerir Git)."""
    global _last_update_status_text, _update_in_progress
    if _update_in_progress:
        return
    _update_in_progress = True

    now_str = datetime.now().strftime("%H:%M:%S")
    _last_update_status_text = f"Consultando actualizaciones en GitHub HTTPS... [{now_str}]"
    render_dashboard(url)

    check = updater.check_for_updates()
    now_str = datetime.now().strftime("%H:%M:%S")

    if not check.get("ok"):
        err_desc = check.get("message") or "Error consultando GitHub"
        append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Update Error] {err_desc}")
        _last_update_status_text = f"[Error] {err_desc[:55]} ({now_str})"
        _update_in_progress = False
        render_dashboard(url)
        return

    status = check.get("status")
    if status == "dev_branch":
        _last_update_status_text = f"[En rama: {check.get('branch')}] Actualizaciones pausadas en desarrollo."
        _update_in_progress = False
        render_dashboard(url)
        return

    if status != "update_available":
        _last_update_status_text = f"[✓] ¡Sistema al día ({check.get('current_version')})! (comprobado {now_str})"
        _update_in_progress = False
        render_dashboard(url)
        return

    latest_ver = check.get("latest_version")
    _last_update_status_text = f"[!] Nueva versión v{latest_ver} disponible"

    os.system("cls" if os.name == "nt" else "clear")
    print("========================================================================")
    print(f"       [!] NUEVA VERSIÓN DISPONIBLE EN GITHUB: v{latest_ver}")
    print("========================================================================")
    if check.get("release_notes"):
        print("Notas de la versión:")
        print(check["release_notes"][:600])
        print("========================================================================")
    print("Seguridad garantizada: se creará un snapshot previo de whatsapp_backup.db.")
    print("")
    try:
        resp = input("¿Deseas aplicar la actualización ahora? (S/N) [S]: ").strip().upper()
    except Exception:
        resp = "N"

    if resp in ("", "S", "SI"):
        print("\nDescargando y aplicando actualización segura...")
        res = updater.apply_update(download_url=check.get("download_url"), restart=False)
        if res.get("ok"):
            print("\n" + "=" * 65)
            print(res.get("message", "¡Actualización completada con éxito!"))
            print("Reiniciando el visor automáticamente para aplicar cambios...")
            print("=" * 65)
            time.sleep(1.2)
            restart_current_process()
        else:
            err_msg = res.get("error", "Error desconocido")
            print(f"      [Error] {err_msg}")
            append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Update Apply Error] {err_msg}")
            _last_update_status_text = f"[Error] {err_msg[:50]} ({datetime.now().strftime('%H:%M:%S')})"
            print("\nPresiona Enter para volver al Panel de Control...")
            try:
                input()
            except Exception:
                pass
    else:
        _last_update_status_text = f"[Aviso] Versión v{latest_ver} pendiente de aplicar"

    _update_in_progress = False
    render_dashboard(url)

def open_logs_window():
    """Abre la ventana dedicada de logs en una consola independiente."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    bat_path = os.path.join(base_dir, "ver_logs.bat")
    try:
        if os.name == "nt" and os.path.isfile(bat_path):
            subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif os.name == "nt":
            ps_cmd = (
                "$Host.UI.RawUI.WindowTitle = 'Logs de Sincronizacion - WAckup'; "
                f"Get-Content -Path '{SYNC_LOG_PATH}' -Wait -Tail 50"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
    except Exception:
        pass

def confirm_and_restart(url=None):
    """Solicita confirmación al usuario antes de reiniciar el visor pausando el animador."""
    global _update_in_progress
    _update_in_progress = True
    try:
        import msvcrt
        print("\n\n" + "=" * 54)
        print("  ¿Deseas reiniciar el visor? (S/N) [S]: ", end="", flush=True)

        # Limpiar cualquier tecla previa residual en el buffer
        while msvcrt.kbhit():
            msvcrt.getch()

        ans = msvcrt.getch()
        if ans in (b"\x00", b"\xe0"):
            msvcrt.getch()
            print("N\n  Reinicio cancelado.")
            print("=" * 54 + "\n")
            _update_in_progress = False
            return

        if ans in (b"s", b"S", b"\r", b"\n", b"y", b"Y", b"r", b"R"):
            print("S\n" + "=" * 54)
            restart_current_process()
        else:
            try:
                char_str = ans.decode("latin-1", errors="ignore")
                print(f"{char_str}\n  Reinicio cancelado.")
            except Exception:
                print("N\n  Reinicio cancelado.")
            print("=" * 54 + "\n")
            _update_in_progress = False
    except Exception as e:
        print(f"\nReinicio cancelado ({e}).")
        _update_in_progress = False

def restart_current_process():
    """Solicita el reinicio ordenado del visor WAckup."""
    global _server_instance, _exit_code, _last_client_ping, _server_stopping, _is_restarting
    _is_restarting = True
    _exit_code = 42
    _server_stopping = True
    append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor] Iniciando reinicio de proceso...")
    try:
        has_recent_client = has_active_web_viewer(max_age=15.0)
        flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".restart_flag")
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("has_client" if has_recent_client else "no_client")
    except Exception:
        pass

    # Apagar el socket de forma asíncrona para desbloquear el serve_forever del hilo principal
    def _trigger_shutdown():
        time.sleep(0.15)
        if _server_instance:
            try:
                _server_instance.shutdown()
            except Exception:
                pass
    threading.Thread(target=_trigger_shutdown, daemon=True).start()

def cycle_workspace_profile(url=None):
    """Alterna cíclicamente el perfil de uso de la cuenta activa."""
    global _last_update_status_text
    profiles = ["backup_only", "support", "sales", "full"]
    accounts = config.list_accounts()
    acc_id = None
    if accounts:
        real = [a for a in accounts if a["id"] != config.DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
        acc_id = real[0]["id"] if real else accounts[0]["id"]
    curr = config.get_account_profile(acc_id)
    try:
        next_idx = (profiles.index(curr) + 1) % len(profiles)
    except ValueError:
        next_idx = 0
    new_prof = profiles[next_idx]
    config.set_account_profile(acc_id, new_prof)
    prof_label = config.WORKSPACE_PROFILE_NAMES.get(new_prof, new_prof)
    try:
        sse_hub.get_sse_hub().broadcast("profile_update", {
            "account_id": acc_id,
            "profile": new_prof,
            "profile_label": prof_label
        })
    except Exception:
        pass
    _last_update_status_text = f"[Perfil activo cambiado a: {prof_label}]"
    render_dashboard(url)

def start_keyboard_listener(url):
    """Escucha teclas de atajo [V], [L], [U], [R] y [P] en segundo plano."""
    if os.name != "nt":
        return
    try:
        import msvcrt

        def _listen():
            while True:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b"l", b"L"):
                            open_logs_window()
                        elif ch in (b"v", b"V"):
                            open_or_focus_viewer(url)
                        elif ch in (b"u", b"U"):
                            check_and_apply_update(url)
                        elif ch in (b"r", b"R"):
                            confirm_and_restart(url)
                        elif ch in (b"p", b"P"):
                            cycle_workspace_profile(url)
                    time.sleep(0.15)
                except Exception:
                    time.sleep(0.5)

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
    except Exception:
        pass

def update_status_display(frame=None):
    """Actualiza la línea de estado del panel de control con feedback amigable y pulso visual."""
    global _anim_frame, _last_sync_status_text
    if frame is not None:
        _anim_frame = frame
    else:
        _anim_frame += 1

    with _ui_lock:
        if _update_in_progress:
            return

        pulse = "●" if (_anim_frame % 2 == 0) else "○"
        now = time.time()

        with _sync_lock:
            last_t = _last_sync_timestamp
            last_msgs = _last_sync_msgs_count
            batch_count = _sync_batch_count
            is_saving = _is_saving_sync
            init_msgs = _initial_total_msgs

        if batch_count == 0:
            dots = "." * ((_anim_frame % 3) + 1)
            if init_msgs > 0:
                body = f"[{pulse}] 🟡 Esperando a WhatsApp Web{dots.ljust(3)} ({init_msgs:,} mensajes en historial)"
            else:
                body = f"[{pulse}] 🟡 Esperando a WhatsApp Web{dots.ljust(3)} (Abre WhatsApp en tu navegador)"
        elif is_saving or (last_t and (now - last_t < 3.0)):
            spinner = ["|", "/", "-", "\\"][_anim_frame % 4]
            msgs_str = f" (+{last_msgs} nuevos mensajes)" if last_msgs > 0 else ""
            body = f"[{spinner}] 🔄 Sincronizando con WhatsApp Web...{msgs_str}"
        else:
            diff = int(now - last_t) if last_t else 0
            if diff < 5:
                ago_str = "recién"
            elif diff < 60:
                ago_str = f"hace {diff} seg"
            elif diff < 3600:
                ago_str = f"hace {diff // 60} min"
            else:
                ago_str = datetime.fromtimestamp(last_t).strftime("%H:%M hs")

            recent_info = f" (+{last_msgs} msgs)" if last_msgs > 0 else ""
            body = f"[{pulse}] 🟢 Conectado y al día · Último respaldo: {ago_str}{recent_info} (Todo seguro)"

        _last_sync_status_text = body
        status_text = f"  Estado: {body}"

        try:
            cols = shutil.get_terminal_size(fallback=(80, 24)).columns
            max_len = max(40, cols - 1)
        except Exception:
            max_len = 79

        if len(status_text) > max_len:
            status_text = status_text[:max_len]

        if sys.stdout and hasattr(sys.stdout, "write"):
            try:
                sys.stdout.write(f"\r{status_text.ljust(max_len)}")
                sys.stdout.flush()
            except Exception:
                pass


def start_status_animator():
    """Hilo secundario que actualiza suavemente la línea de estado del panel cada segundo."""
    def _animate():
        frame = 0
        while True:
            try:
                time.sleep(1.0)
                update_status_display(frame)
                frame = (frame + 1) % 1000
            except Exception:
                time.sleep(1.0)

    t = threading.Thread(target=_animate, daemon=True)
    t.start()


def log_sync_status(stats, account_id="default"):
    global _sync_batch_count, _total_messages_synced
    global _last_sync_timestamp, _last_sync_msgs_count, _is_saving_sync, _last_sync_account
    msgs = stats.get("messages_saved", 0)
    chats = stats.get("chats_saved", 0)
    contacts = stats.get("contacts_saved", 0)
    now_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _sync_lock:
        _sync_batch_count += 1
        _total_messages_synced += msgs
        _last_sync_timestamp = time.time()
        _last_sync_msgs_count = msgs
        _last_sync_account = account_id or ""
        _is_saving_sync = False

    # 1. Guardar log detallado en sync.log para la ventana de logs
    append_sync_log(
        f"[{now_full}] [Sync: {account_id}] Lote #{_sync_batch_count}: +{msgs} msgs, {chats} chats, {contacts} contactos | Detalle: {stats}"
    )

    # 2. Actualizar visualmente la línea en el panel de control
    update_status_display()

class BackupServerHandler(BaseHTTPRequestHandler):
    def is_trusted_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed_origins = [
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
            "https://web.whatsapp.com"
        ]
        if origin in allowed_origins or origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            return True

        # En modo LAN, permitir orígenes de subredes privadas RFC1918
        settings = config.get_system_settings()
        if settings.get("network_mode") == "lan":
            try:
                parsed = urlparse(origin)
                host = parsed.hostname
                if host and config.is_private_ip(host):
                    return True
            except Exception:
                pass
        return False

    def send_cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and self.is_trusted_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Range, X-WAckup-Pin, X-Office-PIN, X-WAckup-Operator, X-Operator-Name")
            self.send_header("Vary", "Origin")

    def is_local_client(self):
        """
        Verifica si la petición proviene físicamente de la PC anfitriona (127.0.0.1 / ::1).
        Jamás permite que un socket externo suplante IP local mediante X-Forwarded-For.
        """
        ip = self.client_address[0] if self.client_address else "127.0.0.1"
        if ip not in ("127.0.0.1", "localhost", "::1"):
            return False
        simulated = self.headers.get("X-Forwarded-For") or self.headers.get("X-Remote-IP")
        if simulated:
            clean_sim = simulated.split(",")[0].strip()
            return clean_sim in ("127.0.0.1", "localhost", "::1")
        return True

    def is_authorized_request(self):
        """
        Control de acceso LAN:
        - Si el cliente es local (127.0.0.1): 100% autorizado sin pedir PIN.
        - Si el cliente es remoto:
          - Si network_mode != 'lan': 403 Forbidden.
          - Si network_mode == 'lan': exige PIN válido (X-WAckup-Pin, X-Office-PIN o query ?pin=).
            Si falta o no coincide -> 401 Unauthorized.
        """
        if self.is_local_client():
            return True, 200, None

        settings = config.get_system_settings()
        if settings.get("network_mode") != "lan":
            return False, 403, "El servidor está configurado en modo local (solo esta PC). Activa Modo Equipo en Red Local en Sistema."

        expected_pin = str(settings.get("lan_pin", "")).strip()
        client_pin = (
            self.headers.get("X-WAckup-Pin") or
            self.headers.get("X-Office-PIN") or
            ""
        ).strip()

        if not client_pin:
            try:
                url = urlparse(self.path)
                params = parse_qs(url.query)
                client_pin = (params.get("pin", [""])[0] or params.get("office_pin", [""])[0]).strip()
            except Exception:
                pass

        if not expected_pin or client_pin != expected_pin:
            return False, 401, "PIN de oficina requerido o incorrecto"

        return True, 200, None

    def do_OPTIONS(self):
        if not self.is_trusted_origin():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, status_code, data):
        response_bytes = json.dumps(data).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(response_bytes)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except OSError as e:
            if getattr(e, "winerror", None) in (10053, 10054):
                pass
            else:
                raise

    def _serve_file_with_range(self, filepath, mime_type):
        """Sirve un archivo soportando peticiones HTTP Range (206 Partial Content) y streaming por bloques."""
        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range")

        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                start_str, end_str = m.groups()
                if start_str:
                    start = int(start_str)
                if end_str:
                    end = min(int(end_str), file_size - 1)
                if start < file_size and start <= end:
                    status_code = 206

        length = end - start + 1
        self.send_response(status_code)
        self.send_header("Content-Type", mime_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_cors_headers()
        self.end_headers()

        chunk_size = 64 * 1024
        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    break
                remaining -= len(chunk)

    def _resolve_account_params(self, params=None, fallback_query_params=None):
        def _extract_aid(src):
            if not isinstance(src, dict):
                return None
            raw = src.get("account_id")
            if raw is None:
                raw = src.get("account")
            if isinstance(raw, (list, tuple)):
                return raw[0] if raw else None
            if isinstance(raw, str):
                return raw.strip() or None
            return str(raw) if raw is not None else None

        account_id = _extract_aid(params) or _extract_aid(fallback_query_params)
        if account_id and account_id != config.DEFAULT_ACCOUNT_ID:
            config.migrate_single_to_multi_account(account_id)
        elif not account_id or account_id == config.DEFAULT_ACCOUNT_ID:
            accounts = config.list_accounts()
            real_accounts = [a for a in accounts if a["id"] != config.DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
            if real_accounts:
                account_id = real_accounts[0]["id"]
        db_path = config.get_account_db(account_id)
        backup_dir = config.get_account_backup_dir(account_id)
        return account_id, db_path, backup_dir

    def do_GET(self):
        global _last_client_ping, _last_sync_epoch
        if not self.is_trusted_origin():
            self.send_json(403, {"error": "Origen no permitido"})
            return

        url = urlparse(self.path)

        if url.path in ["/", "/index.html"]:
            record_web_viewer_ping()
            index_path = os.path.join(WEB_DIR, "index.html")
            if os.path.isfile(index_path):
                with open(index_path, "rb") as f:
                    content_bytes = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content_bytes)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(content_bytes)
            else:
                self.send_response(404)
                self.end_headers()
            return

        # Servir recursos estáticos dentro de web/ (css, js, assets) con protección anti-traversal
        rel_static_path = url.path.lstrip("/")
        candidate_static = os.path.abspath(os.path.join(WEB_DIR, rel_static_path))
        if os.path.commonpath([candidate_static, os.path.abspath(WEB_DIR)]) == os.path.abspath(WEB_DIR) and os.path.isfile(candidate_static):
            ext = os.path.splitext(candidate_static)[1].lower()
            mime_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon"
            }
            content_type = mime_types.get(ext, "application/octet-stream")
            with open(candidate_static, "rb") as f:
                content_bytes = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content_bytes)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(content_bytes)
            return

        elif url.path in ["/favicon.jpg", "/favicon.ico"]:
            favicon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "favicon.jpg")
            if os.path.isfile(favicon_path):
                with open(favicon_path, "rb") as f:
                    icon_bytes = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(icon_bytes)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(icon_bytes)
            else:
                self.send_response(404)
                self.end_headers()
            return

        # Control de autorización para endpoints de API, Logs y Multimedia
        if (
            url.path.startswith("/api/")
            or url.path.startswith("/media/")
            or url.path == "/logs"
            or (url.path.startswith("/data/") and "/media/" in url.path)
        ):
            if url.path not in ("/api/auth/verify_pin", "/api/system/network"):
                auth_ok, status_code, err_msg = self.is_authorized_request()
                if not auth_ok:
                    self.send_json(status_code, {
                        "ok": False,
                        "error": err_msg,
                        "needs_pin": (status_code == 401)
                    })
                    return

        if url.path == "/api/system/network":
            is_local = self.is_local_client()
            settings = config.get_system_settings()
            ips = config.get_local_ip_addresses()
            auth_ok, _, _ = self.is_authorized_request()
            can_see_pin = is_local or auth_ok
            known_ops = settings.get("known_operators", [])
            active_op = settings.get("active_operator", "") or (known_ops[0] if known_ops else "")

            resp_data = {
                "ok": True,
                "network_mode": settings.get("network_mode", "local"),
                "host_ips": ips,
                "local_ips": ips,
                "port": PORT,
                "known_operators": known_ops,
                "active_operator": active_op,
                "online_operators": sse_hub.get_sse_hub().get_online_operators(),
                "is_local": is_local,
                "needs_pin": not can_see_pin
            }
            if can_see_pin:
                resp_data["lan_pin"] = settings.get("lan_pin", "")
            self.send_json(200, resp_data)
            return

        elif url.path == "/api/events":
            global _active_web_sse_clients
            ev_params = parse_qs(url.query)
            ev_client_id = (ev_params.get("client_id", [""])[0] or "").strip()
            is_web_tab = ev_client_id.startswith("web_")

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors_headers()
            self.end_headers()
            try:
                self.wfile.flush()
            except Exception:
                pass

            hub = sse_hub.get_sse_hub()
            client_q = hub.subscribe()

            if is_web_tab:
                with _viewer_lock:
                    _active_web_sse_clients += 1
                    if ev_client_id:
                        _web_sse_queues[ev_client_id] = client_q
                record_web_viewer_ping(ev_client_id)

            try:
                active_prof = config.get_account_profile()
                sys_settings = config.get_system_settings()
                known_ops_sse = sys_settings.get("known_operators", [])
                active_op_sse = sys_settings.get("active_operator", "") or (known_ops_sse[0] if known_ops_sse else "")
                init_payload = {
                    "connected": True,
                    "timestamp": int(time.time()),
                    "active_presence": hub.get_active_presence(),
                    "active_operator": active_op_sse,
                    "profile": active_prof,
                    "profile_label": config.WORKSPACE_PROFILE_NAMES.get(active_prof, active_prof)
                }
                init_msg = f"event: connected\ndata: {json.dumps(init_payload, ensure_ascii=False)}\n\n"
                self.wfile.write(init_msg.encode("utf-8"))
                self.wfile.flush()

                while not _server_stopping:
                    try:
                        msg = client_q.get(timeout=20.0)
                        if msg is None:
                            break
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                    if is_web_tab:
                        record_web_viewer_ping(ev_client_id)
            except (BrokenPipeError, ConnectionResetError, OSError, Exception):
                pass
            finally:
                hub.unsubscribe(client_q)
                if is_web_tab:
                    with _viewer_lock:
                        _active_web_sse_clients = max(0, _active_web_sse_clients - 1)
                        if ev_client_id and _web_sse_queues.get(ev_client_id) is client_q:
                            _web_sse_queues.pop(ev_client_id, None)
                        if not _server_stopping and ev_client_id:
                            _active_web_viewers.pop(ev_client_id, None)
            return

        elif url.path == "/api/presence":
            hub = sse_hub.get_sse_hub()
            active = hub.get_active_presence()
            self.send_json(200, {
                "ok": True,
                "presence": active,
                "active_presence": active
            })
            return

        elif url.path == "/api/copilot/notes/log":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            chat_id = params.get("chat_id", [None])[0]
            limit = int(params.get("limit", ["50"])[0])
            notes = db_manager.get_contact_notes_log(chat_id, limit=limit, db_path=db_path)
            self.send_json(200, {
                "ok": True,
                "chat_id": chat_id,
                "notes_log": notes
            })
            return

        elif url.path == "/api/accounts":
            accounts = config.list_accounts()
            self.send_json(200, accounts)
            return

        elif url.path == "/api/system/info":
            settings = config.get_system_settings()
            ver = updater.get_current_version()
            is_lan = settings.get("network_mode") == "lan"
            ext_connected = (_last_client_ping > 0 and (time.time() - _last_client_ping) < 120.0)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            ext_path = os.path.join(base_dir, "extension")
            info_data = {
                "ok": True,
                "version": ver,
                "network_mode": settings.get("network_mode", "local"),
                "is_lan": is_lan,
                "local_ips": config.get_local_ip_addresses(),
                "port": PORT,
                "onboarding_completed": settings.get("onboarding_completed", False),
                "extension_connected": ext_connected,
                "extension_path": ext_path,
                "desktop_shortcut_exists": check_shortcut_exists("desktop"),
                "startup_shortcut_exists": check_shortcut_exists("startup"),
                "git_branch": updater.get_git_branch(base_dir)
            }
            self.send_json(200, info_data)
            return

        elif url.path == "/api/system/onboarding":
            settings = config.get_system_settings()
            ext_connected = (_last_client_ping > 0 and (time.time() - _last_client_ping) < 120.0)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.send_json(200, {
                "ok": True,
                "onboarding_completed": settings.get("onboarding_completed", False),
                "extension_connected": ext_connected,
                "extension_path": os.path.join(base_dir, "extension")
            })
            return

        elif url.path == "/api/system/update/check":
            status = updater.check_for_updates()
            self.send_json(200, status)
            return

        elif url.path == "/api/profile":
            params = parse_qs(url.query)
            acc_id, _, _ = self._resolve_account_params(params)
            prof = config.get_account_profile(acc_id)
            prof_names = {
                "backup_only": "Solo Respaldo",
                "support": "Atención y Soporte",
                "sales": "Ventas y Mini-CRM",
                "full": "Suite Completa"
            }
            self.send_json(200, {
                "account_id": acc_id,
                "profile": prof,
                "profile_label": prof_names.get(prof, prof),
                "available_profiles": config.VALID_WORKSPACE_PROFILES
            })
            return

        elif url.path == "/api/copilot/context":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            chat_query = params.get("chat_query", [None])[0] or params.get("chat_id", [None])[0]
            if not chat_query:
                self.send_json(400, {"ok": False, "error": "Falta parámetro chat_query"})
                return
            context = db_manager.get_copilot_context(chat_query, db_path=db_path)
            if not context:
                self.send_json(404, {"ok": False, "error": "Chat no encontrado"})
                return
            prof = config.get_account_profile(account_id)
            context["account_id"] = account_id
            context["profile"] = prof
            context["profile_label"] = config.WORKSPACE_PROFILE_NAMES.get(prof, prof)
            self.send_json(200, context)
            return

        elif url.path == "/api/support/unanswered":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            limit = int(params.get("limit", ["30"])[0])
            days = int(params.get("days", ["7"])[0])
            chats = db_manager.get_unanswered_chats(limit=limit, max_age_days=days, db_path=db_path)
            self.send_json(200, {
                "ok": True,
                "account_id": account_id,
                "count": len(chats),
                "chats": chats
            })
            return

        elif url.path == "/api/support/templates":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            p_type = params.get("profile_type", [None])[0]
            templates = db_manager.list_quick_templates(profile_type=p_type, db_path=db_path)
            self.send_json(200, {
                "ok": True,
                "account_id": account_id,
                "templates": templates
            })
            return

        elif url.path == "/api/sales/radar":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            limit = int(params.get("limit", ["30"])[0])
            leads = db_manager.get_sales_followups(limit=limit, db_path=db_path)
            self.send_json(200, {
                "ok": True,
                "account_id": account_id,
                "count": len(leads),
                "leads": leads
            })
            return

        elif url.path == "/api/crm/kanban":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            board_type = params.get("type", ["sales"])[0]
            data = db_manager.get_kanban_board(board_type=board_type, db_path=db_path)
            data["account_id"] = account_id
            self.send_json(200, data)
            return

        elif url.path == "/api/crm/rfm/dormant":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            days = int(params.get("days", ["45"])[0])
            limit = int(params.get("limit", ["5"])[0])
            clients = db_manager.get_rfm_dormant_clients(days=days, limit=limit, db_path=db_path)
            self.send_json(200, {"ok": True, "account_id": account_id, "clients": clients})
            return

        elif url.path == "/api/crm/demand":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            days = int(params.get("days", ["60"])[0])
            limit = int(params.get("limit", ["25"])[0])
            data = db_manager.get_demand_thermometer(days=days, limit=limit, db_path=db_path)
            data["account_id"] = account_id
            self.send_json(200, data)
            return

        elif url.path == "/api/crm/support/metrics":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            data = db_manager.get_support_metrics(db_path=db_path)
            data["account_id"] = account_id
            self.send_json(200, data)
            return

        elif url.path == "/api/sync/status":
            params = parse_qs(url.query)
            is_internal_probe = params.get("internal_probe", ["0"])[0] == "1"
            if not is_internal_probe:
                _last_client_ping = time.time()
                origin_hdr = (self.headers.get("Origin") or "").lower()
                referer_hdr = (self.headers.get("Referer") or "").lower()
                is_viewer_req = (
                    params.get("viewer", ["0"])[0] == "1"
                    or (
                        not origin_hdr.startswith("chrome-extension://")
                        and not origin_hdr.startswith("https://web.whatsapp.com")
                        and (f":{PORT}" in referer_hdr or "127.0.0.1" in referer_hdr or "localhost" in referer_hdr)
                    )
                )
                if is_viewer_req:
                    req_cid = (params.get("client_id", [""])[0] or "").strip() or None
                    record_web_viewer_ping(req_cid)
            account_id, db_path, _ = self._resolve_account_params(params)
            include_ids = params.get("include_ids", ["0"])[0] == "1"
            status = db_manager.get_sync_status(db_path=db_path, include_ids=include_ids)
            status["account_id"] = account_id or config.DEFAULT_ACCOUNT_ID
            status["extension_version"] = get_disk_extension_version()
            status["server_session_id"] = SERVER_SESSION_ID
            status["last_sync_epoch"] = _last_sync_epoch
            status["active_web_viewer"] = has_active_web_viewer()
            self.send_json(200, status)
            return

        elif url.path == "/api/search":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            q = params.get("q", [""])[0]
            chat_id = params.get("chat_id", [None])[0]
            try:
                limit = int(params.get("limit", [100])[0])
            except Exception:
                limit = 100
            limit = max(1, min(limit, 500))
            results = db_manager.search_full_text(q, chat_id=chat_id, limit=limit, db_path=db_path)
            self.send_json(200, results)
            return

        elif url.path == "/api/chats":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            conn = db_manager.get_connection(db_path)
            cur = conn.cursor()
            contact_map = db_manager.build_contact_map(cur)
            tags_map = db_manager.get_all_chats_tags_map(cur)
            query = """
            SELECT 
                c.chat_id,
                c.nombre AS chat_nombre,
                meta.custom_name AS custom_name,
                ct.nombre AS ct_nombre,
                ct.push_name AS ct_push,
                ct.numero AS ct_numero,
                ct_num.nombre AS ct_num_nombre,
                ct_num.push_name AS ct_num_push,
                ct_num.numero AS ct_num_numero,
                ct_can.nombre AS ct_can_nombre,
                ct_can.push_name AS ct_can_push,
                ct_can.numero AS ct_can_numero,
                ca.canonical_id,
                c.ultimo_mensaje AS last_msg,
                c.ultimo_tipo AS last_tipo,
                c.ultimo_timestamp AS last_ts
            FROM chats c
            LEFT JOIN crm_contact_meta meta ON c.chat_id = meta.chat_id
            LEFT JOIN contactos ct ON c.chat_id = ct.contact_id
            LEFT JOIN contactos ct_num ON c.chat_id LIKE '%@c.us' AND REPLACE(c.chat_id, '@c.us', '@s.whatsapp.net') = ct_num.contact_id
            LEFT JOIN contact_aliases ca ON c.chat_id = ca.alias_id
            LEFT JOIN contactos ct_can ON ca.canonical_id = ct_can.contact_id
            WHERE c.chat_id IS NOT NULL 
              AND c.chat_id != 'status@broadcast'
            ORDER BY c.ultimo_timestamp DESC
            """
            cur.execute(query)
            rows = [dict(row) for row in cur.fetchall()]
            conn.close()
            chats = []
            for row_d in rows:
                phone = (
                    (row_d["chat_id"].split("@")[0] if ("@c.us" in row_d["chat_id"] or "@s.whatsapp.net" in row_d["chat_id"]) else None)
                    or row_d.get("ct_numero")
                    or row_d.get("ct_can_numero")
                    or row_d.get("ct_num_numero")
                    or (row_d.get("canonical_id", "").split("@")[0] if row_d.get("canonical_id") and ("@c.us" in row_d["canonical_id"] or "@s.whatsapp.net" in row_d["canonical_id"]) else None)
                )
                push_name = row_d.get("ct_push") or row_d.get("ct_can_push") or row_d.get("ct_num_push")
                saved_name = row_d.get("custom_name") or row_d.get("ct_nombre") or row_d.get("ct_can_nombre") or row_d.get("ct_num_nombre")
                display_name = db_manager.get_best_contact_display(
                    custom_name=row_d.get("custom_name"),
                    contact_name=saved_name or row_d.get("chat_nombre"),
                    push_name=push_name,
                    phone_number=phone,
                    chat_id=row_d["chat_id"]
                )
                is_saved = bool(
                    saved_name and 
                    db_manager.is_human_contact_name(saved_name, row_d["chat_id"]) and 
                    (bool(re.search(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]', str(saved_name))) or len(re.sub(r'\D', '', str(saved_name))) < 6)
                )
                chat_tags = tags_map.get(row_d["chat_id"]) or (tags_map.get(row_d.get("canonical_id")) if row_d.get("canonical_id") else None) or []
                chats.append({
                    "chat_id": row_d["chat_id"],
                    "canonical_id": row_d.get("canonical_id"),
                    "display_name": display_name,
                    "phone_number": phone,
                    "push_name": push_name,
                    "saved_name": saved_name if is_saved else None,
                    "is_saved": is_saved,
                    "tags": chat_tags,
                    "last_msg": db_manager.resolve_mentions_in_text(row_d["last_msg"], contact_map) if row_d.get("last_msg") else row_d.get("last_msg"),
                    "last_tipo": row_d.get("last_tipo"),
                    "last_ts": row_d.get("last_ts")
                })
            self.send_json(200, chats)
            return

        elif url.path == "/api/messages":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            chat_id = params.get("chat_id", [None])[0]
            if not chat_id:
                self.send_json(400, {"error": "Falta parámetro chat_id"})
                return

            conn = db_manager.get_connection(db_path)
            cur = conn.cursor()
            contact_map = db_manager.build_contact_map(cur)
            query = """
            SELECT 
                m.msg_id,
                m.chat_id,
                m.es_mio,
                m.fecha_utc,
                m.timestamp,
                m.tipo,
                m.contenido,
                m.archivo_local,
                m.mimetype,
                m.tamano,
                m.duracion,
                m.thumbnail,
                m.estado_multimedia,
                m.quoted_msg_id,
                m.quoted_author,
                m.quoted_body,
                m.reenviado,
                m.eliminado,
                COALESCE(ct.nombre, ct_num.nombre, ct.push_name, ct_num.push_name, m.remitente) AS remitente_nombre,
                m.remitente AS remitente_jid
            FROM mensajes m
            LEFT JOIN contactos ct ON m.remitente = ct.contact_id
            LEFT JOIN contactos ct_num ON REPLACE(m.remitente, '@c.us', '@s.whatsapp.net') = ct_num.contact_id
            WHERE m.chat_id = ?
              AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'gp2', 'protocol', 'ciphertext', 'biz_content_placeholder')
            ORDER BY m.timestamp ASC
            """
            cur.execute(query, (chat_id,))
            messages = [dict(row) for row in cur.fetchall()]
            conn.close()

            # Mapa de mensajes del chat para auto-resolver mensajes citados si no traían el texto incrustado
            msg_map = {}
            for m in messages:
                mid = m.get("msg_id") or ""
                msg_map[mid] = m
                for p in mid.split("_"):
                    if len(p) >= 12:
                        msg_map[p] = m

            for m in messages:
                if m.get("contenido"):
                    m["contenido"] = db_manager.resolve_mentions_in_text(m["contenido"], contact_map)

                # Si falta quoted_body pero existe quoted_msg_id, resolverlo desde el mensaje original citado
                if not m.get("quoted_body") and m.get("quoted_msg_id"):
                    qid = m["quoted_msg_id"]
                    if qid in msg_map and msg_map[qid].get("contenido"):
                        m["quoted_body"] = msg_map[qid]["contenido"]
                    if not m.get("quoted_author") and qid in msg_map and msg_map[qid].get("remitente_nombre"):
                        m["quoted_author"] = msg_map[qid]["remitente_nombre"]

                if m.get("quoted_body"):
                    m["quoted_body"] = db_manager.resolve_mentions_in_text(m["quoted_body"], contact_map)
                qa = m.get("quoted_author")
                if qa:
                    qa_key = qa.split("@")[0]
                    if qa in contact_map:
                        m["quoted_author"] = contact_map[qa]
                    elif qa_key in contact_map:
                        m["quoted_author"] = contact_map[qa_key]
            self.send_json(200, messages)
            return

        elif url.path == "/api/export/html":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            chat_id = params.get("chat_id", [None])[0]
            embed_media = params.get("embed", ["0"])[0] == "1"
            if not chat_id:
                self.send_json(400, {"error": "Falta parámetro chat_id"})
                return

            html_content = generate_chat_html_export(chat_id, embed_media=embed_media, db_path=db_path)
            html_bytes = html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="chat_{chat_id.split("@")[0]}.html"')
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        elif url.path == "/api/export/contacts":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            contacts = db_manager.get_contacts_for_export(db_path=db_path)
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["Nombre", "Número de teléfono"])
            for c in contacts:
                writer.writerow([c["nombre"], c["telefono"]])
            csv_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
            ts_str = datetime.now().strftime("%Y-%m-%d")
            acc_str = f"_{account_id}" if account_id else ""
            self.send_response(200)
            self.send_header("Content-type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="contactos_whatsapp{acc_str}_{ts_str}.csv"')
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        elif url.path == "/api/backup/list":
            params = parse_qs(url.query)
            _, _, backup_dir = self._resolve_account_params(params)
            backups = db_manager.list_manual_backups(backup_dir=backup_dir)
            self.send_json(200, backups)
            return

        elif url.path == "/api/backup/download":
            params = parse_qs(url.query)
            _, _, backup_dir = self._resolve_account_params(params)
            filename = os.path.basename(params.get("file", [""])[0])
            backups_root = os.path.abspath(backup_dir)
            filepath = os.path.abspath(os.path.join(backups_root, filename))
            if filepath.startswith(backups_root + os.sep) and os.path.isfile(filepath):
                self._serve_file_with_range(filepath, "application/octet-stream")
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif url.path.startswith("/media/") or (url.path.startswith("/data/") and "/media/" in url.path):
            decoded_path = unquote(url.path).lstrip("/")
            abs_file_path = os.path.abspath(os.path.join(BASE_DIR, decoded_path))
            data_root = os.path.abspath(DATA_DIR)
            legacy_media_root = os.path.abspath(os.path.join(BASE_DIR, "media"))

            # Fallback si se solicitó "/media/..." o "/data/media/..." directamente
            if not os.path.isfile(abs_file_path):
                sub_rel = None
                if decoded_path.startswith("media/"):
                    sub_rel = decoded_path[len("media/"):]
                elif decoded_path.startswith("data/media/"):
                    sub_rel = decoded_path[len("data/media/"):]

                if sub_rel:
                    cand = os.path.abspath(os.path.join(MEDIA_DIR, sub_rel))
                    if os.path.isfile(cand):
                        abs_file_path = cand
                    else:
                        for acc in config.list_accounts():
                            acc_media = os.path.join(acc["dir"], "media", sub_rel)
                            if os.path.isfile(acc_media):
                                abs_file_path = os.path.abspath(acc_media)
                                break

            # Prevenir vulnerabilidad de Path Traversal
            is_valid = (
                (abs_file_path.startswith(data_root + os.sep) or abs_file_path.startswith(legacy_media_root + os.sep))
                and os.path.isfile(abs_file_path)
            )

            if is_valid:
                mime, _ = mimetypes.guess_type(abs_file_path)
                mime = mime or "application/octet-stream"
                self._serve_file_with_range(abs_file_path, mime)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif url.path == "/api/extension/info":
            ext_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "extension"))
            self.send_json(200, {
                "extension_path": ext_path,
                "browser_extension_url": "chrome://extensions",
                "extension_version": get_disk_extension_version()
            })
            return

        elif url.path == "/api/logs":
            lines = []
            if os.path.isfile(SYNC_LOG_PATH):
                try:
                    with open(SYNC_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                        lines = [l.rstrip("\r\n") for l in f.readlines()]
                except Exception as e:
                    lines = [f"[Error al leer logs: {e}]"]
            self.send_json(200, {
                "ok": True,
                "logs": lines,
                "count": len(lines),
                "path": SYNC_LOG_PATH
            })
            return

        elif url.path == "/api/copilot/tags/all":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            tags = db_manager.list_all_tags(db_path=db_path)
            self.send_json(200, {
                "ok": True,
                "account_id": account_id,
                "tags": tags
            })
            return

        elif url.path == "/logs":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if os.path.isfile(SYNC_LOG_PATH):
                with open(SYNC_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.wfile.write("Aún no hay registros en logs de sincronización".encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        try:
            if not self.is_trusted_origin():
                self.send_json(403, {"error": "Origen no permitido"})
                return

            url = urlparse(self.path)

            # Control de autorización para endpoints de API (excepto /api/auth/verify_pin)
            if url.path.startswith("/api/") and url.path != "/api/auth/verify_pin":
                auth_ok, status_code, err_msg = self.is_authorized_request()
                if not auth_ok:
                    self.send_json(status_code, {
                        "ok": False,
                        "error": err_msg,
                        "needs_pin": (status_code == 401)
                    })
                    return

            if url.path == "/api/auth/verify_pin":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                pin = str(payload.get("pin", "")).strip()
                operator_name = str(payload.get("operator") or payload.get("operator_name", "")).strip()

                settings = config.get_system_settings()
                expected_pin = str(settings.get("lan_pin", "")).strip()
                network_mode = settings.get("network_mode", "local")
                is_local = self.is_local_client()

                # Si está en modo LAN o se requiere PIN, validar si se especificó expected_pin
                if (network_mode == "lan" or not is_local) and expected_pin:
                    if not pin or pin != expected_pin:
                        self.send_json(401, {
                            "ok": False,
                            "error": "PIN de oficina incorrecto",
                            "needs_pin": True
                        })
                        return
                elif pin and expected_pin and pin != expected_pin:
                    self.send_json(401, {
                        "ok": False,
                        "error": "PIN de oficina incorrecto",
                        "needs_pin": True
                    })
                    return

                known_ops = settings.get("known_operators", [])
                active_op = settings.get("active_operator", "") or (known_ops[0] if known_ops else "")
                online_ops = sse_hub.get_sse_hub().get_online_operators()

                # Si NO se especifica operator_name, responder verificación exitosa con lista de operadores y estado
                if not operator_name:
                    self.send_json(200, {
                        "ok": True,
                        "network_mode": network_mode,
                        "known_operators": known_ops,
                        "active_operator": active_op,
                        "online_operators": online_ops,
                        "message": "PIN verificado correctamente"
                    })
                    return

                if known_ops:
                    matched_op = next((existing for existing in known_ops if existing.lower() == operator_name.lower()), None)
                    if not matched_op:
                        self.send_json(400, {
                            "ok": False,
                            "error": "El operador seleccionado no está dado de alta. Debe registrarse previamente desde Configuración en la PC principal.",
                            "needs_pin": True,
                            "known_operators": known_ops,
                            "active_operator": active_op,
                            "online_operators": online_ops
                        })
                        return
                    operator_name = matched_op
                else:
                    # Si no hay operadores en el sistema, darlo de alta automáticamente
                    config.update_system_settings(add_operator=operator_name, active_operator=operator_name)
                    known_ops = [operator_name]
                    active_op = operator_name

                self.send_json(200, {
                    "ok": True,
                    "operator_name": operator_name,
                    "known_operators": known_ops,
                    "active_operator": active_op,
                    "online_operators": online_ops,
                    "message": "PIN verificado correctamente"
                })
                return

            elif url.path == "/api/presence/kick":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                target_op = str(payload.get("operator") or payload.get("operator_name", "")).strip()
                if not target_op:
                    self.send_json(400, {"ok": False, "error": "Falta el nombre de operador a liberar"})
                    return
                kicked = sse_hub.get_sse_hub().kick_operator(target_op)
                online_ops = sse_hub.get_sse_hub().get_online_operators()
                self.send_json(200, {
                    "ok": True,
                    "kicked": kicked,
                    "operator": target_op,
                    "online_operators": online_ops,
                    "message": f"Sesión de {target_op} liberada correctamente"
                })
                return

            elif url.path == "/api/system/network":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}

                net_mode = payload.get("network_mode")
                lan_pin = payload.get("lan_pin")
                add_op = payload.get("operator_name") or payload.get("add_operator")
                remove_op = payload.get("remove_operator")
                rename_op = payload.get("rename_operator") or payload.get("update_operator")

                updated = config.update_system_settings(
                    network_mode=net_mode,
                    lan_pin=lan_pin,
                    add_operator=add_op,
                    remove_operator=remove_op,
                    rename_operator=rename_op
                )
                ips = config.get_local_ip_addresses()
                self.send_json(200, {
                    "ok": True,
                    "settings": updated,
                    "host_ips": ips,
                    "local_ips": ips,
                    "port": PORT
                })
                return

            elif url.path == "/api/system/operator/active":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                target_op = str(payload.get("operator") or payload.get("operator_name") or "").strip()
                if not target_op:
                    self.send_json(400, {"ok": False, "error": "operator requerido"})
                    return
                updated = config.update_system_settings(active_operator=target_op)
                sse_hub.get_sse_hub().kick_operator("Operador")
                sse_hub.get_sse_hub().broadcast("active_operator_updated", {
                    "event": "active_operator_updated",
                    "active_operator": updated.get("active_operator", target_op)
                })
                self.send_json(200, {
                    "ok": True,
                    "active_operator": updated.get("active_operator", target_op),
                    "settings": updated
                })
                return

            elif url.path == "/api/presence/heartbeat":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}

                chat_id = payload.get("chat_id")
                raw_op = payload.get("operator") or payload.get("operator_name") or self.headers.get("X-WAckup-Operator") or ""
                raw_op = str(raw_op).strip()
                settings = config.get_system_settings()
                active_op = settings.get("active_operator") or (settings.get("known_operators", [""])[0] if settings.get("known_operators") else "")
                if not raw_op or raw_op.lower() == "operador":
                    operator = active_op or "Operador"
                else:
                    operator = raw_op
                client_id = payload.get("client_id")
                source = payload.get("source", "visor")

                if chat_id:
                    try:
                        _, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                        with db_manager.db_connection(db_path, write=False) as conn:
                            chat_id = db_manager.resolve_canonical_chat_id(conn.cursor(), chat_id) or chat_id
                    except Exception:
                        pass

                hub = sse_hub.get_sse_hub()
                hub.record_presence(chat_id, operator, client_id, source=source)
                self.send_json(200, {"ok": True, "canonical_chat_id": chat_id})
                return

            elif url.path == "/api/presence/leave":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                chat_id = payload.get("chat_id")
                client_id = payload.get("client_id")
                if client_id:
                    sse_hub.get_sse_hub().remove_presence(client_id, chat_id=chat_id)
                    if payload.get("tab_closed"):
                        remove_web_viewer(client_id)
                self.send_json(200, {"ok": True})
                return

            elif url.path == "/api/viewer/focused":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > 0:
                    _ = self.rfile.read(content_length)
                record_viewer_focused_ack()
                self.send_json(200, {"ok": True})
                return

            elif url.path == "/api/copilot/notes/log":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}

                account_id, db_path, _ = self._resolve_account_params(payload)
                chat_id = payload.get("chat_id")
                author = payload.get("author") or payload.get("operator_name") or self.headers.get("X-WAckup-Operator") or "Operador"
                content = payload.get("content")

                if not chat_id or not content:
                    self.send_json(400, {"ok": False, "error": "chat_id y content requeridos"})
                    return

                created_note = db_manager.add_contact_note_log(chat_id, author, content, db_path=db_path)
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "note_log_added",
                    "chat_id": chat_id,
                    "note": created_note,
                    "author": author
                })
                self.send_json(200, {"ok": True, "note": created_note})
                return

            elif url.path == "/api/copilot/notes/log/delete":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                _, db_path, _ = self._resolve_account_params(payload)
                note_id = payload.get("id") or payload.get("note_id")
                chat_id = payload.get("chat_id")
                author = payload.get("author")
                db_manager.delete_contact_note_log(note_id, author=author, db_path=db_path)
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "note_log_deleted",
                    "chat_id": chat_id,
                    "note_id": note_id
                })
                self.send_json(200, {"ok": True})
                return

            elif url.path == "/api/copilot/assignment":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}

                _, db_path, _ = self._resolve_account_params(payload)
                chat_id = payload.get("chat_id")
                assigned_to = payload.get("assigned_to", "")
                operator_name = payload.get("operator_name") or payload.get("updated_by") or self.headers.get("X-WAckup-Operator") or ""

                res = db_manager.upsert_contact_meta(chat_id, assigned_to=assigned_to, updated_by=operator_name, db_path=db_path)
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "assignment_updated",
                    "chat_id": chat_id,
                    "assigned_to": assigned_to,
                    "updated_by": operator_name
                })
                self.send_json(200, {"ok": True, "meta": res})
                return

            elif url.path == "/api/logs/open":
                open_logs_window()
                self.send_json(200, {"success": True, "message": "Ventana de logs abierta"})
                return

            elif url.path == "/api/system/restart":
                self.send_json(200, {"ok": True, "message": "Reiniciando visor..."})
                def _do_restart():
                    time.sleep(0.4)
                    restart_current_process()
                threading.Thread(target=_do_restart, daemon=True).start()
                return

            elif url.path == "/api/system/onboarding":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                completed = payload.get("completed", True)
                profile = payload.get("profile")
                config.update_system_settings(onboarding_completed=completed)
                if profile and profile in config.VALID_WORKSPACE_PROFILES:
                    acc_id, _, _ = self._resolve_account_params({})
                    config.set_account_profile(acc_id, profile)
                    try:
                        sse_hub.get_sse_hub().broadcast("profile_update", {
                            "account_id": acc_id,
                            "profile": profile,
                            "profile_label": config.WORKSPACE_PROFILE_NAMES.get(profile, profile)
                        })
                    except Exception:
                        pass
                self.send_json(200, {"ok": True, "onboarding_completed": completed})
                return

            elif url.path == "/api/system/shortcuts":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                action = payload.get("action", "")
                enable = payload.get("enable", True)
                res = manage_windows_shortcut(action, enable=enable)
                self.send_json(200 if res.get("ok") else 400, res)
                return

            elif url.path == "/api/system/update/apply":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
                force = bool(payload.get("force", False))
                download_url = payload.get("download_url")
                result = updater.apply_update(download_url=download_url, force=force, restart=False)
                if result.get("ok"):
                    self.send_json(200, result)
                    def _do_update_restart():
                        time.sleep(0.8)
                        restart_current_process()
                    threading.Thread(target=_do_update_restart, daemon=True).start()
                else:
                    self.send_json(400, result)
                return

            elif url.path == "/api/profile/cycle":
                params = parse_qs(url.query)
                acc_id, _, _ = self._resolve_account_params(params)
                curr = config.get_account_profile(acc_id)
                profiles = list(config.VALID_WORKSPACE_PROFILES)
                try:
                    next_idx = (profiles.index(curr) + 1) % len(profiles)
                except ValueError:
                    next_idx = 0
                new_prof = profiles[next_idx]
                config.set_account_profile(acc_id, new_prof)
                prof_label = config.WORKSPACE_PROFILE_NAMES.get(new_prof, new_prof)
                try:
                    sse_hub.get_sse_hub().broadcast("profile_update", {
                        "account_id": acc_id,
                        "profile": new_prof,
                        "profile_label": prof_label
                    })
                except Exception:
                    pass
                self.send_json(200, {
                    "ok": True,
                    "profile": new_prof,
                    "profile_label": prof_label,
                    "account_id": acc_id
                })
                return

            elif url.path == "/api/profile":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                new_prof = payload.get("profile")
                account_id, _, _ = self._resolve_account_params(payload, parse_qs(url.query))
                if not new_prof or new_prof not in config.VALID_WORKSPACE_PROFILES:
                    self.send_json(400, {"ok": False, "error": f"Perfil inválido: {new_prof}"})
                    return

                config.set_account_profile(account_id, new_prof)
                prof_label = config.WORKSPACE_PROFILE_NAMES.get(new_prof, new_prof)
                try:
                    sse_hub.get_sse_hub().broadcast("profile_update", {
                        "account_id": account_id,
                        "profile": new_prof,
                        "profile_label": prof_label,
                        "profile_name": prof_label
                    })
                except Exception:
                    pass
                self.send_json(200, {
                    "ok": True,
                    "profile": new_prof,
                    "profile_label": prof_label,
                    "profile_name": prof_label,
                    "account_id": account_id
                })
                return

            elif url.path == "/api/copilot/notes":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                notes = payload.get("notes", "")
                expected_updated_at = payload.get("expected_updated_at")
                operator_name = payload.get("updated_by") or payload.get("operator") or payload.get("operator_name") or self.headers.get("X-WAckup-Operator") or self.headers.get("X-Operator-Name") or ""

                if not chat_id:
                    self.send_json(400, {"ok": False, "error": "Falta chat_id"})
                    return

                res = db_manager.upsert_contact_meta(
                    chat_id,
                    notes=notes,
                    updated_by=operator_name,
                    expected_updated_at=expected_updated_at,
                    db_path=db_path
                )

                if isinstance(res, dict) and res.get("conflict"):
                    self.send_json(409, {
                        "ok": False,
                        "conflict": True,
                        "error": res.get("error", "Conflicto de edición en la nota"),
                        "current_notes": res.get("current_notes", ""),
                        "updated_at": res.get("updated_at"),
                        "updated_by": res.get("updated_by", ""),
                        "current_updated_by": res.get("updated_by", "")
                    })
                    return

                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "notes_updated",
                    "chat_id": chat_id,
                    "notes": res.get("notes", "") if res else notes,
                    "updated_by": operator_name
                })
                self.send_json(200, {
                    "ok": True,
                    "chat_id": chat_id,
                    "notes": res.get("notes", "") if res else notes,
                    "updated_at": res.get("updated_at") if res else None,
                    "notes_updated_at": res.get("updated_at") if res else None
                })
                return

            elif url.path == "/api/support/ticket":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                ticket_status = payload.get("ticket_status", "none")

                if not chat_id:
                    self.send_json(400, {"ok": False, "error": "Falta chat_id"})
                    return

                extra_kwargs = {"radar_ignored": 0} if ticket_status == "open" else {}
                res = db_manager.upsert_contact_meta(chat_id, ticket_status=ticket_status, db_path=db_path, **extra_kwargs)
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "ticket_updated",
                    "chat_id": chat_id,
                    "ticket_status": res.get("ticket_status", "none")
                })
                self.send_json(200, {"ok": True, "chat_id": chat_id, "ticket_status": res.get("ticket_status", "none")})
                return

            elif url.path == "/api/radar/dismiss":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                mode = payload.get("mode", "unanswered")
                reason = payload.get("reason", "no_reply_needed")
                operator_name = (
                    payload.get("operator_name")
                    or payload.get("operator")
                    or payload.get("updated_by")
                    or self.headers.get("X-WAckup-Operator")
                    or self.headers.get("X-Operator-Name")
                    or ""
                )

                if not chat_id:
                    self.send_json(400, {"ok": False, "error": "Falta chat_id"})
                    return

                res = db_manager.dismiss_radar_chat(
                    chat_id=chat_id,
                    mode=mode,
                    reason=reason,
                    operator_name=operator_name,
                    db_path=db_path
                )
                if not res.get("ok"):
                    status_code = 403 if res.get("forbidden") else 400
                    self.send_json(status_code, res)
                    return

                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "radar_dismissed",
                    "chat_id": res.get("chat_id", chat_id),
                    "reason": reason,
                    "updated_by": operator_name
                })
                self.send_json(200, res)
                return

            elif url.path == "/api/radar/undo":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    payload = json.loads(body)
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                prev_state = payload.get("prev_state")
                operator_name = (
                    payload.get("operator_name")
                    or payload.get("operator")
                    or payload.get("updated_by")
                    or self.headers.get("X-WAckup-Operator")
                    or self.headers.get("X-Operator-Name")
                    or ""
                )

                if not chat_id or not isinstance(prev_state, dict):
                    self.send_json(400, {"ok": False, "error": "Datos insuficientes para deshacer"})
                    return

                res = db_manager.undo_radar_dismiss(
                    chat_id=chat_id,
                    prev_state=prev_state,
                    operator_name=operator_name,
                    db_path=db_path
                )
                if not res.get("ok"):
                    status_code = 403 if res.get("forbidden") else 400
                    self.send_json(status_code, res)
                    return

                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "radar_restored",
                    "chat_id": res.get("chat_id", chat_id),
                    "updated_by": operator_name
                })
                self.send_json(200, res)
                return

            elif url.path == "/api/support/templates":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                action = payload.get("action", "save")
                template_id = payload.get("template_id") or payload.get("id")

                if action == "delete":
                    if not template_id:
                        self.send_json(400, {"ok": False, "error": "template_id requerido para eliminar"})
                        return
                    db_manager.delete_quick_template(template_id, db_path=db_path)
                    self.send_json(200, {"ok": True, "deleted": True, "template_id": template_id})
                    return

                shortcut = payload.get("shortcut", "").strip()
                title = payload.get("title", "").strip()
                t_body = payload.get("body", "").strip()
                profile_type = payload.get("profile_type", "both")

                if not shortcut or not title or not t_body:
                    self.send_json(400, {"ok": False, "error": "Campos incompletos (shortcut, title, body requeridos)"})
                    return

                tid = db_manager.save_quick_template(shortcut, title, t_body, profile_type=profile_type, template_id=template_id, db_path=db_path)
                self.send_json(200, {"ok": True, "template_id": tid})
                return

            elif url.path == "/api/support/templates/delete":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                except Exception:
                    payload = {}
                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                template_id = payload.get("template_id") or payload.get("id")
                if not template_id:
                    params = parse_qs(url.query)
                    template_id = params.get("template_id", [None])[0] or params.get("id", [None])[0]
                if not template_id:
                    self.send_json(400, {"ok": False, "error": "template_id requerido"})
                    return
                db_manager.delete_quick_template(template_id, db_path=db_path)
                self.send_json(200, {"ok": True, "deleted": True, "template_id": template_id})
                return

            elif url.path == "/api/sales/pipeline":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                sales_stage = payload.get("sales_stage")
                estimated_value = payload.get("estimated_value")
                follow_up_date = payload.get("follow_up_date")

                if not chat_id:
                    self.send_json(400, {"ok": False, "error": "Falta chat_id"})
                    return

                res = db_manager.upsert_contact_meta(
                    chat_id,
                    sales_stage=sales_stage,
                    estimated_value=estimated_value,
                    follow_up_date=follow_up_date,
                    db_path=db_path
                )
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "pipeline_updated",
                    "chat_id": chat_id,
                    "sales_stage": res.get("sales_stage", "none"),
                    "estimated_value": res.get("estimated_value", 0.0),
                    "follow_up_date": res.get("follow_up_date")
                })
                self.send_json(200, {
                    "ok": True,
                    "chat_id": chat_id,
                    "sales_stage": res.get("sales_stage", "none"),
                    "estimated_value": res.get("estimated_value", 0.0),
                    "follow_up_date": res.get("follow_up_date")
                })
                return

            elif url.path == "/api/crm/kanban/move":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id")
                sales_stage = payload.get("sales_stage")
                ticket_status = payload.get("ticket_status")
                estimated_value = payload.get("estimated_value")
                follow_up_date = payload.get("follow_up_date")

                if not chat_id:
                    self.send_json(400, {"ok": False, "error": "Falta chat_id"})
                    return

                res = db_manager.upsert_contact_meta(
                    chat_id,
                    sales_stage=sales_stage,
                    ticket_status=ticket_status,
                    estimated_value=estimated_value,
                    follow_up_date=follow_up_date,
                    db_path=db_path
                )
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "kanban_moved",
                    "chat_id": chat_id,
                    "updated": res
                })
                self.send_json(200, {"ok": True, "chat_id": chat_id, "updated": res})
                return

            elif url.path == "/api/copilot/tags":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"ok": False, "error": "Cuerpo vacío"})
                    return
                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
                    return

                account_id, db_path, _ = self._resolve_account_params(payload, parse_qs(url.query))
                chat_id = payload.get("chat_id") or "__catalog__"
                tag = (payload.get("tag") or "").strip()
                category = payload.get("category", "general")
                action = payload.get("action", "add")

                if not tag:
                    self.send_json(400, {"ok": False, "error": "Falta parámetro tag"})
                    return

                if action == "remove":
                    if payload.get("delete_everywhere") or chat_id == "__catalog__":
                        ok = db_manager.delete_tag_everywhere(tag, db_path=db_path)
                    else:
                        ok = db_manager.remove_chat_tag(chat_id, tag, db_path=db_path)
                else:
                    ok = db_manager.add_chat_tag(chat_id, tag, category=category, db_path=db_path)

                tags = db_manager.get_chat_tags(chat_id, db_path=db_path) if chat_id != "__catalog__" else []
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "tags_updated",
                    "chat_id": chat_id,
                    "tags": tags
                })
                self.send_json(200, {"ok": ok, "chat_id": chat_id, "tags": tags})
                return

            elif url.path == "/api/sync":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"error": "Cuerpo de solicitud vacío"})
                    return

                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"error": f"JSON inválido: {e}"})
                    return

                account_id = payload.get("account_id")
                account_name = payload.get("account_name")
                account_phone = payload.get("account_phone")

                if account_id:
                    config.migrate_single_to_multi_account(account_id, name=account_name, phone=account_phone)

                db_path = config.get_account_db(account_id)
                config.save_account_metadata(account_id, name=account_name, phone=account_phone)
                db_manager.ensure_db_initialized(db_path=db_path)

                global _is_saving_sync
                _is_saving_sync = True
                update_status_display()

                stats = db_manager.save_incremental_sync(payload, db_path=db_path)
                log_sync_status(stats, account_id=account_id or "default")
                media_manager.trigger_media_processing(db_path=db_path)

                global _last_sync_epoch
                _last_sync_epoch = int(time.time())
                sse_hub.get_sse_hub().broadcast("sync_update", {
                    "last_sync_epoch": _last_sync_epoch,
                    "account_id": account_id or "default",
                    "stats": stats
                })

                self.send_json(200, {"success": True, "stats": stats, "account_id": account_id or "default", "last_sync_epoch": _last_sync_epoch})
                return

            elif url.path == "/api/backup/create":
                params = parse_qs(url.query)
                _, db_path, backup_dir = self._resolve_account_params(params)
                backup_info = db_manager.create_manual_backup(db_path=db_path, backup_dir=backup_dir)
                self.send_json(200, {"success": True, "backup": backup_info})
                return

            elif url.path == "/api/media/process":
                params = parse_qs(url.query)
                _, db_path, _ = self._resolve_account_params(params)
                media_manager.trigger_media_processing(db_path=db_path)
                self.send_json(200, {"status": "en_proceso"})
                return

            elif url.path == "/api/media/retry":
                params = parse_qs(url.query)
                _, db_path, _ = self._resolve_account_params(params)
                reset_count = db_manager.retry_failed_media(db_path=db_path)
                media_manager.trigger_media_processing(db_path=db_path)
                self.send_json(200, {"success": True, "reset_count": reset_count})
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": str(e)})

    def do_DELETE(self):
        try:
            if not self.is_trusted_origin():
                self.send_json(403, {"error": "Origen no permitido"})
                return

            auth_ok, status_code, err_msg = self.is_authorized_request()
            if not auth_ok:
                self.send_json(status_code, {
                    "ok": False,
                    "error": err_msg,
                    "needs_pin": (status_code == 401)
                })
                return

            url = urlparse(self.path)
            if url.path == "/api/support/templates":
                params = parse_qs(url.query)
                account_id, db_path, _ = self._resolve_account_params(params)
                template_id = params.get("id", [None])[0] or params.get("template_id", [None])[0]
                if not template_id:
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length > 0:
                        body = self.rfile.read(content_length)
                        try:
                            payload = json.loads(body.decode("utf-8"))
                            template_id = payload.get("id") or payload.get("template_id")
                        except Exception:
                            pass
                if not template_id:
                    self.send_json(400, {"ok": False, "error": "id de plantilla requerido"})
                    return
                db_manager.delete_quick_template(template_id, db_path=db_path)
                self.send_json(200, {"ok": True, "deleted": True, "template_id": template_id})
                return

            elif url.path == "/api/copilot/notes/log":
                params = parse_qs(url.query)
                _, db_path, _ = self._resolve_account_params(params)
                note_id = params.get("id", [None])[0] or params.get("note_id", [None])[0]
                author = params.get("author", [None])[0]
                if not note_id:
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length > 0:
                        body = self.rfile.read(content_length)
                        try:
                            payload = json.loads(body.decode("utf-8"))
                            note_id = payload.get("id") or payload.get("note_id")
                            author = payload.get("author") or author
                        except Exception:
                            pass
                if not note_id:
                    self.send_json(400, {"ok": False, "error": "id de nota requerido"})
                    return
                db_manager.delete_contact_note_log(note_id, author=author, db_path=db_path)
                sse_hub.get_sse_hub().broadcast("crm_update", {
                    "type": "note_log_deleted",
                    "note_id": note_id
                })
                self.send_json(200, {"ok": True, "deleted": True, "note_id": note_id})
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": str(e)})

    def log_message(self, format, *args):
        return

def run():
    migrate_legacy_data()
    ensure_data_dirs()
    accounts = config.list_accounts()
    if accounts:
        for acc in accounts:
            if acc.get("db_path"):
                db_manager.init_db(acc["db_path"])
    else:
        db_manager.init_db()

    media_manager.ensure_media_dirs()
    media_manager.trigger_media_processing()

    flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".restart_flag")
    has_client_flag = False
    is_restart = os.environ.get("WACKUP_RESTARTED") == "1" or os.path.exists(flag_path)
    if os.path.exists(flag_path):
        try:
            with open(flag_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            has_client_flag = (content == "has_client")
            os.remove(flag_path)
        except Exception:
            pass
    if "WACKUP_RESTARTED" in os.environ:
        os.environ.pop("WACKUP_RESTARTED", None)

    class WAckupHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request, client_address):
            exc_type, exc_val, _ = sys.exc_info()
            # Silenciar desconexiones abruptas normales de clientes web (recargas de pestaña, cierres, etc.)
            if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
            if isinstance(exc_val, OSError) and getattr(exc_val, "winerror", None) in (10053, 10054):
                return
            super().handle_error(request, client_address)

    settings = config.get_system_settings()
    is_lan = settings.get("network_mode") == "lan"
    bind_host = "0.0.0.0" if is_lan else "127.0.0.1"

    no_browser = ("--no-browser" in sys.argv) or (os.environ.get("WACKUP_NO_BROWSER") == "1")

    server = None
    bind_attempts = 15 if is_restart else 1
    for attempt in range(bind_attempts):
        try:
            server = WAckupHTTPServer((bind_host, PORT), BackupServerHandler)
            server.daemon_threads = True
            global _server_instance
            _server_instance = server
            break
        except OSError as e:
            if attempt < bind_attempts - 1:
                time.sleep(0.3)
            else:
                url = f"http://127.0.0.1:{PORT}"
                already_open = _running_instance_has_active_viewer(PORT)
                print(f"\n========================================================================")
                print(f"  [AVISO] Ya existe una instancia de WAckup activa en el puerto {PORT}.")
                if already_open or no_browser:
                    print(f"  El visor ya se encuentra abierto en tu navegador ({url}).")
                else:
                    print(f"  Abriendo el visor en tu navegador: {url}")
                print(f"========================================================================\n")
                if not already_open and not no_browser:
                    webbrowser.open(url, new=0, autoraise=True)
                return

    url = f"http://127.0.0.1:{PORT}"
    if is_lan:
        lan_ips = config.get_local_ip_addresses()
        lan_host = lan_ips[0] if lan_ips else "0.0.0.0"
        append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor] Modo Equipo LAN activo en {bind_host}:{PORT} (Acceso red: http://{lan_host}:{PORT})")
    else:
        append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor] Servidor iniciado en {url}")

    global _initial_total_msgs
    if accounts:
        _initial_total_msgs = sum(a.get("total_messages", 0) for a in accounts)

    console_mode = "--console" in sys.argv
    tray_instance = None
    if console_mode:
        render_dashboard(url)
        start_keyboard_listener(url)
        start_status_animator()
    else:
        print(f"\n========================================================================")
        print(f"  WAckup 3.0 Activo")
        print(f"  Servidor: {url}")
        print(f"  Bandeja del sistema: Activa junto al reloj de Windows")
        print(f"  (Usa 'python whatsapp_viewer.py --console' para atajos en terminal)")
        print(f"========================================================================\n")
        try:
            from tray_manager import TrayManager
            if TrayManager.is_available():
                tray_instance = TrayManager(
                    url=url,
                    port=PORT,
                    on_restart=restart_current_process,
                    on_exit=lambda: os._exit(0),
                    on_open=lambda: open_or_focus_viewer(url)
                )
                tray_instance.start()
            else:
                render_dashboard(url)
                start_keyboard_listener(url)
                start_status_animator()
        except Exception as e:
            render_dashboard(url)
            start_keyboard_listener(url)
            start_status_animator()

    if no_browser:
        pass
    elif is_restart and has_client_flag:
        if not console_mode:
            print("  [i] Pestaña activa detectada en el navegador: se actualizará automáticamente.")
    else:
        def _smart_open_browser():
            # Dar margen a que una pestaña ya abierta reconecte antes de abrir otra nueva
            deadline = time.time() + 2.2
            while time.time() < deadline:
                if _server_stopping:
                    return
                if has_active_web_viewer(max_age=12.0):
                    if not console_mode:
                        print("  [i] Pestaña activa detectada en el navegador: reutilizando ventana existente.")
                    return
                time.sleep(0.1)
            if not _server_stopping and not has_active_web_viewer(max_age=12.0):
                webbrowser.open(url, new=0, autoraise=True)

        threading.Thread(target=_smart_open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except (KeyboardInterrupt, OSError):
        pass
    finally:
        global _server_stopping
        _server_stopping = True
        try:
            sse_hub.get_sse_hub().shutdown()
        except Exception:
            pass
        if tray_instance:
            try:
                tray_instance.stop()
            except Exception:
                pass
        try:
            server.server_close()
        except Exception:
            pass

    # Si se solicitó reinicio
    if _is_restarting:
        append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor] Relanzando proceso...")
        # Si corre supervisado por iniciar_visor.bat, delegar el relanzamiento al bucle .bat con código 42
        if os.environ.get("WACKUP_SUPERVISED") == "1":
            os._exit(42)

        # Si corre autónomamente vía pythonw o terminal directa sin supervisor .bat
        try:
            script_path = os.path.abspath(__file__)
            env = os.environ.copy()
            env["WACKUP_RESTARTED"] = "1"
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x00000008 | 0x00000200
                if sys.executable.lower().endswith("pythonw.exe"):
                    creationflags |= 0x08000000
            subprocess.Popen(
                [sys.executable, script_path] + sys.argv[1:],
                cwd=os.path.dirname(script_path),
                env=env,
                close_fds=True,
                creationflags=creationflags
            )
            time.sleep(0.1)
            os._exit(0)
        except Exception as e:
            append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor Error Relanzamiento] {e}")
            os._exit(42)

    if _exit_code != 0:
        os._exit(_exit_code)

if __name__ == "__main__":
    run()