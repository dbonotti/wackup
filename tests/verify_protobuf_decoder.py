import json
import os
import sqlite3
import subprocess
import tempfile
import unittest

import db_manager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRYPTO_SYNC_PATH = os.path.join(PROJECT_ROOT, "extension", "crypto_sync.js")


NODE_HARNESS = r"""
const fs = require('fs');
const cryptoSyncCode = fs.readFileSync(process.argv[2], 'utf8');

// Extraer la sección 2 (decodificador Protobuf) de crypto_sync.js para probar exactamente las funciones reales
const startMarker = '// 2. DECODIFICADOR PROTOBUF ESTRUCTURAL DETERMINISTA';
const endMarker = 'function bufferToBase64(buf)';
const startIdx = cryptoSyncCode.indexOf(startMarker);
const endIdx = cryptoSyncCode.indexOf(endMarker);
if (startIdx === -1 || endIdx === -1) {
  throw new Error('No se encontraron los marcadores del decodificador en crypto_sync.js');
}
const decoderSection = cryptoSyncCode.slice(startIdx, endIdx);

const factory = new Function(
  decoderSection +
  "\nreturn {\n" +
  "  readVarint,\n" +
  "  isBase64MediaBlob,\n" +
  "  parseProtobufFields,\n" +
  "  decodeProtoStringLeaf,\n" +
  "  extractTextFromWAProtoMessage,\n" +
  "  extractFromMsgOpaqueData,\n" +
  "  unpackMsgRowOpaqueData,\n" +
  "  extractContextInfoFromOpaque,\n" +
  "  extractForwardingFromWAProto\n" +
  "};\n"
);
const dec = factory();

function encodeVarint(n) {
  const out = [];
  let v = n >>> 0;
  while (v > 0x7f) {
    out.push((v & 0x7f) | 0x80);
    v >>>= 7;
  }
  out.push(v & 0x7f);
  return Buffer.from(out);
}

function pbBytes(fieldNum, buf) {
  const data = Buffer.isBuffer(buf) ? buf : Buffer.from(buf, 'utf8');
  const tag = encodeVarint((fieldNum * 8) + 2);
  const len = encodeVarint(data.length);
  return Buffer.concat([tag, len, data]);
}

function pbVarint(fieldNum, val) {
  const tag = encodeVarint((fieldNum * 8) + 0);
  return Buffer.concat([tag, encodeVarint(val)]);
}

function pbFixed64(fieldNum) {
  const tag = encodeVarint((fieldNum * 8) + 1);
  return Buffer.concat([tag, Buffer.alloc(8, 0x40)]);
}

function assertEq(actual, expected, label) {
  if (actual !== expected) {
    throw new Error(`[FAIL] ${label}\n  Esperado: ${JSON.stringify(expected)}\n  Actual:   ${JSON.stringify(actual)}`);
  }
}

// 1. Caso real reportado por el usuario (Eugenia)
{
  const fullText = 'Hola Eugenia! anduve con algunas cosas y recién escucho el mensaje. Si, podría ser la parte de carga más que la batería (igual raro que "tardó" en fallar)';
  const opaqueRow = pbBytes(1, pbBytes(1, fullText));
  const { currentMsgBytes, quotedMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  assertEq(res.text, fullText, 'Mensaje real Eugenia completo');
  assertEq(quotedMsgBytes, null, 'Sin cita en mensaje Eugenia');
}

// 2. Mensajes largos con prefijos que antes activaban isSubMessage o scanLengthPrefixedStrings
const trickyTexts = [
  'Buenas tardes! Te escribo porque estuve revisando el equipo y efectivamente el módulo de carga presenta falso contacto cuando se mueve el conector USB-C.',
  'Buenos días.\n\nSe recuerda que, conforme la Disposición Conjunta Nro. 113/24, el plazo de inscripción vence indefectiblemente el próximo viernes a las 12:00 hs.',
  'Para solicitar un turno, puede dirigirse a www.bahiaturnos.com.ar, donde encontrará todos los profesionales disponibles y horarios actualizados de atención.',
  'Me gustaría invitarle a una sesión de AnyDesk. Mi ID es 1 742 254 407.\n\nObtenga AnyDesk para conectarse rápidamente.',
  '*Importante*: Recordá traer el cargador original y la factura de compra para tramitar la garantía oficial sin demoras en recepción.',
  '"Recién hablé con el proveedor" me dijo, así que mañana a primera hora pasamos a retirar los repuestos por el depósito central.',
  '2 de noviembre: reunión general de coordinación técnica en el departamento a las 09:30 hs con todos los integrantes del área.',
  'r 12345678901234567890123456789012', // 34 bytes exactos donde 'r ' parecería tag 14 + len 32 si se desenvolviera 3 veces
  'Si\n¡Muchas gracias por avisar!',
  'Ok\n*Pasame el alias cuando puedas*',
  'Línea 1 desde Word\x0bLínea 2 con Shift+Enter'
];

for (const txt of trickyTexts) {
  const opaqueRow = pbBytes(1, pbBytes(1, txt));
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  const expected = txt.replace(/\x0b/g, '\n');
  assertEq(res.text, expected, `Texto complejo: ${txt.slice(0, 28)}...`);
}

// 3. vCard completa sin perder 'BE' ni cortar el teléfono
{
  const vcard = 'BEGIN:VCARD\nVERSION:3.0\nN:Dekak;Daniel;;;\nFN:Daniel Dekak\nTEL;type=CELL;waid=5492915739300:+54 9 291 573-9300\nEND:VCARD';
  const opaqueRow = pbBytes(1, pbBytes(1, vcard));
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'vcard', false);
  assertEq(res.text, vcard, 'vCard íntegra');
}

// 4. Imagen/Video/Documento con miniatura Base64 en campo 1 y pie de foto (caption) en campo 2
{
  const fakeJpegB64 = '/9j/4AAQSkZJRgABAQAAAQABAAD/' + 'A'.repeat(120);
  const caption = 'Acá te paso el comprobante de transferencia bancaria';
  const currentMsg = Buffer.concat([pbBytes(1, fakeJpegB64), pbBytes(2, caption)]);
  const opaqueRow = pbBytes(1, currentMsg);
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'image', true);
  assertEq(res.text, caption, 'Caption de imagen recuperado');
  assertEq(res.thumbnail, 'data:image/jpeg;base64,' + fakeJpegB64, 'Miniatura rescatada de MsgOpaqueData');
}

// 4b. Imagen con pie de foto (caption) en campo 3 de MsgOpaqueData
{
  const fakeJpegB64 = '/9j/4AAQSkZJRgABAQAAAQABAAD/' + 'C'.repeat(120);
  const caption = 'Confirmar presencia antes del 20/11 por favor';
  const currentMsg = Buffer.concat([pbBytes(1, fakeJpegB64), pbBytes(3, caption)]);
  const opaqueRow = pbBytes(1, currentMsg);
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'image', true);
  assertEq(res.text, caption, 'Caption de imagen en campo 3 recuperado');
}

// 5. Imagen sin caption (solo miniatura en campo 1) -> texto vacío, nunca basura
{
  const fakeJpegB64 = '/9j/4AAQSkZJRgABAQAAAQABAAD/' + 'B'.repeat(120);
  const opaqueRow = pbBytes(1, pbBytes(1, fakeJpegB64));
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'image', true);
  assertEq(res.text, '', 'Imagen sin caption debe dar string vacío');
}

// 6. Mensaje corto con Link Preview gigante en campo 13 (description > 1200 chars)
{
  const shortBody = 'Mirá esta nota https://ejemplo.com/nota';
  const hugeDesc = 'Descripción SEO larguísima '.repeat(60);
  const currentMsg = Buffer.concat([
    pbBytes(1, shortBody),
    pbBytes(10, 'https://ejemplo.com/nota'),
    pbBytes(11, 'https://ejemplo.com/nota'),
    pbBytes(12, 'Título de la nota periodística'),
    pbBytes(13, hugeDesc)
  ]);
  const opaqueRow = pbBytes(1, currentMsg);
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  assertEq(res.text, shortBody, 'Link preview largo nunca pisa al body corto');
}

// 7. Respuesta con cita larga (currentMsg + quotedMsg) y respuesta solo con sticker/audio citado (solo quotedMsg)
{
  const replyText = 'Dale, ahí lo reviso!';
  const quotedFull = 'Buenos días! Te dejo el detalle completo del presupuesto actualizado con las tres opciones de batería y módulo.';
  const opaqueRow = Buffer.concat([
    pbBytes(1, pbBytes(1, replyText)),
    pbBytes(2, pbBytes(1, quotedFull))
  ]);
  const { currentMsgBytes, quotedMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const cur = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  const ctx = dec.extractContextInfoFromOpaque(quotedMsgBytes, null);
  assertEq(cur.text, replyText, 'Texto propio de respuesta');
  assertEq(ctx.quotedBody, quotedFull, 'Texto citado completo sin truncar');

  // Solo campo 2 (ej. responder con audio/sticker sin currentMsg)
  const onlyQuoteRow = pbBytes(2, pbBytes(1, quotedFull));
  const unpacked2 = dec.unpackMsgRowOpaqueData(onlyQuoteRow);
  assertEq(unpacked2.currentMsgBytes, null, 'currentMsgBytes debe ser null si solo existe campo 2');
  const ctx2 = dec.extractContextInfoFromOpaque(unpacked2.quotedMsgBytes, null);
  assertEq(ctx2.quotedBody, quotedFull, 'Cita extraída cuando no hay currentMsg');
}

// 8. CBU de 22 dígitos, UUID y emojis especiales (estrellas, keycaps, relojes)
for (const special of [
  '0000003100012345678901',
  '550e8400-e29b-41d4-a716-446655440000',
  '⭐⭐⭐⭐⭐⭐',
  '1️⃣2️⃣3️⃣',
  '⏰⏳✅'
]) {
  const opaqueRow = pbBytes(1, pbBytes(1, special));
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  assertEq(res.text, special, `Caso especial preservado: ${special}`);
}

// 9. Encuesta (poll_creation) en MsgOpaqueData
{
  const pollOpaque = Buffer.concat([
    pbBytes(21, '¿Qué día nos juntamos?'),
    pbBytes(22, pbBytes(1, 'Miércoles 29')),
    pbBytes(22, pbBytes(1, 'Jueves 30')),
    pbBytes(22, pbBytes(1, 'Viernes 31'))
  ]);
  const opaqueRow = pbBytes(1, pollOpaque);
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'poll_creation', false);
  assertEq(res.text, '📊 ¿Qué día nos juntamos?\n• Miércoles 29\n• Jueves 30\n• Viernes 31', 'Encuesta completa con pregunta y opciones');
}

// 10. futureproofBuffer (campo 14) con WAProto.Message + ContextInfo (reenviado + cita)
{
  const contextInfo = Buffer.concat([
    pbBytes(1, '3EB0ABCDEF123456'),
    pbBytes(2, '5492915729351@s.whatsapp.net'),
    pbBytes(3, pbBytes(1, 'Mensaje original dentro de WAProto')),
    pbVarint(21, 5),
    pbVarint(22, 1)
  ]);
  const extTextMsg = Buffer.concat([
    pbBytes(1, 'Texto extendido en futureproofBuffer'),
    pbBytes(17, contextInfo)
  ]);
  const waProtoMsg = pbBytes(6, extTextMsg);
  const currentMsg = pbBytes(14, waProtoMsg);
  const opaqueRow = pbBytes(1, currentMsg);

  const { currentMsgBytes, quotedMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'chat', false);
  const fwd = dec.extractForwardingFromWAProto(res.futureproofBytes);
  const ctx = dec.extractContextInfoFromOpaque(quotedMsgBytes, res.futureproofBytes);

  assertEq(res.text, 'Texto extendido en futureproofBuffer', 'Texto desde WAProto.ExtendedTextMessage');
  assertEq(fwd.isForwarded, true, 'isForwarded desde ContextInfo');
  assertEq(fwd.forwardingScore, 5, 'forwardingScore=5 desde ContextInfo');
  assertEq(ctx.stanzaId, '3EB0ABCDEF123456', 'stanzaId desde ContextInfo');
  assertEq(ctx.quotedBody, 'Mensaje original dentro de WAProto', 'quotedMessage desde ContextInfo');
}

// 11. Mensaje unknown con bytes binarios arbitrarios en campo desconocido -> no inventa basura tipo 'kcK'
{
  const noisyOpaque = Buffer.concat([
    pbFixed64(5),
    pbBytes(25, Buffer.from([0x00, 0xff, 0x6b, 0x63, 0x4b, 0x00, 0x19]))
  ]);
  const opaqueRow = pbBytes(1, noisyOpaque);
  const { currentMsgBytes } = dec.unpackMsgRowOpaqueData(opaqueRow);
  const res = dec.extractFromMsgOpaqueData(currentMsgBytes, 'unknown', false);
  assertEq(res.text, '', 'Sin basura binaria en mensajes unknown');
}

console.log('ALL_NODE_DECODER_TESTS_PASSED');
"""


