@echo off
REM Win NetWatch RMM - Escaneo continuo de Rendic, sin depender de VPN.
REM Escanea localmente cada 120s y publica el resultado a Firebase (ver
REM firebase_push.py) para que el PC central lo recoja solo. Pensado para
REM dejarlo como tarea programada "Al iniciar el sistema" en este PC.
REM
REM OJO -- 2026-07-24: con --workers 150 este equipo (un PC de reemplazo,
REM mas modesto) se quedaba pegado/sin responder por RDP durante cada ciclo
REM de escaneo (150 procesos de ping.exe + 200 hilos de puertos en paralelo
REM es mucha carga para un PC que ademas se usa para otra cosa). Se bajo a
REM 20 workers -- el escaneo demora un poco mas pero entra comodo en los
REM 120s del ciclo sin competir tan fuerte por CPU/procesos. Ademas se
REM agrego un bucle de auto-reinicio: si scanner.py se cae por cualquier
REM motivo, este bucle lo vuelve a levantar solo a los 10s, en vez de
REM quedar detenido hasta que alguien lo note (visto en la practica: el
REM proceso murio el 21-jul sin dejar error y nadie lo reinicio hasta el 24).
cd /d "%~dp0"
call asegurar_python.bat
:loop
echo [%date% %time%] Iniciando scanner.py... >> scan_rendic.log
"%PYEXE%" -u scanner.py --subnet 172.30.100.0/24 --repeat 120 --firebase-sitio rendic --workers 20 >> scan_rendic.log 2>&1
echo [%date% %time%] scanner.py termino -- reintentando en 10s... >> scan_rendic.log
timeout /t 10 /nobreak >nul
goto loop
