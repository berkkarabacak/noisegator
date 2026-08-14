@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" app.py
  exit /b 0
)
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw app.py
  exit /b 0
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" python app.py
  exit /b 0
)
echo Python was not found.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo then:  pip install -r requirements.txt
echo and run this file again.
pause
