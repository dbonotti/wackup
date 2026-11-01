"""
tests/verify_phase5_release.py - Verificación de Fase 5 (WAckup 3.0)
==================================================================
Valida:
1. Comparación semántica de versiones en updater.py.
2. Filtro estricto de seguridad is_path_safe_to_update.
3. Simulación de apply_update() con ZIP local:
   - Archivos de código se actualizan.
   - Carpeta data/ (bases SQLite, multimedia, configuraciones) permanece 100% INTACTA.
   - Backup SQLite consistente creado preventivamente.
4. Protección contra sobrescritura en ramas de desarrollo de Git.
5. Endpoints /api/system/info y /api/system/onboarding en el servidor HTTP.
"""

import os
import sys
import json
import shutil
import sqlite3
import tempfile
import zipfile
import unittest
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

# Configurar stdout a UTF-8 para Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Agregar raíz al sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import updater
import config
import whatsapp_viewer


class TestPhase5Release(unittest.TestCase):

    def test_semver_comparison(self):
        """Prueba la comparación de versiones semánticas."""
        # v1 < v2
        self.assertEqual(updater.semver_compare("2.4.0", "3.0.0"), -1)
        self.assertEqual(updater.semver_compare("2.9.9", "3.0.0"), -1)
        self.assertEqual(updater.semver_compare("3.0.0", "3.0.1"), -1)
        self.assertEqual(updater.semver_compare("v3.0.0", "v3.1.0"), -1)

        # v1 == v2
        self.assertEqual(updater.semver_compare("3.0.0", "3.0.0"), 0)
        self.assertEqual(updater.semver_compare("v3.0.0", "3.0.0"), 0)
        self.assertEqual(updater.semver_compare("3.0.0-beta", "3.0.0"), 0)

        # v1 > v2
        self.assertEqual(updater.semver_compare("3.0.1", "3.0.0"), 1)
        self.assertEqual(updater.semver_compare("3.1.0", "3.0.9"), 1)
        self.assertEqual(updater.semver_compare("v4.0.0", "3.9.9"), 1)

    def test_is_path_safe_to_update(self):
        """Prueba que el filtro de seguridad impida tocar data/, runtime/ o bases de datos."""
        # PROHIBIDOS
        self.assertFalse(updater.is_path_safe_to_update("data/whatsapp_backup.db"))
        self.assertFalse(updater.is_path_safe_to_update("data/account_1/media/foto.jpg"))
        self.assertFalse(updater.is_path_safe_to_update("data/system_settings.json"))
        self.assertFalse(updater.is_path_safe_to_update("runtime/python/python.exe"))
        self.assertFalse(updater.is_path_safe_to_update("custom/path/malicious.db"))
        self.assertFalse(updater.is_path_safe_to_update(".git/config"))
        self.assertFalse(updater.is_path_safe_to_update(".env"))
        self.assertFalse(updater.is_path_safe_to_update("database.sqlite3"))

        # PERMITIDOS
        self.assertTrue(updater.is_path_safe_to_update("whatsapp_viewer.py"))
        self.assertTrue(updater.is_path_safe_to_update("config.py"))
        self.assertTrue(updater.is_path_safe_to_update("VERSION"))
        self.assertTrue(updater.is_path_safe_to_update("requirements.txt"))
        self.assertTrue(updater.is_path_safe_to_update("web/index.html"))
        self.assertTrue(updater.is_path_safe_to_update("web/js/app.js"))
        self.assertTrue(updater.is_path_safe_to_update("web/css/styles.css"))
        self.assertTrue(updater.is_path_safe_to_update("extension/manifest.json"))
        self.assertTrue(updater.is_path_safe_to_update("extension/background.js"))
        self.assertTrue(updater.is_path_safe_to_update("iniciar_visor.bat"))
        self.assertTrue(updater.is_path_safe_to_update("Iniciar_WAckup.vbs"))

    def test_apply_update_data_preservation_and_backup(self):
        """
        Simula una actualización completa mediante un paquete ZIP:
        Verifica que el código se actualice, la carpeta data/ se preserve íntegra
        y se genere un snapshot consistente de la base de datos en backups/.
        """
        sandbox = tempfile.mkdtemp(prefix="wackup_test_sandbox_")
        try:
            # 1. Crear entorno simulado pre-actualización en sandbox
            code_file = os.path.join(sandbox, "whatsapp_viewer.py")
            with open(code_file, "w", encoding="utf-8") as f:
                f.write("# Version Antigua\nOLD_CODE = True\n")

            version_file = os.path.join(sandbox, "VERSION")
            with open(version_file, "w", encoding="utf-8") as f:
                f.write("2.9.0\n")

            # Crear datos de usuario en data/
            user_data_dir = os.path.join(sandbox, "data", "test_acc")
            os.makedirs(user_data_dir, exist_ok=True)
            db_path = os.path.join(user_data_dir, "whatsapp_backup.db")

            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, note TEXT);")
                conn.execute("INSERT INTO test_data (note) VALUES ('Dato vital del usuario');")
                conn.commit()

            media_dir = os.path.join(user_data_dir, "media")
            os.makedirs(media_dir, exist_ok=True)
            media_file = os.path.join(media_dir, "foto_vital.jpg")
            test_media_bytes = b"\xff\xd8\xff\xe0TEST_MEDIA_BYTES_12345"
            with open(media_file, "wb") as f:
                f.write(test_media_bytes)

            settings_path = os.path.join(sandbox, "data", "system_settings.json")
            original_settings = {"custom_pin": "999888", "onboarding_completed": True}
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(original_settings, f)

            # 2. Crear un paquete ZIP malintencionado / de actualización
            zip_path = os.path.join(sandbox, "mock_update.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                # Código nuevo legítimo
                zf.writestr("VERSION", "3.0.1\n")
                zf.writestr("whatsapp_viewer.py", "# Version Nueva\nNEW_CODE = True\n")
                zf.writestr("web/nuevo_recurso.html", "<h1>Nuevo</h1>")

                # Intentos de sobrescritura que el actualizador DEBE bloquear
                zf.writestr("data/test_acc/whatsapp_backup.db", b"CORRUPTED_DATABASE_ATTEMPT")
                zf.writestr("data/test_acc/media/foto_vital.jpg", b"OVERWRITTEN_MEDIA")
                zf.writestr("data/system_settings.json", "{}")

            # 3. Aplicar actualización
            res = updater.apply_update(
                zip_path=zip_path,
                target_dir=sandbox,
                force=True,
                restart=False
            )

            self.assertTrue(res.get("ok"), f"apply_update falló: {res}")
            self.assertEqual(res.get("status"), "updated")

            # 4. Verificar que el código se actualizó
            with open(code_file, "r", encoding="utf-8") as f:
                new_code = f.read()
            self.assertIn("NEW_CODE = True", new_code)

            with open(version_file, "r", encoding="utf-8") as f:
                new_ver = f.read().strip()
            self.assertEqual(new_ver, "3.0.1")

            new_static = os.path.join(sandbox, "web", "nuevo_recurso.html")
            self.assertTrue(os.path.isfile(new_static))

            # 5. PRESERVACIÓN TOTAL DE DATOS: Verificar que data/ no fue tocada
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT note FROM test_data WHERE id=1;")
                row = cursor.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Dato vital del usuario")

            with open(media_file, "rb") as f:
                read_bytes = f.read()
            self.assertEqual(read_bytes, test_media_bytes, "El archivo multimedia fue corrompido")

            with open(settings_path, "r", encoding="utf-8") as f:
                read_settings = json.load(f)
            self.assertEqual(read_settings.get("custom_pin"), "999888")

            # 6. Verificar que se creó el backup consistente en backups/
            bak_dir = os.path.join(user_data_dir, "backups")
            self.assertTrue(os.path.isdir(bak_dir))
            bak_files = os.listdir(bak_dir)
            self.assertGreaterEqual(len(bak_files), 1)

            bak_db = os.path.join(bak_dir, bak_files[0])
            with sqlite3.connect(bak_db) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT note FROM test_data WHERE id=1;")
                row = cursor.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Dato vital del usuario")

        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_git_dev_branch_protection(self):
        """Verifica que el actualizador aborte si se detecta una rama Git distinta de main."""
        sandbox = tempfile.mkdtemp(prefix="wackup_test_git_")
        try:
            # Crear directorio .git simulado
            git_dir = os.path.join(sandbox, ".git")
            os.makedirs(git_dir, exist_ok=True)

            # Simular get_git_branch devolviendo 'feature/nueva-ui'
            orig_get_branch = updater.get_git_branch
            updater.get_git_branch = lambda target=None: "feature/nueva-ui"

            try:
                res = updater.apply_update(
                    zip_path="dummy.zip",
                    target_dir=sandbox,
                    force=False,
                    restart=False
                )
                self.assertFalse(res.get("ok"))
                self.assertEqual(res.get("status"), "aborted")
                self.assertIn("Rama de desarrollo activa", res.get("error", ""))
            finally:
                updater.get_git_branch = orig_get_branch

        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_system_info_and_onboarding_endpoints(self):
        """Prueba los endpoints HTTP /api/system/info y /api/system/onboarding."""
        # Iniciar servidor temporal en puerto de prueba
        test_port = 8185
        server = ThreadingHTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            base_url = f"http://127.0.0.1:{test_port}"

            # 1. GET /api/system/info
            req = urllib.request.Request(f"{base_url}/api/system/info")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                info = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(info.get("ok"))
                self.assertIn("version", info)
                self.assertIn("onboarding_completed", info)
                self.assertIn("extension_path", info)
                self.assertIn("desktop_shortcut_exists", info)
                self.assertEqual(info.get("version"), updater.get_current_version())

            # 2. GET /api/system/onboarding
            req = urllib.request.Request(f"{base_url}/api/system/onboarding")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                ob = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(ob.get("ok"))
                self.assertIn("onboarding_completed", ob)
                self.assertIn("extension_connected", ob)
                self.assertIn("extension_path", ob)

            # 3. POST /api/system/onboarding (Completar onboarding con perfil 'crm')
            post_data = json.dumps({"completed": True, "profile": "crm"}).encode("utf-8")
            post_req = urllib.request.Request(
                f"{base_url}/api/system/onboarding",
                data=post_data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(post_req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                res_post = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res_post.get("ok"))
                self.assertTrue(res_post.get("onboarding_completed"))

            # 4. Verificar persistencia de onboarding completado
            settings = config.get_system_settings()
            self.assertTrue(settings.get("onboarding_completed"))

        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
