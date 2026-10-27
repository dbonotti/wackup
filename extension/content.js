(function() {
  'use strict';

  const AUTO_SYNC_INTERVAL_MS = 20000; // Sincroniza en segundo plano cada 20 segundos
  const BATCH_SIZE = 500; // Enviar en bloques para no saturar memoria
  const DECRYPT_CHUNK_SIZE = 50; // Descifrado en paralelo por lotes

  let isSyncing = false;
  let autoSyncTimer = null;
  let verifiedKeyData = null; // { key, algName } (clave activa principal)
  const verifiedKeys = []; // Lista MRU de claves verificadas [{ key, algName }]
  const candidateKeys = []; // Pool de todas las CryptoKey secretas detectadas [{ key, algName }]
  let isVerifyingKeys = false;
  let knownMessageKeys = null; // Set en memoria de IDs de mensajes ya sincronizados con éxito
  let lastContactsHash = null;
  let lastChatsHash = null;
  let lastContactsSyncTime = 0;

  function fastHash(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(36);
  }

  console.log("%c[WAckup] Iniciando interceptor pasivo de respaldo local...", "color: #00a884; font-weight: bold; font-size: 13px;");

  // =========================================================================
  // 1. INTERCEPTOR MULTICLAVE DE WEBCRYPTO + ESCÁNER DE INDEXEDDB
  // =========================================================================
  const origDecrypt = window.crypto.subtle.decrypt.bind(window.crypto.subtle);
  const origEncrypt = window.crypto.subtle.encrypt.bind(window.crypto.subtle);
  const origImportKey = window.crypto.subtle.importKey.bind(window.crypto.subtle);
  const origDeriveKey = window.crypto.subtle.deriveKey.bind(window.crypto.subtle);
  const origUnwrapKey = window.crypto.subtle.unwrapKey.bind(window.crypto.subtle);
  const origGenerateKey = window.crypto.subtle.generateKey.bind(window.crypto.subtle);

  function toBufferSource(val) {
    if (!val) return null;
    if (val instanceof ArrayBuffer || ArrayBuffer.isView(val)) return val;
    if (val.buffer instanceof ArrayBuffer) return val.buffer;
    if (typeof val === "object" && typeof val.byteLength === "number") return val;
    return null;
  }

  function registerCandidateKey(key, algHint) {
    if (!key || key.type !== "secret") return;
    const algName = (algHint && (typeof algHint === "string" ? algHint : algHint.name)) ||
                    (key.algorithm && key.algorithm.name) ||
                    "AES-GCM";
    if (!algName.toUpperCase().includes("AES")) return;

    if (!candidateKeys.some(k => k.key === key)) {
      candidateKeys.push({ key, algName });
      verifyCandidateKeys();
    }
  }

  async function verifyCandidateKeys() {
    if (isVerifyingKeys) return;
    const unverified = candidateKeys.filter(c => !verifiedKeys.some(v => v.key === c.key));
    if (unverified.length === 0) return;

    isVerifyingKeys = true;
    try {
      const samples = await getSampleEncryptedMessages();
      if (!samples || samples.length === 0) return;

      let newlyVerified = false;
      for (const cand of unverified) {
        for (const sample of samples) {
          const opaque = sample && sample.msgRowOpaqueData;
          if (!opaque) continue;
          const iv = toBufferSource(opaque.iv);
          const cipher = toBufferSource(opaque._data || opaque.data || opaque.ciphertext);
          if (!iv || !cipher) continue;
          try {
            const testRes = await origDecrypt({ name: cand.algName, iv }, cand.key, cipher);
            if (testRes && testRes.byteLength > 0) {
              if (!verifiedKeys.some(v => v.key === cand.key)) {
                verifiedKeys.unshift(cand);
                newlyVerified = true;
              }
              break;
            }
          } catch (e) {}
        }
      }

      if (newlyVerified && verifiedKeys.length > 0) {
        verifiedKeyData = verifiedKeys[0];
        window.__wa_db_verified = verifiedKeyData;
        window.__wa_db_verified_keys = verifiedKeys;
        console.log(`%c🎯 [WAckup] Clave de base de datos verificada (${verifiedKeys.length} clave(s) activa(s))`, "color: #10b981; font-weight: bold; font-size: 14px;");
        updateWidgetStatus("ready", "Clave activa");
        triggerSync(true, false);
      }
    } finally {
      isVerifyingKeys = false;
    }
  }

  async function scanIndexedDBForCryptoKeys() {
    try {
      const dbNames = ["model-storage", "wawc", "signal-storage"];
      if (typeof window.indexedDB.databases === "function") {
        try {
          const dbs = await window.indexedDB.databases();
          for (const info of dbs) {
            if (info && info.name && !dbNames.includes(info.name)) {
              dbNames.push(info.name);
            }
          }
        } catch (e) {}
      }

      const skipStores = new Set(["message", "chat", "contact", "participant", "group-metadata", "label", "media-data"]);

      function findCryptoKeysInValue(val, depth = 0) {
        if (!val || depth > 4) return;
        if (typeof CryptoKey !== "undefined" && val instanceof CryptoKey) {
          registerCandidateKey(val);
          return;
        }
        if (typeof val === "object" && val.type === "secret" && val.algorithm) {
          registerCandidateKey(val);
          return;
        }
        if (Array.isArray(val)) {
          for (const item of val) findCryptoKeysInValue(item, depth + 1);
        } else if (typeof val === "object" && !(val instanceof ArrayBuffer) && !ArrayBuffer.isView(val)) {
          for (const k of Object.keys(val)) {
            try { findCryptoKeysInValue(val[k], depth + 1); } catch (e) {}
          }
        }
      }

      for (const dbName of dbNames) {
        const db = await openIndexedDB(dbName);
        if (!db) continue;
        const storeNames = Array.from(db.objectStoreNames || []);
        for (const storeName of storeNames) {
          if (skipStores.has(storeName)) continue;
          try {
            const count = await getStoreCount(db, storeName);
            if (count > 0 && count <= 500) {
              const records = await getAllFromStore(db, storeName);
              for (const rec of records) {
                findCryptoKeysInValue(rec, 0);
              }
            }
          } catch (e) {}
        }
        db.close();
      }
    } catch (e) {}
  }

  async function setupCryptoInterceptor() {
    if (Array.isArray(window.__wa_db_verified_keys) && window.__wa_db_verified_keys.length > 0) {
      for (const k of window.__wa_db_verified_keys) {
        if (!verifiedKeys.some(v => v.key === k.key)) verifiedKeys.push(k);
        if (!candidateKeys.some(c => c.key === k.key)) candidateKeys.push(k);
      }
    }
    if (window.__wa_db_verified) {
      verifiedKeyData = window.__wa_db_verified;
      if (!verifiedKeys.some(v => v.key === verifiedKeyData.key)) verifiedKeys.unshift(verifiedKeyData);
      if (!candidateKeys.some(c => c.key === verifiedKeyData.key)) candidateKeys.unshift(verifiedKeyData);
      updateWidgetStatus("ready", "Clave activa");
    }

    window.crypto.subtle.decrypt = async function(algorithm, key, data) {
      if (key && key.type === "secret") {
        registerCandidateKey(key, algorithm);
      }
      return origDecrypt(algorithm, key, data);
    };

    window.crypto.subtle.encrypt = async function(algorithm, key, data) {
      if (key && key.type === "secret") {
        registerCandidateKey(key, algorithm);
      }
      return origEncrypt(algorithm, key, data);
    };

    window.crypto.subtle.importKey = async function(format, keyData, algorithm, extractable, keyUsages) {
      let usages = keyUsages;
      if (Array.isArray(keyUsages) && keyUsages.includes("encrypt") && !keyUsages.includes("decrypt")) {
        usages = [...keyUsages, "decrypt"];
      }
      let res;
      try {
        res = await origImportKey(format, keyData, algorithm, extractable, usages);
      } catch (e) {
        res = await origImportKey(format, keyData, algorithm, extractable, keyUsages);
      }
      if (res && res.type === "secret") registerCandidateKey(res, algorithm);
      return res;
    };

    window.crypto.subtle.deriveKey = async function(algorithm, baseKey, derivedKeyType, extractable, keyUsages) {
      let usages = keyUsages;
      if (Array.isArray(keyUsages) && keyUsages.includes("encrypt") && !keyUsages.includes("decrypt")) {
        usages = [...keyUsages, "decrypt"];
      }
      let res;
      try {
        res = await origDeriveKey(algorithm, baseKey, derivedKeyType, extractable, usages);
      } catch (e) {
        res = await origDeriveKey(algorithm, baseKey, derivedKeyType, extractable, keyUsages);
      }
      if (res && res.type === "secret") registerCandidateKey(res, derivedKeyType);
      return res;
    };

    window.crypto.subtle.unwrapKey = async function(format, wrappedKey, unwrappingKey, unwrapAlgo, unwrappedKeyAlgo, extractable, keyUsages) {
      let usages = keyUsages;
      if (Array.isArray(keyUsages) && keyUsages.includes("encrypt") && !keyUsages.includes("decrypt")) {
        usages = [...keyUsages, "decrypt"];
      }
      let res;
      try {
        res = await origUnwrapKey(format, wrappedKey, unwrappingKey, unwrapAlgo, unwrappedKeyAlgo, extractable, usages);
      } catch (e) {
        res = await origUnwrapKey(format, wrappedKey, unwrappingKey, unwrapAlgo, unwrappedKeyAlgo, extractable, keyUsages);
      }
      if (res && res.type === "secret") registerCandidateKey(res, unwrappedKeyAlgo);
      return res;
    };

    window.crypto.subtle.generateKey = async function(algorithm, extractable, keyUsages) {
      let usages = keyUsages;
      if (Array.isArray(keyUsages) && keyUsages.includes("encrypt") && !keyUsages.includes("decrypt")) {
        usages = [...keyUsages, "decrypt"];
      }
      let res;
      try {
        res = await origGenerateKey(algorithm, extractable, usages);
      } catch (e) {
        res = await origGenerateKey(algorithm, extractable, keyUsages);
      }
      if (res && res.type === "secret") registerCandidateKey(res, algorithm);
      return res;
    };

    // Buscar claves ya persistidas en IndexedDB sin esperar interacción manual
    setTimeout(scanIndexedDBForCryptoKeys, 1200);
    setTimeout(scanIndexedDBForCryptoKeys, 5000);
  }

  function getSampleEncryptedMessages() {
    return new Promise((resolve) => {
      try {
        const req = window.indexedDB.open("model-storage");
        req.onerror = () => resolve([]);
        req.onsuccess = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains("message")) {
            db.close();
            return resolve([]);
          }
          const samples = [];
          const seenIds = new Set();
          const tx = db.transaction("message", "readonly");
          const store = tx.objectStore("message");

          function addSample(val) {
            if (!val || !val.msgRowOpaqueData) return false;
            const opaque = val.msgRowOpaqueData;
            if (!opaque.iv || !(opaque._data || opaque.data || opaque.ciphertext)) return false;
            const id = extractId(val.id) || String(samples.length);
            if (!seenIds.has(id)) {
              seenIds.add(id);
              samples.push(val);
              return true;
            }
            return false;
          }

          // 1. Muestras de los mensajes más recientes por timestamp ('t')
          let recentCount = 0;
          const hasTIndex = store.indexNames && store.indexNames.contains("t");
          const step1Source = hasTIndex ? store.index("t") : store;
          const curRecent = step1Source.openCursor(null, "prev");

          curRecent.onsuccess = (e) => {
            const cursor = e.target.result;
            if (cursor && recentCount < 6) {
              if (addSample(cursor.value)) recentCount++;
              cursor.continue();
            } else {
              // 2. Muestras del final e inicio por clave primaria
              let prevCount = 0;
              const curPrev = store.openCursor(null, "prev");
              curPrev.onsuccess = (e2) => {
                const c2 = e2.target.result;
                if (c2 && prevCount < 4) {
                  if (addSample(c2.value)) prevCount++;
                  c2.continue();
                } else {
                  db.close();
                  resolve(samples);
                }
              };
              curPrev.onerror = () => { db.close(); resolve(samples); };
            }
          };
          curRecent.onerror = () => { db.close(); resolve(samples); };
        };
      } catch (err) {
        resolve([]);
      }
    });
  }

  async function decryptOpaqueData(opaque) {
    if (!opaque) return null;
    const iv = toBufferSource(opaque.iv);
    const cipher = toBufferSource(opaque._data || opaque.data || opaque.ciphertext);
    if (!iv || !cipher) return null;

    // 1. Intentar con claves ya verificadas (ordenadas por uso reciente MRU)
    for (let i = 0; i < verifiedKeys.length; i++) {
      const kData = verifiedKeys[i];
      try {
        const decBuf = await origDecrypt({ name: kData.algName, iv }, kData.key, cipher);
        if (decBuf && decBuf.byteLength > 0) {
          if (i > 0) {
            verifiedKeys.splice(i, 1);
            verifiedKeys.unshift(kData);
            verifiedKeyData = verifiedKeys[0];
          }
          return decBuf;
        }
      } catch (e) {}
    }

    // 2. Intentar con todas las claves candidatas capturadas (rotación de claves de sesión)
    for (let i = 0; i < candidateKeys.length; i++) {
      const kData = candidateKeys[i];
      if (verifiedKeys.some(v => v.key === kData.key)) continue;
      try {
        const decBuf = await origDecrypt({ name: kData.algName, iv }, kData.key, cipher);
        if (decBuf && decBuf.byteLength > 0) {
          verifiedKeys.unshift(kData);
          verifiedKeyData = verifiedKeys[0];
          window.__wa_db_verified = verifiedKeyData;
          window.__wa_db_verified_keys = verifiedKeys;
          return decBuf;
        }
      } catch (e) {}
    }

    return null;
  }

  function getInMemoryMessagesMap() {
    const map = new Map();
    try {
      if (typeof window.require !== "function") return map;
      const mod = window.require("WAWebMsgCollection");
      const col = mod && (mod.MsgCollection || mod.default || mod);
      const models = col && (typeof col.getModelsArray === "function" ? col.getModelsArray() : col._models);
      if (Array.isArray(models)) {
        for (const m of models) {
          const id = extractId(m && m.id);
          if (!id) continue;
          const body = typeof m.body === "string" ? m.body : (typeof m.text === "string" ? m.text : "");
          const caption = typeof m.caption === "string" ? m.caption : "";
          const isForwarded = Boolean(m.isForwarded || (m.forwardingScore && m.forwardingScore > 0));
          const forwardingScore = Number(m.forwardingScore) || (isForwarded ? 1 : 0);
          if (body || caption || isForwarded) {
            map.set(id, { body, caption, isForwarded, forwardingScore });
          }
        }
      }
    } catch (e) {}
    return map;
  }

  // =========================================================================
  // 2. DECODIFICADOR PROTOBUF REAL Y UTILIDADES DE TEXTO
  // =========================================================================
  function readVarint(bytes, pos) {
    let val = 0;
    let shift = 0;
    while (pos < bytes.length) {
      const b = bytes[pos++];
      val += (b & 0x7f) * Math.pow(2, shift);
      if ((b & 0x80) === 0) return { val, pos };
      shift += 7;
      if (shift > 56) return null;
    }
    return null;
  }

  function isGarbageString(s) {
    if (!s) return true;
    const t = s.trim();
    if (!t) return true;
    // Ignorar JIDs de WhatsApp
    if (/^[0-9a-zA-Z._+-]+@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast|newsletter)$/i.test(t)) return true;
    if (/@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast)\b/i.test(t) && !t.includes(" ")) return true;
    // Ignorar hashes hexadecimales o identificadores de mensaje (ej. 3EB0..., AC68...)
    if (/^[0-9A-F]{16,64}$/i.test(t)) return true;
    // Ignorar mimetypes y URLs internas de CDN
    if (/^(image|video|audio|application|text)\/[a-z0-9.+-]+(;.*)?$/i.test(t)) return true;
    if (t.startsWith("https://mmg.whatsapp.net") || t.startsWith("/v/t62.") || t.startsWith("/mms/")) return true;
    // Ignorar cadenas Base64 largas sin espacios
    if (t.startsWith("data:image") || t.includes("/9j/")) return true;
    if (t.length > 35 && !/\s/.test(t) && /^[A-Za-z0-9+/=_-]+$/.test(t)) return true;

    // Verificar proporción de caracteres legibles vs símbolos aleatorios (evitar claves AES/MAC de 32 bytes interpretadas como texto)
    let naturalChars = 0;
    let weirdChars = 0;
    for (let i = 0; i < t.length; i++) {
      const code = t.charCodeAt(i);
      if (
        (code >= 65 && code <= 90) ||
        (code >= 97 && code <= 122) ||
        (code >= 48 && code <= 57) ||
        code === 32 || code === 10 || code === 13 ||
        ".,;:!?¿¡()[]{}-\"'/@#$%&*+=_~<>|°ºª€$".includes(t[i]) ||
        (code >= 0x00C0 && code <= 0x017F) || // Acentos latinos y ñ
        (code >= 0x2000 && code <= 0x206F) || // Puntuación general
        (code >= 0xFE00 && code <= 0xFE0F) || // Variation selectors
        (code >= 0xD800 && code <= 0xDFFF) || // Surrogates de Emojis
        (code >= 0x2600 && code <= 0x27BF)    // Símbolos/Emojis adicionales
      ) {
        naturalChars++;
      } else {
        weirdChars++;
      }
    }
    if (weirdChars > 0 && weirdChars / t.length > 0.2) return true;
    if (!t.includes(" ") && t.length >= 6 && weirdChars >= 1) return true;
    return false;
  }

  function isLikelyMediaBinaryFragment(s) {
    if (!s) return true;
    const t = s.trim();
    if (!t) return true;
    // En mensajes multimedia, descartar fragmentos cortos sin espacios que mezclan símbolos técnicos o dígitos/mayúsculas aleatorias (ej. "2:", "NX<ah+s", "%U3", "t8qJt")
    if (!t.includes(" ") && t.length <= 12) {
      if (/[<>\[\]{}()%^~|&*=+:;\/\\`$_]/.test(t)) return true;
      if (/\d/.test(t) && /[A-Za-z]/.test(t)) return true;
      if (/^[A-Za-z]{1,4}$/.test(t) && /[A-Z]/.test(t) && /[a-z]/.test(t) && !/^[A-Z][a-z]{2,3}$/.test(t)) return true;
    }
    return false;
  }

  function parseProtobufTopFields(bytes) {
    const fields = new Map();
    if (!bytes || bytes.length < 2) return fields;
    let pos = 0;
    while (pos < bytes.length) {
      const tagRes = readVarint(bytes, pos);
      if (!tagRes || tagRes.val <= 0) break;
      pos = tagRes.pos;
      const wireType = tagRes.val & 7;
      const fieldNum = Math.floor(tagRes.val / 8);
      if (fieldNum <= 0 || fieldNum > 5000) break;

      if (wireType === 0) {
        const v = readVarint(bytes, pos);
        if (!v) break;
        pos = v.pos;
      } else if (wireType === 1) {
        pos += 8;
        if (pos > bytes.length) break;
      } else if (wireType === 5) {
        pos += 4;
        if (pos > bytes.length) break;
      } else if (wireType === 2) {
        const lenRes = readVarint(bytes, pos);
        if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > bytes.length) break;
        const sub = bytes.subarray(lenRes.pos, lenRes.pos + lenRes.val);
        pos = lenRes.pos + lenRes.val;
        fields.set(fieldNum, sub);
      } else {
        break;
      }
    }
    return fields;
  }

  function extractProtobufStrings(bytes, depth = 0, isMedia = false) {
    if (depth > 5 || !bytes || bytes.length === 0) return [];
    // Descartar bloques binarios JPEG crudos (miniaturas jpegThumbnail: FF D8 FF)
    if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return [];
    const candidates = [];
    let pos = 0;

    while (pos < bytes.length) {
      const tagRes = readVarint(bytes, pos);
      if (!tagRes || tagRes.val <= 0) break;
      pos = tagRes.pos;

      const wireType = tagRes.val & 7;
      const fieldNum = Math.floor(tagRes.val / 8);
      if (fieldNum <= 0 || fieldNum > 5000) break;

      if (wireType === 0) {
        const v = readVarint(bytes, pos);
        if (!v) break;
        pos = v.pos;
      } else if (wireType === 1) {
        pos += 8;
        if (pos > bytes.length) break;
      } else if (wireType === 5) {
        pos += 4;
        if (pos > bytes.length) break;
      } else if (wireType === 2) {
        const lenRes = readVarint(bytes, pos);
        if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > bytes.length) break;
        const sub = bytes.subarray(lenRes.pos, lenRes.pos + lenRes.val);
        pos = lenRes.pos + lenRes.val;

        if (sub.length > 0) {
          // Evitar inspeccionar miniaturas JPEG crudas como texto o submensajes
          if (sub.length >= 3 && sub[0] === 0xff && sub[1] === 0xd8 && sub[2] === 0xff) {
            continue;
          }
          // ContextInfo (campo 17 en ExtendedTextMessage, ImageMessage, VideoMessage, etc.) contiene el mensaje citado, no el propio
          if (fieldNum === 17) {
            continue;
          }
          // En MsgRowOpaqueData en profundidad 0, el campo 2 es quotedMsg (el mensaje citado). Debe ignorarse como cuerpo del mensaje actual
          if (depth === 0 && fieldNum === 2) {
            continue;
          }

          // Si sub parece ser un submensaje Protobuf anidado (ej. empieza con tag wireType 2 y longitud que abarca el resto de sub),
          // no decodificar sub directamente como string crudo para evitar que el tag (0x0A='\n') y el byte de longitud (ej. 0x42='B')
          // se interpreten como caracteres ASCII pegados al texto.
          let isSubMessage = false;
          if (sub.length >= 2) {
            const subWire = sub[0] & 7;
            const subField = sub[0] >> 3;
            if (subField >= 1 && subField <= 50 && (subWire === 0 || subWire === 2)) {
              if (subWire === 2) {
                const subLen = readVarint(sub, 1);
                if (subLen && subLen.val > 0 && (subLen.pos + subLen.val === sub.length || subLen.pos + subLen.val <= sub.length - 2)) {
                  isSubMessage = true;
                }
              }
            }
          }

          // 1. Intentar decodificar como UTF-8 estricto si no es un submensaje contenedor
          const isAllowedMediaField = !isMedia || (fieldNum === 1 || fieldNum === 2 || fieldNum === 3 || fieldNum === 6 || fieldNum === 8 || fieldNum === 20);
          if (!isSubMessage && isAllowedMediaField) {
            try {
              const str = new TextDecoder("utf-8", { fatal: true }).decode(sub);
              if (!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(str)) {
                const cleaned = str.trim();
                if (cleaned && !isGarbageString(cleaned) && (!isMedia || !isLikelyMediaBinaryFragment(cleaned))) {
                  const bonus = (fieldNum === 1 || fieldNum === 2 || fieldNum === 3 || fieldNum === 6 || fieldNum === 20) ? 1000 : 0;
                  candidates.push({ text: cleaned, score: cleaned.length + bonus });
                }
              }
            } catch (e) {}
          }

          // 2. Explorar recursivamente por si es un mensaje contenedor (Message / ExtendedTextMessage / ImageMessage)
          const nested = extractProtobufStrings(sub, depth + 1, isMedia);
          for (const item of nested) {
            candidates.push(item);
          }
        }
      } else {
        break;
      }
    }
    // Si es un submensaje anidado y no terminó exactamente al final del buffer, era un campo binario (ej. scansSidecar / mediaKey), no un submensaje Protobuf
    if (depth > 0 && pos !== bytes.length) return [];
    return candidates;
  }

  function extractContextInfoFromBuffer(buffer) {
    if (!buffer) return null;
    const bytes = new Uint8Array(buffer);
    if (bytes.length < 3) return null;

    // Si buffer es un MsgRowOpaqueData con campo 2 (quotedMsg), extraer el texto citado
    const topFields = parseProtobufTopFields(bytes);
    let topQuotedBody = "";
    if (topFields.has(2)) {
      topQuotedBody = extractTextFromBuffer(topFields.get(2), false);
    }

    function findContextInfoBytes(subBytes, depth) {
      if (depth > 4 || !subBytes || subBytes.length < 3) return null;
      let pos = 0;
      while (pos < subBytes.length) {
        const tagRes = readVarint(subBytes, pos);
        if (!tagRes || tagRes.val <= 0) break;
        pos = tagRes.pos;
        const wireType = tagRes.val & 7;
        const fieldNum = Math.floor(tagRes.val / 8);

        if (wireType === 0) {
          const v = readVarint(subBytes, pos);
          if (!v) break;
          pos = v.pos;
        } else if (wireType === 1) {
          pos += 8;
        } else if (wireType === 5) {
          pos += 4;
        } else if (wireType === 2) {
          const lenRes = readVarint(subBytes, pos);
          if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > subBytes.length) break;
          const sub = subBytes.subarray(lenRes.pos, lenRes.pos + lenRes.val);
          pos = lenRes.pos + lenRes.val;

          if (fieldNum === 17) {
            return sub;
          }
          if (fieldNum === 1 || fieldNum === 3 || fieldNum === 6 || fieldNum === 7 || fieldNum === 9 || fieldNum === 40 || fieldNum === 41 || fieldNum === 45) {
            const found = findContextInfoBytes(sub, depth + 1);
            if (found) return found;
          }
        } else {
          break;
        }
      }
      return null;
    }

    const ctxBytes = findContextInfoBytes(bytes, 0);
    if (!ctxBytes) {
      if (topQuotedBody) {
        return { stanzaId: null, participant: null, quotedBody: topQuotedBody };
      }
      return null;
    }

    let stanzaId = null;
    let participant = null;
    let quotedBody = "";

    let pos = 0;
    while (pos < ctxBytes.length) {
      const tagRes = readVarint(ctxBytes, pos);
      if (!tagRes || tagRes.val <= 0) break;
      pos = tagRes.pos;
      const wireType = tagRes.val & 7;
      const fieldNum = Math.floor(tagRes.val / 8);

      if (wireType === 0) {
        const v = readVarint(ctxBytes, pos);
        if (!v) break;
        pos = v.pos;
      } else if (wireType === 1) {
        pos += 8;
      } else if (wireType === 5) {
        pos += 4;
      } else if (wireType === 2) {
        const lenRes = readVarint(ctxBytes, pos);
        if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > ctxBytes.length) break;
        const sub = ctxBytes.subarray(lenRes.pos, lenRes.pos + lenRes.val);
        pos = lenRes.pos + lenRes.val;

        if (fieldNum === 1) {
          try { stanzaId = new TextDecoder("utf-8").decode(sub).trim(); } catch (e) {}
        } else if (fieldNum === 2) {
          try { participant = new TextDecoder("utf-8").decode(sub).trim(); } catch (e) {}
        } else if (fieldNum === 3) {
          quotedBody = extractTextFromBuffer(sub, false);
        }
      } else {
        break;
      }
    }

    if (!quotedBody && topQuotedBody) {
      quotedBody = topQuotedBody;
    }

    return { stanzaId, participant, quotedBody };
  }

  function scanLengthPrefixedStrings(bytes) {
    const candidates = [];
    for (let pos = 0; pos < bytes.length - 1; pos++) {
      const tagByte = bytes[pos];
      // Tags de campos string en Protobuf (wireType === 2):
      // 0x0a (field 1: conversation/text), 0x12 (field 2: text), 0x1a (field 3: caption), 0x32 (field 6: caption)
      if ((tagByte & 7) !== 2) continue;
      const fieldNum = tagByte >> 3;
      if (fieldNum < 1 || fieldNum > 15) continue;

      const lenRes = readVarint(bytes, pos + 1);
      if (!lenRes || lenRes.val < 1 || lenRes.pos + lenRes.val > bytes.length) continue;

      const endPos = lenRes.pos + lenRes.val;
      // Si no termina exactamente al final del buffer, verificar que el siguiente byte parezca un tag Protobuf válido o 0
      if (endPos < bytes.length) {
        const nextByte = bytes[endPos];
        const nextWire = nextByte & 7;
        if (nextByte !== 0 && nextWire > 5) continue;
      }

      const sub = bytes.subarray(lenRes.pos, endPos);
      try {
        const str = new TextDecoder("utf-8", { fatal: true }).decode(sub);
        if (!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(str)) {
          const cleaned = str.trim();
          if (cleaned && !isGarbageString(cleaned)) {
            const bonus = (fieldNum === 1 || fieldNum === 2 || fieldNum === 3 || fieldNum === 6) ? 1000 : 0;
            const exactEndBonus = (endPos === bytes.length || bytes[endPos] === 0x9a || bytes[endPos] === 0x8a || (bytes[endPos] & 7) === 0 || (bytes[endPos] & 7) === 2) ? 200 : 0;
            candidates.push({ text: cleaned, score: cleaned.length + bonus + exactEndBonus });
          }
        }
      } catch (e) {}
    }
    return candidates;
  }

  function stripProtobufLengthPrefix(chunk) {
    if (!chunk || chunk.length < 2) return chunk;
    for (let idx = 0; idx <= Math.min(2, chunk.length - 2); idx++) {
      if (chunk[idx] === 10) {
        const lenRes = readVarint(chunk, idx + 1);
        if (lenRes && lenRes.val > 0 && Math.abs((chunk.length - lenRes.pos) - lenRes.val) <= 2) {
          return chunk.slice(lenRes.pos);
        }
      }
    }
    const firstLen = readVarint(chunk, 0);
    if (firstLen && firstLen.val > 0 && firstLen.pos < chunk.length && (chunk.length - firstLen.pos) === firstLen.val) {
      return chunk.slice(firstLen.pos);
    }
    return chunk;
  }

  function extractTextFromBuffer(buffer, isMedia = false) {
    if (!buffer) return "";
    let bytes = new Uint8Array(buffer);
    if (bytes.length === 0) return "";

    // Si buffer es un MsgRowOpaqueData con campo 1 (currentMsg), aislar el submensaje del mensaje actual
    const topFields = parseProtobufTopFields(bytes);
    if (topFields.has(1)) {
      bytes = topFields.get(1);
    }

    // 1. Primero usar el parser estructurado de Protobuf probando offsets 0..16
    for (let offset = 0; offset <= Math.min(16, bytes.length - 2); offset++) {
      const protoCandidates = extractProtobufStrings(offset === 0 ? bytes : bytes.subarray(offset), 0, isMedia);
      if (protoCandidates.length > 0) {
        protoCandidates.sort((a, b) => b.score - a.score);
        if (protoCandidates[0].score >= 1000 || offset === 0) {
          return protoCandidates[0].text;
        }
      }
    }

    // En mensajes multimedia (imagen/video/documento), no ejecutar escaneo de bytes sueltos para no capturar fragmentos ASCII de miniaturas JPEG binarias o hashes
    if (isMedia) return "";

    // 2. Escáner directo de campos length-prefixed (0x0a, 0x12, 0x1a, 0x32) en cualquier posición de cabecera
    const scannedCandidates = scanLengthPrefixedStrings(bytes);
    if (scannedCandidates.length > 0) {
      scannedCandidates.sort((a, b) => b.score - a.score);
      return scannedCandidates[0].text;
    }

    // 3. Fallback seguro reconociendo únicamente secuencias UTF-8 válidas (evita que tags Protobuf como 0x9a o 0x8a rompan TextDecoder)
    const valid = [];
    let cur = [];

    function flushCur() {
      if (cur.length >= 1) {
        try {
          const stripped = stripProtobufLengthPrefix(new Uint8Array(cur));
          let s = new TextDecoder("utf-8", { fatal: false }).decode(stripped).replace(/\ufffd/g, "").trim();
          s = s.replace(/^[a-zA-Z0-9]{1,2}\r?\n[\x20-\x7e]/, "").trim();
          if (s.length >= 1 && !isGarbageString(s)) valid.push(s);
        } catch (e) {}
      }
      cur = [];
    }

    let i = 0;
    while (i < bytes.length) {
      const b = bytes[i];
      if ((b >= 32 && b <= 126) || b === 9 || b === 10 || b === 13) {
        cur.push(b);
        i++;
      } else if (b >= 0xC2 && b <= 0xDF && i + 1 < bytes.length && (bytes[i + 1] & 0xC0) === 0x80) {
        cur.push(b, bytes[i + 1]);
        i += 2;
      } else if (b >= 0xE0 && b <= 0xEF && i + 2 < bytes.length && (bytes[i + 1] & 0xC0) === 0x80 && (bytes[i + 2] & 0xC0) === 0x80) {
        cur.push(b, bytes[i + 1], bytes[i + 2]);
        i += 3;
      } else if (b >= 0xF0 && b <= 0xF4 && i + 3 < bytes.length && (bytes[i + 1] & 0xC0) === 0x80 && (bytes[i + 2] & 0xC0) === 0x80 && (bytes[i + 3] & 0xC0) === 0x80) {
        cur.push(b, bytes[i + 1], bytes[i + 2], bytes[i + 3]);
        i += 4;
      } else {
        flushCur();
        i++;
      }
    }
    flushCur();

    if (valid.length === 0) return "";
    valid.sort((a, b) => b.length - a.length);
    return valid[0];
  }

  function extractForwardingFromBuffer(buffer) {
    if (!buffer) return { isForwarded: false, forwardingScore: 0 };
    const bytes = new Uint8Array(buffer);
    if (bytes.length < 3) return { isForwarded: false, forwardingScore: 0 };

    // 1. Escaneo rápido de firma exacta ContextInfo en WAProto:
    // field 21 (forwardingScore, tag 0xa8 0x01) + field 22 (isForwarded, tag 0xb0 0x01 0x01)
    for (let i = 0; i < bytes.length - 2; i++) {
      if (bytes[i] === 0xa8 && bytes[i + 1] === 0x01) {
        const scoreRes = readVarint(bytes, i + 2);
        if (scoreRes && scoreRes.val > 0 && scoreRes.val <= 1000) {
          const nextPos = scoreRes.pos;
          if (nextPos + 2 < bytes.length && bytes[nextPos] === 0xb0 && bytes[nextPos + 1] === 0x01 && bytes[nextPos + 2] === 0x01) {
            return { isForwarded: true, forwardingScore: scoreRes.val };
          }
        }
      }
    }

    // 2. Búsqueda estructurada recursiva en submensajes Protobuf válidos (ContextInfo)
    function checkSubmessage(sub, depth) {
      if (depth > 5 || !sub || sub.length < 3) return null;
      let pos = 0;
      let hasIsForwarded = false;
      let fwdScore = 0;
      let validFields = 0;

      while (pos < sub.length) {
        const tagRes = readVarint(sub, pos);
        if (!tagRes || tagRes.val <= 0) return null;
        pos = tagRes.pos;
        const wireType = tagRes.val & 7;
        const fieldNum = Math.floor(tagRes.val / 8);
        if (fieldNum <= 0 || fieldNum > 500) return null;

        if (wireType === 0) {
          const v = readVarint(sub, pos);
          if (!v) return null;
          pos = v.pos;
          validFields++;
          if (depth >= 1 && fieldNum === 21 && v.val > 0 && v.val <= 1000) {
            fwdScore = v.val;
          } else if (depth >= 1 && fieldNum === 22 && v.val === 1) {
            hasIsForwarded = true;
          }
        } else if (wireType === 1) {
          pos += 8;
          if (pos > sub.length) return null;
          validFields++;
        } else if (wireType === 5) {
          pos += 4;
          if (pos > sub.length) return null;
          validFields++;
        } else if (wireType === 2) {
          const lenRes = readVarint(sub, pos);
          if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > sub.length) return null;
          const child = sub.subarray(lenRes.pos, lenRes.pos + lenRes.val);
          pos = lenRes.pos + lenRes.val;
          validFields++;
          const nestedRes = checkSubmessage(child, depth + 1);
          if (nestedRes && nestedRes.isForwarded) return nestedRes;
        } else {
          return null;
        }
      }

      if (pos === sub.length && validFields > 0 && (hasIsForwarded || fwdScore > 0)) {
        return { isForwarded: true, forwardingScore: Math.max(1, fwdScore) };
      }
      return null;
    }

    for (let offset = 0; offset <= Math.min(16, bytes.length - 3); offset++) {
      const res = checkSubmessage(offset === 0 ? bytes : bytes.subarray(offset), 0);
      if (res && res.isForwarded) return res;
    }

    return { isForwarded: false, forwardingScore: 0 };
  }

  function bufferToBase64(buf) {
    if (!buf) return null;
    if (typeof buf === "string") return buf;
    const bytes = new Uint8Array(buf.buffer || buf);
    const chunkSize = 0x8000;
    const chunks = [];
    for (let i = 0; i < bytes.byteLength; i += chunkSize) {
      chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize)));
    }
    return window.btoa(chunks.join(""));
  }

  function extractId(val) {
    if (!val) return null;
    if (typeof val === "object") return val._serialized || val.id || String(val);
    return String(val);
  }

  // =========================================================================
  // 3. COMUNICACIÓN CON EL BACKEND (Inmune a CSP)
  // =========================================================================
  function backendFetch(endpoint, options = {}) {
    return new Promise((resolve, reject) => {
      const reqId = "req_" + Math.random().toString(36).substring(2, 11) + "_" + Date.now();

      function messageHandler(event) {
        if (event.source !== window || event.origin !== window.location.origin) return;
        if (!event.data || event.data.type !== "BACKUPWA_RESP") return;
        if (event.data.reqId !== reqId) return;

        window.removeEventListener("message", messageHandler);
        clearTimeout(timeoutId);

        if (event.data.error) {
          return reject(new Error(event.data.error));
        }

        const res = event.data.response;
        if (!res || !res.ok) {
          return reject(new Error((res && res.error) || `Error HTTP ${res ? res.status : 'desconocido'}`));
        }

        resolve(res.data);
      }

      window.addEventListener("message", messageHandler);

      window.postMessage({
        type: "BACKUPWA_REQ",
        reqId: reqId,
        endpoint: endpoint,
        options: options
      }, window.location.origin);

      const timeoutId = setTimeout(() => {
        window.removeEventListener("message", messageHandler);
        reject(new Error("Tiempo de espera agotado al conectar con el servidor local"));
      }, 45000);
    });
  }

  // =========================================================================
  // 4. DETECCIÓN MULTI-CUENTA Y SINCRONIZACIÓN
  // =========================================================================
  let lastDetectedAccountId = null;

  function getActiveAccountInfo() {
    let rawWid = "";
    try {
      rawWid = window.localStorage.getItem("last-wid-md") || window.localStorage.getItem("last-wid") || "";
      if (typeof rawWid === "string" && rawWid.startsWith('"') && rawWid.endsWith('"')) {
        rawWid = JSON.parse(rawWid);
      }
    } catch (e) {}

    let phone = "";
    if (rawWid) {
      const parts = String(rawWid).split("@")[0].split(":");
      const digits = parts[0].replace(/\D/g, "");
      if (digits.length >= 7) {
        phone = digits;
      }
    }

    let name = "";
    try {
      name = window.localStorage.getItem("me-display-name") || window.localStorage.getItem("pushname") || "";
      if (typeof name === "string" && name.startsWith('"') && name.endsWith('"')) {
        name = JSON.parse(name);
      }
    } catch (e) {}

    const accountId = phone || (rawWid ? String(rawWid).split("@")[0].replace(/[^a-zA-Z0-9_\-]/g, "") : "default");

    return {
      id: accountId,
      phone: phone,
      name: name || (phone ? `+${phone}` : "Cuenta WhatsApp"),
      wid: rawWid
    };
  }

  function updateWidgetAccountBadge(accountInfo) {
    const badge = document.getElementById("backupwa-account-badge");
    if (!badge || !accountInfo) return;
    const label = accountInfo.phone ? `+${accountInfo.phone}` : (accountInfo.name || "");
    if (label && label !== "default") {
      badge.textContent = label;
      badge.title = `Cuenta activa en este WhatsApp Web: ${accountInfo.name || label} (${accountInfo.id})`;
      badge.style.display = "inline-block";
    } else {
      badge.style.display = "none";
    }
  }

  async function triggerSync(isAuto = false, forceAll = false) {
    if (isSyncing) return;
    if (verifiedKeys.length === 0 && candidateKeys.length === 0) {
      await scanIndexedDBForCryptoKeys();
    }
    if (verifiedKeys.length === 0 && candidateKeys.length === 0) {
      updateWidgetStatus("waiting", "Abre un chat para activar");
      return;
    }

    let accountInfo = getActiveAccountInfo();
    if (lastDetectedAccountId && lastDetectedAccountId !== accountInfo.id) {
      console.log(`[WAckup] Cambio de cuenta WhatsApp Web detectado: ${lastDetectedAccountId} -> ${accountInfo.id}. Reiniciando caché de IDs.`);
      knownMessageKeys = null;
    }
    lastDetectedAccountId = accountInfo.id;
    updateWidgetAccountBadge(accountInfo);

    isSyncing = true;
    updateWidgetStatus("syncing", forceAll ? "Sincronizando todo..." : "Sincronizando...");

    try {
      let lastTimestamp = 0;

      try {
        const needIds = knownMessageKeys === null ? "&include_ids=1" : "";
        const statusData = await backendFetch(`/api/sync/status?account_id=${encodeURIComponent(accountInfo.id)}${needIds}`);
        lastTimestamp = statusData.last_timestamp || 0;
        if (knownMessageKeys === null && Array.isArray(statusData.synced_msg_ids)) {
          knownMessageKeys = new Set(statusData.synced_msg_ids);
        }
        if (statusData && statusData.extension_version && statusData.installed_extension_version) {
          checkExtensionUpdate(statusData.extension_version, statusData.installed_extension_version);
        }
      } catch (backendErr) {
        console.warn("[WAckup] Servidor local no disponible:", backendErr);
        updateWidgetStatus("error", "Servidor local desconectado");
        isSyncing = false;
        return;
      }

      console.log(`[WAckup] Sincronización iniciada [Cuenta: ${accountInfo.id}] (forceAll=${forceAll}). Último timestamp local: ${lastTimestamp}`);

      const db = await openIndexedDB("model-storage");
      if (!db) {
        updateWidgetStatus("error", "Error al leer IndexedDB");
        isSyncing = false;
        return;
      }

      // Extraer contactos y chats
      const rawContacts = await getAllFromStore(db, "contact");
      const rawChats = await getAllFromStore(db, "chat");

      const meContact = rawContacts.find(c => c.isMe || c.isUser || (accountInfo.wid && c.id === accountInfo.wid));
      if (meContact) {
        if (meContact.name || meContact.pushname || meContact.formattedName) {
          accountInfo.name = meContact.name || meContact.formattedName || meContact.pushname || accountInfo.name;
        }
        const rawNum = meContact.phoneNumber || meContact.pn || meContact.phoneNumberJid || meContact.number;
        if (rawNum) {
          const cleanNum = String(rawNum).split("@")[0].replace(/\D/g, "");
          if (cleanNum.length >= 7) {
            accountInfo.phone = cleanNum;
            accountInfo.id = cleanNum;
          }
        }
        updateWidgetAccountBadge(accountInfo);
      }

      const cleanContacts = rawContacts.map(c => {
        const cid = extractId(c.id);
        const rawPhone = extractId(c.phoneNumber || c.pn || c.phoneNumberJid || c.number);
        const phoneDigits = rawPhone ? String(rawPhone).split("@")[0].replace(/\D/g, "") : null;
        const cidPrefix = cid ? String(cid).split("@")[0] : "";
        const validPhone = (phoneDigits && (!String(cid || "").endsWith("@lid") || phoneDigits !== cidPrefix)) ? phoneDigits : null;
        return {
          id: cid,
          name: c.name || c.formattedName || c.verifiedName || null,
          pushname: c.pushname || null,
          number: validPhone
        };
      });

      const cleanChats = rawChats.map(ch => ({
        id: extractId(ch.id),
        name: ch.name || ch.formattedTitle || null,
        unreadCount: ch.unreadCount || 0,
        t: ch.t || ch.timestamp || null
      }));

      // Determinar si contactos o chats han cambiado respecto al último envío
      const contactsHash = fastHash(JSON.stringify(cleanContacts));
      const chatsHash = fastHash(JSON.stringify(cleanChats));
      const now = Date.now();
      const contactsExpired = (now - lastContactsSyncTime) > 1800000; // Revalidar contactos cada 30 min

      const shouldSendContacts = forceAll || contactsExpired || (contactsHash !== lastContactsHash);
      const shouldSendChats = forceAll || (chatsHash !== lastChatsHash);

      const contactsToSend = shouldSendContacts ? cleanContacts : [];
      const chatsToSend = shouldSendChats ? cleanChats : [];

      // Extraer mensajes de forma segura y optimizada
      const minTimestamp = forceAll ? 0 : Math.max(0, lastTimestamp - 3600);
      const rawMessages = await getMessagesSince(db, minTimestamp, forceAll);
      db.close();

      console.log(`[WAckup] Mensajes candidatos a procesar: ${rawMessages.length}`);

      if (rawMessages.length === 0) {
        if (!shouldSendContacts && !shouldSendChats) {
          const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          updateWidgetStatus("synced", `Al día (${nowStr})`);
          return;
        }

        await backendFetch("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: accountInfo.id,
            account_name: accountInfo.name,
            account_phone: accountInfo.phone,
            contacts: contactsToSend,
            chats: chatsToSend,
            messages: []
          })
        });
        lastContactsHash = contactsHash;
        lastChatsHash = chatsHash;
        lastContactsSyncTime = now;
        const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        console.log("%c[WAckup] Contactos y chats sincronizados.", "color: #10b981; font-weight: bold;");
        updateWidgetStatus("synced", `Al día (${nowStr})`);
        return;
      }

      // Mapa opcional de textos en memoria de WhatsApp Web (si los modelos ya están cargados en RAM)
      const inMemoryMap = getInMemoryMessagesMap();

      // Descifrado concurrente por lotes para velocidad óptima
      const cleanMessages = [];
      for (let i = 0; i < rawMessages.length; i += DECRYPT_CHUNK_SIZE) {
        const chunk = rawMessages.slice(i, i + DECRYPT_CHUNK_SIZE);
        const processedChunk = await Promise.all(chunk.map(async (m) => {
          const msgId = extractId(m.id);
          // Ignorar contenedores virtuales de álbumes (agrupadores sin mensaje propio)
          if (m.type === "album") {
            if (msgId && knownMessageKeys !== null) knownMessageKeys.add(msgId);
            return null;
          }
          const isMedia = m.type in { image: 1, video: 1, sticker: 1, ptt: 1, audio: 1, document: 1 };
          const memEntry = msgId ? inMemoryMap.get(msgId) : null;

          const caption = m.caption || (memEntry && memEntry.caption) || "";
          const rawBody = (typeof m.body === "string" && m.body) ? m.body : ((memEntry && memEntry.body) || "");

          const isBinaryOrB64 = (s) => {
            if (!s) return false;
            if (s.startsWith('data:image') || s.includes('/9j/')) return true;
            if (s.length > 40 && !/\s/.test(s) && /^[A-Za-z0-9+/=_\-\*\&\"\'\(\)\%\;\:\$\^]+$/.test(s)) return true;
            return false;
          };

          let bodyText = "";
          if (isMedia) {
            if (caption && !isBinaryOrB64(caption)) {
              bodyText = caption;
            } else if (rawBody && !isBinaryOrB64(rawBody)) {
              bodyText = rawBody;
            }
          } else if (m.type === "revoked") {
            bodyText = "🚫 Este mensaje fue eliminado";
          } else {
            bodyText = isBinaryOrB64(rawBody) ? "" : (rawBody || caption);
          }

          // Estado de reenviado desde propiedades directas o modelo en memoria
          const ctxInfo = m.msgContextInfo || m.contextInfo || {};
          let forwardingScore = Number(m.forwardingScore) || Number(ctxInfo.forwardingScore) || (memEntry && memEntry.forwardingScore) || 0;
          let isForwarded = Boolean(
            m.isForwarded ||
            ctxInfo.isForwarded ||
            (memEntry && memEntry.isForwarded) ||
            forwardingScore > 0
          );

          // Si falta texto o no hay marca explícita de reenviado y existe msgRowOpaqueData, descifrar e inspeccionar Protobuf
          const canHaveEncryptedText = !isMedia || (m.type in { image: 1, video: 1, document: 1 });
          let quotedInfoFromOpaque = null;
          if (m.msgRowOpaqueData) {
            const decBuf = await decryptOpaqueData(m.msgRowOpaqueData);
            if (decBuf) {
              const opaqueBytes = new Uint8Array(decBuf);
              const topFields = parseProtobufTopFields(opaqueBytes);
              // MsgRowOpaqueData: Campo 1 = currentMsg (mensaje actual), Campo 2 = quotedMsg (mensaje citado)
              const currentMsgBytes = topFields.get(1) || opaqueBytes;
              const quotedMsgBytes = topFields.get(2) || null;

              if (!bodyText && canHaveEncryptedText) {
                const decoded = extractTextFromBuffer(currentMsgBytes, isMedia);
                if (decoded && !isBinaryOrB64(decoded)) {
                  bodyText = decoded;
                }
              }
              if (!isForwarded) {
                const fwdInfo = extractForwardingFromBuffer(currentMsgBytes);
                if (fwdInfo.isForwarded) {
                  isForwarded = true;
                  forwardingScore = Math.max(forwardingScore, fwdInfo.forwardingScore || 1);
                }
              }
              quotedInfoFromOpaque = extractContextInfoFromBuffer(decBuf);
              if (quotedMsgBytes && (!quotedInfoFromOpaque || !quotedInfoFromOpaque.quotedBody)) {
                const qBody = extractTextFromBuffer(quotedMsgBytes, false);
                if (qBody) {
                  if (!quotedInfoFromOpaque) quotedInfoFromOpaque = {};
                  quotedInfoFromOpaque.quotedBody = qBody;
                }
              }
            }
          }
          if (isForwarded && forwardingScore < 1) {
            forwardingScore = 1;
          }

          if (bodyText) {
            bodyText = bodyText.replace(/\ufffd/g, "").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").trim();
            if (m.type in { sticker: 1, ptt: 1, audio: 1 } && bodyText.length <= 15 && !bodyText.includes(" ")) {
              bodyText = "";
            }
          }

          const mediaKeyB64 = m.mediaKey ? bufferToBase64(m.mediaKey) : null;
          let thumbnailStr = null;
          if (rawBody && (rawBody.startsWith('data:image') || rawBody.includes('/9j/'))) {
            const idx = rawBody.indexOf('/9j/');
            const b64Clean = idx !== -1 ? rawBody.substring(idx).trim() : rawBody;
            thumbnailStr = b64Clean.startsWith('data:image') ? b64Clean : 'data:image/jpeg;base64,' + b64Clean;
          } else if (m.thumbnail) {
            const tB64 = bufferToBase64(m.thumbnail);
            if (tB64) thumbnailStr = tB64.startsWith('data:image') ? tB64 : 'data:image/jpeg;base64,' + tB64;
          }

          const qMsg = m.quotedMsg || {};
          const quotedMsgId = extractId(m.quotedStanzaID || qMsg.id || (quotedInfoFromOpaque && quotedInfoFromOpaque.stanzaId));
          const quotedAuthor = extractId(m.quotedParticipant || qMsg.author || qMsg.from || (quotedInfoFromOpaque && quotedInfoFromOpaque.participant));
          let quotedBody = qMsg.caption || (typeof qMsg.body === "string" && !isBinaryOrB64(qMsg.body) ? qMsg.body : "") || (quotedInfoFromOpaque && quotedInfoFromOpaque.quotedBody) || "";

          return {
            id: msgId,
            chatId: extractId(m.chatId),
            from: extractId(m.from),
            to: extractId(m.to),
            author: extractId(m.author),
            fromMe: Boolean(m.fromMe || (m.id && m.id.fromMe)),
            type: m.type || "chat",
            timestamp: m.t || m.timestamp || null,
            body: bodyText,
            mediaKey: mediaKeyB64,
            directPath: m.directPath || null,
            mimetype: m.mimetype || null,
            size: m.size || m.fileLength || null,
            duration: m.duration || null,
            filename: m.filename || null,
            thumbnail: thumbnailStr,
            encFilehash: m.encFilehash ? bufferToBase64(m.encFilehash) : null,
            filehash: m.filehash ? bufferToBase64(m.filehash) : null,
            quotedMsgId: quotedMsgId,
            quotedAuthor: quotedAuthor,
            quotedBody: quotedBody,
            isForwarded: isForwarded,
            forwardingScore: forwardingScore
          };
        }));

        for (const item of processedChunk) {
          if (!item || !item.id) continue;
          const isTextType = item.type === "chat" || item.type === "album" || item.type === "unknown";
          const hasValidContent = Boolean(item.body) || Boolean(item.quotedBody) || Boolean(item.mediaKey && item.directPath);
          // Solo marcar como conocido y enviar si tiene contenido válido o es de tipo multimedia/especial
          if ( isTextType && !hasValidContent ) {
            continue;
          }
          cleanMessages.push(item);
        }

        if (rawMessages.length > 500 && i % 1000 === 0) {
          updateWidgetStatus("syncing", `Procesando ${i}/${rawMessages.length}...`);
        }
      }

      if (cleanMessages.length === 0) {
        if (!shouldSendContacts && !shouldSendChats) {
          const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          updateWidgetStatus("synced", `Al día (${nowStr})`);
          return;
        }

        await backendFetch("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: accountInfo.id,
            account_name: accountInfo.name,
            account_phone: accountInfo.phone,
            contacts: contactsToSend,
            chats: chatsToSend,
            messages: []
          })
        });
        lastContactsHash = contactsHash;
        lastChatsHash = chatsHash;
        lastContactsSyncTime = now;
        const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        updateWidgetStatus("synced", `Al día (${nowStr})`);
        return;
      }

      // Enviar hacia el backend local en lotes
      let totalSaved = 0;
      let totalMediaEnqueued = 0;

      for (let i = 0; i < cleanMessages.length; i += BATCH_SIZE) {
        const batch = cleanMessages.slice(i, i + BATCH_SIZE);
        const payload = {
          account_id: accountInfo.id,
          account_name: accountInfo.name,
          account_phone: accountInfo.phone,
          contacts: i === 0 ? contactsToSend : [],
          chats: i === 0 ? chatsToSend : [],
          messages: batch
        };

        const resJson = await backendFetch("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        if (knownMessageKeys !== null) {
          for (const m of batch) {
            if (m && m.id) knownMessageKeys.add(m.id);
          }
        }

        if (resJson && resJson.stats) {
          totalSaved += resJson.stats.messages_saved || 0;
          totalMediaEnqueued += resJson.stats.media_enqueued || 0;
        } else {
          totalSaved += batch.length;
        }

        if (cleanMessages.length > BATCH_SIZE) {
          updateWidgetStatus("syncing", `Guardando ${Math.min(i + BATCH_SIZE, cleanMessages.length)}/${cleanMessages.length}...`);
        }
      }

      lastContactsHash = contactsHash;
      lastChatsHash = chatsHash;
      lastContactsSyncTime = now;

      const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      console.log(`%c[WAckup] Sincronización exitosa: ${totalSaved} mensajes guardados, ${totalMediaEnqueued} multimedia en cola.`, "color: #10b981; font-weight: bold; font-size: 13px;");
      updateWidgetStatus("synced", `Sincronizado ${nowStr} (${totalSaved} msgs)`);

    } catch (err) {
      console.error("[WAckup] Error durante sincronización:", err);
      updateWidgetStatus("error", "Error de sincronización");
    } finally {
      isSyncing = false;
    }
  }

  // =========================================================================
  // 5. ACCESO A INDEXEDDB (Robusto y optimizado con índices/cursores)
  // =========================================================================
  function openIndexedDB(dbName) {
    return new Promise((resolve) => {
      try {
        const req = window.indexedDB.open(dbName);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
      } catch (e) {
        resolve(null);
      }
    });
  }

  function getAllFromStore(db, storeName) {
    return new Promise((resolve) => {
      try {
        if (!db.objectStoreNames.contains(storeName)) return resolve([]);
        const tx = db.transaction(storeName, "readonly");
        const store = tx.objectStore(storeName);
        const req = store.getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => resolve([]);
      } catch (e) {
        resolve([]);
      }
    });
  }

  function getStoreCount(db, storeName) {
    return new Promise((resolve) => {
      try {
        if (!db.objectStoreNames.contains(storeName)) return resolve(0);
        const tx = db.transaction(storeName, "readonly");
        const store = tx.objectStore(storeName);
        const req = store.count();
        req.onsuccess = () => resolve(req.result || 0);
        req.onerror = () => resolve(0);
      } catch (e) {
        resolve(0);
      }
    });
  }

  function getAllKeysFromStore(db, storeName) {
    return new Promise((resolve) => {
      try {
        if (!db.objectStoreNames.contains(storeName)) return resolve([]);
        const tx = db.transaction(storeName, "readonly");
        const store = tx.objectStore(storeName);
        const req = store.getAllKeys();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => resolve([]);
      } catch (e) {
        resolve([]);
      }
    });
  }

  function getRecordsByKeys(db, storeName, keys) {
    if (!keys || keys.length === 0) return Promise.resolve([]);
    return new Promise((resolve) => {
      try {
        if (!db.objectStoreNames.contains(storeName)) return resolve([]);
        const tx = db.transaction(storeName, "readonly");
        const store = tx.objectStore(storeName);
        const results = [];
        let pending = keys.length;
        for (const key of keys) {
          const req = store.get(key);
          req.onsuccess = () => {
            if (req.result) results.push(req.result);
            if (--pending === 0) resolve(results);
          };
          req.onerror = () => {
            if (--pending === 0) resolve(results);
          };
        }
      } catch (e) {
        resolve([]);
      }
    });
  }

  function getRecentMessagesOnly(db, minTimestamp) {
    return new Promise((resolve) => {
      try {
        if (!db.objectStoreNames.contains("message")) return resolve([]);
        const tx = db.transaction("message", "readonly");
        const store = tx.objectStore("message");

        if (store.indexNames && store.indexNames.contains("t")) {
          const idx = store.index("t");
          const range = IDBKeyRange.lowerBound(minTimestamp);
          const req = idx.getAll(range);
          req.onsuccess = () => resolve(req.result || []);
          req.onerror = () => getAllFromStore(db, "message").then(all => resolve(all.filter(m => (m.t || m.timestamp || 0) >= minTimestamp)));
          return;
        }

        const results = [];
        const curReq = store.openCursor();
        curReq.onsuccess = (e) => {
          const cursor = e.target.result;
          if (cursor) {
            const val = cursor.value;
            if ((val.t || val.timestamp || 0) >= minTimestamp) {
              results.push(val);
            }
            cursor.continue();
          } else {
            resolve(results);
          }
        };
        curReq.onerror = () => resolve([]);
      } catch (e) {
        resolve([]);
      }
    });
  }

  async function getMessagesSince(db, minTimestamp, forceAll = false) {
    if (forceAll || !minTimestamp || minTimestamp <= 0) {
      return getAllFromStore(db, "message");
    }

    const recentMsgs = await getRecentMessagesOnly(db, minTimestamp);
    const allKeys = await getAllKeysFromStore(db, "message");

    let scrolledMsgs = [];
    if (knownMessageKeys === null) {
      knownMessageKeys = new Set();
    }

    const newKeys = allKeys.filter(k => !knownMessageKeys.has(extractId(k)));
    if (newKeys.length > 0) {
      console.log(`[WAckup] Detectados ${newKeys.length} mensajes en IndexedDB pendientes de respaldo.`);
      scrolledMsgs = await getRecordsByKeys(db, "message", newKeys);
    }

    if (scrolledMsgs.length === 0) return recentMsgs;

    const mergedMap = new Map();
    for (const m of recentMsgs) {
      const id = extractId(m.id);
      if (id) mergedMap.set(id, m);
    }
    for (const m of scrolledMsgs) {
      const id = extractId(m.id);
      if (id) mergedMap.set(id, m);
    }
    return Array.from(mergedMap.values());
  }

  // =========================================================================
  // 6. WIDGET VISUAL DISCRETO EN WHATSAPP WEB
  // =========================================================================
  function createWidget() {
    if (document.getElementById("backupwa-widget")) return;

    const widget = document.createElement("div");
    widget.id = "backupwa-widget";
    widget.style.cssText = `
      position: fixed;
      bottom: 16px;
      left: 16px;
      z-index: 999999;
      background: #ffffff;
      border: 1px solid #d1d7db;
      border-radius: 20px;
      padding: 5px 12px;
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 2px 10px rgba(11,20,26,0.18);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 12px;
      color: #111b21;
      user-select: none;
      transition: all 0.2s ease;
    `;

    widget.innerHTML = `
      <span style="font-weight: 700; color: #00a884; font-size: 12px; letter-spacing: 0.2px;">WAckup</span>
      <span id="backupwa-account-badge" style="display: none; background: #e0f2fe; color: #0369a1; padding: 2px 7px; border-radius: 10px; font-size: 11px; font-weight: 600;"></span>
      <div id="backupwa-dot" style="width: 8px; height: 8px; border-radius: 50%; background: #eab308; flex-shrink: 0;"></div>
      <span id="backupwa-text" style="font-weight: 500;">Iniciando respaldo...</span>
      <button id="backupwa-btn-update" title="Nueva versión disponible. Haz clic para recargar la extensión y WhatsApp Web en 1 clic." style="display: none; border: none; background: #2563eb; color: #fff; border-radius: 12px; padding: 3px 9px; font-size: 11px; font-weight: 600; cursor: pointer;">🔄 Actualizar Extensión</button>
      <button id="backupwa-btn-sync" title="Sincronizar mensajes nuevos o recientes" style="border: none; background: #00a884; color: #fff; border-radius: 12px; padding: 3px 9px; font-size: 11px; font-weight: 600; cursor: pointer;">Novedades</button>
      <button id="backupwa-btn-all" title="Sincronizar todos los mensajes para vincular archivos multimedia (fotos, audios, docs)" style="border: 1px solid #00a884; background: #ffffff; color: #00a884; border-radius: 12px; padding: 2px 8px; font-size: 11px; font-weight: 600; cursor: pointer;">Todo (Media)</button>
    `;

    document.body.appendChild(widget);
    updateWidgetAccountBadge(getActiveAccountInfo());

    const btnUpdate = document.getElementById("backupwa-btn-update");
    if (btnUpdate) {
      btnUpdate.onclick = (e) => {
        e.stopPropagation();
        btnUpdate.disabled = true;
        btnUpdate.textContent = "Recargando...";
        updateWidgetStatus("syncing", "Actualizando extensión...");
        window.postMessage({ type: "BACKUPWA_RELOAD_EXT" }, "https://web.whatsapp.com");
        setTimeout(() => {
          window.location.reload();
        }, 1200);
      };
    }

    document.getElementById("backupwa-btn-sync").onclick = (e) => {
      e.stopPropagation();
      knownMessageKeys = null;
      triggerSync(false, false);
    };

    document.getElementById("backupwa-btn-all").onclick = (e) => {
      e.stopPropagation();
      knownMessageKeys = null;
      triggerSync(false, true);
    };
  }

  function checkExtensionUpdate(diskVer, installedVer) {
    const btnUpdate = document.getElementById("backupwa-btn-update");
    if (!btnUpdate) return;
    if (diskVer && installedVer && diskVer !== installedVer) {
      btnUpdate.style.display = "inline-block";
      btnUpdate.textContent = `🔄 Actualizar v${diskVer}`;
      btnUpdate.title = `Versión activa: v${installedVer} | Versión en disco: v${diskVer}. Haz clic para recargar la extensión y WhatsApp Web en 1 clic.`;
    } else {
      btnUpdate.style.display = "none";
    }
  }

  function updateWidgetStatus(state, message) {
    const dot = document.getElementById("backupwa-dot");
    const text = document.getElementById("backupwa-text");
    if (!dot || !text) return;

    text.textContent = message;

    if (state === "ready" || state === "synced") {
      dot.style.background = "#25d366";
    } else if (state === "syncing") {
      dot.style.background = "#3b82f6";
    } else if (state === "waiting") {
      dot.style.background = "#eab308";
    } else if (state === "error") {
      dot.style.background = "#ef4444";
    }
  }

  // =========================================================================
  // 7. INICIALIZACIÓN
  // =========================================================================
  let scrollSyncTimer = null;

  function init() {
    setupCryptoInterceptor();

    if (document.body) {
      createWidget();
    } else {
      window.addEventListener("DOMContentLoaded", createWidget);
    }

    // Al hacer scroll en un chat de WhatsApp Web, sincronizar automáticamente los mensajes cargados 2.5s después
    window.addEventListener("scroll", () => {
      if ((verifiedKeys.length > 0 || candidateKeys.length > 0) && !isSyncing) {
        clearTimeout(scrollSyncTimer);
        scrollSyncTimer = setTimeout(() => {
          triggerSync(true, false);
        }, 2500);
      }
    }, { capture: true, passive: true });

    if (autoSyncTimer) clearInterval(autoSyncTimer);
    autoSyncTimer = setInterval(() => {
      if (verifiedKeys.length > 0 || candidateKeys.length > 0) {
        triggerSync(true, false);
      } else {
        scanIndexedDBForCryptoKeys();
      }
    }, AUTO_SYNC_INTERVAL_MS);
  }

  init();

})();
