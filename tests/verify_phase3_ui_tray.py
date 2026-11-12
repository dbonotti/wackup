"""
tests/verify_phase3_ui_tray.py
Suite de verificacion exhaustiva para la Fase 3 de WAckup:
1. Arquitectura de App Rail, Vistas de Espacios de Trabajo y Layout Responsivo (<1440px).
2. Buscador Único Unificado y Dialogo de Plantillas con Vista Previa en Vivo.
3. CRUD HTTP completo de Plantillas (/api/support/templates: crear, listar, editar, eliminar).
4. Endpoint JSON de Logs de Sincronizacion (/api/logs) y Directorio de Etiquetas (/api/copilot/tags/all).
5. Inicializacion de TrayManager (generacion de icono PIL, menu y compatibilidad headless/console).
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from http.server import HTTPServer

# Asegurar importacion de modulos locales
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
import db_manager
import whatsapp_viewer
import tray_manager


def test_html_css_architecture():
    print("[1/5] Verificando arquitectura de HTML, App Rail y CSS responsivo...")

    index_path = os.path.join(ROOT_DIR, "web", "index.html")
    css_path = os.path.join(ROOT_DIR, "web", "css", "styles.css")
    js_path = os.path.join(ROOT_DIR, "web", "js", "app.js")

    assert os.path.isfile(index_path), "Falta web/index.html"
    assert os.path.isfile(css_path), "Falta web/css/styles.css"
    assert os.path.isfile(js_path), "Falta web/js/app.js"

    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    # 1. App Rail (58px) y sus 4 botones
    assert 'id="app-rail"' in html, "web/index.html debe contener #app-rail"
    assert 'id="railBtnConversations"' in html, "Falta railBtnConversations"
    assert 'id="railBtnCrm"' in html, "Falta railBtnCrm"
    assert 'id="railBtnTemplates"' in html, "Falta railBtnTemplates"
    assert 'id="railBtnSystem"' in html, "Falta railBtnSystem"
    assert any(v in html for v in ['v3.1', 'v3.0']), "Falta version v3.1/v3.0 en el App Rail"

    # 2. Las 4 Vistas de Espacios de Trabajo
    assert 'id="view-conversations"' in html, "Falta #view-conversations"
    assert 'id="view-crm"' in html, "Falta #view-crm"
    assert 'id="view-templates"' in html, "Falta #view-templates"
    assert 'id="view-system"' in html, "Falta #view-system"

    # 3. Buscador Único Unificado
    assert 'id="searchInput"' in html, "Falta #searchInput"
    assert 'id="unified-search-results"' in html, "Falta #unified-search-results"
    assert 'id="unified-contacts-list"' in html, "Falta #unified-contacts-list"
    assert 'id="search-results-list"' in html, "Falta #search-results-list"

    # 4. Copilot Drawer y Backdrop para baja resolucion
    assert 'id="chat-copilot-drawer"' in html, "Falta #chat-copilot-drawer"
    assert 'id="copilot-backdrop"' in html, "Falta #copilot-backdrop"

    # 5. Dialogo de Plantillas con Vista Previa y Chips
    assert 'id="template-modal"' in html, "Falta #template-modal"
    assert '{nombre}' in html and '{saludo}' in html and '{fecha}' in html, "Faltan chips de variables en modal de plantillas"
    assert 'id="tplLivePreviewBox"' in html, "Falta #tplLivePreviewBox para preview en vivo"

    # 6. Tarjetas de Sistema
    assert 'id="profCard-backup_only"' in html, "Falta tarjeta backup_only"
    assert 'id="profCard-support"' in html, "Falta tarjeta support"
    assert 'id="profCard-sales"' in html, "Falta tarjeta sales"
    assert 'id="profCard-full"' in html, "Falta tarjeta full"
    assert 'id="systemLogsTerminal"' in html, "Falta terminal de logs integrada"

    # 7. Reglas CSS para App Rail y Responsive <1440px vs >=1440px
    assert 'width: 58px' in css or 'max-width: 58px' in css, "Falta ancho 58px para .app-rail en CSS"
    assert '@media' in css and '1440px' in css, "Faltan media queries de 1440px en CSS"
    assert 'chat-copilot-drawer' in css, "Faltan reglas para #chat-copilot-drawer en CSS"

    # 8. JS contiene funciones de cambio de vista y atajos Alt+1..4
    assert 'function switchWorkspaceView(' in js, "Falta switchWorkspaceView en app.js"
    assert 'e.altKey' in js, "Falta manejo de atajos Alt+1..4 en app.js"
    assert 'loadTemplatesWorkspace' in js, "Falta loadTemplatesWorkspace en app.js"
    assert 'loadSystemWorkspace' in js, "Falta loadSystemWorkspace en app.js"
    assert 'loadSystemLogs' in js, "Falta loadSystemLogs en app.js"

    print("  [OK] Arquitectura HTML/CSS/JS de App Rail y Responsive verificada con exito.")


def test_tray_manager_initialization():
    print("[2/5] Verificando inicializacion y componentes de tray_manager.py...")

    assert tray_manager.TrayManager.is_available(), "TrayManager debe reportar dependencias disponibles (pystray y Pillow instaladas)"

    # Prueba de generacion de icono de fallback
    fallback_img = tray_manager.create_fallback_icon()
    assert fallback_img is not None, "create_fallback_icon no devolvio una imagen"
    assert fallback_img.size == (64, 64), f"Tamaño inesperado de icono fallback: {fallback_img.size}"

    # Carga de icono real o fallback
    icon_img = tray_manager.load_tray_icon_image()
    assert icon_img is not None, "load_tray_icon_image no devolvio una imagen valida"

    # Construccion de menu contextual en español
    tm = tray_manager.TrayManager(url="http://127.0.0.1:8080", port=8080)
    menu = tm.build_menu()
    assert menu is not None, "build_menu devolvio None"

    menu_labels = [item.text for item in menu.items if hasattr(item, "text")]
    assert any("Abrir Visor" in m for m in menu_labels), f"Falta Abrir Visor Web en {menu_labels}"
    assert any("Estado: En" in m for m in menu_labels), f"Falta Estado en {menu_labels}"
    assert any("Copiar IP" in m for m in menu_labels), f"Falta Copiar IP Red Local en {menu_labels}"
    assert any("Reiniciar" in m for m in menu_labels), f"Falta Reiniciar en {menu_labels}"
    assert any("Salir" in m for m in menu_labels), f"Falta Salir en {menu_labels}"

    print("  [OK] TrayManager y generacion de icono Pillow verificados con exito.")


def test_templates_crud_backend():
    print("[3/5] Verificando CRUD de plantillas y directorio de etiquetas en db_manager...")

    tmp_dir = tempfile.mkdtemp()
    test_db = os.path.join(tmp_dir, "test_phase3.db")

    try:
        db_manager.init_db(test_db)

        # 1. Listar plantillas iniciales por defecto
        templates = db_manager.list_quick_templates(db_path=test_db)
        assert len(templates) >= 3, f"Se esperaban plantillas por defecto, se obtuvieron {len(templates)}"

        # 2. Crear nueva plantilla
        tid = db_manager.save_quick_template(
            shortcut="/garantia",
            title="Garantia de productos",
            body="Hola {nombre}, {saludo}! Todos nuestros productos cuentan con 6 meses de garantia oficial.",
            profile_type="support",
            db_path=test_db
        )
        assert tid is not None and tid > 0, "Error creando plantilla con save_quick_template"

        # Verificar que fue añadida
        t_list = db_manager.list_quick_templates(db_path=test_db)
        created = next((t for t in t_list if t["id"] == tid), None)
        assert created is not None, "Plantilla creada no encontrada en list_quick_templates"
        assert created["shortcut"] == "/garantia"
        assert "{nombre}" in created["body"]

        # 3. Editar plantilla existente
        updated_tid = db_manager.save_quick_template(
            shortcut="/garantia_extendida",
            title="Garantia Extendida 12M",
            body="Hola {nombre}, {saludo}! Contamos con 12 meses de garantia oficial hasta el {fecha}.",
            profile_type="both",
            template_id=tid,
            db_path=test_db
        )
        assert updated_tid == tid, "save_quick_template al editar debe conservar el mismo ID"

        t_list2 = db_manager.list_quick_templates(db_path=test_db)
        edited = next((t for t in t_list2 if t["id"] == tid), None)
        assert edited["shortcut"] == "/garantia_extendida"
        assert "12 meses" in edited["body"]

        # 4. Directorio de etiquetas con list_all_tags
        db_manager.add_chat_tag("chat1@c.us", "vip", category="general", db_path=test_db)
        db_manager.add_chat_tag("chat2@c.us", "vip", category="general", db_path=test_db)
        db_manager.add_chat_tag("chat3@c.us", "nuevo", category="general", db_path=test_db)

        all_tags = db_manager.list_all_tags(db_path=test_db)
        assert len(all_tags) >= 2, f"Se esperaban al menos 2 etiquetas unicas, se obtuvieron {len(all_tags)}"
        vip_tag = next((t for t in all_tags if t["tag"] == "vip"), None)
        assert vip_tag is not None, "Etiqueta 'vip' no encontrada"
        assert vip_tag["count"] == 2, f"Conteo esperado para 'vip': 2, obtenido: {vip_tag['count']}"

        # 5. Eliminar plantilla
        del_ok = db_manager.delete_quick_template(tid, db_path=test_db)
        assert del_ok is True, "delete_quick_template fallo"

        t_list3 = db_manager.list_quick_templates(db_path=test_db)
        deleted = next((t for t in t_list3 if t["id"] == tid), None)
        assert deleted is None, "La plantilla no fue eliminada de la base de datos"

        print("  [OK] CRUD de plantillas y directorio de etiquetas en DB verificado con exito.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_http_api_endpoints():
    print("[4/5] Verificando endpoints HTTP (/api/support/templates, /api/logs, /api/copilot/tags/all)...")

    # Iniciar servidor HTTP en puerto libre para pruebas
    test_port = 8991
    server = HTTPServer(("127.0.0.1", test_port), whatsapp_viewer.BackupServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{test_port}"
    test_acc = "__test_phase3__"
    test_acc_dir = os.path.join(config.DATA_DIR, test_acc)

    try:
        # 1. Probar GET /api/logs
        req = Request(f"{base_url}/api/logs?account_id={test_acc}", headers={"Origin": "https://web.whatsapp.com"})
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True
            assert "logs" in data
            assert isinstance(data["logs"], list)

        # 2. Probar GET /api/copilot/tags/all
        req = Request(f"{base_url}/api/copilot/tags/all?account_id={test_acc}", headers={"Origin": "https://web.whatsapp.com"})
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True
            assert "tags" in data

        # 3. Probar POST /api/support/templates (Crear)
        create_payload = json.dumps({
            "account_id": test_acc,
            "shortcut": "/test_api",
            "title": "Plantilla API Test",
            "body": "Hola {nombre}, este es un test via HTTP",
            "profile_type": "sales"
        }).encode("utf-8")
        req = Request(f"{base_url}/api/support/templates", data=create_payload, headers={
            "Origin": "https://web.whatsapp.com",
            "Content-Type": "application/json"
        }, method="POST")
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True
            new_tid = data.get("template_id")
            assert new_tid is not None

        # 4. Probar POST /api/support/templates (Editar con id)
        edit_payload = json.dumps({
            "account_id": test_acc,
            "id": new_tid,
            "shortcut": "/test_api_v2",
            "title": "Plantilla API Modificada",
            "body": "Hola {nombre}, texto modificado via HTTP",
            "profile_type": "both"
        }).encode("utf-8")
        req = Request(f"{base_url}/api/support/templates", data=edit_payload, headers={
            "Origin": "https://web.whatsapp.com",
            "Content-Type": "application/json"
        }, method="POST")
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True
            assert data.get("template_id") == new_tid

        # 5. Probar POST /api/support/templates/delete (Eliminar)
        del_payload = json.dumps({"account_id": test_acc, "id": new_tid}).encode("utf-8")
        req = Request(f"{base_url}/api/support/templates/delete", data=del_payload, headers={
            "Origin": "https://web.whatsapp.com",
            "Content-Type": "application/json"
        }, method="POST")
        with urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("ok") is True
            assert data.get("deleted") is True

        print("  [OK] Endpoints HTTP de logs, etiquetas y CRUD de plantillas verificados con exito.")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(test_acc_dir, ignore_errors=True)
        config.invalidate_accounts_cache()


def test_console_mode_and_launchers():
    print("[5/5] Verificando lanzador iniciar_visor.bat y flag --console...")

    bat_path = os.path.join(ROOT_DIR, "iniciar_visor.bat")
    assert os.path.isfile(bat_path), "Falta iniciar_visor.bat"

    with open(bat_path, "r", encoding="utf-8", errors="ignore") as f:
        bat_content = f.read()

    assert "%*" in bat_content, "iniciar_visor.bat debe propagar %* hacia whatsapp_viewer.py"

    # Verificar que whatsapp_viewer.py soporta --console en sys.argv
    viewer_path = os.path.join(ROOT_DIR, "whatsapp_viewer.py")
    with open(viewer_path, "r", encoding="utf-8") as f:
        viewer_code = f.read()

    assert "--console" in viewer_code, "whatsapp_viewer.py debe soportar el flag --console"
    assert "tray_manager" in viewer_code, "whatsapp_viewer.py debe integrar tray_manager en ejecucion estandar"

    print("  [OK] Lanzador iniciar_visor.bat y flag --console verificados con exito.")


class TestPhase3UiAndTray(unittest.TestCase):
    def test_01_html_css_architecture(self):
        test_html_css_architecture()

    def test_02_tray_manager_initialization(self):
        test_tray_manager_initialization()

    def test_03_templates_crud_backend(self):
        test_templates_crud_backend()

    def test_04_http_api_endpoints(self):
        test_http_api_endpoints()

    def test_05_console_mode_and_launchers(self):
        test_console_mode_and_launchers()


if __name__ == "__main__":
    unittest.main()
