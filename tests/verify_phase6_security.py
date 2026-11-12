"""
tests/verify_phase6_security.py
Suite de verificación exhaustiva de la Fase 1 (Blindaje de Seguridad Crítica):
1. Rechazo de Host inválido contra DNS Rebinding (403 Forbidden).
2. Bloqueo 403 a clientes remotos en endpoints administrativos y de actualización.
3. Emisión de Cookie HttpOnly wackup_session en /api/auth/verify_pin y acceso autenticado a multimedia LAN.
4. Bloqueo estricto de Path Traversal (/data/media/%2e%2e/ y extensiones prohibidas).
5. Bloqueo de ejecución inline de .html y .svg (descarga forzada application/octet-stream + CSP sandbox).
6. Escape estricto de los 5 caracteres críticos (&, <, >, ", ') en escapeHtml.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest
import subprocess
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import HTTPServer

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
import db_manager
import media_manager
import whatsapp_viewer


class TestPhase6Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_port = 8996
        cls.server = HTTPServer(("127.0.0.1", cls.test_port), whatsapp_viewer.BackupServerHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)
        cls.base_url = f"http://127.0.0.1:{cls.test_port}"

        # Configuración LAN de prueba
        cls.test_pin = "849201"
        cls.orig_settings = config.get_system_settings()
        cls.test_settings = dict(cls.orig_settings)
        cls.test_settings["network_mode"] = "lan"
        cls.test_settings["lan_pin"] = cls.test_pin
        cls.test_settings["known_operators"] = ["Seguridad", "Admin"]
        cls.test_settings["active_operator"] = "Seguridad"
        config.save_system_settings(cls.test_settings)

    @classmethod
    def tearDownClass(cls):
        config.save_system_settings(cls.orig_settings)
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def test_01_dns_rebinding_rejection(self):
        """1. Valida que una cabecera Host maliciosa sea rechazada con HTTP 403."""
        # Host malicioso
        req_bad = Request(f"{self.base_url}/api/chats", headers={"Host": "attacker.dnsrebinding.com"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_bad)
        self.assertEqual(ctx.exception.code, 403)

        # Host con puerto incorrecto
        req_wrong_port = Request(f"{self.base_url}/api/chats", headers={"Host": "127.0.0.1:9999"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_wrong_port)
        self.assertEqual(ctx.exception.code, 403)

        # Host legítimo con puerto dinámico correcto
        req_ok = Request(f"{self.base_url}/api/chats", headers={"Host": f"127.0.0.1:{self.test_port}"})
        with urlopen(req_ok) as resp:
            self.assertEqual(resp.status, 200)

    def test_02_remote_admin_endpoints_blocked_403(self):
        """2. Clientes remotos en LAN deben recibir HTTP 403 en endpoints exclusivos de administración local."""
        remote_headers = {
            "Host": f"127.0.0.1:{self.test_port}",
            "X-Forwarded-For": "192.168.1.188",
            "X-WAckup-Pin": self.test_pin,
            "Content-Type": "application/json"
        }

        # POST /api/system/network
        req_net = Request(
            f"{self.base_url}/api/system/network",
            data=json.dumps({"lan_pin": "999999"}).encode("utf-8"),
            headers=remote_headers
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_net)
        self.assertEqual(ctx.exception.code, 403)

        # POST /api/system/operator/active
        req_active = Request(
            f"{self.base_url}/api/system/operator/active",
            data=json.dumps({"operator": "Infiltrado"}).encode("utf-8"),
            headers=remote_headers
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_active)
        self.assertEqual(ctx.exception.code, 403)

        # POST /api/system/update/apply
        req_update = Request(
            f"{self.base_url}/api/system/update/apply",
            data=json.dumps({"force": True}).encode("utf-8"),
            headers=remote_headers
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_update)
        self.assertEqual(ctx.exception.code, 403)

        # POST /api/system/restart
        req_restart = Request(
            f"{self.base_url}/api/system/restart",
            data=b"{}",
            headers=remote_headers
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_restart)
        self.assertEqual(ctx.exception.code, 403)

        # GET /api/backup/download
        req_download = Request(
            f"{self.base_url}/api/backup/download",
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.188", "X-WAckup-Pin": self.test_pin}
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_download)
        self.assertEqual(ctx.exception.code, 403)

        # GET /api/backup/list
        req_list = Request(
            f"{self.base_url}/api/backup/list",
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.188", "X-WAckup-Pin": self.test_pin}
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req_list)
        self.assertEqual(ctx.exception.code, 403)

    def test_03_httponly_session_cookie_issuance_and_lan_media_access(self):
        """3. Verifica emisión de cookie HttpOnly wackup_session y autorización para multimedia sin exponer PIN."""
        # 3a. Cliente remoto sin credenciales a endpoint protegido -> 401
        remote_no_auth = Request(
            f"{self.base_url}/api/chats",
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.190"}
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(remote_no_auth)
        self.assertEqual(ctx.exception.code, 401)

        # 3b. Autenticación exitosa en /api/auth/verify_pin emite cookie HttpOnly y session_token
        auth_payload = json.dumps({"pin": self.test_pin, "operator": "Seguridad"}).encode("utf-8")
        req_auth = Request(
            f"{self.base_url}/api/auth/verify_pin",
            data=auth_payload,
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.190", "Content-Type": "application/json"}
        )
        with urlopen(req_auth) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            session_token = data.get("session_token")
            self.assertTrue(bool(session_token))
            set_cookie = resp.headers.get("Set-Cookie", "")
            self.assertIn("wackup_session=", set_cookie)
            self.assertIn("HttpOnly", set_cookie)
            self.assertIn("SameSite=Strict", set_cookie)

        # 3c. Petición subsiguiente usando la Cookie de sesión HttpOnly
        cookie_value = f"wackup_session={session_token}"
        req_cookie = Request(
            f"{self.base_url}/api/chats",
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.190", "Cookie": cookie_value}
        )
        with urlopen(req_cookie) as resp:
            self.assertEqual(resp.status, 200)

        # 3d. Petición subsiguiente usando cabecera X-WAckup-Session (utilizada por la extensión)
        req_sess_header = Request(
            f"{self.base_url}/api/chats",
            headers={"Host": f"127.0.0.1:{self.test_port}", "X-Forwarded-For": "192.168.1.190", "X-WAckup-Session": session_token}
        )
        with urlopen(req_sess_header) as resp:
            self.assertEqual(resp.status, 200)

    def test_04_path_traversal_blocking(self):
        """4. Neutralización de Path Traversal hacia datos del sistema o bases de datos."""
        # Intento de salir de media mediante %2e%2e/
        traversal_urls = [
            f"{self.base_url}/data/media/%2e%2e/system_settings.json",
            f"{self.base_url}/data/media/%2e%2e/%2e%2e/whatsapp_viewer.py",
            f"{self.base_url}/data/media/escaped.db"
        ]

        for url in traversal_urls:
            req = Request(url, headers={"Host": f"127.0.0.1:{self.test_port}"})
            try:
                with urlopen(req) as resp:
                    # Si devuelve 200, verificar que no contenga el archivo sensible
                    content = resp.read()
                    self.assertNotIn(b"lan_pin", content)
                    self.assertNotIn(b"BackupServerHandler", content)
            except HTTPError as e:
                # 403 o 404 son respuestas de bloqueo seguras
                self.assertIn(e.code, (403, 404))

    def test_05_unsafe_media_inline_blocking_and_csp_sandbox(self):
        """5. Forzar Content-Disposition: attachment y CSP sandbox en archivos .html y .svg servidos."""
        test_media_dir = config.MEDIA_DIR
        os.makedirs(test_media_dir, exist_ok=True)
        html_file = os.path.join(test_media_dir, "test_xss_probe.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write("<html><body><script>alert(1)</script></body></html>")

        try:
            req = Request(
                f"{self.base_url}/data/media/test_xss_probe.html",
                headers={"Host": f"127.0.0.1:{self.test_port}"}
            )
            with urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                content_disp = resp.headers.get("Content-Disposition", "")
                self.assertIn("attachment", content_disp.lower())
                csp = resp.headers.get("Content-Security-Policy", "")
                self.assertIn("sandbox", csp.lower())
                content_type = resp.headers.get("Content-Type", "")
                # Debe servirse como application/octet-stream o forzado a descarga
                self.assertIn("application/octet-stream", content_type.lower())
                _ = resp.read()
        finally:
            time.sleep(0.1)
            for _ in range(5):
                try:
                    if os.path.exists(html_file):
                        os.remove(html_file)
                    break
                except OSError:
                    time.sleep(0.1)

    def test_06_escape_html_five_characters(self):
        """6. Valida que escapeHtml en Node.js escape los 5 caracteres críticos (&, <, >, ", ')."""
        node_script = """
        const fs = require('fs');
        const sidepanelContent = fs.readFileSync('extension/sidepanel/sidepanel.js', 'utf8');
        const appContent = fs.readFileSync('web/js/app.js', 'utf8');

        // Extraer y evaluar escapeHtml de sidepanel
        const mSide = sidepanelContent.match(/function escapeHtml\\(str\\) \\{[\\s\\S]*?return String\\(str\\)[\\s\\S]*?;\\s*\\}/);
        if (!mSide) throw new Error("No se encontro escapeHtml en sidepanel.js");
        const escapeSide = new Function('str', mSide[0] + '; return escapeHtml(str);');

        // Extraer y evaluar escapeHtml de app.js
        const mApp = appContent.match(/function escapeHtml\\(text\\) \\{[\\s\\S]*?return String\\(text\\)[\\s\\S]*?;\\s*\\}/);
        if (!mApp) throw new Error("No se encontro escapeHtml en app.js");
        const escapeApp = new Function('text', mApp[0] + '; return escapeHtml(text);');

        const testInput = `& < > " '`;
        const expected = `&amp; &lt; &gt; &quot; &#39;`;

        if (escapeSide(testInput) !== expected) {
            console.error("Sidepanel escapeHtml fallo:", escapeSide(testInput));
            process.exit(1);
        }
        if (escapeApp(testInput) !== expected) {
            console.error("App.js escapeHtml fallo:", escapeApp(testInput));
            process.exit(2);
        }
        console.log("OK");
        """
        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, cwd=ROOT_DIR)
        self.assertEqual(res.returncode, 0, f"Error en validación Node.js de escapeHtml: {res.stderr}")
        self.assertIn("OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
