@echo off
REM ONE double-click: starts Docker + the demo if needed, records a fresh baseline, runs every automatic
REM practice incident (about 2 hours, no human needed), scores everything. Keep the laptop plugged in.
cd /d "%~dp0"
python -m oncall.drill auto
echo.
echo Finished. Tell Claude: done
pause
