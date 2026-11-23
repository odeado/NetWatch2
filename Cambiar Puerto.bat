@echo off
REM Win NetWatch RMM - Cambia el puerto de la pagina web.
REM
REM Uso: doble clic (pregunta el puerto nuevo) o desde una consola:
REM   "Cambiar Puerto.bat" 6001          (pide confirmacion)
REM   "Cambiar Puerto.bat" 6001 -y       (sin preguntar)
REM
REM El puerto vive en UN solo archivo, netwatch_puerto.txt (raiz del
REM proyecto), que leen la web (webapp\app.py), el puente de toasts
REM (tools\toast_pantalla1.py) y "Iniciar NetWatch.bat". Este .bat valida el
REM puerto, comprueba que nadie mas lo use, detiene NetWatch, escribe el
REM archivo y lo vuelve a iniciar.

setlocal enableextensions
set "BASE=%~dp0"
set "ARCHIVO=%BASE%netwatch_puerto.txt"

set "ACTUAL=6001"
if exist "%ARCHIVO%" set /p ACTUAL=<"%ARCHIVO%"
set "ACTUAL=%ACTUAL: =%"

echo ============================================
echo   NetWatch - Cambiar puerto de la pagina web
echo ============================================
echo Puerto actual: %ACTUAL%
echo.

set "NUEVO=%~1"
if "%NUEVO%"=="" set /p "NUEVO=Puerto nuevo (1024-65535, Enter para cancelar): "
if "%NUEVO%"=="" goto cancelar
set "NUEVO=%NUEVO: =%"

REM Solo 4 o 5 digitos, sin ceros a la izquierda (set /a los leeria como octal).
echo %NUEVO%| findstr /r "^[1-9][0-9][0-9][0-9]$ ^[1-9][0-9][0-9][0-9][0-9]$" >nul
if errorlevel 1 goto invalido
if %NUEVO% LSS 1024 goto invalido
if %NUEVO% GTR 65535 goto invalido

if "%NUEVO%"=="%ACTUAL%" (
    echo NetWatch ya esta configurado en el puerto %NUEVO%. No hay nada que cambiar.
    goto fin
)

echo Revisando que el puerto %NUEVO% este libre...
powershell -NoProfile -ExecutionPolicy Bypass -File "%BASE%tools\revisar_puerto.ps1" -Puerto %NUEVO%
if errorlevel 2 goto ocupado

if /i "%~2"=="-y" goto aplicar
echo.
echo Se va a detener NetWatch, cambiar el puerto %ACTUAL% a %NUEVO% y volver a iniciarlo.
choice /c SN /m "Continuar"
if errorlevel 2 goto cancelar

:aplicar
call "%BASE%Detener NetWatch.bat"
(echo %NUEVO%)>"%ARCHIVO%"
echo.
echo Puerto cambiado a %NUEVO%. Iniciando NetWatch...
start "" "%BASE%Iniciar NetWatch.bat"
echo.
echo Listo. La pagina queda en http://localhost:%NUEVO%
echo.
echo Si otras PCs abren la pagina o usan el recopilador de inventario
echo (tools\agente_inventario.ps1 -ServerUrl http://IP-SERVIDOR:%ACTUAL%),
echo cambia el puerto ahi tambien y revisa que el Firewall de Windows de
echo este servidor permita el puerto %NUEVO%.
goto fin

:invalido
echo.
echo "%NUEVO%" no es un puerto valido. Usa un numero entre 1024 y 65535, sin ceros a la izquierda.
goto fin

:ocupado
echo.
echo No se cambio nada. Elige otro puerto.
goto fin

:cancelar
echo.
echo Cancelado. No se cambio nada.

:fin
echo.
pause
endlocal
