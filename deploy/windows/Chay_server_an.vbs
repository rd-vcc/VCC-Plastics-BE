' VCC Plastics MES - bat server HOAN TOAN AN: khong mo cua so cmd nao.
' Nhap dup file nay. Co the chep ra thu muc goc (canh VCC-Plastics-BE va VCC-Plastics-FE).
' Thanh cong: thong bao nho tu tat sau 6 giay. Loi: hop thoai dung lai de doc.
Option Explicit
Dim fso, sh, here, ps1, rc, msg
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)

ps1 = FindScript(here)
If ps1 = "" Then
    MsgBox "Khong tim thay server.ps1." & vbCrLf & "Hay dat file nay canh thu muc VCC-Plastics-BE.", vbCritical, "VCC Plastics MES"
    WScript.Quit 1
End If

sh.Popup "Dang bat server VCC Plastics MES..." & vbCrLf & "Vui long cho khoang 20-40 giay.", 3, "VCC Plastics MES", vbInformation
rc = sh.Run("powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """ start", 0, True)
msg = LastResult(ps1)

If rc = 0 Then
    sh.Popup "Server dang chay an." & vbCrLf & vbCrLf & msg, 6, "VCC Plastics MES", vbInformation
Else
    MsgBox "Bat server KHONG thanh cong." & vbCrLf & vbCrLf & msg, vbCritical, "VCC Plastics MES"
End If
WScript.Quit rc

Function FindScript(dir)
    Dim c
    FindScript = ""
    For Each c In Array(dir & "\server.ps1", dir & "\VCC-Plastics-BE\deploy\windows\server.ps1", dir & "\VCC-Plastics-BE-main\deploy\windows\server.ps1")
        If fso.FileExists(c) Then FindScript = c: Exit Function
    Next
End Function

Function LastResult(scriptPath)
    Dim f
    f = fso.BuildPath(fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(scriptPath))), "logs\last_action.txt")
    LastResult = ""
    If fso.FileExists(f) Then LastResult = fso.OpenTextFile(f, 1).ReadAll
End Function
