<#
.SYNOPSIS
    Khoi chay He thong Phat hien Te nga & Giam sat Phong o Thong minh
    (Che do Gia lap khong can cam mach YoloUNO vat ly, chay AI scikit-learn tren CPU)
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
    Write-Error "Chua tim thay .venv! Chay: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

# Tu dong sinh du lieu mau va huan luyen mo hinh Random Forest (T05, T09) neu chua co
if (-not (Test-Path "ai_training\models\fall_rf_model.joblib")) {
    Write-Host "[1/2] Dang sinh du lieu CSI (20 Hz) va huan luyen mo hinh Random Forest (scikit-learn)..." -ForegroundColor Yellow
    & $PythonExe ai_training\csi_data_generator.py --samples-per-class 300
    & $PythonExe ai_training\train_gateway_ai.py
}

Write-Host "[2/2] Dang mo Dashboard Streamlit (http://localhost:8501)..." -ForegroundColor Green
& $StreamlitExe run gateway\streamlit_ui.py
