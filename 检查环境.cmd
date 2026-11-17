@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
"%PY%" -m lmcore_sync doctor %*
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
