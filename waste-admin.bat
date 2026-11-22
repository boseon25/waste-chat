@echo off
rem waste_agent admin commands (Windows). usage: waste-admin.bat help
chcp 65001 >nul
set PYTHONUTF8=1
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0src\law_agent\launch.py" admin %*
) else (
  python "%~dp0src\law_agent\launch.py" admin %*
)
