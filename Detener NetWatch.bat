@echo off
REM Win NetWatch RMM - Apaga el monitor y la pagina web que quedaron
REM corriendo minimizados en segundo plano tras usar "Iniciar NetWatch.bat".

echo Deteniendo NetWatch...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*monitor.py*' -or $_.CommandLine -like '*webapp\\app.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Listo.
timeout /t 2 /nobreak >nul
