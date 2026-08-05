@echo off
REM Win NetWatch RMM - Apaga el monitor y la pagina web que quedaron
REM corriendo minimizados en segundo plano tras usar "Iniciar NetWatch.bat".

echo Deteniendo NetWatch...
REM Se mata tambien el cmd.exe de monitor_loop.bat (no solo python.exe):
REM ese script relanza monitor.py solo si se cae, asi que si se deja vivo
REM iba a volver a levantar el monitor apenas Stop-Process mata al hijo.
REM
REM OJO: antes el filtro buscaba 'webapp\app.py' -- pero iniciar_netwatch.vbs
REM hace "cd /d ...\webapp && python app.py", asi que python.exe arranca con
REM la ruta RELATIVA "app.py" (sin el prefijo "webapp\"), ese texto nunca
REM aparecia en su CommandLine y el proceso de la web quedaba vivo siempre
REM (el que habia que ir a matar a mano). Se busca solo "app.py" ahora, igual
REM que ya se hacia con "monitor.py".
REM pythonw.exe (no python.exe) es el que corre toast_pantalla1.py -- ver
REM iniciar_netwatch.vbs, se lanza sin ventana para no molestar la pantalla chica.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -and ($_.CommandLine -like '*monitor.py*' -or $_.CommandLine -like '*app.py*')) -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*monitor_loop.bat*') -or ($_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*toast_pantalla1.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Listo.
timeout /t 2 /nobreak >nul
