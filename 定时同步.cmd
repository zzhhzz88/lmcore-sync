@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
echo 每天在当前 Windows 用户登录后自动同步，默认时间为 09:00。
echo 可在命令行运行 python -m lmcore_sync schedule install --at 18:30 修改时间。
"%PY%" -m lmcore_sync schedule install %*
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
