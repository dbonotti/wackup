"""
tools/build_portable.py - Constructor del Paquete Portable para Windows
========================================================================
Genera de forma 100% automatizada el paquete autónomo de WAckup 3.0 para Windows
en dist/WAckup_Portable_v{VERSION}.zip.

CARACTERÍSTICAS:
- Descarga el runtime oficial Python 3.12 Embeddable amd64 de python.org.
- Configura python312._pth con import site y ./Lib/site-packages.
- Empaqueta las dependencias en runtime/python/Lib/site-packages.
- Incluye únicamente archivos de producción (cero fugas de data/, .git/ o pruebas).
- Genera el ZIP final con hash SHA256 para verificación.
"""

import os
import sys
import shutil
import zipfile
import hashlib
import tempfile
import subprocess
import urllib.request
from typing import Optional

# Directorio raíz del proyecto
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT_DIR, "tools", ".cache")
DIST_DIR = os.path.join(ROOT_DIR, "dist")
VERSION_FILE = os.path.join(ROOT_DIR, "VERSION")

PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
PYTHON_EMBED_ZIP_NAME = "python-3.12.7-embed-amd64.zip"
# Hash SHA-256 oficial de Python 3.12.7 Embeddable (amd64) publicado por python.org (SEC-16)
PYTHON_EMBED_SHA256 = "0d57bb6cb078b74d23dbfe91f77d6780d45bed328911609f1f7ee2ba1606bf44"


