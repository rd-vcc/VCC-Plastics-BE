@echo off
rem VCC Plastics MES - tat backend (5005) va giao dien (5173) neu dang chay.
title VCC Plastics MES - Tat server
set "PS1=%~dp0server.ps1"
if not exist "%PS1%" set "PS1=%~dp0VCC-Plastics-BE\deploy\windows\server.ps1"
if not exist "%PS1%" set "PS1=%~dp0VCC-Plastics-BE-main\deploy\windows\server.ps1"
if not exist "%PS1%" (
  echo Khong tim thay server.ps1 - hay dat file nay canh thu muc VCC-Plastics-BE.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" stop
timeout /t 5 >nul
