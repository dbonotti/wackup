import sqlite3
import os
import re
import html
from datetime import datetime, timezone
from config import DB_FILE, BACKUP_DIR, ensure_data_dirs

def get_connection(db_path=DB_FILE, check_init=True):
    ensure_data_dirs()
    if db_path:
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    # Activar WAL mode y timeout para alta concurrencia
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=60000;")
    if check_init:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mensajes'")
        if not cur.fetchone():
            init_db(db_path)
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
        ultimo_timestamp INTEGER
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

    conn.commit()
    conn.close()
    print("Base de datos inicializada y esquema verificado con éxito.")

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
              AND NOT (tipo = 'chat' AND (contenido IS NULL OR contenido = '') AND (quoted_body IS NULL OR quoted_body = ''))
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
    """Limpia prefijos residuales de cabeceras Protobuf (ej. 'a\\n9Texto' o 'r\\nJTexto')."""
    if not text or not isinstance(text, str):
        return ""
    t = text.replace("\ufffd", "")
    t = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', t).strip()
    # Si empieza con 1-2 caracteres ASCII + salto de línea + 1 byte de longitud ASCII pegado al texto real:
    # Caso 1: el byte de longitud es dígito/símbolo seguido de letra/emoji/signo (ej. "a\n9Ya en la camita!", "I\n!Muy buenas", "S\n+Nos quedamos")
    t = re.sub(r'^[A-Za-z0-9]{1,2}\r?\n[0-9!-/:-@\[-`{-~](?=[A-Za-zÁÉÍÓÚáéíóúÑñ¿¡"\'\U00010000-\U0010ffff])', '', t)
    # Caso 2: el byte de longitud es una letra (ej. 'J' = 74 bytes, 'M' = 77 bytes) seguida inmediatamente de mayúscula + minúscula (ej. "r\nJAcá todo", "u\nMSe va")
    t = re.sub(r'^[A-Za-z0-9]{1,2}\r?\n[A-Za-z](?=[A-ZÁÉÍÓÚÑ¿¡][a-záéíóúñ])', '', t)
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

    # 1. Miniatura o imagen en base64
    if "/9j/" in t or t.startswith("data:image"):
        return True

    # 2. Identificadores JID de WhatsApp filtrados como texto (ej. "258741781934082@lid")
    if re.match(r'^[0-9a-zA-Z._+\-]+@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast|newsletter)$', t, re.IGNORECASE):
        return True
    if any(domain in t for domain in ("@s.whatsapp.net", "@lid", "@g.us", "@c.us")) and " " not in t:
        return True

    # 3. Cadena base64 o hash hexadecimal largo sin espacios
    if len(t) > 35 and " " not in t and re.match(r'^[A-Za-z0-9+/=_\-\*\&\"\'\(\)\%\;\:\$\^]+$', t):
        return True

    # 4. Ruido binario / claves cifradas decodificadas como texto (ej. ']X0lH͎y)M⌢a"' o '>GpJΕprȇ]Ԁa')
    weird_chars = 0
    for ch in t:
        code = ord(ch)
        if not (
            (65 <= code <= 90) or
            (97 <= code <= 122) or
            (48 <= code <= 57) or
            code in (32, 9, 10, 13) or
            ch in ".,;:!?¿¡()[]{}-\"'/@#$%&*+=_~<>|°ºª€$" or
            (0x00C0 <= code <= 0x017F) or  # Acentos latinos y ñ
            (0x2000 <= code <= 0x206F) or  # Puntuación general
            (0x2600 <= code <= 0x27BF) or  # Símbolos / emojis comunes
            (0xFE00 <= code <= 0xFE0F) or  # Variation selectors
            (0x1F000 <= code <= 0x1FAFF)   # Emojis Unicode suplementarios
        ):
            weird_chars += 1

    if len(t) > 0 and (weird_chars / len(t)) > 0.18:
        return True
    if " " not in t and len(t) >= 6 and weird_chars >= 1:
        return True

    # 5. Audio / PTT: JIDs filtrados o ruido binario
    if tipo in ("audio", "ptt"):
        if any(domain in t for domain in ("@s.whatsapp.net", "@lid", "@g.us", "@c.us")):
            return True
        if not re.search(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}', t):
            return True

    # 6. Stickers
    if tipo == "sticker":
        if any(domain in t for domain in ("@s.whatsapp.net", "@lid", "@g.us", "@c.us")):
            return True
        if len(t) <= 15 and " " not in t:
            return True

    # 7. Documentos con identificadores internos
    if tipo == "document" and len(t) > 50 and " " not in t and re.match(r'^[A-Za-z0-9_\-]+$', t):
        return True

    return False

def save_incremental_sync(data, db_path=DB_FILE):
    """Guarda lotes de contactos, chats y mensajes de forma incremental."""
    conn = get_connection(db_path)
    cur = conn.cursor()

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

        # Protección contra suplantación si el body recibido es idéntico al mensaje citado
        if quoted_msg_id and body:
            cur.execute("SELECT contenido FROM mensajes WHERE msg_id LIKE ? LIMIT 1", (f"%{quoted_msg_id}%",))
            q_row = cur.fetchone()
            if q_row and q_row[0] and q_row[0] == body:
                if not quoted_body:
                    quoted_body = body
                cur.execute("SELECT contenido FROM mensajes WHERE msg_id = ?", (mid,))
                prev_row = cur.fetchone()
                if prev_row and prev_row[0] and prev_row[0] != body:
                    body = prev_row[0]
                else:
                    body = ""

        # Asegurar que el chat padre exista en la tabla chats (con nombre NULL si no se conoce aún)
        if chat_id:
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_timestamp)
                VALUES (?, NULL, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    ultimo_timestamp = CASE
                        WHEN excluded.ultimo_timestamp IS NOT NULL AND (chats.ultimo_timestamp IS NULL OR excluded.ultimo_timestamp > chats.ultimo_timestamp)
                        THEN excluded.ultimo_timestamp
                        ELSE chats.ultimo_timestamp
                    END
            """, (chat_id, ts if m_type != "call_log" else None))

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
                    WHEN excluded.contenido != '' AND (excluded.quoted_body IS NULL OR excluded.contenido != excluded.quoted_body) 
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

    conn.commit()
    conn.close()
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

    if chat_id:
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
        WHERE mensajes_fts MATCH ? AND m.chat_id = ?
        ORDER BY m.timestamp DESC
        LIMIT ?
        """
        cur.execute(sql, (fts_match, chat_id, limit))
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

if __name__ == "__main__":
    init_db()
    status = get_sync_status()
    print("Estado actual de la BD:", status)
