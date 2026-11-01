# -*- coding: utf-8 -*-
"""
tests/verify_phase1.py - Suite exhaustiva de pruebas automáticas para la FASE 1 de WAckup 3.0.
Verifica:
1. Cálculo exacto de SLA con chats intercalados (PARTITION BY chat_id).
2. Unificación canónica @lid + @c.us (fusión de notas, tickets y 1 sola tarjeta Kanban).
3. Alta concurrencia multihilo sin 'database is locked' ni 'Lost Update'.
4. Extracción de DNI con prefijo estricto obligatorio.
5. Peticiones HTTP reales: estáticos (HTML/CSS/JS), /api/copilot/context con profile y /api/copilot/tags.
"""

import os
import sys
import time
import json
import shutil
import sqlite3
import threading
import unittest
import urllib.request
import urllib.parse
from http.server import HTTPServer

# Asegurar importación de módulos raíz
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
import db_manager
import entity_extractor
from whatsapp_viewer import BackupServerHandler

TEST_DATA_DIR = os.path.join(ROOT_DIR, "tests", "temp_data_phase1")
TEST_DB = os.path.join(TEST_DATA_DIR, "test_whatsapp.db")


class TestPhase1(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs(TEST_DATA_DIR, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        # Limpiar directorio de pruebas
        if os.path.isdir(TEST_DATA_DIR):
            try:
                shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
            except Exception:
                pass

    def setUp(self):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass
        # Inicializar base de prueba
        db_manager.ensure_db_initialized(TEST_DB)

    def test_01_sla_partition_no_chat_mixing(self):
        """
        Prueba que el cálculo de SLA usa PARTITION BY chat_id y NO mezcla
        los timestamps de dos conversaciones intercaladas en el tiempo.
        """
        # Chat A:
        # 10:00 (ts=1000) -> Cliente envía (es_mio=0)
        # 10:10 (ts=1600) -> Operador responde (es_mio=1). Delta = 600 seg
        #
        # Chat B:
        # 10:02 (ts=1120) -> Cliente envía (es_mio=0)
        # 10:05 (ts=1300) -> Operador responde (es_mio=1). Delta = 180 seg
        #
        # Si se intercalaran sin PARTITION BY:
        # 1000 (A, 0)
        # 1120 (B, 0)
        # 1300 (B, 1) -> 1300 - 1120 = 180
        # 1600 (A, 1) -> 1600 - 1300 = 300 (mezclado erróneamente con B)
        # Promedio erróneo = (180 + 300) / 2 = 240 seg (~4 min).
        #
        # Con PARTITION BY chat_id:
        # Chat A delta = 1600 - 1000 = 600
        # Chat B delta = 1300 - 1120 = 180
        # Promedio exacto = (600 + 180) / 2 = 390 seg (~6 min).

        with db_manager.db_connection(TEST_DB, write=True) as conn:
            cur = conn.cursor()
            # Chats
            cur.execute("INSERT INTO chats (chat_id, nombre, ultimo_timestamp) VALUES ('chat_a@c.us', 'Cliente A', 1600)")
            cur.execute("INSERT INTO chats (chat_id, nombre, ultimo_timestamp) VALUES ('chat_b@c.us', 'Cliente B', 1300)")

            # Mensajes intercalados cronológicamente
            messages = [
                ("msg_a1", "chat_a@c.us", "chat_a@c.us", 0, 1000, "Hola A"),
                ("msg_b1", "chat_b@c.us", "chat_b@c.us", 0, 1120, "Hola B"),
                ("msg_b2", "chat_b@c.us", "me", 1, 1300, "Respuesta B"),
                ("msg_a2", "chat_a@c.us", "me", 1, 1600, "Respuesta A")
            ]
            cur.executemany("""
                INSERT INTO mensajes (msg_id, chat_id, remitente, es_mio, timestamp, contenido, tipo)
                VALUES (?, ?, ?, ?, ?, ?, 'chat')
            """, messages)

        metrics = db_manager.get_support_metrics(db_path=TEST_DB)
        sla_str = metrics.get("global_sla")

        # Con PARTITION BY, el promedio exacto es 390 segundos -> 390 // 60 = 6 min -> '~6 min'
        self.assertEqual(sla_str, "~6 min", f"El SLA obtenido fue {sla_str}, esperado '~6 min'. Posible mezcla entre chats.")

    def test_02_canonical_identity_unification(self):
        """
        Prueba que interactuar con una identidad @lid y su número canónico @c.us
        produce una única entrada en crm_contact_meta y 1 sola tarjeta en Kanban.
        """
        lid_id = "9988776655@lid"
        canon_id = "5492915551111@c.us"
        phone_num = "5492915551111"

        # 1. Registrar el contacto en la BD con su número y @lid
        with db_manager.db_connection(TEST_DB, write=True) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO contactos (contact_id, nombre, push_name, numero)
                VALUES (?, 'Juan Perez', 'Juan', ?)
            """, (lid_id, phone_num))
            cur.execute("""
                INSERT INTO chats (chat_id, nombre, ultimo_timestamp, ultimo_mensaje, ultimo_es_mio)
                VALUES (?, 'Juan Perez', 1700000000, 'Hola soporte', 0)
            """, (canon_id,))

        # 2. Guardar nota bajo @lid
        meta1 = db_manager.upsert_contact_meta(
            lid_id,
            notes="Nota confidencial del cliente.",
            db_path=TEST_DB
        )
        self.assertEqual(meta1["chat_id"], canon_id, "upsert_contact_meta no resolvió a canonical_id")

        # 3. Guardar ticket y estado comercial bajo @c.us
        meta2 = db_manager.upsert_contact_meta(
            canon_id,
            ticket_status="open",
            sales_stage="lead",
            estimated_value=1500.0,
            db_path=TEST_DB
        )

        # 4. Verificar que en crm_contact_meta exista EXACTAMENTE 1 fila para esta persona
        with db_manager.db_connection(TEST_DB, write=False) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM crm_contact_meta WHERE chat_id IN (?, ?)", (lid_id, canon_id))
            count = cur.fetchone()[0]
            self.assertEqual(count, 1, "Existen filas duplicadas para @lid y @c.us en crm_contact_meta")

            # Verificar que la nota anterior no se perdió y el ticket se guardó
            cur.execute("SELECT notes, ticket_status, sales_stage, estimated_value FROM crm_contact_meta WHERE chat_id = ?", (canon_id,))
            row = cur.fetchone()
            self.assertEqual(row["notes"], "Nota confidencial del cliente.")
            self.assertEqual(row["ticket_status"], "open")
            self.assertEqual(row["sales_stage"], "lead")
            self.assertEqual(row["estimated_value"], 1500.0)

        # 5. Verificar que el tablero Kanban muestre exactamente 1 sola tarjeta
        kanban_sales = db_manager.get_kanban_board("sales", db_path=TEST_DB)
        total_cards = sum(len(c["cards"]) for c in kanban_sales["columns"])
        self.assertEqual(total_cards, 1, f"El Kanban de ventas debería tener 1 tarjeta, pero tiene {total_cards}")

        kanban_support = db_manager.get_kanban_board("support", db_path=TEST_DB)
        total_support_cards = sum(len(c["cards"]) for c in kanban_support["columns"])
        self.assertEqual(total_support_cards, 1, f"El Kanban de soporte debería tener 1 tarjeta, pero tiene {total_support_cards}")

    def test_03_high_concurrency_no_locks_no_lost_updates(self):
        """
        Prueba 5 hilos simultáneos realizando syncs incrementales y actualizaciones
        parciales de notas y tickets sin OperationalError ni pérdida de actualizaciones.
        """
        chat_id = "5491122334455@c.us"
        errors = []

        # Crear chat inicial
        db_manager.upsert_contact_meta(chat_id, notes="Nota Base", ticket_status="none", db_path=TEST_DB)

        def worker_notes(idx):
            try:
                for i in range(5):
                    db_manager.upsert_contact_meta(
                        chat_id,
                        notes=f"Nota hilo {idx} iter {i}",
                        db_path=TEST_DB
                    )
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"Worker notes {idx}: {e}")

        def worker_tickets(idx):
            try:
                for i in range(5):
                    status = "open" if i % 2 == 0 else "pending"
                    db_manager.upsert_contact_meta(
                        chat_id,
                        ticket_status=status,
                        support_priority=1,
                        db_path=TEST_DB
                    )
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"Worker tickets {idx}: {e}")

        def worker_sync(idx):
            try:
                for i in range(5):
                    sync_data = {
                        "account_id": "test_account",
                        "messages": [{
                            "id": f"msg_thread_{idx}_{i}",
                            "chatId": chat_id,
                            "fromMe": True,
                            "timestamp": int(time.time()),
                            "type": "chat",
                            "body": f"Sync msg {idx}-{i}"
                        }]
                    }
                    db_manager.save_incremental_sync(sync_data, db_path=TEST_DB)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"Worker sync {idx}: {e}")

        threads = [
            threading.Thread(target=worker_notes, args=(1,)),
            threading.Thread(target=worker_notes, args=(2,)),
            threading.Thread(target=worker_tickets, args=(3,)),
            threading.Thread(target=worker_tickets, args=(4,)),
            threading.Thread(target=worker_sync, args=(5,))
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Ocurrieron errores de concurrencia: {errors}")

        # Comprobar que los metadatos mantuvieron integridad
        final_meta = db_manager.get_contact_meta(chat_id, db_path=TEST_DB)
        self.assertIn(final_meta["ticket_status"], ["open", "pending"])
        self.assertEqual(final_meta["support_priority"], 1)
        self.assertTrue(final_meta["notes"].startswith("Nota hilo"))

    def test_04_strict_dni_extraction(self):
        """
        Prueba que entity_extractor exige prefijo obligatorio para DNI y no captura
        precios ni números sueltos de 7-8 dígitos como falsos positivos.
        """
        # Falso positivo típico: un presupuesto o monto de 7-8 dígitos
        text_falso = "El presupuesto total es 14500000 pesos por la remodelación completa."
        res1 = entity_extractor.extract_entities_from_text(text_falso)
        self.assertEqual(len(res1["tax_ids"]), 0, f"Falso positivo detectado: {res1['tax_ids']}")

        # Teléfono suelto de 7-8 dígitos
        text_tel = "Llamame a mi línea directa 4567890 luego de las 18 hs."
        res_tel = entity_extractor.extract_entities_from_text(text_tel)
        self.assertEqual(len(res_tel["tax_ids"]), 0, f"Teléfono suelto detectado como DNI: {res_tel['tax_ids']}")

        # DNI real con prefijo
        text_real_1 = "Mi DNI: 30.123.456 para la factura."
        res2 = entity_extractor.extract_entities_from_text(text_real_1)
        self.assertEqual(len(res2["tax_ids"]), 1)
        self.assertEqual(res2["tax_ids"][0]["type"], "DNI")
        self.assertEqual(res2["tax_ids"][0]["value"], "30.123.456")

        # DNI con documento / doc
        text_real_2 = "Te paso doc: 28999888 y el CUIT 20-28999888-4"
        res3 = entity_extractor.extract_entities_from_text(text_real_2)
        # El CUIT y DNI
        dnis = [t for t in res3["tax_ids"] if t["type"] == "DNI"]
        self.assertEqual(len(dnis), 1)
        self.assertEqual(dnis[0]["value"], "28999888")

    def test_05_http_server_endpoints(self):
        """
        Prueba peticiones HTTP reales al servidor web:
        - GET / (web/index.html)
        - GET /css/styles.css
        - GET /js/app.js
        - GET /api/copilot/context (con profile y profile_label)
        - POST /api/copilot/tags (add y remove)
        """
        # Montar servidor de prueba en puerto efímero
        server = HTTPServer(("127.0.0.1", 0), BackupServerHandler)
        port = server.server_port
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. GET /
            req = urllib.request.Request(f"{base_url}/")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                ct = resp.headers.get("Content-Type", "")
                self.assertIn("text/html", ct)
                body = resp.read().decode("utf-8")
                self.assertIn("<title>WAckup - Visor</title>", body)
                self.assertIn('href="/css/styles.css"', body)
                self.assertIn('src="/js/app.js"', body)

            # 2. GET /css/styles.css
            req_css = urllib.request.Request(f"{base_url}/css/styles.css")
            with urllib.request.urlopen(req_css, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                ct = resp.headers.get("Content-Type", "")
                self.assertIn("text/css", ct)
                css_body = resp.read().decode("utf-8")
                self.assertIn("--bg-body", css_body)

            # 3. GET /js/app.js
            req_js = urllib.request.Request(f"{base_url}/js/app.js")
            with urllib.request.urlopen(req_js, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                ct = resp.headers.get("Content-Type", "")
                self.assertIn("application/javascript", ct)
                js_body = resp.read().decode("utf-8")
                self.assertIn("function renderCopilotTags", js_body)

            # 4. GET /api/copilot/context
            # Crear un chat de prueba
            chat_id = "5492914445555@c.us"
            db_manager.upsert_contact_meta(chat_id, notes="Test context", db_path=config.get_default_db())

            req_ctx = urllib.request.Request(f"{base_url}/api/copilot/context?chat_query={chat_id}")
            with urllib.request.urlopen(req_ctx, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok", False))
                self.assertIn("profile", data, "Falta campo profile en /api/copilot/context")
                self.assertIn("profile_label", data, "Falta campo profile_label en /api/copilot/context")
                self.assertIn(data["profile"], config.VALID_WORKSPACE_PROFILES)

            # 5. POST /api/copilot/tags (Agregar)
            payload_add = json.dumps({
                "chat_id": chat_id,
                "tag": "Cliente VIP",
                "category": "sales",
                "action": "add"
            }).encode("utf-8")
            req_tag_add = urllib.request.Request(
                f"{base_url}/api/copilot/tags",
                data=payload_add,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req_tag_add, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                res_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res_data.get("ok"))
                tags = [t["tag"] for t in res_data.get("tags", [])]
                self.assertIn("Cliente VIP", tags)

            # 6. POST /api/copilot/tags (Remover)
            payload_rem = json.dumps({
                "chat_id": chat_id,
                "tag": "Cliente VIP",
                "action": "remove"
            }).encode("utf-8")
            req_tag_rem = urllib.request.Request(
                f"{base_url}/api/copilot/tags",
                data=payload_rem,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req_tag_rem, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                res_data2 = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res_data2.get("ok"))
                tags2 = [t["tag"] for t in res_data2.get("tags", [])]
                self.assertNotIn("Cliente VIP", tags2)

        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
