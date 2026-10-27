@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title WAckup - Panel de Control ^& Visor

rem 1. Verificar dependencias de Python
python -c "import cryptography" >nul 2>&1
if %errorlevel% neq 0 (
    echo ========================================================
    echo   Configurando dependencias necesarias de Python...
    echo ========================================================
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo Error instalando dependencias. Verifica tu conexion y version de Python.
        pause
        exit /b %errorlevel%
    )
)

rem 2. Comprobar actualizaciones en GitHub (garantizando seguridad de datos)
where git >nul 2>&1
if %errorlevel% equ 0 if exist ".git" (
    echo Verificando si hay actualizaciones en GitHub...
    git config --global --add safe.directory "%CD:\=/%" >nul 2>&1
    git -c safe.directory=* fetch origin main --quiet >nul 2>&1
    if !errorlevel! equ 0 (
        set BEHIND=0
        for /f %%i in ('git -c safe.directory=* rev-list HEAD..origin/main --count 2^>nul') do set BEHIND=%%i
        if defined BEHIND if not "!BEHIND!"=="0" (
            echo.
            echo ========================================================
            echo   [ACTUALIZACION DISPONIBLE]
            echo   Hay !BEHIND! actualizacion^(es^) disponible^(s^) en GitHub.
            echo ========================================================
            echo Ultimos cambios:
            git -c safe.directory=* log HEAD..origin/main --oneline -5
            echo ========================================================
            echo.
            set "APPLY_UPDATE=S"
            set /p "APPLY_UPDATE=Deseas actualizar a la ultima version ahora? (S/N) [S]: "
            if /i "!APPLY_UPDATE!"=="S" (
                echo.
                echo [1/3] Creando copia de seguridad preventiva de la base de datos...
                python -c "import config; config.backup_all_databases()"
                
                echo [2/3] Descargando ultimos cambios de GitHub...
                set EXT_UPDATED=0
                for /f %%f in ('git -c safe.directory=* diff HEAD origin/main --name-only -- extension 2^>nul') do set EXT_UPDATED=1
                
                git -c safe.directory=* pull origin main
                if !errorlevel! equ 0 (
                    echo [OK] Codigo actualizado con exito.
                    if "!EXT_UPDATED!"=="1" (
                        echo.
                        echo [i] Se actualizaron archivos de la extension.
                        echo     En WhatsApp Web veras un boton para recargarla en 1 clic.
                    )
                ) else (
                    echo [ADVERTENCIA] No se pudo completar la descarga. Continuando...
                )

                echo [3/3] Verificando dependencias...
                python -m pip install -r requirements.txt --quiet
                echo.
            )
        )
    )
)

echo Iniciando servidor WAckup...
python whatsapp_viewer.py
pause
