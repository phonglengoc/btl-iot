"""
Simple Statistical Signal Processing for Wi-Fi CSI Fall Detection (`T05`, `T09`).

Replaces complex Hampel/Butterworth DSP and STFT spectrograms with intuitive,
easy-to-explain basic statistics over a 2.0-second window (40 frames @ 20 Hz):
  1. Moving Average smoothing (window = 3 frames) to reduce random spike noise.
  2. Standard Deviation (Std) & Peak-to-Peak Range (Max - Min) across 52 subcarriers.
  3. Sudden surge + post-impact stillness indicators (comparing peak jump vs tail stability):
     When a person falls, the signal jumps sharply and then becomes still on the floor.
"""

from __future__ import annotations

import numpy as np

# Sampling & window configuration (20 Hz -> 40 frames = 2.0 seconds)
FS_CSI_HZ: float = 20.0
WINDOW_SECONDS: float = 2.0
WINDOW_SAMPLES: int = 40
NUM_SUBCARRIERS: int = 52
SMOOTH_WINDOW: int = 3

CLASS_NAMES: list[str] = ["EMPTY", "STATIONARY", "WALKING", "FALL"]
CLASS_TO_IDX: dict[str, int] = {name: idx for idx, name in enumerate(CLASS_NAMES)}

FEATURE_NAMES: list[str] = [
    "std_all",            # Mean standard deviation across 52 subcarriers over 2 seconds
    "range_all",          # Mean peak-to-peak amplitude (Max - Min) across 52 subcarriers
    "max_range_sc",       # Largest (Max - Min) among all 52 subcarriers
    "activity_mean",      # Mean room disturbance over the 2-second window
    "activity_std",       # Standard deviation of room disturbance over time
    "activity_max",       # Peak room disturbance within 2 seconds
    "delta_mean",         # Mean frame-to-frame amplitude change
    "delta_max",          # Largest sudden frame-to-frame jump (very high during falls)
    "std_first_half",     # Standard deviation in first half of window (0.0s -> 1.0s)
    "std_second_half",    # Standard deviation in second half of window (1.0s -> 2.0s)
    "post_peak_std",      # Standard deviation after impact peak (low when lying still after fall)
    "peak_to_tail_ratio", # Ratio of sudden peak jump to tail fluctuation
    "rel_std",            # Scale-invariant CV: std_all / (mean_amp + 1) (AGC-independent)
    "rel_range",          # Scale-invariant peak-to-peak range / (mean_amp + 1)
    "rel_delta_max",      # Scale-invariant peak acceleration jump / (mean_amp + 1)
    "burst_crest_factor", # Impulsiveness: activity_max / median(activity) (high for FALL, low for WALKING)
    "active_frac",        # Fraction of frames > 45% peak activity (high for WALKING, low for FALL)
    "raw_lag1_corr",      # Mean per-subcarrier lag-1 autocorrelation (~0 EMPTY noise, high for human motion)
]


def moving_average_smooth(amps: np.ndarray, window_size: int = SMOOTH_WINDOW) -> np.ndarray:
    """
    Smooth a (T, 52) CSI amplitude array along the time axis using a simple Moving Average.
    """
    x = np.asarray(amps, dtype=np.float32)
    if x.ndim != 2 or x.shape[0] < window_size or window_size <= 1:
        return x.copy()

    pad_left = window_size // 2
    pad_right = window_size - 1 - pad_left
    padded = np.pad(x, ((pad_left, pad_right), (0, 0)), mode="edge")
    csum = np.cumsum(padded, axis=0, dtype=np.float64)
    csum = np.vstack([np.zeros((1, x.shape[1]), dtype=np.float64), csum])
    smoothed = (csum[window_size:] - csum[:-window_size]) / float(window_size)
    return smoothed.astype(np.float32)


def compute_disturbance_curve(amps: np.ndarray, smooth_win: int = SMOOTH_WINDOW) -> np.ndarray:
    """
    Compute a 1D room disturbance curve (length T) after Moving Average smoothing.
    Each point represents how much the 52 Wi-Fi subcarriers deviate from their window mean.
    """
    smooth = moving_average_smooth(amps, window_size=smooth_win)
    centered = smooth - np.mean(smooth, axis=0, keepdims=True)
    return np.mean(np.abs(centered), axis=1).astype(np.float32)


