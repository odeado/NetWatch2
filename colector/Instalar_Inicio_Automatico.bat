@echo off
title NetWatch Colector - inicio automatico
REM Crea una tarea programada que arranca el colector (oculto) cada vez que
REM este usuario inicia sesion en Windows. Ejecutalo UNA vez en el PC donde va
REM a quedar el colector. Si el PC reinicia, el colector vuelve solo apenas
REM inicie sesion (conviene dejar el inicio de sesion automatico).
cd /d "%~dp0"
schtasks /create /tn "NetWatch Colector" /tr "wscript.exe \"%~dp0IniciarColectorOculto.vbs\"" /sc onlogon /rl highest /f
if errorlevel 1 (
    echo.
    echo No se pudo crear la tarea. Ejecuta este archivo con clic derecho -^> Ejecutar como administrador.
    pause
    exit /b 1
)
echo.
echo Listo: "NetWatch Colector" arrancara solo en cada inicio de sesion.
echo Para arrancarlo ahora sin reiniciar:  schtasks /run /tn "NetWatch Colector"
echo.
pause
