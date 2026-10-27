@echo off
cd /d "%~dp0"
title Logs de Sincronizacion - WAckup
cls
echo ========================================================================
echo                   WACKUP - LOGS DE SINCRONIZACION EN VIVO
echo ========================================================================
echo   Presiona Ctrl + C para cerrar esta ventana de logs en cualquier momento.
echo ========================================================================
echo.

if not exist "data\logs" mkdir "data\logs"
if not exist "data\logs\sync.log" (
    if exist "logs\sync.log" (
        copy /y "logs\sync.log" "data\logs\sync.log" >nul 2>&1
    ) else (
        echo [%date% %time%] Servidor iniciado. Esperando sincronizaciones... > "data\logs\sync.log"
    )
)

powershell -NoExit -ExecutionPolicy Bypass -Command "$Host.UI.RawUI.WindowTitle = 'Logs de Sincronizacion - WAckup'; Get-Content -Path 'data\logs\sync.log' -Wait -Tail 50"
