@echo off
setlocal
rem hsr-currency-wars-agent: remove everything it installed or created, then delete this folder.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
set "CWAGENT_HOME=%~dp0"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
rem stop the web service of this copy if it is running (it keeps files open)
powershell -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '%DIR%\*' } | Stop-Process -Force"
"python\python.exe" -m cwagent uninstall --all
if %errorlevel% neq 0 (
  pause
  exit /b 1
)
echo.
echo This folder will be deleted after you press a key.
pause
start "" /min /d "%TEMP%" cmd /c "timeout /t 3 >nul & rmdir /s /q "%DIR%""
exit
