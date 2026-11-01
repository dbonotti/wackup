# -*- coding: utf-8 -*-
"""
entity_extractor.py - Módulo de extracción de entidades comerciales para WAckup Copiloto & CRM.
Identifica mediante expresiones regulares optimizadas:
- Precios y presupuestos cotizados ($ / USD / ARS)
- CUIT / CUIL / DNI
- Correos electrónicos
- CBU / CVU y Alias bancarios
"""

import re
from datetime import datetime

# Regex de precios y montos
PRICE_PATTERN = re.compile(
    r'(?:(?P<symbol>[\$€]|USD|u\$s|ARS|EUR)\s*(?P<amt1>(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)))|'
    r'(?:(?P<amt2>(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?))\s*(?P<currency>USD|ARS|pesos|dólares|dolares|euros))',
    re.IGNORECASE
)

# Palabras clave asociadas a cotizaciones / presupuestos
BUDGET_KEYWORDS = re.compile(
    r'\b(?:presupuesto|cotiz|precio|total|costo|cobro|cuota|seña|sena|abono|honorarios|arancel)\b',
    re.IGNORECASE
)

# Emails
EMAIL_PATTERN = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    re.IGNORECASE
)

# CUIT / CUIL: 11 dígitos, prefijos válidos de AFIP/ARCA (20, 23, 24, 27, 30, 33, 34)
CUIT_PATTERN = re.compile(r'\b(20|23|24|27|30|33|34)-?(\d{8})-?(\d)\b')

# DNI: 7 u 8 dígitos antecedidos obligatoriamente por palabra clave
DNI_PATTERN = re.compile(
    r'\b(?:DNI|doc|documento)[:\s#.\-]+([1-9][0-9]{0,1}\.?[0-9]{3}\.?[0-9]{3})\b',
    re.IGNORECASE
)

# CBU / CVU: 22 dígitos consecutivos
CBU_PATTERN = re.compile(r'\b\d{22}\b')

# Alias bancarios con prefijo explícito (alias, cbu, cvu)
ALIAS_PREFIX_PATTERN = re.compile(
    r'\b(?:alias)[:\s]*([a-zA-Z0-9\.\-_]{6,25})\b',
    re.IGNORECASE
)

# Alias estándar con 3 palabras separadas por puntos (ej: martin.gomez.mp)
ALIAS_DOT_PATTERN = re.compile(
    r'\b([a-zA-Z0-9]{3,15}\.[a-zA-Z0-9]{3,15}\.[a-zA-Z0-9]{3,15})\b',
    re.IGNORECASE
)

COMMON_TLDS = {'.com', '.ar', '.net', '.org', '.io', '.co', '.gob', '.edu', '.info', '.biz'}


