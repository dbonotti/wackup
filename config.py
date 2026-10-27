"""
Configuración centralizada de rutas y almacenamiento del proyecto WAckup (WhatsApp Backup Local).

Soporta arquitectura Multi-Cuenta:
Organiza los datos de cada número/cuenta de WhatsApp en una subcarpeta propia:
data/<account_id>/
    ├── whatsapp_backup.db (Base de datos SQLite)
    ├── media/             (Imágenes, audios, videos, documentos)
    ├── backups/           (Copias de seguridad de la cuenta)
    └── account.json       (Metadatos de la cuenta: nombre, teléfono, etc.)

Permite respaldar múltiples líneas sin mezclar mensajes ni contactos y mover
los datos de una cuenta o de todas simplemente copiando sus carpetas.
"""

import os
import re
import json
import time
import shutil
import sqlite3
from datetime import datetime

# Directorio raíz del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Directorio único que contiene toda la información generada por el usuario
DATA_DIR = os.path.join(BASE_DIR, "data")

# Directorio y archivo de logs de sincronización compartidos
LOG_DIR = os.path.join(DATA_DIR, "logs")
SYNC_LOG_PATH = os.path.join(LOG_DIR, "sync.log")

# Rutas heredadas / por defecto
DEFAULT_ACCOUNT_ID = "default"
DB_FILE = os.path.join(DATA_DIR, "whatsapp_backup.db")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

MEDIA_SUBDIRS = {
    "image": os.path.join(MEDIA_DIR, "images"),
    "sticker": os.path.join(MEDIA_DIR, "stickers"),
    "audio": os.path.join(MEDIA_DIR, "audio"),
    "ptt": os.path.join(MEDIA_DIR, "audio"),
    "video": os.path.join(MEDIA_DIR, "video"),
    "document": os.path.join(MEDIA_DIR, "documents"),
}


def sanitize_account_id(account_id):
    """Limpia el identificador de cuenta para ser una carpeta segura en el sistema de archivos."""
    if not account_id:
        return DEFAULT_ACCOUNT_ID
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '', str(account_id)).strip()
    return cleaned or DEFAULT_ACCOUNT_ID


def get_account_dir(account_id=None):
    """Devuelve la ruta absoluta al directorio de una cuenta específica."""
    aid = sanitize_account_id(account_id)
    return os.path.join(DATA_DIR, aid)