class TestProtobufDecoderAndSyncIntegrity(unittest.TestCase):
    def test_01_node_deterministic_protobuf_decoder(self):
        """Ejecuta la batería completa de pruebas sobre las funciones reales de extension/crypto_sync.js."""
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
            tf.write(NODE_HARNESS)
            harness_path = tf.name
        try:
            res = subprocess.run(
                ["node", harness_path, CRYPTO_SYNC_PATH],
                capture_output=True,
                text=True,
                timeout=15
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Falló la suite Node del decodificador Protobuf:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            )
            self.assertIn("ALL_NODE_DECODER_TESTS_PASSED", res.stdout)
        finally:
            if os.path.exists(harness_path):
                os.remove(harness_path)

    def test_02_db_manager_preserves_multiline_cbu_emojis_and_identical_replies(self):
        """Verifica que db_manager.save_incremental_sync no recorte ni descarte mensajes válidos."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_backup.db")
            db_manager.init_db(db_path)

            eugenia_full = (
                'Hola Eugenia! anduve con algunas cosas y recién escucho el mensaje. '
                'Si, podría ser la parte de carga más que la batería (igual raro que "tardó" en fallar)'
            )
            vcard_full = (
                "BEGIN:VCARD\nVERSION:3.0\nN:Dekak;Daniel;;;\nFN:Daniel Dekak\n"
                "TEL;type=CELL;waid=5492915739300:+54 9 291 573-9300\nEND:VCARD"
            )

            payload = {
                "account_id": "5492915739300",
                "account_name": "Test",
                "account_phone": "5492915739300",
                "contacts": [],
                "chats": [{"id": "5492915729351@c.us", "name": "Fernandez Eugenia UNS", "t": 1793616030}],
                "messages": [
                    {
                        "id": "true_5492915729351@c.us_MSG1",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "chat",
                        "timestamp": 1793616001,
                        "body": eugenia_full,
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG2",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "chat",
                        "timestamp": 1793616002,
                        "body": "Si\n¡Muchas gracias!",
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG3",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "chat",
                        "timestamp": 1793616003,
                        "body": "0000003100012345678901",
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG4",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "chat",
                        "timestamp": 1793616004,
                        "body": "⭐⭐⭐⭐⭐⭐",
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG5",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "vcard",
                        "timestamp": 1793616005,
                        "body": vcard_full,
                    },
                    {
                        "id": "false_5492915729351@c.us_MSG6",
                        "chatId": "5492915729351@c.us",
                        "fromMe": False,
                        "type": "chat",
                        "timestamp": 1793616006,
                        "body": "Buen día!",
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG7",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "chat",
                        "timestamp": 1793616007,
                        "body": "Buen día!",
                        "quotedMsgId": "MSG6",
                        "quotedBody": "Buen día!",
                    },
                    {
                        "id": "true_5492915729351@c.us_MSG8",
                        "chatId": "5492915729351@c.us",
                        "fromMe": True,
                        "type": "image",
                        "timestamp": 1793616008,
                        "body": "Pie de foto completo sin perderse",
                    },
                ],
            }

            db_manager.save_incremental_sync(payload, db_path=db_path)

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT msg_id, contenido, quoted_body FROM mensajes ORDER BY timestamp ASC")
            saved = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            conn.close()

            self.assertEqual(saved["true_5492915729351@c.us_MSG1"][0], eugenia_full)
            self.assertEqual(saved["true_5492915729351@c.us_MSG2"][0], "Si\n¡Muchas gracias!")
            self.assertEqual(saved["true_5492915729351@c.us_MSG3"][0], "0000003100012345678901")
            self.assertEqual(saved["true_5492915729351@c.us_MSG4"][0], "⭐⭐⭐⭐⭐⭐")
            self.assertEqual(saved["true_5492915729351@c.us_MSG5"][0], vcard_full)
            self.assertEqual(saved["true_5492915729351@c.us_MSG7"], ("Buen día!", "Buen día!"))
            self.assertEqual(saved["true_5492915729351@c.us_MSG8"][0], "Pie de foto completo sin perderse")


if __name__ == "__main__":
    unittest.main()
