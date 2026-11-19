@echo off
rem Opens the Chrome window Dice Auto Bid attaches to (Settings -> Browser -> My Chrome window).
rem It is a normal Chrome with its own profile (dice_bot\data\chrome-profile): sign in to Dice, DeepSeek and
rem anything else once, keep it open and use it yourself. The debug port is only reachable from this PC.
cd /d "%~dp0"
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (echo Google Chrome is not installed. & pause & exit /b 1)
start "" "%CHROME%" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="%~dp0data\chrome-profile" --no-first-run --no-default-browser-check --start-maximized https://www.dice.com/recommended-jobs
