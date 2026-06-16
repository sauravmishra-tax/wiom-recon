@echo off
title WIOM Zoho Books vs GST Recon
color 0D
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║     WIOM - Zoho Books vs GST Reconciliation     ║
echo  ║          Powered by 9 AI Sub-Agents             ║
echo  ╚══════════════════════════════════════════════════╝
echo.
echo  Installing dependencies...
pip install flask flask-sqlalchemy flask-login openpyxl pandas apscheduler >nul 2>&1
echo  Starting server...
echo.
echo  Open your browser: http://localhost:5000
echo.
start "" "http://localhost:5000"
cd /d "%~dp0"
python app.py
pause
