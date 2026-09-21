@echo off
REM Setup Virtual Environment and Install Dependencies
REM Run this file once to set up everything

echo.
echo ============================================================
echo   LIVE TRACKER - VIRTUAL ENVIRONMENT SETUP
echo ============================================================
echo.

REM Check if venv already exists
if exist venv (
    echo ✓ Virtual environment already exists
    echo.
    echo To activate it, run: venv\Scripts\activate
    echo Then run: python live_tracker.py
    pause
    exit /b
)

echo Creating virtual environment...
python -m venv venv

if errorlevel 1 (
    echo ✗ Error creating virtual environment
    pause
    exit /b 1
)

echo ✓ Virtual environment created

echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat

if errorlevel 1 (
    echo ✗ Error activating virtual environment
    pause
    exit /b 1
)

echo ✓ Virtual environment activated

echo.
echo Installing dependencies...
pip install -r requirements.txt

if errorlevel 1 (
    echo ✗ Error installing dependencies
    pause
    exit /b 1
)

echo ✓ Dependencies installed

echo.
echo ============================================================
echo   ✓ SETUP COMPLETE!
echo ============================================================
echo.
echo Next time, just run: start_tracker.bat
echo.
pause
