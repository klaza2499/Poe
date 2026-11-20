@echo off
rem PoE Build Planner - opens http://127.0.0.1:8765 in your browser
cd /d "%~dp0"
python -m poe_build web %*
pause
