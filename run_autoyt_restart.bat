@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Restart Auto_YT
set "AUTOYT_NO_PAUSE=1"
set "AUTOYT_REQUIRE_GPM_IDLE=1"

echo [Auto_YT] Stopping the current system...
call "%~dp0run_autoyt_stop.bat"
set "AUTOYT_STOP_EXIT_CODE=%ERRORLEVEL%"
if not "%AUTOYT_STOP_EXIT_CODE%"=="0" (
    echo.
    echo Auto_YT restart aborted because the current system could not be stopped safely.
    if not defined AUTOYT_RESTART_NO_PAUSE pause
    exit /b %AUTOYT_STOP_EXIT_CODE%
)

echo.
echo [Auto_YT] Starting the system again...
call "%~dp0run_autoyt.bat" %*
set "AUTOYT_START_EXIT_CODE=%ERRORLEVEL%"
if not "%AUTOYT_START_EXIT_CODE%"=="0" (
    echo.
    echo Auto_YT stopped successfully but failed to start again. Review data\logs.
    if not defined AUTOYT_RESTART_NO_PAUSE pause
    exit /b %AUTOYT_START_EXIT_CODE%
)

echo.
echo Auto_YT restarted successfully.
exit /b 0
