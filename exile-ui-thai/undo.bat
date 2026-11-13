@echo off
chcp 65001 >NUL
cd /d "%~dp0"
python install_thai.py %* --undo
pause
