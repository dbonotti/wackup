import os
import sys
import time
import json
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import db_manager
import whatsapp_viewer


class TestRadarManagement(unittest.TestCase):
    """
    Pruebas integrales de administración del Radar (Opción 3 Híbrida):
    - Límite de antigüedad de 7 días en Pendientes (respetando tickets 'open')
    - Descarte temporal (reaparece automáticamente si entra mensaje nuevo)
    - Exclusión permanente (personal / spam) y reactivación
    - Bloqueo estricto de permisos cuando el chat pertenece a otro operador (403)
    - Resolución comercial en Seguimientos (ganada, perdida, posponer +3d, no presupuesto)
    - Deshacer (Undo) atómico restaurando estado y limpiando bitácora automática
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_radar.db")
        db_manager.init_db(self.db_path)

    def tearDown(self):
        with db_manager._INITIALIZED_LOCK:
            db_manager._INITIALIZED_DBS.discard(os.path.abspath(self.db_path))
        self.tmp_dir.cleanup()

    def test_01_seven_day_limit_and_open_tickets(self):
        now = int(time.time())
        recent_ts = now - 3600            # Hace 1h -> entra
        old_ts = now - (9 * 86400)        # Hace 9 días -> no entra salvo ticket 'open'

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                [
                    ("reciente@c.us", "Cliente Reciente", recent_ts, "Hola necesito ayuda", 0),
                    ("viejo_normal@c.us", "Chat Viejo", old_ts, "Ok gracias", 0),
                    ("viejo_ticket@c.us", "Ticket Antiguo Abierto", old_ts, "Sigo esperando", 0),
                ]
            )
        db_manager.upsert_contact_meta("viejo_ticket@c.us", ticket_status="open", db_path=self.db_path)

        pending = db_manager.get_unanswered_chats(limit=30, max_age_days=7, db_path=self.db_path)
        ids = {c["chat_id"] for c in pending}

        self.assertIn("reciente@c.us", ids)
        self.assertIn("viejo_ticket@c.us", ids)
        self.assertNotIn("viejo_normal@c.us", ids)

    def test_02_temporal_dismiss_reappears_on_new_message_and_undo(self):
        now = int(time.time())
        t1 = now - 600

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                ("cliente1@c.us", "Veronica Gil", t1, "Si, gracias!", 0)
            )
        db_manager.upsert_contact_meta("cliente1@c.us", ticket_status="open", db_path=self.db_path)

        # Descartar con 1 clic
        res = db_manager.dismiss_radar_chat(
            "cliente1@c.us", mode="unanswered", reason="no_reply_needed",
            operator_name="Dario", db_path=self.db_path
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["meta"]["ticket_status"], "resolved")

        # Ya no aparece en pendientes
        pending = db_manager.get_unanswered_chats(db_path=self.db_path)
        self.assertEqual(len(pending), 0)

        # Descarte rápido no agrega ruido en bitácora
        ctx = db_manager.get_copilot_context("cliente1@c.us", db_path=self.db_path)
        self.assertEqual(len(ctx["notes_log"]), 0)

        # Probar Deshacer (Undo): restaura estado previo y limpia la nota automática
        undo_res = db_manager.undo_radar_dismiss(
            "cliente1@c.us", res["prev_state"], operator_name="Dario", db_path=self.db_path
        )
        self.assertTrue(undo_res["ok"])
        self.assertEqual(undo_res["meta"]["ticket_status"], "open")
        self.assertEqual(len(db_manager.get_unanswered_chats(db_path=self.db_path)), 1)
        ctx_after_undo = db_manager.get_copilot_context("cliente1@c.us", db_path=self.db_path)
        self.assertEqual(len(ctx_after_undo["notes_log"]), 0)

        # Volver a descartar y simular un NUEVO mensaje entrante posterior
        db_manager.dismiss_radar_chat(
            "cliente1@c.us", mode="unanswered", reason="no_reply_needed",
            operator_name="Dario", db_path=self.db_path
        )
        self.assertEqual(len(db_manager.get_unanswered_chats(db_path=self.db_path)), 0)

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chats SET ultimo_timestamp = ?, ultimo_mensaje = 'Nueva consulta urgente', ultimo_es_mio = 0 WHERE chat_id = ?",
                (t1 + 120, "cliente1@c.us")
            )

        # Reaparece automáticamente al recibir un mensaje posterior al descarte
        pending_again = db_manager.get_unanswered_chats(db_path=self.db_path)
        self.assertEqual(len(pending_again), 1)
        self.assertEqual(pending_again[0]["chat_id"], "cliente1@c.us")

    def test_03_permanent_ignore_and_reactivate(self):
        now = int(time.time())
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                ("familia@c.us", "Las Rodriguez", now - 300, "Siii querido!", 0)
            )

        res = db_manager.dismiss_radar_chat(
            "familia@c.us", mode="unanswered", reason="ignore_personal",
            operator_name="Dario", db_path=self.db_path
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["meta"]["radar_ignored"], 1)

        # Aunque envíe nuevos mensajes, sigue ignorado
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chats SET ultimo_timestamp = ?, ultimo_mensaje = 'Otro mensaje familiar' WHERE chat_id = ?",
                (now + 60, "familia@c.us")
            )
        self.assertEqual(len(db_manager.get_unanswered_chats(db_path=self.db_path)), 0)

        # Reactivar desde Gestión
        re_res = db_manager.dismiss_radar_chat(
            "familia@c.us", mode="unanswered", reason="reactivate_radar",
            operator_name="Dario", db_path=self.db_path
        )
        self.assertTrue(re_res["ok"])
        self.assertEqual(re_res["meta"]["radar_ignored"], 0)
        self.assertEqual(len(db_manager.get_unanswered_chats(db_path=self.db_path)), 1)

    def test_04_operator_ownership_blocking_and_sales_followups(self):
        now = int(time.time())
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                [
                    ("ajeno@c.us", "Cliente de Ana", now - 120, "Hola Ana", 0),
                    ("venta1@c.us", "Prospecto Cotizado", now - 7200, "Te paso presupuesto $500", 1),
                ]
            )

        db_manager.upsert_contact_meta("ajeno@c.us", assigned_to="Ana", db_path=self.db_path)
        db_manager.upsert_contact_meta("venta1@c.us", sales_stage="quoted", estimated_value=500.0, db_path=self.db_path)

        # Otro operador ("Carlos") o vacío ("") NO puede descartar el chat de Ana
        blocked_other = db_manager.dismiss_radar_chat("ajeno@c.us", operator_name="Carlos", db_path=self.db_path)
        self.assertFalse(blocked_other["ok"])
        self.assertTrue(blocked_other.get("forbidden"))

        blocked_empty = db_manager.dismiss_radar_chat("ajeno@c.us", operator_name="", db_path=self.db_path)
        self.assertFalse(blocked_empty["ok"])
        self.assertTrue(blocked_empty.get("forbidden"))

        # La propia Ana ("ana", case-insensitive) SÍ puede descartarlo
        allowed_owner = db_manager.dismiss_radar_chat("ajeno@c.us", operator_name="ana", db_path=self.db_path)
        self.assertTrue(allowed_owner["ok"])

        # Seguimiento comercial: Posponer +3 días lo saca de los pendientes de hoy
        leads_before = db_manager.get_sales_followups(db_path=self.db_path)
        self.assertEqual(len(leads_before), 1)

        postpone_res = db_manager.dismiss_radar_chat(
            "venta1@c.us", mode="sales", reason="postpone_3d",
            operator_name="Carlos", db_path=self.db_path
        )
        self.assertTrue(postpone_res["ok"])
        self.assertEqual(len(db_manager.get_sales_followups(db_path=self.db_path)), 0)

        # Si vence hoy, vuelve a entrar y al marcar "sale_won" pasa a 'won' y sale del radar
        today_str = time.strftime("%Y-%m-%d", time.localtime(now))
        db_manager.upsert_contact_meta("venta1@c.us", follow_up_date=today_str, db_path=self.db_path)
        self.assertEqual(len(db_manager.get_sales_followups(db_path=self.db_path)), 1)

        won_res = db_manager.dismiss_radar_chat(
            "venta1@c.us", mode="sales", reason="sale_won",
            operator_name="Carlos", db_path=self.db_path
        )
        self.assertTrue(won_res["ok"])
        self.assertEqual(won_res["meta"]["sales_stage"], "won")
        self.assertEqual(len(db_manager.get_sales_followups(db_path=self.db_path)), 0)

    def test_04_lid_without_name_displays_phone_number(self):
        now = int(time.time())
        lid_jid = "125954244481120@lid"
        phone = "5493512042412"
        phone_jid = f"{phone}@c.us"

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            # Chat guardado con el LID como nombre por defecto de WhatsApp
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                (lid_jid, lid_jid, now - 100, "📦 Cama", 0)
            )
            # Contacto registrado con el número conocido
            cur.execute(
                "INSERT INTO contactos (contact_id, numero, nombre, push_name) VALUES (?, ?, ?, ?)",
                (lid_jid, phone, None, None)
            )

        # 1. get_unanswered_chats debe retornar el número formateado, no el LID
        pending = db_manager.get_unanswered_chats(db_path=self.db_path)
        self.assertEqual(len(pending), 1)
        item = pending[0]
        self.assertEqual(item["chat_id"], lid_jid)
        self.assertEqual(item["phone_number"], phone)
        self.assertEqual(item["display_name"], "+54 9 351 204-2412")

        # 2. get_copilot_context debe retornar el número formateado como display_name
        ctx = db_manager.get_copilot_context(lid_jid, db_path=self.db_path)
        self.assertEqual(ctx["display_name"], "+54 9 351 204-2412")
        self.assertEqual(ctx["phone_number"], phone)

        # 3. Prueba de vinculación por remitente si contactos.numero estuviera vacío inicialmente
        lid_jid_2 = "987654321098765@lid"
        phone_2 = "5491122334455"
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                (lid_jid_2, lid_jid_2, now - 50, "Hola", 0)
            )
            cur.execute(
                "INSERT INTO contactos (contact_id, numero, nombre) VALUES (?, ?, ?)",
                (lid_jid_2, None, None)
            )
            cur.execute(
                "INSERT INTO mensajes (msg_id, chat_id, remitente, contenido, es_mio, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                ("m_lid_2", lid_jid_2, f"{phone_2}@s.whatsapp.net", "Hola", 0, now - 50)
            )

        # resolve_lid_phone_numbers debe poblar automáticamente el número
        db_manager.resolve_lid_phone_numbers(db_path=self.db_path)
        ctx2 = db_manager.get_copilot_context(lid_jid_2, db_path=self.db_path)
        self.assertEqual(ctx2["phone_number"], phone_2)
        self.assertEqual(ctx2["display_name"], "+54 9 112 233-4455")

    def test_05_api_chats_endpoint_and_html_export(self):
        # Iniciar servidor HTTP para probar /api/chats
        server = HTTPServer(("127.0.0.1", 0), whatsapp_viewer.BackupServerHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.2)

        lid_jid = "125954244481120@lid"
        phone = "5493512042412"
        now = int(time.time())

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                (lid_jid, lid_jid, now - 100, "📦 Cama", 0)
            )
            cur.execute(
                "INSERT INTO contactos (contact_id, numero, nombre) VALUES (?, ?, ?)",
                (lid_jid, phone, None)
            )

        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chats?db_path={urllib.parse.quote(self.db_path)}")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                chats = json.loads(resp.read().decode("utf-8"))
                self.assertIsInstance(chats, list)
                self.assertGreater(len(chats), 0)
                target = next((c for c in chats if c["chat_id"] == lid_jid), None)
                self.assertIsNotNone(target)
                self.assertEqual(target["display_name"], "+54 9 351 204-2412")
                self.assertEqual(target["phone_number"], phone)

            # Probar también generate_chat_html_export
            html_export = whatsapp_viewer.generate_chat_html_export(lid_jid, db_path=self.db_path)
            self.assertIn("+54 9 351 204-2412", html_export)
        finally:
            server.shutdown()
            server.server_close()

    def test_unsaved_contact_display_name_shows_phone_and_keeps_pushname(self):
        """Verifica que contactos no agendados muestren su teléfono y preserven push_name."""
        unsaved_lid = "100296327987372@lid"
        unsaved_phone = "5492915080263"
        push_name = "Juliana"

        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio) VALUES (?, ?, ?, ?, ?)",
                (unsaved_lid, None, int(time.time()), "Hola", 0)
            )
            cur.execute(
                "INSERT INTO contactos (contact_id, numero, nombre, push_name) VALUES (?, ?, ?, ?)",
                (unsaved_lid, unsaved_phone, None, push_name)
            )

        disp = db_manager.get_best_contact_display(
            contact_name=None,
            push_name=push_name,
            phone_number=unsaved_phone,
            chat_id=unsaved_lid
        )
        self.assertEqual(disp, "+54 9 291 508-0263")

        ctx = db_manager.get_copilot_context(unsaved_lid, db_path=self.db_path)
        self.assertEqual(ctx["display_name"], "+54 9 291 508-0263")
        self.assertEqual(ctx["push_name"], "Juliana")
        self.assertFalse(ctx["is_saved"])

    def test_06_courtesy_filter_ignored_tags_and_bulk_dismiss(self):
        import config
        self.assertTrue(db_manager.is_courtesy_closing_message("Gracias!", "chat"))
        self.assertTrue(db_manager.is_courtesy_closing_message("Ok", "chat"))
        self.assertTrue(db_manager.is_courtesy_closing_message("👍🙏", "chat"))
        self.assertFalse(db_manager.is_courtesy_closing_message("Gracias, ¿a qué hora pasan?", "chat"))
        self.assertFalse(db_manager.is_courtesy_closing_message("Mi pedido es 12345", "chat"))
        self.assertFalse(db_manager.is_courtesy_closing_message("Gracias", "image"))

        now = int(time.time())
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_tipo, ultimo_es_mio) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("cortesia@c.us", "Cortesia", now - 60, "Muchas gracias!", "chat", 0),
                    ("consulta@c.us", "Consulta Real", now - 120, "¿Cuánto sale?", "chat", 0),
                    ("etiquetado@c.us", "Personal", now - 180, "Nos vemos hoy", "chat", 0),
                    ("ajeno_bulk@c.us", "De Otro", now - 240, "Necesito soporte", "chat", 0),
                ]
            )
            cur.execute("INSERT INTO crm_chat_tags (chat_id, tag, category) VALUES ('etiquetado@c.us', 'Personal', 'general')")

        db_manager.upsert_contact_meta("ajeno_bulk@c.us", assigned_to="Ana", db_path=self.db_path)

        orig_settings = config.get_system_settings()
        try:
            config.update_system_settings(radar_auto_hide_courtesy=True, radar_ignored_tags=["Personal"])
            pending = db_manager.get_unanswered_chats(db_path=self.db_path)
            ids = {c["chat_id"] for c in pending}
            self.assertNotIn("cortesia@c.us", ids)
            self.assertNotIn("etiquetado@c.us", ids)
            self.assertIn("consulta@c.us", ids)
            self.assertIn("ajeno_bulk@c.us", ids)

            # Descarte masivo por Dario: limpia consulta@c.us, omite ajeno_bulk@c.us (de Ana) y deja nota en bitácora
            bulk_res = db_manager.dismiss_radar_bulk(
                operator_name="Dario", reason_label="Todo al día", db_path=self.db_path
            )
            self.assertTrue(bulk_res["ok"])
            self.assertIn("consulta@c.us", bulk_res["dismissed_chat_ids"])
            self.assertIn("ajeno_bulk@c.us", bulk_res["skipped_assigned_ids"])
            ctx_consulta = db_manager.get_copilot_context("consulta@c.us", db_path=self.db_path)
            self.assertEqual(len(ctx_consulta["notes_log"]), 1)
            self.assertIn("Descarte masivo", ctx_consulta["notes_log"][0]["content"])
        finally:
            config.save_system_settings(orig_settings)


if __name__ == "__main__":
    unittest.main()
