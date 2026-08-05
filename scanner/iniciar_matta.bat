@echo off
REM Win NetWatch RMM - Escaneo continuo de Matta, sin depender de VPN.
REM Escanea localmente cada 120s y publica el resultado a Firebase (ver
REM firebase_push.py) para que el PC central lo recoja solo. Pensado para
REM dejarlo como tarea programada "Al iniciar el sistema" en este PC.
cd /d "%~dp0"
C:\Python314\python.exe -u scanner.py --subnet 172.30.102.0/24 --repeat 120 --firebase-sitio matta --workers 150 >> scan_matta.log 2>&1
