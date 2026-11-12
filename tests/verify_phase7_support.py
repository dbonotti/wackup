import os
import sys
import time
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import db_manager
import sse_hub


class TestPhase7Support(unittest.TestCase):
    """
    Suite de verificación integral para FASE 2:
    - Auto-reapertura de tickets pending/resolved al entrar mensaje del cliente.
    - Exclusión del Radar de tickets 'open' si ya fueron respondidos (ultimo_es_mio = 1).
    - Preservación de tickets antiguos (> 7 días) si siguen sin respuesta (ultimo_es_mio = 0).
    - Ordenamiento por urgencia: COALESCE(support_priority, 0) DESC, ultimo_timestamp ASC.
    - Prioridades de soporte en Kanban.
    - Limpieza instantánea de presencia (0ms ghosting) en SSEHub al cambiar de chat.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_phase7_support.db")
        db_manager.init_db(self.db_path)

    def tearDown(self):
        with db_manager._INITIALIZED_LOCK:
            db_manager._INITIALIZED_DBS.discard(os.path.abspath(self.db_path))
        self.tmp_dir.cleanup()

    def test_01_auto_reopen_tickets_on_client_reply(self):
        """Valida que un ticket pending o resolved se reabra automáticamente solo si el cliente responde en chat 1 a 1."""
        now = int(time.time())
        t0 = now - 3600
        t1 = now - 1800
        t2 = now - 60

        # Crear dos chats individuales y un grupo
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                [
                    ("cliente_pending@c.us", "Cliente Pending", t0, "Esperando repuesto", 1),
                    ("cliente_resolved@c.us", "Cliente Resolved", t0, "Ticket resuelto", 1),
                    ("grupo_soporte@g.us", "Grupo Soporte", t0, "Atención equipo", 1),
                ]
            )

        db_manager.upsert_contact_meta("cliente_pending@c.us", ticket_status="pending", db_path=self.db_path)
        db_manager.upsert_contact_meta("cliente_resolved@c.us", ticket_status="resolved", db_path=self.db_path)
        db_manager.upsert_contact_meta("grupo_soporte@g.us", ticket_status="resolved", db_path=self.db_path)

        # 1. Mensaje del propio operador (is_mine = 1) -> NO debe reabrir
        data_mine = {
            "messages": [{
                "id": "true_cliente_pending@c.us_1",
                "chatId": "cliente_pending@c.us",
                "fromMe": True,
                "timestamp": t1,
                "body": "Hola, te paso novedad"
            }]
        }
        db_manager.save_incremental_sync(data_mine, db_path=self.db_path)
        meta_pending = db_manager.get_contact_meta("cliente_pending@c.us", db_path=self.db_path)
        self.assertEqual(meta_pending["ticket_status"], "pending")

        # 2. Mensaje en un GRUPO (@g.us) -> NO debe reabrir
        data_group = {
            "messages": [{
                "id": "false_grupo_soporte@g.us_1_123@c.us",
                "chatId": "grupo_soporte@g.us",
                "fromMe": False,
                "timestamp": t1,
                "body": "Comentario de cliente en grupo"
            }]
        }
        db_manager.save_incremental_sync(data_group, db_path=self.db_path)
        meta_group = db_manager.get_contact_meta("grupo_soporte@g.us", db_path=self.db_path)
        self.assertEqual(meta_group["ticket_status"], "resolved")

        # 3. Mensaje del cliente (is_mine = 0) en chat 1 a 1 -> DEBE reabrir a 'open'
        data_client_pending = {
            "messages": [{
                "id": "false_cliente_pending@c.us_2",
                "chatId": "cliente_pending@c.us",
                "fromMe": False,
                "timestamp": t2,
                "body": "Hola, todavía no me llegó nada"
            }]
        }
        db_manager.save_incremental_sync(data_client_pending, db_path=self.db_path)
        meta_pending_after = db_manager.get_contact_meta("cliente_pending@c.us", db_path=self.db_path)
        self.assertEqual(meta_pending_after["ticket_status"], "open")

        # Verificar que se insertó nota de Sistema en la bitácora
        ctx_pending = db_manager.get_copilot_context("cliente_pending@c.us", db_path=self.db_path)
        self.assertGreaterEqual(len(ctx_pending["notes_log"]), 1)
        system_note = ctx_pending["notes_log"][0]
        self.assertEqual(system_note["author"], "Sistema")
        self.assertIn("Ticket reabierto automáticamente", system_note["content"])

        # 4. Mensaje del cliente en ticket resolved enviado JUNTO con el lote 'chats' (como envía bridge.js) -> DEBE reabrir a 'open'
        data_client_resolved = {
            "chats": [{
                "id": "cliente_resolved@c.us",
                "name": "Cliente Resolved",
                "t": t2
            }],
            "messages": [{
                "id": "false_cliente_resolved@c.us_2",
                "chatId": "cliente_resolved@c.us",
                "fromMe": False,
                "timestamp": t2,
                "body": "Se me volvió a romper"
            }]
        }
        db_manager.save_incremental_sync(data_client_resolved, db_path=self.db_path)
        meta_resolved_after = db_manager.get_contact_meta("cliente_resolved@c.us", db_path=self.db_path)
        self.assertEqual(meta_resolved_after["ticket_status"], "open")

    def test_02_radar_excludes_answered_tickets_and_preserves_unanswered_old(self):
        """Valida que los tickets open ya respondidos salgan del Radar, y los viejos sin responder se preserven."""
        now = int(time.time())
        old_ts = now - (12 * 86400)      # 12 días atrás
        recent_ts = now - 300            # 5 min atrás

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                [
                    # Ticket abierto pero el último mensaje es del operador -> DEBE QUEDAR EXCLUIDO DEL RADAR
                    ("ticket_respondido@c.us", "Cliente Respondido", recent_ts, "Te paso la solución", 1),
                    # Ticket abierto viejo pero sin responder (ultimo es del cliente) -> DEBE PERMANECER
                    ("ticket_viejo_sin_respuesta@c.us", "Cliente Esperando", old_ts, "Sigo esperando respuesta", 0),
                    # Chat normal viejo sin ticket open -> DEBE QUEDAR EXCLUIDO POR VENTANA > 7 DÍAS
                    ("chat_viejo_normal@c.us", "Chat Viejo", old_ts, "Gracias", 0),
                    # Chat normal reciente sin responder -> DEBE ENTRAR
                    ("chat_reciente_sin_respuesta@c.us", "Cliente Nuevo", recent_ts, "Hola ayuda", 0),
                ]
            )

        db_manager.upsert_contact_meta("ticket_respondido@c.us", ticket_status="open", db_path=self.db_path)
        db_manager.upsert_contact_meta("ticket_viejo_sin_respuesta@c.us", ticket_status="open", db_path=self.db_path)

        unanswered = db_manager.get_unanswered_chats(limit=80, max_age_days=7, db_path=self.db_path)
        ids = {c["chat_id"] for c in unanswered}

        # Comprobaciones clave
        self.assertNotIn("ticket_respondido@c.us", ids, "Un ticket open no debe figurar en el Radar si ultimo_es_mio = 1")
        self.assertIn("ticket_viejo_sin_respuesta@c.us", ids, "Un ticket open sin responder debe permanecer en el Radar aunque tenga > 7 días")
        self.assertNotIn("chat_viejo_normal@c.us", ids, "Un chat normal sin ticket open > 7 días debe quedar fuera")
        self.assertIn("chat_reciente_sin_respuesta@c.us", ids, "Un chat reciente sin responder debe figurar en el Radar")

    def test_03_urgency_ordering_and_kanban_priority(self):
        """Valida que el Radar y el Kanban ordenen primero por urgencia (support_priority DESC) y luego por espera más antigua (ultimo_timestamp ASC)."""
        now = int(time.time())

        # Tres clientes esperando respuesta
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                [
                    ("normal_espera_larga@c.us", "Normal Espera 2h", now - 7200, "Consulta normal", 0),
                    ("normal_espera_corta@c.us", "Normal Espera 10m", now - 600, "Consulta normal reciente", 0),
                    ("alta_espera_media@c.us", "Alta Espera 30m", now - 1800, "Problema grave", 0),
                    ("urgente_reciente@c.us", "Urgente Espera 5m", now - 300, "Servicio caído", 0),
                ]
            )

        db_manager.upsert_contact_meta("normal_espera_larga@c.us", ticket_status="open", support_priority=0, db_path=self.db_path)
        db_manager.upsert_contact_meta("normal_espera_corta@c.us", ticket_status="open", support_priority=0, db_path=self.db_path)
        db_manager.upsert_contact_meta("alta_espera_media@c.us", ticket_status="open", support_priority=1, db_path=self.db_path)
        db_manager.upsert_contact_meta("urgente_reciente@c.us", ticket_status="open", support_priority=2, db_path=self.db_path)

        unanswered = db_manager.get_unanswered_chats(limit=80, max_age_days=7, db_path=self.db_path)
        ordered_ids = [c["chat_id"] for c in unanswered]

        # 1. El urgente (2) va primero a pesar de ser el más reciente
        self.assertEqual(ordered_ids[0], "urgente_reciente@c.us")
        # 2. El alta (1) va segundo
        self.assertEqual(ordered_ids[1], "alta_espera_media@c.us")
        # 3. Entre los dos normales (0), va primero el que lleva más tiempo esperando (7200s vs 600s -> ASC)
        self.assertEqual(ordered_ids[2], "normal_espera_larga@c.us")
        self.assertEqual(ordered_ids[3], "normal_espera_corta@c.us")

        # Kanban de soporte
        kanban = db_manager.get_kanban_board(board_type="support", db_path=self.db_path)
        open_col = next(c for c in kanban["columns"] if c["id"] == "open")
        self.assertEqual(len(open_col["cards"]), 4)
        # La tarjeta urgente debe tener support_priority = 2
        urg_card = open_col["cards"][0]
        self.assertEqual(urg_card["chat_id"], "urgente_reciente@c.us")
        self.assertEqual(urg_card["support_priority"], 2)

    def test_04_sse_hub_instant_ghost_cleanup_on_chat_switch(self):
        """Valida que SSEHub elimine al cliente de cualquier otro chat inmediatamente (0ms) al registrar presencia en uno nuevo."""
        hub = sse_hub.SSEHub()

        client_id = "client_test_dario"
        operator = "Dario"

        # 1. Dario enfoca en chat A
        hub.record_presence(chat_id="chat_A@c.us", operator=operator, client_id=client_id, source="visor")
        active_1 = hub.get_active_presence()
        self.assertIn("chat_A@c.us", active_1)
        self.assertNotIn("chat_B@c.us", active_1)

        # 2. Dario cambia inmediatamente a chat B
        hub.record_presence(chat_id="chat_B@c.us", operator=operator, client_id=client_id, source="visor")
        active_2 = hub.get_active_presence()

        # chat_A debe quedar completamente libre (sin fantasma de 25-40s)
        self.assertNotIn("chat_A@c.us", active_2, "chat_A debe quedar vacío en 0ms cuando el cliente pasa a chat_B")
        self.assertIn("chat_B@c.us", active_2)
        entry_b = active_2["chat_B@c.us"]
        self.assertEqual(entry_b["count"], 1)
        self.assertEqual(entry_b["operator"], "Dario")


if __name__ == "__main__":
    unittest.main()
