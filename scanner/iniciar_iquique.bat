@echo off
REM Win NetWatch RMM - Escaneo continuo de Iquique, sin depender de VPN.
REM Escanea localmente cada 120s y publica el resultado a Firebase (ver
REM firebase_push.py) para que el PC central lo recoja solo. Pensado para
REM dejarlo como tarea programada "Al iniciar el sistema" en este PC.
REM
REM OJO -- 2026-08-31: Iquique llevaba 16 dias sin reportar porque este
REM script no tenia bucle de auto-reinicio (a diferencia de
REM iniciar_rendic.bat/iniciar_rendic2.bat/iniciar_arica.bat, que ya lo
REM tenian desde el 24-jul por el mismo motivo: "el proceso murio sin dejar
REM error y nadie lo reinicio hasta que alguien lo noto"). Se agrega el
REM mismo bucle y se baja de 150 a 20 workers -- 150 procesos de ping.exe en
REM paralelo es la causa mas probable de que scanner.py se cuelgue en un PC
REM de sucursal, mismo diagnostico que ya se hizo en Rendic/Rendic2/Arica.
cd /d "%~dp0"
call asegurar_python.bat
:loop
echo [%date% %time%] Iniciando scanner.py... >> scan_iquique.log
"%PYEXE%" -u scanner.py --subnet 172.30.112.0/24 --repeat 120 --firebase-sitio iquique --workers 20 >> scan_iquique.log 2>&1
echo [%date% %time%] scanner.py termino -- reintentando en 10s... >> scan_iquique.log
timeout /t 10 /nobreak >nul
goto loop
