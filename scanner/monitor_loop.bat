@echo off
REM Win NetWatch RMM - Wrapper de reinicio automatico para monitor.py
REM ===================================================================
REM Si monitor.py se cae por cualquier motivo (error no manejado, perdida
REM de conexion a la base, etc.), este loop lo vuelve a levantar solo a
REM los 5 segundos en vez de dejar el monitoreo apagado hasta que alguien
REM se de cuenta y lo prenda a mano.
REM
REM "Detener NetWatch.bat" mata este cmd.exe (por el nombre del script)
REM ademas del python.exe hijo, para que no se reinicie solo al presionar
REM Detener.
:loop
python monitor.py --all >> monitor_error.log 2>&1
echo [%date% %time%] monitor.py se cerro -- reiniciando en 5s... >> monitor_error.log
timeout /t 5 /nobreak >nul
goto loop
