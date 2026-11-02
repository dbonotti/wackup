"""
tests/verify_phase4_lan.py
Suite de verificación exhaustiva para la Fase 4 de WAckup:
1. Configuración de Red LAN y Middleware de Seguridad con PIN (config.py y whatsapp_viewer.py).
2. Semáforo Anti-Colisión y Presencia en tiempo real vía Server-Sent Events (SSE) (sse_hub.py).
3. Concurrencia Optimista en Nota Fijada (409 Conflict) y Bitácora de Equipo (crm_contact_notes_log).
4. Asignación de Responsable y filtrado colaborativo en Kanban.
5. Cierre limpio e inmediato del servidor con clientes SSE conectados.
"""

import os
import sys
import json
import time
import socket
import shutil
import tempfile
import threading
import unittest
import copy
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
import db_manager
import sse_hub
import whatsapp_viewer

def test_system_settings_and_network_config():
    print("[1/5] Verificando configuracion de Red LAN y deteccion de IPs...")

    settings = config.get_system_settings()
    assert "network_mode" in settings, "Falta network_mode en system_settings"
    assert "lan_pin" in settings, "Falta lan_pin en system_settings"
    assert "known_operators" in settings, "Falta known_operators en system_settings"
    assert len(settings["lan_pin"]) >= 4, "El PIN generado debe tener al menos 4 digitos"
    assert settings["lan_pin"].isdigit(), "El PIN debe ser numerico"

    # Verificar deteccion de IPs privadas
    local_ips = config.get_local_ip_addresses()
    assert isinstance(local_ips, list), "get_local_ip_addresses() debe retornar una lista"
    assert len(local_ips) > 0, "Debe retornar al menos una direccion IP o fallback"

    # Verificar clasificador de IPs privadas
    assert config.is_private_ip("127.0.0.1") is True, "127.0.0.1 debe ser privada"
    assert config.is_private_ip("192.168.1.100") is True, "192.168.1.100 debe ser privada"
    assert config.is_private_ip("10.0.0.5") is True, "10.0.0.5 debe ser privada"
    assert config.is_private_ip("172.20.0.1") is True, "172.20.0.1 debe ser privada"
    assert config.is_private_ip("8.8.8.8") is False, "8.8.8.8 no debe ser privada"
    assert config.is_private_ip("1.1.1.1") is False, "1.1.1.1 no debe ser privada"

    # Verificar actualizacion idempotente de settings y CRUD de operadores
    orig_mode = settings.get("network_mode", "local")
    orig_ops = list(settings.get("known_operators", []))
    try:
        config.update_system_settings(network_mode="lan")
        assert config.get_system_settings()["network_mode"] == "lan"

        # Test operador CRUD en config
        config.update_system_settings(add_operator="TestOp1")
        assert "TestOp1" in config.get_system_settings()["known_operators"]
        # No duplicados case-insensitive
        config.update_system_settings(add_operator="testop1")
        ops = [x for x in config.get_system_settings()["known_operators"] if x.lower() == "testop1"]
        assert len(ops) == 1, "No debe permitir duplicar operadores sin importar mayusculas/minusculas"

        # Rename
        config.update_system_settings(rename_operator={"old": "TestOp1", "new": "TestOpRenamed"})
        assert "TestOpRenamed" in config.get_system_settings()["known_operators"]
        assert "TestOp1" not in config.get_system_settings()["known_operators"]

        # Remove
        config.update_system_settings(remove_operator="TestOpRenamed")
        assert "TestOpRenamed" not in config.get_system_settings()["known_operators"]
    finally:
        config.update_system_settings(network_mode=orig_mode)
        current = config.get_system_settings()
        current["known_operators"] = orig_ops
        config.save_system_settings(current)

    print("  -> Configuracion de red e IPs OK")


