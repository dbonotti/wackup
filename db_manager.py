import sqlite3
import os
import time
import re
import html
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
import config
from config import DB_FILE, BACKUP_DIR, ensure_data_dirs

_INITIALIZED_DBS = set()
_INITIALIZED_LOCK = threading.Lock()

def ensure_db_initialized(db_path=DB_FILE):
    """
    Garantiza que la base de datos se inicialice una única vez por proceso
    para eliminar drop de triggers, reconstrucción de FTS5 y bloqueos repetitivos en cada sync.
    """
    if not db_path:
        db_path = DB_FILE
    norm_path = os.path.abspath(db_path)
    if norm_path in _INITIALIZED_DBS and os.path.isfile(norm_path):
        return
    with _INITIALIZED_LOCK:
        if norm_path not in _INITIALIZED_DBS or not os.path.isfile(norm_path):
            init_db(norm_path)
            _INITIALIZED_DBS.add(norm_path)

@contextmanager
def db_connection(db_path=DB_FILE, write=False):
    """
    Context manager robusto para conexiones SQLite con WAL mode y timeout de 60s.
    Si write=True, inicia transacción BEGIN IMMEDIATE para prevenir bloqueos por concurrencia.
    Asegura commit al finalizar o rollback en caso de excepción, cerrando siempre la conexión.
    """
    ensure_db_initialized(db_path)
    conn = get_connection(db_path, check_init=False)
    if write:
        conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

def get_connection(db_path=DB_FILE, check_init=True):
    ensure_data_dirs()
    if db_path:
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    if check_init:
        ensure_db_initialized(db_path)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    # Activar WAL mode y timeout para alta concurrencia
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=60000;")
    return conn

def init_db(db_path=DB_FILE):
    """Inicializa la base de datos, aplica migraciones de columnas y configura FTS5."""
    conn = get_connection(db_path, check_init=False)
    cur = conn.cursor()

    # 1. Esquema base
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS cuenta (
        account_id TEXT PRIMARY KEY,
        nombre TEXT,
        telefono TEXT,
        actualizado_en TEXT
    );

    CREATE TABLE IF NOT EXISTS contactos (
        contact_id TEXT PRIMARY KEY,
        nombre TEXT,
        push_name TEXT,
        numero TEXT,
        foto TEXT
    );

    CREATE TABLE IF NOT EXISTS chats (
        chat_id TEXT PRIMARY KEY,
        nombre TEXT,
        no_leidos INTEGER DEFAULT 0,
        ultimo_timestamp INTEGER,
        ultimo_mensaje TEXT,
        ultimo_es_mio INTEGER,
        ultimo_tipo TEXT
    );

    CREATE TABLE IF NOT EXISTS mensajes (
        msg_id TEXT PRIMARY KEY,
        chat_id TEXT,
        remitente TEXT,
        es_mio INTEGER DEFAULT 0,
        fecha_utc TEXT,
        timestamp INTEGER,
        tipo TEXT DEFAULT 'chat',
        contenido TEXT,
        FOREIGN KEY (chat_id) REFERENCES chats (chat_id)
    );

    CREATE INDEX IF NOT EXISTS idx_mensajes_chat ON mensajes (chat_id);
    CREATE INDEX IF NOT EXISTS idx_mensajes_fecha ON mensajes (timestamp);
    CREATE INDEX IF NOT EXISTS idx_mensajes_tipo ON mensajes (tipo);
    CREATE INDEX IF NOT EXISTS idx_mensajes_chat_ts ON mensajes (chat_id, timestamp DESC);
    """)

    # 2. Migraciones: Añadir columnas que puedan faltar en tablas existentes
    # contactos
    cur.execute("PRAGMA table_info(contactos)")
    cols_contactos = [row["name"] for row in cur.fetchall()]
    if "foto" not in cols_contactos:
        cur.execute("ALTER TABLE contactos ADD COLUMN foto TEXT")

    # chats
    cur.execute("PRAGMA table_info(chats)")
    cols_chats = [row["name"] for row in cur.fetchall()]
    if "ultimo_timestamp" not in cols_chats:
        cur.execute("ALTER TABLE chats ADD COLUMN ultimo_timestamp INTEGER")
    if "ultimo_mensaje" not in cols_chats:
        cur.execute("ALTER TABLE chats ADD COLUMN ultimo_mensaje TEXT")
    if "ultimo_es_mio" not in cols_chats:
        cur.execute("ALTER TABLE chats ADD COLUMN ultimo_es_mio INTEGER")
    if "ultimo_tipo" not in cols_chats:
        cur.execute("ALTER TABLE chats ADD COLUMN ultimo_tipo TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chats_ultimo_ts ON chats (ultimo_timestamp DESC)")

    # Poblar de forma idempotente las columnas desnormalizadas en chats existentes si están vacías
    cur.execute("""
        UPDATE chats
        SET 
            ultimo_mensaje = (
                SELECT contenido FROM mensajes m 
                WHERE m.chat_id = chats.chat_id 
                  AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
                ORDER BY timestamp DESC LIMIT 1
            ),
            ultimo_es_mio = (
                SELECT es_mio FROM mensajes m 
                WHERE m.chat_id = chats.chat_id 
                  AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
                ORDER BY timestamp DESC LIMIT 1
            ),
            ultimo_tipo = (
                SELECT tipo FROM mensajes m 
                WHERE m.chat_id = chats.chat_id 
                  AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
                ORDER BY timestamp DESC LIMIT 1
            )
        WHERE ultimo_mensaje IS NULL AND ultimo_timestamp IS NOT NULL
    """)

    # mensajes
    cur.execute("PRAGMA table_info(mensajes)")
    columnas_actuales = [row["name"] for row in cur.fetchall()]

    columnas_nuevas = {
        "archivo_local": "TEXT",
        "mimetype": "TEXT",
        "tamano": "INTEGER",
        "duracion": "REAL",
        "thumbnail": "TEXT",
        "estado_multimedia": "TEXT DEFAULT 'none'",
        "quoted_msg_id": "TEXT",
        "quoted_author": "TEXT",
        "quoted_body": "TEXT",
        "reenviado": "INTEGER"
    }

    for col, col_type in columnas_nuevas.items():
        if col not in columnas_actuales:
            cur.execute(f"ALTER TABLE mensajes ADD COLUMN {col} {col_type}")

    # 3. Tabla dedicada para gestión de descargas multimedia
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS multimedia (
        msg_id TEXT PRIMARY KEY,
        chat_id TEXT,
        tipo TEXT,
        mimetype TEXT,
        media_key TEXT,
        direct_path TEXT,
        enc_filehash TEXT,
        filehash TEXT,
        tamano INTEGER,
        duracion REAL,
        nombre_archivo TEXT,
        archivo_local TEXT,
        estado TEXT DEFAULT 'pendiente',
        intentos INTEGER DEFAULT 0,
        error_msg TEXT,
        fecha_registro TEXT,
        FOREIGN KEY (msg_id) REFERENCES mensajes (msg_id)
    );

    CREATE INDEX IF NOT EXISTS idx_media_estado ON multimedia (estado);
    """)

    # 4. Tabla virtual FTS5 para búsqueda de texto completo
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mensajes_fts'")
    fts_exists = cur.fetchone()

    if not fts_exists:
        cur.executescript("""
        CREATE VIRTUAL TABLE mensajes_fts USING fts5(
            msg_id UNINDEXED,
            chat_id UNINDEXED,
            contenido,
            remitente_nombre,
            tokenize = 'unicode61 remove_diacritics 1'
        );
        """)
        # Poblar mensajes_fts con los mensajes existentes
        print("Indexando mensajes existentes en FTS5 (búsqueda rápida)...")
        cur.execute("""
            INSERT INTO mensajes_fts (msg_id, chat_id, contenido, remitente_nombre)
            SELECT 
                m.msg_id, 
                m.chat_id, 
                COALESCE(m.contenido, ''),
                COALESCE(c.nombre, c.push_name, m.remitente, '')
            FROM mensajes m
            LEFT JOIN contactos c ON m.remitente = c.contact_id
            WHERE m.contenido IS NOT NULL AND length(m.contenido) > 0;
        """)

    # Triggers para mantener actualizado el índice FTS5 automáticamente
    cur.execute("DROP TRIGGER IF EXISTS trg_mensajes_au")
    cur.executescript("""
    CREATE TRIGGER IF NOT EXISTS trg_mensajes_ai AFTER INSERT ON mensajes 
    WHEN new.contenido IS NOT NULL AND length(new.contenido) > 0
    BEGIN
        INSERT INTO mensajes_fts(msg_id, chat_id, contenido, remitente_nombre)
        VALUES (
            new.msg_id, 
            new.chat_id, 
            new.contenido,
            (SELECT COALESCE(nombre, push_name, new.remitente, '') FROM contactos WHERE contact_id = new.remitente)
        );
    END;

    CREATE TRIGGER IF NOT EXISTS trg_mensajes_ad AFTER DELETE ON mensajes BEGIN
        DELETE FROM mensajes_fts WHERE msg_id = old.msg_id;
    END;

    CREATE TRIGGER IF NOT EXISTS trg_mensajes_au AFTER UPDATE OF contenido, remitente ON mensajes 
    WHEN (old.contenido IS NOT new.contenido OR old.remitente IS NOT new.remitente)
    BEGIN
        DELETE FROM mensajes_fts WHERE msg_id = old.msg_id;
        INSERT INTO mensajes_fts(msg_id, chat_id, contenido, remitente_nombre)
        VALUES (
            new.msg_id, 
            new.chat_id, 
            COALESCE(new.contenido, ''),
            (SELECT COALESCE(nombre, push_name, new.remitente, '') FROM contactos WHERE contact_id = new.remitente)
        );
    END;
    """)

    # 5. Limpiar placeholders 'chat' vacíos (sin texto descifrado ni multimedia) para permitir su re-sincronización limpia
    cur.execute("""
        DELETE FROM mensajes
        WHERE tipo = 'chat'
          AND (contenido IS NULL OR contenido = '')
          AND (quoted_body IS NULL OR quoted_body = '')
          AND archivo_local IS NULL
    """)

    # 6. Vincular números de teléfono reales en contactos @lid
    resolve_lid_phone_numbers(cur)

    # 7. Inicializar tablas satélite de Copiloto y CRM
    init_workspace_tables(conn)

    conn.commit()
    conn.close()

def resolve_canonical_chat_id(cur_or_conn, chat_id):
    """
    Resuelve el identificador canónico unificado de un contacto o conversación.
    Si el contacto tiene número telefónico asociado (ej: 549291...), su ID canónico
    será siempre '<numero>@c.us', registrando y resolviendo sus variantes @lid y @s.whatsapp.net.
    """
    if not chat_id:
        return ""
    chat_str = str(chat_id).strip()
    if chat_str.endswith("@g.us") or chat_str.endswith("@newsletter") or chat_str == "status@broadcast":
        return chat_str

    cur = cur_or_conn.cursor() if hasattr(cur_or_conn, "cursor") else cur_or_conn

    # 1. Comprobar si ya existe en contact_aliases
    try:
        cur.execute("SELECT canonical_id FROM contact_aliases WHERE alias_id = ?", (chat_str,))
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    now_ts = int(time.time())
    canonical = None

    # 2. Si termina en @s.whatsapp.net, el canonical estándar es @c.us
    if chat_str.endswith("@s.whatsapp.net"):
        num = chat_str.split("@")[0]
        canonical = f"{num}@c.us"
    elif chat_str.endswith("@lid"):
        # Buscar en contactos si este @lid tiene un número telefónico vinculado
        try:
            cur.execute("SELECT numero FROM contactos WHERE contact_id = ? AND numero IS NOT NULL AND numero != '' LIMIT 1", (chat_str,))
            crow = cur.fetchone()
            if crow and crow[0]:
                canonical = f"{crow[0]}@c.us"
        except Exception:
            pass
    elif chat_str.endswith("@c.us"):
        canonical = chat_str

    if canonical:
        if canonical != chat_str:
            try:
                cur.execute("""
                    INSERT INTO contact_aliases (alias_id, canonical_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(alias_id) DO UPDATE SET
                        canonical_id = excluded.canonical_id,
                        updated_at = excluded.updated_at
                """, (chat_str, canonical, now_ts))
            except Exception:
                pass
        return canonical

    return chat_str

