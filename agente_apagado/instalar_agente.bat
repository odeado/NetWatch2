@echo off
REM instalar_agente.bat
REM
REM Corre esto UNA VEZ en cada PC (por RDP, con la cuenta sistemas u otra
REM cuenta administradora) despues de copiar toda la carpeta agente_apagado
REM a esa PC. Crea una tarea programada que ejecuta netwatch_agente.ps1
REM cada 1 minuto, como SYSTEM, para siempre (no depende de que alguien
REM este logueado).
REM
REM Requisito: agente_config.json ya debe existir en esta misma carpeta,
REM completado con los datos de Firebase (ver README_agente.md).

setlocal
set "CARPETA=%~dp0"
set "SCRIPT=%CARPETA%netwatch_agente.ps1"

if not exist "%SCRIPT%" (
    echo ERROR: no se encontro netwatch_agente.ps1 en esta carpeta.
    pause
    exit /b 1
)

if not exist "%CARPETA%agente_config.json" (
    echo ERROR: falta agente_config.json en esta carpeta.
    echo Copia agente_config.json.example como agente_config.json y completa los datos primero.
    pause
    exit /b 1
)

echo Creando tarea programada "NetWatch Agente Apagado"...
schtasks /create ^
    /tn "NetWatch Agente Apagado" ^
    /tr "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%SCRIPT%\"" ^
    /sc minute /mo 1 ^
    /ru SYSTEM ^
    /rl highest ^
    /f

if errorlevel 1 (
    echo.
    echo ALGO FALLO. Necesitas correr esta ventana como Administrador
    echo ^(click derecho en instalar_agente.bat -^> Ejecutar como administrador^).
    pause
    exit /b 1
)

echo.
echo Listo. La tarea "NetWatch Agente Apagado" quedo creada y va a correr cada
echo minuto desde ahora, incluso despues de reiniciar esta PC.
echo Puedes verla en el Programador de tareas de Windows.
pause