def test_lan_security_and_pin_auth():
    print("[2/5] Verificando seguridad LAN y autenticacion por PIN...")

    test_port = 8994
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{test_port}"
    test_pin = "852963"

    # Configurar temporalmente modo LAN y PIN conocido
    orig_settings = copy.deepcopy(config.get_system_settings())
    temp_settings = copy.deepcopy(orig_settings)
    temp_settings["network_mode"] = "lan"
    temp_settings["lan_pin"] = test_pin
    temp_settings["known_operators"] = ["Ana"]
    config.save_system_settings(temp_settings)

    try:
        # 1. Peticion local autentica (sin headers remotos): debe pasar sin PIN
        req_local = Request(f"{base_url}/api/system/network")
        with urlopen(req_local) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["network_mode"] == "lan"
            assert data["lan_pin"] == test_pin

        # 2. Peticion remota simulada SIN PIN: debe retornar 401 Unauthorized
        req_remote_no_pin = Request(
            f"{base_url}/api/copilot/context?chat_id=test@c.us",
            headers={"X-Forwarded-For": "192.168.1.120"}
        )
        try:
            with urlopen(req_remote_no_pin) as resp:
                assert False, "Peticion remota sin PIN debio fallar con 401"
        except HTTPError as e:
            assert e.code == 401, f"Se esperaba 401, se obtuvo {e.code}"
            err_data = json.loads(e.read().decode("utf-8"))
            assert err_data.get("needs_pin") is True or err_data.get("ok") is False

        # 3. Peticion remota simulada con PIN INCORRECTO: debe retornar 401 Unauthorized
        req_remote_bad_pin = Request(
            f"{base_url}/api/copilot/context?chat_id=test@c.us",
            headers={
                "X-Forwarded-For": "192.168.1.120",
                "X-WAckup-Pin": "000000"
            }
        )
        try:
            with urlopen(req_remote_bad_pin) as resp:
                assert False, "Peticion remota con PIN incorrecto debio fallar con 401"
        except HTTPError as e:
            assert e.code == 401, f"Se esperaba 401, se obtuvo {e.code}"

        # 4. Peticion remota simulada con PIN CORRECTO: debe retornar 200 OK
        req_remote_good_pin = Request(
            f"{base_url}/api/copilot/context?chat_id=test@c.us",
            headers={
                "X-Forwarded-For": "192.168.1.120",
                "X-WAckup-Pin": test_pin
            }
        )
        with urlopen(req_remote_good_pin) as resp:
            assert resp.status == 200

        # 5. Endpoint POST /api/auth/verify_pin
        # 5a. Con PIN incorrecto
        bad_payload = json.dumps({"pin": "wrong", "operator": "Ana"}).encode("utf-8")
        req_verify_bad = Request(f"{base_url}/api/auth/verify_pin", data=bad_payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req_verify_bad) as resp:
                assert False, "Verificacion de PIN incorrecto debio fallar"
        except HTTPError as e:
            assert e.code == 401

        # 5b. Con PIN correcto
        good_payload = json.dumps({"pin": test_pin, "operator": "Ana"}).encode("utf-8")
        req_verify_good = Request(f"{base_url}/api/auth/verify_pin", data=good_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_verify_good) as resp:
            assert resp.status == 200
            v_data = json.loads(resp.read().decode("utf-8"))
            assert v_data.get("ok") is True

        # Verificar que "Ana" se registro en known_operators
        updated_settings = config.get_system_settings()
        assert "Ana" in updated_settings.get("known_operators", [])

        # 5c. Intentar conectar con un operador no dado de alta: debe rechazar con 400
        unlisted_payload = json.dumps({"pin": test_pin, "operator": "Intruso"}).encode("utf-8")
        req_verify_unlisted = Request(f"{base_url}/api/auth/verify_pin", data=unlisted_payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req_verify_unlisted) as resp:
                assert False, "Operador no registrado debio ser rechazado con HTTP 400"
        except HTTPError as e:
            assert e.code == 400

        # 5d. Modificar operador via /api/system/network (POST rename_operator)
        rename_payload = json.dumps({
            "rename_operator": {"old": "Ana", "new": "Anita"}
        }).encode("utf-8")
        req_rename = Request(f"{base_url}/api/system/network", data=rename_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_rename) as resp:
            assert resp.status == 200
            res_rename = json.loads(resp.read().decode("utf-8"))
            assert "Anita" in res_rename["settings"]["known_operators"]
            assert "Ana" not in res_rename["settings"]["known_operators"]

        # 5e. Conectar como "Anita" ahora tiene exito
        anita_payload = json.dumps({"pin": test_pin, "operator": "Anita"}).encode("utf-8")
        req_anita = Request(f"{base_url}/api/auth/verify_pin", data=anita_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_anita) as resp:
            assert resp.status == 200

        # 5f. Heartbeat de presencia no debe dar de alta operadores desconocidos por tipeo
        hb_payload = json.dumps({"chat_id": "test_hb@c.us", "operator": "OperadorFantasma", "client_id": "test_c1"}).encode("utf-8")
        req_hb = Request(f"{base_url}/api/presence/heartbeat", data=hb_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_hb) as resp:
            assert resp.status == 200
        assert "OperadorFantasma" not in config.get_system_settings().get("known_operators", [])

        # 6. Anti-spoofing: un socket remoto real (client_address != 127.0.0.1) JAMAS puede suplantar X-Forwarded-For: 127.0.0.1
        class DummyRemoteHandler:
            client_address = ("192.168.1.250", 54321)
            headers = {"X-Forwarded-For": "127.0.0.1", "X-Remote-IP": "127.0.0.1"}

        assert whatsapp_viewer.BackupServerHandler.is_local_client(DummyRemoteHandler()) is False, (
            "Socket remoto no debe poder suplantar identidad local con X-Forwarded-For: 127.0.0.1"
        )

    finally:
        config.save_system_settings(orig_settings)
        server.shutdown()
        server.server_close()

    print("  -> Seguridad LAN y verificacion de PIN OK")


