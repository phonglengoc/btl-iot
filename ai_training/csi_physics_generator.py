"""
Physics-Based IEEE 802.11n HT20 (2.437 GHz, Ch. 6) Multipath Wi-Fi CSI Dataset Generator (`T05`).

Models the physical OFDM channel response in Eq. (2.1) of `spec_btl_iot_fall_detection.tex`:
    H_k(t) = H_static,k(T, RH) + sum_{p=1}^{P} alpha_p(t) * exp(-j * 2*pi * f_k * d_p(t) / c) + n_k(t)

Generates realistic 2.0-second windows (200 frames @ 100 Hz, 64 subcarriers -> 128-byte int8 I/Q)
across 4 target classes (`EMPTY`, `STATIONARY`, `WALKING`, `FALL`), including:
  - Varying Tx-Rx / subject distances d in {1.0m, 2.0m, 3.0m, 3.5m} and NLOS wall attenuation
  - Hard-negative activities inside `STATIONARY` / `WALKING` (rapid sitting, bending to pick objects,
    door opening, fan vibration) so the AI models learn true fall kinematics (high-frequency acceleration
    5-12 Hz burst followed immediately by post-impact floor stillness)
  - Environmental dielectric drift from DHT20 temperature (23-35 C) & humidity (50-92 %RH)
  - Impulsive RF interference spikes (to test Hampel filter) and packet-drop interpolation.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import numpy as np

from csi_dsp_common import (
    ACTIVE_SC_INDICES,
    CLASS_NAMES,
    FS_CSI_HZ,
    RAW_WINDOW_SAMPLES,
    iq_bytes_to_active_amplitudes,
)

C_LIGHT = 299_792_458.0
F_CENTER_CH6 = 2_437_000_000.0  # Channel 6 = 2437 MHz
DELTA_F_OFDM = 312_500.0        # 20 MHz / 64 subcarriers = 312.5 kHz


def _subcarrier_frequencies_64() -> np.ndarray:
    k_signed = np.arange(-32, 32, dtype=np.float64)
    return F_CENTER_CH6 + k_signed * DELTA_F_OFDM


SUBCARRIER_FREQS_64 = _subcarrier_frequencies_64()


def synthesize_csi_iq_window(
    label: str,
    rng: np.random.Generator,
    distance_m: float | None = None,
    nlos: bool | None = None,
    hard_negative: str | None = None,
    temp_c: float | None = None,
    humi_rh: float | None = None,
) -> dict:
    """
    Synthesize one realistic 2.0-second CSI window (200 x 128 int8 I/Q bytes) + metadata.
    """
    if label not in CLASS_NAMES:
        raise ValueError(f"Unknown label: {label}")

    t = np.arange(RAW_WINDOW_SAMPLES, dtype=np.float64) / FS_CSI_HZ  # (200,) in [0.00 .. 1.99s]
    dist = float(distance_m if distance_m is not None else rng.uniform(1.0, 3.4))
    is_nlos = bool(nlos if nlos is not None else (rng.random() < 0.22))
    room_temp = float(temp_c if temp_c is not None else rng.uniform(24.0, 33.5))
    room_humi = float(humi_rh if humi_rh is not None else rng.uniform(52.0, 88.0))

    # Distance & NLOS path-loss attenuation factor
    nlos_atten = 0.65 if is_nlos else 1.0
    dist_scale = (1.8 / max(0.9, dist)) ** 0.85 * nlos_atten

    # 1. Static multipath room profile (LOS + 3 wall/ceiling/floor reflections)
    h_complex = np.zeros((RAW_WINDOW_SAMPLES, 64), dtype=np.complex128)
    n_static_paths = 4
    base_amp = rng.uniform(26.0, 38.0) * (0.82 if is_nlos else 1.0)
    for p in range(n_static_paths):
        path_len = dist + p * rng.uniform(0.45, 1.6)
        amp_p = base_amp / (1.0 + 0.55 * p)
        phase_0 = rng.uniform(0, 2 * np.pi)
        phase_k = -2.0 * np.pi * SUBCARRIER_FREQS_64 * (path_len / C_LIGHT) + phase_0
        h_complex += amp_p * np.exp(1j * phase_k)[None, :]

    # Dielectric drift from temperature & steam humidity
    env_gain = 1.0 + 0.0018 * (room_temp - 27.0) + 0.0012 * (room_humi - 65.0)
    h_complex *= env_gain

    # 2. Dynamic human body scatterers (torso + limbs) depending on activity
    subtype = hard_negative or "standard"
    if label == "EMPTY":
        # Occasional subtle background vibration (e.g., ceiling fan / AC airflow) in 20% of empty windows
        if hard_negative == "fan_airflow" or (hard_negative is None and rng.random() < 0.20):
            subtype = "fan_airflow"
            f_fan = rng.uniform(0.6, 1.3)
            d_fan = 4.2 + 0.0025 * np.sin(2 * np.pi * f_fan * t)
            phase_dyn = -2.0 * np.pi * SUBCARRIER_FREQS_64[None, :] * (d_fan[:, None] / C_LIGHT)
            h_complex += 0.9 * np.exp(1j * phase_dyn)

    elif label == "STATIONARY":
        # Chest breathing micro-motion (0.20 - 0.48 Hz, displacement 4-8 mm)
        f_breath = rng.uniform(0.20, 0.48)
        ph_b = rng.uniform(0, 2 * np.pi)
        chest_disp = rng.uniform(0.005, 0.011) * np.sin(2 * np.pi * f_breath * t + ph_b)

        if hard_negative == "sit_fast" or (hard_negative is None and rng.random() < 0.22):
            # HARD NEGATIVE: Sitting down quickly onto a chair!
            # Moderate transient (2.5 - 5.2 Hz for ~0.35s) FOLLOWED BY continued seated micro-motion/fidgeting
            subtype = "sit_fast"
            t0 = rng.uniform(0.45, 1.15)
            sit_env = np.exp(-0.5 * ((t - t0) / rng.uniform(0.14, 0.20)) ** 2)
            f_sit = rng.uniform(2.8, 5.4)
            d_torso = dist + 0.16 * sit_env * np.sin(2 * np.pi * f_sit * (t - t0)) + chest_disp
            # Seated person adjusts posture right after sitting (key cue vs unconscious fall!)
            post_fidget = np.where(t > t0 + 0.25, 0.035 * np.sin(2 * np.pi * rng.uniform(1.2, 2.4) * t), 0.0)
            d_torso += post_fidget
            scat_amp = rng.uniform(7.5, 12.5) * dist_scale * (1.0 + 0.55 * sit_env)[:, None]
        else:
            # Normal sitting/reading/sleeping with occasional slight head/hand turn
            fidget = 0.012 * np.sin(2 * np.pi * rng.uniform(0.7, 1.5) * t + rng.uniform(0, 6.28)) * (
                rng.random() < 0.35
            )
            d_torso = dist + chest_disp + fidget
            scat_amp = rng.uniform(5.0, 9.0) * dist_scale

        phase_dyn = -2.0 * np.pi * SUBCARRIER_FREQS_64[None, :] * (2.0 * d_torso[:, None] / C_LIGHT)
        # Frequency-selective indoor multipath across subcarriers
        sc_weights = 1.0 + 0.35 * np.sin(np.linspace(0, rng.uniform(2.5, 5.0) * np.pi, 64))[None, :]
        h_complex += scat_amp * sc_weights * np.exp(1j * phase_dyn)

    elif label == "WALKING":
        # Continuous walking velocity v in [0.55, 1.45] m/s -> Doppler f_D = 2*v/lambda ~ 1.0 - 4.5 Hz
        # Plus swinging arms/legs producing harmonic micro-Doppler!
        f_step = rng.uniform(1.3, 3.6)
        v_rad = rng.uniform(0.08, 0.22) * rng.choice([-1.0, 1.0])
        ph0 = rng.uniform(0, 2 * np.pi)

        if hard_negative == "bend_pickup" or (hard_negative is None and rng.random() < 0.18):
            subtype = "bend_pickup"
            # Walking a step, bending to pick an object, then continuing motion
            t_bend = rng.uniform(0.6, 1.3)
            bend_pulse = np.exp(-0.5 * ((t - t_bend) / 0.18) ** 2)
            d_torso = dist + v_rad * t + 0.09 * np.sin(2 * np.pi * f_step * t + ph0) + 0.14 * bend_pulse
        else:
            d_torso = dist + v_rad * t + 0.085 * np.sin(2 * np.pi * f_step * t + ph0)

        d_limb1 = d_torso + 0.14 * np.sin(2 * np.pi * (2.0 * f_step) * t + ph0 + 0.7)
        d_limb2 = d_torso - 0.12 * np.sin(2 * np.pi * (2.0 * f_step) * t + ph0 + 2.1)

        amp_torso = rng.uniform(8.5, 14.5) * dist_scale
        amp_limb = rng.uniform(3.8, 6.8) * dist_scale
        sc_w1 = 1.0 + 0.45 * np.cos(np.linspace(0, rng.uniform(2.0, 4.5) * np.pi, 64))[None, :]
        sc_w2 = 1.0 + 0.45 * np.sin(np.linspace(0, rng.uniform(3.0, 6.0) * np.pi, 64))[None, :]

        for d_path, amp_p, w_sc in [
            (d_torso, amp_torso, sc_w1),
            (d_limb1, amp_limb, sc_w2),
            (d_limb2, amp_limb * 0.85, sc_w1),
        ]:
            ph = -2.0 * np.pi * SUBCARRIER_FREQS_64[None, :] * (2.0 * d_path[:, None] / C_LIGHT)
            h_complex += amp_p * w_sc * np.exp(1j * ph)

    elif label == "FALL":
        # Physical kinematics of an indoor fall (Section 3.5.1):
        # Phase A (0 .. t_start): brief pre-fall imbalance / slow sway (1-2.5 Hz)
        # Phase B (t_start .. t_impact, duration 0.32 - 0.68s): free-fall acceleration downward
        #         producing high-frequency 5.5 - 12.5 Hz broadband Doppler burst across ALL subcarriers
        # Phase C (t > t_impact): abrupt stillness on floor (only faint breathing or zero motion!)
        t_start = rng.uniform(0.28, 0.78)
        fall_dur = rng.uniform(0.34, 0.65)
        t_impact = t_start + fall_dur

        d_torso = np.full_like(t, dist)
        d_limbs = np.full_like(t, dist)
        dyn_gain = np.zeros_like(t)

        # Pre-fall stumble
        pre_mask = t < t_start
        d_torso[pre_mask] += 0.045 * np.sin(2 * np.pi * rng.uniform(1.2, 2.5) * t[pre_mask])
        dyn_gain[pre_mask] = 0.45

        # Active fall acceleration chirp (frequency sweeps from ~4 Hz -> 11.5 Hz as velocity v = g*t grows!)
        fall_mask = (t >= t_start) & (t <= t_impact)
        tau = (t[fall_mask] - t_start) / fall_dur  # [0 .. 1]
        f_start = rng.uniform(4.5, 6.5)
        f_end = rng.uniform(8.5, 12.5)
        chirp_phase = 2.0 * np.pi * (f_start * (t[fall_mask] - t_start) + 0.5 * (f_end - f_start) * tau * (t[fall_mask] - t_start))
        d_torso[fall_mask] += 0.42 * (tau ** 1.6) + 0.16 * np.sin(chirp_phase)
        d_limbs[fall_mask] += 0.55 * (tau ** 1.8) + 0.22 * np.cos(1.35 * chirp_phase)
        # Strong impact envelope peaking just before hitting the floor
        dyn_gain[fall_mask] = 0.7 + 1.85 * np.sin(np.pi * tau) ** 0.75

        # Post-impact floor stillness (critical temporal signature!)
        post_mask = t > t_impact
        d_torso[post_mask] += 0.42 + 0.005 * np.sin(2 * np.pi * 0.28 * t[post_mask])
        d_limbs[post_mask] += 0.55
        # Brief floor damped shockwave (40ms) then flat stillness
        shock = np.exp(-(t[post_mask] - t_impact) / 0.045)
        dyn_gain[post_mask] = 0.9 * shock + 0.06

        amp_fall = rng.uniform(11.5, 19.5) * dist_scale
        sc_broadband = 1.0 + 0.30 * np.sin(np.linspace(0, rng.uniform(2.0, 4.0) * np.pi, 64))[None, :]
        ph_torso = -2.0 * np.pi * SUBCARRIER_FREQS_64[None, :] * (2.0 * d_torso[:, None] / C_LIGHT)
        ph_limbs = -2.0 * np.pi * SUBCARRIER_FREQS_64[None, :] * (2.0 * d_limbs[:, None] / C_LIGHT)

        h_complex += (amp_fall * dyn_gain[:, None]) * sc_broadband * np.exp(1j * ph_torso)
        h_complex += (0.55 * amp_fall * dyn_gain[:, None]) * np.exp(1j * ph_limbs)

    # 3. Realistic RF impairments: AWGN thermal noise + random per-packet phase offset (ESP32 PLL)
    #    + occasional impulsive Wi-Fi co-channel spike (tested by Hampel filter!)
    awgn_std = rng.uniform(0.45, 1.35) * (1.25 if is_nlos else 1.0)
    noise = rng.normal(0.0, awgn_std, size=h_complex.shape) + 1j * rng.normal(0.0, awgn_std, size=h_complex.shape)
    random_pkt_phase = np.exp(1j * rng.uniform(-np.pi, np.pi, size=(RAW_WINDOW_SAMPLES, 1)))
    h_noisy = (h_complex + noise) * random_pkt_phase

    # Zero out guard bands [0..5], DC [32], [59..63] exactly like IEEE 802.11n HT20 LLTF hardware!
    inactive_mask = np.ones(64, dtype=bool)
    inactive_mask[ACTIVE_SC_INDICES] = False
    h_noisy[:, inactive_mask] = 0.0

    # Inject 1-2 impulsive RF glitch frames in ~35% of windows so Hampel filter has real work
    if rng.random() < 0.35:
        n_spikes = int(rng.integers(1, 3))
        spike_rows = rng.choice(RAW_WINDOW_SAMPLES, size=n_spikes, replace=False)
        h_noisy[spike_rows[:, None], ACTIVE_SC_INDICES[None, :]] *= rng.uniform(2.2, 3.4)

    # Quantize to signed int8 I/Q in [-127, 127] matching ESP32-S3 `wifi_csi_info_t::buf`
    iq_interleaved = np.empty((RAW_WINDOW_SAMPLES, 128), dtype=np.int8)
    iq_interleaved[:, 0::2] = np.clip(np.rint(h_noisy.real), -127, 127).astype(np.int8)
    iq_interleaved[:, 1::2] = np.clip(np.rint(h_noisy.imag), -127, 127).astype(np.int8)

    rssi_dbm = int(np.clip(-44.0 - 18.0 * np.log10(max(0.8, dist)) - (7.0 if is_nlos else 0.0) + rng.normal(0, 1.5), -88, -32))

    return {
        "label": label,
        "subtype": subtype,
        "distance_m": round(dist, 2),
        "nlos": is_nlos,
        "temp_c": round(room_temp, 2),
        "humi_rh": round(room_humi, 2),
        "rssi_dbm": rssi_dbm,
        "iq_raw": iq_interleaved,  # shape (200, 128) int8
    }


def generate_full_dataset(output_dir: Path, samples_per_class: int = 300, seed: int = 2026) -> dict:
    """
    Generate labeled CSV files in `ai_training/dataset/` (matching Chapter 6 repo structure)
    plus a compressed `.npz` archive for rapid GPU/TinyML training.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    all_amps = []
    all_labels = []
    all_temps = []
    all_humis = []
    all_dists = []
    all_nlos = []
    all_subtypes = []

    summary_counts: dict[str, int] = {}

    for cls_idx, label in enumerate(CLASS_NAMES):
        csv_path = output_dir / f"{label.lower()}.csv"
        header = (
            ["sample_id", "frame_idx", "label", "subtype", "distance_m", "nlos", "temp_c", "humi_rh", "rssi_dbm"]
            + [f"sc_{k:02d}" for k in range(52)]
        )
        # Save representative CSV windows (60 full 200-frame windows per class = 12,000 rows/CSV)
        # so GitHub repo stays fast & inspectable while `.npz` holds all `samples_per_class` windows!
        csv_keep = min(samples_per_class, 60)
        with csv_path.open("w", newline="", encoding="utf-8") as f_csv:
            writer = csv.writer(f_csv)
            writer.writerow(header)

            for s_idx in range(samples_per_class):
                win = synthesize_csi_iq_window(label=label, rng=rng)
                amps_52 = iq_bytes_to_active_amplitudes(win["iq_raw"])  # (200, 52)

                all_amps.append(amps_52)
                all_labels.append(cls_idx)
                all_temps.append(win["temp_c"])
                all_humis.append(win["humi_rh"])
                all_dists.append(win["distance_m"])
                all_nlos.append(win["nlos"])
                all_subtypes.append(win["subtype"])

                if s_idx < csv_keep:
                    for frame_i in range(RAW_WINDOW_SAMPLES):
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
                        ] + [round(float(v), 2) for v in amps_52[frame_i]]
                        writer.writerow(row)

        summary_counts[label] = samples_per_class

    npz_path = output_dir / "csi_multimodal_dataset.npz"
    np.savez_compressed(
        npz_path,
        amps_52=np.stack(all_amps, axis=0).astype(np.float32),  # (N, 200, 52)
        labels=np.array(all_labels, dtype=np.int64),
        temps=np.array(all_temps, dtype=np.float32),
        humis=np.array(all_humis, dtype=np.float32),
        distances=np.array(all_dists, dtype=np.float32),
        nlos=np.array(all_nlos, dtype=np.bool_),
        subtypes=np.array(all_subtypes),
        class_names=np.array(CLASS_NAMES),
    )

    meta = {
        "total_windows": int(len(all_labels)),
        "samples_per_class": summary_counts,
        "window_frames": RAW_WINDOW_SAMPLES,
        "sampling_rate_hz": FS_CSI_HZ,
        "active_subcarriers": 52,
        "npz_path": str(npz_path),
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate physics-based Wi-Fi CSI dataset (T05)")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "dataset")
    parser.add_argument("--samples-per-class", type=int, default=350)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    info = generate_full_dataset(args.out_dir, samples_per_class=args.samples_per_class, seed=args.seed)
    print(f"[T05 Dataset Generator] Generated {info['total_windows']} windows -> {info['npz_path']}")
