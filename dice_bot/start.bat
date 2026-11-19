@echo off
rem Dice Auto Bid: sets up a private Python environment the first time, then opens the window.
rem   start.bat         normal start
rem   start.bat debug   start with a console window that shows any error
cd /d "%~dp0"

where node >nul 2>nul || (echo Node.js is needed: install it from https://nodejs.org, then open start.bat again. & pause & exit /b 1)
if not exist "..\extension-v2\lib\resume.js" (
  echo The resume engine is missing: copy the WHOLE resume-builder folder ^(with extension-v2^), not only dice_bot.
  pause
  exit /b 1
)

set PY=
for %%P in (py python) do if not defined PY (%%P -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set PY=%%P)
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" in the installer^), then run start.bat again.
  pause
  exit /b 1
)

rem a .venv copied from another PC points at that PC's Python: rebuild it when it does not work here
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import playwright, tkinter" >nul 2>nul || (echo The .venv folder does not work on this machine: rebuilding it... & rmdir /s /q .venv)
)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time...
  %PY% -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
  .venv\Scripts\python doctor.py
  echo.
  echo Fix anything marked FAIL above, then press a key to open Dice Auto Bid.
  pause >nul
)

if /i "%1"=="debug" (
  .venv\Scripts\python app.py
  pause
) else (
  start "" .venv\Scripts\pythonw.exe app.py
)
