@echo off
title HCMUT IoT HK261 - Real-Room AI Calibrator (100%% CPU)
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" ai_training\calibrate_real_room.py --port AUTO --windows 10
pause
