@echo off
title RailSync Server
echo ===================================================
echo Starting RailSync Server on http://127.0.0.1:8000
echo Press Ctrl+C in this window to stop the server.
echo ===================================================
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
	echo RailSync requires Python 3.11 or newer.
	pause
	exit /b 1
)
python -m uvicorn backend.main:app --reload
pause
