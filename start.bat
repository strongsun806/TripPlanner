@echo off
rem TripPlanner - start backend (8000) and screen (5500) in two windows, then open the browser
rem Close the two black windows (or press Ctrl+C in them) to stop.
chcp 65001 >nul
cd /d "%~dp0"
start "TripPlanner server 8000" cmd /k "call ..\..\venv\Scripts\activate.bat && uvicorn app.main:app --reload"
start "TripPlanner screen 5500" cmd /k "call ..\..\venv\Scripts\activate.bat && python serve_frontend.py"
timeout /t 5 /nobreak >nul
start "" http://localhost:5500
