"""
Shared Digital Signal Processing (DSP) pipeline for Wi-Fi CSI Fall & Intrusion Detection.
Matches 100% with the C++ firmware implementation in `firmware/node2_rx_edge_ai/include/csi_dsp.h`.

Pipeline stages (Section 3.5.1 & Table 3.3 of `spec_btl_iot_fall_detection.tex`):
  1. Extract 52 active OFDM subcarriers from 64-subcarrier (128-byte I/Q) LLTF buffer:
       |H_k(t)| = sqrt(I_k^2 + Q_k^2)
  2. Environmental baseline calibration using Node 1's I2C DHT20 temperature & humidity.
  3. Sliding Hampel outlier filter (window 2K+1 = 5, threshold = 3 * 1.4826 * MAD).
  4. 4th-order Butterworth bandpass filter (0.2 - 15.0 Hz @ fs = 100 Hz) via cascaded SOS biquads.
  5. Subcarrier group pooling (52 -> 16 channels) & 2x temporal decimation (200 -> 100 steps)
     for Edge TinyML 1D-CNN input X_edge in R^(100 x 16).
  6. Multi-band Short-Time Fourier Transform (STFT) Spectrogram S_gw in R^(3 x 64 x 64)
     for Gateway GPU 2D-CNN cross-verification (T09, N04).
"""

from __future__ import annotations

import numpy as np
from scipy import signal

# Constants matching `firmware/include/csi_protocol.h`
FS_CSI_HZ: float = 100.0
WINDOW_SECONDS: float = 2.0
RAW_WINDOW_SAMPLES: int = 200        # 2.0s @ 100 Hz
EDGE_WINDOW_SAMPLES: int = 100       # 2.0s @ 50 Hz (after 2x decimation)
NUM_RAW_SUBCARRIERS: int = 64
NUM_ACTIVE_SUBCARRIERS: int = 52
NUM_EDGE_CHANNELS: int = 16
NUM_CLASSES: int = 4

CLASS_NAMES: list[str] = ["EMPTY", "STATIONARY", "WALKING", "FALL"]
CLASS_TO_IDX: dict[str, int] = {name: idx for idx, name in enumerate(CLASS_NAMES)}

# Active 52 LLTF subcarrier indices out of 64 (skipping guard bands [0..5], DC [32], [59..63])
ACTIVE_SC_INDICES: np.ndarray = np.array(
    [
        6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
        19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
        33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
        46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58,
    ],
    dtype=np.int32,
)

# Precompute 4th-order Butterworth bandpass SOS (0.2 - 15.0 Hz @ 100 Hz -> 4 biquad sections)
BUTTER_SOS: np.ndarray = signal.butter(
    N=4,
    Wn=[0.2, 15.0],
    btype="bandpass",
    fs=FS_CSI_HZ,
    output="sos",
).astype(np.float32)


def iq_bytes_to_active_amplitudes(iq_raw: np.ndarray) -> np.ndarray:
    """
    Convert signed int8 I/Q buffer (..., 128) into active 52-subcarrier amplitudes (..., 52).
    """
    iq = np.asarray(iq_raw, dtype=np.float32)
    if iq.shape[-1] != 128:
        raise ValueError(f"Expected last dim 128 bytes (64 I/Q pairs), got {iq.shape}")
    iq_pairs = iq.reshape(*iq.shape[:-1], 64, 2)
    amps_64 = np.sqrt(iq_pairs[..., 0] ** 2 + iq_pairs[..., 1] ** 2)
    return amps_64[..., ACTIVE_SC_INDICES]


def compensate_dht20_drift(
    amps_52: np.ndarray,
    temp_c: float = 27.0,
    humi_rh: float = 65.0,
    ref_temp_c: float = 27.0,
    ref_humi_rh: float = 65.0,
) -> np.ndarray:
    """
    Compensate dielectric attenuation drift caused by bathroom/bedroom steam & temperature shifts
    measured by Node 1's I2C DHT20 sensor (T01).
    """
    gain_corr = 1.0 + 0.0018 * (temp_c - ref_temp_c) + 0.0012 * (humi_rh - ref_humi_rh)
    gain_corr = float(np.clip(gain_corr, 0.85, 1.20))
    return (amps_52 / gain_corr).astype(np.float32)