def consolidate_contact_aliases(conn):
    """
    Identifica contactos @lid con número real vinculado, registra alias canónicos
    y fusiona registros duplicados en crm_contact_meta y crm_chat_tags para evitar tarjetas duplicadas en Kanban.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT contact_id, numero 
        FROM contactos 
        WHERE contact_id LIKE '%@lid' 
          AND numero IS NOT NULL AND numero != ''
    """)
    rows = cur.fetchall()
    now_ts = int(time.time())

    for r in rows:
        lid_id = r[0]
        num = r[1]
        canonical_id = f"{num}@c.us"
        if lid_id == canonical_id:
            continue

        # Registrar alias
        cur.execute("""
            INSERT INTO contact_aliases (alias_id, canonical_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(alias_id) DO UPDATE SET
                canonical_id = excluded.canonical_id,
                updated_at = excluded.updated_at
        """, (lid_id, canonical_id, now_ts))

        cur.execute("""
            INSERT OR IGNORE INTO contact_aliases (alias_id, canonical_id, updated_at)
            VALUES (?, ?, ?)
        """, (f"{num}@s.whatsapp.net", canonical_id, now_ts))

        # Consolidar crm_contact_meta
        cur.execute("SELECT * FROM crm_contact_meta WHERE chat_id = ?", (lid_id,))
        lid_meta = cur.fetchone()
        cur.execute("SELECT * FROM crm_contact_meta WHERE chat_id = ?", (canonical_id,))
        canon_meta = cur.fetchone()

        if lid_meta and not canon_meta:
            cur.execute("UPDATE crm_contact_meta SET chat_id = ? WHERE chat_id = ?", (canonical_id, lid_id))
        elif lid_meta and canon_meta:
            canon_notes = canon_meta["notes"] or ""
            lid_notes = lid_meta["notes"] or ""
            if lid_notes and lid_notes not in canon_notes:
                merged_notes = f"{canon_notes}\n{lid_notes}".strip() if canon_notes else lid_notes
            else:
                merged_notes = canon_notes

            merged_ticket = canon_meta["ticket_status"] if canon_meta["ticket_status"] != "none" else lid_meta["ticket_status"]
            merged_priority = max(canon_meta["support_priority"] or 0, lid_meta["support_priority"] or 0)
            merged_stage = canon_meta["sales_stage"] if canon_meta["sales_stage"] != "none" else lid_meta["sales_stage"]
            merged_val = max(float(canon_meta["estimated_value"] or 0.0), float(lid_meta["estimated_value"] or 0.0))
            merged_follow = canon_meta["follow_up_date"] or lid_meta["follow_up_date"]
            merged_name = canon_meta["custom_name"] or lid_meta["custom_name"]
            merged_updated = max(canon_meta["updated_at"] or 0, lid_meta["updated_at"] or 0, now_ts)

            cur.execute("""
                UPDATE crm_contact_meta
                SET custom_name = ?, ticket_status = ?, support_priority = ?,
                    sales_stage = ?, estimated_value = ?, follow_up_date = ?,
                    notes = ?, updated_at = ?
                WHERE chat_id = ?
            """, (
                merged_name, merged_ticket, merged_priority,
                merged_stage, merged_val, merged_follow,
                merged_notes, merged_updated, canonical_id
            ))
            cur.execute("DELETE FROM crm_contact_meta WHERE chat_id = ?", (lid_id,))

        # Consolidar crm_chat_tags y bitácora crm_contact_notes_log
        cur.execute("DELETE FROM contact_aliases WHERE alias_id = canonical_id")
        cur.execute("SELECT tag, category FROM crm_chat_tags WHERE chat_id = ?", (lid_id,))
        lid_tags = cur.fetchall()
        for t in lid_tags:
            cur.execute("""
                INSERT OR IGNORE INTO crm_chat_tags (chat_id, tag, category)
                VALUES (?, ?, ?)
            """, (canonical_id, t[0], t[1]))
        cur.execute("DELETE FROM crm_chat_tags WHERE chat_id = ?", (lid_id,))
        cur.execute("UPDATE crm_contact_notes_log SET chat_id = ? WHERE chat_id = ?", (canonical_id, lid_id))

def init_workspace_tables(conn):
    """Inicializa tablas satélite no destructivas para Copiloto y CRM Local."""
    cur = conn.cursor()
    cur.executescript("""
    -- Tabla de mapeo y unificación canónica de identidades (@lid <-> @c.us)
    CREATE TABLE IF NOT EXISTS contact_aliases (
        alias_id TEXT PRIMARY KEY,
        canonical_id TEXT NOT NULL,
        updated_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_contact_aliases_canonical ON contact_aliases (canonical_id);

    -- Metadatos unificados de contacto para Soporte y Ventas
    CREATE TABLE IF NOT EXISTS crm_contact_meta (
        chat_id TEXT PRIMARY KEY,
        custom_name TEXT,
        -- Campos de Soporte
        ticket_status TEXT DEFAULT 'none', -- 'none', 'open', 'pending', 'resolved'
        support_priority INTEGER DEFAULT 0, -- 0: Normal, 1: Alta, 2: Urgente
        -- Campos de Ventas
        sales_stage TEXT DEFAULT 'none',   -- 'none', 'lead', 'quoted', 'won', 'lost'
        estimated_value REAL DEFAULT 0.0,
        follow_up_date TEXT,               -- YYYY-MM-DD
        -- Notas compartidas y asignación colaborativa
        notes TEXT DEFAULT '',
        assigned_to TEXT DEFAULT '',
        updated_by TEXT DEFAULT '',
        updated_at INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_crm_contact_ticket ON crm_contact_meta (ticket_status);
    CREATE INDEX IF NOT EXISTS idx_crm_contact_stage ON crm_contact_meta (sales_stage);
    CREATE INDEX IF NOT EXISTS idx_crm_contact_followup ON crm_contact_meta (follow_up_date);

    -- Bitácora colaborativa de notas de seguimiento por chat
    CREATE TABLE IF NOT EXISTS crm_contact_notes_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        author TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notes_log_chat ON crm_contact_notes_log (chat_id, created_at DESC);

    -- Etiquetas personalizadas por chat
    CREATE TABLE IF NOT EXISTS crm_chat_tags (
        chat_id TEXT,
        tag TEXT,
        category TEXT DEFAULT 'general', -- 'support' o 'sales'
        PRIMARY KEY (chat_id, tag)
    );
    CREATE INDEX IF NOT EXISTS idx_crm_tags_chat ON crm_chat_tags (chat_id);

    -- Plantillas de respuesta rápida (Portapapeles)
    CREATE TABLE IF NOT EXISTS crm_quick_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_type TEXT DEFAULT 'both', -- 'support', 'sales', 'both'
        shortcut TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_crm_templates_profile ON crm_quick_templates (profile_type);
    """)

    # Sembrar plantillas por defecto si la tabla está vacía
    cur.execute("SELECT COUNT(*) FROM crm_quick_templates")
    if cur.fetchone()[0] == 0:
        default_templates = [
            ("both", "/saludo", "Saludo inicial", "¡Hola {nombre}! {saludo}, ¿en qué puedo ayudarte hoy?"),
            ("sales", "/precio", "Envío de presupuesto", "Hola {nombre}, te paso el detalle del presupuesto solicitado. Cualquier duda avisame."),
            ("support", "/resolucion", "Ticket resuelto", "Hola {nombre}, te informo que el inconveniente reportado ya ha sido resuelto. ¡Muchas gracias por tu paciencia!")
        ]
        cur.executemany(
            "INSERT INTO crm_quick_templates (profile_type, shortcut, title, body) VALUES (?, ?, ?, ?)",
            default_templates
        )

    # Migración idempotente para columnas de colaboración en crm_contact_meta
    cur.execute("PRAGMA table_info(crm_contact_meta)")
    existing_meta_cols = [row["name"] for row in cur.fetchall()]
    if "assigned_to" not in existing_meta_cols:
        cur.execute("ALTER TABLE crm_contact_meta ADD COLUMN assigned_to TEXT DEFAULT ''")
    if "updated_by" not in existing_meta_cols:
        cur.execute("ALTER TABLE crm_contact_meta ADD COLUMN updated_by TEXT DEFAULT ''")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crm_contact_assigned ON crm_contact_meta (assigned_to)")

    # Consolidar alias canónicos y fusionar duplicados
    consolidate_contact_aliases(conn)

def resolve_lid_phone_numbers(cur):
    """Limpia números ficticios (LID) en contactos @lid y vincula su número real desde contactos @s.whatsapp.net / @c.us."""
    cur.execute("""
        UPDATE contactos
        SET numero = SUBSTR(contact_id, 1, INSTR(contact_id, '@') - 1)
        WHERE (contact_id LIKE '%@s.whatsapp.net' OR contact_id LIKE '%@c.us')
          AND contact_id != '0@c.us'
          AND (numero IS NULL OR numero = '')
    """)
    cur.execute("""
        UPDATE contactos
        SET numero = NULL
        WHERE contact_id LIKE '%@lid'
          AND numero = REPLACE(contact_id, '@lid', '')
    """)
    cur.execute("""
        UPDATE contactos
        SET numero = COALESCE(
            (
                SELECT s.numero FROM contactos s
                WHERE (s.contact_id LIKE '%@s.whatsapp.net' OR s.contact_id LIKE '%@c.us')
                  AND s.numero IS NOT NULL AND s.numero != ''
                  AND contactos.nombre IS NOT NULL AND contactos.nombre != ''
                  AND s.nombre = contactos.nombre
                  AND s.push_name = contactos.push_name
                LIMIT 1
            ),
            (
                SELECT s.numero FROM contactos s
                WHERE (s.contact_id LIKE '%@s.whatsapp.net' OR s.contact_id LIKE '%@c.us')
                  AND s.numero IS NOT NULL AND s.numero != ''
                  AND contactos.nombre IS NOT NULL AND contactos.nombre != ''
                  AND s.nombre = contactos.nombre
                LIMIT 1
            ),
            (
                SELECT s.numero FROM contactos s
                WHERE (s.contact_id LIKE '%@s.whatsapp.net' OR s.contact_id LIKE '%@c.us')
                  AND s.numero IS NOT NULL AND s.numero != ''
                  AND (contactos.nombre IS NULL OR contactos.nombre = '')
                  AND contactos.push_name IS NOT NULL AND contactos.push_name != ''
                  AND (s.push_name = contactos.push_name OR s.nombre = contactos.push_name)
                LIMIT 1
            )
        )
        WHERE contact_id LIKE '%@lid' AND (numero IS NULL OR numero = '')
    """)