def _format_date(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def _extract_snippet(text, start_idx, end_idx, max_len=90):
    """Extrae un fragmento centrado alrededor de la coincidencia."""
    text_len = len(text)
    if text_len <= max_len:
        return text.strip().replace("\n", " ")
    
    half = max_len // 2
    snip_start = max(0, start_idx - half)
    snip_end = min(text_len, end_idx + half)
    
    snippet = text[snip_start:snip_end].strip().replace("\n", " ")
    if snip_start > 0:
        snippet = "..." + snippet
    if snip_end < text_len:
        snippet = snippet + "..."
    return snippet


def extract_entities_from_text(text):
    """
    Analiza un texto individual y retorna todas las entidades comerciales detectadas.
    """
    if not text or not isinstance(text, str):
        return {
            "prices": [],
            "emails": [],
            "tax_ids": [],
            "bank_details": []
        }

    clean_text = text.strip()

    # Precios
    prices = []
    for m in PRICE_PATTERN.finditer(clean_text):
        symbol = m.group("symbol") or "$"
        amt = m.group("amt1") or m.group("amt2")
        curr = m.group("currency") or ""
        price_str = f"{symbol} {amt}".strip()
        if curr and curr.lower() not in symbol.lower():
            price_str += f" {curr}"
        prices.append({
            "price": price_str,
            "snippet": _extract_snippet(clean_text, m.start(), m.end())
        })

    # Emails
    emails = list(dict.fromkeys(EMAIL_PATTERN.findall(clean_text)))

    # CUITs
    tax_ids = []
    cuit_raws = set()
    for m in CUIT_PATTERN.finditer(clean_text):
        cuit_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        cuit_raws.add(f"{m.group(1)}{m.group(2)}{m.group(3)}")
        tax_ids.append({"type": "CUIT", "value": cuit_str})

    # DNIs (que no sean parte de un CUIT ya capturado)
    for m in DNI_PATTERN.finditer(clean_text):
        val = m.group(1).replace(".", "")
        if 7 <= len(val) <= 8 and val not in cuit_raws:
            tax_ids.append({"type": "DNI", "value": m.group(1)})

    # Deduplicar tax_ids
    unique_tax_ids = []
    seen_tax = set()
    for t in tax_ids:
        if t["value"] not in seen_tax:
            seen_tax.add(t["value"])
            unique_tax_ids.append(t)

    # CBU / CVU
    bank_details = []
    cbus = CBU_PATTERN.findall(clean_text)
    for cbu in cbus:
        bank_details.append({"type": "CBU/CVU", "value": cbu})

    # Alias
    seen_aliases = set(cbus)
    for m in ALIAS_PREFIX_PATTERN.finditer(clean_text):
        cand = m.group(1).strip(" .,-_")
        if cand and cand not in seen_aliases and not cand.isdigit():
            seen_aliases.add(cand)
            bank_details.append({"type": "Alias", "value": cand})

    for m in ALIAS_DOT_PATTERN.finditer(clean_text):
        cand = m.group(1).strip()
        if not any(cand.lower().endswith(tld) for tld in COMMON_TLDS):
            if cand not in seen_aliases and not cand.isdigit():
                seen_aliases.add(cand)
                bank_details.append({"type": "Alias", "value": cand})

    return {
        "prices": prices,
        "emails": emails,
        "tax_ids": unique_tax_ids,
        "bank_details": bank_details
    }


def extract_chat_commercial_entities(cur, chat_ids, limit_prices=5, scan_limit=500):
    """
    Escanea en SQLite el historial de mensajes para los IDs indicados (maneja multi-alias).
    Retorna:
    - quoted_prices: lista de los últimos `limit_prices` mensajes con mención monetaria.
    - extracted_info: conjunto deduplicado de emails, CUIT/DNI y CBU/Alias encontrados.
    """
    if not chat_ids:
        return {
            "quoted_prices": [],
            "extracted_info": {"emails": [], "tax_ids": [], "bank_details": []}
        }

    id_list = list(chat_ids)
    placeholders = ",".join("?" for _ in id_list)

    cur.execute(f"""
        SELECT msg_id, es_mio, timestamp, contenido
        FROM mensajes
        WHERE chat_id IN ({placeholders})
          AND contenido IS NOT NULL 
          AND contenido != ''
          AND tipo NOT IN ('e2e_notification', 'notification_template', 'protocol')
        ORDER BY timestamp DESC
        LIMIT ?
    """, id_list + [scan_limit])

    rows = cur.fetchall()

    quoted_prices = []
    all_emails = set()
    all_tax_ids = {}
    all_bank_details = {}

    for row in rows:
        text = row["contenido"]
        ts = row["timestamp"]
        es_mio = bool(row["es_mio"])
        
        # 1. Búsqueda de precios para quoted_prices
        if len(quoted_prices) < limit_prices:
            price_match = PRICE_PATTERN.search(text)
            has_keyword = BUDGET_KEYWORDS.search(text) is not None
            if price_match or has_keyword:
                symbol = (price_match.group("symbol") if price_match else "$") or "$"
                amt = (price_match.group("amt1") or price_match.group("amt2")) if price_match else ""
                curr = (price_match.group("currency") if price_match else "") or ""
                
                if amt:
                    price_display = f"{symbol} {amt}".strip()
                    if curr and curr.lower() not in symbol.lower():
                        price_display += f" {curr}"
                else:
                    price_display = "Cotización / Presupuesto"

                quoted_prices.append({
                    "msg_id": row["msg_id"],
                    "timestamp": ts,
                    "date_str": _format_date(ts),
                    "es_mio": es_mio,
                    "sender": "Nosotros" if es_mio else "Cliente",
                    "price": price_display,
                    "snippet": _extract_snippet(text, price_match.start() if price_match else 0, price_match.end() if price_match else len(text))
                })

        # 2. Extracción de entidades para la ficha del cliente
        ent = extract_entities_from_text(text)
        for e in ent["emails"]:
            all_emails.add(e)
        for t in ent["tax_ids"]:
            all_tax_ids[t["value"]] = t
        for b in ent["bank_details"]:
            all_bank_details[b["value"]] = b

    return {
        "quoted_prices": quoted_prices,
        "extracted_info": {
            "emails": list(all_emails),
            "tax_ids": list(all_tax_ids.values()),
            "bank_details": list(all_bank_details.values())
        }
    }
