"""
tray_manager.py - Gestor de icono en la Bandeja del Sistema (System Tray) para WAckup.
Permite la ejecución silenciosa de WAckup en segundo plano sin ventana de consola CMD.
"""

import os
import sys
import time
import subprocess
import threading
import webbrowser
import logging

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY_DEPS = True
except ImportError:
    HAS_TRAY_DEPS = False
    pystray = None
    Image = None

logger = logging.getLogger("wackup.tray")


def create_fallback_icon():
    """Genera un icono limpio de WAckup con Pillow si no se encuentra favicon.jpg."""
    size = 64
    img = Image.new("RGBA", (size, size), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Círculo verde estilo WhatsApp / WAckup
    draw.ellipse([4, 4, size - 4, size - 4], fill="#25D366", outline="#128C7E", width=2)
    # Dibujar una 'W' blanca limpia en el centro
    # Puntos para la 'W'
    w_points = [
        (16, 20), (22, 44), (28, 20), (36, 20), (42, 44), (48, 20),
        (43, 20), (39, 38), (34, 20), (30, 20), (25, 38), (21, 20)
    ]
    draw.polygon(w_points, fill="#FFFFFF")
    return img


def load_tray_icon_image():
    """Carga assets/favicon.jpg o crea un icono generado."""
    if not HAS_TRAY_DEPS:
        return None

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(base_dir, "assets", "favicon.jpg"),
        os.path.join(base_dir, "assets", "favicon.png"),
        os.path.join(base_dir, "favicon.jpg"),
        os.path.join(base_dir, "web", "favicon.jpg")
    ]

    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                img = Image.open(p)
                return img.convert("RGBA") if img.mode != "RGBA" else img
            except Exception:
                pass

    return create_fallback_icon()


class TrayManager:
    """Administra el ciclo de vida del icono de WAckup en la bandeja del sistema."""

    def __init__(self, url="http://127.0.0.1:8080", port=8080, on_restart=None, on_exit=None, on_open=None):
        self.url = url
        self.port = port
        self.on_restart = on_restart
        self.on_exit = on_exit
        self.on_open = on_open
        self.icon = None
        self._is_running = False
        self._open_busy = False
        self._lock = threading.Lock()

    @staticmethod
    def is_available():
        """Devuelve True si pystray y Pillow están disponibles en el entorno."""
        return HAS_TRAY_DEPS

    def _on_open_clicked(self, icon=None, item=None):
        # Ejecutar fuera del hilo WndProc de pystray y dar margen a que el menú contextual de Windows cierre y libere el foco
        if self._open_busy:
            return
        self._open_busy = True

        def _worker():
            try:
                time.sleep(0.12)
                if callable(self.on_open):
                    self.on_open()
                else:
                    webbrowser.open(self.url, new=0, autoraise=True)
            except Exception as e:
                logger.error(f"Error abriendo navegador desde tray: {e}")
            finally:
                self._open_busy = False

        threading.Thread(target=_worker, daemon=True, name="WAckup-TrayOpen").start()

    def _on_restart_clicked(self, icon=None, item=None):
        if callable(self.on_restart):
            threading.Thread(target=self.on_restart, daemon=True, name="WAckup-TrayRestart").start()

    def _on_exit_clicked(self, icon=None, item=None):
        self.stop()
        if callable(self.on_exit):
            self.on_exit()
        else:
            os._exit(0)

    def _get_lan_url(self):
        try:
            import config
            ips = config.get_local_ip_addresses()
            if ips:
                return f"http://{ips[0]}:{self.port}"
        except Exception:
            pass
        return self.url

    def _on_copy_lan_clicked(self, icon=None, item=None):
        lan_url = self._get_lan_url()
        try:
            if sys.platform == "win32":
                subprocess.run(["clip"], input=lan_url, text=True, timeout=4, check=False)
        except Exception as e:
            logger.warning(f"No se pudo copiar IP al portapapeles: {e}")

    def build_menu(self):
        """Construye el menú contextual en español para el icono de la bandeja."""
        if not HAS_TRAY_DEPS:
            return None

        lan_url = self._get_lan_url()
        return pystray.Menu(
            pystray.MenuItem("Abrir Visor Web", self._on_open_clicked, default=True),
            pystray.MenuItem("Estado: En línea", lambda: None, enabled=False),
            pystray.MenuItem(f"Copiar IP Red Local ({lan_url})", self._on_copy_lan_clicked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Reiniciar Servicio", self._on_restart_clicked),
            pystray.MenuItem("Salir de WAckup", self._on_exit_clicked)
        )

    def start(self):
        """Inicia el icono en la bandeja del sistema en segundo plano."""
        if not HAS_TRAY_DEPS:
            logger.info("Tray dependencies no disponibles; se omitirá el icono de bandeja.")
            return False

        with self._lock:
            if self._is_running:
                return True

            try:
                image = load_tray_icon_image()
                menu = self.build_menu()
                tooltip = f"WAckup Activo - Puerto {self.port}"
                self.icon = pystray.Icon("WAckup", image, tooltip, menu)

                if hasattr(self.icon, "run_detached"):
                    self.icon.run_detached()
                else:
                    t = threading.Thread(target=self.icon.run, daemon=True, name="WAckup-TrayThread")
                    t.start()

                self._is_running = True

                # Notificación sutil en la bandeja del sistema de Windows
                try:
                    if hasattr(self.icon, "notify"):
                        self.icon.notify(
                            "WAckup está activo en segundo plano junto al reloj de Windows.",
                            "WAckup 3.0"
                        )
                except Exception:
                    pass

                return True
            except Exception as e:
                logger.warning(f"No se pudo iniciar el icono de la bandeja: {e}")
                self._is_running = False
                return False

    def update_tooltip(self, text):
        """Actualiza el texto flotante del icono."""
        if self.icon and self._is_running:
            try:
                self.icon.title = text
            except Exception:
                pass

    def stop(self):
        """Detiene y elimina el icono de la bandeja."""
        with self._lock:
            if self.icon and self._is_running:
                try:
                    self.icon.stop()
                except Exception:
                    pass
                self._is_running = False
