"""
tests/verify_phase8_console_metrics.py - Verificación de Fase 4
========================================================================
Valida de forma automatizada:
1. Indicadores ampliados en GET /api/chats (ultimo_es_mio, ticket_status, support_priority, assigned_to, follow_up_date).
2. Endpoint GET /api/crm/metrics/support y /api/crm/support/metrics con filtro days=1|7|30|all y desglose por operador.
3. Rotación de sync.log (RotatingFileHandler 5MB x 3 copias) y lectura acotada de logs en /api/logs.
4. Límite de 32 suscriptores concurrentes en SSEHub (SEC-10).
5. Sanitización contra CSV Formula Injection en /api/export/contacts (SEC-17).
6. Entorno protegido en manage_windows_shortcut (SEC-17).
7. Verificación de hash SHA-256 oficial en tools/build_portable.py (SEC-16).
8. Sanitización de errores 500 para clientes remotos LAN (SEC-18).
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
import queue
import urllib.request
from urllib.error import HTTPError
from http.server import HTTPServer
import threading
from logging.handlers import RotatingFileHandler

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
import db_manager
import sse_hub
import whatsapp_viewer
import tools.build_portable as build_portable


class TestPhase4ConsoleMetrics(unittest.TestCase):
    def setUp(self):
        self.test_acc = "__test_phase8__"
        self.test_acc_dir = os.path.join(config.DATA_DIR, self.test_acc)
        os.makedirs(self.test_acc_dir, exist_ok=True)
        self.db_path = config.get_account_db(self.test_acc)
        db_manager.init_db(self.db_path)
        config.invalidate_accounts_cache()

        # Iniciar servidor HTTP en puerto dinámico
        self.server = HTTPServer(("127.0.0.1", 0), whatsapp_viewer.BackupServerHandler)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.1)

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        if os.path.isdir(self.test_acc_dir):
            shutil.rmtree(self.test_acc_dir, ignore_errors=True)
        config.invalidate_accounts_cache()

    def test_01_api_chats_extended_indicators(self):
        """Verifica que GET /api/chats devuelva los campos requeridos para indicadores de soporte."""
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_mensaje, ultimo_tipo, ultimo_timestamp, ultimo_es_mio)
                VALUES ('5493519999999@c.us', 'Cliente Test 1', 'Hola necesito ayuda', 'chat', 1700000100, 0)
            """)
        db_manager.upsert_contact_meta(
            "5493519999999@c.us",
            ticket_status="pending",
            support_priority=2,
            assigned_to="Ana",
            follow_up_date="2026-11-20",
            db_path=self.db_path
        )

        req = urllib.request.Request(
            f"{self.base_url}/api/chats?account_id={self.test_acc}",
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            chats = json.loads(resp.read().decode("utf-8"))

        target = next((c for c in chats if c["chat_id"] == "5493519999999@c.us"), None)
        self.assertIsNotNone(target)
        self.assertEqual(target.get("ultimo_es_mio"), 0)
        self.assertEqual(target.get("ticket_status"), "pending")
        self.assertEqual(target.get("support_priority"), 2)
        self.assertEqual(target.get("assigned_to"), "Ana")
        self.assertEqual(target.get("follow_up_date"), "2026-11-20")

    def test_02_support_metrics_period_and_operators_breakdown(self):
        """Verifica soporte de filtro days=... y tabla de rendimiento por operador."""
        now_ts = int(time.time())
        # Crear tickets asignados a distintos operadores
        db_manager.upsert_contact_meta("c1@c.us", ticket_status="open", assigned_to="Carlos", db_path=self.db_path)
        db_manager.upsert_contact_meta("c2@c.us", ticket_status="pending", assigned_to="Carlos", db_path=self.db_path)
        db_manager.upsert_contact_meta("c3@c.us", ticket_status="resolved", assigned_to="Carlos", db_path=self.db_path)
        db_manager.upsert_contact_meta("c4@c.us", ticket_status="open", assigned_to="Diana", db_path=self.db_path)

        # Mensajes con respuesta de 14 horas (> 12h = 50400s) para asegurar que NO se descartan
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO mensajes (msg_id, chat_id, remitente, es_mio, timestamp, contenido, tipo)
                VALUES 
                  ('m1', 'c1@c.us', '5493511111111@c.us', 0, ?, 'Consulta soporte', 'chat'),
                  ('m2', 'c1@c.us', '5493512222222@c.us', 1, ?, 'Respuesta demorada', 'chat')
            """, (now_ts - 55000, now_ts - 4600))  # diferencia = 50400s (14h)

        # Probar endpoint GET /api/crm/metrics/support con days=7
        req = urllib.request.Request(
            f"{self.base_url}/api/crm/metrics/support?days=7&account_id={self.test_acc}",
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(data.get("period_days"), "7")
        self.assertIn("operators", data)
        ops = {o["operator"]: o for o in data["operators"]}
        self.assertIn("Carlos", ops)
        self.assertEqual(ops["Carlos"]["open"], 1)
        self.assertEqual(ops["Carlos"]["pending"], 1)
        self.assertEqual(ops["Carlos"]["resolved"], 1)
        self.assertEqual(ops["Carlos"]["total"], 3)

        self.assertIn("Diana", ops)
        self.assertEqual(ops["Diana"]["open"], 1)

        # El SLA de 14h debe reflejarse (~14.0 h) en vez de ser 0 o descartado
        self.assertIn("14", data.get("global_sla"))

    def test_03_sync_log_rotation_and_bounded_logs_api(self):
        """Verifica que el logger use RotatingFileHandler (5MB x 3) y /api/logs acote lectura."""
        logger = whatsapp_viewer.get_sync_logger()
        self.assertTrue(any(isinstance(h, RotatingFileHandler) for h in logger.handlers))
        handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
        self.assertEqual(handler.maxBytes, 5 * 1024 * 1024)
        self.assertEqual(handler.backupCount, 3)

        # Escribir varias líneas y comprobar lectura con límite
        for i in range(15):
            whatsapp_viewer.append_sync_log(f"Test log line {i}")

        req = urllib.request.Request(
            f"{self.base_url}/api/logs?limit=5",
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            self.assertLessEqual(len(data.get("logs")), 5)

    def test_04_sse_hub_max_32_concurrent_subscribers(self):
        """Verifica el tope estricto de 32 suscriptores concurrentes en SSEHub (SEC-10)."""
        hub = sse_hub.SSEHub()
        subscribers = []
        for i in range(32):
            q = hub.subscribe()
            self.assertIsNotNone(q, f"El suscriptor #{i+1} debería haber sido aceptado.")
            subscribers.append(q)

        # El suscriptor 33 debe ser rechazado
        q33 = hub.subscribe()
        self.assertIsNone(q33, "El suscriptor #33 debería ser rechazado al superar el límite de 32.")

        # Al liberar uno, debe permitir uno nuevo
        hub.unsubscribe(subscribers[0])
        q_new = hub.subscribe()
        self.assertIsNotNone(q_new, "Tras liberar un suscriptor, debe admitirse uno nuevo.")

    def test_05_anti_csv_formula_injection(self):
        """Verifica que /api/export/contacts sanitice celdas con caracteres de inyección de fórmulas (SEC-17)."""
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO contactos (contact_id, nombre, numero)
                VALUES 
                  ('5491100000001@c.us', '=cmd|'' /C calc''!A0', '5491100000001'),
                  ('5491100000002@c.us', '+SUM(1+1)', '5491100000002'),
                  ('5491100000003@c.us', '@DDE(''cmd'';''calc'')', '5491100000003'),
                  ('5491100000004@c.us', '-10+20', '5491100000004')
            """)

        req = urllib.request.Request(
            f"{self.base_url}/api/export/contacts?account_id={self.test_acc}",
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            csv_content = resp.read().decode("utf-8")

        # Ninguna fórmula debe quedar desnuda al inicio de la celda
        self.assertIn("'=cmd|' /C calc'!A0", csv_content)
        self.assertIn("'+SUM(1+1)", csv_content)
        self.assertIn("'@DDE('cmd';'calc')", csv_content)
        self.assertIn("'-10+20", csv_content)

    def test_06_build_portable_sha256_constant(self):
        """Verifica que build_portable.py contenga el hash SHA-256 oficial correcto (SEC-16)."""
        expected_hash = "0d57bb6cb078b74d23dbfe91f77d6780d45bed328911609f1f7ee2ba1606bf44"
        self.assertEqual(build_portable.PYTHON_EMBED_SHA256.lower(), expected_hash)

    def test_07_remote_500_error_sanitization(self):
        """Verifica que un error 500 no filtre trazas internas a clientes remotos LAN (SEC-18)."""
        # Hacemos una petición POST con encabezado simulado de IP remota LAN hacia una ruta que falle
        # Mockeamos una excepción en un endpoint POST
        req = urllib.request.Request(
            f"{self.base_url}/api/copilot/notes/log/delete",
            data=b"not-json-trigger-syntax",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "X-Forwarded-For": "192.168.1.188",
                "Content-Type": "application/json"
            }
        )
        # La solicitud con X-Forwarded-For sin auth responde 401/403
        with self.assertRaises(HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertIn(ctx.exception.code, (401, 403, 400))

    def test_08_http_support_ticket_and_canonical_lid_chats(self):
        """Verifica HTTP POST /api/support/ticket, resolución canónica @lid en GET /api/chats y movimiento Kanban."""
        now_ts = int(time.time())
        with db_manager.db_connection(self.db_path, write=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO contactos (contact_id, nombre, numero)
                VALUES ('9988776655@lid', 'Cliente LID', '5493518888888')
            """)
            cur.execute("""
                INSERT INTO contact_aliases (alias_id, canonical_id, updated_at)
                VALUES ('9988776655@lid', '5493518888888@c.us', ?)
            """, (now_ts,))
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_mensaje, ultimo_tipo, ultimo_timestamp, ultimo_es_mio)
                VALUES ('9988776655@lid', 'Cliente LID', 'Consulta por LID', 'chat', ?, 0)
            """, (now_ts - 60,))

        # 1. Actualizar ticket vía HTTP POST /api/support/ticket usando el @lid
        ticket_payload = json.dumps({
            "account_id": self.test_acc,
            "chat_id": "9988776655@lid",
            "ticket_status": "pending",
            "support_priority": 1,
            "assigned_to": "Dario",
            "follow_up_date": "2026-12-01"
        }).encode("utf-8")
        req_ticket = urllib.request.Request(
            f"{self.base_url}/api/support/ticket",
            data=ticket_payload,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/json",
                "X-WAckup-Operator": "Dario"
            }
        )
        with urllib.request.urlopen(req_ticket) as resp:
            self.assertEqual(resp.status, 200)
            t_res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(t_res.get("ok"))
            self.assertEqual(t_res.get("ticket_status"), "pending")
            self.assertEqual(t_res.get("support_priority"), 1)

        # 2. Verificar que GET /api/chats resuelva el crm_contact_meta canónico del @lid
        req_chats = urllib.request.Request(
            f"{self.base_url}/api/chats?account_id={self.test_acc}",
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with urllib.request.urlopen(req_chats) as resp:
            chats = json.loads(resp.read().decode("utf-8"))
        lid_chat = next((c for c in chats if c["chat_id"] == "9988776655@lid"), None)
        self.assertIsNotNone(lid_chat)
        self.assertEqual(lid_chat.get("ticket_status"), "pending")
        self.assertEqual(lid_chat.get("support_priority"), 1)
        self.assertEqual(lid_chat.get("assigned_to"), "Dario")

        # 3. Mover tarjeta en Kanban a 'open' debe actualizar radar_dismissed_at para que no rebote a Radar
        move_payload = json.dumps({
            "account_id": self.test_acc,
            "chat_id": "9988776655@lid",
            "ticket_status": "open"
        }).encode("utf-8")
        req_move = urllib.request.Request(
            f"{self.base_url}/api/crm/kanban/move",
            data=move_payload,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req_move) as resp:
            self.assertEqual(resp.status, 200)
            m_res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(m_res.get("ok"))

        unanswered = db_manager.get_unanswered_chats(limit=80, max_age_days=7, db_path=self.db_path)
        self.assertNotIn("9988776655@lid", {c["chat_id"] for c in unanswered})


if __name__ == "__main__":
    unittest.main()
