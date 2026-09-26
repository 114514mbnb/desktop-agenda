' Start the desktop-agenda client with no console window.
' Used by the autostart shortcut; output is appended to data\panel.log.
' ASCII-only on purpose (wscript reads .vbs as ANSI).
Option Explicit

Dim shell, fso, root, pythonw, mainPy, logFile, cmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(root)          ' tools\.. -> project root

pythonw = root & "\runtime\pythonw.exe"
If Not fso.FileExists(pythonw) Then pythonw = "pythonw.exe"

mainPy = root & "\main.py"
logFile = root & "\data\panel.log"

cmd = "cmd /c """"" & pythonw & """ -B """ & mainPy & """ --client >> """ & logFile & """ 2>&1"
shell.Run cmd, 0, False
