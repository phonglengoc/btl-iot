"""
GPU-Accelerated STFT Spectrogram 2D-CNN & Collaborative Fusion Engine (`T09`, `N04`).

Loads `ai_training/models/csi_spectrogram_2dcnn.onnx` into ONNX Runtime with
`CUDAExecutionProvider` on the NVIDIA GeForce RTX 5070 GPU (preloading CUDA/cuDNN DLLs
from PyTorch's CUDA 12.8 bundle when needed) and performs Two-Stage Collaborative Fusion:
    P_fused(c) = 0.4 * P_edge_tinyml(c) + 0.6 * P_gpu_2dcnn(c)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import numpy as np

# Ensure `ai_training` DSP utilities are importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "ai_training") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "ai_training"))

# Import torch BEFORE onnxruntime so bundled CUDA 12.8 + cuDNN 9 DLLs are registered on Windows!
import torch  # noqa: E402

torch_lib_dir = Path(torch.__file__).parent / "lib"
if torch_lib_dir.exists() and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(str(torch_lib_dir))
    os.environ["PATH"] = str(torch_lib_dir) + os.pathsep + os.environ.get("PATH", "")

import onnxruntime as ort  # noqa: E402
from csi_dsp_common import (  # noqa: E402
    CLASS_NAMES,
    compute_motion_energy_index,
    extract_edge_1dcnn_tensor,
    extract_gateway_stft_spectrogram,
)
from train_tinyml_1dcnn import run_int8_1dcnn_reference  # noqa: E402


class GatewayGpuSpectrogramEngine:
    """Real-time Spectrogram 2D-CNN cross-verifier accelerated on NVIDIA CUDA (`T09`, `N04`)."""

    def __init__(
        self,
        onnx_path: Path | str | None = None,
        tinyml_npz_path: Path | str | None = None,
        prefer_gpu: bool = True,
        edge_weight: float = 0.40,
        gpu_weight: float = 0.60,
    ) -> None:
        self.onnx_path = Path(onnx_path or (ROOT_DIR / "ai_training" / "models" / "csi_spectrogram_2dcnn.onnx"))
        self.tinyml_npz_path = Path(
            tinyml_npz_path or (ROOT_DIR / "ai_training" / "models" / "tinyml_1dcnn_int8.npz")
        )
        self.edge_weight = float(edge_weight)
        self.gpu_weight = float(gpu_weight)

        if not self.onnx_path.exists():
            raise FileNotFoundError(
                f"ONNX model not found at {self.onnx_path}. Run `python ai_training/train_gpu_2dcnn.py` first."
            )

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        avail = ort.get_available_providers()
        if prefer_gpu and "CUDAExecutionProvider" in avail:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        self.session = ort.InferenceSession(str(self.onnx_path), sess_options=sess_opts, providers=providers)
        self.active_provider: str = self.session.get_providers()[0]
        self.input_name: str = self.session.get_inputs()[0].name
        self.gpu_device_name: str = (
            torch.cuda.get_device_name(0)
            if (self.active_provider == "CUDAExecutionProvider" and torch.cuda.is_available())
            else "CPU"
        )

        self.tinyml_q_params: dict | None = (
            dict(np.load(self.tinyml_npz_path)) if self.tinyml_npz_path.exists() else None
        )

        # Warmup GPU CUDA kernels so first real-time inference has zero JIT lag
        dummy = np.zeros((1, 3, 64, 64), dtype=np.float32)
        for _ in range(5):
            self.session.run(None, {self.input_name: dummy})

    @staticmethod
    def _softmax(logits_1d: np.ndarray) -> np.ndarray:
        shifted = logits_1d - np.max(logits_1d)
        exps = np.exp(shifted)
        return (exps / np.sum(exps)).astype(np.float32)

    def infer_window(
        self,
        amps_200x52: np.ndarray,
        temp_c: float = 27.5,
        humi_rh: float = 65.0,
        edge_probs_from_node2: list[float] | np.ndarray | None = None,
    ) -> dict:
        """
        Compute STFT Spectrogram + run GPU ONNX 2D-CNN + fuse with Edge TinyML INT8 probabilities.
        """
        t_dsp0 = time.perf_counter()
        spec_3x64x64 = extract_gateway_stft_spectrogram(amps_200x52, temp_c=temp_c, humi_rh=humi_rh)
        dsp_ms = (time.perf_counter() - t_dsp0) * 1000.0

        batch_in = spec_3x64x64[None, ...]  # (1, 3, 64, 64)
        t_gpu0 = time.perf_counter()
        logits = self.session.run(None, {self.input_name: batch_in})[0][0]
        gpu_infer_ms = (time.perf_counter() - t_gpu0) * 1000.0

        gpu_probs = self._softmax(logits)
        gpu_cls_idx = int(np.argmax(gpu_probs))

        if edge_probs_from_node2 is not None and len(edge_probs_from_node2) == 4:
            edge_probs = np.asarray(edge_probs_from_node2, dtype=np.float32)
            edge_probs = edge_probs / max(float(np.sum(edge_probs)), 1e-6)
        elif self.tinyml_q_params is not None:
            x_edge = extract_edge_1dcnn_tensor(amps_200x52, temp_c=temp_c, humi_rh=humi_rh)[None, ...]
            edge_probs = run_int8_1dcnn_reference(x_edge, self.tinyml_q_params)[0]
        else:
            edge_probs = gpu_probs.copy()

        edge_cls_idx = int(np.argmax(edge_probs))
        fused_probs = self.edge_weight * edge_probs + self.gpu_weight * gpu_probs
        fused_cls_idx = int(np.argmax(fused_probs))

        return {
            "edge_state": CLASS_NAMES[edge_cls_idx],
            "edge_conf": round(float(edge_probs[edge_cls_idx]), 4),
            "edge_probs": [round(float(x), 4) for x in edge_probs],
            "gpu_state": CLASS_NAMES[gpu_cls_idx],
            "gpu_conf": round(float(gpu_probs[gpu_cls_idx]), 4),
            "gpu_probs": [round(float(x), 4) for x in gpu_probs],
            "fused_state": CLASS_NAMES[fused_cls_idx],
            "fused_conf": round(float(fused_probs[fused_cls_idx]), 4),
            "fused_fall_prob": round(float(fused_probs[3]), 4),
            "fused_probs": [round(float(x), 4) for x in fused_probs],
            "motion_energy": round(compute_motion_energy_index(amps_200x52), 2),
            "dsp_ms": round(dsp_ms, 3),
            "gpu_infer_ms": round(gpu_infer_ms, 3),
            "active_provider": self.active_provider,
            "gpu_device": self.gpu_device_name,
            "spectrogram_ch0": spec_3x64x64[0],  # (64, 64) float32 for UI Heatmap/Snapshot (N06)
        }
