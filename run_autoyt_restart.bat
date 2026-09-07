@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title Restart Auto YT

echo ===================================================
echo KHOI DONG LAI HE THONG AUTO YT
echo ===================================================
echo.
echo Dang tat cac tien trinh dang chay ngam (Backend va Frontend)...

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue | Where-Object State -eq 'Listen' | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue | Where-Object State -eq 'Listen' | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"

echo Da tat thanh cong!
echo.
echo Dang bat lai he thong...
echo.

call run_autoyt.bat

exit /b 0
