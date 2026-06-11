@echo off
cd /d C:\Users\Jackz\CRE-deal-radar-platform

REM Start backend
start cmd /k "cd backend && call ..\venv\Scripts\activate.bat && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

REM Wait for backend
timeout /t 5 >nul

REM Start frontend
start cmd /k "cd frontend && npm run dev"

REM Wait for frontend
timeout /t 8 >nul

REM Open browser
start http://localhost:5173

