@echo off
cd /d "%~dp0"
where pythonw >NUL 2>NUL
if %errorlevel%==0 (
    start "" pythonw -m poe_act_helper %*
) else (
    python -m poe_act_helper %*
    pause
)