def test_sse_and_presence_tracking():
    print("[3/5] Verificando canal SSE en tiempo real y Semaforo de Presencia...")
    orig_settings = copy.deepcopy(config.get_system_settings())

    test_port = 8995
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{test_port}"
    received_events = []
    sse_stop_event = threading.Event()

    def sse_listener():
        req = Request(f"{base_url}/api/events?client_id=listener_1&operator=Lucas")
        try:
            with urlopen(req, timeout=10) as resp:
                while not sse_stop_event.is_set():
                    line = resp.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if line_str.startswith("data: "):
                        try:
                            parsed = json.loads(line_str[6:].strip())
                            received_events.append(parsed)
                        except Exception:
                            pass
        except Exception:
            pass

    listener_thread = threading.Thread(target=sse_listener, daemon=True)
    listener_thread.start()
    time.sleep(0.4)

    try:
        # Registrar presencia de otro operador en un chat
        hb_payload = json.dumps({
            "chat_id": "54911998877@c.us",
            "client_id": "operator_ana_pc",
            "operator": "Ana"
        }).encode("utf-8")
        req_hb = Request(f"{base_url}/api/presence/heartbeat", data=hb_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_hb) as resp:
            assert resp.status == 200

        # Consultar estado de presencia por GET
        req_pres = Request(f"{base_url}/api/presence")
        with urlopen(req_pres) as resp:
            assert resp.status == 200
            pres_data = json.loads(resp.read().decode("utf-8"))
            pres_map = pres_data.get("presence") or pres_data.get("active_presence", {})
            assert "54911998877@c.us" in pres_map
            occ_info = pres_map["54911998877@c.us"]
            viewers = occ_info.get("viewers", []) if isinstance(occ_info, dict) else []
            assert (isinstance(occ_info, dict) and occ_info.get("operator") == "Ana") or any(v.get("operator") == "Ana" for v in viewers)

        # Esperar a que el evento SSE llegue al oyente
        time.sleep(0.5)
        assert any(e.get("event") == "presence_update" for e in received_events), (
            f"El oyente SSE debio recibir presence_update. Eventos recibidos: {received_events}"
        )

        # Operador abandona el chat
        leave_payload = json.dumps({
            "chat_id": "54911998877@c.us",
            "client_id": "operator_ana_pc"
        }).encode("utf-8")
        req_leave = Request(f"{base_url}/api/presence/leave", data=leave_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_leave) as resp:
            assert resp.status == 200

        # Verificar que presencia se limpio
        with urlopen(req_pres) as resp:
            pres_data_after = json.loads(resp.read().decode("utf-8"))
            pres_map_after = pres_data_after.get("presence") or pres_data_after.get("active_presence", {})
            occ_after = pres_map_after.get("54911998877@c.us")
            if occ_after and isinstance(occ_after, dict):
                viewers_after = occ_after.get("viewers", [])
                assert not any(v.get("client_id") == "operator_ana_pc" for v in viewers_after)

    finally:
        config.save_system_settings(orig_settings)
        sse_stop_event.set()
        sse_hub.get_sse_hub().shutdown()
        server.shutdown()
        server.server_close()

    print("  -> SSE y Semáforo de Presencia OK")


