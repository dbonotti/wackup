import os
import re
import base64
import urllib.request
import urllib.error
import mimetypes
import threading
import sqlite3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from db_manager import DB_FILE, get_connection
import config
from config import BASE_DIR, MEDIA_DIR, MEDIA_SUBDIRS, SYNC_LOG_PATH, ensure_data_dirs

INFO_STRINGS = {
    "image": b"WhatsApp Image Keys",
    "sticker": b"WhatsApp Image Keys",
    "video": b"WhatsApp Video Keys",
    "audio": b"WhatsApp Audio Keys",
    "ptt": b"WhatsApp Audio Keys",
    "document": b"WhatsApp Document Keys",
}

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/ogg; codecs=opus": ".ogg",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "text/plain": ".txt"
}

def ensure_media_dirs():
    """Crea los directorios para almacenar archivos multimedia."""
    ensure_data_dirs()

def sanitize_filename(name):
    """Limpia un nombre de archivo para ser seguro en el sistema de archivos."""
    return re.sub(r'[\/:*?"<>|]', '_', name)

def derive_media_keys(media_key_bytes, media_type):
    """
    Expande el mediaKey (32 bytes) usando HKDF-SHA256 con salt de 32 ceros
    y la info correspondiente al tipo de archivo.
    Retorna (iv: 16 bytes, cipher_key: 32 bytes, mac_key: 32 bytes).
    """
    app_info = INFO_STRINGS.get(media_type, b"WhatsApp Image Keys")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=112,
        salt=b"\x00" * 32,
        info=app_info,
        backend=default_backend()
    )
    expanded = hkdf.derive(media_key_bytes)

    iv = expanded[:16]
    cipher_key = expanded[16:48]
    mac_key = expanded[48:80]
    return iv, cipher_key, mac_key

def decrypt_media_buffer(enc_bytes, media_key_bytes, media_type):
    """Descifra el buffer binario usando AES-256-CBC y remueve el padding PKCS7."""
    if len(enc_bytes) <= 10:
        raise ValueError("Buffer cifrado demasiado corto (menor o igual a 10 bytes)")

    # Los últimos 10 bytes corresponden al MAC de WhatsApp
    ciphertext = enc_bytes[:-10]
    iv, cipher_key, _ = derive_media_keys(media_key_bytes, media_type)

    cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(ciphertext) + decryptor.finalize()

    # Quitar padding PKCS7 si es válido
    if len(padded_data) > 0:
        pad_len = padded_data[-1]
        if 1 <= pad_len <= 16 and padded_data[-pad_len:] == bytes([pad_len]) * pad_len:
            return padded_data[:-pad_len]

    return padded_data

def download_and_decrypt(msg_id, media_key_b64, direct_path, media_type, mimetype=None, filename=None, db_path=DB_FILE):
    """
    Descarga el archivo cifrado desde la CDN de WhatsApp, lo descifra con la clave
    y lo almacena localmente, actualizando las tablas SQLite.
    """
    ensure_media_dirs()

    # Construir URL CDN
    if direct_path.startswith("http://") or direct_path.startswith("https://"):
        cdn_url = direct_path
    else:
        cdn_url = f"https://mmg.whatsapp.net{direct_path}"

    # Decodificar clave en bytes
    try:
        if isinstance(media_key_b64, bytes):
            media_key_bytes = media_key_b64
        elif isinstance(media_key_b64, list):
            media_key_bytes = bytes(media_key_b64)
        else:
            media_key_bytes = base64.b64decode(media_key_b64)
    except Exception as e:
        _update_status(msg_id, "error", f"Clave base64 inválida: {e}", db_path)
        return False

    # Descargar archivo cifrado
    req = urllib.request.Request(
        cdn_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            enc_data = resp.read()
    except urllib.error.HTTPError as e:
        # 404 / 410 indica que el archivo expiró en los servidores de WhatsApp
        if e.code in [404, 410]:
            _update_status(msg_id, "expirado", f"HTTP {e.code} en CDN (archivo expirado en servidores)", db_path)
        else:
            _update_status(msg_id, "error", f"HTTP {e.code} al descargar", db_path)
        return False
    except Exception as e:
        _update_status(msg_id, "error", f"Fallo de conexión CDN: {e}", db_path)
        return False

    # Descifrar
    try:
        decrypted_data = decrypt_media_buffer(enc_data, media_key_bytes, media_type)
    except Exception as e:
        _update_status(msg_id, "error", f"Fallo al descifrar: {e}", db_path)
        return False

    # Determinar extensión y destino
    ext = ""
    if filename and "." in filename:
        ext = os.path.splitext(filename)[1].lower()
    if not ext and mimetype:
        ext = MIME_EXTENSIONS.get(mimetype.split(";")[0].strip()) or mimetypes.guess_extension(mimetype) or ""
    if not ext:
        defaults = {"image": ".jpg", "sticker": ".webp", "audio": ".ogg", "ptt": ".ogg", "video": ".mp4", "document": ".bin"}
        ext = defaults.get(media_type, ".bin")

    subfolder = config.get_media_subfolder_for_db(db_path, media_type)
    safe_id = sanitize_filename(msg_id)
    final_filename = f"{safe_id}{ext}"
    out_path = os.path.join(subfolder, final_filename)
    rel_path = os.path.relpath(out_path, start=BASE_DIR).replace("\\", "/")

    try:
        with open(out_path, "wb") as f:
            f.write(decrypted_data)
    except Exception as e:
        _update_status(msg_id, "error", f"Error guardando archivo: {e}", db_path)
        return False

    # Actualizar base de datos con éxito
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        UPDATE multimedia SET
            archivo_local = ?,
            tamano = ?,
            estado = 'descargado',
            error_msg = NULL
        WHERE msg_id = ?
    """, (rel_path, len(decrypted_data), msg_id))

    cur.execute("""
        UPDATE mensajes SET
            archivo_local = ?,
            estado_multimedia = 'descargado',
            tamano = ?
        WHERE msg_id = ?
    """, (rel_path, len(decrypted_data), msg_id))

    conn.commit()
    conn.close()
    return True

def _update_status(msg_id, estado, error_msg=None, db_path=DB_FILE):
    """Actualiza el estado de error o expiración en la base de datos."""
    try:
        conn = get_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
            UPDATE multimedia SET
                estado = ?,
                intentos = intentos + 1,
                error_msg = ?
            WHERE msg_id = ?
        """, (estado, error_msg, msg_id))

        cur.execute("""
            UPDATE mensajes SET
                estado_multimedia = ?
            WHERE msg_id = ?
        """, (estado, msg_id))
        conn.commit()
        conn.close()
    except Exception:
        pass

