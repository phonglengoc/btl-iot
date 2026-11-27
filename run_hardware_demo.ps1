<#
.SYNOPSIS
    1-Click Smart Demo Launcher for HCMUT IoT HK261 (Hardware Auto-Detect + Simulator Fallback)
    - Automatically detects Node 2 (YoloUNO #2, MAC 80:B5:4E:DE:EA:B4) on ANY COM port!
    - Updates gateway/config.yaml safely without erasing CoreIOT/Telegram tokens.
    - Pass -Sim to force Hardware-Free Simulator Mode.
    - Pass -CheckOnly to verify COM detection without launching Streamlit.
#>
param(
    [switch]$Sim,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host "  HCMUT IoT HK261 - Smart Fall Detection Auto Demo Launcher (v1.2) " -ForegroundColor Cyan
Write-Host "===================================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$StreamlitExe = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Khong tim thay .venv! Hay chay: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

# 1. Auto-detect Node 2 COM port by ESP32-S3 MAC (80:B5:4E:DE:EA:B4) and update gateway/config.yaml
$modeFlag = if ($Sim) { "simulator" } else { "auto" }
$pyScript = @'
import sys, pathlib, yaml
import serial.tools.list_ports as lp

NODE2_MAC = "80:B5:4E:DE:EA:B4"
NODE1_MAC = "80:B5:4E:DE:EE:BC"

mode_req = sys.argv[1]
cfg_path = pathlib.Path("gateway/config.yaml")
ex_path = pathlib.Path("gateway/config.example.yaml")
cfg = yaml.safe_load((cfg_path if cfg_path.exists() else ex_path).read_text(encoding="utf-8"))
gw = cfg.setdefault("gateway", {})

ports = sorted(lp.comports(), key=lambda p: p.device)
node2_port = None
node1_port = None
other_esp = []

for p in ports:
    hwid = (p.hwid or "").upper()
    if NODE2_MAC in hwid:
        node2_port = p.device
    elif NODE1_MAC in hwid:
        node1_port = p.device
    elif "303A:1001" in hwid:
        other_esp.append(p.device)

if not node2_port and other_esp:
    node2_port = other_esp[0]

print(f"[DETECT] Node 1 (Tx + DHT20): {node1_port or 'External Power / Not on PC USB'}")
print(f"[DETECT] Node 2 (Rx + LCD/TL): {node2_port or 'NOT FOUND'}")

if mode_req == "simulator" or not node2_port:
    gw["source_mode"] = "simulator"
    if mode_req != "simulator":
        print("[MODE]   Khong thay Node 2 tren USB -> Tu dong chuyen sang SIMULATOR MODE (khong can mach)!")
    else:
        print("[MODE]   SIMULATOR MODE (theo yeu cau -Sim)")
else:
    gw["source_mode"] = "serial"
    gw["serial_port"] = node2_port
    gw["serial_baudrate"] = 115200
    print(f"[MODE]   PHYSICAL HARDWARE MODE -> doc song CSI that tu {node2_port} @ 115200 bps!")

cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
'@

$pyScript | & $PythonExe - $modeFlag
if ($LASTEXITCODE -ne 0) { exit 1 }

# 2. Ensure Random Forest model exists
if (-not (Test-Path "ai_training\models\fall_rf_model.joblib")) {
    Write-Host "[AI] Dang tao dataset va train Random Forest (~3 giay)..." -ForegroundColor Yellow
    & $PythonExe ai_training\csi_data_generator.py --samples-per-class 300
    & $PythonExe ai_training\train_gateway_ai.py
}

if ($CheckOnly) {
    Write-Host "[CHECK] Hoan tat kiem tra phan cung!" -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "[RUN] Dang mo Streamlit Dashboard tai http://localhost:8501 ..." -ForegroundColor Green
Write-Host "      (Muon dung demo, bam Ctrl+C hoac dong cua so nay)" -ForegroundColor DarkGray
Start-Job -ScriptBlock { Start-Sleep -Seconds 2; Start-Process "http://localhost:8501" } | Out-Null
& $StreamlitExe run gateway\streamlit_ui.py --server.port 8501 --server.headless true --browser.gatherUsageStats false
