@echo off
REM Win NetWatch RMM - Escaneo continuo de Arica, sin depender de VPN.
REM Escanea localmente cada 120s y publica el resultado a Firebase (ver
REM firebase_push.py) para que el PC central lo recoja solo. Pensado para
REM dejarlo como tarea programada "Al iniciar el sistema" en este PC.
REM
REM OJO -- 2026-07-24: bajado de --workers 150 a 20 (Arica tambien se quedaba
REM pegado seguido, mismo motivo que Rendic: 150 procesos de ping.exe en
REM paralelo es mucha carga para un PC de sucursal). Se agrego ademas un
REM bucle de auto-reinicio: si scanner.py se cae por cualquier motivo, vuelve
REM a levantarse solo a los 10s en vez de quedar detenido sin que nadie lo note.
cd /d "%~dp0"
call asegurar_python.bat
:loop
echo [%date% %time%] Iniciando scanner.py... >> scan_arica.log
"%PYEXE%" -u scanner.py --subnet 172.30.110.0/24 --repeat 120 --firebase-sitio arica --workers 20 >> scan_arica.log 2>&1
echo [%date% %time%] scanner.py termino -- reintentando en 10s... >> scan_arica.log
timeout /t 10 /nobreak >nul
goto loop
