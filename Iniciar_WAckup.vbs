' ========================================================
' Iniciar_WAckup.vbs - Lanzador silencioso de WAckup 3.0
' Inicia el servidor en segundo plano sin ventana negra CMD
' El control se realiza desde la Bandeja del Sistema y el Visor Web
' ========================================================

Option Explicit

Dim WshShell, fso, scriptDir, runtimePythonw, cmdToRun, exitCode

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' 1. Detectar si existe el entorno Portable embebido
runtimePythonw = fso.BuildPath(scriptDir, "runtime\python\pythonw.exe")

If fso.FileExists(runtimePythonw) Then
    cmdToRun = """" & runtimePythonw & """ """ & fso.BuildPath(scriptDir, "whatsapp_viewer.py") & """"
Else
    ' Fallback al pythonw instalado en el sistema
    cmdToRun = "pythonw.exe """ & fso.BuildPath(scriptDir, "whatsapp_viewer.py") & """"
End If

On Error Resume Next
exitCode = WshShell.Run(cmdToRun, 0, False)

If Err.Number <> 0 Then
    Err.Clear
    ' Intento secundario con cmd start /b
    cmdToRun = "cmd.exe /c start /b pythonw """ & fso.BuildPath(scriptDir, "whatsapp_viewer.py") & """"
    exitCode = WshShell.Run(cmdToRun, 0, False)
    If Err.Number <> 0 Then
        MsgBox "No se encontró un entorno Python funcional para WAckup." & vbCrLf & vbCrLf & _
               "Por favor inicia WAckup usando el archivo 'iniciar_visor.bat'.", _
               vbExclamation, "WAckup 3.0"
    End If
End If

Set WshShell = Nothing
Set fso = Nothing