def log_media_msg(msg):
    try:
        os.makedirs(os.path.dirname(SYNC_LOG_PATH), exist_ok=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SYNC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] {msg}\n")
    except Exception:
        pass

class MediaDownloadWorker:
    """Administrador de descargas en segundo plano con soporte multi-cuenta."""
    def __init__(self, max_workers=3):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.is_running = False
        self._lock = threading.Lock()
        self.pending_databases = set()

    def process_pending(self, db_path=None, limit=100):
        with self._lock:
            if db_path:
                self.pending_databases.add(os.path.abspath(db_path))
            else:
                accounts = config.list_accounts()
                if accounts:
                    for acc in accounts:
                        if acc.get("db_path") and os.path.isfile(acc["db_path"]):
                            self.pending_databases.add(os.path.abspath(acc["db_path"]))
                elif os.path.isfile(DB_FILE):
                    self.pending_databases.add(os.path.abspath(DB_FILE))

            if self.is_running:
                return
            self.is_running = True

        threading.Thread(target=self._run_all_batches, args=(limit,), daemon=True).start()

    def _run_all_batches(self, limit):
        if not HAS_CRYPTO:
            log_media_msg("[MediaWorker] Aviso: Instala 'cryptography' (pip install -r requirements.txt) para descifrar multimedia.")
            with self._lock:
                self.is_running = False
            return
        try:
            while True:
                target_db = None
                with self._lock:
                    if self.pending_databases:
                        target_db = self.pending_databases.pop()
                    else:
                        break

                if not target_db or not os.path.isfile(target_db):
                    continue

                self._process_single_db(target_db, limit)
        finally:
            with self._lock:
                self.is_running = False

    def _process_single_db(self, db_path, limit):
        processed_ids = set()
        acc_name = os.path.basename(os.path.dirname(db_path))
        while True:
            try:
                conn = get_connection(db_path)
                cur = conn.cursor()
                cur.execute("""
                    SELECT msg_id, media_key, direct_path, tipo, mimetype, nombre_archivo
                    FROM multimedia
                    WHERE estado = 'pendiente' AND intentos < 3 AND media_key IS NOT NULL AND direct_path IS NOT NULL
                    ORDER BY rowid DESC
                    LIMIT ?
                """, (limit,))
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
            except Exception as e:
                log_media_msg(f"[MediaWorker] Error al consultar BD {acc_name}: {e}")
                break

            new_rows = [r for r in rows if r["msg_id"] not in processed_ids]
            if not new_rows:
                break

            for r in new_rows:
                processed_ids.add(r["msg_id"])

            log_media_msg(f"[MediaWorker] Procesando lote de {len(new_rows)} multimedia ({acc_name})...")
            futures = []
            for r in new_rows:
                f = self.executor.submit(
                    download_and_decrypt,
                    r["msg_id"],
                    r["media_key"],
                    r["direct_path"],
                    r["tipo"],
                    r["mimetype"],
                    r["nombre_archivo"],
                    db_path
                )
                futures.append(f)

            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    log_media_msg(f"[MediaWorker] Error en hilo de descarga ({acc_name}): {e}")

# Instancia global del worker
worker = MediaDownloadWorker()

def trigger_media_processing(db_path=None):
    worker.process_pending(db_path)