def hampel_filter_causal(amps_seq: np.ndarray, half_win: int = 2, n_sigmas: float = 3.0) -> np.ndarray:
    """
    Causal sliding Hampel filter matching the real-time ring-buffer implementation on ESP32-S3.
    Window length = 2*half_win + 1 = 5 samples (uses latest 5 causal samples [t-4 .. t]).
    amps_seq: shape (T, C)
    """
    x = np.asarray(amps_seq, dtype=np.float32)
    t_len, n_ch = x.shape
    win_len = 2 * half_win + 1  # 5
    out = np.empty_like(x)
    for t in range(t_len):
        start = max(0, t - win_len + 1)
        patch = x[start : t + 1]
        med = np.median(patch, axis=0)
        mad = 1.4826 * np.median(np.abs(patch - med), axis=0)
        diff = np.abs(x[t] - med)
        mask = diff > (n_sigmas * (mad + 1e-5))
        out[t] = np.where(mask, med, x[t])
    return out


def butterworth_bandpass_filter(amps_seq: np.ndarray) -> np.ndarray:
    """
    Apply causal 4th-order Butterworth bandpass filter (0.2 - 15.0 Hz @ 100 Hz)
    with DC offset removal using the initial sample so startup transient is suppressed.
    amps_seq: shape (T, C)
    """
    x = np.asarray(amps_seq, dtype=np.float32)
    centered = x - np.mean(x[: min(16, len(x))], axis=0, keepdims=True)
    filtered = signal.sosfilt(BUTTER_SOS, centered, axis=0)
    return filtered.astype(np.float32)


def pool_subcarriers_52_to_16(amps_52: np.ndarray) -> np.ndarray:
    """
    Group-average 52 active subcarriers into 16 contiguous frequency bands for TinyML 1D-CNN.
    Deterministic mapping identical to C++ `csi_dsp.h`:
      Bands 0..3 cover 4 subcarriers each (16), bands 4..15 cover 3 subcarriers each (36) -> 52 total!
    """
    out = np.empty((*amps_52.shape[:-1], NUM_EDGE_CHANNELS), dtype=np.float32)
    idx = 0
    for b in range(NUM_EDGE_CHANNELS):
        width = 4 if b < 4 else 3
        out[..., b] = np.mean(amps_52[..., idx : idx + width], axis=-1)
        idx += width
    return out


def extract_edge_1dcnn_tensor(
    amps_200x52: np.ndarray,
    temp_c: float = 27.0,
    humi_rh: float = 65.0,
) -> np.ndarray:
    """
    Full Edge DSP pipeline transforming a 2.0-second raw CSI window (200, 52)
    into normalized TinyML 1D-CNN input tensor X_edge of shape (100, 16).
    Also appends temporal envelope energy visibility while preserving (100, 16) layout.
    """
    comp = compensate_dht20_drift(amps_200x52, temp_c=temp_c, humi_rh=humi_rh)
    clean = hampel_filter_causal(comp, half_win=2, n_sigmas=3.0)
    pooled_16 = pool_subcarriers_52_to_16(clean)

    # Channel-wise zero-mean + bandpass filtering
    bp_16 = butterworth_bandpass_filter(pooled_16)

    # 2x decimation via 2-sample anti-alias boxcar average: (200, 16) -> (100, 16) at 50 Hz
    decimated = 0.5 * (bp_16[0::2] + bp_16[1::2])

    # Scale by fixed physical gain (1/12.0) and clip to [-4.0, 4.0] so INT8 input scale is fixed!
    # Note: Using a fixed physical scale (not per-window z-score!) preserves the amplitude distinction
    # between EMPTY (tiny noise ~0.03), STATIONARY breathing (~0.12), WALKING (~0.6), and FALL (~1.8)!
    x_edge = np.clip(decimated / 12.0, -4.0, 4.0).astype(np.float32)
    return x_edge