def extract_statistical_features(amps_40x52: np.ndarray, smooth_win: int = SMOOTH_WINDOW) -> np.ndarray:
    """
    Extract 18 statistical & scale-invariant physics features from a 2.0-second CSI window (40, 52)
    for scikit-learn Random Forest / MLP classification (`T09`).
    """
    raw = np.asarray(amps_40x52, dtype=np.float32)
    smooth = moving_average_smooth(raw, window_size=smooth_win)  # (40, 52)
    n_frames = smooth.shape[0]
    mean_amp = float(np.mean(smooth))

    # 1. Global Standard Deviation & (Max - Min) across the 52 subcarriers
    sc_std = np.std(smooth, axis=0)
    sc_range = np.max(smooth, axis=0) - np.min(smooth, axis=0)
    std_all = float(np.mean(sc_std))
    range_all = float(np.mean(sc_range))
    max_range_sc = float(np.max(sc_range))

    # 2. Instantaneous room disturbance curve over 2 seconds
    centered = smooth - np.mean(smooth, axis=0, keepdims=True)
    activity = np.mean(np.abs(centered), axis=1)  # (40,)
    activity_mean = float(np.mean(activity))
    activity_std = float(np.std(activity))
    activity_max = float(np.max(activity))

    # 3. Frame-to-frame sudden jump (|x[t] - x[t-1]|)
    frame_deltas = np.mean(np.abs(np.diff(smooth, axis=0)), axis=1)  # (39,)
    delta_mean = float(np.mean(frame_deltas))
    delta_max = float(np.max(frame_deltas))
    peak_idx = int(np.argmax(frame_deltas))

    # 4. First half vs Second half comparison (0..1s vs 1..2s)
    mid = n_frames // 2
    std_first_half = float(np.mean(np.std(smooth[:mid], axis=0)))
    std_second_half = float(np.mean(np.std(smooth[mid:], axis=0)))

    # 5. Fast post-peak stillness (key intuition for FALL: sharp jump followed by stillness!)
    # Uses 0.40s tail (8 frames @ 20 Hz) so falls near t=1.2-1.55s trigger ~1s faster.
    post_start = min(n_frames - 7, peak_idx + 4)
    post_segment = smooth[post_start:]
    post_peak_std = float(np.mean(np.std(post_segment, axis=0)))

    tail_segment = smooth[-8:]  # Last 0.40 seconds (8 frames @ 20 Hz)
    tail_std = float(np.mean(np.std(tail_segment, axis=0)))
    peak_to_tail_ratio = float(delta_max / (tail_std + 0.12))

    # 6. Scale-invariant & temporal-shape features (generalize across rooms & ESP32-S3 AGC levels)
    denom_amp = mean_amp + 1.0
    rel_std = float(std_all / denom_amp)
    rel_range = float(range_all / denom_amp)
    rel_delta_max = float(delta_max / denom_amp)
    burst_crest_factor = float(activity_max / (float(np.median(activity)) + 0.05))
    active_frac = float(np.mean(activity > (0.45 * (activity_max + 1e-6))))

    # Per-subcarrier lag-1 autocorrelation on raw frames: ~0 for EMPTY white noise, high for human motion
    raw_c = raw - np.mean(raw, axis=0, keepdims=True)
    num = np.sum(raw_c[:-1] * raw_c[1:], axis=0)
    den = np.sum(raw_c[:-1] ** 2, axis=0) + 1e-4
    raw_lag1_corr = float(np.mean(np.clip(num / den, -1.0, 1.0)))

    return np.array(
        [
            std_all,
            range_all,
            max_range_sc,
            activity_mean,
            activity_std,
            activity_max,
            delta_mean,
            delta_max,
            std_first_half,
            std_second_half,
            post_peak_std,
            peak_to_tail_ratio,
            rel_std,
            rel_range,
            rel_delta_max,
            burst_crest_factor,
            active_frac,
            raw_lag1_corr,
        ],
        dtype=np.float32,
    )


def compute_signal_summary(amps_40x52: np.ndarray) -> dict:
    """
    Compute human-friendly metrics for Gateway telemetry and Streamlit line charts (`T08`, `T10`).
    """
    smooth = moving_average_smooth(amps_40x52, window_size=SMOOTH_WINDOW)
    sc_std = np.std(smooth, axis=0)
    sc_range = np.max(smooth, axis=0) - np.min(smooth, axis=0)
    curve = compute_disturbance_curve(amps_40x52, smooth_win=SMOOTH_WINDOW)
    return {
        "signal_std": round(float(np.mean(sc_std)), 3),
        "signal_range": round(float(np.mean(sc_range)), 3),
        "activity_curve": [round(float(v), 2) for v in curve],
    }