def format_phone_number(raw):
    """Formatea un número telefónico en formato internacional legible (+54 9 291 ...)."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw).split("@")[0])
    if not digits:
        return ""
    if re.match(r"^549\d{10}$", digits):
        return f"+54 9 {digits[3:6]} {digits[6:9]}-{digits[9:]}"
    if re.match(r"^54\d{10}$", digits):
        return f"+54 {digits[2:5]} {digits[5:8]}-{digits[8:]}"
    if re.match(r"^1\d{10}$", digits):
        return f"+1 {digits[1:4]} {digits[4:7]}-{digits[7:]}"
    if len(digits) > 4:
        return f"+{digits[:2]} {digits[2:]}"
    return f"+{digits}"

def get_contacts_for_export(db_path=DB_FILE):
    """Obtiene la lista consolidada y sin duplicados de contactos con nombre y número de teléfono."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    resolve_lid_phone_numbers(cur)
    conn.commit()

    cur.execute("""
        SELECT contact_id, nombre, push_name, numero
        FROM contactos
        WHERE contact_id IS NOT NULL
          AND contact_id != '0@c.us'
          AND contact_id NOT LIKE '%@g.us'
          AND contact_id NOT LIKE '%@broadcast'
          AND contact_id NOT LIKE '%@newsletter'
    """)
    rows = cur.fetchall()
    conn.close()

    by_phone = {}
    for r in rows:
        cid = (r["contact_id"] or "").strip()
        num = (r["numero"] or "").strip()
        if cid.endswith("@s.whatsapp.net") or cid.endswith("@c.us"):
            phone = cid.split("@")[0].strip()
        elif cid.endswith("@lid"):
            lid_part = cid.split("@")[0].strip()
            phone = num if (num and num != lid_part) else ""
        else:
            phone = num

        phone_digits = re.sub(r"\D", "", phone)
        if not phone_digits or len(phone_digits) < 7:
            continue

        nombre = (r["nombre"] or "").strip()
        push_name = (r["push_name"] or "").strip()

        if phone_digits not in by_phone:
            by_phone[phone_digits] = {
                "nombre": nombre,
                "push_name": push_name,
                "phone": phone_digits
            }
        else:
            entry = by_phone[phone_digits]
            if nombre and (not entry["nombre"] or entry["nombre"].startswith("+")):
                entry["nombre"] = nombre
            if push_name and not entry["push_name"]:
                entry["push_name"] = push_name

    result = []
    for phone_digits, info in by_phone.items():
        nombre = info["nombre"]
        push_name = info["push_name"]
        # Si el nombre agendado es solo el número con '+' pero existe push_name real, priorizar push_name
        if nombre and nombre.startswith("+") and push_name and not push_name.startswith("+"):
            display_name = push_name
        else:
            display_name = nombre or push_name or "Sin agendar"

        has_real_name = bool((nombre and not nombre.startswith("+")) or (push_name and not push_name.startswith("+")))
        result.append({
            "nombre": display_name,
            "telefono": format_phone_number(phone_digits),
            "numero": phone_digits,
            "_has_name": has_real_name
        })

    result.sort(key=lambda x: (0 if x["_has_name"] else 1, x["nombre"].lower(), x["numero"]))
    return result

def extract_id(val):
    if isinstance(val, dict):
        return val.get("_serialized") or val.get("id") or str(val)
    return str(val) if val is not None else None

