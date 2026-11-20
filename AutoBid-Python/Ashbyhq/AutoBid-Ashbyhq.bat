@echo off
rem Double-click to open the AutoBid - Ashbyhq desktop app (no console window).
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw app.py
) else (
  start "" "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" app.py
)
