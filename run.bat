@echo off
cd /d "%~dp0"
if not exist ".deps_ok" (
    echo Installing Pillow + keyboard ^(first run only^)...
    python -m pip install --user -r requirements.txt && echo ok> .deps_ok
)
where pythonw >NUL 2>NUL
if %errorlevel%==0 (
    start "" pythonw -m poe_act_helper %*
) else (
    python -m poe_act_helper %*
    pause
)
