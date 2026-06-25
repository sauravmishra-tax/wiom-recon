@echo off
cd /d "%~dp0"
echo.
echo  WIOM Zoho Agent
echo  ===============
echo.
set /p PERIOD="Enter period (YYYY-MM, e.g. 2026-04): "
python zoho_agent.py --period %PERIOD%
echo.
pause
