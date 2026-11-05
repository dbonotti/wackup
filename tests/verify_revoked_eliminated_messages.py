import os
import unittest
import tempfile
import sqlite3
import shutil

import db_manager


class TestRevokedEliminatedMessages(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_whatsapp.db")
        db_manager.init_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_incoming_revoked_marks_original_and_avoids_duplicate(self):
        """Verifica que al llegar una revocación entrante, el mensaje original se marca como eliminado = 1 sin duplicar la burbuja."""
        chat_id = "5492911111111@c.us"
        ts = 1793815554

        # 1. Guardar mensaje original (ej: imagen)
        sync_batch_1 = {
            "messages": [{
                "id": "false_5492911111111@c.us_MSG_ORIG_IMG_1",
                "chatId": chat_id,
                "from": chat_id,
                "fromMe": False,
                "type": "image",
                "timestamp": ts,
                "body": "",
                "directPath": "/v/test.enc",
                "mediaKey": "dGVzdGtleQ=="
            }]
        }
        db_manager.save_incremental_sync(sync_batch_1, db_path=self.db_path)

        with db_manager.db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT msg_id, tipo, eliminado FROM mensajes WHERE chat_id = ?", (chat_id,))
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["tipo"], "image")
            self.assertEqual(rows[0]["eliminado"], 0)

        # 2. Llega la revocación de WhatsApp (tipo: revoked, mismo ts y chat)
        sync_batch_2 = {
            "messages": [{
                "id": "false_5492911111111@c.us_REVOKE_ACTION_1",
                "chatId": chat_id,
                "from": chat_id,
                "fromMe": False,
                "type": "revoked",
                "timestamp": ts,
                "body": "🚫 Este mensaje fue eliminado"
            }]
        }
        db_manager.save_incremental_sync(sync_batch_2, db_path=self.db_path)

        with db_manager.db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT msg_id, tipo, eliminado FROM mensajes WHERE chat_id = ?", (chat_id,))
            rows = cur.fetchall()
            # Debe existir exactamente 1 mensaje (el original conservado), marcado como eliminado = 1
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["msg_id"], "false_5492911111111@c.us_MSG_ORIG_IMG_1")
            self.assertEqual(rows[0]["tipo"], "image")
            self.assertEqual(rows[0]["eliminado"], 1)

    def test_outgoing_revoked_marks_sent_message(self):
        """Verifica que mensajes salientes (es_mio = 1) también se marquen correctamente como eliminados."""
        chat_id = "5492922222222@c.us"
        ts = 1793816000

        # 1. Guardar mensaje enviado
        sync_batch_1 = {
            "messages": [{
                "id": "true_5492922222222@c.us_MSG_OUT_1",
                "chatId": chat_id,
                "fromMe": True,
                "type": "chat",
                "timestamp": ts,
                "body": "Te paso los datos del servidor"
            }]
        }
        db_manager.save_incremental_sync(sync_batch_1, db_path=self.db_path)

        # 2. El usuario elimina el mensaje saliente
        sync_batch_2 = {
            "messages": [{
                "id": "true_5492922222222@c.us_REVOKE_OUT_1",
                "chatId": chat_id,
                "fromMe": True,
                "type": "revoked",
                "timestamp": ts,
                "body": "🚫 Este mensaje fue eliminado"
            }]
        }
        db_manager.save_incremental_sync(sync_batch_2, db_path=self.db_path)

        with db_manager.db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT msg_id, es_mio, contenido, tipo, eliminado FROM mensajes WHERE chat_id = ?", (chat_id,))
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["es_mio"], 1)
            self.assertEqual(rows[0]["contenido"], "Te paso los datos del servidor")
            self.assertEqual(rows[0]["tipo"], "chat")
            self.assertEqual(rows[0]["eliminado"], 1)

    def test_orphan_revoked_then_original_arrives(self):
        """Verifica que si llega un placeholder 'revoked' huérfano y luego el original, se reconcilie con eliminado=1."""
        chat_id = "5492933333333@c.us"
        ts = 1793817000

        # 1. Llega revoked sin que existiera original
        sync_batch_orphan = {
            "messages": [{
                "id": "false_5492933333333@c.us_REVOKE_ORPHAN",
                "chatId": chat_id,
                "fromMe": False,
                "type": "revoked",
                "timestamp": ts,
                "body": "🚫 Este mensaje fue eliminado"
            }]
        }
        db_manager.save_incremental_sync(sync_batch_orphan, db_path=self.db_path)

        with db_manager.db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT msg_id, tipo, eliminado FROM mensajes WHERE chat_id = ?", (chat_id,))
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["tipo"], "revoked")
            self.assertEqual(rows[0]["eliminado"], 1)

        # 2. Posteriormente llega el mensaje original respaldado
        sync_batch_orig = {
            "messages": [{
                "id": "false_5492933333333@c.us_MSG_LATE_ORIG",
                "chatId": chat_id,
                "fromMe": False,
                "type": "chat",
                "timestamp": ts,
                "body": "Mensaje que se borró después"
            }]
        }
        db_manager.save_incremental_sync(sync_batch_orig, db_path=self.db_path)

        with db_manager.db_connection(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT msg_id, tipo, contenido, eliminado FROM mensajes WHERE chat_id = ?", (chat_id,))
            rows = cur.fetchall()
            # El placeholder fue reemplazado por el contenido original con eliminado = 1
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["msg_id"], "false_5492933333333@c.us_MSG_LATE_ORIG")
            self.assertEqual(rows[0]["contenido"], "Mensaje que se borró después")
            self.assertEqual(rows[0]["eliminado"], 1)

    def test_legacy_database_migration_reconciles_duplicates(self):
        """Verifica que una base de datos antigua con filas dobles (original + revoked) se migre limpiamente al iniciar init_db."""
        legacy_db = os.path.join(self.test_dir, "legacy.db")
        conn = sqlite3.connect(legacy_db)
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE chats (chat_id TEXT PRIMARY KEY, nombre TEXT, ultimo_timestamp INTEGER, ultimo_mensaje TEXT, ultimo_es_mio INTEGER, ultimo_tipo TEXT);
            CREATE TABLE mensajes (
                msg_id TEXT PRIMARY KEY, chat_id TEXT, remitente TEXT, es_mio INTEGER, fecha_utc TEXT, timestamp INTEGER, tipo TEXT, contenido TEXT
            );
            INSERT INTO chats VALUES ('chat_test', 'Prueba', 1793815000, 'hola', 0, 'chat');
            -- Mensaje original guardado
            INSERT INTO mensajes VALUES ('orig_1', 'chat_test', 'chat_test', 0, '2026-11-04 15:05:00', 1793815000, 'image', '');
            -- Burbuja 'revoked' guardada posteriormente en la versión antigua
            INSERT INTO mensajes VALUES ('rev_1', 'chat_test', 'chat_test', 0, '2026-11-04 15:05:00', 1793815000, 'revoked', '🚫 Este mensaje fue eliminado');
        """)
        conn.commit()
        conn.close()

        # Ejecutar init_db sobre la base de datos antigua
        db_manager.init_db(legacy_db)

        conn = sqlite3.connect(legacy_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT msg_id, tipo, eliminado FROM mensajes WHERE chat_id = 'chat_test'")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["msg_id"], "orig_1")
        self.assertEqual(rows[0]["tipo"], "image")
        self.assertEqual(rows[0]["eliminado"], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
