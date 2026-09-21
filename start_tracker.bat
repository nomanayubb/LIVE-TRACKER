@echo off
REM Start the Live Tracker

echo.
echo ============================================================
echo   🎬 LIVE TRACKER - STARTING
echo ============================================================
echo.

REM Check if venv exists
if not exist venv (
    echo ✗ Virtual environment not found!
    echo.
    echo Please run setup_venv.bat first to set up the environment.
    echo.
    pause
    exit /b 1
)

REM Activate venv and run tracker
call venv\Scripts\activate.bat
python live_tracker.py

pause
