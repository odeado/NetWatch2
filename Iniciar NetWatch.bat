@echo off
REM Win NetWatch RMM - Inicio rapido con un clic
REM Lanza el monitor y la pagina web totalmente ocultos (via
REM iniciar_netwatch.vbs, sin ninguna ventana ni parpadeo) y abre el
REM navegador solo. La consola del monitor ya se ve embebida dentro de la
REM pagina web, asi que no hace falta ver esas ventanas.
REM
REM Si algo falla, revisa:
REM   scanner\monitor_error.log
REM   webapp\web_error.log
REM
REM Para apagar NetWatch, usa "Detener NetWatch.bat".

setlocal
set "BASE=%~dp0"

wscript "%BASE%iniciar_netwatch.vbs"

timeout /t 4 /nobreak >nul

REM El puerto sale de netwatch_puerto.txt (se cambia con "Cambiar Puerto.bat").
set "PUERTO=6001"
if exist "%BASE%netwatch_puerto.txt" set /p PUERTO=<"%BASE%netwatch_puerto.txt"
set "PUERTO=%PUERTO: =%"
start "" "http://localhost:%PUERTO%"
exit
