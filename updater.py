"""
updater.py - Actualizador Nativo por HTTPS sin Git para WAckup 3.0
==================================================================
Permite verificar y aplicar actualizaciones directamente desde GitHub Releases
o repositorios públicos vía HTTPS utilizando únicamente urllib y zipfile de la
librería estándar de Python.

REGLA DE ORO DE DATOS:
- Preservación 100% de la carpeta data/ (bases SQLite, multimedia, configuraciones).
- Generación automática de copias de seguridad consistentes con sqlite3.Connection.backup().
- Protección contra sobreescritura accidental en ramas de desarrollo de Git.
"""

import os
import sys
import json
import shutil
import tempfile
import zipfile
import subprocess
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple

import config

GITHUB_REPO = "dbonotti/wackup"
VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def get_current_version(target_dir: Optional[str] = None) -> str:
    """Lee la versión actual desde el archivo raíz VERSION."""
    vfile = os.path.join(target_dir, "VERSION") if target_dir else VERSION_FILE
    if os.path.isfile(vfile):
        try:
            with open(vfile, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return "3.0.1"


def parse_semver(version_str: str) -> Tuple[int, int, int]:
    """
    Parsea una versión semántica tipo '3.0.1' o 'v3.0.1'.
    Devuelve una tupla (major, minor, patch).
    """
    if not version_str:
        return (0, 0, 0)
    cleaned = version_str.strip().lstrip("vV")
    # Ignorar sufijos pre-release como -beta o +build
    main_part = cleaned.split("-")[0].split("+")[0]
    segments = main_part.split(".")
    nums = []
    for s in segments:
        try:
            nums.append(int(s))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def semver_compare(v1: str, v2: str) -> int:
    """
    Compara dos versiones semánticas.
    Devuelve:
      -1 si v1 < v2
       0 si v1 == v2
       1 si v1 > v2
    """
    p1 = parse_semver(v1)
    p2 = parse_semver(v2)
    if p1 < p2:
        return -1
    elif p1 > p2:
        return 1
    return 0


def get_git_branch(target_dir: Optional[str] = None) -> Optional[str]:
    """Devuelve el nombre de la rama Git actual si el directorio es un repo Git local."""
    base_dir = target_dir or os.path.dirname(os.path.abspath(__file__))
    git_dir = os.path.join(base_dir, ".git")
    if not os.path.isdir(git_dir):
        return None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            branch = res.stdout.strip()
            return branch if branch else None
    except Exception:
        pass
    return None


def check_for_updates(repo: str = GITHUB_REPO, current_version: Optional[str] = None) -> Dict[str, Any]:
    """
    Comprueba si hay actualizaciones disponibles en GitHub sin requerir Git instalado.
    Consulta primero la API de GitHub Releases; si no hay release publicado, recurre
    a raw.githubusercontent.com/.../VERSION en main.
    """
    curr_v = current_version or get_current_version()
    branch = get_git_branch()
    is_dev = bool(branch and branch != "main")

    latest_version = curr_v
    release_notes = ""
    download_url = f"https://github.com/{repo}/archive/refs/heads/main.zip"
    source = "none"

    # 1. Intentar consultar la API de GitHub Releases
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "User-Agent": f"WAckup-Updater/{curr_v}",
        "Accept": "application/vnd.github.v3+json"
    }

    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                tag = data.get("tag_name", "").strip()
                if tag:
                    latest_version = tag.lstrip("vV")
                    release_notes = data.get("body", "") or ""
                    # Buscar asset zip o zipball_url
                    assets = data.get("assets", [])
                    zip_asset = next((a.get("browser_download_url") for a in assets if a.get("name", "").endswith(".zip")), None)
                    download_url = zip_asset or data.get("zipball_url") or download_url
                    source = "releases"
    except urllib.error.HTTPError as e:
        # Si 404 (aún no se publicó release formal), intentar consultar archivo VERSION en rama main
        if e.code == 404:
            raw_url = f"https://raw.githubusercontent.com/{repo}/main/VERSION"
            try:
                raw_req = urllib.request.Request(raw_url, headers={"User-Agent": f"WAckup-Updater/{curr_v}"})
                with urllib.request.urlopen(raw_req, timeout=6) as raw_resp:
                    if raw_resp.status == 200:
                        v_str = raw_resp.read().decode("utf-8").strip()
                        if v_str:
                            latest_version = v_str
                            release_notes = f"Actualización a versión {latest_version} desde repositorio GitHub."
                            source = "raw_main"
            except Exception:
                pass
        else:
            return {
                "ok": False,
                "status": "error",
                "current_version": curr_v,
                "message": f"Error consultando GitHub (HTTP {e.code})"
            }
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "current_version": curr_v,
            "message": f"No se pudo conectar con GitHub: {str(e)}"
        }

    # Evaluar comparación semántica
    has_update = semver_compare(latest_version, curr_v) > 0

    if is_dev:
        status = "dev_branch"
        msg = f"Rama de desarrollo activa: '{branch}'. Auto-actualizaciones pausadas para proteger cambios locales."
    elif has_update:
        status = "update_available"
        msg = f"Nueva versión {latest_version} disponible (versión instalada: {curr_v})."
    else:
        status = "up_to_date"
        msg = f"WAckup está al día con la última versión ({curr_v})."

    return {
        "ok": True,
        "status": status,
        "current_version": curr_v,
        "latest_version": latest_version,
        "has_update": has_update,
        "release_notes": release_notes,
        "download_url": download_url,
        "source": source,
        "is_dev_branch": is_dev,
        "branch": branch,
        "message": msg
    }


