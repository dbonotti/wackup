import os
import json
import re
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(PROJECT_ROOT, "extension")

class TestPhase2AntiBanAndSidePanel(unittest.TestCase):
    """
    Auditoría estricta de la Fase 2:
    - Pureza Anti-Ban 100% en crypto_sync.js y bridge.js (cero DOM, cero window.__, cero window.require)
    - Conformidad Manifest V3 y configuración nativa de chrome.sidePanel
    - Sintaxis válida en todos los scripts JS de la extensión
    - Endpoints invocados por sidepanel.js existentes en whatsapp_viewer.py y autorizados en background.js
    """

    def test_01_manifest_v3_structure_and_files(self):
        """Verifica que manifest.json sea válido, sea MV3, tenga sidePanel y apunte a archivos existentes."""
        manifest_path = os.path.join(EXTENSION_DIR, "manifest.json")
        self.assertTrue(os.path.isfile(manifest_path), "manifest.json no existe en extension/")

        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("manifest_version"), 3, "Debe ser Manifest V3")
        self.assertEqual(data.get("version"), "3.0.0", "La versión declarada debe ser 3.0.0")

        # Permisos
        permissions = data.get("permissions", [])
        self.assertIn("sidePanel", permissions, "Debe declarar permiso 'sidePanel'")
        self.assertIn("storage", permissions, "Debe declarar permiso 'storage'")

        # Host permissions
        host_perms = data.get("host_permissions", [])
        self.assertTrue(any("web.whatsapp.com" in p for p in host_perms), "Debe incluir web.whatsapp.com")
        self.assertTrue(any("127.0.0.1" in p for p in host_perms), "Debe incluir 127.0.0.1")

        # Side panel default path
        side_panel = data.get("side_panel")
        self.assertIsNotNone(side_panel, "Debe declarar objeto 'side_panel'")
        sp_path = side_panel.get("default_path")
        self.assertTrue(bool(sp_path), "side_panel debe tener 'default_path'")
        sp_full_path = os.path.join(EXTENSION_DIR, sp_path.replace("/", os.sep))
        self.assertTrue(os.path.isfile(sp_full_path), f"El archivo {sp_path} referenciado en side_panel no existe")

        # Service worker
        background = data.get("background", {})
        sw = background.get("service_worker")
        self.assertTrue(bool(sw), "background debe tener 'service_worker'")
        self.assertTrue(os.path.isfile(os.path.join(EXTENSION_DIR, sw)), f"Service worker {sw} no existe")

        # Content scripts
        content_scripts = data.get("content_scripts", [])
        self.assertGreater(len(content_scripts), 0, "Debe haber content_scripts")

        declared_files = set()
        has_isolated = False
        has_main = False

        for cs in content_scripts:
            for js in cs.get("js", []):
                declared_files.add(js)
                self.assertTrue(os.path.isfile(os.path.join(EXTENSION_DIR, js)), f"Script {js} no existe")
            world = cs.get("world", "ISOLATED")
            if world == "MAIN":
                has_main = True
            else:
                has_isolated = True

        self.assertTrue(has_isolated, "Debe haber un script en el contexto ISOLATED (bridge.js)")
        self.assertTrue(has_main, "Debe haber un script en el contexto MAIN (crypto_sync.js)")

        # content.js antiguo NO debe existir
        self.assertFalse(os.path.isfile(os.path.join(EXTENSION_DIR, "content.js")), "content.js antiguo debe estar eliminado")

    def test_02_pureza_antiban_crypto_sync_and_bridge(self):
        """Verifica 0 ocurrencias de tokens prohibidos en crypto_sync.js y bridge.js."""
        prohibited_tokens = [
            "window.require",
            "WAWebMsgCollection",
            "webpackChunk",
            "window.__",
            "document.createElement",
            "attachShadow",
            "document.body.style",
            ".dispatchEvent(",
            "memEntry",
            "inMemoryMap"
        ]

        files_to_check = ["crypto_sync.js", "bridge.js"]

        for fname in files_to_check:
            fpath = os.path.join(EXTENSION_DIR, fname)
            self.assertTrue(os.path.isfile(fpath), f"Archivo {fname} no existe")
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()

            for token in prohibited_tokens:
                count = content.count(token)
                self.assertEqual(count, 0, f"Token prohibido detectado en {fname}: '{token}' (aparece {count} veces)")

    def test_03_js_syntax_validation_with_node(self):
        """Verifica que todos los archivos .js dentro de extension/ no tengan errores sintácticos."""
        js_files = []
        for root, _, files in os.walk(EXTENSION_DIR):
            for file in files:
                if file.endswith(".js"):
                    js_files.append(os.path.join(root, file))

        self.assertGreaterEqual(len(js_files), 4, "Debe haber al menos 4 scripts JS")

        for js_file in js_files:
            rel_path = os.path.relpath(js_file, PROJECT_ROOT)
            res = subprocess.run(["node", "--check", js_file], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Error de sintaxis en {rel_path}:\n{res.stderr}")

    def test_04_sidepanel_endpoints_in_viewer_and_background(self):
        """Verifica que todos los endpoints invocados en sidepanel.js existan en whatsapp_viewer.py y autorizados en background.js."""
        sp_path = os.path.join(EXTENSION_DIR, "sidepanel", "sidepanel.js")
        with open(sp_path, "r", encoding="utf-8") as f:
            sp_code = f.read()

        # Extraer endpoints llamados mediante apiFetch("/api/...")
        called_endpoints = re.findall(r'apiFetch\(\s*["\'](/api/[^"\'?]+)', sp_code)
        called_endpoints += re.findall(r'apiFetch\(\s*`(/api/[^`?]+)', sp_code)
        called_endpoints = sorted(list(set(called_endpoints)))

        self.assertGreater(len(called_endpoints), 0, "No se detectaron endpoints en sidepanel.js")

        # Leer whatsapp_viewer.py
        viewer_path = os.path.join(PROJECT_ROOT, "whatsapp_viewer.py")
        with open(viewer_path, "r", encoding="utf-8") as f:
            viewer_code = f.read()

        # Leer background.js
        bg_path = os.path.join(EXTENSION_DIR, "background.js")
        with open(bg_path, "r", encoding="utf-8") as f:
            bg_code = f.read()

        # Extraer lista de ALLOWED_ENDPOINTS de background.js
        for ep in called_endpoints:
            # 1. Comprobar que el endpoint existe en whatsapp_viewer.py
            # Puede estar como `url.path == "/api/..."` o `url.path.startswith("/api/...")`
            self.assertTrue(
                f'"{ep}"' in viewer_code or f"'{ep}'" in viewer_code,
                f"Endpoint {ep} llamado por sidepanel.js no se encontró en whatsapp_viewer.py"
            )

            # 2. Comprobar que background.js autoriza el endpoint
            escaped_ep = ep.replace("/", r"\/")
            has_auth = (escaped_ep in bg_code) or (ep in bg_code)
            self.assertTrue(has_auth, f"Endpoint {ep} no está contemplado en ALLOWED_ENDPOINTS de background.js")

if __name__ == "__main__":
    unittest.main()
