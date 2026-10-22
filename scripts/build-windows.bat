@echo off
REM Build a standalone Windows app. Output in dist\LocalConvert\LocalConvert.exe
cd /d "%~dp0\.."
py -3 -m venv .build-venv || exit /b 1
.build-venv\Scripts\python.exe -m pip install -q --upgrade pip
.build-venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt || exit /b 1
.build-venv\Scripts\python.exe -m pytest -q tests || exit /b 1
.build-venv\Scripts\pyinstaller.exe --noconfirm --clean LocalConvert.spec || exit /b 1
echo Built: dist\LocalConvert\LocalConvert.exe
