@echo off
setlocal EnableExtensions
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_autoyt.ps1" %*
set "AUTOYT_EXIT_CODE=%ERRORLEVEL%"

if not "%AUTOYT_EXIT_CODE%"=="0" (
    echo.
    echo Auto_YT failed to start. Review the error above and data\logs.
    if not defined AUTOYT_NO_PAUSE pause
)

exit /b %AUTOYT_EXIT_CODE%