def get_sync_status(db_path=DB_FILE, include_ids=False):
    """Devuelve el estado de sincronización actual y el último timestamp registrado."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM mensajes")
    total_messages = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM chats")
    total_chats = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM contactos")
    total_contacts = cur.fetchone()[0]

    cur.execute("""
        SELECT MAX(timestamp) FROM mensajes
        WHERE NOT (tipo = 'chat' AND (contenido IS NULL OR contenido = '') AND (quoted_body IS NULL OR quoted_body = ''))
    """)
    max_ts = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM multimedia WHERE estado = 'pendiente' AND intentos < 3")
    pending_media = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM multimedia WHERE estado = 'descargado'")
    downloaded_media = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM multimedia WHERE estado = 'error' OR (estado = 'pendiente' AND intentos >= 3)")
    failed_media = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM multimedia WHERE estado = 'expirado'")
    expired_media = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM mensajes WHERE contenido IS NOT NULL AND contenido != ''")
    valid_messages = cur.fetchone()[0]

    result = {
        "total_messages": total_messages,
        "valid_messages": valid_messages,
        "total_chats": total_chats,
        "total_contacts": total_contacts,
        "last_timestamp": max_ts,
        "pending_media": pending_media,
        "downloaded_media": downloaded_media,
        "failed_media": failed_media,
        "expired_media": expired_media
    }

    if include_ids:
        cur.execute("""
            SELECT msg_id FROM mensajes
            WHERE msg_id IS NOT NULL
              AND reenviado IS NOT NULL
              AND NOT (tipo = 'chat' AND (contenido IS NULL OR contenido = ''))
        """)
        result["synced_msg_ids"] = [r[0] for r in cur.fetchall()]

    conn.close()
    return result

def extract_base64_jpeg(text):
    if not text or not isinstance(text, str):
        return None
    if "/9j/" in text:
        idx = text.find("/9j/")
        candidate = text[idx:].strip()
        m = re.match(r'^[A-Za-z0-9+/=]+', candidate)
        if m and len(m.group(0)) > 50:
            return f"data:image/jpeg;base64,{m.group(0)}"
    elif text.startswith("data:image"):
        return text.strip()
    return None

def clean_protobuf_text(text):
    """Normaliza saltos de línea suaves y limpia caracteres de control no imprimibles sin alterar texto válido."""
    if not text or not isinstance(text, str):
        return ""
    t = text.replace("\ufffd", "")
    t = re.sub(r'[\x0b\x0c]', '\n', t)
    t = re.sub(r'[\x00-\x08\x0e-\x1f\x7f]', '', t)
    return t.strip()

def build_contact_map(cur):
    """Construye un diccionario {id_o_numero: nombre} a partir de la tabla contactos."""
    cur.execute("SELECT contact_id, numero, COALESCE(nombre, push_name) AS display FROM contactos WHERE COALESCE(nombre, push_name) IS NOT NULL")
    cmap = {}
    for row in cur.fetchall():
        disp = row["display"]
        if not disp:
            continue
        cid = row["contact_id"] or ""
        num = row["numero"] or (cid.split("@")[0] if "@" in cid else "")
        if cid:
            cmap[cid] = disp
        if num:
            cmap[num] = disp
    return cmap

def resolve_mentions_in_text(text, contact_map):
    """Reemplaza menciones @123456789 por @Nombre Contacto si existe en el mapa de contactos."""
    if not text or "@" not in text or not contact_map:
        return text
    def _repl(match):
        num = match.group(1)
        name = contact_map.get(num) or contact_map.get(f"{num}@lid") or contact_map.get(f"{num}@c.us") or contact_map.get(f"{num}@s.whatsapp.net")
        return f"@{name}" if name else match.group(0)
    return re.sub(r'@(\d{8,20})\b', _repl, text)

def is_binary_or_base64_content(tipo, text):
    if not text or not isinstance(text, str):
        return True
    t = text.strip()
    if not t:
        return True

    # 1. Miniatura o imagen en base64 (JPEG /9j/, PNG iVBOR, WebP UklGR, data URI)
    if (
        t.startswith("data:image") or
        t.startswith("/9j/") or
        t.startswith("iVBORw0KGgo") or
        t.startswith("UklGR") or
        extract_base64_jpeg(t) is not None
    ):
        return True

    # 2. Identificadores JID de WhatsApp filtrados como texto (ej. "258741781934082@lid")
    if re.match(r'^[0-9a-zA-Z._+\-]+@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast|newsletter)$', t, re.IGNORECASE):
        return True
    if any(domain in t for domain in ("@s.whatsapp.net", "@lid", "@g.us", "@c.us")) and not re.search(r'\s', t):
        return True

    # 3. Blobs Base64 puros de una sola línea (preserva números largos como CBU/códigos de barras, UUIDs y URLs)
    if (
        len(t) > 80 and
        not re.search(r'\s', t) and
        not t.isdigit() and
        not re.match(r'^https?://', t, re.IGNORECASE) and
        re.match(r'^[A-Za-z0-9+/=]+$', t)
    ):
        return True

    # 4. Audios, notas de voz y stickers nunca llevan cuerpo textual de usuario
    if tipo in ("audio", "ptt", "sticker"):
        return True

    # 5. Ruido binario crudo decodificado accidentalmente (controles o alta densidad de caracteres fuera de bloques de escritura/símbolos/emojis)
    weird_chars = 0
    for ch in t:
        code = ord(ch)
        if not (
            (32 <= code <= 126) or
            code in (9, 10, 13) or
            (0x00A0 <= code <= 0x024F) or  # Suplemento Latino, acentos extendidos, °, ¿, ¡, €
            (0x2000 <= code <= 0x2BFF) or  # Puntuación tipográfica, monedas, flechas, formas, estrellas ⭐, relojes ⏰, keycaps
            (0x3000 <= code <= 0x303F) or
            (0xFE00 <= code <= 0xFE0F) or  # Variation selectors
            (0x1F000 <= code <= 0x1FFFF)   # Todos los bloques modernos de Emojis Unicode
        ):
            weird_chars += 1

    if len(t) >= 8 and (weird_chars / len(t)) > 0.35:
        return True

    return False

def save_incremental_sync(data, db_path=DB_FILE):
    """Guarda lotes de contactos, chats y mensajes de forma incremental."""
    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        stats = _save_incremental_sync_tx(cur, data)

    if stats.get("messages_saved", 0) > 0 or stats.get("contacts_saved", 0) > 0:
        try:
            config.invalidate_accounts_cache()
        except Exception:
            pass
    return stats

def _save_incremental_sync_tx(cur, data):
    # Guardar metadatos de cuenta en la BD si se reciben
    account_id = data.get("account_id")
    account_name = data.get("account_name")
    account_phone = data.get("account_phone")
    if account_id:
        cur.execute("""
            INSERT INTO cuenta (account_id, nombre, telefono, actualizado_en)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(account_id) DO UPDATE SET
                nombre = COALESCE(excluded.nombre, cuenta.nombre),
                telefono = COALESCE(excluded.telefono, cuenta.telefono),
                actualizado_en = excluded.actualizado_en
        """, (str(account_id), account_name, account_phone))

    stats = {
        "contacts_saved": 0,
        "chats_saved": 0,
        "messages_saved": 0,
        "media_enqueued": 0
    }

    # 1. Contactos
    contacts = data.get("contacts", [])
    for c in contacts:
        cid = extract_id(c.get("id"))
        if not cid:
            continue
        nombre = c.get("name") or c.get("formattedName")
        push_name = c.get("pushname")
        raw_num = extract_id(c.get("number") or c.get("phoneNumber"))
        if raw_num and "@" in raw_num:
            raw_num = raw_num.split("@")[0]
        if not raw_num and (cid.endswith("@s.whatsapp.net") or cid.endswith("@c.us")):
            raw_num = cid.split("@")[0]
        if cid.endswith("@lid") and raw_num == cid.split("@")[0]:
            raw_num = None
        numero = raw_num
        thumb_obj = c.get("profilePicThumbObj")
        eurl = thumb_obj.get("eurl") if isinstance(thumb_obj, dict) else None
        foto = c.get("avatar") or eurl

        cur.execute("""
            INSERT INTO contactos (contact_id, nombre, push_name, numero, foto)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET
                nombre = COALESCE(excluded.nombre, contactos.nombre),
                push_name = COALESCE(excluded.push_name, contactos.push_name),
                numero = COALESCE(excluded.numero, contactos.numero),
                foto = COALESCE(excluded.foto, contactos.foto)
        """, (cid, nombre, push_name, numero, foto))
        stats["contacts_saved"] += 1

        if cid.endswith("@lid") and numero:
            try:
                cur.execute("""
                    INSERT INTO contact_aliases (alias_id, canonical_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(alias_id) DO UPDATE SET
                        canonical_id = excluded.canonical_id,
                        updated_at = excluded.updated_at
                """, (cid, f"{numero}@c.us", int(time.time())))
            except Exception:
                pass

    if contacts:
        resolve_lid_phone_numbers(cur)

    # 2. Chats
    chats = data.get("chats", [])
    for ch in chats:
        chid = extract_id(ch.get("id"))
        if not chid:
            continue
        nombre = ch.get("name") or ch.get("formattedTitle")
        no_leidos = ch.get("unreadCount", 0)
        last_ts = ch.get("t") or ch.get("timestamp")

        cur.execute("""
            INSERT INTO chats (chat_id, nombre, no_leidos, ultimo_timestamp)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                nombre = COALESCE(excluded.nombre, chats.nombre),
                no_leidos = excluded.no_leidos,
                ultimo_timestamp = COALESCE(excluded.ultimo_timestamp, chats.ultimo_timestamp)
        """, (chid, nombre, no_leidos, last_ts))
        stats["chats_saved"] += 1

    # 3. Mensajes
    contact_map = build_contact_map(cur)
    messages = data.get("messages", [])
    for m in messages:
        mid = extract_id(m.get("id"))
        if not mid:
            continue

        m_type = m.get("type", "chat")
        # Ignorar mensajes internos de sistema/plantillas vacías de WhatsApp
        if m_type in ("e2e_notification", "notification_template", "gp2", "protocol", "ciphertext", "biz_content_placeholder", "message_history_notice", "pinned_message", "album"):
            continue

        mid_str = str(mid) if mid else ""
        is_mine = 1 if (
            m.get("fromMe") in (True, "true", "True", 1, "1") or 
            (isinstance(m.get("id"), dict) and m.get("id", {}).get("fromMe") in (True, "true", "True", 1, "1")) or
            mid_str.startswith("true_")
        ) else 0
        raw_to = extract_id(m.get("to"))
        raw_from = extract_id(m.get("from"))
        explicit_chat = extract_id(m.get("chatId"))
        chat_id = explicit_chat or (raw_to if is_mine else raw_from)

        author = extract_id(m.get("author"))
        # En grupos, el remitente real es el author
        if author and author != chat_id:
            sender = author
        elif is_mine:
            sender = raw_to
        else:
            # Si el msg_id tiene formato de grupo: false_group_stanza_author
            parts = mid.split("_") if mid else []
            if len(parts) >= 4 and ("@c.us" in parts[-1] or "@lid" in parts[-1]):
                sender = parts[-1]
            else:
                sender = raw_from

        ts = m.get("timestamp") or m.get("t")
        dt_str = None
        if ts and isinstance(ts, (int, float)):
            try:
                # Guardar en hora local del equipo (ej. Argentina UTC-3) para coincidir con WhatsApp Web
                dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        raw_body = clean_protobuf_text(m.get("body") or "")
        caption = clean_protobuf_text(m.get("caption") or "")

        # Detección y rescate de miniatura JPEG
        extracted_thumb = extract_base64_jpeg(m.get("body") or "") or extract_base64_jpeg(m.get("caption") or "")

        # Determinar texto legítimo sin binarios
        if m_type in ("image", "video", "sticker", "audio", "ptt", "document"):
            if caption and not is_binary_or_base64_content(m_type, caption):
                body = caption
            elif raw_body and not is_binary_or_base64_content(m_type, raw_body):
                body = raw_body
            else:
                body = ""
        else:
            cand = raw_body or caption
            body = "" if is_binary_or_base64_content(m_type, cand) else cand

        if body:
            body = resolve_mentions_in_text(body, contact_map)

        # Citas / respuestas (quotedMsg)
        quoted_msg_id = extract_id(m.get("quotedMsgId") or m.get("quotedStanzaID"))
        quoted_author = extract_id(m.get("quotedParticipant") or m.get("quotedAuthor"))
        if quoted_author and quoted_author in contact_map:
            quoted_author = contact_map[quoted_author]
        quoted_body = clean_protobuf_text(m.get("quotedBody") or "")
        if quoted_body and is_binary_or_base64_content("chat", quoted_body):
            quoted_body = ""
        elif quoted_body:
            quoted_body = resolve_mentions_in_text(quoted_body, contact_map)

        # Estado de mensaje reenviado (0 = normal, 1..4 = Reenviado, >=5 = Reenviado muchas veces)
        raw_fwd_score = m.get("forwardingScore") or 0
        try:
            fwd_score_int = int(raw_fwd_score)
        except Exception:
            fwd_score_int = 0
        is_forwarded = bool(
            m.get("isForwarded") in (True, "true", "True", 1, "1") or
            fwd_score_int > 0
        )
        reenviado = max(1, fwd_score_int) if is_forwarded else 0

        # Metadatos multimedia
        media_key = m.get("mediaKey")
        direct_path = m.get("directPath")
        mimetype = m.get("mimetype")
        size = m.get("size") or m.get("fileLength")
        duration = m.get("duration")

        raw_thumb = m.get("thumbnail")
        if raw_thumb and isinstance(raw_thumb, str) and not raw_thumb.startswith("data:image"):
            raw_thumb = f"data:image/jpeg;base64,{raw_thumb}"
        thumbnail = raw_thumb or extracted_thumb
        filename = m.get("filename")

        has_media = bool(media_key and direct_path)
        estado_media = "pendiente" if has_media else "none"

        # No guardar placeholders de texto vacíos aún no descifrados para no contaminar la vista previa ni bloquear su re-sincronización
        if m_type in ("chat", "album", "unknown") and not body and not quoted_body and not has_media:
            continue

        # Completar texto citado desde la BD local si se recibió quoted_msg_id sin quoted_body
        if quoted_msg_id and not quoted_body:
            cur.execute("SELECT contenido FROM mensajes WHERE msg_id LIKE ? LIMIT 1", (f"%{quoted_msg_id}%",))
            q_row = cur.fetchone()
            if q_row and q_row[0]:
                quoted_body = q_row[0]

        # Asegurar que el chat padre exista en la tabla chats y actualizar campos desnormalizados
        if chat_id:
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio, ultimo_tipo)
                VALUES (?, NULL, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    nombre = COALESCE(chats.nombre, excluded.nombre),
                    ultimo_timestamp = CASE
                        WHEN excluded.ultimo_timestamp IS NOT NULL AND (chats.ultimo_timestamp IS NULL OR excluded.ultimo_timestamp >= chats.ultimo_timestamp)
                        THEN excluded.ultimo_timestamp
                        ELSE chats.ultimo_timestamp
                    END,
                    ultimo_mensaje = CASE
                        WHEN excluded.ultimo_timestamp IS NOT NULL AND (chats.ultimo_timestamp IS NULL OR excluded.ultimo_timestamp >= chats.ultimo_timestamp)
                        THEN excluded.ultimo_mensaje
                        ELSE chats.ultimo_mensaje
                    END,
                    ultimo_es_mio = CASE
                        WHEN excluded.ultimo_timestamp IS NOT NULL AND (chats.ultimo_timestamp IS NULL OR excluded.ultimo_timestamp >= chats.ultimo_timestamp)
                        THEN excluded.ultimo_es_mio
                        ELSE chats.ultimo_es_mio
                    END,
                    ultimo_tipo = CASE
                        WHEN excluded.ultimo_timestamp IS NOT NULL AND (chats.ultimo_timestamp IS NULL OR excluded.ultimo_timestamp >= chats.ultimo_timestamp)
                        THEN excluded.ultimo_tipo
                        ELSE chats.ultimo_tipo
                    END
            """, (
                chat_id, 
                ts if m_type != "call_log" else None,
                body or (f"[{m_type}]" if m_type != "chat" else ""),
                is_mine,
                m_type
            ))

        # Inserción de mensaje
        cur.execute("""
            INSERT INTO mensajes (
                msg_id, chat_id, remitente, es_mio, fecha_utc, timestamp, 
                tipo, contenido, mimetype, tamano, duracion, thumbnail, estado_multimedia,
                quoted_msg_id, quoted_author, quoted_body, reenviado
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(msg_id) DO UPDATE SET
                es_mio = excluded.es_mio,
                contenido = CASE 
                    WHEN excluded.contenido != '' 
                    THEN excluded.contenido 
                    ELSE mensajes.contenido 
                END,
                tipo = excluded.tipo,
                mimetype = COALESCE(excluded.mimetype, mensajes.mimetype),
                tamano = COALESCE(excluded.tamano, mensajes.tamano),
                duracion = COALESCE(excluded.duracion, mensajes.duracion),
                thumbnail = COALESCE(excluded.thumbnail, mensajes.thumbnail),
                quoted_msg_id = COALESCE(excluded.quoted_msg_id, mensajes.quoted_msg_id),
                quoted_author = COALESCE(excluded.quoted_author, mensajes.quoted_author),
                quoted_body = COALESCE(NULLIF(excluded.quoted_body, ''), mensajes.quoted_body),
                reenviado = CASE WHEN excluded.reenviado > 0 THEN excluded.reenviado ELSE COALESCE(mensajes.reenviado, excluded.reenviado) END,
                estado_multimedia = CASE 
                    WHEN mensajes.archivo_local IS NOT NULL THEN mensajes.estado_multimedia 
                    ELSE excluded.estado_multimedia 
                END
        """, (
            mid, chat_id, sender, is_mine, dt_str, ts, 
            m_type, body, mimetype, size, duration, thumbnail, estado_media,
            quoted_msg_id, quoted_author, quoted_body, reenviado
        ))
        stats["messages_saved"] += 1

        # Encolar para descarga multimedia si contiene mediaKey y directPath
        if has_media:
            cur.execute("""
                INSERT OR IGNORE INTO multimedia (
                    msg_id, chat_id, tipo, mimetype, media_key, direct_path, 
                    enc_filehash, filehash, tamano, duracion, nombre_archivo, 
                    estado, fecha_registro
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)
            """, (
                mid, chat_id, m_type, mimetype, media_key, direct_path,
                m.get("encFilehash"), m.get("filehash"), size, duration, filename,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            ))
            if cur.rowcount > 0:
                stats["media_enqueued"] += 1

    return stats

def retry_failed_media(db_path=DB_FILE):
    """Restablece el contador de intentos de descargas fallidas (no expiradas) para reintentar."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        UPDATE multimedia
        SET estado = 'pendiente', intentos = 0, error_msg = NULL
        WHERE estado = 'error' OR (estado = 'pendiente' AND intentos >= 3)
    """)
    reset_count = cur.rowcount
    cur.execute("""
        UPDATE mensajes
        SET estado_multimedia = 'pendiente'
        WHERE msg_id IN (SELECT msg_id FROM multimedia WHERE estado = 'pendiente')
          AND archivo_local IS NULL
    """)
    conn.commit()
    conn.close()
    return reset_count

