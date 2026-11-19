@echo off
rem VCC Plastics MES - bat server CHAY AN (khong de lai cua so). Co the chep file nay ra thu muc goc
rem (canh VCC-Plastics-BE va VCC-Plastics-FE) hoac de nguyen trong VCC-Plastics-BE\deploy\windows.
title VCC Plastics MES - Bat server
set "PS1=%~dp0server.ps1"
if not exist "%PS1%" set "PS1=%~dp0VCC-Plastics-BE\deploy\windows\server.ps1"
if not exist "%PS1%" set "PS1=%~dp0VCC-Plastics-BE-main\deploy\windows\server.ps1"
if not exist "%PS1%" (
  echo Khong tim thay server.ps1 - hay dat file nay canh thu muc VCC-Plastics-BE.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" start
if errorlevel 1 (
  echo.
  echo Bat server KHONG thanh cong - doc loi o tren.
  pause
  exit /b 1
)
exit /b 0
