@echo off
cd /d "D:\laravel project\python\CamScan"

REM Activate the virtual environment and start the server
call venv\Scripts\activate.bat

REM Check if uvicorn is available
where uvicorn >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Starting FastAPI server on port 8001...
    echo Press Ctrl+C to stop the server
    echo.
    uvicorn main:app --reload --port 8001
) else (
    echo Uvicorn not found in PATH
    echo Trying with python -m uvicorn...
    python -m uvicorn main:app --reload --port 8001
)

pause