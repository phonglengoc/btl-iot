"""
Simple Wi-Fi CSI Disturbance Dataset Generator (`T05`).

Generates realistic 2.0-second windows (40 frames @ 20 Hz, 52 active subcarriers)
for 4 room activity classes and saves them directly as inspectable CSV files:
  - `EMPTY`      : Quiet room (flat wave + mild background noise)
  - `STATIONARY` : Person sitting/sleeping (gentle breathing + occasional fast sit hard-negative)
  - `WALKING`    : Continuous movement across the entire 2-second window
  - `FALL`       : Sudden sharp wave surge (0.35-0.65s) followed immediately by stillness on the floor
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import numpy as np

from csi_signal_features import (
    CLASS_NAMES,
    FS_CSI_HZ,
    NUM_SUBCARRIERS,
    WINDOW_SAMPLES,
)


def synthesize_csi_window(
    label: str,
    rng: np.random.Generator,
    distance_m: float | None = None,
    nlos: bool | None = None,
    hard_negative: str | None = None,
    temp_c: float | None = None,
    humi_rh: float | None = None,
) -> dict:
    """
    Synthesize one 2.0-second CSI amplitude window (40 x 52 floats) with intuitive room dynamics.
    """
    if label not in CLASS_NAMES:
        raise ValueError(f"Unknown label: {label}")

    t = np.arange(WINDOW_SAMPLES, dtype=np.float64) / FS_CSI_HZ  # 0.00s .. 1.95s
    dist = float(distance_m if distance_m is not None else rng.uniform(1.0, 3.2))
    is_nlos = bool(nlos if nlos is not None else (rng.random() < 0.20))
    room_temp = float(temp_c if temp_c is not None else rng.uniform(24.0, 33.0))
    room_humi = float(humi_rh if humi_rh is not None else rng.uniform(52.0, 86.0))

    # Attenuation factor for distance and wall obstacle (NLOS)
    dist_scale = (1.8 / max(0.9, dist)) ** 0.65 * (0.78 if is_nlos else 1.0)

    # Static baseline across 52 subcarriers (smooth frequency shape of the room)
    sc_axis = np.linspace(0.0, 1.0, NUM_SUBCARRIERS)
    baseline = (
        rng.uniform(24.0, 34.0)
        + 4.0 * np.sin(2.0 * np.pi * rng.uniform(0.8, 2.2) * sc_axis + rng.uniform(0, 6.28))
    )
    # Random per-subcarrier sensitivity weights
    sc_weights = 0.65 + 0.70 * np.abs(
        np.sin(np.pi * rng.uniform(1.0, 3.0) * sc_axis + rng.uniform(0, 6.28))
    )

    amps = np.tile(baseline, (WINDOW_SAMPLES, 1))
    subtype = hard_negative or "standard"

    if label == "EMPTY":
        # Only tiny thermal noise (plus very subtle AC/fan vibration in 20% of cases)
        if hard_negative == "fan_airflow" or (hard_negative is None and rng.random() < 0.20):
            subtype = "fan_airflow"
            fan_wave = 0.45 * np.sin(2.0 * np.pi * rng.uniform(0.6, 1.2) * t)
            amps += fan_wave[:, None] * sc_weights[None, :]
        noise_std = rng.uniform(0.30, 0.65)

    elif label == "STATIONARY":
        # Gentle chest breathing (0.20 - 0.48 Hz)
        f_breath = rng.uniform(0.20, 0.48)
        breath = rng.uniform(0.9, 2.0) * dist_scale * np.sin(2.0 * np.pi * f_breath * t + rng.uniform(0, 6.28))
        amps += breath[:, None] * sc_weights[None, :]

        if hard_negative == "sit_fast" or (hard_negative is None and rng.random() < 0.22):
            # Hard negative: sitting down quickly -> moderate bump + continued seated fidgeting afterwards
            subtype = "sit_fast"
            t0 = rng.uniform(0.45, 1.05)
            bump = np.exp(-0.5 * ((t - t0) / 0.16) ** 2) * rng.uniform(3.5, 6.2) * dist_scale
            fidget = np.where(
                t > t0 + 0.20,
                rng.uniform(1.1, 2.2) * dist_scale * np.sin(2.0 * np.pi * rng.uniform(1.2, 2.5) * t),
                0.0,
            )
            amps += (bump + fidget)[:, None] * sc_weights[None, :]
        noise_std = rng.uniform(0.45, 0.85)

    elif label == "WALKING":
        # Continuous rhythmic wave fluctuation across the ENTIRE 2-second window
        f_step = rng.uniform(1.2, 3.2)
        ph0 = rng.uniform(0, 6.28)
        walk_amp = rng.uniform(4.2, 8.2) * dist_scale
        walk_wave = (
            walk_amp * np.sin(2.0 * np.pi * f_step * t + ph0)
            + 0.45 * walk_amp * np.cos(2.0 * np.pi * (2.0 * f_step) * t + ph0 + 0.8)
        )
        # Slowly varying envelope so walking stays active from start to end
        envelope = 0.75 + 0.25 * np.sin(2.0 * np.pi * 0.55 * t + rng.uniform(0, 6.28))
        amps += (walk_wave * envelope)[:, None] * sc_weights[None, :]
        noise_std = rng.uniform(0.55, 1.05)

    elif label == "FALL":
        # Key physical pattern of a fall:
        #   1) Brief pre-fall sway
        #   2) Sudden high-amplitude impact surge (duration 0.35 - 0.65 s)
        #   3) Abrupt stillness on the floor until the end of the 2-second window
        t_start = rng.uniform(0.25, 0.75)
        fall_dur = rng.uniform(0.35, 0.60)
        t_impact = t_start + fall_dur

        fall_sig = np.zeros_like(t)
        pre_mask = t < t_start
        fall_sig[pre_mask] = rng.uniform(0.8, 1.8) * dist_scale * np.sin(2.0 * np.pi * 1.6 * t[pre_mask])

        burst_mask = (t >= t_start) & (t <= t_impact)
        tau = (t[burst_mask] - t_start) / fall_dur
        burst_amp = rng.uniform(11.5, 19.0) * dist_scale
        # Sharp acceleration oscillation peaking right at floor impact
        fall_sig[burst_mask] = (
            burst_amp * (np.sin(np.pi * tau) ** 0.75) * np.sin(2.0 * np.pi * rng.uniform(3.5, 6.5) * (t[burst_mask] - t_start) + 0.6)
        )

        # Post-impact stillness on the floor (very quiet tail!)
        post_mask = t > t_impact
        shock = np.exp(-(t[post_mask] - t_impact) / 0.06)
        fall_sig[post_mask] = 0.25 * burst_amp * shock + 0.25 * np.sin(2.0 * np.pi * 0.3 * t[post_mask])

        # Slight static baseline shift after body lands on the floor
        floor_shift = np.where(t > t_impact, rng.uniform(1.5, 3.5) * dist_scale, 0.0)
        amps += (fall_sig + floor_shift)[:, None] * sc_weights[None, :]
        noise_std = rng.uniform(0.35, 0.70)

    # Add realistic Gaussian noise
    amps += rng.normal(0.0, noise_std, size=amps.shape)

    # Inject 1 random single-frame RF spike in 25% of windows (smoothed by Moving Average!)
    if rng.random() < 0.25:
        spike_idx = int(rng.integers(1, WINDOW_SAMPLES - 1))
        amps[spike_idx] += rng.uniform(3.0, 6.0)

    amps = np.clip(amps, 0.5, 120.0).astype(np.float32)
    rssi_dbm = int(
        np.clip(-44.0 - 18.0 * np.log10(max(0.8, dist)) - (7.0 if is_nlos else 0.0) + rng.normal(0, 1.5), -88, -32)
    )

    return {
        "label": label,
        "subtype": subtype,
        "distance_m": round(dist, 2),
        "nlos": is_nlos,
        "temp_c": round(room_temp, 2),
        "humi_rh": round(room_humi, 2),
        "rssi_dbm": rssi_dbm,
        "amps_52": amps,  # shape (40, 52) float32
    }


def generate_csv_dataset(output_dir: Path, samples_per_class: int = 300, seed: int = 2026) -> dict:
    """
    Generate labeled CSV files (`empty.csv`, `stationary.csv`, `walking.csv`, `fall.csv`)
    for `T05` and `T09` scikit-learn training.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    header = (
        ["sample_id", "frame_idx", "label", "subtype", "distance_m", "nlos", "temp_c", "humi_rh", "rssi_dbm"]
        + [f"sc_{k:02d}" for k in range(NUM_SUBCARRIERS)]
    )
    summary_counts: dict[str, int] = {}

    for label in CLASS_NAMES:
        csv_path = output_dir / f"{label.lower()}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f_csv:
            writer = csv.writer(f_csv)
            writer.writerow(header)

            for s_idx in range(samples_per_class):
                win = synthesize_csi_window(label=label, rng=rng)
                amps = win["amps_52"]
                for frame_i in range(WINDOW_SAMPLES):
                    row = [
                        s_idx,
                        frame_i,
                        label,
                        win["subtype"],
                        win["distance_m"],
                        int(win["nlos"]),
                        win["temp_c"],
                        win["humi_rh"],
                        win["rssi_dbm"],
                    ] + [round(float(v), 2) for v in amps[frame_i]]
                    writer.writerow(row)

        summary_counts[label] = samples_per_class

    meta = {
        "total_windows": int(sum(summary_counts.values())),
        "samples_per_class": summary_counts,
        "window_frames": WINDOW_SAMPLES,
        "sampling_rate_hz": FS_CSI_HZ,
        "active_subcarriers": NUM_SUBCARRIERS,
        "format": "csv",
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate labeled 20 Hz Wi-Fi CSI CSV dataset (T05)")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "dataset")
    parser.add_argument("--samples-per-class", type=int, default=300)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    info = generate_csv_dataset(args.out_dir, samples_per_class=args.samples_per_class, seed=args.seed)
    print(f"[T05 Dataset Generator] Generated {info['total_windows']} CSV windows (20 Hz, 40 frames) -> {args.out_dir}")
