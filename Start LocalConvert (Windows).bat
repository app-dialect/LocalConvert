@echo off
REM Double-click to run LocalConvert from source on Windows.
REM The first run downloads the Python libraries it needs (one time); after that it works fully offline.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up ^(one time only^)...
  %PY% -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements-app.txt -q || goto :failed
)
".venv\Scripts\python.exe" run.py %*
goto :eof
:nopython
echo Python 3 is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
goto :eof
:failed
echo Setup failed - see the messages above.
pause
