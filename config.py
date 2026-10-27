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
    if not account_id:
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


def migrate_single_to_multi_account(account_id, name=None, phone=None):
    """
    Si existe un archivo data/whatsapp_backup.db previo y ninguna carpeta de cuenta,
    lo migra automáticamente a data/<account_id>/ para no perder historial previo
    y limpia de forma segura el archivo redundante en data/.
    """
    if not account_id:
        return

    target_dir = get_account_dir(account_id)
    legacy_db = os.path.join(DATA_DIR, "whatsapp_backup.db")

    if not os.path.isfile(legacy_db):
        return

    ensure_account_dirs(account_id)
    target_db = os.path.join(target_dir, "whatsapp_backup.db")

    # 1. Si target_db no existe o está vacío (0 bytes), transferir el contenido de legacy_db
    if not os.path.exists(target_db) or os.path.getsize(target_db) == 0:
        for ext in ["", "-wal", "-shm"]:
            fsrc = f"{legacy_db}{ext}"
            fdst = os.path.join(target_dir, f"whatsapp_backup.db{ext}")
            if os.path.isfile(fsrc):
                try:
                    shutil.copy2(fsrc, fdst)
                    os.remove(fsrc)
                except Exception:
                    try:
                        shutil.move(fsrc, fdst)
                    except Exception:
                        pass
    else:
        # 2. Si target_db ya tiene datos consistentes, eliminar el archivo redundante en data/
        try:
            if os.path.getsize(target_db) >= os.path.getsize(legacy_db):
                for ext in ["", "-wal", "-shm"]:
                    fsrc = f"{legacy_db}{ext}"
                    if os.path.isfile(fsrc):
                        try:
                            os.remove(fsrc)
                        except Exception:
                            pass
        except Exception:
            pass

    # 3. Mover media/ existente a target_dir/media/
    legacy_media = os.path.join(DATA_DIR, "media")
    target_media = os.path.join(target_dir, "media")
    if os.path.isdir(legacy_media) and os.path.abspath(legacy_media) != os.path.abspath(target_media):
        try:
            for root, _, files in os.walk(legacy_media):
                rel = os.path.relpath(root, legacy_media)
                dst_folder = os.path.join(target_media, rel)
                os.makedirs(dst_folder, exist_ok=True)
                for f in files:
                    s = os.path.join(root, f)
                    d = os.path.join(dst_folder, f)
                    if not os.path.exists(d):
                        try:
                            shutil.move(s, d)
                        except Exception:
                            shutil.copy2(s, d)
            shutil.rmtree(legacy_media, ignore_errors=True)
        except Exception:
            pass

    # 4. Mover backups/ existentes
        legacy_bak = os.path.join(DATA_DIR, "backups")
        target_bak = os.path.join(target_dir, "backups")
        if os.path.isdir(legacy_bak) and os.path.abspath(legacy_bak) != os.path.abspath(target_bak):
            try:
                for f in os.listdir(legacy_bak):
                    s = os.path.join(legacy_bak, f)
                    d = os.path.join(target_bak, f)
                    if os.path.isfile(s) and not os.path.exists(d):
                        shutil.move(s, d)
                shutil.rmtree(legacy_bak, ignore_errors=True)
            except Exception:
                pass

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