def search_full_text(query_str, chat_id=None, limit=50, db_path=DB_FILE):
    """Búsqueda de texto completo ultrarrápida usando SQLite FTS5."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    # Sanitizar consulta para FTS5
    clean_query = "".join(ch for ch in query_str if ch.isalnum() or ch.isspace()).strip()
    if not clean_query:
        conn.close()
        return []

    fts_match = " ".join([f'"{token}"*' for token in clean_query.split()])

    target_ids = []
    if chat_id:
        if isinstance(chat_id, (list, tuple, set)):
            target_ids = [str(x).strip() for x in chat_id if str(x).strip()]
        elif isinstance(chat_id, str):
            target_ids = [x.strip() for x in chat_id.split(",") if x.strip()]

    if target_ids:
        # Resolver identidades canónicas, aliases y nombres de contactos a sus JIDs
        resolved_set = set()
        for tid in target_ids:
            resolved_set.add(tid)
            # Si no es un JID (@c.us, @lid, @g.us, etc.), buscar por nombre o teléfono
            if "@" not in tid:
                try:
                    cur.execute("SELECT chat_id FROM chats WHERE nombre = ? OR chat_id = ?", (tid, tid))
                    for r in cur.fetchall():
                        if r[0]: resolved_set.add(r[0])
                    cur.execute("SELECT contact_id, numero FROM contactos WHERE nombre = ? OR push_name = ? OR contact_id = ?", (tid, tid, tid))
                    for r in cur.fetchall():
                        if r[0]: resolved_set.add(r[0])
                        if r[1]:
                            resolved_set.add(f"{r[1]}@c.us")
                            resolved_set.add(f"{r[1]}@s.whatsapp.net")
                except Exception:
                    pass

        # Expandir bidireccionalmente con contact_aliases
        if resolved_set:
            seed_ids = list(resolved_set)
            seed_ph = ",".join("?" for _ in seed_ids)
            try:
                cur.execute(f"""
                    SELECT alias_id, canonical_id FROM contact_aliases
                    WHERE alias_id IN ({seed_ph}) OR canonical_id IN ({seed_ph})
                """, seed_ids + seed_ids)
                for r in cur.fetchall():
                    if r[0]: resolved_set.add(r[0])
                    if r[1]: resolved_set.add(r[1])
            except Exception:
                pass
            target_ids = list(resolved_set)

    if target_ids:
        placeholders = ",".join("?" for _ in target_ids)
        sql = f"""
        SELECT 
            m.msg_id,
            m.chat_id,
            COALESCE(c.nombre, ct.nombre, ct.push_name, m.chat_id) AS chat_nombre,
            COALESCE(ct.nombre, ct.push_name, m.remitente) AS remitente_nombre,
            m.es_mio,
            m.fecha_utc,
            m.timestamp,
            m.tipo,
            m.contenido,
            m.archivo_local,
            snippet(mensajes_fts, 2, char(1), char(2), '...', 25) AS snippet
        FROM mensajes_fts fts
        JOIN mensajes m ON fts.msg_id = m.msg_id
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        LEFT JOIN contactos ct ON m.remitente = ct.contact_id
        WHERE mensajes_fts MATCH ? AND m.chat_id IN ({placeholders})
        ORDER BY m.timestamp DESC
        LIMIT ?
        """
        cur.execute(sql, [fts_match] + target_ids + [limit])
    else:
        sql = """
        SELECT 
            m.msg_id,
            m.chat_id,
            COALESCE(c.nombre, ct.nombre, ct.push_name, m.chat_id) AS chat_nombre,
            COALESCE(ct.nombre, ct.push_name, m.remitente) AS remitente_nombre,
            m.es_mio,
            m.fecha_utc,
            m.timestamp,
            m.tipo,
            m.contenido,
            m.archivo_local,
            snippet(mensajes_fts, 2, char(1), char(2), '...', 25) AS snippet
        FROM mensajes_fts fts
        JOIN mensajes m ON fts.msg_id = m.msg_id
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        LEFT JOIN contactos ct ON m.remitente = ct.contact_id
        WHERE mensajes_fts MATCH ?
        ORDER BY m.timestamp DESC
        LIMIT ?
        """
        cur.execute(sql, (fts_match, limit))

    results = [dict(row) for row in cur.fetchall()]
    conn.close()

    # Escapar el texto del mensaje ANTES de insertar los resaltados para evitar XSS:
    # FTS5 devuelve el contenido crudo; los delimitadores char(1)/char(2) se convierten en <mark> tras escapar.
    for r in results:
        snip = r.get("snippet") or ""
        r["snippet"] = html.escape(snip).replace("\x01", "<mark>").replace("\x02", "</mark>")
        r["sender_name"] = r.get("remitente_nombre")
    return results

def create_manual_backup(db_path=DB_FILE, backup_dir=BACKUP_DIR):
    """Crea una copia de seguridad consistente de SQLite usando el API nativo de backup."""
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_filename = f"whatsapp_backup_{ts}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    src_conn = get_connection(db_path)
    dst_conn = sqlite3.connect(backup_path)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    file_size = os.path.getsize(backup_path)
    return {
        "filename": backup_filename,
        "size_bytes": file_size,
        "size_mb": round(file_size / (1024 * 1024), 2),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def list_manual_backups(backup_dir=BACKUP_DIR):
    """Devuelve la lista de copias de seguridad manuales existentes ordenadas por fecha."""
    if not os.path.exists(backup_dir):
        return []
    backups = []
    for f in os.listdir(backup_dir):
        if f.endswith(".db"):
            fp = os.path.join(backup_dir, f)
            sz = os.path.getsize(fp)
            mtime = os.path.getmtime(fp)
            backups.append({
                "filename": f,
                "size_mb": round(sz / (1024 * 1024), 2),
                "created_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups

# =========================================================================
# FUNCIONES DE CONSULTA Y GESTIÓN DE COPILOTO Y CRM LOCAL
# =========================================================================

def get_copilot_context(chat_query, db_path=None):
    """
    Busca de forma ultra-rápida (< 20ms) el contexto de un chat por ID, número o nombre.
    Retorna metadatos CRM, etiquetas, estadísticas de mensajes y notas.
    """
    if not chat_query:
        return None

    conn = get_connection(db_path)
    cur = conn.cursor()

    query_str = str(chat_query).strip()
    digits = re.sub(r'\D', '', query_str)

    found_ids = set()
    contact_name = None
    phone_number = None

    # 1. Buscar en chats por chat_id o nombre exacto
    cur.execute("SELECT chat_id, nombre FROM chats WHERE chat_id = ? OR nombre = ? LIMIT 1", (query_str, query_str))
    row = cur.fetchone()
    if row:
        found_ids.add(row["chat_id"])
        if row["nombre"]:
            contact_name = row["nombre"]

    # 2. Buscar en contactos por contact_id, nombre, push_name o número
    cur.execute("""
        SELECT contact_id, nombre, push_name, numero
        FROM contactos
        WHERE contact_id = ? OR nombre = ? OR push_name = ? OR (numero IS NOT NULL AND numero = ?)
        LIMIT 1
    """, (query_str, query_str, query_str, digits if len(digits) >= 7 else "___"))
    crow = cur.fetchone()
    if crow:
        found_ids.add(crow["contact_id"])
        if not contact_name:
            contact_name = crow["nombre"] or crow["push_name"]
        if crow["numero"]:
            phone_number = crow["numero"]
            found_ids.add(f"{crow['numero']}@c.us")
            found_ids.add(f"{crow['numero']}@s.whatsapp.net")

    # 3. Si no hubo coincidencia exacta, buscar con LIKE en contactos y chats
    if not found_ids and len(query_str) >= 2:
        cur.execute("""
            SELECT contact_id, nombre, push_name, numero
            FROM contactos
            WHERE nombre LIKE ? OR push_name LIKE ?
            LIMIT 1
        """, (f"%{query_str}%", f"%{query_str}%"))
        crow = cur.fetchone()
        if crow:
            found_ids.add(crow["contact_id"])
            contact_name = crow["nombre"] or crow["push_name"]
            if crow["numero"]:
                phone_number = crow["numero"]
                found_ids.add(f"{crow['numero']}@c.us")
                found_ids.add(f"{crow['numero']}@s.whatsapp.net")
        else:
            cur.execute("SELECT chat_id, nombre FROM chats WHERE nombre LIKE ? LIMIT 1", (f"%{query_str}%",))
            ch_row = cur.fetchone()
            if ch_row:
                found_ids.add(ch_row["chat_id"])
                contact_name = ch_row["nombre"]

    # 4. Manejar dígitos de teléfono directos si los hay
    if digits and len(digits) >= 7 and not phone_number:
        phone_number = digits
        found_ids.add(f"{digits}@c.us")
        found_ids.add(f"{digits}@s.whatsapp.net")

    if not found_ids:
        found_ids.add(query_str)

    # 5. Si conocemos el número de teléfono, vincular cualquier @lid asociado en contactos
    if phone_number:
        cur.execute("SELECT contact_id FROM contactos WHERE numero = ?", (phone_number,))
        for r in cur.fetchall():
            found_ids.add(r["contact_id"])

    # Expandir con contact_aliases bidireccionalmente
    seed_ids = list(found_ids)
    if seed_ids:
        seed_ph = ",".join("?" for _ in seed_ids)
        cur.execute(f"""
            SELECT alias_id, canonical_id FROM contact_aliases
            WHERE alias_id IN ({seed_ph}) OR canonical_id IN ({seed_ph})
        """, seed_ids + seed_ids)
        for r in cur.fetchall():
            if r["alias_id"]:
                found_ids.add(r["alias_id"])
            if r["canonical_id"]:
                found_ids.add(r["canonical_id"])

    id_list = list(found_ids)
    placeholders = ",".join("?" for _ in id_list)

    # Determinar qué ID tiene mayor historial de mensajes y resolver canonical_id
    cur.execute(f"""
        SELECT chat_id, COUNT(*) as cnt
        FROM mensajes
        WHERE chat_id IN ({placeholders})
        GROUP BY chat_id
        ORDER BY cnt DESC
        LIMIT 1
    """, id_list)
    top_row = cur.fetchone()
    if top_row:
        effective_chat_id = top_row["chat_id"]
    elif phone_number:
        effective_chat_id = f"{phone_number}@c.us"
    else:
        effective_chat_id = id_list[0]

    canonical_chat_id = resolve_canonical_chat_id(cur, f"{phone_number}@c.us" if phone_number else effective_chat_id)
    if canonical_chat_id and canonical_chat_id not in found_ids:
        found_ids.add(canonical_chat_id)
        id_list = list(found_ids)
        placeholders = ",".join("?" for _ in id_list)

    # Obtener metadatos CRM buscando en cualquiera de los IDs asociados
    cur.execute(f"""
        SELECT custom_name, ticket_status, support_priority,
               sales_stage, estimated_value, follow_up_date, notes,
               coalesce(assigned_to, '') as assigned_to,
               coalesce(updated_by, '') as updated_by,
               updated_at
        FROM crm_contact_meta
        WHERE chat_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 1
    """, id_list)
    crm_row = cur.fetchone()
    crm_meta = dict(crm_row) if crm_row else {
        "custom_name": None,
        "ticket_status": "none",
        "support_priority": 0,
        "sales_stage": "none",
        "estimated_value": 0.0,
        "follow_up_date": None,
        "notes": "",
        "assigned_to": "",
        "updated_by": "",
        "updated_at": None
    }
    if "assigned_to" not in crm_meta:
        crm_meta["assigned_to"] = ""
    if "updated_by" not in crm_meta:
        crm_meta["updated_by"] = ""

    # Obtener notas históricas de la bitácora colaborativa
    cur.execute(f"""
        SELECT id, chat_id, author, content, created_at
        FROM crm_contact_notes_log
        WHERE chat_id IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        LIMIT 50
    """, id_list)
    notes_log = [dict(r) for r in cur.fetchall()]

    # Obtener etiquetas de cualquiera de los IDs asociados
    cur.execute(f"""
        SELECT DISTINCT tag, category 
        FROM crm_chat_tags 
        WHERE chat_id IN ({placeholders}) 
        ORDER BY tag
    """, id_list)
    tags = [dict(r) for r in cur.fetchall()]

    # Estadísticas globales unificadas de mensajes
    cur.execute(f"""
        SELECT 
            COUNT(*) as total_msgs,
            MIN(timestamp) as min_ts,
            MAX(timestamp) as max_ts
        FROM mensajes
        WHERE chat_id IN ({placeholders})
    """, id_list)
    stats_row = cur.fetchone()

    total_msgs = stats_row["total_msgs"] if stats_row else 0
    min_ts = stats_row["min_ts"] if stats_row and stats_row["min_ts"] else 0
    max_ts = stats_row["max_ts"] if stats_row and stats_row["max_ts"] else 0

    # Último mensaje para preview
    cur.execute(f"""
        SELECT remitente, es_mio, contenido, tipo, timestamp
        FROM mensajes
        WHERE chat_id IN ({placeholders})
        ORDER BY timestamp DESC
        LIMIT 1
    """, id_list)
    last_msg_row = cur.fetchone()
    last_msg = dict(last_msg_row) if last_msg_row else None

    # Cálculo del SLA del contacto
    sla_text = get_chat_sla(id_list, db_path=db_path)

    # Plantillas disponibles
    templates = list_quick_templates(db_path=db_path)

    # Extracción de memoria comercial (precios cotizados y ficha automática de contacto)
    import entity_extractor
    commercial = entity_extractor.extract_chat_commercial_entities(cur, id_list)

    conn.close()

    def _fmt_date(ts):
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
        except Exception:
            return None

    display_name = crm_meta.get("custom_name") or contact_name or query_str

    return {
        "ok": True,
        "chat_id": effective_chat_id,
        "canonical_id": canonical_chat_id,
        "display_name": display_name,
        "contact_name": contact_name,
        "phone_number": phone_number,
        "crm": crm_meta,
        "assigned_to": crm_meta.get("assigned_to", ""),
        "updated_by": crm_meta.get("updated_by", ""),
        "notes_log": notes_log,
        "tags": tags,
        "sla": sla_text,
        "templates": templates,
        "quoted_prices": commercial["quoted_prices"],
        "extracted_info": commercial["extracted_info"],
        "associated_chat_ids": id_list,
        "stats": {
            "total_messages": total_msgs,
            "first_message_date": _fmt_date(min_ts),
            "last_message_date": _fmt_date(max_ts),
            "last_message_preview": (last_msg["contenido"][:60] if last_msg and last_msg["contenido"] else "") if last_msg else None,
            "last_message_from_me": bool(last_msg["es_mio"]) if last_msg else False
        }
    }

def get_chat_sla(chat_ids, db_path=None):
    """Calcula el tiempo promedio histórico de respuesta (SLA) para un contacto."""
    if not chat_ids:
        return "Sin datos"
    conn = get_connection(db_path)
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in chat_ids)
    cur.execute(f"""
        WITH ordered AS (
            SELECT 
                es_mio, 
                timestamp,
                LAG(es_mio) OVER (ORDER BY timestamp ASC) as prev_mio,
                LAG(timestamp) OVER (ORDER BY timestamp ASC) as prev_ts
            FROM mensajes
            WHERE chat_id IN ({placeholders})
            ORDER BY timestamp ASC
        )
        SELECT AVG(timestamp - prev_ts) as avg_response_sec
        FROM ordered
        WHERE es_mio = 1 AND prev_mio = 0 AND (timestamp - prev_ts) BETWEEN 5 AND 86400
    """, list(chat_ids))
    res = cur.fetchone()
    conn.close()
    if not res or not res[0]:
        return "Sin datos suficientes"
    sec = float(res[0])
    if sec < 60:
        return f"~{int(sec)} seg"
    elif sec < 3600:
        return f"~{int(sec // 60)} min"
    else:
        hours = round(sec / 3600, 1)
        return f"~{hours} h"

def get_unanswered_chats(limit=30, db_path=None):
    """
    Radar de chats sin responder: devuelve conversaciones individuales donde el último mensaje
    es del cliente (es_mio = 0) o cuyo ticket_status es 'open', ordenados por tiempo de espera.
    Aprovecha las columnas desnormalizadas en chats para respuesta ultrarrápida sin escaneo de mensajes.
    """
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                ch.chat_id,
                ch.ultimo_es_mio as es_mio,
                ch.ultimo_mensaje as contenido,
                ch.ultimo_timestamp as timestamp,
                coalesce(c.custom_name, ch.nombre, con.nombre, con.push_name, ch.chat_id) as display_name,
                con.numero as phone_number,
                coalesce(c.ticket_status, 'none') as ticket_status,
                coalesce(c.support_priority, 0) as support_priority,
                coalesce(c.assigned_to, '') as assigned_to,
                coalesce(c.updated_by, '') as updated_by
            FROM chats ch
            LEFT JOIN contact_aliases ca ON ch.chat_id = ca.alias_id
            LEFT JOIN crm_contact_meta c ON coalesce(ca.canonical_id, ch.chat_id) = c.chat_id
            LEFT JOIN contactos con ON ch.chat_id = con.contact_id OR coalesce(ca.canonical_id, ch.chat_id) = (con.numero || '@c.us')
            WHERE ch.chat_id IS NOT NULL 
              AND ch.chat_id NOT LIKE '%@g.us'
              AND ch.chat_id NOT LIKE '%@newsletter'
              AND ch.chat_id != 'status@broadcast'
              AND (ch.ultimo_es_mio = 0 OR c.ticket_status = 'open')
              AND ch.ultimo_timestamp IS NOT NULL
            ORDER BY ch.ultimo_timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = cur.fetchall()

    now_ts = int(time.time())
    result = []
    for r in rows:
        diff = max(0, now_ts - (r["timestamp"] or now_ts))
        if diff < 60:
            time_str = "hace un momento"
        elif diff < 3600:
            time_str = f"hace {int(diff // 60)}m"
        elif diff < 86400:
            time_str = f"hace {int(diff // 3600)}h"
        else:
            time_str = f"hace {int(diff // 86400)}d"

        snippet = (r["contenido"] or "").strip().replace("\n", " ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."

        result.append({
            "chat_id": r["chat_id"],
            "display_name": r["display_name"] or r["chat_id"],
            "phone_number": r["phone_number"],
            "last_message": snippet,
            "timestamp": r["timestamp"],
            "waiting_str": time_str,
            "waiting_seconds": diff,
            "ticket_status": r["ticket_status"],
            "support_priority": r["support_priority"],
            "assigned_to": r["assigned_to"] if "assigned_to" in r else "",
            "updated_by": r["updated_by"] if "updated_by" in r else ""
        })
    return result

def get_sales_followups(limit=30, db_path=None):
    """
    Radar comercial de seguimiento de ventas:
    Devuelve chats con presupuestos abiertos (sales_stage = 'quoted'),
    leads con fecha de seguimiento agendada, o presupuestos enviados por nosotros
    hace más de 48h sin confirmación comercial (sales_stage != 'won' y != 'lost').
    Aprovecha las columnas desnormalizadas en chats y contact_aliases.
    """
    now_ts = int(time.time())
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                ch.chat_id,
                ch.ultimo_es_mio as es_mio,
                ch.ultimo_mensaje as contenido,
                ch.ultimo_timestamp as timestamp,
                coalesce(c.custom_name, ch.nombre, con.nombre, con.push_name, ch.chat_id) as display_name,
                con.numero as phone_number,
                coalesce(c.sales_stage, 'none') as sales_stage,
                coalesce(c.estimated_value, 0.0) as estimated_value,
                c.follow_up_date,
                coalesce(c.assigned_to, '') as assigned_to,
                coalesce(c.updated_by, '') as updated_by
            FROM chats ch
            LEFT JOIN contact_aliases ca ON ch.chat_id = ca.alias_id
            LEFT JOIN crm_contact_meta c ON coalesce(ca.canonical_id, ch.chat_id) = c.chat_id
            LEFT JOIN contactos con ON ch.chat_id = con.contact_id OR coalesce(ca.canonical_id, ch.chat_id) = (con.numero || '@c.us')
            WHERE ch.chat_id IS NOT NULL 
              AND ch.chat_id NOT LIKE '%@g.us'
              AND ch.chat_id NOT LIKE '%@newsletter'
              AND ch.chat_id != 'status@broadcast'
              AND ch.ultimo_timestamp IS NOT NULL
              AND (
                  c.sales_stage IN ('lead', 'quoted') 
                  OR (c.follow_up_date IS NOT NULL AND c.follow_up_date != '')
                  OR (ch.ultimo_es_mio = 1 AND (ch.ultimo_mensaje LIKE '%$%' OR ch.ultimo_mensaje LIKE '%USD%' OR ch.ultimo_mensaje LIKE '%presupuesto%') AND (? - ch.ultimo_timestamp) >= 172800 AND coalesce(c.sales_stage, 'none') NOT IN ('won', 'lost'))
              )
            GROUP BY coalesce(ca.canonical_id, ch.chat_id)
            ORDER BY 
                CASE 
                    WHEN c.follow_up_date IS NOT NULL AND c.follow_up_date != '' THEN 1 
                    WHEN c.sales_stage = 'quoted' THEN 2 
                    ELSE 3 
                END ASC,
                ch.ultimo_timestamp DESC
            LIMIT ?
        """, (now_ts, limit))
        rows = cur.fetchall()

    result = []
    for r in rows:
        diff = max(0, now_ts - (r["timestamp"] or now_ts))
        if diff < 60:
            time_str = "hace un momento"
        elif diff < 3600:
            time_str = f"hace {int(diff // 60)}m"
        elif diff < 86400:
            time_str = f"hace {int(diff // 3600)}h"
        else:
            time_str = f"hace {int(diff // 86400)}d"

        snippet = (r["contenido"] or "").strip().replace("\n", " ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."

        result.append({
            "chat_id": r["chat_id"],
            "display_name": r["display_name"] or r["chat_id"],
            "phone_number": r["phone_number"],
            "last_message": snippet,
            "timestamp": r["timestamp"],
            "waiting_str": time_str,
            "waiting_seconds": diff,
            "sales_stage": r["sales_stage"],
            "estimated_value": float(r["estimated_value"] or 0.0),
            "follow_up_date": r["follow_up_date"],
            "assigned_to": r["assigned_to"] if "assigned_to" in r else "",
            "updated_by": r["updated_by"] if "updated_by" in r else ""
        })
    return result

def get_contact_meta(chat_id, db_path=None):
    """Devuelve los metadatos CRM de un chat o un dict con valores por defecto resolviendo su canonical_id."""
    if not chat_id:
        return {}
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            SELECT chat_id, custom_name, ticket_status, support_priority,
                   sales_stage, estimated_value, follow_up_date, notes,
                   coalesce(assigned_to, '') as assigned_to,
                   coalesce(updated_by, '') as updated_by,
                   updated_at
            FROM crm_contact_meta
            WHERE chat_id = ?
        """, (canonical_id,))
        row = cur.fetchone()
        if row:
            d = dict(row)
            if "assigned_to" not in d or d["assigned_to"] is None:
                d["assigned_to"] = ""
            if "updated_by" not in d or d["updated_by"] is None:
                d["updated_by"] = ""
            return d
    return {
        "chat_id": canonical_id,
        "custom_name": None,
        "ticket_status": "none",
        "support_priority": 0,
        "sales_stage": "none",
        "estimated_value": 0.0,
        "follow_up_date": None,
        "notes": "",
        "assigned_to": "",
        "updated_by": "",
        "updated_at": None
    }

def upsert_contact_meta(chat_id, custom_name=None, ticket_status=None, support_priority=None,
                        sales_stage=None, estimated_value=None, follow_up_date=None,
                        notes=None, assigned_to=None, updated_by=None,
                        expected_updated_at=None, db_path=None):
    """
    Actualiza o inserta metadatos CRM de forma 100% atómica bajo una única transacción SQLite.
    Resuelve primero el chat_id a su ID canónico y realiza actualización parcial segura
    sin condición de carrera ('Lost Update').
    Soporta bloqueo optimista sobre 'notes' mediante expected_updated_at.
    """
    if not chat_id:
        return None

    now_ts = int(time.time() * 1000)

    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)

        # Protección concurrente optimista sobre la Nota Fijada
        if notes is not None and expected_updated_at is not None:
            cur.execute("SELECT notes, updated_at, updated_by FROM crm_contact_meta WHERE chat_id = ?", (canonical_id,))
            current_row = cur.fetchone()
            if current_row and current_row["updated_at"] is not None:
                try:
                    curr_ts = float(current_row["updated_at"])
                    exp_ts = float(expected_updated_at)
                    if curr_ts != exp_ts and (current_row["notes"] or "") != notes:
                        return {
                            "conflict": True,
                            "error": "Conflicto de concurrencia: la nota fue modificada por otro usuario.",
                            "current_notes": current_row["notes"] or "",
                            "updated_at": curr_ts,
                            "updated_by": current_row["updated_by"] or ""
                        }
                except (ValueError, TypeError):
                    if str(current_row["updated_at"]) != str(expected_updated_at) and (current_row["notes"] or "") != notes:
                        return {
                            "conflict": True,
                            "error": "Conflicto de concurrencia: la nota fue modificada por otro usuario.",
                            "current_notes": current_row["notes"] or "",
                            "updated_at": current_row["updated_at"],
                            "updated_by": current_row["updated_by"] or ""
                        }

        cur.execute("""
            INSERT INTO crm_contact_meta (
                chat_id, custom_name, ticket_status, support_priority,
                sales_stage, estimated_value, follow_up_date, notes,
                assigned_to, updated_by, updated_at
            ) VALUES (
                ?, ?, 
                COALESCE(?, 'none'), 
                COALESCE(?, 0), 
                COALESCE(?, 'none'), 
                COALESCE(?, 0.0), 
                ?, 
                COALESCE(?, ''), 
                COALESCE(?, ''),
                COALESCE(?, ''),
                ?
            )
            ON CONFLICT(chat_id) DO UPDATE SET
                custom_name = CASE WHEN ? IS NOT NULL THEN excluded.custom_name ELSE crm_contact_meta.custom_name END,
                ticket_status = CASE WHEN ? IS NOT NULL THEN excluded.ticket_status ELSE crm_contact_meta.ticket_status END,
                support_priority = CASE WHEN ? IS NOT NULL THEN excluded.support_priority ELSE crm_contact_meta.support_priority END,
                sales_stage = CASE WHEN ? IS NOT NULL THEN excluded.sales_stage ELSE crm_contact_meta.sales_stage END,
                estimated_value = CASE WHEN ? IS NOT NULL THEN excluded.estimated_value ELSE crm_contact_meta.estimated_value END,
                follow_up_date = CASE WHEN ? IS NOT NULL THEN excluded.follow_up_date ELSE crm_contact_meta.follow_up_date END,
                notes = CASE WHEN ? IS NOT NULL THEN excluded.notes ELSE crm_contact_meta.notes END,
                assigned_to = CASE WHEN ? IS NOT NULL THEN excluded.assigned_to ELSE crm_contact_meta.assigned_to END,
                updated_by = CASE WHEN ? IS NOT NULL THEN excluded.updated_by ELSE crm_contact_meta.updated_by END,
                updated_at = excluded.updated_at
        """, (
            canonical_id, custom_name, ticket_status, support_priority,
            sales_stage, estimated_value, follow_up_date, notes,
            assigned_to, updated_by, now_ts,
            custom_name, ticket_status, support_priority,
            sales_stage, estimated_value, follow_up_date, notes,
            assigned_to, updated_by
        ))

    # Registrar operadores conocidos en la configuración del nodo
    if updated_by:
        try:
            config.update_system_settings(add_operator=updated_by)
        except Exception:
            pass
    if assigned_to:
        try:
            config.update_system_settings(add_operator=assigned_to)
        except Exception:
            pass

    return get_contact_meta(canonical_id, db_path=db_path)

def add_contact_note_log(chat_id, author, content, db_path=None):
    """
    Añade una entrada a la bitácora colaborativa del contacto (crm_contact_notes_log).
    """
    if not chat_id or not content:
        return None
    author = (author or "Operador").strip()
    content = str(content).strip()
    now_ts = int(time.time())

    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            INSERT INTO crm_contact_notes_log (chat_id, author, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (canonical_id, author, content, now_ts))
        note_id = cur.lastrowid

    try:
        config.update_system_settings(add_operator=author)
    except Exception:
        pass

    return {
        "id": note_id,
        "chat_id": canonical_id,
        "author": author,
        "content": content,
        "created_at": now_ts
    }

def get_contact_notes_log(chat_id, limit=50, db_path=None):
    """
    Obtiene las notas del registro histórico / bitácora de un chat en orden cronológico inverso.
    """
    if not chat_id:
        return []
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            SELECT id, chat_id, author, content, created_at
            FROM crm_contact_notes_log
            WHERE chat_id = ? OR chat_id IN (SELECT alias_id FROM contact_aliases WHERE canonical_id = ?)
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """, (canonical_id, canonical_id, limit))
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def delete_contact_note_log(note_id, author=None, db_path=None):
    """
    Elimina una entrada de la bitácora por id. Si author se especifica, solo la elimina si coincide.
    """
    if not note_id:
        return False
    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        if author:
            cur.execute("DELETE FROM crm_contact_notes_log WHERE id = ? AND author = ?", (note_id, author))
        else:
            cur.execute("DELETE FROM crm_contact_notes_log WHERE id = ?", (note_id,))
        return cur.rowcount > 0

