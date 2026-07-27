@echo off
cd /d "%~dp0"

if not defined GENMAX_API_KEY if exist "data\genmax_api_key.txt" (
    set /p GENMAX_API_KEY=<"data\genmax_api_key.txt"
)

if not defined GENMAX_API_KEY (
    echo WARNING: Genmax API key is missing.
    echo Set GENMAX_API_KEY or save it to data\genmax_api_key.txt before generating audio.
)

echo 1. Starting Backend (Uvicorn - Port 8080)...
set PYTHONPATH=src
start "Auto_YT Backend" cmd /k "call .venv\Scripts\activate.bat && uvicorn auto_yt.main:app --host 127.0.0.1 --port 8080"

echo 2. Starting Frontend (Vite - Port 5173)...
cd frontend
start "Auto_YT Frontend" cmd /k "npm run dev"

echo All done! Opening browser...
timeout /t 3 /nobreak >nul
start http://localhost:5173
