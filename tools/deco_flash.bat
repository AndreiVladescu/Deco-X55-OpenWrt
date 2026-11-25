@echo off
rem Double-click to run deco_flash.py (installs pyserial on first use).
cd /d "%~dp0"
where py >NUL 2>NUL || (echo Python 3 is needed: https://www.python.org/downloads/ & pause & exit /b 1)
py -3 -c "import serial" 2>NUL || py -3 -m pip install --user pyserial
py -3 deco_flash.py %*
pause
