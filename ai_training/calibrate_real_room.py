"""
1-Click Pure-CPU Real-Room Calibration Wizard (for you & your teammates!).

Automatically detects YoloUNO #2 (Node 2 CSI Rx) on ANY COM port, walks you through
recording 10 short 2-second windows for each of the 4 classes:
  1. EMPTY      (nobody between the 2 boards)
  2. STATIONARY (sitting/standing quietly)
  3. WALKING    (walking or waving arms continuously between the 2 boards)
  4. FALL       (drop fast in first 0.4s, then FREEZE completely still for 1.5s!)

Then blends your real room recordings with the multi-scale base dataset and retrains
the pure-CPU Random Forest model (`ai_training/models/fall_rf_model.joblib`) in ~3 seconds!
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

AI_DIR = Path(__file__).resolve().parent
ROOT_DIR = AI_DIR.parent
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

from csi_data_generator import generate_csv_dataset  # noqa: E402
from csi_serial_collector import collect_from_serial  # noqa: E402
from csi_signal_features import CLASS_NAMES, NUM_SUBCARRIERS  # noqa: E402
from train_gateway_ai import train_and_export  # noqa: E402

CLASS_HINTS = {
    "EMPTY": "De trong vung giua 2 board (KHONG dung/vay tay giua 2 board).",
    "STATIONARY": "Ngoi yen hoac dung yen binh thuong trong phong.",
    "WALKING": "Di ngang hoac vay tay LIEN TUC suot 2 giay giua 2 board.",
    "FALL": "Guc / nga NHANH trong 0.4s dau, sau do NAM/GIU BAT DONG 1.5s con lai!",
}


def detect_node2_port(baudrate: int = 115200) -> str | None:
    """Scan active COM ports and pick the one streaming JSON with `amps_52` (Node 2 CSI Rx)."""
    candidates = [p.device for p in list_ports.comports()]
    for port in candidates:
        try:
            with serial.Serial(port=port, baudrate=baudrate, timeout=0.6) as ser:
                t_end = time.time() + 1.8
                while time.time() < t_end:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("{") and '"amps_52"' in line:
                        pkt = json.loads(line)
                        if isinstance(pkt.get("amps_52"), list) and len(pkt["amps_52"]) == NUM_SUBCARRIERS:
                            return port
        except Exception:
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Real-Room Pure-CPU CSI Calibrator")
    parser.add_argument("--port", default="AUTO", help="COM port of Node 2, or AUTO to detect")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--windows", type=int, default=10, help="Real windows per class (default: 10)")
    parser.add_argument("--distance", type=float, default=1.5, help="Distance between Node 1 & Node 2 (m)")
    args = parser.parse_args()

    print("=" * 68)
    print("  HCMUT IoT HK261 - Real-Room Pure-CPU AI Calibrator (~2 minutes)")
    print("=" * 68)
    print("LUU Y: Hay DONG cua so `start_demo.bat` truoc khi chay (de giai phong cong COM)!\n")

    port = args.port
    if port.upper() == "AUTO":
        print("[1/3] Dang tu dong tim cong COM cua Node 2 (YoloUNO Rx)...")
        found = detect_node2_port(args.baud)
        if not found:
            print("[ERROR] Khong tim thay Node 2 dang phat CSI (`amps_52`).")
            print("        Hay kiem tra da cam 2 board va DONG `start_demo.bat` chua!")
            sys.exit(1)
        port = found
    print(f"  -> Da tim thay Node 2 tai: {port} @ {args.baud} bps!\n")

    real_dir = AI_DIR / "dataset" / "real"
    real_dir.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] Bat dau thu {args.windows} cua so (2s/cua so) cho moi lop (4 lop):")
    for idx, label in enumerate(CLASS_NAMES, start=1):
        print("\n" + "-" * 68)
        print(f"  BUOC {idx}/4: LOP [{label}]")
        print(f"  Huong dan: {CLASS_HINTS[label]}")
        print("-" * 68)
        input(f"  >> San sang thi bam ENTER de bat dau thu {args.windows} mau '{label}'... ")
        countdown = 2 if label in ("EMPTY", "STATIONARY") else 3
        collect_from_serial(
            port=port,
            baudrate=args.baud,
            label=label,
            num_windows=args.windows,
            out_csv=real_dir / f"{label.lower()}.csv",
            distance_m=args.distance,
            countdown_sec=countdown,
            replace_existing=True,
        )

    print("\n[3/3] Dang train lai model Random Forest (100% CPU) ket hop du lieu phong that...")
    base_dir = AI_DIR / "dataset"
    if not (base_dir / "fall.csv").exists():
        generate_csv_dataset(base_dir, samples_per_class=800)

    models_dir = AI_DIR / "models"
    generic_backup = models_dir / "fall_rf_model_generic.joblib"
    current_model = models_dir / "fall_rf_model.joblib"
    if current_model.exists() and not generic_backup.exists():
        shutil.copy2(current_model, generic_backup)

    metrics = train_and_export(
        dataset_dir=base_dir,
        models_dir=models_dir,
        artifacts_dir=AI_DIR / "artifacts",
        extra_real_dir=real_dir,
        real_weight_copies=10,
    )
    print("\n" + "=" * 68)
    print(f"  HOAN TAT CALIBRATE PHONG THAT (100% CPU)!")
    print(f"  Accuracy : {metrics['rf_accuracy']*100:.2f}% | Fall F1: {metrics['rf_fall_f1']*100:.2f}%")
    print(f"  CPU Infer: {metrics['rf_single_infer_ms']:.2f} ms/window")
    print("  Bay gio hay double-click `start_demo.bat` de demo ngay!")
    print("=" * 68)


if __name__ == "__main__":
    main()
