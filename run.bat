@echo off
cd /d "%~dp0"
echo === Prompt Studio ===
echo.
echo Starting on http://127.0.0.1:8765
echo (set APP_ENCRYPTION_KEY in .env first, or credentials will not survive a restart)
echo.
start "" http://127.0.0.1:8765/
python -m app.web.server
pause
