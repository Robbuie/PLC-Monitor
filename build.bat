@echo off
rem Builds dist\PLCMonitor\ and, when Inno Setup 6 is installed, the setup exe
rem and latest.json. Everything real is in packaging\build.py.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt -r packaging\requirements-build.txt || exit /b 1
python packaging\build.py %* || exit /b 1
endlocal
