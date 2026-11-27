<#
.SYNOPSIS
    Launch the Smart Fall Detection & Room Monitoring System
    (Hardware-free simulator mode with CPU scikit-learn AI — no physical YoloUNO boards required!)
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host " HCMUT IoT HK261 - Smart Fall Detection & Room Monitoring Launcher " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$StreamlitExe = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment .venv not found! Run: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

# Automatically generate sample dataset and train Random Forest model (T05, T09) if missing
if (-not (Test-Path "ai_training\models\fall_rf_model.joblib")) {
    Write-Host "[1/2] Generating 20 Hz CSI CSV dataset and training Random Forest (scikit-learn)..." -ForegroundColor Yellow
    & $PythonExe ai_training\csi_data_generator.py --samples-per-class 300
    & $PythonExe ai_training\train_gateway_ai.py
}

Write-Host "[2/2] Starting Streamlit Dashboard (http://localhost:8501)..." -ForegroundColor Green
& $StreamlitExe run gateway\streamlit_ui.py
