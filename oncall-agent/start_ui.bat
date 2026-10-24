@echo off
REM Opens the On-call Control Room in your browser (http://127.0.0.1:8800). Keep this window open.
cd /d "%~dp0"
python -m oncall.ui
pause