def is_path_safe_to_update(rel_path: str) -> bool:
    """
    Verifica que un archivo dentro del ZIP a actualizar pertenezca a los archivos
    de código y recursos permitidos, y NO toque bajo ninguna circunstancia
    data/, runtime/, o archivos de configuración de usuario.
    """
    # Normalizar separadores a '/'
    norm = rel_path.replace("\\", "/").strip().lstrip("/")
    if not norm or norm.startswith("."):
        return False

    parts = norm.split("/")
    first_dir = parts[0].lower()

    # PROHIBIDO tocar:
    forbidden_roots = {"data", "runtime", "errores", "scratch", "dist", ".git", ".venv", "env", "venv"}
    if first_dir in forbidden_roots:
        return False

    # PROHIBIDO sobrescribir bases de datos o configuraciones de usuario en cualquier ruta
    lower_norm = norm.lower()
    for ext in (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".tmp"):
        if lower_norm.endswith(ext):
            return False

    for conf in ("system_settings.json", "account.json"):
        if lower_norm.endswith(conf):
            return False

    # Archivos y carpetas explícitamente permitidos
    allowed_roots = {"web", "extension", "assets", "tools"}
    if first_dir in allowed_roots:
        return True

    # Archivos permitidos en la raíz del proyecto
    if len(parts) == 1:
        allowed_files = {
            "whatsapp_viewer.py",
            "config.py",
            "db_manager.py",
            "media_manager.py",
            "entity_extractor.py",
            "sse_hub.py",
            "tray_manager.py",
            "updater.py",
            "version",
            "requirements.txt",
            "iniciar_visor.bat",
            "ver_logs.bat",
            "readme.md",
            "prompts_wackup_v3.md"
        }
        return lower_norm in allowed_files or lower_norm.endswith(".py")

    return False


MAX_UPDATE_ZIP_BYTES = 60 * 1024 * 1024       # 60 MB máximo de paquete zip
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024    # 150 MB máximo descomprimido (anti zip-bomb)