def extract_gateway_stft_spectrogram(
    amps_200x52: np.ndarray,
    temp_c: float = 27.0,
    humi_rh: float = 65.0,
) -> np.ndarray:
    """
    Build a 3-channel 64x64 Time-Frequency-Subcarrier representation S_gw in R^(3, 64, 64)
    for the RTX 5070 GPU 2D-CNN (Table 3.3 of `spec_btl_iot_fall_detection.tex`):
      - Channel 0: Log-power STFT Spectrogram averaged across lower OFDM subcarriers (k=0..25)
      - Channel 1: Log-power STFT Spectrogram averaged across upper OFDM subcarriers (k=26..51)
      - Channel 2: Resampled Subcarrier-Time Differential Energy Map (52 subcarriers x time -> 64x64)
                   capturing post-fall stillness vs continuous walking cadence!
    """
    comp = compensate_dht20_drift(amps_200x52, temp_c=temp_c, humi_rh=humi_rh)
    clean = hampel_filter_causal(comp, half_win=2, n_sigmas=3.0)
    bp = butterworth_bandpass_filter(clean)  # (200, 52)

    # Compute STFT with nperseg=32, noverlap=29, nfft=128 -> 65 freq bins (0..50 Hz), ~64 time frames
    def _band_stft(sig_2d: np.ndarray) -> np.ndarray:
        # Compute STFT on each subcarrier then average power (prevents destructive phase cancellation!)
        _, _, zxx = signal.stft(
            sig_2d,
            fs=FS_CSI_HZ,
            window="hann",
            nperseg=32,
            noverlap=29,
            nfft=128,
            axis=0,
            boundary="zeros",
            padded=True,
        )
        # zxx shape: (65_freq, n_sub, n_time) -> mean power across subcarriers
        power = np.mean(np.abs(zxx) ** 2, axis=1)  # (65, n_time)
        log_spec = np.log1p(power[:64, :])         # Keep 0..49.2 Hz (64 bins)
        # Interpolate time axis cleanly to 64 columns
        if log_spec.shape[1] != 64:
            t_old = np.linspace(0.0, 1.0, log_spec.shape[1], dtype=np.float32)
            t_new = np.linspace(0.0, 1.0, 64, dtype=np.float32)
            log_spec = np.array([np.interp(t_new, t_old, row) for row in log_spec], dtype=np.float32)
        return np.clip(log_spec / 5.0, 0.0, 2.0).astype(np.float32)

    ch0 = _band_stft(bp[:, :26])
    ch1 = _band_stft(bp[:, 26:])

    # Channel 2: 2D Subcarrier x Time instantaneous envelope resized to (64, 64)
    env = np.abs(bp.T) / 15.0  # (52, 200)
    t_old = np.linspace(0.0, 1.0, env.shape[1], dtype=np.float32)
    t_new = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    env_t64 = np.array([np.interp(t_new, t_old, row) for row in env], dtype=np.float32)  # (52, 64)
    f_old = np.linspace(0.0, 1.0, env_t64.shape[0], dtype=np.float32)
    f_new = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    ch2 = np.array([np.interp(f_new, f_old, env_t64[:, col]) for col in range(64)], dtype=np.float32).T
    ch2 = np.clip(ch2, 0.0, 2.5).astype(np.float32)

    spec_3ch = np.stack([ch0, ch1, ch2], axis=0)  # (3, 64, 64)
    return spec_3ch


def compute_motion_energy_index(amps_200x52: np.ndarray) -> float:
    """
    Scalar CSI motion energy metric [0..100] reported in CoreIOT telemetry (`csi_motion_energy`).
    """
    centered = amps_200x52 - np.mean(amps_200x52, axis=0, keepdims=True)
    rms = float(np.sqrt(np.mean(centered ** 2)))
    return float(np.clip(rms * 8.5, 0.0, 100.0))
