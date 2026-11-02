@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title WAckup 3.0 - Panel de Control ^& Visor

rem 1. Detección automática del entorno Python (Portable embebido vs Sistema)
set "PYTHON_BIN="

if exist "%~dp0runtime\python\python.exe" (
    set "PYTHON_BIN=%~dp0runtime\python\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_BIN=python"
    ) else (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_BIN=py"
        )
    )
)

if not defined PYTHON_BIN (
    echo ========================================================
    echo   [ERROR] No se detecto un interprete de Python.
    echo   Si descargaste la version Portable de WAckup, asegurate
    echo   de que la carpeta 'runtime\python' este presente.
    echo ========================================================
    pause
    exit /b 1
)

rem 2. Si no es entorno portable embebido, verificar dependencias en desarrollo
if not exist "%~dp0runtime\python\python.exe" (
    %PYTHON_BIN% -c "import cryptography, pystray, PIL" >nul 2>&1
    if !errorlevel! neq 0 (
        echo ========================================================
        echo   Configurando dependencias necesarias de Python...
        echo ========================================================
        %PYTHON_BIN% -m pip install -r requirements.txt
        if !errorlevel! neq 0 (
            echo [ADVERTENCIA] No se pudieron verificar algunas dependencias.
        )
    )
)

rem 3. Ciclo de ejecución del servidor con soporte de reinicio en caliente
set "WACKUP_SUPERVISED=1"
if exist ".restart_flag" del /f /q ".restart_flag" >nul 2>&1
:start_server

echo Iniciando servidor WAckup 3.0...
%PYTHON_BIN% whatsapp_viewer.py %*
set "EXIT_CODE=!errorlevel!"

if exist ".restart_flag" (
    cls
    echo [i] Reinicio solicitado por el servidor...
    goto start_server
)
if "!EXIT_CODE!"=="42" (
    cls
    echo [i] Reinicio completado (codigo 42)...
    goto start_server
)

rem Si el usuario cerro normalmente el servidor desde la bandeja o Ctrl+C
if "!EXIT_CODE!"=="0" (
    exit /b 0
)

pause