def apply_update(
    download_url: Optional[str] = None,
    zip_path: Optional[str] = None,
    force: bool = False,
    target_dir: Optional[str] = None,
    restart: bool = False
) -> Dict[str, Any]:
    """
    Aplica una actualización descargando o usando un archivo ZIP:
    1. Si existe .git y la rama != main y not force, aborta preventivamente.
    2. Ejecuta config.backup_all_databases() con copia SQLite consistente.
    3. Descarga o valida el ZIP con zipfile.testzip().
    4. Copia únicamente archivos seguros excluyendo data/ y runtime/.
    5. Opcionalmente solicita reinicio de proceso.
    """
    base_dir = target_dir or os.path.dirname(os.path.abspath(__file__))

    # 1. Protección de desarrollo
    branch = get_git_branch(base_dir)
    if branch and branch != "main" and not force:
        return {
            "ok": False,
            "status": "aborted",
            "error": f"Rama de desarrollo activa: '{branch}'. Actualización abortada para proteger cambios locales. Usa force=True para forzar."
        }

    # 2. Respaldo consistente previo de todas las bases de datos
    try:
        backed_up_count = config.backup_all_databases(target_base_dir=base_dir)
    except Exception as e:
        return {
            "ok": False,
            "status": "error",
            "error": f"Error creando copia preventiva de seguridad: {str(e)}"
        }

    # 3. Obtener o descargar archivo ZIP
    temp_work_dir = tempfile.mkdtemp(prefix="wackup_update_")
    local_zip_path = zip_path

    try:
        if not local_zip_path:
            if not download_url:
                check = check_for_updates()
                if not check.get("ok") or not check.get("download_url"):
                    return {
                        "ok": False,
                        "status": "error",
                        "error": "No se encontró URL de descarga para la actualización."
                    }
                download_url = check["download_url"]

            # Validación de seguridad estricta del origen de descarga
            parsed = urllib.parse.urlparse(download_url)
            allowed_hosts = {"github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com"}
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed_hosts:
                return {
                    "ok": False,
                    "status": "error",
                    "error": f"Origen de actualización no seguro o no autorizado: {download_url}"
                }

            local_zip_path = os.path.join(temp_work_dir, "update_package.zip")
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": f"WAckup-Updater/{get_current_version()}"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(local_zip_path, "wb") as out_f:
                downloaded_size = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    downloaded_size += len(chunk)
                    if downloaded_size > MAX_UPDATE_ZIP_BYTES:
                        return {
                            "ok": False,
                            "status": "error",
                            "error": f"El paquete de actualización supera el límite máximo de {MAX_UPDATE_ZIP_BYTES // (1024 * 1024)} MB."
                        }
                    out_f.write(chunk)

        # Validar integridad del ZIP
        if not os.path.isfile(local_zip_path) or not zipfile.is_zipfile(local_zip_path):
            return {
                "ok": False,
                "status": "error",
                "error": "El paquete de actualización no es un archivo ZIP válido."
            }

        with zipfile.ZipFile(local_zip_path, "r") as zf:
            bad_file = zf.testzip()
            if bad_file:
                return {
                    "ok": False,
                    "status": "error",
                    "error": f"Archivo ZIP corrupto (falla en {bad_file})."
                }

            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                return {
                    "ok": False,
                    "status": "error",
                    "error": f"El contenido descomprimido ({total_uncompressed} bytes) excede el límite de seguridad anti-bomba de {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB."
                }

            # Determinar prefijo común si el zip empaqueta en una subcarpeta (ej. wackup-main/ o wackup-3.0.1/)
            all_names = zf.namelist()
            prefix = ""
            if all_names:
                first_part = all_names[0].split("/")[0] if "/" in all_names[0] else None
                if first_part and all(n.startswith(first_part + "/") or n == first_part + "/" or n == first_part for n in all_names):
                    prefix = first_part + "/"

            updated_files = []
            for member in zf.infolist():
                if member.is_dir():
                    continue

                raw_filename = member.filename
                if prefix and raw_filename.startswith(prefix):
                    clean_rel = raw_filename[len(prefix):]
                else:
                    clean_rel = raw_filename

                if not clean_rel or not is_path_safe_to_update(clean_rel):
                    continue

                dest_file_path = os.path.abspath(os.path.join(base_dir, clean_rel.replace("/", os.sep)))
                abs_base_dir = os.path.abspath(base_dir)
                if os.path.commonpath([dest_file_path, abs_base_dir]) != abs_base_dir:
                    continue
                os.makedirs(os.path.dirname(dest_file_path), exist_ok=True)

                # Extraer archivo
                with zf.open(member) as src_f, open(dest_file_path, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)

                updated_files.append(clean_rel)

        new_version = get_current_version(base_dir)

        # 4. Reinicio si se solicitó
        if restart:
            try:
                import whatsapp_viewer
                whatsapp_viewer.restart_current_process()
            except Exception:
                pass

        return {
            "ok": True,
            "status": "updated",
            "message": f"Actualización a versión {new_version} completada con éxito ({len(updated_files)} archivos actualizados).",
            "version": new_version,
            "updated_files_count": len(updated_files),
            "backup_count": backed_up_count
        }

    finally:
        # Limpieza de temporales
        try:
            shutil.rmtree(temp_work_dir, ignore_errors=True)
        except Exception:
            pass
