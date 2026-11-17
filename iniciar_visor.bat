@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title WAckup 3.0 - Panel de Control ^& Visor

rem 1. Deteccion automatica del entorno Python (Portable embebido vs Sistema >= 3.10)
set "PYTHON_CMD="
set "IS_PORTABLE=0"

if exist "%~dp0runtime\python\python.exe" (
    set PYTHON_CMD="%~dp0runtime\python\python.exe"
    set "IS_PORTABLE=1"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_CMD=python"
        )
    )
    if not defined PYTHON_CMD (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
            if !errorlevel! equ 0 (
                set "PYTHON_CMD=py -3"
            )
        )
    )
)

rem 2. Si no hay Python disponible, ofrecer descarga guiada del runtime portable
if not defined PYTHON_CMD (
    echo ========================================================================
    echo   WAckup - Configuracion inicial requerida
    echo ========================================================================
    echo.
    echo   No se detecto Python ^(^>= 3.10^) en este equipo ni el entorno
    echo   portable local ^('runtime\python'^).
    echo.
    echo   Que se va a hacer si aceptas:
    echo   - Descargar Python 3.12.7 oficial ^(verificado por SHA-256^) y las
    echo     librerias necesarias ^(~25 MB en total^).
    echo   - Instalarlo UNICAMENTE dentro de la carpeta 'runtime\python' de
    echo     WAckup, sin modificar tu sistema ni el Registro de Windows.
    echo.
    echo   Alternativa sin internet ^(offline^):
    echo   Descarga el paquete 'WAckup_Portable_v*.zip' desde GitHub Releases:
    echo   https://github.com/dbonotti/wackup/releases
    echo.
    choice /C SN /N /M "Deseas descargar y configurar el entorno portable ahora? [S/N]: "
    if errorlevel 2 (
        echo.
        echo [i] Configuracion cancelada por el usuario.
        pause
        exit /b 1
    )

    if not exist "%~dp0tools\bootstrap_runtime.ps1" (
        echo.
        echo [ERROR] No se encontro el script 'tools\bootstrap_runtime.ps1'.
        echo Descarga el paquete WAckup_Portable desde GitHub Releases.
        pause
        exit /b 1
    )

    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\bootstrap_runtime.ps1" -RootDir "%~dp0"
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] Fallo la configuracion automatica del entorno portable.
        echo Si estas detras de un proxy o sin internet, descarga la version
        echo WAckup_Portable_v*.zip desde: https://github.com/dbonotti/wackup/releases
        pause
        exit /b 1
    )

    set PYTHON_CMD="%~dp0runtime\python\python.exe"
    set "IS_PORTABLE=1"
)

rem 3. Verificacion y sincronizacion de dependencias
if "!IS_PORTABLE!"=="1" (
    %PYTHON_CMD% -c "import hashlib, pathlib, sys; req=pathlib.Path('requirements.txt').read_bytes(); h=hashlib.sha256(req).hexdigest(); mf=pathlib.Path(r'runtime\python\.req_sha256'); ok=mf.is_file() and mf.read_text().strip()==h; import cryptography, pystray, PIL; sys.exit(0 if ok else 2)" >nul 2>&1
    if !errorlevel! neq 0 (
        %PYTHON_CMD% -m pip --version >nul 2>&1
        if !errorlevel! equ 0 (
            echo ========================================================
            echo   Sincronizando dependencias en entorno portable...
            echo ========================================================
            %PYTHON_CMD% -m pip install --no-warn-script-location -r requirements.txt
            if !errorlevel! equ 0 (
                %PYTHON_CMD% -c "import hashlib, pathlib; pathlib.Path(r'runtime\python\.req_sha256').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())" >nul 2>&1
            ) else (
                echo [ADVERTENCIA] No se pudieron actualizar todas las dependencias.
            )
        ) else (
            %PYTHON_CMD% -c "import cryptography, pystray, PIL" >nul 2>&1
            if !errorlevel! neq 0 (
                echo [ADVERTENCIA] Faltan dependencias en runtime\python y pip no esta disponible.
            )
        )
    )
) else (
    %PYTHON_CMD% -c "import cryptography, pystray, PIL" >nul 2>&1
    if !errorlevel! neq 0 (
        echo ========================================================
        echo   Configurando dependencias necesarias de Python...
        echo ========================================================
        %PYTHON_CMD% -m pip install -r requirements.txt
        if !errorlevel! neq 0 (
            echo [ADVERTENCIA] No se pudieron verificar algunas dependencias.
        )
    )
)

rem 4. Ciclo de ejecucion del servidor con soporte de reinicio en caliente
set "WACKUP_SUPERVISED=1"
if exist ".restart_flag" del /f /q ".restart_flag" >nul 2>&1
:start_server

echo Iniciando servidor WAckup 3.0...
%PYTHON_CMD% whatsapp_viewer.py %*
set "EXIT_CODE=!errorlevel!"

if exist ".restart_flag" (
    cls
    echo [i] Reinicio solicitado por el servidor...
    goto start_server
)
if "!EXIT_CODE!"=="42" (
    cls
    echo [i] Reinicio completado ^(codigo 42^)...
    goto start_server
)

rem Si el usuario cerro normalmente el servidor desde la bandeja o Ctrl+C
if "!EXIT_CODE!"=="0" (
    exit /b 0
)

pause
