<#
.SYNOPSIS
    Khoi chay toan bo He thong Radar Wi-Fi CSI & BLE (Che do Gia lap HIL + GPU RTX 5070)
    khong can cam mach YoloUNO vat ly!
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host " HCMUT IoT HK261 - Wi-Fi CSI Fall & Intrusion Detection Launcher   " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$StreamlitExe = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Chua tim thay .venv! Vui long tao moi truong Python 3.11 truoc."
}

# Neu chua co mo hinh ONNX hoac header TinyML thi tu dong huan luyen tren GPU RTX 5070
if (-not (Test-Path "ai_training\models\csi_spectrogram_2dcnn.onnx") -or -not (Test-Path "firmware\node2_rx_edge_ai\include\model_data.h")) {
    Write-Host "[1/2] Dang sinh du lieu CSI va huan luyen AI tren GPU RTX 5070..." -ForegroundColor Yellow
    & $PythonExe ai_training\csi_physics_generator.py --samples-per-class 350
    & $PythonExe ai_training\train_tinyml_1dcnn.py --epochs 65
    & $PythonExe ai_training\train_gpu_2dcnn.py --epochs 45
}

Write-Host "[2/2] Dang mo Dashboard Streamlit (http://localhost:8501) & Node 2 Web UI (http://127.0.0.1:8082)..." -ForegroundColor Green
& $StreamlitExe run gateway\streamlit_ui.py
