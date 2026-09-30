@echo off
rem Double-click to run: asks for a URL, then scrapes it into the output folder.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set /p URL=Enter website URL:
python scrape.py "%URL%"
echo.
start "" "%~dp0output"
pause
