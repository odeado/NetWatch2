@echo off
REM Detiene el colector (el proceso de Python y el .bat que lo relanza).
echo Deteniendo NetWatch Colector...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*colector_netwatch.py*') -or ($_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*IniciarColector.bat*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Listo.
timeout /t 2 /nobreak >nul
