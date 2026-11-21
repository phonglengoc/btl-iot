"""
Serial / Simulator CSI CSV Data Collection Tool (`T05`).

Two modes:
  1. Hardware Mode (`--mode serial --port COM4 --baud 115200`):
     Reads live 20 Hz CSI JSONL lines directly from YoloUNO #2 over USB Serial
     and saves labeled 2.0-second windows (40 frames) into a CSV file.
  2. Simulator Mode (`--mode simulator`):
     Generates simulated windows into CSV so the team can verify `T05` without physical boards.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
import numpy as np
import serial

from csi_data_generator import synthesize_csi_window
from csi_signal_features import CLASS_NAMES, NUM_SUBCARRIERS, WINDOW_SAMPLES

CSV_HEADER = (
    ["sample_id", "frame_idx", "label", "subtype", "distance_m", "nlos", "temp_c", "humi_rh", "rssi_dbm"]
    + [f"sc_{k:02d}" for k in range(NUM_SUBCARRIERS)]
)


def collect_from_serial(
    port: str,
    baudrate: int,
    label: str,
    num_windows: int,
    out_csv: Path,
    distance_m: float = 2.0,
) -> int:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_csv.exists()
    recorded = 0

    print(f"[CSI Collector T05] Opening {port} @ {baudrate} bps for class '{label}' ({num_windows} windows)...")
    with serial.Serial(port=port, baudrate=baudrate, timeout=2.0) as ser, out_csv.open(
        "a", newline="", encoding="utf-8"
    ) as f_csv:
        writer = csv.writer(f_csv)
        if write_header:
            writer.writerow(CSV_HEADER)

        buf_frames: list[tuple[dict, list[float]]] = []
        while recorded < num_windows:
            raw_line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not raw_line.startswith("{"):
                continue
            try:
                pkt = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            amps = pkt.get("amps_52")
            if not isinstance(amps, list) or len(amps) != NUM_SUBCARRIERS:
                continue

            buf_frames.append((pkt, [float(v) for v in amps]))
            if len(buf_frames) >= WINDOW_SAMPLES:
                sample_id = int(time.time() * 1000) % 1_000_000
                for f_idx, (meta, sc_vals) in enumerate(buf_frames[:WINDOW_SAMPLES]):
                    writer.writerow(
                        [
                            sample_id,
                            f_idx,
                            label,
                            "live_serial",
                            distance_m,
                            0,
                            meta.get("temp_c", 27.5),
                            meta.get("humi_rh", 65.0),
                            meta.get("rssi", -55),
                        ]
                        + [round(v, 2) for v in sc_vals]
                    )
                f_csv.flush()
                buf_frames.clear()
                recorded += 1
                print(f"  -> Saved window {recorded}/{num_windows} to {out_csv.name}")

    return recorded


def collect_from_simulator(
    label: str,
    num_windows: int,
    out_csv: Path,
    distance_m: float = 2.0,
    seed: int = 42,
) -> int:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_csv.exists()
    rng = np.random.default_rng(seed)

    with out_csv.open("a", newline="", encoding="utf-8") as f_csv:
        writer = csv.writer(f_csv)
        if write_header:
            writer.writerow(CSV_HEADER)

        for w_idx in range(num_windows):
            win = synthesize_csi_window(label=label, rng=rng, distance_m=distance_m)
            amps = win["amps_52"]
            for f_idx in range(WINDOW_SAMPLES):
                writer.writerow(
                    [
                        w_idx,
                        f_idx,
                        label,
                        win["subtype"],
                        win["distance_m"],
                        int(win["nlos"]),
                        win["temp_c"],
                        win["humi_rh"],
                        win["rssi_dbm"],
                    ]
                    + [round(float(v), 2) for v in amps[f_idx]]
                )
    print(f"[CSI Collector T05] Recorded {num_windows} simulated '{label}' windows -> {out_csv}")
    return num_windows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CSI Serial / Simulator CSV Collector (T05)")
    parser.add_argument("--mode", choices=["serial", "simulator"], default="simulator")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--label", choices=CLASS_NAMES, default="FALL")
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--distance", type=float, default=2.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    target_csv = args.out or (Path(__file__).parent / "dataset" / f"collected_{args.label.lower()}.csv")
    if args.mode == "serial":
        collect_from_serial(args.port, args.baud, args.label, args.windows, target_csv, args.distance)
    else:
        collect_from_simulator(args.label, args.windows, target_csv, args.distance)
