"""
PC Local Gateway AI Inference Engine (`T09`).

Runs a single lightweight `scikit-learn` Random Forest model directly on the PC CPU
using basic Moving Average + Standard Deviation + (Max - Min) features over a 2-second window.
Zero GPU/CUDA, zero ONNX Runtime, zero C++ TinyML!
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import joblib
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "ai_training") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "ai_training"))

from csi_signal_features import (  # noqa: E402
    CLASS_NAMES,
    compute_signal_summary,
    extract_statistical_features,
)


class GatewayFallAI:
    """Lightweight scikit-learn classifier for 4-class room activity & fall detection (`T09`)."""

    def __init__(self, model_path: Path | str | None = None) -> None:
        self.model_path = Path(model_path or (ROOT_DIR / "ai_training" / "models" / "fall_rf_model.joblib"))
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"AI model not found at {self.model_path}. "
                "Run `python ai_training/csi_data_generator.py && python ai_training/train_gateway_ai.py` first!"
            )

        bundle = joblib.load(self.model_path)
        self.model = bundle["model"]
        if hasattr(self.model, "n_jobs"):
            self.model.n_jobs = 1  # Faster single-sample CPU inference (~2-4 ms without threadpool overhead)
        self.model_name: str = bundle.get("model_name", "RandomForestClassifier (scikit-learn CPU)")
        self.class_names: list[str] = list(bundle.get("class_names", CLASS_NAMES))

    def predict_window(self, amps_40x52: np.ndarray) -> dict:
        """
        Classify a 2.0-second CSI window (40 frames x 52 subcarriers) into:
          `EMPTY`, `STATIONARY`, `WALKING`, or `FALL`.
        """
        t0 = time.perf_counter()
        feats = extract_statistical_features(amps_40x52).reshape(1, -1)
        probs = self.model.predict_proba(feats)[0]
        infer_ms = (time.perf_counter() - t0) * 1000.0

        pred_idx = int(np.argmax(probs))
        pred_state = self.class_names[pred_idx]
        conf = float(probs[pred_idx])
        summary = compute_signal_summary(amps_40x52)

        return {
            "state": pred_state,
            "confidence": round(conf, 4),
            "probs": [round(float(p), 4) for p in probs],
            "signal_std": summary["signal_std"],
            "signal_range": summary["signal_range"],
            "activity_curve": summary["activity_curve"],
            "infer_ms": round(infer_ms, 2),
            "model_name": self.model_name,
        }
