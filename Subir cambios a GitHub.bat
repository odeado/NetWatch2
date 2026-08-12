@echo off
REM Win NetWatch RMM - Sube los cambios locales a GitHub (rama main).
REM Corre esto cuando Claude te avise que dejo cambios listos -- el push
REM nunca se hace solo, asi vos decidis cuando se publican.
REM
REM OJO: si web-admin/ se sirve directo desde GitHub (Pages o algo que lea
REM el repo), la version "en vivo" recien se actualiza DESPUES de este push
REM -- no apenas Claude termina de editar los archivos.

cd /d "%~dp0"

echo ============================================
echo   Win NetWatch RMM - Subir cambios a GitHub
echo ============================================
echo.
echo Cambios sin commitear:
git status --short
echo.

set /p MENSAJE="Mensaje del commit (Enter para uno automatico, o escribe cancelar): "
if /i "%MENSAJE%"=="cancelar" (
    echo Cancelado, no se subio nada.
    pause
    exit /b
)
if "%MENSAJE%"=="" set MENSAJE=Actualizacion %date% %time%

git add -A
git commit -m "%MENSAJE%"

echo.
echo Subiendo a GitHub (rama main)...
git push origin main

echo.
echo Listo. Si arriba aparecio algun error, avisale a Claude para revisarlo.
pause
