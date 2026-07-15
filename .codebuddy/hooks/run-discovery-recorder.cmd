@echo off
setlocal
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0record-discovery-action.py"
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0record-discovery-action.py"
  exit /b %errorlevel%
)
echo CodeBuddy discovery recorder: Python interpreter unavailable 1>&2
exit /b 2
