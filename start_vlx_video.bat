@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_vlx_video.ps1"
set "VLX_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %VLX_EXIT%
