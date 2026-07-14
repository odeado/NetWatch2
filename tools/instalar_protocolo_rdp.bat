@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS1_PATH=%SCRIPT_DIR%rdp_protocol_handler.ps1"

echo ============================================================
echo  Win NetWatch RMM - Instalar protocolo netwatchrdp://
echo ============================================================
echo.
echo Esto registra el protocolo "netwatchrdp://" en tu usuario de Windows
echo (no requiere permisos de administrador), para que el boton "RDP" de
echo la webapp abra Escritorio Remoto directo, sin descargar nada.
echo.

reg add "HKCU\Software\Classes\netwatchrdp" /ve /d "URL:NetWatch RDP Protocol" /f
reg add "HKCU\Software\Classes\netwatchrdp" /v "URL Protocol" /t REG_SZ /d "" /f
reg add "HKCU\Software\Classes\netwatchrdp\shell\open\command" /ve /d "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%PS1_PATH%\" \"%%1\"" /f

echo.
echo Listo. La primera vez que uses el boton RDP, el navegador te va a
echo preguntar si quieres abrir "NetWatch RDP Protocol" - dile que si
echo (y marca "recordar mi eleccion" si aparece esa opcion).
echo.
pause
