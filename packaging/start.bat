@echo off
setlocal
rem hsr-currency-wars-agent: start the web UI and the agent.
rem Needs administrator rights (the game runs elevated); asks for them if missing.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
set "CWAGENT_HOME=%~dp0"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
if not exist "data\knowledge.sqlite" (
  echo First run: downloading game data from the official API, about 1 minute...
  "python\python.exe" -m cwagent knowledge sync
)
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 6; Start-Process http://127.0.0.1:8770"
"python\python.exe" -m cwagent serve
pause
