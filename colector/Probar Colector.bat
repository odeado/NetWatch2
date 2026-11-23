@echo off
title NetWatch Colector - prueba
REM Prueba de conexion: lee Firebase y muestra que veria el colector (sitios,
REM equipos criticos), pero NO publica nada ni guarda estado.
cd /d "%~dp0"
call asegurar_python.bat
"%PYEXE%" colector_netwatch.py --solo-leer
echo.
pause
