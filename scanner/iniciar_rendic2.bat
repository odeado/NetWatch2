@echo off
REM Win NetWatch RMM - Escaneo continuo de Rendic2, sin depender de VPN.
REM Escanea localmente cada 120s y publica el resultado a Firebase (ver
REM firebase_push.py) para que el PC central lo recoja solo. Pensado para
REM dejarlo como tarea programada "Al iniciar el sistema" en este PC.
REM
REM OJO -- 2026-07-24: bajado de --workers 150 a 20, mismo motivo que
REM iniciar_rendic.bat (150 procesos de ping.exe en paralelo dejaba sin
REM respuesta a PCs de sucursal mas modestos). Se agrego ademas un bucle de
REM auto-reinicio: si scanner.py se cae por cualquier motivo, vuelve a
REM levantarse solo a los 10s en vez de quedar detenido sin que nadie lo note.
cd /d "%~dp0"
call asegurar_python.bat
:loop
echo [%date% %time%] Iniciando scanner.py... >> scan_rendic2.log
"%PYEXE%" -u scanner.py --subnet 172.30.101.0/24 --repeat 120 --firebase-sitio rendic2 --workers 20 >> scan_rendic2.log 2>&1
echo [%date% %time%] scanner.py termino -- reintentando en 10s... >> scan_rendic2.log
timeout /t 10 /nobreak >nul
goto loop