def test_optimistic_locking_and_collaborative_crm():
    print("[4/5] Verificando Concurrencia Optimista (409 Conflict), Bitácora y Asignación...")
    orig_settings = copy.deepcopy(config.get_system_settings())

    test_port = 8996
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{test_port}"
    chat_id = "54911223344@c.us"
    test_acc = "__test_phase4__"
    test_acc_dir = os.path.join(config.DATA_DIR, test_acc)

    try:
        # 1. Guardar nota inicial por Operador 1
        note_p1 = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "notes": "Version inicial de la nota",
            "updated_by": "Lucas"
        }).encode("utf-8")
        req1 = Request(f"{base_url}/api/copilot/notes", data=note_p1, headers={"Content-Type": "application/json"})
        with urlopen(req1) as resp:
            assert resp.status == 200
            res1 = json.loads(resp.read().decode("utf-8"))
            assert res1.get("ok") is True
            t1 = res1.get("notes_updated_at") or res1.get("updated_at")
            assert t1 is not None

        time.sleep(0.05)

        # 2. Operador 2 actualiza nota esperando t1 (exitoso)
        note_p2 = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "notes": "Version actualizada por Ana",
            "expected_updated_at": t1,
            "updated_by": "Ana"
        }).encode("utf-8")
        req2 = Request(f"{base_url}/api/copilot/notes", data=note_p2, headers={"Content-Type": "application/json"})
        with urlopen(req2) as resp:
            assert resp.status == 200
            res2 = json.loads(resp.read().decode("utf-8"))
            t2 = res2.get("notes_updated_at") or res2.get("updated_at")
            assert t2 is not None
            assert t2 != t1

        # 3. Operador 1 (que se quedó con t1) intenta guardar: debe dar HTTP 409 CONFLICT
        note_p3_conflict = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "notes": "Version pisada por Lucas que no vio a Ana",
            "expected_updated_at": t1,
            "updated_by": "Lucas"
        }).encode("utf-8")
        req3 = Request(f"{base_url}/api/copilot/notes", data=note_p3_conflict, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req3) as resp:
                assert False, "Deberia haber retornado HTTP 409 Conflict"
        except HTTPError as e:
            assert e.code == 409, f"Se esperaba HTTP 409, se obtuvo {e.code}"
            conflict_data = json.loads(e.read().decode("utf-8"))
            assert conflict_data.get("conflict") is True
            assert conflict_data.get("current_notes") == "Version actualizada por Ana"
            assert conflict_data.get("current_updated_by") == "Ana"

        # 4. Bitacora de Equipo (crm_contact_notes_log)
        # 4a. Agregar nota de bitacora
        log_entry = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "author": "Lucas",
            "content": "Cliente consulto por plan corporativo, coordinamos llamada."
        }).encode("utf-8")
        req_log_add = Request(f"{base_url}/api/copilot/notes/log", data=log_entry, headers={"Content-Type": "application/json"})
        with urlopen(req_log_add) as resp:
            assert resp.status == 200
            log_res = json.loads(resp.read().decode("utf-8"))
            assert log_res.get("ok") is True
            note = log_res.get("note") or {}
            note_id = log_res.get("note_id") or note.get("id")
            assert note_id is not None

        # 4b. Consultar notas de bitacora
        req_log_get = Request(f"{base_url}/api/copilot/notes/log?chat_id={chat_id}&account_id={test_acc}")
        with urlopen(req_log_get) as resp:
            assert resp.status == 200
            logs = json.loads(resp.read().decode("utf-8")).get("notes_log", [])
            assert len(logs) >= 1
            assert any(item["id"] == note_id and item["author"] == "Lucas" for item in logs)

        # 4c. Eliminar nota de bitacora
        del_entry = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "note_id": note_id
        }).encode("utf-8")
        req_log_del = Request(f"{base_url}/api/copilot/notes/log/delete", data=del_entry, headers={"Content-Type": "application/json"})
        with urlopen(req_log_del) as resp:
            assert resp.status == 200

        # 5. Asignacion de Operador Responsable
        assign_payload = json.dumps({
            "account_id": test_acc,
            "chat_id": chat_id,
            "assigned_to": "Ana",
            "updated_by": "Lucas"
        }).encode("utf-8")
        req_assign = Request(f"{base_url}/api/copilot/assignment", data=assign_payload, headers={"Content-Type": "application/json"})
        with urlopen(req_assign) as resp:
            assert resp.status == 200

        # Verificar en contexto del copiloto
        req_ctx = Request(f"{base_url}/api/copilot/context?chat_id={chat_id}&account_id={test_acc}")
        with urlopen(req_ctx) as resp:
            assert resp.status == 200
            ctx = json.loads(resp.read().decode("utf-8"))
            assert ctx.get("crm", {}).get("assigned_to") == "Ana"

    finally:
        config.save_system_settings(orig_settings)
        sse_hub.get_sse_hub().shutdown()
        server.shutdown()
        server.server_close()
        shutil.rmtree(test_acc_dir, ignore_errors=True)
        config.invalidate_accounts_cache()

    print("  -> Concurrencia Optimista, Bitácora y Asignación OK")


