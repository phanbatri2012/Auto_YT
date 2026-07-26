@echo off
cd /d "%~dp0"

echo 1. Starting Backend (Uvicorn - Port 8080)...
set PYTHONPATH=src
start "Auto_YT Backend" cmd /k "call .venv\Scripts\activate.bat && uvicorn auto_yt.main:app --host 127.0.0.1 --port 8080"

echo 2. Starting Frontend (Vite - Port 5173)...
cd frontend
start "Auto_YT Frontend" cmd /k "npm run dev"

echo All done! Opening browser...
timeout /t 3 /nobreak >nul
start http://localhost:5173
