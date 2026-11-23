@echo off
title Compilar APK - NetWatch Colector
cd /d "%~dp0"

echo.
echo ============================================================
echo    COMPILANDO APK - NETWATCH COLECTOR (Android)
echo ============================================================
echo.

if not exist "colector_config.json" (
    echo [ERROR] Falta colector_config.json en esta carpeta.
    pause
    exit /b 1
)

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [ERROR] No se encontro Python para preparar la configuracion.
    pause
    exit /b 1
)
%PY% preparar_apk_config.py
if errorlevel 1 (
    pause
    exit /b 1
)

if not defined JAVA_HOME (
    if exist "C:\Program Files\Android\Android Studio\jbr\bin\java.exe" (
        set "JAVA_HOME=C:\Program Files\Android\Android Studio\jbr"
    )
)
if not defined JAVA_HOME (
    echo [ERROR] No se encontro Java. Abre la carpeta android en Android Studio
    echo         y usa Build ^> Build APK, o define JAVA_HOME.
    pause
    exit /b 1
)
echo [OK] Java: %JAVA_HOME%
echo.

cd /d "%~dp0android"
call gradlew.bat assembleDebug
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la compilacion. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

copy /Y "app\build\outputs\apk\debug\app-debug.apk" "%~dp0NetWatchColector.apk" >nul
echo.
echo ============================================================
echo   APK LISTO:  %~dp0NetWatchColector.apk
echo ============================================================
echo  Pasalo al celular (WhatsApp, Drive o cable USB) e instalalo.
echo.
explorer /select,"%~dp0NetWatchColector.apk"
pause