def get_account_db(account_id=None):
    """Devuelve la ruta al archivo SQLite de una cuenta específica."""
    if not account_id or account_id == DEFAULT_ACCOUNT_ID:
        accounts = list_accounts()
        real_accounts = [a for a in accounts if a["id"] != DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
        if real_accounts:
            return real_accounts[0]["db_path"]
        return get_default_db()
    acc_dir = get_account_dir(account_id)
    return os.path.join(acc_dir, "whatsapp_backup.db")


def get_account_media_dir(account_id=None):
    """Devuelve la ruta a la carpeta multimedia de una cuenta específica."""
    if not account_id:
        return MEDIA_DIR
    return os.path.join(get_account_dir(account_id), "media")


def get_account_backup_dir(account_id=None):
    """Devuelve la ruta a la carpeta de backups de una cuenta específica."""
    if not account_id:
        return BACKUP_DIR
    return os.path.join(get_account_dir(account_id), "backups")


def get_media_subfolder_for_db(db_path, media_type):
    """
    Determina la subcarpeta multimedia correcta para una base de datos dada
    (sea de una cuenta en data/<account_id>/ o heredada en data/).
    """
    account_dir = os.path.dirname(os.path.abspath(db_path))
    if account_dir == os.path.abspath(DATA_DIR):
        media_root = MEDIA_DIR
    else:
        media_root = os.path.join(account_dir, "media")

    type_subfolders = {
        "image": "images",
        "sticker": "stickers",
        "audio": "audio",
        "ptt": "audio",
        "video": "video",
        "document": "documents",
    }
    sub = os.path.join(media_root, type_subfolders.get(media_type, "documents"))
    os.makedirs(sub, exist_ok=True)
    return sub


def ensure_account_dirs(account_id=None):
    """Crea los directorios necesarios para una cuenta."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    if not account_id:
        ensure_data_dirs()
        return

    acc_dir = get_account_dir(account_id)
    os.makedirs(acc_dir, exist_ok=True)
    media_dir = os.path.join(acc_dir, "media")
    os.makedirs(media_dir, exist_ok=True)
    for sub in ["images", "stickers", "audio", "video", "documents"]:
        os.makedirs(os.path.join(media_dir, sub), exist_ok=True)
    os.makedirs(os.path.join(acc_dir, "backups"), exist_ok=True)


def ensure_data_dirs():
    """Crea los directorios raíz y por defecto si aún no existen."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    for sub in MEDIA_SUBDIRS.values():
        os.makedirs(sub, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def save_account_metadata(account_id, name=None, phone=None):
    """Guarda o actualiza el archivo account.json para la cuenta indicada."""
    if not account_id:
        return
    if isinstance(name, dict):
        d = name
        name = d.get("name")
        phone = d.get("phone") or phone
    ensure_account_dirs(account_id)
    meta_path = os.path.join(get_account_dir(account_id), "account.json")
    data = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data["id"] = sanitize_account_id(account_id)
    if phone:
        data["phone"] = str(phone)
    elif "phone" not in data:
        data["phone"] = data["id"] if data["id"].isdigit() else ""

    if name:
        data["name"] = str(name)
    elif "name" not in data:
        data["name"] = f"+{data['phone']}" if data.get("phone") else data["id"]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "created_at" not in data:
        data["created_at"] = now_str
    data["updated_at"] = now_str

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def list_accounts():
    """
    Descubre y devuelve todas las cuentas disponibles en data/.
    Retorna una lista de diccionarios con metadatos de cada cuenta.
    """
    accounts = []
    if not os.path.isdir(DATA_DIR):
        return accounts

    # 1. Cuentas en subdirectorios data/<account_id>/
    for item in os.listdir(DATA_DIR):
        if item.lower() in ("logs", "__pycache__", "media", "backups"):
            continue
        item_path = os.path.join(DATA_DIR, item)
        if not os.path.isdir(item_path):
            continue

        db_path = os.path.join(item_path, "whatsapp_backup.db")
        meta_path = os.path.join(item_path, "account.json")

        meta = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass

        aid = meta.get("id") or item
        phone = meta.get("phone") or (item if item.isdigit() else "")
        name = meta.get("name") or (f"+{phone}" if phone else aid)

        total_msgs = 0
        total_chats = 0
        last_ts = 0

        if os.path.isfile(db_path):
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM mensajes")
                total_msgs = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chats")
                total_chats = cur.fetchone()[0]
                cur.execute("SELECT MAX(timestamp) FROM mensajes")
                r = cur.fetchone()[0]
                last_ts = r or 0
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cuenta'")
                if cur.fetchone():
                    cur.execute("SELECT nombre, telefono FROM cuenta LIMIT 1")
                    crow = cur.fetchone()
                    if crow:
                        if not meta.get("name") and crow[0]:
                            name = crow[0]
                        if not meta.get("phone") and crow[1]:
                            phone = crow[1]
                conn.close()
            except Exception:
                pass

        # Si es la carpeta 'default' vacía (0 mensajes), no listarla como cuenta
        if aid == DEFAULT_ACCOUNT_ID and total_msgs == 0:
            continue

        accounts.append({
            "id": aid,
            "name": name,
            "phone": phone,
            "dir": item_path,
            "db_path": db_path,
            "total_messages": total_msgs,
            "total_chats": total_chats,
            "last_timestamp": last_ts,
            "updated_at": meta.get("updated_at") or ""
        })

    # Si hay cuentas reales identificadas y con mensajes, descartar 'default' huérfana
    real_accounts = [a for a in accounts if a["id"] != DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
    if real_accounts:
        accounts = [a for a in accounts if a["id"] != DEFAULT_ACCOUNT_ID]

    # 2. Si existe data/whatsapp_backup.db suelta y no hay cuentas creadas aún
    legacy_db = os.path.join(DATA_DIR, "whatsapp_backup.db")
    if os.path.isfile(legacy_db) and not accounts:
        total_msgs = 0
        total_chats = 0
        last_ts = 0
        phone = ""
        name = "Cuenta Principal"
        try:
            conn = sqlite3.connect(legacy_db, timeout=5.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM mensajes")
            total_msgs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM chats")
            total_chats = cur.fetchone()[0]
            cur.execute("SELECT MAX(timestamp) FROM mensajes")
            r = cur.fetchone()[0]
            last_ts = r or 0
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cuenta'")
            if cur.fetchone():
                cur.execute("SELECT nombre, telefono FROM cuenta LIMIT 1")
                crow = cur.fetchone()
                if crow:
                    if crow[0]: name = crow[0]
                    if crow[1]: phone = crow[1]
            conn.close()
        except Exception:
            pass

        accounts.append({
            "id": DEFAULT_ACCOUNT_ID,
            "name": name,
            "phone": phone,
            "dir": DATA_DIR,
            "db_path": legacy_db,
            "total_messages": total_msgs,
            "total_chats": total_chats,
            "last_timestamp": last_ts,
            "updated_at": ""
        })

    # Ordenar por último timestamp o actividad reciente descendente
    accounts.sort(key=lambda a: (a["last_timestamp"] or 0, a["updated_at"] or ""), reverse=True)
    return accounts


def get_default_db():
    """Devuelve la base de datos por defecto (la primera activa o fallback)."""
    accounts = list_accounts()
    if accounts:
        return accounts[0]["db_path"]
    return DB_FILE


def merge_account_databases(source_db, target_db, target_account_id):
    """
    Fusiona de forma consistente contactos, chats, mensajes y multimedia
    desde una base de datos origen a una base de datos destino SQLite.
    Actualiza FTS5 y normaliza rutas locales de multimedia.
    """
    if not os.path.isfile(source_db) or not os.path.isfile(target_db):
        return False

    try:
        conn = sqlite3.connect(target_db, timeout=30.0)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute("ATTACH DATABASE ? AS src", (source_db,))

        # 1. Contactos
        cur.execute("""
            INSERT INTO contactos (contact_id, nombre, push_name, numero, foto)
            SELECT contact_id, nombre, push_name, numero, foto FROM src.contactos
            WHERE contact_id NOT IN (SELECT contact_id FROM contactos)
        """)
        cur.execute("""
            UPDATE contactos SET
                nombre = COALESCE(NULLIF(contactos.nombre, ''), (SELECT NULLIF(s.nombre, '') FROM src.contactos s WHERE s.contact_id = contactos.contact_id)),
                push_name = COALESCE(NULLIF(contactos.push_name, ''), (SELECT NULLIF(s.push_name, '') FROM src.contactos s WHERE s.contact_id = contactos.contact_id)),
                numero = COALESCE(NULLIF(contactos.numero, ''), (SELECT NULLIF(s.numero, '') FROM src.contactos s WHERE s.contact_id = contactos.contact_id)),
                foto = COALESCE(NULLIF(contactos.foto, ''), (SELECT NULLIF(s.foto, '') FROM src.contactos s WHERE s.contact_id = contactos.contact_id))
            WHERE contact_id IN (SELECT contact_id FROM src.contactos)
        """)

        # 2. Chats
        cur.execute("""
            INSERT INTO chats (chat_id, nombre, no_leidos, ultimo_timestamp)
            SELECT chat_id, nombre, no_leidos, ultimo_timestamp FROM src.chats
            WHERE chat_id NOT IN (SELECT chat_id FROM chats)
        """)
        cur.execute("""
            UPDATE chats SET
                ultimo_timestamp = MAX(COALESCE(ultimo_timestamp, 0), (SELECT COALESCE(s.ultimo_timestamp, 0) FROM src.chats s WHERE s.chat_id = chats.chat_id)),
                nombre = COALESCE(NULLIF(chats.nombre, ''), (SELECT NULLIF(s.nombre, '') FROM src.chats s WHERE s.chat_id = chats.chat_id))
            WHERE chat_id IN (SELECT chat_id FROM src.chats)
        """)

        # 3. Mensajes
        cur.execute("""
            INSERT OR IGNORE INTO mensajes (
                msg_id, chat_id, remitente, es_mio, fecha_utc, timestamp, tipo, contenido,
                archivo_local, mimetype, tamano, duracion, thumbnail, estado_multimedia,
                quoted_msg_id, quoted_author, quoted_body, reenviado
            )
            SELECT 
                msg_id, chat_id, remitente, es_mio, fecha_utc, timestamp, tipo, contenido,
                archivo_local, mimetype, tamano, duracion, thumbnail, estado_multimedia,
                quoted_msg_id, quoted_author, quoted_body, reenviado
            FROM src.mensajes
        """)
        cur.execute("""
            UPDATE mensajes SET
                estado_multimedia = 'descargado',
                archivo_local = (SELECT s.archivo_local FROM src.mensajes s WHERE s.msg_id = mensajes.msg_id),
                tamano = (SELECT s.tamano FROM src.mensajes s WHERE s.msg_id = mensajes.msg_id)
            WHERE msg_id IN (
                SELECT s.msg_id FROM src.mensajes s WHERE s.estado_multimedia = 'descargado'
            ) AND (estado_multimedia != 'descargado' OR archivo_local IS NULL OR archivo_local = '')
        """)

        # 4. Multimedia
        cur.execute("""
            INSERT OR IGNORE INTO multimedia (
                msg_id, chat_id, tipo, mimetype, media_key, direct_path, enc_filehash, filehash,
                tamano, duracion, nombre_archivo, archivo_local, estado, intentos, error_msg, fecha_registro
            )
            SELECT 
                msg_id, chat_id, tipo, mimetype, media_key, direct_path, enc_filehash, filehash,
                tamano, duracion, nombre_archivo, archivo_local, estado, intentos, error_msg, fecha_registro
            FROM src.multimedia
        """)
        cur.execute("""
            UPDATE multimedia SET
                estado = 'descargado',
                archivo_local = (SELECT s.archivo_local FROM src.multimedia s WHERE s.msg_id = multimedia.msg_id),
                tamano = (SELECT s.tamano FROM src.multimedia s WHERE s.msg_id = multimedia.msg_id),
                error_msg = NULL
            WHERE msg_id IN (
                SELECT s.msg_id FROM src.multimedia s WHERE s.estado = 'descargado'
            ) AND (estado != 'descargado' OR archivo_local IS NULL OR archivo_local = '')
        """)

        # 5. Normalizar rutas de archivos locales en la cuenta de destino
        target_prefix = f"data/{target_account_id}/"
        cur.execute("UPDATE mensajes SET archivo_local = ? || archivo_local WHERE archivo_local LIKE 'media/%'", (target_prefix,))
        cur.execute("UPDATE mensajes SET archivo_local = replace(archivo_local, 'data/default/media/', ?) WHERE archivo_local LIKE 'data/default/media/%'", (f"data/{target_account_id}/media/",))
        cur.execute("UPDATE multimedia SET archivo_local = ? || archivo_local WHERE archivo_local LIKE 'media/%'", (target_prefix,))
        cur.execute("UPDATE multimedia SET archivo_local = replace(archivo_local, 'data/default/media/', ?) WHERE archivo_local LIKE 'data/default/media/%'", (f"data/{target_account_id}/media/",))

        # 6. Reconstruir FTS5 si la tabla existe
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mensajes_fts'")
        if cur.fetchone():
            cur.execute("DELETE FROM mensajes_fts")
            cur.execute("""
                INSERT INTO mensajes_fts (msg_id, chat_id, contenido, remitente_nombre)
                SELECT 
                    m.msg_id, 
                    m.chat_id, 
                    COALESCE(m.contenido, ''),
                    COALESCE(c.nombre, c.push_name, m.remitente, '')
                FROM mensajes m
                LEFT JOIN contactos c ON m.remitente = c.contact_id
                WHERE m.contenido IS NOT NULL AND length(m.contenido) > 0
            """)

        conn.commit()
        try:
            cur.execute("DETACH DATABASE src")
        except Exception:
            pass
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Error al fusionar bases de datos: {e}")
        return False


def migrate_single_to_multi_account(account_id, name=None, phone=None):
    """
    Migración y unificación integral a arquitectura multi-cuenta:
    Si existe un archivo data/whatsapp_backup.db previo o una carpeta previa data/default/,
    fusiona todo el historial (mensajes, contactos, multimedia, backups) hacia data/<account_id>/
    sin perder ningún dato y archiva las carpetas redundantes de forma segura.
    """
    if not account_id or account_id == DEFAULT_ACCOUNT_ID:
        return

    target_dir = get_account_dir(account_id)
    ensure_account_dirs(account_id)
    target_db = os.path.join(target_dir, "whatsapp_backup.db")

    def _transfer_media(src_media_dir, dst_media_dir):
        if not os.path.isdir(src_media_dir) or os.path.abspath(src_media_dir) == os.path.abspath(dst_media_dir):
            return
        os.makedirs(dst_media_dir, exist_ok=True)
        try:
            for root, _, files in os.walk(src_media_dir):
                rel = os.path.relpath(root, src_media_dir)
                out_folder = os.path.join(dst_media_dir, rel)
                os.makedirs(out_folder, exist_ok=True)
                for f in files:
                    s = os.path.join(root, f)
                    d = os.path.join(out_folder, f)
                    if not os.path.exists(d) or os.path.getsize(d) == 0:
                        try:
                            shutil.move(s, d)
                        except Exception:
                            shutil.copy2(s, d)
            shutil.rmtree(src_media_dir, ignore_errors=True)
        except Exception as e:
            print(f"[WARN] Error transfiriendo media: {e}")

    def _transfer_backups(src_bak_dir, dst_bak_dir):
        if not os.path.isdir(src_bak_dir) or os.path.abspath(src_bak_dir) == os.path.abspath(dst_bak_dir):
            return
        os.makedirs(dst_bak_dir, exist_ok=True)
        try:
            for f in os.listdir(src_bak_dir):
                s = os.path.join(src_bak_dir, f)
                d = os.path.join(dst_bak_dir, f)
                if os.path.isfile(s) and not os.path.exists(d):
                    try:
                        shutil.move(s, d)
                    except Exception:
                        pass
            shutil.rmtree(src_bak_dir, ignore_errors=True)
        except Exception:
            pass

    target_media = os.path.join(target_dir, "media")
    target_bak = os.path.join(target_dir, "backups")

    # 1. CASO 1: data/whatsapp_backup.db suelta en data/
    legacy_db = os.path.join(DATA_DIR, "whatsapp_backup.db")
    if os.path.isfile(legacy_db):
        if not os.path.exists(target_db) or os.path.getsize(target_db) == 0:
            for ext in ["", "-wal", "-shm"]:
                fsrc = f"{legacy_db}{ext}"
                fdst = f"{target_db}{ext}"
                if os.path.isfile(fsrc):
                    try:
                        shutil.move(fsrc, fdst)
                    except Exception:
                        pass
        else:
            merge_account_databases(legacy_db, target_db, account_id)
            for ext in ["", "-wal", "-shm"]:
                try:
                    os.remove(f"{legacy_db}{ext}")
                except Exception:
                    pass

        _transfer_media(os.path.join(DATA_DIR, "media"), target_media)
        _transfer_backups(os.path.join(DATA_DIR, "backups"), target_bak)

    # 2. CASO 2: data/default/ (carpeta default previa a la extracción del teléfono)
    default_dir = os.path.join(DATA_DIR, DEFAULT_ACCOUNT_ID)
    default_db = os.path.join(default_dir, "whatsapp_backup.db")
    if os.path.isdir(default_dir) and os.path.abspath(default_dir) != os.path.abspath(target_dir):
        if os.path.isfile(default_db):
            has_data = False
            try:
                c_test = sqlite3.connect(default_db, timeout=5.0)
                cur_t = c_test.cursor()
                cur_t.execute("SELECT COUNT(*) FROM mensajes")
                has_data = cur_t.fetchone()[0] > 0
                c_test.close()
            except Exception:
                pass

            if has_data:
                if not os.path.exists(target_db) or os.path.getsize(target_db) == 0:
                    for ext in ["", "-wal", "-shm"]:
                        fsrc = f"{default_db}{ext}"
                        fdst = f"{target_db}{ext}"
                        if os.path.isfile(fsrc):
                            try:
                                shutil.move(fsrc, fdst)
                            except Exception:
                                pass
                else:
                    merge_account_databases(default_db, target_db, account_id)

                _transfer_media(os.path.join(default_dir, "media"), target_media)
                _transfer_backups(os.path.join(default_dir, "backups"), target_bak)

        # Archivar o limpiar default_dir de forma segura
        try:
            bak_archive = os.path.join(DATA_DIR, "backups", f"migrated_default_{int(time.time())}")
            os.makedirs(os.path.dirname(bak_archive), exist_ok=True)
            shutil.move(default_dir, bak_archive)
        except Exception:
            shutil.rmtree(default_dir, ignore_errors=True)

    save_account_metadata(account_id, name=name, phone=phone)


def backup_all_databases():
    """Genera copias preventivas de seguridad para todas las cuentas antes de una actualización."""
    accounts = list_accounts()
    ts = int(time.time())
    backed_up = 0
    if not accounts:
        if os.path.isfile(DB_FILE):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            bak = os.path.join(BACKUP_DIR, f"db_backup_pre_update_{ts}.db")
            shutil.copy2(DB_FILE, bak)
            backed_up += 1
    else:
        for acc in accounts:
            dbp = acc.get("db_path")
            if dbp and os.path.isfile(dbp):
                bdir = os.path.join(acc["dir"], "backups")
                os.makedirs(bdir, exist_ok=True)
                bak = os.path.join(bdir, f"db_backup_pre_update_{ts}.db")
                shutil.copy2(dbp, bak)
                backed_up += 1
    print(f"[OK] Copia preventiva de seguridad creada para {backed_up} cuenta(s).")


def migrate_legacy_data():
    """
    Migración automática y transparente:
    Si existen bases de datos, carpetas de multimedia, backups o logs en la raíz
    (versiones anteriores), los mueve a la nueva estructura unificada en data/.
    """
    ensure_data_dirs()

    # 1. Base de datos SQLite y archivos asociados (WAL / SHM)
    for ext in ["", "-wal", "-shm"]:
        legacy_db = os.path.join(BASE_DIR, f"whatsapp_backup.db{ext}")
        target_db = os.path.join(DATA_DIR, f"whatsapp_backup.db{ext}")
        if os.path.isfile(legacy_db) and not os.path.exists(target_db):
            try:
                shutil.move(legacy_db, target_db)
            except Exception:
                pass

    # 2. Archivos multimedia en 'media/'
    legacy_media = os.path.join(BASE_DIR, "media")
    if os.path.isdir(legacy_media) and os.path.abspath(legacy_media) != os.path.abspath(MEDIA_DIR):
        try:
            for root, _, files in os.walk(legacy_media):
                rel = os.path.relpath(root, legacy_media)
                target_sub = os.path.join(MEDIA_DIR, rel)
                os.makedirs(target_sub, exist_ok=True)
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(target_sub, f)
                    if not os.path.exists(dst_f):
                        shutil.move(src_f, dst_f)
            shutil.rmtree(legacy_media, ignore_errors=True)
        except Exception:
            pass

    # 3. Copias de seguridad en 'backups/'
    legacy_backups = os.path.join(BASE_DIR, "backups")
    if os.path.isdir(legacy_backups) and os.path.abspath(legacy_backups) != os.path.abspath(BACKUP_DIR):
        try:
            for f in os.listdir(legacy_backups):
                src_f = os.path.join(legacy_backups, f)
                dst_f = os.path.join(BACKUP_DIR, f)
                if os.path.isfile(src_f) and not os.path.exists(dst_f):
                    shutil.move(src_f, dst_f)
            shutil.rmtree(legacy_backups, ignore_errors=True)
        except Exception:
            pass

    # 4. Logs en 'logs/'
    legacy_logs = os.path.join(BASE_DIR, "logs")
    if os.path.isdir(legacy_logs) and os.path.abspath(legacy_logs) != os.path.abspath(LOG_DIR):
        try:
            for f in os.listdir(legacy_logs):
                src_f = os.path.join(legacy_logs, f)
                dst_f = os.path.join(LOG_DIR, f)
                if os.path.isfile(src_f) and not os.path.exists(dst_f):
                    shutil.move(src_f, dst_f)
            shutil.rmtree(legacy_logs, ignore_errors=True)
        except Exception:
            pass

    # 5. Unificación automática: si existe data/default/ o data/whatsapp_backup.db con datos y existe una cuenta real
    try:
        real_accounts = [a for a in list_accounts() if a["id"] != DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
        if real_accounts:
            target_account = real_accounts[0]["id"]
            legacy_db = os.path.join(DATA_DIR, "whatsapp_backup.db")
            default_dir = os.path.join(DATA_DIR, DEFAULT_ACCOUNT_ID)
            if os.path.isfile(legacy_db) or os.path.isdir(default_dir):
                migrate_single_to_multi_account(target_account)
    except Exception as e:
        print(f"[WARN] Error en unificación automática al inicio: {e}")
