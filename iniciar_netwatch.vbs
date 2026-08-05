' Win NetWatch RMM - Lanza el monitor y la web sin mostrar ninguna ventana.
' Se usa WScript.Shell en vez de "start /min" porque en Windows con Windows
' Terminal como terminal por defecto, "/min" no siempre se respeta y las
' ventanas igual aparecen. El estilo 0 aca abajo si las oculta de verdad.
Dim shell, fso, base, q
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)

' monitor_loop.bat relanza monitor.py solo si se cae -- ver comentario
' adentro del .bat para el detalle.
shell.Run "cmd /c cd /d " & q & base & "\scanner" & q & " && monitor_loop.bat", 0, False
shell.Run "cmd /c cd /d " & q & base & "\webapp" & q & " && python app.py >> web_error.log 2>&1", 0, False

' Puente de toasts hacia la pantalla chica de 1024x600 (ver comentario en el
' propio script) -- pythonw.exe en vez de python.exe para que tampoco abra
' ventana negra.
shell.Run "cmd /c cd /d " & q & base & "\tools" & q & " && pythonw toast_pantalla1.py", 0, False
