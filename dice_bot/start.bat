@echo off
rem Dice Auto Bid: sets up a private Python environment the first time, then opens the window.
cd /d "%~dp0"

where node >nul 2>nul || (echo Node.js is needed: https://nodejs.org & pause & exit /b 1)

set PY=
for %%P in (py python) do if not defined PY (%%P -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set PY=%%P)
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" in the installer^), then run start.bat again.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time...
  %PY% -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
)

start "" .venv\Scripts\pythonw.exe app.py