def get_chat_tags(chat_id, db_path=None):
    """Devuelve la lista de etiquetas de un chat unificando variantes canónicas."""
    if not chat_id:
        return []
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            SELECT DISTINCT tag, category 
            FROM crm_chat_tags 
            WHERE chat_id = ? OR chat_id IN (SELECT alias_id FROM contact_aliases WHERE canonical_id = ?)
            ORDER BY tag
        """, (canonical_id, canonical_id))
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def add_chat_tag(chat_id, tag, category="general", db_path=None):
    """Añade una etiqueta al chat resolviendo su canonical_id."""
    tag = (tag or "").strip()
    if not tag or not chat_id:
        return False
    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            INSERT OR IGNORE INTO crm_chat_tags (chat_id, tag, category)
            VALUES (?, ?, ?)
        """, (canonical_id, tag, category))
    return True

def remove_chat_tag(chat_id, tag, db_path=None):
    """Elimina una etiqueta del chat resolviendo su canonical_id."""
    if not tag or not chat_id:
        return False
    with db_connection(db_path, write=True) as conn:
        cur = conn.cursor()
        canonical_id = resolve_canonical_chat_id(cur, chat_id)
        cur.execute("""
            DELETE FROM crm_chat_tags 
            WHERE (chat_id = ? OR chat_id IN (SELECT alias_id FROM contact_aliases WHERE canonical_id = ?))
              AND tag = ?
        """, (canonical_id, canonical_id, tag))
    return True

