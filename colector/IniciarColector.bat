@echo off
title NetWatch Colector
REM NetWatch Colector -- vigilante liviano (ver README_colector.md).
REM Deja este .bat corriendo (o usa Instalar_Inicio_Automatico.bat): lee los
REM escaneos de los sitios en Firebase, detecta quien cae / quien vuelve y lo
REM publica para la app del celular. Si el proceso se cae, se relanza solo.
cd /d "%~dp0"

if not exist "colector_config.json" (
    echo Falta colector_config.json -- copia colector_config.json.example y completa los datos.
    pause
    exit /b 1
)

call asegurar_python.bat
:loop
echo [%date% %time%] Iniciando colector_netwatch.py... >> colector_arranque.log
"%PYEXE%" -u colector_netwatch.py
echo [%date% %time%] colector_netwatch.py termino -- reintentando en 10s... >> colector_arranque.log
timeout /t 10 /nobreak >nul
goto loop