def test_clean_server_shutdown_with_sse_client():
    print("[5/5] Verificando apagado limpio e inmediato con clientes SSE conectados...")

    test_port = 8997
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{test_port}"
    sse_client_connected = threading.Event()

    def client_stream():
        req = Request(f"{base_url}/api/events?client_id=persistent_client&operator=Tester")
        try:
            with urlopen(req, timeout=5) as resp:
                sse_client_connected.set()
                while True:
                    chunk = resp.read(128)
                    if not chunk:
                        break
        except Exception:
            pass

    client_thread = threading.Thread(target=client_stream, daemon=True)
    client_thread.start()

    # Esperar confirmacion de conexion del cliente SSE
    sse_client_connected.wait(timeout=2.0)
    assert sse_client_connected.is_set(), "El cliente SSE no pudo conectarse para la prueba de cierre"

    # Medir tiempo de shutdown
    start_time = time.time()
    sse_hub.get_sse_hub().shutdown()
    server.shutdown()
    server.server_close()
    elapsed = time.time() - start_time

    assert elapsed < 2.0, f"El servidor tardó {elapsed:.2f}s en cerrarse, debe ser < 2.0s"
    print(f"  -> Apagado limpio e inmediato completado en {elapsed:.3f}s OK")


class TestPhase4LanCollaboration(unittest.TestCase):
    def test_01_system_settings_and_network_config(self):
        test_system_settings_and_network_config()

    def test_02_lan_security_and_pin_auth(self):
        test_lan_security_and_pin_auth()

    def test_03_sse_and_presence_tracking(self):
        test_sse_and_presence_tracking()

    def test_04_optimistic_locking_and_collaborative_crm(self):
        test_optimistic_locking_and_collaborative_crm()

    def test_05_clean_server_shutdown_with_sse_client(self):
        test_clean_server_shutdown_with_sse_client()


if __name__ == "__main__":
    unittest.main()