def list_all_tags(db_path=None):
    """Devuelve la lista de etiquetas únicas y su conteo de uso en contactos."""
    with db_connection(db_path, write=False) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT tag, category, COUNT(DISTINCT chat_id) as count
            FROM crm_chat_tags
            GROUP BY tag
            ORDER BY count DESC, tag ASC
        """)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def list_quick_templates(profile_type=None, db_path=None):
    """Devuelve las plantillas de respuesta rápida filtradas por tipo o todas."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    if profile_type and profile_type != "full":
        cur.execute("""
            SELECT id, profile_type, shortcut, title, body
            FROM crm_quick_templates
            WHERE profile_type IN (?, 'both')
            ORDER BY id ASC
        """, (profile_type,))
    else:
        cur.execute("""
            SELECT id, profile_type, shortcut, title, body
            FROM crm_quick_templates
            ORDER BY id ASC
        """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_quick_template(shortcut, title, body, profile_type="both", template_id=None, db_path=None):
    """Crea o actualiza una plantilla de respuesta rápida."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    if template_id:
        cur.execute("""
            UPDATE crm_quick_templates
            SET profile_type = ?, shortcut = ?, title = ?, body = ?
            WHERE id = ?
        """, (profile_type, shortcut, title, body, template_id))
    else:
        cur.execute("""
            INSERT INTO crm_quick_templates (profile_type, shortcut, title, body)
            VALUES (?, ?, ?, ?)
        """, (profile_type, shortcut, title, body))
        template_id = cur.lastrowid
    conn.commit()
    conn.close()
    return template_id

def delete_quick_template(template_id, db_path=None):
    """Elimina una plantilla por su ID."""
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM crm_quick_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return True

# =========================================================================
# FASE 4: Centro de Gestión, Kanban, RFM y Métricas de Demanda / SLA
# =========================================================================

CRM_STOPWORDS = {
    'de', 'la', 'que', 'el', 'en', 'y', 'a', 'los', 'del', 'se', 'las', 'por', 'un', 'para', 'con', 'no', 'una', 
    'su', 'al', 'lo', 'como', 'mas', 'más', 'pero', 'sus', 'le', 'ya', 'o', 'este', 'sí', 'si', 'porque', 'esta', 
    'son', 'entre', 'está', 'cuando', 'muy', 'sin', 'sobre', 'ser', 'tiene', 'también', 'me', 'te', 'nos', 'mi', 
    'tu', 'yo', 'él', 'ella', 'ellos', 'hola', 'gracias', 'buen', 'dia', 'día', 'dias', 'días', 'tarde', 'tardes', 
    'noche', 'noches', 'ok', 'dale', 'bueno', 'bien', 'buenisimo', 'buenísimo', 'ahi', 'ahí', 'todo', 'eso', 'esto', 
    'hace', 'desde', 'hasta', 'puede', 'puedo', 'donde', 'dónde', 'vas', 'voy', 'va', 'vamos', 'van', 'hacer', 
    'tengo', 'tenes', 'tenés', 'mandar', 'mando', 'aviso', 'avisame', 'paso', 'pasame', 'algo', 'nada', 'poco', 
    'mucho', 'cualquier', 'favor', 'abrazo', 'saludos', 'holaa', 'buenas', 'genial', 'listo', 'chau', 'https', 'http', 
    'com', 'ar', 'www', 'null', 'undefined'
}

def get_kanban_board(board_type="sales", db_path=None):
    """
    Retorna los datos estructurados para el tablero Kanban.
    - board_type='sales': columnas lead, quoted, won, lost.
    - board_type='support': columnas open, pending, resolved.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    now_ts = int(time.time())

    cur.execute("""
        SELECT 
            c.chat_id,
            coalesce(c.custom_name, ch.nombre, con.nombre, con.push_name, c.chat_id) as display_name,
            con.numero as phone_number,
            coalesce(c.sales_stage, 'none') as sales_stage,
            coalesce(c.estimated_value, 0.0) as estimated_value,
            c.follow_up_date,
            coalesce(c.assigned_to, '') as assigned_to,
            coalesce(c.updated_by, '') as updated_by,
            coalesce(c.ticket_status, 'none') as ticket_status,
            c.notes,
            c.updated_at,
            ch.ultimo_mensaje as last_message,
            ch.ultimo_es_mio as last_from_me,
            ch.ultimo_timestamp as last_ts
        FROM crm_contact_meta c
        LEFT JOIN chats ch ON c.chat_id = ch.chat_id
        LEFT JOIN contactos con ON c.chat_id = con.contact_id OR c.chat_id = (con.numero || '@c.us')
        WHERE c.chat_id NOT IN (SELECT alias_id FROM contact_aliases)
        GROUP BY c.chat_id
        ORDER BY coalesce(c.updated_at, ch.ultimo_timestamp, 0) DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    def _fmt_activity(ts):
        if not ts:
            return ""
        diff = max(0, now_ts - ts)
        if diff < 3600:
            return f"hace {int(diff // 60)}m"
        elif diff < 86400:
            return f"hace {int(diff // 3600)}h"
        else:
            return f"hace {int(diff // 86400)}d"

    for r in rows:
        r["last_activity_str"] = _fmt_activity(r.get("last_ts"))
        if r.get("last_message"):
            r["last_message"] = r["last_message"][:65]

    if board_type == "support":
        columns = [
            {"id": "open", "title": "Abiertos 🟢", "badge_color": "#10b981", "cards": []},
            {"id": "pending", "title": "En Espera 🟡", "badge_color": "#f59e0b", "cards": []},
            {"id": "resolved", "title": "Resueltos ⚪", "badge_color": "#94a3b8", "cards": []}
        ]
        col_map = {c["id"]: c for c in columns}
        for r in rows:
            st = r["ticket_status"]
            if st in col_map:
                col_map[st]["cards"].append(r)
        for col in columns:
            col["count"] = len(col["cards"])
            col["total_value"] = 0.0
        return {"board_type": "support", "columns": columns}
    else:
        columns = [
            {"id": "lead", "title": "Lead / Prospecto 🎯", "badge_color": "#38bdf8", "cards": [], "total_value": 0.0},
            {"id": "quoted", "title": "Presupuestado 💬", "badge_color": "#f59e0b", "cards": [], "total_value": 0.0},
            {"id": "won", "title": "Ganado 🟢", "badge_color": "#10b981", "cards": [], "total_value": 0.0},
            {"id": "lost", "title": "Perdido 🔴", "badge_color": "#ef4444", "cards": [], "total_value": 0.0}
        ]
        col_map = {c["id"]: c for c in columns}
        for r in rows:
            st = r["sales_stage"]
            if st in col_map:
                col_map[st]["cards"].append(r)
                col_map[st]["total_value"] += float(r.get("estimated_value") or 0.0)
        for col in columns:
            col["count"] = len(col["cards"])
            col["total_value"] = round(col["total_value"], 2)
        return {"board_type": "sales", "columns": columns}

def get_rfm_dormant_clients(days=45, min_messages=10, limit=5, db_path=None):
    """
    Algoritmo RFM de Reactivación Humana: detecta clientes valiosos
    con historial relevante que llevan más de 'days' días sin actividad.
    Genera un mensaje personalizado sugerido para copiar en 1 clic.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    now_ts = int(time.time())
    threshold_sec = days * 86400

    cur.execute("""
        WITH client_stats AS (
            SELECT 
                chat_id,
                COUNT(*) as total_msgs,
                MAX(timestamp) as last_ts
            FROM mensajes
            WHERE chat_id IS NOT NULL 
              AND chat_id NOT LIKE '%@g.us'
              AND chat_id NOT LIKE '%@newsletter'
              AND chat_id != 'status@broadcast'
              AND tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
            GROUP BY chat_id
            HAVING COUNT(*) >= ? AND (? - MAX(timestamp)) >= ?
        ),
        latest_msgs AS (
            SELECT 
                m.chat_id,
                m.contenido,
                m.es_mio,
                m.timestamp,
                ROW_NUMBER() OVER (PARTITION BY m.chat_id ORDER BY m.timestamp DESC) as rn
            FROM mensajes m
            WHERE m.contenido IS NOT NULL AND m.contenido != ''
              AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
        )
        SELECT 
            s.chat_id,
            s.total_msgs,
            s.last_ts,
            coalesce(c.custom_name, ch.nombre, con.nombre, con.push_name, s.chat_id) as display_name,
            con.numero as phone_number,
            coalesce(c.sales_stage, 'none') as sales_stage,
            coalesce(c.estimated_value, 0.0) as estimated_value,
            lm.contenido as last_message,
            lm.es_mio as last_from_me
        FROM client_stats s
        LEFT JOIN chats ch ON s.chat_id = ch.chat_id
        LEFT JOIN contactos con ON s.chat_id = con.contact_id OR s.chat_id = (con.numero || '@c.us')
        LEFT JOIN crm_contact_meta c ON s.chat_id = c.chat_id
        LEFT JOIN latest_msgs lm ON s.chat_id = lm.chat_id AND lm.rn = 1
        GROUP BY s.chat_id
        ORDER BY s.total_msgs DESC
        LIMIT ?
    """, (min_messages, now_ts, threshold_sec, limit))

    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        name = r["display_name"] or r["chat_id"]
        first_name = name.split()[0] if name else "Estimado/a"
        if len(first_name) > 20 or first_name.startswith("+"):
            first_name = "Estimado/a"

        days_inactive = max(0, (now_ts - r["last_ts"]) // 86400) if r["last_ts"] else 0
        last_dt_str = datetime.fromtimestamp(r["last_ts"]).strftime("%d/%m/%Y") if r["last_ts"] else ""

        suggested = (
            f"¡Hola {first_name}! ¿Cómo estás? Te escribo para saludarte y ver cómo andás. "
            f"¿Necesitás alguna consulta o que te preparemos alguna propuesta? ¡Un abrazo!"
        )

        result.append({
            "chat_id": r["chat_id"],
            "display_name": name,
            "phone_number": r["phone_number"],
            "total_messages": r["total_msgs"],
            "days_inactive": days_inactive,
            "last_message_date": last_dt_str,
            "last_message": (r["last_message"][:75] if r["last_message"] else ""),
            "last_from_me": bool(r["last_from_me"]),
            "sales_stage": r["sales_stage"],
            "suggested_message": suggested
        })

    return result

def get_demand_thermometer(days=60, limit=25, db_path=None):
    """
    Termómetro de Demanda: extrae y cuantifica los conceptos y términos
    más consultados por los clientes en mensajes entrantes en los últimos 'days' días.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()
    now_ts = int(time.time())
    since_ts = now_ts - (days * 86400)

    cur.execute("""
        SELECT contenido 
        FROM mensajes 
        WHERE es_mio = 0 
          AND timestamp >= ?
          AND contenido IS NOT NULL 
          AND tipo = 'chat'
        ORDER BY timestamp DESC
        LIMIT 3000
    """, (since_ts,))
    rows = cur.fetchall()
    conn.close()

    from collections import Counter
    import re
    words = []
    for (text,) in rows:
        tokens = re.findall(r'[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]{3,}', text.lower())
        for t in tokens:
            if t not in CRM_STOPWORDS and not t.isdigit():
                words.append(t)

    counts = Counter(words)
    total_words = len(words) or 1
    top_items = []
    for term, cnt in counts.most_common(limit):
        top_items.append({
            "term": term,
            "count": cnt,
            "percentage": round((cnt / total_words) * 100, 1)
        })

    return {
        "days": days,
        "analyzed_messages": len(rows),
        "total_keywords": len(words),
        "top_terms": top_items
    }

def get_support_metrics(db_path=None):
    """
    Panel de Métricas SLA & Horarios Pico:
    Calcula distribución de mensajes entrantes por día de la semana y hora,
    así como estado consolidado de tickets de atención.
    """
    conn = get_connection(db_path)
    cur = conn.cursor()

    # 1. Conteo de tickets
    cur.execute("""
        SELECT ticket_status, COUNT(*) as cnt
        FROM crm_contact_meta
        WHERE ticket_status IN ('open', 'pending', 'resolved')
        GROUP BY ticket_status
    """)
    ticket_counts = {r["ticket_status"]: r["cnt"] for r in cur.fetchall()}

    # 2. SLA global promedio
    cur.execute("""
        WITH ordered AS (
            SELECT 
                chat_id,
                es_mio, 
                timestamp,
                LAG(es_mio) OVER (PARTITION BY chat_id ORDER BY timestamp ASC) as prev_mio,
                LAG(timestamp) OVER (PARTITION BY chat_id ORDER BY timestamp ASC) as prev_ts
            FROM mensajes
            WHERE chat_id NOT LIKE '%@g.us'
              AND chat_id NOT LIKE '%@newsletter'
            ORDER BY timestamp ASC
        )
        SELECT AVG(timestamp - prev_ts) as avg_response_sec
        FROM ordered
        WHERE es_mio = 1 AND prev_mio = 0 AND (timestamp - prev_ts) BETWEEN 5 AND 86400
    """)
    sla_row = cur.fetchone()
    avg_sec = float(sla_row[0]) if (sla_row and sla_row[0]) else 0
    if avg_sec < 60:
        sla_formatted = f"~{int(avg_sec)} seg"
    elif avg_sec < 3600:
        sla_formatted = f"~{int(avg_sec // 60)} min"
    else:
        sla_formatted = f"~{round(avg_sec / 3600, 1)} h"

    # 3. Mapa de calor 7 días x 24 horas (mensajes de clientes)
    cur.execute("""
        SELECT 
            cast(strftime('%w', datetime(timestamp, 'unixepoch', 'localtime')) as integer) as dow,
            cast(strftime('%H', datetime(timestamp, 'unixepoch', 'localtime')) as integer) as hod,
            COUNT(*) as cnt
        FROM mensajes
        WHERE es_mio = 0 
          AND chat_id NOT LIKE '%@g.us'
          AND chat_id NOT LIKE '%@newsletter'
          AND chat_id != 'status@broadcast'
        GROUP BY dow, hod
    """)
    grid = [[0]*24 for _ in range(7)]
    max_val = 0
    peak_dow = 1
    peak_hod = 10
    total_incoming = 0

    for r in cur.fetchall():
        dow = r["dow"]
        hod = r["hod"]
        cnt = r["cnt"]
        grid[dow][hod] = cnt
        total_incoming += cnt
        if cnt > max_val:
            max_val = cnt
            peak_dow = dow
            peak_hod = hod

    conn.close()

    days_names = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
    peak_desc = f"{days_names[peak_dow]} {peak_hod:02d}:00 - {peak_hod+1:02d}:00 hs ({max_val} msgs)"

    return {
        "tickets": {
            "open": ticket_counts.get("open", 0),
            "pending": ticket_counts.get("pending", 0),
            "resolved": ticket_counts.get("resolved", 0),
            "total": sum(ticket_counts.values())
        },
        "global_sla": sla_formatted,
        "heatmap": {
            "grid": grid,
            "days": days_names,
            "max_cell": max_val,
            "total_messages": total_incoming,
            "peak_hour": peak_desc
        }
    }

if __name__ == "__main__":
    init_db()
    status = get_sync_status()
    print("Estado actual de la BD:", status)