def get_version() -> str:
    if os.path.isfile(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return "3.0.0"


def calculate_sha256(filepath: str) -> str:
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def download_python_embed(target_path: str):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.isfile(target_path):
        cached_hash = calculate_sha256(target_path)
        if cached_hash.lower() == PYTHON_EMBED_SHA256.lower():
            print(f"[CACHE] Usando Python embeddable verificado en caché: {target_path}")
            return
        else:
            print(f"[AVISO] Hash SHA-256 de archivo en caché inválido ({cached_hash}), redescargando...")
            try:
                os.remove(target_path)
            except OSError:
                pass

    print(f"[DESCARGA] Descargando Python 3.12 Embeddable desde python.org...")
    print(f"           URL: {PYTHON_EMBED_URL}")
    req = urllib.request.Request(PYTHON_EMBED_URL, headers={"User-Agent": "WAckup-Builder/3.0.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(target_path, "wb") as out_f:
        shutil.copyfileobj(resp, out_f)
    print(f"[OK] Descarga completada ({os.path.getsize(target_path) / 1024 / 1024:.2f} MB)")

    downloaded_hash = calculate_sha256(target_path)
    if downloaded_hash.lower() != PYTHON_EMBED_SHA256.lower():
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise ValueError(
            f"Fallo de integridad criptográfica: el hash SHA-256 ({downloaded_hash}) "
            f"no coincide con el esperado ({PYTHON_EMBED_SHA256})."
        )
    print(f"[OK] Integridad criptográfica SHA-256 de Python 3.12.7 verificada con éxito.")


def setup_embeddable_python(target_python_dir: str):
    """Configura el entorno python embebido para soportar paquetes en Lib/site-packages."""
    os.makedirs(target_python_dir, exist_ok=True)
    cache_zip = os.path.join(CACHE_DIR, PYTHON_EMBED_ZIP_NAME)
    download_python_embed(cache_zip)

    print(f"[EXTRACCION] Extrayendo runtime en {target_python_dir}...")
    with zipfile.ZipFile(cache_zip, "r") as zf:
        zf.extractall(target_python_dir)

    # Modificar python312._pth para habilitar import site y Lib/site-packages
    pth_files = [f for f in os.listdir(target_python_dir) if f.endswith("._pth")]
    if pth_files:
        pth_path = os.path.join(target_python_dir, pth_files[0])
        print(f"[CONFIG] Habilitando import site en {pth_files[0]}...")
        with open(pth_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        has_site_packages = False
        for line in lines:
            stripped = line.strip()
            if stripped == "#import site" or stripped == "import site":
                new_lines.append("import site\n")
            elif "site-packages" in stripped:
                has_site_packages = True
                new_lines.append(line)
            else:
                new_lines.append(line)

        if not has_site_packages:
            new_lines.insert(0, "Lib/site-packages\n")
            new_lines.insert(1, ".\n")

        with open(pth_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)


def install_dependencies(target_python_dir: str):
    """Instala las dependencias de requirements.txt en Lib/site-packages."""
    site_packages_dir = os.path.join(target_python_dir, "Lib", "site-packages")
    os.makedirs(site_packages_dir, exist_ok=True)
    req_file = os.path.join(ROOT_DIR, "requirements.txt")

    print(f"[PIP] Instalando dependencias en {site_packages_dir}...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", site_packages_dir,
        "--no-user",
        "-r", req_file
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[ERROR] Error al instalar dependencias:\n{res.stderr}")
        raise RuntimeError("Fallo instalando dependencias con pip --target")
    print(f"[OK] Dependencias instaladas exitosamente.")


def copy_production_files(dist_wackup_dir: str):
    """Copia únicamente archivos de código y recursos limpios, excluyendo datos personales."""
    print(f"[COPIA] Empaquetando archivos de produccion en {dist_wackup_dir}...")

    # Archivos raíz permitidos
    allowed_root_files = [
        "whatsapp_viewer.py",
        "config.py",
        "db_manager.py",
        "media_manager.py",
        "entity_extractor.py",
        "sse_hub.py",
        "tray_manager.py",
        "updater.py",
        "VERSION",
        "requirements.txt",
        "iniciar_visor.bat",
        "ver_logs.bat",
        "README.md",
        "MANUAL_DE_USUARIO.md"
    ]

    for fname in allowed_root_files:
        src = os.path.join(ROOT_DIR, fname)
        if os.path.isfile(src):
            dst = os.path.join(dist_wackup_dir, fname)
            shutil.copy2(src, dst)

    # Carpetas completas permitidas (filtrando __pycache__)
    for folder in ["web", "extension", "assets"]:
        src_folder = os.path.join(ROOT_DIR, folder)
        if os.path.isdir(src_folder):
            dst_folder = os.path.join(dist_wackup_dir, folder)
            shutil.copytree(
                src_folder,
                dst_folder,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db")
            )

    # Asegurar que data/ esté completamente ausente o limpio
    assert not os.path.exists(os.path.join(dist_wackup_dir, "data")), "ERROR CRÍTICO: data/ se copió por error"
    assert not os.path.exists(os.path.join(dist_wackup_dir, ".git")), "ERROR CRÍTICO: .git se copió por error"


def build_zip_package(version: str, source_dir: str, output_zip_path: str):
    """Comprime el contenido en un archivo ZIP listo para distribución."""
    print(f"[ZIP] Comprimiendo paquete final: {output_zip_path}...")
    os.makedirs(os.path.dirname(output_zip_path), exist_ok=True)

    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, os.path.dirname(source_dir))
                zf.write(full_path, rel_path)

    size_mb = os.path.getsize(output_zip_path) / 1024 / 1024
    sha = calculate_sha256(output_zip_path)
    print("\n" + "=" * 60)
    print("  PAQUETE PORTABLE GENERADO CON ÉXITO")
    print("=" * 60)
    print(f"  Archivo : {output_zip_path}")
    print(f"  Versión : {version}")
    print(f"  Tamaño  : {size_mb:.2f} MB")
    print(f"  SHA256  : {sha}")
    print("=" * 60 + "\n")


def main():
    version = get_version()
    print(f"Iniciando empaquetado de WAckup Portable v{version}...")

    os.makedirs(DIST_DIR, exist_ok=True)
    temp_build_root = tempfile.mkdtemp(prefix="wackup_portable_build_")

    try:
        dist_wackup_dir = os.path.join(temp_build_root, f"WAckup_Portable_v{version}")
        runtime_python_dir = os.path.join(dist_wackup_dir, "runtime", "python")

        # 1. Preparar runtime embeddable
        setup_embeddable_python(runtime_python_dir)

        # 2. Instalar dependencias
        install_dependencies(runtime_python_dir)

        # 3. Copiar archivos de producción
        copy_production_files(dist_wackup_dir)

        # 4. Crear ZIP final
        output_zip_name = f"WAckup_Portable_v{version}.zip"
        output_zip_path = os.path.join(DIST_DIR, output_zip_name)
        build_zip_package(version, dist_wackup_dir, output_zip_path)

    finally:
        try:
            shutil.rmtree(temp_build_root, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
