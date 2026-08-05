@echo off
REM Win NetWatch RMM - Verifica si Python esta disponible en este equipo.
REM Compatible con Windows 7 (PowerShell 2.0): a proposito NO descarga nada
REM por internet desde el script, porque Windows 7 + PS2 no negocia bien
REM TLS 1.2 (los servidores de python.org lo exigen) y ademas Python 3.9+
REM ya pide Windows 8.1 o superior (verificado en pantalla: "At least
REM Windows 8.1 or Windows Server 2012 are required to install Python
REM 3.9.13"). La ultima version que SI corre en Windows 7 es la 3.8.10.
REM En vez de descargar, usa un instalador que ya debe estar copiado en
REM esta misma carpeta.
REM
REM Instala SOLO PARA EL USUARIO ACTUAL (sin pedir permisos de admin),
REM para evitar que la instalacion falle en silencio por falta de
REM privilegios cuando corre sin sesion interactiva (tarea programada).
REM
REM Pasos unicos por equipo (si no tiene Python):
REM 1) En un PC normal, bajar:
REM    https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe
REM    (o la version x86 si el equipo es de 32 bits)
REM 2) Copiarlo a esta carpeta (scanner) con ese mismo nombre exacto.
REM 3) Correr el iniciar_xxx.bat -- este script lo detecta y lo instala solo.

REM OJO -- 2026-07-24 (visto en Rendic, PC nuevo): en Windows 10/11 con el
REM "alias de ejecucion de aplicaciones" de Python activado, "where python"
REM SI encuentra un python.exe (el stub de Microsoft Store) aunque no haya
REM Python real instalado -- ese stub no ejecuta nada, solo imprime "no se
REM encontro Python; ejecutar sin argumentos para instalar desde el
REM Microsoft Store..." y termina. Antes esto se daba por buena (el chequeo
REM solo miraba si "where" encontraba ALGO), asi que el scanner quedaba en
REM un bucle infinito de "Iniciando... termino" sin escanear nunca nada. Ahora
REM se valida que la salida de "python --version" empiece con "Python " de
REM verdad antes de confiar en ese PATH.
set "PYEXE=python"
where python >nul 2>&1
if not %errorlevel%==0 goto :buscar_local

set "PYCHECK="
for /f "delims=" %%v in ('python --version 2^>^&1') do if not defined PYCHECK set "PYCHECK=%%v"
echo %PYCHECK% | findstr /b /c:"Python " >nul
if %errorlevel%==0 goto :fin

echo [%date% %time%] "python" esta en el PATH pero es el alias de Microsoft
echo Store (no Python real) -- se ignora y se busca/instala una copia real.

:buscar_local

set "PYLOCAL=%LocalAppData%\Programs\Python\Python38\python.exe"
if exist "%PYLOCAL%" (
    set "PYEXE=%PYLOCAL%"
    goto :fin
)

REM Prueba primero el instalador de 64 bits, y si no esta, el de 32 bits
REM (equipos viejos de sucursal a veces son de 32 bits -- visto en la
REM practica el 2026-07-20 con un PC de reemplazo en Rendic).
set "PYINST=%~dp0python-3.8.10-amd64.exe"
if not exist "%PYINST%" set "PYINST=%~dp0python-3.8.10.exe"
if not exist "%PYINST%" (
    echo [%date% %time%] ERROR: Python no esta instalado y no encontre un
    echo instalador para copiarlo. Copia UNO de estos dos en esta carpeta,
    echo segun si el equipo es de 64 o 32 bits, con el nombre exacto:
    echo   64 bits: python-3.8.10-amd64.exe
    echo     https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe
    echo   32 bits: python-3.8.10.exe
    echo     https://www.python.org/ftp/python/3.8.10/python-3.8.10.exe
    goto :fin
)

echo [%date% %time%] Python no encontrado -- instalando (sin admin) desde instalador local...
echo Deberia aparecer una ventana con barra de progreso; se cierra sola al terminar.
"%PYINST%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=0 /log "%~dp0python_install.log"
echo [%date% %time%] Instalador de Python termino.

if exist "%PYLOCAL%" (
    set "PYEXE=%PYLOCAL%"
    goto :fin
)

where python >nul 2>&1
if %errorlevel%==0 (
    set "PYEXE=python"
    goto :fin
)

echo [%date% %time%] ERROR: la instalacion de Python parece haber fallado.
echo Revisa "%~dp0python_install.log" para ver el detalle.

:fin
