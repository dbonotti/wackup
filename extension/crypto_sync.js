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
        console.log(`%c🎯 [WAckup] Clave de base de datos verificada (${verifiedKeys.length} clave(s) activa(s))`, "color: #10b981; font-weight: bold; font-size: 14px;");
        notifyStatus("ready", "Clave activa");
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
          return decBuf;
        }
      } catch (e) {}
    }

    return null;
  }

  

  // =========================================================================
  // 2. DECODIFICADOR PROTOBUF ESTRUCTURAL DETERMINISTA (WAWebProtobufsWeb + WAProto)
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

  function isBase64MediaBlob(s) {
    if (!s || typeof s !== "string") return false;
    const t = s.trim();
    if (!t) return false;
    if (
      t.startsWith("data:image") ||
      t.startsWith("/9j/") ||
      t.startsWith("iVBORw0KGgo") ||
      t.startsWith("UklGR")
    ) {
      return true;
    }
    const jpgIdx = t.indexOf("/9j/");
    if (jpgIdx !== -1 && t.length - jpgIdx > 60 && !/\s/.test(t.substring(jpgIdx, jpgIdx + 60))) {
      return true;
    }
    // Blobs Base64 puros largos de una sola línea (descarta números puros como CBU/códigos de barras y URLs)
    if (t.length > 80 && !/\s/.test(t) && !/^\d+$/.test(t) && !/^https?:\/\//i.test(t) && /^[A-Za-z0-9+/=]+$/.test(t)) {
      return true;
    }
    return false;
  }

  function isInternalJidOrUrlNoise(s) {
    if (!s) return true;
    const t = s.trim();
    if (!t) return true;
    if (/^[0-9a-zA-Z._+-]+@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast|newsletter)$/i.test(t)) return true;
    if (/@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast)\b/i.test(t) && !/\s/.test(t)) return true;
    if (/^(image|video|audio|application)\/[a-z0-9.+-]+(;.*)?$/i.test(t)) return true;
    if ((t.startsWith("https://mmg.whatsapp.net") || t.startsWith("/v/t62.") || t.startsWith("/mms/")) && !/\s/.test(t)) return true;
    return false;
  }

  /**
   * Parsea de forma estricta un mensaje Protobuf completo.
   * Exige que el 100% del buffer se consuma sin bytes sobrantes ni wireTypes inválidos.
   * Soporta campos repetidos: Map<fieldNum, Array<{ wireType, value }>>.
   */
  function parseProtobufFields(bytes) {
    if (!bytes || bytes.length === 0) return null;
    if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return null;

    const fields = new Map();
    let pos = 0;

    while (pos < bytes.length) {
      const tagRes = readVarint(bytes, pos);
      if (!tagRes || tagRes.val <= 0) return null;
      pos = tagRes.pos;

      const wireType = tagRes.val & 7;
      const fieldNum = Math.floor(tagRes.val / 8);
      if (fieldNum <= 0 || fieldNum > 50000) return null;

      let entryVal = null;
      if (wireType === 0) {
        const v = readVarint(bytes, pos);
        if (!v) return null;
        pos = v.pos;
        entryVal = v.val;
      } else if (wireType === 1) {
        if (pos + 8 > bytes.length) return null;
        entryVal = bytes.subarray(pos, pos + 8);
        pos += 8;
      } else if (wireType === 5) {
        if (pos + 4 > bytes.length) return null;
        entryVal = bytes.subarray(pos, pos + 4);
        pos += 4;
      } else if (wireType === 2) {
        const lenRes = readVarint(bytes, pos);
        if (!lenRes || lenRes.val < 0 || lenRes.pos + lenRes.val > bytes.length) return null;
        entryVal = bytes.subarray(lenRes.pos, lenRes.pos + lenRes.val);
        pos = lenRes.pos + lenRes.val;
      } else {
        return null;
      }

      let list = fields.get(fieldNum);
      if (!list) {
        list = [];
        fields.set(fieldNum, list);
      }
      list.push({ wireType, value: entryVal });
    }

    if (pos !== bytes.length) return null;
    return fields;
  }

  function getProtoBytes(fields, fieldNum) {
    if (!fields) return null;
    const list = fields.get(fieldNum);
    if (!list) return null;
    for (let i = 0; i < list.length; i++) {
      if (list[i].wireType === 2 && list[i].value) return list[i].value;
    }
    return null;
  }

  function getAllProtoBytes(fields, fieldNum) {
    const out = [];
    if (!fields) return out;
    const list = fields.get(fieldNum);
    if (!list) return out;
    for (let i = 0; i < list.length; i++) {
      if (list[i].wireType === 2 && list[i].value) out.push(list[i].value);
    }
    return out;
  }

  function getProtoVarint(fields, fieldNum) {
    if (!fields) return null;
    const list = fields.get(fieldNum);
    if (!list) return null;
    for (let i = 0; i < list.length; i++) {
      if (list[i].wireType === 0) return list[i].value;
    }
    return null;
  }

  /**
   * Decodifica un campo leaf `string` de Protobuf como UTF-8 estricto íntegro,
   * sin heurísticas de recorte ni escaneo de subcadenas.
   */
  function decodeProtoStringLeaf(sub, rejectMediaBlob = true) {
    if (!sub || sub.length === 0) return "";
    if (sub.length >= 3 && sub[0] === 0xff && sub[1] === 0xd8 && sub[2] === 0xff) return "";
    try {
      let str = new TextDecoder("utf-8", { fatal: true }).decode(sub);
      // Si tiene bytes NUL es un buffer binario, no un string de usuario
      if (str.indexOf("\x00") !== -1) return "";
      // Normalizar saltos de línea suaves (Shift+Enter de Word/Docs: \x0b, \x0c) y limpiar controles no imprimibles residuales
      str = str.replace(/[\x0b\x0c]/g, "\n").replace(/[\x01-\x08\x0e-\x1f\x7f]/g, "").trim();
      if (!str) return "";
      if (rejectMediaBlob && isBase64MediaBlob(str)) return "";
      if (isInternalJidOrUrlNoise(str)) return "";
      return str;
    } catch (e) {
      return "";
    }
  }

  /**
   * Extrae texto de un WAProto.Message real (usado en MsgOpaqueData.futureproofBuffer = 14
   * y en ContextInfo.quotedMessage = 3).
   */
  function extractTextFromWAProtoMessage(protoBytes, isMedia = false, depth = 0) {
    if (depth > 5 || !protoBytes || protoBytes.length === 0) return "";
    const fields = parseProtobufFields(protoBytes);
    if (!fields) return "";

    // 1. Desenvolver contenedores FutureProofMessage (ephemeral=37, viewOnce=40, docWithCaption=51, viewOnceV2=55, edited=58, viewOnceV2Ext=59)
    const wrapperFields = [37, 40, 51, 55, 58, 59];
    for (const wf of wrapperFields) {
      const wrapSub = getProtoBytes(fields, wf);
      if (wrapSub) {
        const wrapParsed = parseProtobufFields(wrapSub);
        const innerMsg = getProtoBytes(wrapParsed, 1) || wrapSub;
        const innerText = extractTextFromWAProtoMessage(innerMsg, isMedia, depth + 1);
        if (innerText) return innerText;
      }
    }

    // 2. conversation (campo 1: string directo en mensajes de texto simples)
    if (!isMedia) {
      const conv = decodeProtoStringLeaf(getProtoBytes(fields, 1));
      if (conv) return conv;
    }

    // 3. extendedTextMessage (campo 6 -> campo 1: text)
    const extBytes = getProtoBytes(fields, 6);
    if (extBytes && !isMedia) {
      const extFields = parseProtobufFields(extBytes);
      if (extFields) {
        const extText = decodeProtoStringLeaf(getProtoBytes(extFields, 1)) ||
                        decodeProtoStringLeaf(getProtoBytes(extFields, 6)) ||
                        decodeProtoStringLeaf(getProtoBytes(extFields, 5));
        if (extText) return extText;
      }
    }

    // 4. Mensajes multimedia en WAProto.Message
    // imageMessage (campo 3 -> campo 3: caption)
    const imgBytes = getProtoBytes(fields, 3);
    if (imgBytes) {
      const imgFields = parseProtobufFields(imgBytes);
      const cap = decodeProtoStringLeaf(getProtoBytes(imgFields, 3));
      if (cap) return cap;
    }

    // videoMessage (campo 9 -> campo 7: caption)
    const vidBytes = getProtoBytes(fields, 9);
    if (vidBytes) {
      const vidFields = parseProtobufFields(vidBytes);
      const cap = decodeProtoStringLeaf(getProtoBytes(vidFields, 7));
      if (cap) return cap;
    }

    // documentMessage (campo 7 -> campo 20: caption, fallback campo 3: title / campo 8: fileName)
    const docBytes = getProtoBytes(fields, 7);
    if (docBytes) {
      const docFields = parseProtobufFields(docBytes);
      if (docFields) {
        const cap = decodeProtoStringLeaf(getProtoBytes(docFields, 20));
        if (cap) return cap;
        if (!isMedia) {
          const docTitle = decodeProtoStringLeaf(getProtoBytes(docFields, 3)) ||
                           decodeProtoStringLeaf(getProtoBytes(docFields, 8));
          if (docTitle) return docTitle;
        }
      }
    }

    if (isMedia) return "";

    // 5. contactMessage (campo 4 -> campo 16: vcard, campo 1: displayName)
    const contactBytes = getProtoBytes(fields, 4);
    if (contactBytes) {
      const cFields = parseProtobufFields(contactBytes);
      const vc = decodeProtoStringLeaf(getProtoBytes(cFields, 16)) ||
                 decodeProtoStringLeaf(getProtoBytes(cFields, 1));
      if (vc) return vc;
    }

    // 6. locationMessage (campo 5 -> campo 3: name, campo 4: address, campo 11: comment) / liveLocationMessage (campo 18 -> campo 3: caption)
    const locBytes = getProtoBytes(fields, 5);
    if (locBytes) {
      const lFields = parseProtobufFields(locBytes);
      if (lFields) {
        const parts = [
          decodeProtoStringLeaf(getProtoBytes(lFields, 3)),
          decodeProtoStringLeaf(getProtoBytes(lFields, 4)),
          decodeProtoStringLeaf(getProtoBytes(lFields, 11))
        ].filter(Boolean);
        if (parts.length > 0) return parts.join(" — ");
      }
    }
    const liveLocBytes = getProtoBytes(fields, 18);
    if (liveLocBytes) {
      const llFields = parseProtobufFields(liveLocBytes);
      const cap = decodeProtoStringLeaf(getProtoBytes(llFields, 3));
      if (cap) return cap;
    }

    // 7. Encuestas en WAProto.Message: pollCreationMessage (49 / 60 / 64 -> campo 2: name, campo 3: repeated Option { 1: optionName })
    for (const pf of [49, 60, 64]) {
      const pollBytes = getProtoBytes(fields, pf);
      if (pollBytes) {
        const pFields = parseProtobufFields(pollBytes);
        if (pFields) {
          const pollTitle = decodeProtoStringLeaf(getProtoBytes(pFields, 2));
          const optSubs = getAllProtoBytes(pFields, 3);
          const opts = [];
          for (const os of optSubs) {
            const oFields = parseProtobufFields(os);
            const oName = decodeProtoStringLeaf(getProtoBytes(oFields, 1));
            if (oName) opts.push(`• ${oName}`);
          }
          if (pollTitle || opts.length > 0) {
            return [pollTitle ? `📊 ${pollTitle}` : "", ...opts].filter(Boolean).join("\n");
          }
        }
      }
    }

    // 8. Botones, listas e interactivos (buttons=42, buttonsResp=43, list=36, listResp=39, templateButtonReply=29, interactive=45)
    const btnBytes = getProtoBytes(fields, 42);
    if (btnBytes) {
      const bFields = parseProtobufFields(btnBytes);
      const txt = decodeProtoStringLeaf(getProtoBytes(bFields, 1)) || decodeProtoStringLeaf(getProtoBytes(bFields, 2));
      if (txt) return txt;
    }
    const btnRespBytes = getProtoBytes(fields, 43) || getProtoBytes(fields, 29);
    if (btnRespBytes) {
      const brFields = parseProtobufFields(btnRespBytes);
      const txt = decodeProtoStringLeaf(getProtoBytes(brFields, 2)) || decodeProtoStringLeaf(getProtoBytes(brFields, 1));
      if (txt) return txt;
    }
    const listBytes = getProtoBytes(fields, 36);
    if (listBytes) {
      const lFields = parseProtobufFields(listBytes);
      const txt = decodeProtoStringLeaf(getProtoBytes(lFields, 2)) || decodeProtoStringLeaf(getProtoBytes(lFields, 1));
      if (txt) return txt;
    }
    const listRespBytes = getProtoBytes(fields, 39);
    if (listRespBytes) {
      const lrFields = parseProtobufFields(listRespBytes);
      const txt = decodeProtoStringLeaf(getProtoBytes(lrFields, 1));
      if (txt) return txt;
    }
    const interBytes = getProtoBytes(fields, 45);
    if (interBytes) {
      const iFields = parseProtobufFields(interBytes);
      if (iFields) {
        const bodySub = parseProtobufFields(getProtoBytes(iFields, 2));
        const headerSub = parseProtobufFields(getProtoBytes(iFields, 1));
        const parts = [
          decodeProtoStringLeaf(getProtoBytes(headerSub, 1)),
          decodeProtoStringLeaf(getProtoBytes(bodySub, 1))
        ].filter(Boolean);
        if (parts.length > 0) return parts.join("\n");
      }
    }

    return "";
  }

  /**
   * Extrae datos de encuesta desde MsgOpaqueData cuando pollName y pollOptions están almacenados en campos superiores (>= 15).
   */
  function extractPollFromMsgOpaqueFields(fields) {
    if (!fields) return "";
    let pollTitle = "";
    const options = [];

    for (const [fieldNum, entries] of fields.entries()) {
      if (fieldNum < 15) continue;
      const wire2Entries = entries.filter(e => e.wireType === 2 && e.value && e.value.length > 0);
      if (wire2Entries.length === 0) continue;

      // Detectar repeated PollOption { optional string name = 1; }
      const parsedOptions = [];
      let allValidOptions = true;
      for (const ent of wire2Entries) {
        const subFields = parseProtobufFields(ent.value);
        if (!subFields || subFields.size > 3) {
          allValidOptions = false;
          break;
        }
        const optName = decodeProtoStringLeaf(getProtoBytes(subFields, 1));
        if (!optName) {
          allValidOptions = false;
          break;
        }
        parsedOptions.push(optName);
      }

      if (allValidOptions && parsedOptions.length > 0 && (wire2Entries.length >= 2 || options.length === 0)) {
        if (parsedOptions.length > options.length) {
          options.length = 0;
          for (const o of parsedOptions) options.push(`• ${o}`);
        }
        continue;
      }

      // Detectar pollName (string único en campos >= 15)
      if (!pollTitle && wire2Entries.length === 1) {
        const cand = decodeProtoStringLeaf(wire2Entries[0].value);
        if (cand && !/^https?:\/\//i.test(cand)) {
          pollTitle = cand;
        }
      }
    }

    if (!pollTitle) {
      pollTitle = decodeProtoStringLeaf(getProtoBytes(fields, 1));
    }
    if (pollTitle || options.length > 0) {
      return [pollTitle ? `📊 ${pollTitle}` : "", ...options].filter(Boolean).join("\n");
    }
    return "";
  }

  /**
   * Extrae texto determinísticamente desde un submensaje MsgOpaqueData (WAWebProtobufsWeb.MsgOpaqueData):
   *   1 = body (string texto en chat/vcard; miniatura JPEG b64 en multimedia)
   *   2 = caption (string pie de foto/video/documento)
   *   4 = loc (string ubicación)
   *   9 = paymentNoteMsgBody (string nota de pago)
   *  12 = title, 13 = description (metadatos rich/link)
   *  14 = futureproofBuffer (bytes -> WAProto.Message)
   */
  function extractFromMsgOpaqueData(msgOpaqueBytes, msgType = "chat", isMedia = false) {
    const empty = { text: "", thumbnail: null, futureproofBytes: null };
    if (!msgOpaqueBytes || msgOpaqueBytes.length === 0) return empty;

    const fields = parseProtobufFields(msgOpaqueBytes);
    if (!fields) return empty;

    const futureproofBytes = getProtoBytes(fields, 14);
    let thumbnail = null;

    // Rescatar miniatura JPEG en Base64 si MsgOpaqueData.body (campo 1) contiene '/9j/' o 'data:image'
    const rawField1Bytes = getProtoBytes(fields, 1);
    if (rawField1Bytes && rawField1Bytes.length > 30) {
      const rawF1 = decodeProtoStringLeaf(rawField1Bytes, false);
      if (rawF1 && (rawF1.startsWith("data:image") || rawF1.includes("/9j/"))) {
        const idx = rawF1.indexOf("/9j/");
        const b64Clean = idx !== -1 ? rawF1.substring(idx).trim() : rawF1;
        thumbnail = b64Clean.startsWith("data:image") ? b64Clean : "data:image/jpeg;base64," + b64Clean;
      }
    }

    // Audios, notas de voz y stickers nunca llevan texto de usuario
    if (msgType === "ptt" || msgType === "audio" || msgType === "sticker") {
      return { text: "", thumbnail, futureproofBytes };
    }

    // 1. Encuestas (poll_creation)
    if (msgType === "poll_creation") {
      const pollText = extractPollFromMsgOpaqueFields(fields) ||
                       (futureproofBytes ? extractTextFromWAProtoMessage(futureproofBytes, false) : "");
      return { text: pollText, thumbnail, futureproofBytes };
    }

    // 2. Multimedia con posible pie de texto (image, video, document)
    if (isMedia) {
      const caption = decodeProtoStringLeaf(getProtoBytes(fields, 2)) ||
                      decodeProtoStringLeaf(getProtoBytes(fields, 3));
      if (caption) return { text: caption, thumbnail, futureproofBytes };

      if (futureproofBytes) {
        const fpCap = extractTextFromWAProtoMessage(futureproofBytes, true);
        if (fpCap) return { text: fpCap, thumbnail, futureproofBytes };
      }

      // Buscar en posibles campos alternativos de futureproof/WAProto dentro de MsgOpaqueData
      for (const [fNum, list] of fields.entries()) {
        if (fNum === 1 || fNum === 2 || fNum === 14) continue;
        for (const item of list) {
          if (item.wireType !== 2 || !item.value || item.value.length < 2) continue;
          const subProto = parseProtobufFields(item.value);
          if (subProto) {
            const subCap = extractTextFromWAProtoMessage(item.value, true);
            if (subCap) return { text: subCap, thumbnail, futureproofBytes };
          }
        }
      }

      const bodyFallback = decodeProtoStringLeaf(rawField1Bytes, true);
      return { text: bodyFallback, thumbnail, futureproofBytes };
    }

    // 3. Ubicación (location / live_location)
    if (msgType === "location" || msgType === "live_location") {
      const locParts = [
        decodeProtoStringLeaf(getProtoBytes(fields, 4)),
        decodeProtoStringLeaf(getProtoBytes(fields, 2)),
        decodeProtoStringLeaf(rawField1Bytes, true)
      ].filter(Boolean);
      return { text: locParts.join(" — "), thumbnail, futureproofBytes };
    }

    // 4. Mensajes de texto estándar, vCards, respuestas interactivas, pagos, etc.
    const body = decodeProtoStringLeaf(rawField1Bytes, true);
    if (body) return { text: body, thumbnail, futureproofBytes };

    const caption = decodeProtoStringLeaf(getProtoBytes(fields, 2));
    if (caption) return { text: caption, thumbnail, futureproofBytes };

    const payNote = decodeProtoStringLeaf(getProtoBytes(fields, 9));
    if (payNote) return { text: payNote, thumbnail, futureproofBytes };

    const loc = decodeProtoStringLeaf(getProtoBytes(fields, 4));
    if (loc) return { text: loc, thumbnail, futureproofBytes };

    if (futureproofBytes) {
      const fpText = extractTextFromWAProtoMessage(futureproofBytes, false);
      if (fpText) return { text: fpText, thumbnail, futureproofBytes };
    }

    if (msgType === "interactive" || msgType === "list" || msgType === "rich_response") {
      const richParts = [
        decodeProtoStringLeaf(getProtoBytes(fields, 12)),
        decodeProtoStringLeaf(getProtoBytes(fields, 13))
      ].filter(Boolean);
      if (richParts.length > 0) return { text: richParts.join("\n"), thumbnail, futureproofBytes };
    }

    return { text: "", thumbnail, futureproofBytes };
  }

  /**
   * Desempaqueta un buffer descifrado de `m.msgRowOpaqueData` (MsgRowOpaqueData):
   *   Campo 1 = currentMsg (MsgOpaqueData)
   *   Campo 2 = quotedMsg  (MsgOpaqueData)
   */
  function unpackMsgRowOpaqueData(decBuf) {
    if (!decBuf) return { currentMsgBytes: null, quotedMsgBytes: null };
    const opaqueBytes = new Uint8Array(decBuf);
    if (opaqueBytes.length === 0) return { currentMsgBytes: null, quotedMsgBytes: null };

    const rowFields = parseProtobufFields(opaqueBytes);
    if (!rowFields) return { currentMsgBytes: null, quotedMsgBytes: null };

    const f1 = getProtoBytes(rowFields, 1);
    const f2 = getProtoBytes(rowFields, 2);

    const f1IsOpaqueMsg = Boolean(f1 && parseProtobufFields(f1));
    const f2IsOpaqueMsg = Boolean(f2 && parseProtobufFields(f2));

    if (f1IsOpaqueMsg || f2IsOpaqueMsg) {
      return {
        currentMsgBytes: f1IsOpaqueMsg ? f1 : null,
        quotedMsgBytes: f2IsOpaqueMsg ? f2 : null
      };
    }

    // Fallback seguro si el buffer ya fuera directamente un MsgOpaqueData sin envoltorio MsgRowOpaqueData
    return { currentMsgBytes: opaqueBytes, quotedMsgBytes: null };
  }

  /**
   * Busca ContextInfo (campo 17) únicamente dentro de un WAProto.Message válido (nunca sobre strings planos).
   */
  function findContextInfoInWAProto(protoBytes, depth = 0) {
    if (depth > 5 || !protoBytes || protoBytes.length < 3) return null;
    const fields = parseProtobufFields(protoBytes);
    if (!fields) return null;

    const directCtx = getProtoBytes(fields, 17);
    if (directCtx && parseProtobufFields(directCtx)) return directCtx;

    // Contenedores de mensaje en WAProto.Message que pueden alojar ContextInfo en su campo 17
    const containerFieldNums = [3, 4, 5, 6, 7, 8, 9, 18, 29, 36, 37, 39, 40, 42, 43, 45, 49, 51, 55, 58, 59, 60, 64];
    for (const fn of containerFieldNums) {
      const sub = getProtoBytes(fields, fn);
      if (sub) {
        const found = findContextInfoInWAProto(sub, depth + 1);
        if (found) return found;
      }
    }
    // En FutureProofMessage (ephemeral/viewOnce/edited), el mensaje interno está en el campo 1
    if (depth > 0) {
      const inner1 = getProtoBytes(fields, 1);
      if (inner1 && parseProtobufFields(inner1)) {
        const found = findContextInfoInWAProto(inner1, depth + 1);
        if (found) return found;
      }
    }
    return null;
  }

  function extractContextInfoFromOpaque(quotedMsgBytes, futureproofBytes) {
    let stanzaId = null;
    let participant = null;
    let quotedBody = "";

    // 1. Extraer texto citado desde MsgRowOpaqueData.quotedMsg (MsgOpaqueData: campo 1=body o campo 2=caption)
    if (quotedMsgBytes) {
      const qExtracted = extractFromMsgOpaqueData(quotedMsgBytes, "chat", false);
      if (qExtracted.text) {
        quotedBody = qExtracted.text;
      }
    }

    // 2. Si existe futureproofBuffer (WAProto.Message), extraer stanzaId, participant y fallback de quotedMessage desde ContextInfo
    if (futureproofBytes) {
      const ctxBytes = findContextInfoInWAProto(futureproofBytes, 0);
      const ctxFields = parseProtobufFields(ctxBytes);
      if (ctxFields) {
        stanzaId = decodeProtoStringLeaf(getProtoBytes(ctxFields, 1), false) || null;
        participant = decodeProtoStringLeaf(getProtoBytes(ctxFields, 2), false) || null;
        if (!quotedBody) {
          const qProtoMsg = getProtoBytes(ctxFields, 3);
          if (qProtoMsg) {
            quotedBody = extractTextFromWAProtoMessage(qProtoMsg, false);
          }
        }
      }
    }

    if (!stanzaId && !participant && !quotedBody) return null;
    return { stanzaId, participant, quotedBody };
  }

  function extractForwardingFromWAProto(futureproofBytes) {
    if (!futureproofBytes || futureproofBytes.length < 3) {
      return { isForwarded: false, forwardingScore: 0 };
    }
    const ctxBytes = findContextInfoInWAProto(futureproofBytes, 0);
    const ctxFields = parseProtobufFields(ctxBytes);
    if (!ctxFields) {
      return { isForwarded: false, forwardingScore: 0 };
    }
    const fwdScore = getProtoVarint(ctxFields, 21) || 0;
    const isFwdFlag = getProtoVarint(ctxFields, 22) === 1;
    if (isFwdFlag || fwdScore > 0) {
      return { isForwarded: true, forwardingScore: Math.max(1, fwdScore) };
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
          const detail = (res && res.data && res.data.error) || (res && res.error) || `Error HTTP ${res ? res.status : 'desconocido'}`;
          return reject(new Error(detail));
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


  async function triggerSync(isAuto = false, forceAll = false) {
    if (isSyncing) return;
    if (verifiedKeys.length === 0 && candidateKeys.length === 0) {
      await scanIndexedDBForCryptoKeys();
    }
    if (verifiedKeys.length === 0 && candidateKeys.length === 0) {
      notifyStatus("waiting", "Abre un chat para activar");
      return;
    }

    let accountInfo = getActiveAccountInfo();
    if (lastDetectedAccountId && lastDetectedAccountId !== accountInfo.id) {
      console.log(`[WAckup] Cambio de cuenta WhatsApp Web detectado: ${lastDetectedAccountId} -> ${accountInfo.id}. Reiniciando caché de IDs.`);
      knownMessageKeys = null;
    }
    lastDetectedAccountId = accountInfo.id;

    isSyncing = true;
    notifyStatus("syncing", forceAll ? "Sincronizando todo..." : "Sincronizando...");

    try {
      let lastTimestamp = 0;

      try {
        const needIds = knownMessageKeys === null ? "&include_ids=1" : "";
        const statusData = await backendFetch(`/api/sync/status?account_id=${encodeURIComponent(accountInfo.id)}${needIds}`);
        lastTimestamp = statusData.last_timestamp || 0;

        // Sincronización Multi-Puesto Inteligente: si otra PC acaba de sincronizar hace < 8s, espaciar
        if (isAuto && !forceAll && statusData && statusData.last_sync_epoch) {
          const nowSec = Math.floor(Date.now() / 1000);
          const syncAge = nowSec - Number(statusData.last_sync_epoch);
          if (syncAge >= 0 && syncAge < 8) {
            isSyncing = false;
            notifyStatus("ready", "Sincronizado en red");
            return;
          }
        }

        if (knownMessageKeys === null && Array.isArray(statusData.synced_msg_ids)) {
          knownMessageKeys = new Set(statusData.synced_msg_ids);
        }
        if (statusData && statusData.extension_version && statusData.installed_extension_version) {
          checkExtensionUpdate(statusData.extension_version, statusData.installed_extension_version);
        }
      } catch (backendErr) {
        console.warn("[WAckup] Servidor local no disponible:", backendErr);
        notifyStatus("error", "Servidor local desconectado");
        isSyncing = false;
        return;
      }

      console.log(`[WAckup] Sincronización iniciada [Cuenta: ${accountInfo.id}] (forceAll=${forceAll}). Último timestamp local: ${lastTimestamp}`);

      const db = await openIndexedDB("model-storage");
      if (!db) {
        notifyStatus("error", "Error al leer IndexedDB");
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
          notifyStatus("synced", `Al día (${nowStr})`);
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
        notifyStatus("synced", `Al día (${nowStr})`);
        return;
      }

      // Descifrado concurrente por lotes exclusivamente desde IndexedDB (pureza anti-ban)
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
          const caption = typeof m.caption === "string" ? m.caption : "";
          const rawBody = (typeof m.body === "string" && m.body) ? m.body : "";

          let bodyText = "";
          let revokedTargetId = null;
          if (isMedia) {
            if (caption && !isBase64MediaBlob(caption)) {
              bodyText = caption;
            } else if (rawBody && !isBase64MediaBlob(rawBody) && !(m.type in { sticker: 1, ptt: 1, audio: 1 })) {
              bodyText = rawBody;
            }
          } else if (m.type === "revoked") {
            bodyText = "🚫 Este mensaje fue eliminado";
            const pKey = m.protocolMessageKey || (m.protocolMessage && m.protocolMessage.key) || m.targetMessageKey || m.revokedMsgKey;
            if (pKey) {
              revokedTargetId = extractId(pKey.id || pKey);
            }
          } else if (m.type === "poll_creation" && m.pollName) {
            const opts = Array.isArray(m.pollOptions)
              ? m.pollOptions.map(o => (o && (o.name || o.optionName)) ? `• ${o.name || o.optionName}` : "").filter(Boolean)
              : [];
            bodyText = [`📊 ${m.pollName}`, ...opts].join("\n");
          } else {
            bodyText = isBase64MediaBlob(rawBody) ? (isBase64MediaBlob(caption) ? "" : caption) : (rawBody || caption);
          }

          // Estado de reenviado desde propiedades directas en IndexedDB
          const ctxInfo = m.msgContextInfo || m.contextInfo || {};
          let forwardingScore = Number(m.forwardingScore) || Number(ctxInfo.forwardingScore) || 0;
          let isForwarded = Boolean(
            m.isForwarded ||
            ctxInfo.isForwarded ||
            forwardingScore > 0
          );

          // Si existe msgRowOpaqueData, desempaquetar determinísticamente MsgRowOpaqueData -> MsgOpaqueData
          const canHaveEncryptedText = !isMedia || (m.type in { image: 1, video: 1, document: 1 });
          let quotedInfoFromOpaque = null;
          let opaqueThumbnail = null;
          if (m.msgRowOpaqueData) {
            const decBuf = await decryptOpaqueData(m.msgRowOpaqueData);
            if (decBuf) {
              const { currentMsgBytes, quotedMsgBytes } = unpackMsgRowOpaqueData(decBuf);
              const opaqueCurrent = currentMsgBytes
                ? extractFromMsgOpaqueData(currentMsgBytes, m.type || "chat", isMedia)
                : { text: "", thumbnail: null, futureproofBytes: null };

              if (opaqueCurrent.thumbnail) {
                opaqueThumbnail = opaqueCurrent.thumbnail;
              }
              if (!bodyText && canHaveEncryptedText && opaqueCurrent.text && !isBase64MediaBlob(opaqueCurrent.text)) {
                bodyText = opaqueCurrent.text;
              }
              if (!isForwarded && opaqueCurrent.futureproofBytes) {
                const fwdInfo = extractForwardingFromWAProto(opaqueCurrent.futureproofBytes);
                if (fwdInfo.isForwarded) {
                  isForwarded = true;
                  forwardingScore = Math.max(forwardingScore, fwdInfo.forwardingScore || 1);
                }
              }
              quotedInfoFromOpaque = extractContextInfoFromOpaque(quotedMsgBytes, opaqueCurrent.futureproofBytes);
            }
          }
          if (isForwarded && forwardingScore < 1) {
            forwardingScore = 1;
          }

          if (bodyText) {
            bodyText = bodyText.replace(/\ufffd/g, "").replace(/[\x0b\x0c]/g, "\n").replace(/[\x00-\x08\x0e-\x1f\x7f]/g, "").trim();
            if (m.type in { sticker: 1, ptt: 1, audio: 1 }) {
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
          } else if (opaqueThumbnail) {
            thumbnailStr = opaqueThumbnail;
          }

          const qMsg = m.quotedMsg || {};
          const quotedMsgId = extractId(m.quotedStanzaID || qMsg.id || (quotedInfoFromOpaque && quotedInfoFromOpaque.stanzaId));
          const quotedAuthor = extractId(m.quotedParticipant || qMsg.author || qMsg.from || (quotedInfoFromOpaque && quotedInfoFromOpaque.participant));
          let quotedBody = (typeof qMsg.caption === "string" && !isBase64MediaBlob(qMsg.caption) ? qMsg.caption : "") ||
                           (typeof qMsg.body === "string" && !isBase64MediaBlob(qMsg.body) ? qMsg.body : "") ||
                           (quotedInfoFromOpaque && quotedInfoFromOpaque.quotedBody) || "";

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
            forwardingScore: forwardingScore,
            revokedTargetId: revokedTargetId
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
          notifyStatus("syncing", `Procesando ${i}/${rawMessages.length}...`);
        }
      }

      if (cleanMessages.length === 0) {
        if (!shouldSendContacts && !shouldSendChats) {
          const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          notifyStatus("synced", `Al día (${nowStr})`);
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
        notifyStatus("synced", `Al día (${nowStr})`);
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
          notifyStatus("syncing", `Guardando ${Math.min(i + BATCH_SIZE, cleanMessages.length)}/${cleanMessages.length}...`);
        }
      }

      lastContactsHash = contactsHash;
      lastChatsHash = chatsHash;
      lastContactsSyncTime = now;

      const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      console.log(`%c[WAckup] Sincronización exitosa: ${totalSaved} mensajes guardados, ${totalMediaEnqueued} multimedia en cola.`, "color: #10b981; font-weight: bold; font-size: 13px;");
      notifyStatus("synced", `Sincronizado ${nowStr} (${totalSaved} msgs)`, totalSaved);

    } catch (err) {
      console.error("[WAckup] Error durante sincronización:", err);
      notifyStatus("error", "Error de sincronización");
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
  // NOTIFICACIÓN PASIVA AL BRIDGE Y SERVICE WORKER (Cero DOM)
  // =========================================================================
  function notifyStatus(state, message, totalSaved = 0) {
    try {
      const accountInfo = getActiveAccountInfo();
      window.postMessage({
        type: "BACKUPWA_STATUS",
        state: state,
        message: message,
        totalSaved: totalSaved,
        accountInfo: accountInfo
      }, "https://web.whatsapp.com");
    } catch (e) {
      console.warn("[WAckup] Error al notificar estado:", e);
    }
  }

  function checkExtensionUpdate(diskVer, installedVer) {
    if (diskVer && installedVer && diskVer !== installedVer) {
      window.postMessage({
        type: "BACKUPWA_VERSION_INFO",
        diskVer: diskVer,
        installedVer: installedVer
      }, "https://web.whatsapp.com");
    }
  }

  // Escuchar solicitudes de sincronización manual desde el bridge/sidepanel
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== "https://web.whatsapp.com") return;
    if (!event.data) return;
    if (event.data.type === "BACKUPWA_TRIGGER_SYNC") {
      triggerSync(false, Boolean(event.data.forceAll));
    }
  });

  // =========================================================================
  // INICIALIZACIÓN PASIVA (Cero DOM)
  // =========================================================================
  let scrollSyncTimer = null;

  function init() {
    setupCryptoInterceptor();

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
