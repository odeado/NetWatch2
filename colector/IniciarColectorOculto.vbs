' NetWatch Colector - lo lanza sin mostrar ventana (estilo 0), como Portadas.
Set WshShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "cmd /c cd /d """ & scriptDir & """ && IniciarColector.bat", 0, False
