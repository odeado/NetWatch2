' Win NetWatch RMM - Lanza el monitor y la web sin mostrar ninguna ventana.
' Se usa WScript.Shell en vez de "start /min" porque en Windows con Windows
' Terminal como terminal por defecto, "/min" no siempre se respeta y las
' ventanas igual aparecen. El estilo 0 aca abajo si las oculta de verdad.
Dim shell, fso, base, q
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)

shell.Run "cmd /c cd /d " & q & base & "\scanner" & q & " && python monitor.py >> monitor_error.log 2>&1", 0, False
shell.Run "cmd /c cd /d " & q & base & "\webapp" & q & " && python app.py >> web_error.log 2>&1", 0, False
