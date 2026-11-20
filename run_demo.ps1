<#
.SYNOPSIS
    Launch the full Wi-Fi CSI & BLE Radar System (Physics HIL Simulator + RTX 5070 GPU)
    without requiring physical YoloUNO hardware connected!
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
    Write-Error "Virtual environment .venv not found! Please create a Python 3.11 environment first."
}

# Automatically generate CSI dataset and train both models on RTX 5070 GPU if artifacts are missing
if (-not (Test-Path "ai_training\models\csi_spectrogram_2dcnn.onnx") -or -not (Test-Path "firmware\node2_rx_edge_ai\include\model_data.h")) {
    Write-Host "[1/2] Generating CSI dataset and training AI models on RTX 5070 GPU..." -ForegroundColor Yellow
    & $PythonExe ai_training\csi_physics_generator.py --samples-per-class 350
    & $PythonExe ai_training\train_tinyml_1dcnn.py --epochs 65
    & $PythonExe ai_training\train_gpu_2dcnn.py --epochs 45
}

Write-Host "[2/2] Launching Streamlit Dashboard (http://localhost:8501) & Node 2 Web UI (http://127.0.0.1:8082)..." -ForegroundColor Green
& $StreamlitExe run gateway\streamlit_ui.py
