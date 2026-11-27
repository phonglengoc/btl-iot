@echo off
title HCMUT IoT HK261 - Train AI Model (100%% CPU)
cd /d "%~dp0"
echo ================================================================
echo   HCMUT IoT HK261 - Pure-CPU Random Forest Training (No GPU!)
echo ================================================================
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] Generating multi-scale 20 Hz CSI dataset (3,200 windows)...
"%PY%" ai_training\csi_data_generator.py --samples-per-class 800
if errorlevel 1 goto :err

echo [2/2] Training tuned Random Forest on CPU (18 scale-invariant features)...
"%PY%" ai_training\train_gateway_ai.py
if errorlevel 1 goto :err

echo.
echo [OK] Done! Model saved to ai_training\models\fall_rf_model.joblib
echo      Ban va teammate co the double-click start_demo.bat de demo ngay!
pause
exit /b 0

:err
echo [ERROR] Training failed. Check Python environment above.
pause
exit /b 1
